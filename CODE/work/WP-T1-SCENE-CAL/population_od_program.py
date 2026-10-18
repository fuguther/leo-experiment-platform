#!/usr/bin/env python3
"""Population-weighted source/destination sampling -> a fixed exogenous trace.

WHAT THIS DOES
--------------
1. Loads the verified GPW v4 population raster through the EXISTING loader
   (leo_sim.population.load_population_regions) at the declared aggregation.
2. Draws ordered source/destination CELL PAIRS with probability proportional
   to population on both ends (alias method, the same method the codebase
   already uses for population destinations), without replacement of pairs.
3. Records, for every selected cell, the population-weighted SUB-CELL table
   from the native 0.25-degree pixels, so a 2-degree cell is represented by
   its population rather than by its centre.
4. Emits a scene-trace-program/v1 JSON that the existing materializer turns
   into the CSV the kernel reads, and generates that CSV to verify it.

WHAT IT DOES NOT DO
-------------------
* It does not change the demand model: model stays s1_stationary (homogeneous
  Poisson per OD at w * R / L), so the only change against the previous
  distributed scene is WHICH pairs exist and HOW the endpoints are placed.
* It does not touch the kernel, the routing policy or any state-time arm.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from CODE.leo_sim import grid, population  # noqa: E402
from CODE.leo_sim.trace import VoseAlias  # noqa: E402
from CODE.experiment_platform import scene_trace_program as TP  # noqa: E402


def load_subcells(raster_path: str, agg_deg: float):
    """Return {cell_id: [(lat, lon, pop), ...]} from the native pixels."""
    from PIL import Image
    with Image.open(raster_path) as image:
        values = np.asarray(image, dtype=np.float64)
        tags = dict(image.tag_v2)
    scale = tags[33550]
    tie = tags[33922]
    pixel_lon, pixel_lat = float(scale[0]), float(scale[1])
    west, north = float(tie[3]), float(tie[4])
    height, width = values.shape
    buckets = collections.defaultdict(dict)
    step = int(round(agg_deg / pixel_lon))
    if step < 1 or abs(step * pixel_lon - agg_deg) > 1e-9:
        raise SystemExit("aggregation must be a whole number of pixels")
    for row in range(height):
        lat = north - (row + 0.5) * pixel_lat
        for col in range(width):
            pop = float(values[row, col])
            if not math.isfinite(pop) or pop <= 0.0:
                continue
            lon = west + (col + 0.5) * pixel_lon
            cid = grid.grid_id(lat, lon, agg_deg)
            # Sub-cell index inside the aggregate cell, from the pixel itself.
            key = (row % step, col % step)
            bucket = buckets[cid]
            entry = bucket.get(key)
            if entry is None:
                bucket[key] = [lat, lon, pop]
            else:
                bucket[key][2] += pop
    return {cid: [tuple(v) for _k, v in sorted(b.items())]
            for cid, b in buckets.items()}


def sample_pairs(regions, n_pairs: int, seed: int, max_tries: int = 200000):
    """Ordered (src, dst) pairs, each end population-weighted."""
    ids = [r.grid_id for r in regions]
    pops = [r.population for r in regions]
    index = {gid: i for i, gid in enumerate(ids)}
    import random
    gen = random.Random(seed * 7919 + 17)
    alias = VoseAlias(pops)
    pairs = []
    seen = set()
    tries = 0
    while len(pairs) < n_pairs and tries < max_tries:
        tries += 1
        src = alias.draw(gen)
        dst = alias.draw(gen)
        if src == dst:
            continue
        key = (src, dst)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    if len(pairs) < 2:
        raise SystemExit("could not sample enough distinct OD pairs")
    return pairs, ids, index, alias


def build_program(raster_path: str, agg_deg: float, n_pairs: int, seed: int,
                  total_mbps: float, packet_bits: int, t0: float, t1: float):
    table = population.load_population_regions(raster_path, agg_deg)
    regions = table.regions
    pairs, ids, index, _alias = sample_pairs(regions, n_pairs, seed)
    total_pop = float(sum(r.population for r in regions))
    prob = [r.population / total_pop for r in regions]
    raw = []
    for src, dst in pairs:
        raw.append(prob[src] * prob[dst])
    norm = sum(raw)
    od_weights = {"%s,%s" % (ids[src], ids[dst]): w / norm
                  for (src, dst), w in zip(pairs, raw)}
    used = sorted({cid for pair in pairs for cid in pair})
    cells = {}
    for i in used:
        gid = ids[i]
        lat, lon = grid.grid_center(gid)
        cells[gid] = [lat, lon]
    subcells = load_subcells(raster_path, agg_deg)
    intra = {}
    for gid in cells:
        entries = subcells.get(gid) or []
        total = sum(e[2] for e in entries)
        if not entries or total <= 0:
            continue
        intra[gid] = [[e[0], e[1], e[2] / total] for e in entries]
    program = {
        "schema": TP.SCHEMA,
        "model": "s1_stationary",
        "cells": cells,
        "od_weights": od_weights,
        "packet_bits": int(packet_bits),
        "emission_start_s": float(t0),
        "emission_end_s": float(t1),
        "seed": int(seed),
        "total_mbps": float(total_mbps),
        "intra_cell": intra,
    }
    return program, table


def access_map(program: dict, lat0=53.0, sats=96, planes=12, alt=800.0,
               min_elev=10.0, step_s=0.05):
    """Population position -> access satellite, from the kernel geometry."""
    from CODE.leo_sim import model
    geo = model.Constellation(sats, planes, alt, lat0, min_elev, 6000.0)
    t0 = float(program["emission_start_s"])
    t1 = float(program["emission_end_s"])
    times = []
    t = t0
    while t <= t1 + 1e-12:
        times.append(t)
        t += step_s
    rows = []
    for gid, entries in sorted(program.get("intra_cell", {}).items()):
        best = collections.Counter()
        nvis = []
        switch = 0
        prev = None
        for lat, lon, _w in entries:
            series = []
            for now in times:
                visible = [s for s in range(sats)
                           if geo.ground_visible(s, lat, lon, now)]
                nvis.append(len(visible))
                if not visible:
                    series.append(None)
                    continue
                best_sat = max(visible,
                               key=lambda s: geo.elevation_deg(s, lat, lon, now))
                series.append(best_sat)
                best[best_sat] += 1
            seen = [s for s in series if s is not None]
            switch += sum(1 for a, b in zip(seen, seen[1:]) if a != b)
            if seen:
                prev = seen[0]
        rows.append({
            "cell_id": gid,
            "subcells": len(entries),
            "visible_min": min(nvis) if nvis else 0,
            "visible_median": (sorted(nvis)[len(nvis) // 2] if nvis else 0),
            "visible_max": max(nvis) if nvis else 0,
            "distinct_best_sats": len(best),
            "switch_events": switch,
            "first_best_sat": prev,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raster", required=True)
    ap.add_argument("--agg-deg", type=float, default=2.0)
    ap.add_argument("--od-pairs", type=int, default=96)
    ap.add_argument("--seed", type=int, default=20261019)
    ap.add_argument("--total-mbps", type=float, default=200.0)
    ap.add_argument("--packet-bits", type=int, default=12000)
    ap.add_argument("--emission-start-s", type=float, default=2.0)
    ap.add_argument("--emission-end-s", type=float, default=2.35)
    ap.add_argument("--out-program", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--out-access-map", default=None)
    args = ap.parse_args()

    program, table = build_program(
        args.raster, args.agg_deg, args.od_pairs, args.seed,
        args.total_mbps, args.packet_bits,
        args.emission_start_s, args.emission_end_s)
    # A declared OD that realises ZERO packets would make the program's OD set
    # disagree with the trace.  Drop it and renormalise, then regenerate, so
    # the declared weights describe the trace that is actually written.
    dropped = []
    for _attempt in range(4):
        rows = TP.generate(program)
        realised = collections.Counter()
        for row in rows:
            realised["%s,%s" % (
                grid.grid_id(row["src_lat"], row["src_lon"], args.agg_deg),
                grid.grid_id(row["dst_lat"], row["dst_lon"], args.agg_deg))] += 1
        empty = [od for od in program["od_weights"] if realised.get(od, 0) == 0]
        if not empty:
            break
        dropped.extend(empty)
        for od in empty:
            program["od_weights"].pop(od)
        total_w = sum(program["od_weights"].values())
        program["od_weights"] = {k: v / total_w
                                 for k, v in program["od_weights"].items()}
        used = sorted({cid for od in program["od_weights"] for cid in od.split(",")})
        program["cells"] = {k: v for k, v in program["cells"].items() if k in used}
        program["intra_cell"] = {k: v for k, v in program["intra_cell"].items()
                                 if k in used}
    digest = TP.program_digest(program)
    out_csv = Path(args.out_csv)
    info = TP.write_csv(rows, out_csv)
    rows2 = TP.generate(program)
    csv2 = out_csv.with_name(out_csv.name + ".second")
    info2 = TP.write_csv(rows2, csv2)
    same = (out_csv.read_bytes() == csv2.read_bytes())
    csv2.unlink()

    centres = 0
    for r in rows:
        cell = grid.grid_id(r["src_lat"], r["src_lon"], args.agg_deg)
        clat, clon = grid.grid_center(cell)
        if abs(r["src_lat"] - clat) < 1e-9 and abs(r["src_lon"] - clon) < 1e-9:
            centres += 1
    per_od = collections.Counter()
    for r in rows:
        per_od[(grid.grid_id(r["src_lat"], r["src_lon"], args.agg_deg),
                grid.grid_id(r["dst_lat"], r["dst_lon"], args.agg_deg))] += 1
    report = {
        "schema": "population-od-program-report/v1",
        "raster": {"path": args.raster, "sha256": table.source_sha256,
                   "aggregation_deg": table.aggregation_deg,
                   "positive_cells": len(table.regions),
                   "total_population": table.total_population},
        "sampling": {"od_pairs": len(program["od_weights"]), "seed": args.seed,
                     "dropped_zero_packet_ods": dropped,
                     "weight_sum": sum(program["od_weights"].values()),
                     "cells": len(program["cells"]),
                     "cells_with_subcells": len(program["intra_cell"]),
                     "subcells_total": sum(len(v) for v in
                                           program["intra_cell"].values())},
        "program_digest": digest,
        "trace": {"rows": len(rows), "sha256": info["sha256"],
                  "sha256_second_run": info2["sha256"],
                  "byte_identical_rerun": bool(same),
                  "center_exact_packets": centres,
                  "center_exact_fraction": (centres / max(1, len(rows))),
                  "distinct_od_realised": len(per_od),
                  "top_od": [{"od": "%s,%s" % k, "packets": v}
                             for k, v in per_od.most_common(5)]},
    }
    if args.out_access_map:
        amap = access_map(program)
        import csv as _csv
        with open(args.out_access_map, "w", newline="", encoding="utf-8") as fh:
            wr = _csv.DictWriter(fh, fieldnames=list(amap[0].keys()))
            wr.writeheader()
            wr.writerows(amap)
        report["access_map"] = {
            "path": args.out_access_map,
            "cells": len(amap),
            "median_visible": sorted(r["visible_median"] for r in amap)[len(amap) // 2],
            "max_distinct_best_sats": max(r["distinct_best_sats"] for r in amap),
            "cells_with_multiple_best_sats": sum(
                1 for r in amap if r["distinct_best_sats"] > 1),
            "cells_with_switches": sum(1 for r in amap if r["switch_events"] > 0),
            "geometry": {"sats": 96, "planes": 12, "alt_km": 800.0,
                         "incl_deg": 53.0, "min_elev_deg": 10.0},
        }
    Path(args.out_program).write_text(
        json.dumps(program, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    report_path = Path(args.out_program).with_name(
        Path(args.out_program).stem + "-report.json")
    report_path.write_text(
        json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
