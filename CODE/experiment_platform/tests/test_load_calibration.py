"""Mock-only tests for the bounded calibration entry point."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from CODE.experiment_platform import load_calibration as lc
from CODE.leo_sim import config as config_mod


REPO_ROOT = Path(lc.__file__).resolve().parents[2]
PROFILE = lc.BASE_PROFILE


def _contract(*, overrides=None, arms=None):
    return {
        "schema": lc.CONTRACT_SCHEMA,
        "base_profile": PROFILE,
        "deadline_s": 4.0,
        "max_packets": 20000,
        "max_duration_s": 12.0,
        "cells": [{"id": "one", "arms": arms or ["stale"],
                   "overrides": overrides or {}}],
    }


def _write_contract(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _fake_trace():
    return [
        {"packet_id": 1, "emit_time_s": 0.0, "src_grid_id": "a",
         "dst_grid_id": "b", "bits": 100, "deadline_at_s": None},
        {"packet_id": 2, "emit_time_s": 0.5, "src_grid_id": "a",
         "dst_grid_id": "c", "bits": 100, "deadline_at_s": None},
    ]


def _decision(pid, works):
    scores = {}
    for direction, work in zip(("N", "E"), works):
        scores[direction] = {
            "total_s": 1.0,
            "terms": {"resource_work_s": work},
            "missing": [],
        }
    return {
        "t": 1.0,
        "t_decision_start": 0.9,
        "decision_id": pid,
        "pid": pid,
        "sat": 4,
        "kind": "forward",
        "candidates": ["N", "E"],
        "chosen": "N",
        "observation_at_start": {
            "time_alignment": {"scores": scores, "eta_terms": {"N": {}},
                                "prediction": {"preserve": True}},
            "legal_directions": ["N", "E"],
            "four_direction_audit": {"N": {"legal": True}},
            "neighbours": {"large": "must not be retained"},
        },
        "truth_at_commit": {"t_observed": 1.0},
        "estimate_at_start": {"direction": "N"},
        "advertised_history": ["large"],
    }


def _fake_result():
    return {
        "natural_end": True,
        "interrupted": False,
        "error": None,
        "events_processed": 10,
        "horizon_s": 8.0,
        "stop_time_s": 4.5,
        "packet_events": [
            {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
            {"kind": "packet_emitted", "pid": 2, "at": 0.5, "bits": 100},
        ],
        "deliveries": {1: {"delivered_at": 3.9, "path": [1, 2]}},
        "fates": {1: "DELIVERED", 2: "IN_SYSTEM_AT_STOP"},
        "queue_area_bits_s": {"isl_data": 3.0},
        "access": {"admitted": 2},
        "mechanisms": {"effective": {"time_alignment": True}},
        "service_log": [{"kind": "service", "at": 1.0}],
        "topology_trace": [{"at": 0.0, "edges": []}],
    }


def _free_disk(monkeypatch, free=6 * 1024**3):
    monkeypatch.setattr(lc.shutil, "disk_usage",
                        lambda _path: SimpleNamespace(free=free))


def test_preflight_parses_config_and_boundaries_without_trace_or_kernel(
        tmp_path, monkeypatch):
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    out_dir = tmp_path / "new-run"
    _free_disk(monkeypatch)
    monkeypatch.setattr(lc.t1_tasks, "design",
                        lambda **_kwargs: pytest.fail("preflight compiled trace"))
    monkeypatch.setattr(lc.kernel, "run_simulation",
                        lambda *_args, **_kwargs: pytest.fail("preflight called kernel"))

    result = lc.run_calibration(contract_path, "one", out_dir,
                                do_preflight=True, repo_root=REPO_ROOT)

    assert result["diagnostic"] == "NOT_FULL_REPLAY_DIAGNOSTIC"
    assert result["formal"] is False and result["full_replay"] is False
    assert result["trace_compiled"] is False and result["kernel_calls"] == 0
    assert not out_dir.exists()


@pytest.mark.parametrize("overrides", [
    {"scenario": {"duration_s": 12.01}},
    {"execution": {"max_packets": 20001}},
])
def test_contract_overrides_cannot_exceed_frozen_bounds(tmp_path, monkeypatch,
                                                         overrides):
    contract_path = _write_contract(tmp_path / "contract.json",
                                    _contract(overrides=overrides))
    _free_disk(monkeypatch)
    with pytest.raises(lc.CalibrationError, match="exceeds contract cap"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_contract_rejects_arbitrary_overrides(tmp_path, monkeypatch):
    contract_path = _write_contract(
        tmp_path / "contract.json",
        _contract(overrides={"routing": {"policy": "oracle"}}))
    _free_disk(monkeypatch)
    with pytest.raises(lc.CalibrationError, match="unsupported override group"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_output_exists_is_refused_without_modifying_it(tmp_path, monkeypatch):
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    out_dir = tmp_path / "already-there"
    out_dir.mkdir()
    marker = out_dir / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="already exists"):
        lc.preflight(contract_path, "one", out_dir, REPO_ROOT)
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_decision_projection_is_lossless_for_declared_fields_and_no_mutation(
        tmp_path, monkeypatch):
    row = _decision(8, [0.0, 0.0])
    original = copy.deepcopy(row)
    budget = lc._OutputBudget()
    sink = lc._JsonlSink(tmp_path / "decisions.jsonl.gz", budget, "decision")
    sink.append(row)
    sink.close()

    assert row == original
    with gzip.open(tmp_path / "decisions.jsonl.gz", "rt", encoding="utf-8") as stream:
        saved = json.loads(stream.readline())
    assert saved["observation_at_start"]["time_alignment"]["prediction"] == {
        "preserve": True}
    assert saved["observation_at_start"]["legal_directions"] == ["N", "E"]
    assert saved["observation_at_start"]["four_direction_audit"] == {
        "N": {"legal": True}}
    assert "neighbours" not in saved["observation_at_start"]
    assert "advertised_history" not in saved


def test_sink_stats_count_zero_and_nonzero_work_and_complete_candidates(tmp_path):
    budget = lc._OutputBudget()
    sink = lc._JsonlSink(tmp_path / "decisions.jsonl.gz", budget, "decision")
    sink.append(_decision(8, [0.0, 0.0]))
    sink.append(_decision(9, [0.25, 0.5]))
    sink.close()
    stats = sink.summary()

    assert stats["rows"] == 2
    assert stats["forward_pids"] == [8, 9]
    assert stats["forward_pids_count"] == 2
    assert stats["nonzero_resource_work_candidate_count"] == 2
    assert stats["decisions_with_at_least_two_complete_candidates"] == 2


def test_d4_requires_complete_emission_set_and_matured_windows():
    rows = _fake_trace()
    result = _fake_result()
    complete = lc.d4_outcome(result, rows, 4.0)
    assert complete["status"] == "COMPUTED"
    assert complete["offered_packets"] == 2
    assert complete["deadline_not_delivered_count"] == 1
    assert complete["deadline_not_delivered_proportion"] == 0.5
    assert complete["capped_loss"] == pytest.approx((3.9 / 4.0 + 1.0) / 2)

    incomplete_emission = copy.deepcopy(result)
    incomplete_emission["packet_events"].pop()
    outcome = lc.d4_outcome(incomplete_emission, rows, 4.0)
    assert outcome["status"] == "NOT_COMPARABLE"
    assert outcome["reason"] == "incomplete_packet_emission_set"
    assert outcome["deadline_not_delivered_proportion"] is None

    short_stop = copy.deepcopy(result)
    short_stop["stop_time_s"] = 4.49
    outcome = lc.d4_outcome(short_stop, rows, 4.0)
    assert outcome["status"] == "NOT_COMPARABLE"
    assert outcome["reason"] == "stop_before_all_D4_windows_matured"


def test_one_arm_calls_mock_kernel_once_and_publishes_full_result(tmp_path,
                                                                  monkeypatch):
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    out_dir = tmp_path / "run"
    _free_disk(monkeypatch)
    base = config_mod.load_config_file(str(REPO_ROOT / PROFILE))
    rows = _fake_trace()
    design_calls = []
    kernel_calls = []

    def fake_design(*, config_path, overrides):
        design_calls.append((config_path, copy.deepcopy(overrides)))
        cfg = copy.deepcopy(base["config"])
        lc._deep_merge(cfg, overrides)
        return config_mod.resolve_config(cfg), rows, object(), {
            "trace_sha256": "a" * 64,
            "rows_digest": "b" * 64,
            "rows": len(rows),
            "native_population": {"population_sha256": "c" * 64},
        }

    def fake_kernel(resolved, passed_rows, *, geometry, decision_sink,
                    timeline_sink, control_audit):
        kernel_calls.append((resolved["config"]["time_alignment"]["arm"],
                             passed_rows, control_audit))
        decision_sink.append(_decision(1, [0.25, 0.0]))
        timeline_sink.append({"milestone": "decision", "at": 1.0,
                              "payload": "full"})
        return _fake_result()

    monkeypatch.setattr(lc.t1_tasks, "design", fake_design)
    monkeypatch.setattr(lc.kernel, "run_simulation", fake_kernel)

    summary = lc.run_calibration(contract_path, "one", out_dir,
                                 repo_root=REPO_ROOT)

    assert len(design_calls) == 1
    assert len(kernel_calls) == 1
    assert kernel_calls[0][0] == "stale"
    assert kernel_calls[0][1] is rows
    assert kernel_calls[0][2] is True
    assert summary["status"] == "DIAGNOSTIC_COMPLETE"
    assert summary["kernel_call_count"] == 1
    assert summary["full_replay"] is False
    assert summary["arms"][0]["config_sha256"]
    result_path = out_dir / "arms/stale/result.json.gz"
    with gzip.open(result_path, "rt", encoding="utf-8") as stream:
        saved = json.load(stream)
    assert saved["service_log"] == [{"kind": "service", "at": 1.0}]
    assert saved["topology_trace"] == [{"at": 0.0, "edges": []}]
    assert json.loads((out_dir / "summary.json").read_text(encoding="utf-8")) == summary
    assert all("sha256" in artifact for artifact in summary["artifact_files"])
