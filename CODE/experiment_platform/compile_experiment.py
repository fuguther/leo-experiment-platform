#!/usr/bin/env python3
"""Compile an Agent experiment request into immutable, reviewable run plans.

This standard-library compiler never launches a simulation and never grants
execution authorization.  It intentionally fails closed when a request cannot
be represented faithfully by the retained runtime.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parents[1]
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
EXPERIMENT_ID = re.compile(r"^EXP-[A-Za-z0-9_-]+$")
FIXED_ARTIFACTS = {
    "run_trace/run_meta.json",
    "config_used.json",
    "artifact_manifest.json",
}
FIXED_COMPLETION = {
    "natural_end=true",
    "interrupted=false",
    "run identity and config hash match the manifest",
    "all required artifacts exist",
    "artifact_manifest hashes every required artifact except itself",
}
LOCAL_CAPABILITIES = {"local_observation", "local_queue", "neighbor_link_state"}
LEARNING_PATHINGS = {"Deep Q-Learning", "Q-Learning"}
NON_LEARNING_DORMANT_CONFIG_PATHS = [
    "routing.mode",
    "routing.eval_only",
    "routing.true_ddqn",
    "checkpoint.q_network",
    "checkpoint.q_target",
    "checkpoint.replay_buffer",
    "checkpoint.path_credit_mixer",
    "checkpoint.path_credit_replay",
    "hyperparameters",
    "rewards",
    "credit",
    "path_credit",
    "mappo",
    "csr",
]
EXECUTION_BOUNDARY = {
    "remote_workspace": "/data/论文/leo-direct-sim",
    "remote_code": "/data/论文/leo-direct-sim/CODE",
    "remote_results": "/data/论文/leo-direct-sim/CODE/Results",
    "forbidden_legacy_roots": [
        "/data/论文/MA-DRL_Routing_Simulator-main",
        "/data/论文/MA-DRL_Routing_Simulator-main/Results",
    ],
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def set_dotted(tree: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cursor = tree
    for part in parts[:-1]:
        next_value = cursor.setdefault(part, {})
        if not isinstance(next_value, dict):
            raise ValueError(f"parameter path crosses a scalar: {dotted}")
        cursor = next_value
    cursor[parts[-1]] = value


def get_dotted(tree: dict[str, Any], dotted: str, default: Any = None) -> Any:
    cursor: Any = tree
    for part in dotted.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def delete_dotted(tree: dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    cursor: Any = tree
    for part in parts[:-1]:
        if not isinstance(cursor, dict) or part not in cursor:
            return
        cursor = cursor[part]
    if isinstance(cursor, dict):
        cursor.pop(parts[-1], None)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def resolve_profile(profiles: dict[str, Any], profile_id: str, stack: tuple[str, ...] = ()) -> dict[str, Any]:
    raw = profiles.get("profiles", {}).get(profile_id)
    if not isinstance(raw, dict):
        raise ValueError(f"unknown base_profile: {profile_id}")
    if profile_id in stack:
        raise ValueError(f"profile inheritance cycle: {' -> '.join((*stack, profile_id))}")
    parent_id = raw.get("extends")
    if parent_id is None:
        if not isinstance(raw.get("config"), dict):
            raise ValueError(f"profile {profile_id} lacks config")
        return copy.deepcopy(raw)
    if not isinstance(parent_id, str) or not parent_id:
        raise ValueError(f"profile {profile_id} has invalid extends")
    overrides = raw.get("config_overrides", {})
    if not isinstance(overrides, dict):
        raise ValueError(f"profile {profile_id} config_overrides must be an object")
    parent = resolve_profile(profiles, parent_id, (*stack, profile_id))
    resolved = copy.deepcopy(parent)
    resolved.update({key: copy.deepcopy(value) for key, value in raw.items() if key not in {"extends", "config_overrides"}})
    resolved["config"] = deep_merge(parent["config"], overrides)
    resolved["resolved_from"] = [*parent.get("resolved_from", [parent_id]), profile_id]
    return resolved


def type_ok(value: Any, kind: str | list[str]) -> bool:
    if isinstance(kind, list):
        return any(type_ok(value, item) for item in kind)
    checks = {
        "null": lambda v: v is None,
        "boolean": lambda v: isinstance(v, bool),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "string": lambda v: isinstance(v, str),
        "array": lambda v: isinstance(v, list),
        "object": lambda v: isinstance(v, dict),
    }
    return checks.get(kind, lambda _v: True)(value)


def schema_errors(value: Any, schema: dict[str, Any], path: str = "$", root: dict[str, Any] | None = None) -> list[str]:
    """Validate the request against the checked-in focused JSON schema.

    This supports the schema keywords used by experiment-request.schema.json;
    unsupported keywords are not silently treated as validation evidence.
    """
    root = root or schema
    errors: list[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/$defs/"):
            return [f"{path}: unsupported schema reference {ref}"]
        schema = root["$defs"][ref.rsplit("/", 1)[-1]]
    kind = schema.get("type")
    if kind is not None and not type_ok(value, kind):
        return [f"{path}: expected {kind}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: string is shorter than {schema['minLength']}")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{path}: does not match required pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{path}: needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: allows at most {schema['maxItems']} items")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{path}: items must be unique")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, schema["items"], f"{path}[{index}]", root))
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{path}: needs at least {schema['minProperties']} properties")
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key}")
        properties = schema.get("properties", {})
        property_name_schema = schema.get("propertyNames")
        if isinstance(property_name_schema, dict):
            for key in value:
                errors.extend(schema_errors(key, property_name_schema, f"{path}.<propertyName>", root))
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}: unexpected property {key}")
        elif isinstance(schema.get("additionalProperties"), dict):
            for key in value:
                if key not in properties:
                    errors.extend(schema_errors(value[key], schema["additionalProperties"], f"{path}.{key}", root))
        for key, child in properties.items():
            if key in value:
                errors.extend(schema_errors(value[key], child, f"{path}.{key}", root))
    for condition in schema.get("allOf", []):
        if not isinstance(condition, dict):
            errors.append(f"{path}: unsupported non-object allOf condition")
            continue
        if "if" in condition:
            if not schema_errors(value, condition["if"], path, root) and "then" in condition:
                errors.extend(schema_errors(value, condition["then"], path, root))
            elif "else" in condition:
                errors.extend(schema_errors(value, condition["else"], path, root))
        else:
            errors.extend(schema_errors(value, condition, path, root))
    return errors


def condition_active(expression: str | None, config: dict[str, Any]) -> bool:
    if not expression:
        return True
    expression = expression.strip()
    if expression == "value is not null":
        return True
    match = re.fullmatch(r"([A-Za-z0-9_.]+)\s+(==|!=)\s+(.+)", expression)
    if match:
        path, operator, raw = match.groups()
        literal = raw.strip()
        expected: Any = {"true": True, "false": False, "none": "none", "null": None}.get(literal, literal)
        if isinstance(expected, str):
            try:
                expected = float(expected) if "." in expected else int(expected)
            except ValueError:
                pass
        actual = get_dotted(config, path)
        return (actual == expected) if operator == "==" else (actual != expected)
    match = re.fullmatch(r"([A-Za-z0-9_.]+)\s+in\s+\[([^]]+)\]", expression)
    if match:
        path, raw_items = match.groups()
        return get_dotted(config, path) in {item.strip() for item in raw_items.split(",")}
    return False


def derive_capabilities(config: dict[str, Any]) -> dict[str, list[str]]:
    train = set(LOCAL_CAPABILITIES)
    evaluation = set(LOCAL_CAPABILITIES)
    deployment = set(LOCAL_CAPABILITIES)
    if get_dotted(config, "simulation.pathing") not in LEARNING_PATHINGS:
        train.clear()
    if get_dotted(config, "state.mode") in {"c2", "c3", "c4", "c5", "c6", "c7"}:
        train.add("k_hop_queue_state")
        evaluation.add("k_hop_queue_state")
        deployment.add("k_hop_queue_state")
    if get_dotted(config, "state.stale_steps", 0) > 0:
        train.add("stale_neighbor_state")
        evaluation.add("stale_neighbor_state")
        deployment.add("stale_neighbor_state")
    if get_dotted(config, "state.update_interval_s", 0) > 0:
        train.add("timed_neighbor_state")
        evaluation.add("timed_neighbor_state")
        deployment.add("timed_neighbor_state")
    if get_dotted(config, "temporal.mode", "none") != "none":
        train.add("temporal_history")
        evaluation.add("temporal_history")
        deployment.add("temporal_history")
    if get_dotted(config, "path_credit.enabled", False):
        train.add("episode_trajectory_return")
    if get_dotted(config, "mappo.centralized_critic", False):
        train |= {"global_queue_state", "full_topology"}
    if get_dotted(config, "simulation.pathing") == "oracle_global_dijkstra":
        evaluation |= {"global_queue_state", "full_topology"}
        deployment |= {"global_queue_state", "full_topology"}
    elif get_dotted(config, "simulation.pathing") == "slant_range":
        # This baseline is not a local-policy router. It computes a weighted
        # shortest path over the complete current graph and reads every
        # considered edge's slant_range. Do not retain the generic local-agent
        # capabilities, which are dormant for this pathing.
        evaluation = {"full_topology", "global_link_slant_range"}
        deployment = {"full_topology", "global_link_slant_range"}
    return {name: sorted(values) for name, values in (("train", train), ("evaluation", evaluation), ("deployment", deployment))}


def derive_method_family(config: dict[str, Any]) -> str:
    pathing = get_dotted(config, "simulation.pathing")
    if pathing != "Deep Q-Learning":
        return str(pathing).lower().replace(" ", "_")
    if get_dotted(config, "path_credit.enabled", False):
        return "ddqn_path_credit"
    mappo_mode = get_dotted(config, "mappo.mode", "none")
    if mappo_mode != "none":
        return f"ddqn_mappo_{mappo_mode}"
    if get_dotted(config, "csr.mode", "off") != "off":
        return "ddqn_csr"
    credit = get_dotted(config, "credit.method", "none")
    if credit != "none":
        return f"ddqn_{credit}"
    return str(get_dotted(config, "routing.mode", "ddqn"))


def derive_execution_semantics(config: dict[str, Any]) -> dict[str, Any]:
    pathing = str(get_dotted(config, "simulation.pathing", ""))
    if pathing in LEARNING_PATHINGS:
        evaluating = bool(get_dotted(config, "routing.eval_only", False))
        return {
            "kind": "learning",
            "run_phase": "evaluation" if evaluating else "training",
            "active_method_parameter": "simulation.pathing",
            "dormant_config_paths": [],
            "optimizer_activity_expected": not evaluating,
        }
    return {
        "kind": "non_learning",
        "run_phase": "non_learning",
        "active_method_parameter": "simulation.pathing",
        "dormant_config_paths": NON_LEARNING_DORMANT_CONFIG_PATHS,
        "optimizer_activity_expected": False,
    }


def scenario_identity(catalog: dict[str, Any], profiles_path: Path, catalog_path: Path) -> dict[str, Any]:
    code_files = sorted(
        f"CODE/{path.name}"
        for path in (PROJECT_ROOT / "CODE").glob("*.py")
        if path.is_file()
    )
    files = code_files + [
        "ANALYSIS/paired_analysis.py",
        "CODE/Gateways.csv",
        "CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif",
        "CODE/scripts/remote/deployment_guard.py",
        "CODE/scripts/remote/common.sh",
        "CODE/scripts/remote/pull-results-remote.sh",
        "CODE/scripts/remote/push-remote.sh",
        "CODE/scripts/remote/remote_job.py",
        "CODE/scripts/remote/run-remote.sh",
        "CODE/scripts/remote/status-remote.sh",
        "CODE/scripts/remote/verify-pulled-run.py",
        "CODE/experiment_platform/authorize_experiment.py",
        "CODE/experiment_platform/parameter-catalog.json",
        "CODE/experiment_platform/profiles.json",
    ]
    file_hashes = {path: sha256(PROJECT_ROOT / path) for path in files}
    locked = {
        item["path"]: item.get("default")
        for item in catalog["parameters"]
        if item.get("status") == "locked_constant"
    }
    return {
        "source_and_input_sha256": file_hashes,
        "locked_constants_sha256": canonical_sha(locked),
        "catalog_sha256": sha256(catalog_path),
        "profiles_sha256": sha256(profiles_path),
    }


def external_input_identity(config: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Hash every configured external input that can change a run's meaning."""
    hashes: dict[str, str] = {}
    errors: list[str] = []
    paths = [
        "traffic.config_path",
        "traffic.bursts_config_path",
        "traffic.diurnal_config_path",
        "traffic.csv_path",
        "checkpoint.q_network",
        "checkpoint.q_target",
        "checkpoint.replay_buffer",
        "checkpoint.path_credit_mixer",
        "checkpoint.path_credit_replay",
    ]
    for config_path in paths:
        raw = get_dotted(config, config_path, "")
        if not raw:
            continue
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / "CODE" / candidate
        candidate = candidate.resolve()
        if not candidate.is_file():
            errors.append(f"configured external input does not exist: {config_path}={raw}")
            continue
        hashes[config_path] = sha256(candidate)
    return hashes, errors


