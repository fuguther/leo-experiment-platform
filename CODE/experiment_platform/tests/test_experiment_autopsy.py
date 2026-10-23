"""Unit tests for the experiment autopsy aggregator (synthetic artifacts)."""
from __future__ import annotations

import json
from pathlib import Path

from CODE.experiment_platform import experiment_autopsy as ea


def _summary(tmp_path: Path):
    document = {
        "deadline": {"deadline_s": 0.15},
        "deadline_binding": {"binding": True},
        "arms": [{
            "arm": "stale",
            "outcome": {
                "offered": 10, "admitted": 10, "delivered": 9,
                "delivered_bits": 9000,
                "fate_counts": {"DELIVERED": 9, "NO_ROUTE": 1},
                "e2e_mean_s": 0.05, "e2e_p50_s": 0.04, "e2e_p90_s": 0.08,
                "e2e_p95_s": 0.09, "e2e_p99_s": 0.1,
            },
            "network_outcome": {
                "delivery_ratio": {"by_packets": 0.9},
                "terminal_loss": {"by_fate": {"NO_ROUTE": 1}, "packets": 1},
                "censoring": {"packets": 0},
                "queue": {"area_bits_s": {"by_area": {
                    "isl_data": 90.0, "isl_ctrl": 10.0, "holding": 0.0,
                    "uplink": 0.0, "downlink": 0.0}}},
            },
            "control": {"bits": {"offered": 900, "delivered": 800,
                                 "in_system": 100, "terminal_loss": 0}},
            "request_rate_per_satellite": {"max": 30.0, "mean": 10.0},
            "congestion_metrics": {
                "links": {"isl:0:1": {"utilization": 0.1},
                          "isl:1:2": {"utilization": 0.3}},
                "packets": {"1": {"delivered_at": 0.2, "e2e_s": 0.05,
                                  "prop_s": 0.03, "tx_s": 0.001,
                                  "queue_wait_s": 0.01,
                                  "total_queue_wait_s": 0.015,
                                  "access_wait_s": 0.002,
                                  "holding_wait_s": 0.001}},
            },
            "time_alignment_audit": {
                "decisions_in_action_log": 100, "fallbacks": 10,
                "decisions_where_the_arm_reordered_candidates": 40},
            "routing_audit_log": {"decision_records_retained": 50,
                                  "decision_records_cap": 100,
                                  "decision_coverage_fraction": 0.5},
        }],
    }
    path = tmp_path / "summary.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_summary_block_and_verdicts(tmp_path):
    block = ea.summary_block(str(_summary(tmp_path)))
    assert block["delivery"] == 0.9
    assert block["fates"]["NO_ROUTE"] == 1
    assert block["util_max"] == 0.3
    assert abs(block["control_overhead"] - 0.1) < 1e-9
    assert block["fallback_pct"] == 10.0
    lines = ea.verdicts(block, None, {"mae_bits": 123, "bias_bits": -1,
                                      "rmse_bits": 456,
                                      "pred_zero_truth_positive_pct": 12.5})
    text = "\n".join(lines)
    assert "时间预算" in text and "排队 20.0%" in text
    assert "预测器" in text


def test_probe_block(tmp_path):
    path = tmp_path / "probe.jsonl"
    rows = [
        {"predicted_bits": 100, "truth": {"work_ahead_bits": 80}},
        {"predicted_bits": 0, "truth": {"work_ahead_bits": 50}},
        {"predicted_bits": 10, "truth": None,
         "missing_reason": "no_prediction"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    block = ea.probe_block(str(path))
    assert block["rows"] == 3
    assert block["labeled"] == 2
    assert block["bias_bits"] == -15
    assert block["pred_zero_truth_positive_pct"] == 50.0
