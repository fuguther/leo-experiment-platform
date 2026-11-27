"""Contract tests for neighbor_1hop/flat_mean/GAT/MPNN observations, action masks, canonical DDQN."""
import importlib.util

import numpy as np
import pytest

from CODE.leo_sim import control, kernel, learning, receipt
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)

TOPO = {0: {"E": 1, "N": 2}, 1: {"W": 0}, 2: {"S": 0}, 3: {"W": 9}}


def _entries(cache_spec):
    c = control.LocalCache()
    for origin, gen, hops in cache_spec:
        payload = {"isl_queue_bits": {"E": 1000}, "access_slots_used": 1,
                   "access_slots_cap": 4, "visible_cells": [B]}
        c.put(control.CacheEntry(origin, payload, gen, gen + 0.01, 10.0, hops=hops))
    return c


def _own():
    return learning.own_state(1, 4, {"E": 1000}, 256_000_000, 2, 10)


def test_flat_mean_and_graph_contracts_share_the_same_information_set():
    cache = _entries([(1, 5.0, 1), (2, 5.0, 2), (3, 5.0, 2)])
    sets = {c: set(learning.information_set(c, 0, cache, 6.0, TOPO))
            for c in ("flat_mean", "C3", "GAT", "MPNN")}
    assert len({frozenset(s) for s in sets.values()}) == 1
    assert sets["flat_mean"] == {1, 2, 3}


def test_legacy_c1_c3_aliases_match_canonical_names_and_c4_c7_rejected():
    cache = _entries([(1, 5.0, 1), (2, 4.0, 2)])
    own = _own()
    assert np.array_equal(
        learning.build_observation("neighbor_1hop", 0, cache, 6.0, TOPO, own),
        learning.build_observation("C1", 0, cache, 6.0, TOPO, own),
    )
    assert np.array_equal(
        learning.build_observation("flat_mean", 0, cache, 6.0, TOPO, own),
        learning.build_observation("C3", 0, cache, 6.0, TOPO, own),
    )
    for removed in ("C4", "C5", "C6", "C7"):
        with pytest.raises(ValueError, match="unknown contract"):
            learning.information_set(removed, 0, cache, 6.0, TOPO)
        with pytest.raises(ValueError, match="unknown contract"):
            learning.build_observation(removed, 0, cache, 6.0, TOPO, own)


def test_c1_sees_only_direct_neighbors_arriving_within_one_hop():
    cache = _entries([(1, 5.0, 1), (2, 5.0, 2), (3, 5.0, 1)])
    # Origin 2 is a current neighbour but its advertisement arrived via two
    # control hops; origin 3 arrived in one hop but is not a current neighbour.
    # Neither may enter the C1 observation/action information set.
    assert set(learning.information_set("C1", 0, cache, 6.0, TOPO)) == {1}


def test_expired_or_future_entries_never_enter_observations():
    c = control.LocalCache()
    c.put(control.CacheEntry(1, {"isl_queue_bits": {}}, 5.0, 5.01, 1.0))   # expired by 7
    c.put(control.CacheEntry(2, {"isl_queue_bits": {}}, 99.0, 99.01, 10.0))  # future
    for contract in learning.CONTRACTS:
        assert learning.information_set(contract, 0, c, 7.0, TOPO) == {}


def test_observation_shapes_fixed_and_deterministic():
    cache = _entries([(1, 5.0, 1), (2, 4.0, 2)])
    own = _own()
    for contract, dim in learning.CONTRACT_DIMS.items():
        o1 = learning.build_observation(contract, 0, cache, 6.0, TOPO, own)
        o2 = learning.build_observation(contract, 0, cache, 6.0, TOPO, own)
        assert o1.shape == (dim,), contract
        assert np.array_equal(o1, o2)
        assert np.all(np.isfinite(o1))


def test_graph_observation_width_matches_contract_dim():
    cache = _entries([(1, 5.0, 1), (2, 5.0, 2)])
    own = _own()
    for contract in learning.GRAPH_CONTRACTS:
        o = learning.build_observation(contract, 0, cache, 6.0, TOPO, own)
        assert o.shape == (learning.CONTRACT_DIMS[contract],), contract
        assert np.array_equal(
            o[-(learning.OWN_FEATURES + 3):-3], own), \
            "graph tail must carry the own-state block"


