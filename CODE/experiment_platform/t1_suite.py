"""T1-COMPLETE P10: compile -> validate -> run -> resume -> report.

The suite is the single entry point that turns the frozen contract into an
executable, resumable diagnostic matrix.  It never re-implements an experiment:
each cell shells out to the existing driver (time_alignment_compare,
execution_compare, benchmark_decision) so the suite owns only scheduling,
budgets, identity and recovery.

Recovery rules
--------------
* Every cell records the input hashes it ran against (bundle identity, contract
  hash, driver source hash) inside the cell directory.
* resume only reuses a cell whose recorded identity matches the CURRENT one; a
  code or contract change invalidates the old output instead of silently
  attaching it to a new identity.
* Budgets are pre-declared; exceeding them marks BUDGET_EXCEEDED and keeps the
  cells that already finished.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from CODE.experiment_platform import artifact_identity, t1_stats

SCHEMA_BUNDLE = "t1-suite-bundle/v1"
SCHEMA_RUN = "t1-suite-run/v1"
SCHEMA_REPORT = "t1-suite-report/v1"

REPO_ROOT = artifact_identity.REPO_ROOT

DEFAULT_BUDGETS = {
    "cell_wall_s": 120.0,
    "max_cells": 120,
    "total_wall_s": 7200.0,
}

VALIDATION_SCHEMA = "t1-suite-validation/v1"


class SuiteError(RuntimeError):
    pass


class BudgetExceeded(SuiteError):
    pass


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _write_json(path, payload):
    path = Path(path)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                         suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _require_new_dir(path, label):
    path = Path(path)
    if path.exists():
        raise SuiteError(f"{label} destination exists: {path}")
    path.mkdir(parents=True)
    return path


# --------------------------------------------------------------- compile
def _cell(cell_id, group, driver, args, description, seed=None,
          predicate=None):
    return {"cell_id": cell_id, "group": group, "driver": driver,
            "args": list(args), "description": description, "seed": seed,
            "predicate": predicate or {"kind": None}}


def _acceptance_cells(contract, bundle_dir):
    """A small, deliberately non-exhaustive acceptance matrix.

    Hand cells are deterministic: their values are checkable by arithmetic, so
    seed variation would not add information and is not pretended to.  The
    constellation group carries the seven/eleven/twenty-three seeds.
    """
    hand_predicate = {
        "kind": "time_alignment",
        "require": {"min_candidates": 2, "max_invalid_pairs": 0,
                    "ideal_arms": ["oracle_now", "oracle_common",
                                   "oracle_candidate"],
                    "decomposition": [
                        "eta_estimated_x_queue_predicted",
                        "eta_estimated_x_queue_truth",
                        "eta_true_x_queue_predicted",
                        "eta_true_x_queue_truth"]},
    }
    execution_predicate = {
        "kind": "execution_modes",
        "require": {
            "per_packet": {"compute_servers": 1, "min_queued": 1,
                           "min_request_rate": 0.0},
            "per_flow": {"compute_servers": 1, "min_cache_hits": 1},
            "precomputed": {"compute_servers": 1},
            "async_point": {"bins": 1, "min_installs": 1,
                            "min_query_service_requests": 1},
            "async_window": {"bins": 4, "min_distinct_bins": 3,
                             "min_installs": 1, "min_distinct_versions": 2},
        },
    }
    cells = [
        _cell("hand-reachability", "hand_mechanism",
              "CODE.experiment_platform.time_alignment_compare",
              ["--scenario", "reachability", "--decision-id", "3"],
              "empty equal queues must show zero difference",
              predicate=hand_predicate),
        _cell("hand-contention", "hand_mechanism",
              "CODE.experiment_platform.time_alignment_compare",
              ["--scenario", "contention", "--decision-id", "4"],
              "declared competing packet: a real per-candidate cost gap",
              predicate=hand_predicate),
        _cell("exec-five-modes", "execution",
              "CODE.experiment_platform.execution_compare",
              ["--scenario", "same_flow", "--compute-servers", "1",
               "--service-s", "0.05", "--query-delay-s", "0.001"],
              "five execution modes, bounded pool, one shared flow",
              predicate=execution_predicate),
        _cell("bench-decision", "execution",
              "CODE.experiment_platform.benchmark_decision",
              ["--scenario", "same_flow", "--iterations", "200",
               "--rounds", "2", "--warmup", "20", "--service-s", "0.05",
               "--pool-sweep", "0,1,2"],
              "decision-path timing and finite-pool congestion",
              predicate={"kind": "benchmark",
                         "require": {"min_complete_rounds": 1,
                                     "pool_servers": [0, 1, 2],
                                     "min_pool_requests": True,
                                     "require_queued_at": 1}}),
    ]
    for size in (372, 891, 1500):
        cells.append(_cell(
            f"packet-{size}B", "packet_sizes",
            "CODE.experiment_platform.execution_compare",
            ["--scenario", "same_flow", "--packet-bits", str(size * 8),
             "--compute-servers", "1", "--service-s", "0.05"],
            f"fixed {size}-byte packets through all five modes",
            predicate={"kind": "execution_modes",
                       "require": {"per_flow": {"packet_bits": size * 8,
                                                "min_cache_hits": 1},
                                   "async_window": {"packet_bits": size * 8,
                                                    "bins": 4}}}))
    seed_cells = []
    for seed in (7, 11, 23):
        cfg = _seed_config(contract, bundle_dir, seed)
        seed_cells.append(_cell(
            f"constellation-seed-{seed}", "constellation",
            "CODE.experiment_platform.time_alignment_compare",
            ["--config", str(cfg), "--decision-id", "first_forward"],
            "frozen branch profile at one development seed", seed=seed,
            predicate=hand_predicate))
    return cells + seed_cells


def _dev_cells(contract, bundle_dir):
    """Development sweep: ONE factor at a time, then a small two-factor cell.

    The candidate ranges are written here, before any arm result is read.  This
    is a DEVELOPMENT tier: it selects candidates, it does not confirm anything.
    """
    def exec_cell(cell_id, args, description, require):
        return _cell(cell_id, "dev_sweep",
                     "CODE.experiment_platform.execution_compare", args,
                     description,
                     predicate={"kind": "execution_modes",
                                "require": require})

    cells = []
    for interval in ("0.1", "0.5", "2.0"):
        cells.append(exec_cell(
            f"dev-update-interval-{interval}",
            ["--scenario", "same_flow", "--update-interval-s", interval,
             "--compute-servers", "1", "--service-s", "0.05"],
            f"single factor: async update interval {interval} s",
            {"async_window": {"bins": 4, "min_installs": 1,
                              "min_query_service_requests": 1}}))
    cells.append(exec_cell(
        "dev-perflow-ttl-0.1",
        ["--scenario", "same_flow", "--per-flow-ttl-s", "0.1",
         "--compute-servers", "1", "--service-s", "0.05"],
        "single factor: per-flow TTL below the flow inter-arrival",
        {"per_flow": {"compute_servers": 1}}))
    cells.append(exec_cell(
        "dev-two-factor-packet372-pool1",
        ["--scenario", "same_flow", "--packet-bits", str(372 * 8),
         "--compute-servers", "1", "--service-s", "0.05"],
        "two-factor: smallest packet x one compute server",
        {"per_packet": {"compute_servers": 1, "min_queued": 1,
                        "packet_bits": 372 * 8}}))
    cells.append(exec_cell(
        "dev-two-factor-packet1500-pool2",
        ["--scenario", "same_flow", "--packet-bits", str(1500 * 8),
         "--compute-servers", "2", "--service-s", "0.05"],
        "two-factor: largest packet x two compute servers",
        {"per_packet": {"compute_servers": 2, "packet_bits": 1500 * 8}}))
    cells.append(exec_cell(
        "dev-query-delay-zero",
        ["--scenario", "same_flow", "--query-delay-s", "0.0",
         "--compute-servers", "1", "--service-s", "0.05"],
        "single factor: query service switched off (historical path)",
        {"per_packet": {"compute_servers": 1}}))
    # development state-time blocks: these are what size the deadline and the
    # confirmation sample, so the dev tier must contain them
    hand_predicate = {
        "kind": "time_alignment",
        "require": {"min_candidates": 2, "max_invalid_pairs": 0,
                    "ideal_arms": ["oracle_now", "oracle_common",
                                   "oracle_candidate"],
                    "decomposition": ["eta_estimated_x_queue_predicted",
                                      "eta_estimated_x_queue_truth",
                                      "eta_true_x_queue_predicted",
                                      "eta_true_x_queue_truth"]},
    }
    for seed in (7, 11, 23):
        cfg = _seed_config(contract, bundle_dir, seed)
        cells.append(_cell(
            f"dev-ta-seed-{seed}", "dev_sweep",
            "CODE.experiment_platform.time_alignment_compare",
            ["--config", str(cfg), "--decision-id", "first_forward",
             "--run-kind", "dev"],
            "development state-time block for the deadline/sample-size plan",
            seed=seed, predicate=hand_predicate))
    return cells


def _formal_package(contract, bundle_dir, formal_state=None):
    """The compiled, NOT-EXECUTED confirmation package.

    Compiling it is part of this task; running it needs an authorization this
    task does not hold.  Everything a later run needs to be reproducible is
    frozen here: the matrix, the dev/confirm separation, the sample-size rule,
    the pre-declared effect thresholds, the information permissions, the cost
    sources, the scenario-validity rule and the failure rules.
    """
    stats = contract.get("statistics") or {}
    half_width = 0.005
    examples = {}
    for s in (0.01, 0.02, 0.05, 0.1):
        examples[str(s)] = t1_stats.plan_sample_size(s, half_width=half_width)
    state = dict(formal_state or {})
    return {
        "schema": "t1-formal-package/v1",
        "status": state.get("status", "NOT_EXECUTED"),
        "execution_status": "NOT_EXECUTED",
        "design_state": state,
        "why_not_executed": "the confirmation matrix requires an explicit "
                            "authorization and a remote/formal execution "
                            "window; neither is held by this task",
        "dev_confirm_separation": {
            "dev_seeds": stats.get("dev_seeds"),
            "confirm_seeds_start": stats.get("confirm_seeds_start"),
            "rule": "candidates are selected and frozen on the dev seeds only; "
                    "the confirmation seeds are never read during selection",
        },
        "sample_size_plan": {
            "rule": stats.get("sample_size_rule"),
            "half_width": half_width,
            "formula_examples": examples,
            "pending": "replace s with the observed dev paired-difference "
                       "standard deviation, then freeze n BEFORE running",
        },
        "effect_thresholds": {
            "minimum_substantive_difference": stats.get(
                "minimum_substantive_difference"),
            "sensitivity": stats.get("sensitivity"),
            "note": "pre-declared design thresholds, not measured effects",
        },
        "information_permissions": {
            "online_snapshot_only": True,
            "forbidden_online": ["TruthSample", "BranchOutcome", "kernel",
                                 "peer caches", "future trace"],
            "audit_types": ["ObservationSnapshot", "ResourcePrediction",
                            "BranchOutcome"],
        },
        "cost_sources": {
            "compute": "shared per-satellite FIFO pool (execution."
                       "compute_servers_per_satellite)",
            "query": "shared per-satellite query service (time_alignment."
                     "query_delay_s)",
            "install": "async_routing.install_delay_s",
            "service_time": "measured host/VM timing (benchmark_decision) or "
                            "the diagnostic configured value, reported "
                            "separately",
        },
        "scenario_validity": {
            "rule": "a scenario whose requests mostly take the shared fallback "
                    "does not activate the mechanism and cannot support an "
                    "algorithm claim",
            "reported_by": "counts.fallbacks / reuse.fallbacks per cell",
        },
        "failure_rules": {
            "no_silent_drop": True,
            "invalid_pairs": "reported with reason and count",
            "budget": "BUDGET_EXCEEDED keeps completed cells",
            "cells": "a cell is ok only if its pre-declared predicate passes",
        },
        "planned_matrix": {
            "arms": ["stale", "now", "common", "candidate"],
            "modes": ["per_packet", "per_flow", "precomputed", "async_point",
                      "async_window"],
            "primary_comparison": stats.get("primary_comparison"),
            "bootstrap": stats.get("bootstrap"),
        },
    }


def _formal_cells(contract, bundle_dir):
    """REAL pending confirmation cells, generated only when the design is ready.

    A cell is only frozen once the development stage has produced a deadline and
    a common_strong choice; until then the formal tier stays empty and the
    package says PENDING_DEV_SELECTION instead of inventing a sample size from
    scenarios that cannot discriminate.
    """
    design = contract.get("formal_design") or {}
    if not design.get("ready"):
        return [], {
            "status": "PENDING_DEV_SELECTION",
            "reason": design.get("reason") or (
                "the development stage has not produced a frozen deadline and a "
                "common_strong choice, so no confirmation cell can be compiled"),
            "required": design.get("required") or [
                "a development block set with non-zero paired-difference "
                "variance",
                "a frozen deadline file (--freeze-deadline-to) with its "
                "identity",
                "a selected common_strong horizon with its provenance",
            ],
        }
    profile = contract.get("source", {}).get(
        "constellation_profile",
        "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml")
    seeds = [int(s) for s in design.get("confirm_seeds", [])]
    if not seeds:
        raise SuiteError("formal_design.ready is true but confirm_seeds is empty")
    deadline = design.get("deadline_file")
    if not deadline or not Path(deadline).exists():
        raise SuiteError(
            "formal_design.ready is true but deadline_file is missing: a "
            "confirmation cell must load a frozen D, not derive one")
    horizon = design.get("common_strong_horizon_s")
    if horizon is None:
        raise SuiteError(
            "formal_design.ready is true but common_strong_horizon_s is unset")
    cells = []
    for seed in seeds:
        cfg = _seed_config(contract, bundle_dir, seed)
        cells.append(_cell(
            f"confirm-seed-{seed}", "confirm",
            "CODE.experiment_platform.time_alignment_compare",
            ["--config", str(cfg), "--decision-id", "first_forward",
             "--deadline-from", str(deadline), "--run-kind", "confirm"],
            "frozen confirmation cell: fixed D, confirm seed, arm comparison",
            seed=seed,
            predicate={"kind": "time_alignment",
                       "require": {"min_candidates": 2,
                                   "max_invalid_pairs": 0,
                                   "ideal_arms": ["oracle_now", "oracle_common",
                                                  "oracle_candidate"],
                                   "decomposition": [
                                       "eta_estimated_x_queue_predicted",
                                       "eta_estimated_x_queue_truth",
                                       "eta_true_x_queue_predicted",
                                       "eta_true_x_queue_truth"]}}))
    return cells, {"status": "READY_TO_EXECUTE",
                   "reason": None,
                   "confirm_seeds": seeds,
                   "deadline_file": str(deadline),
                   "deadline_sha256": _sha256_file(deadline),
                   "common_strong_horizon_s": float(horizon),
                   "sample_size": design.get("sample_size")}


def _seed_config(contract, bundle_dir, seed):
    """Write a per-seed copy of the frozen branch profile into the bundle."""
    profile = contract.get("source", {}).get(
        "constellation_profile", "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml")
    src = REPO_ROOT / profile
    if not src.exists():
        raise SuiteError(f"constellation profile missing: {profile}")
    doc = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    doc.setdefault("scenario", {})
    if isinstance(doc["scenario"], dict):
        doc["scenario"]["seed"] = int(seed)
    # The base profile emits so few packets that some seeds produce no forward
    # decision at all.  The acceptance fixture must be able to FIRE; raising the
    # offered rate is declared here and does not read any arm result.
    doc.setdefault("demand", {})
    if isinstance(doc["demand"], dict):
        doc["demand"]["offered_mbps"] = 2.0
    configs = Path(bundle_dir) / "configs"
    configs.mkdir(exist_ok=True)
    out = configs / f"seed-{seed}.yaml"
    out.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return out


def compile_bundle(contract_path, out_dir):
    contract_path = Path(contract_path)
    if not contract_path.exists():
        raise SuiteError(f"contract not found: {contract_path}")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    budgets = dict(DEFAULT_BUDGETS)
    budgets.update({k: v for k, v in (contract.get("budgets") or {}).items()
                    if k in DEFAULT_BUDGETS})
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise SuiteError(f"bundle destination exists: {out_dir}")
    parent = out_dir.parent
    if not parent.is_dir() or parent.is_symlink():
        raise SuiteError(f"bundle parent must be a real directory: {parent}")
    out_dir.mkdir()
    confirm_cells, formal = _formal_cells(contract, out_dir)
    cells = (_acceptance_cells(contract, out_dir) + _dev_cells(contract,
                                                              out_dir)
             + confirm_cells)
    if len(cells) > budgets["max_cells"]:
        raise BudgetExceeded(
            f"{len(cells)} cells exceed the pre-declared max_cells "
            f"{budgets['max_cells']}")
    identity = artifact_identity.build_identity(
        driver_paths=artifact_identity.execution_chain_paths())
    for cell in cells:
        cell["input"] = _cell_input_binding(cell)
    bundle = {
        "schema": SCHEMA_BUNDLE,
        "contract_path": str(contract_path),
        "contract_sha256": _sha256_file(contract_path),
        "contract_schema": contract.get("schema"),
        "frozen_at_sha": contract.get("frozen_at_sha"),
        "budgets": budgets,
        "identity": identity,
        "execution_chain": {
            "paths": list(artifact_identity.execution_chain_paths()),
            "combined_sha256": identity["sources"]["combined_sha256"],
            "git": identity["git"],
            "runtime": identity["runtime"],
        },
        "tiers": {
            "acceptance": [c["cell_id"] for c in cells
                           if c["group"] != "dev_sweep"],
            "dev": [c["cell_id"] for c in cells if c["group"] == "dev_sweep"],
            "formal": [c["cell_id"] for c in confirm_cells],
        },
        "formal_design": contract.get("formal_design") or {"ready": False},
        "formal_package": _formal_package(contract, out_dir, formal),
        "cells": cells,
        "pre_registration": {
            "primary_comparison": contract.get("statistics", {}).get(
                "primary_comparison"),
            "minimum_substantive_difference": contract.get(
                "statistics", {}).get("minimum_substantive_difference"),
            "dev_seeds": contract.get("statistics", {}).get("dev_seeds"),
            "note": "candidate ranges are fixed here, before any arm result is "
                    "read; the acceptance tier is a mechanism fixture, not a "
                    "scientific result",
        },
        "limits": [
            "the acceptance tier is small by design: it proves the mechanisms "
            "fire, it does not generalise",
            "hand cells are deterministic; seed variation would add no "
            "information and is not claimed",
        ],
    }
    bundle["bundle_fingerprint"] = _bundle_fingerprint(bundle)
    _write_json(out_dir / "bundle.json", bundle)
    return bundle


def _cell_input_binding(cell):
    """Hash every input file a cell actually reads.

    A cell that names a config file is bound to that file content, so editing
    the config after the compile invalidates the cell instead of being re-read
    silently.
    """
    binding = {"config_sha256": None, "config_path": None,
               "scenario": None, "driver_sha256": None, "files": {}}
    args = list(cell.get("args") or [])
    for flag, value in zip(args, args[1:]):
        if flag == "--config":
            path = Path(value)
            binding["config_path"] = str(path)
            binding["config_sha256"] = (_sha256_file(path)
                                        if path.exists() else None)
        if flag == "--scenario":
            binding["scenario"] = value
        if flag in ("--deadline-from", "--checkpoint", "--metadata",
                    "--policy-checkpoint"):
            path = Path(value)
            binding["files"][flag] = {
                "path": str(path),
                "sha256": _sha256_file(path) if path.exists() else None}
    driver = REPO_ROOT / str(cell.get("driver", "")).replace(".", "/")
    driver_py = driver.with_suffix(".py")
    binding["driver_sha256"] = (_sha256_file(driver_py)
                                if driver_py.exists() else None)
    return binding


def _bundle_fingerprint(bundle):
    """Content fingerprint of everything that defines the matrix.

    Parameter edits, cell additions and contract changes all move it, so a
    structure-preserving tamper cannot pass validation.
    """
    payload = {
        "schema": bundle.get("schema"),
        "contract_sha256": bundle.get("contract_sha256"),
        "chain_sha256": (bundle.get("execution_chain") or {}).get(
            "combined_sha256"),
        "budgets": bundle.get("budgets"),
        "tiers": bundle.get("tiers"),
        "cells": bundle.get("cells"),
        "pre_registration": bundle.get("pre_registration"),
        # the formal pending package carries semantic fields (thresholds,
        # sample-size rule, permissions); editing any of them must move the
        # fingerprint, otherwise a threshold could be rewritten silently
        "formal_package": bundle.get("formal_package"),
        "formal_design": bundle.get("formal_design"),
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


# --------------------------------------------------------------- validate
def validate_bundle(bundle_dir):
    bundle_dir = Path(bundle_dir)
    path = bundle_dir / "bundle.json"
    if not path.exists():
        raise SuiteError(f"bundle.json missing in {bundle_dir}")
    bundle = json.loads(path.read_text(encoding="utf-8"))
    errors = []
    if bundle.get("schema") != SCHEMA_BUNDLE:
        errors.append(f"bundle schema {bundle.get('schema')!r}")
    ids = [c.get("cell_id") for c in bundle.get("cells", [])]
    if len(ids) != len(set(ids)):
        errors.append("duplicate cell ids")
    for cell in bundle.get("cells", []):
        for field in ("cell_id", "group", "driver", "args"):
            if field not in cell:
                errors.append(f"cell {cell.get('cell_id')!r} missing {field}")
        if not isinstance(cell.get("args"), list):
            errors.append(f"cell {cell.get('cell_id')!r} args must be a list")
        recorded = cell.get("input")
        if recorded is None:
            errors.append(f"cell {cell.get('cell_id')!r} has no input binding")
            continue
        fresh = _cell_input_binding(cell)
        if fresh != recorded:
            changed = sorted(k for k in set(fresh) | set(recorded)
                             if fresh.get(k) != recorded.get(k))
            errors.append(
                f"cell {cell.get('cell_id')!r} input binding changed: "
                f"{changed}")
        if recorded.get("config_path") and recorded.get("config_sha256") is None:
            errors.append(
                f"cell {cell.get('cell_id')!r} config file missing")
    budgets = bundle.get("budgets") or {}
    for key in DEFAULT_BUDGETS:
        if key not in budgets:
            errors.append(f"budget {key} missing")
    package = bundle.get("formal_package") or {}
    if not package:
        errors.append("formal pending package is missing")
    else:
        for field in ("dev_confirm_separation", "sample_size_plan",
                      "effect_thresholds", "information_permissions",
                      "cost_sources", "scenario_validity", "failure_rules",
                      "planned_matrix"):
            if field not in package:
                errors.append(f"formal package missing {field}")
        if package.get("execution_status") != "NOT_EXECUTED":
            errors.append("formal package must be declared NOT_EXECUTED")
        if package.get("status") not in ("NOT_EXECUTED",
                                         "PENDING_DEV_SELECTION",
                                         "READY_TO_EXECUTE"):
            errors.append(
                f"unknown formal package status {package.get('status')!r}")
        if (package.get("status") == "READY_TO_EXECUTE"
                and not (package.get("design_state") or {}).get(
                    "deadline_sha256")):
            errors.append("a READY formal package must bind its deadline hash")
    if "formal" not in (bundle.get("tiers") or {}):
        errors.append("a formal tier entry is required (even if empty)")
    # --- content fingerprint: a structure-preserving parameter edit moves it
    recorded_fp = bundle.get("bundle_fingerprint")
    if recorded_fp is None:
        errors.append("bundle has no fingerprint (recompile)")
    elif recorded_fp != _bundle_fingerprint(bundle):
        errors.append("bundle content changed since compile "
                      "(fingerprint mismatch)")
    # --- contract content, re-read from disk
    contract_path = Path(str(bundle.get("contract_path") or ""))
    if not contract_path.exists():
        errors.append(f"contract file missing: {contract_path}")
    elif _sha256_file(contract_path) != bundle.get("contract_sha256"):
        errors.append("contract content changed since compile")
    # --- execution chain, recomputed from the CURRENT sources
    current = artifact_identity.current_identity()
    recorded_chain = bundle.get("execution_chain") or {}
    fresh_chain = current["sources"]["combined_sha256"]
    if fresh_chain != recorded_chain.get("combined_sha256"):
        errors.append("execution chain changed since compile: recompile "
                      "instead of reusing this bundle")
    if ((current.get("git") or {}).get("commit")
            != (recorded_chain.get("git") or {}).get("commit")):
        errors.append("git commit changed since compile: recompile")
    report = {"schema": VALIDATION_SCHEMA,
              "bundle": str(path),
              "bundle_sha256": _sha256_file(path),
              "bundle_fingerprint": recorded_fp,
              "execution_chain_sha256": recorded_chain.get("combined_sha256"),
              "current_chain_sha256": fresh_chain,
              "cells": len(ids),
              "valid": not errors,
              "errors": errors}
    _write_json(bundle_dir / "validation.json", report)
    if errors:
        raise SuiteError("bundle validation failed: " + "; ".join(errors))
    return report


# -------------------------------------------------------------------- run
def _cell_identity(bundle, cell):
    """What a cell result is bound to: matrix content + inputs + code chain."""
    return {
        "bundle_fingerprint": bundle.get("bundle_fingerprint"),
        "contract_sha256": bundle.get("contract_sha256"),
        "chain_sha256": (bundle.get("execution_chain") or {}).get(
            "combined_sha256"),
        "cell_id": cell.get("cell_id"),
        "input": dict(cell.get("input") or {}),
    }


def _identity_matches(recorded, current):
    return recorded == current


def _execute_cell(cell, out_root, budgets):
    cell_dir = Path(out_root) / "cells" / cell["cell_id"]
    cell_dir.mkdir(parents=True, exist_ok=True)
    # Old evidence is never overwritten: a superseded result is moved aside and
    # named, so a reader can still see what the previous identity produced.
    result_path = cell_dir / "result.json"
    superseded = None
    if result_path.exists():
        index = 1
        while (cell_dir / f"result.superseded-{index}.json").exists():
            index += 1
        superseded = cell_dir / f"result.superseded-{index}.json"
        result_path.rename(superseded)
    argv = [sys.executable, "-m", cell["driver"], *cell["args"],
            "--out", str(result_path)]
    started = time.perf_counter()
    timed_out = False
    try:
        proc = subprocess.run(argv, cwd=str(REPO_ROOT), capture_output=True,
                              text=True, timeout=budgets["cell_wall_s"])
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = None
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (
            exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (
            exc.stderr or "")
    wall = time.perf_counter() - started
    result_probe = _inspect_result(result_path)
    predicate = cell.get("predicate") or {"kind": None}
    verdict = (check_predicate(result_probe["payload"], predicate)
               if result_probe["payload"] is not None
               else {"passed": False, "checks": [],
                     "reason": "result unavailable"})
    if timed_out:
        status = "timeout"
    elif returncode != 0:
        status = "error"
    elif not result_probe["exists"]:
        status = "error"
    elif result_probe["parse_error"] is not None:
        status = "error"
    elif result_probe["schema"] is None:
        status = "error"
    elif not verdict["passed"]:
        # a returned exit code is not evidence that the mechanism under test
        # fired: the cell must satisfy its PRE-DECLARED behaviour predicate
        status = "predicate_failed"
    else:
        status = "ok"
    return {
        "cell_id": cell["cell_id"],
        "status": status,
        "returncode": returncode,
        "wall_s": wall,
        "argv": argv,
        "stdout_tail": (stdout or "")[-2000:],
        "stderr_tail": (stderr or "")[-2000:],
        "result_path": str(Path("cells") / cell["cell_id"] / "result.json"),
        "result_sha256": result_probe["sha256"],
        "result_schema": result_probe["schema"],
        "result_parse_error": result_probe["parse_error"],
        "predicate": predicate,
        "predicate_verdict": verdict,
        "input": dict(cell.get("input") or {}),
        "superseded_result": (None if superseded is None
                              else str(superseded.relative_to(out_root))),
        "exit_reason": ("cell_wall_s exceeded" if timed_out else
                        (None if status == "ok" else
                         (result_probe["parse_error"]
                          or verdict.get("reason")
                          or (f"returncode {returncode}"
                              if returncode else "result missing or invalid")))),
    }


def _check_execution_predicate(result, require):
    """Machine-check the pre-declared behaviour of an execution cell."""
    checks = []
    modes = {m.get("mode"): m for m in result.get("modes", [])}
    for mode, want in sorted(require.items()):
        row = modes.get(mode)
        checks.append([f"mode {mode} present", row is not None, None])
        if row is None:
            continue
        reuse = row.get("reuse") or {}
        compute = row.get("compute") or {}
        scale = row.get("scale") or {}
        query_service = (row.get("query_service") or {}).get("totals") or {}
        if "min_cache_hits" in want:
            checks.append([f"{mode} cache hits >= {want['min_cache_hits']}",
                           reuse.get("per_flow_cache_hits", 0)
                           >= want["min_cache_hits"],
                           reuse.get("per_flow_cache_hits")])
        if "bins" in want:
            checks.append([f"{mode} bins == {want['bins']}",
                           reuse.get("bins") == want["bins"],
                           reuse.get("bins")])
        if "min_distinct_bins" in want:
            distinct = {k for k in (reuse.get("query_bin_counts") or {})
                        if k != "None"}
            checks.append([f"{mode} distinct queried bins >= "
                           f"{want['min_distinct_bins']}",
                           len(distinct) >= want["min_distinct_bins"],
                           sorted(distinct)])
        if "min_distinct_versions" in want:
            versions = {k for k in (reuse.get("query_version_counts") or {})
                        if k != "None"}
            checks.append([f"{mode} distinct queried versions >= "
                           f"{want['min_distinct_versions']}",
                           len(versions) >= want["min_distinct_versions"],
                           sorted(versions)])
        if "min_installs" in want:
            checks.append([f"{mode} installs >= {want['min_installs']}",
                           reuse.get("schedule_installs", 0)
                           >= want["min_installs"],
                           reuse.get("schedule_installs")])
        if "max_fallbacks" in want:
            checks.append([f"{mode} fallbacks <= {want['max_fallbacks']}",
                           reuse.get("fallbacks", 0) <= want["max_fallbacks"],
                           reuse.get("fallbacks")])
        if "compute_servers" in want:
            checks.append([f"{mode} pool == {want['compute_servers']}",
                           compute.get("servers") == want["compute_servers"],
                           compute.get("servers")])
        if "min_queued" in want:
            checks.append([f"{mode} queued requests >= {want['min_queued']}",
                           compute.get("queued_requests", 0)
                           >= want["min_queued"],
                           compute.get("queued_requests")])
        if "min_query_service_requests" in want:
            checks.append([f"{mode} query service requests >= "
                           f"{want['min_query_service_requests']}",
                           query_service.get("requests", 0)
                           >= want["min_query_service_requests"],
                           query_service.get("requests")])
        if "packet_bits" in want:
            checks.append([f"{mode} packet bits == {want['packet_bits']}",
                           scale.get("packet_bits") == want["packet_bits"],
                           scale.get("packet_bits")])
        if "min_request_rate" in want:
            rate = scale.get("forward_requests_per_satellite_per_s")
            checks.append([f"{mode} per-satellite request rate > 0",
                           bool(rate and rate > want["min_request_rate"]),
                           rate])
    return checks


def check_predicate(result, predicate):
    """Evaluate a cell predicate against its result document."""
    kind = (predicate or {}).get("kind")
    if kind is None:
        return {"passed": True, "checks": [], "kind": None}
    if kind == "execution_modes":
        checks = _check_execution_predicate(result,
                                            predicate.get("require") or {})
    elif kind == "time_alignment":
        require = predicate.get("require") or {}
        checks = []
        counts = result.get("counts") or {}
        if "min_candidates" in require:
            checks.append(["candidates >= min",
                           counts.get("candidates", 0)
                           >= require["min_candidates"],
                           counts.get("candidates")])
        if "max_invalid_pairs" in require:
            checks.append(["invalid pairs <= max",
                           counts.get("invalid_pairs", 0)
                           <= require["max_invalid_pairs"],
                           counts.get("invalid_pairs")])
        for name in require.get("ideal_arms", []):
            arm = (result.get("ideal_arms") or {}).get(name)
            checks.append([f"ideal arm {name} present", arm is not None,
                           None])
            if arm is not None:
                checks.append([f"ideal arm {name} chose an action",
                               bool(arm.get("chosen")), arm.get("chosen")])
                checks.append([f"ideal arm {name} recorded truth inputs",
                               bool(arm.get("truth_inputs")), None])
        for name in require.get("decomposition", []):
            checks.append([f"2x2 cell {name} present",
                           name in (result.get("eta_queue_2x2") or {}), None])
    elif kind == "benchmark":
        require = predicate.get("require") or {}
        checks = []
        full = result.get("full_decision") or {}
        if "min_complete_rounds" in require:
            checks.append(["complete rounds >= min",
                           full.get("complete_rounds", 0)
                           >= require["min_complete_rounds"],
                           full.get("complete_rounds")])
        sweep = {row.get("servers"): row
                 for row in (result.get("finite_pool") or [])}
        for servers in require.get("pool_servers", []):
            row = sweep.get(servers)
            checks.append([f"pool N={servers} present", row is not None, None])
            if row is not None and row.get("skipped"):
                checks.append([f"pool N={servers} executed", False,
                               row.get("reason")])
        if require.get("min_pool_requests"):
            executed = [row for row in sweep.values() if not row.get("skipped")]
            checks.append(["pool sweep produced requests",
                           any(row.get("requests", 0) > 0 for row in executed),
                           {row.get("servers"): row.get("requests")
                            for row in executed}])
        if require.get("require_queued_at"):
            servers = require["require_queued_at"]
            row = sweep.get(servers) or {}
            checks.append([f"pool N={servers} actually queued",
                           row.get("queued", 0) > 0,
                           {"queued": row.get("queued"),
                            "max_wait_s": row.get("max_wait_s")}])
    else:
        return {"passed": False, "checks": [],
                "reason": f"unknown predicate kind {kind!r}", "kind": kind}
    return {"passed": all(c[1] for c in checks), "kind": kind,
            "checks": [{"check": c[0], "passed": bool(c[1]),
                        "observed": c[2]} for c in checks],
            "reason": None if all(c[1] for c in checks)
            else "; ".join(c[0] for c in checks if not c[1])}


def _inspect_result(result_path):
    """Existence + parseability + schema + content hash of one cell result."""
    result_path = Path(result_path)
    probe = {"exists": False, "sha256": None, "schema": None,
             "parse_error": None, "payload": None}
    if not result_path.exists():
        return probe
    probe["exists"] = True
    try:
        raw = result_path.read_bytes()
        probe["sha256"] = hashlib.sha256(raw).hexdigest()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        probe["parse_error"] = f"{type(exc).__name__}: {exc}"
        return probe
    if not isinstance(payload, dict):
        probe["parse_error"] = "result is not a JSON object"
        return probe
    probe["schema"] = payload.get("schema")
    probe["payload"] = payload
    return probe


def run_bundle(bundle_dir, tier, out_dir):
    bundle_dir = Path(bundle_dir)
    # a run must never start from a bundle whose inputs or code have moved
    validate_bundle(bundle_dir)
    bundle = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    if tier == "formal":
        raise SuiteError(
            "the formal tier is a COMPILED PENDING PACKAGE, not a runnable "
            "matrix: executing it needs an explicit authorization this task "
            "does not hold; it is validated by validate, never run here")
    cell_ids = (bundle.get("tiers") or {}).get(tier)
    if cell_ids is None:
        raise SuiteError(f"unknown tier {tier!r}; "
                         f"have {sorted((bundle.get('tiers') or {}))}")
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise SuiteError(f"run destination exists: {out_dir}")
    if not out_dir.parent.is_dir() or out_dir.parent.is_symlink():
        raise SuiteError(f"run parent must be a real directory: {out_dir.parent}")
    out_dir.mkdir()
    budgets = bundle["budgets"]
    if len(cell_ids) > budgets["max_cells"]:
        raise BudgetExceeded("tier exceeds max_cells")
    cells = {c["cell_id"]: c for c in bundle["cells"]}
    started = time.perf_counter()
    records = []
    status = "ok"
    for cell_id in cell_ids:
        if time.perf_counter() - started > budgets["total_wall_s"]:
            status = "BUDGET_EXCEEDED"
            break
        cell = cells[cell_id]
        record = _execute_cell(cell, out_dir, budgets)
        record["identity"] = _cell_identity(bundle, cell)
        _write_json(out_dir / "cells" / cell_id / "cell.json", record)
        records.append(record)
    failed = sum(1 for r in records if r["status"] != "ok")
    if status != "BUDGET_EXCEEDED" and failed:
        status = "FAILED_CELLS"
    run_doc = {
        "schema": SCHEMA_RUN,
        "bundle_dir": str(bundle_dir),
        "tier": tier,
        "status": status,
        "budgets": budgets,
        "identity": bundle.get("identity"),
        "started_wall_s": started,
        "cells": records,
        "counts": _count_records(records, len(cell_ids)),
    }
    _write_json(out_dir / "run.json", run_doc)
    return run_doc


def _count_records(records, total):
    """Exhaustive cell tally.

    Every terminal state is counted and not_ok sums everything that is not ok,
    including cells that were never executed, so a predicate failure (or any
    future state) cannot be left out of the verdict.
    """
    counts = {"total": total, "executed": len(records),
              "ok": 0, "error": 0, "timeout": 0, "predicate_failed": 0,
              "invalidated": 0, "other": 0}
    for record in records:
        status = record.get("status")
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
    counts["not_ok"] = (counts["executed"] - counts["ok"]
                        + (total - counts["executed"]))
    return counts


def _verify_recorded_result(run_dir, record):
    """Prove a recorded ok cell still has a valid, unmodified result.

    Returns None when the record is acceptable, or a precise reason string.  A
    self-consistent old record is NOT evidence: existence, JSON validity, a
    schema and the content hash are all re-derived from disk.
    """
    if record.get("status") != "ok":
        return None
    stored = record.get("result_path")
    if not stored:
        return "record has no result_path"
    probe = _inspect_result(Path(run_dir) / stored)
    if not probe["exists"]:
        return "result file is missing"
    if probe["parse_error"] is not None:
        return f"result is not valid JSON: {probe['parse_error']}"
    if probe["schema"] is None:
        return "result has no schema"
    recorded_sha = record.get("result_sha256")
    if recorded_sha is None:
        return "record has no result hash"
    if recorded_sha != probe["sha256"]:
        return ("result content changed since it was recorded "
                f"({recorded_sha[:12]} != {probe['sha256'][:12]})")
    if record.get("result_schema") != probe["schema"]:
        return "result schema changed since it was recorded"
    return None


def resume_run(run_dir):
    run_dir = Path(run_dir)
    run_doc = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    bundle_dir = Path(run_doc["bundle_dir"])
    # fresh validation: the bundle must still match the contract and the code on
    # disk, and the on-disk results must still match their recorded hashes
    validate_bundle(bundle_dir)
    bundle = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
    current_code = artifact_identity.chain_sha256()
    recorded_chain = (bundle.get("execution_chain") or {}).get("combined_sha256")
    if current_code != recorded_chain:
        raise SuiteError(
            "the execution chain changed since this bundle was compiled "
            f"({str(current_code)[:12]} != {str(recorded_chain)[:12]}); "
            "recompile a NEW identity instead of resuming the old one")
    cells = {c["cell_id"]: c for c in bundle["cells"]}
    budgets = run_doc["budgets"]
    records = list(run_doc["cells"])
    done = {r["cell_id"]: r for r in records}
    revalidated = []
    for cell_id, record in done.items():
        cell = cells.get(cell_id)
        if cell is None:
            revalidated.append({"cell_id": cell_id, "action": "orphan",
                                "reason": "cell no longer in the bundle"})
            continue
        reason = _verify_recorded_result(run_dir, record)
        if reason is not None:
            record["status"] = "invalidated"
            record["exit_reason"] = reason
            revalidated.append({"cell_id": cell_id, "action": "invalidated",
                                "reason": reason})
            continue
        if record.get("status") != "ok":
            continue
        current = _cell_identity(bundle, cell)
        if not _identity_matches(record.get("identity"), current):
            record["status"] = "invalidated"
            record["exit_reason"] = "recorded identity != current identity"
            revalidated.append({"cell_id": cell_id, "action": "invalidated",
                                "reason": "identity changed"})
    retried = []
    started = time.perf_counter()
    for cell_id in (bundle.get("tiers") or {}).get(run_doc["tier"], []):
        record = done.get(cell_id)
        if record is not None and record.get("status") == "ok":
            continue
        if time.perf_counter() - started > budgets["total_wall_s"]:
            run_doc["status"] = "BUDGET_EXCEEDED"
            break
        cell = cells[cell_id]
        fresh = _execute_cell(cell, run_dir, budgets)
        fresh["identity"] = _cell_identity(bundle, cell)
        _write_json(run_dir / "cells" / cell_id / "cell.json", fresh)
        if record is not None:
            records[records.index(record)] = fresh
        else:
            records.append(fresh)
        done[cell_id] = fresh
        retried.append(cell_id)
    run_doc["cells"] = records
    run_doc["resume"] = {"revalidated": revalidated, "retried": retried,
                         "current_chain_sha256": current_code,
                         "bundle_fingerprint": bundle.get("bundle_fingerprint")}
    tier_total = len((bundle.get("tiers") or {}).get(run_doc["tier"], []))
    run_doc["counts"] = _count_records(records, tier_total)
    if run_doc["counts"]["not_ok"]:
        run_doc["status"] = "FAILED_CELLS"
    elif run_doc.get("status") not in ("BUDGET_EXCEEDED",):
        run_doc["status"] = "ok"
    _write_json(run_dir / "run.json", run_doc)
    return run_doc


# ----------------------------------------------------------------- report
def report_run(run_dir):
    run_dir = Path(run_dir)
    run_doc = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    rows = []
    for record in run_doc["cells"]:
        result_path = run_dir / record["result_path"]
        payload = None
        if result_path.exists():
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None
        # a report must never call a cell ok when its result vanished or moved
        reason = _verify_recorded_result(run_dir, record)
        status = record["status"] if reason is None else "invalidated"
        verdict = record.get("predicate_verdict") or {}
        rows.append({
            "cell_id": record["cell_id"],
            "status": status,
            "recorded_status": record["status"],
            "invalid_reason": reason,
            "predicate_passed": bool(verdict.get("passed", True)),
            "predicate_checks": len(verdict.get("checks") or []),
            "wall_s": record["wall_s"],
            "schema": (payload or {}).get("schema"),
            "result_sha256": record.get("result_sha256"),
            "summary": _summarise(payload),
        })
    counts = dict(run_doc["counts"])
    counts["verified_ok"] = sum(1 for r in rows if r["status"] == "ok")
    counts["invalidated_at_report"] = sum(1 for r in rows
                                          if r["status"] == "invalidated")
    counts["predicate_failed_at_report"] = sum(
        1 for r in rows if r["status"] == "predicate_failed")
    counts["not_ok_at_report"] = sum(1 for r in rows if r["status"] != "ok")
    run_status = run_doc["status"]
    if counts["not_ok_at_report"]:
        # a predicate failure, a lost result or an invalidated cell means the
        # round did NOT succeed, whatever the individual exit codes said
        run_status = "FAILED_CELLS"
    report = {
        "schema": SCHEMA_REPORT,
        "run": str(run_dir),
        "tier": run_doc["tier"],
        "run_status": run_status,
        "counts": counts,
        "cells": rows,
        "by_group": _by_group(run_doc, rows),
        "statistics": _statistics_summary(run_dir, rows),
        "limits": [
            "the acceptance tier proves the mechanisms fire; it is not a "
            "scientific result and carries no confirmatory claim",
            "a failed cell is reported as failed, never dropped",
        ],
    }
    _write_json(run_dir / "report.json", report)
    (run_dir / "REPORT.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _summarise(payload):
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") == "time-alignment-compare/v1":
        return {"counts": payload.get("counts"),
                "oracle_chosen": (payload.get("oracle") or {}).get("chosen"),
                "regret": {arm: payload["arms"][arm].get("regret")
                           for arm in payload.get("arms", {})}}
    if payload.get("schema") == "execution-compare/v1":
        return {"ddqn": (payload.get("ddqn") or {}).get("state"),
                "modes": {r["mode"]: {"delivered": r["outcome"]["delivered"],
                                      "compute_requests": r["compute"][
                                          "decision_requests"],
                                      "cache_hits": r["reuse"][
                                          "per_flow_cache_hits"],
                                      "installs": r["reuse"]["schedule_installs"]}
                          for r in payload.get("modes", [])}}
    if payload.get("schema") == "benchmark-decision/v1":
        return {"full_p50_us": None if not payload["full_decision"]["stats"].get(
                    "p50_s") else payload["full_decision"]["stats"]["p50_s"] * 1e6,
                "full_p99_us": None if not payload["full_decision"]["stats"].get(
                    "p99_s") else payload["full_decision"]["stats"]["p99_s"] * 1e6,
                "complete_rounds": payload["full_decision"]["complete_rounds"]}
    return None


def _statistics_summary(run_dir, rows):
    """Wire the frozen statistics into the run summary.

    A block is one cell that compared the primary arms at one branch.  With a
    single block nothing confirmatory can be said, and the summary says so
    instead of printing a number without a unit of replication.
    """
    import statistics as _statistics

    blocks = []
    excluded = []
    for row in rows:
        schema = row.get("schema")
        if not schema:
            # The cell produced no readable result at all.  It is a failed
            # BLOCK CANDIDATE, not a different kind of cell, so it must be
            # listed with its reason.  The schema filter used to run first
            # and silently dropped these from `excluded` (review S4-d).
            excluded.append({
                "unit": row.get("cell_id"),
                "reason": (f"no readable result (recorded status "
                           f"{row.get('status')!r}, schema {schema!r}"
                           + (f", {row['invalid_reason']}"
                              if row.get("invalid_reason") else "") + ")")})
            continue
        if schema != "time-alignment-compare/v1":
            continue          # a different kind of cell: never a block
        if row.get("status") != "ok":
            excluded.append({"unit": row["cell_id"],
                             "reason": f"cell status {row.get('status')}"})
            continue
        if not row.get("predicate_passed", True):
            excluded.append({"unit": row["cell_id"],
                             "reason": "behaviour predicate failed"})
            continue
        path = run_dir / "cells" / row["cell_id"] / "result.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            excluded.append({"unit": row["cell_id"],
                             "reason": "result unreadable"})
            continue
        arms = payload.get("arms") or {}
        common = (arms.get("common") or {}).get("regret")
        candidate = (arms.get("candidate") or {}).get("regret")
        if common is None or candidate is None:
            excluded.append({"unit": row["cell_id"],
                             "reason": "primary arms missing"})
            continue
        source = payload.get("source") or {}
        blocks.append({"unit": row["cell_id"],
                       "common_regret": float(common),
                       "candidate_regret": float(candidate),
                       "common_rule": source.get("arm"),
                       "common_horizon_s": (payload.get("arms", {})
                                            .get("common", {})
                                            .get("common_horizon_s")),
                       "censored": (payload.get("counts") or {}).get("censored"),
                       "deadline": (payload.get("deadline") or {}).get(
                           "deadline_s"),
                       "deadline_source": (payload.get("deadline") or {}).get(
                           "source"),
                       "run_kind": (payload.get("deadline") or {}).get(
                           "run_kind")})
    frozen_common = _common_strong_state(blocks)
    summary = {
        "unit_of_replication": "scenario x trace x seed (one cell here)",
        "blocks": len(blocks),
        "primary_comparison": (
            "common_strong regret - candidate regret"
            if frozen_common["frozen"] else
            "common regret - candidate regret (common_strong NOT frozen; see "
            "common_strong)"),
        "common_strong": frozen_common,
        "minimum_substantive_difference": (
            t1_stats.MINIMUM_SUBSTANTIVE_DIFFERENCE),
        "sensitivity": [0.005, 0.02],
        "per_block": blocks,
        "excluded": excluded,
        "rule": "only cells whose result integrity AND behaviour predicate "
                "both passed enter the paired analysis",
    }
    if not blocks:
        summary["note"] = ("no time-alignment block in this tier: nothing to "
                           "pair")
        return summary
    diffs = [b["common_regret"] - b["candidate_regret"] for b in blocks]
    summary["paired_differences"] = diffs
    summary["mean_difference"] = _statistics.fmean(diffs)
    if len(diffs) < 2:
        summary["note"] = ("a single block is one contrast: no interval, no "
                           "sample-size estimate and no confirmatory claim")
        summary["sample_size_plan"] = {
            "pending": "needs at least two development blocks to estimate s"}
        return summary
    common_list = [b["common_regret"] for b in blocks]
    candidate_list = [b["candidate_regret"] for b in blocks]
    summary["bootstrap"] = t1_stats.primary_comparison_delta(
        common_list, candidate_list)
    s = _statistics.stdev(diffs)
    plan = {
        "observed_std": s,
        "planned_n": t1_stats.plan_sample_size(s),
        "half_width": 0.005,
        "rule": "n = max(20, ceil((1.96*s/0.005)^2))",
        "precision": t1_stats.precision_report(diffs),
        "note": "planning approximation for precision only; not power",
    }
    if s == 0.0:
        plan["degenerate"] = True
        plan["warning"] = (
            "every development block shows the SAME paired difference, so the "
            "standard deviation is zero and the planned n is only the floor: "
            "this sample carries no dispersion information and cannot size a "
            "confirmation run by itself")
    summary["sample_size_plan"] = plan
    return summary


def _common_strong_state(blocks):
    """Has a common_strong horizon actually been SELECTED and frozen?

    The plan requires the shared future horizon to be chosen on development
    data (mean/median/p25/p50/p75 offsets) and then frozen.  If the development
    blocks cannot discriminate (every paired difference identical), no
    candidate can be selected, and saying so is the honest outcome.
    """
    candidates = ["mean_eta_offset", "median_eta_offset", "p25_offset",
                  "p50_offset", "p75_offset"]
    if not blocks:
        return {"frozen": False, "candidates": candidates, "evaluated": [],
                "reason": "no development block in this tier"}
    differences = [b["common_regret"] - b["candidate_regret"] for b in blocks]
    distinct = sorted({round(d, 12) for d in differences})
    if len(distinct) <= 1:
        return {
            "frozen": False,
            "candidates": candidates,
            "evaluated": [{"rule_used": b.get("common_rule"),
                           "unit": b["unit"],
                           "difference": b["common_regret"]
                           - b["candidate_regret"]} for b in blocks],
            "reason": ("every development block shows the SAME paired "
                       "difference, so the dev scenarios cannot discriminate "
                       "between horizon candidates: common_strong is NOT "
                       "frozen and no confirmation sample size can be derived "
                       "from them"),
            "required": ("a development block set with non-zero paired "
                         "difference variance, then re-run the selection over "
                         "the five pre-declared candidates"),
        }
    return {
        "frozen": True,
        "candidates": candidates,
        "rule_used": blocks[0].get("common_rule"),
        "note": "the frozen rule is the one the development cells used; the "
                "selection evidence is the per-block difference below",
        "evaluated": [{"rule_used": b.get("common_rule"), "unit": b["unit"],
                       "difference": b["common_regret"]
                       - b["candidate_regret"]} for b in blocks],
        "reason": None,
    }


def _by_group(run_doc, rows):
    groups = {}
    for row in rows:
        cell_id = row["cell_id"]
        group = "hand_mechanism"
        if cell_id.startswith("exec-") or cell_id.startswith("bench-"):
            group = "execution"
        elif cell_id.startswith("constellation-"):
            group = "constellation"
        groups.setdefault(group, {"ok": 0, "other": 0})
        if row["status"] == "ok":
            groups[group]["ok"] += 1
        else:
            groups[group]["other"] += 1
    return groups


def _markdown(report):
    lines = ["# T1-SUITE acceptance report", "",
             f"- tier: {report['tier']}",
             f"- run status: {report['run_status']}",
             "- cells: " + json.dumps(report["counts"], ensure_ascii=False), "",
             "| cell | status | wall_s | schema |", "|---|---|---|---|"]
    for row in report["cells"]:
        lines.append(f"| {row['cell_id']} | {row['status']} | "
                     f"{row['wall_s']:.3f} | {row['schema']} |")
    lines += ["", "## Limits", ""]
    lines += [f"- {item}" for item in report["limits"]]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------- CLI
def main(argv=None):
    parser = argparse.ArgumentParser(description="T1 diagnostic suite")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compile")
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("validate")
    p.add_argument("--bundle", type=Path, required=True)
    p = sub.add_parser("run")
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--tier", default="acceptance")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("resume")
    p.add_argument("--run-dir", type=Path, required=True)
    p = sub.add_parser("report")
    p.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "compile":
            bundle = compile_bundle(args.contract, args.out)
            print(json.dumps({"status": "compiled", "out": str(args.out),
                              "cells": len(bundle["cells"]),
                              "contract_sha256": bundle["contract_sha256"]},
                             ensure_ascii=False))
        elif args.command == "validate":
            report = validate_bundle(args.bundle)
            print(json.dumps({"status": "validated",
                              "cells": report["cells"],
                              "valid": report["valid"]}, ensure_ascii=False))
        elif args.command == "run":
            run = run_bundle(args.bundle, args.tier, args.out)
            print(json.dumps({"status": run["status"], "out": str(args.out),
                              "counts": run["counts"]}, ensure_ascii=False))
            # exit status is machine-readable: 0 only when the tier fully
            # succeeded, so automation cannot read a failed round as green
            return 0 if run["status"] == "ok" else 3
        elif args.command == "resume":
            run = resume_run(args.run_dir)
            print(json.dumps({"status": run["status"],
                              "counts": run["counts"],
                              "resume": run.get("resume")}, ensure_ascii=False))
            return 0 if run["status"] == "ok" else 3
        elif args.command == "report":
            report = report_run(args.run_dir)
            print(json.dumps({"status": "reported",
                              "run_status": report["run_status"],
                              "cells": len(report["cells"])},
                             ensure_ascii=False))
            return 0 if report["run_status"] == "ok" else 3
    except (SuiteError, BudgetExceeded) as exc:
        print(f"SUITE REFUSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
