"""Bounded, one-arm-at-a-time load calibration diagnostic.

This entry point is deliberately independent of ``t1_suite``.  Its output is
labelled NOT_FULL_REPLAY_DIAGNOSTIC: it is not formal evidence and does not
capture a full replay.  It resolves one contract cell before compiling its
trace, then calls the kernel once for each listed arm and streams audit sinks
to gzip files without changing the rows passed to the kernel.
"""
from __future__ import annotations

import argparse
import copy
import gzip
import hashlib
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

from CODE.experiment_platform import t1_tasks
from CODE.leo_sim import config as config_mod
from CODE.leo_sim import kernel


CONTRACT_SCHEMA = "load-calibration-contract/v1"
DIAGNOSTIC_LABEL = "NOT_FULL_REPLAY_DIAGNOSTIC"
BASE_PROFILE = "CODE/leo_sim/profiles/t1_population_region_cost_smoke.yaml"
#: Profiles a diagnostic contract may select. The smoke profile spreads demand
#: over the global population proxy; the corridor profile is the analytic
#: single-OD pressure window whose directed ISL utilisation is min(1,
#: offered_mbps / isl_rate_mbps) in closed form, which is the only shape in
#: which the candidate resources can be driven into sustained queue growth.
ALLOWED_PROFILES = (
    BASE_PROFILE,
    "CODE/leo_sim/profiles/t1_pressure_corridor.yaml",
    "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml",
)
ALLOWED_ARMS = ("stale", "now", "common", "candidate")
MAX_OUTPUT_BYTES = 10 * 1024**3
MIN_FREE_BYTES = 5 * 1024**3
SUMMARY_RESERVE_BYTES = 1024 * 1024

OVERRIDE_FIELDS = {
    "access": {"slots_per_satellite", "uplink_rate_mbps",
               "downlink_rate_mbps"},
    "control_plane": {"advertise_interval_s"},
    "demand": {"offered_mbps", "packet_bits", "emission_start_s",
               "emission_end_s", "burst_start_s", "burst_duration_s",
               "burst_multiplier"},
    "execution": {"compute_servers_per_satellite", "compute_delay_s",
                  "max_packets"},
    "links": {"rf_isl", "rate_model", "isl_rate_mbps"},
    "routing": {"policy"},
    "scenario": {"duration_s", "seed"},
}


class CalibrationError(RuntimeError):
    """A contract, boundary, or artifact invariant was violated."""


class OutputLimitError(CalibrationError):
    """The per-run on-disk output limit was reached."""


def _json_default(value):
    """Convert only known NumPy values; never stringify unknown objects."""
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False,
                       default=_json_default) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _deep_merge(target: dict, patch: dict) -> dict:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)
    return target


def _validate_override_shape(overrides, cell_id: str) -> None:
    if not isinstance(overrides, dict):
        raise CalibrationError(f"cell {cell_id}: overrides must be an object")
    for group, values in overrides.items():
        if (not isinstance(group, str) or group not in OVERRIDE_FIELDS
                or not isinstance(values, dict)):
            raise CalibrationError(
                f"cell {cell_id}: unsupported override group {group!r}")
        unknown = set(values) - OVERRIDE_FIELDS[group]
        if unknown:
            raise CalibrationError(
                f"cell {cell_id}: unsupported override fields in {group}: "
                f"{sorted(unknown)}")
        if not values:
            raise CalibrationError(
                f"cell {cell_id}: empty override group {group!r}")
        if group == "links" and "rf_isl" in values \
                and not isinstance(values["rf_isl"], dict):
            raise CalibrationError(
                f"cell {cell_id}: links.rf_isl must be a mapping")
        if group == "routing":
            policy = values.get("policy")
            if policy not in config_mod.VALID_POLICIES:
                raise CalibrationError(
                    f"cell {cell_id}: routing.policy must be one of "
                    f"{sorted(config_mod.VALID_POLICIES)}, got {policy!r}")


