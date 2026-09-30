"""T1-COMPLETE R6: a fixed-inference policy adapter, and its hard boundary.

WHY THIS EXISTS
---------------
A trained DDQN checkpoint cannot be validated on a host without TensorFlow, and
"the file exists" is not evidence that an inference interface works.  This
module provides

  * a FIXED-PARAMETER small model adapter (pure numpy) that implements the real
    inference contract: frozen input, epsilon = 0, no learning update, fixed
    normalisation, action mask enforced, deterministic tie-break;
  * a checkpoint verifier that checks path AND sha256 AND sibling metadata AND
    loader availability, and never reports AVAILABLE from existence alone.

REAL TRAINED-CHECKPOINT PERFORMANCE REMAINS AN EXTERNAL BLOCKER on this host.
Nothing here may be quoted as a DDQN policy result.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np

SCHEMA = "leo-sim-fixed-inference/v1"

#: The action order is the learner contract; a tie must resolve the same way
#: every time, so the adapter never depends on dict or set iteration order.
ACTIONS = ("deliver", "N", "S", "E", "W")


class FixedInferenceError(ValueError):
    """An invalid inference request or a violated inference-only guarantee."""


class FixedInferenceAdapter:
    """Deterministic, inference-only linear policy with a fixed parameter set.

    Guarantees (enforced, not documented):
      * epsilon is fixed at 0.0 and cannot be changed;
      * no update path exists: train_step/observe raise;
      * normalisation is frozen at construction;
      * the action mask is enforced, and an all-masked request is an error;
      * ties break by the declared action order.

    The training entry points below exist in order to REFUSE.  The class
    therefore also declares ``inference_only``, so the frozen path can
    VERIFY the refusal instead of inferring a capability from a name.
    """
    inference_only = True

    def __init__(self, weights, *, actions=ACTIONS, feature_mean=None,
                 feature_std=None, name="fixed-small-model"):
        w = np.asarray(weights, dtype=np.float64)
        if w.ndim != 2:
            raise FixedInferenceError(
                f"weights must be 2-D (actions x features), got {w.shape}")
        if w.shape[0] != len(actions):
            raise FixedInferenceError(
                f"weights rows {w.shape[0]} != actions {len(actions)}")
        if not np.all(np.isfinite(w)):
            raise FixedInferenceError("weights contain non-finite values")
        self.weights = np.ascontiguousarray(w)
        self.actions = tuple(actions)
        self.epsilon = 0.0
        mean = (np.zeros(w.shape[1]) if feature_mean is None
                else np.asarray(feature_mean, dtype=np.float64))
        std = (np.ones(w.shape[1]) if feature_std is None
               else np.asarray(feature_std, dtype=np.float64))
        if mean.shape != (w.shape[1],) or std.shape != (w.shape[1],):
            raise FixedInferenceError(
                "feature normalisation must match the weight width")
        if np.any(std <= 0) or not np.all(np.isfinite(std)):
            raise FixedInferenceError("feature_std must be finite and > 0")
        self.feature_mean = mean
        self.feature_std = std
        self.name = str(name)
        self._calls = 0

    # -- inference --------------------------------------------------------
    def q_values(self, features) -> np.ndarray:
        f = np.asarray(features, dtype=np.float64).reshape(-1)
        if f.shape[0] != self.weights.shape[1]:
            raise FixedInferenceError(
                f"features width {f.shape[0]} != weights width "
                f"{self.weights.shape[1]}")
        if not np.all(np.isfinite(f)):
            raise FixedInferenceError("features contain non-finite values")
        normalised = (f - self.feature_mean) / self.feature_std
        return self.weights @ normalised

    def act(self, features, mask=None) -> str:
        """Masked, deterministic argmax.  Never explores."""
        q = self.q_values(features)
        if mask is None:
            allowed = list(self.actions)
        elif isinstance(mask, dict):
            allowed = [a for a in self.actions if mask.get(a)]
        else:
            allowed = [a for a in self.actions if a in set(mask)]
        if not allowed:
            raise FixedInferenceError("every action is masked out")
        self._calls += 1
        best = max(allowed, key=lambda a: (q[self.actions.index(a)],
                                           -self.actions.index(a)))
        return best

    def counters(self) -> dict:
        return {"schema": SCHEMA, "name": self.name, "calls": self._calls,
                "epsilon": 0.0, "updates": 0, "grad_steps": 0,
                "training": False, "actions": list(self.actions),
                "parameter_sha256": self.parameter_sha256()}

    def parameter_sha256(self) -> str:
        h = hashlib.sha256()
        h.update(self.weights.tobytes())
        h.update(self.feature_mean.tobytes())
        h.update(self.feature_std.tobytes())
        return h.hexdigest()

    # -- forbidden paths --------------------------------------------------
    def observe(self, *args, **kwargs):
        raise FixedInferenceError(
            "the fixed-inference adapter has no observation buffer: it never "
            "learns")

    def train_step(self, *args, **kwargs):
        raise FixedInferenceError(
            "the fixed-inference adapter is inference-only: training is "
            "refused, not silently ignored")

    def set_epsilon(self, *args, **kwargs):
        raise FixedInferenceError(
            "epsilon is fixed at 0.0 for a fixed-inference adapter")


def deterministic_small_model(n_features: int, *, seed: int = 20260927,
                              name="fixed-small-model") -> FixedInferenceAdapter:
    """A reproducible fixed-parameter model for interface validation only.

    It is NOT trained and MUST NOT be used for a policy-performance claim.
    """
    rng = np.random.default_rng(seed)
    weights = rng.normal(0.0, 0.1, size=(len(ACTIONS), int(n_features)))
    return FixedInferenceAdapter(weights, name=name)


# --------------------------------------------------------------------------
# inference-only POLICY interfaces: a frozen snapshot in, one legal action out
# --------------------------------------------------------------------------
#: the ordered feature contract a fixed model must match
ETA_FEATURE_KEYS = ("compute_wait_s", "compute_service_s",
                    "local_egress_wait_s", "tx_s", "prop_s",
                    "peer_process_s")
FEATURE_WIDTH_PER_CANDIDATE = len(ETA_FEATURE_KEYS) + 2


class InferencePolicy:
    """What a policy must expose to be usable on a frozen snapshot.

    A legal set and a feature vector in, one action out.  Anything that also
    exposes train_step/observe/update is a TRAINING learner and is refused by
    assert_inference_only.
    """

    name = "abstract"
    parameter_sha256 = ""
    #: declared contract: a policy usable on a frozen snapshot cannot learn.
    #: assert_inference_only still PROBES that claim; the declaration only
    #: says where to look.
    inference_only = True

    def act(self, snapshot):
        raise NotImplementedError

    def counters(self) -> dict:
        raise NotImplementedError


#: entry points that only a learner has any business exposing
TRAINING_ENTRY_POINTS = ("train_step", "observe", "update", "set_epsilon",
                        "learn", "remember")
#: a replay buffer is the mark of a LEARNER.  TensorflowDDQN exposes remember()
#: and no train_step/observe/update at all, so before A1 it passed the
#: inference-only probe while still being a training learner.


def assert_inference_only(policy) -> None:
    """Refuse anything that can learn: the frozen path is inference-only.

    A NAME IS NOT A CAPABILITY.  The previous check rejected on
    hasattr(train_step), which also refused the fixed adapter -- whose
    training entry points exist precisely in order to refuse.  The
    contract is now:

      * a policy declaring inference_only = True is accepted only after
        every training entry point it does expose is PROBED and shown to
        raise; the declaration alone is never enough;
      * a policy that does not declare it is accepted only when it exposes
        no training entry point at all.
    """
    if getattr(policy, "inference_only", False):
        for name in TRAINING_ENTRY_POINTS:
            entry = getattr(policy, name, None)
            if entry is None:
                continue
            args = (0.5,) if name == "set_epsilon" else ()
            try:
                entry(*args)
            except FixedInferenceError:
                continue          # refusal confirmed
            except Exception as exc:
                raise FixedInferenceError(
                    f"policy {type(policy).__name__} declares inference_only "
                    f"but {name}() could not be shown to refuse "
                    f"({type(exc).__name__}: {exc})") from exc
            raise FixedInferenceError(
                f"policy {type(policy).__name__} declares inference_only but "
                f"{name}() accepted a training call: it IS a training "
                "learner, and the frozen branch path accepts inference-only "
                "policies")
        return
    for name in TRAINING_ENTRY_POINTS:
        if hasattr(policy, name):
            raise FixedInferenceError(
                f"policy {type(policy).__name__} exposes {name!r}: it is a "
                "training learner, and the frozen branch path accepts "
                "inference-only policies")


def build_features(snapshot) -> list:
    """Deterministic feature vector of a frozen snapshot (model input contract).

    Only information the snapshot already carries: per-candidate ETA terms in
    seconds, the last advertised queue and the known rate.  The order is fixed
    by ETA_FEATURE_KEYS so a checkpoint can be validated against it.
    """
    from . import time_alignment as _ta

    features = []
    for direction in sorted(snapshot.legal_directions):
        eta = _ta.estimate_eta(snapshot, direction)
        for key in ETA_FEATURE_KEYS:
            value = eta.terms.get(key)
            features.append(0.0 if value is None else float(value))
        resource = snapshot.resource_for(direction)
        samples = snapshot.history_for(resource) if resource is not None else ()
        features.append(float(samples[-1].queue_bits) if samples else 0.0)
        rate = snapshot.rate_for(direction)
        features.append(float(rate) if rate else 0.0)
    return features


def observation_feature_width(snapshot) -> int:
    return FEATURE_WIDTH_PER_CANDIDATE * len(snapshot.legal_directions)


class FixedModelPolicy(InferencePolicy):
    """Inference-only policy over a fixed parameter set.

    The action mask is the snapshot's legal directions; the model may never
    choose outside it.
    """

    def __init__(self, adapter, *, name=None):
        assert_inference_only(adapter)
        if not hasattr(adapter, "act") or not hasattr(adapter, "q_values"):
            raise FixedInferenceError(
                "a fixed model adapter must expose act() and q_values()")
        self.adapter = adapter
        self.name = name or adapter.name

    @property
    def parameter_sha256(self):
        return self.adapter.parameter_sha256()

    def act(self, snapshot):
        legal = tuple(snapshot.legal_directions)
        if not legal:
            raise FixedInferenceError("the snapshot has no legal direction")
        features = build_features(snapshot)
        width = self.adapter.weights.shape[1]
        if len(features) != width:
            raise FixedInferenceError(
                f"feature width {len(features)} != model width {width}; the "
                "checkpoint was trained for a different observation contract")
        unknown = [a for a in legal if a not in self.adapter.actions]
        if unknown:
            raise FixedInferenceError(
                f"legal directions {unknown} are outside the model action "
                f"set {list(self.adapter.actions)}")
        # ONE masked-argmax implementation: the adapter owns the mask, the
        # tie-break AND the call counter, so counters()["calls"] is real
        # (the S6 review found it permanently 0 because this method
        # bypassed act() and re-implemented the argmax on q_values).
        return self.adapter.act(features, mask=legal)

    def counters(self) -> dict:
        return dict(self.adapter.counters(), policy=self.name,
                    kind="fixed_inference_policy")


class WidthAdaptiveFixedPolicy(InferencePolicy):
    """A fixed-parameter model PER FEATURE WIDTH, deterministic from a seed.

    A real checkpoint has one input width, but a branch run can present
    decisions with different candidate counts.  This variant keeps the
    "fixed parameters" property (weights are a pure function of seed+width) and
    lets the interface be exercised across a whole branch without pretending a
    single checkpoint covers it.
    """

    def __init__(self, *, seed: int = 20260927, name="fixed-small-model"):
        self.seed = int(seed)
        self.name = str(name)
        self._cache = {}
        self._calls = 0

    def _adapter_for(self, width: int):
        adapter = self._cache.get(width)
        if adapter is None:
            adapter = deterministic_small_model(width, seed=self.seed,
                                                name=self.name)
            self._cache[width] = adapter
        return adapter

    def act(self, snapshot):
        legal = tuple(snapshot.legal_directions)
        if not legal:
            raise FixedInferenceError("the snapshot has no legal direction")
        adapter = self._adapter_for(observation_feature_width(snapshot))
        features = build_features(snapshot)
        if len(features) != adapter.weights.shape[1]:
            raise FixedInferenceError(
                f"feature width {len(features)} != model width "
                f"{adapter.weights.shape[1]} for the selected sub-model")
        unknown = [a for a in legal if a not in adapter.actions]
        if unknown:
            raise FixedInferenceError(
                f"legal directions {unknown} are outside the model action set")
        self._calls += 1
        # the same single code path as FixedModelPolicy
        return adapter.act(features, mask=legal)

    def parameter_sha256(self):
        h = hashlib.sha256()
        for width in sorted(self._cache):
            h.update(str(width).encode())
            h.update(self._cache[width].parameter_sha256().encode())
        return h.hexdigest()

    def counters(self) -> dict:
        return {
            "schema": SCHEMA, "policy": self.name, "kind":
            "fixed_inference_policy", "seed": self.seed,
            "calls": self._calls, "epsilon": 0.0, "updates": 0,
            "grad_steps": 0, "training": False,
            # adapter-level evidence that the same act() path ran
            "adapter_calls": sum(a.counters()["calls"]
                                 for a in self._cache.values()),
            "widths": sorted(self._cache),
            "parameter_sha256": self.parameter_sha256(),
        }


class ScorerPolicy(InferencePolicy):
    """The deterministic shared scorer behind the same interface."""

    name = "deterministic_scorer"
    parameter_sha256 = ""

    def act(self, snapshot):
        from . import time_alignment as _ta
        return _ta.plan_decision(snapshot).chosen()

    def counters(self) -> dict:
        return {"schema": SCHEMA, "policy": self.name, "calls": 0,
                "epsilon": None, "updates": 0, "training": False}


# --------------------------------------------------------------------------
# A1: READING THE REPOSITORY OWN TENSORFLOW CHECKPOINT
# --------------------------------------------------------------------------
#: The observation contract is a NAMED, PINNED object.  The kernel builds the
#: model input with leo_sim.learning.build_observation, whose feature ORDER is
#: owned by that function; a checkpoint trained against any other order must be
#: refused, never fed a vector whose meaning silently changed.
OBSERVATION_FEATURE_ORDER = "leo_sim.learning.build_observation/v1"
CONTRACT_SCHEMA = "leo-sim-observation-contract/v1"
CHECKPOINT_SCHEMA = "leo-sim-ddqn/v1"
#: "test_weights" marks an interface-validation export; it must never be
#: quoted as a trained-policy performance result.
WEIGHTS_PURPOSES = ("trained", "test_weights")
NORMALISATION_MODES = ("none", "affine")


def _canonical_json(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def observation_contract_id(*, contract, feature_width, feature_order,
                            normalisation, actions) -> str:
    """Content hash of everything that defines what the numbers mean.

    Two checkpoints with the same width but a different feature order, action
    order or normalisation are DIFFERENT contracts and must not collide.
    """
    return hashlib.sha256(_canonical_json({
        "schema": CONTRACT_SCHEMA,
        "contract": str(contract),
        "feature_width": int(feature_width),
        "feature_order": str(feature_order),
        "normalisation": normalisation,
        "actions": list(actions),
    }).encode("utf-8")).hexdigest()


def observation_contract_fields(*, contract, feature_width,
                                feature_order=OBSERVATION_FEATURE_ORDER,
                                normalisation=None, actions=ACTIONS) -> dict:
    """The block a checkpoint must declare for its input to be readable.

    Written by learning.TensorflowDDQN.save_and_verify and required back by
    the reader, so the producer and the consumer cannot drift apart.
    """
    norm = dict(normalisation or {"mode": "none"})
    return {
        "contract": str(contract),
        "feature_width": int(feature_width),
        "input_dim": int(feature_width),
        "feature_order": str(feature_order),
        "normalisation": norm,
        "action_order": list(actions),
        "observation_contract_id": observation_contract_id(
            contract=contract, feature_width=feature_width,
            feature_order=feature_order, normalisation=norm,
            actions=actions),
    }


def validate_observation_contract(metadata, *, expected_contract=None,
                                  expected_width=None, actions=ACTIONS) -> dict:
    """Fail-closed validation of a declared observation contract.

    Order matters: the width is checked before anything else so a width-only
    mismatch keeps its precise message, and the contract id is recomputed
    from the declared fields so a relabelled metadata file cannot pass.
    """
    if not isinstance(metadata, dict):
        raise FixedInferenceError("checkpoint metadata is not a mapping")
    declared = metadata.get("feature_width", metadata.get("input_dim"))
    if declared is None:
        raise FixedInferenceError(
            "checkpoint metadata does not declare feature_width, so the "
            "observation contract cannot be validated")
    width = int(declared)
    if metadata.get("input_dim") is not None \
            and int(metadata.get("input_dim")) != width:
        raise FixedInferenceError(
            "checkpoint metadata disagrees with itself: input_dim "
            + repr(metadata.get("input_dim")) + " != feature_width "
            + repr(width))
    if expected_width is not None and width != int(expected_width):
        raise FixedInferenceError(
            f"checkpoint feature_width {declared} != expected {expected_width}")
    contract = metadata.get("contract")
    if contract is None:
        raise FixedInferenceError(
            "checkpoint metadata does not declare the observation contract "
            "(contract), so the feature meaning cannot be validated")
    if expected_contract is not None and str(contract) != str(expected_contract):
        raise FixedInferenceError(
            f"checkpoint contract {contract!r} != expected "
            f"{expected_contract!r}")
    order = metadata.get("feature_order")
    if order is None:
        raise FixedInferenceError(
            "checkpoint metadata does not declare feature_order: the feature "
            "ORDER is part of the contract and cannot be assumed")
    if str(order) != OBSERVATION_FEATURE_ORDER:
        raise FixedInferenceError(
            f"checkpoint feature_order {order!r} is not the kernel "
            f"observation order {OBSERVATION_FEATURE_ORDER!r}; refusing to "
            "feed a differently ordered vector")
    norm = metadata.get("normalisation")
    if not isinstance(norm, dict) or norm.get("mode") not in NORMALISATION_MODES:
        raise FixedInferenceError(
            "checkpoint metadata must declare normalisation as a mapping with "
            f"mode in {list(NORMALISATION_MODES)}, got {norm!r}")
    if norm["mode"] == "affine":
        for key in ("mean", "std"):
            values = norm.get(key)
            if not isinstance(values, (list, tuple)) or len(values) != width:
                raise FixedInferenceError(
                    f"affine normalisation must carry {width} {key} values")
    declared_actions = metadata.get("action_order")
    if declared_actions is None:
        raise FixedInferenceError(
            "checkpoint metadata does not declare action_order, so the output "
            "index -> direction mapping cannot be validated")
    if list(declared_actions) != list(actions):
        raise FixedInferenceError(
            f"checkpoint action_order {list(declared_actions)} != the kernel "
            f"action order {list(actions)}")
    declared_id = metadata.get("observation_contract_id")
    if declared_id is None:
        raise FixedInferenceError(
            "checkpoint metadata does not declare observation_contract_id")
    recomputed = observation_contract_id(
        contract=contract, feature_width=width, feature_order=order,
        normalisation=dict(norm), actions=list(declared_actions))
    if str(declared_id) != recomputed:
        raise FixedInferenceError(
            "checkpoint observation_contract_id does not match its own "
            "declared contract: the metadata was edited or produced by a "
            "different contract version")
    purpose = metadata.get("weights_purpose", "trained")
    if purpose not in WEIGHTS_PURPOSES:
        raise FixedInferenceError(
            f"checkpoint weights_purpose {purpose!r} is not one of "
            f"{list(WEIGHTS_PURPOSES)}")
    return {"contract": str(contract), "feature_width": width,
            "feature_order": str(order), "normalisation": dict(norm),
            "actions": list(declared_actions),
            "observation_contract_id": recomputed,
            "weights_purpose": purpose}


def _load_keras_model(path):
    """Read a real Keras artifact with the repository custom layers.

    Kept as a one-line seam so the reader can be exercised without TensorFlow
    installed; it is the ONLY place that touches the framework.
    """
    import tensorflow as tf

    from .learning import _graph_custom_objects

    return tf.keras.models.load_model(Path(path), compile=False,
                                      custom_objects=_graph_custom_objects())


class KerasFixedModelAdapter:
    """A READ real checkpoint behind the inference-only contract.

    What is enforced rather than documented:
      * the model is called with training=False on every path;
      * the declared input width, output width and action order are checked
        against the live graph, not trusted from metadata;
      * a deterministic forward probe must return finite values of the right
        shape before the adapter exists at all;
      * the action mask is enforced and an all-masked request is an error;
      * no replay buffer, no update path: remember/observe/train_step/update/
        learn/set_epsilon all refuse;
      * ties break by the declared action order, exactly as the pure-numpy
        adapter does, so the two are interchangeable at the call site.
    """
    inference_only = True

    def __init__(self, model, *, contract, feature_width, feature_order,
                 normalisation, actions=ACTIONS, name="checkpoint-model",
                 weights_purpose="trained", checkpoint_sha256=None,
                 metadata_sha256=None, checkpoint_path=None):
        self.model = model
        self.contract = str(contract)
        self.actions = tuple(actions)
        self.feature_width = int(feature_width)
        self.feature_order = str(feature_order)
        self.normalisation = dict(normalisation or {"mode": "none"})
        self.name = str(name)
        self.weights_purpose = str(weights_purpose)
        self.checkpoint_sha256 = checkpoint_sha256
        self.metadata_sha256 = metadata_sha256
        self.checkpoint_path = (None if checkpoint_path is None
                                else str(checkpoint_path))
        self.epsilon = 0.0
        self._calls = 0
        self._probe_max_abs = None
        self._check_graph()
        self.parameter_sha256_value = self._weights_sha256()
        self._probe_max_abs = self._finite_probe()
        self.observation_contract_id = observation_contract_id(
            contract=self.contract, feature_width=self.feature_width,
            feature_order=self.feature_order,
            normalisation=self.normalisation, actions=self.actions)

    # -- construction-time gates -----------------------------------------
    def _shapes(self):
        try:
            in_shape = tuple(self.model.input_shape)
        except TypeError:
            raise FixedInferenceError(
                "the checkpoint declares several inputs; the repository DDQN "
                "contract has exactly one flat observation input")
        try:
            out_shape = tuple(self.model.output_shape)
        except TypeError:
            raise FixedInferenceError(
                "the checkpoint declares several outputs; the repository DDQN "
                "contract has exactly one Q-value vector")
        return in_shape, out_shape

    def _check_graph(self):
        in_shape, out_shape = self._shapes()
        if len(in_shape) != 2 or in_shape[1] != self.feature_width:
            raise FixedInferenceError(
                f"checkpoint input shape {in_shape} does not match the "
                f"declared feature_width {self.feature_width}: the file was "
                "not trained for this observation contract")
        if len(out_shape) != 2 or out_shape[1] != len(self.actions):
            raise FixedInferenceError(
                f"checkpoint output shape {out_shape} does not match the "
                f"declared action set of {len(self.actions)} actions")

    def _forward(self, batch):
        values = self.model(batch, training=False)
        return np.asarray(values, dtype=np.float64)

    def _finite_probe(self):
        """A read is not a load: prove the graph can actually answer.

        Deterministic, no random probe: a NaN that only appears for some
        inputs must not be discovered in the middle of a run.
        """
        probes = np.zeros((3, self.feature_width), dtype=np.float32)
        if self.feature_width:
            probes[1] = np.linspace(-0.5, 0.5, self.feature_width,
                                    dtype=np.float32)
            probes[2] = np.linspace(0.25, 1.25, self.feature_width,
                                    dtype=np.float32)
        try:
            out = self._forward(probes)
        except FixedInferenceError:
            raise
        except Exception as exc:
            raise FixedInferenceError(
                "the checkpoint could not be evaluated: "
                f"{type(exc).__name__}: {exc}") from exc
        if out.shape != (3, len(self.actions)):
            raise FixedInferenceError(
                f"the checkpoint returned shape {out.shape}, expected "
                f"{(3, len(self.actions))}")
        if not np.all(np.isfinite(out)):
            raise FixedInferenceError(
                "the checkpoint returned non-finite Q values on a finite "
                "probe input; the file is not usable for inference")
        return float(np.max(np.abs(out)))

    def _weights_sha256(self):
        """Stable hash of the frozen parameter set (not the file bytes).

        Two exports of the same weights must hash the same even if the Keras
        container differs; a training update must move it.
        """
        handle = hashlib.sha256()
        try:
            weights = self.model.get_weights()
        except Exception as exc:
            raise FixedInferenceError(
                "the checkpoint exposes no readable weights: "
                f"{type(exc).__name__}: {exc}") from exc
        if not weights:
            raise FixedInferenceError(
                "the checkpoint exposes an empty parameter set")
        for array in weights:
            value = np.asarray(array)
            handle.update(str(value.dtype).encode())
            handle.update(str(value.shape).encode())
            handle.update(np.ascontiguousarray(value).tobytes())
        return handle.hexdigest()

    # -- inference --------------------------------------------------------
    def _prepare(self, features):
        f = np.asarray(features, dtype=np.float64).reshape(-1)
        if f.shape[0] != self.feature_width:
            raise FixedInferenceError(
                f"features width {f.shape[0]} != checkpoint width "
                f"{self.feature_width}")
        if not np.all(np.isfinite(f)):
            raise FixedInferenceError("features contain non-finite values")
        if self.normalisation.get("mode") == "affine":
            mean = np.asarray(self.normalisation["mean"], dtype=np.float64)
            std = np.asarray(self.normalisation["std"], dtype=np.float64)
            if np.any(std <= 0):
                raise FixedInferenceError(
                    "affine normalisation has a non-positive std")
            f = (f - mean) / std
        return f.astype(np.float32)

    def q_values(self, features) -> np.ndarray:
        batch = self._prepare(features)[None, :]
        try:
            out = self._forward(batch)
        except FixedInferenceError:
            raise
        except Exception as exc:
            raise FixedInferenceError(
                "checkpoint inference failed: "
                f"{type(exc).__name__}: {exc}") from exc
        values = out.reshape(-1)
        if values.shape[0] != len(self.actions):
            raise FixedInferenceError(
                f"checkpoint returned {values.shape[0]} Q values for "
                f"{len(self.actions)} actions")
        if not np.all(np.isfinite(values)):
            raise FixedInferenceError(
                "the checkpoint returned non-finite Q values for this "
                "observation")
        return values

    def _allowed(self, mask):
        if mask is None:
            return list(self.actions)
        if isinstance(mask, dict):
            return [a for a in self.actions if mask.get(a)]
        return [a for a in self.actions if a in set(mask)]

    def act(self, features, mask=None) -> str:
        """Masked, deterministic argmax.  Never explores, never trains.
        """
        q = self.q_values(features)
        allowed = self._allowed(mask)
        if not allowed:
            raise FixedInferenceError("every action is masked out")
        self._calls += 1
        best = max(allowed, key=lambda a: (q[self.actions.index(a)],
                                           -self.actions.index(a)))
        return best

    def choose(self, observation, mask, now=None) -> str:
        """Learner-shaped entry: (observation, mask, now) -> action.

        It exists so a fixed checkpoint can occupy the SAME call site as a
        training learner without being one; the frozen path never calls
        remember() on it because there is no replay buffer to call it on.
        """
        return self.act(observation, mask=mask)

    def counters(self) -> dict:
        return {"schema": SCHEMA, "name": self.name,
                "kind": "checkpoint_fixed_inference_policy",
                "calls": self._calls, "epsilon": 0.0, "updates": 0,
                "grad_steps": 0, "training": False,
                "replay_size": 0, "transitions": 0,
                "actions": list(self.actions),
                "contract": self.contract,
                "feature_width": self.feature_width,
                "feature_order": self.feature_order,
                "normalisation_mode": self.normalisation.get("mode"),
                "observation_contract_id": self.observation_contract_id,
                "weights_purpose": self.weights_purpose,
                "checkpoint_sha256": self.checkpoint_sha256,
                "metadata_sha256": self.metadata_sha256,
                "parameter_sha256": self.parameter_sha256(),
                "probe_max_abs_q": self._probe_max_abs}

    def parameter_sha256(self) -> str:
        return self.parameter_sha256_value

    def receipt(self) -> dict:
        """Machine-readable provenance of the read that produced this.
        """
        return {"schema": SCHEMA, "state": "AVAILABLE",
                "reader": "CODE.leo_sim.inference.KerasFixedModelAdapter",
                "checkpoint_path": self.checkpoint_path,
                "checkpoint_sha256": self.checkpoint_sha256,
                "metadata_sha256": self.metadata_sha256,
                "contract": self.contract,
                "feature_width": self.feature_width,
                "feature_order": self.feature_order,
                "normalisation": dict(self.normalisation),
                "action_order": list(self.actions),
                "observation_contract_id": self.observation_contract_id,
                "weights_purpose": self.weights_purpose,
                "parameter_sha256": self.parameter_sha256(),
                "shape_checked": True, "finite_probe_checked": True,
                "training": False, "epsilon": 0.0,
                "note": "a real checkpoint read through the inference-only "
                        "contract; a test_weights export validates the "
                        "interface only and is NOT a policy result"}

    # -- forbidden paths --------------------------------------------------
    def observe(self, *args, **kwargs):
        raise FixedInferenceError(
            "the checkpoint adapter has no observation buffer: it never learns")

    def train_step(self, *args, **kwargs):
        raise FixedInferenceError(
            "the checkpoint adapter is inference-only: training is refused")

    def update(self, *args, **kwargs):
        raise FixedInferenceError(
            "the checkpoint adapter is inference-only: update is refused")

    def learn(self, *args, **kwargs):
        raise FixedInferenceError(
            "the checkpoint adapter is inference-only: learn is refused")

    def remember(self, *args, **kwargs):
        raise FixedInferenceError(
            "the checkpoint adapter builds no replay buffer: it never learns")

    def set_epsilon(self, *args, **kwargs):
        raise FixedInferenceError(
            "epsilon is fixed at 0.0 for a checkpoint inference adapter")


def load_fixed_adapter_from_checkpoint(checkpoint_path, checkpoint_sha256,
                                       metadata_path, metadata_sha256,
                                       *, expected_width=None,
                                       expected_contract=None,
                                       name="checkpoint-model",
                                       require_weights_purpose=None):
    """HARD-GATED READ of a real repository checkpoint.

    Gates, in order: path exists, file hash matches, sibling metadata path and
    hash are declared and match, the loader is installed, the metadata
    declares a complete observation contract, the contract id recomputes, the
    declared width/contract match what the caller asked for, the live graph
    shapes agree, and a finite forward probe returns the right shape.  Only
    then is the state AVAILABLE.  Every failure is a precise blocker; there
    is no silent fallback to a random model.

    Returns the adapter; its receipt() carries the full provenance.
    """
    report = verify_checkpoint(checkpoint_path, checkpoint_sha256,
                               metadata_path, metadata_sha256)
    if report.get("state") != "LOAD_PENDING":
        raise FixedInferenceError(
            "checkpoint not loadable: " + str(report.get("reason")))
    metadata = report.get("metadata") or {}
    declared = validate_observation_contract(
        metadata, expected_contract=expected_contract,
        expected_width=expected_width)
    if require_weights_purpose is not None \
            and declared["weights_purpose"] != str(require_weights_purpose):
        raise FixedInferenceError(
            "checkpoint weights_purpose "
            + repr(declared["weights_purpose"]) + " != required "
            + repr(require_weights_purpose))
    try:
        model = _load_keras_model(checkpoint_path)
    except FixedInferenceError:
        raise
    except Exception as exc:
        raise FixedInferenceError(
            "checkpoint could not be read by the framework: "
            f"{type(exc).__name__}: {exc}") from exc
    return KerasFixedModelAdapter(
        model, contract=declared["contract"],
        feature_width=declared["feature_width"],
        feature_order=declared["feature_order"],
        normalisation=declared["normalisation"],
        actions=declared["actions"], name=name,
        weights_purpose=declared["weights_purpose"],
        checkpoint_sha256=report.get("actual_sha256"),
        metadata_sha256=report.get("metadata_actual_sha256"),
        checkpoint_path=checkpoint_path)


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def tf_available() -> bool:
    try:
        import tensorflow  # noqa: F401
        return True
    except ImportError:
        return False


def verify_checkpoint(checkpoint_path, checkpoint_sha256=None,
                      metadata_path=None, metadata_sha256=None) -> dict:
    """Hash-verified availability of a REAL trained checkpoint.

    AVAILABLE requires ALL of: a path, a matching file hash, a DECLARED
    sibling metadata path, a DECLARED and matching metadata hash, and an
    importable loader.  Existence alone is never enough, and neither is a
    metadata file whose hash was never pinned: the S6 review showed that
    omitting metadata_sha256 (or metadata_path entirely) let a fabricated
    120-byte file reach AVAILABLE.
    """
    report = {"schema": SCHEMA, "checkpoint_path": (None if checkpoint_path
                                                    is None
                                                    else str(checkpoint_path)),
              "metadata_path": (None if metadata_path is None
                                else str(metadata_path)),
              "loader": "tensorflow", "loader_available": tf_available(),
              "reader": "CODE.leo_sim.inference.load_fixed_adapter_from_checkpoint"}
    if not checkpoint_path:
        report.update(state="EXTERNAL_BLOCKER",
                      reason="no checkpoint_path is configured",
                      recovery="provide a trained checkpoint plus its sha256, "
                               "its sibling metadata sha256 and a host with "
                               "the loader installed")
        return report
    path = Path(checkpoint_path)
    if not path.exists():
        report.update(state="EXTERNAL_BLOCKER",
                      reason=f"checkpoint_path does not exist: {path}",
                      recovery="ship the checkpoint or point at the real file")
        return report
    actual = _sha256_file(path)
    if not checkpoint_sha256:
        report.update(state="EXTERNAL_BLOCKER",
                      reason="checkpoint exists but no sha256 is declared, so "
                             "its identity cannot be verified",
                      actual_sha256=actual,
                      recovery="declare learning.checkpoint_sha256")
        return report
    if actual != checkpoint_sha256:
        report.update(state="HASH_MISMATCH", actual_sha256=actual,
                      declared_sha256=checkpoint_sha256,
                      reason="checkpoint content does not match the declared "
                             "sha256",
                      recovery="re-export the checkpoint or correct the hash")
        return report
    if not metadata_path:
        report.update(state="METADATA_MISSING",
                      reason="the checkpoint declares no sibling metadata, "
                             "so its observation contract cannot be "
                             "verified",
                      recovery="declare learning.metadata_path and "
                               "learning.metadata_sha256")
        return report
    if not metadata_sha256:
        report.update(state="METADATA_HASH_MISSING",
                      reason="the sibling metadata is declared but no "
                             "sha256 is given, so its identity cannot be "
                             "verified",
                      recovery="declare learning.metadata_sha256")
        return report
    meta = Path(metadata_path)
    if not meta.exists():
        report.update(state="METADATA_MISSING",
                      reason=f"sibling metadata missing: {meta}",
                      recovery="ship the metadata that pins the "
                               "observation contract")
        return report
    meta_actual = _sha256_file(meta)
    if meta_actual != metadata_sha256:
        report.update(state="METADATA_HASH_MISMATCH",
                      metadata_sha256=meta_actual,
                      declared_metadata_sha256=metadata_sha256,
                      reason="sibling metadata does not match its declared "
                             "hash")
        return report
    try:
        report["metadata"] = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        report.update(state="METADATA_UNREADABLE",
                      reason="sibling metadata is not valid JSON")
        return report
    report["actual_sha256"] = actual
    report["metadata_actual_sha256"] = meta_actual
    if not report["loader_available"]:
        report.update(state="EXTERNAL_BLOCKER",
                      reason="the checkpoint hashes verify but the loader "
                             "(tensorflow) is not installed on this host",
                      recovery="install the pinned loader on the execution "
                               "host")
        return report
    report.update(
        state="LOAD_PENDING",
        reason="the artifact hashes verify and the loader is installed, but "
               "the model has NOT been read yet, so nothing about its "
               "format, shape or numerical output is known",
        recovery="call inference.load_fixed_adapter_from_checkpoint, which "
                 "reports AVAILABLE only after a shape-checked, "
                 "finite-value read",
        note="hash-verified only; A1 requires AVAILABLE to mean loaded")
    return report
