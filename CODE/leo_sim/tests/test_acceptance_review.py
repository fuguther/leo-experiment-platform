"""Permanent regressions from the 2026-08-13 independent acceptance review.

These tests capture only findings independently reproduced by Codex.  A
review comment is not a defect until a failing behavior probe demonstrates it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from CODE.leo_sim import config, control, governance, kernel, learning, receipt, routing, trace
from CODE.leo_sim.__main__ import main
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row


A = cell(0.0, 0.0)
B = cell(0.0, 10.0)


def test_terminal_ddqn_transition_needs_no_next_legal_action():
    y = learning.ddqn_targets(
        np.array([[1.0, 2.0]]), np.array([[3.0, 4.0]]),
        np.array([[False, False]]), rewards=np.array([7.0]),
        dones=np.array([True]), gamma=0.9)
    assert y.tolist() == [7.0]


def test_duplicate_yaml_key_is_rejected(tmp_path):
    p = tmp_path / "duplicate.yaml"
    p.write_text(
        "scenario:\n  duration_s: 1.0\n  duration_s: 9.0\n",
        encoding="utf-8")
    with pytest.raises(config.ConfigError, match="duplicate"):
        config.load_config_file(str(p))


def test_trace_compiler_rejects_symlink_artifact(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("sentinel\n", encoding="utf-8")
    before = hashlib.sha256(victim.read_bytes()).hexdigest()
    out = tmp_path / "out"
    out.mkdir()
    (out / "trace.csv").symlink_to(victim)
    resolved = config.load_config_file("CODE/leo_sim/profiles/smoke.yaml")
    with pytest.raises(trace.TraceError, match="symbolic link"):
        trace.compile_trace(resolved, str(out))
    assert hashlib.sha256(victim.read_bytes()).hexdigest() == before


def test_receipt_cli_handles_directory_artifact_without_traceback(tmp_path, capsys):
    out = tmp_path / "run"
    out.mkdir()
    (out / "receipt.json").mkdir()
    assert main(["receipt", "verify", str(out)]) == 2
    captured = capsys.readouterr()
    assert "FAILED" in captured.out
    assert "Traceback" not in captured.err


def test_csv_run_intent_binds_input_bytes(tmp_path):
    csv_path = tmp_path / "demand.csv"
    csv_path.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits\n"
        "1,0,0,0,0,10,8000\n", encoding="utf-8")
    request = {
        "runtime_kind": "leo_sim_v2",
        "config": {"demand": {"mode": "csv", "csv_path": str(csv_path)}},
    }
    first = governance.build_run_intent(request)
    csv_path.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits\n"
        "1,0,0,0,0,10,16000\n", encoding="utf-8")
    second = governance.build_run_intent(request)
    assert first["input_sha256"] != second["input_sha256"]
    assert first["trace_identity_sha256"] != second["trace_identity_sha256"]


def _make_artifact(tmp_path: Path, cfg_overrides=None):
    base = {
        "scenario": {"num_satellites": 1, "num_planes": 1,
                     "duration_s": 0.2},
        "control_plane": {"enabled": False},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
    }
    if cfg_overrides:
        base.update(cfg_overrides)
    resolved = make_cfg(base)
    tdir = tmp_path / "compiled"
    manifest = trace.compile_trace(resolved, str(tdir))
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    geo = StaticGeometry(
        resolved["config"]["scenario"]["num_satellites"],
        visible=lambda *_args: True)
    rows = trace.load_trace(
        str(tdir / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"])
    result = kernel.run_simulation(resolved, rows, geometry=geo)
    out = tmp_path / "run"
    receipt.write_run(str(out), resolved, tbytes, manifest, result, rows)
    return out


def test_manifest_contract_forgery_is_rejected(tmp_path):
    out = _make_artifact(tmp_path)
    mpath = out / "manifest.json"
    rpath = out / "receipt.json"
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    manifest["schema"] = "attacker/v999"
    manifest["provenance"] = "calibrated_user_demand"
    mpath.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    rcp = json.loads(rpath.read_text(encoding="utf-8"))
    rcp["trace_manifest_sha256"] = hashlib.sha256(mpath.read_bytes()).hexdigest()
    rpath.write_text(json.dumps(rcp, sort_keys=True) + "\n", encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert any("manifest schema" in e for e in errors)
    assert any("manifest provenance" in e for e in errors)


def test_control_counters_must_match_instance_ledger(tmp_path):
    resolved = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1,
                     "duration_s": 0.2},
        "control_plane": {"enabled": True, "vis_k": 1,
                              "advertise_interval_s": 100.0},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
    })
    tdir = tmp_path / "compiled"
    manifest = trace.compile_trace(resolved, str(tdir))
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}})
    result = kernel.run_simulation(resolved, [], geometry=geo)
    out = tmp_path / "run"
    receipt.write_run(str(out), resolved, tbytes, manifest, result, [])

    lpath = out / "ledgers.json"
    rpath = out / "receipt.json"
    ledgers = json.loads(lpath.read_text(encoding="utf-8"))
    ledgers["control_instances"] = {}
    lpath.write_text(json.dumps(ledgers, sort_keys=True) + "\n", encoding="utf-8")
    rcp = json.loads(rpath.read_text(encoding="utf-8"))
    rcp["ledgers_sha256"] = hashlib.sha256(lpath.read_bytes()).hexdigest()
    rpath.write_text(json.dumps(rcp, sort_keys=True) + "\n", encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert any("control registered" in e for e in errors)


def test_local_run_never_self_declares_research_eligible():
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1,
                     "duration_s": 1.0},
        "control_plane": {"enabled": False},
    })
    geo = StaticGeometry(1, visible=lambda *_args: True)
    result = kernel.run_simulation(cfg, [row(1, 0.0, A, B, bits=8_000)], geometry=geo)
    assert result["natural_end"] is True
    assert result["research_eligible"] is False
    assert result["mechanisms"]["effective"]["mbb"] is False


def test_direct_acceptance_non_oracle_routing_check_not_vacuous():
    from CODE.leo_sim import acceptance
    base = {
        "natural_end": True,
        "conservation_ok": True,
        "fate_counts": {"DELIVERED": 1},
        "occupied": {"isl_s": 1.0},
        "control": {"counters": {"arrived": 1}},
        "mechanisms": {"effective": {"control_plane": True}},
        "handover": {"events": []},
        "access": {},
        "routing_label": routing.ORACLE_LABEL,
    }
    # the oracle label must FAIL the gate: the previous "!= 'oracle'" check
    # was always true and let an oracle-labelled direct scenario pass
    assert acceptance._case_checks("direct", base)["non_oracle_routing"] is False
    base["routing_label"] = None
    assert acceptance._case_checks("direct", base)["non_oracle_routing"] is True


class _PermanentlyDownIsl(StaticGeometry):
    def isl_available(self, a, b, t):
        return False


def test_data_deadline_expires_while_waiting_in_down_isl_queue():
    cfg = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1,
                     "duration_s": 2.0},
        "control_plane": {"enabled": False},
    })
    geo = _PermanentlyDownIsl(
        2, neighbors_map={0: {"E": 1}, 1: {"W": 0}})
    kern = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, A, B, 8_000, 0.5, 0.0)
    kern.ledger.register(pkt.pid, pkt.bits)
    kern.isls[0]["E"].put_data(pkt)
    result = kern.run()
    assert result["fates"][1] == "DATA_DEADLINE_EXPIRED"


def test_delay_route_uses_arrived_remote_link_metric_not_global_geometry():
    topo = {0: {"E": 1, "N": 2}, 1: {"W": 0, "E": 2},
            2: {"S": 0, "W": 1}}
    global_ranges = {(0, 1): 100.0, (1, 2): 100_000.0, (0, 2): 1_000.0}
    geo = StaticGeometry(
        3, neighbors_map=topo,
        isl_range_fn=lambda a, b, _t: global_ranges.get(
            (a, b), global_ranges.get((b, a), 1_000.0)))
    cache = control.LocalCache()
    cache.put(control.CacheEntry(
        1, {"serve_cells": [],
            "isl_propagation_s": {"E": {"peer": 2, "value": 0.0001}}},
        0.0, 0.1, 10.0))
    cache.put(control.CacheEntry(
        2, {"serve_cells": [B],
            "isl_propagation_s": {"W": {"peer": 1, "value": 0.0001}}},
        0.0, 0.1, 10.0))
    dirs, status = routing.choose_next_hop(
        "delay", 0, B, 1.0, geo, topo, cache, {}, 1e9,
        lambda km: km / 299_792.458)
    assert status == "ok"
    assert dirs[0] == "E"  # cached 1->2 metric wins; hidden global metric must not
