"""Frozen routing state policies (POLICY-SPEC-20261031 §2-§5).

ONE decision layer, four frozen rules
-------------------------------------

Every policy in this module ranks the SAME already-scored candidate set: the
kernel keeps candidate generation, legal/loop/physical filtering, the physical
model, the control plane and the existing time-alignment terms untouched, and
only the mapping "terms -> ordered actions" changes.  The definitions are
frozen before the experiment and must not be tuned after seeing results.

The four policies (POLICY-SPEC-20261031):

``legacy``
    No policy layer at all: the order is the existing scorer ranking, byte for
    byte.  This is the default and the regression anchor.
``geometry``
    Negative control.  ``J = tx_s + prop_s + remaining_prop_s``: every dynamic
    queue term (``resource_work_s``, ``local_egress_wait_s``) and the peer
    processing state are excluded, so the same stale queue advertisement can
    never change the action.
``queue_greedy``
    High-sensitivity baseline.  ``J`` is the existing scorer ``total_s``
    (hold-last advertised queue, no extrapolation), i.e. exactly
    ``argmin_a J_a`` with the existing tie-break.
``queue_greedy_hysteresis``
    Greedy ``J`` plus a per ``(satellite, destination cell)`` memory ``L`` of
    the last direction a FORWARD action was committed on.  Keep ``L`` when it
    is still legal and ``J_L <= J_best + eta``; otherwise switch to ``J_best``.
    ``eta = 0`` is "keep only while L is still (tied-)optimal".
``queue_greedy_ewma``
    Greedy ``J`` with the instantaneous queue level replaced by a recursive
    EWMA over the samples that ALREADY ARRIVED at the decision instant:
    ``qbar_1 = q_1``, ``qbar_k = alpha*q_k + (1-alpha)*qbar_{k-1}``, and
    ``J = J_a - resource_work_s + qbar_a / rate_a``.  ``n = 1`` therefore
    degenerates to greedy.  The level is recomputed from the received series on
    every decision (pure, deterministic; no hidden running state).

Information boundary
--------------------

This module is PURE.  It has no kernel handle, no cache, no event queue and no
sink: it only reads the ``ScoredCandidates`` it is handed plus the received
history series, and returns a new order and a machine-readable audit.  A
sample that has not arrived by ``snapshot_at`` is not information; a missing
term is never invented (the candidate stays a fallback, ranked after every
fully-known candidate with the existing stable direction order).

Audit contract (fixed by the Lead; offline flip/regret replay depends on it)
---------------------------------------------------------------------------

``rank`` returns ``audit`` for ``ta_audit["state_policy"]`` with these keys:

``policy``
    The policy name.
``hysteresis_eta_s``
    The eta actually used, or ``None`` when the policy does not use eta.
``ewma_alpha``
    The alpha actually used, or ``None`` when the policy does not use it.
``last_direction`` / ``snapshot_at``
    The committed memory entry consulted and the frozen decision instant.
``per_direction``
    One entry per scored direction with EXACTLY these keys:
    ``j_used_s`` (the J this policy sorted on; geometry included),
    ``resource_work_used_s`` (the queue time term this policy actually
    counted: ``0.0`` for geometry, ``qbar/rate`` for EWMA, the predictor's
    ``resource_work_s`` for greedy/hysteresis, ``None`` when the direction is
    a fallback), ``q_used_bits`` (the queue bits that term corresponds to;
    ``None`` for geometry/fallback), ``service_rate_bps`` (the rate that term
    was divided by), ``ewma_samples`` (arrived samples used, ``0`` for
    non-EWMA), ``fallback`` and ``kept`` (``True`` only for the direction the
    hysteresis rule kept).
    The identity ``resource_work_used_s == q_used_bits / service_rate_bps``
    holds whenever both sides are not null, which is what makes the offline
    replay ``J_fresh = j_used_s - resource_work_used_s
    + truth_bits / service_rate_bps`` exact.
``selection``
    ``{"winner": direction|None, "rule": "argmin"|"hysteresis_keep",
    "kept_direction": direction|None}``.
``scorer_ranking`` / ``applied_order`` / ``reason``
    Diagnostics: what the untouched scorer said, what this policy returned,
    and why.

What a caller must not do
-------------------------

* Do not re-rank candidates this module excluded for missing data.
* Do not feed it truth/current state: ``history_by_resource`` must be the
  received advertisements (``time_alignment.order_history`` output).
"""
from __future__ import annotations

