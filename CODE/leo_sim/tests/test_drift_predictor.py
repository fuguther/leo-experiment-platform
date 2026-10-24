"""Tests for the arrival-rate `drift` predictor (air-gapped from any kernel)."""
import math

import pytest

from CODE.leo_sim.time_alignment import (
    KIND_ISL, ResourceKey, StateSample, TimeAlignmentError, TruthSample,
    arrival_ewma, arrival_rate_at, predict_resource)

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


# ---------------------------------------------------------------------------
# arrival_rate EWMA (the state the `drift` predictor consumes)
# ---------------------------------------------------------------------------

def test_arrival_ewma_is_a_rate_over_the_window():
    # one impulse of W bits in a W-second window => 1 bit/s, then decays
    W = 0.1
    lam = arrival_ewma(None, None, bits=W, now=0.0, window_s=W)
    assert lam == pytest.approx(1.0)
    assert arrival_rate_at(lam, 0.0, 0.0, W) == pytest.approx(1.0)
    assert arrival_rate_at(lam, 0.0, W, W) == pytest.approx(math.exp(-1.0))


def test_arrival_ewma_decays_between_arrivals():
    W = 0.2
    first = arrival_ewma(None, None, bits=1e6, now=0.0, window_s=W)
    # no arrivals for half a window: decays by exp(-0.5)
    later = arrival_ewma(first, 0.0, bits=0.0, now=0.5 * W, window_s=W)
    assert later == pytest.approx(first * math.exp(-0.5))


def test_arrival_ewma_rejects_bad_inputs():
    with pytest.raises(TimeAlignmentError):
        arrival_ewma(None, None, 1.0, 0.0, window_s=0.0)
    with pytest.raises(TimeAlignmentError):
        arrival_ewma(None, None, -1.0, 0.0, window_s=0.1)


def test_drift_alpha_shrinks_toward_hold_last():
    h = [sample(0.0, 0.0, rate=1e9, arrival=3e9)]
    full = predict_resource(h, 0.0, 0.1, method="drift")
    half = predict_resource(h, 0.0, 0.1, method="drift", drift_alpha=0.5)
    assert full.predicted_bits == pytest.approx(2e8)
    assert half.predicted_bits == pytest.approx(1e8)
    with pytest.raises(TimeAlignmentError):
        predict_resource(h, 0.0, 0.1, method="drift", drift_alpha=0.0)
    with pytest.raises(TimeAlignmentError):
        predict_resource(h, 0.0, 0.1, method="drift", drift_alpha=1.5)


def test_rate_aware_uses_lookahead_rate_not_measurement_rate():
    # Q=1e8, lambda=1e9, mu_ahead=2e9 over 0.1 s -> 1e8 + (1e9-2e9)*0.1 = 0
    h = [sample(0.0, 1e8, rate=1e9, arrival=1e9)]
    p = predict_resource(h, 0.0, 0.1, method="rate_aware",
                         rate_ahead_bps=2e9)
    assert p.method == "rate_aware" and p.predicted_bits == pytest.approx(0.0)
    # with the measurement-time rate (1e9) the same sample would predict 1e8
    q = predict_resource(h, 0.0, 0.1, method="drift")
    assert q.predicted_bits == pytest.approx(1e8)


def test_rate_aware_refuses_without_lookahead_or_arrival():
    h = [sample(0.0, 1e8, rate=1e9, arrival=1e9)]
    p = predict_resource(h, 0.0, 0.1, method="rate_aware")
    assert p.missing_reason == "no_rate_ahead"
    h2 = [sample(0.0, 1e8, rate=1e9, arrival=None)]
    p2 = predict_resource(h2, 0.0, 0.1, method="rate_aware", rate_ahead_bps=2e9)
    assert p2.missing_reason == "no_arrival_rate"
    p3 = predict_resource(h, 0.0, 0.1, method="rate_aware", rate_ahead_bps=0.0)
    assert p3.missing_reason == "non_positive_rate_ahead"
