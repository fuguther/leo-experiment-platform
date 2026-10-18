#!/usr/bin/env python3
"""Four-arm comparison on the hot scene (route A payoff).

Four cells share the SAME trace program and differ only in the state-time arm,
so any difference is the arm.  The truth probe is on for every arm, so the
same batch answers both questions: does fresh state change decisions/outcomes,
and how accurate is each arm prediction at its own eval target.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
CITIES = {"beijing": [39.9, 116.4], "london": [51.5, -0.13]}
ARMS = ("stale", "now", "common", "candidate")


def build(predictor: str, tag: str, arms=ARMS) -> dict:
    od = {"beijing,london": 0.5, "london,beijing": 0.5}
    program = {"schema": "scene-trace-program/v1", "model": "s1_stationary",
               "cells": CITIES, "od_weights": od, "packet_bits": 120000,
               "emission_start_s": 2.0, "emission_end_s": 2.35,
               "seed": 111, "total_mbps": 1600.0}
    overrides = {
        "scenario": {"duration_s": 6.0, "seed": 111},
        "demand": {"packet_bits": 120000, "emission_start_s": 2.0,
                   "emission_end_s": 2.35},
        "execution": {"max_packets": 40000},
        "access": {"slots_per_satellite": 8},
        "links": {"isl_rate_mbps": 1000.0, "isl_queue_bits": 20000000,
                  "rate_model": "constant"},
        "control_plane": {"advertise_interval_s": 0.05, "vis_k": 2},
        "routing": {"policy": "known_serving"},
        "time_alignment": {"predictor": predictor, "history_limit": 8,
                           "state_source": "advertised"},
    }
    cells = []
    for arm in arms:
        cells.append({
            "id": "hot-%s-%s" % (tag, arm), "arms": [arm],
            "trace_program": json.loads(json.dumps(program)),
            "diagnostics": {"timeline": False},
            "overrides": json.loads(json.dumps(overrides)),
        })
    doc = {"schema": "load-calibration-contract/v1",
           "base_profile": "CODE/leo_sim/profiles/t1_scene_trunk_v1.yaml",
           "deadline_s": 4, "max_packets": 40000, "max_duration_s": 60,
           "diagnostics": {"timeline": False, "control_audit": False,
                           "truth_probe": True, "truth_probe_max": 120000,
                           "truth_probe_stride": 1},
           "cells": cells}
    return doc


def main() -> int:
    bl = build("bounded_linear", "bl")
    (HERE / "scene_hot_arms_bl_20261019.json").write_text(
        json.dumps(bl, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print("bounded_linear cells:", [c["id"] for c in bl["cells"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
