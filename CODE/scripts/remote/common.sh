#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_WORKSPACE_DIR="${LOCAL_WORKSPACE_DIR_OVERRIDE:-$(cd "$SCRIPT_DIR/../../.." && pwd)}"
LOCAL_CODE_DIR="$LOCAL_WORKSPACE_DIR/CODE"
CONFIG_FILE="${REMOTE_CONFIG_FILE:-$SCRIPT_DIR/remote.env}"
SSH_BIN="${SSH_BIN:-/usr/bin/ssh}"
RSYNC_BIN="${RSYNC_BIN:-/usr/bin/rsync}"
TAR_BIN="${TAR_BIN:-/usr/bin/tar}"
GIT_BIN="${GIT_BIN:-/usr/bin/git}"
LOCAL_PYTHON="${LOCAL_PYTHON:-python3}"
CANONICAL_REMOTE_WORKSPACE_DIR="/data/论文/leo-direct-sim"

die() {
    echo "[remote] $*" >&2
    exit 1
}

load_remote_config() {
    if [[ ! -f "$CONFIG_FILE" ]]; then
        die "missing config: $CONFIG_FILE (copy $SCRIPT_DIR/remote.env.template to remote.env and fill the placeholders)"
    fi

    # shellcheck disable=SC1090
    source "$CONFIG_FILE"

    : "${REMOTE_HOST_ALIAS:?REMOTE_HOST_ALIAS is required}"
    if [[ -n "${REMOTE_PROJECT_DIR:-}" && -z "${REMOTE_WORKSPACE_DIR:-}" ]]; then
        die "REMOTE_PROJECT_DIR is obsolete; set REMOTE_WORKSPACE_DIR to the new isolated workspace root"
    fi
    : "${REMOTE_WORKSPACE_DIR:?REMOTE_WORKSPACE_DIR is required}"
    : "${REMOTE_ENV_ACTIVATE:?REMOTE_ENV_ACTIVATE is required}"

    [[ "$REMOTE_WORKSPACE_DIR" == "$CANONICAL_REMOTE_WORKSPACE_DIR" ]] \
        || die "REMOTE_WORKSPACE_DIR must equal the canonical isolated root: $CANONICAL_REMOTE_WORKSPACE_DIR"
    REMOTE_CODE_DIR="${REMOTE_CODE_DIR:-$REMOTE_WORKSPACE_DIR/CODE}"
    [[ "$REMOTE_CODE_DIR" == /* ]] || die "REMOTE_CODE_DIR must be an absolute path"
    [[ "$REMOTE_CODE_DIR" == "$REMOTE_WORKSPACE_DIR"/CODE ]] \
        || die "REMOTE_CODE_DIR must be the CODE child of REMOTE_WORKSPACE_DIR"

    REMOTE_SESSION_PREFIX="${REMOTE_SESSION_PREFIX:-leo}"
    REMOTE_RUNTIME_DIR="${REMOTE_RUNTIME_DIR:-.remote_runtime}"
    REMOTE_STATUS_FILE="${REMOTE_STATUS_FILE:-$REMOTE_RUNTIME_DIR/current_status.json}"
    REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-Results/_overnight_logs}"
    REMOTE_PYTHON="${REMOTE_PYTHON:-python3}"
    [[ "$REMOTE_RUNTIME_DIR" == ".remote_runtime" ]] \
        || die "REMOTE_RUNTIME_DIR must remain .remote_runtime under the new workspace root"
    [[ "$REMOTE_STATUS_FILE" == ".remote_runtime/"* && "$REMOTE_STATUS_FILE" != *".."* ]] \
        || die "REMOTE_STATUS_FILE must remain inside workspace/.remote_runtime"
    [[ "$REMOTE_LOG_DIR" == "Results/"* && "$REMOTE_LOG_DIR" != *".."* ]] \
        || die "REMOTE_LOG_DIR must remain inside workspace/CODE/Results"

    REMOTE_RESULTS_DIR="$REMOTE_CODE_DIR/Results"
    REMOTE_RUNTIME_DIR_ABS="$(resolve_remote_workspace_path "$REMOTE_RUNTIME_DIR")"
    REMOTE_STATUS_FILE_ABS="$(resolve_remote_workspace_path "$REMOTE_STATUS_FILE")"
    REMOTE_LOG_DIR_ABS="$(resolve_remote_code_path "$REMOTE_LOG_DIR")"
    REMOTE_DEPLOYMENT_RECEIPT_ABS="$REMOTE_RUNTIME_DIR_ABS/deployment.json"
    [[ "$REMOTE_DEPLOYMENT_RECEIPT_ABS" == "$CANONICAL_REMOTE_WORKSPACE_DIR/.remote_runtime/deployment.json" ]] \
        || die "deployment receipt path is not canonical"
}

resolve_remote_code_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$REMOTE_CODE_DIR" "$1" ;;
    esac
}

resolve_remote_workspace_path() {
    case "$1" in
        /*) printf '%s\n' "$1" ;;
        *) printf '%s/%s\n' "$REMOTE_WORKSPACE_DIR" "$1" ;;
    esac
}

quote_cmd() {
    local out
    printf -v out '%q ' "$@"
    printf '%s' "${out% }"
}

remote_python_stdin() {
    local python_cmd="${1:-$REMOTE_PYTHON}"
    shift || true
    local remote_cmd
    remote_cmd="$(printf '%q' "$python_cmd")"
    if [[ $# -gt 0 ]]; then
        local arg
        for arg in "$@"; do
            remote_cmd+=" $(printf '%q' "$arg")"
        done
    fi
    "$SSH_BIN" "$REMOTE_HOST_ALIAS" "bash -lc $(printf '%q' "$remote_cmd")"
}
