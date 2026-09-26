#!/usr/bin/env python3
"""E3 臂比较（离线，基于已自证的评分器）：O0 广告值 vs O1 当前真值。

前置：e3_scorer.py 的 O0 复刻自证已通过（200/200 与内核选择一致）。
本脚本把「首跳 peer 的广告队列」按臂替换后重算评分与选择：
  O0：观察里的 advertised_isl_queue_bits（收到的广告原值）
  O1：该 peer 的**当前真值**（candidate_truth[d].peer_egress_queue_bits），注入到 peer 的
      peer_egress_direction 方向（未记录则用候选方向本身）
统计：选择变化数、inf -> 有限 的可用性变化、以及并列情况（显式回显）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from CODE.leo_sim import model, routing  # noqa: E402

C_KM_S = 299792.458
prop_delay = lambda km: km / C_KM_S


def build_ctx(resolved):
    sc, lk = resolved["scenario"], resolved["links"]
    geom = model.Constellation(sc["num_satellites"], sc["num_planes"], sc["altitude_km"],
                              sc["inclination_deg"], sc["min_elevation_deg"],
                              max_isl_km=lk["max_isl_km"], geometry_epoch_s=lk.get("geometry_epoch_s") if False else sc["geometry_epoch_s"])
    topo = routing.build_topology(geom, sc["num_satellites"], lk["isl_dirs"], t=None)
    rev = routing._reverse_adj(topo)
    return geom, topo, rev, {u: sorted(rev.get(u, ())) for u in rev}


def dir_of(topo, a, b):
    for d, n in (topo.get(a) or {}).items():
        if n == b:
            return d
    return None


def scores(ctx, sat, targets, own, legal, peer_queue, rate, now):
    geom, topo, rev, sorted_rev = ctx

    def edge(a, b):
        c = prop_delay(geom.isl_range_km(a, b, now))
        d = dir_of(topo, a, b)
        if d is None:
            return float("inf")
        if a == sat:
            qb = own.get(d, 0)
        else:
            qb = (peer_queue.get(a) or {}).get(d)
        if qb is None:
            return float("inf")
        return c + qb / max(1.0, rate)

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
    return best, bv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--decisions", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=100000)
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

    n = pick_same = 0
    changed = []
    avail_gain = 0          # O1 让某候选从 inf 变有限
    avail_loss = 0
    ties = 0
    for did, e in sorted(bd.items(), key=lambda kv: int(kv[0])):
        obs = e.get("observation_at_start") or {}
        legal = sorted(obs.get("legal_directions") or [])
        if len(legal) < 2:
            continue
        sat, now = obs.get("sat"), (e.get("t_decision_start") or e.get("t") or 0.0)
        dst = dst_by_id.get(str(did))
        if sat is None or dst is None:
            continue
        neigh = obs.get("neighbours") or {}
        targets, pq0 = [], {}
        for origin, rec in neigh.items():
            cells = (rec or {}).get("advertised_serve_cells") or []
            if dst in cells:
                try: targets.append(int(origin))
                except (TypeError, ValueError): pass
            adv = (rec or {}).get("advertised_isl_queue_bits") or {}
            try: pq0[int(origin)] = {k: v for k, v in adv.items() if isinstance(v, (int, float))}
            except (TypeError, ValueError): pass
        s0 = scores(ctx, sat, targets, obs.get("own_queue_bits") or {}, legal, pq0, rate, now)
        # O1：把首跳 peer 的队列替换为当前真值
        ct = ((e.get("truth_at_commit") or {}).get("candidate_truth") or {})
        pq1 = {k: dict(v) for k, v in pq0.items()}
        for d in legal:
            peer = ((ct.get(d) or {}).get("edge") or [None, None])[1]
            val = (ct.get(d) or {}).get("peer_egress_queue_bits")
            if peer is None or val is None:
                continue
            onward = (ct.get(d) or {}).get("downstream", {}).get("peer_egress_direction") or d
            pq1.setdefault(int(peer), {})[onward] = val
        s1 = scores(ctx, sat, targets, obs.get("own_queue_bits") or {}, legal, pq1, rate, now)
        p0, _ = pick(s0, legal); p1, _ = pick(s1, legal)
        n += 1
        if p0 == p1: pick_same += 1
        else: changed.append({"decision_id": did, "kernel": chosen_by_id.get(str(did)),
                              "O0": p0, "O1": p1, "scores_O0": s0, "scores_O1": s1})
        for d in legal:
            a0 = s0.get(d, float("inf")) != float("inf")
            a1 = s1.get(d, float("inf")) != float("inf")
            if a1 and not a0: avail_gain += 1
            if a0 and not a1: avail_loss += 1
        vals = [s0.get(d) for d in legal]
        if len(set(vals)) < len(vals): ties += 1
        if n >= a.limit: break

    rep = {"schema": "leo-t1-e3-arms-compare/v1", "compared": n,
           "O0_vs_O1_pick_same": pick_same, "pick_changed": len(changed),
           "availability_gain_inf_to_finite": avail_gain,
           "availability_loss_finite_to_inf": avail_loss,
           "snapshots_with_ties_in_O0": ties,
           "changed_samples": changed[:5],
           "note": "O0 评分器已自证（200/200 与内核一致）；O1 注入点为首跳 peer 的广告队列。"}
    a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("compared", "O0_vs_O1_pick_same", "pick_changed",
                                          "availability_gain_inf_to_finite", "availability_loss_finite_to_inf",
                                          "snapshots_with_ties_in_O0")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
