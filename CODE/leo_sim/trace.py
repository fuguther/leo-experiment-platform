"""Deterministic immutable demand trace compiler.

Produces trace.csv (packet_id, emit_time_s, src_grid_id, dst_grid_id, bits,
deadline_at_s) plus a manifest with schema version, config/input hashes, RNG
stream mapping, offered packet/bit ledger, active endpoint count and time
range. Identical config+input+seed is byte reproducible.

Supported modes: uniform, gravity, hotspot, burst, diurnal, csv, mlab.
The mlab mode reuses repository M-Lab data as OD weights only; provenance is
always labelled measurement_proxy and never calibrated user demand.  When
burst_start_s/burst_duration_s are supplied with mlab, the same measured OD
weights receive the explicit burst transform.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from collections import deque
from pathlib import Path

from . import config, grid, population, rng

TRACE_SCHEMA = "leo-sim-trace/v1"
TRACE_MANIFEST_SCHEMA_V1 = "leo-sim-trace-manifest/v1"
TRACE_MANIFEST_SCHEMA = "leo-sim-trace-manifest/v2"
TRACE_PROVENANCE_SCHEMA_V1 = "leo-sim-trace-provenance/v1"
TRACE_PROVENANCE_SCHEMA = "leo-sim-trace-provenance/v2"
PACKET_ID_CONTRACT = (
    "synthetic: sequential 1..N in emission order; "
    "csv: source packet_id preserved verbatim")
REPO_MLAB_CSV = Path(__file__).resolve().parent.parent / "data" / "traffic" / "mlab_2026-05-27.csv"
TIME_DECIMALS = 6


class TraceError(ValueError):
    pass


def _format_time(value: float) -> str:
    """Canonical, byte-reproducible trace time representation."""
    text = f"{float(value):.{TIME_DECIMALS}f}".rstrip("0").rstrip(".")
    return text or "0"


def _serialized_time(value: float) -> float:
    return float(_format_time(value))


def validate_packet_rows(rows: list[dict], horizon_s: float,
                         max_packets: int) -> None:
    """The single packet-row contract, enforced identically at trace compile
    time, at precompiled-trace load time, and at kernel entry.

    Per row: packet_id unique positive int; emit_time_s finite, >= 0 and
    within the run horizon; bits positive int; src/dst structurally valid
    grid ids and different cells; deadline empty or finite and not earlier
    than emit_time. Rows must be stably sorted by (emit_time_s, packet_id).
    Anything else is a TraceError (fail closed).
    """
    if len(rows) > max_packets:
        raise TraceError(
            f"trace contains {len(rows)} packets > execution.max_packets "
            f"({max_packets})")
    seen: set[int] = set()
    prev_key: tuple[float, int] | None = None
    for i, r in enumerate(rows):
        try:
            pid, t = r["packet_id"], r["emit_time_s"]
            s, d = r["src_grid_id"], r["dst_grid_id"]
            bits, dl = r["bits"], r["deadline_at_s"]
        except (KeyError, TypeError) as exc:
            raise TraceError(f"trace row {i}: missing field {exc}")
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise TraceError(f"trace row {i}: packet_id must be a positive int: {pid!r}")
        if pid in seen:
            raise TraceError(f"trace row {i}: duplicate packet_id {pid}")
        seen.add(pid)
        if not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(t):
            raise TraceError(f"trace row {i}: emit_time_s not finite: {t!r}")
        if t < 0:
            raise TraceError(f"trace row {i}: negative emit_time_s {t}")
        if t > horizon_s:
            raise TraceError(
                f"trace row {i}: emit_time_s {t} beyond run horizon {horizon_s}")
        if isinstance(bits, bool) or not isinstance(bits, int) or bits <= 0:
            raise TraceError(f"trace row {i}: bits must be a positive int: {bits!r}")
        if not grid.is_valid_grid_id(s) or not grid.is_valid_grid_id(d):
            raise TraceError(f"trace row {i}: invalid grid id {s!r}/{d!r}")
        if s == d:
            raise TraceError(f"trace row {i}: src == dst cell {s!r}")
        if dl is not None:
            if not isinstance(dl, (int, float)) or isinstance(dl, bool) or not math.isfinite(dl):
                raise TraceError(f"trace row {i}: deadline not finite: {dl!r}")
            if dl < t:
                raise TraceError(
                    f"trace row {i}: deadline {dl} earlier than emit_time {t}")
        key = (float(t), pid)
        if prev_key is not None and key < prev_key:
            raise TraceError(
                f"trace row {i}: rows must be sorted by (emit_time_s, packet_id)")
        prev_key = key


def _haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class VoseAlias:
    """One Vose alias table over explicit positive proposal weights.

    Built ONCE for the whole population table (a single O(N) table, never
    one O(N) table per source).  draw(gen) consumes exactly two RNG values
    from the caller's generator: a bucket index and the alias coin.  The
    table is a pure function of the weights, so compilation remains
    byte-deterministic for identical seeds.
    """

    def __init__(self, weights):
        n = len(weights)
        if n == 0:
            raise TraceError("alias table requires at least one candidate")
        total = sum(weights)
        if not math.isfinite(total) or total <= 0:
            raise TraceError("alias proposal weights must be positive-finite "
                             "and sum to a positive total")
        scaled = [n * w / total for w in weights]
        small = [i for i, p in enumerate(scaled) if p < 1.0]
        large = [i for i, p in enumerate(scaled) if p >= 1.0]
        self.prob = [0.0] * n
        self.alias = [0] * n
        while small and large:
            s = small.pop()
            l = large.pop()
            self.prob[s] = scaled[s]
            self.alias[s] = l
            scaled[l] = (scaled[l] + scaled[s]) - 1.0
            if scaled[l] < 1.0:
                small.append(l)
            else:
                large.append(l)
        while large:
            self.prob[large.pop()] = 1.0
        while small:
            self.prob[small.pop()] = 1.0

    def draw(self, gen):
        bucket = int(gen.random() * len(self.prob))
        if bucket >= len(self.prob):  # guard against 1.0 rounding
            bucket = len(self.prob) - 1
        if gen.random() < self.prob[bucket]:
            return bucket
        return self.alias[bucket]


def sample_population_destination(gen, alias, endpoints, src_index, dm):
    """Exact population gravity destination by rejection over ONE alias
    proposal table.  No fallback to scan/uniform/nearest/last exists: the
    rejection cap is part of trace identity and fails loudly."""
    floor = float(dm["gravity_d_floor_km"])
    alpha = float(dm["gravity_alpha"])
    max_draws = int(dm["destination_rejection_max_draws"])
    src = endpoints[src_index]
    for _ in range(max_draws):
        candidate_index = alias.draw(gen)
        if candidate_index == src_index:
            continue
        dst = endpoints[candidate_index]
        distance = max(
            _haversine_km(src["lat"], src["lon"], dst["lat"], dst["lon"]),
            floor,
        )
        acceptance = (floor / distance) ** alpha
        if gen.random() < acceptance:
            return dst
    raise TraceError(
        "population alias_rejection exhausted "
        f"destination_rejection_max_draws={max_draws}")


def alias_rejection_stats(endpoints, dm, source_indices=None) -> dict:
    """Exact per-source rejection-sampler statistics (no drawing needed):

    per_draw_success_probability: p_i = sum_{j != i} W_j/W * (floor/d)^alpha
    expected_draws: 1/p_i (geometric)
    exhaustion_probability: (1 - p_i) ** destination_rejection_max_draws

    Only the listed sources are computed so a 201-source audit on the
    16,988-region table stays analytic instead of quadratic.
    """
    floor = float(dm["gravity_d_floor_km"])
    alpha = float(dm["gravity_alpha"])
    max_draws = int(dm["destination_rejection_max_draws"])
    gamma = float(dm["destination_population_exponent"])
    weights = [e["weight"] ** gamma for e in endpoints]
    total = sum(weights)
    indices = range(len(endpoints)) if source_indices is None \
        else sorted(source_indices)
    out = {}
    for i in indices:
        p_i = 0.0
        for j in range(len(endpoints)):
            if j == i:
                continue
            distance = max(_haversine_km(
                endpoints[i]["lat"], endpoints[i]["lon"],
                endpoints[j]["lat"], endpoints[j]["lon"]), floor)
            p_i += (weights[j] / total) * (floor / distance) ** alpha
        out[i] = {
            "per_draw_success_probability": p_i,
            "expected_draws": (1.0 / p_i if p_i > 0 else float("inf")),
            "exhaustion_probability": (
                (1.0 - p_i) ** max_draws if 0 < p_i <= 1.0 else 1.0),
        }
    return out


def _endpoints(cfg: dict) -> list[dict]:
    ep = cfg["endpoints"]
    sites = ep["sites"]
    if not sites:
        raise TraceError("endpoints.sites must be non-empty for trace compilation")
    out = []
    for s in sites:
        fine = grid.grid_id(s["lat"], s["lon"], deg=ep["grid_deg"])
        agg = grid.aggregate_id(fine, agg_deg=ep["aggregation_deg"])
        out.append({
            "name": s["name"],
            "lat": float(s["lat"]),
            "lon": float(s["lon"]),
            "weight": float(s.get("demand_weight", 1.0)),
            "agg_grid_id": agg,
        })
    # sparse activation: one endpoint per active aggregate cell keeps the first
    seen = {}
    for e in out:
        seen.setdefault(e["agg_grid_id"], e)
    return list(seen.values())


def _dst_choices(gen, mode, endpoints, i, t, dm, mlab_weights=None):
    src = endpoints[i]
    others = [e for e in endpoints if e["agg_grid_id"] != src["agg_grid_id"]]
    if not others:
        raise TraceError("need endpoints in at least two aggregate cells")
    if mode in ("uniform", "burst", "diurnal"):
        return others[gen.integers(len(others))]
    if mode in ("gravity", "population_gravity"):
        alpha = dm["gravity_alpha"]
        floor = dm["gravity_d_floor_km"]
        destination_exponent = (
            dm["destination_population_exponent"]
            if mode == "population_gravity" else 1.0)
        w = []
        for e in others:
            d = max(_haversine_km(src["lat"], src["lon"], e["lat"], e["lon"]), floor)
            w.append(e["weight"] ** destination_exponent / d ** alpha)
        total = sum(w)
        r = gen.random() * total
        acc = 0.0
        for e, wi in zip(others, w):
            acc += wi
            if r <= acc:
                return e
        return others[-1]
    if mode == "hotspot":
        # a fraction of endpoints attracts `concentration` of the traffic
        n_hot = max(1, round(len(others) * dm["hotspot_fraction"]))
        hot = others[:n_hot]  # deterministic ordering; selection below is random
        cold = others[n_hot:]
        conc = dm["hotspot_concentration"]
        pool, share = (hot, conc) if hot else (others, 1.0)
        if gen.random() < share or not cold:
            return pool[gen.integers(len(pool))]
        return cold[gen.integers(len(cold))]
    if mode == "mlab":
        weights = [mlab_weights.get((src["agg_grid_id"], e["agg_grid_id"]), 0.0) for e in others]
        total = sum(weights)
        if total <= 0.0:
            # no smoothing fallback exists: compile-time coverage checks make
            # this unreachable; if it ever fires it is a defect, fail closed
            raise TraceError(
                f"mlab: no measurement coverage from {src['agg_grid_id']} to "
                "any other active cell")
        r = gen.random() * total
        acc = 0.0
        for e, wi in zip(others, weights):
            acc += wi
            if r <= acc:
                return e
        return others[-1]
    raise TraceError(f"unsupported mode {mode}")


def _rate_multiplier(mode, t, src_lon, dm):
    if mode in ("burst", "mlab") and dm["burst_start_s"] is not None:
        start, dur = dm["burst_start_s"], dm["burst_duration_s"]
        if start <= t < start + dur:
            return dm["burst_multiplier"]
        return 1.0
    if mode == "diurnal":
        # Preserve the historical trace contract exactly.
        amp = dm["diurnal_amplitude"]
        local_h = (t / 3600.0 + src_lon / 15.0) % 24.0
        return max(0.0, 1.0 + amp * math.cos(2 * math.pi * (local_h - dm["diurnal_phase_h"]) / 24.0))
    if mode == "population_gravity" \
            and dm["temporal_model"] == "local_diurnal_cosine":
        # Opt-in local-solar-time population proxy: the UTC start hour shifts
        # the local-time clock; longitude maps the local hour.  This is a
        # population proxy, never calibrated subscriber traffic.
        local_h = (dm["utc_start_hour"] + t / 3600.0 + src_lon / 15.0) % 24.0
        amp = dm["diurnal_amplitude"]
        return max(0.0, 1.0 + amp * math.cos(2 * math.pi * (local_h - dm["diurnal_phase_h"]) / 24.0))
    return 1.0


def _read_mlab_measurements(grid_deg: float, agg_deg: float):
    """Read the immutable M-Lab snapshot and aggregate it onto V2 cells.

    The mapping MUST use the resolved config's grid degrees — keying weights
    on any other grid silently disconnects them from the endpoints (the old
    fixed-default grid did exactly that and hid behind 1e-9 smoothing).  This
    helper intentionally returns every measured OD pair; endpoint selection is
    a separate, explicit step for the opt-in multi-OD mode.
    """
    if not REPO_MLAB_CSV.exists():
        raise TraceError(f"m-lab source not found: {REPO_MLAB_CSV}")
    weights: dict[tuple[str, str], float] = {}
    hours: set[int] = set()
    row_count = 0
    required = {
        "client_lat", "client_lon", "server_lat", "server_lon",
        "hour_utc", "sample_count", "mean_throughput_mbps",
    }
    with open(REPO_MLAB_CSV, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise TraceError(f"m-lab source missing columns {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            try:
                lat_s, lon_s = float(row["client_lat"]), float(row["client_lon"])
                lat_d, lon_d = float(row["server_lat"]), float(row["server_lon"])
                hour = int(row["hour_utc"])
                count = int(row["sample_count"])
                throughput = float(row["mean_throughput_mbps"])
            except (TypeError, ValueError, KeyError) as exc:
                raise TraceError(
                    f"m-lab row {row_number}: invalid measurement field: {exc}") from exc
            if (not all(math.isfinite(v) for v in
                        (lat_s, lon_s, lat_d, lon_d, throughput))
                    or not 0 <= hour <= 23
                    or count <= 0
                    or throughput <= 0.0):
                raise TraceError(
                    f"m-lab row {row_number}: hour_utc, sample_count and "
                    "mean_throughput_mbps must be valid positive measurements")
            try:
                s = grid.aggregate_id(grid.grid_id(lat_s, lon_s, grid_deg), agg_deg)
                d = grid.aggregate_id(grid.grid_id(lat_d, lon_d, grid_deg), agg_deg)
            except (ValueError, TypeError) as exc:
                raise TraceError(
                    f"m-lab row {row_number}: endpoint coordinates invalid") from exc
            hours.add(hour)
            weights[(s, d)] = weights.get((s, d), 0.0) + throughput * count
    if row_count == 0 or not weights:
        raise TraceError("m-lab source contains no usable measurement rows")
    return weights, {
        "row_count": row_count,
        "od_pair_count": len(weights),
        "hour_utc_values": sorted(hours),
    }


def _load_mlab_weights(endpoints, grid_deg: float, agg_deg: float):
    """Aggregate M-Lab weights and keep the historic explicit-site API."""
    weights, summary = _read_mlab_measurements(grid_deg, agg_deg)
    # Keep the coverage check in compile_trace as the single fail-closed gate;
    # this function deliberately does not smooth or delete any source pairs.
    return weights, summary


def _strongly_connected_components(adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Return deterministic SCCs without recursion depth hazards."""
    nodes = sorted(set(adjacency) | {
        dst for values in adjacency.values() for dst in values
    })
    visited: set[str] = set()
    order: list[str] = []
    for start in nodes:
        if start in visited:
            continue
        visited.add(start)
        stack: list[tuple[str, int]] = [(start, 0)]
        while stack:
            node, index = stack[-1]
            children = adjacency.get(node, [])
            if index < len(children):
                child = children[index]
                stack[-1] = (node, index + 1)
                if child not in visited:
                    visited.add(child)
                    stack.append((child, 0))
            else:
                order.append(node)
                stack.pop()

    reverse: dict[str, list[str]] = {node: [] for node in nodes}
    for src, destinations in adjacency.items():
        for dst in destinations:
            reverse.setdefault(dst, []).append(src)
    for values in reverse.values():
        values.sort()

    components: list[list[str]] = []
    visited.clear()
    for start in reversed(order):
        if start in visited:
            continue
        component: list[str] = []
        stack = [start]
        visited.add(start)
        while stack:
            node = stack.pop()
            component.append(node)
            for child in reversed(reverse.get(node, [])):
                if child not in visited:
                    visited.add(child)
                    stack.append(child)
        components.append(sorted(component))
    return components


