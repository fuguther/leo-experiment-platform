"""Routing policies for leo_sim.

Information boundary (binding):
- The deliver action uses only the current satellite's own direct, current
  visibility of the destination endpoint.
- hop/delay/capacity discover "who can see the destination cell" ONLY from
  the satellite's own local control cache (actually arrived, non-expired
  advertisements).
- Static constellation topology is a-priori knowledge; it is DIRECTED.
  Reachability and next-hop costs are computed over true directed edges only
  (reverse adjacency from the targets), never over silently mirrored links.
  Physical bidirectionality is verified once at topology construction.
- capacity additionally uses advertised queue state from the cache; the first
  hop uses the satellite's own directly observed queues.
- delay/capacity use directly observed propagation for the first hop and only
  arrived, non-expired advertised propagation for remote edges.  They never
  query global current geometry for a remote link.
- oracle is explicitly labeled an ANALYSIS UPPER BOUND: it may use global
  current knowledge (and may decide to wait). It never feeds learning.

The ``info_queue`` and ``info_physical`` policies are diagnostic anchors for
the information ladder. They use the same arrived destination advertisements
as ``hop`` but differ only in first-hop information: ``info_queue`` adds the
current satellite's own egress queue, while ``info_physical`` additionally
uses current first-hop distance, availability and derived rate. Neither reads
remote current queues or future geometry.

No policy reads future ephemeris or hidden global queues.
"""
from __future__ import annotations

import heapq
from collections import deque

ORACLE_LABEL = "analysis_upper_bound"


def control_broadcast_children(topo, origin: int, max_hops: int) -> dict[int, list[str]]:
    """Build one deterministic shortest-path broadcast tree.

    Every reached satellite has exactly one parent, so one snapshot creates at
    most one real control-packet transmission per reached satellite.  This
    retains hop-by-hop propagation delay, queueing, bandwidth consumption and
    loss/expiry while avoiding exponential duplicate flooding on constellations
    with rings and cross-plane links.
    """
    if origin not in topo:
        raise ValueError(f"control origin {origin} absent from topology")
    if isinstance(max_hops, bool) or not isinstance(max_hops, int) or max_hops < 0:
        raise ValueError("control max_hops must be a non-negative integer")
    children: dict[int, list[str]] = {s: [] for s in topo}
    depth = {origin: 0}
    queue = deque([origin])
    while queue:
        node = queue.popleft()
        if depth[node] >= max_hops:
            continue
        # Peer id is the stable tie-breaker; direction breaks malformed
        # parallel-edge ties deterministically without inventing an edge.
        edges = sorted(topo.get(node, {}).items(), key=lambda item: (item[1], item[0]))
        for direction, peer in edges:
            if peer in depth:
                continue
            depth[peer] = depth[node] + 1
            children[node].append(direction)
            queue.append(peer)
    return children


def build_topology(geometry, num_sats: int, dirs, t: float | None = None) \
        -> dict[int, dict[str, int]]:
    """A-priori neighbor graph; self-links are excluded.

    t=None uses the geometry's static neighbor rules (exact current V2
    behavior).  t given uses dynamic neighbors_at (e.g. legacy Markovian
    cross-plane rematching at a recompute boundary).

    Physical ISLs are bidirectional, and that contract is VERIFIED here (fail
    closed): if a geometry provider hands us a one-way edge we refuse to build
    the topology rather than letting routing silently fabricate the reverse
    edge."""
    topo: dict[int, dict[str, int]] = {}
    for s in range(num_sats):
        nb = geometry.neighbors(s, dirs) if t is None \
            else geometry.neighbors_at(s, dirs, t)
        topo[s] = {d: n for d, n in nb.items() if n != s}
    for s, nb in topo.items():
        for d, n in nb.items():
            if s not in topo.get(n, {}).values():
                raise ValueError(
                    f"ISL topology not bidirectional: {s}-{d}->{n} has no "
                    "reverse edge; refusing to fabricate one in routing")
    return topo


def _reverse_adj(topo) -> dict[int, set[int]]:
    """node -> its in-neighbours under the TRUE directed edges (no fabricated
    reverse links): x in radj[y] iff topo[x] contains an edge to y."""
    radj: dict[int, set[int]] = {s: set() for s in topo}
    for s, nb in topo.items():
        for n in nb.values():
            radj.setdefault(n, set()).add(s)
    return radj


