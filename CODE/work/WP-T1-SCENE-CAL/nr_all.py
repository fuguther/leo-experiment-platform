import gzip, json, glob, collections, os
cells = []
for pat in ("/data/论文/leo-t1-wt/runs/scw1-*/cal/sc-s1-r020-s111",
            "/data/论文/leo-t1-wt/runs/scw1b-*/cal/sc-s2-r020-s111",
            "/data/论文/leo-t1-wt/runs/scw1b-*/cal/sc-s3-r020-s111",
            "/data/论文/leo-t1-wt/runs/scp-*/cal/sc-probe-s1-r050-s111",
            "/data/论文/leo-t1-wt/runs/scw1b-*/cal/sc-s2-r050-s111",
            "/data/论文/leo-t1-wt/runs/scw1b-*/cal/sc-s3-r050-s111",
            "/data/论文/leo-t1-wt/runs/scw1b-*/cal/sc-s1-r050-w035-s111"):
    g = glob.glob(pat)
    if g: cells.append(g[0])
print(f"{'cell':26s} {'NO_ROUTE':>8s} {'ODs w/ loss':>11s}  worst OD")
for c in cells:
    arm = "now" if "probe" in c else "stale"
    rp = os.path.join(c, "arms", arm, "result.json.gz")
    tp = os.path.join(c, "trace.jsonl.gz")
    if not (os.path.exists(rp) and os.path.exists(tp)): 
        print(f"{os.path.basename(c)[:26]:26s} (missing)"); continue
    tracemap = {}
    try:
        with gzip.open(tp, "rt") as fh:
            for line in fh:
                d = json.loads(line)
                tracemap[int(d["packet_id"])] = (d["src_grid_id"], d["dst_grid_id"])
    except Exception:
        pass
    res = json.load(gzip.open(rp))
    fates = res.get("fates") or {}
    per = collections.defaultdict(lambda: [0,0])
    for pid_raw, rec in fates.items():
        od = tracemap.get(int(pid_raw))
        if od is None: continue
        st = rec if isinstance(rec,str) else rec.get("fate")
        per[od][0] += 1
        if st == "NO_ROUTE": per[od][1] += 1
    tot_nr = sum(v[1] for v in per.values())
    bad = sorted(((k,v) for k,v in per.items() if v[1] > 0), key=lambda kv: -kv[1][1])
    worst = f"{bad[0][0][0]}->{bad[0][0][1]} {100*bad[0][1][1]/max(1,bad[0][1][0]):.1f}%" if bad else "-"
    print(f"{os.path.basename(c)[:26]:26s} {tot_nr:8d} {len(bad):11d}/{len(per):<3d} {worst}")
