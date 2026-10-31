#!/usr/bin/env python3
"""Realized-freshness counterfactual (RECOMMENDED BY THE CONTROLLED BOTTLENECK).

The controlled bottleneck showed that the counterfactual |delta| used by the
140-sat persistence analysis can be 5-75x larger than the REALIZED load
difference between the stale run and a fresh-state run on the same trace.  This
contract runs the missing fresh side: the same policies/seed/trace as the main
matrix, but with `time_alignment.state_source = "ideal_current"` (the scorer
reads the true state at the decision instant instead of the advertised one).

Pairing: m3-real-* (ideal_current) vs m3-* (advertised) from the Round-3
contract share the same trace program and seed, so per-link served bits and pid
E2E can be differenced.  This is an oracle-state counterfactual, NOT a
deployable policy; it only measures how much of the counterfactual directional
error becomes a realized load change.
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("EXPERIMENTS/contracts/sq-realized-20261031.json")
BASE = "CODE/leo_sim/profiles/ta_pilot_140_adv1.yaml"
SITES = {
    "tokyo": [35.68, 139.69], "singapore": [1.35, 103.82],
    "london": [51.51, -0.13], "newyork": [40.71, -74.01],
    "saopaulo": [-23.55, -46.63], "lagos": [6.52, 3.38],
}
OD = {}
_rest = 0.30 / 29.0
for _s in SITES:
    for _d in SITES:
        if _s == _d:
            continue
        k = f"{_s},{_d}"
        OD[k] = 0.7 if k == "tokyo,singapore" else _rest
LOADS = {"b": 160.0, "c2": 200.0}


def cell(cell_id, regime, policy, advert_ms, seed=7):
    ta = {"predictor": "hold_last", "state_policy": policy,
          "state_source": "ideal_current"}
    if policy == "queue_softmin":
        ta["softmin_temperature_s"] = 0.010
    if policy == "queue_greedy_sticky":
        ta["sticky_hold_s"] = 0.1
    if policy == "queue_greedy_hysteresis":
        ta["hysteresis_eta_s"] = 0.005
    return {
        "id": cell_id,
        "arms": ["now"],
        "overrides": {
            "control_plane": {"advertise_interval_s": advert_ms / 1000.0,
                              "vis_k": 1, "packet_bits": 2_000},
            "demand": {"emission_start_s": 4.0, "emission_end_s": 14.0,
                       "packet_bits": 1_000_000},
            "execution": {"compute_delay_s": 0.0,
                          "compute_servers_per_satellite": 0,
                          "max_packets": 40_000},
            "links": {"isl_rate_mbps": 100.0, "isl_queue_bits": 64_000_000,
                      "rate_model": "constant"},
            "scenario": {"duration_s": 20.0},
            "time_alignment": ta,
        },
        "trace_program": {
            "schema": "scene-trace-program/v1", "model": "s1_stationary",
            "seed": seed, "cells": SITES, "od_weights": OD,
            "total_mbps": LOADS[regime], "packet_bits": 1_000_000,
            "emission_start_s": 4.0, "emission_end_s": 14.0,
        },
    }


def build():
    cells = []
    for policy in ("queue_greedy", "queue_greedy_ewma", "queue_softmin",
                   "queue_greedy_sticky"):
        cells.append(cell(f"m3-real-c2-{policy}-h1000-s7", "c2", policy, 1000))
    for policy in ("queue_greedy", "queue_greedy_ewma"):
        cells.append(cell(f"m3-real-c2-{policy}-h20-s7", "c2", policy, 20))
    for policy in ("queue_greedy", "queue_greedy_ewma"):
        cells.append(cell(f"m3-real-b-{policy}-h1000-s7", "b", policy, 1000))
    return {"schema": "load-calibration-contract/v1", "base_profile": BASE,
            "deadline_s": 4.0, "max_packets": 50_000, "max_duration_s": 20.0,
            "diagnostics": {"control_audit": False, "decisions": True,
                            "timeline": False, "truth_probe": True,
                            "truth_probe_max": 300_000,
                            "truth_probe_stride": 1},
            "cells": cells}


if __name__ == "__main__":
    doc = build()
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"path": str(OUT), "cells": [c["id"] for c in doc["cells"]]},
                     indent=1))
