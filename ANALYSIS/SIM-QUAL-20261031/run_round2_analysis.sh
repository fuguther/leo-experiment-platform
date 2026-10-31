#!/usr/bin/env bash
# After all Round-2 cells are pulled VERIFIED, build the analysis artifacts.
set -euo pipefail
WORKTREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WORKTREE"
E="/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1"
OUT="ANALYSIS/SIM-QUAL-20261031/round2-results"
mkdir -p "$OUT"

cell_dir() { printf '%s/sq4-%s-20261031-01/%s' "$E" "$1" "$1"; }

A=(); B=(); C=(); D=(); SYNC=()
for pol in geom greedy ewma; do
    A+=("$(cell_dir "m2-a-$pol-s7")")
    for s in 7 8 9; do C+=("$(cell_dir "m2-c2-$pol-s$s")"); done
done
for pol in geom greedy ewma; do
    for s in 7 8; do B+=("$(cell_dir "m2-b-$pol-s$s")"); done
    D+=("$(cell_dir "m2-d-$pol-s7")")
    for adv in 1000 0100 0020; do SYNC+=("$(cell_dir "m2-sync${adv}-c2-$pol-s7")"); done
done
HYST=("$(cell_dir m2-c2-hyst-s7)")

echo "[analysis] A/B/C/D/sync groups"
python3 ANALYSIS/SIM-QUAL-20261031/sq_round2_analyze.py "${A[@]}" "${B[@]}" "${C[@]}" "${D[@]}" "${HYST[@]}" \
    --json "$OUT/regimes.json" --md "$OUT/regimes.md"
python3 ANALYSIS/SIM-QUAL-20261031/sq_round2_analyze.py "${SYNC[@]}" \
    --json "$OUT/sync.json" --md "$OUT/sync.md"

echo "[analysis] persistence (C cells)"
python3 ANALYSIS/SIM-QUAL-20261031/sq_persistence_analyze.py matrix "${C[@]}" \
    --out-dir ANALYSIS/SIM-QUAL-20261031/persistence --label m2-c2 --quiet
python3 ANALYSIS/SIM-QUAL-20261031/sq_persistence_analyze.py matrix "${B[@]}" \
    --out-dir ANALYSIS/SIM-QUAL-20261031/persistence --label m2-b --quiet

echo "[analysis] done: $OUT"
