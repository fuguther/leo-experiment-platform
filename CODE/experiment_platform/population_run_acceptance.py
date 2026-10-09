"""Offline acceptance of the named 96-satellite population diagnostic.

The checker never starts the simulator.  It accepts only one receipt-bound,
completed development cell with the frozen small-region inputs and complete
four-arm replay.  The trace is recompiled from the run's receipt-bound config
and population snapshot; no checkout raster or network fetch is a fallback.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import hashlib
import json
import math
import os
import statistics
import sys
import tempfile
from pathlib import Path, PurePosixPath

from CODE.experiment_platform import t1_suite, t1_tasks
from CODE.leo_sim import config as config_mod
from CODE.leo_sim import trace as trace_mod
from CODE.scripts.remote import release_protocol


SCHEMA = "t1-population-run-acceptance/v1"
CELL_ID = "b-bounded_population_cost_smoke-network-seed-7"
ARMS = ("stale", "now", "common", "candidate")
EXPECTED_PACKETS = 1236
DEADLINE_S = 4.0
POPULATION_WINDOW_S = (2.0, 8.0)
HORIZON_S = 8.0
# Frozen resolved scientific settings, including geometry, service and control.
RESOLVED_CONFIG_SHA256 = "adf0e63800d0d3ab5175c12a0925e5091a5647307d8582d4d62138ee314928a9"
POPULATION_SHA256 = (
    "c5742d16fc01d454e8ac5c5345a7e7716883acd28ac4d0d34c24613bc315e59a")
PROFILE_POPULATION_PATH = (
    "CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif")
TOLERANCE = 1e-9
STUDY_BLOCKS = (
    "primary_steady", "primary_burst", "hold_last_burst",
    "frequent_advertisement_burst",
)
STUDY_EXPECTED_PACKETS = {"primary_steady": 844}
STUDY_EXPECTED_PACKETS.update({block: 9036 for block in STUDY_BLOCKS[1:]})


@dataclass(frozen=True)
class AcceptanceScope:
    """Immutable expected identities for one legacy cell or study block."""

    cell_id: str
    expected_packets: int
    expected_resolved_config_sha256: str
    block: str | None = None
    profile_path: str | None = None
    expected_profile_sha256: str | None = None
    expected_trace_sha256: str | None = None
    expected_rows_digest: str | None = None
    study_contract_sha256: str | None = None
    study_design_sha256: str | None = None
    input_compilation_sha256: str | None = None

    @property
    def is_study(self) -> bool:
        return self.block is not None


def _legacy_scope() -> AcceptanceScope:
    # Read the legacy constants at call time so existing tests and callers that
    # intentionally patch them retain their original behavior.
    return AcceptanceScope(
        cell_id=CELL_ID,
        expected_packets=EXPECTED_PACKETS,
        expected_resolved_config_sha256=RESOLVED_CONFIG_SHA256,
    )


class PopulationRunAcceptanceError(ValueError):
    """The supplied run cannot support this bounded acceptance check."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PopulationRunAcceptanceError(message)


def _finite(value, label: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))):
        raise PopulationRunAcceptanceError(f"{label} must be finite")
    return float(value)


def _integer(value, label: str, *, minimum: int = 0) -> int:
    if (isinstance(value, bool) or not isinstance(value, int)
            or value < minimum):
        raise PopulationRunAcceptanceError(
            f"{label} must be an integer >= {minimum}")
    return value


def _close(actual, expected: float, label: str) -> None:
    value = _finite(actual, label)
    if abs(value - expected) > TOLERANCE:
        raise PopulationRunAcceptanceError(
            f"{label} differs: recorded={value!r}, recomputed={expected!r}")


def _pid_map(value, label: str) -> dict[int, object]:
    if not isinstance(value, dict):
        raise PopulationRunAcceptanceError(f"{label} must be a mapping")
    parsed = {}
    for raw_pid, record in value.items():
        if isinstance(raw_pid, bool):
            raise PopulationRunAcceptanceError(f"{label} has invalid PID")
        if isinstance(raw_pid, int):
            pid = raw_pid
        elif isinstance(raw_pid, str) and raw_pid.isdecimal():
            pid = int(raw_pid)
            if str(pid) != raw_pid:
                raise PopulationRunAcceptanceError(
                    f"{label} has non-canonical PID key {raw_pid!r}")
        else:
            raise PopulationRunAcceptanceError(
                f"{label} has invalid PID key {raw_pid!r}")
        if pid < 0 or pid in parsed:
            raise PopulationRunAcceptanceError(
                f"{label} has duplicate or negative PID {pid}")
        parsed[pid] = record
    return parsed


