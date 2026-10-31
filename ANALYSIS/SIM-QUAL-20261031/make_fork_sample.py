#!/usr/bin/env python3
"""Pre-registered fork sampling for same-continuation counterfactual replays.

Reads one analysed cell (decisions + target probe) and selects exactly ``--n``
consequential decisions for forced-action forks.  The stratum rules are frozen
here and in PROTOCOL-ROUND2; the script only applies them deterministically.

Strata (pre-registered, filled in this order):
  large_gamma      top quartile of Gamma = L*tau/m (local truth-slope estimator)
  small_margin     margin (used-state 1st-2nd) in the bottom decile
  large_proxy_R    proxy regret (truth-substituted) in the top decile
  sustained        part of the longest same-direction wrong-decision run
  random_control    uniform sample of the remaining flip decisions (fixed seed)

A decision is eligible iff:
  * kind == forward, >=2 candidates, chosen present in the truth-substituted J,
  * chosen != fresh (a real flip; geometry cells have none by construction),
  * the fresh direction is inside the decision's candidate set (so the forced
    action is legal at that branch point).

Output: JSON {"schema": ..., "cell": ..., "n": ..., "strata": {...},
"decisions": [{decision_id, pid, sat, t, chosen, fresh, gamma, margin_s,
proxy_regret_s, stratum}]}.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import defaultdict

from sq_policy_analyze import (decision_stats, load_decisions, load_truth_series,
                               local_slope, truth_probe_index)


def _gamma_and_margin(row, series, probe, policy):
    """Return (gamma, margin_used) for one decision; gamma may be None."""
    j_used = row["j_used"]
    ordered = sorted(j_used, key=lambda d: (j_used[d], row["directions"].index(d)))
    m_used = j_used[ordered[1]] - j_used[ordered[0]]
    gamma = None
    pa = probe.get(row["decision_id"], {}).get(ordered[0]) or {}
    pb = probe.get(row["decision_id"], {}).get(ordered[1]) or {}
    moment = pa.get("eval_target_s")
    if m_used > 1e-9 and moment is not None and row["measured_at"] is not None:
        sa = local_slope(series, pa.get("resource_id"), float(moment))
        sb = local_slope(series, pb.get("resource_id"), float(moment))
        if sa is not None and sb is not None:
            tau = row["t"] - float(row["measured_at"])
            gamma = abs(sa - sb) * tau / m_used
    return gamma, m_used


def _fresh(row, policy):
    jt = row["j_truth"]
    if len(jt) < 2:
        return None
    best = min(jt, key=lambda d: (jt[d], row["directions"].index(d)))
    if policy == "geometry":
        return row["policy_top"]
    if policy == "queue_greedy_hysteresis":
        last = row["last_direction"]
        eta = float(row["eta_s"] or 0.0)
        if (last is not None and last in jt
                and float(jt[last]) <= float(jt[best]) + eta):
            return last
    return best


def build(cell, n=12, seed=20261101):
    cfg = json.loads((cell / "arms" / "now" / "config.json").read_text())["resolved_config"]
    policy = cfg["time_alignment"]["state_policy"]
    rows = load_decisions(cell)
    series = load_truth_series(cell)
    probe = truth_probe_index(cell)
    flips = []
    for row in rows:
        fresh = _fresh(row, policy)
        if fresh is None or row["chosen"] == fresh:
            continue
        if fresh not in row["directions"]:
            continue
        jt = row["j_truth"]
        # the chosen direction can be absent from J_truth when its own target
        # probe row is missing; such a decision cannot be analysed (and must
        # not be silently given a zero regret)
        if row["chosen"] not in jt:
            continue
        gamma, margin = _gamma_and_margin(row, series, probe, policy)
        proxy_r = float(jt[row["chosen"]]) - min(jt.values())
        flips.append({
            "decision_id": row["decision_id"], "pid": row["pid"],
            "sat": row["sat"], "t": row["t"], "chosen": row["chosen"],
            "fresh": fresh, "gamma": gamma, "margin_s": margin,
            "proxy_regret_s": proxy_r, "directions": row["directions"],
        })
    if len(flips) < n:
        raise SystemExit(f"only {len(flips)} eligible flips; cannot sample {n}")
    # sustained: longest same-direction consecutive flip run per (sat,dst)
    streams = defaultdict(list)
    by_id = {r["decision_id"]: r for r in flips}
    for row in rows:
        streams[(row["sat"], row["dst"])].append(row)
    longest = []
    for key, seq in streams.items():
        seq.sort(key=lambda r: r["t"])
        run = []
        last_dir = None
        for row in seq:
            fresh = _fresh(row, policy)
            is_flip = (fresh is not None and row["chosen"] != fresh
                       and fresh in row["directions"])
            if is_flip and row["chosen"] == last_dir:
                run.append(row["decision_id"])
            elif is_flip:
                if len(run) >= 2:
                    longest.append((len(run), run))
                run = [row["decision_id"]]
                last_dir = row["chosen"]
            else:
                if len(run) >= 2:
                    longest.append((len(run), run))
                run = []
                last_dir = None
        if len(run) >= 2:
            longest.append((len(run), run))
    longest.sort(key=lambda item: (-item[0], item[1][0]))
    sustained_ids = []
    for _length, ids in longest:
        for did in ids:
            if did in by_id and did not in sustained_ids:
                sustained_ids.append(did)
    # stratum pools, deterministic ordering
    gammas = [r for r in flips if r["gamma"] is not None]
    gammas.sort(key=lambda r: (-r["gamma"], r["decision_id"]))
    gamma_pool = gammas[:max(n, len(gammas) // 4)]
    margins = sorted(flips, key=lambda r: (r["margin_s"], r["decision_id"]))
    margin_pool = margins[:max(n, len(margins) // 10)]
    regrets = sorted(flips, key=lambda r: (-r["proxy_regret_s"], r["decision_id"]))
    regret_pool = regrets[:max(n, len(regrets) // 10)]
    # uniform random control with a fixed seed / deterministic hash
    ordered = sorted(flips, key=lambda r: (hash((seed, r["decision_id"])) % 10**9,
                                           r["decision_id"]))
    random_pool = ordered
    wanted = [("large_gamma", gamma_pool, 4), ("small_margin", margin_pool, 3),
              ("large_proxy_R", regret_pool, 3),
              ("sustained", [by_id[i] for i in sustained_ids if i in by_id], 1)]
    picked = []
    seen = set()
    for name, pool, count in wanted:
        taken = 0
        for row in pool:
            if row["decision_id"] in seen:
                continue
            row = dict(row)
            row["stratum"] = name
            picked.append(row)
            seen.add(row["decision_id"])
            taken += 1
            if taken >= count:
                break
    for row in random_pool:
        if len(picked) >= n:
            break
        if row["decision_id"] in seen:
            continue
        row = dict(row)
        row["stratum"] = "random_control"
        picked.append(row)
        seen.add(row["decision_id"])
    picked = picked[:n]
    for row in picked:
        row.pop("directions", None)
    return {
        "schema": "sq-fork-sample/v1",
        "cell": cell.name,
        "policy": policy,
        "eligible_flips": len(flips),
        "n": len(picked),
        "strata": {name: sum(1 for r in picked if r["stratum"] == name)
                   for name, _p, _c in wanted + [("random_control", [], 0)]},
        "decisions": picked,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cell")
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    from pathlib import Path
    document = build(Path(args.cell), n=args.n)
    Path(args.out).write_text(json.dumps(document, indent=1, sort_keys=True) + "\n",
                              encoding="utf-8")
    print(json.dumps({"out": args.out, "n": document["n"],
                      "strata": document["strata"]}, indent=1))


if __name__ == "__main__":
    main()
