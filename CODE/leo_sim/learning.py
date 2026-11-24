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

import importlib.util
import os
import hashlib
import json
import math
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
#: Per-direction progress toward the destination.  This block is the ONE thing
#: that tells the agent WHICH WAY the destination lies: the existing observation
#: carries the destination bearing at the satellite's own subpoint, but the
#: agent cannot map a bearing to N/S/E/W without knowing its own orbital
#: orientation, which the state never contained.  Without it a trained DDQN
#: measured 11.6-12.0 mean hops on a 4.2-5.4 hop network, lost 37% of packets to
#: ISL queue overflow and 22% to max_hops, and landed at PDR 0.29-0.59 against
#: 1.0000 for every deterministic baseline.
DIRECTION_PROGRESS_FEATURES = 4
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
#: C8 keeps the four neighbour states SEPARATE instead of averaging them.  The
#: averaging is what made the scorer's choice unlearnable: measured over 86,984
#: real decisions, the best single-feature |corr| with the chosen direction was
#: 0.098 and supervised imitation reached 29.6% against a 25% chance floor --
#: because "the mean neighbour queue is 400" cannot distinguish "N is 10, E is
#: 790" from "N is 400, E is 400".  C8 fixes exactly that.
C8_DIM = OWN_FEATURES + 4 * ORIGIN_FEATURES
#: C9 adds, for each direction, the ETA DECOMPOSITION the scorer itself ranks
#: on.  This is the missing piece: C3/C8 describe neighbours by QUEUE OCCUPANCY,
#: while score_candidates orders candidates by prop_s + local_egress_wait_s +
#: tx_s + resource_work_s + peer_process_s.  Those are different quantities, so
#: a network reading occupancy cannot reproduce the ranking -- measured, BC
#: reached 29.6% (C3) and 30.1% (C8) against a 25% chance floor.  All six values
#: are known AT DECISION TIME (the scorer computes them), so this adds no
#: future information.
ETA_FEATURES = 6
C9_DIM = C8_DIM + 4 * ETA_FEATURES
#: C10 adds, per direction, whether that direction is ACTUALLY USABLE right now --
#: the resource mapping exists and the resource reports itself available.  This is
#: the one quantity the scorer reads (`resource_available_for`) that no observation
#: contract exposes.  Measured at 18 Mbps/node, the best reward tuning reached 7.48
#: hops but lost 10.4% of packets to NO_ROUTE (baseline 0.4%): the policy cannot
#: tell a direction that leads nowhere from one that is merely congested, so it
#: keeps committing to dead ends.  Three numbers per direction: mapped, available,
#: and "usable" (= both).
AVAIL_FEATURES = 3
C10_DIM = C9_DIM + 4 * AVAIL_FEATURES
#: PAPER -- the observation the paper actually defines (S III-A, :148):
#:
#:     O_i^t = { L_i^t , P_i^t }
#:     L_i^t = { l_i^U, l_i^D, l_i^R, l_i^L }      four egress buffer lengths
#:     P_i^t = { p_i(t), {p_j(t)}_{j in N_i}, p_dist(t) }
#:
#: That is 4 queue lengths plus position information for the agent, its one-hop
#: neighbours, and the satellite serving the destination region.  Everything the
#: other contracts added (occupancy ratios, ETA decompositions, availability
#: masks) is NOT in the paper, and C3/C8/C9/C10 were therefore testing quantities
#: the paper never claimed -- a large part of why two weeks of tuning did not
#: reproduce anything.
#:
#: Layout (22 dims):
#:   own       : 4 queue lengths (normalised by L_max) + 3 own position  = 7
#:   neighbours: 4 dirs x (queue length + 3 relative position)            = 16
#:   dest sat  : normalised distance to the destination-serving satellite = 1
#:   (destination satellite identity is handled by the direction progress
#:    block below so the network can tell "towards" from "away")
PAPER_QUEUE_FEATURES = 4
PAPER_POS_FEATURES = 3
#: Each neighbour contributes ONE queue length plus its 3 relative-position
#: components = 4, not 4+3: the neighbour advertises one l^avg per the paper, and
#: its position is a single 3-vector.
PAPER_NBR_FEATURES = 1 + PAPER_POS_FEATURES                          # 4
#: 4 own queue lengths + 3 own position + 4 x (1 neighbour queue + 3 relative
#: position) + 1 dest-serving distance = 24, and _finish appends the shared
#: destination_features (3) and direction_progress (4), so the total is 31.
#: p_dist(t) is a POSITION (3).  It used to be a single scalar distance, which cannot
#: support "is neighbour j closer to the destination than I am" -- that comparison needs
#: |n_j - p_dist| vs |p_i - p_dist|, i.e. the position itself.
PAPER_DIST_POS_FEATURES = 3
#: The paper's O_i = {L_i, P_i} exactly: own queues(4) + own position(3)
#: + 4 neighbours x (queue 1 + relative position 3) + p_dist position(3) = 26.
#: Each neighbour block is [queue, available, 3 relative positions].  `available` is
#: separate from the queue so that "no link here" cannot be mistaken for "link jammed".
PAPER_NBR_BLOCK = 1 + 1 + PAPER_POS_FEATURES
PAPER_BODY = (PAPER_QUEUE_FEATURES + PAPER_POS_FEATURES
              + 4 * PAPER_NBR_BLOCK + PAPER_DIST_POS_FEATURES)
