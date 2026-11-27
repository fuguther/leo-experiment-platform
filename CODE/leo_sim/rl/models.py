"""TensorFlow model builders and serializable graph encoders for leo_sim RL."""
from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np

from .observations import ACTIONS, GRAPH_CONTRACTS, GRAPH_MAX_NODES, GRAPH_NODE_FEAT_DIM
from .persistence import LearningUnavailable
from .registry import get_model_builder, register_model

try:
    import tensorflow as tf
except ImportError:  # pragma: no cover - exercised on TF-less hosts
    tf = None


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


def default_model_name(contract: str) -> str:
    """Return the registered model family selected by an observation contract."""
    if contract == "GAT":
        return "gat"
    if contract == "MPNN":
        return "mpnn"
    return "mlp"


@register_model("mlp")
def _build_mlp_model(tf_mod, input_dim: int, n_actions: int):
    return tf_mod.keras.Sequential([
        tf_mod.keras.layers.Input(shape=(int(input_dim),)),
        tf_mod.keras.layers.Dense(64, activation="relu"),
        tf_mod.keras.layers.Dense(64, activation="relu"),
        tf_mod.keras.layers.Dense(int(n_actions), activation="linear"),
    ])


def _build_graph_model(tf_mod, input_dim: int, n_actions: int, mode: str):
    inp = tf_mod.keras.layers.Input(shape=(int(input_dim),))
    x = V2GraphEncoder(
        enc_mode=mode,
        n_nodes=GRAPH_MAX_NODES,
        f_dim=GRAPH_NODE_FEAT_DIM,
        h_dim=64,
        layers=1,
        heads=2,
    )(inp)
    x = tf_mod.keras.layers.Dense(64, activation="relu")(x)
    out = tf_mod.keras.layers.Dense(int(n_actions), activation="linear")(x)
    return tf_mod.keras.Model(inputs=inp, outputs=out)


@register_model("gat")
def _build_gat_model(tf_mod, input_dim: int, n_actions: int):
    return _build_graph_model(tf_mod, input_dim, n_actions, "gat")


@register_model("mpnn")
def _build_mpnn_model(tf_mod, input_dim: int, n_actions: int):
    return _build_graph_model(tf_mod, input_dim, n_actions, "mpnn")


def build_model(contract: str, tf_mod, input_dim: int,
                model_name: str | None = None):
    """Instantiate the registered model family selected by contract or override."""
    selected = default_model_name(contract) if model_name is None else str(model_name)
    builder = get_model_builder(selected)
    return builder(tf_mod, int(input_dim), len(ACTIONS))


__all__ = [
    "tf", "require_tensorflow", "V2GraphEncoder", "_graph_custom_objects",
    "_effective_cpu_count", "_configure_tf_threads", "_extract_dense_mlp",
    "_numpy_mlp_forward", "default_model_name", "build_model",
]
