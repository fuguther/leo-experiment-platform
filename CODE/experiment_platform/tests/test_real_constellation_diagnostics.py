"""Real-constellation diagnostics: the no_info cause and the dual-reachable scan.

Both tools are diagnostic: they read the two streams a normal run already
writes and never feed anything back into a policy.  The tests pin the two
properties that make their output usable -- the cause vocabulary is a closed
set derived from the observation alone, and the scan reports its failures
rather than only its successes.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml"
SCAN = "CODE/leo_sim/profiles/t1_real_dual_scan.yaml"
CAUSES = {"RANGE", "NOT_ARRIVED", "EXPIRED", "ABSENT",
          "COVERING_ENTRY_PRESENT"}


def _run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", *args], cwd=cwd,
                          capture_output=True, text=True)


def _diagnose(out, config=SMOKE, forced=None):
    args = ["CODE.experiment_platform.no_info_diagnosis", "--config", config,
            "--root", str(ROOT), "--out", str(out)]
    if forced:
        args += ["--forced-decision-id", str(forced[0]),
                 "--forced-action", forced[1]]
    done = _run(*args)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(out.read_text())


def test_the_cause_is_classified_from_the_observation_alone(tmp_path):
    doc = _diagnose(tmp_path / "d.json")
    assert doc["schema"] == "no-info-diagnosis/v1"
    assert doc["control_plane"]["vis_k"] >= 1
    for attempt in doc["attempts"]:
        assert attempt["cause"] in CAUSES
        assert "cache_entries" in attempt
        assert attempt["packet_dst_cell"], "the packet's own destination"
        for entry in attempt["cache_entries"]:
            assert set(entry) >= {"origin", "hops", "age_s",
                                  "advertised_serve_cells",
                                  "covers_destination"}


def test_the_real_constellation_no_info_is_a_reach_limit(tmp_path):
    """The documented cause of the original case.  If a future change makes
    this NOT_ARRIVED or EXPIRED the assertion fires, because the remedy would
    be different."""
    doc = _diagnose(tmp_path / "d.json", forced=(2, "S"))
    causes = doc["totals"]["no_info_by_cause"]
    assert causes, "the forced branch is expected to hold"
    assert set(causes) == {"RANGE"}, causes
    assert doc["satellites_never_covered"], (
        "a RANGE verdict must name the satellite that never received a "
        "covering advertisement")


def test_forcing_is_labelled_in_the_artifact(tmp_path):
    plain = _diagnose(tmp_path / "plain.json")
    forced = _diagnose(tmp_path / "forced.json", forced=(2, "S"))
    assert plain["forced"] is None
    assert forced["forced"]["decision_id"] == 2
    assert forced["forced"]["action"] == "S"
    assert "FORCED branch" in forced["forced"]["meaning"]


def test_forcing_needs_both_arguments(tmp_path):
    done = _run("CODE.experiment_platform.no_info_diagnosis",
                "--config", SMOKE, "--forced-decision-id", "2",
                "--out", str(tmp_path / "d.json"))
    assert done.returncode == 2
    assert "DIAGNOSIS REFUSED" in done.stdout


def test_the_scan_declares_its_rule_before_the_run(tmp_path):
    out = tmp_path / "scan.json"
    done = _run("CODE.experiment_platform.dual_reachable_scan",
                "--config", SCAN, "--max-points", "3",
                "--root", str(ROOT), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    rule = doc["selection_rule"]
    assert rule["declared_before_run"] is True
    for key in ("R1", "R2", "R3", "R4"):
        assert key in rule
    assert doc["source"]["obs_mode"] == "frozen"
    assert doc["source"]["learning_algorithm"] == "none"


def test_the_scan_reports_failures_not_only_successes(tmp_path):
    """A scan that dropped its failures would report a ratio over an unknown
    denominator.  Every alternative must appear with a reason when it is not
    dual-reachable."""
    out = tmp_path / "scan.json"
    done = _run("CODE.experiment_platform.dual_reachable_scan",
                "--config", SCAN, "--max-points", "3",
                "--root", str(ROOT), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    population = doc["population"]
    assert population["points_executed"] == 3
    assert population["candidate_points"] >= population["points_executed"]
    alternatives = [a for p in doc["points"] for a in p["alternatives"]]
    assert alternatives, "the fixture must offer alternatives"
    for alt in alternatives:
        if alt["dual_reachable"]:
            assert alt["failure"] is None
        else:
            assert alt["failure"]["kind"] in (
                "BASELINE_NOT_DELIVERED", "FORCED_NOT_DELIVERED",
                "ENGINE_REFUSED")
    summary = doc["dual_reachable"]
    assert summary["alternatives_total"] == len(alternatives)
    assert summary["alternatives_dual"] == sum(
        1 for a in alternatives if a["dual_reachable"])
    assert summary["failure_reasons"]


def test_the_scan_bound_is_reported_rather_than_silent(tmp_path):
    out = tmp_path / "scan.json"
    done = _run("CODE.experiment_platform.dual_reachable_scan",
                "--config", SCAN, "--max-points", "2",
                "--root", str(ROOT), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    skipped = doc["population"]["points_skipped_by_bound"]
    assert len(skipped) == (doc["population"]["candidate_points"]
                            - doc["population"]["points_executed"])
