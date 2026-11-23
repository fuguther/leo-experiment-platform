"""T1-FROZEN-BRANCH: per-packet minimal mechanism comparison of one branch.

Before this entry point existed the platform could freeze an observation
(frozen) and could force one action (forced_actions), but the kernel refused
the PAIR, so the only available approximation was a refresh counterfactual -- a
different branch instant, which cannot be paired against a frozen baseline.
The generator side of that refusal is covered in
CODE/leo_sim/tests/test_frozen_observation.py; this module covers the driver.

Two scenario sources are exercised, because they answer different questions:

  * the real-constellation profile is the no_info DIAGNOSIS case (one candidate
    viable, the other dead-ends), and
  * the scripted scenarios are the COST case, where both candidates deliver and
    the egress contention is arithmetically checkable.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FROZEN = "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml"
ISL_BPS = 1_000_000.0


def _run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", *args], cwd=cwd,
                          capture_output=True, text=True)


def _compare(out, decision_id, forced, scenario=None, config=None):
    args = ["CODE.experiment_platform.minimal_branch_compare"]
    if scenario:
        args += ["--scenario", scenario]
    else:
        args += ["--config", config or FROZEN]
    args += ["--decision-id", str(decision_id), "--forced-action", forced,
             "--root", str(ROOT), "--out", str(out)]
    return _run(*args)


@pytest.fixture(scope="module")
def no_info(tmp_path_factory):
    """The real-constellation diagnosis case."""
    target = tmp_path_factory.mktemp("noinfo") / "compare.json"
    done = _compare(target, 2, "S")
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(target.read_text())


@pytest.fixture(scope="module")
def reachable(tmp_path_factory):
    """Zero load, both candidates viable."""
    target = tmp_path_factory.mktemp("reach") / "compare.json"
    done = _compare(target, 3, "W", scenario="reachability")
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(target.read_text())


@pytest.fixture(scope="module")
def contended(tmp_path_factory):
    """Zero load plus one declared competing packet on the target egress."""
    target = tmp_path_factory.mktemp("cont") / "compare.json"
    done = _compare(target, 4, "W", scenario="contention")
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(target.read_text())


# ------------------------------------------------------- pairing claim scope

def test_the_pairing_claim_is_scoped_to_what_the_evidence_covers(no_info):
    """The fingerprint hashes the DECISION LOG.  Saying "the states were
    identical" would claim more than the evidence supports, so the artifact
    must carry the narrower wording and name the coverage."""
    pairing = no_info["pairing"]
    assert "witnessed by the decision log" in pairing["claim"]
    assert "NOT packet, link, queue or RNG state" in pairing["coverage"]
    pre = no_info["pre_branch_identity"]
    assert pre["claim"] == pairing["claim"]
    assert pre["coverage"] == pairing["coverage"]
    assert pre["decision_rows_identical_before_branch"] is True
    assert pre["timeline_identical_before_branch"] is True


def test_the_first_physical_divergence_ignores_audit_only_markers(no_info):
    """A forced_action row exists only because the comparison injected an
    action.  Reporting it as the first behavioural difference would answer a
    question about the harness, so it must be excluded -- and the unfiltered
    answer must still be available for audit."""
    post = no_info["post_branch_divergence"]
    assert post["audit_only_milestones_excluded"] == ["forced_action"]
    physical = post["first_physical_divergent_row"]
    unfiltered = post["first_divergent_row_including_audit_markers"]
    assert unfiltered["baseline"]["milestone"] != "forced_action"
    assert unfiltered["counterfactual"]["milestone"] == "forced_action"
    assert physical is not None
    assert physical["baseline"]["milestone"] != "forced_action"
    assert physical["counterfactual"]["milestone"] != "forced_action"
    assert float(physical["baseline"]["at"]) >= no_info[
        "pre_branch_identity"]["t_branch_instant"]


# --------------------------------------------------- workload at two instants

def test_workload_is_measured_at_two_instants_on_a_named_resource(contended):
    """Both instants must be reported, on the SAME named resource, each saying
    where its number came from."""
    side = contended["branch"]["baseline"]
    workload = side["workload"]
    arrival, enqueue = (workload["at_peer_arrival"],
                        workload["before_target_egress_enqueue"])
    assert arrival and enqueue
    assert workload["same_resource"] is True
    assert arrival["resource"] == enqueue["resource"] == "isl:1:3"
    assert "peer_arrival" in arrival["source"]
    assert "before insertion" in enqueue["source"]
    assert workload["authoritative"] == "before_target_egress_enqueue"


def test_the_two_instants_disagree_when_the_queue_moves(contended):
    """The arrival snapshot is taken before the peer re-decides; the target
    enqueues after that computation.  The check must notice, or a stale
    snapshot would be reported as the work the packet actually found."""
    workload = contended["branch"]["baseline"]["workload"]
    arrival = workload["at_peer_arrival"]
    enqueue = workload["before_target_egress_enqueue"]
    assert workload["changed_between_the_two_instants"] is True
    assert arrival["measured_at"] < enqueue["measured_at"]
    assert arrival["in_service_remaining_bits"] > enqueue[
        "in_service_remaining_bits"]
    # the gap is exactly the peer's own computation time at the declared rate
    gap_s = (arrival["in_service_remaining_bits"]
             - enqueue["in_service_remaining_bits"]) / ISL_BPS
    assert gap_s == pytest.approx(
        contended["source"]["compute_delay_s"], abs=1e-9)


def test_a_quiet_resource_reports_no_change(reachable):
    """Negative control: with nothing queued the two instants agree, so the
    check is not simply always true."""
    workload = reachable["branch"]["baseline"]["workload"]
    assert workload["changed_between_the_two_instants"] is False
    assert workload["before_target_egress_enqueue"]["total_bits_ahead"] == 0


def test_the_workload_excludes_the_target_packet_itself(contended):
    """Measured before insertion, so the packet cannot count itself.  The
    figure must equal what was queued/in service BEFORE it, which the declared
    arithmetic pins: total ahead / rate is the measured wait."""
    side = contended["branch"]["baseline"]
    enqueue = side["workload"]["before_target_egress_enqueue"]
    assert enqueue["excludes"] == "the enqueueing packet"
    assert enqueue["measured_at"] <= side["t_peer_target_egress_enter"] + 1e-9
    wait = side["egress_wait_s"]
    lower_bound = enqueue["total_bits_ahead"] / ISL_BPS
    # the work measured at enqueue can only be a LOWER bound on the wait: the
    # control plane advertises on a timer, so the resource can grow after the
    # packet looks.  The residual is reported rather than assumed away.
    assert wait >= lower_bound - 1e-9
    assert side["egress_wait_minus_measured_work_s"] == pytest.approx(
        wait - lower_bound, abs=1e-12)
    assert side["egress_wait_minus_measured_work_s"] >= 0.0
    assert "joined the resource after the measurement" in side["egress_wait_basis"]


# ------------------------------------------------------ the two scenarios

def test_reachability_delivers_on_both_candidates(reachable):
    """The scenario exists so that a cost comparison is possible at all: if
    either candidate dead-ends, the contrast is feasibility, not cost."""
    for side in ("baseline", "counterfactual"):
        assert reachable["branch"][side]["fate"] == "DELIVERED"
    assert reachable["branch"]["baseline"]["path"] == [0, 1, 3]
    assert reachable["branch"]["counterfactual"]["path"] == [0, 2, 3]


def test_zero_load_shows_no_cost_difference_and_says_so(reachable):
    """The honest report the scenario is designed to make possible: with no
    load and two identical-geometry paths, the two candidates cost the same."""
    cost = reachable["action_cost"]
    assert cost["fate"]["changed"] is False
    assert cost["delivered_at_delta_s"] == pytest.approx(0.0, abs=1e-12)


def test_contention_cost_is_the_declared_arithmetic(contended):
    """One declared 4 Mbit packet on the target egress, links at 1 Mbps.  The
    baseline pays the outstanding part of that service; the forced branch
    avoids the egress entirely.  Every number below is checkable by hand."""
    declared = contended["declared"]
    assert declared["competing_bits"] == 4_000_000
    assert declared["isl_service_s_per_mbit"] == 1.0
    base = contended["branch"]["baseline"]
    alt = contended["branch"]["counterfactual"]
    assert base["chosen"] == "E" and alt["chosen"] == "W"
    assert base["workload"]["before_target_egress_enqueue"]["resource"] == "isl:1:3"
    assert alt["workload"]["before_target_egress_enqueue"]["resource"] == "isl:2:3"
    assert alt["workload"]["before_target_egress_enqueue"]["total_bits_ahead"] == 0
    base_wait = base["egress_wait_s"]
    assert alt["egress_wait_s"] == 0.0
    cost = contended["action_cost"]
    assert cost["delivered_at"]["baseline"] > cost["delivered_at"]["counterfactual"]
    # the exact identity this scenario exists to make checkable
    assert cost["delivered_at_delta_s"] == pytest.approx(-base_wait, rel=1e-12)
    assert cost["delivered_at_delta_equals_minus_baseline_wait"] is True


def test_the_declared_parameters_are_carried_in_the_artifact(contended):
    declared = contended["declared"]
    for key in ("target_bits", "competing_bits", "isl_service_s_per_mbit",
                "declared_competing_service_s"):
        assert key in declared
    assert declared["declared_competing_service_s"] == 4.0


# ------------------------------------------------------------- no_info case

def test_the_real_constellation_case_is_a_feasibility_contrast(no_info):
    """Kept as a FAILURE diagnosis: the non-preferred candidate leaves the
    packet at a peer that never learns a route, so this pair reports
    feasibility, not cost."""
    cost = no_info["action_cost"]
    assert no_info["source"]["scenario"] == "config"
    assert cost["fate"]["changed"] is True
    assert cost["delivered_at_delta_s"] is None
    assert no_info["branch"]["counterfactual"]["fate"] == "IN_SYSTEM_AT_STOP"


# ------------------------------------------------------------- driver surface

def test_the_source_binds_the_config_and_code_identity(no_info):
    source = no_info["source"]
    assert source["learning_algorithm"] == "none"
    assert source["obs_mode"] == "frozen"
    assert len(source["code_sha256"]) == 64
    assert len(source["config_sha256"]) == 64
    assert source["compute_delay_s"] > 0


def test_a_refresh_config_is_refused(tmp_path):
    config = tmp_path / "refresh.yaml"
    config.write_text(
        (ROOT / FROZEN).read_text().replace(
            "decision_observation_mode: frozen",
            "decision_observation_mode: refresh"))
    done = _compare(tmp_path / "out.json", 2, "S", config=str(config))
    assert done.returncode == 2
    assert "COMPARE REFUSED" in done.stdout
    assert "frozen" in done.stdout


def test_giving_both_sources_is_refused(tmp_path):
    done = _run("CODE.experiment_platform.minimal_branch_compare",
                "--config", FROZEN, "--scenario", "reachability",
                "--decision-id", "2", "--forced-action", "S",
                "--out", str(tmp_path / "out.json"))
    assert done.returncode == 2
    assert "exactly one" in done.stdout


def test_an_illegal_forced_direction_is_refused_not_crashed(tmp_path):
    done = _compare(tmp_path / "out.json", 2, "W")
    assert done.returncode == 2, done.stdout + done.stderr
    assert "COMPARE REFUSED" in done.stdout


def test_an_unknown_scenario_is_refused(tmp_path):
    done = _run("CODE.experiment_platform.minimal_branch_compare",
                "--scenario", "nope", "--decision-id", "1",
                "--forced-action", "E", "--out", str(tmp_path / "out.json"))
    assert done.returncode == 2
    assert "invalid choice" in done.stderr


def test_an_existing_destination_is_refused(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("{}")
    done = _compare(target, 2, "S")
    assert done.returncode == 2
    assert "COMPARE REFUSED" in done.stdout
