"""T1-COMPLETE P8: five execution modes on one fair trace."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from CODE.experiment_platform import execution_compare as ec, scripted_scenarios
from CODE.leo_sim import config as config_mod
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

ROOT = Path(__file__).resolve().parents[3]

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "CODE.experiment_platform.execution_compare",
         *args], cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
        check=False)


def test_all_five_modes_run_on_one_fair_trace(tmp_path):
    out = tmp_path / "exec.json"
    done = _run("--scenario", "contention", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    assert doc["schema"] == "execution-compare/v1"
    assert doc["identity"]["git"]["commit"]
    modes = [r["mode"] for r in doc["modes"]]
    assert modes == list(ec.MODES)
    for r in doc["modes"]:
        assert r["seed"] == doc["fairness"]["seed"]
        assert r["arm"] == doc["modes"][0]["arm"]
        assert r["predictor"] == doc["modes"][0]["predictor"]


def test_the_only_config_difference_is_the_execution_mechanism(tmp_path):
    out = tmp_path / "exec.json"
    done = _run("--scenario", "contention", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    for mode, diffs in doc["fairness"]["diffs"].items():
        for field in diffs:
            assert field.startswith("time_alignment.") or field.startswith(
                "async_routing."), (mode, field)


def _flow_fixture():
    cfg = make_cfg({
        "scenario": {"duration_s": 10.0},
        "demand": {"packet_bits": 400_000},
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": 0.2},
        "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
        "time_alignment": {"enabled": True, "arm": "candidate",
                           "execution_mode": "per_packet",
                           "per_flow_ttl_s": 5.0},
        "async_routing": {"enabled": False, "update_interval_s": 5.0},
    })
    rows = [row(i, 0.05 * i, A, B) for i in range(1, 9)]
    geometry = StaticGeometry(2, neighbors_map=NB, visible=VIS)
    return cfg, rows, geometry


def test_per_flow_reuses_a_cached_flow_and_computes_less():
    cfg, rows, geometry = _flow_fixture()
    packet = ec._row_for_mode(cfg, rows, geometry, "per_packet")
    flow = ec._row_for_mode(cfg, rows, geometry, "per_flow")
    assert flow["reuse"]["per_flow_cache_hits"] > 0, flow["reuse"]
    assert (flow["compute"]["decision_requests"]
            < packet["compute"]["decision_requests"]), (
        packet["compute"], flow["compute"])


def test_precomputed_builds_one_topology_table_and_queries_nothing():
    cfg, rows, geometry = _flow_fixture()
    rowd = ec._row_for_mode(cfg, rows, geometry, "precomputed")
    assert rowd["precompute"]["builds"] == 1
    assert rowd["precompute"]["installs"] == 1
    assert rowd["precompute"]["targets"] > 0
    assert rowd["reuse"]["schedule_queries"] == 0


def test_async_point_and_window_use_one_and_four_bins():
    resolved, rows, geometry, _meta = scripted_scenarios.build("contention")
    point = ec._row_for_mode(resolved, rows, geometry, "async_point")
    window = ec._row_for_mode(resolved, rows, geometry, "async_window")
    assert point["reuse"]["bins"] == 1
    assert window["reuse"]["bins"] == 4
    for rowd in (point, window):
        assert rowd["reuse"]["schedule_queries"] >= 1
        assert rowd["reuse"]["schedule_installs"] >= 1
        assert rowd["config_sha256"]


def test_ddqn_without_a_checkpoint_is_an_external_blocker():
    resolved = config_mod.resolve_config(
        {"routing": {"learning_enabled": True},
         "learning": {"algorithm": "ddqn", "mode": "train"}})
    status = ec.ddqn_status(resolved)
    assert status["state"] == "EXTERNAL_BLOCKER"
    assert "checkpoint" in status["reason"]
    assert status["recovery"]


def test_ddqn_with_a_missing_checkpoint_path_is_reported():
    resolved = config_mod.resolve_config(
        {"routing": {"learning_enabled": True},
         "learning": {"algorithm": "ddqn", "mode": "eval",
                      "checkpoint_path": "/nonexistent/ckpt.h5",
                      "checkpoint_sha256": "a" * 64,
                      "checkpoint_metadata_sha256": "b" * 64}})
    status = ec.ddqn_status(resolved)
    assert status["state"] == "EXTERNAL_BLOCKER"
    assert "does not exist" in status["reason"]


def test_the_deterministic_matrix_reports_ddqn_not_requested():
    resolved = config_mod.resolve_config({})
    status = ec.ddqn_status(resolved)
    assert status["state"] == "NOT_REQUESTED"


def test_an_existing_output_is_refused(tmp_path):
    target = tmp_path / "exists.json"
    target.write_text("{}")
    done = _run("--scenario", "contention", "--out", str(target))
    assert done.returncode == 2
    assert "exists" in done.stdout


def test_an_unknown_mode_is_refused(tmp_path):
    done = _run("--scenario", "contention", "--modes", "per_packet,bogus",
                "--out", str(tmp_path / "bad.json"))
    assert done.returncode == 2
    assert "unknown execution mode" in done.stdout
