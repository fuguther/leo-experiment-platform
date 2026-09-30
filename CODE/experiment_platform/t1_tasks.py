"""A2: the three experiment task types on one driver layer.

WHY THIS EXISTS
---------------
Before A2 the suite could compile exactly one kind of cell: a single offline
branch replay ("first_forward").  That is one contrast at one instant, and it
cannot answer either of the user two experiment questions, which need

  branch_alignment   offline, same-origin intervention: several branches of
                     one baseline trajectory, merged into a BLOCK value
                     before any paired statistic is formed;
  network_alignment  the four online arms each run the WHOLE network from the
                     same input trace, with their own queues evolving;
  execution_modes    the five execution modes on one fixed arm.

The three types share this module input handling, failure accounting, budget
declaration and identity, so a cell of any type is schedulable, resumable and
checkable by the same suite.

WHAT THIS MODULE REFUSES TO DO
------------------------------
* It never chooses a branch by its OUTCOME.  Eligibility is decided at the
  observation instant (at least two legal forward directions) and sampling is
  a fixed stride over decision ids; the future of the unchosen actions is
  never consulted.  A candidate set that is too small keeps its reasons.
* It never lets a failed sub-run disappear.  A branch that fails is recorded
  with its reason and the block is marked DEGRADED; a block with no usable
  branch is NO_LEGAL_BRANCH, not an empty success.
* It never presents a branch replay as a network result, or the reverse: the
  two are separate documents with separate schemas.
"""
from __future__ import annotations

import argparse
import collections
import copy
import hashlib
import json
import math
import os
import statistics
import tempfile
from pathlib import Path

from CODE.experiment_platform import artifact_identity, execution_compare
from CODE.experiment_platform import time_alignment_compare as tac
from CODE.leo_sim import config as config_mod, kernel

SCHEMA_TASK = "t1-task-result/v1"
SCHEMA_BRANCH_BLOCK = "t1-branch-block/v1"
SCHEMA_NETWORK = "network-alignment/v1"

TASK_TYPES = ("branch_alignment", "network_alignment", "execution_modes")

#: Pre-declared sampling rule.  Changing it changes the matrix identity.
BRANCH_SAMPLING_RULE = "even_stride_over_eligible_decision_ids/v1"
DEFAULT_MAX_BRANCHES = 12
#: How many per-decision action records one online arm keeps.  A cap that
#: truncates is recorded as such; a cap that silently dropped records would
#: make "the arm did nothing" and "the log was cut" look the same.
DEFAULT_ACTION_LOG_LIMIT = 5000
#: The four online arms, in the frozen contract order.
NETWORK_ARMS = ("stale", "now", "common", "candidate")
#: The five execution modes, re-exported so the suite declares its cells from
#: the driver own list instead of a second copy that can drift.
EXECUTION_MODES = execution_compare.MODES


class TaskError(RuntimeError):
    pass


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      default=str)


def _rows_digest(rows):
    return hashlib.sha256(_canonical(rows).encode("utf-8")).hexdigest()


def _percentile(values, q):
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


# ------------------------------------------------- branch_alignment (offline)
def _baseline_decisions(resolved, rows, geometry):
    """One deterministic baseline pass, kept ONLY to locate branch points.

    The baseline is the shared origin every branch is replayed from; its
    outcomes are never used to choose which branches to keep.
    """
    sink = []
    kernel.run_simulation(resolved, rows, geometry=geometry,
                          decision_sink=sink, timeline_sink=[])
    return sink


def eligible_branches(decision_rows, *, window=None):
    """Branch points a pre-declared rule can justify, WITH the reasons.

    A branch is eligible when the decision was a FORWARD choice and at least
    two directions were legal AT THE OBSERVATION INSTANT.  Whether the
    unchosen directions would have delivered is deliberately not consulted:
    that is the outcome the branch replay exists to measure.
    """
    eligible, rejected = [], []
    for row in decision_rows:
        decision_id = row.get("decision_id")
        kind = row.get("kind")
        if kind != "forward":
            rejected.append({"decision_id": decision_id,
                             "reason": "not a forward decision: " + repr(kind)})
            continue
        t0 = row.get("t_decision_start")
        if window is not None:
            low, high = float(window[0]), float(window[1])
            if t0 is None or not (low <= float(t0) <= high):
                rejected.append({"decision_id": decision_id,
                                 "reason": "outside the measurement window"})
                continue
        observation = row.get("observation_at_start") or {}
        legal = observation.get("legal_directions")
        if legal is None:
            legal = row.get("candidates")
        if not legal or len(legal) < 2:
            rejected.append({"decision_id": decision_id,
                             "reason": "fewer than two legal directions at "
                                       "the branch point, so there is no "
                                       "choice to compare"})
            continue
        eligible.append({"decision_id": int(decision_id),
                         "t_decision_start": (None if t0 is None
                                             else float(t0)),
                         "legal_directions": list(legal),
                         "baseline_chosen": row.get("chosen")})
    eligible.sort(key=lambda item: item["decision_id"])
    return eligible, rejected


