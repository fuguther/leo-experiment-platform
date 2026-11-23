"""Reproducible, non-kernel static ISL load design calculation for T1.

This computes a nominal load coefficient from one declared geometry snapshot.
It does not compile a trace, run the kernel, model access contention, or prove
that a later stochastic run activates the intended competition structure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

from CODE.leo_sim import config, link_budget, model, population, routing, trace


DIRECTION_ORDER = {name: i for i, name in enumerate(("N", "E", "S", "W"))}


def _shortest_tree(adj, sources):
    """Deterministic multi-source BFS tree, reusable for every destination."""
    source_nodes = sorted(set(int(s) for s in sources))
    parent = {}
    owner = {}
    distance = {}
    queue = []
    for source in source_nodes:
        distance[source] = 0
        owner[source] = source
        queue.append(source)
    cursor = 0
    while cursor < len(queue):
        node = queue[cursor]
        cursor += 1
        for peer, direction in sorted(
                adj.get(node, ()),
                key=lambda item: (int(item[0]), DIRECTION_ORDER[item[1]])):
            if peer in distance:
                continue
            distance[peer] = distance[node] + 1
            owner[peer] = owner[node]
            parent[peer] = (node, direction)
            queue.append(peer)
    return distance, owner, parent


def _tree_path(tree, destinations):
    distance, owner, parent = tree
    destination_nodes = sorted(set(int(s) for s in destinations))
    reachable = [sat for sat in destination_nodes if sat in distance]
    if not reachable:
        return None
    destination = min(reachable, key=lambda sat: (distance[sat], sat))
    edges = []
    node = destination
    while node in parent:
        previous, direction = parent[node]
        edges.append((previous, direction, node))
        node = previous
    edges.reverse()
    return distance[destination], owner[destination], destination, tuple(edges)


def shortest_path(adj, sources, destinations):
    """Deterministic multi-source BFS; return (hops, source, dest, edges)."""
    if not sources or not destinations:
        return None
    return _tree_path(_shortest_tree(adj, sources), destinations)


def destination_probabilities(regions, source_index, *, source_exponent,
                              destination_exponent, distance_exponent,
                              distance_floor_km):
    """Match trace._dst_choices population_gravity exactly, without sampling."""
    source = regions[source_index]
    candidates = []
    for index, destination in enumerate(regions):
        if destination.grid_id == source.grid_id:
            continue
        distance = max(trace._haversine_km(
            source.lat, source.lon, destination.lat, destination.lon),
            distance_floor_km)
        candidates.append((index, destination.population ** destination_exponent
                           / distance ** distance_exponent))
    total = sum(weight for _index, weight in candidates)
    if total <= 0:
        raise ValueError(f"source {source.grid_id} has no destination weight")
    return tuple((index, weight / total) for index, weight in candidates)


def compute_static_load(regions, source_weights, access_sats=None, adjacency=None,
                        edge_capacity_bps=None, *, source_access_sats=None,
                        destination_access_sats=None, source_exponent=1.0,
                        destination_exponent=1.0, distance_exponent=1.25,
                        distance_floor_km=100.0):
    """Compute directed per-port u_e=Σ p_OD/C_e in inverse Mbps.

    ``access_sats`` is retained for the earlier pure-fixture API, where the
    same set represented both access directions.  Profile construction must
    use the explicit source and destination arrays, each containing at most
    the independently selected single service satellite.
    """
    if source_access_sats is None and destination_access_sats is None:
        if access_sats is None:
            raise ValueError("source and destination access arrays are required")
        # Preserve existing fixture semantics without using this multi-access
        # compatibility form for an actual profile.
        source_access_sats = destination_access_sats = access_sats
    elif source_access_sats is None or destination_access_sats is None:
        raise ValueError(
            "source_access_sats and destination_access_sats must be supplied together")
    if adjacency is None or edge_capacity_bps is None:
        raise ValueError("adjacency and edge capacities are required")
    if (len(regions) != len(source_weights)
            or len(regions) != len(source_access_sats)
            or len(regions) != len(destination_access_sats)):
        raise ValueError(
            "region, source-weight and both access arrays must align")

    normalized_capacities = {}
    for raw_edge, raw_capacity in edge_capacity_bps.items():
        edge = tuple(raw_edge)
        if len(edge) != 3:
            raise ValueError(f"invalid directed ISL edge key: {raw_edge!r}")
        key = (int(edge[0]), str(edge[1]), int(edge[2]))
        try:
            capacity = float(raw_capacity)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid capacity for directed edge {key}") from exc
        if not math.isfinite(capacity) or capacity <= 0.0:
            raise ValueError(
                f"directed edge capacity must be finite and positive: {key}={raw_capacity!r}")
        if key in normalized_capacities:
            raise ValueError(f"duplicate normalized directed edge {key}")
        normalized_capacities[key] = capacity
    adjacency_edges = set()
    for raw_sat, neighbors in adjacency.items():
        for raw_peer, raw_direction in neighbors:
            key = (int(raw_sat), str(raw_direction), int(raw_peer))
            if key in adjacency_edges:
                raise ValueError(f"duplicate directed adjacency edge {key}")
            adjacency_edges.add(key)
            if key not in normalized_capacities:
                raise ValueError(f"missing finite positive capacity for edge {key}")
    orphan_capacities = set(normalized_capacities) - adjacency_edges
    if orphan_capacities:
        raise ValueError(
            f"capacity declared for edge absent from adjacency: {min(orphan_capacities)}")

    u = {edge: 0.0 for edge in normalized_capacities}
    direct_mass = 0.0
    routed_mass = 0.0
    unroutable_mass = 0.0
    od_count = 0
    path_hash = hashlib.sha256()
    weights = [float(w) for w in source_weights]
    if any(not math.isfinite(weight) or weight < 0.0 for weight in weights):
        raise ValueError("source weights must be finite and nonnegative")
    source_total = sum(weights)
    if source_total <= 0:
        raise ValueError("source population mass must be positive")
    for src_index, src in enumerate(regions):
        p_source = weights[src_index] / source_total
        tree = _shortest_tree(adjacency, source_access_sats[src_index])
        dst_probs = destination_probabilities(
            regions, src_index, source_exponent=source_exponent,
            destination_exponent=destination_exponent,
            distance_exponent=distance_exponent,
            distance_floor_km=distance_floor_km)
        for dst_index, p_dst in dst_probs:
            dst = regions[dst_index]
            p_od = p_source * p_dst
            route = _tree_path(tree, destination_access_sats[dst_index])
            od_count += 1
            if route is None:
                unroutable_mass += p_od
                edges = ()
                route_doc = {"src": src.grid_id, "dst": dst.grid_id,
                             "p": p_od, "path": None}
            else:
                _hops, _src_sat, _dst_sat, edges = route
                routed_mass += p_od
                if not edges:
                    direct_mass += p_od
                else:
                    for edge in edges:
                        key = tuple(edge)
                        capacity = normalized_capacities[key]
                        u[key] += p_od / (capacity / 1_000_000.0)
                route_doc = {"src": src.grid_id, "dst": dst.grid_id,
                             "p": p_od, "path": [list(e) for e in edges]}
            path_hash.update(json.dumps(
                route_doc, sort_keys=True, separators=(",", ":"),
                allow_nan=False).encode("utf-8"))
            path_hash.update(b"\n")
    positive = [(value, edge) for edge, value in u.items() if value > 0.0]
    if not positive:
        raise ValueError("static OD set produces no routed ISL load")
    max_u, bottleneck = max(positive, key=lambda item: (item[0], item[1]))
    per_port = [{
        "edge": "%d:%s:%d" % edge,
        "capacity_bps": normalized_capacities[edge],
        "capacity_mbps": normalized_capacities[edge] / 1_000_000.0,
        "u_inverse_mbps": u[edge],
    } for edge in sorted(normalized_capacities)]
    return {
        "od_pairs": od_count,
        "source_mass_sum": source_total,
        "routed_od_mass": routed_mass,
        "direct_od_mass": direct_mass,
        "unroutable_od_mass": unroutable_mass,
        "mass_conservation_error": abs(
            routed_mass + unroutable_mass - 1.0),
        "path_table_sha256": path_hash.hexdigest(),
        "per_port": per_port,
        "per_port_u_inverse_mbps": {
            "%d:%s:%d" % edge: value
            for edge, value in sorted(u.items())},
        "per_port_capacity_mbps": {
            "%d:%s:%d" % edge: normalized_capacities[edge] / 1_000_000.0
            for edge in sorted(normalized_capacities)},
        "max_u_inverse_mbps": max_u,
        "bottleneck_edge": list(bottleneck),
        "base_R_mbps_for_nominal_0_6": 0.6 / max_u,
        "peak_2R_mbps": 1.2 / max_u,
    }


def build_static_profile(profile_path: str, t_ref: float = 2.0) -> dict:
    path = Path(profile_path)
    resolved = config.load_config_file(str(path))
    cfg = resolved["config"]
    sc, ep, dm, lk = (cfg[k] for k in ("scenario", "endpoints", "demand", "links"))
    table = population.load_population_regions(
        dm["population_path"], ep["aggregation_deg"],
        lat_bounds_deg=ep["region_lat_bounds_deg"],
        lon_bounds_deg=ep["region_lon_bounds_deg"])
    regions = table.regions
    geo = model.Constellation(
        num_satellites=sc["num_satellites"],
        num_planes=sc["num_planes"], altitude_km=sc["altitude_km"],
        inclination_deg=sc["inclination_deg"],
        min_elevation_deg=sc["min_elevation_deg"],
        max_isl_km=lk["max_isl_km"],
        geometry_epoch_s=sc["geometry_epoch_s"])
    topo = routing.build_topology(
        geo, sc["num_satellites"], lk["isl_dirs"], t=float(t_ref))
    if lk["rate_model"] == "mcs":
        rf = link_budget.RFParams.from_mapping(lk["rf_isl"])
        rate_for_edge = lambda a, b: link_budget.mcs_rate_bps(
            geo.isl_range_km(a, b, float(t_ref)), rf, lk["mcs_table"])
        uplink_rf = link_budget.RFParams.from_mapping(lk["rf_uplink"])
        downlink_rf = link_budget.RFParams.from_mapping(lk["rf_downlink"])
        uplink_rate = lambda s, region: link_budget.mcs_rate_bps(
            geo.slant_range_km(s, region.lat, region.lon, float(t_ref)),
            uplink_rf, lk["mcs_table"])
        downlink_rate = lambda s, region: link_budget.mcs_rate_bps(
            geo.slant_range_km(s, region.lat, region.lon, float(t_ref)),
            downlink_rf, lk["mcs_table"])
    else:
        rate_for_edge = lambda _a, _b: float(lk["isl_rate_mbps"]) * 1e6
        uplink_rate = lambda _s, _region: float(cfg["access"]["uplink_rate_mbps"]) * 1e6
        downlink_rate = lambda _s, _region: float(cfg["access"]["downlink_rate_mbps"]) * 1e6

    adjacency = {}
    edge_capacity = {}
    unavailable_zero_capacity_edges = 0
    for sat, neighbors in topo.items():
        for direction, peer in neighbors.items():
            if not geo.isl_available(sat, peer, float(t_ref)):
                continue
            capacity = float(rate_for_edge(sat, peer))
            if not math.isfinite(capacity):
                raise ValueError(
                    f"non-finite ISL capacity at t={t_ref}: "
                    f"{sat}:{direction}:{peer}={capacity!r}")
            if capacity <= 0.0:
                unavailable_zero_capacity_edges += 1
                continue
            edge = (int(sat), str(direction), int(peer))
            edge_capacity[edge] = capacity
            adjacency.setdefault(int(sat), []).append((int(peer), str(direction)))

    def select_access(region, rate_fn, label):
        eligible = []
        for sat in range(sc["num_satellites"]):
            if not geo.ground_visible(sat, region.lat, region.lon, float(t_ref)):
                continue
            rate = float(rate_fn(sat, region))
            if not math.isfinite(rate):
                raise ValueError(
                    f"non-finite {label} GSL rate for {region.grid_id}, "
                    f"satellite {sat}: {rate!r}")
            if rate < 0.0:
                raise ValueError(
                    f"negative {label} GSL rate for {region.grid_id}, "
                    f"satellite {sat}: {rate!r}")
            if rate == 0.0:
                continue
            elevation = float(geo.elevation_deg(
                sat, region.lat, region.lon, float(t_ref)))
            if not math.isfinite(elevation):
                raise ValueError(
                    f"non-finite elevation for {region.grid_id}, "
                    f"satellite {sat}: {elevation!r}")
            eligible.append((sat, elevation, rate))
        if not eligible:
            return None
        # Highest elevation, then lowest satellite ID.  Access availability
        # and selection are separately computed for uplink and downlink.
        return min(eligible, key=lambda item: (-item[1], item[0]))

    source_access_sats = []
    destination_access_sats = []
    association_rows = []
    for region in regions:
        source = select_access(region, uplink_rate, "uplink")
        destination = select_access(region, downlink_rate, "downlink")
        source_access_sats.append(() if source is None else (source[0],))
        destination_access_sats.append(
            () if destination is None else (destination[0],))
        association_rows.append({
            "grid_id": str(region.grid_id),
            "source_sat": None if source is None else source[0],
            "destination_sat": None if destination is None else destination[0],
            "source_elevation_deg": None if source is None else source[1],
            "destination_elevation_deg": (None if destination is None
                                           else destination[1]),
            "source_rate_bps": None if source is None else source[2],
            "destination_rate_bps": (None if destination is None
                                     else destination[2]),
        })
    static = compute_static_load(
        regions, [r.population ** dm["source_population_exponent"]
                  for r in regions], adjacency=adjacency,
        edge_capacity_bps=edge_capacity,
        source_access_sats=source_access_sats,
        destination_access_sats=destination_access_sats,
        source_exponent=dm["source_population_exponent"],
        destination_exponent=dm["destination_population_exponent"],
        distance_exponent=dm["gravity_alpha"],
        distance_floor_km=dm["gravity_d_floor_km"])
    raw = path.read_bytes()
    out = {
        "schema": "t1-static-load-design/v1",
        "calculation": "static geometry and gravity OD only; no trace and no kernel",
        "profile": str(path),
        "profile_sha256": hashlib.sha256(raw).hexdigest(),
        "population_sha256": table.source_sha256,
        "region": {"lat_half_open_deg": ep["region_lat_bounds_deg"],
                   "lon_half_open_deg": ep["region_lon_bounds_deg"],
                   "aggregation_center_filter": True,
                   "positive_population_regions": len(regions),
                   "population_total": table.total_population},
        "geometry": {"time_s": float(t_ref),
                     "num_satellites": sc["num_satellites"],
                     "num_planes": sc["num_planes"],
                     "altitude_km": sc["altitude_km"],
                     "inclination_deg": sc["inclination_deg"],
                     "min_elevation_deg": sc["min_elevation_deg"],
                     "max_isl_km": lk["max_isl_km"],
                     "rate_model": lk["rate_model"],
                     "isl_capacity_edge_count": len(edge_capacity),
                     "isl_zero_rate_edge_count": unavailable_zero_capacity_edges,
                     "active_ground_source_regions": sum(
                         bool(v) for v in source_access_sats),
                     "active_ground_destination_regions": sum(
                         bool(v) for v in destination_access_sats),
                     "ground_source_region_access_satellites": sum(
                         map(len, source_access_sats)),
                     "ground_destination_region_access_satellites": sum(
                         map(len, destination_access_sats))},
        "access_association": {
            "rule": ("highest elevation among positive finite direction-specific "
                     "GSL rates; ties by satellite ID"),
            "time_s": float(t_ref),
            "rows": association_rows,
            "sha256": hashlib.sha256(json.dumps(
                association_rows, sort_keys=True, separators=(",", ":"),
                allow_nan=False).encode("utf-8")).hexdigest(),
        },
        "algorithm": {
            "source_probability": "population^source_exponent / regional total",
            "destination_probability": (
                "exclude same cell; population^destination_exponent / "
                "max(great_circle_km, distance_floor_km)^gravity_alpha; "
                "normalize among remaining regional cells"),
            "access": ("independently select exactly one source uplink and "
                       "one destination downlink satellite per cell by highest "
                       "elevation among positive finite service rates; ties "
                       "by satellite ID"),
            "path": ("shortest-ISL-hop over positive finite-capacity directed "
                     "ports at the time_s snapshot; peer ties use satellite "
                     "ID and N/E/S/W"),
            "unroutable_mass": "retained in denominator and reported; not renormalized",
            "u_e_units": "inverse Mbps",
            "R": "0.6 / max_e(u_e), in Mbps; peak is 2R",
        },
        **static,
    }
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--t-ref", type=float, default=2.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    report = build_static_profile(args.profile, args.t_ref)
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "schema", "profile_sha256", "population_sha256", "region",
        "geometry", "od_pairs", "routed_od_mass", "direct_od_mass",
        "unroutable_od_mass", "max_u_inverse_mbps", "bottleneck_edge",
        "base_R_mbps_for_nominal_0_6", "peak_2R_mbps")},
        sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
