from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from CODE.leo_sim import comparison, config, trace


PROFILE = Path(__file__).resolve().parent.parent / "profiles" / "comparison.yaml"


def test_comparison_profile_projects_to_distinct_real_gateways(tmp_path):
    resolved = config.load_config_file(str(PROFILE))
    trace_dir = tmp_path / "trace"
    trace.compile_trace(resolved, str(trace_dir))
    trace_path = trace_dir / "trace.csv"
    digest = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    sites = comparison._gateway_sites(Path(__file__).resolve().parents[2])

    rows, manifest = comparison.load_and_project_trace(
        trace_path,
        sites,
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        expected_sha256=digest,
        max_packets=resolved["config"]["execution"]["max_packets"],
    )

    names = {
        row["source_gateway"].name for row in rows
    } | {
        row["destination_gateway"].name for row in rows
    }
    assert names == {"Malaga, Spain", "Tokyo, Japan"}
    assert manifest["trace_sha256"] == digest


def test_comparison_rejects_shell_without_legacy_equivalent(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        PROFILE.read_text(encoding="utf-8").replace(
            "num_satellites: 140", "num_satellites: 21"
        ).replace("num_planes: 7", "num_planes: 3"),
        encoding="utf-8",
    )
    with pytest.raises(comparison.ComparisonError, match="legacy constellation shell"):
        comparison.run_comparison(bad, tmp_path / "out")


def test_legacy_arm_forces_physical_time_and_walker_delta(monkeypatch, tmp_path):
    resolved = config.load_config_file(str(PROFILE))
    trace_dir = tmp_path / "trace"
    trace.compile_trace(resolved, str(trace_dir))
    trace_path = trace_dir / "trace.csv"
    sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    sites = comparison._gateway_sites(Path(__file__).resolve().parents[2])
    selected = [site for site in sites if site.name in {"Malaga, Spain", "Tokyo, Japan"}]
    captured = {}

    class Done:
        returncode = 1

    def fake_run(*args, **kwargs):
        captured.update(kwargs["env"])
        return Done()

    monkeypatch.setattr(comparison.subprocess, "run", fake_run)
    out = tmp_path / "legacy"
    out.mkdir()
    with pytest.raises(comparison.ComparisonError, match="exited 1"):
        comparison._legacy_arm(
            resolved, trace_path, sha, selected, out,
            Path(__file__).resolve().parents[2],
        )
    assert captured["SIM_MOVEMENT_SPEEDUP"] == "1"
    assert captured["SIM_WALKER_PATTERN"] == "delta"