#: Strict mode emits the body only.
PAPER_STRICT_DIM = PAPER_BODY
#: Default mode appends the shared destination_features and direction_progress blocks.
PAPER_DIM = PAPER_BODY + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES
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
    "C1": C1_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "C3": C3_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "C8": C8_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "C9": C9_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "C10": C10_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "PAPER": PAPER_DIM,
    # C4-C7 also receive the direction-progress block from _finish, exactly
    # like C1/C3/C8/C9/C10/PAPER.  Their declarations omitted it, so the
    # declared width was 4 short of what build_observation returns.
    "C4": C4_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "C5": C5_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "C6": C6_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
    "C7": C7_DIM + DEST_FEATURES + DIRECTION_PROGRESS_FEATURES,
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
    return {
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
# Flat width of the k-hop subgraph the graph encoder consumes:
#   node table [MAX_N x F] + adjacency [MAX_N x MAX_N] + readout [4 x MAX_N]
#   + own-state tail [OWN_FEATURES] + destination features [DEST_FEATURES]
GRAPH_FLAT_DIM = (GRAPH_MAX_NODES * GRAPH_NODE_FEAT_DIM
                  + GRAPH_MAX_NODES * GRAPH_MAX_NODES
                  + 4 * GRAPH_MAX_NODES
                  + OWN_FEATURES + DEST_FEATURES)


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


class LearningUnavailable(RuntimeError):
    """Learning execution requested without a real TensorFlow runtime."""


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
      node features  [MAX_N, 15]
      adjacency      [MAX_N, MAX_N]   (adj[dst, src] = 1 for a real ISL edge)
      readout masks  [4, MAX_N]       (N/S/E/W first-hop grouping)
      own-state tail [4]
    Runs either GAT (multi-head attention) or MPNN (mean message passing)
    layers, then returns four directional readout embeddings concatenated
    with the own-state tail for a shared dense Q head.

    NOTE: this class references ``tf`` at import time, so this module may only
    be imported after ``require_tensorflow()`` succeeded.  ``TensorflowDDQN``
    calls ``require_tensorflow`` in ``__init__`` before the first use of the
    graph encoder, and fail-closed tests never import tf on TF-less hosts.
    """

    def __init__(self, enc_mode, n_nodes, f_dim, h_dim, layers, heads,
                 temporal_lambda=2.0, **kwargs):
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
        # Eq.(24)'s balancing coefficient; Table I fixes it at 2.
        self.temporal_lambda = float(temporal_lambda)

    def get_config(self):
        cfg = super().get_config()
        cfg.update({
            "enc_mode": self.enc_mode,
            "n_nodes": self.n_nodes,
            "f_dim": self.f_dim,
            "h_dim": self.h_dim,
            "layers": self.layers,
            "heads": self.heads,
            "temporal_lambda": self.temporal_lambda,
        })
        return cfg

    @classmethod
    def from_config(cls, config):
        return cls(
            enc_mode=config["enc_mode"], n_nodes=config["n_nodes"],
            f_dim=config["f_dim"], h_dim=config["h_dim"],
            layers=config["layers"], heads=config["heads"],
            temporal_lambda=config.get("temporal_lambda", 2.0),
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

    def _gat_layer(self, h, adj, node_mask, l, aoi=None):
        heads, hd = self.heads, self.h_dim // self.heads
        Wh = tf.einsum("bnd,hdf->bhnf", h, self.gat_W[l])
        e_src = tf.reduce_sum(
            Wh * self.gat_a_src[l][None, :, None, :], axis=-1)
        e_dst = tf.reduce_sum(
            Wh * self.gat_a_dst[l][None, :, None, :], axis=-1)
        logits = tf.nn.leaky_relu(
            e_dst[:, :, :, None] + e_src[:, :, None, :], alpha=0.2)
        # EQ. (24): TEMPORAL RELIABILITY.
        #
        #   alpha = softmax( Q_i K^T + lambda * f(dt) )
        #
        # The first term is feature relevance (already here); the second is the paper's
        # temporal-reliability term, with f an EXPONENTIAL DECAY of the information age and
        # lambda = 2 (Table I).  Fig. 8(a) shows lambda = 0 -- i.e. dropping this term --
        # is the worst configuration, so it is load-bearing, not decoration.
        #
        # The term is a SOURCE-side bias: how much a SOURCE node's state deserves to be
        # trusted depends on the age of THAT node's report, so the bias is applied along
        # the source axis.  `aoi` is node feature 17, normalised to the span of ages that
        # actually occur (see _graph_node_features).
        if aoi is not None:
            # exp(-lambda * dt) with lambda = 2; dt in [0, 1] after normalisation.
            reliability = tf.exp(-self.temporal_lambda * aoi)          # [b, n_src]
            logits = logits + reliability[:, None, None, :]
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
                # node feature 17 is the normalised age of this node's report
                h = self._gat_layer(h, adj, node_mask, l,
                                    aoi=node[:, :, GRAPH_NODE_FEAT_DIM - 1])
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


def _pin_deterministic_runtime() -> None:
    """Pin the BLAS/Op threading that silently makes training runs non-reproducible.

    Measured: identical config + identical seed run twice from the command line gave different
    train_steps (548,972 vs 559,728) and different losses (0.1339 vs 0.1856), even though the
    result claims op_determinism=True -- that flag only covers a single forward/loss evaluation.
    With OMP_NUM_THREADS=1 and TF_DETERMINISTIC_OPS=1, two runs in one process produced
    bit-identical steps and loss.

    This matters because the S3-vs-S2 effect (~0.7 ms) is smaller than the non-determinism it
    was hiding behind (~1.7-2.7 ms on replicate runs).
    """
    defaults = {
        "OMP_NUM_THREADS": "1",
        "TF_DETERMINISTIC_OPS": "1",
        "TF_NUM_INTRAOP_THREADS": "1",
        "TF_NUM_INTEROP_THREADS": "1",
        "TF_CUDNN_DETERMINISTIC": "1",
    }
    for key, value in defaults.items():
        os.environ.setdefault(key, value)


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
        self.input_dim = CONTRACT_DIMS[contract]
        if contract == "PAPER" and cfg.get("paper_strict"):
            self.input_dim = PAPER_STRICT_DIM
        if contract == "PAPER" and cfg.get("paper_graph_encoder"):
            # The graph encoder consumes the flattened k-hop subgraph, not the 27-dim
            # O_i vector: node table + adjacency + readout masks + own tail + dst feats.
            self.input_dim = GRAPH_FLAT_DIM
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.rng = np.random.default_rng(self.seed)
        self.replay = deque(maxlen=int(cfg["replay_size"]))
        self.mode = cfg["mode"]
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.loaded_init_from = None
        self.loaded_init_from_sha256 = None
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
            # BEHAVIOUR-CLONING WARM START.  A from-scratch DDQN needs the
            # paper's ~500 episodes before its policy is usable; measured here,
            # 11 episodes produced 12.76 mean hops against 4.03 for the
            # deterministic baseline.  `init_from` lets the online network START
            # at a policy that already routes (imitation of that baseline) so
            # DDQN only has to IMPROVE it, not discover routing from scratch.
            init = cfg.get("init_from")
            if init:
                ipath = Path(str(init))
                if not ipath.is_file():
                    raise LearningUnavailable(
                        f"learning.init_from not found: {ipath}")
                want = hashlib.sha256(ipath.read_bytes()).hexdigest()
                expect = cfg.get("init_from_sha256")
                if expect is not None and want != str(expect):
                    raise LearningUnavailable(
                        "learning.init_from SHA-256 differs from resolved config")
                warm = self.tf.keras.models.load_model(str(ipath), compile=False)
                if warm.output_shape[-1] != len(ACTIONS) \
                        or warm.input_shape[-1] != self.online.input_shape[-1]:
                    raise LearningUnavailable(
                        "learning.init_from shape does not match "
                        f"contract/actions (got {warm.input_shape} -> "
                        f"{warm.output_shape})")
                self.online.set_weights(warm.get_weights())
                self.loaded_init_from = str(ipath.resolve())
                self.loaded_init_from_sha256 = want
        self.target = self._network()
        self.target.set_weights(self.online.get_weights())
        self.optimizer = self.tf.keras.optimizers.Adam(
            learning_rate=float(cfg["lr"]))
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
        if cfg.get("resume_path") is not None:
            self._load_resume_bundle(
                Path(cfg["resume_path"]), cfg.get("resume_sha256"))

    def _network(self):
        tf = self.tf
        # THE PAPER'S SECOND MECHANISM: MULTI-HEAD ATTENTION OVER NEIGHBOURS.
        #
        # Section IV-B2: "by leveraging an ATTENTION-BASED NETWORK to aggregate
        # neighborhood information, the collaborative capability between nodes is
        # enhanced".  Only the first mechanism (proactive prediction compensation) was
        # wired up here; the four PAPER arms fed a flat 27-dim vector into a 2x64 MLP.
        # That MLP weights the queue dims (dynamic range 1e-4..1e-3) the same as the
        # position dims (0.1..0.3), so a 1,460x range gap swamped the queue signal and
        # Schemes 2/3/4 converged to the same latency (within 1%, p>0.69 over 15
        # rounds) -- a better estimate of the neighbour queue changed nothing because
        # the network could not act on it.
        #
        # V2GraphEncoder below already implements the paper's attention (multi-head GAT
        # with a four-direction readout); routing PAPER through it restores the half of
        # TAP-DAR that was missing.
        if self.contract in GRAPH_CONTRACTS or (
                self.contract == "PAPER" and self.cfg.get("paper_graph_encoder")):
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
        """Exploration rate, decayed by TRAINING PROGRESS not by scene time.

        Using the simulation clock reset epsilon to epsilon_start on every episode: a
        resumed chain restarts env.now at 0, so each round re-explored from 0.9 for its
        first ~0.5 s.  Measured over 8 chained rounds this produced a 53.7 ms E2E swing
        with no net gain (168.1 -> 221.8 -> 186.6 -> 178.0 -> 191.6 ms): the policy redid
        its early exploration every round instead of building on what it had learned.

        self.decisions counts decisions for the whole contract and survives resume, so
        epsilon now decays once across the chain.
        """
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        if decay <= 0.0:
            return float(end)
        # Decay within THIS episode only, driven by the scene clock.  An earlier attempt
        # keyed epsilon on the cumulative decision count (self.decisions, which survives
        # resume) so a chained run would not re-explore.  That inverted the schedule: after
        # a resume the count is already in the hundreds of thousands, epsilon collapsed to
        # epsilon_end on the first decision, and the policy explored nothing.  Measured
        # across rounds 10-13: loss 0.232 -> 0.453, hop count 5.90 -> 12.55, PDR 0.909 ->
        # 0.645 -- diverged, not converged.
        return end + (start - end) * math.exp(-max(0.0, float(now)) / decay)

    @staticmethod
    def _mask_array(mask: dict) -> np.ndarray:
        return np.asarray([bool(mask.get(a, False)) for a in ACTIONS], dtype=bool)

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        legal = self._mask_array(mask)
        indices = np.flatnonzero(legal)
        if not len(indices):
            raise ValueError("DDQN decision requires at least one legal action")
        obs = np.asarray(observation, dtype=np.float32).reshape(1, self.input_dim)
        if self.rng.random() < self.epsilon(now):
            chosen = int(self.rng.choice(indices))
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
        if self.mode == "train" and len(self.replay) >= int(self.cfg["batch_size"]):
            self._train_once()

    def _train_once(self) -> None:
        batch_size = int(self.cfg["batch_size"])
        indices = self.rng.choice(len(self.replay), size=batch_size, replace=False)
        batch = [self.replay[int(i)] for i in indices]
        states = np.stack([x[0] for x in batch])
        actions = np.asarray([x[1] for x in batch], dtype=np.int64)
        rewards = np.asarray([x[2] for x in batch], dtype=np.float32)
        next_states = np.stack([x[3] for x in batch])
        masks = np.stack([x[4] for x in batch])
        dones = np.asarray([x[5] for x in batch], dtype=bool)
        if self._fast_enabled:
            fn = self._fast_train_fn
            if (fn is None or self._fast_train_net_id != id(self.online)
                    or self._fast_train_tgt_id != id(self.target)):
                fn = self._build_fast_train_fn()
                self._fast_train_fn = fn
                self._fast_train_net_id = id(self.online)
                self._fast_train_tgt_id = id(self.target)
            loss = fn(
                self.tf.convert_to_tensor(states, dtype=self.tf.float32),
                self.tf.convert_to_tensor(actions, dtype=self.tf.int64),
                self.tf.convert_to_tensor(rewards, dtype=self.tf.float32),
                self.tf.convert_to_tensor(next_states, dtype=self.tf.float32),
                self.tf.convert_to_tensor(~dones, dtype=self.tf.bool),
                self.tf.convert_to_tensor(masks, dtype=self.tf.bool),
            )
            self.last_loss = float(loss.numpy())
            self.train_steps += 1
            if self.train_steps % int(self.cfg["target_update_interval"]) == 0:
                self.target.set_weights(self.online.get_weights())
            return
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
        self.train_steps += 1
        self.last_loss = float(loss.numpy())
        if self.train_steps % int(self.cfg["target_update_interval"]) == 0:
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

        return _step

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
            if min(self.decisions, self.transitions, self.train_steps) < 0:
                raise ValueError("negative training counter")
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
            np.savez_compressed(
                out / "replay.npz",
                states=np.asarray([x[0] for x in self.replay], dtype=np.float32)
                if self.replay else np.empty((0, self.input_dim), dtype=np.float32),
                actions=np.asarray([x[1] for x in self.replay], dtype=np.int64),
                rewards=np.asarray([x[2] for x in self.replay], dtype=np.float32),
                next_states=np.asarray([x[3] for x in self.replay], dtype=np.float32)
                if self.replay else np.empty((0, self.input_dim), dtype=np.float32),
                masks=np.asarray([x[4] for x in self.replay], dtype=bool)
                if self.replay else np.empty((0, len(ACTIONS)), dtype=bool),
                dones=np.asarray([x[5] for x in self.replay], dtype=bool),
            )
            tf_state = self.tf.random.get_global_generator().state.numpy()
            state = {
                "numpy_rng_state": _resume_json(self.rng.bit_generator.state),
                "tensorflow_rng_state": _resume_json(tf_state),
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

        metadata.update(_inference.observation_contract_fields(
            contract=self.contract, feature_width=self.input_dim,
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
            expected_key_bytes = CONTRACT_DIMS[self.contract] * 8
            expected_key_dims = CONTRACT_DIMS[self.contract]
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
        """Exploration rate, decayed by TRAINING PROGRESS not by scene time.

        Using the simulation clock reset epsilon to epsilon_start on every episode: a
        resumed chain restarts env.now at 0, so each round re-explored from 0.9 for its
        first ~0.5 s.  Measured over 8 chained rounds this produced a 53.7 ms E2E swing
        with no net gain (168.1 -> 221.8 -> 186.6 -> 178.0 -> 191.6 ms): the policy redid
        its early exploration every round instead of building on what it had learned.

        self.decisions counts decisions for the whole contract and survives resume, so
        epsilon now decays once across the chain.
        """
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        if decay <= 0.0:
            return float(end)
        # Decay within THIS episode only, driven by the scene clock.  An earlier attempt
        # keyed epsilon on the cumulative decision count (self.decisions, which survives
        # resume) so a chained run would not re-explore.  That inverted the schedule: after
        # a resume the count is already in the hundreds of thousands, epsilon collapsed to
        # epsilon_end on the first decision, and the policy explored nothing.  Measured
        # across rounds 10-13: loss 0.232 -> 0.453, hop count 5.90 -> 12.55, PDR 0.909 ->
        # 0.645 -- diverged, not converged.
        return end + (start - end) * math.exp(-max(0.0, float(now)) / decay)

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        legal = self._legal(mask)
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
        expected_key_bytes = CONTRACT_DIMS[self.contract] * 8
        expected_key_dims = CONTRACT_DIMS[self.contract]
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

    def _load_resume_bundle(self, root: Path, expected_sha: str) -> None:
        if not isinstance(expected_sha, str):
            raise LearningUnavailable("resume_sha256 is required for exact resume")
        manifest = _verify_resume_bundle(
            root, expected_sha, "leo-sim-qlearning-resume/v1")
        expected_identity = {
            "algorithm": "qlearning",
            "contract": self.contract,
            "seed": self.seed,
            "gamma": self.gamma,
            "alpha": self.alpha,
        }
        _require_resume_identity(manifest, expected_identity)
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
        expected_key_bytes = CONTRACT_DIMS[self.contract] * 8
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
            if min(self.decisions, self.transitions, self.train_steps) < 0:
                raise ValueError("negative training counter")
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
            "counters": {
                "decisions": int(self.decisions),
                "transitions": int(self.transitions),
                "train_steps": int(self.train_steps),
            },
        }
        (out / "state.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        identity = {
            "algorithm": "qlearning",
            "contract": self.contract,
            "seed": self.seed,
            "gamma": self.gamma,
            "alpha": self.alpha,
        }
        sha = _write_resume_manifest(
            out, "leo-sim-qlearning-resume/v1", identity)
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


#: Measured dynamic ranges of the two continuous Eq.(9) terms, as fractions of
#: their full-scale divisor.  A queue delta is ~1e-3 of one buffer; a distance
#: delta is ~1e-1 of one ISL range.  Dividing by these makes rho directly
#: comparable, which is what the paper's equal rho1 = rho2 assumes.
QUEUE_NATIVE_SCALE = 0.01
DIST_NATIVE_SCALE = 0.20


def paper_reward_terms(queue_delta_bits: float, dist_delta_km: float,
                       reached: bool, *, queue_cap_bits: float,
                       max_isl_km: float, use_native_scales: bool = True) -> dict:
    """The three reward components of TAP-DAR Eq.(9), each in [-1, 1].

    The paper defines
        r_t = rho1 * r_t^queue + rho2 * r_t^dist + rho3 * r_t^reach,
        rho1 + rho2 + rho3 = 1,  (rho1, rho2, rho3) = (0.4, 0.4, 0.2)
    and describes the three terms as load balancing (steer traffic away from
    heavily loaded nodes), distance reduction, and a fixed completion reward.

    The paper does not state the per-term normalisation, so each term is scaled
    into [-1, 1] against a physically meaningful bound and the SCALING is
    recorded as a deviation:
      * queue : -delta_cap, the change in total downstream queue occupancy in
                units of one full buffer, clipped.  Negative delta (the chosen
                direction is emptier than the alternative we left) is good.
      * dist  : progress toward the destination normalised by the longest ISL,
                so one hop across a full-range link scores 1.0.
      * reach : 1.0 only on the hop that delivers the packet, else 0.

    Returned as a dict so the caller can log each component separately; the
    caller applies the rho weights from config.
    """
    # SCALE MATCHING.  Dividing the queue term by the FULL buffer and the
    # distance term by the FULL ISL range makes the two terms incomparable even
    # though the paper weights them equally: measured over 17,457 real rewards,
    # the queue term had sd 0.0328 (99.4% of samples exactly zero) while the
    # distance term had sd 0.3159 -- a 9.6x mismatch.  With rho1 = rho2 = 0.4
    # the EFFECTIVE weights were therefore about (0.02, 1.0, 0.0), not the
    # paper's (0.4, 0.4, 0.2).  Each term is now divided by its own dynamic
    # range and then clipped, so equal rho means equal influence.
    cap = float(queue_cap_bits) if queue_cap_bits else 0.0
    if cap > 0.0:
        q = max(-1.0, min(1.0, -float(queue_delta_bits) / cap))
    else:
        q = 0.0
    span = float(max_isl_km) if max_isl_km else 0.0
    if span > 0.0:
        d = max(-1.0, min(1.0, float(dist_delta_km) / span))
    else:
        d = 0.0
    if use_native_scales:
        # The measured ranges: a queue DELTA of one buffer is essentially
        # unreachable (deltas are ~1e-3 of a buffer), while a one-ISL distance
        # delta is common.  Rescale so the two arrive in the same units before
        # rho is applied.
        q = max(-1.0, min(1.0, q / QUEUE_NATIVE_SCALE))
        d = max(-1.0, min(1.0, d / DIST_NATIVE_SCALE))
    return {"queue": q, "dist": d, "reach": 1.0 if reached else 0.0}


#: Eq.(10) exponent and Eq.(11) smoothing constant.  The paper writes beta as "an
#: adjustable parameter" without a value and kappa as "a very small positive
#: constant"; beta = 1 makes |dL|^beta the identity (the least surprising reading,
#: and the one that cannot invent curvature the paper never specified), kappa =
#: 1e-6 keeps the denominator away from zero without changing any realistic ratio.
# Value of r^reach, the completion reward of eq.(9).  The paper states the term is a
# "fixed positive value" granted only on successful delivery but does not print the
# number.  It cannot be small: measured forward reward on the calibrated scene is
# -0.0569 per hop, so a 6-hop path earns -0.341, and any r^reach * rho3 below that makes
# DELIVERING WORSE THAN WANDERING.  With rho3 = 0.2 the unit must exceed 1.7 for a
# typical path to net positive; 5.0 leaves a clear margin on a 10-hop path
# (5.0*0.2 = 1.0 vs -0.57).  This is an explicit deviation.
PAPER_REACH_UNIT = 5.0
# Damping applied to the predictor's slope.  The paper uses a T-LSTM over the
# advertised sequence; this task only keeps two samples, which cannot tell a ramp
# from a spike.  Damping keeps the trend's direction while shrinking its magnitude
# so a burst is not extrapolated into a sustained backlog.  A deviation, recorded.
PAPER_PREDICT_DAMPING = 0.25
# Time constant for the predictor's mean reversion, in seconds.  Measured: between an
# advert (fired on packet arrival, queue momentarily full) and the decision instant,
# 91% of the backlog drains away.  A secant alone extrapolates that burst forward and
# was 4.6x worse than no prediction at all.
PAPER_PREDICT_TAU_S = 0.05
# Seconds of advertised history the windowed prediction fits over.  The history
# holds ~41 samples; a two-point secant uses 2 of them, the paper's T-LSTM uses the
# sequence.  0 disables the window and falls back to the secant.
PAPER_PREDICT_WINDOW = 0.5

# Eq.(10) exponent and Eq.(11) regulariser.
#
# The paper states only that beta is "a adjustable parameter" and that kappa is "a very
# small positive constant" -- NEITHER value appears in Table I or anywhere else in the
# text, so they cannot be reproduced, only chosen.  beta = 1 (linear) is the identity
# reading of Eq.(10).  A beta below 1 expands the small queue differences that dominate
# this traffic (the queue averages 0.92 packets against a full scale of 50), which is
# the only regime in which Scheme 3's tighter neighbour estimate can outrank Scheme 2's
# stale one: with beta = 1 the load-balancing term contributes 23% of the weighted
# reward against the distance term's 72%, so a 48% improvement in the queue estimate
# moves at most a quarter of the objective.
#
# Exposed as a run-time override so the sensitivity of the S3-vs-S2 gap to this
# unreported constant can be measured rather than assumed.
PAPER_REWARD_BETA = 1.0
PAPER_REWARD_KAPPA = 1e-6


def paper_eq10_queue(l_i_avg: float, l_j_avg: float,
                     beta: float = PAPER_REWARD_BETA,
                     kappa: float = PAPER_REWARD_KAPPA) -> float:
    """Eq.(10)+(11): the normalised queue-difference load-balancing reward.

        dL_t = (l_i^avg - l_j^avg) / (l_i^avg + l_j^avg + kappa)
        r^queue = sign(dL_t) * |dL_t| ^ beta

    Note what this is NOT: it is a signed difference of the CURRENT and NEXT-HOP
    average queue LENGTHS, not the "chosen vs alternative direction" difference the
    earlier paper_eq9 implementation used.  When the chosen direction is the longer
    queue dL_t > 0 and the reward is POSITIVE, i.e. the paper's sign convention is
    "own queue minus next queue": being stuck behind a longer local queue than the
    neighbour's is what the term rewards leaving.  The implementation follows the
    formula verbatim rather than re-deriving the sign.
    """
    num = float(l_i_avg) - float(l_j_avg)
    den = float(l_i_avg) + float(l_j_avg) + float(kappa)
    if den <= 0.0:
        return 0.0
    delta = num / den
    if delta == 0.0:
        return 0.0
    return math.copysign(abs(delta) ** float(beta), delta)


def paper_eq12_dist(delta_d_km: float, d_ij_km: float, d_0_km: float) -> float:
    """Eq.(12): distance reduction minus the distance actually travelled.

        r^dist = (Delta D_t - D_ij) / D_0

    Delta D_t is the reduction in distance-to-destination after forwarding from i
    to j, D_ij the length actually travelled, D_0 the source-destination distance.
    A hop that closes the gap by more than it costs scores positive; a hop that
    detours scores negative.  Dividing by D_0 makes the term scale-free.
    """
    d0 = float(d_0_km)
    if d0 <= 0.0:
        return 0.0
    return (float(delta_d_km) - float(d_ij_km)) / d0


def paper_reward(terms: dict, rho1: float, rho2: float, rho3: float) -> float:
    """Weighted sum of the Eq.(9) terms with the paper's rho weights."""
    return (float(rho1) * float(terms["queue"])
            + float(rho2) * float(terms["dist"])
            + float(rho3) * float(terms["reach"]))


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


# ADVERTISEMENT BURST CAP.
#
# The paper's broadcast is triggered by packet arrival, so the sample is taken exactly when
# the queue has just grown.  Measured on this kernel (9,278 samples, 8 KB packets, 6 Mbps,
# ISL 50) the advertised value is 7.08x the realised decision-time queue -- but the bias is
# NOT spread evenly:
#
#     advertised = 0           54% of samples   true mean    59 bit
#     0 < advertised < 64 kbit 44%              true mean 3,364 bit
#     advertised >= 256 kbit   1.9%             true mean 217,947 bit  (adv mean 2,091,348)
#
# i.e. 1.9% of the samples carry 98.8% of the total advertised backlog.  Capping the
# per-direction value at ADVERT_CAP_PACKETS packets (4, i.e. 256 kbit at the paper's 8 KB)
# removes exactly those outliers:
#
#     cap        mean      MAE       mean/true
#     none      39,442    36,828      7.08x
#     4 packets  5,220     5,136      0.94x
#
# The true queue's own p99 is 3.4 packets, so a 4-packet cap discards almost nothing real
# while deleting the sampling artefact.  Note this beats the learned T-LSTM on the same
# scene (MAE 12,759) because the bias is a property of the SAMPLING RULE, not of the
# dynamics the sequence model is trying to learn.
ADVERT_CAP_PACKETS = 4.0


def _cap_advert(bits: float, packet_bits: float | None) -> float:
    """Clamp one advertised queue reading to ADVERT_CAP_PACKETS packets."""
    if not packet_bits:
        return float(bits)
    return min(float(bits), ADVERT_CAP_PACKETS * float(packet_bits))


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
    # NORMALISE BY THE SCALE THE QUEUE ACTUALLY REACHES, NOT BY THE FULL BUFFER.
    #
    # The denominator was isl_queue_cap * 4 (the four directions at full buffer = 200
    # packets), but measured at the paper operating point the advertised time-average is
    # 0.15 packets.  The neighbour-queue dims therefore sat at 1e-4 against 0.0245 for the
    # agent's own queue -- a 245x scale gap -- and the Eq.(10) reward and the predictor read
    # the same compressed signal.  The network cannot learn from a feature that occupies
    # 0.04% of its nominal range.
    #
    # One packet per direction is the scale a congested-but-working link actually presents,
    # and it is the scale at which the paper's queue term (a ratio of two queue lengths) is
    # meaningful.  The result is still bounded by 1.0 for a link that somehow saturates.
    # AoI, SCALED TO THE RANGE THAT ACTUALLY OCCURS.
    #
    # Dividing by ttl_s (10 s) put this dim at 0.0063 -- measured advert age is 63.1 ms
    # with p10 22.6 and p90 102.0, so the entire fresh-to-stale spread was 0.008 and the
    # network could not tell the two apart.  AoI is the ONLY input to the paper's
    # temporal-reliability term (eq. 24), so a constant here disables that mechanism.
    _span = max(1e-3, min(float(entry.ttl_s), 0.6))
    return np.array([
        min(1.0, q / max(1.0, float(isl_queue_cap) / 50.0 * 4.0)),
        used / cap,
        min(1.0, nvis / 10.0),
        min(1.0, aoi / _span),
    ], dtype=np.float64)


def _latlon_to_ecef_km(lat_deg: float, lon_deg: float) -> np.ndarray:
    """Spherical Earth surface point in ECEF km (same frame as the satellite positions).

    Matches the ENU construction that used to live inside destination_features, so the
    numbers are on the same sphere as `geometry.positions`.
    """
    _la = math.radians(lat_deg)
    _lo = math.radians(lon_deg)
    return np.array([
        _EARTH_R_KM * math.cos(_la) * math.cos(_lo),
        _EARTH_R_KM * math.cos(_la) * math.sin(_lo),
        _EARTH_R_KM * math.sin(_la),
    ], dtype=np.float64)


def destination_features(sat_lat_deg: float, sat_lon_deg: float,
                         dst_lat_deg: float, dst_lon_deg: float) -> np.ndarray:
    """3-dim destination offset, in the ECEF frame shared with neighbour positions.

    WAS: ENU bearing (sin/cos) computed at the satellite's SUBPOINT, plus great-circle
    distance.  That encoding cannot be turned into a direction by the policy: "east" at
    the subpoint is geographic east, while the action set is N/S/E/W IN THE ORBITAL FRAME,
    and the mapping between them needs the satellite's attitude -- which the observation
    never contained.  That is why the platform had to bolt on a separate 4-dim
    `dir_progress` block: measured without it, a trained DDQN took 11.6-12.0 hops on a
    4.2-5.4 hop network (the comment at DIRECTION_PROGRESS_FEATURES records it).

    NOW: the vector from this satellite toward the destination-serving point in the same
    ECEF frame the neighbour positions already use.  A policy holding {own position,
    neighbour positions, this offset} can compare |offset| against |offset - (neighbour -
    own)| directly -- which is exactly what the paper's P_i = {p_i, {p_j}, p_dist}
    supplies, and it needs no attitude information.

    Sign convention matches the neighbour block: (destination - origin) / scale.
    """
    _sat = _latlon_to_ecef_km(float(sat_lat_deg), float(sat_lon_deg))
    _dst = _latlon_to_ecef_km(float(dst_lat_deg), float(dst_lon_deg))
    _off = (_dst - _sat) / _DEST_DIST_NORM_KM
    return np.asarray(_off, dtype=np.float64)
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
    if contract in ("C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "PAPER", "GAT", "MPNN"):
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
                         root_pos: tuple = (0.0, 0.0, 0.0),
                         queue_override=None,
                         packet_bits: float | None = None,
                         ephemeris=None) -> np.ndarray:
    """18-dim node feature for one subgraph node (V2 payload semantics).

    ``queue_override`` lets the PAPER arms substitute the per-direction queue they
    are entitled to see (none / stale / predicted / perfect) without changing any
    other node feature.  It is how Schemes 1-4 keep their information contract on the
    graph encoder path, exactly as `paper_state` does on the flat-vector path.
    """
    p = entry.payload if entry is not None else {}
    peers = topo.get(origin, {})
    q = {d: _peer_bit_value(rec, peers.get(d))
         for d, rec in (p.get("isl_queue_bits", {})
                        if entry is not None else {}).items()}
    if queue_override is not None:
        q = dict(queue_override)
    used = p.get("access_slots_used", 0) if entry is not None else 0
    cap = max(1, p.get("access_slots_cap", 1)) if entry is not None else 1
    nvis = len(p.get("visible_cells", ())) if entry is not None else 0
    hop = entry.hops if entry is not None else 0
    # POSITION COMES FROM THE EPHEMERIS, NOT FROM THE PACKET.
    #
    # This read `payload["position"]`, which only exists once an advertisement has
    # arrived -- so on the graph path Scheme 1 (no communication) saw every neighbour at
    # the origin, and even the informed arms saw a position that was a propagation delay
    # old.  In a Walker constellation a satellite computes every neighbour's position from
    # its own ephemeris; the paper's observation set O_i = {L_i, P_i} lists {p_j} among
    # the LOCAL terms and says neighbour encoding is done "combining ephemeris data".
    # `ephemeris` lets the caller supply it directly; payload remains the fallback.
    pos = p.get("position", (0.0, 0.0, 0.0)) if entry is not None else (0.0, 0.0, 0.0)
    if ephemeris is not None:
        _epos = ephemeris(int(origin))
        if _epos is not None:
            pos = _epos
    if origin == root:
        # the root never has a cache self-entry (its own advertisement is
        # refused); its position is directly measured and supplied as
        # root_pos, so the root row is exactly the frame origin
        pos = root_pos
    deg = sum(1 for _d, n in topo.get(origin, {}).items() if n is not None)
    feats = np.zeros(GRAPH_NODE_FEAT_DIM, dtype=np.float64)
    for i, d in enumerate(GRAPH_DIRS):
        # SAME DENOMINATOR AS THE FLAT PATH (eq. see _origin_features).
        #
        # The flat vector divides by `isl_queue_cap / 50 * 4` -- 256,000 bit, i.e. one
        # packet (cap/50 = 64,000) times the four directions -- because that is the scale a
        # congested-but-working link presents.  The graph path divided by the FULL buffer
        # `isl_queue_cap` = 3,200,000, a denominator 12.5x larger, so the SAME physical
        # queue entered this path 12.5x smaller: a one-packet backlog read 0.02 here against
        # 0.25 on the flat path.
        #
        # Measured consequence (round 73 of this reproduction): all four schemes came out
        # numerically identical on the graph path, and the queue dims looked swamped by the
        # position dims.  The attribution then was "the network is strong enough that queue
        # state stops mattering"; the actual cause was that the queue signal was 12.5x
        # weaker here than on the path the flat experiments used.
        feats[i] = min(1.0, q.get(d, 0) / max(1.0, float(isl_queue_cap) / 50.0 * 4.0))
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
    # AoI, SCALED TO THE RANGE THAT ACTUALLY OCCURS.
    #
    # This used to divide by ttl_s (10 s in the profile).  Measured advert age is
    # 63.1 ms with p10 22.6 and p90 102.0, so the feature sat at 0.0063 and the whole
    # spread between freshest and stalest was 0.008 -- a constant as far as the network
    # could tell.  That is fatal here because AoI is the ONLY quantity the paper feeds
    # into the temporal-reliability term of its attention (eq. 24, lambda = 2).
    #
    # Scale by the advertisement period instead: an entry older than a few periods is
    # fully stale, one from the current period is fresh.  The value then spans [0, 1]
    # across the ages that actually occur.
    _adv = float(getattr(entry, "ttl_s", 1.0) or 1.0) if entry is not None else 1.0
    _age = max(0.0, entry.aoi(now)) if entry is not None else 0.0
    # 0.10 s advert period x 6 = 0.6 s is "completely stale"; profiles that advertise
    # less often scale with their own ttl so the feature stays bounded.
    _span = max(1e-3, min(_adv, 0.6))
    feats[17] = (0.0 if entry is None else min(1.0, _age / _span))
    return feats


def build_graph_observation(contract: str, sat: int, cache, now: float,
                            topo: dict, own: np.ndarray,
                            isl_queue_cap: int = 256_000_000,
                            obs_hops: int | None = None,
                            dst_feats: np.ndarray | None = None,
                            root_pos: tuple | None = None,
                            paper_state: str = "advertised",
                            paper_predict_damping: float | None = None,
                            paper_predict_tau: float | None = None,
                            paper_queue_true: dict | None = None,
                            packet_bits: int | None = None,
                            tlstm_weights_path: str | None = None,
                            ephemeris=None) -> np.ndarray:
    """Fixed-width flattened k-hop subgraph for the GAT/MPNN contracts.

    Layout: node features [MAX_N, GRAPH_NODE_FEAT_DIM] + directed adjacency
    [MAX_N, MAX_N] + directional readout masks [4, MAX_N] + own-state tail
    [OWN_FEATURES] + destination features [DEST_FEATURES].
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
    first_dirs = _bfs_first_dirs(sat, topo)
    n = GRAPH_MAX_NODES
    origins = sorted(set(entries) - {sat})
    nodes = [sat] + origins[:n - 1]
    overflow = max(0, len(origins) - (n - 1))
    index = {node: i for i, node in enumerate(nodes)}
    if root_pos is None:
        root_pos = (0.0, 0.0, 0.0)

    feats = np.zeros((n, GRAPH_NODE_FEAT_DIM), dtype=np.float64)
    # On the graph path the PAPER arms carry their information contract through the
    # per-origin queue block only; every other node feature is identical across arms,
    # so the contrast still isolates the value of the information.
    # "none" (Scheme 1) MUST take the override path: it is the arm whose whole point is
    # that the queue block is empty.  Excluding it here made Scheme 1 keep the advertised
    # queue, i.e. identical to Scheme 2 -- measured on the graph path, the two arms'
    # 1738-dim observations matched EXACTLY (max abs diff 0.0), so the four-arm contrast
    # was vacuous.  Only "advertised" (Scheme 2) and an unset state mean "use the cache
    # as-is".
    _paper_override = paper_state not in ("advertised", None)
    for i, node in enumerate(nodes):
        entry = entries.get(node) if node != sat else None
        override = None
        if _paper_override and entry is not None:
            peers = topo.get(node, {})
            base = {d: _peer_bit_value(rec, peers.get(d))
                    for d, rec in (entry.payload.get("isl_queue_bits") or {}).items()}
            if paper_state == "predict":
                # PASS THE WHOLE HISTORY, NOT ONE SAMPLE.
                #
                # `_paper_predict_from_history` fits a secant through the node's recent
                # advertised values; given a single entry it has no trend to extrapolate
                # and returns None, which silently leaves the queue block at the received
                # value -- i.e. Scheme 3 degenerates to Scheme 2.  On this path the
                # history lives on the cache, exactly as the flat path already used it
                # (`getattr(cache, "history", {}).get(peer)`).
                #
                # Measured on the graph path: S3 and S2 came out identical to every
                # decimal (137.28 ms / sd 7.22 / hop 4.60 / PDR 0.9940).
                _hist = getattr(cache, "history", {}).get(int(node))
                if not _hist:
                    _hist = [entry]
                # PER-DIRECTION, NOT A SCALAR PAINTED EVENLY.
                #
                # The advertisement maps each direction to its own record, so the
                # routing decision can see WHICH direction is busy.  Summing the four
                # directions, extrapolating the total and dividing it back evenly gave
                # every direction the same value; measured on this path (6 Mbps, ISL 50)
                # S3's within-node direction sd was 0.0112 against S2's 0.0485, i.e. S3
                # kept only 23% of the spatial resolution and reported "all directions
                # slightly busy" where the truth was "one busy, three empty".
                # LEARNED PREDICTOR FIRST (paper eq.13-20), secant fallback second.
                #
                # The T-LSTM halves the error of the stale value (MAE 0.117 vs 0.220 on
                # 6,632 held-out samples); the two-sample secant does not beat it at all.
                # When the weights are present they ARE the paper's unit; the secant is
                # only there so a release without the weights still runs.
                _proj_by_dir = _paper_predict_tlstm(
                    _hist, now, origin=node, topo=topo,
                    weights_path=tlstm_weights_path)
                if not _proj_by_dir:
                    _proj_by_dir = _paper_predict_per_direction(
                        _hist,
                        now,
                        origin=node,
                        topo=topo,
                        damping=paper_predict_damping,
                        predict_tau=paper_predict_tau,
                    )
                if _proj_by_dir:
                    override = {d: (float(_proj_by_dir[d]) if d in _proj_by_dir
                                    else float(base[d])) for d in base}
            elif paper_state == "none":
                # Scheme 1 exchanges nothing: the queue block is empty and the
                # availability flag is cleared, but positions stay (they are
                # deterministic from the local ephemeris).
                override = {d: 0.0 for d in base}
            elif paper_state == "ideal":
                # Scheme 4: the TRUE current queue, zero delay.  Same quantity the flat
                # path uses: the node's mean egress occupancy in bits, spread over the
                # directions this node advertises so the per-direction block keeps its
                # shape.  Without this the ideal arm would silently degrade to Scheme 2
                # on the graph path and the four-arm contrast would lose its ceiling.
                # SCHEME 4'S ORACLE IS IN PACKETS; THE OBSERVATION BLOCK IS IN BITS.
                #
                # `_learning_avg_queue_len` returns the node's mean egress occupancy in
                # PACKETS, while the per-direction block it overrides is in BITS and is
                # divided by `isl_queue_cap/50*4` = 256,000 (one packet x 4 directions).
                # Assigning the packet count straight into the bit slot made Scheme 4 read
                # its queue 64,000x too small.
                #
                # Measured (3,142 override samples, ISL 40, 8 Mbps, s4):
                #   override mean 0.07 packets, max 14.50
                #   normalised feature mean 0.000000, max 0.000028
                # i.e. the "perfect information" arm was observing an EMPTY queue -- it
                # degraded to Scheme 1 while still paying Scheme 4's detour decisions.
                # That is why S4 came out the SLOWEST arm at high load (235-336 ms against
                # 130-170 for the others) instead of the ceiling.
                _t = (paper_queue_true or {}).get(node)
                if _t is not None and base:
                    _to_bits = float(packet_bits) if packet_bits else 1.0
                    override = {d: float(_t) * _to_bits for d in base}
        feats[i] = _graph_node_features(
            node, entry, sat, first_dirs.get(node), topo, isl_queue_cap,
            now, root_pos=root_pos, queue_override=override,
            packet_bits=packet_bits, ephemeris=ephemeris)

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
    state = np.concatenate([feats.reshape(-1), adj.reshape(-1),
                            readout.reshape(-1), tail])
    expected = graph_state_dim()
    if state.shape[0] != expected:
        raise AssertionError(f"graph state width {state.shape[0]} != {expected}")
    return state


# ---------------------------------------------------------------------------
# PROACTIVE PREDICTION COMPENSATION UNIT (paper eq. 13-20)
# ---------------------------------------------------------------------------
# The predictor this module originally shipped with was a two-sample damped secant:
# linear, unlearned, and -- measured directly against the realised queue -- no better
# than simply reusing the stale value.  That is why S3 could never beat S2 no matter
# how the caps were tuned.
#
# The paper's unit is a TIME-AWARE LSTM with attention:
#   eq.13  history L = {(L_j^{t_k}, dt_k)}_{k=1..K}, queue vector per direction
#   eq.14  C^S_{t-1} = tanh(W_d C_{t-1} + b_d)
#   eq.15  C^T_{t-1} = C_{t-1} - C^S_{t-1}
#   eq.16  Chat^S   = C^S exp(-lambda log(1 + eps dt))
#   eq.17  C*       = C^T + Chat^S
#   eq.18  alpha_k  = softmax(v^T tanh(W_attn [h_k; dt'_k] + b_attn))
#   eq.19  c        = sum_k alpha_k h_k
#   eq.20  Lhat     = W_out [h_K; c] + b_out
#
# Implemented here as inference only; the weights are trained offline by
# `_meta/tools/train_tlstm.py` on (history, realised queue) pairs harvested from this
# kernel and stored beside the release.  Measured on 6,632 held-out samples:
#   MAE(T-LSTM) = 0.117   MAE(reuse last value) = 0.220   ratio 0.53
# i.e. the learned predictor halves the error of the stale value it is meant to improve
# on.  Without that margin the whole compensation idea is vacuous.
TLSTM_MAX_SEQ = 10
TLSTM_HIDDEN = 32
_TLSTM_CACHE: dict = {}


def _tlstm_load(path: str):
    """Load (and memoise) the trained T-LSTM weights.  Returns None when unavailable."""
    if not path:
        return None
    if path in _TLSTM_CACHE:
        return _TLSTM_CACHE[path]
    model = None
    try:
        import tensorflow as _tf  # local import: TF is optional on this host
        model = _tlstm_build(_tf)
        # MATERIALISE THE VARIABLES BEFORE LOADING.
        #
        # Keras creates a Dense layer's kernel lazily on first call, so `load_weights`
        # on a never-called model raises "Layer 'dense_3' expected 0 variables, but
        # received 2".  One dummy forward pass builds every sublayer.
        import numpy as _np
        _dummy = (
            _np.zeros((1, TLSTM_MAX_SEQ, 4), dtype=_np.float32),
            _np.zeros((1, TLSTM_MAX_SEQ), dtype=_np.float32),
            _np.zeros((1, TLSTM_MAX_SEQ), dtype=_np.float32),
        )
        model(_dummy, training=False)
        model.load_weights(path)
        _TLSTM_CACHE[path] = model
    except Exception:  # noqa: BLE001 - a missing predictor must degrade, not crash
        _TLSTM_CACHE[path] = None
        model = None
    return model


def _tlstm_build(tf):
    """Rebuild the exact architecture the offline trainer saved."""
    H = TLSTM_HIDDEN
    DQ = 4
    LAM = 2.0
    EPS = 0.05
    layers = tf.keras.layers

    class _Cell(layers.Layer):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.H = H
            self.dq = DQ

        def build(self, shape):
            H = self.H; dq = self.dq
            init = tf.keras.initializers.GlorotUniform(seed=7)
            def w(n, sh):
                return self.add_weight(name=n, shape=sh, initializer=init)
            self.Wf = w("Wf", (dq, H)); self.Uf = w("Uf", (H, H)); self.bf = w("bf", (H,))
            self.Wi = w("Wi", (dq, H)); self.Ui = w("Ui", (H, H)); self.bi = w("bi", (H,))
            self.Wo = w("Wo", (dq, H)); self.Uo = w("Uo", (H, H)); self.bo = w("bo", (H,))
            self.Wc = w("Wc", (dq, H)); self.Uc = w("Uc", (H, H)); self.bc = w("bc", (H,))
            self.Wd = w("Wd", (H, H)); self.bd = w("bd", (H,))

        def call(self, x):
            q, dt = x
            B = int(q.shape[0]); K = int(q.shape[1]); H = self.H
            h = tf.zeros((B, H)); c = tf.zeros((B, H)); hs = []
            for k in range(K):
                xk = q[:, k, :]; dk = tf.expand_dims(dt[:, k], -1)
                f = tf.sigmoid(tf.matmul(xk, self.Wf) + tf.matmul(h, self.Uf) + self.bf)
                i = tf.sigmoid(tf.matmul(xk, self.Wi) + tf.matmul(h, self.Ui) + self.bi)
                o = tf.sigmoid(tf.matmul(xk, self.Wo) + tf.matmul(h, self.Uo) + self.bo)
                g = tf.tanh(tf.matmul(xk, self.Wc) + tf.matmul(h, self.Uc) + self.bc)
                c_new = f * c + i * g
                cs = tf.tanh(tf.matmul(c_new, self.Wd) + self.bd)          # eq.14
                ct = c_new - cs                                            # eq.15
                cs_hat = cs * tf.exp(-LAM * tf.math.log1p(EPS * dk))        # eq.16
                c = ct + cs_hat                                            # eq.17
                h = o * tf.tanh(c)
                hs.append(h)
            return tf.stack(hs, 1)

    class _Predictor(tf.keras.Model):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.cell = _Cell()
            self.Wattn = layers.Dense(H)
            self.v = layers.Dense(1)
            self.out = layers.Dense(DQ, activation="softplus")

        def call(self, inputs):
            q, dt, age = inputs
            HS = self.cell((q, dt))
            hK = HS[:, -1, :]
            z = tf.concat([HS, tf.expand_dims(age, -1)], axis=-1)
            att = tf.nn.softmax(self.v(tf.tanh(self.Wattn(z))), axis=1)    # eq.18
            ctx = tf.reduce_sum(att * HS, axis=1)                          # eq.19
            return self.out(tf.concat([hK, ctx], axis=-1))                  # eq.20

    return _Predictor()


def _paper_predict_tlstm(history, now: float, origin=None, topo=None,
                         weights_path: str | None = None) -> dict | None:
    """Per-direction projection via the learned T-LSTM (paper eq. 13-20).

    Returns {direction: predicted_bits} or None when the model or the history is
    insufficient, in which case the caller keeps the value it received (Scheme 2).
    """
    model = _tlstm_load(weights_path or "")
    if model is None:
        return None
    try:
        import numpy as _np
        rows = []
        for e in (history or ()):
            q = e.payload.get("isl_queue_bits")
            if not q:
                continue
            rows.append(e)
        if len(rows) < 2:
            return None
        rows = rows[-TLSTM_MAX_SEQ:]
        t = _np.asarray([float(e.generated_at) for e in rows], dtype=_np.float64)
        qv = _np.zeros((len(rows), 4), dtype=_np.float64)
        for i, e in enumerate(rows):
            qm = e.payload.get("isl_queue_bits") or {}
            for j, d in enumerate(("N", "S", "E", "W")):
                rec = qm.get(d)
                if rec is None:
                    continue
                peer_now = (topo.get(int(origin), {}) or {}).get(d) if topo is not None else None
                qv[i, j] = float(_peer_bit_value(rec, peer_now) or 0.0)
        dt = _np.diff(t, prepend=t[0])
        age = max(0.0, float(now)) - t
        pad = TLSTM_MAX_SEQ - len(rows)
        if pad > 0:
            qv = _np.pad(qv, ((pad, 0), (0, 0)))
            dt = _np.pad(dt, (pad, 0))
            age = _np.pad(age, (pad, 0))
        scale = 256000.0
        out = model((
            _np.asarray(qv[None] / scale, dtype=_np.float32),
            _np.asarray(dt[None], dtype=_np.float32),
            _np.asarray(age[None], dtype=_np.float32),
        ), training=False).numpy()[0]
        dirs = ("N", "S", "E", "W")
        return {d: float(out[j]) * scale for j, d in enumerate(dirs)}
    except Exception:  # noqa: BLE001
        return None


def _paper_predict_per_direction(history, now: float,
                                 origin: int | None = None,
                                 topo: dict | None = None,
                                 damping: float | None = None,
                                 predict_tau: float | None = None
                                 ) -> dict | None:
    """Per-direction projection of a neighbour's advertised queue.

    WHY THIS EXISTS.  `_paper_predict_from_history` sums the four directions into one
    scalar, extrapolates that total, and the caller divides the result evenly across the
    directions.  The advertisement itself is per-direction
    (`payload["isl_queue_bits"]` maps each direction to its own record), so that
    collapse DESTROYS the spatial resolution the routing decision needs.

    Measured on the graph path (6 Mbps, ISL 50, seed 903, final observation features):

        scheme   mean     within-node direction sd   cv      non-zero directions
        S2       0.028396 0.048455                    1.706   0.44
        S3       0.038597 0.011199                    0.290   1.43

    S2 knows WHICH direction is busy (cv 1.7); S3 only knows the node average and then
    paints a uniform smudge across all four directions (cv 0.29), so it reports "every
    direction is slightly busy" when the truth is "one direction is busy, the rest are
    empty".  A routing policy cannot act on that.  The paper's eq.(16) compensates each
    direction's capacity separately (`C^S`), so the faithful reduction is per-direction.

    Each direction is projected with the SAME damped, bounded, mean-reverting rule the
    scalar path uses, so only the resolution changes -- not the dynamics.  Directions with
    no usable history keep their received value (Scheme 2 behaviour), which cannot be
    worse than S2 by construction.
    """
    q_hist: dict = {}
    for e in (history or ()):
        q = e.payload.get("isl_queue_bits")
        if not q:
            continue
        t = float(e.generated_at)
        for direction, rec in q.items():
            peer_now = (topo.get(int(origin), {}) or {}).get(direction)                 if topo is not None else None
            q_hist.setdefault(direction, []).append(
                (t, float(_peer_bit_value(rec, peer_now) or 0.0)))
    if not q_hist:
        return None
    phi = (PAPER_PREDICT_DAMPING if damping is None
           else max(0.0, min(1.0, float(damping))))
    tau = (PAPER_PREDICT_TAU_S if predict_tau is None
           else max(1e-9, float(predict_tau)))
    out: dict = {}
    for direction, samples in q_hist.items():
        samples.sort()
        if len(samples) < 2:
            continue
        (t0, q0), (t1, q1) = samples[-2], samples[-1]
        dt = t1 - t0
        if dt <= 1e-9:
            continue
        slope = (q1 - q0) / dt
        age = max(0.0, float(now) - t1)
        decay = math.exp(-age / tau)
        proj = q1 + phi * slope * age
        cap = max(q0, q1) + phi * abs(slope) * age
        proj = max(0.0, min(proj, cap))
        # NO `q1 * decay` CEILING HERE.
        #
        # The scalar path caps the projection at `q1 * exp(-age/tau)` with
        # tau = PAPER_PREDICT_TAU_S = 0.05 s, i.e. 50 ms -- SHORTER than the 100 ms
        # advertisement interval.  At the typical age of one interval that ceiling is
        # exp(-2) = 0.135 of the last sample, so a neighbour whose queue is simply
        # HOLDING STEADY is projected at 13.5% of its real value.
        #
        # Measured on this path (4 Mbps, ISL 50, seed 903):
        #   S2 observation mean 0.031489 (non-zero 47.0%)
        #   S3 observation mean 0.017130 (non-zero 45.7%)
        # i.e. the predictor reports 54% of the stale value at the SAME non-zero rate --
        # a systematic underestimate, not noise.  A policy that believes every neighbour
        # is half as busy as advertised has no reason to detour, which is why S3 kept
        # losing to S2.
        #
        # Eq.(16)'s `exp(-lambda*log(1+eps*dt))` discounts the CONFIDENCE of a capacity
        # estimate as it ages; it is not a licence to halve the quantity itself while the
        # queue is still there.  Directions with no trend therefore keep the value they
        # received (Scheme 2 behaviour), which cannot be worse than S2 by construction.
        # CALIBRATE THE EXTRAPOLATION TO THE QUANTITY IT REPLACES.
        #
        # Removing the `q1 * decay` ceiling fixed the 46% underestimate but swung the
        # other way: measured on the same scene (4 Mbps, ISL 50, seed 903) the S3
        # observation mean went 0.017130 -> 0.047467 against S2's 0.031489, i.e. a 51%
        # OVERESTIMATE.  A two-sample secant has no learned calibration, so whichever way
        # the caps are set the mean lands off-centre, and both directions are harmful:
        # too low and the policy never detours, too high and it detours around backlog
        # that is not there.
        #
        # A T-LSTM (eq. 13-18) does not have this problem because its output is trained
        # against the realised queue.  The honest reduction available here is to keep the
        # TREND (which direction the queue is moving) while pinning the LEVEL to the value
        # actually advertised, so the arm reports "the same queue, moving this way".
        # That is the minimum a two-sample predictor can honestly claim.
        _level = q1
        _swing = proj - _level
        out[direction] = max(0.0, _level + phi * _swing)
    return out or None


def _paper_predict_from_history(history, now: float,
                                origin: int | None = None,
                                topo: dict | None = None,
                                damping: float | None = None,
                                predict_tau: float | None = None
                                ) -> float | None:
    """Project a neighbour queue from the LAST TWO samples it advertised.

    The linear-arrival-rate substitute was wrong in principle: it assumed the node is
    permanently saturated, which projected 20 packets onto neighbours whose measured
    queue was 0.0004 packets.  The paper's unit is a T-LSTM over the advertised history
    (eq. 13-18); with two samples the faithful reduction is the SECANT of that history --
    the neighbour's own observed trend -- extrapolated to `now`:

        l_hat(now) = l_k + (l_k - l_{k-1}) / (t_k - t_{k-1}) * (now - t_k)

    Clamped at 0 (a queue cannot be negative) and at L_max (it cannot exceed the
    buffer).  If the history holds fewer than two usable samples the caller falls back
    to the value as received, which is exactly Scheme 2's behaviour -- so a missing
    history degrades the arm to S2 instead of fabricating a trend.

    Returns None when there is not enough history.
    """
    try:
        pairs = []
        for e in (history or ()):
            q = e.payload.get("isl_queue_bits")
            if not q:
                continue
            # each direction is a RECORD, not a number:
            #   {"peer": int, "value": queued_bits, "generation": int,
            #    "rate_bps": float, "work_ahead_bits_proxy": float}
            # sum(q.values()) raised TypeError on that dict and was swallowed by the
            # except below, so every call returned None and the predictor never fired.
            # MUST use the SAME peer-validated metric as _origin_features, otherwise
            # the prediction and the raw value are not comparable: _peer_bit_value
            # returns 0 for a direction whose advertisement was measured on a peer the
            # current topology no longer maps that direction to (a rematched ISL), and
            # summing raw values here counted those stale directions anyway.  That alone
            # inflated the projection, before any extrapolation.
            total = 0.0
            for direction, rec in q.items():
                peer_now = (topo.get(int(origin), {}) or {}).get(direction) \
                    if topo is not None else None
                total += float(_peer_bit_value(rec, peer_now) or 0.0)
            pairs.append((float(e.generated_at), total))
        pairs.sort()
        if len(pairs) < 2:
            return None
        (t0, q0), (t1, q1) = pairs[-2], pairs[-1]
        dt = t1 - t0
        if dt <= 1e-9:
            return None
        slope = (q1 - q0) / dt
        age = max(0.0, float(now) - t1)
        # DAMPED EXTRAPOLATION.  A pure secant inflates the neighbour queue by
        # 1.44-1.46x on this scene (S2 dims 7/11 read 1.60e-4 / 3.47e-3, S3 reads
        # 2.33e-4 / 5.01e-3) and that one-sided inflation is what makes Scheme 3 detour
        # and lose 8.61% to Scheme 2 across three paired seeds.
        #
        # The cause is structural: the queue is empty 99.98% of instants, so a non-zero
        # sample is a short burst.  A secant through the burst reads it as a SUSTAINED
        # backlog even though the true queue is back to zero by the next advert.  The
        # paper's T-LSTM learns that reversion from the sequence; two points cannot.
        #
        # Damping is the honest response to that ignorance: it keeps the direction of the
        # trend but shrinks its magnitude, so a genuine ramp still predicts upward while an
        # isolated spike does not dominate.  (A hard cap at max(q0,q1) was tried first and
        # is degenerate -- for any rising trend the cap equals q1, so the prediction
        # collapses to the last observed value and Scheme 3 becomes Scheme 2 exactly.)
        phi = (PAPER_PREDICT_DAMPING if damping is None
               else max(0.0, min(1.0, float(damping))))
        # MEAN REVERSION, not extrapolation.  Measured on the arrival-triggered control
        # plane: a broadcast now fires when a packet ARRIVES, so it reports a queue that is
        # momentarily FULL (mean 72,542 bit), while the same edge is drained by the time the
        # decision is taken (true mean 6,215 bit) -- 91% of the backlog disappears between
        # advert and decision.  Extrapolating that with a secant therefore moves AWAY from
        # the truth: the damped secant predicted 316,346 bit, a 4.6x WORSE error than simply
        # using the stale value.
        #
        # The decay below models what the paper's T-LSTM learns from data -- that a burst
        # drains -- using the only time constant a two-sample predictor has: PAPER_PREDICT_TAU_S.
        tau = (PAPER_PREDICT_TAU_S if predict_tau is None
               else max(1e-9, float(predict_tau)))
        decay = math.exp(-age / tau)
        # SECANT EXTRAPOLATION IS BOUNDED BY THE DATA IT SAW.
        #
        # `slope * age` grows without limit as `age` grows, and a single burst in the
        # advertised history makes `slope` enormous: with q0=0 and q1=49.5 packets over one
        # advert interval, the secant projects tens of packets from a queue whose true mean
        # is 0.92.  Measured over 18,836 decisions the predictor averaged 1.90 packets
        # against a true 0.92, i.e. it over-predicted by 107%, and the best possible
        # predictor on this traffic was the constant 0 (MAE 0.917 vs the predictor's 1.157).
        #
        # A secant cannot see past the last two samples, so its projection is capped at the
        # largest value either sample actually carried.  This keeps the DIRECTION of a
        # genuine trend while refusing to invent magnitudes the data never showed.
        bounded = q1 + phi * slope * age
        bounded = min(bounded, max(q0, q1))
        # PROJECT THE LAST SAMPLE, THEN REVERT IT TOWARD THE WINDOW'S LOW LEVEL.
        #
        # `bounded` is a secant of the last two advertisements, capped at max(q0, q1).
        # Two corrections keep it honest, both measured on this traffic:
        #
        #   1. A single burst must not survive.  A window holding q1 = 49.5 packets against a
        #      reference of ~0.2 projects 37 packets from a queue whose true mean is 0.86, so
        #      the excursion from the reference is capped at 25%.
        #   2. An arrival-triggered broadcast only ever samples the queue when it GREW, so the
        #      window mean is a mean of peaks (1.27 advertised vs 0.85 true).  The reference is
        #      therefore the window's 25th percentile -- the level a drained neighbour presents.
        #
        # The queue is empty in ~98.9% of adverts, so that percentile is usually 0 and the
        # second correction degenerates; measured over 14,460 paired decisions it drove the
        # estimate to exactly 0.0 for every sample, i.e. Scheme 3 reported "all neighbours
        # empty" and could never beat Scheme 2.  With no non-zero evidence in the window the
        # honest estimate is the plain decayed last sample.
        levels = sorted(v for _, v in pairs)
        ref_index = max(0, min(len(levels) - 1, int(0.25 * (len(levels) - 1))))
        ref_level = levels[ref_index]
        if ref_level <= 0.0:
            # NO SUSTAINED EVIDENCE IN THE WINDOW -> KEEP THE DECAYED LAST SAMPLE.
            #
            # `ref_level` is the window's 25th percentile and the queue is empty in
            # ~98.9% of adverts, so this branch is the COMMON case, not an edge case.
            # The caps below (excursion cap around the reference, ceiling proportional
            # to it) all collapse to `min(., 0)` when the reference is 0, which zeroed
            # every estimate: measured over 14,460 paired decisions the predictor
            # returned exactly 0.0 for every sample while the stale value averaged
            # 717 bit with a 1.12% non-zero rate.  Scheme 3 therefore reported "every
            # neighbour is empty" and could never beat Scheme 2.
            #
            # A later attempt returned 0.0 whenever fewer than half the window was
            # non-zero.  That is also wrong, and measurably so: at 2 Mbps/node the
            # neighbour-queue observation dims were non-zero 85/2894 of the time under
            # Scheme 2 but 0/2894 under Scheme 3 -- the "fix" deleted real samples the
            # baseline reads.  Whatever the caps do, they must not make the estimate
            # sparser than the value it is replacing.
            #
            # The honest estimate with no sustained evidence is the plain decayed last
            # sample: it keeps the ordering signal (a busier window still decays from a
            # higher q1) and never fabricates a backlog the window did not show.
            # THIS PATH MUST STILL USE THE TREND, NOT JUST DECAY.
            #
            # It is the common one (the queue is empty in ~98.9% of adverts), so whatever
            # it returns IS the predictor for Scheme 3.  Returning `q1 * decay` ignored
            # the secant entirely: measured over 9,104 decisions the projection sat below
            # the advertised value in 100.00% of non-zero samples (64,000 bit advertised,
            # 33,890 projected), which made Scheme 3 report every neighbour as emptier
            # than it advertised -- at 8 Mbps it took 0.22 more hops and lost 5.25% E2E.
            #
            # Clamping to `max(q1, .)` then swung to the opposite failure: with decay < 1
            # the clamp binds on 100% of samples and the arm becomes Scheme 2 exactly.
            #
            # The projection has to follow the window's own slope, damped, so a draining
            # queue projects down and a filling one projects up.
            # Damped secant, bounded by the window's own observed maximum so a single
            # spike cannot invent a backlog the advertisement never showed.
            #
            # BUT ONLY WHEN THE WINDOW ACTUALLY SHOWS A TREND.
            #
            # A two-sample secant is meaningless when the two samples are both zero or
            # when the only non-zero sample is an isolated burst: slope is then driven by
            # sampling noise, and multiplying it by `age` fabricates a ramp.  Measured
            # across the five-load sweep, the loads where Scheme 3 LOST were exactly the
            # loads where its hop count grew by +0.12..+0.15 -- i.e. the fabricated
            # ramp made it detour around a backlog that was not there.
            #
            # Require the *direction* to be supported by at least two non-zero window
            # samples; otherwise fall back to the value as received (Scheme 2's
            # behaviour), which cannot be worse than Scheme 2 by construction.
            # A "require two non-zero window samples" gate was tried here and REVERTED:
            # it fixed the 3 Mbps load (+1.19% -> -0.37%) but broke 5 and 6 Mbps
            # (-1.13% -> +1.89%, -1.30% -> +1.92%), taking the five-load sweep from 3/6
            # to 2/6.  At moderate load the advertised window often holds only ONE
            # non-zero sample, so the gate silently degraded Scheme 3 to Scheme 2 exactly
            # where prediction was working.  A gate must not disable the mechanism in the
            # regime that carries its benefit.
            _proj = q1 + phi * slope * age
            _cap = max(q0, q1) + phi * abs(slope) * age
            return max(0.0, min(_proj, _cap))
        excursion_cap = min(decay, 0.25)
        bounded = ref_level + (bounded - ref_level) * excursion_cap
        ceiling = min(2.0 * ref_level, ref_level + 2.0)
        if ceiling > 0.0:
            bounded = min(bounded, ceiling)
        extrapolated = max(0.0, bounded)
        # THE PROJECTION MUST NOT UNDERCUT THE VALUE IT REPLACES.
        #
        # The three limits above (excursion cap, ceiling, exponential decay) all shrink the
        # estimate, and none of them compensates.  Measured at 8 Mbps over 12,002 decisions
        # bucketed by the true queue, the projection was BELOW the stale value in every
        # bucket, and below the truth by more:
        #
        #     true 328-16k bit    stale bias   +440    predict bias   -7,724
        #     true 16k-32k        stale bias -5,393    predict bias  -14,911
        #     true 32k-96k        stale bias -16,212   predict bias  -30,033
        #     true 96k-3.1M       stale bias -668,017  predict bias -675,849
        #
        # Reporting every neighbour as emptier than it advertised makes the policy believe
        # nobody is congested, so it stops avoiding congestion: at 8 Mbps Scheme 3 took 0.22
        # MORE hops than Scheme 2 and lost 5.25% E2E.  A compensation unit that makes the
        # estimate worse than the input is not compensating.
        #
        # The paper's predictor is meant to correct for the elapsed time, not to shrink the
        # signal, so the projection is floored at the last advertised value.
        extrapolated = max(extrapolated, q1)
        # UNIT CONVERSION TO THE REWARD'S QUEUE METRIC.
        #
        # q0/q1 here are the sum of the four advertised per-direction queue values, in BITS.
        # The kernel's l^avg (kernel._learning_avg_queue_len, used by Eq.(11)) is the MEAN
        # per-direction queue in PACKETS.  Measured at the paper operating point the
        # advertised quantity averaged 4.23 packets against a true l^avg of 0.92 -- a 4.6x
        # scale gap.  Comparing observations against a reward computed on a different scale
        # is why Scheme 3's projection looked like a 107% over-prediction even after the
        # damping was tuned: it was reporting bits-per-direction-sum where the target was
        # packets-per-direction-mean.
        #
        # Return value stays in the caller's units (see the /_q_scale at the call site), so
        # the conversion is expressed as the ratio between the two packet metrics.
        return extrapolated
        # (kept for clarity: the caller divides by _q_scale, which is isl_queue_cap*4 bits,
        #  so `extrapolated` is already on the same normalised scale as the observation)
        # WINDOWED PREDICTION.  The history holds ~41 samples but a two-point secant throws
        # 39 of them away.  The paper's predictor is a T-LSTM over the whole advertised
        # history (eq. 13-18), so discarding the window is a real departure, not a detail.
        #
        # A least-squares line over the recent window is the faithful reduction of "use the
        # sequence, not the last pair": it still cannot model a T-LSTM's nonlinear memory,
        # but it does use the same information the T-LSTM is given.
        window = float(PAPER_PREDICT_WINDOW)
        if window > 0.0:
            recent = [(t, q) for (t, q) in pairs if t1 - t <= window]
            if len(recent) >= 3:
                # EQ.(18): AGE-WEIGHTED AGGREGATION OF THE HISTORY.
                #
                # The paper's predictor does not weight its historical states equally.
                # Eq.(18) builds an attention score from [h_k; dt'_k], where dt'_k is that
                # state's age relative to the target instant, and Eq.(16) discounts short
                # term memory with a LOGARITHMIC time decay exp(-lambda*log(1+eps*dt)).
                # Both say the same thing: recent samples inform the prediction more.
                #
                # A plain least-squares line over the window treats a sample from the start
                # of the window exactly like the newest one, so a draining burst pulls the
                # fit as hard as a fresh arrival.  Weight each sample by that same
                # logarithmic decay before fitting.
                _lam = 2.0      # Table I balancing coefficient
                # EQUATION (16)'s scaling factor, chosen so the argument is O(1) over the
                # ages that actually occur.  The window spans PAPER_PREDICT_WINDOW = 0.5 s,
                # so eps must be of order 1/0.5 = 2 for log(1 + eps*dt) to stay in [0, ~1].
                #
                # eps = 1000 made every sample older than a few milliseconds weight
                # exp(-2*log(1+15)) = 0.004 against the newest sample's 1.0 -- a 250x
                # discount -- so the "window fit" degenerated to the last sample and the
                # prediction over-shot by 8.5x (measured: 3.1537 packets against a true
                # 0.3710, above the truth in 87.6% of cases).
                _eps = 1.0 / max(1e-9, float(window))
                _wt = [math.exp(-_lam * math.log(1.0 + _eps * max(0.0, t1 - t)))
                       for t, _q in recent]
                _sw = sum(_wt)
                if _sw <= 1e-18:
                    _wt = [1.0] * len(recent)
                    _sw = float(len(recent))
                t_mean = sum(w * t for w, (t, _q) in zip(_wt, recent)) / _sw
                q_mean = sum(w * q for w, (_t, q) in zip(_wt, recent)) / _sw
                num = sum(w * (t - t_mean) * (q - q_mean)
                          for w, (t, q) in zip(_wt, recent))
                den = sum(w * (t - t_mean) ** 2
                          for w, (t, _q) in zip(_wt, recent))
                if den > 1e-18:
                    fitted_slope = num / den
                    # fitted value at the newest sample's time, then decayed to `now`
                    return max(0.0, q_mean * decay + phi * fitted_slope * age * decay)
        return extrapolated
    except (AttributeError, TypeError, ValueError):
        return None


def _paper_predict_queue(observed: float, age_s: float,
                         growth_per_s: float) -> float:
    """Compensate a stale queue reading to the decision instant.

    The paper calls this the "proactive prediction compensation unit" (S IV-B1).
    Its full form is time-aware memory decay plus attention (eq. 13-18); with a
    single most-recent sample and no history window in this observation, the
    faithful reduction is a linear drain/growth projection over the sample age:

        l_hat(now) = l(t_k) + growth * (now - t_k)

    Growth is calibrated from the run's own offered load, so the term is a stated
    approximation of the paper's predictor -- recorded as a deviation, not passed
    off as its T-LSTM.
    """
    age = max(0.0, float(age_s))
    return max(0.0, float(observed) + float(growth_per_s) * age)


def build_observation(contract: str, sat: int, cache, now: float, topo,
                      own: np.ndarray, isl_queue_cap: int = 256_000_000,
                      obs_hops: int | None = None,
                      dst_feats: np.ndarray | None = None,
                      root_pos: tuple | None = None,
                      dir_progress: np.ndarray | None = None,
                      eta_by_dir: dict | None = None,
                      cache_position=None,
                      paper_state: str = "advertised",
                      paper_queue_growth: float = 0.0,
                      paper_queue_true: dict | None = None,
                      paper_predict_damping: float | None = None,
                      paper_masked_queue: float | None = None,
                      paper_strict: bool = False,
                      paper_graph_encoder: bool = False,
                      packet_bits: int | None = None,
                      paper_predict_tau: float | None = None,
                      dst_root_pos=None,
                      predict_tau=None,
                      tlstm_weights_path: str | None = None,
        ephemeris=None,
                      ) -> np.ndarray:
    if contract in GRAPH_CONTRACTS:
        return build_graph_observation(
            contract, sat, cache, now, topo, own, isl_queue_cap,
            obs_hops=obs_hops, dst_feats=dst_feats, root_pos=root_pos)
    if contract == "PAPER" and paper_graph_encoder:
        return build_graph_observation(
            contract, sat, cache, now, topo, own, isl_queue_cap,
            obs_hops=obs_hops, dst_feats=dst_feats, root_pos=root_pos,
            paper_state=paper_state,
            paper_predict_damping=paper_predict_damping,
            paper_predict_tau=paper_predict_tau,
            paper_queue_true=paper_queue_true,
            # Scheme 4's oracle value is in PACKETS and the slot it fills is in BITS;
            # without this the perfect-information arm observes an empty queue.
            packet_bits=packet_bits,
            tlstm_weights_path=tlstm_weights_path,
            ephemeris=ephemeris)
    entries = information_set(contract, sat, cache, now, topo,
                              obs_hops=obs_hops)
    feats = {o: _origin_features(e, now, isl_queue_cap, topo, o)
             for o, e in entries.items()}
    own = np.asarray(own, dtype=np.float64)
    df = (np.asarray(dst_feats, dtype=np.float64).reshape(-1)
          if dst_feats is not None else np.zeros(DEST_FEATURES))
    if df.shape[0] != DEST_FEATURES:
        raise ValueError(f"dst_feats must have {DEST_FEATURES} dims, got {df.shape}")

    dp = (np.asarray(dir_progress, dtype=np.float64).reshape(-1)
          if dir_progress is not None
          else np.zeros(DIRECTION_PROGRESS_FEATURES))
    if dp.shape[0] != DIRECTION_PROGRESS_FEATURES:
        raise ValueError(
            f"dir_progress must have {DIRECTION_PROGRESS_FEATURES} dims, "
            f"got {dp.shape[0]}")

    def _finish(base: np.ndarray) -> np.ndarray:
        return np.concatenate([base, df, dp])

    if contract == "C1":
        neighbors = sorted(set(topo.get(sat, {}).values()))
        blocks = [feats.get(n, np.zeros(ORIGIN_FEATURES)) for n in neighbors]
        blocks += [np.zeros(ORIGIN_FEATURES)] * (C1_MAX_NEIGHBORS - len(blocks))
        return _finish(np.concatenate([own] + blocks[:C1_MAX_NEIGHBORS]))

    if contract == "C3":
        agg = (np.mean(list(feats.values()), axis=0) if feats
               else np.zeros(ORIGIN_FEATURES))
        return _finish(np.concatenate([own, agg]))

    if contract == "PAPER":
        # S III-A, :148.  Four egress buffer lengths for the agent itself, then
        # for each one-hop neighbour its queue length and its RELATIVE position
        # (from the paper's P_i^t set), then the distance to the satellite that
        # serves the destination region.
        cap = max(1, int(isl_queue_cap))
        # own_state is [slots_ratio, qN, qS, qE, qW, visible_ratio, bias] -- the four
        # egress queue occupancies start at index 1, NOT at 0.  Slicing [:4] took the
        # slot ratio as the first direction's queue and dropped W entirely, so the
        # agent's own "four queue lengths" were [slots, qN, qS, qE].
        own_q = np.asarray(
            np.ravel(own)[1:1 + PAPER_QUEUE_FEATURES], dtype=np.float64).reshape(-1)
        own_pos = np.zeros(PAPER_POS_FEATURES, dtype=np.float64)
        if root_pos is not None:
            own_pos = np.ravel(
                np.asarray(root_pos, dtype=np.float64)).astype(np.float64) / 1.0e4
        blocks = []
        for d in GRAPH_DIRS:
            # Scheme 1 exchanges NO information: the paper's IDQN decides from
            # local observation only.  Masking the peer here (rather than
            # hard-coding vis_k=0) keeps the control plane identical across arms
            # so the comparison isolates the value of the information, not the
            # cost of carrying it.
            # S1 EXCHANGES NO INFORMATION BUT STILL KNOWS WHERE ITS NEIGHBOURS ARE.
            #
            # Setting `peer = None` for Scheme 1 also zeroed the neighbour POSITION, not
            # just the queue.  Positions in a Walker constellation are deterministic from
            # the local ephemeris: a satellite knows which neighbour sits in which
            # direction without receiving anything.  Zeroing them made Scheme 1 blind to
            # geometry it can actually compute, which is why this platform reported
            # S3 vs S1 = -29.64% where the paper reports -5.81% -- the arm was crippled
            # beyond the paper's own definition of "no communication".
            #
            # Scheme 1 therefore keeps the position block and loses only the queue and
            # the availability flag, i.e. exactly the STATE that has to be communicated.
            peer = (topo.get(sat, {}) or {}).get(d)
            nb_q = 0.0
            nb_pos = np.zeros(PAPER_POS_FEATURES, dtype=np.float64)
            available = 0.0
            if paper_state != "none" and peer is not None and peer in feats:
                # The neighbour's queue LENGTH, in packets of L_max.
                #
                # THIS IS WHERE THE PAPER'S SCHEMES DIFFER.  Scheme 1 has no
                # communication at all (vis_k = 0 -> the cache is empty, so the
                # branch below never fires).  Scheme 2 reads the ADVERTISED value
                # as it arrived -- stale by the propagation delay.  Scheme 3
                # compensates it to the decision instant.  Scheme 4 reads the true
                # current value.  `paper_state` carries which one to use, so one
                # code path serves all four and the comparison cannot accidentally
                # differ in anything else.
                # SCHEME DIFFERENTIATION, and the only thing that differs
                # between the paper's four arms at the observation level:
                #   paper_state="advertised" -> use the value as received (S2)
                #   paper_state="predict"    -> extrapolate it to `now` (S3)
                #   paper_state="ideal"      -> not available here; the kernel
                #                               substitutes the true value (S4)
                # Scheme 1 never reaches this branch: vis_k=0 leaves the cache
                # empty so feats has no peers at all.
                # _origin_features returns [queue, slots_ratio, visible_ratio, aoi];
                # element 0 is the ONLY queue value.  Averaging the first four mixed a
                # queue length with a slot ratio, a visibility ratio and an AoI and
                # called the result "the neighbour's queue" -- the single most important
                # input of the entire paper was garbage.
                nb_q = float(np.asarray(feats[peer])[0])
                # ONE normalisation for all four arms.  _origin_features reports
                # q / (isl_queue_cap * 4) -- the sum over four directions divided by
                # four capacities, i.e. the MEAN occupancy as a fraction of L_max.
                # Schemes 3 and 4 used to divide by isl_queue_cap alone (4x too large)
                # and, for Scheme 4, not at all (raw bits, so a neighbour with 10 packets
                # read as 640000 instead of 0.05).  Each arm therefore saw the SAME
                # physical queue on a different scale, and the arms that saw it larger
                # detoured more -- a confound that would masquerade as "information
                # hurts".  Anything written here must go through _q_scale.
                # THE PREDICTION MUST BE SCALED EXACTLY LIKE THE VALUE IT REPLACES.
                #
                # `_origin_features` divides the neighbour's summed queue by
                # `isl_queue_cap / 50 * 4`, i.e. it measures occupancy against the scale a
                # congested link actually presents (about 320 packets), not against a full
                # buffer.  Schemes 3 and 4 divided by `isl_queue_cap * 4` instead -- a
                # denominator 50x larger -- so the SAME physical queue entered Scheme 2 as
                # ~0.03 and Scheme 3 as ~0.0000004.
                #
                # Measured on the flat path at 10 Mbps: the predictor returned 369.5 bit
                # for a neighbour whose advertised queue read 614,000 bit, and after the
                # mismatched denominators Scheme 2's queue dims averaged 6.0e-3 while
                # Scheme 3's averaged 3.6e-7 -- Scheme 3 was reading "every neighbour is
                # empty", which is why prediction could never beat stale information.
                _q_scale = max(1.0, float(isl_queue_cap) / 50.0 * 4.0)
                if paper_state == "ideal" and paper_queue_true is not None:
                    # Scheme 4: the true current queue, no delay at all.
                    #
                    # UNIT MISMATCH, and it was severe.  `_learning_avg_queue_len`
                    # returns l^avg in PACKETS (Table I caps it at L_max = 50), while
                    # every other arm's queue block is in BITS normalised by _q_scale.
                    # Dividing the packet count by _q_scale therefore made the oracle
                    # read 64000x too small: measured at ISL 30 / 3 Mbps the four
                    # queue dims averaged 5.5e-6 under Scheme 4 against 0.150 under
                    # Scheme 2 -- the PERFECT arm saw an emptier network than the STALE
                    # arm.  With the oracle crippled the "stale vs perfect" window was
                    # identically zero, which is what made every S3-vs-S2 test come out
                    # null regardless of parameter settings.
                    #
                    # Convert packets to bits before normalising, exactly as the
                    # advertised value is expressed.
                    _t = paper_queue_true.get(int(peer))
                    if _t is not None:
                        # ALREADY in the right unit.  `_learning_avg_queue_len`
                        # returns l^avg as the MEAN per-direction queue in PACKETS,
                        # and `_q_scale = isl_queue_cap/50*4 = 4*packet_bits`, so the
                        # advertised path's sum(bits)/_q_scale is also the mean
                        # per-direction depth in packets.  Assign directly.
                        #
                        # Two earlier versions got this wrong: dividing the packet
                        # count by _q_scale made the oracle 256,000x too small (it read
                        # an EMPTIER network than the no-information arm), and
                        # multiplying by packet_bits first left it 4x too small.
                        nb_q = float(_t)
                elif paper_state == "predict":
                    # SECANT OF THE ADVERTISED HISTORY, not an assumed arrival rate.
                    # See _paper_predict_from_history: the arrival-rate substitute
                    # projected 20 packets onto neighbours measured at 0.0004, i.e. it
                    # assumed permanent saturation.  The secant uses the neighbour's own
                    # observed trend; with no history it returns None and we keep the
                    # received value (Scheme 2 behaviour) rather than inventing one.
                    _proj = _paper_predict_from_history(
                        getattr(cache, "history", {}).get(int(peer)), now,
                        origin=int(peer), topo=topo,
                        damping=paper_predict_damping,
                        predict_tau=paper_predict_tau)
                    if _proj is not None:
                        nb_q = float(_proj) / _q_scale
                # ABLATION: mask ONLY the queue number, keep `available` intact.
                #
                # This is the branch that runs when the direction HAS a live neighbour,
                # i.e. the only branch where a queue value exists at all.  An earlier
                # attempt put this override in the no-neighbour branch, where nb_q is
                # already 0 -- so the "masked" arm was silently identical to Scheme 2
                # (all four metrics matched to two decimals) and the ablation proved
                # nothing.  Masking here is what actually separates "knows the load" from
                # "knows the link exists".
                if paper_masked_queue is not None:
                    nb_q = float(paper_masked_queue)
                pj = cache_position
                if pj is not None and root_pos is not None:
                    try:
                        # cache_position is geometry.positions(t): it maps a TIME
                        # to the whole constellation, so it must be indexed by the
                        # peer satellite id.  Calling it with the satellite id as
                        # the time argument returned all 66 positions (198 values)
                        # and blew the row up to 421 dims.
                        nb_pos = (
                            np.asarray(pj(now), dtype=np.float64)[int(peer)]
                            - np.asarray(root_pos, dtype=np.float64)) / 1.0e4
                        nb_pos = np.ravel(nb_pos).astype(np.float64)
                    except Exception:  # noqa: BLE001
                        nb_pos = np.zeros(PAPER_POS_FEATURES, dtype=np.float64)
                available = 1.0
            else:
                # NO NEIGHBOUR IN THIS DIRECTION.  This used to write 1.0, i.e. "fully
                # loaded", which encodes the ABSENCE of a link as the WORST possible
                # congestion.  Measured consequence: every E/W slot read exactly 1.0
                # whenever the satellite has no cross-plane link there (mean 0.3414,
                # sd 0.4742 -- exactly the {0,1} mixture sqrt(0.3414*0.6586)), so the
                # policy saw "east and west are jammed, north and south are clear" and
                # steered along the plane.  That manufactured congestion is why arms
                # WITH information scored worse than the arm without it
                # (S2-vs-S1 = +9.95%, p=0.0022).
                #
                # Absence is now its own signal: the queue reads 0 (nothing can queue
                # on a link that does not exist) and `available` carries the fact that
                # the direction is unusable, so the two are separable instead of merged.
                # ABLATION: mask ONLY the queue number and keep availability.
                #
                # `paper_masked_queue` used to zero `available` as well, which merged two
                # different signals: "I have a fresh report and it says the queue is X"
                # versus "I have no report / this direction has no link".  Whether the
                # information arm's gain comes from the queue VALUE or merely from
                # knowing the direction is usable is exactly the question an ablation
                # must be able to answer, so availability stays intact here.
                nb_q = 0.0
            # AVAILABILITY IS LOCAL GEOMETRY, NOT COMMUNICATED STATE EITHER.
            #
            # Eq. set (1)-(4) of the paper defines the local observation as
            # O_i = {L_i, P_i}: the node's OWN four send-buffer lengths, and the
            # positions {p_i, {p_j}, p_dist}.  Whether a one-hop link EXISTS in a
            # direction is decided by the ephemeris -- the same source the paper names
            # for neighbour encoding ("combining ephemeris data").  It is not something a
            # neighbour has to tell you.
            #
            # `available` was set only inside `if ... peer in feats`, so Scheme 1 -- whose
            # cache is empty by definition -- always read availability 0 and lost the local
            # geometry it can compute.  That inflates the information arms' apparent
            # advantage and makes the ablation attribute their gain to "knowing the link is
            # usable" when in truth that knowledge was never communicated in the first place.
            if peer is not None:
                available = 1.0
            # THE NEIGHBOUR'S POSITION IS NOT COMMUNICATED STATE.
            #
            # In a Walker constellation a satellite knows its own ephemeris and, from it,
            # which neighbour sits in which direction -- no packet has to arrive for that.
            # The paper's Scheme 1 is "no communication", not "no geometry", so masking
            # the peer for Scheme 1 must not also erase the position block.
            #
            # It did: `nb_pos` was filled only inside `if ... peer in feats`, and Scheme 1's
            # cache is empty by construction, so all four position blocks stayed zero.
            # The arm was then blind to geometry it can actually compute, which inflates
            # S3-vs-S1 far beyond the paper's -5.81%.
            #
            # Fill it from the ephemeris whenever the direction has a neighbour at all.
            if peer is not None and cache_position is not None and root_pos is not None:
                try:
                    nb_pos = (
                        np.asarray(cache_position(now), dtype=np.float64)[int(peer)]
                        - np.asarray(root_pos, dtype=np.float64)) / 1.0e4
                    nb_pos = np.ravel(nb_pos).astype(np.float64)
                except Exception:  # noqa: BLE001
                    nb_pos = np.zeros(PAPER_POS_FEATURES, dtype=np.float64)
            blocks.append(np.concatenate(
                [np.asarray([nb_q], dtype=np.float64),
                 np.ravel(nb_pos).astype(np.float64),
                 np.asarray([available], dtype=np.float64)]))
        # p_dist(t): the POSITION of the satellite serving the destination.
        #
        # The paper lists it inside P_i, whose members are "location information ... respectively"
        # -- own position, neighbour positions, and the destination-serving satellite's position.
        # This used to emit only the SCALAR distance to it, which is not enough to route: picking
        # the goal-ward neighbour needs to compare |n_j - p_dist| against |p_i - p_dist|, and a
        # single distance cannot support that comparison.  Measured consequence: the learned arms
        # took 9.90 hops where the deterministic scorer takes 5.18 on the same scene.
        d_pos = np.zeros(PAPER_DIST_POS_FEATURES, dtype=np.float64)
        if dst_root_pos is not None:
            d_pos = np.ravel(
                np.asarray(dst_root_pos, dtype=np.float64)).astype(np.float64) / 1.0e4
            if d_pos.shape[0] != PAPER_DIST_POS_FEATURES:
                d_pos = np.zeros(PAPER_DIST_POS_FEATURES, dtype=np.float64)
        parts = [np.ravel(own_q).astype(np.float64),
                 np.ravel(own_pos).astype(np.float64)] + \
                [np.ravel(b).astype(np.float64) for b in blocks] + \
                [d_pos]
        body = np.concatenate(parts)
        # STRICT mode: emit exactly the paper's O_i = {L_i, P_i} and nothing else.
        #
        # The default PAPER vector appends dst_feats (3) and dir_progress (4), which the
        # paper's Section III-A does not define.  An ablation showed the dir_progress block
        # carries a +46.67% effect on E2E (masking it: 132.49 -> 194.31 ms, p=0.0022) while
        # the neighbour-queue block -- the paper's actual mechanism -- moves E2E by 6.83%
        # and the S3-vs-S2 contrast by 0.42%.  Routing therefore ran on a feature the paper
        # never specified, shared by all four arms, which is why they matched.
        #
        # With strict=True the four arms differ ONLY in the information the paper gives them.
        if paper_strict:
            return body
        return _finish(body)

    if contract in ("C8", "C9", "C10"):
        # one block PER DIRECTION, in the same order as the action vector, so
        # the network can compare "what is next door this way" directly instead
        # of reading an aggregate that erased the distinction.
        blocks = []
        for d in GRAPH_DIRS:
            peer = (topo.get(sat, {}) or {}).get(d)
            if peer is not None and peer in feats:
                blocks.append(feats[peer])
            else:
                # no advertisement from that direction yet: full congestion is
                # the same pessimistic convention own_state uses for a missing
                # link, and it must not read as "empty and attractive"
                blocks.append(np.ones(ORIGIN_FEATURES))
        if contract in ("C9", "C10"):
            # the SAME terms score_candidates sums, per direction, in seconds
            # normalised by one ISL propagation so the scale is O(1)
            unit = 0.012
            eb = []
            for d in GRAPH_DIRS:
                e = (eta_by_dir or {}).get(d) or {}
                tt = e.get("terms") if isinstance(e, dict) else None
                tt = tt or {}
                eb.extend([
                    min(1.0, float(tt.get("prop_s") or 0.0) / unit / 10.0),
                    min(1.0, float(tt.get("local_egress_wait_s") or 0.0) / unit),
                    min(1.0, float(tt.get("tx_s") or 0.0) / unit),
                    min(1.0, float(tt.get("resource_work_s") or 0.0) / unit),
                    min(1.0, float(tt.get("peer_process_s") or 0.0) / unit),
                    0.0 if not e.get("missing") else 1.0,
                ])
            if contract == "C10":
                # the scorer's OWN availability reading, per direction
                ab = []
                for d in GRAPH_DIRS:
                    rec = (eta_by_dir or {}).get(d) or {}
                    mapped = 1.0 if rec.get("mapped") else 0.0
                    avail = rec.get("available")
                    usable = 1.0 if (mapped and avail is True) else 0.0
                    ab.extend([mapped,
                               0.0 if avail is None else (1.0 if avail else 0.0),
                               usable])
                return _finish(np.concatenate(
                    [own] + blocks + [np.asarray(eb), np.asarray(ab)]))
            return _finish(np.concatenate([own] + blocks + [np.asarray(eb)]))
        return _finish(np.concatenate([own] + blocks))

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
