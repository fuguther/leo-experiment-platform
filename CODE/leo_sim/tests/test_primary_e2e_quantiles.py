"""E2E delay quantile and tail metrics for the V2 primary-metric vocabulary.

WHY THESE TESTS EXIST
=====================
The baseline suite needs tail latency (p50/p95/p99) and a tail average
(CVaR5) as primary endpoints, not only ``e2e_delay_mean_s``.  Adding ids to
the canonical vocabulary is not enough: the analyzer must publish a precise,
reproducible arithmetic definition, because a mean and a percentile computed
over different packet populations would make two baseline arms incomparable
(the G0 "metric comparability" concern).

Every test calls ``v2_analysis._metric_from_result`` DIRECTLY with hand-built
receipt/ledger mappings - no kernel run, no simulation, no fixture dynamics -
so the assertions are about the published arithmetic only:

* percentiles are linear-interpolation order statistics over the SAME
  delivered-packet ``e2e_s`` sample the mean uses (rank ``q*(n-1)``, no
  nearest-rank rounding).  Expected values are computed by hand in each test;
  e.g. for the sample 1..100, p95 = 95.05 (interpolated), NOT 96 (nearest
  rank);
* ``e2e_delay_cvar5_s`` is the mean of the worst 5 percent of samples BY
  COUNT: ``k = max(1, ceil(0.05*n))`` largest values, averaged;
* an empty sample raises (same contract as the mean branch); n = 1..19 still
  computes, with CVaR5 degrading to the worst one or two packets.

``test_pre_experiment_trust.py`` separately proves the vocabulary stays
bidirectionally consistent with the dispatcher; this file proves the new
members' values are correct.
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import v2_analysis
from CODE.experiment_platform.primary_metrics import SUPPORTED_PRIMARY_METRICS

E2E_QUANTILE_METRICS = (
    "e2e_delay_p50_s",
    "e2e_delay_p95_s",
    "e2e_delay_p99_s",
    "e2e_delay_cvar5_s",
)


def _receipt(delivered: int = 1) -> dict:
    return {"totals": {"delivered_bits": 10.0, "terminal_loss_bits": 0.0,
                       "in_system_bits_at_stop": 0.0},
            "fate_counts": {"DELIVERED": delivered}}


def _ledgers(e2e_values) -> dict:
    """Minimal ledgers carrying exactly the per-packet e2e_s sample."""
    return {"congestion_metrics": {
        "packets": {str(index): {"e2e_s": value}
                    for index, value in enumerate(e2e_values, start=1)},
        "links": {},
    }}


def _metric(e2e_values, primary: str) -> float:
    return v2_analysis._metric_from_result(
        _receipt(len(e2e_values)), _ledgers(e2e_values), primary)


def test_the_four_ids_are_in_the_canonical_vocabulary():
    """The compiler reads this same set, so membership is the compile gate."""
    for primary in E2E_QUANTILE_METRICS:
        assert primary in SUPPORTED_PRIMARY_METRICS


def test_percentiles_are_linear_interpolation_of_the_sorted_sample():
    # Sample 1..10 (n=10).  Hand-computed ranks: q*(n-1) is 4.5 / 8.55 / 8.91,
    # so p50=5.5, p95=9.55, p99=9.91; nearest-rank would give 6, 10 and 10.
    sample = [float(value) for value in range(1, 11)]
    assert _metric(sample, "e2e_delay_p50_s") == pytest.approx(5.5)
    assert _metric(sample, "e2e_delay_p95_s") == pytest.approx(9.55)
    assert _metric(sample, "e2e_delay_p99_s") == pytest.approx(9.91)


def test_percentiles_are_scale_and_sample_size_exact():
    # Sample 1..100 (n=100): ranks 49.5 / 94.05 / 98.01.
    sample = [float(value) for value in range(1, 101)]
    assert _metric(sample, "e2e_delay_p50_s") == pytest.approx(50.5)
    assert _metric(sample, "e2e_delay_p95_s") == pytest.approx(95.05)
    assert _metric(sample, "e2e_delay_p99_s") == pytest.approx(99.01)


def test_cvar5_averages_the_worst_five_percent_by_count():
    # n=100 -> k = ceil(5) = 5 -> mean(96, 97, 98, 99, 100) = 98, not the max.
    sample = [float(value) for value in range(1, 101)]
    assert _metric(sample, "e2e_delay_cvar5_s") == pytest.approx(98.0)
    # n=10 -> k = ceil(0.5) = 1 -> the single worst packet.
    assert _metric(sample[:10], "e2e_delay_cvar5_s") == pytest.approx(10.0)
    # n=40 -> k = ceil(2) = 2 -> mean(39, 40); k is computed in integer
    # arithmetic so the tail count cannot drift with float rounding.
    assert _metric(sample[:40], "e2e_delay_cvar5_s") == pytest.approx(39.5)


def test_small_samples_still_compute_with_a_coarse_tail():
    # n=3 is below the 20-sample planning guard: CVaR5 must still compute
    # (worst 1 packet) rather than being dropped or raising.
    sample = [1.0, 2.0, 3.0]
    assert _metric(sample, "e2e_delay_p50_s") == pytest.approx(2.0)
    assert _metric(sample, "e2e_delay_p95_s") == pytest.approx(2.9)
    assert _metric(sample, "e2e_delay_p99_s") == pytest.approx(2.98)
    assert _metric(sample, "e2e_delay_cvar5_s") == pytest.approx(3.0)


def test_single_sample_boundary():
    # n=1: every order statistic is the one sample; k=1 tail is that sample.
    for primary in E2E_QUANTILE_METRICS:
        assert _metric([2.5], primary) == pytest.approx(2.5)


def test_an_empty_sample_raises_for_every_quantile():
    for primary in E2E_QUANTILE_METRICS:
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="has no delivered packets"):
            _metric([], primary)


def test_the_sample_is_the_same_delivered_packet_population_as_the_mean():
    # Packets without e2e_s are not in the sample; the remaining values are
    # used unchanged (same population the mean branch selects).
    ledgers = {"congestion_metrics": {
        "packets": {"1": {"e2e_s": 1.0},
                    "2": {"total_queue_wait_s": 0.5},   # no e2e_s
                    "3": {"e2e_s": 3.0}},
        "links": {},
    }}
    receipt = _receipt(2)
    assert v2_analysis._metric_from_result(
        receipt, ledgers, "e2e_delay_mean_s") == pytest.approx(2.0)
    assert v2_analysis._metric_from_result(
        receipt, ledgers, "e2e_delay_p50_s") == pytest.approx(2.0)
    assert v2_analysis._metric_from_result(
        receipt, ledgers, "e2e_delay_cvar5_s") == pytest.approx(3.0)


def test_non_finite_samples_are_rejected_not_silently_ranked():
    ledgers = {"congestion_metrics": {
        "packets": {"1": {"e2e_s": 1.0}, "2": {"e2e_s": float("nan")}},
        "links": {},
    }}
    for primary in E2E_QUANTILE_METRICS:
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="must be finite numeric"):
            v2_analysis._metric_from_result(_receipt(2), ledgers, primary)
