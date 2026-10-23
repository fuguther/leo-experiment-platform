import gzip, json, glob, collections
cell=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
res=json.load(gzip.open(cell+"/arms/stale/result.json.gz"))
fates=res.get("fates") or {}
nr={int(k) for k,v in fates.items() if (v if isinstance(v,str) else v.get("fate"))=="NO_ROUTE"}
empty=collections.Counter(); tot=collections.Counter()
reasons=collections.Counter()
n_dec=0
with gzip.open(cell+"/arms/stale/decisions.jsonl.gz","rt") as fh:
    for line in fh:
        d=json.loads(line); n_dec+=1
        pid=d.get("pid")
        cls = "noroute" if pid in nr else "delivered"
        tot[cls]+=1
        cands=d.get("candidates") or []
        if not cands: empty[cls]+=1
        oa=d.get("observation_at_start") or {}
        for cd in ((oa.get("four_direction_audit") or {}).get("candidate_details") or {}).values():
            for r in cd.get("filter_reasons") or []:
                reasons[r]+=1
print("decisions scanned:", n_dec)
for cls in ("delivered","noroute"):
    print(f"  {cls:10s} decisions={tot[cls]:7d} with EMPTY candidate list={empty[cls]:6d} "
          f"({100*empty[cls]/max(1,tot[cls]):.2f}%)")
print("  top filter reasons:", reasons.most_common(8))
# how many hops did the failed packets route through satellites that were NOT serving NY?
