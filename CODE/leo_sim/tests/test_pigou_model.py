"""Focused contract tests for the staged Pigou-MetaDRL implementation."""
from __future__ import annotations

import importlib
import math
import sys
from pathlib import Path

import numpy as np
import pytest

_STAGING_ROOT = Path(__file__).resolve().parents[3]
if str(_STAGING_ROOT) not in sys.path:
    sys.path.insert(0, str(_STAGING_ROOT))

from CODE.leo_sim.rl.pigou_model import (  # noqa: E402
    DIRECTIONS,
    NumpyPigou,
    _EXPECTED_ONLINE_PARAMETER_COUNT,
    _EXPECTED_TRAINING_PARAMETER_COUNT,
    _tensorflow_forward,
    _training_initial_parameters,
    _validate_training_inputs,
    train,
)


def _fixture(*, variant: str = "full", snapshots: int = 5,
             decisions: int = 13, seed: int = 17):
    rng = np.random.default_rng(seed)
    x_2hop = rng.normal(size=(snapshots, 4, 4, 8)).astype(np.float32)
    # Two groups are fully masked and must remain finite through the GAT.
    x_2hop[0, 1] = 0.0
    x_2hop[-1, 3] = 0.0
    snap_cur = rng.normal(size=(snapshots, 4, 8)).astype(np.float32)
    hist_seq = rng.normal(size=(snapshots, 4, 8, 9)).astype(np.float32)
    y_priv = np.stack((
        rng.uniform(0.0, 1.0, size=(snapshots, 4)),
        rng.uniform(-1.0, 1.0, size=(snapshots, 4)),
        rng.uniform(0.0, 1.0, size=(snapshots, 4)),
    ), axis=-1).astype(np.float32)
    y_valid = rng.random(size=(snapshots, 4, 3)) > 0.2
    y_valid[0, 0] = True
    y_valid[-1, -1] = False
    # Invalid values are intentionally hostile; masked training must replace
    # them before masked arithmetic in TensorFlow.
    y_priv[~y_valid] = np.nan

    actions = (np.arange(decisions, dtype=np.int64) % 4).astype(np.int64)
    snap_id = (np.arange(decisions, dtype=np.int64) % snapshots).astype(np.int32)
    g_ext = rng.uniform(0.0, 3.0, size=(decisions,))
    snapshot_data = {
        "x_2hop": x_2hop,
        "snap_cur": snap_cur,
        "hist_seq": hist_seq,
        "y_priv": y_priv,
        "y_valid": y_valid,
    }
    decision_data = {
        "snap_id": snap_id,
        "x_pkt_global": rng.normal(size=(decisions, 10)).astype(np.float32),
        "x_pkt_port": rng.normal(size=(decisions, 4, 10)).astype(np.float32),
        "action": actions,
        "g_ego": rng.uniform(0.0, 1.0, size=(decisions,)).astype(np.float32),
        "g_ext": g_ext.astype(np.float32),
    }
    return snapshot_data, decision_data


def _require_tensorflow_213():
    import os
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    spec = importlib.util.find_spec("tensorflow")
    if spec is None:
        pytest.skip("TensorFlow 2.13 is not installed on this local host")
    tf = importlib.import_module("tensorflow")
    if ".".join(str(tf.__version__).split(".")[:2]) != "2.13":
        pytest.skip(f"frozen parity/training test needs TensorFlow 2.13, got {tf.__version__}")
    return tf


def test_frozen_parameter_counts_and_neutral_outputs():
    expected_stage_counts = {
        "full": (18_720, 2_625),
        "mean": (18_720, 2_130),
        "frame": (288, 2_625),
        "no_ais": (18_720, 2_625),
        "no_ext": (18_720, 2_625),
    }
    assert DIRECTIONS == ("N", "S", "E", "W")
    for variant, expected_total in _EXPECTED_ONLINE_PARAMETER_COUNT.items():
        model = NumpyPigou.neutral(seed=91, variant=variant)
        assert model.parameter_count == expected_total
        assert (model.stage_i_parameter_count, model.stage_ii_parameter_count) \
            == expected_stage_counts[variant]
        assert _EXPECTED_TRAINING_PARAMETER_COUNT[variant] == expected_total + 579

        x_2hop = np.zeros((4, 4, 8), dtype=np.float32)
        snap_cur = np.linspace(-0.5, 0.5, 32, dtype=np.float32).reshape(4, 8)
        hist_seq = np.zeros((4, 8, 9), dtype=np.float32)
        h = model.encode(x_2hop, snap_cur, hist_seq)
        ego, ext = model.score(h, np.zeros(10, dtype=np.float32),
                               np.zeros((4, 10), dtype=np.float32))
        assert h.shape == (4, 32)
        assert ego.shape == (4,)
        assert ext.shape == (4, 1 if variant == "mean" else 16)
        assert np.all(np.isfinite(h))
        assert np.all(np.isfinite(ego))
        assert np.all(np.isfinite(ext))
        np.testing.assert_allclose(ego, 0.5, rtol=0.0, atol=1e-6)
        np.testing.assert_allclose(ext, math.log(2.0), rtol=0.0, atol=1e-6)


