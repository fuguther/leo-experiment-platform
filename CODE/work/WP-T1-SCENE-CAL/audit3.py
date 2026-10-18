import gzip, json, glob
p=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-truth-s111/arms/now/decisions.jsonl.gz")[0]
with gzip.open(p,"rt") as fh:
    d=json.loads(fh.readline())
ta=d["observation_at_start"]["time_alignment"]
tc=d["truth_at_commit"]
print("used_state keys:", sorted(ta["used_state"].keys()) if isinstance(ta["used_state"],dict) else type(ta["used_state"]))
us=ta["used_state"]
if isinstance(us,dict):
    k=sorted(us.keys())[0]
    print(f"used_state[{k}]:", json.dumps(us[k])[:400])
print()
print("candidate_truth type:", type(tc["candidate_truth"]).__name__)
ct=tc["candidate_truth"]
if isinstance(ct,dict):
    k=sorted(ct.keys())[0]
    print(f"candidate_truth[{k}]:", json.dumps(ct[k])[:400])
print()
print("mapping_status:", json.dumps(tc.get("mapping_status"))[:200])
print("t_observed:", tc.get("t_observed"), " contract:", json.dumps(tc.get("contract"))[:150])
print()
print("history_samples.E value:", ta["history_samples"].get("E"))
print("scores[0]:", json.dumps((ta.get("scores") or [{}])[0])[:250])
