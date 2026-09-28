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
    """The reviewer's reproduction, in kernel event semantics.

    At t=1.0 the target (pid 9, 1000 bits) is inserted.  Just before insertion
    the kernel records: 2000 bits WAITING (pid 6) and 500 bits REMAINING in
    service (pid 5, a 1000-bit window 0.0 -> 2.0 s).  Correct work ahead of the
    target is therefore 2500 bits -- the queued bits PLUS the in-service
    remainder -- and the kernel's own pre-insertion record already excludes the
    target.
    """
    return [
        {"milestone": "queue_enter", "at": 0.0, "pid": 5, "link_id": "isl:1:3",
         "backlog_before": {"queued_bits_before": 0,
                            "queued_data_bits_before": 0,
                            "queued_ctrl_bits_before": 0,
                            "in_service_remaining_bits_before": 0.0}},
        {"milestone": "service_start", "at": 0.0, "pid": 5,
         "link_id": "isl:1:3"},
        {"milestone": "queue_enter", "at": 0.2, "pid": 6, "link_id": "isl:1:3",
         "backlog_before": {"queued_bits_before": 0,
                            "queued_data_bits_before": 0,
                            "queued_ctrl_bits_before": 0,
                            "in_service_remaining_bits_before": 900.0}},
        {"milestone": "queue_enter", "at": 1.0, "pid": 9, "link_id": "isl:1:3",
         "backlog_before": {"queued_bits_before": 2000,
                            "queued_data_bits_before": 2000,
                            "queued_ctrl_bits_before": 0,
                            "in_service_remaining_bits_before": 500.0}},
        {"milestone": "service_finish", "at": 2.0, "pid": 5,
         "link_id": "isl:1:3"},
        {"milestone": "service_start", "at": 2.0, "pid": 6,
         "link_id": "isl:1:3"},
        {"milestone": "service_finish", "at": 4.0, "pid": 6,
         "link_id": "isl:1:3"},
    ]


def test_the_reviewer_reproduction_returns_two_thousand_five_hundred():
    bits = {5: 1000.0, 6: 2000.0, 9: 1000.0}
    rec = cmp.resource_work_ahead(_rows(), bits, 1.0, exclude_pid=9)
    assert rec["available"] is True
    assert rec["queued_data_ahead_bits"] == pytest.approx(2000.0)
    assert rec["in_service_remaining_bits"] == pytest.approx(500.0)
    assert rec["queued_ctrl_ahead_bits"] == pytest.approx(0.0)
    assert rec["work_ahead_bits"] == pytest.approx(2500.0)
    assert rec["excluded_target_bits"] == pytest.approx(1000.0)


def test_the_reconstruction_equals_the_kernel_own_pre_insertion_record():
    """At the target enqueue instant it must equal the kernel's observation."""
    bits = {5: 1000.0, 6: 2000.0, 9: 1000.0}
    target_row = _rows()[3]
    backlog = target_row["backlog_before"]
    expected = (float(backlog["queued_bits_before"])
                + float(backlog["in_service_remaining_bits_before"]))
    rec = cmp.resource_work_ahead(_rows(), bits, target_row["at"],
                                  exclude_pid=target_row["pid"])
    assert rec["work_ahead_bits"] == pytest.approx(expected)
    assert rec["basis"] == backlog


def test_the_in_service_remainder_is_a_partial_residual_not_zero():
    bits = {5: 1000.0, 6: 2000.0, 9: 1000.0}
    early = cmp.resource_work_ahead(_rows(), bits, 0.5, exclude_pid=9)
    late = cmp.resource_work_ahead(_rows(), bits, 1.5, exclude_pid=9)
    assert early["in_service_remaining_bits"] == pytest.approx(750.0)
    assert late["in_service_remaining_bits"] == pytest.approx(250.0)
    assert early["work_ahead_bits"] > late["work_ahead_bits"]


def test_packets_that_enter_after_the_target_are_behind_it():
    rows = _rows() + [
        {"milestone": "queue_enter", "at": 1.5, "pid": 7, "link_id": "isl:1:3",
         "backlog_before": {"queued_bits_before": 2000,
                            "queued_data_bits_before": 2000,
                            "queued_ctrl_bits_before": 0,
                            "in_service_remaining_bits_before": 500.0}},
    ]
    bits = {5: 1000.0, 6: 2000.0, 7: 3000.0, 9: 1000.0}
    rec = cmp.resource_work_ahead(rows, bits, 1.6, exclude_pid=9)
    assert rec["fifo_behind_bits"] == pytest.approx(3000.0)
    assert rec["queued_data_ahead_bits"] == pytest.approx(2000.0)
    # pid 5 serves 1000 bits over [0, 2]: at 1.6 the residual is 200 bits
    assert rec["in_service_remaining_bits"] == pytest.approx(200.0)
    assert rec["work_ahead_bits"] == pytest.approx(2200.0)


def test_control_backlog_is_reported_as_its_own_priority_component():
    rows = _rows() + [
        {"milestone": "queue_enter", "at": 1.2, "pid": 8, "link_id": "isl:1:3",
         "backlog_before": {"queued_bits_before": 2400,
                            "queued_data_bits_before": 2000,
                            "queued_ctrl_bits_before": 400,
                            "in_service_remaining_bits_before": 400.0}},
    ]
    bits = {5: 1000.0, 6: 2000.0, 8: 100.0, 9: 1000.0}
    rec = cmp.resource_work_ahead(rows, bits, 1.3, exclude_pid=9)
    assert rec["queued_ctrl_ahead_bits"] == pytest.approx(400.0)
    assert rec["queued_data_ahead_bits"] == pytest.approx(2000.0)
    assert rec["work_ahead_bits"] == pytest.approx(2000.0 + 400.0 + 350.0)


