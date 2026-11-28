"""Focused tests for the staged B6 PRIMAL-inspired CVaR learner."""
from __future__ import annotations

import hashlib
import importlib.util
import json

import numpy as np
import pytest

from CODE.leo_sim.inference import observation_contract_id
from CODE.leo_sim.rl.agents.primal_cvar import (
    ACTION_COUNT,
    DEFAULT_OBSERVATION_CONTRACT_ID,
    INPUT_DIM,
    PACKET_INPUT_DIM,
    PRIMAL_FEATURE_ORDER,
    RISK_QUANTILES,
    SATELLITE_INPUT_DIM,
    _PrimalReplay,
    clip_risk_target,
    cvar_tail,
    masked_risk_argmax,
    project_dual,
    risk_scores,
    split_observation,
)
from CODE.leo_sim.rl.observations import ACTIONS


HAS_TENSORFLOW = importlib.util.find_spec("tensorflow") is not None


def test_primal_observation_contract_is_fixed_32_plus_18():
    obs = np.linspace(-1.0, 1.0, INPUT_DIM, dtype=np.float32)
    satellite, packet = split_observation(obs)
    assert satellite.shape == (SATELLITE_INPUT_DIM,)
    assert packet.shape == (PACKET_INPUT_DIM,)
    assert SATELLITE_INPUT_DIM + PACKET_INPUT_DIM == INPUT_DIM == 50
    assert DEFAULT_OBSERVATION_CONTRACT_ID == observation_contract_id(
        contract="PRIMAL", feature_width=INPUT_DIM,
        feature_order=PRIMAL_FEATURE_ORDER,
        normalisation={"mode": "none"}, actions=ACTIONS)
    with pytest.raises(ValueError, match="shape"):
        split_observation(np.zeros(49, dtype=np.float32))
    bad = np.zeros(INPUT_DIM, dtype=np.float32)
    bad[7] = np.nan
    with pytest.raises(ValueError, match="finite"):
        split_observation(bad)


def test_cvar_uses_worst_quantile_tail_not_the_mean():
    costs = np.arange(1.0, RISK_QUANTILES + 1.0)
    assert np.isclose(cvar_tail(costs, alpha=0.75), 14.5)
    repeated = np.vstack([costs, costs])
    assert np.allclose(cvar_tail(repeated, alpha=0.75), [14.5, 14.5])


def test_risk_target_is_clipped_to_the_bounded_failure_domain():
    values = np.asarray([-3.0, 0.0, 0.25, 1.0, 4.0], dtype=np.float32)
    assert clip_risk_target(values).tolist() == [0.0, 0.0, 0.25, 1.0, 1.0]
    with pytest.raises(ValueError, match="finite"):
        clip_risk_target([0.0, np.nan])


def _zero_numpy_weights():
    return {
        "satellite_w": np.zeros((SATELLITE_INPUT_DIM, 64), dtype=np.float32),
        "satellite_b": np.zeros(64, dtype=np.float32),
        "packet_w": np.zeros((PACKET_INPUT_DIM, 32), dtype=np.float32),
        "packet_b": np.zeros(32, dtype=np.float32),
        "trunk128_w": np.zeros((96, 128), dtype=np.float32),
        "trunk128_b": np.zeros(128, dtype=np.float32),
        "trunk64_w": np.zeros((128, 64), dtype=np.float32),
        "trunk64_b": np.zeros(64, dtype=np.float32),
        "utility_w": np.zeros((64, ACTION_COUNT), dtype=np.float32),
        "utility_b": np.zeros(ACTION_COUNT, dtype=np.float32),
        "risk_w": np.zeros((64, ACTION_COUNT * RISK_QUANTILES),
                           dtype=np.float32),
        "risk_b": np.zeros(ACTION_COUNT * RISK_QUANTILES, dtype=np.float32),
    }


