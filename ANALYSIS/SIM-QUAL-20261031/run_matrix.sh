#!/usr/bin/env bash
# Launch / collect the frozen policy matrix on T1.
#
# Usage:
#   run_matrix.sh launch <RELEASE_ID> <CELL> [<CELL> ...]   # 2 concurrent runs
#   run_matrix.sh pull   <RELEASE_ID> <CELL> [<CELL> ...]
#
# Run ids are frozen by the protocol: sq-<cell>-20261031-01.  A run id is never
# reused; if a cell already has a run directory the runner refuses and this
# script stops (the failure is evidence, not something to overwrite).
set -euo pipefail

WORKTREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WORKTREE"
CONTRACT="EXPERIMENTS/contracts/sq-policy-matrix-20261031.json"
EVIDENCE_ROOT="/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1"
REMOTE_RUNS="/data/论文/leo-t1-wt/runs"
CONCURRENCY=2
TIMEOUT_S=1800

mode="${1:-}"; shift || true
release_id="${1:-}"; shift || true
cells=("$@")

die() { printf '[matrix] %s\n' "$*" >&2; exit 2; }
[[ "$mode" == launch || "$mode" == pull ]] || die "mode must be launch or pull"
[[ "$release_id" =~ ^[0-9a-f]{40}-[0-9a-f]{64}$ ]] || die "invalid release id"
[[ "${#cells[@]}" -gt 0 ]] || die "no cells given"

run_id_for() { printf 'sq-%s-20261031-01' "$1"; }

launch_one() {
    local cell="$1" run_id
    run_id="$(run_id_for "$cell")"
    echo "[matrix] launch cell=$cell run_id=$run_id"
    CODE/scripts/remote/run-release-remote.sh \
        --release-id "$release_id" \
        --run-id "$run_id" \
        --mode diagnostic \
        --config "$CONTRACT" \
        --timeout-seconds "$TIMEOUT_S" \
        -- python3 -m CODE.experiment_platform.load_calibration \
            --contract "$CONTRACT" \
            --cell "$cell" \
            --out-dir "$REMOTE_RUNS/$run_id/$cell"
}

pull_one() {
    local cell="$1" run_id
    run_id="$(run_id_for "$cell")"
    echo "[matrix] pull cell=$cell run_id=$run_id"
    CODE/scripts/remote/pull-release-results-remote.sh \
        --run-id "$run_id" \
        --release-id "$release_id" \
        --evidence-root "$EVIDENCE_ROOT"
}

if [[ "$mode" == launch ]]; then
    # macOS ships bash 3.2 (no `wait -n`): launch at most CONCURRENCY runs and
    # wait for the whole chunk, which keeps the VM below its 2-run ceiling.
    index=0
    total="${#cells[@]}"
    while (( index < total )); do
        chunk=("${cells[@]:$index:$CONCURRENCY}")
        for cell in "${chunk[@]}"; do
            launch_one "$cell" &
        done
        wait
        index=$((index + CONCURRENCY))
    done
else
    for cell in "${cells[@]}"; do
        pull_one "$cell"
    done
fi
echo "[matrix] done ($mode)"
