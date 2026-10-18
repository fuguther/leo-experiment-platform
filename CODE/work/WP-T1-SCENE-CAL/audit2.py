import gzip, json, glob
p=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-truth-s111/arms/now/decisions.jsonl.gz")[0]
with gzip.open(p,"rt") as fh:
    d=json.loads(fh.readline())
ta=d.get("observation_at_start",{}).get("time_alignment",{})
cd=d.get("observation_at_start",{}).get("four_direction_audit",{}).get("candidate_details",{})
tc=d.get("truth_at_commit",{}) or {}
print("top-level keys:", sorted(d.keys()))
print()
print("time_alignment keys:", sorted(ta.keys()))
print()
print("candidate_details[E] keys:", sorted(cd.get("E",{}).keys()))
print("candidate_score[E] keys:", sorted((cd.get("E",{}).get("candidate_score") or {}).keys()))
print("candidate_score[E].terms:", sorted(((cd.get("E",{}).get("candidate_score") or {}).get("terms") or {}).keys()))
print()
print("truth_at_commit keys:", sorted(tc.keys()))
print()
hs=ta.get("history_samples")
print("history_samples type:", type(hs).__name__, "keys:", sorted(hs.keys()) if isinstance(hs,dict) else None)
if isinstance(hs,dict) and "E" in hs:
    e=hs["E"]
    print("  history_samples.E type:", type(e).__name__, "len:", len(e) if hasattr(e,"__len__") else None)
    if isinstance(e,list) and e: print("  sample[0] keys:", sorted(e[0].keys()) if isinstance(e[0],dict) else e[0])
oa=ta.get("observation_age")
print()
print("observation_age:", json.dumps(oa)[:300] if oa else None)
print("query_targets:", json.dumps(ta.get("query_targets"))[:200])
