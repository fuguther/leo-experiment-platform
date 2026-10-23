import csv, os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
D="/tmp/scal/fig"
OUT="/Users/lge/Desktop/topic/.diag-ta-20261002/study-design-20261009/review-20261017/figures"
def read(fn):
    with open(os.path.join(D, fn), newline="") as fh: return list(csv.DictReader(fh))
def save(fig, stem):
    for ext in ("png","pdf"): fig.savefig(os.path.join(OUT, stem+"."+ext), dpi=170)
    plt.close(fig)
# F4 hops: hops.csv holds one row per delivered packet; hop count in "hops"
agg={}
for r in read("hops.csv"):
    h=r.get("hops")
    if h in (None,""): continue
    d=agg.setdefault(r["cell"],{})
    d[int(h)]=d.get(int(h),0)+1
fig,ax=plt.subplots(figsize=(7.0,4.4))
for name in sorted(agg):
    ks=sorted(agg[name]); tot=sum(agg[name].values())
    ax.plot(ks,[agg[name][k]/tot for k in ks],marker="o",ms=3,lw=1.3,label=f"{name} (n={tot})")
ax.set_xlabel("ISL hops per delivered packet"); ax.set_ylabel("fraction")
ax.set_title("F4  path length distribution"); ax.grid(alpha=0.3); ax.legend(fontsize=7)
fig.tight_layout(); save(fig,"fig_scene_hop_distribution")
# F5 queue-area composition
ar=read("area.csv")
fig,ax=plt.subplots(figsize=(7.2,4.6))
names=[r["cell"] for r in ar]
for key,label in (("holding","holding (awaiting destination serving)"),
                  ("isl_data","ISL data"),("uplink","uplink/access")):
    ax.bar(names,[float(r[key])/1e6 for r in ar],label=label)
ax.set_ylabel("queue area (Mbit.s, whole run)")
ax.set_title("F5  where the waiting actually is")
ax.tick_params(axis="x",rotation=30,labelsize=8); ax.grid(alpha=0.3,axis="y"); ax.legend(fontsize=8)
fig.tight_layout(); save(fig,"fig_scene_queue_area_composition")
print("F4/F5 written")
print(sorted(f for f in os.listdir(OUT) if f.startswith("fig_scene")))
