"""Pre-experiment trust gates (2026-09-25).

Four gaps let an experiment reach the point of "every run paid for, result
unusable".  Each test below is the mechanical counter-example for one of them,
written so it FAILS on the code that shipped the gap and passes on the code
that closes it:

1. INPUT MATERIALIZATION.  A burst window that no emitted packet can observe
   was accepted and reached the receipt as a declared treatment.
2. COMPILE-TIME INTERCEPTION.  A primary metric the analyzer cannot compute,
   and a utilization endpoint with no sampled denominator, compiled cleanly and
   failed only after the runs.  A "single factor" claim had nowhere to be
   declared, let alone checked.
3. INDEPENDENT VERIFICATION.  The module that exists to catch fabricated
   denominators fabricated a 0.0 utilization of its own, and there was no
   runnable path from persisted artifacts to the independent recomputation.
4. FORMAL CHAIN.  (inventory only; no test can stand in for a VM receipt)

The tests use the smallest real kernel runs that still exercise ISL service, so
the assertions are about mechanism, not about fixture arithmetic.
"""
from __future__ import annotations

import json

import pytest

from CODE.experiment_platform import v2_analysis
from CODE.experiment_platform.primary_metrics import (
    SUPPORTED_PRIMARY_METRICS,
    UTILIZATION_PRIMARY_METRICS,
)
from CODE.leo_sim import config as config_mod
from CODE.leo_sim import kernel, matrix, recompute, trace as trace_mod
from CODE.leo_sim import metrics_independent as indep
from CODE.leo_sim.tests.helpers import (StaticGeometry, cell, cell_center,
                                        make_cfg, row)

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)


