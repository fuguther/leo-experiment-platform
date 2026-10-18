import gzip, json, glob, collections
cell=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
split=collections.Counter(); tot=0; pairs=collections.Counter()
with gzip.open(cell+"/arms/stale/decisions.jsonl.gz","rt") as fh:
    for line in fh:
        d=json.loads(line)
        oa=d.get("observation_at_start") or {}
        cd=(oa.get("four_direction_audit") or {}).get("candidate_details") or {}
        sc={}
        for k,v in cd.items():
            if not v.get("final_legal"): continue
            s=(v.get("candidate_score") or {}).get("total_s")
            if s is not None: sc[k]=float(s)
        if len(sc)<2: continue
        items=sorted(sc.items(), key=lambda kv: kv[1])
        if items[1][1]-items[0][1] > 1e-12: continue
        a,b=items[0][0],items[1][0]
        pairs[tuple(sorted((a,b)))]+=1
        tot+=1
        ch=d.get("chosen")
        if ch==a: split["first_in_sorted_order"]+=1
        elif ch==b: split["second_in_sorted_order"]+=1
        else: split["neither"]+=1
print("decisions with an exact top-2 tie:", tot)
print("  chosen == the one the sort put first :", split["first_in_sorted_order"])
print("  chosen == the other one              :", split["second_in_sorted_order"])
print("  chosen == neither                    :", split["neither"])
print("  tied direction pairs (top 6):", pairs.most_common(6))
