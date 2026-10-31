#!/usr/bin/env bash
# Launch counterfactual forced-action forks for one analysed cell.
#
#   RUN_FORK_PREFIX=fk-c- run_forks.sh <RELEASE_ID> <DRIVER_CONFIG_REL> <SAMPLE_JSON>
#
# One run id per sampled decision: <prefix><decision>-20261031-01.  Two forks
# at a time (the VM cgroup allows 24 cores / 64 GiB; 2 full replays is the
# proven ceiling).  The driver compiles the trace from the pinned config and
# replays BOTH branches from t=0; the pairing witness is the decision-log
# prefix fingerprint (see COUNTERFACTUAL-AUDIT.md).
set -euo pipefail
WORKTREE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$WORKTREE"
release_id="$1"; driver_config="$2"; sample="$3"
prefix="${RUN_FORK_PREFIX:?set RUN_FORK_PREFIX (e.g. fk-c-)}"
REMOTE_RUNS="/data/论文/leo-t1-wt/runs"
CONCURRENCY="${CONCURRENCY:-2}"
TIMEOUT_S="${TIMEOUT_S:-5400}"

entries="$(mktemp "${TMPDIR:-/tmp}/fork-entries.XXXXXX")"
trap 'rm -f "$entries"' EXIT
python3 - "$sample" > "$entries" <<'PY'
import json,sys
doc=json.load(open(sys.argv[1]))
for row in doc["decisions"]:
    print(f'{row["decision_id"]}\t{row["fresh"]}\t{row["stratum"]}')
PY

launch() {
    local did="$1" fresh="$2" stratum="$3"
    local run_id="${prefix}${did}-20261031-01"
    echo "[fork] decision=$did forced=$fresh stratum=$stratum run_id=$run_id"
    CODE/scripts/remote/run-release-remote.sh \
        --release-id "$release_id" \
        --run-id "$run_id" \
        --mode diagnostic \
        --config "$driver_config" \
        --timeout-seconds "$TIMEOUT_S" \
        -- python3 CODE/scripts/counterfactual_one_decision.py \
            --config "$driver_config" \
            --decision-id "$did" \
            --forced-action "$fresh" \
            --arm now \
            --root /tmp \
            --out "$REMOTE_RUNS/$run_id/cf-$did.json"
}

running=0
while IFS=$'\t' read -r did fresh stratum; do
    [[ -n "$did" ]] || continue
    launch "$did" "$fresh" "$stratum" &
    running=$((running+1))
    if (( running >= CONCURRENCY )); then
        wait
        running=0
    fi
done < "$entries"
wait
echo "[fork] done"
