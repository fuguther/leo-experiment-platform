import csv, os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
D="/tmp/scal/fig"
OUT="/Users/lge/Desktop/topic/.diag-ta-20261002/study-design-20261009/review-20261017/figures"
agg={}
with open(os.path.join(D,"hops.csv"), newline="") as fh:
    for r in csv.DictReader(fh):
        h=r.get("hops")
        if h in (None,""): continue
        agg.setdefault(r["cell"],{})[int(h)]=agg.setdefault(r["cell"],{}).get(int(h),0)+1
fig,ax=plt.subplots(figsize=(7.6,4.8))
order=[c for c in ("S1-low","S3-low","S1-mid","S2-mid","COR-050","COR-095") if c in agg]
w=0.8/len(order)
for i,c in enumerate(order):
    ks=sorted(agg[c]); tot=sum(agg[c].values())
    mean=sum(k*v for k,v in agg[c].items())/tot
    ax.bar([k+i*w for k in ks],[agg[c][k]/tot for k in ks],width=w,
           label=f"{c}: measured mean {mean:.2f}")
# exactly two reference levels, clearly labelled
ax.axvline(3.444,ls="--",lw=1.4,c="k")
ax.axvline(2.083,ls=":",lw=1.4,c="dimgray")
ax.annotate("reference, 3-gateway trunk = 3.44",(3.444,0.72),rotation=90,fontsize=8,
            xytext=(4,0),textcoords="offset points",va="center")
ax.annotate("reference, corridor = 2.08",(2.083,0.72),rotation=90,fontsize=8,color="dimgray",
            xytext=(-12,0),textcoords="offset points",va="center")
ax.set_xlabel("ISL hops per delivered packet")
ax.set_ylabel("fraction of delivered packets")
ax.set_title("F9  path stretch: executed routing vs the reference route set")
ax.set_xlim(0,16); ax.grid(alpha=0.3,axis="y"); ax.legend(fontsize=7,loc="upper right")
fig.tight_layout()
for ext in ("png","pdf"): fig.savefig(os.path.join(OUT,"fig_scene_path_stretch."+ext), dpi=170)
print("F9 redrawn")
for c in order:
    tot=sum(agg[c].values()); mean=sum(k*v for k,v in agg[c].items())/tot
    ref = 3.444 if c.startswith(("S1","S2","S3")) else 2.083
    print(f"  {c:9s} measured {mean:.2f}  reference {ref:.2f}  stretch x{mean/ref:.2f}")
