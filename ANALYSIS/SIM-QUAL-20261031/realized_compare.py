#!/usr/bin/env python3
"""Realized freshness counterfactual: advertised vs ideal_current runs.

The controlled bottleneck showed the counterfactual |delta| can overstate the
REALIZED stale-vs-fresh load difference by 5-75x.  This script pairs a main
matrix cell (state_source=advertised) with its ideal_current twin (same trace,
same seed, same policy/sync) and measures what actually changed:

  * per directed-ISL served rate difference over the evaluation window
  * z_realized = |lambda_advertised - lambda_fresh| / epsilon
  * paired per-pid E2E difference (same trace -> same packet identities)
  * network aggregates (E2E, queue area, fates)

It is an oracle-state counterfactual, not a deployable policy.
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from sq_policy_analyze import network_stats, qstats, _mean  # noqa: E402


def _load_result(cell):
    with gzip.open(cell / "arms" / "now" / "result.json.gz", "rt",
                   encoding="utf-8") as fh:
        return json.load(fh)


def _config(cell):
    return json.loads((cell / "arms" / "now" / "config.json").read_text())["resolved_config"]


def _trace_sha(cell):
    return json.loads((cell / "trace-manifest.json").read_text())["source_trace_sha256"]


def _window_served(result, start=4.0, end=14.0):
    served = defaultdict(float)
    for row in result.get("link_service_windows") or []:
        lid = str(row.get("link_id", ""))
        if not lid.startswith("isl:"):
            continue
        a, b = float(row["start"]), float(row["end"])
        if b <= start or a >= end:
            continue
        width = b - a
        covered = max(0.0, min(b, end) - max(a, start))
        if width > 0:
            served[lid] += float(row.get("served_bits") or 0.0) * (covered / width)
    return served


def compare(advertised: Path, fresh: Path, start=4.0, end=14.0):
    res_a = _load_result(advertised)
    res_f = _load_result(fresh)
    cfg_a = _config(advertised)
    cfg_f = _config(fresh)
    same_trace = _trace_sha(advertised) == _trace_sha(fresh)
    policy_a = cfg_a["time_alignment"]["state_policy"]
    policy_f = cfg_f["time_alignment"]["state_policy"]
    source_f = cfg_f["time_alignment"].get("state_source")
    adv_sync = cfg_a["control_plane"]["advertise_interval_s"]
    rate = float(cfg_a["links"]["isl_rate_mbps"]) * 1e6
    window = end - start
    sa = _window_served(res_a, start, end)
    sf = _window_served(res_f, start, end)
    links = sorted(set(sa) | set(sf))
    rows = []
    for lid in links:
        lam_a = sa.get(lid, 0.0) / window
        lam_f = sf.get(lid, 0.0) / window
        eps = max(0.0, rate - lam_f)
        delta = lam_a - lam_f
        z = (abs(delta) / eps) if eps > 1e-6 else (float("inf") if abs(delta) > 1e-6 else 0.0)
        rows.append({"link": lid, "lambda_advertised_bps": lam_a,
                     "lambda_fresh_bps": lam_f,
                     "delta_bps": delta, "epsilon_bps": eps, "z_realized": z})
    top = sorted(rows, key=lambda r: (-r["z_realized"], r["link"]))[:10]
    abs_delta = sum(abs(r["delta_bps"]) for r in rows)
    q_a = (res_a.get("queue_area_bits_s") or {}).get("isl_data", 0.0)
    q_f = (res_f.get("queue_area_bits_s") or {}).get("isl_data", 0.0)
    # paired pid E2E
    def e2e_map(result):
        packets = (result.get("congestion_metrics") or {}).get("packets") or {}
        return {int(pid): p.get("e2e_s") for pid, p in packets.items()
                if p.get("e2e_s") is not None}
    ea, ef = e2e_map(res_a), e2e_map(res_f)
    common = sorted(set(ea) & set(ef))
    deltas = [ef[pid] - ea[pid] for pid in common]
    return {
        "schema": "sq-realized-compare/v1",
        "advertised_cell": advertised.name,
        "fresh_cell": fresh.name,
        "same_trace": same_trace,
        "policy_advertised": policy_a,
        "policy_fresh": policy_f,
        "fresh_state_source": source_f,
        "advertise_interval_s": adv_sync,
        "links": len(rows),
        "sum_abs_delta_mbps": 1e-6 * abs_delta,
        "max_abs_delta_mbps": 1e-6 * max((abs(r["delta_bps"]) for r in rows),
                                        default=0.0),
        "z_realized_p50": qstats([r["z_realized"] for r in rows
                                  if r["z_realized"] != float("inf")],
                                 points=(0.5,)).get("p50"),
        "z_realized_max": max((r["z_realized"] for r in rows), default=None),
        "top_links": top,
        "e2e": {
            "advertised_mean_ms": 1e-3 * 1e3 * _mean([v for v in ea.values()]) if ea else None,
            "fresh_mean_ms": 1e-3 * 1e3 * _mean([v for v in ef.values()]) if ef else None,
            "paired_n": len(common),
            "paired_mean_delta_ms": (1000.0 * _mean(deltas)) if deltas else None,
            "paired_p50_delta_ms": (1000.0 * qstats(deltas).get("p50", 0.0))
            if deltas else None,
            "fresh_faster": sum(1 for d in deltas if d < -1e-9),
            "fresh_slower": sum(1 for d in deltas if d > 1e-9),
        },
        "queue_area_isl_data": {"advertised": q_a, "fresh": q_f},
        "fates": {"advertised": res_a.get("fate_counts"),
                  "fresh": res_f.get("fate_counts")},
    }


def render(report):
    lines = ["# realized-vs-counterfactual freshness comparison", "",
             f"- advertised cell: {report['advertised_cell']}",
             f"- fresh (ideal_current) cell: {report['fresh_cell']}",
             f"- same trace: {report['same_trace']}  policy: {report['policy_advertised']} -> {report['policy_fresh']}",
             f"- state_source of fresh arm: {report['fresh_state_source']}",
             f"- sum |Δλ| over directed ISLs: {report['sum_abs_delta_mbps']:.3f} Mbps",
             f"- max |Δλ|: {report['max_abs_delta_mbps']:.3f} Mbps",
             f"- z_realized p50 / max: {report['z_realized_p50']} / {report['z_realized_max']}",
             f"- paired E2E (fresh - advertised): mean {report['e2e']['paired_mean_delta_ms']} ms, "
             f"fresh faster {report['e2e']['fresh_faster']} / slower {report['e2e']['fresh_slower']}",
             "", "| link | λ advertised (Mbps) | λ fresh (Mbps) | Δ (Mbps) | ε (Mbps) | z_realized |",
             "|---|---|---|---|---|---|"]
    for row in report["top_links"]:
        lines.append(f"| {row['link']} | {1e-6*row['lambda_advertised_bps']:.3f} | "
                     f"{1e-6*row['lambda_fresh_bps']:.3f} | {1e-6*row['delta_bps']:.3f} | "
                     f"{1e-6*row['epsilon_bps']:.3f} | {row['z_realized']:.3f} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="+",
                    help="advertised_cell_dir:fresh_cell_dir (colon separated)")
    ap.add_argument("--json")
    ap.add_argument("--md")
    args = ap.parse_args()
    reports = []
    for pair in args.pairs:
        a, f = pair.split(":", 1)
        reports.append(compare(Path(a), Path(f)))
    doc = {"schema": "sq-realized-compare-batch/v1", "pairs": reports}
    if args.json:
        Path(args.json).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n",
                                   encoding="utf-8")
    if args.md:
        Path(args.md).write_text("\n".join(render(r) for r in reports),
                                 encoding="utf-8")
    print(json.dumps({"pairs": len(reports),
                      "same_trace_all": all(r["same_trace"] for r in reports)}, indent=1))


if __name__ == "__main__":
    main()
