"""Observation contracts and action-space feature builders for leo_sim RL."""
from __future__ import annotations

import math

import numpy as np


CONTRACTS = ("C1", "C3", "C4", "C5", "C6", "C7", "GAT", "MPNN")

# per-origin feature block layout (all normalized):
#   [isl_queue_ratio, access_load_ratio, n_visible_cells_norm, aoi_norm]
ORIGIN_FEATURES = 4
# own-state feature block layout (all normalized):
#   [access_slots_ratio, qN, qS, qE, qW, visible_cells_ratio, bias_flag]
# qN/qS/qE/qW are the satellite's OWN per-direction ISL egress queue
# occupancies (the M2 local out-queue observation, absorbed as the v1
# baseline); a direction with no link reads 1.0 (worst case), matching the
# legacy infQueue clip semantics.
OWN_FEATURES = 7
# Destination-conditioning features appended to every contract's observation:
#   [dst_bearing_sin, dst_bearing_cos, dst_dist_norm]
# bearing is the azimuth of the destination in the local ENU tangent plane at
# the current satellite (N=0, E=90deg); distance is the great-circle distance
# from the satellite subpoint to the destination, normalized by 20000 km.
DEST_FEATURES = 3
_DEST_DIST_NORM_KM = 20000.0
_EARTH_R_KM = 6371.0
C3_DIM = OWN_FEATURES + ORIGIN_FEATURES            # own state + mean aggregate
C4_DIM = OWN_FEATURES + ORIGIN_FEATURES            # own + AoI-weighted aggregate
C5_DIM = OWN_FEATURES + ORIGIN_FEATURES + 1        # own + freshest entry + staleness flag
C6_MAX_HOPS = 4
C6_DIM = OWN_FEATURES + C6_MAX_HOPS * ORIGIN_FEATURES      # own + per-hop buckets
C7_MAX_ENTRIES = 5
C7_DIM = OWN_FEATURES + C7_MAX_ENTRIES * (ORIGIN_FEATURES + 1)  # own + AoI-ordered seq
C1_MAX_NEIGHBORS = 4
C1_DIM = OWN_FEATURES + C1_MAX_NEIGHBORS * ORIGIN_FEATURES

# Every contract appends the 3 destination-conditioning features.
CONTRACT_DIMS = {
    "C1": C1_DIM + DEST_FEATURES,
    "C3": C3_DIM + DEST_FEATURES,
    "C4": C4_DIM + DEST_FEATURES,
    "C5": C5_DIM + DEST_FEATURES,
    "C6": C6_DIM + DEST_FEATURES,
    "C7": C7_DIM + DEST_FEATURES,
}
ACTIONS = ("deliver", "N", "S", "E", "W")


# Graph-state contracts consume a k-hop local subgraph rather than a fixed aggregate.
GRAPH_MAX_NODES = 32
# Node feature layout (all normalized), v2 (18 dims; the v1 15-dim layout
# predates the access-load/visible-cells/AoI block, so pre-v2 graph
# checkpoints fail the contract width check and must be retrained):
#   [0:4]  per-direction ISL egress queue occupancy (N/S/E/W)
#   [4]    hop distance of the entry from the root
#   [5]    node degree
#   [6]    is_root flag
#   [7]    valid-node flag (padding rows stay 0)
#   [8:12] first-hop direction one-hot from the root (V1-style readout)
#   [12:15] ECEF position relative to the root, scaled by 7000 km
#   [15]   access-slot load ratio (payload access_slots_used / cap)
#   [16]   visible-cell count, clipped at 10
#   [17]   entry AoI normalized by TTL (the root's own fresh state lives in
#          the own-state tail; the root row reads 0 here)
GRAPH_NODE_FEAT_DIM = 18
GRAPH_DIRS = ("N", "S", "E", "W")
GRAPH_CONTRACTS = ("GAT", "MPNN")


def graph_state_dim(max_nodes: int = GRAPH_MAX_NODES,
                    node_feat_dim: int = GRAPH_NODE_FEAT_DIM) -> int:
    """Flat width of a graph contract observation."""
    return (max_nodes * node_feat_dim
            + max_nodes * max_nodes
            + len(GRAPH_DIRS) * max_nodes
            + OWN_FEATURES + DEST_FEATURES)  # own-state tail + destination features


