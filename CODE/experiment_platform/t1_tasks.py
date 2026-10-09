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
import cProfile
import copy
import hashlib
import json
import math
import os
import signal
import statistics
import sys
import tempfile
import time
from pathlib import Path

from CODE.experiment_platform import (artifact_identity, execution_compare,
                                      outcome_metrics)
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


_SUITE_RUNTIME_CONTEXT_ENV = "T1_SUITE_RUNTIME_CONTEXT"
DIAGNOSTIC_PROFILE_ARTIFACT = "diagnostic-kernel-cprofile.pstats"


class _DiagnosticStop(BaseException):
    """Raised by the bounded diagnostic's Python wall-clock alarm."""


class _KernelCProfileScope:
    """Profile only one kernel call, then persist its complete pstats file."""

    def __init__(self, path, duration_s, state, on_start):
        self.path = Path(path)
        self.duration_s = float(duration_s)
        self.state = state
        self.on_start = on_start
        self.profile = cProfile.Profile()
        self.previous_handler = None
        self.previous_timer = None
        self.started_at = None
        self.timer_installed = False
        self.profile_enabled = False

    def _stop(self, _signum, _frame):
        self.state["stop_fired"] = True
        raise _DiagnosticStop(
            f"cProfile diagnostic stopped at {self.duration_s:g} seconds")

    def __enter__(self):
        if not hasattr(signal, "setitimer") or not hasattr(signal, "ITIMER_REAL"):
            raise TaskError(
                "cProfile diagnostic requires a real-time interval timer")
        self.previous_handler = signal.getsignal(signal.SIGALRM)
        self.previous_timer = signal.getitimer(signal.ITIMER_REAL)
        if any(float(value) > 0.0 for value in self.previous_timer):
            raise TaskError(
                "cProfile diagnostic refuses to replace an active real-time timer")
        if self.path.exists() or self.path.is_symlink():
            raise TaskError("diagnostic pstats artifact already exists")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError as exc:
            raise TaskError(
                f"diagnostic pstats artifact cannot be created: {exc}") from exc
        else:
            os.close(descriptor)

        try:
            signal.signal(signal.SIGALRM, self._stop)
            self.profile.enable()
            self.profile_enabled = True
            self.started_at = time.monotonic()
            signal.setitimer(signal.ITIMER_REAL, self.duration_s, 0.0)
            self.timer_installed = True
            self.on_start()
            return self
        except Exception:
            self._restore_runtime_state()
            raise

    def _restore_runtime_state(self):
        if self.timer_installed:
            signal.setitimer(signal.ITIMER_REAL, 0.0, 0.0)
            self.timer_installed = False
        if self.profile_enabled:
            self.profile.disable()
            self.profile_enabled = False
        if self.previous_handler is not None:
            signal.signal(signal.SIGALRM, self.previous_handler)
        if self.previous_timer is not None:
            remaining, interval = self.previous_timer
            if remaining > 0.0 and self.started_at is not None:
                remaining = max(0.0, remaining - (time.monotonic()
                                                  - self.started_at))
            signal.setitimer(signal.ITIMER_REAL, remaining, interval)

    def __exit__(self, exc_type, exc_value, traceback):
        dump_error = None
        try:
            self._restore_runtime_state()
            if self.path.is_symlink() or not self.path.is_file():
                raise OSError("diagnostic pstats artifact identity changed")
            self.profile.dump_stats(str(self.path))
            if self.path.stat().st_size <= 0:
                raise OSError("diagnostic pstats artifact is empty")
        except Exception as error:
            dump_error = error
        if dump_error is not None:
            raise TaskError(
                f"diagnostic pstats artifact is NOT_AVAILABLE: {dump_error}") \
                from dump_error
        return False


def _is_controlled_population_region_profile(config_path):
    """Recognize native population inputs from their effective structure.

    A scenario label is descriptive and caller-controlled, so it cannot decide
    whether the population-input runtime gate applies.
    """
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError):
        return False
    demand = resolved.get("config", {}).get("demand") or {}
    return (demand.get("mode") == "population_gravity"
            and isinstance(demand.get("population_path"), str)
            and bool(demand["population_path"].strip()))


