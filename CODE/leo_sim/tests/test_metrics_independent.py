"""Tests for the independent second implementation of the raw run metrics.

The production path (receipt.py and kernel.py) recomputes the congestion
metrics and the delay decomposition with the *same* metrics.summarize call, so
comparing its output with itself proves nothing.  These tests drive
metrics_independent -- which never imports that module -- against hand-built
event streams and against real kernel runs, and require the two independent
implementations to agree field by field while the closure identity

    e2e == queue_wait + holding_wait + tx + propagation + decision_compute

holds on every delivered packet.  The compute_delay_s = 0 runs are contrasted
with compute_delay_s > 0 runs: without that contrast the uncovered-interval
term is identically zero and the closure gate would be a tautology.
"""
from __future__ import annotations

import json
import math

import pytest

from CODE.leo_sim import kernel, metrics
from CODE.leo_sim import metrics_independent as indep
from CODE.leo_sim.tests.helpers import (StaticGeometry, cell, cell_center,
                                        make_cfg, row)

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)


# ------------------------------------------------------------------ helpers
def _link_mismatches(report, reference):
    """Every field on which the independent and production reports disagree."""
    mismatches = []
    if set(report) != set(reference):
        return [("link set", sorted(report), sorted(reference))]
    for link_id, mine in sorted(report.items()):
        other = reference[link_id]
        for key in ("served_bits", "capacity_bits"):
            if not math.isclose(mine[key], other[key],
                                rel_tol=1e-12, abs_tol=1e-9):
                mismatches.append((link_id, key, mine[key], other[key]))
        if mine["status"] == indep.STATUS_OK:
            for key in ("available_capacity_bits", "utilization"):
                if not math.isclose(mine[key], other[key],
                                    rel_tol=1e-12, abs_tol=1e-15):
                    mismatches.append((link_id, key, mine[key], other[key]))
        else:
            # The production fallback is compared explicitly instead of being
            # accepted as the answer.
            if mine["utilization"] is not None:
                mismatches.append((link_id, "utilization", mine["utilization"],
                                   other["utilization"]))
            if not math.isclose(mine["fallback_utilization"],
                                other["utilization"],
                                rel_tol=1e-12, abs_tol=1e-15):
                mismatches.append((link_id, "fallback_utilization",
                                   mine["fallback_utilization"],
                                   other["utilization"]))
    return mismatches


