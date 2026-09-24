"""Integration tests for D1 distance-dependent MCS rates."""
from __future__ import annotations

import pytest

from CODE.leo_sim import config, kernel, link_budget
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)


def test_module_goldens_match_characterization():
    goldens = {
        1000e3: 500e6 * 3.620536,
        2000e3: 500e6 * 1.972253,
        4000e3: 500e6 * 0.889135,
        6000e3: 0.0,
    }
    for d_m, expected in goldens.items():
        assert link_budget.mcs_rate_bps(
            d_m / 1000.0, link_budget.LEGACY_ISL_RF) == pytest.approx(
            expected, rel=1e-9), d_m


def test_graded_rf_sets_differ():
    d = 1000.0
    up = link_budget.mcs_rate_bps(d, link_budget.LEGACY_UPLINK_RF)
    down = link_budget.mcs_rate_bps(d, link_budget.LEGACY_DOWNLINK_RF)
    isl = link_budget.mcs_rate_bps(d, link_budget.LEGACY_ISL_RF)
    # pinned from the legacy RF trios (computed 2026-08-19)
    assert up == pytest.approx(2.796581e9, rel=1e-9)
    assert down == pytest.approx(1.4281155e9, rel=1e-9)
    assert isl == pytest.approx(1.810268e9, rel=1e-9)
    assert up > isl > down


def test_max_rate_range_matches_zero_rate():
    for rf in (link_budget.LEGACY_ISL_RF, link_budget.LEGACY_UPLINK_RF,
               link_budget.LEGACY_DOWNLINK_RF):
        lim = link_budget.max_rate_range_km(rf)
        assert link_budget.mcs_rate_bps(lim - 1e-3, rf) > 0
        assert link_budget.mcs_rate_bps(lim + 1e-3, rf) == 0.0


def test_config_mcs_requires_valid_rf():
    cfg = config.resolve_config({"links": {"rate_model": "mcs"}})
    assert cfg["config"]["links"]["rate_model"] == "mcs"
    with pytest.raises(config.ConfigError, match="min_rate_bps"):
        config.resolve_config({
            "links": {
                "rate_model": "mcs",
                "rf_isl": {"min_rate_bps": 5e8},
            }})
    with pytest.raises(config.ConfigError, match="rf_isl"):
        config.resolve_config({
            "links": {
                "rf_isl": {"not_a_key": 1},
            }})


def test_constant_rate_ignores_rf_numeric_semantics():
    """R7-D1-F2: inactive RF values are not operational inputs in constant
    mode.  Unknown-key checks remain global config hygiene, while numeric RF
    validation belongs only to the MCS mode that consumes those values.
    """
    resolved = config.resolve_config({
        "scenario": {"num_satellites": 1, "num_planes": 1},
        "links": {
            "rate_model": "constant",
            "mcs_table": "unused-in-constant-mode",
            "rf_isl": {"frequency_hz": 0},
        },
    })
    assert resolved["config"]["links"]["rate_model"] == "constant"
    # The ignore contract covers the runtime constructor too, not only config
    # parsing: no MCS table or RF-derived threshold may be evaluated.
    kernel.Kernel(resolved, [], geometry=StaticGeometry(1))


class _Scripted(StaticGeometry):
    """StaticGeometry + scripted range crossing for rate recovery."""

    def __init__(self, num_satellites, *, isl_range_fn=None,
                 slant_range_fn=None, **kw):
        super().__init__(num_satellites, **kw)
        self._isl_range_fn = isl_range_fn or (lambda a, b, t: self.isl_km)
        self._slant_range_fn = slant_range_fn or (
            lambda s, lat, lon, t: self.slant_km)
        self._step = 0.01

    def isl_range_km(self, a, b, t):
        return float(self._isl_range_fn(a, b, t))

    def slant_range_km(self, s, lat, lon, t):
        return float(self._slant_range_fn(s, lat, lon, t))

    def next_isl_range_under(self, a, b, threshold, t, limit):
        prev = self.isl_range_km(a, b, t)
        x = t + self._step
        while x <= limit + 1e-12:
            v = self.isl_range_km(a, b, x)
            if v <= threshold and prev > threshold:
                return x
            prev = v
            x += self._step
        return None

    def next_slant_range_under(self, s, lat, lon, threshold, t, limit):
        prev = self.slant_range_km(s, lat, lon, t)
        x = t + self._step
        while x <= limit + 1e-12:
            v = self.slant_range_km(s, lat, lon, x)
            if v <= threshold and prev > threshold:
                return x
            prev = v
            x += self._step
        return None


