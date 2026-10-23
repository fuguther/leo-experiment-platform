"""A1: reading a REAL repository checkpoint into the frozen decision path.

Every test here answers a question the previous state of the tree could not
answer at all: before A1 the reader raised unconditionally after its metadata
gates, so "the model chose the action" was untestable.  The counterexamples
are the refusals: a file whose hash is right but which is not a model, a
metadata file that does not pin the observation contract, a model whose graph
disagrees with its metadata, a model that returns NaN, and a training learner
smuggled in where an inference-only policy is required.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest

from CODE.leo_sim import config as config_mod, inference, learning

CONTRACT = "C3"
WIDTH = learning.CONTRACT_DIMS[CONTRACT]


class FakeKerasModel:
    """The minimum surface the reader is allowed to depend on."""

    def __init__(self, weights, *, in_shape=None, out_shape=None,
                 nan=False, training_seen=None):
        self._w = np.asarray(weights, dtype=np.float64)
        self._in_shape = in_shape
        self._out_shape = out_shape
        self.nan = bool(nan)
        self.training_seen = training_seen if training_seen is not None else []

    @property
    def input_shape(self):
        return self._in_shape or (None, int(self._w.shape[1]))

    @property
    def output_shape(self):
        return self._out_shape or (None, int(self._w.shape[0]))

    def __call__(self, batch, training=None):
        self.training_seen.append(training)
        out = np.asarray(batch, dtype=np.float64) @ self._w.T
        if self.nan:
            return out * np.nan
        return out

    def get_weights(self):
        return [self._w]


def _metadata(contract=CONTRACT, width=WIDTH, **overrides):
    block = inference.observation_contract_fields(contract=contract,
                                                  feature_width=width)
    block.update({"schema": inference.CHECKPOINT_SCHEMA,
                  "algorithm": "ddqn", "contract": contract,
                  "checkpoint": "online.keras",
                  "checkpoint_verified": True,
                  "weights_purpose": "test_weights"})
    block.update(overrides)
    return block


def _write(tmp_path, metadata=None, model=None, *, ckpt_bytes=b"keras-bytes"):
    ckpt = tmp_path / "online.keras"
    ckpt.write_bytes(ckpt_bytes)
    meta = tmp_path / "metadata.json"
    payload = _metadata() if metadata is None else metadata
    meta.write_text(json.dumps(payload, sort_keys=True))
    return {"ckpt": ckpt, "meta": meta,
            "ckpt_sha": hashlib.sha256(ckpt.read_bytes()).hexdigest(),
            "meta_sha": hashlib.sha256(meta.read_bytes()).hexdigest(),
            "model": model if model is not None else FakeKerasModel(
                np.zeros((len(inference.ACTIONS), WIDTH)))}


#: This host has no TensorFlow: the pinned loader lives on the VM.  The
#: loader-availability gate is therefore satisfied explicitly so the
#: HOST-INDEPENDENT reader logic (contract gates, shape gates, finite probe,
#: mask, counters) can be exercised here; the real framework round trip is
#: covered on the VM by test_a_real_keras_artifact_round_trips.
def _allow_loader(monkeypatch, model):
    monkeypatch.setattr(inference, "tf_available", lambda: True)
    monkeypatch.setattr(inference, "_load_keras_model", lambda path: model)


def _allow_failing_loader(monkeypatch, loader):
    monkeypatch.setattr(inference, "tf_available", lambda: True)
    monkeypatch.setattr(inference, "_load_keras_model", loader)


def _load(files, monkeypatch, **kwargs):
    _allow_loader(monkeypatch, files["model"])
    return inference.load_fixed_adapter_from_checkpoint(
        files["ckpt"], files["ckpt_sha"], files["meta"], files["meta_sha"],
        **kwargs)


# ------------------------------------------------- the load boundary moved
def test_hashes_alone_are_not_available(tmp_path):
    ckpt = tmp_path / "online.keras"
    ckpt.write_bytes(b"x")
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps(_metadata()))
    report = inference.verify_checkpoint(
        ckpt, hashlib.sha256(b"x").hexdigest(), metadata_path=meta,
        metadata_sha256=hashlib.sha256(meta.read_bytes()).hexdigest())
    assert report["state"] != "AVAILABLE"
    assert report["state"] in ("LOAD_PENDING", "EXTERNAL_BLOCKER")


# ------------------------------------------------------------ the read works
def test_a_declared_artifact_is_read_and_the_model_chooses(tmp_path,
                                                          monkeypatch):
    weights = np.zeros((len(inference.ACTIONS), WIDTH))
    weights[inference.ACTIONS.index("E"), 0] = 1.0
    files = _write(tmp_path, model=FakeKerasModel(weights))
    adapter = _load(files, monkeypatch, expected_contract=CONTRACT,
                    expected_width=WIDTH)
    assert adapter.act([1.0] + [0.0] * (WIDTH - 1)) == "E"
    receipt = adapter.receipt()
    assert receipt["state"] == "AVAILABLE"
    assert receipt["shape_checked"] is True
    assert receipt["finite_probe_checked"] is True
    assert receipt["checkpoint_sha256"] == files["ckpt_sha"]
    assert receipt["metadata_sha256"] == files["meta_sha"]
    assert receipt["observation_contract_id"] == _metadata()[
        "observation_contract_id"]
    # the framework must never be called in training mode on any path
    assert set(files["model"].training_seen) == {False}


def test_the_action_moves_when_the_weights_move(tmp_path, monkeypatch):
    east = np.zeros((len(inference.ACTIONS), WIDTH))
    east[inference.ACTIONS.index("E"), 0] = 1.0
    west = np.zeros((len(inference.ACTIONS), WIDTH))
    west[inference.ACTIONS.index("W"), 0] = 1.0
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first = _load(_write(tmp_path / "a", model=FakeKerasModel(east)),
                  monkeypatch)
    second = _load(_write(tmp_path / "b", model=FakeKerasModel(west)),
                   monkeypatch)
    features = [1.0] + [0.0] * (WIDTH - 1)
    assert first.act(features) == "E"
    assert second.act(features) == "W"
    assert first.parameter_sha256() != second.parameter_sha256()


def test_parameter_hash_is_stable_across_calls(tmp_path, monkeypatch):
    files = _write(tmp_path)
    adapter = _load(files, monkeypatch)
    before = adapter.parameter_sha256()
    for _ in range(50):
        adapter.act([0.0] * WIDTH)
    assert adapter.parameter_sha256() == before
    assert adapter.counters()["calls"] == 50
    assert adapter.counters()["updates"] == 0
    assert adapter.counters()["grad_steps"] == 0
    assert adapter.counters()["transitions"] == 0
    assert adapter.counters()["replay_size"] == 0
    assert adapter.counters()["training"] is False


# ------------------------------------------------------------- the refusals
@pytest.mark.parametrize("missing", ["feature_width", "input_dim",
                                    "feature_order", "normalisation",
                                    "action_order",
                                    "observation_contract_id"])
def test_a_metadata_file_without_the_full_contract_is_refused(
        tmp_path, monkeypatch, missing):
    payload = _metadata()
    # feature_width and input_dim are two names for the same gate: dropping
    # either must leave the width undeclared only when both are gone.
    del payload[missing]
    if missing in ("feature_width", "input_dim"):
        payload.pop("feature_width", None)
        payload.pop("input_dim", None)
    with pytest.raises(inference.FixedInferenceError):
        _load(_write(tmp_path, metadata=payload), monkeypatch)


def test_a_relabelled_contract_is_refused(tmp_path, monkeypatch):
    """The id is recomputed from the declared fields, so relabelling fails."""
    payload = _metadata()
    payload["contract"] = "C4"          # the id still belongs to C3
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        _load(_write(tmp_path, metadata=payload), monkeypatch)
    assert "observation_contract_id" in str(excinfo.value)


def test_a_self_consistent_model_for_another_contract_is_refused(tmp_path,
                                                                 monkeypatch):
    """A genuinely C4 model must not be loaded into a C3 run."""
    payload = _metadata(contract="C4")
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        _load(_write(tmp_path, metadata=payload), monkeypatch,
              expected_contract="C3")
    assert "!= expected" in str(excinfo.value)


def test_a_wrong_feature_order_is_refused(tmp_path, monkeypatch):
    payload = _metadata(feature_order="some.other.order/v9")
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        _load(_write(tmp_path, metadata=payload), monkeypatch)
    assert "feature_order" in str(excinfo.value) or \
        "observation_contract_id" in str(excinfo.value)


def test_a_graph_that_disagrees_with_its_metadata_is_refused(tmp_path,
                                                             monkeypatch):
    model = FakeKerasModel(np.zeros((len(inference.ACTIONS), WIDTH)),
                           in_shape=(None, WIDTH + 1))
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        _load(_write(tmp_path, model=model), monkeypatch)
    assert "does not match the declared feature_width" in str(excinfo.value)


def test_a_model_with_the_wrong_action_count_is_refused(tmp_path,
                                                        monkeypatch):
    model = FakeKerasModel(np.zeros((3, WIDTH)), out_shape=(None, 3))
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        _load(_write(tmp_path, model=model), monkeypatch)
    assert "output shape" in str(excinfo.value)


def test_a_nan_model_is_refused_at_load_time(tmp_path, monkeypatch):
    model = FakeKerasModel(np.zeros((len(inference.ACTIONS), WIDTH)),
                           nan=True)
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        _load(_write(tmp_path, model=model), monkeypatch)
    assert "non-finite" in str(excinfo.value)


def test_a_corrupt_artifact_whose_hash_is_correct_is_refused(tmp_path,
                                                             monkeypatch):
    files = _write(tmp_path, ckpt_bytes=b"not-a-model")

    def explode(path):
        raise ValueError("Unable to open file (truncated)")

    _allow_failing_loader(monkeypatch, explode)
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        inference.load_fixed_adapter_from_checkpoint(
            files["ckpt"], files["ckpt_sha"], files["meta"],
            files["meta_sha"])
    assert "could not be read" in str(excinfo.value)
    assert "truncated" in str(excinfo.value)


def test_an_unpinned_metadata_hash_never_reaches_the_reader(tmp_path,
                                                         monkeypatch):
    files = _write(tmp_path)
    _allow_loader(monkeypatch, files["model"])
    with pytest.raises(inference.FixedInferenceError) as excinfo:
        inference.load_fixed_adapter_from_checkpoint(
            files["ckpt"], files["ckpt_sha"], files["meta"], None)
    assert "not loadable" in str(excinfo.value)


def test_the_adapter_refuses_every_training_path(tmp_path, monkeypatch):
    adapter = _load(_write(tmp_path), monkeypatch)
    inference.assert_inference_only(adapter)
    for name in ("train_step", "observe", "update", "learn", "set_epsilon",
                 "remember"):
        with pytest.raises(inference.FixedInferenceError):
            getattr(adapter, name)()


def test_a_masked_out_request_is_an_error(tmp_path, monkeypatch):
    adapter = _load(_write(tmp_path), monkeypatch)
    assert adapter.act([0.0] * WIDTH,
                       mask={"N": True, "S": False}) in ("N",)
    with pytest.raises(inference.FixedInferenceError):
        adapter.act([0.0] * WIDTH, mask={a: False for a in inference.ACTIONS})


def test_a_bare_ddqn_learner_is_refused_as_an_inference_policy():
    """A learner whose ONLY training entry point is remember() used to pass."""

    class BareLearner:
        def act(self, snapshot):
            return None

        def remember(self, *args):
            return None

    with pytest.raises(inference.FixedInferenceError):
        inference.assert_inference_only(BareLearner())


# ------------------------------------------- the kernel decision path, end to end
def _kernel_fixture(tmp_path, monkeypatch, *, prefer):
    from CODE.experiment_platform import scripted_scenarios

    weights = np.zeros((len(inference.ACTIONS), WIDTH))
    weights[inference.ACTIONS.index(prefer), 0] = 1.0
    files = _write(tmp_path, model=FakeKerasModel(weights))
    _allow_loader(monkeypatch, files["model"])
    # The framework-PRESENCE gate is a host fact: the VM owns the pinned loader
    # and exercises it for real (test_a_real_keras_artifact_round_trips).  What
    # is under test here is the decision path, so the presence gate is
    # satisfied explicitly rather than silently deleted.
    monkeypatch.setattr(learning, "require_tensorflow", lambda: None)
    resolved, rows, geometry, meta = scripted_scenarios.build("same_flow")
    user = json.loads(json.dumps(resolved["config"]))
    user["routing"]["learning_enabled"] = True
    user["learning"].update({
        "algorithm": "ddqn", "mode": "eval", "fixed_inference": True,
        "checkpoint_path": str(files["ckpt"]),
        "checkpoint_sha256": files["ckpt_sha"],
        "checkpoint_metadata_sha256": files["meta_sha"], "seed": 1})
    return config_mod.resolve_config(user), rows, geometry, files


def _forward_choices(cfg, rows, geometry):
    from CODE.leo_sim import kernel

    sink = []
    result = kernel.run_simulation(cfg, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=[])
    return result, [r for r in sink if r.get("kind") == "forward"]


def test_the_deterministic_scorer_chooses_east_first(tmp_path):
    """The baseline the next two tests are measured against."""
    from CODE.experiment_platform import scripted_scenarios

    resolved, rows, geometry, _meta = scripted_scenarios.build("same_flow")
    _result, forwards = _forward_choices(resolved, rows, geometry)
    assert forwards, "the fixture must forward at all"
    assert forwards[0]["chosen"] == "E"


def test_the_loaded_checkpoint_replaces_the_scorer_action(tmp_path,
                                                         monkeypatch):
    """The action must come from the model, not from the default scorer."""
    cfg, rows, geometry, _files = _kernel_fixture(tmp_path, monkeypatch,
                                                 prefer="W")
    _result, forwards = _forward_choices(cfg, rows, geometry)
    assert forwards, "the fixture must forward under the fixed policy"
    assert forwards[0]["chosen"] == "W"
    for row in forwards:
        assert row["chosen"] in row["candidates"]


def test_the_model_choice_follows_the_weights_not_the_config(tmp_path,
                                                              monkeypatch):
    # The loader patch belongs to ONE fixture at a time, so the east run must
    # finish before the west fixture replaces it: otherwise both runs read the
    # same weights and the test would compare a model with itself.
    for name in ("east", "west"):
        (tmp_path / name).mkdir()
    east = _kernel_fixture(tmp_path / "east", monkeypatch, prefer="E")
    _r1, east_forwards = _forward_choices(*east[:3])
    west = _kernel_fixture(tmp_path / "west", monkeypatch, prefer="W")
    _r2, west_forwards = _forward_choices(*west[:3])
    assert east_forwards[0]["chosen"] == "E"
    assert west_forwards[0]["chosen"] == "W"


def test_the_run_reports_the_real_reader_as_the_caller(tmp_path,
                                                      monkeypatch):
    cfg, rows, geometry, files = _kernel_fixture(tmp_path, monkeypatch,
                                               prefer="W")
    result, forwards = _forward_choices(cfg, rows, geometry)
    ledger = result["learning"]
    assert ledger["kind"] == "checkpoint_fixed_inference_policy"
    assert ledger["reader"] == "CODE.leo_sim.inference.KerasFixedModelAdapter"
    assert ledger["calls"] > 0
    assert ledger["training"] is False and ledger["updates"] == 0
    assert ledger["grad_steps"] == 0 and ledger["replay_size"] == 0
    assert ledger["checkpoint_sha256"] == files["ckpt_sha"]
    assert ledger["metadata_sha256"] == files["meta_sha"]
    assert ledger["weights_purpose"] == "test_weights"
    requested = result["mechanisms"]["requested"]
    assert requested["learning_policy"] == "fixed_inference_checkpoint"
    assert requested["learning_algorithm"] == "ddqn"
    assert requested["learning_mode"] == "eval"
    assert result["mechanisms"]["effective"]["learning"] is True
    assert ledger["calls"] >= len(forwards)


def test_a_fixed_inference_config_without_a_pinned_metadata_is_refused():
    with pytest.raises(config_mod.ConfigError) as excinfo:
        config_mod.resolve_config({
            "routing": {"learning_enabled": True},
            "learning": {"algorithm": "ddqn", "mode": "eval",
                         "fixed_inference": True,
                         "checkpoint_path": "/tmp/m.keras",
                         "checkpoint_sha256": "a" * 64}})
    assert "checkpoint_metadata_sha256" in str(excinfo.value)


def test_fixed_inference_may_not_be_combined_with_a_train_mode():
    with pytest.raises(config_mod.ConfigError):
        config_mod.resolve_config({
            "routing": {"learning_enabled": True},
            "learning": {"algorithm": "ddqn", "mode": "train",
                         "fixed_inference": True,
                         "checkpoint_path": "/tmp/m.keras",
                         "checkpoint_sha256": "a" * 64,
                         "checkpoint_metadata_sha256": "b" * 64}})


# ------------------------------------------------- the real framework round trip
@pytest.mark.skipif(not inference.tf_available(),
                    reason="the pinned loader (tensorflow) lives on the VM; "
                           "this host has none, so the real round trip runs "
                           "there")
def test_a_real_keras_artifact_round_trips(tmp_path):
    import tensorflow as tf

    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(WIDTH,)),
        tf.keras.layers.Dense(8, activation="relu"),
        tf.keras.layers.Dense(len(inference.ACTIONS))])
    path = tmp_path / "online.keras"
    model.save(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = _metadata()
    payload.update({"checkpoint_sha256": sha})
    meta = tmp_path / "metadata.json"
    meta.write_text(json.dumps(payload, sort_keys=True))
    meta_sha = hashlib.sha256(meta.read_bytes()).hexdigest()
    adapter = inference.load_fixed_adapter_from_checkpoint(
        path, sha, meta, meta_sha, expected_contract=CONTRACT,
        expected_width=WIDTH)
    receipt = adapter.receipt()
    assert receipt["state"] == "AVAILABLE"
    before = adapter.parameter_sha256()
    first = adapter.act([0.3] * WIDTH)
    assert first in inference.ACTIONS
    assert adapter.act([0.3] * WIDTH) == first
    assert adapter.parameter_sha256() == before, "a read must not train"
    assert adapter.counters()["training"] is False
