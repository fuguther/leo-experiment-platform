"""Focused tests for the pure T1 statistics module (plan section P9 / K14).

These tests pin the plan-mandated behaviour of CODE/experiment_platform/
t1_stats.py: the normalized-loss saturation rule, fail-loud block handling
(no silent sample dropping), fixed-seed bootstrap reproducibility, the default
sample-size formula, the precision cross-check, the deadline defaulting rule,
and the single primary comparison with its pre-declared thresholds.
"""
from __future__ import annotations

import math
import random

import pytest

from CODE.experiment_platform import t1_stats
from CODE.experiment_platform.t1_stats import (
    bootstrap_ci,
    default_deadline,
    normalized_loss,
    paired_differences,
    plan_sample_size,
    precision_report,
    primary_comparison_delta,
)


# ---------------------------------------------------------------------------
# normalized_loss
# ---------------------------------------------------------------------------

def test_normalized_loss_below_deadline_is_ratio():
    assert normalized_loss(2.0, 10.0) == pytest.approx(0.2)
    assert normalized_loss(0.0, 10.0) == 0.0


def test_normalized_loss_at_and_beyond_deadline_saturates():
    assert normalized_loss(10.0, 10.0) == 1.0
    assert normalized_loss(25.0, 10.0) == 1.0


def test_normalized_loss_none_delay_is_loss():
    assert normalized_loss(None, 10.0) == 1.0


@pytest.mark.parametrize("deadline", [0.0, -1.0, -0.001])
def test_normalized_loss_nonpositive_deadline_rejected(deadline):
    with pytest.raises(ValueError, match="dead_line_s must be > 0"):
        normalized_loss(1.0, deadline)


def test_normalized_loss_negative_delay_rejected():
    with pytest.raises(ValueError, match="delay_s must be >= 0"):
        normalized_loss(-0.5, 10.0)


# ---------------------------------------------------------------------------
# paired_differences
# ---------------------------------------------------------------------------

def test_paired_differences_basic_and_order_preserving():
    blocks = [
        {"unit": "s1", "arm_a": {"loss": 0.5}, "arm_b": {"loss": 0.2}},
        {"unit": "s2", "arm_a": {"loss": 0.1}, "arm_b": {"loss": 0.4}},
        {"unit": "s3", "arm_a": {"loss": 0.3}, "arm_b": {"loss": 0.3}},
    ]
    diffs = paired_differences(blocks)
    assert diffs == pytest.approx([-0.3, 0.3, 0.0])
    # Reordering the input blocks must reorder the output identically.
    reversed_diffs = paired_differences(list(reversed(blocks)))
    assert reversed_diffs == pytest.approx(list(reversed(diffs)))


def test_paired_differences_missing_arm_fails_loud():
    with pytest.raises(ValueError, match="missing required arm"):
        paired_differences([
            {"unit": "s1", "arm_a": {"loss": 0.5}, "arm_b": {"loss": 0.2}},
            {"unit": "s2", "arm_a": {"loss": 0.1}},
        ])


def test_paired_differences_missing_loss_fails_loud():
    with pytest.raises(ValueError, match="missing 'loss'"):
        paired_differences([
            {"unit": "s1", "arm_a": {"loss": 0.5}, "arm_b": {}},
        ])


# ---------------------------------------------------------------------------
# bootstrap_ci
# ---------------------------------------------------------------------------

_DIFFS = [0.12, -0.05, 0.31, 0.02, -0.18, 0.24, 0.07, -0.11, 0.40, 0.15]


def test_bootstrap_ci_same_seed_is_reproducible():
    first = bootstrap_ci(_DIFFS, n_boot=500, seed=7)
    second = bootstrap_ci(_DIFFS, n_boot=500, seed=7)
    assert first == second


def test_bootstrap_ci_different_seeds_differ():
    first = bootstrap_ci(_DIFFS, n_boot=2000, seed=1)
    second = bootstrap_ci(_DIFFS, n_boot=2000, seed=2)
    assert (first["low"], first["high"]) != (second["low"], second["high"])


def test_bootstrap_ci_all_zero_diffs_collapse():
    result = bootstrap_ci([0.0, 0.0, 0.0, 0.0], n_boot=200, seed=3)
    assert result["low"] == 0.0
    assert result["high"] == 0.0
    assert result["half_width"] == 0.0


def test_bootstrap_ci_empty_input_rejected():
    with pytest.raises(ValueError, match="at least one paired difference"):
        bootstrap_ci([])


def test_bootstrap_ci_half_width_consistent_and_seed_recorded():
    result = bootstrap_ci(_DIFFS, n_boot=500, seed=11)
    assert result["half_width"] == pytest.approx((result["high"] - result["low"]) / 2.0)
    assert result["half_width"] >= 0.0
    assert result["low"] <= result["mean"] <= result["high"]
    assert result["n"] == len(_DIFFS)
    assert result["n_boot"] == 500
    assert result["seed"] == 11
    assert result["confidence"] == pytest.approx(0.95)