def test_mcs_service_waits_for_rate_recovery_then_delivers():
    topo = {0: {"E": 1}, 1: {"W": 0}}

    def ranges(a, b, t):
        return 5900.0 if t < 1.0 else 1000.0

    geo = _Scripted(
        2, neighbors_map=topo, isl_range_fn=ranges,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "links": {"rate_model": "mcs", "isl_rate_mbps": 1000.0},
    })
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["mechanisms"]["effective"]["mcs"] is True
    assert res["mechanism_counters"]["mcs_rate_samples"] > 0


def test_mcs_zero_rate_waits_then_expires_at_deadline():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        isl_range_fn=lambda a, b, t: 5900.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, deadline=1.0)], geometry=geo)
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"
    # the ISL service never started: zero-rate gate held until the deadline
    assert res["occupied"]["isl_s"] == 0.0
    assert res["mechanism_counters"]["mcs_rate_samples"] > 0


def test_info_physical_zero_rate_keeps_waiting_semantics():
    """F1 filtering must not turn a temporary zero-rate link into NO_ROUTE."""
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        isl_range_fn=lambda a, b, t: 5900.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "links": {"rate_model": "mcs"},
        "control_plane": {"enabled": True},
        "routing": {"policy": "info_physical"},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, deadline=1.0)], geometry=geo)
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"
    assert res["fates"][1] != "NO_ROUTE"


def test_mcs_zero_rate_head_does_not_bypass_endpoint_fifo():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        isl_range_fn=lambda a, b, t: 5900.0 if t < 1.0 else 1000.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, bits=1_000_000),
              row(2, 0.0, A, B, bits=1_000_000)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["fates"][2] == "DELIVERED"
    assert [pid for _cell, pid in res["service_log"]["uplink"]] == [1, 2]


def test_mcs_zero_rate_isl_head_stays_queued_until_recovery():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        isl_range_fn=lambda a, b, t: 5900.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    link = k.isls[0]["E"]
    link.put_data(pkt)

    # Let the ISL server attempt its first scheduling step.  A zero MCS
    # rate is not a service start and must not dequeue/head-of-line block the
    # packet while waiting for rate recovery.
    for _ in range(6):
        k.env.step()
    assert link.data_q and link.data_q[0] is pkt
    assert link.data_bits == pkt.bits
    assert link._svc is None


def test_mcs_gsl_down_does_not_dequeue_shared_uplink_head():
    """A GSL outage must not pin the shared server on a dequeued packet."""
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: t >= 1.0 and (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "access": {"acquisition_delay_s": 0.0},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [row(99, 2.0, A, B)], geometry=geo)
    # lazy endpoint activation (#28): materialize A and give it an ACTIVE
    # association with sat 0 before the geometry drops, so the outage gate
    # (not a missing link) is the only reason _pick may refuse the head
    ep = k._ensure_endpoint(A)
    ep.links[0] = kernel.Link(0, "active", k.env.now, interrupt=k.env.event())
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    ep.queue.append(pkt)
    ep.queued_bits += pkt.bits
    ep.area.add(pkt.bits, k.env.now)
    k._poke(k.uplinks[0].wake)
    k.env.step()
    assert [p.pid for p in ep.queue] == [1]
    assert ep.queued_bits > 0
    assert k.uplinks[0].current is None


def test_mcs_gsl_ge_down_does_not_dequeue_shared_downlink_head():
    """A GSL GE outage must be handled before downlink dequeue as well."""
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: True,
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "access": {"acquisition_delay_s": 0.0},
        "links": {"rate_model": "mcs", "ge_enabled": True,
                   "ge_gsl": {"mean_good_s": 0.001,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [row(99, 2.0, A, B)], geometry=geo)
    # lazy endpoint activation (#28): _servable resolves the destination
    # endpoint, so materialize B and give it an ACTIVE association with sat 1
    # before flipping GE bad -- the GE gate (not a missing link) must be the
    # reason _servable refuses the head
    ep = k._ensure_endpoint(B)
    ep.links[1] = kernel.Link(1, "active", k.env.now, interrupt=k.env.event())
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    k.downlinks[1].put(pkt)
    ge = k._gsl_ge(1, B)
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    assert k.downlinks[1]._servable(B) is None
    assert [p.pid for p in k.downlinks[1].queues[B] ] == [1]
    assert k.downlinks[1].current is None


def test_mcs_zero_rate_downlink_waits_then_delivers_after_recovery():
    """Real GSL scenario: destination visible but beyond MCS range.

    The downlink slant range starts at 5900 km (zero feasible MCS rate for
    the legacy downlink RF, max range ~4482 km) and closes to 600 km at
    t=1.0.  The packet must wait — never be failed — and only be delivered
    once a positive rate exists.
    """
    assert link_budget.mcs_rate_bps(
        5900.0, link_budget.LEGACY_DOWNLINK_RF) == 0.0

    def slant(s, lat, lon, t):
        if (lat, lon) == BC:
            return 5900.0 if t < 1.0 else 600.0
        return 600.0

    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_range_fn=slant)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["deliveries"][1]["delivered_at"] >= 1.0
    assert res["mechanism_counters"]["mcs_rate_samples"] > 0


