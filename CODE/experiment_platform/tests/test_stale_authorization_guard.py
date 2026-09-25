"""A tracked authorization that predates the current code must not be usable.

The frozen tree still carries another work package's authorization
(CODE/work/WP-LEO-V2-GLOBAL-PRESSURE-BRACKET/R01/authorization.json).  It was
not added by this work and it must not be silently reusable: the platform's own
verifier has to refuse it, and this test pins that refusal so the limitation is
guarded rather than hoped for (raised by the cold-start review of 2026-09-25).
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


def test_every_tracked_authorization_predates_the_current_code():
    """If one of them ever binds the current identity it was re-issued and
    this test should be revisited -- it is a deliberately narrow claim."""
    current = receipt.code_sha256()
    tracked = _tracked_authorizations()
    assert tracked, "expected at least the superseded authorization to be tracked"
    for path in tracked:
        bound = json.loads(path.read_text())["authorized_runs"][0]["code_sha256"]
        assert bound != current, (
            f"{path} binds the CURRENT code identity; re-check whether it is "
            "still a superseded artifact")


def test_the_superseded_authorization_is_refused_by_the_platform():
    if not STALE.is_file():
        pytest.skip("the superseded artifact is no longer in the tree")
    payload = json.loads(STALE.read_text())
    run_id = payload["authorized_runs"][0]["run_id"]
    with pytest.raises(authorize_experiment.AuthorizationError):
        authorize_experiment.verify_authorization_for_leo_sim_v2_config(
            ROOT, STALE, STALE, run_id)
