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

    ``probe_rows`` must refer to the same decision.  Only candidates that had a
    finite, non-fallback online score are part of the comparison.  The replay is
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

    rows = {str(row.get("direction")): row for row in probe_rows
            if row.get("direction") is not None}
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
        if (row is None or truth_bits is None or rate is None
                or float(rate) <= 0 or predicted_work_s is None):
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
    online_margin = (replayed[online_rank[1]]["online_total_s"]
                     - replayed[online_rank[0]]["online_total_s"])
    truth_margin = (replayed[truth_rank[1]]["truth_proxy_total_s"]
                    - replayed[truth_rank[0]]["truth_proxy_total_s"])
    proxy_regret = (replayed[online_choice]["truth_proxy_total_s"]
                    - replayed[truth_choice]["truth_proxy_total_s"])
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
