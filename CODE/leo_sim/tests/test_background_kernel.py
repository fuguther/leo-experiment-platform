"""Satellite-sourced packets must reach the kernel and be delivered.

The end-to-end contract: a trace row with ``sat:<src>``/``sat:<dst>`` endpoints
enters the source satellite's forwarding path (not the uplink), is routed with
the ordinary policy, and is counted DELIVERED when it reaches the destination
satellite.  Without this the inter-satellite queue never becomes occupied and
no queue-prediction experiment can be meaningful.
"""
from __future__ import annotations

import pytest

from CODE import leo_sim
from CODE.leo_sim import config, kernel, trace


def _resolved(num_sats=24, planes=3, duration=8.0, background_mbps=2.0,
              packet_bits=64000):
    doc = {
        "scenario": {"name": "bg-kernel-test", "duration_s": duration,
                     "time_step_s": 0.5, "num_satellites": num_sats,
                     "num_planes": planes, "altitude_km": 1200.0,
                     "inclination_deg": 60.0, "min_elevation_deg": 5.0,
                     "seed": 3},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0, "demand_weight": 1.0},
            {"name": "b", "lat": 10.0, "lon": 10.0, "demand_weight": 1.0}]},
        "demand": {"mode": "uniform", "offered_mbps": 1.0,
                   "packet_bits": 64000, "emission_start_s": 0.5,
                   "emission_end_s": 2.0,
                   "sat_background_mbps": background_mbps,
                   "sat_background_packet_bits": packet_bits,
                   "sat_background_seed": 5},
        "access": {"unavailable_policy": "queue", "slots_per_satellite": 4},
        "links": {"rate_model": "constant", "isl_rate_mbps": 100.0,
                  "isl_queue_bits": 2000000, "geometry_loss": False,
                  "isl_dirs": ["N", "S"]},
        "topology": {"recompute_interval_s": 1.0},
        "control_plane": {"enabled": True, "advertise_interval_s": 0.2,
                          "vis_k": 1, "ttl_s": 5.0},
        "routing": {"policy": "known_serving", "max_hops": 32},
        "learning": {"algorithm": "none"},
        "execution": {"max_events": 2000000, "max_entities": 500,
                      "max_packets": 20000},
        "outputs": {"out_dir": "leo_sim_out/bg-kernel-test"},
    }
    return config.resolve_config(doc)


def test_satellite_background_rows_are_emitted_and_delivered():
    resolved = _resolved()
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        manifest = trace.compile_trace(resolved, tmp)
        rows = trace.load_trace(pathlib.Path(tmp) / "trace.csv")
    sat_rows = [r for r in rows if str(r["src_grid_id"]).startswith("sat:")]
    assert sat_rows, "background rows must appear in the compiled trace"
    assert all(str(r["dst_grid_id"]).startswith("sat:") for r in sat_rows)

    result = kernel.run_simulation(resolved, rows)
    fates = result["fate_counts"]
    delivered = fates.get("DELIVERED", 0)
    assert delivered >= len(sat_rows) * 0.5, (
        f"expected most background packets delivered, got {delivered} of "
        f"{len(sat_rows)} (fates={ {k: v for k, v in fates.items() if v} })")


def test_without_the_knob_there_are_no_background_rows():
    resolved = _resolved(background_mbps=None)
    import tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        trace.compile_trace(resolved, tmp)
        rows = trace.load_trace(pathlib.Path(tmp) / "trace.csv")
    assert not [r for r in rows if str(r["src_grid_id"]).startswith("sat:")]
