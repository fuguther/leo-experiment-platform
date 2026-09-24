#!/usr/bin/env python3
"""Fail-closed paired analysis for compiled leo_sim experiment artifacts.

The analyzer is deliberately small and boring.  It does not infer run
identity from directory names, fill missing metrics, or turn a successful
process exit into evidence.  Every run is admitted only after the compiled
manifest, run metadata, config bytes, artifact manifest and summary metrics
agree.  The persisted analysis manifest is then a reproducible claim-bound
artifact for :mod:`PAPER.eligible_claims`.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Iterable

# The runbook invokes this file by path from the project root. Make that
# invocation independent of an ambient PYTHONPATH while keeping imports
# rooted at the exact project copy being analyzed.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from CODE.experiment_platform.authorize_experiment import (
    AuthorizationError,
    verify_authorization,
)


ANALYSIS_SCHEMA = "analysis-request/v2"
MANIFEST_SCHEMA = "experiment-run-manifest/v2"
OUTPUT_SCHEMA = "analysis-manifest/v1"
RECEIPT_SCHEMA = "leo-effective-receipt/v1"
ARTIFACT_SCHEMA = "artifact-manifest/v1"
PERSISTED_ANALYSIS_FIELDS = {
    "schema", "status", "errors", "experiment_id", "analysis_id",
    "primary_metric", "paired_by", "verified_run_ids",
    "bound_run_artifacts", "planned_contrasts", "input_hashes", "inputs",
    "claim_boundary", "analysis_request_path", "run_manifest_path",
    "authorization_path", "results_root", "authorization_sha256",
    "run_entries", "output_hashes", "output_artifacts",
}
RECOMPUTED_ANALYSIS_FIELDS = (
    "schema", "status", "errors", "experiment_id", "analysis_id",
    "primary_metric", "paired_by", "verified_run_ids",
    "bound_run_artifacts", "planned_contrasts", "claim_boundary",
    "analysis_request_path", "run_manifest_path", "authorization_path",
    "results_root", "authorization_sha256", "run_entries",
)


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or symbolic JSON artifact: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable JSON artifact {path}: {exc}") from exc


def _safe_child(root: Path, raw: str, label: str) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
        raise ValueError(f"{label} must be a non-empty relative path")
    root = root.resolve()
    candidate = root / raw
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} escapes its root or is missing: {raw}") from exc
    # Every component is checked.  A symlink that happens to resolve inside
    # the root is still not a reproducible run artifact.
    cursor = root
    for part in Path(raw).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"{label} contains a symbolic link: {raw}")
    return resolved


def _lexical_direct_run(path: Path, results_root: Path, label: str) -> Path:
    """Reject an entry symlink before resolving it.

    Production runs are direct children of CODE/Results.  Tests may inject a
    different explicit results_root, but the direct-child and lexical
    non-symlink contract remains identical.
    """
    lexical = Path(path).absolute()
    root = Path(results_root).absolute()
    if lexical.is_symlink() or not lexical.is_dir():
        raise ValueError(f"{label} must be an existing lexical non-symlink directory")
    if lexical.parent != root or lexical.name.startswith("_"):
        raise ValueError(f"{label} must be a direct non-control child of {root}")
    return lexical


def _safe_artifact(root: Path, raw: str, label: str) -> Path:
    return _safe_child(root, raw, label)


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _load_metrics(path: Path, required: Iterable[str]) -> dict[str, float]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"summary metrics missing or symbolic: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["metric", "value"]:
            raise ValueError("summary_metrics.csv must have exactly metric,value columns")
        metrics: dict[str, float] = {}
        for row_number, row in enumerate(reader, start=2):
            metric = row.get("metric")
            raw_value = row.get("value")
            if not isinstance(metric, str) or not metric.strip():
                raise ValueError(f"summary metrics row {row_number} has an empty metric")
            if metric in metrics:
                raise ValueError(f"summary metrics contains duplicate metric: {metric}")
            try:
                value = float(raw_value)  # type: ignore[arg-type]
            except (TypeError, ValueError) as exc:
                raise ValueError(f"summary metric {metric} is not numeric") from exc
            if not math.isfinite(value):
                raise ValueError(f"summary metric {metric} is not finite")
            metrics[metric] = value
    missing = sorted(set(required) - set(metrics))
    if missing:
        raise ValueError(f"summary metrics missing required fields: {missing}")
    return metrics


def _artifact_entries(run_dir: Path, artifact_manifest: dict[str, Any], required: list[str]) -> list[dict[str, Any]]:
    if artifact_manifest.get("schema") != ARTIFACT_SCHEMA:
        raise ValueError("artifact manifest schema mismatch")
    entries = artifact_manifest.get("artifacts")
    if not isinstance(entries, list):
        raise ValueError("artifact manifest artifacts must be a list")
    if artifact_manifest.get("required_artifacts") != required:
        raise ValueError("artifact manifest required set differs from compiled config")
    seen: set[str] = set()
    checked: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict) or set(item) not in ({"path", "size", "sha256"}, {"path", "size", "sha256", "schema"}):
            raise ValueError("artifact manifest entry must contain path,size,sha256")
        raw = item["path"]
        if not isinstance(raw, str) or raw in seen or raw == "artifact_manifest.json":
            raise ValueError("artifact manifest has duplicate, invalid or self entry")
        seen.add(raw)
        path = _safe_artifact(run_dir, raw, "run artifact")
        if not isinstance(item["size"], int) or item["size"] < 0:
            raise ValueError(f"artifact size is invalid: {raw}")
        digest = item["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or file_sha256(path) != digest:
            raise ValueError(f"artifact hash mismatch: {raw}")
        if path.stat().st_size != item["size"]:
            raise ValueError(f"artifact size mismatch: {raw}")
        checked.append({"path": raw, "size": item["size"], "sha256": digest})
    expected = set(required) - {"artifact_manifest.json"}
    if seen != expected:
        raise ValueError(f"artifact manifest does not cover exact required set: missing={sorted(expected-seen)} extra={sorted(seen-expected)}")
    return checked


def _planned_row(manifest: dict[str, Any], run_id: str) -> dict[str, Any]:
    rows = manifest.get("planned_runs")
    if not isinstance(rows, list):
        raise ValueError("run manifest planned_runs must be a list")
    matches = [row for row in rows if isinstance(row, dict) and row.get("run_id") == run_id]
    if len(matches) != 1:
        raise ValueError(f"run id is not unique in planned cohort: {run_id}")
    return matches[0]


def _verify_compiled_binding(
    root: Path,
    analysis: dict[str, Any],
    run_manifest: dict[str, Any],
    request_path: Path,
    manifest_path: Path,
    authorization_path: Path,
) -> tuple[dict[str, Any], str, dict[str, dict[str, Any]]]:
    """Recompute the compile/review/authorization binding before admitting runs."""
    request = _read_json(request_path)
    analysis_file_sha = file_sha256(request_path)
    manifest_file_sha = file_sha256(manifest_path)
    if analysis.get("request_sha256") != analysis_file_sha:
        raise ValueError("analysis request does not bind request.json")
    if analysis.get("run_manifest_sha256") != manifest_file_sha:
        raise ValueError("analysis request does not bind run-manifest.json")
    if analysis.get("scenario_identity_sha256") != canonical_sha(run_manifest.get("scenario_identity", {})):
        raise ValueError("analysis request scenario identity binding mismatch")
    if request.get("identity", {}).get("experiment_id") != run_manifest.get("experiment_id"):
        raise ValueError("request and run manifest experiment identity mismatch")
    if analysis.get("experiment_id") != run_manifest.get("experiment_id"):
        raise ValueError("analysis and run manifest experiment identity mismatch")
    planned = run_manifest.get("planned_runs")
    if not isinstance(planned, list) or not planned:
        raise ValueError("run manifest planned_runs is missing")
    planned_ids = [row.get("run_id") for row in planned]
    if analysis.get("planned_run_ids") != planned_ids:
        raise ValueError("analysis planned_run_ids do not exactly match manifest order")
    projected = [
        {key: row.get(key) for key in ("run_id", "arm_id", "seed", "config_sha256", "controlled_signature")}
        for row in planned
    ]
    if analysis.get("planned_runs") != projected:
        raise ValueError("analysis planned_runs do not bind manifest cohort and config identities")
    try:
        authorization = verify_authorization(root, authorization_path)
    except (AuthorizationError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"authorization verification failed: {exc}") from exc
    if authorization.get("status") != "AUTHORIZED":
        raise ValueError("authorization status is not AUTHORIZED")
    try:
        experiment_rel = str(request_path.parent.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise ValueError("compiled request is outside project root") from exc
    if authorization.get("experiment_dir") != experiment_rel:
        raise ValueError("authorization experiment directory does not match analysis inputs")
    authorized = authorization.get("authorized_runs")
    if not isinstance(authorized, list):
        raise ValueError("authorization authorized_runs is missing")
    authorized_by_id: dict[str, dict[str, Any]] = {}
    for row in authorized:
        if not isinstance(row, dict) or not isinstance(row.get("run_id"), str) or row["run_id"] in authorized_by_id:
            raise ValueError("authorization authorized_runs is malformed or duplicated")
        authorized_by_id[row["run_id"]] = row
    if set(authorized_by_id) != set(planned_ids):
        raise ValueError("authorization cohort does not exactly match run manifest")
    for row in planned:
        auth_row = authorized_by_id[row["run_id"]]
        if auth_row.get("config_sha256") != row.get("config_sha256"):
            raise ValueError(f"authorization config hash mismatch: {row['run_id']}")
    return authorization, file_sha256(authorization_path), authorized_by_id


def _verify_run(
    root: Path,
    manifest: dict[str, Any],
    run_id: str,
    raw_path: Path,
    required_metrics: list[str],
    results_root: Path,
    authorized_row: dict[str, Any],
    authorization_sha256: str,
) -> dict[str, Any]:
    run_dir = _lexical_direct_run(raw_path, results_root, f"run {run_id}")
    row = _planned_row(manifest, run_id)
    meta_path = _safe_child(run_dir, "run_trace/run_meta.json", "run metadata")
    config_path = _safe_child(run_dir, "config_used.json", "config used")
    artifact_path = _safe_child(run_dir, "artifact_manifest.json", "artifact manifest")
    meta = _read_json(meta_path)
    config = _read_json(config_path)
    artifacts = _read_json(artifact_path)
    if not isinstance(meta, dict) or not isinstance(config, dict) or not isinstance(artifacts, dict):
        raise ValueError(f"run {run_id} identity files must be JSON objects")
    config_sha = canonical_sha(config)
    if meta.get("requested_run_id") != run_id:
        raise ValueError(f"run {run_id} requested_run_id mismatch")
    if authorized_row.get("run_id") != run_id:
        raise ValueError(f"run {run_id} is not authorized")
    if meta.get("config_canonical_sha256") != config_sha:
        raise ValueError(f"run {run_id} config hash mismatch in run_meta")
    if row.get("config_sha256") != config_sha or authorized_row.get("config_sha256") != config_sha:
        raise ValueError(f"run {run_id} config hash differs from run manifest")
    if artifacts.get("run_id") != run_id:
        raise ValueError(f"run {run_id} artifact manifest run_id mismatch")
    if artifacts.get("config_sha256") != config_sha:
        raise ValueError(f"run {run_id} artifact manifest config hash mismatch")
    provenance = config.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError(f"run {run_id} lacks config provenance")
    for key in ("run_id", "arm_id", "seed"):
        if provenance.get(key) != row.get(key):
            raise ValueError(f"run {run_id} provenance {key} differs from run manifest")
    if "controlled_signature" in provenance and provenance.get("controlled_signature") != row.get("controlled_signature"):
        raise ValueError(f"run {run_id} provenance controlled_signature differs from run manifest")
    if provenance.get("experiment_id") != manifest.get("experiment_id"):
        raise ValueError(f"run {run_id} experiment identity mismatch")
    scenario = manifest.get("scenario_identity")
    scenario_sha = canonical_sha(scenario)
    if provenance.get("scenario_identity") != scenario:
        raise ValueError(f"run {run_id} scenario identity mismatch")
    if meta.get("scenario_identity_sha256") not in (None, scenario_sha):
        raise ValueError(f"run {run_id} scenario identity hash mismatch")
    launch_nonce = meta.get("launch_nonce")
    run_attempt_id = meta.get("run_attempt_id")
    if not isinstance(launch_nonce, str) or len(launch_nonce) != 32 or any(c not in "0123456789abcdef" for c in launch_nonce):
        raise ValueError(f"run {run_id} launch_nonce is invalid")
    if not isinstance(run_attempt_id, str) or len(run_attempt_id) != 32 or any(c not in "0123456789abcdef" for c in run_attempt_id):
        raise ValueError(f"run {run_id} run_attempt_id is invalid")
    if meta.get("authorization_sha256") != authorization_sha256:
        raise ValueError(f"run {run_id} authorization hash mismatch")
    if meta.get("natural_end") is not True or meta.get("interrupted") is not False:
        raise ValueError(f"run {run_id} did not end naturally")
    receipt = meta.get("effective_receipt")
    if not isinstance(receipt, dict) or receipt.get("schema") != RECEIPT_SCHEMA or receipt.get("research_eligible") is not True or receipt.get("mismatches") != []:
        raise ValueError(f"run {run_id} effective receipt is not eligible")
    required = provenance.get("required_artifacts")
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError(f"run {run_id} required_artifacts is invalid")
    checked_artifacts = _artifact_entries(run_dir, artifacts, required)
    metrics_path = _safe_child(run_dir, "experiment_bundle/summary_metrics.csv", "summary metrics")
    metrics = _load_metrics(metrics_path, required_metrics)
    return {
        "run_id": run_id,
        "arm_id": row.get("arm_id"),
        "seed": row.get("seed"),
        "config_sha256": config_sha,
        "controlled_signature": row.get("controlled_signature"),
        "scenario_identity": scenario_sha,
        "metrics": metrics,
        "run_path": run_dir,
        "bound_artifacts": [
            {"path": str(path.relative_to(root.resolve())), "sha256": file_sha256(path)}
            for path in (meta_path, config_path, artifact_path, metrics_path)
        ] + [
            {"path": str((run_dir / item["path"]).relative_to(root.resolve())), "sha256": item["sha256"]}
            for item in checked_artifacts
        ],
    }


def _pair_key(row: dict[str, Any], paired_by: list[str]) -> tuple[Any, ...]:
    values = {
        "seed": row["seed"],
        "scenario_identity": row["scenario_identity"],
        "controlled_signature": row["controlled_signature"],
    }
    unknown = sorted(set(paired_by) - set(values))
    if unknown:
        raise ValueError(f"unsupported paired_by fields: {unknown}")
    return tuple(values[key] for key in paired_by)


def _bootstrap(values: list[float], seed: int, draws: int = 4000) -> tuple[float | None, float | None, str]:
    if len(values) < 2:
        return None, None, "NOT_ESTIMATED_LT2_PAIRS"
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(draws):
        means.append(sum(values[rng.randrange(len(values))] for _ in values) / len(values))
    means.sort()
    low = means[max(0, int(0.025 * draws) - 1)]
    high = means[min(draws - 1, int(0.975 * draws))]
    return low, high, "BOOTSTRAP_95"


def execute(
    root: Path,
    analysis: dict[str, Any],
    run_manifest: dict[str, Any],
    run_entries: list[tuple[str, Path]],
    out_dir: Path,
    *,
    request_path: Path | None = None,
    manifest_path: Path | None = None,
    authorization_path: Path | None = None,
    results_root: Path | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    results: list[dict[str, Any]] = []
    root = root.resolve()
    if request_path is None or manifest_path is None or authorization_path is None:
        errors.append("analysis requires request.json, run-manifest.json and authorization.json paths")
        return {
            "schema": OUTPUT_SCHEMA, "status": "BLOCKED", "errors": errors,
            "experiment_id": run_manifest.get("experiment_id"), "analysis_id": analysis.get("analysis_id"),
            "primary_metric": analysis.get("primary_metric", ""), "paired_by": [],
            "verified_run_ids": [], "bound_run_artifacts": [], "planned_contrasts": [],
            "input_hashes": {}, "claim_boundary": {"cannot_conclude": analysis.get("cannot_conclude", [])},
        }, results, errors
    request_path = request_path.resolve()
    manifest_path = manifest_path.resolve()
    authorization_path = authorization_path.resolve()
    results_root = (results_root or root / "CODE" / "Results").absolute()
    for label, path in (
        ("request", request_path), ("run manifest", manifest_path),
        ("authorization", authorization_path), ("results root", results_root),
    ):
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"{label} is outside project root")
    for run_id, path in run_entries:
        try:
            Path(path).absolute().relative_to(root)
        except ValueError:
            errors.append(f"run {run_id} is outside project root")
    try:
        authorization, authorization_sha256, authorized_by_id = _verify_compiled_binding(
            root, analysis, run_manifest, request_path, manifest_path, authorization_path)
    except ValueError as exc:
        errors.append(str(exc))
        authorization = {}
        authorization_sha256 = ""
        authorized_by_id = {}
    if analysis.get("schema") != ANALYSIS_SCHEMA:
        errors.append("analysis request schema mismatch")
    if run_manifest.get("schema") != MANIFEST_SCHEMA:
        errors.append("run manifest schema mismatch")
    if analysis.get("experiment_id") != run_manifest.get("experiment_id"):
        errors.append("analysis and run manifest experiment_id mismatch")
    planned = run_manifest.get("planned_runs")
    if not isinstance(planned, list) or any(not isinstance(row, dict) for row in planned):
        errors.append("run manifest planned_runs is invalid")
        planned = []
    planned_ids = [row.get("run_id") for row in planned]
    if len(planned_ids) != len(set(planned_ids)):
        errors.append("run manifest contains duplicate run ids")
    supplied_ids = [run_id for run_id, _ in run_entries]
    if len(supplied_ids) != len(set(supplied_ids)):
        errors.append("analysis invocation contains duplicate run ids")
    if set(supplied_ids) != set(planned_ids) or supplied_ids != planned_ids:
        errors.append("analysis run cohort/order does not exactly match run manifest")
    primary = analysis.get("primary_metric")
    secondary = analysis.get("secondary_metrics", [])
    if not isinstance(primary, str) or not primary:
        errors.append("analysis primary_metric is invalid")
        primary = ""
    if not isinstance(secondary, list) or any(not isinstance(item, str) for item in secondary):
        errors.append("analysis secondary_metrics is invalid")
        secondary = []
    required_metrics = [primary, *secondary] if primary else list(secondary)
    for run_id, path in run_entries:
        if errors and set(supplied_ids) != set(planned_ids):
            break
        try:
            results.append(_verify_run(
                root, run_manifest, run_id, path, required_metrics, results_root,
                authorized_by_id.get(run_id, {}), authorization_sha256))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"{run_id}: {exc}")
    pair_by = analysis.get("preregistration", {}).get("paired_by", []) if isinstance(analysis.get("preregistration"), dict) else []
    if not isinstance(pair_by, list) or not pair_by:
        errors.append("analysis preregistration paired_by is missing")
        pair_by = []
    planned_grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for row in planned:
        planned_pair_row = {
            "seed": row.get("seed"),
            "scenario_identity": canonical_sha(run_manifest.get("scenario_identity")),
            "controlled_signature": row.get("controlled_signature"),
        }
        try:
            key = _pair_key(planned_pair_row, pair_by)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        arm = row.get("arm_id")
        if arm in planned_grouped.setdefault(key, {}):
            errors.append(f"duplicate planned run for pairing key {key} and arm {arm}")
        planned_grouped[key][arm] = row
    grouped: dict[tuple[Any, ...], dict[str, dict[str, Any]]] = {}
    for row in results:
        try:
            key = _pair_key(row, pair_by)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        arm = row["arm_id"]
        if arm in grouped.setdefault(key, {}):
            errors.append(f"duplicate run for pairing key {key} and arm {arm}")
        grouped[key][arm] = row
    contrasts = analysis.get("preregistration", {}).get("planned_contrasts", []) if isinstance(analysis.get("preregistration"), dict) else []
    if not isinstance(contrasts, list) or not contrasts:
        errors.append("analysis preregistration planned_contrasts is missing")
        contrasts = []
    output_contrasts: list[dict[str, Any]] = []
    for contrast in contrasts:
        if not isinstance(contrast, dict):
            errors.append("planned contrast is not an object")
            continue
        name = contrast.get("name")
        left = contrast.get("left_arm")
        right = contrast.get("right_arm")
        if not isinstance(left, str) or not isinstance(right, str) or left == right:
            errors.append(f"contrast {name} has invalid or identical arms")
            continue
        required_arms = {left, right}
        contrast_keys = {
            key for key, arms in planned_grouped.items()
            if required_arms.intersection(arms)
        }
        diffs: list[float] = []
        for key in sorted(contrast_keys, key=repr):
            planned_arms = set(planned_grouped[key])
            verified_arms = set(grouped.get(key, {}))
            missing_planned = sorted(required_arms - planned_arms)
            missing_verified = sorted(required_arms - verified_arms)
            if missing_planned:
                errors.append(
                    f"contrast {name} pairing key {key} lacks planned arms {missing_planned}")
            if missing_verified:
                errors.append(
                    f"contrast {name} pairing key {key} lacks verified arms {missing_verified}")
            if missing_planned or missing_verified:
                continue
            arms = grouped[key]
            diffs.append(arms[left]["metrics"][primary] - arms[right]["metrics"][primary])
        if not contrast_keys:
            errors.append(f"contrast {name} has no preregistered pairing keys")
        if not diffs:
            errors.append(f"contrast {name} has no complete pairs")
        low, high, uncertainty = _bootstrap(diffs, int(canonical_sha({"experiment_id": run_manifest.get("experiment_id"), "contrast": name})[:16], 16))
        output_contrasts.append({
            "name": name,
            "left_arm": left,
            "right_arm": right,
            "metric": primary,
            "n_pairs": len(diffs),
            "differences": diffs,
            "mean_difference": (sum(diffs) / len(diffs)) if diffs else None,
            "median_difference": (sorted(diffs)[len(diffs) // 2] if diffs and len(diffs) % 2 else ((sorted(diffs)[len(diffs)//2 - 1] + sorted(diffs)[len(diffs)//2]) / 2 if diffs else None)),
            "ci95_low": low,
            "ci95_high": high,
            "uncertainty_status": uncertainty,
        })
    input_paths: dict[str, str] = {}
    if root.exists():
        # The caller adds the exact request/manifest paths in write_outputs;
        # execute itself binds the analyzer and the claim-gate schema so a
        # later schema relaxation cannot silently reinterpret this evidence.
        for source_rel in ("ANALYSIS/paired_analysis.py", "ANALYSIS/claims/claim.schema.json"):
            source_path = (root / source_rel).resolve()
            if source_path.is_file():
                input_paths[source_rel] = file_sha256(source_path)
    def _relative_or_absolute(path: Path) -> str:
        try:
            return str(path.relative_to(root))
        except ValueError:
            return str(path)

    # ``request_path`` is the compile input request.json.  The analyzer
    # itself is analysis-request.json in the same compiled experiment
    # directory; persist that distinct path so verification cannot silently
    # replay the wrong JSON document.
    analysis_request_path = request_path.parent / "analysis-request.json"
    if not analysis_request_path.is_file():
        errors.append("compiled analysis-request.json is missing")
    manifest = {
        "schema": OUTPUT_SCHEMA,
        "status": "VERIFIED" if not errors else "BLOCKED",
        "errors": sorted(set(errors)),
        "experiment_id": run_manifest.get("experiment_id"),
        "analysis_id": analysis.get("analysis_id"),
        "primary_metric": primary,
        "paired_by": pair_by,
        "verified_run_ids": [row["run_id"] for row in results],
        "bound_run_artifacts": [item for row in results for item in row["bound_artifacts"]],
        "planned_contrasts": output_contrasts,
        "input_hashes": input_paths,
        "claim_boundary": {"cannot_conclude": analysis.get("cannot_conclude", [])},
        "analysis_request_path": _relative_or_absolute(analysis_request_path),
        "run_manifest_path": _relative_or_absolute(manifest_path),
        "authorization_path": _relative_or_absolute(authorization_path),
        "results_root": _relative_or_absolute(results_root),
        "authorization_sha256": authorization_sha256,
        "run_entries": [
            {"run_id": run_id, "path": _relative_or_absolute(Path(path).absolute())}
            for run_id, path in run_entries
        ],
    }
    return manifest, results, sorted(set(errors))


def _write_summary(path: Path, manifest: dict[str, Any]) -> None:
    rows = [
        ["contrast", "metric", "n_pairs", "mean_difference", "median_difference", "ci95_low", "ci95_high", "uncertainty_status"]
    ]
    for row in manifest.get("planned_contrasts", []):
        rows.append([row.get(key) for key in ("name", "metric", "n_pairs", "mean_difference", "median_difference", "ci95_low", "ci95_high", "uncertainty_status")])
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)


def _write_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# Paired analysis report",
        "",
        f"- status: `{manifest.get('status')}`",
        f"- experiment: `{manifest.get('experiment_id')}`",
        f"- primary metric: `{manifest.get('primary_metric')}`",
        f"- verified runs: `{len(manifest.get('verified_run_ids', []))}`",
        "",
        "This report is generated from the persisted analysis manifest. It is not a paper claim by itself.",
    ]
    if manifest.get("errors"):
        lines.extend(["", "## Errors", "", *[f"- {item}" for item in manifest["errors"]]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_outputs(root: Path, out_dir: Path, manifest: dict[str, Any], results: list[dict[str, Any]]) -> None:
    root = root.resolve()
    out_dir = out_dir.resolve()
    if out_dir.is_symlink():
        raise ValueError("analysis output directory may not be symbolic")
    if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
        raise ValueError("analysis output directory must be new or empty")
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_summary(out_dir / "summary.csv", manifest)
    _write_report(out_dir / "report.md", manifest)
    code_path = Path(__file__).resolve()
    (out_dir / "analysis-code.py").write_bytes(code_path.read_bytes())
    # Persist the exact input bindings needed to recompute this output.  The
    # request and run-manifest paths are supplied by execute_main through the
    # private keys below and never accepted from untrusted report text.
    persisted = dict(manifest)
    # ``inputs`` is the public, paper-facing name.  Keep input_hashes as an
    # internal-compatible alias so old callers can inspect the same binding.
    persisted["inputs"] = dict(persisted.get("input_hashes", {}))
    for key in ("analysis_request_path", "run_manifest_path", "authorization_path"):
        raw = persisted.get(key)
        if not isinstance(raw, str):
            raise ValueError(f"analysis manifest missing {key}")
        path = _safe_child(root, raw, key)
        persisted["inputs"][raw] = file_sha256(path)
    if persisted.get("authorization_sha256") != persisted["inputs"].get(persisted.get("authorization_path")):
        raise ValueError("authorization hash is not bound in analysis inputs")
    persisted["input_hashes"] = dict(persisted["inputs"])
    persisted["output_hashes"] = {
        "summary.csv": file_sha256(out_dir / "summary.csv"),
        "report.md": file_sha256(out_dir / "report.md"),
        "analysis-code.py": file_sha256(out_dir / "analysis-code.py"),
    }
    persisted["output_artifacts"] = [
        {"path": str((out_dir / name).relative_to(root.resolve())), "sha256": digest}
        for name, digest in persisted["output_hashes"].items()
    ]
    (out_dir / "analysis-manifest.json").write_bytes(_json_bytes(persisted))


def verify_persisted_analysis(root: Path, manifest_path: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        root = root.resolve()
        manifest_path = manifest_path.resolve(strict=True)
        manifest_path.relative_to((root / "ANALYSIS").resolve())
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("schema") != OUTPUT_SCHEMA:
            raise ValueError("analysis manifest schema mismatch")
        if set(manifest) != PERSISTED_ANALYSIS_FIELDS:
            missing = sorted(PERSISTED_ANALYSIS_FIELDS - set(manifest))
            extra = sorted(set(manifest) - PERSISTED_ANALYSIS_FIELDS)
            raise ValueError(
                f"analysis manifest fields mismatch: missing={missing} extra={extra}")
        if manifest.get("status") != "VERIFIED" or manifest.get("errors") != []:
            raise ValueError("analysis manifest is not VERIFIED and empty-error")
        code = _safe_child(root, "ANALYSIS/paired_analysis.py", "analysis source")
        inputs = manifest.get("inputs")
        if not isinstance(inputs, dict) or inputs.get("ANALYSIS/paired_analysis.py") != file_sha256(code):
            raise ValueError("analysis source hash mismatch")
        if manifest.get("input_hashes") != inputs:
            raise ValueError("analysis input hash aliases differ")
        for raw, digest in inputs.items():
            path = _safe_child(root, raw, "analysis input")
            if not isinstance(digest, str) or file_sha256(path) != digest:
                raise ValueError(f"analysis input hash mismatch: {raw}")
        required_input_paths = {
            "analysis_request_path", "run_manifest_path", "authorization_path",
        }
        if any(not isinstance(manifest.get(key), str) for key in required_input_paths):
            raise ValueError("analysis manifest lacks compiled input paths")
        if manifest.get("authorization_sha256") != inputs.get(manifest["authorization_path"]):
            raise ValueError("persisted authorization hash binding mismatch")
        analysis_input_path = _safe_child(root, manifest["analysis_request_path"], "analysis request")
        manifest_input_path = _safe_child(root, manifest["run_manifest_path"], "run manifest")
        authorization_input_path = _safe_child(root, manifest["authorization_path"], "authorization")
        request_input_path = _safe_child(root, str(Path(manifest["analysis_request_path"]).parent / "request.json"), "request")
        analysis_input = _read_json(analysis_input_path)
        run_manifest_input = _read_json(manifest_input_path)
        run_entries_raw = manifest.get("run_entries")
        if not isinstance(run_entries_raw, list) or any(not isinstance(item, dict) for item in run_entries_raw):
            raise ValueError("persisted run_entries are malformed")
        results_root = _safe_child(root, manifest.get("results_root", ""), "results root")
        entries = []
        for item in run_entries_raw:
            if set(item) != {"run_id", "path"}:
                raise ValueError("persisted run entry has unexpected fields")
            entries.append((item["run_id"], root / item["path"]))
        recomputed, _results, recompute_errors = execute(
            root, analysis_input, run_manifest_input, entries, manifest_path.parent,
            request_path=request_input_path, manifest_path=manifest_input_path,
            authorization_path=authorization_input_path, results_root=results_root,
        )
        if recompute_errors:
            raise ValueError("recomputed analysis is blocked: " + "; ".join(recompute_errors))
        for key in RECOMPUTED_ANALYSIS_FIELDS:
            if recomputed.get(key) != manifest.get(key):
                raise ValueError(f"persisted analysis differs from recomputation: {key}")
        expected_inputs = dict(recomputed.get("input_hashes", {}))
        for raw in (
            manifest["analysis_request_path"], manifest["run_manifest_path"],
            manifest["authorization_path"],
        ):
            expected_inputs[raw] = file_sha256(_safe_child(root, raw, "recomputed analysis input"))
        if inputs != expected_inputs:
            raise ValueError("persisted analysis input set differs from recomputation")
        bound = manifest.get("bound_run_artifacts")
        if not isinstance(bound, list) or not bound:
            raise ValueError("analysis manifest has no bound run artifacts")
        for entry in bound:
            if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
                raise ValueError("bound run artifact entry malformed")
            path = _safe_child(root, entry["path"], "bound run artifact")
            if file_sha256(path) != entry["sha256"]:
                raise ValueError(f"bound run artifact hash mismatch: {entry['path']}")
        output_hashes = manifest.get("output_hashes")
        if not isinstance(output_hashes, dict) or set(output_hashes) != {
                "summary.csv", "report.md", "analysis-code.py"}:
            raise ValueError("analysis output hash set is not exact")
        output_artifacts = manifest.get("output_artifacts")
        if not isinstance(output_artifacts, list) or len(output_artifacts) != 3:
            raise ValueError("analysis output artifact set is not exact")
        output_dir = Path(manifest_path).relative_to(root).parent
        output_abs = root / output_dir
        expected_output_hashes: dict[str, str] = {}
        expected_output_artifacts: list[dict[str, str]] = []
        for name in ("summary.csv", "report.md", "analysis-code.py"):
            relative = str(output_dir / name)
            path = _safe_child(root, relative, f"analysis output {name}")
            digest = file_sha256(path)
            expected_output_hashes[name] = digest
            expected_output_artifacts.append({"path": relative, "sha256": digest})
        if output_hashes != expected_output_hashes:
            raise ValueError("analysis output hashes differ from manifest-directory artifacts")
        if output_artifacts != expected_output_artifacts:
            raise ValueError("analysis output artifact declarations differ from manifest directory")
        code_output = _safe_child(root, str(output_dir / "analysis-code.py"), "analysis code snapshot")
        if code_output.read_bytes() != code.read_bytes():
            raise ValueError("analysis-code.py does not match current analyzer")
        summary = _safe_child(root, str(output_dir / "summary.csv"), "analysis summary")
        expected_summary = output_abs / ".expected-summary.csv"
        _write_summary(expected_summary, manifest)
        try:
            if summary.read_bytes() != expected_summary.read_bytes():
                raise ValueError("analysis summary does not match persisted contrasts")
        finally:
            expected_summary.unlink(missing_ok=True)
        report = _safe_child(root, str(output_dir / "report.md"), "analysis report")
        expected_report = output_abs / ".expected-report.md"
        _write_report(expected_report, manifest)
        try:
            if report.read_bytes() != expected_report.read_bytes():
                raise ValueError("analysis report does not match persisted manifest")
        finally:
            expected_report.unlink(missing_ok=True)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return not errors, errors


def _load_run_entries(raw: list[str]) -> list[tuple[str, Path]]:
    result: list[tuple[str, Path]] = []
    for item in raw:
        if "=" not in item:
            raise ValueError("--run must be RUN_ID=RESULT_DIRECTORY")
        run_id, raw_path = item.split("=", 1)
        if not run_id or not raw_path:
            raise ValueError("--run must have non-empty RUN_ID and path")
        result.append((run_id, Path(raw_path)))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--run", action="append", default=[])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        analysis = _read_json(args.analysis)
        run_manifest = _read_json(args.manifest)
        root = Path.cwd().resolve()
        request_path = args.analysis.resolve().parent / "request.json"
        manifest, results, errors = execute(
            root, analysis, run_manifest, _load_run_entries(args.run), args.out,
            request_path=request_path, manifest_path=args.manifest.resolve(),
            authorization_path=args.authorization.resolve(),
            results_root=root / "CODE" / "Results",
        )
        write_outputs(root, args.out.resolve(), manifest, results)
        if errors:
            for error in errors:
                print(f"BLOCK: {error}", file=sys.stderr)
            return 1
        print(f"VERIFIED: {args.out / 'analysis-manifest.json'}")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"BLOCK: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