def _validate_suite_runtime_context(args, raw_args):
    """Re-validate the suite's stage, bundle, cell, input and exact argv.

    This is deliberately scoped to the native population-region profile. The
    generic task API and historical profiles retain their existing behavior.
    """
    from CODE.experiment_platform import t1_suite

    raw = os.environ.get(_SUITE_RUNTIME_CONTEXT_ENV)
    if not raw:
        raise TaskError(
            "controlled population-region profile requires a validated "
            "suite stage context")
    try:
        context = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise TaskError("suite stage context is not valid JSON") from exc
    required = {
        "schema", "bundle_dir", "bundle_fingerprint", "contract_path",
        "contract_sha256", "tier", "cell_id", "selected_cell_ids",
        "append", "result_path", "launch_nonce", "launch_ledger_path",
        "simulator_call_ledger_path", "max_simulator_calls", "expires_at",
        "diagnostic",
    }
    if not isinstance(context, dict) or set(context) != required:
        raise TaskError("suite stage context has missing or unknown fields")
    if context["schema"] != "t1-suite-task-runtime-context/v2":
        raise TaskError("suite stage context schema is unsupported")

    bundle_dir = Path(str(context["bundle_dir"]))
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise TaskError("suite stage context bundle directory is unavailable")
    try:
        validation = t1_suite.validate_bundle(bundle_dir)
        bundle = json.loads((bundle_dir / "bundle.json").read_text(
            encoding="utf-8"))
        contract = t1_suite._require_bundle_contract_runtime_ready(bundle)
        readiness = t1_suite.require_runtime_ready_contract(
            contract, source=str(bundle.get("contract_path")))
    except (OSError, ValueError, t1_suite.SuiteError) as exc:
        raise TaskError(f"suite stage context validation failed: {exc}") from exc
    if validation.get("valid") is not True:
        raise TaskError("suite stage context bundle did not validate")
    if readiness is None:
        raise TaskError("controlled profile requires declared stage readiness")
    if (str(bundle_dir.resolve()) != context["bundle_dir"]
            or bundle.get("bundle_fingerprint")
            != context["bundle_fingerprint"]
            or bundle.get("contract_path") != context["contract_path"]
            or bundle.get("contract_sha256") != context["contract_sha256"]):
        raise TaskError("suite stage context identity differs from bundle")

    cell_id = context["cell_id"]
    tier = context["tier"]
    selected_ids = context["selected_cell_ids"]
    if (not isinstance(cell_id, str) or not isinstance(tier, str)
            or not isinstance(selected_ids, list)
            or not all(isinstance(value, str) for value in selected_ids)
            or len(selected_ids) != len(set(selected_ids))
            or cell_id not in selected_ids
            or not isinstance(context["append"], bool)):
        raise TaskError("suite stage context cell selection is malformed")
    cells = {cell.get("cell_id"): cell
             for cell in bundle.get("cells", [])}
    cell = cells.get(cell_id)
    if (cell is None or cell.get("driver")
            != "CODE.experiment_platform.t1_tasks"
            or cell_id not in (bundle.get("tiers") or {}).get(tier, [])):
        raise TaskError("suite stage context does not name an executable cell")

    try:
        diagnostic = t1_suite._cost_probe_diagnostic(
            readiness, cell_id=cell_id)
    except t1_suite.SuiteError as exc:
        raise TaskError(f"suite stage diagnostic validation failed: {exc}") from exc
    if context["diagnostic"] != diagnostic:
        raise TaskError(
            "suite stage diagnostic differs from its contract authorization")

    try:
        launch_identity = t1_suite._suite_launch_identity(context)
    except t1_suite.SuiteError as exc:
        raise TaskError(f"suite launch context is malformed: {exc}") from exc
    if (os.environ.get("T1_SUITE_LAUNCH_NONCE")
            != launch_identity["launch_nonce"]
            or str(Path(os.environ.get("T1_SIM_CALL_LEDGER", "")).resolve())
            != launch_identity["simulator_call_ledger_path"]
            or os.environ.get("T1_SIM_CALL_CONTEXT") != cell_id):
        raise TaskError("suite launch ledger environment identity differs")
    expected_calls = t1_suite._estimated_calls_for_cells([cell])
    if diagnostic is not None:
        expected_calls = int(diagnostic["max_simulator_calls"])
    if launch_identity["max_simulator_calls"] != expected_calls:
        raise TaskError("suite launch call budget differs from the compiled cell")
    cell_args = list(cell.get("args") or [])
    try:
        config_index = cell_args.index("--config")
        cell_config_path = Path(cell_args[config_index + 1]).resolve()
    except (ValueError, IndexError):
        raise TaskError("suite stage context cell has no config profile")
    if args.config is None or Path(args.config).resolve() != cell_config_path:
        raise TaskError("suite stage context profile differs from its cell")

    # The suite appends exactly one --out argument. Strip only that pair, then
    # require every task/mode/arm/seed/override flag to match the compiled cell.
    stripped = []
    out_values = []
    index = 0
    values = list(raw_args)
    while index < len(values):
        value = values[index]
        if value == "--out":
            if index + 1 >= len(values):
                raise TaskError("suite task argv has an incomplete --out")
            out_values.append(values[index + 1])
            index += 2
        else:
            stripped.append(value)
            index += 1
    if len(out_values) != 1 or stripped != list(cell.get("args") or []):
        raise TaskError("suite task argv differs from the compiled cell")
    result_path = Path(str(context["result_path"])).resolve()
    if (Path(args.out).resolve() != result_path
            or result_path.parts[-3:] != ("cells", cell_id, "result.json")):
        raise TaskError("suite task result path differs from its cell context")

    if readiness.get("status") == "COST_PROBE_READY":
        try:
            estimate = t1_suite.estimate_bundle_cost({"cells": [cell]})[
                "simulator_calls"]
            t1_suite.enforce_runtime_stage(
                contract, bundle, tier=tier,
                selected_cell_ids=selected_ids, append=context["append"],
                cells=cells, estimated_calls=estimate,
                bundle_root=bundle_dir,
                source_root=t1_suite.REPO_ROOT)
        except t1_suite.SuiteError as exc:
            raise TaskError(f"suite stage allowlist rejected task: {exc}") from exc
    try:
        t1_suite._validate_suite_launch_ledger(context)
    except t1_suite.SuiteError as exc:
        raise TaskError(f"suite launch ledger rejected task: {exc}") from exc
    return context


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


