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
    # name the clause that fires, so a change in which clause refuses is
    # noticed rather than silently accepted
    assert "no longer validates" in str(caught.value), str(caught.value)


def test_the_superseded_authorization_also_fails_the_code_binding():
    """The clause the test above does NOT reach.  If the artifact check were
    ever bypassed, the code identity binding still excludes this receipt."""
    payload = json.loads(STALE.read_text())
    bound = {row["code_sha256"] for row in payload["authorized_runs"]}
    assert bound == {"daf695aff5ab443002e8969a89b6ca6948d343ed5fde1be790f247e7e65849ff"}
    assert receipt.code_sha256() not in bound, (
        "the tracked receipt now binds the current code identity; it is no "
        "longer a superseded artifact and this guard must be re-derived")
