"""PRIMAL-inspired online CVaR routing learner for the B6 baseline.

This module implements the platform-facing learner, not a reproduction of the
original PRIMAL, IQN, or Discrete-SAC training procedure.  It consumes the
50-field PRIMAL observation contract supplied by the kernel observation
builder: 32 satellite/control fields followed by 18 packet-context fields.

The learner uses two small encoders, a shared trunk, a five-action utility head,
and a five-action by sixteen-quantile risk-cost head.  The policy scores each
legal action by ``utility - lambda * CVaR_alpha(cost_quantiles)``.  The risk
head learns an externally supplied immediate cost, whose terminal convention is
documented by :meth:`remember_with_cost`.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

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


INPUT_DIM = 50
SATELLITE_INPUT_DIM = 32
PACKET_INPUT_DIM = 18
ACTION_COUNT = len(ACTIONS)
RISK_QUANTILES = 16

CHECKPOINT_SCHEMA = "leo-sim-primal-cvar/v1"
RESUME_SCHEMA = "leo-sim-primal-cvar-resume/v1"
OBSERVATION_CONTRACT_SCHEMA = "leo-sim-observation-contract/v1"
PRIMAL_FEATURE_ORDER = "leo_sim.primal.build_observation/v1"
NATIVE_CHECKPOINT_FILENAME = "online.keras"
NATIVE_NUMPY_CHECKPOINT_FILENAME = "online.npz"
NATIVE_METADATA_FILENAME = "metadata.json"

NUMPY_WEIGHT_KEYS = (
    "satellite_w", "satellite_b",
    "packet_w", "packet_b",
    "trunk128_w", "trunk128_b",
    "trunk64_w", "trunk64_b",
    "utility_w", "utility_b",
    "risk_w", "risk_b",
)

OBSERVATION_LAYOUT = {
    "contract": "PRIMAL",
    "dtype": "float32",
    "width": INPUT_DIM,
    "satellite": {"offset": 0, "width": SATELLITE_INPUT_DIM},
    "packet": {"offset": SATELLITE_INPUT_DIM, "width": PACKET_INPUT_DIM},
}


def _observation_contract_id(feature_order: str,
                             normalisation: dict) -> str:
    payload = json.dumps({
        "schema": OBSERVATION_CONTRACT_SCHEMA,
        "contract": "PRIMAL",
        "feature_width": INPUT_DIM,
        "feature_order": str(feature_order),
        "normalisation": normalisation,
        "actions": list(ACTIONS),
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_NORMALISATION = {"mode": "none"}
DEFAULT_OBSERVATION_CONTRACT_ID = _observation_contract_id(
    PRIMAL_FEATURE_ORDER, DEFAULT_NORMALISATION)


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
    """Validate and split one flat PRIMAL observation into 32+18 streams."""
    obs = np.asarray(observation, dtype=np.float32)
    if obs.shape != (INPUT_DIM,):
        raise ValueError(
            f"PRIMAL observation must have shape ({INPUT_DIM},), got {obs.shape}")
    if not np.all(np.isfinite(obs)):
        raise ValueError("PRIMAL observation must contain only finite values")
    return obs[:SATELLITE_INPUT_DIM], obs[SATELLITE_INPUT_DIM:]


def _split_observation_batch(states) -> tuple[np.ndarray, np.ndarray]:
    array = np.asarray(states, dtype=np.float32)
    if array.ndim == 1:
        array = array[None, :]
    if array.ndim != 2 or array.shape[-1] != INPUT_DIM:
        raise ValueError(
            f"last dimension of PRIMAL observations must be {INPUT_DIM}")
    if not np.all(np.isfinite(array)):
        raise ValueError("PRIMAL observations must contain only finite values")
    return array[..., :SATELLITE_INPUT_DIM], array[..., SATELLITE_INPUT_DIM:]


def cvar_tail(values, alpha: float = 0.75) -> np.ndarray:
    """Mean of the highest-cost ``1-alpha`` tail across the last axis.

    Quantile particles are treated as equiprobable.  With the B6 default of
    sixteen particles and ``alpha=0.75``, the result is the mean of the four
    highest predicted costs.
    """
    arr = np.asarray(values, dtype=np.float64)
    if arr.ndim == 0 or arr.shape[-1] < 1:
        raise ValueError("CVaR input needs a non-empty quantile axis")
    alpha = float(alpha)
    if not 0.0 < alpha < 1.0:
        raise ValueError("CVaR alpha must be in (0, 1)")
    tail_count = max(1, int(math.ceil((1.0 - alpha) * arr.shape[-1] - 1e-12)))
    ordered = np.sort(arr, axis=-1)
    return np.mean(ordered[..., -tail_count:], axis=-1, dtype=np.float64)


def clip_risk_target(values) -> np.ndarray:
    """Keep the bounded failure-cost bootstrap inside its physical domain."""
    arr = np.asarray(values, dtype=np.float32)
    if not np.all(np.isfinite(arr)):
        raise ValueError("risk target must contain only finite values")
    return np.clip(arr, 0.0, 1.0)


def risk_scores(utility, risk_quantiles, multiplier: float,
                alpha: float = 0.75) -> np.ndarray:
    """Return ``utility - lambda * CVaR(cost)`` for every action."""
    q = np.asarray(utility, dtype=np.float64)
    z = np.asarray(risk_quantiles, dtype=np.float64)
    if q.shape != (ACTION_COUNT,):
        raise ValueError(f"utility head must have shape ({ACTION_COUNT},), got {q.shape}")
    if z.shape != (ACTION_COUNT, RISK_QUANTILES):
        raise ValueError(
            "risk head must have shape "
            f"({ACTION_COUNT}, {RISK_QUANTILES}), got {z.shape}")
    multiplier = float(multiplier)
    if not math.isfinite(multiplier) or multiplier < 0.0:
        raise ValueError("dual multiplier must be finite and non-negative")
    return q - multiplier * cvar_tail(z, alpha=alpha)


def masked_risk_argmax(utility, risk_quantiles, mask, multiplier: float,
                       alpha: float = 0.75) -> int:
    """Argmax over legal actions under the risk-adjusted score."""
    legal = _mask_array(mask)
    if not np.any(legal):
        raise ValueError("PRIMAL decision requires at least one legal action")
    scores = risk_scores(utility, risk_quantiles, multiplier, alpha=alpha)
    legal_indices = np.flatnonzero(legal)
    return int(legal_indices[np.argmax(scores[legal])])


def project_dual(multiplier: float, observed_cost: float, budget: float,
                 step_size: float, maximum: float) -> float:
    """One projected dual-ascent update in ``[0, maximum]``."""
    values = (multiplier, observed_cost, budget, step_size, maximum)
    if not all(math.isfinite(float(value)) for value in values):
        raise ValueError("dual update inputs must be finite")
    multiplier, observed_cost, budget, step_size, maximum = map(float, values)
    if multiplier < 0.0 or observed_cost < 0.0 or step_size < 0.0 or maximum < 0.0:
        raise ValueError("dual update inputs must be non-negative")
    updated = multiplier + step_size * (observed_cost - budget)
    return float(min(max(updated, 0.0), maximum))


class _PrimalReplay:
    """FIFO replay with a separate immediate risk-cost channel."""

    def __init__(self, maxlen: int):
        self.maxlen = int(maxlen)
        if self.maxlen < 1:
            raise ValueError("replay capacity must be >= 1")
        self._cap = min(self.maxlen, 64)
        self._states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._actions = np.empty((self._cap,), dtype=np.int64)
        self._utilities = np.empty((self._cap,), dtype=np.float32)
        self._costs = np.empty((self._cap,), dtype=np.float32)
        self._next_states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._next_masks = np.empty((self._cap, ACTION_COUNT), dtype=bool)
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
        arrays = self.to_arrays()
        self._states = np.empty((new_cap, INPUT_DIM), dtype=np.float32)
        self._actions = np.empty((new_cap,), dtype=np.int64)
        self._utilities = np.empty((new_cap,), dtype=np.float32)
        self._costs = np.empty((new_cap,), dtype=np.float32)
        self._next_states = np.empty((new_cap, INPUT_DIM), dtype=np.float32)
        self._next_masks = np.empty((new_cap, ACTION_COUNT), dtype=bool)
        self._dones = np.empty((new_cap,), dtype=bool)
        n = self._size
        if n:
            (self._states[:n], self._actions[:n], self._utilities[:n],
             self._costs[:n], self._next_states[:n], self._next_masks[:n],
             self._dones[:n]) = arrays
        self._cap = new_cap
        self._pos = n

    def append(self, state, action: int, utility: float, cost: float,
               next_state, next_mask, done: bool) -> None:
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
            self._dones[physical],
        )

    def to_arrays(self):
        order = self._physical_indices(np.arange(self._size, dtype=np.int64))
        return (
            self._states[order], self._actions[order],
            self._utilities[order], self._costs[order],
            self._next_states[order], self._next_masks[order],
            self._dones[order],
        )

    def restore(self, states, actions, utilities, costs, next_states,
                next_masks, dones) -> None:
        n = len(states)
        if n > self.maxlen:
            raise ValueError("replay payload exceeds configured capacity")
        expected_actions = (n,)
        expected_states = (n, INPUT_DIM)
        expected_masks = (n, ACTION_COUNT)
        if (actions.shape != expected_actions or utilities.shape != expected_actions
                or costs.shape != expected_actions
                or next_states.shape != expected_states
                or next_masks.shape != expected_masks
                or dones.shape != expected_actions):
            raise ValueError("replay payload shapes do not match the PRIMAL contract")
        self._cap = min(max(1, n, 64), self.maxlen)
        self._states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._actions = np.empty((self._cap,), dtype=np.int64)
        self._utilities = np.empty((self._cap,), dtype=np.float32)
        self._costs = np.empty((self._cap,), dtype=np.float32)
        self._next_states = np.empty((self._cap, INPUT_DIM), dtype=np.float32)
        self._next_masks = np.empty((self._cap, ACTION_COUNT), dtype=bool)
        self._dones = np.empty((self._cap,), dtype=bool)
        if n:
            self._states[:n] = states
            self._actions[:n] = actions
            self._utilities[:n] = utilities
            self._costs[:n] = costs
            self._next_states[:n] = next_states
            self._next_masks[:n] = next_masks
            self._dones[:n] = dones
        self._size = n
        self._pos = n % self._cap if self._size == self.maxlen else n


class NumpyPrimalCVaR:
    """Frozen PRIMAL policy with no TensorFlow import on the inference path."""

    inference_only = True

    def __init__(self, weights_path: str | Path, *, contract: str = "PRIMAL",
                 feature_order: str = PRIMAL_FEATURE_ORDER,
                 normalisation: dict | None = None, cvar_alpha: float = 0.75,
                 lambda_pd: float = 0.1, actions=ACTIONS,
                 checkpoint_path: str | None = None,
                 checkpoint_sha256: str | None = None,
                 numpy_checkpoint_sha256: str | None = None,
                 metadata_sha256: str | None = None,
                 weights_purpose: str = "trained"):
        self.weights_path = Path(weights_path)
        if self.weights_path.is_symlink() or not self.weights_path.is_file():
            raise ValueError(f"PRIMAL NumPy checkpoint not found: {self.weights_path}")
        self.contract = str(contract)
        self.feature_width = INPUT_DIM
        self.input_dim = INPUT_DIM
        self.feature_order = str(feature_order)
        self.normalisation = dict(normalisation or {"mode": "none"})
        self.actions = tuple(actions)
        if self.actions != tuple(ACTIONS):
            raise ValueError("PRIMAL NumPy checkpoint action order mismatch")
        self.cvar_alpha = float(cvar_alpha)
        self.lambda_pd = float(lambda_pd)
        if not 0.0 < self.cvar_alpha < 1.0:
            raise ValueError("PRIMAL NumPy checkpoint CVaR alpha is invalid")
        if not math.isfinite(self.lambda_pd) or self.lambda_pd < 0.0:
            raise ValueError("PRIMAL NumPy checkpoint dual multiplier is invalid")
        self.checkpoint_path = (None if checkpoint_path is None
                                else str(checkpoint_path))
        self.checkpoint_sha256 = checkpoint_sha256
        self.numpy_checkpoint_sha256 = numpy_checkpoint_sha256
        self.metadata_sha256 = metadata_sha256
        self.weights_purpose = str(weights_purpose)
        self.observation_contract_id = _observation_contract_id(
            self.feature_order, self.normalisation)
        self.epsilon = 0.0
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
            "satellite_w": (SATELLITE_INPUT_DIM, 64),
            "satellite_b": (64,),
            "packet_w": (PACKET_INPUT_DIM, 32),
            "packet_b": (32,),
            "trunk128_w": (96, 128),
            "trunk128_b": (128,),
            "trunk64_w": (128, 64),
            "trunk64_b": (64,),
            "utility_w": (64, ACTION_COUNT),
            "utility_b": (ACTION_COUNT,),
            "risk_w": (64, ACTION_COUNT * RISK_QUANTILES),
            "risk_b": (ACTION_COUNT * RISK_QUANTILES,),
        }
        for key, shape in expected.items():
            value = self.weights[key]
            if value.shape != shape:
                raise ValueError(
                    f"PRIMAL NumPy weight {key} shape {value.shape} != {shape}")
            if not np.all(np.isfinite(value)):
                raise ValueError(f"PRIMAL NumPy weight {key} is non-finite")

    def _weights_sha256(self) -> str:
        digest = hashlib.sha256()
        for key in NUMPY_WEIGHT_KEYS:
            value = np.asarray(self.weights[key], dtype="<f4")
            digest.update(f"{key}:{value.shape}".encode("ascii"))
            digest.update(value.tobytes())
        return digest.hexdigest()

    @staticmethod
    def _swish(value: np.ndarray) -> np.ndarray:
        return 0.5 * value * (1.0 + np.tanh(0.5 * value))

    def predict(self, states) -> tuple[np.ndarray, np.ndarray]:
        return self.predict_with_weights(self.weights, states)

    @staticmethod
    def predict_with_weights(weights, states) -> tuple[np.ndarray, np.ndarray]:
        array = np.asarray(states, dtype=np.float32)
        if array.ndim == 1:
            array = array[None, :]
        if array.ndim != 2 or array.shape[-1] != INPUT_DIM:
            raise ValueError(
                f"PRIMAL observations must end in {INPUT_DIM} features")
        if not np.all(np.isfinite(array)):
            raise ValueError("PRIMAL observations must be finite")
        w = weights
        satellite = NumpyPrimalCVaR._swish(
            array[:, :SATELLITE_INPUT_DIM] @ w["satellite_w"] + w["satellite_b"])
        packet = NumpyPrimalCVaR._swish(
            array[:, SATELLITE_INPUT_DIM:] @ w["packet_w"] + w["packet_b"])
        trunk = NumpyPrimalCVaR._swish(
            np.concatenate((satellite, packet), axis=1) @ w["trunk128_w"]
            + w["trunk128_b"])
        trunk = NumpyPrimalCVaR._swish(
            trunk @ w["trunk64_w"] + w["trunk64_b"])
        utility = trunk @ w["utility_w"] + w["utility_b"]
        risk_flat = trunk @ w["risk_w"] + w["risk_b"]
        risk = np.logaddexp(0.0, risk_flat).reshape(
            -1, ACTION_COUNT, RISK_QUANTILES)
        return utility, risk

    def choose(self, observation, mask: dict, now=None) -> str:
        legal = _mask_array(mask)
        if not np.any(legal):
            raise ValueError("PRIMAL decision requires at least one legal action")
        utility, risk = self.predict(observation)
        scores = risk_scores(
            utility[0], risk[0], self.lambda_pd, alpha=self.cvar_alpha)
        self._last_utility = utility[0].copy()
        self._last_cvar = cvar_tail(risk[0], alpha=self.cvar_alpha)
        self._last_scores = scores.copy()
        self._calls += 1
        legal_indices = np.flatnonzero(legal)
        return ACTIONS[int(legal_indices[np.argmax(scores[legal])])]

    def parameter_sha256(self) -> str:
        return self.parameter_sha256_value

    def counters(self) -> dict:
        return {
            "schema": "leo-sim-fixed-inference/v1",
            "name": "primal-cvar-numpy",
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
            "feature_width": self.feature_width,
            "feature_order": self.feature_order,
            "normalisation_mode": self.normalisation.get("mode"),
            "observation_contract_id": self.observation_contract_id,
            "weights_purpose": self.weights_purpose,
            "checkpoint_sha256": self.checkpoint_sha256,
            "numpy_checkpoint_sha256": self.numpy_checkpoint_sha256,
            "metadata_sha256": self.metadata_sha256,
            "parameter_sha256": self.parameter_sha256(),
            "last_utility": (None if self._last_utility is None
                             else self._last_utility.tolist()),
            "last_cvar": (None if self._last_cvar is None
                           else self._last_cvar.tolist()),
            "last_scores": (None if self._last_scores is None
                            else self._last_scores.tolist()),
        }

    def receipt(self) -> dict:
        return {
            "schema": "leo-sim-fixed-inference/v1",
            "state": "AVAILABLE",
            "reader": "CODE.leo_sim.rl.agents.primal_cvar.NumpyPrimalCVaR",
            "checkpoint_path": self.checkpoint_path,
            "checkpoint_sha256": self.checkpoint_sha256,
            "numpy_checkpoint_path": str(self.weights_path),
            "numpy_checkpoint_sha256": self.numpy_checkpoint_sha256,
            "metadata_sha256": self.metadata_sha256,
            "contract": self.contract,
            "feature_width": self.feature_width,
            "feature_order": self.feature_order,
            "normalisation": dict(self.normalisation),
            "action_order": list(self.actions),
            "observation_contract_id": self.observation_contract_id,
            "weights_purpose": self.weights_purpose,
            "parameter_sha256": self.parameter_sha256(),
            "shape_checked": True,
            "finite_probe_checked": True,
            "training": False,
            "epsilon": 0.0,
        }


@register_agent("primal_cvar", scope="shared")
class TensorflowPrimalCVaR:
    """PRIMAL-inspired shared-policy online learner on TensorFlow CPU."""

    def __init__(self, contract: str, cfg: dict, seed: int):
        if contract != "PRIMAL":
            raise ValueError("primal_cvar requires contract='PRIMAL'")
        if not isinstance(cfg, dict):
            raise TypeError("cfg must be a mapping")

        # Keep TensorFlow out of module import and even out of the base package
        # path until a learner is actually constructed.
        from ..models import _configure_tf_threads, require_tensorflow

        self.tf = require_tensorflow()
        _configure_tf_threads(self.tf)
        self._force_cpu_runtime()
        try:
            self.tf.config.experimental.enable_op_determinism()
        except Exception as exc:
            raise LearningUnavailable(
                "TensorFlow op determinism could not be enabled "
                f"({type(exc).__name__}: {exc})") from exc
        self.op_determinism = True
        self.tf.keras.utils.set_random_seed(int(seed))
        try:
            self.tf.random.set_global_generator(
                self.tf.random.Generator.from_seed(int(seed)))
        except Exception as exc:
            raise LearningUnavailable(
                "TensorFlow global RNG could not be initialized for exact "
                f"resume ({type(exc).__name__}: {exc})") from exc

        self.contract = contract
        self.input_dim = INPUT_DIM
        self.cfg = dict(cfg)
        self.seed = int(seed)
        self.mode = str(cfg.get("mode", "train"))
        if self.mode not in {"train", "eval"}:
            raise ValueError("learning mode must be 'train' or 'eval'")
        if str(cfg.get("agent_scope", "shared")) != "shared":
            raise ValueError("primal_cvar currently supports agent_scope='shared'")
        self.rng = np.random.default_rng(self.seed)
        self.replay = _PrimalReplay(int(cfg["replay_size"]))

        self.cvar_alpha = _resolve_cfg_float(
            cfg, ("cvar_alpha", "primal_alpha", "risk_alpha"), 0.75)
        self.lambda_max = _resolve_cfg_float(
            cfg, ("dual_max", "lambda_max", "primal_lambda_max"), 10.0)
        self.lambda_pd = _resolve_cfg_float(
            cfg, ("lambda_init", "lambda_pd_init", "primal_lambda_init"), 0.1)
        self.initial_lambda_pd = float(self.lambda_pd)
        self.dual_lr = _resolve_cfg_float(
            cfg, ("dual_lr", "dual_step", "lambda_lr"), 0.05)
        self.dual_constraint = _resolve_cfg_float(
            cfg, ("dual_constraint", "risk_budget", "training_constraint",
                  "timeout_budget"), 0.05)
        self.risk_loss_weight = _resolve_cfg_float(
            cfg, ("risk_loss_weight", "cost_loss_weight"), 1.0)
        self.quantile_huber_delta = _resolve_cfg_float(
            cfg, ("quantile_huber_delta",), 1.0)
        self._validate_dual_config()
        self.dual_update_on_done = bool(cfg.get("dual_update_on_done", False))

        self._fast_enabled = bool(cfg.get("fast_train", True))
        self._fast_train_fn = None
        self._fast_online_id = None
        self._fast_target_id = None
        self._last_scores = None
        self._last_utility = None
        self._last_cvar = None
        self._pending_terminal_costs: list[float] = []
        self.dual_updates = 0
        self.round_updates = 0
        self.last_training_constraint = None
        self.last_round_failure_rate = None

        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self.last_loss = None
        self.last_reward_loss = None
        self.last_risk_loss = None
        self._last_now = 0.0
        self._resume_time_offset_s = 0.0

        self.loaded_checkpoint = None
        self.loaded_checkpoint_sha256 = None
        self.loaded_checkpoint_metadata_sha256 = None
        self.loaded_resume_path = None
        self.loaded_resume_sha256 = None
        self.resume_manifest_sha256 = None
        self.resume_verified = False
        self.checkpoint_verified = False
        self.shape_checked = False
        self.finite_probe_checked = False
        self.feature_order = str(cfg.get("feature_order", PRIMAL_FEATURE_ORDER))
        self.normalisation = dict(cfg.get("normalisation", DEFAULT_NORMALISATION))
        self.observation_contract_id = _observation_contract_id(
            self.feature_order, self.normalisation)
        configured_contract_id = cfg.get("observation_contract_id")
        if configured_contract_id is not None \
                and str(configured_contract_id) != self.observation_contract_id:
            raise ValueError(
                "configured observation_contract_id disagrees with the PRIMAL "
                "feature order, width, actions, and normalisation")
        if (len(self.observation_contract_id) != 64
                or any(ch not in "0123456789abcdef"
                       for ch in self.observation_contract_id)):
            raise ValueError("observation_contract_id must be a lowercase SHA-256")

        if self.mode == "train" and self.cfg.get("checkpoint_path") is not None:
            raise LearningUnavailable(
                "primal_cvar training mode does not load an inference checkpoint")
        if self.mode == "eval" and self.cfg.get("resume_path") is not None:
            raise LearningUnavailable(
                "primal_cvar eval mode cannot load a training resume bundle")
        if self.mode == "eval" and not self.cfg.get("checkpoint_path"):
            raise LearningUnavailable(
                "primal_cvar eval mode requires checkpoint_path and checkpoint_sha256")

        with self.tf.device("/CPU:0"):
            self.online = self._network()
            self.target = self._network()
        self.target.set_weights(self.online.get_weights())
        options = {"learning_rate": float(cfg["lr"])}
        if cfg.get("clip_norm") is not None:
            options["clipnorm"] = float(cfg["clip_norm"])
        self.optimizer = self.tf.keras.optimizers.Adam(**options)

        checkpoint = cfg.get("checkpoint_path")
        if checkpoint:
            self._load_checkpoint(Path(checkpoint), cfg.get("checkpoint_sha256"))
        if cfg.get("resume_path") is not None:
            self._load_resume_bundle(
                Path(cfg["resume_path"]), cfg.get("resume_sha256"))
        self._numpy_weights = self._numpy_weight_arrays()

    def _force_cpu_runtime(self) -> None:
        self.cpu_only_verified = True
        try:
            gpus = list(self.tf.config.list_physical_devices("GPU"))
            if gpus:
                self.tf.config.set_visible_devices([], "GPU")
                gpus = list(self.tf.config.list_physical_devices("GPU"))
            self.cpu_only_verified = len(gpus) == 0
        except RuntimeError as exc:
            gpus = list(self.tf.config.list_physical_devices("GPU"))
            if gpus:
                raise LearningUnavailable(
                    "primal_cvar requires TensorFlow CPU, but the GPU runtime "
                    "was initialized before CPU visibility could be restricted") from exc
            self.cpu_only_verified = True

    def _validate_dual_config(self) -> None:
        if not 0.0 < self.cvar_alpha < 1.0:
            raise ValueError("cvar_alpha must be in (0, 1)")
        if self.lambda_max < 0.0:
            raise ValueError("dual maximum must be non-negative")
        if not 0.0 <= self.lambda_pd <= self.lambda_max:
            raise ValueError("initial dual multiplier must lie in [0, dual_max]")
        if self.dual_lr < 0.0:
            raise ValueError("dual_lr must be non-negative")
        if not 0.0 <= self.dual_constraint <= 1.0:
            raise ValueError("dual_constraint/risk_budget must lie in [0, 1]")
        if self.risk_loss_weight < 0.0:
            raise ValueError("risk_loss_weight must be non-negative")
        if self.quantile_huber_delta <= 0.0:
            raise ValueError("quantile_huber_delta must be positive")

    def _network(self):
        tf = self.tf
        satellite_in = tf.keras.layers.Input(
            shape=(SATELLITE_INPUT_DIM,), name="satellite_stream")
        packet_in = tf.keras.layers.Input(
            shape=(PACKET_INPUT_DIM,), name="packet_stream")
        satellite = tf.keras.layers.Dense(
            64, activation="swish", name="satellite_encoder")(satellite_in)
        packet = tf.keras.layers.Dense(
            32, activation="swish", name="packet_encoder")(packet_in)
        joined = tf.keras.layers.Concatenate(name="dual_stream_join")([satellite, packet])
        trunk = tf.keras.layers.Dense(
            128, activation="swish", name="shared_trunk_128")(joined)
        trunk = tf.keras.layers.Dense(
            64, activation="swish", name="shared_trunk_64")(trunk)
        utility = tf.keras.layers.Dense(
            ACTION_COUNT, activation="linear", name="utility_head")(trunk)
        risk_flat = tf.keras.layers.Dense(
            ACTION_COUNT * RISK_QUANTILES, activation="linear",
            name="risk_quantile_flat")(trunk)
        risk = tf.keras.layers.Reshape(
            (ACTION_COUNT, RISK_QUANTILES), name="risk_quantile_head")(risk_flat)
        risk = tf.keras.layers.Activation("softplus", name="nonnegative_cost")(risk)
        return tf.keras.Model(
            inputs=[satellite_in, packet_in], outputs=[utility, risk],
            name="primal_cvar")

    def _model_call(self, model, states, training: bool):
        satellite, packet = _split_observation_batch(states)
        return model([satellite, packet], training=training)

    def _predict_numpy(self, model, states) -> tuple[np.ndarray, np.ndarray]:
        utility, risk = self._model_call(model, states, training=False)
        return utility.numpy(), risk.numpy()

    def epsilon(self, now: float) -> float:
        if self.mode == "eval":
            return 0.0
        start = float(self.cfg["epsilon_start"])
        end = float(self.cfg["epsilon_end"])
        decay = float(self.cfg["epsilon_decay_s"])
        elapsed = self._resume_time_offset_s + max(0.0, float(now))
        return end + (start - end) * math.exp(-elapsed / decay)

    def _advance_learning_clock(self, now: float) -> None:
        current = float(now)
        if not math.isfinite(current) or current < 0.0:
            raise ValueError("learning clock must be finite and non-negative")
        if current < self._last_now:
            # Kernel rounds restart their local clock at zero.  Folding the
            # previous round into the offset preserves cumulative decay while
            # also preserving exact continuation when the caller keeps the
            # same monotonic clock.
            self._resume_time_offset_s += self._last_now
        self._last_now = current

    def _learning_clock(self) -> float:
        return float(self._resume_time_offset_s + self._last_now)

    def choose(self, observation: np.ndarray, mask: dict, now: float) -> str:
        split_observation(observation)
        legal = _mask_array(mask)
        legal_indices = np.flatnonzero(legal)
        if not len(legal_indices):
            raise ValueError("PRIMAL decision requires at least one legal action")
        self._advance_learning_clock(now)
        if self.rng.random() < self.epsilon(now):
            chosen = int(self.rng.choice(legal_indices))
        elif len(legal_indices) == 1:
            chosen = int(legal_indices[0])
        else:
            if getattr(self, "_numpy_weights", None) is not None:
                utility, risk = NumpyPrimalCVaR.predict_with_weights(
                    self._numpy_weights,
                    np.asarray(observation, dtype=np.float32))
            else:
                utility, risk = self._predict_numpy(
                    self.online, np.asarray(observation, dtype=np.float32))
            scores = risk_scores(
                utility[0], risk[0], self.lambda_pd, alpha=self.cvar_alpha)
            self._last_utility = utility[0].copy()
            self._last_cvar = cvar_tail(risk[0], alpha=self.cvar_alpha)
            self._last_scores = scores.copy()
            chosen = int(legal_indices[np.argmax(scores[legal])])
        self.decisions += 1
        return ACTIONS[chosen]

    def remember(self, state, action: str, reward: float, next_state,
                 next_mask: dict, done: bool) -> None:
        """Store a transition whose immediate risk cost is exactly zero."""
        self._remember(
            state, action, reward, next_state, next_mask, done, cost=0.0)

    def remember_with_cost(self, state, action: str, reward: float, next_state,
                           next_mask: dict, done: bool, *, cost: float) -> None:
        """Store a transition with an independently supplied immediate cost.

        ``cost`` is the current kernel binary convention: ``1.0`` iff
        ``intermediate`` is false and the packet terminates as a failure or
        non-delivery, otherwise ``0.0``.  A nonzero cost is accepted only on a
        terminal transition.  The
        Bellman risk target discounts that event back through ``gamma`` along
        the observed trajectory.  Queue utility and other routing rewards are
        never reused as this cost channel.
        """
        self._remember(
            state, action, reward, next_state, next_mask, done, cost=cost)

    def _remember(self, state, action: str, reward: float, next_state,
                  next_mask: dict, done: bool, *, cost: float) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown PRIMAL action {action!r}")
        split_observation(state)
        split_observation(next_state)
        utility = float(reward)
        cost = float(cost)
        if not math.isfinite(utility):
            raise ValueError("transition utility must be finite")
        if cost not in (0.0, 1.0):
            raise ValueError("transition cost must be exactly 0.0 or 1.0")
        if cost == 1.0 and not done:
            raise ValueError("a terminal failure cost requires done=True")
        self.transitions += 1
        if self.mode == "eval":
            return
        self.replay.append(
            state, ACTIONS.index(action), utility, cost, next_state,
            _mask_array(next_mask), bool(done),
        )

        train_after = max(
            int(self.cfg["batch_size"]), int(self.cfg.get("warmup_steps", 0)))
        every = max(1, int(self.cfg.get("train_every_steps", 1)))
        if (self.mode == "train" and len(self.replay) >= train_after
                and (self.transitions - train_after) % every == 0):
            self._train_once()

        if done and self.mode == "train":
            self._pending_terminal_costs.append(cost)
            if self.dual_update_on_done:
                self.update_dual_boundary()

    def update_dual_boundary(self, observed_cost_rate: float | None = None) -> float:
        """Project the CVaR dual multiplier once at an episode/round boundary.

        With no explicit rate, the mean of pending terminal costs is used.  The
        constraint target comes only from training configuration and observed
        training costs, never from validation outcomes.  Eval mode returns the
        frozen multiplier unchanged.
        """
        if self.mode == "eval":
            return float(self.lambda_pd)
        if observed_cost_rate is None:
            if not self._pending_terminal_costs:
                return float(self.lambda_pd)
            observed = float(np.mean(self._pending_terminal_costs, dtype=np.float64))
        else:
            observed = float(observed_cost_rate)
            if not math.isfinite(observed) or observed < 0.0:
                raise ValueError("observed cost rate must be finite and non-negative")
        updated = project_dual(
            self.lambda_pd, observed, self.dual_constraint, self.dual_lr,
            self.lambda_max)
        self._pending_terminal_costs.clear()
        self.lambda_pd = updated
        self.last_training_constraint = observed
        self.dual_updates += 1
        return float(self.lambda_pd)

    def finish_round(self, failure_rate: float) -> float:
        """Apply exactly one dual update after the full offered cohort settles.

        ``failure_rate`` must be computed by the kernel over every offered
        packet in the round, including packets that never received a valid
        network action.  Eval mode performs no update.
        """
        if self.mode == "eval":
            return float(self.lambda_pd)
        rate = float(failure_rate)
        if not math.isfinite(rate) or not 0.0 <= rate <= 1.0:
            raise ValueError("round failure_rate must be finite and in [0, 1]")
        self.update_dual_boundary(rate)
        self.round_updates += 1
        self.last_round_failure_rate = rate
        return float(self.lambda_pd)

    def _tensor_specs(self):
        tf = self.tf
        batch = int(self.cfg["batch_size"])
        return [
            tf.TensorSpec([], tf.float32),
            tf.TensorSpec([batch, SATELLITE_INPUT_DIM], tf.float32),
            tf.TensorSpec([batch, PACKET_INPUT_DIM], tf.float32),
            tf.TensorSpec([batch], tf.int64),
            tf.TensorSpec([batch], tf.float32),
            tf.TensorSpec([batch], tf.float32),
            tf.TensorSpec([batch, SATELLITE_INPUT_DIM], tf.float32),
            tf.TensorSpec([batch, PACKET_INPUT_DIM], tf.float32),
            tf.TensorSpec([batch], tf.bool),
            tf.TensorSpec([batch, ACTION_COUNT], tf.bool),
        ]

    def _build_fast_train_fn(self):
        tf = self.tf
        online, target, optimizer = self.online, self.target, self.optimizer
        tau = tf.constant(
            (np.arange(RISK_QUANTILES, dtype=np.float64) + 0.5)
            / RISK_QUANTILES, dtype=tf.float32)
        alpha = float(self.cvar_alpha)
        gamma = float(self.cfg["gamma"])
        risk_weight = float(self.risk_loss_weight)
        huber_delta = float(self.quantile_huber_delta)
        tail_count = max(1, int(math.ceil((1.0 - alpha) * RISK_QUANTILES - 1e-12)))
        specs = self._tensor_specs()

        @tf.function(input_signature=specs, reduce_retracing=True)
        def _step(multiplier, satellite, packet, actions, utilities, costs,
                  next_satellite, next_packet, not_done, next_mask):
            online_next_utility, online_next_risk = online(
                [next_satellite, next_packet], training=False)
            target_next_utility, target_next_risk = target(
                [next_satellite, next_packet], training=False)
            safe_mask = tf.where(
                tf.reduce_any(next_mask, axis=1, keepdims=True),
                next_mask, tf.ones_like(next_mask))
            sorted_online_risk = tf.sort(
                online_next_risk, axis=-1, direction="DESCENDING")
            online_cvar = tf.reduce_mean(
                sorted_online_risk[..., :tail_count], axis=-1)
            masked_scores = tf.where(
                safe_mask,
                online_next_utility - multiplier * online_cvar,
                tf.cast(-1e9, tf.float32),
            )
            next_actions = tf.argmax(masked_scores, axis=1, output_type=tf.int32)
            reward_bootstrap = tf.gather(
                target_next_utility, next_actions, batch_dims=1)
            risk_bootstrap = tf.gather(
                target_next_risk, next_actions, batch_dims=1)
            reward_target = tf.stop_gradient(
                utilities
                + gamma * tf.cast(not_done, tf.float32) * reward_bootstrap)
            risk_target = tf.stop_gradient(tf.clip_by_value(
                costs[:, None]
                + gamma * tf.cast(not_done[:, None], tf.float32) * risk_bootstrap,
                0.0,
                1.0,
            ))

            with tf.GradientTape() as tape:
                utility_pred, risk_pred = online(
                    [satellite, packet], training=True)
                chosen_utility = tf.gather(utility_pred, actions, batch_dims=1)
                chosen_risk = tf.gather(risk_pred, actions, batch_dims=1)
                reward_error = reward_target - chosen_utility
                abs_reward_error = tf.abs(reward_error)
                reward_huber = tf.where(
                    abs_reward_error <= huber_delta,
                    0.5 * tf.square(reward_error),
                    huber_delta * (abs_reward_error - 0.5 * huber_delta),
                )
                reward_loss = tf.reduce_mean(reward_huber)

                risk_error = risk_target - chosen_risk
                abs_risk_error = tf.abs(risk_error)
                risk_huber = tf.where(
                    abs_risk_error <= huber_delta,
                    0.5 * tf.square(risk_error),
                    huber_delta * (abs_risk_error - 0.5 * huber_delta),
                )
                quantile_weight = tf.abs(
                    tau[None, :] - tf.cast(risk_error < 0.0, tf.float32))
                risk_loss = tf.reduce_mean(risk_huber * quantile_weight)
                total_loss = reward_loss + risk_weight * risk_loss

            gradients = tape.gradient(total_loss, online.trainable_variables)
            optimizer.apply_gradients(
                (grad, variable)
                for grad, variable in zip(gradients, online.trainable_variables)
                if grad is not None)
            return reward_loss, risk_loss, total_loss

        return _step.get_concrete_function(*specs)

    def _train_on_batch(self, batch) -> tuple[float, float, float]:
        if self.mode != "train":
            raise LearningUnavailable("eval mode must not perform a training step")
        if not self._fast_enabled:
            return self._train_on_batch_eager(batch)
        fn = self._fast_train_fn
        if (fn is None or self._fast_online_id != id(self.online)
                or self._fast_target_id != id(self.target)):
            fn = self._build_fast_train_fn()
            self._fast_train_fn = fn
            self._fast_online_id = id(self.online)
            self._fast_target_id = id(self.target)
        states, actions, utilities, costs, next_states, masks, dones = batch
        state_sat, state_pkt = _split_observation_batch(states)
        next_sat, next_pkt = _split_observation_batch(next_states)
        reward_loss, risk_loss, total_loss = fn(
            self.tf.constant(self.lambda_pd, dtype=self.tf.float32),
            self.tf.constant(state_sat), self.tf.constant(state_pkt),
            self.tf.constant(actions, dtype=self.tf.int64),
            self.tf.constant(utilities), self.tf.constant(costs),
            self.tf.constant(next_sat), self.tf.constant(next_pkt),
            self.tf.constant(~dones, dtype=self.tf.bool),
            self.tf.constant(masks, dtype=self.tf.bool),
        )
        return (float(reward_loss.numpy()), float(risk_loss.numpy()),
                float(total_loss.numpy()))

    def _train_on_batch_eager(self, batch) -> tuple[float, float, float]:
        tf = self.tf
        states, actions, utilities, costs, next_states, masks, dones = batch
        batch_size = len(states)
        state_sat, state_pkt = _split_observation_batch(states)
        next_sat, next_pkt = _split_observation_batch(next_states)
        online_next_utility, online_next_risk = self.online(
            [next_sat, next_pkt], training=False)
        target_next_utility, target_next_risk = self.target(
            [next_sat, next_pkt], training=False)
        online_utility = online_next_utility.numpy()
        online_risk = online_next_risk.numpy()
        target_utility = target_next_utility.numpy()
        target_risk = target_next_risk.numpy()
        safe_mask = masks.copy()
        no_legal = ~np.any(safe_mask, axis=1)
        safe_mask[no_legal] = True
        online_cvar = cvar_tail(online_risk, alpha=self.cvar_alpha)
        scores = online_utility - self.lambda_pd * online_cvar
        scores[~safe_mask] = -1e9
        next_actions = np.argmax(scores, axis=1)
        rows = np.arange(batch_size)
        reward_bootstrap = target_utility[rows, next_actions]
        risk_bootstrap = target_risk[rows, next_actions]
        reward_target = utilities + float(self.cfg["gamma"]) * (
            ~dones) * reward_bootstrap
        risk_target = clip_risk_target(
            costs[:, None] + float(self.cfg["gamma"]) * (
                ~dones)[:, None] * risk_bootstrap)

        tau = (np.arange(RISK_QUANTILES, dtype=np.float32) + 0.5) / RISK_QUANTILES
        delta = self.quantile_huber_delta
        with tf.GradientTape() as tape:
            utility_pred, risk_pred = self.online(
                [state_sat, state_pkt], training=True)
            chosen_utility = tf.gather(utility_pred, actions, batch_dims=1)
            chosen_risk = tf.gather(risk_pred, actions, batch_dims=1)
            reward_error = tf.stop_gradient(
                tf.constant(reward_target)) - chosen_utility
            reward_abs = tf.abs(reward_error)
            reward_huber = tf.where(
                reward_abs <= delta, 0.5 * tf.square(reward_error),
                delta * (reward_abs - 0.5 * delta))
            reward_loss = tf.reduce_mean(reward_huber)

            risk_error = tf.stop_gradient(
                tf.constant(risk_target)) - chosen_risk
            risk_abs = tf.abs(risk_error)
            risk_huber = tf.where(
                risk_abs <= delta, 0.5 * tf.square(risk_error),
                delta * (risk_abs - 0.5 * delta))
            quantile_weight = tf.abs(
                tf.constant(tau)[None, :]
                - tf.cast(risk_error < 0.0, tf.float32))
            risk_loss = tf.reduce_mean(risk_huber * quantile_weight)
            total_loss = reward_loss + self.risk_loss_weight * risk_loss

        gradients = tape.gradient(total_loss, self.online.trainable_variables)
        self.optimizer.apply_gradients(
            (grad, variable)
            for grad, variable in zip(gradients, self.online.trainable_variables)
            if grad is not None)
        return (float(reward_loss.numpy()), float(risk_loss.numpy()),
                float(total_loss.numpy()))

    def _train_once(self) -> None:
        if self.mode != "train":
            raise LearningUnavailable("eval mode must not perform a training step")
        batch_size = int(self.cfg["batch_size"])
        indices = self.rng.choice(len(self.replay), size=batch_size, replace=False)
        batch = self.replay.sample_batch(indices)
        self.last_reward_loss, self.last_risk_loss, self.last_loss = (
            self._train_on_batch(batch))
        self._numpy_weights = self._numpy_weight_arrays()
        self.train_steps += 1
        if self.train_steps % int(self.cfg["target_update_interval"]) == 0:
            self.target.set_weights(self.online.get_weights())

    def _resume_identity(self) -> dict:
        from ..persistence import _resume_identity

        identity = _resume_identity(self.cfg, self.contract, "primal_cvar", self.seed)
        identity.update({
            "mode": self.mode,
            "cvar_alpha": float(self.cvar_alpha),
            "dual_max": float(self.lambda_max),
            "lambda_init": float(self.initial_lambda_pd),
            "dual_lr": float(self.dual_lr),
            "dual_constraint": float(self.dual_constraint),
            "dual_update_on_done": bool(self.dual_update_on_done),
            "risk_loss_weight": float(self.risk_loss_weight),
            "quantile_huber_delta": float(self.quantile_huber_delta),
            "train_every_steps": int(self.cfg.get("train_every_steps", 1)),
            "warmup_steps": int(self.cfg.get("warmup_steps", 0)),
            "input_dim": int(self.input_dim),
            "observation_contract_id": self.observation_contract_id,
            "feature_order": self.feature_order,
            "normalisation": self.normalisation,
        })
        return identity

    def _checkpoint(self):
        return self.tf.train.Checkpoint(
            online=self.online, target=self.target, optimizer=self.optimizer)

    def _write_checkpoint(self, prefix: Path) -> None:
        self.optimizer.build(self.online.trainable_variables)
        self._checkpoint().write(str(prefix))

    def _save_resume_and_verify(self, out: Path) -> dict:
        if out.is_symlink():
            raise LearningUnavailable("resume output may not be symbolic")
        out.mkdir(parents=True, exist_ok=True)
        if any(out.iterdir()):
            raise LearningUnavailable("resume output directory must be empty")
        try:
            replay_path = out / "replay.npz"
            arrays = self.replay.to_arrays()
            np.savez_compressed(
                replay_path, states=arrays[0], actions=arrays[1],
                utilities=arrays[2], costs=arrays[3], next_states=arrays[4],
                next_masks=arrays[5], dones=arrays[6])
            state = {
                "schema": RESUME_SCHEMA,
                "numpy_rng_state": _resume_json(self.rng.bit_generator.state),
                "tensorflow_rng_state": _resume_json(
                    self.tf.random.get_global_generator().state.numpy()),
                "time_offset_s": float(self._resume_time_offset_s),
                "last_now_s": float(self._last_now),
                "elapsed_time_s": self._learning_clock(),
                "counters": {
                    "decisions": int(self.decisions),
                    "transitions": int(self.transitions),
                    "train_steps": int(self.train_steps),
                },
                "lambda_pd": float(self.lambda_pd),
                "pending_terminal_costs": list(self._pending_terminal_costs),
                "dual_updates": int(self.dual_updates),
                "round_updates": int(self.round_updates),
                "last_training_constraint": self.last_training_constraint,
                "last_round_failure_rate": self.last_round_failure_rate,
                "last_loss": self.last_loss,
                "last_reward_loss": self.last_reward_loss,
                "last_risk_loss": self.last_risk_loss,
            }
            (out / "state.json").write_text(
                json.dumps(state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8")
            self._write_checkpoint(out / "tf_ckpt")
            sha = _write_resume_manifest(out, RESUME_SCHEMA, self._resume_identity())
            verified = _verify_resume_bundle(out, sha, RESUME_SCHEMA)
        except Exception as exc:
            if isinstance(exc, LearningUnavailable):
                raise
            raise LearningUnavailable(
                f"PRIMAL-inspired resume save failed: {exc}") from exc
        self.resume_manifest_sha256 = sha
        self.resume_verified = bool(verified)
        return {
            "resume_path": str(out),
            "resume_sha256": sha,
            "resume_verified": bool(verified),
        }

    def _load_resume_bundle(self, root: Path, expected_sha: str) -> None:
        if not isinstance(expected_sha, str):
            raise LearningUnavailable("resume_sha256 is required for exact resume")
        manifest = _verify_resume_bundle(root, expected_sha, RESUME_SCHEMA)
        _require_resume_identity(manifest, self._resume_identity())
        files = manifest["files"]
        required = {"state.json", "replay.npz", "tf_ckpt.index"}
        if not required.issubset(files):
            raise LearningUnavailable("PRIMAL-inspired resume bundle is incomplete")
        state = _read_json_bytes(
            (root / "state.json").read_bytes(), "PRIMAL resume state")
        if state.get("schema") != RESUME_SCHEMA:
            raise LearningUnavailable("PRIMAL resume state schema mismatch")
        try:
            replay = np.load(root / "replay.npz", allow_pickle=False)
            states = np.asarray(replay["states"], dtype=np.float32)
            actions = np.asarray(replay["actions"], dtype=np.int64)
            utilities = np.asarray(replay["utilities"], dtype=np.float32)
            costs = np.asarray(replay["costs"], dtype=np.float32)
            next_states = np.asarray(replay["next_states"], dtype=np.float32)
            masks = np.asarray(replay["next_masks"], dtype=bool)
            dones = np.asarray(replay["dones"], dtype=bool)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise LearningUnavailable(
                f"PRIMAL replay state is unreadable: {exc}") from exc
        finally:
            if "replay" in locals():
                replay.close()
        if (states.ndim != 2 or states.shape[1] != INPUT_DIM
                or next_states.shape != states.shape
                or masks.shape != (len(states), ACTION_COUNT)
                or not np.all(np.isfinite(states))
                or not np.all(np.isfinite(next_states))
                or not np.all(np.isfinite(utilities))
                or not np.all(np.isfinite(costs))
                or np.any(~np.isin(costs, (0.0, 1.0)))
                or np.any((costs == 1.0) & (~dones))
                or np.any(actions < 0) or np.any(actions >= ACTION_COUNT)
                or dones.shape != (len(states),)):
            raise LearningUnavailable("PRIMAL replay state shape or finiteness mismatch")
        try:
            self.replay.restore(
                states, actions, utilities, costs, next_states, masks, dones)
            self.optimizer.build(self.online.trainable_variables)
            status = self._checkpoint().read(str(root / "tf_ckpt"))
            status.assert_existing_objects_matched()
            try:
                status.assert_consumed()
            except AssertionError as exc:
                message = str(exc)
                if ("Found 1 Python objects" not in message
                        or "save_counter" not in message
                        or "not assigned a value" in message):
                    raise
            self.rng.bit_generator.state = _resume_unjson(
                state["numpy_rng_state"])
            tf_rng_state = self.tf.constant(
                _resume_unjson(state["tensorflow_rng_state"]), dtype=self.tf.int64)
            self.tf.random.get_global_generator().state.assign(tf_rng_state)
            counters = state["counters"]
            self.decisions = int(counters["decisions"])
            self.transitions = int(counters["transitions"])
            self.train_steps = int(counters["train_steps"])
            if "time_offset_s" in state and "last_now_s" in state:
                self._resume_time_offset_s = float(state["time_offset_s"])
                self._last_now = float(state["last_now_s"])
            else:
                self._resume_time_offset_s = float(state["elapsed_time_s"])
                self._last_now = 0.0
            self.lambda_pd = float(state["lambda_pd"])
            self._pending_terminal_costs = [
                float(value) for value in state.get("pending_terminal_costs", [])]
            self.dual_updates = int(state.get("dual_updates", 0))
            self.round_updates = int(state.get("round_updates", 0))
            self.last_training_constraint = state.get("last_training_constraint")
            self.last_round_failure_rate = state.get("last_round_failure_rate")
            self.last_loss = state.get("last_loss")
            self.last_reward_loss = state.get("last_reward_loss")
            self.last_risk_loss = state.get("last_risk_loss")
        except Exception as exc:
            raise LearningUnavailable(
                f"PRIMAL exact resume restore failed: {exc}") from exc
        if min(self.decisions, self.transitions, self.train_steps,
               self.dual_updates, self.round_updates) < 0 \
                or not math.isfinite(self._resume_time_offset_s) \
                or self._resume_time_offset_s < 0.0 \
                or not math.isfinite(self._last_now) \
                or self._last_now < 0.0:
            raise LearningUnavailable(
                "resumed counters or learning clock are invalid")
        if not 0.0 <= self.lambda_pd <= self.lambda_max:
            raise LearningUnavailable("resumed dual multiplier is outside [0, dual_max]")
        if any(not math.isfinite(value) or value not in (0.0, 1.0)
               for value in self._pending_terminal_costs):
            raise LearningUnavailable("resumed pending cost state is invalid")
        if self.last_round_failure_rate is not None and (
                not math.isfinite(float(self.last_round_failure_rate))
                or not 0.0 <= float(self.last_round_failure_rate) <= 1.0):
            raise LearningUnavailable("resumed round failure rate is invalid")
        self.loaded_resume_path = str(root.resolve())
        self.loaded_resume_sha256 = expected_sha
        self.resume_manifest_sha256 = expected_sha
        self.resume_verified = True

    def _verify_model_shapes(self, model) -> None:
        inputs = list(model.inputs)
        outputs = list(model.outputs)
        if len(inputs) != 2 or len(outputs) != 2:
            raise LearningUnavailable(
                "PRIMAL checkpoint must have two input streams and two heads")
        expected_inputs = [
            (SATELLITE_INPUT_DIM,), (PACKET_INPUT_DIM,)]
        expected_outputs = [
            (ACTION_COUNT,), (ACTION_COUNT, RISK_QUANTILES)]
        for tensor, expected in zip(inputs, expected_inputs):
            if tuple(tensor.shape[1:]) != expected:
                raise LearningUnavailable(
                    f"PRIMAL checkpoint input shape {tuple(tensor.shape[1:])} "
                    f"!= {expected}")
        for tensor, expected in zip(outputs, expected_outputs):
            if tuple(tensor.shape[1:]) != expected:
                raise LearningUnavailable(
                    f"PRIMAL checkpoint output shape {tuple(tensor.shape[1:])} "
                    f"!= {expected}")
        self.shape_checked = True

    def _load_checkpoint(self, path: Path, expected_sha: str) -> None:
        if path.is_symlink() or not path.is_file():
            raise LearningUnavailable(f"PRIMAL checkpoint not found: {path}")
        actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha != expected_sha:
            raise LearningUnavailable(
                "PRIMAL checkpoint SHA-256 differs from resolved config")
        meta_path = path.parent / "metadata.json"
        meta, meta_sha = _read_checkpoint_metadata(
            meta_path, self.cfg.get("checkpoint_metadata_sha256"),
            "PRIMAL checkpoint metadata.json")
        _verify_checkpoint_metadata(
            meta, self.contract, path.name, actual_sha,
            CHECKPOINT_SCHEMA, "primal_cvar")
        if meta.get("input_dim") != INPUT_DIM or meta.get("observation_layout") != OBSERVATION_LAYOUT:
            raise LearningUnavailable("PRIMAL checkpoint observation contract mismatch")
        if meta.get("feature_width") != INPUT_DIM \
                or list(meta.get("action_order", [])) != list(ACTIONS):
            raise LearningUnavailable("PRIMAL checkpoint feature/action order mismatch")
        declared_normalisation = meta.get("normalisation")
        if not isinstance(declared_normalisation, dict):
            raise LearningUnavailable("PRIMAL checkpoint normalisation is missing")
        declared_contract_id = meta.get("observation_contract_id")
        recomputed_contract_id = _observation_contract_id(
            str(meta.get("feature_order", "")), declared_normalisation)
        if declared_contract_id != recomputed_contract_id \
                or declared_contract_id != self.observation_contract_id:
            raise LearningUnavailable("PRIMAL checkpoint observation contract mismatch")
        if int(meta.get("risk_quantiles", -1)) != RISK_QUANTILES:
            raise LearningUnavailable("PRIMAL checkpoint risk quantile count mismatch")
        if not math.isclose(float(meta.get("cvar_alpha", -1.0)),
                            self.cvar_alpha, rel_tol=0.0, abs_tol=1e-12):
            raise LearningUnavailable("PRIMAL checkpoint CVaR alpha mismatch")
        try:
            loaded = self.tf.keras.models.load_model(path, compile=False)
        except Exception as exc:
            raise LearningUnavailable(
                f"PRIMAL checkpoint could not be loaded: {exc}") from exc
        self._verify_model_shapes(loaded)
        probe_sat = np.linspace(-0.5, 0.5, SATELLITE_INPUT_DIM, dtype=np.float32)
        probe_pkt = np.linspace(-0.5, 0.5, PACKET_INPUT_DIM, dtype=np.float32)
        utility, risk = loaded([probe_sat[None, :], probe_pkt[None, :]], training=False)
        finite = bool(np.all(np.isfinite(utility.numpy()))
                      and np.all(np.isfinite(risk.numpy())))
        if not finite:
            raise LearningUnavailable("PRIMAL checkpoint produced a non-finite probe")
        self.finite_probe_checked = True
        if not 0.0 <= float(meta.get("lambda_pd", -1.0)) <= self.lambda_max:
            raise LearningUnavailable("PRIMAL checkpoint dual multiplier is invalid")
        self.online = loaded
        self.target.set_weights(self.online.get_weights())
        self.lambda_pd = float(meta["lambda_pd"])
        expected_parameter_sha = meta.get("parameter_sha256")
        if (not isinstance(expected_parameter_sha, str)
                or self._parameter_sha256(loaded) != expected_parameter_sha):
            raise LearningUnavailable("PRIMAL checkpoint parameter hash mismatch")
        self.loaded_checkpoint = str(path.resolve())
        self.loaded_checkpoint_sha256 = actual_sha
        self.loaded_checkpoint_metadata_sha256 = meta_sha
        self.checkpoint_verified = True

    def _parameter_sha256(self, model=None) -> str:
        target = self.online if model is None else model
        digest = hashlib.sha256()
        for index, variable in enumerate(target.trainable_variables):
            digest.update(f"{index}:{tuple(variable.shape)}".encode("ascii"))
            digest.update(np.asarray(variable.numpy(), dtype="<f4").tobytes())
        return digest.hexdigest()

    def _numpy_weight_arrays(self, model=None) -> dict[str, np.ndarray]:
        source = self.online if model is None else model
        layer_keys = {
            "satellite_encoder": "satellite",
            "packet_encoder": "packet",
            "shared_trunk_128": "trunk128",
            "shared_trunk_64": "trunk64",
            "utility_head": "utility",
            "risk_quantile_flat": "risk",
        }
        arrays = {}
        for layer_name, prefix in layer_keys.items():
            weights, biases = source.get_layer(layer_name).get_weights()
            arrays[prefix + "_w"] = np.asarray(weights, dtype="<f4")
            arrays[prefix + "_b"] = np.asarray(biases, dtype="<f4")
        return arrays

    def _save_numpy_and_verify(self, out: Path):
        weights_path = out / NATIVE_NUMPY_CHECKPOINT_FILENAME
        np.savez_compressed(weights_path, **self._numpy_weight_arrays())
        weights_sha = hashlib.sha256(weights_path.read_bytes()).hexdigest()
        policy = NumpyPrimalCVaR(
            weights_path,
            contract=self.contract,
            feature_order=self.feature_order,
            normalisation=self.normalisation,
            cvar_alpha=self.cvar_alpha,
            lambda_pd=self.lambda_pd,
            checkpoint_path=str(out / NATIVE_CHECKPOINT_FILENAME),
            checkpoint_sha256=hashlib.sha256(
                (out / NATIVE_CHECKPOINT_FILENAME).read_bytes()).hexdigest(),
            numpy_checkpoint_sha256=weights_sha,
            weights_purpose=str(self.cfg.get("weights_purpose", "trained")),
        )
        probe = np.concatenate((
            np.linspace(-0.5, 0.5, SATELLITE_INPUT_DIM, dtype=np.float32),
            np.linspace(-0.5, 0.5, PACKET_INPUT_DIM, dtype=np.float32),
        ))
        numpy_utility, numpy_risk = policy.predict(probe)
        tf_utility, tf_risk = self.online(
            [probe[None, :SATELLITE_INPUT_DIM],
             probe[None, SATELLITE_INPUT_DIM:]], training=False)
        max_error = max(
            float(np.max(np.abs(numpy_utility - tf_utility.numpy()))),
            float(np.max(np.abs(numpy_risk - tf_risk.numpy()))),
        )
        verified = bool(
            np.all(np.isfinite(numpy_utility))
            and np.all(np.isfinite(numpy_risk))
            and np.allclose(numpy_utility, tf_utility.numpy(),
                            rtol=1e-5, atol=1e-6)
            and np.allclose(numpy_risk, tf_risk.numpy(),
                            rtol=1e-5, atol=1e-6))
        return weights_path, weights_sha, policy, verified, max_error

    def save_and_verify(self, directory: str | Path) -> dict:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        model_path = out / NATIVE_CHECKPOINT_FILENAME
        tf_rng_state = self.tf.random.get_global_generator().state.numpy().copy()
        resume = self._save_resume_and_verify(out / "resume")
        self.online.save(model_path)
        checkpoint_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
        loaded = self.tf.keras.models.load_model(model_path, compile=False)
        self._verify_model_shapes(loaded)
        probe_sat = np.linspace(-0.5, 0.5, SATELLITE_INPUT_DIM, dtype=np.float32)
        probe_pkt = np.linspace(-0.5, 0.5, PACKET_INPUT_DIM, dtype=np.float32)
        before_utility, before_risk = self.online(
            [probe_sat[None, :], probe_pkt[None, :]], training=False)
        after_utility, after_risk = loaded(
            [probe_sat[None, :], probe_pkt[None, :]], training=False)
        verified = bool(
            np.allclose(before_utility.numpy(), after_utility.numpy(),
                        rtol=0.0, atol=1e-7)
            and np.allclose(before_risk.numpy(), after_risk.numpy(),
                            rtol=0.0, atol=1e-7))
        self.finite_probe_checked = bool(
            np.all(np.isfinite(before_utility.numpy()))
            and np.all(np.isfinite(before_risk.numpy()))
            and np.all(np.isfinite(after_utility.numpy()))
            and np.all(np.isfinite(after_risk.numpy())))
        max_error = max(
            float(np.max(np.abs(before_utility.numpy() - after_utility.numpy()))),
            float(np.max(np.abs(before_risk.numpy() - after_risk.numpy()))))
        numpy_path, numpy_sha, numpy_policy, numpy_verified, numpy_max_error = (
            self._save_numpy_and_verify(out))
        self.tf.random.get_global_generator().state.assign(
            self.tf.constant(tf_rng_state, dtype=self.tf.int64))
        metadata = self.diagnostics()
        metadata.update({
            "schema": CHECKPOINT_SCHEMA,
            "checkpoint": model_path.name,
            "checkpoint_sha256": checkpoint_sha,
            "checkpoint_verified": verified and numpy_verified,
            "numpy_checkpoint": numpy_path.name,
            "numpy_checkpoint_sha256": numpy_sha,
            "numpy_verified": numpy_verified,
            "numpy_parameter_sha256": numpy_policy.parameter_sha256(),
            "numpy_probe_max_abs_error": numpy_max_error,
            "probe_max_abs_error": max_error,
            "weights_purpose": str(self.cfg.get("weights_purpose", "trained")),
            "risk_quantiles": RISK_QUANTILES,
            "cvar_alpha": float(self.cvar_alpha),
            "dual_max": float(self.lambda_max),
            "lambda_pd": float(self.lambda_pd),
            "dual_constraint": float(self.dual_constraint),
            "observation_layout": OBSERVATION_LAYOUT,
            "feature_width": INPUT_DIM,
            "feature_order": self.feature_order,
            "normalisation": self.normalisation,
            "action_order": list(ACTIONS),
            "observation_contract_id": self.observation_contract_id,
            "parameter_sha256": self._parameter_sha256(),
            **resume,
        })
        (out / NATIVE_METADATA_FILENAME).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        if not (verified and numpy_verified):
            raise LearningUnavailable(
                "saved PRIMAL-inspired TensorFlow or NumPy checkpoint failed "
                "load/prediction verification")
        self.checkpoint_verified = True
        result = dict(metadata)
        result["metadata_sha256"] = hashlib.sha256(
            (out / NATIVE_METADATA_FILENAME).read_bytes()).hexdigest()
        return result

    def diagnostics(self) -> dict:
        return {
            "algorithm": "primal_cvar",
            "implementation": "PRIMAL-inspired online dual-stream CVaR learner",
            "native_checkpoint_filename": NATIVE_CHECKPOINT_FILENAME,
            "native_numpy_checkpoint_filename": NATIVE_NUMPY_CHECKPOINT_FILENAME,
            "native_metadata_filename": NATIVE_METADATA_FILENAME,
            "contract": self.contract,
            "input_dim": int(self.input_dim),
            "feature_width": INPUT_DIM,
            "feature_order": self.feature_order,
            "normalisation": self.normalisation,
            "action_order": list(ACTIONS),
            "observation_layout": OBSERVATION_LAYOUT,
            "observation_contract_id": self.observation_contract_id,
            "mode": self.mode,
            "actions": list(ACTIONS),
            "decisions": int(self.decisions),
            "calls": int(self.decisions),
            "transitions": int(self.transitions),
            "train_steps": int(self.train_steps),
            "replay_size": len(self.replay),
            "seed": self.seed,
            "learning_clock_s": self._learning_clock(),
            "op_determinism": bool(self.op_determinism),
            "cpu_only_verified": bool(self.cpu_only_verified),
            "tf_version": str(self.tf.__version__),
            "fast_train": self._fast_enabled,
            "execution_path": "tf.function" if self._fast_enabled else "eager",
            "agent_scope": "shared",
            "clip_norm": self.cfg.get("clip_norm"),
            "gamma": float(self.cfg["gamma"]),
            "train_every_steps": int(self.cfg.get("train_every_steps", 1)),
            "warmup_steps": int(self.cfg.get("warmup_steps", 0)),
            "target_update_interval": int(self.cfg["target_update_interval"]),
            "cvar_alpha": float(self.cvar_alpha),
            "lambda_pd": float(self.lambda_pd),
            "dual_max": float(self.lambda_max),
            "dual_lr": float(self.dual_lr),
            "dual_constraint": float(self.dual_constraint),
            "dual_updates": int(self.dual_updates),
            "dual_update_on_done": bool(self.dual_update_on_done),
            "round_updates": int(self.round_updates),
            "last_training_constraint": self.last_training_constraint,
            "last_round_failure_rate": self.last_round_failure_rate,
            "risk_loss_weight": float(self.risk_loss_weight),
            "quantile_huber_delta": float(self.quantile_huber_delta),
            "last_loss": self.last_loss,
            "last_reward_loss": self.last_reward_loss,
            "last_risk_loss": self.last_risk_loss,
            "last_utility": (None if self._last_utility is None
                             else self._last_utility.tolist()),
            "last_cvar": (None if self._last_cvar is None
                           else self._last_cvar.tolist()),
            "last_scores": (None if self._last_scores is None
                            else self._last_scores.tolist()),
            "cost_semantics": (
                "binary terminal cost: 1.0 iff intermediate=false and the "
                "packet terminates as failure/non-delivery, otherwise 0.0; "
                "queue utility is stored separately"),
            "loaded_checkpoint": self.loaded_checkpoint,
            "loaded_checkpoint_sha256": self.loaded_checkpoint_sha256,
            "loaded_checkpoint_metadata_sha256":
                self.loaded_checkpoint_metadata_sha256,
            "checkpoint_verified": bool(self.checkpoint_verified),
            "shape_checked": bool(self.shape_checked),
            "finite_probe_checked": bool(self.finite_probe_checked),
            "loaded_resume_path": self.loaded_resume_path,
            "loaded_resume_sha256": self.loaded_resume_sha256,
            "resume_manifest_sha256": self.resume_manifest_sha256,
            "resume_verified": bool(self.resume_verified),
            "parameter_sha256": self._parameter_sha256(),
        }


__all__ = [
    "TensorflowPrimalCVaR",
    "NumpyPrimalCVaR",
    "INPUT_DIM",
    "SATELLITE_INPUT_DIM",
    "PACKET_INPUT_DIM",
    "ACTION_COUNT",
    "RISK_QUANTILES",
    "OBSERVATION_LAYOUT",
    "PRIMAL_FEATURE_ORDER",
    "DEFAULT_NORMALISATION",
    "DEFAULT_OBSERVATION_CONTRACT_ID",
    "NATIVE_CHECKPOINT_FILENAME",
    "NATIVE_NUMPY_CHECKPOINT_FILENAME",
    "NATIVE_METADATA_FILENAME",
    "split_observation",
    "clip_risk_target",
    "cvar_tail",
    "risk_scores",
    "masked_risk_argmax",
    "project_dual",
    "_PrimalReplay",
]
