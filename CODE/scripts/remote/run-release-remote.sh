#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE_HOST="${T1_REMOTE_HOST:-vm}"
REMOTE_ROOT="${T1_REMOTE_ROOT:-/data/论文/leo-t1-wt}"
SSH_BIN="${SSH_BIN:-/usr/bin/ssh}"
REMOTE_RUN_PYTHON="${T1_REMOTE_RUN_PYTHON:-/data/liguang13/conda-envs/leo-i39/bin/python}"

die() { printf '[run-release] %s\n' "$*" >&2; exit 2; }
usage() {
    cat <<'EOF'
Usage:
  run-release-remote.sh --release-id ID --run-id ID --mode diagnostic|development \
      [--config RELEASE_RELATIVE_PATH] [--input NAME=VM_PATH]... [--seed N] \
      [--resource-group NAME] [--timeout-seconds N] -- python3 ARGS...

The runner accepts an entrypoint committed under CODE/ in the selected release.
Formal execution remains on the existing authorized runner; this entrypoint
cannot select formal mode. T1_REMOTE_RUN_PYTHON can override the established
T1 conda interpreter when the VM environment changes.
EOF
}
quote() { printf -v REPLY '%q' "$1"; }
remote() {
    local command="" quoted argument
    for argument in "$@"; do
        quote "$argument"
        quoted="$REPLY"
        command+="${command:+ }$quoted"
    done
    "$SSH_BIN" -o BatchMode=yes "$REMOTE_HOST" "$command"
}

[[ "$REMOTE_ROOT" == "/data/论文/leo-t1-wt" ]] || die "T1_REMOTE_ROOT must remain /data/论文/leo-t1-wt"
[[ -x "$SSH_BIN" ]] || die "SSH executable is unavailable: $SSH_BIN"
release_id=""; run_id=""; mode=""; config=""; seed=""; resource_group=""; timeout_seconds="1800"
inputs=(); argv=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --release-id) [[ $# -ge 2 ]] || die "--release-id requires a value"; release_id="$2"; shift 2 ;;
        --run-id) [[ $# -ge 2 ]] || die "--run-id requires a value"; run_id="$2"; shift 2 ;;
        --mode) [[ $# -ge 2 ]] || die "--mode requires a value"; mode="$2"; shift 2 ;;
        --config) [[ $# -ge 2 ]] || die "--config requires a path"; config="$2"; shift 2 ;;
        --input) [[ $# -ge 2 ]] || die "--input requires NAME=PATH"; inputs+=("$2"); shift 2 ;;
        --seed) [[ $# -ge 2 ]] || die "--seed requires an integer"; seed="$2"; shift 2 ;;
        --resource-group) [[ $# -ge 2 ]] || die "--resource-group requires a name"; resource_group="$2"; shift 2 ;;
        --timeout-seconds) [[ $# -ge 2 ]] || die "--timeout-seconds requires an integer"; timeout_seconds="$2"; shift 2 ;;
        --) shift; argv=("$@"); break ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ "$release_id" =~ ^[0-9a-f]{40}-[0-9a-f]{64}$ ]] || die "invalid full release ID"
[[ "$run_id" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ ]] || die "invalid run ID"
[[ "$mode" == diagnostic || "$mode" == development ]] || die "mode must be diagnostic or development; formal is not accepted here"
[[ "${#argv[@]}" -gt 0 && ( "$(basename "${argv[0]}")" == python || "$(basename "${argv[0]}")" == python3 ) ]] \
    || die "command must begin with python/python3 and a CODE script or CODE module in the release"
[[ "$timeout_seconds" =~ ^[0-9]+$ ]] || die "timeout must be an integer"
[[ -z "$seed" || "$seed" =~ ^-?[0-9]+$ ]] || die "seed must be an integer"
if [[ -n "$config" ]]; then
    [[ "$config" != /* && "/$config/" != *"/../"* && "$config" != .. && "$config" != ../* ]] \
        || die "config must be a safe path relative to the immutable release"
fi
for input in "${inputs[@]-}"; do
    [[ -n "$input" ]] || continue
    [[ "$input" == *=* ]] || die "each input must use NAME=VM_PATH"
done

remote_argv=("$REMOTE_RUN_PYTHON" "$REMOTE_ROOT/releases/$release_id/CODE/scripts/remote/release_protocol.py"
    run --release-id "$release_id" --run-id "$run_id"
    --release-root "$REMOTE_ROOT/releases" --runs-root "$REMOTE_ROOT/runs" --mode "$mode"
    --timeout-seconds "$timeout_seconds")
[[ -z "$config" ]] || remote_argv+=(--config "$config")
[[ -z "$seed" ]] || remote_argv+=(--seed "$seed")
[[ -z "$resource_group" ]] || remote_argv+=(--resource-group "$resource_group")
for input in "${inputs[@]-}"; do
    [[ -n "$input" ]] || continue
    remote_argv+=(--input "$input")
done
remote_argv+=(-- "${argv[@]}")
remote "${remote_argv[@]}" || die "T1 diagnostic/development run failed; inspect its isolated run receipt and logs"
