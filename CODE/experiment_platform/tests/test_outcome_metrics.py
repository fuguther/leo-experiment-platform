"""A4: the unified report must carry the FINAL outcome and the TOTAL compute cost.

Every behaviour here is a requirement of the task book item A4, and the
requirements that decide the SHAPE of the module (rather than a single number)
are the ones with the most tests:

* the two throughput denominators must never be mixed;
* a missing raw field is NOT_COMPUTABLE with the field NAMED;
* censoring (the horizon cut a packet off administratively) is never loss;
* background update compute is part of the total, and is reported apart from
  the per-packet decision compute;
* the hotspot rate is the MAXIMUM over satellites, never the all-satellite
  mean;
* every row must be traceable to its cell/run id.

The synthetic fixtures below are built from the kernel's own raw schema (the
event/timeline keys produced by CODE/leo_sim/kernel.py) so the module is
exercised on the shape it will really see; the last tests drive a real
2-packet kernel run through it.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import pytest

from CODE.experiment_platform import execution_compare as ec
from CODE.experiment_platform import outcome_metrics as om
from CODE.experiment_platform import scripted_scenarios
from CODE.leo_sim import config as config_mod
from CODE.leo_sim import kernel

ROOT = Path(__file__).resolve().parents[3]

#: The required A4 metric names, verbatim from the task book.  The test below
#: requires every one of them to be present in the published rows, so a
#: refactor that renames or drops one fails here instead of shipping.
REQUIRED_METRICS = (
    "counts.offered",
    "counts.admitted",
    "counts.delivered",
    "terminal.classification",
    "terminal.expired_and_undelivered",
    "censor.still_in_system_at_stop",
    "payload.goodput_bps",
    "payload.delivery_ratio",
    "e2e.mean_s",
    "e2e.p50_s",
    "e2e.p90_s",
    "e2e.p95_s",
    "e2e.p99_s",
    "queue.peak_bits",
    "queue.area_bits_s",
    "throughput.delivered_bits_in_window_over_fixed_window",
    "throughput.final_delivery_ratio_of_window_queue_population",
    "warmup.packets",
    "drain.packets",
    "rate_per_satellite.hotspot_max_per_s",
    "rate_per_satellite.mean_per_s",
    "compute.packet_decisions.jobs",
    "compute.packet_decisions.queue_wait_s",
    "compute.packet_decisions.service_s",
    "compute.background_updates.jobs",
    "compute.background_updates.queue_wait_s",
    "compute.background_updates.service_s",
    "compute.total.jobs",
    "compute.total.service_s",
    "query_service.requests",
    "query_service.service_s",
    "install_delay.mean_s",
    "version_age.mean_s",
    "table.hit_rate",
    "table.fallback_rate",
    "table.bins_used",
    "prediction.resource_match_rate",
    "prediction.eta_error_mean_s",
    "prediction.same_resource_bits_error_mean",
)

#: The window the fixture rows really derive to: first emission 0.0, median
#: inter-arrival 2.0 s, drain tail 2 x 2.0 = 4.0, last emission 9.5.
#: test_the_measurement_window_is_derived_from_the_trace_rows keeps this in sync
#: with build_measurement_window, so the two can never drift apart.
WINDOW = {"start_s": 0.0, "end_s": 13.5, "fixed_window_s": 13.5,
          "unit_interval_s": 2.0, "drain_tail_s": 4.0,
          "source": "trace_rows_median_interarrival_x_drain_multiplier",
          "status": "COMPUTED"}


def _emitted(pid, at, bits):
    return {"kind": "packet_emitted", "pid": pid, "at": at, "bits": bits}


def _ingress(pid, at, sat, bits):
    return {"kind": "satellite_ingress", "pid": pid, "at": at,
            "endpoint": f"cell-{pid}", "satellite": sat, "bits": bits}


def _queue_enter(pid, at, queue, link_id, qid, decision_id=None):
    return {"kind": "queue_enter", "pid": pid, "at": at, "queue": queue,
            "link_id": link_id, "queue_id": qid}


def _delivered(pid, at):
    return {"kind": "delivered", "pid": pid, "at": at}


def _emitted_row(pid, at, bits):
    return {"packet_id": pid, "emit_time_s": at, "src_grid_id": "S",
            "dst_grid_id": "D", "bits": bits, "deadline_at_s": None}


def _rows():
    """Six trace rows: one warm-up, four inside the window, one drain packet.

    The measurement window derived from these rows is [1.0, 13.5]:
    first emission 1.0, median inter-arrival 2.0, drain tail 4.0, last
    emission 9.5.  Rows 0 and 5 fall outside it and must stay separable.
    """
    return [_emitted_row(0, 0.0, 100), _emitted_row(1, 1.0, 200),
            _emitted_row(2, 3.0, 300), _emitted_row(3, 5.0, 400),
            _emitted_row(4, 5.0, 500), _emitted_row(5, 9.5, 600)]


def _packet_events():
    """Two window packets deliver; one is terminally lost; one is censored.

    Every packet that the fate ledger marks admitted also carries its ingress
    event, so the admitted count is a real count and not a fallback.  pid 3 is
    terminally lost (NO_ROUTE) and pid 4 is still in the system at stop; both
    are admitted, and admission never changes how their fate is classified.
    """
    return [
        _emitted(0, 0.0, 100), _ingress(0, 0.1, 0, 100),
        _delivered(0, 0.5),
        _queue_enter(0, 0.1, "uplink", "gsl:uplink:0:S", 0),
        _emitted(1, 1.0, 200), _ingress(1, 1.1, 0, 200),
        _queue_enter(1, 1.1, "uplink", "gsl:uplink:0:S", 1),
        _emitted(2, 3.0, 300), _ingress(2, 3.1, 1, 300),
        _queue_enter(2, 3.1, "uplink", "gsl:uplink:1:S", 2), _delivered(2, 5.0),
        _emitted(3, 5.0, 400), _ingress(3, 5.1, 1, 400),
        _emitted(4, 5.0, 500), _ingress(4, 5.2, 2, 500),
        _emitted(5, 9.5, 600), _ingress(5, 9.6, 2, 600),
        _delivered(5, 9.8),
    ]


def _timeline():
    """Milestones in kernel shape (pid None for background compute jobs)."""
    return [
        {"milestone": "queue_enter", "at": 1.1, "pid": 1, "decision_id": 0,
         "queue": "uplink", "link_id": "gsl:uplink:0:S",
         "backlog_before": {"queued_bits_before": 0,
                            "in_service_remaining_bits_before": None}},
        {"milestone": "service_start", "at": 1.1, "pid": 1, "decision_id": 0,
         "stage": "uplink", "link_id": "gsl:uplink:0:S"},
        {"milestone": "service_finish", "at": 1.3, "pid": 1, "decision_id": 0,
         "stage": "uplink", "link_id": "gsl:uplink:0:S", "outcome": "ok"},
        {"milestone": "redecision", "at": 1.2, "pid": 1, "decision_id": 1,
         "prev_decision_id": 0, "sat": 0, "obs_mode": "frozen"},
        {"milestone": "redecision", "at": 1.5, "pid": 2, "decision_id": 2,
         "prev_decision_id": 1, "sat": 1, "obs_mode": "frozen"},
        {"milestone": "redecision", "at": 5.3, "pid": 1, "decision_id": 4,
         "prev_decision_id": 0, "sat": 0, "obs_mode": "frozen"},
        {"milestone": "queue_enter", "at": 2.0, "pid": 3, "decision_id": 3,
         "queue": "isl", "link_id": "isl:1:2",
         "backlog_before": {"queued_bits_before": 900,
                            "in_service_remaining_bits_before": 450}},
        {"milestone": "service_start", "at": 2.0, "pid": 3, "decision_id": 3,
         "stage": "isl", "link_id": "isl:1:2"},
        {"milestone": "service_finish", "at": 2.9, "pid": 3, "decision_id": 3,
         "stage": "isl", "link_id": "isl:1:2", "outcome": "ok"},
        # background scope update: same shared pool, no pid
        {"milestone": "compute_request", "at": 2.0, "pid": None,
         "decision_id": None, "sat": 1, "compute_job_id": 1,
         "requested_at": 2.0, "service_s": 0.1,
         "scope": "async:(1, 'D', 'default')"},
        {"milestone": "compute_wait", "at": 2.0, "pid": None,
         "decision_id": None, "sat": 1, "compute_job_id": 1, "wait_s": 0.5,
         "service_s": 0.1, "servers": 1, "servers_busy": 1, "queueing": True,
         "requested_at": 2.0},
        {"milestone": "compute_start", "at": 2.5, "pid": None,
         "decision_id": None, "sat": 1, "compute_job_id": 1,
         "requested_at": 2.0, "started_at": 2.5, "wait_s": 0.5},
        {"milestone": "compute_finish", "at": 2.6, "pid": None,
         "decision_id": None, "sat": 1, "compute_job_id": 1,
         "requested_at": 2.0, "started_at": 2.5, "finished_at": 2.6,
         "wait_s": 0.5, "service_s": 0.1},
        # the kernel emits update_computed immediately before the install delay
        # it then waits out; the pair is what makes the delay measurable
        {"milestone": "schedule_update_computed", "at": 2.6, "pid": None,
         "decision_id": None, "scope": [1, "D", "default"], "version": 1,
         "install_at": 2.7, "install_offset_s": 0.0},
        {"milestone": "schedule_installed", "at": 2.7, "pid": None,
         "decision_id": None, "scope": [1, "D", "default"], "version": 1,
         "trigger": "periodic", "expires_at": 3.7, "bins": 4},
        # four real schedule lookups: 2 hits (non-fallback), 2 fallbacks, and
        # two distinct bins, so the hit/fallback rate and bins_used are both
        # decided by the timeline rather than inferred from the audit flags
        {"milestone": "schedule_query", "at": 3.0, "pid": 1,
         "decision_id": 0, "scope": [1, "D", "default"], "version": 1,
         "bin": 0, "action": "S", "fallback": False, "state": "installed",
         "reason": None},
        {"milestone": "schedule_query", "at": 3.1, "pid": 1,
         "decision_id": 1, "scope": [1, "D", "default"], "version": 1,
         "bin": 2, "action": "S", "fallback": False, "state": "installed",
         "reason": None},
        {"milestone": "schedule_query", "at": 4.6, "pid": None,
         "decision_id": None, "scope": [1, "D", "default"], "version": 1,
         "bin": 0, "action": "S", "fallback": True, "state": "installed",
         "reason": "no_legal_action"},
        {"milestone": "schedule_query", "at": 5.0, "pid": None,
         "decision_id": None, "scope": [2, "D", "default"], "version": None,
         "bin": None, "action": None, "fallback": True,
         "state": "uninitialized", "reason": "no_table_installed"},
        # per-packet decision compute (bounded pool: one job queued)
        {"milestone": "compute_request", "at": 5.0, "pid": 3,
         "decision_id": None, "sat": 1, "compute_job_id": 2,
         "requested_at": 5.0, "service_s": 0.2, "servers": 1,
         "scope": "1|D|default"},
        {"milestone": "compute_wait", "at": 5.0, "pid": 3,
         "decision_id": None, "sat": 1, "compute_job_id": 2, "wait_s": 0.3,
         "service_s": 0.2, "servers": 1, "servers_busy": 1, "queueing": True,
         "requested_at": 5.0},
        {"milestone": "compute_start", "at": 5.3, "pid": 3,
         "decision_id": None, "sat": 1, "compute_job_id": 2,
         "requested_at": 5.0, "started_at": 5.3, "wait_s": 0.3},
        {"milestone": "compute_finish", "at": 5.5, "pid": 3,
         "decision_id": None, "sat": 1, "compute_job_id": 2,
         "requested_at": 5.0, "started_at": 5.3, "finished_at": 5.5,
         "wait_s": 0.3, "service_s": 0.2},
        {"milestone": "compute_request", "at": 5.5, "pid": 4,
         "decision_id": None, "sat": 2, "compute_job_id": 3,
         "requested_at": 5.5, "service_s": 0.2, "servers": 1,
         "scope": "2|D|default"},
        {"milestone": "compute_start", "at": 5.5, "pid": 4,
         "decision_id": None, "sat": 2, "compute_job_id": 3,
         "requested_at": 5.5, "started_at": 5.5, "wait_s": 0.0},
        {"milestone": "compute_finish", "at": 5.7, "pid": 4,
         "decision_id": None, "sat": 2, "compute_job_id": 3,
         "requested_at": 5.5, "started_at": 5.5, "finished_at": 5.7,
         "wait_s": 0.0, "service_s": 0.2},
    ]


def _sink():
    """A re-decision chain per packet, like the kernel's own sink.

    The kernel writes pkt.decision_id as the PREVIOUS commit of that packet, so
    the decision ledger recovers "the next decision for this packet" from the
    row's pid, not from the decision id (a decision id is global).  This
    fixture honours that: d0 -> d1 -> d2 -> d3 are two packets' chains, and each
    commit's candidate_truth[chosen].downstream.peer_egress_direction equals
    what the successor really chooses, so a match is a match for a reason.
    The t0-legal estimate deliberately disagrees at d0: that is the
    deployable-estimate error series, and it must not be merged with the
    hindsight-truth series.
    """
    return [
        {"kind": "forward", "decision_id": 0, "pid": 1, "sat": 0,
         "t": 1.1, "chosen": "E", "obs_mode": "frozen",
         "observation_at_start": {"time_alignment": {"cache_hit": False,
                                                     "fallback": True,
                                                     "installed_version": 1,
                                                     "bin": 0}},
         "estimate_at_start": {"peer_egress_direction": "S",
                               "peer_egress_queue_bits_estimate": 100},
         "info_audit": {"candidate_truth": {"E": {"downstream": {
             "peer_egress_direction": "S", "peer_egress_data_bits": 120,
             "peer_egress_ctrl_bits": 10,
             "peer_in_service_remaining_bits": 40}}}}},
        {"kind": "forward", "decision_id": 1, "pid": 1, "sat": 0,
         "t": 1.4, "chosen": "S", "obs_mode": "frozen",
         "observation_at_start": {"time_alignment": {"cache_hit": True,
                                                     "fallback": False,
                                                     "installed_version": 1,
                                                     "bin": 2}},
         "estimate_at_start": {"peer_egress_direction": "W",
                               "peer_egress_queue_bits_estimate": 200},
         "info_audit": {"candidate_truth": {"S": {"downstream": {
             "peer_egress_direction": "S", "peer_egress_data_bits": 130,
             "peer_egress_ctrl_bits": 10,
             "peer_in_service_remaining_bits": 50}}}}},
        {"kind": "forward", "decision_id": 2, "pid": 2, "sat": 1,
         "t": 1.6, "chosen": "S", "obs_mode": "frozen",
         "observation_at_start": {"time_alignment": {"cache_hit": True,
                                                     "fallback": False}},
         "estimate_at_start": {"peer_egress_direction": "S",
                               "peer_egress_queue_bits_estimate": 300},
         "info_audit": {"candidate_truth": {"S": {"downstream": {
             "peer_egress_direction": "S", "peer_egress_data_bits": 300,
             "peer_egress_ctrl_bits": 0,
             "peer_in_service_remaining_bits": 0}}}}},
        {"kind": "deliver", "decision_id": 3, "pid": 2, "sat": 2,
         "t": 5.6, "chosen": "deliver", "obs_mode": "frozen",
         "observation_at_start": {"time_alignment": {"cache_hit": False,
                                                     "fallback": False}},
         "estimate_at_start": None, "info_audit": {}},
        # second packet of the first chain: the scorer resolves the successor
        # from the row's pid, so d3 must carry pid 1 to close packet 1's chain
        {"kind": "deliver", "decision_id": 4, "pid": 1, "sat": 2,
         "t": 5.7, "chosen": "deliver", "obs_mode": "frozen",
         "observation_at_start": {"time_alignment": {"cache_hit": False,
                                                     "fallback": True}},
         "estimate_at_start": None, "info_audit": {}},
    ]


def _result():
    """A kernel-shaped result with a controlled, hand-checkable fate split."""
    events = _packet_events()
    return {
        "stop_time_s": 10.0,
        "fate_counts": {"DELIVERED": 3, "NO_ROUTE": 1,
                        "DATA_DEADLINE_EXPIRED": 1, "IN_SYSTEM_AT_STOP": 1,
                        "ACCESS_QUEUE_OVERFLOW": 0},
        # pid 1 is the re-decided packet that never arrives (censored), pid 4
        # never leaves (censored), pid 3 is terminally lost.  A packet WITH a
        # delivery record can never be IN_SYSTEM_AT_STOP: that contradiction is
        # exactly what this map must not contain.
        "fates": {0: "DELIVERED", 1: "IN_SYSTEM_AT_STOP", 2: "DELIVERED",
                  3: "NO_ROUTE", 4: "IN_SYSTEM_AT_STOP",
                  5: "DELIVERED"},
        "deliveries": {
            0: {"delivered_at": 0.5, "path": [0]},
            2: {"delivered_at": 5.0, "path": [0, 1, 2]},
            5: {"delivered_at": 9.8, "path": [0, 1, 2]},
        },
        "packet_events": events,
        "link_service_windows": [
            # control-plane transmission windows, separable by packet_kind
            {"pid": None, "stage": "isl", "link_id": "isl:0:1", "start": 0.0,
             "end": 0.01, "rate_bps": 800000.0, "capacity_bits": 8000.0,
             "served_bits": 8000, "bits": 8000, "outcome": "ok",
             "packet_kind": "control"},
            {"pid": None, "stage": "isl", "link_id": "isl:0:1", "start": 0.5,
             "end": 0.52, "rate_bps": 800000.0, "capacity_bits": 16000.0,
             "served_bits": 16000, "bits": 16000, "outcome": "ok",
             "packet_kind": "control"},
        ],
        "queue_area_bits_s": {"uplink": 10.0, "downlink": 20.0,
                              "holding": 30.0, "isl_data": 40.0,
                              "isl_ctrl": 50.0},
        "control": {"bits": {"offered": 24000, "delivered": 16000,
                             "terminal_loss": 0, "in_system": 8000},
                    "totals": {"offered_bits": 24000, "delivered_bits": 16000,
                               "terminal_loss_bits": 0,
                               "in_system_bits_at_stop": 8000}},
        "execution_mode": {
            "mode": "async_window",
            "query_service": {"delay_s": 0.001, "servers_per_satellite": 1,
                              "totals": {"requests": 4, "total_wait_s": 0.4,
                                         "total_service_s": 0.004,
                                         "max_wait_s": 0.2}},
            "precompute": {"builds": 0, "targets": 0, "installs": 0,
                           "queries": 0, "bfs_runs": 0},
        },
        "congestion_metrics": {"offered_packets": 6, "offered_bits": 2100,
                               "admitted_at_satellite_ingress_packets": 6,
                               "delivered_packets": 4},
        "access": {"requests": 4, "grants": 3, "preposition_grants": 1,
                   "wait_time_s_total": 0.2, "wait_time_s_max": 0.15,
                   "slot_hold_s_total": 0.9, "waiting_at_stop": 0,
                   "releases": {}},
    }


def _cost():
    return {"service_s": 0.2, "servers": 1}


def _outcome():
    return om.compare_outcome(_result(), _timeline(), _sink(), _rows(),
                              window=dict(WINDOW), cost=_cost(),
                              context={"cell": "cell-1", "run_id": "run-1"})


def _flat(document):
    return {row["metric"]: row for row in document["rows"]}


def _value(document, metric):
    row = _flat(document)[metric]
    assert row["status"] == "COMPUTED", (metric, row)
    return row["value"]


# ---------------------------------------------------------------- inventory
def test_every_required_a4_metric_is_published_with_a_traceable_row():
    document = _outcome()
    published = set(_flat(document))
    missing = [name for name in REQUIRED_METRICS if name not in published]
    assert missing == []
    for row in document["rows"]:
        assert row["cell"] == "cell-1"
        assert row["run_id"] == "run-1"
        assert row["mode"] == "async_window"


def test_the_counts_partition_the_offered_population_exactly_once():
    """delivered + terminal loss + censored == offered, with no double count."""
    doc = _outcome()
    net = doc["network_outcome"]
    assert net["counts"]["offered"] == 6
    assert net["counts"]["admitted"] == 6
    assert net["counts"]["delivered"] == 3
    assert net["terminal_loss"]["packets"] == 1
    assert net["censoring"]["packets"] == 2
    assert net["counts"]["admitted"] == 6
    assert (net["counts"]["delivered"] + net["terminal_loss"]["packets"]
            + net["censoring"]["packets"]) == net["counts"]["offered"]
    assert doc["partition_exact"] is True


def test_terminal_loss_is_classified_by_fate_and_expiry_is_named():
    net = _outcome()["network_outcome"]
    assert net["terminal_loss"]["by_fate"] == {"NO_ROUTE": 1}
    # this fixture has no deadline expiry, so the named sub-metric is an empty
    # set - and it is an empty set, not a zero that hides an unavailable field
    assert net["terminal_loss"]["expired_and_undelivered"]["packets"] == 0
    assert net["terminal_loss"]["expired_and_undelivered"]["by_fate"] == {}


# ------------------------------------------------------ the two denominators
def test_throughput_reports_both_denominators_labelled_and_never_mixed():
    net = _outcome()["network_outcome"]
    fixed = net["throughput"]["delivered_bits_in_window_over_fixed_window"]
    population = net["throughput"][
        "final_delivery_ratio_of_window_queue_population"]
    assert fixed["denominator_kind"] == "fixed_window_seconds"
    assert fixed["window_s"] == 13.5
    # bits delivered INSIDE the window: pid 0 (100), pid 2 (300), pid 5 (600)
    assert fixed["delivered_bits_in_window"] == 1000
    assert fixed["value_bps"] == pytest.approx(1000 / 13.5)
    assert population["denominator_kind"] == "window_generated_packets"
    assert population["generated_in_window"] == 6
    assert population["delivered_of_generated"] == 3
    assert population["value"] == pytest.approx(0.5)
    assert fixed["denominator_kind"] != population["denominator_kind"]


def test_warmup_and_drain_packets_are_separable_and_never_in_the_ratio():
    """A tighter window makes packets warm-up and drain at once.

    The window is passed explicitly so the warm-up/drain BOUNDARY is what is
    under test; the derivation from the rows has its own test, and the suite
    window is kept equal to it by test_the_suite_window_equals_the_derived_one.
    """
    tight = {"start_s": 2.0, "end_s": 8.0, "fixed_window_s": 6.0,
             "status": "COMPUTED", "unit_interval_s": 2.0,
             "drain_tail_s": 4.0, "source": "explicit test window"}
    doc = om.compare_outcome(_result(), _timeline(), _sink(), _rows(),
                             window=dict(tight), cost=_cost(), context={})
    net = doc["network_outcome"]
    assert net["warmup"]["packets"] == 2
    assert net["warmup"]["pids"] == [0, 1]
    assert net["warmup"]["bits"] == 100 + 200
    assert net["drain"]["packets"] == 1
    assert net["drain"]["pids"] == [5]
    # pid 5 IS delivered, but after the window closed: it is a drain packet and
    # the window ratio must not count it
    assert net["drain"]["delivered"] == 1
    assert net["window"]["offered_in_window"] == 3
    population = net["throughput"][
        "final_delivery_ratio_of_window_queue_population"]
    # generated in the window: pids 2, 3, 4; only pid 2 is ever delivered
    assert population["generated_in_window"] == 3
    assert population["delivered_of_generated"] == 1
    assert population["value"] == pytest.approx(1 / 3)


def test_the_audit_only_path_needs_lookup_evidence():
    """Without a schedule_query row the audit flags alone are not a table lookup.

    The time-alignment audit carries cache_hit/fallback for its OWN lookup, so
    trusting the flag alone would export a hit rate for a mechanism the mode
    never used.  The flag is accepted only beside lookup evidence
    (installed_version or bin).
    """
    timeline = [row for row in _timeline()
                if row.get("milestone") != "schedule_query"]
    doc = om.compare_outcome(_result(), timeline, _sink(), _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    table = doc["network_outcome"]["table"]
    assert table["queries"] == 2          # the two audits that carry evidence
    assert table["hits"] == 1
    assert table["fallbacks"] == 1
    assert table["hit_rate"] == pytest.approx(0.5)
    assert table["fallback_rate"] == pytest.approx(0.5)
    assert "observation_at_start.time_alignment" in table["queries_source"]


def test_a_mode_with_no_table_lookup_reports_not_computable_not_zero():
    """A missing hit rate must never be published as 0.0."""
    audits = json.loads(json.dumps(_sink()))
    for row in audits:
        audit = (row["observation_at_start"] or {}).get("time_alignment")
        if isinstance(audit, dict):
            audit.pop("installed_version", None)
            audit.pop("bin", None)
    timeline = [row for row in _timeline()
                if row.get("milestone") != "schedule_query"]
    doc = om.compare_outcome(_result(), timeline, audits, _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    table = doc["network_outcome"]["table"]
    assert table["queries"] == 0
    assert table["hit_rate"] is None
    assert table["fallback_rate"] is None
    flat = _flat(doc)
    assert flat["table.hit_rate"]["status"] == "NOT_COMPUTABLE"
    assert flat["table.hit_rate"]["value"] is None


def test_the_suite_window_equals_the_derived_one():
    """The shared WINDOW constant must BE what build_measurement_window returns."""
    derived = om.build_measurement_window(_rows())
    for key in ("start_s", "end_s", "fixed_window_s", "unit_interval_s",
                "drain_tail_s"):
        assert WINDOW[key] == pytest.approx(derived[key]), key


def test_administrative_censoring_is_reported_as_censoring_not_loss():
    net = _outcome()["network_outcome"]
    assert net["censoring"]["still_in_system_at_stop"] == 2
    assert net["censoring"]["pids"] == [1, 4]
    assert net["censoring"]["kind"] == "administrative_censoring"
    assert "NOT" in net["censoring"]["note"]
    assert 4 not in net["terminal_loss"]["by_fate"].values()
    assert "IN_SYSTEM_AT_STOP" not in net["terminal_loss"]["by_fate"]
    assert net["censoring"]["bits"] == 200 + 500


def test_the_queue_population_ratio_is_never_the_admitted_packet_ratio():
    """The denominator is packets GENERATED in the window, not admitted ones."""
    result = _result()
    # drop pid 2 admission: it was generated in the window but never admitted
    result["packet_events"] = [
        e for e in result["packet_events"]
        if not (e["pid"] == 2 and e["kind"] == "satellite_ingress")]
    result["fates"][2] = "ACCESS_REJECTED"
    doc = om.compare_outcome(result, _timeline(), _sink(), _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    net = doc["network_outcome"]
    pop = net["throughput"][
        "final_delivery_ratio_of_window_queue_population"]
    assert net["counts"]["admitted"] == 5
    assert pop["generated_in_window"] == 6          # unchanged by admission
    assert pop["value"] == pytest.approx(0.5)


# ------------------------------------------------------------------ payload
def test_payload_goodput_uses_the_fixed_window_and_the_delivered_bits():
    payload = _outcome()["network_outcome"]["payload"]
    assert payload["delivered_bits_in_window"] == 1000
    assert payload["fixed_window_s"] == 13.5
    assert payload["goodput_bps"] == pytest.approx(1000 / 13.5)
    assert payload["basis"] == "bits_delivered_inside_the_window"


def test_e2e_percentiles_are_linear_interpolation_over_delivered_packets():
    e2e = _outcome()["network_outcome"]["e2e"]
    # emitted_at -> delivered_at for the four delivered packets
    assert e2e["samples"] == 3
    # delays: pid0 0.5 (emitted 0.0), pid2 2.0 (3.0 -> 5.0),
    # pid5 0.3 (9.5 -> 9.8)  -- linear interpolation, not nearest rank
    assert e2e["samples"] == 3
    assert e2e["mean_s"] == pytest.approx((0.5 + 2.0 + 0.3) / 3.0)
    assert e2e["p50_s"] == pytest.approx(0.5)
    assert e2e["p95_s"] == pytest.approx(1.85)
    assert e2e["p99_s"] == pytest.approx(1.97)
    assert e2e["p95_s_source"].startswith("CODE.experiment_platform"
                                          ".t1_stats._percentile")


def test_queue_peak_and_area_are_reported_with_their_sources():
    queue = _outcome()["network_outcome"]["queue"]
    assert queue["area_bits_s"]["total"] == pytest.approx(150.0)
    assert queue["area_bits_s"]["by_area"]["isl_data"] == pytest.approx(40.0)
    assert queue["peak_bits"] == pytest.approx(900 + 450)
    assert queue["peak_resource"] == "isl:1:2"
    assert queue["peak_basis"] == "max(backlog_before) over queue_enter rows"


# --------------------------------------------------- per-satellite pressure
def test_per_satellite_rates_include_redecisions_and_report_the_hotspot():
    rates = _outcome()["network_outcome"]["rate_per_satellite"]
    # sat 0: commits 0, 1, 4 plus the redecision to 4; sat 1: commit 2 plus
    # the redecision to 2; sat 2: the two deliver commits 3 and 5.  A
    # re-decision is a request that really happened, so it counts.
    assert rates["requests_by_satellite"] == {"0": 4, "1": 2, "2": 2}
    assert rates["total_requests"] == 8
    assert rates["redecisions"] == 3
    assert rates["window_s"] == 13.5
    assert rates["hotspot_max_per_s"] == pytest.approx(4 / 13.5)
    assert rates["hotspot_satellite"] == 0
    assert rates["mean_per_s"] == pytest.approx((4 + 2 + 2) / 3 / 13.5)
    assert rates["hotspot_is_not_the_mean"] is True
    assert rates["all_satellites_mean_per_s"] == pytest.approx(
        rates["mean_per_s"])
    assert rates["note"].startswith("hotspot pressure is the MAXIMUM")


def test_a_single_hot_satellite_is_not_hidden_by_the_mean():
    rates = _outcome()["network_outcome"]["rate_per_satellite"]
    assert rates["hotspot_max_per_s"] > rates["mean_per_s"]


# -------------------------------------------------------------- total compute
def test_compute_cost_splits_packet_decisions_from_background_updates():
    cost = _outcome()["total_cost"]
    assert cost["packet_decisions"]["jobs"] == 2
    assert cost["packet_decisions"]["queue_wait_s"] == pytest.approx(0.3)
    assert cost["packet_decisions"]["service_s"] == pytest.approx(0.4)
    assert cost["packet_decisions"]["queued_jobs"] == 1
    assert cost["background_updates"]["jobs"] == 1
    assert cost["background_updates"]["queue_wait_s"] == pytest.approx(0.5)
    assert cost["background_updates"]["service_s"] == pytest.approx(0.1)
    assert cost["background_updates"]["mode"] == "async"


def test_the_total_compute_figure_includes_the_background_jobs():
    cost = _outcome()["total_cost"]
    assert cost["total"]["jobs"] == 3
    assert cost["total"]["queue_wait_s"] == pytest.approx(0.8)
    assert cost["total"]["service_s"] == pytest.approx(0.5)
    assert cost["total"]["includes_background_updates"] is True


def test_unbounded_compute_still_records_request_start_and_finish_events():
    timeline = [m for m in _timeline()
                if m.get("compute_job_id") in (None, 1)]
    doc = om.compare_outcome(_result(), timeline, _sink(), _rows(),
                             window=dict(WINDOW),
                             cost={"service_s": 0.2, "servers": 0},
                             context={})
    cost = doc["total_cost"]
    assert cost["servers_per_satellite"] == 0
    assert cost["unbounded"] is True
    assert cost["background_updates"]["jobs"] == 1
    assert cost["background_updates"]["start_events"] == 1
    assert cost["background_updates"]["finish_events"] == 1
    assert cost["background_updates"]["queue_wait_s"] == pytest.approx(0.5)
    assert cost["background_updates"]["zero_queueing"] is False
    # a genuinely unbounded pool: request == start, so no queueing at all
    relay = [dict(m, wait_s=0.0, at=m["requested_at"])
             for m in timeline if m.get("compute_job_id") == 1]
    doc2 = om.compare_outcome(_result(), relay + [
        m for m in timeline if m.get("compute_job_id") not in (None, 1)],
        _sink(), _rows(), window=dict(WINDOW),
        cost={"service_s": 0.2, "servers": 0}, context={})
    bg = doc2["total_cost"]["background_updates"]
    assert bg["zero_queueing"] is True
    assert bg["jobs"] == len(bg["job_ids"])


# ---------------------------------------------------------- separate lines
def test_query_install_and_control_traffic_are_separate_lines():
    doc = _outcome()
    cost = doc["total_cost"]
    assert cost["query_service"]["requests"] == 4
    assert cost["query_service"]["service_s"] == pytest.approx(0.004)
    assert cost["install"]["installs"] == 1
    assert cost["install"]["delay_samples"] == 1
    assert cost["install"]["mean_delay_s"] == pytest.approx(0.1)
    control = cost["control_traffic"]
    assert control["counters"]["delivered_bits"] == 16000
    assert control["occupancy_seconds"] == pytest.approx(0.03)
    assert control["bits_source"].startswith("result.control")
    assert control["occupancy_source"].startswith(
        "result.link_service_windows")
    assert cost["stages_never_summed_into_e2e"] is True
    assert "e2e" not in json.dumps(cost["query_service"]).lower()


def test_the_access_slot_service_is_its_own_line_too():
    access = _outcome()["total_cost"]["access_service"]
    assert access["grants"] == 3
    assert access["slot_hold_seconds"] == pytest.approx(0.9)
    assert access["wait_seconds"] == pytest.approx(0.2)
    assert access["source"].startswith("result.access")
    assert "never summed" in access["note"]
    cost = _outcome()["total_cost"]
    assert {"query_service", "install", "control_traffic",
            "access_service"} <= set(cost)


def test_the_stages_are_not_summed_into_one_e2e_number():
    doc = _outcome()
    assert "e2e" not in doc["total_cost"]
    assert doc["e2e_stage_disclaimer"].startswith(
        "query service, install and control traffic")


# ------------------------------------------------------------- table reuse
def test_table_hit_fallback_rates_bins_and_version_age():
    reuse = _outcome()["network_outcome"]["table"]
    assert reuse["queries"] == 4
    assert reuse["hits"] == 2
    assert reuse["fallbacks"] == 2
    assert reuse["hit_rate"] == pytest.approx(0.5)
    assert reuse["fallback_rate"] == pytest.approx(0.5)
    assert reuse["bins_used"] == [0, 2]
    assert reuse["installs"] == 1
    assert reuse["version_age"]["samples"] == 3
    assert reuse["version_age"]["status"] == "COMPUTED"


def test_version_age_uses_the_installation_instant_of_the_queried_version():
    timeline = _timeline() + [
        {"milestone": "schedule_query", "at": 3.2, "pid": None,
         "decision_id": None, "scope": [1, "D", "default"], "version": 1,
         "bin": 2, "action": "E", "fallback": False,
         "state": "installed", "reason": None},
    ]
    doc = om.compare_outcome(_result(), timeline, _sink(), _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    table = doc["network_outcome"]["table"]
    assert table["bins_used"] == [0, 2]
    # the extra query is at 3.2 with the same installed version 1 (at 2.7),
    # beside the fixture's queries at 3.0, 3.1 and 4.6
    ages = [3.0 - 2.7, 3.1 - 2.7, 3.2 - 2.7, 4.6 - 2.7]
    assert table["version_age"]["samples"] == len(ages)
    assert table["version_age"]["mean_s"] == pytest.approx(
        sum(ages) / len(ages))
    assert table["version_age"]["max_s"] == pytest.approx(4.6 - 2.7)


# --------------------------------------------------------------- prediction
def test_resource_mismatch_and_prediction_errors_are_reported():
    prediction = _outcome()["network_outcome"]["prediction"]
    # d0 predicted E and its successor chose E; d1 predicted S and its
    # successor chose S -> comparable and matching.  d2's successor DELIVERS,
    # so it has no contended egress and is not comparable (never a mismatch).
    # d0's successor really chose E, d1's successor really chose S; d2's
    # successor DELIVERS, so it has no contended egress and is not comparable.
    # The eta error needs an eta_targets audit and a peer_arrival milestone,
    # which this fixture does not carry: zero samples, not a zero error.
    assert prediction["resource_compared"] == 2
    assert prediction["resource_mismatches"] == 0
    assert prediction["resource_match_rate"] == pytest.approx(1.0)
    assert prediction["eta_error"]["samples"] == 0
    assert prediction["same_resource"]["samples"] == 0


def test_a_predicted_direction_that_did_not_match_is_counted_as_mismatch():
    sink = _sink()
    rerouted = json.loads(json.dumps(sink))
    # d1's belief is rewritten to W, but the peer's successor really chooses S:
    # one mismatch out of two comparable decisions
    rerouted[1]["info_audit"]["candidate_truth"]["S"]["downstream"][
        "peer_egress_direction"] = "W"
    doc = om.compare_outcome(_result(), _timeline(), rerouted, _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    prediction = doc["network_outcome"]["prediction"]
    assert prediction["resource_mismatches"] == 1
    assert prediction["resource_match_rate"] == pytest.approx(0.5)


# ------------------------------------------------------- NOT_COMPUTABLE rule
def test_a_missing_raw_field_is_not_computable_and_names_the_field():
    result = _result()
    del result["queue_area_bits_s"]
    doc = om.compare_outcome(result, _timeline(), _sink(), _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    entry = doc["not_computable"]["queue.area_bits_s"]
    assert entry["missing_field"] == "queue_area_bits_s"
    row = _flat(doc)["queue.area_bits_s"]
    assert row["status"] == "NOT_COMPUTABLE"
    assert row["value"] is None
    assert "queue_area_bits_s" in row["missing_field"]


def test_a_missing_field_never_becomes_zero():
    result = _result()
    del result["queue_area_bits_s"]
    doc = om.compare_outcome(result, _timeline(), _sink(), _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    assert _flat(doc)["queue.area_bits_s"]["value"] is None
    assert _flat(doc)["queue.area_bits_s"]["value"] != 0


def test_an_absent_window_is_not_computable_rather_than_assumed():
    doc = om.compare_outcome(_result(), _timeline(), _sink(), [], window=None,
                             cost=_cost(), context={})
    assert doc["network_outcome"]["window"]["status"] == "NOT_COMPUTABLE"
    assert doc["network_outcome"]["window"]["missing_field"] == "trace_rows"
    assert _flat(doc)["throughput.delivered_bits_in_window_over_fixed_window"][
        "status"] == "NOT_COMPUTABLE"


# ------------------------------------------------------------------- machine
def test_the_machine_block_is_json_serializable():
    doc = _outcome()
    assert json.loads(json.dumps(doc, default=str))["schema"] == om.SCHEMA


def test_the_long_form_csv_writer_round_trips_every_row(tmp_path):
    doc = _outcome()
    path = tmp_path / "outcome.csv"
    written = om.write_long_form_csv(doc, str(path))
    assert written == len(doc["rows"])
    with open(path, newline="", encoding="utf-8") as handle:
        back = list(csv.DictReader(handle))
    assert len(back) == len(doc["rows"])
    assert back[0]["cell"] == "cell-1"
    assert {row["metric"] for row in back} == {row["metric"]
                                               for row in doc["rows"]}


def test_the_csv_rows_carry_the_cell_and_run_id_of_every_row(tmp_path):
    doc = _outcome()
    path = tmp_path / "outcome.csv"
    om.write_long_form_csv(doc, str(path))
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            assert row["cell"] == "cell-1"
            assert row["run_id"] == "run-1"
            assert row["mode"] == "async_window"


def test_the_csv_writer_refuses_a_path_whose_parent_does_not_exist(tmp_path):
    with pytest.raises(om.OutcomeMetricsError):
        om.write_long_form_csv(_outcome(), str(tmp_path / "nope" / "x.csv"))


# ----------------------------------------------------------- row integration
def test_every_mode_row_carries_the_a4_blocks_and_keeps_its_old_keys():
    resolved, rows, geometry, _meta = scripted_scenarios.build("contention")
    row = ec._row_for_mode(resolved, rows, geometry, "per_flow")
    # nothing that existed before may be renamed or dropped
    for key in ("mode", "config_sha256", "resolved_execution_mode",
                "async_enabled", "seed", "arm", "predictor", "compute",
                "reuse", "scale", "query_service", "precompute", "outcome"):
        assert key in row, key
    assert row["network_outcome"]["counts"]["offered"] == 2
    assert row["total_cost"]["total"]["jobs"] > 0
    assert row["measurement_window"]["status"] == "COMPUTED"


def test_the_mode_row_a4_rows_cover_every_required_metric():
    resolved, rows, geometry, _meta = scripted_scenarios.build("contention")
    row = ec._row_for_mode(resolved, rows, geometry, "async_window")
    published = {entry["metric"] for entry in row["outcome_document"]["rows"]}
    missing = [name for name in REQUIRED_METRICS if name not in published]
    assert missing == []
    assert row["outcome_document"]["row_count"] == len(
        row["outcome_document"]["rows"])


def test_the_two_throughput_denominators_survive_the_row_round_trip():
    """A mode row must keep both denominators labelled, never merged."""
    resolved, rows, geometry, _meta = scripted_scenarios.build("contention")
    row = ec._row_for_mode(resolved, rows, geometry, "async_point")
    throughput = row["network_outcome"]["throughput"]
    assert set(throughput) >= {
        "delivered_bits_in_window_over_fixed_window",
        "final_delivery_ratio_of_window_queue_population"}
    kinds = {throughput["delivered_bits_in_window_over_fixed_window"][
                 "denominator_kind"],
             throughput["final_delivery_ratio_of_window_queue_population"][
                 "denominator_kind"]}
    assert len(kinds) == 2


def test_the_comparison_document_carries_the_outcome_blocks_per_mode(tmp_path):
    resolved, rows, geometry, source = _design_source()
    document = ec.compare(resolved, rows, geometry, source,
                          modes=("per_packet", "async_window"))
    assert document["schema"] == "execution-compare/v1"
    assert document["units"]["outcome"]["schema"] == om.SCHEMA
    for mode_row in document["modes"]:
        assert mode_row["measurement_window"]["fixed_window_s"] > 0
        assert mode_row["network_outcome"]["counts"]["offered"] == 2
        assert "total_cost" in mode_row
    assert any("network_outcome" in limit for limit in document["limits"])


def test_a_mode_row_can_be_published_as_a_long_form_csv(tmp_path):
    """The A4 block must be regenerable from a table, not only from JSON."""
    resolved, rows, geometry, _meta = scripted_scenarios.build("contention")
    row = ec._row_for_mode(resolved, rows, geometry, "async_point")
    document = {
        "schema": row["outcome_document"]["schema"],
        "context": {"cell": row["mode"], "run_id": row["mode"],
                    "mode": row["mode"],
                    "config_sha256": row["config_sha256"]},
        "network_outcome": {"window": row["measurement_window"]},
        "rows": row["outcome_document"]["rows"],
    }
    path = tmp_path / "mode-row.csv"
    written = om.write_long_form_csv(document, str(path))
    assert written == row["outcome_document"]["row_count"]
    with open(path, newline="", encoding="utf-8") as handle:
        back = list(csv.DictReader(handle))
    assert {entry["metric"] for entry in back} == {
        entry["metric"] for entry in row["outcome_document"]["rows"]}
    assert all(entry["cell"] == "async_point" for entry in back)
    assert all(entry["mode"] == "async_point" for entry in back)
    assert all(entry["window_start_s"] not in ("", None) for entry in back)


def _design_source():
    resolved, rows, geometry, meta = scripted_scenarios.build("contention")
    return (resolved, rows, geometry,
            {"scenario": "contention", "config": None,
             "trace_sha256": None, "rows": len(rows)})


# ------------------------------------------------------------- integration
def _scripted(mode="async_window"):
    resolved, rows, geometry, _meta = scripted_scenarios.build("contention")
    cfg = ec._mode_config(resolved, mode, None)
    timeline, sink = [], []
    result = kernel.run_simulation(cfg, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=timeline)
    return resolved, rows, cfg, result, timeline, sink


def test_a_real_kernel_run_produces_a_complete_outcome_document():
    resolved, rows, cfg, result, timeline, sink = _scripted("async_window")
    doc = om.compare_outcome(
        result, timeline, sink, rows, window=om.build_measurement_window(rows),
        cost={"service_s": cfg["config"]["execution"]["compute_delay_s"],
              "servers": cfg["config"]["execution"][
                  "compute_servers_per_satellite"]},
        context={"cell": "scripted", "run_id": "kernel-async_window"})
    net = doc["network_outcome"]
    assert net["counts"]["offered"] == 2
    assert net["counts"]["delivered"] == 2
    assert doc["total_cost"]["background_updates"]["jobs"] > 0
    assert doc["total_cost"]["total"]["service_s"] >= (
        doc["total_cost"]["background_updates"]["service_s"])
    assert doc["stop_rule"]["interrupted"] is False


def test_an_unbounded_kernel_run_still_records_every_compute_milestone():
    resolved, rows, cfg, result, timeline, sink = _scripted("per_packet")
    # a BOUNDED pool first, so the zero-queueing case has something to differ
    # from: with one server the fixture visibly queues
    bounded = ec._mode_config(resolved, "per_packet", {
        "execution": {"compute_servers_per_satellite": 1}})
    b_timeline = []
    kernel.run_simulation(bounded, rows, geometry=None, decision_sink=[],
                          timeline_sink=b_timeline)
    b_doc = om.compare_outcome(
        result, b_timeline, sink, rows, window=om.build_measurement_window(rows),
        cost={"service_s": bounded["config"]["execution"]["compute_delay_s"],
              "servers": 1},
        context={"cell": "scripted", "run_id": "kernel-bounded"})
    assert b_doc["total_cost"]["packet_decisions"]["queued_jobs"] > 0
    unbounded = ec._mode_config(resolved, "per_packet", {
        "execution": {"compute_servers_per_satellite": 0}})
    timeline, sink = [], []
    result = kernel.run_simulation(unbounded, rows, geometry=None,
                                   decision_sink=sink, timeline_sink=timeline)
    doc = om.compare_outcome(
        result, timeline, sink, rows, window=om.build_measurement_window(rows),
        cost={"service_s": unbounded["config"]["execution"]["compute_delay_s"],
              "servers": 0},
        context={"cell": "scripted", "run_id": "kernel-unbounded"})
    cost = doc["total_cost"]
    assert cost["unbounded"] is True
    assert cost["packet_decisions"]["jobs"] > 0
    assert cost["packet_decisions"]["start_events"] == (
        cost["packet_decisions"]["jobs"])
    assert cost["packet_decisions"]["finish_events"] == (
        cost["packet_decisions"]["jobs"])
    assert cost["packet_decisions"]["queue_wait_s"] == pytest.approx(0.0)


# ------------------------------------------------------ measurement window
def test_the_measurement_window_is_derived_from_the_trace_rows():
    # the fixture's first emission is at 0.0 and its median inter-arrival is
    # 2.0 s, so the window is [0.0, 9.0 + 4.0] - derived from the ROWS
    window = om.build_measurement_window(_rows())
    assert window["start_s"] == pytest.approx(0.0)
    assert window["end_s"] == pytest.approx(13.5)
    assert window["fixed_window_s"] == pytest.approx(13.5)
    assert window["unit_interval_s"] == pytest.approx(2.0)
    assert window["status"] == "COMPUTED"


def test_the_window_is_never_computed_from_an_empty_trace():
    window = om.build_measurement_window([])
    assert window["status"] == "NOT_COMPUTABLE"
    assert window["missing_field"] == "trace_rows"


def test_a_window_that_excludes_delivery_is_reported_as_censoring():
    """A too-short drain window must not be reported as loss."""
    result = _result()
    result["fate_counts"] = dict(result["fate_counts"], IN_SYSTEM_AT_STOP=3)
    result["fates"] = dict(result["fates"])
    result["fates"][1] = "IN_SYSTEM_AT_STOP"
    result["fates"][2] = "IN_SYSTEM_AT_STOP"
    result["deliveries"] = {0: {"delivered_at": 0.5, "path": [0]},
                            5: {"delivered_at": 9.5, "path": [0, 1, 2]}}
    doc = om.compare_outcome(result, _timeline(), _sink(), _rows(),
                             window=dict(WINDOW), cost=_cost(), context={})
    net = doc["network_outcome"]
    assert net["censoring"]["still_in_system_at_stop"] == 3
    assert net["terminal_loss"]["packets"] == 1
    assert net["censoring"]["kind"] == "administrative_censoring"
