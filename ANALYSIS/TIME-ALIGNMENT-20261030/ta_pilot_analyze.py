#!/usr/bin/env python3
"""Offline analysis for the 2026-10-31 state-age mechanism pilot.

Reads ONLY the artifacts a load_calibration cell writes:

    <cell>/arms/<arm>/decisions.jsonl.gz   (all decisions + full TA audit)
    <cell>/arms/<arm>/result.json.gz       (packet_events / link windows / metrics)
    <cell>/arms/<arm>/target_probe.jsonl   (same-run prediction vs truth)
    <cell>/trace-manifest.json             (trace identity shared by all arms)
    <cell>/arms/<arm>/config.json          (resolved config per arm)

and produces the quantities the pilot report is required to show:

  rho          post-warm-up directed-ISL utilisation on the measured corridor
  network      E2E / throughput / delivery / fate / queue-wait / tx / prop /
               holding / access decomposition, per arm
  decisions    state age, candidate ETA dispersion, margin, fallback and
               query-instant semantics, per arm
  probe        predictor accuracy at the candidate target instant in SECONDS
               (coverage, MAE, median AE, p90 AE, signed bias) - ISL rows only
  oracle       OFFLINE re-ranking with truth-probe workload substituted for the
               prediction; never used online.  Reports ranking/action reversal,
               signed differential shift D vs margin M, P(D>M), and the
               direction/persistence of the disagreement.
  compare      cross-arm decisions paired by (pid, hop_index): disagreement
               rate, net directional bias per target resource, run lengths and
               realised per-directed-link traffic deltas.

Usage:
  python3 ta_pilot_analyze.py rho      <cell_dir> [--start 4 --end 14]
  python3 ta_pilot_analyze.py network  <cell_dir>
  python3 ta_pilot_analyze.py decisions <cell_dir>
  python3 ta_pilot_analyze.py probe    <cell_dir>
  python3 ta_pilot_analyze.py oracle   <cell_dir>
  python3 ta_pilot_analyze.py compare  <cell_dir> --arms stale,now \
                                       [--start 4 --end 14] [--burst 8.0 8.5]

All commands accept --json <path> to save the full document.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ISL_PREFIX = "isl:"


# ---------------------------------------------------------------- io helpers
def _open_text(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8")
    return open(path, "r", encoding="utf-8")


def load_json(path: Path):
    with _open_text(path) as fh:
        return json.load(fh)


def load_jsonl(path: Path):
    with _open_text(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def arm_dir(cell: Path, arm: str) -> Path:
    return cell / "arms" / arm


def decision_path(cell: Path, arm: str) -> Path:
    return arm_dir(cell, arm) / "decisions.jsonl.gz"


def result_path(cell: Path, arm: str) -> Path:
    return arm_dir(cell, arm) / "result.json.gz"


def probe_path(cell: Path, arm: str) -> Path:
    return arm_dir(cell, arm) / "target_probe.jsonl"


def available_arms(cell: Path) -> list[str]:
    return sorted(p.name for p in (cell / "arms").iterdir() if p.is_dir())


def _finite(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _quantiles(values, points=(0.5, 0.9, 0.99)):
    if not values:
        return {}
    ordered = sorted(float(v) for v in values)
    out = {}
    for p in points:
        index = min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))
        out["p%g" % (p * 100)] = ordered[index]
    return out


def _stats(values, scale=1.0, points=(0.5, 0.9, 0.99)):
    if not values:
        return {"n": 0}
    scaled = [float(v) * scale for v in values]
    out = {"n": len(scaled), "mean": statistics.fmean(scaled)}
    out.update(_quantiles(scaled, points))
    out["max"] = max(scaled)
    out["min"] = min(scaled)
    return out


# ---------------------------------------------------------------- rho / links
def link_windows(result: dict):
    windows = result.get("link_service_windows") or []
    by_link: dict[str, list[dict]] = defaultdict(list)
    for row in windows:
        link = row.get("link_id")
        stage = row.get("stage")
        if not isinstance(link, str) or stage != "isl":
            continue
        by_link[link].append(row)
    return by_link


def rho_report(cell: Path, start: float, end: float) -> dict:
    """Directed-ISL utilisation realised inside [start, end).

    rho = served_bits / (link_rate * window_s), computed from the actual
    service windows (never from a configured load).  A link that was not
    available for the whole window is reported together with its available
    fraction, and the denominator uses the *available* time when the kernel
    recorded availability windows (static topology: availability is full).
    """
    by_arm = {}
    for arm in available_arms(cell):
        rpath = result_path(cell, arm)
        if not rpath.is_file():
            continue
        result = load_json(rpath)
        by_link = link_windows(result)
        available = defaultdict(float)
        for row in result.get("link_available_windows") or []:
            if row.get("stage") != "isl":
                continue
            a, b = float(row["start"]), float(row["end"])
            overlap = max(0.0, min(b, end) - max(a, start))
            available[row["link_id"]] += overlap
        links = {}
        for link, rows in by_link.items():
            rate = None
            served = 0.0
            for row in rows:
                rate = float(row.get("rate_bps") or rate or 0.0)
                a, b = float(row["start"]), float(row["end"])
                if b <= start or a >= end:
                    continue
                # A window can straddle the analysis boundary; scale the
                # served bits by the covered fraction of that window.
                width = b - a
                if width <= 0:
                    continue
                covered = max(0.0, min(b, end) - max(a, start))
                served += float(row.get("served_bits") or 0.0) * (covered / width)
            if not rate:
                continue
            avail = available.get(link, end - start)
            denom = rate * max(avail, 1e-9)
            links[link] = {
                "served_bits": served,
                "rate_bps": rate,
                "available_s": avail,
                "rho": served / denom if denom else None,
            }
        ranked = sorted(
            (dict(link=k, **v) for k, v in links.items()),
            key=lambda item: item["rho"] or 0.0, reverse=True)
        top = ranked[:10]
        by_arm[arm] = {
            "window_s": [start, end],
            "links_with_traffic": sum(1 for v in links.values() if v["served_bits"] > 0),
            "links": len(links),
            "top10": top,
            "rho_ge": {str(t): sum(1 for v in links.values() if v["rho"] is not None and v["rho"] >= t)
                       for t in (0.2, 0.5, 0.7, 0.9)},
            "max_rho": ranked[0]["rho"] if ranked else None,
            "queue_area_bits_s": result.get("queue_area_bits_s"),
            "fate_counts": result.get("fate_counts"),
        }
    document = {
        "schema": "ta-pilot-rho/v1",
        "cell": str(cell),
        "window_s": [start, end],
        "arms": by_arm,
    }
    return document


# ---------------------------------------------------------------- network
def _delivered_ids(result: dict) -> set[int]:
    ids = set()
    for row in result.get("deliveries") or []:
        pid = row.get("pid") if isinstance(row, dict) else None
        if isinstance(pid, int):
            ids.add(pid)
    return ids


def network_report(cell: Path) -> dict:
    arms = {}
    for arm in available_arms(cell):
        rpath = result_path(cell, arm)
        if not rpath.is_file():
            continue
        result = load_json(rpath)
        metrics = result.get("congestion_metrics") or {}
        packets = metrics.get("packets") or {}
        e2e = [p["e2e_s"] for p in packets.values() if _finite(p.get("e2e_s"))]
        qwait = [p["total_queue_wait_s"] for p in packets.values()
                 if _finite(p.get("total_queue_wait_s"))]
        holding = [p["holding_wait_s"] for p in packets.values()
                   if _finite(p.get("holding_wait_s"))]
        tx = [p["tx_s"] for p in packets.values() if _finite(p.get("tx_s"))]
        prop = [p["prop_s"] for p in packets.values() if _finite(p.get("prop_s"))]
        access = [p["access_wait_s"] for p in packets.values()
                  if _finite(p.get("access_wait_s"))]
        totals = result.get("totals") or {}
        # The kernel ledger is authoritative for bits; congestion_metrics.packets
        # deliberately carries only per-packet timing (no bits), so using it for
        # a bit total silently reported zero.
        delivered_bits = float(totals.get("delivered_bits") or 0.0)
        offered_bits = float(totals.get("offered_bits")
                             or metrics.get("offered_bits") or 0.0)
        arms[arm] = {
            "natural_end": result.get("natural_end"),
            "stop_time_s": result.get("stop_time_s"),
            "events_processed": result.get("events_processed"),
            "offered_packets": metrics.get("offered_packets"),
            "offered_bits": offered_bits,
            "admitted_packets": metrics.get("admitted_at_satellite_ingress_packets"),
            "delivered_packets": metrics.get("delivered_packets"),
            "delivered_bits": delivered_bits,
            "access_admission_rate": metrics.get("access_admission_rate"),
            "delivery_ratio": metrics.get("network_delivery_rate_by_horizon"),
            "fate_counts": result.get("fate_counts"),
            "terminal_loss_bits": totals.get("terminal_loss_bits"),
            "in_system_bits_at_stop": totals.get("in_system_bits_at_stop"),
            "queue_area_bits_s": result.get("queue_area_bits_s"),
            "e2e_s": _stats(e2e, points=(0.5, 0.9, 0.95, 0.99)),
            "queue_wait_s": _stats(qwait),
            "holding_wait_s": _stats(holding),
            "tx_s": _stats(tx),
            "prop_s": _stats(prop),
            "access_wait_s": _stats(access),
            "mean_delivered_e2e_s": (statistics.fmean(e2e) if e2e else None),
            "goodput_mbps_window10": (delivered_bits / 10.0 / 1e6) if delivered_bits else None,
        }
    return {"schema": "ta-pilot-network/v1", "cell": str(cell), "arms": arms}


# ---------------------------------------------------------------- decisions
def _legal(decision: dict):
    obs = decision.get("observation_at_start") or {}
    return obs.get("legal_directions") or []


def _ta(decision: dict):
    obs = decision.get("observation_at_start") or {}
    audit = obs.get("time_alignment")
    return audit if isinstance(audit, dict) else None


def decisions_report(cell: Path, arm: str | None = None,
                     start: float | None = None, end: float | None = None) -> dict:
    arms = [arm] if arm else available_arms(cell)
    out = {}
    for name in arms:
        path = decision_path(cell, name)
        if not path.is_file():
            continue
        rows = load_jsonl(path)
        if start is not None or end is not None:
            lo = -math.inf if start is None else float(start)
            hi = math.inf if end is None else float(end)
            rows = [r for r in rows
                    if _finite(r.get("t")) and lo <= float(r["t"]) < hi]
        ta_rows = [r for r in rows if _ta(r)]
        multi = [r for r in ta_rows if len(_legal(r)) >= 2]
        ages = []
        dispersion = []
        margins = []
        query_ok = 0
        query_n = 0
        fallback_decisions = 0
        fallback_slots = 0
        slots = 0
        complete2 = 0
        zero_work_slots = 0
        resource_work_nonzero = 0
        score_terms = Counter()
        for row in ta_rows:
            audit = _ta(row)
            legal = _legal(row)
            scores = audit.get("scores") or {}
            used = audit.get("used_state") or {}
            eta = audit.get("eta_targets") or {}
            query = audit.get("query_targets") or {}
            snap = audit.get("snapshot_at")
            for direction in legal:
                slots += 1
                score = scores.get(direction) or {}
                if score.get("missing"):
                    fallback_slots += 1
                work = (score.get("terms") or {}).get("resource_work_s")
                if _finite(work) and float(work) > 0:
                    resource_work_nonzero += 1
                if _finite(work) and float(work) == 0:
                    zero_work_slots += 1
            if any((scores.get(d) or {}).get("missing") for d in legal):
                fallback_decisions += 1
            complete = [d for d in legal
                        if isinstance(scores.get(d), dict)
                        and not scores[d].get("missing")
                        and _finite(scores[d].get("total_s"))]
            if len(complete) >= 2:
                complete2 += 1
                totals = sorted(float(scores[d]["total_s"]) for d in complete)
                margins.append(totals[1] - totals[0])
            if len(legal) >= 2 and eta:
                query_n += 1
                if all(_finite(query.get(d)) and _finite(eta.get(d))
                       and abs(float(query[d]) - float(eta[d])) < 1e-9
                       for d in legal):
                    query_ok += 1
            for direction in legal:
                state = used.get(direction) or {}
                if _finite(snap) and _finite(state.get("measured_at")):
                    ages.append(float(snap) - float(state["measured_at"]))
            if eta:
                vals = [float(v) for v in eta.values() if _finite(v)]
                if len(vals) >= 2:
                    dispersion.append(max(vals) - min(vals))
            for score in scores.values():
                for term in (score.get("terms") or {}):
                    score_terms[term] += 1
        out[name] = {
            "rows": len(rows),
            "rows_with_time_alignment": len(ta_rows),
            "multi_candidate_rows": len(multi),
            "decisions_with_two_complete_candidates": complete2,
            "complete_coverage": (complete2 / len(multi)) if multi else None,
            "fallback_decisions": fallback_decisions,
            "fallback_slot_rate": (fallback_slots / slots) if slots else None,
            "candidate_slots": slots,
            "zero_resource_work_slot_rate": (zero_work_slots / slots) if slots else None,
            "nonzero_resource_work_slot_rate": (resource_work_nonzero / slots) if slots else None,
            "query_equals_eta_rate": (query_ok / query_n) if query_n else None,
            "query_comparisons": query_n,
            "state_age_s": _stats(ages),
            "eta_dispersion_s": _stats(dispersion, points=(0.5, 0.9, 0.99)),
            "score_margin_s": _stats(margins, points=(0.1, 0.5, 0.9)),
            "score_term_presence": dict(score_terms),
        }
    return {"schema": "ta-pilot-decisions/v1", "cell": str(cell), "arms": out}


# ---------------------------------------------------------------- probe
def valid_truth_rows(rows, arm: str | None = None):
    """ISL-target rows whose truth identity is the one the predictor used.

    The probe records two identity sources.  Only ``used_state`` rows are rows
    where the prediction carried the same resource id + generation that the
    truth read; the fallback identity rows (downlink targets / no
    advertisement) must never be used as prediction labels.
    """
    out = []
    for row in rows:
        if arm is not None and row.get("arm") != arm:
            continue
        truth = row.get("truth")
        if not isinstance(truth, dict):
            continue
        if row.get("identity_source") != "used_state":
            continue
        if not _finite(row.get("predicted_bits")):
            continue
        if not _finite(row.get("service_rate_bps")) or float(row["service_rate_bps"]) <= 0:
            continue
        if not _finite(truth.get("work_ahead_bits")):
            continue
        if truth.get("generation") != row.get("generation"):
            continue
        out.append(row)
    return out


def _err_seconds(row):
    return ((float(row["predicted_bits"]) - float(row["truth"]["work_ahead_bits"]))
            / float(row["service_rate_bps"]))


def probe_report(cell: Path, arm: str | None = None,
                 start: float | None = None, end: float | None = None) -> dict:
    out = {}
    for name in ([arm] if arm else available_arms(cell)):
        path = probe_path(cell, name)
        if not path.is_file():
            continue
        rows = load_jsonl(path)
        if start is not None or end is not None:
            lo = -math.inf if start is None else float(start)
            hi = math.inf if end is None else float(end)
            rows = [r for r in rows
                    if _finite(r.get("t_commit_s")) and lo <= float(r["t_commit_s"]) < hi]
        valid = valid_truth_rows(rows, name)
        errors_ms = [_err_seconds(r) * 1000 for r in valid]
        abs_ms = [abs(e) for e in errors_ms]
        reasons = Counter(r.get("missing_reason") for r in rows if r.get("missing_reason"))
        identity = Counter(r.get("identity_source") for r in rows)
        # Per-decision label completeness: a decision is usable for oracle
        # re-ranking only when >=2 of its candidates have a valid truth row.
        per_decision = defaultdict(int)
        for row in valid:
            per_decision[row.get("decision_id")] += 1
        usable = sum(1 for count in per_decision.values() if count >= 2)
        decisions = len({r.get("decision_id") for r in rows})
        multi_decisions = None
        dpath = decision_path(cell, name)
        if dpath.is_file():
            multi_decisions = sum(
                1 for decision in load_jsonl(dpath)
                if _ta(decision) and len(_legal(decision)) >= 2)
        out[name] = {
            "probe_rows": len(rows),
            "valid_truth_rows": len(valid),
            "row_coverage": len(valid) / len(rows) if rows else None,
            "identity_sources": dict(identity),
            "missing_reasons": dict(reasons),
            "decisions_in_probe": decisions,
            "decisions_with_two_valid_candidates": usable,
            "decision_label_coverage": (usable / decisions) if decisions else None,
            "multi_candidate_decisions": multi_decisions,
            "multi_candidate_label_coverage": (
                usable / multi_decisions) if multi_decisions else None,
            "error_ms": {
                "n": len(errors_ms),
                "bias_mean_ms": statistics.fmean(errors_ms) if errors_ms else None,
                "mae_ms": statistics.fmean(abs_ms) if abs_ms else None,
                "median_abs_ms": statistics.median(abs_ms) if abs_ms else None,
                "p90_abs_ms": _quantiles(abs_ms, points=(0.9,)).get("p90") if abs_ms else None,
                "p99_abs_ms": _quantiles(abs_ms, points=(0.99,)).get("p99") if abs_ms else None,
                "max_abs_ms": max(abs_ms) if abs_ms else None,
                "within_10ms": (sum(1 for e in abs_ms if e <= 10.0) / len(abs_ms)) if abs_ms else None,
            },
            "truth_nonzero_rate": (sum(1 for r in valid if float(r["truth"]["work_ahead_bits"]) > 0)
                                   / len(valid)) if valid else None,
        }
    return {"schema": "ta-pilot-probe/v1", "cell": str(cell), "arms": out}


# ---------------------------------------------------------------- oracle
def _decision_map(cell: Path, arm: str):
    path = decision_path(cell, arm)
    if not path.is_file():
        return {}
    out = {}
    for row in load_jsonl(path):
        audit = _ta(row)
        if not audit:
            continue
        out[row.get("decision_id")] = row
    return out


def _oracle_decision(decision: dict, truth_by_direction: dict, packet_bits: int):
    """Offline truth re-ranking for ONE decision.

    Only the predicted target-resource workload term is replaced; every other
    recorded term is frozen.  Returns None when fewer than two legal candidates
    have both a complete score and a valid truth row.
    """
    audit = _ta(decision)
    legal = _legal(decision)
    scores = audit.get("scores") or {}
    candidates = []
    for direction in legal:
        score = scores.get(direction) or {}
        truth = truth_by_direction.get(direction)
        if truth is None or score.get("missing"):
            continue
        terms = score.get("terms") or {}
        total = score.get("total_s")
        work = terms.get("resource_work_s")
        rate = score.get("service_rate_bps")
        if not (_finite(total) and _finite(work) and _finite(rate) and float(rate) > 0):
            continue
        true_work_bits = float(truth["truth"]["work_ahead_bits"])
        oracle_total = float(total) - float(work) + true_work_bits / float(rate)
        candidates.append({
            "direction": direction,
            "pred_total": float(total),
            "oracle_total": oracle_total,
            "pred_work_s": float(work),
            "true_work_s": true_work_bits / float(rate),
            "resource_id": (audit.get("used_state") or {}).get(direction, {}).get("resource_id"),
            "error_s": true_work_bits / float(rate) - float(work),
        })
    if len(candidates) < 2:
        return None
    pred_sorted = sorted(candidates, key=lambda c: c["pred_total"])
    oracle_sorted = sorted(candidates, key=lambda c: c["oracle_total"])
    top1, top2 = pred_sorted[0], pred_sorted[1]
    margin = top2["pred_total"] - top1["pred_total"]
    differential = top1["error_s"] - top2["error_s"]
    return {
        "decision_id": decision.get("decision_id"),
        "pid": decision.get("pid"),
        "hop_index": decision.get("hop_index"),
        "sat": decision.get("sat"),
        "t": decision.get("t"),
        "chosen": decision.get("chosen"),
        "pred_top1": top1["direction"],
        "oracle_top1": oracle_sorted[0]["direction"],
        "margin_s": margin,
        "differential_s": differential,
        "crossed": differential > margin,
        "ranking_reversed": pred_sorted[0]["direction"] != oracle_sorted[0]["direction"],
        "action_reversed": decision.get("chosen") != oracle_sorted[0]["direction"],
        "pred_resource": top1["resource_id"],
        "oracle_resource": oracle_sorted[0]["resource_id"],
        "candidates": candidates,
        "n_candidates": len(candidates),
    }


def oracle_report(cell: Path, arm: str, packet_bits: int = 1_000_000,
                  burst: tuple[float, float] = (8.0, 8.5),
                  start: float | None = None, end: float | None = None) -> dict:
    decisions = _decision_map(cell, arm)
    probe_rows = load_jsonl(probe_path(cell, arm)) if probe_path(cell, arm).is_file() else []
    valid = valid_truth_rows(probe_rows, arm)
    by_decision = defaultdict(dict)
    for row in valid:
        by_decision[row.get("decision_id")][row.get("direction")] = row
    rows = []
    lo = -math.inf if start is None else float(start)
    hi = math.inf if end is None else float(end)
    for decision_id, decision in decisions.items():
        if not _finite(decision.get("t")) or not (lo <= float(decision["t"]) < hi):
            continue
        truths = by_decision.get(decision_id)
        if not truths:
            continue
        result = _oracle_decision(decision, truths, packet_bits)
        if result is not None:
            rows.append(result)
    if not rows:
        return {"schema": "ta-pilot-oracle/v1", "cell": str(cell), "arm": arm,
                "decisions": 0, "note": "no decision had >=2 valid truth candidates"}
    margins = [r["margin_s"] for r in rows]
    differentials = [r["differential_s"] for r in rows]
    crossings = [r for r in rows if r["crossed"]]
    reversals = [r for r in rows if r["ranking_reversed"]]
    actions = [r for r in rows if r["action_reversed"]]
    # Directionality of the oracle-vs-predicted disagreement.
    transitions = Counter((r["pred_top1"], r["oracle_top1"]) for r in reversals)
    net_by_resource = defaultdict(int)
    for r in reversals:
        net_by_resource[r["oracle_resource"]] += 1
        net_by_resource[r["pred_resource"]] -= 1
    runs = _run_lengths([(r["t"], r["pred_top1"] != r["oracle_top1"],
                          r["oracle_resource"]) for r in rows if r["t"] is not None])
    burst_lo, burst_hi = burst
    phases = Counter()
    for r in rows:
        t = float(r["t"] or 0.0)
        phase = "pre_burst" if t < burst_lo else ("burst" if t < burst_hi else "post_burst")
        phases[(phase, bool(r["ranking_reversed"]))] += 1
    return {
        "schema": "ta-pilot-oracle/v1",
        "cell": str(cell),
        "arm": arm,
        "decisions": len(rows),
        "decisions_with_reversal": len(reversals),
        "reversal_rate": len(reversals) / len(rows),
        "action_reversal_rate": len(actions) / len(rows),
        "signed_crossing_rate": len(crossings) / len(rows),
        "signed_rule_agreement": (sum(1 for r in rows if r["crossed"] == r["ranking_reversed"])
                                  / len(rows)),
        "margin_s": _stats(margins, points=(0.1, 0.5, 0.9)),
        "differential_s": _stats(differentials, points=(0.1, 0.5, 0.9)),
        # Ratios are only meaningful when the margin is not numerical dust;
        # a 1e-15 s margin would otherwise dominate the max and mean.
        "abs_differential_over_margin": _stats(
            [abs(r["differential_s"]) / r["margin_s"]
             for r in rows if r["margin_s"] is not None
             and r["margin_s"] >= 5e-4],
            points=(0.5, 0.9)),
        "ratio_rows": sum(1 for r in rows if r["margin_s"] is not None
                          and r["margin_s"] >= 5e-4),
        "ratio_rows_excluded_tiny_margin": sum(
            1 for r in rows if r["margin_s"] is not None and r["margin_s"] < 5e-4),
        "transition_counts": {"%s->%s" % k: v for k, v in transitions.most_common(20)},
        "net_decisions_by_oracle_resource": dict(net_by_resource),
        "direction_bias": _direction_bias(transitions),
        "runs": runs,
        "phase_reversal": {"%s|reversed=%s" % k: v for k, v in sorted(phases.items())},
        "row_detail": rows,
    }


def _direction_bias(transitions: Counter) -> dict:
    """b = |N(A->B) - N(B->A)| / N_flip over mirrored direction pairs."""
    by_pair = defaultdict(int)
    total = 0
    for (a, b), count in transitions.items():
        if a == b:
            continue
        key = tuple(sorted((a, b)))
        sign = 1 if (a, b) == key else -1
        by_pair[key] += sign * count
        total += count
    net = sum(abs(v) for v in by_pair.values())
    return {"flips": total, "net": net,
            "b": (net / total) if total else None,
            "per_pair": {"%s/%s" % k: v for k, v in sorted(by_pair.items())}}


def _run_lengths(series):
    """Longest same-direction run of consecutive flip decisions in time."""
    runs = []
    current = None
    for t, flipped, resource in sorted(series):
        if not flipped:
            current = None
            continue
        if current is not None and current["resource"] == resource:
            current["count"] += 1
            current["end"] = t
        else:
            current = {"resource": resource, "count": 1, "start": t, "end": t}
            runs.append(current)
    if not runs:
        return {"n_runs": 0}
    best = max(runs, key=lambda r: (r["count"], r["end"] - r["start"]))
    return {
        "n_runs": len(runs),
        "longest_count": best["count"],
        "longest_duration_s": best["end"] - best["start"],
        "mean_duration_s": statistics.fmean(r["end"] - r["start"] for r in runs),
        "max_duration_s": max(r["end"] - r["start"] for r in runs),
    }


# ---------------------------------------------------------------- compare
def _link_served(cell: Path, arm: str, start: float, end: float):
    result = load_json(result_path(cell, arm))
    out = defaultdict(float)
    for row in result.get("link_service_windows") or []:
        if row.get("stage") != "isl":
            continue
        a, b = float(row["start"]), float(row["end"])
        if b <= start or a >= end or b <= a:
            continue
        covered = max(0.0, min(b, end) - max(a, start))
        out[row["link_id"]] += float(row.get("served_bits") or 0.0) * (covered / (b - a))
    return out


def compare_report(cell: Path, arms: list[str], start: float = 4.0,
                   end: float = 14.0) -> dict:
    decisions = {}
    for arm in arms:
        rows = load_jsonl(decision_path(cell, arm))
        decisions[arm] = {(_decision_key(r)): r for r in rows if _decision_key(r)}
    served = {arm: _link_served(cell, arm, start, end) for arm in arms}
    out = {}
    for left, right in zip(arms, arms[1:]):
        shared = sorted(set(decisions[left]) & set(decisions[right]))
        disagreements = 0
        net = defaultdict(int)
        transitions = Counter()
        series = []
        for key in shared:
            a = decisions[left][key]
            b = decisions[right][key]
            chosen_a = a.get("chosen")
            chosen_b = b.get("chosen")
            t = a.get("t")
            if chosen_a == chosen_b:
                continue
            disagreements += 1
            transitions[(chosen_a, chosen_b)] += 1
            res = _chosen_resource(a, chosen_a)
            if res is not None:
                net[res] += 1
            res_b = _chosen_resource(b, chosen_b)
            if res_b is not None:
                net[res_b] -= 1
            series.append((float(t), True, res))
        link_delta = {}
        for link in set(served[left]) | set(served[right]):
            delta = served[right].get(link, 0.0) - served[left].get(link, 0.0)
            if abs(delta) > 0:
                link_delta[link] = delta
        worst = sorted(link_delta.items(), key=lambda kv: abs(kv[1]), reverse=True)[:10]
        out["%s_vs_%s" % (left, right)] = {
            "shared_decisions": len(shared),
            "action_disagreements": disagreements,
            "disagreement_rate": disagreements / len(shared) if shared else None,
            "transitions": {"%s->%s" % k: v for k, v in transitions.most_common(20)},
            "direction_bias": _direction_bias(transitions),
            "net_decisions_by_resource": dict(net),
            "runs": _run_lengths(series),
            "realised_link_delta_bits": {k: v for k, v in worst},
            "realised_extra_bits_to_same_resource": sum(
                v for v in link_delta.values() if v > 0),
            "window_s": [start, end],
        }
    return {"schema": "ta-pilot-compare/v1", "cell": str(cell), "pairs": out}


def _decision_key(row):
    pid, hop = row.get("pid"), row.get("hop_index")
    if isinstance(pid, int) and not isinstance(pid, bool) and isinstance(hop, int) \
            and not isinstance(hop, bool):
        return (pid, hop)
    return None


def _chosen_resource(decision, chosen):
    audit = _ta(decision) or {}
    state = (audit.get("used_state") or {}).get(chosen) or {}
    return state.get("resource_id")


# ---------------------------------------------------------------- cli
def _print(document, depth=0):
    text = json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False)
    print(text)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["rho", "network", "decisions",
                                            "probe", "oracle", "compare"])
    parser.add_argument("cell", type=Path)
    parser.add_argument("--arm", default=None)
    parser.add_argument("--arms", default=None,
                        help="comma separated arms for compare")
    parser.add_argument("--start", type=float, default=4.0)
    parser.add_argument("--end", type=float, default=14.0)
    parser.add_argument("--window", action="store_true", dest="window_flag",
                        help="restrict decisions/probe/oracle to [start, end)")
    parser.add_argument("--burst", nargs=2, type=float, default=(8.0, 8.5))
    parser.add_argument("--packet-bits", type=int, default=1_000_000)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.command == "rho":
        document = rho_report(args.cell, args.start, args.end)
    elif args.command == "network":
        document = network_report(args.cell)
    elif args.command == "decisions":
        window = (args.start, args.end) if args.window_flag else (None, None)
        document = decisions_report(args.cell, args.arm, window[0], window[1])
    elif args.command == "probe":
        window = (args.start, args.end) if args.window_flag else (None, None)
        document = probe_report(args.cell, args.arm, window[0], window[1])
    elif args.command == "oracle":
        if not args.arm:
            raise SystemExit("oracle requires --arm")
        window = (args.start, args.end) if args.window_flag else (None, None)
        document = oracle_report(args.cell, args.arm, args.packet_bits,
                                 tuple(args.burst), window[0], window[1])
    else:
        if not args.arms:
            raise SystemExit("compare requires --arms a,b")
        document = compare_report(args.cell, [a for a in args.arms.split(",") if a],
                                  args.start, args.end)
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(document, indent=1, sort_keys=True,
                                        ensure_ascii=False) + "\n",
                             encoding="utf-8")
        print("wrote %s" % args.json)
    else:
        _print(document)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
