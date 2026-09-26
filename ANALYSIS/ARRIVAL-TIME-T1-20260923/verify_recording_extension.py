#!/usr/bin/env python3
"""记录面扩展的「应用后验证」脚本（对应 08-recording-gap-proposal.md §6 的五条验收标准）。

用法（补丁应用后）：
  python3 ANALYSIS/ARRIVAL-TIME-T1-20260923/verify_recording_extension.py \
      --config out/E3cap.yaml --baseline-decisions out/E3cap/decisions.jsonl --out out/verify-recording.json

覆盖：
  标准 1 记录结构正确：多候选决策的 info_audit.cache_entries[*].payload_values 覆盖其观察到的全部 origin/方向
  标准 2 语义无漂移：与补丁前的决策流逐位比较（chosen / t / candidates 序列）
  标准 4 E1 保真度不回退：调用 e3_scorer 的自证（由 --skip-scorer 可跳过）
  标准 5 体量：统计决策流增量（字节/决策）
标准 3（在线边界未破）需跑平台测试套件，本脚本只做提示。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--baseline-decisions", type=Path, required=True,
                    help="补丁前同一 config/seed 的 decisions.jsonl，用于逐位比较")
    ap.add_argument("--work", type=Path, default=Path("out/verify-recording"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--skip-scorer", action="store_true")
    a = ap.parse_args(argv)

    outd = (REPO_ROOT / a.work) if not a.work.is_absolute() else a.work
    if outd.exists():
        shutil.rmtree(outd)
    outd.mkdir(parents=True)

    # 跑一次新代码
    p = subprocess.run([sys.executable, "-B", "-m", "CODE.leo_sim", "run", "--config", str(a.config),
                        "--out", str(outd), "--decision-log", str(outd / "decisions.jsonl"),
                        "--timeline-log", str(outd / "timeline.jsonl")],
                       capture_output=True, text=True, cwd=REPO_ROOT)
    res = {"run_exit": p.returncode}
    if p.returncode:
        res["error"] = ((p.stdout or "") + (p.stderr or ""))[-300:]
        a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 2

    new_rows = [json.loads(l) for l in (outd / "decisions.jsonl").open(encoding="utf-8")]
    base_rows = [json.loads(l) for l in a.baseline_decisions.open(encoding="utf-8")]

    # 标准 2：决策序列逐位比较（记录不参与决策 -> 应完全相同）
    def sig(rows):
        return [(r.get("decision_id"), r.get("chosen"), r.get("t"), tuple(r.get("candidates") or [])) for r in rows]
    same = sig(new_rows) == sig(base_rows)
    res["criterion2_semantics_unchanged"] = {
        "identical": same, "new_rows": len(new_rows), "baseline_rows": len(base_rows),
        "note": "记录字段不参与决策；不一致说明补丁改变了语义（应回退）",
    }

    # 标准 1：payload_values 覆盖度
    checked = covered = 0
    misses = []
    for r in new_rows:
        obs = r.get("observation_at_start") or {}
        legal = sorted(obs.get("legal_directions") or [])
        if len(legal) < 2:
            continue
        ia = r.get("info_audit") or {}
        ce = ia.get("cache_entries") or {}
        if not ce:
            continue
        checked += 1
        ok = True
        for origin, rec in (obs.get("neighbours") or {}).items():
            ent = ce.get(str(origin)) or {}
            pv = ent.get("payload_values")
            if not isinstance(pv, dict):
                ok = False
                break
        if ok:
            covered += 1
        elif len(misses) < 3:
            misses.append(r.get("decision_id"))
    res["criterion1_structure"] = {"checked_multi_candidate": checked, "fully_covered": covered,
                                   "sample_missing": misses}

    # 标准 5：体量
    new_bytes = (outd / "decisions.jsonl").stat().st_size
    base_bytes = a.baseline_decisions.stat().st_size
    res["criterion5_size"] = {
        "new_bytes": new_bytes, "baseline_bytes": base_bytes,
        "delta_bytes": new_bytes - base_bytes,
        "delta_per_decision": (new_bytes - base_bytes) / max(1, len(new_rows)),
        "estimate_from_proposal_bytes_per_decision": 384,
    }

    # 标准 4：评分器自证（可选）
    if not a.skip_scorer:
        sc = outd.parent / "verify-recording-scorer.json"
        sp = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve().parent / "e3_scorer.py"),
                             "--ledger", str(outd.parent / "verify-recording-ledger.json"),
                             "--run-dir", str(outd), "--decisions", str(outd / "decisions.jsonl"),
                             "--limit", "100", "--out", str(sc)],
                            capture_output=True, text=True, cwd=REPO_ROOT)
        res["criterion4_scorer"] = {"exit": sp.returncode,
                                    "note": "需先折叠账本；未折叠时该项会失败，属预期",
                                    "stderr_tail": (sp.stderr or "")[-160:]}

    res["criterion3_online_boundary"] = {
        "status": "需跑平台测试套件",
        "command": "python3 -m pytest CODE/leo_sim/tests -q -k 'whitelist or info or audit'",
    }
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in res if k.startswith("criterion") or k == "run_exit"},
                     ensure_ascii=False, indent=1)[:1800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
