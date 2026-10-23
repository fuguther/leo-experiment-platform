import json
from pathlib import Path

from CODE.experiment_platform import t1_admission as admission


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _query_evidence(i, direction, *, resource_valid=True):
    resource = {
        "status": "ok", "kind": "isl", "peer": 100 + i,
        "egress_direction": "E" if direction == "N" else "W",
        "egress_peer": 200 + i, "isl_rate_bps": 5_000_000,
    }
    if not resource_valid:
        resource = {"status": "missing", "kind": None, "peer": None,
                    "egress_direction": None, "egress_peer": None,
                    "isl_rate_bps": None}
    return {
        "query_target": i + (7.0 if direction == "N" else 8.0),
        "score_total_s": 0.1,
        "missing": [],
        "fallback": False,
        "resource_target": resource,
    }


def _action(i, *, effective=True):
    evidence = {direction: _query_evidence(
        i, direction, resource_valid=effective)
        for direction in ("N", "S")}
    return {
        "decision_id": i + 1,
        "t_decision_start": 6.0 + i,
        "legal_candidates": ["N", "S"],
        "query_targets": {direction: value["query_target"]
                          for direction, value in evidence.items()},
        "candidate_query_count": 2,
        "effective_candidate_query_count": 2 if effective else 0,
        "candidate_query_evidence": evidence,
    }


def _network_decision(i, *, competing=True):
    directions = ("N", "E", "S", "W")
    legal = ["N", "S"]
    details = {
        direction: {"queue_bits_before": (
            1000 if competing and direction == "N" else 0)}
        for direction in directions}
    return {"t_decision_start": 6.0 + i, "kind": "forward",
            "four_direction_audit": {
                "decision_kind": "forward",
                "direction_order": list(directions),
                "committed_legal_directions": legal,
                "final_legal_mask": {d: d in legal for d in directions},
                "final_mask_matches_committed_set": True,
                "candidate_details": details}}


def _replay():
    manifest = [{"pid": i + 1, "od_id": f"od-{i + 1}",
                 "emit_time_s": 0.0} for i in range(6)]
    timeline = [{"milestone": "packet_fate", "pid": i + 1,
                 "at": 10.0} for i in range(6)]
    return {"captured": True, "fates": {str(i + 1): "DELIVERED"
                                            for i in range(6)},
            "timeline_rows": timeline, "stop_time_s": 50.0,
            "deliveries": {}}


def _fixtures(tmp_path, *, effective_ratio=1.0, competing=5):
    run_dir = tmp_path / "b_dev"
    scenario = "multiod"
    network_id = f"b-{scenario}-network-seed-7"
    branch_id = f"b-{scenario}-branch-seed-7"
    modes_id = f"b-{scenario}-execution-modes-seed-7"
    arms = []
    flows = [{"id": f"od-{i + 1}"} for i in range(6)]
    manifest = [{"pid": i + 1, "od_id": f"od-{i + 1}",
                 "emit_time_s": 0.0} for i in range(6)]
    for name in ("stale", "now", "common", "candidate"):
        actions = []
        for i in range(20):
            actions.append(_action(
                i, effective=i < round(20 * effective_ratio)))
        arms.append({"arm": name,
                     "scope": {"packets_in_trace": 100},
                     "action_log": {"records": actions},
                     "routing_audit_log": {
                         "decision_record_count": 20,
                         "decision_records": [
                             _network_decision(i, competing=i < competing)
                             for i in range(20)]},
                     "replay": _replay()})
    network = {"document": {
        "arms": arms,
        "source": {"synthetic_workload": {"summary": {
            "flows": flows, "packet_manifest": manifest}}}}}
    branch = {"document": {
        "eligibility": {"eligible": 12},
        "branches": [{"decision_id": i + 1,
                       "resource_pressure_observed": False}
                      for i in range(4)],
        "explanation_replay": {"captured": True},
    }}
    modes = {"document": {"modes": [
        {"mode": name, "outcome_document": {"partition_exact": True}}
        for name in ("per_packet", "per_flow", "precomputed",
                     "async_point", "async_window")
    ]}}
    _write(run_dir / "cells" / network_id / "result.json", network)
    _write(run_dir / "cells" / branch_id / "result.json", branch)
    _write(run_dir / "cells" / modes_id / "result.json", modes)
    record_by_id = {key: {"status": "ok"} for key in
                    (network_id, branch_id, modes_id)}
    cell_by_id = {key: {"cell_id": key, "group": "b_round"} for key in
                  (network_id, branch_id, modes_id)}
    spec = {"id": scenario, "seeds": [7],
            "task_seeds": {"branch_alignment": [7]},
            "admission_seed": 7, "min_sampled_branches": 4}
    thresholds = {
        "min_offered": 1,
        "min_forward_decisions": 20,
        "min_comparable_branch_points": 10,
        "min_competing_branch_points": 5,
        "min_effective_query_coverage": 0.90,
    }
    return run_dir, spec, cell_by_id, record_by_id, thresholds


