"""Regression tests for the 2026-08-16 learning-semantics fixes.

Each test names the defect it pins down; every one of them FAILS on the
pre-fix implementation (branch base def4b26):

1. deliver arrival reward was booked at decision time, so a hard-retired
   downlink re-decision collected arrive_reward without ever delivering;
2. learning transitions still open at the horizon were silently dropped
   (decisions - transitions was never accounted for);
3. the DDQN fast/eager train path was steered by the LEO_FAST_TRAIN
   environment variable instead of the resolved config, and DDQN receipts
   did not pin the TensorFlow build;
4. the GAT/MPNN root node position came from a control-cache self-entry
   that can never exist (own advertisements are refused), so relative
   positions degraded to drifting absolute coordinates;
5. graph node features never carried access load / visible-cell count / AoI
   although they were computed.
"""
from __future__ import annotations

import importlib.util
import json

import numpy as np
import pytest

from CODE.leo_sim import config, control, kernel, learning, receipt
from CODE.leo_sim.__main__ import main
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)
LINE = {0: {"E": 1}, 1: {"W": 0}}


class _StubLearner:
    """Greedy first-legal learner recording every remembered transition
    (same pattern as test_reward_migration._StubLearner; no TF needed)."""

    def __init__(self):
        self.mode = "eval"
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self.records = []  # (action, reward, done)

    def choose(self, state, mask, now):
        self.decisions += 1
        if mask.get("deliver"):
            return "deliver"
        for a in ("N", "S", "E", "W"):
            if mask.get(a):
                return a
        raise AssertionError(f"no legal action in mask {mask}")

    def remember(self, state, action, reward, next_state, next_mask, done):
        assert reward is not None, "transition closed with unrealized reward"
        self.transitions += 1
        self.records.append((action, float(reward), bool(done)))

    def diagnostics(self):
        return {"stub": True}


class _RogueLearner(_StubLearner):
    """Returns an action outside the legal mask: the kernel must fail loud
    instead of silently overflowing an ISL queue (put_data does not
    re-check room())."""

    def choose(self, state, mask, now):
        self.decisions += 1
        return "deliver" if mask.get("deliver") else "N"


# ------------------------------------------- 1. arrival reward at real delivery

class _HandoverGeometry(StaticGeometry):
    def subpoint(self, sat_id, t):
        return (0.0, float(sat_id), 550.0)


