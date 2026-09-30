"""A4: FINAL outcome metrics and TOTAL compute cost for one execution mode.

WHY THIS MODULE EXISTS
=====================
The unified report (CODE/experiment_platform/execution_compare.py) compared five
execution modes on "what did the mechanism do" (requests, hits, installs) but
not on "what did the network finally deliver, and what did it cost to get
there".  A4 closes that gap, and the closure has five hard parts that are
contracts, not preferences:

1. TWO DENOMINATORS, NEVER MIXED.  Throughput is reported twice: once as
   delivered bits inside the measurement window over the fixed window seconds
   (a rate), and once as the final delivery ratio of the packet population
   GENERATED inside the window (a probability).  They answer different
   questions and are labelled by denominator_kind so no reader can add, average
   or substitute one for the other.

2. CENSORING IS NOT LOSS.  A packet still queued when the simulation stops, or
   one the drain window administratively cut off, has no proven fate.  It is
   reported under censoring with its own fate list and is NEVER added to
   terminal loss.  Warm-up and drain packets are counted separately so a
   measurement window can be stated exactly.

3. A MISSING FIELD IS NOT_COMPUTABLE.  Every quantity that needs a raw field
   the run did not produce is reported with status NOT_COMPUTABLE and the
   missing field named.  Zero is never substituted, because a zero and an
   absence are different scientific claims, and the row is never dropped.

4. THE TOTAL COMPUTE COST INCLUDES BACKGROUND JOBS.  Per-packet decision
   compute and asynchronous background updates both occupy the same
   per-satellite compute pool, so the total is their sum; they are also
   reported apart, with job counts, queue wait and service seconds, because
   "the mode got cheaper" is only meaningful with the split visible.
   compute_servers_per_satellite=0 (unbounded) still has request, start and
   finish events: no queueing is not no requests.

5. THE HOTSPOT IS A MAXIMUM, NOT A MEAN.  Per-satellite request rates are
   published as a distribution with the hotspot maximum (and its satellite)
   beside the all-satellite mean.  A single overloaded satellite must never be
   hidden by averaging it away, and re-decisions count as requests.

REUSE, DO NOT REIMPLEMENT
=========================
This module is a pure aggregation layer over raw records.  Where a quantity
already has an authoritative implementation it is CALLED, not re-derived, and
the source is cited at the call site:

* CODE/leo_sim/metrics.py::summarize -- offered/admitted/delivered counts and
  bits, per-packet emitted/delivered instants (access_boundary=True).
* CODE/leo_sim/metrics_independent.py::recompute_link_utilization -- the
  service-window capacity ledger this module reads for control occupancy.
* CODE/leo_sim/decision_ledger.py::score_downstream_predictions and
  ::score_start_estimates -- resource (egress) match/mismatch and the
  same-resource bits error; the two scorers are kept apart exactly as the
  module documents (one uses hindsight truth, the other the t0-legal estimate).
* CODE/experiment_platform/primary_metrics.py -- read (not imported) for its
  vocabulary rule: a metric name is a checkable claim.  The A4 names below are
  deliberately outside SUPPORTED_PRIMARY_METRICS, which is the matrix
  analyzer's vocabulary; they are published with an explicit units/status/
  missing_field contract instead, and none of them is a link-utilization ratio
  (the subset primary_metrics refuses without a capacity interval).
* CODE/experiment_platform/t1_stats.py::_percentile -- linear-interpolation
  percentiles, the same definition the T1 protocol fixes for E2E p95.
* CODE/experiment_platform/time_alignment_compare.py -- the resource-timeline
  field names (queue_enter / service_start / service_finish) this module reads.

LAYOUT OF THE PUBLISHED DOCUMENT
================================
{"schema", "context", "stop_rule", "network_outcome", "total_cost",
 "not_computable", "partition_exact", "e2e_stage_disclaimer", "rows"}.
"rows" is the long-form table (one row per metric) that write_long_form_csv
writes, so every figure can be regenerated from the tables alone.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics

from CODE.experiment_platform import t1_stats
from CODE.leo_sim import decision_ledger
from CODE.leo_sim import metrics as metrics_mod

SCHEMA = "execution-outcome/v1"

#: Status used for every quantity whose raw field the run did not produce.
#: A NOT_COMPUTABLE row still exists and still names what was missing.
NOT_COMPUTABLE = "NOT_COMPUTABLE"

#: Fate ledger values that END a packet's life without delivery.  Only these
#: count as terminal loss; everything else (IN_SYSTEM_AT_STOP, and an
#: interrupt) is censoring, because the run stopped before the fate settled.
#: Derived from the fate ledger vocabulary actually emitted by
#: CODE/leo_sim/kernel.py (Ledger.fate) and the AccessRejected path.
TERMINAL_LOSS_FATES = (
    "ACCESS_REJECTED",
    "ACCESS_QUEUE_OVERFLOW",
    "HOLDING_QUEUE_OVERFLOW",
    "ISL_QUEUE_OVERFLOW",
    "NO_ROUTE",
    "DATA_DEADLINE_EXPIRED",
    "GEOMETRY_LOSS_IN_FLIGHT",
    "RANDOM_OUTAGE_IN_FLIGHT",
)

#: The subset of terminal loss that is a DEADLINE outcome: "expired and
#: undelivered" is a named A4 requirement, not a synonym for all loss.
EXPIRED_FATES = ("DATA_DEADLINE_EXPIRED",)

#: Fates that are administrative censoring, never loss.
CENSORING_FATES = ("IN_SYSTEM_AT_STOP",)

SECONDS = "seconds"
BITS = "bits"
COUNTS = "counts"

#: The two throughput denominators, labelled.  They are compared side by side
#: by a test so they can never be silently collapsed into one number.
DENOMINATOR_FIXED_WINDOW = "fixed_window_seconds"
DENOMINATOR_WINDOW_POPULATION = "window_generated_packets"

#: The compute-cost split.  Both classes draw on the SAME per-satellite pool.
COMPUTE_PACKET_DECISIONS = "packet_decisions"
COMPUTE_BACKGROUND_UPDATES = "background_updates"

E2E_STAGE_DISCLAIMER = (
    "query service, install and control traffic are reported as SEPARATE "
    "lines: their durations belong to different, partly concurrent stages and "
    "are never summed into a single end-to-end number.  The only E2E figure "
    "here is measured per delivered packet, emitted_at -> delivered_at."
)

#: Column contract of the long-form CSV.  Every row carries the cell and run
#: id so a figure can be traced back to the run that produced it.
CSV_COLUMNS = (
    "cell", "run_id", "mode", "config_sha256", "block", "metric", "value",
    "status", "units", "missing_field", "source", "note", "window_start_s",
    "window_end_s",
)


class OutcomeMetricsError(ValueError):
    """The raw records or the requested context cannot produce a report."""


# --------------------------------------------------------------------- utils
def _num(value, label, default=None):
    """Best-effort numeric read: an absent/non-numeric value returns default."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    value = float(value)
    return value if math.isfinite(value) else default


def _value_at(metric_name, value, unit, source, note="", missing_field=None):
    """One COMPUTED row, or NOT_COMPUTABLE when the computed value is None.

    A quantity this module could not compute never becomes 0: it becomes a
    NOT_COMPUTABLE row whose missing_field names the RAW FIELD that was absent
    (explicit when the caller names it, else the source reference).
    """
    if value is None:
        return _missing_at(metric_name, missing_field or source or "field",
                           unit, source, note)
    return (str(metric_name).split(".")[0], metric_name, value, "COMPUTED",
            unit, "", source, note)


def _missing_at(metric_name, field, unit, source, note=""):
    """One NOT_COMPUTABLE row: a named missing field, and value None (never 0)."""
    return (str(metric_name).split(".")[0], metric_name, None, NOT_COMPUTABLE,
            unit, field, source, note)



def _union_seconds(intervals):
    """Total length of the UNION of half-open intervals.

    Used where several resources run at the same instant: summing their
    durations would double count concurrent stages, which is exactly the
    arithmetic the A4 cost rule forbids.
    """
    if not intervals:
        return 0.0
    ordered = sorted((float(a), float(b)) for a, b in intervals if b > a)
    if not ordered:
        return 0.0
    total = 0.0
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start > current_end:
            total += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    total += current_end - current_start
    return float(total)


def _sum_seconds(intervals):
    """Total of the durations themselves (per-resource occupancy add)."""
    return float(sum(max(0.0, float(b) - float(a)) for a, b in intervals))


def _rate_per_window(count, window_s):
    if window_s is None or window_s <= 0.0:
        return None
    return float(count) / float(window_s)


def _safe_rate(numerator, denominator):
    if denominator in (None, 0):
        return None
    return float(numerator) / float(denominator)


def _pid_keyed_records(records, label):
    if not isinstance(records, dict):
        raise OutcomeMetricsError(f"{label} is missing or is not a mapping")
    result = {}
    for raw_pid, record in records.items():
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError) as exc:
            raise OutcomeMetricsError(
                f"{label} has a non-integer packet id {raw_pid!r}") from exc
        if isinstance(raw_pid, bool) or pid < 0:
            raise OutcomeMetricsError(
                f"{label} has an invalid packet id {raw_pid!r}")
        if pid in result:
            raise OutcomeMetricsError(
                f"{label} has duplicate packet id {pid}")
        result[pid] = record
    return result


def _validated_trace_packets(trace_rows):
    if not isinstance(trace_rows, (list, tuple)):
        raise OutcomeMetricsError("trace_rows is missing or is not a sequence")
    packets = {}
    for index, row in enumerate(trace_rows):
        if not isinstance(row, dict):
            raise OutcomeMetricsError(f"trace row {index} is not a mapping")
        pid = row.get("packet_id")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0:
            raise OutcomeMetricsError(
                f"trace row {index}: invalid packet_id {pid!r}")
        if pid in packets:
            raise OutcomeMetricsError(f"duplicate packet_id {pid} in trace_rows")
        emitted = _num(row.get("emit_time_s"), "trace_rows.emit_time_s")
        bits = row.get("bits")
        if emitted is None or emitted < 0:
            raise OutcomeMetricsError(
                f"trace row {index}: emit_time_s is missing or invalid")
        if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
            raise OutcomeMetricsError(
                f"trace row {index}: bits is missing or invalid")
        packets[pid] = {"packet_id": pid, "emit_time_s": emitted,
                        "bits": int(bits)}
    return packets


def _packet_event_bits(result, trace_packets):
    events = result.get("packet_events")
    if not isinstance(events, list):
        return None
    emitted = {}
    for index, event in enumerate(events):
        if event.get("kind") != "packet_emitted":
            continue
        pid, bits = event.get("pid"), event.get("bits")
        if isinstance(pid, bool) or not isinstance(pid, int):
            raise OutcomeMetricsError(
                f"packet_emitted event {index} has invalid pid {pid!r}")
        if pid in emitted:
            raise OutcomeMetricsError(
                f"duplicate packet_emitted event for pid {pid}")
        if pid not in trace_packets:
            raise OutcomeMetricsError(
                f"packet_emitted event has pid {pid} absent from trace_rows")
        if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
            raise OutcomeMetricsError(
                f"packet_emitted event for pid {pid} has invalid bits")
        if int(bits) != trace_packets[pid]["bits"]:
            raise OutcomeMetricsError(
                f"packet_emitted bits differ from trace_rows for pid {pid}")
        emitted[pid] = int(bits)
    return emitted


