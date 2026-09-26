#!/usr/bin/env python3
"""E1 · 物理时间与 ETA 验证（独立脚本，只读）

任务书 v1.4 §E1 要求：对当前正式 config 与合法候选，核对轨道/链路距离/可用性/速率，
对照内核真实 t_q / t_s / t_f / t_a，统计 ΔT_arrival、各成分与 ETA 误差，按远离/临近几何分组。

定位与边界
----------
* 内核**没有 ETA 对象**（kernel._estimate_at_start 预测的是邻居出口队列比特数，不是时间），
  因此 ETA 与 ΔT_arrival 由本脚本自建；内核只提供被对照的真值（十一时刻）。
* 日志（决策流与折叠账本）**不含几何/距离**（candidates 只有方向字符串），传播时延的独立重算
  需要几何模块——本版本先实现不依赖几何的四类校验，几何一项显式标注 NOT_IMPLEMENTED。
* 内核用字符串 "missing" 标记不可得时刻（缺项不填 0），本脚本一律按"不可得"跳过并计数。

已实现校验
----------
A1  星座参数核对（任务书点名：66 星旧配置不得沿用示意值）
A-S 序列化固定点：constant 模式下 t_service_finish − t_service_start == packet_bits / rate
OBS 观测模式语义：refresh → t_measure == t_decision_commit；frozen → t_measure == t_decision_start
D   时间分解不重复计时：十一时刻单调性
D1  ΔT_arrival 统计本体（t_peer_arrival − t_service_finish 的分布）

用法
----
python3 ANALYSIS/ARRIVAL-TIME-T1-20260923/e1_eta_validation.py \
    --run-dir out/E1pipe --ledger out/E1pipe-ledger.json --out out/e1-report.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

TOL = 1e-9
TIMELINE_ORDER = [
    "t_measure",
    "t_decision_start",
    "t_decision_commit",
    "t_local_queue_enter",
    "t_service_start",
    "t_service_finish",
    "t_peer_arrival",
    "t_peer_redecision",
    "t_peer_target_egress_enter",
    "t_peer_target_egress_service_start",
]

KNOWN_GEOMETRY_DECLARATIONS = {
    "acceptance/bbm.yaml": {"num_satellites": 66},
    "acceptance/mbb.yaml": {"num_satellites": 66},
    "mlab_measured_od_burst.yaml": {"num_satellites": 66},
}


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def num(v):
    """内核用 "missing" 标记不可得时刻；非数值一律返回 None。"""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def check_constellation(cfg: dict, profile_hint) -> dict:
    sc = cfg.get("scenario") or {}
    got = {
        "num_satellites": sc.get("num_satellites"),
        "num_planes": sc.get("num_planes"),
        "altitude_km": sc.get("altitude_km"),
        "inclination_deg": sc.get("inclination_deg"),
        "min_elevation_deg": sc.get("min_elevation_deg"),
        "seed": sc.get("seed"),
    }
    missing = [k for k, v in got.items() if v is None]
    declared = KNOWN_GEOMETRY_DECLARATIONS.get(profile_hint or "", {})
    mismatch = {k: {"declared": declared[k], "resolved": got.get(k)} for k in declared if got.get(k) != declared[k]}
    return {
        "status": "PASS" if not missing and not mismatch else "REVIEW",
        "resolved": got,
        "missing_fields": missing,
        "declared_vs_resolved": mismatch,
        "note": (
            "任务书点名：66 星旧配置（acceptance/bbm.yaml、mbb.yaml、mlab_measured_od_burst.yaml）在仓库中零测试引用，"
            "倾角/参数不得沿用示意值。本检查只核对 resolved config 字段完整性与其自报值；"
            "与轨道真值的独立比对属 A2/A3（需几何模块，见 NOT_IMPLEMENTED）。"
        ),
    }


def check_serialization(cfg: dict, decisions: dict) -> dict:
    """A-S: 序列化时延固定点，按链路类型分别核对。

    服务时长 = packet_bits / (该类链路速率 * 1e6)。平台里至少有两类：
      * ISL：links.isl_rate_mbps（走廊 v2 = 10 Mbps -> 0.1 s）
      * 接入 GSL：access.uplink_rate_mbps / downlink_rate_mbps（= 1000 Mbps -> 0.001 s）
    第一版脚本只按 ISL 核对，导致 3525 条接入侧记录被误判为违例（误差恰为 |0.001-0.1|）；
    这里改为"观测值必须落在配置可推导的期望集合内"，并给出分类计数。
    """
    links = cfg.get("links") or {}
    access = cfg.get("access") or {}
    demand = cfg.get("demand") or {}
    mode = links.get("rate_model")
    if mode != "constant":
        return {"status": "SKIP", "reason": "rate_model=%r 不是 constant；MCS 模式下速率随距离变化，固定点不适用" % (mode,)}
    packet_bits = num(demand.get("packet_bits"))
    if not packet_bits:
        return {"status": "SKIP", "reason": "缺 packet_bits"}

    expected_by_class = {}
    for label, key, src in (
        ("isl", "isl_rate_mbps", links),
        ("access_uplink", "uplink_rate_mbps", access),
        ("access_downlink", "downlink_rate_mbps", access),
    ):
        rate = num(src.get(key))
        if rate:
            expected_by_class[label] = packet_bits / (rate * 1_000_000.0)
    if not expected_by_class:
        return {"status": "SKIP", "reason": "配置里没有任何可用速率的期望值"}

    tally = {k: 0 for k in expected_by_class}
    checked, skipped, worst = 0, 0, 0.0
    violations = []
    for did, e in decisions.items():
        ts, tf = num(e.get("t_service_start")), num(e.get("t_service_finish"))
        if ts is None or tf is None:
            skipped += 1
            continue
        actual = tf - ts
        checked += 1
        best_label, best_err = None, None
        for label, exp in expected_by_class.items():
            err = abs(actual - exp)
            if best_err is None or err < best_err:
                best_label, best_err = label, err
        worst = max(worst, best_err)
        if best_err <= TOL:
            tally[best_label] += 1
        else:
            violations.append({
                "decision_id": did, "actual_s": actual,
                "nearest_expected_s": expected_by_class[best_label],
                "nearest_class": best_label, "abs_err_s": best_err,
            })
    return {
        "status": "PASS" if checked and not violations else ("FAIL" if violations else "SKIP"),
        "expected_service_s_by_class": expected_by_class,
        "formula": "packet_bits / (link_rate_mbps * 1e6)",
        "checked_decisions": checked,
        "skipped_missing_timestamps": skipped,
        "matched_by_class": tally,
        "max_abs_error_s": worst,
        "tolerance_s": TOL,
        "violation_count": len(violations),
        "violations_sample": violations[:5],
    }


def check_obs_mode(decisions: dict) -> dict:
    stats = {"refresh": {"checked": 0, "violations": [], "skipped": 0},
             "frozen": {"checked": 0, "violations": [], "skipped": 0}}
    for did, e in decisions.items():
        m = e.get("obs_mode")
        if m not in stats:
            continue
        tm, td, tc = num(e.get("t_measure")), num(e.get("t_decision_start")), num(e.get("t_decision_commit"))
        if tm is None or td is None or tc is None:
            stats[m]["skipped"] += 1
            continue
        stats[m]["checked"] += 1
        if m == "refresh":
            if abs(tm - tc) > TOL:
                stats[m]["violations"].append({"decision_id": did, "t_measure": tm, "t_decision_commit": tc})
        else:
            if abs(tm - td) > TOL:
                stats[m]["violations"].append({"decision_id": did, "t_measure": tm, "t_decision_start": td})
    summary = {}
    for m, s in stats.items():
        summary[m] = {
            "checked": s["checked"],
            "skipped_missing": s["skipped"],
            "violation_count": len(s["violations"]),
            "violations_sample": s["violations"][:3],
            "status": "PASS" if s["checked"] and not s["violations"] else ("FAIL" if s["violations"] else "SKIP"),
        }
    overall = "FAIL" if any(v["status"] == "FAIL" for v in summary.values()) else (
        "PASS" if any(v["status"] == "PASS" for v in summary.values()) else "SKIP")
    return {"status": overall, "by_mode": summary}


def check_monotonic(decisions: dict) -> dict:
    checked, skipped = 0, 0
    violations = []
    for did, e in decisions.items():
        vals = [num(e.get(k)) for k in TIMELINE_ORDER]
        if any(v is None for v in vals):
            skipped += 1
            continue
        checked += 1
        for i in range(len(vals) - 1):
            if vals[i] - vals[i + 1] > TOL:
                violations.append({
                    "decision_id": did,
                    "pair": [TIMELINE_ORDER[i], TIMELINE_ORDER[i + 1]],
                    "values": [vals[i], vals[i + 1]],
                })
                break
    return {
        "status": "PASS" if checked and not violations else ("FAIL" if violations else "SKIP"),
        "order": TIMELINE_ORDER,
        "checked_decisions": checked,
        "skipped_missing_timestamps": skipped,
        "violation_count": len(violations),
        "violations_sample": violations[:5],
    }


def check_delta_arrival(decisions: dict) -> dict:
    deltas, skipped = [], 0
    for e in decisions.values():
        tf, ta = num(e.get("t_service_finish")), num(e.get("t_peer_arrival"))
        if tf is None or ta is None:
            skipped += 1
            continue
        deltas.append(ta - tf)
    if not deltas:
        return {"status": "SKIP", "reason": "没有可用的 t_service_finish / t_peer_arrival 对", "skipped_missing": skipped}
    deltas.sort()
    n = len(deltas)

    def pct(p):
        return deltas[min(n - 1, max(0, int(round(p * (n - 1)))))]

    return {
        "status": "PASS",
        "definition": "DeltaT_arrival := t_peer_arrival - t_service_finish",
        "n": n,
        "skipped_missing": skipped,
        "min_s": deltas[0],
        "p50_s": pct(0.50),
        "p95_s": pct(0.95),
        "max_s": deltas[-1],
        "mean_s": statistics.fmean(deltas),
        "note": "该量含传播时延 + 对端排队/重决策开销；按几何（临近/远离）分组需几何模块，见 NOT_IMPLEMENTED。",
    }


C_KM_S = 299792.458
R_EARTH_KM = 6371.0


def walker_intra_plane_chord_km(num_satellites, num_planes, altitude_km):
    """Walker 星座同轨相邻星间弦长（球面地球 + 圆轨道，独立重算，不调用平台几何）。"""
    if not num_satellites or not num_planes or altitude_km is None:
        return None
    per_plane = float(num_satellites) / float(num_planes)
    if per_plane < 2:
        return None
    import math
    angle = 2.0 * math.pi / per_plane
    return 2.0 * (R_EARTH_KM + float(altitude_km)) * math.sin(angle / 2.0)


def check_propagation_geometry(cfg: dict, decisions: dict) -> dict:
    """A2（可独立完成的部分）：Walker 闭式独立重算星间距离，与观测传播时延比对。

    观测传播时延 := t_peer_arrival - t_service_finish；观测距离 := DeltaT * c。
    独立期望 := 同轨相邻星弦长 2*(Re+h)*sin(pi / sats_per_plane)。
    这一项不读日志几何（日志也没有几何），而是用轨道参数闭式算出应有距离，
    再与内核实际发生的时刻比对——即"固定点值与当前模型一致"的直接检验。
    """
    sc = cfg.get("scenario") or {}
    h = num(sc.get("altitude_km"))
    elev = num(sc.get("min_elevation_deg"))
    expected = walker_intra_plane_chord_km(
        sc.get("num_satellites"), sc.get("num_planes"), h)
    max_slant = None
    if h is not None and elev is not None:
        import math
        _e = math.radians(elev)
        max_slant = (-R_EARTH_KM * math.sin(_e)
                     + math.sqrt((R_EARTH_KM * math.sin(_e)) ** 2 + h * h + 2.0 * R_EARTH_KM * h))
    deltas = {}
    for e in decisions.values():
        tf, ta = num(e.get("t_service_finish")), num(e.get("t_peer_arrival"))
        if tf is None or ta is None:
            continue
        d = round(ta - tf, 9)
        deltas[d] = deltas.get(d, 0) + 1
    if not deltas:
        return {"status": "SKIP", "reason": "没有可用的传播时延观测"}
    isl_rows, other_rows = [], []
    isl_count = other_count = 0
    isl_worst = None
    for d, cnt in sorted(deltas.items(), key=lambda kv: -kv[1]):
        dist = d * C_KM_S
        rel = (abs(dist - expected) / expected) if expected else None
        if rel is not None and rel <= 0.01:
            isl_rows.append({"delta_t_s": d, "observed_distance_km": dist, "count": cnt, "relative_error": rel})
            isl_count += cnt
            isl_worst = rel if isl_worst is None else max(isl_worst, rel)
        else:
            other_rows.append({"delta_t_s": d, "observed_distance_km": dist, "count": cnt})
            other_count += cnt
    oob = [r for r in other_rows
           if h is not None and (r["observed_distance_km"] < h * 0.999
                                 or (max_slant is not None and r["observed_distance_km"] > max_slant * 1.001))]
    isl_ok = bool(expected and isl_count) and (isl_worst is not None and isl_worst <= 1e-6)
    status = "PASS" if (isl_ok and not oob) else ("REVIEW" if expected else "SKIP")
    return {
        "status": status,
        "observations": sum(deltas.values()),
        "isl_class": {
            "expected_intra_plane_chord_km": expected,
            "observations": isl_count,
            "max_relative_error": isl_worst,
            "pass_tolerance": 1e-6,
        },
        "gsl_or_other_class": {
            "observations": other_count,
            "allowed_band_km": [h, max_slant],
            "out_of_band": oob[:5],
            "note": "地面-卫星斜距；本脚本只做上下界约束核对，不做逐时刻位置传播。",
        },
        "formulas": {
            "isl_intra_plane_chord": "2*(R_earth+h)*sin(pi/(n/p))",
            "gsl_max_slant": "-Re*sin(e)+sqrt((Re*sin(e))^2+h^2+2*Re*h), e=min_elevation_deg",
        },
        "details_isl": isl_rows[:6],
        "details_other": other_rows[:6],
        "note": "仍属 NOT_IMPLEMENTED 的是：逐时刻轨道位置传播（含相位因子/地球自转）、GSL 斜距、按临近/远离分组。",
    }


ISL_MCS_ZERO_RATE_BOUNDARY_KM = 5826.776601117517


def check_link_caliber(cfg: dict) -> dict:
    """口径检查：rate_model=mcs 时 max_isl_km 是否越过 MCS 零速率边界。

    平台实测边界为 5826.776601117517 km；若 max_isl_km 大于它，则区间
    [边界, max_isl_km) 内的链路"几何可用但速率为 0"，属于几何可用 != 可服务的口径缺口。
    """
    links = cfg.get("links") or {}
    mode = links.get("rate_model")
    max_km = num(links.get("max_isl_km"))
    if mode != "mcs" or max_km is None:
        return {"status": "SKIP", "reason": "仅在 rate_model=mcs 且配置了 max_isl_km 时适用",
                "rate_model": mode, "max_isl_km": max_km}
    gap = max_km > ISL_MCS_ZERO_RATE_BOUNDARY_KM
    return {
        "status": "REVIEW" if gap else "PASS",
        "rate_model": mode,
        "max_isl_km": max_km,
        "mcs_zero_rate_boundary_km": ISL_MCS_ZERO_RATE_BOUNDARY_KM,
        "zero_rate_band_km": [ISL_MCS_ZERO_RATE_BOUNDARY_KM, max_km] if gap else None,
        "note": "该区间内链路几何可用但 MCS 速率为 0；constant 模式不复现 legacy 链路预算（见 test_link_budget_characterization.py）。",
    }


def filter_by_outcome(decisions: dict, timeline_path):
    """按 outcome 过滤被中断的服务窗口（E1-A 发现的风险点）。

    ledger 的 t_service_finish 取该 decision 下**第一条** service_finish 里程碑
    （decision_ledger._first_at），而 _transmit 中断后会重算并再次写入 ⇒ 该字段可能是
    失败窗口的结束时刻。本函数读 timeline 流的 service_finish 里程碑，剔除非 ok 的 decision。
    """
    if not timeline_path:
        return decisions, {"timeline_provided": False, "checked": 0, "dropped_non_ok": 0,
                           "multiple_finish_milestones": 0}
    tp = Path(timeline_path)
    if not tp.exists():
        return decisions, {"timeline_provided": False, "reason": "timeline 文件不存在", "checked": 0,
                           "dropped_non_ok": 0, "multiple_finish_milestones": 0}
    seen = {}
    with tp.open(encoding="utf-8") as f:
        for line in f:
            if '"service_finish"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            did = r.get("decision_id")
            if did is None:
                continue
            seen.setdefault(did, []).append(r.get("outcome"))
    dropped, multiple = [], 0
    for did, outs in seen.items():
        if len(outs) > 1:
            multiple += 1
        if outs and outs[0] not in (None, "ok"):
            dropped.append(did)
    drop_set = set(dropped)
    kept = {k: v for k, v in decisions.items() if k not in drop_set}
    return kept, {
        "timeline_provided": True,
        "decisions_with_service_finish": len(seen),
        "dropped_non_ok": len(dropped),
        "multiple_finish_milestones": multiple,
        "dropped_sample": dropped[:5],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile-hint", default=None)
    ap.add_argument("--timeline", type=Path, default=None,
                    help="可选：timeline.jsonl，用于按 outcome 过滤被中断的服务窗口")
    args = ap.parse_args(argv)

    cfg_path = args.run_dir / "resolved_config.json"
    for p in (cfg_path, args.ledger):
        if not p.exists():
            print(json.dumps({"status": "input_missing", "path": str(p)}, ensure_ascii=False))
            return 3

    resolved = load_json(cfg_path)
    cfg = resolved.get("config", resolved)
    ledger = load_json(args.ledger)
    decisions_all = ledger.get("by_decision") or {}
    decisions, outcome_filter = filter_by_outcome(decisions_all, args.timeline)

    receipt = {}
    rp = args.run_dir / "receipt.json"
    if rp.exists():
        r = load_json(rp)
        receipt = {k: r.get(k) for k in ("code_sha256", "config_sha256", "trace_sha256", "schema") if k in r}

    report = {
        "schema": "leo-t1-e1-validation/v1",
        "identity": receipt,
        "ledger_schema": ledger.get("schema"),
        "decisions": len(decisions),
        "decisions_all": len(decisions_all),
        "outcome_filter": outcome_filter,
        "ledger_diagnostics": ledger.get("diagnostics"),
        "checks": {
            "A1_constellation_parameters": check_constellation(cfg, args.profile_hint),
            "A_serialization_fixed_point": check_serialization(cfg, decisions),
            "OBS_observation_mode_semantics": check_obs_mode(decisions),
            "D_timeline_monotonicity": check_monotonic(decisions),
            "D1_delta_arrival_statistics": check_delta_arrival(decisions),
            "A2_propagation_geometry_fixed_point": check_propagation_geometry(cfg, decisions),
            "A3_link_caliber_mcs_zero_rate": check_link_caliber(cfg),
        },
        "NOT_IMPLEMENTED": {
            "A2_A3_geometry_and_availability_fixed_points": "需独立重算星座几何（轨道参数+时刻 → 位置 → 星间距离）并与平台模型比对；日志不含距离。",
            "A4_script_vs_kernel_per_hop_crosscheck": "同上，依赖几何模块。",
            "ETA_error_by_geometry_group": "ΔT_arrival 的临近/远离分组依赖几何事件判定，同上。",
        },
    }
    statuses = [c.get("status") for c in report["checks"].values()]
    report["overall_status"] = "FAIL" if "FAIL" in statuses else ("PASS" if "PASS" in statuses else "SKIP")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "status": report["overall_status"],
        "out": str(args.out),
        "decisions": report["decisions"],
        "checks": {k: v.get("status") for k, v in report["checks"].items()},
    }, ensure_ascii=False, indent=1))
    return 0 if report["overall_status"] != "FAIL" else 2


if __name__ == "__main__":
    sys.exit(main())
