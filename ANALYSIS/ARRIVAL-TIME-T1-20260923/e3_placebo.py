#!/usr/bin/env python3
"""E3 有效性对照（§7 第 12 条）：乱序注入安慰剂。

问题：O0 与 O1 选择 100% 相同（3528/3528）——这有两种解释：
  (a) 信息确实不影响选择；
  (b) 评分器根本不理会注入值（注入无效）。
本脚本用**乱序注入**区分二者：把各候选的 O1 值互相交换（错配）后再评分。
  若乱序后选择仍完全不变 -> 评分器对注入不敏感 -> (b)，此前的零差异无信息量；
  若乱序后选择发生变化 -> 评分器敏感 -> (a)，零差异是真实结论。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from CODE.leo_sim import model, routing  # noqa: E402

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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--decisions", type=Path, required=True)
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

    n = same01 = same_shuf = shuf_changed = 0
    samples = []
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
            if dst in ((rec or {}).get("advertised_serve_cells") or []):
                try: targets.append(int(origin))
                except (TypeError, ValueError): pass
            adv = (rec or {}).get("advertised_isl_queue_bits") or {}
            try: pq0[int(origin)] = {k: v for k, v in adv.items() if isinstance(v, (int, float))}
            except (TypeError, ValueError): pass
        own = obs.get("own_queue_bits") or {}
        ct = ((e.get("truth_at_commit") or {}).get("candidate_truth") or {})
        # O1 注入（原始）
        inj = {}
        for d in legal:
            peer = ((ct.get(d) or {}).get("edge") or [None, None])[1]
            val = (ct.get(d) or {}).get("peer_egress_queue_bits")
            if peer is not None and isinstance(val, (int, float)):
                onward = ((ct.get(d) or {}).get("downstream") or {}).get("peer_egress_direction") or d
                inj[d] = (int(peer), onward, val)
        # 乱序：把注入值在候选之间轮换（peer 不变的用同 peer，否则保持不变）
        rot = {}
        ds = [d for d in legal if d in inj]
        for i, d in enumerate(ds):
            src = ds[(i + 1) % len(ds)]
            p_src, on_src, v_src = inj[src]
            p_dst, on_dst, _ = inj[d]
            if p_src == p_dst:
                rot[d] = (p_dst, on_dst, v_src)
        def apply(mapping):
            # 注入到该 peer 的**所有方向**：远端边查的是 peer->next 的方向，
            # 而真值是按 peer 聚合的标量，无法逐方向还原（声明近似）。
            pq = {k: dict(v) for k, v in pq0.items()}
            for _d, (peer, _onward, val) in mapping.items():
                slot = pq.setdefault(peer, {})
                for kk in list(slot.keys()) or ['N', 'S', 'E', 'W']:
                    slot[kk] = val
            return pq
        s0 = scores(ctx, sat, targets, own, legal, pq0, rate, now)
        s1 = scores(ctx, sat, targets, own, legal, apply(inj), rate, now)
        srot = scores(ctx, sat, targets, own, legal, apply(rot), rate, now) if rot else s0
        p0, p1, pr = pick(s0, legal), pick(s1, legal), pick(srot, legal)
        n += 1
        if p0 == p1: same01 += 1
        if p0 == pr: same_shuf += 1
        elif len(samples) < 5:
            samples.append({"decision_id": did, "kernel": chosen_by_id.get(str(did)),
                            "pick_O0": p0, "pick_placebo": pr, "injected_dirs": sorted(rot.keys())})
        if p0 != pr: shuf_changed += 1
    rep = {"schema": "leo-t1-e3-placebo/v1", "compared": n,
           "O0_eq_O1": same01, "O0_eq_placebo": same_shuf, "placebo_changed_pick": shuf_changed,
           "placebo_sensitivity": "scorer reacts to injected values" if shuf_changed else "scorer INSENSITIVE to injected values",
           "samples": samples}
    a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("compared", "O0_eq_O1", "O0_eq_placebo", "placebo_changed_pick",
                                          "placebo_sensitivity")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
