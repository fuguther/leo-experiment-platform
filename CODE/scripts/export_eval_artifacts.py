#!/usr/bin/env python3
"""Export figure-source aggregates from immutable ``leo_sim`` run evidence.

The exporter intentionally reads only files published by
``CODE.leo_sim.receipt.write_run`` (``trace.csv``, ``ledgers.json`` and
``receipt.json``), plus an optional, explicitly associated ``telemetry.jsonl``.
It does not launch the simulator, infer missing packet fields, or use summary
CSV files as evidence.

Implementation assumptions fixed here
=====================================

* A packet belongs to a reporting window by its ``emit_time_s``.  Goodput-PDR
  counts a packet as an on-time delivery only when its complete fate is
  ``DELIVERED`` and its delivery instant is no later than that packet's
  ``deadline_at_s`` (falling back to the platform's explicit
  ``research_deadline_at_s`` when the hard-target column is present).  A
  missing deadline, delivery instant, or fate makes that field incomplete,
  never zero.
* Recovery uses 100 ms emission-time buckets.  A recovery is confirmed only
  when every one of eight consecutive buckets meets 95% of the pre-pulse PDR.
  The last allowed onset is 8.7 s: the confirmation then ends at the 9.5 s
  observation boundary.  Failure to find such a window is right-censored as
  ``> 5.2 s`` after the shock-end reference point at 3.5 s.
* Recovery Goodput Deficit is integrated as
  ``max(0, pre_pulse_PDR - recovery_PDR) * seconds`` over
  ``[3.5, 9.5)``.  Its unit is delivery-ratio seconds.  The baseline is the
  mean of the same trace's fifteen 100 ms pre-pulse PDR buckets in
  ``[0.5, 2.0)``.  It is not a packet-weighted pool over treatments and not
  another algorithm's result.
* Victim latency pairs require the same Exp-0B scene, seed, interval,
  algorithm, training metadata, variant, outage condition and any explicit
  cohort/workload identifier.  The global trace hash is deliberately not used
  as the cohort identity because pulse traffic changes that hash.
* ZWR is computed from raw service windows, not delivery-only logs.  Every
  window contributes its served bits when it completed and its simulated
  capacity/work otherwise; therefore failed, expired, uplink and downlink
  service remain in the denominator.  The ledger does not store complete paths
  for dropped packets, so the paper's path-hop ZWR is marked unavailable.
  Arrival-hop visits for dropped packets are reported separately from unique
  propagation-arrival events, and are blank when event evidence is missing.
* Jain fairness uses an explicit ``corridor_edges`` declaration when present,
  otherwise the complete supported directed-ISL edge set present in
  ``link_available_windows`` during ``[2.0, 3.5)``.  Edges are never selected
  from the traffic outcome.  Utilization is service-capacity time divided by
  available-capacity time with both quantities clipped to the same interval.
* Five test traces are summarized descriptively per trace.  Packets are not
  treated as independent repetitions.  The 95% interval reported for exactly
  five traces is a sample mean +/- t(0.975, 4) standard error, for display only.
  A right-censored T_rec is counted as censored and is never silently omitted
  from an apparently complete mean.
* Telemetry vectors are interpreted in the declared N/S/E/W port order.  The
  ``phi_spill`` record is treated as a per-port spill rate/value and its time
  integral is named ``port_s``; the source telemetry contract must change if a
  producer later declares different units.  Optional ``compute_delay_s`` (or
  ``decision_delay_s``) is summarized only when present.
* Missing or empty evidence produces rows with an explicit ``status``.  Invalid
  identities and mismatched cohorts, corridor universes, or topology support
  raise ``ExportValidationError`` instead of producing a misleading number.

The CSV files are individual figure sources, not paper claims.  CCDF rows are
per-run empirical quantiles and retain the run provenance; no run is silently
pooled with another.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import os
import statistics
import tempfile
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


EXPERIMENTS = frozenset({
    "exp0", "exp0_hysteresis", "exp0b", "exp1", "exp2", "exp3_outage", "exp3_zeroshot",
    "exp4_ablation", "exp4_compute",
})
TERMINAL_LOSS_FATES = frozenset({
    "ACCESS_REJECTED", "ACCESS_QUEUE_OVERFLOW", "ISL_QUEUE_OVERFLOW",
    "HOLDING_QUEUE_OVERFLOW", "GEOMETRY_LOSS_IN_FLIGHT",
    "RANDOM_OUTAGE_IN_FLIGHT", "NO_ROUTE", "DATA_DEADLINE_EXPIRED",
})
ALL_DATA_FATES = (
    "DELIVERED", "ACCESS_REJECTED", "ACCESS_QUEUE_OVERFLOW",
    "ISL_QUEUE_OVERFLOW", "HOLDING_QUEUE_OVERFLOW",
    "GEOMETRY_LOSS_IN_FLIGHT", "RANDOM_OUTAGE_IN_FLIGHT", "NO_ROUTE",
    "DATA_DEADLINE_EXPIRED", "IN_SYSTEM_AT_STOP",
)
DIRECTIONS = ("N", "S", "E", "W")
SHOCK_START_S = 2.0
SHOCK_END_S = 3.5
OBSERVATION_END_S = 9.5
PREPULSE_START_S = 0.5
PREPULSE_END_S = 2.0
RECOVERY_BIN_S = 0.1
RECOVERY_CONFIRM_S = 0.8
RECOVERY_ONSET_MAX_S = OBSERVATION_END_S - RECOVERY_CONFIRM_S
RECOVERY_RIGHT_CENSOR_S = round(RECOVERY_ONSET_MAX_S - SHOCK_END_S, 9)
JAIN_START_S = SHOCK_START_S
JAIN_END_S = SHOCK_END_S
CCDF_POINTS = 200
COHORT_FIELDS = (
    "scene", "seed", "interval_s", "alg", "train_seed", "variant", "outage",
    "cohort_id", "workload_base_id", "base_trace_sha256", "aoi_ms",
    "test_seed", "quality", "workload_id", "demand_model", "topology_id",
    "route_set_id", "config_identity",
)
STANDARD_INDEX_FIELDS = frozenset({
    "exp", "scene", "seed", "interval", "interval_s", "alg", "multiplier",
    "train_seed", "variant", "outage", "run_dir", "run_id", "quality",
    "quality_status", "status", "cohort_id", "workload_base_id",
    "base_trace_sha256", "aoi_ms", "test_seed", "workload_id",
    "demand_model", "topology_id", "route_set_id", "config_identity",
    "corridor_edges", "telemetry_file", "telemetry_path", "train_curve",
})
VOLATILE_QUALITY_FIELDS = frozenset({
    "multiplier", "trace_sha256", "global_trace_sha256",
    "trace_identity_sha256", "run_dir", "run_id", "status", "quality_status",
})

REQUIRED_FILES = (
    "exp0_calibration.csv",
    "exp0_hysteresis_multi_alg.csv",
    "exp0b_shock_sweep_seed101.csv",
    "exp2_main_raw.csv",
    "exp2_main_table_mean_std.csv",
    "exp2_latency_ccdf.csv",
    "exp2_breakdown_mean_std.csv",
    "exp2_internal_q_trace_seed101.csv",
    "exp2_aoi_sweep.csv",
    "exp2_timeseries_100ms_seed101.csv",
    "exp3_link_outage.csv",
    "exp3_zeroshot_large.csv",
    "exp4_ablation_mean_std.csv",
    "exp4_compute_overhead.csv",
)
FIGURE_SOURCE_FILES = {
    1: ("exp0b_shock_sweep_seed101.csv",),
    2: ("exp0_hysteresis_multi_alg.csv",),
    3: ("exp2_main_table_mean_std.csv",),
    4: ("exp2_latency_ccdf.csv",),
    5: ("exp2_breakdown_mean_std.csv",),
    6: ("exp2_internal_q_trace_seed101.csv",),
    7: (
        "exp2_aoi_sweep.csv", "exp3_link_outage.csv",
        "exp3_zeroshot_large.csv",
    ),
    8: ("exp4_ablation_mean_std.csv", "exp4_compute_overhead.csv"),
}

SCENE_WINDOWS = {
    "A": (
        ("full", 0.2, 9.5),
        ("steady_prepulse", 0.5, 2.0),
        ("burst", 2.0, 3.5),
        ("recovery", 3.5, 9.5),
    ),
    "B": (
        ("emission_window", 0.2, 9.5),
        ("steady_prepulse", 0.5, 2.0),
        ("burst", 2.0, 3.5),
        ("recovery", 3.5, 9.5),
        ("full", 0.2, 9.5),
    ),
    "C": (
        ("emission_window", 0.2, 9.5),
        ("steady_prepulse", 0.5, 2.0),
        ("burst", 2.0, 3.5),
        ("recovery", 3.5, 9.5),
        ("full", 0.2, 9.5),
    ),
}

SUMMARY_METRICS = (
    "goodput_pdr_full_pct", "goodput_pdr_prepulse_pct",
    "goodput_pdr_burst_pct", "goodput_pdr_recovery_pct",
    "goodput_pdr_emission_window_pct", "t_rec_s", "rgd_delivery_ratio_seconds",
    "e2e_p50_s", "e2e_p95_s", "e2e_p99_s", "queue_wait_p50_s",
    "queue_wait_p95_s", "queue_wait_p99_s", "queue_cvar_090_s",
    "recovery_e2e_p50_s", "recovery_e2e_p95_s", "recovery_e2e_p99_s",
    "recovery_queue_wait_p99_s", "recovery_queue_cvar_090_s",
    "jain_fairness_index", "hir_actual_over_sp_hop",
    "zombie_work_service_bits_ratio_pct",
)


class ExportValidationError(ValueError):
    """The index or a comparison cohort is structurally invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportValidationError(f"{name} must be numeric")
    out = float(value)
    if not math.isfinite(out):
        raise ExportValidationError(f"{name} must be finite")
    return out


def _maybe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
    value = float(value)
    return value if math.isfinite(value) else None