def sample_branches(eligible, *, max_branches=DEFAULT_MAX_BRANCHES):
    """An EVEN STRIDE over the eligible decision ids.

    The stride is what makes the block spread over the measurement window
    instead of clustering on the first decisions, and it depends only on the
    COUNT of eligible branches -- never on any arm result.  Taking every
    eligible branch when there are few is the same rule with stride 1.
    """
    max_branches = int(max_branches)
    if max_branches < 1:
        raise TaskError("max_branches must be at least 1")
    total = len(eligible)
    if total == 0:
        return {"rule": BRANCH_SAMPLING_RULE, "eligible": 0, "sampled": 0,
                "stride": None, "decisions": []}
    if total <= max_branches:
        return {"rule": BRANCH_SAMPLING_RULE, "eligible": total,
                "sampled": total, "stride": 1,
                "decisions": [item["decision_id"] for item in eligible]}
    stride = total / float(max_branches)
    picked = [eligible[min(int(i * stride), total - 1)]
              for i in range(max_branches)]
    seen, unique = set(), []
    for item in picked:
        if item["decision_id"] not in seen:
            seen.add(item["decision_id"])
            unique.append(item["decision_id"])
    return {"rule": BRANCH_SAMPLING_RULE, "eligible": total,
            "sampled": len(unique), "stride": stride, "decisions": unique}


# ------------------------ development baseline: candidate ETA offsets (A3)
def candidate_eta_offsets(decision_rows, *, window=None):
    """Per-decision candidate ETA offsets from a DEVELOPMENT BASELINE.

    The offset is (the instant this decision queried for a candidate) minus
    (the request instant), read from the ONLINE audit of a baseline run.  A
    quantile taken from confirmation data is refused by construction: this
    function only ever receives a development baseline.
    """
    offsets, per_decision = [], []
    for row in decision_rows:
        if row.get("kind") != "forward":
            continue
        t0 = row.get("t_decision_start")
        if t0 is None:
            continue
        if window is not None and not (float(window[0]) <= float(t0)
                                       <= float(window[1])):
            continue
        audit = (row.get("observation_at_start") or {}).get("time_alignment")
        targets = (audit or {}).get("query_targets") or {}
        values = [float(target) - float(t0) for target in targets.values()]
        if not values:
            continue
        per_decision.append({"decision_id": row.get("decision_id"),
                             "t_decision_start": float(t0),
                             "offsets": values})
        offsets.extend(values)
    return {"offsets": offsets, "per_decision": per_decision,
            "decisions": len(per_decision), "candidates": len(offsets),
            "rule": "offset = queried instant - request instant, read from "
                    "the online audit of a development baseline"}


def project_horizons(offsets):
    """The FIVE pre-declared common-future candidates, from dev data only.

    Two rule-based candidates (mean and median of the per-decision offset) and
    three FIXED horizons taken from the p25/p50/p75 of the development
    baseline offset population.  The quantile source is named in the result so
    a frozen artifact can prove where the numbers came from.
    """
    values = sorted(float(v) for v in offsets)
    if not values:
        raise TaskError(
            "no development-baseline ETA offset was observed, so no horizon "
            "candidate can be projected")

    def _quantile(q):
        if len(values) == 1:
            return values[0]
        pos = q * (len(values) - 1)
        low, high = int(pos), min(int(pos) + 1, len(values) - 1)
        frac = pos - low
        return values[low] * (1 - frac) + values[high] * frac

    return {
        "mean_eta_offset": {"kind": "rule", "common_rule": "mean_eta"},
        "median_eta_offset": {"kind": "rule", "common_rule": "median_eta"},
        "p25_offset": {"kind": "fixed_horizon",
                       "common_horizon_s": _quantile(0.25)},
        "p50_offset": {"kind": "fixed_horizon",
                       "common_horizon_s": _quantile(0.50)},
        "p75_offset": {"kind": "fixed_horizon",
                       "common_horizon_s": _quantile(0.75)},
        "quantile_source": "development_baseline_candidate_eta_offsets",
        "samples": len(values),
        "mean": statistics.fmean(values),
        "median": _quantile(0.50),
    }


def _arm_actions(arms):
    """What each online arm actually DID at one branch, verbatim.

    The four arms share one branch point and one candidate set, so the only
    thing that can differ between them is the direction their own snapshot
    ranked first.  Without this block a result document would say "the arms
    lost the same" without saying whether they also CHOSE the same, and
    those two facts have completely different explanations: a live mechanism
    that cannot discriminate vs. an inert mechanism that never ran.
    """
    out = {}
    for arm, block in sorted((arms or {}).items()):
        block = block or {}
        scores = block.get("scores") or {}
        out[str(arm)] = {
            "chosen": block.get("chosen"),
            "ranking": list(block.get("ranking") or []),
            "predicted_total_s": {str(d): (s or {}).get("total_s")
                                  for d, s in scores.items()},
            "fallback_directions": list(block.get("fallback_directions") or []),
            "missing_directions": list(block.get("missing_directions") or []),
            "missing_terms": sorted({str(term)
                                     for s in scores.values()
                                     for term in ((s or {}).get("missing") or [])}),
        }
    return out


