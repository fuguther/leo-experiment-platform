"""Simplified Walker-delta constellation geometry for leo_sim.

Explicit units: time in seconds, distance in km, angles in degrees.
Positions are pure functions of t; the kernel queries them only at the
current simulation time (no future ephemeris is ever read).
"""
from __future__ import annotations

import math
from collections import OrderedDict

EARTH_RADIUS_KM = 6371.0
EARTH_ROT_RATE_RAD_S = 7.2921159e-5
C_KM_S = 299_792.458


_MISS = object()


def propagation_delay_s(distance_km: float) -> float:
    return distance_km / C_KM_S


def _sph_to_ecef(lat_deg: float, lon_deg: float, r_km: float) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    return (
        r_km * math.cos(lat) * math.cos(lon),
        r_km * math.cos(lat) * math.sin(lon),
        r_km * math.sin(lat),
    )


# Conservative rate bounds for certified change detection, derived to cover
# the full supported config domain (altitude 300-2000 km, any inclination,
# any ground point). These bound the true dynamics of the abstract Walker
# geometry (they are NOT operator calibration):
#
# * Elevation: the fastest case is a zenith pass at the lowest altitude.
#   Near zenith the zenith distance is u ~= (r/h) * psi with r = R + h, so
#     max |d(elev)/dt| = (r/h) * (n + omega_E),  n = sqrt(mu / r^3).
#   At h = 300 km: (6671/300) * (1.1588e-3 + 7.29e-5) ~= 0.02739 rad/s
#   ~= 1.57 deg/s (the analytic bound is conservative: dense numeric scans of
#   the abstract model measure ~1.42 deg/s). ELEV_RATE_DEG_S = 2.0 covers the
#   domain with ~27% margin over the analytic bound; 550 km+ is far slower.
# * Range: ISL relative speed <= 2 * v_orbit(300 km) ~= 15.5 km/s; GSL slant
#   rate <= v_orbit + omega_E * R_earth ~= 8.2 km/s. RANGE_RATE_KM_S = 20.0
#   covers both with margin.
ELEV_RATE_DEG_S = 2.0
RANGE_RATE_KM_S = 20.0


class GeometryCertificationError(RuntimeError):
    """The next-change search could not certify an answer (iteration budget
    exhausted, non-finite input/margin, or invalid arguments). Fail closed:
    None is ONLY ever returned when the absence of a change in (t0, t1] has
    been proven under the rate-bound contract."""