def _maybe_int(value: Any) -> int | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_file(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"missing {path.name}"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"invalid {path.name}: {type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, f"invalid {path.name}: top level must be an object"
    return value, None


def _read_trace(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], f"missing {path.name}"
    required = {
        "packet_id", "emit_time_s", "src_grid_id", "dst_grid_id", "bits",
        "deadline_at_s",
    }
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or ())
            if not required <= columns:
                missing = sorted(required - columns)
                return [], f"trace.csv missing columns {missing}"
            for line_number, raw in enumerate(reader, start=2):
                pid = _maybe_int(raw.get("packet_id"))
                emit = _maybe_float(raw.get("emit_time_s"))
                bits = _maybe_int(raw.get("bits"))
                deadline = _maybe_float(raw.get("deadline_at_s"))
                research_deadline = _maybe_float(raw.get("research_deadline_at_s"))
                if pid is None or pid <= 0 or pid in seen:
                    return [], f"trace.csv line {line_number}: invalid/duplicate packet_id"
                if emit is None or emit < 0.0 or bits is None or bits <= 0:
                    return [], f"trace.csv line {line_number}: invalid emission/bit field"
                if raw.get("deadline_at_s") not in (None, "") and deadline is None:
                    return [], f"trace.csv line {line_number}: invalid deadline"
                if (raw.get("research_deadline_at_s") not in (None, "")
                        and research_deadline is None):
                    return [], (
                        f"trace.csv line {line_number}: invalid research deadline")
                if deadline is not None and deadline < emit:
                    return [], f"trace.csv line {line_number}: deadline precedes emission"
                if research_deadline is not None and research_deadline < emit:
                    return [], (
                        f"trace.csv line {line_number}: research deadline precedes emission")
                seen.add(pid)
                rows.append({
                    "packet_id": pid,
                    "emit_time_s": emit,
                    "src_grid_id": raw.get("src_grid_id"),
                    "dst_grid_id": raw.get("dst_grid_id"),
                    "bits": bits,
                    "deadline_at_s": deadline,
                    "research_deadline_at_s": research_deadline,
                })
    except (OSError, UnicodeError, csv.Error) as exc:
        return [], f"invalid trace.csv: {type(exc).__name__}: {exc}"
    return rows, None


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def _telemetry_path(entry: Mapping[str, Any], run_dir: Path,
                    root: Path) -> Path | None:
    explicit = entry.get("telemetry_file") or entry.get("telemetry_path")
    if explicit:
        path = Path(str(explicit)).expanduser()
        if path.is_absolute():
            return path
        for candidate in (run_dir / path, root / path):
            if candidate.is_file():
                return candidate
        return run_dir / path
    candidates = (
        run_dir / "telemetry.jsonl",
        run_dir.parent / f"{run_dir.name}.telemetry.jsonl",
    )
    return next((path for path in candidates if path.is_file()), None)


def _read_telemetry(path: Path | None) -> tuple[list[dict[str, Any]], str | None]:
    if path is None:
        return [], "not supplied"
    if not path.exists():
        return [], f"missing {path.name}"
    rows: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    return [], f"telemetry line {line_number} is not an object"
                rows.append(value)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [], f"invalid telemetry.jsonl: {type(exc).__name__}: {exc}"
    return rows, None if rows else "telemetry.jsonl is empty"


@dataclass
class RunEvidence:
    entry: dict[str, Any]
    run_dir: Path
    trace: list[dict[str, Any]] = field(default_factory=list)
    ledger: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    telemetry: list[dict[str, Any]] = field(default_factory=list)
    telemetry_path: Path | None = None
    trace_sha256: str | None = None
    errors: list[str] = field(default_factory=list)
    status: str = "MISSING"
    fates_complete: bool = False
    hash_matches_receipt: bool = False
    ledger_hash_status: str = "UNBOUND"
    conservation_ok: bool = False
    events_status: str = "MISSING"
    windows_status: str = "MISSING"
    available_windows_status: str = "MISSING"
    telemetry_status: str = "MISSING"
    window_cache: dict[tuple[float, float], dict[str, Any]] = field(default_factory=dict)
    latency_cache: dict[tuple[float, float], tuple[list[float], str]] = field(default_factory=dict)
    queue_cache: dict[tuple[float, float], tuple[list[float], str]] = field(default_factory=dict)
    trace_sorted: list[dict[str, Any]] | None = None
    trace_times: list[float] | None = None

    @property
    def exp(self) -> str:
        return str(self.entry["exp"])

    @property
    def scene(self) -> str:
        return str(self.entry["scene"])

    @property
    def seed(self) -> int:
        return int(self.entry["seed"])

    @property
    def interval_s(self) -> float:
        return float(self.entry["interval_s"])

    @property
    def alg(self) -> str:
        return str(self.entry["alg"])

    @property
    def multiplier(self) -> float:
        return float(self.entry["multiplier"])

    @property
    def run_key(self) -> str:
        explicit = self.entry.get("run_id")
        return str(explicit) if explicit else self.run_dir.name

    @cached_property
    def fates(self) -> dict[int, str]:
        raw = (self.ledger or {}).get("packet_fates")
        out: dict[int, str] = {}
        if not isinstance(raw, dict):
            return out
        for key, value in raw.items():
            pid = _maybe_int(key)
            if pid is not None and isinstance(value, (list, tuple)) and value:
                out[pid] = str(value[0])
        return out

    @cached_property
    def deliveries(self) -> dict[int, dict[str, Any]]:
        raw = (self.ledger or {}).get("deliveries")
        if not isinstance(raw, dict):
            return {}
        out: dict[int, dict[str, Any]] = {}
        for key, value in raw.items():
            pid = _maybe_int(key)
            if pid is not None and isinstance(value, dict):
                out[pid] = value
        return out

    @property
    def fates_ready(self) -> bool:
        return (self.fates_complete and self.conservation_ok
                and self.hash_matches_receipt
                and self.ledger_hash_status == "MATCH")

    @cached_property
    def trace_by_pid(self) -> dict[int, dict[str, Any]]:
        return {int(row["packet_id"]): row for row in self.trace}


def _normalize_entry(root: Path, raw: Mapping[str, Any], ordinal: int) -> tuple[dict[str, Any], Path]:
    if not isinstance(raw, Mapping):
        raise ExportValidationError(f"index[{ordinal}] must be an object")
    entry = dict(raw)
    missing = [name for name in (
        "exp", "scene", "seed", "interval", "alg", "multiplier", "run_dir")
        if name not in entry]
    if missing:
        raise ExportValidationError(f"index[{ordinal}] missing {missing}")
    if entry["exp"] not in EXPERIMENTS:
        raise ExportValidationError(f"index[{ordinal}] unsupported exp {entry['exp']!r}")
    if entry["scene"] not in {"A", "B", "C"}:
        raise ExportValidationError(f"index[{ordinal}] scene must be A, B, or C")
    entry["seed"] = int(_finite(entry["seed"], f"index[{ordinal}].seed"))
    entry["interval_s"] = _finite(entry["interval"], f"index[{ordinal}].interval")
    if entry["interval_s"] <= 0:
        raise ExportValidationError(f"index[{ordinal}].interval must be positive")
    entry["multiplier"] = _finite(entry["multiplier"], f"index[{ordinal}].multiplier")
    if entry["multiplier"] < 0:
        raise ExportValidationError(f"index[{ordinal}].multiplier must be non-negative")
    entry["alg"] = str(entry["alg"])
    if not entry["alg"]:
        raise ExportValidationError(f"index[{ordinal}].alg must be non-empty")
    run_dir = _resolve_path(root, str(entry["run_dir"]))
    return entry, run_dir


def _load_run(root: Path, raw: Mapping[str, Any], ordinal: int) -> RunEvidence:
    entry, run_dir = _normalize_entry(root, raw, ordinal)
    evidence = RunEvidence(entry=entry, run_dir=run_dir)

    trace, trace_error = _read_trace(run_dir / "trace.csv")
    evidence.trace = trace
    if trace_error:
        evidence.errors.append(trace_error)
    evidence.trace_sha256 = _sha256_file(run_dir / "trace.csv")

    ledger, ledger_error = _read_json(run_dir / "ledgers.json")
    receipt, receipt_error = _read_json(run_dir / "receipt.json")
    evidence.ledger = ledger
    evidence.receipt = receipt
    for error in (ledger_error, receipt_error):
        if error:
            evidence.errors.append(error)

    events = (ledger or {}).get("packet_events")
    windows = (ledger or {}).get("link_service_windows")
    available = (ledger or {}).get("link_available_windows")
    if isinstance(events, list):
        evidence.events_status = "COMPUTED" if events else "EMPTY"
    if isinstance(windows, list):
        evidence.windows_status = "COMPUTED" if windows else "EMPTY"
    if isinstance(available, list):
        evidence.available_windows_status = "COMPUTED" if available else "EMPTY"

    evidence.telemetry_path = _telemetry_path(entry, run_dir, root)
    evidence.telemetry, telemetry_error = _read_telemetry(evidence.telemetry_path)
    if evidence.telemetry:
        evidence.telemetry_status = "COMPUTED"
    elif evidence.telemetry_path is None:
        evidence.telemetry_status = "MISSING"
    elif telemetry_error and "empty" in telemetry_error:
        evidence.telemetry_status = "EMPTY"
    else:
        evidence.telemetry_status = "INVALID"
    if telemetry_error and evidence.telemetry_path is not None:
        evidence.errors.append(telemetry_error)

    trace_bits = {int(row["packet_id"]): int(row["bits"]) for row in trace}
    raw_fates = (ledger or {}).get("packet_fates")
    trace_ids = {int(row["packet_id"]) for row in trace}
    fate_ids: set[int] = set()
    fates_valid = isinstance(raw_fates, dict)
    if isinstance(raw_fates, dict):
        for key, value in raw_fates.items():
            pid = _maybe_int(key)
            if (pid is None or not isinstance(value, (list, tuple)) or len(value) != 2
                    or not isinstance(value[0], str)
                    or value[0] not in ALL_DATA_FATES
                    or (_maybe_int(value[1]) is None or _maybe_int(value[1]) <= 0)
                    or (pid in trace_bits and _maybe_int(value[1]) != trace_bits[pid])):
                fates_valid = False
                break
            fate_ids.add(pid)
    receipt_fates = (receipt or {}).get("packet_fates")
    if receipt_fates is not None and receipt_fates != raw_fates:
        fates_valid = False
        evidence.errors.append("receipt packet_fates differ from ledgers packet_fates")
    receipt_fate_counts = (receipt or {}).get("fate_counts")
    if receipt_fate_counts is not None and isinstance(raw_fates, dict):
        observed_counts = _counts(
            value[0] for value in raw_fates.values()
            if isinstance(value, (list, tuple)) and value and isinstance(value[0], str))
        normalized_receipt_counts = {
            fate: receipt_fate_counts.get(fate, 0) for fate in ALL_DATA_FATES}
        normalized_ledger_counts = {
            fate: observed_counts.get(fate, 0) for fate in ALL_DATA_FATES}
        if normalized_receipt_counts != normalized_ledger_counts:
            fates_valid = False
            evidence.errors.append("receipt fate_counts differ from ledger fates")
    evidence.fates_complete = (
        fates_valid and trace_ids == fate_ids and not trace_error and ledger is not None)
    if not evidence.fates_complete:
        evidence.errors.append(
            "incomplete packet fates or trace/fate packet-set mismatch")

    receipt_trace_sha = (receipt or {}).get("trace_sha256")
    evidence.hash_matches_receipt = (
        isinstance(receipt_trace_sha, str)
        and evidence.trace_sha256 is not None
        and receipt_trace_sha == evidence.trace_sha256)
    receipt_ledger_sha = (receipt or {}).get("ledgers_sha256")
    if isinstance(receipt_ledger_sha, str):
        ledger_sha = _sha256_file(run_dir / "ledgers.json")
        evidence.ledger_hash_status = (
            "MATCH" if ledger_sha is not None and ledger_sha == receipt_ledger_sha
            else "MISMATCH")
        if evidence.ledger_hash_status == "MISMATCH":
            evidence.errors.append("receipt ledgers_sha256 does not match ledgers.json")
    elif receipt is not None:
        evidence.ledger_hash_status = "UNBOUND"
    evidence.conservation_ok = bool((receipt or {}).get("conservation_ok"))
    if receipt is None or not evidence.hash_matches_receipt or not evidence.conservation_ok:
        evidence.errors.append(
            "receipt does not verify trace identity and conservation")

    if not trace and trace_error is None:
        evidence.status = "EMPTY_TRACE"
    elif trace_error:
        evidence.status = "MISSING_TRACE" if trace_error.startswith("missing") else "INVALID_TRACE"
    elif ledger_error:
        evidence.status = "MISSING_LEDGERS" if ledger_error.startswith("missing") else "INVALID_LEDGERS"
    elif receipt_error:
        evidence.status = "MISSING_RECEIPT" if receipt_error.startswith("missing") else "INVALID_RECEIPT"
    elif not evidence.fates_complete:
        evidence.status = "INCOMPLETE_FATES"
    elif not evidence.hash_matches_receipt:
        evidence.status = "INVALID_TRACE_HASH"
    elif evidence.ledger_hash_status == "MISMATCH":
        evidence.status = "INVALID_LEDGER_HASH"
    elif evidence.ledger_hash_status != "MATCH":
        evidence.status = "INCOMPLETE_LEDGER_BINDING"
    elif not evidence.conservation_ok:
        evidence.status = "INVALID_CONSERVATION"
    else:
        evidence.status = "COMPUTED"
    return evidence


