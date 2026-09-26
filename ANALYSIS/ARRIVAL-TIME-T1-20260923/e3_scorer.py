#!/usr/bin/env python3
"""E3 离线评分探针：重建拓扑 + 反向 BFS，比较各臂评分与「可用性通道」。

用途（两步）：
  1) 自证：用 O0（观察到的广告值）复刻 capacity 的选路，与内核实际 chosen 比对——复现率高才说明重建可信；
  2) 比较：把首跳 peer 的队列值按臂注入（O0 广告 / O1 当前真值 / O3 到达真值），看评分与可用性（inf -> 有限）如何变化。

已声明的近似（必须随结论一起引用）：
  * 远端边的传播项用几何真值代替 cache 的 isl_propagation_s（账本未记录后者）；
  * 仅观察里出现的 origin 星有广告队列可用；其余按内核语义 fail-closed（inf）；
  * 注入只作用于「首跳 peer 的广告队列」——这是 capacity 里邻居状态的唯一入口路径。
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


def prop_delay(km: float) -> float:
    return km / C_KM_S


def build_ctx(resolved):
    sc, lk = resolved["scenario"], resolved["links"]
    geom = model.Constellation(
        sc["num_satellites"], sc["num_planes"], sc["altitude_km"],
        sc["inclination_deg"], sc["min_elevation_deg"],
        max_isl_km=lk["max_isl_km"], geometry_epoch_s=sc["geometry_epoch_s"])
    dirs = lk["isl_dirs"]
    topo = routing.build_topology(geom, sc["num_satellites"], dirs, t=None)
    rev = routing._reverse_adj(topo)
    sorted_rev = {u: sorted(rev.get(u, ())) for u in rev}
    return geom, topo, rev, sorted_rev


def dir_of(topo, a, b):
    for d, n in (topo.get(a) or {}).items():
        if n == b:
            return d
    return None


def score_directions(ctx, sat, targets, own_queue_bits, legal, peer_queue, isl_rate_bps, now):
    """返回 {direction: total_cost}，cost 为 inf 表示不可用（fail-closed）。"""
    geom, topo, rev, sorted_rev = ctx

    def edge_cost(a, b):
        # 传播
        if a == sat:
            c = prop_delay(geom.isl_range_km(a, b, now))
        else:
            c = prop_delay(geom.isl_range_km(a, b, now))  # 已声明：几何真值代理
        # 队列
        if a == sat:
            d = dir_of(topo, a, b)
            qb = own_queue_bits.get(d, 0) if d else None
        else:
            d = dir_of(topo, a, b)
            qb = (peer_queue.get(a) or {}).get(d) if d else None
        if c is None or qb is None:
            return float("inf")
        return c + qb / max(1.0, isl_rate_bps)

    if not targets:
        return {d: float("inf") for d in legal}
    dist = routing._multi_source_dist(rev, targets,
                                     lambda u, v: edge_cost(v, u), sorted_adj=sorted_rev)
    out = {}
    for d in legal:
        n = (topo.get(sat) or {}).get(d)
        if n is None:
            out[d] = float("inf")
            continue
        w = edge_cost(sat, n)
        out[d] = float("inf") if w == float("inf") else w + dist.get(n, float("inf"))
    return out


def pick(scores, legal):
    best, bv = None, None
    for d in sorted(legal):          # 与内核一致：sort key = (cost, direction)
        v = scores.get(d, float("inf"))
        if bv is None or v < bv:
            best, bv = d, v
    return best, bv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--decisions", type=Path, required=True,
                    help="decisions.jsonl：折叠账本不保留 dst，必须从决策流取")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    resolved = json.loads((args.run_dir / "resolved_config.json").read_text())["config"]
    ctx = build_ctx(resolved)
    isl_rate_bps = float(resolved["links"]["isl_rate_mbps"]) * 1_000_000.0
    # dst 只在决策流里；折叠账本不含它
    dst_by_id = {}
    for line in args.decisions.open(encoding="utf-8"):
        r = json.loads(line)
        dst_by_id[str(r.get("decision_id"))] = r.get("dst")
    ledger = json.loads(args.ledger.read_text())
    bd = ledger["by_decision"]

    agree = disagree = skipped = 0
    samples = []
    for did, e in sorted(bd.items(), key=lambda kv: int(kv[0])):
        legal = sorted((e.get("observation_at_start") or {}).get("legal_directions") or [])
        if len(legal) < 2:
            continue
        obs = e.get("observation_at_start") or {}
        sat = obs.get("sat")
        now = e.get("t_decision_start") or e.get("t") or 0.0
        dst = dst_by_id.get(str(did))
        own = obs.get("own_queue_bits") or {}
        neigh = obs.get("neighbours") or {}
        if sat is None or dst is None:
            skipped += 1
            continue
        # 目标集合：广告里服务 dst 的星
        targets = []
        for origin, rec in neigh.items():
            cells = (rec or {}).get("advertised_serve_cells") or []
            if dst in cells:
                try:
                    targets.append(int(origin))
                except (TypeError, ValueError):
                    pass
        peer_queue = {}
        for origin, rec in neigh.items():
            adv = (rec or {}).get("advertised_isl_queue_bits") or {}
            try:
                peer_queue[int(origin)] = {k: v for k, v in adv.items() if isinstance(v, (int, float))}
            except (TypeError, ValueError):
                pass
        scores = score_directions(ctx, sat, targets, own, legal, peer_queue, isl_rate_bps, now)
        my_pick, my_val = pick(scores, legal)
        if e.get("chosen") == my_pick:
            agree += 1
        else:
            disagree += 1
            if len(samples) < 5:
                samples.append({"decision_id": did, "kernel": e.get("chosen"), "replica": my_pick,
                                "scores": scores, "targets": targets})
        if agree + disagree >= args.limit:
            break

    report = {
        "schema": "leo-t1-e3-scorer-probe/v1",
        "note": "O0 复刻自证：复现率低说明重建不忠实，其后的臂比较不可用。",
        "compared": agree + disagree, "agree": agree, "disagree": disagree, "skipped": skipped,
        "agreement_rate": (agree / (agree + disagree)) if (agree + disagree) else None,
        "mismatch_samples": samples,
        "declared_approximations": [
            "远端边传播项用几何真值代理（账本未记录 cache 的 isl_propagation_s）",
            "仅观察里出现的 origin 有广告队列；其余 fail-closed 为 inf",
        ],
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("compared", "agree", "disagree", "agreement_rate")},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
