"""A3: the development SELECTION and statistical FREEZING layer.

WHY THIS MODULE EXISTS
======================
The T1 suite already reported that a common horizon had to be selected on
development data (CODE/experiment_platform/t1_suite.py::_common_strong_state),
but it could not select one: it inferred "the five candidates cannot be
discriminated" from the variance of ONE rule (common vs candidate), never
evaluated the five pre-declared candidates, never bound the horizon it chose,
never derived the deadline per scenario, and never checked that the
confirmation matrix it asked for was actually compiled.  This module is that
missing layer.  It is CALLED BY the suite; it never imports the suite, the
kernel or the comparison driver.

THE SEVEN CONTRACTS
===================
1. FIVE PRE-DECLARED CANDIDATES, EVALUATED FOR REAL.  mean ETA offset,
   median ETA offset, and three FIXED horizons taken from the p25 / p50 / p75
   of the DEVELOPMENT-BASELINE candidate ETA-offset population.  The quantiles
   come from the development baseline and nowhere else: a pool, a block or a
   declared source that says "confirmation" is refused, because a quantile
   computed from the data that is supposed to test the hypothesis would let
   the confirmation set choose its own hypothesis.

2. SELECTION ON THE DEVELOPMENT BLOCKS BY THE MEAN PRIMARY LOSS.  Ties are
   broken toward the median-horizon candidate and then by the declared list
   order of the contract's common_horizon_dev_candidates.  What gets frozen is
   the RULE or the fixed h together with the quantile source, the block set,
   the loss table and their sha256 values - never a bare {"frozen": true}.

3. FOUR SEPARATE OUTCOMES.  tie, zero_variance,
   insufficient_scenario_coverage and insufficient_statistical_evidence are
   independent, independently visible flags.  In particular, zero variance is
   a property of THIS candidate's development losses, not evidence that the
   five candidates were compared; the previous module inferred the second from
   the first and this one does not.

4. ONE DEADLINE D PER BUSINESS SCENARIO.  Either a pre-specified business
   deadline or 2 x p95 of the legal-candidate delivery sample of the
   INDEPENDENT DEVELOPMENT BASELINE.  Every arm and every mode of a scenario
   shares that D; resolve_scenario_deadlines refuses a block that carries its
   own per-branch D (or a branch-derived D source), because averaging branches
   with different D would put different loss scales into one mean.

5. PAIRED BLOCKS, WITHIN-STRATUM BOOTSTRAP.  Blocks are paired by scenario x
   trace x seed and bootstrap resampling happens WITHIN a scenario stratum with
   a fixed seed and 10000 replicates; the cross-scenario weights are declared
   in the frozen artifact.  Mismatched, censored and failed blocks are LISTED
   with their reason and never dropped, and a delivered-subset latency never
   stands in for loss or non-completion.

6. PRECISION PLANNING REUSES t1_stats.  n = max(20, ceil((1.96*s/0.005)**2)) on
   the development sample.  That formula is a PLANNING APPROXIMATION and the
   artifact says so; it is never presented as proof of achieved precision.
   When the observed variance is zero there is no infinite and no perfect
   precision: plan_precision emits the BOUNDED branch, states why n cannot be
   sized and what bounded study design would be needed instead.

7. FREEZE AND LOAD BIND EVERYTHING.  freeze_selected_design writes
   selected_design.json binding the candidate loss table, the per-scenario D
   with its source, the rule and parameters actually used, the development
   block set with its identity hashes, the confirmation seeds and the n / cost
   rationale.  load_selected_design recomputes every embedded hash and refuses
   a file with a missing or unrecomputable binding.  check_confirmation_matrix
   refuses a confirmation matrix whose per-scenario independent-block count is
   not exactly the frozen n, whose seeds repeat or overlap the development
   seeds, or whose blocks do not list every arm/mode they need - a file that
   only DECLARES sample_size while generating fewer seeds is refused.

INPUT SHAPES (the caller does not exist yet; ASSUMPTIONS ARE EXPLICIT)
=====================================================================
A block is one branch outcome for one arm/mode, in the shape the T1 driver
records per cell (CODE/experiment_platform/time_alignment_compare.py: scenario
id, trace identity, seed, arm, the primary arms' normalized loss, censoring and
mismatch accounting)::

    {"scenario_id": str,            # required
     "trace_id": str,               # required; with seed it forms the block id
     "seed": int,                   # required
     "arm": str,                    # "stale" | "common" | "candidate" | ...
     "mode": str,                   # "per_packet" | "async_point" | ...
     "phase": "development",        # required; "confirmation" is REFUSED here
     "eval": {"status": str,        # "delivered" | "censored" | "failed" | ...
              "loss": float | None,  # primary loss; required when delivered
              "legal": bool, "deadline_s": float, "latency_s": float | None,
              "delivered": bool, "censored": bool, "failed": bool,
              "resource_mismatch": bool}}

The evaluation section lives under "eval" on purpose: block_eval() fails
loudly when it is missing rather than guessing a score for the block (a
top-level "loss" key is accepted only as a compatibility shortcut).

Every public function accepts analysis_phase="development" and refuses any
other value, so a confirmation-time call is a loud refusal, never a silent
selection on confirmation data.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import statistics
import tempfile
import time
from pathlib import Path

from CODE.experiment_platform import t1_stats

#: Schema of the loss table produced by evaluate_candidates.
LOSS_TABLE_SCHEMA = "t1-design-loss-table/v1"

#: Schema of the frozen artifact written by freeze_selected_design.
SELECTED_DESIGN_SCHEMA = "t1-selected-design/v1"

#: The five pre-declared candidates, in the contract's declared order
#: (CODE/work/WP-T1-COMPLETE/contract.yaml -> common_horizon_dev_candidates).
DECLARED_CANDIDATES = ("mean_eta_offset", "median_eta_offset", "p25_offset",
                       "p50_offset", "p75_offset")

#: The rule candidates: computed by the online common arm from the legal
#: candidates' ETA offsets AT EACH DECISION (CODE/leo_sim/time_alignment.py),
#: never a fixed number.
RULE_CANDIDATES = ("mean_eta_offset", "median_eta_offset")

#: The fixed-h candidates, with the quantile of the DEVELOPMENT-BASELINE
#: candidate ETA-offset population each one freezes.
FIXED_CANDIDATE_QUANTILES = {"p25_offset": 0.25, "p50_offset": 0.5,
                             "p75_offset": 0.75}

#: Candidate names offered to the median-horizon tie-break, in order.
MEDIAN_HORIZON_NAMES = ("median_eta_offset", "p50_offset", "p50_h")

#: The only quantile provenance this module admits.
DEVELOPMENT_BASELINE = "development_baseline"
CONFIRMATION_BASELINE = "confirmation_baseline"
DEVELOPMENT_PHASE = "development"
CONFIRMATION_PHASE = "confirmation"

#: Deadline sources.  "p95_times_multiplier" is the P9 default rule
#: (2 x p95 of the development-baseline legal-candidate delivery sample).
SOURCE_DECLARED_BUSINESS = "declared_business_deadline"
SOURCE_P95_RULE = "p95_times_multiplier"
SOURCE_FROZEN_DESIGN = "frozen_development_selection"

#: Per-branch deadline markers that must never appear on a block.
_FORBIDDEN_BRANCH_DEADLINE_KEYS = ("deadline_source", "deadline_from_branch",
                                   "branch_deadline_s", "per_branch_deadline_s",
                                   "derived_from_branch")

#: Default within-stratum bootstrap parameters (plan section P9).
DEFAULT_BOOTSTRAP_REPLICATES = t1_stats.DEFAULT_BOOTSTRAP_REPLICATES
DEFAULT_BOOTSTRAP_SEED = t1_stats.DEFAULT_BOOTSTRAP_SEED
DEFAULT_BOOTSTRAP_CONFIDENCE = 0.95

#: Minimum number of independent development blocks before a selection counts
#: as statistically evidenced (plan section P9 sample_size_rule floor).
DEFAULT_MINIMUM_DEVELOPMENT_BLOCKS = t1_stats.DEFAULT_MIN_N

#: Relative tolerance used for tie / equality tests on mean losses.
DEFAULT_TIE_TOLERANCE = 1e-9

#: Absolute tolerance below which a development dispersion is treated as ZERO.
#: A paired difference computed in floating point (for example 0.41-0.41) can
#: come out as ~1e-17 instead of 0.0; without this floor that dust would size a
#: confirmation run, which is exactly the false precision the bounded branch
#: exists to prevent.  The floor is absolute in normalized-loss units and is
#: far below the pre-declared 0.005 target half-width.
ZERO_VARIANCE_TOLERANCE = 1e-12

#: Pre-declared precision-planning rule, reused verbatim from t1_stats.
PRECISION_RULE = ("n = max(min_n, ceil((z * s / half_width) ** 2)) with "
                  "half_width = 0.005 and z = 1.96, s from the development "
                  "sample (plan section P9)")

#: Pre-declared compute-cost planning assumption for the name-collision /
#: cost rationale (cell wall time ceiling from the contract budgets section).
DEFAULT_CELL_COST_S = 120.0

_STATISTICS_LIMITS = [
    "the sample-size formula is a PLANNING APPROXIMATION for precision; it is "
    "not proof of achieved precision and it is not a power analysis",
    "the selected candidate is chosen on DEVELOPMENT blocks only; a "
    "confirmation-derived quantile or block is refused",
    "tie / zero_variance / insufficient_scenario_coverage / "
    "insufficient_statistical_evidence are separate outcomes and none of them "
    "proves that the five candidates were compared",
    "mismatched, censored and failed blocks are listed with their reasons, "
    "never dropped, and a delivered-subset latency never stands in for loss",
]


class SelectionError(RuntimeError):
    """A selection or freezing step refused to proceed, with the field named."""


# --------------------------------------------------------------------- hashing
def canonical_json(document) -> str:
    """Deterministic JSON text for hashing (sorted keys, compact separators)."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def sha256_hex(document) -> str:
    """sha256 of the canonical JSON form of document."""
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def _require_mapping(value, label: str) -> dict:
    if not isinstance(value, dict):
        raise SelectionError(
            f"{label} must be an object/dict, got {type(value).__name__}")
    return value


