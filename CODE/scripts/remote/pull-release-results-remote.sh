#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_WORKSPACE="${LOCAL_WORKSPACE_DIR_OVERRIDE:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
REMOTE_HOST="${T1_REMOTE_HOST:-vm}"
REMOTE_ROOT="${T1_REMOTE_ROOT:-/data/论文/leo-t1-wt}"
SSH_BIN="${SSH_BIN:-/usr/bin/ssh}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REMOTE_PYTHON="${T1_REMOTE_PYTHON:-python3}"

die() { printf '[pull-release] %s\n' "$*" >&2; exit 2; }
usage() {
    cat <<'EOF'
Usage:
  pull-release-results-remote.sh --run-id ID --release-id ID --evidence-root ABSOLUTE_PATH

Verifies the VM receipt, streams exactly one T1 run, validates the received
file set and hashes into <evidence-root>/<run-id>, then appends the small
verified record to ANALYSIS/DEPLOYMENT-INDEX.jsonl. Evidence stays outside the
source repository. Choose an evidence root on separately backed-up storage if
independent backup is required.
EOF
}
quote() { printf -v REPLY '%q' "$1"; }

run_id=""; release_id=""; evidence_root=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-id) [[ $# -ge 2 ]] || die "--run-id requires a value"; run_id="$2"; shift 2 ;;
        --release-id) [[ $# -ge 2 ]] || die "--release-id requires a value"; release_id="$2"; shift 2 ;;
        --evidence-root) [[ $# -ge 2 ]] || die "--evidence-root requires an absolute path"; evidence_root="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
[[ "$REMOTE_ROOT" == "/data/论文/leo-t1-wt" ]] || die "T1_REMOTE_ROOT must remain /data/论文/leo-t1-wt"
[[ -x "$SSH_BIN" ]] || die "SSH executable is unavailable: $SSH_BIN"
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die "invalid run ID"
[[ "$release_id" =~ ^[0-9a-f]{40}-[0-9a-f]{64}$ ]] || die "invalid full release ID"
[[ "$evidence_root" == /* ]] || die "evidence root must be an absolute path"

run_dir="$REMOTE_ROOT/runs/$run_id"
helper="$REMOTE_ROOT/releases/$release_id/CODE/scripts/remote/release_protocol.py"
remote_args=("$REMOTE_PYTHON" "$helper" verify-run --run-dir "$run_dir" --run-id "$run_id" --release-id "$release_id")
remote_command=""
for argument in "${remote_args[@]}"; do
    quote "$argument"
    remote_command+="${remote_command:+ }$REPLY"
done
for argument in "&&" "tar" "-czf" "-" "-C" "$run_dir" "."; do
    if [[ "$argument" == "&&" ]]; then
        remote_command+=" &&"
    else
        quote "$argument"
        remote_command+=" $REPLY"
    fi
done

"$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" "$remote_command" |
    "$PYTHON_BIN" "$SCRIPT_DIR/release_protocol.py" pull \
        --evidence-root "$evidence_root" --repo-root "$LOCAL_WORKSPACE" \
        --run-id "$run_id" --release-id "$release_id" \
    || die "pullback failed; any partial stays quarantined and no VERIFIED index row is written"
