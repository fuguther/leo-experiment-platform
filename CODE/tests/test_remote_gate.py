"""Formal governance gate tests (remote_job.v2_governance_errors)."""
import sys
import json
import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

# remote_job.py imports its sibling modules as top-level names (VM layout)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "remote"))

from CODE.scripts.remote import remote_job


def _base_receipt():
    return {
        "mechanisms": {"requested": {}, "effective": {}},
        "fate_counts": {"DELIVERED": 1},
        "control": {"counters": {"arrived": 1}},
    }


def test_require_data_isl_uses_recomputed_multisat_not_diagnostic_occupied():
    """The formal gate must not rest on the diagnostic occupied field: a
    delivery path spanning >=2 satellites proves real data ISL service."""
    # occupied claims ISL service but no multi-sat delivery exists -> gate
    # must fail even though the (diagnostic) field is positive
    receipt = _base_receipt()
    ledgers = {"deliveries": {"1": {"path": [0]}},
               "occupied": {"isl_s": 999.0}}
    errors = remote_job.v2_governance_errors(
        receipt, ledgers, {"require_data_isl": True}, [])
    assert any("data ISL" in e for e in errors)
    # real multi-sat delivery satisfies the gate even if occupied is zero
    ledgers = {"deliveries": {"1": {"path": [0, 1]}},
               "occupied": {"isl_s": 0.0}}
    errors = remote_job.v2_governance_errors(
        receipt, ledgers, {"require_data_isl": True}, [])
    assert not any("data ISL" in e for e in errors)


def test_v2_governance_receipt_binds_raw_config_and_trace_contract(tmp_path):
    receipt_path = tmp_path / "receipt.json"
    config_path = tmp_path / "resolved_config.json"
    manifest_path = tmp_path / "manifest.json"
    receipt = {"schema": "leo-sim-receipt/v5",
               "trace_identity_contract": "leo-sim-trace-identity/v2",
               "natural_end": True, "conservation_ok": True}
    ledgers = {}
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    config_path.write_text('{"config": {"scenario": {}}}\n', encoding="utf-8")
    manifest_path.write_text(json.dumps({"schema": "leo-sim-trace-manifest/v2"}) + "\n",
                              encoding="utf-8")
    deployment = {"source_git_commit": "c" * 40,
                  "source_tree_sha256": "d" * 64}
    governed = remote_job.build_v2_governance_receipt(
        receipt=receipt, ledgers=ledgers, verification_errors=[], acceptance={},
        run_id="run-1", launch_nonce="a" * 32, authorization_sha256="b" * 64,
        deployment=deployment, deployment_receipt_sha256="e" * 64,
        execution_chain_sha256="f" * 64, receipt_path=receipt_path,
        resolved_config_path=config_path, manifest_path=manifest_path)
    assert governed["schema"] == "leo-sim-governance-receipt/v2"
    assert governed["receipt_schema"] == receipt["schema"]
    assert governed["trace_manifest_schema"] == "leo-sim-trace-manifest/v2"
    assert governed["trace_identity_contract"] == receipt["trace_identity_contract"]
    assert len(governed["resolved_config_sha256"]) == 64
    assert len(governed["trace_manifest_sha256"]) == 64
    unsigned = {key: value for key, value in governed.items()
                if key != "payload_sha256"}
    assert governed["payload_sha256"] == remote_job.canonical_sha(unsigned)


def test_remote_v2_predecessor_gate_uses_canonical_nonce_witnesses(tmp_path,
                                                                   monkeypatch):
    root = tmp_path
    experiment = root / "EXPERIMENTS" / "EXP-SERIAL"
    config = experiment / "resolved" / "EXP-SERIAL-second.leo-sim.yaml"
    authorization = experiment / "authorization.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")
    authorization.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(remote_job, "CANONICAL_WORKSPACE", root)
    monkeypatch.setattr(remote_job, "CANONICAL_RESULTS", root / "CODE" / "Results")
    monkeypatch.setattr(remote_job, "CANONICAL_RUNTIME", root / ".remote_runtime")
    args = SimpleNamespace(runtime_kind="leo_sim_v2",
                           expected_run_id="EXP-SERIAL-second")
    deployment = {"source_git_commit": "d" * 40}
    from CODE.experiment_platform import v2_serial_gate
    with mock.patch.object(v2_serial_gate, "verify_predecessors",
                           return_value=["EXP-SERIAL-first"]) as verify:
        assert remote_job.verify_v2_serial_predecessors(
            args, config, authorization, deployment) == ["EXP-SERIAL-first"]
    verify.assert_called_once_with(
        root, experiment, authorization, "EXP-SERIAL-second",
        results_root=root / "CODE" / "Results",
        external_witness_root=root / ".remote_runtime" / "launches",
        external_witness_by_nonce=True,
        deployed_source_commit="d" * 40,
        expected_deployment=deployment)


def test_remote_prepare_and_run_both_enforce_predecessor_gate():
    prepare_source = inspect.getsource(remote_job.prepare_launch)
    run_source = inspect.getsource(remote_job.run_formal)
    assert "verify_v2_serial_predecessors" in prepare_source
    assert "verify_v2_serial_predecessors" in run_source
    assert "verify_v2_run_authorization" in prepare_source
    assert "verify_v2_run_authorization" in run_source
    assert run_source.index("verify_v2_serial_predecessors") < \
        run_source.index("subprocess.Popen")
    assert run_source.index("verify_v2_run_authorization") < \
        run_source.index("subprocess.Popen")