def _ring(num_satellites):
    neighbors = {i: {"E": (i + 1) % num_satellites,
                     "W": (i - 1) % num_satellites}
                 for i in range(num_satellites)}

    def visible(sat, lat, lon, t):
        return ((sat == 0 and (lat, lon) == AC)
                or (sat == num_satellites // 2 and (lat, lon) == BC))

    return StaticGeometry(num_satellites, neighbors_map=neighbors,
                          visible=visible)


def _run(num_satellites=4, packets=3, delay=0.0, interval=1.0,
         duration=30.0, node_delay=0.0, timeline=False):
    cfg = make_cfg({
        "scenario": {"duration_s": duration, "num_satellites": num_satellites,
                     "num_planes": 1, "seed": 5},
        "execution": {"available_capacity_interval_s": interval,
                      "compute_delay_s": delay,
                      "node_process_delay_s": node_delay},
    })
    sink, timeline_sink = [], ([] if timeline else None)
    rows = [row(pid, 0.1 * pid, A, B) for pid in range(1, packets + 1)]
    result = kernel.run_simulation(cfg, rows, geometry=_ring(num_satellites),
                                   decision_sink=sink,
                                   timeline_sink=timeline_sink)
    return cfg, result, timeline_sink


def _persist(tmp_path, cfg, result, timeline_rows=None):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "ledgers.json").write_text(json.dumps(result), encoding="utf-8")
    (run_dir / "resolved_config.json").write_text(json.dumps(
        {"version": cfg["version"], "config": cfg["config"]}), encoding="utf-8")
    (run_dir / "receipt.json").write_text(json.dumps({
        "schema": "leo-sim-receipt/v5", "run_id": "TEST-RUN",
        "fate_counts": result["fate_counts"],
        "deliveries": result["deliveries"],
    }), encoding="utf-8")
    if timeline_rows is not None:
        (run_dir / "timeline.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in timeline_rows),
            encoding="utf-8")
    return run_dir


# ================================================== 1. input materialization
def _trace_cfg(**demand):
    base = {
        "scenario": {"name": "materialization", "duration_s": 60.0,
                     "time_step_s": 0.1, "num_satellites": 8,
                     "num_planes": 1, "seed": 7},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
        "demand": {"mode": "burst", "offered_mbps": 40.0,
                   "burst_start_s": 10.0, "burst_duration_s": 10.0,
                   "burst_multiplier": 5.0},
        "control_plane": {"enabled": False},
        "routing": {"policy": "hop"},
    }
    base["demand"].update(demand)
    return config_mod.resolve_config(base)


def test_a_declared_burst_that_no_packet_observes_is_reported_not_refused(tmp_path):
    """A window that received no packet compiles; the fact is recorded.

    The window IS inside the emission window, so config validation accepts it --
    correctly, because the declaration is well formed.  The rate is simply low
    enough that this draw put nothing inside it.  Whether an experiment has the
    discriminating power to say anything about the treatment is a question for
    the pre-declared design; a generic trace compiler may report the sample, not
    answer it by refusing to compile, dropping the sample, or drawing another
    seed.
    """
    # The named reproduction from the independent review (2026-09-25): 0.16
    # Mbps in 8 Mbit units puts 3 packets on the wire at seed 1 and none of
    # them inside [10, 20).  A rate so low that the trace is EMPTY would not
    # exercise the case this test exists for.
    resolved = config_mod.resolve_config({
        "scenario": {"name": "zero-packet-burst", "duration_s": 60.0,
                     "time_step_s": 0.1, "num_satellites": 8,
                     "num_planes": 1, "seed": 1},
        "endpoints": {"sites": [{"name": "a", "lat": 0.0, "lon": 0.0},
                                {"name": "b", "lat": 0.0, "lon": 10.0}]},
        "demand": {"mode": "burst", "offered_mbps": 0.16, "burst_start_s": 10.0,
                   "burst_duration_s": 10.0, "burst_multiplier": 5.0},
        "control_plane": {"enabled": False},
        "routing": {"policy": "hop"},
    })
    out = tmp_path / "trace"
    manifest = trace_mod.compile_trace(resolved, str(out))
    rows = trace_mod.load_trace(str(out / "trace.csv"))
    report = trace_mod.materialization_report(resolved, rows)
    burst = report["burst"]
    assert report["problems"] == []
    assert burst["packets_inside_window"] == 0
    assert burst["applied"] is False
    diagnostic = burst["zero_packet_diagnostic"]
    assert diagnostic["observed_packets_in_window"] == 0
    assert diagnostic["scenario_seed"] == resolved["config"]["scenario"]["seed"]
    assert diagnostic["expected_packets_if_burst_applied"] == pytest.approx(
        burst["intensity"]["expected_packets_if_burst_applied"])
    assert "unobservable in this sample" in diagnostic["meaning"]
    assert "design" in diagnostic["where_discriminating_power_is_judged"]
    assert burst["applied_reason"] == "NO_PACKET_IN_WINDOW"
    # the sample was neither dropped nor re-drawn
    assert diagnostic["emitted_packets"] == len(rows)
    # …and the emitted count is the trace's, not the window's (an independent
    # review caught this field reporting 0 for a 3-packet trace)
    assert burst["intensity"]["raw_sample_preserved"]["all_emitted"] == len(rows)
    assert len(rows) > 0
    assert (out / "trace.csv").exists() and (out / "manifest.json").exists()
    assert manifest["offered_packets"] == len(rows)


def test_materialization_report_checks_size_endpoints_and_window():
    # No burst declaration: this test is about size / endpoints / window, and a
    # burst declaration would also have to satisfy the (separately tested)
    # intensity predicate.  A two-packet fixture cannot: it declares 250
    # expected in-window packets, so it would be VIOLATED, not "clean".
    resolved = _trace_cfg(mode="uniform", burst_start_s=None,
                          burst_duration_s=None, burst_multiplier=2.0)
    good = [
        {"packet_id": 1, "emit_time_s": 0.0, "src_grid_id": A, "dst_grid_id": B,
         "bits": 8_000_000, "deadline_at_s": None},
        {"packet_id": 2, "emit_time_s": 11.0, "src_grid_id": B, "dst_grid_id": A,
         "bits": 8_000_000, "deadline_at_s": None},
    ]
    report = trace_mod.materialization_report(resolved, good,
                                              declared_cells={A, B})
    assert report["problems"] == []
    assert report["packets"] == 2
    assert report["burst"] is None
    assert report["packet_bits"]["observed_distinct"] == [8_000_000]

    def broken(**over):
        rows = [dict(entry) for entry in good]
        rows[0].update(over)
        return rows

    with pytest.raises(trace_mod.TraceError, match="packet sizes"):
        trace_mod.materialization_report(
            resolved, broken(bits=1_000), declared_cells={A, B})
    with pytest.raises(trace_mod.TraceError, match="same aggregate cell"):
        trace_mod.materialization_report(
            resolved, broken(dst_grid_id=A), declared_cells={A, B})
    with pytest.raises(trace_mod.TraceError,
                       match=r"outside the resolved\s+endpoint set"):
        trace_mod.materialization_report(
            resolved, broken(src_grid_id="not-a-declared-cell"),
            declared_cells={A, B})


def test_a_real_burst_trace_reports_where_its_packets_actually_land(tmp_path):
    """The positive control: the report is not vacuous."""
    resolved = _trace_cfg()
    out = tmp_path / "trace"
    manifest = trace_mod.compile_trace(resolved, str(out))
    rows = trace_mod.load_trace(str(out / "trace.csv"))
    report = trace_mod.materialization_report(
        resolved, rows, declared_cells={A, B})
    assert manifest["provenance_contract"]["traffic_transform"]["burst"] is not None
    assert report["burst"]["transform"]["mismatched_probes"] == 0
    assert report["burst"]["packets_inside_window"] > 0
    assert report["emission"]["packets_after_emission_end"] == 0


# ================================================ 2. compile-time interception
EXPERIMENT_ID = "EXP-LEO-V2-PRE-TRUST"


def _matrix_request(primary_metric="delivery_rate", *, design=None,
                    treatment_overrides=None, available_interval=None,
                    second_override=None):
    common = {
        "scenario": {"duration_s": 1.0, "num_satellites": 1, "num_planes": 1},
        "endpoints": {"sites": [{"name": "a", "lat": 0.0, "lon": 0.0},
                                {"name": "b", "lat": 0.0, "lon": 10.0}]},
        "control_plane": {"enabled": False},
        "routing": {"policy": "oracle"},
    }
    if available_interval is not None:
        common["execution"] = {
            "available_capacity_interval_s": available_interval}
    overrides = treatment_overrides or {"routing": {"policy": "hop"}}
    if second_override is not None:
        overrides = {**overrides, **second_override}
    request = {
        "schema": matrix.MATRIX_REQUEST_SCHEMA,
        "experiment_id": EXPERIMENT_ID,
        "runtime_kind": "leo_sim_v2",
        "work_finalization": "CODE/work/WP-PRE-TRUST/finalization.json",
        "common_config": common,
        "arms": [
            {"arm_id": "control", "config_overrides": {},
             "intervention_paths": []},
            {"arm_id": "treatment", "config_overrides": overrides,
             "intervention_paths": sorted(_leaf_paths(overrides))},
        ],
        "cells": [
            {"run_id": f"{EXPERIMENT_ID}-control-s42", "arm_id": "control",
             "phase": "non_learning", "trace_seed": 42, "learning_seed": None,
             "pairing_key": "pair-42", "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None,
                                     "source_sha256": None}},
            {"run_id": f"{EXPERIMENT_ID}-treatment-s42", "arm_id": "treatment",
             "phase": "non_learning", "trace_seed": 42, "learning_seed": None,
             "pairing_key": "pair-42", "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None,
                                     "source_sha256": None}},
        ],
        "acceptance": {"min_delivered_packets": 0, "min_multisat_deliveries": 0,
                       "require_data_isl": False, "require_control_delivery": False},
        "analysis": {
            "analysis_id": "AN-LEO-V2-PRE-TRUST",
            "primary_metric": primary_metric,
            "estimand": "paired difference",
            "paired_by": ["pairing_key"],
            "planned_contrasts": [{"name": "treatment_minus_control",
                                   "left_arm": "treatment",
                                   "right_arm": "control",
                                   "estimand": "paired difference"}],
        },
        "claim_boundary": {"can_claim": ["fixture"], "cannot_claim": ["anything"]},
    }
    if design is not None:
        request["design"] = design
    return request


