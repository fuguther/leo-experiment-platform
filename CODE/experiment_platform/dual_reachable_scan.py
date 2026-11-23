"""Scan a real-constellation run for decision points where BOTH candidates deliver.

Why this exists
---------------
The first real-constellation branch point found (t1_frozen_branch_smoke) is a
FEASIBILITY contrast: the non-preferred candidate dead-ends.  Before any
predictor work is worth doing, the question is whether the real constellation
contains branch points where the two candidates cost something DIFFERENT and
both are viable -- if it does not, a predictor has nothing to predict.

Selection rule, declared before the run
---------------------------------------
R1  Candidate points are the COMMITTED forward decisions of one unforced
    baseline run whose recorded candidate set has at least two entries.  A
    decision that was held or rejected is not a point: it has no chosen action
    to compare against.
R2  For a point with chosen direction c and legal set L, the alternatives are
    the members of L other than c.  Each alternative is executed as one forced
    replay of the SAME config, trace and seed, forcing exactly that direction
    at that decision.
R3  A point is DUAL-REACHABLE for an alternative d when the target packet's
    fate is DELIVERED in BOTH the baseline and the forced branch.  Anything
    else is a failure and is reported with its reason, not dropped.
R4  A point is counted once as dual-reachable if ANY of its alternatives is
    dual-reachable; every alternative is also reported separately, so a single
    lucky direction cannot hide the ones that dead-end.

Cost bound, also declared: at most --max-points points are executed, taken in
ascending decision id, and every point skipped by the bound is listed.

Reading the scan
----------------
A low dual-reachable ratio is a RESULT, not a failure of the scan: it says the
real constellation rarely offers two viable alternatives at one frozen branch
point, which is exactly what decides whether a cost predictor is worth
building.

Usage
-----
    python3 -m CODE.experiment_platform.dual_reachable_scan \
        --config CODE/leo_sim/profiles/t1_real_dual_scan.yaml \
        --max-points 12 --out out/dual-scan.json
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from CODE.leo_sim import config as config_mod
from CODE.leo_sim import counterfactual, decision_ledger, kernel, trace as trace_mod
from CODE.experiment_platform import minimal_branch_compare as mbc

SCHEMA = "dual-reachable-scan/v1"


class ScanError(RuntimeError):
    pass


def _baseline(resolved, rows):
    sink, timeline = [], []
    result = kernel.run_simulation(resolved, rows, decision_sink=sink,
                                   timeline_sink=timeline)
    return result, sink, timeline


def _points(decision_rows):
    out = []
    for row in decision_rows:
        if row.get("kind") != "forward":
            continue
        candidates = sorted(row.get("candidates") or [])
        if len(candidates) < 2:
            continue
        chosen = row.get("chosen")
        out.append({
            "decision_id": row["decision_id"],
            "pid": row["pid"],
            "sat": row["sat"],
            "t_decision_start": float(row.get("t_decision_start", row["t"])),
            "chosen": chosen,
            "candidates": candidates,
            "alternatives": [d for d in candidates if d != chosen],
        })
    out.sort(key=lambda p: p["decision_id"])
    return out


def _failure_reason(baseline_fate, forced, error):
    if error:
        return {"kind": "ENGINE_REFUSED", "detail": error}
    if baseline_fate != "DELIVERED":
        return {"kind": "BASELINE_NOT_DELIVERED",
                "detail": "the unforced branch itself did not deliver packet "
                          "%s (fate %s), so there is no cost to compare"
                          % (forced["pid"], baseline_fate)}
    if forced["fate"] != "DELIVERED":
        return {"kind": "FORCED_NOT_DELIVERED",
                "detail": "forcing %s ended with fate %s"
                          % (forced["forced_action"], forced["fate"])}
    return None


def scan(config_path: Path, root: Path, max_points: int):
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise ScanError(f"config invalid: {exc}") from exc
    execution = resolved["config"]["execution"]
    if execution["decision_observation_mode"] != "frozen":
        raise ScanError("the scan is defined for frozen branch points")
    if resolved["config"]["learning"]["algorithm"] != "none":
        raise ScanError("the scan requires a deterministic router")

    work = Path(tempfile.mkdtemp(prefix="dual-scan-", dir=str(root)))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=execution["max_packets"])
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

    result, sink, timeline = _baseline(resolved, rows)
    ledger, _diag = decision_ledger.build_ledger(sink, timeline)
    attempts, attempt_diag = decision_ledger.build_attempts(sink, timeline)
    points = _points(sink)
    executed = points[:max_points] if max_points > 0 else points
    skipped = points[len(executed):]

    source = {
        "config": str(config_path),
        "config_sha256": resolved["sha256"],
        "trace_sha256": (manifest.get("__trace_sha256")
                         or manifest.get("trace_sha256")),
        "code_sha256": __import__("CODE.leo_sim.receipt", fromlist=["x"])
        .code_sha256(),
        "obs_mode": execution["decision_observation_mode"],
        "compute_delay_s": execution["compute_delay_s"],
        "routing_policy": resolved["config"]["routing"]["policy"],
        "learning_algorithm": resolved["config"]["learning"]["algorithm"],
    }

    point_reports = []
    for point in executed:
        decision_id = point["decision_id"]
        pid = point["pid"]
        baseline_side = _side(decision_id, pid, ledger, sink, timeline, result,
                              point["t_decision_start"], resolved)
        baseline_fate = result["fates"].get(pid)
        alternatives = []
        for direction in point["alternatives"]:
            try:
                branch = mbc.compare_config(
                    config_path, decision_id, direction, root)
                failure = None
            except mbc.CompareDriverError as exc:
                branch = None
                failure = {"kind": "ENGINE_REFUSED", "detail": str(exc)}
            if branch is None:
                alternatives.append({"forced_action": direction,
                                     "dual_reachable": False,
                                     "failure": failure})
                continue
            alt_side = branch["branch"]["counterfactual"]
            alt_fate = alt_side["fate"]
            reason = None
            if baseline_fate != "DELIVERED":
                reason = {"kind": "BASELINE_NOT_DELIVERED",
                          "detail": f"baseline fate {baseline_fate}"}
            elif alt_fate != "DELIVERED":
                reason = {"kind": "FORCED_NOT_DELIVERED",
                          "detail": f"forced fate {alt_fate}"}
            alternatives.append({
                "forced_action": direction,
                "dual_reachable": reason is None,
                "failure": reason,
                "fate": alt_fate,
                "delivered_at": alt_side["delivered_at"],
                "delivered_at_delta_s": branch["action_cost"][
                    "delivered_at_delta_s"],
                "t_peer_arrival": alt_side["t_peer_arrival"],
                "t_peer_target_egress_enter":
                    alt_side["t_peer_target_egress_enter"],
                "egress_wait_s": alt_side["egress_wait_s"],
                "egress_wait_minus_measured_work_s":
                    alt_side["egress_wait_minus_measured_work_s"],
                "workload_before_enqueue":
                    (alt_side["workload"]["before_target_egress_enqueue"]
                     or {}).get("total_bits_ahead"),
                "workload_resource":
                    (alt_side["workload"]["before_target_egress_enqueue"]
                     or {}).get("resource"),
                "workload_changed_between_instants":
                    alt_side["workload"]["changed_between_the_two_instants"],
                "path": alt_side["path"],
            })
        point_reports.append({
            "decision_id": decision_id,
            "pid": pid,
            "sat": point["sat"],
            "t_decision_start": point["t_decision_start"],
            "chosen": point["chosen"],
            "candidates": point["candidates"],
            "baseline": {
                "fate": baseline_fate,
                "delivered_at": baseline_side["delivered_at"],
                "t_peer_arrival": baseline_side["t_peer_arrival"],
                "t_peer_target_egress_enter":
                    baseline_side["t_peer_target_egress_enter"],
                "egress_wait_s": baseline_side["egress_wait_s"],
                "workload_before_enqueue":
                    (baseline_side["workload"][
                        "before_target_egress_enqueue"] or {}).get(
                            "total_bits_ahead"),
                "workload_resource":
                    (baseline_side["workload"][
                        "before_target_egress_enqueue"] or {}).get("resource"),
                "path": baseline_side["path"],
            },
            "alternatives": alternatives,
            "dual_reachable": any(a["dual_reachable"] for a in alternatives),
        })

    dual = [p for p in point_reports if p["dual_reachable"]]
    alt_total = sum(len(p["alternatives"]) for p in point_reports)
    alt_dual = sum(1 for p in point_reports for a in p["alternatives"]
                   if a["dual_reachable"])
    reasons = {}
    for point in point_reports:
        for alt in point["alternatives"]:
            if alt["dual_reachable"]:
                continue
            kind = (alt["failure"] or {}).get("kind", "UNKNOWN")
            reasons[kind] = reasons.get(kind, 0) + 1

    costs = [a["delivered_at_delta_s"] for p in dual for a in p["alternatives"]
             if a["dual_reachable"] and a["delivered_at_delta_s"] is not None]
    return {
        "schema": SCHEMA,
        "source": source,
        "selection_rule": {
            "R1": "committed forward decisions with >= 2 recorded candidates",
            "R2": "every legal direction except the chosen one, forced one at "
                  "a time at the frozen branch point",
            "R3": "dual-reachable = the target packet is DELIVERED in both "
                  "branches",
            "R4": "a point counts once if ANY alternative is dual-reachable; "
                  "all alternatives are reported",
            "max_points": max_points,
            "declared_before_run": True,
        },
        "population": {
            "packets": len(rows),
            "forward_decisions": sum(1 for r in sink
                                     if r.get("kind") == "forward"),
            "committed_decisions": len(ledger),
            "decision_attempts": len(attempts),
            "attempt_outcomes": attempt_diag["by_outcome"],
            "candidate_points": len(points),
            "points_executed": len(point_reports),
            "points_skipped_by_bound": [p["decision_id"] for p in skipped],
        },
        "dual_reachable": {
            "points": len(dual),
            "points_ratio": (len(dual) / len(point_reports)
                             if point_reports else None),
            "alternatives_total": alt_total,
            "alternatives_dual": alt_dual,
            "alternatives_ratio": (alt_dual / alt_total if alt_total else None),
            "failure_reasons": reasons,
        },
        "cost_difference_when_dual": {
            "samples": len(costs),
            "min_s": min(costs) if costs else None,
            "max_s": max(costs) if costs else None,
            "nonzero_samples": sum(1 for c in costs if abs(c) > 1e-9),
        },
        "points": point_reports,
        "limits": [
            "diagnostic, not an authorized run: no receipt, not research "
            "eligible",
            "one config, one seed: this is a population scan, not a sample",
            "forcing is only defined for ISL forward candidates; deliver and "
            "hold branch points are not points",
            "each alternative is a paired replay verified to reach the same "
            "branch point, and the pairing claim is decision-log-witnessed",
        ],
    }


def _side(decision_id, pid, ledger, sink, timeline, result, t_branch,
          resolved):
    """One branch's record, with the same egress-wait decomposition the
    per-point driver reports.

    The helpers are imported from minimal_branch_compare rather than copied:
    a second implementation of "what was ahead of the packet" would be free to
    drift from the one the artifacts are compared against.
    """
    side = mbc._branch_side(decision_id, pid, ledger, sink, timeline, result,
                            t_branch)
    mbc._add_egress_wait(side, resolved,
                         resolved["config"]["links"]["rate_model"])
    return side


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan for decision points where both candidates deliver")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-points", type=int, default=12)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = scan(args.config, args.root, args.max_points)
    except ScanError as exc:
        print(f"SCAN REFUSED: {exc}")
        return 2
    if args.out.exists() or args.out.is_symlink():
        print(f"SCAN REFUSED: output destination exists: {args.out}")
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
    print(json.dumps({
        "status": "scanned",
        "out": str(args.out),
        "population": document["population"],
        "dual_reachable": document["dual_reachable"],
        "cost_difference_when_dual":
            document["cost_difference_when_dual"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
