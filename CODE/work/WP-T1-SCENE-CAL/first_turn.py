import gzip, json, glob
cell=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
res=json.load(gzip.open(cell+"/arms/stale/result.json.gz"))
want={1:"DELIVERED",5:"NO_ROUTE",10:"NO_ROUTE",7:"NO_ROUTE"}
seen={}
with gzip.open(cell+"/arms/stale/decisions.jsonl.gz","rt") as fh:
    for line in fh:
        d=json.loads(line)
        pid=d.get("pid")
        if pid in want:
            seen.setdefault(pid,[]).append(d)
for pid,fate in want.items():
    rows=seen.get(pid,[])
    print(f"=== pid {pid} ({fate}) decisions={len(rows)}")
    for d in rows[:4]:
        sat=d.get("sat"); t=d.get("t"); ch=d.get("chosen")
        oa=d.get("observation_at_start") or {}
        cd=(oa.get("four_direction_audit") or {}).get("candidate_details") or {}
        parts=[]
        for k in sorted(cd):
            sc=(cd[k].get("candidate_score") or {}).get("total_s")
            lg=cd[k].get("final_legal")
            parts.append(f"{k}{'*' if k==ch else ''}={('%.5f'%sc) if sc is not None else 'NA'}{'' if lg else '(ill)'}")
        ta=oa.get("time_alignment") or {}
        print(f"  sat={sat} t={t:.4f} chose {ch} | {' '.join(parts)}")
