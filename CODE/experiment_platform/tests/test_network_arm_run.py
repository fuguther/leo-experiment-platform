"""Round-6 static checks: which exits exist, and what the scorer covers.

These are pure-snapshot checks; they call the SAME shared scorer the closed
loop uses (time_alignment.plan_decision) and no kernel simulation."""
from __future__ import annotations

import pytest

from CODE.leo_sim import time_alignment as ta


def _resource(peer, egress, generation=None):
    return ta.ResourceKey(int(peer), str(egress), "isl", generation=generation)


def _snapshot(directions, *, arm="common", remaining_prop=None,
              with_history=True):
    """A minimal legal snapshot: idle links, known rates, given directions.

    with_history supplies two received samples per resource so the
    predictor has something to extrapolate; without it the candidate
    correctly reports no_received_history and falls back."""
    resources = {d: _resource(peer, egress)
                 for d, peer, egress in directions}
    samples = []
    if with_history:
        for direction, _peer, _egress in directions:
            sample_resource = resources[direction]
            samples.append(ta.StateSample(sample_resource, 4.0, 4.001,
                                          0.0, 1e6))
            samples.append(ta.StateSample(sample_resource, 4.5, 4.501,
                                          50_000.0, 1e6))
    return ta.make_snapshot(
        satellite=0, snapshot_at=5.0,
        history=tuple(samples),
        legal_directions=tuple(d for d, _p, _e in directions),
        resources=resources,
        egress_queue_bits={d: 0.0 for d, _p, _e in directions},
        local_egress_in_service_s={d: 0.0 for d, _p, _e in directions},
        link_rate_bps={d: 1e6 for d, _p, _e in directions},
        link_propagation_s={d: 0.001 for d, _p, _e in directions},
        peer_process_s={d: 0.01 for d, _p, _e in directions},
        remaining_prop_s={d: (remaining_prop or {}).get(d, 0.001)
                          for d, _p, _e in directions},
        resource_service_rate_bps={d: 1e6 for d, _p, _e in directions},
        compute_wait_s=0.0, compute_service_s=0.01,
        pkt_bits=100_000.0,
        arm=arm, predictor="bounded_linear", history_limit=3,
        common_rule="median_eta", query_delay_s=0.0)


def test_rate4_probe_table_is_the_declared_251_packet_input():
    """Round-8 input, checked statically BEFORE any run (no simulation).

    146 background rows and the original 27 probes must be byte-identical
    to the round-7 restricted input; 78 new probes fill the 0.065 s grid so
    that local waiting can separate the candidates' projected instants."""
    import json
    from CODE.experiment_platform import scripted_scenarios as scen

    rate4, rows, _g, meta = scen.build("net_h1_restricted_rate4",
                                       arm="now")
    assert len(rows) == 251
    pids = [r["packet_id"] for r in rows]
    assert len(set(pids)) == 251, "duplicate PID in the arrival table"
    assert sum(r["bits"] for r in rows) == 25_100_000
    assert rate4["config"]["execution"]["max_packets"] >= 251

    # the shared grid, and the original probe PIDs/times preserved exactly
    probe = [r for r in rows if r["packet_id"] >= 400]
    assert len(probe) == 105
    assert [r["emit_time_s"] for r in probe] == [
        round(scen.PROBE_FIRST_S + scen.RATE4_PROBE_PERIOD_S * k, 6)
        for k in range(105)]
    assert probe[0]["emit_time_s"] == 2.5
    assert probe[-1]["emit_time_s"] == 9.26

    old, old_rows, _og, _om = scen.build("net_h1_restricted", arm="now")
    old_by_pid = {r["packet_id"]: r for r in old_rows}
    new_by_pid = {r["packet_id"]: r for r in rows}
    original_pids = list(range(400, 427))
    for pid in original_pids:
        assert new_by_pid[pid] == old_by_pid[pid], pid
    for pid in [r["packet_id"] for r in old_rows if r["packet_id"] < 400]:
        assert new_by_pid[pid] == old_by_pid[pid], pid
    added = set(new_by_pid) - set(old_by_pid)
    assert len(added) == 78
    assert min(added) == 600 and max(added) == 677

    # 146 background + 27 original + 78 new, and nothing else
    assert len([p for p in pids if p < 400]) == 146
    assert len([p for p in pids if 400 <= p <= 426]) == 27
    assert len([p for p in pids if 600 <= p <= 677]) == 78
    assert sorted(added) == sorted(range(600, 678))
    assert pids.count(0) == 0
    assert all(0 <= p <= 677 for p in pids)

    # expected admission arithmetic, declared not measured
    assert meta["declared"]["expected_isl_transmissions"] == 356
    assert meta["declared"]["expected_dual_exit_opportunities"] == 105


