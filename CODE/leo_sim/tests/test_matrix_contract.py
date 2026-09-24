import copy
import json
from pathlib import Path

import pytest

from CODE.experiment_platform import authorize_experiment
from CODE.leo_sim import matrix
from CODE.work.finalize_decision import evaluate_decision, file_sha256


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _request():
    return {
        "schema": matrix.MATRIX_REQUEST_SCHEMA,
        "experiment_id": "EXP-LEO-V2-MATRIX",
        "runtime_kind": "leo_sim_v2",
        "work_finalization": "CODE/work/WP-MATRIX/R01/finalization.json",
        "common_config": {
            "scenario": {"duration_s": 1.0, "num_satellites": 1,
                          "num_planes": 1},
            "endpoints": {"sites": [
                {"name": "a", "lat": 0.0, "lon": 0.0},
                {"name": "b", "lat": 0.0, "lon": 10.0},
            ]},
            "control_plane": {"enabled": False},
            "routing": {"policy": "oracle"},
        },
        "arms": [
            {"arm_id": "control", "config_overrides": {},
             "intervention_paths": []},
            {"arm_id": "treatment", "config_overrides": {
                "demand": {"offered_mbps": 2.0},
            }, "intervention_paths": ["demand.offered_mbps"]},
        ],
        "cells": [
            {"run_id": "EXP-LEO-V2-MATRIX-control-s42", "arm_id": "control",
             "phase": "non_learning", "trace_seed": 42, "learning_seed": None,
             "pairing_key": "pair-42", "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None, "source_sha256": None}},
            {"run_id": "EXP-LEO-V2-MATRIX-treatment-s42", "arm_id": "treatment",
             "phase": "non_learning", "trace_seed": 42, "learning_seed": None,
             "pairing_key": "pair-42", "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None, "source_sha256": None}},
        ],
        "acceptance": {"min_delivered_packets": 0,
                       "min_multisat_deliveries": 0,
                       "require_data_isl": False,
                       "require_control_delivery": False},
        "analysis": {
            "analysis_id": "AN-LEO-V2-MATRIX",
            "primary_metric": "delivery_rate",
            "estimand": "paired difference",
            "paired_by": ["pairing_key"],
            "planned_contrasts": [{"name": "treatment_minus_control",
                                   "left_arm": "treatment",
                                   "right_arm": "control",
                                   "estimand": "paired difference"}],
        },
        "claim_boundary": {
            "can_claim": ["compiled matrix identity"],
            "cannot_claim": ["run completion", "algorithm effect"],
        },
    }


def test_compile_matrix_emits_one_bound_v2_config_and_runbook_command_per_cell(tmp_path):
    request = _request()
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "EXPERIMENTS" / request["experiment_id"]

    report = matrix.compile_matrix_experiment(source, out, project_root=tmp_path)

    assert report["status"] == "COMPILED_REVIEW_REQUIRED"
    manifest = json.loads((out / "run-manifest.json").read_text())
    assert manifest["schema"] == matrix.MATRIX_MANIFEST_SCHEMA
    assert [c["run_id"] for c in manifest["cells"]] == [
        "EXP-LEO-V2-MATRIX-control-s42", "EXP-LEO-V2-MATRIX-treatment-s42"]
    for cell in manifest["cells"]:
        assert cell["runtime_kind"] == "leo_sim_v2"
        assert len(cell["config_sha256"]) == 64
        assert len(cell["trace_identity_sha256"]) == 64
        assert len(cell["input_sha256"]) == 0
        assert len(cell["code_sha256"]) == 64
        assert all(len(v) == 64 for v in cell["execution_chain_sha256"].values())
        assert len(cell["controlled_signature"]) == 64
        assert cell["acceptance"] == request["acceptance"]
        config_path = out / cell["config_path"]
        assert config_path.name == f"{cell['run_id']}.leo-sim.yaml"
        assert config_path.is_file()
    analysis = json.loads((out / "analysis-request.json").read_text())
    assert analysis["schema"] == matrix.MATRIX_ANALYSIS_SCHEMA
    assert analysis["planned_run_ids"] == [c["run_id"] for c in manifest["cells"]]
    runbook = (out / "RUNBOOK.md").read_text(encoding="utf-8")
    assert runbook.count("CODE/scripts/remote/run-remote.sh") == len(manifest["cells"])
    assert "python3 CODE/scripts/remote/run-remote.sh" not in runbook


def test_matrix_accounts_exact_reexecutions_as_nonindependent_conditions(tmp_path):
    request = _request()
    request["arms"][1]["config_overrides"] = {}
    request["arms"][1]["intervention_paths"] = []
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "EXPERIMENTS" / request["experiment_id"]

    report = matrix.compile_matrix_experiment(
        source, out, project_root=tmp_path)

    assert report["schema"] == matrix.MATRIX_COMPILE_REPORT_SCHEMA
    accounting = report["design_accounting"]
    assert accounting["planned_cells"] == 2
    assert accounting["unique_resolved_configurations"] == 1
    assert accounting["exact_reexecution_cells"] == 1
    assert accounting["independent_condition_rule"] == \
        "one independent condition per unique resolved config SHA256"
    assert accounting["exact_reexecution_groups"] == [{
        "config_sha256": accounting["exact_reexecution_groups"][0][
            "config_sha256"],
        "run_ids": [
            "EXP-LEO-V2-MATRIX-control-s42",
            "EXP-LEO-V2-MATRIX-treatment-s42",
        ],
    }]
    analysis = json.loads((out / "analysis-request.json").read_text())
    assert analysis["design_accounting"] == accounting
    runbook = (out / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "2 planned cells; 1 unique resolved configurations" in runbook
    assert "do not increase the independent-condition count" in runbook
    matrix.verify_compiled_matrix(tmp_path, out)

    report_path = out / "compile-report.json"
    tampered = json.loads(report_path.read_text(encoding="utf-8"))
    tampered["design_accounting"]["unique_resolved_configurations"] = 2
    _write_json(report_path, tampered)
    with pytest.raises(matrix.MatrixError, match="design accounting"):
        matrix.verify_compiled_matrix(tmp_path, out)


def test_serial_fail_closed_policy_is_preserved_in_compiled_runbook(tmp_path):
    request = _request()
    request["execution_policy"] = {"mode": "serial_fail_closed"}
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "EXPERIMENTS" / request["experiment_id"]

    matrix.compile_matrix_experiment(source, out, project_root=tmp_path)

    compiled = json.loads((out / "request.json").read_text(encoding="utf-8"))
    runbook = (out / "RUNBOOK.md").read_text(encoding="utf-8")
    assert compiled["execution_policy"] == {"mode": "serial_fail_closed"}
    assert "machine-enforced serial predecessor gate" in runbook


def test_matrix_binds_and_renders_declared_post_analysis_decision(tmp_path):
    request = _request()
    decision_path = tmp_path / "CODE" / "work" / "WP-MATRIX" / "R01" / \
        "pressure-decision.json"
    decision = {
        "schema": "leo-sim-isl-pressure-decision/v1",
        "canonical_invocation": [
            "python3", "-m", "CODE.experiment_platform.isl_pressure_decision",
            "--root", ".", "--manifest", "ANALYSIS/EXP-LEO-V2-MATRIX/"
            "v2-paired/analysis-manifest.json", "--control-arm", "control",
            "--candidate-arm", "treatment", "--out",
            "ANALYSIS/EXP-LEO-V2-MATRIX/pressure-classification.json",
        ],
    }
    _write_json(decision_path, decision)
    request["analysis"]["decision_contract"] = {
        "path": "CODE/work/WP-MATRIX/R01/pressure-decision.json",
    }
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "EXPERIMENTS" / request["experiment_id"]

    matrix.compile_matrix_experiment(source, out, project_root=tmp_path)

    analysis = json.loads((out / "analysis-request.json").read_text())
    assert analysis["decision_contract"]["path"] == \
        "CODE/work/WP-MATRIX/R01/pressure-decision.json"
    assert len(analysis["decision_contract"]["sha256"]) == 64
    runbook = (out / "RUNBOOK.md").read_text(encoding="utf-8")
    for token in decision["canonical_invocation"]:
        assert token in runbook
    matrix.verify_compiled_matrix(tmp_path, out)

    decision["canonical_invocation"][-1] = "ANALYSIS/tampered.json"
    _write_json(decision_path, decision)
    with pytest.raises(matrix.MatrixError, match="analysis request"):
        matrix.verify_compiled_matrix(tmp_path, out)
    decision["canonical_invocation"][-1] = \
        "ANALYSIS/EXP-LEO-V2-MATRIX/pressure-classification.json"
    _write_json(decision_path, decision)

    runbook_path = out / "RUNBOOK.md"
    runbook_path.write_text(
        runbook_path.read_text(encoding="utf-8").replace(
            "--candidate-arm treatment", "--candidate-arm control"),
        encoding="utf-8")
    report_path = out / "compile-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["artifact_hashes"]["RUNBOOK.md"] = file_sha256(runbook_path)
    _write_json(report_path, report)
    with pytest.raises(matrix.MatrixError, match="RUNBOOK"):
        matrix.verify_compiled_matrix(tmp_path, out)


def test_matrix_binds_scene_decision_and_coverage_artifacts(tmp_path):
    request = _request()
    work = tmp_path / "CODE" / "work" / "WP-MATRIX" / "R01"
    work.mkdir(parents=True)
    decision_path = work / "scene-decision.yaml"
    coverage_path = work / "coverage-audit.json"
    decision_path.write_text("schema: leo-sim-scene-decision/v1\n", encoding="utf-8")
    coverage_path.write_text("{}\n", encoding="utf-8")
    contract_path = work / "scene-check-contract.json"
    contract = {
        "schema": "leo-sim-scene-check-contract/v1",
        "decision_path": "CODE/work/WP-MATRIX/R01/scene-decision.yaml",
        "decision_sha256": file_sha256(decision_path),
        "coverage_path": "CODE/work/WP-MATRIX/R01/coverage-audit.json",
        "coverage_sha256": file_sha256(coverage_path),
        "canonical_invocation": [
            "python3", "-m", "CODE.leo_sim.scene_check", "--root", ".",
            "--contract", "CODE/work/WP-MATRIX/R01/scene-check-contract.json",
        ],
    }
    _write_json(contract_path, contract)
    request["analysis"]["decision_contract"] = {
        "path": "CODE/work/WP-MATRIX/R01/scene-check-contract.json",
    }
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "EXPERIMENTS" / request["experiment_id"]

    matrix.compile_matrix_experiment(source, out, project_root=tmp_path)
    analysis = json.loads((out / "analysis-request.json").read_text())
    binding = analysis["decision_contract"]
    assert binding["schema"] == "leo-sim-scene-check-contract/v1"
    assert binding["bound_artifacts"] == {
        contract["decision_path"]: contract["decision_sha256"],
        contract["coverage_path"]: contract["coverage_sha256"],
    }
    report = json.loads((out / "compile-report.json").read_text())
    assert report["artifact_hashes"][contract["decision_path"]] == \
        contract["decision_sha256"]
    assert report["artifact_hashes"][contract["coverage_path"]] == \
        contract["coverage_sha256"]
    runbook = (out / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "CODE.leo_sim.scene_check" in runbook
    assert "CODE/Results/EXP-LEO-V2-MATRIX-control-s42" in runbook
    matrix.verify_compiled_matrix(tmp_path, out)
    _, authorization_artifacts, _ = authorize_experiment._verified_experiment(
        tmp_path, out)
    assert all((tmp_path / raw).is_file()
               for raw in authorization_artifacts)
    assert not any(raw.startswith(f"EXPERIMENTS/{request['experiment_id']}/CODE/")
                   for raw in authorization_artifacts)

    coverage_path.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match="hash mismatch"):
        matrix.verify_compiled_matrix(tmp_path, out)


@pytest.mark.parametrize("mutation, message", [
    (lambda r: r.update(extra=True), "unknown request fields"),
    (lambda r: r["cells"].append(copy.deepcopy(r["cells"][0])), "duplicate run_id"),
    (lambda r: r["cells"][0].update(arm_id="missing"), "unknown arm_id"),
    (lambda r: r["cells"][0].update(run_id="wrong-id"), "run_id identity"),
])
def test_matrix_compiler_rejects_invalid_contract(mutation, message, tmp_path):
    request = _request()
    mutation(request)
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match=message):
        matrix.compile_matrix_experiment(source, tmp_path / "out", project_root=tmp_path)


def test_authorizer_verifies_matrix_cohort_as_one_atomic_set(tmp_path):
    request = _request()
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    experiment = tmp_path / "EXPERIMENTS" / request["experiment_id"]
    matrix.compile_matrix_experiment(source, experiment, project_root=tmp_path)

    experiment_id, artifacts, authorized = authorize_experiment._verified_experiment(
        tmp_path, experiment)

    assert experiment_id == request["experiment_id"]
    assert len(authorized) == len(request["cells"])
    assert {row["run_id"] for row in authorized} == {
        cell["run_id"] for cell in request["cells"]}
    assert all(row["runtime_kind"] == "leo_sim_v2" for row in authorized)
    assert all(row["config_path"].startswith("EXPERIMENTS/") for row in authorized)
    assert all(path.startswith(str(experiment.relative_to(tmp_path)))
               for path in artifacts)


def _make_finalization(root: Path, experiment: Path) -> Path:
    schema_root = Path(__file__).resolve().parents[2] / "work"
    work_root = root / "CODE" / "work"
    work_root.mkdir(parents=True, exist_ok=True)
    for name in ("work-package.schema.json", "review-receipt.schema.json",
                 "decision.schema.json"):
        (work_root / name).write_bytes((schema_root / name).read_bytes())
    artifact_hashes = {
        str(path.relative_to(root)): file_sha256(path)
        for path in sorted(experiment.rglob("*")) if path.is_file()
    }
    work_dir = work_root / "WP-MATRIX" / "R01"
    brief = {
        "schema": "agent-work-package/v2", "work_id": "WP-MATRIX",
        "revision": 1, "parent_revision": None,
        "producer_id": "producer:test", "producer_session_id": "P-matrix-r01",
        "objective": "Review matrix artifacts before execution.",
        "allowed_inputs": [str(experiment.relative_to(root))], "excluded_inputs": [],
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
            "receipt_id": f"RR-MATRIX-{role.upper()}", "work_id": "WP-MATRIX",
            "revision": 1, "artifact_hashes": artifact_hashes,
            "producer_id": brief["producer_id"],
            "producer_session_id": brief["producer_session_id"],
            "reviewer_id": f"reviewer:{role}", "reviewer_session_id": f"R-{role}",
            "independence": {"producer_and_reviewer_are_distinct": True,
                             "review_started_from_declared_inputs": True},
            "role": role, "verdict": "PASS", "evidence": ["fixture pass"],
            "blocking_findings": [], "unknowns": [], "required_revision": [],
        }
        receipt_path = work_dir / f"review-{role}.json"
        _write_json(receipt_path, receipt)
        refs.append({"receipt_id": receipt["receipt_id"],
                     "path": str(receipt_path.relative_to(root)),
                     "sha256": file_sha256(receipt_path), "role": role,
                     "verdict": "PASS"})
    decision = {
        "schema": "agent-work-decision/v1", "work_id": "WP-MATRIX",
        "revision": 1, "producer_id": brief["producer_id"],
        "artifact_hashes": artifact_hashes, "decision_maker_id": "decision:test",
        "applied_review_receipts": refs, "decision": "ACCEPT",
        "rationale": "Fixture reviews bind the complete matrix.",
        "blocking_findings": [], "revision_instructions": [], "next_revision": None,
    }
    decision_path = work_dir / "decision.json"
    _write_json(decision_path, decision)
    finalization, errors = evaluate_decision(root, brief_path, decision_path)
    assert errors == []
    finalization_path = work_dir / "finalization.json"
    _write_json(finalization_path, finalization)
    return finalization_path


def test_matrix_compile_authorize_row_and_runtime_verifier_e2e(tmp_path):
    request = _request()
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    experiment = tmp_path / "EXPERIMENTS" / request["experiment_id"]
    matrix.compile_matrix_experiment(source, experiment, project_root=tmp_path)
    finalization = _make_finalization(tmp_path, experiment)
    authorization_path = experiment / "authorization.json"
    authorization = authorize_experiment.build_authorization(
        tmp_path, experiment, finalization)
    _write_json(authorization_path, authorization)
    verified = authorize_experiment.verify_authorization(tmp_path, authorization_path)
    assert len(verified["authorized_cells"]) == len(request["cells"])
    row = verified["authorized_runs"][0]
    config_path = tmp_path / row["config_path"]
    runtime_verified = authorize_experiment.verify_authorization_for_leo_sim_v2_config(
        tmp_path, authorization_path, config_path, row["run_id"])
    assert runtime_verified["status"] == "AUTHORIZED"


def test_paired_learning_cells_allow_different_seeds_and_external_checkpoints(tmp_path):
    ckpt_a = tmp_path / "EXPERIMENTS" / "ckpt-a.keras"
    ckpt_b = tmp_path / "EXPERIMENTS" / "ckpt-b.keras"
    ckpt_a.parent.mkdir(parents=True)
    ckpt_a.write_bytes(b"checkpoint-a")
    ckpt_b.write_bytes(b"checkpoint-b")
    metadata = ckpt_a.parent / "metadata.json"
    metadata.write_text("{\"contract\": \"C3\"}\n", encoding="utf-8")
    import hashlib
    sha_a = hashlib.sha256(ckpt_a.read_bytes()).hexdigest()
    sha_b = hashlib.sha256(ckpt_b.read_bytes()).hexdigest()
    metadata_sha = hashlib.sha256(metadata.read_bytes()).hexdigest()
    request = _request()
    request["arms"] = [
        {"arm_id": "left", "config_overrides": {}, "intervention_paths": []},
        {"arm_id": "right", "config_overrides": {}, "intervention_paths": []},
    ]
    request["common_config"]["routing"] = {
        "policy": "hop", "learning_enabled": True, "contract": "C3",
    }
    request["common_config"]["control_plane"] = {"enabled": True}
    request["common_config"]["learning"] = {
        "algorithm": "ddqn", "mode": "eval",
    }
    request["analysis"]["planned_contrasts"][0].update(
        left_arm="left", right_arm="right")
    request["cells"] = [
        {"run_id": "EXP-LEO-V2-MATRIX-left-s42-l1", "arm_id": "left",
         "phase": "evaluation", "trace_seed": 42, "learning_seed": 1,
         "pairing_key": "pair-42", "config_overrides": {
                 "learning": {"checkpoint_path": "EXPERIMENTS/ckpt-a.keras",
                               "checkpoint_sha256": sha_a,
                               "checkpoint_metadata_sha256": metadata_sha}},
         "checkpoint_lineage": {"mode": "evaluation_only",
                                 "source_run_id": f"external-{sha_a}",
                                 "source_sha256": sha_a}},
        {"run_id": "EXP-LEO-V2-MATRIX-right-s42-l2", "arm_id": "right",
         "phase": "evaluation", "trace_seed": 42, "learning_seed": 2,
         "pairing_key": "pair-42", "config_overrides": {
                 "learning": {"checkpoint_path": "EXPERIMENTS/ckpt-b.keras",
                               "checkpoint_sha256": sha_b,
                               "checkpoint_metadata_sha256": metadata_sha}},
         "checkpoint_lineage": {"mode": "evaluation_only",
                                 "source_run_id": f"external-{sha_b}",
                                 "source_sha256": sha_b}},
    ]
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "EXPERIMENTS" / request["experiment_id"]
    report = matrix.compile_matrix_experiment(source, out, project_root=tmp_path)
    assert report["status"] == "COMPILED_REVIEW_REQUIRED"
    bad = copy.deepcopy(request)
    bad["cells"][1]["checkpoint_lineage"]["source_sha256"] = "0" * 64
    source.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match="source_sha256"):
        matrix.compile_matrix_experiment(source,
                                         tmp_path / "EXPERIMENTS" / "EXP-BAD",
                                         project_root=tmp_path)
    unbound = copy.deepcopy(request)
    unbound["experiment_id"] = "EXP-UNBOUND"
    for cell in unbound["cells"]:
        cell["run_id"] = cell["run_id"].replace(
            "EXP-LEO-V2-MATRIX", "EXP-UNBOUND", 1)
    unbound["cells"][0]["checkpoint_lineage"]["source_run_id"] = \
        "external-arbitrary-label"
    source.write_text(json.dumps(unbound), encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match="content-bound external checkpoint"):
        matrix.compile_matrix_experiment(
            source, tmp_path / "EXPERIMENTS" / unbound["experiment_id"],
            project_root=tmp_path)


def test_matrix_rejects_evaluation_cells_with_cyclic_checkpoint_lineage(tmp_path):
    checkpoint = tmp_path / "EXPERIMENTS" / "checkpoint.keras"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    metadata = checkpoint.parent / "metadata.json"
    metadata.write_text("{\"contract\": \"C3\"}\n", encoding="utf-8")
    import hashlib
    checkpoint_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    metadata_sha = hashlib.sha256(metadata.read_bytes()).hexdigest()
    request = _request()
    request["common_config"]["routing"] = {
        "policy": "hop", "learning_enabled": True, "contract": "C3",
    }
    request["common_config"]["control_plane"] = {"enabled": True}
    request["common_config"]["learning"] = {
        "algorithm": "ddqn", "mode": "eval",
    }
    run_ids = [
        "EXP-LEO-V2-MATRIX-control-s42-l1",
        "EXP-LEO-V2-MATRIX-treatment-s42-l2",
    ]
    for index, cell in enumerate(request["cells"]):
        cell.update({
            "run_id": run_ids[index],
            "phase": "evaluation",
            "learning_seed": index + 1,
            "config_overrides": {"learning": {
                "checkpoint_path": "EXPERIMENTS/checkpoint.keras",
                "checkpoint_sha256": checkpoint_sha,
                "checkpoint_metadata_sha256": metadata_sha,
            }},
            "checkpoint_lineage": {
                "mode": "evaluation_only",
                "source_run_id": run_ids[1 - index],
                "source_sha256": checkpoint_sha,
            },
        })
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(matrix.MatrixError, match="content-bound external checkpoint"):
        matrix.compile_matrix_experiment(
            source, tmp_path / "EXPERIMENTS" / request["experiment_id"],
            project_root=tmp_path)


def test_matrix_rejects_duplicate_contrasts_for_same_arm_pair(tmp_path):
    request = _request()
    request["analysis"]["planned_contrasts"].append({
        "name": "control_minus_treatment",
        "left_arm": "control",
        "right_arm": "treatment",
        "estimand": "paired difference",
    })
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(matrix.MatrixError, match="duplicate contrast arm pair"):
        matrix.compile_matrix_experiment(
            source, tmp_path / "EXPERIMENTS" / request["experiment_id"],
            project_root=tmp_path)


@pytest.mark.parametrize("mutation, message", [
    (lambda r: r["arms"][1]["config_overrides"].update(
        scenario={"duration_s": 2.0}),
     "undeclared intervention"),
    (lambda r: r["cells"].pop(), "pairing_key .* exactly one left and one right"),
    (lambda r: r["analysis"].update(paired_by=["seed"]), "unsupported paired_by"),
    (lambda r: r["analysis"]["planned_contrasts"][0].update(right_arm="missing"),
     "unknown arm"),
    (lambda r: r["arms"][1].update(intervention_paths=["demand"]),
     "exact override leaf paths"),
    (lambda r: r["arms"][1]["intervention_paths"].append(
        "scenario.duration_s"), "exact override leaf paths"),
    (lambda r: r["arms"][1].update(
        config_overrides={"scenario": {}}, intervention_paths=["scenario"]),
     "empty mapping"),
])
def test_matrix_pairing_and_intervention_contract_fails_closed(mutation, message, tmp_path):
    request = _request()
    mutation(request)
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match=message):
        matrix.compile_matrix_experiment(source,
                                         tmp_path / "EXPERIMENTS" / request["experiment_id"],
                                         project_root=tmp_path)


