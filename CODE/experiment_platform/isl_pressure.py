"""Recompute fixed-window directed-ISL pressure evidence from raw ledgers.

The formal congestion metric is intentionally horizon-aggregate.  This module
adds a diagnostic view for short-lived pressure without changing the simulator
or inventing queue samples.  ISL queue waiting is reconstructed only for
queue entries that have an exact ``service_start`` match; unmatched entries are
reported as censored rather than assigned a fabricated exit time.
"""
from __future__ import annotations

import math
from typing import Any


class PressureAnalysisError(ValueError):
    """Raw window evidence is malformed or internally inconsistent."""


DEFAULT_WINDOW_S = 1.0
DEFAULT_MIN_AVAILABLE_FRACTION = 0.9
DEFAULT_HIGH_UTILIZATION = 0.8
DEFAULT_MIN_CONSECUTIVE_HIGH_WINDOWS = 2
DEFAULT_MIN_EPISODE_QUEUE_WAIT_S = 0.1
DEFAULT_MIN_EPISODE_QUEUE_AREA_BITS_S = 100000.0


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise PressureAnalysisError(f"{label} must be finite numeric")
    return float(value)


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PressureAnalysisError(f"{label} must be a non-empty string")
    return value


def _pid(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PressureAnalysisError(f"{label} must be a non-negative integer")
    return value


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[rank]


def _longest_consecutive(indices: list[int]) -> int:
    longest = current = 0
    previous: int | None = None
    for index in indices:
        current = current + 1 if previous is not None and index == previous + 1 else 1
        longest = max(longest, current)
        previous = index
    return longest


def _consecutive_runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for index in indices:
        if not runs or index != runs[-1][-1] + 1:
            runs.append([index])
        else:
            runs[-1].append(index)
    return runs


def analyze_windows(
        ledgers: dict[str, Any], *, window_s: float = DEFAULT_WINDOW_S,
        min_available_fraction: float = DEFAULT_MIN_AVAILABLE_FRACTION,
        high_utilization: float = DEFAULT_HIGH_UTILIZATION,
        min_consecutive_high_windows: int =
        DEFAULT_MIN_CONSECUTIVE_HIGH_WINDOWS,
        min_episode_queue_wait_s: float = DEFAULT_MIN_EPISODE_QUEUE_WAIT_S,
        min_episode_queue_area_bits_s: float =
        DEFAULT_MIN_EPISODE_QUEUE_AREA_BITS_S) -> dict[str, Any]:
    """Return exact-overlap 1-D window diagnostics for directed ISLs.

    Service and available-capacity intervals are apportioned by their overlap
    with fixed bins.  A successful constant-rate service interval therefore
    contributes served bits uniformly in time.  This does not infer demand;
    it only rebins already receipt-verified physical service evidence.
    """
    if not isinstance(ledgers, dict):
        raise PressureAnalysisError("ledgers must be a mapping")
    stop = _finite(ledgers.get("stop_time_s"), "stop_time_s")
    window_s = _finite(window_s, "window_s")
    min_available_fraction = _finite(
        min_available_fraction, "min_available_fraction")
    high_utilization = _finite(high_utilization, "high_utilization")
    min_episode_queue_wait_s = _finite(
        min_episode_queue_wait_s, "min_episode_queue_wait_s")
    min_episode_queue_area_bits_s = _finite(
        min_episode_queue_area_bits_s, "min_episode_queue_area_bits_s")
    if stop <= 0 or window_s <= 0:
        raise PressureAnalysisError("stop_time_s and window_s must be positive")
    if not 0 < min_available_fraction <= 1:
        raise PressureAnalysisError(
            "min_available_fraction must be in (0, 1]")
    if not 0 < high_utilization <= 1:
        raise PressureAnalysisError("high_utilization must be in (0, 1]")
    if min_episode_queue_wait_s < 0 or min_episode_queue_area_bits_s < 0:
        raise PressureAnalysisError(
            "episode queue-wait thresholds must be non-negative")
    if isinstance(min_consecutive_high_windows, bool) or not isinstance(
            min_consecutive_high_windows, int) \
            or min_consecutive_high_windows < 1:
        raise PressureAnalysisError(
            "min_consecutive_high_windows must be a positive integer")

    packet_events = ledgers.get("packet_events")
    service_windows = ledgers.get("link_service_windows")
    available_windows = ledgers.get("link_available_windows")
    if not isinstance(packet_events, list) or not isinstance(
            service_windows, list) or not isinstance(available_windows, list):
        raise PressureAnalysisError(
            "packet_events and link window ledgers must be lists")

    bin_count = math.ceil(stop / window_s)
    links: dict[str, dict[str, Any]] = {}

    def ensure(link_id: str) -> dict[str, Any]:
        return links.setdefault(link_id, {
            "served": [0.0] * bin_count,
            "capacity": [0.0] * bin_count,
            "available_time": [0.0] * bin_count,
            "queue_area": [0.0] * bin_count,
            "max_queue_wait_s": 0.0,
            "matched_queue_entries": 0,
            "matched_waits": [],
        })

    def allocate(start: float, end: float, callback) -> None:
        first = max(0, int(math.floor(start / window_s)))
        last = min(bin_count - 1,
                   max(first, int(math.ceil(end / window_s) - 1)))
        for index in range(first, last + 1):
            bin_start = index * window_s
            bin_end = min(stop, bin_start + window_s)
            overlap = max(0.0, min(end, bin_end) - max(start, bin_start))
            if overlap > 0:
                callback(index, overlap)

    interval_sets: dict[tuple[str, str], list[tuple[float, float]]] = {}

    def record_interval(kind: str, link_id: str, start: float, end: float) -> None:
        interval_sets.setdefault((kind, link_id), []).append((start, end))

    for raw in available_windows:
        if not isinstance(raw, dict):
            raise PressureAnalysisError(
                "every available-capacity window must be a mapping")
        if raw.get("stage") != "isl":
            continue
        link_id = _nonempty(raw.get("link_id"), "available.link_id")
        start = _finite(raw.get("start"), f"{link_id}.available.start")
        end = _finite(raw.get("end"), f"{link_id}.available.end")
        rate = _finite(raw.get("rate_bps"), f"{link_id}.available.rate_bps")
        capacity = _finite(
            raw.get("capacity_bits"), f"{link_id}.available.capacity_bits")
        if start < 0 or end <= start or end > stop + 1e-9 or rate <= 0:
            raise PressureAnalysisError(f"invalid available window for {link_id}")
        if not math.isclose(capacity, rate * (end - start),
                            rel_tol=1e-9, abs_tol=1e-6):
            raise PressureAnalysisError(
                f"available capacity mismatch for {link_id}")
        record_interval("available", link_id, start, end)
        item = ensure(link_id)
        allocate(start, end, lambda index, overlap: (
            item["capacity"].__setitem__(
                index, item["capacity"][index] + rate * overlap),
            item["available_time"].__setitem__(
                index, item["available_time"][index] + overlap),
        ))

    service_evidence: dict[tuple[int, str, float], dict[str, Any]] = {}
    for raw in service_windows:
        if not isinstance(raw, dict):
            raise PressureAnalysisError(
                "every service window must be a mapping")
        if raw.get("stage") != "isl":
            continue
        link_id = _nonempty(raw.get("link_id"), "service.link_id")
        pid = _pid(raw.get("pid"), "service.pid")
        start = _finite(raw.get("start"), f"{link_id}.service.start")
        end = _finite(raw.get("end"), f"{link_id}.service.end")
        rate = _finite(raw.get("rate_bps"), f"{link_id}.service.rate_bps")
        capacity = _finite(
            raw.get("capacity_bits"), f"{link_id}.service.capacity_bits")
        served = _finite(raw.get("served_bits"), f"{link_id}.served_bits")
        bits = raw.get("bits")
        if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
            raise PressureAnalysisError(
                f"service bits must be a positive integer for {link_id}")
        if start < 0 or end < start or end > stop + 1e-9 or rate <= 0:
            raise PressureAnalysisError(f"invalid service window for {link_id}")
        if not math.isclose(capacity, rate * (end - start),
                            rel_tol=1e-9, abs_tol=1e-6):
            raise PressureAnalysisError(
                f"service capacity mismatch for {link_id}")
        if served < 0 or served > capacity * (1 + 1e-9):
            raise PressureAnalysisError(
                f"served bits exceed service capacity for {link_id}")
        key = (pid, link_id, start)
        if key in service_evidence:
            raise PressureAnalysisError(
                f"duplicate service-window identity for {link_id}")
        service_evidence[key] = {"rate_bps": rate, "bits": bits}
        if end == start:
            if capacity != 0 or served != 0:
                raise PressureAnalysisError(
                    f"zero-duration service carries bits for {link_id}")
            ensure(link_id)
            continue
        record_interval("service", link_id, start, end)
        item = ensure(link_id)
        served_rate = served / (end - start)
        allocate(start, end, lambda index, overlap: item["served"].__setitem__(
            index, item["served"][index] + served_rate * overlap))

    for (kind, link_id), intervals in interval_sets.items():
        previous_end = -1.0
        for start, end in sorted(intervals):
            if start < previous_end - 1e-9:
                raise PressureAnalysisError(
                    f"overlapping {kind} windows for {link_id}")
            previous_end = max(previous_end, end)

    emitted_bits: dict[int, int] = {}
    queue_entries: dict[int, dict[str, Any]] = {}
    service_starts: list[dict[str, Any]] = []

    def event_time(raw: dict[str, Any], label: str) -> float:
        at = _finite(raw.get("at"), f"{label}.at")
        if at < 0 or at > stop + 1e-9:
            raise PressureAnalysisError(f"{label}.at is outside stop time")
        return at

    for raw in packet_events:
        if not isinstance(raw, dict):
            raise PressureAnalysisError("every packet event must be a mapping")
        kind = raw.get("kind")
        if kind == "packet_emitted":
            pid = _pid(raw.get("pid"), "packet_emitted.pid")
            event_time(raw, "packet_emitted")
            bits = raw.get("bits")
            if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
                raise PressureAnalysisError(
                    "packet_emitted.bits must be a positive integer")
            if pid in emitted_bits:
                raise PressureAnalysisError(f"duplicate packet_emitted for {pid}")
            emitted_bits[pid] = bits
        elif kind == "queue_enter" and raw.get("queue") == "isl":
            qid = raw.get("queue_id")
            if isinstance(qid, bool) or not isinstance(qid, int) or qid < 0:
                raise PressureAnalysisError(
                    "queue_enter.queue_id must be a non-negative integer")
            if qid in queue_entries:
                raise PressureAnalysisError(f"duplicate queue_id {qid}")
            queue_entries[qid] = {
                "pid": _pid(raw.get("pid"), "queue_enter.pid"),
                "at": event_time(raw, "queue_enter"),
                "link_id": _nonempty(raw.get("link_id"),
                                     "queue_enter.link_id"),
            }
        elif kind == "service_start" and raw.get("stage") == "isl" \
                and raw.get("queue_id") is not None:
            event_time(raw, "service_start")
            service_starts.append(raw)

    matched_qids: set[int] = set()
    max_wait_global = 0.0
    for raw in service_starts:
        qid = raw.get("queue_id")
        if qid in matched_qids:
            raise PressureAnalysisError(f"queue_id {qid} starts service twice")
        entry = queue_entries.get(qid)
        if entry is None:
            raise PressureAnalysisError(f"unknown ISL queue_id {qid}")
        pid = _pid(raw.get("pid"), "service_start.pid")
        link_id = _nonempty(raw.get("link_id"), "service_start.link_id")
        start = _finite(raw.get("at"), "service_start.at")
        if pid != entry["pid"] or link_id != entry["link_id"]:
            raise PressureAnalysisError(
                f"ISL queue/service identity mismatch for queue_id {qid}")
        if start < entry["at"]:
            raise PressureAnalysisError(
                f"ISL service precedes queue entry for queue_id {qid}")
        service = service_evidence.get((pid, link_id, start))
        if service is None:
            raise PressureAnalysisError(
                f"ISL service_start has no matching service window for "
                f"queue_id {qid}")
        start_bits = raw.get("bits")
        if start_bits != service["bits"]:
            raise PressureAnalysisError(
                f"ISL service bits mismatch for queue_id {qid}")
        start_rate = _finite(raw.get("rate_bps"), "service_start.rate_bps")
        if not math.isclose(start_rate, service["rate_bps"],
                            rel_tol=1e-9, abs_tol=1e-6):
            raise PressureAnalysisError(
                f"ISL service rate mismatch for queue_id {qid}")
        bits = emitted_bits.get(pid)
        if bits is None:
            bits = raw.get("bits")
        if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
            raise PressureAnalysisError(
                f"missing packet bits for ISL queue_id {qid}")
        wait = start - entry["at"]
        item = ensure(link_id)
        item["matched_queue_entries"] += 1
        item["max_queue_wait_s"] = max(item["max_queue_wait_s"], wait)
        item["matched_waits"].append({
            "start": entry["at"], "end": start,
            "bits": bits, "wait_s": wait,
        })
        max_wait_global = max(max_wait_global, wait)
        allocate(entry["at"], start,
                 lambda index, overlap: item["queue_area"].__setitem__(
                     index, item["queue_area"][index] + bits * overlap))
        matched_qids.add(qid)

    output_links: dict[str, Any] = {}
    sustained: list[str] = []
    pressure_candidates: list[str] = []
    active_utilizations: list[float] = []
    for link_id, raw in sorted(links.items()):
        windows: list[dict[str, float | bool]] = []
        eligible_indices: list[int] = []
        high_indices: list[int] = []
        for index in range(bin_count):
            start = index * window_s
            end = min(stop, start + window_s)
            capacity = raw["capacity"][index]
            served = raw["served"][index]
            available_time = raw["available_time"][index]
            queue_area = raw["queue_area"][index]
            if served > 1e-9 and capacity <= 0:
                raise PressureAnalysisError(
                    f"served bits without available capacity for {link_id} "
                    f"at {start}")
            if served > capacity * (1 + 1e-9):
                raise PressureAnalysisError(
                    f"served bits exceed available capacity for {link_id} "
                    f"at {start}")
            duration = end - start
            eligible = available_time >= min_available_fraction * duration - 1e-9
            utilization = served / capacity if capacity > 0 else 0.0
            if eligible:
                eligible_indices.append(index)
                if served > 0:
                    active_utilizations.append(utilization)
                if utilization >= high_utilization - 1e-12:
                    high_indices.append(index)
            if served > 0 or queue_area > 0:
                windows.append({
                    "start_s": start,
                    "end_s": end,
                    "served_bits": served,
                    "available_capacity_bits": capacity,
                    "available_time_s": available_time,
                    "utilization": utilization,
                    "eligible": eligible,
                    "matched_queue_wait_bits_s": queue_area,
                })
        longest = _longest_consecutive(high_indices)
        sustained_runs = [
            run for run in _consecutive_runs(high_indices)
            if len(run) >= min_consecutive_high_windows
        ]
        if sustained_runs:
            sustained.append(link_id)
        episodes: list[dict[str, Any]] = []
        for run in sustained_runs:
            episode_start = run[0] * window_s
            episode_end = min(stop, (run[-1] + 1) * window_s)
            overlapping_waits = [
                item for item in raw["matched_waits"]
                if min(item["end"], episode_end)
                - max(item["start"], episode_start) > 1e-12
            ]
            queue_area = sum(raw["queue_area"][index] for index in run)
            max_overlapping_wait = max(
                (item["wait_s"] for item in overlapping_waits), default=0.0)
            pressure_candidate = (
                queue_area >= min_episode_queue_area_bits_s - 1e-9
                and max_overlapping_wait >= min_episode_queue_wait_s - 1e-12
            )
            episodes.append({
                "start_s": episode_start,
                "end_s": episode_end,
                "window_count": len(run),
                "window_starts_s": [index * window_s for index in run],
                "matched_queue_wait_bits_s": queue_area,
                "overlapping_matched_queue_entries": len(overlapping_waits),
                "max_overlapping_matched_queue_wait_s": max_overlapping_wait,
                "pressure_candidate": pressure_candidate,
            })
        if any(item["pressure_candidate"] for item in episodes):
            pressure_candidates.append(link_id)
        total_capacity = sum(raw["capacity"])
        total_served = sum(raw["served"])
        output_links[link_id] = {
            "horizon_served_bits": total_served,
            "horizon_available_capacity_bits": total_capacity,
            "horizon_available_time_s": sum(raw["available_time"]),
            "horizon_utilization": (
                total_served / total_capacity if total_capacity > 0 else 0.0),
            "eligible_window_count": len(eligible_indices),
            "active_window_count": sum(
                1 for index in eligible_indices if raw["served"][index] > 0),
            "max_window_utilization": max(
                (raw["served"][index] / raw["capacity"][index]
                 for index in eligible_indices if raw["capacity"][index] > 0),
                default=0.0),
            "high_window_count": len(high_indices),
            "high_window_starts_s": [index * window_s for index in high_indices],
            "longest_consecutive_high_windows": longest,
            "matched_queue_entries": raw["matched_queue_entries"],
            "matched_queue_wait_bits_s": sum(raw["queue_area"]),
            "max_matched_queue_wait_s": raw["max_queue_wait_s"],
            "sustained_high_episodes": episodes,
            "windows": windows,
        }

    return {
        "schema": "leo-sim-isl-window-pressure/v1",
        "window_s": window_s,
        "stop_time_s": stop,
        "min_available_fraction": min_available_fraction,
        "high_utilization_threshold": high_utilization,
        "min_consecutive_high_windows": min_consecutive_high_windows,
        "min_episode_queue_wait_s": min_episode_queue_wait_s,
        "min_episode_queue_area_bits_s": min_episode_queue_area_bits_s,
        "directed_isl_link_count": len(output_links),
        "active_window_utilization_p99": _percentile(
            active_utilizations, 0.99),
        "max_window_utilization": max(active_utilizations, default=0.0),
        "sustained_hotspot_link_ids": sustained,
        "pressure_candidate_link_ids": pressure_candidates,
        "matched_isl_queue_entries": len(matched_qids),
        "unmatched_isl_queue_entries": len(set(queue_entries) - matched_qids),
        "max_matched_isl_queue_wait_s": max_wait_global,
        "links": output_links,
    }
