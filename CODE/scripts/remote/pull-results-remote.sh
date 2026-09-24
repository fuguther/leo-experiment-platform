#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=./common.sh
source "$SCRIPT_DIR/common.sh"

usage() {
    cat <<'EOF'
Usage:
  scripts/remote/pull-results-remote.sh [--run RUN] [--plan PLAN_DIR] [--all-latest N]
                                         [--trace] [--deep] [--mart] [--control]

Purpose:
  One-shot tar-over-ssh pull fallback for summary, trace, deep-analysis, and control
  files when Mutagen is unavailable or when Results/ is excluded from continuous sync.
  V2 formal runs also pull their nonce-bound external launch witness from
  .remote_runtime/launches into Results/_external_launch_witness; it is never
  synthesized from files inside the result directory.

Defaults:
  Pulls the latest remote run's summary/diagnostic files plus Results/_last_run_dir.txt and
  current remote log directories.

Options:
  --run RUN         Direct remote run name, or Results/<run>. Absolute paths are rejected.
  --plan PLAN_DIR   Pull all result runs referenced in a scheduler plan directory
                    (reads Results/_plan_runs/<PLAN_DIR>/state.json and fetches every run_dir).
                    PLAN_DIR can be a bare name (looked up under Results/_plan_runs/) or a
                    Results/_plan_runs/... relative path. Absolute paths are rejected.
  --all-latest N    Pull the N most recently modified result directories (excludes _* dirs).
  --trace           Include run_trace diagnostics for the selected run(s).
  --deep            Include experiment_bundle deep-analysis CSVs for the selected run(s).
  --mart            Include analysis_mart/*.csv and analysis_mart/*.json for the selected run(s).
  --control         Include Results/_od_sweep_configs, Results/_paper_calib_configs, and
                    Results/_parallel_jobs.
EOF
}

run_arg=""
run_flag_seen=0
plan_arg=""
all_latest_n=""
with_trace=0
with_deep=0
with_mart=0
with_control=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run)
            [[ $# -ge 2 ]] || die "--run requires a value"
            run_arg="$2"
            run_flag_seen=1
            shift 2
            ;;
        --plan)
            # W4-4: pull all runs from a scheduler plan directory
            [[ $# -ge 2 ]] || die "--plan requires a value"
            plan_arg="$2"
            shift 2
            ;;
        --all-latest)
            # W4-4: pull the N most recent result directories
            [[ $# -ge 2 ]] || die "--all-latest requires a numeric value"
            all_latest_n="$2"
            shift 2
            ;;
        --trace)
            with_trace=1
            shift
            ;;
        --deep)
            with_deep=1
            shift
            ;;
        --mart)
            with_mart=1
            shift
            ;;
        --control)
            with_control=1
            shift
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

if [[ "$run_flag_seen" -eq 1 ]]; then
    trimmed_run="${run_arg//[[:space:]]/}"
    [[ -n "$trimmed_run" ]] || die "--run requires a non-empty value"
fi

mkdir -p "$LOCAL_CODE_DIR/Results"
if [[ -n "$all_latest_n" && ! "$all_latest_n" =~ ^[1-9][0-9]*$ ]]; then
    die "--all-latest requires a positive integer"
fi

# Validate mutual exclusivity of --run / --plan / --all-latest
mode_count=0
[[ -n "$run_arg" ]]       && mode_count=$((mode_count + 1))
[[ -n "$plan_arg" ]]      && mode_count=$((mode_count + 1))
[[ -n "$all_latest_n" ]]  && mode_count=$((mode_count + 1))
if [[ "$mode_count" -gt 1 ]]; then
    die "--run, --plan, and --all-latest are mutually exclusive"
fi

selected_run_abs=""
plan_dir_abs=""
if [[ -n "$run_arg" ]]; then
    case "$run_arg" in
        /*)
            die "absolute --run paths are forbidden; use a direct Results run name"
            ;;
        Results/*)
            selected_run_abs="$REMOTE_CODE_DIR/$run_arg"
            ;;
        *)
            selected_run_abs="$REMOTE_RESULTS_DIR/$run_arg"
            ;;
    esac
elif [[ -n "$plan_arg" ]]; then
    # W4-4: resolve plan directory on remote
    case "$plan_arg" in
        /*)
            die "absolute --plan paths are forbidden; use a plan name or Results/_plan_runs/..."
            ;;
        Results/_plan_runs/*)
            plan_dir_abs="$REMOTE_CODE_DIR/$plan_arg"
            ;;
        Results/*)
            die "--plan must remain under Results/_plan_runs"
            ;;
        *)
            plan_dir_abs="$REMOTE_RESULTS_DIR/_plan_runs/$plan_arg"
            ;;
    esac
elif [[ -z "$all_latest_n" ]]; then
    selected_run_abs="$("$SCRIPT_DIR/latest-result-remote.sh" || true)"
    if [[ -z "$selected_run_abs" && "$with_control" -eq 0 ]]; then
        die "could not resolve a latest remote run; pass --run <name>, --plan <plan_dir>, --all-latest <N>, or use --control for control files only"
    fi
fi

remote_python_stdin "$REMOTE_PYTHON" - \
    "$REMOTE_CODE_DIR" \
    "$selected_run_abs" \
    "$with_trace" \
    "$with_deep" \
    "$with_mart" \
    "$with_control" \
    "${plan_dir_abs}" \
    "${all_latest_n:-0}" <<'PY' \
    | "$LOCAL_PYTHON" "$SCRIPT_DIR/safe_extract_results.py" --code-dir "$LOCAL_CODE_DIR"
import json
import os
import sys
import tarfile
from pathlib import Path


def die(message: str) -> None:
    raise SystemExit(message)


project_lexical = Path(os.path.abspath(sys.argv[1]))
expected_code = Path("/data/论文/leo-direct-sim/CODE")
if project_lexical != expected_code or project_lexical.is_symlink() or project_lexical.resolve(strict=True) != project_lexical:
    die(f"canonical CODE path is missing, linked, or resolves elsewhere: {project_lexical}")
project_dir = project_lexical
workspace_dir = project_dir.parent
launch_root = workspace_dir / ".remote_runtime" / "launches"
selected_run_raw = sys.argv[2].strip()
with_trace = sys.argv[3] == "1"
with_deep = sys.argv[4] == "1"
with_mart = sys.argv[5] == "1"
with_control = sys.argv[6] == "1"
# W4-4: new arguments
plan_dir_raw = sys.argv[7].strip() if len(sys.argv) > 7 else ""
all_latest_n = int(sys.argv[8]) if len(sys.argv) > 8 and sys.argv[8].strip().isdigit() else 0

results_dir = project_dir / "Results"
if results_dir.is_symlink() or not results_dir.is_dir() or results_dir.resolve(strict=True) != results_dir:
    die(f"canonical Results path is missing, linked, or resolves elsewhere: {results_dir}")
plan_root = (results_dir / "_plan_runs").resolve(strict=False)
files = {}


def within(path: Path, root: Path, label: str, *, strict: bool = True) -> Path:
    if path.is_symlink():
        die(f"{label} is a symbolic link: {path}")
    resolved = path.resolve(strict=strict)
    try:
        resolved.relative_to(root)
    except ValueError:
        die(f"{label} is outside {root}: {resolved}")
    return resolved


def add_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        return
    resolved = within(path, results_dir, "pull file")
    rel = resolved.relative_to(project_dir)
    files[str(rel)] = resolved


def add_external_witness(run_dir: Path) -> None:
    """Pull the nonce-bound launch status from .remote_runtime, never Results."""
    formal_path = run_dir / "formal_run.json"
    if formal_path.is_symlink() or not formal_path.is_file():
        return  # legacy Gateway result: no V2 external witness is expected
    if (launch_root.is_symlink() or not launch_root.is_dir()
            or launch_root.resolve(strict=True) != launch_root):
        die(f"canonical launch witness directory is missing or unsafe: {launch_root}")
    try:
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        die(f"formal_run.json is unreadable for {run_dir.name}: {exc}")
    if not isinstance(formal, dict):
        die(f"formal_run.json must be an object for {run_dir.name}")
    nonce = formal.get("launch_nonce")
    if (not isinstance(nonce, str) or len(nonce) != 32
            or any(char not in "0123456789abcdef" for char in nonce)):
        die(f"formal V2 run has invalid launch nonce: {run_dir.name}")
    if formal.get("run_id") != run_dir.name:
        die(f"formal V2 run id does not match result directory: {run_dir.name}")
    witness_path = launch_root / f"{nonce}.json"
    if witness_path.is_symlink() or not witness_path.is_file():
        die(f"canonical external launch witness is missing: {witness_path}")
    witness = within(witness_path, launch_root, "external launch witness")
    try:
        payload = json.loads(witness.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        die(f"external launch witness is unreadable: {witness}: {exc}")
    if (not isinstance(payload, dict) or payload.get("launch_nonce") != nonce
            or payload.get("run_id") != run_dir.name):
        die(f"external launch witness identity mismatch: {witness}")
    if "/" in run_dir.name or run_dir.name in {".", ".."}:
        die(f"unsafe result directory name for external witness: {run_dir.name}")
    arcname = Path("Results") / "_external_launch_witness" / f"{run_dir.name}.json"
    files[arcname.as_posix()] = witness


def add_tree(path: Path, suffixes=None) -> None:
    if path.is_symlink() or not path.is_dir():
        return
    safe_root = within(path, results_dir, "pull tree")
    for candidate in sorted(safe_root.rglob("*")):
        if not candidate.is_file():
            continue
        if suffixes is not None and candidate.suffix not in suffixes:
            continue
        add_file(candidate)


def add_run_dir(run_dir: Path) -> None:
    """Add the standard summary files for a single run directory."""
    default_files = [
        run_dir / "logfile.log",
        run_dir / "hyperparams.txt",
        run_dir / "config_used.json",
        run_dir / "artifact_manifest.json",
        run_dir / "Congestion_Test" / "blocks_4.npy",
        run_dir / "run_trace" / "run_meta.json",
        run_dir / "run_trace" / "graph_snapshot.json",
        run_dir / "run_trace" / "replay_events.csv",
        run_dir / "experiment_bundle" / "summary_metrics.csv",
        run_dir / "experiment_bundle" / "flow_time_series.csv",
        run_dir / "experiment_bundle" / "metrics_definitions.json",
        run_dir / "experiment_bundle" / "eligibility.json",
        # leo_sim V2 formal artifacts. These are all small, immutable evidence
        # files and are included even without --trace/--deep.
        run_dir / "receipt.json",
        run_dir / "resolved_config.json",
        run_dir / "trace.csv",
        run_dir / "manifest.json",
        run_dir / "ledgers.json",
        run_dir / "formal_run.json",
        run_dir / "governance_receipt.json",
    ]
    for candidate in default_files:
        add_file(candidate)
    add_external_witness(run_dir)

    if with_trace:
        trace_dir = run_dir / "run_trace"
        for filename in (
            "replay_events.csv",
            "packet_fate.parquet",
            "packet_fate.csv.gz",
            "decision_log.parquet",
            "decision_log.csv.gz",
            "reward_log.parquet",
            "reward_log.csv.gz",
            "train_log.parquet",
            "train_log.csv.gz",
            "state_log.parquet",
            "state_log.csv.gz",
            "eval_curve.parquet",
            "eval_curve.csv.gz",
            "link_snapshots.npz",
        ):
            add_file(trace_dir / filename)
        add_tree(run_dir / "replay_snapshots", suffixes={".npz"})
        add_tree(run_dir / "NNs", suffixes={".h5"})

    if with_deep:
        for filename in (
            "per_block_latency.csv",
            "link_load.csv",
            "path_usage.csv",
            "counterfactual_block.csv",
        ):
            add_file(run_dir / "experiment_bundle" / filename)

    if with_mart:
        add_tree(run_dir / "analysis_mart", suffixes={".csv", ".json"})


# Do not pull the mutable global _last_run_dir.txt. Two sequential formal pulls
# legitimately point at different runs; storing both at one local path would
# make the second fail the no-overwrite transaction. The terminal status
# receipt is the authoritative source of the selected direct-child run name.
add_tree(results_dir / "_overnight_logs")
add_tree(results_dir / "_parallel_logs", suffixes={".log"})

if with_control:
    add_tree(results_dir / "_od_sweep_configs")
    add_tree(results_dir / "_paper_calib_configs")
    add_tree(results_dir / "_parallel_jobs")

# ── Mode: single --run ────────────────────────────────────────────────────────
selected_dir = None
if selected_run_raw:
    selected_dir = within(Path(selected_run_raw), results_dir, "selected run")
    if not selected_dir.is_dir():
        die(f"selected run does not exist: {selected_dir}")
    if selected_dir.name.startswith("_"):
        die(f"selected run must be a result directory, not a control directory: {selected_dir.name}")
    if selected_dir.parent != results_dir:
        die("selected run must be a direct child of Results")

if selected_dir is not None:
    add_run_dir(selected_dir)

# ── W4-4 Mode: --plan <plan_dir> ─────────────────────────────────────────────
elif plan_dir_raw:
    plan_dir = within(Path(plan_dir_raw), plan_root, "plan directory")
    if not plan_dir.is_dir():
        die(f"plan directory does not exist: {plan_dir}")
    if plan_dir.parent != plan_root:
        die("plan directory must be a direct child of Results/_plan_runs")
    state_file = plan_dir / "state.json"
    if state_file.is_symlink() or not state_file.is_file():
        die(f"state.json not found in plan directory: {plan_dir}")
    # Also pull the plan's own state.json and summary.csv
    add_file(state_file)
    add_file(plan_dir / "summary.csv")
    add_tree(plan_dir / "logs", suffixes={".log"})
    # Collect all run_dirs referenced in state.json
    state = json.loads(state_file.read_text(encoding="utf-8"))
    pulled = 0
    missing = []
    for job_id, job_info in state.get("jobs", {}).items():
        run_dir_str = (job_info.get("run_dir") or "").strip()
        if not run_dir_str:
            missing.append(job_id)
            continue
        try:
            run_dir = within(Path(run_dir_str), results_dir, "plan run")
        except SystemExit:
            missing.append(job_id)
            continue
        if run_dir.parent != results_dir or run_dir.name.startswith("_"):
            missing.append(job_id)
            continue
        if not run_dir.is_dir():
            missing.append(job_id)
            continue
        add_run_dir(run_dir)
        pulled += 1
    # Print summary to stderr (visible to caller, not captured in tar stream)
    print(f"[pull] plan={plan_dir.name}: {pulled} run dirs collected, {len(missing)} missing/no run_dir", file=sys.stderr)
    if missing:
        print(f"[pull] jobs without run_dir: {', '.join(missing)}", file=sys.stderr)

# ── W4-4 Mode: --all-latest N ────────────────────────────────────────────────
elif all_latest_n > 0:
    candidates = [
        p for p in results_dir.iterdir()
        if p.is_dir() and not p.is_symlink() and not p.name.startswith("_")
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    selected = candidates[:all_latest_n]
    for run_dir in selected:
        add_run_dir(run_dir)
    print(f"[pull] all-latest {all_latest_n}: collected {len(selected)} run dirs", file=sys.stderr)

with tarfile.open(fileobj=sys.stdout.buffer, mode="w:gz") as archive:
    for arcname in sorted(files):
        archive.add(files[arcname], arcname=arcname, recursive=False)
PY
