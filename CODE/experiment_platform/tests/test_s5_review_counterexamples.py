"""Third-round independent review (S5-R2): arm alignment must be a GATE.

targets_match/ranking_match were only written into the artifact.  Nothing
asserted them, so a benchmark whose four arms all queried the wrong instant
still passed its predicate, was marked ok, and had its p50 quoted as the real
online decision cost.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from CODE.experiment_platform import t1_suite

REPO_ROOT = Path(__file__).resolve().parents[3]
ARTIFACT = (REPO_ROOT / "out" / "vm" / "t1-final-8a31496" / "acceptance"
            / "cells" / "bench-decision" / "result.json")

#: mirrors the acceptance declaration; test_the_declaration_gates_alignment
#: below fails if that declaration ever loses the requirement
DECLARED = {"kind": "benchmark",
            "require": {"min_complete_rounds": 1, "pool_servers": [0, 1, 2],
                        "min_pool_requests": True, "require_queued_at": 1,
                        "require_alignment": True}}


def _artifact():
    if not ARTIFACT.exists():
        pytest.skip("pulled VM artifact is not present")
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_the_declaration_gates_alignment():
    text = (REPO_ROOT / "CODE" / "experiment_platform" / "t1_suite.py").read_text(
        encoding="utf-8")
    assert '"require_alignment": True' in text


def test_the_real_aligned_artifact_passes():
    assert t1_suite.check_predicate(_artifact(), DECLARED)["passed"] is True


def test_a_misaligned_artifact_cannot_pass():
    result = _artifact()
    for arm in result["arms"].values():
        arm["alignment"]["targets_match"] = False
        arm["alignment"]["ranking_match"] = False
    verdict = t1_suite.check_predicate(result, DECLARED)
    assert verdict["passed"] is False, (
        "a fully misaligned benchmark must not pass its own predicate")
    failed = [c["check"] for c in verdict["checks"] if not c["passed"]]
    assert len(failed) == 8, failed


def test_a_missing_alignment_block_fails_rather_than_defaulting_to_ok():
    result = _artifact()
    for arm in result["arms"].values():
        arm.pop("alignment", None)
    verdict = t1_suite.check_predicate(result, DECLARED)
    assert verdict["passed"] is False
