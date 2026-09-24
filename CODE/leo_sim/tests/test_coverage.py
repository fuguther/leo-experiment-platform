import hashlib
import json
import math

import numpy as np
import pytest

from CODE.leo_sim import coverage, model, population
from CODE.leo_sim.tests.helpers import StaticGeometry


def test_coverage_scan_reports_endpoint_gaps_and_stable_order():
    geo = StaticGeometry(2, visible=lambda sat, _lat, _lon, t:
                         sat == 0 and t < 1.0 or sat == 1 and t >= 1.0)
    endpoints = [
        {"name": "z", "lat": 0.0, "lon": 0.0},
        {"name": "a", "lat": 5.0, "lon": 5.0},
    ]
    got = coverage.scan_coverage(geo, endpoints, horizon_s=2.0, step_s=1.0,
                                 visible_fraction_threshold=0.5)
    assert [item["name"] for item in got["endpoints"]] == ["a", "z"]
    z = got["endpoints"][1]
    assert z["visible_fraction"] == pytest.approx(1.0)
    assert z["first_visible_wait_s"] == 0.0
    assert z["max_no_coverage_gap_s"] == pytest.approx(0.0)
    assert z["visible_satellites"] == {"min": 1, "mean": 1.0, "max": 1}
    assert got["summary"]["endpoints_total"] == 2
    assert got["summary"]["never_visible"] == 0
    assert got["summary"]["threshold_met_fraction"] == 1.0


def test_coverage_scan_rejects_bad_bounds_and_marks_never_visible():
    geo = StaticGeometry(1, visible=lambda *_: False)
    with pytest.raises(coverage.CoverageAuditError, match="step_s"):
        coverage.scan_coverage(geo, [], horizon_s=1.0, step_s=0.0)
    got = coverage.scan_coverage(
        geo, [{"name": "a", "lat": 0.0, "lon": 0.0}],
        horizon_s=1.0, step_s=0.5)
    item = got["endpoints"][0]
    assert item["never_visible"] is True
    assert item["first_visible_wait_s"] is None
    assert item["max_no_coverage_gap_s"] is None


def test_coverage_scan_rejects_unbounded_work_before_allocating_samples():
    geo = StaticGeometry(140, visible=lambda *_: False)
    with pytest.raises(coverage.CoverageAuditError, match="sample_count.*limit"):
        coverage.scan_coverage(geo, [{"name": "a", "lat": 0.0, "lon": 0.0}],
                               horizon_s=2_000_000.0, step_s=0.001)
    with pytest.raises(coverage.CoverageAuditError, match="coverage checks.*limit"):
        coverage.scan_coverage(
            geo,
            [{"name": str(i), "lat": 0.0, "lon": 0.0} for i in range(400)],
            horizon_s=400.0, step_s=0.1)


# ---------------------------------------------------------------- Task 3:
# extended vectorized population-raster coverage audit.

def _population_source(candidate_regions, total_population,
                       source_sha256="a" * 64, aggregation_deg=1.0):
    return {
        "type": "population_raster",
        "source_sha256": source_sha256,
        "aggregation_deg": aggregation_deg,
        "candidate_regions": candidate_regions,
        "total_population": total_population,
    }


def _pop_endpoint(name, lat, lon, population):
    return {"name": name, "lat": lat, "lon": lon, "population": population}


def test_vector_path_matches_scalar_on_small_real_constellation():
    """The vectorized footprint scan must be identical to the scalar
    elevation scan on a small real Constellation (bitwise comparable rows)."""
    geo = model.Constellation(num_satellites=12, num_planes=3,
                              altitude_km=550.0, inclination_deg=53.0,
                              min_elevation_deg=25.0)
    endpoints = [
        _pop_endpoint("G1:0:0", 0.0, 0.0, 10.0),
        _pop_endpoint("G1:1:1", 10.0, 10.0, 20.0),
        _pop_endpoint("G1:2:3", 30.0, -60.0, 5.0),
        _pop_endpoint("G1:1:5", -45.0, 120.0, 8.0),
        _pop_endpoint("G1:2:8", 60.0, 170.0, 3.0),
    ]
    source = _population_source(len(endpoints), 46.0)
    scalar = coverage.scan_coverage(geo, endpoints, horizon_s=3600.0,
                                    step_s=600.0)
    vector = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=3600.0, step_s=600.0,
        endpoint_source=source)
    assert vector["schema"] == "leo-sim-coverage-audit/v2"
    assert [row["name"] for row in vector["endpoints"]] == [
        row["name"] for row in scalar["endpoints"]]
    for vrow, srow in zip(vector["endpoints"], scalar["endpoints"]):
        for key in ("visible_fraction", "first_visible_wait_s",
                    "max_no_coverage_gap_s", "never_visible",
                    "visible_satellites"):
            assert vrow[key] == srow[key], (vrow["name"], key)
    assert vector["scan"]["sample_count"] == scalar["scan"]["sample_count"]


