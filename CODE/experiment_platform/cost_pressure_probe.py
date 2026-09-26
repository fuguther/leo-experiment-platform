"""One-at-a-time cost and pressure probe for the small frozen scenario.

What this is for
----------------
T1-COST-PRESSURE.  A mechanism comparison is only interpretable if the cost
levers can be moved INDEPENDENTLY: if raising the offered load also changed the
service rate, or if a node's processing cost leaked into the PHY bandwidth
term, then a measured difference could not be attributed to either.

This driver runs a baseline plus one arm per lever, changing exactly one
config leaf per arm, and reports for every arm the quantities that lever is
supposed to move.  It is a SCREENING probe on a small fixture, not an
experiment: one seed, no replication, no statistical statement.  Its job is to
show that the levers are separable and that each one lands on its own recorded
quantity -- which is what a later, replicated design needs to be built on.

Relation to the pre-existing E2-F design
----------------------------------------
A three-factor one-at-a-time design already exists and is more thorough than
this probe: ANALYSIS/ARRIVAL-TIME-T1-20260923/02b-three-factor-one-at-a-time.md
(8 arms over demand.offered_mbps / execution.node_process_delay_s /
links.isl_rate_mbps on the t1_pressure_corridor_v2 scenario, 0 unexpected leaf
changes).  That design is NOT reproduced here and must not be replaced by this
one.  Two things this probe adds, and the reasons they are separate:

  * it runs from a profile that is IN the repository, so it does not depend on
    the untracked E1/E2 profiles and scripts that the E2-F record itself
    documents as unreproducible from a commit alone; and
  * it exercises a lever the E2-F design did not have: a BOUND on concurrent
    computation per satellite.  E2-F measured node processing and link
    capacity, and recorded compute cost only as the analytic quantity rho;
    there was no platform model of a compute queue at all.  The last section
    below is therefore new capability, not a re-measurement.

The levers (all of them existing, EXCEPT the compute bound which this round
added)
----------------------------------------------------------------------------
  input traffic          demand.offered_mbps
  physical capacity      links.isl_rate_mbps
  node processing cost   execution.node_process_delay_s        (F2, disjoint
                         from the computation interval by construction)
  compute resource bound execution.compute_servers_per_satellite (T1-COMPUTE-
                         QUEUE: 0 = unbounded/historical, N = N servers)
  packet length          demand.packet_bits
  target egress pressure reached through the offered load on a single OD: the
                         destination downlink is the contended egress, and it
                         is reported through occupied.gsl_downlink_s and the
                         access counters rather than through a new field.

Where each reported number comes from
-------------------------------------
  fates / totals / queue_area_bits_s / occupied / access / events_processed
      -> kernel.run() result
  decisions, non-committed attempts
      -> decision_ledger.build_ledger / build_attempts
  compute wait (total, max, how many decisions actually queued)
      -> timeline milestones 'compute_wait' (T1-COMPUTE-QUEUE)
  F2 node processing occupancy
      -> timeline milestones 'node_process_start' / 'node_process_end'
  delivery latency
      -> result['deliveries'][pid]['delivered_at'] minus the packet's emission

Usage
-----
    python3 -m CODE.experiment_platform.cost_pressure_probe \
        --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
        --out out/cost-pressure.json
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from CODE.leo_sim import config as config_mod
from CODE.leo_sim import decision_ledger, kernel, receipt
from CODE.leo_sim import trace as trace_mod

SCHEMA = "cost-pressure-probe/v1"

#: Two blocks, because they answer two different questions and the second one
#: only exists once the first has shown the levers are separable.
#:
#: Block A is one-at-a-time around the fixture's own operating point.  Every arm
#: is that same baseline with EXACTLY one leaf replaced, so a difference is
#: attributable to that leaf and nothing else.  Two arms in this block are
#: NEGATIVE CONTROLS and are labelled as such: at this load the compute pool
#: never has two decisions in flight at once, and the fixture's node processing
#: cost is already non-zero, so neither should move anything.  An arm that
#: moves nothing on purpose is evidence, not a missing result.
#:
#: Block B asks the one question block A structurally cannot: a bound on
#: concurrent computation only binds under CONTENTION, so the compute lever is
#: measured against its own higher-load baseline.  Within each pair exactly one
#: leaf differs; across the pair the load also differs, and that is stated
#: rather than hidden.
BLOCKS = (
    {
        "id": "A_lever_separation",
        "question": "does each single-leaf change land on its own quantity, "
                    "and do the controls stay put?",
        "arms": (
            # name, path, op ("set" | "scale"), value, expected report keys
            ("baseline", None, None, None, []),
            # NOTE: this fixture records no access counters (result['access']
            # is empty), so "target egress pressure" is read through
            # occupied.gsl_downlink_s and the delivered-packet count instead of
            # through a dedicated field.  Claiming an access key here would be
            # claiming a measurement the run does not take.
            ("traffic_x4", ("demand", "offered_mbps"), "scale", 4.0,
             ["packets_offered", "packets_delivered", "occupied_s"]),
            ("capacity_div4", ("links", "isl_rate_mbps"), "scale", 0.25,
             ["occupied_s", "queue_area_bits_s"]),
            ("node_process_zero", ("execution", "node_process_delay_s"), "set",
             0.0, ["node_process_occupied_s"]),
            ("packet_bits_div8", ("demand", "packet_bits"), "scale", 0.125,
             ["packets_offered", "decisions_committed"]),
            ("compute_servers_1_control", ("execution",
                                           "compute_servers_per_satellite"),
             "set", 1, []),
        ),
    },
    {
        "id": "B_compute_contention",
        "question": "does a bound on concurrent computation bind when, and "
                    "only when, decisions overlap?",
        "arms": (
            ("load_high", ("demand", "offered_mbps"), "scale", 8.0,
             ["packets_offered", "decisions_committed"]),
            ("load_high_compute_1", ("execution",
                                     "compute_servers_per_satellite"), "set", 1,
             ["compute_wait_s"]),
        ),
    },
)


class ProbeError(RuntimeError):
    """The probe could not be run; nothing was published."""


def _scale(value, factor):
    if isinstance(value, int) and not isinstance(value, bool):
        return int(round(value * factor))
    return float(value) * factor


def _apply_arm(resolved: dict, path, op, value, base=None) -> dict:
    """Resolve one arm.  base lets a block build on another arm's config."""
    cfg = copy.deepcopy((base or resolved)["config"])
    if path is None:
        return config_mod.resolve_config(cfg)
    cfg[path[0]][path[1]] = (_scale(cfg[path[0]][path[1]], value)
                             if op == "scale" else value)
    return config_mod.resolve_config(cfg)