def _ring(num_satellites):
    neighbors = {i: {"E": (i + 1) % num_satellites,
                     "W": (i - 1) % num_satellites}
                 for i in range(num_satellites)}

    def visible(sat, lat, lon, t):
        return ((sat == 0 and (lat, lon) == AC)
                or (sat == num_satellites // 2 and (lat, lon) == BC))

    return StaticGeometry(num_satellites, neighbors_map=neighbors,
                          visible=visible)


def _run(num_satellites, packets, delay, interval, duration=120.0, isl=8.0,
         emit_step=0.1, emit_times=None):
    cfg = make_cfg({
        "scenario": {"duration_s": duration, "num_satellites": num_satellites,
                     "num_planes": 1, "seed": 5},
        "links": {"isl_rate_mbps": isl},
        "execution": {"available_capacity_interval_s": interval,
                      "compute_delay_s": delay},
    })
    sink = []
    if emit_times is None:
        emit_times = [emit_step * pid for pid in range(1, packets + 1)]
    rows = [row(pid, at, A, B) for pid, at in enumerate(emit_times, start=1)]
    result = kernel.run_simulation(cfg, rows, geometry=_ring(num_satellites),
                                   decision_sink=sink)
    return result, sink


def _report(result, **kwargs):
    return indep.verify_delay_decomposition(
        result["packet_events"], result["link_service_windows"],
        result, **kwargs)


def _utilization(result):
    return indep.recompute_link_utilization(
        result["packet_events"], result["link_service_windows"],
        result["link_available_windows"])


def _deferred_decisions(sink):
    """Decisions that really consumed simulated computation time."""
    return sum(1 for row_ in sink
               if float(row_["t"]) > float(row_["t_decision_start"]))


# -------------------------------------------------------- analytic fixtures
ONE_HOP_EVENTS = [
    {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
    {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue": "isl",
     "link_id": "isl:0:1", "queue_id": 7},
    {"kind": "service_start", "pid": 1, "at": 0.5, "stage": "isl",
     "link_id": "isl:0:1", "queue_id": 7, "bits": 100, "rate_bps": 400.0},
    {"kind": "propagation_start", "pid": 1, "at": 0.75, "stage": "isl",
     "link_id": "isl:0:1", "prop_id": 3, "delay_s": 0.25},
    {"kind": "propagation_arrival", "pid": 1, "at": 1.0, "prop_id": 3},
    {"kind": "delivered", "pid": 1, "at": 1.0},
]
ONE_HOP_WINDOWS = [{
    "pid": 1, "stage": "isl", "link_id": "isl:0:1",
    "start": 0.5, "end": 0.75, "rate_bps": 400.0,
    "capacity_bits": 100.0, "served_bits": 100,
    "bits": 100, "outcome": "ok",
}]
SAMPLED_WINDOWS = [
    {"stage": "isl", "link_id": "isl:0:1", "start": 0.0, "end": 0.25,
     "rate_bps": 400.0, "capacity_bits": 100.0},
    {"stage": "isl", "link_id": "isl:0:1", "start": 0.25, "end": 0.5,
     "rate_bps": 400.0, "capacity_bits": 100.0},
    {"stage": "isl", "link_id": "isl:0:1", "start": 0.5, "end": 1.0,
     "rate_bps": 400.0, "capacity_bits": 200.0},
    {"stage": "isl", "link_id": "isl:1:2", "start": 0.0, "end": 0.25,
     "rate_bps": 200.0, "capacity_bits": 50.0},
    {"stage": "isl", "link_id": "isl:2:3", "start": 0.0, "end": 1.0,
     "rate_bps": 800.0, "capacity_bits": 800.0},
]
STALLED_WINDOW = {
    "pid": 1, "stage": "isl", "link_id": "isl:1:2",
    "start": 0.0, "end": 0.25, "rate_bps": 200.0,
    "capacity_bits": 50.0, "served_bits": 0,
    "bits": 100, "outcome": "stalled",
}


def test_analytic_degenerate_denominator_is_an_explicit_status_not_one():
    """With no availability ledger the sampled denominator does not exist.

    The production function silently substitutes the service-window sum, which
    makes utilization ~1.0 by construction; that reading is flagged here.
    """
    report = indep.recompute_link_utilization(
        ONE_HOP_EVENTS, ONE_HOP_WINDOWS, [])
    link = report["isl:0:1"]
    assert link["status"] == indep.STATUS_DEGENERATE
    assert link["utilization"] is None
    assert link["available_capacity_bits"] is None
    assert link["served_bits"] == 100.0
    assert link["capacity_bits"] == pytest.approx(100.0)
    assert link["fallback_utilization"] == pytest.approx(1.0)
    assert link["available_samples"] == 0

    # The production implementation publishes exactly that fallback as if it
    # were a measurement; this assertion pins the divergence.
    production = metrics.summarize(ONE_HOP_EVENTS, ONE_HOP_WINDOWS)
    reference = production["links"]["isl:0:1"]
    assert reference["utilization"] == pytest.approx(1.0)
    assert reference["available_capacity_bits"] == pytest.approx(100.0)
    assert indep.STATUS_DEGENERATE != indep.STATUS_OK


def test_analytic_idle_and_stalled_links_still_enter_the_sampled_denominator():
    """Availability is sampled independently of service.

    An idle link (no service window at all) and a link whose service stalled
    (served_bits == 0) must both appear with a real denominator and 0.0
    utilization, and a served link must be measured against the sampled
    availability rather than against its own service window.
    """
    windows = list(ONE_HOP_WINDOWS) + [STALLED_WINDOW]
    report = indep.recompute_link_utilization(
        ONE_HOP_EVENTS, windows, SAMPLED_WINDOWS)

    served = report["isl:0:1"]
    assert served["status"] == indep.STATUS_OK
    assert served["served_bits"] == 100.0
    assert served["capacity_bits"] == pytest.approx(100.0)
    assert served["available_capacity_bits"] == pytest.approx(400.0)
    assert served["utilization"] == pytest.approx(0.25)
    assert served["available_samples"] == 3

    stalled = report["isl:1:2"]
    assert stalled["status"] == indep.STATUS_OK
    assert stalled["served_bits"] == 0.0
    assert stalled["service_windows"] == 1
    assert stalled["available_capacity_bits"] == pytest.approx(50.0)
    assert stalled["utilization"] == pytest.approx(0.0)

    idle = report["isl:2:3"]
    assert idle["served_bits"] == 0.0
    assert idle["service_windows"] == 0
    assert idle["capacity_bits"] == 0.0
    assert idle["available_capacity_bits"] == pytest.approx(800.0)
    assert idle["utilization"] == pytest.approx(0.0)

    served_total = sum(item["served_bits"] for item in report.values())
    available_total = sum(item["available_capacity_bits"]
                          for item in report.values())
    assert served_total == 100.0
    assert served_total <= available_total

    production = metrics.summarize(
        ONE_HOP_EVENTS, windows,
        available_capacity_windows=SAMPLED_WINDOWS)
    assert _link_mismatches(report, production["links"]) == []


HOLDING_EVENTS = [
    {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
    {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue": "uplink",
     "link_id": "gsl:uplink:pending:a", "queue_id": 0},
    {"kind": "service_start", "pid": 1, "at": 0.0, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "queue_id": 0, "bits": 100,
     "rate_bps": 1000.0},
    {"kind": "propagation_start", "pid": 1, "at": 0.1, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "prop_id": 0, "delay_s": 0.01},
    {"kind": "propagation_arrival", "pid": 1, "at": 0.11, "prop_id": 0},
    {"kind": "satellite_ingress", "pid": 1, "at": 0.11, "endpoint": "a",
     "satellite": 0, "bits": 100},
    {"kind": "queue_enter", "pid": 1, "at": 0.11, "queue": "holding",
     "link_id": "holding:0", "queue_id": 1},
    {"kind": "queue_enter", "pid": 1, "at": 2.0, "queue": "isl",
     "link_id": "isl:0:1", "queue_id": 2},
    {"kind": "service_start", "pid": 1, "at": 2.0, "stage": "isl",
     "link_id": "isl:0:1", "queue_id": 2, "bits": 100, "rate_bps": 200.0},
    {"kind": "propagation_start", "pid": 1, "at": 2.5, "stage": "isl",
     "link_id": "isl:0:1", "prop_id": 1, "delay_s": 0.02},
    {"kind": "propagation_arrival", "pid": 1, "at": 2.52, "prop_id": 1},
    {"kind": "delivered", "pid": 1, "at": 2.52},
    # packet 2 is still held at the horizon: its holding admission has no
    # following admission, so it must not be credited with an exit time
    {"kind": "packet_emitted", "pid": 2, "at": 0.0, "bits": 100},
    {"kind": "queue_enter", "pid": 2, "at": 0.0, "queue": "uplink",
     "link_id": "gsl:uplink:pending:a", "queue_id": 3},
    {"kind": "service_start", "pid": 2, "at": 0.0, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "queue_id": 3, "bits": 100,
     "rate_bps": 1000.0},
    {"kind": "propagation_start", "pid": 2, "at": 0.1, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "prop_id": 2, "delay_s": 0.01},
    {"kind": "propagation_arrival", "pid": 2, "at": 0.11, "prop_id": 2},
    {"kind": "satellite_ingress", "pid": 2, "at": 0.11, "endpoint": "a",
     "satellite": 0, "bits": 100},
    {"kind": "queue_enter", "pid": 2, "at": 0.11, "queue": "holding",
     "link_id": "holding:0", "queue_id": 4},
]
HOLDING_WINDOWS = [
    {"pid": 1, "stage": "uplink", "link_id": "gsl:uplink:0:a",
     "start": 0.0, "end": 0.1, "rate_bps": 1000.0, "capacity_bits": 100.0,
     "served_bits": 100, "bits": 100, "outcome": "ok"},
    {"pid": 1, "stage": "isl", "link_id": "isl:0:1",
     "start": 2.0, "end": 2.5, "rate_bps": 200.0, "capacity_bits": 100.0,
     "served_bits": 100, "bits": 100, "outcome": "ok"},
    {"pid": 2, "stage": "uplink", "link_id": "gsl:uplink:0:a",
     "start": 0.0, "end": 0.1, "rate_bps": 1000.0, "capacity_bits": 100.0,
     "served_bits": 100, "bits": 100, "outcome": "ok"},
]
GAP_EVENTS = [
    {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
    {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue": "uplink",
     "link_id": "gsl:uplink:pending:a", "queue_id": 0},
    {"kind": "service_start", "pid": 1, "at": 0.0, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "queue_id": 0, "bits": 100,
     "rate_bps": 1000.0},
    {"kind": "propagation_start", "pid": 1, "at": 0.1, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "prop_id": 0, "delay_s": 0.01},
    {"kind": "propagation_arrival", "pid": 1, "at": 0.11, "prop_id": 0},
    {"kind": "satellite_ingress", "pid": 1, "at": 0.11, "endpoint": "a",
     "satellite": 0, "bits": 100},
    # 0.25 s of simulated time consumed by a decision that no service window
    # and no propagation interval covers
    {"kind": "queue_enter", "pid": 1, "at": 0.36, "queue": "isl",
     "link_id": "isl:0:1", "queue_id": 1},
    {"kind": "service_start", "pid": 1, "at": 0.36, "stage": "isl",
     "link_id": "isl:0:1", "queue_id": 1, "bits": 100, "rate_bps": 200.0},
    {"kind": "propagation_start", "pid": 1, "at": 0.86, "stage": "isl",
     "link_id": "isl:0:1", "prop_id": 1, "delay_s": 0.02},
    {"kind": "propagation_arrival", "pid": 1, "at": 0.88, "prop_id": 1},
    {"kind": "delivered", "pid": 1, "at": 0.88},
]
GAP_WINDOWS = [
    {"pid": 1, "stage": "uplink", "link_id": "gsl:uplink:0:a",
     "start": 0.0, "end": 0.1, "rate_bps": 1000.0, "capacity_bits": 100.0,
     "served_bits": 100, "bits": 100, "outcome": "ok"},
    {"pid": 1, "stage": "isl", "link_id": "isl:0:1",
     "start": 0.36, "end": 0.86, "rate_bps": 200.0, "capacity_bits": 100.0,
     "served_bits": 100, "bits": 100, "outcome": "ok"},
]
PROP_EVENTS = [
    {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
    {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue": "uplink",
     "link_id": "gsl:uplink:pending:a", "queue_id": 0},
    {"kind": "service_start", "pid": 1, "at": 0.0, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "queue_id": 0, "bits": 100,
     "rate_bps": 1000.0},
    {"kind": "propagation_start", "pid": 1, "at": 0.1, "stage": "uplink",
     "link_id": "gsl:uplink:0:a", "prop_id": 0, "delay_s": 0.5},
    {"kind": "propagation_start", "pid": 1, "at": 0.6, "stage": "isl",
     "link_id": "isl:0:1", "prop_id": 1, "delay_s": 0.05},
    {"kind": "propagation_start", "pid": 1, "at": 0.65, "stage": "downlink",
     "link_id": "gsl:downlink:1:b", "prop_id": 2, "delay_s": 0.5},
    # the arrival rows carry prop_id only: no stage, no link_id
    {"kind": "propagation_arrival", "pid": 1, "at": 1.15, "prop_id": 2},
    {"kind": "propagation_arrival", "pid": 1, "at": 0.6, "prop_id": 0},
    {"kind": "propagation_arrival", "pid": 1, "at": 0.65, "prop_id": 1},
    {"kind": "delivered", "pid": 1, "at": 1.15},
    # a second packet interleaves its own propagation events in the stream
    {"kind": "packet_emitted", "pid": 2, "at": 0.0, "bits": 100},
    {"kind": "queue_enter", "pid": 2, "at": 0.0, "queue": "isl",
     "link_id": "isl:0:1", "queue_id": 5},
    {"kind": "service_start", "pid": 2, "at": 0.0, "stage": "isl",
     "link_id": "isl:0:1", "queue_id": 5, "bits": 100, "rate_bps": 1000.0},
    {"kind": "propagation_start", "pid": 2, "at": 0.2, "stage": "isl",
     "link_id": "isl:0:1", "prop_id": 0, "delay_s": 0.1},
    {"kind": "propagation_arrival", "pid": 2, "at": 0.3, "prop_id": 0},
    {"kind": "delivered", "pid": 2, "at": 0.3},
]
PROP_WINDOWS = [
    {"pid": 1, "stage": "uplink", "link_id": "gsl:uplink:0:a",
     "start": 0.0, "end": 0.1, "rate_bps": 1000.0, "capacity_bits": 100.0,
     "served_bits": 100, "bits": 100, "outcome": "ok"},
    {"pid": 2, "stage": "isl", "link_id": "isl:0:1",
     "start": 0.0, "end": 0.1, "rate_bps": 1000.0, "capacity_bits": 100.0,
     "served_bits": 100, "bits": 100, "outcome": "ok"},
]


def test_analytic_holding_wait_pairs_with_the_next_admission():
    """Holding has no service_start of its own: it is paired with the next
    admission of the same packet, and a packet still held at the horizon is
    not credited with a fabricated exit time."""
    delivered = indep.decompose_packet_delay(HOLDING_EVENTS, HOLDING_WINDOWS, 1)
    assert delivered["queue_wait_s"] == pytest.approx(0.0)
    assert delivered["holding_wait_s"] == pytest.approx(1.89)
    assert delivered["tx_s"] == pytest.approx(0.6)
    assert delivered["prop_s"] == pytest.approx(0.03)
    assert delivered["gaps"] == []
    assert delivered["e2e_s"] == pytest.approx(2.52)
    assert abs(delivered["residual_s"]) <= 1e-12
    assert len(delivered["holding_waits"]) == 1
    hold = delivered["holding_waits"][0]
    assert hold["queue_id"] == 1
    assert hold["entered_at"] == pytest.approx(0.11)
    assert hold["exit_at"] == pytest.approx(2.0)
    assert hold["next_queue"] == "isl"
    assert hold["seconds"] == pytest.approx(1.89)

    still_held = indep.decompose_packet_delay(HOLDING_EVENTS, HOLDING_WINDOWS, 2)
    assert still_held["holding_waits"] == []
    assert still_held["holding_wait_s"] == 0.0
    assert still_held["delivered_at"] is None

    production = metrics.summarize(HOLDING_EVENTS, HOLDING_WINDOWS)
    assert production["packets"]["1"]["holding_wait_s"] == pytest.approx(1.89)
    assert production["packets"]["2"]["holding_wait_s"] == 0.0


def test_analytic_gap_is_named_decision_compute_and_closes_the_identity():
    """A deferred decision consumes simulated time that no window covers."""
    decomposition = indep.decompose_packet_delay(GAP_EVENTS, GAP_WINDOWS, 1)
    gaps = decomposition["gaps"]
    assert len(gaps) == 1
    start, end, seconds = gaps[0]
    assert start == pytest.approx(0.11)
    assert end == pytest.approx(0.36)
    assert seconds == pytest.approx(0.25)
    assert decomposition["decision_compute_s"] == pytest.approx(0.25)
    assert decomposition["max_uncovered_interval_s"] == pytest.approx(0.25)
    assert decomposition["queue_wait_s"] == 0.0
    assert decomposition["holding_wait_s"] == 0.0
    assert decomposition["tx_s"] == pytest.approx(0.6)
    assert decomposition["prop_s"] == pytest.approx(0.03)
    assert decomposition["e2e_s"] == pytest.approx(0.88)
    assert decomposition["residual_s"] == pytest.approx(0.0, abs=1e-12)
    phases = (decomposition["queue_wait_s"] + decomposition["holding_wait_s"]
              + decomposition["tx_s"] + decomposition["prop_s"])
    assert phases == pytest.approx(0.63)
    assert phases + decomposition["decision_compute_s"] == pytest.approx(
        decomposition["e2e_s"], abs=1e-12)
    # the named phases alone do not close the delay: the uncovered interval is
    # exactly the difference, so the gate is not a tautology
    assert abs(phases - decomposition["e2e_s"]) > 0.2
    report = indep.verify_delay_decomposition(GAP_EVENTS, GAP_WINDOWS, [1])
    assert report["ok"] is True
    assert report["checked_packets"] == 1
    assert report["total_decision_compute_s"] == pytest.approx(0.25)
    assert report["max_abs_residual_s"] <= 1e-9


def test_analytic_undelivered_packet_stays_out_of_the_e2e_statistics():
    undelivered = indep.decompose_packet_delay(HOLDING_EVENTS, HOLDING_WINDOWS, 2)
    assert undelivered["delivered"] is False
    assert undelivered["e2e_s"] is None
    assert undelivered["gaps"] is None
    assert undelivered["decision_compute_s"] is None
    assert undelivered["residual_s"] is None
    assert undelivered["tx_s"] == pytest.approx(0.1)

    only_one = indep.verify_delay_decomposition(
        HOLDING_EVENTS, HOLDING_WINDOWS, [1])
    assert only_one["ok"] is True
    assert only_one["delivered_pids"] == [1]
    assert list(only_one["packets"]) == ["1"]

    as_result = indep.verify_delay_decomposition(
        HOLDING_EVENTS, HOLDING_WINDOWS,
        {"deliveries": {1: {"delivered_at": 2.52}}})
    assert as_result["ok"] is True
    assert as_result["checked_packets"] == 1
    assert "2" not in as_result["packets"]

    claimed = indep.verify_delay_decomposition(
        HOLDING_EVENTS, HOLDING_WINDOWS, [1, 2])
    assert claimed["ok"] is False
    assert any("no delivered event" in error for error in claimed["errors"])
    assert "2" not in claimed["packets"]

    production = metrics.summarize(HOLDING_EVENTS, HOLDING_WINDOWS)
    assert "e2e_s" in production["packets"]["1"]
    assert "e2e_s" not in production["packets"]["2"]


def test_analytic_propagation_arrival_is_resolved_through_prop_id():
    """The arrival row has no stage and no link_id, so the hop it closes can
    only be found through prop_id."""
    arrivals = [event for event in PROP_EVENTS
                if event["kind"] == "propagation_arrival"]
    assert arrivals
    for event in arrivals:
        assert set(event) == {"kind", "pid", "at", "prop_id"}

    decomposition = indep.decompose_packet_delay(PROP_EVENTS, PROP_WINDOWS, 1)
    hops = decomposition["propagation_hops"]
    assert [hop["prop_id"] for hop in hops] == [0, 1, 2]
    assert [hop["stage"] for hop in hops] == ["uplink", "isl", "downlink"]
    assert [hop["link_id"] for hop in hops] == [
        "gsl:uplink:0:a", "isl:0:1", "gsl:downlink:1:b"]
    assert [hop["end"] for hop in hops] == pytest.approx([0.6, 0.65, 1.15])
    assert [hop["seconds"] for hop in hops] == pytest.approx([0.5, 0.05, 0.5])
    assert decomposition["prop_s"] == pytest.approx(1.05)
    assert decomposition["gaps"] == []
    assert abs(decomposition["residual_s"]) <= 1e-12

    production = metrics.summarize(PROP_EVENTS, PROP_WINDOWS)
    assert production["packets"]["1"]["prop_s"] == pytest.approx(1.05)

    truncated = [event for event in PROP_EVENTS
                 if not (event["kind"] == "propagation_arrival"
                         and event["pid"] == 1 and event["prop_id"] == 1)]
    with pytest.raises(indep.IndependentMetricsError,
                       match="unmatched propagation_start"):
        indep.decompose_packet_delay(truncated, PROP_WINDOWS, 1)
    with pytest.raises(metrics.MetricsError,
                       match="unmatched propagation starts"):
        metrics.summarize(truncated, PROP_WINDOWS)


# ---------------------------------------------------- real kernel end-to-end
def test_kernel_one_satellite_cd0_matches_production_field_by_field():
    result, sink = _run(1, 3, 0.0, 1.0)
    assert len(result["deliveries"]) == 3
    assert result["link_available_windows"]
    report = _utilization(result)
    assert _link_mismatches(report, result["congestion_metrics"]["links"]) == []

    idle = {link_id: item for link_id, item in report.items()
            if item["served_bits"] == 0.0}
    assert idle
    for link_id, item in idle.items():
        assert item["status"] == indep.STATUS_OK
        assert item["capacity_bits"] == 0.0
        assert item["available_capacity_bits"] > 0.0
        assert item["utilization"] == 0.0
        assert link_id in result["congestion_metrics"]["links"]

    closure = _report(result)
    assert closure["ok"] is True
    assert closure["checked_packets"] == 3
    assert closure["max_abs_residual_s"] <= 1e-9
    for pid in sorted(result["deliveries"]):
        decomposition = closure["packets"][str(pid)]
        assert decomposition["gaps"] == []
        assert decomposition["decision_compute_s"] == 0.0
        assert decomposition["max_uncovered_interval_s"] == 0.0
        assert decomposition["delivered_at"] == pytest.approx(
            result["deliveries"][pid]["delivered_at"])
        assert decomposition["e2e_s"] == pytest.approx(
            decomposition["delivered_at"] - decomposition["emitted_at"])
    assert _deferred_decisions(sink) == 0

    # the kernel result mapping alone can supply pids and both streams
    from_result = indep.verify_delay_decomposition(None, None, result)
    assert from_result["ok"] is True
    assert from_result["delivered_pids"] == sorted(result["deliveries"])


def test_kernel_ring_cd005_gaps_equal_deferred_decisions_times_delay():
    delay = 0.05
    result, sink = _run(6, 3, delay, 1.0)
    assert len(result["deliveries"]) == 3
    report = _utilization(result)
    assert _link_mismatches(report, result["congestion_metrics"]["links"]) == []

    closure = _report(result)
    assert closure["ok"] is True
    assert closure["max_abs_residual_s"] <= 1e-9
    deferred = _deferred_decisions(sink)
    assert deferred > 0
    assert closure["total_decision_compute_s"] == pytest.approx(
        deferred * delay, rel=1e-9)

    per_packet = {}
    for row_ in sink:
        if float(row_["t"]) > float(row_["t_decision_start"]):
            per_packet[row_["pid"]] = per_packet.get(row_["pid"], 0) + 1
    for pid in sorted(result["deliveries"]):
        decomposition = closure["packets"][str(pid)]
        gaps = decomposition["gaps"]
        assert gaps
        assert decomposition["decision_compute_s"] == pytest.approx(
            len(gaps) * delay, rel=1e-9)
        assert decomposition["max_uncovered_interval_s"] == pytest.approx(delay)
        for _start, _end, seconds in gaps:
            assert seconds == pytest.approx(delay)
        # one deferred decision per hop after the first, counted two ways:
        # from the decision ledger and from the service windows crossed
        assert len(gaps) == per_packet[pid]
        assert len(gaps) == decomposition["service_windows"] - 1
        assert len(decomposition["propagation_hops"]) == len(gaps) + 1
        phases = (decomposition["queue_wait_s"]
                  + decomposition["holding_wait_s"]
                  + decomposition["tx_s"] + decomposition["prop_s"])
        assert phases + decomposition["decision_compute_s"] == pytest.approx(
            decomposition["e2e_s"], abs=1e-9)
        # the identity would be false without the uncovered term
        assert decomposition["e2e_s"] - phases == pytest.approx(
            decomposition["decision_compute_s"], abs=1e-9)


def test_kernel_ring_cd05_scales_the_uncovered_time_with_the_delay():
    result, sink = _run(6, 3, 0.5, 1.0)
    closure = _report(result)
    assert closure["ok"] is True
    assert _link_mismatches(_utilization(result),
                            result["congestion_metrics"]["links"]) == []
    deferred = _deferred_decisions(sink)
    assert closure["total_decision_compute_s"] == pytest.approx(
        deferred * 0.5, rel=1e-9)
    for pid in sorted(result["deliveries"]):
        decomposition = closure["packets"][str(pid)]
        assert decomposition["max_uncovered_interval_s"] == pytest.approx(0.5)
        assert decomposition["decision_compute_s"] == pytest.approx(2.0)
        assert len(decomposition["gaps"]) == 4
        assert decomposition["e2e_s"] == pytest.approx(
            result["congestion_metrics"]["packets"][str(pid)]["e2e_s"])


def test_kernel_four_satellite_ring_idle_links_enter_the_denominator():
    result, sink = _run(4, 3, 0.0, 1.0)
    report = _utilization(result)
    assert _link_mismatches(report, result["congestion_metrics"]["links"]) == []
    served = {link_id for link_id, item in report.items()
              if item["served_bits"] > 0.0}
    idle = {link_id for link_id, item in report.items()
            if item["served_bits"] == 0.0}
    assert len(served) == 4
    assert len(idle) == len(report) - len(served)
    assert len(idle) > len(served)
    for link_id in idle:
        item = report[link_id]
        assert item["status"] == indep.STATUS_OK
        assert item["service_windows"] == 0
        assert item["capacity_bits"] == 0.0
        assert item["available_capacity_bits"] > 0.0
        assert item["utilization"] == 0.0
    for item in report.values():
        assert item["served_bits"] <= item["available_capacity_bits"] * (1 + 1e-9)
    closure = _report(result)
    assert closure["ok"] is True
    assert closure["max_abs_residual_s"] <= 1e-9
    assert _deferred_decisions(sink) == 0
    for pid in sorted(result["deliveries"]):
        assert closure["packets"][str(pid)]["gaps"] == []


def test_kernel_degenerate_availability_is_a_status_not_a_utilization_of_one():
    result, sink = _run(6, 3, 0.5, None)
    assert result["link_available_windows"] == []
    report = _utilization(result)
    reference = result["congestion_metrics"]["links"]
    assert report
    assert set(report) == set(reference)
    for link_id, item in report.items():
        assert item["status"] == indep.STATUS_DEGENERATE
        assert item["utilization"] is None
        assert item["available_capacity_bits"] is None
        assert item["available_samples"] == 0
        assert item["served_bits"] > 0.0
        assert item["served_bits"] == pytest.approx(
            reference[link_id]["served_bits"])
        assert item["capacity_bits"] == pytest.approx(
            reference[link_id]["capacity_bits"])
        assert item["fallback_utilization"] == pytest.approx(1.0)
        # the production reading the fallback reproduces, and the fabricated
        # denominator it uses to get there
        assert reference[link_id]["utilization"] == pytest.approx(1.0)
        assert reference[link_id]["available_capacity_bits"] == pytest.approx(
            item["capacity_bits"])
    assert _link_mismatches(report, reference) == []

    # the delay decomposition never reads the denominator, so it still closes
    closure = _report(result)
    assert closure["ok"] is True
    assert closure["max_abs_residual_s"] <= 1e-9
    deferred = _deferred_decisions(sink)
    assert closure["total_decision_compute_s"] == pytest.approx(
        deferred * 0.5, rel=1e-9)
    for pid in sorted(result["deliveries"]):
        decomposition = closure["packets"][str(pid)]
        assert decomposition["decision_compute_s"] == pytest.approx(2.0)
        assert decomposition["e2e_s"] == pytest.approx(
            result["congestion_metrics"]["packets"][str(pid)]["e2e_s"])


def test_kernel_undelivered_packet_is_excluded_from_the_e2e_report():
    result, sink = _run(1, 2, 0.0, 1.0, duration=2.0, emit_times=[0.0, 1.9])
    assert sorted(result["deliveries"]) == [1]
    assert result["fate_counts"]["IN_SYSTEM_AT_STOP"] == 1

    undelivered = indep.decompose_packet_delay(
        result["packet_events"], result["link_service_windows"], 2)
    assert undelivered["delivered"] is False
    assert undelivered["e2e_s"] is None
    assert undelivered["residual_s"] is None
    assert undelivered["tx_s"] > 0.0

    report = _utilization(result)
    assert _link_mismatches(report, result["congestion_metrics"]["links"]) == []
    for item in report.values():
        assert item["served_bits"] <= item["available_capacity_bits"] * (1 + 1e-9)

    closure = _report(result)
    assert closure["ok"] is True
    assert closure["delivered_pids"] == [1]
    assert list(closure["packets"]) == ["1"]
    production = result["congestion_metrics"]["packets"]
    assert "e2e_s" in production["1"]
    assert "e2e_s" not in production["2"]
    assert _deferred_decisions(sink) == 0

    # claiming the undelivered packet as delivered is an error, never a silent
    # end-to-end statistic
    claimed = indep.verify_delay_decomposition(
        result["packet_events"], result["link_service_windows"], [1, 2])
    assert claimed["ok"] is False
    assert any("no delivered event" in error for error in claimed["errors"])
    assert list(claimed["packets"]) == ["1"]


# --------------------------------------------------------- negative controls
def test_negative_tampered_capacity_bits_is_rejected():
    result, _ = _run(1, 1, 0.0, 1.0)
    events = result["packet_events"]
    windows = [dict(window) for window in result["link_service_windows"]]
    available = [dict(window) for window in result["link_available_windows"]]

    windows[0]["capacity_bits"] += 1.0
    with pytest.raises(indep.IndependentMetricsError,
                       match="does not equal rate_bps"):
        indep.recompute_link_utilization(events, windows, available)
    with pytest.raises(indep.IndependentMetricsError,
                       match="does not equal rate_bps"):
        indep.verify_delay_decomposition(events, windows, result)

    available[0]["capacity_bits"] += 1.0
    with pytest.raises(indep.IndependentMetricsError,
                       match="does not equal rate_bps"):
        indep.recompute_link_utilization(
            events, result["link_service_windows"], available)

    # the untouched record still passes, so the rejection is caused by the
    # tampered digit and not by the fixture
    assert _utilization(result)
    assert _report(result)["ok"] is True


def test_negative_deleting_an_available_window_breaks_the_sum_invariant():
    """One deletion must be able to invert sum(served) <= sum(available).

    The analytic fixture is required: in a real run the 1 s sampling interval
    builds a denominator several times the serviced bits, so a single deletion
    is a legitimate smaller denominator instead of a violation -- which is
    asserted below so the sensitivity limit is on the record.
    """
    available = [
        {"stage": "isl", "link_id": "isl:0:1", "start": 0.5, "end": 1.0,
         "rate_bps": 120.0, "capacity_bits": 60.0},
        {"stage": "isl", "link_id": "isl:0:1", "start": 1.0, "end": 1.5,
         "rate_bps": 100.0, "capacity_bits": 50.0},
    ]
    full = indep.recompute_link_utilization(
        ONE_HOP_EVENTS, ONE_HOP_WINDOWS, available)
    assert full["isl:0:1"]["available_capacity_bits"] == pytest.approx(110.0)
    assert full["isl:0:1"]["utilization"] == pytest.approx(100.0 / 110.0)
    with pytest.raises(indep.IndependentMetricsError,
                       match="exceed sampled available capacity"):
        indep.recompute_link_utilization(
            ONE_HOP_EVENTS, ONE_HOP_WINDOWS, available[:1])

    result, _ = _run(6, 3, 0.0, 1.0)
    report = _utilization(result)
    served_link = next(link_id for link_id, item in report.items()
                       if item["served_bits"] > 0.0 and item["stage"] == "isl")
    spans = [window for window in result["link_available_windows"]
             if window["link_id"] == served_link]
    assert len(spans) > 1
    reduced = [window for window in result["link_available_windows"]
               if window is not spans[0]]
    after = indep.recompute_link_utilization(
        result["packet_events"], result["link_service_windows"], reduced)
    assert after[served_link]["available_capacity_bits"] < \
        report[served_link]["available_capacity_bits"]
    assert after[served_link]["utilization"] > report[served_link]["utilization"]


def test_negative_served_link_without_denominator_coverage_is_rejected():
    result, _ = _run(6, 3, 0.0, 1.0)
    report = _utilization(result)
    served_link = next(link_id for link_id, item in report.items()
                       if item["served_bits"] > 0.0 and item["stage"] == "isl")
    without = [window for window in result["link_available_windows"]
               if window["link_id"] != served_link]
    assert without
    with pytest.raises(indep.IndependentMetricsError,
                       match="no available-capacity coverage"):
        indep.recompute_link_utilization(
            result["packet_events"], result["link_service_windows"], without)

    # an empty stream is the degenerate case, not a violation: the whole point
    # of the explicit status is to keep the two apart
    degenerate = indep.recompute_link_utilization(
        result["packet_events"], result["link_service_windows"], [])
    assert degenerate[served_link]["status"] == indep.STATUS_DEGENERATE
    assert degenerate[served_link]["utilization"] is None


def test_negative_overlapping_windows_and_overlapping_phases_are_rejected():
    result, _ = _run(6, 3, 0.0, 1.0)
    available = [dict(window) for window in result["link_available_windows"]]
    original = available[0]
    span = original["end"] - original["start"]
    clone = dict(original)
    clone["start"] = original["start"] + span / 4.0
    clone["end"] = original["end"] + span / 4.0
    clone["capacity_bits"] = clone["rate_bps"] * (clone["end"] - clone["start"])
    available.append(clone)
    with pytest.raises(indep.IndependentMetricsError,
                       match="overlapping available capacity windows"):
        indep.recompute_link_utilization(
            result["packet_events"], result["link_service_windows"], available)

    # the production implementation only rejects an exact duplicate key and
    # silently inflates the denominator of the overlapped link
    production = metrics.summarize(
        result["packet_events"], result["link_service_windows"],
        available_capacity_windows=available)
    reference = result["congestion_metrics"]["links"][original["link_id"]]
    inflated = production["links"][original["link_id"]]
    assert inflated["available_capacity_bits"] > reference["available_capacity_bits"]
    assert inflated["utilization"] < reference["utilization"]

    # a packet charged twice for the same wall-clock time cannot close: the
    # window starts before the service start, so it overlaps the queue wait
    start, end = 0.2, 0.75
    rate = 100.0 / (end - start)
    overlapping = dict(ONE_HOP_WINDOWS[0])
    overlapping["start"] = start
    overlapping["end"] = end
    overlapping["rate_bps"] = rate
    overlapping["capacity_bits"] = rate * (end - start)
    report = indep.verify_delay_decomposition(
        ONE_HOP_EVENTS, [overlapping], [1])
    assert report["ok"] is False
    assert any("overlap" in error for error in report["errors"])
    assert any("residual" in error for error in report["errors"])
    spans_found = report["packets"]["1"]["overlaps"]
    assert spans_found
    assert spans_found[0]["overlap_s"] == pytest.approx(0.3)
    assert report["packets"]["1"]["residual_s"] == pytest.approx(-0.3)

    # the untouched window closes, and the production summarizer accepts the
    # double charge without a word
    assert indep.verify_delay_decomposition(
        ONE_HOP_EVENTS, ONE_HOP_WINDOWS, [1])["ok"] is True
    assert metrics.summarize(
        ONE_HOP_EVENTS, [overlapping])["validation"]["ok"] is True


def test_the_persisted_ledger_mapping_is_accepted_after_a_json_round_trip():
    """A JSON round-trip must not blind the second implementation.

    Mapping keys become strings in JSON, so the PERSISTED ledger presents its
    delivery set as {"1": ...} while the event pids are ints.  Before the
    coercion this function declared every packet "declared delivered but has
    no delivered event" and checked NOTHING: it could not read the artifact
    format it exists to re-check, while still returning ok=False rather than
    crashing -- so the failure looked like a data problem, not a reader bug.
    """
    result = {
        "packet_events": ONE_HOP_EVENTS,
        "link_service_windows": ONE_HOP_WINDOWS,
        "deliveries": {1: {"delivered_at": 1.0}},
    }
    live = indep.verify_delay_decomposition(None, None, dict(result))
    assert live["ok"] is True, live["errors"]
    assert live["checked_packets"] == 1
    assert live["delivered_pids"] == [1]

    persisted = json.loads(json.dumps(result))
    assert set(persisted["deliveries"]) == {"1"}, \
        "the premise: JSON object keys are strings"
    restored = indep.verify_delay_decomposition(None, None, persisted)
    assert restored["ok"] is True, restored["errors"]
    assert restored["checked_packets"] == 1
    assert restored["delivered_pids"] == [1]

    # an explicit pid collection is coerced the same way
    explicit = indep.verify_delay_decomposition(
        ONE_HOP_EVENTS, ONE_HOP_WINDOWS, ["1"])
    assert explicit["ok"] is True and explicit["checked_packets"] == 1

    # ...and a NON-canonical key fails loud instead of silently matching
    # nothing (the failure mode this test exists to prevent)
    for bad in ("01", "1.0", " 1", "x", ""):
        broken = json.loads(json.dumps(result))
        broken["deliveries"] = {bad: {"delivered_at": 1.0}}
        with pytest.raises(indep.IndependentMetricsError):
            indep.verify_delay_decomposition(None, None, broken)
