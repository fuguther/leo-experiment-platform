#!/usr/bin/env python3
"""Build deterministic three-phase Pigou traffic and native leo-sim configs.

The generated CSV is the coordinate-form input consumed by
``CODE.leo_sim.trace.compile_trace`` when ``demand.mode=csv``.  The packet
stream has two disjoint layers:

* a background layer emitted over ``[0.2, 9.5)`` whose packet IDs, routes,
  emission times, and deadlines do not depend on the burst multiplier; and
* an optional pulse layer emitted only over ``[2.0, 3.5)`` on declared hotspot
  corridors, using a separate deterministic random stream.

Packet counts are derived from the declared bit rate before generation.  The
resulting count is therefore an exact, predeclared compile bound, and the code
never trims rows after sampling.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from CODE.leo_sim import config as leo_config  # noqa: E402


TRACE_HEADER = (
    "packet_id",
    "emit_time_s",
    "src_lat",
    "src_lon",
    "dst_lat",
    "dst_lon",
    "bits",
    "deadline_at_s",
    "research_deadline_at_s",
)
TRACE_FILENAME = "three-phase-demand.csv"
PHASE_WINDOWS_S = {
    "steady_base": (0.2, 2.0),
    "declared_pulse_overlay": (2.0, 3.5),
    "recovery_base": (3.5, 9.5),
    "drain": (9.5, 11.5),
}
EMISSION_WINDOW_S = (0.2, 9.5)
PULSE_WINDOW_S = (2.0, 3.5)
DURATION_S = 11.5
TIME_STEP_S = 0.05
REAL_CONTROL_INTERVALS_S = (0.01, 0.05, 0.10)
DEFAULT_OFFERED_MBPS = {"A": 100.0, "B": 180.0}
MAX_PACKETS = 200_000
RETRY_POLICY = {
    "enabled": True,
    "max_retries": 2,
    "retry_fraction": 0.65,
    "backoff_s": 0.040,
    "jitter_s": 0.020,
    "attempt_timeout_s": 0.240,
    "stop_reinject_at_s": 9.3,
}


@dataclass(frozen=True)
class Site:
    name: str
    lat: float
    lon: float
    demand_weight: float = 1.0


@dataclass(frozen=True)
class SceneSpec:
    scene: str
    name: str
    num_satellites: int
    num_planes: int
    altitude_km: float
    inclination_deg: float
    isl_rate_mbps: float
    isl_queue_bits: int
    packet_bits: int
    hard_deadline_s: float
    minimum_site_distance_km: float
    sites: tuple[Site, ...]
    corridor_weights: tuple[tuple[str, str, float], ...]
    hotspot_weights: tuple[tuple[str, str, float], ...]


# Coordinates below are city-centre WGS84 reference points, written explicitly
# so endpoint identity never depends on a remote geocoder or an unpinned file.
SCENE_A_SITES = (
    Site("Beijing", 39.904200, 116.407400, 1.4),
    Site("Tokyo", 35.676200, 139.650300, 1.4),
    Site("Singapore", 1.352100, 103.819800, 1.2),
    Site("Sydney", -33.868800, 151.209300, 0.9),
    Site("Mumbai", 19.076000, 72.877700, 1.1),
    Site("Frankfurt", 50.110900, 8.682100, 1.2),
    Site("London", 51.507400, -0.127800, 1.3),
    Site("Cairo", 30.044400, 31.235700, 0.8),
    Site("NewYork", 40.712800, -74.006000, 1.3),
    Site("SanFrancisco", 37.774900, -122.419400, 1.1),
    Site("SaoPaulo", -23.550500, -46.633300, 1.0),
    Site("Johannesburg", -26.204100, 28.047300, 0.7),
)

SCENE_B_SITES = (
    Site("Beijing", 39.904200, 116.407400, 1.2),
    Site("Tokyo", 35.676200, 139.650300, 1.3),
    Site("Seoul", 37.566500, 126.978000, 1.0),
    Site("Singapore", 1.352100, 103.819800, 1.2),
    Site("Jakarta", -6.208800, 106.845600, 0.7),
    Site("Manila", 14.599500, 120.984200, 0.7),
    Site("Sydney", -33.868800, 151.209300, 0.9),
    Site("Mumbai", 19.076000, 72.877700, 1.0),
    Site("Dubai", 25.204800, 55.270800, 0.9),
    Site("Istanbul", 41.008200, 28.978400, 0.8),
    Site("Moscow", 55.755800, 37.617300, 0.9),
    Site("Frankfurt", 50.110900, 8.682100, 1.2),
    Site("London", 51.507400, -0.127800, 1.3),
    Site("Tunis", 36.806500, 10.181500, 0.7),
    Site("Lagos", 6.524400, 3.379200, 0.6),
    Site("Nairobi", -1.286389, 36.817223, 0.7),
    Site("NewYork", 40.712800, -74.006000, 1.2),
    Site("Toronto", 43.653200, -79.383200, 0.9),
    Site("Chicago", 41.878100, -87.629800, 0.9),
    Site("LosAngeles", 34.052200, -118.243700, 1.0),
    Site("PanamaCity", 8.982400, -79.519900, 0.8),
    Site("Bogota", 4.711000, -74.072100, 0.7),
    Site("SaoPaulo", -23.550500, -46.633300, 1.0),
    Site("BuenosAires", -34.603700, -58.381600, 0.8),
)

SCENE_A_CORRIDORS = (
    # Eurasia and adjacent intercontinental flows.
    ("Beijing", "Frankfurt", 1.4),
    ("Mumbai", "London", 1.2),
    ("Singapore", "Frankfurt", 1.1),
    ("Tokyo", "Frankfurt", 1.0),
    ("Sydney", "Singapore", 0.9),
    ("Cairo", "London", 0.6),
    # Transatlantic and South Atlantic flows.
    ("NewYork", "Frankfurt", 1.4),
    ("SaoPaulo", "Frankfurt", 1.0),
    ("SaoPaulo", "London", 0.9),
    ("Johannesburg", "London", 0.8),
    # Transpacific flows.
    ("SanFrancisco", "Tokyo", 1.4),
    ("SanFrancisco", "Singapore", 1.1),
    ("SanFrancisco", "Sydney", 1.0),
    ("Tokyo", "Sydney", 0.9),
)

# Directed nominal paths from these sources share the same reference trunk
# at the declared Walker epoch. Every city retains a background workload.
SCENE_A_HOTSPOTS = (
    ("Mumbai", "London", 1.2),
    ("Singapore", "Frankfurt", 1.1),
    ("Tokyo", "Frankfurt", 1.0),
    ("SaoPaulo", "Frankfurt", 1.0),
    ("SaoPaulo", "London", 0.9),
    ("Johannesburg", "London", 0.8),
)

SCENE_B_CORRIDORS = (
    ("Beijing", "Frankfurt", 0.9),
    ("Beijing", "Moscow", 0.8),
    ("Tokyo", "Frankfurt", 1.3),
    ("Tokyo", "LosAngeles", 1.2),
    ("Seoul", "LosAngeles", 0.9),
    ("Singapore", "London", 1.3),
    ("Singapore", "Mumbai", 0.8),
    ("Jakarta", "Singapore", 0.6),
    ("Manila", "Sydney", 0.7),
    ("Sydney", "London", 0.9),
    ("Dubai", "London", 0.8),
    ("Istanbul", "Frankfurt", 0.7),
    ("Tunis", "London", 0.7),
    ("Lagos", "London", 0.6),
    ("Nairobi", "Frankfurt", 0.7),
    ("NewYork", "SaoPaulo", 1.2),
    ("Toronto", "Frankfurt", 1.0),
    ("Chicago", "London", 0.8),
    ("PanamaCity", "LosAngeles", 0.7),
    ("Bogota", "NewYork", 0.7),
    ("BuenosAires", "SaoPaulo", 0.6),
)

SCENE_B_HOTSPOTS = (
    ("Tokyo", "Frankfurt", 1.0),
    ("Singapore", "London", 1.0),
    ("NewYork", "SaoPaulo", 1.0),
)


def _scene_spec(scene: str) -> SceneSpec:
    normalized = scene.upper()
    common = {
        "minimum_site_distance_km": 6_000.0,
    }
    if normalized == "A":
        return SceneSpec(
            scene="A",
            name="pigou-scene-a",
            num_satellites=96,
            num_planes=12,
            altitude_km=800.0,
            inclination_deg=53.0,
            isl_rate_mbps=70.0,
            isl_queue_bits=2_800_000,
            packet_bits=64_000,
            hard_deadline_s=0.240,
            sites=SCENE_A_SITES,
            corridor_weights=SCENE_A_CORRIDORS,
            hotspot_weights=SCENE_A_HOTSPOTS,
            **common,
        )
    if normalized == "B":
        return SceneSpec(
            scene="B",
            name="pigou-scene-b",
            num_satellites=66,
            num_planes=6,
            altitude_km=780.0,
            inclination_deg=86.4,
            isl_rate_mbps=60.0,
            isl_queue_bits=2_400_000,
            packet_bits=64_000,
            hard_deadline_s=0.280,
            sites=SCENE_B_SITES,
            corridor_weights=SCENE_B_CORRIDORS,
            hotspot_weights=SCENE_B_HOTSPOTS,
            **common,
        )
    raise ValueError(f"scene must be 'A' or 'B', got {scene!r}")


def _validate_inputs(
    *,
    scene: SceneSpec,
    seed: int,
    multiplier: float,
    interval: float,
    offered_mbps: float,
) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    if (isinstance(multiplier, bool)
            or not isinstance(multiplier, (int, float))
            or not math.isfinite(float(multiplier))
            or float(multiplier) < 1.0):
        raise ValueError("multiplier must be finite and >= 1.0")
    if (isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(float(interval))
            or not any(abs(float(interval) - allowed) <= 1e-12
                       for allowed in REAL_CONTROL_INTERVALS_S)):
        raise ValueError(
            "interval must be one of the real control emission intervals "
            f"{REAL_CONTROL_INTERVALS_S} seconds")
    if (isinstance(offered_mbps, bool)
            or not isinstance(offered_mbps, (int, float))
            or not math.isfinite(float(offered_mbps))
            or float(offered_mbps) <= 0):
        raise ValueError("offered_mbps must be finite and > 0")

    names = [site.name for site in scene.sites]
    if len(names) != len(set(names)):
        raise AssertionError(f"scene {scene.scene} has duplicate site names")
    site_by_name = {site.name: site for site in scene.sites}
    for name, site in site_by_name.items():
        if not (-90.0 <= site.lat <= 90.0 and -180.0 <= site.lon <= 180.0):
            raise AssertionError(f"invalid geocode for {name}")
    for source, destination, weight in (
            scene.corridor_weights + scene.hotspot_weights):
        if source not in site_by_name or destination not in site_by_name:
            raise AssertionError(
                f"route {source!r}->{destination!r} names an unknown site")
        if source == destination:
            raise AssertionError(f"zero-length route {source!r}->{destination!r}")
        if not math.isfinite(weight) or weight <= 0:
            raise AssertionError(
                f"route {source!r}->{destination!r} has invalid weight {weight!r}")


def _haversine_km(a: Site, b: Site) -> float:
    earth_radius_km = 6_371.0088
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    hav = (math.sin(dlat / 2.0) ** 2
           + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2)
    return 2.0 * earth_radius_km * math.asin(min(1.0, math.sqrt(hav)))


def _bidirectional_routes(
    routes: Iterable[tuple[str, str, float]],
) -> tuple[tuple[str, str, float], ...]:
    expanded: dict[tuple[str, str], float] = {}
    for source, destination, weight in routes:
        for src, dst in ((source, destination), (destination, source)):
            key = (src, dst)
            if key in expanded:
                raise AssertionError(f"duplicate directed route {key}")
            expanded[key] = float(weight)
    return tuple((src, dst, weight)
                 for (src, dst), weight in expanded.items())


def _expected_packet_count(
    offered_mbps: float,
    packet_bits: int,
    window_s: float,
) -> int:
    exact = offered_mbps * 1_000_000.0 * window_s / packet_bits
    return int(math.floor(exact + 0.5))


def _midpoint_times(count: int, start: float, end: float) -> list[float]:
    if count <= 0:
        return []
    width = (end - start) / count
    return [start + (index + 0.5) * width for index in range(count)]


def _stable_fraction(*parts: object) -> float:
    encoded = "|".join(str(part) for part in parts).encode("utf-8")
    integer = int.from_bytes(hashlib.sha256(encoded).digest()[:8], "big")
    return integer / float(1 << 64)


def _choose_route(
    routes: tuple[tuple[str, str, float], ...],
    *,
    seed: int,
    stream: str,
    index: int,
) -> tuple[str, str]:
    total = sum(weight for _, _, weight in routes)
    draw = _stable_fraction("pigou-three-phase/v1", seed, stream, index) * total
    cumulative = 0.0
    for source, destination, weight in routes:
        cumulative += weight
        if draw < cumulative:
            return source, destination
    return routes[-1][0], routes[-1][1]


def _format_seconds(value: float) -> str:
    text = f"{value:.9f}".rstrip("0").rstrip(".")
    return text or "0"


def _serialize_row(row: Mapping[str, object]) -> str:
    return ",".join((
        str(row["packet_id"]),
        _format_seconds(float(row["emit_time_s"])),
        f"{float(row['src_lat']):.6f}",
        f"{float(row['src_lon']):.6f}",
        f"{float(row['dst_lat']):.6f}",
        f"{float(row['dst_lon']):.6f}",
        str(row["bits"]),
        _format_seconds(float(row["deadline_at_s"])),
        _format_seconds(float(row["research_deadline_at_s"])),
    ))


def _packet_row(
    *,
    packet_id: int,
    emit_time_s: float,
    source: Site,
    destination: Site,
    packet_bits: int,
    hard_deadline_s: float,
    layer: str,
) -> dict[str, object]:
    deadline = emit_time_s + hard_deadline_s
    return {
        "packet_id": packet_id,
        "emit_time_s": emit_time_s,
        "src_lat": source.lat,
        "src_lon": source.lon,
        "dst_lat": destination.lat,
        "dst_lon": destination.lon,
        "bits": packet_bits,
        "deadline_at_s": deadline,
        "research_deadline_at_s": deadline,
        "layer": layer,
    }


def _transaction_deadline_s(spec: SceneSpec) -> float:
    retries = int(RETRY_POLICY["max_retries"])
    attempts = retries + 1
    return (
        attempts * float(RETRY_POLICY["attempt_timeout_s"])
        + retries * (float(RETRY_POLICY["backoff_s"])
                     + float(RETRY_POLICY["jitter_s"])
                     + TIME_STEP_S)
    )


def _make_config(
    *,
    spec: SceneSpec,
    seed: int,
    interval: float,
    offered_mbps: float,
    trace_path: Path,
    max_packets: int,
) -> dict[str, object]:
    return {
        "scenario": {
            "name": spec.name,
            "duration_s": DURATION_S,
            "time_step_s": TIME_STEP_S,
            "num_satellites": spec.num_satellites,
            "num_planes": spec.num_planes,
            "altitude_km": spec.altitude_km,
            "inclination_deg": spec.inclination_deg,
            "min_elevation_deg": 10.0,
            "seed": seed,
            "geometry_epoch_s": 0.0 if spec.scene == "A" else 1080.0,
        },
        "endpoints": {
            "grid_deg": 0.25,
            "aggregation_deg": 1.0,
            "sites": [
                {
                    "name": site.name,
                    "lat": site.lat,
                    "lon": site.lon,
                    "demand_weight": site.demand_weight,
                }
                for site in spec.sites
            ],
            "mlab_auto": False,
        },
        "demand": {
            "mode": "csv",
            "offered_mbps": offered_mbps,
            "emission_start_s": EMISSION_WINDOW_S[0],
            "emission_end_s": EMISSION_WINDOW_S[1],
            "packet_bits": spec.packet_bits,
            "deadline_s": spec.hard_deadline_s,
            "research_deadline_s": spec.hard_deadline_s,
            "csv_path": str(trace_path),
            "population_path": None,
            "burst_start_s": None,
            "burst_duration_s": None,
            "temporal_model": "constant",
            "retry": dict(RETRY_POLICY),
        },
        "access": {
            "unavailable_policy": "queue",
            "slots_per_satellite": 8,
            "uplink_rate_mbps": 10_000.0,
            "downlink_rate_mbps": 10_000.0,
            "uplink_queue_bits": 1_000_000_000,
            "downlink_queue_bits": 1_000_000_000,
            "holding_queue_bits": 1_000_000_000,
            "association": "bbm",
            "hysteresis_deg": 0.0,
            "min_dwell_s": 0.0,
            "acquisition_delay_s": 0.1,
        },
        "links": {
            "rate_model": "constant",
            "isl_rate_mbps": spec.isl_rate_mbps,
            "isl_queue_bits": spec.isl_queue_bits,
            "max_isl_km": 6_000.0,
            "geometry_loss": True,
            "ge_enabled": False,
            "isl_dirs": ["N", "S", "E", "W"],
        },
        "topology": {
            "recompute_interval_s": 1.0,
            "matching": "markovian",
        },
        "control_plane": {
            "enabled": True,
            "advertisement_protocol_version": 2,
            "vis_k": 1,
            "ttl_s": 2.0,
            "advertise_interval_s": interval,
            "packet_bits": 8_000,
            "priority": "nonpreemptive_priority",
        },
        "routing": {
            "policy": "hop",
            "ephemeris_egress_fallback": True,
            "max_hops": 64,
            "learning_enabled": False,
        },
        "learning": {"algorithm": "none"},
        "execution": {
            "max_events": 50_000_000,
            "max_entities": 20_000,
            "max_packets": max_packets,
            "available_capacity_interval_s": 1.0,
            "compute_delay_s": 0.0,
            "decision_observation_mode": "refresh",
            "node_process_delay_s": 0.0,
            "compute_servers_per_satellite": 0,
        },
        "time_alignment": {"enabled": False},
        "outputs": {
            "out_dir": f"leo_sim_out/{spec.name}",
            "plotting": False,
        },
    }


def _write_trace(path: Path, rows: list[dict[str, object]]) -> None:
    lines = [",".join(TRACE_HEADER)]
    lines.extend(_serialize_row(row) for row in rows)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write("\n".join(lines) + "\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _layer_sha256(rows: Iterable[Mapping[str, object]], layer: str) -> str:
    digest = hashlib.sha256()
    for row in rows:
        if row["layer"] == layer:
            digest.update(_serialize_row(row).encode("utf-8"))
            digest.update(b"\n")
    return digest.hexdigest()


def build(
    out_dir: Path,
    *,
    scene: str,
    seed: int,
    multiplier: float = 2.5,
    interval: float = 0.05,
    offered_mbps: float | None = None,
) -> dict[str, object]:
    """Write one deterministic trace and return its native-config interface.

    ``config`` is a raw mapping suitable for
    ``CODE.leo_sim.config.resolve_config``.  ``trace_path`` is the coordinate
    CSV referenced by ``config['demand']['csv_path']``.  Background packet IDs
    and the SHA-256 of their exact serialized CSV rows are stable for a fixed
    scene and seed at every multiplier.  A multiplier of 1.0 adds no pulse and
    provides the reusable no-burst input for a separate outage experiment.
    """
    spec = _scene_spec(scene)
    resolved_offered = (
        DEFAULT_OFFERED_MBPS[spec.scene]
        if offered_mbps is None else float(offered_mbps)
    )
    resolved_multiplier = float(multiplier)
    resolved_interval = float(interval)
    _validate_inputs(
        scene=spec,
        seed=seed,
        multiplier=resolved_multiplier,
        interval=resolved_interval,
        offered_mbps=resolved_offered,
    )

    out = Path(out_dir).expanduser()
    if out.is_symlink():
        raise ValueError(f"out_dir may not be a symlink: {out}")
    out.mkdir(parents=True, exist_ok=True)
    if not out.is_dir():
        raise ValueError(f"out_dir is not a directory: {out}")
    out = out.resolve()
    trace_path = out / TRACE_FILENAME
    if trace_path.is_symlink():
        raise ValueError(f"trace output may not be a symlink: {trace_path}")

    sites = {site.name: site for site in spec.sites}
    background_routes = _bidirectional_routes(spec.corridor_weights)
    if spec.scene == "A":
        hotspot_pairs = {(src, dst) for src, dst, _ in spec.hotspot_weights}
        background_routes = tuple((src, dst, weight * 8.0 if (src, dst) in hotspot_pairs else weight)
                                  for src, dst, weight in background_routes)
        pulse_routes = spec.hotspot_weights
    else:
        pulse_routes = _bidirectional_routes(spec.hotspot_weights)

    emission_duration = EMISSION_WINDOW_S[1] - EMISSION_WINDOW_S[0]
    pulse_duration = PULSE_WINDOW_S[1] - PULSE_WINDOW_S[0]
    background_count = _expected_packet_count(
        resolved_offered, spec.packet_bits, emission_duration)
    pulse_count = _expected_packet_count(
        resolved_offered * (resolved_multiplier - 1.0),
        spec.packet_bits,
        pulse_duration,
    )
    packet_budget = background_count + pulse_count
    if background_count < 1:
        raise ValueError("offered_mbps produces no background packets")
    if packet_budget > MAX_PACKETS:
        raise ValueError(
            f"predeclared packet budget {packet_budget} exceeds safe bound "
            f"{MAX_PACKETS}; reduce offered_mbps or multiplier")

    rows: list[dict[str, object]] = []
    for index, emit_time_s in enumerate(_midpoint_times(
            background_count, *EMISSION_WINDOW_S)):
        source_name, destination_name = _choose_route(
            background_routes, seed=seed, stream="background", index=index)
        rows.append(_packet_row(
            packet_id=index + 1,
            emit_time_s=emit_time_s,
            source=sites[source_name],
            destination=sites[destination_name],
            packet_bits=spec.packet_bits,
            hard_deadline_s=_transaction_deadline_s(spec),
            layer="background",
        ))

    for index, emit_time_s in enumerate(_midpoint_times(
            pulse_count, *PULSE_WINDOW_S)):
        source_name, destination_name = _choose_route(
            pulse_routes, seed=seed, stream="pulse", index=index)
        rows.append(_packet_row(
            packet_id=background_count + index + 1,
            emit_time_s=emit_time_s,
            source=sites[source_name],
            destination=sites[destination_name],
            packet_bits=spec.packet_bits,
            hard_deadline_s=_transaction_deadline_s(spec),
            layer="pulse_overlay",
        ))

    rows.sort(key=lambda row: (float(row["emit_time_s"]), int(row["packet_id"])))
    if len(rows) != packet_budget:
        raise AssertionError(
            f"predeclared budget {packet_budget} != emitted rows {len(rows)}")

    _write_trace(trace_path, rows)
    raw_config = _make_config(
        spec=spec,
        seed=seed,
        interval=resolved_interval,
        offered_mbps=resolved_offered,
        trace_path=trace_path,
        max_packets=packet_budget,
    )
    # Fail in the builder if the raw mapping is not accepted by the platform.
    # The caller receives the raw mapping, not this resolved envelope, so a
    # matrix/run compiler can still merge its own mechanism overrides.
    leo_config.resolve_config(raw_config)

    background_ids = tuple(range(1, background_count + 1))
    pulse_ids = tuple(
        range(background_count + 1, background_count + pulse_count + 1))
    full_trace_sha256 = hashlib.sha256(trace_path.read_bytes()).hexdigest()

    return {
        "config": raw_config,
        "trace_path": trace_path,
        "scene": spec.scene,
        "seed": seed,
        "multiplier": resolved_multiplier,
        "interval": resolved_interval,
        "offered_mbps": resolved_offered,
        "packet_budget": packet_budget,
        "packet_count": len(rows),
        "background_packet_count": background_count,
        "pulse_packet_count": pulse_count,
        "background_packet_ids": background_ids,
        "background_packet_sha256": _layer_sha256(rows, "background"),
        "pulse_packet_ids": pulse_ids,
        "pulse_packet_sha256": _layer_sha256(rows, "pulse_overlay"),
        "trace_sha256": full_trace_sha256,
        "phase_windows_s": PHASE_WINDOWS_S,
        "hotspot_routes": tuple(
            (source, destination)
            for source, destination, _ in pulse_routes
        ),
    }


def _json_ready(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def _cli_summary(result: Mapping[str, object]) -> dict[str, object]:
    """Keep the CLI receipt readable while the callable returns exact ID sets."""
    background_ids = result["background_packet_ids"]
    pulse_ids = result["pulse_packet_ids"]
    return {
        "config": result["config"],
        "trace_path": result["trace_path"],
        "scene": result["scene"],
        "seed": result["seed"],
        "multiplier": result["multiplier"],
        "interval": result["interval"],
        "offered_mbps": result["offered_mbps"],
        "packet_budget": result["packet_budget"],
        "packet_count": result["packet_count"],
        "background_packet_count": result["background_packet_count"],
        "background_packet_id_range": [
            background_ids[0], background_ids[-1]
        ] if background_ids else [],
        "background_packet_sha256": result["background_packet_sha256"],
        "pulse_packet_count": result["pulse_packet_count"],
        "pulse_packet_id_range": [
            pulse_ids[0], pulse_ids[-1]
        ] if pulse_ids else [],
        "pulse_packet_sha256": result["pulse_packet_sha256"],
        "trace_sha256": result["trace_sha256"],
        "phase_windows_s": result["phase_windows_s"],
        "hotspot_routes": result["hotspot_routes"],
    }


def build_staircase(out_dir, *, seed, interval, offered_mbps):
    """Matched ascending and descending load plateaus for the hysteresis figure."""
    spec = _scene_spec("A")
    _validate_inputs(scene=spec, seed=seed, multiplier=1.0,
                     interval=interval, offered_mbps=offered_mbps)
    out = Path(out_dir).resolve()
    out.mkdir(parents=True)
    path = out / TRACE_FILENAME
    sites = {site.name: site for site in spec.sites}
    hotspots = {(a, b) for a, b, _ in spec.hotspot_weights}
    routes = tuple((a, b, w * 8.0 if (a, b) in hotspots else w)
                   for a, b, w in _bidirectional_routes(spec.corridor_weights))
    levels = (0.65, 0.80, 0.95, 1.10, 1.25, 1.10, 0.95, 0.80, 0.65)
    rows = []
    plateaus = []
    for step, level in enumerate(levels):
        start = round(0.2 + step, 9)
        end = round(start + 1.0, 9)
        rate = offered_mbps * level
        n = _expected_packet_count(rate, spec.packet_bits, 1.0)
        plateaus.append({"index": step, "start_s": start, "end_s": end,
                         "offered_mbps": rate,
                         "direction": "up" if step <= len(levels) // 2 else "down"})
        for at in _midpoint_times(n, start, end):
            pid = len(rows) + 1
            src, dst = _choose_route(routes, seed=seed, stream="staircase", index=pid)
            rows.append(_packet_row(packet_id=pid, emit_time_s=at, source=sites[src],
                                    destination=sites[dst], packet_bits=spec.packet_bits,
                                    hard_deadline_s=_transaction_deadline_s(spec),
                                    layer="background"))
    _write_trace(path, rows)
    cfg = _make_config(spec=spec, seed=seed, interval=interval, offered_mbps=offered_mbps,
                       trace_path=path, max_packets=len(rows))
    cfg["demand"]["emission_end_s"] = plateaus[-1]["end_s"]
    cfg["scenario"]["duration_s"] = plateaus[-1]["end_s"] + 2.0
    return {"config": cfg, "trace_path": path,
            "background_packet_sha256": _layer_sha256(rows, "background"),
            "plateaus": plateaus}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build deterministic three-phase Pigou Scene A/B traffic")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scene", choices=("A", "B"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--multiplier", type=float, default=2.5)
    parser.add_argument(
        "--interval", type=float, choices=REAL_CONTROL_INTERVALS_S,
        default=0.05, help="real ControlPacket emission interval in seconds")
    parser.add_argument("--offered-mbps", type=float, default=None)
    args = parser.parse_args(argv)
    result = build(
        args.out,
        scene=args.scene,
        seed=args.seed,
        multiplier=args.multiplier,
        interval=args.interval,
        offered_mbps=args.offered_mbps,
    )
    print(json.dumps(_json_ready(_cli_summary(result)), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
