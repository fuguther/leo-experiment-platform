import gzip, json, glob, os, statistics, collections, sys
sys.path.insert(0, "/data/论文/leo-t1-wt/releases/e0c59c48ed87785b6db94c11e1bfa70efecb4c59-508bbabb00d6471ef0f938969a6bcb85ae8cdf7df0fba71860f60a941e39ff12/CODE")
from leo_sim import time_alignment as ta

def series_from(path, limit=None):
    series = collections.defaultdict(list)
    n = 0
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                d = json.loads(line); n += 1
                t = d.get("t"); sat = d.get("sat")
                if t is None or sat is None: continue
                cd = ((d.get("observation_at_start") or {}).get("four_direction_audit") or {}).get("candidate_details") or {}
                for direction, rec in cd.items():
                    qb = rec.get("queue_bits_before")
                    if qb is None: continue
                    series[(sat, direction)].append((float(t), float(qb), rec.get("generation")))
                if limit and n >= limit: break
    except Exception:
        pass
    return series

def evaluate(series, methods=("hold_last", "bounded_linear", "zero", "analytic_drain")):
    errs = collections.defaultdict(list)
    horizons = collections.defaultdict(lambda: collections.defaultdict(list))
    for key, pts in series.items():
        pts.sort()
        hist = []
        for i in range(len(pts) - 1):
            t, q, gen = pts[i]
            nt, nq, ngen = pts[i + 1]
            if ngen != gen:            # generation change: not the same resource
                hist = []
                continue
            hist.append(ta.StateSample(ta.ResourceKey(key[0], key[1], "isl"), t, t, q, 1e9))
            if len(hist) > 8: hist = hist[-8:]
            h = nt - t
            for m in methods:
                try:
                    p = ta.predict_resource(list(hist), t, nt, method=m, history_limit=8)
                    v = p.predicted_bits
                except Exception:
                    continue
                if v is None: continue
                e = abs(float(v) - nq)
                errs[m].append((e, nq, h))
                b = ("h<5ms" if h < 0.005 else "5-20ms" if h < 0.02 else ">=20ms")
                horizons[m][b].append(e)
    return errs, horizons

def report(tag, path):
    s = series_from(path)
    if not s: print(tag, "no series"); return
    errs, hor = evaluate(s)
    print(f"--- {tag}: {len(s)} resource series, {len(next(iter(errs.values()),[]))} scored pairs")
    nz = sum(1 for k,v in s.items() for _,q,_ in v if q > 0)
    tot = sum(len(v) for v in s.values())
    print(f"    non-empty samples: {nz}/{tot} = {nz/max(1,tot):.3%}")
    for m in ("zero","hold_last","bounded_linear","analytic_drain"):
        v = errs.get(m) or []
        if not v: continue
        e = [x[0] for x in v]
        busy = [x for x in v if x[1] > 0]
        tail = sorted(v, key=lambda x: -x[1])[:max(1,len(v)//10)]
        print(f"    {m:16s} MAE={statistics.fmean(e):12.1f} bits"
              f"  nonempty MAE={(statistics.fmean([x[0] for x in busy]) if busy else float('nan')):12.1f}"
              f"  busy-tail MAE={statistics.fmean([x[0] for x in tail]):12.1f}")
        hh = hor.get(m) or {}
        parts = " ".join(f"{b}={statistics.fmean(vv):.1f}" for b, vv in sorted(hh.items()) if vv)
        print(f"                     by horizon: {parts}")

p1 = glob.glob("/data/论文/leo-t1-wt/runs/sccor-sc-cor-r050-stale-s111-*/cal/*/arms/stale/decisions.jsonl.gz")
p2 = glob.glob("/data/论文/leo-t1-wt/runs/scp-sc-probe-s1-r050-s111-*/cal/*/arms/now/decisions.jsonl.gz")
if p1: report("corridor r050 stale", p1[0])
if p2: report("trunk S1 mid", p2[0])
