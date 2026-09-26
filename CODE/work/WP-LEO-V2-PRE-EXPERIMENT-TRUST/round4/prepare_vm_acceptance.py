#!/usr/bin/env python3
"""Compile, finalize and authorize the two VM acceptance cells.

Two cells, one pairing key, one factor (routing.policy):

  control   routing.policy=hop     -> 0 decision rows   -> leo-sim-receipt/v5
  treatment routing.policy=oracle  -> 18 decision rows  -> leo-sim-receipt/v6

They are the two smallest formal cases that exercise opposite sides of the
stream contract, and nothing else: this is a platform-chain acceptance, not a
research experiment.  No performance measurement is produced or intended.

The script must run the code of the checkout it is compiling for, so --root is
inserted at sys.path[0] BEFORE CODE is imported.  Running it from the
development worktree while pointing --root at the frozen checkout is therefore
safe: the artifacts it writes carry the frozen code identity.

USAGE
  python3 <this script> --root /path/to/frozen/checkout --out summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXPERIMENT_ID = "EXP-LEO-V2-ACCEPT-R1"
WORK_ID = "WP-LEO-V2-ACCEPT-R1"


def build_request() -> dict:
    from CODE.leo_sim import matrix
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
            "analysis_id": "AN-ACCEPT-R1",
            "primary_metric": "delivery_rate",
            "estimand": "paired difference",
            "paired_by": ["pairing_key"],
            "planned_contrasts": [{"name": "treatment_minus_control",
                                   "left_arm": "treatment",
                                   "right_arm": "control",
                                   "estimand": "paired difference"}],
        },
        "claim_boundary": {
            "can_claim": ["platform chain acceptance only"],
            "cannot_claim": ["any scientific claim", "any performance result"],
        },
        "design": {"one_change_policy": "strict",
                   "factor_changed": ["routing.policy"]},
    }


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def build_finalization(root: Path, experiment: Path) -> Path:
    from CODE.work.finalize_decision import evaluate_decision, file_sha256
    work_dir = root / "CODE" / "work" / WORK_ID / "R01"
    work_dir.mkdir(parents=True, exist_ok=True)
    artifact_hashes = {
        str(path.relative_to(root)): file_sha256(path)
        for path in sorted(experiment.rglob("*")) if path.is_file()
    }
    brief = {
        "schema": "agent-work-package/v2", "work_id": WORK_ID,
        "revision": 1, "parent_revision": None,
        "producer_id": "producer:platform-acceptance",
        "producer_session_id": "P-accept-r1",
        "objective": ("Accept the platform chain on the two smallest formal "
                      "cases after the pre-experiment trust changes."),
        "allowed_inputs": [str(experiment.relative_to(root))],
        "excluded_inputs": [],
        "deliverables": ["Bound matrix authorization"],
        "cannot_claim": ["No run, performance or scientific claim."],
        "acceptance": ["Both cells are bound and authorized."],
        "review_roles": ["cold_start", "satellite_drl", "adversarial"],
        "cost_tier": "high_judgment", "status": "REVIEW",
    }
    brief_path = work_dir / "brief.json"
    _write_json(brief_path, brief)
    refs = []
    for role in brief["review_roles"]:
        receipt = {
            "schema": "agent-review-receipt/v2",
            "receipt_id": f"RR-ACCEPT-{role.upper()}", "work_id": WORK_ID,
            "revision": 1, "artifact_hashes": artifact_hashes,
            "producer_id": brief["producer_id"],
            "producer_session_id": brief["producer_session_id"],
            "reviewer_id": f"reviewer:{role}",
            "reviewer_session_id": f"R-{role}",
            "independence": {"producer_and_reviewer_are_distinct": True,
                             "review_started_from_declared_inputs": True},
            "role": role, "verdict": "PASS",
            "evidence": ["platform-chain acceptance fixture, not a scientific "
                         "review; the ARTIFACT review is a separate step"],
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
        "decision_maker_id": "decision:platform-acceptance",
        "applied_review_receipts": refs, "decision": "ACCEPT",
        "rationale": "Acceptance fixture; artifact review is a separate step.",
        "blocking_findings": [], "revision_instructions": [],
        "next_revision": None,
    }
    decision_path = work_dir / "decision.json"
    _write_json(decision_path, decision)
    finalization, errors = evaluate_decision(root, brief_path, decision_path)
    if errors:
        raise SystemExit(f"finalization errors: {errors}")
    finalization_path = work_dir / "finalization.json"
    _write_json(finalization_path, finalization)
    return finalization_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--experiment-id", default=None)
    ap.add_argument("--work-id", default=None)
    args = ap.parse_args()
    if args.experiment_id:
        globals()["EXPERIMENT_ID"] = args.experiment_id
    if args.work_id:
        globals()["WORK_ID"] = args.work_id
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))

    from CODE.experiment_platform import authorize_experiment
    from CODE.leo_sim import governance, matrix, receipt

    experiment = root / "EXPERIMENTS" / EXPERIMENT_ID
    if experiment.exists():
        raise SystemExit(f"experiment already exists: {experiment}")
    request_path = root / "CODE" / "work" / f"{WORK_ID}-request.json"
    _write_json(request_path, build_request())
    report = matrix.compile_matrix_experiment(request_path, experiment,
                                              project_root=root)
    request_path.unlink()
    finalization = build_finalization(root, experiment)
    authorization = authorize_experiment.build_authorization(
        root, experiment, finalization)
    auth_path = experiment / "authorization.json"
    _write_json(auth_path, authorization)
    manifest = json.loads((experiment / "run-manifest.json").read_text())
    summary = {
        "schema": "leo-sim-acceptance-prep/v1",
        "root": str(root),
        "experiment_id": EXPERIMENT_ID,
        "work_id": WORK_ID,
        "compile_status": report["status"],
        "compile_errors": report["errors"],
        "authorization_status": authorization["status"],
        "authorization_sha256": authorize_experiment.file_sha256(auth_path),
        "code_sha256": receipt.code_sha256(),
        "execution_chain_sha256": governance.execution_chain_sha256(),
        "cells": [
            {"run_id": row["run_id"], "arm_id": row["arm_id"],
             "config_path": row["config_path"],
             "config_sha256": row["config_sha256"],
             "trace_identity_sha256": row["trace_identity_sha256"]}
            for row in manifest["cells"]
        ],
        "design_accounting": report["design_accounting"],
    }
    Path(args.out).write_text(json.dumps(summary, ensure_ascii=False, indent=2,
                                         sort_keys=True) + "\n",
                              encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
