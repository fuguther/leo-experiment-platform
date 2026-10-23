import csv, os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
D="/tmp/scal/fig2"
OUT="/Users/lge/Desktop/topic/.diag-ta-20261002/study-design-20261009/review-20261017/figures"
rows=list(csv.DictReader(open(os.path.join(D,"fates.csv"))))
rows.sort(key=lambda r: (r["cell"], r["arm"]))
labels=[f"{r['cell'].replace('sc-','').replace('-s111','')}" for r in rows]
tot=[int(r["total"]) for r in rows]
nr=[int(r["no_route"]) for r in rows]
ex=[int(r["expired"]) for r in rows]
ov=[int(r["overflow"]) for r in rows]
dl=[int(r["delivered"]) for r in rows]
fig,ax=plt.subplots(figsize=(8.4,5.0))
x=range(len(rows))
ax.bar(x,dl,label="delivered",color="C0")
ax.bar(x,nr,bottom=dl,label="NO_ROUTE (routing failure)",color="C3")
ax.bar(x,ex,bottom=[a+b for a,b in zip(dl,nr)],label="deadline expired",color="C1")
ax.bar(x,ov,bottom=[a+b+c for a,b,c in zip(dl,nr,ex)],label="queue overflow",color="C4")
for i,r in enumerate(rows):
    rate=100*int(r["no_route"])/max(1,int(r["total"]))
    ax.annotate(f"{rate:.2f}%",(i,int(r["total"])),fontsize=6,rotation=90,
                xytext=(0,2),textcoords="offset points",ha="center")
ax.set_xticks(list(x)); ax.set_xticklabels(labels,rotation=45,ha="right",fontsize=7)
ax.set_ylabel("packets")
ax.set_title("F8  packet fates: every loss is a routing failure, none is late or dropped")
ax.grid(alpha=0.3,axis="y"); ax.legend(fontsize=8,loc="upper right")
y=int(max(tot))*1.18
ax.set_ylim(0,y)
fig.tight_layout()
for ext in ("png","pdf"): fig.savefig(os.path.join(OUT,"fig_scene_packet_fates."+ext), dpi=170)
print("F8 written; cells", len(rows), "total expired", sum(ex), "total overflow", sum(ov))
