import gzip, json, glob, collections
cell=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
res=json.load(gzip.open(cell+"/arms/stale/result.json.gz"))
fates=res.get("fates") or {}
nr={int(k) for k,v in fates.items() if (v if isinstance(v,str) else v.get("fate"))=="NO_ROUTE"}
dl={int(k) for k,v in fates.items() if (v if isinstance(v,str) else v.get("fate"))=="DELIVERED"}
path=collections.defaultdict(list)
for e in sorted(res.get("packet_events") or [], key=lambda x: x.get("at",0)):
    if e.get("kind")=="propagation_start" and e.get("stage")=="isl":
        lid=e.get("link_id") or ""
        parts=lid.split(":")
        if len(parts)>=3:
            path[e["pid"]].append(int(parts[1]))
for pid in sorted(nr)[:3]:
    seq=path.get(pid,[])
    print(f"NO_ROUTE pid {pid}: {len(seq)} hops, distinct sats {len(set(seq))}")
    print("   first 16:", seq[:16])
    print("   last  16:", seq[-16:])
dpid=sorted(dl)[0]
seq=path.get(dpid,[])
print(f"DELIVERED pid {dpid}: {len(seq)} hops, distinct {len(set(seq))}: {seq[:16]}")
allv=collections.Counter()
for pid in list(nr)[:200]:
    for s in set(path.get(pid,[])): allv[s]+=1
print()
print("sats visited by >=50% of failed packets:", [s for s,c in allv.most_common() if c>=100][:14])
print("total distinct sats touched by failed packets:", len(allv))
