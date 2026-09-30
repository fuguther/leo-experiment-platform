"""A3: the development SELECTION and statistical FREEZING layer.

Every behaviour here is a requirement of the task book item A3, and the
requirements that decide the SHAPE of CODE/experiment_platform/design_selection.py
(not just one number) are the ones with the most tests:

* the five PRE-DECLARED horizon candidates are genuinely evaluated (mean ETA
  offset, median ETA offset, and the p25/p50/p75 fixed horizons of the
  DEVELOPMENT-baseline candidate ETA-offset population).  A quantile taken from
  confirmation data is refused, because it would let the confirmation set
  choose its own hypothesis;
* common_strong is selected on the development blocks by the mean primary loss,
  ties broken toward the median-horizon candidate and then by the declared list
  order, and what gets frozen is the RULE or the fixed h plus its quantile
  source, the block set, the loss table and their sha256 values - never a bare
  frozen: true;
* tie, zero_variance, insufficient_scenario_coverage and
  insufficient_statistical_evidence are SEPARATE, independently visible
  outcomes, and a degenerate (zero-variance) development sample produces the
  BOUNDED precision branch instead of an infinite or perfect n;
* one deadline D per SCENARIO (a pre-specified business deadline, or 2 x p95 of
  the development-baseline legal-candidate delivery sample).  A per-branch
  derived D is refused;
* freeze_selected_design / load_selected_design round-trip every binding and
  refuse a file that is missing one or whose embedded hashes do not recompute;
* check_confirmation_matrix refuses a short seed list, a repeated or
  dev-overlapping confirmation seed, and a block that does not list every
  arm/mode it needs.

The synthetic blocks below are built from the shape the T1 comparison actually
records per cell (CODE/experiment_platform/time_alignment_compare.py:
scenario id, trace identity, seed, the arm/mode of the branch, the primary arms'
normalized loss, censoring and mismatch accounting), with the evaluation section
under an explicit "eval" key that FAILS LOUDLY when it is missing - guessing a
score would be worse than refusing the block.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

import pytest

from CODE.experiment_platform import design_selection as ds

#: The five pre-declared candidates, verbatim from the contract
#: (CODE/work/WP-T1-COMPLETE/contract.yaml -> common_horizon_dev_candidates).
FIVE = ["mean_eta_offset", "median_eta_offset", "p25_offset", "p50_offset",
        "p75_offset"]

#: The pool the quantiles are taken from in every fixture: sorted, it is
#: [1, 2, 3, 4, 5, 6, 7, 8], so
#: p25 = 2.75, p50 = 4.5, p75 = 6.25 and the mean = 4.5.
OFFSETS = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]

DEV_SEEDS = [7, 11, 23, 42]
#: Confirmation seeds start at the contract's confirm_seeds_start (1001) and
#: are taken consecutively; there are at least DEFAULT_MIN_N (20) of them so the
#: frozen n can actually be met without reusing a development trace.
CONFIRM_SEEDS = list(range(1001, 1021))


def _block(scenario, trace, seed, arm, mode, loss, **eval_over):
    """One development branch block, in the shape the suite records per cell."""
    evaluation = {
        "status": "delivered",
        "loss": loss,
        "legal": True,
        "deadline_s": 2.0,
        "latency_s": 1.0 if loss is not None else None,
        "delivered": loss is not None,
        "censored": False,
        "failed": False,
        "resource_mismatch": False,
    }
    evaluation.update(eval_over)
    return {
        "scenario_id": scenario,
        "trace_id": f"{scenario}-trace",
        "seed": seed,
        "arm": arm,
        "mode": mode,
        "phase": "development",
        "eval": evaluation,
    }


def _dev_blocks():
    """Four development blocks: two scenarios x two traces, all three arms."""
    blocks = []
    for scenario in ("s1", "s2"):
        for trace, seed in (("t1", 7), ("t2", 11)):
            for arm in ("stale", "common", "candidate"):
                blocks.append(_block(scenario, trace, seed, arm, "per_packet",
                                     0.4))
    return blocks


def _eval_blocks(baseline_loss=0.4, candidate_loss=0.4, phase="development"):
    blocks = _dev_blocks()
    for block in blocks:
        block["phase"] = phase
        block["eval"]["loss"] = (baseline_loss if block["arm"] == "stale"
                                 else candidate_loss)
    return blocks


def _scored(value, **over):
    evaluation = {"status": "delivered", "loss": value, "legal": True,
                  "deadline_s": 2.0, "latency_s": value, "delivered": True,
                  "censored": False, "failed": False,
                  "resource_mismatch": False}
    evaluation.update(over)
    return evaluation


def _scored_blocks(**losses):
    """Three blocks where only the 'candidate' arm carries a given loss."""
    default = losses.get("default", 0.5)
    blocks = []
    for index, seed in enumerate((7, 11, 23)):
        block = _block("s1", "t1", seed, "candidate", "per_packet", default)
        block["eval"] = _scored(losses.get(f"b{index}", default))
        blocks.append(block)
    return blocks


def _horizons(offsets=None):
    return ds.build_candidate_horizons(offsets if offsets is not None
                                       else OFFSETS)


#: candidate -> (kind, frozen horizon) in the shape build_candidate_horizons
#: produces: three fixed-h candidates and two per-decision rules.
_CANDIDATE_KIND = {
    "mean_eta_offset": ("rule", None),
    "median_eta_offset": ("rule", None),
    "p25_offset": ("fixed_h", 2.75),
    "p50_offset": ("fixed_h", 4.5),
    "p75_offset": ("fixed_h", 6.25),
}


def _table_with(blocks: int, per_candidate: dict):
    """A hand-built loss table: {candidate: {block: mean-loss}}."""
    losses, candidates = {}, []
    for name in FIVE:
        values = per_candidate.get(name, [1.0] * blocks)
        kind, horizon = _CANDIDATE_KIND[name]
        candidates.append({"candidate": name, "kind": kind,
                           "horizon_s": horizon, "quantile": None,
                           "quantile_source": "development_baseline",
                           "scored_blocks": len(values),
                           "mean_loss": sum(values) / len(values),
                           "variance": 0.0})
        losses[name] = {f"b{i}": float(v) for i, v in enumerate(values)}
    return {"schema": "t1-design-loss-table/v1", "candidates": candidates,
            "blocks": [{"unit": f"b{i}-blk"} for i in range(blocks)],
            "losses": losses, "unscored": []}


def _loss_table(**per_candidate):
    return _table_with(3, per_candidate)


def _two_block_table(**per_candidate):
    """The same table with two blocks, so a scenario list can name both."""
    return _table_with(2, per_candidate)


def _scenario_deadlines(blocks, value=2.0):
    scenarios = []
    for block in blocks:
        if block["scenario_id"] not in scenarios:
            scenarios.append(block["scenario_id"])
    return {scenario: {"scenario_id": scenario, "deadline_s": value,
                       "source": "declared_business_deadline",
                       "legal_samples": 4, "frozen": True}
            for scenario in scenarios}


def _selection(**over):
    scores = over.pop("scores", None) or {"mean_eta_offset": [0.5, 0.5],
                                          "median_eta_offset": [0.4, 0.4],
                                          "p25_offset": [0.6, 0.6],
                                          "p50_offset": [0.6, 0.6],
                                          "p75_offset": [0.6, 0.6]}
    loss_table = _loss_table(**scores)
    selection = ds.select_common_strong(loss_table, declared_order=FIVE)
    selection["loss_table"] = loss_table
    selection["loss_table_sha256"] = ds.loss_table_sha256(loss_table)
    selection.update(over)
    return selection


#: The default freeze destination for tests that only inspect the document.
TMP_FREEZE = Path(tempfile.mkdtemp(prefix="design-selection-freeze-"))


def _freeze(tmp_path, blocks=None, selection=None, **over):
    blocks = _dev_blocks() if blocks is None else blocks
    payload = {
        "selection": _selection() if selection is None else selection,
        "deadline": _scenario_deadlines(blocks),
        "declared_business_deadlines": {scenario: record["deadline_s"]
                                        for scenario, record
                                        in _scenario_deadlines(blocks).items()},
        "blocks": blocks,
        "confirm_seeds": CONFIRM_SEEDS,
        "sample_size": {"n": 20, "per_scenario": True},
        "out_path": tmp_path / "selected_design.json",
    }
    payload.update(over)
    return ds.freeze_selected_design(**payload)


# ---------------------------------------------------------------------------
# build_candidate_horizons: the five pre-declared candidates, real values
# ---------------------------------------------------------------------------
def test_the_five_candidates_are_real_values_from_the_development_population():
    horizons = _horizons()
    assert [c["candidate"] for c in horizons["candidates"]] == FIVE
    by_name = {c["candidate"]: c for c in horizons["candidates"]}
    # the quantiles must come from the development baseline population
    assert by_name["p25_offset"]["horizon_s"] == pytest.approx(2.75)
    assert by_name["p50_offset"]["horizon_s"] == pytest.approx(4.5)
    assert by_name["p75_offset"]["horizon_s"] == pytest.approx(6.25)
    assert by_name["mean_eta_offset"]["horizon_s"] == pytest.approx(4.5)
    assert by_name["median_eta_offset"]["horizon_s"] == pytest.approx(4.5)
    assert horizons["quantile_source"] == "development_baseline"
    assert horizons["population_size"] == len(OFFSETS)
    assert horizons["pooled_offsets_sha256"] == hashlib.sha256(
        json.dumps(OFFSETS, sort_keys=True).encode("utf-8")).hexdigest()


def test_a_confirmation_derived_quantile_is_refused():
    with pytest.raises(ds.SelectionError) as caught:
        ds.build_candidate_horizons(
            {"eta_offsets": OFFSETS, "source": "confirmation_baseline",
             "analysis_phase": "confirmation"})
    message = str(caught.value)
    assert "confirmation" in message
    assert "development" in message


def test_a_pool_without_a_development_provenance_is_refused():
    with pytest.raises(ds.SelectionError) as caught:
        ds.build_candidate_horizons({"eta_offsets": OFFSETS,
                                     "source": "unknown"})
    assert "source" in str(caught.value)


def test_an_empty_offset_population_is_refused():
    with pytest.raises(ds.SelectionError) as caught:
        ds.build_candidate_horizons([])
    assert "eta_offsets" in str(caught.value)


# ---------------------------------------------------------------------------
# evaluate_candidates: a real loss table, with reasons for unscorable blocks
# ---------------------------------------------------------------------------
def test_every_candidate_is_scored_for_every_block():
    table = ds.evaluate_candidates(_horizons(), _dev_blocks())
    assert [c["candidate"] for c in table["candidates"]] == FIVE
    assert len(table["blocks"]) == len(_dev_blocks())
    for name in FIVE:
        assert set(table["losses"][name]) == {
            b["unit"] for b in table["blocks"]}
        assert all(value == pytest.approx(0.4)
                   for value in table["losses"][name].values())
        entry = next(c for c in table["candidates"] if c["candidate"] == name)
        assert entry["scored_blocks"] == len(_dev_blocks())
        assert entry["unscored_blocks"] == 0
        assert entry["variance"] == pytest.approx(0.0)


def test_a_confirmation_block_cannot_enter_the_development_loss_table():
    blocks = _dev_blocks()
    blocks[0]["phase"] = "confirmation"
    with pytest.raises(ds.SelectionError) as caught:
        ds.evaluate_candidates(_horizons(), blocks)
    assert "development" in str(caught.value)


def test_a_latency_derived_block_is_scored_against_the_scenario_deadline():
    blocks = _scored_blocks()
    for block in blocks:
        block["eval"].pop("loss")
        block["eval"]["latency_s"] = 0.5 * block["eval"]["deadline_s"]
    table = ds.evaluate_candidates(_horizons(), blocks,
                                   deadline={"s1": 2.0})
    assert not table["unscored"]
    for name in FIVE:
        assert all(value == pytest.approx(0.5)
                   for value in table["losses"][name].values())


def test_a_latency_beyond_the_deadline_is_a_timeout_not_a_branch_scale():
    blocks = _scored_blocks()
    for block in blocks:
        block["eval"].pop("loss")
        block["eval"]["latency_s"] = 2.0 * block["eval"]["deadline_s"]
    table = ds.evaluate_candidates(_horizons(), blocks, deadline={"s1": 2.0})
    assert not table["unscored"]
    for name in FIVE:
        assert set(table["losses"][name].values()) == {1.0}
        assert all(block == 1.0 for block in table["losses"][name].values())


def test_a_block_scored_against_another_deadline_is_refused():
    blocks = _scored_blocks()
    blocks[0]["eval"].pop("loss")
    blocks[0]["eval"]["latency_s"] = 0.5
    blocks[0]["eval"]["deadline_s"] = 1.0  # not the scenario's 2.0
    with pytest.raises(ds.SelectionError) as caught:
        ds.evaluate_candidates(_horizons(), blocks, deadline={"s1": 2.0})
    assert "per-branch" in str(caught.value)


def test_mismatched_censored_and_failed_blocks_are_listed_never_dropped():
    blocks = _scored_blocks(b0=0.4, b1=0.5, b2=0.6)
    extra = []
    for index, (status, over) in enumerate((
            ("censored", {"censored": True, "latency_s": None}),
            ("failed", {"failed": True, "loss": 1.0}),
            ("resource_mismatch", {"resource_mismatch": True}))):
        block = _block("s9", "t9", 90 + index, "candidate", "per_packet", 0.7)
        block["eval"] = _scored(0.7, status=status, **over)
        extra.append(block)
    table = ds.evaluate_candidates(_horizons(), blocks + extra)
    # every block is ACCOUNTED FOR exactly once: scored or listed, never lost
    assert len(table["blocks"]) + len(table["unscored"]) == \
        len(blocks) + len(extra)
    assert len(table["unscored"]) == len(extra)
    reasons = {item["unit"]: item["reason"] for item in table["unscored"]}
    assert len(reasons) == len(extra)
    for reason in reasons.values():
        assert reason
    for name in FIVE:
        entry = next(c for c in table["candidates"] if c["candidate"] == name)
        assert entry["scored_blocks"] == len(blocks)
        assert entry["unscored_blocks"] == len(extra)
    # a delivered-subset latency never stands in for a missing loss
    assert all(len(table["losses"][name]) == len(blocks) for name in FIVE)


def test_a_delivered_block_with_no_loss_is_refused_not_guessed():
    blocks = _scored_blocks()
    blocks[0]["eval"]["loss"] = None
    with pytest.raises(ds.SelectionError) as caught:
        ds.evaluate_candidates(_horizons(), blocks)
    assert "loss" in str(caught.value)


# ---------------------------------------------------------------------------
# select_common_strong: the rule, the tie-break, and the four outcomes
# ---------------------------------------------------------------------------
def test_a_tie_selects_the_median_horizon_candidate():
    result = ds.select_common_strong(
        _loss_table(), declared_order=FIVE)
    assert result["selected"] == "median_eta_offset"
    assert result["outcomes"]["tie"] is True
    assert result["tied_candidates"] == FIVE
    assert result["outcomes"]["zero_variance"] is True
    assert result["loss_table_sha256"]
    assert result["rule"]["kind"] == "rule"


def test_the_declared_order_breaks_a_tie_without_the_median_candidate():
    # no median-horizon candidate is declared, so the declared order decides
    order = ["p75_offset", "p25_offset"]
    result = ds.select_common_strong(_loss_table(), declared_order=order)
    assert result["selected"] == "p75_offset"
    assert result["outcomes"]["tie"] is True
    assert result["tie_break_applied"]["winner"] == "p75_offset"


def test_the_median_candidate_is_preferred_over_its_position_in_the_order():
    order = ["p75_offset", "median_eta_offset", "mean_eta_offset",
             "p25_offset", "p50_offset"]
    result = ds.select_common_strong(_loss_table(), declared_order=order)
    assert result["selected"] == "median_eta_offset"


def test_the_mean_primary_loss_picks_a_clear_winner():
    result = ds.select_common_strong(
        _loss_table(mean_eta_offset=[0.9, 0.9],
                    median_eta_offset=[0.1, 0.1],
                    p25_offset=[0.5, 0.5], p50_offset=[0.5, 0.5],
                    p75_offset=[0.5, 0.5]), declared_order=FIVE)
    assert result["selected"] == "median_eta_offset"
    assert result["outcomes"]["tie"] is False
    assert result["mean_loss"]["median_eta_offset"] == pytest.approx(0.1)


def test_zero_variance_is_visible_without_any_tie():
    # median_eta_offset wins every block by exactly 0.1: the winner is unique
    # AND its development sample carries no dispersion, so both facts show.
    result = ds.select_common_strong(
        _loss_table(mean_eta_offset=[0.5, 0.5, 0.5],
                    median_eta_offset=[0.4, 0.4, 0.4],
                    p25_offset=[0.5, 0.5, 0.5],
                    p50_offset=[0.5, 0.5, 0.5],
                    p75_offset=[0.5, 0.5, 0.5]), declared_order=FIVE,
        block_scenarios=["s1", "s1", "s2"])
    assert result["outcomes"]["tie"] is False
    assert result["outcomes"]["zero_variance"] is True
    assert result["outcomes"]["insufficient_statistical_evidence"] is True
    assert result["outcomes"]["insufficient_scenario_coverage"] is False


def test_a_single_scenario_is_insufficient_scenario_coverage():
    result = ds.select_common_strong(
        _two_block_table(mean_eta_offset=[0.9, 0.9],
                         median_eta_offset=[0.1, 0.1],
                         p25_offset=[0.5, 0.5], p50_offset=[0.5, 0.5],
                         p75_offset=[0.5, 0.5]), declared_order=FIVE,
        block_scenarios=["s1", "s1"])
    assert result["outcomes"]["insufficient_scenario_coverage"] is True
    assert result["outcomes"]["tie"] is False


def test_two_scenarios_have_adequate_scenario_coverage():
    result = ds.select_common_strong(
        _two_block_table(mean_eta_offset=[0.9, 0.9],
                         median_eta_offset=[0.1, 0.1],
                         p25_offset=[0.5, 0.5], p50_offset=[0.5, 0.5],
                         p75_offset=[0.5, 0.5]), declared_order=FIVE,
        block_scenarios=["s1", "s2"])
    assert result["outcomes"]["insufficient_scenario_coverage"] is False
    assert result["block_scenarios"] == ["s1", "s2"]


def test_a_scenario_list_that_does_not_cover_every_block_is_refused():
    with pytest.raises(ds.SelectionError) as caught:
        ds.select_common_strong(_loss_table(), declared_order=FIVE,
                                block_scenarios=["s1", "s2"])
    assert "block_scenarios" in str(caught.value)


def test_an_undeclared_candidate_cannot_win_a_tie():
    table = _loss_table()
    table["candidates"].append({"candidate": "p99_offset", "kind": "fixed_h",
                                "horizon_s": 9.0, "scored_blocks": 3,
                                "mean_loss": 1.0, "variance": 0.0})
    table["losses"]["p99_offset"] = {"b0": 1.0, "b1": 1.0, "b2": 1.0}
    result = ds.select_common_strong(table, declared_order=FIVE)
    # p99_offset is tied with mean_eta_offset/p25/p50/p75 but is NOT declared,
    # so the declared order decides among the declared names
    assert result["selected"] == "median_eta_offset"
    assert "p99_offset" in result["tied_candidates"]


def test_the_four_outcomes_are_independent_flags():
    result = ds.select_common_strong(
        _loss_table(mean_eta_offset=[0.5, 0.4, 0.6],
                    median_eta_offset=[0.4, 0.4, 0.5],
                    p25_offset=[0.5, 0.4, 0.6], p50_offset=[0.5, 0.4, 0.6],
                    p75_offset=[0.5, 0.4, 0.6]),
        declared_order=FIVE, block_scenarios=["s1", "s1", "s2"],
        minimum_development_blocks=20)
    outcomes = result["outcomes"]
    assert set(outcomes) >= {"tie", "zero_variance",
                             "insufficient_scenario_coverage",
                             "insufficient_statistical_evidence"}
    assert outcomes["tie"] is False
    assert outcomes["insufficient_statistical_evidence"] is True


def test_a_candidate_with_no_scored_block_cannot_be_selected():
    table = _loss_table()
    table["losses"]["p25_offset"] = {}
    result = ds.select_common_strong(table, declared_order=FIVE)
    assert result["selected"] != "p25_offset"
    assert [row["candidate"] for row in result["unscorable"]] == \
        ["p25_offset"]
    assert result["unscorable"][0]["reason"]


# ---------------------------------------------------------------------------
# one deadline D per scenario; a per-branch derived D is refused
# ---------------------------------------------------------------------------
def test_all_arms_of_one_scenario_share_one_deadline():
    blocks = _eval_blocks()
    deadlines = ds.resolve_scenario_deadlines(
        blocks, development_delivery_samples={"s1": [1.0, 1.5, 2.0, 3.0],
                                              "s2": [1.0, 1.0, 1.0, 1.0]})
    assert set(deadlines) == {"s1", "s2"}
    # D = 2 x p95 (linear interpolation), per scenario, shared by every arm
    assert deadlines["s1"]["deadline_s"] == pytest.approx(5.7)
    assert deadlines["s1"]["source"] == "p95_times_multiplier"
    assert deadlines["s1"]["multiplier"] == 2.0
    assert deadlines["s1"]["legal_samples"] == 4
    assert deadlines["s2"]["deadline_s"] == pytest.approx(2.0)
    assert deadlines["s1"]["branches_seen"] == [2.0]
    assert deadlines["s1"]["branch_deadline_conflicts"] is False


def test_a_scenario_with_no_deadline_input_at_all_is_refused():
    with pytest.raises(ds.SelectionError) as caught:
        ds.resolve_scenario_deadlines(_eval_blocks(), development_delivery_samples={
            "s1": [1.0, 1.5, 2.0, 3.0]})
    message = str(caught.value)
    assert "s2" in message
    assert "branch" in message


def test_a_pre_specified_business_deadline_wins_over_the_p95_rule():
    blocks = _eval_blocks()
    deadlines = ds.resolve_scenario_deadlines(
        blocks, development_delivery_samples={"s1": [1.0, 1.5],
                                              "s2": [1.0, 1.5]},
        declared_business_deadlines={"s1": 4.0})
    assert deadlines["s1"]["deadline_s"] == pytest.approx(4.0)
    assert deadlines["s1"]["source"] == "declared_business_deadline"


def test_a_frozen_deadline_needs_no_fresh_derivation():
    # a confirmation-side call hands in the frozen D and nothing else: the
    # frozen value is the authority and must not be re-derived from a branch
    deadlines = ds.resolve_scenario_deadlines(
        _eval_blocks(), frozen={"s1": 2.0, "s2": 2.0})
    assert deadlines["s1"]["deadline_s"] == pytest.approx(2.0)
    assert deadlines["s1"]["source"] == "frozen_development_selection"
    assert deadlines["s1"]["frozen"] is True
    assert deadlines["s1"]["branches_seen"] == [2.0]


def test_a_frozen_deadline_that_disagrees_with_the_derivation_is_refused():
    with pytest.raises(ds.SelectionError) as caught:
        ds.resolve_scenario_deadlines(
            _eval_blocks(),
            development_delivery_samples={"s1": [1.0, 1.5, 2.0, 3.0]},
            frozen={"s1": 1.0, "s2": 2.0})
    assert "frozen" in str(caught.value)


def test_a_per_branch_derived_deadline_is_refused():
    blocks = _eval_blocks()
    blocks[0]["eval"]["deadline_s"] = 1.0
    with pytest.raises(ds.SelectionError) as caught:
        ds.resolve_scenario_deadlines(blocks)
    message = str(caught.value)
    assert "scenario" in message
    assert "branch" in message


def test_a_branch_that_declares_its_own_deadline_source_is_refused():
    blocks = _eval_blocks()
    blocks[0]["eval"]["deadline_source"] = "branch_p95"
    with pytest.raises(ds.SelectionError) as caught:
        ds.resolve_scenario_deadlines(blocks)
    assert "branch" in str(caught.value)


# ---------------------------------------------------------------------------
# precision planning: bounded when the development variance is zero
# ---------------------------------------------------------------------------
def test_zero_variance_gives_the_bounded_branch_not_an_infinite_n():
    plan = ds.plan_precision([0.1, 0.1, 0.1, 0.1])
    assert plan["bounded"] is True
    assert plan["zero_variance"] is True
    assert plan["n"] is None
    assert "cannot" in plan["reason"]
    assert plan["bounded_design"]["needed"]
    assert plan["planning_approximation"] is True
    # a strict JSON dump: an infinite n or a non-finite width would raise here
    assert json.dumps(plan, allow_nan=False)


def test_the_precision_sample_is_the_paired_difference_to_the_rival():
    # a clear winner whose advantage varies block by block: the dispersion that
    # matters is the paired difference, not the candidate's own spread
    table = _loss_table()
    table["losses"]["median_eta_offset"] = {"b0": 0.30, "b1": 0.30, "b2": 0.30}
    table["losses"]["mean_eta_offset"] = {"b0": 0.35, "b1": 0.45, "b2": 0.40}
    table["losses"]["p25_offset"] = {"b0": 0.36, "b1": 0.46, "b2": 0.41}
    table["losses"]["p50_offset"] = {"b0": 0.37, "b1": 0.47, "b2": 0.42}
    table["losses"]["p75_offset"] = {"b0": 0.38, "b1": 0.48, "b2": 0.43}
    for row in table["candidates"]:
        values = list(table["losses"][row["candidate"]].values())
        row["scored_blocks"] = len(values)
        row["mean_loss"] = sum(values) / len(values)
        row["variance"] = 0.0
    selection = ds.select_common_strong(table, declared_order=FIVE,
                                        block_scenarios=["s1", "s2", "s3"])
    selection["loss_table"] = table
    selection["loss_table_sha256"] = ds.loss_table_sha256(table)
    frozen = _freeze(TMP_FREEZE, selection=selection)
    assert frozen["precision"]["observed_std"] > 0.0
    assert frozen["precision"]["paired_with"]
    assert frozen["precision"]["n"] >= ds.t1_stats.DEFAULT_MIN_N
    assert "paired" in frozen["precision"]["paired_difference_definition"]


def test_floating_point_dust_is_treated_as_zero_variance_not_as_dispersion():
    # 0.41 - 0.41 is not exactly 0.0 in binary floating point; that dust must
    # take the BOUNDED branch instead of sizing a confirmation run on it
    dust = [0.41000000000000003 - 0.41 for _ in range(4)]
    assert any(value != 0.0 for value in dust)
    plan = ds.plan_precision(dust)
    assert plan["zero_variance"] is True
    assert plan["bounded"] is True
    assert plan["n"] is None
    assert plan["observed_std"] == 0.0


def test_a_nonzero_variance_gives_the_planned_n_and_says_it_is_an_approximation():
    plan = ds.plan_precision([0.0, 0.1, 0.2, 0.3])
    assert plan["bounded"] is False
    assert plan["n"] == ds.t1_stats.plan_sample_size(
        ds.statistics.stdev([0.0, 0.1, 0.2, 0.3]))
    assert plan["planning_approximation"] is True
    assert plan["plan_is_proof_of_precision"] is False
    assert "sample-size" in plan["rule"] or "s from the development" in \
        plan["rule"]
    assert plan["n"] >= ds.t1_stats.DEFAULT_MIN_N


def math_isfinite(value):
    return isinstance(value, (int, float)) and value == value and value not in (
        float("inf"), float("-inf"))


# ---------------------------------------------------------------------------
# freeze / load: every binding, and nothing unbound
# ---------------------------------------------------------------------------
def test_the_frozen_artifact_binds_every_required_field(tmp_path):
    document = _freeze(tmp_path)
    path = tmp_path / "selected_design.json"
    assert path.exists()
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["schema"] == ds.SELECTED_DESIGN_SCHEMA
    for field in ("loss_table", "loss_table_sha256", "deadline",
                  "deadline_sha256", "blocks", "block_set_sha256",
                  "confirm_seeds", "confirm_seeds_sha256", "rule",
                  "configuration_inputs", "sample_size", "cost_rationale",
                  "statistics", "artifact_sha256"):
        assert field in stored, field
    assert stored["deadline"]["s1"]["deadline_s"] == pytest.approx(2.0)
    assert stored["sample_size"]["n"] == 20
    assert stored["cost_rationale"]["cells"]
    assert document["artifact_sha256"] == stored["artifact_sha256"]
    assert stored["rule"]["selected"] == "median_eta_offset"
    # the artifact must say the planning formula is an approximation
    assert "approximation" in json.dumps(stored["statistics"])


def test_a_fixed_horizon_reaches_the_artifact_and_changes_the_inputs(tmp_path):
    rule = _selection(selected="p75_offset")
    rule["rule"] = {"kind": "fixed_h",
                    "selected": "p75_offset",
                    "name": "p75_offset",
                    "quantile": 0.75,
                    "quantile_source": "development_baseline",
                    "horizon_s": 6.25,
                    "configuration": ds._configuration_inputs(
                        "p75_offset", 6.25)}
    frozen_rule = _freeze(tmp_path / "a", selection=rule)
    assert frozen_rule["configuration_inputs"]["time_alignment"][
        "common_rule"] == "fixed_horizon"
    assert frozen_rule["configuration_inputs"]["time_alignment"][
        "common_horizon_s"] == pytest.approx(6.25)
    rule["rule"]["horizon_s"] = 9.5
    rule["rule"]["configuration"] = ds._configuration_inputs("p75_offset", 9.5)
    frozen_other = _freeze(tmp_path / "b", selection=rule)
    assert frozen_other["configuration_inputs"]["time_alignment"][
        "common_horizon_s"] == pytest.approx(9.5)
    assert frozen_other["artifact_sha256"] != frozen_rule["artifact_sha256"]


def test_a_missing_input_refuses_to_freeze(tmp_path):
    with pytest.raises(ds.SelectionError) as caught:
        ds.freeze_selected_design(
            _selection(), deadline=_scenario_deadlines(_dev_blocks()),
            blocks=_dev_blocks(), sample_size={"n": 20},
            out_path=tmp_path / "x.json")
    assert "confirm_seeds" in str(caught.value)


def test_confirmation_seeds_overlapping_development_seeds_refuse_to_freeze(
        tmp_path):
    with pytest.raises(ds.SelectionError) as caught:
        _freeze(tmp_path, confirm_seeds=[7, 1001])
    assert "overlap" in str(caught.value) or "7" in str(caught.value)


def test_changing_the_confirmation_output_cannot_change_the_selection(
        tmp_path):
    # a confirmation branch, whatever it reports, is not a development block:
    # it is refused when it is offered as one, and it contributes nothing to
    # the development loss table, the selection or their hashes
    reference = _freeze(tmp_path / "ref", blocks=_dev_blocks())
    confirm = []
    for index, seed in enumerate(CONFIRM_SEEDS):
        block = _block("s1", "conf", seed, "candidate", "per_packet",
                       0.99 if index % 2 else 0.01)
        block["phase"] = "confirmation"
        confirm.append(block)
    with pytest.raises(ds.SelectionError) as caught:
        ds.evaluate_candidates(_horizons(), _dev_blocks() + confirm)
    assert "confirmation" in str(caught.value)
    other = _freeze(tmp_path / "other", blocks=_dev_blocks())
    assert other["selection"]["selected"] == reference["selection"]["selected"]
    assert other["selection"]["loss_table_sha256"] == \
        reference["selection"]["loss_table_sha256"]
    assert other["block_set_sha256"] == reference["block_set_sha256"]
    assert other["rule"] == reference["rule"]


def test_load_round_trips_and_recomputes_every_hash(tmp_path):
    _freeze(tmp_path)
    loaded = ds.load_selected_design(tmp_path / "selected_design.json")
    assert loaded["rule"]["selected"] == "median_eta_offset"
    assert loaded["blocks"]["set_sha256"] == loaded["block_set_sha256"]
    assert loaded["confirm_seeds"] == CONFIRM_SEEDS
    assert loaded["configuration_inputs"]["time_alignment"]["common_rule"] == \
        "median_eta"


def test_a_tampered_loss_table_refuses_to_load(tmp_path):
    _freeze(tmp_path)
    path = tmp_path / "selected_design.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["loss_table"]["losses"]["median_eta_offset"]["b0"] = 0.0001
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ds.SelectionError) as caught:
        ds.load_selected_design(path)
    assert "loss_table_sha256" in str(caught.value)


def test_a_missing_field_refuses_to_load(tmp_path):
    _freeze(tmp_path)
    path = tmp_path / "selected_design.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    del stored["sample_size"]
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ds.SelectionError) as caught:
        ds.load_selected_design(path)
    assert "sample_size" in str(caught.value)


def test_a_stripped_confirm_seed_hash_refuses_to_load(tmp_path):
    _freeze(tmp_path)
    path = tmp_path / "selected_design.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["confirm_seeds"] = [1, 2, 3]
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ds.SelectionError) as caught:
        ds.load_selected_design(path)
    assert "confirm_seeds_sha256" in str(caught.value)


def test_a_generated_matrix_shorter_than_the_frozen_n_is_refused(tmp_path):
    # the design authorises 20 seeds for its two scenarios; a matrix that
    # compiles only 19 blocks per scenario is refused, and the refusal names
    # both the frozen n and the number actually generated
    _freeze(tmp_path, sample_size={"n": 20, "per_scenario": True})
    loaded = ds.load_selected_design(tmp_path / "selected_design.json")
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(
            loaded, _confirm_blocks(CONFIRM_SEEDS[:19],
                                    scenarios=("s1", "s2")))
    message = str(caught.value)
    assert "sample_size.n=20" in message and "19" in message


# ---------------------------------------------------------------------------
# check_confirmation_matrix: exactly the frozen n, no seed reuse
# ---------------------------------------------------------------------------
def _confirm_blocks(seeds, arms=("stale", "common", "candidate"),
                    modes=("per_packet",), scenarios=("s1",)):
    blocks = []
    for seed in seeds:
        for arm in arms:
            for mode in modes:
                for scenario in scenarios:
                    block = _block(scenario, "t1", seed, arm, mode, 0.4)
                    block["phase"] = "confirmation"
                    blocks.append(block)
    return blocks


def _single_scenario_design(confirm_seeds=CONFIRM_SEEDS, n=None):
    """A design whose deadline map names exactly the scenario it will run."""
    design = _design(confirm_seeds=confirm_seeds)
    design["deadline"] = {"s1": design["deadline"]["s1"]}
    design["sample_size"]["n"] = len(design["confirm_seeds"]) if n is None else n
    return design


def _design(confirm_seeds=CONFIRM_SEEDS, n=None):
    seeds = list(confirm_seeds)
    return {
        "confirm_seeds": seeds,
        "deadline": _scenario_deadlines(_dev_blocks()),
        "required_arms": ["stale", "common", "candidate"],
        "required_modes": ["per_packet"],
        "sample_size": {"n": len(seeds) if n is None else n,
                        "per_scenario": True},
        "development_seeds": DEV_SEEDS,
    }


def test_the_confirmation_matrix_accepts_exactly_the_frozen_number():
    design = _single_scenario_design()
    ds.check_confirmation_matrix(design, _confirm_blocks(CONFIRM_SEEDS))


def test_a_short_confirmation_matrix_is_refused():
    # the matrix compiles 19 blocks for a frozen n of 20
    design = _single_scenario_design(confirm_seeds=CONFIRM_SEEDS[:20], n=20)
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design,
                                     _confirm_blocks(CONFIRM_SEEDS[:19]))
    message = str(caught.value)
    assert "s1" in message
    assert "19" in message and "20" in message


def test_a_seed_list_shorter_than_the_declared_n_is_refused():
    design = _single_scenario_design(confirm_seeds=CONFIRM_SEEDS[:19], n=20)
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design,
                                     _confirm_blocks(CONFIRM_SEEDS[:19]))
    message = str(caught.value)
    assert "sample_size.n" in message
    assert "19" in message and "20" in message


def test_a_metadata_only_sample_size_is_refused():
    # the metadata DECLARES 20 and the design generates only 12 seeds
    design = _design(confirm_seeds=CONFIRM_SEEDS[:12], n=20)
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design,
                                     _confirm_blocks(CONFIRM_SEEDS[:12]))
    message = str(caught.value)
    assert "12" in message and "20" in message
    assert "sample_size" in message


def test_a_repeated_confirmation_seed_is_refused():
    seeds = CONFIRM_SEEDS[:19] + [CONFIRM_SEEDS[0]]
    design = _single_scenario_design(confirm_seeds=seeds, n=20)
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design, _confirm_blocks(seeds))
    assert "repeat" in str(caught.value)


def test_a_confirmation_seed_overlapping_a_development_seed_is_refused():
    seeds = CONFIRM_SEEDS[:19] + [11]
    design = _single_scenario_design(confirm_seeds=seeds, n=20)
    assert design["development_seeds"] == DEV_SEEDS
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design, _confirm_blocks(seeds))
    assert "development" in str(caught.value)


def test_a_block_missing_an_arm_is_refused():
    design = _design()
    blocks = _confirm_blocks(CONFIRM_SEEDS)
    blocks = [b for b in blocks
              if not (b["seed"] == CONFIRM_SEEDS[0] and b["arm"] == "common")]
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design, blocks)
    assert "common" in str(caught.value)


def test_a_block_missing_a_mode_is_refused():
    design = _design()
    design["required_modes"] = ["per_packet", "background"]
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design, _confirm_blocks(CONFIRM_SEEDS))
    assert "background" in str(caught.value)


def test_a_development_block_inside_the_confirmation_matrix_is_refused():
    design = _design()
    blocks = _confirm_blocks(CONFIRM_SEEDS)
    blocks[0]["phase"] = "development"
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design, blocks)
    assert "confirmation" in str(caught.value)


def test_a_confirmation_block_outside_the_frozen_seed_list_is_refused():
    design = _design()
    blocks = _confirm_blocks(CONFIRM_SEEDS)
    blocks[0]["seed"] = 9999
    with pytest.raises(ds.SelectionError) as caught:
        ds.check_confirmation_matrix(design, blocks)
    assert "9999" in str(caught.value)