def _leaf_paths(value, prefix=""):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaf_paths(item, f"{prefix}{key}.")
    else:
        yield prefix[:-1]


def _compile(tmp_path, request):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = tmp_path / "EXPERIMENTS" / request["experiment_id"]
    return matrix.compile_matrix_experiment(source, out, project_root=tmp_path)


def test_a_primary_metric_the_analyzer_cannot_compute_never_compiles(tmp_path):
    """The three counter-example metrics are catalog ids marked primary.

    Before this gate they compiled to COMPILED_REVIEW_REQUIRED with errors=[]
    and failed only inside v2_analysis, after every cell had run.
    """
    for metric in ("p95_e2e_latency", "packet_loss_rate", "mean_e2e_latency"):
        assert metric not in SUPPORTED_PRIMARY_METRICS
        with pytest.raises(matrix.MatrixError, match="not one the V2 analyzer"):
            _compile(tmp_path / metric, _matrix_request(metric))
    report = _compile(tmp_path / "ok", _matrix_request("delivery_rate"))
    assert report["status"] == "COMPILED_REVIEW_REQUIRED"
    assert report["errors"] == []


def test_the_canonical_vocabulary_matches_what_the_analyzer_dispatches():
    """Membership is checked, not asserted in a comment.

    Every id in SUPPORTED_PRIMARY_METRICS must really be dispatchable, and an
    id outside it must be rejected by the analyzer itself - otherwise the
    compiler could refuse a metric the analyzer would happily have computed, or
    accept one it cannot.
    """
    receipt = {"totals": {"delivered_bits": 10.0, "terminal_loss_bits": 1.0,
                          "in_system_bits_at_stop": 0.0},
               "fate_counts": {"DELIVERED": 2, "NO_ROUTE": 1}}
    ledgers = {"congestion_metrics": {
        "packets": {"1": {"e2e_s": 1.0, "total_queue_wait_s": 0.1,
                          "tx_s": 0.2, "prop_s": 0.3}},
        "links": {"isl:0:1": {"stage": "isl", "utilization": 0.5}},
        "access_admission_rate": 1.0,
        "network_delivery_rate_by_horizon": 1.0,
    }}
    for metric in sorted(SUPPORTED_PRIMARY_METRICS):
        value = v2_analysis._metric_from_result(receipt, ledgers, metric)
        assert isinstance(value, float), metric
    with pytest.raises(v2_analysis.V2AnalysisError,
                       match="unsupported V2 primary metric"):
        v2_analysis._metric_from_result(receipt, ledgers, "p95_e2e_latency")
    assert UTILIZATION_PRIMARY_METRICS <= SUPPORTED_PRIMARY_METRICS


