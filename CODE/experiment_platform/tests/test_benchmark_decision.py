"""T1-COMPLETE P6: decision benchmark and finite-pool pressure checks."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from CODE.experiment_platform import benchmark_decision as bd
from CODE.leo_sim import config as config_mod
from CODE.experiment_platform import scripted_scenarios

ROOT = Path(__file__).resolve().parents[3]


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "CODE.experiment_platform.benchmark_decision",
         *args], cwd=str(ROOT), capture_output=True, text=True, timeout=1800,
        check=False)


def test_the_benchmark_artifact_is_complete(tmp_path):
    out = tmp_path / "bench.json"
    done = _run("--scenario", "reachability", "--iterations", "100",
                "--rounds", "2", "--warmup", "10", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    assert doc["schema"] == "benchmark-decision/v1"
    assert doc["identity"]["git"]["commit"]
    assert doc["identity"]["sources"]["combined_sha256"]
    assert doc["clock"] == "time.perf_counter"
    assert doc["environment"]["device"] == "host_cpu"
    assert "seconds per call" in doc["units"]["stats"]
    for key in ("full_decision", "inference_only", "empty_call_baseline"):
        assert key in doc


def test_percentiles_are_ordered_and_sampling_is_declared(tmp_path):
    out = tmp_path / "bench.json"
    done = _run("--scenario", "reachability", "--iterations", "100",
                "--rounds", "2", "--warmup", "10", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    stats = doc["full_decision"]["stats"]
    assert stats["p50_s"] <= stats["p95_s"] <= stats["p99_s"]
    assert doc["full_decision"]["warmup"] == 10
    assert doc["full_decision"]["rounds"] == 2
    assert doc["full_decision"]["iterations"] == 100
    assert doc["full_decision"]["complete_rounds"] >= 1
    assert stats["requests_per_s"] > 0


def test_the_empty_call_baseline_is_measured_and_smaller(tmp_path):
    out = tmp_path / "bench.json"
    done = _run("--scenario", "reachability", "--iterations", "100",
                "--rounds", "2", "--warmup", "10", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    null = doc["empty_call_baseline"]["per_call_s"]
    full = doc["full_decision"]["stats"]["mean_s"]
    assert null > 0
    assert null < full, "the timing-call baseline must not exceed the work"


def test_full_decision_and_inference_only_are_separate(tmp_path):
    out = tmp_path / "bench.json"
    done = _run("--scenario", "reachability", "--iterations", "100",
                "--rounds", "2", "--warmup", "10", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    assert doc["full_decision"]["stats"]["n"] > 0
    assert doc["inference_only"]["stats"]["n"] > 0


def test_the_pool_sweep_includes_the_unbounded_negative_control(tmp_path):
    out = tmp_path / "bench.json"
    done = _run("--scenario", "reachability", "--iterations", "50",
                "--rounds", "1", "--warmup", "5", "--service-s", "0.01",
                "--pool-sweep", "0,1,2", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    sweep = {row["servers"]: row for row in doc["finite_pool"]}
    assert set(sweep) == {0, 1, 2}
    assert sweep[0]["unbounded"] is True
    for row in sweep.values():
        assert row["requests"] >= 0
        assert row["total_wait_s"] >= 0.0
        assert row["config_sha256"]


def test_a_bounded_pool_without_service_time_is_skipped_with_a_reason():
    resolved, rows, geometry, _meta = scripted_scenarios.build("reachability")
    report = bd.pool_sweep(resolved, rows, geometry, pools=(1,),
                           compute_delay_s=0.0)
    assert report[0]["skipped"] is True
    assert "positive service time" in report[0]["reason"]


def test_a_bounded_pool_really_queues_under_load():
    """Three packets inside one service interval must queue on N=1."""
    resolved, rows, geometry, _meta = scripted_scenarios.build("contention")
    loaded = json.loads(json.dumps(resolved["config"]))
    loaded["demand"]["offered_mbps"] = 50.0
    loaded = config_mod.resolve_config(loaded)
    report = bd.pool_sweep(loaded, rows, geometry, pools=(0, 1),
                           compute_delay_s=0.5)
    by_servers = {row["servers"]: row for row in report}
    assert by_servers[1]["requests"] >= 2
    assert by_servers[1]["total_wait_s"] >= by_servers[0]["total_wait_s"]


def test_pool_sweep_percentile_helpers_are_consistent():
    assert bd._percentiles([]) == {"n": 0}
    one = bd._percentiles([0.5])
    assert one["p50_s"] == 0.5 and one["p99_s"] == 0.5
    many = bd._percentiles([1.0, 2.0, 3.0, 4.0, 5.0])
    assert many["p50_s"] == 3.0
    assert many["min_s"] == 1.0 and many["max_s"] == 5.0
