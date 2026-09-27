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

from CODE.experiment_platform import artifact_identity

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
def _cell(cell_id, group, driver, args, description, seed=None):
    return {"cell_id": cell_id, "group": group, "driver": driver,
            "args": list(args), "description": description, "seed": seed}


def _acceptance_cells(contract, bundle_dir):
    """A small, deliberately non-exhaustive acceptance matrix.

    Hand cells are deterministic: their values are checkable by arithmetic, so
    seed variation would not add information and is not pretended to.  The
    constellation group carries the seven/eleven/twenty-three seeds.
    """
    cells = [
        _cell("hand-reachability", "hand_mechanism",
              "CODE.experiment_platform.time_alignment_compare",
              ["--scenario", "reachability", "--decision-id", "3"],
              "empty equal queues must show zero difference"),
        _cell("hand-contention", "hand_mechanism",
              "CODE.experiment_platform.time_alignment_compare",
              ["--scenario", "contention", "--decision-id", "4"],
              "declared competing packet: a real per-candidate cost gap"),
        _cell("exec-five-modes", "execution",
              "CODE.experiment_platform.execution_compare",
              ["--scenario", "contention"],
              "five execution modes on one fair trace"),
        _cell("bench-decision", "execution",
              "CODE.experiment_platform.benchmark_decision",
              ["--scenario", "reachability", "--iterations", "200",
               "--rounds", "2", "--warmup", "20", "--pool-sweep", "0,1,2"],
              "decision-path timing and finite-pool pressure"),
    ]
    seed_cells = []
    for seed in (7, 11, 23):
        cfg = _seed_config(contract, bundle_dir, seed)
        seed_cells.append(_cell(
            f"constellation-seed-{seed}", "constellation",
            "CODE.experiment_platform.time_alignment_compare",
            ["--config", str(cfg), "--decision-id", "first_forward"],
            "frozen branch profile at one development seed", seed=seed))
    return cells + seed_cells


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
    cells = _acceptance_cells(contract, out_dir)
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
        "tiers": {"acceptance": [c["cell_id"] for c in cells]},
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
               "scenario": None, "driver_sha256": None}
    args = list(cell.get("args") or [])
    for flag, value in zip(args, args[1:]):
        if flag == "--config":
            path = Path(value)
            binding["config_path"] = str(path)
            binding["config_sha256"] = (_sha256_file(path)
                                        if path.exists() else None)
        if flag == "--scenario":
            binding["scenario"] = value
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
        "input": dict(cell.get("input") or {}),
        "superseded_result": (None if superseded is None
                              else str(superseded.relative_to(out_root))),
        "exit_reason": ("cell_wall_s exceeded" if timed_out else
                        (None if status == "ok" else
                         (result_probe["parse_error"]
                          or (f"returncode {returncode}"
                              if returncode else "result missing or invalid")))),
    }


def _inspect_result(result_path):
    """Existence + parseability + schema + content hash of one cell result."""
    result_path = Path(result_path)
    probe = {"exists": False, "sha256": None, "schema": None,
             "parse_error": None}
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
    return probe


def run_bundle(bundle_dir, tier, out_dir):
    bundle_dir = Path(bundle_dir)
    # a run must never start from a bundle whose inputs or code have moved
    validate_bundle(bundle_dir)
    bundle = json.loads((bundle_dir / "bundle.json").read_text(encoding="utf-8"))
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
    failed = sum(1 for r in records if r["status"] in ("error", "timeout"))
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
        "counts": {
            "total": len(cell_ids),
            "executed": len(records),
            "ok": sum(1 for r in records if r["status"] == "ok"),
            "error": sum(1 for r in records if r["status"] == "error"),
            "timeout": sum(1 for r in records if r["status"] == "timeout"),
        },
    }
    _write_json(out_dir / "run.json", run_doc)
    return run_doc


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
    run_doc["counts"] = {
        "total": len((bundle.get("tiers") or {}).get(run_doc["tier"], [])),
        "executed": len(records),
        "ok": sum(1 for r in records if r.get("status") == "ok"),
        "error": sum(1 for r in records if r.get("status") == "error"),
        "timeout": sum(1 for r in records if r.get("status") == "timeout"),
        "invalidated": sum(1 for r in records
                           if r.get("status") == "invalidated"),
    }
    if run_doc["counts"]["error"] or run_doc["counts"]["timeout"]:
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
        rows.append({
            "cell_id": record["cell_id"],
            "status": status,
            "recorded_status": record["status"],
            "invalid_reason": reason,
            "wall_s": record["wall_s"],
            "schema": (payload or {}).get("schema"),
            "result_sha256": record.get("result_sha256"),
            "summary": _summarise(payload),
        })
    counts = dict(run_doc["counts"])
    counts["verified_ok"] = sum(1 for r in rows if r["status"] == "ok")
    counts["invalidated_at_report"] = sum(1 for r in rows
                                          if r["status"] == "invalidated")
    run_status = run_doc["status"]
    if counts["invalidated_at_report"]:
        run_status = "FAILED_CELLS"
    report = {
        "schema": SCHEMA_REPORT,
        "run": str(run_dir),
        "tier": run_doc["tier"],
        "run_status": run_status,
        "counts": counts,
        "cells": rows,
        "by_group": _by_group(run_doc, rows),
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
        elif args.command == "resume":
            run = resume_run(args.run_dir)
            print(json.dumps({"status": run["status"],
                              "counts": run["counts"],
                              "resume": run.get("resume")}, ensure_ascii=False))
        elif args.command == "report":
            report = report_run(args.run_dir)
            print(json.dumps({"status": "reported",
                              "run_status": report["run_status"],
                              "cells": len(report["cells"])},
                             ensure_ascii=False))
    except (SuiteError, BudgetExceeded) as exc:
        print(f"SUITE REFUSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
