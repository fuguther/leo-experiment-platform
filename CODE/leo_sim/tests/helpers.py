"""Shared helpers for leo_sim behavioral tests.

StaticGeometry is a deterministic scripted geometry used to exercise the real
kernel with controlled visibility/topology. The real Walker-delta Constellation
is covered by test_model.py and the CLI integration smoke run.
"""
from __future__ import annotations

from CODE.leo_sim import config, grid

EARTH_RADIUS_KM = 6371.0


def cell(lat: float, lon: float, agg_deg: float = 1.0) -> str:
    return grid.aggregate_id(grid.grid_id(lat, lon, 0.25), agg_deg)


def cell_center(gid: str) -> tuple[float, float]:
    return grid.grid_center(gid)


def make_cfg(overrides: dict | None = None) -> dict:
    """Minimal resolved config for kernel tests.

    Deterministic association (no hysteresis/dwell/acquisition delay), control
    plane disabled, oracle routing so kernel mechanics do not depend on
    destination discovery through advertisements.
    """
    user = {
        "scenario": {
            "duration_s": 10.0,
            "time_step_s": 0.1,
            "num_satellites": 2,
            "num_planes": 1,
            "seed": 1,
        },
        "access": {
            "hysteresis_deg": 0.0,
            "min_dwell_s": 0.0,
            "acquisition_delay_s": 0.0,
        },
        "control_plane": {"enabled": False},
        "routing": {"policy": "oracle"},
    }
    if overrides:
        user = _deep_merge(user, overrides)
    return config.resolve_config(user)


def _deep_merge(base: dict, over: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def row(pid: int, t: float, src: str, dst: str, bits: int = 8_000_000,
        deadline: float | None = None) -> dict:
    return {
        "packet_id": pid,
        "emit_time_s": t,
        "src_grid_id": src,
        "dst_grid_id": dst,
        "bits": bits,
        "deadline_at_s": deadline,
    }


class StaticGeometry:
    """Scripted constellation geometry.

    visible: callable(sat_id, lat, lon, t) -> bool
    elevation: optional callable(sat_id, lat, lon, t) -> float degrees; when
        omitted, 90.0 for visible and -10.0 for not visible.
    neighbors_map: {sat_id: {direction: sat_id}}; static adjacency.
    gsl_changes / isl_changes: explicit sorted change timelines (seconds).
        A listed time c is the FIRST instant of the new state (left-closed
        intervals): the predicate evaluated at exactly c reflects the new
        state. next_*_change answers from these timelines only — never by
        sampling guesses — so a scripted test must declare every flip it
        scripts; an empty list certifies "never changes".
    """

    certifies_change_times = True

    def __init__(self, num_satellites, neighbors_map=None, visible=None,
                 elevation=None, slant_km=600.0, isl_km=1000.0, isl_range_fn=None,
                 gsl_changes=None, isl_changes=None, neighbors_at_fn=None):
        self.num_satellites = num_satellites
        self._nb = neighbors_map or {}
        self._visible = visible or (lambda s, lat, lon, t: False)
        self._elev = elevation
        self.slant_km = float(slant_km)
        self.isl_km = float(isl_km)
        self._isl_range_fn = isl_range_fn
        self._neighbors_at_fn = neighbors_at_fn
        self._gsl_changes = sorted(float(c) for c in (gsl_changes or ()))
        self._isl_changes = sorted(float(c) for c in (isl_changes or ()))

    def ground_visible(self, sat_id, lat, lon, t):
        return bool(self._visible(sat_id, lat, lon, t))

    def elevation_deg(self, sat_id, lat, lon, t):
        if self._elev is not None:
            return float(self._elev(sat_id, lat, lon, t))
        return 90.0 if self._visible(sat_id, lat, lon, t) else -10.0

    def slant_range_km(self, sat_id, lat, lon, t):
        return self.slant_km

    def isl_range_km(self, a, b, t):
        if self._isl_range_fn is not None:
            return float(self._isl_range_fn(a, b, t))
        return self.isl_km

    def neighbors(self, sat_id, dirs):
        return {d: n for d, n in self._nb.get(sat_id, {}).items() if d in dirs}

    def neighbors_at(self, sat_id, dirs, t):
        if self._neighbors_at_fn is None:
            return self.neighbors(sat_id, dirs)
        return {d: n for d, n in self._neighbors_at_fn(sat_id, dirs, t).items()
                if d in dirs}

    def positions(self, t):
        return tuple((0.0, 0.0, 0.0) for _ in range(self.num_satellites))

    # dynamic availability API (same contract as model.Constellation)
    def gsl_available(self, sat_id, lat, lon, t):
        return self.ground_visible(sat_id, lat, lon, t)

    def next_gsl_change(self, sat_id, lat, lon, t, limit):
        prev = self.ground_visible(sat_id, lat, lon, t)
        for c in self._gsl_changes:
            if c <= t or c > limit:
                continue
            v = self.ground_visible(sat_id, lat, lon, c)
            if v != prev:
                return c
            prev = v
        return None

    def isl_available(self, a, b, t):
        if self._neighbors_at_fn is not None:
            return b in self._neighbors_at_fn(a, ("N", "S", "E", "W"), t).values()
        return b in self._nb.get(a, {}).values()

    def next_isl_change(self, a, b, t, limit):
        prev = self.isl_available(a, b, t)
        for c in self._isl_changes:
            if c <= t or c > limit:
                continue
            v = self.isl_available(a, b, c)
            if v != prev:
                return c
            prev = v
        return None
