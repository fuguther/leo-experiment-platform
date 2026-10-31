#!/usr/bin/env bash
# Generic diagnostic cell runner for the 2026-10-31/11-01 state-time line.
#
# Usage:
#   run_cells.sh launch <RELEASE_ID> <CONTRACT_REL> <RUN_ID_PREFIX> <CELL> [<CELL> ...]
#   run_cells.sh pull   <RELEASE_ID> <RUN_ID_PREFIX> <CELL> [<CELL> ...]
#
# Run id = "<prefix><cell>-20261031-01"; a run id is never reused (the runner
# refuses an existing run directory, which is evidence, not something to fix by
# overwriting).  At most CONCURRENCY cells run at once (VM cgroup: 24 cores /
# 64 GiB; two concurrent load_calibration cells is the proven ceiling).
set -euo pipefail

WORKTREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WORKTREE"
EVIDENCE_ROOT="/Users/lge/Desktop/topic/三端文件与同步治理/evidence/t1"
REMOTE_RUNS="/data/论文/leo-t1-wt/runs"
CONCURRENCY="${CONCURRENCY:-2}"
TIMEOUT_S="${TIMEOUT_S:-3600}"

mode="${1:-}"; shift || true
release_id="${1:-}"; shift || true
third="${1:-}"; shift || true
cells=("$@")

die() { printf '[cells] %s\n' "$*" >&2; exit 2; }
[[ "$mode" == launch || "$mode" == pull ]] || die "mode must be launch|pull"
[[ "$release_id" =~ ^[0-9a-f]{40}-[0-9a-f]{64}$ ]] || die "invalid release id"
[[ "${#cells[@]}" -gt 0 ]] || die "no cells given"

if [[ "$mode" == launch ]]; then
    contract="$third"
    prefix="${RUN_ID_PREFIX:-}"
    [[ -n "$prefix" ]] || die "set RUN_ID_PREFIX (e.g. sq2-)"
    [[ -f "$contract" ]] || die "contract not found: $contract"
    run_id_for() { printf '%s%s-20261031-01' "$prefix" "$1"; }
    launch_one() {
        local cell="$1" run_id
        run_id="$(run_id_for "$cell")"
        echo "[cells] launch cell=$cell run_id=$run_id"
        CODE/scripts/remote/run-release-remote.sh \
            --release-id "$release_id" \
            --run-id "$run_id" \
            --mode diagnostic \
            --config "$contract" \
            --timeout-seconds "$TIMEOUT_S" \
            -- python3 -m CODE.experiment_platform.load_calibration \
                --contract "$contract" \
                --cell "$cell" \
                --out-dir "$REMOTE_RUNS/$run_id/$cell"
    }
    index=0; total="${#cells[@]}"
    while (( index < total )); do
        chunk=("${cells[@]:$index:$CONCURRENCY}")
        for cell in "${chunk[@]}"; do launch_one "$cell" & done
        wait
        index=$((index + CONCURRENCY))
    done
else
    prefix="$third"
    run_id_for() { printf '%s%s-20261031-01' "$prefix" "$1"; }
    for cell in "${cells[@]}"; do
        run_id="$(run_id_for "$cell")"
        echo "[cells] pull cell=$cell run_id=$run_id"
        CODE/scripts/remote/pull-release-results-remote.sh \
            --run-id "$run_id" \
            --release-id "$release_id" \
            --evidence-root "$EVIDENCE_ROOT"
    done
fi
echo "[cells] done ($mode)"
