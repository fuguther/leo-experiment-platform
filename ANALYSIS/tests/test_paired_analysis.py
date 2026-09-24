from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ANALYSIS import paired_analysis as pa


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class PairedAnalysisTests(unittest.TestCase):
    """Single source of truth for analysis fixtures used by code and PAPER tests.

    The fixture deliberately patches only the authorization verifier.  The
    production analyzer still requires an authorization path and invokes the
    real verifier; these tests isolate the analyzer's evidence binding from
    the separate review-fixture builder.
    """

    # PAPER imports this fixture class to construct a controlled evidence
    # fixture. Mark it non-collectable so pytest does not execute the same
    # unittest methods once for every historical import path.
    __test__ = False

    def setUp(self) -> None:
        self._tmp_handle = tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "ANALYSIS")
        self.tmp = Path(self._tmp_handle.name)
        self.root = PROJECT_ROOT
        self.results_root = self.tmp / "runs"
        self.results_root.mkdir()
        self.experiment_dir = self.tmp / "EXPERIMENTS" / "EXP-TEST"
        self.experiment_dir.mkdir(parents=True)
        self.request_path = self.experiment_dir / "request.json"
        self.manifest_path = self.experiment_dir / "run-manifest.json"
        self.analysis_path = self.experiment_dir / "analysis-request.json"
        self.authorization_path = self.experiment_dir / "authorization.json"
        self.analysis = {
            "schema": "analysis-request/v2",
            "analysis_id": "AN-TEST",
            "experiment_id": "EXP-TEST",
            "primary_metric": "delivery_rate",
            "secondary_metrics": [],
            "cannot_conclude": ["This fixture is not a paper result."],
            "preregistration": {
                "paired_by": ["seed", "scenario_identity", "controlled_signature"],
                "planned_contrasts": [{
                    "name": "treatment_minus_control",
                    "left_arm": "treatment",
                    "right_arm": "control",
                    "estimand": "paired mean difference in delivery_rate",
                }],
            },
        }
        self.run_manifest = {
            "schema": "experiment-run-manifest/v2",
            "experiment_id": "EXP-TEST",
            "scenario_identity": {"fixture": "paired-analysis"},
            "planned_runs": [],
        }
        self.run_entries: list[tuple[str, Path]] = []
        for arm, value in (("control", 0.4), ("treatment", 0.6)):
            run_id = f"EXP-TEST-{arm}-s7"
            config = {
                "provenance": {
                    "experiment_id": "EXP-TEST", "run_id": run_id,
                    "arm_id": arm, "seed": 7,
                    "scenario_identity": self.run_manifest["scenario_identity"],
                    "required_artifacts": [
                        "run_trace/run_meta.json", "config_used.json",
                        "artifact_manifest.json", "experiment_bundle/summary_metrics.csv",
                    ],
                }
            }
            run = self.results_root / run_id
            run.mkdir()
            _write_json(run / "config_used.json", config)
            (run / "experiment_bundle").mkdir()
            (run / "experiment_bundle/summary_metrics.csv").write_text(
                f"metric,value\ndelivery_rate,{value}\n", encoding="utf-8")
            (run / "run_trace").mkdir()
            meta = {
                "requested_run_id": run_id,
                "config_canonical_sha256": pa.canonical_sha(config),
                "scenario_identity_sha256": pa.canonical_sha(self.run_manifest["scenario_identity"]),
                "launch_nonce": "a" * 32,
                "run_attempt_id": "b" * 32,
                "natural_end": True, "interrupted": False,
                "effective_receipt": {
                    "schema": "leo-effective-receipt/v1",
                    "research_eligible": True, "mismatches": [],
                },
            }
            _write_json(run / "run_trace/run_meta.json", meta)
            required = config["provenance"]["required_artifacts"]
            artifacts = []
            for relative in required:
                if relative == "artifact_manifest.json":
                    continue
                path = run / relative
                artifacts.append({"path": relative, "size": path.stat().st_size, "sha256": _sha(path)})
            _write_json(run / "artifact_manifest.json", {
                "schema": "artifact-manifest/v1", "run_id": run_id,
                "config_sha256": pa.canonical_sha(config),
                "required_artifacts": required, "artifacts": artifacts,
            })
            self.run_manifest["planned_runs"].append({
                "run_id": run_id, "arm_id": arm, "seed": 7,
                "config_sha256": pa.canonical_sha(config),
                "controlled_signature": "same-control-config",
            })
            self.run_entries.append((run_id, run))

        _write_json(self.request_path, {"identity": {"experiment_id": "EXP-TEST"}})
        _write_json(self.manifest_path, self.run_manifest)
        self.analysis.update({
            "request_sha256": _sha(self.request_path),
            "run_manifest_sha256": _sha(self.manifest_path),
            "scenario_identity_sha256": pa.canonical_sha(self.run_manifest["scenario_identity"]),
            "planned_run_ids": [row["run_id"] for row in self.run_manifest["planned_runs"]],
            "planned_runs": [
                {key: row.get(key) for key in (
                    "run_id", "arm_id", "seed", "config_sha256", "controlled_signature")}
                for row in self.run_manifest["planned_runs"]
            ],
        })
        _write_json(self.analysis_path, self.analysis)
        self.authorization = {
            "schema": "experiment-execution-authorization/v1",
            "status": "AUTHORIZED",
            "experiment_id": "EXP-TEST",
            "experiment_dir": str(self.experiment_dir.relative_to(PROJECT_ROOT)),
            "authorized_runs": [
                {"run_id": row["run_id"], "config_sha256": row["config_sha256"]}
                for row in self.run_manifest["planned_runs"]
            ],
        }
        _write_json(self.authorization_path, self.authorization)
        self.authorization_sha256 = _sha(self.authorization_path)
        for _run_id, run in self.run_entries:
            meta_path = run / "run_trace/run_meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["authorization_sha256"] = self.authorization_sha256
            _write_json(meta_path, meta)
            artifact_path = run / "artifact_manifest.json"
            artifacts = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifacts["artifacts"] = [
                {"path": item["path"], "size": (run / item["path"]).stat().st_size,
                 "sha256": _sha(run / item["path"])}
                for item in artifacts["artifacts"]
            ]
            _write_json(artifact_path, artifacts)

        self._real_execute = pa.execute
        self._auth_patch = mock.patch.object(pa, "verify_authorization", return_value=self.authorization)
        self._auth_patch.start()

        def _compat_execute(_root, analysis, run_manifest, entries, out_dir, **kwargs):
            return self._real_execute(
                PROJECT_ROOT, analysis, run_manifest, entries, out_dir,
                request_path=self.request_path, manifest_path=self.manifest_path,
                authorization_path=self.authorization_path, results_root=self.results_root,
            )

        self._execute_compat = _compat_execute
        pa.execute = _compat_execute

    def tearDown(self) -> None:
        pa.execute = self._real_execute
        self._auth_patch.stop()
        self._tmp_handle.cleanup()

    def _run(self, entries: list[tuple[str, Path]] | None = None):
        return self._real_execute(
            PROJECT_ROOT, self.analysis, self.run_manifest,
            self.run_entries if entries is None else entries, self.tmp / "out",
            request_path=self.request_path, manifest_path=self.manifest_path,
            authorization_path=self.authorization_path, results_root=self.results_root,
        )

    def _write_verified(self, out: Path | None = None) -> Path:
        out = out or self.tmp / "out"
        manifest, results, errors = self._run()
        self.assertEqual(errors, [], errors)
        pa.write_outputs(PROJECT_ROOT, out, manifest, results)
        return out / "analysis-manifest.json"

    def _rehash_run_artifacts(self, run: Path) -> None:
        artifact_path = run / "artifact_manifest.json"
        artifact_manifest = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact_manifest["artifacts"] = [
            {
                "path": item["path"],
                "size": (run / item["path"]).stat().st_size,
                "sha256": _sha(run / item["path"]),
            }
            for item in artifact_manifest["artifacts"]
        ]
        _write_json(artifact_path, artifact_manifest)

    def _refresh_compiled_and_authorized_cohort(self) -> None:
        _write_json(self.manifest_path, self.run_manifest)
        self.analysis["run_manifest_sha256"] = _sha(self.manifest_path)
        self.analysis["planned_run_ids"] = [
            row["run_id"] for row in self.run_manifest["planned_runs"]]
        self.analysis["planned_runs"] = [
            {key: row.get(key) for key in (
                "run_id", "arm_id", "seed", "config_sha256", "controlled_signature")}
            for row in self.run_manifest["planned_runs"]
        ]
        _write_json(self.analysis_path, self.analysis)
        self.authorization["authorized_runs"] = [
            {"run_id": row["run_id"], "config_sha256": row["config_sha256"]}
            for row in self.run_manifest["planned_runs"]
        ]
        _write_json(self.authorization_path, self.authorization)
        self.authorization_sha256 = _sha(self.authorization_path)
        for _run_id, run in self.run_entries:
            meta_path = run / "run_trace/run_meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["authorization_sha256"] = self.authorization_sha256
            _write_json(meta_path, meta)
            self._rehash_run_artifacts(run)

    def _append_control_only_seed(self, seed: int) -> None:
        source = self.run_entries[0][1]
        run_id = f"EXP-TEST-control-s{seed}"
        run = self.results_root / run_id
        shutil.copytree(source, run)
        config_path = run / "config_used.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["provenance"]["run_id"] = run_id
        config["provenance"]["seed"] = seed
        _write_json(config_path, config)
        config_sha = pa.canonical_sha(config)
        meta_path = run / "run_trace/run_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["requested_run_id"] = run_id
        meta["config_canonical_sha256"] = config_sha
        _write_json(meta_path, meta)
        artifact_path = run / "artifact_manifest.json"
        artifact_manifest = json.loads(artifact_path.read_text(encoding="utf-8"))
        artifact_manifest["run_id"] = run_id
        artifact_manifest["config_sha256"] = config_sha
        _write_json(artifact_path, artifact_manifest)
        self.run_manifest["planned_runs"].append({
            "run_id": run_id,
            "arm_id": "control",
            "seed": seed,
            "config_sha256": config_sha,
            "controlled_signature": "same-control-config",
        })
        self.run_entries.append((run_id, run))
        self._refresh_compiled_and_authorized_cohort()

    def test_complete_hash_verified_pairs_are_analyzed(self) -> None:
        path = self._write_verified()
        persisted = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("ANALYSIS/claims/claim.schema.json", persisted["inputs"])
        valid, errors = pa.verify_persisted_analysis(PROJECT_ROOT, path)
        self.assertTrue(valid, errors)

    def test_hash_drift_is_fail_closed(self) -> None:
        path = self._write_verified()
        (self.run_entries[0][1] / "experiment_bundle/summary_metrics.csv").write_text(
            "metric,value\ndelivery_rate,9\n", encoding="utf-8")
        valid, errors = pa.verify_persisted_analysis(PROJECT_ROOT, path)
        self.assertFalse(valid)
        self.assertTrue(any("hash mismatch" in item for item in errors), errors)

    def test_persisted_semantics_and_field_set_are_fail_closed(self) -> None:
        path = self._write_verified()
        original = json.loads(path.read_text(encoding="utf-8"))
        def add_extra_input(value: dict) -> None:
            raw = "CODE/__init__.py"
            digest = _sha(PROJECT_ROOT / raw)
            value["inputs"][raw] = digest
            value["input_hashes"][raw] = digest

        mutations = (
            ("analysis_id", lambda value: value.__setitem__("analysis_id", "AN-FORGED")),
            ("claim_boundary", lambda value: value.__setitem__(
                "claim_boundary", {"cannot_conclude": ["Forged conclusion boundary."]})),
            ("extra", lambda value: value.__setitem__("unbound_semantics", {"claim": "forged"})),
            ("extra_input", add_extra_input),
            ("missing", lambda value: value.pop("analysis_id")),
        )
        for label, mutate in mutations:
            candidate = json.loads(json.dumps(original))
            mutate(candidate)
            _write_json(path, candidate)
            valid, errors = pa.verify_persisted_analysis(PROJECT_ROOT, path)
            self.assertFalse(valid, (label, errors))
            self.assertTrue(any(
                "fields mismatch" in error or "differs from recomputation" in error
                for error in errors), (label, errors))

    def test_output_declarations_cannot_point_to_another_project_directory(self) -> None:
        path = self._write_verified()
        candidate = json.loads(path.read_text(encoding="utf-8"))
        forged_dir = self.tmp / "forged-output"
        forged_dir.mkdir()
        forged_payloads = {
            "summary.csv": b"contrast,metric\nforged,forged\n",
            "report.md": b"# Forged report\n",
            "analysis-code.py": b"raise SystemExit('forged')\n",
        }
        candidate["output_hashes"] = {}
        candidate["output_artifacts"] = []
        for name, payload in forged_payloads.items():
            forged = forged_dir / name
            forged.write_bytes(payload)
            digest = _sha(forged)
            candidate["output_hashes"][name] = digest
            candidate["output_artifacts"].append({
                "path": str(forged.relative_to(PROJECT_ROOT)),
                "sha256": digest,
            })
        _write_json(path, candidate)
        valid, errors = pa.verify_persisted_analysis(PROJECT_ROOT, path)
        self.assertFalse(valid)
        self.assertTrue(any(
            "manifest-directory" in error or "manifest directory" in error
            for error in errors), errors)

    def test_every_preregistered_pairing_key_requires_both_contrast_arms(self) -> None:
        self._append_control_only_seed(8)
        manifest, _results, errors = self._run()
        self.assertEqual(manifest["status"], "BLOCKED")
        self.assertTrue(any(
            "pairing key" in error and "lacks planned arms ['treatment']" in error
            for error in errors), errors)
        self.assertEqual(manifest["planned_contrasts"][0]["n_pairs"], 1)

    def test_incomplete_cohort_and_duplicate_run_are_rejected(self) -> None:
        _manifest, _results, errors = self._run(self.run_entries[:1])
        self.assertTrue(any("cohort" in item for item in errors))
        _manifest, _results, errors = self._run(self.run_entries + [self.run_entries[0]])
        self.assertTrue(any("duplicate" in item or "cohort" in item for item in errors))

    def test_missing_artifact_is_blocked(self) -> None:
        (self.run_entries[0][1] / "experiment_bundle/summary_metrics.csv").unlink()
        _manifest, _results, errors = self._run()
        self.assertTrue(any("missing" in item for item in errors), errors)

    def test_non_natural_and_bad_receipt_are_blocked(self) -> None:
        meta_path = self.run_entries[0][1] / "run_trace/run_meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["natural_end"] = False
        meta["effective_receipt"]["mismatches"] = ["forged"]
        _write_json(meta_path, meta)
        _manifest, _results, errors = self._run()
        self.assertTrue(any("naturally" in item or "eligible" in item for item in errors), errors)

    def test_authorization_or_binding_bypass_is_blocked(self) -> None:
        forged = dict(self.analysis)
        forged["request_sha256"] = "0" * 64
        _manifest, _results, errors = self._real_execute(
            PROJECT_ROOT, forged, self.run_manifest, self.run_entries, self.tmp / "out",
            request_path=self.request_path, manifest_path=self.manifest_path,
            authorization_path=self.authorization_path, results_root=self.results_root,
        )
        self.assertTrue(any("bind request" in item for item in errors), errors)
        forged_auth = dict(self.authorization)
        forged_auth["authorized_runs"] = []
        self._auth_patch.stop()
        self._auth_patch = mock.patch.object(pa, "verify_authorization", return_value=forged_auth)
        self._auth_patch.start()
        _manifest, _results, errors = self._run()
        self.assertTrue(any("cohort" in item for item in errors), errors)

    def test_artifact_identity_mismatch_is_blocked(self) -> None:
        path = self.run_entries[0][1] / "artifact_manifest.json"
        artifacts = json.loads(path.read_text(encoding="utf-8"))
        artifacts["run_id"] = "EXP-OTHER"
        _write_json(path, artifacts)
        _manifest, _results, errors = self._run()
        self.assertTrue(any("artifact manifest run_id" in item for item in errors), errors)

    def test_entry_symlink_is_blocked(self) -> None:
        alias = self.results_root / "EXP-TEST-control-s7-alias"
        alias.symlink_to(self.run_entries[0][1], target_is_directory=True)
        entries = [(self.run_entries[0][0], alias), self.run_entries[1]]
        _manifest, _results, errors = self._run(entries)
        self.assertTrue(any("symbolic" in item or "non-symlink" in item for item in errors), errors)

    def test_existing_output_is_not_overwritten(self) -> None:
        out = self.tmp / "out"
        path = self._write_verified(out)
        marker = out / "user-marker.txt"
        marker.write_text("preserve", encoding="utf-8")
        manifest, results, errors = self._run()
        self.assertEqual(errors, [])
        with self.assertRaisesRegex(ValueError, "new or empty"):
            pa.write_outputs(PROJECT_ROOT, out, manifest, results)
        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(path.is_file())


