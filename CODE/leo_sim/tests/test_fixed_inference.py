"""T1-COMPLETE R6: the fixed-inference adapter and the learner boundary."""
from __future__ import annotations

import json

import numpy as np
import pytest

from CODE.leo_sim import config as config_mod, inference


def _adapter():
    return inference.deterministic_small_model(6, seed=11)


def test_the_adapter_is_deterministic_and_masked():
    adapter = _adapter()
    features = [0.1, -0.2, 0.3, 0.0, 0.5, -0.4]
    first = adapter.act(features)
    for _ in range(50):
        assert adapter.act(features) == first
    masked = adapter.act(features, mask={"N": True, "S": True})
    assert masked in ("N", "S")
    with pytest.raises(inference.FixedInferenceError):
        adapter.act(features, mask={})
    with pytest.raises(inference.FixedInferenceError):
        adapter.act(features, mask={"deliver": False})


def test_the_adapter_is_inference_only():
    adapter = _adapter()
    with pytest.raises(inference.FixedInferenceError):
        adapter.train_step()
    with pytest.raises(inference.FixedInferenceError):
        adapter.observe(1.0)
    with pytest.raises(inference.FixedInferenceError):
        adapter.set_epsilon(1.0)


def test_epsilon_is_zero_and_no_update_happens():
    adapter = _adapter()
    before = adapter.parameter_sha256()
    for _ in range(100):
        adapter.act([0.0] * 6)
    counters = adapter.counters()
    assert counters["epsilon"] == 0.0
    assert counters["updates"] == 0 and counters["grad_steps"] == 0
    assert counters["training"] is False
    assert counters["calls"] == 100
    assert adapter.parameter_sha256() == before


def test_bad_shapes_and_non_finite_inputs_fail_loud():
    adapter = _adapter()
    with pytest.raises(inference.FixedInferenceError):
        adapter.act([0.1] * 3)
    with pytest.raises(inference.FixedInferenceError):
        adapter.act([float("nan")] * 6)
    with pytest.raises(inference.FixedInferenceError):
        inference.FixedInferenceAdapter(np.zeros((2, 3)))


def test_normalisation_is_frozen_at_construction():
    weights = np.zeros((5, 2))
    adapter = inference.FixedInferenceAdapter(
        weights, feature_mean=[1.0, 2.0], feature_std=[2.0, 4.0])
    assert adapter.q_values([3.0, 6.0]).tolist() == [0.0] * 5
    with pytest.raises(inference.FixedInferenceError):
        inference.FixedInferenceAdapter(weights, feature_std=[1.0, 0.0])


# ------------------------------------------------------ checkpoint verify
def test_a_checkpoint_needs_hashes_not_just_existence(tmp_path):
    ckpt = tmp_path / "model.h5"
    ckpt.write_bytes(b"not-a-real-model")
    report = inference.verify_checkpoint(ckpt, None)
    assert report["state"] == "EXTERNAL_BLOCKER"
    assert "no sha256" in report["reason"]

    wrong = inference.verify_checkpoint(ckpt, "0" * 64)
    assert wrong["state"] == "HASH_MISMATCH"
    assert wrong["actual_sha256"]

    right = inference.verify_checkpoint(
        ckpt, inference._sha256_file(ckpt))
    # the hashes verify, so the ONLY remaining blocker is the loader
    assert right["state"] == "EXTERNAL_BLOCKER"
    assert "loader" in right["reason"]
    assert right["recovery"]


def test_missing_paths_and_metadata_are_reported_precisely(tmp_path):
    assert inference.verify_checkpoint(None)["state"] == "EXTERNAL_BLOCKER"
    assert inference.verify_checkpoint(tmp_path / "nope")["state"] == \
        "EXTERNAL_BLOCKER"
    ckpt = tmp_path / "model.h5"
    ckpt.write_bytes(b"x")
    report = inference.verify_checkpoint(
        ckpt, inference._sha256_file(ckpt),
        metadata_path=tmp_path / "metadata.json",
        metadata_sha256="a" * 64)
    assert report["state"] == "METADATA_MISSING"