GRAPH_CONTRACT_DIMS = {c: graph_state_dim() for c in GRAPH_CONTRACTS}
CONTRACT_DIMS = dict(CONTRACT_DIMS)
CONTRACT_DIMS.update(GRAPH_CONTRACT_DIMS)
LAST_HOP_FEATURES = len(GRAPH_DIRS)


def contract_dim(contract: str, include_last_hop: bool = False) -> int:
    """Observation width for `contract`, optionally including 4-dim last-hop."""
    if contract not in CONTRACT_DIMS:
        raise ValueError(f"unknown learning contract {contract!r}")
    return CONTRACT_DIMS[contract] + (LAST_HOP_FEATURES if include_last_hop else 0)


def last_hop_features(last_hop_dir: str | None) -> np.ndarray:
    """4-dim one-hot incoming neighbor direction (all zeros at ingress/unknown)."""
    vec = np.zeros(LAST_HOP_FEATURES, dtype=np.float64)
    if last_hop_dir in GRAPH_DIRS:
        vec[GRAPH_DIRS.index(last_hop_dir)] = 1.0
    return vec


def queue_reward(wait_s: float, w1: float, beta: float) -> float:
    """Corrected (M1) queue reward: ``w1 * exp(-beta * max(wait_s, 0))``.

    Legacy source: getQueueReward M1 branch, SimulationRL.py:10289-10291
    (beta = _M1_BETA = 200 s^-1, SimulationRL.py:345; w1 default 20,
    SimulationRL.py:270). ``wait_s`` is the packet's realized queueing delay
    in seconds on the hop being rewarded — the analog of the legacy
    ``block.queueTime[-1]`` (send minus receive checkpoint,
    SimulationRL.py:2052). Absorbed as the v1 baseline per
    ANALYSIS/LEO-V2-ORIGINAL-PLAN.md:86.
    """
    return float(w1) * math.exp(-float(beta) * max(float(wait_s), 0.0))


def forward_reward(wait_s: float, w1: float, beta: float,
                   step_penalty: float) -> float:
    """Learning reward for one forward hop.

    ``queue_reward`` remains the raw legacy M1 diagnostic.  The training
    objective adds a configured per-hop cost.  Config validation requires
    ``step_penalty <= -w1``, therefore every forward transition is <= 0 even
    when the realized queue wait is zero; an agent cannot improve return by
    taking an unnecessary extra hop before it reaches the terminal delivery
    reward.
    """
    raw = queue_reward(wait_s, w1, beta)
    return raw + float(step_penalty)


def own_state(slots_used: int, slots_cap: int, isl_queue_bits: dict,
              isl_queue_cap: int, n_visible: int, n_cells: int) -> np.ndarray:
    """Own state: access-slot ratio + per-direction ISL egress queue
    occupancy + visible-cell ratio + bias flag.

    The per-direction queue block is the M2 local out-queue observation
    (legacy _appendOwnQueueM2, SimulationRL.py:9866-9875): each direction is
    min(queue/cap, 1.0); a direction with no link reads as fully congested
    (1.0), matching the legacy infQueue clip (getQueues,
    SimulationRL.py:9077-9092).
    """
    cap = max(1, isl_queue_cap)
    qs = [1.0 if d not in isl_queue_bits
          else min(isl_queue_bits[d] / cap, 1.0) for d in GRAPH_DIRS]
    return np.array([
        slots_used / max(1, slots_cap),
        *qs,
        n_visible / max(1, n_cells),
        1.0,  # bias/valid flag marking real own measurement
    ], dtype=np.float64)


def _peer_bit_value(record, peer: int | None):
    """Queue metric carried by an advertisement.

    New payloads bind each direction to the peer it was measured on
    (``{"peer": p, "value": bits}``); after a rematch the direction may point
    at a different peer, or be removed from the origin's topology entirely
    (peer is None).  Either way the metric must read as 0: an entry for a
    direction that no longer exists is stale even though there is no peer
    left to mismatch against.  Legacy plain-int payloads are accepted for
    backwards-compatible direct test callers.
    """
    if isinstance(record, dict):
        if peer is None or record.get("peer") != peer:
            return 0
        value = record.get("value")
        return value if isinstance(value, (int, float)) else 0
    return record if isinstance(record, (int, float)) else 0


