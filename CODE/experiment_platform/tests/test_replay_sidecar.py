from __future__ import annotations

import json

import pytest

from CODE.experiment_platform import replay_sidecar


def _document():
    queue_a = {"milestone": "queue_state", "at": 1.0}
    queue_b = {"milestone": "queue_state", "at": 3.0}
    arm = {
        "arm": "stale",
        "scope": {"decision_requests": 3, "forward_decisions": 2},
        "replay": {
            "captured": True, "arm": "stale",
            "decision_rows": [{"kind": "forward", "decision_id": 1},
                              {"kind": "forward", "decision_id": 2},
                              {"kind": "deliver", "decision_id": 3}],
            "timeline_rows": [queue_a, {"milestone": "decision_attempt", "at": 2.0},
                              queue_b],
            "packet_events": [{"kind": "packet_emitted", "pid": 1}],
            "link_service_windows": [], "link_available_windows": [],
            "queue_state_events": [queue_a, queue_b],
            "topology_trace": [], "handover_events": [],
            "fates": {"1": "DELIVERED"},
            "deliveries": {"1": {"at": 2.0}},
            "counts": {"offered": 1, "admitted": 1, "delivered": 1},
        },
    }
    return {"document": {"arms": [arm]}}


def test_write_sidecar_replaces_the_replay_with_a_verified_reference(tmp_path):
    result = tmp_path / "result.json"
    document = _document()
    manifest = replay_sidecar.write_sidecar(document, result)

    target = tmp_path / "result-replay.jsonl"
    assert target.is_file()
    assert manifest["path"] == target.name
    reference = document["document"]["arms"][0]["replay"]
    assert reference["captured"] is True
    assert reference["sidecar"] == target.name
    assert reference["sha256"] == manifest["sha256"]
    assert reference["streams"]["decision_rows"] == 3
    assert reference["streams"]["timeline_rows"] == 3
    assert reference["mappings"]["fates"] == 1
    assert replay_sidecar.verify_reference(reference, result,
                                           expected_arm="stale") == []


def test_sidecar_keeps_every_row_and_mapping(tmp_path):
    result = tmp_path / "result.json"
    replay_sidecar.write_sidecar(_document(), result)
    bucket = replay_sidecar.read_sidecar(tmp_path / "result-replay.jsonl")
    assert bucket["stale"]["declared"]["decision_rows"] == 3
    assert len(bucket["stale"]["streams"]["decision_rows"]) == 3
    assert bucket["stale"]["mappings"]["fates"] == {"1": "DELIVERED"}
    rows = list(replay_sidecar.iter_sidecar(tmp_path / "result-replay.jsonl",
                                            arm="stale", stream="decision_rows"))
    assert [row[2]["decision_id"] for row in rows] == [1, 2, 3]


def test_stream_facts_recomputes_gate_evidence_without_materializing(tmp_path):
    result = tmp_path / "result.json"
    replay_sidecar.write_sidecar(_document(), result)
    facts = replay_sidecar.stream_facts(tmp_path / "result-replay.jsonl", "stale")
    assert facts["counts"]["decision_rows"] == 3
    assert facts["forward_decisions"] == 2
    assert facts["milestones"]["queue_state"] == 2
    assert facts["routing_attempts"] == 1
    assert facts["fates"] == {"1": "DELIVERED"}
    assert facts["queue_subset_count"] == 2
    assert facts["queue_subset_digest"] == facts["timeline_subset_digest"]


def test_routing_audit_lists_are_extracted_to_the_sidecar(tmp_path):
    """The audit lists embed one observation per attempt; they must leave."""
    document = _document()
    arm = document["document"]["arms"][0]
    arm["routing_audit_log"] = {
        "decision_record_count": 1, "decision_records": [{"decision_id": 1}],
        "attempt_record_count": 2,
        "attempt_records": [{"attempt": 1}, {"attempt": 2}],
    }
    result = tmp_path / "result.json"
    replay_sidecar.write_sidecar(document, result)

    audit = arm["routing_audit_log"]
    assert audit["decision_records"] == []
    assert audit["attempt_records"] == []
    reference = arm["replay"]
    assert reference["index"]["routing_decision_records"]["count"] == 1
    assert reference["index"]["routing_attempt_records"]["count"] == 2
    side = replay_sidecar.sidecar_of(result, reference)
    assert [row["attempt"] for row in replay_sidecar.resolve(
        side, reference, "routing_attempt_records")] == [1, 2]


def test_index_resolvers_return_exactly_the_recorded_streams(tmp_path):
    """Acceptance and reporting read through these; they must not rescan."""
    result = tmp_path / "result.json"
    document = _document()
    replay_sidecar.write_sidecar(document, result)
    reference = document["document"]["arms"][0]["replay"]
    side = replay_sidecar.sidecar_of(result, reference)

    assert [row["decision_id"] for row in
            replay_sidecar.resolve(side, reference, "decision_rows")] == [1, 2, 3]
    assert [row["milestone"] for row in
            replay_sidecar.resolve(side, reference, "timeline_rows")] == [
        "queue_state", "decision_attempt", "queue_state"]
    assert replay_sidecar.resolve_mapping(side, reference, "fates") == {
        "1": "DELIVERED"}
    assert replay_sidecar.resolve_mapping(side, reference, "counts") == {
        "offered": 1, "admitted": 1, "delivered": 1}
    assert [row["pid"] for row in
            replay_sidecar.resolve_iter(side, reference, "packet_events")] == [1]


def test_tampering_and_misdeclared_counts_are_reported(tmp_path):
    result = tmp_path / "result.json"
    document = _document()
    replay_sidecar.write_sidecar(document, result)
    reference = document["document"]["arms"][0]["replay"]
    target = tmp_path / "result-replay.jsonl"
    original = target.read_text(encoding="utf-8")
    target.write_text(original.replace("DELIVERED", "NO_ROUTE"), encoding="utf-8")
    issues = replay_sidecar.verify_reference(reference, result)
    assert any("sha256" in issue for issue in issues)
    target.write_text(original, encoding="utf-8")
    reference["streams"]["decision_rows"] = 4
    issues = replay_sidecar.verify_reference(reference, result)
    assert any("declared count" in issue for issue in issues)


def test_missing_sidecar_and_lossy_projection_are_refused(tmp_path):
    result = tmp_path / "result.json"
    document = _document()
    replay_sidecar.write_sidecar(document, result)
    reference = document["document"]["arms"][0]["replay"]
    (tmp_path / "result-replay.jsonl").unlink()
    assert any("missing" in issue for issue in
               replay_sidecar.verify_reference(reference, result))
    lossy = _document()
    lossy["document"]["arms"][0]["replay"]["analysis_projection"] = {}
    with pytest.raises(replay_sidecar.SidecarError):
        replay_sidecar.write_sidecar(lossy, tmp_path / "other.json")