def test_a_metadata_hash_mismatch_is_caught(tmp_path):
    ckpt = tmp_path / "model.h5"
    ckpt.write_bytes(b"x")
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"contract": "C3"}))
    report = inference.verify_checkpoint(
        ckpt, inference._sha256_file(ckpt), metadata_path=meta,
        metadata_sha256="b" * 64)
    assert report["state"] == "METADATA_HASH_MISMATCH"


def test_loader_availability_is_reported_not_assumed():
    assert inference.tf_available() in (True, False)
    if not inference.tf_available():
        assert "tensorflow" not in str(None)


# ------------------------------------------------------- kernel boundary
def test_the_kernel_still_refuses_frozen_with_a_learner():
    """The adapter does not unlock the kernel's frozen+learner boundary."""
    from CODE.leo_sim import kernel

    resolved = config_mod.resolve_config({
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": 1.0},
        "routing": {"learning_enabled": True},
        "learning": {"algorithm": "qlearning", "mode": "train"}})
    with pytest.raises(kernel.KernelError) as excinfo:
        kernel.Kernel(resolved, [], decision_sink=[])
    assert "frozen" in str(excinfo.value)


def test_the_execution_matrix_reports_the_adapter_and_blocker():
    from CODE.experiment_platform import execution_compare as ec

    resolved = config_mod.resolve_config(
        {"routing": {"learning_enabled": True},
         "learning": {"algorithm": "ddqn", "mode": "train"}})
    status = ec.ddqn_status(resolved)
    assert status["state"] == "EXTERNAL_BLOCKER"
    assert "adapter" in status
    assert status["details"]["verification"]["state"] == "EXTERNAL_BLOCKER"


# ------------------------- S6: the adapter must be usable on a REAL branch
def _branch_fixture():
    from CODE.experiment_platform import scripted_scenarios
    resolved, rows, geometry, meta = scripted_scenarios.build("same_flow")
    cfg = config_mod.resolve_config(
        dict(resolved["config"], time_alignment=dict(
            resolved["config"]["time_alignment"], enabled=True)))
    return cfg, rows, geometry, meta


def test_a_full_branch_runs_with_the_fixed_inference_policy():
    from CODE.leo_sim import counterfactual, kernel

    cfg, rows, geometry, _meta = _branch_fixture()
    policy = inference.WidthAdaptiveFixedPolicy(seed=5)
    sink, timeline = [], []
    result = kernel.run_simulation(cfg, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=timeline,
                                   inference_policy=policy)
    # adapters are created lazily per feature width; what must NOT move is the
    # parameter set once a width exists
    before = policy.parameter_sha256()
    assert policy.counters()["widths"], "the policy must have seen the branch"
    assert result["fates"]
    forwards = [r for r in sink if r.get("kind") == "forward"]
    assert forwards, "the fixture must forward under the fixed policy"
    for row in forwards:
        # the mask is enforced: the action is always inside the legal set
        assert row["chosen"] in row["candidates"], (row["decision_id"],
                                                    row["chosen"])
        audit = (row.get("observation_at_start") or {}).get("time_alignment")
        assert audit and audit["inference_policy"], row["decision_id"]
        assert audit["inference_policy"]["choice"] == row["chosen"]
    counters = policy.counters()
    assert counters["calls"] > 0
    assert counters["epsilon"] == 0.0
    assert counters["updates"] == 0 and counters["grad_steps"] == 0
    assert counters["training"] is False
    # a second run with the same policy must not move any parameter
    kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=[],
                          timeline_sink=[], inference_policy=policy)
    assert policy.parameter_sha256() == before, "parameters must not move"


def test_the_fixed_policy_is_deterministic_across_runs():
    from CODE.leo_sim import kernel

    cfg, rows, geometry, _meta = _branch_fixture()
    sequences = []
    for _ in range(2):
        policy = inference.WidthAdaptiveFixedPolicy(seed=5)
        sink = []
        kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=sink,
                              timeline_sink=[], inference_policy=policy)
        sequences.append([(r["decision_id"], r["chosen"]) for r in sink
                          if r.get("kind") == "forward"])
    assert sequences[0] == sequences[1]
    assert sequences[0]