def _timeline_stats(timeline_rows):
    waits = [m for m in timeline_rows if m.get("milestone") == "compute_wait"]
    starts = [m for m in timeline_rows if m.get("milestone") == "node_process_start"]
    ends = [m for m in timeline_rows if m.get("milestone") == "node_process_end"]
    per_pid_start = {}
    for m in starts:
        per_pid_start.setdefault(m.get("pid"), []).append(float(m["at"]))
    f2 = 0.0
    for m in ends:
        queue = per_pid_start.get(m.get("pid")) or []
        if queue:
            f2 += float(m["at"]) - queue.pop(0)
    return {
        "compute_wait_s": sum(float(m.get("wait_s", 0.0)) for m in waits),
        "compute_wait_max_s": max((float(m.get("wait_s", 0.0)) for m in waits),
                                  default=None),
        "compute_wait_decisions": len(waits),
        "compute_queued_decisions": sum(1 for m in waits if m.get("queueing")),
        "node_process_occupied_s": f2,
        "node_process_starts": len(starts),
    }


def _run_arm(resolved):
    work = Path(tempfile.mkdtemp(prefix="cost-pressure-", dir=str(
        os.environ.get("COST_PROBE_TMP", "."))))
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

    sink, timeline = [], []
    result = kernel.run_simulation(resolved, rows, decision_sink=sink,
                                   timeline_sink=timeline)
    by_decision, diag = decision_ledger.build_ledger(sink, timeline)
    attempts, attempt_diag = decision_ledger.build_attempts(sink, timeline)
    latencies = []
    emitted = {r["packet_id"]: float(r["emit_time_s"]) for r in rows}
    for pid, delivery in result["deliveries"].items():
        if pid in emitted:
            latencies.append(float(delivery["delivered_at"]) - emitted[pid])
    latency_mean = sum(latencies) / len(latencies) if latencies else None
    stats = _timeline_stats(timeline)
    return {
        "config_sha256": resolved["sha256"],
        "fate_counts": {k: v for k, v in result["fate_counts"].items() if v},
        "totals": result["totals"],
        "queue_area_bits_s": result["queue_area_bits_s"],
        "occupied": result["occupied"],
        "access": result["access"],
        "events_processed": result["events_processed"],
        "packets_offered": len(rows),
        "packets_delivered": len(result["deliveries"]),
        "delivery_latency_mean_s": latency_mean,
        "decisions_committed": len(by_decision),
        "decision_attempts": len(attempts),
        "attempt_outcomes": attempt_diag["by_outcome"],
        "ledger_diagnostics": diag,
        **stats,
    }