def test_a_resource_that_never_appears_is_unavailable():
    rec = cmp.resource_work_ahead([], {}, 1.0, exclude_pid=1)
    assert rec["available"] is False
    assert "never_appears" in rec["reason"]


def test_an_unknown_control_backlog_is_unavailable_not_zero():
    """With no data enqueue before the instant the ctrl backlog is UNKNOWN."""
    rows = [{"milestone": "service_start", "at": 0.5, "pid": 3,
             "link_id": "isl:1:3"}]
    rec = cmp.resource_work_ahead(rows, {3: 10.0}, 1.0, exclude_pid=9)
    assert rec["queued_data_ahead_bits"] == 0.0
    assert rec["queued_ctrl_ahead_bits"] is None
    assert rec["available"] is False
    assert "queued_ctrl_ahead_bits" in rec["reason"]


def test_the_truth_wrapper_reports_components_without_double_subtraction():
    bits = {5: 1000.0, 6: 2000.0, 9: 1000.0}
    rec = cmp.truth_at_instant(_rows(), bits, 1.0, exclude_pid=9)
    assert rec["bits"] == pytest.approx(2500.0)
    assert rec["excluded_target_bits"] == pytest.approx(1000.0)
    for forbidden in ("fate", "delivered", "delivered_at", "loss", "regret"):
        assert forbidden not in rec


def test_the_truth_record_never_carries_a_final_outcome():
    rec = cmp.truth_at_instant(_rows(), {5: 2000.0}, 1.2, exclude_pid=99)
    for forbidden in ("fate", "delivered", "delivered_at", "loss", "regret"):
        assert forbidden not in rec


# ------------------------------- S1: alignment with the REAL kernel events
def test_the_reconstruction_matches_a_real_kernel_enqueue_record():
    """The kernel measures work ahead BEFORE inserting, so on a real branch
    the reconstruction must reproduce that record exactly."""
    from CODE.experiment_platform import scripted_scenarios
    from CODE.leo_sim import kernel as kernel_mod

    resolved, rows, geometry, meta = scripted_scenarios.build("contention")
    timeline = []
    kernel_mod.run_simulation(resolved, rows, geometry=geometry,
                              decision_sink=[], timeline_sink=timeline)
    target_pid = meta["declared"] and 10
    bits_by_pid = {r["packet_id"]: float(r["bits"]) for r in rows}
    enqueue = next(
        (m for m in timeline
         if m.get("milestone") == "queue_enter" and m.get("pid") == target_pid
         and isinstance(m.get("backlog_before"), dict)), None)
    assert enqueue is not None, "the fixture must enqueue the target"
    backlog = enqueue["backlog_before"]
    resource_rows = cmp._resource_timeline(timeline, enqueue["link_id"], 0.0)
    rec = cmp.resource_work_ahead(resource_rows, bits_by_pid,
                                  enqueue["at"], target_pid)
    assert rec["basis"] == backlog, "must align on the kernel's own record"
    expected = float(backlog["queued_bits_before"])
    if backlog.get("in_service_remaining_bits_before") is not None:
        expected += float(backlog["in_service_remaining_bits_before"])
    assert rec["work_ahead_bits"] == pytest.approx(expected)
    assert rec["queued_data_ahead_bits"] == pytest.approx(
        float(backlog["queued_data_bits_before"]))
    assert rec["queued_ctrl_ahead_bits"] == pytest.approx(
        float(backlog["queued_ctrl_bits_before"]))
    assert rec["excluded_target_bits"] == pytest.approx(
        float(bits_by_pid[target_pid]))


def test_the_real_kernel_reconstruction_respects_fifo_and_priority():
    """At a later instant the components must separate: what is still queued
    ahead, what is in service, and what entered after the target."""
    from CODE.experiment_platform import scripted_scenarios
    from CODE.leo_sim import kernel as kernel_mod

    resolved, rows, geometry, meta = scripted_scenarios.build("contention")
    timeline = []
    kernel_mod.run_simulation(resolved, rows, geometry=geometry,
                              decision_sink=[], timeline_sink=timeline)
    bits_by_pid = {r["packet_id"]: float(r["bits"]) for r in rows}
    enqueue = next(m for m in timeline
                   if m.get("milestone") == "queue_enter"
                   and m.get("pid") == 10)
    link = enqueue["link_id"]
    resource_rows = cmp._resource_timeline(timeline, link, 0.0)
    target_finish = [float(m["at"]) for m in timeline
                     if m.get("milestone") == "service_finish"
                     and m.get("link_id") == link and m.get("pid") == 10]
    if not target_finish:
        pytest.skip("the fixture did not serve the target on its egress")
    rec = cmp.resource_work_ahead(resource_rows, bits_by_pid,
                                  target_finish[-1] + 1e-6, 10)
    # the target has already been served, so nothing can still be ahead of it
    assert rec["work_ahead_bits"] == 0.0
    assert rec["reason"] == "excluded_packet_already_served"


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
