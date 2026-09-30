from __future__ import annotations

import yaml
import pytest

from CODE.experiment_platform import t1_development, t1_suite


CONTRACT_B = (t1_suite.REPO_ROOT /
              "CODE/work/WP-T1-COMPLETE/contract_dev_b.yaml")


def _row(label, *, trace="a" * 64, loss=0.25, pids=(1, 2), window=(5, 20),
         deadline=30.0, loss_status="COMPUTED"):
    packets = [{
        "pid": pid,
        "trace_sha256": trace,
        "emit_time_s": float(10 + pid),
        "bits": 1000,
        "observation_end_s": 50.0,
        "fate": "DELIVERED",
        "delivery_record_present": True,
        "delivery_time_s": float(10 + pid + deadline * loss),
        "terminal_reason": None,
        "censor_reason": None,
        "in_population": True,
        "deadline_loss": {"status": "COMPUTED", "deadline_s": deadline,
                          "value": loss},
    } for pid in pids]
    return {
        "label": label,
        "network_outcome": {
            "window": {"status": "COMPUTED", "start_s": window[0],
                       "end_s": window[1]},
            "packet_outcomes": packets,
            "deadline_primary_loss": {
                "status": loss_status,
                "deadline_s": deadline,
                "packets": len(pids),
                "exact_packets": len(pids) if loss_status == "COMPUTED" else 1,
                "interval_censored": 0 if loss_status == "COMPUTED" else 1,
                "not_computable_packets": 0,
                "value": loss if loss_status == "COMPUTED" else None,
            },
        },
    }


def test_primary_outcome_gate_accepts_exactly_paired_fixed_d_rows():
    labels = ["stale", "now", "common", "candidate"]
    rows = {label: _row(label) for label in labels}

    gate = t1_development._primary_rows_check(rows, labels, 30.0, (5.0, 20.0))

    assert gate["passed"] is True
    assert gate["same_trace"] is True
    assert gate["same_packet_ids"] is True
    assert gate["same_population_ids"] is True


def test_primary_outcome_gate_rejects_wrong_deadline_window_or_censoring():
    labels = ["per_packet", "per_flow", "precomputed", "async_point",
              "async_window"]
    rows = {label: _row(label) for label in labels}
    rows["async_window"] = _row("async_window", deadline=20.0)

    wrong_deadline = t1_development._primary_rows_check(
        rows, labels, 30.0, (5.0, 20.0))
    rows["async_window"] = _row("async_window", window=(5.0, 40.0))
    wrong_window = t1_development._primary_rows_check(
        rows, labels, 30.0, (5.0, 20.0))
    rows["async_window"] = _row("async_window", loss_status="PARTIAL_BOUNDS")
    censored = t1_development._primary_rows_check(
        rows, labels, 30.0, (5.0, 20.0))

    assert wrong_deadline["passed"] is False
    assert wrong_window["passed"] is False
    assert censored["passed"] is False


def test_primary_outcome_gate_recomputes_summary_from_packet_losses():
    labels = ["stale", "now", "common", "candidate"]
    rows = {label: _row(label) for label in labels}
    rows["candidate"]["network_outcome"]["deadline_primary_loss"][
        "value"] = 0.30

    gate = t1_development._primary_rows_check(
        rows, labels, 30.0, (5.0, 20.0))

    assert gate["passed"] is False
    assert gate["checks"][-1]["summary_matches_packets"] is False


def test_primary_outcome_gate_requires_explicit_delivery_record_fields():
    labels = ["stale", "now", "common", "candidate"]
    rows = {label: _row(label) for label in labels}
    del rows["candidate"]["network_outcome"]["packet_outcomes"][0][
        "delivery_time_s"]

    gate = t1_development._primary_rows_check(
        rows, labels, 30.0, (5.0, 20.0))

    assert gate["passed"] is False
    assert gate["checks"][-1]["passed"] is False


def test_branch_primary_gate_recomputes_one_common_minus_candidate_block():
    trace = "b" * 64
    branch_rows = [
        {"decision_id": 8, "t_decision_start": 10.0,
         "regret": {"common": 0.2, "candidate": 0.1}},
        {"decision_id": 29, "t_decision_start": 15.0,
         "regret": {"common": 0.4, "candidate": 0.3}},
    ]
    result = {
        "status": "ok",
        "document": {
            "status": "ok",
            "deadline": {"deadline_s": 30.0},
            "measurement_window": {"start_s": 5.0, "end_s": 20.0},
            "source": {"trace_sha256": trace},
            "sampling": {"sampled": 2, "decisions": [8, 29]},
            "eligibility": {"eligible": 12},
            "branches": branch_rows,
            "block": {
                "status": "ok",
                "arms": {
                    "common": {"mean_regret": 0.3},
                    "candidate": {"mean_regret": 0.2},
                },
            },
        },
    }

    gate = t1_development._branch_primary_check(
        result, 30.0, (5.0, 20.0), min_sampled=2, min_eligible=10)

    assert gate["passed"] is True
    assert gate["independent_unit"] == "one scenario x trace x seed block"
    assert gate["common_minus_candidate_regret"] == pytest.approx(0.1)
    assert gate["checks"]["eligible_count_sufficient"] is True

    result["document"]["sampling"]["sampled"] = 5
    mismatched_count = t1_development._branch_primary_check(
        result, 30.0, (5.0, 20.0), min_sampled=2, min_eligible=10)
    assert mismatched_count["passed"] is False


