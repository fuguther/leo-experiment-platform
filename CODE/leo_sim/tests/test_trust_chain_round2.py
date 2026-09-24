"""Round-2 fixes from the three-role round-1 review.

Round 1 found (3/3 reviewers, independently) that the V5/V6 version family was
propagated only inside receipt.py, so a V6 receipt -- which the newly allowed
--decision-log produces -- was rejected by the governance witness writer and
by the V2 analysis gate.  It also found that the row contract was enforced
only on formal runs, letting a diagnostic run publish a V6 receipt asserting
decision-rows/v1 over a stream that provably violated it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/smoke.yaml"


def _run(*args):
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT,
                          capture_output=True, text=True)


def test_every_receipt_schema_consumer_accepts_the_family():
    """R1: no consumer outside receipt.py may hardcode a single version.

    Round 1 evidence: remote_job.build_v2_governance_receipt raised
    "leo_sim_v2 formal runs must produce receipt/v5", and
    v2_analysis._verify_result raised "receipt schema is not a supported
    formal branch", both on a genuine V6 receipt.
    """
    from CODE.leo_sim import receipt as receipt_mod

    family = receipt_mod.RECEIPT_SCHEMAS_V5_FAMILY
    assert receipt_mod.RECEIPT_SCHEMA in family
    assert receipt_mod.RECEIPT_SCHEMA_V6 in family

    v2a = (ROOT/"CODE/experiment_platform/v2_analysis.py").read_text()
    assert "receipt_schema in receipt_mod.RECEIPT_SCHEMAS_V5_FAMILY" in v2a
    assert '"receipt_schema": receipt_mod.RECEIPT_SCHEMA,' not in v2a, \
        "the witness binding must name the receipt own schema, not a constant"

    job = (ROOT/"CODE/scripts/remote/remote_job.py").read_text()
    assert '"leo-sim-receipt/v5"' not in job, \
        "remote_job must not hardcode one receipt version"


def test_the_governance_witness_binding_follows_the_receipt_schema():
    """R1: the witness names the schema of the receipt it witnesses."""
    v2a = (ROOT/"CODE/experiment_platform/v2_analysis.py").read_text()
    assert '"receipt_schema": receipt_schema,' in v2a


def test_a_diagnostic_run_still_validates_the_row_contract(tmp_path):
    """R2: a valid diagnostic run is unaffected by always-on validation."""
    out = tmp_path/"out"
    log = tmp_path/"d.jsonl"
    verdict = _run("CODE.leo_sim", "run", "--config", SMOKE,
                   "--out", str(out), "--decision-log", str(log))
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    assert log.is_file() and log.read_text().strip()


def test_the_decision_writer_is_always_given_the_row_contract():
    """R2: the validator is mounted unconditionally, with no formal branch."""
    src = (ROOT/"CODE/leo_sim/__main__.py").read_text()
    assert "validate=decision_ledger.validate_decision_row)" in src
    assert "if formal is not None else None" not in src, \
        "the row contract must not be conditional on run formality"


def test_the_empty_stream_refusal_is_not_formal_only():
    """R2: an empty stream is refused on EVERY run, not only formal ones."""
    src = (ROOT/"CODE/leo_sim/__main__.py").read_text()
    assert "if decision_writer is not None and decision_writer.row_count == 0:" in src
    assert "if formal is not None and decision_writer is not None" not in src


def test_the_decision_log_help_no_longer_claims_it_is_forbidden():
    """R4: the CLI help must describe the contract, not the removed refusal."""
    done = _run("CODE.leo_sim", "run", "--help")
    assert "forbidden for formal runs" not in done.stdout
    assert "DECISION_ROW_KEYS" in done.stdout