def test_vector_nextafter_boundary_exercises_scalar_fallback_and_counter():
    """Just-below / within / just-above nextafter cosine cases at the strict
    visibility boundary must route through the scalar ground_visible
    fallback and increment the report counter; far-from-boundary cases must
    not fall back."""

    class ParkedGeometry:
        """Same-altitude single-satellite geometry parked at subpoint (0,0).

        ecef holds the satellite at (r, 0, 0) for every t so the cosine
        margin is time-invariant and the ambiguity band is exercised
        deterministically.  ground_visible replicates model.Constellation's
        elevation math exactly (no second geometry formula)."""
        num_satellites = 1
        r = model.EARTH_RADIUS_KM + 600.0
        min_elevation_deg = 25.0
        geometry_epoch_s = 0.0

        def ecef(self, sat_id, t):
            return (self.r, 0.0, 0.0)

        def ground_visible(self, sat_id, lat, lon, t):
            lat_r = math.radians(lat)
            lon_r = math.radians(lon)
            gs = (model.EARTH_RADIUS_KM * math.cos(lat_r) * math.cos(lon_r),
                  model.EARTH_RADIUS_KM * math.cos(lat_r) * math.sin(lon_r),
                  model.EARTH_RADIUS_KM * math.sin(lat_r))
            sat = self.ecef(0, t)
            dx, dy, dz = sat[0] - gs[0], sat[1] - gs[1], sat[2] - gs[2]
            rng = math.sqrt(dx * dx + dy * dy + dz * dz)
            up = (gs[0] / model.EARTH_RADIUS_KM,
                  gs[1] / model.EARTH_RADIUS_KM,
                  gs[2] / model.EARTH_RADIUS_KM)
            cos_z = (dx * up[0] + dy * up[1] + dz * up[2]) / rng
            return math.degrees(math.asin(max(-1.0, min(1.0, cos_z)))) \
                > self.min_elevation_deg

    geo = ParkedGeometry()
    elevation = math.radians(geo.min_elevation_deg)
    footprint_angle = math.acos(
        model.EARTH_RADIUS_KM / geo.r * math.cos(elevation)) - elevation
    cos_footprint = math.cos(footprint_angle)
    eps = np.finfo(float).eps
    band = 64 * eps
    cases = {
        # far below the boundary: fast path says not visible, no fallback
        "far_below": math.degrees(math.acos(cos_footprint - 2000 * eps)),
        # just below: within the ambiguity band -> scalar fallback
        "just_below": math.degrees(math.acos(
            np.nextafter(cos_footprint, 0.0))),
        # exactly at the boundary: within the ambiguity band -> fallback
        "exact": math.degrees(math.acos(cos_footprint)),
        # just above: within the ambiguity band -> scalar fallback
        "just_above": math.degrees(math.acos(
            np.nextafter(cos_footprint, 1.0))),
        # far above the boundary: fast path says visible, no fallback
        "far_above": math.degrees(math.acos(cos_footprint + 2000 * eps)),
    }
    endpoints = [_pop_endpoint(name, lat, 0.0, 1.0)
                 for name, lat in cases.items()]
    source = _population_source(len(endpoints), float(len(endpoints)))
    vector = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=60.0, step_s=20.0, endpoint_source=source)
    rows = {row["name"]: row for row in vector["endpoints"]}
    # 3 boundary endpoints x 1 satellite x 4 samples = 12 scalar fallbacks
    assert vector["evaluation"]["scalar_fallback_count"] == 12, (
        vector["evaluation"]["scalar_fallback_count"])
    assert rows["far_below"]["never_visible"] is True
    assert rows["far_above"]["never_visible"] is False
    # fallback results must agree with the scalar scan on the same geometry
    scalar = coverage.scan_coverage(geo, endpoints, horizon_s=60.0,
                                    step_s=20.0)
    for vrow, srow in zip(vector["endpoints"], scalar["endpoints"]):
        for key in ("visible_fraction", "first_visible_wait_s",
                    "max_no_coverage_gap_s", "never_visible"):
            assert vrow[key] == srow[key], (vrow["name"], key)


