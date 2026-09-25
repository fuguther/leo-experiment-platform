"""Round-2 follow-ups: recompute field presence, burst intensity (2026-09-25).

Two silent-pass defects found while closing R11:

1. the production cross-check in recompute_run compared with
   abs(mine - float(other.get(field, float("nan")))) > tol.  Every comparison
   against NaN is False, so a production link that had LOST served_bits,
   capacity_bits or utilization compared equal, contributed no mismatch, and
   left check R5 green on a reading that was never verified.
2. materialization_report answered only "did any packet land in the declared
   burst window".  A window with one packet in it therefore read as an applied
   treatment, and the declared INTENSITY was never tested at all.

Both are counter-example-first: the assertion is that the defect's exact input
now fails.
"""
from __future__ import annotations

import json
import shutil

import pytest

from CODE.leo_sim import config as config_mod
from CODE.leo_sim import recompute, trace as trace_mod
from CODE.leo_sim.tests.test_pre_experiment_trust import _persist, _run

# ------------------------------------------------------------------ recompute
def test_recompute_rejects_a_production_reading_that_lost_a_field(tmp_path):
    cfg, result, _ = _run(interval=1.0)
    run_dir = _persist(tmp_path / "run", cfg, result)

    # positive control first: an untouched reading is green
    baseline = recompute.recompute_run(run_dir)
    assert baseline["production_cross_check"]["mismatch_total"] == 0
    assert baseline["ok"] is True

    for field in ("served_bits", "capacity_bits", "utilization"):
        broken = tmp_path / f"no-{field}"
        shutil.copytree(run_dir, broken)
        payload = json.loads((broken / "ledgers.json").read_text())
        for link in payload["congestion_metrics"]["links"].values():
            assert field in link, "fixture must carry the field to remove it"
            del link[field]
        (broken / "ledgers.json").write_text(json.dumps(payload),
                                             encoding="utf-8")
        report = recompute.recompute_run(broken)
        mismatches = report["production_cross_check"]["mismatches"]
        assert report["production_cross_check"]["mismatch_total"] > 0, field
        assert all(item["field"] == field for item in mismatches), mismatches
        assert all("absent from the production reading" in item["why"]
                   for item in mismatches)
        assert report["checks"][4]["id"] == "R5"
        assert report["checks"][4]["ok"] is False
        assert report["ok"] is False

    # a non-numeric production value is a mismatch, not a crash
    broken = tmp_path / "non-numeric"
    shutil.copytree(run_dir, broken)
    payload = json.loads((broken / "ledgers.json").read_text())
    first = sorted(payload["congestion_metrics"]["links"])[0]
    payload["congestion_metrics"]["links"][first]["served_bits"] = "lots"
    (broken / "ledgers.json").write_text(json.dumps(payload), encoding="utf-8")
    report = recompute.recompute_run(broken)
    assert report["ok"] is False
    assert any("not numeric" in item["why"]
               for item in report["production_cross_check"]["mismatches"])


def test_recompute_rejects_a_link_the_production_reading_invented(tmp_path):
    cfg, result, _ = _run(interval=1.0)
    run_dir = _persist(tmp_path / "run", cfg, result)
    broken = tmp_path / "invented-link"
    shutil.copytree(run_dir, broken)
    payload = json.loads((broken / "ledgers.json").read_text())
    payload["congestion_metrics"]["links"]["isl:99:99"] = {
        "stage": "isl", "served_bits": 0.0, "capacity_bits": 0.0,
        "utilization": 0.0, "available_samples": 0}
    (broken / "ledgers.json").write_text(json.dumps(payload), encoding="utf-8")
    report = recompute.recompute_run(broken)
    assert report["ok"] is False
    assert any(item["link_id"] == "isl:99:99"
               for item in report["production_cross_check"]["mismatches"])


# ------------------------------------------------------------- burst intensity
def _burst_cfg(**demand) -> dict:
    base = {
        "scenario": {"name": "intensity", "duration_s": 60.0,
                     "time_step_s": 0.1, "num_satellites": 8,
                     "num_planes": 1, "seed": 7},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0}]},
        "demand": {"mode": "burst", "offered_mbps": 40.0, "burst_start_s": 10.0,
                   "burst_duration_s": 10.0, "burst_multiplier": 5.0},
        "control_plane": {"enabled": False},
        "routing": {"policy": "hop"},
        "execution": {"max_packets": 100000},
    }
    base["demand"].update(demand)
    return config_mod.resolve_config(base)