def select_common_horizon_candidate(branch_rows, candidate_names,
                                    sampled_decisions):
    """Select a common-future rule on one independent development block.

    Every candidate must score every pre-sampled branch using the same
    observation and the same per-direction counterfactual outcome table.
    Candidate order is the predeclared tie-break.  This selector deliberately
    has no access to evaluation seeds or network-arm outcomes.
    """
    names = [str(name) for name in candidate_names]
    expected = [int(decision_id) for decision_id in sampled_decisions]
    by_decision = {int(row["decision_id"]): row for row in branch_rows
                   if row.get("decision_id") is not None}
    summaries = {}
    for name in names:
        values, reasons = [], []
        for decision_id in expected:
            branch = by_decision.get(decision_id)
            if branch is None:
                reasons.append(f"missing sampled decision {decision_id}")
                continue
            items = {str(item.get("candidate")): item
                     for item in branch.get("common_horizon_candidates") or []}
            item = items.get(name)
            if item is None:
                reasons.append(f"candidate missing at decision {decision_id}")
                continue
            digest_pair = {(entry.get("sample_digest"),
                            entry.get("outcome_digest"))
                           for entry in items.values()}
            if (len(digest_pair) != 1 or None in next(iter(digest_pair), ())):
                reasons.append(
                    f"candidate comparisons do not share one frozen sample at decision {decision_id}")
                continue
            loss = item.get("loss")
            if (not item.get("valid") or not item.get("fully_scored")
                    or not isinstance(loss, (int, float))
                    or isinstance(loss, bool) or not math.isfinite(float(loss))):
                reasons.append(f"incomplete candidate score at decision {decision_id}")
                continue
            values.append(float(loss))
        complete = bool(expected) and len(values) == len(expected) and not reasons
        summaries[name] = {
            "status": "eligible" if complete else "ineligible",
            "mean_normalized_branch_loss": (
                statistics.fmean(values) if complete and values else None),
            "sampled_decisions": list(expected),
            "observed_decisions": len(values),
            "reasons": reasons,
        }
    eligible = [(summaries[name]["mean_normalized_branch_loss"], index, name)
                for index, name in enumerate(names)
                if summaries[name]["status"] == "eligible"]
    if not eligible:
        return {
            "status": "NO_STRONG_COMMON",
            "candidate": None,
            "selection_rule": "lowest mean normalized branch loss over all sampled decisions; ties follow frozen candidate order",
            "candidate_order": names,
            "tied_candidates": [],
            "selected_by_tie_break": False,
            "tie_tolerance": 1e-12,
            "candidates": summaries,
        }
    loss = min(item[0] for item in eligible)
    tied = [name for value, _index, name in eligible
            if math.isclose(value, loss, rel_tol=0.0, abs_tol=1e-12)]
    selected = tied[0]
    return {
        "status": "selected",
        "candidate": selected,
        "mean_normalized_branch_loss": loss,
        "selection_rule": "lowest mean normalized branch loss over all sampled decisions; ties follow frozen candidate order",
        "candidate_order": names,
        "tied_candidates": tied,
        "selected_by_tie_break": len(tied) > 1,
        "tie_tolerance": 1e-12,
        "candidates": summaries,
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
        "isl_rate_bps", "candidate_isl_rate_bps",
        "downlink_rate_bps", "downlink_propagation_s",
        "downlink_available", "downlink_queue_scope",
        "downlink_queue_semantics", "downlink_queue_bits")}
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
            and float(value.get("isl_rate_bps")) > 0
            and isinstance(value.get("candidate_isl_rate_bps"), (int, float))
            and not isinstance(value.get("candidate_isl_rate_bps"), bool)
            and math.isfinite(float(value.get("candidate_isl_rate_bps")))
            and float(value.get("candidate_isl_rate_bps")) > 0
            and isinstance(value.get("downlink_rate_bps"), (int, float))
            and not isinstance(value.get("downlink_rate_bps"), bool)
            and math.isfinite(float(value.get("downlink_rate_bps")))
            and float(value.get("downlink_rate_bps")) > 0
            and isinstance(value.get("downlink_propagation_s"), (int, float))
            and not isinstance(value.get("downlink_propagation_s"), bool)
            and math.isfinite(float(value.get("downlink_propagation_s")))
            and float(value.get("downlink_propagation_s")) >= 0
            and value.get("downlink_available") is True
            and value.get("downlink_queue_scope") == "satellite_shared_drr"
            and isinstance(value.get("downlink_queue_bits"), (int, float))
            and not isinstance(value.get("downlink_queue_bits"), bool)
            and math.isfinite(float(value.get("downlink_queue_bits")))
            and float(value.get("downlink_queue_bits")) >= 0)
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


def _routing_audit_log(sink, timeline):
    """Retain each committed route mask and each hold/fail attempt."""
    decisions = []
    for row in sink:
        audit = row.get("four_direction_audit")
        if not isinstance(audit, dict):
            continue
        observation = row.get("observation_at_start") or {}
        decisions.append({
            "t_decision_start": row.get("t_decision_start"),
            "t_decision_commit": row.get("t"),
            "decision_id": row.get("decision_id"),
            "pid": row.get("pid"), "src": row.get("src"),
            "dst": row.get("dst"), "sat": row.get("sat"),
            "kind": row.get("kind"), "candidates": row.get("candidates"),
            "chosen": row.get("chosen"),
            "four_direction_audit": audit,
            "observation_at_start": {
                key: observation.get(key) for key in (
                    "mode", "source", "t_observed", "sat", "neighbours",
                    "candidate_directions", "legal_directions",
                    "routing_status", "kind", "action", "own_queue_bits",
                    "candidate_resources", "time_alignment", "compute_state")
            },
            "estimate_at_start": row.get("estimate_at_start"),
        })
    attempt_aliases = {
        "decision_attempt": "decision_attempt",
        "frozen_inferred_hold": "decision_attempt",
        "commit_rejected": "decision_attempt",
    }
    attempts = []
    for row in timeline:
        source = row.get("milestone")
        canonical = attempt_aliases.get(source)
        if canonical is None:
            continue
        attempt = dict(row)
        attempt["milestone"] = canonical
        attempt["source_milestone"] = row.get("source_milestone", source)
        observation = row.get("observation_at_start") or {}
        attempt["route_status"] = (row.get("route_status")
                                   or row.get("status")
                                   or observation.get("routing_status"))
        if source == "frozen_inferred_hold":
            attempt["attempt_outcome"] = "held"
            attempt.setdefault("action", "hold")
        elif source == "commit_rejected":
            attempt["attempt_outcome"] = "rejected"
        else:
            action = row.get("action")
            attempt["attempt_outcome"] = (
                "held" if action == "hold" else
                "failed" if action == "fail" else "attempted")
        if not isinstance(attempt.get("four_direction_audit"), dict):
            nested_audit = observation.get("four_direction_audit")
            if isinstance(nested_audit, dict):
                attempt["four_direction_audit"] = nested_audit
        attempts.append(attempt)
    return {
        "schema": "t1-routing-audit-log/v1",
        "decision_records": decisions,
        "decision_record_count": len(decisions),
        "attempt_records": attempts,
        "attempt_record_count": len(attempts),
        "coverage_note": "committed records include each four-direction candidate mask, received-history provenance, estimates, scores and action; hold/fail attempts include masks and reasons",
    }


