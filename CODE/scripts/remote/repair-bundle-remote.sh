#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage:
  scripts/remote/repair-bundle-remote.sh --run RUN [--bundle-stages STAGES]

Creates a copy-on-write repair candidate under Results/_repairs/. The original
local and remote run directories are never modified. Only local blocks_*.npy
files are overlaid onto the copied candidate before bundling.
EOF
}

run_name=""
bundle_stages="per_block,paths,analysis,summary,definitions"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --run) [[ $# -ge 2 ]] || die "--run requires a value"; run_name="$2"; shift 2 ;;
        --bundle-stages) [[ $# -ge 2 ]] || die "--bundle-stages requires a value"; bundle_stages="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

[[ -n "$run_name" ]] || die "--run is required"
[[ "$run_name" != */* && "$run_name" != *$'\n'* && "$run_name" != *$'\r'* && "$run_name" != "." && "$run_name" != ".." && "$run_name" != _* ]] \
    || die "--run must be a direct non-control child name of Results"
[[ "$bundle_stages" =~ ^[A-Za-z0-9_,-]+$ ]] || die "--bundle-stages contains unsafe characters"
load_remote_config

local_results="$(cd "$LOCAL_CODE_DIR/Results" && pwd -P)"
local_run_dir="$LOCAL_CODE_DIR/Results/$run_name"
[[ ! -L "$local_run_dir" && -d "$local_run_dir" ]] || die "local run directory is missing or unsafe: $local_run_dir"
resolved_local="$(cd "$local_run_dir" && pwd -P)"
[[ "$(dirname "$resolved_local")" == "$local_results" ]] || die "local run escapes CODE/Results"
local_ct_dir="$resolved_local/Congestion_Test"
[[ ! -L "$local_ct_dir" && -d "$local_ct_dir" ]] || die "local Congestion_Test is missing or unsafe"

block_names=()
while IFS= read -r file_path; do
    block_names+=("$(basename "$file_path")")
done < <(find "$local_ct_dir" -maxdepth 1 -type f -name 'blocks_*.npy' -print | sort)
[[ ${#block_names[@]} -gt 0 ]] || die "no local blocks_*.npy files found"

repair_id="$(date +%Y%m%d_%H%M%S)"
remote_source="$REMOTE_RESULTS_DIR/$run_name"
remote_repair_root="$REMOTE_RESULTS_DIR/_repairs"
remote_candidate="$remote_repair_root/${run_name}__repair_${repair_id}"

prepare=$(cat <<EOF
set -euo pipefail
source_dir=$(printf '%q' "$remote_source")
repair_root=$(printf '%q' "$remote_repair_root")
candidate=$(printf '%q' "$remote_candidate")
[[ "\$(realpath -e $(printf '%q' "$REMOTE_RESULTS_DIR"))" == $(printf '%q' "$REMOTE_RESULTS_DIR") ]] || { echo '[repair] canonical Results is linked or resolves elsewhere' >&2; exit 2; }
[[ -d "\$source_dir" && ! -L "\$source_dir" ]] || { echo '[repair] source run missing or unsafe' >&2; exit 2; }
mkdir -p "\$repair_root"
[[ ! -L "\$repair_root" ]] || { echo '[repair] repair root may not be a symlink' >&2; exit 2; }
[[ ! -e "\$candidate" ]] || { echo '[repair] candidate already exists' >&2; exit 2; }
cp -a -- "\$source_dir" "\$candidate"
mkdir -p "\$candidate/Congestion_Test"
tar -xzf - -C "\$candidate/Congestion_Test"
EOF
)

COPYFILE_DISABLE=1 COPY_EXTENDED_ATTRIBUTES_DISABLE=1 "$TAR_BIN" -czf - \
    -C "$local_ct_dir" "${block_names[@]}" \
    | "$SSH_BIN" "$REMOTE_HOST_ALIAS" "bash -lc $(printf '%q' "$prepare")"

bundle=$(cat <<EOF
set -euo pipefail
cd $(printf '%q' "$REMOTE_CODE_DIR")
$REMOTE_ENV_ACTIVATE
$(printf '%q' "$REMOTE_PYTHON") experiment_bundle.py $(printf '%q' "Results/_repairs/${run_name}__repair_${repair_id}") --stages $(printf '%q' "$bundle_stages")
EOF
)
"$SSH_BIN" "$REMOTE_HOST_ALIAS" "bash -lc $(printf '%q' "$bundle")"

echo "repair_mode=COPY_ON_WRITE"
echo "original_remote_run=$remote_source"
echo "repair_candidate=$remote_candidate"
echo "original_modified=false"