def test_burst_intensity_is_reported_as_compatible_not_as_proof(tmp_path):
    resolved = _burst_cfg()
    out = tmp_path / "trace"
    trace_mod.compile_trace(resolved, str(out))
    rows = trace_mod.load_trace(str(out / "trace.csv"))
    report = trace_mod.materialization_report(resolved, rows)
    burst = report["burst"]
    assert burst["applied"] is True
    intensity = burst["intensity"]
    # COMPATIBLE is the strongest word this diagnostic is allowed to use, and
    # the report says outright that it is not a correctness proof.
    assert intensity["status"] == "COMPATIBLE"
    assert burst["intensity_compatible"] is True
    assert "intensity_verified" not in burst
    assert "not a proof" in intensity["reason"]
    # the declared tolerance and the raw sample are published, not implied
    assert intensity["sigma"] == trace_mod.INTENSITY_SIGMA
    assert intensity["expected_packets_if_burst_applied"] == pytest.approx(250.0)
    assert intensity["expected_packets_if_burst_not_applied"] == pytest.approx(50.0)
    assert intensity["scenario_seed"] == 7
    assert abs(intensity["observed_packets"]
               - intensity["expected_packets_if_burst_applied"]) <= \
        intensity["acceptance_band"]


def test_a_nonempty_burst_window_does_not_prove_the_declared_intensity(tmp_path):
    """The counter-example: multiplier 1.2, window full of packets.

    The window is non-empty (so the historical check called the treatment
    applied) but 60 expected packets inside against 50 without the transform
    cannot be separated at 3 sigma, so the honest verdict is UNDECIDABLE --
    never \"intensity accepted\".
    """
    resolved = _burst_cfg(burst_multiplier=1.2)
    out = tmp_path / "trace"
    trace_mod.compile_trace(resolved, str(out))
    rows = trace_mod.load_trace(str(out / "trace.csv"))
    report = trace_mod.materialization_report(resolved, rows)
    burst = report["burst"]
    assert burst["applied"] is True
    assert burst["packets_inside_window"] > 1
    intensity = burst["intensity"]
    assert intensity["status"] == "UNDECIDABLE"
    assert burst["intensity_compatible"] is False
    assert intensity["predicted_separation"] <= intensity["required_separation"]
    assert "NOT evidence" in intensity["reason"]
    # UNDECIDABLE is reported, not raised: the mechanism is applied, its
    # magnitude simply cannot be established from this sample.
    assert report["problems"] == []


def test_an_incompatible_draw_is_reported_and_never_blocks_the_compile(tmp_path):
    """A draw far from its mean is a property of the SEED, not a defect.

    The sample below is the shape of the independent review's finding: a
    legitimate generator, one seed, a count well outside the 3-sigma band.  The
    compiler must record it and keep the sample, the seed and the distance.
    """
    resolved = _burst_cfg()
    starved = [{}] * 20  # 20 observed against 250 expected
    intensity = trace_mod._burst_intensity_diagnostics(
        resolved, 10.0, 20.0, starved, len(starved))
    assert intensity["status"] == "INCOMPATIBLE"
    assert intensity["observed_packets"] == 20
    assert intensity["raw_sample_preserved"]["all_emitted"] == len(starved)
    assert intensity["scenario_seed"] == 7
    assert intensity["sigma_distance"] > trace_mod.INTENSITY_SIGMA
    assert "not as a defect of the transform" in intensity["reason"]

    rows = [{"packet_id": index, "emit_time_s": 0.5 * index,
             "src_grid_id": "src", "dst_grid_id": "dst",
             "bits": 8_000_000, "deadline_at_s": None}
            for index in range(1, 21)]
    report = trace_mod.materialization_report(resolved, rows,
                                              declared_cells={"src", "dst"})
    assert report["burst"]["intensity"]["status"] == "INCOMPATIBLE"
    assert report["burst"]["intensity_compatible"] is False
    # reported, not raised: the transform check passed, only the draw is far out
    assert report["problems"] == []
