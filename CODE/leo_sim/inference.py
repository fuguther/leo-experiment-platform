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
                        "learn")


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


def load_fixed_adapter_from_checkpoint(checkpoint_path, checkpoint_sha256,
                                       metadata_path, metadata_sha256,
                                       *, expected_width=None,
                                       name="checkpoint-model"):
    """HARD-GATED load of a real checkpoint: every gate must pass.

    Gates: path exists, file hash matches, sibling metadata exists and its hash
    matches, metadata declares the observation contract, the declared feature
    width matches, and the loader is importable.  Any failure is a precise
    blocker, never a silent fallback to a random model.
    """
    report = verify_checkpoint(checkpoint_path, checkpoint_sha256,
                               metadata_path, metadata_sha256)
    if report["state"] != "AVAILABLE":
        raise FixedInferenceError(
            "checkpoint not loadable: " + str(report.get("reason")))
    metadata = report.get("metadata") or {}
    declared = metadata.get("feature_width")
    if declared is None:
        raise FixedInferenceError(
            "checkpoint metadata does not declare feature_width, so the "
            "observation contract cannot be validated")
    if expected_width is not None and int(declared) != int(expected_width):
        raise FixedInferenceError(
            f"checkpoint feature_width {declared} != expected {expected_width}")
    raise FixedInferenceError(
        "this host can import the loader but no trained-model reader is "
        "implemented; a real checkpoint run stays an external blocker")


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
              "loader": "tensorflow", "loader_available": tf_available()}
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
    if not report["loader_available"]:
        report.update(state="EXTERNAL_BLOCKER",
                      reason="the checkpoint hashes verify but the loader "
                             "(tensorflow) is not installed on this host",
                      recovery="install the pinned loader on the execution "
                               "host")
        return report
    report.update(state="AVAILABLE",
                  reason=None,
                  note="hash-verified; a real policy run is still required "
                       "before any performance claim")
    return report
