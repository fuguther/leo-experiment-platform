import gzip, json, glob, collections, os
TARGET=("G1:129:296","G1:130:106")
def analyse(tag, cell_glob, arm="stale"):
    c=glob.glob(cell_glob)
    if not c: print(tag,"missing"); return
    c=c[0]
    res=json.load(gzip.open(os.path.join(c,"arms",arm,"result.json.gz")))
    fates={int(k):(v if isinstance(v,str) else v.get("fate")) for k,v in (res.get("fates") or {}).items()}
    tmap={}
    with gzip.open(os.path.join(c,"trace.jsonl.gz"),"rt") as fh:
        for line in fh:
            d=json.loads(line); tmap[int(d["packet_id"])]=(d["src_grid_id"],d["dst_grid_id"])
    sent=sum(1 for p,od in tmap.items() if od==TARGET)
    nr=sum(1 for p,od in tmap.items() if od==TARGET and fates.get(p)=="NO_ROUTE")
    allnr=sum(1 for p in tmap if fates.get(p)=="NO_ROUTE")
    fc=res.get("fate_counts") or {}
    exp=sum(v for k,v in fc.items() if "DEADLINE" in k or "EXPIRED" in k)
    print(f"{tag:32s} OD sent={sent:5d} NO_ROUTE={nr:4d} ({100*nr/max(1,sent):.2f}%) | all NO_ROUTE={allnr:4d} | expired={exp}")
analyse("BASELINE sc-s1-r050-w035","/data/论文/leo-t1-wt/runs/scw1b-*/cal/sc-s1-r050-w035-s111")
analyse("INTERVENTION tieT (sig=9)","/data/论文/leo-t1-wt/runs/sctd-*/cal/sc-tieT-r050-w035-s111")
