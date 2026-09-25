"""Fold decision snapshots and timeline milestones into per-decision ledgers.

Output-only post-processing for T1 (stale-neighbour-state / candidate-arrival
alignment).  It reads the two optional sinks produced by kernel.Kernel
(decision_sink rows and timeline_sink milestones) and rebuilds, for every
decision, the time chain the T1 measurement contract requires.  Nothing here
influences simulation behaviour: it is a pure function over recorded rows.

See ANALYSIS/T1-MEASUREMENT-PROTOCOL.md for the gate this serves
(T1-TIME-LEDGER-PASS).

Missing values are reported as MISSING rather than 0 so that "the milestone
never happened" can never be confused with "it happened at t=0".

T1-FROZEN-LEDGER.  A frozen decision and a refresh decision carry two
different meanings inside the same row, so every folded decision keeps four
SEPARATE objects (see build_ledger):

* observation_at_start -- what the decision was actually based on, with the
  measurement time of each contributing neighbour.  Frozen: the snapshot
  taken at t_decision_start.  Refresh: the state re-read at t_decision_commit.
  The mode label, never the numbers, is what tells the two apart.
* estimate_at_start   -- the only prediction that observation legally
  supported.  None when it supported none; never backfilled from the
  commit-time truth.
* truth_at_commit     -- the kernel's own truth at commit (the pre-existing
  info_audit content).  This is hindsight, not a deployable prediction:
  score_downstream_predictions scores THIS and must not be read as the
  quality of the decision-time belief.  score_start_estimates scores the t0
  legal estimate instead.
* truth_at_target     -- the realized truth of the resource the packet really
  contended for, folded from the arrival snapshot.  MISSING (with a reason)
  while the target instant has not happened.

t_measure is what the action was actually based on, so it branches on the
recorded mode: frozen -> t_decision_start, refresh -> t_decision_commit.
t_measure is a per-decision instant and says NOTHING about when any single
neighbour was measured; use observation_at_start.neighbours for that.
"""
from __future__ import annotations

MISSING = "missing"

#: The eleven fields required by the T1 time-ledger gate, in lifecycle order.
TIMELINE_FIELDS = (
    "t_measure",
    "t_control_rx",
    "t_decision_start",
    "t_decision_commit",
    "t_local_queue_enter",
    "t_service_start",
    "t_service_finish",
    "t_peer_arrival",
    "t_peer_redecision",
    "t_peer_target_egress_enter",
    "t_peer_target_egress_service_start",
)

#: Decision-row key naming the observation semantics the row was produced
#: under.  Rows written before the key existed can only be the historical
#: refresh schema, so that is the documented default -- an explicit branch,
#: never an inference from the numbers.
DEFAULT_OBS_MODE = "refresh"

#: The frozen top-level key contract of one decision row.
#:
#: The kernel appends decision rows from a single unconditional dict literal
#: (kernel.py, the decision_sink.append call in the decision path), so this
#: key set is fixed by construction rather than by convention -- there are no
#: conditional fields.  It is what a formal run must satisfy for its decision
#: stream to be attachable to an authorized run: without a row contract the
#: stream is diagnostic-only, which is precisely why
#: ANALYSIS/T1-MEASUREMENT-PROTOCOL.md section 3.3 keeps it outside the
#: receipt/ledger trust chain and why a formal run used to refuse it.
DECISION_ROW_KEYS = frozenset({
    "t", "t_decision_start", "decision_id", "state_version", "pid", "src",
    "dst", "sat", "kind", "policy", "candidates", "chosen", "own_queue_bits",
    "obs", "info_audit", "obs_mode", "observation_at_start",
    "estimate_at_start", "truth_at_commit",
})

#: Stream-contract identifier a V6 receipt binds the decision log under.
DECISION_STREAM_CONTRACT = "decision-rows/v1"


class DecisionRowError(ValueError):
    """A decision row does not satisfy the frozen row contract."""


def validate_decision_row(row) -> None:
    """Refuse a row that is not exactly the frozen contract.

    Fail loud in BOTH directions.  An unknown key means the writer changed
    without this contract being updated; a missing key means the row cannot
    carry the evidence the contract promises.  Neither is silently dropped --
    a contract that checks only one direction lets the other drift.
    """
    if not isinstance(row, dict):
        raise DecisionRowError(
            f"decision row is not a JSON object: {type(row).__name__}")
    keys = set(row)
    unknown = sorted(keys - DECISION_ROW_KEYS)
    if unknown:
        raise DecisionRowError(
            "decision row carries keys outside the frozen contract: "
            f"{unknown}")
    missing = sorted(DECISION_ROW_KEYS - keys)
    if missing:
        raise DecisionRowError(
            f"decision row is missing contract keys: {missing}")


