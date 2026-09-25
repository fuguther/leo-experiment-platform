"""A tracked authorization that predates the current code must not be usable.

The frozen tree still carries another work package's authorization
(CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R01/authorization.json).  It was
not added by this work and it must not be silently reusable.

An independent review of 2026-09-25 sharpened this test: the refusal that
actually fires first comes from the ARTIFACT-HASH clause of verify_authorization
(the runbook of that experiment is no longer byte-identical), which is reached
before the code_sha256 / execution_chain binding.  So the test pins two
different things, and says which is which:

  * the artifact is refused today, by a named clause;
  * the artifact is ALSO superseded (its bound code_sha256 differs from the
    current one), so it would be excluded by the binding clause even if the
    artifact check were passed.

It deliberately does NOT skip when the artifact disappears: hiding the file
must break the test, not disarm it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from CODE.experiment_platform import authorize_experiment
from CODE.leo_sim import receipt

ROOT = Path(__file__).resolve().parents[3]
STALE = (ROOT / "CODE" / "work" / "WP-LEO-V2-GLOBAL-PRESSURE-BRACKET" / "R01"
         / "authorization.json")


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


def test_the_superseded_authorization_is_refused_by_the_platform():
    payload = json.loads(STALE.read_text())
    run_id = payload["authorized_runs"][0]["run_id"]
    with pytest.raises(authorize_experiment.AuthorizationError) as caught:
        authorize_experiment.verify_authorization_for_leo_sim_v2_config(
            ROOT, STALE, STALE, run_id)
    # Name the CLAUSE that fires, not the generic wrapper: an independent
    # review of 2026-09-25 showed "no longer validates" alone would also hold
    # for any other refusal raised through the same wrapper, so it did not
    # discriminate what this test claims to pin.
    assert "artifact hash mismatch" in str(caught.value), str(caught.value)


def test_the_superseded_authorization_also_fails_the_code_binding():
    """The clause the test above does NOT reach.  If the artifact check were
    ever bypassed, the code identity binding still excludes this receipt."""
    payload = json.loads(STALE.read_text())
    bound = {row["code_sha256"] for row in payload["authorized_runs"]}
    assert bound == {"daf695aff5ab443002e8969a89b6ca6948d343ed5fde1be790f247e7e65849ff"}
    assert receipt.code_sha256() not in bound, (
        "the tracked receipt now binds the current code identity; it is no "
        "longer a superseded artifact and this guard must be re-derived")
