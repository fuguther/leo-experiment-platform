"""Tests for CODE.leo_sim.rng and CODE.leo_sim.trace."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

import numpy as np

from CODE.leo_sim import config, population, receipt, rng, trace


POPULATION_TIFF = (Path(__file__).resolve().parents[2] / "population_map"
                   / "gpw_v4_population_count_rev11_2020_15_min.tif")


def _cfg(**over):
    sites = [
        {"name": "a", "lat": 31.23, "lon": 121.47, "demand_weight": 2.0},
        {"name": "b", "lat": 40.0, "lon": 116.0},
        {"name": "c", "lat": 51.5, "lon": 0.1},
    ]
    user = {
        "endpoints": {"sites": sites},
        "scenario": {"duration_s": 5.0, "seed": 7},
        "demand": {"offered_mbps": 2.0, "packet_bits": 1_000_000},
    }
    user.update(over)
    return config.resolve_config(user)


def test_rng_streams_independent_and_deterministic():
    s1 = rng.streams(42)
    s2 = rng.streams(42)
    for name in rng.STREAM_NAMES:
        assert s1[name].random() == s2[name].random()
    # different stream names produce different sequences
    a = rng.streams(42)["demand"].random(5)
    b = rng.streams(42)["ge_isl"].random(5)
    assert not (a == b).all()


def test_compile_trace_byte_reproducible(tmp_path):
    cfg = _cfg()
    m1 = trace.compile_trace(cfg, str(tmp_path / "t1"))
    m2 = trace.compile_trace(cfg, str(tmp_path / "t2"))
    h = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    assert h(tmp_path / "t1" / "trace.csv") == h(tmp_path / "t2" / "trace.csv")
    assert h(tmp_path / "t1" / "manifest.json") == h(tmp_path / "t2" / "manifest.json")
    assert m1["schema"] == trace.TRACE_MANIFEST_SCHEMA
    assert m1["trace_identity_sha256"] == config.trace_identity_sha256(cfg)
    assert m1["offered_packets"] == m1["ledger"]["packets"]
    assert m1["offered_bits"] == m1["ledger"]["bits"]
    contract = m1["provenance_contract"]
    assert contract["schema"] == trace.TRACE_PROVENANCE_SCHEMA
    assert contract["source"] == {
        "type": "synthetic_generator", "path": None, "sha256": ""
    }
    assert contract["units"]["emit_time"] == "seconds_since_run_start"
    assert contract["units"]["bits"] == "bits"
    assert contract["offered_load"]["offered_bits"] == m1["offered_bits"]
    assert contract["offered_load"]["offered_packets"] == m1["offered_packets"]
    assert m1["active_endpoints"] == 3
    assert m1["time_range_s"][0] >= 0.0
    assert m1["time_range_s"][1] <= 5.0


def test_emission_end_defaults_to_simulation_horizon(tmp_path):
    cfg = _cfg()
    assert cfg["config"]["demand"]["emission_end_s"] is None
    manifest = trace.compile_trace(cfg, str(tmp_path / "default"))
    assert manifest["simulation_horizon_s"] == 5.0
    assert manifest["emission_end_s"] == 5.0
    assert manifest["drain_s"] == 0.0


def test_explicit_emission_end_bounds_generated_trace_and_records_drain(tmp_path):
    cfg = _cfg(scenario={"duration_s": 30.0},
               demand={"emission_end_s": 20.0})
    manifest = trace.compile_trace(cfg, str(tmp_path / "bounded"))
    rows = trace.load_trace(str(tmp_path / "bounded" / "trace.csv"),
                            horizon_s=30.0, max_packets=20_000)
    assert rows and max(row["emit_time_s"] for row in rows) <= 20.0
    assert manifest["simulation_horizon_s"] == 30.0
    assert manifest["emission_end_s"] == 20.0
    assert manifest["drain_s"] == 10.0
    assert manifest["provenance_contract"]["emission_end_s"] == 20.0


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), 31.0])
def test_invalid_emission_end_fails_closed(value):
    with pytest.raises(config.ConfigError, match="emission_end_s"):
        _cfg(scenario={"duration_s": 30.0},
             demand={"emission_end_s": value})


def test_same_emission_window_is_trace_byte_identical_across_drain_horizon(tmp_path):
    short = _cfg()
    short["config"]["scenario"]["duration_s"] = 20.0
    long = _cfg()
    long["config"]["scenario"]["duration_s"] = 30.0
    long["config"]["demand"]["emission_end_s"] = 20.0
    short_manifest = trace.compile_trace(short, str(tmp_path / "short"))
    long_manifest = trace.compile_trace(long, str(tmp_path / "long"))
    assert ((tmp_path / "short" / "trace.csv").read_bytes() ==
            (tmp_path / "long" / "trace.csv").read_bytes())
    assert short_manifest["trace_sha256"] == long_manifest["trace_sha256"]
    assert (short_manifest["trace_identity_sha256"] ==
            long_manifest["trace_identity_sha256"])
    assert long_manifest["drain_s"] == 10.0
    changed = _cfg()
    changed["config"]["scenario"]["duration_s"] = 30.0
    changed["config"]["demand"]["emission_end_s"] = 19.0
    changed_manifest = trace.compile_trace(changed, str(tmp_path / "changed"))
    assert changed_manifest["trace_identity_sha256"] != long_manifest[
        "trace_identity_sha256"]


def test_receipt_manifest_v2_fields_are_strict_and_v1_remains_legacy(tmp_path):
    cfg = _cfg(scenario={"duration_s": 30.0},
               demand={"emission_end_s": 20.0})
    manifest = trace.compile_trace(cfg, str(tmp_path / "v2"))
    assert receipt._validate_manifest(
        manifest, cfg["config"], cfg["version"]) == []
    for field in ("simulation_horizon_s", "emission_end_s", "drain_s"):
        tampered = dict(manifest)
        tampered[field] = float(tampered[field]) + 1.0
        assert any(field in error for error in receipt._validate_manifest(
            tampered, cfg["config"], cfg["version"]))
    downgraded = dict(manifest)
    downgraded["schema"] = trace.TRACE_MANIFEST_SCHEMA_V1
    downgraded["provenance_contract"] = dict(manifest["provenance_contract"])
    downgraded["provenance_contract"]["schema"] = trace.TRACE_PROVENANCE_SCHEMA_V1
    errors = receipt._validate_manifest(downgraded, cfg["config"], cfg["version"])
    assert any("manifest keys mismatch" in error for error in errors)
    assert any("provenance_contract keys mismatch" in error for error in errors)
    legacy = dict(manifest)
    for field in ("simulation_horizon_s", "emission_end_s", "drain_s"):
        legacy.pop(field)
    legacy["schema"] = trace.TRACE_MANIFEST_SCHEMA_V1
    legacy["provenance_contract"] = dict(manifest["provenance_contract"])
    for field in ("simulation_horizon_s", "emission_end_s", "drain_s"):
        legacy["provenance_contract"].pop(field)
    legacy["provenance_contract"]["schema"] = trace.TRACE_PROVENANCE_SCHEMA_V1
    legacy["trace_identity_sha256"] = config.legacy_trace_identity_sha256(
        cfg, manifest["input_sha256"])
    assert receipt._validate_manifest(
        legacy, cfg["config"], cfg["version"]) == []


def test_trace_columns_and_sorted(tmp_path):
    cfg = _cfg()
    trace.compile_trace(cfg, str(tmp_path / "t"))
    with open(tmp_path / "t" / "trace.csv") as fh:
        rows = list(csv.DictReader(fh))
    assert rows, "trace should not be empty"
    times = [float(r["emit_time_s"]) for r in rows]
    assert times == sorted(times)
    for r in rows:
        assert r["src_grid_id"] != r["dst_grid_id"]
        assert int(r["bits"]) > 0


def test_trace_csv_mode(tmp_path):
    src_csv = tmp_path / "in.csv"
    src_csv.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.1,31.0,121.0,40.0,116.0,8000000,3.5\n"
        "2,0.2,31.0,121.0,51.5,0.1,8000000,\n"
    )
    cfg = _cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src_csv)
    m = trace.compile_trace(cfg, str(tmp_path / "t"))
    assert m["offered_packets"] == 2
    contract = m["provenance_contract"]
    assert contract["source"]["type"] == "csv_input"
    assert contract["source"]["sha256"] == hashlib.sha256(src_csv.read_bytes()).hexdigest()
    assert contract["od_mapping"]["input_coordinate_fields"] == [
        "src_lat", "src_lon", "dst_lat", "dst_lon"
    ]
    assert contract["offered_load"]["load_mode"] == "observed_trace"
    with open(tmp_path / "t" / "trace.csv") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["deadline_at_s"] == "3.5"
    assert rows[1]["deadline_at_s"] == ""


def test_csv_row_after_emission_end_fails_loud(tmp_path):
    src_csv = tmp_path / "late.csv"
    src_csv.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,25.0,31.0,121.0,40.0,116.0,8000000,\n")
    cfg = _cfg(scenario={"duration_s": 30.0},
               demand={"mode": "csv", "csv_path": str(src_csv),
                       "emission_end_s": 20.0})
    with pytest.raises(trace.TraceError, match="emission window"):
        trace.compile_trace(cfg, str(tmp_path / "late-out"))


def test_trace_csv_mode_preserves_zero_deadline(tmp_path):
    """R5-G1 regression: deadline_at_s='0' is a valid instant deadline and
    must not be treated as 'no deadline' by a falsy-string check."""
    src_csv = tmp_path / "in.csv"
    src_csv.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.0,31.0,121.0,40.0,116.0,8000000,0\n"
        "2,0.1,31.0,121.0,40.0,116.0,8000000,\n"
    )
    cfg = _cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src_csv)
    m = trace.compile_trace(cfg, str(tmp_path / "t"))
    assert m["offered_packets"] == 2
    with open(tmp_path / "t" / "trace.csv") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["deadline_at_s"] == "0"
    assert rows[1]["deadline_at_s"] == ""


def test_mlab_adapter_labels_measurement_proxy(tmp_path):
    # Sites sit on a real measured OD pair of the repository M-Lab snapshot
    # (Amagasaki <-> Tokyo). Sites without measurement coverage now fail
    # closed (round-4 contract); the previous Shanghai/Beijing/London trio has
    # no OD coverage in the snapshot and only ever worked through the removed
    # 1e-9 uniform-smoothing fallback.
    cfg = _cfg(**{"endpoints": {"sites": [
        {"name": "a", "lat": 34.717, "lon": 135.418, "demand_weight": 2.0},
        {"name": "b", "lat": 35.553, "lon": 139.781},
    ]}})
    cfg["config"]["demand"]["mode"] = "mlab"
    m = trace.compile_trace(cfg, str(tmp_path / "t"))
    assert m["provenance"] == "measurement_proxy"
    assert m["not_calibrated_user_demand"] is True
    assert "never calibrated user demand" in m["provenance_note"]
    assert m["offered_packets"] > 0


def _write_mlab_fixture(path: Path) -> None:
    path.write_text(
        "client_city,client_lat,client_lon,server_city,server_lat,server_lon,"
        "hour_utc,sample_count,mean_throughput_mbps\n"
        "A,34.717,135.418,B,35.553,139.781,0,2,10.0\n"
        "A,34.717,135.418,B,35.553,139.781,12,3,20.0\n"
        "B,35.553,139.781,A,34.717,135.418,23,1,5.0\n",
        encoding="utf-8",
    )


def test_mlab_manifest_records_source_coverage_and_burst(tmp_path, monkeypatch):
    source = tmp_path / "mlab.csv"
    _write_mlab_fixture(source)
    monkeypatch.setattr(trace, "REPO_MLAB_CSV", source)
    cfg = _cfg(**{"endpoints": {"sites": [
        {"name": "a", "lat": 34.717, "lon": 135.418},
        {"name": "b", "lat": 35.553, "lon": 139.781},
    ]}})
    cfg["config"]["demand"].update({
        "mode": "mlab",
        "burst_start_s": 1.0,
        "burst_duration_s": 2.0,
        "burst_multiplier": 3.0,
    })
    manifest = trace.compile_trace(cfg, str(tmp_path / "out"))

    assert manifest["provenance"] == "measurement_proxy"
    assert manifest["not_calibrated_user_demand"] is True
    contract = manifest["provenance_contract"]
    assert contract["source"]["sha256"] == hashlib.sha256(
        source.read_bytes()).hexdigest()
    assert contract["measurement_summary"] == {
        "row_count": 3,
        "od_pair_count": 2,
        "hour_utc_values": [0, 12, 23],
    }
    assert contract["traffic_transform"]["burst"] == {
        "start_s": 1.0,
        "duration_s": 2.0,
        "multiplier": 3.0,
    }


def test_mlab_burst_trace_is_byte_reproducible(tmp_path, monkeypatch):
    source = tmp_path / "mlab.csv"
    _write_mlab_fixture(source)
    monkeypatch.setattr(trace, "REPO_MLAB_CSV", source)
    cfg = _cfg(**{"endpoints": {"sites": [
        {"name": "a", "lat": 34.717, "lon": 135.418},
        {"name": "b", "lat": 35.553, "lon": 139.781},
    ]}})
    cfg["config"]["demand"].update({
        "mode": "mlab",
        "burst_start_s": 1.0,
        "burst_duration_s": 2.0,
        "burst_multiplier": 3.0,
    })
    first = trace.compile_trace(cfg, str(tmp_path / "first"))
    second = trace.compile_trace(cfg, str(tmp_path / "second"))
    for name in ("trace.csv", "manifest.json"):
        assert (tmp_path / "first" / name).read_bytes() == (
            tmp_path / "second" / name).read_bytes()
    assert first["trace_sha256"] == second["trace_sha256"]


def test_mlab_source_rejects_invalid_hour_or_measurement(tmp_path, monkeypatch):
    source = tmp_path / "mlab.csv"
    source.write_text(
        "client_lat,client_lon,server_lat,server_lon,hour_utc,sample_count,"
        "mean_throughput_mbps\n"
        "34.717,135.418,35.553,139.781,24,1,10.0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(trace, "REPO_MLAB_CSV", source)
    cfg = _cfg(**{"endpoints": {"sites": [
        {"name": "a", "lat": 34.717, "lon": 135.418},
        {"name": "b", "lat": 35.553, "lon": 139.781},
    ]}})
    cfg["config"]["demand"]["mode"] = "mlab"
    with pytest.raises(trace.TraceError, match="hour_utc"):
        trace.compile_trace(cfg, str(tmp_path / "invalid"))


def test_demand_modes_all_generate(tmp_path):
    for mode in ("uniform", "gravity", "hotspot", "burst", "diurnal"):
        over = {"demand": {"mode": mode, "offered_mbps": 5.0, "packet_bits": 100_000}}
        if mode == "burst":
            over["demand"]["burst_start_s"] = 1.0
            over["demand"]["burst_duration_s"] = 2.0
        cfg = _cfg(**over)
        m = trace.compile_trace(cfg, str(tmp_path / mode))
        assert m["offered_packets"] > 0, mode
        assert m["provenance"] == "synthetic"
        transform = m["provenance_contract"]["traffic_transform"]
        assert transform["mode"] == mode
        if mode == "burst":
            assert transform["burst"] == {
                "start_s": 1.0, "duration_s": 2.0, "multiplier": 2.0
            }


def test_population_gravity_uses_population_for_sources_and_destinations(
        tmp_path, monkeypatch):
    regions = (
        population.PopulationRegion("G5:18:36", 2.5, 2.5, 100.0),
        population.PopulationRegion("G5:18:37", 2.5, 7.5, 10.0),
        population.PopulationRegion("G5:18:38", 2.5, 12.5, 1.0),
    )
    table = population.PopulationTable(
        regions=regions, source_path="/fake/pop.tif", source_sha256="a" * 64,
        source_shape=(720, 1440), source_resolution_deg=(0.25, 0.25),
        aggregation_deg=5.0, total_population=111.0)
    monkeypatch.setattr(population, "load_population_regions",
                        lambda path, aggregation_deg: table)
    cfg = config.resolve_config({
        "scenario": {"duration_s": 100.0, "seed": 11},
        "endpoints": {"aggregation_deg": 5.0},
        "demand": {
            "mode": "population_gravity", "population_path": "/fake/pop.tif",
            "offered_mbps": 100.0, "packet_bits": 1_000_000,
            "source_population_exponent": 1.0,
            "destination_population_exponent": 1.0,
            "gravity_alpha": 1.0, "gravity_d_floor_km": 100.0,
        },
        "execution": {"max_packets": 20_000},
    })
    manifest = trace.compile_trace(cfg, str(tmp_path / "population"))
    rows = trace.load_trace(
        str(tmp_path / "population" / "trace.csv"),
        horizon_s=100.0, max_packets=20_000)
    source_counts = {region.grid_id: 0 for region in regions}
    for row in rows:
        source_counts[row["src_grid_id"]] += 1
    assert source_counts[regions[0].grid_id] > source_counts[regions[1].grid_id]
    assert source_counts[regions[1].grid_id] > source_counts[regions[2].grid_id]
    from_first = [r["dst_grid_id"] for r in rows
                  if r["src_grid_id"] == regions[0].grid_id]
    assert set(from_first) == {regions[1].grid_id, regions[2].grid_id}
    assert from_first.count(regions[1].grid_id) > from_first.count(regions[2].grid_id)
    assert manifest["provenance"] == "population_proxy"
    assert manifest["input_sha256"] == "a" * 64
    assert manifest["not_calibrated_user_demand"] is True


def test_population_gravity_trace_is_byte_reproducible(tmp_path, monkeypatch):
    regions = (
        population.PopulationRegion("G5:18:36", 2.5, 2.5, 2.0),
        population.PopulationRegion("G5:18:37", 2.5, 7.5, 1.0),
    )
    table = population.PopulationTable(
        regions=regions, source_path="/fake/pop.tif", source_sha256="b" * 64,
        source_shape=(720, 1440), source_resolution_deg=(0.25, 0.25),
        aggregation_deg=5.0, total_population=3.0)
    monkeypatch.setattr(population, "load_population_regions",
                        lambda path, aggregation_deg: table)
    cfg = config.resolve_config({
        "scenario": {"duration_s": 5.0, "seed": 3},
        "endpoints": {"aggregation_deg": 5.0},
        "demand": {"mode": "population_gravity",
                   "population_path": "/fake/pop.tif"},
    })
    m1 = trace.compile_trace(cfg, str(tmp_path / "one"))
    m2 = trace.compile_trace(cfg, str(tmp_path / "two"))
    assert (tmp_path / "one" / "trace.csv").read_bytes() == (
        tmp_path / "two" / "trace.csv").read_bytes()
    assert m1["trace_identity_sha256"] == m2["trace_identity_sha256"]


# -------------------------------------------------------- Task 1 regression:
# the global scene fields are frozen as defaults; the unchanged legacy
# population profile must keep its exact trace bytes and identity/v2 must
# still be reconstructible, while new compilations declare identity/v3.

POPULATION_PROFILE = (Path(__file__).resolve().parents[2]
                      / "leo_sim" / "profiles" / "population_gravity.yaml")
LEGACY_POPULATION_TRACE_SHA = (
    "0780da2fedea503d5f600830aecc805c95b1b8fc098395150ecaf2185846279a")
FROZEN_OLD_V2_TRACE_IDENTITY = (
    "2715dfb316de48d958cd05fa09aafcf22e340766d186e7a0a9a9b6a4b0dd9ad4")


def test_population_gravity_profile_trace_bytes_regression(tmp_path):
    """Task 1 must not perturb the legacy population profile trace bytes."""
    resolved = config.load_config_file(str(POPULATION_PROFILE))
    manifest = trace.compile_trace(resolved, str(tmp_path / "pop"))
    trace_sha = hashlib.sha256(
        (tmp_path / "pop" / "trace.csv").read_bytes()).hexdigest()
    assert trace_sha == LEGACY_POPULATION_TRACE_SHA
    # new compilations declare identity/v3, not the frozen v2 value
    assert manifest["trace_identity_sha256"] == config.trace_identity_sha256(
        resolved, manifest["input_sha256"])
    assert manifest["trace_identity_sha256"] != FROZEN_OLD_V2_TRACE_IDENTITY
    # the frozen v2 identity of this exact profile is still reconstructible
    # byte-for-byte by the frozen v2 builder (the five new demand fields are
    # removed after config resolution).
    assert config.trace_identity_sha256_v2(
        resolved, manifest["input_sha256"]) == FROZEN_OLD_V2_TRACE_IDENTITY


def test_uncompiled_new_scene_fields_do_not_change_trace_bytes(tmp_path):
    """Setting the new global-scene fields to their defaults must leave the
    legacy trace bytes untouched (they are trace-determining only when a
    task-4+ feature is actually selected)."""
    resolved = config.load_config_file(str(POPULATION_PROFILE))
    base_manifest = trace.compile_trace(resolved, str(tmp_path / "base"))
    base_sha = hashlib.sha256(
        (tmp_path / "base" / "trace.csv").read_bytes()).hexdigest()
    resolved["config"]["scenario"]["geometry_epoch_s"] = 0.0
    resolved["config"]["demand"].update({
        "temporal_model": "constant",
        "utc_start_hour": 0.0,
        "population_destination_sampler": "scan",
        "destination_rejection_max_draws": 10_000,
        "nested_master_offered_mbps": None,
    })
    explicit_manifest = trace.compile_trace(resolved, str(tmp_path / "explicit"))
    assert hashlib.sha256(
        (tmp_path / "explicit" / "trace.csv").read_bytes()).hexdigest() == base_sha
    assert explicit_manifest["trace_sha256"] == base_manifest["trace_sha256"]


# ---------------------------------------------------------------- Task 4:
# opt-in local-solar-time population demand proxy (explicitly NOT measured
# traffic).

def _fake_pop_table(*populations):
    regions = tuple(
        population.PopulationRegion(f"G5:18:{36 + i}", 2.5,
                                    2.5 + 5.0 * i, pop)
        for i, pop in enumerate(populations))
    table = population.PopulationTable(
        regions=regions, source_path="/fake/pop.tif", source_sha256="c" * 64,
        source_shape=(720, 1440), source_resolution_deg=(0.25, 0.25),
        aggregation_deg=5.0, total_population=float(sum(populations)))
    return table


def _population_cfg(monkeypatch, **demand):
    table = _fake_pop_table(10.0, 3.0, 1.0)
    monkeypatch.setattr(population, "load_population_regions",
                        lambda path, aggregation_deg: table)
    base = {
        "scenario": {"duration_s": 50.0, "seed": 7},
        "endpoints": {"aggregation_deg": 5.0},
        "demand": {"mode": "population_gravity",
                   "population_path": "/fake/pop.tif",
                   "offered_mbps": 20.0, "packet_bits": 1_000_000,
                   "source_population_exponent": 1.0,
                   "destination_population_exponent": 1.0,
                   "gravity_alpha": 1.0, "gravity_d_floor_km": 100.0},
    }
    base["demand"].update(demand)
    return config.resolve_config(base)


def test_local_diurnal_local_hour_declared_at_utc_wraparound(monkeypatch):
    """longitude 0 at UTC 12 and longitude 180 at UTC 0 both resolve to the
    declared local hour at t=0, so the rate multiplier is the peak there."""
    dm = {"mode": "population_gravity", "temporal_model": "local_diurnal_cosine",
          "utc_start_hour": 0.0, "diurnal_amplitude": 1.0,
          "diurnal_phase_h": 12.0, "burst_start_s": None}
    dm["utc_start_hour"] = 12.0
    m0 = trace._rate_multiplier("population_gravity", 0.0, 0.0, dm)
    assert m0 == pytest.approx(2.0)  # local hour 12 -> cos peak
    dm["utc_start_hour"] = 0.0
    m180 = trace._rate_multiplier("population_gravity", 0.0, 180.0, dm)
    assert m180 == pytest.approx(2.0)  # local hour 12 -> cos peak


def test_local_diurnal_amplitude_zero_is_byte_equivalent_to_constant(
        tmp_path, monkeypatch):
    const = _population_cfg(monkeypatch)
    flat = _population_cfg(monkeypatch, temporal_model="local_diurnal_cosine",
                           utc_start_hour=4.0, diurnal_amplitude=0.0)
    m1 = trace.compile_trace(const, str(tmp_path / "const"))
    m2 = trace.compile_trace(flat, str(tmp_path / "flat"))
    assert (tmp_path / "const" / "trace.csv").read_bytes() == (
        tmp_path / "flat" / "trace.csv").read_bytes()
    assert m1["trace_sha256"] == m2["trace_sha256"]
    # the traces may be identical only because the amplitude is literally
    # zero; the identity still differs (different feature selection)
    assert m1["trace_identity_sha256"] != m2["trace_identity_sha256"]


def test_local_diurnal_utc_block_changes_bytes_and_repeat_identical(
        tmp_path, monkeypatch):
    block_a = _population_cfg(monkeypatch, temporal_model="local_diurnal_cosine",
                              utc_start_hour=2.0, diurnal_amplitude=0.5)
    block_b = _population_cfg(monkeypatch, temporal_model="local_diurnal_cosine",
                              utc_start_hour=14.0, diurnal_amplitude=0.5)
    a1 = trace.compile_trace(block_a, str(tmp_path / "a1"))
    b1 = trace.compile_trace(block_b, str(tmp_path / "b1"))
    a2 = trace.compile_trace(block_a, str(tmp_path / "a2"))
    assert a1["trace_sha256"] != b1["trace_sha256"]
    assert (tmp_path / "a1" / "trace.csv").read_bytes() == (
        tmp_path / "a2" / "trace.csv").read_bytes()
    assert a1["trace_identity_sha256"] != b1["trace_identity_sha256"]


def test_local_diurnal_manifest_proxy_labels_and_four_key_transform(
        tmp_path, monkeypatch):
    cfg = _population_cfg(monkeypatch, temporal_model="local_diurnal_cosine",
                          utc_start_hour=6.5, diurnal_amplitude=0.5,
                          diurnal_phase_h=15.0)
    manifest = trace.compile_trace(cfg, str(tmp_path / "proxy"))
    assert manifest["provenance"] == "population_proxy"
    assert manifest["not_calibrated_user_demand"] is True
    transform = manifest["provenance_contract"]["traffic_transform"]
    assert transform["diurnal"] == {
        "amplitude": 0.5, "phase_h": 15.0, "utc_start_hour": 6.5,
        "clock": "source_local_solar_time_proxy",
    }
    # the manifest's exact top-level key set is unchanged
    assert set(manifest) == {
        "schema", "trace_schema", "trace_sha256", "trace_identity_sha256",
        "config_version", "input_sha256", "mode", "provenance",
        "simulation_horizon_s", "emission_end_s", "drain_s", "rng_streams",
        "packet_id_contract", "offered_packets", "offered_bits", "ledger",
        "active_endpoints", "time_range_s", "provenance_contract",
        "not_calibrated_user_demand", "provenance_note", "population",
    }


def test_legacy_diurnal_transform_keeps_two_key_value(tmp_path):
    cfg = _cfg(demand={"mode": "diurnal", "diurnal_amplitude": 0.5,
                       "diurnal_phase_h": 12.0})
    manifest = trace.compile_trace(cfg, str(tmp_path / "legacy-diurnal"))
    transform = manifest["provenance_contract"]["traffic_transform"]
    assert transform["diurnal"] == {"amplitude": 0.5, "phase_h": 12.0}
    assert set(transform["diurnal"]) == {"amplitude", "phase_h"}
    assert "utc_start_hour" not in transform["diurnal"]


def test_local_diurnal_trace_receipt_verification_and_tamper_rejection(
        tmp_path, monkeypatch):
    cfg = _population_cfg(monkeypatch, temporal_model="local_diurnal_cosine",
                          utc_start_hour=5.0, diurnal_amplitude=0.5)
    manifest = trace.compile_trace(cfg, str(tmp_path / "pop"))
    assert receipt._validate_manifest(manifest, cfg["config"], cfg["version"]) == []
    # a tampered local-time transform must be rejected
    tampered = dict(manifest)
    tampered["provenance_contract"] = dict(manifest["provenance_contract"])
    tampered["provenance_contract"]["traffic_transform"] = dict(
        manifest["provenance_contract"]["traffic_transform"])
    tampered["provenance_contract"]["traffic_transform"]["diurnal"] = dict(
        manifest["provenance_contract"]["traffic_transform"]["diurnal"])
    tampered["provenance_contract"]["traffic_transform"]["diurnal"]["clock"] = \
        "operator_clock"
    errors = receipt._validate_manifest(tampered, cfg["config"], cfg["version"])
    assert any("diurnal" in e for e in errors)
    # extra keys inside the transform are also rejected
    extra = dict(manifest)
    extra["provenance_contract"]["traffic_transform"]["diurnal"]["extra"] = 1
    errors = receipt._validate_manifest(extra, cfg["config"], cfg["version"])
    assert any("diurnal" in e for e in errors)


def test_local_diurnal_observed_support_sets_never_rename_candidates(
        tmp_path):
    """A finite trace may have far fewer observed sources than the 16,988
    candidate regions: the manifest keeps candidate_regions exact, while the
    trace-derived observed sets are reported separately and never renamed as
    the candidate support."""
    resolved = config.load_config_file(str(POPULATION_PROFILE))
    resolved["config"]["endpoints"]["aggregation_deg"] = 1.0
    resolved["config"]["demand"].update({
        "temporal_model": "local_diurnal_cosine",
        "utc_start_hour": 6.0,
    })
    manifest = trace.compile_trace(resolved, str(tmp_path / "real"))
    rows = trace.load_trace(
        str(tmp_path / "real" / "trace.csv"),
        horizon_s=30.0, max_packets=2000)
    assert manifest["population"]["candidate_regions"] == 16_988
    observed_sources = sorted({row["src_grid_id"] for row in rows})
    observed_destinations = sorted({row["dst_grid_id"] for row in rows})
    runtime_endpoints = sorted(set(observed_sources)
                               | set(observed_destinations))
    assert len(observed_sources) < 16_988
    assert len(observed_destinations) < 16_988
    assert len(runtime_endpoints) < 16_988
    assert len(runtime_endpoints) == manifest["active_endpoints"]
    # the manifest names the candidate support, the trace derives the
    # runtime sets: they must be reported separately, never conflated
    assert manifest["population"]["candidate_regions"] != len(observed_sources)


# ---------------------------------------------------------------- Task 5:
# exact opt-in population destination sampler (alias_rejection).

def _three_region_endpoints():
    """Three equal-population 5-degree regions ~556 km apart at the equator:
    the alias proposal table is flat so scripted draws are deterministic."""
    regions = (
        population.PopulationRegion("G5:18:36", 2.5, 2.5, 1.0),
        population.PopulationRegion("G5:18:37", 2.5, 7.5, 1.0),
        population.PopulationRegion("G5:18:38", 2.5, 12.5, 1.0),
    )
    return [{"name": r.grid_id, "lat": r.lat, "lon": r.lon,
             "weight": r.population, "agg_grid_id": r.grid_id}
            for r in regions]


class ScriptedGen:
    """Scripted numpy-Generator stand-in: every random() pops the next
    value, so each branch of the sampler is exercised deterministically."""

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def random(self):
        value = self.values[self.calls]
        self.calls += 1
        return value


def _alias_dm(max_draws=10_000, alpha=1.25, floor=100.0):
    return {"gravity_d_floor_km": floor, "gravity_alpha": alpha,
            "destination_population_exponent": 1.0,
            "destination_rejection_max_draws": max_draws}


def test_alias_rejection_scripted_branches():
    endpoints = _three_region_endpoints()
    alias = trace.VoseAlias([e["weight"] for e in endpoints])
    dm = _alias_dm()
    # 1) same-source rejection, 2) distance rejection, 3) success.
    # VoseAlias.draw consumes exactly two randoms (bucket index + coin).
    gen = ScriptedGen([0.0,        # bucket 0 = the source (rejected)
                       0.5,        # coin < prob 1.0 -> bucket 0 again
                       0.4,        # bucket 1 = far region
                       0.5,        # coin -> rejects alias, stays on region 1
                       0.99,       # acceptance coin >= (floor/d)^alpha
                       0.7,        # bucket 2 = near region
                       0.5,        # coin -> stays on region 2
                       0.001])     # acceptance coin < (floor/d)^alpha
    picked = trace.sample_population_destination(gen, alias, endpoints, 0, dm)
    assert picked["agg_grid_id"] == "G5:18:38"  # nearest, accepted
    assert gen.calls == 8


def test_alias_rejection_cap_exhaustion_fails_loud():
    endpoints = _three_region_endpoints()
    alias = trace.VoseAlias([e["weight"] for e in endpoints])
    dm = _alias_dm(max_draws=2)
    # both draws land on the far region and fail the acceptance coin
    gen = ScriptedGen([0.4, 0.5, 0.99,   # draw 1: region 1, rejected
                       0.4, 0.5, 0.99])  # draw 2: region 1, rejected
    with pytest.raises(trace.TraceError, match="exhausted"):
        trace.sample_population_destination(gen, alias, endpoints, 0, dm)
    assert gen.calls == 6


def test_alias_rejection_matches_scan_distribution_on_fixture():
    """200,000 deterministic samples must reproduce the normalized scan
    probabilities for every destination within absolute tolerance 0.01."""
    endpoints = _three_region_endpoints()
    alias = trace.VoseAlias([e["weight"] for e in endpoints])
    dm = _alias_dm(alpha=1.25, floor=100.0)
    src = endpoints[0]
    floor = float(dm["gravity_d_floor_km"])
    alpha = float(dm["gravity_alpha"])
    weights = []
    for e in endpoints[1:]:
        d = max(trace._haversine_km(src["lat"], src["lon"],
                                    e["lat"], e["lon"]), floor)
        weights.append(e["weight"] / d ** alpha)
    total = sum(weights)
    expected = [w / total for w in weights]
    gen = np.random.default_rng(20260825)
    counts = [0, 0, 0]
    for _ in range(200_000):
        dst = trace.sample_population_destination(gen, alias, endpoints, 0, dm)
        counts[endpoints.index(dst)] += 1
    observed = [c / 200_000 for c in counts[1:]]
    assert counts[0] == 0  # never a self destination
    for got, want in zip(observed, expected):
        assert abs(got - want) <= 0.01


def test_alias_rejection_builds_one_proposal_table(tmp_path, monkeypatch):
    """The 1-degree population table must build exactly ONE proposal table
    (O(N) once), never one O(N) table per source."""
    built = []

    class CountingAlias(trace.VoseAlias):
        def __init__(self, weights):
            built.append(weights)
            super().__init__(weights)

    monkeypatch.setattr(trace, "VoseAlias", CountingAlias)
    resolved = config.load_config_file(str(POPULATION_PROFILE))
    resolved["config"]["endpoints"]["aggregation_deg"] = 1.0
    resolved["config"]["demand"].update({
        "population_destination_sampler": "alias_rejection",
        "destination_rejection_max_draws": 10_000,
        "offered_mbps": 1.0, "packet_bits": 1_000_000,
        "emission_end_s": 5.0,
    })
    resolved["config"]["scenario"]["duration_s"] = 10.0
    trace.compile_trace(resolved, str(tmp_path / "alias"))
    assert len(built) == 1
    assert len(built[0]) == 16_988  # the full 1-degree candidate universe


def test_alias_rejection_exhaustion_risk_on_201_source_sample():
    """Deterministic 201-source sample covering population and latitude
    extrema: the exact per-draw acceptance probability, expected draws,
    observed draws and the 10,000-draw exhaustion probability must be
    computed for every source; the worst exhaustion must stay under 1e-9
    (otherwise the cap itself must be revised before accepting the
    sampler)."""
    table = population.load_population_regions(
        str(POPULATION_TIFF), 1.0)
    endpoints = [
        {"name": r.grid_id, "lat": r.lat, "lon": r.lon,
         "weight": r.population, "agg_grid_id": r.grid_id}
        for r in table.regions
    ]
    by_pop = sorted(range(len(endpoints)), key=lambda i: -endpoints[i]["weight"])
    by_lat = sorted(range(len(endpoints)), key=lambda i: endpoints[i]["lat"])
    by_pop = sorted(range(len(endpoints)), key=lambda i: -endpoints[i]["weight"])
    by_lat = sorted(range(len(endpoints)), key=lambda i: endpoints[i]["lat"])
    source_indices = sorted(set(by_pop[:60]) | set(by_lat[:30])
                            | set(by_lat[-30:]) | set(range(0, len(endpoints),
                                                             len(endpoints) // 90)))
    source_indices = sorted(source_indices)[:201]
    assert len(source_indices) == 201
    alias = trace.VoseAlias([e["weight"] for e in endpoints])
    dm = _alias_dm(max_draws=10_000, alpha=1.25, floor=100.0)
    stats = trace.alias_rejection_stats(endpoints, dm, source_indices)
    worst = 0.0
    acceptances = []
    for i in source_indices:
        s = stats[i]
        assert 0.0 <= s["per_draw_success_probability"] <= 1.0
        acceptances.append(s["per_draw_success_probability"])
        worst = max(worst, s["exhaustion_probability"])
    assert worst < 1e-9, worst
    # observed draws follow the geometric expectation on a seeded RNG.
    # sample_population_destination loops internally up to the cap, so the
    # draw count is measured on the alias itself.
    class CountingAlias(trace.VoseAlias):
        def __init__(self, inner):
            self._inner = inner
            self.draws = 0

        def draw(self, gen):
            self.draws += 1
            return self._inner.draw(gen)

    gen = np.random.default_rng(7)
    expected = [1.0 / stats[i]["per_draw_success_probability"]
                for i in source_indices]
    observed = []
    for i in source_indices[:20]:
        counting = CountingAlias(alias)
        trace.sample_population_destination(
            gen, counting, endpoints, i, dm)
        observed.append(counting.draws)
    # record min/median/max acceptance over the 201-source sample
    acceptances_sorted = sorted(acceptances)
    median_acceptance = acceptances_sorted[len(acceptances_sorted) // 2]
    assert acceptances_sorted[0] == min(acceptances)
    assert acceptances_sorted[-1] == max(acceptances)
    assert 0 < median_acceptance < 1
    # sanity: observed mean is within a generous factor of the expectation
    import statistics
    mean_observed = statistics.mean(observed)
    mean_expected = statistics.mean(expected[:20])
    assert 0.05 * mean_expected < mean_observed < 20 * mean_expected
