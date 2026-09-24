"""Deterministic GPW population aggregation for V2 traffic endpoints."""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import grid


class PopulationError(ValueError):
    pass


@dataclass(frozen=True)
class PopulationRegion:
    grid_id: str
    lat: float
    lon: float
    population: float


@dataclass(frozen=True)
class PopulationTable:
    regions: tuple[PopulationRegion, ...]
    source_path: str
    source_sha256: str
    source_shape: tuple[int, int]
    source_resolution_deg: tuple[float, float]
    aggregation_deg: float
    total_population: float


def aggregate_population_array(
    values: np.ndarray,
    *,
    west: float,
    north: float,
    pixel_lon_deg: float,
    pixel_lat_deg: float,
    aggregation_deg: float,
) -> tuple[PopulationRegion, ...]:
    """Aggregate north-to-south raster cells into canonical geographic grids."""
    a = np.asarray(values, dtype=np.float64)
    if a.ndim != 2 or not a.size:
        raise PopulationError("population raster must be a non-empty 2-D array")
    for value, label in ((west, "west"), (north, "north"),
                         (pixel_lon_deg, "pixel_lon_deg"),
                         (pixel_lat_deg, "pixel_lat_deg"),
                         (aggregation_deg, "aggregation_deg")):
        if not math.isfinite(float(value)):
            raise PopulationError(f"{label} must be finite")
    if pixel_lon_deg <= 0 or pixel_lat_deg <= 0 or aggregation_deg <= 0:
        raise PopulationError("population resolutions must be positive")
    for pixel, label in ((pixel_lon_deg, "longitude"),
                         (pixel_lat_deg, "latitude")):
        ratio = aggregation_deg / pixel
        if abs(ratio - round(ratio)) > 1e-9:
            raise PopulationError(
                f"aggregation_deg must be an exact multiple of {label} pixel size")
    if abs(180.0 / aggregation_deg - round(180.0 / aggregation_deg)) > 1e-9 \
            or abs(360.0 / aggregation_deg
                   - round(360.0 / aggregation_deg)) > 1e-9:
        raise PopulationError("aggregation_deg must tile the globe exactly")

    height, width = a.shape
    east = west + width * pixel_lon_deg
    south = north - height * pixel_lat_deg
    if west < -180.0 - 1e-9 or east > 180.0 + 1e-9 \
            or south < -90.0 - 1e-9 or north > 90.0 + 1e-9:
        raise PopulationError("population raster geographic extent is outside Earth")

    clean = np.where(np.isfinite(a) & (a > 0.0), a, 0.0)
    totals: dict[str, float] = {}
    for row in range(height):
        lat = north - (row + 0.5) * pixel_lat_deg
        for col in range(width):
            pop = float(clean[row, col])
            if pop <= 0.0:
                continue
            lon = west + (col + 0.5) * pixel_lon_deg
            gid = grid.grid_id(lat, lon, aggregation_deg)
            totals[gid] = totals.get(gid, 0.0) + pop
    regions = []
    for gid in sorted(totals):
        lat, lon = grid.grid_center(gid)
        regions.append(PopulationRegion(gid, lat, lon, totals[gid]))
    if len(regions) < 2:
        raise PopulationError("population aggregation produced fewer than two regions")
    return tuple(regions)


def load_population_regions(path: str | Path,
                            aggregation_deg: float) -> PopulationTable:
    source = Path(path)
    if not source.is_file() or source.is_symlink():
        raise PopulationError(f"population raster not found or unsafe: {source}")
    try:
        from PIL import Image
        with Image.open(source) as image:
            values = np.asarray(image, dtype=np.float64)
            tags = dict(image.tag_v2)
    except Exception as exc:
        raise PopulationError(f"population raster unreadable: {source}: {exc}") from exc
    scale = tags.get(33550)
    tie = tags.get(33922)
    if not (isinstance(scale, tuple) and len(scale) >= 2
            and isinstance(tie, tuple) and len(tie) >= 6):
        raise PopulationError("population GeoTIFF lacks pixel scale/tie point metadata")
    pixel_lon_deg, pixel_lat_deg = float(scale[0]), float(scale[1])
    west, north = float(tie[3]), float(tie[4])
    expected_width = 360.0 / pixel_lon_deg
    expected_height = 180.0 / pixel_lat_deg
    if abs(values.shape[1] - expected_width) > 1e-6 \
            or abs(values.shape[0] - expected_height) > 1e-6 \
            or abs(west + 180.0) > 1e-6 or abs(north - 90.0) > 1e-6:
        raise PopulationError(
            "population GeoTIFF must be a global north-up WGS84 raster")
    regions = aggregate_population_array(
        values, west=west, north=north,
        pixel_lon_deg=pixel_lon_deg, pixel_lat_deg=pixel_lat_deg,
        aggregation_deg=aggregation_deg)
    total = float(sum(region.population for region in regions))
    if not math.isfinite(total) or total <= 0.0:
        raise PopulationError("population raster has no positive finite population")
    return PopulationTable(
        regions=regions,
        # Preserve the configured spelling. An absolute checkout path would
        # make otherwise identical manifests differ across machines.
        source_path=str(source),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        source_shape=tuple(int(v) for v in values.shape),
        source_resolution_deg=(pixel_lat_deg, pixel_lon_deg),
        aggregation_deg=float(aggregation_deg),
        total_population=total,
    )
