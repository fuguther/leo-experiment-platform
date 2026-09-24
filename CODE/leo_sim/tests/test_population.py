"""Population raster adapter contracts."""
from pathlib import Path

import numpy as np
import pytest

from CODE.leo_sim import population


REPO_TIFF = (Path(__file__).resolve().parents[2] / "population_map"
             / "gpw_v4_population_count_rev11_2020_15_min.tif")


def test_aggregate_population_conserves_positive_mass_and_centers():
    # Four 1-degree pixels spanning lon [-2,2], lat [-1,1]. Negative is nodata.
    pixels = np.array([[1.0, 2.0, -999.0, 4.0],
                       [5.0, 6.0, 7.0, 8.0]])
    regions = population.aggregate_population_array(
        pixels, west=-2.0, north=1.0, pixel_lon_deg=1.0,
        pixel_lat_deg=1.0, aggregation_deg=4.0)
    assert [(r.grid_id, r.lat, r.lon, r.population) for r in regions] == [
        ("G4:22:44", 0.0, -2.0, 14.0),
        ("G4:22:45", 0.0, 2.0, 19.0),
    ]
    assert sum(r.population for r in regions) == 33.0


def test_aggregate_population_rejects_misaligned_resolution():
    with pytest.raises(population.PopulationError, match="exact multiple"):
        population.aggregate_population_array(
            np.ones((2, 2)), west=-1.0, north=1.0,
            pixel_lon_deg=1.0, pixel_lat_deg=1.0,
            aggregation_deg=1.5)


def test_repository_gpw_aggregates_to_real_regions():
    pytest.importorskip("PIL")
    table = population.load_population_regions(REPO_TIFF, aggregation_deg=5.0)
    assert table.source_sha256 and len(table.source_sha256) == 64
    assert table.source_shape == (720, 1440)
    assert table.source_resolution_deg == pytest.approx((0.25, 0.25))
    assert len(table.regions) > 100
    assert table.total_population > 7_000_000_000
    assert all(r.population > 0 for r in table.regions)
    assert abs(sum(r.population for r in table.regions)
               - table.total_population) < 1.0


def test_population_loader_rejects_missing_file(tmp_path):
    with pytest.raises(population.PopulationError, match="not found"):
        population.load_population_regions(
            tmp_path / "missing.tif", aggregation_deg=5.0)
