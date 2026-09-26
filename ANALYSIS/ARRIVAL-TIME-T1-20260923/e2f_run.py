#!/usr/bin/env python3
"""E2-F · 三因素单因素诊断运行器（本地诊断，非 research_eligible）

对 e2f-arms/*.resolved.json 逐个执行：
  仿真（带 decision/timeline 双流日志）→ 官方 scene_check 分类 →
  F2 记账（node_process_spans + verify_delay_decomposition）→ ΔT_arrival 统计
输出 summary.json 与可粘贴的结果表。

注意（本轮实测得到的硬要求）：
  * node_process_delay_s > 0 必须带 --timeline-log，且日志父目录链不得含符号链接；
  * F2 分解必须传 node_spans_by_pid，否则 F2 时间会被静默记成 decision_compute；
  * node_process_spans() 遇地平线未闭合区间会抛错，本脚本记录并剔除该 pid 后继续。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import statistics
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from CODE.leo_sim import metrics_independent as mi  # noqa: E402


def run_arm(arm_path: Path, work: Path, contract: str, py: str) -> dict:
    name = arm_path.name.replace(".config.json", "")
    # scene_check 要求 run-dir 位于 project root 之内，因此运行目录必须在根内。
    outd = REPO_ROOT / "out" / "e2f" / name
    if outd.exists():
        shutil.rmtree(outd)
    outd.mkdir(parents=True)
    cmd = [py, "-B", "-m", "CODE.leo_sim", "run", "--config", str(arm_path), "--out", str(outd),
           "--decision-log", str(outd / "decisions.jsonl"), "--timeline-log", str(outd / "timeline.jsonl")]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    row = {"arm": name, "run_exit": p.returncode}
    if p.returncode:
        row["error"] = (p.stderr or p.stdout)[-200:]
        return row

    # --- scene_check ---
    sc = work / f"{name}-sc.json"
    sc = outd.parent / f"{name}-sc.json"
    rel_run = str(outd.relative_to(REPO_ROOT))
    sp = subprocess.run([py, "-B", "-m", "CODE.leo_sim.scene_check", "--root", ".", "--contract", contract,
                         "--run-dir", rel_run, "--out", str(sc)], capture_output=True, text=True, cwd=REPO_ROOT)
    row["scene_check_exit"] = sp.returncode
    if sc.exists():
        j = json.loads(sc.read_text())
        acc = j.get("access") or {}
        links = ((j.get("isl") or {}).get("links") or {})
        utils = [v.get("utilization") or 0 for v in links.values()] if isinstance(links, dict) else []
        row.update({
            "scene_status": j.get("status"),
            "admission_rate": acc.get("admission_rate"),
            "access_clean": j.get("access_clean"),
            "route_clean": j.get("route_clean"),
            "downlink_clean": j.get("downlink_clean"),
            "isl_pressure_ok": j.get("isl_pressure_ok"),
            "max_isl_utilization": max(utils) if utils else None,
        })

    # --- ΔT_arrival 统计（从 timeline 直接算）---
    fin, arr = {}, {}
    for line in (outd / "timeline.jsonl").open(encoding="utf-8"):
        if '"service_finish"' in line:
            r = json.loads(line)
            if r.get("decision_id") is not None and r["decision_id"] not in fin:
                fin[r["decision_id"]] = r.get("at")
        elif '"peer_arrival"' in line:
            r = json.loads(line)
            if r.get("decision_id") is not None and r["decision_id"] not in arr:
                arr[r["decision_id"]] = r.get("at")
    deltas = sorted(a - fin[d] for d, a in arr.items() if d in fin and a is not None and fin[d] is not None)
    if deltas:
        n = len(deltas)
        row["delta_t_arrival"] = {
            "n": n, "min_s": deltas[0],
            "p50_s": deltas[n // 2], "p95_s": deltas[min(n - 1, int(0.95 * n))], "max_s": deltas[-1],
        }

    # --- F2 记账 ---
    led = json.loads((outd / "ledgers.json").read_text())
    trows = [json.loads(l) for l in (outd / "timeline.jsonl").open(encoding="utf-8")]
    # 未闭合区间必须迭代排除：只把每条待处理记录的**第一个字段**（pid）当 pid 剔，
    # 不能把消息里的卫星号等其它数字一并当成 pid（否则会把无关 pid 也剔除）。
    spans, excluded = {}, set()
    for _ in range(50):
        keep = [r for r in trows if str(r.get("pid")) not in excluded]
        try:
            spans = mi.node_process_spans(keep)
            break
        except mi.IndependentMetricsError as exc:
            msg = str(exc)
            inside = msg[msg.find("[") + 1: msg.rfind("]")]
            pids = set()
            for rec in inside.split("),"):
                rec = rec.strip().lstrip("(").strip()
                if not rec:
                    continue
                first = rec.split(",")[0].strip().strip("'").strip()
                if first.isdigit():
                    pids.add(first)
            if not pids or pids <= excluded:
                spans = {}
                break
            excluded |= pids
    unclosed = len(excluded)
    fates = led.get("packet_fates") or {}
    delivered = []
    for k, v in fates.items():
        f = v[0] if isinstance(v, list) and v else v
        if f == "DELIVERED":
            delivered.append(k)
    rep = mi.verify_delay_decomposition(led.get("packet_events") or [], led.get("link_service_windows") or [],
                                        delivered, node_spans_by_pid=spans)
    row["f2_accounting"] = {
        "node_process_spans_supplied": rep.get("node_process_spans_supplied"),
        "total_node_process_s": rep.get("total_node_process_s"),
        "total_decision_compute_s": rep.get("total_decision_compute_s"),
        "max_abs_residual_s": rep.get("max_abs_residual_s"),
        "ok": rep.get("ok"),
        "checked_packets": rep.get("checked_packets"),
        "unclosed_span_pids": unclosed,
        "excluded_pids": sorted(excluded),
        "span_pids": len(spans),
    }
    return row


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms-dir", type=Path, required=True)
    ap.add_argument("--workdir", type=Path, required=True)
    ap.add_argument("--contract", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

    args.workdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for arm in sorted(args.arms_dir.glob("*.config.json")):
        print(f"[run] {arm.name}", flush=True)
        rows.append(run_arm(arm, args.workdir, args.contract, args.python))
        print(f"[done] {arm.name} -> {rows[-1].get('scene_status')}", flush=True)
    args.out.write_text(json.dumps({"schema": "leo-t1-e2f-diagnostic/v1", "arms": rows}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    header = ["arm", "scene_status", "admission_rate", "max_isl_utilization", "isl_pressure_ok",
              "delta_p50_s", "f2_total_s", "spans_supplied", "unclosed"]
    print("\t".join(header))
    for r in rows:
        d = r.get("delta_t_arrival") or {}
        f = r.get("f2_accounting") or {}
        print("\t".join(str(x) for x in [
            r["arm"], r.get("scene_status"), r.get("admission_rate"), r.get("max_isl_utilization"),
            r.get("isl_pressure_ok"), d.get("p50_s"), f.get("total_node_process_s"),
            f.get("node_process_spans_supplied"), f.get("unclosed_span_pids")]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
