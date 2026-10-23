"""Four-arm comparison inside the predeclared warmed window [12, 24).

Fairness filter: a decision is comparable only when all four arms saw the same
instant (sat, t_decision_start) and exactly the same legal direction set.

Reports, for the predeclared evaluation cohort only:
  * per-arm business outcome (D4 miss, delivered, mean/median delay)
  * how many comparable decisions exist at all (coverage of the mechanism)
  * chosen-direction divergence under the fairness filter
  * how often the candidate arm's own choice differs from now and from common

Usage:
  python3 analyze_warmed_arms.py stale=<run> now=<run> common=<run> candidate=<run>
"""
from __future__ import annotations

import gzip
import json
import statistics
import sys
from pathlib import Path

ARMS = ("stale", "now", "common", "candidate")
COHORT = (12.0, 24.0)
DEADLINE_S = 4.0
BASE = Path("/data/论文/leo-t1-wt/runs")


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def one(root, pattern):
    found = list(root.glob(pattern))
    if len(found) != 1:
        raise ValueError("expected one %s under %s, got %d" % (pattern, root, len(found)))
    return found[0]


def load_arm(root):
    result = json.loads(gzip.decompress(one(root, "cal/*/arms/*/result.json.gz").read_bytes()))
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
            "scores": ta.get("scores") or {},
            "ranking": tuple(ta.get("ranking") or ()),
        }
    trace = {int(r["packet_id"]): float(r["emit_time_s"])
             for r in rows(one(root, "cal/*/trace.jsonl.gz"))}
    return {"result": result, "decisions": decisions, "trace": trace}


def outcome(arm, lo, hi):
    deliveries = arm["result"]["deliveries"]
    pids = sorted(p for p, t in arm["trace"].items() if lo <= t < hi)
    miss, delays = 0, []
    for p in pids:
        item = deliveries.get(str(p)) or deliveries.get(p)
        if isinstance(item, dict):
            delay = item["delivered_at"] - arm["trace"][p]
            delays.append(delay)
            if delay > DEADLINE_S:
                miss += 1
        else:
            miss += 1
    return {
        "offered": len(pids),
        "delivered": len(delays),
        "d4_miss": miss,
        "d4_miss_pct": round(100.0 * miss / len(pids), 3) if pids else None,
        "mean_delay_s": round(statistics.fmean(delays), 4) if delays else None,
        "median_delay_s": round(statistics.median(delays), 4) if delays else None,
    }


def main(argv):
    spec = dict(a.split("=", 1) for a in argv)
    missing = [a for a in ARMS if a not in spec]
    if missing:
        raise SystemExit("missing arm runs: %s" % missing)
    arms = {a: load_arm(BASE / spec[a]) for a in ARMS}

    keys = set(arms[ARMS[0]]["decisions"])
    for a in ARMS[1:]:
        keys &= set(arms[a]["decisions"])
    pairs = []
    for key in sorted(keys):
        entries = [arms[a]["decisions"][key] for a in ARMS]
        legal = entries[0]["legal"]
        if len(legal) < 2 or any(e["legal"] != legal for e in entries):
            continue
        if any(not set(legal).issubset(e["scores"]) for e in entries):
            continue
        pairs.append((key, entries))

    def in_cohort(key):
        return COHORT[0] <= float(key[1]) < COHORT[1]

    cohort_pairs = [(k, e) for k, e in pairs if in_cohort(k)]

    chosen_div = 0
    cand_vs_now = 0
    cand_vs_common = 0
    flips = []
    for key, entries in cohort_pairs:
        pick = {a: e["chosen"] for a, e in zip(ARMS, entries)}
        if len(set(pick.values())) > 1:
            chosen_div += 1
            if len(flips) < 20:
                flips.append({"sat": key[0], "t": round(float(key[1]), 4), "chosen": pick})
        if pick["candidate"] != pick["now"]:
            cand_vs_now += 1
        if pick["candidate"] != pick["common"]:
            cand_vs_common += 1

    report = {
        "schema": "warmed-four-arm/v1",
        "claimable": False,
        "warning": "diagnostic run; not formal evidence",
        "runs": spec,
        "fairness_filter": "same (sat, t_decision_start) and identical legal set in all four arms",
        "cohort_s": list(COHORT),
        "comparable_decisions_all_time": len(pairs),
        "comparable_decisions_in_cohort": len(cohort_pairs),
        "chosen_divergence_in_cohort": chosen_div,
        "candidate_differs_from_now_in_cohort": cand_vs_now,
        "candidate_differs_from_common_in_cohort": cand_vs_common,
        "outcome_cohort": {a: outcome(arms[a], *COHORT) for a in ARMS},
        "outcome_warmup": {a: outcome(arms[a], 0.0, COHORT[0]) for a in ARMS},
        "divergence_samples": flips,
    }
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1:])
