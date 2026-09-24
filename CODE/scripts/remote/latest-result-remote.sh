#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

if [[ ${1:-} == "--help" || ${1:-} == "-h" ]]; then
    cat <<'EOF'
Usage:
  scripts/remote/latest-result-remote.sh
EOF
    exit 0
fi

load_remote_config

remote_python_stdin "$REMOTE_PYTHON" - "$REMOTE_STATUS_FILE_ABS" "$REMOTE_CODE_DIR" <<'PY'
import json
import os
import sys
from pathlib import Path

status_path = Path(sys.argv[1]).resolve(strict=False)
project_dir = Path(os.path.abspath(sys.argv[2]))
expected_code = Path("/data/论文/leo-direct-sim/CODE")
if project_dir != expected_code or project_dir.is_symlink() or project_dir.resolve(strict=True) != project_dir:
    raise SystemExit("canonical CODE path is missing, linked, or resolves elsewhere")
results_dir = project_dir / "Results"
if results_dir.is_symlink() or not results_dir.is_dir() or results_dir.resolve(strict=True) != results_dir:
    raise SystemExit("canonical Results path is missing, linked, or resolves elsewhere")


def valid_run(raw: str) -> Path | None:
    if not raw:
        return None
    candidate = Path(raw)
    if candidate.is_symlink() or not candidate.is_dir():
        return None
    resolved = candidate.resolve()
    try:
        resolved.relative_to(results_dir)
    except ValueError:
        return None
    if resolved.parent != results_dir or resolved.name.startswith("_"):
        return None
    return resolved

payload = {}
if status_path.is_file():
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        payload = {}

last_results = payload.get("last_results_dir", "")
validated = valid_run(last_results)
if validated:
    print(validated)
    raise SystemExit(0)

pointer = project_dir / "Results" / "_last_run_dir.txt"
if pointer.is_file() and not pointer.is_symlink():
    try:
        line = pointer.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        line = ""
    validated = valid_run(line)
    if validated:
        print(validated)
        raise SystemExit(0)

if results_dir.is_dir():
    candidates = [valid_run(str(p)) for p in results_dir.iterdir()]
    candidates = [p for p in candidates if p is not None]
    if candidates:
        newest = max(candidates, key=lambda path: path.stat().st_mtime)
        print(str(newest.resolve()))
PY
