from types import SimpleNamespace

import pytest

from CODE.experiment_platform import t1_static_load as static_load


def test_multisource_path_ties_are_deterministic_and_direction_aware():
    adj = {
        1: [(4, "N"), (3, "E")],
        2: [(5, "N")],
        3: [(8, "W")],
        4: [(9, "E")],
        5: [(9, "S")],
    }
    assert static_load.shortest_path(adj, [2, 1], [9]) == (
        2, 1, 9, ((1, "N", 4), (4, "E", 9)))
    assert static_load.shortest_path(adj, [6], [9]) is None


def test_static_load_conserves_unroutable_od_mass_without_renormalizing():
    regions = (
        SimpleNamespace(grid_id="a", lat=0.0, lon=0.0, population=1.0),
        SimpleNamespace(grid_id="b", lat=0.0, lon=1.0, population=1.0),
        SimpleNamespace(grid_id="c", lat=0.0, lon=10.0, population=1.0),
    )
    # a<->b is connected, c is directly accessible at a separate isolated
    # satellite. Each ordered OD is in the denominator exactly once.
    adjacency = {0: [(1, "E")], 1: [(0, "W")], 2: []}
    capacities = {(0, "E", 1): 2_000_000.0,
                  (1, "W", 0): 2_000_000.0}
    report = static_load.compute_static_load(
        regions, [1.0, 1.0, 1.0], [(0,), (1,), (2,)], adjacency,
        capacities, distance_floor_km=100.0)
    assert report["od_pairs"] == 6
    assert 0.0 < report["routed_od_mass"] < 1.0
    assert 0.0 < report["unroutable_od_mass"] < 1.0
    assert report["direct_od_mass"] == 0.0
    assert report["mass_conservation_error"] < 1e-12
    assert report["max_u_inverse_mbps"] > 0.0
    assert report["base_R_mbps_for_nominal_0_6"] == pytest.approx(
        0.6 / report["max_u_inverse_mbps"])


def test_static_load_counts_direct_access_as_zero_isl_mass():
    regions = (
        SimpleNamespace(grid_id="a", lat=0.0, lon=0.0, population=1.0),
        SimpleNamespace(grid_id="b", lat=0.0, lon=1.0, population=1.0),
        SimpleNamespace(grid_id="c", lat=0.0, lon=2.0, population=1.0),
    )
    report = static_load.compute_static_load(
        regions, [1.0, 1.0, 1.0], [(0,), (0, 1), (1,)],
        {0: [(1, "E")], 1: [(0, "W")]},
        {(0, "E", 1): 2_000_000.0, (1, "W", 0): 2_000_000.0},
        distance_floor_km=100.0)
    assert 0.0 < report["direct_od_mass"] < 1.0
    assert report["routed_od_mass"] == pytest.approx(1.0)
    assert report["unroutable_od_mass"] == 0.0
    assert report["max_u_inverse_mbps"] > 0.0


def test_build_static_profile_associates_each_endpoint_with_highest_elevation(
        monkeypatch, tmp_path):
    """Visible alternatives must not become multi-source shortest paths."""
    regions = (
        SimpleNamespace(grid_id="a", lat=0.0, lon=0.0, population=100.0),
        SimpleNamespace(grid_id="b", lat=1.0, lon=0.0, population=100.0),
        SimpleNamespace(grid_id="c", lat=2.0, lon=0.0, population=1.0),
    )
    table = SimpleNamespace(regions=regions, total_population=201.0,
                            source_sha256="population-fixture-sha")
    resolved = {"config": {
        "scenario": {"num_satellites": 3, "num_planes": 1,
                     "altitude_km": 600.0, "inclination_deg": 98.6,
                     "min_elevation_deg": 25.0, "geometry_epoch_s": 0.0},
        "endpoints": {"aggregation_deg": 1.0,
                      "region_lat_bounds_deg": [0.0, 3.0],
                      "region_lon_bounds_deg": [0.0, 1.0]},
        "demand": {"population_path": "population.tif",
                   "source_population_exponent": 1.0,
                   "destination_population_exponent": 1.0,
                   "gravity_alpha": 1.25, "gravity_d_floor_km": 100.0},
        "links": {"isl_dirs": ["E", "W"], "max_isl_km": 6000.0,
                  "rate_model": "fixed", "isl_rate_mbps": 10.0},
        "access": {"uplink_rate_mbps": 1000.0,
                   "downlink_rate_mbps": 1000.0},
    }}

    class FakeGeometry:
        num_satellites = 3

        def __init__(self, **_kwargs):
            pass

        def ground_visible(self, sat, lat, _lon, _t):
            visible = {0: {0, 1}, 1: {1}, 2: {2}}
            return sat in visible[int(lat)]

        def elevation_deg(self, sat, lat, _lon, _t):
            elevations = {0: {0: 60.0, 1: 50.0},
                          1: {1: 55.0}, 2: {2: 70.0}}
            return elevations[int(lat)][sat]

        def isl_available(self, _a, _b, _t):
            return True

        def isl_range_km(self, _a, _b, _t):
            return 1000.0

        def slant_range_km(self, _sat, _lat, _lon, _t):
            return 1000.0

    monkeypatch.setattr(static_load.config, "load_config_file",
                        lambda _path: resolved)
    monkeypatch.setattr(static_load.population, "load_population_regions",
                        lambda *_args, **_kwargs: table)
    monkeypatch.setattr(static_load.model, "Constellation", FakeGeometry)
    monkeypatch.setattr(static_load.routing, "build_topology", lambda *_a, **_k: {
        0: {"E": 1}, 1: {"W": 0, "E": 2}, 2: {"W": 1}})

    profile = tmp_path / "profile.yaml"
    profile.write_text("test profile input\n", encoding="utf-8")
    report = static_load.build_static_profile(str(profile), t_ref=2.0)

    # Region a has two visible satellites, but its source and destination
    # endpoint must each bind to sat 0 (highest elevation), not choose sat 1
    # simply because that makes a shorter/direct route to region b.
    assert report["direct_od_mass"] == 0.0
    assert report["access_association"]["rule"] == (
        "highest elevation among positive finite direction-specific GSL rates; "
        "ties by satellite ID")
    assert report["access_association"]["rows"][0] == {
        "grid_id": "a", "source_sat": 0, "destination_sat": 0,
        "source_elevation_deg": 60.0, "destination_elevation_deg": 60.0,
        "source_rate_bps": 1_000_000_000.0,
        "destination_rate_bps": 1_000_000_000.0}


@pytest.mark.parametrize("capacity", [float("nan"), float("inf"), 0.0, -1.0])
def test_static_load_rejects_invalid_declared_edge_capacity(capacity):
    region = (SimpleNamespace(grid_id="a", lat=0.0, lon=0.0,
                              population=1.0),
              SimpleNamespace(grid_id="b", lat=0.0, lon=1.0,
                              population=1.0))
    with pytest.raises(ValueError, match="capacity must be finite and positive"):
        static_load.compute_static_load(
            region, [1.0, 1.0], [(0,), (1,)],
            {0: [(1, "E")], 1: [(0, "W")]},
            {(0, "E", 1): capacity, (1, "W", 0): 2_000_000.0})
