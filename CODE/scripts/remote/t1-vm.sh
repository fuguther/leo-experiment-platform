#!/usr/bin/env bash
# T1 VM experiment runner.
#
# HARD RULE (AGENTS.md): experiments run ONLY on the VM.  This script is the
# only sanctioned path from this checkout to the VM, and it writes ONLY inside
# the T1 isolated root.  The user's live deployment at
# /data/论文/leo-direct-sim is never touched by this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_WORKSPACE="$(cd "$SCRIPT_DIR/../../.." && pwd)"
LOCAL_CODE="$LOCAL_WORKSPACE/CODE"
LOCAL_OUT="$LOCAL_WORKSPACE/out/vm"

REMOTE_HOST="${T1_REMOTE_HOST:-vm}"
REMOTE_ROOT="${T1_REMOTE_ROOT:-/data/论文/leo-t1-wt}"
REMOTE_CODE="$REMOTE_ROOT/CODE"
REMOTE_RESULTS="$REMOTE_ROOT/Results"
REMOTE_ENV_ACTIVATE='source /opt/anaconda3/bin/activate /data/liguang13/conda-envs/leo-i39'
SSH_BIN="${SSH_BIN:-/usr/bin/ssh}"
TAR_BIN="${TAR_BIN:-/usr/bin/tar}"

die() { echo "[t1-vm] $*" >&2; exit 1; }

[[ "$REMOTE_ROOT" == "/data/论文/leo-t1-wt" ]] \
    || die "REMOTE_ROOT must stay the isolated T1 root (got $REMOTE_ROOT)"
[[ "$REMOTE_ROOT" != "/data/论文/leo-direct-sim" ]] \
    || die "refusing to write into the live deployment"

usage() {
    cat <<'EOF'
Usage:
  t1-vm.sh status
  t1-vm.sh sync
  t1-vm.sh run <run-id> <command...>      # runs inside $REMOTE_CODE
  t1-vm.sh pull <run-id>
  t1-vm.sh experiment [<run-id>]          # sync + compile/validate/acceptance/dev + pull

Environment overrides: T1_REMOTE_HOST, T1_REMOTE_ROOT (must stay isolated)
EOF
}

require_clean() {
    local head dirty
    head="$(cd "$LOCAL_WORKSPACE" && git rev-parse HEAD)"
    dirty="$(cd "$LOCAL_WORKSPACE" && git status --short)"
    echo "[t1-vm] local HEAD $head" >&2
    if [[ -n "$dirty" ]]; then
        echo "[t1-vm] WARNING: worktree is dirty:" >&2
        echo "$dirty" >&2
    fi
    echo "$head"
}

cmd_status() {
    local remote
    remote="hostname; echo ROOT='$REMOTE_ROOT'; if [ -d '$REMOTE_ROOT' ]; then ls -la '$REMOTE_ROOT' | head -12; else echo NO_T1_ROOT; fi; echo ---launch---; cat '$REMOTE_ROOT/launch.json' 2>/dev/null || true; echo ---results---; ls '$REMOTE_RESULTS' 2>/dev/null | tail -10 || true"
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" "bash -lc $(printf '%q' "$remote")"
}

cmd_sync() {
    local head
    head="$(require_clean)"
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" \
        "mkdir -p '$REMOTE_CODE' '$REMOTE_RESULTS'"
    # the VM has no rsync: stream a tar over ssh (same method as the repo own
    # push-remote.sh), excluding results, caches and the private config
    COPYFILE_DISABLE=1 "$TAR_BIN" czf - --no-mac-metadata -C "$LOCAL_WORKSPACE" \
        --exclude 'Results' --exclude '__pycache__' --exclude '.pytest_cache' \
        --exclude '._*' --exclude '.DS_Store' --exclude '*.pyc' --exclude '*.log' \
        --exclude 'CODE/scripts/remote/remote.env' \
        CODE | "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" \
        "tar xzf - -C '$REMOTE_ROOT' 2>&1 | grep -v 'LIBARCHIVE.xattr' || true"
    python3 - "$LOCAL_WORKSPACE" "$head" > "$LOCAL_WORKSPACE/.t1-launch.json" <<'PYEOF'
import json, subprocess, sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / "CODE" / ".."))
sys.path.insert(0, str(Path(sys.argv[1])))
from CODE.experiment_platform import artifact_identity
root = Path(sys.argv[1])
dirty = bool(subprocess.run(["git", "-C", str(root), "status", "--short"],
                            capture_output=True, text=True).stdout.strip())