def _find_bounded_cycle(component: set[str], adjacency: dict[str, list[str]],
                        max_sites: int) -> list[str] | None:
    """Find a deterministic simple directed cycle no longer than max_sites."""
    for start in sorted(component):
        queue: deque[tuple[str, list[str]]] = deque([(start, [start])])
        while queue:
            node, path = queue.popleft()
            for child in adjacency.get(node, []):
                if child == start and len(path) >= 2:
                    return path
                if len(path) >= max_sites:
                    continue
                if child in component and child not in path:
                    queue.append((child, path + [child]))
    return None


def _select_mlab_endpoints(grid_deg: float, agg_deg: float,
                           max_sites: int) -> tuple[list[dict], dict, dict]:
    """Select a bounded, closed, measurement-supported endpoint set.

    M-Lab contains many client/server cells that are only one-way (a client
    cell may never appear as a server).  Since V2 makes every active endpoint
    an emitting traffic source, selecting those rows blindly would either fail
    at compile time or require an unjustified fallback.  We therefore choose
    the largest strongly connected measured subgraph, then apply a
    deterministic cap.  The selected cell IDs and rule are recorded in the
    trace manifest.
    """
    weights, summary = _read_mlab_measurements(grid_deg, agg_deg)
    adjacency: dict[str, list[str]] = {}
    for (src, dst), value in weights.items():
        if src == dst or value <= 0.0:
            continue
        adjacency.setdefault(src, []).append(dst)
    for src in adjacency:
        adjacency[src] = sorted(set(adjacency[src]))
    components = [set(c) for c in _strongly_connected_components(adjacency)
                  if len(c) >= 2]
    if not components:
        raise TraceError(
            "mlab_auto requires at least one measured strongly connected "
            "component with two aggregate cells")

    def rank(component: set[str]):
        internal = sum(weights.get((src, dst), 0.0)
                       for src in component for dst in adjacency.get(src, [])
                       if dst in component)
        return (-len(component), -internal, tuple(sorted(component)))

    component = min(components, key=rank)
    if len(component) <= max_sites:
        selected = sorted(component)
    else:
        cycle = _find_bounded_cycle(component, adjacency, max_sites)
        if cycle is None:
            raise TraceError(
                "mlab_auto endpoint cap cannot preserve a measured directed "
                f"cycle of length <= {max_sites}; increase mlab_max_sites")
        selected_set = set(cycle)
        candidates = sorted(
            (node for node in component if node not in selected_set),
            key=lambda node: (
                -sum(weights.get((node, dst), 0.0)
                    for dst in adjacency.get(node, []) if dst in component),
                node,
            ),
        )
        # Adding a node only when it has a measured edge into the existing set
        # preserves the invariant that every selected source has a measured
        # destination within the active endpoint set.
        for node in candidates:
            if len(selected_set) >= max_sites:
                break
            if any(dst in selected_set for dst in adjacency.get(node, [])):
                selected_set.add(node)
        selected = sorted(selected_set)

    selected_set = set(selected)
    if any(not any(dst in selected_set for dst in adjacency.get(src, []))
           for src in selected):
        raise TraceError(
            "mlab_auto selection produced an endpoint without a measured "
            "outgoing OD; refusing silent demand fallback")

    endpoints = []
    for cell in selected:
        lat, lon = grid.grid_center(cell)
        outgoing = sum(weights.get((cell, dst), 0.0)
                       for dst in selected_set if dst != cell)
        endpoints.append({
            "name": f"mlab:{cell}",
            "lat": lat,
            "lon": lon,
            "weight": outgoing,
            "agg_grid_id": cell,
        })
    summary = dict(summary)
    summary["endpoint_selection"] = {
        "method": "largest_strongly_connected_component",
        "candidate_aggregate_cells": len(set(adjacency) | {
            d for values in adjacency.values() for d in values
        }),
        "candidate_scc_count": len(components),
        "selected_aggregate_cells": len(selected),
        "selected_aggregate_ids": selected,
        "max_sites": max_sites,
        "source_weighting": "measured_outgoing_throughput",
    }
    return endpoints, weights, summary