def test_mcs_zero_rate_downlink_is_not_a_legal_deliver_action():
    """Decision-level mask: zero downlink rate must not enqueue for deliver.

    Consistent with the ISL legal mask (zero-rate directions are excluded),
    a zero-rate downlink must park the packet in ``pending`` for re-decision
    instead of putting it into a downlink queue that cannot be served.
    """
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_range_fn=lambda s, lat, lon, t: (
            5900.0 if (lat, lon) == BC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [row(99, 1.5, A, B)], geometry=geo)
    # lazy endpoint activation (#28): materialize B up front so its
    # association ticker can run and give it an active link to sat 0
    k._ensure_endpoint(B)
    for _ in range(6):
        k.env.step()
    assert k.endpoints[B].links[0].state == "active"

    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    k._decide(pkt, 0)
    assert k.downlinks[0].queued_bits == 0
    assert list(k.pending[0]) == [pkt]


def test_constant_rate_default_is_unaffected():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        isl_range_fn=lambda a, b, t: 5900.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)))
    cfg = make_cfg({"scenario": {"duration_s": 2.0}})
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["mechanisms"]["effective"]["mcs"] is False


def test_mcs_zero_rate_uplink_recovers_at_certified_time_not_next_tick():
    """F1: a zero-rate GSL must wake at the certified recovery instant.

    Uplink slant starts at 12500 km (beyond the uplink MCS max range of
    ~12068 km, so rate is exactly zero) and closes to 600 km at t=1.0.
    With time_step=0.7 the blind ticks land at 0.7 and 1.4; polling would
    start the service at 1.4, the certified recovery is 1.0.
    """
    assert link_budget.mcs_rate_bps(
        12500.0, link_budget.LEGACY_UPLINK_RF) == 0.0

    def slant(s, lat, lon, t):
        if (lat, lon) == AC:
            return 12500.0 if t < 1.0 else 600.0
        return 600.0

    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_range_fn=slant)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "time_step_s": 0.7,
                     "num_satellites": 1},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    # service started at the certified recovery (1.0 + tx + prop), not at
    # the next blind tick 1.4
    assert res["deliveries"][1]["delivered_at"] < 1.2


def test_mcs_zero_rate_isl_recovers_at_certified_time_not_next_tick():
    """R7-D1-F4: ISL recovery is scheduled at the certified range crossing,
    even when that instant is not a simulation tick."""
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        isl_range_fn=lambda a, b, t: 5900.0 if t < 1.0 else 1000.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "time_step_s": 0.7},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["deliveries"][1]["delivered_at"] < 1.2


def test_mcs_zero_rate_deadline_before_horizon_expires_not_in_system():
    """F1: a deadline between the last tick and the horizon must still expire.

    The downlink is beyond MCS range for the whole run, so the packet parks
    behind the zero-rate gate.  Its deadline (0.95) falls after the last
    ticker pass (0.9) but before the horizon (0.96): stop-close must settle
    it as DATA_DEADLINE_EXPIRED, not IN_SYSTEM_AT_STOP.
    """
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_range_fn=lambda s, lat, lon, t: (
            5900.0 if (lat, lon) == BC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 0.96, "num_satellites": 1},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, deadline=0.95)], geometry=geo)
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"


def test_mcs_zero_rate_isl_wait_expires_each_packet_at_its_own_deadline():
    """F2: the ISL zero-rate wait must race wake and all queued expiries.

    p1 (no deadline) holds the zero-rate ISL probe; p2 arrives at t=0.2
    with deadline 0.5.  p2 must expire at its own deadline (queue area
    0.3e6 bit*s), not ride until the horizon (1.8e6 bit*s).
    """
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        isl_range_fn=lambda a, b, t: 5900.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    p1 = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    k.ledger.register(p1.pid, p1.bits)
    k.isls[0]["E"].put_data(p1)
    while k.env.now < 0.2:
        k.env.step()
    p2 = kernel.DataPacket(2, A, B, 1_000_000, 0.5, 0.2)
    k.ledger.register(p2.pid, p2.bits)
    k.isls[0]["E"].put_data(p2)

    res = k.run()
    assert res["natural_end"]
    assert res["fates"][2] == "DATA_DEADLINE_EXPIRED"
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    # p1 waits 0->2.0 (2.0e6 bit*s); p2 waits 0.2->0.5 (0.3e6 bit*s)
    assert res["queue_area_bits_s"]["isl_data"] == pytest.approx(
        2.3e6, rel=1e-9)


