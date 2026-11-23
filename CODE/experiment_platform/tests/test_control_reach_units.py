"""T1-COMPLETE P1: the control-plane cost artifact must not mix units.

Schema v1 exposed occupied["ctrl_isl_s"] (SECONDS) under the name
ctrl_isl_bits, so a reader could add seconds to bits.  v2 splits the two and
labels each; these tests fail if the mislabelling ever returns.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml"


def _run(module: str, *args: str):
    return subprocess.run(
        [sys.executable, "-m", module, *args], cwd=str(ROOT),
        capture_output=True, text=True, timeout=600, check=False)


def test_control_overhead_separates_seconds_from_bits():
    from CODE.experiment_platform.control_reach_probe import _control_overhead

    result = {
        "occupied": {"ctrl_isl_s": 0.02},
        "control": {
            "counters": {"snapshots_created": 3, "registered": 4,
                         "entered_queue": 4, "arrived": 2, "expired": 0,
                         "overflow": 0},
            "bits": {"offered": 32000, "delivered": 16000,
                     "terminal_loss": 0, "in_system": 16000},
            "fate_counts": {"DELIVERED": 2},
        },
        "events_processed": 11,
    }
    overhead = _control_overhead(result)
    # the seconds value is 0.02 seconds, NOT 0.02 bits
    assert overhead["ctrl_isl_occupied_s"] == 0.02
    assert not isinstance(overhead["ctrl_isl_occupied_s"], bool)
    # the bits value is independently accumulated and stays integral
    assert overhead["ctrl_isl_served_bits"] == 16000
    assert overhead["ctrl_isl_offered_bits"] == 32000
    # the mislabelled v1 key is gone from the emitted block
    assert "ctrl_isl_bits" not in overhead


def test_units_metadata_names_each_quantity():
    from CODE.experiment_platform import control_reach_probe as probe

    assert "seconds" in probe.UNITS["ctrl_isl_occupied_s"]
    assert "bits" in probe.UNITS["ctrl_isl_served_bits"]
    legacy = probe.LEGACY_UNIT_NOTES[probe.SCHEMA_V1]
    assert "ctrl_isl_bits" in legacy
    assert "seconds" in legacy["ctrl_isl_bits"]


def test_probe_artifact_carries_units_identity_and_no_mislabelled_key(tmp_path):
    out = tmp_path / "reach.json"
    done = _run("CODE.experiment_platform.control_reach_probe",
                "--config", SMOKE, "--forced-decision-id", "2",
                "--forced-action", "S", "--vis-k", "4",
                "--root", str(ROOT), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    doc = json.loads(out.read_text())
    assert doc["schema"] == "control-reach-probe/v2"
    assert doc["units"]["ctrl_isl_occupied_s"].startswith("seconds")
    assert doc["legacy_unit_notes"]["control-reach-probe/v1"]
    ident = doc["identity"]
    assert ident["git"]["available"] is True
    assert ident["git"]["commit"]
    assert "diff_sha256" in ident["git"]
    assert ident["sources"]["combined_sha256"]
    for arm in doc["arms"]:
        overhead = arm["control_overhead"]
        assert "ctrl_isl_bits" not in overhead
        assert isinstance(overhead["ctrl_isl_occupied_s"], float)
        assert isinstance(overhead["ctrl_isl_served_bits"], int)
        assert overhead["ctrl_isl_occupied_s"] >= 0.0
        assert overhead["ctrl_isl_served_bits"] >= 0
