import sys, statistics, collections
sys.path.insert(0,"/tmp")
from bpl import label_rows
import glob
C=glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cor-r095-stale-s111")
C=C[0] if C else glob.glob("/data/论文/leo-t1-wt/runs/scc-*/cal/sc-cond-r050-arm4-s111")[0]
arm="stale" if "cor" in C else "candidate"
rows=label_rows(C, arm, 40000)
lab=[r for r in rows if r["label_bits"] is not None]
nz=[r for r in lab if r["label_bits"]>0]
pnz=[r for r in lab if r["predicted_bits"]>0]
print(f"cell={C.split('/cal/')[-1]} arm={arm} rows={len(rows)} labelled={len(lab)}")
print(f"  label>0: {len(nz)} ({100*len(nz)/max(1,len(lab)):.2f}%)   predicted>0: {len(pnz)} ({100*len(pnz)/max(1,len(lab)):.2f}%)")
if nz:
    e=[abs(r["error_bits"]) for r in nz]
    print(f"  on label>0 subset: |err| mean={statistics.fmean(e):.0f} bits, med={statistics.median(e):.0f}")
    print(f"  sample non-zero: pred={nz[0]['predicted_bits']:.0f} label={nz[0]['label_bits']:.0f} err={nz[0]['error_bits']:.0f}")