def _counts_map(value, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise PopulationRunAcceptanceError(f"{label} must be a mapping")
    counts = {}
    for name, count in value.items():
        if not isinstance(name, str) or not name:
            raise PopulationRunAcceptanceError(f"{label} has an invalid fate name")
        counts[name] = _integer(count, f"{label}.{name}")
    return {name: count for name, count in counts.items() if count}


def deadline_loss(fate: str, *, emit_time_s: float,
                  delivered_at_s: float | None, stop_time_s: float,
                  deadline_s: float) -> dict:
    """Recompute one D4 value; administrative censoring keeps honest bounds."""
    emit = _finite(emit_time_s, "packet emission time")
    stop = _finite(stop_time_s, "observation stop")
    deadline = _finite(deadline_s, "deadline")
    _require(emit >= 0 and stop >= emit and deadline > 0,
             "invalid emission, stop, or deadline interval")
    if fate == "DELIVERED":
        _require(delivered_at_s is not None,
                 "DELIVERED packet lacks a delivery time")
        delivered = _finite(delivered_at_s, "delivery time")
        _require(emit <= delivered <= stop,
                 "delivery time is outside observation interval")
        value = min(max(delivered - emit, 0.0), deadline) / deadline
        return {"status": "COMPUTED", "value": value,
                "lower_bound": value, "upper_bound": value}
    if fate in t1_tasks.outcome_metrics.CENSORING_FATES:
        observed = stop - emit
        lower = min(observed / deadline, 1.0)
        if observed >= deadline:
            return {"status": "COMPUTED", "value": 1.0,
                    "lower_bound": 1.0, "upper_bound": 1.0}
        return {"status": "INTERVAL_CENSORED", "value": None,
                "lower_bound": lower, "upper_bound": 1.0}
    if fate in t1_tasks.outcome_metrics.TERMINAL_LOSS_FATES:
        return {"status": "COMPUTED", "value": 1.0,
                "lower_bound": 1.0, "upper_bound": 1.0}
    raise PopulationRunAcceptanceError(f"unrecognized fate {fate!r}")


def _linear_percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise PopulationRunAcceptanceError(
            "delivered latency is not computable without delivered packets")
    position = quantile * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    fraction = position - low
    return ordered[low] * (1.0 - fraction) + ordered[high] * fraction


def recompute_arm(arm_row: dict, trace_rows: list[dict], *, deadline_s: float,
                  population_window: tuple[float, float],
                  expected_stop_s: float, sidecar_path=None) -> dict:
    """Reconcile one arm's packet stream, fates and D4/E2E summaries."""
    if not isinstance(arm_row, dict):
        raise PopulationRunAcceptanceError("arm row is not a mapping")
    replay = arm_row.get("replay")
    _require(isinstance(replay, dict) and replay.get("captured") is True,
             "full per-arm replay is missing")
    arm = arm_row.get("arm")
    _require(arm in ARMS and replay.get("arm") == arm,
             "replay arm identity differs")
    stop = _finite(arm_row.get("stop_time_s"), "arm stop time")
    _close(replay.get("stop_time_s"), stop, f"{arm} replay stop time")
    _close(arm_row.get("horizon_s"), expected_stop_s,
           f"{arm} horizon")
    _close(replay.get("horizon_s"), expected_stop_s,
           f"{arm} replay horizon")
    _close(stop, expected_stop_s, f"{arm} observation stop")

    if not isinstance(trace_rows, list) or not trace_rows:
        raise PopulationRunAcceptanceError("rebuilt trace rows are missing")
    trace = {}
    emissions = {}
    for index, row in enumerate(trace_rows):
        if not isinstance(row, dict):
            raise PopulationRunAcceptanceError(
                f"rebuilt trace row {index} is not a mapping")
        pid = _integer(row.get("packet_id"), f"trace row {index} PID")
        emitted = _finite(row.get("emit_time_s"), "trace emission time")
        bits = _integer(row.get("bits"), f"trace PID {pid} bits", minimum=1)
        source, destination = row.get("src_grid_id"), row.get("dst_grid_id")
        _require(isinstance(source, str) and source
                 and isinstance(destination, str) and destination
                 and source != destination,
                 f"trace PID {pid} lacks distinct source/destination grid IDs")
        _require(0 <= emitted <= expected_stop_s,
                 f"trace PID {pid} emission is outside observation interval")
        _require(pid not in trace, f"duplicate PID {pid} in rebuilt trace")
        trace[pid] = row
        emissions[pid] = (emitted, bits)

    if replay.get("sidecar"):
        from CODE.experiment_platform import replay_sidecar
        _require(sidecar_path is not None and Path(sidecar_path).is_file(),
                 "declared replay sidecar is missing")
        raw_events = replay_sidecar.resolve(sidecar_path, replay,
                                            "packet_events")
        fates_raw = replay_sidecar.resolve_mapping(sidecar_path, replay,
                                                   "fates")
        deliveries_raw = replay_sidecar.resolve_mapping(sidecar_path, replay,
                                                        "deliveries")
    else:
        raw_events = replay.get("packet_events")
        fates_raw = replay.get("fates")
        deliveries_raw = replay.get("deliveries")
    _require(isinstance(raw_events, list), "packet_events stream is missing")
    emitted_events, delivered_events = {}, {}
    for index, event in enumerate(raw_events):
        if not isinstance(event, dict):
            raise PopulationRunAcceptanceError(
                f"packet event {index} is not a mapping")
        kind = event.get("kind")
        _require(isinstance(kind, str) and kind,
                 f"packet event {index} has no kind")
        at = _finite(event.get("at"), f"packet event {index} time")
        _require(0 <= at <= stop,
                 f"packet event {index} is outside observation interval")
        if kind not in ("packet_emitted", "delivered"):
            continue
        pid = _integer(event.get("pid"), f"{kind} event PID")
        _require(pid in trace,
                 f"{kind} event PID {pid} is absent from rebuilt trace")
        target = emitted_events if kind == "packet_emitted" else delivered_events
        if pid in target:
            raise PopulationRunAcceptanceError(
                f"duplicate {kind} event for PID {pid}")
        if kind == "packet_emitted":
            bits = _integer(event.get("bits"),
                            f"packet_emitted PID {pid} bits", minimum=1)
            target[pid] = (at, bits)
        else:
            target[pid] = at

    if set(emitted_events) != set(trace):
        raise PopulationRunAcceptanceError(
            "packet_emitted events do not exactly match rebuilt trace PIDs")
    for pid, expected in emissions.items():
        if emitted_events[pid][0] != expected[0] or emitted_events[pid][1] != expected[1]:
            raise PopulationRunAcceptanceError(
                f"packet_emitted PID {pid} differs from rebuilt trace time/bits")

    fates = _pid_map(fates_raw, "replay.fates")
    if set(fates) != set(trace):
        raise PopulationRunAcceptanceError(
            "trace/fate packet set mismatch")
    allowed_fates = (set(t1_tasks.outcome_metrics.TERMINAL_LOSS_FATES)
                     | set(t1_tasks.outcome_metrics.CENSORING_FATES)
                     | {"DELIVERED"})
    for pid, fate in fates.items():
        _require(isinstance(fate, str) and fate in allowed_fates,
                 f"PID {pid} has unrecognized fate {fate!r}")

    deliveries = _pid_map(deliveries_raw, "replay.deliveries")
    delivered_pids = {pid for pid, fate in fates.items()
                      if fate == "DELIVERED"}
    if set(delivered_events) != delivered_pids:
        raise PopulationRunAcceptanceError(
            "delivered events do not exactly match DELIVERED fate PIDs")
    if set(deliveries) != delivered_pids:
        raise PopulationRunAcceptanceError(
            "delivery records do not exactly match DELIVERED fate PIDs")
    delivery_times = {}
    for pid in sorted(delivered_pids):
        record = deliveries[pid]
        _require(isinstance(record, dict),
                 f"delivery record for PID {pid} is not a mapping")
        at = _finite(record.get("delivered_at"),
                     f"delivery record PID {pid} time")
        _require(abs(at - delivered_events[pid]) <= TOLERANCE,
                 f"delivery event and record differ for PID {pid}")
        _require(at >= emissions[pid][0],
                 f"delivery precedes emission for PID {pid}")
        delivery_times[pid] = at

    fate_counts = {}
    for fate in fates.values():
        fate_counts[fate] = fate_counts.get(fate, 0) + 1
    delivered_latencies = [delivery_times[pid] - emissions[pid][0]
                           for pid in sorted(delivered_pids)]
    delivered_mean = (statistics.fmean(delivered_latencies)
                      if delivered_latencies else None)
    delivered_p95 = (_linear_percentile(delivered_latencies, 0.95)
                     if delivered_latencies else None)

    start, end = map(float, population_window)
    population_rows = [row for row in trace_rows
                       if start <= float(row["emit_time_s"]) <= end]
    _require(bool(population_rows), "D4 population window is empty")
    d4_packets = []
    for row in population_rows:
        pid = int(row["packet_id"])
        d4_packets.append(deadline_loss(
            fates[pid], emit_time_s=float(row["emit_time_s"]),
            delivered_at_s=delivery_times.get(pid), stop_time_s=stop,
            deadline_s=deadline_s))
    exact = [packet["value"] for packet in d4_packets
             if packet["status"] == "COMPUTED"]
    lower = [packet["lower_bound"] for packet in d4_packets]
    upper = [packet["upper_bound"] for packet in d4_packets]
    exact_result = len(exact) == len(d4_packets)
    d4_value = statistics.fmean(exact) if exact_result else None
    d4_result = {
        "status": "COMPUTED" if exact_result else "PARTIAL_BOUNDS",
        "packets": len(d4_packets),
        "exact_packets": len(exact),
        "interval_censored": sum(
            packet["status"] == "INTERVAL_CENSORED" for packet in d4_packets),
        "not_computable_packets": 0,
        "value": d4_value,
        "lower_mean": statistics.fmean(lower),
        "upper_mean": statistics.fmean(upper),
    }

    scope = arm_row.get("scope") or {}
    outcome = arm_row.get("outcome") or {}
    network_outcome = arm_row.get("network_outcome") or {}
    counts = network_outcome.get("counts") or {}
    _require(scope.get("packets_in_trace") == len(trace)
             and outcome.get("offered") == len(trace)
             and counts.get("offered") == len(trace),
             f"{arm} offered counts differ from rebuilt trace")
    _require(outcome.get("delivered") == len(delivered_pids)
             and counts.get("delivered") == len(delivered_pids),
             f"{arm} delivered counts differ from replay")
    _require(_counts_map(outcome.get("fate_counts"),
                         f"{arm}.outcome.fate_counts") == fate_counts,
             f"{arm} reported fate counts differ from replay")
    if "fate_counts" in counts:
        _require(_counts_map(counts["fate_counts"],
                             f"{arm}.network_outcome.fate_counts") == fate_counts,
                 f"{arm} network fate counts differ from replay")
    _require(outcome.get("e2e_samples") == len(delivered_pids),
             f"{arm} delivered latency sample count differs from replay")
    if delivered_latencies:
        _close(outcome.get("e2e_mean_s"), delivered_mean,
               f"{arm} delivered latency mean")
        _close(outcome.get("e2e_p95_s"), delivered_p95,
               f"{arm} delivered latency p95")
    else:
        _require(outcome.get("e2e_mean_s") is None
                 and outcome.get("e2e_p95_s") is None,
                 f"{arm} has no deliveries; latency must be uncomputable")

    recorded_d4 = network_outcome.get("deadline_primary_loss")
    _require(isinstance(recorded_d4, dict),
             f"{arm} network D4 summary is missing")
    _require(recorded_d4.get("status") == d4_result["status"]
             and recorded_d4.get("packets") == d4_result["packets"]
             and recorded_d4.get("exact_packets") == d4_result["exact_packets"]
             and recorded_d4.get("interval_censored")
             == d4_result["interval_censored"]
             and recorded_d4.get("not_computable_packets") == 0,
             f"{arm} recorded D4 completeness differs from replay")
    if exact_result:
        _close(recorded_d4.get("value"), d4_value,
               f"{arm} recorded D4 mean")
    else:
        _require(recorded_d4.get("value") is None,
                 f"{arm} interval-censored D4 must not report a point value")
        _close(recorded_d4.get("lower_mean"), d4_result["lower_mean"],
               f"{arm} recorded D4 lower bound")
        _close(recorded_d4.get("upper_mean"), d4_result["upper_mean"],
               f"{arm} recorded D4 upper bound")

    return {
        "arm": arm,
        "counts": {"offered": len(trace), "delivered": len(delivered_pids),
                   **dict(sorted(fate_counts.items()))},
        "deadline_primary_loss": d4_result,
        "delivered_latency": {"packets": len(delivered_latencies),
                              "mean_s": delivered_mean,
                              "p95_s": delivered_p95},
        "trace_emissions_match": True,
    }


def _read_small_json(path: Path, label: str, *, maximum_bytes: int = 20_000_000):
    if path.is_symlink() or not path.is_file():
        raise PopulationRunAcceptanceError(f"{label} is missing or unsafe")
    if path.stat().st_size > maximum_bytes:
        raise PopulationRunAcceptanceError(f"{label} exceeds its size bound")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PopulationRunAcceptanceError(
            f"{label} is unreadable: {type(exc).__name__}: {exc}") from exc


def _read_json_with_sha(path: Path, label: str,
                        *, maximum_bytes: int = 20_000_000) -> tuple[dict, str]:
    if path.is_symlink() or not path.is_file():
        raise PopulationRunAcceptanceError(f"{label} is missing or unsafe")
    try:
        if path.stat().st_size > maximum_bytes:
            raise PopulationRunAcceptanceError(f"{label} exceeds its size bound")
        raw = path.read_bytes()
        value = json.loads(raw)
    except PopulationRunAcceptanceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PopulationRunAcceptanceError(
            f"{label} is unreadable: {type(exc).__name__}: {exc}") from exc
    _require(isinstance(value, dict), f"{label} must be a JSON object")
    return value, hashlib.sha256(raw).hexdigest()


def _is_sha256(value) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value))


