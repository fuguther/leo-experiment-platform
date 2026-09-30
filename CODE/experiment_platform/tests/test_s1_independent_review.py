"""Independent S1 counterexamples from the b1f44af review, plus real traces.

The first three tests are the reviewer file verbatim (kept failing-shaped: they
were written to fail on b1f44af and must not be weakened to close the item).
The rest add the cases the review asked for: a real target pid, a target that
has not been enqueued yet, and mixed control+data on a REAL kernel trace.
"""
from collections import deque
from types import SimpleNamespace

import pytest

from CODE.experiment_platform import scripted_scenarios, time_alignment_compare as cmp
from CODE.leo_sim import config as config_mod, kernel
from CODE.leo_sim.kernel import ControlPacket, ISLLink, Kernel
from CODE.experiment_platform.time_alignment_compare import (
    resource_work_ahead, _resource_timeline,
)

LINK = "isl:0:1"


def ctrl(kind, at, bits=100):
    return dict(milestone=kind, at=at, pid=None, packet_kind="control",
                link_id=LINK, bits=bits, origin=0, seq=1)


# ---------------------------------------------------------------- verbatim
def test_no_target_does_not_make_first_control_packet_the_target():
    rows = [ctrl("queue_enter", 0), ctrl("service_start", 0),
            dict(milestone="queue_enter", at=0.5, pid=7, link_id=LINK),
            ctrl("service_finish", 1)]
    result = resource_work_ahead(rows, {7: 1000}, 0.75, exclude_pid=None)
    assert result["queued_data_ahead_bits"] == 1000, result
    assert result["fifo_behind_bits"] == 0, result


def test_real_expiry_path_removes_control_packet_from_timeline_backlog():
    timeline = [ctrl("queue_enter", 0)]
    failures = []
    owner = SimpleNamespace(
        env=SimpleNamespace(now=2.0), timeline_sink=timeline,
        ctrl_ledger=SimpleNamespace(record=lambda *a: failures.append(a)))
    owner._fail = lambda *a, **kw: Kernel._fail(owner, *a, **kw)
    owner._timeline_ctrl = lambda *a, **kw: Kernel._timeline_ctrl(owner, *a, **kw)
    packet = ControlPacket(1, 0, 1, 0, 1, 1, 100, {})
    link = SimpleNamespace(k=owner, sat=0, peer=1,
                          ctrl_q=deque([packet]), data_q=deque(),
                          ctrl_bits=100, data_bits=0,
                          ctrl_area=SimpleNamespace(remove=lambda *a: None))
    ISLLink._expire_waiting(link)
    assert link.ctrl_bits == 0 and not link.ctrl_q
    assert failures == [(1, "CONTROL_EXPIRED", 100)]
    visible = _resource_timeline(timeline, LINK, 0)
    result = resource_work_ahead(visible, {}, 2.0, exclude_pid=99)
    assert result["queued_ctrl_ahead_bits"] == link.ctrl_bits, result


def test_drop_event_is_not_filtered_out_of_comparison_input():
    rows = [ctrl("queue_enter", 0), ctrl("ctrl_drop", 1)]
    visible = _resource_timeline(rows, LINK, 0)
    assert any(row["milestone"] == "ctrl_drop" for row in visible), visible


# ------------------------------------------- the three cases the review asked
def test_a_real_target_pid_still_works_with_control_rows_present():
    rows = [ctrl("queue_enter", 0), ctrl("service_start", 0),
            dict(milestone="queue_enter", at=0.4, pid=5, link_id=LINK),
            dict(milestone="queue_enter", at=0.6, pid=9, link_id=LINK),
            ctrl("service_finish", 1)]
    result = resource_work_ahead(rows, {5: 1000, 9: 400}, 0.75, exclude_pid=9)
    assert result["queued_data_ahead_bits"] == 1000, result
    assert result["excluded_target_bits"] == 400, result
    assert result["fifo_behind_bits"] == 0, result


def test_a_target_that_has_not_entered_yet_counts_all_data_as_ahead():
    rows = [ctrl("queue_enter", 0), ctrl("service_start", 0),
            dict(milestone="queue_enter", at=0.4, pid=5, link_id=LINK),
            ctrl("service_finish", 1)]
    result = resource_work_ahead(rows, {5: 1000}, 0.75, exclude_pid=9)
    assert result["queued_data_ahead_bits"] == 1000, result
    assert result["fifo_behind_bits"] == 0, result


def test_control_expiry_on_a_real_trace_leaves_the_backlog():
    """A dropped control packet must LEAVE the reconstructed backlog.

    Conservation check derived from the trace itself: at the drop instant the
    backlog must fall by at least the bits of the packet that expired.
    """
    resolved, rows, geometry, _meta = scripted_scenarios.build("same_flow")
    raw = resolved["config"]
    cfg = config_mod.resolve_config(
        dict(raw, time_alignment=dict(raw["time_alignment"], enabled=True),
             control_plane=dict(raw["control_plane"], ttl_s=0.5)))
    timeline = []
    kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=[],
                          timeline_sink=timeline)
    ctrl_rows = [r for r in timeline if r.get("packet_kind") == "control"]
    drops = [r for r in ctrl_rows if r["milestone"] == "ctrl_drop"]
    assert drops, "with a 0.5 s control TTL this fixture must expire control"

    victim = drops[0]
    visible = _resource_timeline(timeline, victim["link_id"], 0)
    before = resource_work_ahead(visible, {}, victim["at"] - 1e-9,
                                 exclude_pid=99)["queued_ctrl_ahead_bits"]
    after = resource_work_ahead(visible, {}, victim["at"] + 1e-9,
                                exclude_pid=99)["queued_ctrl_ahead_bits"]
    assert before is not None and after is not None
    assert before - after >= float(victim["bits"]), (
        "the expired control packet must leave the backlog: "
        f"before={before} after={after} dropped={victim['bits']}")
