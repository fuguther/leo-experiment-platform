from pathlib import Path

import pytest

from CODE.leo_sim import config, kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row


def test_queue_policy_retries_after_temporary_no_coverage_and_delivers():
    src, dst = cell(0.0, 0.0), cell(0.0, 10.0)
    geo = StaticGeometry(1, visible=lambda _s, _lat, _lon, t: t >= 0.5)
    cfg = make_cfg({"scenario": {"duration_s": 3.0},
                    "access": {"unavailable_policy": "queue"}})
    result = kernel.run_simulation(cfg, [row(1, 0.0, src, dst)], geometry=geo)
    assert result["fates"][1] == "DELIVERED"
    assert result["fate_counts"]["ACCESS_REJECTED"] == 0
    assert result["congestion_metrics"]["packets"]["1"]["admitted_at"] >= 0.5


def test_queue_policy_keeps_packet_in_system_when_window_never_visible():
    src, dst = cell(0.0, 0.0), cell(0.0, 10.0)
    cfg = make_cfg({"scenario": {"duration_s": 1.0},
                    "access": {"unavailable_policy": "queue"}})
    result = kernel.run_simulation(
        cfg, [row(1, 0.0, src, dst)],
        geometry=StaticGeometry(1, visible=lambda *_: False))
    assert result["fates"][1] == "IN_SYSTEM_AT_STOP"
    assert result["fate_counts"]["ACCESS_REJECTED"] == 0


def test_reject_policy_preserves_no_coverage_fate():
    src, dst = cell(0.0, 0.0), cell(0.0, 10.0)
    result = kernel.run_simulation(
        make_cfg({"scenario": {"duration_s": 1.0}},),
        [row(1, 0.0, src, dst)],
        geometry=StaticGeometry(1, visible=lambda *_: False))
    assert result["fates"][1] == "ACCESS_REJECTED"


def test_queue_policy_keeps_existing_finite_uplink_overflow():
    src, dst = cell(0.0, 0.0), cell(0.0, 10.0)
    cfg = make_cfg({"scenario": {"duration_s": 1.0},
                    "access": {"unavailable_policy": "queue",
                               "uplink_queue_bits": 100}})
    result = kernel.run_simulation(
        cfg, [row(1, 0.0, src, dst, bits=80), row(2, 0.0, src, dst, bits=80)],
        geometry=StaticGeometry(1, visible=lambda *_: False))
    assert result["fates"][1] == "IN_SYSTEM_AT_STOP"
    assert result["fates"][2] == "ACCESS_QUEUE_OVERFLOW"


def test_queue_profile_only_changes_declared_access_boundary_fields():
    root = Path(__file__).resolve().parents[3]
    base = config.load_config_file(
        str(root / "CODE/leo_sim/profiles/mlab_multiod_burst_t0.yaml"))
    queue = config.load_config_file(
        str(root / "CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue.yaml"))
    allowed = {
        ("scenario", "name"),
        ("access", "unavailable_policy"),
        ("outputs", "out_dir"),
    }
    for group in base["config"]:
        assert set(queue["config"][group]) == set(base["config"][group])
        for key in base["config"][group]:
            if (group, key) not in allowed:
                assert queue["config"][group][key] == base["config"][group][key]


def test_280x14_e25_candidate_only_changes_declared_geometry_fields():
    root = Path(__file__).resolve().parents[3]
    baseline = config.load_config_file(
        str(root / "CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue.yaml"))
    candidate = config.load_config_file(
        str(root / "CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25.yaml"))
    allowed = {
        ("scenario", "name"),
        ("scenario", "duration_s"),
        ("scenario", "num_satellites"),
        ("scenario", "num_planes"),
        ("scenario", "min_elevation_deg"),
        # The 280/14 graph has registered diameter 17.  This candidate is the
        # full-information reference profile, so its propagation radius is an
        # intentional exception to the historical queue profile.
        ("control_plane", "vis_k"),
        ("demand", "emission_end_s"),
        ("outputs", "out_dir"),
    }
    assert set(candidate["config"]) == set(baseline["config"])
    for group in baseline["config"]:
        assert set(candidate["config"][group]) == set(baseline["config"][group])
        for key, expected in baseline["config"][group].items():
            if (group, key) not in allowed:
                assert candidate["config"][group][key] == expected
    assert candidate["config"]["scenario"]["num_satellites"] == 280
    assert candidate["config"]["scenario"]["num_planes"] == 14
    assert candidate["config"]["scenario"]["num_satellites"] % candidate["config"]["scenario"]["num_planes"] == 0
    assert (candidate["config"]["scenario"]["num_satellites"] //
            candidate["config"]["scenario"]["num_planes"]) == 20
    assert candidate["config"]["scenario"]["min_elevation_deg"] == 25
    assert candidate["config"]["scenario"]["duration_s"] == 30
    assert candidate["config"]["demand"]["emission_end_s"] == 20


@pytest.mark.parametrize("profile_name", [
    "mlab_multiod_burst_t0_queue_280x14_e25.yaml",
    "mlab_multiod_burst_t0_queue_280x14_e25_10mbps.yaml",
    "mlab_multiod_burst_t0_queue_280x14_e25_25mbps.yaml",
])
def test_e0_280x14_full_information_reference_covers_registered_diameter(
        profile_name):
    """The E0 reference arm must not inherit vis_k=12 from the old profile.

    The full dynamic-graph diameter (17) is registered by the E0 diagnostic
    evidence.  Information-ablation experiments may intentionally use a
    smaller radius, but these three E0 load arms are not ablations.
    """
    root = Path(__file__).resolve().parents[3]
    resolved = config.load_config_file(
        str(root / "CODE/leo_sim/profiles" / profile_name))
    cfg = resolved["config"]
    assert cfg["scenario"]["num_satellites"] == 280
    assert cfg["scenario"]["num_planes"] == 14
    registered_diameter = 17
    assert cfg["control_plane"]["vis_k"] == registered_diameter


@pytest.mark.parametrize(("arm", "offered_mbps"), [("10mbps", 10), ("25mbps", 25)])
def test_e0_recalibration_profiles_only_change_declared_load_fields(arm, offered_mbps):
    root = Path(__file__).resolve().parents[3]
    baseline = config.load_config_file(
        str(root / "CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25.yaml"))
    candidate = config.load_config_file(
        str(root / f"CODE/leo_sim/profiles/mlab_multiod_burst_t0_queue_280x14_e25_{arm}.yaml"))
    allowed = {("scenario", "name"), ("scenario", "duration_s"),
               ("demand", "offered_mbps"), ("demand", "emission_end_s"),
               ("outputs", "out_dir")}
    assert set(candidate["config"]) == set(baseline["config"])
    for group in baseline["config"]:
        assert set(candidate["config"][group]) == set(baseline["config"][group])
        for key, expected in baseline["config"][group].items():
            if (group, key) not in allowed:
                assert candidate["config"][group][key] == expected
    assert candidate["config"]["demand"]["offered_mbps"] == offered_mbps
    assert candidate["config"]["scenario"]["duration_s"] == 30
    assert candidate["config"]["demand"]["emission_end_s"] == 20
