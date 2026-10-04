"""Whole-run driver for the time-alignment NETWORK arms.

Why this is not time_alignment_compare
--------------------------------------
The compare driver scores one frozen branch offline and never lets an arm
change what the simulation does.  This driver runs the kernel ONCE per arm
with time_alignment.enabled=true and execution_mode=per_packet, so the arm
orders EVERY forwarding decision and each arm evolves its own queues.  The
primary quantity is therefore the whole offered traffic's deadline loss, not
a per-decision regret.

Everything an arm sees (arrival table, information permissions, predictor,
history limit, common rule, compute/query budget) is identical across arms;
only time_alignment.arm differs, and the re-resolved config hash records it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from CODE.experiment_platform import outcome_metrics, scripted_scenarios
from CODE.leo_sim import kernel

SCHEMA = "network-arm-run/v1"
ARMS = ("stale", "now", "common")


def _publish(document, out: Path) -> None:
    parent = out.parent
    if not parent.is_dir() or parent.is_symlink():
        raise SystemExit(f"output parent must be an existing non-symlink "
                         f"directory: {parent}")
    handle, temporary = tempfile.mkstemp(prefix="." + out.name + ".",
                                         suffix=".tmp", dir=str(parent))
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
    # The kernel records the arm's audit INSIDE the frozen observation
    # (kernel.py: "time_alignment": ta_audit in _observation_at_start),
    # not at the top level of the decision row.
    audits = [(r.get("observation_at_start") or {}).get("time_alignment")
              for r in decision_rows]
    present = [a for a in audits if isinstance(a, dict)]
    legal_sizes = {}
    for row in decision_rows:
        obs = row.get("observation_at_start") or {}
        n = len(obs.get("legal_directions") or ())
        legal_sizes[n] = legal_sizes.get(n, 0) + 1
    return {
        "decisions_total": len(decision_rows),
        "decisions_with_time_alignment_audit": len(present),
        "decisions_without_audit": len(decision_rows) - len(present),
        "decisions_with_fallback_directions": sum(
            1 for a in present if a.get("fallback_directions")),
        "decisions_with_missing_directions": sum(
            1 for a in present if a.get("missing_directions")),
        "legal_direction_count_histogram":
            {str(k): v for k, v in sorted(legal_sizes.items())},
        "audit_keys_sample": sorted(present[0].keys()) if present else [],
        "execution_mode_sample": (present[0].get("execution_mode")
                                   if present else None),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one time-alignment network arm over all traffic")
    parser.add_argument("--scenario", required=True,
                        choices=sorted(scripted_scenarios.SCENARIOS))
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--deadline-s", type=float, default=4.0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    resolved, rows, geometry, meta = scripted_scenarios.build(
        args.scenario, arm=args.arm)
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
    _publish(document, args.out)
    print(json.dumps({
        "status": "ran", "out": str(args.out), "arm": args.arm,
        "offered": (outcome.get("counts") or {}).get("offered"),
        "delivered": (outcome.get("counts") or {}).get("delivered"),
        "deadline_mean_loss":
            (outcome.get("deadline_primary_loss") or {}).get("value"),
        "probe_mean_loss": document["traffic_groups"]["probe"]["mean_loss"],
        "background_mean_loss":
            document["traffic_groups"]["background"]["mean_loss"],
        "ta_audits": document["time_alignment_coverage"]
                      ["decisions_with_time_alignment_audit"],
        "decisions": document["time_alignment_coverage"]["decisions_total"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())