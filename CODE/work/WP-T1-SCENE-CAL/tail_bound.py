import gzip, json, glob, statistics, collections, sys
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
rows=[]
for key,pts in series.items():
    pts.sort(); hist=[]
    for i in range(len(pts)-1):
        t,q,g=pts[i]; nt,nq,ng=pts[i+1]
        if g!=ng: hist=[]; continue
        hist.append(ta.StateSample(ta.ResourceKey(key[0],key[1],"isl"), t, t, q, 1e9))
        if len(hist)>8: hist=hist[-8:]
        pr=ta.predict_resource(list(hist), t, nt, method="hold_last", history_limit=8)
        v=pr.predicted_bits
        if v is None: continue
        rows.append((nq, abs(float(v)-nq)))
rows.sort(key=lambda r:-r[0])
n=len(rows)
gmean=statistics.fmean([q for q,_ in rows])
print("pairs:", n, " global mean of targets: %.1f bits" % gmean)
for frac,label in ((0.10,"top 10% queue"),(0.01,"top 1% queue")):
    head=rows[:max(1,int(frac*n))]
    tq=[q for q,_ in head]
    e_hold=[e for _,e in head]
    e_mean=[abs(gmean-q) for q,_ in head]
    print(f"{label}: n={len(head)} mean target={statistics.fmean(tq):.0f} bits"
          f" | MAE hold_last={statistics.fmean(e_hold):.0f}"
          f" | MAE global_mean={statistics.fmean(e_mean):.0f}"
          f" | hold_last captures {100*(1-statistics.fmean(e_hold)/statistics.fmean(e_mean)):.2f}%")
