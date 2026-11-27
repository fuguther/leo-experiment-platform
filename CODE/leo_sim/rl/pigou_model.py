"""Pigou-MetaDRL model and offline optimizer.

The deployed model is a 21,345-parameter, pure-NumPy forward pass for the
``full`` variant. TensorFlow 2.13 on CPU is imported lazily only by
:func:`train`; inference and ``.npz`` persistence never require TensorFlow.

The implementation follows attachment sections 1.1, 1.3, and 1.4:

* the frozen Stage-I and Stage-II widths and parameter counts;
* masked AIS supervision for ``y_priv[M, 4, 3]`` with the future queue-change
  slope kept signed;
* snapshot-indexed decision samples and ``tf.gather``-based mini-batches.

This module does not assert paper-level performance. Their validity must be
established by the simulator, ablations, and evaluation artifacts.
"""
from __future__ import annotations

import math
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np

try:
    from ..persistence import LearningUnavailable
except ModuleNotFoundError as exc:  # scratch-tree import without sibling files
    if not (exc.name or "").endswith("leo_sim.persistence"):
        raise

    class LearningUnavailable(RuntimeError):
        """Fallback used when this file is exercised outside the full package."""


DIRECTIONS: tuple[str, str, str, str] = ("N", "S", "E", "W")
H_DIM = 32
PORT_COUNT = 4
X2HOP_DIM = 8
HISTORY_FRAMES = 8
HISTORY_DIM = 9
PKT_GLOBAL_DIM = 10
PKT_PORT_DIM = 10
EXT_DIM = 16
AIS_DIM = 3
ATTN_HEAD_DIM = 12
LAYER_NORM_EPS = 1e-5
QUANTILE_LEVELS = ((np.arange(EXT_DIM, dtype=np.float32) + 0.5)
                   / np.float32(EXT_DIM))

VALID_VARIANTS = frozenset({"full", "mean", "frame", "no_ais", "no_ext"})
_FORMAT = "pigou-metadrl-npz/v1"
_FORMAT_VERSION = 1
_METADATA_KEYS = frozenset({
    "format", "format_version", "variant", "ext_dim", "parameter_count",
})

_EXPECTED_ONLINE_PARAMETER_COUNT = {
    "full": 21_345,
    "mean": 20_850,
    "frame": 2_913,
    "no_ais": 21_345,
    "no_ext": 21_345,
}
_EXPECTED_TRAINING_PARAMETER_COUNT = {
    variant: count + 579
    for variant, count in _EXPECTED_ONLINE_PARAMETER_COUNT.items()
}


def _architecture_for_variant(variant: str) -> str:
    return "frame" if variant == "frame" else "full"


def _ext_dim_for_variant(variant: str) -> int:
    return 1 if variant == "mean" else EXT_DIM


def _xavier(rng: np.random.Generator, shape: tuple[int, int],
            fan_in: int | None = None, fan_out: int | None = None) -> np.ndarray:
    fan_in = int(fan_in if fan_in is not None else shape[0])
    fan_out = int(fan_out if fan_out is not None else shape[1])
    scale = math.sqrt(2.0 / float(fan_in + fan_out))
    return rng.normal(0.0, scale, size=shape).astype(np.float32)


def _dense_params(prefix: str, fan_in: int, fan_out: int,
                  rng: np.random.Generator, *, neutral: bool = False,
                  out: dict[str, np.ndarray] | None = None) -> None:
    target = {} if out is None else out
    target[f"{prefix}.w"] = (
        np.zeros((fan_in, fan_out), dtype=np.float32)
        if neutral else _xavier(rng, (fan_in, fan_out), fan_in, fan_out)
    )
    target[f"{prefix}.b"] = np.zeros((fan_out,), dtype=np.float32)


def _ln_params(prefix: str, dim: int,
               out: dict[str, np.ndarray]) -> None:
    out[f"{prefix}.gamma"] = np.ones((dim,), dtype=np.float32)
    out[f"{prefix}.beta"] = np.zeros((dim,), dtype=np.float32)


def _full_stage_i_initial(rng: np.random.Generator) -> dict[str, np.ndarray]:
    params: dict[str, np.ndarray] = {}
    _dense_params("emb_2hop", X2HOP_DIM, 24, rng, out=params)
    _dense_params("emb_cur", X2HOP_DIM, 24, rng, out=params)
    for gate in ("q", "k", "v", "o"):
        _dense_params(f"gat.{gate}", 24, 24, rng, out=params)
    _ln_params("gat.ln", 24, params)

    _dense_params("spatial", 32, 32, rng, out=params)
    _ln_params("spatial.ln", 32, params)

    _ln_params("port.ln1", 32, params)
    for gate in ("q", "k", "v", "o"):
        _dense_params(f"port.{gate}", 32, 32, rng, out=params)
    _ln_params("port.ln2", 32, params)
    _dense_params("port.ffn1", 32, 64, rng, out=params)
    _dense_params("port.ffn2", 64, 32, rng, out=params)

    params["gru.wx"] = _xavier(rng, (HISTORY_DIM, 3 * H_DIM))
    params["gru.uh"] = _xavier(rng, (H_DIM, 3 * H_DIM))
    params["gru.b"] = np.zeros((3 * H_DIM,), dtype=np.float32)

    _dense_params("fusion.gate", 64, 32, rng, neutral=True, out=params)
    _ln_params("fusion.ln", 32, params)
    return params


def _stage_ii_initial(rng: np.random.Generator, ext_dim: int
                      ) -> dict[str, np.ndarray]:
    params: dict[str, np.ndarray] = {}
    _dense_params("pkt_global", PKT_GLOBAL_DIM, 16, rng, out=params)
    _dense_params("packet_fuse", 58, 32, rng, out=params)
    _dense_params("ego", 32, 1, rng, neutral=True, out=params)
    _dense_params("ext", 32, ext_dim, rng, neutral=True, out=params)
    return params


def _ais_initial(rng: np.random.Generator) -> dict[str, np.ndarray]:
    params: dict[str, np.ndarray] = {}
    _dense_params("ais.1", 32, 16, rng, out=params)
    _dense_params("ais.2", 16, AIS_DIM, rng, neutral=True, out=params)
    return params


def _online_initial_parameters(rng: np.random.Generator, variant: str
                               ) -> dict[str, np.ndarray]:
    architecture = _architecture_for_variant(variant)
    ext_dim = _ext_dim_for_variant(variant)
    if architecture == "frame":
        params: dict[str, np.ndarray] = {}
        _dense_params("frame", X2HOP_DIM, H_DIM, rng, out=params)
    else:
        params = _full_stage_i_initial(rng)
    params.update(_stage_ii_initial(rng, ext_dim))
    return params


def _training_initial_parameters(rng: np.random.Generator, variant: str
                                 ) -> dict[str, np.ndarray]:
    params = _online_initial_parameters(rng, variant)
    params.update(_ais_initial(rng))
    return params


