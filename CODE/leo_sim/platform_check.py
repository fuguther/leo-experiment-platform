"""One outcome-oriented end-to-end check for the LEO V2 platform.

This is the user-facing closure path, not another review layer.  It runs the
real mechanism scenarios, the retained Gateway/direct same-trace comparison,
and a TensorFlow DDQN train/save/load/eval chain.  The first failed stage stops
the run and is recorded in ``platform-summary.json`` for diagnosis.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

from . import acceptance, comparison, config, kernel, receipt, trace


DDQN_PROFILE = Path(__file__).resolve().parent / "profiles" / "acceptance" / "ddqn.yaml"
COMPARISON_PROFILE = Path(__file__).resolve().parent / "profiles" / "comparison.yaml"
POPULATION_PROFILE = Path(__file__).resolve().parent / "profiles" / "population_gravity.yaml"


class PlatformCheckError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_summary(root: Path, summary: dict) -> None:
    (root / "platform-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dependency_snapshot() -> dict:
    deps = {"python": platform.python_version()}
    for package in ("numpy", "simpy", "pyyaml", "tensorflow"):
        try:
            deps[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            deps[package] = None
    return deps


def _compile_trace(resolved: dict, trace_dir: Path) -> tuple[dict, bytes, list[dict]]:
    manifest = trace.compile_trace(resolved, str(trace_dir))
    trace_bytes = (trace_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (trace_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(trace_dir / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"],
    )
    return manifest, trace_bytes, rows


def _run_learning_arm(name: str, resolved: dict, rows: list[dict],
                      trace_bytes: bytes, manifest: dict, out_dir: Path) -> dict:
    result = kernel.run_simulation(
        resolved, rows, learning_out_dir=out_dir / "ddqn")
    run_receipt = receipt.write_run(
        str(out_dir), resolved, trace_bytes, manifest, result, rows)
    verify_errors = receipt.verify_receipt_dir(str(out_dir))
    learning_ledger = result.get("learning") or {}
    expected_mode = resolved["config"]["learning"]["mode"]
    checks = {
        "natural_end": run_receipt["natural_end"] is True,
        "data_conservation": run_receipt["conservation_ok"] is True,
        "receipt_verified": not verify_errors,
        "delivered_data": run_receipt["fate_counts"]["DELIVERED"] > 0,
        "learning_effective": run_receipt["mechanisms"]["effective"]["learning"] is True,
        "mode_exact": learning_ledger.get("mode") == expected_mode,
        "model_save_load_verified": learning_ledger.get("checkpoint_verified") is True,
    }
    if expected_mode == "train":
        checks["gradient_updates_observed"] = learning_ledger.get("train_steps", 0) > 0
    else:
        checks["model_decisions_observed"] = learning_ledger.get("decisions", 0) > 0
        checks["no_eval_training"] = learning_ledger.get("train_steps") == 0
        checks["requested_checkpoint_loaded"] = (
            learning_ledger.get("loaded_checkpoint_sha256")
            == resolved["config"]["learning"]["checkpoint_sha256"])
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "name": name,
        "result_dir": str(out_dir),
        "trace_sha256": run_receipt["trace_sha256"],
        "fate_counts": run_receipt["fate_counts"],
        "learning": learning_ledger,
        "checks": checks,
        "receipt_errors": verify_errors,
    }


def _run_population(profile: str | Path, out_dir: Path) -> dict:
    resolved = config.load_config_file(str(profile))
    if resolved["config"]["demand"]["mode"] != "population_gravity":
        raise PlatformCheckError(
            "population platform profile must request population_gravity")
    manifest, trace_bytes, rows = _compile_trace(
        resolved, out_dir / "immutable_trace")
    result_dir = out_dir / "satellite_direct"
    result = kernel.run_simulation(resolved, rows)
    run_receipt = receipt.write_run(
        str(result_dir), resolved, trace_bytes, manifest, result, rows)
    verify_errors = receipt.verify_receipt_dir(str(result_dir))
    sources = {row["src_grid_id"] for row in rows}
    destinations = {row["dst_grid_id"] for row in rows}
    checks = {
        "population_proxy_declared": manifest.get("provenance") == "population_proxy",
        "population_not_overclaimed": manifest.get(
            "not_calibrated_user_demand") is True,
        "multiple_source_regions": len(sources) > 1,
        "multiple_destination_regions": len(destinations) > 1,
        "natural_end": run_receipt["natural_end"] is True,
        "data_conservation": run_receipt["conservation_ok"] is True,
        "delivered_data": run_receipt["fate_counts"]["DELIVERED"] > 0,
        "receipt_verified": not verify_errors,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "profile": str(profile),
        "result_dir": str(result_dir),
        "trace_sha256": run_receipt["trace_sha256"],
        "offered_packets": len(rows),
        "active_source_regions": len(sources),
        "active_destination_regions": len(destinations),
        "candidate_population_regions": manifest["population"][
            "candidate_regions"],
        "represented_population": manifest["population"]["total_population"],
        "fate_counts": run_receipt["fate_counts"],
        "checks": checks,
        "receipt_errors": verify_errors,
    }


def _run_ddqn_chain(profile: str | Path, out_dir: Path) -> dict:
    train_resolved = config.load_config_file(str(profile))
    train_cfg = train_resolved["config"]
    if train_cfg["learning"]["algorithm"] != "ddqn" \
            or train_cfg["learning"]["mode"] != "train":
        raise PlatformCheckError("DDQN platform profile must request ddqn train mode")

    manifest, trace_bytes, rows = _compile_trace(
        train_resolved, out_dir / "immutable_trace")
    train = _run_learning_arm(
        "ddqn_train", train_resolved, rows, trace_bytes, manifest,
        out_dir / "train")
    if train["status"] != "PASS":
        raise PlatformCheckError(
            "DDQN training did not satisfy the platform outcome checks: "
            + json.dumps(train["checks"], sort_keys=True))

    model_path = (out_dir / "train" / "ddqn" / "online.keras").resolve()
    model_sha = train["learning"]["checkpoint_sha256"]
    eval_cfg = copy.deepcopy(train_cfg)
    eval_cfg["scenario"]["name"] = f"{train_cfg['scenario']['name']}-eval"
    eval_cfg["learning"]["mode"] = "eval"
    eval_cfg["learning"]["checkpoint_path"] = str(model_path)
    eval_cfg["learning"]["checkpoint_sha256"] = model_sha
    eval_resolved = config.resolve_config(eval_cfg)
    eval_run = _run_learning_arm(
        "ddqn_eval", eval_resolved, rows, trace_bytes, manifest,
        out_dir / "eval")

    same_trace = train["trace_sha256"] == eval_run["trace_sha256"]
    checkpoint_bound = (
        eval_run["learning"].get("loaded_checkpoint_sha256") == model_sha)
    checks = {
        "train_passed": train["status"] == "PASS",
        "eval_passed": eval_run["status"] == "PASS",
        "same_immutable_trace": same_trace,
        "eval_loaded_trained_checkpoint": checkpoint_bound,
    }
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "trace_sha256": train["trace_sha256"],
        "trained_checkpoint": str(model_path),
        "trained_checkpoint_sha256": model_sha,
        "checks": checks,
        "train": train,
        "eval": eval_run,
    }


def run_platform_check(out_dir: str | Path,
                       comparison_config: str | Path = COMPARISON_PROFILE,
                       ddqn_config: str | Path = DDQN_PROFILE,
                       population_config: str | Path = POPULATION_PROFILE) -> dict:
    """Run every executable platform path and return one final outcome."""
    requested_root = Path(out_dir)
    if requested_root.is_symlink():
        raise PlatformCheckError(
            "platform check output may not be a symbolic link")
    root = requested_root.resolve()
    if root.exists() and (
            not root.is_dir() or any(root.iterdir())):
        raise PlatformCheckError(
            "platform check output must be a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "leo-sim-platform-check/v1",
        "status": "RUNNING",
        "started_at_utc": _utc_now(),
        "result_dir": str(root),
        "dependencies": _dependency_snapshot(),
        "evidence_scope": (
            "engineering execution evidence only; this check does not prove "
            "algorithm superiority or calibrated physical fidelity"),
        "stages": {},
    }
    _write_summary(root, summary)

    stage = "mechanisms"
    try:
        mechanism_result = acceptance.run_acceptance(root / stage)
        summary["stages"][stage] = mechanism_result
        _write_summary(root, summary)
        if mechanism_result["status"] != "PASS":
            raise PlatformCheckError("one or more mechanism outcomes failed")

        stage = "population_traffic"
        population_result = _run_population(
            population_config, root / stage)
        summary["stages"][stage] = population_result
        _write_summary(root, summary)
        if population_result["status"] != "PASS":
            raise PlatformCheckError("population traffic outcome failed")

        stage = "gateway_vs_direct"
        comparison_result = comparison.run_comparison(
            comparison_config, root / stage)
        summary["stages"][stage] = comparison_result
        _write_summary(root, summary)
        if comparison_result["status"] != "PASS":
            raise PlatformCheckError("Gateway/direct same-trace comparison failed")

        stage = "ddqn_train_eval"
        learning_result = _run_ddqn_chain(
            ddqn_config, root / stage)
        summary["stages"][stage] = learning_result
        if learning_result["status"] != "PASS":
            raise PlatformCheckError("DDQN train/eval outcome failed")
    except Exception as exc:
        summary["status"] = "FAIL"
        summary["failed_stage"] = stage
        summary["error"] = {
            "type": type(exc).__name__,
            "message": str(exc),
        }
        summary["finished_at_utc"] = _utc_now()
        _write_summary(root, summary)
        return summary

    summary["status"] = "PASS"
    summary["finished_at_utc"] = _utc_now()
    summary["checks"] = {
        "all_mechanisms_ran": True,
        "population_gravity_traffic_ran": True,
        "same_trace_gateway_and_direct_ran": True,
        "ddqn_train_save_load_eval_ran": True,
    }
    _write_summary(root, summary)
    return summary
