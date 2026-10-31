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
``queue_softmin`` (Round 3, P4 -- continuous response strength)
    The SAME ``J`` as ``queue_greedy`` (hold-last queue, no extrapolation), but
    the action is SAMPLED instead of hard-argmin:

        P(a) = exp(-(J_a - J_min)/T) / sum_b exp(-(J_b - J_min)/T)

    over the complete (non-fallback) candidates only.  ``T`` comes from
    ``time_alignment.softmin_temperature_s`` (frozen grid 0.0025/0.010/0.040 s,
    main value 0.010); ``T -> 0`` is the hard greedy limit.  The uniform draw is
    supplied BY THE CALLER (one uniform per decision, from the kernel's
    per-decision private stream) so this function stays pure and deterministic:
    ``sampled = inverse_cdf(probabilities, uniform)``.  A sampled direction is
    placed first and the rest keep the greedy order; the kernel's existing
    legality/loop/queue-room filters are untouched, so a filtered sample falls
    through the same fallback order as every other policy.
``queue_greedy_sticky`` (Round 3, P5 -- smooth decisions, not smooth state)
    The SAME ``J`` as ``queue_greedy`` plus a per ``(satellite, src cell,
    dst cell)`` memory of the last committed forward direction and the instant
    it last CHANGED.  The flow identity is the OD pair ``(src, dst)`` -- this is
    an OD-pair-level flowlet, not a TCP flow.  While
    ``now - last_switch_at < H`` and the remembered direction is still a
    complete (non-fallback) candidate, it is held first; otherwise the greedy
    argmin is used.  ``H`` comes from ``time_alignment.sticky_hold_s`` (frozen
    grid 0.02/0.1/0.5 s, main value 0.1).

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
``softmin_temperature_s`` / ``sticky_hold_s``
    The Round-3 parameter actually used, or ``None`` when the policy does not
    use it.
``per_direction``
    One entry per scored direction with EXACTLY these keys:
    ``j_used_s`` (the J this policy sorted on; geometry included),
    ``resource_work_used_s`` (the queue time term this policy actually
    counted: ``0.0`` for geometry, ``qbar/rate`` for EWMA, the predictor's
    ``resource_work_s`` for greedy/hysteresis/softmin/sticky, ``None`` when the
    direction is a fallback), ``q_used_bits`` (the queue bits that term
    corresponds to; ``None`` for geometry/fallback), ``service_rate_bps`` (the
    rate that term was divided by), ``ewma_samples`` (arrived samples used,
    ``0`` for non-EWMA), ``fallback`` and ``kept`` (``True`` only for the
    direction a keep rule -- hysteresis or sticky -- held).
    The identity ``resource_work_used_s == q_used_bits / service_rate_bps``
    holds whenever both sides are not null, which is what makes the offline
    replay ``J_fresh = j_used_s - resource_work_used_s
    + truth_bits / service_rate_bps`` exact.
``selection``
    ``{"winner": direction|None, "rule": "argmin"|"hysteresis_keep"|
    "softmin_sample"|"sticky_keep", "kept_direction": direction|None}`` plus,
    for ``queue_softmin``: ``sampled``, ``probabilities`` (complete candidates
    only, they sum to 1), ``entropy_nats``, ``entropy_bits``, ``uniform``,
    ``temperature_s``, ``j_min_s`` and ``tie_count``; and for
    ``queue_greedy_sticky``: ``held``, ``held_ms``, ``h_s``, ``flow_key``,
    ``voluntary_switch``, ``forced_switch`` and ``forced_keep_count``.
``rng_key``
    ``queue_softmin`` only: the private stream key the kernel drew the uniform
    from (``softmin:<sat>:<pid>:<decision-ref>``), recorded so a replay can
    reproduce the draw without guessing the stream name.
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
QUEUE_SOFTMIN = "queue_softmin"
QUEUE_GREEDY_STICKY = "queue_greedy_sticky"

