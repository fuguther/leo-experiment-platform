from __future__ import annotations

import json
import unittest
from pathlib import Path

from CODE.experiment_platform.compile_experiment import PROJECT_ROOT, scenario_identity


class CompileScenarioIdentityTest(unittest.TestCase):
    def test_formal_analysis_runner_and_authorizer_are_bound(self):
        catalog_path = PROJECT_ROOT / "CODE/experiment_platform/parameter-catalog.json"
        profiles_path = PROJECT_ROOT / "CODE/experiment_platform/profiles.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        identity = scenario_identity(catalog, profiles_path, catalog_path)
        hashes = identity["source_and_input_sha256"]
        for path in (
            "ANALYSIS/paired_analysis.py",
            "CODE/scripts/remote/deployment_guard.py",
            "CODE/scripts/remote/common.sh",
            "CODE/scripts/remote/remote_job.py",
            "CODE/scripts/remote/run-remote.sh",
            "CODE/scripts/remote/status-remote.sh",
            "CODE/experiment_platform/authorize_experiment.py",
        ):
            self.assertIn(path, hashes)
            self.assertRegex(hashes[path], r"^[a-f0-9]{64}$")


if __name__ == "__main__":
    unittest.main()
