"""Third-round independent review (S1-B/C): control traffic had no timeline.

put_ctrl emitted no enqueue row, and a control packet in service produced no
service_start/service_finish row, so a reconstruction could not see control
traffic that arrived after the last DATA enqueue, nor a control packet that was
being served.  These tests run a REAL kernel trace and assert on it.
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import scripted_scenarios, time_alignment_compare as cmp
from CODE.leo_sim import config as config_mod, kernel

LINK = "isl:0:1"


@pytest.fixture(scope="module")
def trace():
    resolved, rows, geometry, _meta = scripted_scenarios.build("same_flow")
    raw = resolved["config"]
    cfg = config_mod.resolve_config(
        dict(raw, time_alignment=dict(raw["time_alignment"], enabled=True)))
    timeline = []
    kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=[],
                          timeline_sink=timeline)
    link_rows = [r for r in timeline if r.get("link_id") == LINK]
    data_pids = {r["pid"] for r in link_rows
                 if r.get("milestone") == "queue_enter"
                 and r.get("packet_kind") != "control"}
    bits = {pid: 1_000_000.0 for pid in data_pids}
    return link_rows, bits


def test_control_rows_exist_on_the_named_resource(trace):
    link_rows, _bits = trace
    kinds = [r["milestone"] for r in link_rows
             if r.get("packet_kind") == "control"]
    assert "queue_enter" in kinds, "put_ctrl must publish an enqueue event"
    assert "service_start" in kinds, "a control service window must be visible"


def test_a_control_packet_arriving_after_the_last_data_enqueue_is_visible(trace):
    """The old estimator froze the control backlog at the last DATA enqueue."""
    link_rows, bits = trace
    data_enqueues = [r["at"] for r in link_rows
                     if r.get("milestone") == "queue_enter"
                     and r.get("packet_kind") != "control"]
    ctrl_enqueues = [r for r in link_rows
                     if r.get("milestone") == "queue_enter"
                     and r.get("packet_kind") == "control"]
    assert data_enqueues and ctrl_enqueues

    # a control arrival strictly after the last data enqueue on this resource
    victim = None
    for row in ctrl_enqueues:
        earlier = [t for t in data_enqueues if t < row["at"] - 1e-12]
        later = [t for t in data_enqueues if t >= row["at"] - 1e-12]
        if earlier and not later:
            victim = row
            break
    assert victim is not None, "fixture must contain such an arrival"

    rec = cmp.resource_work_ahead(link_rows, bits, victim["at"], exclude_pid=None)
    assert rec["queued_ctrl_ahead_bits"] is not None
    assert rec["queued_ctrl_ahead_bits"] >= float(victim["bits"]), (
        "a control packet that just entered must be counted as ahead; "
        f"got {rec['queued_ctrl_ahead_bits']} for {victim['bits']} bits")
    assert "control timeline" in rec["components_method"]["queued_ctrl_ahead_bits"]


def test_a_control_packet_in_service_is_visible(trace):
    """No control service_start row meant an in-service control was invisible."""
    link_rows, bits = trace
    starts = [r for r in link_rows
              if r.get("milestone") == "service_start"
              and r.get("packet_kind") == "control"]
    finishes = [r for r in link_rows
                if r.get("milestone") == "service_finish"
                and r.get("packet_kind") == "control"]
    assert starts and finishes

    start = starts[0]
    later = [r["at"] for r in finishes if r["at"] > start["at"] + 1e-12]
    assert later, "the first control service must finish"
    instant = (start["at"] + min(later)) / 2.0

    rec = cmp.resource_work_ahead(link_rows, bits, instant, exclude_pid=None)
    assert rec["in_service_remaining_bits"] is not None, (
        "a control packet in service must produce a residual, not unknown")
    assert rec["in_service_remaining_bits"] > 0.0
