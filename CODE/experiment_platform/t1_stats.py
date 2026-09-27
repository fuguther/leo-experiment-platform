"""Pure, dependency-free statistics for the T1 mechanism comparison.

WHY THIS MODULE EXISTS
======================
Plan section P9 fixes one statistical unit for the T1 comparison: a
scenario x trace x seed cell is an independent block, and the arms inside a
cell are paired.  A packet is NOT an independent replicate, and re-running the
same trace exactly does not raise n.  The plan also fixes the failure rule:
blocks that fail because of seeds, deadlines or missing information are
reported, never silently dropped, so the analysis code must fail loudly rather
than quietly shrink the sample.

This module implements only the deterministic bookkeeping around that rule:
normalized loss, paired differences, a fixed-seed non-parametric bootstrap
interval, the sample-size planning formula, a precision cross-check on the
development distribution, the deadline defaulting rule, and the single primary
comparison with its pre-declared effect thresholds.  Everything here is a pure
function: no I/O, no global state, no kernel imports, standard library only
(math, random, statistics).

PRE-DECLARED DESIGN THRESHOLDS, NOT MEASURED VALUES
===================================================
The minimum substantive difference 0.01 and the sensitivity band 0.005 / 0.02
(all in normalized-loss units) are PRE-DECLARED DESIGN THRESHOLDS taken from the
plan (section P9 / section 2.1).  They are NOT measured effects, NOT outputs of
this data, and NOT industry or literature standards.  They exist so that the
primary comparison, its sensitivity band and the target 95% interval half-width
are frozen before any confirmation run is read.  The precision analysis below is
a planning approximation for precision only; it does not establish, and must not
be reported as, statistical power.
"""
from __future__ import annotations

import math
import random
import statistics

#: Pre-declared minimum substantive difference in normalized loss units.
#: A design threshold from plan section P9 - not a measured effect or standard.
MINIMUM_SUBSTANTIVE_DIFFERENCE = 0.01

#: Pre-declared sensitivity band around the minimum substantive difference.
#: Design thresholds from plan section P9 - not measured effects or standards.
SENSITIVITY_THRESHOLDS = (0.005, 0.02)

#: Target 95% bootstrap interval half-width for the default planning algorithm.
DEFAULT_TARGET_HALF_WIDTH = 0.005

#: Minimum sample size admitted by the default planning algorithm.
DEFAULT_MIN_N = 20

#: z multiplier for the default ~95% planning interval.
DEFAULT_Z = 1.96

#: Frozen bootstrap seed from plan section P9 (fixed-seed reproducibility).
DEFAULT_BOOTSTRAP_SEED = 20260927

#: Number of bootstrap replicates in the default interval.
DEFAULT_BOOTSTRAP_REPLICATES = 10000

#: Inner bootstrap replicates used per outer resample in precision_report.
#: The nested check is O(n_boot * inner * planned_n); a bounded inner count
#: keeps a default-sized planning call tractable while reusing exactly the same
#: percentile-interval algorithm as bootstrap_ci.
PRECISION_INNER_REPLICATES = 200


def _finite_float(value: object, label: str) -> float:
    """Return value as a finite float or fail loudly."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a real number, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite, got {value!r}")
    return result


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile of an already sorted sequence.

    q is in [0, 1].  Linear interpolation is required by plan section P9 for
    the E2E p95; nearest-rank rounding is deliberately not used.
    """
    if not sorted_values:
        raise ValueError("percentile needs at least one value")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"quantile must be in [0, 1], got {q!r}")
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    position = q * (n - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[int(lower)])
    fraction = position - lower
    low_value = float(sorted_values[int(lower)])
    high_value = float(sorted_values[int(upper)])
    return low_value + fraction * (high_value - low_value)


