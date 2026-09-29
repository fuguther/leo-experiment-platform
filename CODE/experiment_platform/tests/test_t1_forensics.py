"""Forensics over pulled artifacts: what the four arms did, and why it ties.

The point of these tests is the difference between MISSING and ZERO: an
artifact without a per-arm action record must never be summarised as "the
arms agreed".
"""
from __future__ import annotations

import json
import pathlib

import pytest

from CODE.experiment_platform import t1_forensics as fx
from CODE.experiment_platform import t1_tasks

PULLED = pathlib.Path("out/vm/t1-b6-f55c6fe/b_dev/cells")


def _legacy_branch(decision_id, *, baseline="N"):
    return {"decision_id": decision_id, "t_decision_start": float(decision_id),
            "baseline_chosen": baseline,
            "loss": {"stale": 0.5, "now": 0.5, "common": 0.5,
                     "candidate": 0.5},
            "regret": {"stale": 0.0, "now": 0.0, "common": 0.0,
                       "candidate": 0.0},
            "counts": {"candidates": 2, "valid_pairs": 2, "invalid_pairs": 0},
            "oracle": {"arm": "oracle", "chosen": baseline, "loss": 0.5,
                       "regret": 0.0}}


def _document(branches):
    return {"schema": "t1-branch-block/v1", "branches": branches}


def _arm_record(decision_id, arm, chosen, *, order=None, queried=True,
                missing_terms=(), fallback=(), missing=()):
    return {"decision_id": decision_id, "t_decision_start": float(decision_id),
            "arm": arm, "chosen": chosen,
            "applied_order": list(order or [chosen]),
            "scorer_ranking": list(order or [chosen]),
            "reordered": bool(order) and list(order) != sorted(order),
            "query_targets": ({"N": 1.0} if queried else {}),
            "fallback_directions": list(fallback),
            "missing_directions": list(missing),
            "missing_terms": list(missing_terms),
            "inference_policy_choice": None, "query": None}


def _network_row(arm, records):
    return {"arm": arm,
            "action_log": {"records": records, "count": len(records),
                           "limit": 500, "truncated": False},
            "fallback_reason_counts": {}}


# ------------------------------------------------------------ kendall tau
def test_kendall_tau_b_is_one_for_an_agreeing_ranking():
    predicted = {"N": 1.0, "S": 2.0}
    realised = {"N": 0.1, "S": 0.9}
    assert fx.kendall_tau_b(predicted, realised) == pytest.approx(1.0)


def test_kendall_tau_b_is_minus_one_for_an_inverted_ranking():
    predicted = {"N": 1.0, "S": 2.0}
    realised = {"N": 0.9, "S": 0.1}
    assert fx.kendall_tau_b(predicted, realised) == pytest.approx(-1.0)


def test_kendall_tau_b_is_undefined_when_every_direction_ties():
    predicted = {"N": 0.5, "S": 0.5}
    realised = {"N": 0.3, "S": 0.3}
    assert fx.kendall_tau_b(predicted, realised) is None


# --------------------------------------------------- branch: headroom
def test_a_legacy_block_reports_headroom_and_refuses_to_invent_choices():
    summary = fx.branch_forensics(_document([_legacy_branch(4),
                                             _legacy_branch(9)]))
    assert summary["branches"] == 2
    assert summary["branches_without_arm_actions"] == 2
    assert summary["branches_where_oracle_differs_from_baseline"] == 0
    assert summary["arms_with_action_records"] == 0
    assert sorted(summary["per_arm"]) == ["candidate", "common", "now", "stale"]
    for stats in summary["per_arm"].values():
        # NOT 0: the artifact never recorded a choice, so a hit rate of 0
        # would be a claim the artifact cannot support.
        assert stats["top1_hit_rate"] == fx.NOT_COMPUTABLE
        assert stats["chosen_recorded"] == 0
        assert stats["mean_regret"] == 0.0
        assert stats["zero_regret"] == 2


def test_branch_forensics_separates_the_arms_that_actually_differed():
    branch = _legacy_branch(4, baseline="N")
    branch["arm_actions"] = {
        "stale": {"chosen": "N", "ranking": ["N", "S"],
                  "predicted_total_s": {"N": 1.0, "S": 2.0},
                  "fallback_directions": [], "missing_directions": [],
                  "missing_terms": []},
        "candidate": {"chosen": "S", "ranking": ["S", "N"],
                      "predicted_total_s": {"N": 2.0, "S": 1.0},
                      "fallback_directions": [], "missing_directions": [],
                      "missing_terms": []}}
    branch["candidate_outcomes"] = {
        "N": {"valid": True, "loss": 0.6, "censored": False},
        "S": {"valid": True, "loss": 0.3, "censored": False}}
    branch["oracle"] = {"arm": "oracle", "chosen": "S", "loss": 0.3,
                        "regret": 0.0}
    summary = fx.branch_forensics(_document([branch]))
    assert summary["branches_where_arms_disagree"] == 1
    assert summary["branches_where_all_arms_agree"] == 0
    assert summary["branches_without_arm_actions"] == 0
    assert summary["branches_where_oracle_differs_from_baseline"] == 1
    assert summary["mean_loss_spread"] == pytest.approx(0.3)
    assert summary["per_arm"]["candidate"]["top1_hit_rate"] == 1.0
    assert summary["per_arm"]["candidate"]["differs_from_baseline"] == 1
    assert summary["per_arm"]["stale"]["top1_hits"] == 0
    assert summary["per_arm"]["stale"]["chosen_counts"] == {"N": 1}


