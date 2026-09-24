#!/usr/bin/env python3
"""Issue and re-verify execution authorization for one reviewed experiment.

An authorization is derived evidence, not an approval string.  Issuance and
runtime verification both recompute the underlying work finalization, receipt
hashes, experiment artifact hashes, manifest identities, and canonical config
hashes.  Any changed or missing input fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from CODE.work.finalize_decision import evaluate_decision, file_sha256, load_json, project_path
from CODE.experiment_platform.compile_experiment import EXECUTION_BOUNDARY


SCHEMA = "experiment-execution-authorization/v1"
REQUIRED_EXPERIMENT_REVIEW_ROLES = {"cold_start", "satellite_drl", "adversarial"}


class AuthorizationError(ValueError):
    """The requested authorization cannot be proven from current artifacts."""


def canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def relative_project_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise AuthorizationError(f"path is outside project root: {path}") from exc


def _experiment_path(experiment_dir: Path, raw: str) -> Path:
    lexical = experiment_dir / raw
    current = lexical
    while current != experiment_dir and current != current.parent:
        if current.is_symlink():
            raise AuthorizationError(
                f"experiment artifact path contains a symbolic link: {raw}")
        current = current.parent
    candidate = lexical.resolve()
    try:
        candidate.relative_to(experiment_dir.resolve())
    except ValueError as exc:
        raise AuthorizationError(f"experiment artifact escapes experiment directory: {raw}") from exc
    return candidate


def _load_verified_finalization(root: Path, finalization_path: Path) -> dict[str, Any]:
    finalization_path = finalization_path.resolve()
    finalization = load_json(finalization_path)
    if finalization.get("schema") != "agent-work-finalization/v1":
        raise AuthorizationError("unsupported work finalization schema")
    brief_raw = finalization.get("brief_path")
    decision_raw = finalization.get("decision_path")
    if not isinstance(brief_raw, str) or not isinstance(decision_raw, str):
        raise AuthorizationError("work finalization lacks brief_path or decision_path")
    try:
        brief_path = project_path(root, brief_raw)
        decision_path = project_path(root, decision_raw)
    except (TypeError, ValueError) as exc:
        raise AuthorizationError("work finalization references a path outside the project") from exc
    recomputed, errors = evaluate_decision(root, brief_path, decision_path)
    if errors or recomputed is None:
        raise AuthorizationError("work decision no longer validates: " + "; ".join(errors))
    if recomputed != finalization:
        raise AuthorizationError("work finalization does not exactly match recomputed decision evidence")
    if recomputed.get("status") != "ACCEPTED":
        raise AuthorizationError("work revision is not ACCEPTED")
    required_roles = set(recomputed.get("required_review_roles", []))
    missing_roles = sorted(REQUIRED_EXPERIMENT_REVIEW_ROLES - required_roles)
    if missing_roles:
        raise AuthorizationError(
            "experiment authorization requires cold_start, satellite_drl, and adversarial reviews; "
            f"missing {missing_roles}"
        )
    return finalization


def _verified_experiment(
    root: Path,
    experiment_dir: Path,
) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    experiment_dir = experiment_dir.resolve()
    relative_project_path(root, experiment_dir)
    fixed = ("request.json", "compile-report.json", "run-manifest.json", "analysis-request.json")
    docs: dict[str, dict[str, Any]] = {}
    for name in fixed:
        path = _experiment_path(experiment_dir, name)
        if not path.is_file():
            raise AuthorizationError(f"missing compiled experiment artifact: {name}")
        docs[name] = load_json(path)
    runbook_path = _experiment_path(experiment_dir, "RUNBOOK.md")
    if not runbook_path.is_file():
        raise AuthorizationError("missing compiled experiment artifact: RUNBOOK.md")

    request = docs["request.json"]
    report = docs["compile-report.json"]
    manifest = docs["run-manifest.json"]
    analysis = docs["analysis-request.json"]
    if manifest.get("schema") == "leo-sim-experiment-matrix-manifest/v1":
        return _verified_leo_sim_v2_matrix_experiment(root, experiment_dir)
    if manifest.get("schema") == "leo-sim-experiment-run-manifest/v1":
        return _verified_leo_sim_v2_experiment(
            root, experiment_dir, docs, runbook_path)
    experiment_id = request.get("identity", {}).get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.startswith("EXP-"):
        raise AuthorizationError("request has no valid experiment_id")
    if report.get("schema") != "experiment-compile-report/v2":
        raise AuthorizationError("unsupported compile report schema")
    if report.get("status") != "COMPILED_REVIEW_REQUIRED" or report.get("errors") != []:
        raise AuthorizationError("compile report is not a clean review-required build")
    if report.get("execution_authorized") is not False or report.get("launcher_generated") is not False:
        raise AuthorizationError("compile report has invalid pre-authorization state")
    request_path = experiment_dir / "request.json"
    if report.get("request_sha256") != file_sha256(request_path):
        raise AuthorizationError("compile report request hash mismatch")
    if manifest.get("schema") != "experiment-run-manifest/v2":
        raise AuthorizationError("unsupported run manifest schema")
    if manifest.get("experiment_id") != experiment_id:
        raise AuthorizationError("manifest experiment_id mismatch")
    if manifest.get("request_sha256") != report.get("request_sha256"):
        raise AuthorizationError("manifest request hash mismatch")
    if manifest.get("execution_authorized") is not False:
        raise AuthorizationError("compiled manifest must remain unauthorized")
    if manifest.get("execution_boundary") != EXECUTION_BOUNDARY:
        raise AuthorizationError("manifest execution boundary is not canonical")
    if analysis.get("schema") != "analysis-request/v2" or analysis.get("experiment_id") != experiment_id:
        raise AuthorizationError("analysis request does not match experiment")
    if analysis.get("request_sha256") != report.get("request_sha256"):
        raise AuthorizationError("analysis request does not bind request.json")
    if analysis.get("run_manifest_sha256") != file_sha256(experiment_dir / "run-manifest.json"):
        raise AuthorizationError("analysis request does not bind run-manifest.json")
    if analysis.get("scenario_identity_sha256") != canonical_sha(manifest.get("scenario_identity", {})):
        raise AuthorizationError("analysis request scenario identity mismatch")
    scenario = manifest.get("scenario_identity")
    source_hashes = scenario.get("source_and_input_sha256") if isinstance(scenario, dict) else None
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise AuthorizationError("scenario identity lacks source_and_input_sha256")
    for raw, expected_digest in sorted(source_hashes.items()):
        if not isinstance(raw, str) or not isinstance(expected_digest, str):
            raise AuthorizationError("scenario source hash map is malformed")
        try:
            source_path = project_path(root, raw)
        except (TypeError, ValueError) as exc:
            raise AuthorizationError(f"scenario source escapes project: {raw}") from exc
        if not source_path.is_file() or file_sha256(source_path) != expected_digest:
            raise AuthorizationError(f"scenario source changed or is missing: {raw}")

    planned_runs = manifest.get("planned_runs")
    if not isinstance(planned_runs, list) or not planned_runs:
        raise AuthorizationError("manifest has no planned runs")
    artifact_paths = {*fixed, "RUNBOOK.md"}
    authorized_runs: list[dict[str, Any]] = []
    seen_run_ids: set[str] = set()
    seen_config_paths: set[str] = set()
    for row in planned_runs:
        if not isinstance(row, dict):
            raise AuthorizationError("planned run must be an object")
        run_id = row.get("run_id")
        config_json_raw = row.get("config_json")
        expected_config_sha = row.get("config_sha256")
        if not all(isinstance(item, str) and item for item in (run_id, config_json_raw)):
            raise AuthorizationError("planned run lacks run_id or canonical config path")
        if Path(config_json_raw).suffix.lower() != ".json":
            raise AuthorizationError(f"planned run canonical config must be JSON: {run_id}")
        if run_id in seen_run_ids:
            raise AuthorizationError(f"duplicate planned run_id: {run_id}")
        if config_json_raw in seen_config_paths:
            raise AuthorizationError("planned runs reuse a config path")
        seen_run_ids.add(run_id)
        seen_config_paths.add(config_json_raw)
        config_json_path = _experiment_path(experiment_dir, config_json_raw)
        if not config_json_path.is_file():
            raise AuthorizationError(f"planned run config is missing: {run_id}")
        config = load_json(config_json_path)
        if canonical_sha(config) != expected_config_sha:
            raise AuthorizationError(f"canonical config hash mismatch: {run_id}")
        provenance = config.get("provenance")
        if not isinstance(provenance, dict):
            raise AuthorizationError(f"resolved config lacks provenance: {run_id}")
        exact = {
            "experiment_id": experiment_id,
            "run_id": run_id,
            "arm_id": row.get("arm_id"),
            "seed": row.get("seed"),
        }
        for key, expected in exact.items():
            if provenance.get(key) != expected:
                raise AuthorizationError(f"resolved config provenance mismatch for {run_id}: {key}")
        if provenance.get("execution_boundary") != EXECUTION_BOUNDARY:
            raise AuthorizationError(f"resolved config execution boundary mismatch for {run_id}")
        if provenance.get("execution_semantics") != row.get("execution_semantics"):
            raise AuthorizationError(f"resolved config execution semantics mismatch for {run_id}")
        artifact_paths.add(config_json_raw)
        authorized_runs.append({
            "run_id": run_id,
            "config_json": relative_project_path(root, config_json_path),
            "config_sha256": expected_config_sha,
        })

    if analysis.get("planned_run_ids") != [row["run_id"] for row in planned_runs]:
        raise AuthorizationError("analysis planned_run_ids do not exactly match manifest order")
    projected = [
        {key: row.get(key) for key in ("run_id", "arm_id", "seed", "config_sha256", "controlled_signature")}
        for row in planned_runs
    ]
    if analysis.get("planned_runs") != projected:
        raise AuthorizationError("analysis planned_runs do not bind the manifest cohort and config identities")
    artifact_hashes = {
        relative_project_path(root, _experiment_path(experiment_dir, raw)): file_sha256(
            _experiment_path(experiment_dir, raw)
        )
        for raw in sorted(artifact_paths)
    }
    return experiment_id, artifact_hashes, authorized_runs


def _verified_leo_sim_v2_matrix_experiment(
    root: Path,
    experiment_dir: Path,
) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    """Verify a V2 matrix as one exact planned-cell cohort.

    Matrix verification is intentionally delegated to the dedicated contract
    module.  This branch is selected only by its new manifest schema, so the
    retained single-run V2 and generic legacy V2 paths remain unchanged.
    """
    from CODE.leo_sim import matrix as v2_matrix

    try:
        return v2_matrix.verify_compiled_matrix(root, experiment_dir)
    except (v2_matrix.MatrixError, OSError, json.JSONDecodeError, KeyError,
            TypeError, ValueError) as exc:
        raise AuthorizationError(f"invalid leo_sim V2 matrix: {exc}") from exc


def _verified_leo_sim_v2_experiment(
    root: Path,
    experiment_dir: Path,
    docs: dict[str, dict[str, Any]],
    runbook_path: Path,
) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    """Verify the dedicated V2 compile contract without legacy translation."""
    from CODE.leo_sim import config as v2_config
    from CODE.leo_sim import governance as v2_governance
    from CODE.leo_sim import receipt as v2_receipt

    request = docs["request.json"]
    report = docs["compile-report.json"]
    manifest = docs["run-manifest.json"]
    analysis = docs["analysis-request.json"]
    experiment_id = request.get("experiment_id")
    if set(request) != {
            "schema", "experiment_id", "runtime_kind", "work_finalization",
            "acceptance", "config",
    } \
            or request.get("schema") != v2_governance.REQUEST_SCHEMA \
            or request.get("runtime_kind") != v2_governance.RUNTIME_KIND \
            or not isinstance(request.get("config"), dict):
        raise AuthorizationError("unsupported leo_sim V2 request schema")
    if not isinstance(experiment_id, str) or not experiment_id.startswith("EXP-"):
        raise AuthorizationError("V2 request has no valid experiment_id")
    request_path = experiment_dir / "request.json"
    request_sha = file_sha256(request_path)
    if set(report) != {
            "schema", "status", "runtime_kind", "experiment_id", "errors",
            "request_sha256", "execution_authorized", "launcher_generated",
            "artifact_hashes",
    } or report.get("schema") != v2_governance.COMPILE_REPORT_SCHEMA \
            or report.get("status") != "COMPILED_REVIEW_REQUIRED" \
            or report.get("errors") != []:
        raise AuthorizationError("V2 compile report is not a clean review-required build")
    if report.get("runtime_kind") != v2_governance.RUNTIME_KIND \
            or report.get("experiment_id") != experiment_id \
            or report.get("execution_authorized") is not False \
            or report.get("launcher_generated") is not False \
            or report.get("request_sha256") != request_sha:
        raise AuthorizationError("V2 compile report identity/state mismatch")
    if set(manifest) != {
            "schema", "runtime_kind", "experiment_id", "request_sha256",
            "execution_authorized", "planned_runs",
    } or manifest.get("schema") != v2_governance.RUN_MANIFEST_SCHEMA \
            or manifest.get("runtime_kind") != v2_governance.RUNTIME_KIND \
            or manifest.get("experiment_id") != experiment_id \
            or manifest.get("request_sha256") != request_sha \
            or manifest.get("execution_authorized") is not False:
        raise AuthorizationError("V2 run manifest identity/state mismatch")
    if set(analysis) != {
            "schema", "runtime_kind", "experiment_id", "request_sha256",
            "run_manifest_sha256", "planned_run_ids", "comparison_contract",
    } or analysis.get("schema") != v2_governance.ANALYSIS_REQUEST_SCHEMA \
            or analysis.get("runtime_kind") != v2_governance.RUNTIME_KIND \
            or analysis.get("experiment_id") != experiment_id \
            or analysis.get("request_sha256") != request_sha \
            or analysis.get("run_manifest_sha256") != file_sha256(
                experiment_dir / "run-manifest.json") \
            or analysis.get("comparison_contract") != (
                "same trace identity, seed and resource config"):
        raise AuthorizationError("V2 analysis request does not bind the manifest")
    planned = manifest.get("planned_runs")
    if not isinstance(planned, list) or len(planned) != 1 \
            or analysis.get("planned_run_ids") != [planned[0].get("run_id")]:
        raise AuthorizationError("V2 manifest must contain one analysis-bound run")
    row = planned[0]
    if not isinstance(row, dict) or set(row) != {
            "run_id", "runtime_kind", "config_path", "config_sha256",
            "trace_identity_sha256", "input_sha256", "code_sha256",
            "execution_chain_sha256", "acceptance", "seed",
    } or row.get("runtime_kind") != v2_governance.RUNTIME_KIND:
        raise AuthorizationError("V2 planned run runtime_kind mismatch")
    run_id = row.get("run_id")
    config_raw = row.get("config_path")
    if not isinstance(run_id, str) or not run_id \
            or not isinstance(config_raw, str) or not config_raw:
        raise AuthorizationError("V2 planned run lacks run_id/config path")
    config_path = _experiment_path(experiment_dir, config_raw)
    if config_path.is_symlink() or not config_path.is_file():
        raise AuthorizationError("V2 planned config is missing or symbolic")
    try:
        resolved = v2_config.load_config_file(str(config_path))
    except Exception as exc:
        raise AuthorizationError(f"V2 planned config invalid: {exc}") from exc
    if resolved["sha256"] != row.get("config_sha256"):
        raise AuthorizationError("V2 config SHA mismatch")
    try:
        request_intent = v2_governance.build_run_intent({
            "runtime_kind": request["runtime_kind"],
            "config": request["config"],
        }, project_root=root)
    except Exception as exc:
        raise AuthorizationError(f"V2 request cannot be resolved: {exc}") from exc
    expected_run_id = (
        f"{experiment_id}-main-s"
        f"{request_intent['resolved']['config']['scenario']['seed']}")
    if resolved["config"] != request_intent["resolved"]["config"] \
            or resolved["sha256"] != request_intent["config_sha256"] \
            or run_id != expected_run_id \
            or config_raw != f"resolved/{expected_run_id}.leo-sim.yaml":
        raise AuthorizationError(
            "V2 compiled config/run identity does not derive from request.json")
    current_intent = v2_governance.build_run_intent({
        "runtime_kind": v2_governance.RUNTIME_KIND,
        "config": resolved["config"],
    }, project_root=root)
    input_sha = row.get("input_sha256", "")
    expected_trace_identity = current_intent["trace_identity_sha256"]
    if input_sha != current_intent["input_sha256"] \
            or row.get("trace_identity_sha256") != expected_trace_identity:
        raise AuthorizationError("V2 trace identity mismatch")
    if row.get("code_sha256") != v2_receipt.code_sha256():
        raise AuthorizationError("V2 runtime code changed after compilation")
    if row.get("execution_chain_sha256") != v2_governance.execution_chain_sha256():
        raise AuthorizationError(
            "V2 authorization/deployment/launch chain changed after compilation")
    if row.get("seed") != resolved["config"]["scenario"]["seed"]:
        raise AuthorizationError("V2 planned seed mismatch")
    if row.get("acceptance") != request.get("acceptance"):
        raise AuthorizationError("V2 planned acceptance mismatch")
    artifact_paths = {
        "request.json", "compile-report.json", "run-manifest.json",
        "analysis-request.json", "RUNBOOK.md", config_raw,
    }
    report_bound_paths = artifact_paths - {"compile-report.json"}
    expected_report_hashes = {
        raw: file_sha256(_experiment_path(experiment_dir, raw))
        for raw in sorted(report_bound_paths)
    }
    if report.get("artifact_hashes") != expected_report_hashes:
        raise AuthorizationError(
            "V2 compile report artifact hash map is incomplete or stale")
    artifact_hashes = {
        relative_project_path(root, _experiment_path(experiment_dir, raw)):
            file_sha256(_experiment_path(experiment_dir, raw))
        for raw in sorted(artifact_paths)
    }
    authorized_runs = [{
        "run_id": run_id,
        "runtime_kind": v2_governance.RUNTIME_KIND,
        "config_path": relative_project_path(root, config_path),
        "config_sha256": resolved["sha256"],
        "trace_identity_sha256": expected_trace_identity,
        "code_sha256": row["code_sha256"],
        "execution_chain_sha256": row["execution_chain_sha256"],
        "acceptance": row["acceptance"],
    }]
    return experiment_id, artifact_hashes, authorized_runs


def build_authorization(root: Path, experiment_dir: Path, finalization_path: Path) -> dict[str, Any]:
    root = root.resolve()
    experiment_dir = experiment_dir.resolve()
    finalization_path = finalization_path.resolve()
    finalization = _load_verified_finalization(root, finalization_path)
    experiment_id, artifact_hashes, authorized_runs = _verified_experiment(root, experiment_dir)
    request = load_json(experiment_dir / "request.json")
    if request.get("runtime_kind") == "leo_sim_v2" \
            and request.get("work_finalization") != relative_project_path(
                root, finalization_path):
        raise AuthorizationError(
            "V2 request work_finalization does not match the supplied finalization")
    bound = finalization.get("artifact_hashes")
    if not isinstance(bound, dict):
        raise AuthorizationError("work finalization has no artifact hash map")
    missing_or_changed = [
        path for path, digest in artifact_hashes.items() if bound.get(path) != digest
    ]
    if missing_or_changed:
        raise AuthorizationError(
            "accepted work revision does not bind current compiled artifacts: "
            + ", ".join(missing_or_changed)
        )
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "AUTHORIZED",
        "experiment_id": experiment_id,
        "experiment_dir": relative_project_path(root, experiment_dir),
        "work_finalization": {
            "path": relative_project_path(root, finalization_path),
            "sha256": file_sha256(finalization_path),
            "work_id": finalization.get("work_id"),
            "revision": finalization.get("revision"),
            "brief_path": finalization.get("brief_path"),
            "brief_sha256": finalization.get("brief_sha256"),
            "decision_path": finalization.get("decision_path"),
            "decision_sha256": finalization.get("decision_sha256"),
        },
        "experiment_artifact_hashes": artifact_hashes,
        "authorized_runs": authorized_runs,
        "verification_policy": {
            "recompute_work_finalization": True,
            "require_all_bound_artifact_hashes": True,
            "require_exact_run_config_hash": True,
            "require_current_scenario_source_hashes": True,
            "require_current_trace_input_hashes": any(
                row.get("runtime_kind") == "leo_sim_v2"
                for row in authorized_runs),
        },
    }
    if request.get("schema") == "leo-sim-experiment-matrix-request/v1":
        # The matrix is an atomic authorization cohort: exposing the same
        # exact rows separately makes the all-cells binding auditable without
        # weakening the legacy authorized_runs consumer contract.
        payload["authorized_cells"] = list(authorized_runs)
        payload["verification_policy"]["require_exact_matrix_cohort"] = True
    payload["payload_sha256"] = canonical_sha(payload)
    return payload


def verify_authorization(root: Path, authorization_path: Path) -> dict[str, Any]:
    root = root.resolve()
    authorization_path = authorization_path.resolve()
    relative_project_path(root, authorization_path)
    authorization = load_json(authorization_path)
    if authorization.get("schema") != SCHEMA:
        raise AuthorizationError("unsupported authorization schema")
    claimed_payload_sha = authorization.get("payload_sha256")
    unsigned = dict(authorization)
    unsigned.pop("payload_sha256", None)
    if claimed_payload_sha != canonical_sha(unsigned):
        raise AuthorizationError("authorization payload hash mismatch")
    experiment_raw = authorization.get("experiment_dir")
    finalization_raw = authorization.get("work_finalization", {}).get("path")
    if not isinstance(experiment_raw, str) or not isinstance(finalization_raw, str):
        raise AuthorizationError("authorization lacks experiment or finalization path")
    try:
        experiment_dir = project_path(root, experiment_raw)
        finalization_path = project_path(root, finalization_raw)
    except (TypeError, ValueError) as exc:
        raise AuthorizationError("authorization references a path outside the project") from exc
    if not finalization_path.is_file():
        raise AuthorizationError("authorization finalization file is missing")
    expected = build_authorization(root, experiment_dir, finalization_path)
    if expected != authorization:
        raise AuthorizationError("authorization no longer matches recomputed evidence")
    return authorization


def verify_authorization_for_config(
    root: Path,
    authorization_path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    authorization = verify_authorization(root, authorization_path)
    provenance = config.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise AuthorizationError("real execution requires platform provenance")
    run_id = provenance.get("run_id")
    experiment_id = provenance.get("experiment_id")
    config_sha = canonical_sha(config)
    matches = [
        row for row in authorization.get("authorized_runs", [])
        if row.get("run_id") == run_id and row.get("config_sha256") == config_sha
    ]
    if experiment_id != authorization.get("experiment_id") or len(matches) != 1:
        raise AuthorizationError("config is not one exact run authorized by this receipt")
    return authorization


def verify_authorization_for_leo_sim_v2_config(
    root: Path,
    authorization_path: Path,
    config_path: Path,
    expected_run_id: str,
) -> dict[str, Any]:
    """Recompute a V2 authorization and bind one exact config + run id."""
    from CODE.leo_sim import config as v2_config
    from CODE.leo_sim import governance as v2_governance
    from CODE.leo_sim import receipt as v2_receipt

    authorization = verify_authorization(root, authorization_path)
    config_path = Path(config_path)
    if config_path.is_symlink() or not config_path.is_file():
        raise AuthorizationError("V2 runtime config is missing or symbolic")
    resolved = v2_config.load_config_file(str(config_path))
    config_project_path = relative_project_path(root, config_path)
    matches = [
        row for row in authorization.get("authorized_runs", [])
        if row.get("run_id") == expected_run_id
        and row.get("runtime_kind") == v2_governance.RUNTIME_KIND
        and row.get("config_path") == config_project_path
        and row.get("config_sha256") == resolved["sha256"]
        and row.get("code_sha256") == v2_receipt.code_sha256()
        and row.get("execution_chain_sha256") == (
            v2_governance.execution_chain_sha256())
    ]
    if len(matches) != 1:
        raise AuthorizationError(
            "config is not one exact leo_sim_v2 run authorized by this receipt")
    return authorization


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--finalization", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    experiment = args.experiment if args.experiment.is_absolute() else root / args.experiment
    finalization = args.finalization if args.finalization.is_absolute() else root / args.finalization
    out = args.out if args.out.is_absolute() else root / args.out
    try:
        relative_project_path(root, out)
        authorization = build_authorization(root, experiment.resolve(), finalization.resolve())
    except (AuthorizationError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"BLOCK: {exc}", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(authorization, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"AUTHORIZED: {authorization['experiment_id']} ({len(authorization['authorized_runs'])} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
