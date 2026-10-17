import gzip, json, glob, csv, statistics, sys, os
ROOTS = ["/data/论文/leo-t1-wt/runs/scp-*", "/data/论文/leo-t1-wt/runs/scw1*"]
def pct(v, q):
    if not v: return None
    s=sorted(v); k=(len(s)-1)*q; lo=int(k); hi=min(lo+1,len(s)-1)
    return s[lo]+(s[hi]-s[lo])*(k-lo)
cells=[]; links=[]
for pat in ROOTS:
    for summ in sorted(glob.glob(pat+"/cal/*/summary.json")):
        d=json.load(open(summ))
        if d.get("status")!="DIAGNOSTIC_COMPLETE": continue
        cell=d["cell"]; base=os.path.dirname(summ)
        armdir=os.path.join(base,"arms",(d.get("arms") or [{}])[0].get("arm","now"))
        rp=os.path.join(armdir,"result.json.gz")
        cfg=json.load(open(os.path.join(armdir,"config.json")))["resolved_config"]
        row=dict(cell=cell, wall_s=round(d["arms"][0]["execution_wall_s"],1),
                 events=d["arms"][0]["events_processed"],
                 packets=(d["arms"][0].get("d4") or {}).get("offered_packets"),
                 offered_mbps=cfg["demand"]["offered_mbps"],
                 emission_s=round(cfg["demand"]["emission_end_s"]-cfg["demand"]["emission_start_s"],3),
                 queue_bits=cfg["links"]["isl_queue_bits"],
                 compute_s=cfg["execution"]["compute_delay_s"])
        res=json.load(gzip.open(rp))
        cm=res.get("congestion_metrics") or {}
        L=cm.get("links") or {}
        util={k:v["utilization"] for k,v in L.items() if v.get("stage")=="isl"}
        row["isl_links_with_data"]=sum(1 for k,v in L.items() if v.get("stage")=="isl" and v.get("served_bits"))
        if util:
            row["isl_util_max"]=round(max(util.values()),5)
            top=max(util,key=util.get)
            row["isl_util_max_link"]=top
            row["isl_util_mean_over_384"]=round(sum(util.values())/384.0,6)
            for k,v in sorted(util.items(), key=lambda kv:-kv[1])[:6]:
                links.append(dict(cell=cell, link=k, util=round(v,5)))
        pk=cm.get("packets") or {}
        def col(name): return [float(v.get(name) or 0.0) for v in pk.values()]
        qw=col("queue_wait_s"); tx=col("tx_s"); pr=col("prop_s"); ho=col("holding_wait_s")
        pre=col("pre_ingress_s"); e2e=col("e2e_s")
        # TRUE ISL queue wait, reconstructed from the raw events.  The
        # congestion_metrics 'queue_wait_s' field is NOT ISL-specific (a
        # first version of this table reported it as ISL queueing and got
        # 7.7 ms at 1% link utilisation, which is impossible); it is the
        # queueing across every stage of a packet's path.
        open_q = {}; isl_q = {}
        for ev in res.get("packet_events") or []:
            pid = ev.get("pid"); kind = ev.get("kind")
            stage = ev.get("stage")
            queue = ev.get("queue")
            key = (pid, ev.get("link_id"))
            if kind == "queue_enter" and (queue == "isl" or stage == "isl"):
                open_q.setdefault(key, []).append(ev.get("at"))
            elif kind == "service_start" and (stage == "isl" or queue == "isl"):
                stack = open_q.get(key)
                if stack:
                    isl_q[pid] = isl_q.get(pid, 0.0) + (ev.get("at", 0.0) - stack.pop(0))
        iqv = list(isl_q.values())
        hops = {}
        for ev in res.get("packet_events") or []:
            if ev.get("kind") == "propagation_start" and ev.get("stage") == "isl":
                hops[ev["pid"]] = hops.get(ev["pid"], 0) + 1
        row.update(delivered=len(pk),
                   isl_queue_from_events_mean=round(statistics.fmean(iqv), 8) if iqv else 0.0,
                   isl_queue_from_events_p95=round(pct(iqv, 0.95), 8) if iqv else 0.0,
                   isl_queue_packets=len(iqv),
                   hops_mean=round(statistics.fmean(hops.values()), 3) if hops else 0.0,
                   hops_p95=round(pct(list(hops.values()), 0.95), 3) if hops else 0.0,
                   stage_sum_queue=round(statistics.fmean(qw), 6) if qw else None,
                   area_uplink=round((res.get("queue_area_bits_s") or {}).get("uplink") or 0, 1),
                   realized_mbps=round((row.get("packets") or 0) * 12000
                                       / max(1e-9, row.get("emission_s") or 1e-9) / 1e6, 1),
                   isl_q_mean=round(statistics.fmean(qw),6) if qw else None,
                   isl_q_p95=round(pct(qw,0.95),6) if qw else None,
                   tx_mean=round(statistics.fmean(tx),6) if tx else None,
                   prop_mean=round(statistics.fmean(pr),6) if pr else None,
                   hold_mean=round(statistics.fmean(ho),6) if ho else None,
                   pre_ingress_mean=round(statistics.fmean(pre),6) if pre else None,
                   e2e_mean=round(statistics.fmean(e2e),6) if e2e else None,
                   e2e_p95=round(pct(e2e,0.95),6) if e2e else None)
        fc=res.get("fate_counts") or {}
        row.update(no_route=fc.get("NO_ROUTE",0), isl_overflow=fc.get("ISL_QUEUE_OVERFLOW",0),
                   deadline_expired=fc.get("DATA_DEADLINE_EXPIRED",0),
                   in_system=fc.get("IN_SYSTEM_AT_STOP",0),
                   totals_offered_bits=(res.get("totals") or {}).get("offered_bits"),
                   totals_delivered_bits=(res.get("totals") or {}).get("delivered_bits"))
        qa=res.get("queue_area_bits_s") or {}
        row.update(area_isl_data=round(qa.get("isl_data") or 0,1),
                   area_isl_ctrl=round(qa.get("isl_ctrl") or 0,1),
                   area_holding=round(qa.get("holding") or 0,1))
        cells.append(row)
out=sys.argv[1] if len(sys.argv)>1 else "/tmp/scal"
os.makedirs(out, exist_ok=True)
if cells:
    keys=sorted({k for c in cells for k in c})
    with open(out+"/cells.csv","w",newline="") as fh:
        wr=csv.DictWriter(fh,fieldnames=keys); wr.writeheader()
        for c in cells: wr.writerow(c)
    with open(out+"/links.csv","w",newline="") as fh:
        wr=csv.DictWriter(fh,fieldnames=["cell","link","util"]); wr.writeheader()
        for l in links: wr.writerow(l)
print("analysed cells:", len(cells))
for c in cells:
    print(f"  {c['cell']:26s} mbps={c['offered_mbps']:7.1f} win={c['emission_s']:.2f} "
          f"isl_max={c.get('isl_util_max')} links={c.get('isl_links_with_data')} "
          f"q_mean={c.get('isl_q_mean')} e2e={c.get('e2e_mean')} no_route={c.get('no_route')} "
          f"ovf={c.get('isl_overflow')}")
