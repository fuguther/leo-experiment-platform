"""Behavioral tests for the leo_sim discrete-event kernel.

All tests drive the real kernel through kernel.run_simulation with scripted
StaticGeometry; expected values are computed by hand from the config rates.
"""
import math

import pytest

from CODE.leo_sim import kernel, model  # noqa: F401  (import must exist)
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

PROP_600KM = 600.0 / 299_792.458  # ~0.0020014 s
PROP_1000KM = 1000.0 / 299_792.458

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
C = cell(10.0, 0.0)
NOWHERE = cell(80.0, 170.0)
AC = cell_center(A)   # (0.125, 0.125)
BC = cell_center(B)   # (0.125, 10.125)


def one_sat_visible_all(sat, lat, lon, t):
    return sat == 0


def test_direct_uplink_downlink_delivery():
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["natural_end"] is True
    assert res["fates"][1] == "DELIVERED"
    # hand: 0.08 uplink service + prop + 0.08 downlink service + prop
    expect = 0.08 + PROP_600KM + 0.08 + PROP_600KM
    assert abs(res["deliveries"][1]["delivered_at"] - expect) < 0.02
    assert res["deliveries"][1]["path"] == [0]


def test_isl_forwarding_delivery():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    geo = StaticGeometry(2, neighbors_map=nb, visible=vis)
    res = kernel.run_simulation(make_cfg(), [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["deliveries"][1]["path"] == [0, 1]
    expect = 0.08 + PROP_600KM + 0.008 + PROP_1000KM + 0.08 + PROP_600KM
    assert abs(res["deliveries"][1]["delivered_at"] - expect) < 0.02


def test_access_rejected_when_no_visible_satellite():
    geo = StaticGeometry(2)  # nothing visible
    res = kernel.run_simulation(make_cfg(), [row(1, 0.5, A, B)], geometry=geo)
    assert res["fates"][1] == "ACCESS_REJECTED"


def test_access_queue_overflow():
    cfg = make_cfg({
        "access": {"uplink_rate_mbps": 1.0, "uplink_queue_bits": 8_000_000},
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 40.0},
    })
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    rows = [row(i, 0.0, A, B) for i in (1, 2, 3)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    # 8 s service each: p1 in service, p2 queued (cap=1 packet), p3 overflows
    assert res["fates"][1] == "DELIVERED"
    assert res["fates"][2] == "DELIVERED"
    assert res["fates"][3] == "ACCESS_QUEUE_OVERFLOW"


def test_isl_queue_overflow():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    cfg = make_cfg({"links": {"isl_rate_mbps": 1.0, "isl_queue_bits": 8_000_000},
                    "scenario": {"duration_s": 40.0}})
    geo = StaticGeometry(2, neighbors_map=nb, visible=vis)
    rows = [row(i, 0.0, A, B) for i in (1, 2, 3)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["fates"][2] == "DELIVERED"
    assert res["fates"][3] == "ISL_QUEUE_OVERFLOW"


def test_uplink_fair_scheduling_alternates():
    cfg = make_cfg({
        "access": {"uplink_rate_mbps": 1.0, "downlink_rate_mbps": 1.0},
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 80.0},
    })
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    rows = [row(1, 0.0, A, B), row(2, 0.0, A, B),
            row(3, 0.0, C, B), row(4, 0.0, C, B)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    order = res["service_log"]["uplink"]
    assert len(order) == 4
    cells_served = [c for c, _pid in order]
    for i in range(len(cells_served) - 1):
        assert cells_served[i] != cells_served[i + 1], "DRR must alternate endpoints"
    assert all(res["fates"][p] == "DELIVERED" for p in (1, 2, 3, 4))


def test_geometry_loss_in_flight_accounts_occupied_time():
    vis = lambda s, lat, lon, t: (lat, lon) == BC or t < 0.05
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    geo = StaticGeometry(1, visible=vis, gsl_changes=[0.05])
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    # uplink service spans t in [0, 0.08]; visibility ends at t=0.05, so the
    # in-flight failure is detected at 0.05 and only that part is occupied
    assert res["fates"][1] == "GEOMETRY_LOSS_IN_FLIGHT"
    assert abs(res["occupied"]["gsl_uplink_s"] - 0.05) < 1e-6


def test_future_endpoints_not_prebuilt_and_no_preposition_leak():
    """A cell whose first traffic is in the future must not pre-exist: it
    must not pre-position a K slot, appear in advertisements or inflate
    observation denominators before any demand.  Regression: the kernel used
    to build every trace cell at t=0, so a future-only endpoint could grab
    the only slot before the present traffic arrived."""
    a, b = cell(0.0, 10.0), cell(0.0, 0.0)  # b sorts first (would preposition)
    geo = StaticGeometry(1, neighbors_map={0: {}}, visible=lambda *_: True,
                         gsl_changes=[])
    cfg = make_cfg({
        "scenario": {"duration_s": 8.0, "num_satellites": 1,
                     "num_planes": 1, "time_step_s": 0.1},
        "access": {"slots_per_satellite": 1, "uplink_rate_mbps": 200.0,
                   "downlink_rate_mbps": 200.0, "idle_release_s": 0.2,
                   "min_dwell_s": 0.0, "hysteresis_deg": 0.0,
                   "acquisition_delay_s": 0.0},
    })
    rows = [row(1, 0.0, a, b), row(2, 5.0, b, a)]
    k = kernel.Kernel(cfg, rows, geometry=geo)
    assert len(k.endpoints) == 0  # nothing pre-built from the full trace
    res = k.run()
    # the future cell never pre-positioned the single slot: the present
    # traffic (A) wins the first grant, and the future cell B is only
    # associated after the slot frees (regression: pre-built B grabbed the
    # slot at t=0 and starved A)
    assoc = [e for e in res["handover"]["events"] if e["type"] == "associate"]
    assert assoc[0]["endpoint"] == a
    assert res["access"]["preposition_grants"] == 0


def test_downlink_wakes_on_geometry_recovery_after_temporary_outage():
    """A downlink packet queued during a temporary GSL outage must be served
    when the satellite becomes visible again, even with a coarse time step
    and no further put() poking the server."""
    a, b = cell(0.0, 0.0), cell(0.0, 10.0)
    # sat0 visible on [0, 0.15) and [0.6, inf); GSL down in [0.15, 0.6)
    geo = StaticGeometry(
        2,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: t < 0.15 or t >= 0.6,
        isl_km=1000.0,
        gsl_changes=[0.15, 0.6],
    )
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 2,
                     "num_planes": 1, "time_step_s": 1.0},
        "access": {"uplink_rate_mbps": 200.0, "downlink_rate_mbps": 50.0},
        "links": {"geometry_loss": False},
    })
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, a, b), row(2, 0.0, a, b)], geometry=geo)
    assert res["natural_end"] is True
    # pkt1 completes service before the outage; pkt2 waits through it and is
    # delivered after recovery (regression: it used to sleep until horizon
    # because no geometry-recovery timer/wake existed)
    assert res["fates"][1] == "DELIVERED"
    assert res["fates"][2] == "DELIVERED"