def test_a_utilization_endpoint_without_capacity_sampling_never_compiles(tmp_path):
    for metric in sorted(UTILIZATION_PRIMARY_METRICS):
        with pytest.raises(matrix.MatrixError,
                           match="does not exist unless available capacity"):
            _compile(tmp_path / metric,
                     _matrix_request(metric, available_interval=None))
    report = _compile(tmp_path / "sampled",
                      _matrix_request("link_utilization_mean",
                                      available_interval=0.5))
    assert report["status"] == "COMPILED_REVIEW_REQUIRED"


def test_a_declared_single_factor_claim_is_checked_against_the_resolved_cells(tmp_path):
    two_factor = {"routing": {"policy": "hop"},
                  "links": {"isl_rate_mbps": 20.0}}
    design = {"one_change_policy": "strict",
              "factor_changed": ["routing.policy"]}
    with pytest.raises(matrix.MatrixError,
                       match="single-factor claim is not supported"):
        _compile(tmp_path / "liar",
                 _matrix_request(design=design, treatment_overrides=two_factor))

    # the same two-factor treatment without the claim still compiles, but the
    # policy is then explicit rather than implied
    report = _compile(tmp_path / "honest",
                      _matrix_request(design={
                          "one_change_policy": "exploratory_multi_factor",
                          "factor_changed": ["routing.policy",
                                             "links.isl_rate_mbps"]},
                          treatment_overrides=two_factor))
    check = report["design_accounting"]["one_change_policy_check"]
    assert check["observed_changed_paths_by_contrast"] == {
        "treatment_minus_control": ["links.isl_rate_mbps", "routing.policy"]}
    assert check["single_factor_verified"] is False

    # a genuine single-factor contrast passes and is recorded
    report = _compile(tmp_path / "single", _matrix_request(design=design))
    check = report["design_accounting"]["one_change_policy_check"]
    assert check["observed_changed_paths_by_contrast"] == {
        "treatment_minus_control": ["routing.policy"]}
    assert check["single_factor_verified"] is True
    runbook = (tmp_path / "single" / "EXPERIMENTS" / EXPERIMENT_ID
               / "RUNBOOK.md").read_text(encoding="utf-8")
    assert "One-change policy" in runbook

    # A factor cannot be smuggled in through a cell either: a cell override
    # outside the stochastic-identity set is refused outright, because the
    # cell-level override is not part of any arm's declared intervention.
    request = _matrix_request(design=design)
    request["cells"][1]["config_overrides"] = {"links": {"isl_rate_mbps": 20.0}}
    with pytest.raises(matrix.MatrixError, match="undeclared intervention path"):
        _compile(tmp_path / "cell-hidden", request)


def test_a_request_without_a_design_block_derives_exactly_what_it_did_before(tmp_path):
    """Declaring nothing must not change any derived document."""
    report = _compile(tmp_path, _matrix_request())
    accounting = report["design_accounting"]
    assert set(accounting) == {
        "schema", "planned_cells", "unique_resolved_configurations",
        "exact_reexecution_cells", "exact_reexecution_groups",
        "independent_condition_rule"}


