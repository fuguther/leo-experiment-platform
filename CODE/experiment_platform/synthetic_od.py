"""Deterministic, auditable synthetic multi-OD demand traces for T1.

This module produces explicit packet records from a frozen OD-rate matrix.
The output is synthetic development input, not measured traffic.  Each trace
is seed-bound through small arrival-time jitter while preserving its declared
OD counts, phase windows and load rates.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import random
from pathlib import Path

WORKLOAD_SCHEMA = "t1-synthetic-od-workload/v1"
TRACE_COLUMNS = (
    "packet_id", "emit_time_s", "src_lat", "src_lon", "dst_lat",
    "dst_lon", "bits", "od_id", "phase_id", "nominal_rate_pps",
)


class WorkloadError(ValueError):
    """Frozen OD workload is incomplete, inconsistent or non-finite."""


def _number(value, name, *, minimum=None, strict=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkloadError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise WorkloadError(f"{name} must be finite")
    if minimum is not None:
        if strict and result <= minimum:
            raise WorkloadError(f"{name} must be > {minimum}")
        if not strict and result < minimum:
            raise WorkloadError(f"{name} must be >= {minimum}")
    return result


def _validate(spec):
    if not isinstance(spec, dict) or spec.get("schema") != WORKLOAD_SCHEMA:
        raise WorkloadError(f"workload schema must be {WORKLOAD_SCHEMA!r}")
    workload_id = spec.get("workload_id")
    if not isinstance(workload_id, str) or not workload_id.strip():
        raise WorkloadError("workload_id must be a non-empty string")
    bits = spec.get("packet_bits")
    if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
        raise WorkloadError("packet_bits must be a positive integer")
    jitter = _number(spec.get("jitter_s", 0.0), "jitter_s", minimum=0.0)

    sites = spec.get("sites")
    if not isinstance(sites, list) or len(sites) < 3:
        raise WorkloadError("the regional matrix requires at least three sites")
    site_by_id = {}
    for site in sites:
        if not isinstance(site, dict):
            raise WorkloadError("each site must be a mapping")
        site_id = site.get("id")
        if not isinstance(site_id, str) or not site_id or site_id in site_by_id:
            raise WorkloadError("site ids must be unique non-empty strings")
        site_by_id[site_id] = {
            "lat": _number(site.get("lat"), f"site {site_id}.lat",
                           minimum=-90.0),
            "lon": _number(site.get("lon"), f"site {site_id}.lon",
                           minimum=-180.0),
        }
        if site_by_id[site_id]["lat"] > 90 or site_by_id[site_id]["lon"] > 180:
            raise WorkloadError(f"site {site_id} coordinates are out of range")

    flows = spec.get("flows")
    if not isinstance(flows, list) or len(flows) < 2:
        raise WorkloadError("the OD matrix requires at least two directed flows")
    flow_ids = []
    for flow in flows:
        if not isinstance(flow, dict):
            raise WorkloadError("each flow must be a mapping")
        flow_id, src, dst = flow.get("id"), flow.get("src"), flow.get("dst")
        if not isinstance(flow_id, str) or not flow_id or flow_id in flow_ids:
            raise WorkloadError("flow ids must be unique non-empty strings")
        if src not in site_by_id or dst not in site_by_id or src == dst:
            raise WorkloadError(f"flow {flow_id} must connect two distinct declared sites")
        flow_ids.append(flow_id)

    phases = spec.get("phases")
    if not isinstance(phases, list) or not phases:
        raise WorkloadError("at least one load phase is required")
    phase_ids = set()
    previous_end = None
    total_end = None
    for index, phase in enumerate(phases):
        if not isinstance(phase, dict):
            raise WorkloadError("each phase must be a mapping")
        phase_id = phase.get("id")
        if not isinstance(phase_id, str) or not phase_id or phase_id in phase_ids:
            raise WorkloadError("phase ids must be unique non-empty strings")
        phase_ids.add(phase_id)
        start = _number(phase.get("start_s"), f"phase {phase_id}.start_s",
                        minimum=0.0)
        end = _number(phase.get("end_s"), f"phase {phase_id}.end_s",
                      minimum=0.0, strict=True)
        if end <= start:
            raise WorkloadError(f"phase {phase_id} must have positive duration")
        if index == 0 and start != 0.0:
            raise WorkloadError("the first phase must start at 0 s")
        if previous_end is not None and not math.isclose(
                start, previous_end, rel_tol=0.0, abs_tol=1e-9):
            raise WorkloadError("load phases must be contiguous and non-overlapping")
        rates = phase.get("rates_pps")
        if not isinstance(rates, dict) or set(rates) != set(flow_ids):
            raise WorkloadError("every declared OD needs a rate in every phase")
        duration = end - start
        total_count = 0
        for flow_id in flow_ids:
            rate = _number(rates[flow_id],
                           f"phase {phase_id} rate for {flow_id}",
                           minimum=0.0)
            product = duration * rate
            if not math.isclose(product, round(product), rel_tol=0.0,
                                abs_tol=1e-9):
                raise WorkloadError(
                    f"phase {phase_id} duration x rate must yield an integer "
                    f"packet count for {flow_id}")
            count = int(round(product))
            if count >= 10_000:
                raise WorkloadError("per-flow phase packet count exceeds the stable pid range")
            if rate > 0 and jitter * 2 >= 1.0 / rate:
                raise WorkloadError(
                    f"jitter_s must be less than half an inter-arrival period "
                    f"for {flow_id} in phase {phase_id}")
            total_count += count
        if total_count <= 0:
            raise WorkloadError(f"phase {phase_id} declares no packets")
        previous_end = end
        total_end = end
    return site_by_id, flow_ids, total_end


def render_trace(spec, *, seed):
    """Return deterministic RFC4180 CSV bytes and a content-derived summary."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise WorkloadError("seed must be a non-negative integer")
    site_by_id, flow_ids, horizon = _validate(spec)
    flow_by_id = {flow["id"]: flow for flow in spec["flows"]}
    jitter = float(spec.get("jitter_s", 0.0))
    bits = int(spec["packet_bits"])
    scheduled = []
    phase_counts = {}
    od_counts = {flow_id: 0 for flow_id in flow_ids}
    phase_rates = {}
    for phase_index, phase in enumerate(spec["phases"]):
        phase_id = str(phase["id"])
        start, end = float(phase["start_s"]), float(phase["end_s"])
        duration = end - start
        phase_counts[phase_id] = 0
        rates = phase["rates_pps"]
        phase_rates[phase_id] = {
            flow_id: float(rates[flow_id]) for flow_id in flow_ids}
        for flow_index, flow_id in enumerate(flow_ids):
            rate = float(rates[flow_id])
            if rate <= 0:
                continue
            count = int(round(duration * rate))
            period = 1.0 / rate
            flow = flow_by_id[flow_id]
            src = site_by_id[flow["src"]]
            dst = site_by_id[flow["dst"]]
            for event_index in range(count):
                nominal_at = start + (event_index + 0.5) * period
                rng = random.Random(
                    f"t1-od-v1|{seed}|{phase_id}|{flow_id}|{event_index}")
                jittered_at = nominal_at + rng.uniform(-jitter, jitter)
                emit_at = min(max(jittered_at, start + 1e-9), end - 1e-9)
                packet_id = (phase_index * 1_000_000
                             + flow_index * 10_000 + event_index + 1)
                scheduled.append({
                    "packet_id": packet_id,
                    "emit_time_s": emit_at,
                    "src_lat": src["lat"], "src_lon": src["lon"],
                    "dst_lat": dst["lat"], "dst_lon": dst["lon"],
                    "bits": bits, "od_id": flow_id,
                    "phase_id": phase_id,
                    "nominal_rate_pps": rate,
                    "nominal_emit_time_s": nominal_at,
                    "phase_index": phase_index,
                })
                phase_counts[phase_id] += 1
                od_counts[flow_id] += 1

    scheduled.sort(key=lambda row: (row["emit_time_s"], row["packet_id"]))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=TRACE_COLUMNS,
                            lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(scheduled)
    content = stream.getvalue().encode("utf-8")
    per_second_active = {}
    for row in scheduled:
        key = str(int(math.floor(float(row["emit_time_s"]))))
        per_second_active.setdefault(key, set()).add(row["od_id"])
    summary = {
        "schema": WORKLOAD_SCHEMA,
        "workload_id": str(spec["workload_id"]),
        "synthetic": True,
        "seed": seed,
        "packet_bits": bits,
        "emission_end_s": float(horizon),
        "packet_count": len(scheduled),
        "unique_od_count": len(flow_ids),
        "active_od_counts_by_1s_bin": {
            key: len(value) for key, value in sorted(
                per_second_active.items(), key=lambda item: int(item[0]))},
        "maximum_active_od_count": max(
            (len(value) for value in per_second_active.values()), default=0),
        "sites": [dict(site) for site in spec["sites"]],
        "flows": [dict(flow) for flow in spec["flows"]],
        "phases": [{"id": str(phase["id"]),
                    "start_s": float(phase["start_s"]),
                    "end_s": float(phase["end_s"]),
                    "rates_pps": phase_rates[str(phase["id"])],
                    "packet_count": phase_counts[str(phase["id"])],
                    "nominal_mbps": (sum(phase_rates[str(phase["id"])].values())
                                     * bits / 1_000_000.0)}
                   for phase in spec["phases"]],
        "phase_packet_counts": phase_counts,
        "od_packet_counts": od_counts,
        "packet_manifest": [
            {"pid": row["packet_id"],
             "emit_time_s": row["emit_time_s"],
             "bits": bits, "od_id": row["od_id"],
             "phase_id": row["phase_id"]}
            for row in scheduled],
        "input_sha256": hashlib.sha256(content).hexdigest(),
    }
    return content, summary