#: Milestones that end a decision ATTEMPT without committing it, most
#: authoritative first: a rejected commit also parks the packet, so both
#: commit_rejected and hold are recorded and the rejection is the outcome.
ATTEMPT_VERDICT_PRECEDENCE = ("fail", "commit_rejected",
                              "frozen_inferred_hold", "hold")

_VERDICT_OUTCOME = {
    "fail": "failed",
    "commit_rejected": "rejected",
    "frozen_inferred_hold": "held",
    "hold": "held",
}


def _first_at(milestones, milestone, decision_id):
    rows = milestones.get((milestone, decision_id))
    return float(rows[0]["at"]) if rows else MISSING


def _freshest_control_rx(row):
    """Newest control-cache arrival that could have informed this decision.

    The audit blob carries one entry per contributing origin, so a single
    scalar would be lossy.  The freshest value is reported here (the best the
    decision could have known) while the full per-origin detail stays in the
    decision row's info_audit.cache_entries.  Returns MISSING when the contract
    contributed no entry at all (for example a non-learning run).
    """
    audit = row.get("info_audit") or {}
    entries = audit.get("cache_entries") or {}
    arrivals = [float(e["received_at"]) for e in entries.values()
                if e.get("received_at") is not None]
    return max(arrivals) if arrivals else MISSING


def obs_mode_of(row):
    """The observation semantics a recorded row or milestone was produced
    under: "frozen" or "refresh".  An unknown mode fails loud rather than
    being folded as if it were one of the two known ones."""
    mode = row.get("obs_mode", DEFAULT_OBS_MODE)
    if mode not in ("refresh", "frozen"):
        raise ValueError(
            "unknown obs_mode %r on record %r" % (mode, row.get("decision_id")))
    return mode


def measure_instant(mode, decision_start, decision_commit):
    """The instant a decision's information was actually taken, per mode.

    frozen: the observation and the inference happen BEFORE the compute
    interval, so the action is based on t_decision_start.  refresh: the
    deferred decision re-reads the live state when the computation lands, so
    the information instant is t_decision_commit.  With a zero compute delay
    the two instants coincide and the distinction is inert.
    """
    return decision_start if mode == "frozen" else decision_commit


def recorded(row, key):
    """One recorded object, or MISSING when the row never carried the key.

    MISSING and None are different facts and are kept apart on purpose: None
    is a value this ledger records ("no legal prediction existed at t0"),
    MISSING is "this record predates the field".
    """
    return row[key] if key in row else MISSING


def truth_at_target(arrival_row, contended_direction):
    """The realized truth of the resource the packet REALLY contended for.

    arrival_row is the peer_arrival milestone carrying the egress snapshot
    taken the instant the packet reached the neighbour (None when no such
    arrival was recorded).  contended_direction is the egress the neighbour
    then really chose.  Nothing is defaulted to zero and nothing is copied
    from the commit-time truth: a target instant that has not happened yet is
    MISSING with the reason why.
    """
    out = {
        "schema": "leo-sim-truth-at-target/v1",
        "status": "ok",
        "reason": None,
        "source": "peer_arrival_egress_snapshot",
        "t_arrival": MISSING,
        "contended_direction": (None if contended_direction is None
                                else contended_direction),
        "egress": None,
    }
    if arrival_row is None:
        out["status"] = MISSING
        out["reason"] = "no_arrival_recorded"
        return out
    out["t_arrival"] = float(arrival_row["at"])
    snapshot = arrival_row.get("egress_snapshot")
    if snapshot is None:
        out["status"] = MISSING
        out["reason"] = "arrival_carries_no_egress_snapshot"
        return out
    if contended_direction is None:
        out["status"] = MISSING
        out["reason"] = "contended_direction_unresolved"
        return out
    if contended_direction == "deliver":
        out["status"] = MISSING
        out["reason"] = "peer_delivered_the_packet"
        return out
    slot = snapshot.get(contended_direction)
    if slot is None:
        out["status"] = MISSING
        out["reason"] = "contended_direction_absent_from_snapshot"
        return out
    out["egress"] = dict(slot)
    return out