def _origin_features(entry, now: float, isl_queue_cap: int,
                     topo: dict | None = None,
                     origin: int | None = None) -> np.ndarray:
    p = entry.payload
    peers = (topo.get(origin, {}) if topo is not None and origin is not None
             else {})
    q = sum(_peer_bit_value(rec, peers.get(d))
            for d, rec in p.get("isl_queue_bits", {}).items())
    used = p.get("access_slots_used", 0)
    cap = max(1, p.get("access_slots_cap", 1))
    nvis = len(p.get("visible_cells", ()))
    aoi = max(0.0, entry.aoi(now))
    return np.array([
        q / max(1, isl_queue_cap * 4),
        used / cap,
        min(1.0, nvis / 10.0),
        min(1.0, aoi / max(entry.ttl_s, 1e-9)),
    ], dtype=np.float64)


def destination_features(sat_lat_deg: float, sat_lon_deg: float,
                         dst_lat_deg: float, dst_lon_deg: float) -> np.ndarray:
    """3-dim destination features: ENU bearing (sin/cos) + great-circle dist."""
    sat_lat = math.radians(float(sat_lat_deg))
    sat_lon = math.radians(float(sat_lon_deg))
    dst_lat = math.radians(float(dst_lat_deg))
    dst_lon = math.radians(float(dst_lon_deg))

    # Destination vector in ENU at the satellite subpoint.
    dx = (_EARTH_R_KM * math.cos(dst_lat) * math.cos(dst_lon)
          - _EARTH_R_KM * math.cos(sat_lat) * math.cos(sat_lon))
    dy = (_EARTH_R_KM * math.cos(dst_lat) * math.sin(dst_lon)
          - _EARTH_R_KM * math.cos(sat_lat) * math.sin(sat_lon))
    dz = (_EARTH_R_KM * math.sin(dst_lat)
          - _EARTH_R_KM * math.sin(sat_lat))
    east = (-math.sin(sat_lon) * dx + math.cos(sat_lon) * dy)
    north = (-math.sin(sat_lat) * math.cos(sat_lon) * dx
             - math.sin(sat_lat) * math.sin(sat_lon) * dy
             + math.cos(sat_lat) * dz)
    bearing = math.atan2(east, north)

    # Great-circle distance (haversine).
    dlat = dst_lat - sat_lat
    dlon = dst_lon - sat_lon
    a = (math.sin(dlat / 2.0) ** 2
         + math.cos(sat_lat) * math.cos(dst_lat) * math.sin(dlon / 2.0) ** 2)
    dist_km = 2.0 * _EARTH_R_KM * math.asin(math.sqrt(min(1.0, a)))
    return np.array([
        math.sin(bearing),
        math.cos(bearing),
        min(1.0, dist_km / _DEST_DIST_NORM_KM),
    ], dtype=np.float64)


def information_set(contract: str, sat: int, cache, now: float,
                    topo, obs_hops: int | None = None) -> dict[int, object]:
    """The exact cache entries a contract may see.

    C1 sees only current-neighbour origins whose advertisement also arrived in
    at most one control hop.  C3-C7 and the graph contracts see the same set:
    all valid arrived entries, optionally restricted to entries that travelled
    at most `obs_hops` hops (None = no hop restriction, i.e. the full vis_k
    cache).
    """
    entries = cache.valid_entries(now)
    if contract == "C1":
        allowed = {sat} | set(topo.get(sat, {}).values())
        return {o: e for o, e in entries.items()
                if o in allowed and e.hops <= 1}
    if contract in ("C3", "C4", "C5", "C6", "C7", "GAT", "MPNN"):
        if obs_hops is None:
            return dict(entries)
        return {o: e for o, e in entries.items() if e.hops <= obs_hops}
    raise ValueError(f"unknown contract {contract!r}")


def _bfs_first_dirs(sat: int, topo: dict) -> dict[int, str]:
    """First-hop direction from `sat` to every reachable node in `topo`.

    BFS over the static ISL adjacency; the direction of the very first
    hop from the root labels each node (V1-style directional readout).
    Returns {node: first_dir}; the root itself is absent.
    """
    out: dict[int, str] = {}
    frontier = [(d, n) for d, n in sorted(topo.get(sat, {}).items())]
    for d, n in frontier:
        if n not in out and n != sat:
            out[n] = d
    next_frontier = [n for _d, n in frontier if n != sat]
    while next_frontier:
        further: list[int] = []
        for node in next_frontier:
            for d, n in sorted(topo.get(node, {}).items()):
                if n != sat and n not in out:
                    out[n] = out[node]
                    further.append(n)
        next_frontier = further
    return out


