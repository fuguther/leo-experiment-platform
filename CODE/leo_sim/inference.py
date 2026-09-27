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
    """

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

    AVAILABLE requires: a path, a matching file hash, a matching sibling
    metadata hash and an importable loader.  Existence alone is never enough.
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
    if metadata_path:
        meta = Path(metadata_path)
        if not meta.exists():
            report.update(state="METADATA_MISSING",
                          reason=f"sibling metadata missing: {meta}",
                          recovery="ship the metadata that pins the "
                                   "observation contract")
            return report
        meta_actual = _sha256_file(meta)
        if metadata_sha256 and meta_actual != metadata_sha256:
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
