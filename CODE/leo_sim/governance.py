"""Governance-chain integration surface for leo_sim V2 (local, fail closed).

This module is the ONLY intended entry point for the retained experiment
compiler/authorization chain to reference the V2 runtime. It never accepts
shell commands, never falls back to the legacy Gateway runtime, and binds a
run intent to: canonical config SHA, demand-scope trace identity (via
profiles) and the runtime code SHA. Actual compile -> review -> authorization
-> run-remote binding remains a VM-phase gate; this is the local contract.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import config as config_mod
from . import receipt as receipt_mod
from . import trace as trace_mod

RUNTIME_KIND = "leo_sim_v2"
INTENT_SCHEMA = "leo-sim-run-intent/v1"
REQUEST_SCHEMA = "leo-sim-experiment-request/v1"
COMPILE_REPORT_SCHEMA = "leo-sim-experiment-compile-report/v1"
RUN_MANIFEST_SCHEMA = "leo-sim-experiment-run-manifest/v1"
ANALYSIS_REQUEST_SCHEMA = "leo-sim-analysis-request/v1"
MATRIX_REQUEST_SCHEMA = "leo-sim-experiment-matrix-request/v1"
MATRIX_MANIFEST_SCHEMA = "leo-sim-experiment-matrix-manifest/v1"
MATRIX_ANALYSIS_SCHEMA = "leo-sim-matrix-analysis-request/v1"
EXECUTION_CHAIN_PATHS = (
    "CODE/experiment_platform/authorize_experiment.py",
    "CODE/experiment_platform/v2_analysis.py",
    "CODE/experiment_platform/v2_serial_gate.py",
    "CODE/scripts/remote/deployment_guard.py",
    "CODE/scripts/remote/remote_job.py",
    "CODE/scripts/remote/run-remote.sh",
)


class IntentError(ValueError):
    pass


def execution_chain_sha256() -> dict[str, str]:
    """Bind the non-leo_sim files that authorize, deploy and launch V2."""
    root = Path(__file__).resolve().parents[2]
    result = {}
    for raw in EXECUTION_CHAIN_PATHS:
        path = root / raw
        if path.is_symlink() or not path.is_file():
            raise IntentError(f"execution-chain file is missing or symbolic: {raw}")
        result[raw] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _write_json(path: Path, value: object) -> None:
    if path.is_symlink():
        raise IntentError(f"refusing symbolic output artifact: {path}")
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_run_intent(request: dict, *, project_root: Path | None = None) -> dict:
    """Validate an experiment-request-style dict and return a sealed intent.

    Required request fields:
      runtime_kind: must be exactly "leo_sim_v2"
      config:       a partial leo_sim config mapping (validated strictly)
    Optional:
      profile:      named leo_sim profile
    Anything else is rejected (fail closed).
    """
    if not isinstance(request, dict):
        raise IntentError("request must be a mapping")
    unknown = set(request) - {"runtime_kind", "config", "profile"}
    if unknown:
        raise IntentError(f"unknown request fields {sorted(unknown)}")
    if request.get("runtime_kind") != RUNTIME_KIND:
        raise IntentError(
            f"runtime_kind must be {RUNTIME_KIND!r}; the legacy Gateway "
            "runtime is never an implicit fallback")
    user = request.get("config", {})
    if not isinstance(user, dict):
        raise IntentError("request.config must be a mapping")
    resolved = config_mod.resolve_config(user, profile=request.get("profile"))
    demand_mode = resolved["config"]["demand"]["mode"]
    endpoint_cfg = resolved["config"]["endpoints"]
    auto_mlab = demand_mode == "mlab" and endpoint_cfg["mlab_auto"]
    raster_population = demand_mode == "population_gravity"
    if demand_mode != "csv" and not auto_mlab and not raster_population and \
                len(endpoint_cfg["sites"]) < 2:
        # csv mode supplies rows directly, M-Lab auto selects measured cells,
        # and population_gravity uses the complete positive-population raster
        # as its endpoint universe. Other modes compile traffic from explicit
        # sites and need traffic between distinct cells. Fail at seal time so
        # an intent cannot be marked green on ungenerable demand.
        raise IntentError(
            "non-csv demand requires at least two endpoints.sites, unless "
            "demand.mode=mlab with endpoints.mlab_auto=true")
    mode = demand_mode
    input_sha256 = ""
    if mode == "csv":
        raw_source = Path(resolved["config"]["demand"]["csv_path"])
        base = Path(project_root).resolve() if project_root is not None else Path.cwd()
        source = raw_source if raw_source.is_absolute() else base / raw_source
        source = source.resolve()
        if project_root is not None:
            try:
                source.relative_to(base)
            except ValueError as exc:
                raise IntentError(
                    "formal csv demand input must remain inside the project root") from exc
        if not source.is_file() or source.is_symlink():
            raise IntentError(f"csv demand input is not a regular file: {source}")
        input_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    elif mode == "mlab":
        source = trace_mod.REPO_MLAB_CSV
        if not source.is_file() or source.is_symlink():
            raise IntentError(f"M-Lab demand input is not a regular file: {source}")
        input_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    elif mode == "population_gravity":
        raw_source = Path(resolved["config"]["demand"]["population_path"])
        base = Path(project_root).resolve() if project_root is not None else Path.cwd()
        source = raw_source if raw_source.is_absolute() else base / raw_source
        source = source.resolve()
        if project_root is not None:
            try:
                source.relative_to(base)
            except ValueError as exc:
                raise IntentError(
                    "formal population_gravity demand input must remain inside "
                    "the project root") from exc
        if not source.is_file() or source.is_symlink():
            raise IntentError(
                "population_gravity demand input is not a regular file: "
                f"{source}")
        input_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()

    # R5-G2: seal-time binding of external learning artifacts. The resolved
    # config declares checkpoint_sha256 (and optionally
    # checkpoint_metadata_sha256); authorization must verify the actual files
    # NOW, so a green intent can never refer to a missing or mismatched
    # checkpoint that only fails at kernel load time.
    learning_cfg = resolved["config"]["learning"]
    if learning_cfg.get("algorithm") not in (None, "none") \
            and learning_cfg.get("mode") == "eval":
        ckpt_raw = learning_cfg.get("checkpoint_path")
        ckpt_sha = learning_cfg.get("checkpoint_sha256")
        if not isinstance(ckpt_raw, str) or not ckpt_raw.strip():
            raise IntentError("learning.eval requires checkpoint_path")
        ckpt = Path(ckpt_raw)
        # LEXICAL root decides the symlink-scan boundary: an unresolved
        # absolute checkpoint path under a symlinked root (e.g. /var vs the
        # resolved /private/var) must not be false-rejected by scanning
        # ancestors above the root.  RESOLVED root decides containment.
        lexical_base = (Path(project_root)
                        if project_root is not None else Path.cwd())
        base = lexical_base.resolve()

        def _reject_symlink_path(candidate: Path, what: str) -> None:
            # Reject BEFORE resolve(): resolve() would erase the symlink and
            # make is_symlink() dead code (R5-G2 review finding).
            # Only components at/under the project root are user-controlled;
            # scanning ancestors would false-positive on system symlinks
            # (macOS /var -> /private/var) above the root (R6-G2b).
            try:
                rel = candidate.relative_to(lexical_base)
            except ValueError:
                # outside the project root: containment rejects later, but
                # still scan everything so an outside symlink is never trusted
                probe = candidate
                while probe != probe.parent:
                    if probe.is_symlink():
                        raise IntentError(
                            f"{what} path contains a symbolic link: "
                            f"{candidate}")
                    probe = probe.parent
                return
            for i in range(1, len(rel.parts) + 1):
                probe = lexical_base.joinpath(*rel.parts[:i])
                if probe.is_symlink():
                    raise IntentError(
                        f"{what} path contains a symbolic link: {candidate}")

        raw_source = ckpt if ckpt.is_absolute() else base / ckpt
        _reject_symlink_path(raw_source, "learning checkpoint")
        source = raw_source.resolve()
        if project_root is not None:
            try:
                source.relative_to(base)
            except ValueError as exc:
                raise IntentError(
                    "formal learning checkpoint must remain inside the "
                    "project root") from exc
        if not source.is_file() or source.is_symlink():
            raise IntentError(
                f"learning checkpoint is not a regular file: {source}")
        if not isinstance(ckpt_sha, str) or len(ckpt_sha) != 64:
            raise IntentError("learning.eval requires checkpoint_sha256")
        if hashlib.sha256(source.read_bytes()).hexdigest() != ckpt_sha:
            raise IntentError(
                "learning checkpoint SHA-256 does not match resolved config")
        meta_sha = learning_cfg.get("checkpoint_metadata_sha256")
        if meta_sha is not None:
            # learning.py loads the sibling metadata from the UNRESOLVED
            # checkpoint parent; governance must check the same path so a
            # symlinked checkpoint cannot point governance at one metadata
            # file while the kernel reads another.
            raw_meta = ckpt.parent / "metadata.json"
            meta_candidate = raw_meta if raw_meta.is_absolute() else base / raw_meta
            _reject_symlink_path(meta_candidate, "learning checkpoint metadata")
            meta = meta_candidate.resolve()
            if project_root is not None:
                try:
                    meta.relative_to(base)
                except ValueError as exc:
                    raise IntentError(
                        "formal learning checkpoint metadata must remain "
                        "inside the project root") from exc
            if not meta.is_file() or meta.is_symlink():
                raise IntentError(
                    "learning checkpoint metadata.json missing or symbolic")
            if hashlib.sha256(meta.read_bytes()).hexdigest() != meta_sha:
                raise IntentError(
                    "learning checkpoint metadata SHA-256 does not match "
                    "resolved config")
    return {
        "schema": INTENT_SCHEMA,
        "runtime_kind": RUNTIME_KIND,
        "config_sha256": resolved["sha256"],
        "input_sha256": input_sha256,
        "trace_identity_sha256": config_mod.trace_identity_sha256(
            resolved, input_sha256),
        "code_sha256": receipt_mod.code_sha256(),
        "resolved": resolved,
    }


def compile_experiment(request_path: Path, out_dir: Path,
                       project_root: Path | None = None) -> dict:
    """Compile one immutable V2 experiment into reviewable artifacts.

    Compilation never authorizes or launches a run.  The output directory
    must be new or empty so stale artifacts cannot be mistaken for this build.
    """
    request_path = Path(request_path)
    out_dir = Path(out_dir)
    if request_path.is_symlink() or not request_path.is_file():
        raise IntentError(f"request is not a regular file: {request_path}")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntentError(f"request unreadable: {exc}") from exc
    if not isinstance(request, dict) or set(request) != {
            "schema", "experiment_id", "runtime_kind", "work_finalization",
            "acceptance", "config"}:
        raise IntentError(
            "request must contain exactly schema/experiment_id/runtime_kind/"
            "work_finalization/acceptance/config")
    if request.get("schema") != REQUEST_SCHEMA:
        raise IntentError(f"request.schema must be {REQUEST_SCHEMA!r}")
    experiment_id = request.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.startswith("EXP-") \
            or not experiment_id.replace("-", "").replace("_", "").isalnum():
        raise IntentError("experiment_id must be a safe EXP-* identifier")
    work_finalization = request.get("work_finalization")
    if not isinstance(work_finalization, str) \
            or not work_finalization.startswith("CODE/work/") \
            or not work_finalization.endswith("/finalization.json") \
            or ".." in Path(work_finalization).parts:
        raise IntentError(
            "work_finalization must be a safe CODE/work/.../finalization.json path")
    acceptance = request.get("acceptance")
    if not isinstance(acceptance, dict) or set(acceptance) != {
            "min_delivered_packets", "min_multisat_deliveries",
            "require_data_isl", "require_control_delivery"}:
        raise IntentError("acceptance has an invalid field set")
    for field in ("min_delivered_packets", "min_multisat_deliveries"):
        value = acceptance[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise IntentError(f"acceptance.{field} must be a non-negative int")
    for field in ("require_data_isl", "require_control_delivery"):
        if not isinstance(acceptance[field], bool):
            raise IntentError(f"acceptance.{field} must be bool")
    intent = build_run_intent({
        "runtime_kind": request.get("runtime_kind"),
        "config": request.get("config"),
    }, project_root=project_root)
    if out_dir.is_symlink():
        raise IntentError(f"output directory may not be symbolic: {out_dir}")
    if out_dir.exists():
        if not out_dir.is_dir():
            raise IntentError(f"output path is not a directory: {out_dir}")
        if any(out_dir.iterdir()):
            raise IntentError("output directory must be empty")
    else:
        out_dir.mkdir(parents=True)
    resolved_dir = out_dir / "resolved"
    resolved_dir.mkdir()
    run_id = f"{experiment_id}-main-s{intent['resolved']['config']['scenario']['seed']}"
    config_rel = f"resolved/{run_id}.leo-sim.yaml"
    config_path = out_dir / config_rel
    # JSON is a valid YAML subset and preserves the exact canonical values.
    config_path.write_text(
        json.dumps({"config_version": intent["resolved"]["version"],
                    **intent["resolved"]["config"]},
                   indent=2, sort_keys=True) + "\n", encoding="utf-8")
    request_copy = dict(request)
    _write_json(out_dir / "request.json", request_copy)
    request_sha = hashlib.sha256((out_dir / "request.json").read_bytes()).hexdigest()
    planned_run = {
        "run_id": run_id,
        "runtime_kind": RUNTIME_KIND,
        "config_path": config_rel,
        "config_sha256": intent["config_sha256"],
        "trace_identity_sha256": intent["trace_identity_sha256"],
        "input_sha256": intent["input_sha256"],
        "code_sha256": intent["code_sha256"],
        "execution_chain_sha256": execution_chain_sha256(),
        "acceptance": dict(acceptance),
        "seed": intent["resolved"]["config"]["scenario"]["seed"],
    }
    manifest = {
        "schema": RUN_MANIFEST_SCHEMA,
        "runtime_kind": RUNTIME_KIND,
        "experiment_id": experiment_id,
        "request_sha256": request_sha,
        "execution_authorized": False,
        "planned_runs": [planned_run],
    }
    _write_json(out_dir / "run-manifest.json", manifest)
    manifest_sha = hashlib.sha256(
        (out_dir / "run-manifest.json").read_bytes()).hexdigest()
    analysis = {
        "schema": ANALYSIS_REQUEST_SCHEMA,
        "runtime_kind": RUNTIME_KIND,
        "experiment_id": experiment_id,
        "request_sha256": request_sha,
        "run_manifest_sha256": manifest_sha,
        "planned_run_ids": [run_id],
        "comparison_contract": "same trace identity, seed and resource config",
    }
    _write_json(out_dir / "analysis-request.json", analysis)
    runbook = (
        f"# {experiment_id}\n\n"
        "Runtime: `leo_sim_v2` (no legacy fallback).\n\n"
        "Required order: three independent reviews -> finalization -> "
        "authorization -> clean deployment -> formal remote run.\n\n"
        f"Run id: `{run_id}`\n\n"
        "Authorize after the accepted finalization exists:\n\n"
        "```bash\n"
        "python3 CODE/experiment_platform/authorize_experiment.py \\\n"
        f"  --experiment EXPERIMENTS/{experiment_id} \\\n"
        f"  --finalization {work_finalization} \\\n"
        f"  --out EXPERIMENTS/{experiment_id}/authorization.json\n"
        "```\n\n"
        "Deploy a clean commit with `CODE/scripts/remote/push-remote.sh`, then launch:\n\n"
        "```bash\n"
        "CODE/scripts/remote/run-remote.sh \\\n"
        "  --runtime-kind leo_sim_v2 \\\n"
        f"  --config EXPERIMENTS/{experiment_id}/{config_rel} \\\n"
        f"  --authorization EXPERIMENTS/{experiment_id}/authorization.json \\\n"
        f"  --session {experiment_id.lower()}\n"
        "```\n"
    )
    (out_dir / "RUNBOOK.md").write_text(runbook, encoding="utf-8")
    artifact_hashes = {
        str(p.relative_to(out_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(out_dir.rglob("*")) if p.is_file()
    }
    report = {
        "schema": COMPILE_REPORT_SCHEMA,
        "status": "COMPILED_REVIEW_REQUIRED",
        "runtime_kind": RUNTIME_KIND,
        "experiment_id": experiment_id,
        "errors": [],
        "request_sha256": request_sha,
        "execution_authorized": False,
        "launcher_generated": False,
        "artifact_hashes": artifact_hashes,
    }
    _write_json(out_dir / "compile-report.json", report)
    return report


def compile_matrix_experiment(request_path: Path, out_dir: Path,
                              project_root: Path | None = None) -> dict:
    """Compile the independent leo_sim V2 matrix contract.

    Kept as a lazy wrapper so importing this module preserves the historical
    single-run surface and does not introduce a circular import.
    """
    from .matrix import compile_matrix_experiment as _compile_matrix
    return _compile_matrix(request_path, out_dir, project_root=project_root)