def _control_advertisement_audit(timeline, *, vis_k, packet_bits,
                                 enabled):
    """Compact audit of generated and actually installed whole snapshots."""
    if not enabled:
        return {"enabled": False, "vis_k": int(vis_k),
                "packet_bits_per_hop": int(packet_bits),
                "reason": "control audit was not requested"}
    events = [row for row in timeline
              if row.get("audit") == "control_plane"]
    generated = [row for row in events
                 if row.get("milestone") == "ctrl_advert_generated"]
    arrivals = [row for row in events
                if row.get("milestone") == "ctrl_advert_arrived"]
    by_origin = {}
    for row in arrivals:
        origin = str(row.get("origin"))
        bucket = by_origin.setdefault(origin, {
            "installed_satellites": set(), "max_hops": 0,
            "service_advertisement_installs": 0,
            "downlink_resource_installs": 0,
        })
        if row.get("sat") is not None:
            bucket["installed_satellites"].add(int(row["sat"]))
        bucket["max_hops"] = max(bucket["max_hops"],
                                 int(row.get("hops") or 0))
        if row.get("serve_cells"):
            bucket["service_advertisement_installs"] += 1
        if row.get("downlink_resources"):
            bucket["downlink_resource_installs"] += 1
    compact = {}
    for origin, bucket in sorted(by_origin.items()):
        compact[origin] = {
            "installed_satellites": sorted(bucket["installed_satellites"]),
            "max_hops": bucket["max_hops"],
            "service_advertisement_installs":
                bucket["service_advertisement_installs"],
            "downlink_resource_installs": bucket["downlink_resource_installs"],
        }
    return {
        "enabled": True,
        "vis_k": int(vis_k),
        "packet_bits_per_hop": int(packet_bits),
        "advertisement_scope": "all fields in one snapshot share this hop limit",
        "generated_snapshots": len(generated),
        "accepted_arrivals": len(arrivals),
        "unique_origin_satellite_installs": sum(
            len(value["installed_satellites"]) for value in compact.values()),
        "max_observed_hops": max(
            (value["max_hops"] for value in compact.values()), default=0),
        "by_origin": compact,
    }
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
                           max_branches=DEFAULT_MAX_BRANCHES, window=None,
                           capture_replay=False,
                           calibrate_common_horizon=False):
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
    horizon_projection = None
    horizon_candidates = None
    horizon_offset_source = None
    if calibrate_common_horizon:
        horizon_offset_source = candidate_eta_offsets(decision_rows,
                                                      window=window)
        if horizon_offset_source["offsets"]:
            horizon_projection = project_horizons(
                horizon_offset_source["offsets"])
            horizon_candidates = {
                name: horizon_projection[name]
                for name in ("mean_eta_offset", "median_eta_offset",
                             "p25_offset", "p50_offset", "p75_offset")}
        else:
            horizon_projection = {"status": "NO_CANDIDATE_ETA_OFFSETS"}
            horizon_candidates = {}
    branches, failures = [], []
    capture_id = None
    if capture_replay:
        # This rule is structural and outcome-independent: among the
        # pre-sampled decisions, take the first forward point with at least
        # two legal directions and named resources for every direction.
        for decision_id in sample["decisions"]:
            candidate = next((row for row in decision_rows
                              if row.get("decision_id") == decision_id), None)
            observation = (candidate or {}).get("observation_at_start") or {}
            legal = observation.get("legal_directions") or []
            resources = observation.get("candidate_resources") or {}
            if (len(legal) >= 2 and all(
                    isinstance(resources.get(direction), dict)
                    and resources[direction].get("peer") is not None
                    for direction in legal)):
                capture_id = decision_id
                break
    explanation_replay = None
    for decision_id in sample["decisions"]:
        try:
            document = tac.compare(resolved, rows, geometry, decision_id,
                                   deadline_s, source,
                                   capture_replay=(decision_id == capture_id),
                                   common_horizon_candidates=(
                                       horizon_candidates or None))
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
            "common_horizon_candidates": document.get(
                "common_horizon_candidates") or [],
            "resource_pressure": resource_pressure,
            "resource_pressure_observed": any(
                value["observed"] for value in resource_pressure.values()),
            "trace_identity": (document.get("identity") or {}).get(
                "sources"),
        })
        if decision_id == capture_id:
            explanation_replay = document.get("replay")
    identity = artifact_identity.build_identity(
        config=resolved, trace_digest=source.get("trace_sha256"),
        driver_paths=artifact_identity.execution_chain_paths(),
        extra={"task": "branch_alignment", "decision_ids": sample["decisions"],
               "max_branches": int(max_branches),
               "calibrate_common_horizon": bool(calibrate_common_horizon)})
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
        "measurement_window": {
            "start_s": (None if window is None else float(window[0])),
            "end_s": (None if window is None else float(window[1])),
            "rule": "branch eligibility and even-stride sampling use this "
                    "predeclared decision-time window",
        },
        "sampling": sample,
        "eligibility": {"eligible": len(eligible),
                        "rejected": rejected,
                        "rejected_count": len(rejected),
                        "rule": "forward decision with at least two legal "
                                "directions AT THE OBSERVATION INSTANT; "
                                "the future of the unchosen actions is "
                                "never consulted"},
        "branches": branches,
        "common_horizon_calibration": (
            {
                "status": ("projected" if horizon_offset_source["offsets"]
                           else "NO_CANDIDATE_ETA_OFFSETS"),
                "development_seed": int(resolved["config"]["scenario"]["seed"]),
                "offset_source": horizon_offset_source["rule"],
                "offset_decision_count": horizon_offset_source["decisions"],
                "offset_candidate_count": horizon_offset_source["candidates"],
                "offset_values_sha256": _rows_digest(
                    horizon_offset_source["offsets"]),
                "candidate_definitions": horizon_projection,
                "selection": select_common_horizon_candidate(
                    branches, list(horizon_candidates or {}),
                    sample["decisions"]),
                "sampled_decision_ids": list(sample["decisions"]),
                "branch_evidence_sha256": _rows_digest([
                    {"decision_id": row["decision_id"],
                     "common_horizon_candidates": row.get(
                         "common_horizon_candidates") or []}
                    for row in branches]),
                "selection_scope": (
                    "independent development baseline seed only; all horizon "
                    "candidates share each frozen t0 observation and the same "
                    "per-direction independently replayed outcome table"),
                "cross_arm_leakage": False,
            } if calibrate_common_horizon else {"status": "not_requested"}),
        "explanation_replay": explanation_replay,
        "explanation_selection": {
            "rule": "first sampled forward decision with at least two legal directions and a named peer resource for every legal direction; selection uses no outcome fields",
            "decision_id": capture_id,
            "captured": explanation_replay is not None,
        },
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