def write_trace(path, spec, *, seed):
    """Create one immutable generated trace and return its declared summary."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite synthetic trace: {path}")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise FileNotFoundError(f"trace parent must exist: {path.parent}")
    content, summary = render_trace(spec, seed=seed)
    path.write_bytes(content)
    return summary


def inspect_trace(path):
    """Read an emitted synthetic CSV and rebuild its workload ledger."""
    path = Path(path)
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        required = set(TRACE_COLUMNS)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise WorkloadError(f"synthetic trace missing fields {sorted(missing)}")
        packet_manifest = []
        od_counts = {}
        phase_counts = {}
        active_od = {}
        seen = set()
        max_at = -math.inf
        for row in reader:
            try:
                pid = int(row["packet_id"])
                at = float(row["emit_time_s"])
                bits = int(row["bits"])
                rate = float(row["nominal_rate_pps"])
                od_id, phase_id = row["od_id"], row["phase_id"]
            except (TypeError, ValueError) as exc:
                raise WorkloadError(f"invalid synthetic trace row: {exc}") from exc
            if pid <= 0 or pid in seen or not math.isfinite(at) or at < 0 \
                    or bits <= 0 or not math.isfinite(rate) or rate < 0 \
                    or not od_id or not phase_id:
                raise WorkloadError("invalid or duplicate synthetic packet record")
            seen.add(pid)
            max_at = max(max_at, at)
            od_counts[od_id] = od_counts.get(od_id, 0) + 1
            phase_counts[phase_id] = phase_counts.get(phase_id, 0) + 1
            active_od.setdefault(str(int(math.floor(at))), set()).add(od_id)
            packet_manifest.append({"pid": pid, "emit_time_s": at,
                                    "bits": bits, "od_id": od_id,
                                    "phase_id": phase_id})
    return {
        "synthetic": True,
        "trace_path": str(path),
        "input_sha256": digest,
        "packet_count": len(packet_manifest),
        "maximum_emit_time_s": (None if not packet_manifest else max_at),
        "unique_od_count": len(od_counts),
        "active_od_counts_by_1s_bin": {
            key: len(value) for key, value in sorted(
                active_od.items(), key=lambda item: int(item[0]))},
        "maximum_active_od_count": max(
            (len(value) for value in active_od.values()), default=0),
        "od_packet_counts": od_counts,
        "phase_packet_counts": phase_counts,
        "packet_manifest": packet_manifest,
    }
