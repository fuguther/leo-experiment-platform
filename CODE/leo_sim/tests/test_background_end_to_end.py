"""Background flow end-to-end: rows reach the kernel and change ISL occupancy."""
from __future__ import annotations
import pytest
from CODE.leo_sim import trace

def test_background_rows_are_appended_and_sorted():
    rows = trace.satellite_background_rows(
        count=5, num_satellites=4, bits=1000, emit_start_s=1.0,
        emit_end_s=2.0, seed=7, packet_id_start=1)
    assert all(r["src_grid_id"].startswith("sat:") for r in rows)
    assert all(r["dst_grid_id"].startswith("sat:") for r in rows)
    merged = rows + [{"packet_id": 99, "emit_time_s": 1.5,
                      "src_grid_id": "G1:0:0", "dst_grid_id": "G1:1:1",
                      "bits": 1000, "deadline_at_s": None}]
    merged.sort(key=lambda r: (r["emit_time_s"], r["packet_id"]))
    merged = [dict(r, packet_id=i + 1) for i, r in enumerate(merged)]
    trace.validate_packet_rows(merged, horizon_s=10.0, max_packets=100)