def _arm_row(resolved, rows, geometry, arm, *, deadline_s=None, window=None,
             source=None, capture_replay=False, simulator_call_guard=None):
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
    call_scope = (simulator_call_guard(arm)
                  if simulator_call_guard is not None else None)
    if call_scope is None:
        result = kernel.run_simulation(
            local, rows, geometry=geometry, decision_sink=sink,
            timeline_sink=timeline, control_audit=True)
    else:
        with call_scope:
            result = kernel.run_simulation(
                local, rows, geometry=geometry, decision_sink=sink,
                timeline_sink=timeline, control_audit=True)
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
    congestion_metrics = result["congestion_metrics"]
    measurement_window = (window if window is not None
                          else outcome_metrics.build_measurement_window(rows))
    trace_digest = ((source or {}).get("trace_sha256")
                    or (source or {}).get("rows_digest")
                    or _rows_digest(rows))
    outcome_document = outcome_metrics.compare_outcome(
        result, timeline, sink, rows, window=measurement_window,
        deadline_s=deadline_s,
        cost={"service_s": local["config"]["execution"]["compute_delay_s"],
              "servers": local["config"]["execution"][
                  "compute_servers_per_satellite"]},
        context={"cell": f"network-{arm}", "run_id": f"network-{arm}",
                 "mode": local["config"]["time_alignment"][
                     "execution_mode"],
                 "config_sha256": local["sha256"],
                 "trace_sha256": trace_digest})
    network_outcome = outcome_document["network_outcome"]
    outcome_counts = network_outcome["counts"]
    delivered_bits = outcome_counts["delivered_bits"]
    admitted = outcome_counts["admitted"]
    goodput = network_outcome["payload"].get("goodput_bps")
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
            "offered": outcome_counts["offered"], "admitted": admitted,
            "delivered": outcome_counts["delivered"],
            "delivered_bits": delivered_bits,
            "goodput_bps_in_window": goodput,
            "deadline_primary_loss": network_outcome[
                "deadline_primary_loss"],
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
        "measurement_window": network_outcome["window"],
        "network_outcome": network_outcome,
        "total_cost": outcome_document["total_cost"],
        "outcome_document": {
            "schema": outcome_document["schema"],
            "partition_exact": outcome_document["partition_exact"],
            "not_computable": outcome_document["not_computable"],
            "packet_outcomes_sha256": network_outcome[
                "packet_outcomes_sha256"],
            "row_count": len(outcome_document["rows"]),
            "rows": outcome_document["rows"],
        },
        "time_alignment_audit": audit_block,
        "action_log": action_log,
        "routing_audit_log": _routing_audit_log(sink, timeline),
        "fallback_reason_counts": _fallback_reason_counts(action_log),
        "compute": _compute_cost(timeline, local),
        "control": {"bits": dict(result["control"]["bits"]),
                    "counters": dict(result["control"]["counters"])},
        "control_advertisement_audit": _control_advertisement_audit(
            timeline, vis_k=local["config"]["control_plane"]["vis_k"],
            packet_bits=local["config"]["control_plane"]["packet_bits"],
            enabled=True),
        "query_service": result["execution_mode"]["query_service"],
        "congestion_metrics": congestion_metrics,
        "mechanisms": result["mechanisms"],
        "natural_end": result["natural_end"],
        "stop_time_s": result["stop_time_s"],
        "horizon_s": result["horizon_s"],
        "replay": ({
            "captured": True,
            "arm": arm,
            "decision_rows": sink,
            "timeline_rows": timeline,
            "packet_events": result["packet_events"],
            "link_service_windows": result["link_service_windows"],
            "link_available_windows": result["link_available_windows"],
            "queue_state_events": [row for row in timeline
                                    if row.get("milestone") == "queue_state"],
            "topology_trace": result.get("topology_trace"),
            "fates": result["fates"],
            "stop_time_s": result["stop_time_s"],
            "horizon_s": result["horizon_s"],
            "deliveries": result["deliveries"],
            "handover_events": result["handover"]["events"],
            "counts": dict(outcome_counts),
            "cost": outcome_document["total_cost"],
        } if capture_replay else {"captured": False}),
    }


