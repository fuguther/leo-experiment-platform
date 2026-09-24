"""Decision-level differential snapshot tests (comparison arms).

Direct arm: the kernel decision sink records every per-hop routing decision
(candidates, chosen action, own-queue snapshot, observation summary) without
changing behavior. Legacy arm: the retained runtime is read-only, so its
snapshot is normalized from its own packet_fate diagnostic dump
(SimulationRL.py:1292, columns :870-873) — path granularity only.
"""
from __future__ import annotations

import csv
import gzip
import json

import pytest

from CODE.leo_sim import comparison, kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)


def _two_sat_geo():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    return StaticGeometry(2, neighbors_map=nb, visible=vis)


def test_direct_arm_decision_sink_records_every_hop():
    rows = [row(1, 0.0, A, B)]
    sink: list[dict] = []
    res = kernel.run_simulation(make_cfg(), rows, geometry=_two_sat_geo(),
                                decision_sink=sink)
    assert res["fates"][1] == "DELIVERED"
    # hop 1: forward at sat0 (only candidate E); hop 2: deliver at sat1
    assert [r["kind"] for r in sink] == ["forward", "deliver"]
    fwd, dlv = sink
    assert fwd["sat"] == 0 and fwd["candidates"] == ["E"]
    assert fwd["chosen"] == "E" and fwd["policy"] == "oracle"
    assert fwd["own_queue_bits"] == {"E": 0}
    assert fwd["obs"] is None  # non-learning run: no observation vector
    assert dlv["sat"] == 1 and dlv["chosen"] == "deliver"
    assert dlv["candidates"] == ["deliver"]
    assert all(r["pid"] == 1 for r in sink)


def test_decision_sink_records_per_action_physical_audit_fields():
    rows = [row(1, 0.0, A, B)]
    sink: list[dict] = []
    kernel.run_simulation(make_cfg(), rows, geometry=_two_sat_geo(),
                           decision_sink=sink)
    forward = sink[0]
    audit = forward["info_audit"]
    assert audit["schema"] == "leo-sim-decision-info/v1"
    assert audit["contract"] is None
    candidate = audit["candidate_truth"]["E"]
    assert candidate["edge"] == [0, 1]
    assert candidate["distance_km"] == 1000.0
    assert candidate["rate_bps"] == 1_000_000_000.0
    assert candidate["available"] is True
    assert candidate["peer_egress_queue_bits"] == 0
    assert candidate["reverse_link_queue_bits"] == 0
    assert candidate["topology_available"] is True
    source = candidate["field_sources"]["distance_km"]
    assert source["source"] == "direct_kernel_state"
    assert source["observed_at"] == forward["t"]
    assert source["age_s"] == 0.0
    assert audit["cache_entries"] == {}


def test_decision_sink_does_not_change_behavior():
    rows = [row(i, 0.0, A, B) for i in (1, 2, 3)]
    base = kernel.run_simulation(make_cfg(), rows, geometry=_two_sat_geo())
    sink: list[dict] = []
    with_sink = kernel.run_simulation(make_cfg(), rows,
                                      geometry=_two_sat_geo(),
                                      decision_sink=sink)
    for key in ("fates", "fate_counts", "totals", "deliveries", "occupied",
                "queue_area_bits_s", "access", "service_log", "handover",
                "events_processed"):
        assert with_sink[key] == base[key], key
    # 3 packets x 2 hops each
    assert len(sink) == 6


def test_decision_sink_default_off_is_zero_overhead_path():
    k = kernel.Kernel(make_cfg(), [row(1, 0.0, A, B)],
                      geometry=_two_sat_geo())
    assert k.decision_sink is None


def test_legacy_decision_rows_from_packet_fate_csv_gz(tmp_path):
    cols = ["block_id", "od_pair", "birth_time", "death_time", "n_hops",
            "status", "sum_local_rewards", "e2e_latency", "path_csv"]
    rows = [
        ["b1", "A>B", "0.0", "0.5", "3", "0", "0.0", "0.5", "GT_A|sat_0_1|sat_0_2"],
        ["b2", "A>B", "0.1", "0.9", "1", "1", "0.0", "0.8", "GT_A"],
    ]
    with gzip.open(tmp_path / "packet_fate.csv.gz", "wt", encoding="utf-8",
                   newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(cols)
        writer.writerows(rows)
    out = comparison._legacy_decision_rows(tmp_path)
    # b1: GT_A -> sat_0_1 -> sat_0_2 -> DELIVERED (3 hop rows)
    b1 = [r for r in out if r["pid"] == "b1"]
    assert [(r["hop"], r["sat"], r["chosen"], r["kind"]) for r in b1] == [
        (0, "GT_A", "sat_0_1", "forward"),
        (1, "sat_0_1", "sat_0_2", "forward"),
        (2, "sat_0_2", "DELIVERED", "terminal"),
    ]
    # b2: lost after source GT
    b2 = [r for r in out if r["pid"] == "b2"]
    assert [(r["hop"], r["chosen"], r["kind"]) for r in b2] == [
        (0, "LOST", "terminal")]
    assert all(r["candidates"] is None and r["obs"] is None for r in out)


def test_legacy_decision_rows_missing_dump_fails_loud(tmp_path):
    with pytest.raises(comparison.ComparisonError):
        comparison._legacy_decision_rows(tmp_path)


def test_write_decisions_jsonl_roundtrip(tmp_path):
    path = comparison._write_decisions_jsonl(
        [{"b": 1, "a": "x"}, {"a": [1, 2]}], tmp_path / "decisions.jsonl")
    lines = (tmp_path / "decisions.jsonl").read_text().splitlines()
    assert [json.loads(l) for l in lines] == [{"a": "x", "b": 1},
                                              {"a": [1, 2]}]
    assert path.endswith("decisions.jsonl")
