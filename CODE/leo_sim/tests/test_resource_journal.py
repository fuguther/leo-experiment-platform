"""Unit tests for the audit-only resource journal."""
from __future__ import annotations

import json
from pathlib import Path

from CODE.leo_sim.resource_journal import ResourceJournal


def test_queue_state_sampling_and_loss(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = ResourceJournal(path, stride=2)
    for i in range(5):
        journal.append_queue_state({"milestone": "queue_state", "at": i,
                                    "resource_id": "isl:1:2", "sat": 1,
                                    "peer": 2, "direction": "N",
                                    "generation": 3, "queued_data_bits": 10,
                                    "queued_control_bits": 0,
                                    "queued_bits": 10, "in_service_bits": 5,
                                    "in_service": True, "event": "enqueue",
                                    "outcome": None})
    journal.record_loss(pkt=type("P", (), {"pid": 7, "bits": 100})(),
                        fate="ISL_QUEUE_OVERFLOW", sat=1, peer=2,
                        hop_index=4, decision_id=9, at=1.5)
    summary = journal.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    queue_rows = [r for r in rows if r["kind"] == "queue_state"]
    loss_rows = [r for r in rows if r["kind"] == "loss"]
    assert summary["seen_queue_states"] == 5
    assert len(queue_rows) == 2          # 1-in-2 sampling
    assert len(loss_rows) == 1           # losses are never sampled out
    assert loss_rows[0]["fate"] == "ISL_QUEUE_OVERFLOW"
    assert summary["audit_only"] is True
    assert summary["feeds_online_policy"] is False
