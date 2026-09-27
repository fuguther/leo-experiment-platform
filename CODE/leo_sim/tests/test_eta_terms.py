"""T1-COMPLETE R8: the ETA must contain every local delay it will pay.

The four arms differ ONLY in the instant they query.  If a local queueing delay
is missing from the ETA, the query instant is systematically too early and the
prediction is asked about the wrong moment -- adding the queue to the SCORE
afterwards cannot repair that.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import kernel, time_alignment as ta
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

R = ta.ResourceKey(1, "E", "isl")


def _snap(*, egress=0.0, rate=8000.0, service=0.0, wait=0.0, history=True):
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
        compute_wait_s=wait, compute_service_s=service,
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
            "fifo_pending_tasks_times_service"
        for direction in audit["eta_terms"]:
            assert audit["eta_terms"][direction]["compute_wait_s"] > 0.0


def test_an_unbounded_pool_reports_no_wait_term():
    rows = [row(i, 0.05 * i, A, B) for i in range(1, 7)]
    _res, sink, _tl = _pool_fixture(0, rows)
    for r in sink:
        audit = (r.get("observation_at_start") or {}).get("time_alignment")
        if not audit:
            continue
        assert audit["compute_state"]["wait_estimate_s"] == 0.0
        assert audit["compute_state"]["method"] == "unbounded_pool_no_wait"
