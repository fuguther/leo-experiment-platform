from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from CODE.experiment_platform import v2_analysis, v2_serial_gate


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _fixture(root: Path) -> tuple[Path, Path]:
    experiment = root / "EXPERIMENTS" / "EXP-SERIAL"
    cells = [
        {"run_id": "EXP-SERIAL-first", "arm_id": "first",
         "pairing_key": "pair", "trace_seed": 7},
        {"run_id": "EXP-SERIAL-second", "arm_id": "second",
         "pairing_key": "pair", "trace_seed": 7},
    ]
    _write(experiment / "request.json", {
        "experiment_id": "EXP-SERIAL",
        "execution_policy": {"mode": "serial_fail_closed"},
    })
    _write(experiment / "run-manifest.json", {
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": "EXP-SERIAL", "cells": cells,
    })
    _write(experiment / "analysis-request.json", {
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": "EXP-SERIAL",
        "planned_run_ids": [item["run_id"] for item in cells],
        "analysis": {"primary_metric": "isl_link_utilization_max"},
    })
    authorization = {
        "status": "AUTHORIZED", "experiment_id": "EXP-SERIAL",
        "authorized_cells": cells,
    }
    authorization_path = experiment / "authorization.json"
    _write(authorization_path, authorization)
    return experiment, authorization_path


def test_first_serial_cell_has_no_predecessor(tmp_path):
    experiment, authorization_path = _fixture(tmp_path)
    authorization = json.loads(authorization_path.read_text())
    with mock.patch.object(v2_serial_gate.authorize_experiment,
                           "verify_authorization", return_value=authorization), \
            mock.patch.object(v2_serial_gate.v2_analysis, "_verify_result") as verify:
        prior = v2_serial_gate.verify_predecessors(
            tmp_path, experiment, authorization_path, "EXP-SERIAL-first")
    assert prior == []
    verify.assert_not_called()


def test_existing_nonserial_v2_request_is_not_forced_into_matrix_gate(tmp_path):
    experiment = tmp_path / "EXPERIMENTS" / "EXP-SINGLE"
    authorization_path = experiment / "authorization.json"
    _write(experiment / "request.json", {"experiment_id": "EXP-SINGLE"})
    assert v2_serial_gate.verify_predecessors(
        tmp_path, experiment, authorization_path, "EXP-SINGLE-run") == []


def test_second_serial_cell_requires_verified_current_predecessor(tmp_path):
    experiment, authorization_path = _fixture(tmp_path)
    authorization = json.loads(authorization_path.read_text())
    with mock.patch.object(v2_serial_gate.authorize_experiment,
                           "verify_authorization", return_value=authorization), \
            mock.patch.object(v2_serial_gate.v2_analysis, "_analyzer_identity"), \
            mock.patch.object(v2_serial_gate.v2_analysis, "_verify_result",
                              return_value={"run_id": "EXP-SERIAL-first"}) as verify:
        prior = v2_serial_gate.verify_predecessors(
            tmp_path, experiment, authorization_path, "EXP-SERIAL-second")
    assert prior == ["EXP-SERIAL-first"]
    assert verify.call_args.kwargs["require_external_witness"] is True


def test_remote_serial_gate_uses_nonce_named_external_witness(tmp_path):
    experiment, authorization_path = _fixture(tmp_path)
    (tmp_path / ".deployment_commit").write_text("d" * 40 + "\n",
                                                   encoding="ascii")
    authorization = json.loads(authorization_path.read_text())
    with mock.patch.object(v2_serial_gate.authorize_experiment,
                           "verify_authorization", return_value=authorization), \
            mock.patch.object(v2_serial_gate.v2_analysis,
                              "_analyzer_identity") as analyzer_identity, \
            mock.patch.object(v2_serial_gate.v2_analysis, "_verify_result",
                              return_value={"run_id": "EXP-SERIAL-first"}) as verify:
        prior = v2_serial_gate.verify_predecessors(
            tmp_path, experiment, authorization_path, "EXP-SERIAL-second",
            external_witness_by_nonce=True,
            deployed_source_commit="d" * 40,
            expected_deployment={
                "source_git_commit": "d" * 40,
                "source_tree_sha256": "e" * 64,
                "receipt_sha256": "f" * 64,
            })
    assert prior == ["EXP-SERIAL-first"]
    analyzer_identity.assert_not_called()
    assert verify.call_args.kwargs["external_witness_by_nonce"] is True
    assert verify.call_args.kwargs["expected_deployment"]["source_tree_sha256"] \
        == "e" * 64


def test_remote_serial_gate_rejects_deployment_commit_mismatch(tmp_path):
    experiment, authorization_path = _fixture(tmp_path)
    (tmp_path / ".deployment_commit").write_text("e" * 40 + "\n",
                                                   encoding="ascii")
    authorization = json.loads(authorization_path.read_text())
    with mock.patch.object(v2_serial_gate.authorize_experiment,
                           "verify_authorization", return_value=authorization):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="deployment commit witness mismatch"):
            v2_serial_gate.verify_predecessors(
                tmp_path, experiment, authorization_path, "EXP-SERIAL-second",
                external_witness_by_nonce=True,
                deployed_source_commit="d" * 40)


def test_second_serial_cell_fails_when_predecessor_evidence_fails(tmp_path):
    experiment, authorization_path = _fixture(tmp_path)
    authorization = json.loads(authorization_path.read_text())
    with mock.patch.object(v2_serial_gate.authorize_experiment,
                           "verify_authorization", return_value=authorization), \
            mock.patch.object(v2_serial_gate.v2_analysis, "_analyzer_identity"), \
            mock.patch.object(v2_serial_gate.v2_analysis, "_verify_result",
                              side_effect=v2_analysis.V2AnalysisError(
                                  "zero-rate hold makes the analysis ineligible")):
        with pytest.raises(v2_analysis.V2AnalysisError, match="zero-rate hold"):
            v2_serial_gate.verify_predecessors(
                tmp_path, experiment, authorization_path, "EXP-SERIAL-second")


def test_formal_runner_invokes_serial_gate_before_remote_launch():
    script = (Path(__file__).resolve().parents[2]
              / "scripts" / "remote" / "run-remote.sh").read_text()
    assert "CODE.experiment_platform.v2_serial_gate" in script
    assert script.index("CODE.experiment_platform.v2_serial_gate") < \
        script.index('"$SSH_BIN" "$REMOTE_HOST_ALIAS"')
