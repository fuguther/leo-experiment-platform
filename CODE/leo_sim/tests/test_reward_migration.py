"""Reward/observation migration-alignment tests (M1 queue reward, M2 own
out-queue observation).

Anchor: ANALYSIS/LEO-V2-ORIGINAL-PLAN.md line 86 — "M1 的正确队列奖励和 M2
的本地出向队列观测吸收为统一基线；删除开关". The raw queue-reward
diagnostic remains equal to the legacy CORRECTED version; the training-side
forward objective also applies a non-positive per-hop cost so extra
forwarding cannot improve return before delivery:

- Raw M1 queue reward: ``w1 * exp(-beta * t)`` with w1=20, beta=200 s^-1, where t
  is the packet's realized queueing delay in seconds. Legacy source:
  ``getQueueReward`` M1 branch, SimulationRL.py:10289-10291 (constants:
  ``_M1_BETA = 200.0`` SimulationRL.py:345, ``w1 = 20`` default
  SimulationRL.py:270; ``queueTime`` = send-checkpoint minus
  receive-checkpoint, SimulationRL.py:2052).
- Terminal delivery reward: ``ArriveReward = 50`` (SimulationRL.py:579).
- M2 own out-queue observation: 4 per-direction normalized occupancies
  ``min(q_dir / cap, 1.0)``; a direction whose link is missing reads as fully
  congested (legacy ``infQueue`` clip, SimulationRL.py:9866-9875 and
  getQueues 9077-9092).

Golden values below are recomputed with math.exp from the formulas above;
the legacy module cannot be imported here (module-level TensorFlow import).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from CODE.leo_sim import kernel, learning
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row

W1 = 20.0    # legacy w1 default, SimulationRL.py:270
BETA = 200.0  # legacy _M1_BETA, SimulationRL.py:345
ARRIVE = 50.0  # legacy ArriveReward, SimulationRL.py:579
STEP = -W1     # safe objective: raw queue reward cannot make a hop profitable


# ---------------------------------------------------------- golden: M1 reward

def test_queue_reward_m1_golden_values():
    """Golden values of the legacy M1 branch, recomputed from
    w1 * exp(-beta * max(t, 0)) (SimulationRL.py:10289-10291)."""
    goldens = {
        0.0: W1,                              # empty queue -> maximum reward
        0.005: W1 * math.exp(-1.0),           # docstring calibration point
        0.01: W1 * math.exp(-2.0),
        0.1: W1 * math.exp(-20.0),
    }
    for t, expected in goldens.items():
        assert learning.queue_reward(t, W1, BETA) == pytest.approx(
            expected, rel=1e-12), f"t={t}"
    # negative queue times are clamped to 0 (legacy max(queueTime, 0.0))
    assert learning.queue_reward(-0.5, W1, BETA) == pytest.approx(W1)


def test_config_learning_reward_params_defaults_and_validation():
    from CODE.leo_sim import config
    cfg = config.resolve_config({})
    lr = cfg["config"]["learning"]
    assert lr["reward_w1"] == W1
    assert lr["reward_beta"] == BETA
    assert lr["arrive_reward"] == ARRIVE
    for bad in ({"reward_w1": 0}, {"reward_w1": -1.0}, {"reward_beta": 0.0},
                {"arrive_reward": -0.5}):
        with pytest.raises(config.ConfigError):
            config.resolve_config({"learning": bad})


# ------------------------------------------- golden: M2 own-queue observation

def test_own_state_m2_per_direction_golden():
    """Per-direction own out-queue occupancy, M2 semantics
    (SimulationRL.py:9866-9875): min(q_dir/cap, 1.0); missing direction reads
    fully congested (legacy infQueue clip, SimulationRL.py:9077-9092)."""
    cap = 256_000_000
    own = learning.own_state(
        1, 4, {"N": cap // 2, "E": cap * 2}, cap, 2, 10)
    assert own.shape == (learning.OWN_FEATURES,)
    # slots ratio
    assert own[0] == pytest.approx(0.25)
    # per-direction queues in N/S/E/W order (GRAPH_DIRS)
    assert own[1] == pytest.approx(0.5)    # N: half full
    assert own[2] == pytest.approx(1.0)    # S: no link -> congested
    assert own[3] == pytest.approx(1.0)    # E: clipped at 1.0
    assert own[4] == pytest.approx(1.0)    # W: no link -> congested
    # visible ratio + bias flag
    assert own[5] == pytest.approx(0.2)
    assert own[6] == pytest.approx(1.0)


# ------------------------------------- kernel integration: realized hop reward

class _StubLearner:
    """Minimal learner standing in for TensorflowDDQN (no TF on this host):
    greedy first-legal choice, records every remembered transition."""

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


class _TwoSatGeometry(StaticGeometry):
    def subpoint(self, sat_id, t):
        return (0.0, float(sat_id), 550.0)


def _two_sat_kernel(bits=8_000_000):
    """sat0 sees SRC, sat1 sees DST; one ISL 0 -E-> 1. Uplink 4000 Mbps,
    ISL 1600 Mbps: packet A occupies the ISL for 5 ms, packet B enqueues 2 ms
    after A's service start, so B's realized queue wait is exactly 3 ms
    (uplink prop cancels: both packets share the same src -> sat0 path)."""
    src, dst = cell(31.0, 121.0), cell(40.0, 116.0)
    geo = _TwoSatGeometry(
        2,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (s == 0 and abs(lat - 31.0) < 1.0
                                        ) or (s == 1 and abs(lat - 40.0) < 1.0),
    )
    cfg = make_cfg({
        "access": {"uplink_rate_mbps": 4000.0},
        "links": {"isl_rate_mbps": 1600.0},
    })
    rows = [row(1, 0.0, src, dst, bits=bits), row(2, 0.0, src, dst, bits=bits)]
    k = kernel.Kernel(cfg, rows, geometry=geo)
    k.learner = _StubLearner()
    return k


def test_forward_reward_is_realized_m1_queue_wait():
    k = _two_sat_kernel()
    result = k.run()
    assert result["natural_end"]
    forward = [r for r in k.learner.records if r[0] == "E"]
    deliver = [r for r in k.learner.records if r[0] == "deliver"]
    assert len(forward) == 2 and len(deliver) == 2
    # The raw M1 diagnostic is W1 at zero wait, but the learning objective
    # adds the configured -W1 step cost, so an extra hop is not profitable.
    assert forward[0][1] == pytest.approx(W1 + STEP, rel=1e-12)
    # packet B: realized wait = A's 5 ms ISL service - 2 ms uplink offset
    # = 3 ms; the safe objective is 20 * exp(-0.6) - 20.
    assert forward[1][1] == pytest.approx(W1 * math.exp(-0.6) + STEP,
                                           rel=1e-9)
    assert not forward[0][2] and not forward[1][2]


def test_forward_m1_reward_survives_mid_service_deadline_failure():
    """R4C F2 regression: a forward whose ISL service already started has
    settled its realized M1 queue reward; a later mid-service failure must
    not overwrite that realized reward with 0."""
    src, dst = cell(31.0, 121.0), cell(40.0, 116.0)
    geo = _TwoSatGeometry(
        2,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (s == 0 and abs(lat - 31.0) < 1.0
                                        ) or (s == 1 and abs(lat - 40.0) < 1.0),
    )
    cfg = make_cfg({
        "access": {"uplink_rate_mbps": 4000.0},
        "links": {"isl_rate_mbps": 1600.0},
    })
    # uplink ~2 ms + propagation ~2 ms -> ISL service starts ~4 ms and runs
    # 5 ms; a 6 ms deadline expires inside the ISL transmission, after the
    # M1 queue reward was realized at service start.
    rows = [row(1, 0.0, src, dst, bits=8_000_000, deadline=0.006)]
    k = kernel.Kernel(cfg, rows, geometry=geo)
    k.learner = _StubLearner()
    result = k.run()
    assert result["fates"][1] == "DATA_DEADLINE_EXPIRED"
    forward = [r for r in k.learner.records if r[0] == "E"]
    assert len(forward) == 1
    assert forward[0][1] == pytest.approx(W1 + STEP, rel=1e-6)
    assert forward[0][2] is True
    assert not [r for r in k.learner.records if r[0] == "deliver"]


def test_fail_preserves_realized_reward_for_any_fate():
    """Unit-level lock on _fail: a packet with an already-realized learning
    reward (ISL service started) keeps it under every terminal failure fate;
    only a packet with no realized reward settles at 0."""
    src, dst = cell(31.0, 121.0), cell(40.0, 116.0)
    geo = _TwoSatGeometry(
        2,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        visible=lambda s, lat, lon, t: (s == 0 and abs(lat - 31.0) < 1.0
                                        ) or (s == 1 and abs(lat - 40.0) < 1.0),
    )
    cfg = make_cfg({
        "access": {"uplink_rate_mbps": 4000.0},
        "links": {"isl_rate_mbps": 1600.0},
    })
    k = kernel.Kernel(cfg, [row(1, 0.0, src, dst)], geometry=geo)
    k.learner = _StubLearner()
    pkt = kernel.DataPacket(1, src, dst, 8_000_000, None, 0.0)
    pkt.learning_state = np.zeros(
        learning.CONTRACT_DIMS["C3"], dtype=np.float32)
    pkt.learning_action = "E"
    pkt.learning_reward = 12.5  # realized at ISL service start
    k.ledger.register(pkt.pid, pkt.bits)
    k._learning_open.add(pkt)
    k._fail(pkt, "GEOMETRY_LOSS_IN_FLIGHT")
    rec = [r for r in k.learner.records if r[0] == "E"]
    assert len(rec) == 1
    assert rec[0][1] == pytest.approx(12.5)
    assert rec[0][2] is True


def test_deliver_reward_is_arrive_reward():
    k = _two_sat_kernel()
    k.run()
    deliver = [r for r in k.learner.records if r[0] == "deliver"]
    assert len(deliver) == 2
    for _, reward, done in deliver:
        assert reward == pytest.approx(ARRIVE)
        assert done


def test_observation_carries_per_direction_own_queue():
    """Kernel-assembled own state must expose the M2 per-direction queues."""
    k = _two_sat_kernel()
    own = k._learning_observation(0, cell(40.0, 116.0))
    own_block = own[:learning.OWN_FEATURES]
    # sat0 has exactly one ISL direction E (empty); N/S/W read congested=1.0
    assert own_block[1] == pytest.approx(1.0)  # N missing
    assert own_block[2] == pytest.approx(1.0)  # S missing
    assert own_block[3] == pytest.approx(0.0)  # E present, empty
    assert own_block[4] == pytest.approx(1.0)  # W missing
