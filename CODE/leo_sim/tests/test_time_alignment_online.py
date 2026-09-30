"""T1-COMPLETE P5: the four state-time arms in the real forwarding path."""
from __future__ import annotations

import json

import pytest

from CODE.leo_sim import config, kernel, time_alignment as ta
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=VIS)


def _cfg(ta_over=None, seed=7):
    ta_cfg = {"enabled": True, "arm": "candidate"}
    ta_cfg.update(ta_over or {})
    return make_cfg({"scenario": {"duration_s": 12.0, "seed": seed},
                     "execution": {"decision_observation_mode": "frozen",
                                   "compute_delay_s": 0.5},
                     "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
                     "time_alignment": ta_cfg})


def _run(cfg, rows=None):
    sink, timeline = [], []
    rows = rows if rows is not None else [row(i, 0.5 * i, A, B)
                                          for i in (1, 2, 3)]
    res = kernel.run_simulation(cfg, rows, geometry=_geo(),
                                decision_sink=sink, timeline_sink=timeline)
    return res, sink, timeline


def _forward_rows(sink):
    return [r for r in sink if r.get("kind") == "forward"]


def test_disabled_time_alignment_leaves_the_audit_empty():
    _res, sink, _tl = _run(make_cfg({"execution": {
        "decision_observation_mode": "frozen", "compute_delay_s": 0.5}}))
    assert sink
    for r in _forward_rows(sink):
        assert (r["observation_at_start"] or {}).get("time_alignment") is None


def test_enabling_time_alignment_records_a_replayable_audit():
    _res, sink, _tl = _run(_cfg())
    forwards = _forward_rows(sink)
    assert forwards, "the fixture must forward"
    for r in forwards:
        audit = r["observation_at_start"]["time_alignment"]
        assert audit is not None
        assert audit["arm"] == "candidate"
        assert audit["predictor"] == "bounded_linear"
        assert audit["snapshot_at"] == r["t_decision_start"]
        assert audit["ranking"]
        assert audit["applied_order"]
        assert set(audit["applied_order"]) == set(r["candidates"])
        assert "received advertisements" in audit["source"]


def test_the_snapshot_only_contains_already_received_history():
    _res, sink, _tl = _run(_cfg())
    for r in _forward_rows(sink):
        obs = r["observation_at_start"]
        t0 = r["t_decision_start"]
        for neighbour in (obs.get("neighbours") or {}).values():
            for rec in neighbour.get("advertised_history") or []:
                assert rec["received_at"] <= t0 + 1e-12
                assert rec["generated_at"] <= rec["received_at"] + 1e-12


def test_all_four_arms_see_the_same_t0_input():
    def keyed(forwards):
        return json.dumps([
            {"sat": r["sat"],
             "candidates": sorted(r["candidates"]),
             "resources": sorted(
                 (d, cr.get("peer"), cr.get("egress_direction"))
                 for d, cr in (r["observation_at_start"].get(
                     "candidate_resources") or {}).items())}
            for r in forwards], sort_keys=True, default=str)

    seen = {}
    for arm in ta.ARMS:
        _res, sink, _tl = _run(_cfg({"arm": arm}))
        forwards = _forward_rows(sink)
        assert forwards
        seen[arm] = keyed(forwards)
    assert len(set(seen.values())) == 1


def test_a_constant_state_makes_every_arm_choose_the_same_action():
    chosen = {}
    for arm in ta.ARMS:
        _res, sink, _tl = _run(_cfg({"arm": arm}))
        forwards = _forward_rows(sink)
        chosen[arm] = [r["chosen"] for r in forwards]
    assert len(set(tuple(v) for v in chosen.values())) == 1, chosen


def test_the_audit_never_mentions_a_truth_source():
    _res, sink, _tl = _run(_cfg())
    for r in _forward_rows(sink):
        blob = json.dumps(r["observation_at_start"]["time_alignment"],
                          default=str)
        for forbidden in ("truth_at_commit", "candidate_truth",
                          "deliveries", "futures"):
            assert forbidden not in blob


def test_fixed_horizon_config_is_honoured_in_the_audit():
    _res, sink, _tl = _run(_cfg({"common_rule": "fixed_horizon",
                                 "common_horizon_s": 1.0, "arm": "common"}))
    for r in _forward_rows(sink):
        audit = r["observation_at_start"]["time_alignment"]
        assert audit["common_horizon_s"] == 1.0


def test_unknown_arm_is_rejected_by_the_config():
    with pytest.raises(config.ConfigError):
        _cfg({"arm": "oracle"})
