#!/usr/bin/env python3
"""Fit rho(load) from the measured calibration sweep and pick A/B/C/D loads.

Input  : the raw per-cell utilisation document written by calib_util.py
Output : the final calib.json (raw cells + fit + band suggestions + gaps)

Method (deliberately conservative):
  * Every statistic is measured, never modelled.  The fit is a monotone
    piecewise-linear interpolation BETWEEN measured cells; it is never used
    outside the measured load range.
  * A band recommendation always names one MEASURED cell when a cell lands in
    the band.  When no cell lands in the band the script says GAP and reports
    the two bracketing measured cells plus the interpolated load, flagged as
    interpolated (never as a measured point).
  * The driving statistic is the bottleneck link (max rho), because a "load
    regime" is defined by the most loaded directed ISL; the network-wide p90 is
    reported alongside so a disagreement between the two is visible rather than
    hidden.

Usage:
  python3 calib_fit.py --raw calibration/calib_raw.json --out calibration/calib.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

#: Target pressure bands, as directed-ISL utilisation.
BANDS = {
    "A": (0.40, 0.60),
    "B": (0.80, 0.90),
    "C": (0.95, 0.99),
}
#: Statistic name in the raw document -> human label.
STATISTICS = {
    "rho_max": "max rho over served directed ISLs (bottleneck)",
    "rho_p90": "p90 rho over served directed ISLs (network-wide)",
    "rho_p50": "p50 rho over served directed ISLs",
}


def statistic(raw_cell: dict, name: str):
    rho_all = raw_cell.get("rho_all") or {}
    if name == "rho_max":
        return rho_all.get("max")
    if name == "rho_p90":
        return rho_all.get("p90")
    if name == "rho_p50":
        return rho_all.get("p50")
    raise KeyError(name)


def sweep_rows(document: dict) -> list[dict]:
    rows = []
    for cell, raw in document["cells"].items():
        load = raw.get("offered_load_mbps")
        if load is None:
            continue
        rows.append({
            "cell": cell,
            "load_mbps": float(load),
            "rho_max": statistic(raw, "rho_max"),
            "rho_p90": statistic(raw, "rho_p90"),
            "rho_p50": statistic(raw, "rho_p50"),
            "links_ge_090": raw.get("links_ge_090"),
            "links_ge_095": raw.get("links_ge_095"),
            "top_link": (raw["top10"][0]["link_id"] if raw.get("top10")
                         else None),
            "top_rho": (raw["top10"][0]["rho"] if raw.get("top10") else None),
            "top_eps_mbps": (raw["top10"][0]["epsilon_mbps"]
                             if raw.get("top10") else None),
            "directed_isl_served": raw.get("directed_isl_served"),
            "served_bits_total": raw.get("served_bits_total"),
            "trace_source_sha256": raw.get("trace_source_sha256"),
            "execution_wall_s": raw.get("execution_wall_s"),
        })
    rows.sort(key=lambda row: row["load_mbps"])
    return rows


def interpolate_load(rows: list[dict], statistic_name: str,
                     target: float) -> dict | None:
    """Load at which `statistic_name` equals `target`, by linear interpolation.

    Returns None when the target is outside the measured range (no
    extrapolation is ever reported as a suggestion).
    """
    points = [(row["load_mbps"], row[statistic_name]) for row in rows
              if isinstance(row.get(statistic_name), (int, float))]
    if len(points) < 2:
        return None
    if target < min(value for _load, value in points) \
            or target > max(value for _load, value in points):
        return None
    for (load_a, value_a), (load_b, value_b) in zip(points, points[1:]):
        if value_a == value_b:
            continue
        lo, hi = min(value_a, value_b), max(value_a, value_b)
        if lo <= target <= hi:
            fraction = (target - value_a) / (value_b - value_a)
            return {
                "load_mbps": load_a + fraction * (load_b - load_a),
                "between_cells": [rows[[r["load_mbps"] for r in rows].index(
                    load_a)]["cell"],
                    rows[[r["load_mbps"] for r in rows].index(load_b)]["cell"]],
                "lower": {"load_mbps": load_a, "value": value_a},
                "upper": {"load_mbps": load_b, "value": value_b},
            }
    return None


def band_report(rows: list[dict], statistic_name: str, band_name: str,
                lo: float, hi: float) -> dict:
    inside = [row for row in rows
              if isinstance(row.get(statistic_name), (int, float))
              and lo <= row[statistic_name] <= hi]
    middle = (lo + hi) / 2.0
    report = {"band": band_name, "range": [lo, hi],
              "statistic": statistic_name, "cells_inside": []}
    if inside:
        best = min(inside, key=lambda row: abs(row[statistic_name] - middle))
        report["cells_inside"] = [
            {"cell": row["cell"], "load_mbps": row["load_mbps"],
             "value": row[statistic_name], "links_ge_090": row["links_ge_090"]}
            for row in inside]
        report["status"] = "MEASURED"
        report["suggested_cell"] = best["cell"]
        report["suggested_load_mbps"] = best["load_mbps"]
        report["suggested_value"] = best[statistic_name]
        report["note"] = (
            "suggested cell is the measured cell inside the band whose value is "
            "closest to the band centre")
        return report
    # No cell inside: bracket by the nearest measured values on both sides.
    below = [row for row in rows
             if isinstance(row.get(statistic_name), (int, float))
             and row[statistic_name] < lo]
    above = [row for row in rows
             if isinstance(row.get(statistic_name), (int, float))
             and row[statistic_name] > hi]
    report["status"] = "GAP"
    report["nearest_below"] = (
        max(below, key=lambda row: row[statistic_name]) if below else None)
    report["nearest_above"] = (
        min(above, key=lambda row: row[statistic_name]) if above else None)
    for target, label in ((lo, "lower_edge"), (middle, "band_centre")):
        interp = interpolate_load(rows, statistic_name, target)
        report["interpolated_%s" % label] = interp
    if report.get("interpolated_band_centre"):
        report["suggested_load_mbps"] = report[
            "interpolated_band_centre"]["load_mbps"]
        report["suggested_cell"] = None
        report["note"] = ("no measured cell falls inside the band; the "
                          "suggested load is an interpolation between two "
                          "measured cells and MUST be confirmed by one run")
    else:
        report["suggested_load_mbps"] = None
        report["note"] = ("no measured cell falls inside the band and the band "
                          "is outside the measured load range; no suggestion")
    return report


def saturation_scan(rows: list[dict]) -> dict:
    """Where does the bottleneck link first reach rho >= 1 / stay below it."""
    at_or_above = [row for row in rows
                   if isinstance(row.get("rho_max"), (int, float))
                   and row["rho_max"] >= 1.0]
    below = [row for row in rows
             if isinstance(row.get("rho_max"), (int, float))
             and row["rho_max"] < 1.0]
    crossing = None
    if below and at_or_above:
        last_ok = max(below, key=lambda row: row["load_mbps"])
        first_bad = min(at_or_above, key=lambda row: row["load_mbps"])
        crossing = {"last_measured_below_1": last_ok["cell"],
                    "first_measured_at_or_above_1": first_bad["cell"],
                    "last_load_below_1_mbps": last_ok["load_mbps"],
                    "first_load_at_or_above_1_mbps": first_bad["load_mbps"]}
    return {
        "any_cell_rho_max_ge_1": bool(at_or_above),
        "cells_rho_max_ge_1": [row["cell"] for row in at_or_above],
        "crossing": crossing,
        "max_measured_rho_max": (max(row["rho_max"] for row in rows
                                     if isinstance(row.get("rho_max"),
                                                   (int, float)))
                                 if rows else None),
    }


def consistency_check(rows: list[dict]) -> list[str]:
    """Monotonicity / noise warnings a reader must see next to the fit."""
    warnings = []
    for name in ("rho_max", "rho_p90"):
        previous = None
        drops = []
        for row in rows:
            value = row.get(name)
            if not isinstance(value, (int, float)):
                continue
            if previous is not None and value < previous[1] - 0.02:
                drops.append({"lower_cell": previous[0], "higher_cell": row["cell"],
                              "previous": previous[1], "now": value})
            previous = (row["cell"], value)
        if drops:
            warnings.append(
                "%s is not monotone in load: %s" % (name, json.dumps(drops)))
    return warnings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    document = json.loads(args.raw.read_text())
    rows = sweep_rows(document)
    bands = {}
    for statistic_name in STATISTICS:
        bands[statistic_name] = {}
        for band_name, (lo, hi) in BANDS.items():
            bands[statistic_name][band_name] = band_report(
                rows, statistic_name, band_name, lo, hi)
    document["sweep_table"] = rows
    document["band_suggestions"] = bands
    document["saturation_scan"] = saturation_scan(rows)
    document["warnings"] = consistency_check(rows)
    document["statistic_labels"] = STATISTICS
    document["band_definitions"] = BANDS
    document["fit_method"] = (
        "monotone piecewise-linear interpolation between measured cells; never "
        "extrapolated; a MEASURED suggestion always names the cell it came from")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print("%-6s %-8s %6s %6s %6s %6s %s" % ("cell", "load", "rhoMax",
                                            "rhoP90", "rhoP50", ">=0.9",
                                            "top link"))
    for row in rows:
        print("%-6s %-8.0f %6.3f %6.3f %6.3f %6d %s %.3f" % (
            row["cell"], row["load_mbps"], row["rho_max"] or 0.0,
            row["rho_p90"] or 0.0, row["rho_p50"] or 0.0,
            row["links_ge_090"] or 0, row["top_link"] or "-",
            row["top_rho"] or 0.0))
    for statistic_name, per_band in bands.items():
        print("\n[%s]" % statistic_name)
        for band_name, report in per_band.items():
            print("  %s %s -> %s (load %s)" % (
                band_name, report["status"], report.get("suggested_cell"),
                report.get("suggested_load_mbps")))
    if document["warnings"]:
        print("\nWARNINGS:")
        for warning in document["warnings"]:
            print("  " + warning)
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
