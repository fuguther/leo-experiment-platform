#!/usr/bin/env python3
"""Experiment autopsy: explain a finished run from its own data.

Reads a network_alignment summary (always), the full run JSON (optional, gives
score-term and per-direction audit detail), and a target-probe JSONL (optional,
gives prediction accuracy).  Prints a structured report with verdict lines.

Design rule: every number names its source; missing data is UNKNOWN, never 0.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def _mean(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return statistics.fmean(values) if values else None


def _pct(value):
    return None if value is None else round(100.0 * value, 2)


def summary_block(path):
    d = json.load(open(path))
    a = d["arms"][0]
    o = a["outcome"]
    net = a["network_outcome"]
    block = {
        "arm": a.get("arm"),
        "offered": o.get("offered"),
        "admitted": o.get("admitted"),
        "delivered": o.get("delivered"),
        "delivery": round(net["delivery_ratio"]["by_packets"], 4),
        "fates": o.get("fate_counts"),
        "terminal_loss": net.get("terminal_loss", {}).get("by_fate"),
        "censoring": net.get("censoring", {}).get("packets"),
        "e2e_ms": {k: round(o.get(k, 0) * 1000, 2) for k in (
            "e2e_mean_s", "e2e_p50_s", "e2e_p90_s", "e2e_p95_s",
            "e2e_p99_s")},
        "deadline_s": d.get("deadline", {}).get("deadline_s"),
        "binding": d.get("deadline_binding", {}).get("binding"),
        "control_bits": a.get("control", {}).get("bits", {}),
        "rate_hotspot": a.get("request_rate_per_satellite", {}).get("max"),
        "rate_mean": a.get("request_rate_per_satellite", {}).get("mean"),
        "utils": [],
    }
    links = [v for k, v in a["congestion_metrics"]["links"].items()
             if k.startswith("isl:")]
    utils = sorted(v["utilization"] for v in links)
    block["isl_n"] = len(utils)
    if utils:
        block["util_mean"] = round(statistics.fmean(utils), 4)
        block["util_p99"] = round(utils[int(0.99 * (len(utils) - 1))], 4)
        block["util_max"] = round(utils[-1], 4)
        block["util_ge_02_pct"] = round(
            100.0 * sum(1 for x in utils if x >= 0.2) / len(utils), 2)
        block["utils"] = utils
    # time budget from per-packet records (delivered packets only)
    packets = a["congestion_metrics"].get("packets")
    budget = {}
    if isinstance(packets, dict) and packets:
        rows = [v for v in packets.values() if isinstance(v, dict)
                and v.get("delivered_at") is not None]
        if rows:
            for key in ("e2e_s", "pre_ingress_s", "access_wait_s",
                        "holding_wait_s", "queue_wait_s",
                        "total_queue_wait_s", "prop_s", "tx_s"):
                value = _mean([r.get(key) for r in rows])
                budget[key] = None if value is None else round(value * 1000, 3)
            e2e = budget.get("e2e_s")
            block["time_budget_ms"] = budget
            if e2e:
                block["time_share_pct"] = {
                    k: round(100.0 * budget[k] / e2e, 1)
                    for k in ("prop_s", "queue_wait_s", "access_wait_s",
                              "holding_wait_s", "tx_s")
                    if budget.get(k) is not None}
            block["delivered_packets_in_budget"] = len(rows)
    # queue areas
    areas = net["queue"]["area_bits_s"]["by_area"]
    total = sum(areas.values()) or 1
    block["queue_area_mbit_s"] = {k: round(v / 1e6, 1) for k, v in areas.items()}
    block["queue_share"] = {k: round(v / total, 3) for k, v in areas.items()}
    # control overhead vs delivered data
    cb = block["control_bits"]
    data_bits = o.get("delivered_bits")
    if cb.get("offered") and data_bits:
        block["control_overhead"] = round(cb["offered"] / data_bits, 4)
    ta = a.get("time_alignment_audit", {})
    block["decisions_in_audit"] = ta.get("decisions_in_action_log")
    block["reordered_pct"] = (
        _pct(ta.get("decisions_where_the_arm_reordered_candidates")
             / ta["decisions_in_action_log"])
        if ta.get("decisions_in_action_log") else None)
    block["fallback"] = ta.get("fallbacks")
    block["fallback_pct"] = (
        _pct(ta["fallbacks"] / ta["decisions_in_action_log"])
        if ta.get("decisions_in_action_log") and ta.get("fallbacks") is not None
        else None)
    ral = a.get("routing_audit_log", {})
    block["audit_retained"] = ral.get("decision_records_retained")
    block["audit_cap"] = ral.get("decision_records_cap")
    block["audit_coverage"] = ral.get("decision_coverage_fraction")
    return block


def raw_block(path, code_root):
    sys.path.insert(0, str(code_root))
    from experiment_platform.replay_codec import read_document
    doc = read_document(path)
    arm = doc["document"]["arms"][0]
    recs = arm["routing_audit_log"]["decision_records"]
    rw, rp, tx, eta_spans, ages, dirs = [], [], [], {}, {}, {}
    reasons = {}
    for row in recs:
        obs = row.get("observation_at_start") or {}
        ta = obs.get("time_alignment") or {}
        cr = obs.get("candidate_resources") or {}
        targets = ta.get("eta_targets") or {}
        snapshot = ta.get("snapshot_at")
        for direction in "NESW":
            detail = ((row.get("four_direction_audit") or {})
                      .get("candidate_details") or {}).get(direction) or {}
            terms = (detail.get("candidate_score") or {}).get("terms") or {}
            if terms:
                if isinstance(terms.get("resource_work_s"), (int, float)):
                    rw.append(terms["resource_work_s"])
                if isinstance(terms.get("remaining_prop_s"), (int, float)):
                    rp.append(terms["remaining_prop_s"])
                if isinstance(terms.get("tx_s"), (int, float)):
                    tx.append(terms["tx_s"])
            if targets.get(direction) is not None and snapshot is not None:
                eta_spans.setdefault(direction, []).append(
                    targets[direction] - snapshot)
            measurement = ((cr.get(direction) or {}).get("measurement") or {})
            if measurement.get("age_s") is not None:
                ages.setdefault(direction, []).append(measurement["age_s"])
        for reason in (row.get("fallback_reason_counts") or {}):
            reasons[reason] = reasons.get(reason, 0) + 1
    scored = len(rw)
    return {
        "decisions_retained": len(recs),
        "scored_terms": scored,
        "resource_work_ms": round(1000 * statistics.fmean(rw), 3) if rw else None,
        "remaining_prop_ms": round(1000 * statistics.fmean(rp), 3) if rp else None,
        "score_ratio": (round(statistics.fmean(rw) / statistics.fmean(rp), 4)
                        if rw and rp and statistics.fmean(rp) else None),
        "eta_span_ms": {d: {"mean": round(1000 * statistics.fmean(v), 2),
                            "max": round(1000 * max(v), 2), "n": len(v)}
                        for d, v in eta_spans.items()},
        "state_age_ms": {d: {"mean": round(1000 * statistics.fmean(v), 2),
                             "max": round(1000 * max(v), 2), "n": len(v)}
                         for d, v in ages.items()},
    }


def probe_block(path):
    rows, labeled = [], 0
    errors = []
    pred_zero_truth = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            rows.append(row)
            truth = row.get("truth") or {}
            pred = row.get("predicted_bits")
            work = truth.get("work_ahead_bits")
            if isinstance(pred, (int, float)) and isinstance(work, (int, float)):
                labeled += 1
                errors.append(pred - work)
                if pred == 0 and work > 0:
                    pred_zero_truth += 1
    abs_errors = [abs(e) for e in errors]
    return {
        "rows": len(rows),
        "labeled": labeled,
        "coverage": round(labeled / len(rows), 4) if rows else None,
        "bias_bits": round(statistics.fmean(errors)) if errors else None,
        "mae_bits": round(statistics.fmean(abs_errors)) if errors else None,
        "rmse_bits": (round((statistics.fmean([e * e for e in errors])) ** 0.5)
                      if errors else None),
        "pred_zero_truth_positive_pct": (
            round(100.0 * pred_zero_truth / labeled, 1) if labeled else None),
    }


def verdicts(block, raw, probe):
    lines = []
    budget = block.get("time_budget_ms") or {}
    shares = block.get("time_share_pct") or {}
    if shares:
        lines.append(
            "时间预算：传播 %.1f%% / 排队 %.1f%% / 接入 %.1f%% / 持有 %.1f%% / 发送 %.1f%%（n=%d 已送达包）"
            % (shares.get("prop_s", 0), shares.get("queue_wait_s", 0),
               shares.get("access_wait_s", 0), shares.get("holding_wait_s", 0),
               shares.get("tx_s", 0), block.get("delivered_packets_in_budget", 0)))
    if raw and raw.get("score_ratio") is not None:
        lines.append(
            "机制预算：唯一随臂变化的评分项 resource_work=%.2f ms vs remaining_prop=%.2f ms ⇒ 占评分 %.2f%%（1:%.1f）；"
            "四臂只可能在近似平局处改变决策"
            % (raw["resource_work_ms"], raw["remaining_prop_ms"],
               100 * raw["score_ratio"],
               (1 / raw["score_ratio"]) if raw["score_ratio"] else float("inf")))
    if block.get("fallback_pct") is not None:
        lines.append("机制健康：fallback %.1f%%（>20%% 表示机制未充分触发）"
                     % block["fallback_pct"])
    if probe and probe.get("mae_bits") is not None:
        lines.append(
            "预测器：bias=%d bit、MAE=%d bit、RMSE=%d bit、pred=0 且 truth>0 %.1f%% ⇒ 目标时刻积压被系统性低估"
            % (probe["bias_bits"], probe["mae_bits"], probe["rmse_bits"],
               probe.get("pred_zero_truth_positive_pct") or 0))
    loss = block.get("terminal_loss") or {}
    if loss:
        total = sum(loss.values())
        lines.append("丢包模式：%s（合计 %d 包）——不同 fate 需要不同修法"
                     % (", ".join("%s=%d" % (k, v) for k, v in loss.items()),
                        total))
    if block.get("control_overhead") is not None:
        lines.append("控制面开销：control offered / data delivered = %.1f%%；isl_ctrl 占队列面积 %.1f%%"
                     % (100 * block["control_overhead"],
                        100 * (block.get("queue_share") or {}).get("isl_ctrl", 0)))
    if block.get("audit_coverage") is not None:
        lines.append("审计覆盖：决策级记录保留 %s/%s（%.2f%%）；基于逐决策的结论只代表这一抽样"
                     % (block.get("audit_retained"), block.get("audit_cap"),
                        100 * block["audit_coverage"]))
    if block.get("util_ge_02_pct") is not None:
        lines.append("链路分布：ISL %d 条，util mean %.3f / p99 %.3f / max %.3f，≥0.2 占 %.1f%%"
                     % (block["isl_n"], block.get("util_mean", 0),
                        block.get("util_p99", 0), block.get("util_max", 0),
                        block["util_ge_02_pct"]))
    if block.get("rate_hotspot") and block.get("rate_mean"):
        lines.append("热点压力：max/mean = %.2f×"
                     % (block["rate_hotspot"] / block["rate_mean"]))
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Experiment autopsy")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--raw")
    ap.add_argument("--code-root", default="CODE")
    ap.add_argument("--probe")
    ap.add_argument("--label", default="")
    ap.add_argument("--json", dest="json_out")
    args = ap.parse_args(argv)
    block = summary_block(args.summary)
    block["label"] = args.label or Path(args.summary).name
    raw = raw_block(args.raw, args.code_root) if args.raw else None
    probe = probe_block(args.probe) if args.probe else None
    document = {"summary": block, "raw": raw, "probe": probe,
                "verdicts": verdicts(block, raw, probe)}
    print("== %s ==" % block["label"])
    print("offered=%s delivered=%s delivery=%.4f fates=%s"
          % (block["offered"], block["delivered"], block["delivery"],
             block["fates"]))
    print("e2e ms:", block["e2e_ms"], "D=%s binding=%s"
          % (block["deadline_s"], block["binding"]))
    print("queue area (Mbit·s):", block["queue_area_mbit_s"],
          "share:", block["queue_share"])
    print("control:", block["control_bits"], "overhead:",
          block.get("control_overhead"))
    if block.get("time_budget_ms"):
        print("time budget ms:", block["time_budget_ms"])
    if raw:
        print("raw:", json.dumps(raw, ensure_ascii=False))
    if probe:
        print("probe:", json.dumps(probe, ensure_ascii=False))
    print("\n判读：")
    for line in document["verdicts"]:
        print(" -", line)
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(document, ensure_ascii=False, indent=1))
        print("wrote", args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
