"""Local acceptance of a PULLED-BACK result directory.

WHY THIS EXISTS
===============
The formal chain verifies a run on the VM, at the moment it finishes, over the
files on the VM's own filesystem.  That verdict is not transferable: the pull
that brings the results home (CODE/scripts/remote/pull-results-remote.sh) is a
tar over ssh that copies a fixed list of files and, by construction, tolerates
files that are not there (add_file returns silently when a path is absent).  So
"the VM verified it" and "the copy I am holding is the copy that was verified"
are two different statements, and only the second one is about the artifacts an
analysis will actually read.

WHAT THIS GATE IS ALLOWED TO CONCLUDE
=====================================
Passing it means, and only means:

  * the required artifact set is present in this copy;
  * every hash the receipt binds still matches the files on disk;
  * the stream contract holds, with v6 digests recomputed HERE;
  * IF the caller declared the copy formal: the formal witness and the
    governance receipt carry their full contract key sets, their bindings
    agree with this copy, and the governance receipt is internally sealed by
    its own payload hash.

It does NOT establish formal analysis eligibility, and therefore it NEVER
reports a positive claimability verdict.  Eligibility requires evidence this
gate cannot see: the authorization cohort that binds the run cell, the
deployment receipt, the external launch witness pulled from the VM runtime, and
the run-time identity chain.  Those are checked by the existing authority,
CODE/experiment_platform/v2_analysis.py; this module reports
formal_eligibility.status = NOT_EVALUATED and names exactly what is missing.
An earlier revision returned claimable=true for a hand-written directory whose
witness files held six fields between them (independent review, 2026-09-25);
that verdict was withdrawn.

THE CALLER DECLARES WHAT THE COPY IS
====================================
expect is REQUIRED and is "formal" or "diagnostic".  It is never inferred from
which files happen to be present, because a missing witness would otherwise
turn a formal result into an accepted diagnostic one -- the pull tolerates
absent files, so absence is exactly the failure mode this gate exists to catch.
The declaration is enforced in both directions: expect="formal" fails if the
formal artifacts are missing or abbreviated, and expect="diagnostic" fails if
the copy carries formal artifacts at all.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import receipt as receipt_mod

SCHEMA = "leo-sim-pullback-verification/v1"

#: leo_sim_v2 core result files.  Written by the run CLI itself, so a pulled
#: result set missing any of them cannot be analyzed at all.
REQUIRED_CORE_FILES = ("receipt.json", "resolved_config.json", "manifest.json",
                       "trace.csv", "ledgers.json")

FORMAL_WITNESS = "formal_run.json"
GOVERNANCE_RECEIPT = "governance_receipt.json"
#: Where pull-results-remote.sh drops the launch witness, relative to the run
#: directory (EXPERIMENTS/contracts/run-artifact-contract.md).
EXTERNAL_WITNESS_DIRNAME = "_external_launch_witness"

EXPECT_FORMAL = "formal"
EXPECT_DIAGNOSTIC = "diagnostic"
EXPECTATIONS = (EXPECT_FORMAL, EXPECT_DIAGNOSTIC)

#: Exact key set of the formal witness, mirroring __main__._write_formal_witness.
#: A "simplified" witness is not a weaker witness, it is a different object: the
#: binding fields are the whole content.  Kept in step with the producer by
#: tests/test_pullback_authority.py, which writes a real witness and compares.
FORMAL_WITNESS_SCHEMA = "leo-sim-formal-run/v1"
FORMAL_WITNESS_KEYS = frozenset({
    "schema", "run_id", "launch_nonce", "authorization_sha256", "config_sha256",
    "code_sha256", "receipt_sha256", "natural_end", "conservation_ok",
})
FORMAL_WITNESS_OPTIONAL_KEYS = frozenset({"timeline_log_sha256"})

#: Exact key set of the V2 governance receipt, mirroring
#: remote_job.build_v2_governance_receipt.  Same reasoning, same test.
#: Kept in step with the two producers by construction: _formal_contract()
#: returns v2_analysis, which defines this exact string, and the producer
#: remote_job.V2_GOVERNANCE_SCHEMA is asserted equal to it in
#: tests/test_pullback_authority.py.  The first version of this file spelled it
#: "leo-sim-v2-governance-receipt/v2" and every local fixture agreed with the
#: typo, so only a real VM result exposed it (2026-09-25).
GOVERNANCE_SCHEMA_V2 = "leo-sim-governance-receipt/v2"
GOVERNANCE_KEYS_V2 = frozenset({
    "schema", "research_eligible", "run_id", "launch_nonce",
    "authorization_sha256", "source_git_commit", "source_tree_sha256",
    "deployment_receipt_sha256", "execution_chain_sha256", "acceptance",
    "run_receipt_sha256", "natural_end", "conservation_ok",
    "verification_errors", "receipt_schema", "resolved_config_sha256",
    "trace_manifest_schema", "trace_identity_contract",
    "trace_manifest_sha256", "payload_sha256",
})

#: Governance fields that must agree with the copy being verified.
GOVERNANCE_BINDINGS = {
    "run_receipt_sha256": "receipt.json",
    "resolved_config_sha256": "resolved_config.json",
    "trace_manifest_sha256": "manifest.json",
}

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class PullbackError(ValueError):
    """The directory cannot be verified as it stands."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise PullbackError(f"{label} is missing or symbolic: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PullbackError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise PullbackError(f"{label} must be a JSON object")
    return value