def build_ledger(decision_rows, timeline_rows):
    """Rebuild the per-decision time ledger.

    decision_rows are the rows appended to Kernel.decision_sink; timeline_rows
    are the milestones appended to Kernel.timeline_sink.  Returns
    (by_decision, diagnostics): by_decision maps decision_id to a dict holding
    every TIMELINE_FIELDS key plus pid, sat, kind, chosen, obs_mode and the
    four separated objects (observation_at_start, estimate_at_start,
    truth_at_commit, truth_at_target).
    """
    milestones = {}
    redecision_of = {}
    arrivals = {}
    for row in timeline_rows:
        decision_id = row.get("decision_id")
        if row["milestone"] == "peer_arrival" \
                and row.get("egress_snapshot") is not None:
            arrivals.setdefault(decision_id, row)
        if decision_id is None:
            continue
        milestones.setdefault((row["milestone"], decision_id), []).append(row)
        if row["milestone"] == "redecision":
            previous = row.get("prev_decision_id")
            if previous is not None and previous not in redecision_of:
                redecision_of[previous] = decision_id

    by_decision = {}
    duplicates = []
    for row in decision_rows:
        decision_id = row.get("decision_id")
        if decision_id is None:
            continue
        if decision_id in by_decision:
            duplicates.append(decision_id)
            continue
        committed = float(row["t"])
        started = float(row.get("t_decision_start", committed))
        mode = obs_mode_of(row)
        # The three instants are kept as distinct fields on purpose.  With the
        # default zero compute delay they coincide.  With a non-zero delay the
        # two modes disagree about what the action was based on, and that is a
        # property of the MODE, not of the numbers: frozen decided from the
        # observation taken at t_decision_start, refresh from the state
        # re-read at t_decision_commit.
        by_decision[decision_id] = {
            "pid": row.get("pid"),
            "sat": row.get("sat"),
            "kind": row.get("kind"),
            "chosen": row.get("chosen"),
            "obs_mode": mode,
            "t_measure": measure_instant(mode, started, committed),
            "t_control_rx": _freshest_control_rx(row),
            "t_decision_start": started,
            "t_decision_commit": committed,
            "t_local_queue_enter": _first_at(milestones, "queue_enter",
                                             decision_id),
            "t_service_start": _first_at(milestones, "service_start",
                                         decision_id),
            "t_service_finish": _first_at(milestones, "service_finish",
                                          decision_id),
            "t_peer_arrival": _first_at(milestones, "peer_arrival",
                                        decision_id),
            "t_peer_redecision": MISSING,
            "t_peer_target_egress_enter": MISSING,
            "t_peer_target_egress_service_start": MISSING,
            # T1-FROZEN-LEDGER: four separated objects.  MISSING on a record
            # that predates the field, None for "recorded, no prediction".
            "observation_at_start": recorded(row, "observation_at_start"),
            "estimate_at_start": recorded(row, "estimate_at_start"),
            "truth_at_commit": recorded(row, "truth_at_commit"),
            "truth_at_target": MISSING,
            "_successor": redecision_of.get(decision_id),
        }

    # Second pass: from the predecessor's point of view, the successor's local
    # enqueue and service start ARE the peer-side target egress milestones;
    # the realized egress truth is the arrival snapshot of that same hop.
    for decision_id, entry in by_decision.items():
        successor = entry.pop("_successor", None)
        nxt = by_decision.get(successor) if successor is not None else None
        if nxt is not None:
            entry["t_peer_redecision"] = float(nxt["t_measure"])
            entry["t_peer_target_egress_enter"] = nxt["t_local_queue_enter"]
            entry["t_peer_target_egress_service_start"] = nxt["t_service_start"]
        contended = nxt["chosen"] if nxt is not None else None
        entry["truth_at_target"] = truth_at_target(
            arrivals.get(decision_id), contended)

    diagnostics = {
        "decisions": len(by_decision),
        "duplicate_decision_ids": sorted(duplicates),
        "orphan_redecisions": sorted(previous for previous in redecision_of
                                     if previous not in by_decision),
        "milestones": len(timeline_rows),
    }
    return by_decision, diagnostics


def _observed_at(record, fallback):
    """The instant an attempt was observed at, from its own record."""
    if isinstance(record, dict) and record.get("t_observed") is not None:
        return float(record["t_observed"])
    return fallback