def test_graph_observation_uses_only_arrived_valid_cache():
    c = control.LocalCache()
    c.put(control.CacheEntry(1, {"isl_queue_bits": {"E": 1000},
                                 "access_slots_used": 1, "access_slots_cap": 4,
                                 "visible_cells": [B]}, 5.0, 5.01, 1.0))  # expired at 7
    c.put(control.CacheEntry(2, {"isl_queue_bits": {"E": 2000},
                                 "access_slots_used": 1, "access_slots_cap": 4,
                                 "visible_cells": [B]}, 99.0, 99.01, 10.0))  # future
    own = _own()
    for contract in learning.GRAPH_CONTRACTS:
        o = learning.build_observation(contract, 0, c, 7.0, TOPO, own)
        feats = o[:learning.GRAPH_MAX_NODES * learning.GRAPH_NODE_FEAT_DIM]
        feats = feats.reshape(learning.GRAPH_MAX_NODES,
                              learning.GRAPH_NODE_FEAT_DIM)
        nonempty = [i for i in range(learning.GRAPH_MAX_NODES)
                    if feats[i, 7] > 0.5]
        assert nonempty == [0], "expired/future entries must not be graph nodes"


def test_graph_adjacency_uses_true_directed_topo():
    cache = _entries([(1, 5.0, 1), (2, 5.0, 2)])
    own = _own()
    o = learning.build_observation("GAT", 0, cache, 6.0, TOPO, own)
    n = learning.GRAPH_MAX_NODES
    node_feats = o[:n * learning.GRAPH_NODE_FEAT_DIM].reshape(
        n, learning.GRAPH_NODE_FEAT_DIM)
    adj_start = n * learning.GRAPH_NODE_FEAT_DIM
    adj = o[adj_start:adj_start + n * n].reshape(n, n)
    # root 0 has E->1 and N->2 in TOPO; both are cache origins.
    i1 = next(i for i in range(n) if node_feats[i, 6] == 0
              and node_feats[i, 8:12].argmax() == 2)  # first direction E (idx 2)
    i2 = next(i for i in range(n) if node_feats[i, 6] == 0
              and node_feats[i, 8:12].argmax() == 0)  # first direction N (idx 0)
    assert adj[i1, 0] == 1.0 or adj[i2, 0] == 1.0
    assert np.all(adj[i1] >= 0.0) and np.all(adj[i2] >= 0.0)


def test_destination_features_appear_in_every_contract_tail():
    cache = _entries([(1, 5.0, 1), (2, 5.0, 2)])
    own = _own()
    dst = learning.destination_features(10.0, 20.0, 30.0, 40.0)
    assert dst.shape == (learning.DEST_FEATURES,)
    for contract in learning.CONTRACT_DIMS:
        o = learning.build_observation(
            contract, 0, cache, 6.0, TOPO, own, dst_feats=dst)
        assert o.shape == (learning.CONTRACT_DIMS[contract],), contract
        assert np.allclose(o[-3:], dst), contract


def test_obs_hops_filters_cache_entries():
    # entries at 1 and 2 hops; obs_hops=1 must drop the 2-hop origin.
    cache = _entries([(1, 5.0, 1), (2, 5.0, 2)])
    own = _own()
    info1 = learning.information_set("C3", 0, cache, 6.0, TOPO, obs_hops=1)
    assert set(info1) == {1}
    # graph observation with obs_hops=1 must contain fewer valid nodes
    full = learning.build_observation("GAT", 0, cache, 6.0, TOPO, own)
    limited = learning.build_observation(
        "GAT", 0, cache, 6.0, TOPO, own, obs_hops=1)
    assert not np.array_equal(full, limited)


def test_empty_cache_yields_zero_blocks():
    cache = control.LocalCache()
    own = _own()
    for contract, dim in learning.CONTRACT_DIMS.items():
        o = learning.build_observation(contract, 0, cache, 6.0, TOPO, own)
        assert o.shape == (dim,)
        if contract in learning.GRAPH_CONTRACTS:
            assert np.array_equal(o[-(learning.OWN_FEATURES + 3):-3],
                                  own)  # own block in graph tail
        else:
            assert np.array_equal(o[:learning.OWN_FEATURES], own)
        # destination features are zeroed when no destination is supplied
        assert np.array_equal(o[-3:], np.zeros(learning.DEST_FEATURES))


def test_action_mask_legality():
    mask = learning.build_action_mask(True, {"E": True, "W": False})
    assert mask == {"deliver": True, "E": True, "W": False}
    mask2 = learning.build_action_mask(False, {"E": False, "W": False})
    assert not any(mask2.values())


def test_canonical_ddqn_target_math():
    q_online = np.array([[0.1, 5.0, 2.0]])   # argmax would be action 1
    q_target = np.array([[1.0, 9.0, 3.0]])
    mask = np.array([[True, False, True]])   # action 1 illegal
    y = learning.ddqn_targets(q_online, q_target, mask,
                              rewards=np.array([1.0]), dones=np.array([False]),
                              gamma=0.9)
    # online argmax over legal actions is 2; target value of action 2 is 3.0
    assert abs(y[0] - (1.0 + 0.9 * 3.0)) < 1e-12


def test_ddqn_terminal_transition_blocks_bootstrap():
    q = np.array([[10.0]])
    mask = np.array([[True]])
    y = learning.ddqn_targets(q, q, mask, rewards=np.array([2.0]),
                              dones=np.array([True]), gamma=0.99)
    assert abs(y[0] - 2.0) < 1e-12


