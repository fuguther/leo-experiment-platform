import json

import pytest

from CODE.experiment_platform import replay_html


def _network_payload():
    manifest = [{"pid": 1, "od_id": "a_to_b", "emit_time_s": 5.0},
                {"pid": 2, "od_id": "b_to_c", "emit_time_s": 5.5}]
    summary = {
        "seed": 11,
        "packet_count": 2,
        "unique_od_count": 6,
        "flows": [{"id": f"od{i}", "src": "a", "dst": "b"}
                  for i in range(6)],
        "sites": [],
        "phases": [],
        "packet_manifest": manifest,
        "compiled_od_mapping": {f"od{i}": {"src_grid_id": "1",
                                                 "dst_grid_id": "2"}
                                for i in range(6)},
    }
    arms = []
    for index, name in enumerate(replay_html.ARMS):
        replay = {
            "captured": True,
            "stop_time_s": 50.0,
            "horizon_s": 50.0,
            "fates": {"1": "DELIVERED", "2": "NO_ROUTE"},
            "deliveries": {"1": {"delivered_at": 8.0,
                                  "path": [1, 3, 2]}},
            "topology_trace": [{"at": 0.0, "peer_by_satellite": {
                str(i): {"N": None, "E": None, "S": None, "W": None}
                for i in range(24)}}],
            "packet_events": [{"kind": "queue_enter", "pid": 1,
                               "at": 6.0, "link_id": "isl:1:3"},
                              {"kind": "delivered", "pid": 1,
                               "at": 8.0}],
            "timeline_rows": [{"milestone": "packet_fate", "pid": 1,
                                "at": 8.0, "fate": "DELIVERED"},
                               {"milestone": "packet_fate", "pid": 2,
                                "at": 9.0, "fate": "NO_ROUTE"}],
            "queue_state_events": [
                {"at": 4.0, "resource_id": "isl:1:3", "generation": 1,
                 "queued_data_bits": 10, "queued_control_bits": 0,
                 "in_service_bits": 0},
                {"at": 5.0, "resource_id": "isl:1:3", "generation": 2,
                 "queued_data_bits": 20, "queued_control_bits": 0,
                 "in_service_bits": 0},
            ],
        }
        arms.append({
            "arm": name,
            "scope": {"duration_s": 50.0},
            "stop_time_s": 50.0,
            "horizon_s": 50.0,
            "outcome": {"offered": 2, "delivered": 1,
                        "delivered_bits": 1000,
                        "goodput_bps_in_window": 125.0,
                        "deadline_primary_loss": {
                            "status": "COMPUTED", "value": 0.4}},
            "total_cost": {},
            "replay": replay,
            "routing_audit_log": {
                "decision_record_count": 1,
                "decision_records": [{
                    "pid": 1, "decision_id": 9, "sat": 1,
                    "t_decision_start": 5.0, "t_decision_commit": 5.01,
                    "chosen": "E", "four_direction_audit": {
                        "route_candidates": ["N", "E", "S", "W"],
                        "committed_legal_directions": ["E"],
                    },
                    "observation_at_start": {
                        "t_observed": 5.0,
                        "neighbours": {"3": {"advertised_history": [{
                            "generated_at": 4.0, "received_at": 4.5,
                            "advertised_isl_queue_bits": {"N": 10}}]}},
                    },
                }],
            },
        })
    return {"document": {
        "status": "ok",
        "source": {"scenario": "temporal", "trace_sha256": "a" * 64,
                   "config_sha256": "b" * 64,
                   "synthetic_workload": {"summary": summary}},
        "arms": arms,
    }}


def _branch_payload():
    return {"document": {
        "status": "ok",
        "explanation_selection": {"rule": "first_structurally_eligible"},
        "explanation_replay": {
            "captured": True,
            "offline_diagnostic": True,
            "target_decision": {
                "pid": 1, "decision_id": 9, "sat": 1,
                "t_decision_start": 5.0, "chosen": "E",
                "four_direction_audit": {
                    "route_candidates": ["N", "E", "S", "W"],
                    "route_candidate_mask": {d: True for d in "NESW"},
                    "physical_legal_mask": {d: d == "E" for d in "NESW"},
                    "final_legal_mask": {d: d == "E" for d in "NESW"},
                    "filter_reason_by_direction": {d: [] for d in "NESW"},
                    "candidate_details": {d: {"peer": i + 1}
                                          for i, d in enumerate("NESW")},
                },
                "observation_at_start": {
                    "time_alignment": {"scores": {}, "query_targets": {}},
                    "candidate_resources": {},
                },
            },
            "target_packet_id": 1,
            "shared_baseline": {"timeline_rows": []},
            "forced_candidate_branches": {d: {"timeline_rows": []}
                                           for d in "NESW"},
            "candidate_outcomes": {},
        },
    }}


def test_replay_keeps_each_generation_and_population_fates():
    payload = _network_payload()
    doc = replay_html.build_document(payload, _branch_payload(),
                                     run_identity={"run_id": "test"})
    arm = doc["arms"][0]

    assert arm["replay"]["population_series"][-1]["active_total"] == 0
    assert arm["replay"]["population_series"][-1]["delivered_total"] == 1
    assert arm["replay"]["queue_series"][-1]["queued_data_bits"] == 30


def test_html_is_self_contained_selectable_and_escapes_script_termination(tmp_path):
    payload = _network_payload()
    payload["document"]["source"]["scenario"] = "</script><script>bad()</script>"
    network_path = tmp_path / "network.json"
    branch_path = tmp_path / "branch.json"
    html_path = tmp_path / "replay.html"
    network_path.write_text(json.dumps(payload), encoding="utf-8")
    branch_path.write_text(json.dumps(_branch_payload()), encoding="utf-8")

    result = replay_html.write_html(network_path, branch_path, html_path,
                                    run_identity={"run_id": "test"})
    html = html_path.read_text(encoding="utf-8")

    assert result["arm_count"] == 4
    assert "<select id=\"packetSelect\">" in html
    assert "route_candidate_mask" in html
    assert "function timeBar(events)" in html
    assert "收到→决策→预计→实际" in html
    assert "实际传播" in html
    assert "\\u003c/script\\u003e" in html
    assert "</script><script>bad()" not in html


def test_replay_refuses_missing_arm_or_unbound_od_endpoints():
    payload = _network_payload()
    payload["document"]["arms"] = payload["document"]["arms"][:-1]
    with pytest.raises(replay_html.ReplayError, match="four information arms"):
        replay_html.build_document(payload, _branch_payload())

    payload = _network_payload()
    payload["document"]["source"]["synthetic_workload"]["summary"][
        "compiled_od_mapping"] = {}
    with pytest.raises(replay_html.ReplayError, match="endpoint map"):
        replay_html.build_document(payload, _branch_payload())