def test_frame_variant_ignores_graph_and_history_inputs():
    model = NumpyPigou.neutral(seed=3, variant="frame")
    rng = np.random.default_rng(4)
    snap = rng.normal(size=(4, 8)).astype(np.float32)
    first = model.encode(np.zeros((4, 4, 8), np.float32), snap,
                         np.zeros((4, 8, 9), np.float32))
    second = model.encode(rng.normal(size=(4, 4, 8)).astype(np.float32), snap,
                          rng.normal(size=(4, 8, 9)).astype(np.float32))
    np.testing.assert_array_equal(first, second)


def test_npz_round_trip_uses_no_pickle(tmp_path):
    rng = np.random.default_rng(29)
    model = NumpyPigou.neutral(seed=8, variant="full")
    x_2hop = rng.normal(size=(4, 4, 8)).astype(np.float32)
    snap_cur = rng.normal(size=(4, 8)).astype(np.float32)
    hist_seq = rng.normal(size=(4, 8, 9)).astype(np.float32)
    global_features = rng.normal(size=(10,)).astype(np.float32)
    port_features = rng.normal(size=(4, 10)).astype(np.float32)
    expected_h = model.encode(x_2hop, snap_cur, hist_seq)
    expected_ego, expected_ext = model.score(
        expected_h, global_features, port_features)

    artifact = model.save(tmp_path / "weights")
    assert artifact.name == "weights.npz"
    with np.load(artifact, allow_pickle=False) as archive:
        assert set(model.parameters).issubset(archive.files)
        assert "ais.1.w" not in archive.files
        assert str(archive["variant"].item()) == "full"
    loaded = NumpyPigou.load(artifact)
    actual_h = loaded.encode(x_2hop, snap_cur, hist_seq)
    actual_ego, actual_ext = loaded.score(
        actual_h, global_features, port_features)
    np.testing.assert_array_equal(actual_h, expected_h)
    np.testing.assert_array_equal(actual_ego, expected_ego)
    np.testing.assert_array_equal(actual_ext, expected_ext)


def test_invalid_y_priv_is_sanitized_behind_valid_mask():
    snapshots, decisions = _fixture(variant="full", snapshots=3, decisions=5)
    snapshot_data, _, _ = _validate_training_inputs(
        snapshots, decisions, "full")
    invalid = ~snapshots["y_valid"]
    assert np.any(invalid)
    assert np.all(snapshot_data["y_priv"][invalid] == 0.0)
    assert np.all(np.isfinite(snapshot_data["y_priv"]))


def test_action_direction_contract_rejects_out_of_range_before_tf_import():
    snapshots, decisions = _fixture(variant="full", snapshots=2, decisions=3)
    decisions["action"][0] = 4
    with pytest.raises(ValueError, match=r"0\.\.3"):
        train(snapshots, decisions, seed=1, steps=1, batch_size=2, lr=0.01)


@pytest.mark.parametrize(
    "variant", ["full", "mean", "frame", "no_ais", "no_ext"])
def test_train_is_finite_and_updates_both_stage_families(variant):
    _require_tensorflow_213()
    snapshots, decisions = _fixture(
        variant=variant, snapshots=5, decisions=16, seed=71)
    model, debug = train(
        snapshots, decisions, seed=23, steps=2, batch_size=8, lr=0.01,
        variant=variant)
    assert model.parameter_count == _EXPECTED_ONLINE_PARAMETER_COUNT[variant]
    assert debug["online_parameter_count"] == model.parameter_count
    assert debug["training_parameter_count"] \
        == _EXPECTED_TRAINING_PARAMETER_COUNT[variant]
    assert debug["stage_i_changed_parameters"] > 0
    assert debug["stage_ii_changed_parameters"] > 0
    assert len(debug["loss_history"]) == 2
    for key in ("loss_total", "loss_ego", "loss_ext", "loss_ais"):
        assert math.isfinite(debug[key])
        assert debug[key] >= 0.0
    if variant == "no_ais":
        assert debug["loss_ais"] == 0.0
    if variant == "no_ext":
        assert debug["loss_ext"] == 0.0
        assert not np.any(model.parameters["ext.w"])
        assert not np.any(model.parameters["ext.b"])

    rng = np.random.default_rng(99)
    h = model.encode(rng.normal(size=(4, 4, 8)).astype(np.float32),
                     rng.normal(size=(4, 8)).astype(np.float32),
                     rng.normal(size=(4, 8, 9)).astype(np.float32))
    ego, ext = model.score(
        h, rng.normal(size=10).astype(np.float32),
        rng.normal(size=(4, 10)).astype(np.float32))
    assert ego.shape == (4,)
    assert ext.shape == (4, 1 if variant == "mean" else 16)
    assert np.all(np.isfinite(ego)) and np.all(np.isfinite(ext))


