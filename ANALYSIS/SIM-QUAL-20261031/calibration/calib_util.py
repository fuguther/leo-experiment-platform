#!/usr/bin/env python3
"""Round-2 calibration: measured directed-ISL utilisation per cell.

Reads ONLY the verified pull-back evidence for the Round-2 calibration cells and
computes, per cell, over the evaluation window [4.0, 14.0) s:

    served_bits   bits served on one DIRECTED ISL inside the window
                  (a service window that straddles a boundary contributes its
                  proportional share)
    lambda        served_bits / window_s                     [bit/s]
    mu            rate_bps of that directed ISL              [bit/s]
    rho           lambda / mu
    eps           mu - lambda                                [bit/s]

Then reports the rho distribution, the top-10 links with their eps, the number
of links at or above 0.9 / 0.95, and a rho(load) table for the load sweep.

Nothing here writes to the evidence tree; the output JSON is the only product.

Usage:
  python3 calib_util.py --evidence-root <root> --prefix sq2- \
      --cells cal-060,cal-070,... --window 4,14 --out calibration/calib.json
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

#: Load label -> global offered load in Mbps, for the sweep cells only.
SWEEP_LOADS = {
    "cal-060": 60.0, "cal-070": 70.0, "cal-080": 80.0, "cal-090": 90.0,
    "cal-100": 100.0, "cal-120": 120.0, "cal-140": 140.0, "cal-150": 150.0,
    "cal-160": 160.0, "cal-165": 165.0,
}


def cell_paths(evidence_root: Path, prefix: str, cell: str, date: str):
    """Return (run_dir, cell_dir) for one run id, or (None, None)."""
    run_id = f"{prefix}{cell}-{date}-01"
    run_dir = evidence_root / run_id
    if not run_dir.is_dir():
        return run_id, None, None
    candidates = [p for p in run_dir.iterdir() if p.is_dir()]
    for candidate in candidates:
        if (candidate / "summary.json").is_file():
            return run_id, run_dir, candidate
    return run_id, run_dir, None


def load_result(cell_dir: Path) -> dict:
    path = cell_dir / "arms" / "now" / "result.json.gz"
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def link_rates(windows) -> dict:
    """mu per directed ISL, from the rate recorded on its own windows.

    The rate is a property of the link at service time; taking the modal value
    avoids one MCS/generation outlier hijacking a constant-rate network.  All
    observed values are reported so a mixed-rate link cannot be summarised
    silently as one number.
    """
    per_link = defaultdict(list)
    for row in windows:
        if row.get("stage") != "isl":
            continue
        rate = row.get("rate_bps")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool) \
                and math.isfinite(float(rate)) and float(rate) > 0:
            per_link[row["link_id"]].append(float(rate))
    out = {}
    for link_id, values in per_link.items():
        unique = sorted(set(values))
        out[link_id] = {
            "rate_bps": (max(unique, key=values.count) if len(unique) > 1
                         else unique[0]),
            "rate_values_bps": unique,
            "rate_uniform": len(unique) == 1,
        }
    return out


def served_in_window(windows, start: float, end: float) -> dict:
    """served_bits per directed ISL, proportional share of straddling windows."""
    out = defaultdict(float)
    for row in windows:
        if row.get("stage") != "isl":
            continue
        a, b = float(row["start"]), float(row["end"])
        if b <= a or b <= start or a >= end:
            continue
        covered = max(0.0, min(b, end) - max(a, start))
        out[row["link_id"]] += float(row.get("served_bits") or 0.0) \
            * (covered / (b - a))
    return out


def _quantiles(values, points=(0.5, 0.9, 0.99)):
    ordered = sorted(values)
    if not ordered:
        return {}
    out = {}
    for point in points:
        index = min(len(ordered) - 1,
                    max(0, int(round(point * (len(ordered) - 1)))))
        out["p%g" % (point * 100)] = ordered[index]
    return out


def utilisation_report(cell_dir: Path, window) -> dict:
    result = load_result(cell_dir)
    start, end = float(window[0]), float(window[1])
    span = end - start
    windows = result.get("link_service_windows") or []
    rates = link_rates(windows)
    served = served_in_window(windows, start, end)
    links = {}
    for link_id, rate_info in rates.items():
        mu = float(rate_info["rate_bps"])
        lam = float(served.get(link_id, 0.0)) / span
        links[link_id] = {
            "served_bits": float(served.get(link_id, 0.0)),
            "lambda_bps": lam,
            "lambda_mbps": lam / 1e6,
            "mu_bps": mu,
            "mu_mbps": mu / 1e6,
            "rho": lam / mu,
            "epsilon_bps": mu - lam,
            "epsilon_mbps": (mu - lam) / 1e6,
            "rate_uniform": rate_info["rate_uniform"],
        }
    rhos = [row["rho"] for row in links.values()]
    top = sorted(links.items(), key=lambda kv: kv[1]["rho"], reverse=True)[:10]
    return {
        "window_s": [start, end],
        "directed_isl_served": len(links),
        "served_bits_total": float(sum(row["served_bits"]
                                       for row in links.values())),
        "rho_all": {
            "n": len(rhos),
            "mean": (statistics.fmean(rhos) if rhos else None),
            "p50": _quantiles(rhos).get("p50"),
            "p90": _quantiles(rhos).get("p90"),
            "p99": _quantiles(rhos).get("p99"),
            "max": (max(rhos) if rhos else None),
            "min": (min(rhos) if rhos else None),
        },
        "rho_positive": {
            "n": sum(1 for v in rhos if v > 0),
            "p50": _quantiles([v for v in rhos if v > 0]).get("p50"),
            "p90": _quantiles([v for v in rhos if v > 0]).get("p90"),
            "max": (max([v for v in rhos if v > 0])
                    if any(v > 0 for v in rhos) else None),
        },
        "links_ge_090": sum(1 for v in rhos if v >= 0.90),
        "links_ge_095": sum(1 for v in rhos if v >= 0.95),
        "links_ge_099": sum(1 for v in rhos if v >= 0.99),
        "top10": [
            {"link_id": link_id, **{k: row[k] for k in (
                "rho", "lambda_mbps", "mu_mbps", "epsilon_mbps",
                "served_bits")}}
            for link_id, row in top
        ],
        "links": links,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--prefix", default="sq2-")
    parser.add_argument("--date", default="20261031")
    parser.add_argument("--cells", required=True,
                        help="comma separated cell ids")
    parser.add_argument("--window", default="4,14")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    window = tuple(float(x) for x in args.window.split(","))
    cells = [c.strip() for c in args.cells.split(",") if c.strip()]

    document = {"schema": "sq-calibration-utilisation/v1",
                "evidence_root": str(args.evidence_root),
                "run_id_prefix": args.prefix, "date": args.date,
                "window_s": list(window), "cells": {}}
    missing = []
    for cell in cells:
        run_id, run_dir, cell_dir = cell_paths(
            args.evidence_root, args.prefix, cell, args.date)
        if cell_dir is None:
            missing.append({"cell": cell, "run_id": run_id,
                            "reason": "no pulled cell directory"})
            continue
        summary = json.loads((cell_dir / "summary.json").read_text())
        manifest = json.loads((cell_dir / "trace-manifest.json").read_text())
        report = utilisation_report(cell_dir, window)
        report.update({
            "run_id": run_id,
            "status": summary.get("status"),
            "natural_end": (summary.get("arms") or [{}])[0].get("natural_end"),
            "trace_source_sha256": manifest.get("source_trace_sha256"),
            "offered_load_mbps": SWEEP_LOADS.get(cell),
            "declared_trace_rows": manifest.get("rows"),
            "execution_wall_s": summary.get("execution_wall_s"),
        })
        document["cells"][cell] = report
        print("%-16s served=%3d rho p50/p90/max = %.3f/%.3f/%.3f  >=0.9:%2d  "
              "top=%-14s %.3f" % (
                  cell, report["directed_isl_served"],
                  report["rho_all"]["p50"] or 0.0,
                  report["rho_all"]["p90"] or 0.0,
                  report["rho_all"]["max"] or 0.0,
                  report["links_ge_090"],
                  report["top10"][0]["link_id"] if report["top10"] else "-",
                  report["top10"][0]["rho"] if report["top10"] else 0.0))
    document["missing"] = missing
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print("wrote %s (%d cells, %d missing)" % (args.out, len(document["cells"]),
                                               len(missing)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
