#!/usr/bin/env bash
# VM acceptance round trip for the two minimal platform-chain cases.
#
# Run from the FROZEN checkout (clean tree).  Deploys that commit, runs the two
# cells through the formal launcher, pulls both result directories back and
# verifies them locally.  It produces no performance measurement and is not a
# research experiment.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
R="$REPO_ROOT/CODE/scripts/remote"
EXP_ID="EXP-LEO-V2-ACCEPT-R1"
EXP_DIR="EXPERIMENTS/$EXP_ID"
RESULTS="CODE/Results"
WITNESS_ROOT="$RESULTS/_external_launch_witness"
LOG_DIR="/tmp/acc-r1-logs"
mkdir -p "$LOG_DIR"

skip_deploy=0
only=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-deploy) skip_deploy=1; shift ;;
        --only) only="$2"; shift 2 ;;
        *) echo "unknown arg: $1" >&2; exit 2 ;;
    esac
done

cd "$REPO_ROOT"

if [[ $skip_deploy -eq 0 ]]; then
    echo "=== STEP 1: deploy the frozen commit ==="
    bash "$R/push-remote.sh" 2>&1 | tail -20 || { echo "DEPLOY FAILED"; exit 3; }
fi

run_cell() {
    local arm="$1"
    local run_id="$EXP_ID-$arm-s7"
    local config="$EXP_DIR/resolved/$run_id.leo-sim.yaml"
    local auth="$EXP_DIR/authorization.json"
    echo "=== STEP 2 ($arm): launch $run_id ==="
    local out
    out="$(bash "$R/run-remote.sh" --runtime-kind leo_sim_v2 --config "$config" --authorization "$auth" 2>&1)"
    echo "$out" | tail -12
    local nonce
    nonce="$(printf '%s\n' "$out" | sed -n "s/^launch_nonce=//p" | tail -1)"
    [[ "$nonce" =~ ^[a-f0-9]{32}$ ]] || { echo "NO LAUNCH NONCE for $arm"; return 4; }
    echo "launch_nonce=$nonce"

    echo "=== STEP 3 ($arm): wait for the launch to settle ==="
    local waited=0 state
    while (( waited < 600 )); do
        bash "$R/status-remote.sh" --launch-nonce "$nonce" > "$LOG_DIR/status-$arm.json" 2>&1
        state="$(python3 - "$LOG_DIR/status-$arm.json" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("status", ""))
except Exception:
    print("")
PY
)"
        if [[ "$state" == "success" || "$state" == "failed" ]]; then
            echo "status=$state after ${waited}s"
            break
        fi
        sleep 10
        waited=$((waited + 10))
    done
    if (( waited >= 600 )); then echo "TIMEOUT waiting for $arm"; return 5; fi

    echo "=== STEP 4 ($arm): pull the result directory ==="
    bash "$R/pull-results-remote.sh" --run "$run_id" > "$LOG_DIR/pull-$arm.tar.gz" 2>"$LOG_DIR/pull-$arm.err" \
        || { echo "PULL FAILED"; tail -5 "$LOG_DIR/pull-$arm.err"; return 6; }
    tar -xzf "$LOG_DIR/pull-$arm.tar.gz" -C "$REPO_ROOT" 2>/dev/null || true
    ls "$RESULTS/$run_id" 2>&1 | head -20

    echo "=== STEP 5 ($arm): local pull-back acceptance ==="
    python3 -m CODE.leo_sim receipt verify-pulled \
        --run-dir "$RESULTS/$run_id" --expect formal \
        --witness-root "$WITNESS_ROOT" --out "$LOG_DIR/verify-$arm.json" \
        > "$LOG_DIR/verify-$arm.stdout" 2>&1
    echo "verify exit=$?"
    python3 - "$LOG_DIR/verify-$arm.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(json.dumps({"ok": d["ok"], "claimable": d["claimable"],
                  "failed": [c["id"] for c in d["checks"] if not c["ok"]],
                  "errors": d["errors"][:4],
                  "not_checked": d["formal_eligibility"]["not_checked_here"]},
                 ensure_ascii=False, indent=2))
PY
}

arms=(control treatment)
[[ -n "$only" ]] && arms=("$only")
rc=0
for arm in "${arms[@]}"; do
    run_cell "$arm" || rc=$?
done

echo "=== STEP 6: existing formal eligibility analysis entry ==="
python3 -m CODE.experiment_platform.v2_analysis \
    --root "$REPO_ROOT" --experiment "$EXP_DIR" \
    --authorization "$EXP_DIR/authorization.json" \
    --results-root "$RESULTS" --external-witness-root "$WITNESS_ROOT" \
    --out "$LOG_DIR/analysis-manifest.json" 2>&1 | tail -12
echo "v2_analysis exit=$?"
echo "=== DONE rc=$rc ==="
exit $rc
