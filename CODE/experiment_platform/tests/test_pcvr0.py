from __future__ import annotations

import math

import pytest

from CODE.algorithms.pcvr0.policy import (
    FEATURE_NAMES, OBJECTIVES, PCVRPolicy, extract_snapshot_features,
)
from CODE.algorithms.pcvr0.train import (\n    TrainingError, publish_model, train_rows, validate_rows,\n)\nfrom CODE.algorithms.pcvr0.collect import FeatureCapturePolicy\nfrom CODE.leo_sim import policy_plugin
from CODE.algorithms.pcvr0.collect import CollectionError, cohort_outcome
from CODE.leo_sim import time_alignment as ta


def _snapshot():
    north = ta.ResourceKey(1, "E", ta.KIND_ISL, generation=1)
    east = ta.ResourceKey(2, "N", ta.KIND_ISL, generation=1)
    history = (
        ta.StateSample(north, 8.0, 8.5, 1000.0, 1e6),
        ta.StateSample(north, 9.0, 9.5, 1200.0, 1e6),
        ta.StateSample(east, 8.0, 8.5, 12000.0, 1e6),
        ta.StateSample(east, 9.0, 9.5, 15000.0, 1e6),
    )
    return ta.make_snapshot(
        satellite=0,
        snapshot_at=10.0,
        history=history,
        legal_directions=("N", "E"),
        resources={"N": north, "E": east},
        egress_queue_bits={"N": 0.0, "E": 1000.0},
        link_rate_bps={"N": 1e6, "E": 1e6},
        link_propagation_s={"N": 0.001, "E": 0.02},
        peer_process_s={"N": 0.0, "E": 0.0},
        remaining_prop_s={"N": 0.001, "E": 0.02},
        resource_service_rate_bps={"N": 1e6, "E": 1e6},
        local_egress_in_service_s={"N": 0.0, "E": 0.0},
        pkt_bits=1000,
        arm="candidate",
        predictor="bounded_linear",
        history_limit=8,
        common_rule="median_eta",
    )


def _model(*, p99_error=0.001):
    width = len(FEATURE_NAMES)
    coefficients = {
        target: [0.0] * (width + 1)
        for target in OBJECTIVES
    }
    # Make the model prefer E on p99 while leaving PDR tied.
    coefficients["p99_e2e_s"][1 + FEATURE_NAMES.index("direction_N")] = 0.1
    return {
        "schema": "pcvr0-model/v1",
        "algorithm": "PCVR-0",
        "status": "trained",
        "feature_names": list(FEATURE_NAMES),
        "feature_mean": [0.0] * width,
        "feature_scale": [1.0] * width,
        "coefficients": coefficients,
        "validation_abs_error_p90": {
            "delivery_loss": 0.0,
            "p99_e2e_s": p99_error,
            "mean_e2e_s": 0.0,
            "control_packets": 0.0,
        },
    }


def test_feature_contract_is_finite_and_baseline_is_legal():
    snapshot = _snapshot()
    features, baseline = extract_snapshot_features(snapshot)
    assert set(features) == {"N", "E"}
    assert baseline in snapshot.legal_directions
    for row in features.values():
        assert tuple(row) == FEATURE_NAMES
        assert all(math.isfinite(value) for value in row.values())


def test_pcvr0_switches_only_when_empirical_guard_is_cleared():
    snapshot = _snapshot()
    features, baseline = extract_snapshot_features(snapshot)
    assert baseline == "N"
    policy = PCVRPolicy(model=_model(p99_error=0.001))
    assert policy.act(snapshot) == "E"
    counters = policy.counters()
    assert counters["calls"] == 1
    assert counters["switches"] == 1
    assert counters["training"] is False


def test_pcvr0_falls_back_when_predicted_gain_is_ambiguous():
    snapshot = _snapshot()
    _, baseline = extract_snapshot_features(snapshot)
    policy = PCVRPolicy(model=_model(p99_error=1.0))
    assert policy.act(snapshot) == baseline
    assert policy.counters()["ambiguous_fallbacks"] == 1