def test_rate4_arms_differ_only_in_the_arm_field():
    """Four arms must share one input and one configuration otherwise."""
    import copy
    import json
    from CODE.experiment_platform import scripted_scenarios as scen

    fingerprints = {}
    digests = {}
    for arm in ("stale", "now", "common", "candidate"):
        resolved, rows, _g, _m = scen.build("net_h1_restricted_rate4",
                                            arm=arm)
        doc = copy.deepcopy(resolved)
        # sha256 and canonical_json are DERIVED from the config: they must
        # differ between arms, so they are excluded from the comparison of
        # what the arms are actually given.
        doc.pop("sha256", None)
        doc.pop("canonical_json", None)
        assert doc["config"]["time_alignment"]["arm"] == arm
        doc["config"]["time_alignment"].pop("arm")
        fingerprints[arm] = json.dumps(
            {"config": doc, "rows": rows}, sort_keys=True)
        digests[arm] = resolved["sha256"]
    # one input, one configuration, four distinct identities
    assert len(set(fingerprints.values())) == 1
    assert len(set(digests.values())) == 4


def test_older_scenarios_keep_their_semantics():
    """The new condition must not change any earlier scenario."""
    from CODE.experiment_platform import scripted_scenarios as scen

    net, rows, _g, _m = scen.build("net_h1", arm="stale")
    assert len(rows) == 173
    assert net["config"]["routing"]["min_remaining_hop_only"] is False
    restricted, rrows, _rg, _rm = scen.build("net_h1_restricted",
                                             arm="stale")
    assert len(rrows) == 173
    assert restricted["config"]["routing"]["min_remaining_hop_only"] is True
    rate4, qrows, _qg, _qm = scen.build("net_h1_restricted_rate4",
                                        arm="stale")
    assert len(qrows) == 251
    assert rate4["config"]["routing"]["min_remaining_hop_only"] is True


def test_the_restricted_scenario_keeps_only_shortest_hop_candidates():
    """The round-7 restricted condition, checked before spending a run.

    Background sources sit on sat1 / sat2 and must keep ONLY the direction
    to sat3; the probe source sat0 must keep BOTH equal-length two-hop
    directions.  The restriction is a declared diagnostic and must be OFF
    by default so every earlier scenario keeps its historical candidates.
    """
    from CODE.experiment_platform import scripted_scenarios as scen
    from CODE.leo_sim import routing

    _r, rows, geometry, _m = scen.build("net_h1_restricted", arm="stale")
    assert len(rows) == 173
    probe_pids = {int(r["packet_id"]) for r in rows
                  if int(r["packet_id"]) >= scen.PROBE_FIRST_PID}
    assert len(probe_pids) == 27
    assert len(rows) - len(probe_pids) == 146

    # old scenarios are untouched: the flag is False unless declared
    legacy, _rows, _g, _mm = scen.build("net_h1", arm="stale")
    assert legacy["config"]["routing"]["min_remaining_hop_only"] is False
    assert _r["config"]["routing"]["min_remaining_hop_only"] is True

    # sat1 and sat2 each have exactly ONE direction reaching sat3 directly;
    # sat0 has two equal-length two-hop directions, so the restriction must
    # keep both of them there and only there.
    topo = scen.TOPO
    for sat in (1, 2):
        direct = [d for d, p in topo[sat].items() if p == 3]
        assert len(direct) == 1, (sat, topo[sat])
        assert len(topo[sat]) == 2, (sat, topo[sat])
    sat0 = sorted(topo[0].items())
    assert [d for d, _p in sat0] == ["E", "W"]
    reverse = scen.TOPO
    forward = {s: {p: 1 for p in dict(reverse[s]).values()}
               for s in reverse}
    reach = routing._multi_source_bfs(forward, [3])
    hops0 = sorted(reach[peer] for _d, peer in sat0)
    assert hops0[0] == hops0[1] == 1, hops0
    assert reach[topo[1]["E"]] == 0 and len(topo[1]) == 2

    # the W source cell must be present in the recorded metadata: the older
    # compare source table omitted it, and this is metadata only
    assert len(_m["cells"]) == 4

