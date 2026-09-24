#!/usr/bin/env python3
"""Fail-closed predecessor gate for serial leo_sim V2 matrix launches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from CODE.experiment_platform import authorize_experiment, v2_analysis


def _inside(root: Path, path: Path, label: str) -> Path:
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise v2_analysis.V2AnalysisError(
            f"{label} must remain inside the project root") from exc
    return resolved


def _verify_deployment_commit(root: Path, expected: str) -> None:
    if (len(expected) != 40 or set(expected) == {"0"}
            or any(char not in "0123456789abcdef" for char in expected)):
        raise v2_analysis.V2AnalysisError(
            "deployed source commit must be non-zero lowercase full SHA")
    witness = root / ".deployment_commit"
    if witness.is_symlink() or not witness.is_file():
        raise v2_analysis.V2AnalysisError(
            "deployment commit witness is missing or unsafe")
    if witness.read_text(encoding="ascii") != expected + "\n":
        raise v2_analysis.V2AnalysisError(
            "deployment commit witness mismatch")


def verify_predecessors(
        root: Path, experiment_dir: Path, authorization_path: Path,
        next_run_id: str, *, results_root: Path | None = None,
        external_witness_root: Path | None = None,
        external_witness_by_nonce: bool = False,
        deployed_source_commit: str | None = None,
        expected_deployment: dict[str, Any] | None = None) -> list[str]:
    """Verify every earlier cell before allowing one serial matrix launch."""
    root = Path(root).resolve()
    experiment_dir = _inside(root, experiment_dir, "experiment directory")
    authorization_path = _inside(root, authorization_path, "authorization")
    if authorization_path.parent != experiment_dir:
        raise v2_analysis.V2AnalysisError(
            "authorization must be a direct child of the experiment directory")
    request = v2_analysis._read_json(experiment_dir / "request.json")
    policy = request.get("execution_policy")
    if policy is None:
        return []
    if policy != {"mode": "serial_fail_closed"}:
        raise v2_analysis.V2AnalysisError("serial gate execution policy is invalid")
    matrix = v2_analysis._read_json(experiment_dir / "run-manifest.json")
    analysis = v2_analysis._read_json(experiment_dir / "analysis-request.json")
    experiment_id = request.get("experiment_id")
    if matrix.get("schema") != v2_analysis.MATRIX_SCHEMA \
            or analysis.get("schema") != v2_analysis.ANALYSIS_SCHEMA \
            or matrix.get("experiment_id") != experiment_id \
            or analysis.get("experiment_id") != experiment_id:
        raise v2_analysis.V2AnalysisError("serial gate experiment identity mismatch")
    authorization: dict[str, Any] = authorize_experiment.verify_authorization(
        root, authorization_path)
    if authorization.get("status") != "AUTHORIZED" \
            or authorization.get("experiment_id") != experiment_id:
        raise v2_analysis.V2AnalysisError(
            "serial gate authorization identity mismatch")
    cells = matrix.get("cells")
    planned_ids = analysis.get("planned_run_ids")
    if not isinstance(cells, list) or planned_ids != [
            cell.get("run_id") for cell in cells if isinstance(cell, dict)]:
        raise v2_analysis.V2AnalysisError(
            "serial gate cohort does not match the analysis request")
    positions = [i for i, cell in enumerate(cells)
                 if cell.get("run_id") == next_run_id]
    if len(positions) != 1:
        raise v2_analysis.V2AnalysisError(
            "next run is not one exact matrix cell")
    authorized_rows = (authorization.get("authorized_cells")
                       or authorization.get("authorized_runs"))
    if not isinstance(authorized_rows, list):
        raise v2_analysis.V2AnalysisError(
            "serial gate authorization has no matrix cohort")
    authorization_sha = v2_analysis.file_sha256(authorization_path)
    auth_by_id = {
        row.get("run_id"): {**row, "authorization_sha256": authorization_sha}
        for row in authorized_rows if isinstance(row, dict)
    }
    if set(auth_by_id) != set(planned_ids):
        raise v2_analysis.V2AnalysisError(
            "serial gate authorization cohort differs from the matrix")
    predecessor_cells = cells[:positions[0]]
    if not predecessor_cells:
        return []
    primary = analysis.get("analysis", {}).get("primary_metric")
    if not isinstance(primary, str) or not primary:
        raise v2_analysis.V2AnalysisError(
            "serial gate primary metric is missing")
    results_root = _inside(
        root, results_root or root / "CODE" / "Results", "results root")
    external_witness_root = _inside(
        root, external_witness_root
        or results_root / "_external_launch_witness", "external witness root")
    if deployed_source_commit is None:
        v2_analysis._analyzer_identity()
    else:
        # The canonical VM deployment intentionally excludes .git.  Its
        # caller must first verify the signed deployment manifest/tree; this
        # additional witness prevents a caller from naming another commit.
        _verify_deployment_commit(root, deployed_source_commit)
    verified: list[str] = []
    for cell in predecessor_cells:
        run_id = cell["run_id"]
        v2_analysis._verify_result(
            root, results_root, external_witness_root, cell,
            auth_by_id[run_id], primary, require_external_witness=True,
            external_witness_by_nonce=external_witness_by_nonce,
            expected_deployment=expected_deployment)
        verified.append(run_id)
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--next-run-id", required=True)
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--external-witness-root", type=Path)
    args = parser.parse_args()
    try:
        verified = verify_predecessors(
            args.root, args.experiment, args.authorization, args.next_run_id,
            results_root=args.results_root,
            external_witness_root=args.external_witness_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SERIAL GATE BLOCKED: {exc}")
        return 2
    print(json.dumps({
        "status": "READY",
        "next_run_id": args.next_run_id,
        "verified_predecessors": verified,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