def build_attempts(decision_rows, timeline_rows):
    """Enumerate EVERY decision attempt, committed or not.

    A decision id that committed appears in decision_rows.  One that was
    rejected, held or failed appears ONLY on the timeline, so a ledger built
    from decision_rows alone silently drops exactly the attempts T1 must
    count: the packet paid for a computation and got nothing, and a
    success-only ledger would report that cost as zero.  Frozen rejections and
    holds additionally carry the observation they were inferred from and the
    reason they died.

    Returns (attempts, diagnostics); attempts is ordered by decision_id.
    """
    rows = {}
    for row in decision_rows:
        decision_id = row.get("decision_id")
        if decision_id is not None and decision_id not in rows:
            rows[decision_id] = row
    verdicts = {}
    prev_of = {}
    for row in timeline_rows:
        decision_id = row.get("decision_id")
        if decision_id is None:
            continue
        if row["milestone"] == "redecision":
            previous = row.get("prev_decision_id")
            if previous is not None and decision_id not in prev_of:
                prev_of[decision_id] = previous
            continue
        try:
            rank = ATTEMPT_VERDICT_PRECEDENCE.index(row["milestone"])
        except (KeyError, ValueError):
            continue
        if decision_id not in verdicts or rank < verdicts[decision_id][0]:
            verdicts[decision_id] = (rank, row)

    attempts = []
    for decision_id in sorted(set(rows) | set(verdicts)):
        row = rows.get(decision_id)
        verdict = verdicts[decision_id][1] if decision_id in verdicts else None
        if row is not None:
            outcome, reason = "committed", None
            mode = obs_mode_of(row)
            observation = recorded(row, "observation_at_start")
            estimate = recorded(row, "estimate_at_start")
            t_outcome = float(row["t"])
            t_observed = _observed_at(
                observation,
                measure_instant(mode, float(row.get("t_decision_start",
                                                    row["t"])),
                                float(row["t"])))
            kind, chosen = row.get("kind"), row.get("chosen")
            sat, inferred = row.get("sat"), None
            truth_commit = recorded(row, "truth_at_commit")
        else:
            outcome = _VERDICT_OUTCOME[verdict["milestone"]]
            reason = (verdict.get("reason") or verdict.get("fate")
                      or "attempt_ended_without_a_commit")
            mode = obs_mode_of(verdict)
            observation = verdict.get("observation_at_start", MISSING)
            estimate = verdict.get("estimate_at_start", MISSING)
            t_outcome = float(verdict["at"])
            t_observed = _observed_at(
                observation,
                verdict.get("t_observed", verdict.get("inferred_at", MISSING)))
            kind = (observation.get("kind")
                    if isinstance(observation, dict) else None)
            chosen, sat = None, verdict.get("sat")
            inferred = verdict.get("action")
            truth_commit = MISSING
        attempts.append({
            "decision_id": decision_id,
            "pid": (row if row is not None else verdict).get("pid"),
            "sat": sat,
            "mode": mode,
            "outcome": outcome,
            "reason": reason,
            "kind": kind,
            "chosen": chosen,
            "inferred_action": inferred,
            # a re-decision is an attempt that restarted from another
            # attempt: the link keeps the chain readable instead of leaving
            # two unrelated ids in the stream
            "prev_decision_id": prev_of.get(decision_id),
            "t_observed": t_observed,
            "t_outcome": t_outcome,
            "observation_at_start": observation,
            "estimate_at_start": estimate,
            "truth_at_commit": truth_commit,
            "has_observation": isinstance(observation, dict),
        })

    outcomes = {}
    for attempt in attempts:
        outcomes[attempt["outcome"]] = outcomes.get(attempt["outcome"], 0) + 1
    diagnostics = {
        "attempts": len(attempts),
        "by_outcome": outcomes,
        "with_observation": sum(1 for a in attempts if a["has_observation"]),
        "without_observation": sorted(a["decision_id"] for a in attempts
                                      if not a["has_observation"]),
        "by_mode": {mode: sum(1 for a in attempts if a["mode"] == mode)
                    for mode in sorted({a["mode"] for a in attempts})},
    }
    return attempts, diagnostics


