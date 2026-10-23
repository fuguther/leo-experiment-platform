"""Third-round independent review (S5): the timing must declare its model."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from CODE.experiment_platform import t1_suite

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH = REPO_ROOT / "CODE" / "experiment_platform" / "benchmark_decision.py"

PREDICATE = {"kind": "benchmark",
             "require": {"require_model_provenance": True}}


def test_the_benchmark_module_declares_model_provenance():
    text = BENCH.read_text(encoding="utf-8")
    assert '"model_provenance"' in text
    assert '"trained_checkpoint_used": False' in text
    assert '"ddqn": False' in text
    assert "NOT a DDQN latency" in text


def test_the_acceptance_declaration_requires_it():
    text = (REPO_ROOT / "CODE" / "experiment_platform" / "t1_suite.py").read_text(
        encoding="utf-8")
    assert '"require_model_provenance": True' in text


def test_a_provenance_free_artifact_cannot_pass():
    verdict = t1_suite.check_predicate({}, PREDICATE)
    assert verdict["passed"] is False
    assert [c["check"] for c in verdict["checks"] if not c["passed"]]


def test_a_ddqn_claim_cannot_pass():
    verdict = t1_suite.check_predicate(
        {"model_provenance": {"trained_checkpoint_used": True, "ddqn": True}},
        PREDICATE)
    assert verdict["passed"] is False


def test_a_deterministic_scorer_artifact_passes():
    verdict = t1_suite.check_predicate(
        {"model_provenance": {"scorer": "deterministic_shared_scorer",
                              "trained_checkpoint_used": False, "ddqn": False}},
        PREDICATE)
    assert verdict["passed"] is True, verdict