def _design_overrides(base_resolved: dict, overrides: dict) -> dict:
    """Expand nested RF overrides so t1_tasks.design's shallow group update
    receives a complete mapping while keeping every unmentioned base field.
    """
    result = copy.deepcopy(overrides)
    link_patch = result.get("links")
    if isinstance(link_patch, dict) and "rf_isl" in link_patch:
        merged = copy.deepcopy(base_resolved["config"]["links"]["rf_isl"])
        link_patch["rf_isl"] = _deep_merge(merged, link_patch["rf_isl"])
    return result


def _validate_effective_config(resolved: dict, contract: dict,
                               cell_id: str) -> None:
    cfg = resolved["config"]
    if cfg["execution"]["max_packets"] > contract["max_packets"]:
        raise CalibrationError(
            f"cell {cell_id}: execution.max_packets exceeds contract cap")
    if cfg["scenario"]["duration_s"] > contract["max_duration_s"]:
        raise CalibrationError(
            f"cell {cell_id}: scenario.duration_s exceeds contract cap")
    cell = next((entry for entry in contract["cells"]
                 if entry.get("id") == cell_id), None)
    declared = ((cell or {}).get("overrides") or {}).get("routing") or {}
    if "policy" in declared and cfg["routing"]["policy"] != declared["policy"]:
        raise CalibrationError(
            f"cell {cell_id}: declared routing.policy {declared['policy']!r} did "
            f"not take effect (resolved {cfg['routing']['policy']!r})")


def _validate_contract_document(document: dict) -> None:
    expected = {"schema", "base_profile", "deadline_s", "max_packets",
                "max_duration_s", "cells"}
    if not isinstance(document, dict) or set(document) != expected:
        raise CalibrationError("contract has missing or unknown fields")
    if document["schema"] != CONTRACT_SCHEMA:
        raise CalibrationError("unsupported load calibration contract schema")
    if document["base_profile"] not in ALLOWED_PROFILES:
        raise CalibrationError(
            f"contract base_profile must be one of {list(ALLOWED_PROFILES)}, "
            f"got {document['base_profile']!r}")
    if (isinstance(document["deadline_s"], bool)
            or not isinstance(document["deadline_s"], (int, float))
            or float(document["deadline_s"]) != 4.0):
        raise CalibrationError("contract deadline_s must be the frozen D4=4")
    if (isinstance(document["max_packets"], bool)
            or not isinstance(document["max_packets"], int)
            or document["max_packets"] != 20000):
        raise CalibrationError("contract max_packets must be 20000")
    if (isinstance(document["max_duration_s"], bool)
            or not isinstance(document["max_duration_s"], (int, float))
            or float(document["max_duration_s"]) != 12.0):
        raise CalibrationError("contract max_duration_s must be 12")
    if not isinstance(document["cells"], list) or not document["cells"]:
        raise CalibrationError("contract cells must be a non-empty list")