def network_alignment(resolved, rows, geometry, source,
                     arms=NETWORK_ARMS, overrides=None, *, deadline_s=None,
                     window=None, capture_replay=False,
                     simulator_call_guard=None):
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
            arm_rows.append(_arm_row(
                resolved, rows, geometry, arm, deadline_s=deadline_s,
                window=window, source=source,
                capture_replay=capture_replay,
                simulator_call_guard=simulator_call_guard))
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
        "replay_capture": {
            "requested": bool(capture_replay),
            "captured_arms": [row["arm"] for row in arm_rows
                              if (row.get("replay") or {}).get("captured")],
            "rule": "capture all four arms only for the predeclared replay seed; no outcome-based arm or packet selection",
        },
        "deadline": {"deadline_s": (None if deadline_s is None
                                      else float(deadline_s)),
                     "population_window_s": (list(window)
                                             if isinstance(window, tuple)
                                             else window),
                     "rule": "one frozen D and population window shared by all four arms"},
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
             overrides=None, arms=NETWORK_ARMS, capture_replay=False,
             calibrate_common_horizon=False,
             simulator_call_guard=None):
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
            max_branches=max_branches, window=window,
            capture_replay=capture_replay,
            calibrate_common_horizon=calibrate_common_horizon)
    elif task == "network_alignment":
        driver = network_alignment(resolved, rows, geometry, source,
                                   arms=arms, overrides=overrides,
                                   deadline_s=deadline_s, window=window,
                                   capture_replay=capture_replay,
                                   simulator_call_guard=simulator_call_guard)
    else:
        driver = execution_compare.compare(
            resolved, rows, geometry, source,
            modes=tuple(modes) if modes else execution_compare.MODES,
            overrides=overrides, deadline_s=deadline_s, window=window)
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
    demand = resolved["config"].get("demand") or {}
    if demand.get("mode") == "population_gravity":
        population_manifest = manifest.get("population")
        input_population_sha256 = manifest.get("input_sha256")
        source_population_sha256 = (
            population_manifest.get("source_sha256")
            if isinstance(population_manifest, dict) else None)

        def valid_sha256(value):
            return (isinstance(value, str) and len(value) == 64
                    and all(character in "0123456789abcdef"
                            for character in value))

        if (not valid_sha256(input_population_sha256)
                or not valid_sha256(source_population_sha256)):
            raise TaskError(
                "compiled population manifest has a missing or invalid "
                "SHA-256 hash")
        if input_population_sha256 != source_population_sha256:
            raise TaskError(
                "compiled population input hash differs from population "
                "source hash")
        mapped_rows = [
            row for row in rows
            if isinstance(row.get("src_grid_id"), str)
            and row.get("src_grid_id")
            and isinstance(row.get("dst_grid_id"), str)
            and row.get("dst_grid_id")
        ]
        endpoint_pairs = {(row["src_grid_id"], row["dst_grid_id"])
                          for row in mapped_rows}
        source["native_population"] = {
            "mode": "population_gravity",
            "population_source": str(demand.get("population_path")),
            "population_sha256": input_population_sha256,
            "all_rows_have_ground_grid_ids": len(mapped_rows) == len(rows),
            "source_grid_count": len({row["src_grid_id"]
                                       for row in mapped_rows}),
            "destination_grid_count": len({row["dst_grid_id"]
                                            for row in mapped_rows}),
            "directed_od_count": len(endpoint_pairs),
        }
    if demand.get("mode") == "csv" and demand.get("csv_path"):
        input_path = Path(demand["csv_path"])
        summary_path = input_path.with_suffix(".workload.json")
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise TaskError(
                    f"synthetic workload summary is unreadable: {exc}") from exc
            input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
            if (summary.get("schema") != "t1-synthetic-od-workload/v1"
                    or summary.get("input_sha256") != input_hash):
                raise TaskError(
                    "synthetic workload sidecar does not bind the configured CSV")
            manifest_pids = sorted(
                int(item["pid"]) for item in summary.get("packet_manifest", []))
            trace_pids = sorted(int(row["packet_id"]) for row in rows)
            if (summary.get("packet_count") != len(rows)
                    or manifest_pids != trace_pids):
                raise TaskError(
                    "synthetic OD packet manifest differs from the compiled platform trace")
            row_by_pid = {int(row["packet_id"]): row for row in rows}
            od_grid_mapping = {}
            for packet in summary.get("packet_manifest", []):
                pid = int(packet["pid"])
                row = row_by_pid[pid]
                mapping = {"src_grid_id": str(row["src_grid_id"]),
                           "dst_grid_id": str(row["dst_grid_id"])}
                od_id = str(packet["od_id"])
                previous = od_grid_mapping.setdefault(od_id, mapping)
                if previous != mapping:
                    raise TaskError(
                        f"synthetic OD {od_id!r} maps to multiple compiled endpoint pairs")
            summary["compiled_od_mapping"] = dict(sorted(
                od_grid_mapping.items()))
            source["synthetic_workload"] = {
                "input_csv_path": str(input_path),
                "input_csv_sha256": input_hash,
                "summary_path": str(summary_path),
                "summary_sha256": hashlib.sha256(
                    summary_path.read_bytes()).hexdigest(),
                "summary": summary,
                "manifest_matches_compiled_trace": True,
            }
    return resolved, rows, None, source