def _bootstrap_mean_interval(
    values: list[float],
    n_boot: int,
    rng: random.Random,
    confidence: float,
) -> tuple[float, float]:
    """Percentile bootstrap interval for the mean, driven by rng.

    Resamples len(values) observations with replacement n_boot times, records
    each resample mean, and returns the percentile interval.  The rng is always
    caller-supplied so this module never touches global random state.
    """
    n = len(values)
    means: list[float] = []
    for _ in range(n_boot):
        total = 0.0
        for _ in range(n):
            total += values[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    alpha = 1.0 - confidence
    low = _percentile(means, alpha / 2.0)
    high = _percentile(means, 1.0 - alpha / 2.0)
    return low, high


def normalized_loss(delay_s: float | None, dead_line_s: float) -> float:
    """Return L = min(delay, D) / D, the plan section P9 normalized loss.

    delay_s is None means the packet was lost: loss is 1.0.  A finite delay at
    or beyond the deadline also saturates at 1.0 (an observation window that
    completed without delivery is a timeout, not an unbounded value).
    """
    deadline = _finite_float(dead_line_s, "dead_line_s")
    if deadline <= 0.0:
        raise ValueError(f"dead_line_s must be > 0, got {dead_line_s!r}")
    if delay_s is None:
        return 1.0
    delay = _finite_float(delay_s, "delay_s")
    if delay < 0.0:
        raise ValueError(f"delay_s must be >= 0, got {delay_s!r}")
    if delay >= deadline:
        return 1.0
    return delay / deadline


def paired_differences(blocks: list[dict]) -> list[float]:
    """Return arm_b.loss - arm_a.loss for every block, in block order.

    Each block is a dict with an arm_a and an arm_b mapping carrying a numeric
    loss.  A block missing either arm (or either loss) fails loudly instead of
    being dropped: plan section P9 forbids silently deleting blocks that failed
    for seed/deadline/information reasons, because the missing rate is itself
    part of the report.
    """
    differences: list[float] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ValueError(
                f"block {index} must be a dict, got {type(block).__name__}")
        missing = [arm for arm in ("arm_a", "arm_b") if arm not in block]
        if missing:
            raise ValueError(
                f"block {index} is missing required arm(s) {missing}; "
                "failed blocks must be reported, not silently dropped")
        for arm in ("arm_a", "arm_b"):
            value = block[arm]
            if not isinstance(value, dict):
                raise ValueError(
                    f"block {index} field {arm!r} must be a dict, "
                    f"got {type(value).__name__}")
            if "loss" not in value:
                raise ValueError(
                    f"block {index} arm {arm!r} is missing 'loss'; "
                    "failed blocks must be reported, not silently dropped")
        loss_a = _finite_float(block["arm_a"]["loss"], f"block {index} arm_a.loss")
        loss_b = _finite_float(block["arm_b"]["loss"], f"block {index} arm_b.loss")
        differences.append(loss_b - loss_a)
    return differences


def bootstrap_ci(
    diffs: list[float],
    n_boot: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence: float = 0.95,
) -> dict:
    """Fixed-seed non-parametric bootstrap interval for the mean difference.

    Blocks are the independent unit (plan section P9), so the resampling draws
    whole paired differences.  Reproducibility is guaranteed by
    random.Random(seed) - global random state is never read or written.
    half_width is always (high - low) / 2 by construction.
    """
    values = [_finite_float(d, f"diffs[{i}]") for i, d in enumerate(diffs)]
    if not values:
        raise ValueError("bootstrap_ci requires at least one paired difference; "
                         "an empty sample cannot produce an interval")
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 1:
        raise ValueError(f"n_boot must be a positive integer, got {n_boot!r}")
    conf = _finite_float(confidence, "confidence")
    if not 0.0 < conf < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence!r}")
    seed_value = _finite_float(seed, "seed")
    if seed_value != int(seed_value):
        raise ValueError(f"seed must be an integer, got {seed!r}")

    rng = random.Random(int(seed_value))
    low, high = _bootstrap_mean_interval(values, n_boot, rng, conf)
    return {
        "mean": statistics.fmean(values),
        "low": low,
        "high": high,
        "half_width": (high - low) / 2.0,
        "n": len(values),
        "n_boot": n_boot,
        "confidence": conf,
        "seed": int(seed_value),
    }


def plan_sample_size(
    s: float,
    half_width: float = DEFAULT_TARGET_HALF_WIDTH,
    min_n: int = DEFAULT_MIN_N,
    z: float = DEFAULT_Z,
) -> int:
    """Default planning formula: n = max(min_n, ceil((z * s / half_width) ** 2)).

    s is the development-block paired-difference standard deviation and
    half_width the target 95% interval half-width (plan section P9).  A zero
    standard deviation means no replicated variance is observed, so the floor
    min_n is returned rather than 0 or 1.
    """
    std = _finite_float(s, "s")
    if std < 0.0:
        raise ValueError(f"s must be >= 0, got {s!r}")
    width = _finite_float(half_width, "half_width")
    if width <= 0.0:
        raise ValueError(f"half_width must be > 0, got {half_width!r}")
    if isinstance(min_n, bool) or not isinstance(min_n, int) or min_n < 1:
        raise ValueError(f"min_n must be a positive integer, got {min_n!r}")
    z_value = _finite_float(z, "z")
    if z_value <= 0.0:
        raise ValueError(f"z must be > 0, got {z!r}")
    if std == 0.0:
        return min_n
    required = math.ceil((z_value * std / width) ** 2)
    return max(min_n, required)


