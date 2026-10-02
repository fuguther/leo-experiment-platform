"""Time-aligned resource prediction and unified scoring (T1-COMPLETE P3).

This module is the ONLINE-AUTHORISED half of the four-arm comparison.  It is a
pure-function core: it never imports the kernel, never reads an audit sink and
never looks at a future trace.  Everything it may see arrives inside an
immutable ObservationSnapshot built by the kernel from fields the kernel chose
to expose.

Three types are kept deliberately distinct:

  ObservationSnapshot  what an online policy is allowed to see (frozen)
  ResourcePrediction   a predicted resource state at a target instant
  TruthSample          a measured future value -- OFFLINE ONLY; online entry
                       points reject it (see reject_future_input)

The four online arms differ ONLY in the instant each arm queries:

  stale      the last received measurement, used as-is
  now        projected to snapshot_at (t0)
  common     projected to a horizon shared by every candidate (t0 + h)
  candidate  projected to that candidate own predicted resource-use instant

Everything else (candidate set, resource mapping, scorer, legality, fallback)
is identical across arms.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

# --------------------------------------------------------------------------
# vocabulary
# --------------------------------------------------------------------------
KIND_ISL = "isl"
KIND_DOWNLINK = "downlink"
VALID_KINDS = (KIND_ISL, KIND_DOWNLINK)

# A stable, arm-independent direction order.  It is used ONLY as the final
# tie-break, so two arms that compute the same totals produce the same
# ranking regardless of their names or of dict insertion order.
DIRECTION_ORDER = ("N", "E", "S", "W")

ARMS = ("stale", "now", "common", "candidate")
PREDICTORS = ("hold_last", "bounded_linear")
ETA_METHOD_EXACT = "exact_terms"
ETA_METHOD_DEFAULT_PEER = "default_peer_process"
ETA_METHOD_MISSING = "missing"

# term keys, each a non-overlapping interval in seconds
TERM_KEYS = (
    "compute_wait_s",
    "compute_service_s",
    "query_wait_s",
    "query_service_s",
    "local_egress_wait_s",
    "tx_s",
    "prop_s",
    "peer_process_s",
    "resource_work_s",
    "remaining_prop_s",
    "terminal_tx_s",
    "terminal_prop_s",
)

#: Names of the ETA terms, each a NON-OVERLAPPING interval in seconds.  The
#: target instant is snapshot_at + sum(eta terms); the scorer must therefore
#: reuse these terms instead of adding the local queue a second time.
ETA_TERM_KEYS = (
    "compute_wait_s",
    "compute_service_s",
    "query_wait_s",
    "query_service_s",
    "local_egress_wait_s",
    "tx_s",
    "prop_s",
    "peer_process_s",
)


class TimeAlignmentError(ValueError):
    """Raised for an invalid online request (fail loud, never silent zero)."""


def _finite(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TimeAlignmentError(f"{name} must be a number, got {value!r}")
    v = float(value)
    if not math.isfinite(v):
        raise TimeAlignmentError(f"{name} must be finite, got {value!r}")
    return v


def _direction_index(direction: str) -> int:
    try:
        return DIRECTION_ORDER.index(direction)
    except ValueError:
        return len(DIRECTION_ORDER)


# --------------------------------------------------------------------------
# the three distinct types
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ResourceKey:
    """A named directed egress, not a whole satellite."""

    satellite: int
    direction: str
    kind: str
    # Physical generation is optional for historical/fixture identities.
    # Online ISL estimates must bind it when the remote advertisement carries
    # one; a same-peer rematch may create a different FIFO resource.
    generation: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.satellite, bool) or not isinstance(self.satellite, int):
            raise TimeAlignmentError(f"satellite must be int, got {self.satellite!r}")
        if self.satellite < 0:
            raise TimeAlignmentError(f"satellite must be >= 0, got {self.satellite}")
        if not isinstance(self.direction, str) or not self.direction:
            raise TimeAlignmentError(
                f"direction must be a non-empty str, got {self.direction!r}")
        if self.kind not in VALID_KINDS:
            raise TimeAlignmentError(f"kind must be one of {VALID_KINDS}, got {self.kind!r}")
        if self.generation is not None and (
                isinstance(self.generation, bool)
                or not isinstance(self.generation, int)
                or self.generation < 0):
            raise TimeAlignmentError(
                "generation must be a non-negative integer or None, got "
                f"{self.generation!r}")

    def as_tuple(self) -> tuple:
        base = (self.satellite, self.direction, self.kind)
        return base if self.generation is None else base + (self.generation,)


@dataclass(frozen=True)
class StateSample:
    """One advertisement measured at its source and received locally.

    measured_at is a SOURCE timestamp.  Projecting a prediction never rewrites
    it; a later receive of an older source state cannot overwrite a newer one
    (see order_history).
    """

    resource: ResourceKey
    measured_at: float
    received_at: float
    queue_bits: float
    rate_bps: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "measured_at", _finite("measured_at", self.measured_at))
        object.__setattr__(self, "received_at", _finite("received_at", self.received_at))
        object.__setattr__(self, "queue_bits", _finite("queue_bits", self.queue_bits))
        if self.rate_bps is not None:
            object.__setattr__(self, "rate_bps", _finite("rate_bps", self.rate_bps))
        if self.received_at < self.measured_at:
            raise TimeAlignmentError(
                f"received_at {self.received_at} < measured_at {self.measured_at}")
        if self.queue_bits < 0:
            raise TimeAlignmentError(f"queue_bits must be >= 0, got {self.queue_bits}")


@dataclass(frozen=True)
class ResourcePrediction:
    """A predicted amount of work at a target instant.

    target_at is the instant the value CORRESPONDS TO (for the stale arm that
    is the last source measurement, not the snapshot).  missing_reason is set
    instead of silently returning 0.
    """

    resource: ResourceKey
    snapshot_at: float
    target_at: float
    predicted_bits: float | None
    method: str
    missing_reason: str | None = None
    eta_method: str = ETA_METHOD_EXACT


@dataclass(frozen=True)
class TruthSample:
    """A measured future value.  OFFLINE ONLY.

    It exists so the type boundary is explicit and testable: the online entry
    points below reject it.  Nothing in an online path may construct one from a
    kernel.
    """

    resource: ResourceKey
    at: float
    queue_bits: float
    kind: str = "truth"


@dataclass(frozen=True)
class ObservationSnapshot:
    """What an online policy may see, at one frozen instant.

    Built only by make_snapshot; there is no kernel handle, no cache, no audit
    sink and no trace inside.  All sequences are tuples so a consumer cannot
    mutate history or the legal set.
    """

    satellite: int
    snapshot_at: float
    history: tuple
    legal_directions: tuple
    resources: tuple
    egress_queue_bits: tuple
    link_rate_bps: tuple
    link_propagation_s: tuple
    peer_process_s: tuple
    remaining_prop_s: tuple
    compute_wait_s: float
    compute_service_s: float
    query_wait_s: float
    query_service_s: float
    pkt_bits: float
    max_resource_queue_bits: float | None
    arm: str
    predictor: str
    history_limit: int
    common_horizon_s: float | None
    common_rule: str
    query_delay_s: float
    provenance: tuple = ()
    resource_service_rate_bps: tuple = ()
    terminal_propagation_s: tuple = ()
    resource_available: tuple = ()
    local_egress_in_service_s: tuple = ()

    def _lookup(self, table: tuple, direction: str):
        for key, value in table:
            if key == direction:
                return value
        return None

    def resource_for(self, direction: str):
        for key, value in self.resources:
            if key == direction:
                return value
        return None

    def egress_bits(self, direction: str):
        return self._lookup(self.egress_queue_bits, direction)

    def rate_for(self, direction: str):
        return self._lookup(self.link_rate_bps, direction)

    def propagation_for(self, direction: str):
        return self._lookup(self.link_propagation_s, direction)

    def peer_process_for(self, direction: str):
        return self._lookup(self.peer_process_s, direction)

    def remaining_prop_for(self, direction: str):
        return self._lookup(self.remaining_prop_s, direction)

    def resource_rate_for(self, direction: str):
        return self._lookup(self.resource_service_rate_bps, direction)

    def terminal_prop_for(self, direction: str):
        return self._lookup(self.terminal_propagation_s, direction)

    def resource_available_for(self, direction: str):
        return self._lookup(self.resource_available, direction)

    def local_egress_in_service_for(self, direction: str):
        return self._lookup(self.local_egress_in_service_s, direction)

    def history_for(self, resource) -> tuple:
        return tuple(s for s in self.history if s.resource == resource)

    def describe(self) -> dict:
        return {
            "satellite": self.satellite,
            "snapshot_at": self.snapshot_at,
            "legal_directions": list(self.legal_directions),
            "resources": [[k, v.as_tuple()] for k, v in self.resources],
            "resource_service_rate_bps": dict(self.resource_service_rate_bps),
            "terminal_propagation_s": dict(self.terminal_propagation_s),
            "resource_available": dict(self.resource_available),
            "local_egress_in_service_s": dict(
                self.local_egress_in_service_s),
            "arm": self.arm,
            "predictor": self.predictor,
            "provenance": list(self.provenance),
        }


def reject_future_input(*values) -> None:
    """Fail loud when an online path is handed a future-truth object.

    This is the executable half of the information-permission boundary: it is
    not a comment, a test may (and does) call it with a TruthSample.  Iterables
    are scanned too, so wrapping the truth in a tuple or list does not smuggle
    it past the boundary.
    """
    expanded = []
    for value in values:
        if isinstance(value, (list, tuple, set, frozenset)):
            expanded.extend(value)
        else:
            expanded.append(value)
    for value in expanded:
        if isinstance(value, TruthSample):
            raise TimeAlignmentError(
                "online interface refuses TruthSample (future information)")
        if value is not None and getattr(value, "_t1_branch_outcome", False):
            raise TimeAlignmentError(
                "online interface refuses BranchOutcome (future information)")
        if value is not None and hasattr(value, "truth_sink"):
            raise TimeAlignmentError(
                "online interface refuses an object exposing a truth sink")
        if value is not None and hasattr(value, "kernel"):
            raise TimeAlignmentError(
                "online interface refuses an object exposing the kernel")


# --------------------------------------------------------------------------
# snapshot construction (the only entry point)
# --------------------------------------------------------------------------
def _pairs(name: str, mapping) -> tuple:
    if mapping is None:
        return ()
    items = []
    for key, value in dict(mapping).items():
        if not isinstance(key, str) or not key:
            raise TimeAlignmentError(f"{name} keys must be non-empty strings")
        items.append((key, _finite(f"{name}[{key}]", value)))
    return tuple(items)


def _optional_bool_pairs(name: str, mapping) -> tuple:
    if mapping is None:
        return ()
    items = []
    for key, value in dict(mapping).items():
        if not isinstance(key, str) or not key:
            raise TimeAlignmentError(f"{name} keys must be non-empty strings")
        if value is not None and not isinstance(value, bool):
            raise TimeAlignmentError(f"{name}[{key}] must be bool or None")
        items.append((key, value))
    return tuple(items)


def make_snapshot(*, satellite: int, snapshot_at: float, history,
                  legal_directions, resources,
                  egress_queue_bits=None, link_rate_bps=None,
                  link_propagation_s=None, peer_process_s=None,
                  remaining_prop_s=None, compute_wait_s: float = 0.0,
                  compute_service_s: float = 0.0, pkt_bits: float = 0.0,
                  max_resource_queue_bits=None, arm: str = "candidate",
                  predictor: str = "bounded_linear", history_limit: int = 8,
                  common_horizon_s=None, common_rule: str = "median_eta",
                  query_delay_s: float = 0.0, provenance=(),
                  query_wait_s: float = 0.0,
                  query_service_s: float = 0.0,
                  resource_service_rate_bps=None,
                  terminal_propagation_s=None,
                  resource_available=None,
                  local_egress_in_service_s=None) -> ObservationSnapshot:
    """Build the frozen snapshot from kernel-selected fields only.

    Every accepted keyword is a field the kernel explicitly decided to expose;
    there is no free-form kwargs pass-through and no handle to the live
    simulation.
    """
    reject_future_input(history)
    if arm not in ARMS:
        raise TimeAlignmentError(f"arm must be one of {ARMS}, got {arm!r}")
    if predictor not in PREDICTORS:
        raise TimeAlignmentError(
            f"predictor must be one of {PREDICTORS}, got {predictor!r}")
    if common_rule not in ("median_eta", "mean_eta", "fixed_horizon"):
        raise TimeAlignmentError(f"unknown common_rule {common_rule!r}")
    if common_rule == "fixed_horizon" and common_horizon_s is None:
        raise TimeAlignmentError("fixed_horizon requires a finite common_horizon_s")
    if history_limit < 1:
        raise TimeAlignmentError(f"history_limit must be >= 1, got {history_limit}")
    legal = tuple(str(d) for d in legal_directions)
    res = []
    for direction, key in dict(resources).items():
        if not isinstance(key, ResourceKey):
            raise TimeAlignmentError(f"resources[{direction!r}] must be a ResourceKey")
        res.append((direction, key))
    samples = tuple(history)
    if not all(isinstance(s, StateSample) for s in samples):
        raise TimeAlignmentError("history must contain StateSample objects only")
    horizon = None if common_horizon_s is None else _finite(
        "common_horizon_s", common_horizon_s)
    if horizon is not None and horizon < 0:
        raise TimeAlignmentError("common_horizon_s must be >= 0")
    max_q = None if max_resource_queue_bits is None else _finite(
        "max_resource_queue_bits", max_resource_queue_bits)
    if max_q is not None and max_q < 0:
        raise TimeAlignmentError("max_resource_queue_bits must be >= 0")
    return ObservationSnapshot(
        satellite=int(satellite),
        snapshot_at=_finite("snapshot_at", snapshot_at),
        history=samples,
        legal_directions=legal,
        resources=tuple(res),
        egress_queue_bits=_pairs("egress_queue_bits", egress_queue_bits),
        link_rate_bps=_pairs("link_rate_bps", link_rate_bps),
        link_propagation_s=_pairs("link_propagation_s", link_propagation_s),
        peer_process_s=_pairs("peer_process_s", peer_process_s),
        remaining_prop_s=_pairs("remaining_prop_s", remaining_prop_s),
        compute_wait_s=_finite("compute_wait_s", compute_wait_s),
        compute_service_s=_finite("compute_service_s", compute_service_s),
        query_wait_s=_finite("query_wait_s", query_wait_s),
        query_service_s=_finite("query_service_s", query_service_s),
        pkt_bits=_finite("pkt_bits", pkt_bits),
        max_resource_queue_bits=max_q,
        arm=arm,
        predictor=predictor,
        history_limit=int(history_limit),
        common_horizon_s=horizon,
        common_rule=common_rule,
        query_delay_s=_finite("query_delay_s", query_delay_s),
        provenance=tuple(str(p) for p in provenance),
        resource_service_rate_bps=_pairs(
            "resource_service_rate_bps", resource_service_rate_bps),
        terminal_propagation_s=_pairs(
            "terminal_propagation_s", terminal_propagation_s),
        resource_available=_optional_bool_pairs(
            "resource_available", resource_available),
        local_egress_in_service_s=_pairs(
            "local_egress_in_service_s", local_egress_in_service_s),
    )


# --------------------------------------------------------------------------
# history hygiene + predictors
# --------------------------------------------------------------------------
def order_history(samples, snapshot_at: float) -> tuple:
    """Received-only, source-time ascending, deduped history.

    Rules (task book 2.3): a sample that has not arrived by snapshot_at is not
    information; out-of-order arrivals never let an OLDER source state
    overwrite a NEWER one; exact source-time duplicates keep the freshest
    arrival.
    """
    latest = {}
    for s in samples:
        if s.received_at > snapshot_at:
            continue
        key = s.resource.as_tuple() + (s.measured_at,)
        old = latest.get(key)
        if old is None or s.received_at >= old.received_at:
            latest[key] = s
    return tuple(sorted(latest.values(),
                        key=lambda s: (s.resource.as_tuple(), s.measured_at)))


def _latest_by_resource(history) -> dict:
    grouped = {}
    for s in history:
        grouped.setdefault(s.resource.as_tuple(), []).append(s)
    for key in grouped:
        grouped[key].sort(key=lambda s: s.measured_at)
    return grouped


def predict_resource(history, snapshot_at: float, target_at: float,
                     method: str = "bounded_linear", history_limit: int = 8,
                     max_queue_bits=None, resource=None,
                     allow_past: bool = False) -> ResourcePrediction:
    """Predict the work queued at one resource at target_at.

    Pure: it reads only the received history it is given.  Invalid data is an
    explicit error (NaN / negative span / negative queue) or an explicit
    missing_reason (no history, non-positive rate, unknown resource) -- never a
    silent zero.

    allow_past is set ONLY by the stale arm, whose query instant is the last
    source measurement and therefore legitimately precedes the snapshot.  It
    still cannot reach into the future: no sample received after snapshot_at is
    ever used.
    """
    reject_future_input(history)
    snap = _finite("snapshot_at", snapshot_at)
    target = _finite("target_at", target_at)
    if target < snap and not allow_past:
        raise TimeAlignmentError(
            f"negative time span: target_at {target} < snapshot_at {snap}")
    if method not in PREDICTORS:
        raise TimeAlignmentError(f"method must be one of {PREDICTORS}, got {method!r}")
    if history_limit < 1:
        raise TimeAlignmentError("history_limit must be >= 1")
    if max_queue_bits is not None:
        max_queue_bits = _finite("max_queue_bits", max_queue_bits)
        if max_queue_bits < 0:
            raise TimeAlignmentError("max_queue_bits must be >= 0")
    ordered = order_history(history, snap)
    if resource is not None:
        ordered = tuple(s for s in ordered if s.resource == resource)
    if not ordered:
        res = resource if resource is not None else _placeholder_resource()
        return ResourcePrediction(res, snap, target, None, method,
                                  missing_reason="no_received_history")
    grouped = _latest_by_resource(ordered)
    if len(grouped) > 1 and resource is None:
        raise TimeAlignmentError(
            "predict_resource got samples from multiple resources without an "
            "explicit resource selector")
    series = next(iter(grouped.values()))[-history_limit:]
    last = series[-1]
    res = resource if resource is not None else last.resource
    if last.rate_bps is not None and last.rate_bps <= 0:
        return ResourcePrediction(res, snap, target, None, method,
                                  missing_reason="non_positive_rate")
    if method == "hold_last":
        value = last.queue_bits
    else:
        if len(series) < 2:
            value = last.queue_bits
            method = "hold_last"
        else:
            diffs = []
            for a, b in zip(series, series[1:]):
                dt = b.measured_at - a.measured_at
                if dt <= 0:
                    raise TimeAlignmentError(
                        "non-increasing source time in history: "
                        f"{a.measured_at} -> {b.measured_at}")
                diffs.append((b.queue_bits - a.queue_bits) / dt)
            diffs.sort()
            mid = len(diffs) // 2
            slope = (diffs[mid] if len(diffs) % 2
                     else 0.5 * (diffs[mid - 1] + diffs[mid]))
            value = last.queue_bits + slope * (target - last.measured_at)
        value = max(0.0, value)
        if max_queue_bits is not None:
            value = min(value, max_queue_bits)
    if not math.isfinite(value):
        raise TimeAlignmentError(f"prediction is not finite: {value!r}")
    return ResourcePrediction(res, snap, target, float(value), method)


def _placeholder_resource() -> ResourceKey:
    return ResourceKey(0, "N", KIND_ISL)


def arm_target_at(arm: str, *, snapshot_at: float, last_measured_at,
                  common_horizon_s, candidate_target_at: float) -> float:
    """The instant each arm queries.  This is the ONLY arm-dependent input."""
    if arm == "stale":
        if last_measured_at is None:
            raise TimeAlignmentError("stale arm requires at least one received sample")
        return last_measured_at
    if arm == "now":
        return snapshot_at
    if arm == "common":
        if common_horizon_s is None:
            raise TimeAlignmentError("common arm requires a common_horizon_s")
        return snapshot_at + common_horizon_s
    if arm == "candidate":
        if candidate_target_at < snapshot_at:
            raise TimeAlignmentError("candidate target is before the snapshot")
        return candidate_target_at
    raise TimeAlignmentError(f"unknown arm {arm!r}")


# --------------------------------------------------------------------------
# ETA build-up
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class EtaEstimate:
    """The predicted instant the packet reaches the target directed egress.

    Every term is a non-overlapping interval in seconds; known marks which
    terms the snapshot could actually supply.  Unknown holds are NOT folded in
    as zero -- they are listed in unknown_terms.
    """

    target_at: float
    terms: dict
    known: dict
    unknown_terms: tuple
    method: str

    def total(self) -> float:
        return float(sum(self.terms.values()))


def estimate_eta(snapshot: ObservationSnapshot, direction: str) -> EtaEstimate:
    """Build the ETA to one candidate directed egress from known inputs.

    ETA includes the local compute request, its later query request, and
    work-ahead on the source egress. Queue service continues during compute
    and query. Future arrivals are not available online and are assumed absent
    for this bounded forward estimate; prediction error is measured later.
    """
    reject_future_input(snapshot)
    terms = {}
    known = {}
    unknown = []

    # The ETA is the ONLY place a query instant is produced, so every local
    # delay a packet will actually spend must be inside it -- otherwise the
    # prediction is asked about an instant that is systematically too early.
    terms["compute_wait_s"] = snapshot.compute_wait_s
    known["compute_wait_s"] = True
    terms["compute_service_s"] = snapshot.compute_service_s
    known["compute_service_s"] = True
    terms["query_wait_s"] = snapshot.query_wait_s
    known["query_wait_s"] = True
    terms["query_service_s"] = snapshot.query_service_s
    known["query_service_s"] = True
    pre_egress_elapsed = (snapshot.compute_wait_s
                          + snapshot.compute_service_s
                          + snapshot.query_wait_s
                          + snapshot.query_service_s)

    rate = snapshot.rate_for(direction)
    egress = snapshot.egress_bits(direction)
    if rate is None:
        terms["tx_s"] = 0.0
        known["tx_s"] = False
        unknown.append("tx_s")
        terms["local_egress_wait_s"] = 0.0
        known["local_egress_wait_s"] = False
        unknown.append("local_egress_wait_s")
    else:
        if rate <= 0:
            raise TimeAlignmentError(f"non-positive local rate for {direction}")
        terms["tx_s"] = snapshot.pkt_bits / rate
        known["tx_s"] = True
        if egress is None:
            terms["local_egress_wait_s"] = 0.0
            known["local_egress_wait_s"] = False
            unknown.append("local_egress_wait_s")
        else:
            # This includes all known queued classes. The PHY keeps serving
            # while compute/query run, so subtract that elapsed time once.
            in_service = snapshot.local_egress_in_service_for(direction)
            if in_service is None:
                terms["local_egress_wait_s"] = 0.0
                known["local_egress_wait_s"] = False
                unknown.append("local_egress_wait_s")
            else:
                terms["local_egress_wait_s"] = max(
                    0.0, float(egress) / rate + float(in_service)
                    - pre_egress_elapsed)
                known["local_egress_wait_s"] = True

    prop = snapshot.propagation_for(direction)
    if prop is None:
        terms["prop_s"] = 0.0
        known["prop_s"] = False
        unknown.append("prop_s")
    else:
        terms["prop_s"] = prop
        known["prop_s"] = True

    peer = snapshot.peer_process_for(direction)
    if peer is None:
        terms["peer_process_s"] = 0.0
        known["peer_process_s"] = False
        unknown.append("peer_process_s")
    else:
        terms["peer_process_s"] = peer
        known["peer_process_s"] = True

    method = ETA_METHOD_DEFAULT_PEER if "peer_process_s" in unknown else ETA_METHOD_EXACT
    return EtaEstimate(snapshot.snapshot_at + sum(terms.values()), terms, known,
                       tuple(unknown), method)


# --------------------------------------------------------------------------
# unified scoring
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CandidateScore:
    direction: str
    resource: object
    total_s: float
    terms: dict
    missing: tuple
    fallback: bool
    tie_key: tuple


@dataclass(frozen=True)
class ScoredCandidates:
    ranking: tuple
    scores: tuple
    fallback_directions: tuple
    missing_directions: tuple

    def by_direction(self) -> dict:
        return {s.direction: s for s in self.scores}


def resource_service_bps(snapshot: ObservationSnapshot, direction: str):
    """Rate of the named target resource, distinct from the local first hop."""
    return snapshot.resource_rate_for(direction)


def score_candidates(snapshot: ObservationSnapshot, predictions, etas) -> ScoredCandidates:
    """One scorer for all four arms; only the query instant differs.

    Terms (seconds), each non-overlapping:
      compute_wait_s       estimated pool wait known at request time
      compute_service_s    configured computation service
      local_egress_wait_s  bits already queued on the local directed egress
      tx_s                 pkt_bits / known local egress rate
      prop_s               known local propagation
      peer_process_s       known peer processing (else missing)
      resource_work_s      predicted peer-egress work / known service rate
      remaining_prop_s     fixed visible-topology remaining propagation cost

    A candidate with any missing term is NOT given an invented numeric value:
    it is marked fallback and ranked after every fully-known candidate, using
    the same stable direction order for every arm.
    """
    reject_future_input(snapshot, predictions, etas)
    scores = []
    no_strong_common = (
        snapshot.arm == "common"
        and snapshot.common_rule != "fixed_horizon"
        and snapshot.common_horizon_s is None)
    visible_order = {direction: index for index, direction in
                     enumerate(snapshot.legal_directions)}
    for direction in snapshot.legal_directions:
        resource = snapshot.resource_for(direction)
        prediction = predictions.get(direction)
        eta = etas.get(direction)
        missing = []
        terms = {}
        if resource is None:
            missing.append("resource_mapping")
        if prediction is None:
            missing.append("prediction")
        elif prediction.missing_reason:
            missing.append(prediction.missing_reason)
        if eta is None:
            missing.append("eta")
        if no_strong_common:
            scores.append(CandidateScore(
                direction, resource, math.inf, {}, ("NO_STRONG_COMMON",),
                True, (visible_order[direction], direction)))
            continue
        if missing:
            scores.append(CandidateScore(direction, resource, math.inf, {},
                                         tuple(missing), True,
                                         (visible_order[direction], direction)))
            continue
        # The local terms come from the ETA verbatim: re-deriving the egress
        # wait here would both double-count it and leave the query instant
        # untouched, which is exactly the defect this fixes.
        for name in ("compute_wait_s", "compute_service_s",
                     "query_wait_s", "query_service_s",
                     "local_egress_wait_s", "tx_s", "prop_s",
                     "peer_process_s"):
            terms[name] = eta.terms.get(name, 0.0)
        for name in eta.unknown_terms:
            if name in ETA_TERM_KEYS:
                missing.append(name)
        service_rate = resource_service_bps(snapshot, direction)
        if prediction.predicted_bits is None:
            missing.append("predicted_bits")
        elif service_rate is None or service_rate <= 0:
            missing.append("resource_service_rate")
        else:
            terms["resource_work_s"] = prediction.predicted_bits / service_rate
        resource_key = snapshot.resource_for(direction)
        if resource_key is not None and resource_key.kind == KIND_ISL:
            # The target resource is reached after the local first hop.  Its
            # existing work-ahead is resource_work_s; this packet's own
            # serialization on that resource is a separate interval and must
            # be priced with the target resource's advertised rate.
            if service_rate is None or service_rate <= 0:
                missing.append("resource_service_rate")
            else:
                terms["resource_packet_tx_s"] = snapshot.pkt_bits / service_rate
        if resource_key is not None and resource_key.kind == KIND_DOWNLINK:
            availability = snapshot.resource_available_for(direction)
            if availability is not True:
                missing.append("terminal_resource_unavailable" if availability is False
                               else "terminal_resource_availability_unknown")
            if prediction.predicted_bits is not None \
                    and service_rate is not None and service_rate > 0:
                # Queue bits and the newly routed packet are separate work;
                # use the advertised GSL rate, never the local ISL rate.
                # This is the target packet's GSL serialization.  It already
                # supplies target-resource service, so do not also add the
                # generic ISL target-service term above.
                terms["terminal_tx_s"] = snapshot.pkt_bits / service_rate
            terminal_prop = snapshot.terminal_prop_for(direction)
            if terminal_prop is None:
                missing.append("terminal_prop_s")
            else:
                terms["terminal_prop_s"] = terminal_prop
        remaining = snapshot.remaining_prop_for(direction)
        if remaining is None:
            missing.append("remaining_prop")
        else:
            terms["remaining_prop_s"] = remaining
        if missing:
            scores.append(CandidateScore(direction, resource, math.inf, terms,
                                         tuple(sorted(set(missing))), True,
                                         (visible_order[direction], direction)))
            continue
        total = float(sum(terms.values()))
        scores.append(CandidateScore(direction, resource, total, terms, (),
                                     False,
                                     (0, visible_order[direction], direction)))
    scores.sort(key=lambda s: (s.fallback,
                               0.0 if s.fallback else s.total_s,
                               visible_order[s.direction], s.direction))
    return ScoredCandidates(
        ranking=tuple(s.direction for s in scores),
        scores=tuple(scores),
        fallback_directions=tuple(s.direction for s in scores if s.fallback),
        missing_directions=tuple(s.direction for s in scores if s.missing),
    )


# --------------------------------------------------------------------------
# schedule tables (async point / window), table lookup only
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ScheduleEntry:
    bin_index: int
    starts_at: float
    ends_at: float
    ranking: tuple
    query_target_at: float | None = None


@dataclass(frozen=True)
class Schedule:
    scope: tuple
    version: int
    installed_at: float
    expires_at: float
    source_snapshot_at: float
    entries: tuple
    trigger: str = "periodic"

    def bin_for(self, now: float):
        if now < self.installed_at or now > self.expires_at:
            return None
        for entry in self.entries:
            if entry.starts_at <= now < entry.ends_at:
                return entry
        if self.entries and now == self.expires_at:
            return self.entries[-1]
        return None


def _scope_of(snapshot: ObservationSnapshot) -> tuple:
    traffic_class = "default"
    for item in snapshot.provenance:
        if item.startswith("scope:"):
            parts = item.split("|")
            if len(parts) == 4:
                return (int(parts[1]), parts[2], parts[3])
    return (snapshot.satellite, None, traffic_class)


@dataclass(frozen=True)
class DecisionPlan:
    """One arm's complete decision input, with the instants it actually used.

    targets is the per-direction query instant the arm chose; it is recorded so
    an online run, an offline replay and a benchmark can be checked against each
    other instead of each assuming its own instant.
    """

    snapshot: ObservationSnapshot
    targets: tuple
    scored: ScoredCandidates
    horizon_s: float | None

    def chosen(self):
        return self.scored.ranking[0] if self.scored.ranking else None

    def target_of(self, direction: str):
        for name, instant in self.targets:
            if name == direction:
                return instant
        return None


def _common_comparable_offsets(snapshot: ObservationSnapshot) -> tuple[float, ...]:
    """Return only finite ETAs with every required term observed/known."""
    offsets = []
    for direction in snapshot.legal_directions:
        eta = estimate_eta(snapshot, direction)
        if eta.unknown_terms:
            continue
        offset = eta.target_at - snapshot.snapshot_at
        if math.isfinite(offset) and offset >= 0.0:
            offsets.append(float(offset))
    return tuple(sorted(offsets))


def resolve_common_horizon(snapshot: ObservationSnapshot,
                           rule: str | None = None) -> float | None:
    """The shared t0+h for the common arm, from candidate ETA offsets.

    Uses the SAME estimator the scorer uses, so the horizon and the per-candidate
    ETAs cannot come from two different models.
    """
    rule = rule or snapshot.common_rule
    if rule == "fixed_horizon":
        if snapshot.common_horizon_s is None:
            raise TimeAlignmentError(
                "fixed_horizon requires a configured common_horizon_s")
        return float(snapshot.common_horizon_s)
    offsets = _common_comparable_offsets(snapshot)
    # A one-point statistic is not a meaningful shared reference. Unknown
    # candidates are excluded rather than represented as zero offsets.
    if len(offsets) < 2:
        return None
    if rule == "mean_eta":
        return float(sum(offsets) / len(offsets))
    mid = len(offsets) // 2
    if len(offsets) % 2:
        return float(offsets[mid])
    return float(0.5 * (offsets[mid - 1] + offsets[mid]))


def build_predictions(snapshot: ObservationSnapshot,
                      target_at: float | None = None):
    """(predictions, etas, targets) for every legal candidate at the arm instant.

    Extracted so the benchmark can time prediction separately from scoring
    WITHOUT re-implementing the instant rule: plan_decision calls this, and so
    does the timer.
    """
    reject_future_input(snapshot)
    explicit_target = (None if target_at is None
                       else _finite("target_at", target_at))
    if (explicit_target is not None
            and explicit_target < snapshot.snapshot_at):
        raise TimeAlignmentError(
            "explicit target_at may not precede the frozen snapshot")
    predictions = {}
    etas = {}
    targets = {}
    for direction in snapshot.legal_directions:
        resource = snapshot.resource_for(direction)
        eta = estimate_eta(snapshot, direction)
        etas[direction] = eta
        last_measured = _last_measured_at(snapshot, resource)
        if explicit_target is not None:
            # Async window bins and offline fixed-instant comparisons ask the
            # SAME causal estimator about an explicit absolute instant.  The
            # normal online four-arm path passes no override and therefore
            # keeps its arm-specific target rule unchanged.
            target = explicit_target
        elif (snapshot.arm == "common"
              and snapshot.common_rule != "fixed_horizon"
              and snapshot.common_horizon_s is None):
            # NO_STRONG_COMMON uses the shared fallback; t0 here is a
            # non-decision placeholder so every candidate remains auditable.
            target = snapshot.snapshot_at
        elif last_measured is None:
            # no advertisement for this candidate: the query instant is
            # irrelevant, predict_resource reports no_received_history
            target = snapshot.snapshot_at
        else:
            target = arm_target_at(
                snapshot.arm, snapshot_at=snapshot.snapshot_at,
                last_measured_at=last_measured,
                common_horizon_s=snapshot.common_horizon_s,
                candidate_target_at=eta.target_at)
        targets[direction] = target
        predictions[direction] = predict_resource(
            snapshot.history_for(resource) if resource is not None else (),
            snapshot_at=snapshot.snapshot_at, target_at=target,
            method=snapshot.predictor, history_limit=snapshot.history_limit,
            max_queue_bits=snapshot.max_resource_queue_bits, resource=resource,
            allow_past=(snapshot.arm == "stale"))
    return predictions, etas, targets


def plan_decision(snapshot: ObservationSnapshot,
                  target_at: float | None = None) -> DecisionPlan:
    """THE decision entry point: query instant -> prediction -> score.

    The kernel online path, the offline replay and the benchmark all call THIS
    function with a snapshot that already carries the arm and its horizon, so
    the three cannot query different instants for the same arm.
    """
    predictions, etas, targets = build_predictions(snapshot, target_at=target_at)
    return DecisionPlan(snapshot=snapshot,
                        targets=tuple(sorted(targets.items())),
                        scored=score_candidates(snapshot, predictions, etas),
                        horizon_s=snapshot.common_horizon_s)


def score_snapshot_at(snapshot: ObservationSnapshot,
                      target_at: float | None = None) -> ScoredCandidates:
    """Score at an explicit absolute instant, or use the arm's normal instant.

    Ordinary online calls go through :func:`plan_decision` without an
    override.  This function's explicit instant is used by offline diagnostics
    and asynchronous schedule bins; it is still a causal prediction from the
    snapshot's received history, never a read of truth at that instant.
    """
    return plan_decision(snapshot, target_at=target_at).scored


def _last_measured_at(snapshot: ObservationSnapshot, resource):
    if resource is None:
        return None
    hits = [s for s in snapshot.history
            if s.resource == resource and s.received_at <= snapshot.snapshot_at]
    if not hits:
        return None
    return max(s.measured_at for s in hits)


def build_schedule(snapshot: ObservationSnapshot, install_estimate: float,
                   window_s: float, bins: int, version: int = 1,
                   trigger: str = "periodic") -> Schedule:
    """Build a version candidate table for one scope.

    bins == 1 is async_point (a single ranking for the whole window); bins > 1
    is async_window.  Each bin comes from the SAME snapshot and predictor; a
    bin is predicted at its own midpoint.  This function is pure and returns
    the candidate table -- it does not install anything.
    """
    reject_future_input(snapshot)
    install_estimate = _finite("install_estimate", install_estimate)
    window_s = _finite("window_s", window_s)
    if window_s <= 0:
        raise TimeAlignmentError("window_s must be > 0")
    if isinstance(bins, bool) or not isinstance(bins, int) or bins < 1:
        raise TimeAlignmentError("bins must be a positive integer")
    entries = []
    width = window_s / bins
    for i in range(bins):
        start = install_estimate + i * width
        end = start + width
        midpoint = 0.5 * (start + end)
        scored = score_snapshot_at(snapshot, midpoint)
        entries.append(ScheduleEntry(i, start, end, scored.ranking,
                                     query_target_at=midpoint))
    return Schedule(scope=_scope_of(snapshot), version=int(version),
                    installed_at=install_estimate,
                    expires_at=install_estimate + window_s,
                    source_snapshot_at=snapshot.snapshot_at,
                    entries=tuple(entries), trigger=trigger)


def lookup_schedule(schedule: Schedule, now: float, legal, path=()) -> dict:
    """Query an already-installed table.  Never predicts, never re-scores.

    Returns the first bin action that is legal and does not revisit a node on
    the path; otherwise an explicit safe fallback with a reason.
    """
    reject_future_input(schedule)
    legal_set = {str(d) for d in legal}
    visited = {str(p) for p in path}
    entry = schedule.bin_for(now)
    if entry is None:
        return {"action": None, "bin": None, "version": schedule.version,
                "state": "expired", "reason": "no_bin_covers_now",
                "fallback": True}
    for action in entry.ranking:
        if action in legal_set and action not in visited:
            return {"action": action, "bin": entry.bin_index,
                    "version": schedule.version, "state": "active",
                    "query_target_at": entry.query_target_at,
                    "reason": None, "fallback": False}
    return {"action": None, "bin": entry.bin_index, "version": schedule.version,
            "state": "fallback", "reason": "no_legal_unvisited_direction",
            "fallback": True}