def _candidate_outcomes(candidates):
    """The realised outcome of every forced direction, for the oracle check.

    loss is None for a censored candidate on purpose: a packet whose
    observation window is shorter than D never had the chance to show a loss,
    and turning that into 1.0 would invent a timeout the run never had.
    """
    out = {}
    for direction, item in sorted((candidates or {}).items()):
        item = item or {}
        out[str(direction)] = {
            "valid": bool(item.get("valid")),
            "reason": item.get("reason"),
            "taken_in_baseline": item.get("taken_in_baseline"),
            "loss": item.get("loss"),
            "regret": item.get("regret"),
            "censored": item.get("censored"),
            "censor_reason": item.get("censor_reason"),
            "resource_mismatch": item.get("resource_mismatch"),
        }
    return out


def _resource_target_evidence(value):
    """Keep only the at-decision-time fields that name a target resource."""
    value = value if isinstance(value, dict) else {}
    status = value.get("status")
    fields = {key: value.get(key) for key in (
        "status", "kind", "peer", "egress_direction", "egress_peer",
        "isl_rate_bps")}
    if status == "ok":
        fields["resource_valid"] = (
            value.get("kind") == "isl"
            and isinstance(value.get("peer"), int)
            and not isinstance(value.get("peer"), bool)
            and value.get("peer") >= 0
            and isinstance(value.get("egress_direction"), str)
            and bool(value.get("egress_direction"))
            and isinstance(value.get("egress_peer"), int)
            and not isinstance(value.get("egress_peer"), bool)
            and value.get("egress_peer") >= 0
            and isinstance(value.get("isl_rate_bps"), (int, float))
            and not isinstance(value.get("isl_rate_bps"), bool)
            and math.isfinite(float(value.get("isl_rate_bps")))
            and float(value.get("isl_rate_bps")) > 0)
    elif status == "delivered_downlink":
        fields["resource_valid"] = (
            value.get("kind") == "downlink"
            and isinstance(value.get("peer"), int)
            and not isinstance(value.get("peer"), bool)
            and value.get("peer") >= 0
            and isinstance(value.get("isl_rate_bps"), (int, float))
            and not isinstance(value.get("isl_rate_bps"), bool)
            and math.isfinite(float(value.get("isl_rate_bps")))
            and float(value.get("isl_rate_bps")) > 0)
    else:
        fields["resource_valid"] = False
    return fields


def _arm_action_log(sink, *, limit=DEFAULT_ACTION_LOG_LIMIT):
    """Per-forward-decision record of what ONE online arm did, from its audit.

    This is the once-only evidence that separates "this arm changed nothing"
    from "this arm was never consulted": every record keeps the instants the
    decision actually queried, the order the arm applied, and the candidate
    terms it could not resolve.
    """
    records = []
    for row in sink:
        if row.get("kind") != "forward":
            continue
        audit = (row.get("observation_at_start") or {}).get("time_alignment")
        if not isinstance(audit, dict):
            continue
        ordered = [str(d) for d in (audit.get("applied_order") or [])]
        scores = audit.get("scores") or {}
        policy = audit.get("inference_policy") or {}
        query_targets = audit.get("query_targets") or {}
        observation = row.get("observation_at_start") or {}
        candidate_resources = observation.get("candidate_resources") or {}
        candidate_query_evidence = {}
        for direction, score in scores.items():
            direction = str(direction)
            score = score if isinstance(score, dict) else {}
            candidate_query_evidence[direction] = {
                "query_target": query_targets.get(direction),
                "score_total_s": score.get("total_s"),
                "missing": list(score.get("missing") or []),
                "fallback": bool(score.get("fallback")),
                "resource_target": _resource_target_evidence(
                    candidate_resources.get(direction)),
            }
        effective_directions = [
            direction for direction, evidence in candidate_query_evidence.items()
            if isinstance(evidence.get("query_target"), (int, float))
            and not isinstance(evidence.get("query_target"), bool)
            and math.isfinite(float(evidence["query_target"]))
            and isinstance(evidence.get("score_total_s"), (int, float))
            and not isinstance(evidence.get("score_total_s"), bool)
            and math.isfinite(float(evidence["score_total_s"]))
            and not evidence.get("missing")
            and not evidence.get("fallback")
            and evidence["resource_target"].get("resource_valid") is True]
        records.append({
            "decision_id": row.get("decision_id"),
            "t_decision_start": row.get("t_decision_start"),
            "arm": audit.get("arm"),
            "execution_mode": audit.get("execution_mode"),
            "chosen": (ordered[0] if ordered else None),
            "applied_order": ordered,
            "scorer_ranking": [str(d) for d in (audit.get("ranking") or [])],
            "reordered": bool(ordered) and ordered != sorted(ordered),
            "query_targets": {str(k): v
                              for k, v in query_targets.items()},
            "candidate_query_evidence": candidate_query_evidence,
            "legal_candidates": sorted(str(k) for k in scores),
            "candidate_query_count": len(scores),
            "effective_candidate_query_count": len(effective_directions),
            "effective_candidate_query_directions": sorted(effective_directions),
            "fallback_directions": [str(d)
                                    for d in (audit.get("fallback_directions") or [])],
            "missing_directions": [str(d)
                                   for d in (audit.get("missing_directions") or [])],
            "missing_terms": sorted({str(term) for s in scores.values()
                                     for term in ((s or {}).get("missing") or [])}),
            "inference_policy_choice": policy.get("choice"),
            "query": audit.get("query"),
        })
    return {"records": records[:limit], "count": len(records),
            "limit": int(limit), "truncated": len(records) > limit,
            "rule": "one record per forward decision, in decision order"}


