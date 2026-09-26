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
CAUSES = {"NO_DESTINATION_ADVERTISEMENT", "EXPIRED", "ABSENT",
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


def test_the_observation_alone_cannot_name_the_cause(tmp_path):
    """The original case, described by what the evidence supports: the
    observation says only that no covering advertisement had arrived.  An
    earlier version called this RANGE and asserted reach; that claim is
    retracted here, and the reach explanation must come from the separate
    audit (test_the_isolated_audit_attributes_what_the_observation_cannot)
    and from the single-factor intervention
    (test_the_two_single_factor_arms_discriminate_the_cause)."""
    doc = _diagnose(tmp_path / "d.json", forced=(2, "S"))
    causes = doc["totals"]["no_info_by_cause"]
    assert causes, "the forced branch is expected to hold"
    assert set(causes) == {"NO_DESTINATION_ADVERTISEMENT"}, causes
    assert doc["pairs_never_covered"], (
        "the undetermined case must still name the (satellite, destination) "
        "pair that was never covered")


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


# ---------------------------------------------- retraction and counterexamples

def test_the_default_cause_does_not_claim_a_reason(tmp_path):
    """The observation alone cannot tell "never in reach" from "not generated
    yet" from "still in flight" from "expired on the way".  The default cause
    must say so instead of picking one."""
    doc = _diagnose(tmp_path / "d.json", forced=(2, "S"))
    assert "RANGE" not in doc["totals"]["no_info_by_cause"]
    assert set(doc["totals"]["no_info_by_cause"]) <= {
        "NO_DESTINATION_ADVERTISEMENT", "ABSENT", "EXPIRED",
        "COVERING_ENTRY_PRESENT"}
    flagged = [a for a in doc["attempts"]
               if a["cause"] == "NO_DESTINATION_ADVERTISEMENT"]
    assert flagged, "the forced branch must produce the default cause"
    for attempt in flagged:
        assert attempt["cause_undetermined"] is True


def test_first_coverage_is_keyed_by_satellite_and_destination(tmp_path):
    """A satellite can be covered for one destination and never for another;
    a satellite-only key would average the two into neither."""
    doc = _diagnose(tmp_path / "d.json", forced=(2, "S"))
    assert doc["first_covered_key"] == "sat|destination_cell"
    for key in doc["first_covered_at"]:
        sat, _sep, cell = key.partition("|")
        assert sat.isdigit() and cell
    for key in doc["pairs_never_covered"]:
        assert "|" in key


def test_a_multi_destination_trace_is_not_treated_as_one_destination(tmp_path):
    """The smoke profile carries two endpoints.  A covering entry for the
    OTHER destination must never be counted as covering this packet's."""
    doc = _diagnose(tmp_path / "d.json", forced=(2, "S"))
    cells = {a["packet_dst_cell"] for a in doc["attempts"]}
    assert len(cells) >= 1
    for attempt in doc["attempts"]:
        assert attempt["packet_dst_cell"]
        for entry in attempt["cache_entries"]:
            covers = attempt["packet_dst_cell"] in entry["advertised_serve_cells"]
            assert entry["covers_destination"] is covers


def test_the_isolated_audit_attributes_what_the_observation_cannot(tmp_path):
    """The audit is allowed to see the whole constellation because it decides
    nothing; it must turn the undetermined default into a named lifecycle
    event."""
    out = tmp_path / "audited.json"
    done = _run("CODE.experiment_platform.no_info_diagnosis",
                "--config", SMOKE, "--forced-decision-id", "2",
                "--forced-action", "S", "--ctrl-audit",
                "--root", str(ROOT), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    verdicts = doc["audited_verdicts"]
    assert verdicts, "the audit must produce verdicts"
    assert set(verdicts) <= {
        "AUDIT_NO_DESTINATION_SERVER_ADVERTISED",
        "AUDIT_GENERATED_AFTER_THE_DECISION", "AUDIT_HOP_LIMIT",
        "AUDIT_EXPIRED_BEFORE_USE", "AUDIT_NEVER_REACHED_UNRESOLVED",
        "AUDIT_UNEXPLAINED"}
    explained = [a for a in doc["attempts"] if a.get("audit")]
    assert explained, "attempts must carry their audit record"
    for attempt in explained:
        audit = attempt["audit"]
        assert audit["verdict"] in verdicts
        assert audit["detail"]
        assert audit["dst_advertisements_generated"] >= 0


def test_the_two_single_factor_arms_discriminate_the_cause(tmp_path):
    """The intervention that matters: on a FIXED trace, extending the drain
    window must not resolve the holds while widening the control plane's reach
    must.  If both resolved it, or neither did, the cause would still be open."""
    out = tmp_path / "reach.json"
    done = _run("CODE.experiment_platform.control_reach_probe",
                "--config", SMOKE, "--forced-decision-id", "2",
                "--forced-action", "S", "--drain-to", "200", "--vis-k", "4",
                "--root", str(ROOT), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    assert doc["source"]["rows_identical_across_arms"] is True
    arms = {a["arm"]: a for a in doc["arms"]}
    base = arms["baseline"]["no_info_by_cause"].get(
        "NO_DESTINATION_ADVERTISEMENT", 0)
    assert base > 0
    # time alone does not help: the holds accumulate instead
    assert arms["extended_drain"]["delivered"] == 0
    assert (arms["extended_drain"]["no_info_by_cause"].get(
        "NO_DESTINATION_ADVERTISEMENT", 0) >= base)
    # reach does help, and it is reported with what it cost
    assert arms["wider_reach"]["delivered"] >= 1
    assert (arms["wider_reach"]["no_info_by_cause"].get(
        "NO_DESTINATION_ADVERTISEMENT", 0) < base)
    overhead = arms["wider_reach"]["control_overhead"]
    assert overhead["registered"] > arms["baseline"]["control_overhead"]["registered"]
    assert overhead["events_processed"] > arms["baseline"]["control_overhead"]["events_processed"]


def test_the_reach_probe_needs_a_factor(tmp_path):
    done = _run("CODE.experiment_platform.control_reach_probe",
                "--config", SMOKE, "--root", str(ROOT),
                "--out", str(tmp_path / "x.json"))
    assert done.returncode == 2
    assert "PROBE REFUSED" in done.stdout
