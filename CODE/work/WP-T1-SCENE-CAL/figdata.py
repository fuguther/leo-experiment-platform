import gzip, json, glob, os, csv, statistics, collections
OUT="/tmp/scal/fig"
os.makedirs(OUT, exist_ok=True)
CELLS = []
def add(name, pat, arm=None):
    p = glob.glob(pat)
    if not p: return
    p = p[0]
    try:
        res = json.load(gzip.open(p))
    except Exception:
        return
    CELLS.append((name, res))
add("S1-low",   "/data/论文/leo-t1-wt/runs/scw1-sc-s1-r020-s111-*/cal/*/arms/stale/result.json.gz")
add("S2-low",   "/data/论文/leo-t1-wt/runs/scw1b-sc-s2-r020-s111-*/cal/*/arms/stale/result.json.gz")
add("S3-low",   "/data/论文/leo-t1-wt/runs/scw1b-sc-s3-r020-s111-*/cal/*/arms/stale/result.json.gz")
add("S1-mid",   "/data/论文/leo-t1-wt/runs/scp-sc-probe-s1-r050-s111-*/cal/*/arms/now/result.json.gz")
add("S2-mid",   "/data/论文/leo-t1-wt/runs/scw1b-sc-s2-r050-s111-*/cal/*/arms/stale/result.json.gz")
add("S3-mid",   "/data/论文/leo-t1-wt/runs/scw1b-sc-s3-r050-s111-*/cal/*/arms/stale/result.json.gz")
add("COR-050",  "/data/论文/leo-t1-wt/runs/sccor-sc-cor-r050-stale-s111-*/cal/*/arms/stale/result.json.gz")
add("COR-095",  "/data/论文/leo-t1-wt/runs/sccor-sc-cor-r095-stale-s111-*/cal/*/arms/stale/result.json.gz")
print("cells loaded:", [c[0] for c in CELLS])
rows_util=[]; rows_dec=[]; rows_load=[]; rows_hops=[]; rows_area=[]
for name,res in CELLS:
    L=(res.get("congestion_metrics") or {}).get("links") or {}
    for k,v in L.items():
        if v.get("stage")=="isl":
            rows_util.append((name,k,v["utilization"]))
    pk=(res.get("congestion_metrics") or {}).get("packets") or {}
    if not pk: continue
    def mean(f): 
        v=[float(x.get(f) or 0) for x in pk.values()]
        return statistics.fmean(v) if v else 0.0
    def p95(f):
        v=sorted(float(x.get(f) or 0) for x in pk.values())
        return v[min(len(v)-1,int(0.95*len(v)))] if v else 0.0
    emitted=[(v.get("emitted_at"),v) for v in pk.values() if v.get("emitted_at") is not None]
    win=max(1e-9, max(e for e,_ in emitted)-min(e for e,_ in emitted))
    mbps=len(pk)*12000/win/1e6
    rows_dec.append((name, mean("queue_wait_s"), mean("prop_s"), mean("tx_s"), mean("holding_wait_s"), mean("pre_ingress_s")))
    rows_load.append((name, mbps, mean("e2e_s"), p95("e2e_s")))
    qa=res.get("queue_area_bits_s") or {}
    rows_area.append((name, qa.get("holding") or 0, qa.get("isl_data") or 0, qa.get("uplink") or 0))
    hp=collections.Counter()
    for e in res.get("packet_events") or []:
        if e.get("kind")=="propagation_start" and e.get("stage")=="isl":
            hp[e["pid"]]+=1
    for h,c in sorted(hp.items()):
        rows_hops.append((name, c))
def dump(fn, header, rows):
    with open(os.path.join(OUT,fn),"w",newline="") as fh:
        w=csv.writer(fh); w.writerow(header); w.writerows(rows)
dump("links_util.csv", ["cell","link","util"], rows_util)
dump("decomp.csv", ["cell","stage_queue","prop","tx","holding","pre_ingress"], rows_dec)
dump("load_delay.csv", ["cell","realized_mbps","e2e_mean","e2e_p95"], rows_load)
dump("hops.csv", ["cell","hops","count"], rows_hops)
dump("area.csv", ["cell","holding","isl_data","uplink"], rows_area)
print("rows:", len(rows_util), len(rows_dec), len(rows_hops))
for r in rows_load: print("  ", r[0], round(r[1],1), round(r[2]*1000,1), round(r[3]*1000,1))
for r in rows_area: print("  area", r[0], round(r[1]/1e6,2), round(r[2]/1e6,2), round(r[3]/1e6,2))
