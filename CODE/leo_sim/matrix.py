"""Strict, reviewable matrix contracts for the retained leo_sim V2 runtime.

The matrix contract is deliberately separate from the historical single-run
``leo-sim-experiment-request/v1`` contract.  It resolves every cell through
the same V2 governance path, but never launches a run or performs analysis.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from . import config as config_mod
from . import governance

MATRIX_REQUEST_SCHEMA = "leo-sim-experiment-matrix-request/v1"
MATRIX_MANIFEST_SCHEMA = "leo-sim-experiment-matrix-manifest/v1"
MATRIX_ANALYSIS_SCHEMA = "leo-sim-matrix-analysis-request/v1"
MATRIX_COMPILE_REPORT_SCHEMA = "leo-sim-experiment-matrix-compile-report/v2"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
EXPERIMENT_ID = re.compile(r"^EXP-[A-Za-z0-9][A-Za-z0-9_-]*$")
PHASES = {"training", "evaluation", "train", "eval", "non_learning"}
LINEAGE_MODES = {"new_training", "evaluation_only", "not_applicable"}
SUPPORTED_PAIRED_BY = {"pairing_key"}
STOCHASTIC_IDENTITY_PATHS = {
    "scenario.seed", "learning.seed",
    "learning.checkpoint_path", "learning.checkpoint_sha256",
    "learning.checkpoint_metadata_sha256",
}


class MatrixError(ValueError):
    """Raised when a matrix cannot be represented without ambiguity."""


def _normalize_phase(value: str) -> str:
    return {"train": "training", "eval": "evaluation"}.get(value, value)


def canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _expect_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MatrixError(f"{label} must be a mapping")
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        if label == "matrix request":
            raise MatrixError(f"unknown request fields {sorted(unknown)}")
        raise MatrixError(f"{label} unknown fields {sorted(unknown)}")
    if missing:
        raise MatrixError(f"{label} missing fields {sorted(missing)}")
    return value


def _safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or SAFE_ID.fullmatch(value) is None:
        raise MatrixError(f"{label} must be a safe identifier")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MatrixError(f"{label} must be a non-negative integer")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MatrixError(f"{label} must be a mapping")
    return value


def _dotted_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or any(
            not SAFE_ID.fullmatch(part) for part in value.split(".")):
        raise MatrixError(f"{label} must be a safe dotted path")
    return value


def _leaf_paths(value: dict[str, Any], prefix: str = "") -> set[str]:
    paths: set[str] = set()
    for key, child in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(child, dict):
            if not child:
                raise MatrixError(
                    f"config override contains empty mapping at {path}")
            paths.update(_leaf_paths(child, path))
        else:
            paths.add(path)
    return paths


def _path_allowed(path: str, allowed: set[str]) -> bool:
    return any(path == candidate or path.startswith(candidate + ".")
               for candidate in allowed)


def _validate_override_paths(overrides: dict[str, Any], allowed: set[str],
                             label: str) -> None:
    for path in sorted(_leaf_paths(overrides)):
        if not _path_allowed(path, allowed):
            raise MatrixError(f"{label} has undeclared intervention path: {path}")


def _remove_dotted(tree: dict[str, Any], path: str) -> None:
    cursor: Any = tree
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return
        cursor = cursor[part]
    if isinstance(cursor, dict):
        cursor.pop(parts[-1], None)


def _remove_paths(tree: dict[str, Any], paths: set[str]) -> None:
    for path in paths:
        _remove_dotted(tree, path)


def _validate_checkpoint_lineage(value: Any, label: str) -> dict[str, Any]:
    lineage = _expect_keys(value, {"mode", "source_run_id", "source_sha256"}, label)
    if lineage["mode"] not in LINEAGE_MODES:
        raise MatrixError(f"{label}.mode is invalid")
    source_run_id = lineage["source_run_id"]
    source_sha = lineage["source_sha256"]
    if source_run_id is not None:
        _safe_id(source_run_id, f"{label}.source_run_id")
    if source_sha is not None and (
            not isinstance(source_sha, str) or
            not re.fullmatch(r"[0-9a-f]{64}", source_sha)):
        raise MatrixError(f"{label}.source_sha256 must be lowercase SHA256 or null")
    mode = lineage["mode"]
    if mode == "evaluation_only" and (
            source_run_id is None or source_sha is None):
        raise MatrixError(f"{label}.evaluation_only requires source checkpoint identity")
    if mode != "evaluation_only" and (source_run_id is not None or source_sha is not None):
        raise MatrixError(f"{label}.{mode} cannot name a source checkpoint")
    return lineage


def _validate_acceptance(value: Any) -> dict[str, Any]:
    acceptance = _expect_keys(value, {
        "min_delivered_packets", "min_multisat_deliveries",
        "require_data_isl", "require_control_delivery",
    }, "acceptance")
    for key in ("min_delivered_packets", "min_multisat_deliveries"):
        if not isinstance(acceptance[key], int) or isinstance(acceptance[key], bool) \
                or acceptance[key] < 0:
            raise MatrixError(f"acceptance.{key} must be a non-negative integer")
    for key in ("require_data_isl", "require_control_delivery"):
        if not isinstance(acceptance[key], bool):
            raise MatrixError(f"acceptance.{key} must be bool")
    return acceptance


def _validate_analysis(value: Any) -> dict[str, Any]:
    required = {
        "analysis_id", "primary_metric", "estimand", "paired_by",
        "planned_contrasts",
    }
    if not isinstance(value, dict):
        raise MatrixError("analysis must be a mapping")
    unknown = set(value) - (required | {"decision_contract"})
    missing = required - set(value)
    if unknown:
        raise MatrixError(f"analysis unknown fields {sorted(unknown)}")
    if missing:
        raise MatrixError(f"analysis missing fields {sorted(missing)}")
    analysis = value
    _safe_id(analysis["analysis_id"], "analysis.analysis_id")
    for key in ("primary_metric", "estimand"):
        if not isinstance(analysis[key], str) or not analysis[key]:
            raise MatrixError(f"analysis.{key} must be a non-empty string")
    if analysis["paired_by"] != ["pairing_key"]:
        raise MatrixError("unsupported paired_by; v1 supports only ['pairing_key']")
    contrasts = analysis["planned_contrasts"]
    if not isinstance(contrasts, list) or not contrasts:
        raise MatrixError("analysis.planned_contrasts must be non-empty")
    contrast_names: set[str] = set()
    contrast_arm_pairs: set[frozenset[str]] = set()
    for i, contrast in enumerate(contrasts):
        item = _expect_keys(contrast, {"name", "left_arm", "right_arm", "estimand"},
                            f"analysis.planned_contrasts[{i}]")
        _safe_id(item["name"], f"analysis.planned_contrasts[{i}].name")
        if item["name"] in contrast_names:
            raise MatrixError(f"duplicate contrast name: {item['name']}")
        contrast_names.add(item["name"])
        _safe_id(item["left_arm"], f"analysis.planned_contrasts[{i}].left_arm")
        _safe_id(item["right_arm"], f"analysis.planned_contrasts[{i}].right_arm")
        arm_pair = frozenset((item["left_arm"], item["right_arm"]))
        if arm_pair in contrast_arm_pairs:
            raise MatrixError(
                f"duplicate contrast arm pair: {item['left_arm']}, {item['right_arm']}")
        contrast_arm_pairs.add(arm_pair)
        if not isinstance(item["estimand"], str) or not item["estimand"]:
            raise MatrixError(f"analysis.planned_contrasts[{i}].estimand must be a string")
    if "decision_contract" in analysis:
        decision = _expect_keys(
            analysis["decision_contract"], {"path"},
            "analysis.decision_contract")
        raw_path = decision["path"]
        if (not isinstance(raw_path, str)
                or not raw_path.startswith("CODE/work/")
                or not raw_path.endswith(".json")
                or ".." in Path(raw_path).parts):
            raise MatrixError(
                "analysis.decision_contract.path must be a safe "
                "CODE/work/... JSON path")
    return analysis


def _validate_claim_boundary(value: Any) -> dict[str, Any]:
    boundary = _expect_keys(value, {"can_claim", "cannot_claim"}, "claim_boundary")
    for key in ("can_claim", "cannot_claim"):
        if not isinstance(boundary[key], list) or any(
                not isinstance(x, str) or not x for x in boundary[key]):
            raise MatrixError(f"claim_boundary.{key} must be a string list")
    return boundary


def validate_request(request: Any) -> dict[str, Any]:
    required = {
        "schema", "experiment_id", "runtime_kind", "work_finalization",
        "common_config", "arms", "cells", "acceptance", "analysis",
        "claim_boundary",
    }
    allowed = required | {"execution_policy"}
    if not isinstance(request, dict):
        raise MatrixError("matrix request must be a mapping")
    unknown = set(request) - allowed
    missing = required - set(request)
    if unknown:
        raise MatrixError(f"unknown request fields {sorted(unknown)}")
    if missing:
        raise MatrixError(f"matrix request missing fields {sorted(missing)}")
    if request["schema"] != MATRIX_REQUEST_SCHEMA:
        raise MatrixError(f"request.schema must be {MATRIX_REQUEST_SCHEMA!r}")
    experiment_id = request["experiment_id"]
    if not isinstance(experiment_id, str) or EXPERIMENT_ID.fullmatch(experiment_id) is None:
        raise MatrixError("experiment_id must be a safe EXP-* identifier")
    if request["runtime_kind"] != governance.RUNTIME_KIND:
        raise MatrixError("runtime_kind must be 'leo_sim_v2'")
    execution_policy = request.get("execution_policy")
    if execution_policy is not None:
        execution_policy = _expect_keys(
            execution_policy, {"mode"}, "execution_policy")
        if execution_policy["mode"] != "serial_fail_closed":
            raise MatrixError(
                "execution_policy.mode must be serial_fail_closed")
    finalization = request["work_finalization"]
    if (not isinstance(finalization, str) or not finalization.startswith("CODE/work/")
            or not finalization.endswith("/finalization.json")
            or ".." in Path(finalization).parts):
        raise MatrixError("work_finalization must be a safe CODE/work/.../finalization.json path")
    _mapping(request["common_config"], "common_config")
    arms = request["arms"]
    if not isinstance(arms, list) or not arms:
        raise MatrixError("arms must be a non-empty list")
    arm_ids: set[str] = set()
    normalized_arms: list[dict[str, Any]] = []
    for i, raw in enumerate(arms):
        arm = _expect_keys(raw, {
            "arm_id", "config_overrides", "intervention_paths",
        }, f"arms[{i}]")
        arm_id = _safe_id(arm["arm_id"], f"arms[{i}].arm_id")
        if arm_id in arm_ids:
            raise MatrixError(f"duplicate arm_id: {arm_id}")
        arm_ids.add(arm_id)
        intervention_paths = arm["intervention_paths"]
        if not isinstance(intervention_paths, list):
            raise MatrixError(f"arms[{i}].intervention_paths must be a list")
        normalized_paths = [_dotted_path(path,
                                         f"arms[{i}].intervention_paths")
                            for path in intervention_paths]
        if len(set(normalized_paths)) != len(normalized_paths):
            raise MatrixError(f"arms[{i}].intervention_paths contains duplicates")
        overrides = _mapping(arm["config_overrides"],
                             f"arms[{i}].config_overrides")
        _validate_override_paths(overrides, set(normalized_paths),
                                 f"arm {arm_id}")
        override_paths = _leaf_paths(overrides)
        if set(normalized_paths) != override_paths:
            raise MatrixError(
                f"arm {arm_id} intervention_paths must equal exact override "
                f"leaf paths; declared={sorted(normalized_paths)}, "
                f"actual={sorted(override_paths)}")
        if any(_path_allowed(path, STOCHASTIC_IDENTITY_PATHS)
               for path in _leaf_paths(overrides)):
            raise MatrixError(f"arm {arm_id} cannot override stochastic identity paths")
        normalized_arms.append({"arm_id": arm_id,
                                "config_overrides": overrides,
                                "intervention_paths": normalized_paths})
    cells = request["cells"]
    if not isinstance(cells, list) or not cells:
        raise MatrixError("cells must be a non-empty list")
    run_ids: set[str] = set()
    normalized_cells: list[dict[str, Any]] = []
    for i, raw in enumerate(cells):
        cell = _expect_keys(raw, {
            "run_id", "arm_id", "phase", "trace_seed", "learning_seed",
            "pairing_key", "config_overrides", "checkpoint_lineage",
        }, f"cells[{i}]")
        run_id = _safe_id(cell["run_id"], f"cells[{i}].run_id")
        if run_id in run_ids:
            raise MatrixError(f"duplicate run_id: {run_id}")
        run_ids.add(run_id)
        arm_id = _safe_id(cell["arm_id"], f"cells[{i}].arm_id")
        if arm_id not in arm_ids:
            raise MatrixError(f"unknown arm_id: {arm_id}")
        if cell["phase"] not in PHASES:
            raise MatrixError(f"cells[{i}].phase is invalid")
        trace_seed = _nonnegative_int(cell["trace_seed"], f"cells[{i}].trace_seed")
        learning_seed = cell["learning_seed"]
        if learning_seed is not None:
            learning_seed = _nonnegative_int(learning_seed, f"cells[{i}].learning_seed")
        if not isinstance(cell["pairing_key"], str) or not cell["pairing_key"] \
                or SAFE_ID.fullmatch(cell["pairing_key"]) is None:
            raise MatrixError(f"cells[{i}].pairing_key must be a safe identifier")
        lineage = _validate_checkpoint_lineage(
            cell["checkpoint_lineage"], f"cells[{i}].checkpoint_lineage")
        normalized_cells.append({
            "run_id": run_id, "arm_id": arm_id,
            "phase": _normalize_phase(cell["phase"]),
            "trace_seed": trace_seed, "learning_seed": learning_seed,
            "pairing_key": cell["pairing_key"],
            "config_overrides": _mapping(cell["config_overrides"],
                                           f"cells[{i}].config_overrides"),
            "checkpoint_lineage": lineage,
        })
        _validate_override_paths(normalized_cells[-1]["config_overrides"],
                                 STOCHASTIC_IDENTITY_PATHS,
                                 f"cell {run_id}")
    _validate_acceptance(request["acceptance"])
    _validate_analysis(request["analysis"])
    _validate_claim_boundary(request["claim_boundary"])
    for contrast in request["analysis"]["planned_contrasts"]:
        if contrast["left_arm"] not in arm_ids or contrast["right_arm"] not in arm_ids:
            raise MatrixError("analysis contrast references unknown arm")
        if contrast["left_arm"] == contrast["right_arm"]:
            raise MatrixError("analysis contrast must compare distinct arms")
    return {
        **request,
        **({"execution_policy": execution_policy}
           if execution_policy is not None else {}),
        "arms": normalized_arms,
        "cells": normalized_cells,
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _set_if_absent_or_equal(tree: dict[str, Any], group: str, key: str,
                            expected: Any, label: str) -> None:
    values = tree.setdefault(group, {})
    if not isinstance(values, dict):
        raise MatrixError(f"{label} crosses a scalar")
    actual = values.get(key)
    if actual is not None and actual != expected:
        raise MatrixError(f"{label} conflicts with explicit seed")
    values[key] = expected


def _checkpoint_for_config(config: dict[str, Any], lineage: dict[str, Any],
                           phase: str, learning_seed: int | None,
                           label: str) -> None:
    phase = _normalize_phase(phase)
    learning = config["learning"]
    algorithm = learning["algorithm"]
    if phase == "non_learning":
        if (algorithm != "none" or lineage["mode"] != "not_applicable"
                or learning_seed is not None or learning.get("seed") is not None):
            raise MatrixError(f"{label}: non_learning phase has learning configuration")
        return
    if algorithm == "none":
        raise MatrixError(f"{label}: learning phase requires a learning algorithm")
    if learning_seed is None:
        raise MatrixError(f"{label}: learning phase requires learning_seed")
    expected_phase = "train" if phase == "training" else "eval"
    if learning["mode"] != expected_phase:
        raise MatrixError(f"{label}: phase does not match learning.mode")
    expected_lineage = "new_training" if phase == "training" else "evaluation_only"
    if lineage["mode"] != expected_lineage:
        raise MatrixError(f"{label}: phase/checkpoint lineage mismatch")
    if phase == "evaluation":
        actual_sha = learning.get("checkpoint_sha256")
        if actual_sha != lineage["source_sha256"]:
            raise MatrixError(
                f"{label}: checkpoint_lineage.source_sha256 does not match "
                "resolved learning.checkpoint_sha256")


def _control_projection(resolved_config: dict[str, Any],
                        intervention_paths: list[str]) -> dict[str, Any]:
    projection = copy.deepcopy(resolved_config)
    _remove_paths(projection, STOCHASTIC_IDENTITY_PATHS | set(intervention_paths))
    return projection


def _canonical_run_id(experiment_id: str, cell: dict[str, Any]) -> str:
    suffix = f"s{cell['trace_seed']}"
    if cell["learning_seed"] is not None:
        suffix += f"-l{cell['learning_seed']}"
    return f"{experiment_id}-{cell['arm_id']}-{suffix}"


def _resolve_cells(request: dict[str, Any], project_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    validated = validate_request(request)
    arm_by_id = {arm["arm_id"]: arm for arm in validated["arms"]}
    all_intervention_paths = {
        path for arm in validated["arms"]
        for path in arm["intervention_paths"]
    }
    rows: list[dict[str, Any]] = []
    for index, cell in enumerate(validated["cells"]):
        if _canonical_run_id(validated["experiment_id"], cell) != cell["run_id"]:
            raise MatrixError(f"cells[{index}] run_id identity mismatch")
        merged = _deep_merge(validated["common_config"],
                             arm_by_id[cell["arm_id"]]["config_overrides"])
        merged = _deep_merge(merged, cell["config_overrides"])
        _set_if_absent_or_equal(merged, "scenario", "seed", cell["trace_seed"],
                                f"cells[{index}].trace_seed")
        if cell["learning_seed"] is not None:
            _set_if_absent_or_equal(merged, "learning", "seed",
                                    cell["learning_seed"],
                                    f"cells[{index}].learning_seed")
        intent = governance.build_run_intent({
            "runtime_kind": governance.RUNTIME_KIND,
            "config": merged,
        }, project_root=project_root)
        _checkpoint_for_config(intent["resolved"]["config"],
                               cell["checkpoint_lineage"], cell["phase"],
                               cell["learning_seed"], f"cell {cell['run_id']}")
        arm = arm_by_id[cell["arm_id"]]
        rows.append({
            **cell,
            "runtime_kind": governance.RUNTIME_KIND,
            "config": intent["resolved"],
            "config_sha256": intent["config_sha256"],
            "trace_identity_sha256": intent["trace_identity_sha256"],
            "input_sha256": intent["input_sha256"],
            "code_sha256": intent["code_sha256"],
            "execution_chain_sha256": governance.execution_chain_sha256(),
            "controlled_signature": canonical_sha(_control_projection(
                intent["resolved"]["config"], all_intervention_paths)),
            "acceptance": validated["acceptance"],
            "config_path": f"resolved/{cell['run_id']}.leo-sim.yaml",
        })
    _validate_pairing_contract(validated, rows)
    return validated, rows


def _validate_pairing_contract(request: dict[str, Any],
                               rows: list[dict[str, Any]]) -> None:
    """Validate the executable v1 paired comparison contract.

    Every pairing key is exactly one left/right pair for one preregistered
    contrast. Trace identity is shared; learning identity and checkpoints are
    intentionally allowed to differ between the two arms.
    """
    contrasts = request["analysis"]["planned_contrasts"]
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_pair.setdefault(row["pairing_key"], []).append(row)
        source_run_id = row["checkpoint_lineage"]["source_run_id"]
        if source_run_id is not None and source_run_id == row["run_id"]:
            raise MatrixError(
                f"cell {row['run_id']} checkpoint lineage cannot reference itself")
        if source_run_id is not None and source_run_id != \
                f"external-{row['checkpoint_lineage']['source_sha256']}":
            raise MatrixError(
                f"cell {row['run_id']} evaluation lineage must use the "
                "content-bound external checkpoint identity "
                "external-<source_sha256>; planned training provenance is not "
                "verified by the v1 matrix contract")
    contrast_by_arms = {
        frozenset((contrast["left_arm"], contrast["right_arm"])): contrast
        for contrast in contrasts
    }
    for pairing_key, group in by_pair.items():
        if len(group) != 2:
            raise MatrixError(
                f"pairing_key {pairing_key} must have exactly one left and one right cell")
        arm_ids = frozenset(row["arm_id"] for row in group)
        contrast = contrast_by_arms.get(arm_ids)
        if contrast is None:
            raise MatrixError(
                f"pairing_key {pairing_key} has no matching planned contrast")
        left = next(row for row in group if row["arm_id"] == contrast["left_arm"])
        right = next(row for row in group if row["arm_id"] == contrast["right_arm"])
        if left["trace_seed"] != right["trace_seed"]:
            raise MatrixError(
                f"pairing_key {pairing_key} must share trace_seed for paired comparison")
        if left["phase"] != right["phase"]:
            raise MatrixError(
                f"pairing_key {pairing_key} must share learning phase")
        if left["controlled_signature"] != right["controlled_signature"]:
            raise MatrixError(
                f"pairing_key {pairing_key} has inconsistent controlled configuration")
    for contrast in contrasts:
        contrast_arm_ids = {
            contrast["left_arm"], contrast["right_arm"]}
        contrast_keys = {
            pairing_key for pairing_key, group in by_pair.items()
            if {row["arm_id"] for row in group} == contrast_arm_ids
        }
        if not contrast_keys:
            raise MatrixError(
                f"contrast {contrast['name']} has no planned pairing key")
        for pairing_key in contrast_keys:
            group = by_pair[pairing_key]
            if {row["arm_id"] for row in group} != {
                    contrast["left_arm"], contrast["right_arm"]}:
                raise MatrixError(
                    f"pairing_key {pairing_key} does not contain both arms of "
                    f"contrast {contrast['name']}")


def _write_json(path: Path, value: Any) -> None:
    if path.is_symlink():
        raise MatrixError(f"refusing symbolic output artifact: {path}")
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                               sort_keys=True) + "\n", encoding="utf-8")


def _artifact_hashes(out_dir: Path, paths: list[str]) -> dict[str, str]:
    return {raw: hashlib.sha256((out_dir / raw).read_bytes()).hexdigest()
            for raw in sorted(paths)}


def _design_accounting(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count unique resolved configurations separately from re-executions."""
    run_ids_by_config: dict[str, list[str]] = {}
    for row in rows:
        run_ids_by_config.setdefault(row["config_sha256"], []).append(
            row["run_id"])
    groups = [
        {"config_sha256": digest, "run_ids": sorted(run_ids)}
        for digest, run_ids in sorted(run_ids_by_config.items())
        if len(run_ids) > 1
    ]
    unique = len(run_ids_by_config)
    return {
        "schema": "leo-sim-matrix-design-accounting/v1",
        "planned_cells": len(rows),
        "unique_resolved_configurations": unique,
        "exact_reexecution_cells": len(rows) - unique,
        "exact_reexecution_groups": groups,
        "independent_condition_rule": (
            "one independent condition per unique resolved config SHA256"),
    }


