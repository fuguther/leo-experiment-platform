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