def _fallback_reason_counts(action_log):
    """Count WHY decisions fell back, keyed by the candidate term missing.

    A bare fallback count cannot tell a query that arrived too late from a
    resource that never appears in the branch; the missing-term names can.
    """
    counts = collections.Counter()
    for record in action_log.get("records") or []:
        for term in record.get("missing_terms") or []:
            counts["unknown_term:" + str(term)] += 1
        counts["direction_marked_fallback"] += len(
            record.get("fallback_directions") or [])
        counts["direction_missing"] += len(record.get("missing_directions") or [])
    return dict(sorted(counts.items()))

def branch_alignment_block(resolved, rows, geometry, *, deadline_s, source,
                           max_branches=DEFAULT_MAX_BRANCHES, window=None):
    """Several branches of ONE baseline trajectory, merged into one block.

    The unit of replication is the BLOCK (scenario x trace x seed), not the
    branch: the branches inside a block are averaged FIRST, and only the
    block values enter the paired analysis.  Twelve branches of one trace
    are not twelve independent runs.
    """
    if deadline_s is None:
        # A3: every branch in a block must be scored against the SAME D.  A
        # per-branch derived D would put different loss scales into one
        # average and the block value would not be a loss at all.
        raise TaskError(
            "branch_alignment needs one deadline for the whole block: pass "
            "--deadline-s or --deadline-from (a baseline-derived D per branch "
            "cannot be averaged into one block value)")
    decision_rows = _baseline_decisions(resolved, rows, geometry)
    eligible, rejected = eligible_branches(decision_rows, window=window)
    sample = sample_branches(eligible, max_branches=max_branches)
    by_id = {item["decision_id"]: item for item in eligible}
    branches, failures = [], []
    for decision_id in sample["decisions"]:
        try:
            document = tac.compare(resolved, rows, geometry, decision_id,
                                   deadline_s, source)
        except Exception as exc:          # noqa: BLE001 - recorded, not lost
            failures.append({"decision_id": decision_id,
                             "reason": f"{type(exc).__name__}: {exc}"})
            continue
        arms = document.get("arms") or {}
        resource_pressure = {}
        for direction, candidate in sorted(
                (document.get("candidates") or {}).items()):
            trajectory = (candidate or {}).get("resource_trajectory") or []
            positive_backlog_events = sum(
                1 for event in trajectory
                if event.get("milestone") == "queue_enter"
                and isinstance(event.get("backlog_before"), (int, float))
                and not isinstance(event.get("backlog_before"), bool)
                and float(event["backlog_before"]) > 0.0)
            resource_pressure[str(direction)] = {
                "resource_link": (candidate or {}).get("resource_link"),
                "queue_enter_events_with_positive_backlog":
                    positive_backlog_events,
                "observed": positive_backlog_events > 0,
            }
        branches.append({
            "decision_id": decision_id,
            "t_decision_start": by_id[decision_id]["t_decision_start"],
            "baseline_chosen": by_id[decision_id]["baseline_chosen"],
            "loss": {a: (arms.get(a) or {}).get("loss") for a in arms},
            "regret": {a: (arms.get(a) or {}).get("regret") for a in arms},
            "counts": dict(document.get("counts") or {}),
            "oracle": document.get("oracle"),
            # FORENSICS: what each arm chose, and what each forced
            # direction actually paid.  The two together are what makes
            # "the four arms tie" a fact about the mechanism instead of a
            # fact about the report.
            "arm_actions": _arm_actions(arms),
            "candidate_outcomes": _candidate_outcomes(
                document.get("candidates") or {}),
            "resource_pressure": resource_pressure,
            "resource_pressure_observed": any(
                value["observed"] for value in resource_pressure.values()),
            "trace_identity": (document.get("identity") or {}).get(
                "sources"),
        })
    identity = artifact_identity.build_identity(
        config=resolved, trace_digest=source.get("trace_sha256"),
        driver_paths=artifact_identity.execution_chain_paths(),
        extra={"task": "branch_alignment", "decision_ids": sample["decisions"],
               "max_branches": int(max_branches)})
    document = {
        "schema": SCHEMA_BRANCH_BLOCK,
        "identity": identity,
        "source": dict(source, config_sha256=resolved["sha256"],
                       baseline_rows=len(decision_rows),
                       baseline_rows_digest=_rows_digest(decision_rows)),
        "deadline": {"deadline_s": None if deadline_s is None
                     else float(deadline_s),
                     "rule": "one D for the whole block: every arm and "
                             "every branch is scored against the same "
                             "deadline"},
        "sampling": sample,
        "eligibility": {"eligible": len(eligible),
                        "rejected": rejected,
                        "rejected_count": len(rejected),
                        "rule": "forward decision with at least two legal "
                                "directions AT THE OBSERVATION INSTANT; "
                                "the future of the unchosen actions is "
                                "never consulted"},
        "branches": branches,
        "failures": failures,
        "units": {"loss": "normalized loss in [0,1] (dimensionless)",
                  "regret": "normalized loss difference (dimensionless)"},
    }
    document["block"] = _merge_block(branches, failures, sample)
    document["status"] = document["block"]["status"]
    document["limits"] = [
        "branches inside one block are NOT independent runs: they are "
        "averaged into one block value before any paired statistic",
        "a failed branch is recorded with its reason and degrades the "
        "block; it is never dropped silently",
        "this is an OFFLINE replay from one baseline trajectory and is not "
        "a network-wide result",
        "no candidate was chosen by its outcome: eligibility is fixed at "
        "the observation instant and sampling is an even stride",
    ]
    return document


