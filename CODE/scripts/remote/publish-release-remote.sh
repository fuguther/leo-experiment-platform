#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_WORKSPACE="${LOCAL_WORKSPACE_DIR_OVERRIDE:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
REMOTE_HOST="${T1_REMOTE_HOST:-vm}"
REMOTE_ROOT="${T1_REMOTE_ROOT:-/data/论文/leo-t1-wt}"
SSH_BIN="${SSH_BIN:-/usr/bin/ssh}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
REMOTE_PYTHON="${T1_REMOTE_PYTHON:-python3}"
EXPECTED_REPOSITORY="fuguther/leo-experiment-platform"

die() { printf '[publish-release] %s\n' "$*" >&2; exit 2; }

usage() {
    cat <<'EOF'
Usage:
  scripts/remote/publish-release-remote.sh --commit FULL_40_CHARACTER_SHA

Builds a deterministic package from a clean task worktree's exact Git object,
verifies the file manifest, then atomically publishes it under the T1-only VM
root /data/论文/leo-t1-wt/releases/<release-id>. It never writes to the formal
deployment root. If no advertised origin branch points at the commit, the
release is marked remote_backup_pending and remains development/diagnostic only.
EOF
}

commit=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --commit) [[ $# -ge 2 ]] || die "--commit requires a full SHA"; commit="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || die "--commit must be a lowercase full 40-character SHA"
[[ "$REMOTE_ROOT" == "/data/论文/leo-t1-wt" ]] || die "T1_REMOTE_ROOT must remain /data/论文/leo-t1-wt"
[[ -x "$SSH_BIN" ]] || die "SSH executable is unavailable: $SSH_BIN"
[[ -f "$LOCAL_WORKSPACE/.git" || -d "$LOCAL_WORKSPACE/.git" ]] || die "local T1 worktree is not a Git checkout"

bundle="$(mktemp -d "${TMPDIR:-/tmp}/leo-release.XXXXXX")"
cleanup_local() { rm -rf "$bundle"; }
trap cleanup_local EXIT

build_json="$("$PYTHON_BIN" "$SCRIPT_DIR/release_protocol.py" build \
    --repo "$LOCAL_WORKSPACE" --commit "$commit" --out "$bundle" \
    --expected-repository "$EXPECTED_REPOSITORY")" || die "exact-commit build or source audit failed"
release_id="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["release_id"])' <<<"$build_json")"
[[ "$release_id" =~ ^[0-9a-f]{40}-[0-9a-f]{64}$ ]] || die "release builder returned an invalid release ID"
backup_status="$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["remote_backup"]["status"])' <<<"$build_json")"
remote_bootstrap_dir="$REMOTE_ROOT/releases/.bootstrap/$release_id"

remote_quote() {
    printf -v REPLY '%q' "$1"
}

remote() {
    local command="" quoted argument
    for argument in "$@"; do
        remote_quote "$argument"
        quoted="$REPLY"
        command+="${command:+ }$quoted"
    done
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" "$command"
}

remote_write() {
    local path="$1"
    remote_quote "$path"
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" "cat > $REPLY"
}

# Before the commit-bound helper is available, a small guard creates a unique
# bootstrap directory beneath the isolated T1 root and rejects symlink paths.
ensure_code='from pathlib import Path; import sys; root=Path("/data/论文/leo-t1-wt"); assert all(p.is_dir() and not p.is_symlink() for p in (root, *root.parents)), "T1 root path missing or unsafe"; releases=root/"releases"; releases.mkdir(exist_ok=True); assert releases.is_dir() and not releases.is_symlink(), "releases path unsafe"; parent=releases/".bootstrap"; parent.mkdir(mode=0o700, exist_ok=True); assert parent.is_dir() and not parent.is_symlink(), "bootstrap path unsafe"; target=parent/sys.argv[1]; target.mkdir(mode=0o700); assert not target.is_symlink()'
remote "$REMOTE_PYTHON" -c "$ensure_code" "$release_id" \
    || die "could not create isolated T1 bootstrap directory"
remote_write "$remote_bootstrap_dir/release_protocol.py" < "$SCRIPT_DIR/release_protocol.py"
remote_write "$remote_bootstrap_dir/deployment_guard.py" < "$SCRIPT_DIR/deployment_guard.py"
remote "$REMOTE_PYTHON" "$remote_bootstrap_dir/release_protocol.py" prepare-incoming --release-id "$release_id" \
    || die "VM refused the unique release incoming attempt"

incoming="$REMOTE_ROOT/releases/incoming/$release_id.partial"
remote_write "$incoming/release.tar" < "$bundle/release.tar"
remote_write "$incoming/release.json" < "$bundle/release.json"
remote_write "$incoming/release_protocol.py" < "$SCRIPT_DIR/release_protocol.py"
remote_write "$incoming/deployment_guard.py" < "$SCRIPT_DIR/deployment_guard.py"

install_json="$(remote "$REMOTE_PYTHON" "$incoming/release_protocol.py" install \
    --bundle "$incoming" --releases-root "$REMOTE_ROOT/releases" \
    --bootstrap "$incoming/release_protocol.py")" \
    || die "VM verification or atomic release publish failed; incoming partial retained"
cleanup_json="$(remote "$REMOTE_PYTHON" "$incoming/release_protocol.py" \
    cleanup-incoming --release-id "$release_id")" \
    || die "release is published but verified incoming cleanup failed"

printf 'release_id=%s\n' "$release_id"
printf 'source_git_commit=%s\n' "$commit"
printf 'remote_backup_status=%s\n' "$backup_status"
printf 'remote_install=%s\n' "$install_json"
printf 'remote_incoming=%s\n' "$cleanup_json"
