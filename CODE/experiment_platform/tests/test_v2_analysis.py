"""Focused tests for the dedicated V2 result-to-analysis adapter."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest

from CODE.experiment_platform import authorize_experiment, v2_analysis
from CODE.leo_sim import kernel, receipt, trace
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row


_REAL_ANALYZER_IDENTITY = v2_analysis._analyzer_identity


@pytest.fixture(autouse=True)
def _stable_analyzer_identity(monkeypatch):
    identity = {
        "git_commit": "a" * 40,
        "files": {
            "CODE/experiment_platform/v2_analysis.py": "b" * 64,
            "CODE/experiment_platform/isl_pressure.py": "d" * 64,
            "CODE/experiment_platform/isl_pressure_decision.py": "e" * 64,
            "CODE/leo_sim/metrics.py": "c" * 64,
            "CODE/leo_sim/receipt.py": "9" * 64,
        },
    }
    monkeypatch.setattr(v2_analysis, "_analyzer_identity", lambda: identity)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _seal_governed(value: dict) -> None:
    value.pop("payload_sha256", None)
    value["payload_sha256"] = v2_analysis.canonical_sha(value)


def _write_external_witness(root: Path, run_id: str, governed: dict,
                            *, authorization_sha256: str) -> None:
    witness = {
        "schema": "leo-remote-launch-status/v2",
        "status": "success", "exit_code": 0,
        "launch_nonce": "b" * 32, "run_id": run_id,
        "authorization_sha256": authorization_sha256,
        "last_results_dir": f"/data/论文/leo-direct-sim/CODE/Results/{run_id}",
        "governance_receipt_sha256": v2_analysis.file_sha256(
            root / "CODE" / "Results" / run_id / "governance_receipt.json"),
        "governance_witness": {
            key: governed[key] for key in v2_analysis.GOVERNANCE_WITNESS_FIELDS
        },
    }
    _write(root / "CODE" / "Results" / "_external_launch_witness" / f"{run_id}.json",
           witness)


def test_external_witness_path_supports_explicit_remote_nonce_naming(tmp_path):
    witness_root = tmp_path / ".remote_runtime" / "launches"
    nonce = "d" * 32
    nonce_path = witness_root / f"{nonce}.json"
    _write(nonce_path, {"schema": "leo-remote-launch-status/v2"})

    assert v2_analysis._external_witness_path(
        witness_root, "EXP-run", launch_nonce=nonce) == nonce_path
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="external launch witness is missing"):
        v2_analysis._external_witness_path(witness_root, "EXP-run")


def test_analyzer_identity_binds_clean_full_commit_and_files(monkeypatch):
    responses = [
        mock.Mock(stdout="1" * 40 + "\n"),
        mock.Mock(stdout=""),
    ]
    monkeypatch.setattr(v2_analysis.subprocess, "run",
                        mock.Mock(side_effect=responses))
    identity = _REAL_ANALYZER_IDENTITY()
    assert identity["git_commit"] == "1" * 40
    assert set(identity["files"]) == set(v2_analysis.ANALYZER_FILES)
    assert all(len(value) == 64 for value in identity["files"].values())


def test_analyzer_identity_rejects_dirty_analysis_files(monkeypatch):
    responses = [
        mock.Mock(stdout="1" * 40 + "\n"),
        mock.Mock(stdout=" M CODE/experiment_platform/v2_analysis.py\n"),
    ]
    monkeypatch.setattr(v2_analysis.subprocess, "run",
                        mock.Mock(side_effect=responses))
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="differ from the bound Git commit"):
        _REAL_ANALYZER_IDENTITY()


def _run_fixture(root: Path, run_id: str, arm_id: str, pair: str) -> dict:
    cfg = make_cfg({"scenario": {"duration_s": 1.0, "num_satellites": 1,
                                  "num_planes": 1},
                    "endpoints": {"sites": [
                        {"name": "a", "lat": 0.0, "lon": 0.0},
                        {"name": "b", "lat": 0.0, "lon": 10.0},
                    ]}})
    a, b = cell(0.0, 0.0), cell(0.0, 10.0)
    geometry = StaticGeometry(1, visible=lambda *_: True)
    trace_dir = root / "trace" / run_id
    manifest = trace.compile_trace(cfg, str(trace_dir))
    trace_bytes = (trace_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (trace_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(str(trace_dir / "trace.csv"),
                            horizon_s=cfg["config"]["scenario"]["duration_s"],
                            max_packets=cfg["config"]["execution"]["max_packets"])
    result = kernel.run_simulation(cfg, rows, geometry=geometry)
    out = root / "CODE" / "Results" / run_id
    out.mkdir(parents=True)
    receipt_payload = receipt.write_run(
        str(out), cfg, trace_bytes, manifest, result, rows)
    auth_sha = "a" * 64
    _write(out / "formal_run.json", {
        "schema": "leo-sim-formal-run/v1", "run_id": run_id,
        "launch_nonce": "b" * 32, "authorization_sha256": auth_sha,
        "config_sha256": receipt_payload["config_sha256"],
        "code_sha256": receipt_payload["code_sha256"],
        "receipt_sha256": hashlib.sha256(
            (out / "receipt.json").read_bytes()).hexdigest(),
        "natural_end": True, "conservation_ok": True,
    })
    execution_chain = {"CODE/example.py": "9" * 64}
    governed = {
        "schema": "leo-sim-governance-receipt/v2", "research_eligible": True,
        "run_id": run_id, "launch_nonce": "b" * 32,
        "verification_errors": [],
        "source_git_commit": "d" * 40,
        "source_tree_sha256": "e" * 64,
        "deployment_receipt_sha256": "f" * 64,
        "execution_chain_sha256": execution_chain,
        "receipt_schema": receipt_payload["schema"],
        "resolved_config_sha256": v2_analysis.file_sha256(
            out / "resolved_config.json"),
        "trace_manifest_schema": manifest["schema"],
        "trace_identity_contract": receipt_payload["trace_identity_contract"],
        "trace_manifest_sha256": v2_analysis.file_sha256(out / "manifest.json"),
        "run_receipt_sha256": hashlib.sha256(
            (out / "receipt.json").read_bytes()).hexdigest(),
    }
    _seal_governed(governed)
    _write(out / "governance_receipt.json", governed)
    governed = json.loads((out / "governance_receipt.json").read_text(encoding="utf-8"))
    _write_external_witness(root, run_id, governed,
                            authorization_sha256=auth_sha)
    return {
        "run_id": run_id,
        "runtime_kind": "leo_sim_v2",
        "arm_id": arm_id,
        "phase": "non_learning",
        "pairing_key": pair,
        "trace_seed": receipt_payload["seed"],
        "config_sha256": receipt_payload["config_sha256"],
        "trace_identity_sha256": receipt_payload["trace_identity_sha256"],
        "input_sha256": manifest["input_sha256"],
        "code_sha256": receipt_payload["code_sha256"],
        "execution_chain_sha256": execution_chain,
        "controlled_signature": "c" * 64,
    }


def test_primary_metric_is_derived_from_v2_receipt_and_ledgers():
    receipt_payload = {"totals": {"delivered_bits": 10},
                       "fate_counts": {"DELIVERED": 1, "IN_SYSTEM_AT_STOP": 1}}
    ledgers = {"congestion_metrics": {
        "packets": {"1": {"e2e_s": 1.0, "total_queue_wait_s": 0.2,
                            "tx_s": 0.3, "prop_s": 0.4}},
        "links": {"isl:0:1": {"utilization": 0.5}},
    }}
    assert v2_analysis._metric_from_result(
        receipt_payload, ledgers, "delivery_rate") == 0.5
    assert v2_analysis._metric_from_result(
        receipt_payload, ledgers, "e2e_delay_mean_s") == 1.0
    assert v2_analysis._metric_from_result(
        receipt_payload, ledgers, "link_utilization_mean") == 0.5


def test_access_boundary_metrics_are_explicit_v2_metrics():
    receipt_payload = {"totals": {"delivered_bits": 10},
                       "fate_counts": {"DELIVERED": 1}}
    ledgers = {"congestion_metrics": {
        "schema": "leo-sim-congestion-metrics/v2",
        "offered_packets": 4,
        "admitted_at_satellite_ingress_packets": 2,
        "access_admission_rate": 0.5,
        "network_delivery_rate_by_horizon": 0.25,
        "packets": {}, "links": {}}}
    assert v2_analysis._metric_from_result(
        receipt_payload, ledgers, "access_admission_rate") == 0.5
    assert v2_analysis._metric_from_result(
        receipt_payload, ledgers,
        "network_delivery_rate_by_horizon") == 0.25


def test_isl_utilization_metrics_filter_non_isl_links():
    receipt_payload = {"totals": {"delivered_bits": 10},
                       "fate_counts": {"DELIVERED": 1}}
    ledgers = {"congestion_metrics": {
        "packets": {},
        "links": {
            "gsl:uplink:0:a": {"stage": "uplink", "utilization": 0.99},
            "isl:0:1": {"stage": "isl", "utilization": 0.25},
            "isl:1:2": {"stage": "isl", "utilization": 0.75},
            "gsl:downlink:2:b": {"stage": "downlink", "utilization": 0.95},
        },
    }}
    assert v2_analysis._metric_from_result(
        receipt_payload, ledgers,
        "isl_link_utilization_mean") == 0.5
    assert v2_analysis._metric_from_result(
        receipt_payload, ledgers,
        "isl_link_utilization_max") == 0.75


def test_isl_utilization_metrics_fail_loud_without_isl_links():
    receipt_payload = {"totals": {"delivered_bits": 10},
                       "fate_counts": {"DELIVERED": 1}}
    ledgers = {"congestion_metrics": {
        "packets": {},
        "links": {
            "gsl:uplink:0:a": {"stage": "uplink", "utilization": 0.99},
            "gsl:downlink:0:b": {"stage": "downlink", "utilization": 0.95},
        },
    }}
    for primary in {"isl_link_utilization_mean", "isl_link_utilization_max"}:
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="has no ISL links"):
            v2_analysis._metric_from_result(receipt_payload, ledgers, primary)


def test_planned_contrasts_scope_each_contrast_to_its_own_pairing_key():
    results = [
        {"pairing_key": "load-50", "arm_id": "low_control",
         "primary_metric": 0.40, "trace_sha256": "a" * 64,
         "trace_identity_sha256": "b" * 64, "seed": 7},
        {"pairing_key": "load-50", "arm_id": "low_copy",
         "primary_metric": 0.45, "trace_sha256": "a" * 64,
         "trace_identity_sha256": "b" * 64, "seed": 7},
        {"pairing_key": "load-100", "arm_id": "medium_control",
         "primary_metric": 0.50, "trace_sha256": "d" * 64,
         "trace_identity_sha256": "e" * 64, "seed": 8},
        {"pairing_key": "load-100", "arm_id": "medium_copy",
         "primary_metric": 0.55, "trace_sha256": "d" * 64,
         "trace_identity_sha256": "e" * 64, "seed": 8},
    ]
    contrasts = [
        {"name": "low_copy_minus_low_control", "left_arm": "low_copy",
         "right_arm": "low_control"},
        {"name": "medium_copy_minus_medium_control",
         "left_arm": "medium_copy", "right_arm": "medium_control"},
    ]
    output = v2_analysis._compute_planned_contrasts(
        results, contrasts, "delivery_rate")
    assert [item["n_pairs"] for item in output] == [1, 1]
    assert [item["mean_difference"] for item in output] == pytest.approx([0.05, 0.05])


def test_planned_contrast_rejects_actual_trace_mismatch():
    results = [
        {"pairing_key": "pair-1", "arm_id": "left",
         "primary_metric": 0.4, "trace_sha256": "a" * 64,
         "trace_identity_sha256": "b" * 64, "seed": 7},
        {"pairing_key": "pair-1", "arm_id": "right",
         "primary_metric": 0.5, "trace_sha256": "c" * 64,
         "trace_identity_sha256": "b" * 64, "seed": 7},
    ]
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="actual trace_sha256 mismatch"):
        v2_analysis._compute_planned_contrasts(
            results,
            [{"name": "left_minus_right", "left_arm": "left",
              "right_arm": "right"}],
            "delivery_rate")


def test_run_diagnostics_rejects_zero_rate_holds():
    ledgers = {
        "mechanism_counters": {
            "mcs_rate_samples": 2,
            "mcs_zero_rate_holds": 1,
            "mcs_rate_min_bps": 10,
            "mcs_rate_max_bps": 20,
        },
        "control_counters": {},
        "congestion_metrics": {"links": {}},
    }
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="zero-rate hold"):
        v2_analysis._run_diagnostics(ledgers)


def test_run_diagnostics_preserves_raw_isl_denominator_and_saturation():
    ledgers = {
        "mechanism_counters": {
            "mcs_rate_samples": 2,
            "mcs_zero_rate_holds": 0,
            "mcs_rate_min_bps": 10,
            "mcs_rate_max_bps": 20,
        },
        "control_counters": {"registered": 3, "transmission_completed": 2},
        "packet_fates": {"1": ["IN_SYSTEM_AT_STOP", 1.0]},
        "access": {},
        "queue_area_bits_s": {},
        "stop_time_s": 1.0,
        "packet_events": [],
        "link_service_windows": [],
        "link_available_windows": [{
            "stage": "isl", "link_id": "isl:0:1",
            "start": 0.0, "end": 1.0, "rate_bps": 100.0,
            "capacity_bits": 100.0,
        }],
        "congestion_metrics": {"links": {
            "isl:0:1": {
                "stage": "isl", "served_bits": 100.0,
                "available_capacity_bits": 100.0, "utilization": 1.0,
                "available_samples": 2, "service_windows": 1,
            },
        }},
    }
    diagnostics = v2_analysis._run_diagnostics(ledgers)
    assert diagnostics["mcs"]["zero_rate_holds"] == 0
    assert diagnostics["control"]["registered"] == 3
    assert diagnostics["isl"]["saturated_link_ids"] == ["isl:0:1"]
    assert diagnostics["isl"]["links"]["isl:0:1"]["served_bits"] == 100.0
    assert diagnostics["isl"]["links"]["isl:0:1"]["available_capacity_bits"] == 100.0
    assert diagnostics["drain"]["in_system_at_stop_packets"] == 1
    assert diagnostics["drain"]["unmatched_isl_queue_entries"] == 0


def test_v2_analysis_binds_two_real_receipts_and_persisted_outputs(tmp_path):
    root = tmp_path
    rows = [
        _run_fixture(root, "EXP-V2-ANALYSIS-control-s1", "control", "pair-1"),
        _run_fixture(root, "EXP-V2-ANALYSIS-treatment-s1", "treatment", "pair-1"),
    ]
    experiment = root / "EXPERIMENTS" / "EXP-V2-ANALYSIS"
    experiment.mkdir(parents=True)
    cells = [{**row, "trace_seed": 1} for row in rows]
    _write(experiment / "request.json", {
        "experiment_id": "EXP-V2-ANALYSIS",
        "claim_boundary": {"can_claim": ["none"],
                            "cannot_claim": ["not independently reviewed"]},
    })
    _write(experiment / "run-manifest.json", {
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": "EXP-V2-ANALYSIS", "cells": cells,
    })
    _write(experiment / "analysis-request.json", {
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": "EXP-V2-ANALYSIS",
        "planned_run_ids": [row["run_id"] for row in rows],
        "analysis": {
            "analysis_id": "AN-V2-ANALYSIS", "primary_metric": "delivery_rate",
            "planned_contrasts": [{"name": "treatment_minus_control",
                                   "left_arm": "treatment", "right_arm": "control"}],
        },
    })
    auth = {
        "status": "AUTHORIZED", "experiment_id": "EXP-V2-ANALYSIS",
        "authorized_cells": rows,
    }
    auth_path = experiment / "authorization.json"
    _write(auth_path, auth)
    auth_sha = v2_analysis.file_sha256(auth_path)
    for row in rows:
        result_dir = root / "CODE" / "Results" / row["run_id"]
        formal_path = result_dir / "formal_run.json"
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        formal["authorization_sha256"] = auth_sha
        _write(formal_path, formal)
        governed_path = result_dir / "governance_receipt.json"
        governed = json.loads(governed_path.read_text(encoding="utf-8"))
        governed["authorization_sha256"] = auth_sha
        _seal_governed(governed)
        _write(governed_path, governed)
        _write_external_witness(root, row["run_id"], governed,
                                authorization_sha256=auth_sha)
    analyzer = {
        "git_commit": "d" * 40,
        "files": {
            "CODE/experiment_platform/v2_analysis.py": "e" * 64,
            "CODE/experiment_platform/isl_pressure.py": "0" * 64,
            "CODE/experiment_platform/isl_pressure_decision.py": "1" * 64,
            "CODE/leo_sim/metrics.py": "f" * 64,
            "CODE/leo_sim/receipt.py": "8" * 64,
        },
    }
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth), \
            mock.patch.object(v2_analysis, "_analyzer_identity",
                              return_value=analyzer, create=True):
        manifest = v2_analysis.analyze(root, experiment, auth_path)
        out = root / "ANALYSIS" / "EXP-V2-ANALYSIS"
        v2_analysis.write_outputs(root, out, manifest)
        ok, errors = v2_analysis.verify_persisted_analysis(
            root, out / "analysis-manifest.json")
        persisted_path = out / "analysis-manifest.json"
        gate_path = out / "claim-gate.json"
        summary_path = out / "summary.json"
        report_path = out / "report.md"
        original_persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
        original_gate = json.loads(gate_path.read_text(encoding="utf-8"))
        original_summary = json.loads(summary_path.read_text(encoding="utf-8"))
        original_report = report_path.read_text(encoding="utf-8")
        hashless = json.loads(json.dumps(original_persisted))
        hashless.pop("output_hashes")
        hashless.pop("output_artifacts")
        report_path.write_text("tampered report\n", encoding="utf-8")
        _write(persisted_path, hashless)
        hashless_gate = dict(original_gate)
        hashless_gate["analysis_manifest_sha256"] = v2_analysis.file_sha256(
            persisted_path)
        _write(gate_path, hashless_gate)
        hashless_ok, hashless_errors = v2_analysis.verify_persisted_analysis(
            root, persisted_path)
        _write(persisted_path, original_persisted)
        _write(gate_path, original_gate)
        report_path.write_text(original_report, encoding="utf-8")
        boundary_gate = dict(original_gate)
        boundary_gate["cannot_claim"] = []
        _write(gate_path, boundary_gate)
        boundary_ok, boundary_errors = v2_analysis.verify_persisted_analysis(
            root, persisted_path)
        _write(gate_path, original_gate)
        claim_tampered = json.loads(json.dumps(original_persisted))
        claim_tampered["claim_status"] = "LEGACY_INTERNAL_ONLY"
        claim_summary = dict(original_summary)
        claim_summary["claim_status"] = claim_tampered["claim_status"]
        _write(summary_path, claim_summary)
        claim_tampered["output_hashes"]["summary.json"] = \
            v2_analysis.file_sha256(summary_path)
        for artifact in claim_tampered["output_artifacts"]:
            if artifact["path"].endswith("/summary.json"):
                artifact["sha256"] = claim_tampered["output_hashes"]["summary.json"]
        _write(persisted_path, claim_tampered)
        claim_gate = dict(original_gate)
        claim_gate["status"] = claim_tampered["claim_status"]
        claim_gate["analysis_manifest_sha256"] = v2_analysis.file_sha256(
            persisted_path)
        _write(gate_path, claim_gate)
        claim_ok, claim_errors = v2_analysis.verify_persisted_analysis(
            root, persisted_path)
        _write(persisted_path, original_persisted)
        _write(gate_path, original_gate)
        _write(summary_path, original_summary)
        tampered = json.loads(persisted_path.read_text(encoding="utf-8"))
        tampered["analyzer"]["git_commit"] = "0" * 40
        _write(persisted_path, tampered)
        tampered_ok, tampered_errors = v2_analysis.verify_persisted_analysis(
            root, persisted_path)
    assert ok, errors
    assert not hashless_ok
    assert any("output hash contract" in item for item in hashless_errors)
    assert not boundary_ok
    assert any("claim gate differs" in item for item in boundary_errors)
    assert not claim_ok
    assert any("claim_status" in item for item in claim_errors)
    assert not tampered_ok
    assert any("analyzer identity mismatch" in item for item in tampered_errors)
    assert manifest["status"] == "VERIFIED"
    assert manifest["analyzer"] == analyzer
    assert manifest["claim_status"] == "READY_FOR_INDEPENDENT_CLAIM_REVIEW"
    assert manifest["planned_contrasts"][0]["n_pairs"] == 1
    assert json.loads((out / "claim-gate.json").read_text())["status"] == manifest["claim_status"]
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "MCS zero-rate holds" in report
    assert "saturated directed ISL links" in report
    assert "1 s active-window p99/max utilization" in report
    assert "matched/unmatched ISL queue entries" in report


def test_verify_persisted_analysis_rejects_nonmapping_inputs(tmp_path,
                                                              monkeypatch):
    """Malformed persisted manifests fail closed through the normal error API."""
    root = tmp_path
    manifest_path = root / "analysis-manifest.json"
    analyzer = {"git_commit": "d" * 40, "files": {}}
    _write(manifest_path, {
        "schema": v2_analysis.SCHEMA,
        "status": "VERIFIED",
        "analyzer": analyzer,
        "inputs": [],
    })
    monkeypatch.setattr(v2_analysis, "_analyzer_identity", lambda: analyzer)
    ok, errors = v2_analysis.verify_persisted_analysis(root, manifest_path)
    assert not ok
    assert any("inputs contract" in item for item in errors)


def test_v2_analysis_rejects_empty_authorized_cohort(tmp_path):
    experiment = tmp_path / "EXPERIMENTS" / "EXP-EMPTY"
    _write(experiment / "request.json", {
        "experiment_id": "EXP-EMPTY", "claim_boundary": {},
    })
    _write(experiment / "run-manifest.json", {
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": "EXP-EMPTY", "cells": [],
    })
    _write(experiment / "analysis-request.json", {
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": "EXP-EMPTY", "planned_run_ids": [],
        "analysis": {"primary_metric": "delivery_rate",
                     "planned_contrasts": []},
    })
    authorization_path = experiment / "authorization.json"
    _write(authorization_path, {
        "status": "AUTHORIZED", "experiment_id": "EXP-EMPTY",
        "authorized_cells": [],
    })
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization",
                           return_value=json.loads(
                               authorization_path.read_text())):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="authorization has no authorized V2 cohort"):
            v2_analysis.analyze(tmp_path, experiment, authorization_path)


def test_v2_analysis_rejects_legacy_receipt_without_explicit_internal_mode(
        tmp_path):
    root, experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    result_dir = root / "CODE" / "Results" / row["run_id"]
    receipt_path = result_dir / "receipt.json"
    governed_path = result_dir / "governance_receipt.json"
    formal_path = result_dir / "formal_run.json"
    receipt_doc = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt_doc["schema"] = v2_analysis.receipt_mod.LEGACY_RECEIPT_SCHEMA_V4
    _write(receipt_path, receipt_doc)
    receipt_sha = v2_analysis.file_sha256(receipt_path)
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["schema"] = v2_analysis.GOVERNANCE_SCHEMA_V1
    governed["run_receipt_sha256"] = receipt_sha
    governed["authorization_sha256"] = "a" * 64
    _write(governed_path, governed)
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["receipt_sha256"] = receipt_sha
    _write(formal_path, formal)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth), \
            mock.patch.object(v2_analysis.receipt_mod, "verify_receipt_dir",
                              return_value=[]):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="external-witness mode requires current"):
            v2_analysis.analyze(root, experiment, auth_path)


def test_v2_analysis_rejects_authorized_cell_identity_mismatch(tmp_path):
    root, experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["authorized_cells"][0]["trace_identity_sha256"] = "0" * 64
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="authorized cell identity mismatch"):
            v2_analysis.analyze(root, experiment, auth_path)


def test_v2_analysis_rejects_governance_witness_contract_mismatch(tmp_path):
    root = tmp_path
    row = _run_fixture(root, "EXP-V2-ANALYSIS-witness-s1", "control", "pair-1")
    experiment = root / "EXPERIMENTS" / "EXP-V2-ANALYSIS-WITNESS"
    experiment.mkdir(parents=True)
    row = {**row, "trace_seed": 1}
    _write(experiment / "request.json", {
        "experiment_id": experiment.name,
        "claim_boundary": {"can_claim": [], "cannot_claim": []},
    })
    _write(experiment / "run-manifest.json", {
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": experiment.name, "cells": [row],
    })
    _write(experiment / "analysis-request.json", {
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": experiment.name,
        "planned_run_ids": [row["run_id"]],
        "analysis": {"analysis_id": "AN-WITNESS", "primary_metric": "delivery_rate",
                     "planned_contrasts": []},
    })
    auth = {"status": "AUTHORIZED", "experiment_id": experiment.name,
            "authorized_cells": [row]}
    auth_path = experiment / "authorization.json"
    _write(auth_path, auth)
    auth_sha = v2_analysis.file_sha256(auth_path)
    result_dir = root / "CODE" / "Results" / row["run_id"]
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["authorization_sha256"] = auth_sha
    _write(formal_path, formal)
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["authorization_sha256"] = auth_sha
    governed["trace_manifest_schema"] = "leo-sim-trace-manifest/v1"
    _seal_governed(governed)
    _write(governed_path, governed)
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth):
        with pytest.raises(v2_analysis.V2AnalysisError, match="witness"):
            v2_analysis.analyze(root, experiment, auth_path)


def _single_run_analysis_fixture(tmp_path):
    root = tmp_path
    row = _run_fixture(root, "EXP-V2-ANALYSIS-external-s1", "control", "pair-1")
    experiment = root / "EXPERIMENTS" / "EXP-V2-ANALYSIS-EXTERNAL"
    experiment.mkdir(parents=True)
    row = {**row, "trace_seed": 1}
    _write(experiment / "request.json", {
        "experiment_id": experiment.name,
        "claim_boundary": {"can_claim": [], "cannot_claim": []},
    })
    _write(experiment / "run-manifest.json", {
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": experiment.name, "cells": [row],
    })
    _write(experiment / "analysis-request.json", {
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": experiment.name,
        "planned_run_ids": [row["run_id"]],
        "analysis": {"analysis_id": "AN-EXTERNAL", "primary_metric": "delivery_rate",
                     "planned_contrasts": []},
    })
    auth = {"status": "AUTHORIZED", "experiment_id": experiment.name,
            "authorized_cells": [row]}
    auth_path = experiment / "authorization.json"
    _write(auth_path, auth)
    auth_sha = v2_analysis.file_sha256(auth_path)
    result_dir = root / "CODE" / "Results" / row["run_id"]
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["authorization_sha256"] = auth_sha
    _write(formal_path, formal)
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["authorization_sha256"] = auth_sha
    _seal_governed(governed)
    _write(governed_path, governed)
    _write_external_witness(root, row["run_id"], governed,
                            authorization_sha256=auth_sha)
    return root, experiment, auth_path, row


def test_verify_result_accepts_canonical_remote_nonce_witness(tmp_path):
    root, _experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    local_witness = (root / "CODE" / "Results"
                     / "_external_launch_witness" / f"{row['run_id']}.json")
    remote_witness_root = root / ".remote_runtime" / "launches"
    remote_witness = remote_witness_root / ("b" * 32 + ".json")
    remote_witness_root.mkdir(parents=True)
    local_witness.replace(remote_witness)
    authorized = {
        **row,
        "authorization_sha256": v2_analysis.file_sha256(auth_path),
    }

    verified = v2_analysis._verify_result(
        root, root / "CODE" / "Results", remote_witness_root,
        row, authorized, "delivery_rate", require_external_witness=True,
        external_witness_by_nonce=True)

    assert verified["run_id"] == row["run_id"]
    assert verified["evidence_class"] == "v2_external_witness"
    assert any(item["path"].endswith(".remote_runtime/launches/" + "b" * 32 + ".json")
               for item in verified["artifacts"])


def test_verify_result_rejects_predecessor_from_another_deployment(tmp_path):
    root, _experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    result_dir = root / "CODE" / "Results" / row["run_id"]
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["source_git_commit"] = "0" * 40
    governed["payload_sha256"] = v2_analysis.canonical_sha({
        key: value for key, value in governed.items()
        if key != "payload_sha256"
    })
    _write(governed_path, governed)
    _write_external_witness(
        root, row["run_id"], governed,
        authorization_sha256=v2_analysis.file_sha256(auth_path))
    authorized = {
        **row,
        "authorization_sha256": v2_analysis.file_sha256(auth_path),
    }

    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="deployment identity mismatch"):
        v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate", require_external_witness=True,
            expected_deployment={
                "source_git_commit": "d" * 40,
                "source_tree_sha256": "e" * 64,
                "receipt_sha256": "f" * 64,
            })


def test_verify_result_rejects_invalid_governance_payload_hash(tmp_path):
    root, _experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    governed_path = (root / "CODE" / "Results" / row["run_id"]
                     / "governance_receipt.json")
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["payload_sha256"] = "0" * 64
    _write(governed_path, governed)
    _write_external_witness(
        root, row["run_id"], governed,
        authorization_sha256=v2_analysis.file_sha256(auth_path))
    authorized = {
        **row,
        "authorization_sha256": v2_analysis.file_sha256(auth_path),
    }

    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="payload hash mismatch"):
        v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate", require_external_witness=True)


def test_v2_analysis_requires_external_launch_witness(tmp_path):
    root, experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    witness = root / "CODE" / "Results" / "_external_launch_witness" / f"{row['run_id']}.json"
    witness.unlink()
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=json.loads(auth_path.read_text())):
        with pytest.raises(v2_analysis.V2AnalysisError, match="external launch witness"):
            v2_analysis.analyze(root, experiment, auth_path)


@pytest.mark.parametrize(("field", "value"), [
    ("status", "failed"), ("exit_code", 1),
    ("last_results_dir", "/tmp/not-the-canonical-result"),
])
def test_v2_analysis_rejects_external_launch_witness_terminal_identity(
        tmp_path, field, value):
    root, experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    witness_path = root / "CODE" / "Results" / "_external_launch_witness" / f"{row['run_id']}.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness[field] = value
    _write(witness_path, witness)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth):
        with pytest.raises(v2_analysis.V2AnalysisError, match="external launch witness"):
            v2_analysis.analyze(root, experiment, auth_path)

# ---------------------------------------------------------------- P0 fix:
# posterior analysis of governed historical runtimes.  A v5 run whose
# receipt records a runtime identity different from the local analyzer may be
# analyzed ONLY when the full formal chain (authorization cell + formal run
# witness + governance receipt v2 + nonce external witness) cross-binds the
# historical runtime code/config/auth/result hashes.  Default fail-closed;
# any tamper of the chain rejects.  Legacy v3/v4 (no external witness) keeps
# the exact-runtime requirement.

VM_RUNTIME_DEPS = {
    "python": "3.11.15", "numpy": "1.24.3",
    "simpy": "4.0.1", "pyyaml": "6.0.2",
}
POSTERIOR_CODE_SHA = "1" * 64


def _posterior_run_fixture(root: Path, run_id: str, arm_id: str,
                           pair: str) -> dict:
    """A fully chain-bound run whose receipt records an alien (historical)
    runtime identity: code sha and dependency versions differ from the local
    checkout while authorization/formal/governance/witness all bind that
    historical identity consistently."""
    row = _run_fixture(root, run_id, arm_id, pair)
    row["code_sha256"] = POSTERIOR_CODE_SHA
    result_dir = root / "CODE" / "Results" / run_id
    rcp_path = result_dir / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["code_sha256"] = POSTERIOR_CODE_SHA
    rcp["deps"] = dict(VM_RUNTIME_DEPS)
    _write(rcp_path, rcp)
    receipt_sha = v2_analysis.file_sha256(rcp_path)
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["code_sha256"] = POSTERIOR_CODE_SHA
    formal["receipt_sha256"] = receipt_sha
    _write(formal_path, formal)
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["run_receipt_sha256"] = receipt_sha
    governed["authorization_sha256"] = "a" * 64
    _seal_governed(governed)
    _write(governed_path, governed)
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    _write_external_witness(root, run_id, governed,
                            authorization_sha256="a" * 64)
    return row


def _package_experiment(root: Path, name: str, rows: list[dict],
                        *, primary: str = "delivery_rate") -> tuple[
                            Path, Path, dict]:
    """Write a V2 experiment package (request/matrix/analysis/auth) whose
    authorization hash is propagated back into formal/governed/witness."""
    experiment = root / "EXPERIMENTS" / name
    experiment.mkdir(parents=True)
    cells = [{**row, "trace_seed": 1} for row in rows]
    _write(experiment / "request.json", {
        "experiment_id": name,
        "claim_boundary": {"can_claim": ["none"],
                           "cannot_claim": ["not independently reviewed"]},
    })
    _write(experiment / "run-manifest.json", {
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": name, "cells": cells,
    })
    _write(experiment / "analysis-request.json", {
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": name,
        "planned_run_ids": [row["run_id"] for row in rows],
        "analysis": {
            "analysis_id": f"AN-{name}", "primary_metric": primary,
            "planned_contrasts": [],
        },
    })
    auth = {"status": "AUTHORIZED", "experiment_id": name,
            "authorized_cells": cells}
    auth_path = experiment / "authorization.json"
    _write(auth_path, auth)
    auth_sha = v2_analysis.file_sha256(auth_path)
    for row in rows:
        formal_path = root / "CODE" / "Results" / row["run_id"] / "formal_run.json"
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        formal["authorization_sha256"] = auth_sha
        _write(formal_path, formal)
        governed_path = root / "CODE" / "Results" / row["run_id"]             / "governance_receipt.json"
        governed = json.loads(governed_path.read_text(encoding="utf-8"))
        governed["authorization_sha256"] = auth_sha
        _seal_governed(governed)
        _write(governed_path, governed)
        governed = json.loads(governed_path.read_text(encoding="utf-8"))
        _write_external_witness(root, row["run_id"], governed,
                                authorization_sha256=auth_sha)
    with open(auth_path, "r", encoding="utf-8") as handle:
        auth_on_disk = json.load(handle)
    return experiment, auth_path, auth_on_disk


_STABLE_ANALYZER = {
    "git_commit": "d" * 40,
    "files": {
        "CODE/experiment_platform/v2_analysis.py": "e" * 64,
        "CODE/experiment_platform/isl_pressure.py": "0" * 64,
        "CODE/experiment_platform/isl_pressure_decision.py": "1" * 64,
        "CODE/leo_sim/metrics.py": "f" * 64,
        "CODE/leo_sim/receipt.py": "7" * 64,
    },
}


def test_v2_analysis_posterior_runtime_identity_succeeds(tmp_path):
    """Constraint 6b: a run whose historical runtime identity differs from
    the local analyzer IS analyzable when the full authorization + formal +
    governance + nonce-witness chain binds that identity."""
    root = tmp_path
    row = _posterior_run_fixture(
        root, "EXP-POSTERIOR-s1", "control", "pair-1")
    experiment, auth_path, auth = _package_experiment(
        root, "EXP-POSTERIOR", [row])
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth),             mock.patch.object(v2_analysis, "_analyzer_identity",
                              return_value=_STABLE_ANALYZER, create=True):
        manifest = v2_analysis.analyze(root, experiment, auth_path)
        out = root / "ANALYSIS" / "EXP-POSTERIOR"
        persisted = v2_analysis.write_outputs(root, out, manifest)
        ok, errors = v2_analysis.verify_persisted_analysis(
            root, out / "analysis-manifest.json")
    assert manifest["status"] == "VERIFIED"
    assert manifest["analysis_mode"] == "posterior_governed_runtime"
    assert all(result["runtime_identity_binding"] == "governance_bound_posterior"
               for result in manifest["run_results"])
    assert manifest["run_results"][0]["runtime_deps"] == VM_RUNTIME_DEPS
    assert manifest["run_results"][0]["code_sha256"] == POSTERIOR_CODE_SHA
    assert ok, errors
    assert persisted["status"] == "VERIFIED"


def test_verify_result_rejects_formal_code_hash_tamper(tmp_path):
    root, row, authorized = _posterior_single_fixture(tmp_path)
    result_dir = root / "CODE" / "Results" / row["run_id"]
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["code_sha256"] = "3" * 64
    _write(formal_path, formal)
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="formal witness identity mismatch"):
        v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate", require_external_witness=True)


def test_verify_result_rejects_authorized_code_hash_tamper(tmp_path):
    root, row, authorized = _posterior_single_fixture(tmp_path)
    authorized["code_sha256"] = "4" * 64
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="authorized cell identity mismatch"):
        v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate", require_external_witness=True)


def test_verify_result_rejects_governance_auth_hash_tamper(tmp_path):
    root, row, authorized = _posterior_single_fixture(tmp_path)
    governed_path = (root / "CODE" / "Results" / row["run_id"]
                     / "governance_receipt.json")
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["authorization_sha256"] = "5" * 64
    _write(governed_path, governed)
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="governance authorization hash mismatch"):
        v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate", require_external_witness=True)


def test_verify_result_rejects_external_witness_receipt_hash_tamper(
        tmp_path):
    root, row, authorized = _posterior_single_fixture(tmp_path)
    witness_path = (root / "CODE" / "Results" / "_external_launch_witness"
                    / f"{row['run_id']}.json")
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness["governance_receipt_sha256"] = "6" * 64
    _write(witness_path, witness)
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="external launch witness"):
        v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate", require_external_witness=True)


def test_verify_result_rejects_unbound_receipt_code_hash(tmp_path):
    """The receipt may not claim a runtime code identity the chain does not
    bind: receipt stamped with yet another code sha must be rejected."""
    root, row, authorized = _posterior_single_fixture(tmp_path)
    rcp_path = root / "CODE" / "Results" / row["run_id"] / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["code_sha256"] = "7" * 64
    _write(rcp_path, rcp)
    receipt_sha = v2_analysis.file_sha256(rcp_path)
    formal_path = root / "CODE" / "Results" / row["run_id"] / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["receipt_sha256"] = receipt_sha
    _write(formal_path, formal)
    governed_path = (root / "CODE" / "Results" / row["run_id"]
                     / "governance_receipt.json")
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["run_receipt_sha256"] = receipt_sha
    _seal_governed(governed)
    _write(governed_path, governed)
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    _write_external_witness(root, row["run_id"], governed,
                            authorization_sha256="a" * 64)
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="result identity mismatch"):
        v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate", require_external_witness=True)


def test_v2_analysis_rejects_mixed_exact_and_posterior_cohort(tmp_path):
    root = tmp_path
    exact = _run_fixture(root, "EXP-MIXED-exact-s1", "exact", "pair-1")
    posterior = _posterior_run_fixture(
        root, "EXP-MIXED-post-s1", "post", "pair-1")
    experiment, auth_path, auth = _package_experiment(
        root, "EXP-MIXED", [exact, posterior])
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth),             mock.patch.object(v2_analysis, "_analyzer_identity",
                              return_value=_STABLE_ANALYZER, create=True):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="mixes exact-runtime and posterior"):
            v2_analysis.analyze(root, experiment, auth_path)


def _posterior_single_fixture(tmp_path):
    root = tmp_path
    row = _posterior_run_fixture(
        root, "EXP-POSTERIOR-TAMP-s1", "control", "pair-1")
    authorized = {**row, "authorization_sha256": "a" * 64}
    return root, row, authorized


def test_legacy_receipt_rejects_different_runtime_identity(tmp_path):
    """Constraint 6c-minus: v3/v4 legacy evidence has no external witness,
    so a different analyzer/runtime identity must fail closed."""
    root = tmp_path
    row = _run_fixture(root, "EXP-LEGACY-POST-s1", "control", "pair-1")
    result_dir = root / "CODE" / "Results" / row["run_id"]
    rcp_path = result_dir / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["schema"] = v2_analysis.receipt_mod.LEGACY_RECEIPT_SCHEMA_V4
    _write(rcp_path, rcp)
    receipt_sha = v2_analysis.file_sha256(rcp_path)
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["receipt_sha256"] = receipt_sha
    _write(formal_path, formal)
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["schema"] = v2_analysis.GOVERNANCE_SCHEMA_V1
    governed["run_receipt_sha256"] = receipt_sha
    governed["authorization_sha256"] = "a" * 64
    _write(governed_path, governed)
    authorized = {**row, "authorization_sha256": "a" * 64}
    alien_identity = {"code_sha256": POSTERIOR_CODE_SHA,
                      "deps": VM_RUNTIME_DEPS}
    with mock.patch.object(
            v2_analysis.receipt_mod, "_verify_receipt_dir_artifacts_unbound",
            return_value=([], alien_identity)):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="legacy receipt cannot be analyzed"):
            v2_analysis._verify_result(
                root, root / "CODE" / "Results",
                root / "CODE" / "Results" / "_external_launch_witness",
                row, authorized, "delivery_rate",
                require_external_witness=False)


def test_legacy_receipt_exact_runtime_still_analyzes(tmp_path):
    """The old v3/v4 diagnostic contract keeps working when the local
    analyzer identity exactly matches the run-time record."""
    root = tmp_path
    row = _run_fixture(root, "EXP-LEGACY-EXACT-s1", "control", "pair-1")
    result_dir = root / "CODE" / "Results" / row["run_id"]
    rcp_path = result_dir / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["schema"] = v2_analysis.receipt_mod.LEGACY_RECEIPT_SCHEMA_V4
    _write(rcp_path, rcp)
    receipt_sha = v2_analysis.file_sha256(rcp_path)
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["receipt_sha256"] = receipt_sha
    _write(formal_path, formal)
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["schema"] = v2_analysis.GOVERNANCE_SCHEMA_V1
    governed["run_receipt_sha256"] = receipt_sha
    governed["authorization_sha256"] = "a" * 64
    _write(governed_path, governed)
    authorized = {**row, "authorization_sha256": "a" * 64}
    local_identity = {
        "code_sha256": v2_analysis.receipt_mod.code_sha256(),
        "deps": v2_analysis.receipt_mod.dependency_versions(),
    }
    with mock.patch.object(
            v2_analysis.receipt_mod, "_verify_receipt_dir_artifacts_unbound",
            return_value=([], local_identity)):
        verified = v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate",
            require_external_witness=False)
    assert verified["evidence_class"] == "legacy_v3_v4_internal_only"
    assert verified["runtime_identity_binding"] == "local_exact_match"
# ---------------------------------------------------------------- Re-review:
# 1) posterior semantics do NOT require the local host to reproduce the
#    historical runtime dependencies.  An unresolvable local dependency set
#    (e.g. tensorflow absent) only means "exact cannot be proven": a fully
#    bound v5 external-witness chain admits posterior, legacy (no witness)
#    still fails closed.
# 2) the posterior artifact verifier (receipt.py) itself must be bound by
#    the persisted analyzer identity.

def test_analyzer_identity_binds_receipt_verifier_file(monkeypatch):
    """receipt.py is load-bearing for posterior artifact verification, so a
    persisted analysis manifest must bind its exact bytes."""
    responses = [
        mock.Mock(stdout="1" * 40 + "\n"),
        mock.Mock(stdout=""),
    ]
    monkeypatch.setattr(v2_analysis.subprocess, "run",
                        mock.Mock(side_effect=responses))
    identity = _REAL_ANALYZER_IDENTITY()
    assert "CODE/leo_sim/receipt.py" in v2_analysis.ANALYZER_FILES
    repo_root = Path(__file__).resolve().parents[3]
    expected_sha = v2_analysis.file_sha256(
        repo_root / "CODE" / "leo_sim" / "receipt.py")
    assert identity["files"]["CODE/leo_sim/receipt.py"] == expected_sha


def _ddqn_unresolvable_fixture(tmp_path, run_id):
    """A fully chain-bound v5 run whose receipt records a historical DDQN
    runtime identity (deps key set includes tensorflow) while the local host
    cannot import tensorflow at all."""
    root = tmp_path
    row = _posterior_run_fixture(root, run_id, "control", "pair-1")
    result_dir = root / "CODE" / "Results" / run_id
    rcp_path = result_dir / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["mechanisms"]["requested"]["learning_algorithm"] = "ddqn"
    rcp["deps"] = dict(VM_RUNTIME_DEPS)
    rcp["deps"]["tensorflow"] = "2.12.0"
    _write(rcp_path, rcp)
    receipt_sha = v2_analysis.file_sha256(rcp_path)
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["receipt_sha256"] = receipt_sha
    _write(formal_path, formal)
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["run_receipt_sha256"] = receipt_sha
    _seal_governed(governed)
    _write(governed_path, governed)
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    _write_external_witness(root, run_id, governed,
                            authorization_sha256="a" * 64)
    identity = {"code_sha256": POSTERIOR_CODE_SHA, "deps": rcp["deps"]}
    return root, row, identity


def test_verify_result_ddq_runtime_with_unresolvable_local_tf_is_posterior(
        tmp_path):
    """Historical DDQN runtime + local tensorflow ImportError: the full v5
    governance chain admits posterior (never exact, never rejected)."""
    root, row, identity = _ddqn_unresolvable_fixture(
        tmp_path, "EXP-POSTERIOR-DDQN-s1")
    authorized = {**row, "authorization_sha256": "a" * 64}
    with mock.patch.object(
            v2_analysis.receipt_mod, "_verify_receipt_dir_artifacts_unbound",
            return_value=([], identity)), \
            mock.patch.object(
                v2_analysis.receipt_mod, "dependency_versions",
                side_effect=ImportError("no tensorflow on this host")):
        verified = v2_analysis._verify_result(
            root, root / "CODE" / "Results",
            root / "CODE" / "Results" / "_external_launch_witness",
            row, authorized, "delivery_rate",
            require_external_witness=True)
    assert verified["runtime_identity_binding"] == "governance_bound_posterior"
    assert verified["runtime_deps"]["tensorflow"] == "2.12.0"


def test_legacy_receipt_unresolvable_local_deps_still_rejected(tmp_path):
    """The same unresolvable local dependency set must still fail closed for
    legacy v3/v4 evidence: without an external witness nothing binds the
    historical runtime identity."""
    root, row, identity = _ddqn_unresolvable_fixture(
        tmp_path, "EXP-LEGACY-DDQN-s1")
    result_dir = root / "CODE" / "Results" / row["run_id"]
    rcp_path = result_dir / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["schema"] = v2_analysis.receipt_mod.LEGACY_RECEIPT_SCHEMA_V4
    _write(rcp_path, rcp)
    receipt_sha = v2_analysis.file_sha256(rcp_path)
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["receipt_sha256"] = receipt_sha
    _write(formal_path, formal)
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["schema"] = v2_analysis.GOVERNANCE_SCHEMA_V1
    governed["run_receipt_sha256"] = receipt_sha
    governed["authorization_sha256"] = "a" * 64
    _write(governed_path, governed)
    authorized = {**row, "authorization_sha256": "a" * 64}
    with mock.patch.object(
            v2_analysis.receipt_mod, "_verify_receipt_dir_artifacts_unbound",
            return_value=([], identity)), \
            mock.patch.object(
                v2_analysis.receipt_mod, "dependency_versions",
                side_effect=ImportError("no tensorflow on this host")):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="legacy receipt cannot be analyzed"):
            v2_analysis._verify_result(
                root, root / "CODE" / "Results",
                root / "CODE" / "Results" / "_external_launch_witness",
                row, authorized, "delivery_rate",
                require_external_witness=False)


@pytest.mark.parametrize("field", [
    "launch_nonce", "run_id", "authorization_sha256",
    "governance_receipt_sha256",
])
def test_v2_analysis_rejects_external_launch_witness_identity_mismatch(tmp_path, field):
    root, experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    witness_path = root / "CODE" / "Results" / "_external_launch_witness" / f"{row['run_id']}.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness[field] = "wrong" if field != "launch_nonce" else "c" * 32
    _write(witness_path, witness)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth):
        with pytest.raises(v2_analysis.V2AnalysisError, match="external launch witness"):
            v2_analysis.analyze(root, experiment, auth_path)


@pytest.mark.parametrize("field", v2_analysis.GOVERNANCE_WITNESS_FIELDS)
def test_v2_analysis_rejects_external_launch_witness_field_mismatch(tmp_path, field):
    root, experiment, auth_path, row = _single_run_analysis_fixture(tmp_path)
    witness_path = root / "CODE" / "Results" / "_external_launch_witness" / f"{row['run_id']}.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness["governance_witness"][field] = "wrong"
    _write(witness_path, witness)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization", return_value=auth):
        with pytest.raises(v2_analysis.V2AnalysisError, match="external launch witness"):
            v2_analysis.analyze(root, experiment, auth_path)
def _sealed_matrix_analysis_fixture(
        root: Path, *, include_design_accounting: bool = True,
        ) -> tuple[Path, Path, Path, list[dict]]:
    """Two-arm matrix run whose authorization is a realistic ISSUE-TIME
    artifact: sealed payload, bound work-finalization and artifact
    snapshots, authorized cells.  The strict recomputation gate is expected
    to be mocked away (simulating a newer analyzer checkout)."""
    rows = [
        _run_fixture(root, "EXP-V2-ANALYSIS-ctrl-s1", "control", "pair-1"),
        _run_fixture(root, "EXP-V2-ANALYSIS-treat-s1", "treatment", "pair-1"),
    ]
    experiment = root / "EXPERIMENTS" / "EXP-V2-ANALYSIS"
    experiment.mkdir(parents=True)
    cells = [dict(row) for row in rows]
    _write(experiment / "request.json", {
        "experiment_id": "EXP-V2-ANALYSIS",
        "claim_boundary": {"can_claim": ["none"],
                            "cannot_claim": ["not independently reviewed"]},
    })
    _write(experiment / "run-manifest.json", {
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": "EXP-V2-ANALYSIS", "cells": cells,
    })
    analysis_request = {
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": "EXP-V2-ANALYSIS",
        "planned_run_ids": [row["run_id"] for row in rows],
        "analysis": {
            "analysis_id": "AN-V2-ANALYSIS", "primary_metric": "delivery_rate",
            "planned_contrasts": [{"name": "treatment_minus_control",
                                   "left_arm": "treatment",
                                   "right_arm": "control"}],
        },
    }
    if include_design_accounting:
        analysis_request["design_accounting"] = {
            "schema": "leo-sim-matrix-design-accounting/v1",
            "planned_cells": 2,
            "unique_resolved_configurations": 1,
            "exact_reexecution_cells": 1,
            "exact_reexecution_groups": [{
                "config_sha256": rows[0]["config_sha256"],
                "run_ids": sorted(row["run_id"] for row in rows),
            }],
            "independent_condition_rule": (
                "one independent condition per unique resolved config SHA256"),
        }
    _write(experiment / "analysis-request.json", analysis_request)
    finalization = root / "CODE" / "work" / "WP-V2-ANALYSIS" / "DECISION.md"
    finalization.parent.mkdir(parents=True, exist_ok=True)
    finalization.write_text("sealed decision snapshot\n", encoding="utf-8")
    auth = {
        "schema": "experiment-execution-authorization/v1",
        "status": "AUTHORIZED",
        "experiment_id": "EXP-V2-ANALYSIS",
        "experiment_dir": str(experiment.relative_to(root)),
        "work_finalization": {
            "path": str(finalization.relative_to(root)),
            "sha256": v2_analysis.file_sha256(finalization),
        },
        "experiment_artifact_hashes": {
            str((experiment / name).relative_to(root)):
                v2_analysis.file_sha256(experiment / name)
            for name in ("request.json", "run-manifest.json",
                         "analysis-request.json")
        },
        "verification_policy": {"require_exact_matrix_cohort": True},
        "authorized_cells": rows,
    }
    auth["payload_sha256"] = v2_analysis.canonical_sha(auth)
    auth_path = experiment / "authorization.json"
    _write(auth_path, auth)
    auth_sha = v2_analysis.file_sha256(auth_path)
    for row in rows:
        result_dir = root / "CODE" / "Results" / row["run_id"]
        formal_path = result_dir / "formal_run.json"
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
        formal["authorization_sha256"] = auth_sha
        _write(formal_path, formal)
        governed_path = result_dir / "governance_receipt.json"
        governed = json.loads(governed_path.read_text(encoding="utf-8"))
        governed["authorization_sha256"] = auth_sha
        _seal_governed(governed)
        _write(governed_path, governed)
        _write_external_witness(root, row["run_id"], governed,
                                authorization_sha256=auth_sha)
    return root, experiment, auth_path, rows


def _deny_strict_auth():
    return mock.patch.object(
        v2_analysis.authorize_experiment, "verify_authorization",
        side_effect=authorize_experiment.AuthorizationError(
            "authorization no longer matches recomputed evidence"))


def test_v2_analysis_auto_admits_sealed_historical_authorization(tmp_path):
    root, experiment, auth_path, rows = _sealed_matrix_analysis_fixture(tmp_path)
    with _deny_strict_auth():
        manifest = v2_analysis.analyze(root, experiment, auth_path)
    assert manifest["status"] == "VERIFIED"
    assert manifest["authorization_verification"] == \
        v2_analysis.AUTHORIZATION_VERIFICATION_BOUND
    assert "no longer matches" in manifest["authorization_strict_error"]
    assert manifest["verified_run_ids"] == [row["run_id"] for row in rows]
    assert manifest["planned_contrasts"][0]["n_pairs"] == 1
    assert manifest["design_accounting"]["planned_cells"] == 2
    assert manifest["design_accounting"]["unique_resolved_configurations"] == 1
    assert manifest["design_accounting"]["exact_reexecution_cells"] == 1
    out = root / "ANALYSIS" / "EXP-V2-ANALYSIS"
    with _deny_strict_auth():
        v2_analysis.write_outputs(root, out, manifest)
        ok, errors = v2_analysis.verify_persisted_analysis(
            root, out / "analysis-manifest.json")
    assert ok, errors


def test_v2_analysis_derives_design_accounting_for_sealed_legacy_matrix(tmp_path):
    root, experiment, auth_path, rows = _sealed_matrix_analysis_fixture(
        tmp_path, include_design_accounting=False)
    with _deny_strict_auth():
        manifest = v2_analysis.analyze(root, experiment, auth_path)
    assert manifest["verified_run_ids"] == [row["run_id"] for row in rows]
    assert manifest["design_accounting"] == {
        "schema": "leo-sim-matrix-design-accounting/v1",
        "planned_cells": 2,
        "unique_resolved_configurations": 1,
        "exact_reexecution_cells": 1,
        "exact_reexecution_groups": [{
            "config_sha256": rows[0]["config_sha256"],
            "run_ids": sorted(row["run_id"] for row in rows),
        }],
        "independent_condition_rule": (
            "one independent condition per unique resolved config SHA256"),
    }


def test_v2_analysis_strict_mode_never_falls_back(tmp_path):
    root, experiment, auth_path, _rows = _sealed_matrix_analysis_fixture(tmp_path)
    with _deny_strict_auth():
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="authorization verification failed"):
            v2_analysis.analyze(root, experiment, auth_path,
                                authorization_mode="strict")


def test_v2_analysis_auto_rejects_unsupported_authorization_mode(tmp_path):
    root, experiment, auth_path, _rows = _sealed_matrix_analysis_fixture(tmp_path)
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="unsupported authorization mode"):
        v2_analysis.analyze(root, experiment, auth_path,
                            authorization_mode="sneaky")


def test_v2_analysis_auto_rejects_tampered_bound_artifact(tmp_path):
    root, experiment, auth_path, _rows = _sealed_matrix_analysis_fixture(tmp_path)
    request_path = experiment / "request.json"
    request_path.write_text(
        request_path.read_text(encoding="utf-8") + "\t", encoding="utf-8")
    with _deny_strict_auth():
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="bound artifact no longer matches"):
            v2_analysis.analyze(root, experiment, auth_path)


def test_v2_analysis_auto_rejects_rewritten_finalization(tmp_path):
    root, experiment, auth_path, _rows = _sealed_matrix_analysis_fixture(tmp_path)
    finalization = root / "CODE" / "work" / "WP-V2-ANALYSIS" / "DECISION.md"
    finalization.write_text("edited decision\n", encoding="utf-8")
    with _deny_strict_auth():
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="finalization no longer matches"):
            v2_analysis.analyze(root, experiment, auth_path)


def test_v2_analysis_auto_rejects_resealed_identity_forgery(tmp_path):
    """Even a forger who reseals the payload cannot inject an identity the
    historical witness chain does not bind: the cell cross-check rejects it."""
    root, experiment, auth_path, _rows = _sealed_matrix_analysis_fixture(tmp_path)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    auth["authorized_cells"][0]["code_sha256"] = "1" * 64
    auth.pop("payload_sha256", None)
    auth["payload_sha256"] = v2_analysis.canonical_sha(auth)
    _write(auth_path, auth)
    with _deny_strict_auth():
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="authorized cell identity mismatch"):
            v2_analysis.analyze(root, experiment, auth_path)


def test_v2_analysis_auto_rejects_wrong_recorded_artifact_digest(tmp_path):
    root, experiment, auth_path, _rows = _sealed_matrix_analysis_fixture(tmp_path)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))
    raw = "EXPERIMENTS/EXP-V2-ANALYSIS/request.json"
    auth["experiment_artifact_hashes"][raw] = "2" * 64
    auth.pop("payload_sha256", None)
    auth["payload_sha256"] = v2_analysis.canonical_sha(auth)
    _write(auth_path, auth)
    with _deny_strict_auth():
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="bound artifact no longer matches"):
            v2_analysis.analyze(root, experiment, auth_path)