def test_endpoint_activated_at_first_emission_not_at_t0():
    """A src cell whose first emission is in the future must not be created
    at t=0: activation happens at the actual emission instant.  Regression:
    the emitter used to call _ensure_endpoint before its delay yield, so a
    future src cell was created at t=0 (and could pre-position a slot)."""
    a, b, c = cell(0.0, 10.0), cell(0.0, 0.0), cell(0.0, 20.0)
    geo = StaticGeometry(1, neighbors_map={0: {}}, visible=lambda *_: True,
                         gsl_changes=[])
    cfg = make_cfg({
        "scenario": {"duration_s": 8.0, "num_satellites": 1,
                     "num_planes": 1, "time_step_s": 0.1},
        "access": {"slots_per_satellite": 2, "uplink_rate_mbps": 200.0,
                   "downlink_rate_mbps": 200.0, "idle_release_s": 0.2,
                   "min_dwell_s": 0.0, "hysteresis_deg": 0.0,
                   "acquisition_delay_s": 0.0},
    })
    # c appears only as a src at t=5.0 (never a dst), so it must not exist
    # before its first emission
    rows = [row(1, 0.0, a, b), row(2, 5.0, c, a)]
    k = kernel.Kernel(cfg, rows, geometry=geo)
    k.env.run(until=4.9)
    assert a in k.endpoints  # present traffic activated
    assert c not in k.endpoints  # future src cell not yet created
    k.env.run(until=5.6)
    assert c in k.endpoints  # created at its first emission


