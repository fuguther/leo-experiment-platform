"""Per-OD comparison of two distributed cells: baseline vs quantified tie-break.

Answers the side-effect question in both directions at once:
  * do the OD pairs that failed in the baseline get fixed?
  * do the OD pairs that were healthy in the baseline stay healthy?
Usage:  python3 side_effect_compare.py <baseline_cell_dir> <intervention_cell_dir>
"""
import gzip, json, os, sys, collections

def per_od(cell_dir, arm="stale"):
    res = json.load(gzip.open(os.path.join(cell_dir, "arms", arm, "result.json.gz")))
    fates = {int(k): (v if isinstance(v, str) else v.get("fate"))
             for k, v in (res.get("fates") or {}).items()}
    tmap = {}
    with gzip.open(os.path.join(cell_dir, "trace.jsonl.gz"), "rt") as fh:
        for line in fh:
            d = json.loads(line)
            tmap[int(d["packet_id"])] = (d["src_grid_id"], d["dst_grid_id"])
    out = collections.defaultdict(lambda: [0, 0])
    for pid, od in tmap.items():
        out[od][0] += 1
        if fates.get(pid) == "NO_ROUTE":
            out[od][1] += 1
    return out, sum(1 for p in tmap if fates.get(p) == "NO_ROUTE"), len(tmap)

def main(base_dir, intv_dir):
    b, b_all, b_tot = per_od(base_dir)
    q, q_all, q_tot = per_od(intv_dir)
    keys = sorted(set(b) | set(q))
    fixed = harmed = 0
    print(f"{'OD pair':34s} {'sent':>5s} {'base':>6s} {'intv':>6s}  verdict")
    for k in sorted(keys, key=lambda k: -b.get(k, [0, 0])[1]):
        bs, bn = b.get(k, [0, 0])
        qs, qn = q.get(k, [0, 0])
        if bn > 0 and qn == 0:
            verdict = "FIXED"; fixed += 1
        elif bn == 0 and qn > 0:
            verdict = "*** HARMED ***"; harmed += 1
        elif bn > 0 and qn > 0:
            # a partial reduction is not the same as no effect; say so
            verdict = f"reduced {100*(bn-qn)/bn:.1f}%"
        else:
            verdict = ""
        if verdict or bn or qn:
            print(f"{str(k):34s} {bs:5d} {bn:6d} {qn:6d}  {verdict}")
    print()
    print(f"totals: baseline NO_ROUTE {b_all}/{b_tot}, intervention {q_all}/{q_tot}")
    print(f"OD pairs FIXED: {fixed}   OD pairs HARMED: {harmed}")
    print("VERDICT:", "fix is clean on this layout" if harmed == 0
          else f"fix introduces {harmed} new failing OD pair(s) -- do NOT recommend it")

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
