"""Tests for CODE.leo_sim.config — strict versioned YAML configuration."""
import pytest

from CODE.leo_sim import config


def test_defaults_resolve_and_hash_stable():
    r1 = config.resolve_config()
    r2 = config.resolve_config()
    assert r1["version"] == config.CONFIG_SCHEMA_VERSION
    assert r1["sha256"] == r2["sha256"]
    assert r1["canonical_json"] == r2["canonical_json"]
    for group in config.SCHEMA:
        assert group in r1["config"]


def test_access_unavailable_policy_defaults_to_reject_and_has_enum():
    assert config.resolve_config()["config"]["access"]["unavailable_policy"] == "reject"
    assert config.resolve_config({"access": {"unavailable_policy": "queue"}})["config"]["access"]["unavailable_policy"] == "queue"
    with pytest.raises(config.ConfigError, match="unavailable_policy"):
        config.resolve_config({"access": {"unavailable_policy": "later"}})


def test_unknown_field_rejected():
    with pytest.raises(config.ConfigError, match="unknown field"):
        config.resolve_config({"scenario": {"bogus": 1}})
    with pytest.raises(config.ConfigError, match="unknown top-level"):
        config.resolve_config({"gateway": {}})


def test_profile_and_overrides_resolve():
    r = config.resolve_config(profile="smoke", overrides={"scenario": {"duration_s": 3.0}})
    assert r["config"]["scenario"]["duration_s"] == 3.0
    assert r["config"]["scenario"]["num_satellites"] == 12
    with pytest.raises(config.ConfigError, match="unknown profile"):
        config.resolve_config(profile="nope")


@pytest.mark.parametrize("policy", ["info_queue", "info_physical"])
def test_information_ladder_policies_are_valid_config_values(policy):
    resolved = config.resolve_config({"routing": {"policy": policy}})
    assert resolved["config"]["routing"]["policy"] == policy


def test_invalid_combinations_rejected():
    with pytest.raises(config.ConfigError, match="dual_connect"):
        config.resolve_config({"access": {"association": "mbb"}})
    with pytest.raises(config.ConfigError, match="csv_path"):
        config.resolve_config({"demand": {"mode": "csv"}})
    with pytest.raises(config.ConfigError, match="burst"):
        config.resolve_config({"demand": {"mode": "burst"}})
    with pytest.raises(config.ConfigError, match="learning"):
        config.resolve_config({"routing": {"learning_enabled": True}})
    with pytest.raises(config.ConfigError, match="divisible"):
        config.resolve_config({"scenario": {"num_satellites": 7, "num_planes": 3}})


def test_available_capacity_sampling_interval_is_positive():
    with pytest.raises(config.ConfigError, match="available_capacity_interval_s"):
        config.resolve_config({"execution": {"available_capacity_interval_s": 0}})
    with pytest.raises(config.ConfigError, match="available_capacity_interval_s"):
        config.resolve_config({"execution": {"available_capacity_interval_s": True}})
    with pytest.raises(config.ConfigError, match="available_capacity_interval_s"):
        config.resolve_config({"execution": {"available_capacity_interval_s": 0.001}})
    with pytest.raises(config.ConfigError, match="100000"):
        config.resolve_config({
            "scenario": {"duration_s": 2000.0},
            "execution": {"available_capacity_interval_s": 0.01},
        })
    with pytest.raises(config.ConfigError, match="100000"):
        config.resolve_config({
            "scenario": {"duration_s": 2000.0},
            "topology": {"recompute_interval_s": 0.005},
            "execution": {"available_capacity_interval_s": 1.0},
        })


def test_burst_window_must_intersect_scenario_horizon():
    base = {
        "scenario": {"duration_s": 120.0},
        "demand": {"mode": "burst", "burst_start_s": 121.0,
                   "burst_duration_s": 10.0, "burst_multiplier": 5.0},
    }
    with pytest.raises(config.ConfigError, match="intersect"):
        config.resolve_config(base)
    # window starting exactly at the horizon is also out of [0, duration)
    base["demand"]["burst_start_s"] = 120.0
    with pytest.raises(config.ConfigError, match="intersect"):
        config.resolve_config(base)
    # an intersecting window (including zero-length overlap edge) is valid
    base["demand"]["burst_start_s"] = 110.0
    ok = config.resolve_config(base)
    assert ok["config"]["demand"]["mode"] == "burst"


