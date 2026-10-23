import gzip, json, glob, statistics, collections, sys
sys.path.insert(0, "/data/论文/leo-t1-wt/releases/e0c59c48ed87785b6db94c11e1bfa70efecb4c59-508bbabb00d6471ef0f938969a6bcb85ae8cdf7df0fba71860f60a941e39ff12/CODE")
from leo_sim import time_alignment as ta
def series_from(path):
    s = collections.defaultdict(list)
    try:
        with gzip.open(path, "rt") as fh:
            for line in fh:
                d = json.loads(line)
                t = d.get("t"); sat = d.get("sat")
                if t is None or sat is None: continue
                cd = ((d.get("observation_at_start") or {}).get("four_direction_audit") or {}).get("candidate_details") or {}
                for dr, rec in cd.items():
                    qb = rec.get("queue_bits_before")
                    if qb is None: continue
                    s[(sat, dr)].append((float(t), float(qb), rec.get("generation")))
    except Exception: pass
    return s
def evaluate(s, methods=("hold_last","bounded_linear","zero","analytic_drain")):
    errs = collections.defaultdict(list)
    for key, pts in s.items():
        pts.sort(); hist=[]; gen0=None
        for i in range(len(pts)-1):
            t,q,g = pts[i]; nt,nq,ng = pts[i+1]
            if g != ng: hist=[]; continue
            hist.append(ta.StateSample(ta.ResourceKey(key[0],key[1],"isl"), t, t, q, 1e9))
            if len(hist)>8: hist=hist[-8:]
            for m in methods:
                try:
                    p=ta.predict_resource(list(hist), t, nt, method=m, history_limit=8)
                    v=p.predicted_bits
                except Exception: continue
                if v is None: continue
                errs[m].append((abs(float(v)-nq), nq))
    return errs
def run(tag, pat):
    p=glob.glob(pat)
    if not p: print(tag,"(missing)"); return
    s=series_from(p[0])
    if not s: print(tag,"(no series)"); return
    e=evaluate(s)
    n=len(next(iter(e.values()))) if e else 0
    nz=sum(1 for v in s.values() for _,q,_ in v if q>0); tot=sum(len(v) for v in s.values())
    print(f"{tag:22s} series={len(s):4d} pairs={n:8d} nonempty={nz/max(1,tot):.3%}")
    base=None
    row={}
    for m in ("zero","hold_last","bounded_linear","analytic_drain"):
        v=e.get(m) or []
        if not v: continue
        row[m]=statistics.fmean([x[0] for x in v])
    if "hold_last" in row:
        for m in row:
            rel = row[m]/row["hold_last"]
            print(f"     {m:16s} MAE={row[m]:10.1f}  x{rel:5.2f} vs hold_last")
for tag,pat in (
  ("S1 mid (stationary)","/data/论文/leo-t1-wt/runs/scp-sc-probe-s1-r050-s111-*/cal/*/arms/now/decisions.jsonl.gz"),
  ("S2 mid (OD-indep burst)","/data/论文/leo-t1-wt/runs/scw1b-sc-s2-r050-s111-*/cal/*/arms/stale/decisions.jsonl.gz"),
  ("S3 mid (correlated)","/data/论文/leo-t1-wt/runs/scw1b-sc-s3-r050-s111-*/cal/*/arms/stale/decisions.jsonl.gz"),
  ("CORRIDOR r050","/data/论文/leo-t1-wt/runs/sccor-sc-cor-r050-stale-s111-*/cal/*/arms/stale/decisions.jsonl.gz")):
    run(tag,pat)
