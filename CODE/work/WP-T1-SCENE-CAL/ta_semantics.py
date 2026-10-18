import gzip, json, glob, os
CELL=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
for arm in ("stale","now","common","candidate"):
    p=os.path.join(CELL,"arms",arm,"decisions.jsonl.gz")
    if not os.path.exists(p): print(arm,"(no file)"); continue
    with gzip.open(p,"rt") as fh:
        d=json.loads(fh.readline())
    ta=d["observation_at_start"]["time_alignment"]
    us=ta.get("used_state") or {}
    qt=ta.get("query_targets") or {}
    et=ta.get("eta_targets") or {}
    k=sorted(us.keys())[0]
    u=us[k]
    print(f"=== arm={arm}  snapshot_at={ta.get('snapshot_at')}")
    print(f"    used_state[{k}]: resource={u.get('resource_id')} gen={u.get('generation')} "
          f"measured={u.get('measured_at')} received={u.get('received_at')} used_at={u.get('used_at')}")
    print(f"    query_targets[{k}]={qt.get(k)}   eta_targets[{k}]={et.get(k)}")
    print(f"    truth t_observed={d.get('truth_at_commit',{}).get('t_observed')} "
          f"source={d.get('truth_at_commit',{}).get('source')}")
    print()