def _graph_node_features(origin: int, entry, root: int, first_dir: str,
                         topo: dict, isl_queue_cap: int, now: float,
                         root_pos: tuple = (0.0, 0.0, 0.0)) -> np.ndarray:
    """18-dim node feature for one subgraph node (V2 payload semantics)."""
    p = entry.payload if entry is not None else {}
    peers = topo.get(origin, {})
    q = {d: _peer_bit_value(rec, peers.get(d))
         for d, rec in (p.get("isl_queue_bits", {})
                        if entry is not None else {}).items()}
    used = p.get("access_slots_used", 0) if entry is not None else 0
    cap = max(1, p.get("access_slots_cap", 1)) if entry is not None else 1
    nvis = len(p.get("visible_cells", ())) if entry is not None else 0
    hop = entry.hops if entry is not None else 0
    pos = p.get("position", (0.0, 0.0, 0.0)) if entry is not None else (0.0, 0.0, 0.0)
    if origin == root:
        # the root never has a cache self-entry (its own advertisement is
        # refused); its position is directly measured and supplied as
        # root_pos, so the root row is exactly the frame origin
        pos = root_pos
    deg = sum(1 for _d, n in topo.get(origin, {}).items() if n is not None)
    feats = np.zeros(GRAPH_NODE_FEAT_DIM, dtype=np.float64)
    for i, d in enumerate(GRAPH_DIRS):
        feats[i] = min(1.0, q.get(d, 0) / max(1, isl_queue_cap))
    feats[4] = min(1.0, hop / max(1, 4))
    feats[5] = min(1.0, deg / max(1, len(GRAPH_DIRS)))
    feats[6] = 1.0 if origin == root else 0.0
    feats[7] = 1.0  # valid node flag (padding rows stay 0)
    if first_dir in GRAPH_DIRS:
        feats[8 + GRAPH_DIRS.index(first_dir)] = 1.0
    rel = np.asarray(pos, dtype=np.float64) - np.asarray(root_pos, dtype=np.float64)
    feats[12:15] = rel / 7000.0
    # access load, visible-cell count and AoI — the same quantities the
    # vector contracts expose in their per-origin block (ORIGIN_FEATURES)
    feats[15] = used / cap
    feats[16] = min(1.0, nvis / 10.0)
    feats[17] = (0.0 if entry is None else
                 min(1.0, max(0.0, entry.aoi(now)) / max(entry.ttl_s, 1e-9)))
    return feats


def build_graph_observation(contract: str, sat: int, cache, now: float,
                            topo: dict, own: np.ndarray,
                            isl_queue_cap: int = 256_000_000,
                            obs_hops: int | None = None,
                            dst_feats: np.ndarray | None = None,
                            root_pos: tuple | None = None,
                            first_dirs: dict[int, str] | None = None,
                            include_last_hop: bool = False,
                            last_hop_dir: str | None = None) -> np.ndarray:
    """Fixed-width flattened k-hop subgraph for the GAT/MPNN contracts.

    Layout: node features [MAX_N, GRAPH_NODE_FEAT_DIM] + directed adjacency
    [MAX_N, MAX_N] + directional readout masks [4, MAX_N] + own-state tail
    [OWN_FEATURES] + destination features [DEST_FEATURES]
    (+ optional 4-dim one-hot incoming hop direction when include_last_hop).
    Nodes are the actually-arrived valid cache origins plus the root; only
    payload fields carried by arrived ControlPackets enter node features
    (no future geometry, no hidden global queues).

    ``root_pos`` is the root satellite's own ECEF position, supplied by the
    kernel from direct geometry (the control plane never caches a
    satellite's own advertisement). Without it node positions would degrade
    to drifting absolute coordinates; the (0, 0, 0) fallback exists only
    for direct non-kernel callers.
    """
    entries = information_set(contract, sat, cache, now, topo,
                              obs_hops=obs_hops)
    if first_dirs is None:
        first_dirs = _bfs_first_dirs(sat, topo)
    n = GRAPH_MAX_NODES
    # Prioritize closer (fewer control hops) and fresher (lower AoI) origins
    # when truncating to GRAPH_MAX_NODES - 1 so large constellations never drop
    # immediate 1-hop high-ID neighbors in favor of distant low-ID satellites.
    origins = sorted(
        (o for o in entries if o != sat),
        key=lambda o: (entries[o].hops, entries[o].aoi(now), o),
    )
    nodes = [sat] + origins[:n - 1]
    overflow = max(0, len(origins) - (n - 1))
    index = {node: i for i, node in enumerate(nodes)}
    if root_pos is None:
        root_pos = (0.0, 0.0, 0.0)

    feats = np.zeros((n, GRAPH_NODE_FEAT_DIM), dtype=np.float64)
    for i, node in enumerate(nodes):
        entry = entries.get(node) if node != sat else None
        feats[i] = _graph_node_features(
            node, entry, sat, first_dirs.get(node), topo, isl_queue_cap,
            now, root_pos=root_pos)

    adj = np.zeros((n, n), dtype=np.float64)
    for dst, node in enumerate(nodes):
        for _d, nb in topo.get(node, {}).items():
            src = index.get(nb)
            if src is not None and src != dst:
                adj[dst, src] = 1.0

    readout = np.zeros((len(GRAPH_DIRS), n), dtype=np.float64)
    for i, node in enumerate(nodes):
        fd = first_dirs.get(node)
        if fd in GRAPH_DIRS:
            readout[GRAPH_DIRS.index(fd), i] = 1.0

    tail = np.asarray(own, dtype=np.float64).reshape(-1)
    if tail.shape[0] != OWN_FEATURES:
        raise ValueError(
            f"graph tail requires a {OWN_FEATURES}-dim own state, got {tail.shape}")
    df = (np.asarray(dst_feats, dtype=np.float64).reshape(-1)
          if dst_feats is not None else np.zeros(DEST_FEATURES))
    if df.shape[0] != DEST_FEATURES:
        raise ValueError(f"dst_feats must have {DEST_FEATURES} dims, got {df.shape}")
    tail = np.concatenate([tail, df])
    if include_last_hop:
        tail = np.concatenate([tail, last_hop_features(last_hop_dir)])
    state = np.concatenate([feats.reshape(-1), adj.reshape(-1),
                            readout.reshape(-1), tail])
    expected = graph_state_dim() + (LAST_HOP_FEATURES if include_last_hop else 0)
    if state.shape[0] != expected:
        raise AssertionError(f"graph state width {state.shape[0]} != {expected}")
    return state