def network_summary_document(document):
    """Build a bounded metrics/identity sidecar without mutating the result.

    The full replay remains in the primary result and is still required for a
    successful cell.  This summary preserves every packet-outcome row and
    accounting table while omitting event-, decision-, and route-level logs.
    """
    if not isinstance(document, dict) or document.get("task") != \
            "network_alignment":
        raise TaskError("network summary requires a network_alignment task")
    driver = document.get("document")
    if not isinstance(driver, dict) or not isinstance(driver.get("arms"), list):
        raise TaskError("network summary is missing the network driver arms")

    arms = []
    arm_fields = (
        "arm", "config_sha256", "resolved_arm", "seed", "predictor",
        "scope", "request_rate_per_satellite", "outcome",
        "measurement_window", "network_outcome", "total_cost",
        "outcome_document", "time_alignment_audit",
        "fallback_reason_counts", "compute", "control",
        "control_advertisement_audit", "query_service",
        "congestion_metrics", "mechanisms", "natural_end", "stop_time_s",
        "horizon_s",
    )
    for arm in driver["arms"]:
        if not isinstance(arm, dict):
            raise TaskError("network summary arm is not an object")
        summarized = {key: arm[key] for key in arm_fields if key in arm}
        action_log = arm.get("action_log") or {}
        if action_log:
            summarized["action_log"] = {
                key: action_log[key] for key in ("count", "limit", "truncated",
                                                 "rule") if key in action_log}
        routing_log = arm.get("routing_audit_log") or {}
        if routing_log:
            summarized["routing_audit_log"] = {
                key: routing_log[key] for key in (
                    "schema", "decision_record_count", "attempt_record_count",
                    "coverage_note") if key in routing_log}
        replay = arm.get("replay") or {}
        summarized["replay_summary"] = {
            "captured": bool(replay.get("captured")),
            "arm": replay.get("arm", arm.get("arm")),
            "full_payload_omitted": True,
        }
        arms.append(summarized)

    return {
        "schema": "t1-network-summary/v1",
        "completeness": "aggregate_metrics_only; full replay is separate",
        "full_replay_required_for_cell_success": True,
        "task": document.get("task"),
        "task_status": document.get("status"),
        "failed_units": document.get("failed_units"),
        "driver_schema": document.get("driver_schema"),
        "identity": document.get("identity"),
        "source": driver.get("source"),
        "driver_identity": driver.get("identity"),
        "replay_capture": driver.get("replay_capture"),
        "deadline": driver.get("deadline"),
        "fairness": driver.get("fairness"),
        "arms": arms,
        "failures": driver.get("failures"),
        "driver_status": driver.get("status"),
        "units": driver.get("units"),
        "limits": {"task": document.get("limits"),
                   "driver": driver.get("limits")},
        "omitted_payloads": [
            "full replay event streams", "action-log records",
            "routing decision/attempt records",
        ],
    }


def _json_piece_fits(value, nodes=1024, characters=32768):
    """Bound container encoding without constructing its expanded JSON string.

    A single scalar may exceed the character target; it is encoded intact.
    Shared references are counted for each occurrence, just as JSON expands them.
    """
    remaining = [nodes, characters]
    active = set()

    def visit(item):
        remaining[0] -= 1
        if remaining[0] < 0:
            return False
        if isinstance(item, str):
            remaining[1] -= len(item)
            return remaining[1] >= 0
        if not isinstance(item, (dict, list, tuple)):
            return True
        identity = id(item)
        if identity in active:
            raise ValueError("Circular reference detected")
        active.add(identity)
        try:
            if isinstance(item, dict):
                if len(item) * 2 > remaining[0]:
                    return False
                return all(visit(key) and visit(child)
                           for key, child in item.items())
            if len(item) > remaining[0]:
                return False
            return all(visit(child) for child in item)
        finally:
            active.remove(identity)

    return visit(value)


def _write_json_pieces(value, stream, ancestors=None):
    """C-encode small subtrees; never materialize a whole large result string."""
    container = isinstance(value, (dict, list, tuple))
    if not container or _json_piece_fits(value):
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")))
        return
    if ancestors is None:
        ancestors = set()
    identity = id(value)
    if identity in ancestors:
        raise ValueError("Circular reference detected")
    ancestors.add(identity)
    try:
        if isinstance(value, dict):
            stream.write("{")
            for index, key in enumerate(sorted(value)):
                if index:
                    stream.write(",")
                # Delegate supported key conversion/escaping to the standard
                # encoder; remove only the surrounding object and null value.
                key_prefix = json.dumps({key: None}, ensure_ascii=False,
                                        separators=(",", ":"))[1:-5]
                stream.write(key_prefix)
                _write_json_pieces(value[key], stream, ancestors)
            stream.write("}")
        else:
            stream.write("[")
            for start in range(0, len(value), 128):
                batch = value[start:start + 128]
                if start:
                    stream.write(",")
                if _json_piece_fits(batch):
                    stream.write(json.dumps(batch, ensure_ascii=False,
                                            sort_keys=True,
                                            separators=(",", ":"))[1:-1])
                else:
                    for index, child in enumerate(batch):
                        if index:
                            stream.write(",")
                        _write_json_pieces(child, stream, ancestors)
            stream.write("]")
    finally:
        ancestors.remove(identity)


