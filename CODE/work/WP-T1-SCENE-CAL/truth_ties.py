import gzip, json, glob, collections, os
for tag, glob_pat, arm in (("stale","/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111","stale"),
                           ("truth","/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-truth-s111","now")):
    cell = glob.glob(glob_pat)[0]
    res = json.load(gzip.open(os.path.join(cell,"arms",arm,"result.json.gz")))
    fates = {int(k): (v if isinstance(v,str) else v.get("fate")) for k,v in (res.get("fates") or {}).items()}
    tmap = {}
    with gzip.open(os.path.join(cell,"trace.jsonl.gz"),"rt") as fh:
        for line in fh:
            d=json.loads(line); tmap[int(d["packet_id"])]=(d["src_grid_id"],d["dst_grid_id"])
    TARGET=("G1:129:296","G1:130:106")
    onod={p for p,od in tmap.items() if od==TARGET}
    ties=collections.Counter(); pk=collections.Counter()
    with gzip.open(os.path.join(cell,"arms",arm,"decisions.jsonl.gz"),"rt") as fh:
        for line in fh:
            d=json.loads(line); pid=d.get("pid")
            if pid not in onod: continue
            f=fates.get(pid)
            if f not in ("DELIVERED","NO_ROUTE"): continue
            oa=d.get("observation_at_start") or {}
            cd=(oa.get("four_direction_audit") or {}).get("candidate_details") or {}
            sc=[]
            for k,v in cd.items():
                if not v.get("final_legal"): continue
                s=(v.get("candidate_score") or {}).get("total_s")
                if s is not None: sc.append(float(s))
            if len(sc)<2: continue
            sc.sort()
            if sc[1]-sc[0] <= 1e-12: ties[(f,pid)]+=1
    for p in onod:
        if fates.get(p)=="NO_ROUTE": pk["NO_ROUTE"]+=1
        elif fates.get(p)=="DELIVERED": pk["DELIVERED"]+=1
    nr_ties=[v for (f,p),v in ties.items() if f=="NO_ROUTE"]
    print(f"--- {tag} arm={arm}: OD packets {dict(pk)}")
    print(f"    NO_ROUTE packets with >=1 exact tie: {len(nr_ties)} ; tie counts dist: {collections.Counter(nr_ties)}")
