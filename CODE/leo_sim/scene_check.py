"""Pure layered single-run scene classifier (read-only).

``scene_check`` never runs a simulation, never modifies a receipt and never
grants research eligibility.  It accepts a versioned decision contract, a
coverage report, a trace directory and a *verified* run directory and
recomputes every classification from those artifacts:

- L0 integrity: receipt verification, immutable trace, exact hashes;
- L1 coverage: every audited populated candidate endpoint is in the
  coverage ledger with the declared source/count/window contract;
- L2 demand: the finite trace is a population proxy with the declared
  source/destination/runtime support;
- L3 access: admission, pre-ingress overflow/rejection (always before any
  satellite ingress);
- L4 route: NO_ROUTE plus admitted packets stalled in holding before any
  ISL or downlink service;
- L5 egress: post-ingress (downlink) queue overflow against admitted;
- L6 ISL pressure: per directed link, fixed-window data-plane utilization
  over the sampled available-capacity denominator, plus per-link p95 ISL
  queue wait on the same link.

The status vocabulary is closed:
INVALID_EVIDENCE, COVERAGE_INCOMPLETE, ACCESS_LIMITED, ROUTE_LIMITED,
DOWNLINK_LIMITED, NO_ISL_EXPOSURE, NO_ISL_PRESSURE, ISL_PRESSURE_CANDIDATE

HISTORICAL RUNTIMES: a run whose receipt records a runtime identity older
than the local analyzer (posterior analysis) must NOT be classified from a
self-modifiable run directory alone.  The only posterior evidence path is a
persisted VERIFIED V2 analysis manifest (--analysis-manifest): the manifest
is re-verified through verify_persisted_analysis (full artifact recomputation
plus authorization/formal/governance/external-witness binding), the run_id
must be in its verified cohort, and its inputs must hash-bind the run's key
formal evidence files (receipt/ledgers/resolved_config/manifest/formal_run/
governance_receipt); the run trace is additionally bound by the receipt
inside that manifest.  Without the manifest the strict exact-runtime
integrity gate applies unchanged and an identity mismatch fails closed as
INVALID_EVIDENCE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import yaml

from . import coverage as coverage_mod
from . import fates as fates_mod
from . import metrics as metrics_mod
from . import receipt as receipt_mod
from . import trace as trace_mod

SCENE_DECISION_SCHEMA = "leo-sim-scene-decision/v1"
SCENE_CHECK_SCHEMA = "leo-sim-scene-check/v1"
SCENE_CHECK_CONTRACT_SCHEMA = "leo-sim-scene-check-contract/v1"
SCENE_ANALYSIS_BINDING_SCHEMA = "leo-sim-scene-analysis-binding/v1"
# key formal evidence files of a run that a verified analysis manifest must
# hash-bind before the run may be classified as historical evidence
SCENE_EVIDENCE_KEYS = (
    "receipt.json", "ledgers.json", "resolved_config.json",
    "manifest.json", "formal_run.json", "governance_receipt.json",
)

SCENE_STATUSES = (
    "INVALID_EVIDENCE",
    "COVERAGE_INCOMPLETE",
    "ACCESS_LIMITED",
    "ROUTE_LIMITED",
    "DOWNLINK_LIMITED",
    "NO_ISL_EXPOSURE",
    "NO_ISL_PRESSURE",
    "ISL_PRESSURE_CANDIDATE",
)

# exact key set of the decision contract (v1)
DECISION_KEYS = {
    "schema", "scope", "population", "coverage", "traffic",
    "access_clean", "route_clean", "downlink_clean", "isl_pressure",
    "observation",
}
DECISION_POPULATION_KEYS = {"source_sha256", "aggregation_deg",
                            "candidate_regions"}
DECISION_COVERAGE_KEYS = {"horizon_s", "step_s", "require_never_visible"}
DECISION_TRAFFIC_KEYS = {"provenance", "temporal_model",
                         "require_isl_exposed_packets"}
DECISION_ACCESS_KEYS = {"min_admission_rate",
                        "max_access_rejected_fraction_of_offered",
                        "max_uplink_queue_overflow_fraction_of_offered"}
DECISION_ROUTE_KEYS = {"max_no_route_fraction_of_admitted",
                       "max_route_stalled_fraction_of_admitted"}
DECISION_DOWNLINK_KEYS = {"max_downlink_queue_overflow_fraction_of_admitted"}
DECISION_ISL_KEYS = {"window_s", "min_consecutive_windows_same_directed_link",
                     "min_window_utilization",
                     "require_positive_p95_queue_delay_same_link"}
DECISION_OBSERVATION_KEYS = {"emission_end_s", "observation_end_s"}
DECISION_FRACTION_KEYS = {"min_admission_rate",
                          "max_access_rejected_fraction_of_offered",
                          "max_uplink_queue_overflow_fraction_of_offered",
                          "max_no_route_fraction_of_admitted",
                          "max_route_stalled_fraction_of_admitted",
                          "max_downlink_queue_overflow_fraction_of_admitted"}

# every denominator is closed and fixed
DENOMINATOR_OFFERED = "offered"
DENOMINATOR_ADMITTED = "admitted"


class SceneCheckError(ValueError):
    """Invalid scene-check input or decision contract."""


def _safe_contract_path(root: Path, raw: Any, label: str) -> Path:
    if (not isinstance(raw, str) or not raw.startswith("CODE/work/")
            or ".." in Path(raw).parts
            or Path(raw).suffix.lower() not in {".json", ".yaml", ".yml"}):
        raise SceneCheckError(f"{label} must be a safe CODE/work path")
    path = (root / raw).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise SceneCheckError(f"{label} escapes project root") from exc
    if path.is_symlink() or not path.is_file():
        raise SceneCheckError(f"{label} is missing or symbolic: {raw}")
    return path


def load_scene_check_contract(path: str | Path,
                              root: str | Path | None = None) -> dict[str, Any]:
    """Load the immutable scene/coverage binding used by the CLI.

    The matrix compiler hashes this contract before authorization.  At
    analysis time we re-check the contract, both referenced artifact hashes,
    and the semantic decision/coverage schemas before any classification.
    """
    contract_path = Path(path).resolve()
    if root is None:
        code_root = next((parent for parent in contract_path.parents
                          if parent.name == "CODE"), None)
        if code_root is None:
            raise SceneCheckError("cannot infer project root for scene-check contract")
        project_root = code_root.parent.resolve()
    else:
        project_root = Path(root).resolve()
    try:
        contract_path.relative_to(project_root)
    except ValueError as exc:
        raise SceneCheckError("scene-check contract escapes project root") from exc
    if contract_path.is_symlink() or not contract_path.is_file():
        raise SceneCheckError(f"scene-check contract missing or symbolic: {path}")
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SceneCheckError(f"scene-check contract unreadable: {exc}") from exc
    expected_keys = {
        "schema", "decision_path", "decision_sha256", "coverage_path",
        "coverage_sha256", "canonical_invocation",
    }
    if not isinstance(contract, dict) or set(contract) != expected_keys:
        raise SceneCheckError("scene-check contract keys mismatch")
    if contract.get("schema") != SCENE_CHECK_CONTRACT_SCHEMA:
        raise SceneCheckError(
            f"scene-check contract schema must be {SCENE_CHECK_CONTRACT_SCHEMA}")
    invocation = contract.get("canonical_invocation")
    if (not isinstance(invocation, list) or len(invocation) < 7
            or any(not isinstance(token, str) or not token for token in invocation)
            or invocation[:3] != ["python3", "-m", "CODE.leo_sim.scene_check"]
            or len(invocation[3:]) % 2):
        raise SceneCheckError("scene-check canonical_invocation is invalid")
    expected_prefix = ["--root", ".", "--contract",
                       str(contract_path.relative_to(project_root))]
    if invocation[3:7] != expected_prefix:
        raise SceneCheckError("scene-check canonical_invocation does not bind contract")
    decision_path = _safe_contract_path(
        project_root, contract["decision_path"], "decision_path")
    coverage_path = _safe_contract_path(
        project_root, contract["coverage_path"], "coverage_path")
    decision_sha = hashlib.sha256(decision_path.read_bytes()).hexdigest()
    coverage_sha = hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    if decision_sha != contract["decision_sha256"]:
        raise SceneCheckError("scene decision contract hash mismatch")
    if coverage_sha != contract["coverage_sha256"]:
        raise SceneCheckError("coverage audit hash mismatch")
    try:
        decision = yaml.safe_load(decision_path.read_text(encoding="utf-8"))
        coverage_report = json.loads(coverage_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError, json.JSONDecodeError) as exc:
        raise SceneCheckError(f"scene-check bound artifact unreadable: {exc}") from exc
    if not isinstance(decision, dict):
        raise SceneCheckError("bound scene decision must be a mapping")
    decision_errors = verify_decision_contract(decision)
    if decision_errors:
        raise SceneCheckError("bound scene decision invalid: "
                              + "; ".join(decision_errors))
    coverage_errors = coverage_mod.verify_coverage_audit_v2(coverage_report)
    if coverage_errors:
        raise SceneCheckError("bound coverage audit invalid: "
                              + "; ".join(coverage_errors))
    return {
        "contract": contract,
        "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "contract_path": str(contract_path.relative_to(project_root)),
        "decision": decision,
        "decision_path": str(decision_path.relative_to(project_root)),
        "coverage": coverage_report,
        "coverage_path": str(coverage_path.relative_to(project_root)),
    }


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v)


def _is_frac(v) -> bool:
    return _is_num(v) and 0.0 <= v <= 1.0


def load_decision_contract(path: str) -> dict[str, Any]:
    """Load and validate the versioned decision contract YAML."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except OSError as exc:
        raise SceneCheckError(f"decision contract unreadable: {path}: {exc}")
    except yaml.YAMLError as exc:
        raise SceneCheckError(f"decision contract invalid YAML: {exc}")
    if not isinstance(raw, dict):
        raise SceneCheckError("decision contract must be a mapping")
    errors = verify_decision_contract(raw)
    if errors:
        raise SceneCheckError("decision contract invalid: " + "; ".join(errors))
    return raw