def test_numpy_primal_checkpoint_runs_and_fixed_loader_uses_no_tensorflow(tmp_path):
    from CODE.leo_sim import inference
    from CODE.leo_sim.rl.agents.primal_cvar import NumpyPrimalCVaR

    weights_path = tmp_path / "online.npz"
    np.savez_compressed(weights_path, **_zero_numpy_weights())
    keras_path = tmp_path / "online.keras"
    keras_path.write_bytes(b"hash-pinned tensorflow artifact")
    metadata = inference.observation_contract_fields(
        contract="PRIMAL", feature_width=INPUT_DIM,
        feature_order=PRIMAL_FEATURE_ORDER,
        normalisation={"mode": "none"}, actions=ACTIONS)
    metadata.update({
        "schema": "leo-sim-primal-cvar/v1",
        "algorithm": "primal_cvar",
        "checkpoint": keras_path.name,
        "checkpoint_sha256": hashlib.sha256(keras_path.read_bytes()).hexdigest(),
        "checkpoint_verified": True,
        "numpy_checkpoint": weights_path.name,
        "numpy_checkpoint_sha256": hashlib.sha256(
            weights_path.read_bytes()).hexdigest(),
        "numpy_verified": True,
        "weights_purpose": "trained",
        "risk_quantiles": RISK_QUANTILES,
        "cvar_alpha": 0.75,
        "lambda_pd": 0.1,
        "dual_max": 1.0,
    })
    metadata_path = tmp_path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    policy = inference.load_fixed_adapter_from_checkpoint(
        keras_path, metadata["checkpoint_sha256"], metadata_path,
        hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
        expected_width=INPUT_DIM, expected_contract="PRIMAL",
        expected_feature_order=PRIMAL_FEATURE_ORDER,
        algorithm="primal_cvar")
    assert isinstance(policy, NumpyPrimalCVaR)
    mask = {action: True for action in ACTIONS}
    assert policy.choose(np.zeros(INPUT_DIM, dtype=np.float32), mask) == "deliver"
    assert policy.counters()["calls"] == 1
    assert policy.counters()["training"] is False
    assert policy.receipt()["state"] == "AVAILABLE"


def test_online_selection_uses_legal_mask_and_risk_adjusted_score():
    utility = np.full(ACTION_COUNT, 10.0)
    risk = np.full((ACTION_COUNT, RISK_QUANTILES), 2.0)
    risk[4] = 1.0          # W is cheap
    risk[3] = 100.0        # E is expensive
    risk[2] = 50.0         # S is expensive
    mask = {"deliver": False, "N": True, "S": True, "E": True, "W": True}
    assert masked_risk_argmax(utility, risk, mask, multiplier=2.0) == 4
    scores = risk_scores(utility, risk, multiplier=2.0)
    assert scores[4] == pytest.approx(8.0)
    with pytest.raises(ValueError, match="legal action"):
        masked_risk_argmax(utility, risk, {a: False for a in mask}, multiplier=1.0)


def test_dual_projection_enforces_training_constraint_and_bounds():
    assert project_dual(0.10, observed_cost=1.0, budget=0.05,
                        step_size=0.20, maximum=0.15) == pytest.approx(0.15)
    assert project_dual(0.10, observed_cost=0.0, budget=0.05,
                        step_size=1.0, maximum=10.0) == pytest.approx(0.05)
    assert project_dual(0.00, observed_cost=0.0, budget=1.0,
                        step_size=1.0, maximum=10.0) == pytest.approx(0.0)


def test_replay_keeps_cost_channel_separate_and_preserves_fifo_overwrite():
    replay = _PrimalReplay(3)
    mask = np.asarray([True, True, False, False, False], dtype=bool)
    for index in range(4):
        state = np.full(INPUT_DIM, index, dtype=np.float32)
        next_state = state + 1.0
        replay.append(state, index % ACTION_COUNT, 10.0 + index,
                      1.0 if index == 3 else 0.0,
                      next_state, mask, index == 3)
    states, actions, utilities, costs, next_states, masks, dones = replay.to_arrays()
    assert states.shape == (3, INPUT_DIM)
    assert actions.tolist() == [1, 2, 3]
    assert utilities.tolist() == pytest.approx([11.0, 12.0, 13.0])
    assert costs.tolist() == pytest.approx([0.0, 0.0, 1.0])
    assert np.array_equal(next_states, states + 1.0)
    assert np.array_equal(masks, np.tile(mask, (len(states), 1)))
    assert dones.tolist() == [False, False, True]