def _reject_symlink_ancestors(path: Path, stop: Path) -> None:
    """Reject caller-controlled symlink components up to a trusted root."""
    path = Path(path)
    if not path.is_absolute():
        path = Path.cwd() / path
    stop = Path(stop)
    if not stop.is_absolute():
        stop = Path.cwd() / stop
    resolved_path = path.resolve(strict=False)
    resolved_stop = stop.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_stop)
    except ValueError as exc:
        raise MatrixError(f"path escapes project root: {path}") from exc
    current = path
    while True:
        if current.is_symlink():
            raise MatrixError(f"path contains a symbolic ancestor: {path}")
        if current.resolve(strict=False) == resolved_stop:
            break
        if current == current.parent:
            raise MatrixError(f"path is not below project root: {path}")
        current = current.parent


def _canonical_experiment_dir(root: Path, experiment_id: str,
                               out_dir: Path) -> Path:
    expected = (root / "EXPERIMENTS" / experiment_id).resolve()
    candidate = out_dir.resolve(strict=False)
    if candidate != expected:
        raise MatrixError(
            f"output directory must be canonical {expected}; got {candidate}")
    _reject_symlink_ancestors(out_dir, root)
    return expected


def _load_decision_contract(
        root: Path, request: dict[str, Any]) -> dict[str, Any] | None:
    spec = request["analysis"].get("decision_contract")
    if spec is None:
        return None
    raw_path = spec["path"]
    path = root / raw_path
    _reject_symlink_ancestors(path, root)
    if path.is_symlink() or not path.is_file():
        raise MatrixError(f"missing post-analysis decision contract: {raw_path}")
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"post-analysis decision contract unreadable: {exc}") \
            from exc
    if not isinstance(contract, dict):
        raise MatrixError("post-analysis decision contract must be an object")
    schema = contract.get("schema")
    if schema == "leo-sim-scene-check-contract/v1":
        required = {"schema", "decision_path", "decision_sha256",
                    "coverage_path", "coverage_sha256",
                    "canonical_invocation"}
        if set(contract) != required:
            raise MatrixError("scene-check contract keys mismatch")
        for field, suffix in (("decision_path", (".yaml", ".yml", ".json")),
                              ("coverage_path", (".json",))):
            raw = contract[field]
            if (not isinstance(raw, str) or not raw.startswith("CODE/work/")
                    or ".." in Path(raw).parts
                    or not raw.endswith(suffix)):
                raise MatrixError(f"scene-check {field} is unsafe")
            bound = root / raw
            _reject_symlink_ancestors(bound, root)
            if bound.is_symlink() or not bound.is_file():
                raise MatrixError(f"scene-check {field} is missing or symbolic")
            actual = hashlib.sha256(bound.read_bytes()).hexdigest()
            if actual != contract[f"{field[:-5]}_sha256"]:
                raise MatrixError(f"scene-check {field} hash mismatch")
        invocation = contract["canonical_invocation"]
        expected_prefix = ["python3", "-m", "CODE.leo_sim.scene_check",
                           "--root", ".", "--contract", raw_path]
        if (not isinstance(invocation, list) or len(invocation) != 7
                or invocation != expected_prefix):
            raise MatrixError("scene-check canonical_invocation is not frozen")
        return {
            "schema": schema,
            "path": raw_path,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "canonical_invocation": invocation,
            "bound_artifacts": {
                contract["decision_path"]: contract["decision_sha256"],
                contract["coverage_path"]: contract["coverage_sha256"],
            },
        }
    if schema != "leo-sim-isl-pressure-decision/v1":
        raise MatrixError("unsupported post-analysis decision contract")
    invocation = contract.get("canonical_invocation")
    if (not isinstance(invocation, list) or len(invocation) < 5
            or any(not isinstance(token, str) or not token
                   or re.fullmatch(r"[A-Za-z0-9_./:-]+", token) is None
                   for token in invocation)
            or invocation[:2] != ["python3", "-m"]
            or not invocation[2].startswith("CODE.experiment_platform.")
            or len(invocation[3:]) % 2):
        raise MatrixError(
            "post-analysis canonical_invocation must be a safe python -m "
            "command followed by flag/value pairs")
    return {
        "schema": schema,
        "path": raw_path,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "canonical_invocation": invocation,
        "bound_artifacts": {},
    }