def test_structure_admission_never_reads_arm_outcomes(tmp_path):
    run_dir, spec, cells, records, thresholds = _fixtures(tmp_path)
    path = run_dir / "cells" / "b-multiod-network-seed-7" / "result.json"
    payload = json.loads(path.read_text())
    for row in payload["document"]["arms"]:
        row["loss"] = 999.0 if row["arm"] == "candidate" else -999.0
        row["delivered"] = 0 if row["arm"] == "candidate" else 1000
        row["ranking"] = [row["arm"]]
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = admission._admit_multiod(
        run_dir, spec, cells, records, thresholds, [5.0, 40.0])

    assert result["verdict"] == admission.ADMITTED
    assert result["checks"]["comparable_branch_points"]["value"] == 20
    assert result["checks"]["resource_competition_branch_points"]["value"] == 5
    assert result["checks"]["explanation_sample_block"][
        "used_for_comparable_or_competition_thresholds"] is False
    assert result["checks"]["concurrent_multi_od_coverage_per_arm"][
        "candidate"]["max_simultaneous_ods"] == 6
    assert result["forbidden_criterion"] == admission.FORBIDDEN_CRITERION


def test_effective_coverage_threshold_is_exact_and_fail_closed(tmp_path):
    run_dir, spec, cells, records, thresholds = _fixtures(
        tmp_path, effective_ratio=0.85)

    result = admission._admit_multiod(
        run_dir, spec, cells, records, thresholds, [5.0, 40.0])

    assert result["verdict"] == admission.NOT_VALID
    assert result["checks"]["effective_query_coverage_per_arm"]["candidate"]["passed"] is False


def test_full_network_queue_evidence_requires_five_competing_points(tmp_path):
    run_dir, spec, cells, records, thresholds = _fixtures(
        tmp_path, competing=4)

    result = admission._admit_multiod(
        run_dir, spec, cells, records, thresholds, [5.0, 40.0])

    assert result["verdict"] == admission.NOT_VALID
    check = result["checks"]["resource_competition_branch_points"]
    assert check["value"] == 4
    assert check["passed"] is False


def test_effective_coverage_is_rederived_from_finite_time_and_named_resource(tmp_path):
    run_dir, spec, cells, records, thresholds = _fixtures(tmp_path)
    path = run_dir / "cells" / "b-multiod-network-seed-7" / "result.json"
    payload = json.loads(path.read_text())
    action = payload["document"]["arms"][0]["action_log"]["records"][0]
    action["effective_candidate_query_count"] = 2  # deliberately stale/forged
    action["candidate_query_evidence"]["N"]["query_target"] = float("nan")
    action["query_targets"]["N"] = float("nan")
    action["candidate_query_evidence"]["S"]["resource_target"][
        "egress_peer"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = admission._admit_multiod(
        run_dir, spec, cells, records, thresholds, [5.0, 40.0])

    assert result["verdict"] == admission.NOT_VALID
    checks = result["checks"]
    assert checks["effective_query_coverage_per_arm"]["stale"][
        "effective_queries"] == 38
    assert checks["effective_query_coverage_per_arm"]["stale"][
        "reported_count_mismatches"]
