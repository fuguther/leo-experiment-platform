import csv, os
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
D="/tmp/scal/fig2"
OUT="/Users/lge/Desktop/topic/.diag-ta-20261002/study-design-20261009/review-20261017/figures"
os.makedirs(OUT, exist_ok=True)
def read(fn):
    with open(os.path.join(D,fn), newline="") as fh: return list(csv.DictReader(fh))
def save(fig, stem):
    for ext in ("png","pdf"): fig.savefig(os.path.join(OUT,stem+"."+ext), dpi=170)
    plt.close(fig)

dec=read("decomp_post.csv")
fig,ax=plt.subplots(figsize=(7.4,4.8))
names=[r["cell"] for r in dec]
for key,label in (("isl_queue","ISL queue"),("access","access (pre-ingress)"),
                  ("tx","serialization"),("prop","propagation"),("holding","holding")):
    ax.bar(names,[float(r[key])*1000 for r in dec],label=label)
ax.set_ylabel("mean per-packet time, POST-FILL window (ms)")
ax.set_title("F2  latency decomposition on the declared post-fill window")
ax.tick_params(axis="x",rotation=30,labelsize=8); ax.grid(alpha=0.3,axis="y"); ax.legend(fontsize=7)
fig.tight_layout(); save(fig,"fig_scene_latency_decomposition_postfill")

ld=read("load_delay_post.csv")
fig,ax=plt.subplots(figsize=(6.4,4.4))
for r in ld:
    x=float(r["realized_mbps"]); m=float(r["e2e_mean"])*1000; p=float(r["e2e_p95"])*1000
    ax.scatter(x,m,s=46,c="C0"); ax.scatter(x,p,s=46,c="C3",marker="^")
    ax.annotate(r["cell"],(x,m),fontsize=7,xytext=(4,-10),textcoords="offset points")
ax.scatter([],[],c="C0",label="mean"); ax.scatter([],[],c="C3",marker="^",label="p95")
ax.set_xlabel("realised injection in the post-fill window (Mbps)")
ax.set_ylabel("end-to-end delay (ms)")
ax.set_title("F3  delay vs injection (post-fill)")
ax.grid(alpha=0.3); ax.legend(fontsize=8)
fig.tight_layout(); save(fig,"fig_scene_load_vs_delay_postfill")

cdf=read("cdf.csv")
fig,ax=plt.subplots(figsize=(6.8,4.6))
agg={}
for r in cdf: agg.setdefault(r["cell"],[]).append((float(r["q"]),float(r["e2e_s"])*1000))
for name in sorted(agg):
    pts=sorted(agg[name])
    ax.plot([p[1] for p in pts],[p[0] for p in pts],lw=1.5,label=name)
ax.set_xlabel("end-to-end delay (ms)"); ax.set_ylabel("empirical CDF")
ax.set_title("F6  end-to-end delay CDF (post-fill)")
ax.grid(alpha=0.3); ax.legend(fontsize=7)
fig.tight_layout(); save(fig,"fig_scene_e2e_cdf")

qt=read("queue_timeline.csv")
fig,ax=plt.subplots(figsize=(7.2,4.6))
agg={}
for r in qt: agg.setdefault(r["cell"],[]).append((float(r["emit_t"]),float(r["isl_q_mean"])*1000))
for name in sorted(agg):
    pts=sorted(agg[name])
    ax.plot([p[0] for p in pts],[p[1] for p in pts],lw=1.4,label=name)
ax.axvline(2.0,ls="--",lw=1,c="k"); ax.axvline(2.2,ls=":",lw=1,c="k")
ax.set_xlim(1.95,3.0)
ax.set_xlabel("emission time (s); dashed = emission start, dotted = end of fill period")
ax.set_ylabel("mean ISL queue wait of packets emitted in 10 ms bin (ms)")
ax.set_title("F7  ISL queue time through the emission window")
ax.grid(alpha=0.3); ax.legend(fontsize=7)
fig.tight_layout(); save(fig,"fig_scene_queue_timeline")
print("post-fill figures written")
print(sorted(f for f in os.listdir(OUT) if f.startswith("fig_scene")))
