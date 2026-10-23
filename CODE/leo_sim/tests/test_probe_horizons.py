"""Unit tests for multi-horizon probe row expansion."""
from __future__ import annotations

from CODE.leo_sim.target_probe import expand_horizons


def test_expand_horizons_keeps_prediction_and_moves_target():
    rows = [{"eval_target_s": 1.0, "predicted_bits": 5},
            {"eval_target_s": None, "predicted_bits": None}]
    out = expand_horizons(rows, (0.01, 0.05))
    assert len(out) == 4          # base + two horizons + one passthrough
    times = sorted(r["eval_target_s"] for r in out if r["eval_target_s"])
    assert times == [1.0, 1.01, 1.05]
    assert [r["horizon_offset_s"] for r in out
            if r.get("horizon_offset_s") is not None] == [0.0, 0.01, 0.05]
    for row in out:
        if row.get("horizon_offset_s") is not None:
            assert row["predicted_bits"] == 5
            assert row["base_eval_target_s"] == 1.0


def test_expand_horizons_noop_without_horizons():
    rows = [{"eval_target_s": 1.0}]
    assert expand_horizons(rows, ()) == rows