def test_ddqn_requires_a_legal_action():
    with pytest.raises(ValueError):
        learning.ddqn_targets(np.array([[1.0]]), np.array([[1.0]]),
                              np.array([[False]]), np.array([0.0]),
                              np.array([False]), 0.9)


def test_learning_run_fails_closed_without_tensorflow():
    # A real DDQN adapter is wired into the kernel, but environments without
    # TensorFlow must still fail closed and never degrade to oracle/hop.
    cfg = make_cfg({"routing": {"policy": "hop", "learning_enabled": True},
                    "control_plane": {"enabled": True},
                    "learning": {"algorithm": "ddqn"}})
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    if importlib.util.find_spec("tensorflow") is None:
        with pytest.raises(learning.LearningUnavailable):
            kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)


def test_graph_learning_fails_closed_without_tensorflow():
    # GAT/MPNN graph contracts must obey the same fail-closed rule: without a
    # real TensorFlow runtime a learning run raises, never silently degrading.
    for contract in learning.GRAPH_CONTRACTS:
        cfg = make_cfg({"routing": {"policy": "hop", "learning_enabled": True,
                                    "contract": contract},
                        "control_plane": {"enabled": True},
                        "learning": {"algorithm": "ddqn"}})
        geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
        if importlib.util.find_spec("tensorflow") is None:
            with pytest.raises(learning.LearningUnavailable):
                kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
        else:
            # On TF hosts the run must at least construct without raising at
            # import time (network training itself is exercised on VM).
            # TensorflowDDQN takes the learning config section, not the
            # resolved wrapper dict (the kernel passes cfg["learning"]).
            cfg = make_cfg({
                "routing": {"policy": "hop", "learning_enabled": True,
                            "contract": contract},
                "control_plane": {"enabled": True},
                "learning": {"algorithm": "ddqn", "mode": "train"}})
            learning.TensorflowDDQN(contract, cfg["config"]["learning"],
                                    seed=1)


@pytest.mark.skipif(importlib.util.find_spec("tensorflow") is None,
                    reason="TensorFlow resume contract requires the VM runtime")
def test_ddqn_exact_resume_restores_replay_optimizer_target_and_rng(tmp_path):
    """The DDQN continuation artifact must include every training state."""
    from CODE.leo_sim import config

    resolved = config.resolve_config({
        "scenario": {"seed": 7},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True,
                    "contract": "C3"},
        "learning": {"algorithm": "ddqn", "mode": "train",
                      "batch_size": 1, "replay_size": 8,
                      "target_update_interval": 2, "fast_train": False},
    })
    cfg = resolved["config"]["learning"]
    learner = learning.TensorflowDDQN("C3", cfg, seed=7)
    dim = learning.CONTRACT_DIMS["C3"]
    mask = {a: True for a in learning.ACTIONS}
    learner.remember(np.zeros(dim), "E", 1.0, np.ones(dim), mask, False)
    learner.remember(np.ones(dim), "N", 2.0, np.zeros(dim), mask, True)
    meta = learner.save_and_verify(tmp_path)
    resumed_cfg = dict(cfg)
    resumed_cfg.update({
        "resume_path": str(tmp_path / "resume"),
        "resume_sha256": meta["resume_sha256"],
    })
    resumed = learning.TensorflowDDQN("C3", resumed_cfg, seed=7)
    assert resumed.loaded_resume_sha256 == meta["resume_sha256"]
    assert resumed.transitions == learner.transitions
    assert resumed.train_steps == learner.train_steps
    assert len(resumed.replay) == len(learner.replay)
    assert resumed.optimizer.iterations.numpy() == learner.optimizer.iterations.numpy()
    for left, right in zip(resumed.online.get_weights(), learner.online.get_weights()):
        assert np.array_equal(left, right)
    for left, right in zip(resumed.target.get_weights(), learner.target.get_weights()):
        assert np.array_equal(left, right)
    # Continue both instances with the same transition.  Equal state at the
    # checkpoint must produce the same next action, update count and weights.
    action_a = learner.choose(np.zeros(dim), mask, 1.0)
    action_b = resumed.choose(np.zeros(dim), mask, 1.0)
    assert action_a == action_b
    learner.remember(np.zeros(dim), action_a, 3.0, np.ones(dim), mask, False)
    resumed.remember(np.zeros(dim), action_b, 3.0, np.ones(dim), mask, False)
    assert resumed.train_steps == learner.train_steps
    for left, right in zip(resumed.online.get_weights(), learner.online.get_weights()):
        assert np.array_equal(left, right)
    for left, right in zip(resumed.target.get_weights(), learner.target.get_weights()):
        assert np.array_equal(left, right)


