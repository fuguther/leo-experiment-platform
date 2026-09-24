"""S-1: name the cause, not the symptom, when capacity sampling is off.

execution.available_capacity_interval_s defaults to null and is an explicit
opt-in (kernel._available_capacity_ticker returns immediately).  Without it
link_available_windows is empty, so every link capacity is zero and the first
served window raised "served bits without available capacity" -- a message
about the symptom that sends the reader looking for a physical inconsistency
that is not there.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/smoke.yaml"
T1_PROFILE = "CODE/leo_sim/profiles/t1_pressure_corridor.yaml"


@pytest.fixture(scope="module")
def unsampled_ledgers(tmp_path_factory):
    """A real run that did NOT opt into capacity sampling."""
    import json
    out = tmp_path_factory.mktemp("s1")
    verdict = subprocess.run(
        [sys.executable, "-m", "CODE.leo_sim", "run", "--config", SMOKE,
         "--out", str(out), "--timeline-log", str(out / "t.jsonl")],
        cwd=ROOT, capture_output=True, text=True)
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    return json.loads((out / "ledgers.json").read_text())


def test_missing_capacity_sampling_names_the_config_key(unsampled_ledgers):
    from CODE.experiment_platform import isl_pressure
    assert not (unsampled_ledgers.get("link_available_windows") or [])
    with pytest.raises(isl_pressure.PressureAnalysisError) as caught:
        isl_pressure.analyze_windows(unsampled_ledgers)
    message = str(caught.value)
    assert "available_capacity_interval_s" in message
    assert "no available-capacity windows" in message


def test_the_t1_profile_opts_into_capacity_sampling():
    """The T1 profile must set it, otherwise this line cannot be analyzed."""
    text = (ROOT / T1_PROFILE).read_text()
    assert "available_capacity_interval_s:" in text
    value = text.split("available_capacity_interval_s:")[1].split()[0]
    assert value != "null", value
