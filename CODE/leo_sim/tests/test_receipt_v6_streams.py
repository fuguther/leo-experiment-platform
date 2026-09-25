"""V6 receipt: the decision stream joins the trust chain.

Covers the criteria frozen in
CODE/work/WP-LEO-V2-T1-TRUST-CHAIN-V6/criteria.json (C3-C5, C8, C9, C11) and
guards the failure that the first V6 attempt actually hit: receipt_schema is
dispatched at several sites, and a new version that is added to only one of
them falls through to the legacy branch.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/smoke.yaml"


def _run(*args):
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT,
                          capture_output=True, text=True)


def _verify(directory):
    done = _run("CODE.leo_sim", "receipt", "verify", str(directory))
    return json.loads(done.stdout)


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    """One plain run (V5) and one run publishing both streams (V6)."""
    out = tmp_path_factory.mktemp("v6")
    plain, both = out / "plain", out / "both"
    verdict = _run("CODE.leo_sim", "run", "--config", SMOKE, "--out", str(plain))
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    verdict = _run("CODE.leo_sim", "run", "--config", SMOKE, "--out", str(both),
                   "--decision-log", str(out / "d.jsonl"),
                   "--timeline-log", str(out / "t.jsonl"))
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    return {"plain": plain, "both": both, "dir": out}


def test_a_run_without_streams_keeps_the_v5_receipt(runs):
    """C1: the V5 shape is untouched when no streams are attached."""
    receipt = json.loads((runs["plain"] / "receipt.json").read_text())
    assert receipt["schema"] == "leo-sim-receipt/v5"
    assert len(receipt) == 28
    assert _verify(runs["plain"])["status"] == "verified"


def test_a_run_with_both_streams_publishes_a_v6_receipt(runs):
    receipt = json.loads((runs["both"] / "receipt.json").read_text())
    assert receipt["schema"] == "leo-sim-receipt/v6"
    assert len(receipt) == 31
    assert receipt["decision_stream_contract"] == "decision-rows/v1"


def test_the_v6_receipt_binds_the_streams_it_was_given(runs):
    """C8: the receipt's stream identities are the files' real hashes."""
    receipt = json.loads((runs["both"] / "receipt.json").read_text())
    for key, name in (("decision_log_sha256", "d.jsonl"),
                      ("timeline_log_sha256", "t.jsonl")):
        raw = (runs["dir"] / name).read_bytes()
        assert receipt[key] == hashlib.sha256(raw).hexdigest()


def test_the_v6_receipt_verifies(runs):
    """C9.  This is the regression guard for the dispatch fall-through: a V6
    receipt used to reach the legacy identity/v1 branch and fail."""
    verdict = _verify(runs["both"])
    assert verdict["status"] == "verified", verdict.get("errors")


def test_every_schema_version_belongs_to_exactly_one_family():
    """Guard: adding a schema constant must not leave a dispatch site behind.

    The first V6 attempt updated the key-set branch but not the manifest /
    emission / identity branches, so every V6 receipt failed verification.
    Adding a version to RECEIPT_SCHEMAS_V5_FAMILY (or deliberately excluding
    it) is now a visible decision rather than an omission.
    """
    from CODE.leo_sim import receipt as receipt_mod

    known = {receipt_mod.LEGACY_RECEIPT_SCHEMA,
             receipt_mod.LEGACY_RECEIPT_SCHEMA_V4,
             receipt_mod.RECEIPT_SCHEMA,
             receipt_mod.RECEIPT_SCHEMA_V6}
    family = receipt_mod.RECEIPT_SCHEMAS_V5_FAMILY
    assert family <= known
    assert receipt_mod.RECEIPT_SCHEMA in family
    assert receipt_mod.RECEIPT_SCHEMA_V6 in family
    assert not family & {receipt_mod.LEGACY_RECEIPT_SCHEMA,
                         receipt_mod.LEGACY_RECEIPT_SCHEMA_V4}


def test_the_ledger_key_set_is_unchanged():
    """C11 (protocol 3.4: no ledger top-level key may move)."""
    from CODE.leo_sim import receipt as receipt_mod
    assert len(receipt_mod.LEDGER_KEYS) == 17


def test_the_decision_row_contract_matches_what_a_run_writes(runs):
    """C3: the contract is the writer's key set, not a wish."""
    from CODE.leo_sim import decision_ledger
    rows = [json.loads(line)
            for line in (runs["dir"] / "d.jsonl").read_text().splitlines()]
    assert rows
    for row in rows:
        decision_ledger.validate_decision_row(row)
    assert set(rows[0]) == set(decision_ledger.DECISION_ROW_KEYS)
    assert len(decision_ledger.DECISION_ROW_KEYS) == 19


def test_the_contract_refuses_an_unknown_key():
    """C4."""
    from CODE.leo_sim import decision_ledger
    row = {key: None for key in decision_ledger.DECISION_ROW_KEYS}
    decision_ledger.validate_decision_row(row)
    with pytest.raises(decision_ledger.DecisionRowError, match="rogue"):
        decision_ledger.validate_decision_row({**row, "rogue": 1})


def test_the_contract_refuses_a_missing_key():
    """C5."""
    from CODE.leo_sim import decision_ledger
    row = {key: None for key in decision_ledger.DECISION_ROW_KEYS}
    row.pop("chosen")
    with pytest.raises(decision_ledger.DecisionRowError, match="chosen"):
        decision_ledger.validate_decision_row(row)


def test_the_contract_refuses_a_non_object_row():
    from CODE.leo_sim import decision_ledger
    with pytest.raises(decision_ledger.DecisionRowError):
        decision_ledger.validate_decision_row(["not", "a", "dict"])
