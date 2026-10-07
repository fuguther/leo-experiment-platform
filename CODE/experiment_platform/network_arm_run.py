"""Whole-run driver for the time-alignment NETWORK arms.

Why this is not time_alignment_compare
--------------------------------------
The compare driver scores one frozen branch offline and never lets an arm
change what the simulation does.  This driver runs the kernel ONCE per arm
with time_alignment.enabled=true and execution_mode=per_packet, so the arm
orders EVERY forwarding decision and each arm evolves its own queues.  The
primary quantity is the whole offered traffic's deadline loss.

Recording
---------
With --replay-out the run publishes ONE flat copy of the events needed to
rebuild it offline: decision rows (including each decision's actual
time-alignment audit), timeline rows, packet events, link service and
availability windows, topology trace, fates and deliveries.  queue_state
events are NOT duplicated -- they are exactly the timeline rows whose
milestone is queue_state, and a second copy is what made earlier artifacts
several times larger than necessary.  No second kernel call is made to
produce it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from CODE.experiment_platform import outcome_metrics, scripted_scenarios
from CODE.leo_sim import kernel
from CODE.leo_sim.time_alignment import ARMS

SCHEMA = "network-arm-run/v1"
REPLAY_SCHEMA = "network-arm-replay/v1"


def _write_atomic(path: Path, document) -> None:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise SystemExit(f"output parent must be an existing non-symlink "
                         f"directory: {parent}")
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                         suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
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


def _loss_of(row, deadline):
    """The builder's own per-packet deadline loss (min(delay,D)/D, or 1.0
    for a terminal failure).  Rows outside the population carry no loss."""
    if row.get("in_population") is False:
        return None
    block = row.get("deadline_loss")
    if isinstance(block, dict):
        value = block.get("value")
        return None if value is None else float(value)
    if isinstance(block, (int, float)) and not isinstance(block, bool):
        return float(block)
    return None


def _pid_of(row):
    """The packet identity of an outcome row, whatever the builder called it."""
    for key in ("pid", "packet_id"):
        if row.get(key) is not None:
            return int(row[key])
    return None


def _group_loss(rows, pids, deadline):
    picked = [r for r in rows if _pid_of(r) in pids]
    values = [v for v in (_loss_of(r, deadline) for r in picked)
              if v is not None]
    return {
        "packets": len(picked),
        "with_loss": len(values),
        "mean_loss": (sum(values) / len(values)) if values else None,
        "terminal_failures": sum(1 for r in picked
                                 if r.get("terminal_reason")),
    }


def _ta_coverage(decision_rows):
    """Where the arm actually ran, split by how many exits were executable.

    The 55% fallback figure from the round-5 summary is NOT a comparability
    verdict: with a single legal exit a fallback cannot change the choice.
    This breaks the denominator by legal-direction count and additionally
    reports whether the arm's applied order actually differed from the
    un-reordered candidate order, which is the only thing that can change an
    action.
    """
    by_legal = {}
    audits = 0
    for row in decision_rows:
        obs = row.get("observation_at_start") or {}
        audit = obs.get("time_alignment")
        n_legal = len(obs.get("legal_directions") or ())
        slot = by_legal.setdefault(str(n_legal), {
            "decisions": 0, "with_audit": 0, "fallback": 0,
            "missing": 0, "order_differs_from_unordered": 0,
            "chosen_differs_from_unordered": 0})
        slot["decisions"] += 1
        if not isinstance(audit, dict):
            continue
        audits += 1
        slot["with_audit"] += 1
        if audit.get("fallback_directions"):
            slot["fallback"] += 1
        if audit.get("missing_directions"):
            slot["missing"] += 1
        order = list(audit.get("applied_order") or ())
        four = obs.get("four_direction_audit") or {}
        unordered = (four.get("loop_free_candidates")\
                     or four.get("route_candidates") or ())
        if order and unordered:
            if order[0] != list(unordered)[0]:
                slot["order_differs_from_unordered"] += 1
            if row.get("chosen") is not None and row["chosen"] != list(unordered)[0]:
                slot["chosen_differs_from_unordered"] += 1
    return {
        "decisions_total": len(decision_rows),
        "decisions_with_time_alignment_audit": audits,
        "by_legal_direction_count": by_legal,
    }


def _replay_payload(result, decision_rows, timeline_rows):
    """One flat copy of the events needed to rebuild this run offline."""
    handover = result.get("handover") or {}
    control = result.get("control") or {}
    return {
        "schema": REPLAY_SCHEMA,
        "decision_rows": list(decision_rows),
        "timeline_rows": list(timeline_rows),
        "packet_events": list(result.get("packet_events") or ()),
        "link_service_windows": list(result.get("link_service_windows") or ()),
        "link_available_windows": list(
            result.get("link_available_windows") or ()),
        "topology_trace": list(result.get("topology_trace") or ()),
        "fates": dict(result.get("fates") or {}),
        "deliveries": dict(result.get("deliveries") or {}),
        "handover_events": list(handover.get("events") or ()),
        "control_totals": {
            "counters": dict((control.get("counters") or {})),
            "bits": dict(control.get("bits") or {}),
        },
        "recording_notes": {
            "queue_state_events": "not duplicated: select timeline_rows "
                                  "with milestone == queue_state",
            "no_second_kernel_call": True,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one time-alignment network arm over all traffic")
    parser.add_argument("--scenario", required=True,
                        choices=sorted(scripted_scenarios.SCENARIOS))
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--deadline-s", type=float, default=4.0)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replay-out", type=Path, required=True,
                        help="publish the required flat full-event record here")
    parser.add_argument("--preflight", action="store_true",
                        help="validate this invocation and input identity without running or writing")
    args = parser.parse_args(argv)

    if not math.isfinite(args.deadline_s) or args.deadline_s <= 0:
        parser.error("--deadline-s must be finite and positive")
    if args.out.resolve() == args.replay_out.resolve():
        parser.error("--out and --replay-out must be different files")
    for path in (args.out, args.replay_out):
        if path.exists() or path.is_symlink():
            parser.error(f"output already exists; preserve it and use a new path: {path}")
        if not path.parent.is_dir() or path.parent.is_symlink():
            parser.error(f"output parent must be an existing non-symlink directory: {path.parent}")

    resolved, rows, geometry, meta = scripted_scenarios.build(
        args.scenario, arm=args.arm)
    alignment = resolved["config"]["time_alignment"]
    if not alignment["enabled"] or alignment["arm"] != args.arm:
        parser.error("scenario must enable the requested time-alignment arm")
    if args.preflight:
        print(json.dumps({
            "status": "preflight_ok", "scenario": args.scenario,
            "arm": args.arm, "config_sha256": resolved["sha256"],
            "trace_rows": len(rows),
            "trace_sha256": hashlib.sha256(
                json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest(),
            "time_alignment": alignment, "kernel_calls": 0,
            "claimable": False,
        }, ensure_ascii=False))
        return 0
    sink, timeline = [], []
    result = kernel.run_simulation(resolved, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=timeline)
    execution = resolved["config"]["execution"]
    document = outcome_metrics.compare_outcome(
        result, timeline, sink, rows, deadline_s=float(args.deadline_s),
        cost={"service_s": execution["compute_delay_s"],
              "servers": execution["compute_servers_per_satellite"]},
        context={"mode": "network_arm", "scenario": args.scenario,
                 "arm": args.arm,
                 "config_sha256": resolved["sha256"]})

    outcome = document.get("network_outcome") or {}
    outcomes = outcome.get("packet_outcomes") or []
    if isinstance(outcomes, dict):
        outcomes = list(outcomes.values())
    probe_pids = {int(r["packet_id"]) for r in rows
                  if int(r["packet_id"]) >= scripted_scenarios.PROBE_FIRST_PID}
    all_ids = {_pid_of(r) for r in outcomes} - {None}
    probe_ids = probe_pids & all_ids
    background_ids = all_ids - probe_ids
    deadline = float(args.deadline_s)
    document["traffic_groups"] = {
        "probe": _group_loss(outcomes, probe_ids, deadline),
        "background": _group_loss(outcomes, background_ids, deadline),
    }
    document["time_alignment_coverage"] = _ta_coverage(sink)
    document["identity"] = {
        "scenario": args.scenario,
        "arm": args.arm,
        "config_sha256": resolved["sha256"],
        "trace_rows": len(rows),
        "trace_sha256": hashlib.sha256(
            json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest(),
        "time_alignment": resolved["config"]["time_alignment"],
    }
    document["schema"] = SCHEMA
    document["primary_metric"] = {
        "name": "deadline_primary_loss over ALL offered packets",
        "value": (outcome.get("deadline_primary_loss") or {}).get("value"),
        "deadline_s": deadline,
        "status": (outcome.get("deadline_primary_loss") or {}).get("status"),
        "counts": outcome.get("counts"),
        "censoring": outcome.get("censoring"),
        "delivery_ratio": outcome.get("delivery_ratio"),
        "throughput": outcome.get("throughput"),
        "e2e": outcome.get("e2e"),
    }
    if args.replay_out is not None:
        payload = _replay_payload(result, sink, timeline)
        _write_atomic(args.replay_out, payload)
        document["replay"] = {
            "published": True,
            "path": str(args.replay_out),
            "schema": REPLAY_SCHEMA,
            "decision_rows": len(payload["decision_rows"]),
            "timeline_rows": len(payload["timeline_rows"]),
            "packet_events": len(payload["packet_events"]),
            "link_service_windows": len(payload["link_service_windows"]),
        }
    else:
        document["replay"] = {"published": False}
    _write_atomic(args.out, document)
    print(json.dumps({
        "status": "ran", "out": str(args.out), "arm": args.arm,
        "offered": (outcome.get("counts") or {}).get("offered"),
        "delivered": (outcome.get("counts") or {}).get("delivered"),
        "deadline_mean_loss":
            (outcome.get("deadline_primary_loss") or {}).get("value"),
        "probe_mean_loss": document["traffic_groups"]["probe"]["mean_loss"],
        "background_mean_loss":
            document["traffic_groups"]["background"]["mean_loss"],
        "replay": document["replay"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