def test_branch_primary_gate_rejects_missing_branch_outcome_or_wrong_window():
    result = {
        "status": "ok",
        "document": {
            "status": "ok",
            "deadline": {"deadline_s": 30.0},
            "measurement_window": {"start_s": 5.0, "end_s": 40.0},
            "source": {"trace_sha256": "c" * 64},
            "sampling": {"sampled": 1, "decisions": [1]},
            "eligibility": {"eligible": 10},
            "branches": [{"decision_id": 1, "t_decision_start": 12.0,
                          "regret": {"common": 0.1, "candidate": None}}],
            "block": {
                "status": "ok",
                "arms": {
                    "common": {"mean_regret": 0.1},
                    "candidate": {"mean_regret": 0.0},
                },
            },
        },
    }

    gate = t1_development._branch_primary_check(
        result, 30.0, (5.0, 20.0), min_sampled=1, min_eligible=10)

    assert gate["passed"] is False
    assert gate["checks"]["window_matches"] is False
    assert gate["checks"]["all_sampled_regrets_computed"] is False


def test_b_contract_compiles_the_minimum_frozen_matrix_and_common_d(tmp_path):
    contract = yaml.safe_load(CONTRACT_B.read_text(encoding="utf-8"))
    cells = t1_suite._b_cells(contract, tmp_path)
    estimate = t1_suite.estimate_bundle_cost({"cells": cells})
    by_id = {cell["cell_id"]: cell for cell in cells}

    assert len(cells) == 9
    assert estimate["simulator_calls"] == 57
    assert not any("benchmark" in cell["driver"] for cell in cells)
    for cell in cells:
        args = cell["args"]
        task = args[args.index("--task") + 1]
        assert "--deadline-s" in args
        assert args[args.index("--deadline-s") + 1] == "30.0"
        assert args[args.index("--window-start") + 1] == "5.0"
        assert args[args.index("--window-end") + 1] == "20.0"
        if task == "branch_alignment":
            assert args[args.index("--max-branches") + 1] == "4"
    assert by_id[contract["pilot_cell_ids"][0]]["cell_id"] == \
        "b-steady_multi_od_negative_control_b-network-seed-7"
    branch = by_id["b-temporal_multi_od_transfer_b-branch-seed-11"]
    network = by_id["b-temporal_multi_od_transfer_b-network-seed-11"]
    assert "--capture-replay" in branch["args"]
    assert "--capture-replay" in network["args"]
    assert "--capture-replay" not in by_id[
        "b-temporal_multi_od_transfer_b-network-seed-23"]["args"]


def test_pilot_gate_treats_static_branch_calls_as_upper_bound(tmp_path, monkeypatch):
    cell = {"cell_id": "branch", "driver": "branch_alignment"}
    run_doc = {
        "cells": [{"cell_id": "branch", "status": "ok", "wall_s": 3.0,
                   "simulator_calls": {
                       "started": 9, "ended": 9, "failed": 0,
                       "timed_out": 0, "interrupted": 0,
                       "unresolved": 0, "simulator_wall_s": 6.0}}],
        "simulator_call_accounting": {"simulator_wall_s": 6.0},
    }
    monkeypatch.setattr(t1_suite, "estimate_bundle_cost",
                        lambda bundle: {"simulator_calls": 10})
    monkeypatch.setattr(t1_development, "_primary_estimand_check",
                        lambda *args: {"passed": True})
    monkeypatch.setattr(t1_development.t1_admission, "evaluate_run",
                        lambda *args: {"summary": {"admitted": 1},
                                       "competitive_scenarios": [
                                           {"verdict": "ADMITTED"}]})

    gate = t1_development._evaluate_pilot_budget(
        {"cells": [cell], "tiers": {"b_dev": ["branch"]}}, ["branch"],
        run_doc, {"cell_wall_s": 120, "simulator_call_budget": 10,
                  "total_wall_s": 3600}, tmp_path, {})

    assert gate["passed"] is True
    assert gate["checks"][0]["calls_upper"] == 10
    assert gate["checks"][0]["calls_started"] == 9
    assert gate["checks"][0]["call_ledger_complete"] is True
