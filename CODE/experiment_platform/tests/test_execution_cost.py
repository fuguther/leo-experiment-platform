"""T1-COMPLETE S3: a table lookup must not pay a per-packet computation.

The five execution modes exist to compare COMPUTE REUSE.  If a mode that reads
an installed table still waits the full configured computation for every
packet, every arm pays the same and the comparison measures nothing.
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import execution_compare as ec, scripted_scenarios
from CODE.leo_sim import kernel


def _run(mode, *, servers=1, service=0.05, query=0.001, scenario="same_flow"):
    resolved, rows, geometry, _meta = scripted_scenarios.build(scenario)
    cfg = ec._mode_config(resolved, mode, {
        "execution": {"compute_servers_per_satellite": servers,
                      "compute_delay_s": service},
        "time_alignment": {"query_delay_s": query},
    })
    timeline, sink = [], []
    result = kernel.run_simulation(cfg, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=timeline)
    return result, sink, timeline


def _packet_compute(timeline):
    return [m for m in timeline
            if m.get("milestone") == "compute_request" and m.get("pid") is not None]


def _background_compute(timeline):
    return [m for m in timeline
            if m.get("milestone") == "compute_request" and m.get("pid") is None]


def test_a_table_lookup_mode_issues_no_packet_compute_request():
    for mode in ("precomputed", "async_point", "async_window"):
        _result, _sink, timeline = _run(mode)
        assert _packet_compute(timeline) == [], mode


def test_per_packet_and_per_flow_misses_still_compute():
    _r1, _s1, tl1 = _run("per_packet")
    packets_1 = _packet_compute(tl1)
    assert packets_1, "per_packet must compute per decision"
    _r2, _s2, tl2 = _run("per_flow")
    packets_2 = _packet_compute(tl2)
    assert packets_2, "per_flow must still compute on a miss"
    assert len(packets_2) < len(packets_1), (len(packets_2), len(packets_1))


def test_the_async_background_job_pays_the_real_compute_cost():
    _result, _sink, timeline = _run("async_window")
    background = _background_compute(timeline)
    assert background, "the async update must run as an accounted job"
    finishes = [m for m in timeline
                if m.get("milestone") == "compute_finish"
                and m.get("pid") is None]
    assert finishes
    assert finishes[0]["at"] - background[0]["at"] == pytest.approx(0.05)
    assert _packet_compute(timeline) == []


def test_every_mode_pays_the_public_query_service():
    for mode in ec.MODES:
        result, sink, timeline = _run(mode)
        totals = result["execution_mode"]["query_service"]["totals"]
        forwards = [r for r in sink if r.get("kind") == "forward"]
        assert forwards
        assert totals["requests"] > 0, mode
        assert totals["total_service_s"] == pytest.approx(
            totals["requests"] * 0.001), mode


def test_a_zero_query_cost_changes_nothing_about_compute_reuse():
    for mode in ("precomputed", "async_point", "async_window"):
        result, _sink, timeline = _run(mode, query=0.0)
        assert _packet_compute(timeline) == [], mode
        totals = result["execution_mode"]["query_service"]["totals"]
        assert totals == {"requests": 0, "total_wait_s": 0.0,
                          "total_service_s": 0.0, "max_wait_s": 0.0}


def test_the_precompute_build_cost_is_reported_separately():
    result, _sink, timeline = _run("precomputed")
    pre = result["execution_mode"]["precompute"]
    assert pre["builds"] == 1 and pre["installs"] == 1
    assert pre["targets"] > 0
    assert pre["build_wall_s"] > 0.0
    assert "NOT a per-packet compute charge" in pre["cost_kind"]
    assert _packet_compute(timeline) == []


def test_the_mode_configs_are_machine_verified():
    resolved, _rows, _geometry, _meta = scripted_scenarios.build("same_flow")
    for mode in ec.MODES:
        cfg = ec._mode_config(resolved, mode, {
            "execution": {"compute_servers_per_satellite": 1}})
        assert cfg["config"]["time_alignment"]["execution_mode"] == mode
        assert cfg["config"]["execution"]["compute_servers_per_satellite"] == 1
        if mode in ec.ASYNC_MODES:
            assert cfg["config"]["async_routing"]["enabled"] is True
        else:
            assert cfg["config"]["async_routing"]["enabled"] is False
