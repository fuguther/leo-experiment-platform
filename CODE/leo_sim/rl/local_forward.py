"""Value-only single-hop forwarding extensions for the Pigou study.

The simulator constructs these views from local measurements, the accepted
one-hop cache, immutable packet fields and the nominal ephemeris. Policies
never receive live link, endpoint, packet, geometry or cache objects.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import math
from pathlib import Path
import zlib

import numpy as np

DIRS = ("N", "S", "E", "W")
STAGE_S = 0.020
FLOWLET_S = 0.010


@dataclass(frozen=True, slots=True)
class EdgeReport:
    peer: int
    queue: float
    work: float
    rate: float
    propagation: float


@dataclass(frozen=True, slots=True)
class Port:
    peer: int | None
    up: bool = False
    queue: float = 0.0
    rate: float = 0.0
    propagation: float = 0.0
    generated_at: float | None = None
    received_at: float | None = None
    expires_at: float | None = None
    reports: tuple[EdgeReport | None, ...] = (None,) * 4
    hops: int | None = None
    progress_km: float = 0.0
    last_hop: bool = False
    generation: int = 0


@dataclass(frozen=True, slots=True)
class LocalForwardView:
    sat: int
    now: float
    pid: int
    src: str
    dst: str
    emitted_at: float
    bits: int
    hops_taken: int
    dst_features: tuple[float, float, float]
    egress_count: int
    ports: tuple[Port, ...]
    legal: tuple[str, ...]
    shortest: tuple[str, ...]
    # Used as first choice and fallback in the static and data-collection arms.
    base_order: tuple[str, ...]


def distance_to_cell_km(geometry, sat, lat, lon, at):
    slat, slon, _ = geometry.subpoint(sat, at)
    dlat = math.radians(lat - slat)
    dlon = math.radians(lon - slon)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(slat)) * math.cos(math.radians(lat))
         * math.sin(dlon / 2) ** 2)
    return 12742.0 * math.asin(math.sqrt(min(1.0, max(0.0, a))))


def _mean(values):
    values = tuple(values)
    return sum(values) / len(values) if values else 0.0


def _not_empty(port):
    return tuple(edge for edge in port.reports if edge is not None)


def _relative_hop(view):
    best = min((p.hops for p in view.ports if p.hops is not None), default=0)
    return [max(0, p.hops - best) if p.hops is not None else 0 for p in view.ports]


def primal_observation(view):
    own = []
    advertised = []
    for port in view.ports:
        own.extend((port.queue, port.rate, port.propagation, float(port.up)))
        records = _not_empty(port)
        age = 0.0 if port.generated_at is None else view.now - port.generated_at
        advertised.extend((_mean(edge.queue for edge in records),
                           _mean(edge.work for edge in records),
                           min(1.0, age / 0.2), float(port.received_at is not None)))
    packet = [min(4.0, view.bits / 250_000), min(2.0, view.hops_taken / 16),
              min(2.0, (view.now - view.emitted_at) / 0.3)]
    packet.extend(min(2.0, p.hops / 16) if p.hops is not None else 2.0
                  for p in view.ports)
    packet.extend(float(np.clip(p.progress_km / 6000.0, -1, 1)) for p in view.ports)
    packet.extend(float(p.last_hop) for p in view.ports)
    packet.extend(view.dst_features)
    return np.asarray(own + advertised + packet, dtype=np.float32)


def validate_adapter(options):
    allowed = {"kind", "threshold", "beta_ext", "temperature", "argmax", "shield",
               "checkpoint_path", "checkpoint_sha256", "capture", "telemetry_sat",
               "exploration", "seed", "hop_guard", "hop_penalty"}
    if set(options) - allowed:
        raise ValueError("unknown adapter options")
    kind = options.get("kind")
    if kind not in {"static_guard", "pigou_metadrl", "explore"}:
        raise ValueError("unsupported adapter kind")
    for name, default, lo, hi in (
            ("threshold", 0.50, 0.0, 1.0), ("beta_ext", 0.50, 0.0, 2.0),
            ("temperature", 0.08, 0.001, 2.0), ("exploration", 0.33, 0.0, 1.0),
            ("hop_penalty", 0.0, 0.0, 2.0)):
        value = options.get(name, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise ValueError(f"{name} must be a number in [{lo}, {hi}]")
    for name in ("argmax", "shield", "capture"):
        if name in options and not isinstance(options[name], bool):
            raise ValueError(f"{name} must be boolean")
    for name in ("telemetry_sat", "seed", "hop_guard"):
        if name in options and (type(options[name]) is not int or options[name] < 0):
            raise ValueError(f"{name} must be a non-negative integer")
    path, digest = options.get("checkpoint_path"), options.get("checkpoint_sha256")
    if kind == "pigou_metadrl":
        if not isinstance(path, str) or not path or not isinstance(digest, str) \
                or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("pigou_metadrl requires a checkpoint path and lowercase SHA-256")
    elif path is not None or digest is not None:
        raise ValueError("only pigou_metadrl loads weights")


@dataclass
class _SatelliteState:
    frames: deque = field(default_factory=lambda: deque(maxlen=8))
    timestamps: deque = field(default_factory=lambda: deque(maxlen=8))
    last_refresh: float = -math.inf
    peers: tuple = ()
    signatures: tuple = ()
    spill: np.ndarray = field(default_factory=lambda: np.zeros(4))
    total: np.ndarray = field(default_factory=lambda: np.zeros(4))
    residual: np.ndarray = field(default_factory=lambda: np.full(4, 0.05))
    previous_report: dict = field(default_factory=dict)
    phi: float = 0.0
    shield_state: float = 0.0
    encoded: np.ndarray | None = None
    snap_id: int | None = None
    flowlets: dict = field(default_factory=dict)
    bucket: int | None = None


def _bypasses(view, index):
    port = view.ports[index]
    direct = {p.peer for p in view.ports if p.up and p.peer is not None}
    reported = {edge.peer for j, p in enumerate(view.ports)
                if j != index and p.peer in direct for edge in _not_empty(p)}
    flags = tuple(edge is not None and edge.peer != view.sat
                  and (edge.peer in direct or edge.peer in reported)
                  for edge in port.reports)
    # Bypass directions are u -> w -> z -> v and u -> w -> v.
    # The last edge z -> v may be unseen in a one-hop control domain. A
    # topological closure with only v -> z is useful as a cycle feature,
    # but is not credited as an observed available directed bypass.
    strength = 0.0
    for j, alt in enumerate(view.ports):
        if j == index or not alt.up:
            continue
        for edge in _not_empty(alt):
            if edge.rate > 0 and edge.peer == port.peer:
                strength += 1.5 * math.sqrt((1 - alt.queue) * (1 - edge.queue))
        for z in sorted(direct - {port.peer, alt.peer}):
            incoming = next((e for e in _not_empty(alt) if e.peer == z and e.rate > 0), None)
            z_port = next(p for p in view.ports if p.peer == z and p.up)
            outgoing = next((e for e in _not_empty(z_port) if e.peer == port.peer and e.rate > 0), None)
            if incoming is not None and outgoing is not None:
                strength += ((1 - alt.queue) * (1 - incoming.queue) * (1 - outgoing.queue)) ** (1 / 3)
    return flags, max(0.0, 2.0 - strength) / 2.0


def _softmax(values, temperature):
    logits = (values - np.max(values)) / temperature
    probs = np.exp(logits)
    return probs / probs.sum()


class LocalForwardAdapter:
    def __init__(self, cfg, seed, model=None):
        self.cfg = cfg
        self.kind = cfg["kind"]
        self.model = model
        self.rng = np.random.default_rng(np.random.SeedSequence([cfg.get("seed", seed), 20261128]))
        self.sats = {}
        self.snapshots = []
        self.decisions = []
        self.traces = []
        self.calls = 0
        self.excess_hops = {}

    def _sat(self, sat):
        if sat not in self.sats:
            self.sats[sat] = _SatelliteState()
        return self.sats[sat]

    def arrive(self, sat, inbound, bits, is_spill):
        if inbound in DIRS:
            state = self._sat(sat)
            index = DIRS.index(inbound)
            state.total[index] += bits
            if is_spill:
                state.spill[index] += bits

    def _refresh(self, view, state, spatial):
        peers = tuple((p.peer, p.generation, bool(p.up))
                      if p.received_at is not None else (p.peer, p.generation, None) for p in view.ports)
        signatures = tuple(p.generated_at for p in view.ports)
        due = view.now - state.last_refresh >= STAGE_S
        newer = signatures != state.signatures and view.now - state.last_refresh >= 0.002
        if not (due or newer or peers != state.peers):
            return
        for i, peer in enumerate(peers):
            if state.peers and peer != state.peers[i]:
                for frame in state.frames:
                    frame[i, 2:5] = 0.0
                    frame[i, 7] = 0.05
                state.previous_report.pop(i, None)
                state.residual[i] = 0.05
        state.phi = float(state.spill.sum() / max(1e-6, state.total.sum()))
        qmean = _mean(p.queue for p in view.ports if p.up)
        state.shield_state = min(3.0, max(
            0.0, state.shield_state + 0.15 * state.phi * (qmean > 0.65)
            - 0.30 * state.shield_state * (qmean <= 0.25)))
        current = np.zeros((4, 8), dtype=np.float32)
        x_graph = np.zeros((4, 4, 8), dtype=np.float32)
        for i, (port, (cycles, _)) in enumerate(zip(view.ports, spatial)):
            records = _not_empty(port)
            qn = _mean(edge.queue for edge in records)
            if port.generated_at is not None:
                prior = state.previous_report.get(i)
                if prior is None or port.generated_at > prior[0]:
                    if prior is not None:
                        prediction = prior[1] * math.exp(-(port.generated_at - prior[0]) / 0.10)
                        e = abs(qn - prediction)
                        state.residual[i] = np.clip(state.residual[i] + 0.05 * (
                            float(e > state.residual[i]) - 0.10), 0.0, 1.0)
                    state.previous_report[i] = (port.generated_at, qn)
            age = 0.0 if port.generated_at is None else min(1.0, (view.now - port.generated_at) / 0.20)
            current[i] = [port.up, port.queue, qn, _mean(e.work for e in records),
                          age, port.rate, state.spill[i] / max(1e-6, state.total[i]),
                          state.residual[i]]
            for j, edge in enumerate(port.reports):
                if edge is not None and edge.rate > 0:
                    x_graph[i, j] = [1, edge.queue, edge.work, edge.rate,
                                     edge.propagation, age, edge.peer == view.sat, cycles[j]]
        state.frames.append(current.copy())
        state.timestamps.append(view.now)
        history = np.zeros((4, 8, 9), dtype=np.float32)
        first = 8 - len(state.frames)
        prior_time = state.timestamps[0]
        for i, (frame, timestamp) in enumerate(zip(state.frames, state.timestamps), first):
            history[:, i, :8] = frame
            history[:, i, 8] = math.log1p((timestamp - prior_time) / STAGE_S)
            prior_time = timestamp
        if self.model is not None:
            state.encoded = self.model.encode(x_graph, current, history)
        if self.cfg.get("capture"):
            state.snap_id = len(self.snapshots)
            # The trainer uses peers and graph ids only to join offline truth.
            self.snapshots.append((view.now, view.sat, tuple(p.peer if p.up else None for p in view.ports),
                                   x_graph, current, history))
        state.spill.fill(0)
        state.total.fill(0)
        state.last_refresh = view.now
        state.signatures = signatures
        state.peers = peers

    def choose(self, view, decision_id):
        self.calls += 1
        legal = view.legal
        if not legal:
            raise ValueError("a forwarding policy needs a nonempty legal mask")
        hops = _relative_hop(view)
        prior_debt = self.excess_hops.get(view.pid, 0)
        if self.kind == "static_guard":
            shortest = [d for d in view.base_order if d in view.shortest]
            full = any(p.queue > self.cfg.get("threshold", 0.50)
                       for d, p in zip(DIRS, view.ports) if d in view.shortest)
            action = shortest[0] if shortest and (full or prior_debt >= 2) else legal[0]
            self.excess_hops[view.pid] = prior_debt + hops[DIRS.index(action)]
            return action

        state = self._sat(view.sat)
        spatial = tuple(_bypasses(view, i) for i in range(4))
        self._refresh(view, state, spatial)
        qmean = _mean(p.queue for p in view.ports if p.up)
        g = np.asarray([*view.dst_features, (view.now - view.emitted_at) / 0.3,
                        view.hops_taken / 16.0, view.bits / 250_000.0, len(legal) / 4.0,
                        view.egress_count / 8.0, qmean, state.shield_state / 3.0],
                       dtype=np.float32)
        x = np.asarray([
            [d in legal, p.last_hop, p.queue, _mean(e.queue for e in _not_empty(p)),
             p.propagation, hops[i], np.clip(p.progress_km / 6000.0, -1, 1),
             0.0 if p.generated_at is None else min(1.0, (view.now - p.generated_at) / 0.2),
             spatial[i][1], d in view.shortest]
            for i, (d, p) in enumerate(zip(DIRS, view.ports))], dtype=np.float32)
        ids = [DIRS.index(d) for d in legal]
        eligible_positions = list(range(len(legal)))
        guard_hops = int(self.cfg.get("hop_guard", 0))
        if guard_hops and view.hops_taken >= guard_hops:
            shortest_positions = [
                index for index, direction in enumerate(legal)
                if direction in view.shortest
            ]
            if shortest_positions:
                eligible_positions = shortest_positions
        eligible_ids = [ids[index] for index in eligible_positions]
        ego = costs = None
        if self.model is None:
            explore = self.cfg.get("exploration", 0.33)
            prob = np.full(len(eligible_positions), explore / len(eligible_positions))
            prob[0] += 1.0 - explore
            local_choice = int(self.rng.choice(len(eligible_positions), p=prob))
            choice = eligible_positions[local_choice]
        else:
            ego, quantile = self.model.score(state.encoded, g, x)
            costs = quantile[:, -4:].mean(axis=1)
            charge = 2.0 * max(0.0, state.shield_state - 1.0) if self.cfg.get("shield", True) else 0.0
            score = (ego[ids]
                     - self.cfg.get("beta_ext", 0.50) * costs[ids]
                     - charge * np.asarray(hops)[ids]
                     - self.cfg.get("hop_penalty", 0.0) * np.asarray(hops)[ids])
            if self.cfg.get("argmax"):
                local_choice = int(np.argmax(score[eligible_positions]))
                choice = eligible_positions[local_choice]
                prob = np.zeros(len(legal), dtype=np.float64)
                prob[choice] = 1.0
            else:
                eligible_prob = _softmax(
                    score[eligible_positions], self.cfg.get("temperature", 0.08))
                prob = np.zeros(len(legal), dtype=np.float64)
                prob[eligible_positions] = eligible_prob
                if self.cfg.get("capture"):
                    explore = self.cfg.get("exploration", 0.10)
                    prob = (1 - explore) * prob
                    prob[eligible_positions] += explore / len(eligible_positions)
                    choice = int(self.rng.choice(len(legal), p=prob))
                else:
                    bucket = int(view.now / FLOWLET_S)
                    if bucket != state.bucket:
                        state.flowlets.clear()
                        state.bucket = bucket
                    flow = (view.src, view.dst)
                    pinned = state.flowlets.get(flow)
                    if pinned in legal and pinned in [legal[p] for p in eligible_positions]:
                        choice = legal.index(pinned)
                    else:
                        key = f"{view.src}|{view.dst}|{bucket}|{view.sat}".encode()
                        u = zlib.crc32(key) / 2**32
                        choice = min(len(legal) - 1, int(np.searchsorted(prob.cumsum(), u, side="right")))
                        state.flowlets[flow] = legal[choice]
        action = legal[choice]
        index = ids[choice]
        if self.cfg.get("capture"):
            self.decisions.append((state.snap_id, view.pid, view.now, view.sat,
                                   view.ports[index].peer, view.ports[index].generation, view.hops_taken, g, x, index,
                                   float(prob[choice]), hops[index]))
        if self.cfg.get("telemetry_sat") == view.sat:
            self.traces.append({
                "at": view.now, "sat": view.sat, "pid": view.pid, "decision_id": decision_id,
                "action": action, "legal": list(legal), "directions": list(DIRS),
                "queues": [p.queue for p in view.ports], "phi_spill": state.phi,
                "z": state.shield_state, "delta_h": hops, "ego": None if ego is None else ego.tolist(),
                "cvar": None if costs is None else costs.tolist(),
                "aoi_s": [None if p.generated_at is None else view.now - p.generated_at for p in view.ports],
            })
        return action

    def audit(self):
        return {"kind": self.kind, "calls": self.calls, "telemetry": self.traces,
                "snapshots": self.snapshots, "decisions": self.decisions}


def build_adapter(cfg, seed):
    validate_adapter(cfg)
    model = None
    if cfg["kind"] == "pigou_metadrl":
        from .pigou_model import NumpyPigou
        path = Path(cfg["checkpoint_path"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != cfg["checkpoint_sha256"]:
            raise ValueError("Pigou checkpoint SHA-256 mismatch")
        model = NumpyPigou.load(path)
    return LocalForwardAdapter(cfg, seed, model)
