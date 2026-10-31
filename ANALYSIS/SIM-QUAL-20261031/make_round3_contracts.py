#!/usr/bin/env python3
"""Round-3 contract: 6 policies x {1s, 20ms} sync x A/B/C/D x seeds 7/8/9.

Frozen inputs (Round-2 measured loads, unchanged):
  A = 90 Mbps, B = 160, C = 200, D = 140 + pulse 1.6x for 0.5 s at 8.0 s.
Both sync levels share the SAME control-plane configuration (vis_k=1,
control packet 2000 bit) so the sync axis changes only the advertisement
interval.  vis_k=1 keeps only direct-neighbour advertisements, which are the
advertisements the scorer actually uses for a candidate (the candidate's
resource is the peer's own egress queue); the used state freshness is therefore
identical to vis_k=3, while 20 ms advertisements stay affordable.

Cells:
  m3-<regime>-<policy>-h<sync_ms>-s<seed>          main matrix (144)
  m3-b|c2-<policy>-h100-s7                         100 ms sanity (12)
  m3-x-temp<us>-<regime>-h<sync>-s7                P4 temperature appendix (12)
  m3-x-sticky<ms>-<regime>-h1000-s7                P5 hold appendix (4)

Run from the worktree root:
  python3 ANALYSIS/SIM-QUAL-20261031/make_round3_contracts.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path("EXPERIMENTS/contracts/sq-round3-20261031.json")
BASE_PROFILE = "CODE/leo_sim/profiles/ta_pilot_140_adv1.yaml"

SITES = {
    "tokyo": [35.68, 139.69], "singapore": [1.35, 103.82],
    "london": [51.51, -0.13], "newyork": [40.71, -74.01],
    "saopaulo": [-23.55, -46.63], "lagos": [6.52, 3.38],
}
OD_WEIGHTS = {}
_rest = 0.30 / 29.0
for _src in SITES:
    for _dst in SITES:
        if _src == _dst:
            continue
        key = f"{_src},{_dst}"
        OD_WEIGHTS[key] = 0.7 if key == "tokyo,singapore" else _rest

LOADS = {"a": 90.0, "b": 160.0, "c2": 200.0}
BURST = {"start_s": 8.0, "duration_s": 0.5, "multiplier": 1.6}
D_BASELINE = 140.0

POLICIES = ("geometry", "queue_greedy", "queue_greedy_hysteresis",
            "queue_greedy_ewma", "queue_softmin", "queue_greedy_sticky")
SYNC_MS = (1000, 20)

DIAGNOSTICS = {
    "control_audit": False, "decisions": True, "timeline": False,
    "truth_probe": True, "truth_probe_max": 300_000,
    "truth_probe_stride": 1,
}


def trace_program(total_mbps: float, seed: int, pulse: dict | None = None) -> dict:
    program = {
        "schema": "scene-trace-program/v1", "model": "s1_stationary",
        "seed": int(seed), "cells": SITES, "od_weights": OD_WEIGHTS,
        "total_mbps": float(total_mbps), "packet_bits": 1_000_000,
        "emission_start_s": 4.0, "emission_end_s": 14.0,
    }
    if pulse is not None:
        program["pulse"] = dict(pulse)
    return program


def overrides(*, policy: str, advert_s: float) -> dict:
    ta = {
        "predictor": "hold_last",
        "state_policy": policy,
        "ewma_alpha": 0.25,
    }
    # Only the parameter the selected policy actually reads is written: the two
    # Round-3 keys carry a non-zero pre-registered DEFAULT and config refuses an
    # explicit value different from that default under another policy, so a
    # value here would either be a no-op or a hard error.
    if policy == "queue_greedy_hysteresis":
        ta["hysteresis_eta_s"] = 0.005
    if policy == "queue_softmin":
        ta["softmin_temperature_s"] = 0.010
    if policy == "queue_greedy_sticky":
        ta["sticky_hold_s"] = 0.1
    return {
        "control_plane": {
            "advertise_interval_s": float(advert_s),
            "vis_k": 1,
            "packet_bits": 2_000,
        },
        "demand": {"emission_start_s": 4.0, "emission_end_s": 14.0,
                   "packet_bits": 1_000_000},
        "execution": {"compute_delay_s": 0.0,
                      "compute_servers_per_satellite": 0,
                      "max_packets": 40_000},
        "links": {"isl_rate_mbps": 100.0, "isl_queue_bits": 64_000_000,
                  "rate_model": "constant"},
        "scenario": {"duration_s": 20.0},
        "time_alignment": ta,
    }


def cell(cell_id: str, *, regime: str, policy: str, advert_ms: int,
         seed: int) -> dict:
    if regime == "d":
        total = D_BASELINE
        pulse = BURST
    else:
        total = LOADS[regime]
        pulse = None
    return {
        "id": cell_id,
        "arms": ["now"],
        "overrides": overrides(policy=policy, advert_s=advert_ms / 1000.0),
        "trace_program": trace_program(total, seed, pulse),
    }


def build() -> dict:
    cells = []
    for regime in ("a", "b", "c2", "d"):
        for policy in POLICIES:
            for sync in SYNC_MS:
                for seed in (7, 8, 9):
                    cells.append(cell(
                        f"m3-{regime}-{policy}-h{sync}-s{seed}",
                        regime=regime, policy=policy,
                        advert_ms=sync, seed=seed))
    # 100 ms sanity on the two most discriminative regimes.
    for regime in ("b", "c2"):
        for policy in POLICIES:
            cells.append(cell(f"m3-{regime}-{policy}-h100-s7",
                              regime=regime, policy=policy,
                              advert_ms=100, seed=7))
    # P4 temperature appendix (frozen grid; main result uses T=0.010).
    for regime in ("b", "c2"):
        for sync in SYNC_MS:
            for tus in (2500, 10000, 40000):
                cid = f"m3-x-temp{tus}-{regime}-h{sync}-s7"
                c = cell(cid, regime=regime, policy="queue_softmin",
                         advert_ms=sync, seed=7)
                c["overrides"]["time_alignment"]["softmin_temperature_s"] = tus / 1e6
                cells.append(c)
    # P5 hold appendix (main result uses H=0.100 s).
    for regime in ("b", "c2"):
        for hold_ms in (20, 500):
            cid = f"m3-x-sticky{hold_ms}-{regime}-h1000-s7"
            c = cell(cid, regime=regime, policy="queue_greedy_sticky",
                     advert_ms=1000, seed=7)
            c["overrides"]["time_alignment"]["sticky_hold_s"] = hold_ms / 1000.0
            cells.append(c)
    return {
        "schema": "load-calibration-contract/v1",
        "base_profile": BASE_PROFILE,
        "deadline_s": 4.0,
        "max_packets": 50_000,
        "max_duration_s": 20.0,
        "diagnostics": DIAGNOSTICS,
        "cells": cells,
    }


def main() -> None:
    document = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"path": str(OUT), "cells": len(document["cells"]),
                      "first": document["cells"][0]["id"],
                      "last": document["cells"][-1]["id"]}, indent=1))


if __name__ == "__main__":
    main()
