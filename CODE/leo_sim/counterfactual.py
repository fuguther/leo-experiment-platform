"""Strictly paired counterfactual replay for a single decision.

Implements the harness required by T1-COUNTERFACTUAL-REPLAY-PASS.  The
2026-09-23 platform review fixed the constraints, and this module must not
violate them:

* never deep-copy a SimPy environment;
* never read an un-chosen candidate future out of the original trajectory;
* replay the SAME immutable trace / config / seed to the SAME decision, prove
  the pre-branch state is identical, and then change exactly one action -- the
  target packet action at that one decision.

The pre-branch proof is a fingerprint over every decision committed strictly
before the target (full row) plus the target own pre-commit fields, i.e. the
decision row WITHOUT the chosen action.  If the two fingerprints differ, the
replay did not reach the same state and the comparison is REFUSED rather than
reported: a counterfactual computed from a different branch point is not a
counterfactual.
"""
from __future__ import annotations

import hashlib
import json

from CODE.leo_sim import kernel


class CounterfactualError(RuntimeError):
    """The replay did not produce a usable, strictly paired comparison."""


#: Fields that describe the branch point, i.e. everything the decision row
#: carries BEFORE an action is chosen.  Deliberately excludes "chosen".
PRECOMMIT_FIELDS = (
    "t", "t_decision_start", "decision_id", "state_version",
    "pid", "src", "dst", "sat", "kind", "policy",
    "candidates", "own_queue_bits", "obs", "info_audit",
)

#: Result keys compared between the baseline and the forced replay.
OUTCOME_KEYS = ("fate_counts", "totals", "occupied", "queue_area_bits_s",
                "access", "events_processed")


def _canonical(payload) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _precommit(row: dict) -> dict:
    return {name: row.get(name) for name in PRECOMMIT_FIELDS}


def branch_fingerprint(decision_rows, target_decision_id: int) -> str:
    """Fingerprint the state of the run at the target decision.

    Covers every decision committed strictly before the target plus the
    target own pre-commit fields, so two runs agree here only if they arrived
    at the same branch point in the same way.
    """
    earlier = []
    target = None
    for row in decision_rows:
        if row.get("decision_id") == target_decision_id:
            target = row
            break
        earlier.append(row)
    if target is None:
        raise CounterfactualError(
            "decision %r never occurred in this run" % (target_decision_id,))
    payload = {"earlier": earlier, "target_precommit": _precommit(target)}
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def replay_with_forced_action(resolved: dict, rows: list, *,
                              target_decision_id: int, forced_action: str,
                              geometry=None) -> dict:
    """Replay one run twice and force exactly one action at one decision.

    Returns a dict with:
      verification: the pairing proof (fingerprints, equality, chosen actions)
      baseline:     the unforced run's result plus its sink rows
      counterfactual: the forced run's result plus its sink rows
      outcome_delta: per-key difference of the compared outcome keys
    """
    algorithm = resolved["config"]["learning"]["algorithm"]
    if algorithm != "none":
        raise CounterfactualError(
            "the first version of the counterfactual harness requires a "
            "deterministic router with learning off; got learning.algorithm="
            f"{algorithm!r}")

    base_sink: list = []
    base_timeline: list = []
    baseline = kernel.run_simulation(resolved, rows, geometry=geometry,
                                     decision_sink=base_sink,
                                     timeline_sink=base_timeline)
    cf_sink: list = []
    cf_timeline: list = []
    forced_run = kernel.run_simulation(
        resolved, rows, geometry=geometry,
        decision_sink=cf_sink, timeline_sink=cf_timeline,
        forced_actions={target_decision_id: forced_action})

    base_fp = branch_fingerprint(base_sink, target_decision_id)
    cf_fp = branch_fingerprint(cf_sink, target_decision_id)
    base_row = next(r for r in base_sink
                    if r["decision_id"] == target_decision_id)
    cf_row = next(r for r in cf_sink if r["decision_id"] == target_decision_id)

    verification = {
        "target_decision_id": target_decision_id,
        "baseline_branch_fingerprint": base_fp,
        "counterfactual_branch_fingerprint": cf_fp,
        "branch_states_identical": base_fp == cf_fp,
        "baseline_action": base_row["chosen"],
        "forced_action": forced_action,
        "action_changed": base_row["chosen"] != cf_row["chosen"],
        "legal_at_branch_point": forced_action in (cf_row["candidates"] or []),
    }
    if not verification["branch_states_identical"]:
        raise CounterfactualError(
            "the replay did not reach the same branch point "
            f"({base_fp} != {cf_fp}); refusing to report a counterfactual")
    if not verification["action_changed"]:
        raise CounterfactualError(
            f"forcing {forced_action!r} did not change the action at decision "
            f"{target_decision_id} (baseline also chose it)")

    outcome_delta = {}
    for key in OUTCOME_KEYS:
        left, right = baseline.get(key), forced_run.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            names = set(left) | set(right)
            outcome_delta[key] = {name: (right.get(name, 0) - left.get(name, 0))
                                  for name in sorted(names)
                                  if right.get(name, 0) != left.get(name, 0)}
        elif isinstance(left, (int, float)) and isinstance(right, (int, float)):
            outcome_delta[key] = right - left
        else:
            outcome_delta[key] = None

    return {
        "verification": verification,
        "baseline": {"result": baseline, "decision_rows": base_sink,
                     "timeline_rows": base_timeline},
        "counterfactual": {"result": forced_run, "decision_rows": cf_sink,
                           "timeline_rows": cf_timeline},
        "outcome_delta": outcome_delta,
    }
