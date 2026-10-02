"""Compile, validate, run and report the frozen T1 development matrix.

This wrapper is the single development-only entrypoint used inside an
immutable T1 release.  It cannot select the formal tier.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
from pathlib import Path

import yaml

from CODE.experiment_platform import (replay_html, t1_admission, t1_stats,
                                      t1_suite)


def _negative_control_smoke(cell, record, payload):
    """Outcome-independent A0 gate: all four real network arms must run."""
    checks = []

    def check(name, passed, value=None):
        checks.append({"name": name, "passed": bool(passed), "value": value})

    check("cell_predicate_ok", record.get("status") == "ok",
          record.get("status"))
    document = ((payload or {}).get("document") or {})
    arms = document.get("arms") or []
    names = [str(arm.get("arm")) for arm in arms]
    expected = ["stale", "now", "common", "candidate"]
    check("network_result_ok", document.get("status") == "ok",
          document.get("status"))
    check("all_four_arms_present", sorted(names) == sorted(expected), names)
    check("no_arm_failures", not document.get("failures"),
          document.get("failures"))
    source = document.get("source") or {}
    workload = source.get("synthetic_workload") or {}
    summary = workload.get("summary") or {}
    flows = summary.get("flows") or []
    check("six_directed_ods_in_frozen_workload",
          summary.get("unique_od_count") == 6 and len(flows) == 6
          and len({flow.get("id") for flow in flows}) == 6,
          {"unique_od_count": summary.get("unique_od_count"),
           "flow_rows": len(flows)})
    active = summary.get("active_od_counts_by_1s_bin") or {}
    check("multiple_ods_coactive_in_source_trace",
          any(isinstance(count, int) and count >= 2
              for count in active.values()),
          max(active.values(), default=0))
    for name in expected:
        row = next((arm for arm in arms if arm.get("arm") == name), {})
        scope, outcome = row.get("scope") or {}, row.get("outcome") or {}
        offered = outcome.get("offered")
        packets = scope.get("packets_in_trace")
        check(f"{name}_offered_packets_positive",
              isinstance(packets, int) and packets > 0, packets)
        check(f"{name}_forward_decisions_positive",
              isinstance(scope.get("forward_decisions"), int)
              and scope["forward_decisions"] > 0,
              scope.get("forward_decisions"))
        check(f"{name}_delivered_packets_positive",
              isinstance(outcome.get("delivered"), int)
              and outcome["delivered"] > 0, outcome.get("delivered"))
        delivered_bits = outcome.get("delivered_bits")
        check(f"{name}_delivered_bits_positive",
              isinstance(delivered_bits, int) and delivered_bits > 0,
              delivered_bits)
        goodput = outcome.get("goodput_bps_in_window")
        check(f"{name}_goodput_positive_when_computable",
              isinstance(goodput, (int, float))
              and not isinstance(goodput, bool)
              and math.isfinite(float(goodput)) and goodput > 0,
              goodput)
        outcome_document = row.get("outcome_document") or {}
        check(f"{name}_event_partition_exact",
              outcome_document.get("partition_exact") is True,
              outcome_document.get("partition_exact"))
        check(f"{name}_offered_count_matches_trace", offered == packets,
              {"offered": offered, "trace": packets})
    return {
        "schema": "t1-a0-smoke-gate/v1",
        "purpose": "prove the declared negative-control network simulation ran; no arm ranking or advantage criterion",
        "cell_id": cell.get("cell_id"),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _validate_a0_smoke_cell(contract, b_dev_cells):
    smoke_contract = contract.get("a0_smoke") or {}
    smoke_scenario = str(smoke_contract.get("scenario_id") or "")
    smoke_seed = smoke_contract.get("network_seed")
    smoke_specs = [spec for spec in
                   ((contract.get("b_round") or {}).get("scenarios") or [])
                   if str(spec.get("id")) == smoke_scenario
                   and spec.get("expectation") == "negative_control"]
    if not smoke_specs or not isinstance(smoke_seed, int) \
            or isinstance(smoke_seed, bool):
        raise RuntimeError(
            "development contract must bind a seeded negative-control "
            "network smoke in a0_smoke")
    smoke_spec = smoke_specs[0]
    network_seeds = [int(value) for value in
                     (smoke_spec.get("task_seeds") or {}).get(
                         "network_alignment", smoke_spec.get("seeds", []))]
    if ("network_alignment" not in (smoke_spec.get("tasks") or [])
            or smoke_seed not in network_seeds):
        raise RuntimeError(
            "a0_smoke must name a network_alignment seed in its negative "
            "control scenario")
    expected_smoke_id = (
        f"b-{smoke_scenario}-network-seed-{smoke_seed}")
    first = b_dev_cells[0] if b_dev_cells else {}
    args = list(first.get("args") or [])
    has_network_task = any(
        args[i] == "--task" and i + 1 < len(args)
        and args[i + 1] == "network_alignment"
        for i in range(len(args)))
    if (not b_dev_cells or first.get("cell_id") != expected_smoke_id
            or first.get("driver") != "CODE.experiment_platform.t1_tasks"
            or not has_network_task):
        observed = None if not b_dev_cells else first.get("cell_id")
        raise RuntimeError(
            f"the first b_dev cell must be the predeclared negative-control "
            f"network smoke ({expected_smoke_id}); observed {observed}")
    return expected_smoke_id


def _primary_rows_check(rows, expected_labels, deadline_s, population_window):
    """Check that a task carries one exact, paired deadline outcome per unit."""
    checks = []
    pids_by_label = {}
    trace_hashes = {}
    population_pids = {}
    for label in expected_labels:
        row = rows.get(label)
        network = (row or {}).get("network_outcome") or {}
        metric = network.get("deadline_primary_loss") or {}
        window = network.get("window") or {}
        packet_rows = network.get("packet_outcomes")
        try:
            window_start = float(window.get("start_s"))
            window_end = float(window.get("end_s"))
            expected_window = (window.get("status") == "COMPUTED"
                               and math.isfinite(window_start)
                               and math.isfinite(window_end)
                               and window_start == float(population_window[0])
                               and window_end == float(population_window[1]))
        except (TypeError, ValueError):
            window_start, window_end, expected_window = None, None, False
        packet_rows_ok = isinstance(packet_rows, list)
        if packet_rows_ok:
            packet_rows_ok = all(
                isinstance(packet, dict)
                and isinstance(packet.get("pid"), int)
                and not isinstance(packet.get("pid"), bool)
                and packet.get("pid") >= 0
                and isinstance(packet.get("trace_sha256"), str)
                and bool(packet.get("trace_sha256"))
                and packet.get("fate") is not None
                and isinstance(packet.get("observation_end_s"), (int, float))
                and not isinstance(packet.get("observation_end_s"), bool)
                and math.isfinite(float(packet.get("observation_end_s")))
                and isinstance(packet.get("bits"), int)
                and not isinstance(packet.get("bits"), bool)
                and packet.get("bits") > 0
                and isinstance(packet.get("in_population"), bool)
                and isinstance(packet.get("delivery_record_present"), bool)
                and "delivery_time_s" in packet
                and "terminal_reason" in packet
                and "censor_reason" in packet
                and isinstance(packet.get("emit_time_s"), (int, float))
                and not isinstance(packet.get("emit_time_s"), bool)
                and math.isfinite(float(packet.get("emit_time_s")))
                for packet in packet_rows)
        population = ([packet for packet in packet_rows
                       if packet.get("in_population") is True]
                      if packet_rows_ok else [])
        pids = sorted(packet.get("pid") for packet in packet_rows
                      if isinstance(packet, dict)
                      and isinstance(packet.get("pid"), int)) \
            if packet_rows_ok else []
        pop_pids = sorted(packet.get("pid") for packet in population)
        trace_set = sorted({packet.get("trace_sha256")
                            for packet in packet_rows
                            if isinstance(packet, dict)
                            and packet.get("trace_sha256")}) \
            if packet_rows_ok else []
        try:
            metric_deadline = float(metric.get("deadline_s"))
            raw_metric_packets = metric.get("packets")
            metric_packets = (int(raw_metric_packets)
                              if isinstance(raw_metric_packets, int)
                              and not isinstance(raw_metric_packets, bool)
                              else 0)
            metric_value = float(metric.get("value"))
            metric_numeric = (math.isfinite(metric_deadline)
                              and math.isfinite(metric_value)
                              and metric_packets > 0
                              and isinstance(raw_metric_packets, int)
                              and not isinstance(raw_metric_packets, bool))
        except (TypeError, ValueError, OverflowError):
            metric_deadline, metric_packets, metric_value = None, 0, None
            metric_numeric = False
        recomputed_mean = None
        population_membership_ok = False
        packet_losses_ok = False
        packet_loss_fields_match = False
        if packet_rows_ok and expected_window:
            delivery_rows_ok = all(
                (packet.get("fate") == "DELIVERED"
                 and packet.get("delivery_record_present") is True
                 and isinstance(packet.get("delivery_time_s"), (int, float))
                 and not isinstance(packet.get("delivery_time_s"), bool)
                 and math.isfinite(float(packet.get("delivery_time_s")))
                 and packet.get("terminal_reason") is None
                 and packet.get("censor_reason") is None)
                or (packet.get("fate") != "DELIVERED"
                    and packet.get("delivery_record_present") is False
                    and packet.get("delivery_time_s") is None
                    and ((packet.get("terminal_reason")
                          == packet.get("fate")
                          and packet.get("censor_reason") is None)
                         or (packet.get("terminal_reason") is None
                             and packet.get("censor_reason")
                             == "administrative_censoring_at_stop")))
                for packet in packet_rows)
            population_membership_ok = all(
                packet["in_population"] is (
                    float(population_window[0])
                    <= float(packet["emit_time_s"])
                    <= float(population_window[1]))
                for packet in packet_rows)
            deadline_losses = [packet.get("deadline_loss")
                               for packet in population]
            try:
                loss_values = []
                packet_loss_fields_match = True
                for packet, item in zip(population, deadline_losses):
                    fate = packet.get("fate")
                    emitted_at = float(packet["emit_time_s"])
                    delivered_at = packet.get("delivery_time_s")
                    stop_at = float(packet["observation_end_s"])
                    if fate == "DELIVERED":
                        delay = float(delivered_at) - emitted_at
                        if delay < 0:
                            packet_loss_fields_match = False
                            continue
                        expected_loss = min(delay, float(deadline_s)) / float(deadline_s)
                    elif packet.get("terminal_reason") is not None:
                        expected_loss = 1.0
                    elif (packet.get("censor_reason") is not None
                          and stop_at - emitted_at >= float(deadline_s)):
                        expected_loss = 1.0
                    else:
                        packet_loss_fields_match = False
                        continue
                    if (not isinstance(item, dict)
                            or item.get("status") != "COMPUTED"
                            or float(item.get("deadline_s"))
                            != float(deadline_s)
                            or not math.isclose(float(item.get("value")),
                                                expected_loss,
                                                rel_tol=1e-9, abs_tol=1e-12)):
                        packet_loss_fields_match = False
                    loss_values.append(expected_loss)
                packet_losses_ok = (
                    len(loss_values) == len(population)
                    and all(math.isfinite(value) and 0.0 <= value <= 1.0
                            for value in loss_values))
                if packet_losses_ok:
                    recomputed_mean = statistics.fmean(loss_values)
            except (TypeError, ValueError, OverflowError):
                packet_losses_ok = False
                recomputed_mean = None
        summary_matches_packets = (
            recomputed_mean is not None and metric_numeric
            and math.isclose(recomputed_mean, metric_value,
                             rel_tol=1e-9, abs_tol=1e-12))
        status_ok = (
            metric.get("status") == "COMPUTED" and metric_numeric
            and metric_deadline == float(deadline_s)
            and metric.get("exact_packets") == metric_packets
            and metric.get("interval_censored", 0) == 0
            and metric.get("not_computable_packets", 0) == 0
            and expected_window and packet_rows_ok
            and delivery_rows_ok and population_membership_ok
            and packet_losses_ok and packet_loss_fields_match
            and summary_matches_packets
            and len(pop_pids) == metric_packets
            and len(pids) == len(set(pids))
            and len(trace_set) == 1)
        checks.append({"label": label, "passed": bool(status_ok),
                       "deadline_loss_status": metric.get("status"),
                       "deadline_s": metric_deadline,
                       "value": metric_value,
                       "recomputed_packet_mean": recomputed_mean,
                       "summary_matches_packets": summary_matches_packets,
                       "packet_loss_fields_match": packet_loss_fields_match,
                       "packet_fates_complete": (delivery_rows_ok
                                                 if packet_rows_ok
                                                 and expected_window else False),
                       "population_membership_ok": population_membership_ok,
                       "population_packets": metric_packets,
                       "exact_packets": metric.get("exact_packets"),
                       "interval_censored": metric.get("interval_censored"),
                       "not_computable_packets": metric.get(
                           "not_computable_packets"),
                       "window": [window_start, window_end],
                       "trace_sha256": (trace_set[0] if len(trace_set) == 1
                                        else None),
                       "packet_rows": len(packet_rows) if packet_rows_ok
                       else None})
        pids_by_label[label] = pids
        population_pids[label] = pop_pids
        trace_hashes[label] = trace_set[0] if len(trace_set) == 1 else None
    paired = (len(rows) == len(expected_labels)
              and all(check["passed"] for check in checks)
              and len({tuple(values) for values in pids_by_label.values()}) == 1
              and len({tuple(values) for values in
                       population_pids.values()}) == 1
              and len(set(trace_hashes.values())) == 1)
    return {"passed": paired, "checks": checks,
            "same_trace": len(set(trace_hashes.values())) == 1,
            "same_packet_ids": (len({tuple(values)
                                     for values in pids_by_label.values()}) == 1),
            "same_population_ids": (len({tuple(values)
                                         for values in
                                         population_pids.values()}) == 1),
            "trace_sha256": next(iter(set(trace_hashes.values())))
            if len(set(trace_hashes.values())) == 1 else None}


def _branch_primary_check(result, deadline_s, population_window,
                          min_sampled=1, min_eligible=0):
    """Recompute the predeclared common-regret minus candidate-regret block."""
    document = (result or {}).get("document") or {}
    deadline = document.get("deadline") or {}
    measurement_window = document.get("measurement_window") or {}
    sampling = document.get("sampling") or {}
    eligibility = document.get("eligibility") or {}
    block = document.get("block") or {}
    branch_rows = document.get("branches")
    branch_rows_ok = isinstance(branch_rows, list)
    ids = [row.get("decision_id") for row in branch_rows
           if isinstance(row, dict)] if isinstance(branch_rows, list) else []
    sampled_ids = sampling.get("decisions")
    sample_count = sampling.get("sampled")
    eligible_count = eligibility.get("eligible")
    try:
        actual_window = [float(measurement_window.get("start_s")),
                         float(measurement_window.get("end_s"))]
        expected_window = (actual_window[0] == float(population_window[0])
                           and actual_window[1] == float(population_window[1]))
        actual_deadline = float(deadline.get("deadline_s"))
    except (TypeError, ValueError):
        actual_window = [None, None]
        expected_window = False
        actual_deadline = None
    branch_pairs = []
    branch_window_ok = True
    for row in branch_rows if isinstance(branch_rows, list) else []:
        if not isinstance(row, dict):
            branch_pairs.append(None)
            branch_window_ok = False
            continue
        regret = row.get("regret") or {}
        common, candidate = regret.get("common"), regret.get("candidate")
        if (not isinstance(common, (int, float)) or isinstance(common, bool)
                or not math.isfinite(float(common))
                or not isinstance(candidate, (int, float))
                or isinstance(candidate, bool)
                or not math.isfinite(float(candidate))):
            branch_pairs.append(None)
        else:
            branch_pairs.append({
                "decision_id": row.get("decision_id"),
                "common_regret": float(common),
                "candidate_regret": float(candidate),
                "common_minus_candidate_regret": float(common) - float(candidate),
            })
        try:
            at = float(row.get("t_decision_start"))
            branch_window_ok = (branch_window_ok
                                and float(population_window[0]) <= at
                                <= float(population_window[1]))
        except (TypeError, ValueError):
            branch_window_ok = False
    complete_pairs = [row for row in branch_pairs if row is not None]
    arms = block.get("arms") or {}
    common_summary = arms.get("common") or {}
    candidate_summary = arms.get("candidate") or {}
    try:
        common_mean = float(common_summary.get("mean_regret"))
        candidate_mean = float(candidate_summary.get("mean_regret"))
        recomputed_delta = (statistics.fmean(
            row["common_minus_candidate_regret"] for row in complete_pairs)
            if complete_pairs else None)
        block_delta = common_mean - candidate_mean
        means_match = (recomputed_delta is not None
                       and math.isfinite(common_mean)
                       and math.isfinite(candidate_mean)
                       and math.isclose(recomputed_delta, block_delta,
                                        rel_tol=1e-9, abs_tol=1e-12))
    except (TypeError, ValueError):
        common_mean = candidate_mean = block_delta = None
        recomputed_delta = None
        means_match = False
    expected_ids = (isinstance(sampled_ids, list)
                    and ids == sampled_ids
                    and len(ids) == len(set(ids))
                    and all(isinstance(value, int)
                            and not isinstance(value, bool)
                            for value in sampled_ids))
    sample_count_matches_rows = (
        isinstance(sample_count, int) and not isinstance(sample_count, bool)
        and sample_count == len(ids))
    eligible_count_covers_sample = (
        isinstance(eligible_count, int)
        and not isinstance(eligible_count, bool)
        and eligible_count >= len(ids))
    passed = (
        (result or {}).get("status") == "ok"
        and document.get("status") == "ok"
        and block.get("status") == "ok"
        and actual_deadline == float(deadline_s)
        and expected_window
        and isinstance(sample_count, int) and not isinstance(sample_count, bool)
        and sample_count >= int(min_sampled)
        and sample_count_matches_rows and eligible_count_covers_sample
        and isinstance(eligible_count, int) and not isinstance(eligible_count, bool)
        and eligible_count >= int(min_eligible)
        and expected_ids and branch_rows_ok
        and len(complete_pairs) == len(branch_rows)
        and branch_window_ok and means_match)
    source = document.get("source") or {}
    trace_sha256 = source.get("trace_sha256")
    return {
        "passed": bool(passed),
        "status": document.get("status"),
        "block_status": block.get("status"),
        "deadline_s": actual_deadline,
        "measurement_window_s": actual_window,
        "eligible_comparable_branch_points": eligible_count,
        "sampled_branch_points": sample_count,
        "sampled_branch_ids": sampled_ids,
        "complete_branch_regret_pairs": len(complete_pairs),
        "common_mean_regret": common_mean,
        "candidate_mean_regret": candidate_mean,
        "common_minus_candidate_regret": block_delta,
        "independent_unit": "one scenario x trace x seed block",
        "trace_sha256": trace_sha256,
        "branches": branch_pairs,
        "checks": {
            "task_ok": (result or {}).get("status") == "ok",
            "block_ok": block.get("status") == "ok",
            "deadline_matches": actual_deadline == float(deadline_s),
            "window_matches": expected_window,
            "sampled_count_sufficient": (isinstance(sample_count, int)
                                         and sample_count >= int(min_sampled)),
            "sampled_count_matches_rows": sample_count_matches_rows,
            "eligible_count_covers_sample": eligible_count_covers_sample,
            "eligible_count_sufficient": (isinstance(eligible_count, int)
                                         and eligible_count >= int(min_eligible)),
            "sampled_ids_match_rows": bool(expected_ids),
            "all_sampled_regrets_computed": (
                len(complete_pairs) == len(branch_rows)
                if isinstance(branch_rows, list) else False),
            "sample_times_in_window": branch_window_ok,
            "block_mean_recomputes": means_match,
        },
    }


def _primary_estimand_check(run_dir, bundle, contract, cell_ids=None):
    """Verify the D/window estimand and exact within-seed pairing in results."""
    run_dir = Path(run_dir)
    cells = {cell["cell_id"]: cell for cell in bundle.get("cells", [])}
    run_doc_path = run_dir / "run.json"
    run_doc = (json.loads(run_doc_path.read_text(encoding="utf-8"))
               if run_doc_path.is_file() else {})
    records = {item.get("cell_id"): item
               for item in run_doc.get("cells", [])}
    selected = set(cell_ids if cell_ids is not None
                   else (bundle.get("tiers") or {}).get("b_dev", []))
    deadline_s = t1_suite._development_deadline_s(contract)
    population_window = t1_suite._development_population_window(contract)
    cell_checks = {}
    for cell_id in sorted(selected):
        cell = cells[cell_id]
        args = list(cell.get("args") or [])
        task = None
        for index, arg in enumerate(args[:-1]):
            if arg == "--task":
                task = args[index + 1]
                break
        if task not in ("branch_alignment", "network_alignment",
                        "execution_modes"):
            continue
        record = records.get(cell_id) or {}
        result_path = (run_dir / record.get("result_path", ""))
        result_sha = record.get("result_sha256")
        if (record.get("status") != "ok" or not result_path.is_file()
                or not result_sha
                or t1_suite._sha256_file(result_path) != result_sha):
            cell_checks[cell_id] = {
                "passed": False, "status": record.get("status", "MISSING"),
                "reason": "cell result is missing, failed, or hash-mismatched"}
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            cell_checks[cell_id] = {
                "passed": False, "status": "INVALID_RESULT",
                "reason": f"{type(exc).__name__}: {exc}"}
            continue
        document = result.get("document") or {}
        if task == "branch_alignment":
            spec = next((item for item in
                         ((contract.get("b_round") or {}).get(
                             "scenarios") or [])
                         if cell_id.startswith(f"b-{item.get('id')}-")), {})
            check = _branch_primary_check(
                result, deadline_s, population_window,
                min_sampled=int(spec.get("min_sampled_branches", 1)),
                min_eligible=int(
                    (contract.get("admission_thresholds") or {}).get(
                        "min_comparable_branch_points", 0)))
        elif task == "network_alignment":
            rows = {str(row.get("arm")): row
                    for row in document.get("arms", [])}
            labels = ["stale", "now", "common", "candidate"]
        else:
            rows = {str(row.get("mode")): row
                    for row in document.get("modes", [])}
            labels = list(t1_suite.t1_tasks.EXECUTION_MODES)
        check = _primary_rows_check(rows, labels, deadline_s,
                                    population_window)
        check.update({"task": task, "status": document.get("status"),
                      "result_sha256": result_sha})
        check["passed"] = bool(check["passed"]
                                and result.get("status") == "ok"
                                and document.get("status") == "ok")
        cell_checks[cell_id] = check

    paired_blocks = []
    for spec in ((contract.get("b_round") or {}).get("scenarios") or []):
        if spec.get("expectation") not in ("competitive_multi_od",
                                             "negative_control"):
            continue
        sid = str(spec.get("id"))
        seeds = [int(value) for value in
                 ((spec.get("task_seeds") or {}).get("network_alignment")
                  or spec.get("seeds", []))]
        for seed in seeds:
            branch_id = f"b-{sid}-branch-seed-{seed}"
            network_id = f"b-{sid}-network-seed-{seed}"
            mode_id = f"b-{sid}-execution-modes-seed-{seed}"
            if not ({branch_id, network_id, mode_id} & selected):
                continue
            branch_seeds = [int(value) for value in
                            ((spec.get("task_seeds") or {}).get(
                                "branch_alignment") or
                             (spec.get("seeds", [])
                              if "branch_alignment" in
                              (spec.get("tasks") or []) else []))]
            requires_branch = seed in branch_seeds
            branch = cell_checks.get(branch_id) or {"passed": not requires_branch,
                                                   "reason": "not sampled for this seed"}
            network = cell_checks.get(network_id) or {"passed": False,
                                                      "reason": "missing"}
            modes = cell_checks.get(mode_id) or {"passed": False,
                                                 "reason": "missing"}
            same_trace = (network.get("trace_sha256") is not None
                          and network.get("trace_sha256")
                          == modes.get("trace_sha256"))
            paired_blocks.append({
                "scenario": sid, "role": spec.get("expectation"),
                "seed": seed,
                "branch_cell_id": (branch_id if requires_branch else None),
                "network_cell_id": network_id,
                "execution_modes_cell_id": mode_id,
                "branch_primary_complete": (bool(branch.get("passed"))
                                            if requires_branch else None),
                "branch_trace_sha256": (branch.get("trace_sha256")
                                        if requires_branch else None),
                "network_complete": bool(network.get("passed")),
                "execution_modes_complete": bool(modes.get("passed")),
                "same_trace": same_trace,
                "trace_sha256": (network.get("trace_sha256")
                                 if same_trace else None),
                "same_branch_trace": (
                    (branch.get("trace_sha256") is not None
                     and branch.get("trace_sha256")
                     == network.get("trace_sha256")
                     == modes.get("trace_sha256"))
                    if requires_branch else None),
                "passed": bool(branch.get("passed") and network.get("passed")
                               and modes.get("passed") and same_trace
                               and (not requires_branch
                                    or branch.get("trace_sha256")
                                    == network.get("trace_sha256"))),
            })
    return {
        "schema": "t1-development-primary-estimand-check/v1",
        "status": ("COMPUTED" if cell_checks
                   and all(item.get("passed") for item in cell_checks.values())
                   else "NOT_COMPUTABLE"),
        "passed": bool(cell_checks and
                       all(item.get("passed") for item in cell_checks.values())
                       and all(block["passed"] for block in paired_blocks)),
        "deadline_s": deadline_s,
        "population_window_s": list(population_window),
        "cell_checks": cell_checks,
        "paired_blocks": paired_blocks,
        "units": "scenario x trace x seed; arms/modes are paired within a block",
        "packet_is_independent_repeat": False,
        "note": "this gate tests outcome availability, common denominators, and exact pairing only; it never ranks arms or selects by effect",
    }


def _evaluate_pilot_budget(bundle, pilot_ids, run_doc, budgets,
                           run_dir, contract):
    """Check pilot outcome availability and measured cost before continuation."""
    cells = {cell["cell_id"]: cell for cell in bundle.get("cells", [])}
    records = {record.get("cell_id"): record
               for record in run_doc.get("cells", [])}
    checks = []
    measured_rates = []
    pilot_upper_calls = 0
    for cell_id in pilot_ids:
        cell = cells[cell_id]
        estimate = t1_suite.estimate_bundle_cost({"cells": [cell]})[
            "simulator_calls"]
        pilot_upper_calls += estimate
        record = records.get(cell_id) or {}
        accounting = record.get("simulator_calls") or {}
        started = int(accounting.get("started") or 0)
        terminal = (int(accounting.get("ended") or 0)
                    + int(accounting.get("failed") or 0)
                    + int(accounting.get("timed_out") or 0)
                    + int(accounting.get("interrupted") or 0))
        call_ledger_complete = (started <= estimate and terminal == started
                                and int(accounting.get("unresolved") or 0) == 0)
        complete = (record.get("status") == "ok"
                    and float(record.get("wall_s") or 0.0)
                    <= float(budgets["cell_wall_s"])
                    and call_ledger_complete and started > 0
                    and accounting.get("ended") == started
                    and accounting.get("failed", 0) == 0
                    and accounting.get("timed_out", 0) == 0
                    and accounting.get("unresolved", 0) == 0)
        rate = (float(accounting.get("simulator_wall_s") or 0.0) / started
                if started else None)
        if rate is not None:
            measured_rates.append(rate)
        checks.append({"cell_id": cell_id, "passed": complete,
                       "status": record.get("status"),
                       "outer_wall_s": record.get("wall_s"),
                       "calls_upper": estimate,
                       "calls_started": started,
                       "call_ledger_complete": call_ledger_complete,
                       "calls_ended": accounting.get("ended", 0),
                       "calls_failed": accounting.get("failed", 0),
                       "calls_timed_out": accounting.get("timed_out", 0),
                       "simulator_wall_s": accounting.get(
                           "simulator_wall_s", 0.0)})
    call_accounting = run_doc.get("simulator_call_accounting") or {}
    total_upper = t1_suite.estimate_bundle_cost({
        "cells": [cells[cell_id] for cell_id in
                  (bundle.get("tiers") or {}).get("b_dev", [])]
    })["simulator_calls"]
    remaining_upper = max(0, total_upper - pilot_upper_calls)
    per_call_s = max(measured_rates) if measured_rates else None
    projected = (None if per_call_s is None else
                 float(call_accounting.get("simulator_wall_s") or 0.0)
                 + per_call_s * remaining_upper)
    call_budget_ok = total_upper <= int(budgets["simulator_call_budget"])
    wall_budget_ok = (projected is not None
                      and projected <= float(budgets["total_wall_s"]))
    primary = _primary_estimand_check(run_dir, bundle, contract, pilot_ids)
    admission = t1_admission.evaluate_run(run_dir, bundle, contract)
    admission_ok = (admission.get("summary", {}).get("admitted")
                    == len(admission.get("competitive_scenarios") or [])
                    and bool(admission.get("competitive_scenarios")))
    return {
        "schema": "t1-development-pilot-gate/v1",
        "passed": (all(check["passed"] for check in checks)
                   and call_budget_ok and wall_budget_ok
                   and primary.get("passed") and admission_ok),
        "checks": checks,
        "simulator_call_budget": {
            "passed": call_budget_ok, "upper_bound": total_upper,
            "limit": budgets["simulator_call_budget"]},
        "wall_budget_projection": {
            "passed": wall_budget_ok, "measured_max_s_per_call": per_call_s,
            "remaining_call_upper_bound": remaining_upper,
            "projected_total_simulator_wall_s": projected,
            "limit_s": budgets["total_wall_s"],
            "rule": "pilot maximum observed simulator seconds per call times remaining static call upper bound"},
        "primary_estimand_gate": primary,
        "outcome_blind_structural_admission": admission,
        "reasons": [
            *[f"pilot cell {check['cell_id']} did not finish with all calls accounted"
              for check in checks if not check["passed"]],
            *([] if call_budget_ok else ["static simulator call upper bound exceeds budget"]),
            *([] if wall_budget_ok else ["measured-cost wall projection exceeds budget"]),
            *([] if primary.get("passed") else
              ["pilot does not have a complete, fixed-D, exactly paired primary outcome"]),
            *([] if admission_ok else
              ["pilot has not passed the outcome-blind multi-OD structural admission gate"]),
        ],
    }


def _verified_cell_document(run_dir, record):
    """Load a completed cell only when its recorded result hash still matches."""
    if record is None or record.get("status") != "ok":
        return None, "cell missing or not ok"
    if record.get("predicate_verdict", {}).get("passed") is not True:
        return None, "cell predicate did not pass"
    relative = record.get("result_path")
    if not relative:
        return None, "cell record has no result path"
    path = Path(run_dir) / relative
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"result unreadable: {exc}"
    digest = hashlib.sha256(raw).hexdigest()
    if digest != record.get("result_sha256"):
        return None, "result hash differs from run.json"
    document = payload.get("document")
    if not isinstance(document, dict) or document.get("status") != "ok":
        return None, "result document is not complete"
    return payload, None


def _primary_loss(task_document, row, label, deadline_s, population_window):
    outcome = ((row.get("network_outcome") or {})
               if label in t1_suite.t1_tasks.EXECUTION_MODES
               else (row.get("outcome") or {}))
    metric = outcome.get("deadline_primary_loss") or {}
    deadline = metric.get("deadline_s")
    packets = metric.get("packets")
    exact = metric.get("exact_packets")
    window = ((task_document.get("deadline") or {}).get(
        "population_window_s")
        or row.get("measurement_window"))
    try:
        deadline_value = float(deadline)
        loss_value = float(metric.get("value"))
    except (TypeError, ValueError):
        return None
    if (metric.get("status") != "COMPUTED"
            or not isinstance(metric.get("value"), (int, float))
            or isinstance(metric.get("value"), bool)
            or not math.isfinite(loss_value)
            or deadline_value != float(deadline_s)
            or not isinstance(packets, int) or packets <= 0
            or exact != packets or metric.get("interval_censored", 0) != 0
            or list(window or []) != list(population_window)):
        return None
    return {"loss": loss_value, "packets": packets, "exact_packets": exact,
            "deadline_s": deadline_value,
            "population_window_s": list(window)}


def _packet_rows_for_document(document):
    """Yield exact packet-level outcomes without filling absent fields."""
    source = document.get("source") or {}
    workload = source.get("synthetic_workload") or {}
    summary = workload.get("summary") or {}
    manifest = {int(row["pid"]): row for row in
                (summary.get("packet_manifest") or [])
                if isinstance(row, dict) and isinstance(row.get("pid"), int)}
    rows = []
    for label, entries in (
            [(str(row.get("arm")),
              (row.get("outcome") or {}).get("deadline_primary_loss", {}).get(
                  "packet_outcomes") or [])
             for row in document.get("arms") or []]
            + [(str(row.get("mode")),
                (row.get("network_outcome") or {}).get(
                    "deadline_primary_loss", {}).get("packet_outcomes") or [])
               for row in document.get("modes") or []]):
        for item in entries:
            if not isinstance(item, dict):
                continue
            pid = item.get("pid")
            manifest_item = manifest.get(pid) if isinstance(pid, int) else None
            loss = item.get("deadline_loss") or {}
            rows.append({
                "label": label,
                "pid": pid,
                "od_id": (None if manifest_item is None
                          else manifest_item.get("od_id")),
                "emit_time_s": item.get("emit_time_s"),
                "bits": item.get("bits"),
                "fate": item.get("fate"),
                "delivery_record_present": item.get(
                    "delivery_record_present"),
                "delivery_time_s": item.get("delivery_time_s"),
                "observation_end_s": item.get("observation_end_s"),
                "in_population": item.get("in_population"),
                "deadline_s": loss.get("deadline_s"),
                "deadline_loss_status": loss.get("status"),
                "deadline_loss": loss.get("value"),
                "deadline_loss_lower": loss.get("lower_bound"),
                "deadline_loss_upper": loss.get("upper_bound"),
                "terminal_reason": item.get("terminal_reason"),
                "censor_reason": item.get("censor_reason"),
                "packet_outcomes_sha256": (document.get("outcome_document") or {}).get(
                    "packet_outcomes_sha256"),
            })
    return rows


def _cost_fields_present(row):
    cost = row.get("total_cost") or {}
    precompute = ((cost.get("control_traffic") or {}).get("precompute") or {})
    return all((
        isinstance(cost.get("packet_decisions"), dict),
        isinstance(cost.get("background_updates"), dict),
        isinstance(cost.get("query_service"), dict),
        isinstance(cost.get("install"), dict),
        all(key in precompute for key in
            ("builds", "targets", "installs", "cost_kind", "build_wall_s")),
    ))


def _paired_development_outputs(run_dir, bundle, contract, run_doc, out_dir):
    """Write exact per-packet rows and seed-block summaries; never drop a block silently."""
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    records = {str(row.get("cell_id")): row
               for row in run_doc.get("cells", [])}
    packet_fields = ["scenario", "seed", "task", "cell_id", "result_sha256",
                     "trace_sha256", "label", "pid", "od_id", "emit_time_s",
                     "bits", "fate", "delivery_record_present",
                     "delivery_time_s", "observation_end_s", "in_population",
                     "deadline_s", "deadline_loss_status", "deadline_loss",
                     "deadline_loss_lower", "deadline_loss_upper",
                     "terminal_reason", "censor_reason",
                     "packet_outcomes_sha256"]
    csv_path = out_dir / "paired-packet-outcomes.csv"
    loaded = {}
    issues = []
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=packet_fields)
        writer.writeheader()
        for cell in bundle.get("cells", []):
            if cell.get("cell_id") not in records:
                continue
            record = records[cell["cell_id"]]
            payload, issue = _verified_cell_document(run_dir, record)
            if issue:
                issues.append({"cell_id": cell["cell_id"], "reason": issue})
                continue
            loaded[cell["cell_id"]] = payload
            task = (cell.get("args") or [])
            task_type = (task[task.index("--task") + 1]
                         if "--task" in task else None)
            document = payload["document"]
            source = document.get("source") or {}
            for item in _packet_rows_for_document(document):
                writer.writerow({
                    "scenario": ((source.get("synthetic_workload") or {}).get(
                        "summary") or {}).get("workload_id"),
                    "seed": cell.get("seed"), "task": task_type,
                    "cell_id": cell.get("cell_id"),
                    "result_sha256": record.get("result_sha256"),
                    "trace_sha256": source.get("trace_sha256"),
                    **item,
                })

    deadline_s = float((contract.get("statistics") or {}).get(
        "deadline", {}).get("value_s"))
    population_window = list((contract.get("statistics") or {}).get(
        "population_window_s") or [])
    scenario_blocks = []
    missing_blocks = []
    comparisons = {}
    for spec in ((contract.get("b_round") or {}).get("scenarios") or []):
        if spec.get("expectation") != "competitive_multi_od":
            continue
        sid = str(spec.get("id"))
        network_seeds = [int(value) for value in
                         ((spec.get("task_seeds") or {}).get(
                             "network_alignment") or spec.get("seeds", []))]
        for seed in network_seeds:
            network_id = f"b-{sid}-network-seed-{seed}"
            modes_id = f"b-{sid}-execution-modes-seed-{seed}"
            network_payload = loaded.get(network_id)
            modes_payload = loaded.get(modes_id)
            network_doc = ((network_payload or {}).get("document") or {})
            modes_doc = ((modes_payload or {}).get("document") or {})
            errors = []
            if network_payload is None:
                errors.append(f"network result unavailable: {network_id}")
            if modes_payload is None:
                errors.append(f"five-mode result unavailable: {modes_id}")
            network_source = network_doc.get("source") or {}
            modes_source = modes_doc.get("source") or {}
            trace_a, trace_b = (network_source.get("trace_sha256"),
                                modes_source.get("trace_sha256"))
            if not trace_a or trace_a != trace_b:
                errors.append("network and five-mode traces do not match")
            network_losses, mode_losses, costs = {}, {}, {}
            if network_payload is not None:
                arms = {row.get("arm"): row for row in
                        network_doc.get("arms") or []}
                for arm in ("stale", "now", "common", "candidate"):
                    row = arms.get(arm)
                    exact = _primary_loss(
                        network_doc, row or {}, arm, deadline_s,
                        population_window)
                    if exact is None:
                        errors.append(f"{arm} D=30/window outcome incomplete")
                    else:
                        network_losses[arm] = exact
                        costs["network_" + arm] = row.get("total_cost")
                    if row is not None and not _cost_fields_present(row):
                        errors.append(f"{arm} compute/query/precompute cost fields missing")
                network_packet_ids = [sorted(int(packet["pid"]) for packet in
                                     ((row or {}).get("network_outcome") or {}).get(
                                         "packet_outcomes") or [])
                              for row in arms.values()]
                if network_packet_ids and any(ids != network_packet_ids[0]
                                              for ids in network_packet_ids[1:]):
                    errors.append("four network arms do not share packet IDs")
            if modes_payload is not None:
                modes = {row.get("mode"): row for row in
                         modes_doc.get("modes") or []}
                expected_modes = tuple(t1_suite.t1_tasks.EXECUTION_MODES)
                for mode in expected_modes:
                    row = modes.get(mode)
                    exact = _primary_loss(
                        modes_doc, row or {}, mode, deadline_s,
                        population_window)
                    if exact is None:
                        errors.append(f"{mode} D=30/window outcome incomplete")
                    else:
                        mode_losses[mode] = exact
                        costs["mode_" + mode] = row.get("total_cost")
                    if row is not None and not _cost_fields_present(row):
                        errors.append(f"{mode} compute/query/precompute cost fields missing")
                mode_packet_ids = [sorted(int(packet["pid"]) for packet in
                                     ((row or {}).get("network_outcome") or {}).get(
                                         "packet_outcomes") or [])
                              for row in modes.values()]
                if mode_packet_ids and any(ids != mode_packet_ids[0]
                                           for ids in mode_packet_ids[1:]):
                    errors.append("five modes do not share packet IDs")
                if (network_packet_ids and mode_packet_ids
                        and network_packet_ids[0] != mode_packet_ids[0]):
                    errors.append("network arms and five modes do not share packet IDs")
            block = {"scenario": sid, "seed": seed,
                     "trace_sha256": trace_a if trace_a == trace_b else None,
                     "network_cell_id": network_id,
                     "execution_modes_cell_id": modes_id,
                     "network_losses": network_losses,
                     "execution_mode_losses": mode_losses,
                     "costs": costs,
                     "complete": not errors,
                     "reasons": errors}
            scenario_blocks.append(block)
            if errors:
                missing_blocks.append({"scenario": sid, "seed": seed,
                                       "reasons": errors})

    complete = bool(scenario_blocks) and not missing_blocks
    if complete:
        pairs = {}
        for other in ("stale", "now", "common"):
            pairs[f"{other}_loss_minus_candidate"] = [
                block["network_losses"][other]["loss"]
                - block["network_losses"]["candidate"]["loss"]
                for block in scenario_blocks]
        for mode in t1_suite.t1_tasks.EXECUTION_MODES:
            if mode == "per_packet":
                continue
            pairs[f"per_packet_loss_minus_{mode}"] = [
                block["execution_mode_losses"]["per_packet"]["loss"]
                - block["execution_mode_losses"][mode]["loss"]
                for block in scenario_blocks]
        for name, differences in pairs.items():
            stats = t1_stats.bootstrap_ci(
                differences, n_boot=10000,
                seed=t1_stats.DEFAULT_BOOTSTRAP_SEED)
            comparisons[name] = {
                "paired_differences": differences,
                "blocks": [{"scenario": block["scenario"],
                            "seed": block["seed"],
                            "difference": differences[index]}
                           for index, block in enumerate(scenario_blocks)],
                "bootstrap_95_percentile_ci": stats,
                "threshold_sensitivity": {
                    str(threshold): bool(stats["mean"] >= threshold)
                    for threshold in (0.005, 0.01, 0.02)},
                "interpretation": "positive favors the right-hand method named in the contrast; descriptive only, n=3, no significance claim",
            }
    analysis = {
        "schema": "t1-development-paired-analysis/v1",
        "status": "COMPUTED" if complete else "INCOMPLETE",
        "unit": "one scenario x trace x seed block; never packet-level",
        "deadline_s": deadline_s,
        "population_window_s": population_window,
        "expected_independent_blocks": len(scenario_blocks),
        "completed_independent_blocks": sum(
            1 for block in scenario_blocks if block["complete"]),
        "blocks": scenario_blocks,
        "missing_blocks": missing_blocks,
        "result_integrity_issues": issues,
        "comparisons": comparisons,
        "packet_rows_csv": csv_path.name,
        "limits": ["only complete, hash-verified frozen blocks enter paired summaries",
                   "n=3 development seeds are descriptive; no significance claim",
                   "execution modes retain the precomputed baseline and its build/update cost"],
    }
    analysis_path = out_dir / "paired-analysis.json"
    analysis_path.write_text(json.dumps(analysis, ensure_ascii=False,
                                        indent=2) + "\n", encoding="utf-8")
    with csv_path.open(encoding="utf-8", newline="") as stream:
        packet_rows = sum(1 for _ in csv.DictReader(stream))
    return {"analysis": analysis, "analysis_path": str(analysis_path),
            "packet_rows_path": str(csv_path),
            "packet_rows": packet_rows}


def _replay_output(run_dir, bundle, contract, run_doc, out_dir,
                   external_run_identity=None):
    """Build the predeclared all-arm replay only from verified result files."""
    specs = [spec for spec in
             ((contract.get("b_round") or {}).get("scenarios") or [])
             if spec.get("expectation") == "competitive_multi_od"
             and spec.get("replay_seed") is not None]
    if len(specs) != 1:
        return {"status": "NOT_AVAILABLE",
                "reason": "the contract does not identify one replay scenario/seed"}
    spec = specs[0]
    scenario, seed = str(spec["id"]), int(spec["replay_seed"])
    branch_id = f"b-{scenario}-branch-seed-{seed}"
    network_id = f"b-{scenario}-network-seed-{seed}"
    records = {str(row.get("cell_id")): row
               for row in run_doc.get("cells", [])}
    branch_payload, branch_issue = _verified_cell_document(
        run_dir, records.get(branch_id))
    network_payload, network_issue = _verified_cell_document(
        run_dir, records.get(network_id))
    if branch_issue or network_issue:
        return {"status": "NOT_AVAILABLE", "scenario": scenario,
                "seed": seed,
                "reasons": [reason for reason in
                            (branch_issue, network_issue) if reason]}
    cells = {str(cell.get("cell_id")): cell
             for cell in bundle.get("cells", [])}
    if (cells.get(branch_id, {}).get("driver")
            != "CODE.experiment_platform.t1_tasks"
            or cells.get(network_id, {}).get("driver")
            != "CODE.experiment_platform.t1_tasks"):
        return {"status": "NOT_AVAILABLE", "scenario": scenario,
                "seed": seed, "reasons": ["replay cells are not task drivers"]}
    identity = bundle.get("identity") or {}
    git_identity = identity.get("git") or {}
    run_identity = {
        "run_id": (external_run_identity or {}).get("run_id"),
        "release_id": (external_run_identity or {}).get("release_id"),
        "source_git_commit": git_identity.get("commit"),
        "bundle_fingerprint": bundle.get("bundle_fingerprint"),
        "execution_chain_sha256": (bundle.get("execution_chain") or {}).get(
            "combined_sha256"),
        "contract_sha256": bundle.get("contract_sha256"),
        "branch_cell_id": branch_id,
        "branch_result_sha256": records[branch_id].get("result_sha256"),
        "network_cell_id": network_id,
        "network_result_sha256": records[network_id].get("result_sha256"),
    }
    path = Path(out_dir) / "replay.html"
    try:
        info = replay_html.write_html(
            Path(run_dir) / records[network_id]["result_path"],
            Path(run_dir) / records[branch_id]["result_path"],
            path, run_identity=run_identity)
    except (OSError, ValueError) as exc:
        return {"status": "FAILED", "scenario": scenario, "seed": seed,
                "reason": f"{type(exc).__name__}: {exc}"}
    return {"status": "WRITTEN", **info}


def execute(contract_path: Path, out_dir: Path, *, run_id=None,
            release_id=None) -> dict:
    contract_path = Path(contract_path)
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise RuntimeError(f"development output directory exists: {out_dir}")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    readiness = t1_suite.require_runtime_ready_contract(
        contract, source=str(contract_path))
    runtime_stage = readiness.get("status") if readiness else None
    if contract.get("not_formal") is not True:
        raise RuntimeError("development wrapper requires not_formal: true")
    if contract.get("not_training") is not True:
        raise RuntimeError("development wrapper refuses training contracts")
    if (contract.get("formal_design") or {}).get("ready") is True:
        raise RuntimeError("development wrapper refuses a ready formal design")

    out_dir.mkdir(parents=True)
    bundle_dir = out_dir / "bundle"
    bundle = t1_suite.compile_bundle(contract_path, bundle_dir)
    validation = t1_suite.validate_bundle(bundle_dir)
    if validation.get("valid") is not True:
        raise RuntimeError("compiled bundle did not validate")

    b_dev_ids = set((bundle.get("tiers") or {}).get("b_dev") or [])
    b_dev_cells = [cell for cell in bundle.get("cells", [])
                   if cell.get("cell_id") in b_dev_ids]
    cost = t1_suite.estimate_bundle_cost({"cells": b_dev_cells})
    limit = int(contract.get("simulator_call_budget", 80))
    b_limits = contract.get("b_run_limits") or {}
    if b_limits:
        if len(b_dev_cells) > int(b_limits.get("max_b_dev_cells", 40)):
            raise RuntimeError("b_dev cell count exceeds the frozen B limit")
        if cost["simulator_calls"] > int(
                b_limits.get("max_simulator_calls", 60)):
            raise RuntimeError("simulator-call estimate exceeds the frozen B limit")
        if (float(bundle["budgets"]["cell_wall_s"])
                > float(b_limits.get("max_cell_wall_s", 120))):
            raise RuntimeError("cell wall budget exceeds the frozen B limit")
        if (float(bundle["budgets"]["total_wall_s"])
                > float(b_limits.get("max_simulator_wall_s", 3600))):
            raise RuntimeError("total simulator wall budget exceeds the frozen B limit")
        if (int(bundle["budgets"]["simulator_call_budget"])
                > int(b_limits.get("max_simulator_calls", 60))):
            raise RuntimeError("suite simulator-call cap exceeds the frozen B limit")
        if int(b_limits.get("max_concurrency", 1)) != 1:
            raise RuntimeError("B development execution is single-concurrency only")
    if cost["simulator_calls"] > limit:
        raise RuntimeError(
            f"predeclared simulator-call estimate {cost['simulator_calls']} "
            f"exceeds budget {limit}; no simulation was started")
    (out_dir / "simulator-call-estimate.json").write_text(
        json.dumps(cost, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    run_dir = out_dir / "b_dev"
    if runtime_stage == "COST_PROBE_READY":
        authorization = readiness["runtime_authorization"]
        probe_ids = list(authorization["cell_ids"])
        probe_run = t1_suite.run_bundle(
            bundle_dir, "b_dev", run_dir, cell_ids=probe_ids,
            append=False, stop_on_failure=True)
        probe_accounting = probe_run.get("simulator_call_accounting") or {}
        summary = {
            "schema": "t1-development-pipeline/v1",
            "tier": "b_dev",
            "runtime_stage": runtime_stage,
            "not_formal": True,
            "not_training": True,
            "release_identity": {"run_id": run_id,
                                 "release_id": release_id},
            "contract_sha256": bundle["contract_sha256"],
            "execution_chain_sha256": bundle["execution_chain"][
                "combined_sha256"],
            "bundle_fingerprint": bundle["bundle_fingerprint"],
            "simulator_call_estimate": cost["simulator_calls"],
            "authorized_probe_cell_ids": probe_ids,
            "probe_run_status": probe_run.get("status"),
            "probe_run_counts": probe_run.get("counts"),
            "simulator_call_accounting": probe_accounting,
            "simulator_wall_s": probe_accounting.get("simulator_wall_s"),
            "pending_cell_ids": probe_run.get("pending_cell_ids"),
        }
        (out_dir / "pipeline-summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        return summary

    _validate_a0_smoke_cell(contract, b_dev_cells)

    pilot_ids = [str(value) for value in contract.get("pilot_cell_ids", [])]
    pilot_gate = None
    if pilot_ids:
        if len(pilot_ids) != len(set(pilot_ids)) \
                or not set(pilot_ids).issubset(b_dev_ids):
            raise RuntimeError("pilot_cell_ids must be unique b_dev cells")
        expected_smoke = next((cell["cell_id"] for cell in b_dev_cells
                               if cell.get("group") == "b_round"), None)
        if not pilot_ids or pilot_ids[0] != expected_smoke:
            raise RuntimeError(
                "pilot_cell_ids must start with the negative-control smoke cell")
        run = t1_suite.run_bundle(
            bundle_dir, "b_dev", run_dir,
            smoke_validator=_negative_control_smoke,
            cell_ids=pilot_ids, stop_on_failure=True)
        if run.get("status") == "INCOMPLETE":
            pilot_gate = _evaluate_pilot_budget(
                bundle, pilot_ids, run, bundle["budgets"], run_dir,
                contract)
            run["pilot_gate"] = pilot_gate
            if pilot_gate["passed"]:
                remaining = [cell_id for cell_id in
                             (bundle.get("tiers") or {}).get("b_dev", [])
                             if cell_id not in set(pilot_ids)]
                run = t1_suite.run_bundle(
                    bundle_dir, "b_dev", run_dir,
                    cell_ids=remaining, append=True)
                run["pilot_gate"] = pilot_gate
            else:
                run["status"] = "PILOT_GATE_FAILED"
                (run_dir / "run.json").write_text(
                    json.dumps(run, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    else:
        run = t1_suite.run_bundle(
            bundle_dir, "b_dev", run_dir,
            smoke_validator=_negative_control_smoke)
    report = t1_suite.report_run(run_dir)
    admission = t1_admission.evaluate_run(run_dir, bundle, contract)
    (run_dir / "scenario-admission.json").write_text(
        json.dumps(admission, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    primary_estimand = _primary_estimand_check(run_dir, bundle, contract)
    paired_outputs = _paired_development_outputs(
        run_dir, bundle, contract, run, out_dir)
    replay_output = _replay_output(
        run_dir, bundle, contract, run, out_dir,
        external_run_identity={"run_id": run_id, "release_id": release_id})
    summary = {
        "schema": "t1-development-pipeline/v1",
        "tier": "b_dev",
        "not_formal": True,
        "not_training": True,
        "release_identity": {"run_id": run_id,
                             "release_id": release_id},
        "contract_sha256": bundle["contract_sha256"],
        "execution_chain_sha256": bundle["execution_chain"][
            "combined_sha256"],
        "git_identity": bundle["identity"]["git"],
        "bundle_fingerprint": bundle["bundle_fingerprint"],
        "simulator_call_estimate": cost["simulator_calls"],
        "simulator_call_budget": limit,
        "b_dev_cell_count": len(b_dev_cells),
        "b_dev_cell_budget": b_limits.get("max_b_dev_cells",
                                            contract.get("b_dev_cell_budget")),
        "b_run_limits": b_limits,
        "simulator_call_accounting": run.get(
            "simulator_call_accounting"),
        "pilot_gate": pilot_gate,
        "primary_estimand": primary_estimand,
        "paired_outputs": {
            "status": paired_outputs["analysis"]["status"],
            "analysis": Path(paired_outputs["analysis_path"]).name,
            "packet_outcomes": Path(paired_outputs[
                "packet_rows_path"]).name,
            "packet_rows": paired_outputs["packet_rows"],
            "completed_blocks": paired_outputs["analysis"][
                "completed_independent_blocks"],
            "expected_blocks": paired_outputs["analysis"][
                "expected_independent_blocks"],
        },
        "replay": replay_output,
        "run_status": run.get("status"),
        "run_counts": run.get("counts"),
        "report_status": report.get("run_status"),
        "admission": admission.get("summary"),
        "run_dir": str(run_dir),
    }
    (out_dir / "pipeline-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.run_id):
        parser.error("--run-id must be an unused release-run identifier")
    if not re.fullmatch(r"[0-9a-f]{40}-[0-9a-f]{64}", args.release_id):
        parser.error("--release-id must be the full immutable release identity")
    try:
        summary = execute(args.contract, args.out, run_id=args.run_id,
                          release_id=args.release_id)
    except (OSError, ValueError, RuntimeError, t1_suite.SuiteError,
            t1_suite.BudgetExceeded) as exc:
        print(f"T1 DEVELOPMENT REFUSED/FAILED: {exc}")
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    if not isinstance(summary, dict):
        return 3
    if summary.get("runtime_stage") == "COST_PROBE_READY":
        return 0 if summary.get("probe_run_status") == "ok" else 3
    if "probe_run_status" in summary:
        # A cost-probe-shaped document without its stage label is malformed;
        # never fall through to the legacy summary fields.
        return 3
    return 0 if (summary.get("run_status") == "ok"
                 and summary.get("report_status") == "ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