def test_future_src_does_not_preposition_when_present_cell_cannot_grant():
    """Kimi probe-2 counterexample: even when the present-demand cell cannot
    win the t=0 race (not yet visible), a future src cell must not
    pre-position the slot at t=0.  Regression: the emitter created future src
    endpoints at t=0, so B could preposition the only slot and hold it idle."""
    a, b = cell(0.0, 10.0), cell(0.0, 0.0)
    ac, bc = cell_center(a), cell_center(b)

    def vis(s, lat, lon, t):
        if (lat, lon) == ac:
            return t >= 1.0  # present cell A visible only from t=1
        return True          # future src cell B always visible

    geo = StaticGeometry(1, neighbors_map={0: {}}, visible=vis, gsl_changes=[])
    cfg = make_cfg({
        "scenario": {"duration_s": 8.0, "num_satellites": 1,
                     "num_planes": 1, "time_step_s": 0.1},
        "access": {"slots_per_satellite": 1, "uplink_rate_mbps": 200.0,
                   "downlink_rate_mbps": 200.0, "idle_release_s": 0.2,
                   "min_dwell_s": 0.0, "hysteresis_deg": 0.0,
                   "acquisition_delay_s": 0.0},
    })
    rows = [row(1, 0.0, a, b), row(2, 5.0, b, a)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    # B (future src) must not be associated before its first emission at t=5
    assoc_b = [e["t"] for e in res["handover"]["events"]
               if e["type"] == "associate" and e["endpoint"] == b]
    assert assoc_b and min(assoc_b) >= 5.0
    assert res["fates"][2] == "DELIVERED"


def test_random_outage_in_flight():
    cfg = make_cfg({
        # mean_good ~ 0: the GSL goes down essentially immediately and stays
        # down; the in-flight failure fires inside the first service interval
        "links": {"ge_enabled": True,
                  "ge_gsl": {"mean_good_s": 1e-9, "mean_bad_s": 1e9}},
        "scenario": {"num_satellites": 1, "num_planes": 1},
    })
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "RANDOM_OUTAGE_IN_FLIGHT"
    assert res["mechanisms"]["effective"]["ge_gsl_queries"] > 0


def test_ge_downwait_beyond_horizon_not_counted_as_occupied():
    """A packet that never transmits a bit (GE down with recovery beyond the
    horizon) must not have its waiting time counted as occupied at the stop.
    Regression: the stop-time closer added env.now - pick_time for any _svc
    still set, and ge.next_up() was appended without a horizon clamp, so the
    whole wait from pick to horizon was booked as occupied (inconsistent with
    the _transmit wait branch, which never counts down-wait)."""
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1,
                     "num_planes": 1},
        "links": {"ge_enabled": True,
                  "ge_gsl": {"mean_good_s": 1e-9, "mean_bad_s": 1e9}},
    })
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    # emit at t=1.0: GE flipped bad near t=0 and recovers far beyond horizon
    res = kernel.run_simulation(cfg, [row(1, 1.0, A, B)], geometry=geo)
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    assert res["occupied"]["gsl_uplink_s"] == pytest.approx(0.0, abs=1e-9)


