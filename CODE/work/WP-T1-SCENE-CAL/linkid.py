import gzip, json, glob, collections
p=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111/arms/candidate/result.json.gz")[0]
res=json.load(gzip.open(p))
ev=res.get("packet_events") or []
qe=[e for e in ev if e.get("kind")=="queue_enter" and e.get("queue")=="isl"][:5]
ss=[e for e in ev if e.get("kind")=="service_start" and e.get("stage")=="isl"][:5]
print("isl queue_enter link_id samples:", [e.get("link_id") for e in qe])
print("isl queue_enter keys:", sorted(qe[0].keys()) if qe else None)
print("isl service_start link_id samples:", [e.get("link_id") for e in ss])
print("isl service_start keys:", sorted(ss[0].keys()) if ss else None)
print()
w=[x for x in (res.get("link_service_windows") or []) if x.get("stage")=="isl"][:3]
print("window link_id samples:", [x.get("link_id") for x in w])