class MemoizedGeometry:
    """Exact-argument LRU memoization wrapper for a geometry provider.

    Bit-level equivalence contract: the wrapped geometry is a pure function of
    its arguments, so returning the exact first-computed value for an
    identical argument tuple never changes control flow or outputs.  Every
    cache is a bounded LRU (capacity entries per query family) and is only
    filled on demand — the wrapper itself never reads future times.

    Cache policy: values are keyed by their EXACT arguments (never quantized,
    so outputs stay bit-identical), but eviction is per time instant — each
    query family keeps a small LRU of the most recent distinct ``t`` values
    and, under each ``t``, every (sat, lat, lon)/edge entry computed at that
    instant.  Discrete-event runs query the same instant many times (one
    routing decision relaxes every edge at the same ``now``; access/handover
    scans every satellite at the same ``now``), so the per-instant reuse is
    what removes the trigonometric recomputation; a plain capacity-LRU over
    exact ``t`` keys instead thrashes because successive events have distinct
    times.

    Two modes:
    - constellation-like providers (expose ``ecef``/``min_elevation_deg``/
      ``max_isl_km``): the composite queries (elevation / slant range / ISL
      range / ISL availability / certified next-change searches) are
      re-derived from the memoized ``ecef`` and exact-arg memoized results,
      replicating ``Constellation``'s arithmetic bit-for-bit.  This is the
      routing hot path: one trig computation per satellite per instant is
      shared by every edge/endpoint query at that instant.
    - any other provider: every public query is delegated to the inner
      provider and its exact result is memoized.
    """

    #: how many distinct instants each query family retains (FIFO over
    #: first-seen time; hits do not refresh).  Entries under one instant are
    #: bounded by the constellation/endpoint counts, so memory stays small.
    #: A burst of certified next-change bisection samples (unique times) CAN
    #: evict the current event time's slot; that only costs recomputation
    #: (values are bit-identical pure functions), never correctness.
    _TIME_CAPACITY = 128

    def __init__(self, inner, capacity: int = 4096):
        self._inner = inner
        self._capacity = capacity
        self._caches: dict[str, "OrderedDict"] = {}
        self._compose = (
            hasattr(inner, "ecef")
            and hasattr(inner, "min_elevation_deg")
            and hasattr(inner, "max_isl_km")
        )
        self.certifies_change_times = bool(
            getattr(inner, "certifies_change_times", False))
        self.num_satellites = inner.num_satellites

    def __getattr__(self, name):
        # only reached for attributes not defined here; forwards provider
        # surface (e.g. legacy helpers on scripted geometries)
        return getattr(self._inner, name)

    def _slot(self, name: str, t: float) -> dict:
        """Exact-``t`` slot for a query family; evicts oldest instants."""
        cache = self._caches.get(name)
        if cache is None:
            cache = OrderedDict()
            self._caches[name] = cache
        slot = cache.get(t)
        if slot is None:
            slot = {}
            cache[t] = slot
            if len(cache) > self._TIME_CAPACITY:
                cache.popitem(last=False)
        return slot

    def _lookup(self, name: str, t: float, args, compute):
        """Memoize by exact ``t`` slot then exact ``args`` (cold paths)."""
        slot = self._slot(name, t)
        hit = slot.get(args, _MISS)
        if hit is not _MISS:
            return hit
        value = compute()
        slot[args] = value
        return value

    def _lookup_result(self, name: str, key, compute):
        """Plain bounded-LRU memoization for results that repeat across
        different instants (e.g. certified next-change answers for the same
        interval)."""
        cache = self._caches.get(name)
        if cache is None:
            cache = OrderedDict()
            self._caches[name] = cache
        hit = cache.get(key, _MISS)
        if hit is not _MISS:
            cache.move_to_end(key)
            return hit
        value = compute()
        cache[key] = value
        if len(cache) > self._capacity:
            cache.popitem(last=False)
        return value

    # ---- pure queries, memoized by exact arguments -----------------------

    def subpoint(self, sat_id: int, t: float):
        return self._lookup(
            "subpoint", t, (sat_id,),
            lambda: self._inner.subpoint(sat_id, t))

    def positions(self, t: float):
        return self._lookup(
            "positions", t, (), lambda: self._inner.positions(t))

    def neighbors(self, sat_id: int, dirs):
        key_dirs = dirs if isinstance(dirs, str) else tuple(dirs)
        return self._lookup_result(
            "neighbors", (sat_id, key_dirs),
            lambda: self._inner.neighbors(sat_id, dirs))

    def neighbors_at(self, sat_id: int, dirs, t: float):
        key_dirs = dirs if isinstance(dirs, str) else tuple(dirs)
        return self._lookup(
            "neighbors_at", t, (sat_id, key_dirs),
            lambda: self._inner.neighbors_at(sat_id, dirs, t))

    def ecef(self, sat_id: int, t: float):
        slot = self._slot("ecef", t)
        hit = slot.get(sat_id, _MISS)
        if hit is not _MISS:
            return hit
        value = self._inner.ecef(sat_id, t)
        slot[sat_id] = value
        return value

    # ---- composite queries: constellation-mode derivation ----------------

    def elevation_deg(self, sat_id: int, lat: float, lon: float, t: float):
        if self._compose:
            slot = self._slot("elevation_deg", t)
            key = (sat_id, lat, lon)
            hit = slot.get(key, _MISS)
            if hit is not _MISS:
                return hit
            sat = self.ecef(sat_id, t)
            gs = _sph_to_ecef(lat, lon, EARTH_RADIUS_KM)
            dx, dy, dz = sat[0] - gs[0], sat[1] - gs[1], sat[2] - gs[2]
            rng = math.sqrt(dx * dx + dy * dy + dz * dz)
            if rng == 0:
                value = 90.0
            else:
                up = (gs[0] / EARTH_RADIUS_KM,
                      gs[1] / EARTH_RADIUS_KM,
                      gs[2] / EARTH_RADIUS_KM)
                cos_z = (dx * up[0] + dy * up[1] + dz * up[2]) / rng
                value = math.degrees(
                    math.asin(max(-1.0, min(1.0, cos_z))))
            slot[key] = value
            return value
        return self._lookup(
            "elevation_deg", t, (sat_id, lat, lon),
            lambda: self._inner.elevation_deg(sat_id, lat, lon, t))

    def ground_visible(self, sat_id: int, lat: float, lon: float, t: float) -> bool:
        if self._compose:
            slot = self._slot("ground_visible", t)
            key = (sat_id, lat, lon)
            hit = slot.get(key, _MISS)
            if hit is not _MISS:
                return hit
            value = self.elevation_deg(sat_id, lat, lon, t) > \
                self._inner.min_elevation_deg
            slot[key] = value
            return value
        return self._lookup(
            "ground_visible", t, (sat_id, lat, lon),
            lambda: self._inner.ground_visible(sat_id, lat, lon, t))

    def gsl_available(self, sat_id: int, lat: float, lon: float, t: float) -> bool:
        # both model.Constellation and test helpers implement this as
        # ground_visible; keeping the identity here preserves bit-equivalence.
        # NOTE: this assumes gsl_available == ground_visible for every wrapped
        # provider (currently true for both in-repo providers); a future
        # provider with distinct semantics must override or be excluded.
        return self.ground_visible(sat_id, lat, lon, t)

    def slant_range_km(self, sat_id: int, lat: float, lon: float, t: float):
        if self._compose:
            slot = self._slot("slant_range_km", t)
            key = (sat_id, lat, lon)
            hit = slot.get(key, _MISS)
            if hit is not _MISS:
                return hit
            value = math.dist(
                self.ecef(sat_id, t),
                _sph_to_ecef(lat, lon, EARTH_RADIUS_KM))
            slot[key] = value
            return value
        return self._lookup(
            "slant_range_km", t, (sat_id, lat, lon),
            lambda: self._inner.slant_range_km(sat_id, lat, lon, t))

    def isl_range_km(self, a: int, b: int, t: float):
        if self._compose:
            slot = self._slot("isl_range_km", t)
            key = (a, b)
            hit = slot.get(key, _MISS)
            if hit is not _MISS:
                return hit
            value = math.dist(self.ecef(a, t), self.ecef(b, t))
            slot[key] = value
            return value
        return self._lookup(
            "isl_range_km", t, (a, b),
            lambda: self._inner.isl_range_km(a, b, t))

    def isl_available(self, a: int, b: int, t: float) -> bool:
        if self._compose:
            slot = self._slot("isl_available", t)
            key = (a, b)
            hit = slot.get(key, _MISS)
            if hit is not _MISS:
                return hit
            pa = self.ecef(a, t)
            pb = self.ecef(b, t)
            dx, dy, dz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            rng = math.sqrt(dx * dx + dy * dy + dz * dz)
            if rng >= self._inner.max_isl_km or rng == 0:
                value = False
            else:
                dot = pa[0] * dx + pa[1] * dy + pa[2] * dz
                s = max(0.0, min(1.0, -dot / (rng * rng)))
                cx, cy, cz = pa[0] + s * dx, pa[1] + s * dy, pa[2] + s * dz
                closest = math.sqrt(cx * cx + cy * cy + cz * cz)
                value = closest > EARTH_RADIUS_KM
            slot[key] = value
            return value
        return self._lookup(
            "isl_available", t, (a, b),
            lambda: self._inner.isl_available(a, b, t))

    def next_gsl_change(self, sat_id: int, lat: float, lon: float, t: float,
                        limit: float):
        key = (sat_id, lat, lon, t, limit)
        if self._compose:
            def compute():
                def margin(x):
                    return self.elevation_deg(sat_id, lat, lon, x) \
                        - self._inner.min_elevation_deg
                return _next_change_adaptive(margin, t, limit, ELEV_RATE_DEG_S)
            return self._lookup_result("next_gsl_change", key, compute)
        return self._lookup_result(
            "next_gsl_change", key,
            lambda: self._inner.next_gsl_change(sat_id, lat, lon, t, limit))

    def next_isl_change(self, a: int, b: int, t: float, limit: float):
        key = (a, b, t, limit)
        if self._compose:
            def compute():
                def margin(x):
                    pa = self.ecef(a, x)
                    pb = self.ecef(b, x)
                    dx, dy, dz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
                    rng = math.sqrt(dx * dx + dy * dy + dz * dz)
                    if rng == 0:
                        return -1.0
                    dot = pa[0] * dx + pa[1] * dy + pa[2] * dz
                    s = max(0.0, min(1.0, -dot / (rng * rng)))
                    cx, cy, cz = pa[0] + s * dx, pa[1] + s * dy, pa[2] + s * dz
                    closest = math.sqrt(cx * cx + cy * cy + cz * cz)
                    return min(self._inner.max_isl_km - rng,
                               closest - EARTH_RADIUS_KM)
                return _next_change_adaptive(margin, t, limit, RANGE_RATE_KM_S)
            return self._lookup_result("next_isl_change", key, compute)
        return self._lookup_result(
            "next_isl_change", key,
            lambda: self._inner.next_isl_change(a, b, t, limit))

    def next_slant_range_under(self, sat_id: int, lat: float, lon: float,
                               threshold_km: float, t: float, limit: float):
        """Next time in (t, limit] the ground link range drops to
        threshold_km or below (certified with the RANGE_RATE_KM_S bound)."""
        key = (sat_id, lat, lon, threshold_km, t, limit)
        if self._compose:
            def compute():
                def margin(x):
                    return threshold_km - self.slant_range_km(
                        sat_id, lat, lon, x)
                return _next_change_adaptive(margin, t, limit, RANGE_RATE_KM_S)
            return self._lookup_result("next_slant_range_under", key, compute)
        return self._lookup_result(
            "next_slant_range_under", key,
            lambda: self._inner.next_slant_range_under(
                sat_id, lat, lon, threshold_km, t, limit))

    def next_isl_range_under(self, a: int, b: int, threshold_km: float,
                             t: float, limit: float):
        """Next time in (t, limit] the ISL range drops to threshold_km or
        below (certified with the RANGE_RATE_KM_S bound)."""
        key = (a, b, threshold_km, t, limit)
        if self._compose:
            def compute():
                def margin(x):
                    return threshold_km - self.isl_range_km(a, b, x)
                return _next_change_adaptive(margin, t, limit, RANGE_RATE_KM_S)
            return self._lookup_result("next_isl_range_under", key, compute)
        return self._lookup_result(
            "next_isl_range_under", key,
            lambda: self._inner.next_isl_range_under(
                a, b, threshold_km, t, limit))