def test_mcs_all_zero_rate_run_marks_mechanism_effective():
    """F5: zero-rate gating IS the MCS mechanism affecting scheduling.

    A run whose links are all beyond MCS range the whole time must report
    effective.mcs=True (packets were held by the gate) even though no
    transmission was ever paced (mcs_rate_samples == 0).
    """
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_range_fn=lambda s, lat, lon, t: (
            5900.0 if (lat, lon) == BC else 12500.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 0.96, "num_satellites": 1},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, deadline=0.95)], geometry=geo)
    counters = res["mechanism_counters"]
    assert counters["mcs_rate_samples"] == 0
    assert counters["mcs_zero_rate_holds"] > 0
    assert res["mechanisms"]["effective"]["mcs"] is True


def test_mcs_receipt_records_sampled_rate_range():
    """F5/R7-D1-F4: sampled rates are attributed, and every real service
    duration is exactly ``bits / sampled_rate`` for its fixed slant range."""
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        isl_range_fn=lambda a, b, t: 1000.0,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    counters = res["mechanism_counters"]
    # samples: uplink@600km ~2.95e9, isl@1000km ~1.81e9, downlink@600 ~2.10e9
    assert 1.8e9 < counters["mcs_rate_min_bps"] < 1.82e9
    assert 2.9e9 < counters["mcs_rate_max_bps"] < 3.0e9
    bits = 8_000_000
    assert res["occupied"]["gsl_uplink_s"] == pytest.approx(
        bits / link_budget.mcs_rate_bps(
            600.0, link_budget.LEGACY_UPLINK_RF), rel=1e-9)
    assert res["occupied"]["isl_s"] == pytest.approx(
        bits / link_budget.mcs_rate_bps(
            1000.0, link_budget.LEGACY_ISL_RF), rel=1e-9)
    assert res["occupied"]["gsl_downlink_s"] == pytest.approx(
        bits / link_budget.mcs_rate_bps(
            600.0, link_budget.LEGACY_DOWNLINK_RF), rel=1e-9)


def test_mcs_retiring_uplink_gated_head_stays_queued_and_server_free():
    """D1 round-3 F1: a retiring uplink must NOT bypass the availability
    gates.  A head assigned to the retiring sat stays queued while its GSL
    is geometrically unavailable, and the shared server is free to serve a
    second endpoint whose link to the same sat is available."""
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        # A-sat0 geometry is DOWN until t=1.0; B-sat0 and B-sat1 stay up
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC and t >= 1.0)
            or (s == 0 and (lat, lon) == BC)
            or (s == 1 and (lat, lon) == BC)),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "access": {"acquisition_delay_s": 0.0},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    epA = k._ensure_endpoint(A)
    # retiring link to sat 0 with a far-future hard deadline: it is still
    # draining, so _pick must apply the same gates as for an active link
    epA.links[0] = kernel.Link(0, "retiring", k.env.now, retire_at=100.0,
                               cause="test", interrupt=k.env.event())
    pktA = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pktA.assigned_sat = 0
    epA.queue.append(pktA)
    epA.queued_bits += pktA.bits
    epA.area.add(pktA.bits, k.env.now)

    epB = k._ensure_endpoint(B)
    epB.links[0] = kernel.Link(0, "active", k.env.now,
                               interrupt=k.env.event())
    pktB = kernel.DataPacket(2, B, A, 1_000_000, None, 0.0)
    pktB.assigned_sat = 0
    k.ledger.register(pktB.pid, pktB.bits)
    epB.queue.append(pktB)
    epB.queued_bits += pktB.bits
    epB.area.add(pktB.bits, k.env.now)

    k._poke(k.uplinks[0].wake)
    k.env.step()

    # A's gated head must NOT be dequeued/pin the server
    assert [p.pid for p in epA.queue] == [1]
    assert epA.queued_bits == pktA.bits
    # the server must be serving B (or have already served B), never pinned
    # on A's unservable retiring link
    cur = k.uplinks[0].current
    served = [pid for _cell, pid in k.service_log["uplink"]]
    assert 2 in served or (cur is not None and cur[0] is epB), (
        "server was pinned on the retiring gated head")
    assert 1 not in served