def test_ge_downwait_beyond_horizon_with_late_deadline_settles_in_system():
    """When GE recovery is beyond the horizon AND the packet's deadline is
    also beyond the horizon, the run never reaches the deadline: the packet
    must settle as IN_SYSTEM_AT_STOP, not be mislabelled
    DATA_DEADLINE_EXPIRED at emission time."""
    cfg = make_cfg({
        "scenario": {"duration_s": 2.0, "num_satellites": 1,
                     "num_planes": 1},
        "links": {"ge_enabled": True,
                  "ge_gsl": {"mean_good_s": 1e-9, "mean_bad_s": 1e9}},
    })
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    # deadline (3.0) > horizon (2.0) > recovery start; GE stays down
    res = kernel.run_simulation(
        cfg, [row(1, 1.0, A, B, deadline=3.0)], geometry=geo)
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    assert res["occupied"]["gsl_uplink_s"] == pytest.approx(0.0, abs=1e-9)


def test_no_route_when_discovery_impossible():
    # control plane disabled + non-oracle policy: no way to learn who sees dst
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    cfg = make_cfg({"routing": {"policy": "hop"}})
    geo = StaticGeometry(2, neighbors_map=nb, visible=vis)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "NO_ROUTE"


def test_in_system_at_stop_when_destination_never_found():
    vis = lambda s, lat, lon, t: (lat, lon) == AC  # nobody sees B
    cfg = make_cfg({
        "control_plane": {"enabled": True, "advertise_interval_s": 1.0},
        "routing": {"policy": "hop"},
        "scenario": {"duration_s": 3.0},
    })
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}}, visible=vis)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"


def test_stalled_link_at_horizon_settles_without_infinite_loop():
    """A packet whose link never recovers before the horizon must settle as
    IN_SYSTEM_AT_STOP instead of looping forever in the same time slice
    (regression: stalled requeue lacked a horizon stop, causing
    RecursionError with large queues that never overflow)."""
    def vis(s, lat, lon, t):
        # A's uplink GSL is available until t=0.5 and never recovers.
        if (lat, lon) == AC:
            return t < 0.5
        return (lat, lon) == BC
    cfg = make_cfg({
        "control_plane": {"enabled": True, "advertise_interval_s": 1.0},
        "routing": {"policy": "hop"},
        "access": {"uplink_rate_mbps": 1.0, "uplink_queue_bits": 1_000_000_000},
        "links": {"geometry_loss": True},
        "scenario": {"duration_s": 2.0},
    })
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                         visible=vis, gsl_changes={0: {A: [0.5]}})
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    t = res["totals"]
    assert t["offered_bits"] == (t["delivered_bits"] + t["terminal_loss_bits"]
                                 + t["in_system_bits_at_stop"])


def test_data_deadline_expired_while_waiting():
    vis = lambda s, lat, lon, t: (lat, lon) == AC
    cfg = make_cfg({
        "control_plane": {"enabled": True, "advertise_interval_s": 1.0},
        "routing": {"policy": "hop"},
        "scenario": {"duration_s": 3.0},
    })
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}}, visible=vis)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B, deadline=0.5)], geometry=geo)
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"


def test_data_deadline_expired_in_transit():
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    # delivery would happen at ~0.164 s but deadline is 0.1 s
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B, deadline=0.1)], geometry=geo)
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"


def test_conservation_across_mixed_fates():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    cfg = make_cfg({
        "control_plane": {"enabled": True, "advertise_interval_s": 1.0},
        "routing": {"policy": "hop"},
        "access": {"uplink_rate_mbps": 1.0, "uplink_queue_bits": 16_000_000},
        "scenario": {"duration_s": 60.0},
    })
    geo = StaticGeometry(2, neighbors_map=nb, visible=vis)
    rows = [
        row(1, 0.0, A, B),                    # delivered (slow uplink, 0-8 s)
        row(2, 0.0, A, B),                    # queued, served 8-16 s, delivered
        row(3, 0.0, A, B),                    # queued, served 16-24 s, delivered
        row(4, 0.0, A, B),                    # access queue overflow (cap=2 pkts)
        row(5, 8.5, A, B, deadline=9.0),      # expires in queue at tick 9.1
        row(6, 9.5, A, NOWHERE),              # never found -> in system at stop
    ]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    t = res["totals"]
    assert t["offered_bits"] == 6 * 8_000_000
    assert t["offered_bits"] == (t["delivered_bits"] + t["terminal_loss_bits"]
                                 + t["in_system_bits_at_stop"])
    assert res["fates"][1] == "DELIVERED"
    assert res["fates"][4] == "ACCESS_QUEUE_OVERFLOW"
    assert res["fates"][5] == "DATA_DEADLINE_EXPIRED"
    assert res["fates"][6] == "IN_SYSTEM_AT_STOP"
    # exactly one fate per packet
    assert sorted(res["fates"]) == [1, 2, 3, 4, 5, 6]


