"""Evidence-bound analysis for the dedicated ``leo_sim_v2`` matrix runtime.

The historical paired analyzer consumes the legacy Gateway artifact layout.
V2 has a different, stricter result contract (receipt/ledgers/formal witness),
so this adapter verifies that contract directly and only then computes paired
metrics.  A VERIFIED analysis is still not a paper claim: ``claim-gate.json``
records the boundary and requires the normal independent claim-support/value
reviews before any claim can be promoted.  Receipt/v5 results additionally
require the nonce-bound launch status pulled from the canonical VM
``.remote_runtime/launches`` directory; historical receipt/v3/v4 results use
an explicit legacy/internal-only branch and cannot masquerade as v5 evidence.

POSTERIOR RUNTIME ANALYSIS: a v5 run whose receipt records a runtime
identity different from the local analyzer (code sha and/or dependency
versions, e.g. a VM Python 3.11 environment vs a newer local checkout) may
still be analyzed when authorization + formal_run + governance_receipt v2 +
nonce external witness FULLY cross-bind the historical runtime
code/config/auth/result hashes.  Artifact integrity is then recomputed
without the local==runtime gate (an internal, unbound receipt primitive)
and the recorded run-time identity is bound by that chain (analysis_mode
"posterior_governed_runtime").  Fail-closed by default: any hash, identity,
eligibility or verification_errors anomaly rejects; legacy v3/v4 evidence
keeps the strict exact-runtime requirement (no external witness exists to
bind it); the strict default verify_receipt_dir is never weakened, and the
receipt.json research_eligible=false vs governance research_eligible=true
semantic difference is never rewritten.

Authorization verification mirrors the same rule: the strict recomputation
is attempted first (today's code/config identities); when it can no longer
match only because the analyzer checkout differs from the historical
runtime, the issue-time authorization is re-admitted through BOUND snapshot
verification (payload seal, every recorded artifact hash, structural row
identity) and the witness chain below must still bind every run identity.
The manifest records this as ``bound_posterior`` under
``authorization_verification``; it is never a bypass of the strict gate for
tampered or drifted snapshots.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from CODE.experiment_platform import authorize_experiment
from CODE.experiment_platform import isl_pressure
from CODE.leo_sim import metrics as metrics_mod
from CODE.leo_sim import receipt as receipt_mod


SCHEMA = "leo-sim-v2-analysis/v1"
CLAIM_GATE_SCHEMA = "leo-sim-v2-claim-gate/v1"
MATRIX_SCHEMA = "leo-sim-experiment-matrix-manifest/v1"
ANALYSIS_SCHEMA = "leo-sim-matrix-analysis-request/v1"
GOVERNANCE_SCHEMA_V1 = "leo-sim-governance-receipt/v1"
GOVERNANCE_SCHEMA_V2 = "leo-sim-governance-receipt/v2"
EXTERNAL_STATUS_SCHEMA = "leo-remote-launch-status/v2"
# how a result's runtime identity was bound: reproduced by the local
# analyzer exactly, or governed through the formal chain because the local
# analyzer is a newer version than the historical runtime
RUNTIME_BINDING_EXACT = "local_exact_match"
RUNTIME_BINDING_POSTERIOR = "governance_bound_posterior"
ANALYSIS_MODE_POSTERIOR = "posterior_governed_runtime"
# how the cohort authorization was admitted: recomputed strictly from
# current artifacts, or bound to its own issue-time snapshots because the
# analyzer checkout can no longer reproduce the historical code identity
AUTHORIZATION_VERIFICATION_STRICT = "strict_recomputed"
AUTHORIZATION_VERIFICATION_BOUND = "bound_posterior"
GOVERNANCE_WITNESS_FIELDS = (
    "receipt_schema", "resolved_config_sha256", "trace_manifest_schema",
    "trace_identity_contract", "trace_manifest_sha256",
)
REMOTE_RESULTS_ROOT = Path("/data/论文/leo-direct-sim/CODE/Results")
ANALYZER_FILES = (
    "CODE/experiment_platform/v2_analysis.py",
    "CODE/experiment_platform/isl_pressure.py",
    "CODE/experiment_platform/isl_pressure_decision.py",
    "CODE/leo_sim/coverage.py",
    "CODE/leo_sim/metrics.py",
    "CODE/leo_sim/receipt.py",
    "CODE/leo_sim/scene_check.py",
)
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class V2AnalysisError(ValueError):
    """A V2 result or analysis contract cannot be verified."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _analyzer_identity() -> dict[str, Any]:
    """Bind analysis semantics to one clean Git commit and exact files."""
    repository = Path(__file__).resolve().parents[2]
    try:
        commit = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip().lower()
        status = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain", "--",
             *ANALYZER_FILES],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise V2AnalysisError(f"cannot resolve analyzer Git identity: {exc}") from exc
    if not GIT_COMMIT.fullmatch(commit):
        raise V2AnalysisError("analyzer Git commit is not an exact full SHA")
    if status:
        raise V2AnalysisError("analyzer files differ from the bound Git commit")
    files = {}
    for raw in ANALYZER_FILES:
        path = repository / raw
        if path.is_symlink() or not path.is_file():
            raise V2AnalysisError(f"analyzer file is missing or symbolic: {raw}")
        files[raw] = file_sha256(path)
    return {"git_commit": commit, "files": files}


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise V2AnalysisError(f"missing or symbolic artifact: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise V2AnalysisError(f"unreadable JSON artifact {path}: {exc}") from exc


def _direct_result(results_root: Path, run_id: str) -> Path:
    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        raise V2AnalysisError(f"invalid run id: {run_id!r}")
    path = results_root / run_id
    if path.is_symlink() or not path.is_dir() or path.resolve() != path.absolute():
        raise V2AnalysisError(f"result must be a lexical direct directory: {run_id}")
    if path.parent.resolve() != results_root.resolve() or run_id.startswith("_"):
        raise V2AnalysisError(f"result is outside the canonical results root: {run_id}")
    return path


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(float(value)):
        raise V2AnalysisError(f"{label} must be finite numeric")
    return float(value)


def _external_witness_path(
        witness_root: Path, run_id: str, *,
        launch_nonce: str | None = None) -> Path:
    if witness_root.is_symlink() or not witness_root.is_dir():
        raise V2AnalysisError(f"external launch witness directory is missing or unsafe: {witness_root}")
    witness_id = launch_nonce if launch_nonce is not None else run_id
    if launch_nonce is not None and (
            len(launch_nonce) != 32
            or any(char not in "0123456789abcdef" for char in launch_nonce)):
        raise V2AnalysisError(f"{run_id} launch nonce is invalid")
    path = witness_root / f"{witness_id}.json"
    if path.is_symlink() or not path.is_file() or path.parent != witness_root:
        raise V2AnalysisError(f"external launch witness is missing or unsafe: {path}")
    try:
        path.resolve(strict=True).relative_to(witness_root.resolve(strict=True))
    except ValueError as exc:
        raise V2AnalysisError(f"external launch witness escapes its directory: {path}") from exc
    return path


def _verify_external_witness(
        *, witness_root: Path, run_id: str,
        formal: dict[str, Any], governed: dict[str, Any],
        paths: dict[str, Path], authorized: dict[str, Any],
        external_witness_by_nonce: bool = False) -> dict[str, Any]:
    """Verify the launch-scoped status pulled from the canonical VM runtime."""
    expected_nonce = formal.get("launch_nonce")
    path = _external_witness_path(
        witness_root, run_id,
        launch_nonce=expected_nonce if external_witness_by_nonce else None)
    witness = _read_json(path)
    if not isinstance(witness, dict) or witness.get("schema") != EXTERNAL_STATUS_SCHEMA:
        raise V2AnalysisError(f"{run_id} external launch witness schema mismatch")
    if witness.get("status") != "success" or witness.get("exit_code") != 0:
        raise V2AnalysisError(f"{run_id} external launch witness is not successful")
    expected_auth = authorized.get("authorization_sha256")
    if any(witness.get(key) != expected for key, expected in {
            "launch_nonce": expected_nonce,
            "run_id": run_id,
            "authorization_sha256": expected_auth,
    }.items()):
        raise V2AnalysisError(f"{run_id} external launch witness identity mismatch")
    remote_result = witness.get("last_results_dir")
    expected_remote_result = REMOTE_RESULTS_ROOT / run_id
    if (not isinstance(remote_result, str)
            or Path(remote_result) != expected_remote_result
            or Path(remote_result).is_symlink()):
        raise V2AnalysisError(f"{run_id} external launch witness result identity mismatch")
    governed_path = paths["governance_receipt.json"]
    if witness.get("governance_receipt_sha256") != file_sha256(governed_path):
        raise V2AnalysisError(f"{run_id} external launch witness receipt hash mismatch")
    expected_fields = {key: governed.get(key) for key in GOVERNANCE_WITNESS_FIELDS}
    if witness.get("governance_witness") != expected_fields:
        raise V2AnalysisError(f"{run_id} external launch witness binding mismatch")
    if governed.get("launch_nonce") != expected_nonce \
            or governed.get("authorization_sha256") != expected_auth:
        raise V2AnalysisError(f"{run_id} governance/external witness identity mismatch")
    return witness


def _metric_from_result(receipt: dict[str, Any], ledgers: dict[str, Any],
                        primary: str) -> float:
    totals = receipt.get("totals")
    fate_counts = receipt.get("fate_counts")
    if not isinstance(totals, dict) or not isinstance(fate_counts, dict):
        raise V2AnalysisError("receipt totals/fate_counts are missing")
    if primary == "delivery_rate":
        offered = sum(int(value) for value in fate_counts.values())
        return (float(fate_counts.get("DELIVERED", 0)) / offered
                if offered else 0.0)
    if primary in {"delivered_bits", "terminal_loss_bits",
                   "in_system_bits_at_stop"}:
        return _finite(totals.get(primary), f"receipt totals.{primary}")
    congestion = ledgers.get("congestion_metrics")
    if not isinstance(congestion, dict):
        raise V2AnalysisError("ledgers.congestion_metrics is missing")
    if primary in {"access_admission_rate",
                   "network_delivery_rate_by_horizon"}:
        value = congestion.get(primary)
        return _finite(value, f"congestion metrics.{primary}")
    packets = congestion.get("packets")
    links = congestion.get("links")
    if not isinstance(packets, dict) or not isinstance(links, dict):
        raise V2AnalysisError("congestion metrics packets/links are missing")
    delivered_packets = [item for item in packets.values()
                         if isinstance(item, dict) and "e2e_s" in item]
    if primary in {"e2e_delay_mean_s", "queue_wait_mean_s",
                   "tx_time_mean_s", "propagation_time_mean_s"}:
        key = {
            "e2e_delay_mean_s": "e2e_s",
            "queue_wait_mean_s": "total_queue_wait_s",
            "tx_time_mean_s": "tx_s",
            "propagation_time_mean_s": "prop_s",
        }[primary]
        values = [_finite(item.get(key), f"packet metric {key}")
                  for item in delivered_packets]
        if not values:
            raise V2AnalysisError(f"primary metric {primary} has no delivered packets")
        return sum(values) / len(values)
    if primary in {"link_utilization_mean", "service_window_utilization_mean"}:
        values = [_finite(item.get("utilization"), "link utilization")
                  for item in links.values()]
        if not values:
            raise V2AnalysisError(f"primary metric {primary} has no service links")
        return sum(values) / len(values)
    if primary in {"isl_link_utilization_mean", "isl_link_utilization_max"}:
        values = [_finite(item.get("utilization"), "ISL utilization")
                  for item in links.values()
                  if isinstance(item, dict) and item.get("stage") == "isl"]
        if not values:
            raise V2AnalysisError(f"primary metric {primary} has no ISL links")
        return (max(values) if primary.endswith("_max")
                else sum(values) / len(values))
    raise V2AnalysisError(f"unsupported V2 primary metric: {primary}")


def _run_diagnostics(ledgers: dict[str, Any]) -> dict[str, Any]:
    """Preserve mechanism and raw ISL denominator evidence for interpretation."""
    counters = ledgers.get("mechanism_counters")
    control = ledgers.get("control_counters")
    congestion = ledgers.get("congestion_metrics")
    if not isinstance(counters, dict):
        raise V2AnalysisError("ledgers.mechanism_counters is missing")
    if not isinstance(control, dict):
        raise V2AnalysisError("ledgers.control_counters is missing")
    if not isinstance(congestion, dict) or not isinstance(congestion.get("links"), dict):
        raise V2AnalysisError("ledgers.congestion_metrics.links is missing")
    zero_rate_holds = counters.get("mcs_zero_rate_holds")
    if isinstance(zero_rate_holds, bool) or not isinstance(zero_rate_holds, int) \
            or zero_rate_holds < 0:
        raise V2AnalysisError("mcs_zero_rate_holds must be a non-negative integer")
    if zero_rate_holds:
        raise V2AnalysisError("zero-rate hold makes the analysis ineligible")
    mcs = {
        "rate_samples": counters.get("mcs_rate_samples"),
        "zero_rate_holds": zero_rate_holds,
        "rate_min_bps": counters.get("mcs_rate_min_bps"),
        "rate_max_bps": counters.get("mcs_rate_max_bps"),
    }
    try:
        windowed_isl = isl_pressure.analyze_windows(ledgers)
    except isl_pressure.PressureAnalysisError as exc:
        raise V2AnalysisError(
            f"windowed ISL pressure evidence is invalid: {exc}") from exc
    packet_fates = ledgers.get("packet_fates")
    access = ledgers.get("access")
    queue_area = ledgers.get("queue_area_bits_s")
    if not isinstance(packet_fates, dict) or not isinstance(access, dict) \
            or not isinstance(queue_area, dict):
        raise V2AnalysisError(
            "packet fate, access, or queue-area diagnostics are missing")
    fate_counts: dict[str, int] = {}
    for pair in packet_fates.values():
        if not isinstance(pair, list) or len(pair) != 2 \
                or not isinstance(pair[0], str):
            raise V2AnalysisError("packet_fates entry is malformed")
        fate_counts[pair[0]] = fate_counts.get(pair[0], 0) + 1
    links: dict[str, dict[str, Any]] = {}
    saturated: list[str] = []
    for link_id, item in sorted(congestion["links"].items()):
        if not isinstance(item, dict) or item.get("stage") != "isl":
            continue
        values = {
            "served_bits": _finite(item.get("served_bits"),
                                    f"{link_id}.served_bits"),
            "available_capacity_bits": _finite(
                item.get("available_capacity_bits"),
                f"{link_id}.available_capacity_bits"),
            "utilization": _finite(item.get("utilization"),
                                   f"{link_id}.utilization"),
            "available_samples": item.get("available_samples"),
            "service_windows": item.get("service_windows"),
        }
        links[link_id] = values
        if values["utilization"] >= 1.0 - 1e-12:
            saturated.append(link_id)
    return {
        "mcs": mcs,
        "control": dict(sorted(control.items())),
        "access": dict(sorted(access.items())),
        "fate_counts": dict(sorted(fate_counts.items())),
        "queue_area_bits_s": dict(sorted(queue_area.items())),
        "windowed_isl": windowed_isl,
        "drain": {
            "in_system_at_stop_packets": fate_counts.get(
                "IN_SYSTEM_AT_STOP", 0),
            "unmatched_isl_queue_entries": windowed_isl[
                "unmatched_isl_queue_entries"],
        },
        "isl": {
            "link_count": len(links),
            "saturated_link_ids": saturated,
            "links": links,
        },
    }


def _verify_authorized_cell(row: dict[str, Any],
                            authorized: dict[str, Any]) -> None:
    fields = (
        "run_id", "runtime_kind", "arm_id", "phase", "trace_seed",
        "pairing_key", "config_sha256", "trace_identity_sha256",
        "input_sha256", "code_sha256", "controlled_signature",
    )
    changed = [key for key in fields if row.get(key) != authorized.get(key)]
    if changed:
        raise V2AnalysisError(
            f"{row.get('run_id')} authorized cell identity mismatch: "
            + ", ".join(changed))


def _verify_governance_identity(
        run_id: str, governed: dict[str, Any], authorized: dict[str, Any],
        expected_deployment: dict[str, Any] | None) -> None:
    claimed_payload = governed.get("payload_sha256")
    unsigned = {
        key: value for key, value in governed.items()
        if key != "payload_sha256"
    }
    if claimed_payload != canonical_sha(unsigned):
        raise V2AnalysisError(f"{run_id} governance payload hash mismatch")
    if governed.get("execution_chain_sha256") \
            != authorized.get("execution_chain_sha256"):
        raise V2AnalysisError(
            f"{run_id} governance execution-chain mismatch")
    if expected_deployment is None:
        return
    expected = {
        "source_git_commit": expected_deployment.get("source_git_commit"),
        "source_tree_sha256": expected_deployment.get("source_tree_sha256"),
        "deployment_receipt_sha256": expected_deployment.get("receipt_sha256"),
    }
    if any(not isinstance(value, str) or not value
           for value in expected.values()):
        raise V2AnalysisError("expected deployment identity is incomplete")
    if any(governed.get(key) != value for key, value in expected.items()):
        raise V2AnalysisError(
            f"{run_id} predecessor deployment identity mismatch")


def _verify_result(root: Path, results_root: Path, witness_root: Path,
                   row: dict[str, Any], authorized: dict[str, Any],
                   primary: str, *, require_external_witness: bool,
                   external_witness_by_nonce: bool = False,
                   expected_deployment: dict[str, Any] | None = None) -> dict[str, Any]:
    run_id = row.get("run_id")
    result_dir = _direct_result(results_root, run_id)
    required = (
        "formal_run.json", "governance_receipt.json", "receipt.json",
        "ledgers.json", "resolved_config.json", "manifest.json",
    )
    paths = {name: result_dir / name for name in required}
    docs = {name: _read_json(path) for name, path in paths.items()}
    # Verification order: the formal binding chain (authorization cell,
    # formal run witness, governance receipt, external launch witness) runs
    # FIRST because it binds the historical runtime code/config/result
    # identity independently of the local analyzer checkout.  Only after the
    # chain binds the run is artifact integrity recomputed and the runtime
    # identity resolved (exact local match vs posterior governed runtime).
    formal = docs["formal_run.json"]
    governed = docs["governance_receipt.json"]
    receipt = docs["receipt.json"]
    ledgers = docs["ledgers.json"]
    _verify_authorized_cell(row, authorized)
    if not isinstance(formal, dict) or formal.get("schema") != "leo-sim-formal-run/v1":
        raise V2AnalysisError(f"{run_id} formal witness schema mismatch")
    if any(formal.get(key) != expected for key, expected in {
            "run_id": run_id,
            "config_sha256": authorized.get("config_sha256"),
            "code_sha256": authorized.get("code_sha256"),
            "authorization_sha256": authorized.get("authorization_sha256"),
            "natural_end": True,
            "conservation_ok": True,
    }.items()):
        raise V2AnalysisError(f"{run_id} formal witness identity mismatch")
    if not isinstance(governed, dict):
        raise V2AnalysisError(f"{run_id} governance receipt schema mismatch")
    receipt_schema = receipt.get("schema")
    if receipt_schema == receipt_mod.RECEIPT_SCHEMA:
        expected_governance_schema = GOVERNANCE_SCHEMA_V2
    elif receipt_schema in {receipt_mod.LEGACY_RECEIPT_SCHEMA,
                            receipt_mod.LEGACY_RECEIPT_SCHEMA_V4}:
        # Historical v3/v4 artifacts remain readable, but never inherit the
        # v2 external witness contract by self-reporting extra fields.
        if require_external_witness:
            raise V2AnalysisError(
                f"{run_id} external-witness mode requires current receipt evidence")
        expected_governance_schema = GOVERNANCE_SCHEMA_V1
    else:
        raise V2AnalysisError(f"{run_id} receipt schema is not a supported formal branch")
    if governed.get("schema") != expected_governance_schema:
        raise V2AnalysisError(f"{run_id} governance receipt schema mismatch")
    if governed.get("run_id") != run_id or governed.get("research_eligible") is not True \
            or governed.get("verification_errors") != []:
        raise V2AnalysisError(f"{run_id} governance receipt is not eligible")
    if governed.get("authorization_sha256") != authorized.get("authorization_sha256"):
        raise V2AnalysisError(f"{run_id} governance authorization hash mismatch")
    if expected_governance_schema == GOVERNANCE_SCHEMA_V2:
        _verify_governance_identity(
            run_id, governed, authorized, expected_deployment)
    receipt_sha = file_sha256(paths["receipt.json"])
    if governed.get("run_receipt_sha256") != receipt_sha \
            or formal.get("receipt_sha256") != receipt_sha:
        raise V2AnalysisError(f"{run_id} receipt hash is not bound by witnesses")
    manifest_doc = docs["manifest.json"]
    result_identity = {
        "config_sha256": receipt.get("config_sha256"),
        "trace_identity_sha256": receipt.get("trace_identity_sha256"),
        "input_sha256": manifest_doc.get("input_sha256"),
        "trace_seed": receipt.get("seed"),
        "code_sha256": receipt.get("code_sha256"),
    }
    identity_mismatches = [
        key for key, actual in result_identity.items()
        if actual != authorized.get(key)
    ]
    if identity_mismatches:
        raise V2AnalysisError(
            f"{run_id} result identity mismatch: "
            + ", ".join(identity_mismatches))
    if expected_governance_schema == GOVERNANCE_SCHEMA_V2:
        expected_binding = {
            "receipt_schema": receipt_mod.RECEIPT_SCHEMA,
            "resolved_config_sha256": file_sha256(paths["resolved_config.json"]),
            "trace_manifest_schema": docs["manifest.json"].get("schema"),
            "trace_identity_contract": receipt.get("trace_identity_contract"),
            "trace_manifest_sha256": file_sha256(paths["manifest.json"]),
        }
        if any(governed.get(key) != value
               for key, value in expected_binding.items()):
            raise V2AnalysisError(f"{run_id} governance witness binding mismatch")
        external_witness = _verify_external_witness(
            witness_root=witness_root, run_id=run_id, formal=formal,
            governed=governed, paths=paths, authorized=authorized,
            external_witness_by_nonce=external_witness_by_nonce)
    else:
        external_witness = None
    # Artifact integrity: full recomputation of receipt/manifest/trace/
    # config/ledgers/fates/totals/conservation/mechanisms WITHOUT the
    # local==runtime gate.  Any artifact anomaly fails closed regardless of
    # the identity mode resolved below.
    artifact_errors, run_identity = (
        receipt_mod._verify_receipt_dir_artifacts_unbound(str(result_dir)))
    if artifact_errors:
        raise V2AnalysisError(
            f"{run_id} receipt verification failed: "
            f"{'; '.join(artifact_errors)}")
    # Runtime identity: is the local analyzer exactly the run-time code, or
    # a (newer) analyzer analyzing a governed historical runtime?  The
    # posterior mode is admitted ONLY because the formal chain above bound
    # the historical identity; legacy v3/v4 evidence has no external witness
    # and keeps the strict exact-runtime requirement.
    runtime_code = run_identity.get("code_sha256")
    runtime_deps = run_identity.get("deps")
    requested_learning = (receipt.get("mechanisms") or {}).get(
        "requested", {}).get("learning_algorithm")
    try:
        local_deps = receipt_mod.dependency_versions(
            with_tensorflow=requested_learning == "ddqn")
    except ImportError:
        # Posterior semantics never require the local host to reproduce
        # the historical runtime dependencies (e.g. a TF-less host
        # analyzing a historical DDQN run).  An unresolvable local
        # dependency set only means "exact cannot be proven": the
        # binding falls through to the governed chain below, and
        # legacy evidence without an external witness still fails
        # closed.
        local_deps = None
    if not (isinstance(runtime_code, str) and len(runtime_code) == 64
            and all(ch in "0123456789abcdef" for ch in runtime_code)):
        raise V2AnalysisError(f"{run_id} receipt code identity is malformed")
    # exact requires the LOCAL analyzer to reproduce the full run-time
    # identity; anything short (unresolvable or different local deps,
    # or a different local code sha) is posterior when the v5
    # external-witness chain bound the historical identity, and
    # rejection when it did not.
    if (local_deps is not None
            and runtime_code == receipt_mod.code_sha256()
            and runtime_deps == local_deps):
        runtime_identity_binding = RUNTIME_BINDING_EXACT
    elif external_witness is None:
        raise V2AnalysisError(
            f"{run_id} legacy receipt cannot be analyzed with a different "
            "analyzer/runtime identity (no external witness binding)")
    else:
        runtime_identity_binding = RUNTIME_BINDING_POSTERIOR
    diagnostics = _run_diagnostics(ledgers)
    metric = _metric_from_result(receipt, ledgers, primary)
    artifacts = [{
        "path": str(path.relative_to(root)),
        "sha256": file_sha256(path),
    } for path in paths.values()]
    if external_witness is not None:
        witness_path = _external_witness_path(
            witness_root, run_id,
            launch_nonce=(formal.get("launch_nonce")
                          if external_witness_by_nonce else None))
        artifacts.append({
            "path": str(witness_path.relative_to(root)),
            "sha256": file_sha256(witness_path),
        })
    return {
        "run_id": run_id,
        "arm_id": row.get("arm_id"),
        "pairing_key": row.get("pairing_key"),
        "seed": row.get("trace_seed"),
        "config_sha256": receipt.get("config_sha256"),
        "trace_sha256": receipt.get("trace_sha256"),
        "trace_identity_sha256": receipt.get("trace_identity_sha256"),
        "input_sha256": manifest_doc.get("input_sha256"),
        "code_sha256": receipt.get("code_sha256"),
        "controlled_signature": authorized.get("controlled_signature"),
        "primary_metric": metric,
        "diagnostics": diagnostics,
        "result_path": str(result_dir.relative_to(root)),
        "evidence_class": ("v2_external_witness" if external_witness is not None
                            else "legacy_v3_v4_internal_only"),
        "runtime_identity_binding": runtime_identity_binding,
        "runtime_deps": run_identity.get("deps"),
        "artifacts": artifacts,
    }


def _compute_planned_contrasts(
        results: list[dict[str, Any]], contrasts: list[dict[str, Any]],
        primary: str) -> list[dict[str, Any]]:
    """Compute each registered contrast only over its own arm pairs.

    A matrix may contain several independent pairing keys (for example one
    pair per offered-load tier).  An unrelated pair must not be treated as a
    missing arm for every contrast; only a pairing key that contains exactly
    one side of the requested contrast is malformed.
    """
    by_pair: dict[str, dict[str, dict[str, Any]]] = {}
    for result in results:
        by_pair.setdefault(result["pairing_key"], {})[result["arm_id"]] = result
    output: list[dict[str, Any]] = []
    for contrast in contrasts:
        left, right = contrast.get("left_arm"), contrast.get("right_arm")
        diffs: list[float] = []
        for pair_key in sorted(by_pair):
            pair = by_pair[pair_key]
            has_left, has_right = left in pair, right in pair
            if has_left != has_right:
                raise V2AnalysisError(
                    f"contrast {contrast.get('name')} missing arm at pair {pair_key}")
            if has_left:
                for field in ("trace_sha256", "trace_identity_sha256",
                              "input_sha256", "seed", "code_sha256",
                              "controlled_signature"):
                    if pair[left].get(field) != pair[right].get(field):
                        label = ("actual trace_sha256 mismatch" if field == "trace_sha256"
                                 else f"paired identity mismatch: {field}")
                        raise V2AnalysisError(
                            f"contrast {contrast.get('name')} {label} at pair {pair_key}")
                diffs.append(pair[left]["primary_metric"] -
                             pair[right]["primary_metric"])
        if not diffs:
            raise V2AnalysisError(f"contrast {contrast.get('name')} has no pairs")
        output.append({
            "name": contrast.get("name"), "left_arm": left, "right_arm": right,
            "metric": primary, "n_pairs": len(diffs),
            "differences": diffs, "mean_difference": sum(diffs) / len(diffs),
        })
    return output


def _validated_design_accounting(
        analysis_request: dict[str, Any],
        cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive design accounting and validate it when the compiler declared it.

    Historical sealed matrices predate the compiler field, but their cells
    still carry the resolved config digest needed for exact accounting.  The
    analyzer must therefore emit the derived counts for both old and new
    authorizations; otherwise exact re-executions in a historical matrix can
    still be mistaken for additional independent conditions.
    """
    declared = analysis_request.get("design_accounting")
    run_ids_by_config: dict[str, list[str]] = {}
    for cell in cells:
        config_sha = cell.get("config_sha256")
        run_id = cell.get("run_id")
        if (not isinstance(config_sha, str)
                or re.fullmatch(r"[0-9a-f]{64}", config_sha) is None
                or not isinstance(run_id, str) or not run_id):
            raise V2AnalysisError(
                "design accounting requires cell config SHA and run id")
        run_ids_by_config.setdefault(config_sha, []).append(run_id)
    expected = {
        "schema": "leo-sim-matrix-design-accounting/v1",
        "planned_cells": len(cells),
        "unique_resolved_configurations": len(run_ids_by_config),
        "exact_reexecution_cells": len(cells) - len(run_ids_by_config),
        "exact_reexecution_groups": [
            {"config_sha256": digest, "run_ids": sorted(run_ids)}
            for digest, run_ids in sorted(run_ids_by_config.items())
            if len(run_ids) > 1
        ],
        "independent_condition_rule": (
            "one independent condition per unique resolved config SHA256"),
    }
    if declared is not None and declared != expected:
        raise V2AnalysisError(
            "analysis request design accounting does not match matrix cells")
    return expected


def _verify_authorization_bound(
        root: Path, experiment_dir: Path, authorization_path: Path,
        strict_error: Exception) -> dict[str, Any]:
    """Bounded re-verification of a HISTORICAL authorization.

    The strict gate (``authorize_experiment.verify_authorization``) recomputes
    the entire authorization with CURRENT code identities: ``code_sha256``,
    the execution chain and config/trace identities are re-derived by
    today's checker, so a newer analyzer checkout can never match an
    authorization issued for an older runtime that already executed.
    Posterior analysis instead binds the authorization to its OWN immutable
    snapshots:

    * the payload seal (``payload_sha256``) proves the file is the exact
      issue-time artifact;
    * every path recorded in ``work_finalization`` and
      ``experiment_artifact_hashes`` must still exist and hash to the
      recorded value (tamper/edition check over the bound snapshots);
    * the authorized run/cell rows must be structurally complete with
      well-formed identity fields.

    No current-code identity is recomputed or trusted here.  This function
    is deliberately private and its return value is NOT an evidence
    verdict: ``analyze()`` still requires the full formal witness +
    governance receipt v2 + external launch witness + receipt cross-hash
    chain in ``_verify_result``, which rejects any identity that the
    historical witnesses do not bind.  Any snapshot drift fails closed with
    the original strict error preserved for the record.
    """
    root = Path(root).resolve()
    auth_path = Path(authorization_path).resolve()
    try:
        authorize_experiment.relative_project_path(root, auth_path)
    except authorize_experiment.AuthorizationError as exc:
        raise V2AnalysisError(
            f"authorization snapshot verification failed: {exc}"
        ) from strict_error
    auth = _read_json(auth_path)
    if auth.get("schema") != authorize_experiment.SCHEMA:
        raise V2AnalysisError(
            "authorization snapshot verification failed: unsupported "
            "authorization schema") from strict_error
    claimed_payload = auth.get("payload_sha256")
    unsigned = {key: value for key, value in auth.items()
                if key != "payload_sha256"}
    if claimed_payload != canonical_sha(unsigned):
        raise V2AnalysisError(
            "authorization snapshot verification failed: payload seal "
            "mismatch") from strict_error
    if auth.get("status") != "AUTHORIZED" \
            or not isinstance(auth.get("experiment_id"), str) \
            or not auth["experiment_id"]:
        raise V2AnalysisError(
            "authorization snapshot verification failed: status/identity "
            "is incomplete") from strict_error
    experiment_raw = auth.get("experiment_dir")
    if not isinstance(experiment_raw, str) or not experiment_raw:
        raise V2AnalysisError(
            "authorization snapshot verification failed: no experiment "
            "path") from strict_error
    try:
        bound_experiment_dir = root / experiment_raw
        authorize_experiment.relative_project_path(root, bound_experiment_dir)
    except (TypeError, ValueError,
            authorize_experiment.AuthorizationError) as exc:
        raise V2AnalysisError(
            "authorization snapshot verification failed: experiment path "
            f"is invalid: {exc}") from strict_error
    if bound_experiment_dir.resolve() != Path(experiment_dir).resolve():
        raise V2AnalysisError(
            "authorization snapshot verification failed: authorization is "
            "not for this experiment directory") from strict_error
    finalization = auth.get("work_finalization")
    finalization_raw = (finalization or {}).get("path")
    finalization_sha = (finalization or {}).get("sha256")
    if not isinstance(finalization_raw, str) or not isinstance(
            finalization_sha, str) or not _SHA256_HEX.fullmatch(
                finalization_sha):
        raise V2AnalysisError(
            "authorization snapshot verification failed: work "
            "finalization identity is incomplete") from strict_error
    try:
        finalization_path = root / finalization_raw
        authorize_experiment.relative_project_path(root, finalization_path)
    except (TypeError, ValueError,
            authorize_experiment.AuthorizationError) as exc:
        raise V2AnalysisError(
            "authorization snapshot verification failed: finalization "
            f"path is invalid: {exc}") from strict_error
    if finalization_path.is_symlink() or not finalization_path.is_file() \
            or file_sha256(finalization_path) != finalization_sha:
        raise V2AnalysisError(
            "authorization snapshot verification failed: work "
            "finalization no longer matches the bound snapshot"
        ) from strict_error
    artifact_hashes = auth.get("experiment_artifact_hashes")
    if not isinstance(artifact_hashes, dict):
        raise V2AnalysisError(
            "authorization snapshot verification failed: bound artifact "
            "hash map is missing") from strict_error
    for raw, digest in sorted(artifact_hashes.items()):
        if not isinstance(raw, str) or not isinstance(digest, str) \
                or not _SHA256_HEX.fullmatch(digest):
            raise V2AnalysisError(
                "authorization snapshot verification failed: bound "
                "artifact hash map is malformed") from strict_error
        try:
            path = root / raw
            authorize_experiment.relative_project_path(root, path)
        except (TypeError, ValueError,
                authorize_experiment.AuthorizationError) as exc:
            raise V2AnalysisError(
                "authorization snapshot verification failed: bound "
                f"artifact path is invalid: {raw}") from strict_error
        if path.is_symlink() or not path.is_file() \
                or file_sha256(path) != digest:
            raise V2AnalysisError(
                "authorization snapshot verification failed: bound "
                f"artifact no longer matches its recorded hash: {raw}"
            ) from strict_error
    rows = auth.get("authorized_cells") or auth.get("authorized_runs")
    if not isinstance(rows, list) or not rows or any(
            not isinstance(row, dict) for row in rows):
        raise V2AnalysisError(
            "authorization snapshot verification failed: authorized "
            "cohort is missing or malformed") from strict_error
    for row in rows:
        # Identity spine: every authorized run must at least carry a
        # run id, runtime kind, arm/pairing anatomy and the two hashes
        # the witness chain will cross-bind (config + code).  The
        # remaining identity fields are re-checked when present; their
        # authoritative binding happens in _verify_result against the
        # formal witness / governance receipt / external witness, so a
        # structurally weaker row can never bypass that chain.
        missing = [key for key in (
            "run_id", "runtime_kind", "arm_id", "pairing_key",
            "config_sha256", "code_sha256",
        ) if not isinstance(row.get(key), str) or not row.get(key)]
        if missing:
            raise V2AnalysisError(
                "authorization snapshot verification failed: authorized "
                f"row {row.get('run_id')} lacks bound identity: "
                + ", ".join(missing)) from strict_error
        if not _SHA256_HEX.fullmatch(row["config_sha256"]) \
                or not _SHA256_HEX.fullmatch(row["code_sha256"]):
            raise V2AnalysisError(
                "authorization snapshot verification failed: authorized "
                f"row {row.get('run_id')} has a malformed identity hash"
            ) from strict_error
        for optional_key in ("trace_identity_sha256", "input_sha256",
                             "controlled_signature"):
            value = row.get(optional_key)
            # None/"" mean the identity is left to the witness chain to
            # bind (or reject); any concrete value must be well-formed.
            if value not in (None, "") and (
                    not isinstance(value, str)
                    or not _SHA256_HEX.fullmatch(value)):
                raise V2AnalysisError(
                    "authorization snapshot verification failed: "
                    f"authorized row {row.get('run_id')} has a malformed "
                    f"{optional_key}") from strict_error
        execution_chain = row.get("execution_chain_sha256")
        if execution_chain is not None and (
                not isinstance(execution_chain, dict) or not execution_chain
                or any(not isinstance(chain_raw, str) or not isinstance(
                    chain_digest, str)
                       or not _SHA256_HEX.fullmatch(chain_digest)
                       for chain_raw, chain_digest
                       in execution_chain.items())):
            raise V2AnalysisError(
                "authorization snapshot verification failed: authorized "
                f"row {row.get('run_id')} execution chain is malformed"
            ) from strict_error
    return auth


def analyze(root: Path, experiment_dir: Path, authorization_path: Path,
            results_root: Path | None = None,
            external_witness_root: Path | None = None,
            *, allow_legacy_internal: bool = False,
            authorization_mode: str = "auto") -> dict[str, Any]:
    """Verify an authorized V2 cohort and return a persisted analysis manifest.

    ``authorization_mode``: ``"auto"`` (default) tries the strict recomputed
    authorization gate first and falls back to the bound snapshot
    verification only when the strict gate rejects a complete, sealed
    historical authorization; ``"strict"`` never falls back.
    """
    root = Path(root).resolve()
    experiment_dir = Path(experiment_dir).resolve()
    results_root = (Path(results_root) if results_root is not None
                    else root / "CODE" / "Results").resolve()
    external_witness_root = (Path(external_witness_root)
                             if external_witness_root is not None
                             else results_root / "_external_launch_witness").resolve()
    try:
        external_witness_root.relative_to(root)
    except ValueError as exc:
        raise V2AnalysisError("external witness root must be inside analysis root") from exc
    request = _read_json(experiment_dir / "request.json")
    matrix = _read_json(experiment_dir / "run-manifest.json")
    analysis_request = _read_json(experiment_dir / "analysis-request.json")
    if matrix.get("schema") != MATRIX_SCHEMA or analysis_request.get("schema") != ANALYSIS_SCHEMA:
        raise V2AnalysisError("V2 matrix/analysis schema mismatch")
    if request.get("experiment_id") != matrix.get("experiment_id") \
            or analysis_request.get("experiment_id") != matrix.get("experiment_id"):
        raise V2AnalysisError("V2 experiment identity mismatch")
    if authorization_mode not in ("auto", "strict"):
        raise V2AnalysisError(
            f"unsupported authorization mode: {authorization_mode}")
    try:
        authorization = authorize_experiment.verify_authorization(
            root, Path(authorization_path))
        authorization_verification = AUTHORIZATION_VERIFICATION_STRICT
        authorization_strict_error = None
    except Exception as strict_exc:
        if authorization_mode == "strict":
            raise V2AnalysisError(
                f"authorization verification failed: {strict_exc}"
            ) from strict_exc
        authorization = _verify_authorization_bound(
            root, experiment_dir, Path(authorization_path), strict_exc)
        authorization_verification = AUTHORIZATION_VERIFICATION_BOUND
        authorization_strict_error = str(strict_exc)
    if authorization.get("status") != "AUTHORIZED" \
            or authorization.get("experiment_id") != matrix.get("experiment_id"):
        raise V2AnalysisError("authorization is not for this V2 experiment")
    cells = matrix.get("cells")
    planned_ids = analysis_request.get("planned_run_ids")
    if not isinstance(cells, list) or not isinstance(planned_ids, list) \
            or planned_ids != [cell.get("run_id") for cell in cells]:
        raise V2AnalysisError("analysis cohort does not exactly match matrix cells")
    design_accounting = _validated_design_accounting(
        analysis_request, cells)
    authorized_rows = authorization.get("authorized_cells") or authorization.get("authorized_runs")
    if not isinstance(authorized_rows, list):
        raise V2AnalysisError("authorization has no authorized V2 cohort")
    authorization_sha256 = file_sha256(Path(authorization_path))
    auth_by_id = {
        row.get("run_id"): {**row, "authorization_sha256": authorization_sha256}
        for row in authorized_rows if isinstance(row, dict)
    }
    if set(auth_by_id) != set(planned_ids):
        raise V2AnalysisError("authorization cohort differs from matrix cohort")
    primary = analysis_request.get("analysis", {}).get("primary_metric")
    if not isinstance(primary, str) or not primary:
        raise V2AnalysisError("V2 primary metric is missing")
    analyzer = _analyzer_identity()
    results = [
        _verify_result(root, results_root, external_witness_root, cell,
                       auth_by_id[cell["run_id"]], primary,
                       require_external_witness=not allow_legacy_internal)
        for cell in cells
    ]
    evidence_classes = {result["evidence_class"] for result in results}
    if len(evidence_classes) > 1:
        raise V2AnalysisError("analysis cohort mixes v5 external and v3/v4 legacy evidence")
    legacy_only = evidence_classes == {"legacy_v3_v4_internal_only"}
    runtime_bindings = {
        result["runtime_identity_binding"] for result in results}
    if len(runtime_bindings) > 1:
        raise V2AnalysisError(
            "analysis cohort mixes exact-runtime and posterior-governed "
            "evidence")
    posterior = runtime_bindings == {RUNTIME_BINDING_POSTERIOR}
    contrasts = analysis_request.get("analysis", {}).get("planned_contrasts", [])
    output_contrasts = _compute_planned_contrasts(results, contrasts, primary)
    input_paths = [experiment_dir / name for name in (
        "request.json", "run-manifest.json", "analysis-request.json")]
    input_paths.append(Path(authorization_path).resolve())
    for result in results:
        input_paths.extend(root / item["path"] for item in result["artifacts"])
    inputs = {str(path.relative_to(root)): file_sha256(path)
              for path in input_paths}
    manifest = {
        "schema": SCHEMA,
        "status": "VERIFIED",
        "errors": [],
        "experiment_id": matrix["experiment_id"],
        "analysis_id": analysis_request["analysis"].get("analysis_id"),
        "primary_metric": primary,
        "verified_run_ids": planned_ids,
        "run_results": results,
        "planned_contrasts": output_contrasts,
        "claim_boundary": request.get("claim_boundary", {}),
        "inputs": inputs,
        "authorization_sha256": authorization_sha256,
        "analyzer": analyzer,
        "analysis_mode": (ANALYSIS_MODE_POSTERIOR if posterior
                          else ("legacy_internal" if allow_legacy_internal
                                else "current_external_witness")),
        "authorization_verification": authorization_verification,
        "authorization_strict_error": authorization_strict_error,
        "experiment_dir": str(experiment_dir.relative_to(root)),
        "authorization_path": str(Path(authorization_path).resolve().relative_to(root)),
        "results_root": str(results_root.relative_to(root)),
        "external_witness_root": str(external_witness_root.relative_to(root)),
        "claim_status": ("LEGACY_INTERNAL_ONLY" if legacy_only
                          else "READY_FOR_INDEPENDENT_CLAIM_REVIEW"),
    }
    manifest["design_accounting"] = design_accounting
    return manifest


def write_outputs(root: Path, out_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    root = Path(root).resolve()
    out_dir = Path(out_dir).resolve()
    if out_dir.exists() and (out_dir.is_symlink() or not out_dir.is_dir()
                             or any(out_dir.iterdir())):
        raise V2AnalysisError("analysis output directory must be new or empty")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "schema": "leo-sim-v2-analysis-summary/v1",
        "experiment_id": manifest["experiment_id"],
        "primary_metric": manifest["primary_metric"],
        "planned_contrasts": manifest["planned_contrasts"],
        "claim_status": manifest["claim_status"],
    }
    if "design_accounting" in manifest:
        summary["design_accounting"] = manifest["design_accounting"]
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = [
        "# leo_sim V2 paired analysis", "",
        f"- status: `{manifest['status']}`",
        f"- primary metric: `{manifest['primary_metric']}`",
        f"- verified runs: `{len(manifest['verified_run_ids'])}`", "",
    ]
    if "design_accounting" in manifest:
        accounting = manifest["design_accounting"]
        report.extend([
            "## Design accounting", "",
            f"- planned cells: `{accounting['planned_cells']}`",
            f"- unique resolved configurations: "
            f"`{accounting['unique_resolved_configurations']}`",
            f"- exact re-execution cells: "
            f"`{accounting['exact_reexecution_cells']}`",
            "- exact re-executions are repeatability evidence, not additional "
            "independent conditions", "",
        ])
    report.extend(["## Run diagnostics", ""])
    for result in manifest["run_results"]:
        diagnostics = result["diagnostics"]
        mcs = diagnostics["mcs"]
        control = diagnostics["control"]
        isl = diagnostics["isl"]
        saturated = ", ".join(isl["saturated_link_ids"]) or "none"
        report.extend([
            f"### {result['run_id']}", "",
            f"- MCS samples: `{mcs['rate_samples']}`; MCS zero-rate holds: "
            f"`{mcs['zero_rate_holds']}`; rate range: "
            f"`{mcs['rate_min_bps']}`–`{mcs['rate_max_bps']}` bps",
            f"- control registered/completed: `{control.get('registered')}`/"
            f"`{control.get('transmission_completed')}`",
            f"- directed ISL links: `{isl['link_count']}`; "
            f"saturated directed ISL links: `{saturated}`",
            f"- 1 s active-window p99/max utilization: "
            f"`{diagnostics['windowed_isl']['active_window_utilization_p99']}`/"
            f"`{diagnostics['windowed_isl']['max_window_utilization']}`; "
            f"sustained hotspot links: "
            f"`{diagnostics['windowed_isl']['sustained_hotspot_link_ids']}`",
            f"- episode-coincident pressure-candidate links: "
            f"`{diagnostics['windowed_isl']['pressure_candidate_link_ids']}`",
            f"- drain residue packets/unmatched ISL queue entries: "
            f"`{diagnostics['drain']['in_system_at_stop_packets']}`/"
            f"`{diagnostics['drain']['unmatched_isl_queue_entries']}`",
            f"- matched/unmatched ISL queue entries: "
            f"`{diagnostics['windowed_isl']['matched_isl_queue_entries']}`/"
            f"`{diagnostics['windowed_isl']['unmatched_isl_queue_entries']}`", "",
        ])
    report.extend([
        "This output is evidence-bound analysis, not a paper claim.",
        "Independent claim-support and value-gate review remains required.",
    ])
    (out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    persisted = dict(manifest)
    persisted["output_hashes"] = {
        "summary.json": file_sha256(out_dir / "summary.json"),
        "report.md": file_sha256(out_dir / "report.md"),
    }
    persisted["output_artifacts"] = [
        {"path": str((out_dir / name).relative_to(root)), "sha256": digest}
        for name, digest in persisted["output_hashes"].items()
    ]
    (out_dir / "analysis-manifest.json").write_text(
        json.dumps(persisted, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    claim_gate = {
        "schema": CLAIM_GATE_SCHEMA,
        "status": manifest["claim_status"],
        "analysis_manifest": str((out_dir / "analysis-manifest.json").relative_to(root)),
        "analysis_manifest_sha256": file_sha256(out_dir / "analysis-manifest.json"),
        "cannot_claim": manifest.get("claim_boundary", {}).get("cannot_claim", []),
    }
    (out_dir / "claim-gate.json").write_text(
        json.dumps(claim_gate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return persisted


def verify_persisted_analysis(root: Path, manifest_path: Path) -> tuple[bool, list[str]]:
    """Verify the output hashes and every bound input without trusting report text."""
    errors: list[str] = []
    root = Path(root).resolve()
    manifest_path = Path(manifest_path).resolve()
    try:
        manifest = _read_json(manifest_path)
        if manifest.get("schema") != SCHEMA or manifest.get("status") != "VERIFIED":
            raise V2AnalysisError("analysis manifest is not VERIFIED")
        if manifest.get("analyzer") != _analyzer_identity():
            raise V2AnalysisError("analysis manifest analyzer identity mismatch")
        inputs = manifest.get("inputs")
        if not isinstance(inputs, dict) or any(
                not isinstance(raw, str) or not raw
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
                for raw, digest in inputs.items()):
            raise V2AnalysisError("analysis manifest inputs contract is invalid")
        for raw, digest in inputs.items():
            path = (root / raw).resolve(strict=True)
            path.relative_to(root)
            if file_sha256(path) != digest:
                raise V2AnalysisError(f"input hash mismatch: {raw}")
        output_dir = manifest_path.parent
        output_hashes = manifest.get("output_hashes")
        if not isinstance(output_hashes, dict) or set(output_hashes) != {
                "summary.json", "report.md"}:
            raise V2AnalysisError("analysis output hash contract is incomplete")
        expected_output_artifacts = {
            str((output_dir / name).relative_to(root)): digest
            for name, digest in output_hashes.items()
        }
        output_artifacts = manifest.get("output_artifacts")
        if not isinstance(output_artifacts, list) or any(
                not isinstance(item, dict)
                or set(item) != {"path", "sha256"}
                for item in output_artifacts):
            raise V2AnalysisError("analysis output artifact contract is inconsistent")
        actual_output_artifacts = {
            item["path"]: item["sha256"] for item in output_artifacts
        }
        if len(actual_output_artifacts) != len(output_artifacts) \
                or actual_output_artifacts != expected_output_artifacts:
            raise V2AnalysisError("analysis output artifact contract is inconsistent")
        for name, digest in output_hashes.items():
            path = output_dir / name
            if file_sha256(path) != digest:
                raise V2AnalysisError(f"output hash mismatch: {name}")
        gate = _read_json(output_dir / "claim-gate.json")
        expected_gate = {
            "schema": CLAIM_GATE_SCHEMA,
            "status": manifest.get("claim_status"),
            "analysis_manifest": str(manifest_path.relative_to(root)),
            "analysis_manifest_sha256": file_sha256(manifest_path),
            "cannot_claim": manifest.get("claim_boundary", {}).get(
                "cannot_claim", []),
        }
        if gate != expected_gate:
            raise V2AnalysisError("claim gate differs from analysis manifest")
        summary = _read_json(output_dir / "summary.json")
        expected_summary = {
            "schema": "leo-sim-v2-analysis-summary/v1",
            "experiment_id": manifest.get("experiment_id"),
            "primary_metric": manifest.get("primary_metric"),
            "planned_contrasts": manifest.get("planned_contrasts"),
            "claim_status": manifest.get("claim_status"),
        }
        if "design_accounting" in manifest:
            expected_summary["design_accounting"] = \
                manifest["design_accounting"]
        if summary != expected_summary:
            raise V2AnalysisError("analysis summary differs from manifest")
        analysis_mode = manifest.get("analysis_mode")
        if analysis_mode not in {"current_external_witness", "legacy_internal",
                                 ANALYSIS_MODE_POSTERIOR}:
            raise V2AnalysisError("analysis manifest mode is invalid")
        recomputed = analyze(
            root,
            root / manifest["experiment_dir"],
            root / manifest["authorization_path"],
            root / manifest["results_root"],
            root / manifest["external_witness_root"],
            allow_legacy_internal=(analysis_mode == "legacy_internal"),
        )
        for key in ("experiment_id", "primary_metric", "verified_run_ids",
                    "run_results", "planned_contrasts", "claim_boundary",
                    "inputs", "authorization_sha256", "analyzer",
                    "analysis_mode", "authorization_verification",
                    "authorization_strict_error", "claim_status",
                    "design_accounting"):
            if recomputed.get(key) != manifest.get(key):
                raise V2AnalysisError(f"persisted analysis differs for {key}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    return not errors, errors


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verify/analyze leo_sim_v2 matrix results")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--external-witness-root", type=Path)
    parser.add_argument(
        "--allow-legacy-internal", action="store_true",
        help="opt in to diagnostic-only v3/v4 evidence without external witness")
    parser.add_argument(
        "--authorization-mode", choices=("auto", "strict"), default="auto",
        help="strict=never fall back; auto=fall back to bound snapshot "
             "verification for sealed historical authorizations (default)")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = analyze(args.root, args.experiment, args.authorization,
                           args.results_root, args.external_witness_root,
                           allow_legacy_internal=args.allow_legacy_internal,
                           authorization_mode=args.authorization_mode)
        write_outputs(args.root, args.out, manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"V2 ANALYSIS BLOCKED: {exc}")
        return 2
    print(json.dumps({
        "status": manifest["status"],
        "experiment_id": manifest["experiment_id"],
        "verified_runs": len(manifest["verified_run_ids"]),
        "claim_status": manifest["claim_status"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