def test_mcs_deadline_wake_expires_exactly_at_deadline():
    """D1 round-3 F2-A: when the GSL certified wait wakes the server exactly
    at a packet's deadline, the packet must expire at the deadline, not one
    time_step later.  time_step=0.7 makes 1.0 a non-tick instant, so a strict
    (>) sweep would only catch the packet at 1.4."""
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)),
        # uplink slant always beyond the MCS max range: zero rate forever
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if (lat, lon) == AC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "time_step_s": 0.7},
        "links": {"rate_model": "mcs"},
        "execution": {"monitor": True},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, deadline=1.0)], geometry=geo)
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"
    fate_t = [t for t, kind, kv in res["monitor_log"]
              if kind == "fate" and dict(kv).get("pid") == 1]
    assert fate_t, "missing fate monitor event"
    assert fate_t[0] == pytest.approx(1.0, abs=1e-9), (
        "packet expired at %s, not exactly at its deadline 1.0" % fate_t[0])


def test_mcs_uplink_deadline_sweep_not_skipped_by_same_time_wake():
    """D1 round-4 F2-RACE: a poke landing on the same timestamp as a
    certified deadline must not skip the deadline sweep.  The zero-rate
    uplink head with deadline 1.0 must expire at exactly 1.0 even though
    another endpoint pokes the shared server at t=1.0."""
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)),
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if (lat, lon) == AC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "time_step_s": 0.7},
        "links": {"rate_model": "mcs"},
        "execution": {"monitor": True},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    epA = k._ensure_endpoint(A)
    epA.links[0] = kernel.Link(0, "active", k.env.now,
                               interrupt=k.env.event())
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pkt.deadline = 1.0
    pkt.assigned_sat = 0
    k.ledger.register(pkt.pid, pkt.bits)
    epA.queue.append(pkt)
    epA.queued_bits += pkt.bits
    epA.area.add(pkt.bits, k.env.now)
    # a second endpoint on the SAME shared server whose only role here is to
    # poke the server at exactly the deadline instant
    epB = k._ensure_endpoint(B)
    epB.links[0] = kernel.Link(0, "active", k.env.now,
                               interrupt=k.env.event())

    def _poke_at_deadline():
        yield k.env.timeout(1.0)
        k._poke(k.uplinks[0].wake)
    k.env.process(_poke_at_deadline())
    k._poke(k.uplinks[0].wake)
    k.env.run(until=2.0)

    fates = [t for t, kind, kv in k.monitor_log
             if kind == "fate" and dict(kv).get("pid") == 1]
    assert fates, "missing fate monitor event"
    assert fates[0] == pytest.approx(1.0, abs=1e-9), (
        "packet expired at %s, not exactly at its deadline 1.0" % fates[0])


def test_mcs_pending_deadline_equal_horizon_expires():
    """D1 round-4 F3: a packet parked in pending whose deadline EQUALS the
    horizon must expire (inclusive), consistent with the endpoint/downlink/
    ISL inclusive deadline semantics, not settle as IN_SYSTEM_AT_STOP."""
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_range_fn=lambda s, lat, lon, t: (
            5900.0 if (lat, lon) == BC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 1.0, "num_satellites": 1},
        "links": {"rate_model": "mcs"},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, deadline=1.0)], geometry=geo)
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"


