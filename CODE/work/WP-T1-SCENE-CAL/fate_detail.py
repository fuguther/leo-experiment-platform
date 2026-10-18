import gzip, json, glob, os
for tag, pat in (("cond-arm4 stale","/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111/arms/stale/result.json.gz"),
                 ("cond-truth now","/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-truth-s111/arms/now/result.json.gz"),
                 ("corridor r050","/data/论文/leo-t1-wt/runs/sccor-*/cal/sc-cor-r050-stale-s111/arms/stale/result.json.gz"),
                 ("S1 mid probe","/data/论文/leo-t1-wt/runs/scp-*/cal/sc-probe-s1-r050-s111/arms/now/result.json.gz")):
    p = glob.glob(pat)
    if not p: print(tag, "(missing)"); continue
    try:
        res = json.load(gzip.open(p[0]))
    except Exception as e:
        print(tag, "unreadable", type(e).__name__); continue
    fc = res.get("fate_counts") or {}
    tot = sum(fc.values()) or 1
    print(tag, "total", sum(fc.values()))
    for k in sorted(fc, key=lambda x: -fc[x]):
        if fc[k]: print(f"    {k:26s} {fc[k]:6d}  {100*fc[k]/tot:6.2f}%")
