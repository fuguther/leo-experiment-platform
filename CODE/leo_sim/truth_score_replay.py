"""Offline target-truth substitution for recorded time-alignment scores.

This diagnostic is intentionally narrower than a counterfactual routing oracle.
It freezes every recorded score term except ``resource_work_s`` and replaces
that one term with same-run target-time truth from ``target_probe.jsonl``.
The result answers only whether queue/work prediction error is large enough to
change the *existing scorer's* ranking when all other terms are held fixed.
"""
from __future__ import annotations

from typing import Iterable


def _time_alignment(decision: dict) -> dict:
    observation = decision.get("observation_at_start") or {}
    return observation.get("time_alignment") or decision.get("time_alignment") or {}


def _visible_order(decision: dict, ranking: list[str]) -> dict[str, int]:
    candidates = decision.get("candidates") or []
    ordered = [str(d) for d in candidates]
    for direction in ranking:
        if direction not in ordered:
            ordered.append(direction)
    return {direction: index for index, direction in enumerate(ordered)}


def replay_decision(decision: dict, probe_rows: Iterable[dict]) -> dict:
    """Replay one recorded decision after substituting target-time truth.

    ``probe_rows`` must refer to the same decision. Only candidates that had a
    finite, non-fallback online score are part of the comparison. The replay is
    marked non-comparable unless every such candidate has target truth and a
    positive service rate; silently dropping a missing candidate could invent a
    ranking flip.
    """
    ta = _time_alignment(decision)
    scores = ta.get("scores") or {}
    ranking = [str(d) for d in (ta.get("ranking") or [])]
    visible = _visible_order(decision, ranking)

    eligible: list[str] = []
    for direction in ranking:
        score = scores.get(direction) or {}
        total = score.get("total_s")
        if score.get("fallback") or score.get("missing") or total is None:
            continue
        eligible.append(direction)

    rows = {
        str(row.get("direction")): row
        for row in probe_rows
        if row.get("direction") is not None
    }
    missing: list[str] = []
    replayed: dict[str, dict] = {}

    for direction in eligible:
        score = scores[direction]
        row = rows.get(direction)
        truth = None if row is None else row.get("truth")
        truth_bits = None if not truth else truth.get("work_ahead_bits")
        rate = score.get("service_rate_bps")
        if rate is None and row is not None:
            rate = row.get("service_rate_bps")
        terms = score.get("terms") or {}
        predicted_work_s = terms.get("resource_work_s")
        if (
            row is None
            or truth_bits is None
            or rate is None
            or float(rate) <= 0
            or predicted_work_s is None
        ):
            missing.append(direction)
            continue

        truth_work_s = float(truth_bits) / float(rate)
        delta = truth_work_s - float(predicted_work_s)
        online_total = float(score["total_s"])
        truth_total = online_total + delta
        replayed[direction] = {
            "online_total_s": online_total,
            "predicted_resource_work_s": float(predicted_work_s),
            "truth_resource_work_s": truth_work_s,
            "delta_resource_work_s": delta,
            "truth_proxy_total_s": truth_total,
        }

    comparable = len(eligible) >= 2 and not missing and len(replayed) == len(eligible)
    base = {
        "pid": decision.get("pid"),
        "decision_id": decision.get("decision_id"),
        "comparable": comparable,
        "eligible_directions": eligible,
        "missing_truth_directions": missing,
        "directions": replayed,
        "actual_chosen": decision.get("chosen"),
    }
    if not comparable:
        return {
            **base,
            "online_choice": eligible[0] if eligible else None,
            "truth_choice": None,
            "ranking_flipped": None,
            "proxy_regret_s": None,
            "online_top2_margin_s": None,
            "truth_top2_margin_s": None,
            "differential_perturbation_span_s": None,
        }

    online_rank = sorted(
        eligible,
        key=lambda d: (replayed[d]["online_total_s"], visible.get(d, 10**9), d),
    )
    truth_rank = sorted(
        eligible,
        key=lambda d: (replayed[d]["truth_proxy_total_s"], visible.get(d, 10**9), d),
    )
    online_choice = online_rank[0]
    truth_choice = truth_rank[0]
    online_margin = (
        replayed[online_rank[1]]["online_total_s"]
        - replayed[online_rank[0]]["online_total_s"]
    )
    truth_margin = (
        replayed[truth_rank[1]]["truth_proxy_total_s"]
        - replayed[truth_rank[0]]["truth_proxy_total_s"]
    )
    proxy_regret = (
        replayed[online_choice]["truth_proxy_total_s"]
        - replayed[truth_choice]["truth_proxy_total_s"]
    )
    deltas = [replayed[d]["delta_resource_work_s"] for d in eligible]

    return {
        **base,
        "online_ranking": online_rank,
        "truth_ranking": truth_rank,
        "online_choice": online_choice,
        "truth_choice": truth_choice,
        "ranking_flipped": online_choice != truth_choice,
        "proxy_regret_s": proxy_regret,
        "online_top2_margin_s": online_margin,
        "truth_top2_margin_s": truth_margin,
        "differential_perturbation_span_s": max(deltas) - min(deltas),
    }


