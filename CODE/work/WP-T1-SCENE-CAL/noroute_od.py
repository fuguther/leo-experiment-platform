import gzip, json, glob, collections, os
runs=[("cond-arm4 stale","/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111","stale"),
      ("cond-truth","/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-truth-s111","now")]
for tag, cellglob, arm in runs:
    c=glob.glob(cellglob)
    if not c: print(tag,"missing"); continue
    cell=c[0]
    tracemap={}
    tf=os.path.join(cell,"trace.jsonl.gz")
    try:
        with gzip.open(tf,"rt") as fh:
            for line in fh:
                d=json.loads(line)
                tracemap[int(d["packet_id"])]=(d["src_grid_id"], d["dst_grid_id"])
    except Exception as e:
        print(tag,"trace unreadable",type(e).__name__); continue
    res=json.load(gzip.open(os.path.join(cell,"arms",arm,"result.json.gz")))
    fates=res.get("fates") or {}
    per_od=collections.defaultdict(lambda:[0,0])
    for pid_raw, rec in fates.items():
        pid=int(pid_raw)
        od=tracemap.get(pid)
        if od is None: continue
        st = rec if isinstance(rec,str) else (rec.get("fate") if isinstance(rec,dict) else None)
        per_od[od][0]+=1
        if st=="NO_ROUTE": per_od[od][1]+=1
    tot_nr=sum(v[1] for v in per_od.values())
    print(f"--- {tag}: total NO_ROUTE {tot_nr}, OD pairs {len(per_od)}")
    bad=[(k,v) for k,v in per_od.items() if v[1]>0]
    bad.sort(key=lambda kv:-kv[1][1])
    print(f"    OD pairs with at least one NO_ROUTE: {len(bad)}/{len(per_od)}")
    for k,v in bad[:6]:
        print(f"      {k} sent={v[0]} NO_ROUTE={v[1]} ({100*v[1]/max(1,v[0]):.1f}%)")
