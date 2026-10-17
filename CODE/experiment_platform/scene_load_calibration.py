#!/usr/bin/env python3
"""Scene load calibration for the T1 LEO inter-satellite routing study.

WHAT THIS COMPUTES
------------------
Given a constellation geometry, an origin-destination (OD) demand definition,
a link capacity model and a control-plane definition, this module produces the
injection rates R(rho) that put the *reference* bottleneck link at a target
utilisation rho, together with the feasibility checks that decide whether a
tier can be run at all.

It is deliberately independent of any routing method under comparison: the
reference is a FIXED, reproducible minimum-propagation-delay route set, and the
same OD weights / paths / seed are used for every method.

DEFINITIONS (the exact ones implemented here)
---------------------------------------------
* OD business k = one ordered (source cell, destination cell) pair.
* w_k = long-run fraction of injected BITS on OD k, with sum_k w_k = 1.
* a[e,k] in [0,1] = fraction of OD k's traffic crossing DIRECTED link e.
  This implementation uses a single deterministic shortest-propagation-delay
  path per OD (no equal-cost splitting); a[e,k] is therefore 0 or 1 and the
  ECMP question does not arise.  The tie-break is fixed: among predecessors u
  of t with dist[u] + prop(u,t) == dist[t], the smallest satellite id wins.
* b_e = sum_k a[e,k] * w_k                   (link load coefficient, 1/bit).
* D_e = R * b_e                              (reference data load, bit/s)
  where R is the NETWORK-WIDE TOTAL injection rate, not per source and not
  per OD.  R is the same number the simulator is configured with
  (demand.offered_mbps * 1e6).
* c_e = control-plane bit rate on directed link e, derived from the real
  packet size, advertising period and the real relay rule (a deterministic
  shortest-path broadcast tree of at most vis_k hops per origin), NOT from a
  fixed percentage.
* C_e = effective service capacity of e inside the same time window.
  A link that is down, invisible or rate-limited for part of the window
  contributes only its available fraction; "closed" is never counted as full
  capacity.
* rho_e(R) = (R * b_e + c_e) / C_e.

CALIBRATION CRITERION
---------------------
R(rho) = min over (window w, directed link e in window w with b_e > 0) of
             (rho * C_e^w - c_e^w) / b_e^w

The topology is rebuilt window by window, so a link that exists only briefly
still constrains the tier through its own window.  This is a WORST-WINDOW
criterion: the returned R is the largest network-total injection rate that
holds every directed link at or below rho in every window of the declared
horizon.  If c_e^w >= rho * C_e^w for any link that carries reference data,
the tier is reported INFEASIBLE.  It is never clamped to zero and run anyway.

UNITS
-----
distance km, time s, rate bit/s, capacity bit/s, packet size bit.
Config rates are Mbps and are converted explicitly at the boundary.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "CODE") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "CODE"))

C_KM_S = 299792.458            # speed of light, km/s (matches leo_sim.model)
EARTH_RADIUS_KM = 6371.0
SCHEMA = "scene-load-calibration/v1"
DEFAULT_TIERS = (0.20, 0.50, 0.70, 0.85, 0.95, 1.10)


class CalibrationError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# topology providers
# --------------------------------------------------------------------------
def _haversine_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180.0
    a = (math.sin((lat2 - lat1) * p / 2.0) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p)
         * math.sin((lon2 - lon1) * p / 2.0) ** 2)
    return 2.0 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


class ExplicitTopology:
    """Hand-checkable topology: a fixed adjacency with explicit distances.

    Used by --selftest and by any study that wants to separate the arithmetic
    from the geometry module.  links: {"u,v": distance_km} for a DIRECTED edge
    u -> v; the reverse edge must be listed separately (physical ISLs are
    bidirectional but the calculation is directed).
    """

    def __init__(self, num_nodes, links, elevation_deg=None, min_elevation_deg=10.0):
        self.num_nodes = num_nodes
        self._links = {tuple(int(x) for x in k.split(",")): float(v)
                       for k, v in links.items()}
        self._elev = elevation_deg or {}

    def topology_at(self, t):
        topo = {s: {} for s in range(self.num_nodes)}
        for (u, v) in self._links:
            topo[u][str(v)] = v
        return topo

    def range_km(self, u, v, t):
        return self._links[(u, v)]

    def visible(self, cell, t):
        """cell is the full cell record; keys elevation by cell id."""
        return list(self._elev.get(cell["id"], []))


class ConstellationTopology:
    """Real leo_sim Walker geometry: the topology the simulator itself builds.

    Dynamic mode rebuilds neighbours at every window with the same greedy
    cross-plane matching the kernel uses, then applies the same physical
    availability test (max range AND earth clearance).
    """

    def __init__(self, num_satellites, num_planes, altitude_km, inclination_deg,
                 min_elevation_deg, max_isl_km, geometry_epoch_s,
                 dirs=("N", "S", "E", "W"), dynamic=True):
        from leo_sim import model as model_mod
        self.model = model_mod
        self.geo = model_mod.Constellation(
            num_satellites, num_planes, altitude_km, inclination_deg,
            min_elevation_deg, max_isl_km, geometry_epoch_s)
        self.num_nodes = num_satellites
        self.dirs = tuple(dirs)
        self.dynamic = dynamic

    def topology_at(self, t):
        from leo_sim import routing as routing_mod
        topo = routing_mod.build_topology(
            self.geo, self.num_nodes, self.dirs,
            t=(t if self.dynamic else None))
        out = {}
        for s, nb in topo.items():
            keep = {}
            for d, n in nb.items():
                if self.geo.isl_available(s, n, t):
                    keep[d] = n
            out[s] = keep
        # physical ISLs are bidirectional: verify, never fabricate a reverse
        for s, nb in out.items():
            for d, n in nb.items():
                if s not in out.get(n, {}).values():
                    raise CalibrationError(
                        f"non-bidirectional ISL {s}-{d}->{n}; refusing")
        return out

    def range_km(self, u, v, t):
        return self.geo.isl_range_km(u, v, t)

    def visible(self, cell, t):
        lat, lon = float(cell["lat"]), float(cell["lon"])
        out = []
        for s in range(self.num_nodes):
            elev = self.geo.elevation_deg(s, lat, lon, t)
            if elev > self.geo.min_elevation_deg:
                out.append((elev, s))
        return out


# --------------------------------------------------------------------------
# OD construction
# --------------------------------------------------------------------------
def od_weights_from_gravity(sites, source_exponent=1.0,
                            destination_exponent=1.0, gravity_alpha=1.5,
                            d_floor_km=100.0):
    """Analytic OD weights for the simulator's population/gravity sampler.

    Mirrors trace.py: source i is drawn with weight W_i**source_exponent /
    sum(W**source_exponent); destination j given i with weight
    W_j**destination_exponent * (d_floor / d_ij)**gravity_alpha, normalised
    over j != i.  Returns {(i, j): w} with sum == 1 (to float tolerance).
    """
    n = len(sites)
    src = [float(s["weight"]) ** source_exponent for s in sites]
    dst = [float(s["weight"]) ** destination_exponent for s in sites]
    total_src = sum(src)
    if total_src <= 0:
        raise CalibrationError("source weights sum to zero")
    od = {}
    for i in range(n):
        dist = [max(_haversine_km(sites[i]["lat"], sites[i]["lon"],
                                  sites[j]["lat"], sites[j]["lon"]), d_floor_km)
                for j in range(n)]
        den = 0.0
        for j in range(n):
            if j == i:
                continue
            den += dst[j] * (d_floor_km / dist[j]) ** gravity_alpha
        if den <= 0:
            raise CalibrationError(f"destination weights for source {i} sum to zero")
        for j in range(n):
            if j == i:
                continue
            w = (src[i] / total_src) * (dst[j] * (d_floor_km / dist[j])
                                        ** gravity_alpha) / den
            if w > 0:
                od[(i, j)] = w
    scale = sum(od.values())
    if abs(scale - 1.0) > 1e-9:
        od = {k: v / scale for k, v in od.items()}
    return od


def od_weights_uniform_destinations(sites, source_exponent=1.0):
    """Analytic OD weights for the simulator's demand.mode=uniform.

    Mirrors trace.py exactly: source i is drawn with weight
    W_i**source_exponent / sum(W**source_exponent); given source i the
    destination is drawn UNIFORMLY over the other endpoints, independent of
    weight (trace.py::_dst_choices, the "uniform"/"burst"/"diurnal" branch).
    Returns {(i, j): w} with sum == 1.
    """
    n = len(sites)
    if n < 2:
        raise CalibrationError("uniform demand needs at least two cells")
    src = [float(s["weight"]) ** source_exponent for s in sites]
    total = sum(src)
    if total <= 0:
        raise CalibrationError("source weights sum to zero")
    return {(i, j): (src[i] / total) / (n - 1)
            for i in range(n) for j in range(n) if i != j}


# --------------------------------------------------------------------------
# reference routing (fixed, reproducible, single path)
# --------------------------------------------------------------------------
def all_pairs_reference(topology, t, num_nodes):
    """Minimum-propagation-delay distances and a deterministic predecessor DAG.

    Tie-break is fixed and stated in the module docstring: the smallest
    satellite id among equally-good predecessors wins.  No ECMP splitting is
    performed, so a[e,k] is binary.
    """
    adj = {s: [] for s in range(num_nodes)}
    topo = topology.topology_at(t)
    for s, nb in topo.items():
        for _d, n in nb.items():
            adj[s].append((n, topology.range_km(s, n, t) / C_KM_S))
    for s in adj:
        adj[s].sort()
    dist = {}
    pred = {}
    for src in range(num_nodes):
        dd = {src: 0.0}
        pr = {src: None}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dd.get(u, float("inf")) + 1e-15:
                continue
            for v, w in adj[u]:
                nd = d + w
                if nd < dd.get(v, float("inf")) - 1e-15:
                    dd[v] = nd
                    pr[v] = u
                    heapq.heappush(pq, (nd, v))
                elif abs(nd - dd.get(v, float("inf"))) <= 1e-15:
                    if pr.get(v) is None or u < pr[v]:
                        pr[v] = u
        dist[src] = dd
        pred[src] = pr
    return dist, pred, topo


def path_edges(pred, src, dst):
    if src == dst:
        return []
    path = []
    cur = dst
    guard = 0
    while cur is not None and cur != src:
        path.append(cur)
        cur = pred[src].get(cur)
        guard += 1
        if guard > 10000:
            raise CalibrationError("reference path reconstruction did not terminate")
    if cur is None:
        return None
    path.append(src)
    path.reverse()
    return list(zip(path, path[1:]))


def first_alternative_s(adj, dist, src, dst, used_edges):
    """Length of the shortest s->t walk that avoids ALL edges of the reference
    path.  Banning the whole path (not one edge at a time) is what answers the
    question the study actually asks: if this ISL corridor is congested, does a
    route that does not touch ANY of it still exist, and how much longer is it?

    Returns None when banning the corridor disconnects the pair.  This is a
    LOWER bound on the detour a hop-by-hop policy could take; it is not an
    ECMP split and no traffic is assigned to it.
    """
    banned = set(used_edges)
    dd = {src: 0.0}
    pq = [(0.0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dd.get(u, float("inf")) + 1e-15:
            continue
        if u == dst:
            return d
        for v, w in adj[u]:
            if (u, v) in banned:
                continue
            nd = d + w
            if nd < dd.get(v, float("inf")) - 1e-15:
                dd[v] = nd
                heapq.heappush(pq, (nd, v))
    return None


# --------------------------------------------------------------------------
# control-plane load
# --------------------------------------------------------------------------
def control_edge_counts(topology, t, num_nodes, vis_k, dirs=("N", "S", "E", "W")):
    """Directed-edge relay counts for the real broadcast rule.

    Each satellite originates one advertisement per period; the kernel relays
    it once per reached satellite along a deterministic shortest-path broadcast
    tree of at most vis_k hops (routing.control_broadcast_children).  The
    count for directed edge e is therefore the number of origins whose tree
    contains e, and c_e = count_e * packet_bits / advertise_interval_s.
    """
    from leo_sim import routing as routing_mod
    topo = topology.topology_at(t)
    counts = {}
    for origin in range(num_nodes):
        children = routing_mod.control_broadcast_children(topo, origin, vis_k)
        for u, dirlist in children.items():
            for d in dirlist:
                e = (u, topo[u][d])
                counts[e] = counts.get(e, 0) + 1
    return counts


# --------------------------------------------------------------------------
# main calibration
# --------------------------------------------------------------------------
def calibrate(spec):
    tiers = tuple(float(x) for x in spec.get("tiers", DEFAULT_TIERS))
    horizon = float(spec["horizon_s"])
    window = float(spec["window_s"])
    if window <= 0 or horizon <= 0:
        raise CalibrationError("horizon_s and window_s must be positive")

    geo = spec["geometry"]
    provider = spec.get("provider", "constellation")
    if provider == "explicit":
        topology = ExplicitTopology(geo["num_nodes"], geo["links"],
                                    geo.get("elevation_deg"),
                                    geo.get("min_elevation_deg", 10.0))
    else:
        topology = ConstellationTopology(
            geo["num_satellites"], geo["num_planes"], geo["altitude_km"],
            geo["inclination_deg"], geo["min_elevation_deg"],
            geo["max_isl_km"], geo.get("geometry_epoch_s", 0.0),
            dynamic=geo.get("dynamic", True))
    num_nodes = topology.num_nodes

    packet_bits = int(spec["packet_bits"])
    ac = spec["access_layer"]
    cp = spec["control_plane"]
    links = spec["links"]
    rate_bps = float(links["rate_mbps"]) * 1e6

    # --- OD weights ------------------------------------------------------
    cells = spec["cells"]                      # {"id": {"lat":..,"lon":..,.}}
    if "od_weights" in spec:
        # keys are either "srcId,dstId" cell ids, or "i,j" indices into cells
        ids = [c["id"] for c in cells]
        od = {}
        for k, v in spec["od_weights"].items():
            a, b = k.split(",")
            a = ids[int(a)] if (a.lstrip("-").isdigit() and int(a) < len(ids)) else a
            b = ids[int(b)] if (b.lstrip("-").isdigit() and int(b) < len(ids)) else b
            od[(a, b)] = float(v)
    else:
        order = [c["id"] for c in cells]
        od = od_weights_from_gravity(
            cells,
            source_exponent=float(spec.get("source_population_exponent", 1.0)),
            destination_exponent=float(spec.get("destination_population_exponent", 1.0)),
            gravity_alpha=float(spec.get("gravity_alpha", 1.5)),
            d_floor_km=float(spec.get("gravity_d_floor_km", 100.0)))
        # remap index keys to cell ids for reporting
        od = {(order[i], order[j]): w for (i, j), w in od.items()}
    od_sum = sum(od.values())
    if abs(od_sum - 1.0) > 1e-6:
        raise CalibrationError(f"OD weights must sum to 1, got {od_sum!r}")

    # --- per window ------------------------------------------------------
    n_win = int(round(horizon / window))
    if abs(n_win * window - horizon) > 1e-9:
        raise CalibrationError("horizon_s must be an integer multiple of window_s")
    b_acc = {}
    c_acc = {}
    cap_acc = {}
    win_rows = []
    od_report = {}
    alt_acc = {}
    dec_node = {}
    zero_hop_w = 0.0
    unroutable_w = 0.0
    hops_w = 0.0
    routed_w = 0.0
    stretch_w = 0.0
    for wi in range(n_win):
        t = wi * window
        dist, pred, topo = all_pairs_reference(topology, t, num_nodes)
        edges = [(u, v) for u, nb in topo.items() for v in nb.values()]
        counts = control_edge_counts(topology, t, num_nodes, int(cp["vis_k"]))
        vis = {}
        for c in cells:
            vis[c["id"]] = topology.visible(c, t)
        ingress = {cid: (max(v)[1] if v else None) for cid, v in vis.items()}
        egress = {cid: sorted(s for _e, s in v) for cid, v in vis.items()}
        pair_w = {}
        for (a_id, b_id), w in od.items():
            a = ingress.get(a_id)
            if a is None or not egress.get(b_id):
                unroutable_w += w
                continue
            dd = dist[a]
            best = None
            for b in egress[b_id]:
                if b in dd and (best is None
                                or dd[b] < dd[best] - 1e-15
                                or (abs(dd[b] - dd[best]) <= 1e-15 and b < best)):
                    best = b
            if best is None:
                unroutable_w += w
                continue
            if best == a:
                zero_hop_w += w
                continue
            pair_w[(a, best)] = pair_w.get((a, best), 0.0) + w
        adj_w = {s: [(n, topology.range_km(s, n, t) / C_KM_S) for n in nb.values()]
                 for s, nb in topo.items()}
        for s in adj_w:
            adj_w[s].sort()
        for (a, b), w in pair_w.items():
            path = path_edges(pred, a, b)
            if path is None:
                unroutable_w += w
                continue
            for e in path:
                b_acc[e] = b_acc.get(e, 0.0) + w
            hops_w += w * len(path)
            routed_w += w
            stretch_w += w * (dist[a][b] * C_KM_S)
            alt = first_alternative_s(adj_w, dist, a, b, path) if len(path) > 1 else None
            rec = alt_acc.setdefault((a, b), dict(weight_s=0.0, ref_s=dist[a][b],
                                                  alt_windows=0, alt_sum_s=0.0))
            rec["weight_s"] += w
            if alt is not None:
                rec["alt_windows"] += 1
                rec["alt_sum_s"] += alt
            # one forwarding decision per path EDGE, charged to its sender:
            # the ingress decides the first hop; the egress does not forward.
            for node, _peer in path:
                dec_node[node] = dec_node.get(node, 0.0) + w
            od_report.setdefault((a, b), {"weight_s": 0.0, "hops": len(path),
                                          "prop_s": dist[a][b]})
            od_report[(a, b)]["weight_s"] += w
        edge_set = set(edges)
        for e in edge_set:
            c_acc[e] = (c_acc.get(e, 0.0)
                        + counts.get(e, 0) * int(cp["packet_bits"])
                        / float(cp["interval_s"]) * window)
            cap_acc[e] = cap_acc.get(e, 0.0) + rate_bps * window
        win_rows.append(dict(window=wi, start=t, end=t + window,
                             edges=len(edge_set), od_pairs=len(pair_w)))

    n_win_f = float(n_win)
    b_e = {e: v / n_win_f for e, v in b_acc.items()}
    c_e = {e: v / n_win_f for e, v in c_acc.items()}
    cap_e = {e: v / n_win_f for e, v in cap_acc.items()}

    # --- tiers -----------------------------------------------------------
    tier_rows = []
    for rho in tiers:
        best = None
        infeasible = []
        for e, b in b_e.items():
            if b <= 0:
                continue
            num = rho * cap_e[e] - c_e.get(e, 0.0)
            if num <= 0:
                infeasible.append({"edge": list(e),
                                   "control_util": (c_e.get(e, 0.0) / cap_e[e]
                                                    if cap_e[e] else None)})
                continue
            val = num / b
            if best is None or val < best[0]:
                best = (val, e)
        row = dict(target_rho=rho,
                   feasible=not infeasible and best is not None,
                   infeasible_links=infeasible[:10],
                   infeasible_link_count=len(infeasible))
        if best is not None:
            R = best[0]
            row.update(
                R_bit_s=R,
                R_mbps=R / 1e6,
                bottleneck_edge=list(best[1]),
                bottleneck_b_e=b_e[best[1]],
                bottleneck_c_e_bit_s=c_e.get(best[1], 0.0),
                bottleneck_capacity_bit_s=cap_e[best[1]],
                bottleneck_control_util=(c_e.get(best[1], 0.0) / cap_e[best[1]]),
                packet_rate_network=R / packet_bits,
                packet_rate_bottleneck=R * b_e[best[1]] / packet_bits)
        tier_rows.append(row)

    # --- cross-checks ----------------------------------------------------
    serialization_s = packet_bits / rate_bps
    max_control_e = max(c_e.values()) if c_e else 0.0
    checks = dict(
        serialization_s=serialization_s,
        per_hop_propagation_s=(spec.get("representative_hop_km", 5488.4) / C_KM_S),
        serialization_over_propagation=(serialization_s
                                        / (spec.get("representative_hop_km", 5488.4) / C_KM_S)),
        max_control_bit_s=max_control_e,
        max_control_util=(max_control_e / rate_bps),
        buffer=dict(),
        access=dict(),
        compute=dict(),
    )
    for tau in spec.get("buffer_taus_s", [0.005, 0.02, 0.1]):
        bits = rate_bps * float(tau)
        cap_bits = float(links.get("queue_bits", bits))
        checks["buffer"][str(tau)] = dict(
            bits=bits, bytes=bits / 8.0,
            packets=bits / packet_bits,
            drain_time_s=bits / rate_bps,
            fits_declared_queue_bits=(bits <= cap_bits) if "queue_bits" in links else None)

    # ---- access, PER GATEWAY (not a uniform split by gateway count)
    src_share = {c["id"]: 0.0 for c in cells}
    dst_share = {c["id"]: 0.0 for c in cells}
    for (a_id, b_id), w in od.items():
        src_share[a_id] = src_share.get(a_id, 0.0) + w
        dst_share[b_id] = dst_share.get(b_id, 0.0) + w
    up_bps = float(ac["uplink_mbps"]) * 1e6
    down_bps = float(ac["downlink_mbps"]) * 1e6
    checks["access"] = dict(
        per_cell_source_share={k: round(v, 6) for k, v in src_share.items()},
        per_cell_destination_share={k: round(v, 6) for k, v in dst_share.items()},
        uplink_mbps=ac["uplink_mbps"], downlink_mbps=ac["downlink_mbps"],
        tiers=[])
    for row in tier_rows:
        if not row.get("feasible"):
            continue
        R = row["R_bit_s"]
        worst_up = max(src_share.values()) * R
        worst_down = max(dst_share.values()) * R
        checks["access"]["tiers"].append(dict(
            target_rho=row["target_rho"], R_bit_s=R,
            worst_gateway_uplink_bit_s=worst_up,
            worst_gateway_downlink_bit_s=worst_down,
            worst_uplink_util=worst_up / up_bps,
            worst_downlink_util=worst_down / down_bps,
            uplink_binding_cell=max(src_share, key=src_share.get),
            downlink_binding_cell=max(dst_share, key=dst_share.get)))

    # ---- compute, PER SATELLITE (the hotspot, not the 96-satellite average)
    mean_hops = (hops_w / routed_w) if routed_w > 0 else 0.0
    servers = int(spec.get("compute_servers_per_satellite", 1))
    service_s = float(spec.get("compute_delay_s", 0.001))
    checks["compute"] = dict(
        nodes_with_decisions=len(dec_node),
        mean_hops=mean_hops, servers=servers, service_s=service_s,
        service_capacity_per_satellite=(servers / service_s if service_s else None),
        tiers=[])
    for row in tier_rows:
        if not row.get("feasible"):
            continue
        pkt_s = row["packet_rate_network"]
        # dec_node accumulates per window: normalise exactly like b_e
        lam = {v: pkt_s * (share / n_win_f) for v, share in dec_node.items()}
        hot_v = max(lam, key=lam.get) if lam else None
        hot = lam[hot_v] if hot_v is not None else 0.0
        checks["compute"]["tiers"].append(dict(
            target_rho=row["target_rho"], network_packets_s=pkt_s,
            mean_lambda_all_satellites=(pkt_s * sum(dec_node.values()) / n_win_f
                                    / num_nodes),
            max_lambda_per_satellite=hot,
            hotspot_satellite=hot_v,
            rho_cpu_hotspot=(hot * service_s / servers) if (servers and service_s) else None))
    checks["compute"]["max_lambda_per_satellite"] = max(
        (t["max_lambda_per_satellite"] for t in checks["compute"]["tiers"]),
        default=0.0)

    result = dict(
        schema=SCHEMA,
        spec_digest=hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        definitions=dict(
            R_semantics="network-wide total injection rate (bit/s); equals "
                         "demand.offered_mbps * 1e6 in the simulator",
            reference_routing="single minimum-propagation-delay path per OD; "
                              "predecessor tie-break = smallest satellite id; "
                              "no equal-cost multipath splitting",
            capacity="per-window effective capacity = rate * window; a link "
                     "present for only part of a window contributes only that part",
            criterion="R(rho) = min over (window, directed edge with b_e>0) of "
                      "(rho*C_e - c_e)/b_e"),
        inputs=dict(packet_bits=packet_bits, window_s=window, horizon_s=horizon,
                    windows=n_win, rate_mbps=links["rate_mbps"],
                    num_nodes=num_nodes, control=dict(cp),
                    cells=len(cells), od_pairs=len(od)),
        od_sum=od_sum,
        alternatives=dict(
            routed_pairs=len(alt_acc),
            pairs_with_no_alternative=sum(1 for r in alt_acc.values()
                                          if r["alt_windows"] == 0),
            median_alternative_stretch=(
                sorted((r["alt_sum_s"] / r["alt_windows"]) / r["ref_s"]
                       for r in alt_acc.values() if r["alt_windows"])
                [len([r for r in alt_acc.values() if r["alt_windows"]]) // 2]
                if any(r["alt_windows"] for r in alt_acc.values()) else None),
            per_pair={f"{a}->{b}": dict(
                reference_s=r["ref_s"],
                alternative_s=(r["alt_sum_s"] / r["alt_windows"]
                               if r["alt_windows"] else None),
                stretch=((r["alt_sum_s"] / r["alt_windows"]) / r["ref_s"]
                         if r["alt_windows"] else None),
                windows_with_alternative=r["alt_windows"], windows=n_win)
                for (a, b), r in sorted(alt_acc.items())}),
        traffic_split=dict(zero_hop_weight=zero_hop_w / n_win_f,
                           unroutable_weight=unroutable_w / n_win_f,
                           routed_weight=routed_w / n_win_f,
                           mean_isl_hops=(hops_w / routed_w) if routed_w else 0.0),
        link_coefficients=dict(
            directed_edges=len(b_e),
            sum_b_e=sum(b_e.values()),
            max_b_e=max(b_e.values()) if b_e else 0.0,
            max_b_e_edge=list(max(b_e, key=b_e.get)) if b_e else None,
            max_c_e_bit_s=max_control_e),
        tiers=tier_rows,
        checks=checks,
        windows=win_rows,
    )
    result["_b_e"] = b_e
    result["_c_e"] = c_e
    result["_cap_e"] = cap_e
    result["_od_paths"] = od_report
    return result


def write_outputs(result, out_dir, spec):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    (out / "calibration.json").write_text(
        json.dumps(public, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    with (out / "link-coefficients.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["src_sat", "dst_sat", "b_e", "c_e_bit_s", "capacity_bit_s",
                     "control_util"] + [f"rho_at_R_{t}" for t in
                                        [r["target_rho"] for r in result["tiers"]]])
        rates = {r["target_rho"]: r.get("R_bit_s") for r in result["tiers"]}
        for e in sorted(result["_b_e"]):
            b = result["_b_e"][e]
            c = result["_c_e"].get(e, 0.0)
            cap = result["_cap_e"][e]
            row = [e[0], e[1], repr(b), repr(c), repr(cap), repr(c / cap if cap else 0.0)]
            for t in [r["target_rho"] for r in result["tiers"]]:
                R = rates.get(t)
                row.append(repr((R * b + c) / cap if (R and cap) else ""))
            wr.writerow(row)
    with (out / "od-table.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["ingress_sat", "egress_sat", "od_weight", "isl_hops", "propagation_s"])
        for (a, b), rec in sorted(result["_od_paths"].items(),
                                  key=lambda kv: -kv[1]["weight_s"]):
            wr.writerow([a, b, repr(rec["weight_s"] / result["inputs"]["windows"]),
                         rec["hops"], repr(rec["prop_s"])])
    return out


# --------------------------------------------------------------------------
# self test: a hand-checked example, verified against closed-form arithmetic
# --------------------------------------------------------------------------
SELFTEST_SPEC = {
    "provider": "explicit",
    "geometry": {
        "num_nodes": 4,
        "links": {"0,1": 2000.0, "1,0": 2000.0,
                  "1,2": 2000.0, "2,1": 2000.0,
                  "2,3": 2000.0, "3,2": 2000.0},
        "elevation_deg": {"a": [[90.0, 0]], "b": [[90.0, 3]]},
        "min_elevation_deg": 10.0,
    },
    "cells": [{"id": "a", "lat": 0.0, "lon": 0.0, "weight": 1.0},
              {"id": "b", "lat": 0.0, "lon": 0.0, "weight": 1.0}],
    "od_weights": {"a,b": 1.0},
    "packet_bits": 12000,
    "links": {"rate_mbps": 1000.0},
    "control_plane": {"packet_bits": 8000, "interval_s": 0.05, "vis_k": 1},
    "access_layer": {"uplink_mbps": 10000.0, "downlink_mbps": 10000.0, "gateways": 1},
    "compute_delay_s": 0.001,
    "compute_servers_per_satellite": 128,
    "buffer_taus_s": [0.005, 0.02, 0.1],
    "horizon_s": 2.0,
    "window_s": 1.0,
    "representative_hop_km": 2000.0,
}


def selftest():
    """Closed-form verification of every arithmetic step, no geometry module."""
    res = calibrate(SELFTEST_SPEC)
    prop = 2000.0 / C_KM_S                       # 6.6713e-3 s
    c_expect = 8000 / 0.05                       # 160000 bit/s on every edge
    cap = 1e9
    failures = []

    def check(name, got, want, tol=1e-9, rel=True):
        ok = (abs(got - want) <= tol * max(1.0, abs(want)) if rel
              else abs(got - want) <= tol)
        if not ok:
            failures.append(f"{name}: got {got!r}, want {want!r}")

    # path 0->3 = 0-1-2-3, so b_e == 1 on exactly those three directed edges
    b = res["_b_e"]
    check("b(0,1)", b.get((0, 1), 0.0), 1.0)
    check("b(1,2)", b.get((1, 2), 0.0), 1.0)
    check("b(2,3)", b.get((2, 3), 0.0), 1.0)
    check("b(1,0)", b.get((1, 0), 0.0), 0.0)
    check("sum_b_e", res["link_coefficients"]["sum_b_e"], 3.0)
    check("mean hops", res["traffic_split"]["mean_isl_hops"], 3.0)
    check("zero hop weight", res["traffic_split"]["zero_hop_weight"], 0.0)

    # control: vis_k=1 -> each origin's tree is its own out-edges, count_e == 1
    for e, val in res["_c_e"].items():
        check(f"c{e}", val, c_expect)

    # R(rho) = rho*1e9 - 1.6e5 on every b=1 edge
    for row in res["tiers"]:
        rho = row["target_rho"]
        check(f"R({rho})", row["R_bit_s"], rho * cap - c_expect)
        check(f"bottleneck_b({rho})", row["bottleneck_b_e"], 1.0)

    # serialization and buffer
    check("serialization_s", res["checks"]["serialization_s"], 12000 / 1e9, 1e-12, False)
    check("per-hop propagation_s", res["checks"]["per_hop_propagation_s"], prop, 1e-12, False)
    check("buffer 0.02 bits", res["checks"]["buffer"]["0.02"]["bits"], 2e7)
    check("buffer 0.02 packets", res["checks"]["buffer"]["0.02"]["packets"], 2e7 / 12000)
    check("buffer 0.005 bits", res["checks"]["buffer"]["0.005"]["bits"], 5e6)
    check("buffer 0.1 bits", res["checks"]["buffer"]["0.1"]["bits"], 1e8)

    # dimension check on rho_e at R(0.5) on the bottleneck: must be exactly 0.5
    R = next(r for r in res["tiers"] if r["target_rho"] == 0.5)["R_bit_s"]
    rho_bottleneck = (R * 1.0 + c_expect) / cap
    check("rho at R(0.5)", rho_bottleneck, 0.5, 1e-12, False)

    # a link that carries reference data and whose control load alone exceeds
    # the target must be reported INFEASIBLE, never clamped
    bad = json.loads(json.dumps(SELFTEST_SPEC))
    bad["control_plane"] = {"packet_bits": 8000, "interval_s": 1e-6, "vis_k": 2}
    bad["tiers"] = [0.20]
    res_bad = calibrate(bad)
    if res_bad["tiers"][0]["feasible"]:
        failures.append("infeasible tier was reported feasible (clamped)")
    if not res_bad["tiers"][0]["infeasible_links"]:
        failures.append("infeasible tier did not name the offending link")

    # non-normalised OD weights must be refused
    bad2 = json.loads(json.dumps(SELFTEST_SPEC))
    bad2["od_weights"] = {"a,b": 0.5}
    try:
        calibrate(bad2)
        failures.append("unnormalised OD weights were accepted")
    except CalibrationError:
        pass

    print(json.dumps(dict(checks_passed=not failures, failures=failures,
                          R_tiers=[{"rho": r["target_rho"], "R_mbps": r["R_mbps"]}
                                   for r in res["tiers"]],
                          c_e_bit_s=c_expect, serialization_s=12000 / 1e9),
                     indent=1))
    return 0 if not failures else 1


def spec_from_profile(profile_path, repo_root=None, **overrides):
    """Build a calibration spec FROM the profile the runs actually use.

    The 2026-10-18 review found the static numbers and the executed profile had
    drifted apart: the spec carried 128 compute servers at 1 ms while the
    profile carried 1 at 50 us.  Deriving the spec from the profile removes that
    whole class of error -- geometry, links, control, access and compute have
    exactly one source of truth.
    """
    import yaml
    prof = yaml.safe_load(Path(profile_path).read_text(encoding="utf-8"))
    sc, ep, dm = prof["scenario"], prof["endpoints"], prof["demand"]
    lk, cp, ac = prof["links"], prof["control_plane"], prof["access"]
    ex = prof["execution"]
    sites = ep.get("sites") or []
    if not sites:
        raise CalibrationError(
            "spec_from_profile needs endpoints.sites; expand a "
            "population_gravity profile to its resolved cell list first")
    cells = [{"id": s["name"], "lat": float(s["lat"]), "lon": float(s["lon"]),
              "weight": float(s.get("demand_weight", 1.0))} for s in sites]
    spec = dict(
        provider="constellation",
        geometry=dict(num_satellites=int(sc["num_satellites"]),
                      num_planes=int(sc["num_planes"]),
                      altitude_km=float(sc["altitude_km"]),
                      inclination_deg=float(sc["inclination_deg"]),
                      min_elevation_deg=float(sc["min_elevation_deg"]),
                      max_isl_km=float(lk["max_isl_km"]),
                      geometry_epoch_s=float(sc.get("geometry_epoch_s", 0.0)),
                      dynamic=True),
        cells=cells,
        packet_bits=int(dm["packet_bits"]),
        links={"rate_mbps": float(lk["isl_rate_mbps"]),
               "queue_bits": float(lk["isl_queue_bits"])},
        control_plane={"packet_bits": int(cp["packet_bits"]),
                       "interval_s": float(cp["advertise_interval_s"]),
                       "vis_k": int(cp["vis_k"])},
        access_layer={"uplink_mbps": float(ac["uplink_rate_mbps"]),
                      "downlink_mbps": float(ac["downlink_rate_mbps"]),
                      "gateways": len(cells)},
        compute_delay_s=float(ex["compute_delay_s"]),
        compute_servers_per_satellite=int(ex["compute_servers_per_satellite"]),
        buffer_taus_s=[0.005, 0.02, 0.1],
        horizon_s=float(sc["duration_s"]),
        window_s=float(prof["topology"]["recompute_interval_s"] or 1.0),
        representative_hop_km=5488.4,
    )
    if dm.get("mode") in ("uniform", "burst", "diurnal"):
        pairs = od_weights_uniform_destinations(cells)
        spec["od_weights"] = {cells[i]["id"] + "," + cells[j]["id"]: w
                              for (i, j), w in pairs.items()}
    else:
        spec.update(
            source_population_exponent=float(dm["source_population_exponent"]),
            destination_population_exponent=float(
                dm["destination_population_exponent"]),
            gravity_alpha=float(dm["gravity_alpha"]),
            gravity_d_floor_km=float(dm["gravity_d_floor_km"]))
    spec.update(overrides)
    return spec

# --------------------------------------------------------------------------
# OD-scope comparison: the evidence that the OD definition, not the link rate,
# decides whether the ISL mesh is used at all.
# --------------------------------------------------------------------------
SCOPE_BASE = dict(
    provider="constellation",
    geometry=dict(num_satellites=96, num_planes=12, altitude_km=800.0,
                  inclination_deg=53.0, min_elevation_deg=10.0, max_isl_km=6000.0,
                  geometry_epoch_s=0.0, dynamic=True),
    packet_bits=12000,
    links={"rate_mbps": 1000.0, "queue_bits": 20000000},
    control_plane={"packet_bits": 8000, "interval_s": 0.05, "vis_k": 2},
    access_layer={"uplink_mbps": 10000.0, "downlink_mbps": 10000.0, "gateways": 4},
    compute_delay_s=0.00005, compute_servers_per_satellite=1,
    buffer_taus_s=[0.005, 0.02, 0.1],
    horizon_s=12.0, window_s=1.0, representative_hop_km=5488.4)

#: Declared gateway coordinates.  Every scenario below uses the same geometry,
#: the same links and the same control plane; only the OD definition changes.
SCOPE_GATEWAYS = {
    "beijing": (39.90, 116.40), "shanghai": (31.20, 121.50), "tokyo": (35.70, 139.70),
    "london": (51.50, -0.13), "frankfurt": (50.10, 8.68), "newyork": (40.70, -74.00),
    "sydney": (-33.87, 151.20), "mumbai": (19.10, 72.90),
    "singapore": (1.35, 103.80), "saopaulo": (-23.55, -46.63),
}


def _uniform_od(cells, weights):
    tot = sum(weights[c["id"]] for c in cells)
    n = len(cells)
    return {f"{s['id']},{d['id']}": (weights[s["id"]] / tot) / (n - 1)
            for s in cells for d in cells if d["id"] != s["id"]}


def scope_specs(population_path=None, repo_root=None):
    """The five OD scopes compared in review §11.3, all other settings equal."""
    spec = {}
    g = SCOPE_GATEWAYS

    def cells(ids, weights=None):
        weights = weights or {i: 1.0 for i in ids}
        return [{"id": i, "lat": g[i][0], "lon": g[i][1], "weight": weights[i]}
                for i in ids]

    spec["B_global10_uniform"] = dict(
        SCOPE_BASE, cells=cells(list(g)), od_weights=_uniform_od(cells(list(g)), {i: 1.0 for i in g}))
    spec["C_3gw_uniform"] = dict(
        SCOPE_BASE, cells=cells(["beijing", "london", "newyork"]),
        od_weights=_uniform_od(cells(["beijing", "london", "newyork"]),
                               {i: 1.0 for i in ("beijing", "london", "newyork")}))
    spec["D_2gw_equal"] = dict(
        SCOPE_BASE, cells=cells(["beijing", "london"]),
        od_weights=_uniform_od(cells(["beijing", "london"]),
                               {"beijing": 1.0, "london": 1.0}))
    sel = cells(["beijing", "london", "newyork"],
                {"beijing": 4.0, "london": 1.0, "newyork": 1.0})
    spec["E_trunk_v1_selected"] = dict(
        SCOPE_BASE, cells=sel,
        od_weights=_uniform_od(sel, {"beijing": 4.0, "london": 1.0, "newyork": 1.0}))
    if population_path is not None:
        from leo_sim import population as population_mod
        table = population_mod.load_population_regions(
            population_path, 1.0, lat_bounds_deg=[20.0, 30.0],
            lon_bounds_deg=[100.0, 110.0], source_root=repo_root)
        spec["A_existing_region_population_gravity"] = dict(
            SCOPE_BASE,
            cells=[{"id": f"r{i}", "lat": r.lat, "lon": r.lon, "weight": r.population}
                   for i, r in enumerate(table.regions)],
            source_population_exponent=1.0, destination_population_exponent=3.0,
            gravity_alpha=0.5, gravity_d_floor_km=100.0)
    return spec


def scope_comparison(population_path=None, repo_root=None):
    rows = []
    for name, spec in scope_specs(population_path, repo_root).items():
        res = calibrate(spec)
        lc = res["link_coefficients"]
        ts = res["traffic_split"]
        R = {t["target_rho"]: t["R_mbps"] for t in res["tiers"]}
        rows.append(dict(
            scenario=name, cells=res["inputs"]["cells"],
            od_pairs=res["inputs"]["od_pairs"],
            zero_hop_weight=round(ts["zero_hop_weight"], 4),
            unroutable_weight=round(ts["unroutable_weight"], 4),
            mean_isl_hops=round(ts["mean_isl_hops"], 2),
            max_b_e=round(lc["max_b_e"], 4),
            service_edges=lc["directed_edges"],
            max_c_e_bit_s=lc["max_c_e_bit_s"],
            R020_mbps=round(R[0.2], 1), R050_mbps=round(R[0.5], 1),
            R095_mbps=round(R[0.95], 1),
            network_packets_s_at_095=round(R[0.95] * 1e6 / spec["packet_bits"]),
            median_alternative_stretch=(
                round(res["alternatives"]["median_alternative_stretch"], 3)
                if res["alternatives"]["median_alternative_stretch"] is not None else None),
            pairs_without_alternative=res["alternatives"]["pairs_with_no_alternative"]))
    return sorted(rows, key=lambda r: r["max_b_e"])


# --------------------------------------------------------------------------
def _load_spec(path):
    text = Path(path).read_text(encoding="utf-8")
    if path.endswith(".json"):
        return json.loads(text)
    raise CalibrationError("spec must be JSON")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("calibrate")
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    sub.add_parser("selftest")
    sc = sub.add_parser("scope-comparison")
    sc.add_argument("--population", default=None,
                    help="population raster path; omit to skip the regional scope")
    sc.add_argument("--repo-root", default=None)
    sc.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        return selftest()
    if args.cmd == "scope-comparison":
        rows = scope_comparison(args.population,
                                Path(args.repo_root) if args.repo_root else REPO_ROOT)
        text = json.dumps(rows, indent=1) + chr(10)
        if args.out:
            Path(args.out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.out).write_text(text, encoding="utf-8")
        print(text)
        return 0
    spec = _load_spec(args.spec)
    result = calibrate(spec)
    out = write_outputs(result, args.out, spec)
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    print(json.dumps(dict(out=str(out),
                          traffic_split=public["traffic_split"],
                          link_coefficients=public["link_coefficients"],
                          tiers=[{kk: vv for kk, vv in t.items()
                                  if kk in ("target_rho", "feasible", "R_mbps",
                                            "bottleneck_edge", "bottleneck_b_e",
                                            "infeasible_link_count")}
                                 for t in public["tiers"]]), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
