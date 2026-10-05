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
