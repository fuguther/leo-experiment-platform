#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage:
  scripts/remote/run-remote.sh --config CONFIG --authorization AUTHORIZATION
      [--runtime-kind legacy_gateway|leo_sim_v2]
      [--session SESSION] [--no-monitor] [--bundle] [--bundle-stages STAGES]
      [--cpu-list CPU_LIST] [--exclusive-simulation]

This is only a FORMAL_EXPERIMENT entrypoint and does not accept a shell command.
Runtime dispatch: legacy_gateway -> deployed CODE/run.py;
leo_sim_v2 -> python -m CODE.leo_sim run. Use an audited direct SSH maintenance
session for administration; such commands are not formal runs.
EOF
}

session_name=""
config_path=""
authorization_path=""
no_monitor=0
bundle=0
bundle_stages=""
cpu_list=""
exclusive_simulation=0
runtime_kind="legacy_gateway"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --session) [[ $# -ge 2 ]] || die "--session requires a value"; session_name="$2"; shift 2 ;;
        --config) [[ $# -ge 2 ]] || die "--config requires a value"; config_path="$2"; shift 2 ;;
        --authorization) [[ $# -ge 2 ]] || die "--authorization requires a value"; authorization_path="$2"; shift 2 ;;
        --runtime-kind) [[ $# -ge 2 ]] || die "--runtime-kind requires a value"; runtime_kind="$2"; shift 2 ;;
        --no-monitor) no_monitor=1; shift ;;
        --bundle) bundle=1; shift ;;
        --bundle-stages) [[ $# -ge 2 ]] || die "--bundle-stages requires a value"; bundle_stages="$2"; bundle=1; shift 2 ;;
        --cpu-list) [[ $# -ge 2 ]] || die "--cpu-list requires a value"; cpu_list="$2"; shift 2 ;;
        --exclusive-simulation) exclusive_simulation=1; shift ;;
        --help|-h) usage; exit 0 ;;
        --) die "arbitrary commands are forbidden by the formal runner" ;;
        *) die "unknown argument: $1" ;;
    esac
done

load_remote_config
[[ -n "$config_path" ]] || die "--config is required"
[[ -n "$authorization_path" ]] || die "--authorization is required"
[[ "$runtime_kind" == "legacy_gateway" || "$runtime_kind" == "leo_sim_v2" ]] \
    || die "--runtime-kind must be legacy_gateway or leo_sim_v2"
[[ "$bundle_stages" =~ ^[A-Za-z0-9_,-]*$ ]] || die "--bundle-stages contains unsafe characters"
[[ "$cpu_list" =~ ^[0-9,-]*$ ]] || die "--cpu-list contains unsafe characters"

session_base="${session_name:-${REMOTE_SESSION_PREFIX}}"
[[ "$session_base" =~ ^[A-Za-z0-9_.-]+$ ]] || die "invalid session base"

case "$config_path" in
    "$REMOTE_WORKSPACE_DIR"/EXPERIMENTS/*) remote_config="$config_path" ;;
    /*) die "absolute --config must remain inside the canonical EXPERIMENTS directory" ;;
    EXPERIMENTS/*) [[ "$config_path" != *".."* ]] || die "--config may not contain .."; remote_config="$REMOTE_WORKSPACE_DIR/$config_path" ;;
    *) die "--config must be an absolute canonical path or start with EXPERIMENTS/" ;;
esac
case "$authorization_path" in
    "$REMOTE_WORKSPACE_DIR"/EXPERIMENTS/*) remote_authorization="$authorization_path" ;;
    /*) die "absolute --authorization must remain inside the canonical EXPERIMENTS directory" ;;
    EXPERIMENTS/*) [[ "$authorization_path" != *".."* ]] || die "--authorization may not contain .."; remote_authorization="$REMOTE_WORKSPACE_DIR/$authorization_path" ;;
    *) die "--authorization must be an absolute canonical path or start with EXPERIMENTS/" ;;
esac

case "$config_path" in
    "$REMOTE_WORKSPACE_DIR"/*) local_config="$LOCAL_WORKSPACE_DIR/${config_path#"$REMOTE_WORKSPACE_DIR"/}" ;;
    EXPERIMENTS/*) local_config="$LOCAL_WORKSPACE_DIR/$config_path" ;;
esac
case "$authorization_path" in
    "$REMOTE_WORKSPACE_DIR"/*) local_authorization="$LOCAL_WORKSPACE_DIR/${authorization_path#"$REMOTE_WORKSPACE_DIR"/}" ;;
    EXPERIMENTS/*) local_authorization="$LOCAL_WORKSPACE_DIR/$authorization_path" ;;
esac
[[ -f "$local_config" && ! -L "$local_config" ]] || die "local compiled config is missing or symbolic"
[[ -f "$local_authorization" && ! -L "$local_authorization" ]] || die "local authorization is missing or symbolic"

identity="$($LOCAL_PYTHON - "$local_config" "$local_authorization" <<'PY'
import hashlib
import json
import secrets
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
authorization_path = Path(sys.argv[2])
if config_path.name.endswith(".leo-sim.yaml"):
    # The V2 compiler intentionally emits JSON syntax (a strict YAML subset),
    # so launcher identity does not depend on a local PyYAML installation.
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    run_id = config_path.name.removesuffix(".leo-sim.yaml")
    raw.pop("config_version", None)
    canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
else:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    run_id = str(config.get("provenance", {}).get("run_id", ""))
    canonical = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    config_sha = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
if not run_id or "|" in run_id or "\n" in run_id:
    raise SystemExit("compiled config lacks a safe run id")
authorization_sha = hashlib.sha256(authorization_path.read_bytes()).hexdigest()
print("|".join((secrets.token_hex(16), run_id, config_sha, authorization_sha)))
PY
)"
IFS='|' read -r launch_nonce expected_run_id expected_config_sha expected_authorization_sha <<<"$identity"
[[ "$launch_nonce" =~ ^[a-f0-9]{32}$ ]] || die "could not generate launch nonce"
[[ "$expected_config_sha" =~ ^[a-f0-9]{64}$ && "$expected_authorization_sha" =~ ^[a-f0-9]{64}$ ]] \
    || die "could not bind local config/authorization hashes"
if [[ "$runtime_kind" == "leo_sim_v2" ]]; then
    local_experiment_dir="$(dirname "$local_authorization")"
    (
        cd "$LOCAL_WORKSPACE_DIR"
        "$LOCAL_PYTHON" -m CODE.experiment_platform.v2_serial_gate \
            --root "$LOCAL_WORKSPACE_DIR" \
            --experiment "$local_experiment_dir" \
            --authorization "$local_authorization" \
            --next-run-id "$expected_run_id"
    )
fi
session_name="${session_base}_${launch_nonce:0:12}"
[[ "$session_name" =~ ^[A-Za-z0-9_.-]+$ ]] || die "invalid nonce-bound session name"

status_file="$REMOTE_STATUS_FILE_ABS"
log_file="$REMOTE_LOG_DIR_ABS/${session_name}.log"
runner_args=(
    run
    --session-name "$session_name"
    --status-file "$status_file"
    --log-file "$log_file"
    --workdir "$REMOTE_CODE_DIR"
    --deployment-receipt "$REMOTE_DEPLOYMENT_RECEIPT_ABS"
    --config "$remote_config"
    --authorization "$remote_authorization"
    --launch-nonce "$launch_nonce"
    --expected-run-id "$expected_run_id"
    --expected-config-sha256 "$expected_config_sha"
    --expected-authorization-sha256 "$expected_authorization_sha"
    --runtime-kind "$runtime_kind"
)
[[ -n "$cpu_list" ]] && runner_args+=(--cpu-list "$cpu_list")
prepare_args=(prepare "${runner_args[@]:1}")
fail_args=(fail "${runner_args[@]:1}")
[[ "$no_monitor" -eq 1 ]] && runner_args+=(--no-monitor)
[[ "$bundle" -eq 1 ]] && runner_args+=(--bundle)
[[ -n "$bundle_stages" ]] && runner_args+=(--bundle-stages "$bundle_stages")

q_code_dir="$(printf '%q' "$REMOTE_CODE_DIR")"
q_runtime_dir="$(printf '%q' "$REMOTE_RUNTIME_DIR_ABS")"
q_log_dir="$(printf '%q' "$REMOTE_LOG_DIR_ABS")"
q_receipt="$(printf '%q' "$REMOTE_DEPLOYMENT_RECEIPT_ABS")"
q_session="$(printf '%q' "$session_name")"
q_python="$(printf '%q' "$REMOTE_PYTHON")"
q_runner_args="$(quote_cmd "${runner_args[@]}")"
q_prepare_args="$(quote_cmd "${prepare_args[@]}")"
q_fail_args="$(quote_cmd "${fail_args[@]}")"

tmux_script=$(cat <<EOF
cd $q_code_dir
set -euo pipefail
$REMOTE_ENV_ACTIVATE
$q_python scripts/remote/remote_job.py $q_runner_args
EOF
)
remote_command=$(cat <<EOF
set -euo pipefail
cd $q_code_dir
mkdir -p $q_runtime_dir $q_log_dir
$(if [[ "$exclusive_simulation" -eq 1 ]]; then printf '%s\n' "if pgrep -af '[S]imulationRL.py' >/dev/null; then echo '[remote] another SimulationRL.py process is active' >&2; exit 3; fi"; fi)
tmux has-session -t $q_session >/dev/null 2>&1 && { echo '[remote] session already exists' >&2; exit 2; }
$REMOTE_ENV_ACTIVATE
$q_python scripts/remote/remote_job.py $q_prepare_args
if ! tmux new-session -d -s $q_session bash -lc $(printf '%q' "$tmux_script"); then
  $q_python scripts/remote/remote_job.py $q_fail_args || true
  exit 2
fi
EOF
)
"$SSH_BIN" "$REMOTE_HOST_ALIAS" "bash -lc $(printf '%q' "$remote_command")"

echo "remote_session=$session_name"
echo "launch_nonce=$launch_nonce"
echo "run_id=$expected_run_id"
echo "config_sha256=$expected_config_sha"
echo "authorization_sha256=$expected_authorization_sha"
echo "remote_status_file=$status_file"
echo "remote_log_file=$log_file"
echo "execution_class=FORMAL_EXPERIMENT"
