"""The pull-back gate may not grant what it cannot establish.

Independent review, 2026-09-25: a hand-written directory whose formal witness
and governance receipt held six fields between them returned
ok=true, claimable=true, errors=[].  Passing a file-integrity check had become
a formal-eligibility verdict.

The gate now:

  * requires the caller to DECLARE whether the copy is formal or diagnostic, so
    a witness lost in transit cannot be re-labelled a diagnostic result;
  * enforces the full contract key sets of both formal artifacts;
  * NEVER reports claimable=true -- formal analysis eligibility stays with
    CODE/experiment_platform/v2_analysis.py, over evidence this gate cannot see;
  * checks the external launch witness only when the caller says where it is,
    and says so in the report when it did not.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from CODE.leo_sim import pullback as pullback_mod
from CODE.leo_sim import receipt as receipt_mod

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "CODE" / "scripts" / "remote"))
from CODE.scripts.remote import remote_job as rj  # noqa: E402

RUN_ID = "EXP-PULLBACK-AUTHORITY-s7"


def _diagnostic_run(root: Path) -> Path:
    """One real run of the official CLI (no formal identity flags)."""
    from CODE.leo_sim import config as config_mod
    resolved = config_mod.resolve_config({
        "scenario": {"duration_s": 40.0, "num_satellites": 8, "num_planes": 1,
                     "seed": 7},
        "endpoints": {"sites": [{"name": "a", "lat": 0.1, "lon": 0.1},
                                {"name": "b", "lat": 52.6, "lon": 90.4}]},
        "demand": {"mode": "csv",
                   "csv_path": "CODE/data/traffic/t1_step5_micro_ab.csv"},
        "control_plane": {"enabled": False},
        "execution": {"available_capacity_interval_s": 0.1},
        "routing": {"policy": "oracle"},
        "learning": {"algorithm": "none"},
    })
    config_path = root / "run.yaml"
    config_path.write_text(json.dumps(
        {"config_version": resolved["version"], **resolved["config"]},
        indent=2, sort_keys=True), encoding="utf-8")
    out_dir = root / RUN_ID
    out_dir.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "CODE.leo_sim", "run", "--config",
         str(config_path), "--out", str(out_dir),
         "--decision-log", str(out_dir / "decisions.jsonl"),
         "--timeline-log", str(out_dir / "timeline.jsonl")],
        cwd=str(REPO), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return out_dir


@pytest.fixture(scope="module")
def run_dir(tmp_path_factory):
    return _diagnostic_run(tmp_path_factory.mktemp("pullback"))


def _copy(run_dir: Path, tmp_path: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copytree(run_dir, target)
    return target


def _sha(path: Path) -> str:
    return receipt_mod.hashlib.sha256(path.read_bytes()).hexdigest()


def _write_contract_shaped_formal(directory: Path, *, nonce: str = "a" * 32,
                                  witness_root: Path | None = None) -> None:
    """A formal increment that satisfies every shape and binding rule."""
    receipt = json.loads((directory / "receipt.json").read_text())
    formal = {
        "schema": "leo-sim-formal-run/v1",
        "run_id": directory.name,
        "launch_nonce": nonce,
        "authorization_sha256": "b" * 64,
        "config_sha256": "c" * 64,
        "code_sha256": "d" * 64,
        "receipt_sha256": _sha(directory / "receipt.json"),
        "natural_end": True,
        "conservation_ok": True,
    }
    (directory / "formal_run.json").write_text(json.dumps(formal),
                                               encoding="utf-8")
    governed = {
        "schema": pullback_mod.GOVERNANCE_SCHEMA_V2,
        "research_eligible": True,
        "run_id": directory.name,
        "launch_nonce": nonce,
        "authorization_sha256": formal["authorization_sha256"],
        "source_git_commit": "e" * 40,
        "source_tree_sha256": "f" * 64,
        "deployment_receipt_sha256": "1" * 64,
        "execution_chain_sha256": {"CODE/leo_sim/receipt.py": "2" * 64},
        "acceptance": {"min_delivered_packets": 0},
        "run_receipt_sha256": _sha(directory / "receipt.json"),
        "natural_end": True,
        "conservation_ok": True,
        "verification_errors": [],
        "receipt_schema": receipt["schema"],
        "resolved_config_sha256": _sha(directory / "resolved_config.json"),
        "trace_manifest_schema": json.loads(
            (directory / "manifest.json").read_text())["schema"],
        "trace_identity_contract": receipt["trace_identity_contract"],
        "trace_manifest_sha256": _sha(directory / "manifest.json"),
    }
    governed["payload_sha256"] = pullback_mod._formal_contract().canonical_sha(
        governed)
    (directory / "governance_receipt.json").write_text(
        json.dumps(governed), encoding="utf-8")
    if witness_root is not None:
        witness_root.mkdir(parents=True, exist_ok=True)
        (witness_root / f"{nonce}.json").write_text(json.dumps({
            "schema": pullback_mod._formal_contract().EXTERNAL_STATUS_SCHEMA,
            "status": "success", "exit_code": 0, "launch_nonce": nonce,
            "run_id": directory.name,
            "governance_receipt_sha256": _sha(
                directory / "governance_receipt.json"),
            "governance_witness": {
                key: governed[key]
                for key in pullback_mod._formal_contract().GOVERNANCE_WITNESS_FIELDS},
        }), encoding="utf-8")


def test_the_caller_must_declare_what_the_copy_is(run_dir):
    with pytest.raises(TypeError):
        pullback_mod.verify_pulled_run(run_dir)
    with pytest.raises(pullback_mod.PullbackError, match="expect must be one of"):
        pullback_mod.verify_pulled_run(run_dir, expect="looks-fine-to-me")


def test_a_simplified_witness_never_grants_claimable(run_dir, tmp_path):
    """The review's exact probe: six fields across two files."""
    directory = _copy(run_dir, tmp_path, "simplified")
    (directory / "formal_run.json").write_text(json.dumps(
        {"receipt_sha256": _sha(directory / "receipt.json")}), encoding="utf-8")
    (directory / "governance_receipt.json").write_text(json.dumps({
        "research_eligible": True, "verification_errors": [],
        "run_receipt_sha256": _sha(directory / "receipt.json"),
        "resolved_config_sha256": _sha(directory / "resolved_config.json"),
        "trace_manifest_sha256": _sha(directory / "manifest.json"),
    }), encoding="utf-8")

    report = pullback_mod.verify_pulled_run(directory, expect="formal")
    assert report["ok"] is False
    assert report["claimable"] is False
    assert report["formal_eligibility"]["status"] == "NOT_EVALUATED"
    assert [c["id"] for c in report["checks"] if not c["ok"]] == ["P5"]
    assert any("formal_run.json is missing contract fields" in e
               for e in report["errors"])
    assert any("governance_receipt.json is missing contract fields" in e
               for e in report["errors"])
    assert any("payload_sha256" in e for e in report["errors"])


