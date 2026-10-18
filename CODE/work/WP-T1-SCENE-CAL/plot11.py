import os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
OUT="/Users/lge/Desktop/topic/.diag-ta-20261002/study-design-20261009/review-20261017/figures"
seeds=["111","112","113"]
base_sent=[7282,7308,7128]; base_nr=[194,186,198]
intv_sent=[7282,7308,7128]; intv_nr=[1,0,0]
base_rate=[100*n/s for n,s in zip(base_nr,base_sent)]
intv_rate=[100*n/s for n,s in zip(intv_nr,intv_sent)]
fig,(a1,a2)=plt.subplots(1,2,figsize=(10.4,4.4))
x=range(len(seeds)); w=0.38
a1.bar([i-w/2 for i in x],base_nr,w,label="baseline (as-is)",color="C3")
a1.bar([i+w/2 for i in x],intv_nr,w,label="intervention (score quantised)",color="C0")
for i,(b,q) in enumerate(zip(base_nr,intv_nr)):
    a1.annotate(str(b),(i-w/2,b),ha="center",va="bottom",fontsize=9)
    a1.annotate(str(q),(i+w/2,q),ha="center",va="bottom",fontsize=9)
a1.set_xticks(list(x)); a1.set_xticklabels([f"seed {s}" for s in seeds])
a1.set_ylabel("NO_ROUTE packets on Beijing -> New York")
a1.set_title("paired, one cell each: same trace, same window,\nonly the score comparison changed")
a1.grid(alpha=0.3,axis="y"); a1.legend(fontsize=8)
a1.set_ylim(0,max(base_nr)*1.22)
a2.bar([i-w/2 for i in x],base_rate,w,label="baseline",color="C3")
a2.bar([i+w/2 for i in x],intv_rate,w,label="intervention",color="C0")
for i,(b,q) in enumerate(zip(base_rate,intv_rate)):
    a2.annotate(f"{b:.2f}%",(i-w/2,b),ha="center",va="bottom",fontsize=9)
    a2.annotate(f"{q:.2f}%",(i+w/2,q),ha="center",va="bottom",fontsize=9)
a2.set_xticks(list(x)); a2.set_xticklabels([f"seed {s}" for s in seeds])
a2.set_ylabel("failure rate on that OD (%)")
a2.set_title("baseline spread is 6% across seeds;\nthe intervention is at or near zero")
a2.grid(alpha=0.3,axis="y"); a2.legend(fontsize=8)
a2.set_ylim(0,max(base_rate)*1.22)
fig.suptitle("F11  score quantisation removes the routing failures, three paired seeds", fontsize=11)
fig.tight_layout(rect=(0,0,1,0.94))
for ext in ("png","pdf"): fig.savefig(os.path.join(OUT,"fig_tiebreak_intervention."+ext), dpi=170)
print("F11 written")
print("baseline rates", [round(r,2) for r in base_rate], "intervention", [round(r,2) for r in intv_rate])