def validate_request(
    request: dict[str, Any],
    schema: dict[str, Any],
    catalog: dict[str, Any],
    profiles: dict[str, Any],
    metric_catalog: dict[str, Any],
) -> tuple[list[str], list[str]]:
    errors = schema_errors(request, schema)
    warnings: list[str] = []
    if errors:
        return errors, warnings

    identity, research, design = request["identity"], request["research"], request["design"]
    if EXPERIMENT_ID.fullmatch(identity["experiment_id"]) is None:
        errors.append("identity.experiment_id is unsafe")
    for key in ("question", "hypothesis", "falsification_condition"):
        if len(research[key].strip()) < 10:
            errors.append(f"research.{key} is too vague")

    profile_id = design["base_profile"]
    try:
        profile = resolve_profile(profiles, profile_id)
    except ValueError as exc:
        errors.append(str(exc))
        return errors, warnings
    role = design["intended_role"]
    if role == "confirmatory" and profile.get("status") != "PAPER_ELIGIBLE":
        errors.append(f"confirmatory design cannot use profile status {profile.get('status')}")
    if role == "confirmatory" and get_dotted(profile["config"], "simulation.fast", False):
        errors.append("confirmatory design cannot use simulation.fast")
    if request["execution"]["resume_mode"] in {"warm_start", "exact_resume"}:
        errors.append(f"{request['execution']['resume_mode']} is blocked: full compatible training state restoration is not implemented")
    orchestration = request["execution"].get("orchestration")
    if orchestration is not None:
        for field in ("driver", "plan"):
            relative = Path(orchestration[field])
            candidate = (PROJECT_ROOT / relative).resolve()
            try:
                candidate.relative_to(PROJECT_ROOT.resolve())
            except ValueError:
                errors.append(f"execution.orchestration.{field} escapes the project root")
                continue
            if relative.is_absolute() or ".." in relative.parts or not candidate.is_file():
                errors.append(f"execution.orchestration.{field} is not a checked-in project file")
    metric_specs = {item["id"]: item for item in metric_catalog["metrics"]}
    primary_metric = design["primary_metric"]
    if primary_metric not in metric_specs:
        errors.append(f"primary_metric is not produced by the formal bundle: {primary_metric}")
    elif not metric_specs[primary_metric].get("eligible_as_primary", False):
        errors.append(f"metric is not eligible as a primary endpoint: {primary_metric}")
    unknown_secondary = sorted(set(design.get("secondary_metrics", [])) - set(metric_specs))
    if unknown_secondary:
        errors.append(f"secondary_metrics are not produced by the formal bundle: {unknown_secondary}")

    factor_changed = set(design["factor_changed"])
    coupled = set(design.get("coupled_parameters", []))
    if design["one_change_policy"] == "strict":
        if len(factor_changed) != 1:
            errors.append("strict one_change_policy requires exactly one factor_changed path")
        if coupled:
            errors.append("strict one_change_policy forbids coupled_parameters; use exploratory_multi_factor")
    else:
        warnings.append("multi-factor exploratory design cannot support single-factor causal claims")
    parameter_specs = {item["path"]: item for item in catalog["parameters"]}
    for path in factor_changed | coupled:
        if path not in parameter_specs:
            errors.append(f"declared factor is unknown: {path}")

    arms = design["arms"]
    if role in {"diagnostic", "confirmatory"} and len(arms) < 2:
        errors.append(f"{role} design requires at least two arms")
    controls = [arm for arm in arms if arm["role"] == "control"]
    if role != "upper_bound" and len(controls) != 1:
        errors.append("design requires exactly one control arm")
    if controls and controls[0]["changes"]:
        errors.append("control arm must inherit the base profile without changes")
    arm_ids: set[str] = set()
    for arm in arms:
        arm_id = arm["id"]
        if SAFE_ID.fullmatch(arm_id) is None:
            errors.append(f"unsafe arm id: {arm_id}")
        if arm_id in arm_ids:
            errors.append(f"duplicated arm id: {arm_id}")
        arm_ids.add(arm_id)
        undeclared = sorted(set(arm["changes"]) - factor_changed - coupled)
        if undeclared:
            errors.append(f"arm {arm_id} changes undeclared factors: {undeclared}")
        if arm["role"] == "upper_bound" and role != "upper_bound":
            errors.append(f"arm {arm_id}: upper_bound arm requires experiment intended_role=upper_bound")
        for path, value in arm["changes"].items():
            spec = parameter_specs.get(path)
            if spec is None:
                errors.append(f"arm {arm_id} uses unknown parameter: {path}")
                continue
            if spec.get("status") in {"declared_unwired", "env_only", "locked_constant", "sealed"}:
                errors.append(f"arm {arm_id} cannot change {path}: status={spec.get('status')}")
            if not type_ok(value, spec["type"]):
                errors.append(f"arm {arm_id} {path} expects {spec['type']}")
            if "enum" in spec and value not in spec["enum"]:
                errors.append(f"arm {arm_id} {path} must be one of {spec['enum']}")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if "minimum" in spec and value < spec["minimum"]:
                    errors.append(f"arm {arm_id} {path} below minimum {spec['minimum']}")
                if "maximum" in spec and value > spec["maximum"]:
                    errors.append(f"arm {arm_id} {path} above maximum {spec['maximum']}")
        lineage = arm["checkpoint_lineage"]
        if lineage["mode"] == "new_training" and (lineage["source_run_id"] is not None or lineage["source_sha256"] is not None):
            errors.append(f"arm {arm_id}: new_training lineage cannot name a source checkpoint")
        if lineage["mode"] == "evaluation_only" and (not lineage["source_run_id"] or not lineage["source_sha256"]):
            errors.append(f"arm {arm_id}: evaluation_only lineage requires source_run_id and source_sha256")
        if lineage["mode"] == "not_applicable" and (lineage["source_run_id"] is not None or lineage["source_sha256"] is not None):
            errors.append(f"arm {arm_id}: not_applicable lineage cannot name a source checkpoint")

    analysis = request["analysis"]
    if analysis["minimum_effect"]["metric"] != design["primary_metric"]:
        errors.append("analysis.minimum_effect.metric must equal design.primary_metric")
    for contrast in analysis["planned_contrasts"]:
        for side in ("left_arm", "right_arm"):
            if contrast[side] not in arm_ids:
                errors.append(f"planned contrast {contrast['name']} references unknown {side}={contrast[side]}")
        if contrast["left_arm"] == contrast["right_arm"]:
            errors.append(f"planned contrast {contrast['name']} compares an arm with itself")
    for field, expected_role in (("negative_control", "negative_control"), ("target_ablation", "ablation")):
        planned = analysis[field]
        if planned["status"] == "included":
            matches = [arm for arm in arms if arm["id"] == planned["arm_id"] and arm["role"] == expected_role]
            if not matches:
                errors.append(f"analysis.{field} must reference an arm with role={expected_role}")
        elif planned["arm_id"] is not None:
            errors.append(f"analysis.{field}.arm_id must be null when status=not_included")
    calibration = analysis.get("calibration_decision")
    if calibration is not None:
        unknown_candidates = sorted(set(calibration["candidate_arms"]) - arm_ids)
        if unknown_candidates:
            errors.append(f"analysis.calibration_decision references unknown arms: {unknown_candidates}")
        if calibration["burst_reference_arm"] not in calibration["candidate_arms"]:
            errors.append("analysis.calibration_decision.burst_reference_arm must be a candidate arm")
        if calibration["min_seed_agreement"] > len(design["planned_seeds"]):
            errors.append("analysis.calibration_decision.min_seed_agreement exceeds planned seed count")

    required_artifacts = set(request["outputs"]["required_artifacts"])
    required_completion = set(request["outputs"]["completion_contract"])
    if not FIXED_ARTIFACTS <= required_artifacts:
        errors.append(f"required_artifacts must include {sorted(FIXED_ARTIFACTS)}")
    if role in {"confirmatory", "diagnostic"} and "experiment_bundle/summary_metrics.csv" not in required_artifacts:
        errors.append("diagnostic/confirmatory requests require experiment_bundle/summary_metrics.csv")
    if not FIXED_COMPLETION <= required_completion:
        errors.append(f"completion_contract must include {sorted(FIXED_COMPLETION)}")
    return errors, warnings


