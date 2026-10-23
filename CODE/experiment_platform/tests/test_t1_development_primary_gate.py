from __future__ import annotations

import hashlib
import yaml
import json
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


def test_plan_only_contract_is_rejected_before_development_outputs_are_created(
        tmp_path):
    contract = t1_suite.REPO_ROOT / \
        "CODE/work/WP-T1-COMPLETE/contract_dev_c.yaml"
    out_dir = tmp_path / "development-run"

    with pytest.raises(RuntimeError, match="runtime readiness gate"):
        t1_development.execute(contract, out_dir,
                               run_id="run-not-started",
                               release_id="a" * 40 + "-" + "b" * 64)

    assert not out_dir.exists()


def test_suite_run_rejects_plan_only_bundle_before_simulator_or_run_dir(
        tmp_path, monkeypatch):
    contract = tmp_path / "plan-only.yaml"
    contract.write_text(yaml.safe_dump({
        "design_readiness": {
            "status": "PLAN_ONLY_NOT_RELEASE_READY",
            "runtime_gate_implemented": False,
        },
    }), encoding="utf-8")
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "bundle.json").write_text(json.dumps({
        "contract_path": str(contract),
        "tiers": {"b_dev": ["cell-1"]},
        "cells": [{"cell_id": "cell-1", "group": "dev",
                   "driver": "CODE.experiment_platform.t1_tasks",
                   "args": []}],
        "budgets": dict(t1_suite.DEFAULT_BUDGETS),
    }), encoding="utf-8")
    monkeypatch.setattr(t1_suite, "validate_bundle",
                        lambda _bundle: {"valid": True})
    monkeypatch.setattr(
        t1_suite, "_execute_cell",
        lambda *_args, **_kwargs: pytest.fail("simulator must not start"))
    out_dir = tmp_path / "run"

    with pytest.raises(t1_suite.SuiteError,
                       match="runtime readiness gate"):
        t1_suite.run_bundle(bundle_dir, "b_dev", out_dir)

    assert not out_dir.exists()


def test_suite_resume_rechecks_plan_only_runtime_gate_before_retry(
        tmp_path, monkeypatch):
    contract = tmp_path / "plan-only.yaml"
    contract.write_text(yaml.safe_dump({
        "design_readiness": {
            "status": "PLAN_ONLY_NOT_RELEASE_READY",
            "runtime_gate_implemented": False,
        },
    }), encoding="utf-8")
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "bundle.json").write_text(json.dumps({
        "contract_path": str(contract),
    }), encoding="utf-8")
    run_dir = tmp_path / "existing-run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({
        "bundle_dir": str(bundle_dir), "tier": "b_dev",
        "status": "INCOMPLETE", "cells": [],
    }), encoding="utf-8")
    monkeypatch.setattr(t1_suite, "validate_bundle",
                        lambda _bundle: {"valid": True})
    monkeypatch.setattr(
        t1_suite, "_execute_cell",
        lambda *_args, **_kwargs: pytest.fail("simulator must not retry"))

    with pytest.raises(t1_suite.SuiteError,
                       match="runtime readiness gate"):
        t1_suite.resume_run(run_dir)


def test_cost_probe_gate_requires_one_exact_seed7_four_arm_cell(tmp_path):
    from CODE.experiment_platform import t1_suite as suite

    cell_id = "b-native-low-cost-network-seed-7"
    repo = suite.REPO_ROOT
    profile = yaml.safe_load((repo / "CODE/leo_sim/profiles/"
                              "t1_population_region_cost_smoke.yaml"
                              ).read_text(encoding="utf-8"))
    profile["scenario"]["seed"] = 7
    profile_path = tmp_path / "profile-seed-7.yaml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False),
                            encoding="utf-8")
    cell = {
        "cell_id": cell_id,
        "group": "b_round",
        "driver": "CODE.experiment_platform.t1_tasks",
        "args": ["--task", "network_alignment", "--config",
                 str(profile_path), "--arms",
                 "stale,now,common,candidate"],
        "seed": 7,
    }
    cell["input"] = suite._cell_input_binding(cell)
    cell_sha = suite._cell_input_sha256(cell["input"])
    auth = {
        "stage": "cost_probe",
        "tier": "b_dev",
        "cell_ids": [cell_id],
        "max_selected_cells": 1,
        "expected_simulator_calls": 4,
        "max_simulator_calls": 4,
        "allow_append": False,
        "task": "network_alignment",
        "seed": 7,
        "execution_chain_sha256": "c" * 64,
        "cell_input_sha256": {cell_id: cell_sha},
    }
    contract = {"design_readiness": {
        "status": "COST_PROBE_READY",
        "runtime_gate_implemented": True,
        "runtime_authorization": auth,
    }}
    bundle = {"execution_chain": {"combined_sha256": "c" * 64}}

    assert suite.require_runtime_ready_contract(contract)["status"] == \
        "COST_PROBE_READY"
    suite.enforce_runtime_stage(
        contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
        append=False, cells={cell_id: cell}, estimated_calls=4)
    with pytest.raises(suite.SuiteError, match="allowlist"):
        suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev",
            selected_cell_ids=[cell_id, "b-other"], append=False,
            cells={cell_id: cell}, estimated_calls=4)
    with pytest.raises(suite.SuiteError, match="append"):
        suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=True, cells={cell_id: cell}, estimated_calls=4)
    with pytest.raises(suite.SuiteError, match="simulator-call"):
        suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=False, cells={cell_id: cell}, estimated_calls=5)
    with pytest.raises(suite.SuiteError, match="cell seed metadata"):
        bad_cell = dict(cell, seed=11)
        suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=False, cells={cell_id: bad_cell}, estimated_calls=4)


