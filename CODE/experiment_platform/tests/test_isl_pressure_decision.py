"""Tests for the preregistered R03 pair classifier."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from CODE.experiment_platform import isl_pressure
from CODE.experiment_platform import isl_pressure_decision


def _run(arm: str, *, pressure: bool = False) -> dict:
    return {
        "arm_id": arm,
        "evidence_class": "v2_external_witness",
        "diagnostics": {
            "mcs": {"zero_rate_holds": 0},
            "access": {"grants": 100},
            "fate_counts": {},
            "control": {
                "expired": 0, "lost": 0, "geometry_lost": 0,
                "overflow": 0,
            },
            "drain": {
                "in_system_at_stop_packets": 0,
                "unmatched_isl_queue_entries": 0,
            },
            "windowed_isl": {
                "pressure_candidate_link_ids": (["isl:1:2"]
                                                 if pressure else []),
            },
        },
    }


def _manifest(control: dict, candidate: dict) -> dict:
    return {
        "schema": "leo-sim-v2-analysis/v1",
        "status": "VERIFIED",
        "run_results": [control, candidate],
    }


@pytest.mark.parametrize(
    ("control_pressure", "candidate_pressure", "expected"),
    [
        (False, False, "NO_PRESSURE_PHYS_VALID"),
        (False, True, "PRESSURE_CANDIDATE"),
        (True, False, "CONTROL_PRESSURE_UNBRACKETED"),
        (True, True, "CONTROL_PRESSURE_UNBRACKETED"),
    ],
)
def test_classifies_pressure_only_after_testing_control(
        control_pressure, candidate_pressure, expected):
    got = isl_pressure_decision.classify_verified_pair(
        _manifest(_run("b5", pressure=control_pressure),
                  _run("b2", pressure=candidate_pressure)),
        control_arm="b5", candidate_arm="b2")

    assert got["classification"] == expected


def test_physical_failure_precedes_pressure_signal():
    control = _run("b5")
    candidate = _run("b2", pressure=True)
    candidate["diagnostics"]["fate_counts"]["NO_ROUTE"] = 1

    got = isl_pressure_decision.classify_verified_pair(
        _manifest(control, candidate), control_arm="b5", candidate_arm="b2")

    assert got["classification"] == "PHYS_INVALID"
    assert any("NO_ROUTE" in item for item in got["reasons"])


def test_drain_failure_precedes_pressure_signal():
    control = _run("b5")
    candidate = _run("b2", pressure=True)
    candidate["diagnostics"]["drain"]["in_system_at_stop_packets"] = 1

    got = isl_pressure_decision.classify_verified_pair(
        _manifest(control, candidate), control_arm="b5", candidate_arm="b2")

    assert got["classification"] == "DRAIN_INCOMPLETE"


def test_isl_overflow_supplements_but_does_not_replace_localized_pressure():
    control = _run("b5")
    candidate = _run("b2", pressure=True)
    candidate["diagnostics"]["fate_counts"]["ISL_QUEUE_OVERFLOW"] = 2

    got = isl_pressure_decision.classify_verified_pair(
        _manifest(control, candidate), control_arm="b5", candidate_arm="b2")

    assert got["classification"] == "PRESSURE_CANDIDATE"
    assert got["candidate_isl_queue_overflows"] == 2

    candidate = _run("b2")
    candidate["diagnostics"]["fate_counts"]["ISL_QUEUE_OVERFLOW"] = 1
    with pytest.raises(isl_pressure_decision.PressureDecisionError,
                       match="no localized sustained pressure episode"):
        isl_pressure_decision.classify_verified_pair(
            _manifest(control, candidate), control_arm="b5",
            candidate_arm="b2")


def test_control_pressure_precedes_candidate_unlocalized_isl_overflow():
    control = _run("b5", pressure=True)
    candidate = _run("b2")
    candidate["diagnostics"]["fate_counts"]["ISL_QUEUE_OVERFLOW"] = 3

    got = isl_pressure_decision.classify_verified_pair(
        _manifest(control, candidate), control_arm="b5", candidate_arm="b2")

    assert got["classification"] == "CONTROL_PRESSURE_UNBRACKETED"
    assert got["candidate_isl_queue_overflows"] == 3


def test_holding_overflow_remains_physical_invalid():
    candidate = _run("b2", pressure=True)
    candidate["diagnostics"]["fate_counts"]["HOLDING_QUEUE_OVERFLOW"] = 1

    got = isl_pressure_decision.classify_verified_pair(
        _manifest(_run("b5"), candidate), control_arm="b5",
        candidate_arm="b2")

    assert got["classification"] == "PHYS_INVALID"


def test_rejects_unverified_or_ambiguous_cohort():
    manifest = _manifest(_run("b5"), _run("b2"))
    manifest["status"] = "PARTIAL"
    with pytest.raises(isl_pressure_decision.PressureDecisionError,
                       match="VERIFIED"):
        isl_pressure_decision.classify_verified_pair(
            manifest, control_arm="b5", candidate_arm="b2")


def test_rejects_missing_control_failure_counter():
    control = _run("b5")
    candidate = _run("b2")
    del candidate["diagnostics"]["control"]["geometry_lost"]

    with pytest.raises(isl_pressure_decision.PressureDecisionError,
                       match="lacks required failure counters"):
        isl_pressure_decision.classify_verified_pair(
            _manifest(control, candidate), control_arm="b5",
            candidate_arm="b2")


def test_frozen_contract_matches_analyzer_and_classifier_constants():
    root = Path(__file__).resolve().parents[3]
    contract = json.loads((
        root / "CODE/work/WP-LEO-V2-ISL-BANDWIDTH-PILOT/R03/"
        "pressure-decision.json").read_text(encoding="utf-8"))

    window = contract["window_contract"]
    queue = contract["same_link_queue_contract"]
    physical = contract["physical_validity"]
    assert window["window_s"] == isl_pressure.DEFAULT_WINDOW_S
    assert window["high_utilization_threshold"] == \
        isl_pressure.DEFAULT_HIGH_UTILIZATION
    assert window["min_available_fraction_per_counted_window"] == \
        isl_pressure.DEFAULT_MIN_AVAILABLE_FRACTION
    assert window["min_consecutive_high_windows"] == \
        isl_pressure.DEFAULT_MIN_CONSECUTIVE_HIGH_WINDOWS
    assert queue["min_max_overlapping_matched_queue_wait_s"] == \
        isl_pressure.DEFAULT_MIN_EPISODE_QUEUE_WAIT_S
    assert queue["min_episode_matched_queue_wait_bits_s"] == \
        isl_pressure.DEFAULT_MIN_EPISODE_QUEUE_AREA_BITS_S
    assert tuple(physical[
        "candidate_noncongestion_fates_may_exceed_control_by"]) == \
        isl_pressure_decision.NONCONGESTION_FATES
    assert tuple(physical[
        "invalid_non_isl_overflow_fates_must_equal_zero_per_arm"]) == \
        isl_pressure_decision.INVALID_OVERFLOW_FATES
    assert contract["isl_queue_overflow_handling"] == {
        "control_pressure_branch_precedes_candidate_unlocalized_overflow":
        True,
        "qualified_pressure_episode_required": True,
        "role": "supplementary_congestion_outcome_not_physical_failure",
        "unlocalized_overflow_blocks_classification": True,
    }
    assert tuple(physical["candidate_control_failure_fields"]) == \
        isl_pressure_decision.CONTROL_FAILURES
    assert physical["required_control_failure_fields_must_be_present"] is True
    assert contract["canonical_invocation"][-8:] == [
        "--manifest",
        "ANALYSIS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/v2-paired/analysis-manifest.json",
        "--control-arm", "b5", "--candidate-arm", "b2", "--out",
        "ANALYSIS/EXP-20260824-ISL-BANDWIDTH-PILOT-R03/pressure-classification.json",
    ]
    assert [item["class"] for item in contract["ordered_classification"]] == [
        "PHYS_INVALID", "DRAIN_INCOMPLETE", "CONTROL_PRESSURE_UNBRACKETED",
        "PRESSURE_CANDIDATE", "NO_PRESSURE_PHYS_VALID",
    ]
    runbook = (root / "EXPERIMENTS/"
               "EXP-20260824-ISL-BANDWIDTH-PILOT-R03/RUNBOOK.md").read_text(
                   encoding="utf-8")
    for token in contract["canonical_invocation"]:
        assert token in runbook


def test_persisted_classifier_verifies_and_binds_manifest(tmp_path, monkeypatch):
    manifest = _manifest(_run("b5"), _run("b2", pressure=True))
    path = tmp_path / "analysis-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    from CODE.experiment_platform import v2_analysis
    monkeypatch.setattr(v2_analysis, "verify_persisted_analysis",
                        lambda root, manifest_path: (True, []))

    got = isl_pressure_decision.classify_persisted_pair(
        tmp_path, path, control_arm="b5", candidate_arm="b2")

    assert got["classification"] == "PRESSURE_CANDIDATE"
    assert got["analysis_manifest"] == "analysis-manifest.json"
    assert len(got["analysis_manifest_sha256"]) == 64

    out = tmp_path / "analysis" / "pressure-classification.json"
    assert isl_pressure_decision.main([
        "--root", str(tmp_path), "--manifest", str(path),
        "--control-arm", "b5", "--candidate-arm", "b2",
        "--out", str(out),
    ]) == 0
    persisted = json.loads(out.read_text(encoding="utf-8"))
    assert persisted == got

    manifest = _manifest(_run("b5"), _run("b2"))
    manifest["run_results"].append(_run("b2"))
    with pytest.raises(isl_pressure_decision.PressureDecisionError,
                       match="exactly one"):
        isl_pressure_decision.classify_verified_pair(
            manifest, control_arm="b5", candidate_arm="b2")


def test_persisted_classifier_fails_when_analysis_verification_fails(
        tmp_path, monkeypatch):
    path = tmp_path / "analysis-manifest.json"
    path.write_text(json.dumps(_manifest(_run("b5"), _run("b2"))),
                    encoding="utf-8")
    from CODE.experiment_platform import v2_analysis
    monkeypatch.setattr(
        v2_analysis, "verify_persisted_analysis",
        lambda root, manifest_path: (False, ["analyzer identity mismatch"]))

    with pytest.raises(isl_pressure_decision.PressureDecisionError,
                       match="analyzer identity mismatch"):
        isl_pressure_decision.classify_persisted_pair(
            tmp_path, path, control_arm="b5", candidate_arm="b2")