def _multi_source_dist(adj, sources, edge_cost,
                       sorted_adj: dict | None = None) -> dict[int, float]:
    dist = {s: float("inf") for s in adj}
    pq = []
    for s in sources:
        if s in dist:
            dist[s] = 0.0
            heapq.heappush(pq, (0.0, s))
    if sorted_adj is None:
        sorted_adj = {u: sorted(adj.get(u, ())) for u in adj}
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]:
            continue
        for v in sorted_adj.get(u, ()):
            w = edge_cost(u, v)
            if w == float("inf"):
                continue
            nd = d + w
            if nd < dist[v] - 1e-15:
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist


def _multi_source_bfs(adj, sources) -> dict[int, int]:
    """Multi-source BFS over unit-cost edges (hop policy).

    hop policy uses a constant edge cost of 1.0, so Dijkstra and BFS produce
    identical minimum hop counts; BFS avoids the heap and the float tolerance
    entirely.  Reachable nodes map to their nearest-source hop count; missing
    nodes are treated as unreachable (inf) by the caller."""
    dist: dict[int, int] = {}
    frontier = deque()
    for s in sources:
        if s in adj and s not in dist:
            dist[s] = 0
            frontier.append(s)
    while frontier:
        u = frontier.popleft()
        for v in adj.get(u, ()):
            if v not in dist:
                dist[v] = dist[u] + 1
                frontier.append(v)
    return dist


def destinations_in_cache(cache, dst_cell: str, now: float,
                          max_cache_hops: int | None = None) -> list[int]:
    """Origins whose valid, actually-arrived advertisement reports CURRENT
    service capability (serve_cells) for dst_cell — visibility alone does
    not make a satellite a legal egress."""
    out = []
    for origin, entry in cache.valid_entries(now).items():
        if max_cache_hops is not None and entry.hops > max_cache_hops:
            continue
        if dst_cell in entry.payload.get("serve_cells", ()):
            out.append(origin)
    return sorted(out)


