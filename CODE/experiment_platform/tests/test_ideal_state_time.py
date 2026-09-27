"""T1-COMPLETE R2: the three IDEAL state-time arms.

oracle_now / oracle_common / oracle_candidate replace the resource state with
the TRUTH of the NAMED resource at that arm own query instant, read from each
candidate OWN branch, and then run the SAME unified scorer.  The final outcome
never enters that input; it only scores the chosen action afterwards.
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import time_alignment_compare as cmp
from CODE.leo_sim import time_alignment as ta

R_E = ta.ResourceKey(1, "E", "isl")
R_W = ta.ResourceKey(2, "S", "isl")


def _snapshot():
    history = (ta.StateSample(R_E, 0.0, 0.1, 0.0, 1e6),
               ta.StateSample(R_W, 0.0, 0.1, 0.0, 1e6))
    return ta.make_snapshot(
        satellite=0, snapshot_at=1.0, history=history,
        legal_directions=("E", "W"),
        resources={"E": R_E, "W": R_W},
        egress_queue_bits={"E": 0.0, "W": 0.0},
        link_rate_bps={"E": 1e6, "W": 1e6},
        link_propagation_s={"E": 0.01, "W": 0.01},
        peer_process_s={"E": 0.0, "W": 0.0},
        remaining_prop_s={"E": 0.02, "W": 0.02},
        pkt_bits=1000.0, arm="candidate")


# ------------------------------------------------- truth reconstruction
def _rows():
    return [
        {"milestone": "queue_enter", "at": 1.0, "pid": 5, "link_id": "isl:1:3",
         "backlog_before": {"queued_bits_before": 2000.0,
                            "in_service_remaining_bits_before": 0.0}},
        {"milestone": "service_finish", "at": 1.5, "pid": 5,
         "link_id": "isl:1:3"},
        {"milestone": "queue_enter", "at": 2.0, "pid": 9, "link_id": "isl:1:3",
         "backlog_before": {"queued_bits_before": 500.0,
                            "in_service_remaining_bits_before": 0.0}},
    ]


def test_truth_at_instant_subtracts_what_left_service():
    bits = {5: 2000.0, 9: 500.0}
    rec = cmp.truth_at_instant(_rows(), bits, 1.2, exclude_pid=99)
    assert rec["available"] is True
    assert rec["bits"] == pytest.approx(2000.0)
    assert rec["served_bits_subtracted"] == 0.0
    rec = cmp.truth_at_instant(_rows(), bits, 1.6, exclude_pid=99)
    assert rec["bits"] == pytest.approx(0.0)
    assert rec["served_bits_subtracted"] == pytest.approx(2000.0)


def test_truth_at_instant_excludes_the_target_packet_itself():
    bits = {5: 2000.0, 9: 500.0}
    rec = cmp.truth_at_instant(_rows(), bits, 2.5, exclude_pid=9)
    assert rec["excluded_self"] is True
    assert rec["bits"] == pytest.approx(0.0)


def test_an_empty_queue_at_the_instant_is_a_zero_not_a_missing_value():
    rec = cmp.truth_at_instant(_rows(), {5: 2000.0}, 0.5, exclude_pid=99)
    assert rec["available"] is True
    assert rec["bits"] == 0.0
    assert rec["reason"] == "queue_empty_at_instant"


def test_a_resource_that_never_appears_is_unavailable():
    rec = cmp.truth_at_instant([], {}, 1.0, exclude_pid=1)
    assert rec["available"] is False
    assert "never_appears" in rec["reason"]


def test_the_truth_record_never_carries_a_final_outcome():
    rec = cmp.truth_at_instant(_rows(), {5: 2000.0}, 1.2, exclude_pid=99)
    for forbidden in ("fate", "delivered", "delivered_at", "loss", "regret"):
        assert forbidden not in rec


# --------------------------------------------- the ideal arms themselves
def test_the_common_and_candidate_instants_can_pick_different_actions():
    snap = _snapshot()
    eta_targets = {d: ta.estimate_eta(snap, d).target_at
                   for d in snap.legal_directions}
    common_instant = snap.snapshot_at + 0.05
    truth = {
        ("E", round(common_instant, 6)): 0.0,
        ("W", round(common_instant, 6)): 1000.0,
        ("E", round(eta_targets["E"], 6)): 1000.0,
        ("W", round(eta_targets["W"], 6)): 0.0,
    }

    def truth_fn(direction, instant):
        value = truth.get((direction, round(instant, 6)))
        return {"bits": value, "available": value is not None,
                "instant": instant, "reason": None,
                "source": "fixture"}

    scored_common, detail_common = cmp.score_with_state(
        snap, {d: common_instant for d in snap.legal_directions},
        use_truth=True, truth_fn=truth_fn)
    scored_candidate, detail_candidate = cmp.score_with_state(
        snap, eta_targets, use_truth=True, truth_fn=truth_fn)
    assert scored_common.ranking[0] == "E"
    assert scored_candidate.ranking[0] == "W"
    assert detail_common["E"]["bits"] == 0.0
    assert detail_candidate["E"]["bits"] == 1000.0


def test_the_zero_gain_control_makes_every_ideal_arm_agree():
    snap = _snapshot()
    targets_now = {d: snap.snapshot_at for d in snap.legal_directions}
    targets_common = {d: snap.snapshot_at + 0.05
                      for d in snap.legal_directions}
    targets_candidate = {d: ta.estimate_eta(snap, d).target_at
                         for d in snap.legal_directions}

    def flat_truth(direction, instant):
        return {"bits": 0.0, "available": True, "instant": instant,
                "reason": None, "source": "fixture"}

    rankings = []
    for targets in (targets_now, targets_common, targets_candidate):
        scored, _detail = cmp.score_with_state(snap, targets, use_truth=True,
                                               truth_fn=flat_truth)
        rankings.append(tuple(scored.ranking))
    assert len(set(rankings)) == 1, rankings


def test_a_truth_arm_uses_the_shared_scorer_and_marks_its_source():
    snap = _snapshot()
    targets = {d: snap.snapshot_at for d in snap.legal_directions}

    def flat_truth(direction, instant):
        return {"bits": 100.0, "available": True, "instant": instant,
                "reason": None, "source": "fixture"}

    scored, detail = cmp.score_with_state(snap, targets, use_truth=True,
                                          truth_fn=flat_truth)
    view = cmp.arm_view(scored, detail, "oracle_now", "fixture", {}, None)
    assert view["query_instants"] == {"E": snap.snapshot_at,
                                      "W": snap.snapshot_at}
    assert view["truth_inputs"]["E"]["source"] == "fixture"
    # the shared scorer produced the same term set as the online path
    terms = view["scores"]["E"]["terms"]
    for name in ta.TERM_KEYS:
        assert name in terms, name


# ----------------------------------------------------------- integration
def test_the_artifact_carries_the_three_ideal_arms_and_the_2x2():
    doc = cmp.compare_scenario("contention", 4, None)
    for name in ("oracle_now", "oracle_common", "oracle_candidate"):
        arm = doc["ideal_arms"][name]
        assert arm["chosen"]
        assert arm["query_instants"]
        assert arm["truth_inputs"]
        for direction, record in arm["truth_inputs"].items():
            assert "instant" in record
            assert "available" in record
    assert set(doc["eta_queue_2x2"]) == {
        "eta_estimated_x_queue_predicted", "eta_estimated_x_queue_truth",
        "eta_true_x_queue_predicted", "eta_true_x_queue_truth"}
    assert "diagnostic only" in doc["eta_queue_2x2_meaning"]["eta_true"]
