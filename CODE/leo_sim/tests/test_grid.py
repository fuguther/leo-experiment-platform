"""Tests for CODE.leo_sim.grid — stable 0.25-degree grid IDs and aggregation."""
import pytest

from CODE.leo_sim import grid


def test_grid_id_stable_and_decodable():
    gid = grid.grid_id(31.2304, 121.4737, deg=0.25)
    assert isinstance(gid, str)
    lat, lon = grid.grid_center(gid)
    assert abs(lat - 31.2304) <= 0.25
    assert abs(lon - 121.4737) <= 0.25
    # stability: same inputs, same id
    assert grid.grid_id(31.2304, 121.4737, deg=0.25) == gid


def test_grid_id_cell_boundaries():
    # points within the same 0.25 cell share an id; adjacent cells differ
    a = grid.grid_id(10.01, 20.01)
    b = grid.grid_id(10.24, 20.24)
    c = grid.grid_id(10.26, 20.26)
    assert a == b
    assert a != c


def test_aggregate_id_default_1_degree():
    gid = grid.grid_id(31.2304, 121.4737)
    agg = grid.aggregate_id(gid, agg_deg=1.0)
    alat, alon = grid.grid_center(agg)
    assert abs(alat - 31.5) < 1e-9
    assert abs(alon - 121.5) < 1e-9


def test_out_of_range_rejected():
    with pytest.raises(ValueError):
        grid.grid_id(91.0, 0.0)
    with pytest.raises(ValueError):
        grid.grid_id(0.0, 181.0)


def test_sparse_activation():
    sites = [(31.23, 121.47), (31.24, 121.48), (40.0, 116.0)]
    active = grid.active_aggregate_cells(sites, deg=0.25, agg_deg=1.0)
    # shanghai pair collapses into one 1-degree cell; beijing is separate
    assert len(active) == 2