def test_learning_rejects_oracle_information():
    from CODE.leo_sim import config
    with pytest.raises(config.ConfigError, match="oracle"):
        make_cfg({"routing": {"policy": "oracle", "learning_enabled": True},
                  "control_plane": {"enabled": True},
                  "learning": {"algorithm": "ddqn"}})


def test_learning_seed_is_independent_of_scenario_seed():
    from CODE.leo_sim import config
    cfg = make_cfg({"scenario": {"seed": 7},
                    "routing": {"policy": "hop", "learning_enabled": True},
                    "control_plane": {"enabled": True},
                    "learning": {"algorithm": "ddqn", "seed": 41}})
    resolved = cfg["config"]
    assert resolved["learning"]["seed"] == 41
    assert resolved["scenario"]["seed"] == 7
    # Default: learning.seed is null and falls back to the scenario seed.
    cfg2 = make_cfg({"scenario": {"seed": 9},
                     "routing": {"policy": "hop", "learning_enabled": True},
                     "control_plane": {"enabled": True},
                     "learning": {"algorithm": "ddqn"}})
    assert cfg2["config"]["learning"]["seed"] is None
    for bad in (-1, True, 1.5):
        with pytest.raises(config.ConfigError, match="learning.seed"):
            make_cfg({"routing": {"policy": "hop", "learning_enabled": True},
                      "control_plane": {"enabled": True},
                      "learning": {"algorithm": "ddqn", "seed": bad}})


def test_eval_model_decisions_are_effective_without_training_steps():
    effective = receipt.effective_from_counters(
        {"control_entered_queue": 0, "ge_gsl_queries": 0,
         "ge_isl_queries": 0, "mbb_events": 0,
         "learning_decisions": 3, "learning_train_steps": 0},
        {"ge_enabled": False, "learning_mode": "eval"},
    )
    assert effective["learning"] is True


def _origin_entry_cache(origin, peer_claim, value):
    """One cache entry from `origin` advertising isl_queue_bits E->{peer,value}."""
    c = control.LocalCache()
    payload = {"isl_queue_bits": {"E": {"peer": peer_claim, "value": value}},
               "access_slots_used": 1, "access_slots_cap": 4,
               "visible_cells": [B]}
    c.put(control.CacheEntry(origin, payload, 5.0, 5.01, 10.0, hops=1))
    return c


def test_flat_contracts_validate_queue_bits_against_advertisement_origin():
    """Peer-bound isl_queue_bits must be validated against the topology edge
    of the advertisement's ORIGIN, not the root satellite's own edges: after
    a rematch the two can differ, and using the root would zero a valid
    fresh metric."""
    # root 0's E edge points at 3; the entry from origin 1 measured E on peer
    # 2 (topo[1]["E"] == 2).  Using origin 1 accepts the fresh value.
    topo = {0: {"E": 3, "N": 1}, 1: {"W": 0, "E": 2}, 2: {"W": 0},
            3: {"W": 0}}
    c = _origin_entry_cache(origin=1, peer_claim=2, value=1000)
    own = _own()
    for contract in ("flat_mean", "neighbor_1hop"):
        o = learning.build_observation(contract, 0, c, 6.0, topo, own)
        q = o[learning.OWN_FEATURES]  # queue ratio is the 1st origin feature
        assert q > 0.0, f"{contract} must accept a peer-matching entry"
        assert o.shape == (learning.CONTRACT_DIMS[contract],), contract


def test_flat_contracts_reject_queue_bits_matching_only_root_edge():
    """A record whose peer matches only the root satellite's edge (not the
    origin's) must read as 0: it is stale for the real advertisement origin."""
    # root 0 has E->2, but the entry from origin 1 measured E on peer 3
    # (topo[1]["E"] == 3): the claimed peer 2 is wrong for origin 1 even
    # though it happens to match root 0's own edge.
    topo = {0: {"E": 2, "N": 1}, 1: {"W": 0, "E": 3}, 2: {"W": 0},
            3: {"W": 0}}
    c = _origin_entry_cache(origin=1, peer_claim=2, value=1000)
    own = _own()
    for contract in ("flat_mean", "neighbor_1hop"):
        o = learning.build_observation(contract, 0, c, 6.0, topo, own)
        q = o[learning.OWN_FEATURES]
        assert q == 0.0, f"{contract} must reject a stale-peer record"


def test_flat_contracts_reject_queue_bits_after_direction_removed():
    """A record whose origin no longer has the advertised direction at all
    (a rematch deleted it, so topo[origin] has no such key) must read as 0:
    the stale metric must not be accepted just because no peer is left to
    mismatch against."""
    # origin 1 advertised E->{peer 2, value 1000}; after the rematch topo[1]
    # has no "E" direction at all (peer=None)
    topo = {0: {"E": 2, "N": 1}, 1: {"W": 0}, 2: {"W": 0}, 3: {"W": 0}}
    c = _origin_entry_cache(origin=1, peer_claim=2, value=1000)
    own = _own()
    for contract in ("flat_mean", "neighbor_1hop"):
        o = learning.build_observation(contract, 0, c, 6.0, topo, own)
        q = o[learning.OWN_FEATURES]
        assert q == 0.0, f"{contract} must reject a removed-direction record"


