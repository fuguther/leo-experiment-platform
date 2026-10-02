"""T1-COMPLETE P3: time semantics, information permission, prediction, scoring.

These tests pin the ONLINE-authorised pure core: it must see only received
history, must never rewrite a source timestamp, must refuse future truth and
must never turn missing information into a numeric zero.
"""
from __future__ import annotations

import math

import pytest

from CODE.leo_sim import time_alignment as ta

R = ta.ResourceKey(2, "E", "isl")


def _sample(queue, measured, received=None, rate=8000.0, resource=None):
    return ta.StateSample(resource or R, measured,
                          measured + 0.2 if received is None else received,
                          queue, rate)


def _snapshot(**kw):
    base = dict(satellite=1, snapshot_at=1.2,
                history=(_sample(800.0, 0.0), _sample(1600.0, 1.0)),
                legal_directions=("N", "E"),
                resources={"N": ta.ResourceKey(2, "N", "isl"),
                           "E": ta.ResourceKey(2, "E", "isl")},
                egress_queue_bits={"N": 0.0, "E": 0.0},
                link_rate_bps={"N": 8000.0, "E": 8000.0},
                link_propagation_s={"N": 0.01, "E": 0.01},
                peer_process_s={"N": 0.0, "E": 0.0},
                remaining_prop_s={"N": 0.02, "E": 0.02},
                pkt_bits=800.0, arm="candidate", common_horizon_s=1.0,
                max_resource_queue_bits=1.0e9)
    base.update(kw)
    return ta.make_snapshot(**base)


# ---- source timestamps and projection --------------------------------
def test_projection_keeps_source_timestamp():
    r = ta.ResourceKey(2, "E", "isl")
    h = (ta.StateSample(r, 0.0, 0.2, 800.0, 8000.0),
         ta.StateSample(r, 1.0, 1.2, 1600.0, 8000.0))
    p = ta.predict_resource(h, snapshot_at=1.2, target_at=2.0,
                            method="bounded_linear")
    assert p.predicted_bits == 2400.0
    assert h[-1].measured_at == 1.0
    assert p.target_at == 2.0
    assert p.snapshot_at == 1.2


def test_physical_resource_generation_never_shares_predictor_history():
    old = ta.ResourceKey(2, "E", "isl", generation=4)
    current = ta.ResourceKey(2, "E", "isl", generation=9)
    history = (
        ta.StateSample(old, 0.0, 0.1, 1_000_000.0, 8_000.0),
        ta.StateSample(old, 1.0, 1.1, 2_000_000.0, 8_000.0),
        ta.StateSample(current, 1.0, 1.1, 100.0, 8_000.0),
        ta.StateSample(current, 2.0, 2.1, 200.0, 8_000.0),
    )
    prediction = ta.predict_resource(
        history, snapshot_at=2.1, target_at=3.0, resource=current)
    assert prediction.predicted_bits == pytest.approx(300.0)
    assert prediction.resource == current


def test_a_constant_resource_predicts_the_same_at_three_instants():
    r = ta.ResourceKey(3, "S", "isl")
    h = (ta.StateSample(r, 0.0, 0.1, 500.0, 1000.0),
         ta.StateSample(r, 1.0, 1.1, 500.0, 1000.0),
         ta.StateSample(r, 2.0, 2.1, 500.0, 1000.0))
    vals = {ta.predict_resource(h, 2.1, t, "bounded_linear").predicted_bits
            for t in (2.1, 3.0, 10.0)}
    assert vals == {500.0}


def test_one_valid_record_falls_back_to_hold_last():
    p = ta.predict_resource((_sample(700.0, 0.0),), 0.2, 5.0,
                            method="bounded_linear")
    assert p.predicted_bits == 700.0
    assert p.method == "hold_last"


def test_unreceived_sample_is_not_information():
    history = (_sample(800.0, 0.0), _sample(9999.0, 1.0, received=5.0))
    ordered = ta.order_history(history, snapshot_at=1.2)
    assert [s.queue_bits for s in ordered] == [800.0]


