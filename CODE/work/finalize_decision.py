#!/usr/bin/env python3
"""Materialize a work decision only when all required independent reviews bind the artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
from CODE.experiment_platform.compile_experiment import schema_errors
WORK_ID = re.compile(r"^WP-[A-Za-z0-9_-]+$")
PRODUCER_ID = re.compile(r"^producer:[A-Za-z0-9._-]+$")
PRODUCER_SESSION = re.compile(r"^P-[A-Za-z0-9._-]+$")
REVIEWER_ID = re.compile(r"^reviewer:[A-Za-z0-9._-]+$")
REVIEWER_SESSION = re.compile(r"^R-[A-Za-z0-9._-]+$")
RECEIPT_ID = re.compile(r"^RR-[A-Za-z0-9_-]+$")
REVIEW_ROLES = {
    "cold_start", "satellite_drl", "research_governance", "adversarial",
    "paper_value", "claim_support", "claim_value",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


LEGACY_ARCHIVE_REWRITES = (
    ("WORK/", "ARCHIVE-20260803/WORK/"),
)


def project_path(root: Path, raw: str | Path) -> Path:
    raw_str = str(raw)
    for prefix, replacement in LEGACY_ARCHIVE_REWRITES:
        if raw_str == prefix.rstrip("/") or raw_str.startswith(prefix):
            raw_str = replacement + raw_str[len(prefix):]
            break
    candidate = (root / raw_str).resolve()
    candidate.relative_to(root.resolve())
    return candidate


def canonical_project_relative(root: Path, path: Path) -> str:
    """Return the stable WORK/... identifier even after physical files moved to ARCHIVE."""
    relative = path.resolve().relative_to(root.resolve())
    try:
        relative.relative_to("ARCHIVE-20260803/WORK")
    except ValueError:
        return str(relative)
    return str(Path("WORK") / relative.relative_to("ARCHIVE-20260803/WORK"))


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def evaluate_decision(root: Path, brief_path: Path, decision_path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    brief = load_json(brief_path)
    decision = load_json(decision_path)
    brief_schema = load_json(PROJECT_ROOT / "CODE" / "work" / "work-package.schema.json")
    receipt_schema = load_json(PROJECT_ROOT / "CODE" / "work" / "review-receipt.schema.json")
    decision_schema = load_json(PROJECT_ROOT / "CODE" / "work" / "decision.schema.json")
    errors.extend(f"brief{item[1:]}" for item in schema_errors(brief, brief_schema))
    errors.extend(f"decision{item[1:]}" for item in schema_errors(decision, decision_schema))
    if brief.get("schema") != "agent-work-package/v2":
        errors.append("brief.schema must be agent-work-package/v2")
    if decision.get("schema") != "agent-work-decision/v1":
        errors.append("decision.schema must be agent-work-decision/v1")
    if not isinstance(brief.get("work_id"), str) or not WORK_ID.fullmatch(brief["work_id"]):
        errors.append("brief.work_id is invalid")
    if not isinstance(brief.get("revision"), int) or isinstance(brief.get("revision"), bool) or brief["revision"] < 1:
        errors.append("brief.revision must be an integer >= 1")
    if not isinstance(brief.get("producer_id"), str) or not PRODUCER_ID.fullmatch(brief["producer_id"]):
        errors.append("brief.producer_id is invalid")
    if not isinstance(brief.get("producer_session_id"), str) or not PRODUCER_SESSION.fullmatch(brief["producer_session_id"]):
        errors.append("brief.producer_session_id is invalid")
    roles = brief.get("review_roles")
    if (
        not isinstance(roles, list)
        or not roles
        or any(not isinstance(role, str) for role in roles)
        or len(set(roles)) != len(roles)
        or any(role not in REVIEW_ROLES for role in roles)
    ):
        errors.append("brief.review_roles must be a non-empty unique list of supported roles")
        roles = []
    if not isinstance(decision.get("decision_maker_id"), str) or not decision["decision_maker_id"].startswith("decision:"):
        errors.append("decision.decision_maker_id is invalid")
    if not isinstance(decision.get("rationale"), str) or len(decision["rationale"].strip()) < 10:
        errors.append("decision.rationale must contain at least 10 characters")
    for key in ("work_id", "revision", "producer_id"):
        if decision.get(key) != brief.get(key):
            errors.append(f"decision.{key} does not match brief")
    artifact_hashes = decision.get("artifact_hashes")
    if not isinstance(artifact_hashes, dict) or not artifact_hashes:
        errors.append("decision.artifact_hashes must be non-empty")
        artifact_hashes = {}
    for raw, expected in artifact_hashes.items():
        try:
            path = project_path(root, raw)
        except (ValueError, TypeError):
            errors.append(f"artifact path escapes project: {raw}")
            continue
        if not path.is_file() or file_sha256(path) != expected:
            errors.append(f"artifact hash mismatch: {raw}")

    receipt_summaries: list[dict[str, Any]] = []
    seen_receipts: set[str] = set()
    seen_reviewers: set[tuple[str, str]] = set()
    references = decision.get("applied_review_receipts")
    if not isinstance(references, list) or not references:
        errors.append("decision requires applied review receipts")
        references = []
    for reference in references:
        if not isinstance(reference, dict):
            errors.append("review reference must be an object")
            continue
        raw_path = reference.get("path", "")
        try:
            path = project_path(root, raw_path)
        except (ValueError, TypeError):
            errors.append(f"review path escapes project: {raw_path}")
            continue
        if not path.is_file() or file_sha256(path) != reference.get("sha256"):
            errors.append(f"review receipt hash mismatch: {raw_path}")
            continue
        receipt = load_json(path)
        errors.extend(
            f"receipt {raw_path}{item[1:]}"
            for item in schema_errors(receipt, receipt_schema)
        )
        receipt_id = receipt.get("receipt_id")
        if receipt.get("schema") != "agent-review-receipt/v2":
            errors.append(f"receipt {receipt_id} has unsupported schema")
        if not isinstance(receipt_id, str) or not RECEIPT_ID.fullmatch(receipt_id):
            errors.append(f"receipt has invalid receipt_id: {receipt_id}")
            receipt_id = str(receipt_id)
        if not isinstance(receipt.get("reviewer_id"), str) or not REVIEWER_ID.fullmatch(receipt["reviewer_id"]):
            errors.append(f"receipt {receipt_id} has invalid reviewer_id")
        if not isinstance(receipt.get("reviewer_session_id"), str) or not REVIEWER_SESSION.fullmatch(receipt["reviewer_session_id"]):
            errors.append(f"receipt {receipt_id} has invalid reviewer_session_id")
        evidence = receipt.get("evidence")
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, str) or not item.strip() for item in evidence):
            errors.append(f"receipt {receipt_id} requires non-empty evidence")
        if receipt_id in seen_receipts:
            errors.append(f"duplicate review receipt: {receipt_id}")
        seen_receipts.add(receipt_id)
        exact = {
            "receipt_id": reference.get("receipt_id"),
            "work_id": brief.get("work_id"),
            "revision": brief.get("revision"),
            "producer_id": brief.get("producer_id"),
            "producer_session_id": brief.get("producer_session_id"),
            "role": reference.get("role"),
            "verdict": reference.get("verdict"),
        }
        for key, expected in exact.items():
            if receipt.get(key) != expected:
                errors.append(f"receipt {receipt_id} has mismatched {key}")
        if receipt.get("independence") != {
            "producer_and_reviewer_are_distinct": True,
            "review_started_from_declared_inputs": True,
        }:
            errors.append(f"receipt {receipt_id} lacks independence attestation")
        reviewer = (str(receipt.get("reviewer_id", "")), str(receipt.get("reviewer_session_id", "")))
        if reviewer in seen_reviewers:
            errors.append(f"reviewer/session reused across applied receipts: {reviewer}")
        seen_reviewers.add(reviewer)
        bound = receipt.get("artifact_hashes")
        if not isinstance(bound, dict) or any(bound.get(k) != v for k, v in artifact_hashes.items()):
            errors.append(f"receipt {receipt_id} does not bind every decided artifact")
        if receipt.get("verdict") == "PASS" and receipt.get("blocking_findings") != []:
            errors.append(f"PASS receipt {receipt_id} contains blockers")
        receipt_summaries.append({
            "receipt_id": receipt_id,
            "path": raw_path,
            "sha256": reference.get("sha256"),
            "role": str(receipt.get("role", "")),
            "verdict": str(receipt.get("verdict", "")),
            "reviewer_id": reviewer[0],
            "reviewer_session_id": reviewer[1],
        })

    decision_value = decision.get("decision")
    required_roles = set(roles)
    pass_roles = {item["role"] for item in receipt_summaries if item["verdict"] == "PASS"}
    block_receipts = [item for item in receipt_summaries if item["verdict"] == "BLOCK"]
    unknown_receipts = [item for item in receipt_summaries if item["verdict"] == "UNKNOWN"]
    if decision_value == "ACCEPT":
        missing_roles = sorted(required_roles - pass_roles)
        if missing_roles:
            errors.append(f"ACCEPT is missing required PASS roles: {missing_roles}")
        if block_receipts or unknown_receipts:
            errors.append("ACCEPT cannot include BLOCK or UNKNOWN receipts")
        if decision.get("blocking_findings") != [] or decision.get("next_revision") is not None:
            errors.append("ACCEPT has inconsistent blocker/revision fields")
    elif decision_value == "REVISE":
        if not block_receipts:
            errors.append("REVISE requires at least one BLOCK receipt")
        if not decision.get("revision_instructions") or decision.get("next_revision") != brief.get("revision", 0) + 1:
            errors.append("REVISE requires instructions and the next sequential revision")
    elif decision_value == "STOP":
        if not decision.get("blocking_findings"):
            errors.append("STOP requires blocking findings")
    else:
        errors.append(f"unknown decision: {decision_value}")

    if errors:
        return None, sorted(set(errors))
    receipt = {
        "schema": "agent-work-finalization/v1",
        "status": {"ACCEPT": "ACCEPTED", "REVISE": "REVISION_REQUIRED", "STOP": "STOPPED"}[decision_value],
        "work_id": brief["work_id"],
        "revision": brief["revision"],
        "brief_path": canonical_project_relative(root, brief_path),
        "brief_sha256": file_sha256(brief_path),
        "decision_path": canonical_project_relative(root, decision_path),
        "decision_sha256": file_sha256(decision_path),
        "required_review_roles": sorted(required_roles),
        "applied_reviews": receipt_summaries,
        "artifact_hashes": artifact_hashes,
    }
    return receipt, []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", type=Path, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args()
    root = args.root.resolve()
    brief = args.brief if args.brief.is_absolute() else root / args.brief
    decision = args.decision if args.decision.is_absolute() else root / args.decision
    receipt, errors = evaluate_decision(root, brief.resolve(), decision.resolve())
    if errors:
        for error in errors:
            print(f"BLOCK: {error}")
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(receipt["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