def _read_contract(contract_path: Path, repo_root: Path):
    try:
        raw = contract_path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"contract is unreadable JSON: {exc}") from exc
    _validate_contract_document(document)
    profile_path = repo_root / document["base_profile"]
    if not profile_path.is_file() or profile_path.is_symlink():
        raise CalibrationError(f"frozen base profile is unavailable: {profile_path}")
    try:
        base_resolved = config_mod.load_config_file(str(profile_path))
    except (config_mod.ConfigError, OSError) as exc:
        raise CalibrationError(f"base profile is invalid: {exc}") from exc

    seen = set()
    validated = {}
    for cell in document["cells"]:
        if not isinstance(cell, dict) or set(cell) != {"id", "arms", "overrides"}:
            raise CalibrationError("each cell must contain exactly id, arms, overrides")
        cell_id = cell["id"]
        if (not isinstance(cell_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", cell_id)):
            raise CalibrationError(f"invalid cell id: {cell_id!r}")
        if cell_id in seen:
            raise CalibrationError(f"duplicate cell id: {cell_id}")
        seen.add(cell_id)
        arms = cell["arms"]
        if (not isinstance(arms, list) or not arms
                or any(not isinstance(arm, str) or arm not in ALLOWED_ARMS
                       for arm in arms)
                or len(set(arms)) != len(arms)):
            raise CalibrationError(
                f"cell {cell_id}: arms must be unique names from {ALLOWED_ARMS}")
        _validate_override_shape(cell["overrides"], cell_id)
        normalized = _design_overrides(base_resolved, cell["overrides"])
        cfg = copy.deepcopy(base_resolved["config"])
        _deep_merge(cfg, normalized)
        try:
            resolved = config_mod.resolve_config(cfg)
        except config_mod.ConfigError as exc:
            raise CalibrationError(f"cell {cell_id}: resolved config is invalid: {exc}") from exc
        _validate_effective_config(resolved, document, cell_id)
        validated[cell_id] = {
            "cell": copy.deepcopy(cell),
            "resolved": resolved,
            "design_overrides": normalized,
        }
    return raw, document, base_resolved, validated


def _check_output_destination(out_dir: Path) -> int:
    """Validate the destination and create any missing ancestor directories.

    The destination itself must not exist.  The sanctioned runner hands the
    entry point a fresh run directory whose inner directories do not exist yet,
    so missing ancestors are created after every level is checked for symlinks;
    no path component can silently redirect the output outside the run
    directory.
    """
    if out_dir.exists() or out_dir.is_symlink():
        raise CalibrationError(f"output destination already exists: {out_dir}")
    missing = []
    parent = out_dir.parent
    while not parent.exists():
        if parent.is_symlink():
            raise CalibrationError(f"refusing symlinked output parent: {parent}")
        missing.append(parent)
        if parent.parent == parent:
            raise CalibrationError(f"no existing ancestor for output: {out_dir}")
        parent = parent.parent
    if not parent.is_dir() or parent.is_symlink():
        raise CalibrationError(f"output parent must be a real directory: {parent}")
    for path in reversed(missing):
        path.mkdir(mode=0o700)
    free = shutil.disk_usage(out_dir.parent).free
    if free < MIN_FREE_BYTES:
        raise CalibrationError(
            f"less than 5 GiB free before run: {free} bytes available")
    return free


class _BudgetedFile:
    """Exclusive raw file that enforces one compressed-output budget."""
    def __init__(self, path: Path, budget):
        self.path = path
        self.budget = budget
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.file = os.fdopen(fd, "wb")
        budget.paths.add(path)

    def write(self, data):
        data = bytes(data)
        remaining = self.budget.limit - self.budget.used
        if len(data) > remaining:
            if remaining > 0:
                written = self.file.write(data[:remaining])
                self.budget.used += written
            self.budget.exhausted = True
            raise OutputLimitError("per-run output limit of 10 GiB reached")
        written = self.file.write(data)
        self.budget.used += written
        return written

    def flush(self):
        self.file.flush()

    def fileno(self):
        return self.file.fileno()

    def close(self):
        self.file.close()


class _OutputBudget:
    def __init__(self, limit=MAX_OUTPUT_BYTES - SUMMARY_RESERVE_BYTES):
        self.limit = limit
        self.used = 0
        self.exhausted = False
        self.paths: set[Path] = set()


class _GzipWriter:
    def __init__(self, path: Path, budget: _OutputBudget):
        self.raw = _BudgetedFile(path, budget)
        try:
            self.gz = gzip.GzipFile(fileobj=self.raw, mode="wb", mtime=0)
        except Exception:
            self.raw.close()
            raise
        self.closed = False

    def write(self, data: bytes):
        self.gz.write(data)

    def close(self):
        if self.closed:
            return
        self.closed = True
        error = None
        try:
            self.gz.close()
        except Exception as exc:  # retain partial file and report the first failure
            error = exc
        try:
            self.raw.close()
        except Exception as exc:
            if error is None:
                error = exc
        if error is not None:
            raise error


class _JsonlSink:
    def __init__(self, path: Path, budget: _OutputBudget, kind: str):
        self.writer = _GzipWriter(path, budget)
        self.kind = kind
        self.count = 0
        self.forward_pids: set[int] = set()
        self.nonzero_resource_work_candidates = 0
        self.decisions_with_two_complete_candidates = 0

    @staticmethod
    def _project_decision(row):
        identity = ("t", "pid", "sat", "decision_id", "kind", "candidates",
                    "chosen", "t_decision_start")
        projected = {key: row[key] for key in identity if key in row}
        observation = row.get("observation_at_start")
        if isinstance(observation, dict):
            projected["observation_at_start"] = {
                key: observation[key]
                for key in ("time_alignment", "legal_directions",
                            "four_direction_audit")
                if key in observation
            }
        for key in ("truth_at_commit", "estimate_at_start"):
            if key in row:
                projected[key] = row[key]
        return projected

    def append(self, row):
        if not isinstance(row, dict):
            raise CalibrationError(f"{self.kind} sink received a non-object row")
        if self.kind == "decision":
            record = self._project_decision(row)
            if row.get("kind") == "forward" and row.get("pid") is not None:
                pid = row["pid"]
                if isinstance(pid, bool) or not isinstance(pid, int):
                    raise CalibrationError("forward decision pid must be an integer")
                self.forward_pids.add(pid)
            observation = row.get("observation_at_start")
            audit = observation.get("time_alignment") if isinstance(observation, dict) else None
            if isinstance(audit, dict):
                legal = observation.get("legal_directions")
                scores = audit.get("scores")
                if isinstance(legal, list) and isinstance(scores, dict):
                    complete = 0
                    for direction in legal:
                        score = scores.get(direction)
                        if not isinstance(score, dict):
                            continue
                        terms = score.get("terms")
                        work = terms.get("resource_work_s") if isinstance(terms, dict) else None
                        if (isinstance(work, (int, float)) and not isinstance(work, bool)
                                and math.isfinite(float(work)) and float(work) > 0.0):
                            self.nonzero_resource_work_candidates += 1
                        total = score.get("total_s")
                        if (score.get("missing") == []
                                and isinstance(total, (int, float))
                                and not isinstance(total, bool)
                                and math.isfinite(float(total))):
                            complete += 1
                    if complete >= 2:
                        self.decisions_with_two_complete_candidates += 1
        else:
            record = row
        self.writer.write(_json_bytes(record))
        self.count += 1

    def close(self):
        self.writer.close()

    def summary(self):
        return {
            "rows": self.count,
            "forward_pids_count": len(self.forward_pids),
            "forward_pids": sorted(self.forward_pids),
            "nonzero_resource_work_candidate_count":
                self.nonzero_resource_work_candidates,
            "decisions_with_at_least_two_complete_candidates":
                self.decisions_with_two_complete_candidates,
        }


def _write_gzip_json(path: Path, value, budget: _OutputBudget) -> None:
    writer = _GzipWriter(path, budget)
    first_error = None
    try:
        encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False,
                                   default=_json_default)
        for chunk in encoder.iterencode(value):
            writer.write(chunk.encode("utf-8"))
        writer.write(b"\n")
    except Exception as exc:
        first_error = exc
    try:
        writer.close()
    except Exception as exc:
        if first_error is None:
            first_error = exc
    if first_error is not None:
        raise first_error


def _write_json_artifact(path: Path, value, budget: _OutputBudget) -> None:
    raw = _BudgetedFile(path, budget)
    try:
        raw.write(_json_bytes(value))
        raw.flush()
        os.fsync(raw.fileno())
    finally:
        raw.close()


def _write_bytes_artifact(path: Path, payload: bytes,
                          budget: _OutputBudget) -> None:
    raw = _BudgetedFile(path, budget)
    try:
        raw.write(payload)
        raw.flush()
        os.fsync(raw.fileno())
    finally:
        raw.close()


def _input_bindings(resolved: dict, repo_root: Path) -> dict:
    logical = resolved["config"].get("demand", {}).get("population_path")
    configured = None
    if isinstance(logical, str):
        candidate = Path(logical)
        configured = str(candidate if candidate.is_absolute()
                         else (repo_root / candidate).resolve())
    snapshot = os.environ.get("T1_INPUT_POPULATION_RASTER")
    return {
        "logical_population_path": logical,
        "T1_INPUT_POPULATION_RASTER": snapshot,
        "physical_population_path": snapshot or configured,
        "physical_path_source": ("T1_INPUT_POPULATION_RASTER"
                                  if snapshot is not None else "profile path"),
    }


def _write_trace(path: Path, rows, budget: _OutputBudget) -> dict:
    writer = _GzipWriter(path, budget)
    digest = hashlib.sha256()
    count = 0
    first_error = None
    try:
        for row in rows:
            line = _json_bytes(row)
            digest.update(line)
            writer.write(line)
            count += 1
    except Exception as exc:
        first_error = exc
    try:
        writer.close()
    except Exception as exc:
        if first_error is None:
            first_error = exc
    if first_error is not None:
        raise first_error
    return {"rows": count, "jsonl_sha256": digest.hexdigest()}


def _finite_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def d4_outcome(result: dict, rows: list[dict], deadline_s: float) -> dict:
    """Recompute whole-trace D4 outcomes from emissions and deliveries only."""
    events = result.get("packet_events")
    deliveries = result.get("deliveries")
    stop = result.get("stop_time_s")
    if not isinstance(events, list) or not isinstance(deliveries, dict) \
            or not _finite_number(stop):
        return {"status": "NOT_COMPARABLE", "reason": "required_fields_missing",
                "deadline_s": deadline_s, "offered_packets": None,
                "deadline_not_delivered_proportion": None,
                "capped_loss": None}

    expected = {}
    for row in rows:
        pid, emitted = row.get("packet_id"), row.get("emit_time_s")
        if pid is None or not _finite_number(emitted) or str(pid) in expected:
            return {"status": "NOT_COMPARABLE", "reason": "invalid_trace_identity",
                    "deadline_s": deadline_s, "offered_packets": None,
                    "deadline_not_delivered_proportion": None,
                    "capped_loss": None}
        expected[str(pid)] = float(emitted)

    emissions = {}
    for event in events:
        if isinstance(event, dict) and event.get("kind") == "packet_emitted":
            pid, emitted = event.get("pid"), event.get("at")
            key = str(pid)
            if pid is None or key in emissions or not _finite_number(emitted):
                return {"status": "NOT_COMPARABLE", "reason": "invalid_emission_ledger",
                        "deadline_s": deadline_s, "offered_packets": None,
                        "deadline_not_delivered_proportion": None,
                        "capped_loss": None}
            emissions[key] = float(emitted)
    if not emissions:
        return {"status": "NOT_COMPARABLE", "reason": "no_packet_emissions",
                "deadline_s": deadline_s, "offered_packets": 0,
                "deadline_not_delivered_proportion": None,
                "capped_loss": None}
    if set(emissions) != set(expected):
        return {"status": "NOT_COMPARABLE", "reason": "incomplete_packet_emission_set",
                "deadline_s": deadline_s, "offered_packets": len(emissions),
                "expected_trace_packets": len(expected),
                "deadline_not_delivered_proportion": None,
                "capped_loss": None}
    if any(abs(emissions[pid] - expected[pid]) > 1e-9 for pid in expected):
        return {"status": "NOT_COMPARABLE", "reason": "emission_time_mismatch",
                "deadline_s": deadline_s, "offered_packets": len(emissions),
                "deadline_not_delivered_proportion": None,
                "capped_loss": None}
    if float(stop) + 1e-9 < max(emissions.values()) + deadline_s:
        return {"status": "NOT_COMPARABLE", "reason": "stop_before_all_D4_windows_matured",
                "deadline_s": deadline_s, "offered_packets": len(emissions),
                "stop_time_s": float(stop),
                "latest_required_stop_s": max(emissions.values()) + deadline_s,
                "deadline_not_delivered_proportion": None,
                "capped_loss": None}

    delivery_by_pid = {str(pid): value for pid, value in deliveries.items()}
    if set(delivery_by_pid) - set(emissions):
        return {"status": "NOT_COMPARABLE", "reason": "delivery_without_emission",
                "deadline_s": deadline_s, "offered_packets": len(emissions),
                "deadline_not_delivered_proportion": None,
                "capped_loss": None}
    not_delivered = 0
    capped_losses = []
    for pid, emitted in emissions.items():
        delivery = delivery_by_pid.get(pid)
        if delivery is None:
            not_delivered += 1
            capped_losses.append(1.0)
            continue
        delivered_at = delivery.get("delivered_at") if isinstance(delivery, dict) else None
        if not _finite_number(delivered_at) or float(delivered_at) < emitted:
            return {"status": "NOT_COMPARABLE", "reason": "invalid_delivery_time",
                    "deadline_s": deadline_s, "offered_packets": len(emissions),
                    "deadline_not_delivered_proportion": None,
                    "capped_loss": None}
        delay = float(delivered_at) - emitted
        if delay > deadline_s:
            not_delivered += 1
        capped_losses.append(min(delay / deadline_s, 1.0))
    offered = len(emissions)
    return {
        "status": "COMPUTED",
        "rule": "whole_trace packet_emitted ledger; loss=not delivered by fixed D4; capped_loss=mean(min(delay/D4,1)); missing delivery counts as loss only after every emitted D4 window matured",
        "deadline_s": deadline_s,
        "offered_packets": offered,
        "deadline_not_delivered_count": not_delivered,
        "deadline_not_delivered_proportion": not_delivered / offered,
        "capped_loss": sum(capped_losses) / offered,
        "stop_time_s": float(stop),
        "latest_required_stop_s": max(emissions.values()) + deadline_s,
    }


def _artifact_manifest(out_dir: Path, excluded=()) -> list[dict]:
    excluded = set(excluded)
    manifest = []
    for path in sorted(out_dir.rglob("*")):
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise CalibrationError(f"unexpected output artifact type: {path}")
        if path in excluded:
            continue
        digest, size = _sha256_file(path)
        manifest.append({"path": path.relative_to(out_dir).as_posix(),
                         "size_bytes": size, "sha256": digest})
    return manifest


def _atomic_summary(out_dir: Path, summary: dict) -> None:
    final = out_dir / "summary.json"
    temporary = out_dir / ".summary.json.pending"
    if final.exists() or final.is_symlink() or temporary.exists() or temporary.is_symlink():
        raise CalibrationError("summary destination already exists")
    payload = _json_bytes(summary)
    if len(payload) > SUMMARY_RESERVE_BYTES:
        raise CalibrationError("summary exceeds its 1 MiB reserved output budget")
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, final)
        os.unlink(temporary)
        directory_fd = os.open(out_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        # Both files are created exclusively by this function.  Preserve any
        # artifact that was present before the call; clean only this temp.
        if temporary.exists() and not temporary.is_symlink():
            temporary.unlink()
        raise


def _summary_error(exc: Exception) -> dict:
    return {"type": type(exc).__name__, "message": str(exc)}


def preflight(contract_path: Path, cell_id: str, out_dir: Path,
              repo_root: Path | None = None) -> dict:
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2])
    contract_path = Path(contract_path)
    out_dir = Path(out_dir).absolute()
    raw, contract, _base, cells = _read_contract(contract_path, repo_root)
    if cell_id not in cells:
        raise CalibrationError(f"unknown cell {cell_id!r}")
    free = _check_output_destination(out_dir)
    selected = cells[cell_id]
    return {
        "schema": "load-calibration-preflight/v1",
        "diagnostic": DIAGNOSTIC_LABEL,
        "formal": False,
        "full_replay": False,
        "contract_sha256": hashlib.sha256(raw).hexdigest(),
        "cell": cell_id,
        "arms": selected["cell"]["arms"],
        "resolved_config_sha256": selected["resolved"]["sha256"],
        "max_packets": selected["resolved"]["config"]["execution"]["max_packets"],
        "duration_s": selected["resolved"]["config"]["scenario"]["duration_s"],
        "deadline_s": contract["deadline_s"],
        "output_path": str(out_dir),
        "free_bytes_at_preflight": free,
        "trace_compiled": False,
        "kernel_calls": 0,
    }