def _deadline(row: Mapping[str, Any]) -> float | None:
    value = _maybe_float(row.get("deadline_at_s"))
    return value if value is not None else _maybe_float(row.get("research_deadline_at_s"))


def _delivery_time(evidence: RunEvidence, pid: int) -> float | None:
    record = evidence.deliveries.get(pid)
    if not isinstance(record, dict):
        return None
    return _maybe_float(record.get("delivered_at"))


def _trace_rows_between(
        evidence: RunEvidence, start: float, end: float,
) -> list[dict[str, Any]]:
    if evidence.trace_sorted is None or evidence.trace_times is None:
        evidence.trace_sorted = sorted(
            evidence.trace,
            key=lambda row: (float(row["emit_time_s"]), int(row["packet_id"])))
        evidence.trace_times = [
            float(row["emit_time_s"]) for row in evidence.trace_sorted]
    left = bisect.bisect_left(evidence.trace_times, start)
    right = bisect.bisect_left(evidence.trace_times, end)
    return evidence.trace_sorted[left:right]


def _window_stats(evidence: RunEvidence, start: float, end: float) -> dict[str, Any]:
    cache_key = (float(start), float(end))
    if cache_key in evidence.window_cache:
        return evidence.window_cache[cache_key]
    rows = _trace_rows_between(evidence, start, end)
    result: dict[str, Any] = {
        "status": "COMPUTED", "window_start_s": start, "window_end_s": end,
        "packets_offered": len(rows), "delivered_any": None,
        "delivered_by_deadline": None, "goodput_pdr_fraction": None,
        "goodput_pdr_pct": None, "delivered_by_deadline_bits": None,
    }
    if not evidence.fates_ready:
        result.update(status=(
            evidence.status if evidence.status != "COMPUTED" else "INCOMPLETE_FATES"))
        evidence.window_cache[cache_key] = result
        return result
    if not rows:
        result.update(status="EMPTY_WINDOW")
        evidence.window_cache[cache_key] = result
        return result
    fates = evidence.fates
    delivered_any = sum(1 for row in rows if fates.get(int(row["packet_id"])) == "DELIVERED")
    ontime = 0
    ontime_bits = 0
    for row in rows:
        pid = int(row["packet_id"])
        if fates.get(pid) != "DELIVERED":
            continue
        delivered_at = _delivery_time(evidence, pid)
        deadline = _deadline(row)
        if delivered_at is None:
            result.update(status="INCOMPLETE_DELIVERY_TIME")
            evidence.window_cache[cache_key] = result
            return result
        if deadline is None:
            result.update(status="MISSING_DEADLINE")
            evidence.window_cache[cache_key] = result
            return result
        if delivered_at <= deadline + 1e-12:
            ontime += 1
            ontime_bits += int(row["bits"])
    ratio = ontime / len(rows)
    result.update(
        delivered_any=delivered_any,
        delivered_by_deadline=ontime,
        delivered_by_deadline_bits=ontime_bits,
        goodput_pdr_fraction=ratio,
        goodput_pdr_pct=100.0 * ratio,
    )
    evidence.window_cache[cache_key] = result
    return result


