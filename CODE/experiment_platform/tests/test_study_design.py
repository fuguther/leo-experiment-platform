import copy
from pathlib import Path

import pytest
import yaml

from CODE.experiment_platform import study_design as study
from CODE.experiment_platform import t1_suite
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


def test_suite_plans_preserve_all_declared_profile_parameters(tmp_path, monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("suite-plan compilation must not simulate")
    monkeypatch.setattr(kernel, "run_simulation", prohibited)

    design_dir = tmp_path / "design"
    report = study.compile_study(CONTRACT, design_dir)
    assert len(report["cells"]) == 16
    spec = yaml.safe_load(CONTRACT.read_text())
    conditions = {condition["id"]: condition for condition in spec["conditions"]}
    blocks = {block["id"]: block for block in spec["blocks"]}
    suite_parameter_keys = set(t1_suite.DEFAULT_EXPERIMENT_PARAMETERS)
    suite_parameter_keys.update(t1_suite.EXPERIMENT_PARAMETER_MULTIPLES)

    def parameter_values(config):
        values = {}
        for dotted in suite_parameter_keys:
            section, field = dotted.split(".", 1)
            assert section in config and field in config[section], dotted
            values[dotted] = config[section][field]
        return values

    declared = {}
    for cell in report["cells"]:
        config = config_mod.load_config_file(
            str(design_dir / cell["profile"]))["config"]
        declared[cell["cell_id"]] = config
        assert set(parameter_values(config)) == suite_parameter_keys
        for dotted, value in spec["fixed"].items():
            section, field = dotted.split(".", 1)
            assert config[section][field] == value
        for dotted, value in conditions[cell["condition"]]["overrides"].items():
            section, field = dotted.split(".", 1)
            assert config[section][field] == value
        assert config["scenario"]["num_satellites"] == 96
        assert config["demand"]["packet_bits"] == 12000
        assert config["execution"]["compute_servers_per_satellite"] == 1
        assert config["execution"]["compute_delay_s"] == 0.001
        assert config["async_routing"]["valid_window_s"] == 1.0
        assert config["time_alignment"]["arm"] == cell["arm"]
        assert config["time_alignment"]["predictor"] == blocks[
            cell["block"]]["predictor"]
        assert config["control_plane"]["advertise_interval_s"] == blocks[
            cell["block"]]["advertise_interval_s"]

    raster = study.ROOT / "CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif"
    study.compile_suite_plans(design_dir, raster)

    for block in report["execution_blocks"]:
        block_id = block["block"]
        source_cell = next(cell for cell in report["cells"]
                           if cell["block"] == block_id and cell["arm"] == "now")
        expected_config = declared[source_cell["cell_id"]]
        expected_parameters = parameter_values(expected_config)

        suite_contract = yaml.safe_load(
            (design_dir / "suites" / block_id / "contract.yaml").read_text())
        scenario = suite_contract["b_round"]["scenarios"][0]
        assert scenario.get("parameters") == expected_parameters

        bundle = yaml.safe_load(
            (design_dir / "suites" / block_id / "bundle" / "bundle.json").read_text())
        suite_cell = next(cell for cell in bundle["cells"]
                          if cell["cell_id"] == f"b-{block_id}-network-seed-7")
        config_path = Path(suite_cell["args"][suite_cell["args"].index("--config") + 1])
        materialized = config_mod.load_config_file(str(config_path))
        assert materialized["config"] == expected_config
        assert materialized["sha256"] == config_mod.resolve_config(
            expected_config)["sha256"]


def test_suite_plans_fail_closed_on_materialized_profile_drift(tmp_path, monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("suite-plan compilation must not simulate")
    monkeypatch.setattr(kernel, "run_simulation", prohibited)

    design_dir = tmp_path / "design"
    study.compile_study(CONTRACT, design_dir)
    raster = study.ROOT / "CODE/population_map/gpw_v4_population_count_rev11_2020_15_min.tif"
    compile_bundle = t1_suite.compile_bundle

    def compile_with_profile_drift(contract_path, out_dir):
        bundle = compile_bundle(contract_path, out_dir)
        out_dir = Path(out_dir)
        if out_dir.parent.name == "primary_steady":
            cell = next(c for c in bundle["cells"]
                        if c["cell_id"] == "b-primary_steady-network-seed-7")
            config_path = Path(cell["args"][cell["args"].index("--config") + 1])
            config = yaml.safe_load(config_path.read_text())
            config["execution"]["compute_delay_s"] = 9.0
            config_path.write_text(yaml.safe_dump(config, sort_keys=False))
            cell["input"] = t1_suite._cell_input_binding(
                cell, bundle_root=out_dir)
            bundle["bundle_fingerprint"] = t1_suite._bundle_fingerprint(bundle)
            t1_suite._write_json(out_dir / "bundle.json", bundle)
        return bundle

    def enforce_expected_gate(contract, bundle, **kwargs):
        if contract["design_readiness"]["status"] != "COST_PROBE_READY":
            raise t1_suite.SuiteError("test preserves the pending runtime gate")

    monkeypatch.setattr(t1_suite, "compile_bundle", compile_with_profile_drift)
    monkeypatch.setattr(t1_suite, "enforce_runtime_stage", enforce_expected_gate)
    with pytest.raises(ValueError, match="materialized suite profile differs"):
        study.compile_suite_plans(design_dir, raster)
