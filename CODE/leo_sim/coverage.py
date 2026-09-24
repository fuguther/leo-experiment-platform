"""Deterministic geometry-only access coverage audit.

This module samples an already-resolved geometry provider.  It has no route,
queue, learning, or capacity semantics and therefore cannot decide whether a
constellation is sufficient for a traffic experiment.

Two endpoint paths share the same sampled audit:

- ``--trace`` (v1, scalar reference ``scan_coverage``): endpoints are
  resolved from an immutable trace's active cells.  The legacy caps stay
  unchanged (10,000 endpoints, 50,000,000 scalar visibility calls).
- ``--population`` (v2, chunked vectorized ``scan_constellation_coverage``):
  endpoints are every positive-population region of the checked-in GPW
  raster aggregated to the audited degree (default 1.0).  This path has its
  own explicit contract (population raster source, 20,000 endpoint cap,
  1,000,001 sample cap, 50e9 comparison cap, bounded working memory) and
  must never be reached from arbitrary trace endpoints.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import resource
import sys
from pathlib import Path
from typing import Any

import numpy as np

from . import model


class CoverageAuditError(ValueError):
    """Coverage audit input or geometry contract is invalid."""


# The 56-endpoint x 140-satellite x 3,601-sample audit is about 28.2M
# visibility checks. Keep a generous but finite headroom without allowing an
# accidental tiny step to allocate unbounded time samples.
MAX_COVERAGE_CHECKS = 50_000_000
MAX_COVERAGE_SAMPLES = 1_000_001
MAX_COVERAGE_ENDPOINTS = 10_000

# Population-vector audit contract (Task 3).  Distinct from the scalar caps:
# the full positive-population 1-degree universe is ~16,988 endpoints and a
# 600 s/60 s smoke is already ~26M comparisons, so it cannot legally pass
# through the scalar 50,000,000-cap either.  The vector path is separate,
# explicit, and only ever accepts a population_raster endpoint source.
VECTOR_MAX_ENDPOINTS = 20_000
VECTOR_MAX_SAMPLES = 1_000_001
VECTOR_MAX_COMPARISONS = 50_000_000_000
VECTOR_MAX_WORKING_MIB = 4096.0
COVERAGE_AUDIT_V2 = "leo-sim-coverage-audit/v2"
_FP_EPS = np.finfo(np.float64).eps
# declared floating-point ambiguity band for the vectorized footprint test:
# algebraically equivalent formulas are not bit-identical, so pairs whose
# cosine margin is within 64 * machine_epsilon of the boundary are routed to
# the existing scalar predicate instead of being guessed by the fast path.
VECTOR_AMBIGUITY_BAND = 64 * _FP_EPS


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CoverageAuditError(f"{name} must be finite numeric")
    value = float(value)
    if not math.isfinite(value):
        raise CoverageAuditError(f"{name} must be finite numeric")
    return value


def _endpoints(endpoints) -> list[dict[str, Any]]:
    if not isinstance(endpoints, (list, tuple)):
        raise CoverageAuditError("endpoints must be a finite list")
    out = []
    seen = set()
    for index, item in enumerate(endpoints):
        if not isinstance(item, dict):
            raise CoverageAuditError(f"endpoint {index} must be a mapping")
        name = item.get("name", item.get("cell"))
        if not isinstance(name, str) or not name:
            raise CoverageAuditError(f"endpoint {index}.name must be non-empty")
        if name in seen:
            raise CoverageAuditError(f"duplicate endpoint name {name!r}")
        seen.add(name)
        lat = _finite(item.get("lat"), f"endpoint {name}.lat")
        lon = _finite(item.get("lon"), f"endpoint {name}.lon")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise CoverageAuditError(f"endpoint {name} lat/lon out of range")
        out.append({"name": name, "lat": lat, "lon": lon})
    return sorted(out, key=lambda item: item["name"])


def _sample_times(horizon: float, step: float) -> list[float]:
    """Inclusive sample instants 0, step, ..., horizon (scalar semantics)."""
    interval_count = max(1, math.ceil(horizon / step - 1e-12))
    times = [min(float(i * step), horizon) for i in range(interval_count)]
    times.append(float(horizon))
    return times


def _interval_and_sample_count(horizon: float, step: float):
    """Mathematical sample-count semantics WITHOUT allocating the instants:
    caps must fail before any allocation."""
    interval_count = max(1, math.ceil(horizon / step - 1e-12))
    return interval_count, interval_count + 1


def scan_coverage(geometry, endpoints, *, horizon_s: float, step_s: float,
                  visible_fraction_threshold: float = 0.5,
                  provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """Scan inclusive samples ``0, step, ..., horizon`` in stable order."""
    horizon = _finite(horizon_s, "horizon_s")
    step = _finite(step_s, "step_s")
    threshold = _finite(visible_fraction_threshold,
                        "visible_fraction_threshold")
    if horizon <= 0:
        raise CoverageAuditError("horizon_s must be > 0")
    if step <= 0:
        raise CoverageAuditError("step_s must be > 0")
    if step > horizon:
        raise CoverageAuditError("step_s must be <= horizon_s")
    if not 0 <= threshold <= 1:
        raise CoverageAuditError("visible_fraction_threshold must be in [0, 1]")
    if not hasattr(geometry, "num_satellites") or not hasattr(
            geometry, "ground_visible"):
        raise CoverageAuditError("geometry must expose num_satellites and ground_visible")
    n_sat = geometry.num_satellites
    if isinstance(n_sat, bool) or not isinstance(n_sat, int) or n_sat < 1:
        raise CoverageAuditError("geometry.num_satellites must be a positive integer")
    endpoint_specs = _endpoints(endpoints)
    if len(endpoint_specs) > MAX_COVERAGE_ENDPOINTS:
        raise CoverageAuditError(
            f"endpoint_count {len(endpoint_specs)} exceeds limit "
            f"{MAX_COVERAGE_ENDPOINTS}")
    _interval_count, sample_count = _interval_and_sample_count(horizon, step)
    if sample_count > MAX_COVERAGE_SAMPLES:
        raise CoverageAuditError(
            f"sample_count {sample_count} exceeds limit "
            f"{MAX_COVERAGE_SAMPLES}")
    checks = len(endpoint_specs) * sample_count * n_sat
    if checks > MAX_COVERAGE_CHECKS:
        raise CoverageAuditError(
            f"coverage checks {checks} exceeds limit {MAX_COVERAGE_CHECKS} "
            f"(endpoints={len(endpoint_specs)}, samples={sample_count}, "
            f"satellites={n_sat})")
    times = _sample_times(horizon, step)
    results = []
    for endpoint in endpoint_specs:
        counts = []
        for at in times:
            count = sum(bool(geometry.ground_visible(
                sat, endpoint["lat"], endpoint["lon"], at))
                        for sat in range(n_sat))
            counts.append(count)
        visible_samples = sum(count > 0 for count in counts)
        never = visible_samples == 0
        first = next((at for at, count in zip(times, counts) if count > 0), None)
        max_gap = None
        if not never:
            gaps = []
            gap_start = None
            for at, count in zip(times, counts):
                if count == 0 and gap_start is None:
                    gap_start = at
                elif count > 0 and gap_start is not None:
                    gaps.append(at - gap_start)
                    gap_start = None
            if gap_start is not None:
                gaps.append(horizon - gap_start)
            max_gap = max(gaps, default=0.0)
        results.append({
            **endpoint,
            "visible_fraction": visible_samples / len(times),
            "first_visible_wait_s": first,
            "max_no_coverage_gap_s": max_gap,
            "never_visible": never,
            "visible_satellites": {
                "min": min(counts),
                "mean": sum(counts) / len(counts),
                "max": max(counts),
            },
            "min_visible_satellites": min(counts),
            "mean_visible_satellites": sum(counts) / len(counts),
            "max_visible_satellites": max(counts),
        })
    total = len(results)
    return {
        "schema": "leo-sim-coverage-audit/v1",
        "scan": {"horizon_s": horizon, "step_s": step,
                 "sample_count": len(times),
                 "visible_fraction_threshold": threshold},
        "endpoints": results,
        "summary": {
            "endpoints_total": total,
            "never_visible": sum(item["never_visible"] for item in results),
            "threshold_met_fraction": (
                sum(item["visible_fraction"] >= threshold for item in results)
                / total if total else 0.0),
        },
        "provenance": provenance or {},
    }


def _unit_vectors(endpoint_specs: list[dict[str, Any]]) -> np.ndarray:
    """ECEF unit vectors (E, 3) for a list of lat/lon endpoint specs."""
    lats = np.radians(np.array([e["lat"] for e in endpoint_specs],
                               dtype=np.float64))
    lons = np.radians(np.array([e["lon"] for e in endpoint_specs],
                               dtype=np.float64))
    return np.stack([
        np.cos(lats) * np.cos(lons),
        np.cos(lats) * np.sin(lons),
        np.sin(lats),
    ], axis=1)


def _sat_unit_vectors(geometry, n_sat: int, at: float) -> np.ndarray:
    """Same-altitude satellite unit vectors (S, 3) at time at."""
    units = np.empty((n_sat, 3), dtype=np.float64)
    for s in range(n_sat):
        x, y, z = geometry.ecef(s, at)
        norm = math.sqrt(x * x + y * y + z * z)
        units[s] = (x / norm, y / norm, z / norm)
    return units


def _population_endpoint_specs(
        endpoints, endpoint_source: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate population-weighted endpoints (fail closed before any
    allocation).  Unlike the scalar _endpoints, the population weight is a
    required, strictly positive finite field."""
    if endpoint_source is None or not isinstance(endpoint_source, dict):
        raise CoverageAuditError(
            "population-vector audit requires endpoint_source")
    if endpoint_source.get("type") != "population_raster":
        raise CoverageAuditError(
            "endpoint_source.type must be population_raster")
    candidate_regions = endpoint_source.get("candidate_regions")
    if isinstance(candidate_regions, bool) or not isinstance(
            candidate_regions, int) or candidate_regions < 1:
        raise CoverageAuditError("candidate_regions must be a positive integer")
    if len(endpoints) != candidate_regions:
        raise CoverageAuditError(
            "candidate_regions must equal endpoint count (no silently "
            "omitted endpoints)")
    if not isinstance(endpoints, (list, tuple)):
        raise CoverageAuditError("endpoints must be a finite list")
    seen = set()
    specs = []
    for index, item in enumerate(endpoints):
        if not isinstance(item, dict):
            raise CoverageAuditError(f"endpoint {index} must be a mapping")
        name = item.get("name", item.get("cell"))
        if not isinstance(name, str) or not name:
            raise CoverageAuditError(f"endpoint {index}.name must be non-empty")
        if name in seen:
            raise CoverageAuditError(f"duplicate endpoint name {name!r}")
        seen.add(name)
        lat = _finite(item.get("lat"), f"endpoint {name}.lat")
        lon = _finite(item.get("lon"), f"endpoint {name}.lon")
        if not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise CoverageAuditError(f"endpoint {name} lat/lon out of range")
        pop = item.get("population")
        if isinstance(pop, bool) or not isinstance(pop, (int, float)) \
                or not math.isfinite(pop) or pop <= 0:
            raise CoverageAuditError(
                f"endpoint {name} must have a positive finite "
                "population weight")
        specs.append({"name": name, "lat": lat, "lon": lon,
                      "population": float(pop)})
    specs.sort(key=lambda spec: spec["name"])
    total = endpoint_source.get("total_population")
    if isinstance(total, bool) or not isinstance(total, (int, float)) \
            or not math.isfinite(total) or total <= 0:
        raise CoverageAuditError("total_population must be a positive number")
    weighted = sum(spec["population"] for spec in specs)
    if not math.isclose(weighted, float(total), rel_tol=1e-12,
                        abs_tol=1e-9):
        raise CoverageAuditError(
            "total_population must equal the sum of endpoint population "
            "weights (no silently omitted endpoints)")
    return specs