import math

SCHEMA = "leo-sim-state-policy/v1"

LEGACY = "legacy"
GEOMETRY = "geometry"
QUEUE_GREEDY = "queue_greedy"
QUEUE_GREEDY_HYSTERESIS = "queue_greedy_hysteresis"
QUEUE_GREEDY_EWMA = "queue_greedy_ewma"

POLICIES = (LEGACY, GEOMETRY, QUEUE_GREEDY, QUEUE_GREEDY_HYSTERESIS,
            QUEUE_GREEDY_EWMA)
QUEUE_POLICIES = (QUEUE_GREEDY, QUEUE_GREEDY_HYSTERESIS, QUEUE_GREEDY_EWMA)

#: Pre-registered grid (POLICY-SPEC-20261031 §4).  ``eta`` may only take a
#: value from this grid; the main experiment is frozen at 0.005 s and other
#: values are sensitivity runs only.
HYSTERESIS_ETA_GRID_S = (0.0, 0.002, 0.005, 0.010)
#: Pre-registered grid (POLICY-SPEC-20261031 §5).  Main value 0.25.
EWMA_ALPHA_GRID = (0.1, 0.25, 0.5)

#: Membership tolerance for the two frozen grids.  The grids are decimal
#: constants that are not exactly representable in binary floating point; the
#: tolerance is far below any physically meaningful separation (a picosecond
#: for eta) so it cannot be used to smuggle in a different value.
GRID_TOL = 1e-12

#: The geometry policy's cost terms, in POLICY-SPEC §2 order.  Everything
#: dynamic (queue state, peer processing) is deliberately absent.
GEOMETRY_TERMS = ("tx_s", "prop_s", "remaining_prop_s")

#: Terms that are dynamic queue/congestion state and must not enter geometry.
QUEUE_TERMS = ("resource_work_s", "local_egress_wait_s")

_SELECTION_ARGMIN = "argmin"
_SELECTION_HYSTERESIS_KEEP = "hysteresis_keep"


class StatePolicyError(ValueError):
    """A state-policy request that cannot be honoured without inventing data."""


