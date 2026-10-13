"""Summarize bounded load-calibration runs into a comparison table.

Reads one or more run directories (each holding cal/<cell>/summary.json), pulls
the headline physics out of every arm summary and, when the gzipped per-arm
result is present, the fate split, delivery count and state-age statistics.
Emits JSON plus a Markdown table. Read-only: never writes inside a run.

Usage:
  python3 -B CODE/work/WP-T1-COMPLETE/analyze_load_calibration.py \
      --run-dir <dir> [--run-dir <dir> ...] [--json out.json] [--md out.md]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from pathlib import Path


QUEUE_KEYS = ("uplink", "downlink", "holding", "isl_data", "isl_ctrl")


def _load_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_gzip_json(path: Path):
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError, EOFError):
        return None


def _num(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def _age_stats(result):
    """Mean/max state age across cached advertisements at the stop instant."""
    caches = (result or {}).get("caches")
    if not isinstance(caches, dict):
        return None, None, 0
    ages = []
    for per_sat in caches.values():
        if not isinstance(per_sat, dict):
            continue
        for entry in per_sat.values():
            aoi = _num((entry or {}).get("aoi"))
            if aoi is not None:
                ages.append(aoi)
    if not ages:
        return None, None, 0
    return statistics.fmean(ages), max(ages), len(ages)


def collect(run_dir: Path) -> dict:
    manifest = _load_json(run_dir / "run-manifest.json") or {}
    receipt = _load_json(run_dir / "run-receipt.json") or {}
    row = {
        "run_id": run_dir.name,
        "run_status": manifest.get("status") or receipt.get("status"),
        "release_id": manifest.get("release_id") or receipt.get("release_id"),
        "cells": [],
    }
    for summary_path in sorted(run_dir.glob("cal/*/summary.json")):
        summary = _load_json(summary_path)
        if summary is None:
            continue
        error = summary.get("error")
        cell = {
            "cell": summary.get("cell") or summary_path.parent.name,
            "status": summary.get("status"),
            "error": error.get("message") if isinstance(error, dict) else error,
            "execution_wall_s": _num(summary.get("execution_wall_s")),
            "kernel_call_count": summary.get("kernel_call_count"),
            "trace_rows": (summary.get("trace") or {}).get("rows"),
            "arms": [],
        }
        for arm in summary.get("arms") or []:
            result = _load_gzip_json(
                summary_path.parent / (arm.get("result_path") or ""))
            access = arm.get("access") or {}
            areas = arm.get("queue_area_bits_s") or {}
            decisions = arm.get("decisions") or {}
            mean_age, max_age, age_n = _age_stats(result)
            cell["arms"].append({
                "arm": arm.get("arm"),
                "natural_end": arm.get("natural_end"),
                "stop_time_s": _num(arm.get("stop_time_s")),
                "events_processed": arm.get("events_processed"),
                "execution_wall_s": _num(arm.get("execution_wall_s")),
                "queue_area_bits_s": {k: _num(areas.get(k)) for k in QUEUE_KEYS},
                "access_requests": access.get("requests"),
                "access_grants": access.get("grants"),
                "access_preposition_grants": access.get("preposition_grants"),
                "access_wait_total_s": _num(access.get("wait_time_s_total")),
                "access_wait_max_s": _num(access.get("wait_time_s_max")),
                "access_waiting_at_stop": access.get("waiting_at_stop"),
                "decision_rows": decisions.get("rows"),
                "forward_pids_count": decisions.get("forward_pids_count"),
                "nonzero_resource_work_candidate_count":
                    decisions.get("nonzero_resource_work_candidate_count"),
                "decisions_with_at_least_two_complete_candidates":
                    decisions.get("decisions_with_at_least_two_complete_candidates"),
                "d4": arm.get("d4"),
                "fate_counts": (result or {}).get("fate_counts"),
                "delivered": len((result or {}).get("deliveries") or {}),
                "mean_state_age_s": mean_age,
                "max_state_age_s": max_age,
                "state_age_samples": age_n,
                "error": arm.get("error"),
                "kernel_error": arm.get("kernel_error"),
            })
        row["cells"].append(cell)
    return row


def _fmt(value, digits=4):
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def to_markdown(rows: list) -> str:
    header = ("| cell | arm | offered | grants | delivered | D4 fail | isl_data area | "
              "uplink area | access wait s | >=2cand decisions | nonzero work cands | "
              "mean age s | events | wall s |")
    sep = "|" + "---|" * 15
    lines = [header, sep]
    for row in rows:
        for cell in row["cells"]:
            for arm in cell["arms"]:
                d4 = arm.get("d4") or {}
                areas = arm.get("queue_area_bits_s") or {}
                lines.append("| " + " | ".join([
                    cell["cell"], str(arm.get("arm")), _fmt(cell.get("trace_rows")),
                    _fmt(arm.get("access_grants")),
                    _fmt(arm.get("delivered")),
                    _fmt(d4.get("deadline_not_delivered_proportion")),
                    _fmt(areas.get("isl_data")), _fmt(areas.get("uplink")),
                    _fmt(arm.get("access_wait_total_s")),
                    _fmt(arm.get("decisions_with_at_least_two_complete_candidates")),
                    _fmt(arm.get("nonzero_resource_work_candidate_count")),
                    _fmt(arm.get("mean_state_age_s")),
                    _fmt(arm.get("events_processed")),
                    _fmt(arm.get("execution_wall_s")),
                ]) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", action="append", required=True)
    parser.add_argument("--json")
    parser.add_argument("--md")
    args = parser.parse_args(argv)
    rows = [collect(Path(path)) for path in args.run_dir]
    if args.json:
        Path(args.json).write_text(
            json.dumps(rows, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    table = to_markdown(rows)
    if args.md:
        Path(args.md).write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
