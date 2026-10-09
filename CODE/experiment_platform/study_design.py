"""Compile the focused time-alignment design; never launch a simulator.

Profiles are ordinary simulator configs, not T1 run authorizations. Every
four-arm block fixes all non-arm parameters and binds the resolved identity.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

import yaml

from CODE.leo_sim import config as config_mod

ROOT = Path(__file__).resolve().parents[2]
ARMS = ["stale", "now", "common", "candidate"]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _set(config, key, value):
    section, field = key.split(".", 1)
    if section not in config or field not in config[section]:
        raise ValueError(f"unknown resolved configuration key: {key}")
    config[section][field] = value


def _without_arm(config):
    value = copy.deepcopy(config)
    value["time_alignment"].pop("arm")
    return value


def compile_study(contract_path, out_dir):
    contract_path, out_dir = Path(contract_path), Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise ValueError("output must be a new directory")
    spec = yaml.safe_load(contract_path.read_text())
    if spec["schema"] != "time-alignment-study/v1" or spec["arms"] != ARMS:
        raise ValueError("wrong study schema or four-arm order")
    if spec["seed"] != 7 or isinstance(spec["seed"], bool):
        raise ValueError("this development design freezes seed 7")
    base_path = ROOT / spec["base_profile"]
    base = config_mod.load_config_file(str(base_path))["config"]
    conditions = {c["id"]: c for c in spec["conditions"]}
    if len(conditions) != len(spec["conditions"]):
        raise ValueError("duplicate condition")
    cells, configs, seen, execution_blocks = [], {}, set(), []
    for block in spec["blocks"]:
        block_id = block["id"]
        if block_id in seen:
            raise ValueError("duplicate block")
        seen.add(block_id)
        config = copy.deepcopy(base)
        for key, value in spec["fixed"].items():
            _set(config, key, value)
        for key, value in conditions[block["condition"]]["overrides"].items():
            if key not in {"demand.offered_mbps", "demand.burst_multiplier",
                           "demand.burst_start_s", "demand.burst_duration_s"}:
                raise ValueError("traffic condition changes a non-traffic parameter")
            _set(config, key, value)
        config["scenario"]["seed"] = spec["seed"]
        config["time_alignment"].update(enabled=True, predictor=block["predictor"])
        config["control_plane"]["advertise_interval_s"] = block["advertise_interval_s"]
        end = config["demand"]["emission_end_s"]
        if end + spec["primary"]["deadline_s"] > config["scenario"]["duration_s"]:
            raise ValueError("observation stops before the last packet deadline")
        if config["routing"]["learning_enabled"] or config["learning"]["algorithm"] != "none":
            raise ValueError("this study does not authorize training")
        reference = None
        for arm in ARMS:
            arm_config = copy.deepcopy(config)
            arm_config["time_alignment"]["arm"] = arm
            resolved = config_mod.resolve_config(arm_config)
            invariant = _without_arm(resolved["config"])
            if reference is not None and invariant != reference:
                raise ValueError("non-arm settings differ inside a comparison block")
            reference = invariant
            cell_id = f"{block_id}-{arm}-seed-{spec['seed']}"
            configs[cell_id] = resolved["config"]
            cells.append({"cell_id": cell_id, "block": block_id,
                          "role": block["role"], "condition": block["condition"],
                          "arm": arm, "resolved_config_sha256": resolved["sha256"],
                          "profile": f"profiles/{cell_id}.yaml"})
        execution_blocks.append({
            "block": block_id, "expected_simulator_calls": 4,
            "profile": f"profiles/{block_id}-now-seed-{spec['seed']}.yaml",
            "driver": "CODE.experiment_platform.t1_tasks",
            "task": "network_alignment", "arms": ARMS,
            "capture_replay": True,
            "driver_options": {"--task": "network_alignment",
                               "--deadline-s": str(float(spec["primary"]["deadline_s"])),
                               "--window-start": str(float(config["demand"]["emission_start_s"])),
                               "--window-end": str(float(config["scenario"]["duration_s"])),
                               "--arms": ",".join(ARMS), "--capture-replay": True},
            "requires_t1_suite_launch_context": True,
            "direct_cli_execution_supported": False,
            "note": "one four-arm invocation per block, not four invocations per profile",
        })
    if len(cells) > spec["run_boundary"]["maximum_materialized_cells"]:
        raise ValueError("study cell cap exceeded")
    out_dir.mkdir()
    (out_dir / "profiles").mkdir()
    for cell in cells:
        path = out_dir / cell["profile"]
        path.write_text(yaml.safe_dump(configs[cell["cell_id"]], sort_keys=False))
        reread = config_mod.load_config_file(str(path))
        if reread["sha256"] != cell["resolved_config_sha256"]:
            raise ValueError("serialized profile identity changed")
        cell["profile_sha256"] = _sha(path)
    report = {"schema": spec["schema"], "status": "COMPILED_NOT_RUN",
              "simulator_calls": 0, "execution_authorized_by_this_file": False,
              "contract_sha256": _sha(contract_path), "base_profile_sha256": _sha(base_path),
              "compiler_sha256": _sha(__file__), "cells": cells,
              "execution_blocks": execution_blocks,
              "within_block_only_arm_changes": True,
              "trace_equivalence": "must compile once per condition/seed and verify at runtime",
              "primary": spec["primary"], "information": spec["information"],
              "diagnostics": spec["diagnostics"], "run_boundary": spec["run_boundary"]}
    (out_dir / "design.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def inspect_summary(path):
    """Describe existing summary evidence without promoting it to accepted data."""
    path = Path(path)
    document = json.loads(path.read_text())
    if document.get("schema") != "t1-network-summary/v1":
        raise ValueError("expected a network summary, not a research result")
    rows = []
    for arm in document["arms"]:
        outcome = arm["outcome"]
        audit = arm["time_alignment_audit"]
        rows.append({"arm": arm["arm"], "offered": outcome["offered"],
                     "admitted": outcome["admitted"], "delivered": outcome["delivered"],
                     "fates": outcome["fate_counts"],
                     "queue_area_bits_s": outcome["queue_area_bits_s"],
                     "state_time_decisions": audit["decisions_with_state_time_audit"],
                     "fallback_decisions": audit["fallbacks"],
                     "D4_summary_only": outcome["deadline_primary_loss"]["value"]})
    return {"status": "SUMMARY_DIAGNOSTIC_ONLY", "accepted_data": False,
            "summary_sha256": _sha(path), "arms": rows,
            "target_resource_excitation": "UNVERIFIED: ISL queue area is not advertised work-ahead variation",
            "need_raw_observations": ["source_and_receive_timestamps", "work_ahead_history",
                                      "candidate_resource_generation", "query_targets_and_eta",
                                      "matching_future_resource_truth"]}


def compile_inputs(design_dir, population_raster):
    """Compile every frozen input trace without ever invoking a simulator."""
    from CODE.experiment_platform import t1_tasks
    from CODE.leo_sim import trace as trace_mod
    design_dir = Path(design_dir)
    output = design_dir / "input-compilation.json"
    if output.exists():
        raise ValueError("input compilation already exists")
    raster = Path(population_raster).resolve(strict=True)
    population_sha = _sha(raster)
    if population_sha != "c5742d16fc01d454e8ac5c5345a7e7716883acd28ac4d0d34c24613bc315e59a":
        raise ValueError("population raster differs from frozen input")
    design = json.loads((design_dir / "design.json").read_text())
    blocks = []
    for block in design["execution_blocks"]:
        resolved = config_mod.load_config_file(str(design_dir / block["profile"]))
        resolved["config"]["demand"]["population_path"] = str(raster)
        with tempfile.TemporaryDirectory(prefix="study-input-") as temporary:
            manifest = trace_mod.compile_trace(resolved, temporary)
            rows = trace_mod.load_trace(str(Path(temporary) / "trace.csv"),
                horizon_s=manifest["emission_end_s"],
                max_packets=resolved["config"]["execution"]["max_packets"])
        blocks.append({"block": block["block"], "packets": len(rows),
            "rows_digest": t1_tasks._rows_digest(rows),
            "trace_sha256": manifest.get("__trace_sha256") or manifest.get("trace_sha256"),
            "population_sha256": population_sha})
    burst = [b for b in blocks if b["block"] != "primary_steady"]
    same = len({(b["trace_sha256"], b["rows_digest"], b["packets"]) for b in burst}) == 1
    if not same:
        raise ValueError("predictor/protocol sensitivity blocks changed exogenous traffic")
    result = {"simulator_calls": 0, "blocks": blocks,
        "burst_trace_identical_across_predictor_and_advertisement_blocks": same}
    output.write_text(json.dumps(result, indent=2) + "\n")
    return result


def compile_suite_plans(design_dir, population_raster):
    """Materialize real suite bundles; keep technical blockers fail-closed."""
    from CODE.experiment_platform import t1_suite
    design_dir = Path(design_dir).resolve()
    design = json.loads((design_dir / "design.json").read_text())
    parent = design_dir / "suites"
    if parent.exists():
        raise ValueError("suite plans already exist")
    raster = Path(population_raster).resolve(strict=True)
    if _sha(raster) != "c5742d16fc01d454e8ac5c5345a7e7716883acd28ac4d0d34c24613bc315e59a":
        raise ValueError("population raster differs from the frozen study input")
    previous = os.environ.get("T1_INPUT_POPULATION_RASTER")
    os.environ["T1_INPUT_POPULATION_RASTER"] = str(raster)
    parent.mkdir()
    results = []
    try:
        for block in design["execution_blocks"]:
            name = block["block"]
            target = parent / name
            target.mkdir()
            cell_id = f"b-{name}-network-seed-7"
            auth = {"stage": "cost_probe", "tier": "b_dev", "cell_ids": [cell_id],
                    "max_selected_cells": 1, "expected_simulator_calls": 4,
                    "max_simulator_calls": 4, "allow_append": False,
                    "task": "network_alignment", "seed": 7,
                    "execution_chain_sha256": "0" * 64,
                    "cell_input_sha256": {cell_id: "0" * 64}}
            contract = {
                "schema": "t1-controlled-cost-probe/v1", "not_formal": True,
                "not_training": True, "study_contract_sha256": design["contract_sha256"],
                "design_readiness": {"status": "PENDING_STORAGE_AND_SCOPE_ACCEPTANCE",
                                     "runtime_gate_implemented": True,
                                     "runtime_authorization": auth, "release_ready": False},
                "statistics": {"deadline": {"value_s": float(block["driver_options"]["--deadline-s"])},
                               "population_window_s": [float(block["driver_options"]["--window-start"]),
                                                       float(block["driver_options"]["--window-end"])],
                               "dev_seeds": [7]},
                "b_round": {"scenarios": [{"id": name, "profile": str(design_dir / block["profile"]),
                                           "seeds": [7], "tasks": ["network_alignment"],
                                           "arms": ARMS, "replay_seed": 7}]},
                "budgets": {"cell_wall_s": 1800., "total_wall_s": 1800., "simulator_call_budget": 4},
                "budget_note": "bounded proposal, not a measured cost guarantee; no launch in this compiler",
            }
            path = target / "contract.yaml"
            path.write_text(yaml.safe_dump(contract, sort_keys=False))
            with tempfile.TemporaryDirectory(prefix="study-bind-") as temporary:
                draft = t1_suite.compile_bundle(path, Path(temporary) / "bundle")
                cell = next(c for c in draft["cells"] if c["cell_id"] == cell_id)
                auth["execution_chain_sha256"] = draft["execution_chain"]["combined_sha256"]
                auth["cell_input_sha256"][cell_id] = t1_suite._cell_input_sha256(cell["input"])
            path.write_text(yaml.safe_dump(contract, sort_keys=False))
            bundle = t1_suite.compile_bundle(path, target / "bundle")
            t1_suite.validate_bundle(target / "bundle")
            kwargs = dict(tier="b_dev", selected_cell_ids=[cell_id], append=False,
                          cells={c["cell_id"]: c for c in bundle["cells"]},
                          estimated_calls=4, bundle_root=target / "bundle")
            try:
                t1_suite.enforce_runtime_stage(contract, bundle, **kwargs)
            except t1_suite.SuiteError as exc:
                refusal = str(exc)
            else:
                raise ValueError("technical blockers must not silently authorize a run")
            hypothetical = copy.deepcopy(contract)
            hypothetical["design_readiness"]["status"] = "COST_PROBE_READY"
            t1_suite.enforce_runtime_stage(hypothetical, bundle, **kwargs)
            results.append({"block": name, "cell_id": cell_id,
                            "bundle_validated": True, "allowlist_validated_in_memory": True,
                            "actual_runtime_refusal": refusal,
                            "cell_input_sha256": auth["cell_input_sha256"][cell_id]})
    finally:
        if previous is None:
            os.environ.pop("T1_INPUT_POPULATION_RASTER", None)
        else:
            os.environ["T1_INPUT_POPULATION_RASTER"] = previous
    (parent / "validation.json").write_text(json.dumps({"simulator_calls": 0,
        "blocks": results, "recompile_from_final_clean_release_required": True}, indent=2) + "\n")
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--population-raster", type=Path,
                        help="also compile real, technically blocked T1 suite plans")
    args = parser.parse_args(argv)
    report = compile_study(args.contract, args.out)
    if args.summary:
        audit = inspect_summary(args.summary)
        (args.out / "existing-summary-diagnosis.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    if args.population_raster:
        compile_inputs(args.out, args.population_raster)
        compile_suite_plans(args.out, args.population_raster)
    print(json.dumps({"status": report["status"], "profiles": len(report["cells"]),
                      "simulator_calls": 0, "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
