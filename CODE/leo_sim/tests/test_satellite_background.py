"""Inter-satellite background flow: the missing half of the paper's traffic.

The paper mixes ground test flow with random satellite-to-satellite background
flow.  Without the second term the inter-satellite queues stay empty on this
platform (measured: 10,424/10,424 candidate scores dominated by propagation, so
perfect state changed E2E by 0.003%).  These tests pin the generic capability:
rows with ``sat:<id>`` endpoints are generated deterministically, validated,
injected at the source satellite and delivered at the destination satellite.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import trace


def test_background_rows_are_deterministic_and_distinct_endpoints():
    rows = trace.satellite_background_rows(
        count=50, num_satellites=8, bits=1000, emit_start_s=1.0,
        emit_end_s=2.0, seed=7, packet_id_start=1)
    again = trace.satellite_background_rows(
        count=50, num_satellites=8, bits=1000, emit_start_s=1.0,
        emit_end_s=2.0, seed=7, packet_id_start=1)
    assert rows == again, "same seed must give byte-identical rows"
    assert len(rows) == 50
    for row in rows:
        assert row["src_grid_id"].startswith("sat:")
        assert row["dst_grid_id"].startswith("sat:")
        assert row["src_grid_id"] != row["dst_grid_id"]
        assert 1.0 <= row["emit_time_s"] <= 2.0
    # the row contract requires (emit_time_s, packet_id) ordering, NOT
    # packet-id ordering: ids follow emission-time sort after generation.
    assert rows == sorted(rows, key=lambda r: (r["emit_time_s"], r["packet_id"]))
    assert len({r["packet_id"] for r in rows}) == 50


def test_background_rows_pass_the_packet_row_contract():
    rows = trace.satellite_background_rows(
        count=20, num_satellites=4, bits=8000, emit_start_s=0.5,
        emit_end_s=1.5, seed=3, packet_id_start=100)
    trace.validate_packet_rows(rows, horizon_s=10.0, max_packets=1000)


def test_a_ground_row_may_not_carry_a_satellite_destination():
    row = {"packet_id": 1, "emit_time_s": 1.0, "src_grid_id": "G1:0:0",
           "dst_grid_id": "sat:3", "bits": 100, "deadline_at_s": ""}
    with pytest.raises(trace.TraceError, match="mixed ground/satellite"):
        trace.validate_packet_rows([row], horizon_s=10.0, max_packets=10)


def test_zero_count_is_allowed_and_one_satellite_is_not():
    assert trace.satellite_background_rows(
        count=0, num_satellites=4, bits=100, emit_start_s=0.0,
        emit_end_s=1.0, seed=1, packet_id_start=1) == []
    with pytest.raises(trace.TraceError):
        trace.satellite_background_rows(
            count=1, num_satellites=1, bits=100, emit_start_s=0.0,
            emit_end_s=1.0, seed=1, packet_id_start=1)