def test_a_lost_formal_witness_is_not_accepted_as_diagnostic(run_dir, tmp_path):
    """Absence must fail, in both declarations -- never auto-downgrade."""
    lost = _copy(run_dir, tmp_path, "lost-witness")
    _write_contract_shaped_formal(lost)
    (lost / "formal_run.json").unlink()

    as_formal = pullback_mod.verify_pulled_run(lost, expect="formal")
    assert as_formal["ok"] is False
    assert [c["id"] for c in as_formal["checks"] if not c["ok"]] == ["P5"]
    assert any("formal_run.json is missing or symbolic" in e
               for e in as_formal["errors"])

    # The governance receipt still marks the copy as formal.  Declaring it
    # diagnostic must fail rather than quietly drop the formal checks.
    as_diagnostic = pullback_mod.verify_pulled_run(lost, expect="diagnostic")
    assert as_diagnostic["ok"] is False
    assert as_diagnostic["formal_artifacts_present"] is True
    assert any("must not be accepted as a diagnostic result" in e
               for e in as_diagnostic["errors"])

    # …and a copy with neither artifact is a legitimate diagnostic directory
    bare = _copy(run_dir, tmp_path, "bare")
    assert pullback_mod.verify_pulled_run(bare, expect="diagnostic")["ok"] is True


def test_a_contract_shaped_formal_copy_still_never_claims_eligibility(
        run_dir, tmp_path):
    directory = _copy(run_dir, tmp_path, "full-formal")
    witness_root = tmp_path / "_external_launch_witness"
    _write_contract_shaped_formal(directory, witness_root=witness_root)

    report = pullback_mod.verify_pulled_run(
        directory, expect="formal", witness_root=witness_root)
    assert report["ok"] is True, report["errors"]
    # Every shape and binding rule holds, and the verdict is STILL not
    # eligibility: that needs the authorization cohort, the deployment receipt
    # and the run-time identity chain.
    assert report["claimable"] is False
    assert report["formal_eligibility"]["status"] == "NOT_EVALUATED"
    assert report["formal_eligibility"]["authority"].endswith("v2_analysis.py")
    not_checked = " ".join(report["formal_eligibility"]["not_checked_here"])
    assert "authorization cohort" in not_checked
    assert "deployment identity" in not_checked
    assert "run-time identity" in not_checked


