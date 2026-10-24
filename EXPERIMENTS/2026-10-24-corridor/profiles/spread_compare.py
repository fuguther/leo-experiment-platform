"""Severity test: stale vs ideal_now (perfect current state) in the 20-city queue-dominated scenario."""
import json,statistics,os,sys
D=0.4
def load(p):
    d=json.load(open(p)); arms=d.get('arms') or []
    if not arms: return None
    a=arms[0]; no=a['network_outcome']; recs={}; miss={}; fates={}
    for r in no.get('packet_outcomes') or []:
        if not r.get('in_population',True): continue
        dl=None
        if r.get('fate')=='DELIVERED' and r.get('delivery_time_s') is not None:
            dl=r['delivery_time_s']-r['emit_time_s']
        recs[r['pid']]=dl; miss[r['pid']]=1 if (dl is None or dl>D) else 0
        fates[r.get('fate')]=fates.get(r.get('fate'),0)+1
    n=len(recs); links=a['congestion_metrics']['links']
    isl=[v['utilization'] for k,v in links.items() if v.get('stage')=='isl']
    return dict(recs=recs,miss=miss,n=n,
        mean=1000*statistics.fmean([x for x in recs.values() if x is not None]),
        pp=100*sum(miss.values())/n,
        util_max=max(isl), frac=sum(1 for u in isl if u>=0.2)/len(isl),
        loss=(no.get('terminal_loss') or {}).get('packets'),
        censor=(no.get('censoring') or {}).get('packets'),
        delivery=no.get('delivery_ratio',{}).get('by_packets'),
        e2e_p95=1000*((no.get('e2e') or {}).get('p95_s') or 0),
        trace=((d.get('source') or {}).get('trace_sha256') or '')[:12])
def pair(A,B):
    pid=[p for p in A['recs'] if p in B['recs']]
    d=[1000*(A['recs'][p]-B['recs'][p]) for p in pid if A['recs'][p] is not None and B['recs'][p] is not None]
    dpp=[A['miss'][p]-B['miss'][p] for p in pid]
    sd=sorted(d); n=len(sd)
    trim=statistics.fmean(sd[int(0.1*n):int(0.9*n)]) if n>10 else float('nan')
    return n,statistics.fmean(d),trim,statistics.median(d),100*statistics.fmean(dpp)
def main(runs):
    for s in (7,8):
        sp=os.path.join(runs,f'spread20_s{s}_stale-summary.json'); op=os.path.join(runs,f'spread20_orc_s{s}_now-summary.json')
        if not (os.path.exists(sp) and os.path.exists(op)): print(f'seed {s}: missing'); continue
        S=load(sp); O=load(op)
        n,dm,trim,dmed,dpp=pair(O,S)
        print(f"seed {s}: stale {S['mean']:.2f}ms/{S['pp']:.2f}% (deliv {S['delivery']:.3f}, censor {S['censor']}, util {S['util_max']:.3f}, frac {100*S['frac']:.1f}%)")
        print(f"         ideal_now {O['mean']:.2f}ms/{O['pp']:.2f}% (deliv {O['delivery']:.3f}, censor {O['censor']}, util {O['util_max']:.3f}, frac {100*O['frac']:.1f}%)")
        print(f"         ideal−stale: n={n} Δmean={dm:+.3f} ms (median {dmed:+.3f}, trim {trim:+.3f}) Δpp={dpp:+.3f} trace_same={S['trace']==O['trace']}")
main(sys.argv[1] if len(sys.argv)>1 else '.')
