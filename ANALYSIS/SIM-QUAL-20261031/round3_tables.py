#!/usr/bin/env python3
"""Render the Round-3 tables the report needs, from matrix.json (no new data)."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(matrix_path, out_dir):
    m = json.loads(Path(matrix_path).read_text())
    rows = {(r["regime"], r["sync_ms"], r["policy"]): r
            for r in m["response_sensitivity"]["rows"]}
    # join the aggregate-only numbers (AoI, PDR, control bytes) by group key
    for key, block in m["aggregates"].items():
        parts = key.split("|")
        regime, policy, sync = parts[2], parts[3], parts[4]
        sync_ms = int(sync[1:])
        if isinstance(policy, str) and policy.startswith("appendix"):
            continue
        scal = block.get("scalars") or {}
        row = rows.setdefault((regime, sync_ms, policy), {"regime": regime,
                              "sync_ms": sync_ms, "policy": policy})
        for name, target in (("aoi_p50_ms", "aoi_p50_ms"), ("pdr", "pdr"),
                             ("ctrl_offered_mbits", "ctrl_offered_mbits")):
            if name in scal:
                row[target] = scal[name]["mean"]
    order = ["geometry", "queue_greedy", "queue_greedy_hysteresis",
             "queue_greedy_ewma", "queue_softmin", "queue_greedy_sticky"]
    regimes = [("a", "A stable (90 Mbps)"), ("b", "B high (160 Mbps)"),
               ("c2", "C near-capacity (200 Mbps)"), ("d", "D burst (140+ru)")]
    lines = ["## Main matrix (seed means; A/B/C/D x 1 s / 20 ms x 6 policies)", "",
             "| regime | sync | policy | E2E mean | E2E p99 | flip rule | flip truth | switch | run | AoI p50 | PDR | ctrl Mbit |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for regime, label in regimes:
        for sync in (1000, 20):
            for policy in order:
                r = rows.get((regime, sync, policy))
                if r is None:
                    continue
                lines.append(
                    f"| {label} | {sync} ms | {policy} | {r['e2e_mean_ms']:.1f} | "
                    f"{r['e2e_p99_ms']:.1f} | {r['flip_rule_rate']:.4f} | "
                    f"{r['flip_truth_rate']:.4f} | {r['switch_rate']:.4f} | "
                    f"{r['run_length_mean_decisions']:.2f} | "
                    f"{r.get('aoi_p50_ms') or 0:.0f} | "
                    f"{r.get('pdr') if r.get('pdr') is not None else 0:.5f} | "
                    f"{r.get('ctrl_offered_mbits') or 0:.1f} |")
    lines += ["", "## Freshness–stability trade-off (20 ms − 1 s; same trace/seed)", "",
              "| group | pairs | ΔAoI p50 | Δflip | Δswitch | ΔE2E mean | ΔE2E p99 | Δctrl Mbit |",
              "|---|---|---|---|---|---|---|---|"]
    for row in m["freshness_stability"]["table"]:
        lines.append(
            f"| {row['group']} | {row['n_pairs']} | {row['delta_aoi_p50_ms']:.0f} | "
            f"{row['delta_flip_rule']:+.4f} | {row['delta_switch']:+.4f} | "
            f"{row['delta_e2e_mean_ms']:+.2f} | {row['delta_e2e_p99_ms']:+.2f} | "
            f"{row['delta_ctrl_offered_mbits']:+.0f} |")
    text = "\n".join(lines) + "\n"
    Path(out_dir, "tables.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "ANALYSIS/SIM-QUAL-20261031/round3-results/matrix.json",
         sys.argv[2] if len(sys.argv) > 2
         else "ANALYSIS/SIM-QUAL-20261031/round3-results")