def _training_rows():
    rows = []
    for world in ("w1", "w2", "w3"):
        for action in ("N", "E"):
            features = {name: 0.0 for name in FEATURE_NAMES}
            features[f"direction_{action}"] = 1.0
            rows.append({
                "schema": "pcvr0-training-row/v1",
                "sample_id": f"{world}-decision-1",
                "world_id": world,
                "cohort_id": f"{world}-cohort",
                "policy_id": "baseline-policy-v1",
                "branch_fingerprint": f"{world}-prefix",
                "decision_id": 1,
                "baseline_action": "N",
                "action": action,
                "features": features,
                "outcome": {
                    "delivery_loss": 0.0,
                    "p99_e2e_s": (0.2 if action == "N" else 0.1) + 0.01 * int(world[-1]),
                    "mean_e2e_s": 0.1 if action == "N" else 0.08,
                    "control_packets": 5.0 if action == "N" else 6.0,
                },
                "offered_packets": 10,
                "natural_end": True,
            })
    return rows


def test_training_uses_world_grouped_validation_and_emits_model():
    rows = _training_rows()
    model = train_rows(rows, "a" * 64, ridge_alpha=1.0)
    assert model["validation_method"] == "leave_one_world_out"
    assert model["training_worlds"] == ["w1", "w2", "w3"]
    assert model["training_rows"] == 6
    assert all(
        math.isfinite(model["validation_abs_error_p90"][name])
        for name in OBJECTIVES
    )


def test_training_refuses_single_world_data():
    rows = [row for row in _training_rows() if row["world_id"] == "w1"]
    with pytest.raises(TrainingError, match="three distinct world_id"):
        validate_rows(rows)


def test_training_refuses_unpaired_actions():
    rows = [
        row for row in _training_rows()
        if not (row["sample_id"] == "w1-decision-1" and row["action"] == "E")
    ]
    with pytest.raises(TrainingError, match="fewer than two action forks"):
        validate_rows(rows)


def test_cohort_outcome_uses_offered_denominator_and_nearest_rank_p99():
    result = {
        "natural_end": True,
        "interrupted": False,
        "fates": {"1": "DELIVERED", "2": "DATA_DEADLINE_EXPIRED"},
        "fate_counts": {"DELIVERED": 1, "DATA_DEADLINE_EXPIRED": 1},
        "congestion_metrics": {
            "packets": {"1": {"e2e_s": 0.125}},
        },
        "control": {"counters": {"entered_queue": 3}},
    }
    outcome = cohort_outcome(result, offered_packets=2)
    assert outcome == {
        "delivery_loss": 0.5,
        "p99_e2e_s": 0.125,
        "mean_e2e_s": 0.125,
        "control_packets": 3.0,
    }


def test_interrupted_run_is_not_a_training_label():
    result = {
        "natural_end": False,
        "interrupted": True,
        "fates": {},
        "fate_counts": {},
        "congestion_metrics": {"packets": {}},
        "control": {"counters": {"entered_queue": 0}},
    }
    with pytest.raises(CollectionError, match="natural_end"):
        cohort_outcome(result, offered_packets=0)



def test_feature_capture_policy_preserves_time_alignment_baseline():
    snapshot = _snapshot()
    _, baseline = extract_snapshot_features(snapshot)
    policy = FeatureCapturePolicy()
    assert policy.act(snapshot) == baseline
    assert policy.records[0]["baseline_action"] == baseline
    assert policy.counters()["training"] is False


def test_trained_bundle_loads_through_platform_plugin_boundary(tmp_path):
    model = train_rows(_training_rows(), "b" * 64, ridge_alpha=1.0)
    bundle = tmp_path / "bundle"
    publish_model(model, bundle)
    policy = policy_plugin.load_policy_plugin(bundle / "manifest.json")
    action = policy.act(_snapshot())
    assert action in {"N", "E"}
    assert policy.counters()["training"] is False
    assert policy.counters()["calls"] == 1
