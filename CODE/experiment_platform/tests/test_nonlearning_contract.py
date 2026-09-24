from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from CODE.experiment_platform.compile_experiment import arm_constraints, compile_request, load_json


def test_default_generic_template_is_external_legacy_only(tmp_path) -> None:
    request_path = ROOT / "EXPERIMENTS" / "templates" / "experiment-request.example.json"
    compiled = tmp_path / "compiled"

    assert compile_request(request_path, compiled) == 0
    manifest = load_json(compiled / "run-manifest.json")
    runbook = (compiled / "RUNBOOK.md").read_text(encoding="utf-8")

    assert manifest["runtime_kind"] == "legacy_gateway_external"
    assert "external legacy platform compatibility artifact" in runbook
    assert "CODE/scripts/remote/run-remote.sh" not in runbook
    assert "CODE/run.py" not in runbook


class NonLearningContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        fixture_root = Path(self.tmp.name)
        self.request_path = fixture_root / "request.json"
        template = load_json(ROOT / "EXPERIMENTS" / "templates" / "experiment-request.example.json")
        template["identity"].update({"experiment_id": "EXP-TEST-NONLEARNING", "owner": "test"})
        template["design"].update({
            "intended_role": "diagnostic",
            "base_profile": "infrastructure-smoke-v1",
            "planned_seeds": [20260715],
            "factor_changed": ["traffic.fraction"],
            "secondary_metrics": [],
        })
        capabilities = {
            "train": [],
            "evaluation": ["full_topology", "global_link_slant_range"],
            "deployment": ["full_topology", "global_link_slant_range"],
        }
        def arm(arm_id: str, role: str, changes: dict) -> dict:
            return {
                "id": arm_id,
                "label": arm_id,
                "role": role,
                "method_family": "slant_range",
                "execution_kind": "non_learning",
                "information_contract": capabilities,
                "training_budget": {"simulated_seconds": 0},
                "evaluation_budget": {"simulated_seconds": 0},
                "execution_budget": {"simulated_seconds": 0.3},
                "checkpoint_lineage": {"mode": "not_applicable", "source_run_id": None, "source_sha256": None},
                "changes": changes,
            }
        template["design"]["arms"] = [arm("control", "control", {}), arm("higher_load", "treatment", {"traffic.fraction": 0.2})]
        template["analysis"]["planned_contrasts"] = [{
            "name": "higher_load_minus_control",
            "left_arm": "higher_load",
            "right_arm": "control",
            "estimand": "paired mean difference in delivery_rate",
        }]
        template["analysis"]["minimum_effect"]["metric"] = "delivery_rate"
        self.request_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
        self.request = template
        self.compiled = fixture_root / "compiled"
        self.assertEqual(compile_request(self.request_path, self.compiled), 0)
        self.config = load_json(self.compiled / "resolved" / "control.s20260715.config.json")
        catalog = load_json(ROOT / "CODE" / "experiment_platform" / "parameter-catalog.json")
        self.specs = {item["path"]: item for item in catalog["parameters"]}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def errors_for(self, config=None, arm=None) -> list[str]:
        errors, _warnings, _effective = arm_constraints(
            copy.deepcopy(config or self.config),
            copy.deepcopy(self.request),
            copy.deepcopy(arm or self.request["design"]["arms"][0]),
            self.specs,
        )
        return errors

    def test_r04_compiles_as_non_learning(self) -> None:
        manifest = json.loads((self.compiled / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["runtime_kind"], "legacy_gateway_external")
        self.assertTrue(all(row["run_phase"] == "non_learning" for row in manifest["planned_runs"]))
        self.assertTrue(all(row["checkpoint_lineage"]["mode"] == "not_applicable" for row in manifest["planned_runs"]))
        runbook = (self.compiled / "RUNBOOK.md").read_text(encoding="utf-8")
        self.assertIn("external legacy platform compatibility artifact", runbook)
        self.assertIn("cannot be executed from this repository", runbook)
        self.assertIn("execution_authorized=false", runbook)
        self.assertNotIn("CODE/scripts/remote/run-remote.sh", runbook)
        self.assertNotIn("CODE/run.py", runbook)

    def test_non_learning_rejects_learning_identity_budget_eval_and_checkpoint(self) -> None:
        arm = copy.deepcopy(self.request["design"]["arms"][0])
        arm["execution_kind"] = "learning"
        self.assertTrue(any("execution_kind" in item for item in self.errors_for(arm=arm)))

        arm = copy.deepcopy(self.request["design"]["arms"][0])
        arm["checkpoint_lineage"]["mode"] = "new_training"
        self.assertTrue(any("not_applicable" in item for item in self.errors_for(arm=arm)))

        arm = copy.deepcopy(self.request["design"]["arms"][0])
        arm["training_budget"]["simulated_seconds"] = 0.3
        self.assertTrue(any("training_budget=0" in item for item in self.errors_for(arm=arm)))

        config = copy.deepcopy(self.config)
        config["routing"]["eval_only"] = True
        self.assertTrue(any("eval_only=false" in item for item in self.errors_for(config=config)))

        config = copy.deepcopy(self.config)
        config["checkpoint"]["path_credit_replay"] = "unexpected.npz"
        self.assertTrue(any("forbids checkpoint" in item for item in self.errors_for(config=config)))

    def test_q_learning_eval_only_is_explicitly_blocked(self) -> None:
        config = copy.deepcopy(self.config)
        config["simulation"]["pathing"] = "Q-Learning"
        config["routing"]["eval_only"] = True
        arm = copy.deepcopy(self.request["design"]["arms"][0])
        arm["execution_kind"] = "learning"
        arm["method_family"] = "q-learning"
        arm["checkpoint_lineage"]["mode"] = "evaluation_only"
        arm["checkpoint_lineage"]["source_run_id"] = "source"
        arm["checkpoint_lineage"]["source_sha256"] = "a" * 64
        arm["evaluation_budget"]["simulated_seconds"] = 0.3
        arm["execution_budget"]["simulated_seconds"] = 0
        self.assertTrue(any("Q-Learning evaluation-only" in item for item in self.errors_for(config=config, arm=arm)))

    def test_raac_rejects_fixed_multistep_until_action_masks_are_wired(self) -> None:
        config = copy.deepcopy(self.config)
        config["simulation"]["pathing"] = "Deep Q-Learning"
        config["state"] = {"mode": "c6", "vis_k": 2}
        config["credit"] = {"method": "nstep", "n": 3}
        arm = copy.deepcopy(self.request["design"]["arms"][0])
        arm["execution_kind"] = "learning"
        arm["method_family"] = "ddqn_nstep"
        arm["checkpoint_lineage"]["mode"] = "new_training"
        arm["training_budget"]["simulated_seconds"] = 0.3
        arm["execution_budget"]["simulated_seconds"] = 0
        self.assertTrue(any("next-action masks" in item for item in self.errors_for(config=config, arm=arm)))


if __name__ == "__main__":
    unittest.main()
