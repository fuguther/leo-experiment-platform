"""Third-round independent review (S1): the counterexamples it found.

Counterexample A is the sharper one: at the excluded packet OWN enqueue instant
the kernel hands the reconstruction the authoritative backlog, and the review
showed an 86% shortfall there whenever a long service window runs past the
recorded horizon -- the correct number was sitting unused in rec["basis"].
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import time_alignment_compare as cmp


def test_a_truncated_service_window_falls_back_to_the_kernel_backlog():
    """S1-A: no service_finish row must not mean "in service = unknown".

    pid 1 starts service at t=0 and never finishes inside the recorded
    horizon; the target (pid 43) enters at t=5.0, and its own backlog_before
    carries the real in-service residual.
    """
    rows = [
        {"milestone": "service_start", "at": 0.0, "pid": 1},
        {"milestone": "queue_enter", "at": 0.5, "pid": 1,
         "backlog_before": {"queued_data_bits_before": 0,
                            "queued_ctrl_bits_before": 0,
                            "in_service_remaining_bits_before": 0}},
        {"milestone": "queue_enter", "at": 2.0, "pid": 42,
         "backlog_before": {"queued_data_bits_before": 1000000,
                            "queued_ctrl_bits_before": 0,
                            "in_service_remaining_bits_before": 11000000.0}},
        # the target itself, with the kernel authoritative backlog
        {"milestone": "queue_enter", "at": 5.0, "pid": 43,
         "backlog_before": {"queued_data_bits_before": 1000000,
                            "queued_ctrl_bits_before": 256000,
                            "in_service_remaining_bits_before": 7632000.0}},
    ]
    bits = {1: 12000000.0, 42: 1000000.0, 43: 3000000.0}

    rec = cmp.resource_work_ahead(rows, bits, 5.0, exclude_pid=43)

    # 1_000_000 queued (pid 42) + 256_000 control + 7_632_000 in service
    assert rec["available"] is True, rec["reason"]
    assert rec["in_service_remaining_bits"] == pytest.approx(7632000.0)
    assert rec["work_ahead_bits"] == pytest.approx(8888000.0), (
        "the kernel backlog_before at the target enqueue instant is the truth; "
        f"got {rec['work_ahead_bits']!r}")


def test_an_unknown_component_is_not_folded_into_a_number():
    """S1-D: work_ahead_bits used to return the partial sum when unavailable."""
    rows = [
        {"milestone": "queue_enter", "at": 1.0, "pid": 5,
         "backlog_before": {"queued_data_bits_before": 0,
                            "queued_ctrl_bits_before": None,
                            "in_service_remaining_bits_before": 0}},
    ]
    rec = cmp.resource_work_ahead(rows, {5: 1000.0}, 2.0, exclude_pid=9)

    assert rec["available"] is False
    assert rec["work_ahead_bits"] is None, (
        "an unavailable reconstruction must not expose a number a caller can "
        f"mistake for the truth; got {rec['work_ahead_bits']!r}")
    assert rec["work_ahead_bits_known_components_sum"] == pytest.approx(1000.0)