def _study_profile_path(study_dir: Path, relative: str) -> Path:
    _require(isinstance(relative, str), "study profile path is missing")
    pure = PurePosixPath(relative)
    _require(pure.parts and not pure.is_absolute()
             and all(part not in ("", ".", "..") for part in pure.parts)
             and pure.parts[0] == "profiles",
             "study profile path is unsafe")
    path = study_dir / Path(*pure.parts)
    _require(not study_dir.is_symlink() and study_dir.is_dir(),
             "study design directory is missing or unsafe")
    cursor = path
    while cursor != study_dir:
        _require(not cursor.is_symlink(), "study profile path contains a symlink")
        cursor = cursor.parent
    _require(path.is_file(), "study profile file is missing")
    try:
        _require(path.resolve(strict=True).is_relative_to(
            study_dir.resolve(strict=True)),
            "study profile resolves outside the study design directory")
    except (OSError, RuntimeError) as exc:
        raise PopulationRunAcceptanceError(
            f"study profile path cannot be resolved: {exc}") from exc
    return path


def _unique_block_records(records, label: str) -> dict[str, dict]:
    _require(isinstance(records, list), f"{label} must be a list")
    mapped = {}
    for index, record in enumerate(records):
        _require(isinstance(record, dict),
                 f"{label}[{index}] must be an object")
        block = record.get("block")
        _require(isinstance(block, str) and block,
                 f"{label}[{index}] is missing block")
        _require(block not in mapped,
                 f"{label} contains duplicate block {block!r}")
        mapped[block] = record
    _require(set(mapped) == set(STUDY_BLOCKS),
             f"{label} does not contain the four frozen study blocks")
    return mapped