def _deadline_packet_loss(fate, emitted_at, delivered_at, observation_end,
                          deadline_s):
    base = {"deadline_s": deadline_s, "value": None,
            "lower_bound": None, "upper_bound": None,
            "observed_delay_s": None, "missing_field": None}
    if deadline_s is None:
        return dict(base, status=NOT_COMPUTABLE, missing_field="deadline_s")
    if fate == "DELIVERED":
        if delivered_at is None:
            return dict(base, status=NOT_COMPUTABLE,
                        lower_bound=0.0, upper_bound=1.0,
                        missing_field="deliveries[pid].delivered_at")
        delay = delivered_at - emitted_at
        loss = min(max(delay, 0.0), deadline_s) / deadline_s
        return dict(base, status="COMPUTED", value=loss,
                    lower_bound=loss, upper_bound=loss,
                    observed_delay_s=delay,
                    duration_basis="emitted_at_to_delivered_at")
    if fate in TERMINAL_LOSS_FATES:
        return dict(base, status="COMPUTED", value=1.0,
                    lower_bound=1.0, upper_bound=1.0,
                    duration_basis="terminal_loss")
    if fate in CENSORING_FATES:
        if observation_end is None:
            return dict(base, status=NOT_COMPUTABLE, lower_bound=0.0,
                        upper_bound=1.0,
                        missing_field="result.stop_time_s")
        observed = observation_end - emitted_at
        if observed < 0:
            raise OutcomeMetricsError(
                "observation end precedes packet emission")
        if observed >= deadline_s:
            return dict(base, status="COMPUTED", value=1.0,
                        lower_bound=1.0, upper_bound=1.0,
                        observed_delay_s=observed,
                        duration_basis="censored_after_full_deadline")
        return dict(base, status="INTERVAL_CENSORED", value=None,
                    lower_bound=observed / deadline_s, upper_bound=1.0,
                    observed_delay_s=observed,
                    duration_basis="observed_until_run_stop")
    raise OutcomeMetricsError(f"unrecognised fate {fate!r}")


def _packet_observations(trace_packets, fates, delivery_records,
                         observation_end, deadline_s, window, context):
    trace_ids, fate_ids = set(trace_packets), set(fates)
    missing = sorted(trace_ids - fate_ids)
    extra = sorted(fate_ids - trace_ids)
    if missing or extra:
        raise OutcomeMetricsError(
            f"trace/fate packet set mismatch; missing fate records {missing[:10]}, "
            f"extra fate records {extra[:10]}")
    delivery_ids = set(delivery_records)
    extra_deliveries = sorted(delivery_ids - trace_ids)
    if extra_deliveries:
        raise OutcomeMetricsError(
            f"delivery records have pids absent from trace_rows: "
            f"{extra_deliveries[:10]}")
    window_block = _window_from(window, [
        dict(packet, packet_id=pid)
        for pid, packet in trace_packets.items()])
    stop = _num(observation_end, "result.stop_time_s")
    packet_rows = []
    for pid in sorted(trace_packets):
        trace = trace_packets[pid]
        fate = fates[pid]
        if fate not in set(TERMINAL_LOSS_FATES) | set(CENSORING_FATES) | {"DELIVERED"}:
            raise OutcomeMetricsError(f"unrecognised fate {fate!r} for pid {pid}")
        record_present = pid in delivery_records
        record = delivery_records.get(pid)
        if record_present and not isinstance(record, dict):
            raise OutcomeMetricsError(f"delivery record for pid {pid} is invalid")
        if fate != "DELIVERED" and record_present:
            raise OutcomeMetricsError(
                f"non-delivered pid {pid} has a delivery record")
        delivered_at = None
        if fate == "DELIVERED" and isinstance(record, dict):
            raw_at = record.get("delivered_at")
            if raw_at is not None:
                delivered_at = _num(raw_at, "deliveries[pid].delivered_at")
                if delivered_at is None:
                    raise OutcomeMetricsError(
                        f"delivery time for pid {pid} is non-finite")
                if delivered_at < trace["emit_time_s"]:
                    raise OutcomeMetricsError(
                        f"delivery time precedes emission for pid {pid}")
                if stop is not None and delivered_at > stop:
                    raise OutcomeMetricsError(
                        f"delivery time is after observation end for pid {pid}")
        emitted_at = trace["emit_time_s"]
        in_population = (window_block.get("status") == "COMPUTED"
                         and window_block["start_s"] <= emitted_at
                         <= window_block["end_s"])
        loss = _deadline_packet_loss(
            fate, emitted_at, delivered_at, stop, deadline_s)
        packet_rows.append({
            "pid": pid,
            "trace_sha256": (context.get("trace_sha256")
                             or context.get("trace_rows_digest")),
            "emit_time_s": emitted_at,
            "bits": trace["bits"],
            "observation_end_s": stop,
            "fate": fate,
            "delivery_record_present": record_present,
            "delivery_time_s": delivered_at,
            "terminal_reason": (fate if fate in TERMINAL_LOSS_FATES else None),
            "censor_reason": ("administrative_censoring_at_stop"
                              if fate in CENSORING_FATES else None),
            "in_population": bool(in_population),
            "deadline_loss": loss,
        })
    return packet_rows, window_block


def _deadline_summary(packet_rows, deadline_s):
    population = [row for row in packet_rows if row["in_population"]]
    if deadline_s is None or not population:
        return {"status": NOT_COMPUTABLE, "deadline_s": deadline_s,
                "packets": len(population), "value": None,
                "lower_mean": None, "upper_mean": None,
                "missing_field": ("deadline_s" if deadline_s is None
                                  else "population_window")}
    lower, upper, exact = [], [], 0
    interval_censored = 0
    missing = 0
    for row in population:
        item = row["deadline_loss"]
        if item.get("status") == "COMPUTED":
            value = float(item["value"])
            lower.append(value)
            upper.append(value)
            exact += 1
        else:
            interval_censored += item.get("status") == "INTERVAL_CENSORED"
            missing += item.get("status") == NOT_COMPUTABLE
            lo = item.get("lower_bound")
            hi = item.get("upper_bound")
            lower.append(0.0 if lo is None else float(lo))
            upper.append(1.0 if hi is None else float(hi))
    lo_mean = statistics.fmean(lower)
    hi_mean = statistics.fmean(upper)
    complete = exact == len(population)
    return {"status": "COMPUTED" if complete else "PARTIAL_BOUNDS",
            "deadline_s": deadline_s, "packets": len(population),
            "exact_packets": exact,
            "interval_censored": interval_censored,
            "not_computable_packets": missing,
            "value": lo_mean if complete else None,
            "lower_mean": lo_mean, "upper_mean": hi_mean,
            "missing_field": None if complete else "packet deadline outcome"}


# ------------------------------------------------------- measurement window
def build_measurement_window(trace_rows, *, drain_multiplier=2.0,
                             sample_interval_s=None):
    """Interval [first emission, last emission + drain] for one trace.

    The window is derived from the TRACE ROWS, never from the configured
    duration: the configured value is what was asked for, the rows are what was
    emitted.  The drain tail is drain_multiplier times the median inter-arrival
    (the time the last packet plausibly needs to finish), and is capped by the
    run's natural stop so the window can never claim time the run did not have.

    An empty trace has no window: the result is NOT_COMPUTABLE naming
    trace_rows, and every rate that would have used it inherits the status.
    """
    emissions = sorted(_num(row.get("emit_time_s"), "emit_time_s")
                       for row in (trace_rows or []))
    emissions = [value for value in emissions if value is not None]
    if not emissions:
        return {"status": NOT_COMPUTABLE, "missing_field": "trace_rows",
                "source": "trace_rows.emit_time_s",
                "note": "an empty trace has no measurement window; the window "
                        "is never assumed from the configured duration"}
    start = emissions[0]
    last = emissions[-1]
    gaps = [b - a for a, b in zip(emissions, emissions[1:]) if b > a]
    if sample_interval_s is not None and sample_interval_s > 0:
        interval = float(sample_interval_s)
        unit_source = "explicit_sample_interval_s"
    elif gaps:
        interval = float(statistics.median(gaps))
        unit_source = "median_inter_arrival"
    else:
        interval = 0.0
        unit_source = "single_emission_no_interval"
    tail = max(0.0, float(drain_multiplier) * interval)
    return {
        "status": "COMPUTED",
        "start_s": float(start),
        "end_s": float(last + tail),
        "last_emission_s": float(last),
        "drain_tail_s": float(tail),
        "fixed_window_s": float((last + tail) - start),
        "unit_interval_s": float(interval),
        "unit_source": unit_source,
        "drain_multiplier": float(drain_multiplier),
        "source": "trace_rows.emit_time_s",
        "note": "window = [first emission, last emission + "
                f"{drain_multiplier}x{unit_source}]",
    }


def _window_from(value, trace_rows):
    if value is None:
        return build_measurement_window(trace_rows)
    if isinstance(value, dict):
        window = dict(value)
        if window.get("status") != "COMPUTED":
            window.setdefault("missing_field", "trace_rows")
            return window
        for key in ("start_s", "end_s"):
            if _num(window.get(key), key) is None:
                return {"status": NOT_COMPUTABLE, "missing_field": key,
                        "source": "window argument",
                        "note": f"window.{key} is absent"}
        window.setdefault("fixed_window_s",
                          float(window["end_s"]) - float(window["start_s"]))
        window.setdefault("source", "window argument")
        return window
    if isinstance(value, (tuple, list)) and len(value) == 2:
        start, end = _num(value[0], "start"), _num(value[1], "end")
        if start is None or end is None or end <= start:
            return {"status": NOT_COMPUTABLE, "missing_field": "window_bounds",
                    "source": "window argument",
                    "note": f"invalid window bounds {value!r}"}
        return {"status": "COMPUTED", "start_s": start, "end_s": end,
                "fixed_window_s": end - start, "source": "explicit bounds"}
    raise OutcomeMetricsError(
        f"window must be None, a window dict or (start_s, end_s), got {value!r}")


def _packet_bits(trace_rows):
    """pid -> bits, from the TRACE rows (demand.packet_bits is only a default)."""
    out = {}
    for row in trace_rows or []:
        pid = row.get("packet_id")
        bits = row.get("bits")
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
            continue
        out[pid] = int(bits)
    return out


def _emission_times(trace_rows):
    """pid -> emit_time_s, from the TRACE rows (authoritative offer instant)."""
    out = {}
    for row in trace_rows or []:
        pid = row.get("packet_id")
        at = row.get("emit_time_s")
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        value = _num(at, "emit_time_s")
        if value is not None:
            out[pid] = value
    return out


def _window_membership(window, emissions):
    inside, warmup, drain = [], [], []
    for pid in sorted(emissions):
        at = emissions[pid]
        if at < window["start_s"]:
            warmup.append(pid)
        elif at > window["end_s"]:
            drain.append(pid)
        else:
            inside.append(pid)
    return inside, warmup, drain


def _delivery_instants(result):
    out = {}
    for pid, record in (result.get("deliveries") or {}).items():
        at = _num((record or {}).get("delivered_at"), "delivered_at")
        if at is not None:
            out[int(pid)] = at
    return out