def verify_decision_contract(decision: dict[str, Any]) -> list[str]:
    """Validate the exact-key decision contract.  Empty = valid.  A tampered
    threshold or window is an evidence failure, never a silent default."""
    errors: list[str] = []
    if not isinstance(decision, dict):
        return ["decision contract must be a mapping"]
    if decision.get("schema") != SCENE_DECISION_SCHEMA:
        errors.append(f"decision schema must be {SCENE_DECISION_SCHEMA}")
    if set(decision) != DECISION_KEYS:
        errors.append("decision keys mismatch: "
                      f"unknown={sorted(set(decision) - DECISION_KEYS)} "
                      f"missing={sorted(DECISION_KEYS - set(decision))}")
    if decision.get("scope") != "global_populated_land":
        errors.append("decision scope must be global_populated_land")
    pop = decision.get("population")
    if not isinstance(pop, dict) or set(pop) != DECISION_POPULATION_KEYS:
        errors.append("decision population keys mismatch")
    else:
        sha = pop.get("source_sha256")
        if not (isinstance(sha, str) and len(sha) == 64
                and all(c in "0123456789abcdef" for c in sha)):
            errors.append("population.source_sha256 must be lowercase SHA-256")
        if not _is_num(pop.get("aggregation_deg")) \
                or pop["aggregation_deg"] <= 0:
            errors.append("population.aggregation_deg must be positive")
        if isinstance(pop.get("candidate_regions"), bool) \
                or not isinstance(pop.get("candidate_regions"), int) \
                or pop["candidate_regions"] < 1:
            errors.append("population.candidate_regions must be positive int")
    cov = decision.get("coverage")
    if not isinstance(cov, dict) or set(cov) != DECISION_COVERAGE_KEYS:
        errors.append("decision coverage keys mismatch")
    else:
        for key in ("horizon_s", "step_s"):
            if not _is_num(cov.get(key)) or cov[key] <= 0:
                errors.append(f"coverage.{key} must be positive")
        if not isinstance(cov.get("require_never_visible"), int) \
                or isinstance(cov["require_never_visible"], bool) \
                or cov["require_never_visible"] < 0:
            errors.append("coverage.require_never_visible must be int >= 0")
    traf = decision.get("traffic")
    if not isinstance(traf, dict) or set(traf) != DECISION_TRAFFIC_KEYS:
        errors.append("decision traffic keys mismatch")
    else:
        if traf.get("provenance") != "population_proxy":
            errors.append("traffic.provenance must be population_proxy")
        if traf.get("temporal_model") != "local_diurnal_cosine":
            errors.append("traffic.temporal_model must be "
                          "local_diurnal_cosine")
        if not isinstance(traf.get("require_isl_exposed_packets"), int) \
                or isinstance(traf["require_isl_exposed_packets"], bool) \
                or traf["require_isl_exposed_packets"] < 0:
            errors.append("traffic.require_isl_exposed_packets must be "
                          "int >= 0")
    for group, keys in (("access_clean", DECISION_ACCESS_KEYS),
                        ("route_clean", DECISION_ROUTE_KEYS),
                        ("downlink_clean", DECISION_DOWNLINK_KEYS)):
        g = decision.get(group)
        if not isinstance(g, dict) or set(g) != keys:
            errors.append(f"decision {group} keys mismatch")
        elif isinstance(g, dict):
            for key, value in g.items():
                if "_fraction_of_" in key:
                    if not _is_frac(value):
                        errors.append(f"{group}.{key} must be in [0, 1]")
                elif key == "min_admission_rate":
                    if not _is_frac(value):
                        errors.append(
                            f"{group}.{key} must be in [0, 1]")
    isl = decision.get("isl_pressure")
    if not isinstance(isl, dict) or set(isl) != DECISION_ISL_KEYS:
        errors.append("decision isl_pressure keys mismatch")
    else:
        if not _is_num(isl.get("window_s")) or isl["window_s"] <= 0:
            errors.append("isl_pressure.window_s must be positive")
        if not isinstance(isl.get("min_consecutive_windows_same_directed_link"),
                          int) \
                or isinstance(
                    isl["min_consecutive_windows_same_directed_link"], bool) \
                or isl["min_consecutive_windows_same_directed_link"] < 1:
            errors.append("isl_pressure.min_consecutive_windows_"
                          "same_directed_link must be int >= 1")
        if not _is_frac(isl.get("min_window_utilization")):
            errors.append("isl_pressure.min_window_utilization must be "
                          "in [0, 1]")
        if not isinstance(isl.get("require_positive_p95_queue_delay_same_link"),
                          bool):
            errors.append("isl_pressure.require_positive_p95_queue_delay_"
                          "same_link must be bool")
    obs = decision.get("observation")
    if not isinstance(obs, dict) or set(obs) != DECISION_OBSERVATION_KEYS:
        errors.append("decision observation keys mismatch")
    else:
        if not _is_num(obs.get("emission_end_s")) \
                or obs["emission_end_s"] <= 0:
            errors.append("observation.emission_end_s must be positive")
        if not _is_num(obs.get("observation_end_s")) \
                or obs["observation_end_s"] < obs["emission_end_s"]:
            errors.append("observation.observation_end_s must be >= "
                          "emission_end_s")
    return errors


