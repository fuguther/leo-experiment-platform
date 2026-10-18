import gzip, json, glob, os, collections
cell=glob.glob("/data/论文/leo-t1-wt/runs/scbl-*/cal/sc-blin-r050-p0fa-s111")[0]
s=json.load(open(os.path.join(cell,"summary.json")))
print("cell", s["cell"], s["status"])
arms={}
for a in s.get("arms") or []:
    d4=a.get("d4") or {}
    print("  %-10s capped=%s notD4=%s pkts=%s wall=%s" % (
        a.get("arm"), d4.get("capped_loss"), d4.get("deadline_not_delivered_proportion"),
        d4.get("offered_packets"), round(a.get("execution_wall_s") or 0)))
    if a.get("decisions"):
        p=os.path.join(cell,"arms",a["arm"],"decisions.jsonl.gz")
        m={}
        try:
            with gzip.open(p,"rt") as fh:
                for line in fh:
                    d=json.loads(line)
                    m[(d.get("sat"), round(float(d.get("t") or 0),6))]=d.get("chosen")
        except Exception as e:
            print("     (read issue)", type(e).__name__)
        arms[a["arm"]]=m
names=sorted(arms)
print("  arms with decision streams:", names)
for i in range(1,len(names)):
    b,o=arms[names[0]],arms[names[i]]
    common=set(b)&set(o)
    if not common: continue
    diff=sum(1 for k in common if b[k]!=o[k])
    print("  decisions differing %s vs %s: %d/%d = %.4f%%" % (names[i],names[0],diff,len(common),100*diff/len(common)))
