#!/usr/bin/env python3
"""Emit the hot-load calibration contracts (route A).

Each cell keeps the device parameters (1 Gbps ISL, 20 Mbit buffer, 12000-bit
packets become 120000-bit packets so that the buffer holds ~167 packets, and
every city egress stays at 0.8 Gbps = rho 0.8 of one ISL).  What changes is
HOW MANY links the same traffic is spread over: two ODs put the whole load on
one path, six ODs share it three ways.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CITIES = {"beijing": [39.9, 116.4], "london": [51.5, -0.13],
          "newyork": [40.7, -74.0]}
BASE = "CODE/leo_sim/profiles/t1_scene_trunk_v1.yaml"


def cell(name, od_pairs, total_mbps, packet_bits=120000, window_s=0.35,
         duration_s=6.0, slots=8):
    used = sorted({c for od in od_pairs for c in od.split(",")})
    w = 1.0 / len(od_pairs)
    program = {"schema": "scene-trace-program/v1", "model": "s1_stationary",
               "cells": {c: CITIES[c] for c in used},
               "od_weights": {od: w for od in od_pairs},
               "packet_bits": int(packet_bits),
               "emission_start_s": 2.0,
               "emission_end_s": 2.0 + float(window_s),
               "seed": 111, "total_mbps": float(total_mbps)}
    return {
        "id": name, "arms": ["candidate"], "trace_program": program,
        "diagnostics": {"timeline": False},
        "overrides": {
            "scenario": {"duration_s": float(duration_s), "seed": 111},
            "demand": {"packet_bits": int(packet_bits),
                       "emission_start_s": 2.0,
                       "emission_end_s": 2.0 + float(window_s)},
            "execution": {"max_packets": 40000},
            "access": {"slots_per_satellite": int(slots)},
            "links": {"isl_rate_mbps": 1000.0, "isl_queue_bits": 20000000,
                      "rate_model": "constant"},
            "control_plane": {"advertise_interval_s": 0.05, "vis_k": 2},
            "routing": {"policy": "known_serving"},
            "time_alignment": {"predictor": "hold_last", "history_limit": 8,
                               "state_source": "advertised"},
        },
    }


def main() -> int:
    two = ["beijing,london", "london,beijing"]
    four = ["beijing,london", "beijing,newyork",
            "london,beijing", "newyork,beijing"]
    six = ["beijing,london", "beijing,newyork", "london,beijing",
           "london,newyork", "newyork,beijing", "newyork,london"]
    cells = [
        cell("hot-h1-r1600-2od", two, 1600),
        cell("hot-h3-r1600-4od", four, 1600),
        cell("hot-h4-r2400-6od", six, 2400),
    ]
    doc = {"schema": "load-calibration-contract/v1", "base_profile": BASE,
           "deadline_s": 4, "max_packets": 40000, "max_duration_s": 60,
           "diagnostics": {"timeline": False, "control_audit": False,
                           "truth_probe": True, "truth_probe_max": 200000,
                           "truth_probe_stride": 1},
           "cells": cells}
    (HERE / "scene_hot_calibration_20261019.json").write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for c in cells:
        p = c["trace_program"]
        pkts = p["total_mbps"] * 1e6 * (p["emission_end_s"]
                                        - p["emission_start_s"]) / p["packet_bits"]
        print("%-22s ODs=%d  R=%.0f Mbps  packets~%.0f" % (
            c["id"], len(p["od_weights"]), p["total_mbps"], pkts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
