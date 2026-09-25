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
adding it here (or the reverse) fails that test.
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
