import sys, os, math, json, pathlib
sys.path.insert(0, "CODE")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplcfg")
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from leo_sim import model as M
c = M.Constellation(96, 12, 800.0, 53.0, 10.0, 6000.0, 0.0)
t = 2.10
subs = [c.subpoint(s, t) for s in range(96)]
lats = [s[0] for s in subs]; lons = [s[1] for s in subs]
planes = [s // 8 for s in range(96)]
pos = c.positions(t)
fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11.4, 5.2))
edges = 0
for i in range(96):
    for j in range(i+1, 96):
        if math.dist(pos[i], pos[j]) <= 6000.0:
            ax.plot([lons[i], lons[j]], [lats[i], lats[j]], "-", c="0.78", lw=0.4, zorder=1)
            edges += 1
ax.scatter(lons, lats, c=planes, cmap="tab20", s=34, zorder=3)
SITES = {"Beijing": (39.90, 116.40), "London": (51.50, -0.13), "New York": (40.70, -74.00),
         "Shanghai": (31.20, 121.50), "Tokyo": (35.70, 139.70), "Frankfurt": (50.10, 8.68),
         "Sydney": (-33.87, 151.20), "Sao Paulo": (-23.55, -46.63)}
for name, (la, lo) in SITES.items():
    ax.scatter([lo], [la], marker="*", s=150, c="crimson", zorder=5, edgecolors="k", linewidths=0.5)
    ax.annotate(name, (lo, la), fontsize=7, xytext=(4, 3), textcoords="offset points", zorder=6)
ax.set_xlabel("longitude (deg)"); ax.set_ylabel("latitude (deg)")
ax.set_title("F10a  96/12/8 @800 km at t=2.10 s. Grey = ALL %d pairs inside the 6000 km RANGE;"
             " the kernel then MATCHES a subset (executed topology = 192 undirected ISLs)." % edges,
             fontsize=8)
ax.grid(alpha=0.3)
W = pathlib.Path("CODE/work/WP-T1-SCENE-CAL")
trunk = json.loads((W/"scene-spec-from-profile.json").read_text())["od_weights"]
ks = sorted(trunk, key=lambda k: -trunk[k])
ax2.barh(range(len(ks)), [trunk[k] for k in ks], color="C0")
ax2.set_yticks(range(len(ks))); ax2.set_yticklabels(ks, fontsize=8)
ax2.invert_yaxis(); ax2.set_xlabel("OD weight")
ax2.set_title("F10b  trunk OD weights (3 gateways, 4:1:1)\nthe failing OD is beijing,newyork", fontsize=8)
ax2.grid(alpha=0.3, axis="x")
fig.tight_layout()
OUT = "/Users/lge/Desktop/topic/.diag-ta-20261002/study-design-20261009/review-20261017/figures"
for ext in ("png", "pdf"):
    fig.savefig(os.path.join(OUT, "fig_scene_topology_od." + ext), dpi=165)
print("F10 rewritten; range-pairs =", edges)