# --------------------------------------------------------------------------
# parameter validation (fail loud, same message shape as config.py)
# --------------------------------------------------------------------------
def _finite(name: str, value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatePolicyError(f"{name} must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value):
        raise StatePolicyError(f"{name} must be finite, got {value!r}")
    return value


def _on_grid(value: float, grid) -> bool:
    return any(abs(value - float(candidate)) <= GRID_TOL for candidate in grid)


def validate_policy(policy) -> str:
    if not isinstance(policy, str) or policy not in POLICIES:
        raise StatePolicyError(
            f"state_policy must be one of {list(POLICIES)}, got {policy!r}")
    return policy


def validate_eta_s(value, frozen_grid: bool = True) -> float:
    """``hysteresis_eta_s``: finite, >= 0 (and, for a RUN, on the frozen grid).

    ``frozen_grid=True`` is the config/kernel rule: the pre-registered grid is
    part of the experiment identity and may not be chosen after results.
    ``frozen_grid=False`` is the mathematical domain used by ``rank`` itself,
    so the frozen definition can be tested (e.g. eta=0 vs a large eta) without
    the experiment-registration rule.
    """
    eta = _finite("hysteresis_eta_s", value)
    if eta < 0.0:
        raise StatePolicyError(
            f"hysteresis_eta_s must be >= 0, got {value!r}")
    if frozen_grid and not _on_grid(eta, HYSTERESIS_ETA_GRID_S):
        raise StatePolicyError(
            "hysteresis_eta_s must be one of the pre-registered grid "
            f"{list(HYSTERESIS_ETA_GRID_S)} s, got {value!r}; the value is "
            "frozen before the experiment and may not be chosen afterwards")
    return eta


def validate_alpha(value, frozen_grid: bool = True) -> float:
    """``ewma_alpha``: finite, in (0, 1] (and, for a RUN, on the frozen grid).

    Same split as ``validate_eta_s``: the grid is the config rule, the open
    interval is the mathematical domain (so alpha=1 "instantaneous" and
    alpha -> 0 "first sample" remain testable).
    """
    alpha = _finite("ewma_alpha", value)
    if not 0.0 < alpha <= 1.0:
        raise StatePolicyError(f"ewma_alpha must be in (0, 1], got {value!r}")
    if frozen_grid and not _on_grid(alpha, EWMA_ALPHA_GRID):
        raise StatePolicyError(
            "ewma_alpha must be one of the pre-registered grid "
            f"{list(EWMA_ALPHA_GRID)}, got {value!r}; the value is frozen "
            "before the experiment and may not be chosen afterwards")
    return alpha


# --------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------
def _resource_key(resource):
    """Hashable identity of the target resource (``None`` when unmapped)."""
    if resource is None:
        return None
    as_tuple = getattr(resource, "as_tuple", None)
    if callable(as_tuple):
        return as_tuple()
    return resource


def _samples_for(history_by_resource, resource):
    """The received sample series of one resource, or an empty tuple.

    ``history_by_resource`` is either the ``time_alignment._latest_by_resource``
    mapping (``resource.as_tuple() -> [StateSample, ...]``) or a flat sample
    sequence.  Any other type is a programming error, not "no history": a typo
    must not silently degrade a policy to greedy.
    """
    if history_by_resource is None:
        return ()
    if isinstance(history_by_resource, dict):
        key = _resource_key(resource)
        for candidate in (key, resource):
            if candidate is not None and candidate in history_by_resource:
                return tuple(history_by_resource[candidate])
        return ()
    if isinstance(history_by_resource, (list, tuple)):
        key = _resource_key(resource)
        if key is None:
            return ()
        return tuple(s for s in history_by_resource
                     if _resource_key(getattr(s, "resource", None)) == key)
    raise StatePolicyError(
        "history_by_resource must be None, a mapping, or a sample sequence, "
        f"got {type(history_by_resource).__name__}")


def _arrived_series(samples, snapshot_at) -> tuple:
    """Received-only, deduped, source-time-ascending series (PUBLIC RULE).

    Same rule as ``time_alignment.order_history``: a sample received after the
    snapshot is not information; an out-of-order arrival never lets an older
    source state overwrite a newer one; duplicate source instants keep the
    freshest arrival.  Samples measured after ``snapshot_at`` are excluded as
    well (POLICY-SPEC §5: future samples never enter).
    """
    cutoff = _finite("snapshot_at", snapshot_at)
    latest = {}
    for sample in samples:
        measured_at = getattr(sample, "measured_at", None)
        received_at = getattr(sample, "received_at", None)
        queue_bits = getattr(sample, "queue_bits", None)
        if measured_at is None or received_at is None or queue_bits is None:
            raise StatePolicyError(
                "history samples must carry measured_at/received_at/queue_bits")
        measured = _finite("measured_at", measured_at)
        received = _finite("received_at", received_at)
        if received > cutoff or measured > cutoff:
            continue
        key = (_resource_key(getattr(sample, "resource", None)), measured)
        old = latest.get(key)
        if old is None or received >= _finite("received_at", old.received_at):
            latest[key] = sample
    return tuple(sorted(latest.values(),
                        key=lambda s: (float(s.measured_at),
                                       float(s.received_at))))


def ewma_queue_bits(samples, snapshot_at, alpha) -> tuple:
    """Recursive EWMA of the queue level over the ARRIVED samples.

    Returns ``(level_bits, n_samples)``; ``level`` is ``None`` when the series
    is empty (nothing is invented for a resource with no arrived sample).
    ``n == 1`` returns that single sample, so the policy degenerates to greedy.
    ``alpha == 1`` returns the newest sample; ``alpha -> 0`` approaches the
    oldest sample.
    """
    alpha = validate_alpha(alpha, frozen_grid=False)
    series = _arrived_series(samples, snapshot_at)
    if not series:
        return None, 0
    level = float(series[0].queue_bits)
    for sample in series[1:]:
        level = alpha * float(sample.queue_bits) + (1.0 - alpha) * level
    return level, len(series)


def _direction_index(scored, visible_order=None) -> dict:
    """Stable direction order used by the existing scorer's tie-break.

    ``visible_order`` is the snapshot's legal-direction sequence (what
    ``time_alignment.score_candidates`` uses as ``visible_order``).  When the
    caller does not supply it, the scorer's own ranking is used as the stable
    order, which is the same order for a fully-known set.
    """
    if visible_order is not None:
        return {str(d): int(i) for i, d in enumerate(visible_order)}
    return {str(d): int(i) for i, d in enumerate(scored.ranking)}


def _quantise(value: float, tie_sig_digits) -> float:
    """The scorer's diagnostics-only quantisation, reused so a tie-break
    experiment cannot mean one thing in the scorer and another here."""
    if tie_sig_digits:
        return float(f"{value:.{int(tie_sig_digits)}e}")
    return value


def _geometry_j(score) -> tuple:
    """``(J, terms)`` for ``tx_s + prop_s + remaining_prop_s``.

    ``None`` J means the geometry cost is not fully known: such a candidate is
    NOT given an invented value (the same rule the scorer applies to its own
    missing terms).
    """
    terms = score.terms or {}
    used = {}
    total = 0.0
    for name in GEOMETRY_TERMS:
        value = terms.get(name)
        if value is None:
            return None, None
        try:
            value = _finite(name, value)
        except StatePolicyError:
            return None, None
        used[name] = value
        total += value
    return float(total), used


def _greedy_j(score):
    """The existing scorer total (hold-last queue, no extrapolation)."""
    if score.fallback:
        return None
    total = score.total_s
    if total is None:
        return None
    try:
        return _finite("total_s", total)
    except StatePolicyError:
        return None


def _ewma_cost(score, history_by_resource, snapshot_at, alpha) -> tuple:
    """``(J, qbar_bits, work_s, n_samples)`` with the EWMA queue level.

    ``J = total_s - resource_work_s + qbar/rate``; any missing factor leaves J
    ``None`` (fallback semantics), never a guessed number.
    """
    if score.fallback:
        return None, None, None, 0
    terms = score.terms or {}
    work = terms.get("resource_work_s")
    rate = score.service_rate_bps
    if work is None or rate is None:
        return None, None, None, 0
    try:
        work = _finite("resource_work_s", work)
        rate = _finite("service_rate_bps", rate)
    except StatePolicyError:
        return None, None, None, 0
    if rate <= 0.0:
        return None, None, None, 0
    base = _greedy_j(score)
    if base is None:
        return None, None, None, 0
    level, count = ewma_queue_bits(
        _samples_for(history_by_resource, score.resource), snapshot_at, alpha)
    if level is None:
        return None, None, None, count
    work_s = level / rate
    return float(base - work + work_s), float(level), float(work_s), count


def _sorted_known(j_by_direction, scored, index, tie_sig_digits) -> list:
    """Fully-known candidates by (J, visible order, direction), then the
    fallbacks in the scorer's existing stable order."""
    known = []
    unknown = []
    order_of = {s.direction: i for i, s in enumerate(scored.scores)}
    for direction, j in j_by_direction.items():
        if j is None:
            unknown.append(direction)
        else:
            known.append((_quantise(float(j), tie_sig_digits),
                          index.get(direction, order_of.get(direction, 0)),
                          direction))
    known.sort()
    unknown.sort(key=lambda d: (index.get(d, order_of.get(d, 0)), d))
    return [d for _j, _i, d in known] + unknown


def _min_j(j_by_direction, index) -> tuple:
    """``(best_direction, best_j)`` over the fully-known candidates."""
    best = None
    for direction, j in j_by_direction.items():
        if j is None:
            continue
        key = (float(j), index.get(direction, 0), direction)
        if best is None or key < best[0]:
            best = (key, direction, float(j))
    if best is None:
        return None, None
    return best[1], best[2]


# --------------------------------------------------------------------------
# the ONE entry point
# --------------------------------------------------------------------------
def rank(scored, history_by_resource=None, snapshot_at=None, policy=LEGACY,
         eta_s=0.0, alpha=0.25, last_direction=None, visible_order=None,
         tie_sig_digits=None) -> tuple:
    """Order one decision's already-scored candidates under one policy.

    Parameters
    ----------
    scored : time_alignment.ScoredCandidates
        ``ranking``/``scores`` as produced by ``score_candidates``.  Untouched.
    history_by_resource : mapping or sequence, optional
        The RECEIVED advertisement samples (``order_history`` output grouped by
        resource).  Only ``queue_greedy_ewma`` reads it.
    snapshot_at : float
        The frozen decision instant; samples received after it never enter.
        Required by every policy that measures a series (EWMA).
    policy : str
        One of ``POLICIES``.
    eta_s, alpha : float
        Frozen hysteresis / EWMA parameters (validated against the
        pre-registered grids).
    last_direction : str or None
        The direction the last committed FORWARD action used for this
        ``(satellite, destination)``; ``None`` when nothing was committed yet.
    visible_order : sequence of str, optional
        The snapshot's legal-direction order (the scorer's tie-break order).
    tie_sig_digits : int or None
        The scorer's diagnostics-only quantisation, applied to the policy J so
        a tie experiment means the same thing on both sides.

    Returns
    -------
    (order, audit)
        ``order`` is the new direction order over ``scored``'s candidates.
        Candidates the caller holds that were never scored are NOT appended
        here: the caller keeps its existing append rule.  ``audit`` is the
        record for ``ta_audit["state_policy"]`` (contract in the module
        docstring).
    """
    validate_policy(policy)
    # The MATHEMATICAL domain is checked here; the pre-registered grid is the
    # RUN rule and is enforced by config.py / Kernel.__init__, so the frozen
    # definition stays testable (eta=0, alpha=1) without weakening a run.
    eta_s = validate_eta_s(eta_s, frozen_grid=False)
    alpha = validate_alpha(alpha, frozen_grid=False)
    ranking = [str(d) for d in scored.ranking]
    index = _direction_index(scored, visible_order)
    fallback_by_direction = {s.direction: bool(s.fallback)
                             for s in scored.scores}

    # per-direction policy cost, the queue work actually counted, the queue
    # bits that work corresponds to, and the EWMA sample count.
    j_by_direction = {}
    work_by_direction = {}
    q_by_direction = {}
    ewma_samples = {d: 0 for d in fallback_by_direction}
    geometry_terms = {}
    if policy == GEOMETRY:
        for score in scored.scores:
            j, terms = _geometry_j(score)
            j_by_direction[score.direction] = j
            geometry_terms[score.direction] = terms
            work_by_direction[score.direction] = (None if j is None else 0.0)
            q_by_direction[score.direction] = None
    elif policy == QUEUE_GREEDY_EWMA:
        for score in scored.scores:
            j, level, work_s, count = _ewma_cost(
                score, history_by_resource, snapshot_at, alpha)
            j_by_direction[score.direction] = j
            work_by_direction[score.direction] = work_s
            q_by_direction[score.direction] = level
            ewma_samples[score.direction] = int(count)
    else:  # legacy / queue_greedy / queue_greedy_hysteresis
        for score in scored.scores:
            j = _greedy_j(score)
            j_by_direction[score.direction] = j
            work = (score.terms or {}).get("resource_work_s")
            rate = score.service_rate_bps
            work = None if work is None else float(work)
            work_by_direction[score.direction] = (
                work if (work is not None and rate is not None
                         and float(rate) > 0.0) else None)
            q_by_direction[score.direction] = (
                None if work_by_direction[score.direction] is None
                else float(work) * float(rate))

    kept_direction = None
    kept_by_direction = {d: False for d in fallback_by_direction}
    rule = _SELECTION_ARGMIN
    j_last = None
    j_best = None
    best_direction = None
    reason = None

    if policy == LEGACY:
        # The legacy layer is the existing ranking itself; no re-sort, no
        # extra decision, so this path cannot drift from the pre-policy code.
        order = list(ranking)
        best_direction = order[0] if order else None
        reason = ("no policy layer: the existing scorer ranking is used "
                  "unchanged (byte-identical to the pre-policy path)")
    elif policy == QUEUE_GREEDY:
        # The greedy definition IS the existing ranking; re-sorting it could
        # only differ by float ties the scorer already broke deterministically.
        order = list(ranking)
        best_direction, j_best = _min_j(j_by_direction, index)
        reason = "argmin of the existing scorer total (hold-last queue)"
    elif policy == QUEUE_GREEDY_HYSTERESIS:
        best_direction, j_best = _min_j(j_by_direction, index)
        order = list(ranking)
        if last_direction is None:
            reason = "no committed forward direction for this (sat, dst) yet"
        elif str(last_direction) not in j_by_direction:
            reason = ("last direction %r is not a scored candidate: switch"
                      % (last_direction,))
        else:
            j_last = j_by_direction[str(last_direction)]
            if j_last is None:
                reason = ("last direction %r has no known cost (fallback): "
                          "switch" % (last_direction,))
            elif j_best is None:
                reason = "no fully-known candidate: the scorer order is kept"
            elif float(j_last) <= float(j_best) + eta_s:
                kept_direction = str(last_direction)
                kept_by_direction[kept_direction] = True
                rule = _SELECTION_HYSTERESIS_KEEP
                best_direction = kept_direction
                order = [kept_direction] + [
                    d for d in order if d != kept_direction]
                reason = ("J_last %.12g <= J_best %.12g + eta %.12g s: hold"
                          % (float(j_last), float(j_best), eta_s))
            else:
                reason = ("J_last %.12g > J_best %.12g + eta %.12g s: switch"
                          % (float(j_last), float(j_best), eta_s))
    else:  # geometry / queue_greedy_ewma
        order = _sorted_known(j_by_direction, scored, index, tie_sig_digits)
        best_direction, j_best = _min_j(j_by_direction, index)
        reason = ("geometry cost only (queue terms excluded)"
                  if policy == GEOMETRY else
                  "existing total with the arrived-sample EWMA queue level")

    per_direction = {}
    for score in scored.scores:
        direction = score.direction
        rate = score.service_rate_bps
        entry = {
            "j_used_s": j_by_direction.get(direction),
            "resource_work_used_s": work_by_direction.get(direction),
            "q_used_bits": q_by_direction.get(direction),
            "service_rate_bps": (None if rate is None else float(rate)),
            "ewma_samples": int(ewma_samples.get(direction, 0)),
            "fallback": bool(fallback_by_direction.get(direction, False)),
            "kept": bool(kept_by_direction.get(direction, False)),
        }
        if policy == GEOMETRY:
            entry["geometry_terms_s"] = geometry_terms.get(direction)
        per_direction[direction] = entry

    audit = {
        "schema": SCHEMA,
        "policy": policy,
        "hysteresis_eta_s": (float(eta_s) if policy == QUEUE_GREEDY_HYSTERESIS
                              else None),
        "ewma_alpha": (float(alpha) if policy == QUEUE_GREEDY_EWMA else None),
        "last_direction": (None if last_direction is None
                           else str(last_direction)),
        "snapshot_at": (None if snapshot_at is None
                        else float(snapshot_at)),
        "per_direction": per_direction,
        "selection": {
            "winner": best_direction,
            "rule": rule,
            "kept_direction": kept_direction,
        },
        "j_last_s": j_last,
        "j_best_s": j_best,
        "threshold_s": (float(eta_s) if policy == QUEUE_GREEDY_HYSTERESIS
                        else None),
        "reason": reason,
        "applied_order": list(order),
        "scorer_ranking": list(ranking),
        "source": "leo_sim.state_policy.rank: received advertisements and the "
                  "existing scorer terms only; no truth, no extrapolation",
    }
    return list(order), audit
