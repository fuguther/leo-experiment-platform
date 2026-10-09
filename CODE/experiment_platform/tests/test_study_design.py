import copy
from pathlib import Path

import pytest
import yaml

from CODE.experiment_platform import study_design as study
from CODE.leo_sim import config as config_mod
from CODE.leo_sim import kernel


CONTRACT = study.ROOT / "CODE/work/WP-T1-COMPLETE/time_alignment_study.yaml"


def test_materialized_blocks_change_only_arm_and_never_call_kernel(tmp_path, monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("design compilation must not simulate")
    monkeypatch.setattr(kernel, "run_simulation", prohibited)
    report = study.compile_study(CONTRACT, tmp_path / "design")
    assert len(report["cells"]) == 16
    assert report["simulator_calls"] == 0
    for block in report["execution_blocks"]:
        assert block["driver_options"]["--deadline-s"] == "4.0"
        assert block["driver_options"]["--window-start"] == "2.0"
        assert block["driver_options"]["--window-end"] == "8.0"
        assert block["requires_t1_suite_launch_context"] is True
    groups = {}
    for cell in report["cells"]:
        cfg = config_mod.load_config_file(str(tmp_path / "design" / cell["profile"]))["config"]
        assert cfg["control_plane"]["advertisement_protocol_version"] == 2
        assert cfg["scenario"]["num_satellites"] == 96
        assert cfg["demand"]["packet_bits"] == 12000
        assert cfg["execution"]["node_process_delay_s"] == 0
        groups.setdefault(cell["block"], []).append(study._without_arm(cfg))
    assert all(all(c == configs[0] for c in configs) for configs in groups.values())
    assert groups["hold_last_burst"][0]["time_alignment"]["predictor"] == "hold_last"
    assert groups["frequent_advertisement_burst"][0]["control_plane"]["advertise_interval_s"] == .25


@pytest.mark.parametrize("mutation", ["unknown", "deadline", "duplicate", "cap", "training"])
def test_invalid_design_rejected_before_materialization(tmp_path, mutation):
    spec = yaml.safe_load(CONTRACT.read_text())
    if mutation == "unknown":
        spec["conditions"][0]["overrides"]["links.isl_rate_mbps"] = 1
    elif mutation == "deadline":
        spec["primary"]["deadline_s"] = 40
    elif mutation == "duplicate":
        spec["blocks"].append(copy.deepcopy(spec["blocks"][0]))
    elif mutation == "cap":
        spec["run_boundary"]["maximum_materialized_cells"] = 1
    else:
        spec["fixed"]["learning.algorithm"] = "ddqn"
        spec["fixed"]["routing.learning_enabled"] = True
    contract = tmp_path / "bad.yaml"
    contract.write_text(yaml.safe_dump(spec))
    out = tmp_path / "out"
    with pytest.raises((ValueError, config_mod.ConfigError)):
        study.compile_study(contract, out)
    assert not out.exists()


def test_existing_output_preserved(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "original").write_text("keep")
    with pytest.raises(ValueError, match="new directory"):
        study.compile_study(CONTRACT, out)
    assert (out / "original").read_text() == "keep"
