"""Contract tests for C1/C3-C7 observations, action masks, canonical DDQN."""
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


def test_c3_to_c7_share_exactly_the_same_information_set():
    cache = _entries([(1, 5.0, 1), (2, 5.0, 2), (3, 5.0, 2)])
    sets = {c: set(learning.information_set(c, 0, cache, 6.0, TOPO))
            for c in ("C3", "C4", "C5", "C6", "C7")}
    assert len({frozenset(s) for s in sets.values()}) == 1
    assert sets["C3"] == {1, 2, 3}


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


def test_c6_buckets_by_actual_hop_count():
    # distinct access loads make the two hop buckets identifiable by value
    c = control.LocalCache()
    c.put(control.CacheEntry(
        1, {"isl_queue_bits": {}, "access_slots_used": 1,
            "access_slots_cap": 4, "visible_cells": []},
        5.0, 5.01, 10.0, hops=1))
    c.put(control.CacheEntry(
        2, {"isl_queue_bits": {}, "access_slots_used": 3,
            "access_slots_cap": 4, "visible_cells": []},
        5.0, 5.01, 10.0, hops=2))
    o = learning.build_observation("C6", 0, c, 6.0, TOPO, _own())
    # hop buckets start after the own-state block, not at offset 4 (the
    # OWN_FEATURES=7 block would be sliced mid-field at offset 4)
    base = learning.OWN_FEATURES
    h1 = o[base:base + learning.ORIGIN_FEATURES]
    h2 = o[base + learning.ORIGIN_FEATURES:base + 2 * learning.ORIGIN_FEATURES]
    # ORIGIN_FEATURES layout: [queue_ratio, access_load, n_visible, aoi]
    assert h1[1] == pytest.approx(0.25)   # 1-hop bucket: origin 1 (used 1/4)
    assert h2[1] == pytest.approx(0.75)   # 2-hop bucket: origin 2 (used 3/4)
    assert np.array_equal(
        o[base + 2 * learning.ORIGIN_FEATURES:
          base + 4 * learning.ORIGIN_FEATURES],
        np.zeros(2 * learning.ORIGIN_FEATURES))  # buckets 3-4 empty


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


def test_c5_c7_validate_queue_bits_against_advertisement_origin():
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
    for contract in ("C5", "C7"):
        o = learning.build_observation(contract, 0, c, 6.0, topo, own)
        q = o[learning.OWN_FEATURES]  # queue ratio is the 1st origin feature
        assert q > 0.0, f"{contract} must accept a peer-matching entry"
        assert o.shape == (learning.CONTRACT_DIMS[contract],), contract


def test_c5_c7_reject_queue_bits_matching_only_root_edge():
    """A record whose peer matches only the root satellite's edge (not the
    origin's) must read as 0: it is stale for the real advertisement origin."""
    # root 0 has E->2, but the entry from origin 1 measured E on peer 3
    # (topo[1]["E"] == 3): the claimed peer 2 is wrong for origin 1 even
    # though it happens to match root 0's own edge.
    topo = {0: {"E": 2, "N": 1}, 1: {"W": 0, "E": 3}, 2: {"W": 0},
            3: {"W": 0}}
    c = _origin_entry_cache(origin=1, peer_claim=2, value=1000)
    own = _own()
    for contract in ("C5", "C7"):
        o = learning.build_observation(contract, 0, c, 6.0, topo, own)
        q = o[learning.OWN_FEATURES]
        assert q == 0.0, f"{contract} must reject a stale-peer record"
def test_c5_c7_reject_queue_bits_after_direction_removed():
    """A record whose origin no longer has the advertised direction at all
    (a rematch deleted it, so topo[origin] has no such key) must read as 0:
    the stale metric must not be accepted just because no peer is left to
    mismatch against."""
    # origin 1 advertised E->{peer 2, value 1000}; after the rematch topo[1]
    # has no "E" direction at all (peer=None)
    topo = {0: {"E": 2, "N": 1}, 1: {"W": 0}, 2: {"W": 0}, 3: {"W": 0}}
    c = _origin_entry_cache(origin=1, peer_claim=2, value=1000)
    own = _own()
    for contract in ("C5", "C7"):
        o = learning.build_observation(contract, 0, c, 6.0, topo, own)
        q = o[learning.OWN_FEATURES]
        assert q == 0.0, f"{contract} must reject a removed-direction record"