def test_graph_observation_retains_close_high_id_neighbors_over_distant_low_ids():
    """When cache origins exceed GRAPH_MAX_NODES - 1, closer/fresher origins
    must be retained rather than truncating by ascending satellite ID."""
    # 66-node ring where sat 60's 1-hop neighbors are 59 and 61, while 0..39
    # are 2-to-4-hop distant entries with low IDs.
    topo = {i: {"E": (i + 1) % 66, "W": (i - 1) % 66} for i in range(66)}
    c = control.LocalCache()
    for o in range(40):
        c.put(control.CacheEntry(
            o, {"isl_queue_bits": {"E": {"peer": (o + 1) % 66, "value": 100}},
                "access_slots_used": 1, "access_slots_cap": 4,
                "visible_cells": []},
            5.0, 5.01, 10.0, hops=3))
    for nb in (59, 61):
        c.put(control.CacheEntry(
            nb, {"isl_queue_bits": {"E": {"peer": (nb + 1) % 66, "value": 9999}},
                 "access_slots_used": 2, "access_slots_cap": 4,
                 "visible_cells": []},
            5.5, 5.51, 10.0, hops=1))
    o = learning.build_observation("GAT", 60, c, 6.0, topo, _own())
    n = learning.GRAPH_MAX_NODES
    feats = o[:n * learning.GRAPH_NODE_FEAT_DIM].reshape(
        n, learning.GRAPH_NODE_FEAT_DIM)
    # 1-hop neighbor slots must appear (hop_norm == 1/4 == 0.25) and have
    # first-hop direction E or W (columns 8..11) active in the subgraph.
    one_hop_rows = [i for i in range(1, n) if np.isclose(feats[i, 4], 0.25)]
    assert len(one_hop_rows) == 2


def test_qlearning_resume_preserves_elapsed_time_for_epsilon_decay(tmp_path):
    from CODE.leo_sim import config
    resolved = config.resolve_config({
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {
            "algorithm": "qlearning", "mode": "train",
            "epsilon_start": 0.9, "epsilon_end": 0.1, "epsilon_decay_s": 5.0,
        },
    })
    cfg = resolved["config"]["learning"]
    ql = learning.TabularQLearning("C3", cfg, seed=7)
    dim = learning.CONTRACT_DIMS["C3"]
    mask = {a: True for a in learning.ACTIONS}
    ql.choose(np.zeros(dim), mask, 5.0)
    eps_at_5 = ql.epsilon(5.0)
    meta = ql.save_and_verify(tmp_path)
    resumed_cfg = dict(cfg, resume_path=str(tmp_path / "resume"),
                       resume_sha256=meta["resume_sha256"])
    resumed = learning.TabularQLearning("C3", resumed_cfg, seed=7)
    assert resumed.epsilon(0.0) == pytest.approx(eps_at_5)


@pytest.mark.skipif(importlib.util.find_spec("tensorflow") is None,
                    reason="TensorFlow compiled-inference test requires TF runtime")
def test_ddqn_fast_train_matches_eager_and_preserves_epsilon_resume(tmp_path):
    from CODE.leo_sim import config
    base = config.resolve_config({
        "scenario": {"seed": 7},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "C3"},
        "learning": {
            "algorithm": "ddqn", "mode": "train",
            "batch_size": 2, "replay_size": 4, "target_update_interval": 2,
            "epsilon_start": 0.8, "epsilon_end": 0.05, "epsilon_decay_s": 4.0,
            "fast_train": True,
        },
    })["config"]["learning"]
    eager_cfg = dict(base, fast_train=False)
    fast_net = learning.TensorflowDDQN("C3", base, seed=11)
    eager_net = learning.TensorflowDDQN("C3", eager_cfg, seed=11)
    dim = learning.CONTRACT_DIMS["C3"]
    mask = {a: True for a in learning.ACTIONS}
    rng = np.random.default_rng(1)
    for step in range(6):
        s = rng.normal(size=dim)
        ns = rng.normal(size=dim)
        a1 = fast_net.choose(s, mask, float(step))
        a2 = eager_net.choose(s, mask, float(step))
        assert a1 == a2
        fast_net.remember(s, a1, -1.0, ns, mask, False)
        eager_net.remember(s, a2, -1.0, ns, mask, False)
    assert len(fast_net.replay) == 4
    for lw, rw in zip(fast_net.online.get_weights(), eager_net.online.get_weights()):
        assert np.allclose(lw, rw, atol=1e-6)
    eps_at_5 = fast_net.epsilon(5.0)
    meta = fast_net.save_and_verify(tmp_path)
    resumed = learning.TensorflowDDQN(
        "C3", dict(base, resume_path=str(tmp_path / "resume"),
                   resume_sha256=meta["resume_sha256"]), seed=11)
    assert resumed.epsilon(0.0) == pytest.approx(eps_at_5)


