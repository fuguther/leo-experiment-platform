"""PCVR-0: Policy-Conditioned Cohort-Value Router.

The model is trained from same-world, same-cohort, single-action forked
simulation outcomes. At inference it sees only a frozen ObservationSnapshot.
It predicts a lexicographic outcome vector and switches away from the existing
time-alignment ranking only when the predicted improvement clears an empirical
leave-one-world-out error margin. Otherwise it keeps the baseline action.

This is a research prototype, not a performance claim or a formal guarantee.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Mapping

from CODE.leo_sim import time_alignment as ta

MODEL_SCHEMA = "pcvr0-model/v1"
ALGORITHM_NAME = "PCVR-0"
OBJECTIVES = ("delivery_loss", "p99_e2e_s", "mean_e2e_s", "control_packets")
# Practical tie tolerances. They are deliberately separate from the empirical
# prediction-error margin and are not statistical confidence levels.
TIE_TOLERANCE = {
    "delivery_loss": 1e-4,
    "p99_e2e_s": 1e-4,
    "mean_e2e_s": 1e-4,
    "control_packets": 0.5,
}
FEATURE_NAMES = (
    "score_total_s",
    "compute_wait_s",
    "compute_service_s",
    "query_wait_s",
    "query_service_s",
    "local_egress_wait_s",
    "tx_s",
    "prop_s",
    "peer_process_s",
    "resource_work_s",
    "resource_packet_tx_s",
    "remaining_prop_s",
    "resource_age_s",
    "resource_queue_bits",
    "resource_rate_bps",
    "resource_history_count",
    "local_queue_bits",
    "local_rate_bps",
    "local_in_service_s",
    "terminal_tx_s",
    "terminal_prop_s",
    "resource_is_isl",
    "resource_is_downlink",
    "resource_available_known",
    "resource_available",
    "packet_bits",
    "direction_N",
    "direction_E",
    "direction_S",
    "direction_W",
    "candidate_rank",
    "candidate_count",
    "common_horizon_s",
    "score_fallback",
    "score_missing_count",
    "resource_missing",
    "resource_work_missing",
)


class PCVRError(ValueError):
    """Invalid PCVR model or inference input."""


def _finite(value, default=0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _feature_row(snapshot, direction: str, score, rank: int) -> dict[str, float]:
    resource = snapshot.resource_for(direction)
    history = ()
    if resource is not None:
        history = ta.order_history(
            snapshot.history_for(resource), snapshot.snapshot_at)
    latest = history[-1] if history else None
    terms = score.terms or {}
    eta = ta.estimate_eta(snapshot, direction)
    resource_rate = snapshot.resource_rate_for(direction)
    if resource_rate is None and latest is not None:
        resource_rate = latest.rate_bps
    local_queue = snapshot.egress_bits(direction)
    local_rate = snapshot.rate_for(direction)
    local_in_service = snapshot.local_egress_in_service_for(direction)
    availability = snapshot.resource_available_for(direction)
    resource_kind = None if resource is None else resource.kind
    total = score.total_s if math.isfinite(float(score.total_s)) else 0.0

    values = {
        "score_total_s": total,
        "compute_wait_s": terms.get("compute_wait_s", eta.terms.get("compute_wait_s")),
        "compute_service_s": terms.get("compute_service_s", eta.terms.get("compute_service_s")),
        "query_wait_s": terms.get("query_wait_s", eta.terms.get("query_wait_s")),
        "query_service_s": terms.get("query_service_s", eta.terms.get("query_service_s")),
        "local_egress_wait_s": terms.get("local_egress_wait_s", eta.terms.get("local_egress_wait_s")),
        "tx_s": terms.get("tx_s", eta.terms.get("tx_s")),
        "prop_s": terms.get("prop_s", eta.terms.get("prop_s")),
        "peer_process_s": terms.get("peer_process_s", eta.terms.get("peer_process_s")),
        "resource_work_s": terms.get("resource_work_s"),
        "resource_packet_tx_s": terms.get("resource_packet_tx_s"),
        "remaining_prop_s": terms.get("remaining_prop_s", snapshot.remaining_prop_for(direction)),
        "resource_age_s": (
            max(0.0, float(snapshot.snapshot_at) - float(latest.measured_at))
            if latest is not None else 0.0),
        "resource_queue_bits": latest.queue_bits if latest is not None else 0.0,
        "resource_rate_bps": resource_rate,
        "resource_history_count": len(history),
        "local_queue_bits": local_queue,
        "local_rate_bps": local_rate,
        "local_in_service_s": local_in_service,
        "terminal_tx_s": terms.get("terminal_tx_s"),
        "terminal_prop_s": terms.get("terminal_prop_s", snapshot.terminal_prop_for(direction)),
        "resource_is_isl": int(resource_kind == ta.KIND_ISL),
        "resource_is_downlink": int(resource_kind == ta.KIND_DOWNLINK),
        "resource_available_known": int(availability is not None),
        "resource_available": int(availability is True),
        "packet_bits": snapshot.pkt_bits,
        "direction_N": int(direction == "N"),
        "direction_E": int(direction == "E"),
        "direction_S": int(direction == "S"),
        "direction_W": int(direction == "W"),
        "candidate_rank": rank,
        "candidate_count": len(snapshot.legal_directions),
        "common_horizon_s": snapshot.common_horizon_s,
        "score_fallback": int(bool(score.fallback)),
        "score_missing_count": len(score.missing or ()),
        "resource_missing": int(resource is None or latest is None),
        "resource_work_missing": int("resource_work_s" not in terms),
    }
    return {name: _finite(values.get(name)) for name in FEATURE_NAMES}


def extract_snapshot_features(snapshot):
    """Return (features_by_direction, baseline_action) from legal local input.

    This is shared by the training-data collector and the online policy so
    feature semantics cannot drift between collection and deployment.
    """
    ta.reject_future_input(snapshot)
    legal = tuple(str(d) for d in snapshot.legal_directions)
    if not legal:
        raise PCVRError("PCVR-0 requires at least one legal direction")
    plan = ta.plan_decision(snapshot)
    scores = plan.scored.by_direction()
    ranking = list(plan.scored.ranking)
    rank = {direction: i for i, direction in enumerate(ranking)}
    features = {
        direction: _feature_row(
            snapshot, direction, scores[direction], rank.get(direction, len(rank)))
        for direction in legal
    }
    baseline = ranking[0] if ranking and ranking[0] in legal else sorted(legal)[0]
    return features, baseline


def _validate_model(model: Mapping) -> None:
    if not isinstance(model, Mapping) or model.get("schema") != MODEL_SCHEMA:
        raise PCVRError(f"model schema must be {MODEL_SCHEMA!r}")
    if model.get("algorithm") != ALGORITHM_NAME:
        raise PCVRError(f"model algorithm must be {ALGORITHM_NAME!r}")
    if tuple(model.get("feature_names") or ()) != FEATURE_NAMES:
        raise PCVRError("model feature_names do not match this policy implementation")
    mean = model.get("feature_mean")
    scale = model.get("feature_scale")
    if not isinstance(mean, list) or not isinstance(scale, list):
        raise PCVRError("model feature_mean/feature_scale must be arrays")
    if len(mean) != len(FEATURE_NAMES) or len(scale) != len(FEATURE_NAMES):
        raise PCVRError("model feature normalisation width mismatch")
    if any(not math.isfinite(_finite(x, float("nan"))) for x in mean):
        raise PCVRError("model feature_mean contains non-finite values")
    if any(not math.isfinite(_finite(x, float("nan"))) or float(x) <= 0 for x in scale):
        raise PCVRError("model feature_scale must be finite and positive")
    coefficients = model.get("coefficients")
    errors = model.get("validation_abs_error_p90")
    if not isinstance(coefficients, Mapping) or not isinstance(errors, Mapping):
        raise PCVRError("model coefficients and validation errors are required")
    for name in OBJECTIVES:
        beta = coefficients.get(name)
        if not isinstance(beta, list) or len(beta) != len(FEATURE_NAMES) + 1:
            raise PCVRError(f"model coefficient width mismatch for {name}")
        if any(not math.isfinite(_finite(x, float("nan"))) for x in beta):
            raise PCVRError(f"model coefficients contain non-finite values for {name}")
        error = _finite(errors.get(name), float("nan"))
        if not math.isfinite(error) or error < 0:
            raise PCVRError(f"invalid validation error for {name}")


class PCVRPolicy:
    """Inference-only PCVR-0 policy loaded from a trained model.json."""

    inference_only = True
    name = "pcvr0"

    def __init__(self, model_path: str | Path | None = None, model: Mapping | None = None):
        if model is None:
            path = Path(model_path) if model_path is not None else Path(__file__).with_name("model.json")
            if not path.is_file():
                raise PCVRError(
                    f"trained model not found at {path}; run pcvr0.train first")
            try:
                model = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PCVRError(f"cannot read PCVR-0 model: {exc}") from exc
            self.model_path = str(path.resolve())
            self.model_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            self.model_path = None
            self.model_sha256 = "in-memory-test-model"
        _validate_model(model)
        self.model = dict(model)
        self._calls = 0
        self._switches = 0
        self._fallbacks = 0
        self._ambiguous = 0

    def _predict(self, features: Mapping[str, float]) -> dict[str, float]:
        mean = self.model["feature_mean"]
        scale = self.model["feature_scale"]
        x = [1.0] + [
            (_finite(features.get(name)) - float(mu)) / float(sd)
            for name, mu, sd in zip(FEATURE_NAMES, mean, scale)
        ]
        out = {}
        for target in OBJECTIVES:
            beta = self.model["coefficients"][target]
            value = sum(float(b) * float(v) for b, v in zip(beta, x))
            if target == "delivery_loss":
                value = min(1.0, max(0.0, value))
            else:
                value = max(0.0, value)
            out[target] = value
        return out

    def _guarded_choice(self, predictions: Mapping[str, Mapping[str, float]],
                        baseline: str, legal: tuple[str, ...]) -> str:
        ordered = sorted(
            legal,
            key=lambda d: tuple(predictions[d][name] for name in OBJECTIVES)
                         + (d,),
        )
        best = ordered[0]
        if best == baseline:
            return baseline
        errors = self.model["validation_abs_error_p90"]
        for target in OBJECTIVES:
            delta = predictions[best][target] - predictions[baseline][target]
            tolerance = TIE_TOLERANCE[target]
            if abs(delta) <= tolerance:
                continue
            # This is an empirical guard based on leave-one-world-out errors,
            # not a formal confidence interval or a worst-case guarantee.
            margin = 2.0 * float(errors[target])
            if delta < -(margin + tolerance):
                return best
            if delta > margin + tolerance:
                return baseline
            self._ambiguous += 1
            return baseline
        return baseline

    def act(self, snapshot) -> str:
        ta.reject_future_input(snapshot)
        features, baseline = extract_snapshot_features(snapshot)
        legal = tuple(str(d) for d in snapshot.legal_directions)
        predictions = {d: self._predict(features[d]) for d in legal}
        self._calls += 1
        chosen = self._guarded_choice(predictions, baseline, legal)
        if chosen != baseline:
            self._switches += 1
        else:
            self._fallbacks += 1
        return chosen

    def counters(self) -> dict:
        return {
            "algorithm": ALGORITHM_NAME,
            "name": self.name,
            "calls": self._calls,
            "switches": self._switches,
            "baseline_fallbacks": self._fallbacks,
            "ambiguous_fallbacks": self._ambiguous,
            "training": False,
            "updates": 0,
            "model_sha256": self.model_sha256,
            "objectives": list(OBJECTIVES),
            "selection": "guarded_lexicographic_against_time_alignment_baseline",
        }
