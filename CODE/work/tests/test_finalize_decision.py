from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from CODE.work.finalize_decision import (
    canonical_project_relative,
    evaluate_decision,
    file_sha256,
    project_path,
)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class FinalizeDecisionTests(unittest.TestCase):
    def test_project_path_rewrites_legacy_work_to_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "ARCHIVE-20260803" / "WORK" / "WP-TEST" / "r01" / "brief.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}", encoding="utf-8")
            self.assertEqual(
                project_path(root, "WORK/WP-TEST/r01/brief.json"),
                target.resolve(),
            )
            self.assertEqual(
                project_path(root, "WORK"),
                (root / "ARCHIVE-20260803" / "WORK").resolve(),
            )

    def test_canonical_work_path_is_stable_across_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "ARCHIVE-20260803" / "WORK" / "WP-TEST" / "r01" / "brief.json"
            self.assertEqual(
                canonical_project_relative(root, target),
                "WORK/WP-TEST/r01/brief.json",
            )

    def make_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        artifact = root / "producer" / "artifact.json"
        write_json(artifact, {"result": "candidate"})
        artifact_hashes = {"producer/artifact.json": file_sha256(artifact)}
        brief = {
            "schema": "agent-work-package/v2",
            "work_id": "WP-TEST",
            "revision": 1,
            "parent_revision": None,
            "producer_id": "producer:test",
            "producer_session_id": "P-test-r01",
            "objective": "Produce one immutable test artifact for review.",
            "allowed_inputs": ["producer/artifact.json"],
            "excluded_inputs": [],
            "deliverables": ["producer/artifact.json"],
            "cannot_claim": ["This fixture cannot support scientific claims."],
            "acceptance": ["Every required reviewer binds the artifact hash."],
            "review_roles": ["cold_start", "adversarial"],
            "cost_tier": "standard",
            "status": "REVIEW",
        }
        brief_path = root / "CODE" / "work" / "brief.json"
        write_json(brief_path, brief)
        references = []
        for suffix, role in (("COLD", "cold_start"), ("ADV", "adversarial")):
            receipt = {
                "schema": "agent-review-receipt/v2",
                "receipt_id": f"RR-{suffix}",
                "work_id": "WP-TEST",
                "revision": 1,
                "artifact_hashes": artifact_hashes,
                "producer_id": "producer:test",
                "producer_session_id": "P-test-r01",
                "reviewer_id": f"reviewer:{role}",
                "reviewer_session_id": f"R-{role}-r01",
                "independence": {
                    "producer_and_reviewer_are_distinct": True,
                    "review_started_from_declared_inputs": True,
                },
                "role": role,
                "verdict": "PASS",
                "evidence": ["Bound artifact was independently inspected."],
                "blocking_findings": [],
                "unknowns": [],
                "required_revision": [],
            }
            path = root / "CODE" / "work" / f"receipt-{role}.json"
            write_json(path, receipt)
            references.append({
                "receipt_id": receipt["receipt_id"],
                "path": str(path.relative_to(root)),
                "sha256": file_sha256(path),
                "role": role,
                "verdict": "PASS",
            })
        decision = {
            "schema": "agent-work-decision/v1",
            "work_id": "WP-TEST",
            "revision": 1,
            "producer_id": "producer:test",
            "artifact_hashes": artifact_hashes,
            "decision_maker_id": "decision:test",
            "applied_review_receipts": references,
            "decision": "ACCEPT",
            "rationale": "All required independent reviews passed.",
            "blocking_findings": [],
            "revision_instructions": [],
            "next_revision": None,
        }
        decision_path = root / "CODE" / "work" / "decision.json"
        write_json(decision_path, decision)
        return brief_path, decision_path, artifact

    def test_accept_requires_every_declared_pass_role_and_current_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief, decision, artifact = self.make_fixture(root)
            receipt, errors = evaluate_decision(root, brief, decision)
            self.assertEqual(errors, [])
            self.assertEqual(receipt["status"], "ACCEPTED")

            value = json.loads(decision.read_text(encoding="utf-8"))
            value["applied_review_receipts"] = value["applied_review_receipts"][:1]
            write_json(decision, value)
            rejected, errors = evaluate_decision(root, brief, decision)
            self.assertIsNone(rejected)
            self.assertTrue(any("missing required PASS roles" in item for item in errors))

            self.make_fixture(root)
            artifact.write_text('{"result":"changed"}\n', encoding="utf-8")
            rejected, errors = evaluate_decision(root, brief, decision)
            self.assertIsNone(rejected)
            self.assertTrue(any("artifact hash mismatch" in item for item in errors))

    def test_schema_incomplete_brief_and_receipt_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brief, decision, _ = self.make_fixture(root)
            value = json.loads(brief.read_text())
            value.pop("objective")
            write_json(brief, value)
            receipt, errors = evaluate_decision(root, brief, decision)
            self.assertIsNone(receipt)
            self.assertTrue(any("missing required property objective" in item for item in errors))

            brief, decision, _ = self.make_fixture(root)
            decision_value = json.loads(decision.read_text())
            receipt_path = root / decision_value["applied_review_receipts"][0]["path"]
            receipt_value = json.loads(receipt_path.read_text())
            receipt_value.pop("unknowns")
            write_json(receipt_path, receipt_value)
            decision_value["applied_review_receipts"][0]["sha256"] = file_sha256(receipt_path)
            write_json(decision, decision_value)
            receipt, errors = evaluate_decision(root, brief, decision)
            self.assertIsNone(receipt)
            self.assertTrue(any("missing required property unknowns" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