def test_remote_run_recomputes_authorization_for_nonserial_v2(
        tmp_path, monkeypatch):
    root = tmp_path
    config = root / "EXPERIMENTS" / "EXP-SINGLE" / "resolved" / "run.leo-sim.yaml"
    authorization = config.parents[1] / "authorization.json"
    args = SimpleNamespace(runtime_kind="leo_sim_v2", expected_run_id="run")
    monkeypatch.setattr(remote_job, "CANONICAL_WORKSPACE", root)
    from CODE.experiment_platform import authorize_experiment
    with mock.patch.object(
            authorize_experiment, "verify_authorization_for_leo_sim_v2_config") as verify:
        remote_job.verify_v2_run_authorization(args, config, authorization)
    verify.assert_called_once_with(root, authorization, config, "run")


def test_direct_remote_run_blocks_second_cell_without_predecessor(
        tmp_path, monkeypatch):
    """Bypass the local launcher entirely; the deployed run path must block."""
    root = tmp_path
    code = root / "CODE"
    results = code / "Results"
    runtime = root / ".remote_runtime"
    experiment = root / "EXPERIMENTS" / "EXP-SERIAL"
    config = experiment / "resolved" / "EXP-SERIAL-second.leo-sim.yaml"
    authorization = experiment / "authorization.json"
    log = results / "_overnight_logs" / "second.log"
    status = runtime / "current_status.json"
    cells = [
        {"run_id": "EXP-SERIAL-first", "arm_id": "first",
         "pairing_key": "pair", "trace_seed": 7},
        {"run_id": "EXP-SERIAL-second", "arm_id": "second",
         "pairing_key": "pair", "trace_seed": 7},
    ]
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")
    authorization.write_text("{}\n", encoding="utf-8")
    (root / ".deployment_commit").write_text("d" * 40 + "\n",
                                               encoding="ascii")
    (experiment / "request.json").write_text(json.dumps({
        "experiment_id": "EXP-SERIAL",
        "execution_policy": {"mode": "serial_fail_closed"},
    }) + "\n", encoding="utf-8")
    (experiment / "run-manifest.json").write_text(json.dumps({
        "schema": "leo-sim-experiment-matrix-manifest/v1",
        "experiment_id": "EXP-SERIAL", "cells": cells,
    }) + "\n", encoding="utf-8")
    (experiment / "analysis-request.json").write_text(json.dumps({
        "schema": "leo-sim-matrix-analysis-request/v1",
        "experiment_id": "EXP-SERIAL",
        "planned_run_ids": [cell["run_id"] for cell in cells],
        "analysis": {"primary_metric": "isl_link_utilization_max"},
    }) + "\n", encoding="utf-8")
    authorized = {
        "status": "AUTHORIZED", "experiment_id": "EXP-SERIAL",
        "authorized_cells": cells,
    }
    nonce = "a" * 32
    prepared = {
        "status": "prepared", "launch_nonce": nonce,
        "session_name": "serial-second", "run_id": "EXP-SERIAL-second",
        "config_sha256": "b" * 64,
        "authorization_sha256": "c" * 64,
    }
    launch_path = runtime / "launches" / f"{nonce}.json"
    launch_path.parent.mkdir(parents=True)
    launch_path.write_text(json.dumps(prepared) + "\n", encoding="utf-8")
    args = SimpleNamespace(
        action="run", runtime_kind="leo_sim_v2", launch_nonce=nonce,
        session_name="serial-second", expected_run_id="EXP-SERIAL-second",
        expected_config_sha256="b" * 64,
        expected_authorization_sha256="c" * 64,
        deployment_receipt=str(runtime / "deployment.json"),
        cpu_list="", status_file=str(status), log_file=str(log),
        workdir=str(code), config=str(config), authorization=str(authorization),
        no_monitor=False, bundle=False, bundle_stages="",
    )
    deployment = {
        "source_git_commit": "d" * 40, "source_git_branch": "main",
        "source_git_dirty": False, "source_tree_sha256": "e" * 64,
        "receipt_sha256": "f" * 64,
    }
    monkeypatch.setattr(remote_job, "CANONICAL_WORKSPACE", root)
    monkeypatch.setattr(remote_job, "CANONICAL_CODE", code)
    monkeypatch.setattr(remote_job, "CANONICAL_RESULTS", results)
    monkeypatch.setattr(remote_job, "CANONICAL_RUNTIME", runtime)
    monkeypatch.setattr(remote_job, "CANONICAL_STATUS", status)
    monkeypatch.setattr(remote_job, "parse_args", lambda: args)
    monkeypatch.setattr(remote_job, "validate_formal_paths",
                        lambda _args: (code, status, log, config, authorization))
    monkeypatch.setattr(remote_job, "verify_receipt", lambda _path: deployment)
    monkeypatch.setattr(remote_job, "validate_expected_identity",
                        lambda *_args: None)
    monkeypatch.setattr(remote_job, "validate_cpu_affinity", lambda _value: [])
    monkeypatch.setattr(remote_job, "verify_v2_run_authorization",
                        lambda *_args: None)
    persisted = []
    monkeypatch.setattr(remote_job, "persist_status",
                        lambda _args, payload, **_kwargs: persisted.append(dict(payload)))
    from CODE.experiment_platform import v2_serial_gate
    monkeypatch.setattr(v2_serial_gate.authorize_experiment,
                        "verify_authorization", lambda *_args: authorized)
    popen = mock.Mock()
    monkeypatch.setattr(remote_job.subprocess, "Popen", popen)

    assert remote_job.main() == 2
    popen.assert_not_called()
    assert persisted[-1]["status"] == "failed"
    assert persisted[-1]["failure_stage"] == "run_preflight"
    assert "result" in persisted[-1]["error"]
