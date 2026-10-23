"""Unit tests for the prediction-accuracy aggregator (synthetic rows only)."""
from __future__ import annotations

from CODE.experiment_platform import prediction_accuracy as pa


def _row(arm="stale", direction="N", predicted=100, truth=80,
         measured=0.0, used=0.01, target=0.02, selected=True):
    return {
        "arm": arm, "direction": direction, "selected": selected,
        "predicted_bits": predicted,
        "eval_target_s": target, "state_measured_at_s": measured,
        "state_used_at_s": used, "t_commit_s": used,
        "truth": {"work_ahead_bits": truth},
    }


def test_coverage_and_error_sign():
    rows = [_row(predicted=100, truth=80), _row(direction="S", predicted=0,
                                                truth=50),
            {"arm": "stale", "direction": "E", "missing_reason": "no_prediction"}]
    doc = pa.summarize(rows)
    assert doc["rows"] == 3
    assert doc["labeled"] == 2
    assert abs(doc["coverage"] - 2 / 3) < 1e-12
    assert doc["missing_reasons"] == {"no_prediction": 1}
    # bias = predicted - truth: (+20 + -50) / 2 = -15
    assert abs(doc["overall"]["bias_mean_bits"] + 15.0) < 1e-12
    assert abs(doc["overall"]["mae_bits"] - 35.0) < 1e-12
    assert abs(doc["overall"]["truth_nonzero_rate"] - 1.0) < 1e-12


def test_buckets_and_groups():
    rows = [
        _row(arm="stale", direction="N", measured=0.0, used=0.01, target=0.02),
        _row(arm="common", direction="S", measured=0.0, used=0.20, target=0.30),
        _row(arm="common", direction="S", measured=0.0, used=0.01, target=0.02,
             selected=False),
    ]
    doc = pa.summarize(rows)
    assert set(doc["by_arm"]) == {"stale", "common"}
    assert set(doc["by_direction"]) == {"N", "S"}
    # first row age = target - measured = 20 ms -> 10-25ms bucket
    assert "10-25ms" in doc["by_age_bucket"]
    # second row horizon = target - used = 100 ms -> 25ms+
    assert "25ms+" in doc["by_horizon_bucket"]
    assert "chosen" in doc["by_selection"]
    assert "not_chosen" in doc["by_selection"]


def test_missing_truth_is_not_scored():
    rows = [{"arm": "stale", "direction": "N", "predicted_bits": 10,
             "truth": None, "missing_reason": "resource_missing"}]
    doc = pa.summarize(rows)
    assert doc["labeled"] == 0
    assert doc["overall"]["n"] == 0
    assert doc["missing_reasons"] == {"resource_missing": 1}