def test_an_older_source_state_cannot_overwrite_a_newer_one():
    # the newer source state arrives FIRST; the older one arrives later
    newer = _sample(2000.0, 1.0, received=1.1)
    older = _sample(100.0, 0.0, received=1.15)
    ordered = ta.order_history((newer, older), snapshot_at=1.2)
    latest = [s for s in ordered if s.measured_at == 1.0][0]
    assert latest.queue_bits == 2000.0


def test_negative_time_span_is_an_error_not_a_zero():
    with pytest.raises(ta.TimeAlignmentError):
        ta.predict_resource((_sample(1.0, 0.0),), snapshot_at=5.0, target_at=1.0)


def test_non_positive_rate_is_missing_not_zero():
    p = ta.predict_resource((ta.StateSample(R, 0.0, 0.1, 800.0, 0.0),),
                            snapshot_at=0.1, target_at=1.0)
    assert p.predicted_bits is None
    assert p.missing_reason == "non_positive_rate"


def test_nan_and_negative_queue_fail_loud():
    with pytest.raises(ta.TimeAlignmentError):
        ta.StateSample(R, 0.0, 0.1, float("nan"), 1.0)
    with pytest.raises(ta.TimeAlignmentError):
        ta.StateSample(R, 0.0, 0.1, -1.0, 1.0)


# ---- information permission -----------------------------------------
def test_truth_sample_is_refused_by_every_online_entry_point():
    truth = ta.TruthSample(R, 2.0, 100.0)
    with pytest.raises(ta.TimeAlignmentError):
        ta.predict_resource((truth,), 1.0, 2.0)
    with pytest.raises(ta.TimeAlignmentError):
        ta.reject_future_input(truth)


def test_a_kernel_handle_is_refused():
    class Trap:
        kernel = object()
    with pytest.raises(ta.TimeAlignmentError):
        ta.reject_future_input(Trap())


def test_snapshot_exposes_no_kernel_or_truth_sink():
    snap = _snapshot()
    for forbidden in ("kernel", "truth_sink", "audit", "trace", "cache"):
        assert not hasattr(snap, forbidden)


def test_snapshot_rejects_unknown_kwargs():
    with pytest.raises(TypeError):
        ta.make_snapshot(satellite=1, snapshot_at=0.0, history=(),
                         legal_directions=(), resources={},
                         kernel=object())


# ---- candidate resource, not whole satellite queue -------------------
def test_an_empty_target_egress_is_not_charged_the_peer_total_backlog():
    empty = ta.ResourceKey(5, "W", "isl")
    busy = ta.ResourceKey(5, "E", "isl")
    history = (ta.StateSample(busy, 0.0, 0.1, 9_000_000.0, 1000.0),)
    snap = _snapshot(history=history,
                     resources={"N": ta.ResourceKey(2, "N", "isl"),
                                "E": ta.ResourceKey(2, "E", "isl")})
    p = ta.predict_resource(snap.history_for(empty), snapshot_at=0.1,
                            target_at=1.0, resource=empty)
    assert p.predicted_bits is None
    assert p.missing_reason == "no_received_history"


# ---- unified scoring / fallback / tie-break --------------------------
def test_missing_candidates_share_one_fallback_ordering():
    snap = _snapshot(peer_process_s=None)  # makes every candidate missing
    scored = ta.score_snapshot_at(snap, 2.0)
    assert len(scored.fallback_directions) == 2
    assert scored.ranking == ("N", "E")


def test_unknown_fallback_preserves_visible_min_hop_order():
    snap = _snapshot(legal_directions=("E", "N"), peer_process_s={})
    scored = ta.plan_decision(snap).scored
    assert scored.fallback_directions == ("E", "N")
    assert scored.ranking == ("E", "N")


def test_common_excludes_unknown_eta_and_marks_insufficient_sample():
    snap = _snapshot(legal_directions=("E", "N"), peer_process_s={"N": 0.0},
                     arm="common", common_horizon_s=None)
    assert ta.resolve_common_horizon(snap) is None
    scored = ta.plan_decision(snap).scored
    assert scored.ranking == ("E", "N")
    assert all("NO_STRONG_COMMON" in item.missing for item in scored.scores)


