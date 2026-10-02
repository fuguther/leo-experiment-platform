"""T1-COMPLETE P5: the four state-time arms in the real forwarding path."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from CODE.leo_sim import config, kernel, time_alignment as ta
from CODE.leo_sim.control import CacheEntry
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=VIS)


def _cfg(ta_over=None, seed=7):
    ta_cfg = {"enabled": True, "arm": "candidate"}
    ta_cfg.update(ta_over or {})
    return make_cfg({"scenario": {"duration_s": 12.0, "seed": seed},
                     "execution": {"decision_observation_mode": "frozen",
                                   "compute_delay_s": 0.5},
                     "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
                     "time_alignment": ta_cfg})


def _run(cfg, rows=None):
    sink, timeline = [], []
    rows = rows if rows is not None else [row(i, 0.5 * i, A, B)
                                          for i in (1, 2, 3)]
    res = kernel.run_simulation(cfg, rows, geometry=_geo(),
                                decision_sink=sink, timeline_sink=timeline)
    return res, sink, timeline


def _forward_rows(sink):
    return [r for r in sink if r.get("kind") == "forward"]


def test_disabled_time_alignment_leaves_the_audit_empty():
    _res, sink, _tl = _run(make_cfg({"execution": {
        "decision_observation_mode": "frozen", "compute_delay_s": 0.5}}))
    assert sink
    for r in _forward_rows(sink):
        assert (r["observation_at_start"] or {}).get("time_alignment") is None


def test_enabling_time_alignment_records_a_replayable_audit():
    _res, sink, _tl = _run(_cfg())
    forwards = _forward_rows(sink)
    assert forwards, "the fixture must forward"
    for r in forwards:
        audit = r["observation_at_start"]["time_alignment"]
        assert audit is not None
        assert audit["arm"] == "candidate"
        assert audit["predictor"] == "bounded_linear"
        assert audit["snapshot_at"] == r["t_decision_start"]
        assert audit["ranking"]
        assert audit["applied_order"]
        assert set(audit["applied_order"]) == set(r["candidates"])
        assert "received advertisements" in audit["source"]


def test_the_snapshot_only_contains_already_received_history():
    _res, sink, _tl = _run(_cfg())
    for r in _forward_rows(sink):
        obs = r["observation_at_start"]
        t0 = r["t_decision_start"]
        for neighbour in (obs.get("neighbours") or {}).values():
            for rec in neighbour.get("advertised_history") or []:
                assert rec["received_at"] <= t0 + 1e-12
                assert rec["generated_at"] <= rec["received_at"] + 1e-12


def test_all_four_arms_see_the_same_t0_input():
    def keyed(forwards):
        return json.dumps([
            {"sat": r["sat"],
             "candidates": sorted(r["candidates"]),
             "resources": sorted(
                 (d, cr.get("peer"), cr.get("egress_direction"))
                 for d, cr in (r["observation_at_start"].get(
                     "candidate_resources") or {}).items())}
            for r in forwards], sort_keys=True, default=str)

    seen = {}
    for arm in ta.ARMS:
        _res, sink, _tl = _run(_cfg({"arm": arm}))
        forwards = _forward_rows(sink)
        assert forwards
        seen[arm] = keyed(forwards)
    assert len(set(seen.values())) == 1


def test_received_downlink_advertisement_scores_terminal_with_gsl_rate():
    # The snapshot includes one ISL hop to sat1 and sat1's shared GSL service.
    # The two service rates are deliberately far apart so an accidental ISL
    # rate reuse is visible in the audit.
    cfg = make_cfg({
        "scenario": {"duration_s": 10.0, "num_satellites": 2,
                      "num_planes": 1},
        "demand": {"packet_bits": 8000},
        "links": {"isl_rate_mbps": 5.0},
        "access": {"uplink_rate_mbps": 1000.0,
                   "downlink_rate_mbps": 100.0,
                   "idle_release_s": 100.0, "slot_lease_s": 100.0},
        "control_plane": {"enabled": True,
                          "advertisement_protocol_version": 2,
                          "advertise_interval_s": 0.2,
                          "ttl_s": 10.0, "vis_k": 2,
                          "packet_bits": 10_000},
        "routing": {"policy": "hop"},
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": 0.1},
        "time_alignment": {"enabled": True, "arm": "candidate"},
    })
    sink, timeline = [], []
    sim = kernel.Kernel(cfg, [row(71, 0.0, A, B, bits=8000)], geometry=_geo(),
                        decision_sink=sink, timeline_sink=timeline)
    result = sim.run()
    assert result["fates"][71] == "DELIVERED"
    forward = next(r for r in _forward_rows(sink) if r["pid"] == 71)
    obs = forward["observation_at_start"]
    resource = obs["candidate_resources"]["E"]
    score = obs["time_alignment"]["scores"]["E"]
    assert resource["status"] == "delivered_downlink"
    assert resource["downlink_queue_scope"] == "satellite_shared_drr"
    assert resource["isl_rate_bps"] == 5_000_000.0
    assert resource["downlink_rate_bps"] == 100_000_000.0
    assert resource["downlink_available"] is True
    assert score["fallback"] is False, score
    assert score["terms"]["tx_s"] == pytest.approx(8000 / 5_000_000)
    assert score["terms"]["terminal_tx_s"] == pytest.approx(8000 / 100_000_000)
    assert score["terms"]["terminal_prop_s"] >= 0
    peer_record = obs["neighbours"]["1"]
    delivered_ad = peer_record["advertised_history"][-1]
    assert delivered_ad["advertised_isl_generation"]["W"] == \
        sim.isls[1]["W"].gen
    assert delivered_ad["advertised_isl_rate_bps"]["W"] == 5_000_000.0
    assert delivered_ad["advertised_isl_work_ahead_bits_proxy"]["W"] >= 0
    assert result["control"]["bits"]["offered"] >= 10_000


def test_compute_fifo_throttles_two_od_flows_before_fast_shared_isl():
    """The configured 1 ms decision pool, not a 594 Mbps ISL, paces ingress."""
    src_a, src_b = cell(0.0, 0.0), cell(10.0, 0.0)
    dst_a, dst_b = cell(0.0, 10.0), cell(10.0, 10.0)
    src_centers = {cell_center(src_a), cell_center(src_b)}
    dst_centers = {cell_center(dst_a), cell_center(dst_b)}
    geo = StaticGeometry(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda sat, lat, lon, _t: (
            sat == 0 and (lat, lon) in src_centers
            or sat == 1 and (lat, lon) in dst_centers))
    rows = [row(101, 0.0, src_a, dst_a, bits=12_000),
            row(102, 0.0, src_b, dst_b, bits=12_000)]
    overrides = {
        "scenario": {"duration_s": 0.007, "num_satellites": 2,
                     "num_planes": 1},
        "demand": {"packet_bits": 12_000},
        "links": {"rate_model": "constant", "isl_rate_mbps": 594.152},
        "access": {"uplink_rate_mbps": 1000.0,
                   "downlink_rate_mbps": 1000.0,
                   "acquisition_delay_s": 0.0,
                   "idle_release_s": 100.0, "slot_lease_s": 100.0},
        "control_plane": {"enabled": False},
        "routing": {"policy": "oracle"},
        "execution": {"compute_delay_s": 0.001,
                      "compute_servers_per_satellite": 1,
                      "decision_observation_mode": "refresh"},
    }
    cfg = make_cfg(overrides)
    sink, timeline = [], []
    sim = kernel.Kernel(cfg, rows, geometry=geo,
                        decision_sink=sink, timeline_sink=timeline)
    result = sim.run()

    admissions = [event for event in timeline
                  if event.get("milestone") == "queue_enter"
                  and event.get("queue") == "isl"
                  and event.get("link_id") == "isl:0:1"]
    assert {event["pid"] for event in admissions} == {101, 102}
    admissions.sort(key=lambda event: event["at"])
    serial_s = 12_000 / 594_152_000
    assert admissions[1]["at"] - admissions[0]["at"] >= 0.001 - 1e-9
    for event in admissions:
        ahead = event["backlog_before"]
        assert ahead["resource"] == "isl:0:1"
        assert ahead["queued_data_bits_before"] == 0
        assert ahead["in_service_remaining_bits_before"] in (None, 0, 0.0)
    assert serial_s < 0.000021
    assert result["control"]["bits"]["offered"] == 0
    assert result["control"]["counters"]["entered_queue"] == 0

    control_cfg = make_cfg({**overrides, "control_plane": {
        "enabled": True, "advertisement_protocol_version": 2,
        "advertise_interval_s": 1.0, "ttl_s": 10.0,
        "vis_k": 2, "packet_bits": 8_000,
    }})
    control_sink, control_timeline = [], []
    control_sim = kernel.Kernel(
        control_cfg, rows, geometry=geo, decision_sink=control_sink,
        timeline_sink=control_timeline)
    control_result = control_sim.run()
    control_admissions = [event for event in control_timeline
                          if event.get("milestone") == "queue_enter"
                          and event.get("packet_kind") == "control"
                          and event.get("link_id") == "isl:0:1"]
    control_data_admissions = [event for event in control_timeline
                               if event.get("milestone") == "queue_enter"
                               and event.get("queue") == "isl"
                               and event.get("link_id") == "isl:0:1"]
    assert control_result["control"]["counters"]["entered_queue"] > 0
    assert control_result["control"]["bits"]["offered"] == 17_216
    assert control_admissions
    control_service = [event for event in control_timeline
                       if event.get("milestone") == "service_start"
                       and event.get("packet_kind") == "control"
                       and event.get("link_id") == "isl:0:1"]
    assert control_service
    assert {event["pid"] for event in control_data_admissions} == {101, 102}
    assert all(event["backlog_before"]["queued_ctrl_bits_before"] == 0
               for event in control_data_admissions)
    assert (control_data_admissions[1]["at"]
            - control_data_admissions[0]["at"] >= 0.001 - 1e-9)


def test_online_resource_history_is_bound_to_physical_link_generation():
    nb = {0: {"E": 1}, 1: {"W": 0, "E": 2}, 2: {"W": 1}}
    geo = StaticGeometry(
        3, neighbors_map=nb,
        visible=lambda sat, lat, lon, _t: (
            sat == 0 and (lat, lon) == AC
            or sat == 2 and (lat, lon) == BC))
    cfg = make_cfg({
        "scenario": {"duration_s": 4.0, "num_satellites": 3,
                     "num_planes": 1},
        "control_plane": {"enabled": True, "vis_k": 3,
                          "ttl_s": 10.0},
        "routing": {"policy": "hop"},
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": 0.01},
        "time_alignment": {"enabled": True, "arm": "candidate"},
    })
    k = kernel.Kernel(cfg, [row(91, 0.0, A, B, bits=8000)], geometry=geo,
                      decision_sink=[], timeline_sink=[])
    k.isls[1]["E"].gen = 9
    for generated, received, generation, work in (
            (0.0, 0.05, 4, 1_000_000.0),
            (0.1, 0.15, 9, 100.0),
            (0.4, 0.45, 9, 200.0)):
        payload = {"serve_cells": [], "isl_queue_bits": {
            "E": {"peer": 2, "generation": generation, "value": int(work),
                  "rate_bps": 5_000_000.0,
                  "work_ahead_bits_proxy": work}}}
        k.caches[0].put(CacheEntry(1, payload, generated, received, 10.0))
    k.caches[0].put(CacheEntry(2, {"serve_cells": [B]}, 0.0, 0.05, 10.0))
    pkt = SimpleNamespace(bits=8000, dst=B, path=[])

    snap = k._build_ta_snapshot(
        pkt, 0, 0.5, ["E"], {}, "candidate", None)

    resource = snap.resource_for("E")
    assert resource == ta.ResourceKey(1, "E", "isl", generation=9)
    samples = snap.history_for(resource)
    assert [sample.queue_bits for sample in samples] == [100.0, 200.0]
    assert [sample.rate_bps for sample in samples] == [5_000_000.0] * 2
    assert snap.resource_rate_for("E") == 5_000_000.0


def test_legacy_advertisement_without_resource_identity_stays_unknown():
    cfg = _cfg()
    k = kernel.Kernel(cfg, [row(93, 0.0, A, B)], geometry=_geo(),
                      decision_sink=[], timeline_sink=[])
    # A previously received advertisement has the legacy queue-only field,
    # but lacks physical generation, rate and work-ahead measurements.
    # Parsing must not turn that old queue value into a finite ETA sample.
    k.caches[0].put(CacheEntry(
        1, {"isl_queue_bits": {"W": {"peer": 0, "value": 999_999}}},
        generated_at=0.0, received_at=0.1, ttl_s=10.0, hops=1))
    k._candidate_resource_map = lambda *args, **kwargs: {
        "E": {"status": "ok", "peer": 1, "egress_direction": "W",
              "egress_peer": 0, "egress_generation": 9,
              "isl_rate_bps": 5_000_000.0,
              "candidate_isl_rate_bps": 5_000_000.0,
              "propagation_s": 0.01, "remaining_prop_s": 0.02}}

    pkt = SimpleNamespace(bits=8000, dst=B, path=[])
    snap = k._build_ta_snapshot(
        pkt, 0, 0.2, ["E"], {"E": 0.0}, "candidate", None, dst=B)
    assert snap.history == ()
    scored = ta.score_snapshot_at(snap, snap.snapshot_at + 0.1)
    candidate = scored.by_direction()["E"]
    assert candidate.fallback is True
    assert "no_received_history" in candidate.missing
    assert candidate.total_s == float("inf")


def test_advertised_resource_identity_and_workahead_are_charged():
    geo = _geo()
    cfg = make_cfg({
        "scenario": {"duration_s": 4.0, "num_satellites": 2,
                     "num_planes": 1},
        "control_plane": {"enabled": True,
                          "advertisement_protocol_version": 2, "vis_k": 2,
                          "ttl_s": 10.0, "packet_bits": 8000},
    })
    k = kernel.Kernel(cfg, [row(92, 0.0, A, B, bits=8000)], geometry=geo)
    link = k.isls[0]["E"]
    link.data_bits = 30_000
    link.ctrl_bits = 10_000
    link.current = SimpleNamespace(bits=100_000)
    link._svc_phase = "transmitting"
    link._tx_started_at = 0.0
    # The service began at 500 Mbps; the current request-time MCS estimate
    # below is 1 Gbps.  Residual work must use the actual service-start rate.
    link._service_rate_bps = 500_000_000.0

    # After 20 us, 10,000 bits were served at the sampled 500 Mbps rate.
    state = k._isl_resource_state_advertisement(link, 0.00002)
    k._advertise(0)
    child = next(iter(k.control_children[0][0]))
    packet = k.isls[0][child].ctrl_q[0]
    record = packet.payload["isl_queue_bits"]["E"]

    assert state["generation"] == link.gen
    assert state["work_ahead_bits_proxy"] == pytest.approx(130_000.0)
    # _advertise is evaluated at env.now=0 in this fixture, so the active
    # packet still has its full 100,000 bits plus 40,000 queued bits.
    assert record["work_ahead_bits_proxy"] == pytest.approx(140_000.0)
    assert record["rate_bps"] == 1_000_000_000.0
    # Three fixed-width 64-bit telemetry values per advertised direction:
    # generation, resource rate, and work-ahead proxy.
    expected_bits = (8000 + 3 * 64 * len(k.topo[0]) + 416
                     + 200 * len(packet.payload["downlink_resources"]))
    assert packet.bits == expected_bits
    assert k.ctrl_ledger._offered[packet.iid] == expected_bits


def test_a_constant_state_makes_every_arm_choose_the_same_action():
    chosen = {}
    for arm in ta.ARMS:
        _res, sink, _tl = _run(_cfg({"arm": arm}))
        forwards = _forward_rows(sink)
        chosen[arm] = [r["chosen"] for r in forwards]
    assert len(set(tuple(v) for v in chosen.values())) == 1, chosen


def test_the_audit_never_mentions_a_truth_source():
    _res, sink, _tl = _run(_cfg())
    for r in _forward_rows(sink):
        blob = json.dumps(r["observation_at_start"]["time_alignment"],
                          default=str)
        for forbidden in ("truth_at_commit", "candidate_truth",
                          "deliveries", "futures"):
            assert forbidden not in blob


def test_fixed_horizon_config_is_honoured_in_the_audit():
    _res, sink, _tl = _run(_cfg({"common_rule": "fixed_horizon",
                                 "common_horizon_s": 1.0, "arm": "common"}))
    for r in _forward_rows(sink):
        audit = r["observation_at_start"]["time_alignment"]
        assert audit["common_horizon_s"] == 1.0


def test_unknown_arm_is_rejected_by_the_config():
    with pytest.raises(config.ConfigError):
        _cfg({"arm": "oracle"})
