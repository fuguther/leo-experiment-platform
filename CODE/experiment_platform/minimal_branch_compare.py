"""Per-packet minimal mechanism comparison of one branch point.

Why this entry point exists
---------------------------
T1-FROZEN-BRANCH.  The platform could already freeze an observation
(execution.decision_observation_mode=frozen) and could already force one
action (forced_actions + counterfactual.py), but the two could not be
combined: the kernel refused the pair outright.  With that restriction lifted,
the two runs of a pair differ by exactly one action, taken from the legal set
the frozen observation recorded.

This driver turns such a pair into the three per-packet quantities T1 needs:

  (a) candidate actual arrival / contended-resource instants;
  (b) work ahead of the target packet, measured at TWO instants and on a NAMED
      resource, excluding the target packet itself;
  (c) realized action cost: the per-packet difference between the branches.

Two scenario sources
--------------------
--config <profile>   a real Walker constellation (the no_info DIAGNOSIS case:
                     one candidate is viable and the other dead-ends, so the
                     contrast is feasibility, not cost).
--scenario <name>    a deterministic scripted topology from
                     experiment_platform.scripted_scenarios, where BOTH
                     candidates are viable and the contention is hand-checkable.

What the pairing claim does and does not cover
----------------------------------------------
The two runs are compared through the DECISION LOG: the branch fingerprint
covers every decision row committed strictly before the target plus the
target's own pre-commit fields, and this driver additionally compares the
timeline rows strictly before the branch instant.  It does NOT hash packet,
link, queue or RNG state, so the supported claim is "the two runs reached the
same branch point as witnessed by the decision log", not "the two simulations
were in bit-identical states".  The artifact carries that wording in
pairing.claim and pairing.coverage rather than leaving it to the reader.

What this does NOT establish
----------------------------
* The branch instant is the observation (t_decision_start); the action is
  committed at t_decision_commit, exactly as in the unforced path.
* Each branch is a separate replay from t=0 that is verified to reach the same
  branch point, not a copy-on-write fork of one simulation.
* One pair is one contrast.  No statistical statement is made or implied.

Usage
-----
    python3 -m CODE.experiment_platform.minimal_branch_compare \
        --scenario contention --decision-id 5 --forced-action W \
        --out out/branch-contention.json

    python3 -m CODE.experiment_platform.minimal_branch_compare \
        --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
        --decision-id 2 --forced-action S --out out/branch-noinfo.json
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from CODE.leo_sim import config as config_mod
from CODE.leo_sim import counterfactual, decision_ledger, kernel, receipt
from CODE.leo_sim import trace as trace_mod
from CODE.experiment_platform import scripted_scenarios

SCHEMA = "minimal-branch-compare/v2"

#: Milestones that exist ONLY because the comparison injected something.  They
#: are real rows and are kept in the artifact, but they are not physical
#: behaviour, so the FIRST PHYSICAL divergence must skip them -- otherwise the
#: answer is always "the forced_action marker", which says nothing about the
#: mechanism.
AUDIT_ONLY_MILESTONES = frozenset({"forced_action"})

PAIRING_CLAIM = (
    "the two runs reached the same branch point as witnessed by the decision "
    "log and the timeline head")
PAIRING_COVERAGE = (
    "every decision row committed strictly before the target, the target's own "
    "pre-commit fields (chosen excluded), and every timeline row with "
    "at < t_branch; NOT packet, link, queue or RNG state")


class CompareDriverError(RuntimeError):
    """The comparison could not be driven; nothing was published."""


def _check_new_destination(target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise CompareDriverError(f"output destination exists: {target}")
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise CompareDriverError(
            f"output parent must be an existing non-symlink directory: {parent}")


def _canonical(row) -> str:
    return json.dumps(row, sort_keys=True, default=str)


def _rows_before(timeline_rows, t_branch: float):
    return [r for r in timeline_rows if float(r["at"]) < t_branch]


def _first_divergence(left, right, t_branch: float, exclude=frozenset()):
    """First differing row at/after the branch instant.

    Rows are compared canonically so key order or float formatting cannot
    masquerade as a mechanism difference.  Rows whose milestone is in the
    exclude set are skipped: an audit-only marker is not physical behaviour.
    """
    def tail(rows):
        return [r for r in rows
                if float(r["at"]) >= t_branch
                and r.get("milestone") not in exclude]

    tail_left, tail_right = tail(left), tail(right)
    for index, (a, b) in enumerate(zip(tail_left, tail_right)):
        if _canonical(a) != _canonical(b):
            return {"index": index, "baseline": a, "counterfactual": b}
    if len(tail_left) != len(tail_right):
        shorter = min(len(tail_left), len(tail_right))
        if len(tail_left) > len(tail_right):
            return {"index": shorter, "only_in": "baseline",
                    "row": tail_left[shorter]}
        return {"index": shorter, "only_in": "counterfactual",
                "row": tail_right[shorter]}
    return None


def _total_ahead(queued, in_service):
    if queued is None:
        return None
    if in_service is None:
        return int(queued)
    return int(queued) + float(in_service)


def _arrival_workload(arrival, resource):
    """Work ahead of the packet on a NAMED resource, at the ARRIVAL instant.

    The arrival carries an egress snapshot of the whole peer, keyed by the
    peer's own direction names.  The snapshot is turned into a named resource
    (isl:sat:peer) so it can be compared with the pre-enqueue measurement of
    the same resource instead of being trusted as if it were that measurement.
    """
    if not arrival or not resource:
        return None
    snapshot = arrival.get("egress_snapshot")
    if not snapshot:
        return None
    sat = arrival.get("sat")
    for direction, slot in sorted(snapshot.items()):
        if f"isl:{sat}:{slot['peer']}" != resource:
            continue
        queued = int(slot["data_bits"]) + int(slot["ctrl_bits"])
        in_service = slot["in_service_remaining_bits"]
        return {
            "resource": resource,
            "resource_kind": "isl_egress",
            "direction_at_peer": direction,
            "measured_at": float(arrival["at"]),
            "source": "peer_arrival egress_snapshot (taken at arrival, before "
                      "the peer re-decides)",
            "queued_bits_ahead": queued,
            "in_service_remaining_bits": in_service,
            "in_service_remaining_method": slot["in_service_remaining_method"],
            "total_bits_ahead": _total_ahead(queued, in_service),
            "excludes": "the arriving packet is not yet in this queue",
        }
    return None


def _pre_enqueue_workload(timeline_rows, pid, arrival_sat, t_branch):
    """Work ahead on the PEER's egress, measured immediately before the target
    packet is inserted into it.

    Identified by RESOURCE rather than by direction: an enqueue into an
    isl:<arrival_sat>:* link can only be the target packet entering that peer's
    egress, because the branch's own enqueue left the SENDER.
    """
    if arrival_sat is None:
        return None
    prefix = f"isl:{arrival_sat}:"
    for row in timeline_rows:
        if row.get("milestone") != "queue_enter" or row.get("pid") != pid:
            continue
        if float(row["at"]) < t_branch:
            continue
        link_id = row.get("link_id") or ""
        if not link_id.startswith(prefix):
            continue
        backlog = row.get("backlog_before")
        if backlog is None:
            return {"resource": link_id, "measured_at": float(row["at"]),
                    "source": "queue_enter", "backlog_before": None,
                    "reason": "producer recorded no pre-insert backlog"}
        queued = backlog["queued_bits_before"]
        in_service = backlog.get("in_service_remaining_bits_before")
        return {
            "resource": backlog["resource"],
            "resource_kind": backlog["resource_kind"],
            "measured_at": float(row["at"]),
            "source": "queue_enter backlog_before (measured before insertion)",
            "queued_bits_ahead": int(queued),
            "in_service_remaining_bits": in_service,
            "in_service_remaining_method":
                backlog["in_service_remaining_method"],
            "total_bits_ahead": _total_ahead(queued, in_service),
            "excludes": backlog["excludes"],
            "decision_id": row.get("decision_id"),
        }
    return None


def _egress_workload(snapshot, direction):
    """Direction-keyed view of the peer snapshot, kept so a reader of the
    previous artifact version does not silently lose a field."""
    if not snapshot or direction is None:
        return None
    slot = snapshot.get(direction)
    if slot is None:
        return None
    queued = slot["data_bits"] + slot["ctrl_bits"]
    return {
        "peer": slot["peer"],
        "data_bits": slot["data_bits"],
        "ctrl_bits": slot["ctrl_bits"],
        "ctrl_packets": slot["ctrl_packets"],
        "queued_bits_ahead": queued,
        "in_service_remaining_bits": slot["in_service_remaining_bits"],
        "in_service_remaining_method": slot["in_service_remaining_method"],
        "in_service_phase": slot["in_service_phase"],
    }


def _attempts_of(decision_rows, timeline_rows, pid):
    attempts, _diag = decision_ledger.build_attempts(decision_rows, timeline_rows)
    return [a for a in attempts if a["pid"] == pid]


def _contention_timeline(timeline_rows, pid, t_after):
    enters = [r for r in timeline_rows
              if r.get("milestone") == "queue_enter" and r.get("pid") == pid
              and float(r["at"]) >= t_after]
    isl = [r for r in enters if r.get("queue") == "isl"]
    holding = [r for r in enters if r.get("queue") != "isl"]
    first = None
    if isl:
        first = {"at": isl[0]["at"], "link_id": isl[0].get("link_id"),
                 "queue": isl[0].get("queue"),
                 "decision_id": isl[0].get("decision_id")}
    return {
        "first_isl_egress_queue_enter": first,
        "first_other_queue_enter": (
            None if not holding
            else {"at": holding[0]["at"], "queue": holding[0].get("queue"),
                  "decision_id": holding[0].get("decision_id")}),
        "isl_egress_queue_enters": len(isl),
        "other_queue_enters": len(holding),
        "source": "timeline queue_enter milestones for this pid at/after the "
                  "branch instant",
    }


def _branch_side(decision_id, pid, ledger, decision_rows, timeline_rows,
                 result, t_after):
    entry = ledger.get(decision_id, {})
    row = next((r for r in decision_rows
                if r.get("decision_id") == decision_id), None)
    delivery = result["deliveries"].get(pid)
    arrival = next((m for m in timeline_rows
                    if m.get("milestone") == "peer_arrival"
                    and m.get("decision_id") == decision_id), None)
    arrival_sat = (arrival or {}).get("sat")
    truth_target = entry.get("truth_at_target")
    # The contended egress is the PEER's choice, in the peer's own direction
    # frame.  The sender's chosen direction is a DIFFERENT frame and must never
    # index the peer's snapshot.
    contended = None
    if isinstance(truth_target, dict):
        contended = truth_target.get("contended_direction")
    pre_enqueue = _pre_enqueue_workload(timeline_rows, pid, arrival_sat,
                                        t_after)
    resource = (pre_enqueue or {}).get("resource")
    arrival_workload = _arrival_workload(arrival, resource)
    changed = None
    if arrival_workload and pre_enqueue:
        changed = (arrival_workload["total_bits_ahead"]
                   != pre_enqueue["total_bits_ahead"])
    return {
        "chosen": (row or {}).get("chosen"),
        "obs_mode": entry.get("obs_mode"),
        "t_measure": entry.get("t_measure"),
        "t_decision_start": entry.get("t_decision_start"),
        "t_decision_commit": entry.get("t_decision_commit"),
        "own_queue_bits_at_branch": (row or {}).get("own_queue_bits"),
        "t_local_queue_enter": entry.get("t_local_queue_enter"),
        "t_service_start": entry.get("t_service_start"),
        "t_service_finish": entry.get("t_service_finish"),
        "t_peer_arrival": entry.get("t_peer_arrival"),
        "t_peer_arrival_sat": arrival_sat,
        "t_peer_redecision": entry.get("t_peer_redecision"),
        "t_peer_target_egress_enter": entry.get("t_peer_target_egress_enter"),
        "t_peer_target_egress_service_start":
            entry.get("t_peer_target_egress_service_start"),
        "truth_at_target": truth_target,
        "workload_ahead_at_contended_egress":
            _egress_workload((arrival or {}).get("egress_snapshot"), contended),
        "workload": {
            "at_peer_arrival": arrival_workload,
            "before_target_egress_enqueue": pre_enqueue,
            "same_resource": (None if not (arrival_workload and pre_enqueue)
                              else arrival_workload["resource"]
                              == pre_enqueue["resource"]),
            "changed_between_the_two_instants": changed,
            "authoritative": "before_target_egress_enqueue",
            "why": ("the arrival snapshot is taken before the peer re-decides; "
                    "the enqueue measurement is taken on the resource the "
                    "packet actually joined, so when they disagree the arrival "
                    "figure is stale and must not be reported as the work the "
                    "packet found"),
        },
        "contention_timeline": _contention_timeline(timeline_rows, pid, t_after),
        "fate": result["fates"].get(pid),
        "delivered_at": (delivery or {}).get("delivered_at"),
        "path": (delivery or {}).get("path"),
        "attempts": len(_attempts_of(decision_rows, timeline_rows, pid)),
    }


def _add_egress_wait(side, resolved, rate_model):
    """Put the measured egress wait next to the measured work ahead.

    The wait is what actually happened; the pre-enqueue figure is what was
    there when the packet looked.  They are NOT the same number in general: a
    resource that keeps receiving traffic (the control plane advertises on a
    timer) can grow after the measurement, so the work-ahead figure is a LOWER
    BOUND on the wait and the residual is reported rather than hidden.  With a
    constant PHY rate the conversion is exact, so no residual is attributed
    when the model cannot support it (MCS) -- no number is produced rather
    than a wrong one.
    """
    workload = side.get("workload") or {}
    enqueue = workload.get("before_target_egress_enqueue")
    enter = side.get("t_peer_target_egress_enter")
    start = side.get("t_peer_target_egress_service_start")
    side["egress_wait_s"] = None
    side["egress_wait_minus_measured_work_s"] = None
    side["egress_wait_basis"] = None
    if (enqueue is None or enter in (None, "missing")
            or start in (None, "missing")):
        return
    wait = float(start) - float(enter)
    side["egress_wait_s"] = wait
    if rate_model != "constant" or enqueue.get("total_bits_ahead") is None:
        side["egress_wait_basis"] = (
            "wait reported, work-ahead conversion unavailable under "
            f"rate_model={rate_model}")
        return
    rate_bps = float(resolved["config"]["links"]["isl_rate_mbps"]) * 1e6
    lower_bound = float(enqueue["total_bits_ahead"]) / rate_bps
    side["egress_wait_minus_measured_work_s"] = wait - lower_bound
    side["egress_wait_basis"] = (
        "wait = work ahead at enqueue / isl rate + work that joined the "
        "resource after the measurement")


def _core(resolved, rows, geometry, decision_id, forced_action, source,
          declared=None):
    try:
        result = counterfactual.replay_with_forced_action(
            resolved, rows, geometry=geometry,
            target_decision_id=decision_id, forced_action=forced_action)
    except counterfactual.CounterfactualError as exc:
        raise CompareDriverError(f"replay refused: {exc}") from exc
    except kernel.KernelError as exc:
        raise CompareDriverError(f"engine refused the branch: {exc}") from exc

    base, alt = result["baseline"], result["counterfactual"]
    base_rows, base_tl = base["decision_rows"], base["timeline_rows"]
    alt_rows, alt_tl = alt["decision_rows"], alt["timeline_rows"]
    base_ledger, _d1 = decision_ledger.build_ledger(base_rows, base_tl)
    alt_ledger, _d2 = decision_ledger.build_ledger(alt_rows, alt_tl)

    target = next((r for r in base_rows if r["decision_id"] == decision_id),
                  None)
    if target is None:
        raise CompareDriverError(
            f"decision {decision_id} never committed in the baseline run")
    pid = target["pid"]
    t_branch = float(target["t_decision_start"])

    pre_left = _rows_before(base_tl, t_branch)
    pre_right = _rows_before(alt_tl, t_branch)
    pre_identical = ([_canonical(r) for r in pre_left]
                     == [_canonical(r) for r in pre_right])
    verification = result["verification"]

    side_base = _branch_side(decision_id, pid, base_ledger, base_rows, base_tl,
                             base["result"], t_branch)
    side_alt = _branch_side(decision_id, pid, alt_ledger, alt_rows, alt_tl,
                            alt["result"], t_branch)
    rate_model = resolved["config"]["links"]["rate_model"]
    for side in (side_base, side_alt):
        _add_egress_wait(side, resolved, rate_model)

    candidates = sorted(target["candidates"] or [])
    taken = {"baseline": side_base["chosen"],
             "counterfactual": side_alt["chosen"]}
    truth = ((target.get("info_audit") or {}).get("candidate_truth") or {})
    per_candidate = {}
    for direction in candidates:
        realised = {}
        for name, side in (("baseline", side_base),
                           ("counterfactual", side_alt)):
            if taken[name] != direction:
                realised[name] = {"taken": False}
                continue
            target_truth = side["truth_at_target"]
            contended = (target_truth.get("contended_direction")
                         if isinstance(target_truth, dict) else None)
            realised[name] = {
                "taken": True,
                "t_peer_arrival": side["t_peer_arrival"],
                "t_peer_arrival_sat": side["t_peer_arrival_sat"],
                "t_peer_target_egress_enter":
                    side["t_peer_target_egress_enter"],
                "t_peer_target_egress_service_start":
                    side["t_peer_target_egress_service_start"],
                "contended_egress_at_peer": contended,
                "contended_egress_resolution":
                    (target_truth.get("reason")
                     if isinstance(target_truth, dict)
                     and target_truth.get("status") == "missing" else "ok"),
                "workload": side["workload"],
                "contention_timeline": side["contention_timeline"],
                "fate": side["fate"],
                "delivered_at": side["delivered_at"],
            }
        downstream = (truth.get(direction) or {}).get("downstream") or {}
        per_candidate[direction] = {
            "direction": direction,
            "workload_ahead_local_bits_at_branch":
                (side_base["own_queue_bits_at_branch"] or {}).get(direction),
            "predicted_peer_egress_at_commit":
                downstream.get("peer_egress_direction"),
            "taken_in": sorted(n for n, c in taken.items() if c == direction),
            "realized": realised,
        }

    cost_delta = (
        None if side_base["delivered_at"] is None
        or side_alt["delivered_at"] is None
        else side_alt["delivered_at"] - side_base["delivered_at"])
    cost = {
        "fate": {"baseline": side_base["fate"],
                 "counterfactual": side_alt["fate"],
                 "changed": side_base["fate"] != side_alt["fate"]},
        "delivered_at": {"baseline": side_base["delivered_at"],
                         "counterfactual": side_alt["delivered_at"]},
        "delivered_at_delta_s": cost_delta,
        "path": {"baseline": side_base["path"],
                 "counterfactual": side_alt["path"]},
        "hops_delta": (None if side_base["path"] is None
                       or side_alt["path"] is None
                       else len(side_alt["path"]) - len(side_base["path"])),
        "decision_attempts": {"baseline": side_base["attempts"],
                              "counterfactual": side_alt["attempts"]},
        "egress_wait": {
            "baseline": side_base["egress_wait_s"],
            "counterfactual": side_alt["egress_wait_s"],
        },
        "delivered_at_delta_equals_minus_baseline_wait": (
            None if cost_delta is None or side_base["egress_wait_s"] is None
            else bool(abs(cost_delta + side_base["egress_wait_s"]) <= 1e-9)),
        "run_level": result["outcome_delta"],
    }

    return {
        "schema": SCHEMA,
        "source": dict(source, code_sha256=receipt.code_sha256(),
                       config_sha256=resolved["sha256"],
                       obs_mode=resolved["config"]["execution"][
                           "decision_observation_mode"],
                       compute_delay_s=resolved["config"]["execution"][
                           "compute_delay_s"],
                       node_process_delay_s=resolved["config"]["execution"][
                           "node_process_delay_s"],
                       policy=resolved["config"]["routing"]["policy"],
                       learning_algorithm=resolved["config"]["learning"][
                           "algorithm"],
                       target_decision_id=decision_id, target_pid=pid,
                       forced_action=forced_action),
        "declared": declared,
        "pairing": dict(verification, claim=PAIRING_CLAIM,
                        coverage=PAIRING_COVERAGE),
        "pre_branch_identity": {
            "decision_rows_identical_before_branch":
                verification["branch_states_identical"],
            "timeline_rows_before_branch": {
                "baseline": len(pre_left), "counterfactual": len(pre_right)},
            "timeline_identical_before_branch": pre_identical,
            "t_branch_instant": t_branch,
            "t_branch_basis": "observation (frozen)",
            "claim": PAIRING_CLAIM,
            "coverage": PAIRING_COVERAGE,
        },
        "post_branch_divergence": {
            "first_physical_divergent_row": _first_divergence(
                base_tl, alt_tl, t_branch, exclude=AUDIT_ONLY_MILESTONES),
            "first_divergent_row_including_audit_markers": _first_divergence(
                base_tl, alt_tl, t_branch),
            "audit_only_milestones_excluded": sorted(AUDIT_ONLY_MILESTONES),
            "why_excluded": (
                "a forced_action row exists only because the comparison "
                "injected an action; reporting it as the first behavioural "
                "difference would answer a question about the harness rather "
                "than about the mechanism"),
            "timeline_rows_after_branch": {
                "baseline": len(base_tl) - len(pre_left),
                "counterfactual": len(alt_tl) - len(pre_right)},
            "separation_expected": True,
        },
        "branch": {"baseline": side_base, "counterfactual": side_alt},
        "candidates": per_candidate,
        "action_cost": cost,
        "limits": [
            "one pair is one contrast; no statistical statement is made",
            "the pairing claim is decision-log-witnessed, not a bit-identical "
            "state claim; see pairing.coverage",
            "each branch is a separate replay from t=0, verified to reach the "
            "same branch point, not a copy-on-write fork of one simulation",
            "the branch instant is the observation (t_decision_start); the "
            "chosen action is committed at t_decision_commit",
            "only ISL forward candidates are forcible; a deliver or hold "
            "branch point fails loud rather than being silently ignored",
            "in_service_remaining_bits is null under rate_model=mcs (varying "
            "rate) and for downlink egresses (the ISL-rate extrapolation would "
            "be wrong there), so total_bits_ahead can be queued-bits only",
        ],
    }


def compare_config(config_path: Path, decision_id: int, forced_action: str,
                   root: Path) -> dict[str, Any]:
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise CompareDriverError(f"config invalid: {exc}") from exc
    algorithm = resolved["config"]["learning"]["algorithm"]
    mode = resolved["config"]["execution"]["decision_observation_mode"]
    if algorithm != "none":
        raise CompareDriverError(
            "the comparison requires a deterministic router; "
            f"learning.algorithm={algorithm!r}")
    if mode != "frozen":
        raise CompareDriverError(
            "the minimal branch comparison is defined for "
            "execution.decision_observation_mode=frozen; this config is "
            f"{mode!r}")

    work = Path(tempfile.mkdtemp(prefix="branch-compare-", dir=str(root)))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=resolved["config"]["execution"]["max_packets"])
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
              "trace_sha256": (manifest.get("__trace_sha256")
                               or manifest.get("trace_sha256"))}
    return _core(resolved, rows, None, decision_id, forced_action, source)


def compare_scenario(name: str, decision_id: int, forced_action: str):
    try:
        resolved, rows, geometry, meta = scripted_scenarios.build(name)
    except KeyError as exc:
        raise CompareDriverError(str(exc)) from exc
    source = {"scenario": name, "config": None, "trace_sha256": None,
              "scripted_topology": meta["topology"],
              "scripted_cells": meta["cells"]}
    return _core(resolved, rows, geometry, decision_id, forced_action, source,
                 declared=meta["declared"])


def publish(document: dict[str, Any], out: Path) -> None:
    _check_new_destination(out)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Per-packet minimal mechanism comparison of one frozen "
                    "branch point")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scenario",
                        choices=sorted(scripted_scenarios.SCENARIOS))
    parser.add_argument("--decision-id", type=int, required=True)
    parser.add_argument("--forced-action", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.scenario):
        print("COMPARE REFUSED: give exactly one of --config or --scenario")
        return 2
    try:
        if args.config:
            document = compare_config(args.config, args.decision_id,
                                      args.forced_action, args.root)
        else:
            document = compare_scenario(args.scenario, args.decision_id,
                                        args.forced_action)
        publish(document, args.out)
    except CompareDriverError as exc:
        print(f"COMPARE REFUSED: {exc}")
        return 2
    cost = document["action_cost"]
    base_side = document["branch"]["baseline"]
    alt_side = document["branch"]["counterfactual"]
    print(json.dumps({
        "status": "compared",
        "out": str(args.out),
        "scenario": document["source"]["scenario"],
        "pre_branch_identical":
            document["pre_branch_identity"]["timeline_identical_before_branch"],
        "decision_rows_identical":
            document["pairing"]["branch_states_identical"],
        "baseline_action": document["pairing"]["baseline_action"],
        "forced_action": document["pairing"]["forced_action"],
        "first_physical_divergence":
            document["post_branch_divergence"][
                "first_physical_divergent_row"] is not None,
        "target_pid": document["source"]["target_pid"],
        "baseline_delivered_at": cost["delivered_at"]["baseline"],
        "counterfactual_delivered_at": cost["delivered_at"]["counterfactual"],
        "delivered_at_delta_s": cost["delivered_at_delta_s"],
        "baseline_egress_workload_ahead":
            (base_side["workload"]["before_target_egress_enqueue"] or {})
            .get("total_bits_ahead"),
        "counterfactual_egress_workload_ahead":
            (alt_side["workload"]["before_target_egress_enqueue"] or {})
            .get("total_bits_ahead"),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