def _numeric_summary(values: Iterable[float]) -> dict:
    data = sorted(float(v) for v in values)
    if not data:
        return {"n": 0, "mean": None, "median": None, "p95": None, "max": None}
    n = len(data)
    if n % 2:
        median = data[n // 2]
    else:
        median = 0.5 * (data[n // 2 - 1] + data[n // 2])
    p95 = data[int(0.95 * (n - 1))]
    return {
        "n": n,
        "mean": sum(data) / n,
        "median": median,
        "p95": p95,
        "max": data[-1],
    }


def summarize_replays(results: Iterable[dict]) -> dict:
    """Aggregate replay rows without conflating score flips with final actions."""
    rows = list(results)
    comparable = [r for r in rows if r.get("comparable")]
    flips = [r for r in comparable if r.get("ranking_flipped") is True]
    action_auditable = [
        r
        for r in comparable
        if r.get("actual_chosen") is not None and r.get("online_choice") is not None
    ]
    online_matches = sum(
        1
        for r in action_auditable
        if r.get("actual_chosen") == r.get("online_choice")
    )
    n_comp = len(comparable)
    n_action = len(action_auditable)
    return {
        "decisions": len(rows),
        "comparable": n_comp,
        "comparable_rate": (n_comp / len(rows)) if rows else None,
        "ranking_flips": len(flips),
        "ranking_flip_rate": (len(flips) / n_comp) if n_comp else None,
        "action_auditable": n_action,
        "online_top1_matches_actual": online_matches,
        "online_top1_match_rate": (online_matches / n_action) if n_action else None,
        "proxy_regret_s": _numeric_summary(
            r["proxy_regret_s"]
            for r in comparable
            if r.get("proxy_regret_s") is not None
        ),
        "proxy_regret_s_on_flips": _numeric_summary(
            r["proxy_regret_s"] for r in flips if r.get("proxy_regret_s") is not None
        ),
        "online_top2_margin_s": _numeric_summary(
            r["online_top2_margin_s"]
            for r in comparable
            if r.get("online_top2_margin_s") is not None
        ),
        "differential_perturbation_span_s": _numeric_summary(
            r["differential_perturbation_span_s"]
            for r in comparable
            if r.get("differential_perturbation_span_s") is not None
        ),
    }


def replay_dataset(decisions: Iterable[dict], probe_rows: Iterable[dict]) -> dict:
    """Join decision records with systematic probe rows and aggregate results.

    Forced/debug-only probe groups are excluded from the statistical cohort. If
    a decision has both systematic and forced rows, only systematic rows are
    used so debug oversampling cannot change the ranking-flip rate.
    """
    decision_index = {
        (d.get("pid"), d.get("decision_id")): d
        for d in decisions
        if d.get("pid") is not None and d.get("decision_id") is not None
    }
    grouped: dict[tuple[object, object], list[dict]] = {}
    for row in probe_rows:
        key = (row.get("pid"), row.get("decision_id"))
        if key[0] is None or key[1] is None:
            continue
        grouped.setdefault(key, []).append(row)

    results: list[dict] = []
    forced_only = 0
    missing_decisions = 0
    for key, rows in grouped.items():
        systematic = [r for r in rows if not r.get("forced")]
        if not systematic:
            forced_only += 1
            continue
        decision = decision_index.get(key)
        if decision is None:
            missing_decisions += 1
            continue
        results.append(replay_decision(decision, systematic))

    return {
        "probe_decisions": len(grouped),
        "forced_groups_excluded": forced_only,
        "missing_decision_records": missing_decisions,
        "summary": summarize_replays(results),
        "results": results,
    }
