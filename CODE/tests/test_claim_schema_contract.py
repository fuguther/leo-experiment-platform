from __future__ import annotations

import copy
import json
from pathlib import Path

from CODE.experiment_platform.compile_experiment import schema_errors


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "ANALYSIS/claims/claim.schema.json"


def _receipt(kind: str) -> dict[str, str]:
    upper = "SUPPORT" if kind == "support" else "VALUE"
    role = "claim_support" if kind == "support" else "claim_value"
    filename = "support-r1.json" if kind == "support" else "value-r1.json"
    return {
        "receipt_id": f"RR-{upper}-R1",
        "path": f"ANALYSIS/claims/{filename}",
        "sha256": "a" * 64,
        "reviewer_id": f"reviewer:{kind}",
        "reviewer_session_id": f"R-{kind}-r1",
        "role": role,
        "verdict": "PASS",
        "subject_sha256": "b" * 64,
    }


def _valid_claim() -> dict[str, object]:
    return {
        "schema": "research-claim/v2",
        "claim_id": "CL-SCHEMA-R1",
        "author_id": "producer:claim-author",
        "author_session_id": "P-claim-r1",
        "candidate_artifact": {"path": "PAPER/draft.md", "sha256": "c" * 64},
        "statement": "The bounded diagnostic result is reproducible.",
        "claim_type": "EXPERIMENT_RESULT",
        "status": "SUPPORTED_LIMITED",
        "scope": "Only the declared diagnostic fixture is covered.",
        "evidence": [{
            "evidence_id": "EV-ANALYSIS-R1",
            "evidence_kind": "VERIFIED_ANALYSIS",
            "artifact": {
                "path": "ANALYSIS/EXP-TEST/analysis-manifest.json",
                "sha256": "d" * 64,
            },
            "supports": "Supports only the bounded diagnostic statement.",
        }],
        "limitations": ["This is not a confirmatory paper result."],
        "alternative_explanations": ["The fixture may not generalize beyond its scope."],
        "support_gate": {"status": "PASS", "receipt": _receipt("support")},
        "value_gate": {"status": "KEEP", "receipt": _receipt("value")},
    }


def _schema() -> dict[str, object]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_valid_research_claim_matches_strict_schema() -> None:
    assert schema_errors(_valid_claim(), _schema()) == []


def test_claim_schema_rejects_unknown_top_level_fields() -> None:
    claim = _valid_claim()
    claim["unreviewed_note"] = "not bound"
    errors = schema_errors(claim, _schema())
    assert any("unexpected property unreviewed_note" in error for error in errors)


def test_claim_schema_rejects_weak_artifact_and_receipt_bindings() -> None:
    claim = _valid_claim()
    claim["candidate_artifact"] = {"path": "../outside.md", "sha256": "A" * 64}
    claim["support_gate"]["receipt"]["role"] = "claim_value"  # type: ignore[index]
    errors = schema_errors(claim, _schema())
    assert any("candidate_artifact.path" in error for error in errors)
    assert any("support_gate.receipt.role" in error for error in errors)


def test_pass_and_keep_gates_require_receipts() -> None:
    for gate in ("support_gate", "value_gate"):
        claim = copy.deepcopy(_valid_claim())
        claim[gate].pop("receipt")  # type: ignore[union-attr]
        errors = schema_errors(claim, _schema())
        assert any("missing required property receipt" in error for error in errors), (gate, errors)


def test_claim_schema_rejects_non_analysis_evidence_path() -> None:
    claim = _valid_claim()
    claim["evidence"][0]["artifact"]["path"] = "PAPER/report.md"  # type: ignore[index]
    errors = schema_errors(claim, _schema())
    assert any("evidence[0].artifact.path" in error for error in errors)
