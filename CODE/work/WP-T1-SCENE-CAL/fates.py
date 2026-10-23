import json, glob, os
rows=[]
for p in sorted(glob.glob("/data/论文/leo-t1-wt/runs/sccor-*/cal/*/summary.json")
                + glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/*/summary.json")
                + glob.glob("/data/论文/leo-t1-wt/runs/scp-*/cal/*/summary.json")
                + glob.glob("/data/论文/leo-t1-wt/runs/scw1b-*/cal/*/summary.json")
                + glob.glob("/data/论文/leo-t1-wt/runs/scw1-*/cal/*/summary.json")):
    d=json.load(open(p))
    if d.get("status")!="DIAGNOSTIC_COMPLETE": continue
    for a in d.get("arms") or []:
        d4=a.get("d4") or {}
        rows.append((d["cell"], a.get("arm"), d4.get("offered_packets"),
                     d4.get("deadline_not_delivered_count"),
                     round((d4.get("deadline_not_delivered_proportion") or 0)*100,4),
                     round(d4.get("capped_loss") or 0,6)))
seen=set(); out=[]
for r in rows:
    if (r[0],r[1]) in seen: continue
    seen.add((r[0],r[1])); out.append(r)
print(f"{'cell':30s} {'arm':10s} {'pkts':>7s} {'missed':>7s} {'miss%':>8s} {'capped':>9s}")
for r in out:
    print(f"{r[0]:30s} {str(r[1]):10s} {str(r[2]):>7s} {str(r[3]):>7s} {str(r[4]):>8s} {str(r[5]):>9s}")
print("rows", len(out))
