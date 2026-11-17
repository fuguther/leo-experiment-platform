"""The VM formal gate and the local pull-back gate (round 2, 2026-09-25).

Two receipt forms meet the SAME gate:

  * a configuration that records ZERO decision rows is signed v5, which carries
    no stream binding at all;
  * a configuration that records decision rows is signed v6, whose two stream
    digests must be recomputed.

The gate used to hand both stream paths over unconditionally, so the first form
failed at failure_stage=receipt_verification AFTER the whole simulation had
run: verify_receipt_dir answers "stream paths were supplied but this receipt is
leo-sim-receipt/v5" -- correct at the API level, wrong at the gate.  Removing
the handover must not turn the v6 recomputation off, so both directions are
asserted here.

The run directories are produced through the argv remote_job.formal_command
builds, minus the three formal-identity flags (authorization / launch nonce /
expected run id): those need a work finalization and a decision chain, they do
not touch the stream contract, and requiring them here would make this a
slow integration test rather than a regression test.  The stream flags, the
paths and the verification function are the ones the VM uses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from CODE.leo_sim import pullback as pullback_mod
from CODE.leo_sim import receipt as receipt_mod

REPO = Path(__file__).resolve().parents[3]
# remote_job imports its sibling deployment_guard by bare name, exactly as it
# does on the VM where the launcher runs it from that directory.
sys.path.insert(0, str(REPO / "CODE" / "scripts" / "remote"))
from CODE.scripts.remote import remote_job as rj  # noqa: E402

STREAM_FLAGS = ("--decision-log", "--timeline-log")


def _config(policy: str) -> dict:
    from CODE.leo_sim import config as config_mod
    return config_mod.resolve_config({
        "scenario": {"duration_s": 40.0, "num_satellites": 8, "num_planes": 1,
                     "seed": 7},
        "endpoints": {"sites": [{"name": "a", "lat": 0.1, "lon": 0.1},
                                {"name": "b", "lat": 52.6, "lon": 90.4}]},
        "demand": {"mode": "csv",
                   "csv_path": "CODE/data/traffic/t1_step5_micro_ab.csv"},
        "control_plane": {"enabled": False},
        "execution": {"available_capacity_interval_s": 0.1},
        "routing": {"policy": policy},
        "learning": {"algorithm": "none"},
    })


def _formal_argv(config_path: Path, run_id: str) -> list[str]:
    """The real formal argv, then the three identity flags dropped.

    The two stream flags and their paths are asserted against --out BEFORE the
    identity flags are removed, so this stays a statement about the formal
    vector itself.
    """
    argv = rj.formal_command(
        argparse.Namespace(runtime_kind="leo_sim_v2", expected_run_id=run_id,
                           launch_nonce="0" * 32),
        REPO / "CODE", config_path, REPO / "EXPERIMENTS" / "authorization.json")
    out_dir = Path(argv[argv.index("--out") + 1])
    assert out_dir.is_dir() and not any(out_dir.iterdir())
    for flag in STREAM_FLAGS:
        assert flag in argv, "the formal entry must request both streams"
        assert Path(argv[argv.index(flag) + 1]).parent == out_dir
    for flag in ("--authorization", "--launch-nonce", "--expect-run-id"):
        index = argv.index(flag)
        del argv[index:index + 2]
    return argv


def _run(tmp_path: Path, policy: str, run_id: str) -> Path:
    resolved = _config(policy)
    config_path = tmp_path / f"{policy}.yaml"
    config_path.write_text(json.dumps(
        {"config_version": resolved["version"], **resolved["config"]},
        indent=2, sort_keys=True), encoding="utf-8")
    argv = _formal_argv(config_path, run_id)
    proc = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return Path(argv[argv.index("--out") + 1])


def _with_stream_witness(source: Path, target: Path) -> Path:
    """Copy a synthetic run and add the formal witness its real CLI writes."""
    shutil.copytree(source, target)
    timeline_sha = hashlib.sha256(
        (target / "timeline.jsonl").read_bytes()).hexdigest()
    formal_path = target / "formal_run.json"
    receipt = json.loads((target / "receipt.json").read_text(encoding="utf-8"))
    ledgers = json.loads((target / "ledgers.json").read_text(encoding="utf-8"))
    from CODE.leo_sim import __main__ as cli_mod
    cli_mod._write_formal_witness(
        str(target),
        {"run_id": target.name, "launch_nonce": "0" * 32,
         "authorization_sha256": "a" * 64,
         "config_sha256": receipt["config_sha256"],
         "code_sha256": receipt["code_sha256"],
         "results_dir": str(target.parent)},
        receipt, timeline_log_sha256=timeline_sha)
    governed = rj.build_governance_receipt(
        receipt=receipt, ledgers=ledgers, verification_errors=[],
        acceptance={}, run_id=target.name, launch_nonce="0" * 32,
        authorization_sha256="a" * 64,
        deployment={"source_git_commit": "b" * 40,
                    "source_tree_sha256": "c" * 64},
        deployment_receipt_sha256="d" * 64,
        execution_chain_sha256={"CODE/example.py": "e" * 64},
        receipt_path=target / "receipt.json",
        resolved_config_path=target / "resolved_config.json",
        manifest_path=target / "manifest.json",
        formal_run_path=formal_path)
    rj.write_json(target / "governance_receipt.json", governed)
    return target


def _copy_vm_run(source: Path, target: Path) -> Path:
    """Copy a VM fixture under its new run-id and rebind local witnesses."""
    shutil.copytree(source, target)
    formal_path = target / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["run_id"] = target.name
    rj.write_json(formal_path, formal)
    governed_path = target / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["run_id"] = target.name
    governed["formal_run_sha256"] = hashlib.sha256(
        formal_path.read_bytes()).hexdigest()
    unsigned = {key: value for key, value in governed.items()
                if key != "payload_sha256"}
    governed["payload_sha256"] = rj.canonical_sha(unsigned)
    rj.write_json(governed_path, governed)
    return target


def _write_sealed_governance(path: Path, governed: dict) -> None:
    unsigned = {key: value for key, value in governed.items()
                if key != "payload_sha256"}
    governed["payload_sha256"] = rj.canonical_sha(unsigned)
    rj.write_json(path, governed)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    # The two canonical VM paths are relocated for the test, exactly as the
    # round-3 reviewers relocated them (there is no VM here).  Nothing else
    # about formal_command changes.
    original_results = rj.CANONICAL_RESULTS
    rj.CANONICAL_RESULTS = tmp_path_factory.mktemp("Results")
    try:
        root = tmp_path_factory.mktemp("formal-streams")
        zero = _run(root, "hop", "EXP-GATE-ZERO-s7")
        nonzero = _run(root, "oracle", "EXP-GATE-NONZERO-s7")
    finally:
        rj.CANONICAL_RESULTS = original_results
    return {"zero": zero, "nonzero": nonzero}


def _rows(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
               if line.strip())


def test_a_zero_decision_formal_run_is_signed_v5_and_passes_the_gate(
        runs, tmp_path):
    run = runs["zero"]
    receipt = json.loads((run / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == receipt_mod.RECEIPT_SCHEMA
    assert _rows(run / "decisions.jsonl") == 0
    assert _rows(run / "timeline.jsonl") > 0

    certified = _with_stream_witness(run, tmp_path / "v5-certified")
    assert rj.verify_v2_formal_result(certified) == []
    expected_authority = {
        "run_id": certified.name,
        "launch_nonce": "0" * 32,
        "authorization_sha256": "a" * 64,
        "source_git_commit": "b" * 40,
        "source_tree_sha256": "c" * 64,
        "deployment_receipt_sha256": "d" * 64,
        "execution_chain_sha256": {"CODE/example.py": "e" * 64},
        "acceptance": {},
    }
    assert rj.verify_v2_formal_result(
        certified, require_governance=True,
        expected_authority=expected_authority) == []

    tampered = tmp_path / "v5-timeline-tampered"
    _copy_vm_run(certified, tampered)
    (tampered / "timeline.jsonl").write_bytes(
        (tampered / "timeline.jsonl").read_bytes() + b'{"extra": 1}\n')
    errors = rj.verify_v2_formal_result(tampered)
    assert errors and "timeline_log_sha256 does not match" in errors[0]

    unbound = tmp_path / "v5-timeline-unbound"
    _copy_vm_run(certified, unbound)
    formal_path = unbound / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal.pop("timeline_log_sha256")
    formal_path.write_text(json.dumps(formal, indent=2, sort_keys=True) + "\n",
                           encoding="utf-8")
    errors = rj.verify_v2_formal_result(unbound)
    assert errors and "does not bind timeline.jsonl" in errors[0]

    jointly_tampered = tmp_path / "v5-jointly-tampered"
    _copy_vm_run(certified, jointly_tampered)
    changed_timeline = (jointly_tampered / "timeline.jsonl").read_bytes() + \
        b'{"joint": true}\n'
    (jointly_tampered / "timeline.jsonl").write_bytes(changed_timeline)
    changed_formal_path = jointly_tampered / "formal_run.json"
    changed_formal = json.loads(
        changed_formal_path.read_text(encoding="utf-8"))
    changed_formal["timeline_log_sha256"] = hashlib.sha256(
        changed_timeline).hexdigest()
    changed_formal_path.write_text(
        json.dumps(changed_formal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    errors = rj.verify_v2_formal_result(jointly_tampered)
    assert errors and "formal_run_sha256 does not match" in errors[0]

    legacy_governance = tmp_path / "v5-legacy-governance-v2"
    _copy_vm_run(certified, legacy_governance)
    governed_path = legacy_governance / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["schema"] = "leo-sim-governance-receipt/v2"
    governed_path.write_text(json.dumps(governed), encoding="utf-8")
    errors = rj.verify_v2_formal_result(
        legacy_governance, require_governance=True)
    assert any("governance_receipt v2" in error for error in errors)

    missing_governance_field = tmp_path / "v5-missing-governance-field"
    _copy_vm_run(certified, missing_governance_field)
    governed_path = missing_governance_field / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed.pop("acceptance")
    governed_path.write_text(json.dumps(governed), encoding="utf-8")
    errors = rj.verify_v2_formal_result(
        missing_governance_field, require_governance=True)
    assert any("missing contract fields" in error for error in errors)

    authority_tampered = tmp_path / "v5-authority-tampered"
    _copy_vm_run(certified, authority_tampered)
    governed_path = authority_tampered / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["execution_chain_sha256"] = {
        "CODE/example.py": "f" * 64}
    _write_sealed_governance(governed_path, governed)
    errors = rj.verify_v2_formal_result(
        authority_tampered, require_governance=True,
        expected_authority={**expected_authority,
                            "run_id": authority_tampered.name})
    assert any("execution_chain_sha256 does not match" in error
               for error in errors), errors

    deployment_tampered = tmp_path / "v5-deployment-tampered"
    _copy_vm_run(certified, deployment_tampered)
    governed_path = deployment_tampered / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["source_git_commit"] = "f" * 40
    _write_sealed_governance(governed_path, governed)
    errors = rj.verify_v2_formal_result(
        deployment_tampered, require_governance=True,
        expected_authority={**expected_authority,
                            "run_id": deployment_tampered.name})
    assert any("source_git_commit does not match" in error
               for error in errors), errors

    nonce_auth_tampered = tmp_path / "v5-nonce-auth-tampered"
    _copy_vm_run(certified, nonce_auth_tampered)
    formal_path = nonce_auth_tampered / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["launch_nonce"] = "1" * 32
    formal["authorization_sha256"] = "2" * 64
    rj.write_json(formal_path, formal)
    governed_path = nonce_auth_tampered / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["launch_nonce"] = "1" * 32
    governed["authorization_sha256"] = "2" * 64
    governed["formal_run_sha256"] = hashlib.sha256(
        formal_path.read_bytes()).hexdigest()
    _write_sealed_governance(governed_path, governed)
    errors = rj.verify_v2_formal_result(
        nonce_auth_tampered, require_governance=True,
        expected_authority={**expected_authority,
                            "run_id": nonce_auth_tampered.name})
    assert any("launch_nonce does not match" in error
               for error in errors), errors
    assert any("authorization_sha256 does not match" in error
               for error in errors)

    # …and the form that WAS there before the fix mis-failed exactly this run,
    # after the simulation had already been paid for.
    old_form = receipt_mod.verify_receipt_dir(
        str(run), decision_log=str(run / "decisions.jsonl"),
        timeline_log=str(run / "timeline.jsonl"))
    assert old_form == ["stream paths were supplied but this receipt is "
                        "leo-sim-receipt/v5, which carries no stream binding"]


def test_a_nonzero_decision_formal_run_is_signed_v6_and_its_digests_are_recomputed(
        runs, tmp_path):
    run = runs["nonzero"]
    receipt = json.loads((run / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["schema"] == receipt_mod.RECEIPT_SCHEMA_V6
    assert _rows(run / "decisions.jsonl") > 0
    certified = _with_stream_witness(run, tmp_path / "v6-certified")
    assert rj.verify_v2_formal_result(certified) == []

    appended = tmp_path / "appended"
    _copy_vm_run(certified, appended)
    (appended / "decisions.jsonl").write_bytes(
        (appended / "decisions.jsonl").read_bytes() + b'{"extra": 1}\n')
    errors = rj.verify_v2_formal_result(appended)
    assert errors and "decision_log_sha256 does not match" in errors[0]

    dropped = tmp_path / "dropped"
    _copy_vm_run(certified, dropped)
    (dropped / "timeline.jsonl").unlink()
    errors = rj.verify_v2_formal_result(dropped)
    assert errors and "timeline_log_sha256: missing or symbolic stream file" \
        in errors[0]


def test_the_gate_never_hands_over_stream_paths_unconditionally():
    """The VM path cannot be executed here, so its SHAPE is pinned instead."""
    source = (REPO / "CODE" / "scripts" / "remote" / "remote_job.py").read_text(
        encoding="utf-8")
    assert "verify_v2_formal_result" in source
    assert "verify_receipt_streams(" in source
    assert "require_streams=True" in source
    assert "require_formal_witness=True" in source
    assert "decision_log=str(_run_dir" not in source
    assert "timeline_log=str(_run_dir" not in source


def test_a_zero_decision_dir_that_gained_a_row_is_rejected(runs, tmp_path):
    """The v5 downgrade must be PROVEN, not assumed."""
    broken = tmp_path / "contradiction"
    certified = _with_stream_witness(
        runs["zero"], tmp_path / "zero-certified")
    _copy_vm_run(certified, broken)
    (broken / "decisions.jsonl").write_text('{"pid": 1}\n', encoding="utf-8")
    errors = rj.verify_v2_formal_result(broken)
    assert errors and "contradicts its own run directory" in errors[0]


# ------------------------------------------------------ local pull-back gate
def test_pullback_accepts_an_intact_diagnostic_result_set(runs):
    for run in runs.values():
        report = pullback_mod.verify_pulled_run(run, expect="diagnostic")
        assert report["ok"] is True, report["errors"]
        # ok means "this copy is intact and the covered bindings hold".  It
        # never means claimable: formal eligibility is not decided here.
        assert report["claimable"] is False
        assert report["formal_eligibility"]["status"] == "NOT_EVALUATED"
        assert report["checks"][-1]["skipped"] is True


def test_pullback_rejects_an_incomplete_or_tampered_copy(runs, tmp_path):
    def copy(name: str) -> Path:
        target = tmp_path / name
        shutil.copytree(runs["nonzero"], target)
        return target

    missing_stream = copy("missing-stream")
    (missing_stream / "timeline.jsonl").unlink()
    report = pullback_mod.verify_pulled_run(missing_stream, expect="diagnostic")
    assert report["ok"] is False
    assert [c["id"] for c in report["checks"] if not c["ok"]] == ["P4"]

    tampered = copy("tampered-stream")
    (tampered / "decisions.jsonl").write_bytes(
        (tampered / "decisions.jsonl").read_bytes() + b'{"extra": 1}\n')
    report = pullback_mod.verify_pulled_run(tampered, expect="diagnostic")
    assert report["ok"] is False
    assert [c["id"] for c in report["checks"] if not c["ok"]] == ["P4"]

    missing_core = copy("missing-core")
    (missing_core / "ledgers.json").unlink()
    report = pullback_mod.verify_pulled_run(missing_core, expect="diagnostic")
    assert report["ok"] is False
    assert {c["id"] for c in report["checks"] if not c["ok"]} == {"P1", "P3"}

    tampered_ledger = copy("tampered-ledger")
    ledgers = json.loads((tampered_ledger / "ledgers.json").read_text())
    ledgers["stop_time_s"] = float(ledgers["stop_time_s"]) + 1.0
    (tampered_ledger / "ledgers.json").write_text(json.dumps(ledgers),
                                                  encoding="utf-8")
    report = pullback_mod.verify_pulled_run(tampered_ledger,
                                            expect="diagnostic")
    assert report["ok"] is False
    assert [c["id"] for c in report["checks"] if not c["ok"]] == ["P3"]


def test_a_recorded_vm_verdict_cannot_make_an_incomplete_copy_pass(runs, tmp_path):
    """\"the VM verified it\" is a statement about the VM's directory.

    The pull tolerates absent files by construction, so the local copy is a
    different byte set and nothing carries the VM's verdict across.  This
    builds the strongest version of the claim -- a governance receipt that
    records research_eligible=true and verification_errors=[] -- around a copy
    the pull silently truncated, and requires the local gate to reject it.
    """
    truncated = tmp_path / "EXP-GATE-NONZERO-s7"
    shutil.copytree(runs["nonzero"], truncated)
    (truncated / "timeline.jsonl").unlink()
    (truncated / "formal_run.json").write_text(json.dumps({
        "schema": "leo-sim-formal-run/v1", "run_id": truncated.name,
        "launch_nonce": "0" * 32,
        "receipt_sha256": receipt_mod.hashlib.sha256(
            (truncated / "receipt.json").read_bytes()).hexdigest(),
        "natural_end": True, "conservation_ok": True,
    }), encoding="utf-8")
    (truncated / "governance_receipt.json").write_text(json.dumps({
        "schema": "leo-sim-v2-governance-receipt/v2",
        "research_eligible": True, "verification_errors": [],
    }), encoding="utf-8")

    report = pullback_mod.verify_pulled_run(truncated, expect="formal")
    assert report["expect"] == "formal"
    assert report["ok"] is False
    assert "P4" in {c["id"] for c in report["checks"] if not c["ok"]}
    # The abbreviated witness cannot rescue it either: the governance receipt
    # has two of its twenty contract fields.
    assert "P5" in {c["id"] for c in report["checks"] if not c["ok"]}
    assert report["claimable"] is False