# ============================================== 3. independent verification
def _stalled_without_coverage():
    """isl:0:1 is covered by the sampler; isl:1:2 stalled and is not."""
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 1000},
        {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue_id": 0,
         "queue": "isl", "link_id": "isl:1:2"},
        {"kind": "service_start", "pid": 1, "at": 0.0, "stage": "isl",
         "link_id": "isl:1:2", "rate_bps": 1000.0, "bits": 1000,
         "queue_id": 0},
        {"kind": "delivered", "pid": 1, "at": 1.0},
    ]
    windows = [{"pid": 1, "stage": "isl", "link_id": "isl:1:2", "start": 0.0,
                "end": 1.0, "rate_bps": 1000.0, "capacity_bits": 1000.0,
                "served_bits": 0.0, "bits": 1000, "outcome": "stalled"}]
    available = [{"stage": "isl", "link_id": "isl:0:1", "start": 0.0,
                  "end": 1.0, "rate_bps": 1000.0, "capacity_bits": 1000.0}]
    return events, windows, available


def test_a_link_without_a_denominator_publishes_no_utilization():
    events, windows, available = _stalled_without_coverage()
    report = indep.recompute_link_utilization(events, windows, available)
    row = report["isl:1:2"]
    assert row["status"] == indep.STATUS_DEGENERATE
    assert row["utilization"] is None
    assert row["available_capacity_bits"] is None
    assert row["available_samples"] == 0
    # a link that DID serve something without coverage stays a hard error
    served, _, _ = _stalled_without_coverage()
    served[2]["rate_bps"] = 1000.0
    served_windows = [dict(windows[0], served_bits=1000.0, outcome="ok")]
    with pytest.raises(indep.IndependentMetricsError,
                       match="no available-capacity coverage"):
        indep.recompute_link_utilization(served, served_windows, available)


def test_recompute_publishes_utilization_only_with_a_denominator(tmp_path):
    cfg, result, _ = _run(interval=None)
    run_dir = _persist(tmp_path / "unsampled", cfg, result)
    report = recompute.recompute_run(run_dir)
    summary = report["link_utilization"]["summary"]
    assert summary["status"] == indep.STATUS_DEGENERATE
    assert summary["link_utilization_mean"] is None
    assert summary["degenerate_links"]
    assert report["checks"][1]["id"] == "R2"
    assert report["checks"][1]["ok"] is False
    assert report["ok"] is False
    # no link row claims a number either
    for link in report["link_utilization"]["links"].values():
        assert link["utilization"] is None


def test_recompute_reports_a_sampled_run_as_a_measurement(tmp_path):
    cfg, result, _ = _run(interval=1.0)
    run_dir = _persist(tmp_path / "sampled", cfg, result)
    report = recompute.recompute_run(run_dir)
    summary = report["link_utilization"]["summary"]
    assert summary["status"] == indep.STATUS_OK
    assert summary["link_utilization_mean"] is not None
    assert report["production_cross_check"]["mismatch_total"] == 0
    assert report["delay_decomposition"]["ok"] is True


def test_recompute_refuses_to_name_node_time_as_decision_time(tmp_path):
    """A run whose config enables F2 but whose timeline is missing or empty."""
    cfg, result, _ = _run(interval=1.0)
    run_dir = _persist(tmp_path / "no-timeline", cfg, result)
    payload = json.loads((run_dir / "resolved_config.json").read_text())
    payload["config"]["execution"]["node_process_delay_s"] = 0.05
    (run_dir / "resolved_config.json").write_text(json.dumps(payload))
    with pytest.raises(recompute.RecomputeError, match="requires the timeline"):
        recompute.recompute_run(run_dir)

    empty = tmp_path / "empty-timeline"
    empty.mkdir(parents=True)
    for name in ("ledgers.json", "resolved_config.json", "receipt.json"):
        (empty / name).write_bytes((run_dir / name).read_bytes())
    (empty / "timeline.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(recompute.RecomputeError, match="no node_process span"):
        recompute.recompute_run(empty)


def test_recompute_separates_node_processing_from_decision_computation(tmp_path):
    cfg, result, timeline = _run(interval=1.0, delay=0.05, node_delay=0.05,
                                 timeline=True)
    assert timeline, "the run must have produced a timeline sink"
    run_dir = _persist(tmp_path / "f2", cfg, result, timeline_rows=timeline)
    report = recompute.recompute_run(run_dir)
    separation = report["f2_separation"]
    assert separation["node_process_s"] > 0.0
    assert separation["unlabelled_decision_compute_s"] == pytest.approx(
        separation["labelled_decision_compute_s"]
        + separation["node_process_s"], rel=1e-9)
    assert report["checks"][3]["id"] == "R4"
    assert report["checks"][3]["ok"] is True
    assert report["delay_decomposition"]["ok"] is True
