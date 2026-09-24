"""Stable geographic grid IDs for leo_sim.

Grid IDs encode a lat/lon cell at a configurable resolution (default 0.25
degrees). Aggregation cells (default 1 degree) group fine cells for endpoint
sparsity: only cells that appear in the demand trace are activated.
"""
from __future__ import annotations

import math

DEFAULT_GRID_DEG = 0.25
DEFAULT_AGG_DEG = 1.0


def _cell_index(v: float, lo: float, deg: float, span: float) -> int:
    # clamp so boundary coordinates (lat=90, lon=180) stay in the last cell
    n = int(math.floor(span / deg))
    return min(int(math.floor((v - lo) / deg)), n - 1)


def grid_id(lat: float, lon: float, deg: float = DEFAULT_GRID_DEG) -> str:
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"lat out of range: {lat}")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"lon out of range: {lon}")
    ilat = _cell_index(lat, -90.0, deg, 180.0)
    ilon = _cell_index(lon, -180.0, deg, 360.0)
    # quantize degrees in the id so it is self-describing and stable
    deg_q = f"{deg:g}"
    return f"G{deg_q}:{ilat}:{ilon}"


def grid_center(gid: str) -> tuple[float, float]:
    prefix, ilat_s, ilon_s = gid.split(":")
    deg = float(prefix[1:])
    ilat, ilon = int(ilat_s), int(ilon_s)
    lat = -90.0 + (ilat + 0.5) * deg
    lon = -180.0 + (ilon + 0.5) * deg
    # round away floating noise for stable downstream keys
    return round(lat, 9), round(lon, 9)


def aggregate_id(gid: str, agg_deg: float = DEFAULT_AGG_DEG) -> str:
    lat, lon = grid_center(gid)
    return grid_id(lat, lon, deg=agg_deg)


def is_valid_grid_id(gid) -> bool:
    """Validity of a *canonical* grid id.

    A physical cell has exactly one accepted spelling.  Accepting aliases
    such as ``G1.0:090:180`` would make string-keyed endpoint identity and
    source/destination comparisons ambiguous.
    """
    if not isinstance(gid, str):
        return False
    parts = gid.split(":")
    if len(parts) != 3 or not parts[0].startswith("G"):
        return False
    try:
        deg = float(parts[0][1:])
        ilat, ilon = int(parts[1]), int(parts[2])
    except (ValueError, TypeError):
        return False
    if not math.isfinite(deg) or deg <= 0:
        return False
    nlat = int(math.floor(180.0 / deg))
    nlon = int(math.floor(360.0 / deg))
    if abs(180.0 / deg - nlat) > 1e-9 or abs(360.0 / deg - nlon) > 1e-9:
        return False
    if not (0 <= ilat < nlat and 0 <= ilon < nlon):
        return False
    return gid == f"G{deg:g}:{ilat}:{ilon}"


def active_aggregate_cells(
    sites: list[tuple[float, float]],
    deg: float = DEFAULT_GRID_DEG,
    agg_deg: float = DEFAULT_AGG_DEG,
) -> dict[str, list[str]]:
    """Map each active aggregate cell to its active fine cells (sparse)."""
    active: dict[str, set[str]] = {}
    for lat, lon in sites:
        fine = grid_id(lat, lon, deg=deg)
        agg = aggregate_id(fine, agg_deg=agg_deg)
        active.setdefault(agg, set()).add(fine)
    return {agg: sorted(fines) for agg, fines in sorted(active.items())}
