"""A tracked authorization that predates the current code must not be usable.

The frozen tree still carries another work package's authorization
(CODE/experiment_platform/tests/fixtures/stale_authorization.json).  It was
not added by this work and it must not be silently reusable.

The historical finalization graph is intentionally absent from the active
tree.  Current verification therefore fails closed before it can reach an
artifact-hash or code-binding clause.  This module tests that fail-closed
behaviour and separately asserts that the retained authorization binds a
different code identity.

End-to-end artifact-hash and code-binding rejection are exercised against
self-contained temporary authorization graphs in
test_authorize_experiment.py.  Keeping those tests separate prevents this
historical fixture from claiming a clause it cannot reach.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from CODE.experiment_platform import authorize_experiment
from CODE.leo_sim import receipt

ROOT = Path(__file__).resolve().parents[3]
STALE = (ROOT / "CODE" / "experiment_platform" / "tests" / "fixtures"
         / "stale_authorization.json")


def _tracked_authorizations() -> list[Path]:
    import subprocess
    out = subprocess.run(["git", "ls-files", "*authorization.json"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / line for line in out.split() if line.strip()]


def test_no_tracked_authorization_binds_the_current_code_identity():
    """The invariant, not one hardcoded path: every authorization tracked in
    this tree is a superseded artifact of some earlier code identity.  The
    first version of this guard narrowed to a single path (review weakness w2).

    If a genuine re-issued authorization ever lands here it will bind the
    current identity and this test should be revisited deliberately.
    """
    current = receipt.code_sha256()
    tracked = _tracked_authorizations()
    assert tracked, "expected at least one tracked authorization"
    for path in tracked:
        bound = {row["code_sha256"]
                 for row in json.loads(path.read_text())["authorized_runs"]}
        assert current not in bound, (
            f"{path} binds the CURRENT code identity {current}; re-check "
            "whether it is still a superseded artifact")


def test_the_superseded_authorization_is_still_in_the_tree():
    """Not a skip: the guard below is only meaningful while the hazard exists,
    and a missing file must be a visible failure rather than a silent pass."""
    assert STALE.is_file(), (
        f"{STALE} is gone; if that was deliberate, delete this test with it "
        "instead of letting the guard skip")


def test_the_superseded_authorization_is_refused_without_historical_finalization():
    payload = json.loads(STALE.read_text())
    run_id = payload["authorized_runs"][0]["run_id"]
    with pytest.raises(authorize_experiment.AuthorizationError) as caught:
        authorize_experiment.verify_authorization_for_leo_sim_v2_config(
            ROOT, STALE, STALE, run_id)
    # The historical finalization file is intentionally not retained in the
    # active platform tree.  Absence must refuse the receipt, never disarm it.
    assert "finalization file is missing" in str(caught.value), str(caught.value)


def test_the_superseded_authorization_binds_a_different_code_identity():
    """The retained fixture is a real stale-code witness, not a verifier run.

    A complete temporary graph reaches the public code-binding verifier below;
    this historical fixture only preserves that its frozen code identity is
    no longer current.
    """
    payload = json.loads(STALE.read_text())
    bound = {row["code_sha256"] for row in payload["authorized_runs"]}
    assert bound == {"daf695aff5ab443002e8969a89b6ca6948d343ed5fde1be790f247e7e65849ff"}
    assert receipt.code_sha256() not in bound, (
        "the tracked receipt now binds the current code identity; it is no "
        "longer a superseded artifact and this guard must be re-derived")