def _run_fixture_method(name: str) -> None:
    fixture = PairedAnalysisTests(methodName=name)
    fixture.setUp()
    try:
        getattr(fixture, name)()
    finally:
        fixture.tearDown()


def test_complete_hash_verified_pairs_are_analyzed() -> None:
    _run_fixture_method("test_complete_hash_verified_pairs_are_analyzed")


def test_hash_drift_is_fail_closed() -> None:
    _run_fixture_method("test_hash_drift_is_fail_closed")


def test_persisted_semantics_and_field_set_are_fail_closed() -> None:
    _run_fixture_method("test_persisted_semantics_and_field_set_are_fail_closed")


def test_output_declarations_cannot_point_to_another_project_directory() -> None:
    _run_fixture_method("test_output_declarations_cannot_point_to_another_project_directory")


def test_every_preregistered_pairing_key_requires_both_contrast_arms() -> None:
    _run_fixture_method("test_every_preregistered_pairing_key_requires_both_contrast_arms")


def test_incomplete_cohort_and_duplicate_run_are_rejected() -> None:
    _run_fixture_method("test_incomplete_cohort_and_duplicate_run_are_rejected")


def test_missing_artifact_is_blocked() -> None:
    _run_fixture_method("test_missing_artifact_is_blocked")


def test_non_natural_and_bad_receipt_are_blocked() -> None:
    _run_fixture_method("test_non_natural_and_bad_receipt_are_blocked")


def test_authorization_or_binding_bypass_is_blocked() -> None:
    _run_fixture_method("test_authorization_or_binding_bypass_is_blocked")


def test_artifact_identity_mismatch_is_blocked() -> None:
    _run_fixture_method("test_artifact_identity_mismatch_is_blocked")


def test_entry_symlink_is_blocked() -> None:
    _run_fixture_method("test_entry_symlink_is_blocked")


def test_existing_output_is_not_overwritten() -> None:
    _run_fixture_method("test_existing_output_is_not_overwritten")


if __name__ == "__main__":
    unittest.main()
