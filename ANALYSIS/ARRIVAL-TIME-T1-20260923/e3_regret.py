#!/usr/bin/env python3
"""E3 · 配对反事实后悔值诊断（探索性；O0/O1/O3，替代 O2strong 对照）

设计（冻结、可审计；详见 03-preregistration.md §11 与 04-counterfactual-validation.md）
----------------------------------------------------------------------------------
基线策略 policy=capacity（平台唯一消费邻居广告队列的确定性策略，routing.py:247-279）。
事实分支：直接用基线运行（chosen 动作不可重放——重放会因 action_changed 断言拒绝）。
反事实分支：对每个**非选中**合法候选各跑一次强制重放，取该分支自己的结局与真值。
信息源：O0 缓存广告值；O1 分支 truth_at_commit.candidate_truth[dir].peer_egress_queue_bits
        （缺失时回退到 downstream 三项之和）；O3 分支 truth_at_target.egress（未送达则天然缺失）。
冻结评分器：v(d) 最小者胜，并列按方向字典序。
代价：目标包未送达 = PENALTY；送达 = 末里程碑时刻 − 发包时刻。
后悔值：Regret(arm) = cost(该臂所选动作) − min_a cost(a)。
主指标替代：Regret(O1) − Regret(O3)（**非** O2strong 对照，结论必须标注探索性）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from CODE.leo_sim import decision_ledger as dl  # noqa: E402

PENALTY = 10.0
DELIVERED_ARRIVALS = 2  # 走廊观察：送达包至少 2 次 peer_arrival（3 跳路径含首跳服务）


def scalar_downstream(ds):
    if not isinstance(ds, dict):
        return None
    parts = [ds.get("peer_egress_data_bits"), ds.get("peer_egress_ctrl_bits"),
             ds.get("peer_in_service_bits")]
    if all(p is None for p in parts):
        return None
    return sum(p or 0 for p in parts)


def o1_value(crow, direction):
    ct = ((crow.get("truth_at_commit") or {}).get("candidate_truth") or {}).get(direction) or {}
    v = ct.get("peer_egress_queue_bits")
    if isinstance(v, (int, float)):
        return float(v)
    return scalar_downstream(ct.get("downstream"))


def o3_value(crow):
    eg = (crow.get("truth_at_target") or {}).get("egress")
    if not isinstance(eg, dict):
        return None
    parts = [eg.get("data_bits"), eg.get("ctrl_bits"), eg.get("in_service_bits")]
    if all(p is None for p in parts):
        return None
    return sum(p or 0 for p in parts)


def choose_arm(values, legal):
    best, best_v = None, None
    for d in sorted(legal):
        v = values.get(d)
        v = float("inf") if v is None else v
        if best_v is None or v < best_v:
            best, best_v = d, v
    return best


def packet_cost(timeline_rows, pid, emit_t):
    ms = [r for r in timeline_rows if str(r.get("pid")) == str(pid)]
    if not ms:
        return None, False, 0
    last = max(r.get("at") or 0.0 for r in ms)
    arrivals = sum(1 for r in ms if r.get("milestone") == "peer_arrival")
    delivered = arrivals >= DELIVERED_ARRIVALS
    return (last - emit_t if delivered else PENALTY), delivered, arrivals


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--snapshots", type=int, default=2)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--keep-raw", action="store_true")
    args = ap.parse_args(argv)

    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    by_decision = ledger["by_decision"]
    base_timeline = [json.loads(l) for l in (args.run_dir / "timeline.jsonl").open(encoding="utf-8")]

    snapshots = []
    for did, e in sorted(by_decision.items(), key=lambda kv: int(kv[0])):
        legal = sorted((e.get("observation_at_start") or {}).get("legal_directions") or [])
        if len(legal) >= 2:
            snapshots.append((int(did), legal))
        if len(snapshots) >= args.snapshots:
            break
    if not snapshots:
        print(json.dumps({"status": "no_snapshots"}, ensure_ascii=False))
        return 3

    args.work.mkdir(parents=True, exist_ok=True)
    report = {"schema": "leo-t1-e3-exploratory/v1", "policy": "capacity",
              "penalty_s": PENALTY, "delivered_arrivals_threshold": DELIVERED_ARRIVALS,
              "snapshots": []}

    for did, legal in snapshots:
        base_e = by_decision[str(did)]
        emit_t = float(base_e.get("t") or base_e.get("t_decision_start") or 0.0)
        pid = base_e.get("pid")
        chosen = base_e.get("chosen")
        neigh = (base_e.get("observation_at_start") or {}).get("neighbours") or {}
        o0 = {}
        for d in legal:
            vals = [(pr or {}).get("advertised_isl_queue_bits", {}).get(d)
                    for pr in neigh.values() if isinstance(pr, dict)]
            vals = [v for v in vals if isinstance(v, (int, float))]
            o0[d] = max(vals) if vals else None

        snap = {"decision_id": did, "legal": legal, "chosen": chosen, "pid": pid,
                "O0_values": o0, "branches": {}}

        # 事实分支：直接用基线运行
        cost, delivered, arrivals = packet_cost(base_timeline, pid, emit_t)
        snap["branches"][chosen] = {
            "status": "ok", "source": "baseline_run",
            "O1_values": {d: o1_value(base_e, d) for d in legal},
            "O3_value_for_this_branch": o3_value(base_e),
            "contended_direction": (base_e.get("truth_at_target") or {}).get("contended_direction"),
            "cost_s": cost, "delivered": delivered, "arrivals": arrivals,
        }

        # 反事实分支：逐个非选中候选做强制重放
        for d in legal:
            if d == chosen:
                continue
            raw = args.work / ("cf-%s-%s.json" % (did, d))
            if raw.exists():
                raw.unlink()
            cmd = [sys.executable, "-B", "-m", "CODE.experiment_platform.replay_counterfactual",
                   "--config", str(args.config), "--decision-id", str(did),
                   "--forced-action", d, "--root", ".", "--out", str(raw)]
            p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
            if p.returncode or not raw.exists():
                snap["branches"][d] = {"status": "replay_failed",
                                       "err": (p.stderr or p.stdout or "")[-200:].strip()}
                continue
            j = json.loads(raw.read_text())
            ver = j.get("verification") or {}
            cf = j.get("counterfactual") or {}
            by_dec, _d = dl.build_ledger(cf.get("decision_rows") or [], cf.get("timeline_rows") or [])
            crow = by_dec.get(str(did)) or by_dec.get(did) or {}
            c_cost, c_delivered, c_arr = packet_cost(cf.get("timeline_rows") or [], pid, emit_t)
            snap["branches"][d] = {
                "status": "ok", "source": "forced_replay",
                "branch_states_identical": ver.get("branch_states_identical"),
                "legal_at_branch_point": ver.get("legal_at_branch_point"),
                "action_changed": ver.get("action_changed"),
                "O1_values": {dd: o1_value(crow, dd) for dd in legal},
                "O3_value_for_this_branch": o3_value(crow),
                "contended_direction": (crow.get("truth_at_target") or {}).get("contended_direction"),
                "cost_s": c_cost, "delivered": c_delivered, "arrivals": c_arr,
            }
            if not args.keep_raw:
                raw.unlink()
        report["snapshots"].append(snap)

    summary = []
    for snap in report["snapshots"]:
        brs = {d: b for d, b in snap["branches"].items() if b.get("status") == "ok"}
        if len(brs) < 2:
            summary.append({"decision_id": snap["decision_id"], "status": "insufficient_branches",
                            "detail": {d: b.get("status") for d, b in snap["branches"].items()}})
            continue
        costs = {d: b["cost_s"] for d, b in brs.items() if b.get("cost_s") is not None}
        if len(costs) < 2:
            summary.append({"decision_id": snap["decision_id"], "status": "cost_missing"})
            continue
        best = min(costs.values())
        o1_vals = {d: (brs[d].get("O1_values") or {}).get(d) for d in brs}
        o3_vals = {d: brs[d].get("O3_value_for_this_branch") for d in brs}
        arms = {"O0": snap["O0_values"], "O1": o1_vals, "O3": o3_vals}
        row = {"decision_id": snap["decision_id"], "legal": snap["legal"], "chosen": snap["chosen"],
               "costs_s": costs, "best_cost_s": best, "arms": {}}
        for arm, vals in arms.items():
            pick = choose_arm(vals, list(brs.keys()))
            row["arms"][arm] = {"values": vals, "pick": pick,
                                "regret_s": (costs[pick] - best) if pick in costs else None}
        r1 = row["arms"]["O1"]["regret_s"]
        r3 = row["arms"]["O3"]["regret_s"]
        row["exploratory_regret_O1_minus_O3"] = (r1 - r3) if (r1 is not None and r3 is not None) else None
        summary.append(row)

    report["summary"] = summary
    report["note"] = ("探索性：对照为 O0/O1/O3，**不是** O2strong；O2（共同未来真值）平台未记录，"
                      "故主指标的确认性比较不可执行。事实分支取自基线运行（选中动作不可重放）。")
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"status": "ok", "out": str(args.out), "snapshots": len(report["snapshots"]),
                      "summary": summary}, ensure_ascii=False, indent=1)[:2200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
