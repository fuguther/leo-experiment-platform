import gzip, json, glob, collections, os
TARGET=("G1:129:296","G1:130:106")
def info(pat):
    c=glob.glob(pat)
    if not c: return None
    c=c[0]
    try:
        res=json.load(gzip.open(os.path.join(c,"arms","stale","result.json.gz")))
    except Exception: return None
    fates={int(k):(v if isinstance(v,str) else v.get("fate")) for k,v in (res.get("fates") or {}).items()}
    tmap={}
    with gzip.open(os.path.join(c,"trace.jsonl.gz"),"rt") as fh:
        for line in fh:
            d=json.loads(line); tmap[int(d["packet_id"])]=(d["src_grid_id"],d["dst_grid_id"])
    sent=sum(1 for p,od in tmap.items() if od==TARGET)
    nr=sum(1 for p,od in tmap.items() if od==TARGET and fates.get(p)=="NO_ROUTE")
    allnr=sum(1 for p in tmap if fates.get(p)=="NO_ROUTE")
    return sent, nr, allnr, len(tmap)
print(f"{'cell':34s} {'OD sent':>8s} {'OD NO_ROUTE':>12s} {'rate':>7s} {'all NR':>7s}")
rows={}
for tag,pat in (("seed111 BASE","/data/论文/leo-t1-wt/runs/scw1b-*/cal/sc-s1-r050-w035-s111"),
                ("seed111 INTERV","/data/论文/leo-t1-wt/runs/sctd-*/cal/sc-tieT-r050-w035-s111"),
                ("seed112 BASE","/data/论文/leo-t1-wt/runs/screp-*/cal/sc-rep-r050-w035-s112b"),
                ("seed112 INTERV","/data/论文/leo-t1-wt/runs/screp-*/cal/sc-rep-r050-w035-s112q"),
                ("seed113 BASE","/data/论文/leo-t1-wt/runs/screp-*/cal/sc-rep-r050-w035-s113b"),
                ("seed113 INTERV","/data/论文/leo-t1-wt/runs/screp-*/cal/sc-rep-r050-w035-s113q")):
    d=info(pat)
    if not d: print(f"{tag:34s} (not ready)"); continue
    sent,nr,allnr,tot=d
    print(f"{tag:34s} {sent:8d} {nr:12d} {100*nr/max(1,sent):6.2f}% {allnr:7d}")
    rows[tag]=(sent,nr)
for s in ("111","112","113"):
    b=rows.get(f"seed{s} BASE"); q=rows.get(f"seed{s} INTERV")
    if b and q:
        print(f"  seed {s}: {b[1]} -> {q[1]}  (relative {100*(q[1]-b[1])/max(1,b[1]):+.1f}%)")
