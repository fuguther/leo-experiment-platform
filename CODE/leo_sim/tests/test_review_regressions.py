"""Permanent regression tests for the 2026-08-13 independent review probes.

Each test reproduces one confirmed counterexample from
/private/tmp/leo_v2_review_probes.py. They FAILED on the pre-remediation
implementation and must now pass.
"""
import json

import pytest

from CODE.leo_sim import config, kernel, receipt
from CODE.leo_sim.__main__ import main
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)


def test_r1_no_downlink_without_destination_slot():
    # the destination is never visible to the satellite holding its packet:
    # without a REAL destination association DELIVERED must stay impossible
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1},
        "access": {"slots_per_satellite": 1},
    })
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: (lat, lon) == AC)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    assert res["fates"][1] != "DELIVERED"
    assert not res["deliveries"]


def test_r2_vis_k_zero_sends_nothing():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    cfg = make_cfg({
        "scenario": {"duration_s": 0.1},
        "control_plane": {"enabled": True, "vis_k": 0,
                          "advertise_interval_s": 1.0, "packet_bits": 8_000},
    })
    res = kernel.run_simulation(cfg, [], geometry=StaticGeometry(2, neighbors_map=topo))
    assert res["control"]["counters"]["snapshots_created"] == 0
    assert res["control"]["counters"]["registered"] == 0
    assert res["caches"][0] == {} and res["caches"][1] == {}
    # control requested but never on the send path -> not research eligible
    assert res["mechanisms"]["effective"]["control_plane"] is False
    assert res["research_eligible"] is False


def test_r3_control_ledger_covers_in_system_and_conserves():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    cfg = make_cfg({
        "scenario": {"duration_s": 0.1},
        "links": {"isl_rate_mbps": 1.0},
        "control_plane": {"enabled": True, "vis_k": 1,
                          "advertise_interval_s": 1.0, "packet_bits": 8_000_000},
    })
    res = kernel.run_simulation(cfg, [], geometry=StaticGeometry(2, neighbors_map=topo))
    bits = res["control"]["bits"]
    assert bits["offered"] > 0
    # 8 s service on a 0.1 s horizon: everything still in system at stop
    assert bits["in_system"] == bits["offered"]
    t = res["control"]["totals"]
    assert t["offered_bits"] == (t["delivered_bits"] + t["terminal_loss_bits"]
                                 + t["in_system_bits_at_stop"])
    # the in-service control packets (one per satellite) each occupied their
    # link for the whole horizon
    assert abs(res["occupied"]["ctrl_isl_s"] - 0.2) < 1e-9


def test_r4_learning_never_falls_back_to_oracle():
    with pytest.raises(config.ConfigError, match="oracle"):
        make_cfg({
            "scenario": {"num_satellites": 1, "num_planes": 1},
            "routing": {"policy": "oracle", "learning_enabled": True},
            "control_plane": {"enabled": True},
            "learning": {"algorithm": "ddqn"},
        })


def _write_oracle_cfg(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "scenario:\n  duration_s: 2.0\n  num_satellites: 1\n  num_planes: 1\n  seed: 3\n"
        "endpoints:\n  sites:\n"
        "    - {name: a, lat: 0.1, lon: 0.1}\n"
        "    - {name: b, lat: 2.0, lon: 3.0}\n"
        "demand:\n  mode: uniform\n  offered_mbps: 4.0\n  packet_bits: 1000000\n"
        "routing:\n  policy: oracle\n"
        "control_plane:\n  enabled: false\n",
        encoding="utf-8")
    return p


def test_r5_receipt_rejects_fate_tampering(tmp_path):
    out = tmp_path / "out"
    assert main(["run", "--config", str(_write_oracle_cfg(tmp_path)),
                 "--out", str(out)]) == 0
    rcp_path = out / "receipt.json"
    rcp = json.loads(rcp_path.read_text())
    old_key = sorted(rcp["packet_fates"], key=int)[0]
    rcp["packet_fates"]["999999"] = rcp["packet_fates"].pop(old_key)  # rename id
    rcp_path.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n")
    errors = receipt.verify_receipt_dir(str(out))
    assert any("id set" in e for e in errors)