def probe(config_path: Path, root: Path) -> dict[str, Any]:
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise ProbeError(f"config invalid: {exc}") from exc
    if resolved["config"]["learning"]["algorithm"] != "none":
        raise ProbeError(
            "the cost/pressure probe is defined for a deterministic router; "
            f"learning.algorithm="
            f"{resolved['config']['learning']['algorithm']!r}")

    old_tmp = os.environ.get("COST_PROBE_TMP")
    os.environ["COST_PROBE_TMP"] = str(root)
    blocks = []
    try:
        for block in BLOCKS:
            base_cfg = resolved
            base_name = None
            arms, deltas = {}, {}
            for name, path, op, value, expected in block["arms"]:
                # the first arm of a block is its own baseline; every later arm
                # differs from THAT by exactly one leaf
                if base_name is None:
                    base_name = name
                    base_cfg = _apply_arm(base_cfg, path, op, value)
                    cfg = base_cfg
                else:
                    cfg = _apply_arm(base_cfg, path, op, value)
                arms[name] = {
                    "changed_path": (".".join(path) if path else None),
                    "changed_to": (cfg["config"][path[0]][path[1]]
                                   if path else None),
                    "expected_to_move": expected,
                    "is_block_baseline": name == base_name,
                    **_run_arm(cfg),
                }
            base = arms[base_name]
            for name, arm in arms.items():
                if name == base_name:
                    continue
                deltas[name] = {
                    "vs": base_name,
                    "changed_path": arm["changed_path"],
                    "packets_offered": (arm["packets_offered"]
                                        - base["packets_offered"]),
                    "packets_delivered": (arm["packets_delivered"]
                                          - base["packets_delivered"]),
                    "decisions_committed": (arm["decisions_committed"]
                                            - base["decisions_committed"]),
                    "events_processed": (arm["events_processed"]
                                         - base["events_processed"]),
                    "queue_area_bits_s": {
                        k: round(v - base["queue_area_bits_s"].get(k, 0.0), 3)
                        for k, v in arm["queue_area_bits_s"].items()},
                    "occupied_s": {k: round(v - base["occupied"].get(k, 0.0), 6)
                                   for k, v in arm["occupied"].items()},
                    "compute_wait_s": round(arm["compute_wait_s"]
                                            - base["compute_wait_s"], 6),
                    "node_process_occupied_s": round(
                        arm["node_process_occupied_s"]
                        - base["node_process_occupied_s"], 6),
                    "delivery_latency_mean_s": (
                        None if arm["delivery_latency_mean_s"] is None
                        or base["delivery_latency_mean_s"] is None
                        else round(arm["delivery_latency_mean_s"]
                                   - base["delivery_latency_mean_s"], 6)),
                    "access": {k: (round(v - base["access"].get(k, 0.0), 6)
                                   if isinstance(v, (int, float)) else None)
                               for k, v in arm["access"].items()},
                }
            # Separation verdict, stated in the report rather than left to the
            # reader: did every key the arm was EXPECTED to move actually move,
            # and did any key outside that set move as well?
            for name, delta in deltas.items():
                expected = set(arms[name]["expected_to_move"])
                movable = ("packets_offered", "packets_delivered",
                           "decisions_committed", "events_processed",
                           "compute_wait_s", "node_process_occupied_s",
                           "delivery_latency_mean_s", "occupied_s",
                           "queue_area_bits_s", "access")
                moved = []
                for key in movable:
                    value = delta.get(key)
                    if isinstance(value, dict):
                        magnitude = max((abs(v) for v in value.values()
                                         if isinstance(v, (int, float))),
                                        default=0.0)
                    else:
                        magnitude = abs(value or 0.0)
                    if magnitude > 0:
                        moved.append(key)
                delta["quantities_that_moved"] = moved
                delta["expected_keys_moved"] = sorted(expected & set(moved))
                delta["unexpected_keys_moved"] = sorted(set(moved) - expected)
                delta["separation_ok"] = (not (expected - set(moved)))
            blocks.append({
                "id": block["id"],
                "question": block["question"],
                "baseline_arm": base_name,
                "arms": arms,
                "deltas": deltas,
            })
    finally:
        if old_tmp is None:
            os.environ.pop("COST_PROBE_TMP", None)
        else:
            os.environ["COST_PROBE_TMP"] = old_tmp

    return {
        "schema": SCHEMA,
        "source": {
            "config": str(config_path),
            "config_sha256": resolved["sha256"],
            "code_sha256": receipt.code_sha256(),
            "policy": resolved["config"]["routing"]["policy"],
            "learning_algorithm": resolved["config"]["learning"]["algorithm"],
            "obs_mode":
                resolved["config"]["execution"]["decision_observation_mode"],
        },
        "design": {
            "kind": "one_at_a_time_screening, two blocks",
            "blocks": [{"id": b["id"], "question": b["question"],
                        "baseline_arm": b["baseline_arm"],
                        "arms": [a[0] for a in dict(
                            (x["id"], x["arms"]) for x in BLOCKS)[b["id"]]]}
                       for b in blocks],
            "negative_controls": ["compute_servers_1_control"],
        },
        "blocks": blocks,
        "limits": [
            "one seed, one cell per arm, no replication: this is a screening "
            "probe and supports no statistical statement",
            "one-at-a-time screening cannot detect an interaction between two "
            "levers; a replicated factorial design is required for that",
            "the fixture has a single OD pair, so 'target egress pressure' is "
            "read through the destination downlink occupancy and the access "
            "counters rather than through a dedicated field",
            "delivery latency is a mean over delivered packets only; a loss "
            "changes the denominator, so read it together with fate_counts",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="One-at-a-time cost and pressure probe")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = probe(args.config, args.root)
    except ProbeError as exc:
        print(f"PROBE REFUSED: {exc}")
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(f"PROBE REFUSED: output destination exists: {args.out}")
        return 2
    handle, temporary = tempfile.mkstemp(
        prefix=f".{args.out.name}.", suffix=".tmp", dir=str(args.out.parent))
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                  indent=1)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, args.out)
    summary = {}
    for block in document["blocks"]:
        summary[block["id"]] = {
            "baseline": block["baseline_arm"],
            "arms": list(block["arms"]),
            "separation_ok": {name: d["separation_ok"]
                              for name, d in block["deltas"].items()},
            "unexpected_moves": {name: d["unexpected_keys_moved"]
                                 for name, d in block["deltas"].items()
                                 if d["unexpected_keys_moved"]},
        }
    print(json.dumps({
        "status": "probed",
        "out": str(args.out),
        "blocks": summary,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