def _merge_block(branches, failures, sample):
    """Average the branches into ONE value per arm (the unit of analysis)."""
    if not branches:
        return {"status": "NO_LEGAL_BRANCH", "arms": {}, "value": None,
                "branch_count": 0, "failure_count": len(failures),
                "reason": ("no branch was both eligible and replayable; the "
                           "scenario cannot support this task in this block")}
    arms = {}
    for arm in sorted({a for b in branches for a in (b["loss"] or {})}):
        losses = [float(b["loss"][arm]) for b in branches
                  if (b["loss"] or {}).get(arm) is not None]
        regrets = [float(b["regret"][arm]) for b in branches
                   if (b["regret"] or {}).get(arm) is not None]
        arms[arm] = {"mean_loss": (statistics.fmean(losses) if losses
                                    else None),
                     "mean_regret": (statistics.fmean(regrets) if regrets
                                     else None),
                     "branch_count": len(losses)}
    value = {arm: arms[arm]["mean_regret"] for arm in arms}
    difference = None
    if value.get("common") is not None and value.get("candidate") is not None:
        difference = value["common"] - value["candidate"]
    status = "ok" if not failures else "DEGRADED"
    return {"status": status, "arms": arms, "value": value,
            "primary_difference_common_minus_candidate": difference,
            "branch_count": len(branches),
            "failure_count": len(failures),
            "sampled": sample["sampled"],
            "reason": (None if not failures else
                       "at least one sampled branch could not be replayed")}


# ------------------------------------------------ network_alignment (online)
COMPUTE_MILESTONES = ("compute_request", "compute_wait", "compute_start",
                      "compute_finish")


def _compute_cost(timeline, resolved):
    """Compute cost with the BACKGROUND jobs counted in the total.

    A background ranking is a compute job like any other: it draws on the
    SAME per-satellite pool, so leaving it out of the total would make the
    async modes look free exactly where they are supposed to pay.  A=0
    (unbounded) still produces complete request/start/finish records --
    "nothing queued" is not "nothing ran" -- so jobs with a missing phase
    are counted as such instead of being dropped.
    """
    jobs = {}
    for row in timeline:
        milestone = row.get("milestone")
        if milestone not in COMPUTE_MILESTONES:
            continue
        # the kernel stamps compute jobs with compute_job_id; decision_id is
        # None on a compute phase (it is allocated later), so keying on it
        # would collapse every job into one bucket
        job = row.get("compute_job_id")
        kind = "background" if row.get("pid") is None else "decision"
        jobs.setdefault((kind, job), {})[milestone] = row

    def _tally(kind):
        waits, services = [], []
        missing_start, missing_finish, count = 0, 0, 0
        for (job_kind, _job), rec in jobs.items():
            if job_kind != kind:
                continue
            count += 1
            finish = rec.get("compute_finish") or {}
            start = rec.get("compute_start") or {}
            request = rec.get("compute_request") or {}
            wait = finish.get("wait_s")
            if wait is None:
                wait = (rec.get("compute_wait") or {}).get("wait_s")
            if wait is None:
                started_at = finish.get("started_at", start.get("at"))
                if started_at is not None and request.get("at") is not None:
                    wait = float(started_at) - float(request["at"])
            service = finish.get("service_s")
            if service is None and finish.get("started_at") is not None \
                    and finish.get("finished_at") is not None:
                service = (float(finish["finished_at"])
                           - float(finish["started_at"]))
            if wait is not None:
                waits.append(max(0.0, float(wait)))
            if service is not None:
                services.append(max(0.0, float(service)))
            if start.get("at") is None and finish.get("started_at") is None:
                missing_start += 1
            if finish.get("at") is None and finish.get("finished_at") is None:
                missing_finish += 1
        return {"jobs": count,
                "jobs_with_wait": len(waits),
                "jobs_with_service": len(services),
                "missing_start_events": missing_start,
                "missing_finish_events": missing_finish,
                "queue_wait_s_total": (sum(waits) if waits else 0.0),
                "service_s_total": (sum(services) if services else 0.0),
                "max_wait_s": (max(waits) if waits else 0.0)}

    decision = _tally("decision")
    background = _tally("background")
    servers = int(resolved["config"]["execution"][
        "compute_servers_per_satellite"])
    return {
        "decision_jobs": decision,
        "background_jobs": background,
        "total_jobs": decision["jobs"] + background["jobs"],
        "total_service_s": (decision["service_s_total"]
                            + background["service_s_total"]),
        "total_queue_wait_s": (decision["queue_wait_s_total"]
                               + background["queue_wait_s_total"]),
        "servers_per_satellite": servers,
        "unbounded_pool": servers == 0,
        "includes_background": True,
        "note": "the total includes background update jobs; a total that "
                "omitted them would make the async modes look free",
    }