def test_mcs_ge_gated_head_zero_rate_not_attributed_to_mcs():
    """D1 round-4 F4: when GE (not MCS) is the actual blocking gate, a
    coincident zero MCS rate must not be counted as an MCS hold.  The head
    is GE-down for the whole run AND beyond MCS range: the deferral belongs
    to GE, so mcs_zero_rate_holds must stay 0 and the head must stay
    queued."""
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) == AC,
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if (lat, lon) == AC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1},
        "links": {"rate_model": "mcs", "ge_enabled": True,
                   "ge_gsl": {"mean_good_s": 0.001,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    ep = k._ensure_endpoint(A)
    ep.links[0] = kernel.Link(0, "active", k.env.now,
                              interrupt=k.env.event())
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pkt.assigned_sat = 0
    k.ledger.register(pkt.pid, pkt.bits)
    ep.queue.append(pkt)
    ep.queued_bits += pkt.bits
    ep.area.add(pkt.bits, k.env.now)
    ge = k._gsl_ge(0, A)
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    k._poke(k.uplinks[0].wake)
    k.env.run(until=2.0)
    assert k.mech["mcs_zero_rate_holds"] == 0
    assert [p.pid for p in ep.queue] == [1]
    assert k.uplinks[0].current is None


def test_mcs_ge_gated_deliver_decision_not_attributed_to_mcs():
    """D1 round-5 F4-DECIDE: the _decide() deliver branch must not attribute
    a zero MCS rate as an MCS hold when the GSL GE is the actual blocker.
    The GE-gated uplink regression covers the queue/server path only; this
    covers the decision-level path (GE down + beyond-MCS-range downlink)."""
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) == BC,
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if (lat, lon) == BC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1},
        "links": {"rate_model": "mcs", "ge_enabled": True,
                   "ge_gsl": {"mean_good_s": 0.001,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    ep = k._ensure_endpoint(B)
    ep.links[0] = kernel.Link(0, "active", k.env.now,
                              interrupt=k.env.event())
    ge = k._gsl_ge(0, B)
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    k.ledger.register(pkt.pid, pkt.bits)
    k._decide(pkt, 0)
    assert k.mech["mcs_zero_rate_holds"] == 0
    assert k.pending[0] == [pkt]

def test_mcs_ge_gated_isl_candidate_decision_not_attributed_to_mcs():
    """D1 round-6 F4-DECIDE (ISL lane): the _decide() ISL candidate loop must
    not attribute a zero MCS rate as an MCS hold when the ISL GE is the
    actual blocker.  Geometry up + MCS rate 0 + GE down must park the packet
    in pending without incrementing mcs_zero_rate_holds."""
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        isl_range_fn=lambda a, b, t: 7000.0,  # beyond ISL MCS range: rate 0
        visible=lambda s, lat, lon, t: (s == 1 and (lat, lon) == BC))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 2},
        "links": {"rate_model": "mcs", "ge_enabled": True,
                   "ge_isl": {"mean_good_s": 0.001,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    # sat 1 actively serves B (so the oracle routes via the 0:E ISL); sat 0
    # itself must not serve B, forcing the decision into the ISL candidate
    # loop instead of the deliver branch
    ep = k._ensure_endpoint(B)
    ep.links[1] = kernel.Link(1, "active", k.env.now,
                              interrupt=k.env.event())
    ge = k.isls[0]["E"].ge
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pkt.assigned_sat = 0
    k.ledger.register(pkt.pid, pkt.bits)
    k._decide(pkt, 0)
    assert k.mech["mcs_zero_rate_holds"] == 0
    assert k.pending[0] == [pkt]


def test_mcs_zero_rate_pending_expires_at_exact_deadline_not_tick():
    """D1 round-6 independent finding 1: the normal _decide() zero-rate path
    parks the packet in pending; the certified pending wake must expire it at
    the exact deadline (0.5), not at the next blind time_step tick (0.7)."""
    import math
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) == BC,
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if (lat, lon) == BC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 1.0, "num_satellites": 1,
                     "time_step_s": 0.7},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    ep = k._ensure_endpoint(B)
    ep.links[0] = kernel.Link(0, "active", k.env.now,
                              interrupt=k.env.event())
    pkt = kernel.DataPacket(1, A, B, 1_000_000, 0.5, 0.0)
    k.ledger.register(pkt.pid, pkt.bits)
    k._decide(pkt, 0)
    assert k.pending[0] == [pkt]
    assert k.mech["mcs_zero_rate_holds"] == 1

    while True:
        t_next = k.env.peek()
        if t_next > k.horizon or t_next == math.inf:
            break
        k.env.step()
        if k.ledger.fate_of(pkt.pid) is not None:
            assert k.env.now == pytest.approx(0.5, abs=1e-9), (
                f"packet expired at {k.env.now}, not exactly at deadline 0.5")
            return
    raise AssertionError("packet never expired before horizon")


def test_mcs_zero_rate_pending_recovers_at_exact_range_crossing_not_tick():
    """D1 round-6 independent finding 1: the certified pending wake must
    re-decide a parked zero-rate packet at the exact range recovery (t=1.0),
    not at the next time_step tick (t=1.4)."""
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) == BC,
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if t < 1.0
            else (600.0 if (lat, lon) == BC else 600.0)))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1,
                     "time_step_s": 0.7},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    ep = k._ensure_endpoint(B)
    ep.links[0] = kernel.Link(0, "active", k.env.now,
                              interrupt=k.env.event())
    pkt = kernel.DataPacket(1, A, B, 8_000, 10.0, 0.0)
    k.ledger.register(pkt.pid, pkt.bits)
    k._decide(pkt, 0)
    assert k.pending[0] == [pkt]

    # step until the packet leaves pending or horizon; the certified wake at
    # t=1.0 must re-decide it into the downlink queue well before the t=1.4
    # time_step tick
    import math
    redecided_at = None
    while True:
        t_next = k.env.peek()
        if t_next > k.horizon or t_next == math.inf:
            break
        k.env.step()
        if not k.pending[0] and k.ledger.fate_of(pkt.pid) is None:
            # left pending -> downlink queue or in service
            redecided_at = k.env.now
            break
    assert redecided_at is not None, "packet never left pending"
    assert redecided_at == pytest.approx(1.0, abs=1e-9), (
        f"re-decided at {redecided_at}, not at the certified recovery 1.0")