def arm_constraints(config: dict[str, Any], request: dict[str, Any], arm: dict[str, Any], specs: dict[str, Any]) -> tuple[list[str], list[str], dict[str, list[str]]]:
    arm_id = arm["id"]
    errors: list[str] = []
    warnings: list[str] = []
    if get_dotted(config, "simulation.pathing") == "oracle_global_dijkstra" and arm["role"] != "upper_bound":
        errors.append(f"arm {arm_id}: oracle_global_dijkstra requires arm.role=upper_bound")
    if get_dotted(config, "routing.eval_only", False):
        if not get_dotted(config, "checkpoint.q_network") or not get_dotted(config, "checkpoint.q_target"):
            errors.append(f"arm {arm_id}: eval_only requires q_network and q_target checkpoints")
    lineage = arm["checkpoint_lineage"]
    semantics = derive_execution_semantics(config)
    if arm["execution_kind"] != semantics["kind"]:
        errors.append(
            f"arm {arm_id}: execution_kind={arm['execution_kind']} does not match resolved "
            f"simulation.pathing kind {semantics['kind']}"
        )
    effective_seconds = get_dotted(config, "simulation.time_limit")
    if effective_seconds is None:
        effective_seconds = get_dotted(config, "simulation.test_length")
    training_seconds = arm["training_budget"]["simulated_seconds"]
    evaluation_seconds = arm["evaluation_budget"]["simulated_seconds"]
    execution_seconds = arm["execution_budget"]["simulated_seconds"]
    if semantics["kind"] == "non_learning":
        if get_dotted(config, "routing.eval_only", False):
            errors.append(f"arm {arm_id}: non-learning pathing requires routing.eval_only=false")
        checkpoint_paths = (
            "checkpoint.q_network", "checkpoint.q_target", "checkpoint.replay_buffer",
            "checkpoint.path_credit_mixer", "checkpoint.path_credit_replay",
        )
        active_checkpoints = [path for path in checkpoint_paths if str(get_dotted(config, path, "")).strip()]
        if active_checkpoints:
            errors.append(f"arm {arm_id}: non-learning pathing forbids checkpoint inputs: {active_checkpoints}")
        non_neutral = []
        if get_dotted(config, "path_credit.enabled", False):
            non_neutral.append("path_credit.enabled")
        if get_dotted(config, "mappo.mode", "none") != "none":
            non_neutral.append("mappo.mode")
        if get_dotted(config, "mappo.centralized_critic", False):
            non_neutral.append("mappo.centralized_critic")
        if get_dotted(config, "csr.mode", "off") != "off":
            non_neutral.append("csr.mode")
        if get_dotted(config, "credit.method", "none") != "none":
            non_neutral.append("credit.method")
        if get_dotted(config, "execution.fast_train", False):
            non_neutral.append("execution.fast_train")
        if non_neutral:
            errors.append(f"arm {arm_id}: non-learning pathing requires neutral learning mechanisms: {non_neutral}")
        if lineage["mode"] != "not_applicable":
            errors.append(f"arm {arm_id}: non-learning run requires checkpoint_lineage.mode=not_applicable")
        if training_seconds != 0 or evaluation_seconds != 0 or execution_seconds != effective_seconds:
            errors.append(
                f"arm {arm_id}: non-learning run requires training_budget=0, evaluation_budget=0, "
                f"and execution_budget.simulated_seconds={effective_seconds}"
            )
    elif get_dotted(config, "routing.eval_only", False):
        if get_dotted(config, "simulation.pathing") == "Q-Learning":
            errors.append(f"arm {arm_id}: Q-Learning evaluation-only execution is not wired")
        if lineage["mode"] != "evaluation_only":
            errors.append(f"arm {arm_id}: eval_only config requires checkpoint_lineage.mode=evaluation_only")
        if training_seconds != 0 or evaluation_seconds != effective_seconds:
            errors.append(
                f"arm {arm_id}: evaluation run requires training_budget=0 and "
                f"evaluation_budget.simulated_seconds={effective_seconds}"
            )
        if execution_seconds != 0:
            errors.append(f"arm {arm_id}: learning evaluation run requires execution_budget=0")
    else:
        if lineage["mode"] != "new_training":
            errors.append(f"arm {arm_id}: training config requires checkpoint_lineage.mode=new_training")
        if training_seconds != effective_seconds or evaluation_seconds != 0 or execution_seconds != 0:
            errors.append(
                f"arm {arm_id}: training run requires training_budget.simulated_seconds={effective_seconds}, "
                "evaluation_budget=0, and execution_budget=0; evaluation must be a separate checkpoint-bound arm"
            )
    if get_dotted(config, "path_credit.enabled", False) and get_dotted(config, "mappo.mode", "none") != "none":
        errors.append(f"arm {arm_id}: path_credit and mappo are mutually exclusive")
    if get_dotted(config, "path_credit.enabled", False) and get_dotted(config, "routing.eval_only", False):
        errors.append(
            f"arm {arm_id}: path_credit is training-only and must be disabled in evaluation configs; "
            "bind evaluation to the trained Q-network checkpoint lineage instead"
        )
    if get_dotted(config, "path_credit.enabled", False) and get_dotted(config, "csr.mode", "off") != "off":
        errors.append(f"arm {arm_id}: path_credit and csr are mutually exclusive")
    if get_dotted(config, "credit.method", "none") != "none" and get_dotted(config, "path_credit.enabled", False):
        errors.append(f"arm {arm_id}: fixed credit and path_credit are mutually exclusive")
    state_mode = get_dotted(config, "state.mode", "")
    if state_mode in {"c2", "c3", "c4", "c5", "c6", "c7"} and get_dotted(config, "state.vis_k", 0) < 1:
        errors.append(f"arm {arm_id}: state.vis_k must be >=1 for {state_mode}")
    if state_mode in {"c6", "c7"} and get_dotted(config, "credit.method", "none") != "none":
        errors.append(
            f"arm {arm_id}: {state_mode} currently requires credit.method=none because "
            "next-action masks are not carried by the n-step/TD-lambda adapters"
        )
    if get_dotted(config, "hyperparameters.min_epsilon", 0) > get_dotted(config, "hyperparameters.max_epsilon", 1):
        errors.append(f"arm {arm_id}: min_epsilon exceeds max_epsilon")
    if get_dotted(config, "hyperparameters.buffer_size", 0) < get_dotted(config, "hyperparameters.batch_size", 0):
        errors.append(f"arm {arm_id}: buffer_size is smaller than batch_size")
    if get_dotted(config, "simulation.fast", False):
        warnings.append(f"arm {arm_id}: fast mode is not eligible for formal evidence")
    if get_dotted(config, "execution.fail_closed", True) is not True:
        errors.append(f"arm {arm_id}: execution.fail_closed must be true for Agent experiments")
    fast_train = get_dotted(config, "execution.fast_train", False)
    if fast_train:
        incompatible = []
        if get_dotted(config, "routing.mode") != "ddqn":
            incompatible.append("routing.mode")
        if get_dotted(config, "mappo.mode", "none") != "none":
            incompatible.append("mappo.mode")
        if get_dotted(config, "credit.method", "none") != "none":
            incompatible.append("credit.method")
        if get_dotted(config, "path_credit.enabled", False):
            incompatible.append("path_credit.enabled")
        if incompatible:
            errors.append(f"arm {arm_id}: execution.fast_train is incompatible with {incompatible}")
        warnings.append(f"arm {arm_id}: fast_train requires a post-run effective-equivalence receipt")
    if get_dotted(config, "execution.inference_backend", "keras") != "keras":
        warnings.append(f"arm {arm_id}: non-keras inference requires a post-run effective-equivalence receipt")
    actual_method_family = derive_method_family(config)
    if arm["method_family"] != actual_method_family:
        errors.append(
            f"arm {arm_id}: method_family={arm['method_family']} does not match resolved method {actual_method_family}"
        )

    for changed in arm["changes"]:
        spec = specs[changed]
        if spec.get("enabled_when") and not condition_active(spec["enabled_when"], config):
            errors.append(f"arm {arm_id}: changed parameter {changed} is inactive under {spec['enabled_when']}")

    effective = derive_capabilities(config)
    declared = arm["information_contract"]
    for phase in ("train", "evaluation", "deployment"):
        if set(declared[phase]) != set(effective[phase]):
            errors.append(f"arm {arm_id}: declared {phase} capabilities do not match effective capabilities; declared={sorted(declared[phase])}, effective={effective[phase]}")
    if arm["role"] != "upper_bound" and set(effective["evaluation"]) != set(effective["deployment"]):
        errors.append(f"arm {arm_id}: evaluation/deployment information mismatch")
    return errors, warnings, effective