def _formal_contract():
    """The existing formal-analysis vocabulary, imported where it lives.

    Reused rather than restated so the witness contract cannot drift: these are
    the same constants v2_analysis verifies against.  The import is lazy because
    this module is also used by the run CLI, which must not pull the whole
    analysis package in to check a directory.
    """
    from CODE.experiment_platform import v2_analysis
    return v2_analysis


def _key_set_errors(label: str, value: dict, required: frozenset,
                    optional: frozenset = frozenset()) -> list[str]:
    keys = set(value)
    unknown = sorted(keys - required - optional)
    missing = sorted(required - keys)
    errors = []
    if missing:
        errors.append(f"{label} is missing contract fields {missing}")
    if unknown:
        errors.append(f"{label} has unknown fields {unknown}")
    return errors


def _hex_or_error(label: str, value, pattern: re.Pattern) -> list[str]:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        return [f"{label} is not a well-formed digest"]
    return []


def _formal_increment_errors(run_dir: Path, receipt: dict,
                             witness_root: Path | None) -> tuple[list[str], dict]:
    """Check the formal increment as far as a standalone copy allows."""
    errors: list[str] = []
    not_checked: list[str] = []
    formal_path = run_dir / FORMAL_WITNESS
    governed_path = run_dir / GOVERNANCE_RECEIPT
    try:
        formal = _read_json(formal_path, FORMAL_WITNESS)
        governed = _read_json(governed_path, GOVERNANCE_RECEIPT)
    except PullbackError as exc:
        return [str(exc)], not_checked

    v2 = _formal_contract()
    errors.extend(_key_set_errors(FORMAL_WITNESS, formal, FORMAL_WITNESS_KEYS,
                                  FORMAL_WITNESS_OPTIONAL_KEYS))
    errors.extend(_key_set_errors(GOVERNANCE_RECEIPT, governed,
                                  GOVERNANCE_KEYS_V2))
    if formal.get("schema") != FORMAL_WITNESS_SCHEMA:
        errors.append(f"{FORMAL_WITNESS} schema mismatch: {formal.get('schema')!r}")
    if governed.get("schema") != GOVERNANCE_SCHEMA_V2:
        errors.append(
            f"{GOVERNANCE_RECEIPT} schema mismatch: {governed.get('schema')!r}")
    if formal.get("run_id") != run_dir.name:
        errors.append(f"{FORMAL_WITNESS} run_id does not name this directory")
    if governed.get("run_id") != run_dir.name:
        errors.append(f"{GOVERNANCE_RECEIPT} run_id does not name this directory")
    for label, value in (("formal_run.launch_nonce", formal.get("launch_nonce")),
                         ("governance.launch_nonce", governed.get("launch_nonce"))):
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
            errors.append(f"{label} is not a 32-character lowercase hex nonce")
    if governed.get("launch_nonce") != formal.get("launch_nonce"):
        errors.append("governance and formal witness launch nonces disagree")
    if governed.get("authorization_sha256") != formal.get("authorization_sha256"):
        errors.append("governance and formal witness authorization bindings disagree")

    # The governance receipt must seal itself: payload_sha256 is the canonical
    # hash of every other field, exactly as the producer computes it.
    claimed = governed.get("payload_sha256")
    unsigned = {key: value for key, value in governed.items()
                if key != "payload_sha256"}
    if claimed != v2.canonical_sha(unsigned):
        errors.append("governance_receipt.payload_sha256 does not seal its own "
                      "content")

    if governed.get("research_eligible") is not True:
        errors.append("governance_receipt.research_eligible is not true")
    if governed.get("verification_errors") != []:
        errors.append("governance_receipt.verification_errors is not empty: "
                      f"{governed.get('verification_errors')!r}")
    for label, value in (
            ("formal_run.natural_end", formal.get("natural_end")),
            ("formal_run.conservation_ok", formal.get("conservation_ok")),
            ("governance.natural_end", governed.get("natural_end")),
            ("governance.conservation_ok", governed.get("conservation_ok"))):
        if value is not True:
            errors.append(f"{label} is not true")

    receipt_sha = _sha256(run_dir / "receipt.json")
    if formal.get("receipt_sha256") != receipt_sha:
        errors.append(f"{FORMAL_WITNESS} receipt_sha256 does not match this "
                      f"copy's receipt.json")
    for field, name in sorted(GOVERNANCE_BINDINGS.items()):
        path = run_dir / name
        if path.is_symlink() or not path.is_file():
            errors.append(f"governance_receipt.{field}: {name} is missing")
            continue
        actual = _sha256(path)
        if governed.get(field) != actual:
            errors.append(f"governance_receipt.{field} does not match this "
                          f"copy's {name}")

    if governed.get("receipt_schema") != receipt.get("schema"):
        errors.append("governance_receipt.receipt_schema disagrees with the copy")
    if governed.get("trace_identity_contract")             != receipt.get("trace_identity_contract"):
        errors.append(
            "governance_receipt.trace_identity_contract disagrees with the copy")
    errors.extend(_hex_or_error("formal_run.config_sha256",
                                formal.get("config_sha256"), _SHA256_HEX))
    errors.extend(_hex_or_error("formal_run.code_sha256",
                                formal.get("code_sha256"), _SHA256_HEX))
    errors.extend(_hex_or_error("governance.source_tree_sha256",
                                governed.get("source_tree_sha256"), _SHA256_HEX))
    errors.extend(_hex_or_error("governance.deployment_receipt_sha256",
                                governed.get("deployment_receipt_sha256"),
                                _SHA256_HEX))
    errors.extend(_hex_or_error("governance.source_git_commit",
                                governed.get("source_git_commit"), _GIT_COMMIT))
    if not isinstance(governed.get("execution_chain_sha256"), dict) \
            or not governed["execution_chain_sha256"]:
        errors.append("governance_receipt.execution_chain_sha256 is not a "
                      "non-empty mapping")
    if not isinstance(governed.get("acceptance"), dict):
        errors.append("governance_receipt.acceptance is not a mapping")

    # External launch witness: checked only when the caller says where the pull
    # put it.  Its absence is NOT inferred from the file being missing.
    if witness_root is None:
        not_checked.append(
            "external launch witness (no --witness-root supplied; the witness "
            "is pulled next to the results, not inside the run directory)")
    else:
        errors.extend(_external_witness_errors(
            Path(witness_root), run_dir, governed, not_checked))
    not_checked.extend([
        "authorization cohort binding (v2_analysis, needs the compiled "
        "experiment directory and authorization.json)",
        "deployment identity binding (needs the deployment receipt)",
        "run-time identity chain / research eligibility verdict "
        "(CODE/experiment_platform/v2_analysis.py)",
    ])
    return errors, not_checked


