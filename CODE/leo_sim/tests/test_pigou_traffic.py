"""Contract tests for deterministic three-phase Pigou traffic generation.

These tests compile the CSV through the native leo-sim trace compiler.  They
never instantiate a constellation, kernel, or simulator.
"""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import pytest

import CODE
from CODE.leo_sim import config, trace


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "build_three_phase_trace.py"
)
EXPECTED_A = {
    "Beijing", "Tokyo", "Singapore", "Sydney", "Mumbai", "Frankfurt",
    "London", "Cairo", "NewYork", "SanFrancisco", "SaoPaulo",
    "Johannesburg",
}
EXPECTED_B = {
    "Beijing", "Tokyo", "Seoul", "Singapore", "Jakarta", "Manila", "Sydney",
    "Mumbai", "Dubai", "Istanbul", "Moscow", "Frankfurt", "London", "Tunis",
    "Lagos", "Nairobi", "NewYork", "Toronto", "Chicago", "LosAngeles",
    "PanamaCity", "Bogota", "SaoPaulo", "BuenosAires",
}
EXPECTED_A_HOTSPOTS = {
    ("Mumbai", "London"), ("Singapore", "Frankfurt"), ("Tokyo", "Frankfurt"),
    ("SaoPaulo", "Frankfurt"), ("SaoPaulo", "London"),
    ("Johannesburg", "London"),
}


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location(
        "pigou_three_phase_builder", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _raw_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _background_projection(path: Path, count: int) -> bytes:
    lines = path.read_bytes().splitlines()
    selected = [
        line for line in lines[1:]
        if int(line.split(b",", 1)[0]) <= count
    ]
    return b"\n".join(selected) + b"\n"


def _route_for(
    row: dict[str, str],
    coordinates: dict[tuple[str, str], str],
) -> tuple[str, str]:
    source = coordinates[(row["src_lat"], row["src_lon"])]
    destination = coordinates[(row["dst_lat"], row["dst_lon"])]
    return source, destination


def test_scene_catalogs_and_native_config_contract(builder, tmp_path):
    assert {site.name for site in builder.SCENE_A_SITES} == EXPECTED_A
    assert {site.name for site in builder.SCENE_B_SITES} == EXPECTED_B
    assert len(builder.SCENE_A_SITES) == 12
    assert len(builder.SCENE_B_SITES) == 24

    for sites in (builder.SCENE_A_SITES, builder.SCENE_B_SITES):
        names = [site.name for site in sites]
        assert len(names) == len(set(names))
        for site in sites:
            assert -90.0 <= site.lat <= 90.0
            assert -180.0 <= site.lon <= 180.0
            assert math.isfinite(site.lat) and math.isfinite(site.lon)
            assert site.demand_weight > 0

    a_sites = {site.name: site for site in builder.SCENE_A_SITES}
    for source, destination in EXPECTED_A_HOTSPOTS:
        assert builder._haversine_km(
            a_sites[source], a_sites[destination]) >= 6_000.0

    scene_a = builder.build(
        tmp_path / "a-small", scene="A", seed=101, interval=0.05,
        offered_mbps=1.0)
    cfg_a = scene_a["config"]
    assert cfg_a["scenario"]["num_satellites"] == 96
    assert cfg_a["scenario"]["num_planes"] == 12
    assert cfg_a["scenario"]["altitude_km"] == 800.0
    assert cfg_a["scenario"]["inclination_deg"] == 53.0
    assert cfg_a["scenario"]["duration_s"] == 9.5
    assert cfg_a["demand"]["emission_start_s"] == 0.2
    assert cfg_a["demand"]["emission_end_s"] == 7.5
    assert cfg_a["demand"]["packet_bits"] == 64_000
    assert cfg_a["demand"]["deadline_s"] == 0.240
    assert cfg_a["demand"]["research_deadline_s"] == 0.240
    assert cfg_a["demand"]["mode"] == "csv"
    assert cfg_a["demand"]["csv_path"] == str(scene_a["trace_path"])
    assert cfg_a["endpoints"]["mlab_auto"] is False
    assert cfg_a["demand"]["population_path"] is None
    assert cfg_a["links"]["isl_rate_mbps"] == 70.0
    assert cfg_a["links"]["isl_queue_bits"] == 2_240_000
    assert 0.030 <= (cfg_a["links"]["isl_queue_bits"]
                      / (cfg_a["links"]["isl_rate_mbps"] * 1_000_000)) <= 0.035
    assert cfg_a["links"]["max_isl_km"] >= 6_000.0
    assert cfg_a["control_plane"]["vis_k"] == 1
    assert cfg_a["control_plane"]["ttl_s"] > 0
    assert cfg_a["control_plane"]["advertise_interval_s"] == 0.05
    assert cfg_a["routing"]["policy"] == "hop"
    assert cfg_a["execution"]["decision_observation_mode"] == "refresh"
    assert cfg_a["execution"]["max_packets"] == scene_a["packet_budget"]

    scene_b = builder.build(
        tmp_path / "b-small", scene="B", seed=101, interval=0.10,
        offered_mbps=1.0)
    cfg_b = scene_b["config"]
    assert cfg_b["scenario"]["num_satellites"] == 66
    assert cfg_b["scenario"]["num_planes"] == 6
    assert cfg_b["scenario"]["altitude_km"] == 780.0
    assert cfg_b["scenario"]["inclination_deg"] == 86.4
    assert cfg_b["demand"]["deadline_s"] == 0.280
    assert cfg_b["demand"]["research_deadline_s"] == 0.280
    assert cfg_b["links"]["isl_rate_mbps"] == 60.0
    assert cfg_b["links"]["isl_queue_bits"] == 1_500_000
    assert cfg_b["links"]["max_isl_km"] >= 6_000.0
    assert cfg_b["control_plane"]["advertise_interval_s"] == 0.10

    assert scene_a["trace_path"].read_text(encoding="utf-8").splitlines()[0] \
        == ",".join(builder.TRACE_HEADER)
    config.resolve_config(cfg_a)
    config.resolve_config(cfg_b)


def test_background_is_byte_identical_and_pulse_is_only_targeted(
        builder, tmp_path):
    built = {}
    for multiplier in (1.0, 1.6, 2.5):
        built[multiplier] = builder.build(
            tmp_path / f"m-{multiplier}",
            scene="A",
            seed=101,
            multiplier=multiplier,
            interval=0.05,
        )

    baseline = built[1.0]
    base_count = baseline["background_packet_count"]
    base_ids = tuple(range(1, base_count + 1))
    baseline_projection = _background_projection(
        baseline["trace_path"], base_count)
    baseline_hash = hashlib.sha256(baseline_projection).hexdigest()
    assert baseline_hash == baseline["background_packet_sha256"]
    assert baseline["pulse_packet_count"] == 0
    assert baseline["pulse_packet_ids"] == ()
    assert baseline["packet_count"] == baseline["packet_budget"]

    coordinate_names = {
        (f"{site.lat:.6f}", f"{site.lon:.6f}"): site.name
        for site in builder.SCENE_A_SITES
    }
    expected_hotspots = set(baseline["hotspot_routes"])
    assert expected_hotspots == EXPECTED_A_HOTSPOTS

    for multiplier, result in built.items():
        assert result["background_packet_ids"] == base_ids
        assert result["background_packet_count"] == base_count
        assert result["packet_count"] == result["packet_budget"]
        projection = _background_projection(result["trace_path"], base_count)
        assert projection == baseline_projection
        assert hashlib.sha256(projection).hexdigest() \
            == baseline["background_packet_sha256"]

        rows = _raw_rows(result["trace_path"])
        pulse_ids = set(result["pulse_packet_ids"])
        assert set(result["pulse_packet_ids"]) == set(
            range(base_count + 1, result["packet_count"] + 1))
        for row in rows:
            emit_time_s = float(row["emit_time_s"])
            assert 0.2 <= emit_time_s < 7.5
            assert float(row["deadline_at_s"]) == pytest.approx(
                float(row["research_deadline_at_s"]))
            assert float(row["deadline_at_s"]) == pytest.approx(
                emit_time_s + 0.240)
            if int(row["packet_id"]) in pulse_ids:
                assert 2.0 <= emit_time_s < 3.5
                assert _route_for(row, coordinate_names) in expected_hotspots

    assert built[1.6]["pulse_packet_count"] > 0
    assert built[2.5]["pulse_packet_count"] > built[1.6]["pulse_packet_count"]
    assert 13_000 <= built[2.5]["packet_count"] <= 15_000
    assert built[2.5]["pulse_packet_sha256"] != baseline["pulse_packet_sha256"]


@pytest.mark.parametrize(
    ("scene", "expected_count_range", "expected_sites"),
    [
        ("A", (13_000, 15_000), 12),
        ("B", (12_500, 14_500), 24),
    ],
)
def test_native_compile_preserves_deadline_and_budget(
        builder, tmp_path, scene, expected_count_range, expected_sites):
    result = builder.build(
        tmp_path / f"scene-{scene}", scene=scene, seed=101, multiplier=2.5,
        interval=0.05)
    low, high = expected_count_range
    assert low <= result["packet_count"] <= high
    assert len(result["background_packet_ids"]) == \
        result["background_packet_count"]
    assert len(result["pulse_packet_ids"]) == result["pulse_packet_count"]
    assert result["config"]["execution"]["max_packets"] == \
        result["packet_count"]
    assert len(result["config"]["endpoints"]["sites"]) == expected_sites

    resolved = config.resolve_config(result["config"])
    compiled_dir = tmp_path / f"compiled-{scene}"
    manifest = trace.compile_trace(resolved, str(compiled_dir))
    assert manifest["offered_packets"] == result["packet_count"]
    assert manifest["drain_s"] == pytest.approx(2.0)
    assert manifest["trace_schema"] == trace.TRACE_SCHEMA_V2
    assert manifest["provenance_contract"]["source"]["sha256"] == \
        result["trace_sha256"]

    compiled_rows = trace.load_trace(
        str(compiled_dir / "trace.csv"),
        horizon_s=9.5,
        max_packets=result["packet_budget"],
    )
    assert len(compiled_rows) == result["packet_count"]
    assert all(
        row["deadline_at_s"] == pytest.approx(row["research_deadline_at_s"])
        for row in compiled_rows
    )


def test_repeat_build_and_cli_are_deterministic(builder, tmp_path):
    first = builder.build(
        tmp_path / "repeat-1", scene="A", seed=7, multiplier=1.0,
        interval=0.01, offered_mbps=5.0)
    second = builder.build(
        tmp_path / "repeat-2", scene="A", seed=7, multiplier=1.0,
        interval=0.01, offered_mbps=5.0)
    assert first["trace_path"].read_bytes() == second["trace_path"].read_bytes()
    assert first["background_packet_sha256"] \
        == second["background_packet_sha256"]
    assert first["pulse_packet_sha256"] == second["pulse_packet_sha256"]
    assert first["trace_sha256"] == second["trace_sha256"]
    assert first["pulse_packet_count"] == 0
    assert first["config"]["links"]["ge_enabled"] is False
    rows = _raw_rows(first["trace_path"])
    for start, end in ((0.2, 2.0), (2.0, 3.5), (3.5, 7.5)):
        assert any(start <= float(row["emit_time_s"]) < end for row in rows)
    no_burst_manifest = trace.compile_trace(
        config.resolve_config(first["config"]),
        str(tmp_path / "no-burst-compiled"),
    )
    assert no_burst_manifest["offered_packets"] == first["packet_count"]

    cli_out = tmp_path / "cli"
    package_root = Path(CODE.__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(package_root), env.get("PYTHONPATH", "")) if item)
    completed = subprocess.run(
        [
            sys.executable, "-B", str(SCRIPT_PATH),
            "--out", str(cli_out), "--scene", "A", "--seed", "7",
            "--multiplier", "1.0", "--interval", "0.10",
            "--offered-mbps", "5.0",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    summary = json.loads(completed.stdout)
    assert summary["trace_path"].endswith("/three-phase-demand.csv")
    assert summary["background_packet_id_range"][0] == 1
    assert summary["pulse_packet_count"] == 0
    assert summary["pulse_packet_id_range"] == []
    assert summary["background_packet_sha256"] == \
        first["background_packet_sha256"]
    assert Path(summary["trace_path"]).is_file()


@pytest.mark.parametrize("interval", [0.01, 0.05, 0.10])
def test_only_real_control_intervals_are_accepted(builder, tmp_path, interval):
    result = builder.build(
        tmp_path / f"interval-{interval}",
        scene="A",
        seed=101,
        interval=interval,
        offered_mbps=0.5,
    )
    assert result["config"]["control_plane"]["advertise_interval_s"] == interval
    with pytest.raises(ValueError, match="real control emission intervals"):
        builder.build(
            tmp_path / "bad-interval", scene="A", seed=101, interval=0.02)

    with pytest.raises(ValueError, match="multiplier"):
        builder.build(
            tmp_path / "bad-multiplier", scene="A", seed=101, multiplier=0.99)
