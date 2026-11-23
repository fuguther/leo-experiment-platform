"""Outcome-blind structural admission for the bounded T1 development run.

Admission checks only whether a scenario exercised the declared mechanisms:
offered traffic, forward choices, legal alternatives, competing named-resource
queues, finite candidate/time queries, and event-ledger conservation.  Losses,
delivery advantage and arm rankings are deliberately never read here.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

FORBIDDEN_CRITERION = "the candidate arm wins / is better / loses less"
ADMITTED = "ADMITTED"
NOT_VALID = "NOT_VALID"
NOT_COMPUTABLE = "NOT_COMPUTABLE"


def _read(path):
    try:
        from CODE.experiment_platform.replay_codec import read_document
        return read_document(path)
    except (OSError, ValueError):
        return None


def _records(cell):
    payload = _read(cell / "result.json")
    return (payload or {}).get("document") or {}


def _valid_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _valid_named_resource(value):
    """Re-derive resource identity from the logged target snapshot."""
    if not isinstance(value, dict):
        return False
    status, kind = value.get("status"), value.get("kind")
    peer, rate = value.get("peer"), value.get("isl_rate_bps")
    if (not isinstance(peer, int) or isinstance(peer, bool) or peer < 0
            or not _valid_number(rate) or float(rate) <= 0):
        return False
    if status == "ok":
        egress_peer = value.get("egress_peer")
        return (kind == "isl"
                and isinstance(value.get("egress_direction"), str)
                and bool(value.get("egress_direction"))
                and isinstance(egress_peer, int)
                and not isinstance(egress_peer, bool)
                and egress_peer >= 0)
    if status == "delivered_downlink":
        return kind == "downlink"
    return False


def _effective_query(evidence):
    if not isinstance(evidence, dict):
        return False
    missing = evidence.get("missing")
    return (_valid_number(evidence.get("query_target"))
            and _valid_number(evidence.get("score_total_s"))
            and isinstance(missing, list) and not missing
            and evidence.get("fallback") is False
            and _valid_named_resource(evidence.get("resource_target")))


def _same_query_time(left, right):
    if left == right:
        return True
    return (isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
            and math.isnan(float(left)) and math.isnan(float(right)))


def _complete_query_evidence(action):
    count = action.get("candidate_query_count")
    evidence = action.get("candidate_query_evidence")
    legal = action.get("legal_candidates")
    query_targets = action.get("query_targets")
    if (not isinstance(count, int) or isinstance(count, bool) or count < 0
            or not isinstance(evidence, dict) or len(evidence) != count
            or not isinstance(legal, list)
            or {str(value) for value in legal} != set(evidence)
            or not isinstance(query_targets, dict)
            or set(query_targets) != set(evidence)):
        return False
    for direction, value in evidence.items():
        if (not isinstance(value, dict)
                or not _same_query_time(query_targets.get(direction),
                                        value.get("query_target"))
                or not isinstance(value.get("missing"), list)
                or not isinstance(value.get("fallback"), bool)
                or not isinstance(value.get("resource_target"), dict)):
            return False
    return True


def _full_network_branch_counts(arm_row, window, *, result_path=None):
    """Count multi-legal and queued-alternative decisions from full arm logs.

    In production the audit records are a sidecar stream and the arm row keeps
    only their count, so read them row by row through the recorded index: the
    reachability count must come from the same evidence, not from an empty list.
    """
    audit = arm_row.get("routing_audit_log") or {}
    records = audit.get("decision_records")
    reported = audit.get("decision_record_count")
    reference = arm_row.get("replay") or {}
    stream = None
    if isinstance(records, list) and records:
        stream = iter(records)
    elif reference.get("sidecar") and result_path is not None:
        from CODE.experiment_platform import replay_sidecar
        side = replay_sidecar.sidecar_of(result_path, reference)
        entry = (reference.get("index") or {}).get("routing_decision_records")
        if not side.is_file() or not isinstance(entry, dict):
            return None, "declared routing audit sidecar is missing"
        reported = int(entry.get("count", -1))
        stream = (payload.get("row") for payload in replay_sidecar.iter_indexed(
            side, int(entry.get("offset", 0)),
            int(entry.get("lines", entry.get("count", 0)))))
    if stream is None or not isinstance(reported, int):
        return None, "full routing audit is missing, truncated, or inconsistent"
    comparable, competing, sampled_ids = 0, 0, []
    seen = 0
    for record in stream:
        seen += 1
        at = record.get("t_decision_start")
        if not _valid_number(at) or not window[0] <= float(at) <= window[1]:
            continue
        mask = record.get("four_direction_audit") or {}
        if mask.get("decision_kind") != "forward":
            continue
        details = mask.get("candidate_details")
        legal = mask.get("committed_legal_directions")
        final_mask = mask.get("final_legal_mask")
        if (not isinstance(details, dict) or not isinstance(legal, list)
                or not isinstance(final_mask, dict)
                or set(final_mask) != {"N", "E", "S", "W"}
                or mask.get("final_mask_matches_committed_set") is not True):
            return None, "a forward decision lacks a complete, self-consistent N/E/S/W mask"
        legal = [direction for direction in legal
                 if direction in {"N", "E", "S", "W"}
                 and final_mask.get(direction) is True]
        if len(legal) < 2:
            continue
        comparable += 1
        sampled_ids.append(record.get("decision_id"))
        has_queued_candidate = False
        for direction in legal:
            detail = details.get(direction)
            if not isinstance(detail, dict):
                return None, "a legal candidate lacks its exact queue snapshot"
            bits = detail.get("queue_bits_before")
            if not _valid_number(bits) or float(bits) < 0:
                return None, "a legal candidate queue snapshot is missing or invalid"
            if float(bits) > 0:
                has_queued_candidate = True
        if has_queued_candidate:
            competing += 1
    if seen != reported:
        return None, "full routing audit is missing, truncated, or inconsistent"
    return {"comparable": comparable, "competing": competing,
            "decision_ids": sampled_ids}, None


def _network_concurrency(arm_row, workload_summary):
    """Rebuild simultaneous packet/OD presence from the captured full trace."""
    replay = arm_row.get("replay") or {}
    if replay.get("captured") is not True:
        return None, "full-network replay is not captured"
    manifest = workload_summary.get("packet_manifest") or []
    flows = workload_summary.get("flows") or []
    flow_ids = {str(flow.get("id")) for flow in flows
                if isinstance(flow, dict) and flow.get("id") is not None}
    if len(flow_ids) != 6 or not manifest:
        return None, "the frozen six-directed-OD packet manifest is missing"
    fates = replay.get("fates") or {}
    if not isinstance(fates, dict):
        return None, "packet fate ledger is missing"
    fate_at = {}
    for row in replay.get("timeline_rows") or []:
        if (row.get("milestone") == "packet_fate"
                and row.get("pid") is not None
                and _valid_number(row.get("at"))):
            fate_at[int(row["pid"])] = float(row["at"])
    stop = replay.get("stop_time_s")
    if not _valid_number(stop):
        return None, "exact replay stop time is missing"
    events = {}
    observed_flows = set()
    for packet in manifest:
        try:
            pid, od_id = int(packet["pid"]), str(packet["od_id"])
            emitted = float(packet["emit_time_s"])
        except (KeyError, TypeError, ValueError):
            return None, "packet manifest has an invalid identity or emission time"
        if od_id not in flow_ids or not _valid_number(emitted):
            return None, "packet manifest does not map to a declared directed OD"
        fate = fates.get(str(pid), fates.get(pid))
        if fate is None:
            return None, "packet manifest and fate ledger do not match"
        ended = fate_at.get(pid, float(stop))
        delivery = (replay.get("deliveries") or {}).get(str(pid))
        if isinstance(delivery, dict) and _valid_number(
                delivery.get("delivered_at")):
            ended = float(delivery["delivered_at"])
        if ended < emitted:
            return None, "packet fate precedes its emission"
        observed_flows.add(od_id)
        events.setdefault(emitted, {"start": [], "end": []})["start"].append(od_id)
        events.setdefault(ended, {"start": [], "end": []})["end"].append(od_id)
    active_by_od, max_packets, max_ods = {}, 0, 0
    for at in sorted(events):
        group = events[at]
        for od_id in group["end"]:
            active_by_od[od_id] = active_by_od.get(od_id, 0) - 1
            if active_by_od[od_id] <= 0:
                active_by_od.pop(od_id, None)
        for od_id in group["start"]:
            active_by_od[od_id] = active_by_od.get(od_id, 0) + 1
        max_packets = max(max_packets, sum(active_by_od.values()))
        max_ods = max(max_ods, sum(value > 0
                                   for value in active_by_od.values()))
    return {"generated_ods": len(observed_flows),
            "declared_ods": len(flow_ids),
            "max_simultaneous_packets": max_packets,
            "max_simultaneous_ods": max_ods}, None


def _admit_multiod(run_dir, spec, cell_by_id, record_by_id, thresholds,
                   window):
    scenario = str(spec["id"])
    seeds = [int(value) for value in spec.get("seeds", [7])]
    task_seeds = spec.get("task_seeds") or {}
    network_seed = int(spec.get("admission_seed", seeds[0]))
    branch_seed = int((task_seeds.get("branch_alignment") or [network_seed])[0])
    network_id = f"b-{scenario}-network-seed-{network_seed}"
    branch_id = f"b-{scenario}-branch-seed-{branch_seed}"
    network_status = record_by_id.get(network_id, {}).get("status")
    branch_status = record_by_id.get(branch_id, {}).get("status")
    network_doc = _records(run_dir / "cells" / network_id)
    branch_doc = _records(run_dir / "cells" / branch_id)
    arms = {str(row.get("arm")): row
            for row in (network_doc.get("arms") or [])}
    reasons, checks = [], {}

    if network_status != "ok" or not arms:
        checks["offered_packets"] = NOT_COMPUTABLE
        checks["forward_decisions_per_arm"] = NOT_COMPUTABLE
        checks["effective_query_coverage_per_arm"] = NOT_COMPUTABLE
        checks["comparable_branch_points"] = NOT_COMPUTABLE
        checks["resource_competition_branch_points"] = NOT_COMPUTABLE
        checks["concurrent_multi_od_coverage_per_arm"] = NOT_COMPUTABLE
        reasons.append("four-arm network cell is missing or failed")
    else:
        expected_arms = ["stale", "now", "common", "candidate"]
        offered = [((arms.get(name) or {}).get("scope") or {}).get(
            "packets_in_trace") for name in expected_arms]
        if (len(offered) != 4 or any(not isinstance(value, int) for value in offered)
                or len(set(offered)) != 1):
            checks["offered_packets"] = NOT_COMPUTABLE
            reasons.append("offered trace count is absent or differs by arm")
        else:
            checks["offered_packets"] = {
                "passed": offered[0] >= thresholds["min_offered"],
                "value": offered[0],
                "minimum": thresholds["min_offered"],
            }
            if not checks["offered_packets"]["passed"]:
                reasons.append("offered trace count is below its structural minimum")

        forward_by_arm, coverage_by_arm = {}, {}
        query_evidence_by_arm = {}
        all_query_targets = set()
        for name in expected_arms:
            row = arms.get(name)
            if row is None:
                forward_by_arm[name] = NOT_COMPUTABLE
                coverage_by_arm[name] = NOT_COMPUTABLE
                continue
            action_log = row.get("action_log") or {}
            actions = action_log.get("records") or []
            sampled = [action for action in actions
                       if _valid_number(action.get("t_decision_start"))
                       and window[0] <= float(action["t_decision_start"])
                       <= window[1]]
            scope = row.get("scope") or {}
            forward_count = scope.get("forward_decisions")
            if not isinstance(forward_count, int):
                forward_count = len(actions)
            forward_by_arm[name] = {
                "passed": forward_count >= thresholds["min_forward_decisions"],
                "value": forward_count,
                "minimum": thresholds["min_forward_decisions"],
            }
            if not forward_by_arm[name]["passed"]:
                reasons.append(f"{name} forward decisions are below the structural minimum")
            total, effective, complete, mismatches = 0, 0, True, []
            for action in sampled:
                count = action.get("candidate_query_count")
                evidence = action.get("candidate_query_evidence")
                if not _complete_query_evidence(action):
                    complete = False
                    continue
                total += count
                action_effective = sum(
                    1 for value in evidence.values()
                    if _effective_query(value))
                effective += action_effective
                reported = action.get("effective_candidate_query_count")
                if reported != action_effective:
                    mismatches.append({
                        "decision_id": action.get("decision_id"),
                        "reported": reported,
                        "rederived": action_effective,
                    })
            for action in sampled:
                for value in (action.get("query_targets") or {}).values():
                    if _valid_number(value):
                        all_query_targets.add(round(float(value), 9))
            if action_log.get("truncated"):
                coverage_by_arm[name] = NOT_COMPUTABLE
                reasons.append(f"{name} action log is truncated; full-window coverage cannot be computed")
            elif not complete:
                coverage_by_arm[name] = NOT_COMPUTABLE
                reasons.append(f"{name} candidate query evidence is incomplete")
            elif total <= 0:
                coverage_by_arm[name] = NOT_COMPUTABLE
                reasons.append(f"{name} has no scored candidate queries in the measurement window")
            else:
                ratio = effective / total
                coverage_by_arm[name] = {
                    "passed": ratio >= thresholds["min_effective_query_coverage"],
                    "effective_queries": effective,
                    "candidate_queries": total,
                    "ratio": ratio,
                    "minimum": thresholds["min_effective_query_coverage"],
                    "reported_count_mismatches": mismatches,
                }
                if mismatches:
                    coverage_by_arm[name]["passed"] = False
                if not coverage_by_arm[name]["passed"]:
                    reasons.append(f"{name} target-resource/time coverage is below threshold")
            query_evidence_by_arm[name] = {
                "complete": complete,
                "reported_count_mismatches": mismatches,
                "candidate_queries": total,
                "rederived_effective_queries": effective,
            }
        checks["forward_decisions_per_arm"] = forward_by_arm
        checks["effective_query_coverage_per_arm"] = coverage_by_arm
        checks["candidate_query_evidence_per_arm"] = query_evidence_by_arm
        checks["distinct_query_instants"] = {
            "passed": len(all_query_targets) > 1,
            "value": len(all_query_targets),
            "minimum": 2,
        }
        if len(all_query_targets) <= 1:
            reasons.append("time-alignment targets are degenerate")

        workload_summary = (((network_doc.get("source") or {}).get(
            "synthetic_workload") or {}).get("summary") or {})
        comparable_by_arm, competing_by_arm = {}, {}
        concurrency_by_arm = {}
        for name in expected_arms:
            row = arms.get(name)
            if row is None:
                comparable_by_arm[name] = NOT_COMPUTABLE
                competing_by_arm[name] = NOT_COMPUTABLE
                concurrency_by_arm[name] = NOT_COMPUTABLE
                continue
            counts, issue = _full_network_branch_counts(
                row, window,
                result_path=run_dir / "cells" / network_id / "result.json")
            if issue:
                comparable_by_arm[name] = NOT_COMPUTABLE
                competing_by_arm[name] = NOT_COMPUTABLE
                reasons.append(f"{name} full-network branch evidence: {issue}")
            else:
                comparable_by_arm[name] = {
                    "passed": counts["comparable"] >= thresholds[
                        "min_comparable_branch_points"],
                    "value": counts["comparable"],
                    "minimum": thresholds["min_comparable_branch_points"],
                    "decision_ids": counts["decision_ids"],
                }
                competing_by_arm[name] = {
                    "passed": counts["competing"] >= thresholds[
                        "min_competing_branch_points"],
                    "value": counts["competing"],
                    "minimum": thresholds["min_competing_branch_points"],
                    "definition": "full-window forward decision with at least two legal N/E/S/W directions and a positive exact queue snapshot on at least one legal outgoing resource",
                }
                if not comparable_by_arm[name]["passed"]:
                    reasons.append(
                        f"{name} full-network comparable branch points are below threshold")
                if not competing_by_arm[name]["passed"]:
                    reasons.append(
                        f"{name} full-network queue-competition points are below threshold")
            concurrency, issue = _network_concurrency(row, workload_summary)
            if issue:
                concurrency_by_arm[name] = NOT_COMPUTABLE
                reasons.append(f"{name} full-network concurrent OD evidence: {issue}")
            else:
                passed = (concurrency["generated_ods"] >= thresholds.get(
                    "min_generated_ods", 6)
                    and concurrency["max_simultaneous_packets"] >= thresholds.get(
                        "min_simultaneous_packets", 5)
                    and concurrency["max_simultaneous_ods"] >= thresholds.get(
                        "min_simultaneous_ods", 2))
                concurrency_by_arm[name] = {
                    **concurrency, "passed": passed,
                    "minimum_simultaneous_packets": thresholds.get(
                        "min_simultaneous_packets", 5),
                    "minimum_simultaneous_ods": thresholds.get(
                        "min_simultaneous_ods", 2),
                    "required_generated_ods": thresholds.get(
                        "min_generated_ods", 6),
                }
                if not passed:
                    reasons.append(
                        f"{name} did not show six generated ODs with simultaneous multi-OD in-flight traffic")
        comparable_values = [value for value in comparable_by_arm.values()
                             if isinstance(value, dict)]
        competing_values = [value for value in competing_by_arm.values()
                            if isinstance(value, dict)]
        checks["comparable_branch_points"] = (
            {"passed": (len(comparable_values) == 4 and all(
                value["passed"] for value in comparable_values)),
             "value": min((value["value"] for value in comparable_values),
                          default=0),
             "minimum": thresholds["min_comparable_branch_points"],
             "per_arm": comparable_by_arm,
             "source": "complete routing_audit_log from each full-network arm"}
            if len(comparable_values) == 4 else NOT_COMPUTABLE)
        checks["resource_competition_branch_points"] = (
            {"passed": (len(competing_values) == 4 and all(
                value["passed"] for value in competing_values)),
             "value": min((value["value"] for value in competing_values),
                          default=0),
             "minimum": thresholds["min_competing_branch_points"],
             "per_arm": competing_by_arm,
             "source": "full-window N/E/S/W queue snapshots in routing_audit_log; explanation branch samples are not used for this threshold"}
            if len(competing_values) == 4 else NOT_COMPUTABLE)
        checks["concurrent_multi_od_coverage_per_arm"] = concurrency_by_arm

    if branch_status != "ok":
        checks["explanation_sample_block"] = NOT_COMPUTABLE
        reasons.append("branch-alignment explanation sample cell is missing or failed")
    else:
        branch_rows = branch_doc.get("branches") or []
        minimum_samples = int(spec.get("min_sampled_branches", 1))
        explanation = branch_doc.get("explanation_replay") or {}
        checks["explanation_sample_block"] = {
            "passed": (len(branch_rows) >= minimum_samples
                       and explanation.get("captured") is True),
            "sampled_decisions": len(branch_rows),
            "minimum_sampled_decisions": minimum_samples,
            "captured": explanation.get("captured") is True,
            "used_for_comparable_or_competition_thresholds": False,
        }
        if not checks["explanation_sample_block"]["passed"]:
            reasons.append("the predeclared explanation sample block is incomplete")

    mode_ids = [cell_id for cell_id, cell in cell_by_id.items()
                if cell_id.startswith(f"b-{scenario}-execution-modes-seed-")
                or cell_id.startswith(f"b-{scenario}-modes-")]
    partitions = []
    for cell_id in mode_ids:
        payload = _read(run_dir / "cells" / cell_id / "result.json")
        document = (payload or {}).get("document") or {}
        for mode in (document.get("modes") or []):
            partition = (mode.get("outcome_document") or {}).get("partition_exact")
            partitions.append({"cell_id": cell_id, "mode": mode.get("mode"),
                               "partition_exact": partition})
    if not partitions:
        checks["event_conservation"] = NOT_COMPUTABLE
        reasons.append("no execution-mode fate partitions are available")
    else:
        checks["event_conservation"] = {
            "passed": all(row["partition_exact"] is True for row in partitions),
            "partitions_checked": len(partitions),
            "partitions": partitions,
        }
        if not checks["event_conservation"]["passed"]:
            reasons.append("one or more offered/admitted/delivered/loss/censored partitions failed")

    flat_passes = []
    for name, value in checks.items():
        if value == NOT_COMPUTABLE:
            flat_passes.append(None)
        elif name in ("forward_decisions_per_arm",
                      "effective_query_coverage_per_arm",
                      "concurrent_multi_od_coverage_per_arm"):
            flat_passes.extend(
                None if item == NOT_COMPUTABLE else item.get("passed")
                for item in value.values())
        elif name == "candidate_query_evidence_per_arm":
            flat_passes.extend(
                None if not item.get("complete") else
                not bool(item.get("reported_count_mismatches"))
                for item in value.values())
        elif name == "event_conservation":
            flat_passes.append(value.get("passed"))
        else:
            flat_passes.append(value.get("passed"))
    if any(value is False for value in flat_passes):
        verdict = NOT_VALID
    elif any(value is None for value in flat_passes):
        verdict = NOT_COMPUTABLE
    else:
        verdict = ADMITTED
    return {"schema": "t1-scenario-admission/v1", "scenario": scenario,
            "role": "competitive_multi_od", "seed": network_seed,
            "verdict": verdict, "checks": checks, "reasons": reasons,
            "forbidden_criterion": FORBIDDEN_CRITERION}


def evaluate_run(run_dir, bundle, contract):
    """Evaluate every declared competitive scenario without reading outcomes."""
    run_dir = Path(run_dir)
    run_doc = _read(run_dir / "run.json") or {}
    cell_by_id = {cell["cell_id"]: cell for cell in bundle.get("cells", [])}
    record_by_id = {cell.get("cell_id"): cell
                    for cell in run_doc.get("cells", [])}
    thresholds = {
        "min_offered": 1,
        "min_forward_decisions": 20,
        "min_comparable_branch_points": 10,
        "min_competing_branch_points": 5,
        "min_effective_query_coverage": 0.90,
        "min_generated_ods": 6,
        "min_simultaneous_packets": 5,
        "min_simultaneous_ods": 2,
    }
    thresholds.update(contract.get("admission_thresholds") or {})
    window = [float(value) for value in
              contract.get("admission_measurement_window_s", [5.0, 40.0])]
    competitive = [spec for spec in
                   ((contract.get("b_round") or {}).get("scenarios") or [])
                   if spec.get("expectation") == "competitive_multi_od"]
    entries = [_admit_multiod(run_dir, spec, cell_by_id, record_by_id,
                              thresholds, window)
               for spec in competitive]
    return {"schema": "t1-scenario-admission-report/v1",
            "measurement_window_s": window,
            "thresholds": thresholds,
            "outcome_blind": True,
            "competitive_scenarios": entries,
            "summary": {"admitted": sum(e["verdict"] == ADMITTED for e in entries),
                        "not_valid": sum(e["verdict"] == NOT_VALID for e in entries),
                        "not_computable": sum(e["verdict"] == NOT_COMPUTABLE
                                               for e in entries)},
            "limitations": [
                "admission is a development design gate, not an effect or power test",
                "branches within one seed are not independent statistical units",
                "candidate outcome, loss, delivery advantage and arm rank are not inspected",
            ]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    bundle = _read(args.bundle)
    contract = _read_yaml(args.contract)
    if bundle is None or contract is None:
        raise SystemExit("T1 admission refused: bundle or contract is unreadable")
    report = evaluate_run(args.run_dir, bundle, contract)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    return 0


def _read_yaml(path):
    try:
        import yaml
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