def choose_next_hop(policy: str, sat: int, dst_cell: str, now: float,
                    geometry, topo, cache, own_queue_bits: dict,
                    isl_rate_bps: float, prop_delay,
                    oracle_targets: list[int] | None = None,
                    best_only: bool = False,
                    reverse_adj: dict | None = None,
                    sorted_adj: dict | None = None,
                    rate_from_propagation=None,
                    cache_hops: int | None = None) -> tuple[list[str], str]:
    """Return (ordered candidate directions, status).

    status: "ok" (candidates non-empty), "no_info" (no destination
    advertisement available), "unreachable" (advertised but no path).
    Only satellites advertising CURRENT service capability for dst_cell
    (serve_cells) count as legal egress; mere visibility is not enough.
    """
    if policy not in (
            "hop", "delay", "capacity", "oracle",
            "info_queue", "info_physical"):
        raise ValueError(f"unknown routing policy {policy!r}")

    if policy == "oracle":
        # analysis upper bound: caller passes the true current serving sats.
        targets = list(oracle_targets or [])
    else:
        targets = destinations_in_cache(
            cache, dst_cell, now, max_cache_hops=cache_hops)
    targets = [t for t in targets if t != sat]
    if not targets:
        return [], "no_info"

    # reachability and path cost follow the TRUE directed edges only: the
    # multi-source search expands backward from the targets over the reverse
    # adjacency, so dist[x] is the cost of a real directed path x -> target.
    # edge_cost(u, v) below is evaluated with u = popped node, v = predecessor
    # being relaxed, i.e. the forward edge v -> u. The reverse adjacency and
    # its sorted neighbour lists are static (topo never changes) and are
    # precomputed by the kernel; tests may pass None to rebuild on demand.
    if reverse_adj is None:
        reverse_adj = _reverse_adj(topo)
    adj = reverse_adj
    if sorted_adj is None:
        sorted_adj = {u: sorted(adj.get(u, ())) for u in adj}

    def observed_propagation(a, b):
        """Propagation metric available at `sat` for forward edge a -> b."""
        if a == sat:
            # A satellite directly observes its own incident link now.
            return prop_delay(geometry.isl_range_km(a, b, now))
        entry = cache.entry(a)
        if entry is None or not entry.valid_at(now) \
                or (cache_hops is not None and entry.hops > cache_hops):
            return None
        direction = _dir_of(topo, a, b)
        if direction is None:
            return None
        rec = entry.payload.get("isl_propagation_s", {}).get(direction)
        # the advertised metric is valid only for the peer it was measured
        # on; after a rematch the direction may point at a different peer
        if not isinstance(rec, dict) or rec.get("peer") != b:
            return None
        value = rec.get("value")
        if not isinstance(value, (int, float)) or value < 0:
            return None
        return float(value)

    if policy in ("hop", "info_queue", "info_physical"):
        # unit-cost policy: multi-source BFS over the reverse adjacency is
        # exactly Dijkstra with weight 1.0, with no heap/tolerance overhead
        bfs_dist = _multi_source_bfs(adj, targets)
        dist = {s: float("inf") for s in adj}
        dist.update({s: float(d) for s, d in bfs_dist.items()})
    elif policy == "oracle":
        # The explicitly labeled oracle uses perfect global current knowledge.
        def fwd_cost(a, b):
            return prop_delay(geometry.isl_range_km(a, b, now))
    elif policy == "delay":
        def fwd_cost(a, b):
            value = observed_propagation(a, b)
            return float("inf") if value is None else value
    else:  # capacity
        def fwd_cost(a, b):
            c = observed_propagation(a, b)
            if c is None:
                return float("inf")
            if a == sat:
                q = own_queue_bits  # directly observed local queues
                dir_ab = _dir_of(topo, a, b)
                qb = q.get(dir_ab, 0) if dir_ab else None
            else:
                entry = cache.entry(a)
                if entry is None or not entry.valid_at(now) \
                        or (cache_hops is not None
                            and entry.hops > cache_hops):
                    qb = None
                else:
                    dir_ab = _dir_of(topo, a, b)
                    rec = (entry.payload.get("isl_queue_bits", {})
                           .get(dir_ab) if dir_ab else None)
                    qb = (rec.get("value")
                          if isinstance(rec, dict) and rec.get("peer") == b
                          else None)
            if qb is None:
                return float("inf")  # unknown queue state is not assumed free
            # Dynamic-rate mode derives capacity from the exact same
            # propagation observation used above: current for our incident
            # edge, cached/stale for a remote edge.  This changes no
            # information boundary and makes a zero-MCS edge unreachable.
            rate = (isl_rate_bps if rate_from_propagation is None
                    else rate_from_propagation(c))
            if rate <= 0:
                return float("inf")
            return c + qb / rate

    if policy not in ("hop", "info_queue", "info_physical"):
        # dist[x] = forward cost from x to the nearest target; the search
        # expands backward from targets, so the forward edge cost is evaluated
        # reversed.
        dist = _multi_source_dist(
            adj, targets, lambda u, v: fwd_cost(v, u), sorted_adj=sorted_adj)
    if dist.get(sat, float("inf")) == float("inf"):
        return [], "unreachable"
    scored = []
    for d, n in sorted(topo.get(sat, {}).items()):
        if policy == "hop":
            w = 1.0
        elif policy == "info_queue":
            # F0: only the current satellite's own direction queue is
            # visible. The remainder of the path is a hop-count anchor.
            w = 1.0 + own_queue_bits.get(d, 0) / max(1.0, isl_rate_bps)
        elif policy == "info_physical":
            # F1: add current first-hop physical facts. A zero derived rate
            # or unavailable geometry is fail-closed for this candidate.
            if not geometry.isl_available(sat, n, now):
                continue
            propagation = prop_delay(geometry.isl_range_km(sat, n, now))
            rate = (isl_rate_bps if rate_from_propagation is None
                    else rate_from_propagation(propagation))
            if rate <= 0:
                continue
            w = propagation + own_queue_bits.get(d, 0) / max(1.0, rate)
        else:
            w = fwd_cost(sat, n)
        total = w + dist.get(n, float("inf"))
        if total < float("inf"):
            scored.append((total, d))
    if not scored and policy == "info_physical":
        # A temporary zero-rate/geometry outage must still enter the kernel's
        # holding/re-decision path. Returning no candidates would be decoded
        # as NO_ROUTE, changing packet-fate semantics relative to hop. Keep
        # every topologically reachable first hop as a deferred fallback;
        # the kernel performs the authoritative availability check.
        scored = [
            (1e300, d) for d, n in sorted(topo.get(sat, {}).items())
            if dist.get(n, float("inf")) < float("inf")
        ]
    scored.sort(key=lambda x: (x[0], x[1]))
    if best_only and scored:
        best = scored[0][0]
        scored = [item for item in scored
                  if abs(item[0] - best) <= 1e-12 * max(1.0, abs(best))]
    return [d for _c, d in scored], "ok"


def _dir_of(topo, a, b):
    for d, n in topo.get(a, {}).items():
        if n == b:
            return d
    return None
