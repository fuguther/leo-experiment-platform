#!/usr/bin/env python3
"""Summarise one probe cell: access, queue areas, fates and probe coverage."""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cell-dir", required=True)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    cell = Path(args.cell_dir)
    summary = json.loads((cell / "summary.json").read_text())
    arm = (summary.get("arms") or [{}])[0]
    name = arm.get("arm")
    with gzip.open(cell / "arms" / name / "result.json.gz", "rt",
                   encoding="utf-8") as fh:
        result = json.load(fh)
    fates = result.get("fate_counts") or {}
    totals = result.get("totals") or {}
    areas = result.get("queue_area_bits_s") or {}
    access = result.get("access") or {}
    probe = (arm.get("target_probe") or {}).get("summary") or {}
    offered = float(totals.get("offered_bits") or 0.0)
    delivered = float(totals.get("delivered_bits") or 0.0)
    isl = float(areas.get("isl_data") or 0.0) + float(areas.get("isl_ctrl") or 0.0)
    uplink = float(areas.get("uplink") or 0.0)
    doc = {
        "schema": "probe-cell-summary/v1",
        "cell": summary.get("cell"),
        "arm": name,
        "status": summary.get("status"),
        "wall_s": summary.get("execution_wall_s"),
        "horizon_s": result.get("horizon_s"),
        "stop_time_s": result.get("stop_time_s"),
        "natural_end": result.get("natural_end"),
        "fates": fates,
        "packets": int(sum(fates.values())),
        "offered_bits": offered,
        "delivered_bits": delivered,
        "delivery_rate": (delivered / offered if offered else None),
        "no_route": int(fates.get("NO_ROUTE") or 0),
        "deadline_expired": int(fates.get("DATA_DEADLINE_EXPIRED") or 0),
        "queue_area_bits_s": areas,
        "access": access,
        "access_share_of_queue_area": (
            uplink / (uplink + isl) if (uplink + isl) > 0 else None),
        "probe": {
            "rows": (arm.get("target_probe") or {}).get("rows"),
            "enqueued": probe.get("enqueued"),
            "stride": probe.get("stride"),
            "skipped_by_stride": probe.get("skipped_by_stride"),
            "dropped_overflow": probe.get("dropped_overflow"),
            "sampled": probe.get("sampled"),
            "reason_counts": probe.get("reason_counts"),
            "eval_target_min_s": probe.get("eval_target_min_s"),
            "eval_target_max_s": probe.get("eval_target_max_s"),
        },
    }
    text = json.dumps(doc, indent=1, sort_keys=True)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
