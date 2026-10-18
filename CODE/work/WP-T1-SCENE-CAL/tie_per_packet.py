import gzip, json, glob, collections, statistics
cell=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
res=json.load(gzip.open(cell+"/arms/stale/result.json.gz"))
fates=res.get("fates") or {}
fate={int(k):(v if isinstance(v,str) else v.get("fate")) for k,v in fates.items()}
# OD of interest
tmap={}
with gzip.open(cell+"/trace.jsonl.gz","rt") as fh:
    for line in fh:
        d=json.loads(line); tmap[int(d["packet_id"])]=(d["src_grid_id"],d["dst_grid_id"])
TARGET=("G1:129:296","G1:130:106")
onod={p for p,od in tmap.items() if od==TARGET}
per=collections.defaultdict(lambda: {"dec":0,"tie":0,"near":0})
with gzip.open(cell+"/arms/stale/decisions.jsonl.gz","rt") as fh:
    for line in fh:
        d=json.loads(line); pid=d.get("pid")
        if pid not in onod: continue
        f=fate.get(pid)
        if f not in ("DELIVERED","NO_ROUTE"): continue
        oa=d.get("observation_at_start") or {}
        cd=(oa.get("four_direction_audit") or {}).get("candidate_details") or {}
        sc=[]
        for k,v in cd.items():
            if not v.get("final_legal"): continue
            s=(v.get("candidate_score") or {}).get("total_s")
            if s is not None: sc.append(float(s))
        if len(sc)<2: continue
        sc.sort()
        gap=sc[1]-sc[0]
        per[f]["dec"]+=1
        if gap<=1e-12: per[f]["tie"]+=1
        if gap<1e-5: per[f]["near"]+=1
print(f"OD {TARGET}: packets on this OD = {len(onod)}")
for f in ("DELIVERED","NO_ROUTE"):
    d=per[f]
    n_pk=sum(1 for p in onod if fate.get(p)==f)
    print(f"  {f:10s} packets={n_pk:5d} decisions={d['dec']:6d} "
          f"exact ties={d['tie']:5d} ({100*d['tie']/max(1,d['dec']):.2f}% of decisions) "
          f"near(<1e-5)={d['near']:5d} ({100*d['near']/max(1,d['dec']):.2f}%) "
          f"ties/packet={d['tie']/max(1,n_pk):.2f}")
