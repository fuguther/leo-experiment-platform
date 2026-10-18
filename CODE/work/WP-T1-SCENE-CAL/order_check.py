import gzip, json, glob, collections
cell=glob.glob("/data/论文/leo-t1-wt/runs/sccor-sc-cor-r050-stale-s111-*")[0]
p=cell+"/cal/sc-cor-r050-stale-s111/arms/stale/decisions.jsonl.gz"
c=collections.Counter(); n=0
with gzip.open(p,"rt") as fh:
    for line in fh:
        d=json.loads(line); n+=1
        oa=d.get("observation_at_start") or {}
        ta=oa.get("time_alignment") or {}
        ao=ta.get("applied_order")
        if ao: c[tuple(ao)]+=1
        if n>=40000: break
print("decisions scanned:", n)
print("distinct applied_order sequences:", len(c))
for seq, cnt in c.most_common(6):
    print("   ", seq, cnt)