def _stage_i_parameter_names(architecture: str) -> set[str]:
    if architecture == "frame":
        return {"frame.w", "frame.b"}
    return {
        "emb_2hop.w", "emb_2hop.b", "emb_cur.w", "emb_cur.b",
        "gat.q.w", "gat.q.b", "gat.k.w", "gat.k.b",
        "gat.v.w", "gat.v.b", "gat.o.w", "gat.o.b",
        "gat.ln.gamma", "gat.ln.beta",
        "spatial.w", "spatial.b", "spatial.ln.gamma", "spatial.ln.beta",
        "port.ln1.gamma", "port.ln1.beta",
        "port.q.w", "port.q.b", "port.k.w", "port.k.b",
        "port.v.w", "port.v.b", "port.o.w", "port.o.b",
        "port.ln2.gamma", "port.ln2.beta",
        "port.ffn1.w", "port.ffn1.b", "port.ffn2.w", "port.ffn2.b",
        "gru.wx", "gru.uh", "gru.b",
        "fusion.gate.w", "fusion.gate.b",
        "fusion.ln.gamma", "fusion.ln.beta",
    }


def _stage_ii_parameter_names() -> set[str]:
    return {
        "pkt_global.w", "pkt_global.b",
        "packet_fuse.w", "packet_fuse.b",
        "ego.w", "ego.b", "ext.w", "ext.b",
    }


def _required_parameter_names(variant: str) -> set[str]:
    return (_stage_i_parameter_names(_architecture_for_variant(variant))
            | _stage_ii_parameter_names())


def _sigmoid(x: np.ndarray) -> np.ndarray:
    out = np.empty_like(x, dtype=np.float32)
    positive = x >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-x[positive]))
    exp_x = np.exp(x[~positive])
    out[~positive] = exp_x / (1.0 + exp_x)
    return out


def _silu(x: np.ndarray) -> np.ndarray:
    return x * _sigmoid(x)


def _softplus(x: np.ndarray) -> np.ndarray:
    return np.logaddexp(np.float32(0.0), x).astype(np.float32, copy=False)


def _layer_norm(x: np.ndarray, gamma: np.ndarray, beta: np.ndarray
                ) -> np.ndarray:
    mean = np.mean(x, axis=-1, keepdims=True, dtype=np.float32)
    centered = x - mean
    variance = np.mean(centered * centered, axis=-1, keepdims=True,
                       dtype=np.float32)
    return ((centered / np.sqrt(variance + np.float32(LAYER_NORM_EPS)))
            * gamma + beta).astype(np.float32, copy=False)


def _numpy_local_attention(x_2hop: np.ndarray, snap_cur: np.ndarray,
                           params: Mapping[str, np.ndarray]) -> np.ndarray:
    edge_emb = _silu(x_2hop @ params["emb_2hop.w"] + params["emb_2hop.b"])
    cur_emb = _silu(snap_cur @ params["emb_cur.w"] + params["emb_cur.b"])

    q = cur_emb @ params["gat.q.w"] + params["gat.q.b"]
    k = edge_emb @ params["gat.k.w"] + params["gat.k.b"]
    v = edge_emb @ params["gat.v.w"] + params["gat.v.b"]
    q = q.reshape(PORT_COUNT, 2, ATTN_HEAD_DIM)
    k = k.reshape(PORT_COUNT, 4, 2, ATTN_HEAD_DIM)
    v = v.reshape(PORT_COUNT, 4, 2, ATTN_HEAD_DIM)

    valid = np.any(x_2hop != 0.0, axis=-1)
    scores = np.einsum("nhd,nkhd->nhk", q, k)
    scores = scores / np.float32(math.sqrt(ATTN_HEAD_DIM))
    masked_scores = np.where(valid[:, None, :], scores, np.float32(-1.0e9))
    maxima = np.max(masked_scores, axis=-1, keepdims=True)
    exponentials = np.where(
        valid[:, None, :], np.exp(masked_scores - maxima), np.float32(0.0))
    denominator = np.sum(exponentials, axis=-1, keepdims=True)
    weights = exponentials / np.maximum(denominator, np.float32(1.0))
    context = np.einsum("nhk,nkhd->nhd", weights, v)
    context = context.reshape(PORT_COUNT, 24)
    projected = context @ params["gat.o.w"] + params["gat.o.b"]
    return _layer_norm(cur_emb + projected, params["gat.ln.gamma"],
                       params["gat.ln.beta"])


def _numpy_port_attention(s0: np.ndarray,
                          params: Mapping[str, np.ndarray]) -> np.ndarray:
    normed = _layer_norm(s0, params["port.ln1.gamma"],
                         params["port.ln1.beta"])
    q = normed @ params["port.q.w"] + params["port.q.b"]
    k = normed @ params["port.k.w"] + params["port.k.b"]
    v = normed @ params["port.v.w"] + params["port.v.b"]
    q = q.reshape(PORT_COUNT, 4, 8)
    k = k.reshape(PORT_COUNT, 4, 8)
    v = v.reshape(PORT_COUNT, 4, 8)

    scores = np.einsum("phd,pkd->phk", q, k)
    reference = np.max(scores, axis=-1, keepdims=True)
    exponentials = np.exp(scores - reference)
    weights = exponentials / np.sum(exponentials, axis=-1, keepdims=True)
    context = np.einsum("phk,pkd->phd", weights, v)
    context = context.reshape(PORT_COUNT, 32)
    attention = context @ params["port.o.w"] + params["port.o.b"]
    after_attention = s0 + attention

    normed2 = _layer_norm(after_attention, params["port.ln2.gamma"],
                          params["port.ln2.beta"])
    hidden = _silu(normed2 @ params["port.ffn1.w"] + params["port.ffn1.b"])
    feed_forward = hidden @ params["port.ffn2.w"] + params["port.ffn2.b"]
    return (after_attention + feed_forward).astype(np.float32, copy=False)


def _numpy_gru(hist_seq: np.ndarray,
               params: Mapping[str, np.ndarray]) -> np.ndarray:
    input_projection = hist_seq @ params["gru.wx"] + params["gru.b"]
    hidden = np.zeros((PORT_COUNT, H_DIM), dtype=np.float32)
    for timestep in range(HISTORY_FRAMES):
        recurrent = hidden @ params["gru.uh"]
        input_now = input_projection[:, timestep, :]
        update = _sigmoid(input_now[:, :H_DIM] + recurrent[:, :H_DIM])
        reset = _sigmoid(input_now[:, H_DIM:2 * H_DIM]
                         + recurrent[:, H_DIM:2 * H_DIM])
        candidate = np.tanh(input_now[:, 2 * H_DIM:]
                            + reset * recurrent[:, 2 * H_DIM:])
        hidden = (1.0 - update) * hidden + update * candidate
    return hidden.astype(np.float32, copy=False)


