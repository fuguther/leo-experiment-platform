import gzip, json, glob, statistics, collections, sys, os
sys.path.insert(0, "/data/论文/leo-t1-wt/releases/e0c59c48ed87785b6db94c11e1bfa70efecb4c59-508bbabb00d6471ef0f938969a6bcb85ae8cdf7df0fba71860f60a941e39ff12/CODE")
from leo_sim import time_alignment as ta
p=glob.glob("/data/论文/leo-t1-wt/runs/sccor-sc-cor-r050-stale-s111-*/cal/*/arms/stale/decisions.jsonl.gz")[0]
series=collections.defaultdict(list)
with gzip.open(p,"rt") as fh:
    for line in fh:
        d=json.loads(line); t=d.get("t"); sat=d.get("sat")
        if t is None or sat is None: continue
        cd=((d.get("observation_at_start") or {}).get("four_direction_audit") or {}).get("candidate_details") or {}
        for dr,rec in cd.items():
            qb=rec.get("queue_bits_before")
            if qb is None: continue
            series[(sat,dr)].append((float(t), float(qb), rec.get("generation")))
# global mean of the target values = "no information about this resource"
allv=[q for v in series.values() for _,q,_ in v]
gmean=statistics.fmean(allv)
errs=collections.defaultdict(list)
for key,pts in series.items():
    pts.sort(); hist=[]
    for i in range(len(pts)-1):
        t,q,g=pts[i]; nt,nq,ng=pts[i+1]
        if g!=ng: hist=[]; continue
        hist.append(ta.StateSample(ta.ResourceKey(key[0],key[1],"isl"), t, t, q, 1e9))
        if len(hist)>8: hist=hist[-8:]
        for m in ("hold_last","bounded_linear","analytic_drain"):
            try:
                pr=ta.predict_resource(list(hist), t, nt, method=m, history_limit=8)
                v=pr.predicted_bits
            except Exception: continue
            if v is None: continue
            errs[m].append(abs(float(v)-nq))
        errs["global_mean"].append(abs(gmean-nq))
        errs["oracle_next"].append(0.0)
print("series:", len(series), " target values:", len(allv), " global mean = %.1f bits" % gmean)
for m in ("global_mean","hold_last","bounded_linear","analytic_drain","oracle_next"):
    v=errs.get(m) or []
    if v: print("  %-16s MAE = %12.1f" % (m, statistics.fmean(v)))
print()
print("instrument check: MAE(global_mean) >> MAE(hold_last) > MAE(oracle)=0 means the harness DISCRIMINATES.")