def test_r5b_receipt_rejects_fabricated_counts_and_mechanisms(tmp_path):
    out = tmp_path / "out"
    assert main(["run", "--config", str(_write_oracle_cfg(tmp_path)),
                 "--out", str(out)]) == 0
    rcp_path = out / "receipt.json"
    rcp = json.loads(rcp_path.read_text())
    rcp["fate_counts"] = {"fabricated": 123}
    rcp_path.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n")
    assert receipt.verify_receipt_dir(str(out))


def test_r5c_receipt_rejects_mechanism_tampering(tmp_path):
    out = tmp_path / "out"
    assert main(["run", "--config", str(_write_oracle_cfg(tmp_path)),
                 "--out", str(out)]) == 0
    rcp_path = out / "receipt.json"
    rcp = json.loads(rcp_path.read_text())
    rcp["mechanisms"]["requested"]["control_enabled"] = True
    rcp["mechanisms"]["effective"]["control_generated"] = 5
    rcp_path.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n")
    errors = receipt.verify_receipt_dir(str(out))
    assert errors, "tampered mechanisms must fail verification"


def test_r6_negative_and_zero_values_fail_closed():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"access": {"uplink_rate_mbps": -1.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"access": {"downlink_rate_mbps": 0.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"access": {"uplink_queue_bits": -5}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"access": {"acquisition_delay_s": -0.1}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"links": {"isl_rate_mbps": 0.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"links": {"ge_gsl": {"mean_good_s": -1.0, "mean_bad_s": 1.0}}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"control_plane": {"ttl_s": 0.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"demand": {"deadline_s": -2.0}})


def test_r7_transient_geometry_loss_fails_midflight_packet():
    # link down strictly inside the 0.08 s service interval, back up at
    # completion: the packet must still fail, and only the up-time is occupied
    # (explicit change timeline; left-closed: down at 0.02, up at 0.04)
    def visible(_s, _lat, _lon, t):
        return not (0.02 <= t < 0.04)

    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B)],
        geometry=StaticGeometry(1, visible=visible, gsl_changes=[0.02, 0.04]))
    assert res["fates"][1] == "GEOMETRY_LOSS_IN_FLIGHT"
    assert abs(res["occupied"]["gsl_uplink_s"] - 0.02) < 1e-6


def test_r8_deadline_crossed_during_final_propagation():
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    res = kernel.run_simulation(
        cfg, [row(1, 0.0, A, B, deadline=0.163)],
        geometry=StaticGeometry(1, visible=lambda s, lat, lon, t: True))
    assert res["fates"][1] == "DATA_DEADLINE_EXPIRED"
    assert 1 not in res["deliveries"]


def test_r9_propagation_flag_removed():
    # V2 always models propagation delay; the dead switch is gone
    with pytest.raises(config.ConfigError, match="unknown field"):
        config.resolve_config({"links": {"propagation": False}})


def test_r10_max_hops_duplicate_param_removed():
    with pytest.raises(config.ConfigError, match="unknown field"):
        config.resolve_config({"control_plane": {"vis_k": 4, "max_hops": 1}})


def test_r11_grid_boundary_coordinates_are_legal():
    from CODE.leo_sim import grid
    gid = grid.grid_id(90.0, 180.0)
    lat, lon = grid.grid_center(gid)
    assert -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def test_r12_control_and_data_share_one_isl_queue_cap():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    cfg = make_cfg({
        "scenario": {"duration_s": 1.0},
        "links": {"isl_queue_bits": 16_000_000},
        "control_plane": {"enabled": True, "vis_k": 1},
    })
    k = kernel.Kernel(cfg, [], geometry=StaticGeometry(2, neighbors_map=topo))
    link = k.isls[0]["E"]
    dp = kernel.DataPacket(1, A, B, 8_000_000, None, 0.0)
    cp = kernel.ControlPacket(1, 0, 1, 0.0, 10.0, 1, 8_000_000, {})
    link.put_data(dp)
    assert link.room(8_000_000)       # 8e6 of 16e6 used: one more fits
    link.put_ctrl(cp)
    assert not link.room(1)           # control consumed the SAME cap
    # and a further control packet must overflow the shared cap as well
    k.ctrl_ledger.register(2, 8_000_000)
    assert not link.room(8_000_000)