def test_the_external_launch_witness_is_checked_only_when_declared(
        run_dir, tmp_path):
    directory = _copy(run_dir, tmp_path, "witness")
    witness_root = tmp_path / "_external_launch_witness"
    _write_contract_shaped_formal(directory, witness_root=witness_root)

    undeclared = pullback_mod.verify_pulled_run(directory, expect="formal")
    assert undeclared["ok"] is True
    assert any("external launch witness" in item
               for item in undeclared["formal_eligibility"]["not_checked_here"])

    witness = next(witness_root.iterdir())
    payload = json.loads(witness.read_text())
    payload["status"] = "failed"
    witness.write_text(json.dumps(payload), encoding="utf-8")
    declared = pullback_mod.verify_pulled_run(
        directory, expect="formal", witness_root=witness_root)
    assert declared["ok"] is False
    assert any("not a successful launch" in e for e in declared["errors"])


def test_the_governance_schema_constant_matches_both_producers():
    """The schema STRING is part of the contract too, not just the key set.

    The first version of pullback.py spelled it leo-sim-v2-governance-receipt/v2
    while both producers spell it leo-sim-governance-receipt/v2.  Every local
    fixture used the module's own constant, so all of them agreed with the typo
    and only a real VM result exposed it (2026-09-25).
    """
    from CODE.experiment_platform import v2_analysis
    assert pullback_mod.GOVERNANCE_SCHEMA_V2 == rj.V2_GOVERNANCE_SCHEMA
    assert pullback_mod.GOVERNANCE_SCHEMA_V2 == v2_analysis.GOVERNANCE_SCHEMA_V2
    assert pullback_mod._formal_contract().EXTERNAL_STATUS_SCHEMA \
        == "leo-remote-launch-status/v2"


def test_the_external_launch_witness_is_found_by_run_id_or_nonce(
        run_dir, tmp_path):
    """pull-results-remote.sh names the witness after the RUN ID.

    The first version looked only for <launch_nonce>.json and therefore
    rejected every genuinely pulled result (2026-09-25).
    """
    nonce = "c" * 32
    for index, (name, expected) in enumerate(
            ((f"{RUN_ID}.json", True), (f"{nonce}.json", True),
             ("neither.json", False))):
        base = tmp_path / f"case-{index}"
        base.mkdir()
        directory = _copy(run_dir, base, RUN_ID)
        witness_root = base / "_external_launch_witness"
        _write_contract_shaped_formal(directory, nonce=nonce,
                                      witness_root=witness_root)
        # rename the witness to the spelling under test
        written = next(witness_root.iterdir())
        written.rename(witness_root / name)
        report = pullback_mod.verify_pulled_run(
            directory, expect="formal", witness_root=witness_root)
        assert report["ok"] is expected, (name, report["errors"])


def test_the_contract_key_sets_match_their_producers(run_dir, tmp_path):
    """The constants are pinned against the code that writes the files.

    A key set that drifts from its producer would either reject every real
    formal copy or accept a simplified one; this is the mechanical link.
    """
    directory = _copy(run_dir, tmp_path, "producers")
    ledgers = json.loads((directory / "ledgers.json").read_text())
    receipt = json.loads((directory / "receipt.json").read_text())
    governed = rj.build_v2_governance_receipt(
        receipt=receipt, ledgers=ledgers, verification_errors=[],
        acceptance={"min_delivered_packets": 0}, run_id=directory.name,
        launch_nonce="a" * 32, authorization_sha256="b" * 64,
        deployment={"source_git_commit": "e" * 40,
                    "source_tree_sha256": "f" * 64},
        deployment_receipt_sha256="1" * 64,
        execution_chain_sha256={"CODE/leo_sim/receipt.py": "2" * 64},
        receipt_path=directory / "receipt.json",
        resolved_config_path=directory / "resolved_config.json",
        manifest_path=directory / "manifest.json")
    assert set(governed) == set(pullback_mod.GOVERNANCE_KEYS_V2)

    from CODE.leo_sim import __main__ as cli_mod
    cli_mod._write_formal_witness(
        str(directory),
        {"run_id": directory.name, "launch_nonce": "a" * 32,
         "authorization_sha256": "b" * 64, "config_sha256": "c" * 64,
         "code_sha256": receipt_mod.code_sha256(),
         "results_dir": str(directory.parent)},
        receipt)
    written = json.loads((directory / "formal_run.json").read_text())
    assert set(written) <= (set(pullback_mod.FORMAL_WITNESS_KEYS)
                            | set(pullback_mod.FORMAL_WITNESS_OPTIONAL_KEYS))
    assert set(pullback_mod.FORMAL_WITNESS_KEYS) <= set(written)