def _require_finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SelectionError(f"{label} must be a real number, got {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise SelectionError(f"{label} must be finite, got {value!r}")
    return result


def _require_seed(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SelectionError(f"{label} must be an integer, got {value!r}")
    return int(value)


def _require_development_phase(phase, label: str) -> str:
    if phase is None:
        raise SelectionError(
            f"{label} must declare analysis_phase; only "
            f"{DEVELOPMENT_PHASE!r} may take part in development selection")
    if str(phase) != DEVELOPMENT_PHASE:
        raise SelectionError(
            f"{label} must be {DEVELOPMENT_PHASE!r} to take part in "
            f"development selection, got {phase!r}; a quantile or a loss "
            "computed from confirmation data must be refused")
    return DEVELOPMENT_PHASE


# ------------------------------------------------- A3.1 candidate horizons
def build_candidate_horizons(eta_offsets) -> dict:
    """The five pre-declared common-horizon candidates, with real values.

    eta_offsets is one of

    * a sequence of ETA offsets (seconds) of the legal candidates of the
      INDEPENDENT DEVELOPMENT BASELINE (one value per legal candidate per
      decision), or
    * a mapping {"eta_offsets": [...], "source": "development_baseline",
      "analysis_phase": "development", "stratum": {...}} - the explicit form
      the suite should use, because it carries the provenance.

    Returns the candidate list (name / kind / horizon_s / quantile / rule), the
    frozen p25/p50/p75, the population size and the pool digest.  The pool is
    the DEVELOPMENT baseline: a confirmation-derived pool, or an analysis_phase
    other than "development", is refused with the reason naming the field.
    """
    stratum = {}
    if isinstance(eta_offsets, dict):
        if "eta_offsets" not in eta_offsets:
            raise SelectionError(
                "build_candidate_horizons: eta_offsets mapping is missing the "
                "'eta_offsets' field")
        source = eta_offsets.get("source", DEVELOPMENT_BASELINE)
        _require_development_phase(
            eta_offsets.get("analysis_phase", DEVELOPMENT_PHASE),
            "build_candidate_horizons eta_offsets analysis_phase")
        _check_quantile_source(source, "build_candidate_horizons source")
        stratum = eta_offsets.get("stratum") or {}
        raw = eta_offsets["eta_offsets"]
    else:
        source = DEVELOPMENT_BASELINE
        raw = eta_offsets
    if isinstance(raw, (str, bytes)) or raw is None:
        raise SelectionError(
            "build_candidate_horizons: eta_offsets must be a sequence of "
            f"numbers, got {type(raw).__name__}")
    try:
        values = [_require_finite(value, f"eta_offsets[{index}]")
                  for index, value in enumerate(raw)]
    except TypeError as exc:
        raise SelectionError(
            f"build_candidate_horizons: eta_offsets must be iterable, got "
            f"{type(raw).__name__}") from exc
    if not values:
        raise SelectionError(
            "build_candidate_horizons: eta_offsets is empty; the p25/p50/p75 "
            "candidates cannot be derived without a development-baseline "
            "population")
    for index, value in enumerate(values):
        if value < 0.0:
            raise SelectionError(
                f"eta_offsets[{index}] must be >= 0, got {value!r}")

    ordered = sorted(values)
    p25 = t1_stats._percentile(ordered, 0.25)
    p50 = t1_stats._percentile(ordered, 0.50)
    p75 = t1_stats._percentile(ordered, 0.75)
    mean = statistics.fmean(values)

    candidates = []
    for name in DECLARED_CANDIDATES:
        if name == "mean_eta_offset":
            candidates.append(_rule_candidate(name, mean))
        elif name == "median_eta_offset":
            candidates.append(_rule_candidate(name, p50))
        else:
            quantile = FIXED_CANDIDATE_QUANTILES[name]
            horizon = {"p25_offset": p25, "p50_offset": p50,
                       "p75_offset": p75}[name]
            candidates.append({
                "candidate": name, "kind": "fixed_h", "horizon_s": horizon,
                "quantile": quantile, "quantile_source": source,
                "computed_from": "development_baseline_population",
                "pooled_value_s": horizon,
                "mean_offset_s": mean,
            })
    return {
        "schema": "t1-candidate-horizons/v1",
        "analysis_phase": DEVELOPMENT_PHASE,
        "candidates": candidates,
        "candidate_names": list(DECLARED_CANDIDATES),
        "quantiles": {"p25": p25, "p50": p50, "p75": p75},
        "mean_eta_offset_s": mean,
        "median_eta_offset_s": statistics.median(values),
        "population_size": len(values),
        "pooled_offsets": values,
        "pooled_offsets_sha256": hashlib.sha256(
            json.dumps(values, sort_keys=True).encode("utf-8")).hexdigest(),
        "quantile_source": source,
        "stratum": stratum,
        "rule": ("the p25/p50/p75 candidates are FIXED horizons from the "
                 "development-baseline candidate ETA-offset population; "
                 "mean/median ETA offset stay online rules resolved by the "
                 "common arm at each decision"),
    }


def _rule_candidate(name: str, pooled_value: float) -> dict:
    """A rule candidate: resolved per decision, with its pooled reference.

    horizon_s is the POOLED value over the development-baseline population.  It
    is informational only: the online common arm resolves this candidate from
    the legal candidates at its own decision (CODE/leo_sim/time_alignment.py),
    so the fixed-horizon column of the loss table does not freeze it.
    """
    return {
        "candidate": name, "kind": "rule", "horizon_s": pooled_value,
        "quantile": None, "quantile_source": DEVELOPMENT_BASELINE,
        "computed_from": "per_decision_candidate_eta_offsets",
        "pooled_value_s": pooled_value,
        "horizon_s_is_pooled_reference": True,
    }


def _check_quantile_source(source, label: str) -> None:
    if source is None:
        raise SelectionError(
            f"{label} must declare the quantile source; only "
            f"{DEVELOPMENT_BASELINE!r} is admissible")
    text = str(source)
    if text == DEVELOPMENT_BASELINE:
        return
    if text == CONFIRMATION_BASELINE or CONFIRMATION_PHASE in text:
        raise SelectionError(
            f"{label} is {source!r}: a quantile computed from confirmation "
            "data is refused, because the quantile must come from the "
            "development baseline only")
    raise SelectionError(
        f"{label} is {source!r}, which is not {DEVELOPMENT_BASELINE!r}; the "
        "quantiles of the common-horizon candidates must come from the "
        "development baseline")


# ------------------------------------------------------ A3.2 block handling
def block_eval(block: dict) -> dict:
    """The evaluation section of a block, or a loud refusal.

    The section lives under "eval".  A block with no evaluation section names
    the missing field instead of being silently skipped or scored as zero.
    """
    _require_mapping(block, "block")
    if "eval" in block:
        return _require_mapping(block["eval"], "block['eval']")
    if "loss" in block:
        # compatibility shortcut for a flat record
        return {key: block[key] for key in (
            "status", "loss", "legal", "deadline_s", "latency_s", "delivered",
            "censored", "failed", "resource_mismatch") if key in block}
    raise SelectionError(
        "block is missing its 'eval' section; a block without an evaluation "
        "cannot be scored and must be reported, not guessed "
        f"(scenario_id={block.get('scenario_id')!r}, "
        f"trace_id={block.get('trace_id')!r}, seed={block.get('seed')!r}, "
        f"arm={block.get('arm')!r})")


def _block_fields(block: dict, index: int) -> dict:
    _require_mapping(block, f"blocks[{index}]")
    label = f"blocks[{index}]"
    for field in ("scenario_id", "trace_id", "seed"):
        if field not in block:
            raise SelectionError(f"{label} is missing the field {field!r}")
    scenario = block["scenario_id"]
    if not isinstance(scenario, str) or not scenario:
        raise SelectionError(
            f"{label}.scenario_id must be a non-empty string, "
            f"got {scenario!r}")
    trace = block["trace_id"]
    if not isinstance(trace, str) or not trace:
        raise SelectionError(
            f"{label}.trace_id must be a non-empty string, got {trace!r}")
    seed = _require_seed(block["seed"], f"{label}.seed")
    phase = _require_development_phase(block.get("phase"), f"{label}.phase")
    arm = block.get("arm")
    if not isinstance(arm, str) or not arm:
        raise SelectionError(
            f"{label}.arm must be a non-empty string, got {arm!r}")
    mode = block.get("mode")
    if not isinstance(mode, str) or not mode:
        raise SelectionError(
            f"{label}.mode must be a non-empty string, got {mode!r}")
    for key in _FORBIDDEN_BRANCH_DEADLINE_KEYS:
        if key in block:
            raise SelectionError(
                f"{label} declares {key!r}: a per-branch derived deadline is "
                "refused; one deadline D is resolved per business scenario")
    return {"scenario_id": scenario, "trace_id": trace, "seed": seed,
            "phase": phase, "arm": arm, "mode": mode,
            "stratum": f"{scenario}|{trace}|{seed}"}


def block_identity(block: dict) -> str:
    """Stable id of a block: scenario x trace x seed, carrying arm and mode.

    The id is a sha256 of those five fields so two callers with different key
    ORDER still agree, and a block that is duplicated at a different arm is a
    different block instead of a silent overwrite.
    """
    fields = _block_fields(block, 0)
    payload = {key: fields[key] for key in ("scenario_id", "trace_id", "seed",
                                            "arm", "mode")}
    return sha256_hex(payload)


def unit_for(block: dict) -> str:
    """Readable unit key: <scenario>|<trace>|<seed>|<arm>|<mode>."""
    fields = _block_fields(block, 0)
    return "|".join(str(fields[key]) for key in (
        "scenario_id", "trace_id", "seed", "arm", "mode"))


def block_set_document(blocks) -> dict:
    """The development block set with the identity hashes that bind it."""
    entries = []
    for index, block in enumerate(blocks):
        fields = _block_fields(block, index)
        entries.append({
            "unit": unit_for(block),
            "block_id": block_identity(block),
            "identity_hash": sha256_hex(fields),
            "scenario_id": fields["scenario_id"],
            "trace_id": fields["trace_id"],
            "seed": fields["seed"],
            "arm": fields["arm"],
            "mode": fields["mode"],
            "stratum": fields["stratum"],
        })
    entries.sort(key=lambda entry: entry["unit"])
    document = {
        "schema": "t1-design-block-set/v1",
        "pairing": "scenario x trace x seed",
        "unit_of_replication": "scenario x trace x seed (one block per branch)",
        "independent_blocks": len({entry["stratum"] for entry in entries}),
        "counts_by_scenario": _counts(entries, "scenario_id"),
        "counts_by_arm": _counts(entries, "arm"),
        "counts_by_mode": _counts(entries, "mode"),
        "blocks": entries,
    }
    document["set_sha256"] = sha256_hex(document)
    return document


def _counts(entries, key):
    counts = {}
    for entry in entries:
        counts[entry[key]] = counts.get(entry[key], 0) + 1
    return dict(sorted(counts.items()))


def _normalize_loss(value, label: str) -> float:
    loss = _require_finite(value, label)
    if not 0.0 <= loss <= 1.0:
        raise SelectionError(
            f"{label} must be a normalized loss in [0, 1], got {loss!r}")
    return loss


def _scenario_deadline_of(deadline: dict, fields: dict, unit: str) -> float:
    """The resolved D of this block's scenario, or a loud refusal."""
    scenario = fields["scenario_id"]
    if scenario not in deadline:
        raise SelectionError(
            f"no deadline is resolved for scenario {scenario!r} (block "
            f"{unit!r}); call resolve_scenario_deadlines for every scenario "
            "before scoring")
    return _require_finite(deadline[scenario],
                           f"deadline[{scenario!r}].deadline_s")


def _score_block(block: dict, index: int, deadline: dict | None) -> dict:
    """Score ONE block, or return the precise reason it cannot be scored.

    The returned dict always has a "scored" flag and a "reason" when it is
    False.  Nothing is ever inferred from a delivered-subset latency.
    """
    fields = _block_fields(block, index)
    unit = unit_for(block)
    evaluation = block_eval(block)
    status = str(evaluation.get("status", "unknown"))
    reason = None
    if evaluation.get("censored") is True or status == "censored":
        reason = ("censored: the observation window cut the packet off, so "
                  "there is no proven fate and no loss to score")
    elif evaluation.get("failed") is True \
            or status in ("failed", "missing", "skipped"):
        reason = (f"{status}: the block failed for seed/deadline/information "
                  "reasons and is reported, never dropped")
    elif evaluation.get("resource_mismatch") is True \
            or status == "resource_mismatch":
        reason = ("resource_mismatch: the realised egress differs from the "
                  "predicted resource, so the block does not belong to the "
                  "same-resource loss population")
    elif "loss" in evaluation:
        if evaluation["loss"] is None:
            raise SelectionError(
                f"block {unit!r} is delivered and declares eval.loss=None; a "
                "missing score must not be guessed from the latency, and a "
                "delivered-subset latency must not stand in for loss")
        # the score already exists and was produced against the shared D; the
        # block-level deadline below is only checked for CONSISTENCY, so a
        # comparison run without a deadline argument can still be tabulated
    elif deadline is None:
        reason = ("no deadline resolved for this scenario: the primary loss "
                  "L = min(delay, D) / D is undefined without the frozen D")
    elif evaluation.get("delivered") is True \
            and evaluation.get("latency_s") is not None:
        pass
    else:
        raise SelectionError(
            f"block {unit!r} is delivered but carries neither eval.loss nor "
            "eval.latency_s; a missing score must not be guessed and a "
            "delivered-subset latency must not stand in for loss")
    deadline_s_scenario = (None if deadline is None else
                           _scenario_deadline_of(deadline, fields, unit))
    if reason is not None:
        return {"scored": False, "unit": unit, "block_id": block_identity(block),
                **{key: fields[key] for key in ("scenario_id", "trace_id",
                                                "seed", "arm", "mode")},
                "status": status, "reason": reason}

    deadline_s = None
    if "loss" in evaluation and evaluation["loss"] is not None:
        loss = _normalize_loss(evaluation["loss"], f"block {unit!r}.loss")
        if deadline is not None and evaluation.get("deadline_s") is not None:
            block_deadline = _require_finite(
                evaluation["deadline_s"], f"block {unit!r}.deadline_s")
            scenario_deadline = deadline_s_scenario
            if abs(block_deadline - scenario_deadline) > 1e-12:
                raise SelectionError(
                    f"block {unit!r} was scored against its own deadline "
                    f"{block_deadline!r} while the scenario deadline is "
                    f"{scenario_deadline!r}; one deadline D per scenario is "
                    "frozen and a per-branch scale must be refused")
            deadline_s = block_deadline
    else:
        if deadline is None:
            raise SelectionError(
                f"block {unit!r} must be scored from its latency but no "
                "deadline is resolved for scenario "
                f"{fields['scenario_id']!r}; pass deadline= from "
                "resolve_scenario_deadlines")
        latency = _require_finite(evaluation["latency_s"],
                                  f"block {unit!r}.latency_s")
        scenario_deadline = deadline_s_scenario
        # the loss is scored against the SCENARIO deadline; a latency is a
        # delivery delay, never a per-branch deadline (t1_stats.normalized_loss
        # saturates a delay at or beyond D at 1.0, and a loss whose scored D
        # differs from the scenario D is refused as a different loss scale)
        if evaluation.get("deadline_s") is not None:
            scored_against = _require_finite(
                evaluation["deadline_s"], f"block {unit!r}.deadline_s")
            if abs(scored_against - scenario_deadline) > 1e-12:
                raise SelectionError(
                    f"block {unit!r} was scored from a latency against its own "
                    f"deadline {scored_against!r} while the scenario deadline "
                    f"is {scenario_deadline!r}; one deadline D per scenario is "
                    "frozen and a per-branch scale must be refused")
        deadline_s = scored_against if evaluation.get("deadline_s") is not None \
            else scenario_deadline
        loss = t1_stats.normalized_loss(latency, scenario_deadline)
    return {"scored": True, "unit": unit, "block_id": block_identity(block),
            **{key: fields[key] for key in ("scenario_id", "trace_id", "seed",
                                            "arm", "mode")},
            "status": status, "loss": loss, "deadline_s": deadline_s,
            "reason": None}


def evaluate_candidates(candidates, blocks, *, deadline=None,
                        analysis_phase: str = DEVELOPMENT_PHASE) -> dict:
    """The candidate x block primary-loss table, with reasons for the rest.

    candidates is the document returned by build_candidate_horizons (or its
    candidate list).  blocks are development blocks paired by scenario x trace
    x seed; every block is scored for EVERY candidate, and a block that cannot
    be scored is listed in "unscored" with its reason instead of being dropped.
    deadline maps scenario_id -> the resolved scenario deadline (see
    resolve_scenario_deadlines); a block whose own deadline differs from its
    scenario's is refused.
    """
    _require_development_phase(analysis_phase, "evaluate_candidates")
    candidates = _candidate_list(candidates)
    deadline = _coerce_deadline_argument(deadline)
    if not isinstance(blocks, (list, tuple)) or not blocks:
        raise SelectionError(
            "evaluate_candidates requires a non-empty list of development "
            "blocks")
    _require_mapping(deadline, "deadline") if deadline is not None else None

    scored, unscored = [], []
    for index, block in enumerate(blocks):
        row = _score_block(block, index, deadline)
        (scored if row["scored"] else unscored).append(row)
    if not scored:
        raise SelectionError(
            "evaluate_candidates: no development block could be scored; the "
            "unscored list below carries every reason and a selection cannot "
            "be made from an empty sample: "
            + json.dumps(unscored, ensure_ascii=False))

    losses, candidate_rows = {}, []
    for candidate in candidates:
        name = candidate["candidate"]
        losses[name] = {row["unit"]: row["loss"] for row in scored}
        values = list(losses[name].values())
        candidate_rows.append({
            "candidate": name,
            "kind": candidate.get("kind"),
            "horizon_s": candidate.get("horizon_s"),
            "quantile": candidate.get("quantile"),
            "scored_blocks": len(values),
            "unscored_blocks": len(unscored),
            "mean_loss": statistics.fmean(values),
            "variance": statistics.variance(values) if len(values) >= 2
                        else 0.0,
        })
    table = {
        "schema": LOSS_TABLE_SCHEMA,
        "analysis_phase": DEVELOPMENT_PHASE,
        "candidates": candidate_rows,
        "blocks": [{"unit": row["unit"], "block_id": row["block_id"],
                    "scenario_id": row["scenario_id"],
                    "trace_id": row["trace_id"], "seed": row["seed"],
                    "arm": row["arm"], "mode": row["mode"]}
                   for row in scored],
        "losses": losses,
        "unscored": [{"unit": row["unit"], "scenario_id": row["scenario_id"],
                      "trace_id": row["trace_id"], "seed": row["seed"],
                      "arm": row["arm"], "mode": row["mode"],
                      "status": row["status"], "reason": row["reason"]}
                     for row in unscored],
        "deadline": None if deadline is None else dict(sorted(
            deadline.items())),
        "rule": ("primary loss per candidate x block; mismatched, censored and "
                 "failed blocks are listed in 'unscored', never dropped"),
    }
    table["loss_table_sha256"] = sha256_hex(table)
    return table


def _candidate_list(candidates) -> list:
    if isinstance(candidates, dict):
        if "candidates" not in candidates:
            raise SelectionError(
                "candidates document is missing its 'candidates' field; pass "
                "the result of build_candidate_horizons")
        candidates = candidates["candidates"]
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise SelectionError(
            "candidates must be a non-empty list or a build_candidate_horizons "
            "document")
    result = []
    for index, candidate in enumerate(candidates):
        _require_mapping(candidate, f"candidates[{index}]")
        name = candidate.get("candidate")
        if not isinstance(name, str) or not name:
            raise SelectionError(
                f"candidates[{index}] is missing the 'candidate' name")
        result.append(candidate)
    return result


def loss_table_sha256(loss_table: dict) -> str:
    """The sha256 the selection binds; recomputable from the table alone."""
    _require_mapping(loss_table, "loss_table")
    return sha256_hex(loss_table)


# ---------------------------------------------------- A3.3 common_strong
def select_common_strong(loss_table: dict, *, declared_order=None,
                         minimum_development_blocks: int =
                         DEFAULT_MINIMUM_DEVELOPMENT_BLOCKS,
                         tie_tolerance: float = DEFAULT_TIE_TOLERANCE,
                         block_scenarios=None,
                         declared_weights=None,
                         bootstrap_replicates: int =
                         DEFAULT_BOOTSTRAP_REPLICATES,
                         bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
                         analysis_phase: str = DEVELOPMENT_PHASE) -> dict:
    """Select common_strong on the development loss table.

    The winner is the candidate with the smallest MEAN primary loss.  Ties are
    broken toward the median-horizon candidate and then by the declared list
    order (the contract's common_horizon_dev_candidates).  The result carries
    the four independent outcomes, the per-candidate evidence, the bootstrap
    design and the loss table hash - the caller freezes these, never a bare
    frozen: true.
    """
    _require_development_phase(analysis_phase, "select_common_strong")
    _require_mapping(loss_table, "loss_table")
    if "losses" not in loss_table or "candidates" not in loss_table:
        raise SelectionError(
            "loss_table is missing 'losses' or 'candidates'; pass the result "
            "of evaluate_candidates")
    declared_order = list(declared_order or DECLARED_CANDIDATES)
    if len(set(declared_order)) != len(declared_order):
        raise SelectionError(
            f"declared_order repeats a candidate: {declared_order!r}")
    tolerance = _require_finite(tie_tolerance, "tie_tolerance")
    if tolerance < 0.0:
        raise SelectionError(
            f"tie_tolerance must be >= 0, got {tie_tolerance!r}")

    evidence, unscorable = [], []
    for row in loss_table["candidates"]:
        name = row.get("candidate")
        if not isinstance(name, str):
            raise SelectionError("loss_table.candidates entry has no name")
        values = list((loss_table["losses"].get(name) or {}).values())
        if not values:
            unscorable.append({
                "candidate": name,
                "reason": ("no block could be scored for this candidate; it "
                           "cannot be selected")})
            continue
        numeric = [_require_finite(value, f"losses[{name}][{index}]")
                   for index, value in enumerate(values)]
        variance = statistics.variance(numeric) if len(numeric) >= 2 else 0.0
        evidence.append({
            "candidate": name,
            "kind": row.get("kind"),
            "horizon_s": row.get("horizon_s"),
            "quantile": row.get("quantile"),
            "scored_blocks": len(numeric),
            "mean_loss": statistics.fmean(numeric),
            "median_loss": statistics.median(numeric),
            "variance": variance,
            "zero_variance": variance == 0.0,
        })
    if not evidence:
        raise SelectionError(
            "select_common_strong: no candidate has a scored development "
            "block; there is nothing to select: "
            + json.dumps(unscorable, ensure_ascii=False))
    best = min(row["mean_loss"] for row in evidence)
    tied = [row["candidate"] for row in evidence
            if abs(row["mean_loss"] - best) <= tolerance]
    winner = _break_tie(tied, declared_order)

    block_units = [row["unit"] for row in (loss_table.get("blocks") or [])]
    scenario_coverage = sorted(set(_scenario_map(
        loss_table, block_scenarios).values()))
    best_row = next(row for row in evidence if row["candidate"] == winner)
    sparse = [row["candidate"] for row in evidence
              if row["scored_blocks"] < int(minimum_development_blocks)]
    tie = len(tied) > 1
    outcomes = {
        "tie": tie,
        "zero_variance": bool(best_row["zero_variance"]),
        "insufficient_scenario_coverage": len(scenario_coverage) < 2,
        "insufficient_statistical_evidence": bool(sparse),
    }
    bootstrap = _within_stratum_bootstrap(loss_table, evidence,
                                          _scenario_map(loss_table, None),
                                          declared_weights,
                                          bootstrap_replicates, bootstrap_seed)
    selected_row = next(row for row in loss_table["candidates"]
                        if row.get("candidate") == winner)
    result = {
        "schema": "t1-common-strong-selection/v1",
        "analysis_phase": DEVELOPMENT_PHASE,
        "selected": winner,
        "rule": _frozen_rule(winner, loss_table),
        "declared_order": declared_order,
        "outcomes": outcomes,
        "tied_candidates": tied if tie else [winner],
        "winner_mean_loss": best_row["mean_loss"],
        "tie_tolerance": tolerance,
        "mean_loss": {row["candidate"]: row["mean_loss"] for row in evidence},
        "evidence": evidence,
        "unscorable": unscorable,
        "selection_basis": ("development mean primary loss per candidate over "
                            "the paired blocks; tie-break: median-horizon "
                            "candidate first, then the declared list order"),
        "block_scenarios": scenario_coverage,
        "blocks": block_units,
        "bootstrap": bootstrap,
        "bootstrap_design_sha256": bootstrap["design_sha256"],
        "loss_table_sha256": loss_table_sha256(loss_table),
        "notes": [item for item in _STATISTICS_LIMITS],
    }
    if outcomes["insufficient_statistical_evidence"]:
        result["insufficient_statistical_evidence_detail"] = {
            "candidates": sparse,
            "minimum_development_blocks": int(minimum_development_blocks),
            "why": ("at least one candidate has fewer scored development "
                    "blocks than the pre-declared floor; the selection is "
                    "still reported, but the sample cannot size a "
                    "confirmation run on its own"),
        }
    if outcomes["insufficient_scenario_coverage"]:
        result["insufficient_scenario_coverage_detail"] = {
            "scenarios": scenario_coverage,
            "why": ("the development block set covers fewer than two business "
                    "scenarios, so a cross-scenario statement cannot be made "
                    "and the cross-scenario weights cannot be applied"),
        }
    if outcomes["zero_variance"]:
        result["zero_variance_detail"] = {
            "candidate": winner,
            "why": ("every scored development block gives this candidate the "
                    "same loss, so the sample carries no dispersion "
                    "information; selection is still reported (a candidate may "
                    "legitimately win with zero variance) but it must not be "
                    "read as a comparison of the five candidates"),
        }
    if tie:
        result["tie_break_applied"] = {
            "tied": tied,
            "winner": winner,
            "first_rule": "median-horizon candidate first ("
                          + ", ".join(MEDIAN_HORIZON_NAMES) + ")",
            "second_rule": "then the declared list order",
        }
    del selected_row
    return result


def _break_tie(tied: list, declared_order: list) -> str:
    """Median-horizon candidate first, then the declared list order.

    The median preference is a HEAD-START inside the declared order: a
    candidate that the caller did not declare cannot be selected, so a
    declared order without a median-horizon name falls straight through to the
    declared order itself.
    """
    ranked = [name for name in declared_order if name in tied]
    if not ranked:
        raise SelectionError(
            f"tied candidates {tied!r} have no name in the declared order "
            f"{declared_order!r}; the tie cannot be broken deterministically, "
            "and the fixed declared list order is the frozen tie-break")
    for name in MEDIAN_HORIZON_NAMES:
        if name in ranked:
            ranked.remove(name)
            ranked.insert(0, name)
            break
    return ranked[0]
    # no declared name matches: fall back to the declared order of the tied
    # names themselves so the choice stays deterministic and reported
    raise SelectionError(
        f"tied candidates {tied!r} have no name in the declared order "
        f"{declared_order!r}; the tie cannot be broken deterministically")


def _frozen_rule(winner: str, loss_table: dict) -> dict:
    """The rule or the fixed h that the artifact freezes, with its source."""
    row = next((item for item in loss_table["candidates"]
                if item.get("candidate") == winner), {})
    kind = str(row.get("kind") or "")
    if kind == "fixed_h" or (kind == "" and winner in FIXED_CANDIDATE_QUANTILES):
        horizon = row.get("horizon_s")
        if horizon is None:
            raise SelectionError(
                f"candidate {winner!r} is a fixed horizon but the loss table "
                "carries no horizon_s for it; a fixed h must be frozen as a "
                "number, not as a name")
        return {
            "kind": "fixed_h",
            "selected": winner,
            "name": winner,
            "quantile": row.get("quantile"),
            "quantile_source": row.get("quantile_source",
                                       DEVELOPMENT_BASELINE),
            "horizon_s": _require_finite(horizon,
                                         f"candidates[{winner}].horizon_s"),
            "configuration": _configuration_inputs(winner, float(horizon)),
        }
    if winner in RULE_CANDIDATES or kind == "rule":
        return {
            "kind": "rule",
            "selected": winner,
            "name": winner,
            "rule": winner,
            "horizon_s": None,
            "quantile_source": None,
            "configuration": _configuration_inputs(winner, None),
        }
    raise SelectionError(
        f"candidate {winner!r} is neither a rule nor a declared fixed horizon; "
        f"the admissible candidates are {list(DECLARED_CANDIDATES)!r}")


#: The rule candidate names mapped onto the kernel's common_rule vocabulary
#: (CODE/leo_sim/config.py -> VALID_COMMON_RULES).  The frozen artifact records
#: BOTH names so a reader never has to guess which one the config expects.
COMMON_RULE_NAMES = {"mean_eta_offset": "mean_eta",
                     "median_eta_offset": "median_eta"}


def _configuration_inputs(selected: str, horizon: float | None) -> dict:
    """The configuration keys the selection drives (config.py names)."""
    if horizon is None:
        if selected not in COMMON_RULE_NAMES:
            raise SelectionError(
                f"candidate {selected!r} is not a rule candidate with a "
                f"config name; known rules are {sorted(COMMON_RULE_NAMES)!r}")
        common_rule, common_horizon = COMMON_RULE_NAMES[selected], None
    else:
        common_rule, common_horizon = "fixed_horizon", float(horizon)
    return {
        "time_alignment.common_rule": common_rule,
        "time_alignment.common_horizon_s": common_horizon,
        "time_alignment": {"common_rule": common_rule,
                           "common_horizon_s": common_horizon},
        "candidate_name": selected,
        "common_rule_source": ("per_decision_candidate_eta_offsets"
                               if horizon is None
                               else "development_baseline_quantile"),
    }


def _scenario_map(loss_table: dict, block_scenarios) -> dict:
    """unit -> scenario_id, taken from block_scenarios or the loss table."""
    mapping = {}
    if isinstance(block_scenarios, dict):
        return {str(key): str(value) for key, value in block_scenarios.items()}
    if isinstance(block_scenarios, (list, tuple)):
        units = [block["unit"] for block in (loss_table.get("blocks") or [])]
        if len(block_scenarios) != len(units):
            raise SelectionError(
                f"block_scenarios gives {len(block_scenarios)} scenario "
                f"name(s) for {len(units)} scored block(s); the list must name "
                "the scenario of every block in the table, in order")
        for unit, scenario in zip(units, block_scenarios):
            mapping[unit] = str(scenario)
        return mapping
    for block in loss_table.get("blocks") or []:
        if "scenario_id" in block:
            mapping[block["unit"]] = str(block["scenario_id"])
    return mapping


def _check_weights(weights: list) -> list:
    if not weights:
        raise SelectionError(
            "declared_weights is empty: the cross-scenario weights must be "
            "pre-declared")
    total = sum(weights)
    if abs(total - 1.0) > 1e-9:
        raise SelectionError(
            f"declared_weights must sum to 1.0, got {total!r}")
    for index, weight in enumerate(weights):
        if weight < 0.0:
            raise SelectionError(
                f"declared_weights[{index}] must be >= 0, got {weight!r}")
    return weights


def _within_stratum_bootstrap(loss_table, evidence, scenarios, declared_weights,
                              replicates, seed) -> dict:
    """Fixed-seed within-stratum bootstrap of the scenario-weighted mean loss.

    Resampling happens WITHIN one scenario x trace x seed stratum; the
    cross-scenario weights are pre-declared and are bound by their own sha256.
    """
    if isinstance(replicates, bool) or not isinstance(replicates, int) \
            or replicates < 1:
        raise SelectionError(
            f"bootstrap_replicates must be a positive integer, got "
            f"{replicates!r}")
    seed_value = _require_seed(seed, "bootstrap_seed")
    strata = {}
    if scenarios:
        for row in evidence:
            for unit, loss in (loss_table["losses"].get(row["candidate"])
                               or {}).items():
                strata.setdefault(scenarios.get(unit, "unassigned"),
                                  []).append(loss)
    scenario_names = sorted(strata, key=lambda name: str(name))
    if declared_weights is None:
        if not scenario_names:
            weights = []
            weight_map = {}
        else:
            weight_map = {name: 1.0 / len(scenario_names)
                          for name in scenario_names}
            weights = [weight_map[name] for name in scenario_names]
    else:
        if isinstance(declared_weights, dict):
            weight_map = {str(key): _require_finite(
                value, f"declared_weights[{key!r}]")
                for key, value in declared_weights.items()}
        else:
            weight_map = {name: _require_finite(
                value, f"declared_weights[{index}]")
                for index, (name, value) in
                enumerate(zip(scenario_names, declared_weights))}
            if len(declared_weights) != len(scenario_names):
                raise SelectionError(
                    "declared_weights must give one weight per scenario "
                    f"stratum {scenario_names!r}, got "
                    f"{list(declared_weights)!r}")
        missing = [name for name in scenario_names if name not in weight_map]
        if missing:
            raise SelectionError(
                f"declared_weights is missing the scenario(s) {missing!r}")
        weights = [weight_map[name] for name in scenario_names]
    _check_weights(weights) if weights else None
    design = {
        "schema": "t1-within-stratum-bootstrap/v1",
        "unit_of_replication": "scenario x trace x seed (within-stratum)",
        "strata": scenario_names,
        "weights": {name: weight_map[name] for name in scenario_names},
        "replicates": int(replicates),
        "seed": seed_value,
        "confidence": DEFAULT_BOOTSTRAP_CONFIDENCE,
        "method": ("resample the paired block losses WITHIN each scenario "
                   "stratum with random.Random(seed), then combine the "
                   "stratum means with the pre-declared weights"),
    }
    if not scenario_names:
        design["statistic"] = None
        design["note"] = ("no scenario is attached to the blocks, so the "
                          "cross-scenario weighted mean is not computable")
        # the digest is computed LAST, so it covers every field of the design
        design["design_sha256"] = sha256_hex(design)
        return design
    rng = random.Random(seed_value)
    stratum_values = {name: list(strata[name]) for name in scenario_names}
    estimates = []
    for _ in range(replicates):
        total = 0.0
        for name, weight in zip(scenario_names, weights):
            values = stratum_values[name]
            total += weight * statistics.fmean(
                values[rng.randrange(len(values))] for _ in range(len(values)))
        estimates.append(total)
    estimates.sort()
    alpha = 1.0 - DEFAULT_BOOTSTRAP_CONFIDENCE
    design["statistic"] = {
        "name": "weighted_mean_primary_loss",
        "estimate": sum(weight * statistics.fmean(stratum_values[name])
                        for name, weight in zip(scenario_names, weights)),
        "low": t1_stats._percentile(estimates, alpha / 2.0),
        "high": t1_stats._percentile(estimates, 1.0 - alpha / 2.0),
        "per_stratum_mean": {name: statistics.fmean(stratum_values[name])
                             for name in scenario_names},
    }
    design["design_sha256"] = sha256_hex(design)
    return design


# ------------------------------------------------------- A3.4 one D per scenario
def resolve_scenario_deadlines(blocks, *, development_delivery_samples=None,
                               declared_business_deadlines=None,
                               declared_horizons=None, multiplier: float = 2.0,
                               source: str = DEVELOPMENT_BASELINE,
                               frozen=None,
                               analysis_phase: str = DEVELOPMENT_PHASE) -> dict:
    """ONE deadline D per business scenario, with its source.

    D is the pre-specified business deadline when there is one; otherwise
    2 x p95 of the legal-candidate delivery sample of the INDEPENDENT
    DEVELOPMENT BASELINE (t1_stats.default_deadline, plan section P9).  Every
    arm and every mode of the scenario shares the result.  A block that carries
    its own per-branch D, or a deadline whose provenance is not the development
    baseline, is refused.

    development_delivery_samples is either {scenario_id: [delay_s, ...]} or a
    list of {"scenario_id": ..., "delay_s": ...} records.
    """
    _require_development_phase(analysis_phase,
                               "resolve_scenario_deadlines")
    if str(source) != DEVELOPMENT_BASELINE:
        raise SelectionError(
            f"resolve_scenario_deadlines source must be "
            f"{DEVELOPMENT_BASELINE!r}, got {source!r}; a deadline derived from "
            "the branch under test or from confirmation data is refused")
    if not isinstance(blocks, (list, tuple)) or not blocks:
        raise SelectionError(
            "resolve_scenario_deadlines requires a non-empty block list")
    samples = _normalize_samples(development_delivery_samples)
    declared = _normalize_declared(declared_business_deadlines,
                                   "declared_business_deadlines")
    horizons = _normalize_declared(declared_horizons, "declared_horizons")
    frozen_map = _normalize_frozen(_coerce_deadline_argument(frozen))

    seen = {}
    for index, block in enumerate(blocks):
        fields = _block_fields(block, index)
        scenario = fields["scenario_id"]
        evaluation = block_eval(block)
        if evaluation.get("deadline_s") is not None:
            seen.setdefault(scenario, set()).add(
                _require_finite(evaluation["deadline_s"],
                                f"blocks[{index}].eval.deadline_s"))
    per_scenario = {}
    for scenario in sorted({_block_fields(block, index)["scenario_id"]
                            for index, block in enumerate(blocks)}):
        declared_deadline = declared.get(scenario)
        samples_here = samples.get(scenario, [])
        can_derive = bool(samples_here) or scenario in horizons
        if declared_deadline is None and scenario in frozen_map and \
                not can_derive:
            # nothing to derive from and a frozen D exists: the frozen value is
            # the authority and is never re-derived.  With derivation input
            # present the derived and frozen values are cross-checked instead.
            per_scenario[scenario] = {
                "scenario_id": scenario,
                "deadline_s": frozen_map[scenario],
                "source": SOURCE_FROZEN_DESIGN,
                "legal_samples": 0,
                "multiplier": None,
                "weak": bool(horizons and scenario in horizons),
                "fresh": False,
                "frozen": True,
                "frozen_note": ("this D comes from the frozen artifact and is "
                                "never re-derived"),
                "branches_seen": sorted(seen.get(scenario, set())),
                "branch_deadline_conflicts": len(seen.get(scenario, set())) > 1,
            }
            if per_scenario[scenario]["branch_deadline_conflicts"]:
                raise SelectionError(
                    f"scenario {scenario!r} carries more than one branch "
                    f"deadline {sorted(seen[scenario])!r}; a per-branch "
                    "derived D is refused because one D must be shared by "
                    "every arm and mode")
            continue
        if declared_deadline is not None:
            per_scenario[scenario] = {
                "scenario_id": scenario,
                "deadline_s": declared_deadline,
                "source": SOURCE_DECLARED_BUSINESS,
                "legal_samples": len(samples.get(scenario, [])),
                "multiplier": None,
                "weak": False,
                "fresh": False,
            }
        else:
            delays = samples.get(scenario, [])
            if not delays and scenario not in horizons:
                raise SelectionError(
                    f"scenario {scenario!r} has neither a declared business "
                    "deadline nor development-baseline delivery samples nor a "
                    "declared horizon; the deadline D is undefined and must "
                    "not be invented or taken from the branch under test")
            report = t1_stats.default_deadline(
                delays, multiplier=multiplier,
                declared_horizon_s=horizons.get(scenario))
            per_scenario[scenario] = {
                "scenario_id": scenario,
                "deadline_s": report["deadline_s"],
                "source": report["source"],
                "legal_samples": len(delays),
                "multiplier": float(multiplier),
                "weak": bool(report["weak"]),
                "fresh": True,
            }
        if scenario in frozen_map:
            frozen_deadline = frozen_map[scenario]
            if abs(per_scenario[scenario]["deadline_s"] - frozen_deadline) \
                    > 1e-12:
                raise SelectionError(
                    f"scenario {scenario!r} resolves to deadline "
                    f"{per_scenario[scenario]['deadline_s']!r} but the frozen "
                    f"design says {frozen_deadline!r}; the frozen D is the "
                    "only D a confirmation run may use")
            per_scenario[scenario]["source"] = SOURCE_FROZEN_DESIGN
            per_scenario[scenario]["frozen"] = True
            per_scenario[scenario]["fresh"] = False
        else:
            per_scenario[scenario]["frozen"] = False
            per_scenario[scenario]["frozen_note"] = (
                "derived now from the development baseline; freeze it with "
                "freeze_selected_design before any confirmation run")
        branches = seen.get(scenario, set())
        per_scenario[scenario]["branches_seen"] = sorted(branches)
        per_scenario[scenario]["branch_deadline_conflicts"] = len(branches) > 1
        if len(branches) > 1:
            raise SelectionError(
                f"scenario {scenario!r} carries more than one branch deadline "
                f"{sorted(branches)!r}; a per-branch derived D is refused "
                "because one D must be shared by every arm and mode")
    return per_scenario


def _normalize_samples(samples) -> dict:
    if samples is None:
        return {}
    result = {}
    if isinstance(samples, dict):
        for scenario, values in samples.items():
            if isinstance(values, (list, tuple)):
                result[str(scenario)] = [
                    _require_finite(value,
                                    f"development_delivery_samples"
                                    f"[{scenario!r}][{index}]")
                    for index, value in enumerate(values)]
            else:
                result[str(scenario)] = [
                    _require_finite(values,
                                    f"development_delivery_samples"
                                    f"[{scenario!r}]")]
        return result
    if isinstance(samples, (list, tuple)):
        for index, record in enumerate(samples):
            _require_mapping(record,
                             f"development_delivery_samples[{index}]")
            if "scenario_id" not in record or "delay_s" not in record:
                raise SelectionError(
                    "development_delivery_samples records need 'scenario_id' "
                    f"and 'delay_s'; got {sorted(record)!r}")
            result.setdefault(str(record["scenario_id"]), []).append(
                _require_finite(record["delay_s"],
                                f"development_delivery_samples[{index}]"
                                ".delay_s"))
        return result
    raise SelectionError(
        "development_delivery_samples must be a mapping or a list of records, "
        f"got {type(samples).__name__}")


def _normalize_declared(declared, label: str) -> dict:
    if declared is None:
        return {}
    _require_mapping(declared, label)
    for key in declared:
        if key in _FORBIDDEN_BRANCH_DEADLINE_KEYS or "branch" in str(key):
            raise SelectionError(
                f"{label}[{key!r}] is a per-branch deadline; one D per "
                "business scenario is frozen, never one scale per branch")
    result = {}
    for key, value in declared.items():
        number = _require_finite(value, f"{label}[{key!r}]")
        if number <= 0.0:
            raise SelectionError(
                f"{label}[{key!r}] must be > 0, got {value!r}")
        result[str(key)] = number
    return result


def _normalize_frozen(frozen) -> dict:
    """The frozen D per scenario as numbers, from either accepted shape.

    Accepted: {scenario_id: deadline_s} or the deadline section of a loaded
    artifact ({scenario_id: {"deadline_s": ..., ...}}).  frozen=True without
    values is refused: a bare frozen flag binds no scale.
    """
    if frozen is None or frozen is False:
        return {}
    if frozen is True:
        raise SelectionError(
            "frozen=True without values is refused: pass the frozen deadline "
            "mapping {scenario_id: deadline_s} from load_selected_design")
    _require_mapping(frozen, "frozen")
    result = {}
    for scenario, value in frozen.items():
        if isinstance(value, dict):
            if "deadline_s" not in value:
                raise SelectionError(
                    f"frozen[{scenario!r}] carries no deadline_s")
            value = value["deadline_s"]
        result[str(scenario)] = _require_finite(value,
                                                f"frozen[{scenario!r}]")
    return result


# ---------------------------------------------------- A3.5 precision planning
def plan_precision(differences, *, half_width=None, n_boot=None,
                   seed: int = DEFAULT_BOOTSTRAP_SEED) -> dict:
    """Reuse the t1_stats planning rule, with the bounded zero-variance branch.

    differences are the development-block paired differences of the selected
    candidate.  s = stdev(differences) and
    n = max(20, ceil((1.96 * s / 0.005) ** 2)); the formula and its constants
    come from t1_stats (plan section P9).  It is a PLANNING APPROXIMATION and
    the returned document says so; it is never proof of achieved precision.

    When s == 0.0 the lives here: n stays None and the result takes the BOUNDED
    branch, naming why n cannot be sized and what bounded study design would be
    needed - never inf and never a perfect interval.
    """
    target = (t1_stats.DEFAULT_TARGET_HALF_WIDTH if half_width is None
              else _require_finite(half_width, "half_width"))
    if target <= 0.0:
        raise SelectionError(f"half_width must be > 0, got {half_width!r}")
    values = [_require_finite(value, f"differences[{index}]")
              for index, value in enumerate(differences)]
    if not values:
        raise SelectionError(
            "plan_precision requires a non-empty development distribution")
    raw_std = statistics.stdev(values) if len(values) >= 2 else 0.0
    zero_variance = raw_std <= ZERO_VARIANCE_TOLERANCE
    observed_std = 0.0 if zero_variance else raw_std
    plan = {
        "schema": "t1-precision-plan/v1",
        "rule": PRECISION_RULE,
        "planning_approximation": True,
        "plan_is_proof_of_precision": False,
        "minimum_substantive_difference":
            t1_stats.MINIMUM_SUBSTANTIVE_DIFFERENCE,
        "sensitivity": list(t1_stats.SENSITIVITY_THRESHOLDS),
        "target_half_width": target,
        "z": t1_stats.DEFAULT_Z,
        "min_n": t1_stats.DEFAULT_MIN_N,
        "observed_std": observed_std,
        "raw_stdev": raw_std,
        "zero_variance_tolerance": ZERO_VARIANCE_TOLERANCE,
        "development_blocks": len(values),
        "n": None,
        "bounded": False,
        "zero_variance": zero_variance,
        "resampling_check": {
            "pending": ("resample the development distribution at the planned "
                        "n; run it only after the deployment can afford the "
                        "nested bootstrap")},
    }
    if zero_variance:
        plan["n"] = None
        plan["bounded"] = True
        plan["reason"] = (
            "every development block shows the SAME paired difference, so the "
            "observed variance is zero and the planning formula cannot size a "
            "confirmation run: no finite n is derived, and an infinite n or a "
            "perfect interval would be a false claim of precision")
        plan["bounded_design"] = {
            "needed": ("a study design that creates replicated between-block "
                       "dispersion before a confirmation size can be sized"),
            "options": [
                "run the confirmation over MORE independent development "
                "blocks (trace x seed strata) until the paired difference "
                "carries non-zero dispersion",
                "if the mechanism is deterministic on the strata available, "
                "change the unit of replication to one that is independent "
                "(packet is NOT an independent repeat) or run several traces "
                "per scenario and treat the trace as the replicate",
                "state the result as a bounded NEGATIVE/degenerate outcome "
                "(the sample cannot discriminate) instead of claiming "
                "precision",
            ],
            "not_admissible": [
                "an infinite n",
                "a zero-width or perfect interval",
                "a confirmation size taken from a confirmation sample",
            ],
        }
        plan["resampling_check"] = {
            "pending": None,
            "why": ("a zero-variance development sample cannot resample any "
                    "dispersion into an interval"),
        }
        return plan
    planned = t1_stats.plan_sample_size(observed_std, half_width=target)
    plan["n"] = planned
    plan["plan_source"] = ("t1_stats.plan_sample_size(s=observed_std, "
                           "half_width=target)")
    if n_boot is None:
        plan["resampling_check"] = {
            "planned_n_for_resampling": planned,
            "pending": ("pass n_boot to run t1_stats.precision_report on the "
                        "development distribution "
                        f"(x{len(values)} blocks would be {planned} x n_boot "
                        "inner bootstraps)"),
        }
    else:
        plan["resampling_check"] = t1_stats.precision_report(
            values, half_width=target, n_boot=int(n_boot), seed=int(seed))
        plan["resampling_check"]["is_planning_approximation"] = True
    return plan


# ------------------------------------------------- A3.6 freeze / load / check
_REQUIRED_ARTIFACT_FIELDS = (
    "schema", "created_utc", "analysis_phase", "candidates", "selection",
    "loss_table", "loss_table_sha256", "deadline", "deadline_sha256",
    "blocks", "block_set_sha256", "confirm_seeds", "confirm_seeds_sha256",
    "development_seeds", "rule", "configuration_inputs", "sample_size",
    "cost_rationale", "precision", "statistics", "bootstrap",
    "bootstrap_design_sha256", "artifact_sha256")


def freeze_selected_design(selection, *, deadline=None, blocks,
                           confirm_seeds=None,
                           sample_size, out_path,
                           development_delivery_samples=None,
                           declared_business_deadlines=None,
                           declared_horizons=None, multiplier: float = 2.0,
                           declared_weights=None,
                           precision_n_boot=None,
                           cell_cost_s: float = DEFAULT_CELL_COST_S,
                           created_utc=None) -> dict:
    """Write selected_design.json binding everything the confirmation needs.

    The file binds: the candidate loss table (and its sha256), the common D per
    scenario with its source (and their sha256), the rule or fixed h and the
    parameters actually used, the configuration inputs, the development block
    set with its identity hashes, the confirmation seeds (and their sha256),
    the within-stratum bootstrap design, the precision plan and the n / cost
    rationale.  Every hash is recomputed by load_selected_design.
    """
    _require_mapping(selection, "selection")
    if selection.get("analysis_phase", DEVELOPMENT_PHASE) != DEVELOPMENT_PHASE:
        raise SelectionError(
            "freeze_selected_design refuses a selection made on "
            f"{selection.get('analysis_phase')!r} data")
    for field in ("rule", "outcomes", "loss_table_sha256", "selected"):
        if field not in selection:
            raise SelectionError(
                f"selection is missing the field {field!r}; freeze the result "
                "of select_common_strong")
    if not isinstance(blocks, (list, tuple)) or not blocks:
        raise SelectionError(
            "freeze_selected_design requires a non-empty development block set")
    if confirm_seeds is None:
        raise SelectionError(
            "freeze_selected_design is missing 'confirm_seeds'; the frozen "
            "artifact must bind the confirmation seeds it authorises")
    seeds = _normalize_seed_list(confirm_seeds, "confirm_seeds")
    if not seeds:
        raise SelectionError(
            "confirm_seeds is empty: the frozen artifact must bind a non-empty "
            "confirmation seed list")

    loss_table = _require_mapping(selection.get("loss_table"), "loss_table")
    recomputed = loss_table_sha256(loss_table)
    if recomputed != selection.get("loss_table_sha256"):
        raise SelectionError(
            "selection.loss_table_sha256 does not match the loss table it "
            f"carries ({selection.get('loss_table_sha256')!r} != "
            f"{recomputed!r})")

    deadlines = resolve_scenario_deadlines(
        blocks, development_delivery_samples=development_delivery_samples,
        declared_business_deadlines=declared_business_deadlines,
        declared_horizons=declared_horizons, multiplier=multiplier,
        frozen=deadline)
    block_set = block_set_document(blocks)
    development_seeds = sorted({_block_fields(block, index)["seed"]
                                for index, block in enumerate(blocks)})
    overlap = sorted(set(seeds) & set(development_seeds))
    if overlap:
        raise SelectionError(
            f"confirm_seeds overlap the development seeds: {overlap!r}; the "
            "confirmation traces must be independent of the development ones")
    if len(set(seeds)) != len(seeds):
        raise SelectionError(
            f"confirm_seeds repeat a seed: {seeds!r}")

    # every development block must actually be scorable with the frozen D: a
    # design frozen on a block set that cannot be scored would bind a
    # selection nobody can reproduce
    if "candidates" not in loss_table:
        raise SelectionError(
            "freeze_selected_design: the loss table carries no 'candidates' "
            "field")
    checked = evaluate_candidates(loss_table["candidates"], blocks,
                                  deadline=deadlines)
    if checked["unscored"]:
        raise SelectionError(
            "freeze_selected_design refuses a block set with unscorable "
            "blocks: " + json.dumps(checked["unscored"], ensure_ascii=False))

    scenario_of_unit = {entry["unit"]: entry["scenario_id"]
                       for entry in block_set["blocks"]}
    bootstrap = _within_stratum_bootstrap(
        loss_table,
        [{"candidate": row["candidate"]} for row in loss_table["candidates"]],
        scenario_of_unit, declared_weights,
        (selection.get("bootstrap") or {}).get(
            "replicates", DEFAULT_BOOTSTRAP_REPLICATES),
        (selection.get("bootstrap") or {}).get("seed", DEFAULT_BOOTSTRAP_SEED))
    differences = _paired_differences(selection)
    plan = plan_precision(differences, n_boot=precision_n_boot)
    plan["paired_with"] = _precision_reference(selection)
    plan["paired_difference_definition"] = (
        "paired per block: the selected candidate's primary loss minus its "
        "strongest rival's (the sample the primary comparison is defined on)")
    size = _normalize_sample_size(sample_size, seeds, plan)
    per_scenario = _count_blocks_per_scenario(blocks)
    cost = _cost_rationale(size["n"], per_scenario,
                           _count_arms_modes(blocks), cell_cost_s)
    candidates_doc = _candidates_document(selection, loss_table)
    statistics_doc = {
        "unit_of_replication": block_set["unit_of_replication"],
        "blocks": len(blocks),
        "independent_blocks": block_set["independent_blocks"],
        "bootstrap_replicates": (selection.get("bootstrap") or {}).get(
            "replicates", DEFAULT_BOOTSTRAP_REPLICATES),
        "bootstrap_seed": (selection.get("bootstrap") or {}).get(
            "seed", DEFAULT_BOOTSTRAP_SEED),
        "minimum_substantive_difference":
            t1_stats.MINIMUM_SUBSTANTIVE_DIFFERENCE,
        "sensitivity": list(t1_stats.SENSITIVITY_THRESHOLDS),
        "plan_sample_size_rule": PRECISION_RULE,
        "planning_approximation": True,
        "plan_is_proof_of_precision": False,
        "outcomes": dict(selection["outcomes"]),
        "limits": list(_STATISTICS_LIMITS),
    }
    document = {
        "schema": SELECTED_DESIGN_SCHEMA,
        "created_utc": created_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                    time.gmtime()),
        "analysis_phase": DEVELOPMENT_PHASE,
        "candidates": candidates_doc,
        "selection": {
            "selected": selection["selected"],
            "rule": selection["rule"],
            "outcomes": dict(selection["outcomes"]),
            "tied_candidates": list(selection.get("tied_candidates")
                                    or [selection["selected"]]),
            "selection_basis": selection.get("selection_basis"),
            "tie_tolerance": selection.get("tie_tolerance"),
            "mean_loss": dict(selection.get("mean_loss") or {}),
            "loss_table_sha256": selection["loss_table_sha256"],
        },
        "loss_table": loss_table,
        "loss_table_sha256": recomputed,
        "deadline": deadlines,
        "deadline_sha256": sha256_hex(deadlines),
        "blocks": block_set,
        "block_set_sha256": block_set["set_sha256"],
        "confirm_seeds": seeds,
        "confirm_seeds_sha256": sha256_hex(seeds),
        "development_seeds": development_seeds,
        "rule": dict(selection["rule"]),
        "configuration_inputs": {
            "time_alignment": dict(selection["rule"]["configuration"][
                "time_alignment"]),
            "common_horizon_source": selection["rule"]["kind"],
            "quantile_source": selection["rule"].get("quantile_source"),
        },
        "sample_size": size,
        "cost_rationale": cost,
        "precision": plan,
        "statistics": statistics_doc,
        "bootstrap": bootstrap,
        "bootstrap_design_sha256": bootstrap["design_sha256"],
        "required_arms": sorted(_counts(block_set["blocks"], "arm")),
        "required_modes": sorted(_counts(block_set["blocks"], "mode")),
        "scenarios": sorted(per_scenario),
        "written_by": "CODE/experiment_platform/design_selection.py::"
                      "freeze_selected_design",
    }
    document["artifact_sha256"] = sha256_hex(
        {key: value for key, value in document.items()
         if key != "artifact_sha256"})
    _atomic_write_json(out_path, document)
    return document


def _coerce_deadline_argument(deadline):
    """Accept a numeric deadline mapping or a deadline SECTION of records.

    The T1 driver records a deadline section
    ({scenario: {"deadline_s": ..., "source": ...}}); a caller that already
    resolved the numbers passes {scenario: seconds}.  Both mean "the
    pre-specified D of this scenario" and are normalised here, so the parameter
    has one meaning everywhere and a per-scenario record is never read as a
    per-branch scale.
    """
    if deadline is None:
        return None
    _require_mapping(deadline, "deadline")
    records = any(isinstance(value, dict) for value in deadline.values())
    if records:
        return {str(scenario): (record.get("deadline_s")
                                if isinstance(record, dict) else record)
                for scenario, record in deadline.items()}
    return {str(scenario): value for scenario, value in deadline.items()}


def _normalize_seed_list(seeds, label: str) -> list:
    if isinstance(seeds, (str, bytes)) or not isinstance(seeds, (list, tuple)):
        raise SelectionError(
            f"{label} must be a list of integer seeds, got "
            f"{type(seeds).__name__}")
    return [_require_seed(seed, f"{label}[{index}]")
            for index, seed in enumerate(seeds)]


def _paired_differences(selection: dict) -> list:
    """The paired differences the primary comparison is defined on.

    Block by block: the selected candidate's primary loss minus the loss of its
    strongest RIVAL (the candidate with the second smallest mean loss).  This is
    the dispersion the confirmation has to resolve, so it - not one candidate's
    own spread - is what plan_precision sizes.  The reference is reported in the
    artifact so the choice is auditable.
    """
    table = selection.get("loss_table") or {}
    losses = table.get("losses") or {}
    selected = selection.get("selected")
    values = losses.get(selected) or {}
    if not values:
        raise SelectionError(
            f"the selected candidate {selected!r} has no scored block in the "
            "loss table, so no paired difference can be formed")
    means = {}
    for name, rows in losses.items():
        if name == selected or not rows:
            continue
        means[name] = statistics.fmean(float(value) for value in rows.values())
    if not means:
        return [0.0 for _ in sorted(values)]
    reference = min(means, key=lambda name: (means[name], name))
    other = losses[reference]
    units = sorted(set(values) & set(other))
    if not units:
        raise SelectionError(
            f"the selected candidate {selected!r} and its reference "
            f"{reference!r} share no scored block; the paired difference "
            "cannot be formed")
    return [float(values[unit]) - float(other[unit]) for unit in units]


def _precision_reference(selection: dict) -> str | None:
    """The rival the precision paired differences are taken against."""
    table = selection.get("loss_table") or {}
    losses = table.get("losses") or {}
    selected = selection.get("selected")
    means = {name: statistics.fmean(float(value) for value in rows.values())
             for name, rows in losses.items()
             if name != selected and rows}
    if not means:
        return None
    return min(means, key=lambda name: (means[name], name))


def _normalize_sample_size(sample_size, seeds: list, plan: dict) -> dict:
    _require_mapping(sample_size, "sample_size")
    if "n" not in sample_size:
        raise SelectionError(
            "sample_size is missing 'n'; the frozen artifact must bind the "
            "confirmation size")
    n = sample_size["n"]
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise SelectionError(
            f"sample_size.n must be a positive integer, got {n!r}")
    if plan.get("bounded") and sample_size.get("bounded_design") is None:
        # a zero-variance development sample still needs the count it will run
        pass
    document = {
        "n": n,
        "per_scenario": bool(sample_size.get("per_scenario", True)),
        "scenarios": sorted(sample_size.get("scenarios")
                            or sample_size.get("per_scenario_n") or {}),
        "declared_seeds": len(seeds),
        "planning_n": plan.get("n"),
        "planning_rule": plan.get("rule"),
        "planning_approximation": True,
        "bounded": bool(plan.get("bounded")),
        "rule": ("n is the number of INDEPENDENT blocks (scenario x trace x "
                 "seed) the confirmation must compile per scenario; the "
                 "planning formula is an approximation, not proof of precision"),
    }
    if plan.get("bounded"):
        document["note"] = (
            "the development variance is zero, so this n is a DECLARED "
            "bounded size and not a planning estimate: read "
            "precision.bounded_design for what would be needed instead")
    return document


def _count_blocks_per_scenario(blocks) -> dict:
    counts = {}
    for index, block in enumerate(blocks):
        scenario = _block_fields(block, index)["scenario_id"]
        counts[scenario] = counts.get(scenario, 0) + 1
    return dict(sorted(counts.items()))


def _count_arms_modes(blocks) -> dict:
    return {
        "arms": sorted({_block_fields(block, index)["arm"]
                        for index, block in enumerate(blocks)}),
        "modes": sorted({_block_fields(block, index)["mode"]
                         for index, block in enumerate(blocks)}),
    }


def _cost_rationale(n: int, per_scenario: dict, arms_modes: dict,
                    cell_cost_s: float) -> dict:
    cell_cost = _require_finite(cell_cost_s, "cell_cost_s")
    if cell_cost <= 0.0:
        raise SelectionError(
            f"cell_cost_s must be > 0, got {cell_cost_s!r}")
    arms = len(arms_modes["arms"])
    modes = len(arms_modes["modes"])
    cells_per_scenario = n * arms * modes
    return {
        "cell_model": ("one cell = one independent block x one arm x one mode; "
                       "the wall-clock assumption is the contract cell budget, "
                       "not a measurement"),
        "cell_wall_assumption_s": cell_cost,
        "cells_per_scenario": cells_per_scenario,
        "scenarios": len(per_scenario),
        "cells": cells_per_scenario * len(per_scenario),
        "wall_budget_s": cell_cost * cells_per_scenario * len(per_scenario),
        "arms": arms_modes["arms"],
        "modes": arms_modes["modes"],
        "n_per_scenario": n,
        "formula": "cells = scenarios x n x arms x modes",
        "is_measurement": False,
    }


def _candidates_document(selection: dict, loss_table: dict) -> list:
    rows = []
    for row in loss_table.get("candidates") or []:
        rows.append({
            "candidate": row.get("candidate"),
            "kind": row.get("kind"),
            "horizon_s": row.get("horizon_s"),
            "quantile": row.get("quantile"),
            "quantile_source": row.get("quantile_source"),
            "scored_blocks": row.get("scored_blocks"),
            "mean_loss": row.get("mean_loss"),
            "variance": row.get("variance"),
        })
    if not rows:
        raise SelectionError(
            "freeze_selected_design: the loss table carries no candidates")
    return rows


def _atomic_write_json(path, document) -> None:
    path = Path(path)
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                         suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def load_selected_design(path) -> dict:
    """Load a frozen design and REFUSE a file that does not bind everything.

    Every embedded hash is recomputed from the file itself (loss table, block
    set, deadline, confirmation seeds, bootstrap design, artifact digest), and
    a missing field names itself in the refusal.
    """
    path = Path(path)
    if not path.exists():
        raise SelectionError(f"selected design file missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SelectionError(
            f"selected design file is not JSON: {exc}") from exc
    _require_mapping(document, "selected_design")
    missing = [field for field in _REQUIRED_ARTIFACT_FIELDS
               if field not in document]
    if missing:
        raise SelectionError(
            f"selected design is missing the field(s) {missing!r}; a frozen "
            "artifact that does not bind them cannot be loaded")
    if document["schema"] != SELECTED_DESIGN_SCHEMA:
        raise SelectionError(
            f"selected design schema is {document['schema']!r}, expected "
            f"{SELECTED_DESIGN_SCHEMA!r}")
    if document["analysis_phase"] != DEVELOPMENT_PHASE:
        raise SelectionError(
            f"selected design analysis_phase is "
            f"{document['analysis_phase']!r}; only a development selection may "
            "be frozen")

    expected = {
        "loss_table_sha256": loss_table_sha256(document["loss_table"]),
        "block_set_sha256": document["blocks"].get("set_sha256")
                            if isinstance(document["blocks"], dict) else None,
        "deadline_sha256": sha256_hex(document["deadline"]),
        "confirm_seeds_sha256": sha256_hex(document["confirm_seeds"]),
    }
    for field, value in expected.items():
        if field == "block_set_sha256":
            block_set = document["blocks"]
            if not isinstance(block_set, dict):
                raise SelectionError(
                    "selected design 'blocks' must be a block-set document")
            recomputed_blocks = sha256_hex(
                {key: item for key, item in block_set.items()
                 if key != "set_sha256"})
            if recomputed_blocks != block_set.get("set_sha256"):
                raise SelectionError(
                    "block_set_sha256 does not recompute from the block set "
                    "in the file")
            value = recomputed_blocks
        if value != document[field]:
            raise SelectionError(
                f"{field} does not recompute from the file "
                f"({document[field]!r} != {value!r})")
    expected_artifact = sha256_hex(
        {key: value for key, value in document.items()
         if key != "artifact_sha256"})
    if expected_artifact != document["artifact_sha256"]:
        raise SelectionError(
            "artifact_sha256 does not recompute from the file "
            f"({document['artifact_sha256']!r} != {expected_artifact!r})")
    if document.get("bootstrap_design_sha256") is not None:
        design = document.get("bootstrap")
        recomputed_design = sha256_hex(
            {key: value for key, value in design.items()
             if key != "design_sha256"}) if isinstance(design, dict) else None
        if recomputed_design != document["bootstrap_design_sha256"]:
            raise SelectionError(
                "bootstrap_design_sha256 does not recompute from the bootstrap "
                "design in the file")
        if isinstance(design, dict) and design.get("design_sha256") != \
                document["bootstrap_design_sha256"]:
            raise SelectionError(
                "bootstrap.design_sha256 disagrees with "
                "bootstrap_design_sha256 in the file")
    for field in ("loss_table", "deadline", "blocks", "rule",
                  "configuration_inputs", "sample_size", "cost_rationale",
                  "statistics", "precision", "selection"):
        _require_mapping(document[field], field)
    if not isinstance(document["candidates"], list) or not document["candidates"]:
        raise SelectionError(
            "candidates must be a non-empty list of candidate records")
    if document["selection"].get("selected") != document["rule"].get(
            "selected"):
        raise SelectionError(
            "selection.selected and rule.selected disagree in the file: "
            f"{document['selection'].get('selected')!r} != "
            f"{document['rule'].get('selected')!r}")
    if document["selection"].get("loss_table_sha256") != \
            document["loss_table_sha256"]:
        raise SelectionError(
            "selection.loss_table_sha256 disagrees with loss_table_sha256 in "
            "the file")
    if document["configuration_inputs"].get("common_horizon_source") != \
            document["rule"].get("kind"):
        raise SelectionError(
            "configuration_inputs.common_horizon_source disagrees with the "
            "frozen rule kind")
    frozen_horizon = document["configuration_inputs"]["time_alignment"].get(
        "common_horizon_s")
    if document["rule"].get("kind") == "fixed_h":
        if frozen_horizon != document["rule"].get("horizon_s"):
            raise SelectionError(
                "the frozen fixed horizon and the configuration input "
                "disagree in the file")
    elif frozen_horizon is not None:
        raise SelectionError(
            "a rule candidate must freeze common_horizon_s=None, got "
            f"{frozen_horizon!r}")
    return document


def _declared_confirmation_blocks(design: dict):
    """How many independent confirmation blocks the design actually generates.

    sample_size.n is a CLAIM; this is the evidence.  When the design records
    the seeds it authorises, their number is that evidence; when it records a
    larger declared count beside a shorter seed list, the shorter one wins and
    the mismatch is refused by check_confirmation_matrix.
    """
    size = design.get("sample_size")
    if not isinstance(size, dict):
        return []
    seeds = design.get("confirm_seeds")
    if isinstance(seeds, (list, tuple)):
        return list(range(len(seeds)))
    declared = size.get("declared_seeds")
    if isinstance(declared, int) and not isinstance(declared, bool):
        return list(range(declared))
    return []


def check_confirmation_matrix(design: dict, blocks, *,
                              analysis_phase: str = CONFIRMATION_PHASE) -> None:
    """Refuse a confirmation matrix that does not match the frozen design.

    For every scenario the number of INDEPENDENT blocks (scenario x trace x
    seed) must be exactly the frozen n; confirmation seeds must not repeat and
    must not overlap the development seeds; and every block must list every
    arm/mode the design needs.  A design that only DECLARES sample_size while
    generating fewer seeds is refused.
    """
    if str(analysis_phase) != CONFIRMATION_PHASE:
        raise SelectionError(
            f"check_confirmation_matrix expects analysis_phase="
            f"{CONFIRMATION_PHASE!r}, got {analysis_phase!r}")
    _require_mapping(design, "design")
    for field in ("confirm_seeds", "deadline", "required_arms",
                  "required_modes", "sample_size"):
        if field not in design:
            raise SelectionError(
                f"design is missing the field {field!r}; load it with "
                "load_selected_design")
    seeds = _normalize_seed_list(design["confirm_seeds"], "confirm_seeds")
    if not seeds:
        raise SelectionError(
            "design.confirm_seeds is empty: nothing was authorised to run")
    repeats = sorted({seed for seed in seeds if seeds.count(seed) > 1})
    if repeats:
        raise SelectionError(
            f"confirm_seeds repeat: {repeats!r}; a repeated confirmation seed "
            "is the same block twice and does not raise n")
    n = design["sample_size"].get("n")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise SelectionError(
            f"design.sample_size.n must be a positive integer, got {n!r}")
    per_scenario = bool(design["sample_size"].get("per_scenario", True))
    declared_block = sum(1 for _ in _declared_confirmation_blocks(design))
    if declared_block is not None and declared_block != n:
        raise SelectionError(
            f"the frozen design declares sample_size.n={n} but only generates "
            f"{declared_block} confirmation seed(s); a design that only "
            "DECLARES a sample size in metadata while generating fewer seeds "
            "is refused")
    development_seeds = set(_normalize_seed_list(
        design.get("development_seeds") or [], "development_seeds"))
    overlap = sorted(set(seeds) & development_seeds)
    if overlap:
        raise SelectionError(
            f"confirmation seeds {overlap!r} overlap the DEVELOPMENT seeds "
            f"{sorted(development_seeds)!r}; the confirmation traces must be "
            "independent of the development ones")
    if per_scenario and not design["sample_size"].get("independent_per_scenario",
                                                      True):
        raise SelectionError(
            "sample_size requires independent blocks per scenario but the "
            "design does not declare independent_per_scenario")

    entries = []
    for index, block in enumerate(blocks or []):
        _require_mapping(block, f"blocks[{index}]")
        label = f"blocks[{index}]"
        for field in ("scenario_id", "trace_id", "seed", "arm", "mode"):
            if field not in block:
                raise SelectionError(f"{label} is missing the field {field!r}")
        phase = block.get("phase")
        if str(phase) != CONFIRMATION_PHASE:
            raise SelectionError(
                f"{label} phase is {phase!r}; the confirmation matrix may only "
                f"contain {CONFIRMATION_PHASE!r} blocks")
        seed = _require_seed(block["seed"], f"{label}.seed")
        if seed not in set(seeds):
            raise SelectionError(
                f"{label} uses seed {seed!r}, which is not in the frozen "
                f"confirmation seed list: {sorted(set(seeds))!r}")
        entries.append({"scenario_id": str(block["scenario_id"]),
                        "trace_id": str(block["trace_id"]), "seed": seed,
                        "arm": str(block["arm"]), "mode": str(block["mode"]),
                        "stratum": f"{block['scenario_id']}|"
                                   f"{block['trace_id']}|{seed}"})
    required_arms = [str(arm) for arm in design["required_arms"]]
    required_modes = [str(mode) for mode in design["required_modes"]]
    scenarios = sorted(
        {str(key) for key in (design.get("deadline") or {})}
        | {entry["scenario_id"] for entry in entries})
    by_scenario = {}
    for entry in entries:
        by_scenario.setdefault(entry["scenario_id"], []).append(entry)
    for scenario in scenarios:
        rows = by_scenario.get(scenario, [])
        strata = sorted({row["stratum"] for row in rows})
        wanted = n if per_scenario else n
        if len(strata) != wanted:
            raise SelectionError(
                f"scenario {scenario!r} compiles {len(strata)} independent "
                f"block(s) but the frozen design declares sample_size.n="
                f"{wanted}; a design that only DECLARES a sample size while "
                "generating fewer seeds is refused")
        for stratum in strata:
            rows_here = [row for row in rows if row["stratum"] == stratum]
            arms_here = {row["arm"] for row in rows_here}
            modes_here = {row["mode"] for row in rows_here}
            missing_arms = [arm for arm in required_arms
                            if arm not in arms_here]
            if missing_arms:
                raise SelectionError(
                    f"scenario {scenario!r} block {stratum!r} is missing the "
                    f"required arm(s) {missing_arms!r}; every block must list "
                    "every arm it needs")
            missing_modes = [mode for mode in required_modes
                             if mode not in modes_here]
            if missing_modes:
                raise SelectionError(
                    f"scenario {scenario!r} block {stratum!r} is missing the "
                    f"required mode(s) {missing_modes!r}; every block must "
                    "list every mode it needs")
