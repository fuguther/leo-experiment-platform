#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage:
  scripts/remote/push-remote.sh

Purpose:
  Deploy one clean Git commit to the canonical isolated VM workspace. The source
  tree is staged and hash-verified before canonical source roots are replaced.
  CODE/Results is preserved and is never part of the deployment archive.
EOF
}

if [[ $# -gt 0 ]]; then
    [[ "$1" == "--help" || "$1" == "-h" ]] && { usage; exit 0; }
    die "unknown argument: $1 (dirty deployment overrides are intentionally unsupported)"
fi

load_remote_config

[[ -d "$LOCAL_WORKSPACE_DIR/.git" ]] || die "local workspace is not a Git repository: $LOCAL_WORKSPACE_DIR"
source_commit="$($GIT_BIN -C "$LOCAL_WORKSPACE_DIR" rev-parse HEAD)"
source_branch="$($GIT_BIN -C "$LOCAL_WORKSPACE_DIR" branch --show-current)"
source_status="$($GIT_BIN -C "$LOCAL_WORKSPACE_DIR" status --porcelain --untracked-files=all)"
[[ -z "$source_status" ]] || die "refusing to deploy a dirty workspace; commit every intended source file first"
[[ "$source_commit" =~ ^[0-9a-fA-F]{40}$ && "$source_commit" != "0000000000000000000000000000000000000000" ]] \
    || die "invalid source Git commit: $source_commit"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/leo-deploy.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT
archive="$tmp_dir/workspace.tar.gz"
manifest_summary="$($LOCAL_PYTHON "$SCRIPT_DIR/deployment_guard.py" build \
    --root "$LOCAL_WORKSPACE_DIR" \
    --archive "$archive" \
    --commit "$source_commit" \
    --branch "$source_branch")"

q_workspace="$(printf '%q' "$REMOTE_WORKSPACE_DIR")"
q_runtime="$(printf '%q' "$REMOTE_RUNTIME_DIR_ABS")"
q_python="$(printf '%q' "$REMOTE_PYTHON")"
remote_install=$(cat <<EOF
set -euo pipefail
mkdir -p $q_workspace $q_runtime
staging=\$(mktemp -d $q_runtime/incoming.XXXXXX)
cleanup() { rm -rf "\$staging"; }
trap cleanup EXIT
tar -xzf - -C "\$staging"
$REMOTE_ENV_ACTIVATE
$q_python "\$staging/CODE/scripts/remote/deployment_guard.py" install --staging "\$staging" --workspace $q_workspace
EOF
)

remote_summary="$("$SSH_BIN" "$REMOTE_HOST_ALIAS" "bash -lc $(printf '%q' "$remote_install")" < "$archive")"

echo "source_git_commit=$source_commit"
echo "source_git_branch=$source_branch"
echo "local_manifest=$manifest_summary"
echo "remote_install=$remote_summary"