POLICIES = (LEGACY, GEOMETRY, QUEUE_GREEDY, QUEUE_GREEDY_HYSTERESIS,
            QUEUE_GREEDY_EWMA, QUEUE_SOFTMIN, QUEUE_GREEDY_STICKY)
QUEUE_POLICIES = (QUEUE_GREEDY, QUEUE_GREEDY_HYSTERESIS, QUEUE_GREEDY_EWMA,
                  QUEUE_SOFTMIN, QUEUE_GREEDY_STICKY)

#: Pre-registered grid (POLICY-SPEC-20261031 §4).  ``eta`` may only take a
#: value from this grid; the main experiment is frozen at 0.005 s and other
#: values are sensitivity runs only.
HYSTERESIS_ETA_GRID_S = (0.0, 0.002, 0.005, 0.010)
#: Pre-registered grid (POLICY-SPEC-20261031 §5).  Main value 0.25.
EWMA_ALPHA_GRID = (0.1, 0.25, 0.5)
#: Round-3 P4 pre-registered grid (seconds).  Main value 0.010.
SOFTMIN_TEMPERATURE_GRID_S = (0.0025, 0.010, 0.040)
SOFTMIN_TEMPERATURE_DEFAULT_S = 0.010
#: Round-3 P5 pre-registered grid (seconds).  Main value 0.1.
STICKY_HOLD_GRID_S = (0.02, 0.1, 0.5)
STICKY_HOLD_DEFAULT_S = 0.1

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
_SELECTION_SOFTMIN_SAMPLE = "softmin_sample"
_SELECTION_STICKY_KEEP = "sticky_keep"


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


def validate_temperature_s(value, frozen_grid: bool = True) -> float:
    """``softmin_temperature_s``: finite, >= 0 (RUN grid 0.0025/0.010/0.040).

    ``T = 0`` is the mathematical hard-argmin limit and is accepted by the pure
    model only (``frozen_grid=False``); a RUN may not register it, because the
    registered cells are 0.0025/0.010/0.040 s.
    """
    temperature = _finite("softmin_temperature_s", value)
    if temperature < 0.0:
        raise StatePolicyError(
            f"softmin_temperature_s must be >= 0, got {value!r}")
    if frozen_grid and not _on_grid(temperature, SOFTMIN_TEMPERATURE_GRID_S):
        raise StatePolicyError(
            "softmin_temperature_s must be one of the pre-registered grid "
            f"{list(SOFTMIN_TEMPERATURE_GRID_S)} s, got {value!r}; the value "
            "is frozen before the experiment and may not be chosen afterwards")
    return temperature