def _external_witness_errors(witness_root: Path, run_dir: Path, governed: dict,
                             not_checked: list[str]) -> list[str]:
    v2 = _formal_contract()
    nonce = governed.get("launch_nonce")
    if not isinstance(nonce, str) or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        return ["external launch witness not checked: governance launch nonce "
                "is malformed"]
    if witness_root.is_symlink() or not witness_root.is_dir():
        return [f"external launch witness directory is missing or unsafe: "
                f"{witness_root}"]
    # The pull names the witness after the RUN ID (pull-results-remote.sh
    # fetches .remote_runtime/launches/<nonce>.json into
    # Results/_external_launch_witness/<run_id>.json), while v2_analysis can
    # also address it by nonce.  Both spellings are accepted; the first
    # version only looked for the nonce and therefore rejected every real
    # pulled result (2026-09-25).
    candidates = [witness_root / f"{run_dir.name}.json",
                  witness_root / f"{nonce}.json"]
    path = next((c for c in candidates
                 if c.is_file() and not c.is_symlink()), None)
    if path is None:
        return [f"external launch witness is missing: tried "
                + ", ".join(str(c) for c in candidates)]
    try:
        witness = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"external launch witness is unreadable: {exc}"]
    errors = []
    if not isinstance(witness, dict) or witness.get("schema") \
            != v2.EXTERNAL_STATUS_SCHEMA:
        errors.append("external launch witness schema mismatch")
        return errors
    if witness.get("status") != "success" or witness.get("exit_code") != 0:
        errors.append("external launch witness is not a successful launch")
    if witness.get("launch_nonce") != nonce \
            or witness.get("run_id") != run_dir.name:
        errors.append("external launch witness identity does not name this run")
    if witness.get("governance_receipt_sha256") \
            != _sha256(run_dir / GOVERNANCE_RECEIPT):
        errors.append("external launch witness does not bind this copy's "
                      "governance receipt")
    expected_fields = {key: governed.get(key)
                       for key in v2.GOVERNANCE_WITNESS_FIELDS}
    if witness.get("governance_witness") != expected_fields:
        errors.append("external launch witness governance binding mismatch")
    not_checked.append(
        "external launch witness VM-side fields (authorization hash, "
        "canonical results path) are not re-derivable from a local copy")
    return errors