def test_tie_break_is_arm_independent():
    rankings = {}
    for arm in ta.ARMS:
        snap = _snapshot(arm=arm)
        rankings[arm] = ta.score_snapshot_at(snap, 2.0).ranking
    assert len(set(rankings.values())) == 1


def test_only_the_query_instant_differs_between_arms():
    seen = {}
    for arm in ta.ARMS:
        snap = _snapshot(arm=arm)
        preds = {}
        etas = {}
        for direction in snap.legal_directions:
            resource = snap.resource_for(direction)
            etas[direction] = ta.estimate_eta(snap, direction)
            target = ta.arm_target_at(
                arm, snapshot_at=snap.snapshot_at,
                last_measured_at=max(s.measured_at for s in snap.history),
                common_horizon_s=snap.common_horizon_s,
                candidate_target_at=2.0)
            preds[direction] = ta.predict_resource(
                snap.history_for(resource), snap.snapshot_at, target,
                max_queue_bits=snap.max_resource_queue_bits, resource=resource,
                allow_past=(arm == "stale"))
        seen[arm] = {d: preds[d].target_at for d in preds}
    assert seen["now"] == {"N": 1.2, "E": 1.2}
    assert seen["stale"] == {"N": 1.0, "E": 1.0}
    assert seen["common"] == {"N": 2.2, "E": 2.2}
    assert seen["candidate"] == {"N": 2.0, "E": 2.0}


# ---- schedule build / lookup ----------------------------------------
def test_async_point_has_one_bin_and_async_window_has_four():
    snap = _snapshot()
    point = ta.build_schedule(snap, install_estimate=10.0, window_s=1.0, bins=1)
    window = ta.build_schedule(snap, install_estimate=10.0, window_s=1.0, bins=4)
    assert len(point.entries) == 1
    assert [round(e.starts_at, 6) for e in window.entries] == [
        10.0, 10.25, 10.5, 10.75]
    assert window.expires_at == 11.0


def test_explicit_schedule_target_changes_per_bin_prediction_and_ranking():
    north = ta.ResourceKey(2, "N", "isl")
    east = ta.ResourceKey(3, "E", "isl")
    history = (
        ta.StateSample(north, 0.0, 0.1, 0.0, 1000.0),
        ta.StateSample(north, 1.0, 1.05, 100.0, 1000.0),
        ta.StateSample(east, 0.0, 0.1, 300.0, 1000.0),
        ta.StateSample(east, 1.0, 1.05, 200.0, 1000.0),
    )
    snap = _snapshot(
        snapshot_at=1.1, history=history,
        resources={"N": north, "E": east},
        resource_service_rate_bps={"N": 1000.0, "E": 1000.0},
        local_egress_in_service_s={"N": 0.0, "E": 0.0},
        max_resource_queue_bits=10_000.0,
    )
    early = ta.score_snapshot_at(snap, 1.2)
    late = ta.score_snapshot_at(snap, 2.0)
    assert early.ranking == ("N", "E")
    assert late.ranking == ("E", "N")

    schedule = ta.build_schedule(
        snap, install_estimate=1.1, window_s=1.0, bins=2)
    assert [entry.query_target_at for entry in schedule.entries] == [1.35, 1.85]
    assert schedule.entries[0].ranking != schedule.entries[1].ranking


def test_bin_selection_uses_the_actual_install_instant():
    snap = _snapshot()
    window = ta.build_schedule(snap, install_estimate=10.0, window_s=1.0, bins=4)
    assert window.bin_for(10.1).bin_index == 0
    assert window.bin_for(10.3).bin_index == 1
    assert window.bin_for(12.0) is None


def test_lookup_schedule_filters_legality_and_loops_without_rescoring():
    snap = _snapshot()
    window = ta.build_schedule(snap, install_estimate=1.2, window_s=1.0, bins=1)
    hit = ta.lookup_schedule(window, 1.7, legal=("N", "E"), path=())
    assert hit["action"] == window.entries[0].ranking[0]
    assert hit["fallback"] is False
    looped = ta.lookup_schedule(window, 1.7, legal=("N", "E"),
                                path=(window.entries[0].ranking[0],))
    assert looped["action"] != window.entries[0].ranking[0]
    none_legal = ta.lookup_schedule(window, 1.7, legal=(), path=())
    assert none_legal["action"] is None and none_legal["fallback"] is True
    expired = ta.lookup_schedule(window, 99.0, legal=("N",), path=())
    assert expired["state"] == "expired"


