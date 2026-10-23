import gzip, json, glob, os, csv
pats = ["/data/论文/leo-t1-wt/runs/scw1b-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/scw1-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/scp-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/scc-*/cal/*/arms/*/result.json.gz",
        "/data/论文/leo-t1-wt/runs/sccor-*/cal/*/arms/*/result.json.gz"]
rows=[]; seen=set()
for pat in pats:
    for p in sorted(glob.glob(pat)):
        if p in seen: continue
        seen.add(p)
        parts=p.split("/")
        arm=parts[-2]; cell=parts[-4]
        try: res=json.load(gzip.open(p))
        except Exception: continue
        fc=res.get("fate_counts") or {}
        if not fc: continue
        tot=sum(fc.values())
        rows.append(dict(cell=cell, arm=arm, total=tot,
                         delivered=fc.get("DELIVERED",0),
                         no_route=fc.get("NO_ROUTE",0),
                         expired=sum(v for k,v in fc.items() if "DEADLINE" in k or "EXPIRED" in k),
                         overflow=sum(v for k,v in fc.items() if "OVERFLOW" in k),
                         in_system=fc.get("IN_SYSTEM_AT_STOP",0)))
out="/tmp/scal/fig2/fates.csv"
os.makedirs("/tmp/scal/fig2", exist_ok=True)
with open(out,"w",newline="") as fh:
    w=csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
print(f"{'cell':34s} {'arm':10s} {'total':>7s} {'deliv':>7s} {'noroute':>8s} {'exp':>4s} {'ovf':>4s}")
for r in rows:
    print(f"{r['cell'][:34]:34s} {r['arm']:10s} {r['total']:7d} {r['delivered']:7d} {r['no_route']:8d} {r['expired']:4d} {r['overflow']:4d}")
print("rows", len(rows), "total expired", sum(r["expired"] for r in rows), "total overflow", sum(r["overflow"] for r in rows))
