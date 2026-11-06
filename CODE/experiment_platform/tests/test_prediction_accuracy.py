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


def test_extra_horizon_does_not_pollute_overall():
    base = _row(predicted=100, truth=80)
    base["horizon_offset_s"] = 0.0
    later = _row(predicted=100, truth=300, target=0.07)
    later["base_eval_target_s"] = 0.02
    later["horizon_offset_s"] = 0.05
    doc = pa.summarize([base, later])
    assert doc["base_rows"] == 1
    assert doc["extra_horizon_rows"] == 1
    assert doc["overall"]["n"] == 1
    assert abs(doc["overall"]["bias_mean_bits"] - 20.0) < 1e-12
    assert doc["by_horizon_offset"]["+0.050s"]["n"] == 1
    assert doc["by_horizon_offset"]["+0.050s"]["bias_mean_bits"] == -200.0


def test_forced_rows_excluded_from_accuracy_and_coverage():
    ok = _row(predicted=100, truth=80)
    forced = _row(predicted=0, truth=100, measured=0.0, used=0.01, target=0.02)
    forced["forced"] = True
    doc = pa.summarize([ok, forced])
    assert doc["forced_rows"] == 1
    assert doc["base_rows"] == 1
    assert doc["labeled"] == 1
    assert doc["overall"]["n"] == 1
    assert abs(doc["overall"]["bias_mean_bits"] - 20.0) < 1e-12
    assert abs(doc["coverage"] - 1.0) < 1e-12


def test_identity_mismatch_fails_closed():
    one = _row(); one["run_id"] = "r1"; one["config_sha256"] = "a" * 64
    two = _row(); two["run_id"] = "r1"; two["config_sha256"] = "b" * 64
    try:
        pa.summarize([one, two])
    except ValueError as exc:
        assert "identity" in str(exc)
    else:
        raise AssertionError("mixed config identity was accepted")


def test_identity_consistent_is_reported():
    one = _row(); one["run_id"] = "r1"; one["config_sha256"] = "a" * 64
    two = _row(); two["run_id"] = "r1"; two["config_sha256"] = "a" * 64
    doc = pa.summarize([one, two])
    assert doc["identity"]["verified"] is True
    assert doc["identity"]["run_id"] == "r1"


def test_partial_identity_is_not_verified():
    one = _row(); one["run_id"] = "r1"; one["config_sha256"] = "a" * 64
    two = _row()  # no identity
    doc = pa.summarize([one, two])
    assert doc["identity"]["verified"] is False
    assert doc["identity"]["rows_missing_identity"] == 1


def test_placeholder_config_sha_is_rejected():
    one = _row(); one["run_id"] = "r1"; one["config_sha256"] = "fake"
    try:
        pa.summarize([one])
    except ValueError as exc:
        assert "64-char hex" in str(exc)
    else:
        raise AssertionError("placeholder config_sha256 was accepted")