def render_runbook(request: dict[str, Any], manifest: dict[str, Any]) -> str:
    experiment_id = request["identity"]["experiment_id"]
    if manifest.get("runtime_kind") == "legacy_gateway_external":
        planned = [row["run_id"] for row in manifest["planned_runs"]]
        lines = [
            "# Agent runbook",
            "",
            "> **EXTERNAL LEGACY COMPATIBILITY — NOT RUNNABLE IN THIS REPOSITORY**",
            "",
            "This generated file is an external legacy platform compatibility artifact.",
            "Its `*.config.json` outputs require the external old platform runtime and",
            "cannot be executed from this repository. `execution_authorized=false`;",
            "compilation does not grant authorization or create a current launch route.",
            "",
            f"- experiment: `{experiment_id}`",
            f"- planned legacy runs: `{len(planned)}`",
            "- config family: `resolved/*.config.json`",
            "- current in-repository formal runtime: `leo_sim_v2` only",
            "",
            "Do not invoke the in-repository formal runner for these configs and do not",
            "reconstruct the absent legacy runtime. Reviving a legacy experiment requires",
            "a separately governed external-platform work package and a new full review.",
            "",
            "## Planned compatibility artifacts",
            "",
        ]
        lines.extend(f"- `{run_id}`" for run_id in planned)
        return "\n".join(lines) + "\n"
    lines = [
        "# Agent runbook",
        "",
        "This generated file is part of the reviewed experiment artifact set. Run commands from the project root.",
        "",
        "## Fixed execution boundary",
        "",
        f"- remote workspace: `{EXECUTION_BOUNDARY['remote_workspace']}`",
        f"- remote code: `{EXECUTION_BOUNDARY['remote_code']}`",
        f"- remote results: `{EXECUTION_BOUNDARY['remote_results']}`",
        "- forbidden legacy roots:",
    ]
    lines.extend(f"  - `{path}`" for path in EXECUTION_BOUNDARY["forbidden_legacy_roots"])
    lines.extend([
        "",
        "Any symlink or resolved workspace, result, log, status, config, or authorization path that reaches a forbidden root is a hard BLOCK.",
        "",
        "## Hash meanings",
        "",
        "- `config_sha256` is the SHA256 of compact canonical JSON semantics: sorted keys with compact separators.",
        "- review `artifact_hashes` and ordinary `sha256` fields are SHA256 of exact file bytes.",
        "- These hash classes bind different serializations and must not be compared as though they were interchangeable.",
    ])
    orchestration = request.get("execution", {}).get("orchestration")
    if orchestration is not None:
        lines.extend([
            "",
            "## Cross-experiment master execution",
            "",
            "This experiment is one component of a preregistered cross-experiment A/B. Do not execute this file's component runs separately or use the generic paired analyzer.",
            "",
            "Run the reviewed master driver only after every component experiment has a valid authorization:",
            "",
            "```bash",
            f"python3 {orchestration['driver']} --plan {orchestration['plan']} --cpu-list {orchestration['cpu_list']}",
            "```",
            "",
            "The driver enforces the registered 16-run order, four-CPU affinity, formal pull/verification, and dedicated cross-experiment analysis. Any failure is retained as BLOCK.",
            "",
        ])
        return "\n".join(lines)
    lines.extend([
        "",
        "## Formal VM runs and analysis",
        "",
        "Run the complete block in one Bash shell. It has a hard per-run timeout and binds every status query to the launch nonce returned by that exact launcher invocation.",
        "",
        "```bash",
        "set -euo pipefail",
        "read_launch_value() {",
        "  local key=$1 payload=$2 value",
        "  value=$(printf '%s\\n' \"$payload\" | awk -F= -v key=\"$key\" '$1==key {sub(/^[^=]*=/, \"\"); print; found=1} END {if (!found) exit 1}')",
        "  [[ -n \"$value\" ]]",
        "  printf '%s' \"$value\"",
        "}",
        "",
    ])
    result_vars: list[str] = []
    for index, row in enumerate(manifest["planned_runs"], start=1):
        session_base = f"leo_vm_smoke_{index:02d}"
        result_var = f"RUN_{index}_DIR"
        result_vars.append(result_var)
        prefix = f"RUN_{index}"
        config_rel = f"EXPERIMENTS/{experiment_id}/{row['config_json']}"
        authorization_rel = f"EXPERIMENTS/{experiment_id}/authorization.json"
        lines.extend([
            f"# {row['run_id']}",
            f"{prefix}_LAUNCH=$(CODE/scripts/remote/run-remote.sh --session {session_base} --config {config_rel} --authorization {authorization_rel} --no-monitor --bundle-stages summary)",
            f"{prefix}_SESSION=$(read_launch_value remote_session \"${{{prefix}_LAUNCH}}\")",
            f"{prefix}_NONCE=$(read_launch_value launch_nonce \"${{{prefix}_LAUNCH}}\")",
            f"{prefix}_RUN_ID=$(read_launch_value run_id \"${{{prefix}_LAUNCH}}\")",
            f"{prefix}_CONFIG_SHA=$(read_launch_value config_sha256 \"${{{prefix}_LAUNCH}}\")",
            f"{prefix}_AUTH_SHA=$(read_launch_value authorization_sha256 \"${{{prefix}_LAUNCH}}\")",
            f"[[ \"${{{prefix}_RUN_ID}}\" == \"{row['run_id']}\" ]]",
            f"[[ \"${{{prefix}_CONFIG_SHA}}\" == \"{row['config_sha256']}\" ]]",
            f"[[ \"${{{prefix}_NONCE}}\" =~ ^[a-f0-9]{{32}}$ && \"${{{prefix}_AUTH_SHA}}\" =~ ^[a-f0-9]{{64}}$ ]]",
            f"{prefix}_DEADLINE=$((SECONDS + 900))",
            f"{prefix}_TERMINAL=0",
            f"{prefix}_STATUS=''",
            f"while (( SECONDS < {prefix}_DEADLINE )); do",
            f"  if {prefix}_STATUS=$(CODE/scripts/remote/status-remote.sh --session \"${{{prefix}_SESSION}}\" --launch-nonce \"${{{prefix}_NONCE}}\"); then",
            f"    if printf '%s' \"${{{prefix}_STATUS}}\" | jq -e --arg s \"${{{prefix}_SESSION}}\" --arg n \"${{{prefix}_NONCE}}\" --arg r \"${{{prefix}_RUN_ID}}\" --arg c \"${{{prefix}_CONFIG_SHA}}\" --arg a \"${{{prefix}_AUTH_SHA}}\" 'select(.session_name==$s and .launch_nonce==$n and .run_id==$r and .config_sha256==$c and .authorization_sha256==$a and (.status==\"success\" or .status==\"failed\") and .tmux_state==\"missing\")' >/dev/null; then",
            f"      {prefix}_TERMINAL=1",
            "      break",
            "    fi",
            "  fi",
            "  sleep 5",
            "done",
            f"CODE/scripts/remote/logs-remote.sh --path \"${{{prefix}_SESSION}}.log\" --lines 160 || true",
            f"[[ \"${{{prefix}_TERMINAL}}\" -eq 1 ]] || {{ echo '{row['run_id']}: terminal status timeout' >&2; exit 124; }}",
            f"printf '%s' \"${{{prefix}_STATUS}}\" | jq -e --arg s \"${{{prefix}_SESSION}}\" --arg n \"${{{prefix}_NONCE}}\" --arg r \"${{{prefix}_RUN_ID}}\" --arg c \"${{{prefix}_CONFIG_SHA}}\" --arg a \"${{{prefix}_AUTH_SHA}}\" 'select(.session_name==$s and .launch_nonce==$n and .run_id==$r and .config_sha256==$c and .authorization_sha256==$a and .status==\"success\" and .exit_code==0 and .tmux_state==\"missing\" and (.run_attempt_id|test(\"^[a-f0-9]{{32}}$\")) and (.last_results_dir|type==\"string\" and length>0))' >/dev/null",
            f"{prefix}_ATTEMPT=$(printf '%s' \"${{{prefix}_STATUS}}\" | jq -er '.run_attempt_id')",
            f"{result_var}_REMOTE=$(printf '%s' \"${{{prefix}_STATUS}}\" | jq -er '.last_results_dir | select(startswith(\"{EXECUTION_BOUNDARY['remote_results']}/\"))')",
            f"{result_var}=${{{result_var}_REMOTE##*/}}",
            f"[[ -n \"${result_var}\" && \"${result_var}\" != _* && \"${result_var}\" != */* ]]",
            f"[[ \"${{{result_var}_REMOTE}}\" == \"{EXECUTION_BOUNDARY['remote_results']}/${{{result_var}}}\" ]]",
            f"CODE/scripts/remote/pull-results-remote.sh --run \"${result_var}\" --trace --deep",
            f"python3 CODE/scripts/remote/verify-pulled-run.py --run-id '{row['run_id']}' --config {config_rel} --authorization {authorization_rel} --launch-nonce \"${{{prefix}_NONCE}}\" --run-attempt-id \"${{{prefix}_ATTEMPT}}\" --result \"CODE/Results/${result_var}\"",
            "",
        ])
    run_args = " ".join(
        f"--run \"{row['run_id']}=CODE/Results/${{{result_vars[index]}}}\""
        for index, row in enumerate(manifest["planned_runs"])
    )
    analysis_command = (
        f"python3 ANALYSIS/paired_analysis.py --analysis EXPERIMENTS/{experiment_id}/analysis-request.json "
        f"--manifest EXPERIMENTS/{experiment_id}/run-manifest.json "
        f"--authorization EXPERIMENTS/{experiment_id}/authorization.json {run_args} "
        f"--out ANALYSIS/{experiment_id}"
    )
    lines.extend([
        analysis_command,
        f"jq -e 'select(.schema==\"analysis-manifest/v1\" and .status==\"VERIFIED\" and .errors==[])' ANALYSIS/{experiment_id}/analysis-manifest.json >/dev/null",
        "```",
        "",
        "The verifier must report `VERIFIED`; the puller refuses an existing different local byte. Any failure is preserved and requires a new revision.",
        "",
        "## Fail-closed acceptance",
        "",
        "1. The deployment receipt re-verifies a clean commit and canonical non-symlink Results root.",
        "2. All planned runs report natural_end=true, interrupted=false, exact identity, eligible effective receipt, and every required artifact hash.",
        "3. Paired analysis exits 0 and persists a reproducible VERIFIED manifest.",
        "4. With fewer than two pairs, uncertainty is not estimated; no confidence interval or scientific inference is allowed.",
        "5. Any failure is retained and requires a new revision. Never fill missing values, relax artifacts, or reuse an old result.",
        "",
    ])
    return "\n".join(lines)