def test_mlab_burst_requires_complete_intersecting_window():
    with pytest.raises(config.ConfigError, match="mode=mlab"):
        config.resolve_config({
            "demand": {"mode": "mlab", "burst_start_s": 1.0},
        })
    with pytest.raises(config.ConfigError, match="intersect"):
        config.resolve_config({
            "scenario": {"duration_s": 10.0},
            "demand": {"mode": "mlab", "burst_start_s": 10.0,
                       "burst_duration_s": 1.0},
        })


def test_learning_eval_requires_sha_bound_checkpoint():
    with pytest.raises(config.ConfigError, match="checkpoint_path and checkpoint_sha256"):
        config.resolve_config({
            "routing": {"policy": "hop", "learning_enabled": True},
            "control_plane": {"enabled": True},
            "learning": {"algorithm": "ddqn", "mode": "eval"},
        })
    with pytest.raises(config.ConfigError, match="lowercase SHA-256"):
        config.resolve_config({
            "routing": {"policy": "hop", "learning_enabled": True},
            "control_plane": {"enabled": True},
            "learning": {"algorithm": "ddqn", "mode": "eval",
                         "checkpoint_path": "/tmp/model.keras",
                         "checkpoint_sha256": "bad"},
        })
    # DDQN eval with valid checkpoint SHA but no metadata SHA pin must fail
    # closed (the metadata file is the only contract anchor for opaque
    # model artifacts).
    with pytest.raises(config.ConfigError,
                       match="checkpoint_metadata_sha256"):
        config.resolve_config({
            "routing": {"policy": "hop", "learning_enabled": True},
            "control_plane": {"enabled": True},
            "learning": {"algorithm": "ddqn", "mode": "eval",
                         "checkpoint_path": "/tmp/model.keras",
                         "checkpoint_sha256": "ab" * 32},
        })


def test_bool_rejected_for_numeric():
    with pytest.raises(config.ConfigError, match="bool"):
        config.resolve_config({"scenario": {"seed": True}})


def test_load_config_file(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "profile: smoke\nscenario:\n  duration_s: 2.0\n  num_satellites: 6\n  num_planes: 2\n"
    )
    r = config.load_config_file(str(p))
    assert r["config"]["scenario"]["duration_s"] == 2.0
    bad = tmp_path / "bad.yaml"
    bad.write_text("config_version: other/v9\n")
    with pytest.raises(config.ConfigError, match="config_version"):
        config.load_config_file(str(bad))


def test_ge_dwell_rejects_bool():
    # bool is an int subclass: must not pass the GE dwell type check
    with pytest.raises(config.ConfigError, match="mean dwell"):
        config.resolve_config({"links": {"ge_enabled": True,
                                         "ge_gsl": {"mean_good_s": True,
                                                    "mean_bad_s": 1.0}}})


# ---------------------------------------------------------------- Task 1:
# global populated-land direct-access scene configuration contract.

NEW_DEMAND_FIELDS = (
    "temporal_model",
    "utc_start_hour",
    "population_destination_sampler",
    "destination_rejection_max_draws",
    "nested_master_offered_mbps",
)


def test_global_scene_defaults_resolve():
    cfg = config.resolve_config()["config"]
    assert cfg["scenario"]["geometry_epoch_s"] == 0.0
    for field in NEW_DEMAND_FIELDS:
        assert field in cfg["demand"]
    assert cfg["demand"]["temporal_model"] == "constant"
    assert cfg["demand"]["utc_start_hour"] == 0.0
    assert cfg["demand"]["population_destination_sampler"] == "scan"
    assert cfg["demand"]["destination_rejection_max_draws"] == 10_000
    assert cfg["demand"]["nested_master_offered_mbps"] is None