def _fate_of(result, pid):
    fates = result.get("fates")
    if isinstance(fates, dict):
        return fates.get(pid)
    counts = result.get("fate_counts") or {}
    return None if not counts else None


def _emitted_bits(result):
    out = {}
    for event in result.get("packet_events") or []:
        if event.get("kind") != "packet_emitted":
            continue
        pid = event.get("pid")
        bits = event.get("bits")
        if isinstance(pid, bool) or not isinstance(pid, int):
            continue
        if isinstance(bits, bool) or not isinstance(bits, int):
            continue
        out[pid] = int(bits)
    return out


def _admitted_pids(result):
    events = result.get("packet_events")
    if not isinstance(events, list):
        return None
    out = set()
    for event in events:
        if event.get("kind") == "satellite_ingress":
            pid = event.get("pid")
            if isinstance(pid, bool) or not isinstance(pid, int):
                raise OutcomeMetricsError(
                    f"satellite_ingress event has invalid pid {pid!r}")
            if pid in out:
                continue  # a packet may have more than one ingress event
            out.add(pid)
    return out


# ------------------------------------------------------------------ outcome
def _totals(pids, bits, fates):
    """One population summary (counts + bits) over a pid list.

    by_fate keeps only the fates that really occurred: a zero count is an
    absence of that outcome, and padding the classification with zeroes would
    make an unobserved fate look observed-and-empty.
    """
    by_fate = {}
    for pid in pids:
        fate = fates.get(pid)
        if fate is None:
            continue
        by_fate[fate] = by_fate.get(fate, 0) + 1
    return {
        "packets": len(pids),
        "bits": int(sum(bits.get(pid, 0) for pid in pids)),
        "by_fate": {fate: by_fate[fate] for fate in sorted(by_fate)},
        "pids": sorted(pids),
    }