@pytest.mark.skipif(importlib.util.find_spec("tensorflow") is None,
                    reason="TensorFlow MLP fast-forward test requires TF runtime")
def test_ddqn_train_every_steps_warmup_and_numpy_mlp_equivalence():
    from CODE.leo_sim import config
    cfg = config.resolve_config({
        "scenario": {"seed": 7},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "C3"},
        "learning": {
            "algorithm": "ddqn", "mode": "train",
            "batch_size": 2, "replay_size": 8, "target_update_interval": 2,
            "train_every_steps": 3, "warmup_steps": 4,
            "epsilon_start": 0.0, "epsilon_end": 0.0, "epsilon_decay_s": 4.0,
            "fast_train": True,
        },
    })["config"]["learning"]
    with pytest.raises(config.ConfigError, match="train_every_steps"):
        config.resolve_config({"learning": {"train_every_steps": 0}})
    with pytest.raises(config.ConfigError, match="warmup_steps"):
        config.resolve_config({"learning": {"warmup_steps": -1}})

    agent = learning.TensorflowDDQN("C3", cfg, seed=13)
    dim = learning.CONTRACT_DIMS["C3"]
    mask = {a: True for a in learning.ACTIONS}
    rng = np.random.default_rng(5)
    counts = []
    for step in range(10):
        s = rng.normal(size=dim).astype(np.float32)
        ns = rng.normal(size=dim).astype(np.float32)
        a = agent.choose(s, mask, float(step))
        agent.remember(s, a, -1.0, ns, mask, False)
        counts.append(agent.train_steps)
    # Transitions 1..3 < warmup_steps(4): 0 steps; transitions 4,7,10 trigger steps 1,2,3.
    assert counts == [0, 0, 0, 1, 1, 1, 2, 2, 2, 3]
    probe = rng.normal(size=(1, dim)).astype(np.float32)
    np_q = learning._numpy_mlp_forward(agent._current_np_mlp_layers(), probe)
    tf_q = agent.online(probe, training=False).numpy()
    assert np.allclose(np_q, tf_q, atol=1e-5)


def test_replay_buffer_dynamic_growth_and_wraparound():
    buf = learning._ReplayBuffer(maxlen=150, state_dim=3, n_actions=5)
    assert buf._cap == 64
    mask = np.array([True, False, True, False, False], dtype=bool)
    for i in range(70):
        buf.append((np.full(3, float(i)), i % 5, -float(i),
                    np.full(3, float(i + 1)), mask, i % 2 == 0))
    assert buf._cap == 128
    assert len(buf) == 70
    s, a, r, ns, m, d = buf.to_arrays()
    assert np.array_equal(s[:, 0], np.arange(70, dtype=np.float32))
    assert float(buf[-1][2]) == -69.0

    for i in range(70, 190):
        buf.append((np.full(3, float(i)), i % 5, -float(i),
                    np.full(3, float(i + 1)), mask, i % 2 == 0))
    assert buf._cap == 150
    assert len(buf) == 150
    s2, *_ = buf.to_arrays()
    assert np.array_equal(s2[:, 0], np.arange(40, 190, dtype=np.float32))
    assert float(buf[0][0][0]) == 40.0
    assert float(buf[-1][0][0]) == 189.0


def test_include_last_hop_encoding_for_vector_graph_and_kernel():
    assert learning.contract_dim("C3", False) == learning.CONTRACT_DIMS["C3"]
    assert learning.contract_dim("C3", True) == learning.CONTRACT_DIMS["C3"] + 4
    assert learning.contract_dim("GAT", True) == learning.CONTRACT_DIMS["GAT"] + 4

    cache = _entries([(1, 5.0, 1)])
    for c in ("C3", "GAT"):
        ingress = learning.build_observation(
            c, 0, cache, 6.0, TOPO, _own(), include_last_hop=True,
            last_hop_dir=None)
        from_south = learning.build_observation(
            c, 0, cache, 6.0, TOPO, _own(), include_last_hop=True,
            last_hop_dir="S")
        assert ingress.shape == (learning.contract_dim(c, True),)
        assert np.array_equal(ingress[-4:], [0.0, 0.0, 0.0, 0.0])
        assert np.array_equal(from_south[-4:], [0.0, 1.0, 0.0, 0.0])

    # Two-sat topology: 0 --E--> 1, 1 --W--> 0.
    from CODE.leo_sim.tests.helpers import cell_center
    ac, bc = cell_center(A), cell_center(B)

    class _TwoSatGeo(StaticGeometry):
        def subpoint(self, sat_id, t):
            return (0.5, 0.5 + float(sat_id) * 10.0, 550.0)

    geo = _TwoSatGeo(
        2,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (
            (s == 0 and (lat, lon) == ac) or (s == 1 and (lat, lon) == bc)
        ),
    )
    cfg = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 5.0},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "C3"},
        "learning": {
            "algorithm": "qlearning", "mode": "train",
            "agent_scope": "per_satellite", "include_last_hop": True,
        },
    })
    pkt = kernel.DataPacket(1, A, B, 1000, 5.0, 0.0)
    k = kernel.Kernel(cfg, [row(1, 0.0, A, B)], geometry=geo)
    pkt.path = [0]
    s0 = k._learning_observation(0, B, pkt=pkt)
    assert np.array_equal(s0[-4:], [0.0, 0.0, 0.0, 0.0])
    pkt.path = [0, 1]
    s1 = k._learning_observation(1, B, pkt=pkt)
    assert np.array_equal(s1[-4:], [0.0, 0.0, 0.0, 1.0])


