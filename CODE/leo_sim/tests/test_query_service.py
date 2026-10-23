"""T1-COMPLETE R9: the shared per-satellite query service is real cost.

Every execution mode reads its answer through ONE FIFO server per satellite, so
"it is just a table lookup" is priced rather than assumed free.  The default
query_delay_s = 0 keeps the historical instantaneous path bit-identical.
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import execution_compare as ec
from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=VIS)


def _cfg(query_delay, packets=3, mode="per_packet", service=0.0, servers=0):
    return make_cfg({
        "scenario": {"duration_s": 10.0},
        "demand": {"packet_bits": 800_000},
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": service,
                      "compute_servers_per_satellite": servers},
        "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0,
                   "uplink_rate_mbps": 1e9, "acquisition_delay_s": 0.0},
        "time_alignment": {"enabled": True, "arm": "candidate",
                           "execution_mode": mode,
                           "query_delay_s": query_delay},
        "async_routing": {"enabled": False},
    })


def _run(cfg):
    rows = [row(i, 0.0, A, B) for i in range(1, 4)]
    sink, timeline = [], []
    result = kernel.run_simulation(cfg, rows, geometry=_geo(),
                                   decision_sink=sink, timeline_sink=timeline)
    return result, sink, timeline


def _queries(timeline, sat=None):
    rows = [m for m in timeline if m.get("milestone") == "query_start"]
    if sat is not None:
        rows = [m for m in rows if m.get("sat") == sat]
    return rows


def test_a_positive_query_delay_is_charged_and_serialised():
    result, _sink, timeline = _run(_cfg(0.1))
    events = sorted(
        [(m["at"], "start" if m["milestone"] == "query_start" else "finish")
         for m in timeline
         if m.get("sat") == 0
         and m.get("milestone") in ("query_start", "query_finish")])
    assert len(events) >= 6, timeline[:8]
    # ONE server per satellite: service intervals never overlap
    busy_until = None
    for at, kind in events:
        if kind == "start":
            if busy_until is not None:
                assert at >= busy_until - 1e-9, (at, busy_until)
            busy_until = at + 0.1
    totals = result["execution_mode"]["query_service"]["totals"]
    assert totals["requests"] >= 3
    assert totals["total_service_s"] == pytest.approx(
        totals["requests"] * 0.1)
    assert totals["max_wait_s"] > 0.0


def test_the_query_delay_zero_default_keeps_the_instantaneous_path():
    result, _sink, timeline = _run(_cfg(0.0))
    assert not _queries(timeline)
    totals = result["execution_mode"]["query_service"]["totals"]
    assert totals == {"requests": 0, "total_wait_s": 0.0,
                      "total_service_s": 0.0, "max_wait_s": 0.0}
    assert result["execution_mode"]["query_service"]["servers_per_satellite"] == 0


def test_every_execution_mode_pays_the_same_query_service():
    """The five modes must not differ by an unpriced lookup."""
    rows = [row(i, 0.0, A, B) for i in range(1, 4)]
    charged = {}
    for mode in ec.MODES:
        if mode not in ("async_point", "async_window"):
            cfg = _cfg(0.1, mode=mode)
        else:
            cfg = make_cfg({
                "scenario": {"duration_s": 10.0},
                "demand": {"packet_bits": 800_000},
                "execution": {"decision_observation_mode": "frozen",
                              "compute_delay_s": 0.0},
                "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0,
                           "uplink_rate_mbps": 1e9,
                           "acquisition_delay_s": 0.0},
                "time_alignment": {"enabled": True, "arm": "candidate",
                                   "execution_mode": mode,
                                   "query_delay_s": 0.1},
                "async_routing": {"enabled": True, "update_interval_s": 5.0,
                                  "window_bins": 1 if mode == "async_point"
                                  else 4},
            })
        result = kernel.run_simulation(cfg, rows, geometry=_geo(),
                                       decision_sink=[], timeline_sink=[])
        charged[mode] = result["execution_mode"]["query_service"]["totals"]
    for mode, totals in charged.items():
        assert totals["requests"] > 0, (mode, totals)
        assert totals["total_service_s"] == pytest.approx(
            totals["requests"] * 0.1), (mode, totals)
        assert totals["total_wait_s"] >= 0.0


def test_the_query_charge_is_recorded_on_each_decision_audit():
    _result, sink, _timeline = _run(_cfg(0.1))
    forwards = [r for r in sink if r.get("kind") == "forward"]
    assert forwards
    for r in forwards:
        audit = (r.get("observation_at_start") or {}).get("time_alignment")
        assert audit is not None
        assert audit["query"]["requests"] == 1
        assert audit["query"]["service_s"] == pytest.approx(0.1)


def test_the_query_server_never_serves_two_queries_at_once():
    _result, _sink, timeline = _run(_cfg(0.1, packets=3))
    for sat in (0, 1):
        events = sorted(
            [(m["at"], m["milestone"]) for m in timeline
             if m.get("sat") == sat
             and m.get("milestone") in ("query_start", "query_finish")])
        busy_until = None
        for at, kind in events:
            if kind == "query_start":
                if busy_until is not None:
                    assert at >= busy_until - 1e-9, (sat, at, busy_until)
                busy_until = at + 0.1