def _render_invocation(invocation: list[str]) -> list[str]:
    lines = [" ".join(invocation[:3]) + " \\"]
    pairs = list(zip(invocation[3::2], invocation[4::2]))
    for index, (flag, value) in enumerate(pairs):
        suffix = " \\" if index < len(pairs) - 1 else ""
        lines.append(f"  {flag} {value}{suffix}")
    return lines


def _render_runbook(
        request: dict[str, Any], rows: list[dict[str, Any]],
        decision_contract: dict[str, Any] | None,
        design_accounting: dict[str, Any] | None = None) -> str:
    serial = (request.get("execution_policy", {}).get("mode")
              == "serial_fail_closed")
    command_contract = (
        "Cells are listed in mandatory order; every later command is blocked "
        "until its predecessors pass the serial evidence gate:"
        if serial else
        "Each cell is an independent controlled command after review, "
        "authorization, and clean deployment:"
    )
    lines = [
        f"# {request['experiment_id']}", "",
        "Runtime: `leo_sim_v2`; compilation only, no run is launched.", "",
        command_contract, "",
    ]
    if design_accounting is not None:
        lines.extend([
            "## Design accounting", "",
            f"{design_accounting['planned_cells']} planned cells; "
            f"{design_accounting['unique_resolved_configurations']} unique "
            "resolved configurations; "
            f"{design_accounting['exact_reexecution_cells']} exact "
            "re-execution cells.", "",
            "Exact re-executions are repeatability evidence only and do not "
            "increase the independent-condition count.", "",
        ])
    if serial:
        lines.extend([
            "Execution policy: `serial_fail_closed`. The canonical runner applies a "
            "machine-enforced serial predecessor gate before every cell after the first; "
            "missing or ineligible pulled predecessor evidence blocks the next launch.", "",
        ])
    for row in rows:
        lines.extend([
            f"## {row['run_id']}", "", "```bash",
            "CODE/scripts/remote/run-remote.sh \\",
            "  --runtime-kind leo_sim_v2 \\",
            f"  --config EXPERIMENTS/{request['experiment_id']}/{row['config_path']} \\",
            f"  --authorization EXPERIMENTS/{request['experiment_id']}/authorization.json \\",
            f"  --session {row['run_id'].lower()}", "```", "",
        ])
    lines.extend([
        "## V2 analysis after every authorized cell has a natural-end result", "",
        "```bash",
        "python3 -m CODE.experiment_platform.v2_analysis \\",
        f"  --experiment EXPERIMENTS/{request['experiment_id']} \\",
        f"  --authorization EXPERIMENTS/{request['experiment_id']}/authorization.json \\",
        f"  --out ANALYSIS/{request['experiment_id']}/v2-paired",
        "```", "",
        "The output is evidence-bound analysis only; claim-support and value-gate review remain required.",
    ])
    if decision_contract is not None:
        if decision_contract.get("schema") == "leo-sim-scene-check-contract/v1":
            lines.extend([
                "", "## Apply the frozen scene and coverage gates", "",
                "Run scene_check once per verified natural-end result. The "
                "contract freezes both the scene thresholds and the full "
                "population coverage ledger; any binding or classification "
                "error is a stop, never a clean-scene result.", "",
            ])
            for row in rows:
                invocation = decision_contract["canonical_invocation"] + [
                    "--run-dir", f"CODE/Results/{row['run_id']}",
                    "--out", f"ANALYSIS/{request['experiment_id']}/scene-check/"
                             f"{row['run_id']}.json",
                ]
                lines.extend(["```bash", *_render_invocation(invocation),
                              "```", ""])
            lines.extend([
                f"The command and all bound inputs are frozen in "
                f"`{decision_contract['path']}`. Do not substitute a different "
                "decision, coverage report, or in-memory threshold.",
            ])
        else:
            lines.extend([
                "", "## Apply the frozen post-analysis decision", "",
                "Run this persisted classifier only after the V2 analysis above "
                "produces a verified manifest. Any verification or classification "
                "error is a stop, never a no-pressure result.", "", "```bash",
                *_render_invocation(decision_contract["canonical_invocation"]),
                "```", "",
                f"The command and subsequent action are frozen in "
                f"`{decision_contract['path']}`. Do not substitute an in-memory "
                "classification or change thresholds after observing results.",
            ])
    return "\n".join(lines)