def load_trace_dir(trace_dir: str) -> list[dict[str, Any]]:
    """Load the immutable trace rows (the offered universe)."""
    tdir = Path(trace_dir)
    tpath = tdir / "trace.csv"
    if not tpath.is_file() or tpath.is_symlink():
        raise SceneCheckError(f"trace.csv missing or unsafe: {trace_dir}")
    return trace_mod.load_trace(str(tpath))


def _safe_analysis_manifest_path(root: Path, raw: Any, label: str) -> Path:
    """Require a real non-symbolic JSON file inside the project root.

    Symlinks are rejected on the UNRESOLVED lexical candidate, one
    component at a time below the root (the leaf included): resolving
    first would hide a terminal symlink from is_symlink().  Only after
    that rejection is the candidate resolved and re-checked for
    containment and presence.
    """
    if not (isinstance(raw, str) and raw
            and Path(raw).suffix.lower() == ".json"
            and ".." not in Path(raw).parts):
        raise SceneCheckError(
            f"{label} must be a JSON path inside the project")
    candidate = (root / raw) if not Path(raw).is_absolute() else Path(raw)
    try:
        rel = candidate.relative_to(root)
    except ValueError as exc:
        raise SceneCheckError(f"{label} escapes project root") from exc
    # Reject every symlink component on the lexical path before any
    # resolution can hide it behind its target.
    probe = root
    for part in rel.parts:
        probe = probe / part
        if probe.is_symlink():
            raise SceneCheckError(
                f"{label} must not be a symlink component: {probe}")
    path = candidate.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise SceneCheckError(f"{label} escapes project root") from exc
    if path.is_symlink() or not path.is_file():
        raise SceneCheckError(f"{label} is missing or symbolic: {raw}")
    return path