def _numpy_encode(parameters: Mapping[str, np.ndarray], variant: str,
                  x_2hop: np.ndarray, snap_cur: np.ndarray,
                  hist_seq: np.ndarray) -> np.ndarray:
    if _architecture_for_variant(variant) == "frame":
        return _silu(snap_cur @ parameters["frame.w"] + parameters["frame.b"])

    graph = _numpy_local_attention(x_2hop, snap_cur, parameters)
    spatial = np.concatenate((graph, snap_cur), axis=-1)
    s0 = _silu(spatial @ parameters["spatial.w"] + parameters["spatial.b"])
    s0 = _layer_norm(s0, parameters["spatial.ln.gamma"],
                     parameters["spatial.ln.beta"])
    s_port = _numpy_port_attention(s0, parameters)
    t_port = _numpy_gru(hist_seq, parameters)
    gate = _sigmoid(np.concatenate((s_port, t_port), axis=-1)
                    @ parameters["fusion.gate.w"] + parameters["fusion.gate.b"])
    fused = gate * s_port + (1.0 - gate) * t_port
    return _layer_norm(fused, parameters["fusion.ln.gamma"],
                       parameters["fusion.ln.beta"])


def _numpy_score(parameters: Mapping[str, np.ndarray], h: np.ndarray,
                 x_pkt_global: np.ndarray,
                 x_pkt_port: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    global_embedding = _silu(
        x_pkt_global @ parameters["pkt_global.w"]
        + parameters["pkt_global.b"])
    global_by_port = np.broadcast_to(global_embedding[:, None, :],
                                     (h.shape[0], PORT_COUNT, 16))
    fused_input = np.concatenate((h, x_pkt_port, global_by_port), axis=-1)
    packet_hidden = _silu(
        fused_input @ parameters["packet_fuse.w"]
        + parameters["packet_fuse.b"])
    ego = _sigmoid(packet_hidden @ parameters["ego.w"] + parameters["ego.b"])
    ext = _softplus(packet_hidden @ parameters["ext.w"] + parameters["ext.b"])
    return ego[..., 0].astype(np.float32, copy=False), ext.astype(
        np.float32, copy=False)


def _artifact_path(path: str | os.PathLike[str]) -> Path:
    resolved = Path(path).expanduser()
    if resolved.exists() and resolved.is_dir():
        return resolved / "pigou_weights.npz"
    if resolved.suffix.lower() == ".npz":
        return resolved
    return resolved.with_suffix(".npz")


class NumpyPigou:
    """Pure-NumPy deployed Pigou-MetaDRL model."""

    def __init__(self, parameters: Mapping[str, np.ndarray], *, variant: str):
        if variant not in VALID_VARIANTS:
            raise ValueError(f"unknown Pigou-MetaDRL variant {variant!r}")
        expected = _required_parameter_names(variant)
        provided = set(parameters)
        missing = sorted(expected - provided)
        extra = sorted(provided - expected)
        if missing or extra:
            raise ValueError(
                "Pigou-MetaDRL parameter set mismatch "
                f"(missing={missing}, extra={extra})")
        self.variant = variant
        self.architecture = _architecture_for_variant(variant)
        self.ext_dim = _ext_dim_for_variant(variant)
        self._parameters: dict[str, np.ndarray] = {}
        for name in sorted(expected):
            value = np.asarray(parameters[name], dtype=np.float32)
            if value.dtype != np.dtype(np.float32):
                raise ValueError(f"parameter {name!r} cannot be represented as float32")
            if not np.all(np.isfinite(value)):
                raise ValueError(f"parameter {name!r} contains non-finite values")
            self._parameters[name] = np.array(value, dtype=np.float32, copy=True,
                                              order="C")
        expected_count = _EXPECTED_ONLINE_PARAMETER_COUNT[variant]
        if self.parameter_count != expected_count:
            raise ValueError(
                f"{variant} parameter count {self.parameter_count} != "
                f"frozen count {expected_count}")

    @classmethod
    def neutral(cls, *, seed: int = 0, variant: str = "full") -> "NumpyPigou":
        """Construct a deterministic architecture with neutral output heads."""
        if variant not in VALID_VARIANTS:
            raise ValueError(f"unknown Pigou-MetaDRL variant {variant!r}")
        rng = np.random.default_rng(int(seed))
        return cls(_online_initial_parameters(rng, variant), variant=variant)

    @property
    def parameters(self) -> dict[str, np.ndarray]:
        """Return a defensive copy suitable for warm starts or inspection."""
        return {name: value.copy() for name, value in self._parameters.items()}

    @property
    def parameter_count(self) -> int:
        return int(sum(value.size for value in self._parameters.values()))

    @property
    def stage_i_parameter_count(self) -> int:
        names = _stage_i_parameter_names(self.architecture)
        return int(sum(self._parameters[name].size for name in names))

    @property
    def stage_ii_parameter_count(self) -> int:
        names = _stage_ii_parameter_names()
        return int(sum(self._parameters[name].size for name in names))

    def encode(self, x_2hop: np.ndarray, snap_cur: np.ndarray,
               hist_seq: np.ndarray) -> np.ndarray:
        """Encode one slow-clock snapshot into ``H[4, 32]``.

        Arrays follow the frozen shapes ``[4, 4, 8]``, ``[4, 8]`` and
        ``[4, 8, 9]``. The frame variant intentionally ignores graph/history.
        """
        x2 = np.asarray(x_2hop, dtype=np.float32)
        snap = np.asarray(snap_cur, dtype=np.float32)
        hist = np.asarray(hist_seq, dtype=np.float32)
        if x2.shape != (4, 4, 8):
            raise ValueError(f"x_2hop must have shape (4, 4, 8), got {x2.shape}")
        if snap.shape != (4, 8):
            raise ValueError(f"snap_cur must have shape (4, 8), got {snap.shape}")
        if hist.shape != (4, 8, 9):
            raise ValueError(f"hist_seq must have shape (4, 8, 9), got {hist.shape}")
        if not (np.all(np.isfinite(x2)) and np.all(np.isfinite(snap))
                and np.all(np.isfinite(hist))):
            raise ValueError("encode inputs must be finite")
        return _numpy_encode(self._parameters, self.variant, x2, snap, hist)

    def score(self, h: np.ndarray, x_pkt_global: np.ndarray,
              x_pkt_port: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Score a packet without re-encoding its slow snapshot.

        For the required single-snapshot API, ``H`` is ``[4, 32]``,
        ``x_pkt_global`` is ``[10]`` and ``x_pkt_port`` is ``[4, 10]``.
        Leading batch dimensions are accepted for harness reuse. The returned
        externality tensor is ``[4, 16]`` except for the ``mean`` variant,
        which returns ``[4, 1]``.
        """
        h_array = np.asarray(h, dtype=np.float32)
        global_array = np.asarray(x_pkt_global, dtype=np.float32)
        port_array = np.asarray(x_pkt_port, dtype=np.float32)
        single = h_array.ndim == 2
        if single:
            if h_array.shape != (4, 32):
                raise ValueError(f"H must have shape (4, 32), got {h_array.shape}")
            if global_array.shape != (10,):
                raise ValueError(
                    f"x_pkt_global must have shape (10,), got {global_array.shape}")
            if port_array.shape != (4, 10):
                raise ValueError(
                    f"x_pkt_port must have shape (4, 10), got {port_array.shape}")
            h_batch = h_array[None, ...]
            global_batch = global_array[None, ...]
            port_batch = port_array[None, ...]
        else:
            if h_array.ndim != 3 or h_array.shape[1:] != (4, 32):
                raise ValueError(
                    "batched H must have shape (B, 4, 32), "
                    f"got {h_array.shape}")
            batch = h_array.shape[0]
            if global_array.ndim == 1 and global_array.shape == (10,):
                global_batch = np.broadcast_to(global_array,
                                               (batch, 10)).copy()
            elif global_array.shape == (batch, 10):
                global_batch = global_array
            else:
                raise ValueError(
                    "batched x_pkt_global must have shape "
                    f"(10,) or ({batch}, 10), got {global_array.shape}")
            if port_array.shape != (batch, 4, 10):
                raise ValueError(
                    "batched x_pkt_port must have shape "
                    f"({batch}, 4, 10), got {port_array.shape}")
            h_batch = h_array
            port_batch = port_array
        if not (np.all(np.isfinite(h_batch))
                and np.all(np.isfinite(global_batch))
                and np.all(np.isfinite(port_batch))):
            raise ValueError("score inputs must be finite")
        ego, ext = _numpy_score(self._parameters, h_batch, global_batch,
                                port_batch)
        if single:
            return ego[0], ext[0]
        return ego, ext

    def save(self, path: str | os.PathLike[str]) -> Path:
        """Atomically save deployed weights as a non-pickle NumPy ``.npz``."""
        target = _artifact_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            name: value for name, value in self._parameters.items()
        }
        payload.update({
            "format": np.asarray(_FORMAT),
            "format_version": np.asarray(_FORMAT_VERSION, dtype=np.int32),
            "variant": np.asarray(self.variant),
            "ext_dim": np.asarray(self.ext_dim, dtype=np.int32),
            "parameter_count": np.asarray(self.parameter_count, dtype=np.int64),
        })
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="wb", dir=target.parent,
                    prefix=f".{target.stem}.", suffix=".tmp",
                    delete=False) as handle:
                temporary = handle.name
                np.savez_compressed(handle, **payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        return target

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "NumpyPigou":
        """Load a deployed model with ``allow_pickle=False`` enforced."""
        source = _artifact_path(path)
        try:
            with np.load(source, allow_pickle=False) as archive:
                if "format" not in archive.files:
                    raise LearningUnavailable(
                        f"Pigou model {source} has no format marker")
                file_format = str(np.asarray(archive["format"]).item())
                if file_format != _FORMAT:
                    raise LearningUnavailable(
                        f"Pigou model format {file_format!r} != {_FORMAT!r}")
                file_version = int(np.asarray(archive["format_version"]).item())
                if file_version != _FORMAT_VERSION:
                    raise LearningUnavailable(
                        f"unsupported Pigou model version {file_version}")
                variant = str(np.asarray(archive["variant"]).item())
                if variant not in VALID_VARIANTS:
                    raise LearningUnavailable(
                        f"unsupported Pigou model variant {variant!r}")
                declared_ext_dim = int(np.asarray(archive["ext_dim"]).item())
                if declared_ext_dim != _ext_dim_for_variant(variant):
                    raise LearningUnavailable(
                        "Pigou model ext dimension does not match its variant")
                declared_count = int(np.asarray(
                    archive["parameter_count"]).item())
                required = _required_parameter_names(variant)
                missing = sorted(required - set(archive.files))
                extra = sorted(set(archive.files) - required - _METADATA_KEYS)
                if missing or extra:
                    raise LearningUnavailable(
                        "Pigou model parameter set mismatch "
                        f"(missing={missing}, extra={extra})")
                parameters = {
                    name: np.array(archive[name], dtype=np.float32, copy=True)
                    for name in required
                }
                if any(value.dtype == np.dtype("O") for value in parameters.values()):
                    raise LearningUnavailable(
                        "Pigou model contains an object array")
        except LearningUnavailable:
            raise
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise LearningUnavailable(
                f"Pigou model {source} is unreadable: {exc}") from exc
        try:
            model = cls(parameters, variant=variant)
        except (TypeError, ValueError) as exc:
            raise LearningUnavailable(
                f"Pigou model {source} failed validation: {exc}") from exc
        if model.parameter_count != declared_count:
            raise LearningUnavailable(
                "Pigou model declared parameter count does not match weights")
        return model


def _require_finite(name: str, value: np.ndarray) -> None:
    if not np.all(np.isfinite(value)):
        raise ValueError(f"{name} contains non-finite values")


def _integer_vector(value: Any, name: str, length: int) -> np.ndarray:
    raw = np.asarray(value)
    if raw.shape != (length,):
        raise ValueError(f"{name} must have shape ({length},), got {raw.shape}")
    if not np.issubdtype(raw.dtype, np.integer):
        numeric = np.asarray(raw, dtype=np.float64)
        if not np.all(np.isfinite(numeric)) or not np.all(numeric == np.floor(numeric)):
            raise ValueError(f"{name} must contain integers")
        raw = numeric.astype(np.int64)
    return np.asarray(raw, dtype=np.int64)


def _select_action_target(value: Any, name: str, count: int,
                          actions: np.ndarray) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if array.shape == (count,):
        selected = array
    elif array.shape == (count, 4):
        selected = array[np.arange(count), actions]
    elif array.ndim >= 3 and array.shape[:2] == (count, 4):
        selected = array[np.arange(count), actions]
    else:
        raise ValueError(
            f"{name} must have shape ({count},), ({count}, 4), "
            f"or ({count}, 4, ...), got {array.shape}")
    if selected.ndim == 2 and selected.shape[1] == 1:
        selected = selected[:, 0]
    _require_finite(name, selected)
    return np.asarray(selected, dtype=np.float32)


def _validate_training_inputs(
        snapshots: Mapping[str, Any], decisions: Mapping[str, Any],
        variant: str) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]:
    if not isinstance(snapshots, Mapping) or not isinstance(decisions, Mapping):
        raise TypeError("snapshots and decisions must be mappings")
    required_snapshots = {"x_2hop", "snap_cur", "hist_seq", "y_priv"}
    required_decisions = {
        "snap_id", "x_pkt_global", "x_pkt_port", "action", "g_ego", "g_ext",
    }
    missing_snapshots = sorted(required_snapshots - set(snapshots))
    missing_decisions = sorted(required_decisions - set(decisions))
    if missing_snapshots or missing_decisions:
        raise ValueError(
            "missing Pigou training data "
            f"(snapshots={missing_snapshots}, decisions={missing_decisions})")

    x2 = np.asarray(snapshots["x_2hop"], dtype=np.float32)
    snap_cur = np.asarray(snapshots["snap_cur"], dtype=np.float32)
    hist_seq = np.asarray(snapshots["hist_seq"], dtype=np.float32)
    y_priv = np.asarray(snapshots["y_priv"], dtype=np.float32)
    if x2.ndim != 4 or x2.shape[1:] != (4, 4, 8):
        raise ValueError(
            "snapshots.x_2hop must have shape (M, 4, 4, 8), "
            f"got {x2.shape}")
    snapshot_count = x2.shape[0]
    if snapshot_count <= 0:
        raise ValueError("snapshots must contain at least one row")
    if snap_cur.shape != (snapshot_count, 4, 8):
        raise ValueError(
            "snapshots.snap_cur must have shape "
            f"({snapshot_count}, 4, 8), got {snap_cur.shape}")
    if hist_seq.shape != (snapshot_count, 4, 8, 9):
        raise ValueError(
            "snapshots.hist_seq must have shape "
            f"({snapshot_count}, 4, 8, 9), got {hist_seq.shape}")
    if y_priv.shape != (snapshot_count, 4, 3):
        raise ValueError(
            "snapshots.y_priv must have shape "
            f"({snapshot_count}, 4, 3), got {y_priv.shape}")
    _require_finite("snapshots.x_2hop", x2)
    _require_finite("snapshots.snap_cur", snap_cur)
    _require_finite("snapshots.hist_seq", hist_seq)

    if "y_valid" in snapshots and snapshots["y_valid"] is not None:
        raw_valid = np.asarray(snapshots["y_valid"])
        if raw_valid.shape != (snapshot_count, 4, 3):
            raise ValueError(
                "snapshots.y_valid must have shape "
                f"({snapshot_count}, 4, 3), got {raw_valid.shape}")
        if not np.all((raw_valid == 0) | (raw_valid == 1)):
            raise ValueError("snapshots.y_valid must contain only booleans")
        y_valid = raw_valid.astype(bool)
    else:
        y_valid = np.ones((snapshot_count, 4, 3), dtype=bool)
    if np.any(y_valid):
        valid_values = y_priv[y_valid]
        _require_finite("valid snapshots.y_priv entries", valid_values)
        occupancy = y_priv[..., 0][y_valid[..., 0]]
        signed_slope = y_priv[..., 1][y_valid[..., 1]]
        bypass = y_priv[..., 2][y_valid[..., 2]]
        if np.any((occupancy < 0.0) | (occupancy > 1.0)):
            raise ValueError("y_priv occupancy labels must be in [0, 1]")
        if np.any((signed_slope < -1.0) | (signed_slope > 1.0)):
            raise ValueError("y_priv signed slope labels must be in [-1, 1]")
        if np.any((bypass < 0.0) | (bypass > 1.0)):
            raise ValueError("y_priv bypass labels must be in [0, 1]")

    x_global = np.asarray(decisions["x_pkt_global"], dtype=np.float32)
    if x_global.ndim != 2 or x_global.shape[1] != PKT_GLOBAL_DIM:
        raise ValueError(
            "decisions.x_pkt_global must have shape (N, 10), "
            f"got {x_global.shape}")
    decision_count = x_global.shape[0]
    if decision_count <= 0:
        raise ValueError("decisions must contain at least one row")
    x_port = np.asarray(decisions["x_pkt_port"], dtype=np.float32)
    if x_port.shape != (decision_count, 4, PKT_PORT_DIM):
        raise ValueError(
            "decisions.x_pkt_port must have shape "
            f"({decision_count}, 4, 10), got {x_port.shape}")
    _require_finite("decisions.x_pkt_global", x_global)
    _require_finite("decisions.x_pkt_port", x_port)

    snap_ids = _integer_vector(decisions["snap_id"], "decisions.snap_id",
                               decision_count)
    if np.any((snap_ids < 0) | (snap_ids >= snapshot_count)):
        raise ValueError("decisions.snap_id contains an out-of-range value")
    actions = _integer_vector(decisions["action"], "decisions.action",
                              decision_count)
    if np.any((actions < 0) | (actions >= len(DIRECTIONS))):
        raise ValueError("decisions.action must be in 0..3 for N,S,E,W")

    ego_target = _select_action_target(
        decisions["g_ego"], "decisions.g_ego", decision_count, actions)
    if ego_target.shape != (decision_count,):
        raise ValueError(
            "decisions.g_ego must select to one scalar per decision")
    if np.any((ego_target < 0.0) | (ego_target > 1.0)):
        raise ValueError("decisions.g_ego must be in [0, 1]")

    ext_target = _select_action_target(
        decisions["g_ext"], "decisions.g_ext", decision_count, actions)
    ext_dim = _ext_dim_for_variant(variant)
    if ext_target.shape == (decision_count, 1):
        ext_target = ext_target[:, 0]
    if ext_target.shape not in ((decision_count,), (decision_count, ext_dim)):
        raise ValueError(
            "decisions.g_ext must select to (N,), (N, 1), or "
            f"(N, {ext_dim}) for variant {variant!r}; "
            f"got {ext_target.shape}")
    if np.any(ext_target < 0.0):
        raise ValueError("decisions.g_ext must be non-negative")

    # Masked labels are invalid by definition and may be NaN in producer
    # artifacts. Replace them before TensorFlow sees the tensor so `x * 0`
    # cannot propagate NaN through the masked auxiliary loss.
    y_priv_safe = np.where(y_valid, y_priv, np.float32(0.0)).astype(
        np.float32, copy=False)
    snapshot_data = {
        "x_2hop": x2,
        "snap_cur": snap_cur,
        "hist_seq": hist_seq,
        "y_priv": y_priv_safe,
        "y_valid": y_valid,
    }
    decision_data = {
        "snap_id": snap_ids,
        "x_pkt_global": x_global,
        "x_pkt_port": x_port,
        "action": actions,
        "g_ego": ego_target,
        "g_ext": ext_target,
    }
    return snapshot_data, decision_data, snapshot_count


def _import_tensorflow_213():
    """Import TensorFlow lazily and fail unless this is the frozen CPU runtime."""
    # These must be set before TensorFlow initializes its device layer.
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    try:
        import tensorflow as tf  # type: ignore
    except (ImportError, OSError) as exc:
        raise LearningUnavailable(
            "TensorFlow is unavailable; offline Pigou-MetaDRL training "
            "requires TensorFlow 2.13 on CPU") from exc
    version = str(getattr(tf, "__version__", ""))
    if ".".join(version.split(".")[:2]) != "2.13":
        raise LearningUnavailable(
            "Pigou-MetaDRL training requires TensorFlow 2.13; "
            f"found {version or 'unknown'}")
    return tf


def _tf_layer_norm(tf, x, gamma, beta):
    mean = tf.reduce_mean(x, axis=-1, keepdims=True)
    centered = x - mean
    variance = tf.reduce_mean(tf.square(centered), axis=-1, keepdims=True)
    return centered / tf.sqrt(variance + tf.cast(LAYER_NORM_EPS, x.dtype)) \
        * gamma + beta


def _tf_local_attention(tf, x_2hop, snap_cur, parameters):
    edge_emb = tf.nn.silu(x_2hop @ parameters["emb_2hop.w"]
                           + parameters["emb_2hop.b"])
    cur_emb = tf.nn.silu(snap_cur @ parameters["emb_cur.w"]
                         + parameters["emb_cur.b"])
    q = cur_emb @ parameters["gat.q.w"] + parameters["gat.q.b"]
    k = edge_emb @ parameters["gat.k.w"] + parameters["gat.k.b"]
    v = edge_emb @ parameters["gat.v.w"] + parameters["gat.v.b"]
    q = tf.reshape(q, (-1, PORT_COUNT, 2, ATTN_HEAD_DIM))
    k = tf.reshape(k, (-1, PORT_COUNT, 4, 2, ATTN_HEAD_DIM))
    v = tf.reshape(v, (-1, PORT_COUNT, 4, 2, ATTN_HEAD_DIM))

    valid = tf.reduce_any(tf.not_equal(x_2hop, 0.0), axis=-1)
    valid_for_head = valid[:, :, tf.newaxis, :]
    scores = tf.einsum("nphd,npkhd->nphk", q, k)
    scores = scores / tf.cast(math.sqrt(ATTN_HEAD_DIM), scores.dtype)
    scores = tf.where(valid_for_head, scores,
                      tf.fill(tf.shape(scores), tf.cast(-1.0e9, scores.dtype)))
    maxima = tf.reduce_max(scores, axis=-1, keepdims=True)
    exponentials = tf.where(valid_for_head, tf.exp(scores - maxima),
                            tf.zeros_like(scores))
    denominator = tf.reduce_sum(exponentials, axis=-1, keepdims=True)
    weights = exponentials / tf.maximum(denominator, tf.ones_like(denominator))
    context = tf.einsum("nphk,npkhd->nphd", weights, v)
    context = tf.reshape(context, (-1, PORT_COUNT, 24))
    projected = context @ parameters["gat.o.w"] + parameters["gat.o.b"]
    return _tf_layer_norm(tf, cur_emb + projected,
                          parameters["gat.ln.gamma"],
                          parameters["gat.ln.beta"])


def _tf_port_attention(tf, s0, parameters):
    normed = _tf_layer_norm(tf, s0, parameters["port.ln1.gamma"],
                            parameters["port.ln1.beta"])
    q = normed @ parameters["port.q.w"] + parameters["port.q.b"]
    k = normed @ parameters["port.k.w"] + parameters["port.k.b"]
    v = normed @ parameters["port.v.w"] + parameters["port.v.b"]
    q = tf.reshape(q, (-1, PORT_COUNT, 4, 8))
    k = tf.reshape(k, (-1, PORT_COUNT, 4, 8))
    v = tf.reshape(v, (-1, PORT_COUNT, 4, 8))
    scores = tf.einsum("nphd,npkd->nphk", q, k)
    reference = tf.reduce_max(scores, axis=-1, keepdims=True)
    exponentials = tf.exp(scores - reference)
    weights = exponentials / tf.reduce_sum(exponentials, axis=-1, keepdims=True)
    context = tf.einsum("nphk,npkd->nphd", weights, v)
    context = tf.reshape(context, (-1, PORT_COUNT, 32))
    attention = context @ parameters["port.o.w"] + parameters["port.o.b"]
    after_attention = s0 + attention
    normed2 = _tf_layer_norm(tf, after_attention,
                             parameters["port.ln2.gamma"],
                             parameters["port.ln2.beta"])
    hidden = tf.nn.silu(normed2 @ parameters["port.ffn1.w"]
                        + parameters["port.ffn1.b"])
    feed_forward = hidden @ parameters["port.ffn2.w"] + parameters["port.ffn2.b"]
    return after_attention + feed_forward


def _tf_gru(tf, hist_seq, parameters):
    input_projection = hist_seq @ parameters["gru.wx"] + parameters["gru.b"]
    input_projection = tf.reshape(
        input_projection, (-1, PORT_COUNT, HISTORY_FRAMES, 3, H_DIM))
    hidden = tf.zeros((tf.shape(hist_seq)[0], PORT_COUNT, H_DIM),
                      dtype=hist_seq.dtype)
    for timestep in range(HISTORY_FRAMES):
        recurrent = hidden @ parameters["gru.uh"]
        recurrent = tf.reshape(recurrent, (-1, PORT_COUNT, 3, H_DIM))
        input_now = input_projection[:, :, timestep, :, :]
        update = tf.sigmoid(input_now[:, :, 0, :] + recurrent[:, :, 0, :])
        reset = tf.sigmoid(input_now[:, :, 1, :] + recurrent[:, :, 1, :])
        candidate = tf.tanh(input_now[:, :, 2, :]
                            + reset * recurrent[:, :, 2, :])
        hidden = (1.0 - update) * hidden + update * candidate
    return hidden


def _tensorflow_forward(tf, parameters, variant: str, x_2hop, snap_cur,
                        hist_seq, x_pkt_global, x_pkt_port, *,
                        include_aux: bool = False):
    """Shared TensorFlow forward used by training and the focused parity test."""
    if _architecture_for_variant(variant) == "frame":
        h = tf.nn.silu(snap_cur @ parameters["frame.w"] + parameters["frame.b"])
    else:
        graph = _tf_local_attention(tf, x_2hop, snap_cur, parameters)
        spatial = tf.concat((graph, snap_cur), axis=-1)
        s0 = tf.nn.silu(spatial @ parameters["spatial.w"]
                        + parameters["spatial.b"])
        s0 = _tf_layer_norm(tf, s0, parameters["spatial.ln.gamma"],
                            parameters["spatial.ln.beta"])
        s_port = _tf_port_attention(tf, s0, parameters)
        t_port = _tf_gru(tf, hist_seq, parameters)
        gate = tf.sigmoid(tf.concat((s_port, t_port), axis=-1)
                          @ parameters["fusion.gate.w"]
                          + parameters["fusion.gate.b"])
        fused = gate * s_port + (1.0 - gate) * t_port
        h = _tf_layer_norm(tf, fused, parameters["fusion.ln.gamma"],
                           parameters["fusion.ln.beta"])

    global_embedding = tf.nn.silu(
        x_pkt_global @ parameters["pkt_global.w"]
        + parameters["pkt_global.b"])
    global_by_port = tf.broadcast_to(
        global_embedding[:, tf.newaxis, :], (tf.shape(h)[0], PORT_COUNT, 16))
    fused_input = tf.concat((h, x_pkt_port, global_by_port), axis=-1)
    packet_hidden = tf.nn.silu(
        fused_input @ parameters["packet_fuse.w"]
        + parameters["packet_fuse.b"])
    ego = tf.sigmoid(packet_hidden @ parameters["ego.w"] + parameters["ego.b"])
    ext = tf.nn.softplus(packet_hidden @ parameters["ext.w"]
                         + parameters["ext.b"])
    result = {"H": h, "ego": ego[..., 0], "ext": ext}
    if include_aux:
        hidden = tf.nn.silu(h @ parameters["ais.1.w"] + parameters["ais.1.b"])
        raw = hidden @ parameters["ais.2.w"] + parameters["ais.2.b"]
        ais = tf.stack((tf.sigmoid(raw[..., 0]), tf.tanh(raw[..., 1]),
                        tf.sigmoid(raw[..., 2])), axis=-1)
        result["ais"] = ais
        result["ais_raw"] = raw
    return result


def _load_previous_parameters(previous_weights: Any, variant: str
                              ) -> dict[str, np.ndarray]:
    required = _required_parameter_names(variant)
    if isinstance(previous_weights, NumpyPigou):
        candidate = previous_weights.parameters
    elif isinstance(previous_weights, (str, os.PathLike)):
        candidate = NumpyPigou.load(previous_weights).parameters
    elif isinstance(previous_weights, Mapping):
        missing = sorted(required - set(previous_weights))
        if missing:
            raise ValueError(
                "previous_weights is incompatible with the requested variant; "
                f"missing={missing}")
        candidate = {}
        for name in required:
            try:
                candidate[name] = np.asarray(previous_weights[name],
                                             dtype=np.float32)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"previous_weights[{name!r}] is not a float array") from exc
    else:
        raise TypeError(
            "previous_weights must be a NumpyPigou, array mapping, or path")
    missing = sorted(required - set(candidate))
    if missing:
        raise ValueError(
            "previous_weights is incompatible with the requested variant; "
            f"missing={missing}")
    return {
        name: np.array(candidate[name], dtype=np.float32, copy=True)
        for name in required
    }


def _tf_quantile_loss(tf, prediction, target):
    taus = tf.constant(QUANTILE_LEVELS, dtype=prediction.dtype)
    taus = tf.reshape(taus, (1, EXT_DIM))
    target = tf.reshape(target, (-1, 1))
    delta = target - prediction
    absolute = tf.abs(delta)
    huber = tf.where(absolute <= 1.0, 0.5 * tf.square(delta), absolute - 0.5)
    weight = tf.abs(taus - tf.cast(delta < 0.0, prediction.dtype))
    return weight * huber


def train(snapshots: dict, decisions: dict, *, seed: int, steps: int,
          batch_size: int, lr: float, variant: str = "full",
          previous_weights=None) -> tuple[NumpyPigou, dict]:
    """Train Pigou-MetaDRL offline and export its pure-NumPy deployment model.

    Ego supervision uses Huber loss on the selected sigmoid score
    (N,S,E,W -> 0,1,2,3). The full externality head uses quantile Huber loss
    over 16 fixed quantile levels. The
    ``mean`` variant uses the specified scalar MSE objective. AIS supervision
    is masked elementwise by ``y_valid[M,4,3]`` and keeps channel 1 signed
    with ``tanh``. ``no_ais`` and ``no_ext`` set the named auxiliary objective
    to exactly zero.
    """
    if variant not in VALID_VARIANTS:
        raise ValueError(f"unknown Pigou-MetaDRL variant {variant!r}")
    if int(seed) < 0:
        raise ValueError("seed must be non-negative")
    if int(steps) <= 0:
        raise ValueError("steps must be positive")
    if int(batch_size) <= 0:
        raise ValueError("batch_size must be positive")
    if not math.isfinite(float(lr)) or float(lr) <= 0.0:
        raise ValueError("lr must be a positive finite value")

    snapshot_data, decision_data, snapshot_count = _validate_training_inputs(
        snapshots, decisions, variant)
    previous = None
    if previous_weights is not None:
        previous = _load_previous_parameters(previous_weights, variant)

    tf = _import_tensorflow_213()
    try:
        try:
            tf.config.set_visible_devices([], "GPU")
        except RuntimeError as exc:
            visible_gpus = tf.config.list_logical_devices("GPU")
            if visible_gpus:
                raise LearningUnavailable(
                    "TensorFlow GPU devices were already initialized; "
                    "Pigou-MetaDRL training requires CPU execution") from exc
        tf.keras.utils.set_random_seed(int(seed))
        tf.keras.backend.set_floatx("float32")
        tf.config.experimental.enable_op_determinism()
    except LearningUnavailable:
        raise
    except Exception as exc:
        raise LearningUnavailable(
            "TensorFlow 2.13 deterministic CPU setup failed: "
            f"{type(exc).__name__}: {exc}") from exc
    for setter in (tf.config.threading.set_intra_op_parallelism_threads,
                   tf.config.threading.set_inter_op_parallelism_threads):
        try:
            setter(1)
        except RuntimeError:
            # A host process may have initialized TensorFlow before this call.
            pass

    initial_rng = np.random.default_rng(int(seed))
    initial = _training_initial_parameters(initial_rng, variant)
    if previous is not None:
        for name, value in previous.items():
            if value.shape != initial[name].shape:
                raise ValueError(
                    f"previous_weights[{name!r}] has shape {value.shape}; "
                    f"expected {initial[name].shape}")
            if not np.all(np.isfinite(value)):
                raise ValueError(
                    f"previous_weights[{name!r}] contains non-finite values")
        initial.update(previous)
    expected_training_count = _EXPECTED_TRAINING_PARAMETER_COUNT[variant]
    actual_training_count = int(sum(value.size for value in initial.values()))
    if actual_training_count != expected_training_count:
        raise AssertionError(
            f"training parameter count {actual_training_count} != "
            f"frozen count {expected_training_count}")

    variables = {}
    for index, (name, value) in enumerate(sorted(initial.items())):
        safe_name = re.sub(r"[^A-Za-z0-9_]", "_", f"pigou_{index}_{name}")
        variables[name] = tf.Variable(value, dtype=tf.float32,
                                      trainable=True, name=safe_name)
    stage_i_names = sorted(_stage_i_parameter_names(
        _architecture_for_variant(variant)))
    stage_ii_names = sorted(_stage_ii_parameter_names())
    initial_stage_i = {
        name: np.array(variables[name].numpy(), copy=True) for name in stage_i_names
    }
    initial_stage_ii = {
        name: np.array(variables[name].numpy(), copy=True)
        for name in stage_ii_names
    }

    tensor_x2 = tf.convert_to_tensor(snapshot_data["x_2hop"], dtype=tf.float32)
    tensor_snap = tf.convert_to_tensor(snapshot_data["snap_cur"], dtype=tf.float32)
    tensor_hist = tf.convert_to_tensor(snapshot_data["hist_seq"], dtype=tf.float32)
    tensor_y = tf.convert_to_tensor(snapshot_data["y_priv"], dtype=tf.float32)
    tensor_valid = tf.convert_to_tensor(snapshot_data["y_valid"], dtype=tf.bool)
    tensor_snap_id = tf.convert_to_tensor(decision_data["snap_id"],
                                          dtype=tf.int32)
    tensor_global = tf.convert_to_tensor(decision_data["x_pkt_global"],
                                         dtype=tf.float32)
    tensor_port = tf.convert_to_tensor(decision_data["x_pkt_port"],
                                       dtype=tf.float32)
    tensor_action = tf.convert_to_tensor(decision_data["action"], dtype=tf.int32)
    tensor_ego_target = tf.convert_to_tensor(decision_data["g_ego"],
                                             dtype=tf.float32)
    tensor_ext_target = tf.convert_to_tensor(decision_data["g_ext"],
                                             dtype=tf.float32)

    optimizer = tf.keras.optimizers.Adam(learning_rate=float(lr))
    rng = np.random.default_rng(int(seed) ^ 0x5049474F55)
    decision_count = int(decision_data["snap_id"].shape[0])
    sample_size = min(int(batch_size), decision_count)
    loss_history: list[float] = []
    last_components = {"total": float("nan"), "ego": float("nan"),
                       "ext": float("nan"), "ais": float("nan")}
    masked_ais_entries = 0
    valid_ais_entries = int(snapshot_data["y_valid"].sum())

    @tf.function
    def train_step(batch):
        with tf.GradientTape() as tape:
            snap_ids = tf.gather(tensor_snap_id,
                                 batch)
            x2_batch = tf.gather(tensor_x2, snap_ids)
            snap_batch = tf.gather(tensor_snap, snap_ids)
            hist_batch = tf.gather(tensor_hist, snap_ids)
            y_batch = tf.gather(tensor_y, snap_ids)
            valid_batch = tf.gather(tensor_valid, snap_ids)
            global_batch = tf.gather(tensor_global,
                                     batch)
            port_batch = tf.gather(tensor_port,
                                   batch)
            action_batch = tf.gather(tensor_action,
                                     batch)
            ego_target_batch = tf.gather(tensor_ego_target,
                                         batch)
            ext_target_batch = tf.gather(tensor_ext_target,
                                         batch)

            outputs = _tensorflow_forward(
                tf, variables, variant, x2_batch, snap_batch, hist_batch,
                global_batch, port_batch, include_aux=True)
            ego_selected = tf.gather(outputs["ego"], action_batch, axis=1, batch_dims=1)
            ego_error = tf.abs(ego_selected - ego_target_batch)
            ego_loss = tf.reduce_mean(tf.where(
                ego_error <= 1.0, 0.5 * tf.square(ego_error), ego_error - 0.5))

            ext_selected = tf.gather(outputs["ext"], action_batch, axis=1, batch_dims=1)
            if variant == "no_ext":
                ext_loss = tf.zeros((), dtype=tf.float32)
            elif variant == "mean":
                ext_loss = tf.reduce_mean(
                    tf.square(ext_selected[:, 0] - ext_target_batch))
            else:
                ext_loss = tf.reduce_mean(
                    _tf_quantile_loss(tf, ext_selected, ext_target_batch))

            if variant == "no_ais":
                ais_loss = tf.zeros((), dtype=tf.float32)
            else:
                mask = tf.cast(valid_batch, tf.float32)
                ais_squared_error = tf.square(outputs["ais"] - y_batch)
                ais_loss = (tf.reduce_sum(ais_squared_error * mask)
                            / tf.maximum(tf.reduce_sum(mask), 1.0))
            loss = ego_loss + ext_loss + 0.3 * ais_loss

        trainable = list(variables.values())
        gradients = tape.gradient(loss, trainable)
        pairs = [(gradient, variable) for gradient, variable in zip(gradients, trainable)
                 if gradient is not None]
        clipped, _ = tf.clip_by_global_norm([gradient for gradient, _ in pairs], 10.0)
        optimizer.apply_gradients(zip(clipped, [variable for _, variable in pairs]))
        return loss, ego_loss, ext_loss, ais_loss, valid_batch

    for _step in range(int(steps)):
        batch = rng.choice(decision_count, size=sample_size, replace=False)
        loss, ego_loss, ext_loss, ais_loss, valid_batch = train_step(
            tf.convert_to_tensor(batch, dtype=tf.int32))
        loss_value = float(loss.numpy())
        ego_value = float(ego_loss.numpy())
        ext_value = float(ext_loss.numpy())
        ais_value = float(ais_loss.numpy())
        if not all(math.isfinite(value) for value in
                   (loss_value, ego_value, ext_value, ais_value)):
            raise LearningUnavailable("Pigou-MetaDRL training produced a non-finite loss")
        for name, variable in variables.items():
            if not bool(tf.math.reduce_all(tf.math.is_finite(variable)).numpy()):
                raise LearningUnavailable(
                    f"Pigou-MetaDRL parameter {name!r} became non-finite")
        loss_history.append(loss_value)
        last_components = {
            "total": loss_value, "ego": ego_value,
            "ext": ext_value, "ais": ais_value,
        }

    exported = {
        name: np.array(variables[name].numpy(), dtype=np.float32, copy=True)
        for name in sorted(_required_parameter_names(variant))
    }
    model = NumpyPigou(exported, variant=variant)
    stage_i_changes = sum(
        int(not np.array_equal(initial_stage_i[name], exported[name]))
        for name in stage_i_names)
    stage_ii_changes = sum(
        int(not np.array_equal(initial_stage_ii[name], exported[name]))
        for name in stage_ii_names)
    debug = {
        "variant": variant,
        "seed": int(seed),
        "steps": int(steps),
        "batch_size": int(sample_size),
        "lr": float(lr),
        "snapshot_count": int(snapshot_count),
        "decision_count": int(decision_count),
        "loss_history": loss_history,
        "loss_total": last_components["total"],
        "loss_ego": last_components["ego"],
        "loss_ext": last_components["ext"],
        "loss_ais": last_components["ais"],
        "online_parameter_count": model.parameter_count,
        "training_parameter_count": actual_training_count,
        "stage_i_parameter_count": model.stage_i_parameter_count,
        "stage_ii_parameter_count": model.stage_ii_parameter_count,
        "stage_i_changed_parameters": int(stage_i_changes),
        "stage_ii_changed_parameters": int(stage_ii_changes),
        "valid_ais_entries_per_snapshot": valid_ais_entries,
        "masked_ais_entries_per_snapshot": int(
            snapshot_data["y_valid"].size - valid_ais_entries),
        "ext_dim": model.ext_dim,
        "direction_order": list(DIRECTIONS),
        "ais_output_channels": ["sigmoid_occupancy", "signed_slope_tanh",
                                "sigmoid_bypass"],
        "deterministic_cpu": True,
    }
    return model, debug


__all__ = [
    "DIRECTIONS", "NumpyPigou", "QUANTILE_LEVELS", "train",
    "_EXPECTED_ONLINE_PARAMETER_COUNT", "_EXPECTED_TRAINING_PARAMETER_COUNT",
    "_import_tensorflow_213", "_numpy_encode", "_numpy_score",
    "_tensorflow_forward",
]
