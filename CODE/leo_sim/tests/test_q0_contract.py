import pytest

from CODE.leo_sim import kernel
from CODE.leo_sim.q0 import JointPlan, PlanAction, PlanError, validate_plan_version
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row


def test_plan_is_immutable_and_version_bound():
    action = PlanAction("forward", packet_id=1, sat=0, direction="E")
    plan = JointPlan(version=4, actions=(action,))
    assert validate_plan_version(plan, 4) == (True, ())
    assert validate_plan_version(plan, 5)[0] is False
    with pytest.raises((AttributeError, TypeError)):
        plan.version = 5


def test_plan_rejects_unknown_or_malformed_actions():
    with pytest.raises(PlanError):
        PlanAction("teleport", packet_id=1, sat=0)  # type: ignore[arg-type]
    with pytest.raises(PlanError):
        PlanAction("forward", packet_id=1, sat=0, direction="X")
    with pytest.raises(PlanError):
        PlanAction("wait", packet_id=1, sat=0, until=None)


def test_plan_rejects_duplicate_packets_and_mutable_actions():
    a = PlanAction("deliver", packet_id=1, sat=0)
    with pytest.raises(PlanError):
        JointPlan(version=0, actions=(a, a))
    with pytest.raises(PlanError):
        JointPlan(version=0, actions=[a])  # type: ignore[arg-type]


def test_kernel_plan_validation_is_versioned_and_fail_closed():
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}})
    cfg = make_cfg({"scenario": {"num_satellites": 2, "num_planes": 1}})
    src, dst = cell(0.0, 0.0), cell(0.0, 10.0)
    k = kernel.Kernel(cfg, [row(1, 0.0, src, dst)], geometry=geo)
    pkt = kernel.DataPacket(1, src, dst, 8_000, None, 0.0)
    k.pending[0].append(pkt)
    valid = JointPlan(0, (PlanAction("forward", 1, 0, direction="E"),))
    assert k.validate_joint_plan(valid) == (True, ())
    stale = JointPlan(1, valid.actions)
    assert k.validate_joint_plan(stale)[0] is False
    bad_dir = JointPlan(0, (PlanAction("forward", 1, 0, direction="N"),))
    assert k.validate_joint_plan(bad_dir)[0] is False
    k._in_flight[1] = {"kind": "isl", "sat": 1, "arrival_at": 1.0, "pkt": pkt}
    assert k.validate_joint_plan(valid)[0] is False


def test_kernel_applies_valid_plan_atomically_and_records_audit():
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}})
    cfg = make_cfg({"scenario": {"num_satellites": 2, "num_planes": 1}})
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, cell(0.0, 0.0), cell(0.0, 10.0), 8_000, None, 0.0)
    k.pending[0].append(pkt)
    plan = JointPlan(0, (PlanAction("forward", 1, 0, direction="E"),))
    assert k.apply_joint_plan(plan) == (True, ())
    assert k.pending[0] == []
    assert [p.pid for p in k.isls[0]["E"].data_q] == [1]
    assert k._state_version == 1
    assert k.q0_plan_audit == [{"version": 0, "applied_at": 0.0, "actions": 1}]


def test_kernel_rejects_mixed_plan_without_partial_mutation():
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}})
    cfg = make_cfg({"scenario": {"num_satellites": 2, "num_planes": 1}})
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, cell(0.0, 0.0), cell(0.0, 10.0), 8_000, None, 0.0)
    k.pending[0].append(pkt)
    mixed = JointPlan(0, (
        PlanAction("forward", 1, 0, direction="E"),
        PlanAction("forward", 2, 0, direction="E"),
    ))
    ok, errors = k.apply_joint_plan(mixed)
    assert not ok and errors
    assert [p.pid for p in k.pending[0]] == [1]
    assert not k.isls[0]["E"].data_q
    assert k._state_version == 0


def test_kernel_rejects_wait_on_wrong_satellite_without_mutation():
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}})
    cfg = make_cfg({"scenario": {"num_satellites": 2, "num_planes": 1}})
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, cell(0.0, 0.0), cell(0.0, 10.0), 8_000, None, 0.0)
    k.pending[0].append(pkt)
    plan = JointPlan(0, (PlanAction("wait", 1, 1, until=1.0),))

    ok, errors = k.apply_joint_plan(plan)

    assert not ok and any("wrong pending satellite" in e for e in errors)
    assert [p.pid for p in k.pending[0]] == [1]
    assert k.ledger.fate_of(1) is None


def test_kernel_wait_is_atomic_when_holding_queue_is_full():
    geo = StaticGeometry(1, neighbors_map={0: {}})
    cfg = make_cfg({
        "scenario": {"num_satellites": 1},
        "access": {"holding_queue_bits": 8_000},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, cell(0.0, 0.0), cell(0.0, 10.0), 8_000, None, 0.0)
    k.pending[0].append(pkt)
    plan = JointPlan(0, (PlanAction("wait", 1, 0, until=1.0),))

    ok, errors = k.apply_joint_plan(plan)

    assert ok, errors
    assert [p.pid for p in k.pending[0]] == [1]
    assert k.pending[0].queued_bits == 8_000
    assert k.pending[0][0].holding_until == 1.0