def test_matrix_rejects_escape_and_symlink_output_paths(tmp_path):
    request = _request()
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match="canonical"):
        matrix.compile_matrix_experiment(source, tmp_path / "elsewhere",
                                         project_root=tmp_path)
    canonical = tmp_path / "EXPERIMENTS" / request["experiment_id"]
    canonical.parent.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    canonical.symlink_to(target, target_is_directory=True)
    with pytest.raises(matrix.MatrixError, match="symbolic"):
        matrix.compile_matrix_experiment(source, canonical, project_root=tmp_path)


def test_matrix_accepts_macos_var_alias_for_same_project_root(tmp_path):
    raw = str(tmp_path)
    if not raw.startswith("/private/var/"):
        pytest.skip("macOS /var alias is not present")
    alias_root = Path(raw.replace("/private/var/", "/var/", 1))
    if alias_root.resolve() != tmp_path.resolve():
        pytest.skip("/var does not resolve to /private/var")
    request = _request()
    source = alias_root / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")

    report = matrix.compile_matrix_experiment(
        source, alias_root / "EXPERIMENTS" / request["experiment_id"],
        project_root=alias_root)

    assert report["status"] == "COMPILED_REVIEW_REQUIRED"


def test_matrix_verifier_rejects_symlinked_resolved_ancestor_and_report_rebind(tmp_path):
    request = _request()
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    experiment = tmp_path / "EXPERIMENTS" / request["experiment_id"]
    matrix.compile_matrix_experiment(source, experiment, project_root=tmp_path)
    resolved = experiment / "resolved"
    real_resolved = experiment / "resolved-real"
    resolved.rename(real_resolved)
    resolved.symlink_to(real_resolved, target_is_directory=True)
    with pytest.raises(matrix.MatrixError, match="symbolic"):
        matrix.verify_compiled_matrix(tmp_path, experiment)

    # A fresh compile isolates the report identity check from the symlink case.
    experiment = tmp_path / "EXPERIMENTS" / "EXP-LEO-V2-MATRIX-REPORT"
    request["experiment_id"] = "EXP-LEO-V2-MATRIX-REPORT"
    request["cells"][0]["run_id"] = "EXP-LEO-V2-MATRIX-REPORT-control-s42"
    request["cells"][1]["run_id"] = "EXP-LEO-V2-MATRIX-REPORT-treatment-s42"
    source.write_text(json.dumps(request), encoding="utf-8")
    matrix.compile_matrix_experiment(source, experiment, project_root=tmp_path)
    report_path = experiment / "compile-report.json"
    report = json.loads(report_path.read_text())
    report["runtime_kind"] = "legacy_gateway"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(matrix.MatrixError, match="compile report"):
        matrix.verify_compiled_matrix(tmp_path, experiment)