def _arm_row(resolved, rows, geometry, arm):
    """One full-network run under ONE online arm.

    The four arms start from the SAME input trace and the SAME initial
    state; from the first decision on, each queue evolves under its own
    policy.  That is the point: a network effect is not the sum of
    per-decision differences measured along somebody else trajectory.
    """
    cfg = copy.deepcopy(resolved["config"])
    # The arm is only meaningful with the feature ON: with time alignment
    # disabled every arm is inert and the four rows would be the same run
    # four times, which is exactly the "four arms really ran" failure A2 has
    # to make impossible.
    cfg["time_alignment"]["enabled"] = True
    cfg["time_alignment"]["arm"] = arm
    local = config_mod.resolve_config(cfg)
    sink, timeline = [], []
    result = kernel.run_simulation(local, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=timeline)
    e2e = execution_compare._e2e(result["deliveries"],
                                 result["packet_events"])
    forwards = [r for r in sink if r.get("kind") == "forward"]
    duration = float(local["config"]["scenario"]["duration_s"])
    n_sats = int(local["config"]["scenario"]["num_satellites"])
    per_sat = {}
    for row in forwards:
        key = str(row.get("sat"))
        per_sat[key] = per_sat.get(key, 0) + 1
    counts = sorted(per_sat.values())
    delivered_bits = sum(int(d.get("bits") or 0)
                         for d in result["deliveries"].values())
    admitted = sum(1 for ev in result["packet_events"]
                   if ev.get("kind") == "packet_admitted")
    # Evidence that THIS arm actually ran the state-time path: an inert arm
    # (feature disabled, or a config that silently ignored the arm) would
    # produce a run indistinguishable from the deterministic baseline, and
    # "four arms" would then be one run reported four times.
    audits = [(r.get("observation_at_start") or {}).get("time_alignment")
              for r in sink]
    audits = [a for a in audits if isinstance(a, dict)]
    reordered = sum(1 for a in audits
                    if a.get("applied_order")
                    and list(a["applied_order"]) != sorted(a["applied_order"]))
    audit_block = {
        "decisions_with_state_time_audit": len(audits),
        "decisions_where_the_arm_reordered_candidates": reordered,
        "arms_seen_in_the_audit": sorted({str(a.get("arm")) for a in audits}),
        "decisions_with_query_targets": sum(1 for a in audits
                                            if a.get("query_targets")),
        # THE instants this arm actually queried, so "the arm changed nothing"
        # and "the arm was never consulted" are distinguishable facts.
        "distinct_query_targets": sorted({
            round(float(value), 9)
            for a in audits for value in (a.get("query_targets") or {}).values()}),
        "fallbacks": sum(1 for a in audits
                         if a.get("fallback_directions")),
        "missing": sum(1 for a in audits if a.get("missing_directions")),
        "execution_mode": local["config"]["time_alignment"]["execution_mode"],
    }
    action_log = _arm_action_log(sink)
    audit_block["decisions_in_action_log"] = action_log["count"]
    audit_block["action_log_truncated"] = action_log["truncated"]
    return {
        "arm": arm,
        "config_sha256": local["sha256"],
        "resolved_arm": local["config"]["time_alignment"]["arm"],
        "seed": local["config"]["scenario"]["seed"],
        "predictor": local["config"]["time_alignment"]["predictor"],
        "scope": {"num_satellites": n_sats, "duration_s": duration,
                  "packets_in_trace": len(rows),
                  "decision_requests": len(sink),
                  "forward_decisions": len(forwards),
                  "satellites_that_decided": len(per_sat)},
        "request_rate_per_satellite": {
            "per_satellite": per_sat,
            "mean": (statistics.fmean(counts) if counts else None),
            "max": (counts[-1] if counts else None),
            "note": "the all-satellite mean must not stand in for hotspot "
                    "pressure"},
        "outcome": {
            "offered": len(rows), "admitted": admitted,
            "delivered": len(result["deliveries"]),
            "delivered_bits": delivered_bits,
            "goodput_bps_in_window": (delivered_bits / duration
                                      if duration else None),
            "fate_counts": {k: v for k, v in result["fate_counts"].items()
                            if v},
            "e2e_samples": len(e2e),
            "e2e_mean_s": (None if not e2e else statistics.fmean(e2e)),
            "e2e_p50_s": _percentile(e2e, 0.50),
            "e2e_p90_s": _percentile(e2e, 0.90),
            "e2e_p95_s": _percentile(e2e, 0.95),
            "e2e_p99_s": _percentile(e2e, 0.99),
            "queue_area_bits_s": dict(result["queue_area_bits_s"]),
            "events_processed": result["events_processed"],
        },
        "time_alignment_audit": audit_block,
        "action_log": action_log,
        "fallback_reason_counts": _fallback_reason_counts(action_log),
        "compute": _compute_cost(timeline, local),
        "control": {"bits": dict(result["control"]["bits"]),
                    "counters": dict(result["control"]["counters"])},
        "query_service": result["execution_mode"]["query_service"],
        "congestion_metrics": result["congestion_metrics"],
        "mechanisms": result["mechanisms"],
        "natural_end": result["natural_end"],
    }


