import gzip, json, glob, collections, os
c=glob.glob("/data/论文/leo-t1-wt/runs/scds-*/cal/sc-dist-r020-stale-s111")[0]
res=json.load(gzip.open(os.path.join(c,"arms","stale","result.json.gz")))
fc=res.get("fate_counts") or {}
tot=sum(fc.values()) or 1
print("fates:", {k:round(100*v/tot,3) for k,v in fc.items() if v})
fates={int(k):(v if isinstance(v,str) else v.get("fate")) for k,v in (res.get("fates") or {}).items()}
tmap={}
with gzip.open(os.path.join(c,"trace.jsonl.gz"),"rt") as fh:
    for line in fh:
        d=json.loads(line); tmap[int(d["packet_id"])]=(d["src_grid_id"],d["dst_grid_id"])
per=collections.defaultdict(lambda:[0,0])
for p,od in tmap.items():
    per[od][0]+=1
    if fates.get(p)=="NO_ROUTE": per[od][1]+=1
bad=sorted(((k,v) for k,v in per.items() if v[1]>0), key=lambda kv:-kv[1][1])
print("OD pairs:", len(per), " with NO_ROUTE:", len(bad), " total NO_ROUTE:", sum(v[1] for v in per.values()))
for k,v in bad[:5]: print("   ", k, "sent", v[0], "NO_ROUTE", v[1], "%.2f%%"%(100*v[1]/max(1,v[0])))
