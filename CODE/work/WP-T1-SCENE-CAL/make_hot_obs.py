#!/usr/bin/env python3
"""Observation-rate calibration on the hot scene.

Measured: with advertise_interval_s = 0.05 the newest observation is zero in
74.5% of the cases where the truth is non-zero, so NEITHER hold_last NOR
bounded_linear can differ between arms: the information is not in the channel.
This contract shortens the advertisement interval so the same scene is observed
often enough for the state-time arms to have something to disagree about.  The
extra control traffic is real and is part of the cost to report.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CITIES = {"beijing": [39.9, 116.4], "london": [51.5, -0.13]}


def cell(name, arm, interval_s):
    program = {"schema": "scene-trace-program/v1", "model": "s1_stationary",
               "cells": CITIES,
               "od_weights": {"beijing,london": 0.5, "london,beijing": 0.5},
               "packet_bits": 120000, "emission_start_s": 2.0,
               "emission_end_s": 2.35, "seed": 111,
               "total_mbps": 1600.0}
    return {
        "id": name, "arms": [arm], "trace_program": program,
        "diagnostics": {"timeline": False},
        "overrides": {
            "scenario": {"duration_s": 6.0, "seed": 111},
            "demand": {"packet_bits": 120000, "emission_start_s": 2.0,
                       "emission_end_s": 2.35},
            "execution": {"max_packets": 40000},
            "access": {"slots_per_satellite": 8},
            "links": {"isl_rate_mbps": 1000.0, "isl_queue_bits": 20000000,
                      "rate_model": "constant"},
            "control_plane": {"advertise_interval_s": float(interval_s),
                              "vis_k": 2},
            "routing": {"policy": "known_serving"},
            "time_alignment": {"predictor": "bounded_linear",
                               "history_limit": 8,
                               "state_source": "advertised"},
        },
    }


def main() -> int:
    cells = [cell("hot-obs5ms-stale", "stale", 0.005),
             cell("hot-obs5ms-candidate", "candidate", 0.005)]
    doc = {"schema": "load-calibration-contract/v1",
           "base_profile": "CODE/leo_sim/profiles/t1_scene_trunk_v1.yaml",
           "deadline_s": 4, "max_packets": 40000, "max_duration_s": 60,
           "diagnostics": {"timeline": False, "control_audit": False,
                           "truth_probe": True, "truth_probe_max": 120000,
                           "truth_probe_stride": 1},
           "cells": cells}
    (HERE / "scene_hot_obs_20261019.json").write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("cells:", [c["id"] for c in cells], "advertise 5 ms, bounded_linear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