def test_cost_probe_accepts_only_the_fixed_first_call_diagnostic():
    diagnostic = {
        "mode": "first_kernel_call_cprofile",
        "max_simulator_calls": 1,
        "maximum_cell_wall_s": 45,
        "profile_duration_s": 30,
    }
    authorization = {
        "stage": "cost_probe", "tier": "b_dev",
        "cell_ids": ["cell"], "max_selected_cells": 1,
        "expected_simulator_calls": 4, "max_simulator_calls": 4,
        "allow_append": False, "task": "network_alignment", "seed": 7,
        "execution_chain_sha256": "c" * 64,
        "cell_input_sha256": {"cell": "d" * 64},
        "diagnostic": diagnostic,
    }
    contract = {"design_readiness": {
        "status": "COST_PROBE_READY", "runtime_gate_implemented": True,
        "runtime_authorization": authorization,
    }}

    assert t1_suite.require_runtime_ready_contract(contract)["status"] == \
        "COST_PROBE_READY"
    for key, value in (("max_simulator_calls", 2),
                       ("maximum_cell_wall_s", 120),
                       ("profile_duration_s", 31),
                       ("mode", "arbitrary_profiler")):
        malformed = json.loads(json.dumps(contract))
        malformed["design_readiness"]["runtime_authorization"][
            "diagnostic"][key] = value
        with pytest.raises(t1_suite.SuiteError,
                           match="diagnostic|authorization"):
            t1_suite.require_runtime_ready_contract(malformed)


def _main_with_summary(monkeypatch, capsys, summary):
    monkeypatch.setattr(t1_development, "execute",
                        lambda *_args, **_kwargs: summary)
    release_id = "a" * 40 + "-" + "b" * 64
    code = t1_development.main([
        "--contract", "contract.yaml", "--out", "out",
        "--run-id", "probe-run", "--release-id", release_id,
    ])
    output = capsys.readouterr().out
    assert "Traceback" not in output
    return code, output


def test_cost_probe_timeout_main_prints_probe_summary_and_returns_nonzero(
        monkeypatch, capsys):
    summary = {
        "runtime_stage": "COST_PROBE_READY",
        "probe_run_status": "FAILED_CELLS",
        "probe_run_counts": {"timeout": 1, "ok": 0},
        "simulator_call_accounting": {
            "started": 1, "timed_out": 1,
            "simulator_wall_s": 116.074384128,
        },
    }

    code, output = _main_with_summary(monkeypatch, capsys, summary)

    assert code == 3
    assert json.loads(output) == summary
    assert '"probe_run_status": "FAILED_CELLS"' in output


def test_cost_probe_main_is_green_only_for_exact_ok_probe_status(
        monkeypatch, capsys):
    ok_summary = {
        "runtime_stage": "COST_PROBE_READY",
        "probe_run_status": "ok",
        "probe_run_counts": {"timeout": 0, "ok": 1},
    }
    failed_summary = {
        "runtime_stage": "COST_PROBE_READY",
        "probe_run_status": "SMOKE_FAILED",
        "run_status": "ok",
        "report_status": "ok",
    }

    ok_code, ok_output = _main_with_summary(monkeypatch, capsys, ok_summary)
    failed_code, failed_output = _main_with_summary(
        monkeypatch, capsys, failed_summary)

    assert ok_code == 0
    assert json.loads(ok_output) == ok_summary
    assert failed_code == 3
    assert json.loads(failed_output) == failed_summary


