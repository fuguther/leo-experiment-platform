#!/usr/bin/env python3
"""E2-F · 三因素单因素臂生成与「严格单因素」校验（只解析配置，不跑仿真）

任务书 §5A 要求：
  * 固定基准，避免伪单因素；
  * 每轮只改一个参数族；
  * 必须在 expanded resolved config / event ledger 中验证其他因素未变化。

本脚本用平台自己的配置解析器（CODE.leo_sim.config.resolve_config）为每个臂生成
**resolved config**，逐字段 diff 出与基线的差异，并断言差异集合不超过该轴的允许集合。
它不运行任何仿真、不写仓库以外的东西。

用法
----
python3 ANALYSIS/ARRIVAL-TIME-T1-20260923/e2f_arms.py \
    --profile CODE/leo_sim/profiles/t1_pressure_corridor_v2.yaml \
    --outdir ANALYSIS/ARRIVAL-TIME-T1-20260923/e2f-arms
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from CODE.leo_sim import config as leo_config  # noqa: E402

# 臂定义：(名称, 轴, 允许变化的字段集合, overrides)
ARMS = [
    ("base", "baseline", set(), {}),
    ("F1_offered_9", "F1_background_traffic", {"demand.offered_mbps"}, {"demand": {"offered_mbps": 9.0}}),
    ("F1_offered_15", "F1_background_traffic", {"demand.offered_mbps"}, {"demand": {"offered_mbps": 15.0}}),
    ("F2_delay_0p05", "F2_forwarding_efficiency", {"execution.node_process_delay_s"},
     {"execution": {"node_process_delay_s": 0.05}}),
    ("F2_delay_0p15", "F2_forwarding_efficiency", {"execution.node_process_delay_s"},
     {"execution": {"node_process_delay_s": 0.15}}),
    ("F3_isl_rate_8", "F3_link_bandwidth", {"links.isl_rate_mbps"}, {"links": {"isl_rate_mbps": 8.0}}),
    ("F3_isl_rate_12p5", "F3_link_bandwidth", {"links.isl_rate_mbps"}, {"links": {"isl_rate_mbps": 12.5}}),
    ("NEG_no_queue", "negative_control", {"demand.offered_mbps"}, {"demand": {"offered_mbps": 1.0}}),
]

# sha256 与 canonical_json 都是"配置内容的派生表示"，内容一变它们必变，不属于参数差异。
VOLATILE = {"sha256", "config.canonical_json", "canonical_json"}


def flatten(obj, prefix=""):
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(flatten(v, prefix + ("." if prefix else "") + str(k)))
    elif isinstance(obj, list):
        out[prefix] = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    else:
        out[prefix] = obj
    return out


def resolve(profile_path: Path, overrides: dict) -> dict:
    """profile= 只认注册表里的命名 profile（当前仅 smoke），因此走 user= 文件路径。"""
    user = leo_config.load_config_file(str(profile_path))
    if isinstance(user, dict) and "config" in user:
        user = user["config"]
    return leo_config.resolve_config(user=user, overrides=overrides or None)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", type=Path, required=True)
    ap.add_argument("--outdir", type=Path, required=True)
    args = ap.parse_args(argv)

    args.outdir.mkdir(parents=True, exist_ok=True)
    base_user = leo_config.load_config_file(str(args.profile))
    if isinstance(base_user, dict) and "config" in base_user:
        base_user = base_user["config"]

    def deep_merge(dst, src):
        out = json.loads(json.dumps(dst))
        for k, v in (src or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = deep_merge(out[k], v)
            else:
                out[k] = v
        return out

    resolved_by_arm = {}
    for name, axis, _allowed, ov in ARMS:
        # resolved 包装层：用于严格单因素 diff（证据）
        resolved = resolve(args.profile, ov)
        resolved_by_arm[name] = resolved
        (args.outdir / (name + ".resolved.json")).write_text(
            json.dumps(resolved, ensure_ascii=False, indent=1), encoding="utf-8")
        # 用户层：运行入口（--config）真正接受的形态
        (args.outdir / (name + ".config.json")).write_text(
            json.dumps(deep_merge(base_user, ov), ensure_ascii=False, indent=1), encoding="utf-8")

    def flat_norm(resolved):
        out = {}
        for k, v in flatten(resolved).items():
            if k in VOLATILE:
                continue
            out[k[len("config."):] if k.startswith("config.") else k] = v
        return out

    base = flat_norm(resolved_by_arm["base"])
    report = {"schema": "leo-t1-e2f-arms/v1", "profile": str(args.profile), "arms": []}
    strict_ok = True
    for name, axis, allowed, _ov in ARMS:
        flat = flat_norm(resolved_by_arm[name])
        changed = sorted(k for k in set(base) | set(flat)
                         if base.get(k, "<absent>") != flat.get(k, "<absent>"))
        extra = sorted(set(changed) - allowed)
        if extra:
            strict_ok = False
        report["arms"].append({
            "arm": name,
            "axis": axis,
            "config_sha256": resolved_by_arm[name].get("sha256"),
            "changed_fields_vs_base": changed,
            "allowed_fields": sorted(allowed),
            "unexpected_changes": extra,
            "strict_single_factor": not extra,
        })
    report["strict_single_factor_overall"] = strict_ok
    (args.outdir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(json.dumps({
        "strict_single_factor_overall": strict_ok,
        "arms": [{"arm": a["arm"], "axis": a["axis"], "sha": (a["config_sha256"] or "")[:16],
                  "changed": a["changed_fields_vs_base"], "unexpected": a["unexpected_changes"]}
                 for a in report["arms"]],
    }, ensure_ascii=False, indent=1))
    return 0 if strict_ok else 2


if __name__ == "__main__":
    sys.exit(main())