def compile_request(request_path: Path, out_dir: Path) -> int:
    catalog_path = ROOT / "parameter-catalog.json"
    profiles_path = ROOT / "profiles.json"
    schema_path = ROOT / "experiment-request.schema.json"
    metric_catalog_path = ROOT / "metric-catalog.json"
    request = load_json(request_path)
    catalog = load_json(catalog_path)
    profiles_doc = load_json(profiles_path)
    schema = load_json(schema_path)
    metric_catalog = load_json(metric_catalog_path)
    errors, warnings = validate_request(request, schema, catalog, profiles_doc, metric_catalog)
    profile_id = request.get("design", {}).get("base_profile")
    try:
        profile = resolve_profile(profiles_doc, str(profile_id))
    except ValueError:
        profile = {}
    specs = {item["path"]: item for item in catalog["parameters"]}
    identity = scenario_identity(catalog, profiles_path, catalog_path)
    resolved_records: list[dict[str, Any]] = []

    if not errors:
        arm_configs: dict[str, dict[str, Any]] = {}
        effective_by_arm: dict[str, dict[str, list[str]]] = {}
        for arm in request["design"]["arms"]:
            config = copy.deepcopy(profile["config"])
            for path, value in arm["changes"].items():
                set_dotted(config, path, value)
            arm_errors, arm_warnings, effective = arm_constraints(config, request, arm, specs)
            external_hashes, external_errors = external_input_identity(config)
            errors.extend(arm_errors)
            errors.extend(f"arm {arm['id']}: {message}" for message in external_errors)
            if arm["checkpoint_lineage"]["mode"] == "evaluation_only":
                q_hash = external_hashes.get("checkpoint.q_network")
                if q_hash != arm["checkpoint_lineage"]["source_sha256"]:
                    errors.append(
                        f"arm {arm['id']}: checkpoint_lineage.source_sha256 does not match checkpoint.q_network"
                    )
            warnings.extend(arm_warnings)
            arm_configs[arm["id"]] = config
            effective_by_arm[arm["id"]] = effective
            arm["_external_input_sha256"] = external_hashes

        controls = [arm for arm in request["design"]["arms"] if arm["role"] == "control"]
        if request["design"]["one_change_policy"] == "strict" and controls:
            control_config = arm_configs[controls[0]["id"]]
            factor = request["design"]["factor_changed"][0]
            for arm in request["design"]["arms"]:
                if arm["role"] == "control":
                    continue
                if get_dotted(arm_configs[arm["id"]], factor) == get_dotted(control_config, factor):
                    errors.append(f"arm {arm['id']}: strict factor {factor} does not actually differ from control")
                stripped_control = copy.deepcopy(control_config)
                stripped_arm = copy.deepcopy(arm_configs[arm["id"]])
                delete_dotted(stripped_control, factor)
                delete_dotted(stripped_arm, factor)
                if stripped_arm != stripped_control:
                    errors.append(f"arm {arm['id']}: resolved config differs from control outside strict factor {factor}")

        if not errors:
            for arm in request["design"]["arms"]:
                for seed in request["design"]["planned_seeds"]:
                    config = copy.deepcopy(arm_configs[arm["id"]])
                    set_dotted(config, "simulation.seed", seed)
                    run_id = f"{request['identity']['experiment_id']}-{arm['id']}-s{seed}"
                    config["provenance"] = {
                        "experiment_id": request["identity"]["experiment_id"],
                        "run_id": run_id,
                        "arm_id": arm["id"],
                        "experiment_role": request["design"]["intended_role"],
                        "arm_role": arm["role"],
                        "method_family": arm["method_family"],
                        "seed": seed,
                        "scenario_identity": identity,
                        "external_input_sha256": arm["_external_input_sha256"],
                        "required_artifacts": request["outputs"]["required_artifacts"],
                        "information_contract": effective_by_arm[arm["id"]],
                        "execution_semantics": derive_execution_semantics(config),
                        "execution_boundary": EXECUTION_BOUNDARY,
                    }
                    controlled = copy.deepcopy(config)
                    delete_dotted(controlled, "simulation.seed")
                    for path in request["design"]["factor_changed"] + request["design"].get("coupled_parameters", []):
                        delete_dotted(controlled, path)
                    controlled.pop("provenance", None)
                    resolved_records.append({
                        "run_id": run_id,
                        "arm": arm,
                        "seed": seed,
                        "config": config,
                        "config_sha256": canonical_sha(config),
                        "controlled_signature": canonical_sha({"config": controlled, "information": effective_by_arm[arm["id"]]}),
                        "effective_information_contract": effective_by_arm[arm["id"]],
                    })

    report = {
        "schema": "experiment-compile-report/v2",
        "request_source": str(request_path),
        "request_snapshot": "request.json",
        "request_sha256": sha256(request_path),
        "schema_sha256": sha256(schema_path),
        "catalog_sha256": sha256(catalog_path),
        "profiles_sha256": sha256(profiles_path),
        "metric_catalog_sha256": sha256(metric_catalog_path),
        "status": "BLOCKED" if errors else "COMPILED_REVIEW_REQUIRED",
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "launcher_generated": False,
        "execution_authorized": False,
        "note": "Compilation never authorizes execution; independent design review is required.",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "request.json").write_bytes(request_path.read_bytes())
    write_json(out_dir / "compile-report.json", report)
    if errors:
        return 2

    out_root = out_dir.resolve()
    unresolved_dir = out_dir / "resolved"
    # resolved/ is compiler-owned, so never follow a caller-planted symlink
    # before cleaning stale files or writing generated configurations.
    if unresolved_dir.is_symlink():
        raise ValueError("resolved directory must not be a symbolic link")
    resolved_dir = unresolved_dir.resolve()
    try:
        relative_resolved = resolved_dir.relative_to(out_root)
    except ValueError as exc:
        raise ValueError("resolved directory escaped experiment directory") from exc
    if relative_resolved == Path("."):
        raise ValueError("resolved directory must be below experiment directory")
    resolved_dir.mkdir(parents=True, exist_ok=True)
    expected_json_names = {
        f"{row['arm']['id']}.s{row['seed']}.config.json"
        for row in resolved_records
    }
    # resolved/ is compiler-owned. Remove only prior generated config files so
    # recompiling an experiment cannot leave an obsolete YAML twin or stale run.
    for stale_path in resolved_dir.iterdir():
        if not stale_path.is_file():
            continue
        is_old_yaml_twin = stale_path.name.endswith(".config.yaml")
        is_stale_json = (
            stale_path.name.endswith(".config.json")
            and stale_path.name not in expected_json_names
        )
        if is_old_yaml_twin or is_stale_json:
            stale_path.unlink()
    for row in resolved_records:
        stem = f"{row['arm']['id']}.s{row['seed']}.config"
        json_path = (resolved_dir / f"{stem}.json").resolve()
        if resolved_dir not in json_path.parents:
            raise ValueError("resolved path escaped experiment directory")
        write_json(json_path, row["config"])
        row["config_json"] = str(json_path.relative_to(out_root))

    manifest = {
        "schema": "experiment-run-manifest/v2",
        "runtime_kind": "legacy_gateway_external",
        "experiment_id": request["identity"]["experiment_id"],
        "request_sha256": report["request_sha256"],
        "base_profile": profile_id,
        "profile_status": profile.get("status"),
        "execution_boundary": EXECUTION_BOUNDARY,
        "scenario_identity": identity,
        "resume_mode": request["execution"]["resume_mode"],
        "planned_runs": [
            {
                "run_id": row["run_id"],
                "attempt": 1,
                "arm_id": row["arm"]["id"],
                "arm_role": row["arm"]["role"],
                "method_family": row["arm"]["method_family"],
                "seed": row["seed"],
                "config_json": row["config_json"],
                "config_sha256": row["config_sha256"],
                "controlled_signature": row["controlled_signature"],
                "information_contract": row["effective_information_contract"],
                "training_budget": row["arm"]["training_budget"],
                "evaluation_budget": row["arm"]["evaluation_budget"],
                "execution_budget": row["arm"]["execution_budget"],
                "execution_semantics": derive_execution_semantics(row["config"]),
                "run_phase": derive_execution_semantics(row["config"])["run_phase"],
                "checkpoint_lineage": row["arm"]["checkpoint_lineage"],
                "external_input_sha256": row["arm"]["_external_input_sha256"],
                "status": "PLANNED",
            }
            for row in resolved_records
        ],
        "completion_contract": request["outputs"]["completion_contract"],
        "required_artifacts": request["outputs"]["required_artifacts"],
        "execution_authorized": False,
    }
    write_json(out_dir / "run-manifest.json", manifest)
    (out_dir / "RUNBOOK.md").write_text(render_runbook(request, manifest), encoding="utf-8")
    analysis = {
        "schema": "analysis-request/v2",
        "analysis_id": f"AN-{request['identity']['experiment_id'][4:]}",
        "experiment_id": request["identity"]["experiment_id"],
        "question": request["research"]["question"],
        "hypothesis": request["research"]["hypothesis"],
        "falsification_condition": request["research"]["falsification_condition"],
        "primary_metric": request["design"]["primary_metric"],
        "secondary_metrics": request["design"].get("secondary_metrics", []),
        "planned_run_ids": [row["run_id"] for row in manifest["planned_runs"]],
        "planned_runs": [
            {
                "run_id": row["run_id"],
                "arm_id": row["arm_id"],
                "seed": row["seed"],
                "config_sha256": row["config_sha256"],
                "controlled_signature": row["controlled_signature"],
            }
            for row in manifest["planned_runs"]
        ],
        "request_sha256": report["request_sha256"],
        "run_manifest_sha256": sha256(out_dir / "run-manifest.json"),
        "scenario_identity_sha256": canonical_sha(identity),
        "preregistration": request["analysis"],
        "metric_catalog_sha256": report["metric_catalog_sha256"],
        "cannot_conclude": request["research"]["cannot_conclude"],
        "status": "WAITING_FOR_VERIFIED_RUNS",
    }
    write_json(out_dir / "analysis-request.json", analysis)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return compile_request(args.request.resolve(), args.out.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