def test_cost_probe_summary_missing_its_status_fails_closed(
        monkeypatch, capsys):
    summary = {
        "runtime_stage": "COST_PROBE_READY",
        "run_status": "ok",
        "report_status": "ok",
    }

    code, output = _main_with_summary(monkeypatch, capsys, summary)

    assert code == 3
    assert json.loads(output) == summary


def test_standard_development_main_keeps_legacy_summary_contract(
        monkeypatch, capsys):
    ok_code, ok_output = _main_with_summary(
        monkeypatch, capsys,
        {"run_status": "ok", "report_status": "ok"})
    partial_code, partial_output = _main_with_summary(
        monkeypatch, capsys, {"run_status": "ok"})

    assert ok_code == 0
    assert json.loads(ok_output) == {
        "run_status": "ok", "report_status": "ok"}
    assert partial_code == 3
    assert json.loads(partial_output) == {"run_status": "ok"}


def test_suite_run_cost_probe_rejects_unlisted_selection_before_run_dir(
        tmp_path, monkeypatch):
    cell_id = "b-native-low-cost-network-seed-7"
    cell = {
        "cell_id": cell_id,
        "group": "b_round",
        "driver": "CODE.experiment_platform.t1_tasks",
        "args": ["--task", "network_alignment", "--seed", "7",
                 "--arms", "stale,current,common,candidate"],
        "input": {"config_sha256": "a" * 64,
                  "driver_sha256": "b" * 64, "files": {}},
    }
    cell_sha = t1_suite._cell_input_sha256(cell["input"])
    contract = {
        "design_readiness": {
            "status": "COST_PROBE_READY",
            "runtime_gate_implemented": True,
            "runtime_authorization": {
                "stage": "cost_probe", "tier": "b_dev",
                "cell_ids": [cell_id], "max_selected_cells": 1,
                "expected_simulator_calls": 4,
                "max_simulator_calls": 4, "allow_append": False,
                "task": "network_alignment", "seed": 7,
                "execution_chain_sha256": "c" * 64,
                "cell_input_sha256": {cell_id: cell_sha},
            },
        },
    }
    contract_path = tmp_path / "cost-probe.yaml"
    contract_path.write_text(yaml.safe_dump(contract), encoding="utf-8")
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle = {
        "contract_path": str(contract_path),
        "contract_sha256": hashlib.sha256(
            contract_path.read_bytes()).hexdigest(),
        "execution_chain": {"combined_sha256": "c" * 64},
        "tiers": {"b_dev": [cell_id]},
        "cells": [cell],
        "budgets": {**t1_suite.DEFAULT_BUDGETS,
                    "simulator_call_budget": 4},
    }
    (bundle_dir / "bundle.json").write_text(
        json.dumps(bundle), encoding="utf-8")
    monkeypatch.setattr(t1_suite, "validate_bundle",
                        lambda _bundle: {"valid": True})
    monkeypatch.setattr(
        t1_suite, "_execute_cell",
        lambda *_args, **_kwargs: pytest.fail("simulator must not start"))
    out_dir = tmp_path / "run"

    with pytest.raises(t1_suite.SuiteError, match="allowlist"):
        t1_suite.run_bundle(bundle_dir, "b_dev", out_dir, cell_ids=[])

    assert not out_dir.exists()


def test_b_contract_compiles_the_minimum_frozen_matrix_and_common_d(tmp_path):
    contract = yaml.safe_load(CONTRACT_B.read_text(encoding="utf-8"))
    cells = t1_suite._b_cells(contract, tmp_path)
    estimate = t1_suite.estimate_bundle_cost({"cells": cells})
    by_id = {cell["cell_id"]: cell for cell in cells}

    assert len(cells) == 8
    assert estimate["simulator_calls"] == 52
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
    pilot_cells = [by_id[cell_id]
                   for cell_id in contract["pilot_cell_ids"]]
    assert len(pilot_cells) == 4
    assert t1_suite.estimate_bundle_cost({"cells": pilot_cells})[
        "simulator_calls"] == 34
    assert "b-steady_multi_od_negative_control_b-execution-modes-seed-7" \
        not in by_id
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