def publish(document, out):
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise TaskError(f"output destination exists: {out}")
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise TaskError(f"output parent must be a real directory: {out.parent}")
    # Large replay graphs expand shared observations repeatedly in JSON.
    # Store shared containers once for network results, preserving every field
    # and event. Legacy/generic documents retain their standard JSON bytes.
    handle, temporary = tempfile.mkstemp(prefix="." + out.name + ".",
                                         suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            if (isinstance(document, dict)
                    and document.get("schema") == SCHEMA_TASK
                    and document.get("task") == "network_alignment"):
                from CODE.experiment_platform.replay_codec import dump_graph
                dump_graph(document, stream)
            else:
                _write_json_pieces(document, stream)
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
    parser.add_argument("--capture-replay", action="store_true",
                        help="retain real event streams for an offline replay")
    parser.add_argument("--compute-servers", type=int, default=None)
    parser.add_argument("--service-s", type=float, default=None)
    parser.add_argument("--packet-bits", type=int, default=None)
    parser.add_argument("--offered-mbps", type=float, default=None)
    parser.add_argument("--update-interval-s", type=float, default=None)
    parser.add_argument("--window-bins", type=int, default=None)
    parser.add_argument("--common-horizon-s", type=float, default=None)
    parser.add_argument("--calibrate-common-horizon", action="store_true",
                        help="project and compare predeclared common-future candidates on this development block")
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_args)
    if bool(args.config) == bool(args.scenario):
        print("T1TASKS REFUSED: give exactly one of --config or --scenario")
        return 2
    suite_runtime_context = None
    if (args.config is not None
            and _is_controlled_population_region_profile(args.config)):
        if args.task != "network_alignment":
            print("T1TASKS REFUSED: controlled native-population input only "
                  "supports its network_alignment suite cell")
            return 2
        try:
            suite_runtime_context = _validate_suite_runtime_context(
                args, raw_args)
        except TaskError as exc:
            print(f"T1TASKS REFUSED: {exc}")
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
    suite_launch_claimed = False
    call_guard = None
    diagnostic = ((suite_runtime_context or {}).get("diagnostic")
                  if suite_runtime_context is not None else None)
    diagnostic_call_count = 0
    diagnostic_kernel_started = 0
    diagnostic_call_exhausted = False
    diagnostic_state = {"stop_fired": False}
    exit_code = 0
    try:
        if suite_runtime_context is not None:
            from CODE.experiment_platform import t1_suite
            try:
                t1_suite._claim_suite_launch_ledger(suite_runtime_context)
            except t1_suite.SuiteError as exc:
                raise TaskError(f"suite launch ledger claim failed: {exc}") from exc
            suite_launch_claimed = True

            def call_guard(arm):
                nonlocal diagnostic_call_count, diagnostic_kernel_started
                nonlocal diagnostic_call_exhausted
                if arm not in NETWORK_ARMS:
                    raise TaskError(
                        f"controlled suite cell requested unknown arm {arm!r}")
                if diagnostic is not None and diagnostic_call_count >= \
                        int(diagnostic["max_simulator_calls"]):
                    diagnostic_call_exhausted = True
                    raise TaskError(
                        "first-call diagnostic has exhausted its one-kernel-call cap")
                profile_path = None
                if diagnostic is not None:
                    profile_path = (args.out.parent
                                    / DIAGNOSTIC_PROFILE_ARTIFACT)
                    if profile_path.exists() or profile_path.is_symlink():
                        raise TaskError(
                            "diagnostic pstats artifact already exists; "
                            "refusing overwrite")
                try:
                    t1_suite._reserve_suite_simulator_call(
                        suite_runtime_context)
                except t1_suite.SuiteError as exc:
                    raise TaskError(
                        f"suite launch call reservation failed: {exc}") from exc
                if diagnostic is not None:
                    diagnostic_call_count += 1
                    def mark_kernel_started():
                        nonlocal diagnostic_kernel_started
                        diagnostic_kernel_started += 1
                    return _KernelCProfileScope(
                        profile_path,
                        diagnostic["profile_duration_s"],
                        diagnostic_state,
                        mark_kernel_started)
                return None

        resolved, rows, geometry, source = design(
            args.config, args.scenario, args.root, overrides)
        document = run_task(
            args.task, resolved, rows, geometry, source,
            deadline_s=_deadline_s(args), max_branches=args.max_branches,
            window=window,
            modes=([m.strip() for m in str(args.modes).split(",") if m.strip()]
                   if args.modes else None),
            arms=(tuple(a.strip() for a in str(args.arms).split(",") if a.strip())
                  if args.arms else NETWORK_ARMS),
            capture_replay=args.capture_replay,
            calibrate_common_horizon=args.calibrate_common_horizon,
            simulator_call_guard=call_guard)
        if diagnostic is not None:
            if diagnostic_state["stop_fired"]:
                raise TaskError(
                    "first-call cProfile diagnostic reached its 30-second cap; "
                    "partial four-arm output is intentionally not published")
            if diagnostic_call_count != 1 or diagnostic_kernel_started != 1:
                raise TaskError(
                    "first-call diagnostic did not start exactly one kernel call")
            if diagnostic_call_exhausted:
                raise TaskError(
                    "first-call diagnostic stopped before another arm; "
                    "partial four-arm output is intentionally not published")
        if args.task == "network_alignment":
            if args.out.exists() or args.out.is_symlink():
                raise TaskError(f"output destination exists: {args.out}")
            summary_out = args.out.with_name(
                args.out.stem + "-summary" + args.out.suffix)
            if summary_out == args.out:
                raise TaskError("network summary path collides with full result")
            publish(network_summary_document(document), summary_out)
        publish(document, args.out)
    except _DiagnosticStop as exc:
        print(f"T1TASKS DIAGNOSTIC STOP: {exc}")
        exit_code = 3
    except TaskError as exc:
        print(f"T1TASKS REFUSED: {exc}")
        exit_code = 2
    finally:
        if suite_launch_claimed:
            from CODE.experiment_platform import t1_suite
            try:
                t1_suite._close_suite_launch_ledger(suite_runtime_context)
            except t1_suite.SuiteError as exc:
                print(f"T1TASKS REFUSED: suite launch close failed: {exc}")
                exit_code = 2
    if exit_code:
        return exit_code
    print(json.dumps({"status": document["status"], "task": document["task"],
                      "out": str(args.out),
                      "failed_units": document["failed_units"]},
                     ensure_ascii=False))
    return 0 if document["status"] == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())