def score_downstream_predictions(decision_rows, timeline_rows):
    """Score each forward decision's downstream belief against reality.

    Reads info_audit.candidate_truth[chosen].downstream, which the kernel
    computes at COMMIT time from the neighbour's real queues and the
    neighbour's own cache.  That is hindsight truth, not a prediction a
    deployment could have had at the observation instant: it is an upper bound
    on predictive accuracy and must never be quoted as the stale-neighbour
    misalignment of a t0 belief.  score_start_estimates is the deployable
    counterpart and is what a T1 claim about decision-time information uses.

    Pairs the belief with the egress snapshot taken when the packet ACTUALLY
    arrived at that neighbour, plus the direction the neighbour then really
    chose.  Returns (scores, summary); scores maps decision_id to a dict.
    """
    arrivals = {}
    successor = {}
    for row in timeline_rows:
        if row.get("egress_snapshot") is not None:
            arrivals[row.get("decision_id")] = row
        if row["milestone"] == "redecision":
            previous = row.get("prev_decision_id")
            if previous is not None and previous not in successor:
                successor[previous] = row.get("decision_id")

    by_id = {}
    for row in decision_rows:
        if row.get("decision_id") is not None:
            by_id[row["decision_id"]] = row

    scores = {}
    for decision_id, row in sorted(by_id.items()):
        if row.get("kind") != "forward":
            continue
        truth = (row.get("info_audit") or {}).get("candidate_truth") or {}
        entry = truth.get(row.get("chosen"))
        prediction = (entry or {}).get("downstream")
        arrival = arrivals.get(decision_id)
        succ = successor.get(decision_id)
        realized_direction = None
        if succ is not None and succ in by_id:
            realized_direction = by_id[succ].get("chosen")

        predicted_direction = None
        predicted_bits = None
        predicted_remaining = None
        if prediction is not None:
            predicted_direction = prediction.get("peer_egress_direction")
            predicted_bits = (prediction.get("peer_egress_data_bits", 0)
                              + prediction.get("peer_egress_ctrl_bits", 0))
            predicted_remaining = prediction.get("peer_in_service_remaining_bits")

        realized_bits = None
        realized_remaining = None
        realized_actual_bits = None
        if arrival is not None:
            snapshot = arrival.get("egress_snapshot") or {}
            if predicted_direction is not None:
                slot = snapshot.get(predicted_direction)
                if slot is not None:
                    realized_bits = slot["data_bits"] + slot["ctrl_bits"]
                    realized_remaining = slot["in_service_remaining_bits"]
            if realized_direction is not None:
                slot = snapshot.get(realized_direction)
                if slot is not None:
                    realized_actual_bits = slot["data_bits"] + slot["ctrl_bits"]

        match = None
        if predicted_direction is not None and realized_direction is not None:
            match = predicted_direction == realized_direction

        scores[decision_id] = {
            "pid": row.get("pid"),
            "sat": row.get("sat"),
            "source": "info_audit_truth_at_commit",
            "predicted_egress": predicted_direction,
            "realized_egress": realized_direction,
            "egress_match": match,
            "arrival_recorded": arrival is not None,
            "predicted_egress_bits": predicted_bits,
            "realized_egress_bits": realized_bits,
            "egress_bits_delta": (None if (predicted_bits is None
                                           or realized_bits is None)
                                  else realized_bits - predicted_bits),
            "realized_actual_egress_bits": realized_actual_bits,
            "predicted_remaining_bits": predicted_remaining,
            "realized_remaining_bits": realized_remaining,
            "remaining_delta": (None if (predicted_remaining is None
                                         or realized_remaining is None)
                                else realized_remaining - predicted_remaining),
        }

    compared = [s for s in scores.values() if s["egress_match"] is not None]
    deltas = [s["egress_bits_delta"] for s in scores.values()
              if s["egress_bits_delta"] is not None]
    summary = {
        "scored_decisions": len(scores),
        "with_prediction": sum(1 for s in scores.values()
                               if s["predicted_egress"] is not None),
        "with_arrival_snapshot": sum(1 for s in scores.values()
                                     if s["arrival_recorded"]),
        "egress_compared": len(compared),
        "egress_matched": sum(1 for s in compared if s["egress_match"]),
        "egress_match_rate": ((sum(1 for s in compared if s["egress_match"])
                               / len(compared)) if compared else None),
        "egress_bits_delta_samples": len(deltas),
        "egress_bits_delta_mean": (sum(deltas) / len(deltas)) if deltas else None,
        "egress_bits_delta_max": max(deltas) if deltas else None,
        "source": "info_audit_truth_at_commit",
    }
    return scores, summary