def validate_sticky_hold_s(value, frozen_grid: bool = True) -> float:
    """``sticky_hold_s``: finite, > 0 (RUN grid 0.02/0.1/0.5 s)."""
    hold = _finite("sticky_hold_s", value)
    if hold <= 0.0:
        raise StatePolicyError(f"sticky_hold_s must be > 0, got {value!r}")
    if frozen_grid and not _on_grid(hold, STICKY_HOLD_GRID_S):
        raise StatePolicyError(
            "sticky_hold_s must be one of the pre-registered grid "
            f"{list(STICKY_HOLD_GRID_S)} s, got {value!r}; the value is "
            "frozen before the experiment and may not be chosen afterwards")
    return hold


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
# Round-3 P4: softmin distribution (pure, caller supplies the uniform)
# --------------------------------------------------------------------------
def softmin_distribution(j_by_direction, index, temperature_s) -> tuple:
    """``({direction: p}, j_min, ranked, tie_count)`` over COMPLETE candidates.

    ``P(a) = exp(-(J_a - J_min)/T) / sum_b exp(-(J_b - J_min)/T)``.

    Fallbacks (``J is None``) are EXCLUDED from the distribution: a candidate
    whose cost is not known may not receive invented probability mass.  The
    returned ``probabilities`` therefore sum to 1 over the complete candidates.

    ``T = 0`` is defined as EXACTLY the hard greedy choice (the scorer's
    tie-broken argmin gets probability 1.0), not as the ``T -> 0`` limit
    distribution: that keeps the degenerate case identical to
    ``queue_greedy``.  For ``T > 0`` the limit as ``T -> 0`` is uniform over the
    argmin set, and an exponential underflow is clamped to 0 weight (which is
    that same limit, not a NaN).
    """
    temperature_s = validate_temperature_s(temperature_s, frozen_grid=False)
    ranked = []
    for direction, j in j_by_direction.items():
        if j is None:
            continue
        ranked.append((float(j), index.get(direction, 0), str(direction)))
    ranked.sort()
    if not ranked:
        return {}, None, [], 0
    j_min = ranked[0][0]
    tie_count = sum(1 for j, _index, _direction in ranked if j == j_min)
    if temperature_s <= 0.0:
        # hard greedy: the first (J, visible order, name) candidate wins
        winner = ranked[0][2]
        return ({direction: (1.0 if direction == winner else 0.0)
                 for _j, _i, direction in ranked}, j_min, ranked, tie_count)
    weights = {}
    for j, _index, direction in ranked:
        exponent = -(j - j_min) / temperature_s
        weights[direction] = math.exp(exponent) if exponent > -745.0 else 0.0
    total = sum(weights.values())
    if not math.isfinite(total) or total <= 0.0:
        # unreachable for finite inputs; never invent a distribution
        weights = {direction: (1.0 if j == j_min else 0.0)
                   for j, _index, direction in ranked}
        total = sum(weights.values())
    return ({d: w / total for d, w in weights.items()}, j_min, ranked,
            tie_count)


def inverse_cdf_sample(ranked, probabilities, uniform) -> str:
    """Deterministic inverse-CDF draw over ``ranked`` (sorted by J, idx, name)."""
    uniform = _finite("softmin_uniform", uniform)
    if not 0.0 <= uniform < 1.0:
        raise StatePolicyError(
            f"softmin_uniform must be in [0, 1), got {uniform!r}")
    cumulative = 0.0
    last = None
    for _j, _index, direction in ranked:
        last = direction
        cumulative += float(probabilities.get(direction, 0.0))
        if uniform < cumulative:
            return direction
    if last is None:
        raise StatePolicyError("inverse_cdf_sample got no complete candidate")
    return last


def distribution_entropy_nats(probabilities) -> float:
    """Shannon entropy of the sampled distribution, in nats."""
    return -sum(float(p) * math.log(float(p))
                for p in probabilities.values() if float(p) > 0.0)


# --------------------------------------------------------------------------
# the ONE entry point
# --------------------------------------------------------------------------

