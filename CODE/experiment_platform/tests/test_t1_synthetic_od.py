from __future__ import annotations

import csv
import hashlib
from pathlib import Path

import pytest

from CODE.experiment_platform import synthetic_od


def _workload():
    sites = [
        {"id": "equator", "lat": 0.1, "lon": 0.1},
        {"id": "north", "lat": 52.6, "lon": 90.4},
        {"id": "west", "lat": 0.1, "lon": -80.1},
    ]
    flows = [
        {"id": "equator_to_north", "src": "equator", "dst": "north"},
        {"id": "equator_to_west", "src": "equator", "dst": "west"},
        {"id": "north_to_equator", "src": "north", "dst": "equator"},
        {"id": "north_to_west", "src": "north", "dst": "west"},
        {"id": "west_to_equator", "src": "west", "dst": "equator"},
        {"id": "west_to_north", "src": "west", "dst": "north"},
    ]
    return {
        "schema": synthetic_od.WORKLOAD_SCHEMA,
        "workload_id": "temporal_multi_od_transfer",
        "packet_bits": 1_000_000,
        "jitter_s": 0.02,
        "sites": sites,
        "flows": flows,
        "phases": [
            {"id": "low", "start_s": 0.0, "end_s": 4.0,
             "rates_pps": {flow["id"]: 0.5 for flow in flows}},
            {"id": "growth", "start_s": 4.0, "end_s": 12.0,
             "rates_pps": {
                 flow["id"]: (3.0 if flow["id"] in {
                     "equator_to_north", "north_to_equator"} else 1.0)
                 for flow in flows}},
            {"id": "transfer", "start_s": 12.0, "end_s": 16.0,
             "rates_pps": {
                 flow["id"]: (3.0 if flow["id"] in {
                     "north_to_west", "west_to_north"} else 1.0)
                 for flow in flows}},
            {"id": "decay", "start_s": 16.0, "end_s": 20.0,
             "rates_pps": {flow["id"]: 0.5 for flow in flows}},
        ],
    }


def test_synthetic_od_trace_is_seed_bound_multiod_and_phase_auditable(tmp_path):
    spec = _workload()
    first = tmp_path / "seed-11-a.csv"
    second = tmp_path / "seed-11-b.csv"
    other = tmp_path / "seed-23.csv"

    summary = synthetic_od.write_trace(first, spec, seed=11)
    synthetic_od.write_trace(second, spec, seed=11)
    synthetic_od.write_trace(other, spec, seed=23)

    first_bytes = first.read_bytes()
    assert first_bytes == second.read_bytes()
    assert hashlib.sha256(first_bytes).hexdigest() != hashlib.sha256(
        other.read_bytes()).hexdigest()
    with first.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 144
    assert {row["od_id"] for row in rows} == {
        flow["id"] for flow in spec["flows"]}
    assert {row["phase_id"] for row in rows} == {
        "low", "growth", "transfer", "decay"}
    assert len({int(row["packet_id"]) for row in rows}) == len(rows)
    assert all(0.0 <= float(row["emit_time_s"]) < 20.0 for row in rows)
    assert {phase: count for phase, count in summary["phase_packet_counts"].items()} == {
        "low": 12, "growth": 80, "transfer": 40, "decay": 12}
    assert summary["unique_od_count"] == 6
    assert summary["maximum_active_od_count"] == 6
    assert summary["input_sha256"] == hashlib.sha256(first_bytes).hexdigest()


def test_synthetic_od_trace_refuses_overwrite_and_bad_matrix(tmp_path):
    spec = _workload()
    path = tmp_path / "trace.csv"
    synthetic_od.write_trace(path, spec, seed=7)
    with pytest.raises(FileExistsError):
        synthetic_od.write_trace(path, spec, seed=7)

    bad = _workload()
    bad["phases"][1]["rates_pps"].pop("equator_to_north")
    with pytest.raises(synthetic_od.WorkloadError, match="every declared OD"):
        synthetic_od.render_trace(bad, seed=7)
