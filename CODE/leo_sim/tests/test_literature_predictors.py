"""Tests for the literature-standard estimators: EWMA smoothing and Kalman."""
import pytest

from CODE.leo_sim.time_alignment import (
    KIND_ISL, ResourceKey, StateSample, predict_resource)

RES = ResourceKey(1, "N", KIND_ISL)


def series(qs, dt=0.05):
    return [StateSample(RES, i * dt, i * dt, q, 1e9) for i, q in enumerate(qs)]


def test_ewma_predicts_weighted_mean_not_last():
    p = predict_resource(series([0.0, 0.0, 1e6]), 0.1, 0.1, method="ewma")
    assert p.predicted_bits is not None
    assert 0.0 < p.predicted_bits < 1e6


def test_kalman_is_finite_and_between_extremes():
    p = predict_resource(series([0.0, 1e6, 0.0, 2e6]), 0.15, 0.15,
                         method="kalman")
    assert p.predicted_bits is not None
    assert 0.0 <= p.predicted_bits <= 2e6


def test_both_require_history():
    for m in ("ewma", "kalman"):
        p = predict_resource([], 0.1, 0.1, method=m)
        assert p.predicted_bits is None
        assert p.missing_reason == "no_received_history"