def _validate_frozen_config_identity(resolved: dict,
                                     scope: AcceptanceScope) -> None:
    cfg = resolved.get("config") or {}
    scenario, demand, endpoints = (
        cfg.get("scenario") or {}, cfg.get("demand") or {},
        cfg.get("endpoints") or {})
    _require(scenario.get("name") == "t1-population-small-region-cost-smoke"
             and scenario.get("duration_s") == HORIZON_S
             and scenario.get("seed") == 7
             and scenario.get("num_satellites") == 96
             and scenario.get("num_planes") == 12,
             "config is not the frozen 96-satellite seed-7 scenario")
    _require(demand.get("mode") == "population_gravity"
             and demand.get("population_path") == PROFILE_POPULATION_PATH
             and demand.get("emission_start_s") == 2.0
             and demand.get("emission_end_s") == 4.0
             and demand.get("packet_bits") == 12000
             and demand.get("research_deadline_s") == DEADLINE_S,
             "config demand differs from the frozen population trace")
    offered_mbps = demand.get("offered_mbps")
    _require(isinstance(offered_mbps, (int, float))
             and not isinstance(offered_mbps, bool) and offered_mbps > 0,
             "config offered rate must be positive")
    if not scope.is_study:
        _require(offered_mbps == 5.0,
                 "config demand differs from the frozen population trace")
    _require(endpoints.get("region_lat_bounds_deg") == [20.0, 30.0]
             and endpoints.get("region_lon_bounds_deg") == [100.0, 110.0],
             "config population region differs from the frozen scope")
    if scope.is_study:
        time_alignment = cfg.get("time_alignment") or {}
        _require(time_alignment.get("enabled") is True
                 and time_alignment.get("arm") == "now",
                 "study block profile must be the frozen now-arm launch profile")


