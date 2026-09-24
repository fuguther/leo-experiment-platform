#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage:
  scripts/remote/logs-remote.sh [--lines N] [--path REMOTE_LOG_PATH]
EOF
}

lines="120"
log_path=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --lines)
            [[ $# -ge 2 ]] || die "--lines requires a value"
            lines="$2"
            shift 2
            ;;
        --path)
            [[ $# -ge 2 ]] || die "--path requires a value"
            log_path="$2"
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
[[ "$lines" =~ ^[1-9][0-9]*$ && "$lines" -le 10000 ]] || die "--lines must be an integer from 1 to 10000"
if [[ -n "$log_path" ]]; then
    case "$log_path" in
        "$REMOTE_LOG_DIR_ABS"/*.log) ;;
        /*) die "--path must remain inside the canonical remote log directory" ;;
        *.log) [[ "$log_path" != */* && "$log_path" != *".."* ]] || die "--path must be a direct log filename" ;;
        *) die "--path must identify a .log file" ;;
    esac
fi

if [[ -z "$log_path" ]]; then
    log_path="$(
        remote_python_stdin "$REMOTE_PYTHON" - "$REMOTE_STATUS_FILE_ABS" "$REMOTE_LOG_DIR_ABS" <<'PY'
import json
import os
import sys
from pathlib import Path

status_path = Path(sys.argv[1])
log_dir = Path(os.path.abspath(sys.argv[2]))
expected = Path("/data/论文/leo-direct-sim/CODE/Results/_overnight_logs")
results = expected.parent
if results.is_symlink() or not results.is_dir() or results.resolve(strict=True) != results:
    raise SystemExit("canonical Results path is missing, linked, or resolves elsewhere")
if log_dir != expected or (log_dir.exists() and (log_dir.is_symlink() or log_dir.resolve(strict=True) != log_dir)):
    raise SystemExit("canonical log path is linked or resolves elsewhere")

payload = {}
if status_path.is_file() and not status_path.is_symlink():
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}

log_file = payload.get("log_file", "")
if log_file:
    print(log_file)
    raise SystemExit(0)

if log_dir.is_dir():
    candidates = sorted(
        [p for p in log_dir.glob("*.log") if p.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if candidates:
        print(str(candidates[0].resolve()))
PY
    )"
fi

[[ -n "$log_path" ]] || die "no remote log file could be determined"

remote_python_stdin "$REMOTE_PYTHON" - "$REMOTE_LOG_DIR_ABS" "$log_path" "$lines" <<'PY'
import sys
import os
from collections import deque
from pathlib import Path

root = Path(os.path.abspath(sys.argv[1]))
expected = Path("/data/论文/leo-direct-sim/CODE/Results/_overnight_logs")
results = expected.parent
if root != expected or results.is_symlink() or not results.is_dir() or results.resolve(strict=True) != results:
    raise SystemExit("canonical Results/log root is missing, linked, or resolves elsewhere")
if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
    raise SystemExit("canonical log root is missing, linked, or resolves elsewhere")
raw = sys.argv[2]
candidate = Path(raw) if Path(raw).is_absolute() else root / raw
if candidate.is_symlink() or not candidate.is_file():
    raise SystemExit("log path is missing or unsafe")
resolved = candidate.resolve(strict=True)
try:
    resolved.relative_to(root)
except ValueError as exc:
    raise SystemExit(f"log path is outside canonical log directory: {resolved}") from exc
if resolved.parent != root or resolved.suffix != ".log":
    raise SystemExit("log path must be a direct .log child of the canonical log directory")
with resolved.open("r", encoding="utf-8", errors="replace") as handle:
    for line in deque(handle, maxlen=int(sys.argv[3])):
        print(line, end="")
PY
