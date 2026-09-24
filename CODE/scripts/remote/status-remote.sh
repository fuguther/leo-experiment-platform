#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage:
  scripts/remote/status-remote.sh [--session SESSION] [--launch-nonce NONCE]
EOF
}

session_override=""
launch_nonce_override=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --session)
            [[ $# -ge 2 ]] || die "--session requires a value"
            session_override="$2"
            shift 2
            ;;
        --launch-nonce)
            [[ $# -ge 2 ]] || die "--launch-nonce requires a value"
            launch_nonce_override="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

load_remote_config
[[ -z "$session_override" || "$session_override" =~ ^[A-Za-z0-9_.-]+$ ]] || die "invalid session name"
[[ -z "$launch_nonce_override" || "$launch_nonce_override" =~ ^[a-f0-9]{32}$ ]] || die "invalid launch nonce"

remote_python_stdin "$REMOTE_PYTHON" - "$REMOTE_STATUS_FILE_ABS" "$session_override" "$launch_nonce_override" "$REMOTE_WORKSPACE_DIR" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
session_override = sys.argv[2] or ""
launch_nonce_override = sys.argv[3] or ""
workspace = Path(os.path.abspath(sys.argv[4]))
expected_workspace = Path("/data/论文/leo-direct-sim")
if workspace != expected_workspace or workspace.is_symlink() or workspace.resolve(strict=True) != workspace:
    raise SystemExit("canonical workspace is missing, linked, or resolves elsewhere")
results = workspace / "CODE" / "Results"
if results.is_symlink() or not results.is_dir() or results.resolve(strict=True) != results:
    raise SystemExit("canonical Results path is missing, linked, or resolves elsewhere")
logs = (results / "_overnight_logs").resolve(strict=False)
expected_status = (workspace / ".remote_runtime" / "current_status.json").resolve(strict=False)
if status_path.resolve(strict=False) != expected_status:
    raise SystemExit("status path is outside the canonical runtime directory")
if launch_nonce_override:
    launches = expected_status.parent / "launches"
    if launches.is_symlink() or not launches.is_dir() or launches.resolve(strict=True) != launches:
        raise SystemExit("canonical launch receipt directory is missing or unsafe")
    status_path = launches / f"{launch_nonce_override}.json"
    if status_path.is_symlink() or not status_path.is_file():
        raise SystemExit("nonce-bound launch receipt is missing or unsafe")
payload = {}
if status_path.is_file() and not status_path.is_symlink():
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {"status": "invalid_json", "status_file": str(status_path)}

payload_session_name = payload.get("session_name", "")
payload_launch_nonce = payload.get("launch_nonce", "")
if session_override and session_override != payload_session_name:
    raise SystemExit(
        f"status payload belongs to {payload_session_name!r}, not requested session {session_override!r}"
    )
if launch_nonce_override and launch_nonce_override != payload_launch_nonce:
    raise SystemExit(
        f"status payload launch nonce {payload_launch_nonce!r} does not match requested nonce"
    )
session_name = payload_session_name or session_override
tmux_state = "unknown"
if session_name:
    rc = subprocess.run(
        ["tmux", "has-session", "-t", session_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode
    tmux_state = "running" if rc == 0 else "missing"

def safe_path(raw, root, *, direct_child=False):
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_symlink():
        return None
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    if direct_child and (resolved.parent != root or resolved.name.startswith("_")):
        return None
    return str(resolved)


summary = {
    "status_file": str(status_path),
    "status": payload.get("status", "missing"),
    "session_name": session_name,
    "payload_session_name": payload_session_name,
    "launch_nonce": payload_launch_nonce,
    "run_id": payload.get("run_id"),
    "runtime_kind": payload.get("runtime_kind"),
    "config_sha256": payload.get("config_sha256"),
    "authorization_sha256": payload.get("authorization_sha256"),
    "run_attempt_id": payload.get("run_attempt_id"),
    "failure_stage": payload.get("failure_stage"),
    "error": payload.get("error"),
    "tmux_state": tmux_state,
    "started_at": payload.get("started_at"),
    "finished_at": payload.get("finished_at"),
    "exit_code": payload.get("exit_code"),
    "log_file": safe_path(payload.get("log_file"), logs),
    "last_results_dir": safe_path(payload.get("last_results_dir"), results, direct_child=True),
    "command": payload.get("command"),
    "deployment_receipt": payload.get("deployment_receipt"),
    "source_git_commit": payload.get("source_git_commit"),
    "source_git_branch": payload.get("source_git_branch"),
    "source_git_dirty": payload.get("source_git_dirty"),
    "deployment_receipt_sha256": payload.get("deployment_receipt_sha256"),
    "governance_receipt": safe_path(payload.get("governance_receipt"), results),
    "governance_receipt_sha256": payload.get("governance_receipt_sha256"),
    "governance_witness": payload.get("governance_witness"),
    "research_eligible": payload.get("research_eligible"),
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