def _compile_two_sat_run(tmp_path, overrides: dict):
    import hashlib
    from CODE.leo_sim import trace
    from CODE.leo_sim.tests.helpers import cell_center

    cfg = make_cfg({
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
        "demand": {"offered_mbps": 20.0, "packet_bits": 1_000_000},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        **overrides,
    })
    tdir = tmp_path / "compiled"
    manifest = trace.compile_trace(cfg, str(tdir))
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(tdir / "trace.csv"),
        horizon_s=cfg["config"]["scenario"]["duration_s"],
        max_packets=cfg["config"]["execution"]["max_packets"])
    ac, bc = cell_center(A), cell_center(B)

    class _TwoSatGeo(StaticGeometry):
        def subpoint(self, sat_id, t):
            return (0.5, 0.5 + float(sat_id) * 10.0, 550.0)

    geo = _TwoSatGeo(
        2,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s_id, lat, lon, t: (
            (s_id == 0 and (lat, lon) == ac)
            or (s_id == 1 and (lat, lon) == bc)
        ),
    )
    return cfg, tbytes, manifest, rows, geo


def test_per_satellite_qlearning_isolation_and_verified_receipt(tmp_path):
    cfg, tbytes, manifest, rows, geo = _compile_two_sat_run(tmp_path, {
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 5.0,
                     "seed": 7},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "C3"},
        "learning": {
            "algorithm": "qlearning", "mode": "train",
            "agent_scope": "per_satellite", "include_last_hop": True,
            "epsilon_start": 0.2, "epsilon_end": 0.05, "epsilon_decay_s": 2.0,
        },
    })
    agent = learning.PerSatelliteTabularQLearning(
        "C3", cfg["config"]["learning"], seed=7)
    dim = learning.contract_dim("C3", True)
    s = np.ones(dim, dtype=np.float64)
    ns = np.zeros(dim, dtype=np.float64)
    mask = {"deliver": False, "N": False, "S": False, "E": True, "W": False}
    a0 = agent.choose_for(0, s, mask, 0.0)
    agent.remember_for(0, s, a0, -5.0, ns, mask, True)
    assert 0 in agent.tables and 1 not in agent.tables
    assert len(agent.tables[0]) == 1

    run_dir = tmp_path / "q_persat_run"
    res = kernel.run_simulation(
        cfg, rows, geometry=geo, learning_out_dir=run_dir / "qlearning")
    assert res["natural_end"] is True
    assert res["learning"]["agent_scope"] == "per_satellite"
    assert res["learning"]["include_last_hop"] is True
    assert res["learning"]["n_agents"] == 2
    assert res["learning"]["table_size"] >= 2
    assert (run_dir / "qlearning" / "per_satellite_q_tables.json").is_file()
    receipt.write_run(str(run_dir), cfg, tbytes, manifest, res, rows)
    assert receipt.verify_receipt_dir(str(run_dir)) == []


@pytest.mark.skipif(importlib.util.find_spec("tensorflow") is None,
                    reason="TensorFlow wave2 DDQN tests require TF runtime")