def verify_pulled_run(run_dir: str | Path, *, expect: str,
                      witness_root: str | Path | None = None) -> dict:
    """Verify one pulled-back run directory.

    expect (REQUIRED) is "formal" or "diagnostic": the caller states what the
    copy is supposed to be.  It is never derived from the files present, so a
    formal witness lost in transit cannot be accepted as a diagnostic copy.

    Never raises for a bad result set: every failure is a failed check with its
    reason, so a caller can always tell "verified here" from "could not verify".
    claimable is ALWAYS false; see the module docstring.
    """
    if expect not in EXPECTATIONS:
        raise PullbackError(
            f"expect must be one of {list(EXPECTATIONS)}, got {expect!r}")
    run_dir = Path(run_dir)
    if run_dir.is_symlink():
        raise PullbackError(f"run directory may not be symbolic: {run_dir}")
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise PullbackError(f"run directory does not exist: {run_dir}")
    witness_root = None if witness_root is None else Path(witness_root)

    formal_present = any(
        (run_dir / name).is_file() and not (run_dir / name).is_symlink()
        for name in (FORMAL_WITNESS, GOVERNANCE_RECEIPT))
    require_streams = expect == EXPECT_FORMAL

    checks: list[dict] = []
    errors: list[str] = []

    def check(cid: str, statement: str, ok: bool, **detail) -> None:
        checks.append({"id": cid, "statement": statement, "ok": bool(ok),
                       **detail})
        if not ok:
            errors.extend(detail.get("errors") or [detail.get("why", cid)])

    missing = [name for name in REQUIRED_CORE_FILES
               if (run_dir / name).is_symlink()
               or not (run_dir / name).is_file()]
    check("P1", "every required core result file is present", not missing,
          missing=missing,
          errors=[f"missing or symbolic core result file: {name}"
                  for name in missing])

    receipt = None
    try:
        receipt = _read_json(run_dir / "receipt.json", "receipt.json")
    except PullbackError as exc:
        check("P2", "receipt.json is a readable JSON object", False, why=str(exc))
    else:
        schema = receipt.get("schema")
        ok = schema in receipt_mod.RECEIPT_SCHEMAS_V5_FAMILY
        check("P2", "the receipt schema is one this checkout can verify", ok,
              schema=schema, why=f"unsupported receipt schema: {schema!r}")

    artifact_errors: list[str] = []
    if receipt is not None and not missing:
        artifact_errors, _identity = receipt_mod.verify_run_artifacts(str(run_dir))
    check("P3", "every hash the receipt binds matches the files on disk",
          not artifact_errors and not missing and receipt is not None,
          errors=artifact_errors,
          why="artifact set incomplete; integrity not evaluated")

    schema = None if receipt is None else receipt.get("schema")
    if schema == receipt_mod.RECEIPT_SCHEMA_V6:
        binding = "v6 digests recomputed from this copy"
    elif schema is not None:
        binding = ("v5: no digest is bound, so the downgrade is checked for "
                   "contradiction"
                   + (" and required to be the empty-decision case"
                      if require_streams else
                      " (streams not required for a diagnostic copy)"))
    else:
        binding = "not evaluated: no readable receipt"
    stream_errors = receipt_mod.verify_receipt_streams(
        str(run_dir), require_streams=require_streams)
    check("P4",
          "the stream contract holds (v6 digests recomputed HERE, not "
          "trusted from the VM run)",
          not stream_errors, expect=expect, require_streams=require_streams,
          stream_binding=binding, errors=stream_errors)

    not_checked: list[str] = []
    if expect == EXPECT_FORMAL:
        formal_errors, not_checked = _formal_increment_errors(
            run_dir, receipt or {}, witness_root)
        check("P5",
              "the formal increment is complete, contract-shaped and agrees "
              "with this copy",
              not formal_errors, errors=formal_errors,
              witness_root=None if witness_root is None else str(witness_root))
    elif formal_present:
        check("P5",
              "the copy carries no formal artifacts, matching the declaration "
              "that it is diagnostic",
              False,
              why=("this copy contains formal_run.json and/or "
                   "governance_receipt.json but the caller declared "
                   "expect='diagnostic'; a lost witness must not be accepted "
                   "as a diagnostic result -- re-run with expect='formal'"))
    else:
        check("P5", "diagnostic copy: no formal increment is claimed", True,
              skipped=True)

    local_code = receipt_mod.code_sha256()
    runtime_code = None if receipt is None else receipt.get("code_sha256")
    identity = {
        "runtime_code_sha256": runtime_code,
        "local_code_sha256": local_code,
        "runtime_equals_local": runtime_code == local_code,
        "note": ("reported for the record only: this checker deliberately does "
                 "not require the local checkout to equal the run-time "
                 "identity, so a false here is expected for a VM run and is "
                 "not an error"),
    }

    ok = all(item["ok"] for item in checks)
    return {
        "schema": SCHEMA,
        "run_dir": str(run_dir),
        "expect": expect,
        "formal_artifacts_present": formal_present,
        "receipt_schema": schema,
        "run_id": None if receipt is None else receipt.get("run_id"),
        "runtime_identity": identity,
        "checks": checks,
        "errors": errors,
        "ok": ok,
        # NEVER true from this gate.  Formal analysis eligibility is decided by
        # the existing authority, over evidence this gate cannot see.
        "claimable": False,
        "claimable_reason": (
            "this gate establishes local file integrity and the bindings it "
            "covers only; formal analysis eligibility is NOT evaluated here "
            "and remains with CODE/experiment_platform/v2_analysis.py"),
        "formal_eligibility": {
            "status": "NOT_EVALUATED",
            "authority": "CODE/experiment_platform/v2_analysis.py",
            "checked_here": [item["id"] for item in checks if item["ok"]
                             and not item.get("skipped")],
            "not_checked_here": not_checked,
        },
        "scope": ("local acceptance of a pulled-back result set: required "
                  "files, receipt-bound hashes, stream contract, and -- when "
                  "declared formal -- the formal witness and governance "
                  "receipt contracts. This is NOT authorization, NOT an "
                  "analysis, NOT formal eligibility, and it does not "
                  "re-derive the run-time identity."),
    }
