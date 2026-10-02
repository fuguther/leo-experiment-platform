"""Contract tests for the stateless per-packet deadline objective helper."""

from dataclasses import FrozenInstanceError
import math

import pytest

from CODE.leo_sim.deadline_objective import deadline_loss_v1


def _evaluate(*, outcome, deadline_s=4.0, e2e_delay_s=None, gamma=1.0,
              episode_closed=False):
    return deadline_loss_v1(
        deadline_s=deadline_s,
        outcome=outcome,
        e2e_delay_s=e2e_delay_s,
        gamma=gamma,
        episode_closed=episode_closed,
    )


@pytest.mark.parametrize(
    ("elapsed_s", "expected_reward"),
    [
        (0.0, 0.0),
        (1.0, -0.25),
        (3.0, -0.75),
        (4.0, -1.0),
        (5.0, -1.0),
    ],
)
def test_delivery_settles_one_terminal_deadline_loss(elapsed_s, expected_reward):
    result = _evaluate(outcome="delivered", e2e_delay_s=elapsed_s)

    assert result.reward == expected_reward
    assert result.terminal is True


def test_deadline_expiry_without_terminal_delivery_is_failure():
    result = _evaluate(outcome="deadline_expired")

    assert result.reward == -1.0
    assert result.terminal is True


def test_intermediate_transition_has_zero_reward_and_stays_open():
    result = _evaluate(outcome="intermediate")

    assert result.reward == 0.0
    assert result.terminal is False


def test_administrative_censor_returns_no_learning_transition():
    assert _evaluate(outcome="admin_censored") is None


@pytest.mark.parametrize("deadline_s", [0.0, -1.0, math.inf, -math.inf, math.nan])
def test_deadline_must_be_finite_and_positive(deadline_s):
    with pytest.raises(ValueError):
        _evaluate(outcome="intermediate", deadline_s=deadline_s)


@pytest.mark.parametrize("elapsed_s", [-1.0, math.inf, -math.inf, math.nan])
def test_delivery_delay_must_be_finite_and_nonnegative(elapsed_s):
    with pytest.raises(ValueError):
        _evaluate(outcome="delivered", e2e_delay_s=elapsed_s)


@pytest.mark.parametrize("gamma", [0.0, 0.99, -1.0, math.inf, -math.inf, math.nan])
def test_gamma_must_equal_one(gamma):
    with pytest.raises(ValueError):
        _evaluate(outcome="intermediate", gamma=gamma)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"deadline_s": True}, TypeError),
        ({"gamma": True}, TypeError),
        ({"e2e_delay_s": False}, TypeError),
    ],
)
def test_boolean_values_are_not_numeric_objective_inputs(kwargs, error):
    call_kwargs = {"outcome": "delivered", "e2e_delay_s": 0.5}
    call_kwargs.update(kwargs)

    with pytest.raises(error):
        _evaluate(**call_kwargs)


def test_only_delivery_accepts_an_e2e_delay_and_delivery_requires_one():
    with pytest.raises(ValueError):
        _evaluate(outcome="delivered")
    with pytest.raises(ValueError):
        _evaluate(outcome="intermediate", e2e_delay_s=0.5)


def test_unknown_outcome_is_rejected():
    with pytest.raises(ValueError):
        _evaluate(outcome="late_delivery")


def test_expired_episode_rejects_later_physical_delivery_settlement():
    expired = _evaluate(outcome="deadline_expired")
    assert expired.terminal is True

    with pytest.raises(ValueError, match="closed"):
        _evaluate(
            outcome="delivered",
            e2e_delay_s=5.0,
            episode_closed=expired.terminal,
        )


def test_separate_packet_calls_are_independent_and_result_is_immutable():
    first_packet = _evaluate(outcome="delivered", e2e_delay_s=1.0)
    second_packet = _evaluate(outcome="delivered", e2e_delay_s=2.0)

    assert first_packet.reward == -0.25
    assert second_packet.reward == -0.5
    with pytest.raises(FrozenInstanceError):
        first_packet.reward = 0.0
