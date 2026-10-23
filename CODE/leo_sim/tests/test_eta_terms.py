"""T1-COMPLETE R8: the ETA must contain every local delay it will pay.

The four arms differ ONLY in the instant they query.  If a local queueing delay
is missing from the ETA, the query instant is systematically too early and the
prediction is asked about the wrong moment -- adding the queue to the SCORE
afterwards cannot repair that.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from CODE.leo_sim import kernel, time_alignment as ta
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

R = ta.ResourceKey(1, "E", "isl")


def _snap(*, egress=0.0, rate=8000.0, service=0.0, wait=0.0,
          query_wait=0.0, query_service=0.0, history=True):
    history = ((ta.StateSample(R, 0.0, 0.2, 0.0, rate),) if history else ())
    # an absent key means "not known", which is different from a numeric zero
    rates = {} if rate is None else {"E": rate}
    egress_map = {} if egress is None else {"E": egress}
    return ta.make_snapshot(
        satellite=0, snapshot_at=0.2, history=history,
        legal_directions=("E",),
        resources={"E": R},
        egress_queue_bits=egress_map,
        link_rate_bps=rates,
        link_propagation_s={"E": 0.01},
        peer_process_s={"E": 0.0},
        remaining_prop_s={"E": 0.0},
        resource_service_rate_bps={"E": rate} if rate is not None else {},
        local_egress_in_service_s={"E": 0.0},
        compute_wait_s=wait, compute_service_s=service,
        query_wait_s=query_wait, query_service_s=query_service,
        pkt_bits=800.0, arm="candidate")


def test_the_local_egress_queue_moves_the_query_instant_later():
    empty = ta.estimate_eta(_snap(egress=0.0), "E")
    queued = ta.estimate_eta(_snap(egress=8000.0), "E")
    assert empty.terms["local_egress_wait_s"] == 0.0
    # 8000 bits at 8000 bit/s is one second of extra local waiting
    assert queued.terms["local_egress_wait_s"] == pytest.approx(1.0)
    assert queued.target_at - empty.target_at == pytest.approx(1.0)


def test_the_compute_wait_is_its_own_term_and_moves_the_target():
    idle = ta.estimate_eta(_snap(wait=0.0, service=0.5), "E")
    busy = ta.estimate_eta(_snap(wait=1.5, service=0.5), "E")
    assert idle.terms["compute_wait_s"] == 0.0
    assert busy.terms["compute_wait_s"] == pytest.approx(1.5)
    assert busy.terms["compute_service_s"] == pytest.approx(0.5)
    assert busy.target_at - idle.target_at == pytest.approx(1.5)


def test_query_wait_and_service_are_separate_eta_terms():
    no_query = ta.estimate_eta(_snap(), "E")
    queried = ta.estimate_eta(
        _snap(query_wait=0.0004, query_service=0.0001), "E")

    assert queried.terms["query_wait_s"] == pytest.approx(0.0004)
    assert queried.terms["query_service_s"] == pytest.approx(0.0001)
    assert queried.target_at - no_query.target_at == pytest.approx(0.0005)


def test_compute_wait_projects_remaining_service_and_all_fifo_waiters():
    # Isolate the pool state from the kernel's scheduled traffic processes.
    # Those processes may legitimately enqueue other jobs at the test time.
    active = SimpleNamespace(usage_since=0.0)
    pool = SimpleNamespace(capacity=1, users=[active], queue=[object(), object()])
    service_by_request = {id(active): 0.5}

    # At t=.2, the active job has .3 s left, then two .5 s FIFO jobs ahead
    # of the newly requested job.  The request-time estimate is 1.3 s.
    wait, queued = kernel.Kernel._pool_wait_for_new_request(
        pool, 0.5, 0.2, service_by_request)
    assert queued == 2
    assert wait == pytest.approx(1.3)


def test_the_score_reuses_the_eta_terms_without_double_counting():
    snap = _snap(egress=8000.0, rate=8000.0)
    eta = ta.estimate_eta(snap, "E")
    scored = ta.score_snapshot_at(snap, eta.target_at)
    terms = scored.by_direction()["E"].terms
    assert terms["local_egress_wait_s"] == pytest.approx(1.0)
    assert terms["tx_s"] == pytest.approx(800.0 / 8000.0)
    # the total is exactly the sum of the recorded, non-overlapping terms
    assert scored.by_direction()["E"].total_s == pytest.approx(
        sum(terms.values()))
    # and the ETA terms agree with the score terms for the shared keys
    for name in ta.ETA_TERM_KEYS:
        if name in eta.terms:
            assert terms[name] == pytest.approx(eta.terms[name])


def test_an_unknown_local_rate_is_missing_not_a_zero():
    snap = _snap(egress=8000.0, rate=None)
    eta = ta.estimate_eta(snap, "E")
    assert "tx_s" in eta.unknown_terms
    assert "local_egress_wait_s" in eta.unknown_terms
    scored = ta.score_snapshot_at(snap, snap.snapshot_at + 1.0)
    assert "tx_s" in scored.by_direction()["E"].missing or \
        "local_egress_wait_s" in scored.by_direction()["E"].missing


def _direct_downlink_snapshot(queue_bits, *, downlink_rate=100_000_000.0,
                              downlink_prop=0.01):
    direct = ta.ResourceKey(2, B, "downlink")
    detour = ta.ResourceKey(19, "E", "isl")
    history = (
        ta.StateSample(direct, 0.0, 0.1, float(queue_bits), downlink_rate),
        ta.StateSample(detour, 0.0, 0.1, 0.0, 5_000_000.0),
    )
    return ta.make_snapshot(
        satellite=1, snapshot_at=0.2, history=history,
        legal_directions=("N", "W"), resources={"N": direct, "W": detour},
        egress_queue_bits={"N": 0.0, "W": 0.0},
        link_rate_bps={"N": 5_000_000.0, "W": 5_000_000.0},
        local_egress_in_service_s={"N": 0.0, "W": 0.0},
        link_propagation_s={"N": 0.002, "W": 0.002},
        peer_process_s={"N": 0.0, "W": 0.0},
        remaining_prop_s={"N": 0.0, "W": 0.02},
        resource_service_rate_bps=(
            {"W": 5_000_000.0}
            if downlink_rate is None else
            {"N": downlink_rate, "W": 5_000_000.0}),
        terminal_propagation_s=(
            {} if downlink_prop is None else {"N": downlink_prop}),
        resource_available={"N": True},
        compute_wait_s=0.0, compute_service_s=0.0, pkt_bits=1_000_000.0,
        arm="candidate")


def _score_two_resources(snapshot):
    predictions, etas = {}, {}
    for direction in snapshot.legal_directions:
        eta = ta.estimate_eta(snapshot, direction)
        etas[direction] = eta
        key = snapshot.resource_for(direction)
        predictions[direction] = ta.predict_resource(
            snapshot.history_for(key), snapshot.snapshot_at, eta.target_at,
            snapshot.predictor, snapshot.history_limit,
            snapshot.max_resource_queue_bits, key)
    return ta.score_candidates(snapshot, predictions, etas)


def test_delivered_downlink_has_a_real_shared_resource_score():
    # The local N link is always the same 5 Mbps ISL.  Its destination-side
    # terminal resource is the independently advertised 100 Mbps GSL.
    snap = _direct_downlink_snapshot(0.0)
    scored = _score_two_resources(snap).by_direction()
    direct = scored["N"]
    assert not direct.fallback
    assert direct.terms["resource_work_s"] == pytest.approx(0.0)
    assert direct.terms["terminal_tx_s"] == pytest.approx(0.01)
    assert direct.terms["terminal_prop_s"] == pytest.approx(0.01)
    assert _score_two_resources(snap).ranking[0] == "N"


def test_busy_downlink_can_lose_to_a_finite_detour_and_unknown_stays_unknown():
    busy = _score_two_resources(_direct_downlink_snapshot(40_000_000.0))
    scores = busy.by_direction()
    assert not scores["N"].fallback and not scores["W"].fallback
    assert busy.ranking[0] == "W"

    unknown = _score_two_resources(_direct_downlink_snapshot(
        0.0, downlink_rate=None, downlink_prop=None)).by_direction()["N"]
    assert unknown.fallback
    assert "resource_service_rate" in unknown.missing
    assert "terminal_prop_s" in unknown.missing


A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _pool_fixture(servers, rows):
    cfg = make_cfg({
        "scenario": {"duration_s": 12.0},
        "demand": {"packet_bits": 800_000},
        "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": 0.5,
                      "compute_servers_per_satellite": servers},
        "time_alignment": {"enabled": True, "arm": "candidate"},
    })
    sink, timeline = [], []
    result = kernel.run_simulation(
        cfg, rows, geometry=StaticGeometry(2, neighbors_map=NB, visible=VIS),
        decision_sink=sink, timeline_sink=timeline)
    return result, sink, timeline


def test_a_bounded_pool_wait_enters_the_prediction_span():
    rows = [row(i, 0.05 * i, A, B) for i in range(1, 7)]
    _res, sink, _tl = _pool_fixture(1, rows)
    audits = [(r, (r.get("observation_at_start") or {}).get("time_alignment"))
              for r in sink if r.get("kind") == "forward"]
    audits = [(r, a) for r, a in audits if a]
    assert audits, "the fixture must record online audits"
    waits = [(r, a) for r, a in audits
             if a["compute_state"]["wait_estimate_s"] > 0.0]
    assert waits, [a["compute_state"] for _r, a in audits]
    for _r, audit in waits:
        assert audit["compute_state"]["method"] == \
            "fifo_remaining_service_plus_waiters"
        for direction in audit["eta_terms"]:
            assert audit["eta_terms"][direction]["compute_wait_s"] > 0.0


# --------------------- S2: packet size must not be an advertisement value
class _Pkt:
    dst = B
    bits = 8000


def _builder_kernel(*, compute_delay=0.0, compute_servers=1,
                    query_delay=0.0):
    if compute_delay <= 0.0 and compute_servers == 1:
        compute_servers = 0
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "demand": {"packet_bits": 8000},
        "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": compute_delay,
                      "compute_servers_per_satellite": compute_servers},
        "time_alignment": {"enabled": True, "arm": "candidate",
                           "query_delay_s": query_delay},
    })
    kern = kernel.Kernel(cfg, [row(1, 0.0, A, B)],
                         geometry=StaticGeometry(2, neighbors_map=NB,
                                                 visible=VIS),
                         decision_sink=[], timeline_sink=[])
    kern._candidate_resource_map = lambda *a, **kw: {
        "E": {"status": "ok", "peer": 1, "egress_direction": "E",
              "egress_generation": 9,
              "isl_rate_bps": 1e6, "propagation_s": 0.01,
              "remaining_prop_s": 0.02}}
    return kern


def test_the_snapshot_packet_size_is_not_an_advertised_queue_value():
    kern = _builder_kernel()
    kern._advertisement_history = lambda sat, peer, now, force=False: [
        {"generated_at": 0.0, "received_at": 0.1,
         "advertised_isl_generation": {"E": 9},
         "advertised_isl_rate_bps": {"E": 1e6},
         "advertised_isl_work_ahead_bits_proxy": {"E": 0}},
        {"generated_at": 1.0, "received_at": 1.1,
         "advertised_isl_generation": {"E": 9},
         "advertised_isl_rate_bps": {"E": 1e6},
         "advertised_isl_work_ahead_bits_proxy": {"E": 999_999}},
    ]
    snap = kern._build_ta_snapshot(_Pkt(), 0, 1.2, ["E"], {"E": 0.0},
                                   "candidate", None)
    assert snap.pkt_bits == 8000.0, snap.pkt_bits
    assert snap.history[-1].queue_bits == 999_999.0


def test_the_packet_size_is_invariant_to_advertisement_order_and_values():
    sizes = []
    for history in (
            [{"generated_at": 0.0, "received_at": 0.1,
              "advertised_isl_generation": {"E": 9},
              "advertised_isl_rate_bps": {"E": 1e6},
              "advertised_isl_work_ahead_bits_proxy": {"E": 0}}],
            [{"generated_at": 0.0, "received_at": 0.1,
              "advertised_isl_generation": {"E": 9},
              "advertised_isl_rate_bps": {"E": 1e6},
              "advertised_isl_work_ahead_bits_proxy": {"E": 5_000_000}}],
            [{"generated_at": 0.0, "received_at": 0.1,
              "advertised_isl_generation": {"E": 9},
              "advertised_isl_rate_bps": {"E": 1e6},
              "advertised_isl_work_ahead_bits_proxy": {"E": 1}},
             {"generated_at": 1.0, "received_at": 1.1,
              "advertised_isl_generation": {"E": 9},
              "advertised_isl_rate_bps": {"E": 1e6},
              "advertised_isl_work_ahead_bits_proxy": {"E": 2}},
             {"generated_at": 2.0, "received_at": 2.1,
              "advertised_isl_generation": {"E": 9},
              "advertised_isl_rate_bps": {"E": 1e6},
              "advertised_isl_work_ahead_bits_proxy": {"E": 3}}]):
        kern = _builder_kernel()
        kern._advertisement_history = (
            lambda sat, peer, now, force=False, h=history: h)
        snap = kern._build_ta_snapshot(_Pkt(), 0, 3.0, ["E"], {"E": 0.0},
                                       "candidate", None)
        sizes.append(snap.pkt_bits)
        assert snap.pkt_bits == 8000.0
    assert len(set(sizes)) == 1


def test_the_explicit_packet_bits_argument_wins_for_scope_snapshots():
    kern = _builder_kernel()
    kern._advertisement_history = lambda sat, peer, now, force=False: [
        {"generated_at": 0.0, "received_at": 0.1,
         "advertised_isl_generation": {"E": 9},
         "advertised_isl_rate_bps": {"E": 1e6},
         "advertised_isl_work_ahead_bits_proxy": {"E": 7_000_000}}]
    snap = kern._build_ta_snapshot(None, 0, 1.0, ["E"], {"E": 0.0},
                                   "candidate", None, dst=B,
                                   packet_bits=372 * 8)
    assert snap.pkt_bits == 372 * 8


def test_the_online_transmit_term_matches_the_real_packet_size():
    """End to end on a real branch: every legal candidate transmit term must
    equal the packet own bits / the known rate."""
    from CODE.experiment_platform import scripted_scenarios

    import copy
    from CODE.leo_sim import config as config_mod

    resolved, rows, geometry, _meta = scripted_scenarios.build("same_flow")
    enabled = copy.deepcopy(resolved["config"])
    enabled["time_alignment"]["enabled"] = True
    enabled["control_plane"]["advertisement_protocol_version"] = 2
    resolved = config_mod.resolve_config(enabled)
    bits_by_pid = {r["packet_id"]: float(r["bits"]) for r in rows}
    sink = []
    kernel.run_simulation(resolved, rows, geometry=geometry,
                          decision_sink=sink, timeline_sink=[])
    checked = 0
    advertised_nonzero = False
    for row in sink:
        if row.get("kind") != "forward":
            continue
        audit = (row.get("observation_at_start") or {}).get("time_alignment")
        if not audit:
            continue
        unknown = audit.get("eta_unknown_terms") or {}
        for direction, terms in (audit.get("eta_terms") or {}).items():
            if "tx_s" in (unknown.get(direction) or []):
                continue  # the rate is not knowable for this candidate
            rate = 1e6  # the scripted scenario declares a constant 1 Mbps ISL
            assert terms["tx_s"] == pytest.approx(
                bits_by_pid[row["pid"]] / rate), (direction, terms)
            checked += 1
        for cr in (row["observation_at_start"].get(
                "candidate_resources") or {}).values():
            if cr.get("advertised_queue_known"):
                advertised_nonzero = True
    assert checked > 0
    # at least one candidate had an ADVERTISED queue value (0 or not): the
    # shadowing regression itself is pinned by the direct builder test above,
    # which feeds a 999999-bit advertisement and requires pkt_bits == 8000
    assert advertised_nonzero


def test_an_unbounded_pool_reports_no_wait_term():
    rows = [row(i, 0.05 * i, A, B) for i in range(1, 7)]
    _res, sink, _tl = _pool_fixture(0, rows)
    for r in sink:
        audit = (r.get("observation_at_start") or {}).get("time_alignment")
        if not audit:
            continue
        assert audit["compute_state"]["wait_estimate_s"] == 0.0
        assert audit["compute_state"]["method"] == "unbounded_pool_no_wait"
