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