def test_build_schedule_rejects_a_non_positive_window_and_bad_bins():
    snap = _snapshot()
    with pytest.raises(ta.TimeAlignmentError):
        ta.build_schedule(snap, 0.0, 0.0, 1)
    with pytest.raises(ta.TimeAlignmentError):
        ta.build_schedule(snap, 0.0, 1.0, 0)


# ---- P3.1 configuration namespace ------------------------------------
def _resolve(**over):
    from CODE.leo_sim import config as config_mod
    return config_mod.resolve_config(over)


def test_the_new_namespaces_default_to_disabled():
    resolved = _resolve()["config"]
    assert resolved["time_alignment"]["enabled"] is False
    assert resolved["async_routing"]["enabled"] is False
    assert resolved["time_alignment"]["execution_mode"] == "per_packet"
    assert resolved["async_routing"]["window_bins"] == 4
    assert resolved["time_alignment"]["query_delay_s"] == 1e-06


def test_an_unknown_field_in_either_namespace_is_rejected():
    from CODE.leo_sim import config as config_mod
    with pytest.raises(config_mod.ConfigError):
        _resolve(time_alignment={"rogue": 1})
    with pytest.raises(config_mod.ConfigError):
        _resolve(async_routing={"rogue": 1})


def test_bad_time_alignment_values_are_rejected():
    from CODE.leo_sim import config as config_mod
    for bad in ({"arm": "oracle"}, {"predictor": "lstm"},
                {"history_limit": 0}, {"common_rule": "mean"},
                {"per_flow_ttl_s": 0.0}, {"query_delay_s": -1.0}):
        with pytest.raises(config_mod.ConfigError):
            _resolve(time_alignment=bad)


def test_fixed_horizon_needs_a_horizon_and_vice_versa():
    from CODE.leo_sim import config as config_mod
    with pytest.raises(config_mod.ConfigError):
        _resolve(time_alignment={"common_rule": "fixed_horizon"})
    with pytest.raises(config_mod.ConfigError):
        _resolve(time_alignment={"common_rule": "median_eta",
                                 "common_horizon_s": 1.0})
    ok = _resolve(time_alignment={"common_rule": "fixed_horizon",
                                  "common_horizon_s": 1.0})
    assert ok["config"]["time_alignment"]["common_horizon_s"] == 1.0


def test_async_execution_mode_and_async_routing_must_agree():
    from CODE.leo_sim import config as config_mod
    with pytest.raises(config_mod.ConfigError):
        _resolve(time_alignment={"execution_mode": "async_window"})
    with pytest.raises(config_mod.ConfigError):
        _resolve(async_routing={"enabled": True})
    ok = _resolve(time_alignment={"execution_mode": "async_window"},
                  async_routing={"enabled": True})
    assert ok["config"]["time_alignment"]["execution_mode"] == "async_window"


def test_async_point_forces_a_single_bin():
    from CODE.leo_sim import config as config_mod
    with pytest.raises(config_mod.ConfigError):
        _resolve(time_alignment={"execution_mode": "async_point"},
                  async_routing={"enabled": True, "window_bins": 4})
    ok = _resolve(time_alignment={"execution_mode": "async_point"},
                  async_routing={"enabled": True, "window_bins": 1})
    assert ok["config"]["async_routing"]["window_bins"] == 1


def test_bad_async_intervals_are_rejected():
    from CODE.leo_sim import config as config_mod
    for bad in ({"update_interval_s": 0.0}, {"valid_window_s": -1.0},
                {"install_delay_s": -0.001}, {"window_bins": 0},
                {"window_bins": True}, {"max_pending_per_scope": 0},
                {"trigger": "on_demand"}):
        with pytest.raises(config_mod.ConfigError):
            _resolve(async_routing=bad)


def test_enabling_time_alignment_changes_the_config_identity():
    base = _resolve()
    enabled = _resolve(time_alignment={"enabled": True})
    assert base["sha256"] != enabled["sha256"]
