"""Independent second implementation of link utilization and delay decomposition.

WHY A SECOND IMPLEMENTATION EXISTS
==================================
The review requires the utilization / queueing / transmission / propagation
numbers of a run to be *recomputed independently* from the immutable event
record and to close against the realized end-to-end delay of the target packet.
Before this module the recompute step was not independent at all: receipt.py and
the production kernel called the *same* metrics.summarize function, so a defect
inside that function was compared against itself and could never be caught.
This module is written from the recorded events only, shares no code with
CODE.leo_sim.metrics, and is the second opinion used by
tests/test_metrics_independent.py.

INDEPENDENCE CONTRACT (fail loud, checked at import time)
=========================================================
This module must never import CODE.leo_sim.metrics (nor any helper of it), and
must never receive a value that was produced by it: the whole point is that a
disagreement between the two implementations stays observable.  The guard at
the bottom of this file walks this file with ast and raises RuntimeError if an
import of that module is ever added, or if this module namespace ever holds a
reference to it.  The production module is not imported transitively either:
this file imports the standard library only.

SEMANTICS THAT ARE DELIBERATELY NOT COPIED
==========================================
metrics.summarize falls back to denominator = service-window capacity when no
availability sample was recorded, which makes utilization identically ~1.0 -- a
meaningless reading, not a measurement.  This module reports that case as the
explicit status DEGENERATE_DENOMINATOR with utilization = None instead of
silently publishing 1.0.  It also rejects two conditions the production function
accepts: two *overlapping* (not merely duplicate) available-capacity windows on
one link, which would double-count the denominator, and a per-packet phase
decomposition whose intervals overlap, which would charge the same wall-clock
time twice.

The uncovered intervals of a packet timeline (a deferred decision consumes
simulated time that no service window and no propagation interval covers) are
named decision_compute_s.
"""
from __future__ import annotations

import math

# --------------------------------------------------------------- vocabulary
# The seven packet-event kinds the kernel emits (kernel.py:1484-1635).  An
# unknown kind is a hard error rather than a silently ignored row: a new
# emission site must be taught to this module explicitly.
EVENT_KINDS = frozenset({
    "packet_emitted", "queue_enter", "service_start", "propagation_start",
    "propagation_arrival", "satellite_ingress", "delivered",
})
QUEUE_NAMES = frozenset({"uplink", "holding", "isl", "downlink"})
STAGES = frozenset({"uplink", "isl", "downlink"})

CAPACITY_REL_TOL = 1e-9
CAPACITY_ABS_TOL = 1e-9
PROPAGATION_REL_TOL = 1e-9
PROPAGATION_ABS_TOL = 1e-12
# Two coverage intervals that touch to within this tolerance count as
# contiguous; it is far below every simulated time step of this platform.
CONTIGUITY_EPS_S = 1e-12
DEFAULT_TOLERANCE_S = 1e-9

STATUS_OK = "OK"
STATUS_DEGENERATE = "DEGENERATE_DENOMINATOR"

SCHEMA = "leo-sim-independent-metrics/v1"


class IndependentMetricsError(ValueError):
    """The immutable event/window record is malformed or inconsistent."""


# ---------------------------------------------------------------- validators
def _mapping_list(value, name):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise IndependentMetricsError(f"{name} must be a list")
    return list(value)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IndependentMetricsError(f"{name} must be numeric")
    value = float(value)
    if not math.isfinite(value):
        raise IndependentMetricsError(f"{name} must be finite")
    return value


