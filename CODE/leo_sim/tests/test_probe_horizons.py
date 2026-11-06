"""Multi-horizon rows must be sampled per BASE row, not per expanded row."""
from __future__ import annotations

import json

from CODE.leo_sim.target_probe import TargetProbe


def test_stride_is_applied_before_horizon_expansion(tmp_path):
    probe = TargetProbe(None, tmp_path / "probe.jsonl", stride=2,
                        horizons=(0.01, 0.05))
    rows = []
    for i in range(4):
        rows.append({"eval_target_s": 1.0 + i, "pid": i, "predicted_bits": 1})
    probe.enqueue(rows)
    targets = sorted(row["eval_target_s"] for row in probe.pending)
    # stride=2 keeps the 2nd and 4th base rows (targets 2.0 and 4.0);
    # each expands to 0/+10/+50 ms
    assert len(probe.pending) == 6
    assert targets == [2.0, 2.01, 2.05, 4.0, 4.01, 4.05]
    offsets = sorted({row["horizon_offset_s"] for row in probe.pending})
    assert offsets == [0.0, 0.01, 0.05]


def test_probe_identity_is_attached_to_base_rows(tmp_path):
    probe = TargetProbe(None, tmp_path / "probe.jsonl",
                        identity={"run_id": "run-1",
                                  "config_sha256": "a" * 64})
    probe.enqueue([{"eval_target_s": 1.0, "pid": 0,
                    "predicted_bits": 1}])
    assert probe.pending[0]["run_id"] == "run-1"
    assert probe.pending[0]["config_sha256"] == "a" * 64
