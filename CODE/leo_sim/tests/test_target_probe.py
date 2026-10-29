"""Same-run prediction -> truth probe: contract, truth definition, no interference."""
from __future__ import annotations

import json
import types

import simpy

from CODE.leo_sim import kernel
from CODE.leo_sim.target_probe import TargetProbe
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}


def _visible(s, lat, lon, t):
    return (((s == 0) and (lat, lon) == AC)
            or ((s == 1) and (lat, lon) == BC))


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=_visible)


def _cfg():
    return make_cfg({"scenario": {"duration_s": 12.0, "seed": 7},
                     "execution": {"decision_observation_mode": "frozen",
                                   "compute_delay_s": 0.5},
                     "access": {"idle_release_s": 100.0,
                                "slot_lease_s": 100.0},
                     "time_alignment": {"enabled": True,
                                        "arm": "candidate",
                                        "predictor": "hold_last"}})


def _rows():
    return [row(i, 0.5 * i, A, B) for i in (1, 2, 3)]


def _run(probe_path=None, max_samples=None):
    sink = []
    res = kernel.run_simulation(_cfg(), _rows(), geometry=_geo(),
                               decision_sink=sink,
                               target_probe_path=probe_path,
                               target_probe_max=max_samples)
    return res, sink


def test_probe_is_off_by_default():
    res, _sink = _run()
    assert "target_probe" not in res


def test_probe_records_one_row_per_candidate_with_a_reason_or_a_truth(tmp_path):
    path = tmp_path / "probe.jsonl"
    res, _sink = _run(str(path))
    doc = res["target_probe"]
    summary = doc["summary"]
    assert doc["rows"] > 0
    assert summary["enqueued"] == doc["rows"]
    assert summary["pending_unresolved"] == 0
    assert summary["audit_only"] is True
    assert summary["feeds_online_policy"] is False
    assert summary["consumes_business_randomness"] is False
    assert summary["same_instant_convention"]
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == doc["rows"]
    for rec in records:
        assert rec["eval_target_s"] is not None
        assert rec["decision_id"] is not None
        assert rec["direction"] in rec["candidate_directions"]
        assert rec["resource_id"] is not None
        assert rec["generation"] is not None
        if rec["truth"] is None:
            assert rec["missing_reason"]
        else:
            assert rec["truth"]["resource_id"] == rec["resource_id"]
            assert rec["truth"]["generation"] == rec["generation"]
    reasons = summary["reason_counts"]
    assert reasons, "the fixture has no advertisement, so a reason is expected"
    assert "no_prediction" in reasons


def test_probe_reads_the_kernel_own_work_ahead_definition():
    """The truth uses the SAME accessor the platform already describes a
    directed ISL resource with: queued(data+control) + in-service residual."""
    class Link:
        sat = 0
        peer = 1
        gen = 4
        retired = False
        data_bits = 1000
        ctrl_bits = 200
        _svc_phase = "transmitting"
        _tx_started_at = 1.0
        _service_rate_bps = 1000.0

        class _P:
            bits = 800.0
        current = _P()

    class Stub:
        horizon = 10.0

        def _link_rate(self, kind, now, sat, peer=None):
            return 1000.0

    stub = Stub()
    stub.env = simpy.Environment()
    stub.isls = {0: {"E": Link()}}
    stub._isl_resource_state_advertisement = types.MethodType(
        kernel.Kernel._isl_resource_state_advertisement, stub)
    probe = TargetProbe(stub, "/tmp/never-written.jsonl")
    probe.enqueue([{
        "sat": 0, "direction": "E", "resource_id": "isl:0:1",
        "resource_peer": 1, "generation": 4, "eval_target_s": 1.0,
        "predicted_bits": 1500.0, "service_rate_bps": 1000.0,
        "decision_id": 1, "pid": 1, "kind": "forward", "chosen": "E",
        "selected": True, "candidate_directions": ["E"],
    }])
    record = None
    for row_ in probe.pending:
        probe._sample_one(row_, 1.5)
        record = probe.records[-1]
    truth = record["truth"]
    # The head is 800 bits and started at t=1.0 at 1000 bit/s.  At t=1.5,
    # 500 bits have left, so 300 bits remain in service; the queue holds 1200.
    # The sum is 1500 and the head appears ONLY in the in-service part.
    assert truth["queued_bits"] == 1200
    assert truth["in_service_remaining_bits"] == 300.0
    assert truth["work_ahead_bits"] == 1500.0
    assert truth["generation"] == 4
    assert truth["source"] == "kernel_live_resource_at_target"


def test_probe_refuses_a_replaced_generation():
    class Link:
        sat = 0
        peer = 1
        gen = 5
        retired = False
        data_bits = 0
        ctrl_bits = 0
        _svc_phase = "idle"
        _tx_started_at = None
        _service_rate_bps = None
        current = None

    class Stub:
        horizon = 10.0

        def _link_rate(self, kind, now, sat, peer=None):
            return 1000.0

    stub = Stub()
    stub.env = simpy.Environment()
    stub.isls = {0: {"E": Link()}}
    stub._isl_resource_state_advertisement = types.MethodType(
        kernel.Kernel._isl_resource_state_advertisement, stub)
    probe = TargetProbe(stub, "/tmp/never-written.jsonl")
    probe.enqueue([{
        "sat": 0, "direction": "E", "resource_id": "isl:0:1",
        "resource_peer": 1, "generation": 4, "eval_target_s": 1.0,
        "predicted_bits": 1.0, "service_rate_bps": 1000.0,
        "decision_id": 1, "pid": 1, "kind": "forward", "chosen": "E",
        "selected": True, "candidate_directions": ["E"],
    }])
    probe._sample_one(probe.pending[0], 1.0)
    record = probe.records[-1]
    assert record["truth"] is None
    assert record["missing_reason"] == "generation_changed"
    assert record["generation_at_target"] == 5