def _nonneg_int(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IndependentMetricsError(f"{name} must be a non-negative integer")
    return value


def _positive_int(value, name):
    value = _nonneg_int(value, name)
    if value <= 0:
        raise IndependentMetricsError(f"{name} must be positive")
    return value


def _text(value, name):
    if not isinstance(value, str) or not value:
        raise IndependentMetricsError(f"{name} must be a non-empty string")
    return value


def _stage(value, name):
    value = _text(value, name)
    if value not in STAGES:
        raise IndependentMetricsError(f"{name} {value!r} is not a known stage")
    return value


def _capacity_matches(rate, start, end, declared, name):
    expected = rate * (end - start)
    if not math.isclose(expected, declared,
                        rel_tol=CAPACITY_REL_TOL, abs_tol=CAPACITY_ABS_TOL):
        raise IndependentMetricsError(
            f"{name}: capacity_bits does not equal rate_bps*(end-start)")


# ------------------------------------------------------------- event scanning
def _scan_packet_events(packet_events):
    """Fold the immutable event list into one record per packet.

    Only the documented fields are read.  propagation_arrival carries no
    stage/link_id (kernel.py:1596-1604), so its duration is resolved through
    prop_id against the matching propagation_start.
    """
    records = {}
    queue_owner = {}
    queue_entries = {}

    def record(pid):
        item = records.get(pid)
        if item is None:
            item = {
                "pid": pid, "emitted_at": None, "bits": None,
                "queue_entries": [], "service_starts": [],
                "prop_starts": {}, "prop_arrivals": {},
                "ingress_at": None, "delivered_at": None,
            }
            records[pid] = item
        return item

    for index, event in enumerate(packet_events):
        if not isinstance(event, dict):
            raise IndependentMetricsError(
                f"packet event {index} must be a mapping")
        kind = _text(event.get("kind"), "kind")
        if kind not in EVENT_KINDS:
            raise IndependentMetricsError(f"unknown packet event kind {kind!r}")
        pid = _nonneg_int(event.get("pid"), "packet event pid")
        at = _number(event.get("at"), f"{kind}.at")
        if at < 0:
            raise IndependentMetricsError(f"{kind}.at must be non-negative")
        item = record(pid)

        if kind == "packet_emitted":
            if item["emitted_at"] is not None:
                raise IndependentMetricsError(
                    f"duplicate packet_emitted for packet {pid}")
            item["emitted_at"] = at
            item["bits"] = _positive_int(event.get("bits"),
                                         "packet_emitted.bits")
        elif kind == "queue_enter":
            qid = _nonneg_int(event.get("queue_id"), "queue_enter.queue_id")
            if qid in queue_owner:
                raise IndependentMetricsError(f"duplicate queue_id {qid}")
            queue = _text(event.get("queue"), "queue_enter.queue")
            if queue not in QUEUE_NAMES:
                raise IndependentMetricsError(
                    f"queue_enter.queue {queue!r} is not a known queue")
            entry = {
                "queue_id": qid, "at": at, "queue": queue,
                "link_id": _text(event.get("link_id"), "queue_enter.link_id"),
                "index": index,
            }
            queue_owner[qid] = pid
            queue_entries[qid] = entry
            item["queue_entries"].append(entry)
        elif kind == "service_start":
            stage = _stage(event.get("stage"), "service_start.stage")
            link_id = _text(event.get("link_id"), "service_start.link_id")
            rate = _number(event.get("rate_bps"), "service_start.rate_bps")
            if rate <= 0:
                raise IndependentMetricsError(
                    "service_start.rate_bps must be positive")
            bits = _positive_int(event.get("bits"), "service_start.bits")
            qid = event.get("queue_id")
            entry = None
            if qid is not None:
                qid = _nonneg_int(qid, "service_start.queue_id")
                if qid not in queue_owner:
                    raise IndependentMetricsError(
                        f"service_start references unknown queue_id {qid}")
                if queue_owner[qid] != pid:
                    raise IndependentMetricsError(
                        f"queue_id {qid} belongs to another packet")
                entry = queue_entries[qid]
                if at < entry["at"]:
                    raise IndependentMetricsError(
                        f"service_start precedes queue_enter for packet {pid}")
            item["service_starts"].append({
                "at": at, "stage": stage, "link_id": link_id, "bits": bits,
                "rate_bps": rate, "queue_id": qid, "queue_entry": entry,
                "index": index,
            })
        elif kind == "propagation_start":
            prop_id = _nonneg_int(event.get("prop_id"),
                                  "propagation_start.prop_id")
            if prop_id in item["prop_starts"]:
                raise IndependentMetricsError(
                    f"duplicate propagation_start {prop_id} for packet {pid}")
            delay = _number(event.get("delay_s"), "propagation_start.delay_s")
            if delay < 0:
                raise IndependentMetricsError(
                    "propagation_start.delay_s must be non-negative")
            item["prop_starts"][prop_id] = {
                "prop_id": prop_id, "at": at, "delay_s": delay,
                "stage": _stage(event.get("stage"), "propagation_start.stage"),
                "link_id": _text(event.get("link_id"),
                                 "propagation_start.link_id"),
                "index": index,
            }
        elif kind == "propagation_arrival":
            prop_id = _nonneg_int(event.get("prop_id"),
                                  "propagation_arrival.prop_id")
            start = item["prop_starts"].get(prop_id)
            if start is None:
                raise IndependentMetricsError(
                    f"packet {pid} propagation_arrival references unknown "
                    f"prop_id {prop_id}")
            if prop_id in item["prop_arrivals"]:
                raise IndependentMetricsError(
                    f"duplicate propagation_arrival {prop_id} for packet {pid}")
            if at < start["at"]:
                raise IndependentMetricsError(
                    f"propagation_arrival precedes propagation_start for "
                    f"packet {pid} prop_id {prop_id}")
            realized = at - start["at"]
            declared_delay = start["delay_s"]
            if not math.isclose(realized, declared_delay,
                                rel_tol=PROPAGATION_REL_TOL,
                                abs_tol=PROPAGATION_ABS_TOL):
                raise IndependentMetricsError(
                    f"propagation delay mismatch for packet {pid} prop_id "
                    f"{prop_id}: realized {realized!r} but the start "
                    f"declared {declared_delay!r}")
            item["prop_arrivals"][prop_id] = at
        elif kind == "satellite_ingress":
            if item["ingress_at"] is not None:
                raise IndependentMetricsError(
                    f"duplicate satellite_ingress for packet {pid}")
            _text(event.get("endpoint"), "satellite_ingress.endpoint")
            _nonneg_int(event.get("satellite"), "satellite_ingress.satellite")
            bits = _positive_int(event.get("bits"), "satellite_ingress.bits")
            if item["emitted_at"] is None:
                raise IndependentMetricsError(
                    f"satellite_ingress packet {pid} was never emitted")
            if at < item["emitted_at"]:
                raise IndependentMetricsError(
                    "satellite_ingress precedes packet_emitted")
            if item["bits"] is not None and bits != item["bits"]:
                raise IndependentMetricsError(
                    f"satellite_ingress bits mismatch for packet {pid}")
            item["ingress_at"] = at
        else:  # delivered
            if item["delivered_at"] is not None:
                raise IndependentMetricsError(
                    f"duplicate delivered event for packet {pid}")
            if item["emitted_at"] is None:
                raise IndependentMetricsError(
                    f"delivered packet {pid} was never emitted")
            if at < item["emitted_at"]:
                raise IndependentMetricsError(
                    f"negative end-to-end delay for packet {pid}")
            item["delivered_at"] = at

    for pid, item in records.items():
        if item["delivered_at"] is None:
            continue
        unmatched = sorted(set(item["prop_starts"]) - set(item["prop_arrivals"]))
        if unmatched:
            raise IndependentMetricsError(
                f"delivered packet {pid} has unmatched propagation_start "
                f"prop_id(s) {unmatched}")
    return records


# ------------------------------------------------------------- window streams
def _validate_service_windows(service_windows, records):
    """Validate the service-window stream; return per-link and per-packet views."""
    links = {}
    by_pid = {}
    for index, window in enumerate(service_windows):
        if not isinstance(window, dict):
            raise IndependentMetricsError(
                f"service window {index} must be a mapping")
        pid = _nonneg_int(window.get("pid"), "service_window.pid")
        stage = _stage(window.get("stage"), "service_window.stage")
        link_id = _text(window.get("link_id"), "service_window.link_id")
        start = _number(window.get("start"), "service_window.start")
        end = _number(window.get("end"), "service_window.end")
        rate = _number(window.get("rate_bps"), "service_window.rate_bps")
        if start < 0 or end < start or rate <= 0:
            raise IndependentMetricsError(
                f"invalid service window bounds or rate for {link_id}")
        capacity = _number(window.get("capacity_bits"),
                           "service_window.capacity_bits")
        _capacity_matches(rate, start, end, capacity,
                          f"service window on {link_id}")
        served = _number(window.get("served_bits"), "service_window.served_bits")
        bits = window.get("bits")
        item = records.get(pid)
        if bits is None:
            bits = item["bits"] if item is not None else 0
        bits = _positive_int(bits, "service_window.bits")
        if served < 0 or served > bits:
            raise IndependentMetricsError(
                f"service window served_bits {served} outside packet bits "
                f"{bits} on {link_id}")
        if served > capacity * (1.0 + CAPACITY_REL_TOL):
            raise IndependentMetricsError(
                f"service window served_bits {served} exceed its capacity "
                f"{capacity} on {link_id}")
        outcome = window.get("outcome")
        if outcome == "ok" and not math.isclose(
                served, bits, rel_tol=CAPACITY_REL_TOL,
                abs_tol=CAPACITY_ABS_TOL):
            raise IndependentMetricsError(
                f"successful service window on {link_id} did not serve the "
                f"whole packet")
        if item is None or item["emitted_at"] is None:
            raise IndependentMetricsError(
                f"service window references packet {pid} that was never "
                f"emitted")
        packet_bits = item["bits"]
        if packet_bits is not None and bits != packet_bits:
            raise IndependentMetricsError(
                f"service window bits {bits} disagree with packet {pid} "
                f"bits {packet_bits}")
        aggregate = links.setdefault(link_id, {
            "stage": stage, "served_bits": 0.0, "capacity_bits": 0.0,
            "service_windows": 0,
        })
        if aggregate["stage"] != stage:
            raise IndependentMetricsError(f"link {link_id} changes stage")
        aggregate["served_bits"] += served
        aggregate["capacity_bits"] += capacity
        aggregate["service_windows"] += 1
        by_pid.setdefault(pid, []).append({
            "pid": pid, "stage": stage, "link_id": link_id, "start": start,
            "end": end, "rate_bps": rate, "capacity_bits": capacity,
            "served_bits": served, "bits": bits,
            "outcome": outcome, "index": index,
        })
    return links, by_pid


def _validate_available_windows(available_windows):
    """Validate the availability stream; reject duplicates *and* overlaps."""
    links = {}
    intervals = {}
    seen = set()
    for index, window in enumerate(available_windows):
        if not isinstance(window, dict):
            raise IndependentMetricsError(
                f"available window {index} must be a mapping")
        stage = _stage(window.get("stage"), "available_window.stage")
        link_id = _text(window.get("link_id"), "available_window.link_id")
        start = _number(window.get("start"), "available_window.start")
        end = _number(window.get("end"), "available_window.end")
        rate = _number(window.get("rate_bps"), "available_window.rate_bps")
        capacity = _number(window.get("capacity_bits"),
                           "available_window.capacity_bits")
        if start < 0 or end <= start or rate <= 0 or capacity < 0:
            raise IndependentMetricsError(
                f"invalid available window bounds or rate for {link_id}")
        _capacity_matches(rate, start, end, capacity,
                          f"available window on {link_id}")
        key = (link_id, start, end)
        if key in seen:
            raise IndependentMetricsError(
                f"duplicate available capacity window {key}")
        seen.add(key)
        aggregate = links.setdefault(link_id, {
            "stage": stage, "available_capacity_bits": 0.0,
            "available_time_s": 0.0, "available_samples": 0,
        })
        if aggregate["stage"] != stage:
            raise IndependentMetricsError(f"link {link_id} changes stage")
        aggregate["available_capacity_bits"] += capacity
        aggregate["available_time_s"] += end - start
        aggregate["available_samples"] += 1
        intervals.setdefault(link_id, []).append((start, end, index))

    # A double-counted denominator is invisible to a duplicate-key check, so
    # overlapping intervals on one link are rejected explicitly.  This is
    # strictly stronger than the production implementation, which only catches
    # exact (link_id, start, end) repeats and therefore accepts an overlap.
    for link_id, spans in intervals.items():
        spans.sort()
        previous_start, previous_end, previous_index = spans[0]
        for start, end, index in spans[1:]:
            if start < previous_end - CONTIGUITY_EPS_S:
                raise IndependentMetricsError(
                    f"overlapping available capacity windows on {link_id}: "
                    f"[{previous_start!r}, {previous_end!r}] (row "
                    f"{previous_index}) and [{start!r}, {end!r}] "
                    f"(row {index})")
            if end > previous_end:
                previous_start, previous_end = start, end
                previous_index = index
    return links


# ------------------------------------------------------------- public API #1
def recompute_link_utilization(packet_events, service_windows,
                               available_windows=None):
    """Recompute per-link served bits and utilization from the raw windows.

    Returns {link_id: {served_bits, capacity_bits, available_capacity_bits,
    utilization, status, ...}}.

    available_capacity_bits and utilization are the *sampled* physical
    availability; both are None when the run recorded no availability sample at
    all, and status is then DEGENERATE_DENOMINATOR.  The service-window
    fallback ratio is reported separately as fallback_utilization so a reader
    can never mistake it for a measurement (it is ~1.0 by construction).

    Raises IndependentMetricsError for a tampered capacity_bits, for overlapping
    or duplicated available windows, for a link whose served bits exceed its
    sampled available capacity (sum(served) <= sum(available)), and for a link
    with served_bits > 0 that has no availability coverage while availability
    was sampled at all.
    """
    records = _scan_packet_events(_mapping_list(packet_events, "packet_events"))
    service_windows = _mapping_list(service_windows, "service_windows")
    available_windows = _mapping_list(available_windows, "available_windows")
    links, _ = _validate_service_windows(service_windows, records)
    availability = _validate_available_windows(available_windows)
    sampled = bool(available_windows)

    for link_id, item in availability.items():
        aggregate = links.setdefault(link_id, {
            "stage": item["stage"], "served_bits": 0.0, "capacity_bits": 0.0,
            "service_windows": 0,
        })
        if aggregate["stage"] != item["stage"]:
            raise IndependentMetricsError(f"link {link_id} changes stage")

    report = {}
    for link_id, item in links.items():
        available = availability.get(link_id)
        available_bits = (available["available_capacity_bits"]
                          if available is not None else 0.0)
        capacity = item["capacity_bits"]
        served = item["served_bits"]
        entry = {
            "stage": item["stage"],
            "served_bits": served,
            "capacity_bits": capacity,
            "service_windows": item["service_windows"],
            "available_samples": (available["available_samples"]
                                  if available is not None else 0),
            "available_time_s": (available["available_time_s"]
                                 if available is not None else 0.0),
        }
        if not sampled:
            # No physical availability was sampled: the sampled denominator
            # does not exist.  Say so instead of publishing 1.0.
            entry["available_capacity_bits"] = None
            entry["utilization"] = None
            entry["status"] = STATUS_DEGENERATE
            entry["fallback_utilization"] = min(
                1.0, served / capacity if capacity else 0.0)
        else:
            if served > 0 and available is None:
                raise IndependentMetricsError(
                    f"served link {link_id} has no available-capacity "
                    f"coverage: {served} served bits are missing from the "
                    f"denominator")
            if served > available_bits * (1.0 + CAPACITY_REL_TOL):
                raise IndependentMetricsError(
                    f"link {link_id}: served bits {served} exceed sampled "
                    f"available capacity {available_bits}")
            entry["available_capacity_bits"] = available_bits
            entry["utilization"] = min(
                1.0, served / available_bits if available_bits else 0.0)
            entry["status"] = STATUS_OK
        report[link_id] = entry
    return report


# ------------------------------------------------------------- phase pairing
def _queue_waits(item):
    """Pair every service_start with the queue admission it consumed."""
    waits = []
    for start in item["service_starts"]:
        entry = start["queue_entry"]
        if entry is None:
            continue
        if start["at"] < entry["at"]:
            raise IndependentMetricsError(
                f"service_start precedes queue_enter for the packet")
        waits.append({
            "queue_id": entry["queue_id"], "queue": entry["queue"],
            "entered_at": entry["at"], "service_start_at": start["at"],
            "seconds": start["at"] - entry["at"],
        })
    return waits


def _holding_waits(item):
    """Pair each holding admission with the next queue admission of the packet.

    A packet still held at the horizon has no next admission and is therefore
    not credited with a fabricated exit time (tested explicitly).
    """
    entries = sorted(item["queue_entries"], key=lambda e: (e["at"], e["index"]))
    waits = []
    for index, entry in enumerate(entries[:-1]):
        if entry["queue"] != "holding":
            continue
        following = entries[index + 1]
        if following["at"] < entry["at"]:
            raise IndependentMetricsError("holding queue exit precedes entry")
        waits.append({
            "queue_id": entry["queue_id"], "entered_at": entry["at"],
            "exit_at": following["at"], "next_queue": following["queue"],
            "seconds": following["at"] - entry["at"],
        })
    return waits


def _propagation_hops(item):
    """Resolve every realized propagation interval through its prop_id."""
    hops = []
    for prop_id in sorted(item["prop_starts"]):
        start = item["prop_starts"][prop_id]
        arrival = item["prop_arrivals"].get(prop_id)
        if arrival is None:
            continue
        hops.append({
            "prop_id": prop_id, "stage": start["stage"],
            "link_id": start["link_id"], "start": start["at"],
            "end": arrival, "seconds": arrival - start["at"],
        })
    return hops


def _spans(item, tx_windows, queue_waits, holding_waits, propagation_hops):
    spans = []
    for wait in queue_waits:
        spans.append((wait["entered_at"], wait["service_start_at"],
                      "queue_wait", wait["queue_id"]))
    for window in tx_windows:
        spans.append((window["start"], window["end"], "tx", window["link_id"]))
    for hop in propagation_hops:
        spans.append((hop["start"], hop["end"], "propagation", hop["prop_id"]))
    for wait in holding_waits:
        spans.append((wait["entered_at"], wait["exit_at"], "holding_wait",
                      wait["queue_id"]))
    return sorted(spans, key=lambda span: (span[0], span[1]))


def _overlaps(spans):
    """Report phase intervals that charge the same wall-clock time twice."""
    found = []
    if not spans:
        return found
    current_start, current_end, current_label, current_key = spans[0]
    for start, end, label, key in spans[1:]:
        if start < current_end - CONTIGUITY_EPS_S:
            found.append({
                "previous": current_label, "previous_key": current_key,
                "previous_interval": (current_start, current_end),
                "next": label, "next_key": key,
                "next_interval": (start, end),
                "overlap_s": min(current_end, end) - start,
            })
        if end > current_end:
            current_start, current_end = start, end
            current_label, current_key = label, key
    return found


def _uncovered(emitted_at, delivered_at, spans, eps):
    """Uncovered sub-intervals of the closed interval [emitted, delivered]."""
    gaps = []
    cursor = emitted_at
    for start, end, _label, _key in spans:
        if start > cursor + eps:
            gaps.append((cursor, start, start - cursor))
        cursor = max(cursor, end)
    if delivered_at > cursor + eps:
        gaps.append((cursor, delivered_at, delivered_at - cursor))
    return gaps


# ------------------------------------------------------------- public API #2
def decompose_packet_delay(packet_events, service_windows, pid):
    """Decompose one realized packet delay into named, disjoint phases.

    e2e_s == queue_wait_s + holding_wait_s + tx_s + prop_s + decision_compute_s
    holds to machine precision for every delivered packet; residual_s is the
    measured slack.  decision_compute_s is the sum of the uncovered intervals of
    the packet timeline, i.e. simulated time consumed by a deferred decision
    that no service window and no propagation interval accounts for.

    An undelivered packet keeps its phase durations but reports delivered_at,
    gaps, decision_compute_s, e2e_s and residual_s as None: it has no realized
    delay and must never enter an end-to-end statistic.
    """
    records = _scan_packet_events(_mapping_list(packet_events, "packet_events"))
    _, by_pid = _validate_service_windows(
        _mapping_list(service_windows, "service_windows"), records)
    pid = _nonneg_int(pid, "pid")
    return _decompose(records, by_pid, pid)


def _decompose(records, by_pid, pid):
    item = records.get(pid)
    if item is None or item["emitted_at"] is None:
        raise IndependentMetricsError(f"packet {pid} was never emitted")
    tx_windows = sorted(by_pid.get(pid, ()),
                        key=lambda w: (w["start"], w["index"]))
    queue_waits = _queue_waits(item)
    holding_waits = _holding_waits(item)
    propagation_hops = _propagation_hops(item)
    queue_wait_s = math.fsum(w["seconds"] for w in queue_waits)
    holding_wait_s = math.fsum(w["seconds"] for w in holding_waits)
    tx_s = math.fsum(w["end"] - w["start"] for w in tx_windows)
    prop_s = math.fsum(h["seconds"] for h in propagation_hops)
    spans = _spans(item, tx_windows, queue_waits, holding_waits,
                   propagation_hops)
    emitted_at = item["emitted_at"]
    delivered_at = item["delivered_at"]
    result = {
        "schema": SCHEMA,
        "pid": pid,
        "bits": item["bits"],
        "emitted_at": emitted_at,
        "delivered_at": delivered_at,
        "queue_wait_s": queue_wait_s,
        "holding_wait_s": holding_wait_s,
        "tx_s": tx_s,
        "prop_s": prop_s,
        "queue_waits": queue_waits,
        "holding_waits": holding_waits,
        "propagation_hops": propagation_hops,
        "service_windows": len(tx_windows),
        "ingress_at": item["ingress_at"],
        "overlaps": _overlaps(spans),
    }
    if delivered_at is None:
        result.update({
            "delivered": False, "e2e_s": None, "gaps": None,
            "decision_compute_s": None, "max_uncovered_interval_s": None,
            "residual_s": None, "coverage_s": None,
        })
        return result
    gaps = _uncovered(emitted_at, delivered_at, spans, CONTIGUITY_EPS_S)
    raw_gaps = _uncovered(emitted_at, delivered_at, spans, 0.0)
    decision_compute_s = math.fsum(gap[2] for gap in gaps)
    e2e_s = delivered_at - emitted_at
    residual_s = e2e_s - (queue_wait_s + holding_wait_s + tx_s + prop_s
                          + decision_compute_s)
    result.update({
        "delivered": True,
        "e2e_s": e2e_s,
        "gaps": gaps,
        "decision_compute_s": decision_compute_s,
        "max_uncovered_interval_s": max((gap[2] for gap in raw_gaps),
                                        default=0.0),
        "residual_s": residual_s,
        "coverage_s": math.fsum(
            max(0.0, span[1] - span[0]) for span in spans),
    })
    return result


# ------------------------------------------------------------- public API #3
def verify_delay_decomposition(packet_events, service_windows,
                               delivered_pids=None, *,
                               tolerance_s=DEFAULT_TOLERANCE_S):
    """Close every delivered packet decomposition against its own e2e delay.

    delivered_pids may be an explicit collection of packet ids or the kernel
    result mapping (which supplies deliveries and, when the two stream
    arguments are None, packet_events and link_service_windows).

    Returns a report whose ok is False exactly when a closure or double-charge
    violation was found; structural violations (a tampered capacity_bits, an
    unknown event kind, an overlapping window ledger) raise
    IndependentMetricsError instead.  Undelivered packets never enter the
    report.
    """
    if tolerance_s <= 0 or not math.isfinite(tolerance_s):
        raise IndependentMetricsError("tolerance_s must be positive and finite")

    declared = None
    if isinstance(delivered_pids, dict):
        result = delivered_pids
        if packet_events is None:
            packet_events = result.get("packet_events")
        if service_windows is None:
            service_windows = result.get("link_service_windows")
        if "deliveries" in result:
            declared = set(result["deliveries"])
        elif "delivered_pids" in result:
            declared = set(result["delivered_pids"])
        else:
            raise IndependentMetricsError(
                "result mapping has neither deliveries nor delivered_pids")
    elif delivered_pids is None:
        declared = None
    else:
        declared = {_nonneg_int(pid, "delivered pid") for pid in delivered_pids}

    records = _scan_packet_events(_mapping_list(packet_events, "packet_events"))
    _, by_pid = _validate_service_windows(
        _mapping_list(service_windows, "service_windows"), records)
    observed = {pid for pid, item in records.items()
                if item["delivered_at"] is not None}
    if declared is None:
        declared = set(observed)

    errors = []
    for pid in sorted(declared - observed):
        errors.append(f"packet {pid} is declared delivered but has no "
                      f"delivered event")
    for pid in sorted(observed - declared):
        errors.append(f"packet {pid} has a delivered event but is not in the "
                      f"declared delivered set")

    packets = {}
    max_abs_residual = 0.0
    total_decision_compute = 0.0
    for pid in sorted(declared & observed):
        decomposition = _decompose(records, by_pid, pid)
        packets[str(pid)] = decomposition
        residual = decomposition["residual_s"]
        max_abs_residual = max(max_abs_residual, abs(residual))
        total_decision_compute += decomposition["decision_compute_s"]
        e2e = decomposition["e2e_s"]
        queue = decomposition["queue_wait_s"]
        holding = decomposition["holding_wait_s"]
        tx = decomposition["tx_s"]
        prop = decomposition["prop_s"]
        decision = decomposition["decision_compute_s"]
        if abs(residual) > tolerance_s:
            errors.append(
                f"packet {pid}: e2e {e2e!r} is not queue {queue!r} + holding "
                f"{holding!r} + tx {tx!r} + prop {prop!r} + decision_compute "
                f"{decision!r}; residual {residual!r} exceeds the tolerance "
                f"{tolerance_s!r}")
        for overlap in decomposition["overlaps"]:
            previous = overlap["previous"]
            following = overlap["next"]
            overlap_s = overlap["overlap_s"]
            errors.append(
                f"packet {pid}: phase intervals overlap ({previous} and "
                f"{following}, {overlap_s!r}s charged twice)")
    return {
        "schema": SCHEMA,
        "ok": not errors,
        "tolerance_s": tolerance_s,
        "checked_packets": len(packets),
        "delivered_pids": sorted(declared & observed),
        "packets": packets,
        "max_abs_residual_s": max_abs_residual,
        "total_decision_compute_s": total_decision_compute,
        "errors": errors,
    }


# ------------------------------------------------------------- independence
def _assert_no_metrics_import():
    """Fail loud if the independence contract of this module is ever broken."""
    import ast
    with open(__file__, "r", encoding="utf-8") as handle:
        source = handle.read()
    for node in ast.walk(ast.parse(source, filename=__file__)):
        names = ()
        if isinstance(node, ast.Import):
            names = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = tuple([node.module or ""]
                          + [alias.name for alias in node.names])
        for name in names:
            if name == "metrics" or name.endswith(".metrics"):
                raise RuntimeError(
                    "metrics_independent must not import the production "
                    "metrics module: this file exists to be a second, "
                    "independent implementation")
    for value in globals().values():
        if getattr(value, "__name__", "") in ("metrics", "CODE.leo_sim.metrics"):
            raise RuntimeError(
                "metrics_independent holds a reference to the production "
                "metrics module")


_assert_no_metrics_import()