def load_study_scope(study_design_dir: str | Path, block: str) -> AcceptanceScope:
    """Load and verify one block from an immutable, pre-run study plan."""
    study_dir = Path(study_design_dir).absolute()
    _require(not study_dir.is_symlink() and study_dir.is_dir(),
             "study design directory is missing or unsafe")
    design, design_sha = _read_json_with_sha(
        study_dir / "design.json", "study design")
    input_compilation, input_sha = _read_json_with_sha(
        study_dir / "input-compilation.json", "study input compilation")

    _require(design.get("schema") == "time-alignment-study/v1"
             and design.get("status") == "COMPILED_NOT_RUN"
             and design.get("simulator_calls") == 0
             and design.get("execution_authorized_by_this_file") is False,
             "study design is not the frozen compile-only contract")
    contract_sha = design.get("contract_sha256")
    _require(_is_sha256(contract_sha),
             "study contract SHA-256 is missing or malformed")
    _require(design.get("within_block_only_arm_changes") is True
             and design.get("trace_equivalence")
             == "must compile once per condition/seed and verify at runtime",
             "study design omits the frozen within-block trace contract")
    design_boundary = design.get("run_boundary") or {}
    _require(design_boundary.get("requires_matching_scope_validator_not_the_old_1236_packet_validator") is True,
             "study design does not require a matching scope validator")
    _require((design.get("primary") or {}).get("deadline_s") == DEADLINE_S,
             "study design deadline differs from the frozen D4 scope")

    execution = _unique_block_records(
        design.get("execution_blocks"), "study execution blocks")
    compiled_inputs = _unique_block_records(
        input_compilation.get("blocks"), "input compilation blocks")
    _require(input_compilation.get("simulator_calls") == 0
             and input_compilation.get(
                 "burst_trace_identical_across_predictor_and_advertisement_blocks")
             is True,
             "input compilation is not the frozen compile-only trace plan")
    _require(isinstance(block, str) and block in STUDY_BLOCKS,
             f"unknown study block {block!r}")

    exec_block = execution[block]
    required_options = {
        "--task": "network_alignment",
        "--deadline-s": "4.0",
        "--window-start": "2.0",
        "--window-end": "8.0",
        "--arms": ",".join(ARMS),
        "--capture-replay": True,
    }
    _require(exec_block.get("driver") == "CODE.experiment_platform.t1_tasks"
             and exec_block.get("task") == "network_alignment"
             and exec_block.get("arms") == list(ARMS)
             and exec_block.get("capture_replay") is True
             and exec_block.get("driver_options") == required_options,
             "study execution block differs from the frozen four-arm D4/stop-8 scope")
    profile_relative = exec_block.get("profile")

    cells = design.get("cells")
    _require(isinstance(cells, list), "study design cells must be a list")
    cell_ids = [cell.get("cell_id") for cell in cells if isinstance(cell, dict)]
    _require(len(cell_ids) == len(cells)
             and all(isinstance(cell_id, str) and cell_id for cell_id in cell_ids)
             and len(set(cell_ids)) == len(cell_ids),
             "study design cells have missing or duplicate cell IDs")
    block_cells = [cell for cell in cells
                   if cell.get("block") == block]
    _require(len(block_cells) == len(ARMS)
             and {cell.get("arm") for cell in block_cells} == set(ARMS),
             "study block cells do not contain each of the four arms exactly once")
    now_cells = [cell for cell in block_cells if cell.get("arm") == "now"]
    _require(len(now_cells) == 1, "study block has a duplicate or missing now cell")
    now_cell = now_cells[0]
    _require(now_cell.get("profile") == profile_relative,
             "execution profile differs from the design now-arm profile")

    input_record = compiled_inputs[block]
    expected_packets = input_record.get("packets")
    _require(expected_packets == STUDY_EXPECTED_PACKETS[block],
             "input compilation packet count differs from the frozen block count")
    trace_sha = input_record.get("trace_sha256")
    rows_digest = input_record.get("rows_digest")
    population_sha = input_record.get("population_sha256")
    _require(_is_sha256(trace_sha) and _is_sha256(rows_digest),
             "input compilation trace identity is missing or malformed")
    _require(population_sha == POPULATION_SHA256,
             "input compilation population raster differs from the frozen input")

    profile_sha = now_cell.get("profile_sha256")
    resolved_sha = now_cell.get("resolved_config_sha256")
    _require(_is_sha256(profile_sha) and _is_sha256(resolved_sha),
             "study now profile hashes are missing or malformed")
    profile_path = _study_profile_path(study_dir, profile_relative)
    try:
        profile_bytes = profile_path.read_bytes()
    except OSError as exc:
        raise PopulationRunAcceptanceError(
            f"study profile cannot be read: {exc}") from exc
    _require(hashlib.sha256(profile_bytes).hexdigest() == profile_sha,
             "study original profile SHA differs from design.json")
    try:
        resolved = config_mod.load_config_file(str(profile_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise PopulationRunAcceptanceError(
            f"study profile cannot be resolved: {exc}") from exc
    _require(resolved.get("sha256") == resolved_sha,
             "study profile resolved-config SHA differs from design.json")

    scope = AcceptanceScope(
        cell_id=f"b-{block}-network-seed-7",
        expected_packets=expected_packets,
        expected_resolved_config_sha256=resolved_sha,
        block=block,
        profile_path=profile_relative,
        expected_profile_sha256=profile_sha,
        expected_trace_sha256=trace_sha,
        expected_rows_digest=rows_digest,
        study_contract_sha256=contract_sha,
        study_design_sha256=design_sha,
        input_compilation_sha256=input_sha,
    )
    _validate_frozen_config_identity(resolved, scope)
    return scope


def _receipt_files(receipt: dict) -> dict[str, dict]:
    files = receipt.get("files")
    if not isinstance(files, list):
        raise PopulationRunAcceptanceError("verified receipt has no file list")
    result = {}
    for item in files:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise PopulationRunAcceptanceError("verified receipt file entry is malformed")
        if item["path"] in result:
            raise PopulationRunAcceptanceError("verified receipt has duplicate paths")
        result[item["path"]] = item
    return result


def _receipt_sha(files: dict[str, dict], relative_path: str) -> str:
    record = files.get(relative_path)
    _require(isinstance(record, dict),
             f"receipt does not bind {relative_path}")
    digest = record.get("sha256")
    _require(isinstance(digest, str) and len(digest) == 64,
             f"receipt SHA is malformed for {relative_path}")
    return digest


def _safe_bundle_config_path(dev_root: Path, cell: dict) -> tuple[Path, str]:
    input_record = cell.get("input") or {}
    files = input_record.get("files") or {}
    profile = files.get("profile_config") or {}
    identity = profile.get("identity")
    _require(isinstance(identity, str) and identity.startswith("bundle:"),
             "cell config is not bound to the compiled bundle")
    relative = PurePosixPath(identity[len("bundle:"):])
    _require(relative.parts and not relative.is_absolute()
             and all(part not in ("", ".", "..") for part in relative.parts)
             and relative.parts[0] == "configs",
             "cell config bundle identity is unsafe")
    local_path = dev_root / "bundle" / Path(*relative.parts)
    remote_path = input_record.get("config_path")
    _require(profile.get("path") == remote_path,
             "bundle config input path differs from cell binding")
    _require(input_record.get("config_identity") == identity,
             "cell config identity differs from profile input")
    digest = input_record.get("config_sha256")
    _require(isinstance(digest, str) and len(digest) == 64
             and profile.get("sha256") == digest,
             "cell config SHA binding is malformed")
    return local_path, digest


def _population_snapshot(run_dir: Path, manifest: dict,
                         bundle_cell: dict) -> tuple[Path, str]:
    inputs = manifest.get("inputs") or []
    matches = [item for item in inputs if isinstance(item, dict)
               and item.get("name") == "population_raster"]
    _require(len(matches) == 1,
             "run manifest must bind exactly one population raster snapshot")
    item = matches[0]
    rel = PurePosixPath(str(item.get("snapshot_path") or ""))
    _require(rel.parts and not rel.is_absolute()
             and all(part not in ("", ".", "..") for part in rel.parts)
             and rel.parts[0] == "inputs",
             "population raster snapshot path is unsafe")
    path = run_dir / Path(*rel.parts)
    cell_input = (bundle_cell.get("input") or {}).get("files") or {}
    population = cell_input.get("population_path") or {}
    digest = item.get("sha256")
    _require(digest == POPULATION_SHA256
             and population.get("sha256") == digest
             and population.get("identity")
             == f"source:{PROFILE_POPULATION_PATH}",
             "population raster snapshot differs from the frozen input")
    bound_path = PurePosixPath(str(population.get("path") or ""))
    _require(len(bound_path.parts) >= len(rel.parts)
             and bound_path.parts[-len(rel.parts):] == rel.parts,
             "bundle population path does not identify its run snapshot")
    return path, digest


def _validate_fixed_config(config_path: Path, expected_sha: str,
                           population_path: Path, source: dict,
                           scope: AcceptanceScope | None = None) -> dict:
    scope = scope or _legacy_scope()
    if config_path.is_symlink() or not config_path.is_file():
        raise PopulationRunAcceptanceError(
            "receipt-bound profile config snapshot is missing or unsafe")
    config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    _require(config_sha == expected_sha,
             "receipt-bound profile config SHA differs from bundle")
    if scope.expected_profile_sha256 is not None:
        _require(config_sha == scope.expected_profile_sha256,
                 "receipt-bound profile SHA differs from the study plan")
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise PopulationRunAcceptanceError(
            f"profile config cannot be resolved: {exc}") from exc
    _require(resolved["sha256"] == scope.expected_resolved_config_sha256,
             "config differs from the frozen resolved scientific configuration")
    _validate_frozen_config_identity(resolved, scope)
    source_sha = source.get("config_sha256")
    _require(source_sha == resolved["sha256"],
             "result resolved-config SHA differs from receipt-bound profile")
    # Retain the original resolved-config identity, but force trace generation
    # to use only the population raster snapshot verified in this run receipt.
    trace_resolved = copy.deepcopy(resolved)
    trace_resolved["config"]["demand"]["population_path"] = str(
        population_path.resolve())
    return trace_resolved


def _rebuild_trace(run_dir: Path, dev_root: Path, manifest: dict,
                   receipt_files: dict[str, dict], bundle_cell: dict,
                   source: dict,
                   scope: AcceptanceScope | None = None
                   ) -> tuple[list[dict], str, str]:
    scope = scope or _legacy_scope()
    if scope.is_study:
        _require(source.get("trace_sha256") == scope.expected_trace_sha256,
                 "result trace SHA differs from the study plan")
        _require(source.get("rows_digest") == scope.expected_rows_digest,
                 "result rows_digest differs from the study plan")
        _require(source.get("rows") == scope.expected_packets,
                 "result packet count differs from the study plan")
    config_path, config_sha = _safe_bundle_config_path(dev_root, bundle_cell)
    config_rel = config_path.relative_to(run_dir).as_posix()
    _require(_receipt_sha(receipt_files, config_rel) == config_sha,
             "profile config snapshot is not receipt-bound to the bundle SHA")
    population_path, population_sha = _population_snapshot(
        run_dir, manifest, bundle_cell)
    _require(_receipt_sha(receipt_files, population_path.relative_to(
        run_dir).as_posix()) == population_sha,
        "population raster snapshot is not receipt-bound")
    trace_resolved = (_validate_fixed_config(
        config_path, config_sha, population_path, source, scope)
        if scope.is_study else _validate_fixed_config(
            config_path, config_sha, population_path, source))
    try:
        with tempfile.TemporaryDirectory(prefix="population-acceptance-") as temp:
            trace_manifest = trace_mod.compile_trace(trace_resolved, temp)
            rows = trace_mod.load_trace(
                str(Path(temp) / "trace.csv"),
                horizon_s=trace_manifest["emission_end_s"],
                max_packets=trace_resolved["config"]["execution"][
                    "max_packets"])
    except Exception as exc:
        raise PopulationRunAcceptanceError(
            f"receipt-bound population trace could not be rebuilt: "
            f"{type(exc).__name__}: {exc}") from exc
    trace_sha = (trace_manifest.get("__trace_sha256")
                 or trace_manifest.get("trace_sha256"))
    rows_sha = t1_tasks._rows_digest(rows)
    if scope.is_study:
        _require(trace_sha == scope.expected_trace_sha256,
                 "rebuilt trace SHA differs from the study plan")
        _require(rows_sha == scope.expected_rows_digest,
                 "rebuilt trace rows_digest differs from the study plan")
    _require(trace_sha == source.get("trace_sha256"),
             "rebuilt trace SHA differs from the result source identity")
    _require(rows_sha == source.get("rows_digest"),
             "rebuilt trace rows_digest differs from the result source identity")
    _require(trace_manifest.get("input_sha256") == population_sha
             and trace_manifest.get("population", {}).get(
                 "source_sha256") == population_sha,
             "trace compiler did not use the receipt-bound population raster")
    _require(len(rows) == scope.expected_packets
             and source.get("rows") == scope.expected_packets,
             f"rebuilt trace is not the expected {scope.expected_packets}-packet trace")
    return rows, trace_sha, rows_sha


def _validate_bundle_cell(bundle: dict, run_doc: dict,
                          run_record: dict, cell_file: dict,
                          source_commit: str,
                          scope: AcceptanceScope | None = None
                          ) -> tuple[dict, Path]:
    scope = scope or _legacy_scope()
    _require(bundle.get("bundle_fingerprint")
             == run_doc.get("bundle_fingerprint"),
             "b_dev run and compiled bundle fingerprints differ")
    _require(((bundle.get("identity") or {}).get("git") or {}).get("commit")
             == source_commit,
             "compiled bundle source commit differs from run manifest")
    cell_matches = [cell for cell in bundle.get("cells", [])
                    if isinstance(cell, dict)
                    and cell.get("cell_id") == scope.cell_id]
    _require(len(cell_matches) == 1,
             "compiled bundle does not contain exactly one named scope cell")
    cell = cell_matches[0]
    _require(cell.get("group") == "b_round" and cell.get("seed") == 7,
             "compiled cell is not the named seed-7 b_round cell")
    _require(run_doc.get("schema") == t1_suite.SCHEMA_RUN
             and run_doc.get("tier") == "b_dev"
             and run_doc.get("status") == "ok",
             "b_dev run status is not ok")
    records = run_doc.get("cells")
    _require(isinstance(records, list) and len(records) == 1
             and records[0].get("cell_id") == scope.cell_id,
             "b_dev run must contain only the named scope cell")
    _require(run_record == records[0] == cell_file,
             "cell record differs between b_dev run and cell.json")
    _require(run_record.get("status") == "ok"
             and run_record.get("returncode") == 0
             and run_record.get("child_returncode") == 0,
             "named b_dev cell status or exit code is not ok")
    _require(run_record.get("predicate") == cell.get("predicate")
             and isinstance(run_record.get("predicate"), dict),
             "runtime cell predicate differs from compiled bundle")
    requirements = run_record["predicate"].get("require") or {}
    _require(requirements.get("require_full_replay") is True
             and requirements.get("require_native_population_trace") is True
             and requirements.get("require_routing_audit_log") is True
             and requirements.get("arms") == list(ARMS),
             "compiled predicate omits the frozen four-arm full-replay gates")
    _require((run_record.get("predicate_verdict") or {}).get("passed") is True,
             "recorded cell predicate did not pass")
    expected_config = run_record.get("input") or {}
    _require(expected_config == cell.get("input"),
             "cell input identity differs between run and bundle")
    expected_args = ["--task", "network_alignment", "--config",
                     str(expected_config.get("config_path")),
                     "--deadline-s", "4.0", "--window-start", "2.0",
                     "--window-end", "8.0", "--arms",
                     ",".join(ARMS), "--capture-replay"]
    actual_argv = run_record.get("argv")
    _require(cell.get("driver") == "CODE.experiment_platform.t1_tasks"
             and cell.get("args") == expected_args
             and isinstance(actual_argv, list)
             and len(actual_argv) == len(expected_args) + 5
             and actual_argv[1:3]
             == ["-m", "CODE.experiment_platform.t1_tasks"]
             and actual_argv[3:-2] == expected_args
             and actual_argv[-2] == "--out"
             and str(actual_argv[-1]).endswith(
                 f"/cells/{scope.cell_id}/result.json"),
             "cell invocation differs from the fixed four-arm D4/stop-8 scope")
    relative_result = run_record.get("result_path")
    expected_result = f"cells/{scope.cell_id}/result.json"
    _require(relative_result == expected_result,
             "cell primary result path differs from the fixed result.json path")
    return cell, PurePosixPath(relative_result)


def _accept_run(run_dir: Path,
                scope: AcceptanceScope | None = None) -> dict:
    scope = scope or _legacy_scope()
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise PopulationRunAcceptanceError("official run directory is missing or unsafe")
    run_id = run_dir.name
    try:
        receipt = release_protocol.verify_run_directory(
            run_dir, expected_run_id=run_id)
    except Exception as exc:
        raise PopulationRunAcceptanceError(
            f"official run receipt verification failed: {type(exc).__name__}: {exc}") from exc
    receipt_files = _receipt_files(receipt)
    manifest = _read_small_json(run_dir / "run-manifest.json", "run manifest")
    _require(manifest.get("status") == "completed"
             and manifest.get("exit_code") == 0,
             "run manifest must be completed with exit code 0")
    _require(manifest.get("execution_class") == "development",
             "only a T1 development diagnostic run is in scope")
    source_commit = manifest.get("source_git_commit")
    _require(isinstance(source_commit, str) and len(source_commit) == 40
             and all(char in "0123456789abcdef" for char in source_commit),
             "run manifest source commit is not a full SHA")
    _require(receipt.get("source_git_commit") == source_commit,
             "verified receipt source commit differs from run manifest")
    _receipt_sha(receipt_files, "run-manifest.json")

    dev_root = run_dir / "t1-development"
    bundle_path = dev_root / "bundle" / "bundle.json"
    run_path = dev_root / "b_dev" / "run.json"
    _receipt_sha(receipt_files, "t1-development/bundle/bundle.json")
    _receipt_sha(receipt_files, "t1-development/b_dev/run.json")
    bundle = _read_small_json(bundle_path, "compiled bundle")
    run_doc = _read_small_json(run_path, "b_dev run record")
    records = run_doc.get("cells")
    _require(isinstance(records, list) and len(records) == 1
             and isinstance(records[0], dict),
             "b_dev run must contain exactly one cell record")
    run_record = records[0]
    cell_dir = dev_root / "b_dev" / "cells" / scope.cell_id
    cell_record_path = cell_dir / "cell.json"
    _receipt_sha(receipt_files,
                 f"t1-development/b_dev/cells/{scope.cell_id}/cell.json")
    cell_file = _read_small_json(cell_record_path, "b_dev cell record")
    bundle_cell, result_rel = _validate_bundle_cell(
        bundle, run_doc, run_record, cell_file, source_commit, scope)

    # Check the expected primary result before any trace recompilation. A
    # summary sidecar is deliberately insufficient and failed runs stop here.
    result_path = dev_root / "b_dev" / Path(*result_rel.parts)
    result_rel_rooted = (Path("t1-development") / "b_dev" / Path(*result_rel.parts)).as_posix()
    result_receipt_sha = _receipt_sha(receipt_files, result_rel_rooted)
    if result_path.is_symlink() or not result_path.is_file():
        raise PopulationRunAcceptanceError(
            "full primary result.json is missing; summary-only runs are rejected")
    probe = t1_suite._inspect_result(result_path)
    _require(probe.get("exists") is True and probe.get("payload") is not None
             and probe.get("parse_error") is None,
             "full primary result.json is unreadable")
    result_sha = probe.get("sha256")
    _require(isinstance(result_sha, str) and result_sha == result_receipt_sha
             and result_sha == run_record.get("result_sha256"),
             "primary result SHA differs from receipt or cell record")
    _require(probe.get("schema") == t1_tasks.SCHEMA_TASK,
             "result.json is not a full T1 task result")
    payload = probe["payload"]
    _require(payload.get("driver_schema") == t1_tasks.SCHEMA_NETWORK
             and payload.get("task") == "network_alignment"
             and payload.get("status") == "ok"
             and payload.get("failed_units") == 0,
             "primary result is not a complete network_alignment result")
    _require(run_record.get("result_schema") == probe.get("schema"),
             "cell result schema differs from inspected primary result")
    verdict = t1_suite.check_predicate(payload, run_record["predicate"])
    _require(isinstance(verdict, dict) and verdict.get("passed") is True,
             "full primary result fails the compiled cell predicate")

    document = payload.get("document") or {}
    source = document.get("source") or {}
    identity = document.get("identity") or {}
    _require(document.get("status") == "ok"
             and document.get("failures") == [],
             "network alignment document reports failed arms")
    _require(identity.get("git", {}).get("commit") == source_commit,
             "network result source commit differs from run manifest")
    _require(document.get("deadline", {}).get("deadline_s") == DEADLINE_S
             and document.get("deadline", {}).get("population_window_s")
             == list(POPULATION_WINDOW_S),
             "network result D4/population window differs from frozen scope")
    fairness = document.get("fairness") or {}
    _require(fairness.get("same_trace") is True
             and fairness.get("same_seed") == 7,
             "network result does not declare the fixed common trace and seed")

    rows, trace_sha, rows_sha = _rebuild_trace(
        run_dir, dev_root, manifest, receipt_files, bundle_cell, source, scope)
    _require(len(rows) == scope.expected_packets,
             "rebuilt trace packet count differs from the selected scope")
    _require(source.get("native_population", {}).get("population_sha256")
             == POPULATION_SHA256,
             "network result population SHA differs from frozen snapshot")
    _require(fairness.get("rows_digest") == rows_sha,
             "four-arm fairness digest differs from rebuilt native trace")

    arm_rows = document.get("arms")
    _require(isinstance(arm_rows, list)
             and [row.get("arm") for row in arm_rows] == list(ARMS),
             "network result does not contain the exact four-arm order")
    arm_results = {}
    for arm_row in arm_rows:
        _require(arm_row.get("seed") == 7
                 and arm_row.get("resolved_arm") == arm_row.get("arm")
                 and arm_row.get("scope", {}).get("duration_s") == HORIZON_S
                 and arm_row.get("scope", {}).get("packets_in_trace")
                 == scope.expected_packets,
                 f"{arm_row.get('arm')} result scope differs from fixed run")
        reference = arm_row.get("replay") or {}
        arm_results[arm_row["arm"]] = recompute_arm(
            arm_row, rows, deadline_s=DEADLINE_S,
            population_window=POPULATION_WINDOW_S,
            expected_stop_s=HORIZON_S,
            sidecar_path=(Path(result_path).with_name(
                str(reference.get("sidecar")))
                if reference.get("sidecar") else None))
        d4 = arm_results[arm_row["arm"]]["deadline_primary_loss"]
        _require(d4["status"] == "COMPUTED"
                 and d4["packets"] == scope.expected_packets,
                 f"{arm_row['arm']} D4 is incomplete or interval-censored")

    d4_pairs, latency_pairs = {}, {}
    for left_index, left in enumerate(ARMS):
        for right in ARMS[left_index + 1:]:
            suffix = f"{right}_minus_{left}"
            d4_pairs[suffix] = (
                arm_results[right]["deadline_primary_loss"]["value"]
                - arm_results[left]["deadline_primary_loss"]["value"])
            right_mean = arm_results[right]["delivered_latency"]["mean_s"]
            left_mean = arm_results[left]["delivered_latency"]["mean_s"]
            latency_pairs[suffix] = (right_mean - left_mean
                                     if right_mean is not None and left_mean is not None
                                     else None)

    report = {
        "schema": SCHEMA,
        "status": "ACCEPTED_DATA",
        "validator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "analysis_execution_chain_sha256": t1_tasks.artifact_identity.source_identity(
            t1_tasks.artifact_identity.execution_chain_paths())["combined_sha256"],
        "scope": {
            "scope_kind": "only_named_96_diagnostic",
            "scenario": "t1_population_region_cost_smoke",
            "satellites": 96, "planes": 12, "seed": 7,
            "arms": list(ARMS), "offered_packets": scope.expected_packets,
            "deadline_s": DEADLINE_S, "population_window_s": list(
                POPULATION_WINDOW_S), "stop_time_s": HORIZON_S,
        },
        "claimable": False,
        "formal": False,
        "identity": {
            "run_id": run_id, "release_id": manifest.get("release_id"),
            "source_git_commit": source_commit,
            "receipt_sha256": receipt.get("receipt_sha256"),
            "result_sha256": result_sha,
            "cell_id": CELL_ID,
            "trace_sha256": trace_sha,
            "trace_rows_digest": rows_sha,
            "population_sha256": POPULATION_SHA256,
        },
        "trace_equivalence": {
            "four_arm_emissions_match_rebuilt_trace": True,
            "replay_event_fields": ["pid", "at", "bits"],
            "src_dst_basis": (
                "src_grid_id/dst_grid_id come from the receipt-bound trace "
                "recompiled from the run config+raster snapshot; their full "
                "rows are bound by the matching trace_sha256 and rows_digest, "
                "not fields on packet_emitted events"),
            "fairness.same_trace": True,
        },
        "arms": arm_results,
        "pairwise": {"D4_mean_change": d4_pairs,
                     "delivered_latency_mean_s_change": latency_pairs},
        "boundaries": [
            "Named deterministic 96-satellite diagnostic only; claimable=false and formal=false.",
            "A D4 value is a capped-delay metric, not a physical loss rate.",
            "IN_SYSTEM_AT_STOP remains administrative censoring in fate counts; exact D4 is computed only after the full deadline is observed.",
            "Packet fates, D4 and delivered latency are reconciled offline from the receipt-bound full replay.",
            "No per-hop resource reconstruction, causal counterfactual, multi-seed statistic, or real-LEO population claim is made.",
            "A zero or negative arm difference is accepted data and is not a benefit claim.",
            "Delivered-only latency differences describe potentially different delivered populations; they are not paired all-offered causal effects. Zero-delivery latency is null, never zero.",
        ],
    }
    if scope.is_study:
        report["scope"].update({
            "scope_kind": "frozen_study_block",
            "block": scope.block,
            "cell_id": scope.cell_id,
        })
        report["scope"]["expected_plan"] = {
            "study_contract_sha256": scope.study_contract_sha256,
            "design_sha256": scope.study_design_sha256,
            "input_compilation_sha256": scope.input_compilation_sha256,
            "profile": scope.profile_path,
            "profile_sha256": scope.expected_profile_sha256,
            "resolved_config_sha256": scope.expected_resolved_config_sha256,
            "packets": scope.expected_packets,
            "trace_sha256": scope.expected_trace_sha256,
            "rows_digest": scope.expected_rows_digest,
        }
        report["identity"].update({
            "study_contract_sha256": scope.study_contract_sha256,
            "study_design_sha256": scope.study_design_sha256,
            "input_compilation_sha256": scope.input_compilation_sha256,
        })
        report["identity"]["cell_id"] = scope.cell_id
    return report


def accept_run(run_dir: str | Path,
               scope: AcceptanceScope | None = None) -> dict:
    """Verify one official run and return a non-formal bounded data record."""
    try:
        return _accept_run(Path(run_dir).absolute(), scope)
    except PopulationRunAcceptanceError:
        raise
    except Exception as exc:
        raise PopulationRunAcceptanceError(
            f"run acceptance failed closed: {type(exc).__name__}: {exc}") from exc


def _write_new_json(path: Path, payload: dict) -> None:
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise PopulationRunAcceptanceError(
            f"output must be a new file: {path}")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise PopulationRunAcceptanceError(
            f"output parent must be an existing real directory: {path.parent}")
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.",
                                         suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True,
                      indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except FileExistsError as exc:
        raise PopulationRunAcceptanceError(
            f"output must be a new file: {path}") from exc
    except (OSError, TypeError, ValueError) as exc:
        raise PopulationRunAcceptanceError(
            f"cannot atomically publish acceptance output: {exc}") from exc
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _validate_output_location(run_dir: Path, out_path: Path) -> Path:
    run_root = Path(run_dir).resolve(strict=False)
    output = Path(out_path).absolute()
    resolved_parent = output.parent.resolve(strict=False)
    resolved_target = resolved_parent / output.name
    if (resolved_parent == run_root or run_root in resolved_parent.parents
            or resolved_target == run_root or run_root in resolved_target.parents):
        raise PopulationRunAcceptanceError(
            "acceptance output may not be written inside the source run")
    if output.exists() or output.is_symlink() or resolved_target.exists() \
            or resolved_target.is_symlink():
        raise PopulationRunAcceptanceError(
            f"output must be a new file: {output}")
    if not resolved_parent.is_dir():
        raise PopulationRunAcceptanceError(
            f"output parent must resolve to an existing directory: "
            f"{output.parent}")
    return resolved_target


def _write_run_report(run_dir: Path, out_path: Path, payload: dict) -> None:
    output = _validate_output_location(run_dir, out_path)
    _write_new_json(output, payload)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="official returned run directory with verified receipt")
    parser.add_argument("--out", type=Path, required=True,
                        help="new JSON output path; existing files are never overwritten")
    parser.add_argument("--study-design-dir", type=Path,
                        help="validated study plan directory containing design.json, input-compilation.json, and profiles/")
    parser.add_argument("--block",
                        help="one frozen study execution block; requires --study-design-dir")
    args = parser.parse_args(argv)
    run_dir = args.run_dir.absolute()
    try:
        out_path = _validate_output_location(run_dir, args.out)
    except PopulationRunAcceptanceError as exc:
        print(f"population-run-acceptance: {exc}", file=sys.stderr)
        return 2
    try:
        has_study_dir = args.study_design_dir is not None
        has_block = args.block is not None
        _require(has_study_dir == has_block,
                 "--study-design-dir and --block must be provided together")
        scope = (load_study_scope(args.study_design_dir, args.block)
                 if has_study_dir else None)
        report = (accept_run(run_dir) if scope is None
                  else accept_run(run_dir, scope))
    except Exception as exc:
        report = {
            "schema": SCHEMA, "status": "REJECTED",
            "claimable": False, "formal": False,
            "run_dir": str(args.run_dir),
            "reasons": [f"{type(exc).__name__}: {exc}"],
        }
        try:
            _write_run_report(run_dir, out_path, report)
        except Exception as output_exc:
            print(f"population-run-acceptance: {output_exc}", file=sys.stderr)
            return 2
        print(json.dumps({"status": "REJECTED", "claimable": False,
                          "out": str(args.out)}, ensure_ascii=False))
        print(report["reasons"][0], file=sys.stderr)
        return 2
    try:
        _write_run_report(run_dir, out_path, report)
    except PopulationRunAcceptanceError as exc:
        print(f"population-run-acceptance: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "claimable": False,
                      "out": str(args.out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