def test_ddqn_clip_norm_and_include_last_hop_fixed_inference(tmp_path):
    from CODE.leo_sim import inference
    train_cfg, _tb, _mf, rows, geo = _compile_two_sat_run(tmp_path / "t", {
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 5.0,
                     "seed": 9},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "C3"},
        "learning": {
            "algorithm": "ddqn", "mode": "train",
            "batch_size": 2, "replay_size": 8, "target_update_interval": 2,
            "clip_norm": 0.5, "include_last_hop": True, "fast_train": True,
        },
    })
    ckpt_dir = tmp_path / "ckpt"
    res = kernel.run_simulation(
        train_cfg, rows, geometry=geo, learning_out_dir=ckpt_dir)
    lr = res["learning"]
    assert lr["clip_norm"] == 0.5
    assert lr["include_last_hop"] is True
    assert lr["feature_order"] == inference.OBSERVATION_FEATURE_ORDER_LAST_HOP
    assert lr["feature_width"] == learning.contract_dim("C3", True)
    assert lr["train_steps"] >= 1

    bad_cfg = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 2.0,
                     "seed": 9},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "C3"},
        "learning": {
            "algorithm": "ddqn", "mode": "eval", "fixed_inference": True,
            "include_last_hop": False,
            "checkpoint_path": str(ckpt_dir / "online.keras"),
            "checkpoint_sha256": lr["checkpoint_sha256"],
            "checkpoint_metadata_sha256": lr["metadata_sha256"],
        },
    })
    with pytest.raises(inference.FixedInferenceError):
        kernel.Kernel(bad_cfg, [row(1, 0.0, A, B)], geometry=geo)

    good_cfg = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 3.0,
                     "seed": 9},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "C3"},
        "learning": {
            "algorithm": "ddqn", "mode": "eval", "fixed_inference": True,
            "include_last_hop": True,
            "checkpoint_path": str(ckpt_dir / "online.keras"),
            "checkpoint_sha256": lr["checkpoint_sha256"],
            "checkpoint_metadata_sha256": lr["metadata_sha256"],
        },
    })
    eval_res = kernel.run_simulation(good_cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert eval_res["fates"][1] == "DELIVERED"


@pytest.mark.skipif(importlib.util.find_spec("tensorflow") is None,
                    reason="TensorFlow per-satellite DDQN tests require TF runtime")
def test_per_satellite_ddqn_isolation_and_verified_receipt(tmp_path):
    from CODE.leo_sim import config
    mask = {"deliver": False, "N": False, "S": True, "E": True, "W": False}
    for contract in ("C3", "GAT"):
        cfg = config.resolve_config({
            "scenario": {"seed": 7},
            "control_plane": {"enabled": True},
            "routing": {"policy": "hop", "learning_enabled": True,
                        "contract": contract},
            "learning": {
                "algorithm": "ddqn", "mode": "train",
                "agent_scope": "per_satellite",
                "clip_norm": 1.0,
                "include_last_hop": True,
                "batch_size": 2, "replay_size": 8, "target_update_interval": 2,
                "epsilon_start": 0.0, "epsilon_end": 0.0, "epsilon_decay_s": 4.0,
                "fast_train": True,
            },
        })["config"]["learning"]
        ps = learning.PerSatelliteTensorflowDDQN(contract, cfg, seed=17)
        dim = learning.contract_dim(contract, True)
        rng = np.random.default_rng(3)
        s0_init = [w.copy() for w in ps._agent(0)["online_w"]]
        s1_init = [w.copy() for w in ps._agent(1)["online_w"]]
        assert not all(np.array_equal(a, b) for a, b in zip(s0_init, s1_init))
        for step in range(4):
            s = rng.normal(size=dim).astype(np.float32)
            ns = rng.normal(size=dim).astype(np.float32)
            a = ps.choose_for(0, s, mask, float(step))
            ps.remember_for(0, s, a, -1.0, ns, mask, False)
        assert ps.agents[0]["train_steps"] == 3
        assert ps.agents[1]["train_steps"] == 0
        for a, b in zip(s1_init, ps.agents[1]["online_w"]):
            assert np.array_equal(a, b)
        assert int(ps.agents[1]["opt_w"][0]) == 0
        assert int(ps.agents[0]["opt_w"][0]) == 3
        assert any(not np.array_equal(a, b)
                   for a, b in zip(s0_init, ps.agents[0]["online_w"]))

    run_cfg, tbytes, manifest, rows, geo = _compile_two_sat_run(tmp_path / "p", {
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 5.0,
                     "seed": 19},
        "routing": {"policy": "hop", "learning_enabled": True, "contract": "GAT"},
        "learning": {
            "algorithm": "ddqn", "mode": "train",
            "agent_scope": "per_satellite",
            "clip_norm": 1.0,
            "include_last_hop": True,
            "batch_size": 2, "replay_size": 8, "target_update_interval": 2,
            "fast_train": True,
        },
    })
    run_dir = tmp_path / "ddqn_persat_run"
    res = kernel.run_simulation(
        run_cfg, rows, geometry=geo, learning_out_dir=run_dir / "ddqn")
    assert res["natural_end"] is True
    assert res["learning"]["agent_scope"] == "per_satellite"
    assert res["learning"]["n_agents"] == 2
    assert res["learning"]["train_steps"] >= 5
    assert res["mechanisms"]["effective"]["learning"] is True
    assert res["learning"]["per_satellite_verified"] is True
    assert (run_dir / "ddqn" / "per_satellite_weights.npz").is_file()
    receipt.write_run(str(run_dir), run_cfg, tbytes, manifest, res, rows)
    assert receipt.verify_receipt_dir(str(run_dir)) == []
