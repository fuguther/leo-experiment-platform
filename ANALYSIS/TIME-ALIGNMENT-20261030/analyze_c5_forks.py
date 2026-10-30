#!/usr/bin/env python3
"""Summarise compact counterfactual fork results (C5: causal regret).

Input: one or more `cf.json` produced by
`CODE/scripts/counterfactual_one_decision.py`.  For every fork it reports:

  * the pairing proof (both branch fingerprints, action actually changed),
  * the tagged packet's fate and end-to-end delay under BOTH branches,
  * R_d = e2e(stale action) - e2e(fresh action)  (the user's Q(s,a_stale) -
    Q(s,a_fresh), on the tagged packet; positive means the stale action hurts),
  * the hop/queue/propagation decomposition of the tagged packet when the
    event trace allows it,
  * the cohort: how many OTHER packets changed fate or delivery time
    (network externality), with the distribution of their deltas.

Nothing here is a statistical claim: a fork is a deterministic paired run, so
its difference has no sampling noise — only coverage/representativeness is at
issue, and that is reported as counts, not as a p-value.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os


def _emitted_at(events):
    for row in events or ():
        if row.get("kind") == "packet_emitted":
            return row.get("at")
    return None


def _queue_and_prop(events):
    """Queue-wait and propagation time for the tagged packet from its events."""
    first_enter = None
    last = None
    prop = 0.0
    hops = 0
    for row in events or ():
        at = row.get("at")
        if at is None:
            continue
        if row.get("milestone") in ("propagation_start", "propagation_end"):
            if row.get("milestone") == "propagation_start":
                hops += 1
            continue
        if row.get("kind") == "queue_enter" and first_enter is None:
            first_enter = at
        last = row
    return {"first_queue_enter_s": first_enter,
            "propagation_hops_observed": hops,
            "last_event_s": None if last is None else last.get("at")}


def summarise(path):
    with open(path) as fh:
        doc = json.load(fh)
    verification = doc.get("verification") or {}
    tagged = doc.get("tagged_packet") or {}
    base = (tagged.get("baseline") or {})
    forced = (tagged.get("counterfactual") or {})
    base_del = base.get("delivery") or {}
    forced_del = forced.get("delivery") or {}
    base_events = base.get("events") or []
    forced_events = forced.get("events") or []
    base_emit = _emitted_at(base_events)
    forced_emit = _emitted_at(forced_events)
    base_e2e = (None if base_del.get("delivered_at") is None or base_emit is None
                else float(base_del["delivered_at"]) - float(base_emit))
    forced_e2e = (None if forced_del.get("delivered_at") is None
                  or forced_emit is None
                  else float(forced_del["delivered_at"]) - float(forced_emit))
    cohort = (doc.get("cohort") or {}).get("delivery_deltas") or []
    deltas = [c["delta_s"] for c in cohort if c.get("delta_s") is not None]
    deltas.sort()
    positive = sum(1 for d in deltas if d > 1e-12)
    negative = sum(1 for d in deltas if d < -1e-12)
    fate_changes = (doc.get("cohort") or {}).get("fate_changes") or {}
    return {
        "file": os.path.basename(path),
        "target_decision_id": (doc.get("source") or {}).get("target_decision_id"),
        "forced_action": (doc.get("source") or {}).get("forced_action"),
        "verification": {
            "branch_states_identical": verification.get(
                "branch_states_identical"),
            "baseline_action": verification.get("baseline_action"),
            "forced_action": verification.get("forced_action"),
            "action_changed": verification.get("action_changed"),
            "legal_at_branch_point": verification.get("legal_at_branch_point"),
        },
        "tagged_packet": {
            "pid": tagged.get("pid"),
            "baseline": {"fate": base.get("fate"),
                         "emitted_at": base_emit,
                         "delivered_at": base_del.get("delivered_at"),
                         "e2e_s": base_e2e,
                         "path": base_del.get("path"),
                         **_queue_and_prop(base_events)},
            "counterfactual": {"fate": forced.get("fate"),
                               "emitted_at": forced_emit,
                               "delivered_at": forced_del.get("delivered_at"),
                               "e2e_s": forced_e2e,
                               "path": forced_del.get("path"),
                               **_queue_and_prop(forced_events)},
        },
        "R_d_e2e_s": (None if base_e2e is None or forced_e2e is None
                      else base_e2e - forced_e2e),
        "R_d_status": ("computed" if base_e2e is not None
                       and forced_e2e is not None
                       else "tagged packet not delivered in one branch"),
        "cohort": {
            "n_common_delivered": len(deltas),
            "n_delta_positive": positive,
            "n_delta_negative": negative,
            "n_delta_zero": len(deltas) - positive - negative,
            "p50_delta_s": deltas[len(deltas) // 2] if deltas else None,
            "max_abs_delta_s": (max(abs(d) for d in deltas) if deltas else None),
            "fate_changes": len(fate_changes),
            "fate_change_detail": fate_changes,
        },
        "outcome_delta": doc.get("outcome_delta"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True,
                        help="directory holding cf.json files (glob)")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = [summarise(path)
            for path in sorted(glob.glob(os.path.join(args.dir, "*.json")))]
    computed = [r["R_d_e2e_s"] for r in rows if r["R_d_e2e_s"] is not None]
    payload = {
        "schema": "t1-c5-fork-summary/v1",
        "forks": len(rows),
        "R_d_computed": len(computed),
        "R_d_positive": sum(1 for v in computed if v > 1e-9),
        "R_d_negative": sum(1 for v in computed if v < -1e-9),
        "R_d_zero": sum(1 for v in computed if abs(v) <= 1e-9),
        "R_d_values_s": computed,
        "rows": rows,
    }
    with open(args.out, "w") as fh:
        json.dump(payload, fh, indent=1, ensure_ascii=False)
    print(json.dumps({k: payload[k] for k in
                      ("forks", "R_d_computed", "R_d_positive",
                       "R_d_negative", "R_d_zero", "R_d_values_s")},
                     indent=1))


if __name__ == "__main__":
    main()
