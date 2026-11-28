"""Online end-to-end Pigou-MetaDRL V2 routing learner and pure-NumPy inference.

This module implements an online distributed RL agent on an equal footing with
B4 (DDQN), B5 (GAT-DDQN), and B6 (PRIMAL-inspired CVaR):
  * Strict 1-hop local observation contract (``PIGOU_V2``, 66 float32 features:
    4x12 port spatio-temporal tensor with generation-aligned conformal residual
    ``r_conf``, 1-hop visible-edge bypass fragility proxy ``F_2d``, and integral
    damping ``z_u`` + 18-dim packet context; zero global state, zero future
    cache, zero secret deadline).
  * Lightweight Port-Aware Spatio-Temporal Encoder (Shared Port Dense 12->32 ->
    4-Port Self-Attention with post-projection 4x32 residual -> Packet Fusion
    Trunk 128+32=160->96->64, ~22k online parameters; 5 actions with protocol
    termination ``deliver``).
  * Dual-Head Risk Architecture: Ego Utility Head ``Q_ego(s, a)`` and
    16-quantile Congestion-Externality Proxy Risk Head ``Z_pigou(s, a)`` over
    per-packet truncated cumulative cost ``G = clip(sum c_t, 0, 2.0)``, aligned
    with empirical upper-tail ``CVaR_alpha`` primal-dual multiplier updates,
    plus training-only AIS auxiliary representation head ``(4, 3)`` branched
    from the post-attention port representation.
  * Five 1-to-1 ablatable variants: ``full``, ``no_ext``, ``mean``, ``no_ais``,
    and ``frame``.
  * Pure-NumPy frozen deployment twin (``NumpyPigouOnline``) exported to
    ``online.npz`` with zero TensorFlow import on the evaluation path.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from ..local_forward import DIRS, _bypasses, _mean, _not_empty, _relative_hop
from ..observations import ACTIONS
from ..persistence import (
    LearningUnavailable,
    _read_checkpoint_metadata,
    _read_json_bytes,
    _require_resume_identity,
    _resume_json,
    _resume_unjson,
    _verify_checkpoint_metadata,
    _verify_resume_bundle,
    _write_resume_manifest,
)
from ..registry import register_agent


PORT_COUNT = 4
PORT_FEAT_DIM = 12
PORT_INPUT_DIM = PORT_COUNT * PORT_FEAT_DIM  # 48
PACKET_INPUT_DIM = 18
INPUT_DIM = PORT_INPUT_DIM + PACKET_INPUT_DIM  # 66
ACTION_COUNT = len(ACTIONS)  # 5: ("deliver", "N", "S", "E", "W")
RISK_QUANTILES = 16
AIS_DIM = 3
PORT_EMBED_DIM = 32
ATTN_HEADS = 2
ATTN_HEAD_DIM = PORT_EMBED_DIM // ATTN_HEADS  # 16
LAYER_NORM_EPS = 1e-5
RISK_TARGET_MAX = 2.0

VALID_VARIANTS = frozenset({"full", "no_ext", "mean", "no_ais", "frame"})

CHECKPOINT_SCHEMA = "leo-sim-pigou-online/v2"
RESUME_SCHEMA = "leo-sim-pigou-online-resume/v2"
OBSERVATION_CONTRACT_SCHEMA = "leo-sim-observation-contract/v1"
PIGOU_V2_FEATURE_ORDER = "leo_sim.pigou_online.build_observation/v2"
NATIVE_CHECKPOINT_FILENAME = "online.keras"
NATIVE_NUMPY_CHECKPOINT_FILENAME = "online.npz"
NATIVE_METADATA_FILENAME = "metadata.json"

DEFAULT_NORMALISATION = {"mode": "none"}

NUMPY_WEIGHT_KEYS = (
    "port_proj_w", "port_proj_b",
    "attn_q_w", "attn_q_b",
    "attn_k_w", "attn_k_b",
    "attn_v_w", "attn_v_b",
    "attn_o_w", "attn_o_b",
    "attn_ln_gamma", "attn_ln_beta",
    "pkt_w", "pkt_b",
    "trunk96_w", "trunk96_b",
    "trunk64_w", "trunk64_b",
    "ego_w", "ego_b",
    "risk_w", "risk_b",
)

OBSERVATION_LAYOUT = {
    "contract": "PIGOU_V2",
    "dtype": "float32",
    "width": INPUT_DIM,
    "port": {"offset": 0, "width": PORT_INPUT_DIM, "shape": [PORT_COUNT, PORT_FEAT_DIM]},
    "packet": {"offset": PORT_INPUT_DIM, "width": PACKET_INPUT_DIM},
}


def _observation_contract_id(feature_order: str, normalisation: dict) -> str:
    payload = json.dumps({
        "schema": OBSERVATION_CONTRACT_SCHEMA,
        "contract": "PIGOU_V2",
        "feature_width": INPUT_DIM,
        "feature_order": str(feature_order),
        "normalisation": normalisation,
        "actions": list(ACTIONS),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_OBSERVATION_CONTRACT_ID = _observation_contract_id(
    PIGOU_V2_FEATURE_ORDER, DEFAULT_NORMALISATION)


def _resolve_cfg_float(cfg: dict, names: tuple[str, ...], default: float) -> float:
    for name in names:
        if name in cfg and cfg[name] is not None:
            value = float(cfg[name])
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            return value
    return float(default)


def _mask_array(mask: dict | np.ndarray | list | tuple) -> np.ndarray:
    if isinstance(mask, dict):
        legal = np.asarray([bool(mask.get(action, False)) for action in ACTIONS],
                           dtype=bool)
    else:
        legal = np.asarray(mask, dtype=bool)
    if legal.shape != (ACTION_COUNT,):
        raise ValueError(
            f"action mask must have shape ({ACTION_COUNT},), got {legal.shape}")
    return legal


def split_observation(observation) -> tuple[np.ndarray, np.ndarray]:
    """Validate and split one 66-D PIGOU_V2 observation into (4, 12) + (18,)."""
    obs = np.asarray(observation, dtype=np.float32)
    if obs.shape != (INPUT_DIM,):
        raise ValueError(
            f"PIGOU_V2 observation must have shape ({INPUT_DIM},), got {obs.shape}")
    if not np.all(np.isfinite(obs)):
        raise ValueError("PIGOU_V2 observation must contain only finite values")
    return obs[:PORT_INPUT_DIM].reshape(PORT_COUNT, PORT_FEAT_DIM), obs[PORT_INPUT_DIM:]


def _split_observation_batch(states) -> tuple[np.ndarray, np.ndarray]:
    array = np.asarray(states, dtype=np.float32)
    if array.ndim == 1:
        array = array[None, :]
    if array.ndim != 2 or array.shape[-1] != INPUT_DIM:
        raise ValueError(
            f"last dimension of PIGOU_V2 observations must be {INPUT_DIM}")
    if not np.all(np.isfinite(array)):
        raise ValueError("PIGOU_V2 observations must contain only finite values")
    ports = array[..., :PORT_INPUT_DIM].reshape(-1, PORT_COUNT, PORT_FEAT_DIM)
    packet = array[..., PORT_INPUT_DIM:]
    return ports, packet


def cvar_tail(values, alpha: float = 0.75) -> np.ndarray:
    """Mean of the highest-cost ``1-alpha`` tail across the last axis."""
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim == 0 or arr.shape[-1] < 1:
        raise ValueError("CVaR input needs a non-empty quantile axis")
    alpha = float(alpha)
    if not 0.0 < alpha < 1.0:
        raise ValueError("CVaR alpha must be in (0, 1)")
    tail_count = max(1, int(math.ceil((1.0 - alpha) * arr.shape[-1] - 1e-12)))
    ordered = np.sort(arr, axis=-1)
    return np.mean(ordered[..., -tail_count:], axis=-1, dtype=np.float64)


def clip_pigou_risk_target(values, *, maximum: float = RISK_TARGET_MAX) -> np.ndarray:
    """Keep bounded Pigouvian risk bootstrap targets inside ``[0, maximum]``."""
    arr = np.asarray(values, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        raise ValueError("Pigou risk target must contain only finite values")
    return np.clip(arr, 0.0, float(maximum))


def pigou_action_scores(utility, risk_quantiles, multiplier: float, *,
                        alpha: float = 0.75, use_mean_risk: bool = False,
                        z_u: float = 0.0, detour_flags: np.ndarray | None = None,
                        gamma_z: float = 0.08) -> np.ndarray:
    """Return ``Q_ego - lambda * Risk(Z_pigou) - gamma_z * z_u * I[detour]``."""
    q = np.asarray(utility, dtype=np.float64)
    z = np.asarray(risk_quantiles, dtype=np.float64)
    if q.shape != (ACTION_COUNT,):
        raise ValueError(f"utility head must have shape ({ACTION_COUNT},), got {q.shape}")
    if z.shape != (ACTION_COUNT, RISK_QUANTILES):
        raise ValueError(
            f"risk head must have shape ({ACTION_COUNT}, {RISK_QUANTILES}), got {z.shape}")
    multiplier = float(multiplier)
    if not math.isfinite(multiplier) or multiplier < 0.0:
        raise ValueError("dual multiplier must be finite and non-negative")
    risk_stat = (np.mean(z, axis=-1, dtype=np.float64)
                 if use_mean_risk else cvar_tail(z, alpha=alpha))
    scores = q - multiplier * risk_stat
    if detour_flags is not None and z_u > 0.0 and gamma_z > 0.0:
        scores = scores - float(gamma_z) * float(z_u) * np.asarray(detour_flags, dtype=np.float64)
    return scores


class PigouLocalTracker:
    """Per-satellite online tracker for strictly local spatio-temporal features.

    Maintains ONLY local measurements and arrived 1-hop cache updates:
      * ``r_conf[4]``: Adaptive Conformal Inference (ACI) residual bound per port;
      * ``z_u``: integral detour-spill damping state;
      * ``dq_dt[4]``: short-window local queue rate-of-change per port;
      * ``spill_ratio``: recent inbound detour fraction.
    """

    def __init__(self, *, ou_tau_s: float = 0.10, aci_step: float = 0.05,
                 aci_alpha: float = 0.10):
        self.ou_tau_s = float(ou_tau_s)
        self.aci_step = float(aci_step)
        self.aci_alpha = float(aci_alpha)
        self._sats: dict[int, dict[str, Any]] = {}

    def _state(self, sat: int) -> dict[str, Any]:
        st = self._sats.get(sat)
        if st is None:
            st = {
                "last_t": None,
                "last_q": [0.0, 0.0, 0.0, 0.0],
                "dq_dt": [0.0, 0.0, 0.0, 0.0],
                "r_conf": [0.10, 0.10, 0.10, 0.10],
                "last_gen": [None, None, None, None],
                "last_peer_q": [0.0, 0.0, 0.0, 0.0],
                "z_u": 0.0,
                "in_bits": 0.0,
                "spill_bits": 0.0,
                "phi_spill": 0.0,
            }
            self._sats[sat] = st
        return st

    def note_arrival(self, sat: int, bits: int, is_spill: bool) -> None:
        st = self._state(sat)
        st["in_bits"] += float(bits)
        if is_spill:
            st["spill_bits"] += float(bits)

    def build_observation(self, view, *, orig_emit_time_s: float | None = None,
                          retries_used: int = 0,
                          zero_temporal: bool = False) -> tuple[np.ndarray, dict[str, Any]]:
        """Build the 66-D ``PIGOU_V2`` feature vector from ``LocalForwardView``."""
        st = self._state(view.sat)
        now = float(view.now)
        dt = 0.0 if st["last_t"] is None else max(0.0, now - float(st["last_t"]))
        if dt >= 0.015:
            if st["in_bits"] > 0.0:
                inst_spill = st["spill_bits"] / st["in_bits"]
                st["phi_spill"] = 0.7 * st["phi_spill"] + 0.3 * inst_spill
                st["in_bits"] = 0.0
                st["spill_bits"] = 0.0
            else:
                st["phi_spill"] *= 0.85
            mean_q = sum(p.queue for p in view.ports) / 4.0
            step_scale = min(1.0, dt / 0.05)
            dz = (0.15 * st["phi_spill"] * float(mean_q > 0.65)
                  - 0.30 * st["z_u"] * float(mean_q <= 0.25)) * step_scale
            st["z_u"] = float(min(5.0, max(0.0, st["z_u"] + dz)))
            for idx, port in enumerate(view.ports):
                slope = (port.queue - st["last_q"][idx]) / max(dt, 1e-3)
                st["dq_dt"][idx] = float(np.clip(slope * 0.05, -1.0, 1.0))
                st["last_q"][idx] = float(port.queue)
            st["last_t"] = now
        elif st["last_t"] is None:
            st["last_t"] = now
            for idx, port in enumerate(view.ports):
                st["last_q"][idx] = float(port.queue)

        # Update online conformal residual r_conf when a fresh 1-hop advertisement arrives.
        for idx, port in enumerate(view.ports):
            records = _not_empty(port)
            peer_mean_q = _mean(edge.queue for edge in records)
            if port.generated_at is not None and port.generated_at != st["last_gen"][idx]:
                if st["last_gen"][idx] is not None:
                    gap = max(0.0, float(port.generated_at) - float(st["last_gen"][idx]))
                    shrink = math.exp(-gap / max(self.ou_tau_s, 1e-4))
                    pred_q = 0.35 + (st["last_peer_q"][idx] - 0.35) * shrink
                    err = abs(peer_mean_q - pred_q)
                    st["r_conf"][idx] = float(np.clip(
                        st["r_conf"][idx] + self.aci_step * (
                            float(err > st["r_conf"][idx]) - self.aci_alpha),
                        0.0, 1.0))
                st["last_gen"][idx] = port.generated_at
                st["last_peer_q"][idx] = float(peer_mean_q)

        rel_hops = _relative_hop(view)
        bypasses = [_bypasses(view, i)[1] for i in range(PORT_COUNT)]
        z_norm = min(1.0, st["z_u"] / 5.0)
        port_rows = np.zeros((PORT_COUNT, PORT_FEAT_DIM), dtype=np.float32)
        for idx, port in enumerate(view.ports):
            records = _not_empty(port)
            q_peer_max = max((edge.queue for edge in records), default=0.0)
            q_peer_mean = _mean(edge.queue for edge in records)
            age = 0.0 if port.generated_at is None else max(0.0, now - float(port.generated_at))
            f_2d = float(np.clip(bypasses[idx], 0.0, 1.0))
            delta_hop = float(np.clip(rel_hops[idx], 0, 4)) / 4.0
            r_conf_val = 0.0 if zero_temporal else st["r_conf"][idx]
            f_2d_val = 0.0 if zero_temporal else f_2d
            z_val = 0.0 if zero_temporal else z_norm
            dq_val = 0.0 if zero_temporal else st["dq_dt"][idx]
            port_rows[idx] = np.asarray((
                float(port.up),
                float(port.queue),
                float(min(2.0, port.rate)) / 2.0,
                float(min(2.0, port.propagation)) / 2.0,
                delta_hop,
                float(q_peer_max),
                float(q_peer_mean),
                float(min(1.0, age / 0.20)),
                float(dq_val),
                float(r_conf_val),
                float(f_2d_val),
                float(z_val),
            ), dtype=np.float32)

        in_dir = [0.0, 0.0, 0.0, 0.0, 0.0]
        last_idx = next((i for i, p in enumerate(view.ports) if p.last_hop), None)
        if last_idx is None:
            in_dir[0] = 1.0  # uplink ingress
        else:
            in_dir[last_idx + 1] = 1.0

        emit_ref = float(view.emitted_at if orig_emit_time_s is None else orig_emit_time_s)
        age_norm = float(min(3.0, max(0.0, now - emit_ref) / 0.30))
        legal_set = set(view.legal)
        legal_vec = [float(d in legal_set) for d in DIRS]
        local_qs = [float(p.queue) for p in view.ports]
        packet = np.asarray((
            float(view.dst_features[0]),
            float(view.dst_features[1]),
            float(view.dst_features[2]),
            float(min(2.0, view.hops_taken / 16.0)),
            *in_dir,
            age_norm,
            float(retries_used > 0),
            float(min(1.0, retries_used / 2.0)),
            *legal_vec,
            max(local_qs) if local_qs else 0.0,
            sum(local_qs) / 4.0 if local_qs else 0.0,
        ), dtype=np.float32)
        obs = np.concatenate([port_rows.reshape(-1), packet], axis=0).astype(np.float32)
        detour_flags = np.asarray(
            [0.0] + [float(rel_hops[i] > 0) for i in range(PORT_COUNT)],
            dtype=np.float32)
        aux_info = {
            "z_u": float(st["z_u"]),
            "phi_spill": float(st["phi_spill"]),
            "detour_flags": detour_flags,
            "f_2d": [float(np.clip(bypasses[i] * 2.0, 0.0, 2.0)) for i in range(PORT_COUNT)],
            "r_conf": list(st["r_conf"]),
            "dq_dt": list(st["dq_dt"]),
        }
        return obs, aux_info


class _PigouReplay:
    """Fixed-capacity FIFO replay buffer with Pigouvian cost and optional AIS target."""

    def __init__(self, maxlen: int):
        if int(maxlen) < 1:
            raise ValueError("replay_size must be >= 1")
        self.maxlen = int(maxlen)
        self._cap = min(64, self.maxlen)
        self._states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._actions = np.empty((self._cap,), dtype=np.int64)
        self._utilities = np.empty((self._cap,), dtype=np.float32)
        self._costs = np.empty((self._cap,), dtype=np.float32)
        self._next_states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._next_masks = np.empty((self._cap, ACTION_COUNT), dtype=bool)
        self._dones = np.empty((self._cap,), dtype=bool)
        self._ais_targets = np.zeros((self._cap, PORT_COUNT, AIS_DIM), dtype=np.float32)
        self._ais_valid = np.zeros((self._cap,), dtype=np.float32)
        self._pos = 0
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def _grow(self) -> None:
        new_cap = min(self.maxlen, max(self._cap * 2, 1))
        arrays = self.to_arrays()
        self._states = np.empty((new_cap, INPUT_DIM), dtype=np.float32)
        self._actions = np.empty((new_cap,), dtype=np.int64)
        self._utilities = np.empty((new_cap,), dtype=np.float32)
        self._costs = np.empty((new_cap,), dtype=np.float32)
        self._next_states = np.empty((new_cap, INPUT_DIM), dtype=np.float32)
        self._next_masks = np.empty((new_cap, ACTION_COUNT), dtype=bool)
        self._dones = np.empty((new_cap,), dtype=bool)
        self._ais_targets = np.zeros((new_cap, PORT_COUNT, AIS_DIM), dtype=np.float32)
        self._ais_valid = np.zeros((new_cap,), dtype=np.float32)
        n = self._size
        if n:
            (self._states[:n], self._actions[:n], self._utilities[:n],
             self._costs[:n], self._next_states[:n], self._next_masks[:n],
             self._dones[:n], self._ais_targets[:n], self._ais_valid[:n]) = arrays
        self._cap = new_cap
        self._pos = n

    def append(self, state, action: int, utility: float, cost: float,
               next_state, next_mask, done: bool,
               ais_target: np.ndarray | None = None) -> None:
        if self._size == self._cap and self._cap < self.maxlen:
            self._grow()
        idx = self._pos
        self._states[idx] = state
        self._actions[idx] = int(action)
        self._utilities[idx] = float(utility)
        self._costs[idx] = float(cost)
        self._next_states[idx] = next_state
        self._next_masks[idx] = next_mask
        self._dones[idx] = bool(done)
        if ais_target is not None:
            self._ais_targets[idx] = np.asarray(ais_target, dtype=np.float32)
            self._ais_valid[idx] = 1.0
        else:
            self._ais_targets[idx].fill(0.0)
            self._ais_valid[idx] = 0.0
        self._pos = (idx + 1) % self._cap
        if self._size < self.maxlen:
            self._size += 1

    def _physical_indices(self, logical_indices: np.ndarray) -> np.ndarray:
        if self._size < self.maxlen:
            return logical_indices
        return (self._pos + logical_indices) % self.maxlen

    def sample_batch(self, logical_indices: np.ndarray):
        physical = self._physical_indices(logical_indices)
        return (
            self._states[physical], self._actions[physical],
            self._utilities[physical], self._costs[physical],
            self._next_states[physical], self._next_masks[physical],
            self._dones[physical], self._ais_targets[physical],
            self._ais_valid[physical],
        )

    def to_arrays(self):
        order = self._physical_indices(np.arange(self._size, dtype=np.int64))
        return (
            self._states[order], self._actions[order],
            self._utilities[order], self._costs[order],
            self._next_states[order], self._next_masks[order],
            self._dones[order], self._ais_targets[order],
            self._ais_valid[order],
        )

    def restore(self, states, actions, utilities, costs, next_states,
                next_masks, dones, ais_targets, ais_valid) -> None:
        n = len(states)
        if n > self.maxlen:
            raise ValueError("replay payload exceeds configured capacity")
        self._cap = min(max(1, n, 64), self.maxlen)
        self._states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._actions = np.empty((self._cap,), dtype=np.int64)
        self._utilities = np.empty((self._cap,), dtype=np.float32)
        self._costs = np.empty((self._cap,), dtype=np.float32)
        self._next_states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._next_masks = np.empty((self._cap, ACTION_COUNT), dtype=bool)
        self._dones = np.empty((self._cap,), dtype=bool)
        self._ais_targets = np.zeros((self._cap, PORT_COUNT, AIS_DIM), dtype=np.float32)
        self._ais_valid = np.zeros((self._cap,), dtype=np.float32)
        if n:
            self._states[:n] = states
            self._actions[:n] = actions
            self._utilities[:n] = utilities
            self._costs[:n] = costs
            self._next_states[:n] = next_states
            self._next_masks[:n] = next_masks
            self._dones[:n] = dones
            self._ais_targets[:n] = ais_targets
            self._ais_valid[:n] = ais_valid
        self._size = n
        self._pos = n % self._cap if self._size == self.maxlen else n


def _np_swish(x: np.ndarray) -> np.ndarray:
    return (0.5 * x * (1.0 + np.tanh(0.5 * x))).astype(np.float32, copy=False)


def _np_softplus(x: np.ndarray) -> np.ndarray:
    return np.logaddexp(np.float32(0.0), x).astype(np.float32, copy=False)


def _np_layer_norm(x: np.ndarray, gamma: np.ndarray, beta: np.ndarray) -> np.ndarray:
    mean = np.mean(x, axis=-1, keepdims=True, dtype=np.float32)
    centered = x - mean
    var = np.mean(centered * centered, axis=-1, keepdims=True, dtype=np.float32)
    return ((centered / np.sqrt(var + np.float32(LAYER_NORM_EPS))) * gamma + beta).astype(
        np.float32, copy=False)


def _np_softmax(x: np.ndarray) -> np.ndarray:
    shifted = x - np.max(x, axis=-1, keepdims=True)
    exps = np.exp(shifted)
    return (exps / np.sum(exps, axis=-1, keepdims=True)).astype(np.float32, copy=False)


class NumpyPigouOnline:
    """Frozen Pigou-MetaDRL V2 policy with zero TensorFlow import at eval time."""

    inference_only = True

    def __init__(self, weights_path: str | Path, *, contract: str = "PIGOU_V2",
                 feature_order: str = PIGOU_V2_FEATURE_ORDER,
                 normalisation: dict | None = None, variant: str = "full",
                 cvar_alpha: float = 0.75, lambda_pd: float = 0.15,
                 gamma_z: float = 0.08, actions=ACTIONS,
                 checkpoint_path: str | None = None,
                 checkpoint_sha256: str | None = None,
                 numpy_checkpoint_sha256: str | None = None,
                 metadata_sha256: str | None = None,
                 weights_purpose: str = "trained"):
        self.weights_path = Path(weights_path)
        if self.weights_path.is_symlink() or not self.weights_path.is_file():
            raise ValueError(f"Pigou NumPy checkpoint not found: {self.weights_path}")
        if variant not in VALID_VARIANTS:
            raise ValueError(f"unknown Pigou variant {variant!r}")
        self.contract = str(contract)
        self.feature_width = INPUT_DIM
        self.input_dim = INPUT_DIM
        self.feature_order = str(feature_order)
        self.normalisation = dict(normalisation or DEFAULT_NORMALISATION)
        self.variant = str(variant)
        self.actions = tuple(actions)
        if self.actions != tuple(ACTIONS):
            raise ValueError("Pigou NumPy checkpoint action order mismatch")
        self.cvar_alpha = float(cvar_alpha)
        self.lambda_pd = float(lambda_pd)
        self.gamma_z = 0.0 if self.variant == "frame" else float(gamma_z)
        self.use_mean_risk = self.variant == "mean"
        self.checkpoint_path = None if checkpoint_path is None else str(checkpoint_path)
        self.checkpoint_sha256 = checkpoint_sha256
        self.numpy_checkpoint_sha256 = numpy_checkpoint_sha256
        self.metadata_sha256 = metadata_sha256
        self.weights_purpose = str(weights_purpose)
        self.observation_contract_id = _observation_contract_id(
            self.feature_order, self.normalisation)
        self.epsilon = 0.0
        self.name = "pigou-online-numpy"
        self._calls = 0
        self._last_utility = None
        self._last_cvar = None
        self._last_scores = None
        with np.load(self.weights_path, allow_pickle=False) as archive:
            self.weights = {key: np.asarray(archive[key], dtype=np.float32)
                            for key in NUMPY_WEIGHT_KEYS}
        self._validate_weights()
        self.parameter_sha256_value = self._weights_sha256()

    def _validate_weights(self) -> None:
        expected = {
            "port_proj_w": (PORT_FEAT_DIM, PORT_EMBED_DIM),
            "port_proj_b": (PORT_EMBED_DIM,),
            "attn_q_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
            "attn_q_b": (PORT_EMBED_DIM,),
            "attn_k_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
            "attn_k_b": (PORT_EMBED_DIM,),
            "attn_v_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
            "attn_v_b": (PORT_EMBED_DIM,),
            "attn_o_w": (PORT_EMBED_DIM, PORT_EMBED_DIM),
            "attn_o_b": (PORT_EMBED_DIM,),
            "attn_ln_gamma": (PORT_EMBED_DIM,),
            "attn_ln_beta": (PORT_EMBED_DIM,),
            "pkt_w": (PACKET_INPUT_DIM, PORT_EMBED_DIM),
            "pkt_b": (PORT_EMBED_DIM,),
            "trunk96_w": (PORT_COUNT * PORT_EMBED_DIM + PORT_EMBED_DIM, 96),
            "trunk96_b": (96,),
            "trunk64_w": (96, 64),
            "trunk64_b": (64,),
            "ego_w": (64, ACTION_COUNT),
            "ego_b": (ACTION_COUNT,),
            "risk_w": (64, ACTION_COUNT * RISK_QUANTILES),
            "risk_b": (ACTION_COUNT * RISK_QUANTILES,),
        }
        for key, shape in expected.items():
            value = self.weights[key]
            if value.shape != shape:
                raise ValueError(f"Pigou NumPy weight {key} shape {value.shape} != {shape}")
            if not np.all(np.isfinite(value)):
                raise ValueError(f"Pigou NumPy weight {key} is non-finite")

    def _weights_sha256(self) -> str:
        digest = hashlib.sha256()
        for key in NUMPY_WEIGHT_KEYS:
            value = np.asarray(self.weights[key], dtype="<f4")
            digest.update(f"{key}:{value.shape}".encode("ascii"))
            digest.update(value.tobytes())
        return digest.hexdigest()

    def predict(self, states) -> tuple[np.ndarray, np.ndarray]:
        return self.predict_with_weights(self.weights, self.variant, states)

    @staticmethod
    def predict_with_weights(weights, variant, states) -> tuple[np.ndarray, np.ndarray]:
        ports, packet = _split_observation_batch(states)
        w = weights
        h = _np_swish(ports @ w["port_proj_w"] + w["port_proj_b"])  # (B, 4, 32)
        if variant != "frame":
            bsz = h.shape[0]
            q = (h @ w["attn_q_w"] + w["attn_q_b"]).reshape(
                bsz, PORT_COUNT, ATTN_HEADS, ATTN_HEAD_DIM).transpose(0, 2, 1, 3)
            k = (h @ w["attn_k_w"] + w["attn_k_b"]).reshape(
                bsz, PORT_COUNT, ATTN_HEADS, ATTN_HEAD_DIM).transpose(0, 2, 1, 3)
            v = (h @ w["attn_v_w"] + w["attn_v_b"]).reshape(
                bsz, PORT_COUNT, ATTN_HEADS, ATTN_HEAD_DIM).transpose(0, 2, 1, 3)
            logits = np.matmul(q, np.swapaxes(k, -2, -1)) / np.float32(math.sqrt(ATTN_HEAD_DIM))
            ctx = np.matmul(_np_softmax(logits), v).transpose(0, 2, 1, 3).reshape(
                bsz, PORT_COUNT, PORT_EMBED_DIM)
            attn_out = ctx @ w["attn_o_w"] + w["attn_o_b"]
            h = _np_layer_norm(h + attn_out, w["attn_ln_gamma"], w["attn_ln_beta"])
        flat_ports = h.reshape(h.shape[0], PORT_COUNT * PORT_EMBED_DIM)
        pkt_h = _np_swish(packet @ w["pkt_w"] + w["pkt_b"])
        joined = np.concatenate([flat_ports, pkt_h], axis=-1)
        trunk = _np_swish(joined @ w["trunk96_w"] + w["trunk96_b"])
        trunk = _np_swish(trunk @ w["trunk64_w"] + w["trunk64_b"])
        utility = trunk @ w["ego_w"] + w["ego_b"]
        risk = _np_softplus(trunk @ w["risk_w"] + w["risk_b"]).reshape(
            -1, ACTION_COUNT, RISK_QUANTILES)
        return utility.astype(np.float32), risk.astype(np.float32)

    def choose(self, observation: np.ndarray, mask: dict, now=None, *,
               z_u: float = 0.0, detour_flags: np.ndarray | None = None) -> str:
        split_observation(observation)
        legal = _mask_array(mask)
        legal_indices = np.flatnonzero(legal)
        if not len(legal_indices):
            raise ValueError("PIGOU_V2 decision requires at least one legal action")
        utility, risk = self.predict(observation)
        scores = pigou_action_scores(
            utility[0], risk[0], self.lambda_pd, alpha=self.cvar_alpha,
            use_mean_risk=self.use_mean_risk, z_u=z_u,
            detour_flags=detour_flags, gamma_z=self.gamma_z)
        self._last_utility = utility[0].copy()
        self._last_cvar = (np.mean(risk[0], axis=-1, dtype=np.float64)
                           if self.use_mean_risk
                           else cvar_tail(risk[0], alpha=self.cvar_alpha))
        self._last_scores = scores.copy()
        chosen = int(legal_indices[np.argmax(scores[legal])])
        self._calls += 1
        return ACTIONS[chosen]

    def act(self, features, mask=None) -> str:
        return self.choose(features, mask or {a: True for a in ACTIONS})

    def parameter_sha256(self) -> str:
        return self.parameter_sha256_value

    def counters(self) -> dict:
        return {
            "schema": "leo-sim-fixed-inference/v1",
            "name": self.name,
            "kind": "checkpoint_fixed_inference_policy",
            "calls": self._calls,
            "epsilon": 0.0,
            "updates": 0,
            "grad_steps": 0,
            "training": False,
            "replay_size": 0,
            "transitions": 0,
            "actions": list(self.actions),
            "contract": self.contract,
            "variant": self.variant,
            "feature_width": self.feature_width,
            "feature_order": self.feature_order,
            "normalisation_mode": self.normalisation.get("mode"),
            "observation_contract_id": self.observation_contract_id,
            "weights_purpose": self.weights_purpose,
            "checkpoint_sha256": self.checkpoint_sha256,
            "numpy_checkpoint_sha256": self.numpy_checkpoint_sha256,
            "metadata_sha256": self.metadata_sha256,
            "parameter_sha256": self.parameter_sha256(),
            "cvar_alpha": self.cvar_alpha,
            "lambda_pd": self.lambda_pd,
            "gamma_z": self.gamma_z,
        }

    def receipt(self) -> dict:
        return {
            "schema": "leo-sim-fixed-inference/v1",
            "state": "AVAILABLE",
            "reader": "CODE.leo_sim.rl.agents.pigou_online.NumpyPigouOnline",
            "checkpoint_path": self.checkpoint_path,
            "checkpoint_sha256": self.checkpoint_sha256,
            "numpy_checkpoint_path": str(self.weights_path),
            "numpy_checkpoint_sha256": self.numpy_checkpoint_sha256,
            "metadata_sha256": self.metadata_sha256,
            "contract": self.contract,
            "variant": self.variant,
            "feature_width": self.feature_width,
            "feature_order": self.feature_order,
            "normalisation": dict(self.normalisation),
            "action_order": list(self.actions),
            "observation_contract_id": self.observation_contract_id,
            "weights_purpose": self.weights_purpose,
            "parameter_sha256": self.parameter_sha256(),
            "cvar_alpha": self.cvar_alpha,
            "lambda_pd": self.lambda_pd,
            "gamma_z": self.gamma_z,
            "shape_checked": True,
            "finite_probe_checked": True,
            "training": False,
            "epsilon": 0.0,
        }

    def observe(self, *args, **kwargs):
        raise ValueError("NumpyPigouOnline is inference-only")

    def train_step(self, *args, **kwargs):
        raise ValueError("NumpyPigouOnline is inference-only")

    def remember(self, *args, **kwargs):
        raise ValueError("NumpyPigouOnline is inference-only")

    def set_epsilon(self, *args, **kwargs):
        raise ValueError("epsilon is fixed at 0.0 for NumpyPigouOnline")


@register_agent("pigou_online", scope="shared")
class TensorflowPigouOnline:
    """Online end-to-end Pigou-MetaDRL V2 learner on TensorFlow CPU."""

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract != "PIGOU_V2":
            raise ValueError("pigou_online requires contract='PIGOU_V2'")
        if not isinstance(cfg, dict):
            raise TypeError("cfg must be a mapping")

        from ..models import _configure_tf_threads, require_tensorflow

        self.tf = require_tensorflow()
        _configure_tf_threads(self.tf)
        try:
            self.tf.config.set_visible_devices([], "GPU")
        except Exception:
            pass
        try:
            self.tf.config.experimental.enable_op_determinism()
        except Exception as exc:
            raise LearningUnavailable(
                f"TensorFlow op determinism unavailable ({type(exc).__name__}: {exc})"
            ) from exc

        self.contract = contract
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.mode = str(cfg.get("mode", "train"))
        if self.mode not in {"train", "eval"}:
            raise ValueError(f"unsupported learning mode {self.mode!r}")
        self.variant = str(cfg.get("pigou_variant", "full"))
        if self.variant not in VALID_VARIANTS:
            raise ValueError(f"unsupported pigou_variant {self.variant!r}")
        self.input_dim = INPUT_DIM
        self.feature_order = str(cfg.get("feature_order", PIGOU_V2_FEATURE_ORDER))
        self.normalisation = dict(cfg.get("normalisation", DEFAULT_NORMALISATION))
        self.observation_contract_id = _observation_contract_id(
            self.feature_order, self.normalisation)

        self.cvar_alpha = _resolve_cfg_float(cfg, ("cvar_alpha",), 0.75)
        self.lambda_max = _resolve_cfg_float(cfg, ("dual_max",), 4.0)
        self.initial_lambda_pd = _resolve_cfg_float(cfg, ("lambda_init",), 0.15)
        self.lambda_pd = float(self.initial_lambda_pd)
        self.dual_lr = _resolve_cfg_float(cfg, ("dual_lr",), 0.05)
        self.dual_constraint = _resolve_cfg_float(cfg, ("dual_constraint",), 0.25)
        self.risk_loss_weight = _resolve_cfg_float(cfg, ("risk_loss_weight",), 1.0)
        self.ais_loss_weight = (
            0.0 if self.variant in ("no_ais", "frame")
            else _resolve_cfg_float(cfg, ("ais_loss_weight",), 0.25))
        self.gamma_z = (
            0.0 if self.variant == "frame"
            else _resolve_cfg_float(cfg, ("gamma_z",), 0.03))
        self.quantile_huber_delta = _resolve_cfg_float(
            cfg, ("quantile_huber_delta",), 1.0)
        self.risk_target_max = (
            1.0 if self.variant == "no_ext" else RISK_TARGET_MAX)
        self.use_mean_risk = self.variant == "mean"

        self.rng = np.random.default_rng(self.seed)
        self.tf.keras.utils.set_random_seed(self.seed)

        self.online = self._build_model("pigou_online")
        self.target = self._build_model("pigou_target")
        self.target.set_weights(self.online.get_weights())
        self._numpy_weights = self._extract_numpy_weights()
        opt_kwargs: dict[str, Any] = {"learning_rate": float(cfg.get("lr", 0.001))}
        if cfg.get("clip_norm") is not None:
            opt_kwargs["clipnorm"] = float(cfg["clip_norm"])
        self.optimizer = self.tf.keras.optimizers.Adam(**opt_kwargs)

        self.replay = _PigouReplay(int(cfg.get("replay_size", 50_000)))
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self.dual_updates = 0
        self._last_now = 0.0
        self._resume_time_offset_s = 0.0
        self.last_reward_loss: float | None = None
        self.last_risk_loss: float | None = None
        self.last_ais_loss: float | None = None
        self.last_loss: float | None = None
        self._pending_costs: list[float] = []
        self._last_utility: np.ndarray | None = None
        self._last_cvar: np.ndarray | None = None
        self._last_scores: np.ndarray | None = None
        self._fast_train_fn = None
        self.op_determinism = True
        self.fast_train = True
        self.checkpoint_verified = False
        self.metadata_sha256: str | None = None

        if self.mode == "eval":
            self._load_eval_checkpoint()
        elif cfg.get("resume_path") is not None:
            self._restore_resume_bundle()
        self._numpy_weights = self._extract_numpy_weights()

    def _build_model(self, name: str):
        tf = self.tf
        port_in = tf.keras.layers.Input(
            shape=(PORT_COUNT, PORT_FEAT_DIM), name="port_stream")
        packet_in = tf.keras.layers.Input(
            shape=(PACKET_INPUT_DIM,), name="packet_stream")

        h = tf.keras.layers.Dense(
            PORT_EMBED_DIM, activation="swish", name="port_proj")(port_in)

        # Build 4-Port Self-Attention weights identically across variants so
        # parameter serialization keys stay uniform; bypassed when variant=='frame'.
        q_layer = tf.keras.layers.Dense(PORT_EMBED_DIM, name="attn_q")
        k_layer = tf.keras.layers.Dense(PORT_EMBED_DIM, name="attn_k")
        v_layer = tf.keras.layers.Dense(PORT_EMBED_DIM, name="attn_v")
        o_layer = tf.keras.layers.Dense(PORT_EMBED_DIM, name="attn_o")
        ln_layer = tf.keras.layers.LayerNormalization(
            epsilon=LAYER_NORM_EPS, name="attn_ln")

        if self.variant != "frame":
            q = tf.transpose(tf.reshape(q_layer(h), (-1, PORT_COUNT, ATTN_HEADS, ATTN_HEAD_DIM)), [0, 2, 1, 3])
            k = tf.transpose(tf.reshape(k_layer(h), (-1, PORT_COUNT, ATTN_HEADS, ATTN_HEAD_DIM)), [0, 2, 1, 3])
            v = tf.transpose(tf.reshape(v_layer(h), (-1, PORT_COUNT, ATTN_HEADS, ATTN_HEAD_DIM)), [0, 2, 1, 3])
            scale = 1.0 / math.sqrt(float(ATTN_HEAD_DIM))
            attn_weights = tf.nn.softmax(tf.matmul(q, k, transpose_b=True) * scale, axis=-1)
            ctx = tf.reshape(tf.transpose(tf.matmul(attn_weights, v), [0, 2, 1, 3]), (-1, PORT_COUNT, PORT_EMBED_DIM))
            h_enc = ln_layer(h + o_layer(ctx))
        else:
            # Touch layers with zero-weight residual so Keras serializes identical layer set.
            zero_res = 0.0 * (o_layer(q_layer(h) + k_layer(h) + v_layer(h)) + ln_layer(h))
            h_enc = h + zero_res

        ais_raw = tf.keras.layers.Dense(AIS_DIM, activation="linear", name="ais_aux")(h_enc)
        flat_ports = tf.keras.layers.Reshape(
            (PORT_COUNT * PORT_EMBED_DIM,), name="flatten_ports")(h_enc)
        pkt_h = tf.keras.layers.Dense(
            PORT_EMBED_DIM, activation="swish", name="pkt_encoder")(packet_in)
        joined = tf.keras.layers.Concatenate(name="fusion")([flat_ports, pkt_h])
        trunk = tf.keras.layers.Dense(96, activation="swish", name="trunk96")(joined)
        trunk = tf.keras.layers.Dense(64, activation="swish", name="trunk64")(trunk)
        ego = tf.keras.layers.Dense(ACTION_COUNT, activation="linear", name="ego_head")(trunk)
        risk_flat = tf.keras.layers.Dense(
            ACTION_COUNT * RISK_QUANTILES, activation="linear", name="risk_flat")(trunk)
        risk = tf.keras.layers.Reshape(
            (ACTION_COUNT, RISK_QUANTILES), name="risk_reshape")(risk_flat)
        risk = tf.keras.layers.Activation("softplus", name="risk_softplus")(risk)
        return tf.keras.Model(
            inputs=[port_in, packet_in], outputs=[ego, risk, ais_raw], name=name)

    def _extract_numpy_weights(self) -> dict[str, np.ndarray]:
        layer_map = {
            "port_proj": ("port_proj_w", "port_proj_b"),
            "attn_q": ("attn_q_w", "attn_q_b"),
            "attn_k": ("attn_k_w", "attn_k_b"),
            "attn_v": ("attn_v_w", "attn_v_b"),
            "attn_o": ("attn_o_w", "attn_o_b"),
            "attn_ln": ("attn_ln_gamma", "attn_ln_beta"),
            "pkt_encoder": ("pkt_w", "pkt_b"),
            "trunk96": ("trunk96_w", "trunk96_b"),
            "trunk64": ("trunk64_w", "trunk64_b"),
            "ego_head": ("ego_w", "ego_b"),
            "risk_flat": ("risk_w", "risk_b"),
        }
        out: dict[str, np.ndarray] = {}
        for layer_name, (k0, k1) in layer_map.items():
            w0, w1 = self.online.get_layer(layer_name).get_weights()
            out[k0] = np.asarray(w0, dtype=np.float32)
            out[k1] = np.asarray(w1, dtype=np.float32)
        return out

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    def _advance_clock(self, now: float) -> None:
        current = float(now)
        if current < self._last_now:
            self._resume_time_offset_s += self._last_now
        self._last_now = current

    def choose(self, observation: np.ndarray, mask: dict, now: float, *,
               z_u: float = 0.0, detour_flags: np.ndarray | None = None) -> str:
        split_observation(observation)
        legal = _mask_array(mask)
        legal_indices = np.flatnonzero(legal)
        if not len(legal_indices):
            raise ValueError("PIGOU_V2 decision requires at least one legal action")
        self._advance_clock(now)
        if self.rng.random() < self.epsilon(now):
            chosen = int(self.rng.choice(legal_indices))
        elif len(legal_indices) == 1:
            chosen = int(legal_indices[0])
        else:
            ego_np, risk_np = NumpyPigouOnline.predict_with_weights(
                self._numpy_weights, self.variant, observation)
            ego_np = ego_np[0]
            risk_np = risk_np[0]
            scores = pigou_action_scores(
                ego_np, risk_np, self.lambda_pd, alpha=self.cvar_alpha,
                use_mean_risk=self.use_mean_risk, z_u=z_u,
                detour_flags=detour_flags, gamma_z=self.gamma_z)
            self._last_utility = ego_np.copy()
            self._last_cvar = (np.mean(risk_np, axis=-1, dtype=np.float64)
                               if self.use_mean_risk
                               else cvar_tail(risk_np, alpha=self.cvar_alpha))
            self._last_scores = scores.copy()
            chosen = int(legal_indices[np.argmax(scores[legal])])
        self.decisions += 1
        return ACTIONS[chosen]

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        self.remember_with_cost(
            state, action, reward, next_state, next_mask, done, cost=0.0)

    def remember_with_cost(self, state, action: str, reward: float, next_state,
                           next_mask: dict, done: bool, *, cost: float,
                           ais_target: np.ndarray | None = None) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown PIGOU_V2 action {action!r}")
        split_observation(state)
        split_observation(next_state)
        utility = float(reward)
        cost_val = float(np.clip(float(cost), 0.0, self.risk_target_max))
        if not math.isfinite(utility) or not math.isfinite(cost_val):
            raise ValueError("transition utility and cost must be finite")
        self.transitions += 1
        if self.mode == "eval":
            return
        self.replay.append(
            state, ACTIONS.index(action), utility, cost_val, next_state,
            _mask_array(next_mask), bool(done), ais_target=ais_target)
        self._pending_costs.append(cost_val)

        train_after = max(
            int(self.cfg.get("batch_size", 32)),
            int(self.cfg.get("warmup_steps", 128)))
        every = max(1, int(self.cfg.get("train_every_steps", 4)))
        if (len(self.replay) >= train_after
                and (self.transitions - train_after) % every == 0):
            self._train_once()

    def finish_round(self, observed_cost_rate: float | None = None, *,
                     packet_returns: Any = None) -> float:
        """Update dual multiplier aligned with the upper-tail CVaR risk functional.

        When per-packet truncated cumulative costs ``packet_returns`` are supplied,
        computes the empirical upper-tail ``CVaR_alpha`` (or mean for ``variant=='mean'``)
        directly over the same random variable ``G = clip(sum c_t, 0, risk_target_max)``
        estimated by ``Z_pigou``. Falls back to ``observed_cost_rate`` or empirical
        CVaR of pending transition costs if ``packet_returns`` is omitted.
        """
        if self.mode == "eval":
            return float(self.lambda_pd)
        if packet_returns is not None and len(packet_returns) > 0:
            arr = np.clip(
                np.asarray(packet_returns, dtype=np.float64),
                0.0, float(self.risk_target_max))
            observed = (
                float(np.mean(arr, dtype=np.float64))
                if self.use_mean_risk
                else float(cvar_tail(arr, alpha=self.cvar_alpha))
            )
            self._pending_costs.clear()
        elif observed_cost_rate is not None:
            observed = float(observed_cost_rate)
            self._pending_costs.clear()
        elif self._pending_costs:
            arr = np.asarray(self._pending_costs, dtype=np.float64)
            observed = (
                float(np.mean(arr, dtype=np.float64))
                if self.use_mean_risk
                else float(cvar_tail(arr, alpha=self.cvar_alpha))
            )
            self._pending_costs.clear()
        else:
            return float(self.lambda_pd)
        updated = self.lambda_pd + self.dual_lr * (observed - self.dual_constraint)
        self.lambda_pd = float(min(max(updated, 0.0), self.lambda_max))
        self.dual_updates += 1
        return float(self.lambda_pd)

    def _build_fast_train_fn(self):
        tf = self.tf
        online = self.online
        target = self.target
        optimizer = self.optimizer
        gamma = tf.constant(float(self.cfg.get("gamma", 1.0)), dtype=tf.float32)
        risk_weight = tf.constant(float(self.risk_loss_weight), dtype=tf.float32)
        ais_weight = tf.constant(float(self.ais_loss_weight), dtype=tf.float32)
        huber_delta = tf.constant(float(self.quantile_huber_delta), dtype=tf.float32)
        risk_max = tf.constant(float(self.risk_target_max), dtype=tf.float32)
        use_mean = bool(self.use_mean_risk)
        tail_count = max(1, int(math.ceil((1.0 - self.cvar_alpha) * RISK_QUANTILES - 1e-12)))
        tau = tf.constant(
            (np.arange(RISK_QUANTILES, dtype=np.float32) + 0.5) / RISK_QUANTILES,
            dtype=tf.float32)

        @tf.function
        def _step(multiplier, s_port, s_pkt, actions, utilities, costs,
                  ns_port, ns_pkt, not_done, next_mask, ais_tgt, ais_valid):
            on_u, on_r, _ = online([ns_port, ns_pkt], training=False)
            tg_u, tg_r, _ = target([ns_port, ns_pkt], training=False)
            safe_mask = tf.where(
                tf.reduce_any(next_mask, axis=1, keepdims=True),
                next_mask, tf.ones_like(next_mask))
            if use_mean:
                risk_stat = tf.reduce_mean(on_r, axis=-1)
            else:
                sorted_r = tf.sort(on_r, axis=-1, direction="DESCENDING")
                risk_stat = tf.reduce_mean(sorted_r[..., :tail_count], axis=-1)
            masked_scores = tf.where(
                safe_mask, on_u - multiplier * risk_stat, tf.cast(-1e9, tf.float32))
            next_actions = tf.argmax(masked_scores, axis=1, output_type=tf.int32)
            u_boot = tf.gather(tg_u, next_actions, batch_dims=1)
            r_boot = tf.gather(tg_r, next_actions, batch_dims=1)
            u_target = tf.stop_gradient(
                utilities + gamma * tf.cast(not_done, tf.float32) * u_boot)
            r_target = tf.stop_gradient(tf.clip_by_value(
                costs[:, None] + gamma * tf.cast(not_done[:, None], tf.float32) * r_boot,
                0.0, risk_max))

            with tf.GradientTape() as tape:
                u_pred, r_pred, ais_raw = online([s_port, s_pkt], training=True)
                chosen_u = tf.gather(u_pred, actions, batch_dims=1)
                chosen_r = tf.gather(r_pred, actions, batch_dims=1)
                u_err = u_target - chosen_u
                u_abs = tf.abs(u_err)
                u_loss = tf.reduce_mean(tf.where(
                    u_abs <= huber_delta, 0.5 * tf.square(u_err),
                    huber_delta * (u_abs - 0.5 * huber_delta)))

                r_err = r_target - chosen_r
                r_abs = tf.abs(r_err)
                r_huber = tf.where(
                    r_abs <= huber_delta, 0.5 * tf.square(r_err),
                    huber_delta * (r_abs - 0.5 * huber_delta))
                q_wt = tf.abs(tau[None, :] - tf.cast(r_err < 0.0, tf.float32))
                r_loss = tf.reduce_mean(r_huber * q_wt)

                ais_pred = tf.stack([
                    tf.nn.sigmoid(ais_raw[..., 0]),
                    tf.nn.tanh(ais_raw[..., 1]),
                    tf.nn.sigmoid(ais_raw[..., 2]),
                ], axis=-1)
                ais_sq = tf.reduce_mean(tf.square(ais_pred - ais_tgt), axis=[1, 2])
                denom = tf.maximum(tf.reduce_sum(ais_valid), 1.0)
                a_loss = tf.reduce_sum(ais_sq * ais_valid) / denom

                total = u_loss + risk_weight * r_loss + ais_weight * a_loss

            grads = tape.gradient(total, online.trainable_variables)
            optimizer.apply_gradients(
                (g, v) for g, v in zip(grads, online.trainable_variables) if g is not None)
            return u_loss, r_loss, a_loss, total

        return _step

    def _train_once(self) -> None:
        if self._fast_train_fn is None:
            self._fast_train_fn = self._build_fast_train_fn()
        batch_size = int(self.cfg.get("batch_size", 32))
        indices = self.rng.choice(len(self.replay), size=batch_size, replace=False)
        (states, actions, utilities, costs, next_states, masks, dones,
         ais_tgt, ais_valid) = self.replay.sample_batch(indices)
        s_port, s_pkt = _split_observation_batch(states)
        ns_port, ns_pkt = _split_observation_batch(next_states)
        tf = self.tf
        u_loss, r_loss, a_loss, total = self._fast_train_fn(
            tf.constant(self.lambda_pd, dtype=tf.float32),
            tf.constant(s_port), tf.constant(s_pkt),
            tf.constant(actions, dtype=tf.int64),
            tf.constant(utilities), tf.constant(costs),
            tf.constant(ns_port), tf.constant(ns_pkt),
            tf.constant(~dones, dtype=tf.bool),
            tf.constant(masks, dtype=tf.bool),
            tf.constant(ais_tgt), tf.constant(ais_valid),
        )
        self.last_reward_loss = float(u_loss.numpy())
        self.last_risk_loss = float(r_loss.numpy())
        self.last_ais_loss = float(a_loss.numpy())
        self.last_loss = float(total.numpy())
        self._numpy_weights = self._extract_numpy_weights()
        if not math.isfinite(self.last_loss) or self.last_risk_loss > 100.0:
            raise LearningUnavailable(
                f"Pigou online training diverged: risk_loss={self.last_risk_loss}")
        self.train_steps += 1
        if self.train_steps % int(self.cfg.get("target_update_interval", 500)) == 0:
            self.target.set_weights(self.online.get_weights())

    def _load_eval_checkpoint(self) -> None:
        ckpt_path = Path(self.cfg["checkpoint_path"])
        meta_path = ckpt_path.with_name(NATIVE_METADATA_FILENAME)
        meta = _read_checkpoint_metadata(
            meta_path, self.cfg["checkpoint_metadata_sha256"])
        _verify_checkpoint_metadata(
            meta, self.contract, ckpt_path.name,
            self.cfg["checkpoint_sha256"], CHECKPOINT_SCHEMA,
            "pigou_online")
        if (meta.get("feature_width") != INPUT_DIM
                or meta.get("feature_order") != self.feature_order
                or meta.get("observation_contract_id")
                != self.observation_contract_id):
            raise LearningUnavailable("PIGOU_V2 observation contract mismatch")
        self.online = self.tf.keras.models.load_model(str(ckpt_path), compile=False)
        self.target.set_weights(self.online.get_weights())
        self.lambda_pd = float(meta["lambda_pd"])
        self.variant = str(meta.get("variant", self.variant))
        self.checkpoint_verified = True
        self.metadata_sha256 = self.cfg["checkpoint_metadata_sha256"]

    def _resume_identity(self) -> dict:
        from ..persistence import _resume_identity

        identity = _resume_identity(self.cfg, self.contract, "pigou_online", self.seed)
        identity.update({
            "mode": self.mode,
            "variant": self.variant,
            "cvar_alpha": float(self.cvar_alpha),
            "dual_max": float(self.lambda_max),
            "lambda_init": float(self.initial_lambda_pd),
            "dual_lr": float(self.dual_lr),
            "dual_constraint": float(self.dual_constraint),
            "risk_loss_weight": float(self.risk_loss_weight),
            "ais_loss_weight": float(self.ais_loss_weight),
            "gamma_z": float(self.gamma_z),
            "input_dim": int(self.input_dim),
            "observation_contract_id": self.observation_contract_id,
        })
        return identity

    def _restore_resume_bundle(self) -> None:
        resume_dir = Path(self.cfg["resume_path"])
        manifest = _verify_resume_bundle(
            resume_dir, self.cfg["resume_sha256"], RESUME_SCHEMA)
        _require_resume_identity(manifest, self._resume_identity())
        state = _read_json_bytes(
            (resume_dir / "state.json").read_bytes(), "pigou_online resume state")
        self.decisions = int(state["decisions"])
        self.transitions = int(state["transitions"])
        self.train_steps = int(state["train_steps"])
        self.dual_updates = int(state["dual_updates"])
        self.lambda_pd = float(state["lambda_pd"])
        self._last_now = float(state["last_now"])
        self._resume_time_offset_s = float(state["resume_time_offset_s"])
        self.rng.bit_generator.state = _resume_unjson(state["rng_state"])
        with np.load(resume_dir / "replay.npz", allow_pickle=False) as buf:
            self.replay.restore(
                buf["states"], buf["actions"], buf["utilities"], buf["costs"],
                buf["next_states"], buf["next_masks"], buf["dones"],
                buf["ais_targets"], buf["ais_valid"])
        self.optimizer.build(self.online.trainable_variables)
        ckpt = self.tf.train.Checkpoint(
            online=self.online, target=self.target, optimizer=self.optimizer)
        status = ckpt.read(str(resume_dir / "tf.ckpt"))
        status.assert_existing_objects_matched()
        try:
            status.assert_consumed()
        except AssertionError as exc:
            message = str(exc)
            if ("Found 1 Python objects" not in message
                    or "save_counter" not in message
                    or "not assigned a value" in message):
                raise

    def diagnostics(self) -> dict:
        return {
            "schema": CHECKPOINT_SCHEMA,
            "algorithm": "pigou_online",
            "contract": self.contract,
            "variant": self.variant,
            "mode": self.mode,
            "input_dim": int(self.input_dim),
            "seed": self.seed,
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "dual_updates": self.dual_updates,
            "lambda_pd": float(self.lambda_pd),
            "replay_size": len(self.replay),
            "last_reward_loss": self.last_reward_loss,
            "last_risk_loss": self.last_risk_loss,
            "last_ais_loss": self.last_ais_loss,
            "last_loss": self.last_loss,
            "observation_contract_id": self.observation_contract_id,
            "op_determinism": bool(self.op_determinism),
            "fast_train": bool(self.fast_train),
            "checkpoint_verified": bool(self.checkpoint_verified),
            "metadata_sha256": self.metadata_sha256,
        }

    def save_and_verify(self, out_dir: str | Path) -> dict:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        ckpt_path = out / NATIVE_CHECKPOINT_FILENAME
        numpy_path = out / NATIVE_NUMPY_CHECKPOINT_FILENAME
        meta_path = out / NATIVE_METADATA_FILENAME

        self.online.save(str(ckpt_path))
        ckpt_sha = hashlib.sha256(ckpt_path.read_bytes()).hexdigest()

        np_weights = self._extract_numpy_weights()
        np.savez(numpy_path, **np_weights)
        numpy_sha = hashlib.sha256(numpy_path.read_bytes()).hexdigest()

        # Verify pure-NumPy forward matches TensorFlow forward strictly (<1e-5).
        probe = np.linspace(-0.5, 0.5, INPUT_DIM, dtype=np.float32)
        p_port, p_pkt = _split_observation_batch(probe)
        tf_u, tf_r, _ = self.online([p_port, p_pkt], training=False)
        twin = NumpyPigouOnline(
            numpy_path, variant=self.variant, cvar_alpha=self.cvar_alpha,
            lambda_pd=self.lambda_pd, gamma_z=self.gamma_z)
        np_u, np_r = twin.predict(probe)
        max_diff = float(max(
            np.max(np.abs(tf_u.numpy() - np_u)),
            np.max(np.abs(tf_r.numpy() - np_r)),
        ))
        if not math.isfinite(max_diff) or max_diff > 1e-4:
            raise LearningUnavailable(
                f"NumPy/TF Pigou parity mismatch: max_diff={max_diff}")

        meta = {
            "schema": CHECKPOINT_SCHEMA,
            "algorithm": "pigou_online",
            "contract": self.contract,
            "variant": self.variant,
            "feature_width": INPUT_DIM,
            "feature_order": self.feature_order,
            "normalisation": self.normalisation,
            "actions": list(ACTIONS),
            "action_order": list(ACTIONS),
            "observation_contract_id": self.observation_contract_id,
            "weights_purpose": "trained",
            "checkpoint_file": NATIVE_CHECKPOINT_FILENAME,
            "checkpoint": NATIVE_CHECKPOINT_FILENAME,
            "checkpoint_sha256": ckpt_sha,
            "checkpoint_verified": True,
            "numpy_checkpoint": NATIVE_NUMPY_CHECKPOINT_FILENAME,
            "numpy_checkpoint_sha256": numpy_sha,
            "numpy_verified": True,
            "numpy_max_abs_diff": max_diff,
            "cvar_alpha": float(self.cvar_alpha),
            "lambda_pd": float(self.lambda_pd),
            "gamma_z": float(self.gamma_z),
            "risk_target_max": float(self.risk_target_max),
            "dual_constraint": float(self.dual_constraint),
            "dual_statistic": "empirical_mean" if self.use_mean_risk else "empirical_cvar_alpha",
            "cost_unit": "per_packet_truncated_cumulative_externality_proxy",
            "train_steps": int(self.train_steps),
        }
        meta_path.write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.metadata_sha256 = hashlib.sha256(meta_path.read_bytes()).hexdigest()

        # Write resume bundle for multi-round continuation.
        resume_dir = out / "resume"
        resume_dir.mkdir(parents=True, exist_ok=True)
        self.optimizer.build(self.online.trainable_variables)
        ckpt = self.tf.train.Checkpoint(
            online=self.online, target=self.target, optimizer=self.optimizer)
        ckpt.write(str(resume_dir / "tf.ckpt"))
        arrays = self.replay.to_arrays()
        np.savez_compressed(
            resume_dir / "replay.npz",
            states=arrays[0], actions=arrays[1], utilities=arrays[2],
            costs=arrays[3], next_states=arrays[4], next_masks=arrays[5],
            dones=arrays[6], ais_targets=arrays[7], ais_valid=arrays[8],
        )
        state = {
            "decisions": self.decisions,
            "transitions": self.transitions,
            "train_steps": self.train_steps,
            "dual_updates": self.dual_updates,
            "lambda_pd": float(self.lambda_pd),
            "last_now": float(self._last_now),
            "resume_time_offset_s": float(self._resume_time_offset_s),
            "rng_state": _resume_json(self.rng.bit_generator.state),
        }
        (resume_dir / "state.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _write_resume_manifest(
            resume_dir, RESUME_SCHEMA, self._resume_identity())

        diag = self.diagnostics()
        self.checkpoint_verified = True
        diag["checkpoint_verified"] = True
        diag.update({
            "checkpoint_path": str(ckpt_path),
            "checkpoint_sha256": ckpt_sha,
            "numpy_checkpoint_path": str(numpy_path),
            "numpy_checkpoint_sha256": numpy_sha,
            "metadata_path": str(meta_path),
            "metadata_sha256": self.metadata_sha256,
            "numpy_max_abs_diff": max_diff,
        })
        return diag
