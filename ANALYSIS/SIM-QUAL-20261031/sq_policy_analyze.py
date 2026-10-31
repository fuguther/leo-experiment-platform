#!/usr/bin/env python3
"""Offline analysis for the 2026-10-31 four-policy stale-state matrix.

Reads only the artifacts a load_calibration cell writes (one directory per
cell, e.g. <run-dir>/<cell-id>/):

    arms/now/decisions.jsonl.gz    every forward decision + full TA audit
    arms/now/target_probe.jsonl    same-run truth at each candidate's target
    arms/now/result.json.gz        packet timings, fates, queues, control ledger
    arms/now/config.json           resolved config (policy + frozen parameters)
    trace-manifest.json            trace identity shared by the policy cells

Frozen semantics (PROTOCOL-20261031 section 7):

* flip = truth-substituted same-decision re-ranking, NOT a re-simulated
  counterfactual: a_fresh is the argmin of the SAME policy cost after replacing
  the queue state the policy actually used with the probe's truth work-ahead.
  For ``geometry`` the cost has no state term, so flip is ZERO by construction
  (the negative control); anything else there is a bug in this script.
* J_truth = j_used_s - resource_work_used_s + truth.work_ahead_bits / rate
  for queue-aware policies; geometry keeps J_truth = j_used_s.
* hysteresis applies the recorded last direction + recorded eta to the truth J.
* regret R = J_truth(chosen) - min_a J_truth(a) >= 0.
* W = consecutive-wrong-decision duration inside one (sat, dst) decision
  stream: from a flip decision to the first later non-flip decision of that
  stream (censored at the horizon).
* L = |d(Q_a/rate_a)/dt - d(Q_b/rate_b)/dt| estimated locally from the probe
  truth series (linear fit over +-0.25 s), and Gamma = L * tau / m_truth.
* tau = t_commit - used_state.measured_at (AoI of the chosen candidate).

Usage:
    python3 sq_policy_analyze.py cell   <cell-dir> [--json F] [--md F]
    python3 sq_policy_analyze.py matrix <cell-dir> [<cell-dir> ...] [--json F] [--md F]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import defaultdict

ISL = "isl:"


# ----------------------------------------------------------------- helpers
def qstats(values, points=(0.5, 0.9, 0.95, 0.99), scale=1.0):
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return {}
    clean.sort()
    n = len(clean)
    return {f"p{int(p * 100)}": scale * clean[min(n - 1, int(round(p * (n - 1))))]
            for p in points}


def _mean(values):
    clean = [float(v) for v in values if v is not None]
    return (sum(clean) / len(clean)) if clean else None


def _timeline(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def _cells_arms(cell):
    return cell / "arms" / "now"


# ------------------------------------------------------------- decision log
def load_decisions(cell):
    """Rows with >=2 candidates that were scored by the policy layer."""
    base = _cells_arms(cell)
    probe = defaultdict(dict)
    for row in _timeline(base / "target_probe.jsonl"):
        if row.get("identity_source") != "used_state":
            continue
        if row.get("arm") != "now":
            continue
        probe[row["decision_id"]][row["direction"]] = row
    rows = []
    with gzip.open(base / "decisions.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            decision = json.loads(line)
            if decision.get("kind") != "forward":
                continue
            directions = decision.get("candidates") or []
            if len(directions) < 2:
                continue
            audit = decision["observation_at_start"]["time_alignment"]
            policy = audit.get("state_policy") or {}
            per_dir = policy.get("per_direction") or {}
            used = audit.get("used_state") or {}
            truth = probe.get(decision["decision_id"], {})
            queue_aware = policy.get("policy") in (
                "queue_greedy", "queue_greedy_hysteresis",
                "queue_greedy_ewma", "legacy")
            j_used = {}
            j_truth = {}
            for direction in directions:
                entry = per_dir.get(direction) or {}
                j = entry.get("j_used_s")
                work = entry.get("resource_work_used_s")
                rate = entry.get("service_rate_bps")
                if j is None or work is None or rate is None or float(rate) <= 0:
                    continue
                j_used[direction] = float(j)
                trow = truth.get(direction)
                if trow is None:
                    continue
                bits = float(trow["truth"]["work_ahead_bits"])
                j_truth[direction] = (float(j) - float(work) + bits / float(rate)
                                      if queue_aware else float(j))
            if len(j_used) < 2:
                continue
            order_used = sorted(j_used, key=lambda d: (j_used[d], directions.index(d)))
            chosen = decision.get("chosen")
            rows.append({
                "decision_id": decision["decision_id"],
                "t": float(decision["t"]),
                "pid": decision.get("pid"),
                "sat": decision.get("sat"),
                "dst": str(decision.get("dst")),
                "chosen": chosen,
                "directions": directions,
                "j_used": j_used,
                "j_truth": j_truth,
                "policy_top": order_used[0],
                "last_direction": policy.get("last_direction"),
                "eta_s": policy.get("hysteresis_eta_s"),
                "measured_at": (used.get(chosen) or {}).get("measured_at"),
                "received_at": (used.get(chosen) or {}).get("received_at"),
            })
    return rows


# --------------------------------------------------------------- truth slopes
def load_truth_series(cell):
    series = defaultdict(list)
    for row in _timeline(_cells_arms(cell) / "target_probe.jsonl"):
        if row.get("identity_source") != "used_state":
            continue
        rid = row.get("resource_id")
        rate = row.get("service_rate_bps")
        if rid is None or not rate or float(rate) <= 0:
            continue
        series[rid].append((float(row["eval_target_s"]),
                            float(row["truth"]["work_ahead_bits"]),
                            float(rate)))
    uniq = {}
    for rid, values in series.items():
        seen = {}
        for t, bits, rate in values:
            seen[round(t, 9)] = (bits, rate)
        uniq[rid] = sorted((t, bits, rate) for t, (bits, rate) in seen.items())
    return uniq


def local_slope(series, rid, moment, half_window=0.25):
    """d(bits/rate)/dt near ``moment`` (s/s); None when under-determined."""
    points = series.get(rid)
    if not points:
        return None
    import bisect
    times = [p[0] for p in points]
    lo = bisect.bisect_left(times, moment - half_window)
    hi = bisect.bisect_right(times, moment + half_window)
    seg = [p for p in points[lo:hi] if p[2] > 0]
    if len(seg) < 3 or (seg[-1][0] - seg[0][0]) < 0.05:
        return None
    xs = [p[0] for p in seg]
    ys = [p[1] for p in seg]
    rate = seg[0][2]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den / rate


def truth_probe_index(cell):
    index = defaultdict(dict)
    for row in _timeline(_cells_arms(cell) / "target_probe.jsonl"):
        if row.get("identity_source") != "used_state":
            continue
        index[row["decision_id"]][row["direction"]] = row
    return index


# ------------------------------------------------------------------ cell stats
def _fresh_action(row, policy):
    jt = row["j_truth"]
    if len(jt) < 2:
        return None
    best = min(jt, key=lambda d: (jt[d], row["directions"].index(d)))
    if policy == "geometry":
        return row["policy_top"]
    if policy == "queue_greedy_hysteresis":
        last = row["last_direction"]
        eta_s = float(row["eta_s"] or 0.0)
        if (last is not None and last in jt
                and float(jt[last]) <= float(jt[best]) + eta_s):
            return last
    return best


def decision_stats(cell):
    cfg = json.loads((_cells_arms(cell) / "config.json").read_text())["resolved_config"]
    ta = cfg["time_alignment"]
    policy = ta.get("state_policy")
    eta = ta.get("hysteresis_eta_s")
    rows = load_decisions(cell)
    series = load_truth_series(cell)
    probe = truth_probe_index(cell)

    aoi, gen2rx, rx2dec = [], [], []
    margins_used, margins_truth = [], []
    flips, regrets, gammas, flip_gamma = [], [], [], []
    switch_pairs = switch_moves = 0
    streams = defaultdict(list)
    for row in rows:
        streams[(row["sat"], row["dst"])].append(row)
        if row["measured_at"] is not None:
            aoi.append(row["t"] - float(row["measured_at"]))
        if row["received_at"] is not None and row["measured_at"] is not None:
            gen2rx.append(float(row["received_at"]) - float(row["measured_at"]))
        if row["received_at"] is not None:
            rx2dec.append(row["t"] - float(row["received_at"]))
        ordered = sorted(row["j_used"].values())
        margins_used.append(ordered[1] - ordered[0])
        jt = row["j_truth"]
        if len(jt) < 2:
            continue
        ordered_truth = sorted(jt.values())
        m_truth = ordered_truth[1] - ordered_truth[0]
        margins_truth.append(m_truth)
        fresh = _fresh_action(row, policy)
        flipped = row["chosen"] != fresh
        if flipped:
            flips.append(row)
        if row["chosen"] in jt:
            regrets.append(float(jt[row["chosen"]]) - float(ordered_truth[0]))
        # Gamma couples the margin the DECISION actually had (used-state top-1
        # vs top-2) with how fast that pair's relative state changed.  Using
        # the truth-best/fresh pair would be circular: a decision can only flip
        # when the used and truth orderings differ, which is not what Gamma is
        # supposed to predict.
        used_order = sorted(row["j_used"],
                            key=lambda d: (row["j_used"][d],
                                           row["directions"].index(d)))
        if len(used_order) >= 2:
            m_used = row["j_used"][used_order[1]] - row["j_used"][used_order[0]]
            pa = probe.get(row["decision_id"], {}).get(used_order[0]) or {}
            pb2 = probe.get(row["decision_id"], {}).get(used_order[1]) or {}
            ra, rb = pa.get("resource_id"), pb2.get("resource_id")
            moment = pa.get("eval_target_s")
            if (m_used > 1e-9 and ra and rb and moment is not None
                    and row["measured_at"] is not None):
                sa = local_slope(series, ra, float(moment))
                sb = local_slope(series, rb, float(moment))
                if sa is not None and sb is not None:
                    tau = row["t"] - float(row["measured_at"])
                    gammas.append(abs(sa - sb) * tau / m_used)
                    flip_gamma.append(1 if flipped else 0)
    for seq in streams.values():
        seq.sort(key=lambda r: r["t"])
        for a, b in zip(seq, seq[1:]):
            switch_pairs += 1
            switch_moves += (a["chosen"] != b["chosen"])
    W, censored = [], 0
    for seq in streams.values():
        seq.sort(key=lambda r: r["t"])
        flags = []
        for row in seq:
            fresh = _fresh_action(row, policy)
            flags.append(None if fresh is None else (row["chosen"] != fresh))
        i = 0
        while i < len(seq):
            if flags[i] is True:
                start = seq[i]["t"]
                j = i + 1
                while j < len(seq) and flags[j] is True:
                    j += 1
                if j < len(seq):
                    W.append(seq[j]["t"] - start)
                else:
                    censored += 1
                i = j
            else:
                i += 1
    bins = [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, math.inf)]
    gamma_bins = []
    for lo, hi in bins:
        idx = [i for i, g in enumerate(gammas) if lo <= g < hi]
        gamma_bins.append({"bin": f"[{lo:g},{hi:g})", "n": len(idx),
                           "flip_rate": (sum(flip_gamma[i] for i in idx) / len(idx))
                           if idx else None})
    regret_pos = [r for r in regrets if r > 0]
    return {
        "schema": "sq-policy-decision-stats/v1",
        "policy": policy,
        "hysteresis_eta_s": eta if policy == "queue_greedy_hysteresis" else None,
        "ewma_alpha": ta.get("ewma_alpha") if policy == "queue_greedy_ewma" else None,
        "config_sha256": _config_sha(cell),
        "n_decisions_multi": len(rows),
        "n_decisions_with_truth": sum(1 for r in rows if len(r["j_truth"]) >= 2),
        "aoi_ms": qstats(aoi, scale=1000.0),
        "aoi_decomposition_ms": {
            "generated_to_received": qstats(gen2rx, scale=1000.0),
            "received_to_decision": qstats(rx2dec, scale=1000.0)},
        "margin_used_ms": qstats(margins_used, scale=1000.0),
        "margin_truth_ms": qstats(margins_truth, scale=1000.0),
        "flip": {"semantics": "truth_substituted_rerank_not_resimulated_counterfactual",
                 "n": len(flips),
                 "rate": (len(flips) / len(rows)) if rows else None},
        "regret_ms": {
            "mean": (1000.0 * _mean(regrets)) if regrets else None,
            "p50": 1000.0 * qstats(regrets).get("p50", 0.0) if regrets else None,
            "p90": 1000.0 * qstats(regrets).get("p90", 0.0) if regrets else None,
            "p99": 1000.0 * qstats(regrets).get("p99", 0.0) if regrets else None,
            "frac_gt_0": (len(regret_pos) / len(regrets)) if regrets else None,
            "frac_gt_1ms": (sum(1 for r in regrets if r > 1e-3) / len(regrets))
            if regrets else None},
        "switch": {"rate": (switch_moves / switch_pairs) if switch_pairs else None,
                   "pairs": switch_pairs},
        "wrong_duration_W_ms": {
            "p50": 1000.0 * qstats(W).get("p50", 0.0) if W else None,
            "p90": 1000.0 * qstats(W).get("p90", 0.0) if W else None,
            "n": len(W), "censored": censored},
        "gamma": {
            "definition": "Gamma = |d(Q_a/rate_a)/dt - d(Q_b/rate_b)/dt| * tau / m_truth",
            "median": statistics.median(gammas) if gammas else None,
            "n": len(gammas), "bins": gamma_bins},
    }


def _config_sha(cell):
    try:
        return json.loads((_cells_arms(cell) / "config.json").read_text())["sha256"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------- network stats
def network_stats(cell):
    with gzip.open(_cells_arms(cell) / "result.json.gz", "rt", encoding="utf-8") as fh:
        result = json.load(fh)
    packets = (result.get("congestion_metrics") or {}).get("packets") or {}
    e2e = [p["e2e_s"] for p in packets.values() if p.get("e2e_s") is not None]
    queue = [p.get("total_queue_wait_s") for p in packets.values()
             if p.get("total_queue_wait_s") is not None]
    hops = [len(rec.get("path") or []) for rec in (result.get("deliveries") or {}).values()]
    totals = result.get("totals") or {}
    control = result.get("control") or {}
    counters = control.get("counters") or {}
    duration = float(result.get("stop_time_s") or 0.0)
    ctrl_transmissions = int(counters.get("transmission_started") or 0)
    ctrl_bits, isl_rate = 8000.0, 100e6
    try:
        cfg = json.loads((_cells_arms(cell) / "config.json").read_text())["resolved_config"]
        ctrl_bits = float(cfg["control_plane"]["packet_bits"])
        isl_rate = float(cfg["links"]["isl_rate_mbps"]) * 1e6
    except (OSError, KeyError, json.JSONDecodeError):
        pass
    n_links = 364
    return {
        "schema": "sq-policy-network-stats/v1",
        "e2e_ms": {"mean": 1000.0 * _mean(e2e) if e2e else None,
                   **qstats(e2e, scale=1000.0)},
        "queue_wait_ms": qstats(queue, scale=1000.0),
        "delivery": {"delivered": (result.get("fate_counts") or {}).get("DELIVERED"),
                     "offered": (result.get("congestion_metrics") or {}).get("offered_packets"),
                     "fate_counts": result.get("fate_counts")},
        "queue_area_bits_s": result.get("queue_area_bits_s"),
        "delivered_bits": totals.get("delivered_bits"),
        "offered_bits": totals.get("offered_bits"),
        "goodput_mbps_window": ((totals.get("delivered_bits") or 0.0) / duration / 1e6
                                if duration else None),
        "hops_mean": _mean(hops), "hops_max": max(hops) if hops else None,
        "control": {"bits": control.get("bits"), "counters": counters,
                    "fate_counts": control.get("fate_counts"),
                    "service_share_estimate": (
                        ctrl_transmissions * ctrl_bits / isl_rate / (n_links * duration)
                        if duration and isl_rate else None)},
    }


# ------------------------------------------------------------- cross-cell pair
def _trace_sha(cell):
    try:
        return json.loads((cell / "trace-manifest.json").read_text())["source_trace_sha256"]
    except (OSError, KeyError, json.JSONDecodeError):
        return None


def _deliveries(cell):
    with gzip.open(_cells_arms(cell) / "result.json.gz", "rt", encoding="utf-8") as fh:
        result = json.load(fh)
    packets = (result.get("congestion_metrics") or {}).get("packets") or {}
    return {int(pid): p.get("e2e_s") for pid, p in packets.items()
            if p.get("e2e_s") is not None}


def matrix_report(cells, baseline=None):
    per_cell = {}
    for cell in cells:
        dec = decision_stats(cell)
        per_cell[cell.name] = {"cell": str(cell), "policy": dec["policy"],
                               "trace_source_sha256": _trace_sha(cell),
                               "decisions": dec, "network": network_stats(cell)}
    traces = defaultdict(list)
    for name, block in per_cell.items():
        traces[block["trace_source_sha256"]].append(name)
    names = list(per_cell)
    base = baseline if baseline in per_cell else names[0]
    base_del = _deliveries(cells[names.index(base)])
    base_sha = _trace_sha(cells[names.index(base)])
    pairing = []
    for name in names:
        cell = cells[names.index(name)]
        del_this = _deliveries(cell)
        common = sorted(set(base_del) & set(del_this))
        deltas = [del_this[pid] - base_del[pid] for pid in common]
        pairing.append({
            "cell": name, "baseline": base,
            "same_trace": _trace_sha(cell) == base_sha,
            "paired_packets": len(common),
            "mean_delta_ms": 1000.0 * _mean(deltas) if deltas else None,
            "p50_delta_ms": 1000.0 * qstats(deltas).get("p50", 0.0) if deltas else None,
            "faster": sum(1 for d in deltas if d < -1e-9),
            "slower": sum(1 for d in deltas if d > 1e-9)})
    return {"schema": "sq-policy-matrix/v1", "trace_groups": dict(traces),
            "cells": per_cell, "paired_vs_baseline": pairing}


# --------------------------------------------------------------------- output
def render_markdown(report):
    lines = ["# Four-policy stale-state matrix (diagnostic)", "",
             "| cell | policy | multi | flip | R mean | R p90 | AoI p50 | m p50 | "
             "switch | W p50 | E2E mean | E2E p95 | isl_data area |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    if "cells" in report:
        for name, block in report["cells"].items():
            d, n = block["decisions"], block["network"]
            def f(v, fmt):
                return fmt % v if v is not None else "n/a"
            lines.append(
                f"| {name} | {d['policy']} | {d['n_decisions_multi']} | "
                f"{f(d['flip']['rate'], '%.4f')} | {f(d['regret_ms']['mean'], '%.2f')} | "
                f"{f(d['regret_ms']['p90'], '%.2f')} | {f(d['aoi_ms'].get('p50'), '%.1f')} | "
                f"{f(d['margin_used_ms'].get('p50'), '%.2f')} | "
                f"{f(d['switch']['rate'], '%.3f')} | "
                f"{f(d['wrong_duration_W_ms']['p50'], '%.1f')} | "
                f"{f(n['e2e_ms']['mean'], '%.1f')} | {f(n['e2e_ms'].get('p95'), '%.1f')} | "
                f"{f((n['queue_area_bits_s'] or {}).get('isl_data'), '%.0f')} |")
        lines += ["", "## Gamma bins vs flip rate", ""]
        for name, block in report["cells"].items():
            g = block["decisions"]["gamma"]
            lines.append(f"**{name}** (median Gamma "
                         f"{'n/a' if g['median'] is None else format(g['median'], '.3f')}, "
                         f"n={g['n']})")
            lines += ["", "| bin | n | flip rate |", "|---|---|---|"]
            for b in g["bins"]:
                fr = "n/a" if b["flip_rate"] is None else format(b["flip_rate"], ".3f")
                lines.append(f"| {b['bin']} | {b['n']} | {fr} |")
            lines.append("")
        lines += ["## Paired E2E vs baseline", "",
                  "| cell | baseline | same trace | paired | mean delta ms | faster | slower |",
                  "|---|---|---|---|---|---|---|"]
        for p in report["paired_vs_baseline"]:
            md = "n/a" if p["mean_delta_ms"] is None else format(p["mean_delta_ms"], ".2f")
            lines.append(f"| {p['cell']} | {p['baseline']} | {p['same_trace']} | "
                         f"{p['paired_packets']} | {md} | {p['faster']} | {p['slower']} |")
    else:
        d, n = report["decisions"], report["network"]
        lines = ["# Cell report", "",
                 f"- policy: {d['policy']}",
                 f"- decisions (multi-candidate): {d['n_decisions_multi']}",
                 f"- with truth: {d['n_decisions_with_truth']}",
                 f"- flip rate: {d['flip']['rate']}",
                 f"- regret mean / p90 ms: {d['regret_ms']['mean']} / {d['regret_ms']['p90']}",
                 f"- AoI p50/p90 ms: {d['aoi_ms'].get('p50')} / {d['aoi_ms'].get('p90')}",
                 f"- E2E mean/p95 ms: {n['e2e_ms']['mean']} / {n['e2e_ms'].get('p95')}",
                 f"- isl_data queue area: {(n['queue_area_bits_s'] or {}).get('isl_data')}"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="four-policy stale-state analysis")
    parser.add_argument("command", choices=["cell", "matrix"])
    parser.add_argument("cells", nargs="+")
    parser.add_argument("--json")
    parser.add_argument("--md")
    parser.add_argument("--baseline")
    args = parser.parse_args(argv)
    from pathlib import Path
    paths = [Path(c) for c in args.cells]
    if args.command == "cell":
        report = {"decisions": decision_stats(paths[0]),
                  "network": network_stats(paths[0])}
    else:
        report = matrix_report(paths, baseline=args.baseline)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    print(json.dumps(report, indent=1, sort_keys=True)[:4000])


if __name__ == "__main__":
    main()
