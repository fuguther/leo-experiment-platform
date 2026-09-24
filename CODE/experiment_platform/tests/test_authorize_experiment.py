from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from CODE.experiment_platform.authorize_experiment import (
    AuthorizationError,
    EXECUTION_BOUNDARY,
    build_authorization,
    canonical_sha,
    verify_authorization,
    verify_authorization_for_config,
    verify_authorization_for_leo_sim_v2_config,
)
from CODE.work.finalize_decision import evaluate_decision, file_sha256


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class AuthorizeExperimentTests(unittest.TestCase):
    def make_fixture(self, root: Path) -> tuple[Path, Path, Path, dict]:
        source_path = root / "CODE" / "source.py"
        source_path.parent.mkdir(parents=True)
        source_path.write_text("SOURCE = 1\n", encoding="utf-8")
        experiment = root / "EXPERIMENTS" / "EXP-TEST"
        resolved = experiment / "resolved"
        resolved.mkdir(parents=True)
        request = {"identity": {"experiment_id": "EXP-TEST"}}
        write_json(experiment / "request.json", request)
        config = {
            "simulation": {"seed": 42},
            "provenance": {
                "experiment_id": "EXP-TEST",
                "run_id": "EXP-TEST-control-s42",
                "arm_id": "control",
                "seed": 42,
                "execution_boundary": EXECUTION_BOUNDARY,
            },
        }
        write_json(resolved / "control.s42.config.json", config)
        report = {
            "schema": "experiment-compile-report/v2",
            "status": "COMPILED_REVIEW_REQUIRED",
            "errors": [],
            "request_sha256": file_sha256(experiment / "request.json"),
            "execution_authorized": False,
            "execution_boundary": EXECUTION_BOUNDARY,
            "launcher_generated": False,
        }
        write_json(experiment / "compile-report.json", report)
        manifest = {
            "schema": "experiment-run-manifest/v2",
            "experiment_id": "EXP-TEST",
            "request_sha256": report["request_sha256"],
            "execution_authorized": False,
            "execution_boundary": EXECUTION_BOUNDARY,
            "scenario_identity": {
                "constellation": "fixture",
                "source_and_input_sha256": {"CODE/source.py": file_sha256(source_path)},
            },
            "planned_runs": [{
                "run_id": "EXP-TEST-control-s42",
                "arm_id": "control",
                "seed": 42,
                "config_json": "resolved/control.s42.config.json",
                "config_sha256": canonical_sha(config),
                "controlled_signature": "c" * 64,
            }],
        }
        write_json(experiment / "run-manifest.json", manifest)
        (experiment / "RUNBOOK.md").write_text("# Fixture runbook\n", encoding="utf-8")
        write_json(experiment / "analysis-request.json", {
            "schema": "analysis-request/v2",
            "experiment_id": "EXP-TEST",
            "planned_run_ids": ["EXP-TEST-control-s42"],
            "planned_runs": [{
                "run_id": "EXP-TEST-control-s42", "arm_id": "control", "seed": 42,
                "config_sha256": canonical_sha(config), "controlled_signature": "c" * 64,
            }],
            "request_sha256": report["request_sha256"],
            "run_manifest_sha256": file_sha256(experiment / "run-manifest.json"),
            "scenario_identity_sha256": canonical_sha(manifest["scenario_identity"]),
        })

        artifact_hashes = {
            str(path.relative_to(root)): file_sha256(path)
            for path in sorted(experiment.rglob("*")) if path.is_file()
        }
        brief = {
            "schema": "agent-work-package/v2",
            "work_id": "WP-EXP-TEST",
            "revision": 1,
            "parent_revision": None,
            "producer_id": "producer:designer",
            "producer_session_id": "P-exp-r01",
            "objective": "Review one compiled experiment before any real execution.",
            "allowed_inputs": ["EXPERIMENTS/EXP-TEST"],
            "excluded_inputs": [],
            "deliverables": ["A bound execution decision"],
            "cannot_claim": ["The experiment has not run and no performance claim is allowed."],
            "acceptance": ["All mandatory experiment roles pass and bind every compiled artifact."],
            "review_roles": ["cold_start", "satellite_drl", "adversarial"],
            "cost_tier": "high_judgment",
            "status": "REVIEW",
        }
        brief_path = root / "CODE" / "work" / "WP-EXP-TEST" / "r01" / "brief.json"
        write_json(brief_path, brief)
        receipt_references = []
        for role in brief["review_roles"]:
            receipt = {
                "schema": "agent-review-receipt/v2",
                "receipt_id": f"RR-EXP-{role.upper().replace('_', '-')}",
                "work_id": "WP-EXP-TEST", "revision": 1,
                "artifact_hashes": artifact_hashes,
                "producer_id": "producer:designer", "producer_session_id": "P-exp-r01",
                "reviewer_id": f"reviewer:{role}", "reviewer_session_id": f"R-exp-{role}-r01",
                "independence": {"producer_and_reviewer_are_distinct": True, "review_started_from_declared_inputs": True},
                "role": role, "verdict": "PASS",
                "evidence": ["Compiled experiment artifacts were independently reviewed."],
                "blocking_findings": [], "unknowns": [], "required_revision": [],
            }
            receipt_path = brief_path.parent / f"review-{role}.json"
            write_json(receipt_path, receipt)
            receipt_references.append({
                "receipt_id": receipt["receipt_id"], "path": str(receipt_path.relative_to(root)),
                "sha256": file_sha256(receipt_path), "role": role, "verdict": "PASS",
            })
        decision = {
            "schema": "agent-work-decision/v1",
            "work_id": "WP-EXP-TEST",
            "revision": 1,
            "producer_id": "producer:designer",
            "artifact_hashes": artifact_hashes,
            "decision_maker_id": "decision:test",
            "applied_review_receipts": receipt_references,
            "decision": "ACCEPT",
            "rationale": "The required independent review passed with bound artifacts.",
            "blocking_findings": [],
            "revision_instructions": [],
            "next_revision": None,
        }
        decision_path = brief_path.parent / "decision.json"
        write_json(decision_path, decision)
        finalization, errors = evaluate_decision(root, brief_path, decision_path)
        self.assertEqual(errors, [])
        finalization_path = brief_path.parent / "finalization.json"
        write_json(finalization_path, finalization)
        authorization_path = experiment / "authorization.json"
        return experiment, finalization_path, authorization_path, config

    def test_authorization_is_recomputed_and_bound_to_exact_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment, finalization, authorization_path, config = self.make_fixture(root)
            authorization = build_authorization(root, experiment, finalization)
            self.assertNotIn("config_yaml", authorization["authorized_runs"][0])
            write_json(authorization_path, authorization)
            self.assertEqual(verify_authorization(root, authorization_path), authorization)
            self.assertEqual(
                verify_authorization_for_config(root, authorization_path, config)["status"],
                "AUTHORIZED",
            )
            changed = json.loads(json.dumps(config))
            changed["simulation"]["seed"] = 43
            with self.assertRaises(AuthorizationError):
                verify_authorization_for_config(root, authorization_path, changed)

            source_path = root / "CODE" / "source.py"
            source_path.write_text("SOURCE = 2\n", encoding="utf-8")
            with self.assertRaisesRegex(AuthorizationError, "scenario source changed"):
                verify_authorization(root, authorization_path)

    def test_forged_status_and_post_review_artifact_change_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            experiment, finalization, authorization_path, _ = self.make_fixture(root)
            authorization = build_authorization(root, experiment, finalization)
            write_json(authorization_path, authorization)

            accepted = json.loads(finalization.read_text(encoding="utf-8"))
            fake = json.loads(json.dumps(accepted))
            fake["decision_sha256"] = "0" * 64
            fake["status"] = "ACCEPTED"
            write_json(finalization, fake)
            with self.assertRaises(AuthorizationError):
                verify_authorization(root, authorization_path)

            write_json(finalization, accepted)
            config_path = experiment / "resolved" / "control.s42.config.json"
            changed = json.loads(config_path.read_text(encoding="utf-8"))
            changed["simulation"]["seed"] = 999
            write_json(config_path, changed)
            with self.assertRaises(AuthorizationError):
                verify_authorization(root, authorization_path)

    def test_v2_authorization_binds_compiled_config_and_code(self) -> None:
        from CODE.leo_sim import governance

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The V2 verifier imports schemas from the real package, while all
            # reviewed artifacts live in this isolated fixture root.
            (root / "CODE" / "work").mkdir(parents=True)
            for name in ("work-package.schema.json", "review-receipt.schema.json",
                         "decision.schema.json"):
                (root / "CODE" / "work" / name).write_bytes(
                    (PROJECT_ROOT / "CODE" / "work" / name).read_bytes())
            request = {
                "schema": governance.REQUEST_SCHEMA,
                "experiment_id": "EXP-LEO-V2-AUTH",
                "runtime_kind": "leo_sim_v2",
                "work_finalization": (
                    "CODE/work/WP-LEO-V2-AUTH/r01/finalization.json"),
                "acceptance": {"min_delivered_packets": 0,
                               "min_multisat_deliveries": 0,
                               "require_data_isl": False,
                               "require_control_delivery": False},
                "config": {
                    "scenario": {"duration_s": 1.0, "num_satellites": 1,
                                 "num_planes": 1, "seed": 9},
                    "endpoints": {"sites": [
                        {"name": "a", "lat": 0.0, "lon": 0.0},
                        {"name": "b", "lat": 0.0, "lon": 10.0},
                    ]},
                    "control_plane": {"enabled": False},
                    "routing": {"policy": "oracle"},
                },
            }
            request_path = root / "request-input.json"
            write_json(request_path, request)
            experiment = root / "EXPERIMENTS" / request["experiment_id"]
            governance.compile_experiment(request_path, experiment, project_root=root)
            manifest = json.loads((experiment / "run-manifest.json").read_text())
            run = manifest["planned_runs"][0]
            config_path = experiment / run["config_path"]

            artifacts = {
                str(path.relative_to(root)): file_sha256(path)
                for path in sorted(experiment.rglob("*")) if path.is_file()
            }
            work_dir = root / "CODE" / "work" / "WP-LEO-V2-AUTH" / "r01"
            brief = {
                "schema": "agent-work-package/v2", "work_id": "WP-LEO-V2-AUTH",
                "revision": 1, "parent_revision": None,
                "producer_id": "producer:codex", "producer_session_id": "P-v2-auth-r01",
                "objective": "Review the compiled V2 formal experiment artifacts.",
                "allowed_inputs": [str(experiment.relative_to(root))],
                "excluded_inputs": [], "deliverables": ["Bound review decision"],
                "cannot_claim": ["No VM run or algorithm effect is claimed."],
                "acceptance": ["All three independent roles bind every artifact."],
                "review_roles": ["cold_start", "satellite_drl", "adversarial"],
                "cost_tier": "high_judgment", "status": "REVIEW",
            }
            brief_path = work_dir / "brief.json"
            write_json(brief_path, brief)
            refs = []
            for role in brief["review_roles"]:
                rr = {
                    "schema": "agent-review-receipt/v2",
                    "receipt_id": f"RR-V2-{role.upper().replace('_', '-')}",
                    "work_id": brief["work_id"], "revision": 1,
                    "artifact_hashes": artifacts,
                    "producer_id": brief["producer_id"],
                    "producer_session_id": brief["producer_session_id"],
                    "reviewer_id": f"reviewer:{role}",
                    "reviewer_session_id": f"R-v2-{role}-r01",
                    "independence": {"producer_and_reviewer_are_distinct": True,
                                     "review_started_from_declared_inputs": True},
                    "role": role, "verdict": "PASS", "evidence": ["fixture pass"],
                    "blocking_findings": [], "unknowns": [], "required_revision": [],
                }
                rp = work_dir / f"review-{role}.json"
                write_json(rp, rr)
                refs.append({"receipt_id": rr["receipt_id"],
                             "path": str(rp.relative_to(root)),
                             "sha256": file_sha256(rp), "role": role,
                             "verdict": "PASS"})
            decision = {
                "schema": "agent-work-decision/v1", "work_id": brief["work_id"],
                "revision": 1, "producer_id": brief["producer_id"],
                "artifact_hashes": artifacts, "decision_maker_id": "decision:codex",
                "applied_review_receipts": refs, "decision": "ACCEPT",
                "rationale": "All mandatory independent roles passed the bound build.",
                "blocking_findings": [], "revision_instructions": [], "next_revision": None,
            }
            decision_path = work_dir / "decision.json"
            write_json(decision_path, decision)
            finalization, errors = evaluate_decision(root, brief_path, decision_path)
            self.assertEqual(errors, [])
            finalization_path = work_dir / "finalization.json"
            write_json(finalization_path, finalization)
            authorization_path = experiment / "authorization.json"
            write_json(authorization_path, build_authorization(
                root, experiment, finalization_path))
            verified = verify_authorization_for_leo_sim_v2_config(
                root, authorization_path, config_path, run["run_id"])
            self.assertEqual(verified["status"], "AUTHORIZED")
            with self.assertRaises(AuthorizationError):
                verify_authorization_for_leo_sim_v2_config(
                    root, authorization_path, config_path, run["run_id"] + "-wrong")


if __name__ == "__main__":
    unittest.main()
