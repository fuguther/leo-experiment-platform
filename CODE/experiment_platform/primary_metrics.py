"""Canonical vocabulary of V2 primary metrics the analyzer can actually compute.

WHY THIS MODULE EXISTS
======================
The compiler (CODE/leo_sim/matrix.py) and the analyzer
(CODE/experiment_platform/v2_analysis.py) used to agree on nothing.  The
compiler accepted any non-empty string as analysis.primary_metric, while the
analyzer dispatched over its own private if-chain.  The measurable
consequence (platform audit, finding B4): 20 of the 23 ids in
metric-catalog.json - including p95_e2e_latency and packet_loss_rate, which
the catalog itself marks eligible_as_primary - compiled cleanly, ran every
cell, and only then raised "unsupported V2 primary metric".

This module is the single source of truth both sides read.  It deliberately
has NO imports: a compiler that pulls the whole analysis package in would
couple compilation to analysis dependencies, and this file must stay
importable from CODE/leo_sim/matrix.py without a cycle.

KEEPING IT HONEST
=================
Membership here is not a promise - it is checked.  CODE/leo_sim/tests/
test_pre_experiment_trust.py drives v2_analysis._metric_from_result for every
id in SUPPORTED_PRIMARY_METRICS and requires a numeric result, then requires an
id outside the set to be rejected.  Adding a metric to the dispatcher without
adding it here (or the reverse) fails that test.  metric-catalog.json is NOT a
second copy of this set: it is the legacy ``summary_metrics.csv`` vocabulary
read only by compile_experiment.py (the non-V2 path), and the V2 analyzer
deliberately refuses most of it (audit B4).  Do not mirror V2 ids into the
catalog - the legacy bundle does not produce them, so marking one
``eligible_as_primary`` there would re-open B4 on the legacy path.

E2E DELAY QUANTILES AND TAIL
============================
``e2e_delay_p50_s`` / ``e2e_delay_p95_s`` / ``e2e_delay_p99_s`` /
``e2e_delay_cvar5_s`` summarize the SAME per-delivered-packet ``e2e_s`` sample
that ``e2e_delay_mean_s`` averages, in seconds.  Exact definitions (arithmetic:
CODE/experiment_platform/v2_analysis.py::_e2e_quantile, itself a caller of
t1_stats._percentile; this file stays import-free):

* percentiles are LINEAR-INTERPOLATION order statistics: for the sorted sample
  ``v[0..n-1]`` the rank is ``q*(n-1)`` and the value is interpolated between
  the two neighbouring order statistics - no nearest-rank rounding;
* ``e2e_delay_cvar5_s`` is the mean of the worst 5 percent of samples: with
  ``k = max(1, ceil(0.05*n))`` it averages the ``k`` largest ``e2e_s`` values
  (so a percentile is an order-statistic interpolation, while CVaR5 averages
  actual tail samples);
* an empty sample raises; n = 1..19 still computes, but there CVaR5 averages
  one or two packets, so it is a coarse tail summary and must not be read as a
  stable tail estimate.
"""

#: Primary metrics v2_analysis._metric_from_result can compute from
#: receipt.json + ledgers.json alone.
SUPPORTED_PRIMARY_METRICS = frozenset({
    "delivery_rate",
    "delivered_bits",
    "terminal_loss_bits",
    "in_system_bits_at_stop",
    "access_admission_rate",
    "network_delivery_rate_by_horizon",
    "e2e_delay_mean_s",
    "e2e_delay_p50_s",
    "e2e_delay_p95_s",
    "e2e_delay_p99_s",
    "e2e_delay_cvar5_s",
    "queue_wait_mean_s",
    "tx_time_mean_s",
    "propagation_time_mean_s",
    "link_utilization_mean",
    "service_window_utilization_mean",
    "isl_link_utilization_mean",
    "isl_link_utilization_max",
})

#: The subset whose value IS a link-utilization ratio, i.e. served bits over an
#: available-capacity denominator.  These are the metrics that cannot be
#: interpreted at all without execution.available_capacity_interval_s: with the
#: interval null, metrics.summarize substitutes the service-window capacity as
#: the denominator and publishes ~1.0 for any successfully served link, with
#: available_samples == 0 (metrics.py:334-340).  matrix.py refuses to compile a
#: request that asks for one of these without sampling.
UTILIZATION_PRIMARY_METRICS = frozenset({
    "link_utilization_mean",
    "service_window_utilization_mean",
    "isl_link_utilization_mean",
    "isl_link_utilization_max",
})