def test_bootstrap_ci_uses_local_rng_not_global_state():
    random.seed(1234)
    before = random.random()
    bootstrap_ci(_DIFFS, n_boot=50, seed=99)
    random.seed(1234)
    after = random.random()
    assert before == after


# ---------------------------------------------------------------------------
# plan_sample_size
# ---------------------------------------------------------------------------

def test_plan_sample_size_zero_std_returns_min_n():
    assert plan_sample_size(0.0) == 20
    assert plan_sample_size(0.0, half_width=0.001, min_n=37) == 37


def test_plan_sample_size_matches_hand_formula():
    expected = max(20, math.ceil((1.96 * 0.1 / 0.005) ** 2))
    assert expected == 1537
    assert plan_sample_size(0.1) == expected


def test_plan_sample_size_monotonic_in_std():
    n_small = plan_sample_size(0.05)
    n_mid = plan_sample_size(0.1)
    n_large = plan_sample_size(0.2)
    assert n_small <= n_mid <= n_large
    assert n_small < n_large


@pytest.mark.parametrize("kwargs", [
    {"s": -0.01},
    {"s": 0.1, "half_width": 0.0},
    {"s": 0.1, "half_width": -0.005},
    {"s": 0.1, "min_n": 0},
])
def test_plan_sample_size_invalid_arguments_rejected(kwargs):
    with pytest.raises(ValueError):
        plan_sample_size(**kwargs)


# ---------------------------------------------------------------------------
# precision_report
# ---------------------------------------------------------------------------

def test_precision_report_frequency_and_planned_n():
    report = precision_report(_DIFFS, half_width=0.05, n_boot=200, seed=5)
    assert 0.0 <= report["achieved_frequency"] <= 1.0
    expected_n = plan_sample_size(report["observed_std"], 0.05)
    assert report["planned_n"] == expected_n
    assert report["target_half_width"] == pytest.approx(0.05)
    assert report["n_boot"] == 200


def test_precision_report_empty_input_rejected():
    with pytest.raises(ValueError, match="non-empty development distribution"):
        precision_report([])


# ---------------------------------------------------------------------------
# default_deadline
# ---------------------------------------------------------------------------

def test_default_deadline_uses_linear_p95_times_multiplier():
    result = default_deadline([1.0, 2.0, 3.0, 4.0, 5.0])
    # Linear-interpolated p95 of 1..5 is 4.8 (nearest-rank would give 5.0).
    assert result["deadline_s"] == pytest.approx(9.6)
    assert result["source"] == "p95_times_multiplier"
    assert result["weak"] is False
    assert result["deadline_s"] != pytest.approx(10.0)


def test_default_deadline_falls_back_to_declared_horizon_weakly():
    result = default_deadline([], declared_horizon_s=42.0)
    assert result["deadline_s"] == pytest.approx(42.0)
    assert result["source"] == "declared_horizon"
    assert result["weak"] is True


def test_default_deadline_without_deliveries_or_horizon_rejected():
    with pytest.raises(ValueError, match="default deadline is undefined"):
        default_deadline([])


# ---------------------------------------------------------------------------
# primary_comparison_delta
# ---------------------------------------------------------------------------

def test_primary_comparison_delta_length_mismatch_rejected():
    with pytest.raises(ValueError, match="paired block by block"):
        primary_comparison_delta([0.1, 0.2, 0.3], [0.05, 0.1])


def test_primary_comparison_delta_carries_declared_thresholds():
    result = primary_comparison_delta(
        [0.30, 0.25, 0.40, 0.35], [0.10, 0.12, 0.20, 0.15], n_boot=300, seed=8)
    assert result["minimum_substantive_difference"] == 0.01
    assert result["sensitivity"] == [0.005, 0.02]
    assert result["meets_minimum_difference"] is True
    # Paired diffs are 0.20, 0.13, 0.20, 0.20 -> mean 0.1825.
    assert result["mean"] == pytest.approx(0.1825)
    assert "low" in result and "high" in result and "half_width" in result


def test_primary_comparison_delta_flags_below_minimum():
    result = primary_comparison_delta([0.100], [0.099], n_boot=50, seed=1)
    assert result["mean"] == pytest.approx(0.001)
    assert result["meets_minimum_difference"] is False


def test_module_declares_thresholds_are_design_choices():
    doc = t1_stats.__doc__ or ""
    assert "PRE-DECLARED DESIGN THRESHOLDS" in doc
    assert "NOT measured" in doc or "NOT measured effects" in doc
