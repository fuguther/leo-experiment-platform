#!/usr/bin/env python3
"""Formal-entry reproduction of the zero-decision and non-zero-decision cases.

WHY
===
The remote formal gate verifies a result directory with
remote_job.verify_v2_formal_result.  Two configurations exercise opposite sides
of that gate and both must pass it:

  * control  (routing.policy=hop)    -> 0 decision rows -> leo-sim-receipt/v5
  * treatment(routing.policy=oracle) -> 18 decision rows -> leo-sim-receipt/v6

The unconditional stream handover that this round replaced failed the FIRST of
those after the whole simulation had already run (measured 2026-09-25), and the
replacement must still recompute the second one's digests rather than trust
them.

WHAT IS REAL HERE AND WHAT IS RELOCATED
=======================================
Real: the matrix compile, the work finalization and authorization
(authorize_experiment.build_authorization, so the run carries
--authorization/--launch-nonce/--expect-run-id and emits formal_run.json), the
argv built by remote_job.formal_command, the run CLI, and the verification
function remote_job.verify_v2_formal_result that the VM itself calls.

Relocated: only the two canonical VM paths.  remote_job.CANONICAL_WORKSPACE and
CANONICAL_RESULTS point at this checkout and CODE/Results, because
/data/论文/leo-direct-sim does not exist on this host.  There is no VM: this
reproduces the GATE, it does not reproduce the deployment, the external launch
witness, or governance_receipt.json.

USAGE
  python3 CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/tools/repro_formal_streams.py \
      --out CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/evidence/formal-repro.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "CODE" / "scripts" / "remote"))

from CODE.experiment_platform import authorize_experiment  # noqa: E402
from CODE.leo_sim import matrix  # noqa: E402
from CODE.leo_sim import pullback as pullback_mod  # noqa: E402
from CODE.leo_sim import receipt as receipt_mod  # noqa: E402
from CODE.scripts.remote import remote_job as rj  # noqa: E402
from CODE.work.finalize_decision import (  # noqa: E402
    evaluate_decision,
    file_sha256,
)

EXPERIMENT_ID = "EXP-LEO-V2-PRE-TRUST-REPRO"
WORK_ID = "WP-LEO-V2-PRE-TRUST-REPRO"
LAUNCH_NONCE = "0" * 30 + "c9"  # exactly 32 lowercase hex characters


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def build_finalization(root: Path, experiment: Path) -> Path:
    """A review->decision->finalization chain for THIS work id.

    Deliberately not the shared test helper: that one hardcodes the work id
    "WP-MATRIX", and CODE/work/WP-MATRIX/ already exists in this checkout as
    untracked material that this round must not touch.  Same schema files, same
    evaluator, distinct work id and directory.
    """
    schema_root = root / "CODE" / "work"
    work_dir = schema_root / WORK_ID / "R01"
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact_hashes = {
        str(path.relative_to(root)): file_sha256(path)
        for path in sorted(experiment.rglob("*")) if path.is_file()
    }
    brief = {
        "schema": "agent-work-package/v2", "work_id": WORK_ID,
        "revision": 1, "parent_revision": None,
        "producer_id": "producer:pre-experiment-trust",
        "producer_session_id": "P-pre-trust-r1",
        "objective": "Reproduce the formal stream gate on both receipt forms.",
        "allowed_inputs": [str(experiment.relative_to(root))],
        "excluded_inputs": [],
        "deliverables": ["Bound matrix authorization"],
        "cannot_claim": ["No run or performance claim."],
        "acceptance": ["All matrix cells are bound."],
        "review_roles": ["cold_start", "satellite_drl", "adversarial"],
        "cost_tier": "high_judgment", "status": "REVIEW",
    }
    brief_path = work_dir / "brief.json"
    _write_json(brief_path, brief)
    refs = []
    for role in brief["review_roles"]:
        receipt = {
            "schema": "agent-review-receipt/v2",
            "receipt_id": f"RR-PRE-TRUST-{role.upper()}", "work_id": WORK_ID,
            "revision": 1, "artifact_hashes": artifact_hashes,
            "producer_id": brief["producer_id"],
            "producer_session_id": brief["producer_session_id"],
            "reviewer_id": f"reviewer:{role}",
            "reviewer_session_id": f"R-{role}",
            "independence": {"producer_and_reviewer_are_distinct": True,
                             "review_started_from_declared_inputs": True},
            "role": role, "verdict": "PASS",
            "evidence": ["fixture: gate reproduction, not a scientific review"],
            "blocking_findings": [], "unknowns": [], "required_revision": [],
        }
        receipt_path = work_dir / f"review-{role}.json"
        _write_json(receipt_path, receipt)
        refs.append({"receipt_id": receipt["receipt_id"],
                     "path": str(receipt_path.relative_to(root)),
                     "sha256": file_sha256(receipt_path), "role": role,
                     "verdict": "PASS"})
    decision = {
        "schema": "agent-work-decision/v1", "work_id": WORK_ID, "revision": 1,
        "producer_id": brief["producer_id"],
        "artifact_hashes": artifact_hashes,
        "decision_maker_id": "decision:pre-experiment-trust",
        "applied_review_receipts": refs, "decision": "ACCEPT",
        "rationale": "Fixture reviews bind the complete matrix.",
        "blocking_findings": [], "revision_instructions": [],
        "next_revision": None,
    }
    decision_path = work_dir / "decision.json"
    _write_json(decision_path, decision)
    finalization, errors = evaluate_decision(root, brief_path, decision_path)
    if errors:
        raise RuntimeError(f"finalization errors: {errors}")
    finalization_path = work_dir / "finalization.json"
    _write_json(finalization_path, finalization)
    return finalization_path

#: control -> hop (no deferred decisions); treatment -> oracle (18 decisions).
ARMS = {"control": "hop", "treatment": "oracle"}


def build_request() -> dict:
    return {
        "schema": matrix.MATRIX_REQUEST_SCHEMA,
        "experiment_id": EXPERIMENT_ID,
        "runtime_kind": "leo_sim_v2",
        "work_finalization": f"CODE/work/{WORK_ID}/R01/finalization.json",
        "common_config": {
            "scenario": {"duration_s": 40.0, "num_satellites": 8,
                         "num_planes": 1, "seed": 7},
            "endpoints": {"sites": [
                {"name": "a", "lat": 0.1, "lon": 0.1},
                {"name": "b", "lat": 52.6, "lon": 90.4}]},
            "demand": {"mode": "csv",
                       "csv_path": "CODE/data/traffic/t1_step5_micro_ab.csv"},
            "control_plane": {"enabled": False},
            "execution": {"available_capacity_interval_s": 0.1},
            "learning": {"algorithm": "none"},
        },
        "arms": [
            {"arm_id": "control", "config_overrides": {},
             "intervention_paths": []},
            {"arm_id": "treatment",
             "config_overrides": {"routing": {"policy": "oracle"}},
             "intervention_paths": ["routing.policy"]},
        ],
        "cells": [
            {"run_id": f"{EXPERIMENT_ID}-control-s7", "arm_id": "control",
             "phase": "non_learning", "trace_seed": 7, "learning_seed": None,
             "pairing_key": "pair-7", "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None,
                                     "source_sha256": None}},
            {"run_id": f"{EXPERIMENT_ID}-treatment-s7", "arm_id": "treatment",
             "phase": "non_learning", "trace_seed": 7, "learning_seed": None,
             "pairing_key": "pair-7", "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None,
                                     "source_sha256": None}},
        ],
        "acceptance": {"min_delivered_packets": 0, "min_multisat_deliveries": 0,
                       "require_data_isl": False,
                       "require_control_delivery": False},
        "analysis": {
            "analysis_id": "AN-REPRO",
            "primary_metric": "delivery_rate",
            "estimand": "paired difference",
            "paired_by": ["pairing_key"],
            "planned_contrasts": [{"name": "treatment_minus_control",
                                   "left_arm": "treatment",
                                   "right_arm": "control",
                                   "estimand": "paired difference"}],
        },
        "claim_boundary": {"can_claim": ["reproduction fixture"],
                           "cannot_claim": ["anything scientific"]},
        "design": {"one_change_policy": "strict",
                   "factor_changed": ["routing.policy"]},
    }


def nonblank(path: Path) -> int:
    if not path.is_file():
        return -1
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip())


def run_one(cell: dict, argv: list[str]) -> dict:
    run_dir = Path(argv[argv.index("--out") + 1])
    proc = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True)
    receipt = None
    receipt_path = run_dir / "receipt.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    old_form = []
    if receipt is not None:
        # EXACTLY what the replaced code did: hand over both paths
        # unconditionally.
        old_form = list(receipt_mod.verify_receipt_dir(
            str(run_dir),
            decision_log=str(run_dir / "decisions.jsonl"),
            timeline_log=str(run_dir / "timeline.jsonl")))
    return {
        "run_id": cell["run_id"],
        "arm": cell["arm_id"],
        "routing_policy": ARMS[cell["arm_id"]],
        "exit_code": proc.returncode,
        "stdout_tail": proc.stdout.strip().splitlines()[-1][:200]
        if proc.stdout.strip() else "",
        "stderr_tail": proc.stderr.strip()[-300:],
        "receipt_schema": None if receipt is None else receipt["schema"],
        "decision_rows": nonblank(run_dir / "decisions.jsonl"),
        "timeline_rows": nonblank(run_dir / "timeline.jsonl"),
        "vm_gate_errors_now": rj.verify_v2_formal_result(run_dir),
        "old_unconditional_stream_form_errors": old_form,
        # These runs carry formal_run.json but no governance receipt (no VM),
        # so they are declared formal and are expected to FAIL the formal
        # increment: the declaration is the caller's, never inferred.
        "pullback": pullback_mod.verify_pulled_run(run_dir, expect="formal"),
        "run_dir": str(run_dir),
    }


def tamper_checks(results: list[dict], scratch: Path) -> list[dict]:
    """Prove the gate RECOMPUTES rather than trusts, on each receipt form."""
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    checks = []
    for run in results:
        source = Path(run["run_dir"])
        schema = run["receipt_schema"]
        variants = {}
        if schema == "leo-sim-receipt/v6":
            variants["decision stream appended to"] = (
                lambda d: (d / "decisions.jsonl").write_bytes(
                    (d / "decisions.jsonl").read_bytes() + b'{"extra": 1}\n'))
            variants["timeline stream deleted"] = (
                lambda d: (d / "timeline.jsonl").unlink())
            variants["timeline stream rewritten"] = (
                lambda d: (d / "timeline.jsonl").write_text("{}\n",
                                                            encoding="utf-8"))
        else:
            variants["empty decision stream given a row"] = (
                lambda d: (d / "decisions.jsonl").write_text(
                    '{"pid": 1}\n', encoding="utf-8"))
            variants["decision stream deleted"] = (
                lambda d: (d / "decisions.jsonl").unlink())
            variants["timeline stream deleted"] = (
                lambda d: (d / "timeline.jsonl").unlink())
        for name, mutate in variants.items():
            target = scratch / f"{run['arm']}-{abs(hash(name)) % 10000}"
            shutil.copytree(source, target)
            mutate(target)
            errors = rj.verify_v2_formal_result(target)
            checks.append({
                "run_id": run["run_id"], "receipt_schema": schema,
                "corruption": name,
                "gate_errors": errors,
                "rejected": bool(errors),
            })
    return checks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rj.CANONICAL_WORKSPACE = REPO
    rj.CANONICAL_RESULTS = REPO / "CODE" / "Results"

    experiment = REPO / "EXPERIMENTS" / EXPERIMENT_ID
    if experiment.exists():
        shutil.rmtree(experiment)
    work_root = REPO / "CODE" / "work" / WORK_ID
    if work_root.exists():
        shutil.rmtree(work_root)
    source = REPO / "CODE" / "work" / "WP-LEO-V2-PRE-EXPERIMENT-TRUST" / \
        "_repro-request.json"
    source.write_text(json.dumps(build_request()), encoding="utf-8")

    report = matrix.compile_matrix_experiment(source, experiment,
                                              project_root=REPO)
    finalization = build_finalization(REPO, experiment)
    authorization = authorize_experiment.build_authorization(
        REPO, experiment, finalization)
    auth_path = experiment / "authorization.json"
    auth_path.write_text(json.dumps(authorization, sort_keys=True) + "\n",
                         encoding="utf-8")

    cells = {row["run_id"]: row
             for row in json.loads((experiment / "run-manifest.json").read_text())["cells"]}
    results = []
    for run_id in sorted(cells):
        cell = cells[run_id]
        # A formal --out must be a NEW empty directory; clear anything a
        # previous attempt left so the reproduction is repeatable.
        stale = rj.CANONICAL_RESULTS / run_id
        if stale.exists():
            shutil.rmtree(stale)
        config = experiment / cell["config_path"]
        argv = rj.formal_command(
            argparse.Namespace(runtime_kind="leo_sim_v2",
                               expected_run_id=run_id,
                               launch_nonce=LAUNCH_NONCE),
            REPO / "CODE", config, auth_path)
        results.append(run_one(cell, argv))

    tamper_scratch = (REPO / "CODE" / "work" / "WP-LEO-V2-PRE-EXPERIMENT-TRUST"
                      / "_tamper-scratch")
    checks = tamper_checks(results, tamper_scratch)
    shutil.rmtree(tamper_scratch, ignore_errors=True)

    payload = {
        "schema": "leo-sim-formal-stream-repro/v1",
        "relocated_constants": {
            "CANONICAL_WORKSPACE": str(rj.CANONICAL_WORKSPACE),
            "CANONICAL_RESULTS": str(rj.CANONICAL_RESULTS),
            "note": ("the VM path /data/论文/leo-direct-sim does not exist on "
                     "this host; only these two constants are relocated, and "
                     "there is no VM, no deployment and no external launch "
                     "witness here"),
        },
        "compile": {"status": report["status"], "errors": report["errors"],
                    "design_accounting": report["design_accounting"]},
        "authorization": {"status": authorization["status"],
                          "sha256_of_file": __import__("hashlib").sha256(
                              auth_path.read_bytes()).hexdigest()},
        "runs": results,
        "tamper_checks": checks,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    for run in results:
        print(json.dumps({k: run[k] for k in (
            "run_id", "routing_policy", "exit_code", "receipt_schema",
            "decision_rows", "timeline_rows", "vm_gate_errors_now",
            "old_unconditional_stream_form_errors")}, ensure_ascii=False))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