def _next_change_adaptive(margin, t0: float, t1: float, rate_bound: float,
                          tol: float = 1e-9, max_iter: int = 1_000_000):
    """First time in (t0, t1] where margin(t) crosses zero (available <-> not).

    margin(t) > 0 means "available" (strictly: sitting exactly at the
    threshold is treated as unavailable, matching the left-closed
    change-instant contract used by scripted geometries). Certified stepping:
    if the true margin rate never exceeds rate_bound, the sign cannot flip
    within |margin|/rate_bound, so no crossing is skipped; each bracketed
    crossing is bisected to `tol` seconds. Error contract: a crossing feature
    narrower than what the rate bound admits cannot exist; if a provider's
    dynamics can exceed rate_bound it must not use this routine.
    Deterministic.

    Returns None only when the whole interval is certified change-free.
    Raises GeometryCertificationError on invalid input, non-finite or
    degenerate (exactly zero) margin values, or iteration-budget exhaustion
    (never silently guesses).

    SCHEDULING-ONLY contract: this exists so the discrete-event scheduler can
    compute physical link events. Routing, association and learning decisions
    must never read future geometry through it — it is not an oracle channel.
    """
    if not (math.isfinite(t0) and math.isfinite(t1)) or t1 < t0:
        raise GeometryCertificationError(f"invalid interval ({t0}, {t1}]")
    if not math.isfinite(rate_bound) or rate_bound <= 0:
        raise GeometryCertificationError(f"invalid rate_bound {rate_bound}")
    if not (math.isfinite(tol) and tol > 0):
        raise GeometryCertificationError(f"invalid tol {tol}")
    def _bisect(lo: float, hi: float, ref_sign: bool) -> float:
        """Deterministic bisection of a bracketed sign change to tol seconds."""
        while hi - lo > tol:
            mid = (lo + hi) / 2.0
            mv = margin(mid)
            if not math.isfinite(mv):
                raise GeometryCertificationError(
                    f"non-finite margin at t={mid}")
            if (mv > 0.0) == ref_sign:
                lo = mid
            else:
                hi = mid
        return hi

    prev_t = t0
    prev_v = margin(t0)
    if not math.isfinite(prev_v):
        raise GeometryCertificationError(f"non-finite margin at t={t0}")
    if prev_v == 0.0:
        # The query starts exactly on a zero-margin instant.  This is a
        # transient threshold crossing (a link that just became available or
        # just failed): step a tiny epsilon into (t0, t1] and re-sample.  Only
        # a genuinely identically-zero margin (also zero after the offset) is
        # degenerate and stays fail-closed.
        eps = min(max(tol, 1e-12), (t1 - t0) / 2.0)
        probe = margin(t0 + eps)
        if not math.isfinite(probe):
            raise GeometryCertificationError(
                f"non-finite margin at t={t0 + eps}")
        if probe != 0.0:
            prev_t, prev_v = t0 + eps, probe
        else:
            raise GeometryCertificationError(
                f"degenerate zero margin at t={prev_t}; cannot certify")
    for _ in range(max_iter):
        step = abs(prev_v) / rate_bound
        if step <= 0:
            # margin exactly at the threshold: a crossing could begin at any
            # instant, so no certified step exists — fail closed
            raise GeometryCertificationError(
                f"degenerate zero margin at t={prev_t}; cannot certify")
        if step < tol:
            step = tol
        cand = prev_t + step
        if cand >= t1:
            # t1 belongs to the searched interval (t0, t1]: evaluate it
            # explicitly so a crossing exactly at the interval end is found
            # instead of being reported as "no change".
            v1 = margin(t1)
            if not math.isfinite(v1):
                raise GeometryCertificationError(f"non-finite margin at t={t1}")
            if (v1 > 0.0) != (prev_v > 0.0):
                return _bisect(prev_t, t1, prev_v > 0.0)
            return None
        v = margin(cand)
        if not math.isfinite(v):
            raise GeometryCertificationError(f"non-finite margin at t={cand}")
        if v == 0.0:
            # The step landed exactly on a transient zero-margin instant:
            # the crossing lies in (prev_t, cand], so certify it immediately
            # instead of carrying a zero value into the next step (which would
            # make step=0 and fail closed on a certifiable event).
            return _bisect(prev_t, cand, prev_v > 0.0)
        if (v > 0.0) != (prev_v > 0.0):
            return _bisect(prev_t, cand, prev_v > 0.0)
        prev_t, prev_v = cand, v
    raise GeometryCertificationError(
        f"next-change search exhausted max_iter={max_iter} on ({t0}, {t1}]; "
        "refusing to guess")