def _mbb_retire_kernel():
    """sat0 serves src A and dst B; at t=2 B's association switches to sat1
    under MBB with a 0.5 s retirement deadline, hard-retiring the 8 s
    downlink service mid-flight. The packet bounces back to pending at sat0,
    is re-decided, forwards E and is finally delivered by sat1."""
    def elev(s, lat, lon, t):
        if (lat, lon) == AC:
            return 90.0 if s == 0 else -10.0
        if s == 0:
            return 80.0 if t < 2.0 else 60.0  # stays visible: no geom loss
        return 20.0 if t < 2.0 else 80.0

    vis = lambda s, lat, lon, t: elev(s, lat, lon, t) >= 25.0
    geo = _HandoverGeometry(2, neighbors_map=LINE, visible=vis,
                            elevation=elev, gsl_changes=[2.0])
    cfg = make_cfg({
        "scenario": {"duration_s": 30.0},
        "access": {"association": "mbb", "dual_connect": True,
                   "uplink_rate_mbps": 4000.0, "downlink_rate_mbps": 1.0,
                   "retirement_deadline_s": 0.5},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    k = kernel.Kernel(cfg, [row(1, 0.0, A, B)], geometry=geo)
    k.learner = _StubLearner()
    return k


def test_retired_deliver_never_collects_arrive_reward():
    k = _mbb_retire_kernel()
    result = k.run()
    assert result["natural_end"]
    assert result["fates"][1] == "DELIVERED"
    # sanity: the downlink really was hard-retired mid-service
    assert any(e["type"] == "release" and e["reason"] == "mbb_retire_deadline"
               for e in result["handover"]["events"])
    records = k.learner.records
    actions = [r[0] for r in records]
    assert actions == ["deliver", "E", "deliver"]
    # the retired deliver settles at 0 on re-decision (not delivered)
    assert records[0] == ("deliver", 0.0, False)
    # the forward hop settles raw M1 plus the safe -W1 step cost (~zero wait)
    assert records[1][0] == "E" and not records[1][2]
    assert records[1][1] == pytest.approx(0.0, rel=1e-9)
    # only the actual delivery collects the arrival reward, done=True
    assert records[2][0] == "deliver" and records[2][2]
    assert records[2][1] == pytest.approx(50.0)
    # no path may book the arrival reward without done=True
    assert not [r for r in records
                if r[0] == "deliver" and r[1] == pytest.approx(50.0)
                and not r[2]]


# ----------------------------- 2. open transitions accounted for at the horizon

def test_open_learning_transitions_are_discarded_visibly_at_stop():
    """Two packets share one slow ISL (8 s service, horizon 1 s): at the stop
    time packet A is mid-service and packet B is still queued, both holding
    open forward transitions. They must be explicitly discarded and counted,
    not silently lost."""
    # distinct-latitude cells (test_reward_migration convention): sat0 sees
    # only SRC, sat1 sees only DST, so every packet must cross the ISL
    src, dst = cell(31.0, 121.0), cell(40.0, 116.0)
    # lazy endpoint activation: the destination cell is only advertised after
    # it becomes active (first routed packet creates it), so the first control
    # snapshot cannot contain it.  horizon must therefore be long enough for a
    # post-activation advertisement (advertise_interval_s=2) to arrive and
    # the packets to be decided forward, while staying inside the 8 s ISL
    # service so both transitions remain open at the stop.
    geo = _HandoverGeometry(
        2, neighbors_map=LINE,
        visible=lambda s, lat, lon, t: (s == 0 and abs(lat - 31.0) < 1.0
                                        ) or (s == 1 and abs(lat - 40.0) < 1.0))
    cfg = make_cfg({
        "scenario": {"duration_s": 3.0},
        "access": {"uplink_rate_mbps": 4000.0},
        "links": {"isl_rate_mbps": 1.0},  # 8 s per 8 Mbit packet
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    rows = [row(1, 0.0, src, dst), row(2, 0.0, src, dst)]
    result = kernel.run_simulation(cfg, rows, geometry=geo)
    assert result["natural_end"]
    assert [result["fates"][p] for p in (1, 2)] == ["IN_SYSTEM_AT_STOP"] * 2
    mc = result["mechanism_counters"]
    # both packets decided forward exactly once; neither transition closed
    assert mc["learning_decisions"] == 2
    assert mc["learning_transitions"] == 0
    assert mc["learning_discarded_at_stop"] == 2
    # the accounting identity a receipt can check
    assert mc["learning_decisions"] == (
        mc["learning_transitions"] + mc["learning_discarded_at_stop"])


# ------------------------------- 3. training path bound to config, TF pinned

def test_fast_train_is_config_bound_not_env(monkeypatch):
    # the legacy environment escape hatch must not steer anything anymore
    monkeypatch.setenv("LEO_FAST_TRAIN", "0")
    base = config.resolve_config({})
    assert base["config"]["learning"]["fast_train"] is True
    off = config.resolve_config({"learning": {"fast_train": False}})
    assert off["config"]["learning"]["fast_train"] is False
    # the field participates in the config SHA identity
    assert off["sha256"] != base["sha256"]
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"fast_train": "yes"}})


def test_dependency_versions_tensorflow_rules():
    deps = receipt.dependency_versions()
    assert set(deps) == receipt.DEP_KEYS
    if importlib.util.find_spec("tensorflow") is None:
        # fail closed: a DDQN dependency pin cannot be produced or verified
        # on a TF-less host
        with pytest.raises(ImportError):
            receipt.dependency_versions(with_tensorflow=True)
    else:
        deps_tf = receipt.dependency_versions(with_tensorflow=True)
        assert set(deps_tf) == receipt.DEP_KEYS | {"tensorflow"}


def _run_dir(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "scenario:\n  duration_s: 2.0\n  num_satellites: 1\n  num_planes: 1\n"
        "endpoints:\n  sites:\n"
        "    - {name: a, lat: 0.1, lon: 0.1}\n"
        "    - {name: b, lat: 2.0, lon: 3.0}\n"
        "demand:\n  mode: uniform\n  offered_mbps: 4.0\n  packet_bits: 1000000\n"
        "routing:\n  policy: oracle\ncontrol_plane:\n  enabled: false\n",
        encoding="utf-8")
    out = tmp_path / "out"
    assert main(["run", "--config", str(cfg), "--out", str(out)]) == 0
    return out


def test_non_ddqn_receipt_rejects_tensorflow_dep_key(tmp_path):
    out = _run_dir(tmp_path)
    assert receipt.verify_receipt_dir(str(out)) == []
    rp = out / "receipt.json"
    r = json.loads(rp.read_text(encoding="utf-8"))
    r["deps"]["tensorflow"] = "0.0.0-fabricated"
    rp.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    assert receipt.verify_receipt_dir(str(out)), \
        "a tensorflow dep pin on a non-DDQN receipt must fail verification"


# ------------------------------------- 4. graph root position from geometry

class _PosGeometry(StaticGeometry):
    """Scripted ECEF positions: sat0 at z=3000 km, sat1 at z=3100 km."""

    def positions(self, t):
        return ((1000.0, 2000.0, 3000.0), (1000.0, 2000.0, 3100.0))

    def subpoint(self, sat_id, t):
        return (0.0, float(sat_id), 550.0)


def test_graph_root_position_comes_from_geometry_not_cache():
    geo = _PosGeometry(2, neighbors_map=LINE,
                       visible=lambda s, lat, lon, t: True)
    cfg = make_cfg({"routing": {"contract": "GAT"}})
    k = kernel.Kernel(cfg, [row(1, 0.0, A, B)], geometry=geo)
    # a neighbor advertisement carrying its ECEF payload position
    k.caches[0].put(control.CacheEntry(
        1, {"isl_queue_bits": {}, "access_slots_used": 1,
            "access_slots_cap": 4, "visible_cells": [B],
            "position": (1000.0, 2000.0, 3100.0)},
        0.0, 0.0, 100.0, hops=1))
    obs = k._learning_observation(0, B)
    n, d = learning.GRAPH_MAX_NODES, learning.GRAPH_NODE_FEAT_DIM
    feats = obs[:n * d].reshape(n, d)
    # the root's relative position is exactly zero because root_pos is its
    # TRUE geometry position (pre-fix: the cache self-entry never exists and
    # root_pos silently fell back to (0, 0, 0))
    assert np.array_equal(feats[0, 12:15], np.zeros(3))
    # sat1 sits 100 km above the root: relative position is payload minus
    # root geometry, scaled by 7000 km
    assert np.allclose(feats[1, 12:15], np.array([0.0, 0.0, 100.0]) / 7000.0)


# -------------------- 5. graph node features carry load / visibility / AoI

def test_graph_node_features_carry_access_load_visibility_aoi():
    assert learning.GRAPH_NODE_FEAT_DIM == 18  # 15-dim layout widened by 3
    payload = {"isl_queue_bits": {"E": 1000}, "access_slots_used": 2,
               "access_slots_cap": 4,
               "visible_cells": [A, B, cell(10.0, 0.0), cell(10.0, 10.0),
                                 cell(20.0, 20.0)],
               "position": (0.0, 0.0, 7000.0)}
    cache = control.LocalCache()
    cache.put(control.CacheEntry(1, payload, 4.0, 4.01, 10.0, hops=2))
    own = learning.own_state(1, 4, {"E": 1000}, 256_000_000, 2, 10)
    topo = {0: {"E": 1, "N": 2}, 1: {"W": 0}, 2: {"S": 0}, 3: {"W": 9}}
    for contract in learning.GRAPH_CONTRACTS:
        o = learning.build_observation(contract, 0, cache, 6.0, topo, own,
                                       root_pos=(0.0, 0.0, 0.0))
        n, d = learning.GRAPH_MAX_NODES, learning.GRAPH_NODE_FEAT_DIM
        feats = o[:n * d].reshape(n, d)
        # node 1 is origin 1 (nodes = [root] + sorted origins); AoI runs
        # from generation time (CacheEntry.aoi)
        assert feats[1, 15] == pytest.approx(2 / 4)          # access load
        assert feats[1, 16] == pytest.approx(5 / 10)         # visible cells
        assert feats[1, 17] == pytest.approx((6.0 - 4.0) / 10.0)  # AoI/TTL
        # the root row reads 0: its own fresh state lives in the own-state
        # tail, which these payload-derived fields must not duplicate
        assert np.array_equal(feats[0, 15:18], np.zeros(3))
        assert feats[0, 7] == 1.0  # root is still a valid node


# ---------------------------------------- 修复 C：学习动作空间不被启发式预裁剪
class _MaskRecordingLearner:
    """Greedy-first-legal learner that records every mask it was offered."""

    def __init__(self):
        self.mode = "eval"
        self.decisions = 0
        self.transitions = 0
        self.train_steps = 0
        self.masks = []

    def choose(self, state, mask, now):
        self.decisions += 1
        self.masks.append(dict(mask))
        if mask.get("deliver"):
            return "deliver"
        for a in ("N", "S", "E", "W"):
            if mask.get(a):
                return a
        raise AssertionError(f"no legal action in mask {mask}")

    def remember(self, state, action, reward, next_state, next_mask, done):
        self.transitions += 1

    def diagnostics(self):
        return {"stub": True}


class _FourSatGeometry(StaticGeometry):
    def subpoint(self, sat_id, t):
        return (0.0, float(sat_id), 550.0)

    def positions(self, t):
        return tuple((float(i), 0.0, 550.0) for i in range(self.num_satellites))


@pytest.mark.parametrize(
    ("contract", "obs_hops", "hidden_origin"),
    [("C3", 1, 2), ("C1", None, 1)],
)
def test_learning_routing_cannot_use_cache_beyond_obs_hops(
        contract, obs_hops, hidden_origin):
    """R1-A2: the action/decision gate is part of the learner's information.

    A two-hop destination advertisement is intentionally cropped by
    ``obs_hops=1``.  Adding that hidden entry leaves the observation bytewise
    unchanged, so it must not make a forwarding decision/action available.
    """
    topo = {
        0: {"E": 1},
        1: {"W": 0, "E": 2},
        2: {"W": 1},
    }
    geo = _FourSatGeometry(3, neighbors_map=topo)
    cfg = make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1},
        "control_plane": {"enabled": True, "vis_k": 2},
        "routing": {"policy": "hop", "learning_enabled": True,
                    "contract": contract},
        "learning": {"algorithm": "qlearning", "obs_hops": obs_hops},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    k._ensure_endpoint(B)
    visible_obs = k._learning_observation(0, B)
    k.caches[0].put(control.CacheEntry(
        hidden_origin,
        {"serve_cells": [B], "isl_queue_bits": {},
         "isl_propagation_s": {}},
        generated_at=0.0, received_at=0.0, ttl_s=10.0, hops=2,
    ))
    hidden_obs = k._learning_observation(0, B)
    assert np.array_equal(visible_obs, hidden_obs), (
        "the hidden entry unexpectedly entered the learning observation")

    learner = _MaskRecordingLearner()
    k.learner = learner
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pkt.path.append(0)
    k.ledger.register(pkt.pid, pkt.bits)
    k._decide(pkt, 0)

    assert learner.decisions == 0, (
        "a cache entry hidden from the observation opened a learning action")
    assert list(k.pending[0]) == [pkt]


