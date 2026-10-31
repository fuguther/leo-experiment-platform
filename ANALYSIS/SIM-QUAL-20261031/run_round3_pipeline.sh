#!/usr/bin/env bash
# Incremental Round-3 pipeline: pull every VERIFIED sq5-* cell, then rebuild the
# six-policy matrix analysis.  Safe to rerun while cells are still finishing:
# pull returns already_verified for cells pulled before, and the analyzer is
# given only the cell dirs that exist locally.
set -euo pipefail
WORKTREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WORKTREE"
E="/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1"
OUT="ANALYSIS/SIM-QUAL-20261031/round3-results"
mkdir -p "$OUT"

CELLS=()
for run in "$E"/sq5-*-20261031-01; do
    [ -d "$run" ] || continue
    cell="$(basename "$run")"
    cell="${cell#sq5-}"
    cell="${cell%-20261031-01}"
    [ -d "$run/$cell/arms/now" ] && CELLS+=("$run/$cell")
done
echo "[pipeline] evidence cell dirs: ${#CELLS[@]}"
if [ "${#CELLS[@]}" -eq 0 ]; then
    echo "[pipeline] nothing pulled yet"; exit 0
fi
python3 ANALYSIS/SIM-QUAL-20261031/sq_round3_analyze.py matrix "${CELLS[@]}" \
    --baseline geometry --jobs 4 \
    --json "$OUT/matrix.json" --md "$OUT/matrix.md"
echo "[pipeline] wrote $OUT/matrix.{json,md}"
