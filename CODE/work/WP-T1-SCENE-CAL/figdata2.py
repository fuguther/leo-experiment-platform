import gzip, json, glob, os, csv, statistics, collections
OUT="/tmp/scal/fig2"; os.makedirs(OUT, exist_ok=True)
def add(name, pat):
    p=glob.glob(pat)
    if not p: return None
    try: return (name, json.load(gzip.open(p[0])))
    except Exception: return None
CELLS=[c for c in (
  add("S1-low","/data/论文/leo-t1-wt/runs/scw1-sc-s1-r020-s111-*/cal/*/arms/stale/result.json.gz"),
  add("S3-low","/data/论文/leo-t1-wt/runs/scw1b-sc-s3-r020-s111-*/cal/*/arms/stale/result.json.gz"),
  add("S1-mid","/data/论文/leo-t1-wt/runs/scp-sc-probe-s1-r050-s111-*/cal/*/arms/now/result.json.gz"),
  add("S2-mid","/data/论文/leo-t1-wt/runs/scw1b-sc-s2-r050-s111-*/cal/*/arms/stale/result.json.gz"),
  add("COR-050","/data/论文/leo-t1-wt/runs/sccor-sc-cor-r050-stale-s111-*/cal/*/arms/stale/result.json.gz"),
  add("COR-095","/data/论文/leo-t1-wt/runs/sccor-sc-cor-r095-stale-s111-*/cal/*/arms/stale/result.json.gz"),
) if c]
dec=[]; ld=[]; cdf=[]; tl=[]
for name,res in CELLS:
    pk=(res.get("congestion_metrics") or {}).get("packets") or {}
    if not pk: continue
    ev=res.get("packet_events") or []
    iq={}; oq={}
    for e in ev:
        pid=e.get("pid"); k=e.get("kind")
        if k=="queue_enter" and e.get("queue")=="isl":
            oq.setdefault((pid,e.get("link_id")),[]).append(e.get("at"))
        elif k=="service_start" and e.get("stage")=="isl":
            st=oq.get((pid,e.get("link_id")))
            if st: iq[pid]=iq.get(pid,0.0)+(e.get("at",0)-st.pop(0))
    em=[(v.get("emitted_at"),int(k),v) for k,v in pk.items() if v.get("emitted_at") is not None]
    lo=min(e for e,_,_ in em)+0.20; hi=max(e for e,_,_ in em)
    sel=[(pid,v) for e,pid,v in em if lo<=e<=hi]
    if not sel: continue
    win=max(1e-9, hi-lo); mbps=len(sel)*12000/win/1e6
    def m(f): 
        return statistics.fmean([float(v.get(f) or 0) for _,v in sel])
    def p95(f):
        a=sorted(float(v.get(f) or 0) for _,v in sel); return a[min(len(a)-1,int(.95*len(a)))]
    iqv=[iq.get(pid,0.0) for pid,_ in sel]
    dec.append((name, statistics.fmean(iqv), m("pre_ingress_s"), m("tx_s"), m("prop_s"), m("holding_wait_s")))
    ld.append((name, mbps, m("e2e_s"), p95("e2e_s")))
    e2=sorted(float(v.get("e2e_s") or 0) for _,v in sel)
    for q in range(0,201):
        idx=min(len(e2)-1, int(q/200*(len(e2)-1)))
        cdf.append((name, q/200.0, e2[idx]))
    L=(res.get("congestion_metrics") or {}).get("links") or {}
    busy=max(((v["utilization"],k) for k,v in L.items() if v.get("stage")=="isl" and v.get("served_bits")), default=(0,None))
    if busy[1]:
        sat,peer=busy[1].split(":")[1:3]
        q=0
        series=[]
        for e in sorted(ev, key=lambda x: x.get("at",0)):
            if str(e.get("sat"))==sat and str(e.get("peer"))==peer:
                if e.get("kind")=="queue_enter": q+=e.get("bits") or 0
                elif e.get("kind")=="service_start": q-=e.get("bits") or 0
                series.append((e.get("at",0), q))
        for t,qb in series[::max(1,len(series)//400)]:
            tl.append((name, busy[1], t, qb))
def dump(fn,h,rows):
    with open(os.path.join(OUT,fn),"w",newline="") as fh:
        w=csv.writer(fh); w.writerow(h); w.writerows(rows)
dump("decomp_post.csv",["cell","isl_queue","access","tx","prop","holding"],dec)
dump("load_delay_post.csv",["cell","realized_mbps","e2e_mean","e2e_p95"],ld)
dump("cdf.csv",["cell","q","e2e_s"],cdf)
dump("timeline.csv",["cell","link","t","queue_bits"],tl)
print("post-fill cells:", len(dec), "cdf rows", len(cdf), "timeline rows", len(tl))
for r in ld: print("  ", r[0], round(r[1],1), "Mbps  e2e", round(r[2]*1000,1), "p95", round(r[3]*1000,1))
for r in dec: print("  ", r[0], "islq", round(r[1]*1000,3), "hold", round(r[5]*1000,1))