def build_observation(contract: str, sat: int, cache, now: float, topo,
                      own: np.ndarray, isl_queue_cap: int = 256_000_000,
                      obs_hops: int | None = None,
                      dst_feats: np.ndarray | None = None,
                      root_pos: tuple | None = None,
                      first_dirs: dict[int, str] | None = None,
                      include_last_hop: bool = False,
                      last_hop_dir: str | None = None) -> np.ndarray:
    if contract in GRAPH_CONTRACTS:
        return build_graph_observation(
            contract, sat, cache, now, topo, own, isl_queue_cap,
            obs_hops=obs_hops, dst_feats=dst_feats, root_pos=root_pos,
            first_dirs=first_dirs, include_last_hop=include_last_hop,
            last_hop_dir=last_hop_dir)
    entries = information_set(contract, sat, cache, now, topo,
                              obs_hops=obs_hops)
    feats = {o: _origin_features(e, now, isl_queue_cap, topo, o)
             for o, e in entries.items()}
    own = np.asarray(own, dtype=np.float64)
    df = (np.asarray(dst_feats, dtype=np.float64).reshape(-1)
          if dst_feats is not None else np.zeros(DEST_FEATURES))
    if df.shape[0] != DEST_FEATURES:
        raise ValueError(f"dst_feats must have {DEST_FEATURES} dims, got {df.shape}")
    lh = last_hop_features(last_hop_dir) if include_last_hop else None

    def _finish(base: np.ndarray) -> np.ndarray:
        if lh is not None:
            return np.concatenate([base, df, lh])
        return np.concatenate([base, df])

    if contract == "C1":
        neighbors = sorted(set(topo.get(sat, {}).values()))
        blocks = [feats.get(n, np.zeros(ORIGIN_FEATURES)) for n in neighbors]
        blocks += [np.zeros(ORIGIN_FEATURES)] * (C1_MAX_NEIGHBORS - len(blocks))
        return _finish(np.concatenate([own] + blocks[:C1_MAX_NEIGHBORS]))

    if contract == "C3":
        agg = (np.mean(list(feats.values()), axis=0) if feats
               else np.zeros(ORIGIN_FEATURES))
        return _finish(np.concatenate([own, agg]))

    if contract == "C4":
        if not entries:
            agg = np.zeros(ORIGIN_FEATURES)
        else:
            w = np.array([np.exp(-max(0.0, e.aoi(now)) / max(e.ttl_s, 1e-9))
                          for e in entries.values()])
            agg = np.average(list(feats.values()), axis=0, weights=w)
        return _finish(np.concatenate([own, agg]))

    if contract == "C5":
        if not entries:
            return _finish(np.concatenate(
                [own, np.zeros(ORIGIN_FEATURES), [0.0]]))
        freshest_o, freshest = min(
            entries.items(), key=lambda oe: oe[1].aoi(now))
        return _finish(np.concatenate(
            [own, _origin_features(freshest, now, isl_queue_cap, topo,
                                   freshest_o),
             [1.0]]))

    if contract == "C6":
        buckets = [[] for _ in range(C6_MAX_HOPS)]
        for o, e in entries.items():
            h = min(max(1, e.hops), C6_MAX_HOPS)
            buckets[h - 1].append(feats[o])
        blocks = [np.mean(b, axis=0) if b else np.zeros(ORIGIN_FEATURES)
                  for b in buckets]
        return _finish(np.concatenate([own] + blocks))

    if contract == "C7":
        ordered = sorted(entries.items(), key=lambda oe: oe[1].aoi(now))
        blocks = []
        for o, e in ordered[:C7_MAX_ENTRIES]:
            # keep each entry's advertisement origin: peer-bound queue bits
            # must be validated against the peer the entry was measured on
            # (topo[origin]), never against the root satellite's own edges
            blocks.append(np.concatenate(
                [_origin_features(e, now, isl_queue_cap, topo, o), [1.0]]))
        while len(blocks) < C7_MAX_ENTRIES:
            blocks.append(np.zeros(ORIGIN_FEATURES + 1))
        return _finish(np.concatenate([own] + blocks))

    raise ValueError(f"unknown contract {contract!r}")


