import sys
sys.path.insert(0, "CODE")
from leo_sim import population as P
p = "CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif"
for deg in (0.25, 1.0, 2.0):
    t = P.load_population_regions(p, deg, source_root=".")
    regs = getattr(t, "regions", [])
    tot = sum(r.population for r in regs)
    nz = sum(1 for r in regs if r.population > 0)
    print(f"agg={deg:>5} deg  cells={len(regs):6d}  non-zero={nz:6d}  total={tot:,.0f}")