def test_probe_does_not_change_the_run(tmp_path):
    base, base_sink = _run()
    probed, probe_sink = _run(str(tmp_path / "probe.jsonl"))
    assert probed["fate_counts"] == base["fate_counts"]
    assert probed["deliveries"] == base["deliveries"]
    assert probed["stop_time_s"] == base["stop_time_s"]
    assert len(probed["packet_events"]) == len(base["packet_events"])
    assert len(probe_sink) == len(base_sink)
    assert [r["chosen"] for r in probe_sink] == [r["chosen"] for r in base_sink]


def test_probe_cap_is_counted_not_silent(tmp_path):
    res, _sink = _run(str(tmp_path / "probe.jsonl"), max_samples=1)
    summary = res["target_probe"]["summary"]
    assert summary["enqueued"] <= 1
    assert summary["reason_counts"].get("overflow", 0) >= 1


def test_probe_is_deterministic(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    res_a, _ = _run(str(a))
    res_b, _ = _run(str(b))
    assert a.read_text() == b.read_text()
    assert res_a["target_probe"]["rows"] == res_b["target_probe"]["rows"]
    assert res_a["fate_counts"] == res_b["fate_counts"]

def test_out_of_order_targets_are_each_read_at_their_own_instant(tmp_path):
    """Regression: an unsorted queue made the sampler sleep past earlier
    targets (2508/6313 rows read 8 us to 10.9 ms late)."""
    class Link:
        sat = 0
        peer = 1
        gen = 1
        retired = False
        data_bits = 0
        ctrl_bits = 0
        _svc_phase = "idle"
        _tx_started_at = None
        _service_rate_bps = None
        current = None

    class Stub:
        horizon = 10.0

        def _link_rate(self, kind, now, sat, peer=None):
            return 1000.0

    stub = Stub()
    stub.env = simpy.Environment()
    stub.isls = {0: {"E": Link()}}
    stub._isl_resource_state_advertisement = types.MethodType(
        kernel.Kernel._isl_resource_state_advertisement, stub)
    probe = TargetProbe(stub, str(tmp_path / "p.jsonl"))
    probe.start()
    base = {"sat": 0, "direction": "E", "resource_id": "isl:0:1",
            "resource_peer": 1, "generation": 1, "predicted_bits": 0.0,
            "service_rate_bps": 1000.0, "decision_id": 1, "pid": 1,
            "kind": "forward", "chosen": "E", "selected": True,
            "candidate_directions": ["E"]}
    probe.enqueue([dict(base, eval_target_s=1.0, decision_id=1),
                   dict(base, eval_target_s=0.4, decision_id=2)])
    stub.env.run(until=2.0)
    sampled = sorted(r["sampled_at_s"] for r in probe.records)
    assert sampled == [0.4, 1.0]
    assert all(abs(r["sampled_at_s"] - r["eval_target_s"]) < 1e-12
               for r in probe.records)


def test_stride_samples_complete_decision_groups():
    probe = TargetProbe(None, "/tmp/never-written.jsonl", stride=2)
    rows = []
    for decision_id in (1, 2, 3):
        for i, direction in enumerate(("E", "W", "N", "S")):
            rows.append({
                "pid": 7, "decision_id": decision_id, "direction": direction,
                "eval_target_s": float(decision_id) + i * 0.01,
            })
    probe.enqueue(rows)

    selected = {(r["pid"], r["decision_id"]) for r in probe.pending}
    assert selected == {(7, 2)}
    assert len(probe.pending) == 4
    assert {r["direction"] for r in probe.pending} == {"E", "W", "N", "S"}
    assert probe.counters["decision_groups_seen"] == 3
    assert probe.counters["decision_groups_enqueued"] == 1
    assert probe.counters["decision_groups_skipped_by_stride"] == 2
    assert probe.counters["skipped_by_stride"] == 8


def test_sample_cap_drops_whole_decision_group():
    probe = TargetProbe(None, "/tmp/never-written.jsonl",
                        max_samples=3, stride=1)
    rows = []
    for decision_id in (1, 2):
        for i, direction in enumerate(("E", "W")):
            rows.append({
                "pid": 7, "decision_id": decision_id, "direction": direction,
                "eval_target_s": float(decision_id) + i * 0.01,
            })
    probe.enqueue(rows)

    assert {(r["pid"], r["decision_id"]) for r in probe.pending} == {(7, 1)}
    assert probe.counters["enqueued"] == 2
    assert probe.counters["dropped_overflow"] == 2
    assert probe.counters["decision_groups_dropped_overflow"] == 1
