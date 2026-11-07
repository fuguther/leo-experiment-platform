"""Satellite-sourced packets must reach the kernel and be delivered.

The end-to-end contract: a trace row with ``sat:<src>``/``sat:<dst>`` endpoints
enters the source satellite's forwarding path (not the uplink), is routed with
the ordinary policy, and is counted DELIVERED -- in the ledger, in
``deliveries`` and in the fate counts -- when it reaches the destination
satellite.  Without this the inter-satellite queue never becomes occupied and
no queue-prediction experiment can be meaningful.

The assertions are on the BACKGROUND packet ids only.  Counting the aggregate
DELIVERED fate is not enough: ground test flow is delivered by the same run and
would let a run that delivers zero background packets pass (that false positive
is what this file used to contain).
"""
from __future__ import annotations

import tempfile
import pathlib

import pytest

from CODE.leo_sim import config, kernel, trace


def _resolved(num_sats=96, planes=12, duration=8.0, background_mbps=20.0,
              packet_bits=12000, policy="known_serving", vis_k=10,
              destination_source=None, control_plane_enabled=True,
              time_alignment=None, isl_dirs=("N", "S", "E", "W")):
    doc = {
        "scenario": {"name": "bg-kernel-test", "duration_s": duration,
                     "time_step_s": 0.5, "num_satellites": num_sats,
                     "num_planes": planes, "altitude_km": 800.0,
                     "inclination_deg": 53.0, "min_elevation_deg": 10.0,
                     "seed": 3},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0, "demand_weight": 1.0},
            {"name": "b", "lat": 25.0, "lon": 105.0, "demand_weight": 1.0}]},
        "demand": {"mode": "uniform", "offered_mbps": 5.0,
                   "packet_bits": 12000, "emission_start_s": 1.0,
                   "emission_end_s": 3.0,
                   "sat_background_mbps": background_mbps,
                   "sat_background_packet_bits": packet_bits,
                   "sat_background_seed": 5},
        "access": {"unavailable_policy": "queue", "slots_per_satellite": 4},
        "links": {"rate_model": "constant", "isl_rate_mbps": 200.0,
                  "max_isl_km": 6000.0,
                  "isl_queue_bits": 4000000, "geometry_loss": False,
                  "isl_dirs": list(isl_dirs)},
        "topology": {"recompute_interval_s": 1.0},
        "control_plane": {"enabled": control_plane_enabled,
                          "advertise_interval_s": 0.2, "vis_k": vis_k,
                          "ttl_s": 5.0, "event_advert_enabled": False},
        "routing": {"policy": policy, "max_hops": 32},
        "learning": {"algorithm": "none"},
        "execution": {"max_events": 2000000, "max_entities": 500,
                      "max_packets": 20000},
        "outputs": {"out_dir": "leo_sim_out/bg-kernel-test"},
    }
    if destination_source is not None:
        doc["routing"]["destination_source"] = destination_source
    if time_alignment is not None:
        doc["time_alignment"] = dict(time_alignment)
    return config.resolve_config(doc)


def _run(resolved):
    with tempfile.TemporaryDirectory() as tmp:
        trace.compile_trace(resolved, tmp)
        rows = trace.load_trace(pathlib.Path(tmp) / "trace.csv")
    sat_rows = [r for r in rows if str(r["src_grid_id"]).startswith("sat:")]
    result = kernel.run_simulation(resolved, rows)
    return sat_rows, result


def _bg_delivered(sat_rows, result):
    want = {int(r["packet_id"]) for r in sat_rows}
    return want & set(result["deliveries"])


def test_satellite_background_rows_are_emitted_and_delivered():
    resolved = _resolved()
    sat_rows, result = _run(resolved)
    assert sat_rows, "background rows must appear in the compiled trace"
    assert all(str(r["dst_grid_id"]).startswith("sat:") for r in sat_rows)
    delivered = _bg_delivered(sat_rows, result)
    assert len(delivered) == len(sat_rows), (
        f"every background packet must be delivered, got {len(delivered)} of "
        f"{len(sat_rows)} (fates="
        f"{ {k: v for k, v in result['fate_counts'].items() if v} })")
    # the delivery carries the satellite-destination marker
    marked = [pid for pid in delivered
              if result["deliveries"][pid].get("satellite_destination")
              is not None]
    assert len(marked) == len(delivered)


def test_node_destination_resolves_without_an_advertisement():
    """``sat:<id>`` is a network node: no service advertisement to look up."""
    resolved = _resolved(policy="info_queue", vis_k=0,
                         control_plane_enabled=False,
                         destination_source="known")
    sat_rows, result = _run(resolved)
    delivered = _bg_delivered(sat_rows, result)
    assert len(delivered) == len(sat_rows), (
        "known-node addressing must route the background flow with no control "
        f"plane, got {len(delivered)} of {len(sat_rows)}")


def test_known_addressing_is_what_makes_a_no_control_plane_arm_routable():
    """Guard the contrast: the knob is what makes the previous test pass."""
    resolved = _resolved(policy="info_queue", vis_k=0,
                         control_plane_enabled=False)
    sat_rows, result = _run(resolved)
    # background delivery works either way (the destination is a node), so the
    # observable difference must be on the GROUND test flow: without
    # destination_source=known an arm with no adverts cannot address a cell
    ground = [pid for pid, rec in result["deliveries"].items()
              if rec.get("satellite_destination") is None
              and pid not in {int(r["packet_id"]) for r in sat_rows}]
    assert not ground, (
        "without destination_source=known a no-control-plane arm must not "
        f"resolve ground destinations, got {len(ground)} ground deliveries")


def test_a_ring_only_topology_leaves_cross_plane_background_unroutable():
    """Regression guard for the diagnosis: with only intra-plane ISLs the
    random sat->sat pairs split across disconnected rings, so 'half delivered'
    is a TOPOLOGY artifact, not a delivery bug.  The four-direction topology
    must deliver everything."""
    ring, ring_res = _run(_resolved(isl_dirs=("N", "S")))
    full, full_res = _run(_resolved())
    ring_done = len(_bg_delivered(ring, ring_res))
    full_done = len(_bg_delivered(full, full_res))
    assert ring_done < len(ring)
    assert full_done == len(full)


def test_without_the_knob_there_are_no_background_rows():
    resolved = _resolved(background_mbps=None)
    with tempfile.TemporaryDirectory() as tmp:
        trace.compile_trace(resolved, tmp)
        rows = trace.load_trace(pathlib.Path(tmp) / "trace.csv")
    assert not [r for r in rows if str(r["src_grid_id"]).startswith("sat:")]