def test_learning_action_space_not_preclipped_to_heuristic_best():
    """DDQN must be able to pick ANY locally legal direction, not only the
    heuristic-best one. Regression: kernel passed best_only=True for learning
    runs, so a non-optimal-but-legal direction never reached the mask."""
    A, B = cell(31.0, 121.0), cell(40.0, 116.0)
    # sat0 -> E:1 (1 hop to B); sat0 -> W:2 -> E:3 (2 hops to B): E is the
    # heuristic-best direction, W is legal but worse per the heuristic
    geo = _FourSatGeometry(
        4,
        neighbors_map={0: {"E": 1, "W": 2}, 1: {"W": 0},
                       2: {"E": 3, "W": 0}, 3: {"W": 2}},
        visible=lambda s, lat, lon, t: (
            (s == 0 and abs(lat - 31.0) < 1.0)
            or (s in (1, 3) and abs(lat - 40.0) < 1.0)),
    )
    cfg = make_cfg({
        "scenario": {"duration_s": 10.0, "num_satellites": 4, "num_planes": 1},
        "control_plane": {"enabled": True, "vis_k": 4, "ttl_s": 20.0,
                          "advertise_interval_s": 1.0},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    k = kernel.Kernel(cfg, [row(1, 0.0, A, B)], geometry=geo)
    learner = _MaskRecordingLearner()
    k.learner = learner
    result = k.run()
    assert result["natural_end"]
    assert result["fates"][1] == "DELIVERED"
    forward_masks = [m for m in learner.masks if not m.get("deliver")]
    assert forward_masks, "packet must have made a forward decision"
    # both E (heuristic best) and W (legal, longer) must be selectable
    last_forward = forward_masks[-1]
    assert last_forward.get("E") is True
    assert last_forward.get("W") is True, (
        "learning action set was pre-clipped to the heuristic best direction")


def test_decision_snapshot_policy_label_uses_algorithm():
    """Decision snapshots must record the REAL learning algorithm
    (qlearning), not hard-code ddqn — otherwise Q-learning audit trails are
    mislabeled (regression for the label fix)."""
    A, B = cell(31.0, 121.0), cell(40.0, 116.0)
    geo = _FourSatGeometry(
        2,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (
            (s == 0 and abs(lat - 31.0) < 1.0)
            or (s == 1 and abs(lat - 40.0) < 1.0)),
    )
    cfg = make_cfg({
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    sink = []
    k = kernel.Kernel(cfg, [row(1, 0.0, A, B)], geometry=geo,
                      decision_sink=sink)
    k.learner = _MaskRecordingLearner()
    result = k.run()
    assert result["natural_end"]
    forward = [d for d in sink if d["kind"] == "forward"]
    assert forward, "expected at least one forward decision in the sink"
    assert all(d["policy"].startswith("qlearning:") for d in forward), (
        [d["policy"] for d in forward])


def test_rogue_learner_out_of_mask_action_fails_loud():
    """A learner returning an action outside the legal mask must raise
    KernelError (like the deliver-only branch) instead of silently
    overflowing an ISL queue."""
    cfg = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 2.0},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    geo = _HandoverGeometry(
        2, neighbors_map=LINE,
        visible=lambda s, lat, lon, t: (s == 0 and abs(lat - 31.0) < 1.0
                                        ) or (s == 1 and abs(lat - 40.0) < 1.0))
    k = kernel.Kernel(
        cfg, [row(1, 0.0, cell(31.0, 121.0), cell(40.0, 116.0))], geometry=geo)
    k.learner = _RogueLearner()
    with pytest.raises(kernel.KernelError):
        k.run()
