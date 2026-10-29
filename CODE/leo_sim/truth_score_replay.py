"""Offline target-truth substitution for recorded time-alignment scores.

This diagnostic is intentionally narrower than a counterfactual routing oracle.
It freezes every recorded score term except ``resource_work_s`` and replaces
that one term with same-run target-time truth from ``target_probe.jsonl``.
The result answers only whether queue/work prediction error is large enough to
change the *existing scorer's* ranking when all other terms are held fixed.
"""
from __future__ import annotations

import math
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


def _finite_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _same_number(left, right, *, abs_tol: float = 1e-9) -> bool:
    return (_finite_number(left) and _finite_number(right)
            and math.isclose(float(left), float(right), rel_tol=0.0,
                             abs_tol=abs_tol))


def replay_decision(decision: dict, probe_rows: Iterable[dict]) -> dict:
    """Replay only when every eligible candidate has aligned target-time truth."""
    ta = _time_alignment(decision)
    scores = ta.get("scores") or {}
    ranking = [str(d) for d in (ta.get("ranking") or [])]
    visible = _visible_order(decision, ranking)
    decision_candidates = [str(d) for d in (decision.get("candidates") or [])]
    used_state = ta.get("used_state") or {}
    eta_targets = ta.get("eta_targets") or {}

    eligible: list[str] = []
    for direction in ranking:
        score = scores.get(direction) or {}
        total = score.get("total_s")
        if score.get("fallback") or score.get("missing") or not _finite_number(total):
            continue
        eligible.append(direction)

    rows_by_direction: dict[str, list[dict]] = {}
    for row in probe_rows:
        if row.get("direction") is not None:
            rows_by_direction.setdefault(str(row["direction"]), []).append(row)

    missing: list[str] = []
    invalid: list[str] = []
    duplicates: list[str] = []
    replayed: dict[str, dict] = {}
    integrity_errors: dict[str, list[str]] = {}

    for direction in eligible:
        matches = rows_by_direction.get(direction, [])
        if not matches:
            missing.append(direction)
            integrity_errors[direction] = ["missing_probe_row"]
            continue
        if len(matches) != 1:
            duplicates.append(direction)
            integrity_errors[direction] = ["duplicate_probe_rows"]
            continue

        row = matches[0]
        truth = row.get("truth")
        if truth is None:
            missing.append(direction)
            integrity_errors[direction] = [str(row.get("missing_reason") or "missing_truth")]
            continue

        score = scores[direction]
        terms = score.get("terms") or {}
        expected = used_state.get(direction) or {}
        errors: list[str] = []

        if row.get("forced") is not False:
            errors.append("forced_status_not_explicit_false")
        if [str(x) for x in (row.get("candidate_directions") or [])] != decision_candidates:
            errors.append("candidate_set_mismatch")

        expected_resource_id = expected.get("resource_id")
        expected_generation = expected.get("generation")
        if (not expected_resource_id or expected_generation is None
                or row.get("identity_source") != "used_state"):
            errors.append("decision_resource_identity_missing_or_not_used")
        if (row.get("resource_id") != expected_resource_id
                or row.get("generation") != expected_generation):
            errors.append("decision_resource_identity_mismatch")
        if (row.get("sampled_resource_id") != row.get("resource_id")
                or row.get("sampled_generation") != row.get("generation")):
            errors.append("sampled_resource_identity_mismatch")
        if (truth.get("resource_id") != row.get("resource_id")
                or truth.get("generation") != row.get("generation")):
            errors.append("truth_resource_identity_mismatch")

        expected_target = eta_targets.get(direction)
        if not _same_number(row.get("eval_target_s"), expected_target):
            errors.append("evaluation_target_mismatch")
        if not _same_number(row.get("sampled_at_s"), row.get("eval_target_s")):
            errors.append("sampled_instant_mismatch")

        rate = score.get("service_rate_bps")
        if not _finite_number(rate) or float(rate) <= 0:
            errors.append("score_service_rate_invalid")
        if not _same_number(row.get("service_rate_bps"), rate):
            errors.append("recorded_service_rate_mismatch")

        predicted_work_s = terms.get("resource_work_s")
        online_total = score.get("total_s")
        truth_bits = truth.get("work_ahead_bits")
        if not _finite_number(predicted_work_s):
            errors.append("predicted_resource_work_invalid")
        if not _finite_number(online_total):
            errors.append("online_score_invalid")
        if not _same_number(row.get("predicted_work_s"), predicted_work_s):
            errors.append("recorded_resource_work_mismatch")
        if row.get("score_total_s") is not None and not _same_number(
                row.get("score_total_s"), online_total):
            errors.append("recorded_total_score_mismatch")
        try:
            if any(not _finite_number(v) for v in terms.values()):
                raise ValueError("non-finite score term")
            term_sum = sum(float(v) for v in terms.values())
            if not _same_number(term_sum, online_total):
                errors.append("score_terms_not_additive")
        except (TypeError, ValueError, OverflowError):
            errors.append("score_terms_not_finite_or_additive")
        if not _finite_number(truth_bits) or float(truth_bits) < 0:
            errors.append("target_truth_work_invalid")

        if errors:
            invalid.append(direction)
            integrity_errors[direction] = sorted(set(errors))
            continue

        truth_work_s = float(truth_bits) / float(rate)
        delta = truth_work_s - float(predicted_work_s)
        truth_total = float(online_total) + delta
        replayed[direction] = {
            "online_total_s": float(online_total),
            "predicted_resource_work_s": float(predicted_work_s),
            "truth_resource_work_s": truth_work_s,
            "delta_resource_work_s": delta,
            "truth_proxy_total_s": truth_total,
        }

    comparable = (
        len(eligible) >= 2 and not missing and not invalid and not duplicates
        and len(replayed) == len(eligible)
    )
    base = {
        "pid": decision.get("pid"),
        "decision_id": decision.get("decision_id"),
        "comparable": comparable,
        "eligible_directions": eligible,
        "missing_truth_directions": missing,
        "invalid_directions": invalid,
        "duplicate_directions": duplicates,
        "integrity_errors": integrity_errors,
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
    """Join sampled decision groups and report provenance/coverage failures."""
    decision_rows = list(decisions)
    decision_index: dict[tuple[object, object], dict] = {}
    for decision in decision_rows:
        pid, decision_id = decision.get("pid"), decision.get("decision_id")
        if pid is None or decision_id is None:
            continue
        key = (pid, decision_id)
        if key in decision_index:
            raise ValueError(f"duplicate decision key (pid, decision_id)={key!r}")
        decision_index[key] = decision

    probe_list = list(probe_rows)
    grouped: dict[tuple[object, object], list[dict]] = {}
    for row in probe_list:
        key = (row.get("pid"), row.get("decision_id"))
        if key[0] is None or key[1] is None:
            continue
        grouped.setdefault(key, []).append(row)

    alignment_keys = {
        (d.get("pid"), d.get("decision_id"))
        for d in decision_rows
        if d.get("pid") is not None and d.get("decision_id") is not None
        and bool(_time_alignment(d).get("scores"))
    }
    alignment_with_probe = alignment_keys.intersection(grouped)

    results: list[dict] = []
    forced_groups_excluded = 0
    forced_status_unknown_groups = 0
    mixed_forced_status_groups = 0
    missing_decisions = 0
    for key, rows in grouped.items():
        flags = [row.get("forced") for row in rows]
        if any(not isinstance(flag, bool) for flag in flags):
            forced_status_unknown_groups += 1
            continue
        if len(set(flags)) != 1:
            mixed_forced_status_groups += 1
            continue
        if flags[0] is True:
            forced_groups_excluded += 1
            continue
        decision = decision_index.get(key)
        if decision is None:
            missing_decisions += 1
            continue
        results.append(replay_decision(decision, rows))

    return {
        "decision_records": len(decision_rows),
        "alignment_decision_records": len(alignment_keys),
        "alignment_decisions_with_probe_rows": len(alignment_with_probe),
        "alignment_decisions_without_probe_rows": len(alignment_keys - set(grouped)),
        "alignment_probe_coverage_rate": (
            len(alignment_with_probe) / len(alignment_keys) if alignment_keys else None
        ),
        "candidate_rows_received": len(probe_list),
        "probe_decisions": len(grouped),
        "forced_groups_excluded": forced_groups_excluded,
        "forced_status_unknown_groups": forced_status_unknown_groups,
        "mixed_forced_status_groups": mixed_forced_status_groups,
        "missing_decision_records": missing_decisions,
        "summary": summarize_replays(results),
        "results": results,
    }
