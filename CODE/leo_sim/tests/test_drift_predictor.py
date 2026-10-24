"""Tests for the arrival-rate `drift` predictor (air-gapped from any kernel)."""
import pytest

from CODE.leo_sim.time_alignment import (
    KIND_ISL, ResourceKey, StateSample, TimeAlignmentError, TruthSample,
    predict_resource)

RES = ResourceKey(1, "N", KIND_ISL)


def sample(measured_at, queue_bits, rate=1e9, arrival=None, received_at=None):
    return StateSample(RES, measured_at,
                       measured_at if received_at is None else received_at,
                       queue_bits, rate, arrival)


def test_drift_grows_when_arrival_exceeds_service():
    h = [sample(0.0, 0.0, rate=1e9, arrival=2e9)]
    p = predict_resource(h, 0.0, 0.1, method="drift")
    assert p.method == "drift" and p.missing_reason is None
    assert p.predicted_bits == pytest.approx(1e8, rel=1e-9)


def test_drift_shrinks_when_service_exceeds_arrival():
    h = [sample(0.0, 1e8, rate=1e9, arrival=0.0)]
    p = predict_resource(h, 0.0, 0.1, method="drift")
    assert p.predicted_bits == pytest.approx(0.0, abs=1e-6)


def test_drift_clips_to_zero_and_buffer():
    up = predict_resource([sample(0.0, 0.0, 1e9, 5e9)], 0.0, 1.0, method="drift",
                          max_queue_bits=5e7)
    assert up.predicted_bits == pytest.approx(5e7)
    down = predict_resource([sample(0.0, 1e7, 1e9, 0.0)], 0.0, 1.0, method="drift")
    assert down.predicted_bits == 0.0


def test_drift_requires_arrival_rate():
    p = predict_resource([sample(0.0, 1e6, 1e9, None)], 0.0, 0.1, method="drift")
    assert p.predicted_bits is None
    assert p.missing_reason == "no_arrival_rate"


def test_drift_ignores_samples_that_arrive_after_the_snapshot():
    # received after the snapshot is not information: it must be filtered out,
    # which leaves no history at all (an explicit missing_reason, never a value).
    late = sample(0.0, 1e6, 1e9, 1e9, received_at=1.0)
    p = predict_resource([late], 0.5, 0.6, method="drift")
    assert p.predicted_bits is None
    assert p.missing_reason == "no_received_history"


def test_drift_rejects_offline_truth_samples():
    with pytest.raises(TimeAlignmentError):
        predict_resource([TruthSample(RES, 0.1, 1e6)], 0.0, 0.1, method="drift")


def test_negative_arrival_rate_rejected():
    with pytest.raises(TimeAlignmentError):
        sample(0.0, 0.0, 1e9, -1.0)