def test_vector_path_rejects_non_population_source_before_allocation():
    geo = model.Constellation(num_satellites=1, num_planes=1,
                              altitude_km=550.0, inclination_deg=53.0)
    endpoints = [_pop_endpoint("G1:0:0", 0.0, 0.0, 1.0)]
    with pytest.raises(coverage.CoverageAuditError,
                       match="population_raster"):
        coverage.scan_constellation_coverage(
            geo, endpoints, horizon_s=60.0, step_s=10.0)
    with pytest.raises(coverage.CoverageAuditError,
                       match="population_raster"):
        coverage.scan_constellation_coverage(
            geo, endpoints, horizon_s=60.0, step_s=10.0,
            endpoint_source={"type": "resolved_trace_cells"})


def test_vector_path_caps_fail_before_allocation():
    geo = model.Constellation(num_satellites=1, num_planes=1,
                              altitude_km=550.0, inclination_deg=53.0)
    one = [_pop_endpoint("G1:0:0", 0.0, 0.0, 1.0)]
    src = _population_source(1, 1.0)
    # max_working_mib must be in (0, 4096]
    with pytest.raises(coverage.CoverageAuditError, match="max_working_mib"):
        coverage.scan_constellation_coverage(
            geo, one, horizon_s=60.0, step_s=10.0, endpoint_source=src,
            max_working_mib=0.0)
    with pytest.raises(coverage.CoverageAuditError, match="max_working_mib"):
        coverage.scan_constellation_coverage(
            geo, one, horizon_s=60.0, step_s=10.0, endpoint_source=src,
            max_working_mib=4097.0)
    # endpoint count cap: 20,000
    many = [_pop_endpoint(f"G1:{i%179}:{i%360}", 0.0, 0.0, 1.0)
            for i in range(20_001)]
    with pytest.raises(coverage.CoverageAuditError, match="20,000"):
        coverage.scan_constellation_coverage(
            geo, many, horizon_s=60.0, step_s=10.0,
            endpoint_source=_population_source(len(many), len(many) * 1.0))
    # sample cap: 1,000,001 samples -> horizon/step too fine
    with pytest.raises(coverage.CoverageAuditError, match="sample_count"):
        coverage.scan_constellation_coverage(
            geo, one, horizon_s=2_000_000.0, step_s=0.001,
            endpoint_source=_population_source(1, 1.0))
    # comparison cap: endpoints * samples * satellites <= 50e9.  A single
    # endpoint can never trip it inside the sample cap (1,000,001 x 280 <<
    # 50e9), so use 200 endpoints at the exact 1,000,001-sample cap:
    # 200 * 1,000,001 * 280 = 5.6e10 > 5e10.
    big_geo = model.Constellation(num_satellites=280, num_planes=14,
                                  altitude_km=600.0, inclination_deg=98.6,
                                  min_elevation_deg=25.0)
    endpoints = [_pop_endpoint(f"G1:{i%179}:{i%360}", i % 90, 0.0, 1.0)
                 for i in range(200)]
    with pytest.raises(coverage.CoverageAuditError, match="comparisons"):
        coverage.scan_constellation_coverage(
            big_geo, endpoints, horizon_s=10_000.0, step_s=0.01,
            endpoint_source=_population_source(200, 200.0))


def _pop_endpoint_dup_named():
    return [_pop_endpoint("G1:0:0", 0.0, 0.0, 1.0),
            _pop_endpoint("G1:0:0", 1.0, 1.0, 1.0)]


def test_population_weights_reject_invalid_endpoints():
    geo = model.Constellation(num_satellites=1, num_planes=1,
                              altitude_km=550.0, inclination_deg=53.0)
    good = _pop_endpoint("G1:0:0", 0.0, 0.0, 1.0)
    cases = [
        ([_pop_endpoint("G1:0:0", 0.0, 0.0, -1.0)], "population",
         "population"),
        ([_pop_endpoint("G1:0:0", 0.0, 0.0, float("nan"))], "population",
         "population"),
        ([_pop_endpoint("G1:0:0", 0.0, 0.0, float("inf"))], "population",
         "population"),
        ([{"name": "G1:0:0", "lat": 0.0, "lon": 0.0}], "population",
         "missing population weight"),
        ([good, good], "duplicate", "duplicate endpoint name"),
        ([_pop_endpoint("G1:0:0", 0.0, 0.0, 2.0)], "candidate_regions",
         "candidate_regions must equal endpoint count"),
    ]
    for endpoints, match, _label in cases:
        # the silently-omitted case declares one extra candidate region;
        # all other cases use exact counts so the *weight* check is what
        # fires
        declared = len(endpoints) + 1 if match == "candidate_regions" \
            else len(endpoints)
        src = _population_source(declared, 2.0)
        with pytest.raises(coverage.CoverageAuditError, match=match):
            coverage.scan_constellation_coverage(
                geo, endpoints, horizon_s=60.0, step_s=10.0,
                endpoint_source=src)


