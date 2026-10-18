import gzip, json, glob, os
pats = ["/data/论文/leo-t1-wt/runs/scw1b-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/scw1-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/scp-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/scc-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/sccor-*/cal/*/arms/*/result.json.gz"]
seen=set(); rows=[]
for pat in pats:
    for p in sorted(glob.glob(pat)):
        key=os.path.dirname(p)
        if key in seen: continue
        seen.add(key)
        try: res=json.load(gzip.open(p))
        except Exception: continue
        fc=res.get("fate_counts") or {}
        if not fc: continue
        tot=sum(fc.values())
        expired=sum(v for k,v in fc.items() if "DEADLINE" in k or "EXPIRED" in k)
        overflow=sum(v for k,v in fc.items() if "OVERFLOW" in k)
        noroute=fc.get("NO_ROUTE",0)
        delivered=fc.get("DELIVERED",0)
        insys=fc.get("IN_SYSTEM_AT_STOP",0)
        cell=os.path.basename(key).replace("sc-cond-r050-arm4-s111","cond-arm4")
        arm=os.path.basename(os.path.dirname(p))
        rows.append((cell, arm, tot, delivered, noroute, expired, overflow, insys))
print(f"{'cell':22s} {'arm':9s} {'total':>7s} {'deliv':>7s} {'NO_ROUTE':>8s} {'expired':>7s} {'ovf':>4s} {'insys':>5s}")
for r in rows:
    print(f"{r[0][:22]:22s} {r[1]:9s} {r[2]:7d} {r[3]:7d} {r[4]:8d} {r[5]:7d} {r[6]:4d} {r[7]:5d}")
nz=[r for r in rows if r[5]>0]
print()
print("cells with ANY deadline expiry:", len(nz), "of", len(rows))
print("cells with NO_ROUTE>0:", sum(1 for r in rows if r[4]>0))
print("total expired across all:", sum(r[5] for r in rows), " total NO_ROUTE:", sum(r[4] for r in rows))
