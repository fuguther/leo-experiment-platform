"""Join policy decisions to terminal fates and output-only resource events.

These are online-realized congestion-risk proxies. Queue-room rejection
without a chosen action remains unassigned; an earlier action can observe a
later rejection as a terminal consequence. The online policy never reads this
module or the future targets it produces.
"""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

import numpy as np


def _close_busy(episode, labels, pkt_bits, rate_bps, deadline_s):
    saturated_after = episode["last_saturated"]
    accepted = episode["accepted"]
    for i, (position, key) in enumerate(accepted):
        if saturated_after > position:
            cost = 1.0
        else:
            cost = min(1.0, 0.5 * (len(accepted) - i - 1) * pkt_bits / (rate_bps * deadline_s))
        if key in labels:
            raise ValueError(f"duplicate directed enqueue label {key}")
        labels[key] = cost


def _reconstruct(events, capacity, pkt_bits, rate_bps, deadline_s):
    busy = {}
    labels = {}
    port_states = defaultdict(dict)
    averages = defaultdict(lambda: ([], []))
    topologies = ([], [])
    for order, row in enumerate(events):
        if row[0] == "topology":
            topologies[0].append(row[1])
            topologies[1].append(row[2])
            continue
        at, sat, direction, peer, generation, retired, data_bits, control_bits, active, event, pid = row
        edge = (sat, direction, peer, generation)
        occupied = data_bits + control_bits
        if occupied or active:
            interval = busy.setdefault(edge, {"accepted": [], "last_saturated": -1})
            if event == "enqueue_data":
                interval["accepted"].append((order, (pid, sat, peer, generation)))
            if occupied > capacity - pkt_bits:
                interval["last_saturated"] = order
        elif edge in busy:
            _close_busy(busy.pop(edge), labels, pkt_bits, rate_bps, deadline_s)
        ports = port_states[sat]
        known = ports.get(direction)
        if known is None or generation >= known[0]:
            ports[direction] = (generation, 0 if retired else occupied)
            times, values = averages[sat]
            times.append(at)
            values.append(min(1.0, sum(x[1] for x in ports.values()) / (4.0 * capacity)))
    for interval in busy.values():
        _close_busy(interval, labels, pkt_bits, rate_bps, deadline_s)
    return labels, averages, topologies


def _prefix_spill(decisions):
    by_sat = defaultdict(lambda: ([], [0]))
    for row in decisions:
        _snap, _pid, at, sat, _peer, _gen, _hops, _g, _x, _action, _prob, delta_h = row
        times, prefix = by_sat[sat]
        times.append(at)
        prefix.append(prefix[-1] + (delta_h > 0))
    return by_sat


def _window_share(spills, satellites, start, end):
    positive = count = 0
    for sat in satellites:
        times, prefix = spills.get(sat, ((), (0,)))
        first, last = bisect_right(times, start), bisect_right(times, end)
        count += last - first
        positive += prefix[last] - prefix[first]
    return (positive / count, True) if count else (0.0, False)


def _queue_at(averages, sat, when):
    times, values = averages.get(sat, ((), ()))
    index = bisect_right(times, when) - 1
    return values[index] if index >= 0 else 0.0


def _two_hop(topologies, sat, at):
    times, graphs = topologies
    index = bisect_right(times, at) - 1
    if index < 0:
        return {sat}
    graph = graphs[index]
    neighbors = graph[sat]
    return {sat, *neighbors, *(node for peer in neighbors for node in graph[peer])}


