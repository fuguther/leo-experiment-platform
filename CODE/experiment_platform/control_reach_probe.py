"""Two single-factor diagnostics on a FIXED traffic trace.

Purpose, and what it is not
---------------------------
The point is to DISCRIMINATE between explanations for a missing destination
advertisement, not to show that either change is good.  Neither arm is a
performance result and neither may be quoted as one: extending the horizon and
widening the control plane both cost something, and this tool reports that cost
next to the effect so the two cannot be read apart.

The trace is FIXED
------------------
Both arms reuse the baseline arm's compiled trace rows.  The horizon arm
achieves this by overriding execution/scenario duration AFTER the trace was
compiled and loaded, so the packet set is byte-identical; the reach arm only
changes control_plane.vis_k, which no emission depends on.  The tool asserts
the row digest is equal across arms and refuses if it is not -- an arm that
silently changed the traffic would not be a single-factor diagnostic.

Reading the output
------------------
  reach arm   answers "is the advertisement missing because it cannot travel
              that far?"  If widening vis_k removes the no_info attempts, the
              cause was reach; if it does not, reach was not the cause.
  horizon arm answers "is it missing because the run stopped too early?"
              If a longer drain resolves the holds, the cause was the window;
              if it does not, the window was not the cause.

Usage
-----
    python3 -m CODE.experiment_platform.control_reach_probe \
        --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
        --drain-to 200 --vis-k 4 --out out/control-reach.json
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

from CODE.experiment_platform import artifact_identity
from CODE.leo_sim import config as config_mod, kernel, trace as trace_mod

SCHEMA = "control-reach-probe/v2"
SCHEMA_V1 = "control-reach-probe/v1"

# v1 named a SECONDS quantity "ctrl_isl_bits".  The value was always
# occupied["ctrl_isl_s"] (seconds); the field name alone was wrong.  v2 keeps
# the seconds under ctrl_isl_occupied_s and records control bits separately,
# so a reader can never add seconds to bits.
UNITS = {
    "ctrl_isl_occupied_s": "seconds of control-packet ISL service occupancy",
    "ctrl_isl_served_bits": "control bits that actually arrived at a peer "
                            "(one instance per forwarding hop)",
    "ctrl_isl_offered_bits": "control bits offered into the control plane",
}
LEGACY_UNIT_NOTES = {
    SCHEMA_V1: {
        "ctrl_isl_bits": "v1 mislabelled a SECONDS value as bits; the reader "
                         "must treat it as seconds and use "
                         "ctrl_isl_occupied_s",
    },
}


class ProbeError(RuntimeError):
    pass


def _rows_digest(rows):
    payload = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _no_info_counts(timeline, dst_of):
    counts, pairs = {}, set()
    for row in timeline:
        obs = row.get("observation_at_start")
        if row.get("milestone") not in ("frozen_inferred_hold",
                                        "commit_rejected"):
            continue
        if not isinstance(obs, dict) or obs.get("routing_status") != "no_info":
            continue
        entries = (obs.get("neighbours") or {}).values()
        covering = any(dst_of.get(row.get("pid")) in
                       (e.get("advertised_serve_cells") or [])
                       for e in entries)
        key = ("COVERING_ENTRY_PRESENT" if covering
               else "NO_DESTINATION_ADVERTISEMENT")
        counts[key] = counts.get(key, 0) + 1
        if not covering:
            pairs.add("%s|%s" % (row.get("sat"), dst_of.get(row.get("pid"))))
    return counts, sorted(pairs)


def _control_overhead(result: dict) -> dict:
    """Extract the control-plane cost block with UNITS that cannot be mixed.

    ctrl_isl_occupied_s is SECONDS of ISL service time; ctrl_isl_served_bits is
    control BITS that actually arrived (accumulated independently from the
    control fate ledger, one instance per forwarding hop).  Schema v1 put the
    seconds value in a field named ctrl_isl_bits; this function never does.
    """
    control = result["control"]
    bits = control.get("bits") if isinstance(control.get("bits"), dict) else {}
    return {
        "snapshots_created": control["counters"]["snapshots_created"],
        "registered": control["counters"]["registered"],
        "entered_queue": control["counters"]["entered_queue"],
        "arrived": control["counters"]["arrived"],
        "expired": control["counters"]["expired"],
        "overflow": control["counters"]["overflow"],
        # SECONDS (was mislabelled ctrl_isl_bits in schema v1)
        "ctrl_isl_occupied_s": float(result["occupied"].get("ctrl_isl_s", 0.0)),
        # BITS, independent accumulation
        "ctrl_isl_served_bits": int(bits.get("delivered", 0)),
        "ctrl_isl_offered_bits": int(bits.get("offered", 0)),
        "control_packet_bits": sum(bits.values()) if bits else None,
        "control_fate_counts": {k: v for k, v in
                                control["fate_counts"].items() if v},
        "events_processed": result["events_processed"],
    }


def _arm(name, resolved, rows, dst_of, changed, forced=None):
    sink, timeline = [], []
    result = kernel.run_simulation(
        resolved, rows, decision_sink=sink, timeline_sink=timeline,
        forced_actions=(None if forced is None
                        else {int(forced[0]): str(forced[1])}))
    counts, pairs = _no_info_counts(timeline, dst_of)
    return {
        "arm": name,
        "changed": changed,
        "config_sha256": resolved["sha256"],
        "no_info_by_cause": counts,
        "pairs_never_covered": pairs,
        "fate_counts": {k: v for k, v in result["fate_counts"].items() if v},
        "delivered": len(result["deliveries"]),
        "control_overhead": _control_overhead(result),
    }


def probe(config_path: Path, root: Path, drain_to, vis_k, forced=None):
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise ProbeError(f"config invalid: {exc}") from exc
    if resolved["config"]["learning"]["algorithm"] != "none":
        raise ProbeError("the probe requires a deterministic router")

    work = Path(tempfile.mkdtemp(prefix="reach-probe-", dir=str(root)))
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
    digest = _rows_digest(rows)
    dst_of = {r["packet_id"]: r["dst_grid_id"] for r in rows}

    arms = [_arm("baseline", resolved, rows, dst_of, None, forced)]

    if drain_to is not None:
        if drain_to <= resolved["config"]["scenario"]["duration_s"]:
            raise ProbeError("--drain-to must exceed the configured duration")
        longer = copy.deepcopy(resolved)
        longer["config"]["scenario"]["duration_s"] = float(drain_to)
        longer = config_mod.resolve_config(longer["config"])
        if _rows_digest(rows) != digest:
            raise ProbeError("the drain arm changed the trace; refusing")
        arms.append(_arm("extended_drain", longer, rows, dst_of,
                         {"scenario.duration_s":
                          longer["config"]["scenario"]["duration_s"]}, forced))

    if vis_k is not None:
        wider = copy.deepcopy(resolved)
        wider["config"]["control_plane"]["vis_k"] = int(vis_k)
        wider = config_mod.resolve_config(wider["config"])
        if _rows_digest(rows) != digest:
            raise ProbeError("the reach arm changed the trace; refusing")
        arms.append(_arm("wider_reach", wider, rows, dst_of,
                         {"control_plane.vis_k": int(vis_k)}, forced))

    return {
        "schema": SCHEMA,
        "units": dict(UNITS),
        "legacy_unit_notes": LEGACY_UNIT_NOTES,
        "identity": artifact_identity.build_identity(
            config=resolved, trace_digest=digest,
            driver_paths=artifact_identity.DEFAULT_DRIVER_PATHS + (
                "CODE/experiment_platform/control_reach_probe.py",),
            extra={"rows": len(rows), "source_config": str(config_path)}),
        "source": {
            "config": str(config_path),
            "config_sha256": resolved["sha256"],
            "rows": len(rows),
            "rows_digest": digest,
            "rows_identical_across_arms": True,
            "baseline_vis_k": resolved["config"]["control_plane"]["vis_k"],
            "baseline_duration_s":
                resolved["config"]["scenario"]["duration_s"],
            "forced": (None if forced is None
                       else {"decision_id": int(forced[0]),
                             "action": str(forced[1])}),
        },
        "arms": arms,
        "limits": [
            "cause discrimination only: neither arm is a performance result",
            "one config, one seed, no replication",
            "the drain arm extends the simulation horizon by overriding the "
            "duration after the trace was compiled and loaded; the packet set "
            "is unchanged and the digest is asserted equal",
            "the reach arm raises vis_k, which also multiplies control "
            "traffic; the added cost is reported in control_overhead",
            "the widened control plane is not a deployable proposal",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Single-factor cause discrimination on a fixed trace")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--drain-to", type=float, default=None)
    parser.add_argument("--vis-k", type=int, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--forced-decision-id", type=int, default=None)
    parser.add_argument("--forced-action", default=None)
    args = parser.parse_args(argv)
    if (args.forced_decision_id is None) != (args.forced_action is None):
        print("PROBE REFUSED: give both --forced-decision-id and "
              "--forced-action, or neither")
        return 2
    forced = (None if args.forced_decision_id is None
              else (args.forced_decision_id, args.forced_action))
    if args.drain_to is None and args.vis_k is None:
        print("PROBE REFUSED: give --drain-to and/or --vis-k")
        return 2
    try:
        document = probe(args.config, args.root, args.drain_to, args.vis_k,
                        forced)
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
    print(json.dumps({"status": "probed", "out": str(args.out),
                      "arms": [{"arm": a["arm"],
                                "changed": a["changed"],
                                "no_info": a["no_info_by_cause"],
                                "delivered": a["delivered"],
                                "ctrl_registered":
                                    a["control_overhead"]["registered"],
                                "events": a["control_overhead"]["events_processed"]}
                               for a in document["arms"]]},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