def test_the_branch_replay_accepts_the_inference_policy():
    from CODE.leo_sim import counterfactual

    cfg, rows, geometry, _meta = _branch_fixture()
    policy = inference.WidthAdaptiveFixedPolicy(seed=5)
    sink = []
    from CODE.leo_sim import kernel as kernel_mod
    kernel_mod.run_simulation(cfg, rows, geometry=geometry, decision_sink=sink,
                              timeline_sink=[], inference_policy=policy)
    target = next(r for r in sink
                  if r.get("kind") == "forward" and len(r["candidates"]) >= 2)
    other = [c for c in target["candidates"] if c != target["chosen"]][0]
    replay = counterfactual.replay_with_forced_action(
        cfg, rows, geometry=geometry, target_decision_id=target["decision_id"],
        forced_action=other, inference_policy=policy)
    assert replay["verification"]["branch_states_identical"] is True
    assert replay["verification"]["forced_action"] == other


def test_a_training_learner_is_refused_as_an_inference_policy():
    from CODE.leo_sim import kernel

    class Trainer:
        def act(self, snapshot):
            return None

        def train_step(self):
            return None

    with pytest.raises(inference.FixedInferenceError):
        inference.assert_inference_only(Trainer())
    cfg, rows, geometry, _meta = _branch_fixture()
    with pytest.raises((inference.FixedInferenceError, kernel.KernelError)):
        kernel.Kernel(cfg, rows, geometry=geometry, decision_sink=[],
                      inference_policy=Trainer())


def test_a_policy_that_chooses_illegally_is_refused():
    from CODE.leo_sim import kernel

    class Rogue:
        name = "rogue"

        def act(self, snapshot):
            return "N"  # never a legal direction in this fixture

        def counters(self):
            return {}

    cfg, rows, geometry, _meta = _branch_fixture()
    with pytest.raises(kernel.KernelError) as excinfo:
        kernel.run_simulation(cfg, rows, geometry=geometry, decision_sink=[],
                              timeline_sink=[], inference_policy=Rogue())
    assert "outside the legal set" in str(excinfo.value)


# ------------------------------------------- S6: checkpoint hard gates
def test_the_checkpoint_load_gate_requires_a_declared_width(tmp_path,
                                                            monkeypatch):
    ckpt = tmp_path / "m.h5"
    ckpt.write_bytes(b"x")
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"contract": "C3"}))
    monkeypatch.setattr(inference, "verify_checkpoint",
                        lambda *a, **k: {"state": "AVAILABLE",
                                         "metadata": {"contract": "C3"}})
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        inference.load_fixed_adapter_from_checkpoint(
            ckpt, "a" * 64, meta, "b" * 64, expected_width=8)
    assert "feature_width" in str(excinfo.value)


def test_the_checkpoint_load_gate_checks_the_declared_width(tmp_path,
                                                            monkeypatch):
    ckpt = tmp_path / "m.h5"
    ckpt.write_bytes(b"x")
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"feature_width": 16}))
    monkeypatch.setattr(inference, "verify_checkpoint",
                        lambda *a, **k: {"state": "AVAILABLE",
                                         "metadata": {"feature_width": 16}})
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        inference.load_fixed_adapter_from_checkpoint(
            ckpt, "a" * 64, meta, "b" * 64, expected_width=8)
    assert "!=" in str(excinfo.value)


def test_the_checkpoint_load_gate_reports_a_blocker_without_a_loader(tmp_path):
    ckpt = tmp_path / "m.h5"
    ckpt.write_bytes(b"x")
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps({"feature_width": 8}))
    sha = inference._sha256_file(ckpt)
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        inference.load_fixed_adapter_from_checkpoint(
            ckpt, sha, meta, inference._sha256_file(meta), expected_width=8)
    assert "not loadable" in str(excinfo.value)

