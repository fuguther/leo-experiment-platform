"""Behavioral tests for capability-aware handover (BBM default, gated MBB).

Geometry convention: the destination cell B is visible only to sat1, so B's
serving satellite is always sat1. The source cell A follows a crossover:
sat0 preferred before t=5, sat1 preferred from t=5 (loss=True also makes
sat0 drop below min elevation). ISL 0<->1 exists, so packets for B can be
forwarded whenever A's current serving satellite is not sat1.
"""
import pytest

from CODE.leo_sim import config, kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)
LINE = {0: {"E": 1}, 1: {"W": 0}}


def _crossover_geometry(loss=False):
    def elev(s, lat, lon, t):
        if (lat, lon) == BC:
            return 90.0 if s == 1 else -10.0  # B served by sat1 only
        if s == 0:
            if t < 5.0:
                return 80.0
            return 20.0 if loss else 30.0
        return 20.0 if t < 5.0 else 80.0

    def vis(s, lat, lon, t):
        return elev(s, lat, lon, t) >= 25.0

    # explicit change timeline: the crossover happens at t=5.0 (left-closed)
    return StaticGeometry(2, neighbors_map=LINE, visible=vis, elevation=elev,
                          gsl_changes=[5.0])


def _handover_cfg(duration=30.0, **access_over):
    over = {
        "scenario": {"duration_s": duration, "time_step_s": 0.1},
        "access": access_over,
    }
    return make_cfg(over)


def test_bbm_switches_on_better_elevation():
    cfg = _handover_cfg()
    # lazy endpoint activation: a src cell is created at its first emission.
    # pkt1 at t=0 activates A on sat0 (pre-crossover); the t=5 crossover then
    # exercises the BBM switch; pkt2 arrives after the switch.
    rows = [row(1, 0.0, A, B), row(2, 6.0, A, B)]
    res = kernel.run_simulation(cfg, rows, geometry=_crossover_geometry())
    assert [res["fates"][p] for p in (1, 2)] == ["DELIVERED"] * 2
    assert res["deliveries"][2]["path"] == [1]  # A on sat1, B served by sat1
    bbm = [e for e in res["handover"]["events"] if e["type"] == "bbm"]
    assert bbm and bbm[0]["from"] == 0 and bbm[0]["to"] == 1


