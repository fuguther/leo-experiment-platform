"""Pure result-document fixtures for the structural full-replay gate.

These tests exercise only ``check_predicate``. They do not build or run a
simulation and they do not claim that all event semantics have been replayed.
"""
from __future__ import annotations

import pytest

from CODE.experiment_platform import t1_suite


def _result():
    queue_state = {"milestone": "queue_state", "at": 1.0, "link_id": "isl:1:2"}
    return {
        "status": "ok",
        "failed_units": 0,
        "task": "network_alignment",
        "document": {
            "status": "ok",
            "arms": [{
                "arm": "candidate",
                "scope": {
                    "packets_in_trace": 2,
                    "decision_requests": 1,
                    "forward_decisions": 1,
                    "satellites_that_decided": 1,
                },
                "outcome": {
                    "offered": 2,
                    "admitted": 2,
                    "delivered": 1,
                    "fate_counts": {
                        "DELIVERED": 1,
                        "DATA_DEADLINE_EXPIRED": 1,
                    },
                },
                "time_alignment_audit": {
                    "arms_seen_in_the_audit": ["candidate"],
                    "decisions_with_query_targets": 1,
                },
                "request_rate_per_satellite": {"max": 1.0},
                "action_log": {"count": 1},
                "routing_audit_log": {
                    "decision_record_count": 1,
                    "decision_records": [{}],
                    "attempt_record_count": 0,
                    "attempt_records": [],
                },
                "congestion_metrics": {
                    "links": {
                        "isl:1:2": {
                            "service_windows": 1,
                            "available_samples": 1,
                        },
                    },
                    "validation": {"ok": True, "errors": []},
                },
                "replay": {
                    "captured": True,
                    "arm": "candidate",
                    "decision_rows": [{"kind": "forward", "decision_id": 1}],
                    "timeline_rows": [queue_state],
                    "packet_events": [
                        {"kind": "packet_emitted", "pid": 10, "at": 0.0},
                        {"kind": "packet_emitted", "pid": 11, "at": 0.0},
                        {"kind": "satellite_ingress", "pid": 10, "at": 0.1},
                        {"kind": "satellite_ingress", "pid": 11, "at": 0.1},
                        {"kind": "delivered", "pid": 10, "at": 1.0},
                    ],
                    "link_service_windows": [{"pid": 10, "start": 0.2, "end": 0.3}],
                    "link_available_windows": [{
                        "link_id": "isl:1:2", "start": 0.0, "end": 1.0,
                    }],
                    "queue_state_events": [queue_state],
                    "topology_trace": [{
                        "at": 0.0,
                        "reason": "initial",
                        "num_satellites": 2,
                        "peer_by_satellite": {},
                    }],
                    "fates": {"10": "DELIVERED", "11": "DATA_DEADLINE_EXPIRED"},
                    "deliveries": {"10": {"delivered_at": 1.0, "path": [1, 2]}},
                    "handover_events": [],
                    "counts": {
                        "offered": 2,
                        "admitted": 2,
                        "delivered": 1,
                        "fate_counts": {
                            "DELIVERED": 1,
                            "DATA_DEADLINE_EXPIRED": 1,
                        },
                    },
                },
            }],
        },
    }


_PREDICATE = {
    "kind": "t1_task",
    "require": {
        "require_task": "network_alignment",
        "arms": ["candidate"],
        "require_full_replay": True,
        "min_decisions_per_arm": 0,
        "min_satellites": 0,
    },
}


def _replay_check(result):
    verdict = t1_suite.check_predicate(result, _PREDICATE)
    check = next(check for check in verdict["checks"]
                 if "full-network" in check["check"])
    return check["check"], check["passed"], check["observed"]


@pytest.mark.parametrize("field", [
    "decision_rows",
    "timeline_rows",
    "queue_state_events",
    "link_service_windows",
    "link_available_windows",
    "fates",
    "deliveries",
    "handover_events",
    "counts",
])
def test_full_replay_gate_rejects_a_missing_required_stream(field):
    result = _result()
    del result["document"]["arms"][0]["replay"][field]

    assert _replay_check(result)[1] is False


@pytest.mark.parametrize("field", [
    "decision_rows",
    "timeline_rows",
    "queue_state_events",
    "packet_events",
    "link_service_windows",
    "link_available_windows",
    "topology_trace",
    "handover_events",
])
def test_full_replay_gate_rejects_a_non_list_stream(field):
    result = _result()
    result["document"]["arms"][0]["replay"][field] = {}

    assert _replay_check(result)[1] is False


@pytest.mark.parametrize("field", [
    "decision_rows",
    "timeline_rows",
    "packet_events",
    "link_service_windows",
    "link_available_windows",
    "queue_state_events",
    "topology_trace",
    "handover_events",
])
def test_full_replay_gate_rejects_non_mapping_stream_rows(field):
    result = _result()
    result["document"]["arms"][0]["replay"][field] = [None]

    assert _replay_check(result)[1] is False


@pytest.mark.parametrize("mismatch", ["request_scope", "forward_audit"])
def test_full_replay_gate_reconciles_decision_rows_with_scope_and_audits(mismatch):
    result = _result()
    arm = result["document"]["arms"][0]
    if mismatch == "request_scope":
        arm["replay"]["decision_rows"].append(
            {"kind": "forward", "decision_id": 2})
    else:
        arm["scope"]["forward_decisions"] = 2

    assert _replay_check(result)[1] is False


def test_full_replay_gate_requires_queue_state_stream_to_equal_timeline_subset():
    result = _result()
    result["document"]["arms"][0]["replay"]["queue_state_events"] = []

    assert _replay_check(result)[1] is False