def test_single_legal_exit_fallback_cannot_change_the_choice():
    """Round 5 reported a 55% fallback rate for common.  With ONE executable
    exit a NO_STRONG_COMMON fallback cannot change the action, so that rate is
    not by itself a comparability verdict."""
    snapshot = _snapshot([("E", 1, "S")], arm="common")
    scored = ta.plan_decision(snapshot).scored
    assert scored.fallback_directions == ("E",)
    assert "NO_STRONG_COMMON" in scored.by_direction()["E"].missing
    assert scored.ranking[0] == "E"


def test_two_legal_exits_without_a_resolvable_horizon_still_fall_back():
    """The fallback only matters where a real choice exists: with two exits
    and no resolvable common horizon, BOTH fall back and the order reverts to
    the stable direction order -- which is exactly the case that can move an
    action away from what a scored ranking would have picked."""
    snapshot = _snapshot([("E", 1, "S"), ("W", 2, "S")], arm="common")
    scored = ta.plan_decision(snapshot).scored
    assert set(scored.fallback_directions) == {"E", "W"}
    for direction in ("E", "W"):
        assert "NO_STRONG_COMMON" in scored.by_direction()[direction].missing
    assert scored.ranking[0] == "E"  # stable order, not a scored preference


def test_scorer_covers_local_send_and_one_target_resource_only():
    """A detour's extra hops are NOT priced as service.

    The scorer has the local send (`tx_s`), the target resource's own send
    (`resource_packet_tx_s`), the target resource's queued work
    (`resource_work_s`) and a propagation-only remainder.  It has no
    serialization or queueing term for hops AFTER the target resource, so
    with remaining_hops = 2 the remainder is propagation only.
    """
    direction = "E"
    snapshot = _snapshot([(direction, 1, "S")], arm="now",
                         remaining_prop={"E": 0.002})  # 2 hops x 0.001 s
    scored = ta.plan_decision(snapshot).scored.by_direction()[direction]
    terms = scored.terms
    assert not scored.fallback, scored.missing
    assert terms["tx_s"] == pytest.approx(0.1)              # local 100 kbit
    assert terms["resource_packet_tx_s"] == pytest.approx(0.1)
    assert terms["remaining_prop_s"] == pytest.approx(0.002)
    # propagation for two further hops is counted, service for them is not
    hop_service_terms = [k for k in terms
                         if k.endswith("_tx_s") and k not in
                         ("tx_s", "resource_packet_tx_s", "terminal_tx_s")]
    assert hop_service_terms == []
    # and the remainder scales with propagation, not with 0.1 s per hop
    assert terms["remaining_prop_s"] != pytest.approx(0.2)


def test_three_hop_remainder_is_propagation_scaled_only():
    """Same statement at 3 remaining hops, the detour length in net_h1."""
    direction = "E"
    snapshot = _snapshot([(direction, 1, "S")], arm="now",
                         remaining_prop={"E": 0.003})
    terms = ta.plan_decision(snapshot).scored.by_direction()[direction].terms
    assert terms["remaining_prop_s"] == pytest.approx(0.003)
    assert "remaining_service_s" not in terms