def test_population_ledger_contains_each_grid_id_exactly_once():
    geo = model.Constellation(num_satellites=1, num_planes=1,
                              altitude_km=550.0, inclination_deg=53.0)
    regions = (
        population.PopulationRegion("G1:1:1", 5.0, 5.0, 10.0),
        population.PopulationRegion("G1:2:2", 15.0, 15.0, 20.0),
        population.PopulationRegion("G1:3:3", 25.0, 25.0, 30.0),
    )
    table = population.PopulationTable(
        regions=regions, source_path="/fake/pop.tif", source_sha256="b" * 64,
        source_shape=(720, 1440), source_resolution_deg=(0.25, 0.25),
        aggregation_deg=1.0, total_population=60.0)
    endpoints = [
        _pop_endpoint(r.grid_id, r.lat, r.lon, r.population)
        for r in table.regions
    ]
    source = _population_source(3, 60.0,
                                source_sha256=table.source_sha256)
    vector = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=60.0, step_s=10.0, endpoint_source=source)
    names = [row["name"] for row in vector["endpoints"]]
    assert sorted(names) == sorted(r.grid_id for r in table.regions)
    assert len(names) == len(set(names)) == 3
    assert vector["endpoint_source"]["candidate_regions"] == 3
    assert vector["endpoint_source"]["source_sha256"] == "b" * 64


def test_population_weighted_summaries_use_population_denominator():
    """Weighted fractions divide by total population, never by endpoint
    count: a tiny always-visible region must not dilute a huge never-visible
    region under the population denominator."""
    geo = model.Constellation(num_satellites=1, num_planes=1,
                              altitude_km=600.0, inclination_deg=53.0,
                              min_elevation_deg=25.0)
    endpoints = [
        # subpoint-adjacent: always visible (sat 0 at (0,0) at t=0 and stays
        # near it over short windows)
        _pop_endpoint("G1:0:0", 0.0, 0.0, 1.0),
        # antipode: never visible
        _pop_endpoint("G1:0:180", 0.0, 180.0, 999.0),
    ]
    source = _population_source(2, 1000.0)
    vector = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=60.0, step_s=10.0, endpoint_source=source)
    summary = vector["summary"]
    assert summary["endpoints_total"] == 2
    assert summary["never_visible"] == 1
    assert summary["population_weighted_visible_fraction"] == \
        pytest.approx(1.0 / 1000.0)
    assert summary["population_weighted_never_visible_fraction"] == \
        pytest.approx(999.0 / 1000.0)


def test_tampered_source_sha_or_candidate_count_fails_l1_verifier(tmp_path):
    geo = model.Constellation(num_satellites=1, num_planes=1,
                              altitude_km=550.0, inclination_deg=53.0)
    endpoints = [
        _pop_endpoint("G1:0:0", 0.0, 0.0, 10.0),
        _pop_endpoint("G1:1:1", 10.0, 10.0, 20.0),
    ]
    source = _population_source(2, 30.0)
    report = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=60.0, step_s=10.0, endpoint_source=source)
    assert coverage.verify_coverage_audit_v2(report) == []
    # tamper the source SHA with a wrong-length / non-hex value
    bad_sha = json.loads(coverage.stable_json(report))
    bad_sha["endpoint_source"]["source_sha256"] = "z" * 64
    assert any("source_sha256" in e
               for e in coverage.verify_coverage_audit_v2(bad_sha))
    # ... and with a different valid-looking SHA (must still mismatch the
    # expected source anchor)
    bad_sha2 = json.loads(coverage.stable_json(report))
    bad_sha2["endpoint_source"]["source_sha256"] = "0" * 64
    assert any("source_sha256" in e
               for e in coverage.verify_coverage_audit_v2(
                   bad_sha2, expected_source_sha256=source["source_sha256"]))
    # tamper the candidate count
    bad_count = json.loads(coverage.stable_json(report))
    bad_count["endpoint_source"]["candidate_regions"] = 99
    assert any("candidate_regions" in e
               for e in coverage.verify_coverage_audit_v2(bad_count))
    # tamper a population-weighted summary value
    bad_sum = json.loads(coverage.stable_json(report))
    bad_sum["summary"]["population_weighted_visible_fraction"] = 1.0
    assert any("population_weighted" in e
               for e in coverage.verify_coverage_audit_v2(bad_sum))
    # tamper a scan sample count
    bad_scan = json.loads(coverage.stable_json(report))
    bad_scan["scan"]["sample_count"] = 1234
    assert any("sample_count" in e
               for e in coverage.verify_coverage_audit_v2(bad_scan))