@pytest.mark.parametrize("stream", [
    "link_service_windows",
    "link_available_windows",
])
def test_full_replay_gate_reconciles_windows_with_congestion_audit(stream):
    result = _result()
    arm = result["document"]["arms"][0]
    arm["replay"][stream].append({"test": "extra"})

    assert _replay_check(result)[1] is False


@pytest.mark.parametrize("mismatch", [
    "delivery_record",
    "packet_emitted_event",
    "satellite_ingress_event",
    "delivered_event",
])
def test_full_replay_gate_reconciles_fates_deliveries_and_packet_event_counts(mismatch):
    result = _result()
    replay = result["document"]["arms"][0]["replay"]
    if mismatch == "delivery_record":
        replay["deliveries"].pop("10")
    else:
        event_kind = {
            "packet_emitted_event": "packet_emitted",
            "satellite_ingress_event": "satellite_ingress",
            "delivered_event": "delivered",
        }[mismatch]
        replay["packet_events"].remove(
            next(event for event in replay["packet_events"]
                 if event["kind"] == event_kind))

    assert _replay_check(result)[1] is False


def test_full_replay_gate_counts_unique_satellite_ingress_pids():
    result = _result()
    replay = result["document"]["arms"][0]["replay"]
    replay["packet_events"].append({
        "kind": "satellite_ingress", "pid": 10, "at": 0.2,
    })

    assert replay["counts"]["admitted"] == 2
    assert _replay_check(result)[1] is True


@pytest.mark.parametrize("kind,pid", [
    ("satellite_ingress", "invalid"),
    ("satellite_ingress", True),
    ("satellite_ingress", 0),
    ("satellite_ingress", 999),
    ("packet_emitted", 999),
    ("delivered", 999),
])
def test_full_replay_gate_rejects_invalid_or_unknown_packet_event_pid(kind, pid):
    result = _result()
    replay = result["document"]["arms"][0]["replay"]
    next(event for event in replay["packet_events"]
         if event["kind"] == kind)["pid"] = pid

    assert _replay_check(result)[1] is False


def test_full_replay_gate_rejects_duplicate_emission_pid_even_when_count_matches():
    result = _result()
    events = result["document"]["arms"][0]["replay"]["packet_events"]
    emitted = [event for event in events if event["kind"] == "packet_emitted"]
    emitted[1]["pid"] = emitted[0]["pid"]

    assert _replay_check(result)[1] is False


def test_full_replay_gate_rejects_duplicate_delivery_pid_even_when_count_matches():
    result = _result()
    arm = result["document"]["arms"][0]
    replay = arm["replay"]
    replay["fates"] = {"10": "DELIVERED", "11": "DELIVERED"}
    replay["deliveries"]["11"] = {"delivered_at": 2.0, "path": [2, 3]}
    replay["counts"].update(delivered=2, fate_counts={"DELIVERED": 2})
    arm["outcome"].update(delivered=2, fate_counts={"DELIVERED": 2})
    replay["packet_events"].append({
        "kind": "delivered", "pid": 10, "at": 2.0,
    })

    assert _replay_check(result)[1] is False


def test_full_replay_gate_matches_forward_row_kinds_to_scope():
    result = _result()
    result["document"]["arms"][0]["replay"]["decision_rows"][0]["kind"] = "hold"

    assert _replay_check(result)[1] is False


def test_full_replay_gate_accepts_zero_delivery_service_and_handover_counts():
    result = _result()
    arm = result["document"]["arms"][0]
    replay = arm["replay"]
    replay["packet_events"] = [
        {"kind": "packet_emitted", "pid": 10, "at": 0.0},
        {"kind": "packet_emitted", "pid": 11, "at": 0.0},
    ]
    replay["link_service_windows"] = []
    replay["fates"] = {
        "10": "DATA_DEADLINE_EXPIRED",
        "11": "DATA_DEADLINE_EXPIRED",
    }
    replay["deliveries"] = {}
    replay["handover_events"] = []
    replay["counts"] = {
        "offered": 2,
        "admitted": 0,
        "delivered": 0,
        "fate_counts": {"DATA_DEADLINE_EXPIRED": 2},
    }
    arm["outcome"].update(
        admitted=0,
        delivered=0,
        fate_counts={"DATA_DEADLINE_EXPIRED": 2},
    )
    arm["congestion_metrics"]["links"]["isl:1:2"].update(
        service_windows=0,
        available_samples=1,
    )

    assert _replay_check(result)[1] is True


def test_full_replay_gate_allows_empty_packet_event_stream_for_zero_offers():
    result = _result()
    arm = result["document"]["arms"][0]
    replay = arm["replay"]
    arm["scope"].update(
        packets_in_trace=0,
        decision_requests=0,
        forward_decisions=0,
        satellites_that_decided=0,
    )
    arm["outcome"].update(
        offered=0,
        admitted=0,
        delivered=0,
        fate_counts={},
    )
    arm["action_log"]["count"] = 0
    arm["routing_audit_log"].update(
        decision_record_count=0,
        decision_records=[],
    )
    replay.update(
        decision_rows=[],
        timeline_rows=[],
        packet_events=[],
        link_service_windows=[],
        link_available_windows=[],
        queue_state_events=[],
        fates={},
        deliveries={},
        handover_events=[],
        counts={"offered": 0, "admitted": 0, "delivered": 0,
                "fate_counts": {}},
    )
    arm["congestion_metrics"]["links"] = {}

    assert _replay_check(result)[1] is True


def test_replay_gate_reports_structural_scope_without_claiming_event_replay():
    result = _result()

    _name, passed, detail = _replay_check(result)

    assert passed is True
    assert "structural" in detail["validation_scope"]
    assert detail["full_event_recomputation"] is False
