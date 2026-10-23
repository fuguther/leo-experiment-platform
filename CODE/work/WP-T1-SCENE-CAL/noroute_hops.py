import gzip, json, glob, collections, statistics
cell=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
res=json.load(gzip.open(cell+"/arms/stale/result.json.gz"))
fates=res.get("fates") or {}
hops=collections.Counter()
for e in res.get("packet_events") or []:
    if e.get("kind")=="propagation_start" and e.get("stage")=="isl":
        hops[e["pid"]]+=1
by=collections.defaultdict(list)
for pid_raw, rec in fates.items():
    st = rec if isinstance(rec,str) else (rec.get("fate") if isinstance(rec,dict) else None)
    by[st].append(hops.get(int(pid_raw),0))
for st, hs in sorted(by.items(), key=lambda kv:-len(kv[1])):
    if not hs: continue
    hs_s=sorted(hs)
    print(f"{st:16s} n={len(hs):6d} hops mean={statistics.fmean(hs):6.2f} "
          f"p50={hs_s[len(hs_s)//2]:3d} p95={hs_s[int(.95*len(hs_s))]:3d} max={hs_s[-1]:3d} "
          f"at_limit(>=63)={sum(1 for h in hs if h>=63)}")
# holding wait for NO_ROUTE packets
hold={}
for e in res.get("packet_events") or []:
    pid=e.get("pid")
    if e.get("kind")=="queue_enter" and e.get("queue")=="holding":
        hold.setdefault(pid, []).append(e.get("at"))
nr=[int(k) for k,v in fates.items() if (v if isinstance(v,str) else v.get("fate"))=="NO_ROUTE"]
print()
print("NO_ROUTE packets:", len(nr), "with holding entries:", sum(1 for p in nr if p in hold))
if nr:
    print("  hops of NO_ROUTE:", sorted(set(hops.get(p,0) for p in nr))[:12])
