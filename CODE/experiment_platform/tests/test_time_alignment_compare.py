"""T1-COMPLETE P4: four-group comparison, branch pairing, truth isolation."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from CODE.experiment_platform import time_alignment_compare as cmp
from CODE.leo_sim import config as config_mod, time_alignment as ta

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "CODE.experiment_platform.time_alignment_compare",
         *args], cwd=str(ROOT), capture_output=True, text=True, timeout=900,
        check=False)


def _resolved(**over):
    base = {"execution": {"decision_observation_mode": "frozen",
                          "compute_delay_s": 0.0,
                          "node_process_delay_s": 0.0},
            "time_alignment": {"common_rule": "fixed_horizon",
                               "common_horizon_s": 3.0}}
    for key, value in over.items():
        base.setdefault(key, {}).update(value)
    return config_mod.resolve_config(base)


def _history(measured, received, bits):
    return {"generated_at": measured, "received_at": received,
            "advertised_isl_queue_bits": bits}


def _row():
    """A synthetic frozen branch with an INCREASING resource and a flat one.

    E is flat at 200000 bits; W grows from 0 to 100000 bits over 2 s.  The
    stale arm therefore sees W cheap and the now/common/candidate arms see
    W expensive enough to lose, so the ranking must change with the arm.
    """
    return {
        "decision_id": 7, "pid": 1, "sat": 0, "dst": "B",
        "kind": "forward", "chosen": "E", "candidates": ["E", "W"],
        "t_decision_start": 5.0, "t": 5.0,
        "observation_at_start": {
            "sat": 0, "legal_directions": ["E", "W"],
            "own_queue_bits": {"E": 0, "W": 0},
            "candidate_resources": {
                "E": {"status": "ok", "peer": 1, "egress_direction": "E",
                      "egress_peer": 3, "isl_rate_bps": 1e6,
                      "propagation_s": 0.01, "remaining_prop_s": 0.02,
                      "advertised_queue_known": True},
                "W": {"status": "ok", "peer": 2, "egress_direction": "S",
                      "egress_peer": 4, "isl_rate_bps": 1e6,
                      "propagation_s": 0.01, "remaining_prop_s": 0.02,
                      "advertised_queue_known": True},
            },
            "neighbours": {
                "1": {"advertised_history": [
                    _history(0.0, 0.1, {"E": 200000}),
                    _history(2.0, 2.1, {"E": 200000})]},
                "2": {"advertised_history": [
                    _history(0.0, 0.1, {"S": 0}),
                    _history(2.0, 2.1, {"S": 100000})]},
            },
        },
    }


# -------------------------------------------------------- arm behaviour
def test_the_query_instant_is_the_only_arm_difference():
    resolved = _resolved()
    row = _row()
    rankings = {}
    totals = {}
    for arm in ta.ARMS:
        snap = cmp.build_snapshot(row, resolved, arm, 3.0, 1000.0, 0.0, 0.0)
        scored = ta.score_snapshot_at(snap, snap.snapshot_at + 3.0)
        rankings[arm] = list(scored.ranking)
        totals[arm] = {s.direction: max(v for v in s.terms.values())
                       if False else s.total_s for s in scored.scores}
    # stale reads the last source values: W is still cheap
    assert rankings["stale"][0] == "W"
    # projecting to now (or beyond) makes W grow past E
    assert rankings["now"][0] == "E"
    assert rankings["common"][0] == "E"
    assert rankings["candidate"][0] == "E"
    assert totals["now"]["W"] > totals["stale"]["W"]


def test_a_constant_resource_predicts_the_same_for_every_arm():
    resolved = _resolved()
    row = _row()
    # E is flat; its projected work must be identical in every arm
    works = {}
    for arm in ta.ARMS:
        snap = cmp.build_snapshot(row, resolved, arm, 3.0, 1000.0, 0.0, 0.0)
        scored = ta.score_snapshot_at(snap, snap.snapshot_at + 3.0)
        works[arm] = scored.by_direction()["E"].terms["resource_work_s"]
    assert len(set(round(v, 12) for v in works.values())) == 1


def test_the_legal_set_is_the_decision_set_not_every_resolved_direction():
    resolved = _resolved()
    row = _row()
    row["observation_at_start"]["candidate_resources"]["N"] = {
        "status": "ok", "peer": 5, "egress_direction": "E", "egress_peer": 6,
        "isl_rate_bps": 1e6, "propagation_s": 0.01, "remaining_prop_s": 0.02,
        "advertised_queue_known": True}
    snap = cmp.build_snapshot(row, resolved, "now", 3.0, 1000.0, 0.0, 0.0)
    assert set(snap.legal_directions) == {"E", "W"}


def test_a_missing_candidate_is_scored_as_fallback_not_as_zero():
    resolved = _resolved()
    row = _row()
    row["observation_at_start"]["neighbours"].pop("2")  # W is unresolved
    row["observation_at_start"]["candidate_resources"]["W"] = {
        "status": "missing", "reason": "peer_route_no_info"}
    snap = cmp.build_snapshot(row, resolved, "now", 3.0, 1000.0, 0.0, 0.0)
    scored = ta.score_snapshot_at(snap, snap.snapshot_at + 3.0)
    assert "W" in scored.fallback_directions
    assert scored.ranking[-1] == "W"


# ------------------------------------------------------ evaluator isolation
def test_the_oracle_is_never_a_policy_input():
    class BranchOutcome:
        _t1_branch_outcome = True
    with pytest.raises(ta.TimeAlignmentError):
        ta.reject_future_input(BranchOutcome())
    with pytest.raises(ta.TimeAlignmentError):
        ta.reject_future_input(ta.TruthSample(ta.ResourceKey(1, "E", "isl"),
                                               9.0, 1.0))


def test_predictions_never_accept_a_truth_sample_in_history():
    truth = ta.TruthSample(ta.ResourceKey(1, "E", "isl"), 9.0, 1.0)
    with pytest.raises(ta.TimeAlignmentError):
        ta.predict_resource((truth,), 5.0, 6.0)


# ------------------------------------------------------- deadline handling
def test_a_single_delivered_sample_is_flagged_degenerate():
    resolved = _resolved()
    per_candidate = {
        "E": {"valid": True, "outcome": {"delay_s": 2.0}},
    }
    report = cmp._deadline(resolved, per_candidate, None)
    assert report["deadline_s"] == pytest.approx(4.0)
    assert report["delivered_samples"] == 1
    assert report["note"]


def test_a_configured_deadline_is_used_verbatim():
    report = cmp._deadline(_resolved(), {}, 7.5)
    assert report == {"deadline_s": 7.5, "source": "configured", "weak": False}
    with pytest.raises(cmp.CompareError):
        cmp._deadline(_resolved(), {}, 0.0)


def test_no_delivery_falls_back_to_the_declared_horizon():
    report = cmp._deadline(_resolved(scenario={"duration_s": 60.0}), {}, None)
    assert report["source"] == "declared_scenario_horizon"
    assert report["weak"] is True


# -------------------------------------------------------- trajectory export
def test_the_resource_trajectory_is_read_only_and_filtered():
    timeline = [
        {"milestone": "peer_arrival", "at": 1.0, "pid": 9, "link_id": "x"},
        {"milestone": "queue_enter", "at": 2.0, "pid": 9,
         "link_id": "isl:1:3", "decision_id": 4, "backlog_before": {"a": 1}},
        {"milestone": "service_start", "at": 3.0, "pid": 9,
         "link_id": "isl:1:3"},
        {"milestone": "queue_enter", "at": 4.0, "pid": 9,
         "link_id": "isl:9:9"},
    ]
    rows = cmp._resource_trajectory(timeline, "isl:1:3", 1.5)
    assert [r["milestone"] for r in rows] == ["queue_enter", "service_start"]
    assert rows[0]["backlog_before"] == {"a": 1}


# --------------------------------------------------------- end to end runs
def test_empty_equal_queues_give_zero_difference(tmp_path):
    out = tmp_path / "reach.json"
    done = _run("--scenario", "reachability", "--decision-id", "3",
                "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    assert doc["counts"]["invalid_pairs"] == 0
    cands = doc["candidates"]
    assert len(cands) == 2
    losses = {c["loss"] for c in cands.values()}
    assert losses == {0.5}, losses
    assert all(c["regret"] == 0.0 for c in cands.values())
    for arm in ta.ARMS:
        assert doc["arms"][arm]["regret"] == 0.0


def test_contention_produces_a_real_per_candidate_cost_gap(tmp_path):
    out = tmp_path / "cont.json"
    done = _run("--scenario", "contention", "--decision-id", "4",
                "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    cands = doc["candidates"]
    losses = {d: c["loss"] for d, c in cands.items() if c["valid"]}
    assert len(losses) == 2
    assert max(losses.values()) - min(losses.values()) > 0.0
    assert doc["oracle"]["chosen"] == min(losses, key=losses.get)
    assert doc["oracle"]["regret"] == 0.0
    worst = max(losses, key=losses.get)
    assert cands[worst]["regret"] == pytest.approx(
        max(losses.values()) - min(losses.values()))


def test_the_artifact_carries_identity_units_and_observation_audit(tmp_path):
    out = tmp_path / "audit.json"
    done = _run("--config", SMOKE, "--decision-id", "2", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    assert doc["schema"] == "time-alignment-compare/v1"
    assert doc["identity"]["git"]["commit"]
    assert doc["identity"]["sources"]["combined_sha256"]
    assert "dimensionless" in doc["units"]["loss"]
    assert "seconds" in doc["units"]["score_terms"]
    assert doc["observation"]["history_samples"]
    assert doc["counts"]["valid_pairs"] + doc["counts"]["invalid_pairs"] == \
        doc["counts"]["candidates"]
    for arm in ta.ARMS:
        assert arm in doc["arms"]


def test_a_deliver_branch_is_refused_loudly(tmp_path):
    done = _run("--scenario", "reachability", "--decision-id", "5",
                "--out", str(tmp_path / "deliver.json"))
    assert done.returncode == 2
    assert "COMPARE REFUSED" in done.stdout


def test_an_existing_output_is_refused(tmp_path):
    target = tmp_path / "exists.json"
    target.write_text("{}")
    done = _run("--scenario", "reachability", "--decision-id", "3",
                "--out", str(target))
    assert done.returncode == 2
    assert "exists" in done.stdout