def _population_gravity_demand(**extra):
    """Minimal demand group that passes population_gravity semantics."""
    return {"mode": "population_gravity",
            "population_path": "/fake/pop.tif",
            **extra}


def test_geometry_epoch_s_must_be_finite_ge_zero():
    for bad in (-1.0, -1, float("-inf")):
        with pytest.raises(config.ConfigError, match="geometry_epoch_s"):
            config.resolve_config({"scenario": {"geometry_epoch_s": bad}})
    ok = config.resolve_config({"scenario": {"geometry_epoch_s": 0.0}})
    assert ok["config"]["scenario"]["geometry_epoch_s"] == 0.0
    ok = config.resolve_config({"scenario": {"geometry_epoch_s": 1234.5}})
    assert ok["config"]["scenario"]["geometry_epoch_s"] == 1234.5
    # bool is an int subclass and must fail the numeric type check
    with pytest.raises(config.ConfigError, match="geometry_epoch_s"):
        config.resolve_config({"scenario": {"geometry_epoch_s": True}})


def test_temporal_model_must_be_known_enum():
    for bad in ("solar", "step", "", 1, True):
        with pytest.raises(config.ConfigError, match="temporal_model"):
            config.resolve_config({"demand": {"temporal_model": bad}})
    for good in ("constant", "local_diurnal_cosine"):
        assert config.resolve_config(
            {"demand": _population_gravity_demand(
                temporal_model=good)})["config"]["demand"][
            "temporal_model"] == good


def test_local_diurnal_cosine_only_valid_with_population_gravity():
    base = {"demand": {"temporal_model": "local_diurnal_cosine"}}
    for mode in ("uniform", "gravity", "hotspot", "diurnal", "mlab"):
        with pytest.raises(config.ConfigError, match="local_diurnal_cosine"):
            config.resolve_config({"demand": {"mode": mode,
                                              "temporal_model": "local_diurnal_cosine"}})
    # burst/csv only reach the temporal-model gate once their own
    # prerequisites are satisfied
    with pytest.raises(config.ConfigError, match="local_diurnal_cosine"):
        config.resolve_config({"demand": {"mode": "burst",
                                          "burst_start_s": 1.0,
                                          "burst_duration_s": 2.0,
                                          "temporal_model": "local_diurnal_cosine"}})
    with pytest.raises(config.ConfigError, match="local_diurnal_cosine"):
        config.resolve_config({"demand": {"mode": "csv", "csv_path": "/tmp/in.csv",
                                          "temporal_model": "local_diurnal_cosine"}})


def test_utc_start_hour_range_and_mode_gating():
    for bad in (-0.1, 24.0, 24, float("nan")):
        with pytest.raises(config.ConfigError, match="utc_start_hour"):
            config.resolve_config({"demand": {"utc_start_hour": bad}})
    for ok in (0.0, 11.5, 23.999):
        cfg = config.resolve_config(
            {"demand": _population_gravity_demand(utc_start_hour=ok)})
        assert cfg["config"]["demand"]["utc_start_hour"] == ok
    # only population_gravity may set a non-zero UTC start hour
    for mode in ("uniform", "gravity", "diurnal", "mlab"):
        with pytest.raises(config.ConfigError, match="utc_start_hour"):
            config.resolve_config({"demand": {"mode": mode,
                                              "utc_start_hour": 5.0}})
    # zero start hour is the default and is not special-cased per mode
    for mode in ("uniform", "diurnal"):
        cfg = config.resolve_config({"demand": {"mode": mode,
                                                "utc_start_hour": 0.0}})
        assert cfg["config"]["demand"]["utc_start_hour"] == 0.0


