#!/usr/bin/env python3
"""F2 attribution step for WP-LEO-V2-PLATFORM-AUDIT-F2 (PROCEDURE section 5b).

Why this tool exists: the F2 node-processing stage is recorded ONLY on the kernel
timeline sink (kernel.py:4297/4300; it deliberately emits no packet_event).
metrics_independent.decompose_packet_delay defines decision_compute_s as the sum of
the UNCOVERED intervals of the packet timeline, and an F2 occupancy IS exactly an
uncovered interval -- so an analyzer that is not handed the F2 spans publishes
satellite node-processing time as decision computation time.  This tool performs
both readings on the SAME persisted run so the difference is explicit and auditable,
and it fails loud when the identity it claims does not hold.

Usage:
  python3 CODE/work/WP-LEO-V2-PLATFORM-AUDIT-F2/R02/attribute_f2.py \
      --results-root CODE/Results \
      --experiment EXP-20260924-PLATFORM-AUDIT-F2-R02 \
      --compute-delay-s 0.05 \
      --out ANALYSIS/EXP-20260924-PLATFORM-AUDIT-F2-R02/f2-attribution.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from CODE.leo_sim import metrics_independent as indep  # noqa: E402

ARMS = ("control", "f2")
TOL = 1e-9


class AttributionError(RuntimeError):
    """A claimed identity did not hold: report it, never round it away."""


def _read_arm(results_root: Path, run_id: str) -> tuple[dict, list]:
    base = results_root / run_id
    ledger_path = base / "ledgers.json"
    timeline_path = base / "timeline.jsonl"
    if not ledger_path.is_file():
        raise AttributionError(f"missing ledger: {ledger_path}")
    if not timeline_path.is_file():
        raise AttributionError(
            f"missing timeline stream: {timeline_path} -- F2 is unattributable "
            f"without it; re-run with --timeline-log")
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in
            timeline_path.read_text(encoding="utf-8").splitlines() if line]
    return ledger, rows


def analyse_arm(results_root: Path, run_id: str, compute_delay_s: float) -> dict:
    ledger, timeline = _read_arm(results_root, run_id)
    events = ledger.get("packet_events") or []
    windows = ledger.get("link_service_windows") or []

    milestones = [r.get("milestone") for r in timeline]
    starts = milestones.count("node_process_start")
    ends = milestones.count("node_process_end")
    if starts != ends:
        raise AttributionError(
            f"{run_id}: {starts} node_process_start vs {ends} node_process_end "
            f"-- an unclosed occupancy must never be silently dropped")

    spans = indep.node_process_spans(timeline)
    node_total = math.fsum(end - start
                           for values in spans.values()
                           for start, end in values)

    labelled = indep.verify_delay_decomposition(
        events, windows, ledger, node_spans_by_pid=spans)
    unlabelled = indep.verify_delay_decomposition(events, windows, ledger)

    labelled_dc = labelled["total_decision_compute_s"]
    unlabelled_dc = unlabelled["total_decision_compute_s"]
    leakage = unlabelled_dc - labelled_dc

    checks = {
        "spans_supplied": labelled["node_process_spans_supplied"] is True,
        "labelled_closure_ok": bool(labelled["ok"]),
        "labelled_residual_within_tol":
            labelled["max_abs_residual_s"] <= TOL,
        "leakage_equals_node_total": math.isclose(
            leakage, node_total, rel_tol=0.0, abs_tol=TOL),
        "labelled_dc_is_multiple_of_compute_delay":
            abs(labelled_dc / compute_delay_s
                - round(labelled_dc / compute_delay_s)) <= 1e-9,
        "unlabelled_closure_ok": bool(unlabelled["ok"]),
    }
    links = (ledger.get("congestion_metrics") or {}).get("links") or {}
    return {
        "run_id": run_id,
        "node_process_milestones": {"start": starts, "end": ends},
        "f2_packets": sorted(int(pid) for pid in spans),
        "total_node_process_s": node_total,
        "labelled_total_decision_compute_s": labelled_dc,
        "unlabelled_total_decision_compute_s": unlabelled_dc,
        "leakage_s": leakage,
        "labelled_max_abs_residual_s": labelled["max_abs_residual_s"],
        "delivered_pids": labelled["delivered_pids"],
        "per_packet": {
            pid: {"node_process_s": item["node_process_s"],
                  "decision_compute_s": item["decision_compute_s"],
                  "e2e_s": item["e2e_s"]}
            for pid, item in labelled["packets"].items()},
        "link_capacity": {
            link: {"served_bits": item["served_bits"],
                   "capacity_bits": item["capacity_bits"],
                   "available_capacity_bits": item["available_capacity_bits"],
                   "available_time_s": item["available_time_s"],
                   "utilization": item["utilization"]}
            for link, item in links.items()},
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=Path("CODE/Results"))
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--compute-delay-s", type=float, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    arms = {arm: analyse_arm(args.results_root,
                             f"{args.experiment}-{arm}-s7",
                             args.compute_delay_s)
            for arm in ARMS}

    # cross-arm comparison: REPORTED, never asserted equal
    left = arms["control"]["link_capacity"]
    right = arms["f2"]["link_capacity"]
    differing = sorted(k for k in set(left) | set(right)
                       if left.get(k) != right.get(k))

    failed = [f"{arm}:{name}" for arm, data in arms.items()
              for name, ok in data["checks"].items() if not ok]
    report = {
        "schema": "leo-sim-f2-attribution/v1",
        "experiment": args.experiment,
        "compute_delay_s": args.compute_delay_s,
        "tolerance_s": TOL,
        "arms": arms,
        "cross_arm": {
            "differing_link_count": len(differing),
            "differing_links": differing[:50],
            "asserted_equal": False,
            "note": ("reported, not asserted: a 0.05 s shift may legitimately move an "
                     "availability window across the 0.1 s sampling boundary"),
        },
        "failed_checks": failed,
        "ok": not failed,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")

    print(json.dumps({
        "status": "ok" if report["ok"] else "FAILED",
        "out": str(args.out),
        "failed_checks": failed,
        **{f"{arm}_node_process_s": d["total_node_process_s"]
           for arm, d in arms.items()},
        **{f"{arm}_labelled_decision_compute_s":
           d["labelled_total_decision_compute_s"] for arm, d in arms.items()},
        **{f"{arm}_unlabelled_decision_compute_s":
           d["unlabelled_total_decision_compute_s"] for arm, d in arms.items()},
        "cross_arm_differing_links": len(differing),
    }, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
