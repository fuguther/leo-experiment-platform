"""Extension contracts for the modular RL registry and learner factory."""
import pytest

from CODE.leo_sim import kernel, learning, model, rl
from CODE.leo_sim.tests.helpers import cell, make_cfg, row


def test_custom_agent_plugin_is_loaded_through_factory():
    @rl.register_agent("test_factory_agent")
    class StubAgent:
        def __init__(self, contract, cfg, seed):
            self.contract = contract
            self.cfg = cfg
            self.seed = seed

    learner = rl.create_learner(
        "C3", {"algorithm": "test_factory_agent", "agent_scope": "shared"}, 23)
    assert isinstance(learner, StubAgent)
    assert (learner.contract, learner.seed) == ("C3", 23)


def test_custom_model_plugin_is_loaded_and_executed():
    calls = []

    @rl.register_model("test_factory_model")
    def build(_tf, input_dim, n_actions):
        calls.append((input_dim, n_actions))
        return "model-instance"

    assert rl.build_model(
        "C3", object(), 11, model_name="test_factory_model") == "model-instance"
    assert calls == [(11, len(learning.ACTIONS))]


def test_plugin_registration_fails_closed_on_duplicate_names():
    rl.register_model("test_duplicate_model")(lambda *_: None)
    with pytest.raises(ValueError, match="duplicate model registration"):
        rl.register_model("test_duplicate_model")(lambda *_: None)

    rl.register_agent("test_duplicate_agent", scope="shared")(object)
    with pytest.raises(ValueError, match="duplicate agent registration"):
        rl.register_agent("test_duplicate_agent", scope="shared")(object)


def test_kernel_learning_construction_uses_registered_factory(monkeypatch):
    calls = []
    original = learning.TabularQLearning

    def factory(contract, cfg, seed):
        calls.append((contract, cfg["algorithm"], cfg["agent_scope"], seed))
        return original(contract, cfg, seed)

    monkeypatch.setattr(learning, "create_learner", factory)
    a = cell(0.0, 0.0)
    b = cell(0.0, 10.0)
    cfg = make_cfg({
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    geo = model.Constellation(
        num_satellites=1, num_planes=1, altitude_km=550,
        inclination_deg=53, min_elevation_deg=0.0)
    kernel.run_simulation(cfg, [row(1, 0.0, a, b)], geometry=geo)
    expected_seed = cfg["config"]["scenario"]["seed"]
    assert calls == [("C3", "qlearning", "shared", expected_seed)]

def test_compatibility_facade_preserves_model_and_agent_class_identity():
    assert learning.V2GraphEncoder is rl.V2GraphEncoder
    assert learning._graph_custom_objects()["V2GraphEncoder"] is rl.V2GraphEncoder
    assert rl.get_agent_class("ddqn", scope="shared") is learning.TensorflowDDQN
    assert rl.get_agent_class(
        "qlearning", scope="per_satellite"
    ) is learning.PerSatelliteTabularQLearning