def test_population_destination_sampler_enum_and_mode_gating():
    for bad in ("uniform", "nearest", "", 1):
        with pytest.raises(config.ConfigError,
                           match="population_destination_sampler"):
            config.resolve_config(
                {"demand": {"population_destination_sampler": bad}})
    for mode in ("uniform", "gravity", "diurnal", "mlab"):
        with pytest.raises(config.ConfigError,
                           match="population destination sampler"):
            config.resolve_config(
                {"demand": {"mode": mode,
                            "population_destination_sampler": "alias_rejection"}})
    # scan is the default and is valid everywhere
    for mode in ("uniform", "gravity"):
        cfg = config.resolve_config(
            {"demand": {"mode": mode,
                        "population_destination_sampler": "scan"}})
        assert cfg["config"]["demand"]["population_destination_sampler"] == "scan"
    cfg = config.resolve_config(
        {"demand": _population_gravity_demand(
            population_destination_sampler="alias_rejection")})
    assert cfg["config"]["demand"][
        "population_destination_sampler"] == "alias_rejection"


def test_destination_rejection_max_draws_boundary():
    for bad in (0, -1, True):
        with pytest.raises(config.ConfigError,
                           match="destination_rejection_max_draws"):
            config.resolve_config(
                {"demand": {"destination_rejection_max_draws": bad}})
    ok = config.resolve_config(
        {"demand": {"destination_rejection_max_draws": 1}})
    assert ok["config"]["demand"]["destination_rejection_max_draws"] == 1


def test_nested_master_load_mode_gating_and_ordering():
    for mode in ("uniform", "gravity", "diurnal"):
        with pytest.raises(config.ConfigError, match="nested master"):
            config.resolve_config(
                {"demand": {"mode": mode,
                            "nested_master_offered_mbps": 80.0}})
    # master must be >= the child offered load
    with pytest.raises(config.ConfigError, match="nested_master_offered_mbps"):
        config.resolve_config(
            {"demand": _population_gravity_demand(
                offered_mbps=20.0, nested_master_offered_mbps=10.0)})
    cfg = config.resolve_config(
        {"demand": _population_gravity_demand(
            offered_mbps=20.0, nested_master_offered_mbps=20.0)})
    assert cfg["config"]["demand"]["nested_master_offered_mbps"] == 20.0
    cfg = config.resolve_config(
        {"demand": _population_gravity_demand(
            offered_mbps=5.0, nested_master_offered_mbps=80.0)})
    assert cfg["config"]["demand"]["nested_master_offered_mbps"] == 80.0
    # bool must fail the numeric-or-null type check
    with pytest.raises(config.ConfigError, match="nested_master_offered_mbps"):
        config.resolve_config(
            {"demand": _population_gravity_demand(
                nested_master_offered_mbps=True)})


def test_geometry_epoch_s_excluded_from_trace_identity():
    """geometry changes geometry, not trace bytes: it must never enter any
    trace identity payload."""
    cfg = config.resolve_config({"scenario": {"geometry_epoch_s": 4321.0}})
    for payload in (config.trace_identity_payload(cfg),
                    config.trace_identity_payload_v2(cfg)):
        assert "geometry_epoch_s" not in payload
        assert "geometry_epoch_s" not in payload["scenario"]


def test_v3_payload_has_new_demand_fields_v2_strips_exactly_them():
    cfg = config.resolve_config({"demand": _population_gravity_demand()})
    v3 = config.trace_identity_payload(cfg)
    v2 = config.trace_identity_payload_v2(cfg)
    assert v3["identity_version"] == "leo-sim-trace-identity/v3"
    assert v2["identity_version"] == "leo-sim-trace-identity/v2"
    for field in NEW_DEMAND_FIELDS:
        assert field in v3["demand"]
        assert field not in v2["demand"]
    # the v2 builder removes exactly those fields and nothing else
    assert set(v2["demand"]) == set(v3["demand"]) - set(NEW_DEMAND_FIELDS)
    assert v3["scenario"] == v2["scenario"]
    assert v3["endpoints"] == v2["endpoints"]
    assert v3["execution"] == v2["execution"]
    assert v3["config_version"] == v2["config_version"]


def test_v2_and_v3_identity_hashes_differ():
    cfg = config.resolve_config({"demand": _population_gravity_demand()})
    assert config.trace_identity_sha256_v2(cfg) != \
        config.trace_identity_sha256(cfg)
    assert len(config.trace_identity_sha256_v2(cfg)) == 64
    assert len(config.trace_identity_sha256(cfg)) == 64
