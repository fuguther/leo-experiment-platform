#!/usr/bin/env python3
"""Generate the frozen policy-sensitivity contracts (2026-10-31).

Writes EXPERIMENTS/contracts/sq-policy-matrix-20261031.json from the frozen
ta_pilot_140_adv1 base profile.  The contract is the experiment definition:
it must be generated once, reviewed, committed, and then left untouched for
the whole matrix.  Trace programs are identical across policy cells that share
a load/control setting, so the compiled trace CSV (content-addressed by the
program digest) is byte-identical and packet ids are paired across runs.

Run from the worktree root:
    python3 ANALYSIS/SIM-QUAL-20261031/make_policy_contracts.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("EXPERIMENTS/contracts/sq-policy-matrix-20261031.json")
BASE_PROFILE = "CODE/leo_sim/profiles/ta_pilot_140_adv1.yaml"

#: The pilot's 6 named sites (same as ta-pilot contracts).
SITES = {
    "tokyo": [35.68, 139.69],
    "singapore": [1.35, 103.82],
    "london": [51.51, -0.13],
    "newyork": [40.71, -74.01],
    "saopaulo": [-23.55, -46.63],
    "lagos": [6.52, 3.38],
}

#: 30/70 hotspot OD: tokyo->singapore carries 0.70, the other 29 directed
#: pairs share 0.30 equally.  This is the pilot's frozen hotspot trace.
OD_WEIGHTS = {}
_rest = 0.30 / 29.0
for _src in SITES:
    for _dst in SITES:
        if _src == _dst:
            continue
        key = f"{_src},{_dst}"
        OD_WEIGHTS[key] = 0.7 if key == "tokyo,singapore" else _rest

HIGH_PULSE = {"start_s": 8.0, "duration_s": 0.5, "multiplier": 1.6}

POLICIES = {
    "geom": "geometry",
    "greedy": "queue_greedy",
    "hyst": "queue_greedy_hysteresis",
    "ewma": "queue_greedy_ewma",
}

POLICY_PARAMS = {
    "geometry": {},
    "queue_greedy": {},
    "queue_greedy_hysteresis": {"hysteresis_eta_s": 0.005},
    "queue_greedy_ewma": {"ewma_alpha": 0.25},
}


def trace_program(total_mbps: float, pulse: dict | None) -> dict:
    program = {
        "schema": "scene-trace-program/v1",
        "model": "s1_stationary",
        "seed": 7,
        "cells": SITES,
        "od_weights": OD_WEIGHTS,
        "total_mbps": total_mbps,
        "packet_bits": 1_000_000,
        "emission_start_s": 4.0,
        "emission_end_s": 14.0,
    }
    if pulse is not None:
        program["pulse"] = dict(pulse)
    return program


def cell_state_policy(policy: str, *, advert_s: float, ctrl_bits: int) -> dict:
    return {
        "control_plane": {
            "advertise_interval_s": advert_s,
            "vis_k": 3,
            "packet_bits": ctrl_bits,
        },
        "demand": {
            "emission_start_s": 4.0,
            "emission_end_s": 14.0,
            "packet_bits": 1_000_000,
        },
        "execution": {
            "compute_delay_s": 0.0,
            "compute_servers_per_satellite": 0,
            "max_packets": 40_000,
        },
        "links": {
            "isl_rate_mbps": 100.0,
            "isl_queue_bits": 64_000_000,
            "rate_model": "constant",
        },
        "scenario": {"duration_s": 20.0},
        "time_alignment": {
            # hold_last = "use the last advertisement exactly as received";
            # no extrapolation is mixed into the four policy definitions.
            "predictor": "hold_last",
            "state_policy": policy,
            **POLICY_PARAMS[policy],
        },
    }


def build_cells() -> list[dict]:
    cells = []
    # Phase 1: fixed network + traffic, ONLY the policy changes.
    for tag, policy in POLICIES.items():
        cells.append({
            "id": f"p1-high-{tag}",
            "arms": ["now"],
            "overrides": cell_state_policy(policy, advert_s=1.0, ctrl_bits=8_000),
            "trace_program": trace_program(120.0, HIGH_PULSE),
        })
    # Phase 1 negative control: low load, no burst.
    for tag, policy in POLICIES.items():
        cells.append({
            "id": f"p1-low-{tag}",
            "arms": ["now"],
            "overrides": cell_state_policy(policy, advert_s=1.0, ctrl_bits=8_000),
            "trace_program": trace_program(40.0, None),
        })
    # Phase 2: strong-sync baseline -- fast advertisements + small control
    # packets, so the remaining AoI is propagation-dominated.
    for tag, policy in POLICIES.items():
        cells.append({
            "id": f"p2-sync-{tag}",
            "arms": ["now"],
            "overrides": cell_state_policy(policy, advert_s=0.1, ctrl_bits=2_000),
            "trace_program": trace_program(120.0, HIGH_PULSE),
        })
    return cells


def main() -> None:
    document = {
        "schema": "load-calibration-contract/v1",
        "base_profile": BASE_PROFILE,
        "deadline_s": 4.0,
        "max_packets": 50_000,
        "max_duration_s": 20.0,
        "diagnostics": {
            "control_audit": False,
            "decisions": True,
            "timeline": False,
            "truth_probe": True,
            "truth_probe_max": 200_000,
            "truth_probe_stride": 1,
        },
        "cells": build_cells(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"path": str(OUT), "cells": [c["id"] for c in document["cells"]]},
                     indent=1))


if __name__ == "__main__":
    main()