def network_alignment(resolved, rows, geometry, source,
                     arms=NETWORK_ARMS, overrides=None):
    """The FOUR online arms, each running the WHOLE network to the horizon."""
    if overrides:
        cfg = copy.deepcopy(resolved["config"])
        for group, values in dict(overrides).items():
            cfg.setdefault(group, {})
            cfg[group].update(values)
        resolved = config_mod.resolve_config(cfg)
    rows_digest = _rows_digest(rows)
    arm_rows = []
    failures = []
    for arm in arms:
        if arm not in NETWORK_ARMS:
            raise TaskError(f"unknown online arm {arm!r}")
        try:
            arm_rows.append(_arm_row(resolved, rows, geometry, arm))
        except Exception as exc:      # noqa: BLE001 - recorded, not lost
            failures.append({"arm": arm,
                             "reason": f"{type(exc).__name__}: {exc}"})
    document = {
        "schema": SCHEMA_NETWORK,
        "identity": artifact_identity.build_identity(
            config=resolved, trace_digest=source.get("trace_sha256"),
            driver_paths=artifact_identity.execution_chain_paths(),
            extra={"task": "network_alignment", "arms": list(arms)}),
        "source": dict(source, rows_digest=rows_digest,
                       config_sha256=resolved["sha256"]),
        "fairness": {
            "rows_digest": rows_digest,
            "same_trace": True,
            "same_seed": resolved["config"]["scenario"]["seed"],
            "same_predictor": resolved["config"]["time_alignment"]["predictor"],
            "arms_differ_only_in": "time_alignment.arm (the query instant the "
                                   "arm aligns each candidate to)",
        },
        "arms": arm_rows,
        "failures": failures,
        "status": ("ok" if len(arm_rows) == len(arms) else "DEGRADED" if arm_rows
                   else "ALL_ARMS_FAILED"),
        "units": {"e2e": "seconds", "goodput": "bits per second",
                  "queue_area": "bit-seconds"},
        "limits": [
            "each arm runs the whole network from the same trace and the "
            "same initial state; queues then evolve under that arm policy",
            "this is a NETWORK result and is NOT interchangeable with the "
            "offline branch comparison",
            "one trace, one seed: no statistical statement is made here",
            "the deterministic scorer is used; this is not a DDQN result",
        ],
    }
    return document


# ------------------------------------------------------- the task dispatcher
def run_task(task, resolved, rows, geometry, source, *, deadline_s=None,
             max_branches=DEFAULT_MAX_BRANCHES, window=None, modes=None,
             overrides=None, arms=NETWORK_ARMS):
    """One entry point for the three task types.

    The suite only ever calls this, so budget enforcement, failure
    accounting, identity and recovery are identical for all three and a new
    task type cannot quietly get its own private rules.
    """
    if task not in TASK_TYPES:
        raise TaskError(f"unknown task {task!r}; have {list(TASK_TYPES)}")
    if task == "branch_alignment":
        driver = branch_alignment_block(
            resolved, rows, geometry, deadline_s=deadline_s, source=source,
            max_branches=max_branches, window=window)
    elif task == "network_alignment":
        driver = network_alignment(resolved, rows, geometry, source,
                                   arms=arms, overrides=overrides)
    else:
        driver = execution_compare.compare(
            resolved, rows, geometry, source,
            modes=tuple(modes) if modes else execution_compare.MODES,
            overrides=overrides)
    failures = driver.get("failures") or []
    return {
        "schema": SCHEMA_TASK,
        "task": task,
        "status": driver.get("status", "ok" if not failures else "DEGRADED"),
        "failed_units": len(failures),
        "driver_schema": driver.get("schema"),
        "identity": driver.get("identity"),
        "document": driver,
        "limits": [
            "a failed sub-run is recorded in failed_units and degrades the "
            "task; it is never wrapped as a success by the caller",
        ],
    }


# ------------------------------------------------------------- design + CLI
def _apply_overrides(resolved, overrides):
    cfg = copy.deepcopy(resolved["config"])
    for group, values in dict(overrides or {}).items():
        cfg.setdefault(group, {})
        cfg[group].update(values)
    return config_mod.resolve_config(cfg)


