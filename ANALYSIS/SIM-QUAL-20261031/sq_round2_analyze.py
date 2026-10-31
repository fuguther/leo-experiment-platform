#!/usr/bin/env python3
"""Round-2 aggregation: calibrated regimes x policies x seeds, and the
strong-sync sweep.

Reads the per-cell evidence directories produced by the Round-2 release and
prints/JSON-dumps the pre-registered quantities:

  * per-cell: flip (truth-substituted), proxy regret, per-(sat,dst) W/switch,
    AoI, margins, E2E mean/p50/p95/p99, delivery, queue areas, hops,
    control bits / expired / service-share estimate.
  * regime x policy: mean and range across seeds.
  * paired E2E within one seed between two policies (same trace -> same pid
    for delivered packets), so policy differences are measured pairwise.
  * sync sweep: AoI / flip / regret / E2E / control overhead per advertisement
    interval.

Cell ids follow ``m2-<regime>-<policy>-s<seed>`` or
``m2-sync<advert_ms>-<regime>-<policy>-s<seed>``; the mapping is parsed, never
guessed from the run id.  A cell that is missing is reported, not skipped
silently.

Usage:
    python3 sq_round2_analyze.py --json out.json --md out.md <cell-dir> [...]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from sq_policy_analyze import decision_stats, network_stats, _deliveries


CELL_RE = re.compile(
    r"^(?P<prefix>m2|f2)-(?:(?P<sync>sync\d+)-)?(?P<regime>[a-z0-9]+)-"
    r"(?P<policy>[a-z]+)-s(?P<seed>\d+)$")


def parse_cell(name):
    match = CELL_RE.match(name)
    if not match:
        return None
    out = match.groupdict()
    out["advert_ms"] = (None if out["sync"] is None
                        else int(out["sync"][4:]))
    return out


def collect(cell):
    dec = decision_stats(cell)
    net = network_stats(cell)
    meta = parse_cell(cell.name) or {"raw": cell.name}
    return {
        "cell": cell.name,
        "meta": meta,
        "decisions": dec,
        "network": net,
    }


def _range(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals),
            "n": len(vals)}


def aggregate(cells):
    groups = defaultdict(list)
    for block in cells:
        meta = block["meta"]
        key = (meta.get("sync"), meta.get("regime"), meta.get("policy"))
        groups[key].append(block)
    out = {}
    for (sync, regime, policy), blocks in sorted(
            groups.items(), key=lambda kv: tuple("" if x is None else x
                                                 for x in kv[0])):
        blocks.sort(key=lambda b: b["meta"].get("seed"))
        name = "sync{}|{}|{}".format(sync or "default", regime, policy)
        out[name] = {
            "seeds": [b["meta"].get("seed") for b in blocks],
            "cells": [b["cell"] for b in blocks],
            "flip_rate": _range([b["decisions"]["flip"]["rate"] for b in blocks]),
            "regret_mean_ms": _range(
                [b["decisions"]["regret_ms"]["mean"] for b in blocks]),
            "regret_p90_ms": _range(
                [b["decisions"]["regret_ms"]["p90"] for b in blocks]),
            "aoi_p50_ms": _range(
                [b["decisions"]["aoi_ms"].get("p50") for b in blocks]),
            "aoi_p90_ms": _range(
                [b["decisions"]["aoi_ms"].get("p90") for b in blocks]),
            "margin_p50_ms": _range(
                [b["decisions"]["margin_used_ms"].get("p50") for b in blocks]),
            "switch_rate": _range(
                [b["decisions"]["switch"]["rate"] for b in blocks]),
            "W_p50_ms": _range(
                [b["decisions"]["wrong_duration_W_ms"]["p50"] for b in blocks]),
            "e2e_mean_ms": _range(
                [b["network"]["e2e_ms"]["mean"] for b in blocks]),
            "e2e_p95_ms": _range(
                [b["network"]["e2e_ms"].get("p95") for b in blocks]),
            "e2e_p99_ms": _range(
                [b["network"]["e2e_ms"].get("p99") for b in blocks]),
            "delivered": _range(
                [(b["network"]["delivery"] or {}).get("delivered")
                 for b in blocks]),
            "isl_data_area": _range(
                [(b["network"]["queue_area_bits_s"] or {}).get("isl_data")
                 for b in blocks]),
            "control_offered_bits": _range(
                [((b["network"]["control"] or {}).get("bits") or {})
                 .get("offered") for b in blocks]),
            "control_expired": _range(
                [((b["network"]["control"] or {}).get("counters") or {})
                 .get("expired") for b in blocks]),
            "hops_mean": _range([b["network"]["hops_mean"] for b in blocks]),
        }
    return out


def paired(cells, policy_a, policy_b):
    """Paired E2E delta (b - a) per seed, same regime/sync/seed (same trace)."""
    keyed = {}
    for block in cells:
        meta = block["meta"]
        keyed[(meta.get("sync"), meta.get("regime"), meta.get("seed"),
               meta.get("policy"))] = block
    out = []
    for (sync, regime, seed, policy), block_a in keyed.items():
        if policy != policy_a:
            continue
        block_b = keyed.get((sync, regime, seed, policy_b))
        if block_b is None:
            continue
        del_a = _deliveries_from(block_a["cell"])
        del_b = _deliveries_from(block_b["cell"])
        common = sorted(set(del_a) & set(del_b))
        deltas = [del_b[pid] - del_a[pid] for pid in common]
        out.append({
            "sync": sync, "regime": regime, "seed": seed,
            "policies": [policy_a, policy_b],
            "paired_packets": len(common),
            "mean_delta_ms": (1000.0 * sum(deltas) / len(deltas)
                              if deltas else None),
            "faster_b": sum(1 for d in deltas if d < -1e-9),
            "slower_b": sum(1 for d in deltas if d > 1e-9),
        })
    return out


def _deliveries_from(cell_name):
    # the evidence dir is passed as the cell argument; keep a global map
    return _DELIVERIES_CACHE[cell_name]


_DELIVERIES_CACHE = {}


def render_markdown(report):
    lines = ["# Round-2 four-policy stale-state matrix", ""]
    lines += ["## Regime x policy aggregates (mean [min-max] over seeds)", "",
              "| group | seeds | flip | regret mean ms | AoI p50 ms | switch | "
              "W p50 ms | E2E mean ms | E2E p95 ms | isl_data area | ctrl Mbit |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, g in report["groups"].items():
        def fmt(key, spec):
            value = g.get(key)
            if not value:
                return "n/a"
            return (spec + " [" + spec + "-" + spec + "]") % (
                value["mean"], value["min"], value["max"])
        lines.append(
            f"| {name} | {g['seeds']} | {fmt('flip_rate','%.4f')} | "
            f"{fmt('regret_mean_ms','%.2f')} | {fmt('aoi_p50_ms','%.1f')} | "
            f"{fmt('switch_rate','%.3f')} | {fmt('W_p50_ms','%.1f')} | "
            f"{fmt('e2e_mean_ms','%.1f')} | {fmt('e2e_p95_ms','%.1f')} | "
            f"{fmt('isl_data_area','%.0f')} | "
            f"{(fmt('control_offered_bits','%.0f') if g.get('control_offered_bits') else 'n/a')} |")
    if report.get("paired"):
        lines += ["", "## Paired E2E within seed (b - a)", "",
                  "| regime | seed | a -> b | paired | mean delta ms | faster b | slower b |",
                  "|---|---|---|---|---|---|---|"]
        for row in report["paired"]:
            lines.append(
                f"| {row['regime']} | {row['seed']} | "
                f"{row['policies'][0]}->{row['policies'][1]} | "
                f"{row['paired_packets']} | "
                f"{'n/a' if row['mean_delta_ms'] is None else format(row['mean_delta_ms'], '.2f')} | "
                f"{row['faster_b']} | {row['slower_b']} |")
    if report.get("missing"):
        lines += ["", "## Missing cells", ""] + [f"- {m}" for m in report["missing"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cells", nargs="+")
    parser.add_argument("--json")
    parser.add_argument("--md")
    parser.add_argument("--paired", nargs=2, metavar=("A", "B"),
                        default=["geometry", "queue_greedy"])
    args = parser.parse_args()
    paths = [Path(c) for c in args.cells]
    blocks = []
    missing = []
    for path in paths:
        if not (path / "arms" / "now" / "decisions.jsonl.gz").is_file():
            missing.append(str(path))
            continue
        _DELIVERIES_CACHE[path.name] = _deliveries(path)
        blocks.append(collect(path))
    report = {"schema": "sq-round2/v1", "cells": blocks,
              "groups": aggregate(blocks),
              "paired": paired(blocks, args.paired[0], args.paired[1]),
              "missing": missing}
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n",
                                   encoding="utf-8")
    if args.md:
        Path(args.md).write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"cells": len(blocks), "missing": missing,
                      "groups": list(report["groups"])}, indent=1))


if __name__ == "__main__":
    main()