def scan_constellation_coverage(
        geometry, endpoints, *, horizon_s: float, step_s: float,
        visible_fraction_threshold: float = 0.5,
        endpoint_source: dict[str, Any] | None = None,
        max_working_mib: float = 256.0,
        full_scan: bool = False,
        provenance: dict[str, Any] | None = None) -> dict[str, Any]:
    """Chunked vectorized population-raster coverage audit (v2 report).

    Separate explicit contract (never reached from arbitrary trace
    endpoints): the endpoint source must be a population raster, endpoint
    count <= 20,000, sample count <= 1,000,001, endpoint*sample*satellite
    comparisons <= 50,000,000,000 and 0 < max_working_mib <= 4096.  All
    gates fail before any allocation.  The visible-at-boundary pairs use an
    exact 64 * machine_epsilon cosine-margin band and fall back to the
    existing scalar ground_visible predicate, whose calls are counted.
    """
    horizon = _finite(horizon_s, "horizon_s")
    step = _finite(step_s, "step_s")
    if horizon <= 0:
        raise CoverageAuditError("horizon_s must be > 0")
    if step <= 0:
        raise CoverageAuditError("step_s must be > 0")
    if step > horizon:
        raise CoverageAuditError("step_s must be <= horizon_s")
    if endpoint_source is None or not isinstance(endpoint_source, dict):
        raise CoverageAuditError(
            "population-vector audit requires a population_raster "
            "endpoint_source")
    if endpoint_source.get("type") != "population_raster":
        raise CoverageAuditError(
            "endpoint_source.type must be population_raster")
    if not (isinstance(max_working_mib, (int, float))
            and not isinstance(max_working_mib, bool)
            and math.isfinite(max_working_mib)
            and 0 < max_working_mib <= VECTOR_MAX_WORKING_MIB):
        raise CoverageAuditError(
            f"max_working_mib must be in (0, {VECTOR_MAX_WORKING_MIB:.0f}]")
    n_sat = geometry.num_satellites
    if isinstance(n_sat, bool) or not isinstance(n_sat, int) or n_sat < 1:
        raise CoverageAuditError("geometry.num_satellites must be a positive integer")
    if not hasattr(geometry, "r") or not hasattr(geometry, "min_elevation_deg"):
        raise CoverageAuditError(
            "vector path requires a same-altitude constellation geometry "
            "(r and min_elevation_deg)")
    specs = _population_endpoint_specs(endpoints, endpoint_source)
    if len(specs) > VECTOR_MAX_ENDPOINTS:
        raise CoverageAuditError(
            f"endpoint_count {len(specs)} exceeds the population-vector "
            f"limit {VECTOR_MAX_ENDPOINTS:,}")
    _interval_count, sample_count = _interval_and_sample_count(horizon, step)
    if sample_count > VECTOR_MAX_SAMPLES:
        raise CoverageAuditError(
            f"sample_count {sample_count} exceeds the population-vector "
            f"limit {VECTOR_MAX_SAMPLES:,}")
    checks = len(specs) * sample_count * n_sat
    if checks > VECTOR_MAX_COMPARISONS:
        raise CoverageAuditError(
            f"coverage comparisons {checks} exceed the population-vector "
            f"limit {VECTOR_MAX_COMPARISONS:,} (endpoints={len(specs)}, "
            f"samples={sample_count}, satellites={n_sat})")
    times = _sample_times(horizon, step)

    # exact spherical footprint for a same-altitude constellation
    elevation = math.radians(float(geometry.min_elevation_deg))
    footprint_angle = math.acos(
        model.EARTH_RADIUS_KM / float(geometry.r) * math.cos(elevation)) \
        - elevation
    cos_footprint = math.cos(footprint_angle)

    # chunk endpoints and times so the peak working set stays under the
    # caller-specified ceiling; never allocate an E x S x T tensor
    budget = float(max_working_mib) * 1024.0 * 1024.0 * 0.8
    e_chunk = min(len(specs), 4096)
    t_chunk = min(sample_count, 4096)
    while (e_chunk * n_sat * 8 + t_chunk * n_sat * 3 * 8) > budget \
            and (e_chunk > 1 or t_chunk > 1):
        if t_chunk > 64:
            t_chunk = max(1, t_chunk // 2)
        else:
            e_chunk = max(1, e_chunk // 2)
    projected_bytes = (e_chunk * n_sat * 8 + t_chunk * n_sat * 3 * 8
                       + e_chunk * 3 * 8)

    ep_units = _unit_vectors(specs)  # (E, 3)
    rss_before = _peak_rss_mib()
    fallback_count = 0
    rows = []

    # stream times per endpoint chunk so per-endpoint state stays O(E_c)
    for e0 in range(0, len(specs), e_chunk):
        e1 = min(len(specs), e0 + e_chunk)
        chunk_units = ep_units[e0:e1]  # (E_c, 3)
        ec = e1 - e0
        count_sum = [0.0] * ec
        visible_samples = [0] * ec
        first = [None] * ec
        min_count = [None] * ec
        max_count = [0] * ec
        gap_start = [None] * ec
        max_gap = [0.0] * ec
        for t0 in range(0, sample_count, t_chunk):
            t1 = min(sample_count, t0 + t_chunk)
            chunk_times = times[t0:t1]
            sat_units = np.stack([_sat_unit_vectors(geometry, n_sat, at)
                                  for at in chunk_times])  # (T_c, S, 3)
            for ti, at in enumerate(chunk_times):
                dot = chunk_units @ sat_units[ti].T  # (E_c, S)
                margin = dot - cos_footprint
                approx_visible = margin > VECTOR_AMBIGUITY_BAND
                ambiguous = np.abs(margin) <= VECTOR_AMBIGUITY_BAND
                if ambiguous.any():
                    for ei, si in zip(*np.nonzero(ambiguous)):
                        spec = specs[e0 + ei]
                        approx_visible[ei, si] = bool(geometry.ground_visible(
                            int(si), spec["lat"], spec["lon"], at))
                        fallback_count += 1
                counts = approx_visible.sum(axis=1).astype(int)  # (E_c,)
                for i in range(ec):
                    c = int(counts[i])
                    count_sum[i] += c
                    if c > 0:
                        visible_samples[i] += 1
                        if first[i] is None:
                            first[i] = at
                        if gap_start[i] is not None:
                            max_gap[i] = max(max_gap[i], at - gap_start[i])
                            gap_start[i] = None
                    else:
                        if gap_start[i] is None:
                            gap_start[i] = at
                    if min_count[i] is None or c < min_count[i]:
                        min_count[i] = c
                    max_count[i] = max(max_count[i], c)
        for i in range(ec):
            never = visible_samples[i] == 0
            m = (min_count[i] if not never else 0)
            mean = count_sum[i] / sample_count
            gap = max_gap[i]
            if not never and gap_start[i] is not None:
                gap = max(gap, horizon - gap_start[i])
            spec = specs[e0 + i]
            rows.append({
                **spec,
                "visible_fraction": visible_samples[i] / sample_count,
                "first_visible_wait_s": first[i],
                "max_no_coverage_gap_s": (gap if not never else None),
                "never_visible": never,
                "visible_satellites": {"min": m, "mean": mean, "max": max_count[i]},
                "min_visible_satellites": m,
                "mean_visible_satellites": mean,
                "max_visible_satellites": max_count[i],
            })
    rss_after = _peak_rss_mib()
    # ru_maxrss is a monotone process-wide high-water mark whose baseline
    # (imports, allocator noise) varies between processes, which would break
    # the byte-stable repeat of the report.  Record the peak RSS attributable
    # to the audit itself (delta above the pre-scan mark) at whole-MiB
    # granularity so the same command reproduces the same bytes.
    observed_rss_mib = math.floor(max(0.0, rss_after - rss_before))
    rows.sort(key=lambda row: row["name"])
    # The population denominator is recomputed from the ledger rows in the
    # report's own (sorted) order so the L1 verifier reproduces the summary
    # bit-for-bit; the declared source total is bound by isclose at input.
    row_total_population = sum(float(r["population"]) for r in rows)
    weighted_visible = sum(float(r["population"]) * r["visible_fraction"]
                           for r in rows)
    weighted_never = sum(float(r["population"]) * (1.0 if r["never_visible"]
                                                   else 0.0)
                         for r in rows)
    return {
        "schema": COVERAGE_AUDIT_V2,
        "endpoint_source": {
            "type": "population_raster",
            "source_sha256": endpoint_source["source_sha256"],
            "aggregation_deg": endpoint_source["aggregation_deg"],
            "candidate_regions": len(rows),
            "total_population": endpoint_source["total_population"],
        },
        "scan": {
            "horizon_s": horizon,
            "step_s": step,
            "sample_count": sample_count,
            "sampling_error_bound_s": step,
            "geometry_epoch_s": float(getattr(geometry, "geometry_epoch_s",
                                              0.0)),
        },
        "limits": {
            "max_endpoints": VECTOR_MAX_ENDPOINTS,
            "max_samples": VECTOR_MAX_SAMPLES,
            "max_comparisons": VECTOR_MAX_COMPARISONS,
            "max_working_mib": float(max_working_mib),
        },
        "evaluation": {
            "comparison_count": checks,
            "satellite_count": n_sat,
            "endpoint_chunk_size": e_chunk,
            "time_chunk_size": t_chunk,
            "projected_bytes": projected_bytes,
            "observed_peak_rss_mib": observed_rss_mib,
            "full_scan": bool(full_scan),
            "scalar_fallback_count": fallback_count,
        },
        "endpoints": rows,
        "summary": {
            "endpoints_total": len(rows),
            "never_visible": sum(1 for r in rows if r["never_visible"]),
            "population_weighted_visible_fraction": (
                weighted_visible / row_total_population),
            "population_weighted_never_visible_fraction": (
                weighted_never / row_total_population),
        },
        "provenance": provenance or {},
    }


def _peak_rss_mib() -> float:
    """Observed peak resident set size of this process, in MiB."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":  # bytes on macOS
        return rss / (1024.0 * 1024.0)
    return rss / 1024.0  # KiB on Linux


def verify_coverage_audit_v2(report: dict[str, Any],
                             expected_source_sha256: str | None = None,
                             expected_candidate_regions: int | None = None,
                             ) -> list[str]:
    """L1 verifier for the population-vector audit: recompute every summary
    value from the endpoint ledger and validate the source/scan contract.
    Empty list = verified."""
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["coverage audit report must be a JSON object"]
    if report.get("schema") != COVERAGE_AUDIT_V2:
        errors.append("coverage audit schema must be " + COVERAGE_AUDIT_V2)
    source = report.get("endpoint_source")
    if not isinstance(source, dict):
        errors.append("endpoint_source must be a mapping")
        source = {}
    if source.get("type") != "population_raster":
        errors.append("endpoint_source.type must be population_raster")
    sha = source.get("source_sha256")
    if not (isinstance(sha, str) and len(sha) == 64
            and all(c in "0123456789abcdef" for c in sha)):
        errors.append("endpoint_source.source_sha256 must be lowercase SHA-256")
    elif expected_source_sha256 is not None \
            and sha != expected_source_sha256:
        errors.append("endpoint_source.source_sha256 != expected source SHA")
    if expected_candidate_regions is not None \
            and source.get("candidate_regions") != expected_candidate_regions:
        errors.append("endpoint_source.candidate_regions != expected count")
    agg = source.get("aggregation_deg")
    if isinstance(agg, bool) or not isinstance(agg, (int, float)) \
            or not math.isfinite(agg) or agg <= 0:
        errors.append("endpoint_source.aggregation_deg must be positive")
    candidates = source.get("candidate_regions")
    scan = report.get("scan")
    if not isinstance(scan, dict):
        errors.append("scan must be a mapping")
        scan = {}
    horizon = scan.get("horizon_s")
    step = scan.get("step_s")
    samples = scan.get("sample_count")
    if isinstance(horizon, bool) or not isinstance(horizon, (int, float)) \
            or horizon <= 0:
        errors.append("scan.horizon_s must be positive")
    if isinstance(step, bool) or not isinstance(step, (int, float)) \
            or step <= 0:
        errors.append("scan.step_s must be positive")
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1:
        errors.append("scan.sample_count must be a positive integer")
    if (isinstance(horizon, (int, float)) and not isinstance(horizon, bool)
            and isinstance(step, (int, float)) and not isinstance(step, bool)
            and isinstance(samples, int) and not isinstance(samples, bool)
            and step > 0):
        expected = len(_sample_times(float(horizon), float(step)))
        if samples != expected:
            errors.append(f"scan.sample_count {samples} != recomputed "
                          f"{expected} from horizon/step")
    if scan.get("sampling_error_bound_s") != step:
        errors.append("scan.sampling_error_bound_s must equal step_s")
    epoch = scan.get("geometry_epoch_s")
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)) \
            or not math.isfinite(epoch) or epoch < 0:
        errors.append("scan.geometry_epoch_s must be non-negative")

    rows = report.get("endpoints")
    if not isinstance(rows, list) or not rows:
        errors.append("endpoints ledger must be a non-empty list")
        rows = []
    seen_names = set()
    never_count = 0
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"endpoints[{i}] must be a mapping")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"endpoints[{i}].name must be a non-empty string")
        elif name in seen_names:
            errors.append(f"endpoints[{i}].name {name!r} duplicated")
        else:
            seen_names.add(name)
        for key in ("lat", "lon"):
            v = row.get(key)
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v):
                errors.append(f"endpoints[{i}].{key} must be finite numeric")
        pop = row.get("population")
        if isinstance(pop, bool) or not isinstance(pop, (int, float)) \
                or not math.isfinite(pop) or pop <= 0:
            errors.append(f"endpoints[{i}].population must be positive "
                          "finite numeric")
        vf = row.get("visible_fraction")
        if isinstance(vf, bool) or not isinstance(vf, (int, float)) \
                or not math.isfinite(vf) or not 0.0 <= vf <= 1.0:
            errors.append(f"endpoints[{i}].visible_fraction out of [0, 1]")
        never = row.get("never_visible")
        if never is not True and never is not False:
            errors.append(f"endpoints[{i}].never_visible must be bool")
        if never is True and (vf or 0) != 0.0:
            errors.append(f"endpoints[{i}] never_visible but visible_fraction "
                          "> 0")
        if never is True:
            never_count += 1
    # Weighted fractions: recompute with the IDENTICAL builtin-sum
    # expressions and row order the producer used (sorted ledger), so the
    # verification reproduces the summary bit-for-bit instead of differing
    # in the last ulp of a different summation order.
    row_total_population = sum(float(r["population"]) for r in rows
                               if isinstance(r.get("population"), (int, float))
                               and not isinstance(r.get("population"), bool))
    weighted_visible = sum(
        float(r["population"]) * r["visible_fraction"]
        for r in rows
        if isinstance(r.get("population"), (int, float))
        and not isinstance(r.get("population"), bool)
        and isinstance(r.get("visible_fraction"), (int, float))
        and not isinstance(r.get("visible_fraction"), bool))
    weighted_never = sum(
        float(r["population"])
        for r in rows
        if r.get("never_visible") is True
        and isinstance(r.get("population"), (int, float))
        and not isinstance(r.get("population"), bool))
    summary = report.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be a mapping")
        summary = {}
    if isinstance(candidates, int) and not isinstance(candidates, bool):
        if candidates != len(rows):
            errors.append("candidate_regions must equal the endpoint ledger "
                          "length")
    if summary.get("endpoints_total") != len(rows):
        errors.append("summary.endpoints_total != endpoint ledger length")
    if summary.get("never_visible") != never_count:
        errors.append("summary.never_visible != recomputed from ledger")
    if row_total_population > 0:
        if not math.isclose(
                summary.get("population_weighted_visible_fraction", math.nan),
                weighted_visible / row_total_population,
                rel_tol=0.0, abs_tol=2e-15):
            errors.append("summary.population_weighted_visible_fraction != "
                          "recomputed from ledger")
        if not math.isclose(
                summary.get("population_weighted_never_visible_fraction",
                            math.nan),
                weighted_never / row_total_population,
                rel_tol=0.0, abs_tol=2e-15):
            errors.append("summary.population_weighted_never_visible_fraction "
                          "!= recomputed from ledger")
    evaluation = report.get("evaluation")
    if not isinstance(evaluation, dict):
        errors.append("evaluation must be a mapping")
        evaluation = {}
    n_sat = evaluation.get("satellite_count")
    comparisons = evaluation.get("comparison_count")
    if isinstance(n_sat, int) and not isinstance(n_sat, bool) and n_sat > 0 \
            and isinstance(samples, int) and not isinstance(samples, bool) \
            and rows:
        expected = len(rows) * samples * n_sat
        if comparisons != expected:
            errors.append("evaluation.comparison_count != endpoints * "
                          "samples * satellites")
    for key in ("endpoint_chunk_size", "time_chunk_size"):
        v = evaluation.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            errors.append(f"evaluation.{key} must be a positive integer")
    projected = evaluation.get("projected_bytes")
    if isinstance(projected, bool) or not isinstance(projected, (int, float)) \
            or projected <= 0:
        errors.append("evaluation.projected_bytes must be positive")
    rss = evaluation.get("observed_peak_rss_mib")
    if isinstance(rss, bool) or not isinstance(rss, (int, float)) \
            or rss < 0:
        errors.append("evaluation.observed_peak_rss_mib must be >= 0")
    if not isinstance(evaluation.get("full_scan"), bool):
        errors.append("evaluation.full_scan must be bool")
    fallback = evaluation.get("scalar_fallback_count")
    if isinstance(fallback, bool) or not isinstance(fallback, int) \
            or fallback < 0:
        errors.append("evaluation.scalar_fallback_count must be >= 0")
    return errors


def stable_json(report: dict[str, Any]) -> str:
    """Serialize a report without nondeterministic key or whitespace changes."""
    return json.dumps(report, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n"


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="resolved simulator YAML config")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--trace", type=Path,
                       help="compiled trace.csv or trace directory "
                            "(v1 scalar audit, trace-active cells)")
    group.add_argument("--population", type=Path,
                       help="population raster GeoTIFF "
                            "(v2 vector audit, all positive-population "
                            "regions)")
    parser.add_argument("--horizon", type=float)
    parser.add_argument("--step", type=float, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--aggregation", type=float, default=1.0,
                        help="aggregation degrees for the population audit "
                             "(default 1.0)")
    parser.add_argument("--max-working-mib", type=float, default=256.0,
                        help="peak working-memory ceiling for the vector "
                             "audit (default 256 MiB)")
    parser.add_argument("--full-scan", action="store_true",
                        help="record this population audit as the full scan "
                             "rather than a bounded smoke")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from . import config, grid, model, population, trace
    resolved = config.load_config_file(str(args.config))
    cfg = resolved["config"]
    horizon = (cfg["scenario"]["duration_s"]
               if args.horizon is None else args.horizon)
    geometry = model.Constellation(
        cfg["scenario"]["num_satellites"], cfg["scenario"]["num_planes"],
        cfg["scenario"]["altitude_km"], cfg["scenario"]["inclination_deg"],
        cfg["scenario"]["min_elevation_deg"],
        max_isl_km=cfg["links"]["max_isl_km"],
        geometry_epoch_s=cfg["scenario"]["geometry_epoch_s"])
    if args.population is not None:
        table = population.load_population_regions(
            args.population, float(args.aggregation))
        endpoints = [
            {"name": r.grid_id, "lat": r.lat, "lon": r.lon,
             "population": r.population}
            for r in table.regions
        ]
        provenance = {
            "config_sha256": resolved["sha256"],
            "endpoint_source": "population_raster",
            "aggregation_deg": float(args.aggregation),
            "source_resolution_deg": list(table.source_resolution_deg),
        }
        report = scan_constellation_coverage(
            geometry, endpoints, horizon_s=horizon, step_s=args.step,
            visible_fraction_threshold=args.threshold,
            endpoint_source={
                "type": "population_raster",
                "source_sha256": table.source_sha256,
                "aggregation_deg": float(args.aggregation),
                "candidate_regions": len(table.regions),
                "total_population": table.total_population,
            },
            max_working_mib=args.max_working_mib,
            full_scan=args.full_scan,
            provenance=provenance)
    else:
        trace_path = args.trace / "trace.csv" \
            if args.trace.is_dir() else args.trace
        rows = trace.load_trace(str(trace_path), horizon_s=horizon,
                                max_packets=cfg["execution"]["max_packets"])
        cells = sorted({cell for row in rows for cell in
                        (row["src_grid_id"], row["dst_grid_id"])})
        endpoints = [{"name": cell, "lat": grid.grid_center(cell)[0],
                      "lon": grid.grid_center(cell)[1]} for cell in cells]
        report = scan_coverage(
            geometry, endpoints, horizon_s=horizon, step_s=args.step,
            visible_fraction_threshold=args.threshold,
            provenance={"config_sha256": resolved["sha256"],
                        "trace_path": str(trace_path),
                        "trace_sha256": hashlib.sha256(
                            trace_path.read_bytes()).hexdigest(),
                        "endpoint_source": "resolved_trace_cells"})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(stable_json(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    _cli()
