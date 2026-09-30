"""Read-only forensics over PULLED T1 artifacts.

WHY THIS EXISTS
---------------
The B round delivered "the four arms tie" and "the async mode needs fewer
compute jobs".  Neither statement explains itself.  This module answers the
three questions those numbers leave open, from the DECLARED traces only:
it never runs an experiment, never picks a branch by its outcome, and never
fills a missing value with a zero.

  1. WHAT EACH ARM DID.  Per sampled branch: the direction each of the four
     online arms ranked first, whether the arms chose differently from each
     other, and whether they chose differently from the baseline.
  2. WAS THE PICKED DIRECTION THE RIGHT ONE.  The chosen direction is scored
     against the offline oracle (the forced-replay best), together with the
     realised spread between candidate directions.  A branch where every
     direction pays the same has ZERO HEADROOM: no policy can win there, so
     a tie between arms there is not evidence about the mechanism.
  3. WHY A DECISION FELL BACK.  Counts keyed by the candidate term that
     could not be resolved, never a bare fallback total.

Every aggregate carries the number of items it was computed from, and an
aggregate over zero usable items is NOT_COMPUTABLE, not 0.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib

SCHEMA = "t1-forensics/v1"
NOT_COMPUTABLE = "NOT_COMPUTABLE"


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def load_result(path):
    """Read one pulled cell result file (never an experiment)."""
    payload = json.loads(pathlib.Path(path).read_text())
    if not isinstance(payload, dict):
        raise ValueError("cell result is not a JSON object: %s" % path)
    return payload


def document_of(payload):
    document = payload.get("document")
    if not isinstance(document, dict):
        raise ValueError("cell result has no document block")
    return document


def kendall_tau_b(predicted, realised):
    """Rank agreement between a prediction and the realised loss, tau-b.

    Both mappings are direction -> score with LOWER = BETTER (the prediction
    is the scorer total, the realised side is the normalised loss).  Ties are
    kept and handled by the b variant, because a candidate set where two
    directions cost exactly the same is a tie, not a coin flip.
    Returns None when the statistic is undefined (fewer than two usable
    directions).
    """
    shared = [d for d in predicted if d in realised
              and _finite(predicted[d]) and _finite(realised[d])]
    if len(shared) < 2:
        return None
    concordant = discordant = ties_pred = ties_real = 0
    for i in range(len(shared)):
        for j in range(i + 1, len(shared)):
            a, b = shared[i], shared[j]
            pa = predicted[a] - predicted[b]
            ra = realised[a] - realised[b]
            if pa == 0 and ra == 0:
                ties_pred += 1
                ties_real += 1
            elif pa == 0:
                ties_pred += 1
            elif ra == 0:
                ties_real += 1
            elif (pa > 0) == (ra > 0):
                concordant += 1
            else:
                discordant += 1
    n0 = len(shared) * (len(shared) - 1) / 2.0
    denom = math.sqrt((n0 - ties_pred) * (n0 - ties_real))
    if denom == 0:
        return None
    return (concordant - discordant) / denom


def _mean(values):
    usable = [float(v) for v in values if _finite(v)]
    if not usable:
        return None
    return sum(usable) / float(len(usable))


def branch_forensics(document):
    """Question 1 + 2 for one branch block.

    The per-arm action record is the only place the CHOSEN direction lives.
    Artifacts that predate it still carry the per-arm loss/regret and the
    oracle choice, so the HEADROOM question stays computable while the
    choice question is reported as NOT_COMPUTABLE instead of being guessed.
    """
    branches = document.get("branches") or []
    rows, arms_seen = [], []

    def _note(arm):
        arm = str(arm)
        if arm not in arms_seen:
            arms_seen.append(arm)

    for branch in branches:
        for arm in (branch.get("arm_actions") or {}):
            _note(arm)
        for key in ("loss", "regret"):
            for arm in (branch.get(key) or {}):
                _note(arm)
    per_arm = {arm: {"chosen_counts": {}, "top1_hits": 0, "branches": 0,
                     "chosen_recorded": 0, "differs_from_baseline": 0,
                     "taus": [], "regrets": [], "zero_regret": 0,
                     "missing_terms": {}} for arm in arms_seen}
    agree = disagree = zero_spread = oracle_off_baseline = 0
    without_actions = 0
    spreads, gradable = [], 0
    for branch in branches:
        actions = branch.get("arm_actions") or {}
        outcomes = branch.get("candidate_outcomes") or {}
        baseline_chosen = branch.get("baseline_chosen")
        oracle = (branch.get("oracle") or {}).get("chosen")
        realised = {d: item.get("loss") for d, item in outcomes.items()
                    if item.get("valid") and _finite(item.get("loss"))}
        choices = {arm: (block or {}).get("chosen")
                   for arm, block in actions.items()}
        distinct = {c for c in choices.values() if c is not None}
        if not distinct:
            # No arm record at all is NOT agreement.  Counting it as
            # agreement would turn a missing instrument into a finding.
            without_actions += 1
        elif len(distinct) == 1:
            agree += 1
        else:
            disagree += 1
        spread = (None if len(realised) < 2
                  else max(realised.values()) - min(realised.values()))
        if spread is not None:
            spreads.append(spread)
            gradable += 1
            if spread == 0:
                zero_spread += 1
        if oracle is not None and baseline_chosen is not None \
                and oracle != baseline_chosen:
            oracle_off_baseline += 1
        best = (min(realised.values()) if realised else None)
        for arm in arms_seen:
            block = actions.get(arm) or {}
            stats = per_arm[arm]
            stats["branches"] += 1
            chosen = block.get("chosen")
            if chosen is not None:
                stats["chosen_recorded"] += 1
                stats["chosen_counts"][chosen] = \
                    stats["chosen_counts"].get(chosen, 0) + 1
                if chosen != baseline_chosen:
                    stats["differs_from_baseline"] += 1
                if oracle is not None and chosen == oracle:
                    stats["top1_hits"] += 1
            # the artifact own regret first: it was computed against the
            # same forced replays the oracle was
            regret = (branch.get("regret") or {}).get(arm)
            if regret is None and chosen is not None and best is not None \
                    and chosen in realised:
                regret = realised[chosen] - best
            if _finite(regret):
                stats["regrets"].append(float(regret))
                if float(regret) == 0:
                    stats["zero_regret"] += 1
            tau = kendall_tau_b(block.get("predicted_total_s") or {},
                                realised)
            if tau is not None:
                stats["taus"].append(tau)
            for term in block.get("missing_terms") or []:
                stats["missing_terms"][term] = \
                    stats["missing_terms"].get(term, 0) + 1
        rows.append({"decision_id": branch.get("decision_id"),
                     "t_decision_start": branch.get("t_decision_start"),
                     "baseline_chosen": baseline_chosen,
                     "oracle_chosen": oracle,
                     "arm_choices": choices,
                     "realised_loss": realised,
                     "loss_spread": spread,
                     "censored_directions": sorted(
                         d for d, item in outcomes.items()
                         if item.get("censored"))})
    arms_with_records = 0
    for arm, stats in per_arm.items():
        if stats["chosen_recorded"]:
            arms_with_records += 1
        stats["top1_hit_rate"] = (
            NOT_COMPUTABLE if not stats["chosen_recorded"]
            else stats["top1_hits"] / float(stats["chosen_recorded"]))
        stats["hit_rate_reason"] = (
            None if stats["chosen_recorded"] else
            "this artifact does not record which direction the arm chose")
        stats["mean_kendall_tau"] = _mean(stats["taus"])
        stats["mean_regret"] = _mean(stats["regrets"])
        stats["tau_branches"] = len(stats["taus"])
        stats["regret_branches"] = len(stats["regrets"])
        if stats["tau_branches"] == 0:
            stats["mean_kendall_tau"] = NOT_COMPUTABLE
            stats["tau_reason"] = (
                "no branch has two candidates with a finite realised loss;"
                " the existing losses tie or are censored")
        if stats["regret_branches"] == 0:
            stats["mean_regret"] = NOT_COMPUTABLE
        del stats["taus"], stats["regrets"]
    spread_summary = (_mean(spreads) if spreads else NOT_COMPUTABLE)
    return {
        "schema": SCHEMA,
        "kind": "branch",
        "branches": len(branches),
        "branches_gradable": gradable,
        "branches_without_arm_actions": without_actions,
        "arms_with_action_records": arms_with_records,
        "branches_where_all_arms_agree": agree,
        "branches_where_arms_disagree": disagree,
        "branches_with_zero_spread": zero_spread,
        "branches_where_oracle_differs_from_baseline": oracle_off_baseline,
        "mean_loss_spread": spread_summary,
        "max_loss_spread": (max(spreads) if spreads else NOT_COMPUTABLE),
        "headroom_rule": "headroom is the realised loss difference between "
                         "the best and the worst forced direction at one "
                         "branch; a branch with zero spread cannot be won "
                         "by any policy",
        "zero_spread_reason": (None if zero_spread == 0 else
                               "every direction pays the same in these "
                               "branches, so no policy can win there"),
        "per_arm": per_arm,
        "per_branch": rows,
    }


def network_forensics(document):
    """Question 1 + 3 for one four-arm network document."""
    arms = document.get("arms") or []
    per_arm, choice_by_decision, reasons = {}, {}, {}
    for row in arms:
        arm = str(row.get("arm"))
        log = row.get("action_log") or {}
        records = log.get("records") or []
        stats = {"decisions_logged": len(records),
                 "decisions_reported": log.get("count"),
                 "log_truncated": bool(log.get("truncated")),
                 "chosen_counts": {}, "reordered_decisions": 0,
                 "decisions_with_query_targets": 0,
                 "query_instants": [], "fallback_reasons": {},
                 "missing_directions_total": 0}
        for record in records:
            chosen = record.get("chosen")
            if chosen is not None:
                stats["chosen_counts"][chosen] = \
                    stats["chosen_counts"].get(chosen, 0) + 1
            if record.get("reordered"):
                stats["reordered_decisions"] += 1
            targets = record.get("query_targets") or {}
            if targets:
                stats["decisions_with_query_targets"] += 1
                for value in targets.values():
                    if _finite(value):
                        stats["query_instants"].append(round(float(value), 9))
            stats["missing_directions_total"] += len(
                record.get("missing_directions") or [])
            for term in record.get("missing_terms") or []:
                key = "unknown_term:" + str(term)
                stats["fallback_reasons"][key] = \
                    stats["fallback_reasons"].get(key, 0) + 1
            stats["fallback_reasons"]["direction_marked_fallback"] = \
                stats["fallback_reasons"].get("direction_marked_fallback", 0) \
                + len(record.get("fallback_directions") or [])
            stats["fallback_reasons"]["direction_missing"] = \
                stats["fallback_reasons"].get("direction_missing", 0) \
                + len(record.get("missing_directions") or [])
            choice_by_decision.setdefault(record.get("decision_id"), {})[arm] \
                = chosen
        stats["distinct_query_instants"] = sorted(set(stats["query_instants"]))
        stats["distinct_query_instant_count"] = len(
            stats.pop("distinct_query_instants"))
        stats["distinct_choices"] = sorted(stats["chosen_counts"])
        # The artifact own aggregate is kept as a CROSS-CHECK of the log,
        # never merged into it: merging the same records twice would double
        # every fallback count.
        reported = dict(row.get("fallback_reason_counts") or {})
        stats["reported_fallback_reason_counts"] = reported
        stats["fallback_reason_mismatch"] = bool(reported) and \
            reported != stats["fallback_reasons"]
        per_arm[arm] = stats
        reasons[arm] = dict(stats["fallback_reasons"])
    logged = sum(stats["decisions_logged"] for stats in per_arm.values())
    if logged == 0:
        # A per-decision log that was never recorded cannot support the
        # claim that the arms never disagreed.
        return {"schema": SCHEMA, "kind": "network", "arms": sorted(per_arm),
                "verdict": NOT_COMPUTABLE,
                "reason": "the artifact carries no per-decision action log; "
                          "re-run the cell with the action log enabled",
                "per_arm": per_arm}
    disagreement = 0
    differs_from_reference = {}
    for decision_id, choices in choice_by_decision.items():
        values = {c for c in choices.values() if c is not None}
        if len(values) > 1:
            disagreement += 1
        reference = choices.get("stale")
        for arm, chosen in choices.items():
            if arm == "stale" or chosen is None or reference is None:
                continue
            if chosen != reference:
                differs_from_reference[arm] = \
                    differs_from_reference.get(arm, 0) + 1
    decisions = len(choice_by_decision)
    return {
        "schema": SCHEMA,
        "kind": "network",
        "arms": sorted(per_arm),
        "decisions_compared": decisions,
        "decisions_where_arms_disagree": disagreement,
        "arm_disagreement_rule": "two arms disagree at a decision when the "
                                 "direction they applied differs",
        "differs_from_stale_arm": differs_from_reference,
        "per_arm": per_arm,
        "fallback_reasons_by_arm": reasons,
    }


def summarize(payloads):
    """Forensics for a list of (path, payload) pulled cell results."""
    out = {"schema": SCHEMA, "cells": [], "branches": [], "networks": []}
    for path, payload in payloads:
        document = document_of(payload)
        schema = document.get("schema")
        entry = {"path": str(path), "task": payload.get("task"),
                 "document_schema": schema}
        if schema == "t1-branch-block/v1":
            summary = branch_forensics(document)
            out["branches"].append({"path": str(path), "summary": summary})
            entry["branches"] = summary["branches"]
            entry["branches_without_arm_actions"] = \
                summary["branches_without_arm_actions"]
            entry["branches_where_all_arms_agree"] = \
                summary["branches_where_all_arms_agree"]
            entry["branches_with_zero_spread"] = \
                summary["branches_with_zero_spread"]
        elif schema == "network-alignment/v1":
            summary = network_forensics(document)
            out["networks"].append({"path": str(path), "summary": summary})
            if summary.get("verdict") == NOT_COMPUTABLE:
                entry["verdict"] = NOT_COMPUTABLE
                entry["reason"] = summary.get("reason")
            else:
                entry["decisions_compared"] = summary["decisions_compared"]
                entry["decisions_where_arms_disagree"] = \
                    summary["decisions_where_arms_disagree"]
        else:
            entry["verdict"] = NOT_COMPUTABLE
            entry["reason"] = "no forensics view for schema %r" % schema
        out["cells"].append(entry)
    return out


def _fmt(value):
    if value is None:
        return NOT_COMPUTABLE
    if isinstance(value, str):
        return value
    return "%.6g" % float(value)


def _render_branch(path, branch, lines):
    lines += ["## Branch: " + path, ""]
    if branch["branches_without_arm_actions"] == branch["branches"]:
        lines += ["NOT_COMPUTABLE: this artifact predates the per-arm action "
                  "record; re-run the branch cell. The headroom below still "
                  "comes from the recorded per-arm regrets.", ""]
    lines += ["| arms | branches | all arms agree | arms disagree | no action "
              "record | zero spread | oracle != baseline |",
              "|---|---|---|---|---|---|---|",
              "| %d | %d | %d | %d | %d | %d | %d |" % (
                  len(branch["per_arm"]), branch["branches"],
                  branch["branches_where_all_arms_agree"],
                  branch["branches_where_arms_disagree"],
                  branch["branches_without_arm_actions"],
                  branch["branches_with_zero_spread"],
                  branch["branches_where_oracle_differs_from_baseline"]),
              "",
              "mean realised loss spread across forced directions: %s "
              "(max %s)" % (_fmt(branch["mean_loss_spread"]),
                            _fmt(branch["max_loss_spread"])),
              "",
              "| arm | branches | chosen recorded | top1 hit rate | mean "
              "regret | zero-regret branches | mean kendall tau | differs "
              "from baseline |",
              "|---|---|---|---|---|---|---|---|"]
    for arm, stats in sorted(branch["per_arm"].items()):
        lines.append("| %s | %d | %d | %s | %s | %d | %s | %d |" % (
            arm, stats["branches"], stats["chosen_recorded"],
            _fmt(stats["top1_hit_rate"]), _fmt(stats["mean_regret"]),
            stats["zero_regret"], _fmt(stats["mean_kendall_tau"]),
            stats["differs_from_baseline"]))
    if branch.get("zero_spread_reason"):
        lines += ["", "> " + branch["zero_spread_reason"]]
    lines.append("")


def _render_network(path, data, lines):
    lines += ["## Network: " + path, ""]
    if data.get("verdict") == NOT_COMPUTABLE:
        lines += ["NOT_COMPUTABLE: " + str(data.get("reason")), ""]
        return
    lines += ["| arm | decisions | distinct choices | reordered | query "
              "instants | fallbacks by reason |",
              "|---|---|---|---|---|---|"]
    for arm, stats in sorted(data["per_arm"].items()):
        lines.append("| %s | %d | %d | %d | %d | %s |" % (
            arm, stats["decisions_logged"],
            len(stats["distinct_choices"]),
            stats["reordered_decisions"],
            stats["distinct_query_instant_count"],
            json.dumps(stats["fallback_reasons"], sort_keys=True)))
    lines += ["",
              "decisions where two arms applied different directions: %d / %d"
              % (data["decisions_where_arms_disagree"],
                 data["decisions_compared"]), ""]


def render_markdown(summary):
    lines = ["# T1 forensics (read-only, from pulled artifacts)", ""]
    for item in summary.get("branches") or []:
        _render_branch(item["path"], item["summary"], lines)
    for item in summary.get("networks") or []:
        _render_network(item["path"], item["summary"], lines)
    return "\n".join(lines) + "\n"


def headline(summary):
    """The two counters a reader must not have to recompute."""
    branch = summary.get("branches") or []
    return {
        "branch_cells": len(branch),
        "branches": sum(item["summary"]["branches"] for item in branch),
        "branches_with_zero_spread": sum(
            item["summary"]["branches_with_zero_spread"] for item in branch),
        "branches_where_arms_disagree": sum(
            item["summary"]["branches_where_arms_disagree"] for item in branch),
        "branches_without_arm_actions": sum(
            item["summary"]["branches_without_arm_actions"] for item in branch),
        "network_cells": len(summary.get("networks") or []),
        "network_cells_without_action_log": sum(
            1 for item in (summary.get("networks") or [])
            if item["summary"].get("verdict") == NOT_COMPUTABLE),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cell", action="append", default=[],
                        help="one pulled cell result.json; repeatable")
    parser.add_argument("--out", type=pathlib.Path, required=True)
    args = parser.parse_args(argv)
    payloads = [(pathlib.Path(p), load_result(p)) for p in args.cell]
    summary = summarize(payloads)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "forensics.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n")
    (args.out / "FORENSICS.md").write_text(render_markdown(summary))
    print(json.dumps(headline(summary), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