def build_dataset(result, events, rows, config):
    audit = result["local_forward_adapter"]
    snapshots, decisions = audit["snapshots"], audit["decisions"]
    if not snapshots or not decisions:
        raise ValueError("no legal forwarding decisions were collected")
    demand = config["demand"]
    links = config["links"]
    capacity = links["isl_queue_bits"]
    deadline_s = demand["deadline_s"]
    labels, averages, topologies = _reconstruct(
        events, capacity, demand["packet_bits"], links["isl_rate_mbps"] * 1e6, deadline_s)
    spills = _prefix_spill(decisions)
    horizon = float(result["stop_time_s"])
    truth = np.zeros((len(snapshots), 4, 3), dtype=np.float32)
    valid = np.zeros(truth.shape, dtype=bool)
    for i, (at, sat, peers, _, _, _) in enumerate(snapshots):
        future = at + 0.2
        share, has_volume = _window_share(spills, _two_hop(topologies, sat, at), at, future)
        for d, peer in enumerate(peers):
            if peer is None:
                continue
            now_queue = _queue_at(averages, peer, at)
            truth[i, d, 0] = now_queue
            valid[i, d, 0] = True
            if future <= horizon:
                truth[i, d, 1] = np.clip(_queue_at(averages, peer, future) - now_queue, -1, 1)
                truth[i, d, 2] = share
                valid[i, d, 1] = True
                valid[i, d, 2] = has_volume

    by_pid = {row["packet_id"]: row for row in rows}
    pack_ego = {}
    for pid, delivery in result["deliveries"].items():
        row = by_pid[pid]
        delay = delivery["delivered_at"] - row["emit_time_s"]
        if delivery["delivered_at"] < row["deadline_at_s"]:
            pack_ego[pid] = 1.0 - 0.5 * delay / deadline_s

    g_ego = np.zeros(len(decisions), dtype=np.float32)
    g_ext = np.zeros_like(g_ego)
    next_cost = {}
    seen = set()
    for i in range(len(decisions) - 1, -1, -1):
        sid, pid, _at, sat, peer, generation, _hop, _g, _x, _action, _prob, _delta = decisions[i]
        key = (pid, sat, peer, generation)
        if key not in labels:
            raise ValueError(f"policy choice {key} has no accepted enqueue event")
        downstream = next_cost.get(pid, 1.5 if result["fates"][pid] == "ISL_QUEUE_OVERFLOW" else 0.0)
        accumulated = labels[key] + 0.98 * downstream
        next_cost[pid] = accumulated
        seen.add(pid)
        g_ego[i] = pack_ego.get(pid, 0.0)
        g_ext[i] = min(3.0, accumulated)
        if sid is None or not 0 <= sid < len(snapshots):
            raise ValueError(f"invalid snapshot ID {sid}")
    unassigned = sum(fate == "ISL_QUEUE_OVERFLOW" and pid not in seen
                     for pid, fate in result["fates"].items())

    snaps = {
        "x_2hop": np.stack([s[3] for s in snapshots]), "snap_cur": np.stack([s[4] for s in snapshots]),
        "hist_seq": np.stack([s[5] for s in snapshots]), "y_priv": truth, "y_valid": valid,
        "at": np.asarray([s[0] for s in snapshots]), "sat": np.asarray([s[1] for s in snapshots]),
    }
    packets = {
        "snap_id": np.asarray([d[0] for d in decisions], dtype=np.int32),
        "pid": np.asarray([d[1] for d in decisions], dtype=np.int32),
        "x_pkt_global": np.stack([d[7] for d in decisions]),
        "x_pkt_port": np.stack([d[8] for d in decisions]),
        "action": np.asarray([d[9] for d in decisions], dtype=np.int8),
        "probability": np.asarray([d[10] for d in decisions], dtype=np.float32),
        "g_ego": g_ego,
        "g_ext": g_ext,
    }
    diagnostics = {
        "snapshots": len(snapshots), "decisions": len(decisions),
        "forward_action_counts": np.bincount(packets["action"], minlength=4).tolist(),
        "unassigned_overflow_packets": unassigned,
        "observed_quantiles": np.quantile(g_ext, [0.0, 0.5, 0.9, 1.0]).tolist(),
        "nonzero_external_cost": int(np.count_nonzero(g_ext)),
    }
    return snaps, packets, diagnostics


def merge_dataset(paths: list[tuple[Path, Path]]):
    snapshots = defaultdict(list)
    decisions = defaultdict(list)
    offset = 0
    for snapshots_path, packets_path in paths:
        with np.load(snapshots_path, allow_pickle=False) as snap, np.load(
                packets_path, allow_pickle=False) as pkt:
            for key in snap.files:
                snapshots[key].append(snap[key])
            for key in pkt.files:
                decisions[key].append(pkt[key] + offset if key == "snap_id" else pkt[key])
            offset += len(snap["x_2hop"])
    if offset == 0:
        raise ValueError("training requires at least one nonempty episode")
    return ({key: np.concatenate(values) for key, values in snapshots.items()},
            {key: np.concatenate(values) for key, values in decisions.items()})