def _learning_cfg(*, mode: str = "train", fast_train: bool = False) -> dict:
    return {
        "mode": mode,
        "replay_size": 8,
        "batch_size": 2,
        "warmup_steps": 0,
        "train_every_steps": 1,
        "target_update_interval": 1,
        "lr": 1e-3,
        "gamma": 0.99,
        "clip_norm": 10.0,
        "fast_train": fast_train,
        "epsilon_start": 0.0,
        "epsilon_end": 0.0,
        "epsilon_decay_s": 15.0,
        "cvar_alpha": 0.75,
        "lambda_init": 0.1,
        "dual_max": 0.5,
        "dual_lr": 0.05,
        "dual_constraint": 0.05,
    }


@pytest.mark.skipif(not HAS_TENSORFLOW,
                    reason="TensorFlow 2.13 CPU smoke test")
def test_tf_smoke_sha_bound_eval_does_not_train_or_update_dual(tmp_path):
    from CODE.leo_sim.rl.agents.primal_cvar import TensorflowPrimalCVaR

    learner = TensorflowPrimalCVaR("PRIMAL", _learning_cfg(), seed=7)
    state = np.zeros(INPUT_DIM, dtype=np.float32)
    next_state = np.ones(INPUT_DIM, dtype=np.float32)
    train_mask = {action: True for action in ("deliver", "N", "S", "E", "W")}
    terminal_mask = {action: False for action in train_mask}
    with pytest.raises(ValueError, match="done=True"):
        learner.remember_with_cost(
            state, "E", 1.0, next_state, train_mask, False, cost=1.0)
    learner.remember_with_cost(
        state, "E", 1.0, next_state, train_mask, False, cost=0.0)
    learner.remember_with_cost(
        next_state, "N", 0.5, state, terminal_mask, True, cost=1.0)
    assert learner.train_steps == 1
    assert learner.dual_updates == 0
    learner.finish_round(1.0)
    assert learner.dual_updates == 1
    assert learner.round_updates == 1
    assert learner.lambda_pd > 0.1
    metadata = learner.save_and_verify(tmp_path / "trained")
    assert metadata["algorithm"] == "primal_cvar"
    assert metadata["schema"] == "leo-sim-primal-cvar/v1"
    assert metadata["checkpoint_verified"] is True
    assert metadata["risk_quantiles"] == RISK_QUANTILES
    assert metadata["native_checkpoint_filename"] == "online.keras"
    assert metadata["native_numpy_checkpoint_filename"] == "online.npz"
    assert metadata["numpy_verified"] is True
    assert (tmp_path / "trained" / "online.npz").is_file()
    assert metadata["round_updates"] == 1
    assert metadata["last_round_failure_rate"] == 1.0

    eval_cfg = _learning_cfg(mode="eval")
    eval_cfg.update({
        "checkpoint_path": str(tmp_path / "trained" / "online.keras"),
        "checkpoint_sha256": metadata["checkpoint_sha256"],
        "checkpoint_metadata_sha256": metadata["metadata_sha256"],
    })
    frozen = TensorflowPrimalCVaR("PRIMAL", eval_cfg, seed=7)
    lambda_before = frozen.lambda_pd
    action = frozen.choose(state, train_mask, now=1.0)
    assert action in train_mask
    assert frozen.decisions == 1
    assert frozen.train_steps == 0
    assert frozen.lambda_pd == lambda_before
    frozen.finish_round(1.0)
    assert frozen.lambda_pd == lambda_before
    assert frozen.round_updates == 0