print(json.dumps({
    "schema": "t1-vm-launch/v1",
    "head": sys.argv[2],
    "dirty": dirty,
    "local_workspace": str(root),
    "synced_at": datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
    "chain_files": list(artifact_identity.execution_chain_paths()),
}, indent=2))
PYEOF
    COPYFILE_DISABLE=1 "$TAR_BIN" czf - --no-mac-metadata \
        -C "$LOCAL_WORKSPACE" .t1-launch.json | "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" \
        "tar xzf - -C '$REMOTE_ROOT' 2>/dev/null; mv '$REMOTE_ROOT/.t1-launch.json' '$REMOTE_ROOT/launch.json'"
    rm -f "$LOCAL_WORKSPACE/.t1-launch.json"
    # a tar stream cannot delete: AFTER the manifest exists, prune the deployed
    # tree to exactly the file set this commit declares, so a stray file (e.g.
    # a macOS ._ sidecar) can never change the execution identity
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" \
        "cd '$REMOTE_ROOT' && python3 CODE/scripts/remote/prune_remote_chain.py"
    echo "[t1-vm] synced $head -> $REMOTE_HOST:$REMOTE_CODE"
}

cmd_run() {
    local run_id="$1"; shift
    [[ $# -gt 0 ]] || die "run needs a command"
    local quoted=""
    local arg
    for arg in "$@"; do
        quoted="$quoted $(printf '%q' "$arg")"
    done
    # the module root is the WORKSPACE root (CODE is a package inside it), not
    # CODE itself: all drivers are invoked as python3 -m CODE.<...>
    local remote="cd '$REMOTE_ROOT' && $REMOTE_ENV_ACTIVATE && mkdir -p '$REMOTE_RESULTS/$run_id' && $quoted 2>&1 | tee '$REMOTE_RESULTS/$run_id/run.log'; exit \${PIPESTATUS[0]}"
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" "bash -lc $(printf '%q' "$remote")"
}

cmd_pull() {
    local run_id="$1"
    mkdir -p "$LOCAL_OUT/$run_id"
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" \
        "cd '$REMOTE_RESULTS/$run_id' && tar czf - ." | \
        "$TAR_BIN" xzf - -C "$LOCAL_OUT/$run_id"
    echo "[t1-vm] pulled $REMOTE_HOST:$REMOTE_RESULTS/$run_id -> $LOCAL_OUT/$run_id"
}

cmd_experiment() {
    local run_id="${1:-t1-\$(date -u +%Y%m%dT%H%M%SZ)}"
    cmd_sync
    local rc=0
    cmd_run "$run_id" python3 -m CODE.experiment_platform.t1_suite compile \
        --contract CODE/work/WP-T1-COMPLETE/contract.yaml \
        --out "Results/$run_id/compiled" || rc=$?
    if [[ $rc -eq 0 ]]; then
        cmd_run "$run_id" python3 -m CODE.experiment_platform.t1_suite validate \
            --bundle "Results/$run_id/compiled" || rc=$?
    fi
    if [[ $rc -eq 0 ]]; then
        cmd_run "$run_id" python3 -m CODE.experiment_platform.t1_suite run \
            --bundle "Results/$run_id/compiled" --tier acceptance \
            --out "Results/$run_id/acceptance" || rc=$?
    fi
    if [[ $rc -eq 0 ]]; then
        cmd_run "$run_id" python3 -m CODE.experiment_platform.t1_suite run \
            --bundle "Results/$run_id/compiled" --tier dev \
            --out "Results/$run_id/dev" || rc=$?
    fi
    if [[ $rc -eq 0 ]]; then
        cmd_run "$run_id" python3 -m CODE.experiment_platform.t1_suite report \
            --run-dir "Results/$run_id/acceptance" || rc=$?
        cmd_run "$run_id" python3 -m CODE.experiment_platform.t1_suite report \
            --run-dir "Results/$run_id/dev" || rc=$?
    fi
    cmd_pull "$run_id" || true
    echo "[t1-vm] experiment $run_id finished with rc=$rc (pulled to $LOCAL_OUT/$run_id)"
    return "$rc"
}

case "${1:-}" in
    status) cmd_status ;;
    sync) cmd_sync ;;
    run) shift; [[ $# -ge 2 ]] || die "run <run-id> <command...>"; cmd_run "$@" ;;
    pull) shift; [[ $# -eq 1 ]] || die "pull <run-id>"; cmd_pull "$1" ;;
    experiment) shift; cmd_experiment "$@" ;;
    *) usage; exit 2 ;;
esac