def test_monitor_does_not_change_behavior():
    cfg1 = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1},
                     "execution": {"monitor": True}})
    cfg2 = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1},
                     "execution": {"monitor": False}})
    geo1 = StaticGeometry(1, visible=one_sat_visible_all)
    geo2 = StaticGeometry(1, visible=one_sat_visible_all)
    rows = [row(1, 0.0, A, B), row(2, 0.05, A, B)]
    r1 = kernel.run_simulation(cfg1, rows, geometry=geo1)
    r2 = kernel.run_simulation(cfg2, rows, geometry=geo2)
    assert r1["fates"] == r2["fates"]
    assert r1["deliveries"] == r2["deliveries"]
    assert r1["monitor_log"], "monitor=True must record events"


def test_same_time_emission_order_is_packet_id_order():
    cfg = make_cfg({
        "access": {"uplink_rate_mbps": 1.0},
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 80.0},
    })
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    rows = [row(1, 0.0, A, B), row(2, 0.0, A, B), row(3, 0.0, A, B)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    pids = [pid for _c, pid in res["service_log"]["uplink"]]
    assert pids == [1, 2, 3]


def test_drr_bit_fairness_with_mixed_packet_sizes():
    # A offers 4 x 8e6-bit packets, C offers 32 x 1e6-bit packets (equal
    # totals). With quantum 1e6 bits, DRR must keep the served-bits imbalance
    # bounded by quantum + max_packet at every completion, which plain
    # packet-level round-robin violates.
    cfg = make_cfg({
        "access": {"uplink_rate_mbps": 100.0, "drr_quantum_bits": 1_000_000},
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 10.0},
    })
    geo = StaticGeometry(1, visible=one_sat_visible_all)
    rows = [row(i + 1, 0.0, A, B, bits=8_000_000) for i in range(4)]
    rows += [row(100 + i, 0.0, C, B, bits=1_000_000) for i in range(32)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    log = res["service_log"]["uplink_bits"]
    assert len(log) == 36
    bound = 1_000_000 + 8_000_000  # quantum + max packet
    served = {A: 0, C: 0}
    max_imbalance = 0
    for _t, cell_id, bits in log:
        served[cell_id] += bits
        max_imbalance = max(max_imbalance, abs(served[A] - served[C]))
    assert max_imbalance <= bound, f"DRR fairness bound violated: {max_imbalance}"
    assert served[A] == 32_000_000 and served[C] == 32_000_000


# ---------------------------------------------------------------- Task 2:
# the kernel must wire scenario.geometry_epoch_s into the real
# Constellation geometry provider.

def test_kernel_passes_resolved_geometry_epoch_to_provider(monkeypatch):
    """A one-packet kernel fixture at two epochs must forward the resolved
    scenario.geometry_epoch_s into the real Constellation construction path
    (geometry=None), so the geometry is an explicit function of (epoch, t)
    and never of the machine clock."""
    seen = {}

    class RecordingConstellation(model.Constellation):
        def __init__(self, *args, **kwargs):
            seen["epoch"] = kwargs.get("geometry_epoch_s")
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(kernel.model, "Constellation", RecordingConstellation)
    for epoch in (0.0, 3600.0):
        cfg = make_cfg({
            "scenario": {"num_satellites": 2, "num_planes": 1,
                         "duration_s": 1.0, "geometry_epoch_s": epoch,
                         "altitude_km": 600.0},
        })
        # geometry=None: the kernel must build the Constellation itself and
        # hand it the resolved epoch
        res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)])
        assert res["natural_end"] is True
        assert seen["epoch"] == epoch