def test_tensorflow_numpy_parity_after_export(tmp_path):
    tf = _require_tensorflow_213()
    snapshots, decisions = _fixture(
        variant="full", snapshots=4, decisions=9, seed=123)
    trained, _ = train(
        snapshots, decisions, seed=31, steps=1, batch_size=6, lr=0.005)
    exported = NumpyPigou.load(trained.save(tmp_path / "trained_parity.npz"))
    rng = np.random.default_rng(124)
    x_2hop = rng.normal(size=(1, 4, 4, 8)).astype(np.float32)
    snap_cur = rng.normal(size=(1, 4, 8)).astype(np.float32)
    hist_seq = rng.normal(size=(1, 4, 8, 9)).astype(np.float32)
    global_features = rng.normal(size=(1, 10)).astype(np.float32)
    port_features = rng.normal(size=(1, 4, 10)).astype(np.float32)

    numpy_h = exported.encode(x_2hop[0], snap_cur[0], hist_seq[0])
    numpy_ego, numpy_ext = exported.score(
        numpy_h, global_features[0], port_features[0])
    tf_parameters = {
        name: tf.constant(value, dtype=tf.float32)
        for name, value in exported.parameters.items()
    }
    tf_result = _tensorflow_forward(
        tf, tf_parameters, exported.variant, x_2hop, snap_cur, hist_seq,
        global_features, port_features)
    np.testing.assert_allclose(
        tf_result["H"].numpy()[0], numpy_h, rtol=2e-5, atol=2e-6)
    np.testing.assert_allclose(
        tf_result["ego"].numpy()[0], numpy_ego, rtol=2e-5, atol=2e-6)
    np.testing.assert_allclose(
        tf_result["ext"].numpy()[0], numpy_ext, rtol=2e-5, atol=2e-6)


def test_tensorflow_gat_masks_broadcast_over_heads_and_batch():
    tf = _require_tensorflow_213()
    rng = np.random.default_rng(201)
    parameters = _training_initial_parameters(rng, "full")
    tf_parameters = {
        name: tf.constant(value, dtype=tf.float32)
        for name, value in parameters.items()
    }
    x_2hop = rng.normal(size=(65, 4, 4, 8)).astype(np.float32)
    x_2hop[:, 1] = 0.0
    result = _tensorflow_forward(
        tf, tf_parameters, "full",
        tf.convert_to_tensor(x_2hop),
        tf.convert_to_tensor(rng.normal(size=(65, 4, 8)).astype(np.float32)),
        tf.convert_to_tensor(rng.normal(size=(65, 4, 8, 9)).astype(np.float32)),
        tf.zeros((65, 10), tf.float32),
        tf.zeros((65, 4, 10), tf.float32),
        include_aux=True)
    assert result["H"].shape == (65, 4, 32)
    assert bool(tf.reduce_all(tf.math.is_finite(result["H"])).numpy())
    assert bool(tf.reduce_all(tf.math.is_finite(result["ais"])).numpy())


def test_tensorflow_ais_slope_channel_is_signed():
    tf = _require_tensorflow_213()
    rng = np.random.default_rng(200)
    parameters = _training_initial_parameters(rng, "full")
    parameters["ais.2.b"][1] = -2.0
    tf_parameters = {
        name: tf.constant(value, dtype=tf.float32)
        for name, value in parameters.items()
    }
    result = _tensorflow_forward(
        tf, tf_parameters, "full",
        tf.zeros((1, 4, 4, 8), tf.float32),
        tf.zeros((1, 4, 8), tf.float32),
        tf.zeros((1, 4, 8, 9), tf.float32),
        tf.zeros((1, 10), tf.float32),
        tf.zeros((1, 4, 10), tf.float32),
        include_aux=True)
    slope = result["ais"].numpy()[..., 1]
    assert np.all(slope < 0.0)
    assert np.all(slope >= -1.0)
    assert np.all(slope <= 1.0)