def test_l1_verifier_accepts_json_rounding_noise_in_weighted_summary():
    """Cross-runtime summation noise must not invalidate an untampered audit."""
    geo = model.Constellation(num_satellites=1, num_planes=1,
                              altitude_km=550.0, inclination_deg=53.0)
    endpoints = [
        _pop_endpoint("G1:0:0", 0.0, 0.0, 10.1),
        _pop_endpoint("G1:1:1", 10.0, 10.0, 20.2),
    ]
    report = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=60.0, step_s=10.0,
        endpoint_source=_population_source(2, 30.3))
    report["summary"]["population_weighted_visible_fraction"] += 1e-15

    assert coverage.verify_coverage_audit_v2(report) == []


def test_vector_report_is_stable_json_and_chunked(tmp_path):
    geo = model.Constellation(num_satellites=12, num_planes=3,
                              altitude_km=550.0, inclination_deg=53.0,
                              min_elevation_deg=25.0)
    endpoints = [
        _pop_endpoint("G1:0:0", 0.0, 0.0, 10.0),
        _pop_endpoint("G1:1:1", 10.0, 10.0, 20.0),
        _pop_endpoint("G1:2:3", 30.0, -60.0, 5.0),
    ]
    source = _population_source(3, 35.0)
    first = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=3600.0, step_s=600.0,
        endpoint_source=source)
    second = coverage.scan_constellation_coverage(
        geo, endpoints, horizon_s=3600.0, step_s=600.0,
        endpoint_source=source)
    assert coverage.stable_json(first) == coverage.stable_json(second)
    assert hashlib.sha256(coverage.stable_json(first).encode()).hexdigest() \
        == hashlib.sha256(coverage.stable_json(second).encode()).hexdigest()
    evaluation = first["evaluation"]
    assert evaluation["comparison_count"] == 3 * 7 * 12
    assert evaluation["endpoint_chunk_size"] >= 1
    assert evaluation["time_chunk_size"] >= 1
    assert evaluation["projected_bytes"] > 0
    assert evaluation["observed_peak_rss_mib"] >= 0.0
    assert isinstance(evaluation["full_scan"], bool)
    # four resolved limits are recorded
    limits = first["limits"]
    assert limits["max_endpoints"] == 20_000
    assert limits["max_samples"] == 1_000_001
    assert limits["max_comparisons"] == 50_000_000_000
    assert limits["max_working_mib"] == 256.0
    # sample count semantics: floor(3600/600)+1 = 6+1? ceil(3600/600)=6 -> 7
    assert first["scan"]["sample_count"] == 7
    assert first["scan"]["sampling_error_bound_s"] == 600.0
    assert first["scan"]["geometry_epoch_s"] == 0.0


def test_v1_trace_cli_mode_retains_v1_schema(tmp_path, capsys):
    """The trace-source CLI path must still emit the v1 audit schema."""
    from CODE.leo_sim import config as config_mod
    from CODE.leo_sim import trace as trace_mod
    cfg = config_mod.resolve_config({"scenario": {"duration_s": 1.0},
                                     "endpoints": {"sites": [
                                         {"name": "a", "lat": 0.0, "lon": 0.0},
                                         {"name": "b", "lat": 0.0, "lon": 10.0},
                                     ]}})
    tdir = tmp_path / "trace"
    trace_mod.compile_trace(cfg, str(tdir))
    out = tmp_path / "out.json"
    rc = coverage._cli(["--config", "CODE/leo_sim/profiles/smoke.yaml",
                        "--trace", str(tdir), "--step", "1", "--horizon", "1",
                        "--output", str(out)])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["schema"] == "leo-sim-coverage-audit/v1"