def score_start_estimates(decision_rows, timeline_rows):
    """Score the t0-LEGAL estimate against what the packet really met.

    Uses estimate_at_start -- built only from information the node had at its
    own observation instant -- and pairs it with the arrival snapshot taken
    when the packet really reached the neighbour.  This is the deployable
    prediction score: the gap between it and score_downstream_predictions is
    exactly how much of the "prediction quality" of the truth audit came from
    hindsight.  Returns (scores, summary).
    """
    arrivals = {}
    successor = {}
    for row in timeline_rows:
        if row.get("milestone") == "peer_arrival" \
                and row.get("egress_snapshot") is not None:
            arrivals.setdefault(row.get("decision_id"), row)
        if row.get("milestone") == "redecision":
            previous = row.get("prev_decision_id")
            if previous is not None and previous not in successor:
                successor[previous] = row.get("decision_id")

    by_id = {}
    for row in decision_rows:
        if row.get("decision_id") is not None:
            by_id[row["decision_id"]] = row

    scores = {}
    for decision_id, row in sorted(by_id.items()):
        if row.get("kind") != "forward":
            continue
        estimate = row.get("estimate_at_start")
        estimate = estimate if isinstance(estimate, dict) else None
        predicted_direction = None
        predicted_bits = None
        information_source = None
        if estimate is not None:
            predicted_direction = estimate.get("peer_egress_direction")
            predicted_bits = estimate.get("peer_egress_queue_bits_estimate")
            information_source = estimate.get("information_source")
        arrival = arrivals.get(decision_id)
        snapshot = (arrival or {}).get("egress_snapshot") or {}
        succ = successor.get(decision_id)
        realized_direction = None
        if succ is not None and succ in by_id:
            realized_direction = by_id[succ].get("chosen")
        realized_bits = None
        if predicted_direction is not None and predicted_direction in snapshot:
            slot = snapshot[predicted_direction]
            realized_bits = slot["data_bits"] + slot["ctrl_bits"]
        realized_actual_bits = None
        if realized_direction in snapshot:
            slot = snapshot[realized_direction]
            realized_actual_bits = slot["data_bits"] + slot["ctrl_bits"]
        match = None
        if predicted_direction is not None and realized_direction is not None:
            match = predicted_direction == realized_direction
        scores[decision_id] = {
            "pid": row.get("pid"),
            "sat": row.get("sat"),
            "source": "estimate_at_start",
            "truth_used": False,
            "estimate_available": estimate is not None,
            "information_source": information_source,
            "predicted_egress": predicted_direction,
            "realized_egress": realized_direction,
            "egress_match": match,
            "arrival_recorded": arrival is not None,
            "predicted_egress_bits": predicted_bits,
            "realized_egress_bits": realized_bits,
            "egress_bits_delta": (None if (predicted_bits is None
                                           or realized_bits is None)
                                  else realized_bits - predicted_bits),
            "realized_actual_egress_bits": realized_actual_bits,
        }

    compared = [s for s in scores.values() if s["egress_match"] is not None]
    deltas = [s["egress_bits_delta"] for s in scores.values()
              if s["egress_bits_delta"] is not None]
    sources = {}
    for score in scores.values():
        key = score["information_source"] or "no_estimate"
        sources[key] = sources.get(key, 0) + 1
    summary = {
        "scored_decisions": len(scores),
        "with_estimate": sum(1 for s in scores.values()
                             if s["estimate_available"]),
        "with_arrival_snapshot": sum(1 for s in scores.values()
                                     if s["arrival_recorded"]),
        "egress_compared": len(compared),
        "egress_matched": sum(1 for s in compared if s["egress_match"]),
        "egress_match_rate": ((sum(1 for s in compared if s["egress_match"])
                               / len(compared)) if compared else None),
        "egress_bits_delta_samples": len(deltas),
        "egress_bits_delta_mean": (sum(deltas) / len(deltas)) if deltas else None,
        "egress_bits_delta_max": max(deltas) if deltas else None,
        "information_sources": sources,
        "truth_used": False,
        "source": "estimate_at_start",
    }
    return scores, summary


def audit_ledger(by_decision):
    """Report which of the eleven fields are still MISSING, per decision."""
    gaps = {}
    for decision_id, entry in sorted(by_decision.items()):
        missing = [field for field in TIMELINE_FIELDS
                   if entry.get(field) == MISSING]
        if missing:
            gaps[decision_id] = missing
    return gaps