def rank(scored, history_by_resource=None, snapshot_at=None, policy=LEGACY,
         eta_s=0.0, alpha=0.25, last_direction=None, visible_order=None,
         tie_sig_digits=None,
         softmin_temperature_s=SOFTMIN_TEMPERATURE_DEFAULT_S,
         softmin_uniform=None,
         sticky_hold_s=STICKY_HOLD_DEFAULT_S, sticky_state=None,
         flow_key=None) -> tuple:
    """Order one decision's already-scored candidates under one policy.

    Parameters
    ----------
    scored : time_alignment.ScoredCandidates
        ``ranking``/``scores`` as produced by ``score_candidates``.  Untouched.
    history_by_resource : mapping or sequence, optional
        The RECEIVED advertisement samples (``order_history`` output grouped by
        resource).  Only ``queue_greedy_ewma`` reads it.
    snapshot_at : float
        The frozen decision instant; samples received after it never enter, and
        it is the ``now`` the Round-3 sticky hold compares its memory against.
    policy : str
        One of ``POLICIES``.
    eta_s, alpha, softmin_temperature_s, sticky_hold_s : float
        Frozen policy parameters (validated here against the mathematical
        domain; the pre-registered grids are the RUN rule enforced by
        ``config.py`` / ``Kernel.__init__``).
    last_direction : str or None
        The direction the last committed FORWARD action used for this
        ``(satellite, destination)``; ``None`` when nothing was committed yet.
        Used by ``queue_greedy_hysteresis``.
    visible_order : sequence of str, optional
        The snapshot's legal-direction order (the scorer's tie-break order).
    tie_sig_digits : int or None
        The scorer's diagnostics-only quantisation, applied to the policy J so
        a tie experiment means the same thing on both sides.
    softmin_uniform : float or None
        One uniform draw in ``[0, 1)`` from the CALLER's private per-decision
        stream.  ``queue_softmin`` requires it (a missing draw is an error: the
        function never reaches for a global RNG).
    sticky_state : (direction, switched_at) or None
        The committed memory of the ``(satellite, src, dst)`` flowlet, as of
        ``snapshot_at``; ``None`` means "nothing committed for this flow yet".
    flow_key : sequence or None
        The memory key recorded in the audit (satellite + OD-pair identity).

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
    # definition stays testable (eta=0, alpha=1, T=0) without weakening a run.
    eta_s = validate_eta_s(eta_s, frozen_grid=False)
    alpha = validate_alpha(alpha, frozen_grid=False)
    softmin_temperature_s = validate_temperature_s(
        softmin_temperature_s, frozen_grid=False)
    sticky_hold_s = validate_sticky_hold_s(sticky_hold_s, frozen_grid=False)
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
    else:  # legacy / greedy / hysteresis / softmin / sticky: the greedy J
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
    # Round-3 audit payloads (only the policy that uses them fills them).
    softmin_audit = None
    sticky_audit = None

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
    elif policy == QUEUE_SOFTMIN:
        if softmin_uniform is None:
            raise StatePolicyError(
                "queue_softmin requires softmin_uniform: the caller must draw "
                "one uniform from its own per-decision stream (no global RNG)")
        probabilities, j_min, ranked_known, tie_count = softmin_distribution(
            j_by_direction, index, softmin_temperature_s)
        greedy_best, j_best = _min_j(j_by_direction, index)
        uniform = _finite("softmin_uniform", softmin_uniform)
        if not 0.0 <= uniform < 1.0:
            raise StatePolicyError(
                f"softmin_uniform must be in [0, 1), got {uniform!r}")
        if ranked_known:
            sampled = inverse_cdf_sample(ranked_known, probabilities, uniform)
            rule = _SELECTION_SOFTMIN_SAMPLE
            best_direction = sampled
            order = [sampled] + [d for d in ranking if d != sampled]
            entropy = distribution_entropy_nats(probabilities)
            reason = (
                "sampled from P(a) proportional to exp(-(J_a - J_min)/T) over "
                "the %d complete candidate(s); the greedy argmin is %r and "
                "T=%.6g s, uniform=%.12g"
                % (len(ranked_known), greedy_best,
                   softmin_temperature_s, uniform))
        else:
            sampled = None
            order = list(ranking)
            entropy = 0.0
            reason = ("no complete candidate: no distribution to sample; the "
                      "scorer order is kept")
        softmin_audit = {
            "temperature_s": float(softmin_temperature_s),
            "uniform": uniform,
            "sampled": sampled,
            "greedy_argmin": greedy_best,
            "j_min_s": j_min,
            "tie_count": int(tie_count),
            "probabilities": dict(probabilities),
            "entropy_nats": float(entropy),
            "entropy_bits": (float(entropy) / math.log(2.0)
                             if entropy else 0.0),
        }
    elif policy == QUEUE_GREEDY_STICKY:
        greedy_best, j_best = _min_j(j_by_direction, index)
        order = list(ranking)
        best_direction = greedy_best
        held = False
        held_ms = None
        last_complete = False
        previous_direction = None
        if sticky_state is not None:
            if snapshot_at is None:
                raise StatePolicyError(
                    "queue_greedy_sticky requires snapshot_at: the hold window "
                    "cannot be evaluated without the decision instant")
            previous_direction = str(sticky_state[0])
            switched_at = _finite("sticky_state_switched_at", sticky_state[1])
            held_ms = (float(snapshot_at) - switched_at) * 1000.0
            held = (float(snapshot_at) - switched_at) < sticky_hold_s
            last_complete = j_by_direction.get(previous_direction) is not None
            if held and last_complete:
                kept_direction = previous_direction
                kept_by_direction[kept_direction] = True
                rule = _SELECTION_STICKY_KEEP
                best_direction = kept_direction
                order = [kept_direction] + [
                    d for d in order if d != kept_direction]
                reason = ("flowlet held: switched %.3f ms ago < H %.1f ms and "
                          "%r is still a complete candidate"
                          % (held_ms, sticky_hold_s * 1000.0,
                             previous_direction))
            elif held and not last_complete:
                reason = ("flowlet memory exists (%.3f ms ago, inside H) but "
                          "%r is no longer a complete candidate: forced "
                          "switch to the greedy argmin"
                          % (held_ms, previous_direction))
            else:
                reason = ("flowlet hold expired (%.3f ms >= H %.1f ms): "
                          "greedy argmin"
                          % (held_ms, sticky_hold_s * 1000.0))
        else:
            reason = "no committed forward direction for this flowlet yet"
        greedy_switch = (previous_direction is not None
                         and greedy_best is not None
                         and greedy_best != previous_direction)
        sticky_audit = {
            "hold_s": float(sticky_hold_s),
            "held": bool(held),
            "held_ms": (None if held_ms is None else float(held_ms)),
            "flow_key": (None if flow_key is None else
                         {"satellite": int(flow_key[0]),
                          "src": str(flow_key[1]),
                          "dst": str(flow_key[2])}
                         if len(flow_key) == 3
                         else [str(part) for part in flow_key]),
            "previous_direction": previous_direction,
            "greedy_argmin": greedy_best,
            "voluntary_switch": bool(rule == _SELECTION_ARGMIN
                                     and last_complete and greedy_switch),
            "forced_switch": bool(rule == _SELECTION_ARGMIN
                                  and previous_direction is not None
                                  and not last_complete),
            "forced_keep_count": int(
                1 if rule == _SELECTION_STICKY_KEEP and greedy_switch else 0),
        }
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

    selection = {
        "winner": best_direction,
        "rule": rule,
        "kept_direction": kept_direction,
    }
    if softmin_audit is not None:
        selection.update(softmin_audit)
    if sticky_audit is not None:
        selection.update(sticky_audit)

    audit = {
        "schema": SCHEMA,
        "policy": policy,
        "hysteresis_eta_s": (float(eta_s) if policy == QUEUE_GREEDY_HYSTERESIS
                              else None),
        "ewma_alpha": (float(alpha) if policy == QUEUE_GREEDY_EWMA else None),
        "softmin_temperature_s": (
            float(softmin_temperature_s) if policy == QUEUE_SOFTMIN else None),
        "sticky_hold_s": (float(sticky_hold_s)
                          if policy == QUEUE_GREEDY_STICKY else None),
        "last_direction": (None if last_direction is None
                           else str(last_direction)),
        "snapshot_at": (None if snapshot_at is None
                        else float(snapshot_at)),
        "per_direction": per_direction,
        "selection": selection,
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
    if policy in (QUEUE_SOFTMIN, QUEUE_GREEDY_STICKY):
        # The kernel records these AFTER the unchanged legality pipeline runs
        # (a policy only orders candidates; it may never commit a direction the
        # existing filters rejected).  Declared here as None so the block schema
        # is stable for every decision of the Round-3 policies, including the
        # ones that end in hold/fail and therefore never commit anything.
        audit["override"] = None
        audit["committed_direction"] = None
    return list(order), audit
