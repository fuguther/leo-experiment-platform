#!/usr/bin/env python3
"""E3 全缓存注入对照（需先应用 recording-extension.patch）。

与 e3_arms_compare.py 的区别：注入覆盖**全部缓存起源**（而非仅首跳 peer），
数据来自补丁新增的 info_audit.cache_entries[*].payload_values（O0）与 truth_queue_bits（O1）。

用法：
  python3 e3_full_cache_compare.py --ledger <折叠账本> --run-dir <run> --decisions <decisions.jsonl> \
      --out <报告>
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
INF = float("inf")


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
    geom, topo, rev, srev = ctx

    def edge(a, b):
        c = prop_delay(geom.isl_range_km(a, b, now))
        d = dir_of(topo, a, b)
        if d is None:
            return INF
        qb = own.get(d, 0) if a == sat else (pq.get(a) or {}).get(d)
        return INF if qb is None else c + qb / max(1.0, rate)

    if not targets:
        return {d: INF for d in legal}
    dist = routing._multi_source_dist(rev, targets, lambda u, v: edge(v, u), sorted_adj=srev)
    out = {}
    for d in legal:
        n = (topo.get(sat) or {}).get(d)
        if n is None:
            out[d] = INF
            continue
        w = edge(sat, n)
        out[d] = INF if w == INF else w + dist.get(n, INF)
    return out


def pick(sc, legal):
    best, bv = None, None
    for d in sorted(legal):
        v = sc.get(d, INF)
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
    dst_by = {}
    for line in a.decisions.open(encoding="utf-8"):
        r = json.loads(line)
        dst_by[str(r.get("decision_id"))] = r.get("dst")
    bd = json.loads(a.ledger.read_text())["by_decision"]
    # 决策流里带上补丁新增字段的行
    rows = {}
    for line in a.decisions.open(encoding="utf-8"):
        r = json.loads(line)
        rows[str(r.get("decision_id"))] = r

    n = same = flipped = 0
    o0_vs_truth_diff = 0
    samples = []
    for did, e in sorted(bd.items(), key=lambda kv: int(kv[0])):
        obs = e.get("observation_at_start") or {}
        legal = sorted(obs.get("legal_directions") or [])
        if len(legal) < 2:
            continue
        sat, now = obs.get("sat"), (e.get("t_decision_start") or e.get("t") or 0.0)
        dst = dst_by.get(str(did))
        row = rows.get(str(did)) or {}
        ce = (row.get("info_audit") or {}).get("cache_entries") or {}
        if sat is None or dst is None or not ce:
            continue
        targets = []
        for origin, rec in (obs.get("neighbours") or {}).items():
            if dst in ((rec or {}).get("advertised_serve_cells") or []):
                try: targets.append(int(origin))
                except (TypeError, ValueError): pass
        pq0, pq1 = {}, {}
        for origin, ent in ce.items():
            try: o = int(origin)
            except (TypeError, ValueError): continue
            pv = ((ent.get("payload_values") or {}).get("isl_queue_bits") or {})
            pq0[o] = {d: (rec or {}).get("value") for d, rec in pv.items()
                      if isinstance(rec, dict) and isinstance(rec.get("value"), (int, float))}
            tq = ent.get("truth_queue_bits")
            if isinstance(tq, dict):
                pq1[o] = {d: v for d, v in tq.items() if isinstance(v, (int, float))}
        if not pq0:
            continue
        own = obs.get("own_queue_bits") or {}
        s0 = scores(ctx, sat, targets, own, legal, pq0, rate, now)
        s1 = scores(ctx, sat, targets, own, legal, pq1 or pq0, rate, now)
        p0, p1 = pick(s0, legal), pick(s1, legal)
        n += 1
        if p0 == p1: same += 1
        else:
            flipped += 1
            if len(samples) < 5:
                samples.append({"decision_id": did, "chosen": e.get("chosen"), "O0": p0, "O1": p1,
                                "scores_O0": s0, "scores_O1": s1})
    rep = {"schema": "leo-t1-e3-full-cache/v1", "compared": n,
           "O0_eq_O1_pick": same, "pick_flipped": flipped, "samples": samples,
           "note": "全缓存注入（覆盖所有 origin）；需先应用 recording-extension.patch"}
    a.out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("compared", "O0_eq_O1_pick", "pick_flipped")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
