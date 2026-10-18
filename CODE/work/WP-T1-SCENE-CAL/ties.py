import gzip, json, glob, collections
cell=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
tot=0; tied=0; tie_at_entry=0; tie_examples=0
margins=[]
with gzip.open(cell+"/arms/stale/decisions.jsonl.gz","rt") as fh:
    for line in fh:
        d=json.loads(line); tot+=1
        oa=d.get("observation_at_start") or {}
        cd=(oa.get("four_direction_audit") or {}).get("candidate_details") or {}
        sc=[]
        for k,v in cd.items():
            if not v.get("final_legal"): continue
            s=(v.get("candidate_score") or {}).get("total_s")
            if s is None: continue
            sc.append((float(s),k))
        if len(sc)<2: continue
        sc.sort()
        gap=sc[1][0]-sc[0][0]
        margins.append(gap)
        if gap <= 1e-12:
            tied+=1
            if tie_examples<3:
                print("  tie at sat",d.get("sat"),"t=%.4f"%d.get("t"),sc[:4]); tie_examples+=1
margins.sort()
import statistics
print(f"decisions={tot} with>=2 legal candidates={len(margins)}")
print(f"EXACT ties among top-2 (gap<=1e-12): {tied} = {100*tied/max(1,len(margins)):.2f}%")
n=len(margins)
print(f"score gap top1-top2: p10={margins[int(.1*n)]:.3e} p25={margins[int(.25*n)]:.3e} "
      f"p50={margins[n//2]:.3e} p75={margins[int(.75*n)]:.3e}")
print(f"gap < 1e-5 s: {sum(1 for m in margins if m<1e-5)} = {100*sum(1 for m in margins if m<1e-5)/max(1,n):.2f}%")
