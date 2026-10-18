import sys
sys.path.insert(0, "CODE")
from leo_sim import grid as G, model as M
SITES = {"beijing": (39.90, 116.40), "london": (51.50, -0.13), "newyork": (40.70, -74.00)}
print("--- site -> grid ids ---")
ids = {}
for name, (lat, lon) in SITES.items():
    fine = G.grid_id(lat, lon, deg=0.25)
    agg = G.aggregate_id(fine, agg_deg=1.0)
    ids[agg] = name
    print(f"  {name:10s} fine={fine} agg={agg}")
target = "G1:130:106"
print("target", target, "->", ids.get(target, "(not one of the three sites)"))
c = M.Constellation(96, 12, 800.0, 53.0, 10.0, 6000.0, 0.0)
print()
print("--- serving-satellite count over the emission window, min elevation 10 deg ---")
for agg, name in ids.items():
    cnts = []
    t = 2.00
    while t <= 2.401:
        c_lat, c_lon = G.grid_center(agg)
        n = sum(1 for s in range(96) if c.elevation_deg(s, c_lat, c_lon, t) > 10.0)
        cnts.append((round(t, 3), n))
        t += 0.01
    ns = [n for _, n in cnts]
    print(f"  {name:10s} agg={agg}: min={min(ns)} max={max(ns)} "
          f"zero-windows={sum(1 for n in ns if n == 0)}/{len(ns)} "
          f"mean={sum(ns)/len(ns):.2f}")
    gaps = [t for t, n in cnts if n == 0]
    if gaps:
        print(f"      zero-coverage times (first 8): {gaps[:8]}")