def compare_outcome(result, timeline_rows, decision_rows, trace_rows, *,
                    window=None, deadline_s=None, cost=None, context=None):
    """Publish the A4 outcome + total-cost document for ONE mode row.

    result        the dict returned by CODE.leo_sim.kernel.run_simulation
    timeline_rows the timeline sink passed to the same run
    decision_rows the decision sink passed to the same run
    trace_rows    the trace rows the run was given (real packet sizes live
                  here; demand.packet_bits is only the configured default)
    window        None (derive from trace_rows), a window dict, or
                  (start_s, end_s)
    cost          {"service_s", "servers"} from the resolved config
    deadline_s    one predeclared deadline used for the packet-level primary
                  loss; an absent delivery/observation field remains missing
    context       traceability including the immutable trace digest
    """
    if not isinstance(result, dict):
        raise OutcomeMetricsError("result must be the kernel result dict")
    context = dict(context or {})
    if context.get("mode") is None:
        context["mode"] = (result.get("execution_mode") or {}).get("mode")
    cost = dict(cost or {})

    trace_packets = _validated_trace_packets(trace_rows)
    trace_ids = set(trace_packets)
    bits = {pid: item["bits"] for pid, item in trace_packets.items()}
    emissions = {pid: item["emit_time_s"]
                 for pid, item in trace_packets.items()}
    trace_rows_digest = hashlib.sha256(json.dumps(
        trace_rows, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode("utf-8")).hexdigest()
    context.setdefault("trace_rows_digest", trace_rows_digest)
    if deadline_s is not None:
        deadline_s = _num(deadline_s, "deadline_s")
        if deadline_s is None or deadline_s <= 0:
            raise OutcomeMetricsError("deadline_s must be finite and positive")
    fates = _pid_keyed_records(result.get("fates"), "result.fates")
    fates = {pid: value for pid, value in fates.items()}
    delivery_records = _pid_keyed_records(result.get("deliveries"),
                                          "result.deliveries")
    event_bits = _packet_event_bits(result, trace_packets)
    if event_bits is not None and set(event_bits) != trace_ids:
        raise OutcomeMetricsError(
            "packet_emitted events do not match trace_rows packet ids; "
            f"missing {sorted(trace_ids - set(event_bits))[:10]}, "
            f"extra {sorted(set(event_bits) - trace_ids)[:10]}")
    delivery_times = {}
    for pid, record in delivery_records.items():
        if isinstance(record, dict) and record.get("delivered_at") is not None:
            at = _num(record.get("delivered_at"),
                      "deliveries[pid].delivered_at")
            if at is None:
                raise OutcomeMetricsError(
                    f"delivery time for pid {pid} is non-finite")
            delivery_times[pid] = at
    admitted_pids = _admitted_pids(result)
    if admitted_pids is not None:
        outside = sorted(admitted_pids - trace_ids)
        if outside:
            raise OutcomeMetricsError(
                f"satellite_ingress events have pids absent from trace_rows: "
                f"{outside[:10]}")
    stop_time = _num(result.get("stop_time_s"), "result.stop_time_s")
    packet_outcomes, window_block = _packet_observations(
        trace_packets, fates, delivery_records, stop_time, deadline_s,
        window, context)
    deadline_primary_loss = _deadline_summary(packet_outcomes, deadline_s)
    packet_outcomes_sha256 = hashlib.sha256(json.dumps(
        packet_outcomes, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, default=str).encode("utf-8")).hexdigest()

    # The authoritative counts/bits layer is the kernel's own congestion
    # metrics (CODE/leo_sim/metrics.py::summarize with access_boundary=True);
    # the fates and the delivery instants are read from the fate ledger and the
    # delivery map, which that summarizer does not own.
    congestion = result.get("congestion_metrics") or {}
    if not isinstance(congestion, dict):
        congestion = {}
    rows = []
    stop_rule = {
        "natural_end": bool(result.get("natural_end", True)),
        "interrupted": bool(result.get("interrupted", False)),
        "error": result.get("error"),
        "stop_time_s": stop_time,
        "horizon_s": _num(result.get("horizon_s"), "horizon_s"),
        "events_processed": result.get("events_processed"),
    }

    # -- partition -----------------------------------------------------------
    offered = sorted(trace_ids)
    delivered = sorted(pid for pid in offered
                       if fates.get(pid) == "DELIVERED")
    terminal = sorted(pid for pid in offered
                      if fates.get(pid) in TERMINAL_LOSS_FATES)
    censored = sorted(pid for pid in offered
                      if fates.get(pid) in CENSORING_FATES)
    partition_exact = len(delivered) + len(terminal) + len(censored) == len(offered)
    if not partition_exact:
        raise OutcomeMetricsError("packet fate partition is not exact")

    admitted_metric = congestion.get(
        "admitted_at_satellite_ingress_packets")
    admitted_canonical = (_num(admitted_metric, "canonical admitted count")
                          if admitted_metric is not None else None)
    if (admitted_canonical is not None and admitted_pids is not None
            and admitted_canonical != len(admitted_pids)):
        raise OutcomeMetricsError(
            "satellite_ingress event count differs from canonical congestion "
            "admitted count")
    admitted_count = (len(admitted_pids) if admitted_pids is not None
                      else admitted_canonical)
    admitted_bits = (None if admitted_pids is None
                     else int(sum(bits[pid] for pid in admitted_pids)))
    delivered_bits = int(sum(bits[pid] for pid in delivered))

    counts = {
        "offered": len(offered),
        "admitted": admitted_count,
        "delivered": len(delivered),
        "offered_bits": int(sum(bits[pid] for pid in offered)),
        "admitted_bits": admitted_bits,
        "delivered_bits": delivered_bits,
        "fate_counts": dict(result.get("fate_counts") or {}),
    }
    if congestion:
        counts["congestion_metrics_offered_packets"] = congestion.get(
            "offered_packets")
        counts["congestion_metrics_delivered_packets"] = congestion.get(
            "delivered_packets")
        counts["counts_source"] = ("own aggregate over trace rows + fate "
                                   "ledger; congestion_metrics mirrored")
    else:
        counts["counts_source"] = "own aggregate over trace rows + fate ledger"
    terminal_loss = _totals(terminal, bits, fates)
    terminal_loss["classification"] = dict(terminal_loss["by_fate"])
    terminal_loss["expired_and_undelivered"] = _totals(
        [pid for pid in terminal if fates.get(pid) in EXPIRED_FATES],
        bits, fates)
    censoring = _totals(censored, bits, fates)
    censoring.update({
        "still_in_system_at_stop": censoring["by_fate"].get(
            "IN_SYSTEM_AT_STOP", 0),
        "kind": "administrative_censoring",
        "note": "still in the system when the run stopped: the drain window "
                "ended before this packet's fate settled.  This is NOT loss "
                "and is never added to terminal loss.",
    })
    delivery_ratio = {
        "by_packets": _safe_rate(len(delivered), len(offered)),
        "by_bits": _safe_rate(sum(bits.get(p, 0) for p in delivered),
                              sum(bits.get(p, 0) for p in offered)),
        "numerator": len(delivered),
        "denominator": len(offered),
        "denominator_kind": "offered_packets_all_fates",
    }

    # -- measurement window --------------------------------------------------
    inside, warmup, drain = ([], [], [])
    if window_block.get("status") == "COMPUTED":
        inside, warmup, drain = _window_membership(window_block, emissions)

    # -- the two throughput denominators ------------------------------------
    throughput = {}
    payload = {}
    if window_block.get("status") == "COMPUTED":
        window_s = float(window_block["fixed_window_s"])
        missing_delivery_times = sorted(
            pid for pid in delivered if pid not in delivery_times)
        inside_delivered = ([pid for pid in delivered
                             if window_block["start_s"] <= delivery_times[pid]
                             <= window_block["end_s"]]
                            if not missing_delivery_times else None)
        inside_bits = (int(sum(bits[pid] for pid in inside_delivered))
                       if inside_delivered is not None else None)
        generated = list(inside)
        delivered_of_generated = [pid for pid in generated
                                  if fates[pid] == "DELIVERED"]
        delivered_of_generated_bits = int(
            sum(bits[pid] for pid in delivered_of_generated))
        throughput = {
            "delivered_bits_in_window_over_fixed_window": {
                "value_bps": (None if inside_bits is None
                              else _safe_rate(inside_bits, window_s)),
                "delivered_bits_in_window": inside_bits,
                "window_s": window_s,
                "packets_delivered_in_window": (
                    None if inside_delivered is None
                    else len(inside_delivered)),
                "status": (NOT_COMPUTABLE if inside_delivered is None
                           else "COMPUTED"),
                "missing_field": ("result.deliveries[pid].delivered_at"
                                  if inside_delivered is None else None),
                "denominator_kind": DENOMINATOR_FIXED_WINDOW,
                "numerator_kind": "bits_of_packets_delivered_inside_the_window",
                "definition": "sum(bits of packets whose delivered_at is inside "
                              "the window) / fixed window seconds",
            },
            "final_delivery_ratio_of_window_queue_population": {
                "value": _safe_rate(len(delivered_of_generated), len(generated)),
                "generated_in_window": len(generated),
                "delivered_of_generated": len(delivered_of_generated),
                "delivered_bits_of_generated": delivered_of_generated_bits,
                "by_bits": _safe_rate(
                    delivered_of_generated_bits,
                    sum(bits.get(pid, 0) for pid in generated)),
                "denominator_kind": DENOMINATOR_WINDOW_POPULATION,
                "numerator_kind": "packets_delivered_eventually",
                "definition": "packets generated in the window and finally "
                              "delivered / packets generated in the window "
                              "(NOT the admitted subset)",
            },
            "never_mixed": True,
            "note": "the two entries answer different questions and carry "
                    "different denominator_kind labels; do not add them, "
                    "average them, or substitute one for the other",
        }
        payload = {
            "delivered_bits_in_window": inside_bits,
            "fixed_window_s": window_s,
            "goodput_bps": (None if inside_bits is None
                             else _safe_rate(inside_bits, window_s)),
            "status": (NOT_COMPUTABLE if inside_bits is None else "COMPUTED"),
            "missing_field": ("result.deliveries[pid].delivered_at"
                              if inside_bits is None else None),
            "basis": "bits_delivered_inside_the_window",
            "delivery_ratio": delivery_ratio,
            "all_delivered_bits": counts["delivered_bits"],
            "note": "goodput uses the measurement window; the configured "
                    "demand.packet_bits is only a default and the real sizes "
                    "come from the trace rows",
        }

    # -- per-satellite request rate -----------------------------------------
    window_s = (float(window_block["fixed_window_s"])
                if window_block.get("status") == "COMPUTED" else None)
    if window_block.get("status") == "COMPUTED":
        window_block["offered_in_window"] = len(inside)
        window_block["offered_in_window_bits"] = int(
            sum(bits.get(pid, 0) for pid in inside))
    rate = _rate_block(decision_rows or [], timeline_rows or [], window_s)

    queue = _queue_block(result, timeline_rows or [])
    e2e = _e2e_block(emissions, delivery_times, delivered)
    table = _table_block(decision_rows or [], timeline_rows or [])
    prediction = _prediction_block(decision_rows or [], timeline_rows or [],
                                   emissions)
    cost_block = _cost_block(result, timeline_rows or [], cost)

    network_rows = _network_rows(counts, terminal_loss, censoring,
                                 delivery_ratio, window_block, throughput,
                                 payload, queue, e2e, rate, table, prediction,
                                 warmup, drain, bits, delivery_records,
                                 admitted_count, deadline_primary_loss,
                                 delivered)
    cost_rows = _cost_rows(cost_block)
    all_rows = network_rows + cost_rows

    network_outcome = {
        "window": window_block,
        "counts": counts,
        "terminal_loss": terminal_loss,
        "censoring": censoring,
        "delivery_ratio": delivery_ratio,
        "throughput": throughput,
        "payload": payload,
        "deadline_primary_loss": deadline_primary_loss,
        "packet_outcomes": packet_outcomes,
        "packet_outcomes_sha256": packet_outcomes_sha256,
        "queue": queue,
        "e2e": e2e,
        "rate_per_satellite": rate,
        "table": table,
        "prediction": prediction,
        "warmup": {"packets": len(warmup), "pids": warmup,
                  "bits": int(sum(bits[p] for p in warmup)),
                   "window_start_s": window_block.get("start_s"),
                   "note": "emitted before the measurement window; excluded "
                           "from every window denominator"},
        "drain": {"packets": len(drain), "pids": drain,
                  "bits": int(sum(bits[p] for p in drain)),
            "delivered": sum(1 for p in drain if p in set(delivered)),
                  "window_end_s": window_block.get("end_s"),
                  "note": "emitted after the measurement window closed; kept "
                          "separable so a ceiling is not mistaken for a loss"},
    }
    network_outcome["window"]["offered_in_window"] = len(inside)
    network_outcome["window"]["offered_in_window_bits"] = int(
        sum(bits.get(pid, 0) for pid in inside))

    return {
        "schema": SCHEMA,
        "context": context,
        "units": {"e2e": SECONDS, "queue_area": "bits_s",
                  "compute_service": SECONDS, "compute_queue_wait": SECONDS,
                  "rates": "1/s", "bits": BITS, "counts": COUNTS},
        "stop_rule": stop_rule,
        "network_outcome": network_outcome,
        "total_cost": cost_block,
        "partition_exact": bool(partition_exact),
        "e2e_stage_disclaimer": E2E_STAGE_DISCLAIMER,
        "not_computable": _collect_missing(all_rows),
        "rows": _rows_from_tuples(all_rows, context),
        "row_source": "compare_outcome",
    }


#: A4 metric names that collide with the matrix analyzer's vocabulary would
#: silently change what an existing id means, so the collision is refused here
#: rather than discovered by a reader.
FORBIDDEN_NAME_COLLISIONS = frozenset({
    "link_utilization_mean", "service_window_utilization_mean",
    "isl_link_utilization_mean", "isl_link_utilization_max",
})


def _check_name(metric_name):
    if metric_name in FORBIDDEN_NAME_COLLISIONS:
        raise OutcomeMetricsError(
            f"{metric_name} is a link-utilization ratio whose denominator "
            "needs execution.available_capacity_interval_s; it must not be "
            "redefined here")


def _rows_from_tuples(tuples, context=None):
    """Expand the 8-tuple contract into the published row dicts."""
    out = []
    for (block, metric, value, status, units, missing_field, source,
         note) in tuples:
        _check_name(metric)
        out.append({"block": block, "metric": metric, "value": value,
                    "status": status, "units": units,
                    "missing_field": missing_field, "source": source,
                    "note": note, "cell": context.get("cell"),
                    "run_id": context.get("run_id"),
                    "mode": context.get("mode"),
                    "config_sha256": context.get("config_sha256")})
    return out


def _collect_missing(rows):
    out = {}
    for row in rows:
        if row[3] == NOT_COMPUTABLE:
            out[row[1]] = {"missing_field": row[5] or "field",
                           "source": row[6]}
    return out


# -------------------------------------------------------- per-satellite rate
def _rate_block(decision_rows, timeline_rows, window_s):
    """Request-rate distribution per satellite, INCLUDING re-decisions.

    A re-decision is a request that was really issued (the packet paid for a
    second computation), so it belongs in the pressure count.  The hotspot is
    the MAXIMUM of the per-satellite rates; the all-satellite mean is published
    beside it and is explicitly forbidden from standing in for it.
    """
    per_sat = {}
    redecisions = []
    for row in timeline_rows:
        if row.get("milestone") != "redecision":
            continue
        sat = row.get("sat")
        if isinstance(sat, int) and not isinstance(sat, bool):
            per_sat[sat] = per_sat.get(sat, 0) + 1
            redecisions.append(sat)
    commits = 0
    for row in decision_rows:
        sat = row.get("sat")
        if isinstance(sat, int) and not isinstance(sat, bool):
            per_sat[sat] = per_sat.get(sat, 0) + 1
            commits += 1
    total = commits + len(redecisions)
    by_sat = {str(sat): per_sat[sat] for sat in sorted(per_sat)}
    if window_s is None or window_s <= 0.0:
        return {
            "requests_by_satellite": by_sat,
            "total_requests": total,
            "committed_decisions": commits,
            "redecisions": len(redecisions),
            "redecisions_by_satellite": {
                str(sat): redecisions.count(sat) for sat in sorted(set(redecisions))},
            "satellites_with_requests": len(per_sat),
            "window_s": window_s,
            "hotspot_max_per_s": None,
            "mean_per_s": None,
            "all_satellites_mean_per_s": None,
            "hotspot_satellite": None,
            "hotspot_is_not_the_mean": None,
            "status": NOT_COMPUTABLE,
            "missing_field": "window.fixed_window_s",
            "source": "decision_rows.sat + timeline redecision rows",
            "note": "hotspot pressure is the MAXIMUM per-satellite request "
                    "rate; the all-satellite mean must never stand in for it",
        }
    rates = {sat: count / window_s for sat, count in per_sat.items()}
    hotspot = max(rates, key=lambda sat: (rates[sat], -sat)) if rates else None
    mean = statistics.fmean(rates.values()) if rates else None
    return {
        "requests_by_satellite": by_sat,
        "rates_by_satellite_per_s": {str(sat): rates[sat]
                                     for sat in sorted(rates)},
        "total_requests": total,
        "committed_decisions": commits,
        "redecisions": len(redecisions),
        "redecisions_by_satellite": {
            str(sat): redecisions.count(sat) for sat in sorted(set(redecisions))},
        "satellites_with_requests": len(per_sat),
        "window_s": window_s,
        "hotspot_max_per_s": (None if hotspot is None else rates[hotspot]),
        "hotspot_satellite": hotspot,
        "mean_per_s": mean,
        "all_satellites_mean_per_s": mean,
        "p90_per_s": (t1_stats._percentile(sorted(rates.values()), 0.90)
                      if rates else None),
        "hotspot_is_not_the_mean": (None if hotspot is None
                                    else rates[hotspot] > (mean or 0.0)),
        "status": "COMPUTED",
        "source": "decision_rows.sat + timeline redecision rows",
        "note": "hotspot pressure is the MAXIMUM per-satellite request rate "
                "(including re-decisions); the all-satellite mean is reported "
                "beside it and is never a substitute",
    }


def _queue_block(result, timeline_rows):
    """Queue peak and queue area, from the records that actually own them.

    area: the kernel closes every queue-area integral at the exact stop time
    and publishes result["queue_area_bits_s"]; summing the areas is meaningful
    because each is an integral over its own queue.
    peak: read from the timeline queue_enter rows, whose backlog_before is
    measured before insertion (so a packet never counts itself).
    """
    area_source = "result.queue_area_bits_s (kernel closed at stop_time_s)"
    area = result.get("queue_area_bits_s")
    peak = None
    peak_resource = None
    backlog_samples = []
    if isinstance(area, dict):
        numeric = {key: _num(value, f"queue_area_bits_s.{key}")
                   for key, value in area.items()}
        numeric = {key: value for key, value in numeric.items()
                   if value is not None}
        area_block = {"total": float(sum(numeric.values())),
                      "by_area": numeric,
                      "source": area_source}
    else:
        area_block = {"total": None, "by_area": None, "source": area_source,
                      "status": NOT_COMPUTABLE,
                      "missing_field": "queue_area_bits_s",
                      "note": "the kernel publishes queue_area_bits_s for "
                              "every run; its absence is reported, never "
                              "replaced by 0"}
    for row in timeline_rows:
        if row.get("milestone") != "queue_enter":
            continue
        backlog = row.get("backlog_before")
        if not isinstance(backlog, dict):
            continue
        queued = _num(backlog.get("queued_bits_before"), "queued_bits_before")
        in_service = _num(backlog.get("in_service_remaining_bits_before"),
                          "in_service_remaining_bits_before")
        if queued is None and in_service is None:
            continue
        total = float((queued or 0.0) + (in_service or 0.0))
        backlog_samples.append(total)
        if peak is None or total > peak:
            peak = total
            peak_resource = row.get("link_id") or row.get("queue")
    return {
        "area_bits_s": area_block,
        "peak_bits": (None if peak is None else float(peak)),
        "peak_resource": peak_resource,
        "peak_samples": len(backlog_samples),
        "peak_basis": "max(backlog_before) over queue_enter rows",
        "peak_source": "timeline queue_enter.backlog_before (measured before "
                       "insertion, so a packet never counts itself)",
    }


def _e2e_block(emissions, deliveries, delivered_pids=None):
    expected = (sorted(delivered_pids) if delivered_pids is not None
                else sorted(deliveries))
    samples, pids = [], []
    missing_delivery_times = []
    for pid in expected:
        emitted_at = emissions.get(pid)
        delivered_at = deliveries.get(pid)
        if emitted_at is None or delivered_at is None:
            missing_delivery_times.append(pid)
            continue
        samples.append(float(delivered_at) - float(emitted_at))
        pids.append(pid)
    if missing_delivery_times:
        return {"samples": len(samples), "expected_samples": len(expected),
                "mean_s": None, "p50_s": None, "p90_s": None,
                "p95_s": None, "p99_s": None, "pids": pids,
                "status": NOT_COMPUTABLE,
                "missing_delivery_time_pids": missing_delivery_times,
                "missing_field": "result.deliveries[pid].delivered_at",
                "source": "result.deliveries + trace_rows.emit_time_s",
                "note": "one or more delivered packets lack a delivery or "
                        "emission instant; no partial E2E summary is reported"}
    if not samples:
        return {"samples": 0, "mean_s": None, "p50_s": None, "p90_s": None,
                "p95_s": None, "p99_s": None, "pids": [],
                "status": NOT_COMPUTABLE,
                "missing_field": "deliveries/emit_time_s",
                "source": "result.deliveries + trace_rows.emit_time_s",
                "note": "no delivered packet has both a delivery instant and "
                        "an emission instant"}
    ordered = sorted(samples)
    return {
        "samples": len(samples),
        "pids": pids,
        "mean_s": statistics.fmean(samples),
        "p50_s": t1_stats._percentile(ordered, 0.50),
        "p90_s": t1_stats._percentile(ordered, 0.90),
        "p95_s": t1_stats._percentile(ordered, 0.95),
        "p99_s": t1_stats._percentile(ordered, 0.99),
        "max_s": max(samples),
        "status": "COMPUTED",
        "subset": "delivered packets (all delivered, in and out of window)",
        # Reuse: linear-interpolation percentile is fixed by the T1 protocol;
        # calling the tested helper keeps one definition of p95 in the repo.
        "p95_s_source": "CODE.experiment_platform.t1_stats._percentile "
                        "(linear interpolation)",
        "source": "result.deliveries.delivered_at - "
                  "trace_rows.emit_time_s",
    }


# ------------------------------------------------------------- table reuse
def _table_block(decision_rows, timeline_rows):
    """Hit/fallback rates, bins actually used, install count and version age.

    Reuse: the hit/fallback flags are the per-decision audit the time-alignment
    layer already writes (observation_at_start.time_alignment.cache_hit /
    .fallback); the install and query milestones are the kernel's own
    schedule_installed / schedule_query rows.
    """
    hits = fallbacks = audits = 0
    for row in decision_rows:
        audit = ((row.get("observation_at_start") or {}).get("time_alignment"))
        if not isinstance(audit, dict):
            continue
        audits += 1
        if audit.get("cache_hit") is True:
            hits += 1
        if audit.get("fallback") is True:
            fallbacks += 1
    timeline_queries = [row for row in timeline_rows
                        if row.get("milestone") == "schedule_query"]
    # SOURCE RULE.  The kernel emits one schedule_query timeline row per table
    # lookup, with the fallback outcome ON the row.  The per-decision audit
    # (observation_at_start.time_alignment.cache_hit/.fallback) is a DIFFERENT
    # observation, so it is only accepted as evidence of a table lookup when an
    # explicit cache_hit/fallback flag is present AND the decision row carries
    # time-alignment evidence of the lookup (installed_version or bin).
    # Anything else yields no lookup at all: guessing here would export a
    # hit/fallback rate for a mechanism the mode never used.
    def _has_lookup_evidence(row):
        audit = (row.get("observation_at_start") or {}).get("time_alignment")
        if not isinstance(audit, dict):
            return False
        if not isinstance(audit.get("cache_hit"), bool) and not isinstance(
                audit.get("fallback"), bool):
            return False
        return (audit.get("installed_version") is not None
                or audit.get("bin") is not None)

    if timeline_queries:
        queries = timeline_queries
        query_fallbacks = sum(1 for row in timeline_queries
                              if row.get("fallback") is True)
        queries_source = "timeline schedule_query rows"
    elif any(_has_lookup_evidence(row) for row in decision_rows):
        audit_rows = [row for row in decision_rows
                      if _has_lookup_evidence(row)]
        queries = [{"at": None, "version": None, "bin": None}
                   for _ in audit_rows]
        query_fallbacks = sum(
            1 for row in audit_rows
            if (((row.get("observation_at_start") or {}).get(
                "time_alignment")) or {}).get("fallback") is True)
        queries_source = ("decision rows observation_at_start.time_alignment "
                          "(explicit cache_hit/fallback flag + lookup evidence)")
    else:
        queries = []
        query_fallbacks = 0
        queries_source = ("no table lookup in this mode: no schedule_query row "
                          "and no audit flag with lookup evidence")
    installs = [row for row in timeline_rows
                if row.get("milestone") == "schedule_installed"]
    bins = sorted({int(row["bin"]) for row in queries
                   if isinstance(row.get("bin"), int)
                   and not isinstance(row.get("bin"), bool)})
    install_at = {}
    for row in installs:
        version = row.get("version")
        at = _num(row.get("at"), "schedule_installed.at")
        if at is None or version is None:
            continue
        install_at.setdefault(version, at)
    ages = []
    for row in queries:
        version = row.get("version")
        if version is None:
            continue
        installed = install_at.get(version)
        at = _num(row.get("at"), "schedule_query.at")
        if installed is None or at is None:
            continue
        ages.append(max(0.0, at - installed))
    if not queries and not installs:
        version_age = {"samples": 0, "mean_s": None, "max_s": None,
                       "status": NOT_COMPUTABLE,
                       "missing_field": "schedule_installed/schedule_query",
                       "source": "timeline schedule_* milestones",
                       "note": "this mode installs no schedule table, so no "
                               "version age exists (it is not zero)"}
    elif ages:
        version_age = {
            "samples": len(ages),
            "mean_s": statistics.fmean(ages),
            "max_s": max(ages),
            "status": "COMPUTED",
            "missing_field": None,
            "source": "timeline schedule_query.at - schedule_installed.at "
                      "of the queried version",
        }
    else:
        # The mode does install tables, but no query carries a version number
        # that can be matched to an install instant.  That is a real zero
        # sample set (nothing to average), not a missing field.
        version_age = {
            "samples": 0,
            "mean_s": None,
            "max_s": None,
            "status": "COMPUTED",
            "missing_field": None,
            "source": "timeline schedule_query.at - schedule_installed.at "
                      "of the queried version",
            "note": "no queried version could be matched to an install "
                    "instant, so no age sample exists",
        }
    # A mode that never looks a table up has NO hit rate: reporting 0.0 would
    # read as "every lookup missed", which is a different (and false) claim.
    has_queries = bool(queries)
    return {
        "queries": len(queries),
        "queries_source": queries_source,
        "hits": (len(queries) - query_fallbacks) if has_queries else None,
        "fallbacks": query_fallbacks if has_queries else None,
        "audited_decisions": audits,
        "hit_rate": (_safe_rate(len(queries) - query_fallbacks, len(queries))
                     if has_queries else None),
        "fallback_rate": (_safe_rate(query_fallbacks, len(queries))
                          if has_queries else None),
        "bins_used": bins,
        "installs": len(installs),
        "version_age": version_age,
        "source": queries_source,
        "per_decision_audit": {"audited_decisions": audits,
                               "cache_hits": hits,
                               "fallbacks": fallbacks,
                               "source": "observation_at_start."
                                         "time_alignment.cache_hit/.fallback"},
        "table_kind": ("schedule_table_lookup" if has_queries
                       else "no_table_lookup_in_this_mode"),
        "note": "hit/fallback rates are SCHEDULE-TABLE lookup rates: a mode "
                "that installs and queries a table reports them, a mode that "
                "queries nothing reports NOT_COMPUTABLE (a 0.0 here would read "
                "as 'every lookup missed', a different and false claim), and "
                "the per-flow flow cache is accounted in reuse."
                "per_flow_cache_hits instead",
    }


def _install_block(timeline_rows):
    """Install count and install delay from the async schedule lifecycle.

    delay = schedule_installed.at - schedule_update_computed.at, matched per
    version in event order (the kernel emits update_computed immediately before
    the delay it then waits out).  It measures the table-delivery lag, not a
    compute cost, and is kept out of the compute totals.
    """
    # Version numbers are issued by the async manager in one global sequence
    # per run, so (version -> instants) is the correlation key.  The scope is
    # deliberately NOT part of the key: the compute_request milestone carries
    # it as a formatted string and the schedule_* milestones as a list, and
    # matching on that encoding would silently fail.
    computed = {}
    for row in timeline_rows:
        if row.get("milestone") != "schedule_update_computed":
            continue
        version = row.get("version")
        at = _num(row.get("at"), "schedule_update_computed.at")
        if version is not None and at is not None:
            computed[version] = at
    delays = []
    installs = 0
    for row in timeline_rows:
        if row.get("milestone") != "schedule_installed":
            continue
        installs += 1
        finished = computed.get(row.get("version"))
        at = _num(row.get("at"), "schedule_installed.at")
        if finished is None or at is None:
            continue
        delays.append(max(0.0, at - finished))
    if not installs:
        return {"installs": 0, "delay_samples": 0, "mean_delay_s": None,
                "max_delay_s": None, "status": NOT_COMPUTABLE,
                "missing_field": "schedule_installed",
                "source": "timeline schedule_update_computed / "
                          "schedule_installed",
                "note": "this mode installs no table, so there is no install "
                        "delay to report"}
    return {
        "installs": installs,
        "delay_samples": len(delays),
        "mean_delay_s": (statistics.fmean(delays) if delays else None),
        "max_delay_s": (max(delays) if delays else None),
        "status": "COMPUTED" if delays else NOT_COMPUTABLE,
        "missing_field": None if delays else
            "schedule_update_computed.at matched by version",
        "source": "timeline schedule_installed.at - "
                  "schedule_update_computed.at",
    }


# -------------------------------------------------------------- prediction
def _prediction_block(decision_rows, timeline_rows, emissions):
    """Resource mismatch, ETA error and same-resource prediction error.

    Reuse: CODE.leo_sim.decision_ledger.score_downstream_predictions owns the
    hindsight-truth resource comparison and the same-resource bits error, and
    ::score_start_estimates owns the t0-legal estimate.  Both are CALLED and
    their summaries merged under distinct keys, because the module documents
    that the first must never be quoted as a deployable prediction.
    """
    # Every sub-block is always a mapping: a caller must never have to guard
    # against None before reading a prediction error, and "no score" is
    # expressed by samples=0 plus a status, not by a missing object.
    empty = {
        "resource_compared": 0,
        "resource_mismatches": 0,
        "resource_match_rate": None,
        "mismatch_decision_ids": [],
        "same_resource": {"samples": 0, "mean_bits_error": None,
                          "max_bits_error": None},
        "t0_estimate": {"samples": 0, "mean_bits_error": None,
                        "max_bits_error": None},
        "eta_error": {"samples": 0, "mean_abs_error_s": None,
                      "max_abs_error_s": None},
        "downstream": {},
        "status": NOT_COMPUTABLE,
        "source": "CODE.leo_sim.decision_ledger.score_downstream_predictions "
                  "+ score_start_estimates",
    }
    if not decision_rows:
        empty["missing_field"] = "decision_rows"
        return empty
    try:
        truth_scores, truth_summary = decision_ledger.score_downstream_predictions(
            decision_rows, timeline_rows)
        estimate_scores, estimate_summary = decision_ledger.score_start_estimates(
            decision_rows, timeline_rows)
    except (ValueError, KeyError) as exc:
        empty["missing_field"] = f"decision_ledger scorer refused: {exc}"
        return empty

    # A consumer that delivers has no neighbour egress to contend for, so a
    # realised direction of "deliver"/None is "not comparable", never a
    # mismatch: the comparison is about the SHARED RESOURCE's direction.
    def _comparable(score):
        if score.get("egress_match") is None:
            return False
        return score.get("predicted_egress") is not None \
            and score.get("realized_egress") not in (None, "deliver")

    compared = [score for score in truth_scores.values() if _comparable(score)]
    mismatches = [score for score in compared if not score["egress_match"]]
    mismatch_ids = sorted(decision_id for decision_id in truth_scores
                          if truth_scores[decision_id] in mismatches)
    # Same-resource prediction error: compare the predicted bits with the
    # REALIZED bits on the SAME resource.  The kernel's realized egress bits
    # come from the arrival snapshot, which this fixture (like a real run
    # without an arrival for that hop) does not carry; the delta is then
    # genuinely unavailable rather than zero.
    same = [score for score in truth_scores.values()
            if score.get("egress_match") is True
            and score.get("egress_bits_delta") is not None]
    bits_errors = [abs(float(score["egress_bits_delta"])) for score in same]
    estimate_same = [score for score in estimate_scores.values()
                     if score.get("egress_match") is True
                     and score.get("egress_bits_delta") is not None]
    estimate_errors = [abs(float(score["egress_bits_delta"]))
                       for score in estimate_same]

    # ETA error: the decision-time ETA targets (one per candidate direction)
    # against the instant the packet really arrived at the chosen peer.  The
    # arrival instant is read from the peer_arrival milestone the kernel emits,
    # never reconstructed from a service window.
    arrivals = {}
    for row in timeline_rows:
        if row.get("milestone") != "peer_arrival":
            continue
        decision_id = row.get("decision_id")
        at = _num(row.get("at"), "peer_arrival.at")
        if decision_id is not None and at is not None:
            arrivals.setdefault(decision_id, at)
    eta_errors = []
    eta_samples = 0
    for row in decision_rows:
        decision_id = row.get("decision_id")
        if decision_id not in arrivals:
            continue
        audit = ((row.get("observation_at_start") or {}).get("time_alignment"))
        if not isinstance(audit, dict):
            continue
        targets = audit.get("eta_targets")
        if not isinstance(targets, dict) or not targets:
            continue
        chosen = row.get("chosen")
        target = _num(targets.get(chosen), "eta_targets")
        if target is None:
            continue
        eta_samples += 1
        eta_errors.append(abs(float(arrivals[decision_id]) - target))
    return {
        "resource_compared": len(compared),
        "resource_mismatches": len(mismatches),
        "resource_match_rate": _safe_rate(len(compared) - len(mismatches),
                                          len(compared)),
        "mismatch_decision_ids": mismatch_ids,
        "same_resource": {
            "samples": len(bits_errors),
            "mean_bits_error": (statistics.fmean(bits_errors)
                                if bits_errors else None),
            "max_bits_error": (max(bits_errors) if bits_errors else None),
            "source": "score_downstream_predictions egress_bits_delta",
        },
        "t0_estimate": {
            "samples": len(estimate_errors),
            "mean_bits_error": (statistics.fmean(estimate_errors)
                                if estimate_errors else None),
            "max_bits_error": (max(estimate_errors) if estimate_errors else None),
            "source": "score_start_estimates egress_bits_delta",
        },
        "eta_error": {
            "samples": eta_samples,
            "mean_abs_error_s": (statistics.fmean(eta_errors)
                                 if eta_errors else None),
            "max_abs_error_s": (max(eta_errors) if eta_errors else None),
            "source": "observation_at_start.time_alignment.eta_targets[chosen] "
                      "- timeline peer_arrival.at",
        },
        "downstream": truth_summary,
        "truth_summary": truth_summary,
        "estimate_summary": estimate_summary,
        "status": "COMPUTED",
        "source": "CODE.leo_sim.decision_ledger score_downstream_predictions "
                  "+ score_start_estimates; the two are never merged",
        "note": "egress_match compares the belief's direction with the "
                "direction the neighbour really chose; the bits error is the "
                "same-resource prediction error",
    }


# --------------------------------------------------------------- compute
def _compute_jobs(timeline_rows):
    """Group compute_request rows into jobs, keyed by compute_job_id."""
    jobs = {}
    for row in timeline_rows:
        job_id = row.get("compute_job_id")
        if job_id is None:
            continue
        job = jobs.setdefault(job_id, {"job_id": job_id, "sat": row.get("sat"),
                                       "pid": row.get("pid"),
                                       "scope": row.get("scope")})
        milestone = row.get("milestone")
        at = _num(row.get("at"), f"{milestone}.at")
        if milestone == "compute_request" and at is not None:
            job["requested_at"] = at
            job["service_s"] = _num(row.get("service_s"), "service_s")
            job["servers"] = row.get("servers")
        elif milestone == "compute_wait":
            wait = _num(row.get("wait_s"), "wait_s")
            if wait is not None:
                job["wait_s"] = wait
        elif milestone == "compute_start" and at is not None:
            job["started_at"] = at
        elif milestone == "compute_finish":
            job["finished_at"] = at
            if row.get("service_s") is not None:
                job["service_finished_s"] = _num(row.get("service_s"),
                                                 "service_s")
    return jobs


def _job_class(job):
    """packet decision compute or asynchronous background update.

    A packet decision job carries the packet id the kernel stamped from the
    DataPacket; a background scope update has no packet at all (the kernel
    writes pid=None and an async scope label) and is a compute job on the same
    per-satellite pool.  Either signal is enough, and the scope label is what
    tells the two async modes apart from a precomputed table build.
    """
    scope = job.get("scope")
    async_scope = isinstance(scope, str) and scope.startswith("async:")
    if job.get("pid") is not None or not async_scope:
        return COMPUTE_PACKET_DECISIONS
    return COMPUTE_BACKGROUND_UPDATES


def _jobs_summary(jobs, observation_end_s=None):
    """Counts, queue wait and service seconds for one class of compute job.

    A job is complete when it has a request, a start and a finish: the
    unbounded pool (servers=0) still emits all three, so "no queueing" is
    reported as zero wait, never as a missing job.

    Reuse: service time prefers the kernel's own compute_finish.service_s (the
    measured finished-started interval) over the configured service_s, so a
    starved or delayed job cannot be billed at the nominal cost.
    """
    waits = [_num(job.get("wait_s"), "wait_s") or 0.0 for job in jobs]
    requested = [job for job in jobs if job.get("requested_at") is not None]
    started = [job for job in jobs if job.get("started_at") is not None]
    finished = [job for job in jobs if job.get("finished_at") is not None]
    completed_service = []
    partial_service = []
    configured_service = []
    for job in jobs:
        nominal = _num(job.get("service_s"), "service_s")
        if job.get("requested_at") is not None and nominal is not None:
            configured_service.append(nominal)
        finished_at = _num(job.get("finished_at"), "compute_finish.at")
        started_at = _num(job.get("started_at"), "compute_start.at")
        measured = _num(job.get("service_finished_s"),
                        "compute_finish.service_s")
        if measured is None and finished_at is not None and started_at is not None:
            measured = max(0.0, finished_at - started_at)
        if finished_at is not None and measured is not None:
            completed_service.append(measured)
        elif (started_at is not None and observation_end_s is not None
              and observation_end_s > started_at):
            elapsed = float(observation_end_s) - started_at
            if nominal is not None:
                elapsed = min(elapsed, max(0.0, nominal))
            partial_service.append(max(0.0, elapsed))
    satellites = sorted({job.get("sat") for job in jobs
                         if job.get("sat") is not None})
    configured_total = float(sum(configured_service))
    completed_total = float(sum(completed_service))
    partial_total = float(sum(partial_service))
    return {
        "jobs": len(jobs),
        "job_ids": sorted(job["job_id"] for job in jobs),
        "request_events": len(requested),
        "start_events": len(started),
        "finish_events": len(finished),
        "queue_wait_s": float(sum(waits)),
        "queue_wait_mean_s": (statistics.fmean(waits) if waits else None),
        "queue_wait_max_s": (max(waits) if waits else None),
        "queued_jobs": sum(1 for job in jobs
                           if (_num(job.get("wait_s"), "wait_s") or 0.0) > 0.0),
        # `service_s` is measured service for jobs that reached a finish event.
        # Never charge an unfinished job its full configured duration.
        "service_s": completed_total,
        "service_mean_s": (statistics.fmean(completed_service)
                           if completed_service else None),
        "service_s_completed": completed_total,
        "service_s_observed_partial": partial_total,
        "service_s_observed_lower_bound": completed_total + partial_total,
        "service_s_configured_requested": configured_total,
        "service_s_projected_remaining": max(
            0.0, configured_total - completed_total - partial_total),
        "unfinished_jobs": sum(1 for job in jobs
                               if job.get("requested_at") is not None
                               and job.get("finished_at") is None),
        "zero_queueing": (all((_num(job.get("wait_s"), "wait_s") or 0.0) <= 0.0
                              for job in jobs) if jobs else None),
        "by_satellite": {str(sat): sum(1 for job in jobs
                                       if job.get("sat") == sat)
                         for sat in satellites},
    }


def _cost_block(result, timeline_rows, cost):
    """The TOTAL compute resource figure, with every separate line beside it."""
    jobs = _compute_jobs(timeline_rows)
    packet_jobs = {job_id: job for job_id, job in jobs.items()
                   if _job_class(job) == COMPUTE_PACKET_DECISIONS}
    background_jobs = {job_id: job for job_id, job in jobs.items()
                       if _job_class(job) == COMPUTE_BACKGROUND_UPDATES}
    mode = (result.get("execution_mode") or {}).get("mode")
    execution = result.get("execution_mode") or {}
    # Whole-run job accounting: a compute job's request/start/finish are a
    # single unit, so windowing the JOBS would split one job across two rows.
    observation_end = _num(result.get("stop_time_s"), "result.stop_time_s")
    served = _jobs_summary(list(packet_jobs.values()), observation_end)
    background = _jobs_summary(list(background_jobs.values()), observation_end)
    background["mode"] = ("async" if background_jobs else "none")

    def _observed_service_end(job, start):
        finished = _num(job.get("finished_at"), "compute_finish.at")
        if finished is not None:
            return finished
        if observation_end is None:
            return None
        nominal = _num(job.get("service_s"), "service_s")
        return (observation_end if nominal is None
                else min(observation_end, start + max(0.0, nominal)))

    service_intervals = []
    for row in timeline_rows:
        if row.get("milestone") != "compute_start":
            continue
        start = _num(row.get("at"), "compute_start.at")
        if start is None:
            continue
        job_id = row.get("compute_job_id")
        job = jobs.get(job_id) or {}
        end = _observed_service_end(job, start)
        if end is not None and end > start:
            service_intervals.append((start, end))

    total = {
        "jobs": served["jobs"] + background["jobs"],
        "request_events": served["request_events"] + background["request_events"],
        "start_events": served["start_events"] + background["start_events"],
        "finish_events": served["finish_events"] + background["finish_events"],
        "queue_wait_s": served["queue_wait_s"] + background["queue_wait_s"],
        "service_s": served["service_s"] + background["service_s"],
        "service_s_observed_partial": (
            served["service_s_observed_partial"]
            + background["service_s_observed_partial"]),
        "service_s_observed_lower_bound": (
            served["service_s_observed_lower_bound"]
            + background["service_s_observed_lower_bound"]),
        "service_s_configured_requested": (
            served["service_s_configured_requested"]
            + background["service_s_configured_requested"]),
        "service_s_projected_remaining": (
            served["service_s_projected_remaining"]
            + background["service_s_projected_remaining"]),
        "unfinished_jobs": served["unfinished_jobs"]
        + background["unfinished_jobs"],
        "queued_jobs": served["queued_jobs"] + background["queued_jobs"],
        "includes_background_updates": True,
        "note": "the total is the sum of the per-packet decision compute and "
                "the asynchronous background update compute: both occupy the "
                "same per-satellite compute pool",
    }
    servers = cost.get("servers", execution.get("compute_servers_per_satellite"))
    unbounded = servers == 0
    concurrency = None
    if cost.get("service_s") not in (None, 0):
        concurrency = total["service_s"] / float(cost["service_s"])
    # Pool saturation proxies: the union of the busy intervals per satellite is
    # the only honest "how long was the pool occupied" figure (summing the
    # per-satellite sums would double count concurrent satellites).
    per_sat_intervals = {}
    for row in timeline_rows:
        if row.get("milestone") != "compute_start":
            continue
        start = _num(row.get("at"), "compute_start.at")
        job = jobs.get(row.get("compute_job_id")) or {}
        end = (_observed_service_end(job, start)
               if start is not None else None)
        if start is None or end is None:
            continue
        sat = job.get("sat", row.get("sat"))
        per_sat_intervals.setdefault(sat, []).append((start, end))
    occupied_union = {str(sat): _union_seconds(intervals)
                      for sat, intervals in sorted(per_sat_intervals.items(),
                                                   key=lambda kv: str(kv[0]))}
    query = execution.get("query_service") or {}
    query_totals = query.get("totals") or {}
    install = _install_block(timeline_rows)
    control = _control_block(result, timeline_rows)
    precompute = execution.get("precompute") or {}

    control["precompute"] = {
        "builds": precompute.get("builds"),
        "targets": precompute.get("targets"),
        "installs": precompute.get("installs"),
        "cost_kind": precompute.get("cost_kind"),
        "build_wall_s": _num(precompute.get("build_wall_s"), "build_wall_s"),
        "note": "the offline topology table is built once and is NOT a "
                "per-packet compute charge; it is reported here so the "
                "precomputed mode's real cost is visible",
    }
    return {
        "packet_decisions": served,
        "background_updates": background,
        "total": total,
        "servers_per_satellite": servers,
        "unbounded": bool(unbounded),
        "service_s_configured": _num(cost.get("service_s"), "service_s"),
        "service_seconds_over_configured_service": concurrency,
        "pool_occupied_seconds_union_by_satellite": occupied_union,
        "compute_mode": mode,
        "query_service": {
            "requests": query_totals.get("requests"),
            "queue_wait_s": _num(query_totals.get("total_wait_s"), "total_wait_s"),
            "service_s": _num(query_totals.get("total_service_s"),
                              "total_service_s"),
            "max_wait_s": _num(query_totals.get("max_wait_s"), "max_wait_s"),
            "servers_per_satellite": query.get("servers_per_satellite"),
            "delay_s": _num(query.get("delay_s"), "delay_s"),
            "source": "result.execution_mode.query_service.totals",
            "note": "a separate service stage; its seconds are not added to "
                    "the compute totals or to any end-to-end figure",
        },
        "install": install,
        "control_traffic": control,
        "access_service": _access_block(result),
        "install_and_control_are_separate_lines": True,
        "stages_never_summed_into_e2e": True,
        "source": "timeline compute_* milestones (per-packet pid set vs "
                  "async scope jobs) + result.execution_mode",
    }


def _access_block(result):
    """Access-grant service as its own line: a stage, not an E2E cost.

    Reuse: the kernel counts these in result.access (requests, grants, slot
    hold seconds, wait seconds).  Slot hold is the link time a granted access
    slot kept the channel, which is a DIFFERENT resource from the compute pool
    and from the schedule query service; adding it to either would double count
    concurrent stages.
    """
    access = result.get("access") or {}
    return {
        "requests": access.get("requests"),
        "grants": access.get("grants"),
        "preposition_grants": access.get("preposition_grants"),
        "slot_hold_seconds": _num(access.get("slot_hold_s_total"),
                                  "access.slot_hold_s_total"),
        "wait_seconds": _num(access.get("wait_time_s_total"),
                             "access.wait_time_s_total"),
        "max_wait_seconds": _num(access.get("wait_time_s_max"),
                                 "access.wait_time_s_max"),
        "waiting_at_stop": access.get("waiting_at_stop"),
        "source": "result.access (access controller counters closed at stop)",
        "note": "a separate service stage; its seconds are never summed into "
                "the compute totals or into any end-to-end figure",
    }


def _control_block(result, timeline_rows):
    """Control traffic bits and occupancy seconds, as their own lines.

    bits: the control ledger's own offered/delivered/terminal_loss/in_system
    totals (result.control.totals).
    occupancy: the control transmission windows in
    result.link_service_windows (packet_kind == "control"), summed per resource
    with the concurrency-free total reported beside the union, so a reader can
    see both "how much link time was spent" and "how much wall time that was".
    """
    control = result.get("control") or {}
    totals = control.get("totals") or {}
    counters = control.get("bits") or {}
    windows = [row for row in (result.get("link_service_windows") or [])
               if row.get("packet_kind") == "control"]
    intervals = []
    by_stage = {}
    for row in windows:
        start = _num(row.get("start"), "link_service_window.start")
        end = _num(row.get("end"), "link_service_window.end")
        if start is None or end is None or end <= start:
            continue
        intervals.append((start, end))
        stage = row.get("stage") or "unknown"
        item = by_stage.setdefault(stage, {"windows": 0, "occupancy_seconds": 0.0,
                                           "bits": 0})
        item["windows"] += 1
        item["occupancy_seconds"] += end - start
        item["bits"] += int(_num(row.get("served_bits"), "served_bits") or 0)
    return {
        "counters": {
            "offered_bits": totals.get("offered_bits", counters.get("offered")),
            "delivered_bits": totals.get("delivered_bits",
                                         counters.get("delivered")),
            "terminal_loss_bits": totals.get(
                "terminal_loss_bits", counters.get("terminal_loss")),
            "in_system_bits_at_stop": totals.get(
                "in_system_bits_at_stop", counters.get("in_system")),
        },
        "bits_source": "result.control.totals (control ledger closed at stop)",
        "occupancy_seconds": _sum_seconds(intervals),
        "occupancy_seconds_union": _union_seconds(intervals),
        "occupancy_windows": len(intervals),
        "occupancy_by_stage": by_stage,
        "occupancy_source": "result.link_service_windows where "
                            "packet_kind == 'control' (served_bits/stage per "
                            "window)",
    }


# ------------------------------------------------------------ long-form rows
def _network_rows(counts, terminal_loss, censoring, delivery_ratio, window_block,
                  throughput, payload, queue, e2e, rate, table, prediction,
                  warmup, drain, bits, deliveries, admitted_count,
                  deadline_primary_loss, delivered_pids):
    source_counts = "trace rows (offer) + fate ledger + deliveries"
    rows = [
        _value_at("counts.offered", counts["offered"], COUNTS, source_counts),
        _value_at("counts.admitted", counts["admitted"], COUNTS,
                  "packet_events satellite_ingress",
                  missing_field=("result.packet_events.satellite_ingress"
                                 if admitted_count is None else None)),
        _value_at("counts.delivered", counts["delivered"], COUNTS,
                  "result.deliveries"),
        _value_at("counts.offered_bits", counts["offered_bits"], BITS,
                  "trace rows bits"),
        _value_at("counts.delivered_bits", counts["delivered_bits"], BITS,
                  "trace rows bits of delivered packets"),
        _value_at("counts.admitted_bits", counts["admitted_bits"], BITS,
                  "trace rows bits of packets observed at satellite_ingress",
                  missing_field=("result.packet_events.satellite_ingress"
                                 if counts["admitted_bits"] is None else None)),
        _value_at("counts.delivery_ratio_by_packets",
                  delivery_ratio["by_packets"], "ratio", source_counts),
        _value_at("counts.delivery_ratio_by_bits", delivery_ratio["by_bits"],
                  "ratio", source_counts),
        _value_at("terminal.classification",
                  json.dumps(terminal_loss["by_fate"], sort_keys=True),
                  COUNTS, "result.fates filtered to terminal loss fates"),
        _value_at("terminal.packets", terminal_loss["packets"], COUNTS,
                  "result.fates"),
        _value_at("terminal.bits", terminal_loss["bits"], BITS,
                  "trace rows bits of terminally lost packets"),
        _value_at("terminal.expired_and_undelivered",
                  terminal_loss["expired_and_undelivered"]["packets"], COUNTS,
                  "result.fates == DATA_DEADLINE_EXPIRED"),
        _value_at("censor.still_in_system_at_stop",
                  censoring["by_fate"].get("IN_SYSTEM_AT_STOP", 0), COUNTS,
                  "result.fates == IN_SYSTEM_AT_STOP"),
        _value_at("censor.packets", censoring["packets"], COUNTS,
                  "result.fates filtered to censoring fates"),
        _value_at("deadline_loss.mean", deadline_primary_loss.get("value"),
                  "normalized delay / D",
                  "trace_rows + result.fates + result.deliveries + stop_time_s",
                  note=("population mean; censored packets retain interval bounds"
                        if deadline_primary_loss.get("status") == "PARTIAL_BOUNDS"
                        else "population mean over the fixed measurement window"),
                  missing_field=deadline_primary_loss.get("missing_field")
                  or "packet-level delivery/fate/deadline fields"),
        _value_at("deadline_loss.lower_mean",
                  deadline_primary_loss.get("lower_mean"),
                  "normalized delay / D",
                  "trace_rows + result.fates + result.deliveries + stop_time_s",
                  note="lower bound; unknown outcomes are not replaced by zero",
                  missing_field=deadline_primary_loss.get("missing_field")
                  or "packet-level delivery/fate/deadline fields"),
        _value_at("deadline_loss.upper_mean",
                  deadline_primary_loss.get("upper_mean"),
                  "normalized delay / D",
                  "trace_rows + result.fates + result.deliveries + stop_time_s",
                  note="upper bound; unknown outcomes retain their worst-case bound",
                  missing_field=deadline_primary_loss.get("missing_field")
                  or "packet-level delivery/fate/deadline fields"),
    ]
    if window_block.get("status") == "COMPUTED":
        rows += [
            _value_at("window.start_s", window_block["start_s"], SECONDS,
                      window_block.get("source", "trace rows")),
            _value_at("window.end_s", window_block["end_s"], SECONDS,
                      window_block.get("source", "trace rows")),
            _value_at("window.fixed_window_s", window_block["fixed_window_s"],
                      SECONDS, window_block.get("source", "trace rows")),
            _value_at("window.offered_in_window",
                      window_block.get("offered_in_window"), COUNTS,
                      "trace rows emit_time_s inside the window",
                      missing_field="trace_rows.emit_time_s"),
        ]
    else:
        rows.append(_missing_at("window.fixed_window_s",
                                window_block.get("missing_field", "trace_rows"),
                                SECONDS, "trace rows",
                                "no measurement window could be built"))
    fixed = throughput.get("delivered_bits_in_window_over_fixed_window")
    population = throughput.get(
        "final_delivery_ratio_of_window_queue_population")
    if fixed is None:
        reason = window_block.get("missing_field", "trace_rows")
        rows += [
            _missing_at("throughput.delivered_bits_in_window_over_fixed_window",
                        reason, "1/s", "trace rows",
                        "the fixed window denominator is required"),
            _missing_at(
                "throughput.final_delivery_ratio_of_window_queue_population",
                reason, "ratio", "trace rows",
                "the window-generated population is required"),
        ]
    else:
        rows += [
            _value_at("throughput.delivered_bits_in_window_over_fixed_window",
                      fixed["value_bps"], "1/s",
                      "result.deliveries + trace rows bits + window seconds",
                      f"denominator={DENOMINATOR_FIXED_WINDOW}"),
            _value_at(
                "throughput.final_delivery_ratio_of_window_queue_population",
                population["value"], "ratio",
                "trace rows emit_time_s + result.deliveries",
                f"denominator={DENOMINATOR_WINDOW_POPULATION}"),
        ]
    rows += [
        _value_at("payload.goodput_bps", payload.get("goodput_bps"), "1/s",
                  "delivered bits inside the window / window seconds"),
        _value_at("payload.delivery_ratio", delivery_ratio["by_packets"],
                  "ratio", source_counts),
        _value_at("payload.delivered_bits_in_window",
                  payload.get("delivered_bits_in_window"), BITS,
                  "trace rows bits"),
        _value_at("warmup.packets", len(warmup), COUNTS,
                  "trace rows emit_time_s before the window",
                  "excluded from every window denominator"),
        _value_at("warmup.bits", int(sum(bits.get(pid, 0) for pid in warmup)),
                  BITS, "trace rows bits"),
        _value_at("drain.packets", len(drain), COUNTS,
                  "trace rows emit_time_s after the window",
                  "kept separable so a drain ceiling is not read as loss"),
        _value_at("drain.delivered",
                  sum(1 for pid in drain if pid in set(delivered_pids)), COUNTS,
                  "result.fates == DELIVERED"),
        _value_at("e2e.mean_s", e2e.get("mean_s"), SECONDS, e2e.get("source", ""),
                  e2e.get("subset", "")),
        _value_at("e2e.p50_s", e2e.get("p50_s"), SECONDS, e2e.get("source", "")),
        _value_at("e2e.p90_s", e2e.get("p90_s"), SECONDS, e2e.get("source", "")),
        _value_at("e2e.p95_s", e2e.get("p95_s"), SECONDS, e2e.get("source", "")),
        _value_at("e2e.p99_s", e2e.get("p99_s"), SECONDS, e2e.get("source", "")),
        _value_at("queue.peak_bits", queue.get("peak_bits"), BITS,
                  queue.get("peak_source", ""), queue.get("peak_basis", "")),
        _value_at("queue.area_bits_s", queue["area_bits_s"].get("total"),
                  "bits_s", queue["area_bits_s"].get("source", ""),
                  queue["area_bits_s"].get("note", ""),
                  missing_field="queue_area_bits_s"),
        _value_at("rate_per_satellite.hotspot_max_per_s",
                  rate.get("hotspot_max_per_s"), "1/s", rate.get("source", ""),
                  rate.get("note", "")),
        _value_at("rate_per_satellite.hotspot_satellite",
                  rate.get("hotspot_satellite"), "satellite index",
                  rate.get("source", "")),
        _value_at("rate_per_satellite.mean_per_s", rate.get("mean_per_s"),
                  "1/s", rate.get("source", "")),
        _value_at("rate_per_satellite.total_requests",
                  rate.get("total_requests"), COUNTS, rate.get("source", "")),
        _value_at("rate_per_satellite.redecisions", rate.get("redecisions"),
                  COUNTS, "timeline redecision milestones"),
        _value_at("table.hit_rate", table.get("hit_rate"), "ratio",
                  table.get("source", ""), table.get("note", ""),
                  missing_field="schedule_query rows / audit fallback flags"),
        _value_at("table.fallback_rate", table.get("fallback_rate"), "ratio",
                  table.get("source", ""), table.get("note", ""),
                  missing_field="schedule_query rows / audit fallback flags"),
        _value_at("table.queries", table.get("queries"), COUNTS,
                  table.get("source", "")),
        _value_at("table.installs", table.get("installs"), COUNTS,
                  table.get("source", "")),
        _value_at("table.bins_used", json.dumps(table.get("bins_used")),
                  "bin index", table.get("source", "")),
        _value_at("version_age.mean_s",
                  table["version_age"].get("mean_s"), SECONDS,
                  table["version_age"].get("source", ""),
                  table["version_age"].get("note", ""),
                  missing_field=table["version_age"].get("missing_field")
                  or "schedule_query.version vs schedule_installed.version"),
        _value_at("version_age.max_s", table["version_age"].get("max_s"),
                  SECONDS, table["version_age"].get("source", ""),
                  table["version_age"].get("note", ""),
                  missing_field=table["version_age"].get("missing_field")
                  or "schedule_query.version vs schedule_installed.version"),
        _value_at("prediction.resource_match_rate",
                  prediction.get("resource_match_rate"), "ratio",
                  prediction.get("source", "")),
        _value_at("prediction.resource_mismatches",
                  prediction.get("resource_mismatches"), COUNTS,
                  prediction.get("source", "")),
        _value_at("prediction.eta_error_mean_s",
                  prediction["eta_error"].get("mean_abs_error_s"), SECONDS,
                  prediction["eta_error"].get("source", "")),
        _value_at("prediction.same_resource_bits_error_mean",
                  prediction["same_resource"].get("mean_bits_error"), BITS,
                  prediction["same_resource"].get("source", "")),
        _value_at("prediction.t0_estimate_bits_error_mean",
                  prediction["t0_estimate"].get("mean_bits_error"), BITS,
                  prediction["t0_estimate"].get("source", "")),
    ]
    return rows


def _cost_rows(cost):
    packet = cost["packet_decisions"]
    background = cost["background_updates"]
    total = cost["total"]
    query = cost["query_service"]
    install = cost["install"]
    control = cost["control_traffic"]
    access = cost["access_service"]
    compute_source = ("timeline compute_request/compute_wait/"
                      "compute_start/compute_finish grouped by compute_job_id")
    rows = [
        _value_at("compute.packet_decisions.jobs", packet["jobs"], COUNTS,
                  compute_source, "jobs with a packet id (a decision had to run)"),
        _value_at("compute.packet_decisions.queued_jobs", packet["queued_jobs"],
                  COUNTS, compute_source),
        _value_at("compute.packet_decisions.queue_wait_s",
                  packet["queue_wait_s"], SECONDS, compute_source),
        _value_at("compute.packet_decisions.service_s", packet["service_s"],
                  SECONDS, compute_source),
        _value_at("compute.background_updates.jobs", background["jobs"], COUNTS,
                  compute_source, "async scope jobs on the same pool"),
        _value_at("compute.background_updates.queued_jobs",
                  background["queued_jobs"], COUNTS, compute_source),
        _value_at("compute.background_updates.queue_wait_s",
                  background["queue_wait_s"], SECONDS, compute_source),
        _value_at("compute.background_updates.service_s",
                  background["service_s"], SECONDS, compute_source),
        _value_at("compute.total.jobs", total["jobs"], COUNTS, compute_source,
                  "packet decisions + background updates"),
        _value_at("compute.total.queue_wait_s", total["queue_wait_s"], SECONDS,
                  compute_source),
        _value_at("compute.total.service_s", total["service_s"], SECONDS,
                  compute_source,
                  "TOTAL compute resource seconds, background included"),
        _value_at("query_service.requests", query["requests"], COUNTS,
                  query["source"]),
        _value_at("query_service.queue_wait_s", query["queue_wait_s"], SECONDS,
                  query["source"]),
        _value_at("query_service.service_s", query["service_s"], SECONDS,
                  query["source"]),
        _value_at("install.installs", install["installs"], COUNTS,
                  install["source"]),
        _value_at("install_delay.mean_s", install["mean_delay_s"], SECONDS,
                  install["source"]),
        _value_at("control_traffic.delivered_bits",
                  control["counters"]["delivered_bits"], BITS,
                  control["bits_source"]),
        _value_at("control_traffic.occupancy_seconds",
                  control["occupancy_seconds"], SECONDS,
                  control["occupancy_source"]),
        _value_at("access_service.grants", access["grants"], COUNTS,
                  access["source"]),
        _value_at("access_service.slot_hold_s", access["slot_hold_seconds"],
                  SECONDS, access["source"]),
        _value_at("access_service.wait_s", access["wait_seconds"], SECONDS,
                  access["source"]),
    ]
    return rows


# ---------------------------------------------------------------- CSV writer
def _csv_rows(document):
    context = document.get("context") or {}
    window = ((document.get("network_outcome") or {}).get("window") or {})
    out = []
    for row in document.get("rows") or []:
        out.append({
            "cell": context.get("cell"),
            "run_id": context.get("run_id"),
            "mode": context.get("mode"),
            "config_sha256": context.get("config_sha256"),
            "block": row.get("block"),
            "metric": row.get("metric"),
            "value": row.get("value"),
            "status": row.get("status"),
            "units": row.get("units"),
            "missing_field": row.get("missing_field"),
            "source": row.get("source"),
            "note": row.get("note"),
            "window_start_s": window.get("start_s"),
            "window_end_s": window.get("end_s"),
        })
    return out


def write_long_form_csv(document, path):
    """Write one row per metric, with its cell/run identity, and return the count.

    Long form (one metric per row) is what makes a figure regenerable from the
    tables: the metric name is data, not a column header that a re-run can
    silently reorder.
    """
    target = os.path.abspath(str(path))
    parent = os.path.dirname(target)
    if not parent or not os.path.isdir(parent):
        raise OutcomeMetricsError(
            f"output parent must be an existing directory: {parent!r}")
    if os.path.isdir(target):
        raise OutcomeMetricsError(f"output path is a directory: {target}")
    rows = _csv_rows(document)
    with open(target, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return len(rows)
