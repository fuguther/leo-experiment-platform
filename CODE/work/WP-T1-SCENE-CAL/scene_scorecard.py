#!/usr/bin/env python3
"""ONE-PAGE SCORECARD for a traffic pattern / scenario.

Six blocks, each tied to a requirement that must hold before the scenario can
be used for a state-time claim:
  1 identity        reproducible and citable
  2 backlog exists  otherwise there is nothing to predict
  3 candidates differ  otherwise there is no routing choice to improve
  4 decision leverage  otherwise the difference is too small to flip a ranking
  5 observability   otherwise no predictor can see the backlog at all
  6 purity and cost  otherwise the effect is access or reachability, or too slow
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import statistics
from pathlib import Path


def _median(values):
    return statistics.median(values) if values else None


def _pct(a, b):
    return (100.0 * a / b) if b else None


def collect(cell_dir: Path, arm: str):
    probe = cell_dir / "arms" / arm / "target_probe.jsonl"
    rows = [json.loads(line) for line in open(probe, encoding="utf-8")]
    with gzip.open(cell_dir / "arms" / arm / "result.json.gz", "rt",
                   encoding="utf-8") as fh:
        result = json.load(fh)
    summary = json.loads((cell_dir / "summary.json").read_text())
    return rows, result, summary


def metrics(rows, result, summary):
    links = collections.Counter()
    per_dec = collections.defaultdict(list)
    labelled = truth_pos = obs_miss = 0
    ages, spans, state_s, total_s, potential_s = [], [], [], [], []
    max_backlog = 0.0
    for r in rows:
        if not r.get("truth"):
            continue
        lab = r["truth"].get("work_ahead_bits")
        if lab is None:
            continue
        lab = float(lab)
        links[r.get("resource_id")] += 1
        labelled += 1
        max_backlog = max(max_backlog, lab)
        per_dec[(r["pid"], r["decision_id"])].append(
            (lab, float(r.get("predicted_bits") or 0.0),
             bool(r.get("selected")), r.get("score_total_s"),
             float(r.get("predicted_work_s") or 0.0)))
        if lab > 0:
            truth_pos += 1
            st = r.get("state_bits")
            if st is not None and float(st) == 0.0:
                obs_miss += 1
        m, s = r.get("state_measured_at_s"), r.get("snapshot_at_s")
        if m is not None and s is not None:
            ages.append(float(s) - float(m))
        et = r.get("eval_target_s")
        if et is not None and s is not None:
            spans.append(float(et) - float(s))
        if r.get("score_total_s") is not None:
            total_s.append(float(r["score_total_s"]))
        if lab > 0 and r.get("predicted_work_s") is not None:
            state_s.append(float(r["predicted_work_s"]))
        if lab > 0 and r.get("score_total_s"):
            # Upper bound on how much a PERFECT state estimate could move the
            # score: the truth converted to seconds at the recorded rate is not
            # available, so use 1 Gbps as the declared ISL rate of this scene.
            potential_s.append(lab / 1e9)
    gaps = []
    for v in per_dec.values():
        scores = sorted(x[3] for x in v if x[3] is not None)
        if len(scores) >= 2:
            gaps.append(scores[1] - scores[0])
    spread = [max(x[0] for x in v) - min(x[0] for x in v)
              for v in per_dec.values() if len(v) >= 2]
    chosen_not_empty = 0
    saved = []
    for v in per_dec.values():
        if len(v) < 2:
            continue
        ch = [x[0] for x in v if x[2]]
        if not ch:
            continue
        m = min(x[0] for x in v)
        if ch[0] > m + 1e-9:
            chosen_not_empty += 1
            saved.append(ch[0] - m)
    fates = result.get("fate_counts") or {}
    areas = result.get("queue_area_bits_s") or {}
    access = result.get("access") or {}
    arm = (summary.get("arms") or [{}])[0]
    probe_sum = (arm.get("target_probe") or {}).get("summary") or {}
    offered = float((result.get("totals") or {}).get("offered_bits") or 0.0)
    delivered = float((result.get("totals") or {}).get("delivered_bits") or 0.0)
    n_dec = len(per_dec)
    with_backlog = sum(1 for v in per_dec.values() if max(x[0] for x in v) > 0)
    return {
        "identity": {
            "cell": summary.get("cell"), "arm": arm.get("arm"),
            "contract_sha256": summary.get("contract_sha256"),
            "config_sha256": arm.get("config_sha256"),
            "trace_rows": (summary.get("trace") or {}).get("rows"),
            "probe_rows": probe_sum.get("rows") or len(rows),
            "probe_stride": probe_sum.get("stride"),
            "wall_s": round(float(summary.get("execution_wall_s") or 0.0), 1)},
        "backlog": {
            "links_seen": len(links),
            "links_with_backlog": sum(1 for k in links),
            "max_backlog_bits": max_backlog,
            "rows_truth_pos_pct": _pct(truth_pos, labelled),
            "decisions_with_any_backlog_pct": _pct(with_backlog, n_dec)},
        "spread": {
            "decisions": n_dec,
            "decisions_with_spread_pct": _pct(sum(1 for x in spread if x > 0),
                                              len(spread)),
            "spread_bits_median": _median([x for x in spread if x > 0])},
        "leverage": {
            "score_total_ms_median": (1000.0 * _median(total_s) if total_s else None),
            "state_term_ms_median": (1000.0 * _median(state_s) if state_s else None),
            "state_share_pct": (_pct(_median(state_s), _median(total_s))
                                if state_s and total_s else None),
            "top2_score_gap_ms_median": (1000.0 * _median(gaps) if gaps else None),
            "state_potential_share_pct": (
                _pct(_median(potential_s), _median(total_s))
                if potential_s and total_s else None),
            "chosen_not_emptiest_pct": _pct(chosen_not_empty, n_dec),
            "savable_bits_mean": (statistics.fmean(saved) if saved else None)},
        "observability": {
            "truth_pos_but_obs_zero_pct": _pct(obs_miss, truth_pos),
            "aoi_ms_median": (1000.0 * _median(ages) if ages else None),
            "horizon_ms_median": (1000.0 * _median(spans) if spans else None),
            "age_over_horizon": ((_median(ages) / _median(spans))
                                 if ages and spans and _median(spans) else None)},
        "purity": {
            "access_wait_s_total": access.get("wait_time_s_total"),
            "access_wait_s_max": access.get("wait_time_s_max"),
            "access_waiting_at_stop": access.get("waiting_at_stop"),
            "no_route": fates.get("NO_ROUTE", 0),
            "deadline_expired": fates.get("DATA_DEADLINE_EXPIRED", 0),
            "queue_overflow": sum(fates.get(k, 0) for k in (
                "ISL_QUEUE_OVERFLOW", "HOLDING_QUEUE_OVERFLOW",
                "ACCESS_QUEUE_OVERFLOW")),
            "in_system_at_stop": fates.get("IN_SYSTEM_AT_STOP", 0),
            "packets": sum(fates.values()),
            "delivery_rate": (delivered / offered) if offered else None},
        "cost": {
            "isl_ctrl_area": areas.get("isl_ctrl"),
            "isl_data_area": areas.get("isl_data"),
            "wall_s": round(float(summary.get("execution_wall_s") or 0.0), 1)},
    }


def verdicts(m, arm_b=None):
    v = []
    b, s, l = m["backlog"], m["spread"], m["leverage"]
    o, p = m["observability"], m["purity"]
    rp = b["rows_truth_pos_pct"] or 0.0
    v.append(("积压存在", rp >= 20, "真值>0 的行 %.1f%% (>=20%%)" % rp))
    sp = s["decisions_with_spread_pct"] or 0.0
    v.append(("候选有差异", sp >= 50, "有差异决策 %.1f%% (>=50%%)" % sp))
    share = l["state_share_pct"]
    pot = l["state_potential_share_pct"]
    v.append(("状态项有分量（观测已用）", (share or 0) >= 10,
              ("当前状态项占评分 %.3f%% (>=10%%)" % share) if share is not None
              else "无法计算 (>=10%)"))
    v.append(("状态项有分量（完美状态的上限）", (pot or 0) >= 10,
              ("完美状态可占评分 %.2f%% (>=10%%)" % pot) if pot is not None
              else "无法计算 (>=10%)"))
    miss = o["truth_pos_but_obs_zero_pct"]
    v.append(("积压可被观测", (miss is not None) and miss <= 20,
              ("漏看率 %.1f%% (<=20%%)" % miss) if miss is not None
              else "漏看率未知 (<=20%)"))
    v.append(("接入不主导", (p["access_wait_s_total"] or 0) == 0,
              "接入等待 %ss" % p["access_wait_s_total"]))
    v.append(("无溢出/超期", (p["queue_overflow"] == 0 and p["deadline_expired"] == 0),
              "溢出 %d 超期 %d" % (p["queue_overflow"], p["deadline_expired"])))
    v.append(("可达性不混淆", (p["no_route"] or 0) == 0, "NO_ROUTE %d" % p["no_route"]))
    if arm_b is not None:
        v.append(("两臂预测确实分叉", arm_b["pred_diff_pct"] >= 1.0,
                  "预测不同 %.2f%%" % arm_b["pred_diff_pct"]))
        v.append(("两臂决策确实分叉", arm_b["decision_diff_pct"] >= 1.0,
                  "决策不同 %.2f%%" % arm_b["decision_diff_pct"]))
    return v


def page(label, m, vs):
    ok = sum(1 for _n, good, _d in vs if good)
    i, b, s = m["identity"], m["backlog"], m["spread"]
    l, o, p = m["leverage"], m["observability"], m["purity"]
    out = ["### %s" % label, ""]
    out.append("| 组 | 指标 | 值 |")
    out.append("|---|---|---|")
    out.append("| ① 身份 | cell / arm | %s / %s |" % (i["cell"], i["arm"]))
    out.append("| | trace 行 / 探针行 | %s / %s (stride %s) |"
               % (i["trace_rows"], i["probe_rows"], i["probe_stride"]))
    out.append("| | contract sha | %s |" % (i["contract_sha256"] or "")[:16])
    out.append("| | 墙钟 | %.0f s |" % i["wall_s"])
    out.append("| ② 积压 | 用到链路 / 出现过积压 | %d / %d |"
               % (b["links_seen"], b["links_with_backlog"]))
    out.append("| | 最深积压 | %.0f bit = %.1f 个包 (1 Gbps 上 %.2f ms) |"
               % (b["max_backlog_bits"], b["max_backlog_bits"] / 12000.0,
                  b["max_backlog_bits"] / 1e6))
    out.append("| | 真值>0 的行 | **%.1f%%** |" % (b["rows_truth_pos_pct"] or 0))
    out.append("| ③ 差异 | 候选有差异的决策 | **%.1f%%** |"
               % (s["decisions_with_spread_pct"] or 0))
    out.append("| | 差异中位 | %.0f bit |" % (s["spread_bits_median"] or 0))
    out.append("| ④ 杠杆 | 评分总分中位 | %.2f ms |"
               % (l["score_total_ms_median"] or 0))
    out.append("| | **状态项占评分（当前 / 完美上限）** | **%.3f%% / %.2f%%** |"
               % (l["state_share_pct"] or 0, l["state_potential_share_pct"] or 0))
    out.append("| | 前两名评分差中位 | %.2f ms |"
               % (l["top2_score_gap_ms_median"] or 0))
    out.append("| | 选中不是最空 | %.1f%%（均值可省 %.0f bit） |"
               % (l["chosen_not_emptiest_pct"] or 0, l["savable_bits_mean"] or 0))
    out.append("| ⑤ 观测 | 真值>0 却被漏看 | **%.1f%%** |"
               % (o["truth_pos_but_obs_zero_pct"] or 0))
    out.append("| | 年龄中位 / 预测跨度中位 | %.1f ms / %.1f ms (%.1fx) |"
               % (o["aoi_ms_median"] or 0, o["horizon_ms_median"] or 0,
                  o["age_over_horizon"] or 0))
    out.append("| ⑥ 纯净/代价 | 接入等待 / 停时等待 | %.1f s / %s |"
               % (p["access_wait_s_total"] or 0, p["access_waiting_at_stop"]))
    out.append("| | NO_ROUTE / 超期 / 溢出 | %d / %d / %d |"
               % (p["no_route"], p["deadline_expired"], p["queue_overflow"]))
    out.append("| | 送达率 / 控制面面积 | %.4f / %.3g |"
               % (p["delivery_rate"] or 0, m["cost"]["isl_ctrl_area"] or 0))
    out.append("")
    out.append("**判定 %d/%d**" % (ok, len(vs)))
    out.append("")
    out.append("| 要求 | 结论 | 实测 |")
    out.append("|---|---|---|")
    for name, good, detail in vs:
        out.append("| %s | %s | %s |" % (name, "PASS" if good else "FAIL", detail))
    out.append("")
    return "\n".join(out)


def arm_compare(dir_a, arm_a, dir_b, arm_b):
    pa, ca, pb, cb = {}, {}, {}, {}
    for line in open(Path(dir_a) / "arms" / arm_a / "target_probe.jsonl"):
        r = json.loads(line)
        pa[(r["pid"], r["decision_id"], r["direction"])] = r.get("predicted_bits")
        ca[(r["pid"], r["decision_id"])] = r["chosen"]
    for line in open(Path(dir_b) / "arms" / arm_b / "target_probe.jsonl"):
        r = json.loads(line)
        pb[(r["pid"], r["decision_id"], r["direction"])] = r.get("predicted_bits")
        cb[(r["pid"], r["decision_id"])] = r["chosen"]
    common = set(pa) & set(pb)
    dv = sum(1 for k in common if pa[k] != pb[k])
    ck = set(ca) & set(cb)
    dd = sum(1 for k in ck if ca[k] != cb[k])
    return {"pred_diff_pct": _pct(dv, len(common)) or 0.0,
            "decision_diff_pct": _pct(dd, len(ck)) or 0.0,
            "common_rows": len(common), "common_decisions": len(ck)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--cell-dir", required=True)
    ap.add_argument("--arm", default="candidate")
    ap.add_argument("--arm-b-dir", default=None)
    ap.add_argument("--arm-b", default="candidate")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    rows, result, summary = collect(Path(args.cell_dir), args.arm)
    m = metrics(rows, result, summary)
    ab = None
    if args.arm_b_dir:
        ab = arm_compare(args.cell_dir, args.arm, args.arm_b_dir, args.arm_b)
        m["arm_compare"] = ab
    text = page(args.label, m, verdicts(m, ab))
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json:
        Path(args.json).write_text(
            json.dumps({"label": args.label, **m}, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
