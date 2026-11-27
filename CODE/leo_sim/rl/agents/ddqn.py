"""Double-DQN learning agents registered under ``algorithm=ddqn``."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ..models import (
    _configure_tf_threads,
    _extract_dense_mlp,
    _graph_custom_objects,
    _numpy_mlp_forward,
    build_model,
    require_tensorflow,
)
from ..observations import (
    ACTIONS,
    CONTRACT_DIMS,
    GRAPH_CONTRACTS,
    contract_dim,
    ddqn_targets,
)
from ..persistence import (
    LearningUnavailable,
    _read_checkpoint_metadata,
    _read_json_bytes,
    _require_resume_identity,
    _resume_identity,
    _resume_json,
    _resume_unjson,
    _verify_checkpoint_metadata,
    _verify_resume_bundle,
    _write_resume_manifest,
)
from ..registry import register_agent
from ..replay import _ReplayBuffer


@register_agent("ddqn", scope="shared")
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
        return build_model(
            self.contract, self.tf, self.input_dim,
            model_name=self.cfg.get("model"))

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
        from ... import inference as _inference

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


@register_agent("ddqn", scope="per_satellite")
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
        from ... import inference as _inference

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


__all__ = ["TensorflowDDQN", "PerSatelliteTensorflowDDQN"]
