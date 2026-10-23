"""Four-arm comparison for the warmed-up ISL-1 Mbps cohort.

Fairness filter: a decision is comparable only when all four arms saw the same
instant (sat, t_decision_start) and exactly the same legal direction set. The
earlier (pid, decision_id) pairing is NOT sufficient, because the arms can reach
different decision instances, which made two arms look identical when they were
never compared on the same observation.

Reports per-arm business outcome for the whole run and for the predeclared
emission cohort, then the decision-level divergence and the work-shift versus
ranking-margin statistics.

Usage:
  python3 -B analyze_warmup_arms.py --evidence-root DIR --out FILE
"""
from __future__ import annotations

import argparse
import gzip
import json
import statistics
from pathlib import Path

ARMS = ("stale", "now", "common", "candidate")
COHORT = (6.0, 8.0)
DEADLINE_S = 4.0


def rows(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def one(root: Path, pattern: str) -> Path:
    found = list(root.glob(pattern))
    if len(found) != 1:
        raise ValueError(f"expected one {pattern} under {root}, got {len(found)}")
    return found[0]


def load_arm(root: Path) -> dict:
    result = json.loads(gzip.decompress(
        one(root, "cal/*/arms/*/result.json.gz").read_bytes()))
    decisions = {}
    for row in rows(one(root, "cal/*/arms/*/decisions.jsonl.gz")):
        if row.get("kind") != "forward":
            continue
        obs = row["observation_at_start"]
        ta = obs.get("time_alignment") or {}
        key = (row["sat"], row["t_decision_start"])
        decisions[key] = {
            "legal": tuple(obs.get("legal_directions") or ()),
            "chosen": row.get("chosen"),
            "ranking": tuple(ta.get("ranking") or ()),
            "applied_order": tuple(ta.get("applied_order") or ()),
            "scores": ta.get("scores") or {},
            "pid": row.get("pid"),
            "t": row.get("t"),
        }
    trace = {int(r["packet_id"]): float(r["emit_time_s"])
             for r in rows(one(root, "cal/*/trace.jsonl.gz"))}
    return {"result": result, "decisions": decisions, "trace": trace}


def outcome(arm: dict, cohort: dict) -> dict:
    result = arm["result"]
    deliveries = result["deliveries"]
    delays = {}
    for pid, at in arm["trace"].items():
        item = deliveries.get(str(pid))
        if isinstance(item, dict):
            delays[pid] = item["delivered_at"] - at
    def stats(pids):
        miss = sum(1 for p in pids if p not in delays or delays[p] > DEADLINE_S)
        present = [delays[p] for p in pids if p in delays]
        return {
            "offered": len(pids),
            "delivered": len(present),
            "deadline_miss": miss,
            "deadline_miss_ratio": miss / len(pids) if pids else None,
            "capped_delay_D4": (sum(min(delays.get(p, DEADLINE_S), DEADLINE_S)
                                    / DEADLINE_S for p in pids) / len(pids)
                                if pids else None),
            "delivered_only_mean_delay_s": (statistics.fmean(present)
                                            if present else None),
        }
    return {"whole_run": stats(sorted(arm["trace"])),
            "cohort_6_8": stats(sorted(cohort)),
            "queue_area_bits_s": result["queue_area_bits_s"],
            "fate_counts": result.get("fate_counts")}


def comparable(arms: dict) -> list:
    keys = set(arms[ARMS[0]]["decisions"])
    for a in ARMS[1:]:
        keys &= set(arms[a]["decisions"])
    out = []
    for key in sorted(keys):
        entries = [arms[a]["decisions"][key] for a in ARMS]
        legal = entries[0]["legal"]
        if len(legal) < 2 or any(e["legal"] != legal for e in entries):
            continue
        if any(not set(legal).issubset(e["scores"]) for e in entries):
            continue
        out.append((key, entries))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--evidence-root", required=True)
    p.add_argument("--run-prefix", default="t1-warmup-isl1")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    root = Path(args.evidence_root)
    arms = {}
    for a in ARMS:
        matches = sorted(root.glob(f"{args.run_prefix}-{a}-*"))
        if not matches:
            raise ValueError(f"missing arm {a} under {root}")
        arms[a] = load_arm(matches[0])

    cohort = {pid: t for pid, t in arms["candidate"]["trace"].items()
              if COHORT[0] <= t < COHORT[1]}
    pairs = comparable(arms)
    chosen_div = {a: 0 for a in ARMS}
    ranking_div = 0
    order_div = 0
    flips = []
    for key, entries in pairs:
        chosen = {e["chosen"] for e in entries}
        rankings = {e["ranking"] for e in entries}
        orders = {e["applied_order"] for e in entries}
        if len(chosen) > 1:
            for a, e in zip(ARMS, entries):
                if e["chosen"] != entries[0]["chosen"]:
                    chosen_div[a] += 1
            if len(flips) < 25:
                flips.append({"sat": key[0], "t_decision_start": key[1],
                              "chosen": {a: e["chosen"] for a, e in zip(ARMS, entries)},
                              "ranking": {a: list(e["ranking"]) for a, e in zip(ARMS, entries)}})
        if len(rankings) > 1:
            ranking_div += 1
        if len(orders) > 1:
            order_div += 1

    shifts, margins = [], []
    for key, entries in pairs:
        totals = []
        for e in entries:
            vals = sorted(v["total_s"] for d, v in e["scores"].items()
                          if d in e["legal"] and v.get("missing") == []
                          and isinstance(v.get("total_s"), (int, float)))
            if len(vals) >= 2:
                totals.append(vals[1] - vals[0])
        if totals:
            margins.append(min(totals))
        works = []
        for d in entries[0]["legal"]:
            w = [e["scores"][d]["terms"].get("resource_work_s") for e in entries
                 if isinstance(e["scores"].get(d), dict)]
            w = [x for x in w if isinstance(x, (int, float))]
            if len(w) >= 2:
                works.append(max(w) - min(w))
        if works:
            shifts.append(max(works))

    # Trajectory divergence does not show up as a different choice at the same
    # decision: once two arms act differently the runs stop visiting the same
    # decision instances, so the shared-key comparison above collapses to the
    # common prefix. Count each arm's own decision set and the keys unique to
    # one arm to expose that divergence instead of hiding it.
    key_sets = {a: set(arms[a]["decisions"]) for a in ARMS}
    union = set().union(*key_sets.values())
    decision_sets = {
        "per_arm_forward_decisions": {a: len(key_sets[a]) for a in ARMS},
        "union_forward_decisions": len(union),
        "shared_by_all_four": len(set.intersection(*key_sets.values())),
        "unique_to_one_arm": {a: len(key_sets[a] - set().union(
            *(key_sets[b] for b in ARMS if b != a))) for a in ARMS},
    }

    report = {
        "schema": "warmup-four-arm/v1",
        "claimable": False,
        "decision_sets": decision_sets,
        "fairness_filter": "same (sat, t_decision_start) and identical legal set in all four arms",
        "runs": {a: matches_name for a, matches_name in
                 ((a, sorted(root.glob(f"{args.run_prefix}-{a}-*"))[0].name) for a in ARMS)},
        "comparable_decisions": len(pairs),
        "chosen_divergence_count": chosen_div,
        "ranking_divergence_decisions": ranking_div,
        "applied_order_divergence_decisions": order_div,
        "divergence_samples": flips,
        "work_shift_max_s": max(shifts) if shifts else None,
        "work_shift_median_s": statistics.median(shifts) if shifts else None,
        "margin_median_s": statistics.median(margins) if margins else None,
        "decisions_where_shift_exceeds_margin": sum(
            1 for s, m in zip(shifts, margins) if s > m),
        "outcomes": {a: outcome(arms[a], cohort) for a in ARMS},
        "cohort_size": len(cohort),
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                              encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items()
                      if k not in ("divergence_samples", "outcomes")},
                     ensure_ascii=False, indent=1))
    for a in ARMS:
        o = report["outcomes"][a]
        print(a, "whole", o["whole_run"], "cohort", o["cohort_6_8"]["deadline_miss_ratio"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