def test_mcs_ge_gated_deliver_decision_counts_ge_query():
    """D1 round-6 independent finding 2: the _decide() GSL GE attribution
    query must be counted so receipt effective.ge is not a false negative
    when GE (not MCS) is the actual blocker."""
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) == BC,
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if (lat, lon) == BC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1},
        "links": {"rate_model": "mcs", "ge_enabled": True,
                   "ge_gsl": {"mean_good_s": 0.001,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    ep = k._ensure_endpoint(B)
    ep.links[0] = kernel.Link(0, "active", k.env.now,
                              interrupt=k.env.event())
    ge = k._gsl_ge(0, B)
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    k.ledger.register(pkt.pid, pkt.bits)
    k._decide(pkt, 0)
    assert k.mech["mcs_zero_rate_holds"] == 0
    assert k.pending[0] == [pkt]
    assert k.mech["ge_gsl_queries"] > 0, (
        "GE attribution query must be counted for receipt effective.ge")


def test_isl_ge_down_pre_gate_counts_query_without_transmit():
    """D1 round-6 independent finding 2: ISLLink.available_now() queries GE
    in the pre-gate; a GE-down link that never reaches _transmit() must still
    be counted so receipt effective.ge reflects the actual blocker."""
    topo = {0: {"E": 1}, 1: {"W": 0}}
    geo = _Scripted(
        2, neighbors_map=topo,
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)))
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 2},
        "links": {"rate_model": "mcs", "ge_enabled": True,
                   "ge_isl": {"mean_good_s": 0.001,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    link = k.isls[0]["E"]
    ge = link.ge
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    pkt = kernel.DataPacket(1, A, B, 8_000, 10.0, 0.0)
    k.ledger.register(pkt.pid, pkt.bits)
    link.put_data(pkt)
    k.env.run(until=2.0)
    assert k.mech["ge_isl_queries"] > 0
    assert k.ledger.fate_of(pkt.pid) is None  # still queued, not served
    assert link._svc is None



def test_mcs_downlink_tail_deadline_wakes_server_at_exact_time():
    """D1 round-5 independent finding 2: the downlink MCS wait must include
    EVERY queued packet's deadline, not just the head's.  A tail packet with
    an earlier deadline must expire at the exact deadline, not at the next
    blind time_step tick."""
    import math
    from collections import deque

    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) == BC,
        slant_range_fn=lambda s, lat, lon, t: (
            12500.0 if (lat, lon) == BC else 600.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 1.0, "num_satellites": 1,
                     "time_step_s": 0.7},
        "links": {"rate_model": "mcs"},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    ep = k._ensure_endpoint(B)
    ep.links[0] = kernel.Link(0, "active", k.env.now,
                              interrupt=k.env.event())
    p_head = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    p_tail = kernel.DataPacket(2, A, B, 1_000_000, 0.5, 0.0)
    for p in (p_head, p_tail):
        k.ledger.register(p.pid, p.bits)
        p.assigned_sat = 0
    dl = k.downlinks[0]
    dl.queues.setdefault(B, deque([p_head, p_tail]))
    dl.queued_bits += p_head.bits + p_tail.bits
    dl.area.add(p_head.bits, 0.0)
    dl.area.add(p_tail.bits, 0.0)
    k._poke(dl.wake)  # bypassed put(); wake the sleeping server

    expiry_t = None
    while True:
        t_next = k.env.peek()
        if t_next > k.horizon or t_next == math.inf:
            break
        k.env.step()
        if k.ledger._fates.get(2) == "DATA_DEADLINE_EXPIRED":
            expiry_t = k.env.now
            break
    assert expiry_t == pytest.approx(0.5, abs=1e-9)
    # the no-deadline head stays queued behind the zero MCS rate
    assert [p.pid for p in dl.queues.get(B, ())] == [1]


def test_constant_rate_uplink_ge_gated_head_does_not_pin_shared_server():
    """D1 round-4 F5: constant-rate shared GSL must pre-gate a GE-down head
    exactly like MCS, so the shared server can serve another endpoint.  A is
    GE-down, B is servable on the same satellite: B must be served while A
    stays queued."""
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1},
        "access": {"acquisition_delay_s": 0.0},
        "links": {"rate_model": "constant", "ge_enabled": True,
                   "ge_gsl": {"mean_good_s": 1000.0,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    epA = k._ensure_endpoint(A)
    epA.links[0] = kernel.Link(0, "active", k.env.now,
                               interrupt=k.env.event())
    pktA = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pktA.assigned_sat = 0
    k.ledger.register(pktA.pid, pktA.bits)
    epA.queue.append(pktA)
    epA.queued_bits += pktA.bits
    epA.area.add(pktA.bits, k.env.now)
    geA = k._gsl_ge(0, A)
    geA._bad = True
    geA._last_t = 0.0
    geA._next_flip = float("inf")

    epB = k._ensure_endpoint(B)
    epB.links[0] = kernel.Link(0, "active", k.env.now,
                               interrupt=k.env.event())
    pktB = kernel.DataPacket(2, B, A, 1_000_000, None, 0.0)
    pktB.assigned_sat = 0
    epB.queue.append(pktB)
    epB.queued_bits += pktB.bits
    epB.area.add(pktB.bits, k.env.now)

    k._poke(k.uplinks[0].wake)
    k.env.run(until=2.0)
    assert [p.pid for p in epA.queue] == [1]
    served = [pid for _cell, pid in k.service_log["uplink"]]
    assert 2 in served, "servable endpoint B was never served"
    assert 1 not in served, "GE-gated head A was dequeued"


def test_constant_rate_downlink_ge_gated_head_does_not_pin_shared_server():
    """D1 round-4 F5: constant-rate downlink must also pre-gate a GE-down
    head so the shared server can serve another endpoint."""
    geo = _Scripted(
        1, neighbors_map={0: {}},
        visible=lambda s, lat, lon, t: (lat, lon) in (AC, BC),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1},
        "access": {"acquisition_delay_s": 0.0},
        "links": {"rate_model": "constant", "ge_enabled": True,
                   "ge_gsl": {"mean_good_s": 1000.0,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    epA = k._ensure_endpoint(A)
    epA.links[0] = kernel.Link(0, "active", k.env.now,
                               interrupt=k.env.event())
    epB = k._ensure_endpoint(B)
    epB.links[0] = kernel.Link(0, "active", k.env.now,
                               interrupt=k.env.event())
    pktA = kernel.DataPacket(1, B, A, 1_000_000, None, 0.0)
    pktB = kernel.DataPacket(2, A, B, 1_000_000, None, 0.0)
    k.ledger.register(pktA.pid, pktA.bits)
    k.ledger.register(pktB.pid, pktB.bits)
    k.downlinks[0].put(pktA)
    k.downlinks[0].put(pktB)
    geA = k._gsl_ge(0, A)
    geA._bad = True
    geA._last_t = 0.0
    geA._next_flip = float("inf")
    k.env.run(until=2.0)
    assert [p.pid for p in k.downlinks[0].queues[A]] == [1]
    served = [pid for _cell, pid in k.service_log["downlink"]]
    assert 2 in served, "servable endpoint B was never served"
    assert 1 not in served, "GE-gated head A was dequeued"


def test_mcs_gated_head_hard_retirement_released_at_instant_not_next_tick():
    """D1 round-5 F1-R: a pre-dequeue-gated head (never in _transmit) must
    still be released at the hard retirement deadline via the interrupt, not
    deferred to the next _evaluate_handover tick.  time_step=0.7 makes
    retire_at=1.0 a non-tick instant; the release must happen at 1.0 and the
    packet must be unassigned so a successor can serve it."""
    geo = _Scripted(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == AC)
            or (s == 1 and (lat, lon) == BC)),
        slant_km=600.0)
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "time_step_s": 0.7},
        "access": {"acquisition_delay_s": 0.0},
        "links": {"rate_model": "mcs", "ge_enabled": True,
                   "ge_gsl": {"mean_good_s": 1000.0,
                               "mean_bad_s": 1000.0}},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    ep = k._ensure_endpoint(A)
    k._associate(ep, 0, k.env.now)
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pkt.deadline = 1.5  # after retire_at: expiry must not preempt the release
    pkt.assigned_sat = 0
    k.ledger.register(pkt.pid, pkt.bits)
    ep.queue.append(pkt)
    ep.queued_bits += pkt.bits
    ep.area.add(pkt.bits, k.env.now)
    ge = k._gsl_ge(0, A)
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    link = ep.links[0]
    link.state = "retiring"
    link.cause = "lease"
    link.retire_at = 1.0
    k.env.process(k._fire_interrupt(link, 1.0))
    k._poke(k.uplinks[0].wake)
    k.env.run(until=2.0)
    releases = [e for e in k.handover_events
                if e["type"] == "release"
                and str(e["reason"]).startswith("lease")]
    assert releases, "retiring gated link was never released"
    assert releases[0]["t"] == pytest.approx(1.0, abs=1e-9), (
        "release at %s, not the hard retirement instant 1.0" % releases[0]["t"])
    assert pkt.assigned_sat is None, (
        "gated head still assigned to the retired link")