def test_zero_loss_spread_is_a_finding_not_a_missing_value():
    branch = _legacy_branch(4, baseline="N")
    branch["arm_actions"] = {
        "stale": {"chosen": "N", "ranking": ["N", "S"],
                  "predicted_total_s": {"N": 1.0, "S": 2.0},
                  "fallback_directions": [], "missing_directions": [],
                  "missing_terms": []}}
    branch["candidate_outcomes"] = {
        "N": {"valid": True, "loss": 0.25, "censored": False},
        "S": {"valid": True, "loss": 0.25, "censored": False}}
    summary = fx.branch_forensics(_document([branch]))
    assert summary["branches_with_zero_spread"] == 1
    assert summary["mean_loss_spread"] == 0.0
    assert summary["zero_spread_reason"] is not None
    # every realised loss ties, so the rank agreement is undefined
    assert summary["per_arm"]["stale"]["mean_kendall_tau"] == fx.NOT_COMPUTABLE


def test_a_censored_candidate_is_not_a_loss():
    branch = _legacy_branch(4, baseline="N")
    branch["candidate_outcomes"] = {
        "N": {"valid": True, "loss": 0.4, "censored": False},
        "S": {"valid": True, "loss": None, "censored": True,
              "censor_reason": "observation window 0.2s < deadline 1s"}}
    summary = fx.branch_forensics(_document([branch]))
    assert summary["per_branch"][0]["realised_loss"] == {"N": 0.4}
    assert summary["per_branch"][0]["censored_directions"] == ["S"]


# --------------------------------------------------- network: action log
def test_a_network_block_without_an_action_log_is_not_computable():
    document = {"schema": "network-alignment/v1",
                "arms": [{"arm": "stale"}, {"arm": "candidate"}]}
    summary = fx.network_forensics(document)
    assert summary["verdict"] == fx.NOT_COMPUTABLE
    assert "decisions_where_arms_disagree" not in summary


def test_network_forensics_counts_disagreement_and_fallback_reasons():
    document = {"schema": "network-alignment/v1", "arms": [
        _network_row("stale", [_arm_record(1, "stale", "N"),
                               _arm_record(2, "stale", "N")]),
        _network_row("candidate", [
            _arm_record(1, "candidate", "N", order=["N", "S"],
                        missing_terms=["query_instant_unknown"],
                        fallback=["S"]),
            _arm_record(2, "candidate", "S")])]}
    summary = fx.network_forensics(document)
    assert summary["decisions_compared"] == 2
    assert summary["decisions_where_arms_disagree"] == 1
    assert summary["differs_from_stale_arm"] == {"candidate": 1}
    reasons = summary["fallback_reasons_by_arm"]["candidate"]
    assert reasons["unknown_term:query_instant_unknown"] == 1
    assert reasons["direction_marked_fallback"] == 1
    assert summary["per_arm"]["candidate"]["distinct_query_instant_count"] == 1


# ------------------------------------------------- t1_tasks field builders
def test_arm_actions_keeps_the_predicted_scores_and_the_missing_terms():
    actions = t1_tasks._arm_actions({"stale": {
        "chosen": "N", "ranking": ["N", "S"],
        "scores": {"N": {"total_s": 1.0, "missing": []},
                   "S": {"total_s": None,
                         "missing": ["resource_never_appears_in_this_branch"]}},
        "fallback_directions": ["S"], "missing_directions": ["S"]}})
    assert actions["stale"]["chosen"] == "N"
    assert actions["stale"]["predicted_total_s"] == {"N": 1.0, "S": None}
    assert actions["stale"]["missing_terms"] == [
        "resource_never_appears_in_this_branch"]
    assert actions["stale"]["fallback_directions"] == ["S"]


def test_candidate_outcomes_keeps_a_censored_loss_at_none():
    outcomes = t1_tasks._candidate_outcomes({
        "N": {"valid": True, "loss": 0.4, "censored": False,
              "taken_in_baseline": True},
        "S": {"valid": True, "loss": None, "censored": True,
              "censor_reason": "window"}})
    assert outcomes["N"]["loss"] == 0.4
    assert outcomes["S"]["loss"] is None and outcomes["S"]["censored"] is True


def test_fallback_reason_counts_name_the_missing_term():
    counts = t1_tasks._fallback_reason_counts({"records": [
        {"missing_terms": ["query_instant_unknown"],
         "fallback_directions": ["S"], "missing_directions": []}]})
    assert counts["unknown_term:query_instant_unknown"] == 1
    assert counts["direction_marked_fallback"] == 1
    assert counts["direction_missing"] == 0


# ------------------------------------------------------------ integration
@pytest.mark.skipif(not (PULLED / "b-fixed_hotspot-branch-seed-7").exists(),
                    reason="pulled VM artifacts are not present")
def test_the_pulled_b_round_block_has_zero_headroom():
    payload = json.loads((PULLED / "b-fixed_hotspot-branch-seed-7"
                          / "result.json").read_text())
    summary = fx.branch_forensics(fx.document_of(payload))
    assert summary["branches"] >= 1
    assert summary["branches_where_oracle_differs_from_baseline"] == 0
    for stats in summary["per_arm"].values():
        assert stats["zero_regret"] == stats["branches"]


@pytest.mark.skipif(not (PULLED / "b-fixed_hotspot-branch-seed-7").exists(),
                    reason="pulled VM artifacts are not present")
def test_summarize_lists_every_cell_and_never_overwrites_one():
    paths = [PULLED / "b-fixed_hotspot-branch-seed-7" / "result.json",
             PULLED / "b-burst_hotspot-branch-seed-7" / "result.json"]
    payloads = [(path, json.loads(path.read_text())) for path in paths]
    summary = fx.summarize(payloads)
    assert len(summary["branches"]) == 2
    assert len(summary["cells"]) == 2
    head = fx.headline(summary)
    assert head["branch_cells"] == 2
    assert head["branches"] == 6