def design(config_path=None, scenario=None, root=None, overrides=None):
    """Resolve the run inputs ONCE, so all three task types share them.

    Overrides are applied BEFORE the trace is compiled, which is what makes
    "change the offered load" change the final resolved config AND the
    trace the drivers run on, instead of only editing a frozen horizon in a
    metadata file.
    """
    from CODE.experiment_platform import scripted_scenarios
    from CODE.leo_sim import trace as trace_mod

    if scenario:
        try:
            resolved, rows, geometry, meta = scripted_scenarios.build(scenario)
        except KeyError as exc:
            raise TaskError(str(exc)) from exc
        if overrides:
            resolved = _apply_overrides(resolved, overrides)
        source = {"scenario": scenario, "config": None, "trace_sha256": None,
                  "rows": len(rows), "overrides": dict(overrides or {})}
        return resolved, rows, geometry, source
    if not config_path:
        raise TaskError("give exactly one of config_path or scenario")
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise TaskError(f"config invalid: {exc}") from exc
    if overrides:
        resolved = _apply_overrides(resolved, overrides)
    # ``root`` remains accepted for CLI/API compatibility, but it may point at
    # the immutable release checkout. The runner points TMPDIR at the isolated
    # run directory, so temporary trace material stays writable and does not
    # mutate the code.
    work = Path(tempfile.mkdtemp(prefix="t1task-"))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=resolved["config"]["execution"]["max_packets"])
        digest = (manifest.get("__trace_sha256")
                  or manifest.get("trace_sha256"))
    finally:
        for leftover in sorted(work.glob("*"), reverse=True):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass
    source = {"scenario": "config", "config": str(config_path),
              "trace_sha256": digest, "rows": len(rows),
              "overrides": dict(overrides or {})}
    return resolved, rows, None, source


def publish(document, out):
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise TaskError(f"output destination exists: {out}")
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise TaskError(f"output parent must be a real directory: {out.parent}")
    handle, temporary = tempfile.mkstemp(prefix="." + out.name + ".",
                                         suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, out)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _deadline_s(args):
    if args.deadline_from:
        payload = json.loads(Path(args.deadline_from).read_text(
            encoding="utf-8"))
        value = payload.get("deadline_s")
        if value is None:
            raise TaskError(
                f"{args.deadline_from} does not carry a deadline_s value")
        return float(value)
    return args.deadline_s


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="T1 experiment task driver (A2): three task types")
    parser.add_argument("--task", required=True, choices=list(TASK_TYPES))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scenario")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--deadline-s", type=float, default=None)
    parser.add_argument("--deadline-from", type=Path, default=None)
    parser.add_argument("--max-branches", type=int, default=DEFAULT_MAX_BRANCHES)
    parser.add_argument("--window-start", type=float, default=None)
    parser.add_argument("--window-end", type=float, default=None)
    parser.add_argument("--modes", default=None)
    parser.add_argument("--arms", default=None)
    parser.add_argument("--compute-servers", type=int, default=None)
    parser.add_argument("--service-s", type=float, default=None)
    parser.add_argument("--packet-bits", type=int, default=None)
    parser.add_argument("--offered-mbps", type=float, default=None)
    parser.add_argument("--update-interval-s", type=float, default=None)
    parser.add_argument("--window-bins", type=int, default=None)
    parser.add_argument("--common-horizon-s", type=float, default=None)
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.scenario):
        print("T1TASKS REFUSED: give exactly one of --config or --scenario")
        return 2
    overrides = {}
    if args.compute_servers is not None:
        overrides.setdefault("execution", {})[
            "compute_servers_per_satellite"] = int(args.compute_servers)
    if args.service_s is not None:
        overrides.setdefault("execution", {})["compute_delay_s"] = float(
            args.service_s)
    if args.packet_bits is not None:
        overrides.setdefault("demand", {})["packet_bits"] = int(args.packet_bits)
    if args.offered_mbps is not None:
        overrides.setdefault("demand", {})["offered_mbps"] = float(
            args.offered_mbps)
    if args.update_interval_s is not None:
        overrides.setdefault("async_routing", {})["update_interval_s"] = float(
            args.update_interval_s)
    if args.window_bins is not None:
        overrides.setdefault("async_routing", {})["window_bins"] = int(
            args.window_bins)
    if args.common_horizon_s is not None:
        overrides.setdefault("time_alignment", {})["common_horizon_s"] = float(
            args.common_horizon_s)
        overrides["time_alignment"]["common_rule"] = "fixed_horizon"
    window = None
    if args.window_start is not None or args.window_end is not None:
        if args.window_start is None or args.window_end is None:
            print("T1TASKS REFUSED: give both --window-start and --window-end")
            return 2
        window = (float(args.window_start), float(args.window_end))
    try:
        resolved, rows, geometry, source = design(
            args.config, args.scenario, args.root, overrides)
        document = run_task(
            args.task, resolved, rows, geometry, source,
            deadline_s=_deadline_s(args), max_branches=args.max_branches,
            window=window,
            modes=([m.strip() for m in str(args.modes).split(",") if m.strip()]
                   if args.modes else None),
            arms=(tuple(a.strip() for a in str(args.arms).split(",") if a.strip())
                  if args.arms else NETWORK_ARMS))
        publish(document, args.out)
    except TaskError as exc:
        print(f"T1TASKS REFUSED: {exc}")
        return 2
    print(json.dumps({"status": document["status"], "task": document["task"],
                      "out": str(args.out),
                      "failed_units": document["failed_units"]},
                     ensure_ascii=False))
    return 0 if document["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