def build_action_mask(can_deliver: bool, isl_room: dict) -> dict:
    """Legal actions: deliver only when directly visible with downlink room;
    each ISL direction only with queue room. Computed by the kernel from
    current local state only."""
    mask = {"deliver": bool(can_deliver)}
    for d, room in sorted(isl_room.items()):
        mask[d] = bool(room)
    return mask


def ddqn_targets(q_online_next: np.ndarray, q_target_next: np.ndarray,
                 next_mask: np.ndarray, rewards: np.ndarray,
                 dones: np.ndarray, gamma: float) -> np.ndarray:
    """Canonical Double-DQN targets.

    q_online_next, q_target_next: (batch, n_actions)
    next_mask: (batch, n_actions) bool, True = legal action
    rewards, dones: (batch,)
    """
    q_online_next = np.asarray(q_online_next, dtype=np.float64)
    q_target_next = np.asarray(q_target_next, dtype=np.float64)
    dones = np.asarray(dones, dtype=bool)
    masked = np.where(next_mask, q_online_next, -np.inf)
    a_star = np.argmax(masked, axis=1)
    selected = masked[np.arange(len(a_star)), a_star]
    # Terminal transitions do not bootstrap, so they do not need a legal
    # action in the next state.  Non-terminal rows still fail closed.
    if not np.all(np.isfinite(selected[~dones])):
        raise ValueError("every non-terminal transition must have at least one legal action")
    bootstrap = q_target_next[np.arange(len(a_star)), a_star]
    bootstrap = np.where(dones, 0.0, bootstrap)
    return rewards + gamma * bootstrap


__all__ = [
    "CONTRACTS", "ORIGIN_FEATURES", "OWN_FEATURES", "DEST_FEATURES",
    "_DEST_DIST_NORM_KM", "_EARTH_R_KM",
    "C3_DIM", "C4_DIM", "C5_DIM", "C6_MAX_HOPS", "C6_DIM",
    "C7_MAX_ENTRIES", "C7_DIM", "C1_MAX_NEIGHBORS", "C1_DIM",
    "ACTIONS", "GRAPH_MAX_NODES", "GRAPH_NODE_FEAT_DIM", "GRAPH_DIRS",
    "GRAPH_CONTRACTS", "graph_state_dim", "GRAPH_CONTRACT_DIMS",
    "CONTRACT_DIMS", "LAST_HOP_FEATURES", "contract_dim",
    "last_hop_features", "queue_reward", "forward_reward", "own_state",
    "destination_features", "information_set", "_peer_bit_value",
    "_origin_features", "_bfs_first_dirs", "_graph_node_features",
    "build_graph_observation", "build_observation", "build_action_mask",
    "ddqn_targets",
]