def compile_trace(resolved: dict, out_dir: str) -> dict:
    """Compile an immutable trace. Returns the manifest dict."""
    cfg = resolved["config"]
    sc, dm, ep = cfg["scenario"], cfg["demand"], cfg["endpoints"]
    mode = dm["mode"]
    duration = float(sc["duration_s"])
    emission_end = (duration if dm["emission_end_s"] is None
                    else float(dm["emission_end_s"]))
    drain_s = duration - emission_end
    bits_per_pkt = int(dm["packet_bits"])
    deadline = dm["deadline_s"]
    out = Path(out_dir)
    if out.is_symlink():
        raise TraceError(f"output directory may not be a symbolic link: {out}")
    os.makedirs(out, exist_ok=True)
    if not out.is_dir():
        raise TraceError(f"output path is not a directory: {out}")
    for name in ("trace.csv", "manifest.json", "nested-family.json"):
        artifact = out / name
        if artifact.is_symlink():
            raise TraceError(f"output artifact may not be a symbolic link: {artifact}")
        if artifact.exists() and not artifact.is_file():
            raise TraceError(f"output artifact is not a regular file: {artifact}")

    rows: list[tuple] = []
    master_candidate_packets = 0
    input_hash = ""
    provenance = "synthetic"
    source_type = "synthetic_generator"
    source_path: str | None = None
    endpoints: list[dict] = []  # csv mode fills this from the CSV itself
    mlab_weights = None
    mlab_summary = None

    if mode == "csv":
        src_path = dm["csv_path"]
        if not src_path or not os.path.exists(src_path):
            raise TraceError(f"csv input not found: {src_path}")
        input_hash = hashlib.sha256(Path(src_path).read_bytes()).hexdigest()
        source_type = "csv_input"
        source_path = str(Path(src_path).resolve())
        with open(src_path, newline="", encoding="utf-8") as fh:
            required = {"packet_id", "emit_time_s", "src_lat", "src_lon", "dst_lat", "dst_lon", "bits"}
            reader = csv.DictReader(fh)
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise TraceError(f"csv missing columns {sorted(missing)}")
            seen_ids: set[int] = set()
            for row in reader:
                src_id = row["packet_id"]
                # packet identity contract: source packet_id values are kept
                # verbatim (never renumbered); they must be unique positive
                # integers so the manifest/receipt bind the exact input set.
                try:
                    pid_val = int(src_id)
                except (TypeError, ValueError):
                    raise TraceError(
                        f"csv packet_id must be a positive integer: {src_id!r}")
                if pid_val <= 0 or str(pid_val) != str(src_id).strip():
                    raise TraceError(
                        f"csv packet_id must be a positive integer: {src_id!r}")
                if pid_val in seen_ids:
                    raise TraceError(f"duplicate packet_id in csv: {src_id}")
                seen_ids.add(pid_val)
                try:
                    t = float(row["emit_time_s"])
                except (TypeError, ValueError):
                    raise TraceError(
                        f"csv row {src_id}: emit_time_s must be a number: "
                        f"{row['emit_time_s']!r}")
                if not math.isfinite(t) or t < 0.0 or t > emission_end:
                    # out-of-horizon records are never silently dropped; an
                    # explicitly approved separate filtering stage would be
                    # required to drop demand, and none exists
                    if dm["emission_end_s"] is None:
                        raise TraceError(
                            f"csv row {src_id}: emit_time_s {t} outside run horizon "
                            f"[0, {duration}]")
                    raise TraceError(
                        f"csv row {src_id}: emit_time_s {t} outside emission window "
                        f"[0, {emission_end}]")
                try:
                    s = grid.aggregate_id(grid.grid_id(float(row["src_lat"]), float(row["src_lon"]), ep["grid_deg"]), ep["aggregation_deg"])
                    d = grid.aggregate_id(grid.grid_id(float(row["dst_lat"]), float(row["dst_lon"]), ep["grid_deg"]), ep["aggregation_deg"])
                except (TypeError, ValueError):
                    raise TraceError(
                        f"csv row {src_id}: invalid endpoint coordinates "
                        f"({row['src_lat']!r}, {row['src_lon']!r}) -> "
                        f"({row['dst_lat']!r}, {row['dst_lon']!r})")
                if s == d:
                    raise TraceError(f"csv row {src_id}: src and dst in the same cell")
                raw_bits = row["bits"]
                try:
                    bits_val = int(raw_bits)
                except (TypeError, ValueError):
                    raise TraceError(
                        f"csv row {src_id}: bits must be a positive integer: "
                        f"{raw_bits!r}")
                if bits_val <= 0 or str(bits_val) != str(raw_bits).strip():
                    raise TraceError(
                        f"csv row {src_id}: bits must be a positive integer: "
                        f"{raw_bits!r}")
                dl_raw = (row.get("deadline_at_s") or "").strip()
                if dl_raw != "":
                    try:
                        dl_val = float(dl_raw)
                    except (TypeError, ValueError):
                        raise TraceError(
                            f"csv row {src_id}: invalid deadline {dl_raw!r}")
                    if not math.isfinite(dl_val):
                        raise TraceError(
                            f"csv row {src_id}: invalid deadline {dl_raw!r}")
                dl = dl_raw  # validated, preserved verbatim (immutable input)
                rows.append((pid_val, t, s, d, bits_val, dl))
        rows.sort(key=lambda r: (r[1], r[0]))  # emission order; ids preserved
        # sparse endpoints come straight from the CSV's active cells;
        # endpoints.sites is not required in csv mode
        active_cells = sorted({r[2] for r in rows} | {r[3] for r in rows})
        endpoints = [{"agg_grid_id": c} for c in active_cells]
    else:
        population_table = None
        if mode == "population_gravity":
            population_table = population.load_population_regions(
                dm["population_path"], ep["aggregation_deg"])
            endpoints = [
                {"name": region.grid_id, "lat": region.lat, "lon": region.lon,
                 "weight": region.population, "agg_grid_id": region.grid_id}
                for region in population_table.regions
            ]
            provenance = "population_proxy"
            input_hash = population_table.source_sha256
            source_type = "population_raster"
            source_path = population_table.source_path
        else:
            if mode == "mlab" and ep["mlab_auto"]:
                endpoints, mlab_weights, mlab_summary = _select_mlab_endpoints(
                    ep["grid_deg"], ep["aggregation_deg"], ep["mlab_max_sites"])
            else:
                endpoints = _endpoints(cfg)
        generators = rng.streams(sc["seed"])
        gen = generators["demand"]
        # Task 6 nested family: when a master load is declared, candidates
        # are generated at the MASTER rate on the demand stream, and every
        # fully generated candidate receives one independent nested_filter
        # draw on its own child-7 stream; kept candidates form the child.
        nested_master = dm["nested_master_offered_mbps"]
        filter_gen = None
        inclusion_probability = 1.0
        if nested_master is not None:
            filter_gen = generators["nested_filter"]
            inclusion_probability = (
                float(dm["offered_mbps"]) / float(nested_master))
        generation_mbps = (float(nested_master)
                           if nested_master is not None
                           else float(dm["offered_mbps"]))
        if mode == "mlab":
            if mlab_weights is None:
                mlab_weights, mlab_summary = _load_mlab_weights(
                    endpoints, ep["grid_deg"], ep["aggregation_deg"])
            provenance = "measurement_proxy"
            input_hash = hashlib.sha256(REPO_MLAB_CSV.read_bytes()).hexdigest()
            source_type = "mlab_snapshot"
            source_path = str(REPO_MLAB_CSV.resolve())
            # coverage contract (fail closed): every active source cell must
            # have positive measurement weight to at least one other active
            # cell; there is NO smoothing fallback into uniform demand.
            uncovered = []
            for e in endpoints:
                total = sum(
                    mlab_weights.get((e["agg_grid_id"], d["agg_grid_id"]), 0.0)
                    for d in endpoints if d["agg_grid_id"] != e["agg_grid_id"])
                if total <= 0.0:
                    uncovered.append(e["agg_grid_id"])
            if uncovered:
                raise TraceError(
                    f"mlab measurements do not cover active OD source(s) "
                    f"{uncovered}; measurement_proxy demand cannot be compiled "
                    "without measurement coverage (fail closed, no silent "
                    "uniform fallback)")
        total_rate = generation_mbps * 1e6 / bits_per_pkt  # pkts/s across endpoints
        source_exponent = (dm["source_population_exponent"]
                           if mode == "population_gravity" else 1.0)
        weights = [e["weight"] ** source_exponent for e in endpoints]
        wsum = sum(weights)
        # exact opt-in destination sampler: ONE alias proposal table for the
        # whole population universe (never one O(N) table per source)
        population_alias = None
        if mode == "population_gravity" \
                and dm["population_destination_sampler"] == "alias_rejection":
            destination_exponent = dm["destination_population_exponent"]
            population_alias = VoseAlias(
                [e["weight"] ** destination_exponent for e in endpoints])
        pid = 0
        master_rows: list[tuple] = []
        nested_child_rows: list[tuple] | None = \
            [] if filter_gen is not None else None
        for i, e in enumerate(endpoints):
            base_rate = total_rate * weights[i] / wsum
            if base_rate <= 0:
                continue
            # thinning with max multiplier keeps diurnal/burst deterministic
            max_mult = 1.0
            if mode in ("burst", "mlab") and dm["burst_start_s"] is not None:
                max_mult = max(1.0, dm["burst_multiplier"])
            elif mode == "diurnal":
                max_mult = 1.0 + abs(dm["diurnal_amplitude"])
            elif mode == "population_gravity" \
                    and dm["temporal_model"] == "local_diurnal_cosine":
                # same envelope legacy diurnal uses: 1 + |amplitude|
                max_mult = 1.0 + abs(dm["diurnal_amplitude"])
            t = 0.0
            while True:
                t += float(gen.exponential(1.0 / (base_rate * max_mult)))
                if t > emission_end:
                    break
                if gen.random() > _rate_multiplier(mode, t, e["lon"], dm) / max_mult:
                    continue
                pid += 1
                if population_alias is not None:
                    dst = sample_population_destination(
                        gen, population_alias, endpoints, i, dm)
                else:
                    dst = _dst_choices(gen, mode, endpoints, i, t, dm,
                                       mlab_weights)
                dl = f"{t + deadline:.6f}" if deadline is not None else ""
                candidate = (pid, t, e["agg_grid_id"], dst["agg_grid_id"],
                             bits_per_pkt, dl)
                master_rows.append(candidate)
                # every fully generated candidate receives exactly one
                # independent nested_filter draw (generation order); kept
                # candidates form the child trace
                if nested_child_rows is not None \
                        and filter_gen.random() < inclusion_probability:
                    nested_child_rows.append(candidate)
        # the master candidate count is binding BEFORE any child filtering
        master_candidate_packets = len(master_rows)
        if nested_master is not None and \
                master_candidate_packets > int(cfg["execution"]["max_packets"]):
            raise TraceError(
                f"nested master trace would contain "
                f"{master_candidate_packets} candidate packets > "
                f"execution.max_packets "
                f"({int(cfg['execution']['max_packets'])}); tighten the "
                f"master load instead of generating an unbounded trace")
        if nested_child_rows is not None:
            rows = nested_child_rows
        else:
            rows = master_rows
        rows.sort(key=lambda r: (r[1], r[0]))
        rows = [(i + 1, *r[1:]) for i, r in enumerate(rows)]

    # compile-time bound: refuse unbounded traces before the kernel ever runs
    max_packets = int(cfg["execution"]["max_packets"])
    if len(rows) > max_packets:
        raise TraceError(
            f"trace would contain {len(rows)} packets > execution.max_packets "
            f"({max_packets}); tighten the demand config instead of generating "
            f"an unbounded trace")

    # Validate the exact serialized values, not higher-precision in-memory
    # values.  This guarantees compile success implies load_trace success.
    serialized_rows = [
        (pid, _serialized_time(t), s, d, bits,
         (_serialized_time(float(dl)) if dl != "" else ""))
        for pid, t, s, d, bits, dl in rows
    ]
    validate_packet_rows(
        [{"packet_id": pid, "emit_time_s": float(t), "src_grid_id": s,
          "dst_grid_id": d, "bits": int(bits),
          "deadline_at_s": (float(dl) if dl != "" else None)}
         for pid, t, s, d, bits, dl in serialized_rows],
        horizon_s=duration, max_packets=max_packets)

    trace_path = out / "trace.csv"
    with open(trace_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["packet_id", "emit_time_s", "src_grid_id", "dst_grid_id", "bits", "deadline_at_s"])
        for pid, t, s, d, bits, dl in serialized_rows:
            w.writerow([pid, _format_time(t), s, d, bits,
                        (_format_time(dl) if dl != "" else "")])
    trace_sha256 = hashlib.sha256(trace_path.read_bytes()).hexdigest()

    offered_bits = sum(r[4] for r in rows)
    provenance_contract = {
        "schema": TRACE_PROVENANCE_SCHEMA,
        "simulation_horizon_s": duration,
        "emission_end_s": emission_end,
        "drain_s": drain_s,
        "source": {
            "type": source_type,
            "path": source_path,
            "sha256": input_hash,
        },
        "units": {
            "emit_time": "seconds_since_run_start",
            "deadline": "seconds_since_run_start_or_empty",
            "coordinates": "degrees_wgs84",
            "bits": "bits",
        },
        "od_mapping": {
            "input_coordinate_fields": (
                ["src_lat", "src_lon", "dst_lat", "dst_lon"]
                if mode == "csv" else None
            ),
            "output_fields": ["src_grid_id", "dst_grid_id"],
            "grid_deg": ep["grid_deg"],
            "aggregation_deg": ep["aggregation_deg"],
            "rule": (
                "grid_id(lat,lon,grid_deg) then aggregate_id(...,aggregation_deg)"
                if mode == "csv" else
                ("M-Lab aggregate cells selected from the largest measured "
                 "strongly connected component, bounded by endpoints.mlab_max_sites"
                 if mode == "mlab" and ep["mlab_auto"] else
                 "generated endpoint aggregate IDs")
            ),
        },
        "measurement_summary": mlab_summary,
        "offered_load": {
            "load_mode": "observed_trace" if mode == "csv" else "target_rate_sampler",
            "target_offered_mbps": (
                None if mode == "csv" else float(dm["offered_mbps"])
            ),
            "realized_offered_mbps": (
                float(offered_bits) / emission_end / 1_000_000.0
                if emission_end > 0 else 0.0
            ),
            "horizon_s": duration,
            "packet_bits": bits_per_pkt,
            "offered_packets": len(rows),
            "offered_bits": offered_bits,
        },
        "traffic_transform": {
            "mode": mode,
            "burst": ({
                "start_s": float(dm["burst_start_s"]),
                "duration_s": float(dm["burst_duration_s"]),
                "multiplier": float(dm["burst_multiplier"]),
            } if mode in ("burst", "mlab")
            and dm["burst_start_s"] is not None else None),
            # legacy mode:diurnal keeps its historical two-key value
            # exactly; only the new population-local-time combination uses
            # the four-key value with the explicit proxy clock label
            "diurnal": ({
                "amplitude": float(dm["diurnal_amplitude"]),
                "phase_h": float(dm["diurnal_phase_h"]),
            } if mode == "diurnal" else
             ({
                "amplitude": float(dm["diurnal_amplitude"]),
                "phase_h": float(dm["diurnal_phase_h"]),
                "utc_start_hour": float(dm["utc_start_hour"]),
                "clock": "source_local_solar_time_proxy",
             } if mode == "population_gravity"
               and dm["temporal_model"] == "local_diurnal_cosine" else None)),
        },
    }
    # rng_streams contract: nested families select the canonical demand
    # and nested-filter entries from the FULL stream mapping; legacy and
    # non-nested traces keep the historical single-demand mapping
    # (demand is child 0 in both branches; nested_filter is child 7).
    if dm["nested_master_offered_mbps"] is not None:
        full_streams = rng.stream_mapping(sc["seed"])
        rng_streams = {"demand": full_streams["demand"],
                       "nested_filter": full_streams["nested_filter"]}
    else:
        rng_streams = rng.stream_mapping(sc["seed"], ["demand"])

    manifest = {
        "schema": TRACE_MANIFEST_SCHEMA,
        "trace_schema": TRACE_SCHEMA,
        "trace_sha256": trace_sha256,
        "trace_identity_sha256": config.trace_identity_sha256(resolved, input_hash),
        "config_version": resolved["version"],
        "input_sha256": input_hash,
        "mode": mode,
        "provenance": provenance,
        "simulation_horizon_s": duration,
        "emission_end_s": emission_end,
        "drain_s": drain_s,
        "rng_streams": rng_streams,
        "packet_id_contract": PACKET_ID_CONTRACT,
        "offered_packets": len(rows),
        "offered_bits": offered_bits,
        "ledger": {"packets": len(rows), "bits": offered_bits},
        # Activation is sparse and trace-derived: configured sites/cells that
        # emitted or received no packet are not runtime endpoints.
        "active_endpoints": len({r[2] for r in serialized_rows}
                                | {r[3] for r in serialized_rows}),
        "time_range_s": [serialized_rows[0][1] if serialized_rows else 0.0,
                         serialized_rows[-1][1] if serialized_rows else 0.0],
        "provenance_contract": provenance_contract,
    }
    if provenance == "measurement_proxy":
        manifest["not_calibrated_user_demand"] = True
        manifest["provenance_note"] = (
            "M-Lab measurements reused as OD weight proxy only; "
            "this is measurement_proxy traffic, never calibrated user demand."
        )
    elif provenance == "population_proxy":
        manifest.update({
            "not_calibrated_user_demand": True,
            "provenance_note": (
                "GPW population counts drive source intensity and gravity "
                "destination probabilities; this is population_proxy demand, "
                "never calibrated Internet traffic."),
            "population": {
                "source_path": population_table.source_path,
                "source_sha256": population_table.source_sha256,
                "source_shape": list(population_table.source_shape),
                "source_resolution_deg": list(
                    population_table.source_resolution_deg),
                "aggregation_deg": population_table.aggregation_deg,
                "total_population": population_table.total_population,
                "candidate_regions": len(population_table.regions),
                "source_population_exponent": dm[
                    "source_population_exponent"],
                "destination_population_exponent": dm[
                    "destination_population_exponent"],
                "distance_exponent": dm["gravity_alpha"],
                "distance_floor_km": dm["gravity_d_floor_km"],
            },
        })
    with open(out / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    if dm["nested_master_offered_mbps"] is not None:
        # exact-key, versioned companion artifact (never nested metadata in
        # the manifest itself).  Written only after trace.csv and
        # manifest.json succeeded, so a failed compile cannot leave a
        # companion that appears valid.
        from . import trace_family as _family
        family = {
            "schema": _family.FAMILY_SCHEMA,
            "family_identity_sha256": _family.family_identity_sha256(
                resolved, manifest["input_sha256"]),
            "master_offered_mbps": float(dm["nested_master_offered_mbps"]),
            "child_offered_mbps": float(dm["offered_mbps"]),
            "inclusion_probability": (
                float(dm["offered_mbps"])
                / float(dm["nested_master_offered_mbps"])),
            "master_candidate_packets": master_candidate_packets,
            "child_packets": len(rows),
            "demand_rng_stream": _family._canonical_stream_label(
                resolved, "demand"),
            "filter_rng_stream": _family._canonical_stream_label(
                resolved, "nested_filter"),
            "canonical_row_contract": _family.CANONICAL_ROW_CONTRACT,
            "config_sha256": resolved["sha256"],
            "trace_identity_sha256": manifest["trace_identity_sha256"],
            "trace_sha256": trace_sha256,
        }
        with open(out / "nested-family.json", "w", encoding="utf-8") as fh:
            json.dump(family, fh, indent=2, sort_keys=True)
            fh.write("\n")
    return manifest


def load_trace(path: str, horizon_s: float | None = None,
               max_packets: int | None = None) -> list[dict]:
    """Load a compiled trace.csv into immutable-style dict rows (fail closed).

    Every row passes the unified packet-row contract; when horizon_s /
    max_packets are given they are enforced here as well (the kernel always
    re-enforces both at run entry regardless of loader arguments).
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if set(reader.fieldnames or []) != {"packet_id", "emit_time_s", "src_grid_id", "dst_grid_id", "bits", "deadline_at_s"}:
            raise TraceError(f"trace columns mismatch in {path}")
        for i, r in enumerate(reader):
            try:
                pid = int(r["packet_id"])
                t = float(r["emit_time_s"])
                bits = int(r["bits"])
            except (TypeError, ValueError) as exc:
                raise TraceError(f"trace row {i}: unparsable numeric field: {exc}")
            dl_raw = r["deadline_at_s"]
            if dl_raw in (None, ""):
                dl = None
            else:
                try:
                    dl = float(dl_raw)
                except (TypeError, ValueError):
                    raise TraceError(f"trace row {i}: unparsable deadline {dl_raw!r}")
            rows.append({
                "packet_id": pid,
                "emit_time_s": t,
                "src_grid_id": r["src_grid_id"],
                "dst_grid_id": r["dst_grid_id"],
                "bits": bits,
                "deadline_at_s": dl,
            })
    validate_packet_rows(
        rows,
        horizon_s=math.inf if horizon_s is None else float(horizon_s),
        max_packets=(1 << 62) if max_packets is None else int(max_packets))
    return rows
