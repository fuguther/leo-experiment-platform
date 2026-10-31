#!/usr/bin/env python3
"""Aggregate same-continuation forced-action forks.

Each fork evidence directory contains the compact `cf-<decision>.json` written by
CODE/scripts/counterfactual_one_decision.py for one sampled decision:

  * verification.branch_states_identical / baseline_action / forced_action
  * tagged_packet: the packet whose decision was forced, with per-branch fate,
    delivery and event trace
  * cohort: per-packet delivered_at delta (counterfactual - baseline), plus
    fate changes
  * outcome_delta: aggregate differences (network totals)

Sign convention: positive delta = the FRESH action made that packet slower.
Causal metric regret = mean cohort delta; negative = fresh better.
The proxy regret (truth-substituted J difference) from the sampling JSON is
reported next to it, but they are different quantities (single-decision score
vs realised network outcome) and must not be conflated.

Usage:
    python3 fork_analyze.py --sample ANALYSIS/.../fork-samples/c2-greedy-s7.json \
        --json out.json --md out.md <fork-evidence-dir> [...]
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def load_fork(directory):
    files = sorted(Path(directory).glob("cf-*.json"))
    if not files:
        return None
    return json.loads(files[0].read_text(encoding="utf-8"))


def summarize(document):
    verification = document.get("verification") or {}
    tagged = document.get("tagged_packet") or {}
    cohort_doc = document.get("cohort") or {}
    cohort = cohort_doc.get("delivery_deltas") or []
    deltas = [row["delta_s"] for row in cohort
              if row.get("delta_s") is not None]
    delivery_rows = [row for row in cohort if row.get("counterfactual_at") is not None]
    fate_changes = cohort_doc.get("fate_changes") or {}
    tagged_delta = None
    base_del = (tagged.get("baseline") or {}).get("delivery") or {}
    force_del = (tagged.get("counterfactual") or {}).get("delivery") or {}
    if base_del.get("delivered_at") is not None and force_del.get("delivered_at") is not None:
        tagged_delta = float(force_del["delivered_at"]) - float(base_del["delivered_at"])
    deltas.sort()
    return {
        "decision_id": (document.get("target") or {}).get("decision_id")
        or (document.get("verification") or {}).get("target_decision_id"),
        "branch_states_identical": verification.get("branch_states_identical"),
        "baseline_action": verification.get("baseline_action"),
        "forced_action": verification.get("forced_action"),
        "tagged_pid": tagged.get("pid"),
        "tagged_delta_ms": None if tagged_delta is None else 1000.0 * tagged_delta,
        "cohort_paired": len(deltas),
        "cohort_mean_delta_ms": (1000.0 * sum(deltas) / len(deltas)) if deltas else None,
        "cohort_p50_delta_ms": (1000.0 * statistics.median(deltas)) if deltas else None,
        "cohort_p10_delta_ms": (1000.0 * deltas[int(0.1 * (len(deltas) - 1))]) if deltas else None,
        "cohort_p90_delta_ms": (1000.0 * deltas[int(0.9 * (len(deltas) - 1))]) if deltas else None,
        "cohort_faster": sum(1 for d in deltas if d < -1e-9),
        "cohort_slower": sum(1 for d in deltas if d > 1e-9),
        "cohort_same": sum(1 for d in deltas if abs(d) <= 1e-9),
        "fate_changes": len(fate_changes),
        "outcome_delta": document.get("outcome_delta"),
    }


def aggregate(rows, sample):
    proxy = {}
    if sample:
        for row in json.loads(Path(sample).read_text())["decisions"]:
            proxy[row["decision_id"]] = row
    for row in rows:
        extra = proxy.get(row["decision_id"]) or {}
        row["stratum"] = extra.get("stratum")
        row["proxy_regret_ms"] = (None if extra.get("proxy_regret_s") is None
                                  else 1000.0 * extra["proxy_regret_s"])
        row["gamma"] = extra.get("gamma")
        row["margin_ms"] = (None if extra.get("margin_s") is None
                            else 1000.0 * extra["margin_s"])
    means = [r["cohort_mean_delta_ms"] for r in rows
             if r["cohort_mean_delta_ms"] is not None]
    tg = [r["tagged_delta_ms"] for r in rows if r["tagged_delta_ms"] is not None]
    return {
        "schema": "sq-fork-aggregate/v1",
        "forks": len(rows),
        "branch_states_identical_all": all(r["branch_states_identical"] for r in rows),
        "cohort_mean_delta_ms": {
            "mean": statistics.fmean(means) if means else None,
            "median": statistics.median(means) if means else None,
            "negative_fresh_better": sum(1 for v in means if v < 0),
            "positive_stale_better": sum(1 for v in means if v > 0),
        },
        "tagged_delta_ms": {
            "median": statistics.median(tg) if tg else None,
            "negative": sum(1 for v in tg if v < 0),
            "positive": sum(1 for v in tg if v > 0),
        },
        "proxy_regret_ms": {
            "median": statistics.median([r["proxy_regret_ms"] for r in rows
                                         if r["proxy_regret_ms"] is not None])
            if any(r["proxy_regret_ms"] is not None for r in rows) else None,
        },
        "rows": rows,
    }


def render(report):
    lines = ["# Same-continuation forced-action forks", "",
             f"- forks: {report['forks']}",
             f"- all branch_states_identical: {report['branch_states_identical_all']}",
             "- sign: cohort delta = fresh - stale (negative = fresh action faster)", "",
             "| decision | stratum | baseline->forced | tagged delta ms | cohort mean ms | "
             "p10 | p90 | faster/slower | proxy R ms | gamma |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in report["rows"]:
        def f(v, spec="%.2f"):
            return "n/a" if v is None else (spec % v)
        lines.append(
            f"| {row['decision_id']} | {row['stratum']} | "
            f"{row['baseline_action']}->{row['forced_action']} | "
            f"{f(row['tagged_delta_ms'],'%.1f')} | {f(row['cohort_mean_delta_ms'],'%.1f')} | "
            f"{f(row['cohort_p10_delta_ms'],'%.1f')} | {f(row['cohort_p90_delta_ms'],'%.1f')} | "
            f"{row['cohort_faster']}/{row['cohort_slower']} | "
            f"{f(row['proxy_regret_ms'],'%.2f')} | {f(row['gamma'],'%.3f')} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dirs", nargs="+")
    parser.add_argument("--sample")
    parser.add_argument("--json")
    parser.add_argument("--md")
    args = parser.parse_args()
    rows = []
    for directory in args.dirs:
        document = load_fork(directory)
        if document is None:
            continue
        rows.append(summarize(document))
    report = aggregate(rows, args.sample)
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                                   encoding="utf-8")
    if args.md:
        Path(args.md).write_text(render(report), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
