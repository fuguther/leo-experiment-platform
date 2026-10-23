#!/usr/bin/env python3
"""LOCAL design sweep for the hot-corridor scene (NOT evidence).

Measures, per candidate setting: fraction of target instants with a non-zero
truth, how often the candidates of one decision actually DIFFER, the access
wait, the failure mix, and the wall time.  The VM probe is the only evidence;
this script exists to choose the setting without spending kernel calls.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import subprocess
import sys
import time
from pathlib import Path

CITIES = {"beijing": [39.9, 116.4], "london": [51.5, -0.13],
          "newyork": [40.7, -74.0], "frankfurt": [50.1, 8.68]}


def make_contract(name, od_pairs, total_mbps, packet_bits, window_s,
                  duration_s, slots):
    cells = sorted({c for od in od_pairs for c in od.split(",")})
    w = 1.0 / len(od_pairs)
    program = {"schema": "scene-trace-program/v1", "model": "s1_stationary",
               "cells": {c: CITIES[c] for c in cells},
               "od_weights": {od: w for od in od_pairs},
               "packet_bits": int(packet_bits), "emission_start_s": 2.0,
               "emission_end_s": 2.0 + float(window_s), "seed": 111,
               "total_mbps": float(total_mbps)}
    return {
        "schema": "load-calibration-contract/v1",
        "base_profile": "CODE/leo_sim/profiles/t1_scene_trunk_v1.yaml",
        "deadline_s": 4, "max_packets": 60000, "max_duration_s": 60,
        "diagnostics": {"timeline": False, "control_audit": False,
                        "truth_probe": True, "truth_probe_max": 400000,
                        "truth_probe_stride": 1},
        "cells": [{
            "id": name, "arms": ["candidate"], "trace_program": program,
            "diagnostics": {"timeline": False},
            "overrides": {
                "scenario": {"duration_s": float(duration_s), "seed": 111},
                "demand": {"packet_bits": int(packet_bits),
                           "emission_start_s": 2.0,
                           "emission_end_s": 2.0 + float(window_s)},
                "execution": {"max_packets": 60000},
                "access": {"slots_per_satellite": int(slots)},
                "links": {"isl_rate_mbps": 1000.0,
                          "isl_queue_bits": 20000000,
                          "rate_model": "constant"},
                "control_plane": {"advertise_interval_s": 0.05, "vis_k": 2},
                "routing": {"policy": "known_serving"},
                "time_alignment": {"predictor": "hold_last",
                                   "history_limit": 8,
                                   "state_source": "advertised"},
            },
        }],
    }


def analyse(cell_dir):
    path = cell_dir / "arms" / "candidate" / "target_probe.jsonl"
    per_dec = collections.defaultdict(list)
    labelled = nonzero = 0
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if not r.get("truth"):
            continue
        lab = r["truth"].get("work_ahead_bits")
        if lab is None:
            continue
        labelled += 1
        if lab > 0:
            nonzero += 1
        per_dec[(r["pid"], r["decision_id"])].append(float(lab))
    spread_dec = sum(1 for v in per_dec.values() if max(v) - min(v) > 0)
    with gzip.open(cell_dir / "arms" / "candidate" / "result.json.gz", "rt",
                   encoding="utf-8") as fh:
        res = json.load(fh)
    return {
        "labelled": labelled,
        "truth_nonzero_pct": (100.0 * nonzero / labelled if labelled else 0.0),
        "decisions": len(per_dec),
        "decisions_with_spread_pct": (100.0 * spread_dec / len(per_dec)
                                      if per_dec else 0.0),
        "access_wait_s": (res.get("access") or {}).get("wait_time_s_total"),
        "access_waiting_at_stop": (res.get("access") or {}).get("waiting_at_stop"),
        "fates": res.get("fate_counts"),
        "packets": sum((res.get("fate_counts") or {}).values()),
        "queue_area_isl_data": (res.get("queue_area_bits_s") or {}).get("isl_data"),
        "queue_area_uplink": (res.get("queue_area_bits_s") or {}).get("uplink"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", required=True,
                    help="JSON file with a list of settings")
    ap.add_argument("--root", default="out/sweep")
    args = ap.parse_args()
    root = Path(args.root)
    root.mkdir(parents=True, exist_ok=True)
    settings = json.loads(Path(args.settings).read_text())
    out = []
    for s in settings:
        name = s["name"]
        contract = make_contract(name, s["od_pairs"], s["total_mbps"],
                                 s["packet_bits"], s["window_s"],
                                 s.get("duration_s", 6.0),
                                 s.get("slots", 8))
        cpath = root / f"{name}.contract.json"
        cpath.write_text(json.dumps(contract, indent=1))
        odir = root / f"{name}.run"
        subprocess.run(["rm", "-rf", str(odir)], check=True)
        started = time.time()
        proc = subprocess.run(
            [sys.executable, "-m", "CODE.experiment_platform.load_calibration",
             "--contract", str(cpath), "--cell", name,
             "--out-dir", str(odir)],
            capture_output=True, text=True)
        wall = time.time() - started
        # --out-dir IS the cell directory (the batch runner passes
        # runs/<run-id>/cal/<cell>, load_calibration writes directly there).
        cell_dir = odir
        row = {"name": name, "wall_s": round(wall, 1),
               "rc": proc.returncode, "settings": s}
        if proc.returncode != 0:
            row["error"] = (proc.stdout or proc.stderr)[-400:]
        elif (cell_dir / "arms" / "candidate" / "target_probe.jsonl").is_file():
            row.update(analyse(cell_dir))
        out.append(row)
        print(json.dumps(row, default=str)[:900], flush=True)
    (root / "sweep-results.json").write_text(
        json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
