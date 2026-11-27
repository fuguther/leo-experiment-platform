"""Learning contracts for leo_sim (C1/C3/C4/C5/C6/C7) and canonical DDQN math.

Information boundary: every contract observes ONLY the current satellite's own
directly measured state plus its actually-arrived, non-expired local control
cache. C1 restricts the cache further to 1-hop origins; C3-C7 share the
configured ``obs_hops`` information set (``None`` means the full vis_k cache)
and differ only in representation/aggregation and AoI handling.  The kernel
applies the same hop boundary to learning-route discovery/action gating.

Canonical Double-DQN target (dependency-independent, tested here):
    a* = argmax_a Q_online(s', a)   over legal (masked) actions only
    y  = r + gamma * (1 - done) * Q_target(s', a*)
Real TensorFlow training is gated: without a working TF runtime any learning
run fails closed (LearningUnavailable) — no mock stands in for migration.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
from collections import deque
from pathlib import Path

import numpy as np

try:
    import tensorflow as tf
except ImportError:  # pragma: no cover - exercised on TF-less hosts
    tf = None

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

def _verify_checkpoint_metadata(meta, expected_contract, checkpoint_name,
                                checkpoint_sha, schema, algorithm):
    """Fail-closed provenance gate for sibling checkpoint metadata.

    The metadata file must be a regular, non-symlink artifact whose fields
    bind it to the exact checkpoint artifact being loaded: schema, algorithm,
    contract, filename and the artifact SHA.  Any mismatch or missing field
    is a LearningUnavailable (never a silent skip)."""
    if not isinstance(meta, dict):
        raise LearningUnavailable("checkpoint metadata is not a mapping")
    if meta.get("schema") != schema:
        raise LearningUnavailable(
            f"checkpoint metadata schema {meta.get('schema')!r} != {schema!r}")
    if meta.get("algorithm") != algorithm:
        raise LearningUnavailable(
            f"checkpoint metadata algorithm {meta.get('algorithm')!r} "
            f"!= {algorithm!r}")
    if meta.get("contract") != expected_contract:
        raise LearningUnavailable(
            "checkpoint contract mismatch: metadata says "
            f"{meta.get('contract')!r}, resolved config wants "
            f"{expected_contract!r}")
    if meta.get("checkpoint") != checkpoint_name:
        raise LearningUnavailable(
            f"checkpoint metadata filename {meta.get('checkpoint')!r} "
            f"!= {checkpoint_name!r}")
    if meta.get("checkpoint_sha256") != checkpoint_sha:
        raise LearningUnavailable(
            "checkpoint metadata SHA does not match the artifact")
    if meta.get("checkpoint_verified") is not True:
        raise LearningUnavailable(
            "checkpoint metadata was not verified at save time")


def _read_json_bytes(data: bytes, what: str) -> dict:
    """Decode+parse one UTF-8 JSON artifact from already-read bytes.

    Decode errors (invalid UTF-8) and parse errors must surface as
    LearningUnavailable, never as a bare UnicodeError/JSONDecodeError that
    escapes the learning contract and makes a corrupt artifact look like an
    unrelated crash.  Parsing from the same bytes that were hashed closes
    the hash-then-reopen TOCTOU window for JSON artifacts."""
    try:
        return json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LearningUnavailable(f"{what} unreadable: {exc}") from exc


def _read_checkpoint_metadata(path: Path, expected_sha, what: str):
    """Read, hash, verify and parse a sibling metadata artifact atomically.

    The metadata file must be a regular non-symlink file whose own SHA-256 is
    pinned in the resolved config.  Without this independent anchor, a
    checkpoint could be relabeled (e.g. C3 -> C4) by rewriting only the
    sibling metadata while the pinned checkpoint SHA stays valid.  Returns
    (meta, actual_sha); the SHA and the parsed content come from the SAME
    byte read, so a concurrent rewrite cannot slip a different payload past
    the pin (single-read, no hash-then-reopen for JSON)."""
    if path.is_symlink() or not path.is_file():
        raise LearningUnavailable(f"{what} missing or symbolic")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise LearningUnavailable(
            f"{what} SHA-256 not pinned in resolved config")
    data = path.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise LearningUnavailable(
            f"{what} SHA-256 differs from resolved config")
    return _read_json_bytes(data, what), actual_sha


def _resume_json(value):
    """Convert numpy/TensorFlow scalar state to deterministic JSON values."""
    if isinstance(value, np.ndarray):
        return {
            "__ndarray__": value.tolist(),
            "dtype": str(value.dtype),
            "shape": list(value.shape),
        }
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _resume_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resume_json(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise LearningUnavailable(
        f"resume state contains non-serializable value {type(value).__name__}")


def _resume_unjson(value):
    if isinstance(value, dict) and set(value) == {"__ndarray__", "dtype", "shape"}:
        try:
            arr = np.asarray(value["__ndarray__"], dtype=value["dtype"])
            if list(arr.shape) != list(value["shape"]):
                raise ValueError("array shape mismatch")
            return arr
        except (TypeError, ValueError) as exc:
            raise LearningUnavailable(f"resume ndarray is invalid: {exc}") from exc
    if isinstance(value, dict):
        return {k: _resume_unjson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resume_unjson(v) for v in value]
    return value


def _resume_file_record(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise LearningUnavailable(f"resume artifact missing or symbolic: {path.name}")
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def _write_resume_manifest(root: Path, schema: str, identity: dict) -> str:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.name == "manifest.json" or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("../") or Path(rel).is_absolute():
            raise LearningUnavailable("resume artifact path escapes bundle")
        files[rel] = _resume_file_record(path)
    if not files:
        raise LearningUnavailable("resume bundle contains no state artifacts")
    payload = {
        "schema": schema,
        "identity": identity,
        "files": files,
    }
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _verify_resume_bundle(root: Path, expected_sha: str, schema: str) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise LearningUnavailable("resume_path must be a regular directory")
    manifest = root / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise LearningUnavailable("resume manifest missing or symbolic")
    data = manifest.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise LearningUnavailable("resume manifest SHA-256 differs from resolved config")
    payload = _read_json_bytes(data, "resume manifest")
    if payload.get("schema") != schema:
        raise LearningUnavailable(
            f"resume schema {payload.get('schema')!r} != {schema!r}")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise LearningUnavailable("resume manifest has no file records")
    for rel, record in files.items():
        candidate = Path(rel)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise LearningUnavailable("resume manifest contains unsafe file path")
        path = root / candidate
        actual = _resume_file_record(path)
        if actual != record:
            raise LearningUnavailable(f"resume artifact hash mismatch: {rel}")
    return payload


def _resume_identity(cfg: dict, contract: str, algorithm: str, seed: int) -> dict:
    ident = {
        "algorithm": algorithm,
        "contract": contract,
        "seed": int(seed),
        "gamma": float(cfg["gamma"]),
        "lr": float(cfg["lr"]),
        "batch_size": int(cfg["batch_size"]),
        "replay_size": int(cfg["replay_size"]),
        "target_update_interval": int(cfg["target_update_interval"]),
        "fast_train": bool(cfg["fast_train"]),
    }
    if bool(cfg.get("include_last_hop", False)):
        ident["include_last_hop"] = True
    if cfg.get("clip_norm") is not None:
        ident["clip_norm"] = float(cfg["clip_norm"])
    return ident


def _require_resume_identity(manifest: dict, expected: dict) -> None:
    if manifest.get("identity") != expected:
        raise LearningUnavailable(
            "resume training contract identity differs from resolved config")


# Graph-state contracts: real GAT / MPNN encoders consume a k-hop local
# subgraph, not a hand-rolled fixed aggregate.  These names are new: they do
# NOT reuse the V1 C4/C5 semantics (V2's C4/C5 are cache-aggregation rules).
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


class LearningUnavailable(RuntimeError):
    """Learning execution requested without a real TensorFlow runtime."""


def _effective_cpu_count() -> int:
    """Return container-capped CPU count when cgroup quotas apply."""
    host_cpus = os.cpu_count() or 1
    try:
        v2 = Path("/sys/fs/cgroup/cpu.max")
        if v2.is_file():
            parts = v2.read_text(encoding="utf-8").strip().split()
            if len(parts) == 2 and parts[0] != "max":
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    return max(1, min(host_cpus, quota // period))
        quota_p = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
        period_p = Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
        if quota_p.is_file() and period_p.is_file():
            quota = int(quota_p.read_text(encoding="utf-8").strip())
            period = int(period_p.read_text(encoding="utf-8").strip())
            if quota > 0 and period > 0:
                return max(1, min(host_cpus, quota // period))
    except (OSError, ValueError):
        pass
    return max(1, host_cpus)


def _configure_tf_threads(tf_mod) -> None:
    """Cap TF CPU thread pools so tiny DDQN/GAT ops do not thrash many-core hosts."""
    default_threads = min(4, _effective_cpu_count())
    for env_key, setter in (
        ("TF_NUM_INTRAOP_THREADS", tf_mod.config.threading.set_intra_op_parallelism_threads),
        ("TF_NUM_INTEROP_THREADS", tf_mod.config.threading.set_inter_op_parallelism_threads),
    ):
        raw = os.environ.get(env_key)
        try:
            n = int(raw) if raw is not None else default_threads
            if n >= 1:
                setter(n)
        except (RuntimeError, ValueError):
            # TF runtime already initialized earlier in this process.
            pass


def _extract_dense_mlp(tf_mod, model):
    """Extract weights of a pure Dense MLP (linear/relu/tanh) for fast NumPy forward.

    Returns ``((w, b, act_name), ...)`` or ``None`` if the model contains any
    non-Dense layer (e.g. ``V2GraphEncoder``) or unsupported activation/bias config.
    """
    if not isinstance(model, tf_mod.keras.Model):
        return None
    layers_out = []
    for layer in getattr(model, "layers", ()):
        if isinstance(layer, tf_mod.keras.layers.InputLayer):
            continue
        if not isinstance(layer, tf_mod.keras.layers.Dense):
            return None
        if not getattr(layer, "use_bias", True):
            return None
        act_name = getattr(getattr(layer, "activation", None), "__name__", None)
        if act_name not in ("linear", "relu", "tanh"):
            return None
        weights = layer.get_weights()
        if len(weights) != 2:
            return None
        layers_out.append((
            np.asarray(weights[0], dtype=np.float32),
            np.asarray(weights[1], dtype=np.float32),
            act_name,
        ))
    return tuple(layers_out) if layers_out else None


def _numpy_mlp_forward(layers, x: np.ndarray) -> np.ndarray:
    """Evaluate extracted Dense layers identically to Keras float32 forward."""
    h = np.asarray(x, dtype=np.float32)
    for w, b, act_name in layers:
        h = h @ w + b
        if act_name == "relu":
            np.maximum(h, 0.0, out=h)
        elif act_name == "tanh":
            np.tanh(h, out=h)
    return h


class _ReplayBuffer:
    """Contiguous NumPy ring buffer preserving deque(maxlen=...) FIFO ordering."""

    def __init__(self, maxlen: int, state_dim: int, n_actions: int):
        self.maxlen = int(maxlen)
        self.state_dim = int(state_dim)
        self.n_actions = int(n_actions)
        self._cap = min(max(1, self.maxlen), 64)
        self._states = np.empty((self._cap, self.state_dim), dtype=np.float32)
        self._actions = np.empty((self._cap,), dtype=np.int64)
        self._rewards = np.empty((self._cap,), dtype=np.float32)
        self._next_states = np.empty((self._cap, self.state_dim), dtype=np.float32)
        self._masks = np.empty((self._cap, self.n_actions), dtype=bool)
        self._dones = np.empty((self._cap,), dtype=bool)
        self._pos = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def clear(self) -> None:
        self._pos = 0
        self._size = 0

    def _grow(self) -> None:
        new_cap = min(self.maxlen, max(self._cap * 2, 1))
        states = np.empty((new_cap, self.state_dim), dtype=np.float32)
        actions = np.empty((new_cap,), dtype=np.int64)
        rewards = np.empty((new_cap,), dtype=np.float32)
        next_states = np.empty((new_cap, self.state_dim), dtype=np.float32)
        masks = np.empty((new_cap, self.n_actions), dtype=bool)
        dones = np.empty((new_cap,), dtype=bool)
        n = self._size
        if n:
            states[:n] = self._states[:n]
            actions[:n] = self._actions[:n]
            rewards[:n] = self._rewards[:n]
            next_states[:n] = self._next_states[:n]
            masks[:n] = self._masks[:n]
            dones[:n] = self._dones[:n]
        self._states = states
        self._actions = actions
        self._rewards = rewards
        self._next_states = next_states
        self._masks = masks
        self._dones = dones
        self._cap = new_cap
        self._pos = n

    def append(self, transition) -> None:
        s, a, r, ns, m, d = transition
        if self._size == self._cap and self._cap < self.maxlen:
            self._grow()
        idx = self._pos
        self._states[idx] = s
        self._actions[idx] = a
        self._rewards[idx] = r
        self._next_states[idx] = ns
        self._masks[idx] = m
        self._dones[idx] = d
        self._pos = (idx + 1) % self._cap
        if self._size < self.maxlen:
            self._size += 1

    def _physical_indices(self, logical_indices: np.ndarray) -> np.ndarray:
        if self._size < self.maxlen:
            return logical_indices
        return (self._pos + logical_indices) % self.maxlen

    def sample_batch(self, logical_indices: np.ndarray):
        phys = self._physical_indices(logical_indices)
        return (
            self._states[phys],
            self._actions[phys],
            self._rewards[phys],
            self._next_states[phys],
            self._masks[phys],
            self._dones[phys],
        )

    def to_arrays(self):
        if self._size == 0:
            return (
                np.empty((0, self.state_dim), dtype=np.float32),
                np.empty((0,), dtype=np.int64),
                np.empty((0,), dtype=np.float32),
                np.empty((0, self.state_dim), dtype=np.float32),
                np.empty((0, self.n_actions), dtype=bool),
                np.empty((0,), dtype=bool),
            )
        order = self._physical_indices(np.arange(self._size, dtype=np.int64))
        return (
            self._states[order],
            self._actions[order],
            self._rewards[order],
            self._next_states[order],
            self._masks[order],
            self._dones[order],
        )

    def __getitem__(self, index: int):
        if index < 0:
            index += self._size
        if index < 0 or index >= self._size:
            raise IndexError("replay index out of range")
        phys = index if self._size < self.maxlen else (self._pos + index) % self.maxlen
        return (
            self._states[phys],
            int(self._actions[phys]),
            float(self._rewards[phys]),
            self._next_states[phys],
            self._masks[phys],
            bool(self._dones[phys]),
        )

    def __iter__(self):
        for i in range(self._size):
            yield self[i]


def require_tensorflow():
    """Fail closed unless a genuinely importable TensorFlow exists."""
    if tf is None:
        raise LearningUnavailable(
            "TensorFlow is not available in this environment; learning runs "
            "fail closed. Remaining gate: VM/TensorFlow verification.")
    return tf


class V2GraphEncoder(tf.keras.layers.Layer if tf is not None else object):
    """V2 GAT/MPNN graph encoder (module-level, serializable).

    Consumes the flattened observation built by ``build_graph_observation``:
      node features  [MAX_N, 18]
      adjacency      [MAX_N, MAX_N]   (adj[dst, src] = 1 for a real ISL edge)
      readout masks  [4, MAX_N]       (N/S/E/W first-hop grouping)
      own+dst tail   [OWN_FEATURES + DEST_FEATURES]
    Runs either GAT (multi-head attention) or MPNN (mean message passing)
    layers, then returns four directional readout embeddings concatenated
    with the own-state+destination tail for a shared dense Q head.

    NOTE: this class references ``tf`` at import time, so this module may only
    be imported after ``require_tensorflow()`` succeeded.  ``TensorflowDDQN``
    calls ``require_tensorflow`` in ``__init__`` before the first use of the
    graph encoder, and fail-closed tests never import tf on TF-less hosts.
    """

    def __init__(self, enc_mode, n_nodes, f_dim, h_dim, layers, heads,
                 **kwargs):
        super().__init__(**kwargs)
        if enc_mode not in ("gat", "mpnn"):
            raise ValueError(
                f"graph encoder mode must be gat/mpnn, got {enc_mode!r}")
        if enc_mode == "gat" and int(h_dim) % int(heads) != 0:
            raise ValueError("GAT hidden_dim must be divisible by num_heads")
        self.enc_mode = enc_mode
        self.n_nodes = int(n_nodes)
        self.f_dim = int(f_dim)
        self.h_dim = int(h_dim)
        self.layers = int(layers)
        self.heads = int(heads)
        self.node_in = tf.keras.layers.Dense(
            self.h_dim, activation="relu", name="node_in")
        self.dir_default = self.add_weight(
            name="dir_default", shape=(4, self.h_dim),
            initializer="zeros", trainable=True)

    def get_config(self):
        cfg = super().get_config()
        cfg.update({
            "enc_mode": self.enc_mode,
            "n_nodes": self.n_nodes,
            "f_dim": self.f_dim,
            "h_dim": self.h_dim,
            "layers": self.layers,
            "heads": self.heads,
        })
        return cfg

    @classmethod
    def from_config(cls, config):
        return cls(
            enc_mode=config["enc_mode"], n_nodes=config["n_nodes"],
            f_dim=config["f_dim"], h_dim=config["h_dim"],
            layers=config["layers"], heads=config["heads"],
        )

    def build(self, input_shape):
        if self.enc_mode == "gat":
            hd = self.h_dim // self.heads
            self.gat_W = [
                self.add_weight(name=f"gat_{l}_W",
                                shape=(self.heads, self.h_dim, hd),
                                initializer="glorot_uniform",
                                trainable=True)
                for l in range(self.layers)]
            self.gat_a_src = [
                self.add_weight(name=f"gat_{l}_a_src",
                                shape=(self.heads, hd),
                                initializer="glorot_uniform",
                                trainable=True)
                for l in range(self.layers)]
            self.gat_a_dst = [
                self.add_weight(name=f"gat_{l}_a_dst",
                                shape=(self.heads, hd),
                                initializer="glorot_uniform",
                                trainable=True)
                for l in range(self.layers)]
            self.gat_self_W = [
                self.add_weight(name=f"gat_{l}_self_W",
                                shape=(self.h_dim, self.h_dim),
                                initializer="glorot_uniform",
                                trainable=True)
                for l in range(self.layers)]
            self.gat_bias = [
                self.add_weight(name=f"gat_{l}_bias",
                                shape=(self.h_dim,),
                                initializer="zeros", trainable=True)
                for l in range(self.layers)]
        else:
            self.msg_W = [
                self.add_weight(name=f"mpnn_{l}_msg_W",
                                shape=(self.h_dim, self.h_dim),
                                initializer="glorot_uniform",
                                trainable=True)
                for l in range(self.layers)]
            self.self_W = [
                self.add_weight(name=f"mpnn_{l}_self_W",
                                shape=(self.h_dim, self.h_dim),
                                initializer="glorot_uniform",
                                trainable=True)
                for l in range(self.layers)]
            self.mpnn_bias = [
                self.add_weight(name=f"mpnn_{l}_bias",
                                shape=(self.h_dim,),
                                initializer="zeros", trainable=True)
                for l in range(self.layers)]
        super().build(input_shape)

    def _parse(self, flat):
        n, f = self.n_nodes, self.f_dim
        node = tf.reshape(flat[:, :n * f], (-1, n, f))
        adj = tf.reshape(flat[:, n * f:n * f + n * n], (-1, n, n))
        readout = tf.reshape(
            flat[:, n * f + n * n:n * f + n * n + 4 * n],
            (-1, 4, n))
        tail = flat[:, n * f + n * n + 4 * n:]
        node_mask = node[:, :, 7:8]
        adj = adj * node_mask * tf.transpose(node_mask, [0, 2, 1])
        readout = readout * tf.transpose(node_mask, [0, 2, 1])
        return node, adj, readout, tail, node_mask

    def _gat_layer(self, h, adj, node_mask, l):
        heads, hd = self.heads, self.h_dim // self.heads
        Wh = tf.einsum("bnd,hdf->bhnf", h, self.gat_W[l])
        e_src = tf.reduce_sum(
            Wh * self.gat_a_src[l][None, :, None, :], axis=-1)
        e_dst = tf.reduce_sum(
            Wh * self.gat_a_dst[l][None, :, None, :], axis=-1)
        logits = tf.nn.leaky_relu(
            e_dst[:, :, :, None] + e_src[:, :, None, :], alpha=0.2)
        edge_mask = tf.cast(adj[:, None, :, :] > 0.0, tf.float32)
        logits = logits + (1.0 - edge_mask) * -1e9
        alpha = tf.nn.softmax(logits, axis=-1) * edge_mask
        denom = tf.reduce_sum(alpha, axis=-1, keepdims=True)
        alpha = alpha / tf.maximum(denom, 1e-9)
        msg = tf.matmul(alpha, Wh)
        msg = tf.transpose(msg, [0, 2, 1, 3])
        msg = tf.reshape(msg, (-1, self.n_nodes, heads * hd))
        out = tf.nn.relu(tf.matmul(h, self.gat_self_W[l]) + msg
                         + self.gat_bias[l])
        return out * node_mask

    def _mpnn_layer(self, h, adj, node_mask, l):
        src_msg = tf.matmul(h, self.msg_W[l])
        deg = tf.reduce_sum(adj, axis=-1, keepdims=True)
        msg = tf.matmul(adj, src_msg) / tf.maximum(deg, 1.0)
        out = tf.nn.relu(tf.matmul(h, self.self_W[l]) + msg
                         + self.mpnn_bias[l])
        return out * node_mask

    def call(self, flat, training=False):
        node, adj, readout, tail, node_mask = self._parse(flat)
        h = self.node_in(node) * node_mask
        for l in range(self.layers):
            if self.enc_mode == "gat":
                h = self._gat_layer(h, adj, node_mask, l)
            else:
                h = self._mpnn_layer(h, adj, node_mask, l)
        counts = tf.reduce_sum(readout, axis=-1, keepdims=True)
        dir_sum = tf.matmul(readout, h)
        dir_mean = dir_sum / tf.maximum(counts, 1.0)
        has_dir = tf.cast(counts > 0.0, tf.float32)
        dir_emb = has_dir * dir_mean \
            + (1.0 - has_dir) * self.dir_default[None, :, :]
        return tf.concat(
            [tf.reshape(dir_emb, (-1, 4 * self.h_dim)), tail], axis=1)


def _graph_custom_objects():
    return {"V2GraphEncoder": V2GraphEncoder}


class TensorflowDDQN:
    """Small shared-policy Double-DQN used by the V2 hop-by-hop runtime.

    The kernel, not the network, constructs the destination-aware legal mask
    from arrived local control state.  Consequently exploration cannot choose
    a hidden-global, looping, full-queue or geometrically unavailable action.
    One model is shared by all satellites, while every decision consumes only
    that satellite's local observation and legal mask.
    """

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract not in CONTRACT_DIMS:
            raise ValueError(f"unknown learning contract {contract!r}")
        self.tf = require_tensorflow()
        _configure_tf_threads(self.tf)
        # Op determinism is part of the reproducibility claim: record the
        # outcome instead of swallowing it. The value lands in diagnostics()
        # and therefore in the receipt-bound learning ledger.
        try:
            self.tf.config.experimental.enable_op_determinism()
        except Exception as exc:
            # Determinism is a declared invariant; a host that cannot enable
            # TF op determinism must not produce a run that later passes the
            # receipt gate while being potentially nondeterministic.
            raise LearningUnavailable(
                "TensorFlow op determinism could not be enabled "
                f"({type(exc).__name__}: {exc})") from exc
        self.op_determinism = True
        self.tf.keras.utils.set_random_seed(int(seed))
        # TensorFlow refuses to lazily create its global generator after
        # deterministic ops are enabled.  Install an explicit generator so
        # its state can be included in an exact-resume bundle rather than
        # silently omitting one of the training RNGs.
        try:
            self.tf.random.set_global_generator(
                self.tf.random.Generator.from_seed(int(seed)))
        except Exception as exc:
            raise LearningUnavailable(
                "TensorFlow global RNG could not be initialized for exact "
                f"resume ({type(exc).__name__}: {exc})") from exc
        self.contract = contract
        self.include_last_hop = bool(cfg.get("include_last_hop", False))
        self.input_dim = contract_dim(contract, self.include_last_hop)
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.replay = _ReplayBuffer(int(cfg["replay_size"]), self.input_dim, len(ACTIONS))
        self.mode = cfg["mode"]
        self._resume_time_offset_s = 0.0
        self._last_now = 0.0
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.resume_manifest_sha256 = None
        self.resume_verified = False
        checkpoint = cfg.get("checkpoint_path")
        if checkpoint:
            path = Path(checkpoint)
            if not path.is_file():
                raise LearningUnavailable(f"DDQN checkpoint not found: {path}")
            actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual_sha != cfg.get("checkpoint_sha256"):
                raise LearningUnavailable(
                    "DDQN checkpoint SHA-256 differs from resolved config")
            meta_path = path.parent / "metadata.json"
            meta, meta_sha = _read_checkpoint_metadata(
                meta_path, cfg.get("checkpoint_metadata_sha256"),
                "DDQN checkpoint metadata.json")
            _verify_checkpoint_metadata(
                meta, self.contract, path.name, actual_sha,
                "leo-sim-ddqn/v1", "ddqn")
            # NOTE: the model bytes are hashed above and keras then re-opens
            # the pathname to load.  Concurrent replacement between the two
            # reads is explicitly OUT of scope for this artifact threat model
            # (single-user local research artifacts, no adversarial writer);
            # closing it fully would require loading from the verified bytes.
            try:
                self.online = self.tf.keras.models.load_model(
                    path, compile=False, custom_objects=_graph_custom_objects())
            except Exception as exc:
                # Any loader failure (corrupt file, bad custom objects,
                # version mismatch) is a controlled learning failure, never
                # an unclassified crash escaping the learning contract.
                raise LearningUnavailable(
                    f"DDQN checkpoint could not be loaded: {exc}") from exc
            if tuple(self.online.input_shape) != (None, self.input_dim) \
                    or tuple(self.online.output_shape) != (None, len(ACTIONS)):
                # fail closed: graph checkpoints predate the 15->18 node
                # feature widening (GRAPH_NODE_FEAT_DIM) and must be
                # retrained, never silently reshaped
                raise LearningUnavailable(
                    "DDQN checkpoint shape does not match contract/actions")
            self.loaded_checkpoint = str(path.resolve())
            self.loaded_checkpoint_sha256 = actual_sha
            self.loaded_checkpoint_metadata_sha256 = meta_sha
        else:
            self.online = self._network()
            self.loaded_checkpoint = None
            self.loaded_checkpoint_sha256 = None
            self.loaded_checkpoint_metadata_sha256 = None
        self.target = self._network()
        self.target.set_weights(self.online.get_weights())
        opt_kwargs = {"learning_rate": float(cfg["lr"])}
        if cfg.get("clip_norm") is not None:
            opt_kwargs["clipnorm"] = float(cfg["clip_norm"])
        self.optimizer = self.tf.keras.optimizers.Adam(**opt_kwargs)
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self.last_loss = None
        # tf.function-compiled DDQN train step (bit-equivalent to the eager
        # path; ~5-6x faster). The switch is config-bound
        # (learning.fast_train, part of the resolved config SHA) — never an
        # environment variable, so one config SHA maps to exactly one
        # training execution path. fast_train=False falls back to eager for
        # equivalence checks without changing the math.
        self._fast_enabled = bool(cfg["fast_train"])
        self._fast_train_fn = None
        self._fast_train_net_id = None
        self._fast_train_tgt_id = None
        self._fast_infer_fn = None
        self._fast_infer_net_id = None
        self._weights_version = 0
        self._np_mlp_layers = None
        self._np_mlp_version = -1
        if cfg.get("resume_path") is not None:
            self._load_resume_bundle(
                Path(cfg["resume_path"]), cfg.get("resume_sha256"))

    def _network(self):
        tf = self.tf
        if self.contract in GRAPH_CONTRACTS:
            inp = tf.keras.layers.Input(shape=(self.input_dim,))
            x = V2GraphEncoder(
                enc_mode="gat" if self.contract == "GAT" else "mpnn",
                n_nodes=GRAPH_MAX_NODES, f_dim=GRAPH_NODE_FEAT_DIM,
                h_dim=64, layers=1, heads=2,
            )(inp)
            x = tf.keras.layers.Dense(64, activation="relu")(x)
            out = tf.keras.layers.Dense(len(ACTIONS), activation="linear")(x)
            return tf.keras.Model(inputs=inp, outputs=out)
        return tf.keras.Sequential([
            tf.keras.layers.Input(shape=(self.input_dim,)),
            tf.keras.layers.Dense(64, activation="relu"),
            tf.keras.layers.Dense(64, activation="relu"),
            tf.keras.layers.Dense(len(ACTIONS), activation="linear"),
        ])

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    @staticmethod
    def _mask_array(mask: dict) -> np.ndarray:
        return np.asarray([bool(mask.get(a, False)) for a in ACTIONS], dtype=bool)

    def _build_fast_infer_fn(self):
        tf = self.tf
        net = self.online
        spec = [tf.TensorSpec([1, self.input_dim], tf.float32)]

        @tf.function(input_signature=spec, reduce_retracing=True)
        def _infer(obs):
            return net(obs, training=False)

        return _infer.get_concrete_function(*spec)

    def _sync_target(self) -> None:
        if len(self.target.variables) == len(self.online.variables):
            for tgt_var, src_var in zip(self.target.variables, self.online.variables):
                tgt_var.assign(src_var)
        else:
            self.target.set_weights(self.online.get_weights())

    def _current_np_mlp_layers(self):
        if self._np_mlp_layers is None or self._np_mlp_version != self._weights_version:
            self._np_mlp_layers = _extract_dense_mlp(self.tf, self.online)
            self._np_mlp_version = self._weights_version
        return self._np_mlp_layers

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        legal = self._mask_array(mask)
        indices = np.flatnonzero(legal)
        if not len(indices):
            raise ValueError("DDQN decision requires at least one legal action")
        self._last_now = max(self._last_now, float(now))
        if self.rng.random() < self.epsilon(now):
            chosen = int(self.rng.choice(indices))
        elif len(indices) == 1:
            chosen = int(indices[0])
        else:
            obs = np.asarray(observation, dtype=np.float32).reshape(1, self.input_dim)
            if self._fast_enabled:
                np_layers = (self._current_np_mlp_layers()
                             if self.contract not in GRAPH_CONTRACTS else None)
                if np_layers is not None:
                    q = _numpy_mlp_forward(np_layers, obs)[0]
                else:
                    fn = self._fast_infer_fn
                    if fn is None or self._fast_infer_net_id != id(self.online):
                        fn = self._build_fast_infer_fn()
                        self._fast_infer_fn = fn
                        self._fast_infer_net_id = id(self.online)
                    q = fn(self.tf.constant(obs, dtype=self.tf.float32)).numpy()[0]
            else:
                q = self.online(obs, training=False).numpy()[0]
            chosen = int(indices[np.argmax(q[indices])])
        self.decisions += 1
        return ACTIONS[chosen]

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown DDQN action {action!r}")
        transition = (
            np.asarray(state, dtype=np.float32), ACTIONS.index(action),
            float(reward), np.asarray(next_state, dtype=np.float32),
            self._mask_array(next_mask), bool(done),
        )
        self.replay.append(transition)
        self.transitions += 1
        train_after = max(
            int(self.cfg["batch_size"]), int(self.cfg.get("warmup_steps", 0)))
        every = max(1, int(self.cfg.get("train_every_steps", 1)))
        if (self.mode == "train"
                and len(self.replay) >= train_after
                and (every == 1 or (self.transitions - train_after) % every == 0)):
            self._train_once()

    def _train_on_batch(self, states, actions, rewards, next_states, masks, dones) -> float:
        """Run one DDQN update step on the active online/target/optimizer state."""
        if self._fast_enabled:
            fn = self._fast_train_fn
            if (fn is None or self._fast_train_net_id != id(self.online)
                    or self._fast_train_tgt_id != id(self.target)):
                fn = self._build_fast_train_fn()
                self._fast_train_fn = fn
                self._fast_train_net_id = id(self.online)
                self._fast_train_tgt_id = id(self.target)
            loss = fn(
                self.tf.constant(states, dtype=self.tf.float32),
                self.tf.constant(actions, dtype=self.tf.int64),
                self.tf.constant(rewards, dtype=self.tf.float32),
                self.tf.constant(next_states, dtype=self.tf.float32),
                self.tf.constant(~dones, dtype=self.tf.bool),
                self.tf.constant(masks, dtype=self.tf.bool),
            )
            return float(loss.numpy())
        online_next = self.online(next_states, training=False).numpy()
        target_next = self.target(next_states, training=False).numpy()
        targets = ddqn_targets(
            online_next, target_next, masks, rewards, dones,
            float(self.cfg["gamma"]),
        ).astype(np.float32)
        with self.tf.GradientTape() as tape:
            q = self.online(states, training=True)
            chosen_q = self.tf.gather(q, actions, batch_dims=1)
            loss = self.tf.reduce_mean(self.tf.square(targets - chosen_q))
        grads = tape.gradient(loss, self.online.trainable_variables)
        self.optimizer.apply_gradients(zip(grads, self.online.trainable_variables))
        return float(loss.numpy())

    def _train_once(self) -> None:
        batch_size = int(self.cfg["batch_size"])
        indices = self.rng.choice(len(self.replay), size=batch_size, replace=False)
        states, actions, rewards, next_states, masks, dones = self.replay.sample_batch(indices)
        self.last_loss = self._train_on_batch(
            states, actions, rewards, next_states, masks, dones)
        self.train_steps += 1
        self._weights_version += 1
        if self.train_steps % int(self.cfg["target_update_interval"]) == 0:
            if self._fast_enabled:
                self._sync_target()
            else:
                self.target.set_weights(self.online.get_weights())

    def _build_fast_train_fn(self):
        tf = self.tf
        net, tgt, opt = self.online, self.target, self.optimizer
        B = int(self.cfg["batch_size"])
        S = self.input_dim
        A = len(ACTIONS)
        gamma = float(self.cfg["gamma"])
        spec = [
            tf.TensorSpec([B, S], tf.float32),      # states
            tf.TensorSpec([B], tf.int64),           # actions
            tf.TensorSpec([B], tf.float32),         # rewards
            tf.TensorSpec([B, S], tf.float32),      # next states
            tf.TensorSpec([B], tf.bool),            # not_done
            tf.TensorSpec([B, A], tf.bool),         # next action mask
        ]

        @tf.function(input_signature=spec, reduce_retracing=True)
        def _step(states, actions, rewards, next_states, not_done, next_mask):
            online_next = net(next_states, training=False)
            target_next = tgt(next_states, training=False)
            safe_mask = tf.where(
                tf.reduce_any(next_mask, axis=1, keepdims=True),
                next_mask,
                tf.ones_like(next_mask),
            )
            masked_online = tf.where(
                safe_mask, online_next, tf.cast(-1e9, online_next.dtype))
            a_star = tf.argmax(masked_online, axis=1)
            bootstrap = tf.gather(target_next, a_star, batch_dims=1)
            bootstrap = tf.where(not_done, bootstrap, tf.zeros_like(bootstrap))
            expected = tf.stop_gradient(rewards + gamma * bootstrap)
            with tf.GradientTape() as tape:
                q = net(states, training=True)
                q_a = tf.gather(q, actions, batch_dims=1)
                loss = tf.reduce_mean(tf.square(q_a - expected))
            grads = tape.gradient(loss, net.trainable_variables)
            opt.apply_gradients(zip(grads, net.trainable_variables))
            return loss

        return _step.get_concrete_function(*spec)

    def _load_resume_bundle(self, root: Path, expected_sha: str) -> None:
        if not isinstance(expected_sha, str):
            raise LearningUnavailable("resume_sha256 is required for exact resume")
        manifest = _verify_resume_bundle(
            root, expected_sha, "leo-sim-ddqn-resume/v1")
        _require_resume_identity(
            manifest, _resume_identity(self.cfg, self.contract, "ddqn", self.seed))
        files = manifest["files"]
        state_path = root / "state.json"
        replay_path = root / "replay.npz"
        prefix = root / "tf_ckpt"
        if "state.json" not in files or "replay.npz" not in files \
                or "tf_ckpt.index" not in files:
            raise LearningUnavailable("DDQN resume bundle is incomplete")
        state = _read_json_bytes(state_path.read_bytes(), "DDQN resume state")
        try:
            replay = np.load(replay_path, allow_pickle=False)
            states = np.asarray(replay["states"], dtype=np.float32)
            actions = np.asarray(replay["actions"], dtype=np.int64)
            rewards = np.asarray(replay["rewards"], dtype=np.float32)
            next_states = np.asarray(replay["next_states"], dtype=np.float32)
            masks = np.asarray(replay["masks"], dtype=bool)
            dones = np.asarray(replay["dones"], dtype=bool)
        except (OSError, KeyError, ValueError, TypeError) as exc:
            raise LearningUnavailable(f"DDQN replay state is unreadable: {exc}") from exc
        n = len(states)
        if (actions.shape != (n,) or rewards.shape != (n,)
                or next_states.shape != states.shape
                or masks.shape != (n, len(ACTIONS)) or dones.shape != (n,)
                or states.ndim != 2 or states.shape[1] != self.input_dim
                or not np.all(np.isfinite(states))
                or not np.all(np.isfinite(next_states))):
            raise LearningUnavailable("DDQN replay state shape or finiteness mismatch")
        if n > int(self.cfg["replay_size"]):
            raise LearningUnavailable("DDQN replay state exceeds configured capacity")
        self.replay.clear()
        for i in range(n):
            self.replay.append((states[i], int(actions[i]), float(rewards[i]),
                                next_states[i], masks[i], bool(dones[i])))
        try:
            self.rng.bit_generator.state = _resume_unjson(state["numpy_rng_state"])
            counters = state["counters"]
            self.decisions = int(counters["decisions"])
            self.transitions = int(counters["transitions"])
            self.train_steps = int(counters["train_steps"])
            self._resume_time_offset_s = float(state.get("elapsed_time_s", 0.0))
            if min(self.decisions, self.transitions, self.train_steps) < 0 \
                    or not math.isfinite(self._resume_time_offset_s) \
                    or self._resume_time_offset_s < 0:
                raise ValueError("negative or non-finite training counter/time")
        except (KeyError, TypeError, ValueError) as exc:
            raise LearningUnavailable(f"DDQN resume state is invalid: {exc}") from exc
        try:
            tf_rng = state.get("tensorflow_rng_state")
            if tf_rng is not None:
                self.tf.random.get_global_generator().state.assign(
                    np.asarray(_resume_unjson(tf_rng), dtype=np.int64))
        except (AttributeError, TypeError, ValueError) as exc:
            raise LearningUnavailable(f"TensorFlow RNG state is invalid: {exc}") from exc
        try:
            self.optimizer.build(self.online.trainable_variables)
        except AttributeError:
            # TensorFlow 2.13 Adam exposes build; retain a controlled fallback
            # for compatible runtimes where slots are materialized lazily.
            pass
        checkpoint = self.tf.train.Checkpoint(
            online=self.online, target=self.target, optimizer=self.optimizer)
        try:
            status = checkpoint.restore(str(prefix))
            try:
                status.assert_consumed()
            except AssertionError as exc:
                # ``Checkpoint.write`` does not persist the synthetic
                # save_counter, while a fresh Checkpoint object creates it.
                # Require every real model/optimizer object to match, and
                # tolerate only that one TensorFlow bookkeeping variable.
                message = str(exc)
                if ("Found 1 Python objects" not in message
                        or "save_counter" not in message
                        or "not assigned a value" in message):
                    raise
        except Exception as exc:
            raise LearningUnavailable(
                f"DDQN TensorFlow resume checkpoint could not be restored: {exc}") from exc
        self.loaded_resume_path = str(root.resolve())
        self.loaded_resume_sha256 = expected_sha
        self.resume_manifest_sha256 = expected_sha
        self.resume_verified = True
        self._weights_version += 1
        self._np_mlp_layers = None

    def save_resume_and_verify(self, directory: str | Path) -> dict:
        """Save and verify the complete DDQN continuation state.

        The bundle is separate from the eval model checkpoint.  It contains
        TensorFlow online/target/optimizer variables, replay, counters and
        both numpy and TensorFlow RNG state, all bound by a hashed manifest.
        """
        out = Path(directory)
        if out.is_symlink():
            raise LearningUnavailable("resume output may not be symbolic")
        out.mkdir(parents=True, exist_ok=True)
        if any(out.iterdir()):
            raise LearningUnavailable("resume output directory must be empty")
        checkpoint = self.tf.train.Checkpoint(
            online=self.online, target=self.target, optimizer=self.optimizer)
        prefix = out / "tf_ckpt"
        try:
            checkpoint.write(str(prefix))
            r_states, r_actions, r_rewards, r_next_states, r_masks, r_dones = (
                self.replay.to_arrays())
            np.savez_compressed(
                out / "replay.npz",
                states=r_states,
                actions=r_actions,
                rewards=r_rewards,
                next_states=r_next_states,
                masks=r_masks,
                dones=r_dones,
            )
            tf_state = self.tf.random.get_global_generator().state.numpy()
            state = {
                "numpy_rng_state": _resume_json(self.rng.bit_generator.state),
                "tensorflow_rng_state": _resume_json(tf_state),
                "elapsed_time_s": float(self._resume_time_offset_s + self._last_now),
                "counters": {
                    "decisions": int(self.decisions),
                    "transitions": int(self.transitions),
                    "train_steps": int(self.train_steps),
                },
            }
            (out / "state.json").write_text(
                json.dumps(state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            sha = _write_resume_manifest(
                out, "leo-sim-ddqn-resume/v1",
                _resume_identity(self.cfg, self.contract, "ddqn", self.seed))
            verified = _verify_resume_bundle(
                out, sha, "leo-sim-ddqn-resume/v1")
        except Exception as exc:
            if isinstance(exc, LearningUnavailable):
                raise
            raise LearningUnavailable(f"DDQN resume save failed: {exc}") from exc
        self.resume_manifest_sha256 = sha
        self.resume_verified = True
        return {
            "resume_path": str(out),
            "resume_sha256": sha,
            "resume_verified": bool(verified),
        }

    def save_and_verify(self, directory: str | Path) -> dict:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        model_path = out / "online.keras"
        resume = self.save_resume_and_verify(out / "resume")
        self.online.save(model_path)
        checkpoint_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
        loaded = self.tf.keras.models.load_model(
            model_path, compile=False, custom_objects=_graph_custom_objects())
        probe = np.linspace(-0.5, 0.5, self.input_dim, dtype=np.float32)[None, :]
        before = self.online(probe, training=False).numpy()
        after = loaded(probe, training=False).numpy()
        verified = bool(np.allclose(before, after, rtol=0.0, atol=1e-7))
        metadata = self.diagnostics()
        # A1: a checkpoint must DECLARE its observation contract, otherwise a
        # reader cannot tell "trained for C3 with this feature order" from
        # "some 40-wide vector".  The block is produced by the same module that
        # defines the observation, and the reader recomputes the contract id
        # from the declared fields, so the two cannot drift apart silently.
        from . import inference as _inference

        feature_order = (
            _inference.OBSERVATION_FEATURE_ORDER_LAST_HOP
            if self.include_last_hop
            else _inference.OBSERVATION_FEATURE_ORDER
        )
        metadata.update(_inference.observation_contract_fields(
            contract=self.contract, feature_width=self.input_dim,
            feature_order=feature_order,
            normalisation={"mode": "none"}))
        metadata.update({
            "schema": "leo-sim-ddqn/v1",
            "checkpoint": model_path.name,
            "checkpoint_sha256": checkpoint_sha,
            "checkpoint_verified": verified,
            "probe_max_abs_error": float(np.max(np.abs(before - after))),
            "weights_purpose": str(self.cfg.get("weights_purpose", "trained")),
            **resume,
        })
        (out / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not verified:
            raise LearningUnavailable("saved DDQN checkpoint failed load/prediction verification")
        result = dict(metadata)
        result["metadata_sha256"] = hashlib.sha256(
            (out / "metadata.json").read_bytes()).hexdigest()
        return result

    def diagnostics(self) -> dict:
        return {
            "algorithm": "ddqn",
            "contract": self.contract,
            "mode": self.mode,
            "loaded_checkpoint": self.loaded_checkpoint,
            "loaded_checkpoint_sha256": self.loaded_checkpoint_sha256,
            "loaded_checkpoint_metadata_sha256":
                self.loaded_checkpoint_metadata_sha256,
            "loaded_resume_path": self.loaded_resume_path,
            "loaded_resume_sha256": self.loaded_resume_sha256,
            "resume_manifest_sha256": self.resume_manifest_sha256,
            "resume_verified": self.resume_verified,
            "actions": list(ACTIONS),
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "replay_size": len(self.replay),
            "last_loss": self.last_loss,
            "seed": self.seed,
            # execution-path bindings: which train path ran and whether TF
            # op determinism was actually enabled on this host
            "fast_train": self._fast_enabled,
            "op_determinism": self.op_determinism,
            "agent_scope": str(self.cfg.get("agent_scope", "shared")),
            "clip_norm": self.cfg.get("clip_norm"),
            "include_last_hop": self.include_last_hop,
        }


class PerSatelliteTensorflowDDQN:
    """Independent per-satellite DDQN agents (no Federated Learning weight sync).

    All satellites share one compiled TensorFlow execution engine (``_engine``)
    so TF builds a single computation graph instead of tracing N_sat models,
    while each satellite owns independent online/target weights, Adam
    optimizer slot variables, replay buffer, RNG stream, and counters.
    """

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract not in CONTRACT_DIMS:
            raise ValueError(f"unknown learning contract {contract!r}")
        engine_cfg = dict(cfg)
        engine_cfg["agent_scope"] = "shared"
        engine_cfg["checkpoint_path"] = None
        engine_cfg["checkpoint_sha256"] = None
        engine_cfg["checkpoint_metadata_sha256"] = None
        engine_cfg["resume_path"] = None
        engine_cfg["resume_sha256"] = None
        self._engine = TensorflowDDQN(contract, engine_cfg, seed)
        self.tf = self._engine.tf
        self.contract = contract
        self.include_last_hop = self._engine.include_last_hop
        self.input_dim = self._engine.input_dim
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.mode = cfg["mode"]
        self.op_determinism = self._engine.op_determinism
        self._fast_enabled = self._engine._fast_enabled
        self.loaded_checkpoint = None
        self.loaded_checkpoint_sha256 = None
        self.loaded_checkpoint_metadata_sha256 = None
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.resume_manifest_sha256 = None
        self.resume_verified = False
        self._last_now = 0.0
        self._resume_time_offset_s = 0.0
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self.last_loss = None

        try:
            self._engine.optimizer.build(self._engine.online.trainable_variables)
        except AttributeError:
            pass
        self._template_online = [
            v.numpy().copy() for v in self._engine.online.variables
        ]
        self._init_opt_weights = [
            v.numpy().copy() for v in self._engine.optimizer.variables
        ]
        self._mlp_template = (
            _extract_dense_mlp(self.tf, self._engine.online)
            if self.contract not in GRAPH_CONTRACTS else None
        )
        self._mlp_acts = (
            tuple(act for _, _, act in self._mlp_template)
            if self._mlp_template is not None
            and len(self._template_online) == 2 * len(self._mlp_template)
            else None
        )
        self.agents: dict[int, dict] = {}
        self._active_online_sat: int | None = None
        self._active_target_sat: int | None = None
        self._active_opt_sat: int | None = None

    @property
    def replay_size(self) -> int:
        return sum(len(a["replay"]) for a in self.agents.values())

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    def _init_sat_weights(self, weight_rng: np.random.Generator) -> list[np.ndarray]:
        weights = []
        for arr in self._template_online:
            if arr.ndim < 2 or not np.any(arr):
                weights.append(np.zeros_like(arr))
            else:
                receptive = int(np.prod(arr.shape[:-2])) if arr.ndim > 2 else 1
                fan_in = int(arr.shape[-2]) * receptive
                fan_out = int(arr.shape[-1]) * receptive
                limit = math.sqrt(6.0 / max(1, fan_in + fan_out))
                weights.append(
                    weight_rng.uniform(-limit, limit, size=arr.shape).astype(arr.dtype)
                )
        return weights

    def _agent(self, sat_id: int) -> dict:
        sid = int(sat_id)
        if sid < 0:
            raise ValueError(f"satellite id must be non-negative, got {sid}")
        agent = self.agents.get(sid)
        if agent is None:
            w_ss, rng_ss = np.random.SeedSequence([self.seed, sid]).spawn(2)
            online_w = self._init_sat_weights(np.random.default_rng(w_ss))
            agent = {
                "online_w": online_w,
                "target_w": [w.copy() for w in online_w],
                "opt_w": [w.copy() for w in self._init_opt_weights],
                "replay": _ReplayBuffer(
                    int(self.cfg["replay_size"]), self.input_dim, len(ACTIONS)
                ),
                "rng": np.random.default_rng(rng_ss),
                "decisions": 0,
                "transitions": 0,
                "train_steps": 0,
                "last_loss": None,
                "np_layers": None,
            }
            self.agents[sid] = agent
        return agent

    def _load_online(self, sid: int, agent: dict) -> None:
        if self._active_online_sat != sid:
            for var, arr in zip(self._engine.online.variables, agent["online_w"]):
                var.assign(arr)
            self._active_online_sat = sid

    def _load_target(self, sid: int, agent: dict) -> None:
        if self._active_target_sat != sid:
            for var, arr in zip(self._engine.target.variables, agent["target_w"]):
                var.assign(arr)
            self._active_target_sat = sid

    def _load_opt(self, sid: int, agent: dict) -> None:
        if self._active_opt_sat != sid:
            for var, arr in zip(self._engine.optimizer.variables, agent["opt_w"]):
                var.assign(arr)
            self._active_opt_sat = sid

    def choose_for(self, sat_id: int, observation: np.ndarray,
                   mask: dict, now: float) -> str:
        sid = int(sat_id)
        agent = self._agent(sid)
        legal = self._engine._mask_array(mask)
        indices = np.flatnonzero(legal)
        if not len(indices):
            raise ValueError("DDQN decision requires at least one legal action")
        self._last_now = max(self._last_now, float(now))
        if agent["rng"].random() < self.epsilon(now):
            chosen = int(agent["rng"].choice(indices))
        elif len(indices) == 1:
            chosen = int(indices[0])
        else:
            obs = np.asarray(observation, dtype=np.float32).reshape(1, self.input_dim)
            if self._fast_enabled and self._mlp_acts is not None:
                np_layers = agent["np_layers"]
                if np_layers is None:
                    online_w = agent["online_w"]
                    np_layers = tuple(
                        (online_w[2 * i], online_w[2 * i + 1], act)
                        for i, act in enumerate(self._mlp_acts)
                    )
                    agent["np_layers"] = np_layers
                q = _numpy_mlp_forward(np_layers, obs)[0]
            elif self._fast_enabled:
                self._load_online(sid, agent)
                fn = self._engine._fast_infer_fn
                if fn is None or self._engine._fast_infer_net_id != id(self._engine.online):
                    fn = self._engine._build_fast_infer_fn()
                    self._engine._fast_infer_fn = fn
                    self._engine._fast_infer_net_id = id(self._engine.online)
                q = fn(self.tf.constant(obs, dtype=self.tf.float32)).numpy()[0]
            else:
                self._load_online(sid, agent)
                q = self._engine.online(obs, training=False).numpy()[0]
            chosen = int(indices[np.argmax(q[indices])])
        agent["decisions"] += 1
        self.decisions += 1
        return ACTIONS[chosen]

    def remember_for(self, sat_id: int, state, action: str, reward: float,
                     next_state, next_mask: dict, done: bool) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown DDQN action {action!r}")
        sid = int(sat_id)
        agent = self._agent(sid)
        transition = (
            np.asarray(state, dtype=np.float32),
            ACTIONS.index(action),
            float(reward),
            np.asarray(next_state, dtype=np.float32),
            self._engine._mask_array(next_mask),
            bool(done),
        )
        agent["replay"].append(transition)
        agent["transitions"] += 1
        self.transitions += 1
        train_after = max(
            int(self.cfg["batch_size"]), int(self.cfg.get("warmup_steps", 0)))
        every = max(1, int(self.cfg.get("train_every_steps", 1)))
        if (self.mode == "train"
                and len(agent["replay"]) >= train_after
                and (every == 1 or (agent["transitions"] - train_after) % every == 0)):
            batch_size = int(self.cfg["batch_size"])
            indices = agent["rng"].choice(
                len(agent["replay"]), size=batch_size, replace=False)
            states, actions, rewards, next_states, masks, dones = (
                agent["replay"].sample_batch(indices))
            self._load_online(sid, agent)
            self._load_target(sid, agent)
            self._load_opt(sid, agent)
            loss = self._engine._train_on_batch(
                states, actions, rewards, next_states, masks, dones)
            agent["online_w"] = [
                v.numpy().copy() for v in self._engine.online.variables
            ]
            agent["opt_w"] = [
                v.numpy().copy() for v in self._engine.optimizer.variables
            ]
            agent["np_layers"] = None
            agent["last_loss"] = loss
            self.last_loss = loss
            agent["train_steps"] += 1
            self.train_steps += 1
            if agent["train_steps"] % int(self.cfg["target_update_interval"]) == 0:
                agent["target_w"] = [w.copy() for w in agent["online_w"]]
                self._engine._sync_target()

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        return self.choose_for(0, observation, mask, now)

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        self.remember_for(0, state, action, reward, next_state, next_mask, done)

    def save_and_verify(self, directory: str | Path) -> dict:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        model_path = out / "online.keras"
        rep_sat = min(self.agents) if self.agents else None
        if rep_sat is not None:
            self._load_online(rep_sat, self.agents[rep_sat])
        self._engine.online.save(model_path)
        checkpoint_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
        loaded = self.tf.keras.models.load_model(
            model_path, compile=False, custom_objects=_graph_custom_objects())
        probe = np.linspace(-0.5, 0.5, self.input_dim, dtype=np.float32)[None, :]
        before = self._engine.online(probe, training=False).numpy()
        after = loaded(probe, training=False).numpy()
        verified = bool(np.allclose(before, after, rtol=0.0, atol=1e-7))

        weights_path = out / "per_satellite_weights.npz"
        sat_ids = sorted(self.agents)
        arrays: dict[str, np.ndarray] = {"sat_ids": np.asarray(sat_ids, dtype=np.int64)}
        for sid in sat_ids:
            for idx, arr in enumerate(self.agents[sid]["online_w"]):
                arrays[f"sat_{sid}_w_{idx}"] = arr
        np.savez_compressed(weights_path, **arrays)
        weights_sha = hashlib.sha256(weights_path.read_bytes()).hexdigest()
        loaded_npz = np.load(weights_path, allow_pickle=False)
        try:
            npz_verified = bool(
                np.array_equal(loaded_npz["sat_ids"], arrays["sat_ids"])
                and all(
                    np.array_equal(loaded_npz[f"sat_{sid}_w_{idx}"], arr)
                    for sid in sat_ids
                    for idx, arr in enumerate(self.agents[sid]["online_w"])
                )
            )
        finally:
            loaded_npz.close()

        metadata = self.diagnostics()
        from . import inference as _inference

        feature_order = (
            _inference.OBSERVATION_FEATURE_ORDER_LAST_HOP
            if self.include_last_hop
            else _inference.OBSERVATION_FEATURE_ORDER
        )
        metadata.update(_inference.observation_contract_fields(
            contract=self.contract, feature_width=self.input_dim,
            feature_order=feature_order,
            normalisation={"mode": "none"}))
        metadata.update({
            "schema": "leo-sim-ddqn/v1",
            "checkpoint": model_path.name,
            "checkpoint_sha256": checkpoint_sha,
            "checkpoint_verified": verified and npz_verified,
            "representative_sat": rep_sat,
            "per_satellite_weights": weights_path.name,
            "per_satellite_weights_sha256": weights_sha,
            "per_satellite_verified": npz_verified,
            "probe_max_abs_error": float(np.max(np.abs(before - after))),
            "weights_purpose": str(self.cfg.get("weights_purpose", "trained")),
        })
        (out / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if not (verified and npz_verified):
            raise LearningUnavailable(
                "saved per-satellite DDQN checkpoint failed verification")
        result = dict(metadata)
        result["metadata_sha256"] = hashlib.sha256(
            (out / "metadata.json").read_bytes()).hexdigest()
        return result

    def diagnostics(self) -> dict:
        return {
            "algorithm": "ddqn",
            "contract": self.contract,
            "mode": self.mode,
            "loaded_checkpoint": self.loaded_checkpoint,
            "loaded_checkpoint_sha256": self.loaded_checkpoint_sha256,
            "loaded_checkpoint_metadata_sha256":
                self.loaded_checkpoint_metadata_sha256,
            "loaded_resume_path": self.loaded_resume_path,
            "loaded_resume_sha256": self.loaded_resume_sha256,
            "resume_manifest_sha256": self.resume_manifest_sha256,
            "resume_verified": self.resume_verified,
            "actions": list(ACTIONS),
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "replay_size": self.replay_size,
            "last_loss": self.last_loss,
            "seed": self.seed,
            "fast_train": self._fast_enabled,
            "op_determinism": self.op_determinism,
            "agent_scope": "per_satellite",
            "n_agents": len(self.agents),
            "clip_norm": self.cfg.get("clip_norm"),
            "include_last_hop": self.include_last_hop,
        }


class TabularQLearning:
    """Tabular Q-learning baseline — migration of the legacy M1 QLearning
    class (SimulationRL.py:5682) onto the V2 learning contract.

    Semantics kept from the legacy implementation (line numbers cited):
    - Q values are initialized uniform in [0, 1) (__init__, 5703-5704;
      createQTable at 10238 is dead code and is NOT the reference);
    - exploration picks uniformly among LEGAL actions, exploitation argmaxes
      over legal actions only (5758-5769; the legacy code masks unavailable
      directions to -inf, which is argmax-equivalent to the legal set);
    - non-terminal update Q <- (1-alpha)*Q + alpha*(r + gamma*max_a' Q(s',a'))
      over legal next actions (5791-5794);
    - a terminal transition writes the terminal reward DIRECTLY into
      Q(s, a) (5743);
    - alpha default 0.25 (558), gamma default 0.99 (274).

    Deliberate contract adaptations (documented, not drift):
    - the legacy discrete 5-tuple state (getState, 9443) has no V2 analog;
      table keys are the exact float64 bytes of the V2 contract observation;
    - epsilon follows the shared V2 schedule (epsilon_start/end/decay_s),
      not the legacy LAMBDA/decayRate/GT^2 formula (5810);
    - rewards are the V2 kernel's M1 queue reward + arrive_reward (task-1
      baseline); the legacy QLearning distance reward V1 (5783) is excluded
      from v1 per plan (ANALYSIS/LEO-V2-ORIGINAL-PLAN.md:86).
    Pure numpy: no TensorFlow required.
    """

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract not in CONTRACT_DIMS:
            raise ValueError(f"unknown learning contract {contract!r}")
        self.contract = contract
        self.include_last_hop = bool(cfg.get("include_last_hop", False))
        self.input_dim = contract_dim(contract, self.include_last_hop)
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.mode = cfg["mode"]
        self.alpha = float(cfg.get("qlearning_alpha", 0.25))
        self.gamma = float(cfg["gamma"])
        self.table: dict[bytes, np.ndarray] = {}
        checkpoint = cfg.get("checkpoint_path")
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.resume_manifest_sha256 = None
        self.resume_verified = False
        if checkpoint:
            path = Path(checkpoint)
            if not path.is_file():
                raise LearningUnavailable(f"Q-learning checkpoint not found: {path}")
            data = path.read_bytes()
            actual_sha = hashlib.sha256(data).hexdigest()
            if actual_sha != cfg.get("checkpoint_sha256"):
                raise LearningUnavailable(
                    "Q-learning checkpoint SHA-256 differs from resolved config")
            payload = _read_json_bytes(data, "Q-learning checkpoint")
            if not isinstance(payload, dict):
                raise LearningUnavailable(
                    "Q-learning checkpoint payload is not a mapping")
            if payload.get("schema") != "leo-sim-qlearning-table/v1":
                raise LearningUnavailable(
                    "Q-learning checkpoint schema "
                    f"{payload.get('schema')!r} != "
                    "'leo-sim-qlearning-table/v1'")
            canonical_keys = {"schema", "contract", "entries"}
            legacy_keys = {"schema", "entries"}
            if set(payload) not in (canonical_keys, legacy_keys):
                raise LearningUnavailable(
                    "Q-learning checkpoint payload has unknown top-level "
                    f"fields or misses required ones: {sorted(payload)}")
            loaded_meta_sha = None
            if "contract" not in payload:
                # Legacy v1 tables have no contract field in the payload.
                # Migrate only when the sibling metadata.json independently
                # binds contract + filename + SHA and is itself pinned in the
                # resolved config; otherwise fail closed (never accept an
                # unverifiable provenance).
                meta_path = path.parent / "metadata.json"
                if not meta_path.is_file() or meta_path.is_symlink():
                    raise LearningUnavailable(
                        "Q-learning checkpoint contract mismatch: payload "
                        "has no contract field, resolved config "
                        f"wants {self.contract!r}")
                meta, loaded_meta_sha = _read_checkpoint_metadata(
                    meta_path, cfg.get("checkpoint_metadata_sha256"),
                    "Q-learning checkpoint metadata.json")
                _verify_checkpoint_metadata(
                    meta, self.contract, path.name, actual_sha,
                    "leo-sim-qlearning/v1", "qlearning")
            elif payload["contract"] != self.contract:
                raise LearningUnavailable(
                    "Q-learning checkpoint contract mismatch: payload "
                    f"says {payload['contract']!r}, resolved config "
                    f"wants {self.contract!r}")
            elif cfg.get("checkpoint_metadata_sha256") is not None:
                # Canonical table with an explicit metadata pin: verify and
                # record it so loader/receipt provenance semantics stay
                # consistent (a configured pin must never be ignored).
                meta_path = path.parent / "metadata.json"
                meta, loaded_meta_sha = _read_checkpoint_metadata(
                    meta_path, cfg.get("checkpoint_metadata_sha256"),
                    "Q-learning checkpoint metadata.json")
                _verify_checkpoint_metadata(
                    meta, self.contract, path.name, actual_sha,
                    "leo-sim-qlearning/v1", "qlearning")
            entries = payload.get("entries")
            if not isinstance(entries, list):
                raise LearningUnavailable("Q-learning checkpoint lacks entries")
            expected_key_bytes = self.input_dim * 8
            expected_key_dims = self.input_dim
            seen_keys = set()
            for entry in entries:
                if (not isinstance(entry, (list, tuple)) or len(entry) != 2
                        or not isinstance(entry[0], str)):
                    raise LearningUnavailable(
                        "Q-learning checkpoint entry must be "
                        "[key_hex, values]")
                try:
                    key = bytes.fromhex(entry[0])
                except ValueError as exc:
                    raise LearningUnavailable(
                        "Q-learning checkpoint state key is not valid hex: "
                        f"{exc}") from exc
                if len(key) != expected_key_bytes:
                    raise LearningUnavailable(
                        "Q-learning checkpoint state key width "
                        f"{len(key)} bytes != contract {self.contract} "
                        f"observation width {expected_key_bytes}")
                key_view = np.frombuffer(key, dtype="<f8")
                if key_view.shape != (expected_key_dims,) \
                        or not np.all(np.isfinite(key_view)):
                    # _key() always serializes a finite observation vector;
                    # an unreachable representation (NaN/Inf/extra dims)
                    # would silently miss every lookup and degrade to the
                    # zero-row fallback.
                    raise LearningUnavailable(
                        "Q-learning checkpoint state key is not a finite "
                        f"float64 {expected_key_dims}-dim observation")
                if key in seen_keys:
                    raise LearningUnavailable(
                        "Q-learning checkpoint contains duplicate state keys")
                seen_keys.add(key)
                try:
                    arr = np.asarray(entry[1], dtype=np.float64)
                except (TypeError, ValueError) as exc:
                    raise LearningUnavailable(
                        f"Q-learning checkpoint row unreadable: {exc}") from exc
                if arr.shape != (len(ACTIONS),) \
                        or not np.all(np.isfinite(arr)):
                    raise LearningUnavailable(
                        "Q-learning checkpoint row width or finiteness "
                        "mismatch")
                self.table[key] = arr
            self.loaded_checkpoint = str(path.resolve())
            self.loaded_checkpoint_sha256 = actual_sha
            self.loaded_checkpoint_metadata_sha256 = loaded_meta_sha
        else:
            self.loaded_checkpoint = None
            self.loaded_checkpoint_sha256 = None
            self.loaded_checkpoint_metadata_sha256 = None
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0  # tabular: one Q-table update per train step
        self._resume_time_offset_s = 0.0
        self._last_now = 0.0
        if cfg.get("resume_path") is not None:
            self._load_resume_bundle(
                Path(cfg["resume_path"]), cfg.get("resume_sha256"))

    @staticmethod
    def _key(observation) -> bytes:
        # Canonical little-endian float64 bytes: identical on LE hosts and
        # portable across endianness, so a checkpoint's key representation is
        # schema-bound (N3-STATE-KEY-ENDIAN).
        return np.ascontiguousarray(
            np.asarray(observation, dtype=np.float64).astype("<f8"),
        ).tobytes()

    def _row(self, observation) -> np.ndarray:
        key = self._key(observation)
        row = self.table.get(key)
        if row is None:
            if self.mode == "eval":
                # Eval must evaluate a fixed policy: an unseen state gets a
                # deterministic all-zero row (argmax = first legal action),
                # never a random-init row, and the table is not mutated.
                return np.zeros(len(ACTIONS))
            # legacy init: np.random.rand per (state, action) (5703-5704)
            row = self.rng.random(len(ACTIONS))
            self.table[key] = row
        return row

    @staticmethod
    def _legal(mask: dict) -> np.ndarray:
        legal = np.flatnonzero([bool(mask.get(a, False)) for a in ACTIONS])
        if not len(legal):
            raise ValueError("Q-learning decision requires at least one legal action")
        return legal

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        legal = self._legal(mask)
        self._last_now = max(self._last_now, float(now))
        if self.mode != "eval" and self.rng.random() < self.epsilon(now):
            chosen = int(self.rng.choice(legal))
        else:
            row = self._row(observation)
            chosen = int(legal[np.argmax(row[legal])])
        self.decisions += 1
        return ACTIONS[chosen]

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown Q-learning action {action!r}")
        row = self._row(state)
        idx = ACTIONS.index(action)
        if self.mode == "train":
            if done:
                # legacy: terminal writes the reward directly (5743)
                row[idx] = float(reward)
            else:
                next_row = self._row(next_state)
                legal_next = self._legal(next_mask)
                max_next = float(np.max(next_row[legal_next]))
                row[idx] = ((1.0 - self.alpha) * row[idx]
                            + self.alpha * (float(reward) + self.gamma * max_next))
            self.train_steps += 1
        self.transitions += 1

    def save_and_verify(self, directory: str | Path) -> dict:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        table_path = out / "q_table.json"
        resume = self.save_resume_and_verify(out / "resume")
        # F4-RUNTIME-STATE-KEY: save must never produce an artifact that the
        # eval loader would reject.  Reuse the loader's exact semantic
        # contract: canonical key width, finite little-endian float64 keys,
        # and finite 5-action rows.
        expected_key_bytes = self.input_dim * 8
        expected_key_dims = self.input_dim
        for key, row in self.table.items():
            if len(key) != expected_key_bytes:
                raise LearningUnavailable(
                    "cannot save Q-learning checkpoint: state key width "
                    f"{len(key)} != contract observation width "
                    f"{expected_key_bytes}")
            key_view = np.frombuffer(key, dtype="<f8")
            if key_view.shape != (expected_key_dims,) \
                    or not np.all(np.isfinite(key_view)):
                raise LearningUnavailable(
                    "cannot save Q-learning checkpoint: state key is not a "
                    f"finite float64 {expected_key_dims}-dim observation")
            if row.shape != (len(ACTIONS),) \
                    or not np.all(np.isfinite(row)):
                raise LearningUnavailable(
                    "cannot save Q-learning checkpoint: Q row width or "
                    "finiteness mismatch")
        payload = {
            "schema": "leo-sim-qlearning-table/v1",
            "contract": self.contract,
            "entries": [[key.hex(), [float(v) for v in row]]
                        for key, row in sorted(self.table.items())],
        }
        table_path.write_text(json.dumps(payload, sort_keys=True) + "\n",
                              encoding="utf-8")
        # F1-RESIDUAL: hash and parse the SAME bytes (single read) so the
        # verification step has no hash-then-reopen window on the save side.
        data = table_path.read_bytes()
        checkpoint_sha = hashlib.sha256(data).hexdigest()
        loaded = _read_json_bytes(data, "Q-learning checkpoint")
        verified = loaded == payload
        metadata = self.diagnostics()
        metadata.update({
            "schema": "leo-sim-qlearning/v1",
            "checkpoint": table_path.name,
            "checkpoint_sha256": checkpoint_sha,
            "checkpoint_verified": verified,
            **resume,
        })
        (out / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if not verified:
            raise LearningUnavailable(
                "saved Q-learning checkpoint failed reload verification")
        result = dict(metadata)
        result["metadata_sha256"] = hashlib.sha256(
            (out / "metadata.json").read_bytes()).hexdigest()
        return result

    def _q_resume_identity(self) -> dict:
        ident = {
            "algorithm": "qlearning",
            "contract": self.contract,
            "seed": self.seed,
            "gamma": self.gamma,
            "alpha": self.alpha,
        }
        if self.include_last_hop:
            ident["include_last_hop"] = True
        return ident

    def _load_resume_bundle(self, root: Path, expected_sha: str) -> None:
        if not isinstance(expected_sha, str):
            raise LearningUnavailable("resume_sha256 is required for exact resume")
        manifest = _verify_resume_bundle(
            root, expected_sha, "leo-sim-qlearning-resume/v1")
        _require_resume_identity(manifest, self._q_resume_identity())
        if "q_table.json" not in manifest["files"] or "state.json" not in manifest["files"]:
            raise LearningUnavailable("Q-learning resume bundle is incomplete")
        payload = _read_json_bytes(
            (root / "q_table.json").read_bytes(), "Q-learning resume table")
        if payload.get("schema") != "leo-sim-qlearning-table/v1" \
                or payload.get("contract") != self.contract:
            raise LearningUnavailable("Q-learning resume table contract mismatch")
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise LearningUnavailable("Q-learning resume table lacks entries")
        expected_key_bytes = self.input_dim * 8
        for entry in entries:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise LearningUnavailable("Q-learning resume table entry is invalid")
            try:
                key = bytes.fromhex(entry[0])
                values = np.asarray(entry[1], dtype=np.float64)
            except (TypeError, ValueError) as exc:
                raise LearningUnavailable(
                    f"Q-learning resume table entry is unreadable: {exc}") from exc
            if (len(key) != expected_key_bytes
                    or values.shape != (len(ACTIONS),)
                    or not np.all(np.isfinite(values))):
                raise LearningUnavailable("Q-learning resume table shape mismatch")
            self.table[key] = values
        state = _read_json_bytes(
            (root / "state.json").read_bytes(), "Q-learning resume state")
        try:
            self.rng.bit_generator.state = _resume_unjson(state["numpy_rng_state"])
            counters = state["counters"]
            self.decisions = int(counters["decisions"])
            self.transitions = int(counters["transitions"])
            self.train_steps = int(counters["train_steps"])
            self._resume_time_offset_s = float(state.get("elapsed_time_s", 0.0))
            if min(self.decisions, self.transitions, self.train_steps) < 0 \
                    or not math.isfinite(self._resume_time_offset_s) \
                    or self._resume_time_offset_s < 0:
                raise ValueError("negative or non-finite training counter/time")
        except (KeyError, TypeError, ValueError) as exc:
            raise LearningUnavailable(f"Q-learning resume state is invalid: {exc}") from exc
        self.loaded_resume_path = str(root.resolve())
        self.loaded_resume_sha256 = expected_sha
        self.resume_manifest_sha256 = expected_sha
        self.resume_verified = True

    def save_resume_and_verify(self, directory: str | Path) -> dict:
        """Save the complete tabular continuation state and its hash manifest."""
        out = Path(directory)
        if out.is_symlink():
            raise LearningUnavailable("resume output may not be symbolic")
        out.mkdir(parents=True, exist_ok=True)
        if any(out.iterdir()):
            raise LearningUnavailable("resume output directory must be empty")
        payload = {
            "schema": "leo-sim-qlearning-table/v1",
            "contract": self.contract,
            "entries": [[key.hex(), [float(v) for v in row]]
                        for key, row in sorted(self.table.items())],
        }
        (out / "q_table.json").write_text(
            json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        state = {
            "numpy_rng_state": _resume_json(self.rng.bit_generator.state),
            "elapsed_time_s": float(self._resume_time_offset_s + self._last_now),
            "counters": {
                "decisions": int(self.decisions),
                "transitions": int(self.transitions),
                "train_steps": int(self.train_steps),
            },
        }
        (out / "state.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        sha = _write_resume_manifest(
            out, "leo-sim-qlearning-resume/v1", self._q_resume_identity())
        verified = _verify_resume_bundle(
            out, sha, "leo-sim-qlearning-resume/v1")
        self.resume_manifest_sha256 = sha
        self.resume_verified = True
        return {
            "resume_path": str(out),
            "resume_sha256": sha,
            "resume_verified": bool(verified),
        }

    def diagnostics(self) -> dict:
        return {
            "algorithm": "qlearning",
            "contract": self.contract,
            "mode": self.mode,
            "loaded_checkpoint": self.loaded_checkpoint,
            "loaded_checkpoint_sha256": self.loaded_checkpoint_sha256,
            "loaded_checkpoint_metadata_sha256":
                self.loaded_checkpoint_metadata_sha256,
            "loaded_resume_path": self.loaded_resume_path,
            "loaded_resume_sha256": self.loaded_resume_sha256,
            "resume_manifest_sha256": self.resume_manifest_sha256,
            "resume_verified": self.resume_verified,
            "actions": list(ACTIONS),
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "table_size": len(self.table),
            "seed": self.seed,
            "agent_scope": str(self.cfg.get("agent_scope", "shared")),
            "include_last_hop": self.include_last_hop,
        }


class PerSatelliteTabularQLearning:
    """Independent per-satellite tabular Q-learning (no Federated sync)."""

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract not in CONTRACT_DIMS:
            raise ValueError(f"unknown learning contract {contract!r}")
        self.contract = contract
        self.include_last_hop = bool(cfg.get("include_last_hop", False))
        self.input_dim = contract_dim(contract, self.include_last_hop)
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.mode = cfg["mode"]
        self.alpha = float(cfg.get("qlearning_alpha", 0.25))
        self.gamma = float(cfg["gamma"])
        self.tables: dict[int, dict[bytes, np.ndarray]] = {}
        self.rngs: dict[int, np.random.Generator] = {}
        self.loaded_checkpoint = None
        self.loaded_checkpoint_sha256 = None
        self.loaded_checkpoint_metadata_sha256 = None
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.resume_manifest_sha256 = None
        self.resume_verified = False
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self._resume_time_offset_s = 0.0
        self._last_now = 0.0

    @property
    def table_size(self) -> int:
        return sum(len(tbl) for tbl in self.tables.values())

    def _agent(self, sat_id: int) -> tuple[dict[bytes, np.ndarray], np.random.Generator]:
        sid = int(sat_id)
        if sid < 0:
            raise ValueError(f"satellite id must be non-negative, got {sid}")
        tbl = self.tables.get(sid)
        if tbl is None:
            tbl = {}
            self.tables[sid] = tbl
            self.rngs[sid] = np.random.default_rng(
                np.random.SeedSequence([self.seed, sid]))
        return tbl, self.rngs[sid]

    def _row(self, sat_id: int, observation) -> np.ndarray:
        tbl, rng = self._agent(sat_id)
        key = TabularQLearning._key(observation)
        row = tbl.get(key)
        if row is None:
            if self.mode == "eval":
                return np.zeros(len(ACTIONS))
            row = rng.random(len(ACTIONS))
            tbl[key] = row
        return row

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    def choose_for(self, sat_id: int, observation: np.ndarray,
                   mask: dict, now: float) -> str:
        legal = TabularQLearning._legal(mask)
        _tbl, rng = self._agent(sat_id)
        self._last_now = max(self._last_now, float(now))
        if self.mode != "eval" and rng.random() < self.epsilon(now):
            chosen = int(rng.choice(legal))
        else:
            row = self._row(sat_id, observation)
            chosen = int(legal[np.argmax(row[legal])])
        self.decisions += 1
        return ACTIONS[chosen]

    def remember_for(self, sat_id: int, state, action: str, reward: float,
                     next_state, next_mask: dict, done: bool) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown Q-learning action {action!r}")
        row = self._row(sat_id, state)
        idx = ACTIONS.index(action)
        if self.mode == "train":
            if done:
                row[idx] = float(reward)
            else:
                next_row = self._row(sat_id, next_state)
                legal_next = TabularQLearning._legal(next_mask)
                max_next = float(np.max(next_row[legal_next]))
                row[idx] = ((1.0 - self.alpha) * row[idx]
                            + self.alpha * (float(reward) + self.gamma * max_next))
            self.train_steps += 1
        self.transitions += 1

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        return self.choose_for(0, observation, mask, now)

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        self.remember_for(0, state, action, reward, next_state, next_mask, done)

    def save_and_verify(self, directory: str | Path) -> dict:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        expected_key_bytes = self.input_dim * 8
        expected_key_dims = self.input_dim
        for tbl in self.tables.values():
            for key, row in tbl.items():
                if len(key) != expected_key_bytes:
                    raise LearningUnavailable(
                        "cannot save Q-learning checkpoint: state key width "
                        f"{len(key)} != contract observation width "
                        f"{expected_key_bytes}")
                key_view = np.frombuffer(key, dtype="<f8")
                if key_view.shape != (expected_key_dims,) \
                        or not np.all(np.isfinite(key_view)):
                    raise LearningUnavailable(
                        "cannot save Q-learning checkpoint: state key is not a "
                        f"finite float64 {expected_key_dims}-dim observation")
                if row.shape != (len(ACTIONS),) or not np.all(np.isfinite(row)):
                    raise LearningUnavailable(
                        "cannot save Q-learning checkpoint: Q row width or "
                        "finiteness mismatch")

        rep_sat = min(self.tables) if self.tables else None
        rep_tbl = self.tables[rep_sat] if rep_sat is not None else {}
        payload = {
            "schema": "leo-sim-qlearning-table/v1",
            "contract": self.contract,
            "entries": [[key.hex(), [float(v) for v in row]]
                        for key, row in sorted(rep_tbl.items())],
        }
        table_path = out / "q_table.json"
        table_path.write_text(json.dumps(payload, sort_keys=True) + "\n",
                              encoding="utf-8")
        data = table_path.read_bytes()
        checkpoint_sha = hashlib.sha256(data).hexdigest()
        loaded = _read_json_bytes(data, "Q-learning checkpoint")
        verified = loaded == payload

        all_payload = {
            "schema": "leo-sim-per-sat-qlearning-tables/v1",
            "contract": self.contract,
            "include_last_hop": self.include_last_hop,
            "satellites": {
                str(sid): [[key.hex(), [float(v) for v in row]]
                           for key, row in sorted(tbl.items())]
                for sid, tbl in sorted(self.tables.items())
            },
        }
        all_path = out / "per_satellite_q_tables.json"
        all_path.write_text(json.dumps(all_payload, sort_keys=True) + "\n",
                            encoding="utf-8")
        all_data = all_path.read_bytes()
        all_sha = hashlib.sha256(all_data).hexdigest()
        all_loaded = _read_json_bytes(all_data, "Per-satellite Q-learning tables")
        all_verified = all_loaded == all_payload

        metadata = self.diagnostics()
        metadata.update({
            "schema": "leo-sim-qlearning/v1",
            "checkpoint": table_path.name,
            "checkpoint_sha256": checkpoint_sha,
            "checkpoint_verified": verified and all_verified,
            "representative_sat": rep_sat,
            "per_satellite_q_tables": all_path.name,
            "per_satellite_q_tables_sha256": all_sha,
            "per_satellite_verified": all_verified,
        })
        (out / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if not (verified and all_verified):
            raise LearningUnavailable(
                "saved per-satellite Q-learning checkpoint failed verification")
        result = dict(metadata)
        result["metadata_sha256"] = hashlib.sha256(
            (out / "metadata.json").read_bytes()).hexdigest()
        return result

    def diagnostics(self) -> dict:
        return {
            "algorithm": "qlearning",
            "contract": self.contract,
            "mode": self.mode,
            "loaded_checkpoint": self.loaded_checkpoint,
            "loaded_checkpoint_sha256": self.loaded_checkpoint_sha256,
            "loaded_checkpoint_metadata_sha256":
                self.loaded_checkpoint_metadata_sha256,
            "loaded_resume_path": self.loaded_resume_path,
            "loaded_resume_sha256": self.loaded_resume_sha256,
            "resume_manifest_sha256": self.resume_manifest_sha256,
            "resume_verified": self.resume_verified,
            "actions": list(ACTIONS),
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "table_size": self.table_size,
            "seed": self.seed,
            "agent_scope": "per_satellite",
            "n_agents": len(self.tables),
            "include_last_hop": self.include_last_hop,
        }


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