def test_hysteresis_blocks_switch():
    # sat0=50 deg always; sat1 appears at t>=2 with 60 deg: better, but the
    # 10 deg gain is below the 30 deg hysteresis margin -> keep stable.
    def elev(s, lat, lon, t):
        if (lat, lon) == BC:
            return 90.0 if s == 1 else -10.0
        if s == 0:
            return 50.0
        return 20.0 if t < 2.0 else 60.0

    vis = lambda s, lat, lon, t: elev(s, lat, lon, t) >= 25.0
    geo = StaticGeometry(2, neighbors_map=LINE, visible=vis, elevation=elev,
                         gsl_changes=[2.0])
    cfg = _handover_cfg(**{"hysteresis_deg": 30.0})
    rows = [row(1, 0.0, A, B), row(2, 6.0, A, B)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    assert [res["fates"][p] for p in (1, 2)] == ["DELIVERED"] * 2
    # pkt2 after the crossover: hysteresis still holds A on sat0 -> forwarded
    assert res["deliveries"][2]["path"] == [0, 1]
    assert [e for e in res["handover"]["events"]
            if e["type"] in ("bbm", "mbb") and e.get("from") == 0] == []


def test_min_dwell_blocks_voluntary_switch():
    cfg = _handover_cfg(**{"min_dwell_s": 100.0})
    rows = [row(1, 0.0, A, B), row(2, 6.0, A, B)]
    res = kernel.run_simulation(cfg, rows,
                                geometry=_crossover_geometry(loss=False))
    assert [res["fates"][p] for p in (1, 2)] == ["DELIVERED"] * 2
    # pkt2 after the crossover: min dwell holds the old link -> forwarded
    assert res["deliveries"][2]["path"] == [0, 1]
    assert [e for e in res["handover"]["events"] if e["type"] == "bbm"] == []


def test_forced_switch_on_geometry_loss_despite_dwell():
    # dwell blocks voluntary switches, never loss of the serving satellite
    cfg = _handover_cfg(**{"min_dwell_s": 100.0})
    res = kernel.run_simulation(cfg, [row(1, 6.0, A, B)],
                                geometry=_crossover_geometry(loss=True))
    assert res["fates"][1] == "DELIVERED"
    assert res["deliveries"][1]["path"] == [1]


def test_acquisition_delay_queues_without_rejection():
    cfg = _handover_cfg(**{"acquisition_delay_s": 2.0})
    res = kernel.run_simulation(cfg, [row(1, 5.2, A, B)],
                                geometry=_crossover_geometry(loss=True))
    assert res["fates"][1] == "DELIVERED"
    # switch decided at t=5.0, link usable at t=7.0 (0.08 s services follow)
    assert res["deliveries"][1]["delivered_at"] >= 7.0


def test_mbb_drains_old_link_without_loss():
    cfg = _handover_cfg(**{
        "association": "mbb", "dual_connect": True,
        "uplink_rate_mbps": 1.0, "retirement_deadline_s": 100.0,
    })
    geo = _crossover_geometry()
    rows = [row(1, 3.9, A, B), row(2, 4.8, A, B), row(3, 6.0, A, B)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    assert [res["fates"][p] for p in (1, 2, 3)] == ["DELIVERED"] * 3
    # p1 in service during the switch (3.9-11.9) finishes on the old link;
    # p2 queued before the switch drains on the old link; p3 uses the new one.
    assert res["deliveries"][3]["path"] == [1]
    mbb_ev = [e for e in res["handover"]["events"] if e["type"] == "mbb"]
    assert mbb_ev and mbb_ev[0]["from"] == 0 and mbb_ev[0]["to"] == 1
    rel = [e for e in res["handover"]["events"]
           if e["type"] == "release" and e["reason"] == "mbb_drained"
           and e["endpoint"] == A]
    assert rel and rel[0]["t"] >= 11.9  # old link lived until drain


def test_mbb_retirement_deadline_reassigns_leftover():
    # hard retirement sacrifices the in-flight packet's progress (it is
    # re-served in full on the new link), so the horizon leaves room for the
    # rework: 40 s instead of 30 s
    cfg = _handover_cfg(duration=40.0, **{
        "association": "mbb", "dual_connect": True,
        "uplink_rate_mbps": 1.0, "retirement_deadline_s": 2.0,
    })
    geo = _crossover_geometry()
    rows = [row(1, 3.9, A, B), row(2, 4.8, A, B), row(3, 6.0, A, B)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    assert [res["fates"][p] for p in (1, 2, 3)] == ["DELIVERED"] * 3
    rel = [e for e in res["handover"]["events"]
           if e["type"] == "release" and e["reason"] == "mbb_retire_deadline"
           and e["endpoint"] == A]
    assert rel, "retirement deadline must force the release"
    # p1 was in service at the deadline: interrupted, re-served in full (the
    # uplink log shows it twice), and never delivered from the dying link
    p1_services = [pid for _c, pid in res["service_log"]["uplink"] if pid == 1]
    assert len(p1_services) == 2
    # p2 was queued for the old link; after the deadline it is reassigned and
    # reaches B via the new serving satellite
    assert res["deliveries"][2]["path"][-1] == 1


def test_mbb_requires_dual_connect_fail_closed():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"access": {"association": "mbb", "dual_connect": False}})


def test_access_slot_admission_limit():
    # K=1 under contention: at most one endpoint may hold the slot at any
    # instant, and fair rotation must serve every demanding endpoint — the
    # old static allocation starved all but the first (cell-sorted) endpoint.
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 20.0},
        "access": {"slots_per_satellite": 1, "idle_release_s": 0.5},
    })
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    rows = [row(1, 1.0, A, B), row(2, 1.0, cell(10.0, 0.0), B)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assert res["fates"][2] == "DELIVERED"
    # instantaneous admission never exceeded K=1: replay associate/release in
    # their true emission order (the event list is already chronological)
    active = set()
    for e in res["handover"]["events"]:
        if e["type"] == "associate":
            assert e["endpoint"] not in active
            active.add(e["endpoint"])
            assert len(active) <= 1
        elif e["type"] == "release":
            active.discard(e["endpoint"])