class Constellation:
    """num_planes x sats_per_plane Walker-delta constellation.

    certifies_change_times: next_gsl_change/next_isl_change use the certified
    adaptive root-find (_next_change_adaptive) with the documented rate bounds
    ELEV_RATE_DEG_S / RANGE_RATE_KM_S, so a geometry loss inside a service
    interval is never silently skipped.
    """

    certifies_change_times = True

    def __init__(self, num_satellites: int, num_planes: int, altitude_km: float,
                 inclination_deg: float, min_elevation_deg: float = 25.0,
                 max_isl_km: float = 6000.0, geometry_epoch_s: float = 0.0):
        if num_satellites % num_planes != 0:
            raise ValueError("num_satellites must be divisible by num_planes")
        if not math.isfinite(float(geometry_epoch_s)) or geometry_epoch_s < 0:
            raise ValueError("geometry_epoch_s must be finite and >= 0")
        self.num_satellites = num_satellites
        self.num_planes = num_planes
        self.per_plane = num_satellites // num_planes
        self.altitude_km = altitude_km
        self.inclination_deg = inclination_deg
        self.min_elevation_deg = min_elevation_deg
        self.max_isl_km = max_isl_km
        self.geometry_epoch_s = float(geometry_epoch_s)
        self.r = EARTH_RADIUS_KM + altitude_km
        # circular orbit period
        mu = 398600.4418  # km^3/s^2
        self.period_s = 2 * math.pi * math.sqrt(self.r ** 3 / mu)

    def subpoint(self, sat_id: int, t: float) -> tuple[float, float, float]:
        """Geodetic lat/lon (deg) and altitude (km) of sat subpoint at time t.

        A single explicit deterministic phase block: the epoch is applied
        exactly once here so every higher geometry method (ecef, elevation,
        visibility, ISL availability, certified change times) inherits it.
        geometry_epoch_s = 0 keeps the historical bytes bit-identical."""
        at = float(t) + self.geometry_epoch_s
        plane = sat_id // self.per_plane
        idx = sat_id % self.per_plane
        raan = 2 * math.pi * plane / self.num_planes
        phase = 2 * math.pi * (idx / self.per_plane + at / self.period_s)
        inc = math.radians(self.inclination_deg)
        lat = math.asin(math.sin(inc) * math.sin(phase))
        lon_inertial = math.atan2(math.cos(inc) * math.sin(phase), math.cos(phase)) + raan
        lon = lon_inertial - EARTH_ROT_RATE_RAD_S * at
        lon = math.degrees((lon + math.pi) % (2 * math.pi) - math.pi)
        return math.degrees(lat), lon, self.altitude_km

    def ecef(self, sat_id: int, t: float) -> tuple[float, float, float]:
        lat, lon, _ = self.subpoint(sat_id, t)
        return _sph_to_ecef(lat, lon, self.r)

    def positions(self, t: float) -> tuple[tuple[float, float, float], ...]:
        return tuple(self.ecef(i, t) for i in range(self.num_satellites))

    def elevation_deg(self, sat_id: int, lat: float, lon: float, t: float) -> float:
        sat = self.ecef(sat_id, t)
        gs = _sph_to_ecef(lat, lon, EARTH_RADIUS_KM)
        dx, dy, dz = sat[0] - gs[0], sat[1] - gs[1], sat[2] - gs[2]
        rng = math.sqrt(dx * dx + dy * dy + dz * dz)
        if rng == 0:
            return 90.0
        # angle between ground->sat vector and local zenith
        up = (gs[0] / EARTH_RADIUS_KM, gs[1] / EARTH_RADIUS_KM, gs[2] / EARTH_RADIUS_KM)
        cos_z = (dx * up[0] + dy * up[1] + dz * up[2]) / rng
        return math.degrees(math.asin(max(-1.0, min(1.0, cos_z))))

    def ground_visible(self, sat_id: int, lat: float, lon: float, t: float) -> bool:
        # strictly above the threshold: matches the certified next-change
        # sign convention (margin > 0 == available)
        return self.elevation_deg(sat_id, lat, lon, t) > self.min_elevation_deg

    def slant_range_km(self, sat_id: int, lat: float, lon: float, t: float) -> float:
        sat = self.ecef(sat_id, t)
        gs = _sph_to_ecef(lat, lon, EARTH_RADIUS_KM)
        return math.dist(sat, gs)

    def isl_range_km(self, a: int, b: int, t: float) -> float:
        return math.dist(self.ecef(a, t), self.ecef(b, t))

    def neighbors(self, sat_id: int, dirs) -> dict[str, int]:
        """Directional ISL neighbors: N/S intra-plane, E/W adjacent plane."""
        plane = sat_id // self.per_plane
        idx = sat_id % self.per_plane
        out = {}
        if "N" in dirs:
            out["N"] = plane * self.per_plane + (idx + 1) % self.per_plane
        if "S" in dirs:
            out["S"] = plane * self.per_plane + (idx - 1) % self.per_plane
        if "E" in dirs:
            out["E"] = ((plane + 1) % self.num_planes) * self.per_plane + idx
        if "W" in dirs:
            out["W"] = ((plane - 1) % self.num_planes) * self.per_plane + idx
        return out

    def neighbors_at(self, sat_id: int, dirs, t: float) -> dict[str, int]:
        """Dynamic ISL neighbors at time t (D2: legacy Markovian rematch).

        N/S intra-plane neighbors stay fixed by index (matches the old
        platform's findIntraNeighbours).  E/W cross-plane neighbors are
        re-matched greedily by shortest current range with one transceiver per
        direction per satellite (legacy markovianMatchingTwo semantics,
        SimulationRL.py:8330-8433); only physically available candidates
        (max_isl_km + earth clearance) are admitted.
        """
        out = self.neighbors(sat_id, [d for d in dirs if d in ("N", "S")])
        if not any(d in dirs for d in ("E", "W")):
            return out
        cross = self._cross_plane_matching(dirs, t)
        for d in ("E", "W"):
            if d in dirs and cross.get(sat_id, {}).get(d) is not None:
                out[d] = cross[sat_id][d]
        return out

    def _cross_plane_matching(self, dirs, t: float) -> dict[int, dict[str, int]]:
        """Greedy shortest-range E/W matching across adjacent planes."""
        used: dict[int, set[str]] = {
            s: set() for s in range(self.num_satellites)}
        edges: list[tuple[float, int, int]] = []
        for p in range(self.num_planes):
            if "E" not in dirs:
                break
            east_plane = (p + 1) % self.num_planes
            for i in range(self.per_plane):
                a = p * self.per_plane + i
                for j in range(self.per_plane):
                    b = east_plane * self.per_plane + j
                    if not self.isl_available(a, b, t):
                        continue
                    r = self.isl_range_km(a, b, t)
                    if r >= self.max_isl_km:
                        continue
                    edges.append((r, a, b))
        edges.sort()
        cross: dict[int, dict[str, int]] = {}
        for _r, a, b in edges:
            if "E" in used[a] or "W" in used[b]:
                continue
            used[a].add("E")
            used[b].add("W")
            cross.setdefault(a, {})["E"] = b
            cross.setdefault(b, {})["W"] = a
        return cross

    # ---- dynamic availability (checked at every use, never cached) --------

    def gsl_available(self, sat_id: int, lat: float, lon: float, t: float) -> bool:
        return self.ground_visible(sat_id, lat, lon, t)

    def isl_available(self, a: int, b: int, t: float) -> bool:
        """Earth-occultation and max-range check at time t (strict margins,
        matching the certified next-change sign convention)."""
        pa = self.ecef(a, t)
        pb = self.ecef(b, t)
        dx, dy, dz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        rng = math.sqrt(dx * dx + dy * dy + dz * dz)
        if rng >= self.max_isl_km or rng == 0:
            return False
        # closest approach of the a-b segment to Earth's centre
        dot = pa[0] * dx + pa[1] * dy + pa[2] * dz
        s = max(0.0, min(1.0, -dot / (rng * rng)))
        cx, cy, cz = pa[0] + s * dx, pa[1] + s * dy, pa[2] + s * dz
        closest = math.sqrt(cx * cx + cy * cy + cz * cz)
        return closest > EARTH_RADIUS_KM

    def next_gsl_change(self, sat_id: int, lat: float, lon: float, t: float,
                        limit: float):
        """Next time in (t, limit] where GSL availability flips, or None.

        Certified adaptive root-find on the elevation margin (available iff
        elevation - min_elevation >= 0) with the ELEV_RATE_DEG_S bound."""
        def margin(x):
            return self.elevation_deg(sat_id, lat, lon, x) - self.min_elevation_deg
        return _next_change_adaptive(margin, t, limit, ELEV_RATE_DEG_S)

    def next_isl_change(self, a: int, b: int, t: float, limit: float):
        """Next time in (t, limit] where ISL availability flips, or None.

        Margin = min(max_isl_km - range, earth-clearance - R_earth), stepping
        certified by the RANGE_RATE_KM_S bound."""

        def margin(x):
            pa = self.ecef(a, x)
            pb = self.ecef(b, x)
            dx, dy, dz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
            rng = math.sqrt(dx * dx + dy * dy + dz * dz)
            if rng == 0:
                return -1.0
            dot = pa[0] * dx + pa[1] * dy + pa[2] * dz
            s = max(0.0, min(1.0, -dot / (rng * rng)))
            cx, cy, cz = pa[0] + s * dx, pa[1] + s * dy, pa[2] + s * dz
            closest = math.sqrt(cx * cx + cy * cy + cz * cz)
            return min(self.max_isl_km - rng, closest - EARTH_RADIUS_KM)

        return _next_change_adaptive(margin, t, limit, RANGE_RATE_KM_S)

    def next_slant_range_under(self, sat_id: int, lat: float, lon: float,
                               threshold_km: float, t: float, limit: float):
        """Next time in (t, limit] the GSL range is at most threshold_km.

        Scheduling-only query (never an oracle channel for routing/learning);
        stepping certified by the RANGE_RATE_KM_S bound, which also covers the
        ground-station line-of-sight geometry (satellite orbital speed +
        Earth rotation is bounded by the same constant).
        """
        def margin(x):
            return threshold_km - self.slant_range_km(sat_id, lat, lon, x)

        return _next_change_adaptive(margin, t, limit, RANGE_RATE_KM_S)

    def next_isl_range_under(self, a: int, b: int, threshold_km: float,
                             t: float, limit: float):
        """Next time in (t, limit] the ISL range is at most threshold_km.

        Scheduling-only query (never an oracle channel for routing/learning);
        stepping certified by the RANGE_RATE_KM_S bound.
        """
        def margin(x):
            return threshold_km - self.isl_range_km(a, b, x)

        return _next_change_adaptive(margin, t, limit, RANGE_RATE_KM_S)