@pytest.mark.skipif(not HAS_TENSORFLOW,
                    reason="TensorFlow 2.13 CPU exact-resume contract")
def test_tf_exact_resume_continues_weights_optimizer_rng_replay_and_clock(tmp_path):
    from CODE.leo_sim.rl.agents.primal_cvar import TensorflowPrimalCVaR

    cfg = _learning_cfg(fast_train=True)
    learner = TensorflowPrimalCVaR("PRIMAL", cfg, seed=11)
    state = np.zeros(INPUT_DIM, dtype=np.float32)
    next_state = np.ones(INPUT_DIM, dtype=np.float32)
    mask = {action: True for action in ("deliver", "N", "S", "E", "W")}
    learner.remember_with_cost(
        state, "E", 1.0, next_state, mask, False, cost=0.0)
    learner.remember_with_cost(
        next_state, "W", 0.5, state, mask, False, cost=0.0)
    learner.choose(state, mask, now=0.25)
    learner.finish_round(0.5)
    metadata = learner.save_and_verify(tmp_path / "checkpoint")

    resumed_cfg = dict(cfg)
    resumed_cfg.update({
        "resume_path": str(tmp_path / "checkpoint" / "resume"),
        "resume_sha256": metadata["resume_sha256"],
    })
    resumed = TensorflowPrimalCVaR("PRIMAL", resumed_cfg, seed=11)
    assert resumed.decisions == learner.decisions
    assert resumed.transitions == learner.transitions
    assert resumed.train_steps == learner.train_steps
    assert resumed.lambda_pd == learner.lambda_pd
    assert resumed.round_updates == learner.round_updates == 1
    assert resumed.last_round_failure_rate == learner.last_round_failure_rate
    assert resumed._learning_clock() == learner._learning_clock()
    for left, right in zip(resumed.online.get_weights(), learner.online.get_weights()):
        assert np.array_equal(left, right)
    for left, right in zip(resumed.target.get_weights(), learner.target.get_weights()):
        assert np.array_equal(left, right)
    for left, right in zip(resumed.optimizer.variables, learner.optimizer.variables):
        assert np.array_equal(left.numpy(), right.numpy())
    for left, right in zip(resumed.replay.to_arrays(), learner.replay.to_arrays()):
        assert np.array_equal(left, right)
    assert np.array_equal(
        resumed.tf.random.get_global_generator().state.numpy(),
        learner.tf.random.get_global_generator().state.numpy())

    probe = np.linspace(-0.5, 0.5, INPUT_DIM, dtype=np.float32)
    action_a = learner.choose(probe, mask, now=1.0)
    action_b = resumed.choose(probe, mask, now=1.0)
    assert action_b == action_a
    learner.remember_with_cost(
        probe, action_a, 0.25, state, mask, False, cost=0.0)
    resumed.remember_with_cost(
        probe, action_b, 0.25, state, mask, False, cost=0.0)
    assert resumed.train_steps == learner.train_steps
    for left, right in zip(resumed.online.get_weights(), learner.online.get_weights()):
        assert np.array_equal(left, right)
    for left, right in zip(resumed.target.get_weights(), learner.target.get_weights()):
        assert np.array_equal(left, right)


def test_primal_one_packet_is_batched_and_eval_does_not_write_replay():
    from CODE.leo_sim.rl.agents.primal_cvar import TensorflowPrimalCVaR, _split_observation_batch

    state = np.zeros(50, dtype=np.float32)
    satellite, packet = _split_observation_batch(state)
    assert satellite.shape == (1, 32)
    assert packet.shape == (1, 18)
    learner = object.__new__(TensorflowPrimalCVaR)
    learner.mode = "eval"
    learner.transitions = 0
    learner.remember_with_cost(state, "N", -1, state, [False] * 5, True, cost=1.0)
    assert learner.transitions == 1