def _nearest_rank(values: Sequence[float], probability: float) -> float | None:
    if not values or not 0.0 <= probability <= 1.0:
        return None
    ordered = sorted(float(value) for value in values)
    if probability == 0.0:
        return ordered[0]
    rank = max(1, math.ceil(probability * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def _upper_cvar(values: Sequence[float], tail_fraction: float = 0.10) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    count = max(1, math.ceil(tail_fraction * len(ordered)))
    return sum(ordered[-count:]) / count


def _latency_values(evidence: RunEvidence, start: float, end: float) -> tuple[list[float], str]:
    cache_key = (float(start), float(end))
    if cache_key in evidence.latency_cache:
        values, status = evidence.latency_cache[cache_key]
        return list(values), status
    if not evidence.fates_ready:
        result = ([], (evidence.status if evidence.status != "COMPUTED" else "INCOMPLETE_FATES"))
        evidence.latency_cache[cache_key] = result
        return result
    values: list[float] = []
    for row in _trace_rows_between(evidence, start, end):
        emit = float(row["emit_time_s"])
        pid = int(row["packet_id"])
        if evidence.fates.get(pid) != "DELIVERED":
            continue
        delivered_at = _delivery_time(evidence, pid)
        if delivered_at is None or delivered_at < emit:
            result = ([], "INCOMPLETE_DELIVERY_TIME")
            evidence.latency_cache[cache_key] = result
            return result
        values.append(delivered_at - emit)
    result = (values, "COMPUTED" if values else "NO_DELIVERED_PACKETS")
    evidence.latency_cache[cache_key] = result
    return list(values), result[1]


def _queue_wait_values(evidence: RunEvidence, start: float, end: float) -> tuple[list[float], str]:
    cache_key = (float(start), float(end))
    if cache_key in evidence.queue_cache:
        values, status = evidence.queue_cache[cache_key]
        return list(values), status
    if not evidence.fates_ready:
        result = ([], (evidence.status if evidence.status != "COMPUTED" else "INCOMPLETE_FATES"))
        evidence.queue_cache[cache_key] = result
        return result
    raw_events = (evidence.ledger or {}).get("packet_events")
    if not isinstance(raw_events, list):
        result = ([], "MISSING_PACKET_EVENTS")
        evidence.queue_cache[cache_key] = result
        return result
    eligible = {
        int(row["packet_id"]) for row in _trace_rows_between(evidence, start, end)
        if evidence.fates.get(int(row["packet_id"])) == "DELIVERED"}
    queue_enter: dict[tuple[int, Any], float] = {}
    service_start: dict[tuple[int, Any], float] = {}
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        pid = _maybe_int(event.get("pid"))
        if pid not in eligible:
            continue
        at = _maybe_float(event.get("at"))
        qid = event.get("queue_id")
        if at is None or qid is None:
            continue
        key = (pid, qid)
        if event.get("kind") == "queue_enter":
            queue_enter[key] = at
        elif event.get("kind") == "service_start":
            service_start[key] = at
    values = [service_start[key] - entered for key, entered in queue_enter.items()
              if key in service_start and service_start[key] >= entered]
    if not eligible:
        result = ([], "NO_DELIVERED_PACKETS")
        evidence.queue_cache[cache_key] = result
        return result
    covered = {pid for pid, _qid in queue_enter if pid in eligible}
    paired = {pid for pid, qid in queue_enter
              if (pid, qid) in service_start and pid in eligible}
    if covered != eligible or paired != eligible:
        result = (values, "INCOMPLETE_QUEUE_EVIDENCE")
        evidence.queue_cache[cache_key] = result
        return list(values), result[1]
    result = (values, "COMPUTED")
    evidence.queue_cache[cache_key] = result
    return list(values), result[1]


def _latency_stats(evidence: RunEvidence, start: float, end: float) -> dict[str, Any]:
    e2e, e2e_status = _latency_values(evidence, start, end)
    queue, queue_status = _queue_wait_values(evidence, start, end)
    return {
        "latency_window_start_s": start,
        "latency_window_end_s": end,
        "e2e_latency_status": e2e_status,
        "e2e_latency_samples": len(e2e),
        "e2e_p50_s": _nearest_rank(e2e, 0.50),
        "e2e_p95_s": _nearest_rank(e2e, 0.95),
        "e2e_p99_s": _nearest_rank(e2e, 0.99),
        "queue_wait_status": queue_status,
        "queue_wait_samples": len(queue),
        "queue_wait_mean_s": statistics.fmean(queue) if queue else None,
        "queue_wait_p50_s": _nearest_rank(queue, 0.50),
        "queue_wait_p95_s": _nearest_rank(queue, 0.95),
        "queue_wait_p99_s": _nearest_rank(queue, 0.99),
        "queue_cvar_090_s": _upper_cvar(queue, 0.10),
    }


def _recovery_metrics(evidence: RunEvidence) -> dict[str, Any]:
    prepulse = _window_stats(evidence, PREPULSE_START_S, PREPULSE_END_S)
    result: dict[str, Any] = {
        "prepulse_pdr_fraction": prepulse["goodput_pdr_fraction"],
        "prepulse_pdr_basis": "mean_of_100ms_emission_time_buckets",
        "window_goodput_pdr_basis": "pooled_packets_by_emission_time",
        "prepulse_status": prepulse["status"],
        "recovery_bin_s": RECOVERY_BIN_S,
        "recovery_confirmation_window_s": RECOVERY_CONFIRM_S,
        "recovery_onset_max_s": RECOVERY_ONSET_MAX_S,
        "recovery_onset_max_after_shock_end_s": (
            RECOVERY_ONSET_MAX_S - SHOCK_END_S),
        "t_rec_s": None,
        "t_rec_right_censor_s": None,
        "t_rec_display": None,
        "t_rec_status": "MISSING_BASELINE",
        "rgd_delivery_ratio_seconds": None,
        "rgd_unit": "delivery_ratio_second",
        "rgd_status": "MISSING_BASELINE",
    }
    if not evidence.fates_ready:
        return result
    prepulse_count = int(round(
        (PREPULSE_END_S - PREPULSE_START_S) / RECOVERY_BIN_S))
    prepulse_rates: list[float | None] = []
    for index in range(prepulse_count):
        start = PREPULSE_START_S + index * RECOVERY_BIN_S
        stats = _window_stats(evidence, start, start + RECOVERY_BIN_S)
        prepulse_rates.append(stats["goodput_pdr_fraction"])
    if any(rate is None for rate in prepulse_rates):
        result["prepulse_status"] = "INCOMPLETE_PREPULSE_BUCKETS"
        return result
    base = statistics.fmean(float(rate) for rate in prepulse_rates if rate is not None)
    result.update(prepulse_pdr_fraction=base, prepulse_status="COMPUTED")
    bucket_count = int(round((OBSERVATION_END_S - SHOCK_END_S) / RECOVERY_BIN_S))
    rates: list[float | None] = []
    statuses: list[str] = []
    for index in range(bucket_count):
        start = SHOCK_END_S + index * RECOVERY_BIN_S
        stats = _window_stats(evidence, start, start + RECOVERY_BIN_S)
        rates.append(stats["goodput_pdr_fraction"])
        statuses.append(stats["status"])
    threshold = 0.95 * base
    if base == 0.0:
        result["t_rec_status"] = "ZERO_PREPULSE_GOODPUT"
    confirmation_bins = int(round(RECOVERY_CONFIRM_S / RECOVERY_BIN_S))
    found: float | None = None
    for index in range(0, len(rates) - confirmation_bins + 1):
        window = rates[index:index + confirmation_bins]
        if base > 0 and all(rate is not None and rate + 1e-12 >= threshold for rate in window):
            found = SHOCK_END_S + index * RECOVERY_BIN_S
            break
    if found is None and base > 0:
        result.update(
            t_rec_right_censor_s=RECOVERY_RIGHT_CENSOR_S,
            t_rec_display=f">{RECOVERY_RIGHT_CENSOR_S:.1f}",
            t_rec_status="RIGHT_CENSORED",
        )
    elif found is not None:
        result.update(
            t_rec_s=found - SHOCK_END_S,
            t_rec_display=f"{found - SHOCK_END_S:.1f}",
            t_rec_status="COMPUTED",
        )
    if any(rate is None for rate in rates):
        result["rgd_status"] = "INCOMPLETE_RECOVERY_BUCKETS"
    else:
        deficit = sum(
            max(0.0, base - float(rate)) * RECOVERY_BIN_S
            for rate in rates if rate is not None)
        result.update(rgd_delivery_ratio_seconds=deficit, rgd_status="COMPUTED")
    result["recovery_bucket_status_counts"] = _json_text(_counts(statuses))
    return result


def _counts(values: Iterable[Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def _service_work_metrics(evidence: RunEvidence) -> dict[str, Any]:
    windows = (evidence.ledger or {}).get("link_service_windows")
    events = (evidence.ledger or {}).get("packet_events")
    result: dict[str, Any] = {
        "zombie_work_service_bits_ratio_pct": None,
        "zombie_work_numerator_capacity_bits": None,
        "zombie_work_denominator_capacity_bits": None,
        "zombie_work_status": "MISSING_SERVICE_WINDOWS",
        "path_hop_zwr_status": "MISSING_DROPPED_PACKET_PATHS",
        "dropped_arrival_hop_visits": None,
        "dropped_isl_service_visits": None,
        "dropped_hop_evidence_status": "MISSING_PACKET_EVENTS",
    }
    if not isinstance(windows, list):
        return result
    if not evidence.fates_ready:
        result["zombie_work_status"] = "INCOMPLETE_FATES"
        return result
    work_by_pid: dict[int, float] = {}
    valid = True
    for window in windows:
        if not isinstance(window, dict):
            valid = False
            break
        pid = _maybe_int(window.get("pid"))
        if pid is None:
            continue
        capacity = _maybe_float(window.get("capacity_bits"))
        served = _maybe_float(window.get("served_bits"))
        outcome = str(window.get("outcome", ""))
        if capacity is None or served is None:
            valid = False
            break
        work = served if outcome == "ok" and served > 0.0 else max(0.0, capacity)
        work_by_pid[pid] = work_by_pid.get(pid, 0.0) + work
    if not valid:
        result["zombie_work_status"] = "INVALID_SERVICE_WINDOW"
        return result
    denominator = sum(work_by_pid.values())
    numerator = sum(
        work for pid, work in work_by_pid.items()
        if evidence.fates.get(pid) in TERMINAL_LOSS_FATES)
    result.update(
        zombie_work_numerator_capacity_bits=numerator,
        zombie_work_denominator_capacity_bits=denominator,
    )
    if denominator <= 0.0:
        result["zombie_work_status"] = "ZERO_SERVICE_WORK"
    else:
        result.update(
            zombie_work_service_bits_ratio_pct=100.0 * numerator / denominator,
            zombie_work_status="COMPUTED_SERVICE_CAPACITY_BITS",
        )

    if not isinstance(events, list):
        return result
    emitted: set[int] = set()
    # Arrival records carry a propagation ID but not its stage. Join them to
    # starts so uplink/downlink propagations never count as ISL hops.
    isl_starts = {(event.get("pid"), event.get("prop_id")): event.get("link_id")
                  for event in events if isinstance(event, dict)
                  and event.get("kind") == "propagation_start"
                  and event.get("stage") == "isl"}
    arrival_nodes: dict[int, list[int]] = {}
    arrival_visits: dict[int, set[Any]] = {}
    isl_service_visits: dict[int, int] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        pid = _maybe_int(event.get("pid"))
        if pid is None:
            continue
        kind = event.get("kind")
        if kind == "packet_emitted":
            emitted.add(pid)
        elif kind == "satellite_ingress" and event.get("satellite") is not None:
            arrival_nodes.setdefault(pid, []).append(int(event["satellite"]))
        elif kind == "propagation_arrival":
            token = event.get("prop_id")
            edge = isl_starts.get((pid, token))
            if isinstance(edge, str):
                arrival_visits.setdefault(pid, set()).add(token)
                arrival_nodes.setdefault(pid, []).append(int(edge.split(":")[-1]))
        elif kind == "service_start" and event.get("stage") == "isl":
            isl_service_visits[pid] = isl_service_visits.get(pid, 0) + 1
    dropped = {pid for pid, fate in evidence.fates.items()
               if fate in TERMINAL_LOSS_FATES}
    trace_ids = set(evidence.trace_by_pid)
    if trace_ids <= emitted:
        dropped_hops = sum(len(arrival_visits.get(pid, ())) for pid in dropped)
        all_hops = sum(map(len, arrival_visits.values()))
        result.update(
            path_hop_zwr_status="COMPUTED_ISL_ARRIVALS" if all_hops else "ZERO_ISL_ARRIVALS",
            path_hop_zwr_pct=100.0 * dropped_hops / all_hops if all_hops else None,
            loop_revisit_rate_pct=100.0 * sum(len(path) != len(set(path))
                                              for path in arrival_nodes.values()) / len(trace_ids)
                                               if trace_ids else None,
            dropped_arrival_hop_visits=dropped_hops,
            dropped_isl_service_visits=sum(isl_service_visits.get(pid, 0) for pid in dropped),
            dropped_hop_evidence_status="COMPUTED",
        )
    else:
        result.update(
            dropped_hop_evidence_status="INCOMPLETE_EMISSION_EVENTS",
            dropped_hop_missing_event_pids=_json_text(sorted(dropped - emitted)),
        )
    return result


def _hop_work(evidence: RunEvidence) -> tuple[int | None, str]:
    windows = (evidence.ledger or {}).get("link_service_windows")
    events = (evidence.ledger or {}).get("packet_events")
    if not evidence.fates_ready:
        return None, "INCOMPLETE_FATES"
    if not isinstance(windows, list):
        return None, "MISSING_SERVICE_WINDOWS"
    if isinstance(events, list):
        emitted = {
            _maybe_int(event.get("pid")) for event in events
            if isinstance(event, dict) and event.get("kind") == "packet_emitted"}
        trace_ids = {int(row["packet_id"]) for row in evidence.trace}
        if not trace_ids <= emitted:
            return None, "INCOMPLETE_EMISSION_EVENTS"
    else:
        return None, "MISSING_PACKET_EVENTS"
    count = 0
    for window in windows:
        if not isinstance(window, dict) or window.get("stage") != "isl":
            continue
        if _maybe_int(window.get("pid")) is not None:
            count += 1
    return count, "COMPUTED"


def _clip_fraction(start: float, end: float, window_start: float, window_end: float) -> float:
    overlap = min(end, window_end) - max(start, window_start)
    duration = end - start
    return max(0.0, overlap) / duration if duration > 0.0 else 0.0


def _canonical_edge(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("isl:"):
            parts = text.split(":")
            if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                return f"isl:{int(parts[1])}:{int(parts[2])}"
        if "->" in text:
            left, right = text.split("->", 1)
            if left.strip().isdigit() and right.strip().isdigit():
                return f"isl:{int(left)}:{int(right)}"
    if isinstance(value, (list, tuple)) and len(value) == 2:
        left, right = _maybe_int(value[0]), _maybe_int(value[1])
        if left is not None and right is not None:
            return f"isl:{left}:{right}"
    if isinstance(value, dict):
        left = _maybe_int(value.get("from", value.get("src")))
        right = _maybe_int(value.get("to", value.get("dst")))
        if left is not None and right is not None:
            return f"isl:{left}:{right}"
    raise ExportValidationError(f"invalid corridor edge {value!r}")


def _explicit_edges(entry: Mapping[str, Any]) -> frozenset[str] | None:
    value = entry.get("corridor_edges")
    if value is None:
        return None
    if isinstance(value, Mapping):
        alg = str(entry.get("alg"))
        if "edges" in value:
            value = value["edges"]
        elif alg in value:
            value = value[alg]
        elif "default" in value:
            value = value["default"]
        else:
            raise ExportValidationError(
                f"corridor_edges has no declaration for algorithm {alg!r}")
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ExportValidationError("corridor_edges must be a sequence")
    edges = frozenset(_canonical_edge(item) for item in value)
    if not edges:
        raise ExportValidationError("corridor_edges must not be empty")
    return edges


def _available_is_edge_set(evidence: RunEvidence, start: float, end: float) -> frozenset[str]:
    windows = (evidence.ledger or {}).get("link_available_windows")
    if not isinstance(windows, list):
        return frozenset()
    edges: set[str] = set()
    for window in windows:
        if (not isinstance(window, dict) or window.get("stage") != "isl"
                or _maybe_float(window.get("start")) is None
                or _maybe_float(window.get("end")) is None):
            continue
        if _clip_fraction(
                float(window["start"]), float(window["end"]), start, end) > 0.0:
            edges.add(_canonical_edge(window.get("link_id")))
    return frozenset(edges)


def _jain_for_edges(
        evidence: RunEvidence, edges: frozenset[str], start: float, end: float,
) -> tuple[float | None, str, dict[str, Any]]:
    available = (evidence.ledger or {}).get("link_available_windows")
    service = (evidence.ledger or {}).get("link_service_windows")
    if not isinstance(available, list):
        return None, "MISSING_LINK_AVAILABLE_WINDOWS", {}
    if not isinstance(service, list):
        return None, "MISSING_LINK_SERVICE_WINDOWS", {}
    available_bits: dict[str, float] = {edge: 0.0 for edge in edges}
    service_bits: dict[str, float] = {edge: 0.0 for edge in edges}
    for window in available:
        if not isinstance(window, dict) or window.get("stage") != "isl":
            continue
        edge = _canonical_edge(window.get("link_id"))
        if edge not in edges:
            continue
        capacity = _maybe_float(window.get("capacity_bits"))
        window_start = _maybe_float(window.get("start"))
        window_end = _maybe_float(window.get("end"))
        if capacity is None or window_start is None or window_end is None:
            return None, "INVALID_LINK_AVAILABLE_WINDOW", {}
        available_bits[edge] += capacity * _clip_fraction(
            window_start, window_end, start, end)
    for window in service:
        if not isinstance(window, dict) or window.get("stage") != "isl":
            continue
        edge = _canonical_edge(window.get("link_id"))
        if edge not in edges:
            continue
        capacity = _maybe_float(window.get("capacity_bits"))
        window_start = _maybe_float(window.get("start"))
        window_end = _maybe_float(window.get("end"))
        if capacity is None or window_start is None or window_end is None:
            return None, "INVALID_LINK_SERVICE_WINDOW", {}
        service_bits[edge] += capacity * _clip_fraction(
            window_start, window_end, start, end)
    if any(available_bits[edge] <= 0.0 for edge in edges):
        return None, "MISSING_DECLARED_EDGE_CAPACITY", {
            "available_capacity_bits": sum(available_bits.values()),
            "service_capacity_bits": sum(service_bits.values()),
        }
    utilization = [service_bits[edge] / available_bits[edge] for edge in sorted(edges)]
    if any(value > 1.0 + 1e-6 for value in utilization):
        return None, "UTILIZATION_EXCEEDS_AVAILABLE_CAPACITY", {
            "available_capacity_bits": sum(available_bits.values()),
            "service_capacity_bits": sum(service_bits.values()),
        }
    utilization = [min(1.0, max(0.0, value)) for value in utilization]
    numerator = sum(utilization) ** 2
    denominator = len(utilization) * sum(value * value for value in utilization)
    if denominator <= 0.0:
        return None, "ZERO_UTILIZATION_NO_FAIRNESS", {
            "available_capacity_bits": sum(available_bits.values()),
            "service_capacity_bits": sum(service_bits.values()),
        }
    return numerator / denominator, "COMPUTED", {
        "available_capacity_bits": sum(available_bits.values()),
        "service_capacity_bits": sum(service_bits.values()),
        "corridor_edge_count": len(edges),
        "corridor_edges": _json_text(sorted(edges)),
        "corridor_edge_source": (
            "explicit_index_declaration" if evidence.entry.get("corridor_edges") is not None
            else "complete_supported_isl_set"),
    }


def _apply_jain(evidences: Sequence[RunEvidence]) -> None:
    groups: dict[tuple[Any, ...], list[RunEvidence]] = {}
    for evidence in evidences:
        key = (evidence.scene, evidence.seed, evidence.interval_s, evidence.multiplier)
        groups.setdefault(key, []).append(evidence)
    for group in groups.values():
        computed: dict[int, tuple[frozenset[str], dict[str, Any]]] = {}
        for evidence in group:
            if evidence.status not in {"COMPUTED", "EMPTY_TRACE"}:
                evidence.entry["_jain_result"] = {
                    "jain_fairness_index": None,
                    "jain_status": evidence.status,
                }
                continue
            explicit = _explicit_edges(evidence.entry)
            if explicit is None:
                edges = _available_is_edge_set(evidence, JAIN_START_S, JAIN_END_S)
                if not edges:
                    evidence.entry["_jain_result"] = {
                        "jain_fairness_index": None,
                        "jain_status": "MISSING_SUPPORTED_ISL_EDGES",
                    }
                    continue
            else:
                edges = explicit
            jain, status, details = _jain_for_edges(
                evidence, edges, JAIN_START_S, JAIN_END_S)
            result = {
                "jain_fairness_index": jain,
                "jain_status": status,
                "jain_window_start_s": JAIN_START_S,
                "jain_window_end_s": JAIN_END_S,
                **details,
            }
            evidence.entry["_jain_result"] = result
            computed[id(evidence)] = (edges, result)
        universes = {edges for edges, _result in computed.values()}
        if len(universes) > 1:
            raise ExportValidationError(
                "Jain corridor/support set mismatch within cohort "
                f"{groups_key_text(group)}: {sorted(map(sorted, universes))}")


def groups_key_text(group: Sequence[RunEvidence]) -> str:
    if not group:
        return "{}"
    first = group[0]
    return _json_text({
        "scene": first.scene, "seed": first.seed,
        "interval_s": first.interval_s, "multiplier": first.multiplier,
    })


def _zero4(value: Any) -> list[float] | None:
    if isinstance(value, dict):
        if all(direction in value for direction in DIRECTIONS):
            value = [value[direction] for direction in DIRECTIONS]
        else:
            return None
    if isinstance(value, (list, tuple)) and len(value) == 4:
        values = [_maybe_float(item) for item in value]
        return [float(item) for item in values if item is not None] \
            if all(item is not None for item in values) else None
    return None


def _telemetry_rows(evidence: RunEvidence, *, seed_filter: int | None = None) -> list[dict[str, Any]]:
    if seed_filter is not None and evidence.seed != seed_filter:
        return []
    if not evidence.telemetry:
        return [{
            "status": "MISSING_TELEMETRY" if evidence.telemetry_path is None else "EMPTY_TELEMETRY",
            "provenance_run": evidence.run_key,
            "run_dir": str(evidence.run_dir),
            "telemetry_source": str(evidence.telemetry_path) if evidence.telemetry_path else "",
        }]
    output: list[dict[str, Any]] = []
    for ordinal, row in enumerate(evidence.telemetry):
        queues = _zero4(row.get("queues"))
        delta_h = _zero4(row.get("delta_h"))
        ego = _zero4(row.get("ego"))
        cvar = _zero4(row.get("cvar"))
        aoi_value = next((row.get(key) for key in (
            "actual_aoi", "aoi", "actual_aoi_by_direction",
            "actual_aoi_per_port", "aoi_actual", "actual_aoi_s",
            "actual_aoi_by_port", "aoi_by_port") if key in row), None)
        aoi = _zero4(aoi_value)
        aoi_scalar = _maybe_float(aoi_value) if aoi is None else None
        legal_value = row.get("legal")
        legal_actions = None
        if isinstance(legal_value, dict):
            legal_actions = [direction for direction in DIRECTIONS
                             if bool(legal_value.get(direction))]
        elif isinstance(legal_value, (list, tuple)):
            legal_actions = [direction for direction, allowed in zip(DIRECTIONS, legal_value)
                             if bool(allowed)]
        action = row.get("action")
        if isinstance(action, str) and action.upper() in DIRECTIONS:
            action_direction = action.upper()
        else:
            action_int = _maybe_int(action)
            action_direction = DIRECTIONS[action_int] if action_int is not None and 0 <= action_int < 4 else None
        ego_arg = max(range(4), key=lambda index: ego[index]) if ego else None
        cvar_arg = max(range(4), key=lambda index: cvar[index]) if cvar else None
        record: dict[str, Any] = {
            "status": "COMPUTED",
            "provenance_run": evidence.run_key,
            "run_dir": str(evidence.run_dir),
            "telemetry_source": str(evidence.telemetry_path) if evidence.telemetry_path else "",
            "telemetry_ordinal": ordinal,
            "pid": row.get("pid"),
            "at_s": row.get("at"),
            "sat": row.get("sat"),
            "decision_id": row.get("decision_id"),
            "action": action,
            "action_direction": action_direction,
            "direction": row.get("direction"),
            "legal_actions": _json_text(legal_actions) if legal_actions is not None else "",
            "queues_json": _json_text(queues) if queues is not None else "",
            "phi_spill": row.get("phi_spill"),
            "z_json": _json_text(row.get("z")) if row.get("z") is not None else "",
            "delta_h_json": _json_text(delta_h) if delta_h is not None else "",
            "ego_json": _json_text(ego) if ego is not None else "",
            "cvar_json": _json_text(cvar) if cvar is not None else "",
            "actual_aoi_json": _json_text(aoi) if aoi is not None else "",
            "actual_aoi_scalar": aoi_scalar,
            "actual_aoi_direction_value": (
                aoi_scalar if str(row.get("direction", "")).upper() in DIRECTIONS
                else None),
            "ego_argmax_direction": DIRECTIONS[ego_arg] if ego_arg is not None else "",
            "cvar_argmax_direction": DIRECTIONS[cvar_arg] if cvar_arg is not None else "",
            "ego_cvar_direction_disagreement": (
                ego_arg != cvar_arg if ego_arg is not None and cvar_arg is not None else None),
            "cvar_direction_spread": (
                max(cvar) - min(cvar) if cvar is not None else None),
            "cvar_direction_std": (
                statistics.pstdev(cvar) if cvar is not None else None),
        }
        for index, direction in enumerate(DIRECTIONS):
            for prefix, values in (
                    ("queue", queues), ("delta_h", delta_h), ("ego", ego),
                    ("cvar", cvar), ("actual_aoi", aoi)):
                record[f"{prefix}_{direction}"] = (
                    values[index] if values is not None else None)
        output.append(record)
    return output


def _telemetry_summary(evidence: RunEvidence, start: float, end: float) -> dict[str, Any]:
    if evidence.status not in {"COMPUTED", "EMPTY_TRACE"}:
        return {
            "telemetry_status": evidence.status,
            "telemetry_interval_start_s": start,
            "telemetry_interval_end_s": end,
            "telemetry_rows": 0,
        }
    rows = [row for row in evidence.telemetry
            if (value := _maybe_float(row.get("at"))) is not None and start <= value < end]
    result: dict[str, Any] = {
        "telemetry_status": "MISSING_TELEMETRY" if evidence.telemetry_path is None
        else "EMPTY_TELEMETRY" if not evidence.telemetry else "COMPUTED",
        "telemetry_interval_start_s": start,
        "telemetry_interval_end_s": end,
        "telemetry_rows": len(rows),
    }
    if not rows:
        return result
    queues = [_zero4(row.get("queues")) for row in rows]
    queues = [value for value in queues if value is not None]
    for index, direction in enumerate(DIRECTIONS):
        column = [value[index] for value in queues]
        result[f"queue_mean_{direction}"] = statistics.fmean(column) if column else None
        result[f"queue_max_{direction}"] = max(column) if column else None
    phi = sorted(
        (_maybe_float(row.get("at")), _maybe_float(row.get("phi_spill")))
        for row in rows)
    phi = [(at, value) for at, value in phi if at is not None and value is not None]
    result["phi_spill_integral_port_s"] = sum(
        0.5 * (phi[index - 1][1] + phi[index][1])
        * (phi[index][0] - phi[index - 1][0])
        for index in range(1, len(phi)))
    result["phi_spill_observed_span_s"] = phi[-1][0] - phi[0][0] if len(phi) > 1 else 0.0
    compute_values = [
        _maybe_float(row.get("compute_delay_s", row.get("decision_delay_s")))
        for row in rows]
    compute_values = [value for value in compute_values if value is not None]
    result["compute_delay_samples"] = len(compute_values)
    result["compute_delay_mean_s"] = statistics.fmean(compute_values) if compute_values else None
    result["compute_delay_p95_s"] = _nearest_rank(compute_values, 0.95)
    cvar_rows = [_zero4(row.get("cvar")) for row in rows]
    cvar_rows = [value for value in cvar_rows if value is not None]
    spreads = [max(value) - min(value) for value in cvar_rows]
    result["cvar_direction_spread_mean"] = statistics.fmean(spreads) if spreads else None
    result["cvar_direction_spread_max"] = max(spreads) if spreads else None
    aoi_rows = []
    aoi_scalars: dict[str, list[float]] = {direction: [] for direction in DIRECTIONS}
    for row in rows:
        value = next((row.get(key) for key in (
            "actual_aoi", "aoi", "actual_aoi_by_direction",
            "actual_aoi_per_port", "aoi_actual", "actual_aoi_s",
            "actual_aoi_by_port", "aoi_by_port") if key in row), None)
        parsed = _zero4(value)
        if parsed is not None:
            aoi_rows.append(parsed)
        else:
            scalar = _maybe_float(value)
            direction = str(row.get("direction", "")).upper()
            if scalar is not None and direction in DIRECTIONS:
                aoi_scalars[direction].append(scalar)
    for index, direction in enumerate(DIRECTIONS):
        values = [value[index] for value in aoi_rows]
        combined = values + aoi_scalars[direction]
        result[f"actual_aoi_mean_{direction}"] = (
            statistics.fmean(combined) if combined else None)
        result[f"actual_aoi_max_{direction}"] = max(combined) if combined else None
    return result


def _metadata(evidence: RunEvidence) -> dict[str, Any]:
    entry = evidence.entry
    extra = {
        key: value for key, value in entry.items()
        if not key.startswith("_") and key not in STANDARD_INDEX_FIELDS}
    return {
        "run_id": evidence.run_key,
        "exp": evidence.exp,
        "scene": evidence.scene,
        "seed": evidence.seed,
        "interval_s": evidence.interval_s,
        "alg": evidence.alg,
        "multiplier": evidence.multiplier,
        "train_seed": entry.get("train_seed"),
        "variant": entry.get("variant"),
        "outage": entry.get("outage"),
        "cohort_id": entry.get("cohort_id"),
        "workload_base_id": entry.get("workload_base_id"),
        "base_trace_sha256": entry.get("base_trace_sha256"),
        "workload_id": entry.get("workload_id"),
        "demand_model": entry.get("demand_model"),
        "topology_id": entry.get("topology_id"),
        "route_set_id": entry.get("route_set_id"),
        "config_identity": entry.get("config_identity"),
        "aoi_ms": entry.get("aoi_ms"),
        "test_seed": entry.get("test_seed"),
        "quality_status": entry.get("quality_status", entry.get("status")),
        "run_dir": str(evidence.run_dir),
        "trace_sha256": evidence.trace_sha256,
        "receipt_trace_sha256": (evidence.receipt or {}).get("trace_sha256"),
        "trace_identity_sha256": (evidence.receipt or {}).get("trace_identity_sha256"),
        "quality_json": (
            _json_text(entry.get("quality")) if entry.get("quality") is not None else ""),
        "extra_metadata_json": _json_text(extra) if extra else "",
    }


def _run_metrics(evidence: RunEvidence) -> dict[str, Any]:
    windows = {
        name: _window_stats(evidence, start, end)
        for name, start, end in SCENE_WINDOWS[evidence.scene]
    }
    recovery = _recovery_metrics(evidence)
    latency = _latency_stats(evidence, 0.2, OBSERVATION_END_S)
    recovery_latency = _latency_stats(evidence, SHOCK_END_S, OBSERVATION_END_S)
    service = _service_work_metrics(evidence)
    hop_count, hop_status = _hop_work(evidence)
    jain = dict(evidence.entry.get("_jain_result") or {
        "jain_fairness_index": None, "jain_status": "NOT_APPLICABLE_OR_NOT_SCHEDULED",
    })
    telemetry = _telemetry_summary(evidence, JAIN_START_S, JAIN_END_S)
    fallback_names = {
        "full": "goodput_pdr_full_pct",
        "steady_prepulse": "goodput_pdr_prepulse_pct",
        "burst": "goodput_pdr_burst_pct",
        "recovery": "goodput_pdr_recovery_pct",
        "emission_window": "goodput_pdr_emission_window_pct",
    }
    status_names = {
        "full": "full_goodput_status",
        "steady_prepulse": "prepulse_goodput_status",
        "burst": "burst_goodput_status",
        "recovery": "recovery_goodput_status",
        "emission_window": "emission_window_goodput_status",
    }
    result: dict[str, Any] = {
        "status": evidence.status,
        "evidence_errors": _json_text(evidence.errors),
        "events_status": evidence.events_status,
        "service_windows_status": evidence.windows_status,
        "available_windows_status": evidence.available_windows_status,
        "fates_complete": evidence.fates_complete,
        "conservation_ok": evidence.conservation_ok,
        "trace_hash_matches_receipt": evidence.hash_matches_receipt,
        "ledger_hash_status": evidence.ledger_hash_status,
        "packets_offered": len(evidence.trace),
        "packet_fate_counts": _json_text(_counts(evidence.fates.values())),
        "actual_isl_hop_work_attempts": hop_count,
        "hop_work_status": hop_status,
        "sp_hop_reference_hops": None,
        "hir_actual_over_sp_hop": None,
        "hir_status": "MISSING_SP_HOP_REFERENCE",
        **recovery,
        **latency,
        **service,
        **jain,
        **telemetry,
    }
    result.update({
        f"recovery_{key}": value for key, value in recovery_latency.items()})
    for name, stats in windows.items():
        column = fallback_names[name]
        result[column] = stats["goodput_pdr_pct"]
        result[status_names[name]] = stats["status"]
        result[f"{name}_packets_offered"] = stats["packets_offered"]
        result[f"{name}_delivered_by_deadline"] = stats["delivered_by_deadline"]
    return result


def _apply_hop_reference(evidences: Sequence[RunEvidence], metrics_by_id: dict[int, dict[str, Any]]) -> None:
    references: dict[tuple[Any, ...], list[RunEvidence]] = {}
    for evidence in evidences:
        if evidence.alg.casefold() not in {"sp-hop", "sp_hop", "sp hop", "shortest_path", "shortest-path"}:
            continue
        key = (
            evidence.scene, evidence.seed, evidence.interval_s,
            evidence.multiplier, str(evidence.entry.get("train_seed")),
            str(evidence.entry.get("variant")), str(evidence.entry.get("outage")),
        )
        references.setdefault(key, []).append(evidence)
    for key, group in references.items():
        shas = {item.trace_sha256 for item in group}
        if len(shas) != 1:
            raise ExportValidationError(
                f"ambiguous SP-Hop traces for cohort {key}: {sorted(str(x) for x in shas)}")
    for evidence in evidences:
        nominal = evidence.entry.get("nominal_shortest_hops")
        if nominal is not None:
            actual = metrics_by_id[id(evidence)]["actual_isl_hop_work_attempts"]
            denominator = nominal.get("hops")
            if denominator is not None and denominator > 0 and actual is not None:
                metrics_by_id[id(evidence)].update(sp_hop_reference_hops=denominator,
                    hir_actual_over_sp_hop=actual / denominator,
                    hir_status="COMPUTED_NOMINAL_ZERO_QUEUE")
            else:
                metrics_by_id[id(evidence)]["hir_status"] = "INCOMPLETE_NOMINAL_COHORT"
            continue
        key = (
            evidence.scene, evidence.seed, evidence.interval_s,
            evidence.multiplier, str(evidence.entry.get("train_seed")),
            str(evidence.entry.get("variant")), str(evidence.entry.get("outage")),
        )
        reference = references.get(key, [])
        if evidence.alg.casefold() in {"sp-hop", "sp_hop", "sp hop", "shortest_path", "shortest-path"}:
            reference_evidence = evidence
        elif len(reference) == 1:
            reference_evidence = reference[0]
        else:
            continue
        if reference_evidence.trace_sha256 != evidence.trace_sha256:
            raise ExportValidationError(
                f"HIR cohort trace mismatch between {evidence.run_key} and "
                f"SP-Hop {reference_evidence.run_key}")
        actual = metrics_by_id[id(evidence)].get("actual_isl_hop_work_attempts")
        baseline = metrics_by_id[id(reference_evidence)].get("actual_isl_hop_work_attempts")
        if actual is None or baseline is None or baseline <= 0:
            metrics_by_id[id(evidence)]["hir_status"] = "INCOMPLETE_HOP_EVIDENCE"
            continue
        metrics_by_id[id(evidence)].update(
            sp_hop_reference_hops=baseline,
            hir_actual_over_sp_hop=actual / baseline,
            hir_status="COMPUTED" if actual or reference_evidence is evidence else "COMPUTED_EMPTY",
        )


def _cohort_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set, frozenset)):
        if isinstance(value, (set, frozenset)):
            value = sorted(value, key=str)
        return _json_text(value)
    return str(value)


def _cohort_key(evidence: RunEvidence) -> tuple[Any, ...]:
    entry = evidence.entry
    return tuple(
        (field, _cohort_value(
            {key: value for key, value in entry[field].items()
             if key not in VOLATILE_QUALITY_FIELDS}
            if field == "quality" and isinstance(entry.get(field), Mapping)
            else entry.get(field)))
        for field in COHORT_FIELDS)


def _victim_ratio(evidence: RunEvidence, baseline: RunEvidence) -> dict[str, Any]:
    if evidence.scene != baseline.scene or evidence.seed != baseline.seed:
        raise ExportValidationError("victim cohort mismatch in scene/seed")
    if evidence.interval_s != baseline.interval_s or evidence.alg != baseline.alg:
        raise ExportValidationError("victim cohort mismatch in interval/algorithm")
    if _cohort_key(evidence) != _cohort_key(baseline):
        raise ExportValidationError(
            "victim cohort metadata mismatch beyond the changed trace hash")
    if (abs(evidence.multiplier - baseline.multiplier) > 1e-9
            and evidence.trace_sha256 == baseline.trace_sha256):
        raise ExportValidationError(
            "Exp-0B non-baseline burst shares the 1.0x trace hash; the shock "
            "intervention is not evidenced in the trace")
    current_cohort = _trace_rows_between(evidence, SHOCK_END_S, OBSERVATION_END_S)
    reference_cohort = _trace_rows_between(baseline, SHOCK_END_S, OBSERVATION_END_S)
    if current_cohort != reference_cohort:
        raise ExportValidationError("victim recovery packets differ across shock arms")
    current = _latency_stats(evidence, SHOCK_END_S, OBSERVATION_END_S)
    reference = _latency_stats(baseline, SHOCK_END_S, OBSERVATION_END_S)
    result = {
        "victim_baseline_run_id": baseline.run_key,
        "victim_cohort_match": True,
        "victim_p50_ratio": None,
        "victim_p99_ratio": None,
        "victim_ratio_status": "COMPUTED",
    }
    for probability, column, ratio_column in (
            (0.50, "e2e_p50_s", "victim_p50_ratio"),
            (0.99, "e2e_p99_s", "victim_p99_ratio")):
        numerator = current[column]
        denominator = reference[column]
        if numerator is None or denominator is None or denominator <= 0.0:
            result["victim_ratio_status"] = "INCOMPLETE_LATENCY_REFERENCE"
            continue
        result[ratio_column] = numerator / denominator
    return result


def _attach_exp0b_pairs(evidences: Sequence[RunEvidence]) -> None:
    sweeps = [evidence for evidence in evidences if evidence.exp == "exp0_hysteresis"]
    exp0b = [evidence for evidence in evidences if evidence.exp == "exp0b"]
    baselines: dict[tuple[Any, ...], RunEvidence] = {}
    for evidence in exp0b:
        if abs(evidence.multiplier - 1.0) > 1e-9:
            continue
        key = _cohort_key(evidence)
        if key in baselines:
            raise ExportValidationError(
                "multiple 1.0x Exp-0B baselines share one recovery cohort")
        baselines[key] = evidence
    for evidence in exp0b:
        if abs(evidence.multiplier - 1.0) <= 1e-9:
            evidence.entry["_victim_result"] = {
                "victim_baseline_run_id": evidence.run_key,
                "victim_cohort_match": True,
                "victim_p50_ratio": 1.0,
                "victim_p99_ratio": 1.0,
                "victim_ratio_status": "BASELINE",
            }
            continue
        baseline = baselines.get(_cohort_key(evidence))
        if baseline is None:
            nominal_key = (
                evidence.scene, evidence.seed, evidence.interval_s, evidence.alg,
                _cohort_value(evidence.entry.get("cohort_id")),
                _cohort_value(evidence.entry.get("workload_base_id")),
            )
            nominal_baselines = [
                item for item in baselines.values()
                if (
                    item.scene, item.seed, item.interval_s, item.alg,
                    _cohort_value(item.entry.get("cohort_id")),
                    _cohort_value(item.entry.get("workload_base_id")),
                ) == nominal_key
            ]
            if len(nominal_baselines) == 1:
                raise ExportValidationError(
                    "Exp-0B paired comparison has a 1.0x baseline with "
                    "cohort metadata mismatch beyond the changed trace hash")
            evidence.entry["_victim_result"] = {
                "victim_baseline_run_id": None,
                "victim_cohort_match": False,
                "victim_p50_ratio": None,
                "victim_p99_ratio": None,
                "victim_ratio_status": "MISSING_EXACT_1X_COHORT",
            }
        else:
            evidence.entry["_victim_result"] = _victim_ratio(evidence, baseline)


def _series_rows(evidence: RunEvidence, start: float, end: float, bin_s: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    count = int(round((end - start) / bin_s))
    for index in range(count):
        left = start + index * bin_s
        right = left + bin_s
        stats = _window_stats(evidence, left, right)
        row = _metadata(evidence)
        row.update({
            "bin_start_s": left,
            "bin_end_s": right,
            "goodput_pdr_pct": stats["goodput_pdr_pct"],
            "goodput_status": stats["status"],
            "packets_offered": stats["packets_offered"],
            "delivered_by_deadline": stats["delivered_by_deadline"],
            "bin_width_s": bin_s,
        })
        rows.append(row)
    return rows


def _metric_row(evidence: RunEvidence, metrics: Mapping[str, Any]) -> dict[str, Any]:
    row = _metadata(evidence)
    row.update(metrics)
    row.update(evidence.entry.get("_victim_result") or {})
    return row


def _metric_value(metrics: Mapping[str, Any], name: str) -> float | None:
    value = metrics.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _summary_group_key(evidence: RunEvidence) -> tuple[Any, ...]:
    entry = evidence.entry
    return (
        evidence.scene, evidence.interval_s, evidence.alg, evidence.multiplier,
        str(entry.get("train_seed")), str(entry.get("variant")),
        str(entry.get("outage")),
    )


def _group_evidence_status(group: Sequence[RunEvidence]) -> tuple[str, str]:
    incomplete = [
        evidence.run_key for evidence in group
        if evidence.status not in {"COMPUTED", "EMPTY_TRACE"}]
    return (
        "COMPUTED" if not incomplete else "INCOMPLETE_EVIDENCE",
        _json_text(incomplete),
    )


def _assert_independent_seed_group(group: Sequence[RunEvidence]) -> None:
    seeds = [evidence.seed for evidence in group]
    duplicates = sorted({seed for seed in seeds if seeds.count(seed) > 1})
    if duplicates:
        raise ExportValidationError(
            f"summary cohort repeats test seed(s) {duplicates}; "
            "repeated traces are not independent experimental units")
    hashes = [evidence.trace_sha256 for evidence in group
              if evidence.trace_sha256 is not None]
    duplicate_hashes = sorted({digest for digest in hashes if hashes.count(digest) > 1})
    if duplicate_hashes:
        raise ExportValidationError(
            "summary cohort repeats an identical trace hash across distinct seeds; "
            "the traces are not independent evidence")


def _summary_rows(
        evidences: Sequence[RunEvidence], metrics_by_id: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[RunEvidence]] = {}
    for evidence in evidences:
        key = _summary_group_key(evidence)
        groups.setdefault(key, []).append(evidence)
    rows: list[dict[str, Any]] = []
    for group in groups.values():
        _assert_independent_seed_group(group)
        first = group[0]
        row: dict[str, Any] = {
            "scene": first.scene,
            "interval_s": first.interval_s,
            "alg": first.alg,
            "multiplier": first.multiplier,
            "train_seed": first.entry.get("train_seed"),
            "variant": first.entry.get("variant"),
            "outage": first.entry.get("outage"),
            "n_traces": len(group),
            "independent_unit": "seed/trace",
            "inference_status": (
                "DESCRIPTIVE_5_TRACES" if len(group) == 5
                else f"DESCRIPTIVE_{len(group)}_TRACES"),
            "provenance_run_ids": _json_text([item.run_key for item in group]),
        }
        row["aggregation_status"], row[
            "incomplete_provenance_run_ids"] = _group_evidence_status(group)
        for metric in SUMMARY_METRICS:
            values = [
                value for evidence in group
                if (value := _metric_value(metrics_by_id[id(evidence)], metric)) is not None]
            row[f"{metric}_n"] = len(values)
            if metric == "t_rec_s":
                censored = sum(
                    1 for evidence in group
                    if metrics_by_id[id(evidence)].get("t_rec_status") == "RIGHT_CENSORED")
                complete = len(values) == len(group) and censored == 0
                row["t_rec_observed_n"] = len(values)
                row["t_rec_right_censored_n"] = censored
                row["t_rec_observed_mean_s"] = statistics.fmean(values) if values else None
                row["t_rec_summary_status"] = (
                    "COMPUTED_ALL_TRACES" if complete
                    else "RIGHT_CENSORED_PRESENT" if censored
                    else "INCOMPLETE_TRACES")
                row[f"{metric}_mean"] = (
                    statistics.fmean(values) if complete else None)
            else:
                row[f"{metric}_mean"] = statistics.fmean(values) if values else None
            row[f"{metric}_std"] = (
                statistics.stdev(values) if len(values) >= 2 else None)
            if len(group) == 5 and len(values) == 5 and not (
                    metric == "t_rec_s" and row.get("t_rec_right_censored_n")):
                sem = statistics.stdev(values) / math.sqrt(5.0)
                half = 2.7764451051977987 * sem
                mean = statistics.fmean(values)
                row[f"{metric}_ci95_low"] = mean - half
                row[f"{metric}_ci95_high"] = mean + half
            else:
                row[f"{metric}_ci95_low"] = None
                row[f"{metric}_ci95_high"] = None
        rows.append(row)
    return sorted(rows, key=lambda row: (
        str(row["scene"]), float(row["interval_s"]), str(row["alg"]),
        str(row.get("variant")), float(row["multiplier"])))


def _breakdown_rows(
        evidences: Sequence[RunEvidence], metrics_by_id: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[RunEvidence]] = {}
    for evidence in evidences:
        groups.setdefault(_summary_group_key(evidence), []).append(evidence)
    rows: list[dict[str, Any]] = []
    for group in groups.values():
        _assert_independent_seed_group(group)
        first = group[0]
        row: dict[str, Any] = {
            "scene": first.scene,
            "interval_s": first.interval_s,
            "alg": first.alg,
            "multiplier": first.multiplier,
            "train_seed": first.entry.get("train_seed"),
            "variant": first.entry.get("variant"),
            "outage": first.entry.get("outage"),
            "n_traces": len(group),
            "independent_unit": "seed/trace",
            "inference_status": (
                "DESCRIPTIVE_5_TRACES" if len(group) == 5
                else f"DESCRIPTIVE_{len(group)}_TRACES"),
            "provenance_run_ids": _json_text([item.run_key for item in group]),
        }
        row["aggregation_status"], row[
            "incomplete_provenance_run_ids"] = _group_evidence_status(group)
        for fate in ALL_DATA_FATES:
            rates = []
            for evidence in group:
                if not evidence.fates_ready or not evidence.trace:
                    continue
                count = sum(1 for value in evidence.fates.values() if value == fate)
                rates.append(100.0 * count / len(evidence.trace))
            row[f"{fate}_pct_mean"] = statistics.fmean(rates) if rates else None
            row[f"{fate}_pct_std"] = statistics.stdev(rates) if len(rates) >= 2 else None
        for metric in (
                "zombie_work_service_bits_ratio_pct",
                "zombie_work_numerator_capacity_bits",
                "dropped_arrival_hop_visits", "dropped_isl_service_visits",
                "path_hop_zwr_pct", "loop_revisit_rate_pct"):
            values = [
                value for evidence in group
                if (value := _metric_value(metrics_by_id[id(evidence)], metric)) is not None]
            row[f"{metric}_mean"] = statistics.fmean(values) if values else None
            row[f"{metric}_std"] = statistics.stdev(values) if len(values) >= 2 else None
        rows.append(row)
    return sorted(rows, key=lambda row: (
        str(row["scene"]), float(row["interval_s"]), str(row["alg"]),
        str(row.get("variant")), str(row.get("outage")),
        float(row["multiplier"])))


def _ccdf_rows(
        exp2: Sequence[RunEvidence], metrics_by_id: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    probabilities = [index / (CCDF_POINTS - 1) for index in range(CCDF_POINTS)]
    for evidence in exp2:
        base = _metadata(evidence)
        metrics = metrics_by_id[id(evidence)]
        series = (
            ("e2e_latency", "e2e_latency_status", _latency_values(
                evidence, 0.2, OBSERVATION_END_S)),
            ("queue_wait", "queue_wait_status", _queue_wait_values(
                evidence, 0.2, OBSERVATION_END_S)),
            ("e2e_latency_recovery", "recovery_e2e_latency_status",
             _latency_values(evidence, SHOCK_END_S, OBSERVATION_END_S)),
            ("queue_wait_recovery", "recovery_queue_wait_status",
             _queue_wait_values(evidence, SHOCK_END_S, OBSERVATION_END_S)),
        )
        for metric, status_column, (values, status) in series:
            if status != "COMPUTED":
                values = []
            if not values:
                row = dict(base)
                row.update({
                    "metric": metric,
                    "quantile": None,
                    "latency_s": None,
                    "survival_probability": None,
                    "n_samples": 0,
                    "status": status,
                    "quantile_method": "nearest-rank",
                    "statistical_unit": "packet_age_within_run_not_replicate",
                })
                rows.append(row)
                continue
            for probability in probabilities:
                row = dict(base)
                row.update({
                    "metric": metric,
                    "quantile": probability,
                    "latency_s": _nearest_rank(values, probability),
                    "survival_probability": 1.0 - probability,
                    "n_samples": len(values),
                    "status": status,
                    "quantile_method": "nearest-rank empirical within-run",
                    "statistical_unit": "packet_age_within_run_not_replicate",
                    "source_metric_status": metrics.get(status_column),
                })
                rows.append(row)
    return rows


def _internal_rows(evidences: Sequence[RunEvidence]) -> list[dict[str, Any]]:
    selected = [evidence for evidence in evidences
                if evidence.exp == "exp2" and evidence.seed == 101]
    if not selected:
        return [{
            "status": "NO_EXP2_SEED101_INDEX_ROWS",
            "provenance_note": "internal telemetry requires explicit Exp-2 seed 101 rows",
        }]
    out: list[dict[str, Any]] = []
    for evidence in selected:
        base = _metadata(evidence)
        if evidence.status not in {"COMPUTED", "EMPTY_TRACE"}:
            row = dict(base)
            row.update({
                "status": evidence.status,
                "provenance_run": evidence.run_key,
                "run_dir": str(evidence.run_dir),
                "telemetry_source": (
                    str(evidence.telemetry_path) if evidence.telemetry_path else ""),
            })
            out.append(row)
            continue
        for telemetry_row in _telemetry_rows(evidence, seed_filter=101):
            row = dict(base)
            row.update(telemetry_row)
            out.append(row)
    return out


def _train_curve_rows(evidences: Sequence[RunEvidence]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    metric_keys = (
        "epoch", "step", "train_loss", "val_loss", "validation_score",
        "actor_loss", "critic_loss", "checkpoint_sha256")
    for evidence in evidences:
        entry = evidence.entry
        curve = entry.get("train_curve")
        if isinstance(curve, list):
            points = curve
        else:
            points = [entry]
        for point in points:
            if not isinstance(point, Mapping):
                continue
            row = _metadata(evidence)
            for key in metric_keys:
                row[key] = point.get(key, entry.get(key))
            row["status"] = (
                "COMPUTED_EXPLICIT_INDEX_ROW"
                if any(row.get(key) is not None for key in metric_keys)
                else "INDEX_ROW_WITHOUT_CURVE_FIELDS")
            row["provenance_note"] = (
                "Training points are copied from explicit index rows; no curve is inferred")
            rows.append(row)
    return rows


def _resource_rows(
        evidences: Sequence[RunEvidence], metrics_by_id: Mapping[int, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[RunEvidence]] = {}
    for evidence in evidences:
        key = (
            evidence.scene, evidence.interval_s, evidence.alg,
            str(evidence.entry.get("variant")),
            str(evidence.entry.get("train_seed")),
            str(evidence.entry.get("outage")), evidence.multiplier,
        )
        groups.setdefault(key, []).append(evidence)
    rows: list[dict[str, Any]] = []
    resource_metrics = (
        "compute_delay_mean_s", "compute_delay_p95_s",
        "resource_queue_wait_mean_s", "resource_queue_wait_p95_s",
        "resource_queue_wait_p99_s", "resource_queue_wait_cvar_090_s",
        "phi_spill_integral_port_s", "cvar_direction_spread_mean",
        "jain_fairness_index", "goodput_pdr_burst_pct",
    )
    for group in groups.values():
        _assert_independent_seed_group(group)
        first = group[0]
        row: dict[str, Any] = {
            "scene": first.scene,
            "interval_s": first.interval_s,
            "alg": first.alg,
            "variant": first.entry.get("variant"),
            "train_seed": first.entry.get("train_seed"),
            "outage": first.entry.get("outage"),
            "multiplier": first.multiplier,
            "n_traces": len(group),
            "matched_interval_start_s": JAIN_START_S,
            "matched_interval_end_s": JAIN_END_S,
            "independent_unit": "seed/trace",
            "inference_status": (
                "DESCRIPTIVE_5_TRACES" if len(group) == 5
                else f"DESCRIPTIVE_{len(group)}_TRACES"),
            "provenance_run_ids": _json_text([item.run_key for item in group]),
        }
        row["aggregation_status"], row[
            "incomplete_provenance_run_ids"] = _group_evidence_status(group)
        group_metrics: dict[int, dict[str, Any]] = {}
        for evidence in group:
            values = dict(metrics_by_id[id(evidence)])
            queue_values, queue_status = _queue_wait_values(
                evidence, JAIN_START_S, JAIN_END_S)
            values.update({
                "resource_queue_wait_status": queue_status,
                "resource_queue_wait_mean_s": (
                    statistics.fmean(queue_values) if queue_values else None),
                "resource_queue_wait_p95_s": _nearest_rank(queue_values, 0.95),
                "resource_queue_wait_p99_s": _nearest_rank(queue_values, 0.99),
                "resource_queue_wait_cvar_090_s": _upper_cvar(queue_values, 0.10),
            })
            group_metrics[id(evidence)] = values
        for metric in resource_metrics:
            values = [
                value for evidence in group
                if (value := _metric_value(group_metrics[id(evidence)], metric)) is not None]
            row[f"{metric}_mean"] = statistics.fmean(values) if values else None
            row[f"{metric}_std"] = statistics.stdev(values) if len(values) >= 2 else None
            row[f"{metric}_n"] = len(values)
        row["resource_queue_wait_status_counts"] = _json_text(_counts(
            group_metrics[id(evidence)]["resource_queue_wait_status"]
            for evidence in group))
        rows.append(row)
    return sorted(rows, key=lambda row: (
        str(row["scene"]), float(row["interval_s"]), str(row["alg"]),
        str(row.get("variant")), str(row.get("outage")),
        float(row["multiplier"])))


def _write_csv_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns: list[str] = []
    if rows:
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)
    else:
        columns = ["status", "provenance_note"]
        rows = ({"status": "NO_ROWS", "provenance_note": "No matching index evidence"},)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key) for key in columns})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def export(root: Path | str, index: list[dict[str, Any]]) -> dict[str, Any]:
    """Write all figure-source CSVs under ``root`` and return names/counts.

    ``root`` is the export directory.  A relative ``run_dir`` in an index row
    is resolved against this same root.  The function computes every table
    before replacing a target file, validates cohort comparisons centrally,
    and uses per-file temporary writes plus ``os.replace`` for atomic publishes.
    """
    if not isinstance(index, list):
        raise ExportValidationError("index must be a list")
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    evidences = [_load_run(root_path, raw, ordinal) for ordinal, raw in enumerate(index)]
    _apply_jain(evidences)
    metrics_by_id = {id(evidence): _run_metrics(evidence) for evidence in evidences}
    _apply_hop_reference(evidences, metrics_by_id)
    _attach_exp0b_pairs(evidences)
    # Hop and victim enrichment happened after the initial row construction.
    for evidence in evidences:
        metrics_by_id[id(evidence)].update(evidence.entry.get("_victim_result") or {})

    exp0 = [evidence for evidence in evidences if evidence.exp == "exp0"]
    sweeps = [evidence for evidence in evidences if evidence.exp == "exp0_hysteresis"]
    exp0b = [evidence for evidence in evidences if evidence.exp == "exp0b"]
    exp1 = [evidence for evidence in evidences if evidence.exp == "exp1"]
    exp2 = [evidence for evidence in evidences if evidence.exp == "exp2"]
    exp3_outage = [evidence for evidence in evidences if evidence.exp == "exp3_outage"]
    exp3_zeroshot = [evidence for evidence in evidences if evidence.exp == "exp3_zeroshot"]
    exp4_ablation = [evidence for evidence in evidences if evidence.exp == "exp4_ablation"]
    exp4_compute = [evidence for evidence in evidences if evidence.exp == "exp4_compute"]

    exp0b_series: list[dict[str, Any]] = []
    for evidence in exp0b:
        if evidence.seed != 101:
            continue
        for row in _series_rows(evidence, 0.2, 9.5, RECOVERY_BIN_S):
            row.update(evidence.entry.get("_victim_result") or {})
            exp0b_series.append(row)

    outputs: dict[str, list[dict[str, Any]]] = {
        "exp0_calibration.csv": [
            _metric_row(evidence, metrics_by_id[id(evidence)]) for evidence in exp0],
        "exp0_hysteresis_multi_alg.csv": [
            {**_metadata(evidence), **step,
             **_window_stats(evidence, step["start_s"] + 0.2, step["end_s"])}
            for evidence in sweeps for step in evidence.entry["plateaus"]],
        "exp0b_shock_sweep_seed101.csv": exp0b_series,
        "exp2_main_raw.csv": [
            _metric_row(evidence, metrics_by_id[id(evidence)]) for evidence in exp2],
        "exp2_main_table_mean_std.csv": _summary_rows(exp2, metrics_by_id),
        "exp2_latency_ccdf.csv": _ccdf_rows(exp2, metrics_by_id),
        "exp2_breakdown_mean_std.csv": _breakdown_rows(exp2, metrics_by_id),
        "exp2_internal_q_trace_seed101.csv": _internal_rows(exp2),
        "exp2_aoi_sweep.csv": _summary_rows(exp2, metrics_by_id),
        "exp2_timeseries_100ms_seed101.csv": [
            row for evidence in exp2 if evidence.seed == 101
            for row in _series_rows(evidence, 0.2, 9.5, RECOVERY_BIN_S)],
        "exp3_link_outage.csv": [
            _metric_row(evidence, metrics_by_id[id(evidence)]) for evidence in exp3_outage],
        "exp3_zeroshot_large.csv": [
            _metric_row(evidence, metrics_by_id[id(evidence)]) for evidence in exp3_zeroshot],
        "exp4_ablation_mean_std.csv": _summary_rows(exp4_ablation, metrics_by_id),
        "exp4_compute_overhead.csv": _resource_rows(exp4_compute, metrics_by_id),
    }
    if exp1:
        outputs["exp1_train_curves.csv"] = _train_curve_rows(exp1)

    filenames = [name for name in REQUIRED_FILES]
    if exp1:
        filenames.insert(3, "exp1_train_curves.csv")
    row_counts: dict[str, int] = {}
    for filename in filenames:
        rows = outputs[filename]
        _write_csv_atomic(root_path / filename, rows)
        row_counts[filename] = len(rows)
    return {
        "files": filenames,
        "filenames": filenames,
        "row_counts": row_counts,
        "total_rows": sum(row_counts.values()),
        "root": str(root_path),
        "figure_sources": {
            str(number): list(filenames)
            for number, filenames in FIGURE_SOURCE_FILES.items()},
        "conventions": {
            "independent_unit": "seed/trace",
            "goodput_window": "packet emission time",
            "recovery_confirmation_s": RECOVERY_CONFIRM_S,
            "recovery_right_censor_s": RECOVERY_RIGHT_CENSOR_S,
            "rgd_unit": "delivery_ratio_second",
            "ccdf_quantile_points": CCDF_POINTS,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("index", nargs="?", type=Path,
                        help="JSON list of run-index entries")
    parser.add_argument("--index", dest="index_option", type=Path,
                        help="JSON list of run-index entries (option form)")
    parser.add_argument("--root", type=Path, default=None,
                        help="export directory (default: index parent)")
    parser.add_argument("--out", dest="root_option", type=Path, default=None,
                        help="alias for --root")
    args = parser.parse_args(argv)
    index_path = args.index_option or args.index
    if index_path is None:
        parser.error("an index JSON path is required")
    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        parser.error(f"cannot read index: {exc}")
    if not isinstance(value, list):
        parser.error("index JSON top level must be a list")
    root_override = args.root_option or args.root
    root = root_override if root_override is not None else index_path.resolve().parent
    try:
        result = export(root, value)
    except ExportValidationError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