def _analysis_document(request: dict[str, Any], manifest_sha: str,
                       request_sha: str, rows: list[dict[str, Any]],
                       decision_contract: dict[str, Any] | None = None,
                       design_accounting: dict[str, Any] | None = None) -> dict[str, Any]:
    document = {
        "schema": MATRIX_ANALYSIS_SCHEMA,
        "runtime_kind": governance.RUNTIME_KIND,
        "experiment_id": request["experiment_id"],
        "analysis": request["analysis"],
        "claim_boundary": request["claim_boundary"],
        "planned_run_ids": [row["run_id"] for row in rows],
        "planned_cells": [
            {key: row[key] for key in (
                "run_id", "arm_id", "phase", "trace_seed", "learning_seed",
                "pairing_key", "config_sha256", "trace_identity_sha256",
                "input_sha256", "controlled_signature", "acceptance")}
            for row in rows
        ],
        "request_sha256": request_sha,
        "matrix_manifest_sha256": manifest_sha,
        "status": "WAITING_FOR_VERIFIED_RUNS",
    }
    if decision_contract is not None:
        document["decision_contract"] = decision_contract
    if design_accounting is not None:
        document["design_accounting"] = design_accounting
    return document


def compile_matrix_experiment(request_path: Path, out_dir: Path,
                              project_root: Path | None = None) -> dict[str, Any]:
    request_path = Path(request_path)
    out_dir = Path(out_dir)
    if not request_path.is_absolute():
        request_path = Path.cwd() / request_path
    if not out_dir.is_absolute():
        out_dir = Path.cwd() / out_dir
    project_root = Path(project_root or request_path.parent).resolve()
    _reject_symlink_ancestors(request_path, project_root)
    if request_path.is_symlink() or not request_path.is_file():
        raise MatrixError(f"request is not a regular file: {request_path}")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatrixError(f"request unreadable: {exc}") from exc
    validated = validate_request(request)
    decision_contract = _load_decision_contract(project_root, validated)
    validated, rows = _resolve_cells(validated, project_root)
    design_accounting = _design_accounting(rows)
    out_dir = _canonical_experiment_dir(project_root,
                                         validated["experiment_id"], out_dir)
    if out_dir.is_symlink():
        raise MatrixError("output directory may not be symbolic")
    if out_dir.exists():
        if not out_dir.is_dir():
            raise MatrixError("output path is not a directory")
        if any(out_dir.iterdir()):
            raise MatrixError("output directory must be empty")
    else:
        out_dir.mkdir(parents=True)
    (out_dir / "resolved").mkdir()
    _write_json(out_dir / "request.json", validated)
    request_sha = hashlib.sha256((out_dir / "request.json").read_bytes()).hexdigest()
    for row in rows:
        config_path = out_dir / row["config_path"]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({
            "config_version": row["config"]["version"],
            **row["config"]["config"],
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema": MATRIX_MANIFEST_SCHEMA,
        "runtime_kind": governance.RUNTIME_KIND,
        "experiment_id": validated["experiment_id"],
        "request_sha256": request_sha,
        "common_config_sha256": canonical_sha(validated["common_config"]),
        "arms": validated["arms"],
        "cells": [{key: row[key] for key in (
            "run_id", "runtime_kind", "arm_id", "phase", "trace_seed",
            "learning_seed", "pairing_key", "config_overrides",
            "checkpoint_lineage", "config_path", "config_sha256",
            "trace_identity_sha256", "input_sha256", "code_sha256",
            "execution_chain_sha256", "controlled_signature", "acceptance")}
                   for row in rows],
        "execution_authorized": False,
    }
    _write_json(out_dir / "run-manifest.json", manifest)
    manifest_sha = hashlib.sha256((out_dir / "run-manifest.json").read_bytes()).hexdigest()
    analysis = _analysis_document(
        validated, manifest_sha, request_sha, rows, decision_contract,
        design_accounting)
    _write_json(out_dir / "analysis-request.json", analysis)
    (out_dir / "RUNBOOK.md").write_text(
        _render_runbook(validated, rows, decision_contract, design_accounting),
        encoding="utf-8")
    bound_paths = ["request.json", "run-manifest.json", "analysis-request.json",
                   "RUNBOOK.md", *(row["config_path"] for row in rows)]
    report = {
        "schema": MATRIX_COMPILE_REPORT_SCHEMA,
        "status": "COMPILED_REVIEW_REQUIRED",
        "runtime_kind": governance.RUNTIME_KIND,
        "experiment_id": validated["experiment_id"],
        "errors": [],
        "request_sha256": request_sha,
        "execution_authorized": False,
        "launcher_generated": False,
        "design_accounting": design_accounting,
        "artifact_hashes": _artifact_hashes(out_dir, bound_paths),
    }
    if decision_contract is not None:
        report["artifact_hashes"][decision_contract["path"]] = \
            decision_contract["sha256"]
        report["artifact_hashes"].update(decision_contract["bound_artifacts"])
    _write_json(out_dir / "compile-report.json", report)
    return report


def verify_compiled_matrix(root: Path, experiment_dir: Path) -> tuple[str, dict[str, str], list[dict[str, Any]]]:
    """Recompute all matrix identities without trusting compiled documents."""
    root = Path(root).resolve()
    raw_experiment_dir = Path(experiment_dir)
    _reject_symlink_ancestors(raw_experiment_dir, root)
    experiment_dir = raw_experiment_dir.resolve()
    docs = {}
    for name in ("request.json", "compile-report.json", "run-manifest.json",
                 "analysis-request.json"):
        path = experiment_dir / name
        if path.is_symlink() or not path.is_file():
            raise MatrixError(f"missing compiled matrix artifact: {name}")
        docs[name] = json.loads(path.read_text(encoding="utf-8"))
    runbook = experiment_dir / "RUNBOOK.md"
    if runbook.is_symlink() or not runbook.is_file():
        raise MatrixError("missing compiled matrix artifact: RUNBOOK.md")
    request = validate_request(docs["request.json"])
    report = docs["compile-report.json"]
    manifest = docs["run-manifest.json"]
    analysis = docs["analysis-request.json"]
    request_sha = hashlib.sha256((experiment_dir / "request.json").read_bytes()).hexdigest()
    report_fields = {
            "schema", "status", "runtime_kind", "experiment_id", "errors",
            "request_sha256", "execution_authorized", "launcher_generated",
            "artifact_hashes"}
    legacy_report = (report.get("schema") == governance.COMPILE_REPORT_SCHEMA
                     and set(report) == report_fields)
    current_report = (report.get("schema") == MATRIX_COMPILE_REPORT_SCHEMA
                      and set(report) == report_fields | {"design_accounting"})
    if not legacy_report and not current_report:
        raise MatrixError("matrix compile report has an invalid field set")
    if (report["status"] != "COMPILED_REVIEW_REQUIRED"
            or report["runtime_kind"] != governance.RUNTIME_KIND
            or report["experiment_id"] != request["experiment_id"]
            or report["errors"] != [] or report["request_sha256"] != request_sha
            or report["execution_authorized"] is not False
            or report["launcher_generated"] is not False):
        raise MatrixError("matrix compile report is not a clean review-required build")
    expected_experiment_dir = _canonical_experiment_dir(
        root, request["experiment_id"], experiment_dir)
    if manifest.get("schema") != MATRIX_MANIFEST_SCHEMA or \
            manifest.get("runtime_kind") != governance.RUNTIME_KIND or \
            manifest.get("experiment_id") != request["experiment_id"] or \
            manifest.get("request_sha256") != request_sha or \
            manifest.get("execution_authorized") is not False or \
            manifest.get("common_config_sha256") != canonical_sha(request["common_config"]) or \
            manifest.get("arms") != request["arms"]:
        raise MatrixError("matrix manifest identity/state mismatch")
    _, rows = _resolve_cells(request, root)
    design_accounting = None if legacy_report else _design_accounting(rows)
    if not legacy_report and report["design_accounting"] != design_accounting:
        raise MatrixError("matrix compile report design accounting mismatch")
    expected_cells = [{key: row[key] for key in (
        "run_id", "runtime_kind", "arm_id", "phase", "trace_seed",
        "learning_seed", "pairing_key", "config_overrides", "checkpoint_lineage",
        "config_path", "config_sha256", "trace_identity_sha256", "input_sha256",
        "code_sha256", "execution_chain_sha256", "controlled_signature", "acceptance")}
                     for row in rows]
    if set(manifest) != {"schema", "runtime_kind", "experiment_id", "request_sha256",
                         "common_config_sha256", "arms", "cells", "execution_authorized"}:
        raise MatrixError("matrix manifest has an invalid field set")
    if manifest["cells"] != expected_cells:
        raise MatrixError("matrix manifest cells do not derive from request")
    expected_manifest_sha = hashlib.sha256((experiment_dir / "run-manifest.json").read_bytes()).hexdigest()
    decision_contract = _load_decision_contract(root, request)
    expected_analysis = _analysis_document(
        request, expected_manifest_sha, request_sha, rows, decision_contract,
        design_accounting)
    if analysis != expected_analysis:
        raise MatrixError("matrix analysis request does not bind the exact cohort")
    if runbook.read_text(encoding="utf-8") != _render_runbook(
            request, rows, decision_contract, design_accounting):
        raise MatrixError("matrix RUNBOOK does not derive from the request")
    paths = ["request.json", "run-manifest.json", "analysis-request.json", "RUNBOOK.md",
             *(row["config_path"] for row in rows)]
    expected_hashes = _artifact_hashes(experiment_dir, paths)
    if decision_contract is not None:
        expected_hashes[decision_contract["path"]] = decision_contract["sha256"]
        expected_hashes.update(decision_contract["bound_artifacts"])
    if report["artifact_hashes"] != expected_hashes:
        raise MatrixError("matrix compile report artifact hash map is incomplete or stale")
    for row in rows:
        config_path = experiment_dir / row["config_path"]
        _reject_symlink_ancestors(config_path, experiment_dir)
        try:
            config_path.resolve().relative_to(expected_experiment_dir)
        except ValueError as exc:
            raise MatrixError(
                f"matrix resolved config escapes experiment directory: {row['run_id']}") from exc
        if config_path.is_symlink() or not config_path.is_file():
            raise MatrixError(f"matrix config is missing or symbolic: {row['run_id']}")
        resolved = config_mod.load_config_file(str(config_path))
        if resolved != row["config"]:
            raise MatrixError(f"matrix resolved config changed: {row['run_id']}")
    artifact_hashes: dict[str, str] = {}
    for raw, digest in expected_hashes.items():
        # The compiled matrix artifacts are relative to the experiment
        # directory; decision/coverage contracts are already project-root
        # relative CODE/work paths.  Do not prepend EXPERIMENTS/<id> to the
        # latter or authorization receives phantom artifact paths.
        if raw.startswith(("CODE/", "ANALYSIS/", "EXPERIMENTS/")):
            candidate = (root / raw).resolve()
            key = raw
        else:
            candidate = (experiment_dir / raw).resolve()
            key = str(candidate.relative_to(root))
        if not candidate.is_file():
            raise MatrixError(f"matrix artifact is missing: {raw}")
        artifact_hashes[key] = digest
    # The report cannot hash itself recursively, but authorization must still
    # bind the report file as an artifact after recomputing its embedded map.
    report_relative = str((experiment_dir / "compile-report.json").resolve().relative_to(root))
    artifact_hashes[report_relative] = hashlib.sha256(
        (experiment_dir / "compile-report.json").read_bytes()).hexdigest()
    authorized = [{key: row[key] for key in (
        "run_id", "runtime_kind", "arm_id", "phase", "trace_seed",
        "learning_seed", "pairing_key", "config_path", "config_sha256",
        "trace_identity_sha256", "input_sha256", "code_sha256",
        "execution_chain_sha256", "controlled_signature", "checkpoint_lineage",
        "acceptance")}
                  for row in rows]
    for row in authorized:
        row["config_path"] = str(
            (experiment_dir / row["config_path"]).resolve().relative_to(root))
    if len(authorized) != len(request["cells"]) or \
            {row["run_id"] for row in authorized} != {cell["run_id"] for cell in request["cells"]}:
        raise MatrixError("authorized matrix cohort is not exactly the planned cells")
    return request["experiment_id"], artifact_hashes, authorized