def run_calibration(contract_path: Path, cell_id: str, out_dir: Path,
                    *, do_preflight=False, repo_root: Path | None = None) -> dict:
    repo_root = Path(repo_root or Path(__file__).resolve().parents[2])
    contract_path = Path(contract_path)
    out_dir = Path(out_dir).absolute()
    if do_preflight:
        return preflight(contract_path, cell_id, out_dir, repo_root)
    raw_contract, contract, base_resolved, cells = _read_contract(
        contract_path, repo_root)
    if cell_id not in cells:
        raise CalibrationError(f"unknown cell {cell_id!r}")
    free_bytes = _check_output_destination(out_dir)
    try:
        out_dir.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise CalibrationError(f"output destination already exists: {out_dir}") from exc

    selected = cells[cell_id]
    cell = selected["cell"]
    resolved = selected["resolved"]
    budget = _OutputBudget()
    arms = []
    error = None
    wall_started = time.monotonic()
    source = None
    trace_metadata = None
    binding = _input_bindings(resolved, repo_root)
    summary = {
        "schema": "load-calibration-result/v1",
        "diagnostic": DIAGNOSTIC_LABEL,
        "formal": False,
        "full_replay": False,
        "claimable": False,
        "contract_sha256": hashlib.sha256(raw_contract).hexdigest(),
        "cell": cell_id,
        "declared_arms": list(cell["arms"]),
        "deadline_s": contract["deadline_s"],
        "max_packets": contract["max_packets"],
        "max_duration_s": contract["max_duration_s"],
        "minimum_free_bytes": MIN_FREE_BYTES,
        "free_bytes_at_start": free_bytes,
        "maximum_output_bytes": MAX_OUTPUT_BYTES,
        "base_profile": contract["base_profile"],
        "logical_config_sha256": resolved["sha256"],
        "input_bindings": binding,
        "arms": arms,
        "kernel_call_count": 0,
        "error": None,
    }
    try:
        _write_bytes_artifact(out_dir / "contract.json", raw_contract, budget)
        frozen_config = {
            "resolved_config": resolved["config"],
            "canonical_json": resolved["canonical_json"],
            "sha256": resolved["sha256"],
            "cell_overrides": cell["overrides"],
            "design_overrides": selected["design_overrides"],
            "input_bindings": binding,
        }
        _write_json_artifact(out_dir / "resolved-config.json", frozen_config, budget)

        # design() is intentionally called only after the contract cell and
        # effective config have passed every boundary check above.
        design_resolved, rows, geometry, source = t1_tasks.design(
            config_path=str(repo_root / contract["base_profile"]),
            overrides=selected["design_overrides"])
        if design_resolved["sha256"] != resolved["sha256"]:
            raise CalibrationError("design() resolved config differs from preflight config")
        if len(rows) > contract["max_packets"]:
            raise CalibrationError(
                f"compiled trace has {len(rows)} packets above contract max_packets")
        trace_metadata = _write_trace(out_dir / "trace.jsonl.gz", rows, budget)
        trace_metadata.update({
            "source_trace_sha256": source.get("trace_sha256"),
            "source_rows_digest": source.get("rows_digest"),
            "source_rows": source.get("rows"),
            "native_population": source.get("native_population"),
        })
        _write_json_artifact(out_dir / "trace-manifest.json", trace_metadata, budget)

        for arm_name in cell["arms"]:
            arm_started = time.monotonic()
            arm_dir = out_dir / "arms" / arm_name
            arm_dir.parent.mkdir(mode=0o700, exist_ok=True)
            arm_dir.mkdir(mode=0o700)
            arm_config = copy.deepcopy(resolved["config"])
            arm_config["time_alignment"]["enabled"] = True
            arm_config["time_alignment"]["arm"] = arm_name
            arm_resolved = config_mod.resolve_config(arm_config)
            _validate_effective_config(arm_resolved, contract, cell_id)
            _write_json_artifact(arm_dir / "config.json", {
                "resolved_config": arm_resolved["config"],
                "canonical_json": arm_resolved["canonical_json"],
                "sha256": arm_resolved["sha256"],
                "arm": arm_name,
                "input_bindings": binding,
            }, budget)
            arm_summary = {
                "arm": arm_name,
                "config_sha256": arm_resolved["sha256"],
                "config_path": f"arms/{arm_name}/config.json",
                "kernel_called": False,
                "timeline": None,
                "decisions": None,
                "result_path": None,
                "natural_end": None,
                "stop_time_s": None,
                "error": None,
            }
            arms.append(arm_summary)
            timeline_sink = _JsonlSink(arm_dir / "timeline.jsonl.gz", budget,
                                       "timeline")
            decision_sink = _JsonlSink(arm_dir / "decisions.jsonl.gz", budget,
                                       "decision")
            result = None
            sink_error = None
            try:
                arm_summary["kernel_called"] = True
                summary["kernel_call_count"] += 1
                result = kernel.run_simulation(
                    arm_resolved, rows, geometry=geometry,
                    decision_sink=decision_sink, timeline_sink=timeline_sink,
                    control_audit=True)
            except Exception as exc:
                sink_error = exc
            finally:
                for sink in (timeline_sink, decision_sink):
                    try:
                        sink.close()
                    except Exception as exc:
                        if sink_error is None:
                            sink_error = exc
            arm_summary["timeline"] = timeline_sink.summary()
            arm_summary["decisions"] = decision_sink.summary()
            if sink_error is not None:
                arm_summary["error"] = _summary_error(sink_error)
                error = sink_error
                break

            result_path = arm_dir / "result.json.gz"
            try:
                _write_gzip_json(result_path, result, budget)
            except Exception as exc:
                arm_summary["result_path"] = f"arms/{arm_name}/result.json.gz"
                arm_summary["error"] = _summary_error(exc)
                error = exc
                break
            arm_summary["result_path"] = f"arms/{arm_name}/result.json.gz"
            arm_summary["natural_end"] = result.get("natural_end")
            arm_summary["stop_time_s"] = result.get("stop_time_s")
            arm_summary["kernel_error"] = result.get("error")
            arm_summary["queue_area_bits_s"] = result.get("queue_area_bits_s")
            arm_summary["access"] = result.get("access")
            arm_summary["mechanisms"] = result.get("mechanisms")
            arm_summary["events_processed"] = result.get("events_processed")
            arm_summary["execution_wall_s"] = time.monotonic() - arm_started
            arm_summary["d4"] = d4_outcome(result, rows, contract["deadline_s"])
            if result.get("error") is not None or result.get("natural_end") is not True:
                error = CalibrationError(
                    f"arm {arm_name} did not end naturally: "
                    f"{result.get('error') or 'natural_end=false'}")
                arm_summary["error"] = _summary_error(error)
                break
    except Exception as exc:
        error = exc

    summary["execution_wall_s"] = time.monotonic() - wall_started
    summary["trace"] = trace_metadata
    summary["source"] = source
    summary["status"] = "DIAGNOSTIC_COMPLETE" if error is None else "FAILED_CLOSED"
    summary["error"] = None if error is None else _summary_error(error)
    summary["artifact_files"] = _artifact_manifest(out_dir)
    _atomic_summary(out_dir, summary)
    return summary


def _parser():
    parser = argparse.ArgumentParser(
        description=("Run one bounded load-calibration diagnostic cell. "
                     "Output is not formal and is not a full replay."))
    parser.add_argument("--contract", required=True)
    parser.add_argument("--cell", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--preflight", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = run_calibration(
            Path(args.contract), args.cell, Path(args.out_dir),
            do_preflight=args.preflight)
    except CalibrationError as exc:
        print(json.dumps({"status": "REFUSED", "diagnostic": DIAGNOSTIC_LABEL,
                          "error": _summary_error(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if args.preflight:
        return 0
    return 0 if summary["status"] == "DIAGNOSTIC_COMPLETE" else 3


if __name__ == "__main__":
    raise SystemExit(main())
