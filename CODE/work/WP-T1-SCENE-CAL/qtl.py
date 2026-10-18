import gzip, json, glob, os, csv, statistics
OUT="/tmp/scal/fig2"; os.makedirs(OUT, exist_ok=True)
rows=[]
for name,pat in (("COR-050","/data/论文/leo-t1-wt/runs/sccor-sc-cor-r050-stale-s111-*/cal/*/arms/stale/result.json.gz"),
                 ("COR-095","/data/论文/leo-t1-wt/runs/sccor-sc-cor-r095-stale-s111-*/cal/*/arms/stale/result.json.gz"),
                 ("S1-mid","/data/论文/leo-t1-wt/runs/scp-sc-probe-s1-r050-s111-*/cal/*/arms/now/result.json.gz")):
    p=glob.glob(pat)
    if not p: continue
    res=json.load(gzip.open(p[0]))
    pk=(res.get("congestion_metrics") or {}).get("packets") or {}
    iq={}; oq={}
    for e in res.get("packet_events") or []:
        pid=e.get("pid"); k=e.get("kind")
        if k=="queue_enter" and e.get("queue")=="isl":
            oq.setdefault((pid,e.get("link_id")),[]).append(e.get("at"))
        elif k=="service_start" and e.get("stage")=="isl":
            st=oq.get((pid,e.get("link_id")))
            if st: iq[pid]=iq.get(pid,0.0)+(e.get("at",0)-st.pop(0))
    bins={}
    for k,v in pk.items():
        at=v.get("emitted_at")
        if at is None: continue
        b=round(at*100,1)          # 10 ms bins
        bins.setdefault(b,[]).append(iq.get(int(k),0.0))
    for b in sorted(bins):
        rows.append((name,b,len(bins[b]),statistics.fmean(bins[b]),
                     sorted(bins[b])[int(0.95*(len(bins[b])-1))]))
    print(name,"bins",len(bins),"max mean islq ms",
          round(max(statistics.fmean(v) for v in bins.values())*1000,2))
with open(os.path.join(OUT,"queue_timeline.csv"),"w",newline="") as fh:
    w=csv.writer(fh); w.writerow(["cell","emit_t","n","isl_q_mean","isl_q_p95"]); w.writerows(rows)
print("rows",len(rows))