def precision_report(
    diffs: list[float],
    half_width: float = DEFAULT_TARGET_HALF_WIDTH,
    n_boot: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict:
    """Resample the development distribution to check achieved precision.

    This follows plan section P9: from the development paired differences, size
    the run with plan_sample_size, then resample the development distribution
    n_boot times at that sample size and record how often the bootstrap mean
    interval half-width actually meets the target.  Each outer resample gets an
    interval from the same percentile algorithm as bootstrap_ci (with a bounded
    number of inner replicates for tractability).

    This is a precision planning approximation.  It does NOT estimate power and
    must not be reported as a power analysis.
    """
    values = [_finite_float(d, f"diffs[{i}]") for i, d in enumerate(diffs)]
    if not values:
        raise ValueError("precision_report requires a non-empty development "
                         "distribution")
    target = _finite_float(half_width, "half_width")
    if target <= 0.0:
        raise ValueError(f"half_width must be > 0, got {half_width!r}")
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 1:
        raise ValueError(f"n_boot must be a positive integer, got {n_boot!r}")
    seed_value = _finite_float(seed, "seed")
    if seed_value != int(seed_value):
        raise ValueError(f"seed must be an integer, got {seed!r}")

    observed_std = statistics.stdev(values) if len(values) >= 2 else 0.0
    planned_n = plan_sample_size(observed_std, target)

    rng = random.Random(int(seed_value))
    achieved = 0
    n = len(values)
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(planned_n)]
        inner_rng = random.Random(rng.randrange(2 ** 63))
        low, high = _bootstrap_mean_interval(
            sample, PRECISION_INNER_REPLICATES, inner_rng, 0.95)
        if (high - low) / 2.0 <= target:
            achieved += 1

    return {
        "planned_n": planned_n,
        "observed_std": float(observed_std),
        "target_half_width": target,
        "achieved_frequency": achieved / n_boot,
        "n_boot": n_boot,
    }


def default_deadline(
    delivery_delays: list[float],
    multiplier: float = 2.0,
    declared_horizon_s: float | None = None,
) -> dict:
    """Derive the mechanism-comparison deadline D per plan section P9.

    With a business deadline, D is that deadline.  Without one, D defaults to
    multiplier times the E2E p95 of every legal candidate delivery sample in the
    development baseline (linear interpolation, frozen once chosen).  When the
    development set has no delivery at all, D falls back to the scenario's
    pre-declared measurement horizon and the result is flagged weakly
    interpretable.  Having neither is an error, not an invented number.
    """
    factor = _finite_float(multiplier, "multiplier")
    if factor <= 0.0:
        raise ValueError(f"multiplier must be > 0, got {multiplier!r}")
    delays = [_finite_float(d, f"delivery_delays[{i}]")
              for i, d in enumerate(delivery_delays)]
    for index, delay in enumerate(delays):
        if delay < 0.0:
            raise ValueError(
                f"delivery_delays[{index}] must be >= 0, got {delay!r}")

    if delays:
        p95 = _percentile(sorted(delays), 0.95)
        return {
            "deadline_s": factor * p95,
            "source": "p95_times_multiplier",
            "weak": False,
        }

    if declared_horizon_s is None:
        raise ValueError(
            "no legal delivery samples and no declared_horizon_s: the default "
            "deadline is undefined; supply a positive declared horizon")
    horizon = _finite_float(declared_horizon_s, "declared_horizon_s")
    if horizon <= 0.0:
        raise ValueError(
            f"declared_horizon_s must be > 0, got {declared_horizon_s!r}")
    return {
        "deadline_s": horizon,
        "source": "declared_horizon",
        "weak": True,
    }


def primary_comparison_delta(
    common_strong_regret: list[float],
    candidate_regret: list[float],
    n_boot: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict:
    """Single primary comparison: common_strong regret - candidate regret.

    Regrets are paired by independent block, differenced, then bootstrapped with
    bootstrap_ci.  The result additionally carries the pre-declared minimum
    substantive difference (0.01), its sensitivity band, and whether the
    observed mean reaches the minimum.  meets_minimum_difference is a
    descriptive flag against a frozen design threshold, not a significance test.
    """
    common = [_finite_float(v, f"common_strong_regret[{i}]")
              for i, v in enumerate(common_strong_regret)]
    candidate = [_finite_float(v, f"candidate_regret[{i}]")
                 for i, v in enumerate(candidate_regret)]
    if len(common) != len(candidate):
        raise ValueError(
            "common_strong_regret and candidate_regret must be paired block by "
            f"block: got {len(common)} and {len(candidate)}")
    if not common:
        raise ValueError(
            "primary_comparison_delta requires at least one paired block; "
            "an empty comparison cannot be summarized")

    diffs = [c - k for c, k in zip(common, candidate)]
    result = bootstrap_ci(diffs, n_boot=n_boot, seed=seed)
    result["minimum_substantive_difference"] = MINIMUM_SUBSTANTIVE_DIFFERENCE
    result["sensitivity"] = list(SENSITIVITY_THRESHOLDS)
    result["meets_minimum_difference"] = bool(
        result["mean"] >= MINIMUM_SUBSTANTIVE_DIFFERENCE)
    return result
