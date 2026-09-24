#!/usr/bin/env python3
"""Fail-closed formal remote runner for the canonical isolated workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from deployment_guard import CANONICAL_WORKSPACE, verify_receipt


CANONICAL_CODE = CANONICAL_WORKSPACE / "CODE"
CANONICAL_RESULTS = CANONICAL_CODE / "Results"
CANONICAL_RUNTIME = CANONICAL_WORKSPACE / ".remote_runtime"
CANONICAL_STATUS = CANONICAL_RUNTIME / "current_status.json"
CANONICAL_LOGS = CANONICAL_RESULTS / "_overnight_logs"
CANONICAL_EXPERIMENTS = CANONICAL_WORKSPACE / "EXPERIMENTS"
CPU_LIST_LIMIT = 64
V2_GOVERNANCE_SCHEMA = "leo-sim-governance-receipt/v2"
V2_WITNESS_FIELDS = (
    "receipt_schema", "resolved_config_sha256", "trace_manifest_schema",
    "trace_identity_contract", "trace_manifest_sha256",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing to replace symbolic status path: {path}")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def launch_status_path(nonce: str) -> Path:
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ValueError("invalid launch nonce")
    launches = CANONICAL_RUNTIME / "launches"
    if launches.is_symlink():
        raise ValueError("launch receipt directory may not be symbolic")
    launches.mkdir(parents=True, exist_ok=True)
    if launches.resolve(strict=True) != launches:
        raise ValueError("launch receipt directory resolves outside its lexical path")
    return launches / f"{nonce}.json"


def persist_status(args: argparse.Namespace, payload: dict[str, Any], *, force_current: bool = False) -> None:
    attempt_path = launch_status_path(args.launch_nonce)
    payload["attempt_status_file"] = str(attempt_path)
    write_json(attempt_path, payload)
    current = Path(args.status_file)
    update_current = force_current or not current.is_file()
    if not update_current and not current.is_symlink():
        try:
            update_current = json.loads(current.read_text(encoding="utf-8")).get("launch_nonce") == args.launch_nonce
        except Exception:
            update_current = False
    if update_current:
        write_json(current, payload)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def v2_governance_errors(receipt: dict[str, Any], ledgers: dict[str, Any],
                         acceptance: dict[str, Any],
                         verification_errors: list[str]) -> list[str]:
    """Add formal effectiveness gates to local receipt verification."""
    errors = list(verification_errors)
    mechanisms = receipt.get("mechanisms", {})
    requested = mechanisms.get("requested", {}) if isinstance(mechanisms, dict) else {}
    effective = mechanisms.get("effective", {}) if isinstance(mechanisms, dict) else {}
    requirements = (
        (bool(requested.get("control_enabled")), "control_plane"),
        (bool(requested.get("ge_enabled")), "ge"),
        (requested.get("association") == "mbb", "mbb"),
    )
    for required, key in requirements:
        if required and effective.get(key) is not True:
            errors.append(
                f"requested mechanism was not effective in the send path: {key}")
    delivered = int(receipt.get("fate_counts", {}).get("DELIVERED", 0))
    if delivered < acceptance.get("min_delivered_packets", 0):
        errors.append("delivered packet count is below the authorized minimum")
    deliveries = ledgers.get("deliveries", {})
    multisat = sum(
        1 for row in deliveries.values()
        if isinstance(row, dict) and isinstance(row.get("path"), list)
        and len(row["path"]) >= 2)
    if multisat < acceptance.get("min_multisat_deliveries", 0):
        errors.append("multi-satellite delivery count is below the authorized minimum")
    if acceptance.get("require_data_isl") and multisat == 0:
        # recomputed criterion: a delivery whose path spans >=2 satellites
        # proves real data ISL service; occupied.isl_s is FIELD_AUTHORITY
        # "diagnostic" (self-reported, never recomputed) and must not gate
        # formal authorization
        errors.append("authorized smoke requires actual data ISL service")
    if acceptance.get("require_control_delivery") \
            and not (receipt.get("control", {}).get("counters", {}).get("arrived", 0) > 0):
        errors.append("authorized smoke requires at least one arrived control packet")
    return errors


def build_v2_governance_receipt(
        *, receipt: dict[str, Any], ledgers: dict[str, Any],
        verification_errors: list[str], acceptance: dict[str, Any],
        run_id: str, launch_nonce: str, authorization_sha256: str,
        deployment: dict[str, Any], deployment_receipt_sha256: str,
        execution_chain_sha256: str, receipt_path: Path,
        resolved_config_path: Path, manifest_path: Path) -> dict[str, Any]:
    """Build the V2 witness and the values copied to external launch status.

    The result directory is only an internally self-consistent artifact.  The
    launch-scoped status file is the separate VM witness that carries these
    bindings outside that directory.
    """
    if receipt.get("schema") != "leo-sim-receipt/v5":
        raise ValueError("leo_sim_v2 formal runs must produce receipt/v5")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("manifest.json must contain an object")
    governed = {
        "schema": V2_GOVERNANCE_SCHEMA,
        "research_eligible": not verification_errors,
        "run_id": run_id,
        "launch_nonce": launch_nonce,
        "authorization_sha256": authorization_sha256,
        "source_git_commit": deployment["source_git_commit"],
        "source_tree_sha256": deployment["source_tree_sha256"],
        "deployment_receipt_sha256": deployment_receipt_sha256,
        "execution_chain_sha256": execution_chain_sha256,
        "acceptance": acceptance,
        "run_receipt_sha256": file_sha256(receipt_path),
        "natural_end": receipt.get("natural_end"),
        "conservation_ok": receipt.get("conservation_ok"),
        "verification_errors": verification_errors,
        # These are the external witness contract.  In particular, the
        # resolved-config hash is the raw file hash, not only its canonical
        # config identity, so a byte-level rewrite is visible.
        "receipt_schema": receipt["schema"],
        "resolved_config_sha256": file_sha256(resolved_config_path),
        "trace_manifest_schema": manifest.get("schema"),
        "trace_identity_contract": receipt.get("trace_identity_contract"),
        "trace_manifest_sha256": file_sha256(manifest_path),
    }
    governed["payload_sha256"] = canonical_sha(governed)
    return governed


def parse_cpu_list(value: str) -> list[int]:
    """Parse a bounded Linux CPU-list such as ``0-3,8`` without shelling out."""
    cpus: list[int] = []
    if not value:
        return cpus
    for token in value.split(","):
        token = token.strip()
        if not token:
            raise ValueError("CPU list contains an empty component")
        if "-" in token:
            parts = token.split("-")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError("CPU list range is invalid")
            start, end = (int(part) for part in parts)
            if start > end:
                raise ValueError("CPU list range is descending")
            cpus.extend(range(start, end + 1))
        elif token.isdigit():
            cpus.append(int(token))
        else:
            raise ValueError("CPU list component is invalid")
    if not cpus or len(cpus) > CPU_LIST_LIMIT or len(cpus) != len(set(cpus)):
        raise ValueError("CPU list must contain 1-64 unique CPUs")
    return sorted(cpus)


def validate_cpu_affinity(value: str) -> list[int]:
    cpus = parse_cpu_list(value)
    if not cpus:
        return cpus
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        raise ValueError("requested CPU affinity is unsupported on this host")
    allowed = set(os.sched_getaffinity(0))
    if not set(cpus).issubset(allowed):
        raise ValueError("requested CPU affinity is outside the launcher affinity")
    return cpus


def _linux_cpu_counters(cpus: list[int]) -> tuple[int, int]:
    wanted = {f"cpu{cpu}" for cpu in cpus}
    total = idle = 0
    with Path("/proc/stat").open("r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.split()
            if not fields or fields[0] not in wanted:
                continue
            values = [int(value) for value in fields[1:]]
            if len(values) < 5:
                raise ValueError("/proc/stat CPU row is incomplete")
            total += sum(values)
            idle += values[3] + values[4]
    if total <= 0:
        raise ValueError("could not read preregistered CPUs from /proc/stat")
    return total, idle


def sample_cpu_busy_fraction(cpus: list[int], interval_seconds: float = 0.25) -> float:
    if not cpus:
        return 0.0
    before_total, before_idle = _linux_cpu_counters(cpus)
    time.sleep(interval_seconds)
    after_total, after_idle = _linux_cpu_counters(cpus)
    total_delta = after_total - before_total
    idle_delta = after_idle - before_idle
    if total_delta <= 0 or idle_delta < 0:
        raise ValueError("invalid /proc/stat delta during CPU preflight")
    return max(0.0, min(1.0, 1.0 - idle_delta / total_delta))


def config_identity(path: Path) -> tuple[str, str]:
    if path.name.endswith(".leo-sim.yaml"):
        sys.path.insert(0, str(CANONICAL_WORKSPACE))
        from CODE.leo_sim.config import load_config_file

        resolved = load_config_file(str(path))
        # V2 run id is authorization-bound and supplied separately; the config
        # itself intentionally contains no operational provenance overlay.
        return "", resolved["sha256"]
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("formal config must contain a JSON object")
    run_id = str(config.get("provenance", {}).get("run_id", ""))
    if not run_id:
        raise ValueError("formal config lacks provenance.run_id")
    return run_id, canonical_sha(config)


def require_within(path: Path, root: Path, label: str, *, must_exist: bool = True) -> Path:
    if path.is_symlink():
        raise ValueError(f"{label} may not be a symbolic link: {path}")
    resolved_root = root.resolve()
    resolved = path.resolve(strict=must_exist)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"{label} is outside {resolved_root}: {resolved}") from exc
    return resolved


def validate_formal_paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path, Path]:
    workdir = Path(args.workdir).resolve(strict=True)
    if workdir != CANONICAL_CODE:
        raise ValueError(f"formal workdir must be {CANONICAL_CODE}, got {workdir}")
    status = Path(args.status_file).resolve(strict=False)
    if status != CANONICAL_STATUS:
        raise ValueError(f"formal status file must be {CANONICAL_STATUS}, got {status}")
    log = require_within(Path(args.log_file), CANONICAL_LOGS, "log file", must_exist=False)
    if log.parent != CANONICAL_LOGS.resolve() or log.suffix != ".log":
        raise ValueError("formal log file must be a direct .log child of the canonical log directory")
    config = require_within(Path(args.config), CANONICAL_EXPERIMENTS, "config")
    authorization = require_within(Path(args.authorization), CANONICAL_EXPERIMENTS, "authorization")
    runtime_kind = getattr(args, "runtime_kind", "legacy_gateway")
    if runtime_kind == "leo_sim_v2":
        if not config.name.endswith(".leo-sim.yaml"):
            raise ValueError("formal V2 config must be a compiled *.leo-sim.yaml")
    elif not config.name.endswith(".config.json"):
        raise ValueError("formal legacy config must be a compiled *.config.json")
    if authorization.name != "authorization.json":
        raise ValueError("formal authorization must be named authorization.json")
    return workdir, status, log, config, authorization


def formal_command(args: argparse.Namespace, workdir: Path, config: Path, authorization: Path) -> list[str]:
    if getattr(args, "runtime_kind", "legacy_gateway") == "leo_sim_v2":
        out_dir = CANONICAL_RESULTS / args.expected_run_id
        return [
            sys.executable, "-m", "CODE.leo_sim", "run",
            "--config", str(config),
            "--out", str(out_dir),
            "--authorization", str(authorization),
            "--launch-nonce", args.launch_nonce,
            "--expect-run-id", args.expected_run_id,
        ]
    runtime_input = CANONICAL_RESULTS / "_runtime_inputs" / f"inputRL_{args.launch_nonce}.csv"
    command = [
        sys.executable, str(workdir / "run.py"), "--config", str(config),
        "--authorization", str(authorization), "--launch-nonce", args.launch_nonce,
        "--input-rl", str(runtime_input),
    ]
    if args.no_monitor:
        command.append("--no-monitor")
    if args.bundle:
        command.append("--bundle")
    if args.bundle_stages:
        command.extend(("--bundle-stages", args.bundle_stages))
    return command


def formal_child_cwd(args: argparse.Namespace, workdir: Path) -> Path:
    """Return the runtime cwd whose import/data semantics were authorized."""
    if getattr(args, "runtime_kind", "legacy_gateway") == "leo_sim_v2":
        return CANONICAL_WORKSPACE
    return workdir


def base_launch_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "schema": "leo-remote-launch-status/v2",
        "status": "launching",
        "execution_class": "FORMAL_EXPERIMENT",
        "runtime_kind": getattr(args, "runtime_kind", "legacy_gateway"),
        "launch_nonce": args.launch_nonce,
        "session_name": args.session_name,
        "run_id": args.expected_run_id,
        "config_sha256": args.expected_config_sha256,
        "authorization_sha256": args.expected_authorization_sha256,
        "host": socket.gethostname(),
        "launched_at": now_iso(),
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
        "failure_stage": None,
        "error": None,
        "workdir": str(args.workdir),
        "config": str(args.config),
        "authorization": str(args.authorization),
        "log_file": str(args.log_file),
        "status_file": str(args.status_file),
        "last_results_dir": "",
        "run_attempt_id": "",
        "governance_witness": None,
        "governance_receipt_sha256": None,
        "verified_predecessors": [],
        "requested_cpu_affinity": parse_cpu_list(getattr(args, "cpu_list", "")),
        "cpu_preflight_busy_fraction": None,
    }


def validate_expected_identity(args: argparse.Namespace, config: Path, authorization: Path) -> None:
    if not args.launch_nonce or len(args.launch_nonce) != 32 or any(char not in "0123456789abcdef" for char in args.launch_nonce):
        raise ValueError("launch nonce must be exactly 32 lowercase hex characters")
    run_id, config_sha = config_identity(config)
    runtime_kind = getattr(args, "runtime_kind", "legacy_gateway")
    if ((runtime_kind != "leo_sim_v2" and run_id != args.expected_run_id)
            or config_sha != args.expected_config_sha256):
        raise ValueError("remote config identity differs from the launcher-bound run id or canonical hash")
    if file_sha256(authorization) != args.expected_authorization_sha256:
        raise ValueError("remote authorization bytes differ from the launcher-bound hash")


def verify_v2_serial_predecessors(
        args: argparse.Namespace, config: Path,
        authorization: Path, deployment: dict[str, Any]) -> list[str]:
    """Enforce the deployed serial gate at the remote trust boundary.

    Local launch checks are only an early diagnostic.  The deployed runner
    repeats this check before preparation and again immediately before the
    child starts, using the VM's nonce-named launch receipts as the external
    witnesses for all predecessor cells.
    """
    if getattr(args, "runtime_kind", "legacy_gateway") != "leo_sim_v2":
        return []
    experiment_dir = authorization.parent
    sys.path.insert(0, str(CANONICAL_WORKSPACE))
    from CODE.experiment_platform.v2_serial_gate import verify_predecessors

    return verify_predecessors(
        CANONICAL_WORKSPACE, experiment_dir, authorization,
        args.expected_run_id, results_root=CANONICAL_RESULTS,
        external_witness_root=CANONICAL_RUNTIME / "launches",
        external_witness_by_nonce=True,
        deployed_source_commit=deployment["source_git_commit"],
        expected_deployment=deployment)


def verify_v2_run_authorization(
        args: argparse.Namespace, config: Path, authorization: Path) -> None:
    """Recompute V2 authorization in every remote process, not only prepare."""
    if getattr(args, "runtime_kind", "legacy_gateway") != "leo_sim_v2":
        return
    sys.path.insert(0, str(CANONICAL_WORKSPACE))
    from CODE.experiment_platform.authorize_experiment import (
        verify_authorization_for_leo_sim_v2_config,
    )
    verify_authorization_for_leo_sim_v2_config(
        CANONICAL_WORKSPACE, authorization, config, args.expected_run_id)


def prepare_launch(args: argparse.Namespace) -> int:
    status_path = Path(args.status_file)
    if status_path.resolve(strict=False) != CANONICAL_STATUS:
        raise ValueError(f"formal status file must be {CANONICAL_STATUS}")
    payload = base_launch_payload(args)
    # Atomically invalidate any older terminal receipt before a validation that
    # could fail. A unique nonce also prevents an older payload from satisfying
    # this launch's status query.
    persist_status(args, payload, force_current=True)
    try:
        workdir, _status, log, config, authorization = validate_formal_paths(args)
        deployment = verify_receipt(Path(args.deployment_receipt))
        validate_expected_identity(args, config, authorization)
        requested_cpu_affinity = validate_cpu_affinity(getattr(args, "cpu_list", ""))
        cpu_preflight_busy_fraction = sample_cpu_busy_fraction(requested_cpu_affinity)
        sys.path.insert(0, str(CANONICAL_WORKSPACE))
        verified_predecessors: list[str] = []
        if getattr(args, "runtime_kind", "legacy_gateway") == "leo_sim_v2":
            verify_v2_run_authorization(args, config, authorization)
            verified_predecessors = verify_v2_serial_predecessors(
                args, config, authorization, deployment)
        else:
            from CODE.experiment_platform.authorize_experiment import verify_authorization_for_config

            config_payload = json.loads(config.read_text(encoding="utf-8"))
            if not isinstance(config_payload, dict):
                raise ValueError("formal config must contain a JSON object")
            verify_authorization_for_config(
                CANONICAL_WORKSPACE, authorization, config_payload)
        payload.update({
            "status": "prepared",
            "workdir": str(workdir),
            "log_file": str(log),
            "config": str(config),
            "authorization": str(authorization),
            "deployment_receipt": str(Path(args.deployment_receipt).resolve()),
            "source_git_commit": deployment["source_git_commit"],
            "source_git_branch": deployment["source_git_branch"],
            "source_git_dirty": deployment["source_git_dirty"],
            "source_tree_sha256": deployment["source_tree_sha256"],
            "deployment_receipt_sha256": deployment["receipt_sha256"],
            "requested_cpu_affinity": requested_cpu_affinity,
            "cpu_preflight_busy_fraction": cpu_preflight_busy_fraction,
            "verified_predecessors": verified_predecessors,
        })
    except Exception as exc:
        payload.update({
            "status": "failed",
            "finished_at": now_iso(),
            "exit_code": 2,
            "failure_stage": "prepare",
            "error": f"{type(exc).__name__}: {exc}",
        })
        persist_status(args, payload)
        print(payload["error"], file=sys.stderr)
        return 2
    persist_status(args, payload)
    return 0


def fail_launch(args: argparse.Namespace) -> int:
    status_path = Path(args.status_file)
    try:
        payload = json.loads(launch_status_path(args.launch_nonce).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError("cannot mark launch failure without its prepared status") from exc
    if payload.get("launch_nonce") != args.launch_nonce or payload.get("session_name") != args.session_name:
        raise ValueError("refusing to modify a status receipt for another launch")
    payload.update({
        "status": "failed",
        "finished_at": now_iso(),
        "exit_code": 2,
        "failure_stage": "tmux_launch",
        "error": "tmux session creation failed",
    })
    persist_status(args, payload)
    return 2


def result_receipts_dir(*, create: bool = False) -> Path:
    if CANONICAL_RESULTS.is_symlink():
        raise ValueError("canonical Results directory may not be symbolic")
    receipts = CANONICAL_RESULTS / "_run_receipts"
    if receipts.is_symlink():
        raise ValueError("launch-scoped result receipt directory may not be symbolic")
    if create:
        receipts.mkdir(parents=True, exist_ok=True)
    if receipts.exists():
        if not receipts.is_dir() or receipts.resolve(strict=True) != receipts.absolute():
            raise ValueError("launch-scoped result receipt directory resolves outside its lexical path")
    return receipts


def result_pointer_path(launch_nonce: str, *, create_parent: bool = False) -> Path:
    if len(launch_nonce) != 32 or any(char not in "0123456789abcdef" for char in launch_nonce):
        raise ValueError("invalid launch nonce")
    return result_receipts_dir(create=create_parent) / f"{launch_nonce}.txt"


def result_dir_from_pointer(started_at: float, launch_nonce: str) -> str:
    pointer = result_pointer_path(launch_nonce)
    if pointer.is_symlink() or not pointer.is_file():
        return ""
    try:
        if pointer.stat().st_mtime + 2 < started_at:
            return ""
        line = pointer.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        candidate = require_within(Path(line), CANONICAL_RESULTS, "last result")
    except (OSError, IndexError, ValueError):
        return ""
    if candidate.parent != CANONICAL_RESULTS.resolve() or candidate.name.startswith("_") or not candidate.is_dir():
        return ""
    return str(candidate)


def run_formal(args: argparse.Namespace) -> int:
    workdir, status_path, log_path, config, authorization = validate_formal_paths(args)
    deployment = verify_receipt(Path(args.deployment_receipt))
    validate_expected_identity(args, config, authorization)
    requested_cpu_affinity = validate_cpu_affinity(getattr(args, "cpu_list", ""))
    prepared = json.loads(launch_status_path(args.launch_nonce).read_text(encoding="utf-8"))
    expected_prepared = {
        "status": "prepared",
        "launch_nonce": args.launch_nonce,
        "session_name": args.session_name,
        "run_id": args.expected_run_id,
        "config_sha256": args.expected_config_sha256,
        "authorization_sha256": args.expected_authorization_sha256,
    }
    if any(prepared.get(key) != value for key, value in expected_prepared.items()):
        raise ValueError("prepared launch status does not match this formal job")
    verify_v2_run_authorization(args, config, authorization)
    verified_predecessors = verify_v2_serial_predecessors(
        args, config, authorization, deployment)
    command = formal_command(args, workdir, config, authorization)
    child_cwd = formal_child_cwd(args, workdir)
    pointer = result_pointer_path(args.launch_nonce, create_parent=True)
    if pointer.is_symlink():
        raise ValueError("launch-scoped result pointer may not be symbolic")
    pointer.unlink(missing_ok=True)
    started_at = datetime.now().timestamp()
    payload: dict[str, Any] = {
        **prepared,
        "status": "running",
        "started_at": now_iso(),
        "finished_at": None,
        "exit_code": None,
        "workdir": str(workdir),
        "command": shlex.join(command),
        "config": str(config),
        "authorization": str(authorization),
        "log_file": str(log_path),
        "status_file": str(status_path),
        "last_results_dir": "",
        "deployment_receipt": str(Path(args.deployment_receipt).resolve()),
        "source_git_commit": deployment["source_git_commit"],
        "source_git_branch": deployment["source_git_branch"],
        "source_git_dirty": deployment["source_git_dirty"],
        "source_tree_sha256": deployment["source_tree_sha256"],
        "deployment_receipt_sha256": deployment["receipt_sha256"],
        "verified_predecessors": verified_predecessors,
        "requested_cpu_affinity": requested_cpu_affinity,
    }
    persist_status(args, payload)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rc = 1
    with log_path.open("a", encoding="utf-8", buffering=1) as log_fh:
        log_fh.write(f"[remote_job] started_at={payload['started_at']}\n")
        log_fh.write("[remote_job] execution_class=FORMAL_EXPERIMENT\n")
        log_fh.write(f"[remote_job] source_tree_sha256={deployment['source_tree_sha256']}\n")
        log_fh.write(f"[remote_job] command={shlex.join(command)}\n")
        log_fh.flush()
        try:
            child_setup = None
            if requested_cpu_affinity:
                child_setup = lambda: os.sched_setaffinity(0, requested_cpu_affinity)
            proc = subprocess.Popen(
                command,
                cwd=str(child_cwd),
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                text=True,
                preexec_fn=child_setup,
                env={
                    **os.environ,
                    "LEO_CPU_PREFLIGHT_BUSY_FRACTION": str(prepared.get("cpu_preflight_busy_fraction", "")),
                },
            )
            rc = proc.wait()
        except Exception:
            traceback.print_exc(file=log_fh)
            rc = 1
        finally:
            runtime_input = CANONICAL_RESULTS / "_runtime_inputs" / f"inputRL_{args.launch_nonce}.csv"
            if getattr(args, "runtime_kind", "legacy_gateway") != "leo_sim_v2":
                runtime_input.unlink(missing_ok=True)
        payload["finished_at"] = now_iso()
        payload["exit_code"] = rc
        payload["status"] = "success" if rc == 0 else "failed"
        payload["last_results_dir"] = result_dir_from_pointer(started_at, args.launch_nonce)
        if rc == 0 and not payload["last_results_dir"]:
            payload["status"] = "failed"
            payload["exit_code"] = 2
            payload["failure_stage"] = "result_identity"
            payload["error"] = "child exited zero without a fresh exact result pointer"
            rc = 2
        if payload["last_results_dir"]:
            try:
                if getattr(args, "runtime_kind", "legacy_gateway") == "leo_sim_v2":
                    meta = json.loads(
                        (Path(payload["last_results_dir"]) / "formal_run.json").read_text(
                            encoding="utf-8"))
                    expected_result_identity = {
                        "run_id": args.expected_run_id,
                        "config_sha256": args.expected_config_sha256,
                        "launch_nonce": args.launch_nonce,
                        "authorization_sha256": args.expected_authorization_sha256,
                    }
                    payload["run_attempt_id"] = args.launch_nonce
                else:
                    meta = json.loads(
                        (Path(payload["last_results_dir"]) / "run_trace" / "run_meta.json").read_text(encoding="utf-8")
                    )
                    payload["run_attempt_id"] = str(meta.get("run_attempt_id", ""))
                    expected_result_identity = {
                        "requested_run_id": args.expected_run_id,
                        "config_canonical_sha256": args.expected_config_sha256,
                        "launch_nonce": args.launch_nonce,
                        "authorization_sha256": args.expected_authorization_sha256,
                    }
                if any(meta.get(key) != value for key, value in expected_result_identity.items()):
                    payload["status"] = "failed"
                    payload["exit_code"] = 2
                    payload["failure_stage"] = "result_identity"
                    payload["error"] = "result run/config/launch/authorization identity does not match prepared launch"
                    rc = 2
                elif len(payload["run_attempt_id"]) != 32 or any(
                    char not in "0123456789abcdef" for char in payload["run_attempt_id"]
                ):
                    payload["status"] = "failed"
                    payload["exit_code"] = 2
                    payload["failure_stage"] = "result_identity"
                    payload["error"] = "result lacks a valid run attempt id"
                    rc = 2
                elif getattr(args, "runtime_kind", "legacy_gateway") == "leo_sim_v2":
                    receipt_path = Path(payload["last_results_dir"]) / "receipt.json"
                    sys.path.insert(0, str(CANONICAL_WORKSPACE))
                    from CODE.leo_sim.receipt import verify_receipt_dir

                    receipt_errors = verify_receipt_dir(payload["last_results_dir"])
                    receipt_payload = json.loads(receipt_path.read_text(encoding="utf-8"))
                    ledgers_payload = json.loads(
                        (Path(payload["last_results_dir"]) / "ledgers.json").read_text(
                            encoding="utf-8"))
                    authorization_payload = json.loads(
                        authorization.read_text(encoding="utf-8"))
                    authorized_row = next(
                        row for row in authorization_payload["authorized_runs"]
                        if row["run_id"] == args.expected_run_id)
                    receipt_errors = v2_governance_errors(
                        receipt_payload, ledgers_payload,
                        authorized_row["acceptance"], receipt_errors)
                    governed = build_v2_governance_receipt(
                        receipt=receipt_payload,
                        ledgers=ledgers_payload,
                        verification_errors=receipt_errors,
                        acceptance=authorized_row["acceptance"],
                        run_id=args.expected_run_id,
                        launch_nonce=args.launch_nonce,
                        authorization_sha256=args.expected_authorization_sha256,
                        deployment=deployment,
                        deployment_receipt_sha256=deployment["receipt_sha256"],
                        execution_chain_sha256=authorized_row[
                            "execution_chain_sha256"],
                        receipt_path=receipt_path,
                        resolved_config_path=Path(payload["last_results_dir"]) / "resolved_config.json",
                        manifest_path=Path(payload["last_results_dir"]) / "manifest.json",
                    )
                    if receipt_errors:
                        payload["status"] = "failed"
                        payload["exit_code"] = 2
                        payload["failure_stage"] = "receipt_verification"
                        payload["error"] = "V2 receipt verification failed"
                        rc = 2
                    governed_path = Path(payload["last_results_dir"]) / "governance_receipt.json"
                    write_json(governed_path, governed)
                    payload["governance_receipt"] = str(governed_path)
                    payload["governance_receipt_sha256"] = file_sha256(governed_path)
                    payload["governance_witness"] = {
                        key: governed[key] for key in V2_WITNESS_FIELDS
                    }
                    payload["research_eligible"] = governed["research_eligible"]
            except Exception as exc:
                payload["status"] = "failed"
                payload["exit_code"] = 2
                payload["failure_stage"] = "result_identity"
                payload["error"] = f"could not re-read result identity: {exc}"
                rc = 2
        log_fh.write(f"[remote_job] finished_at={payload['finished_at']}\n")
        log_fh.write(f"[remote_job] exit_code={rc}\n")
        log_fh.flush()
    persist_status(args, payload)
    return rc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Canonical formal experiment runner")
    parser.add_argument("action", choices=("prepare", "run", "fail"))
    parser.add_argument("--session-name", required=True)
    parser.add_argument("--status-file", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--deployment-receipt", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--authorization", required=True)
    parser.add_argument("--launch-nonce", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-config-sha256", required=True)
    parser.add_argument("--expected-authorization-sha256", required=True)
    parser.add_argument("--runtime-kind", choices=("legacy_gateway", "leo_sim_v2"),
                        default="legacy_gateway")
    parser.add_argument("--no-monitor", action="store_true")
    parser.add_argument("--bundle", action="store_true")
    parser.add_argument("--bundle-stages", default="")
    parser.add_argument("--cpu-list", default="")
    args = parser.parse_args()
    if args.bundle_stages:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_,-")
        if any(char not in allowed for char in args.bundle_stages):
            parser.error("--bundle-stages contains unsafe characters")
        args.bundle = True
    return args


def main() -> int:
    args = parse_args()
    try:
        if args.action == "prepare":
            return prepare_launch(args)
        if args.action == "fail":
            return fail_launch(args)
        return run_formal(args)
    except Exception as exc:
        if args.action == "run":
            try:
                payload = json.loads(launch_status_path(args.launch_nonce).read_text(encoding="utf-8"))
                if payload.get("launch_nonce") == args.launch_nonce and payload.get("session_name") == args.session_name:
                    payload.update({
                        "status": "failed",
                        "finished_at": now_iso(),
                        "exit_code": 2,
                        "failure_stage": "run_preflight",
                        "error": f"{type(exc).__name__}: {exc}",
                        "last_results_dir": "",
                    })
                    persist_status(args, payload)
            except Exception:
                pass
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
