#!/usr/bin/env python3
"""SUPERSEDED（2026-09-25T02:45+08:00）—— 不要用本脚本出结论。

原因：本脚本的 O3 注入只覆盖「候选首跳 peer」的广告队列。05 §9 已实测证明，在 capacity 策略下决策相关的远端边属于**路径上的其他卫星**，而这些卫星的缓存真值**平台未记录**（只有 candidate_truth 覆盖首跳 peer）。因此本脚本的注入无法驱动翻转；其输出（若有）只反映记录面缺口，不反映信息价值。

正确做法见 08-recording-gap-proposal.md：先扩记录面（落盘全部缓存条目数值），再重跑 O0/O1/O3。
本文件保留为尝试记录与失败轨迹，不参与任何交付结论。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from CODE.leo_sim import model, routing  # noqa: E402
from CODE.leo_sim import decision_ledger as dl  # noqa: E402

C_KM_S = 299792.458
prop_delay = lambda km: km / C_KM_S


def build_ctx(resolved):
    sc, lk = resolved["scenario"], resolved["links"]
    geom = model.Constellation(sc["num_satellites"], sc["num_planes"], sc["altitude_km"],
                              sc["inclination_deg"], sc["min_elevation_deg"],
                              max_isl_km=lk["max_isl_km"], geometry_epoch_s=sc["geometry_epoch_s"])
    topo = routing.build_topology(geom, sc["num_satellites"], lk["isl_dirs"], t=None)
    rev = routing._reverse_adj(topo)
    return geom, topo, rev, {u: sorted(rev.get(u, ())) for u in rev}


def dir_of(topo, a, b):
    for d, n in (topo.get(a) or {}).items():
        if n == b:
            return d
    return None


def scores(ctx, sat, targets, own, legal, pq, rate, now):
    geom, topo, rev, sorted_rev = ctx

    def edge(a, b):
        c = prop_delay(geom.isl_range_km(a, b, now))
        d = dir_of(topo, a, b)
        if d is None:
            return float("inf")
        qb = own.get(d, 0) if a == sat else (pq.get(a) or {}).get(d)
        return float("inf") if qb is None else c + qb / max(1.0, rate)

    if not targets:
        return {d: float("inf") for d in legal}
    dist = routing._multi_source_dist(rev, targets, lambda u, v: edge(v, u), sorted_adj=sorted_rev)
    out = {}
    for d in legal:
        n = (topo.get(sat) or {}).get(d)
        if n is None:
            out[d] = float("inf"); continue
        w = edge(sat, n)
        out[d] = float("inf") if w == float("inf") else w + dist.get(n, float("inf"))
    return out


def pick(sc, legal):
    best, bv = None, None
    for d in sorted(legal):
        v = sc.get(d, float("inf"))
        if bv is None or v < bv:
            best, bv = d, v
    return best


def base_peer_queue(obs):
    pq = {}
    targets = []
    for origin, rec in (obs.get("neighbours") or {}).items():
        adv = (rec or {}).get("advertised_isl_queue_bits") or {}
        try:
            pq[int(origin)] = {k: v for k, v in adv.items() if isinstance(v, (int, float))}
        except (TypeError, ValueError):
            pass
    return pq, targets


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--decisions", type=Path, required=True)
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--snapshots", type=int, default=5)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)

    resolved = json.loads((a.run_dir / "resolved_config.json").read_text())["config"]
    ctx = build_ctx(resolved)
    rate = float(resolved["links"]["isl_rate_mbps"]) * 1_000_000.0
    dst_by_id, chosen_by_id = {}, {}
    for line in a.decisions.open(encoding="utf-8"):
        r = json.loads(line)
        dst_by_id[str(r["decision_id"])] = r.get("dst")
        chosen_by_id[str(r["decision_id"])] = r.get("chosen")
    bd = json.loads(a.ledger.read_text())["by_decision"]
    a.work.mkdir(parents=True, exist_ok=True)

    rows = []
    for did, e in sorted(bd.items(), key=lambda kv: int(kv[0])):
        obs = e.get("observation_at_start") or {}
        legal = sorted(obs.get("legal_directions") or [])
        if len(legal) < 2:
            continue
        sat, now = obs.get("sat"), (e.get("t_decision_start") or e.get("t") or 0.0)
        dst = dst_by_id.get(str(did))
        if sat is None or dst is None:
            continue
        pq0, _ = base_peer_queue(obs)
        targets = []
        for origin, rec in (obs.get("neighbours") or {}).items():
            if dst in ((rec or {}).get("advertised_serve_cells") or []):
                try: targets.append(int(origin))
                except (TypeError, ValueError): pass
        own = obs.get("own_queue_bits") or {}
        s0 = scores(ctx, sat, targets, own, legal, pq0, rate, now)
        # O1 注入
        ct = ((e.get("truth_at_commit") or {}).get("candidate_truth") or {})
        pq1 = {k: dict(v) for k, v in pq0.items()}
        for d in legal:
            peer = ((ct.get(d) or {}).get("edge") or [None, None])[1]
            val = (ct.get(d) or {}).get("peer_egress_queue_bits")
            if peer is not None and isinstance(val, (int, float)):
                onward = ((ct.get(d) or {}).get("downstream") or {}).get("peer_egress_direction") or d
                pq1.setdefault(int(peer), {})[onward] = val
        s1 = scores(ctx, sat, targets, own, legal, pq1, rate, now)
        # O3 注入：逐候选强制重放（选中候选用基线账本自己的 truth_at_target）
        pq3 = {k: dict(v) for k, v in pq0.items()}
        o3_info = {}
        chosen = chosen_by_id.get(str(did))
        for d in legal:
            if d == chosen:
                eg = (e.get("truth_at_target") or {}).get("egress") or {}
                val = sum(x or 0 for x in (eg.get("data_bits"), eg.get("ctrl_bits"), eg.get("in_service_bits"))) if eg else None
                peer = eg.get("peer")
                src = "baseline"
            else:
                raw = a.work / ("o3-%s-%s.json" % (did, d))
                if raw.exists(): raw.unlink()
                p = subprocess.run([sys.executable, "-B", "-m", "CODE.experiment_platform.replay_counterfactual",
                                    "--config", str(a.config), "--decision-id", str(did),
                                    "--forced-action", d, "--root", ".", "--out", str(raw)],
                                   capture_output=True, text=True, cwd=REPO_ROOT)
                if p.returncode or not raw.exists():
                    o3_info[d] = {"status": "replay_failed", "err": (p.stderr or p.stdout or "")[-120:].strip()}
                    continue
                rr = json.loads(raw.read_text())
                cf = rr.get("counterfactual") or {}
                by_dec, _dg = dl.build_ledger(cf.get("decision_rows") or [], cf.get("timeline_rows") or [])
                crow = by_dec.get(str(did)) or by_dec.get(did) or {}
                eg = (crow.get("truth_at_target") or {}).get("egress") or {}
                val = (sum(x or 0 for x in (eg.get("data_bits"), eg.get("ctrl_bits"), eg.get("in_service_bits")))
                       if eg else None)
                peer = eg.get("peer")
                src = "forced_replay"
                o3_info[d] = {"status": "replayed", "source": src, "contended": (crow.get("truth_at_target") or {}).get("contended_direction"),
                              "o3_value": val, "o3_peer": peer,
                              "cost_s": (rr.get("outcome_delta") or {}).get("totals"),
                              "verification": {k: (rr.get("verification") or {}).get(k)
                                               for k in ("branch_states_identical", "action_changed")}}
                if peer is not None and isinstance(val, (int, float)):
                    pq3.setdefault(int(peer), {})[d] = val
                raw.unlink()
        row = {"decision_id": did, "legal": legal, "chosen": chosen,
               "pick_O0": pick(s0, legal), "pick_O1": pick(s1, legal),
               "scores_O0": s0, "scores_O1": s1, "o3": o3_info}
        rows.append(row)
        if len(rows) >= a.snapshots:
            break

    rep = {"schema": "leo-t1-e3-o3-probe/v1", "snapshots": len(rows),
           "pick_O0_eq_O1": sum(1 for r in rows if r["pick_O0"] == r["pick_O1"]),
           "pick_O1_eq_O3": sum(1 for r in rows if r["pick_O1"] == r["pick_O3"]),
           "pick_O0_eq_kernel": sum(1 for r in rows if r["pick_O0"] == r["chosen"]),
           "rows": rows,
           "note": "O3 的注入需要分支自己的 truth_at_target（需在分支内折叠账本）；本轮先验证重放链路与 O0/O1 一致性。"}
    a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"snapshots": rep["snapshots"], "pick_O0_eq_O1": rep["pick_O0_eq_O1"],
                      "samples": [{k: r[k] for k in ("decision_id", "chosen", "pick_O0", "pick_O1")} for r in rows]},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