def _verify_analysis_manifest_binding(root: Path, manifest_path: Path,
                                      run_dir: Path) -> dict[str, Any]:
    """Bind a run directory to a persisted VERIFIED V2 analysis manifest.

    This is the ONLY posterior evidence path for scene classification: the
    manifest is re-verified through verify_persisted_analysis (full artifact
    recomputation plus the authorization/formal/governance/external-witness
    chain), the run_id must be in its verified cohort, every key formal
    evidence file of the run must be hash-bound in the manifest inputs, and
    the on-disk run trace must be bound by the receipt inside that manifest.
    Any failure raises SceneCheckError (fail closed).
    """
    from CODE.experiment_platform import v2_analysis as v2_mod
    ok, errors = v2_mod.verify_persisted_analysis(root, manifest_path)
    if not ok:
        raise SceneCheckError(
            "analysis manifest is not fully verified: " + "; ".join(errors))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SceneCheckError(f"analysis manifest unreadable: {exc}") from exc
    if not (isinstance(manifest, dict)
            and manifest.get("schema") == v2_mod.SCHEMA
            and manifest.get("status") == "VERIFIED"):
        raise SceneCheckError(
            "analysis manifest is not a VERIFIED V2 analysis manifest")
    formal_path = run_dir / "formal_run.json"
    if formal_path.is_symlink() or not formal_path.is_file():
        raise SceneCheckError("run directory lacks a real formal_run.json")
    try:
        formal = json.loads(formal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SceneCheckError(f"run formal_run.json unreadable: {exc}") from exc
    run_id = formal.get("run_id") if isinstance(formal, dict) else None
    if not isinstance(run_id, str) or not run_id or run_dir.name != run_id:
        raise SceneCheckError("run directory name != formal_run.json run_id")
    verified_ids = manifest.get("verified_run_ids")
    if not isinstance(verified_ids, list) or run_id not in verified_ids:
        raise SceneCheckError(
            f"run {run_id} is not in the VERIFIED analysis cohort")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, dict):
        raise SceneCheckError("analysis manifest inputs contract is missing")
    key_evidence: dict[str, str] = {}
    for name in SCENE_EVIDENCE_KEYS:
        path = run_dir / name
        if path.is_symlink() or not path.is_file():
            raise SceneCheckError(f"run key evidence missing or unsafe: {name}")
        rel = str(path.relative_to(root))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if inputs.get(rel) != digest:
            raise SceneCheckError(
                f"run key evidence {name} is not hash-bound by the "
                "analysis manifest")
        key_evidence[name] = digest
    rcp_path = run_dir / "receipt.json"
    try:
        bound_receipt = json.loads(rcp_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SceneCheckError(f"run receipt.json unreadable: {exc}") from exc
    trace_sha = (bound_receipt.get("trace_sha256")
                 if isinstance(bound_receipt, dict) else None)
    tpath = run_dir / "trace.csv"
    if not (isinstance(trace_sha, str)
            and not tpath.is_symlink() and tpath.is_file()
            and hashlib.sha256(tpath.read_bytes()).hexdigest() == trace_sha):
        raise SceneCheckError(
            "run trace.csv is not bound by the receipt in the analysis "
            "manifest")
    return {
        "schema": SCENE_ANALYSIS_BINDING_SCHEMA,
        "analysis_manifest": str(manifest_path.relative_to(root)),
        "analysis_manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()).hexdigest(),
        "run_id": run_id,
        "verified_run_ids": list(verified_ids),
        "key_evidence": key_evidence,
    }


def _check_integrity(run_dir: str, trace_rows: list[dict],
                     trace_dir: str,
                     *, analysis_manifest_binding=None) -> list[str]:
    """L0: the run directory must be receipt-verified (strict default) or
    bound to a VERIFIED V2 analysis manifest, and the trace must be
    immutable and identical to the receipt's trace.

    analysis_manifest_binding (built by _verify_analysis_manifest_binding)
    replaces the strict local exact-runtime receipt gate: integrity is then
    established by the manifested analysis chain (full artifact recomputation
    + formal governance/external-witness binding + hash-bound key evidence
    files), never by a self-modifiable run directory alone and never by
    weakening verify_receipt_dir."""
    errors: list[str] = []
    if analysis_manifest_binding is None:
        errors.extend(receipt_mod.verify_receipt_dir(run_dir))
    # else: the manifest binding gate already proved the full artifact
    # recomputation under the formal chain and the hash-binding of the run's
    # key evidence files including the trace-vs-receipt binding.
    out = Path(run_dir)
    tpath = out / "trace.csv"
    if not tpath.is_file() or tpath.is_symlink():
        errors.append("run directory trace.csv missing or unsafe")
        return errors
    run_trace_hash = hashlib.sha256(tpath.read_bytes()).hexdigest()
    trace_dir_hash = hashlib.sha256(
        (Path(trace_dir) / "trace.csv").read_bytes()).hexdigest()
    if run_trace_hash != trace_dir_hash:
        errors.append("run trace.csv != scene trace.csv (trace not immutable)")
    if len(trace_rows) == 0:
        errors.append("scene trace is empty (offered = 0)")
    return errors


def _check_coverage(coverage_report: dict[str, Any],
                    decision: dict[str, Any]) -> list[str]:
    """L1: the coverage audit must match the declared population asset,
    candidate count, and audited window; summary recomputed from rows."""
    errors: list[str] = []
    cov_errors = coverage_mod.verify_coverage_audit_v2(coverage_report)
    if cov_errors:
        errors.extend(f"coverage: {e}" for e in cov_errors)
        return errors
    source = coverage_report.get("endpoint_source", {})
    pop = decision.get("population", {})
    cov = decision.get("coverage", {})
    scan = coverage_report.get("scan", {})
    if source.get("source_sha256") != pop.get("source_sha256"):
        errors.append("coverage source_sha256 != decision population SHA")
    if source.get("aggregation_deg") != pop.get("aggregation_deg"):
        errors.append("coverage aggregation_deg != decision aggregation")
    if source.get("candidate_regions") != pop.get("candidate_regions"):
        errors.append("coverage candidate_regions != decision candidates")
    if scan.get("horizon_s") != cov.get("horizon_s") \
            or scan.get("step_s") != cov.get("step_s"):
        errors.append("coverage audited window != decision window")
    summary = coverage_report.get("summary", {})
    if summary.get("never_visible") != cov.get("require_never_visible"):
        errors.append("coverage never_visible != required value")
    # no omitted IDs: every candidate region appears exactly once
    rows = coverage_report.get("endpoints", [])
    names = [row.get("name") for row in rows]
    if len(names) != len(set(names)):
        errors.append("coverage ledger contains duplicate endpoint ids")
    if len(rows) != source.get("candidate_regions"):
        errors.append("coverage ledger length != candidate_regions")
    return errors

def _load_run_artifacts(run_dir: str):
    out = Path(run_dir)
    ledgers_path = out / "ledgers.json"
    if not ledgers_path.is_file() or ledgers_path.is_symlink():
        raise SceneCheckError("ledgers.json missing or unsafe")
    try:
        ledgers = json.loads(ledgers_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SceneCheckError(f"ledgers.json unreadable: {exc}")
    if not isinstance(ledgers, dict):
        raise SceneCheckError("ledgers.json must be a JSON object")
    raw_events = ledgers.get("packet_events")
    if not isinstance(raw_events, list):
        raise SceneCheckError("ledgers.packet_events must be a list")
    service_windows = ledgers.get("link_service_windows")
    available_windows = ledgers.get("link_available_windows")
    if not isinstance(service_windows, list) \
            or not isinstance(available_windows, list):
        raise SceneCheckError("ledgers link windows must be lists")
    packet_fates = ledgers.get("packet_fates")
    if not isinstance(packet_fates, dict):
        raise SceneCheckError("ledgers.packet_fates must be a mapping")
    return ledgers, raw_events, service_windows, available_windows, \
        packet_fates


def _ingress_pids(raw_events: list[dict]) -> set[str]:
    """Independent admission recomputation: unique satellite_ingress pids."""
    return {str(event.get("pid")) for event in raw_events
            if isinstance(event, dict)
            and event.get("kind") == "satellite_ingress"}


def _recompute_access_layers(trace_rows, packet_fates, admitted_pids,
                             raw_events) -> dict[str, Any]:
    """L3/L5 numerators and fractions with closed denominators.

    ACCESS_QUEUE_OVERFLOW is the historical dual-use fate: split it by the
    independently verified ingress event (no ingress = source uplink /
    access overflow; ingress = destination downlink overflow).  A
    contradictory pre-ingress fate with an ingress event is invalid
    evidence (the receipt verifier already refuses it; the checker also
    recomputes it here).
    """
    offered_pids = {str(row["packet_id"]) for row in trace_rows}
    fate_counts = {f: 0 for f in fates_mod.DATA_FATES}
    rejected_pids: set[str] = set()
    uplink_overflow_pids: set[str] = set()
    downlink_overflow_pids: set[str] = set()
    no_route_pids: set[str] = set()
    in_system_pids: set[str] = set()
    contradictory_pids: set[str] = set()
    for pid, pair in packet_fates.items():
        fate = pair[0] if isinstance(pair, list) and len(pair) == 2 \
            else None
        if fate in fate_counts:
            fate_counts[fate] += 1
        if fate == "ACCESS_REJECTED":
            rejected_pids.add(pid)
            if pid in admitted_pids:
                contradictory_pids.add(pid)
        elif fate == "ACCESS_QUEUE_OVERFLOW":
            if pid in admitted_pids:
                downlink_overflow_pids.add(pid)
            else:
                uplink_overflow_pids.add(pid)
        elif fate == "NO_ROUTE":
            no_route_pids.add(pid)
        elif fate == "IN_SYSTEM_AT_STOP":
            in_system_pids.add(pid)
    if packet_fates and set(packet_fates) - offered_pids:
        return {"error": "packet fates contain ids not in the trace"}
    if offered_pids - set(packet_fates):
        return {"error": "trace ids missing from packet fates"}
    return {
        "offered_packets": len(trace_rows),
        "offered_pids": sorted(offered_pids, key=int),
        "admitted_packets": len(admitted_pids),
        "admitted_pids": sorted(admitted_pids, key=int),
        "admission_rate": (len(admitted_pids) / len(trace_rows)
                           if trace_rows else 0.0),
        "access_rejected_packets": len(rejected_pids),
        "access_rejected_pids": sorted(rejected_pids, key=int),
        "uplink_overflow_packets": len(uplink_overflow_pids),
        "uplink_overflow_pids": sorted(uplink_overflow_pids, key=int),
        "downlink_overflow_packets": len(downlink_overflow_pids),
        "downlink_overflow_pids": sorted(downlink_overflow_pids, key=int),
        "no_route_packets": len(no_route_pids),
        "no_route_pids": sorted(no_route_pids, key=int),
        "in_system_at_stop_packets": len(in_system_pids),
        "in_system_at_stop_pids": sorted(in_system_pids, key=int),
        "contradictory_pids": sorted(contradictory_pids, key=int),
        "fate_counts": fate_counts,
    }


def _route_stalled_pids(raw_events, packet_fates, admitted_pids) -> set[str]:
    """Admitted packets ending IN_SYSTEM_AT_STOP that entered a holding
    queue but never entered ISL or downlink service."""
    entered_holding: set[str] = set()
    isl_service: set[str] = set()
    downlink_service: set[str] = set()
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        pid = str(event.get("pid"))
        if kind == "queue_enter" and event.get("queue") == "holding":
            entered_holding.add(pid)
        elif kind == "service_start":
            stage = event.get("stage")
            if stage == "isl":
                isl_service.add(pid)
            elif stage == "downlink":
                downlink_service.add(pid)
    stalled = set()
    for pid in entered_holding:
        if pid not in isl_service and pid not in downlink_service \
                and pid in admitted_pids:
            pair = packet_fates.get(pid)
            fate = pair[0] if isinstance(pair, list) and len(pair) == 2 \
                else None
            if fate == "IN_SYSTEM_AT_STOP":
                stalled.add(pid)
    return stalled


def _isl_exposed_pids(service_windows) -> set[str]:
    """Packets that ever entered ISL service (data plane)."""
    return {str(window.get("pid")) for window in service_windows
            if isinstance(window, dict) and window.get("stage") == "isl"}


def _isl_queue_waits_per_link(raw_events) -> dict[str, list[float]]:
    """ISL data-plane queue waits per directed link: for each packet, the
    wait between queue_enter(isl, link) and the next service_start(isl,
    link) of the same packet."""
    enters: dict[str, list[tuple[str, float]]] = {}
    starts: dict[str, list[tuple[str, float]]] = {}
    for event in raw_events:
        if not isinstance(event, dict):
            continue
        if event.get("kind") == "queue_enter" and \
                event.get("queue") == "isl":
            link = event.get("link_id")
            if isinstance(link, str):
                enters.setdefault(link, []).append(
                    (str(event.get("pid")), float(event.get("at", 0))))
        elif event.get("kind") == "service_start" and \
                event.get("stage") == "isl":
            link = event.get("link_id")
            if isinstance(link, str):
                starts.setdefault(link, []).append(
                    (str(event.get("pid")), float(event.get("at", 0))))
    waits: dict[str, list[float]] = {}
    for link, queue_entries in enters.items():
        start_entries = sorted(starts.get(link, []), key=lambda e: e[1])
        used = [False] * len(start_entries)
        for pid, queued_at in queue_entries:
            for idx, (spid, started_at) in enumerate(start_entries):
                if not used[idx] and spid == pid and started_at >= queued_at:
                    used[idx] = True
                    waits.setdefault(link, []).append(started_at - queued_at)
                    break
    return {k: sorted(v) for k, v in waits.items()}


def _p95(sorted_values: list[float]) -> float | None:
    if not sorted_values:
        return None
    idx = int(math.ceil(0.95 * len(sorted_values))) - 1
    return sorted_values[max(0, min(idx, len(sorted_values) - 1))]


def _fixed_windows(start: float, end: float, window_s: float):
    """Windows on an absolute time grid [k*window_s, (k+1)*window_s), keyed
    by absolute index k, covering [start, end).  The grid is anchored at
    t=0 so two different windows cannot collapse onto the same key."""
    out = []
    k = int(math.floor(start / window_s))
    while True:
        wlo = k * window_s
        whi = (k + 1) * window_s
        if wlo >= end - 1e-12:
            break
        out.append((wlo, whi, k))
        k += 1
    return out


def _recompute_isl_pressure(service_windows, available_windows,
                            raw_events, decision) -> dict[str, Any]:
    """Per directed link, fixed-window data-plane utilization over the
    sampled available-capacity denominator.  Returns a candidate only when
    one directed link meets the utilization threshold in at least
    min_consecutive_windows same-directed adjacent windows AND has positive
    p95 ISL queue wait on that same link."""
    window_s = float(decision["isl_pressure"]["window_s"])
    min_consecutive = int(
        decision["isl_pressure"]
        ["min_consecutive_windows_same_directed_link"])
    min_utilization = float(decision["isl_pressure"]["min_window_utilization"])
    require_queue = bool(
        decision["isl_pressure"]["require_positive_p95_queue_delay_same_link"])
    isl_queue_waits = _isl_queue_waits_per_link(raw_events)

    # available capacity denominator per directed ISL link and fixed window
    available: dict[str, dict[int, float]] = {}
    for window in available_windows:
        if not isinstance(window, dict) or window.get("stage") != "isl":
            continue
        link = window.get("link_id")
        if not isinstance(link, str):
            continue
        lo = float(window.get("start", 0.0))
        hi = float(window.get("end", lo))
        capacity = float(window.get("capacity_bits", 0.0))
        if hi <= lo:
            continue
        for wlo, whi, k in _fixed_windows(lo, hi, window_s):
            if wlo >= hi - 1e-12 or whi <= lo + 1e-12:
                continue
            overlap = min(whi, hi) - max(wlo, lo)
            if overlap <= 0:
                continue
            available.setdefault(link, {}).setdefault(k, 0.0)
            available[link][k] += capacity * overlap / (hi - lo)

    served: dict[str, dict[int, float]] = {}
    for window in service_windows:
        if not isinstance(window, dict) or window.get("stage") != "isl":
            continue
        link = window.get("link_id")
        if not isinstance(link, str):
            continue
        lo = float(window.get("start", 0.0))
        hi = float(window.get("end", lo))
        bits = float(window.get("served_bits", 0.0))
        for wlo, whi, k in _fixed_windows(lo, hi, window_s):
            if wlo >= hi - 1e-12 or whi <= lo + 1e-12:
                continue
            overlap = min(whi, hi) - max(wlo, lo)
            if overlap <= 0:
                continue
            served.setdefault(link, {}).setdefault(k, 0.0)
            served[link][k] += bits * overlap / (hi - lo)

    utilization: dict[str, dict[int, float]] = {}
    missing_denominator: dict[str, set[int]] = {}
    for link, windows in served.items():
        for k, served_bits in windows.items():
            capacity = available.get(link, {}).get(k)
            if capacity is None or capacity <= 0:
                missing_denominator.setdefault(link, set()).add(k)
                continue
            utilization.setdefault(link, {})[k] = min(
                1.0, served_bits / capacity)

    links: dict[str, Any] = {}
    for link in sorted(set(served) | set(available)):
        windows_util = utilization.get(link, {})
        missing = sorted(missing_denominator.get(link, set()))
        links[link] = {
            "utilization_windows": {
                str(k): windows_util[k] for k in sorted(windows_util)},
            "missing_denominator_windows": missing,
        }

    candidate = None
    for link, windows_util in utilization.items():
        ordered = sorted(windows_util)
        if len(ordered) < min_consecutive:
            continue
        best_run_windows: list[int] = []
        current_run: list[int] = []
        previous_k: int | None = None
        for k in ordered:
            qualifies = windows_util[k] >= min_utilization
            adjacent = previous_k is not None and k == previous_k + 1
            if qualifies and (not current_run or adjacent):
                current_run.append(k)
            elif qualifies:
                # A missing/undocumented fixed window is a real gap.  It
                # must break the "consecutive" episode rather than being
                # silently removed from the run.
                current_run = [k]
            else:
                current_run = []
            if len(current_run) > len(best_run_windows):
                best_run_windows = list(current_run)
            previous_k = k
        if len(best_run_windows) < min_consecutive:
            continue
        if require_queue:
            waits = isl_queue_waits.get(link, [])
            p95 = _p95(waits)
            if p95 is None or p95 <= 0.0:
                continue
        # Select from the actual best qualifying episode.  Do not use a
        # later, shorter run merely because it was the last run observed.
        consecutive = best_run_windows[-min_consecutive:]
        candidate = {
            "link_id": link,
            "qualifying_windows": [int(k) for k in consecutive],
            "windows": {str(k): windows_util[k] for k in consecutive},
            "served_bits": {
                str(k): served.get(link, {}).get(k, 0.0)
                for k in consecutive},
            "available_capacity_bits": {
                str(k): available.get(link, {}).get(k, 0.0)
                for k in consecutive},
            "p95_queue_wait_s": _p95(isl_queue_waits.get(link, [])),
        }
        break
    return {"links": links, "candidate": candidate}


def _recompute_demand_layer(trace_rows) -> dict[str, Any]:
    """L2: observed source / destination / runtime endpoint support sets."""
    observed_sources = sorted({row["src_grid_id"] for row in trace_rows})
    observed_destinations = sorted({row["dst_grid_id"] for row in trace_rows})
    runtime_endpoints = sorted(set(observed_sources)
                               | set(observed_destinations))
    return {
        "observed_source_regions": observed_sources,
        "observed_destination_regions": observed_destinations,
        "runtime_endpoint_regions": runtime_endpoints,
        "observed_source_count": len(observed_sources),
        "observed_destination_count": len(observed_destinations),
        "runtime_endpoint_count": len(runtime_endpoints),
    }


def check_scene(run_dir: str, trace_dir: str,
                coverage_report: dict[str, Any],
                decision: dict[str, Any], *,
                analysis_manifest: str | None = None,
                project_root: str | Path | None = None) -> dict[str, Any]:
    """Pure read-only layered single-run classification.

    Returns a scene-check v1 report whose ``status`` is one of the closed
    SCENE_STATUSES.  Never runs a simulation, never modifies a receipt,
    never grants research eligibility.
    """
    errors: list[str] = []
    integrity_errors = verify_decision_contract(decision)
    if integrity_errors:
        # a tampered threshold/window contract is itself invalid evidence
        return {"schema": SCENE_CHECK_SCHEMA,
                "status": "INVALID_EVIDENCE",
                "integrity_ok": False,
                "decision_errors": integrity_errors}
    trace_rows = load_trace_dir(trace_dir)
    manifest_binding = None
    if analysis_manifest is not None:
        if project_root is None:
            raise SceneCheckError("analysis_manifest requires project_root")
        root = Path(project_root).resolve()
        manifest_path = _safe_analysis_manifest_path(
            root, analysis_manifest, "analysis_manifest")
        manifest_binding = _verify_analysis_manifest_binding(
            root, manifest_path, Path(run_dir))
    integrity_ok = True
    integrity_errors = list(_check_integrity(
        run_dir, trace_rows, trace_dir,
        analysis_manifest_binding=manifest_binding))
    if integrity_errors:
        integrity_ok = False
        errors.extend(integrity_errors)
    ledgers = raw_events = service_windows = available_windows = None
    packet_fates = {}
    if integrity_ok:
        try:
            ledgers, raw_events, service_windows, available_windows, \
                packet_fates = _load_run_artifacts(run_dir)
        except SceneCheckError as exc:
            integrity_ok = False
            errors.append(str(exc))
    if not integrity_ok:
        return {"schema": SCENE_CHECK_SCHEMA, "status": "INVALID_EVIDENCE",
                "integrity_ok": False, "errors": errors}

    report: dict[str, Any] = {
        "schema": SCENE_CHECK_SCHEMA,
        "scope": "global_populated_land",
        "integrity_ok": True,
        "trace_reported": True,
    }

    # L1 coverage
    coverage_errors = _check_coverage(coverage_report, decision)
    report["coverage_ok"] = not coverage_errors
    report["coverage_errors"] = coverage_errors
    report["coverage_report"] = {
        "endpoint_source": coverage_report.get("endpoint_source"),
        "scan": coverage_report.get("scan"),
        "summary": coverage_report.get("summary"),
        "endpoint_count": len(coverage_report.get("endpoints", [])),
    }

    admitted_pids = _ingress_pids(raw_events)
    access = _recompute_access_layers(trace_rows, packet_fates, admitted_pids,
                                      raw_events)
    if "error" in access:
        access = {**{k: 0 for k in (
            "offered_packets", "admitted_packets", "access_rejected_packets",
            "uplink_overflow_packets", "downlink_overflow_packets",
            "no_route_packets", "in_system_at_stop_packets")},
                  "contradictory_pids": [], "fate_counts": {},
                  "offered_pids": [], "admitted_pids": [],
                  "access_rejected_pids": [], "uplink_overflow_pids": [],
                  "downlink_overflow_pids": [], "no_route_pids": [],
                  "in_system_at_stop_pids": [], "admission_rate": 0.0,
                  "error": access["error"]}
        report["status"] = "INVALID_EVIDENCE"
        report["access"] = access
        report["errors"] = [access["error"]]
        return report
    report["access"] = access
    report["demand"] = _recompute_demand_layer(trace_rows)

    offered = access["offered_packets"]
    admitted = access["admitted_packets"]
    # L3 access
    access_clean = True
    access_reasons = []
    if offered == 0 or admitted == 0:
        access_clean = False
        access_reasons.append("zero admitted denominator: access cannot "
                              "be proven clean and routing is not evaluated")
    else:
        if access["admission_rate"] < float(
                decision["access_clean"]["min_admission_rate"]):
            access_clean = False
            access_reasons.append("admission_rate below min_admission_rate")
        if access["access_rejected_packets"] / offered > float(
                decision["access_clean"]
                ["max_access_rejected_fraction_of_offered"]):
            access_clean = False
            access_reasons.append("access rejected fraction above limit")
        if access["uplink_overflow_packets"] / offered > float(
                decision["access_clean"]
                ["max_uplink_queue_overflow_fraction_of_offered"]):
            access_clean = False
            access_reasons.append("uplink queue overflow fraction above limit")
    if access["contradictory_pids"]:
        access_clean = False
        access_reasons.append("contradictory access-fate/ingress pairs")
    report["access_clean"] = access_clean
    report["access_reasons"] = access_reasons

    # L4 route
    route_clean = True
    route_reasons = []
    stalled_pids = _route_stalled_pids(raw_events, packet_fates,
                                       admitted_pids) if admitted else set()
    if admitted == 0:
        # classification order: zero admitted stops at ACCESS_LIMITED and
        # never evaluates routing
        pass
    else:
        no_route_frac = access["no_route_packets"] / admitted
        stalled_frac = len(stalled_pids) / admitted
        if no_route_frac > float(
                decision["route_clean"]["max_no_route_fraction_of_admitted"]):
            route_clean = False
            route_reasons.append("no-route fraction above limit")
        if stalled_frac > float(
                decision["route_clean"]
                ["max_route_stalled_fraction_of_admitted"]):
            route_clean = False
            route_reasons.append("route-stalled fraction above limit")
    report["route_clean"] = route_clean
    report["route_reasons"] = route_reasons
    report["route_stalled_pids"] = sorted(stalled_pids, key=int)

    # L5 egress
    downlink_clean = True
    downlink_reasons = []
    if admitted > 0:
        downlink_frac = access["downlink_overflow_packets"] / admitted
        if downlink_frac > float(
                decision["downlink_clean"]
                ["max_downlink_queue_overflow_fraction_of_admitted"]):
            downlink_clean = False
            downlink_reasons.append("downlink queue overflow fraction above "
                                    "limit")
    report["downlink_clean"] = downlink_clean
    report["downlink_reasons"] = downlink_reasons

    # L6 ISL exposure + pressure
    isl_exposed = _isl_exposed_pids(service_windows)
    report["isl_exposed_packets"] = len(isl_exposed)
    report["isl_exposed_pids"] = sorted(isl_exposed, key=int)
    isl = _recompute_isl_pressure(service_windows, available_windows,
                                  raw_events, decision)
    isl_pressure_ok = isl["candidate"] is not None
    report["isl"] = isl
    report["isl_pressure_ok"] = isl_pressure_ok
    report["control_plane"] = _control_plane_summary(ledgers)

    # classification order
    if not report["integrity_ok"]:
        status = "INVALID_EVIDENCE"
    elif not report["coverage_ok"]:
        status = "COVERAGE_INCOMPLETE"
    elif not access_clean:
        status = "ACCESS_LIMITED"
    elif not route_clean:
        status = "ROUTE_LIMITED"
    elif not downlink_clean:
        status = "DOWNLINK_LIMITED"
    elif len(isl_exposed) < int(
            decision["traffic"]["require_isl_exposed_packets"]):
        status = "NO_ISL_EXPOSURE"
    elif not isl_pressure_ok:
        status = "NO_ISL_PRESSURE"
    else:
        status = "ISL_PRESSURE_CANDIDATE"
    if manifest_binding is not None:
        report["analysis_manifest_binding"] = manifest_binding
    report["status"] = status
    return report


def _control_plane_summary(ledgers: dict) -> dict[str, Any]:
    """Run-level control occupancy/failures (never per-link/total-link
    utilization comparisons): control packets share physical ISL capacity,
    so data-plane utilization is only a conservative lower bound."""
    occupied = ledgers.get("occupied")
    counters = ledgers.get("control_counters")
    mechanism = ledgers.get("mechanism_counters")
    return {
        "occupied": occupied if isinstance(occupied, dict) else {},
        "control_counters": counters if isinstance(counters, dict) else {},
        "control_failures": {
            k: mechanism.get(k, 0)
            for k in ("control_entered_queue", "control_tx_started",
                      "control_tx_completed")
        } if isinstance(mechanism, dict) else {},
    }


def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="project root containing CODE/work")
    parser.add_argument("--contract", type=Path, required=True,
                        help="bound scene-check contract, relative to root")
    parser.add_argument("--run-dir", type=Path, required=True,
                        help="verified V2 result directory, relative to root")
    parser.add_argument(
        "--analysis-manifest", type=Path,
        help="persisted VERIFIED V2 analysis manifest, relative to root; "
             "required to classify historical runs whose runtime predates "
             "the local analyzer")
    parser.add_argument("--out", type=Path, required=True,
                        help="scene-check JSON output, relative to root")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    def rooted(raw: Path, label: str) -> Path:
        path = (root / raw).resolve() if not raw.is_absolute() else raw.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise SceneCheckError(f"{label} escapes project root") from exc
        if path.is_symlink() or not path.is_dir():
            raise SceneCheckError(f"{label} must be a real directory: {raw}")
        return path

    run_dir = rooted(args.run_dir, "run-dir")
    bound = load_scene_check_contract(root / args.contract, root)
    report = check_scene(
        str(run_dir), str(run_dir), bound["coverage"], bound["decision"],
        analysis_manifest=(str(args.analysis_manifest)
                           if args.analysis_manifest else None),
        project_root=root)
    report["contract_binding"] = {
        "schema": SCENE_CHECK_CONTRACT_SCHEMA,
        "contract_path": bound["contract_path"],
        "contract_sha256": bound["contract_sha256"],
        "decision_path": bound["decision_path"],
        "decision_sha256": bound["contract"]["decision_sha256"],
        "coverage_path": bound["coverage_path"],
        "coverage_sha256": bound["contract"]["coverage_sha256"],
    }
    out = (root / args.out).resolve() if not args.out.is_absolute() else args.out.resolve()
    try:
        out.relative_to(root)
    except ValueError as exc:
        raise SceneCheckError("out escapes project root") from exc
    if out.is_symlink():
        raise SceneCheckError("out must not be symbolic")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(_cli())
    except SceneCheckError as exc:
        raise SystemExit(f"scene_check: {exc}") from exc
