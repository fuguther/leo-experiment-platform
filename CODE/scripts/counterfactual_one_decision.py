#!/usr/bin/env python3
"""Persist a COMPACT summary of one strictly paired counterfactual replay.

Why this exists
--------------
`experiment_platform/replay_counterfactual.py` (the platform driver) serialises
the whole `kernel.run_simulation` result for BOTH branches.  On the Stage C
profile (140 satellites, 133 743 decisions) that is multi-gigabyte output for a
question whose answer is a handful of numbers per packet.  This driver uses the
SAME harness (`counterfactual.replay_with_forced_action`) and the SAME pairing
proof, and writes only:

  * the pairing verification (branch fingerprints),
  * per-branch aggregate outcome,
  * per-packet `fate` / `delivered_at` / `path` for every packet (cohort),
  * the tagged packet's own event trace,
  * the target decision row before/after the forced action.

It performs no analysis and no statistics; it only decides what is worth
keeping.  Nothing about the engine changes: no kernel edit, no new RNG call, no
process/timeout, and the forced action is the harness's own opt-in hook.

Usage
-----
    python3 CODE/scripts/counterfactual_one_decision.py \
        --config CODE/leo_sim/profiles/stage_c_e2_6g_adv200.yaml \
        --decision-id 96253 --forced-action E \
        --root /tmp --out /data/.../runs/<run-id>/cf.json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from CODE.leo_sim import config as config_mod  # noqa: E402
from CODE.leo_sim import counterfactual  # noqa: E402
from CODE.leo_sim import kernel  # noqa: E402
from CODE.leo_sim import trace as trace_mod  # noqa: E402

SCHEMA = "t1-counterfactual-compact/v1"


def _deliveries(result):
    out = {}
    for pid, row in (result.get("deliveries") or {}).items():
        out[str(pid)] = {
            "delivered_at": (None if not isinstance(row, dict)
                             else row.get("delivered_at")),
            "path": (None if not isinstance(row, dict) else row.get("path")),
        }
    return out


def _compact(result):
    return {
        "fate_counts": result.get("fate_counts"),
        "fates": {str(k): v for k, v in (result.get("fates") or {}).items()},
        "totals": result.get("totals"),
        "occupied": result.get("occupied"),
        "access": result.get("access"),
        "queue_area_bits_s": result.get("queue_area_bits_s"),
        "events_processed": result.get("events_processed"),
        "stop_time_s": result.get("stop_time_s"),
        "horizon_s": result.get("horizon_s"),
        "natural_end": result.get("natural_end"),
        "deliveries": _deliveries(result),
    }


def _target_row(rows, decision_id):
    for row in rows or ():
        if row.get("decision_id") == decision_id:
            return row
    return None


def _packet_events(events, pid):
    return [row for row in (events or ())
            if row.get("pid") == pid]


def run(args) -> dict:
    resolved = config_mod.load_config_file(str(args.config))
    algorithm = resolved["config"]["learning"]["algorithm"]
    if algorithm != "none":
        raise SystemExit(
            "this driver requires a deterministic router; learning.algorithm="
            f"{algorithm!r}")
    work = Path(tempfile.mkdtemp(prefix="counterfactual-compact-",
                                 dir=str(args.root)))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=resolved["config"]["execution"]["max_packets"])
        replay = counterfactual.replay_with_forced_action(
            resolved, rows, target_decision_id=args.decision_id,
            forced_action=args.forced_action)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    base = replay["baseline"]
    forced = replay["counterfactual"]
    base_result = base["result"]
    forced_result = forced["result"]
    base_target = _target_row(base.get("decision_rows"), args.decision_id)
    forced_target = _target_row(forced.get("decision_rows"), args.decision_id)
    pid = None if base_target is None else base_target.get("pid")
    if pid is None and forced_target is not None:
        pid = forced_target.get("pid")

    base_compact = _compact(base_result)
    forced_compact = _compact(forced_result)
    tagged = None
    if pid is not None:
        tagged = {
            "pid": pid,
            "baseline": {
                "fate": base_compact["fates"].get(str(pid)),
                "delivery": base_compact["deliveries"].get(str(pid)),
                "events": _packet_events(
                    base_result.get("packet_events"), pid),
            },
            "counterfactual": {
                "fate": forced_compact["fates"].get(str(pid)),
                "delivery": forced_compact["deliveries"].get(str(pid)),
                "events": _packet_events(
                    forced_result.get("packet_events"), pid),
            },
        }
    cohort = []
    base_del = base_compact["deliveries"]
    forced_del = forced_compact["deliveries"]
    for pid_key, left in base_del.items():
        right = forced_del.get(pid_key)
        if right is None:
            cohort.append({"pid": int(pid_key), "baseline_at": left["delivered_at"],
                           "counterfactual_at": None})
            continue
        if (left["delivered_at"] is None or right["delivered_at"] is None):
            continue
        cohort.append({"pid": int(pid_key),
                       "baseline_at": left["delivered_at"],
                       "counterfactual_at": right["delivered_at"],
                       "delta_s": float(right["delivered_at"])
                       - float(left["delivered_at"])})
    fate_changes = {
        pid_key: {"baseline": base_compact["fates"].get(pid_key),
                  "counterfactual": forced_compact["fates"].get(pid_key)}
        for pid_key in set(base_compact["fates"]) | set(forced_compact["fates"])
        if base_compact["fates"].get(pid_key)
        != forced_compact["fates"].get(pid_key)
    }
    return {
        "schema": SCHEMA,
        "source": {
            "config": str(args.config),
            "config_sha256": resolved["sha256"],
            "trace_sha256": (manifest.get("__trace_sha256")
                             or manifest.get("trace_sha256")),
            "target_decision_id": args.decision_id,
            "forced_action": args.forced_action,
        },
        "verification": replay["verification"],
        "outcome_delta": replay["outcome_delta"],
        "target": {
            "baseline": (None if base_target is None else {
                key: base_target.get(key) for key in
                ("decision_id", "pid", "hop_index", "sat", "kind",
                 "candidates", "chosen", "t", "t_decision_start")}),
            "counterfactual": (None if forced_target is None else {
                key: forced_target.get(key) for key in
                ("decision_id", "pid", "hop_index", "sat", "kind",
                 "candidates", "chosen", "t", "t_decision_start")}),
        },
        "tagged_packet": tagged,
        "cohort": {
            "n_common_delivered": len(cohort),
            "fate_changes": fate_changes,
            "delivery_deltas": cohort,
        },
        "baseline": base_compact,
        "counterfactual": forced_compact,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Compact, strictly paired counterfactual replay")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--decision-id", type=int, required=True)
    parser.add_argument("--forced-action", required=True)
    parser.add_argument("--root", default="/tmp")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = run(args)
    except counterfactual.CounterfactualError as exc:
        print("REPLAY REFUSED:", exc)
        return 2
    except kernel.KernelError as exc:
        print("ENGINE REFUSED:", exc)
        return 2
    handle, temporary = tempfile.mkstemp(
        prefix="." + args.out.name + ".", suffix=".tmp",
        dir=str(args.out.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
            stream.write("\n")
        os.replace(temporary, args.out)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    verification = document["verification"]
    print(json.dumps({
        "status": "replayed",
        "out": str(args.out),
        "branch_states_identical": verification["branch_states_identical"],
        "baseline_action": verification["baseline_action"],
        "forced_action": verification["forced_action"],
        "tagged_pid": (document.get("tagged_packet") or {}).get("pid"),
        "outcome_delta": document["outcome_delta"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
