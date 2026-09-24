"""Safety contract for the learning objective used in paper experiments."""

from __future__ import annotations

import pytest

from CODE.leo_sim import config, learning


def test_forward_reward_cannot_make_an_extra_hop_positive() -> None:
    """A forwarding transition must not be profitable before delivery."""
    assert learning.forward_reward(0.0, 20.0, 200.0, -20.0) == pytest.approx(0.0)
    assert learning.forward_reward(0.005, 20.0, 200.0, -20.0) < 0.0
    assert learning.forward_reward(1.0, 20.0, 200.0, -20.0) < 0.0


def test_config_requires_step_penalty_to_dominate_raw_queue_reward() -> None:
    resolved = config.resolve_config({})["config"]["learning"]
    assert resolved["forward_step_penalty"] == pytest.approx(-resolved["reward_w1"])

    with pytest.raises(config.ConfigError, match="forward_step_penalty"):
        config.resolve_config({"learning": {"forward_step_penalty": -19.0}})
