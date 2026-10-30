#!/usr/bin/env python3
"""Offline truth-semantics analysis for ONE recorded routing trajectory.

Inputs (all produced by an existing run; no simulator is started here):
  --extract   gzip JSON extract of a t1 network-alignment run:
              arms[0].routing_audit_log.decision_records (sampled decisions)
  --qlevels   gz JSONL queue ledger (`leo-sim-queue-ledger/v1`, rows kind=level)
  --svcwin    gz JSONL link service windows (`leo-sim-service-window`, rows with
              link_id/start/end/rate_bps)
  --out       output JSON

What it computes, per sampled decision and per ISL candidate:
  t_g  : source measurement instant of the advertisement actually used
  t_r  : instant the advertisement arrived locally
  t_d  : decision instant (snapshot_at)
  T    : predicted arrival instant at the candidate's egress (eta target)
  Q(t) : EXACT simulator truth = isl_data(t) + isl_ctrl(t) + in-service residual
         reconstructed from the ledger; never read from a prediction.

  S0 = Q(t_g)   stale truth
  S1 = Q(t_d)   decision-time truth
  S2 = Q(T)     use-time (arrival) truth

Scores are re-priced with truth, holding every other term fixed:
  C_X = recorded_total - recorded resource_work_s + Q_X / service_rate
so the only difference between C_S0/C_S1/C_S2 is the state instant.

Everything here is DIAGNOSTIC; nothing is written back to the simulator.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import statistics
from collections import defaultdict


# --------------------------------------------------------------------- ledger
class LevelIndex:
    """Exact post-transition queue levels per queue id."""

    def __init__(self, path):
        self.times = defaultdict(list)
        self.bits = defaultdict(list)
        with gzip.open(path, "rt") as fh:
            for line in fh:
                if '"kind":"level"' not in line:
                    continue
                row = json.loads(line)
                self.times[row["queue"]].append(float(row["t"]))
                self.bits[row["queue"]].append(float(row["bits"]))
        for key in self.times:
            order = sorted(range(len(self.times[key])),
                           key=lambda i: self.times[key][i])
            self.times[key] = [self.times[key][i] for i in order]
            self.bits[key] = [self.bits[key][i] for i in order]

    def at(self, queue, t):
        times = self.times.get(queue)
        if not times:
            return None
        index = bisect.bisect_right(times, t) - 1
        if index < 0:
            return 0.0 if times[0] > t else float(self.bits[queue][0])
        return float(self.bits[queue][index])


class WindowIndex:
    """In-service residual per directed ISL from the service-window ledger."""

    def __init__(self, path):
        self.windows = defaultdict(list)
        with gzip.open(path, "rt") as fh:
            for line in fh:
                row = json.loads(line)
                link = str(row.get("link_id") or "")
                if not link.startswith("isl:"):
                    continue
                self.windows[link].append(
                    (float(row["start"]), float(row["end"]),
                     float(row["rate_bps"])))
        for key in self.windows:
            self.windows[key].sort()

    def residual(self, link, t):
        windows = self.windows.get(link)
        if not windows:
            return 0.0
        starts = [w[0] for w in windows]
        index = bisect.bisect_right(starts, t) - 1
        if index < 0:
            return 0.0
        start, end, rate = windows[index]
        if t >= end:
            return 0.0
        return max(0.0, (end - t) * rate)


def queued(index, sat, peer, generation, t):
    """data + ctrl queued bits on the directed ISL sat->peer at instant t."""
    suffix = "{sat}:{peer}:gen{gen}".format(sat=sat, peer=peer, gen=generation)
    data = index.at("isl_data:isl:" + suffix, t)
    ctrl = index.at("isl_ctrl:isl:" + suffix, t)
    if data is None or ctrl is None:
        return None
    return data + ctrl


def truth(index, windows, sat, peer, generation, t):
    base = queued(index, sat, peer, generation, t)
    if base is None:
        return None
    return base + windows.residual("isl:%d:%d" % (sat, peer), t)


# ------------------------------------------------------------------ analysis
def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(math.floor(pos))
    high = int(math.ceil(pos))
    if low == high:
        return ordered[low]
    return ordered[low] * (high - pos) + ordered[high] * (pos - low)


def describe(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "p10": percentile(values, 0.10),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "min": min(values),
    }


def candidate_truth(audit, row, cand_res, index, windows):
    """Per-candidate truth block, or None when the ISL resource is unusable."""
    ta = audit["time_alignment"]
    snap = float(ta["snapshot_at"])
    out = {}
    for direction in row.get("candidates") or ():
        detail = cand_res.get(str(direction)) or {}
        if detail.get("status") != "ok" or detail.get("kind") != "isl":
            continue
        peer = detail.get("peer")
        egress = detail.get("egress_peer")
        generation = detail.get("egress_generation")
        eta = (ta.get("eta_targets") or {}).get(str(direction))
        score = (ta.get("scores") or {}).get(str(direction)) or {}
        terms = score.get("terms") or {}
        rate = score.get("service_rate_bps")
        total = score.get("total_s")
        if (peer is None or egress is None or generation is None
                or eta is None or total is None or rate in (None, 0.0)
                or "resource_work_s" not in terms):
            continue
        used = (ta.get("used_state") or {}).get(str(direction)) or {}
        t_g = used.get("measured_at")
        t_r = used.get("received_at")
        if t_g is None:
            continue
        q0_recon = truth(index, windows, peer, egress, generation, float(t_g))
        q1 = truth(index, windows, peer, egress, generation, snap)
        q2 = truth(index, windows, peer, egress, generation, float(eta))
        if q0_recon is None or q1 is None or q2 is None:
            continue
        # S0 is BY DEFINITION the measurement made at t_g: the advertisement
        # the router actually held.  No reconstruction noise is added to it;
        # the reconstruction is kept only as a ledger-validation cross-check.
        q0 = used.get("bits")
        if q0 is None:
            q0 = q0_recon
        base = float(total) - float(terms["resource_work_s"])
        out[str(direction)] = {
            "peer": int(peer), "egress_peer": int(egress),
            "generation": int(generation),
            "t_g": float(t_g), "t_r": float(t_r), "t_d": snap,
            "T_arrival": float(eta),
            "eta_terms": (ta.get("eta_terms") or {}).get(str(direction)) or {},
            "advertised_bits": used.get("bits"),
            "advertised_queued": detail.get("advertised_queue_bits"),
            "Q_queued_g": queued(index, peer, egress, generation, float(t_g)),
            "S0_reconstructed": q0_recon,
            "Q_queued_d": queued(index, peer, egress, generation, snap),
            "Q_queued_T": queued(index, peer, egress, generation, float(eta)),
            "S0": q0, "S1": q1, "S2": q2,
            "rate": float(rate),
            "base_s": base,
            "C0": base + q0 / float(rate),
            "C1": base + q1 / float(rate),
            "C2": base + q2 / float(rate),
            "predicted_bits": score.get("predicted_bits"),
            "local_egress_bits": (row.get("observation_at_start") or {})
            .get("own_queue_bits", {}).get(str(direction)),
            "fallback": bool(score.get("fallback")),
        }
    return out


def analyse(decisions, index, windows, limit=None):
    rows = []
    validation = []
    excluded = {"no_time_alignment": 0, "lt_2_isl_candidates": 0,
                "candidate_detail_missing": 0, "truth_unavailable": 0}
    cand_hist = {}
    for row in decisions[:limit] if limit else decisions:
        audit = (row.get("observation_at_start") or {})
        if not isinstance(audit.get("time_alignment"), dict):
            excluded["no_time_alignment"] += 1
            continue
        cand_res = audit.get("candidate_resources") or {}
        n_legal = len(row.get("candidates") or ())
        cand_hist[n_legal] = cand_hist.get(n_legal, 0) + 1
        block = candidate_truth(audit, row, cand_res, index, windows)
        if len(block) < 2:
            if sum(1 for d in (row.get("candidates") or ())
                   if (cand_res.get(str(d)) or {}).get("status") == "ok") >= 2:
                excluded["truth_unavailable"] += 1
            else:
                excluded["lt_2_isl_candidates"] += 1
            continue
        # reconstruction validation: truth at t_g must equal the advertisement
        # the kernel actually generated at that instant (work-ahead bits)
        for direction, entry in block.items():
            expected = entry["advertised_bits"]
            if expected is None:
                continue
            validation.append({
                "diff_bits": entry["S0_reconstructed"] - float(expected),
                "diff_queued_bits": (None if entry["advertised_queued"] is None
                                     else entry["Q_queued_g"]
                                     - float(entry["advertised_queued"])),
                "direction": direction,
            })
        order0 = sorted(block, key=lambda d: (block[d]["C0"], d))
        order1 = sorted(block, key=lambda d: (block[d]["C1"], d))
        order2 = sorted(block, key=lambda d: (block[d]["C2"], d))
        top1, top2 = order0[0], order0[1]
        margin = block[top2]["C0"] - block[top1]["C0"]
        d_ab = ((block[top1]["C2"] - block[top2]["C2"])
                - (block[top1]["C0"] - block[top2]["C0"]))
        common_mode = statistics.fmean(
            block[d]["S2"] - block[d]["S0"] for d in block)
        abs_g = block[top1]["S1"] - block[top1]["S0"]
        abs_f = block[top1]["S2"] - block[top1]["S1"]
        diff_g = ((block[top1]["S1"] - block[top1]["S0"])
                  - (block[top2]["S1"] - block[top2]["S0"]))
        diff_f = ((block[top1]["S2"] - block[top1]["S1"])
                  - (block[top2]["S2"] - block[top2]["S1"]))
        local_loss = (block[order0[0]]["C2"] - block[order2[0]]["C2"])
        rows.append({
            "decision_id": row.get("decision_id"),
            "local_margin_loss_s": local_loss,
            "pid": row.get("pid"), "hop_index": row.get("hop_index"),
            "sat": row.get("sat"), "kind": row.get("kind"),
            "chosen": row.get("chosen"),
            "n_candidates": len(block),
            "candidates": order0,
            "top1": top1, "top2": top2,
            "margin_s": margin,
            "C0": {d: block[d]["C0"] for d in block},
            "C1": {d: block[d]["C1"] for d in block},
            "C2": {d: block[d]["C2"] for d in block},
            "S0": {d: block[d]["S0"] for d in block},
            "S1": {d: block[d]["S1"] for d in block},
            "S2": {d: block[d]["S2"] for d in block},
            "common_mode_f": common_mode,
            "abs_drift_g": abs_g, "abs_drift_f": abs_f,
            "diff_drift_g": diff_g, "diff_drift_f": diff_f,
            "D_ab": d_ab,
            "lambda_diag": (abs(d_ab) / margin if margin > 0 else None),
            "t_d": block[top1]["t_d"],
            "age_g": block[top1]["t_d"] - block[top1]["t_g"],
            "horizon_f": block[top1]["T_arrival"] - block[top1]["t_d"],
            "age_total": block[top1]["T_arrival"] - block[top1]["t_g"],
            "R_T": (max(block[d]["T_arrival"] for d in block)
                    - min(block[d]["T_arrival"] for d in block)),
            "rank_changed_0_1": order1[0] != order0[0],
            "rank_changed_0_2": order2[0] != order0[0],
            "rank_changed_1_2": order2[0] != order1[0],
            "action_changed_vs_recorded_1": order1[0] != row.get("chosen"),
            "action_changed_vs_recorded_2": order2[0] != row.get("chosen"),
            "split_frac_bits": {
                d: (block[d]["S1"] - block[d]["S0"]) / max(1.0, block[d]["S1"])
                for d in block},
            "T_top1": block[top1]["T_arrival"], "T_top2": block[top2]["T_arrival"],
            "abs_D_over_margin": (abs(d_ab) / margin if margin > 0 else None),
            "top1_block": {k: block[top1][k] for k in (
                "t_g","t_r","t_d","T_arrival","advertised_bits","S0",
                "S1","S2","rate","C0","C1","C2","predicted_bits")},
            "top2_block": {k: block[top2][k] for k in (
                "t_g","t_r","t_d","T_arrival","advertised_bits","S0",
                "S1","S2","rate","C0","C1","C2","predicted_bits")},
        })
    excluded["candidates_per_decision_histogram"] = dict(sorted(cand_hist.items()))
    return rows, validation, excluded


def summarize(rows, validation, excluded=None):
    def flag(name):
        return sum(1 for r in rows if r[name])

    def collect(name):
        return [r[name] for r in rows]

    dist_bands = {}
    for name, fn in (("|D_ab|", lambda r: abs(r["D_ab"])),
                     ("margin_s", lambda r: r["margin_s"]),
                     ("lambda_diag", lambda r: r["lambda_diag"]),
                     ("abs_drift_g", lambda r: abs(r["abs_drift_g"])),
                     ("abs_drift_f", lambda r: abs(r["abs_drift_f"])),
                     ("diff_drift_g", lambda r: abs(r["diff_drift_g"])),
                     ("diff_drift_f", lambda r: abs(r["diff_drift_f"])),
                     ("age_g", lambda r: r["age_g"]),
                     ("horizon_f", lambda r: r["horizon_f"]),
                     ("R_T", lambda r: r["R_T"]),
                     ("local_margin_loss_s",
                      lambda r: r["local_margin_loss_s"])):
        dist_bands[name] = describe([fn(r) for r in rows])
    vdiff = [abs(v["diff_bits"]) for v in validation if v["diff_bits"] is not None]
    vq = [abs(v["diff_queued_bits"]) for v in validation
          if v["diff_queued_bits"] is not None]
    reversal = [r for r in rows if r["rank_changed_0_2"]]
    lambda_bands = {}
    for lo, hi in ((0.0, 0.25), (0.25, 0.5), (0.5, 1.0), (1.0, 2.0), (2.0, 1e9)):
        sub = [r for r in rows
               if r["lambda_diag"] is not None
               and lo <= r["lambda_diag"] < hi]
        lambda_bands["lambda_%s_%s" % (lo, hi)] = {
            "n": len(sub),
            "reversed": sum(1 for r in sub if r["rank_changed_0_2"]),
            "reversed_frac": (sum(1 for r in sub if r["rank_changed_0_2"])
                              / len(sub) if sub else None),
        }
    return {
        "decisions": len(rows),
        "excluded": excluded or {},
        "reversal_lambda_bands": lambda_bands,
        "sign_check": {
            "n": sum(1 for r in rows if r["margin_s"] > 0),
            "signed_D_gt_margin": sum(1 for r in rows
                                      if r["D_ab"] > r["margin_s"] > 0),
            "actual_reversal": len(reversal),
            "agreement": (sum(1 for r in rows
                              if (r["D_ab"] > r["margin_s"]) == r["rank_changed_0_2"])
                          / max(1, len(rows))),
        },
        "local_margin_loss": {
            "n_positive": sum(1 for r in rows if r["local_margin_loss_s"] > 0),
            "reversed_with_positive_loss": sum(
                1 for r in rows
                if r["rank_changed_0_2"] and r["local_margin_loss_s"] > 0),
            "median_positive_s": (statistics.median(
                [r["local_margin_loss_s"] for r in rows
                 if r["local_margin_loss_s"] > 0])
                if any(r["local_margin_loss_s"] > 0 for r in rows) else None),
        },
        "reversal": {
            "n": len(reversal),
            "median_margin_s": (statistics.median([r["margin_s"] for r in reversal])
                                if reversal else None),
            "median_D_abs": (statistics.median([abs(r["D_ab"]) for r in reversal])
                             if reversal else None),
        },
        "validation": {
            "rows": len(vdiff),
            "max_abs_diff_bits": max(vdiff) if vdiff else None,
            "mean_abs_diff_bits": statistics.fmean(vdiff) if vdiff else None,
            "within_1bit": sum(1 for v in vdiff if v <= 1.0),
            "queued_rows": len(vq),
            "max_abs_diff_queued_bits": max(vq) if vq else None,
        },
        "counts": {
            "rank_changed_0_1": flag("rank_changed_0_1"),
            "rank_changed_0_2": flag("rank_changed_0_2"),
            "rank_changed_1_2": flag("rank_changed_1_2"),
            "action_changed_vs_recorded_1": flag("action_changed_vs_recorded_1"),
            "action_changed_vs_recorded_2": flag("action_changed_vs_recorded_2"),
        },
        "rates": {
            "rank_changed_0_1": flag("rank_changed_0_1") / max(1, len(rows)),
            "rank_changed_0_2": flag("rank_changed_0_2") / max(1, len(rows)),
            "action_changed_vs_recorded_2":
                flag("action_changed_vs_recorded_2") / max(1, len(rows)),
        },
        "distributions": dist_bands,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extract", required=True)
    parser.add_argument("--qlevels", required=True)
    parser.add_argument("--svcwin", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dump-rows", action="store_true")
    args = parser.parse_args()

    with gzip.open(args.extract, "rt") as fh:
        document = json.load(fh)
    decisions = document.get("decisions") or []
    index = LevelIndex(args.qlevels)
    windows = WindowIndex(args.svcwin)
    rows, validation, excluded = analyse(decisions, index, windows,
                                         limit=args.limit)
    summary = summarize(rows, validation, excluded)
    payload = {
        "schema": "t1-truth-trajectory-analysis/v1",
        "inputs": {"extract": args.extract, "qlevels": args.qlevels,
                   "svcwin": args.svcwin},
        "arm": document.get("arm", {}).get("arm"),
        "snapshot_at": None,
        "summary": summary,
        "rows": rows if args.dump_rows else None,
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    print(json.dumps(summary, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
