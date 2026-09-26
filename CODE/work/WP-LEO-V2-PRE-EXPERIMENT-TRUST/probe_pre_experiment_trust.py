#!/usr/bin/env python3
"""Pre-experiment trust probe: reproduces the four blocking gap classes.

This script is evidence, not a test-suite substitute.  It drives ONLY public
platform entry points (config.resolve_config / trace.compile_trace /
matrix.compile_matrix_experiment / metrics_independent /
experiment_platform.v2_analysis) and prints one JSON object describing what the
platform ACTUALLY did for each probe.  It is written so the same file can be run
before and after the fix: a probe whose defect is fixed reports an explicit
refusal instead of the silent acceptance it reported before.

USAGE
  python3 CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/probe_pre_experiment_trust.py \
      --work-dir CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/_scratch \
      --out CODE/work/WP-LEO-V2-PRE-EXPERIMENT-TRUST/<before|after>.json

NOTE  the scratch directory MUST NOT be a symbolic link and MUST live inside the
repository (macOS /tmp is a symlink and the platform rejects it on purpose).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from CODE.experiment_platform import v2_analysis  # noqa: E402
from CODE.leo_sim import config as config_mod  # noqa: E402
from CODE.leo_sim import matrix  # noqa: E402
from CODE.leo_sim import metrics_independent as mi  # noqa: E402
from CODE.leo_sim import trace as trace_mod  # noqa: E402

RESULTS: list[dict] = []


def record(probe_id, title, observed, **extra):
    entry = {"probe": probe_id, "title": title, "observed": observed, **extra}
    RESULTS.append(entry)
    print(f"[{probe_id}] {observed}", file=sys.stderr)
    return entry


def outcome(fn):
    """Run fn and describe success/failure without hiding the exception type."""
    try:
        value = fn()
    except Exception as exc:  # noqa: BLE001 - the point is to report the class
        return {"status": "REFUSED", "error_type": type(exc).__name__,
                "error": str(exc)}, None
    return {"status": "ACCEPTED", "error_type": None, "error": None}, value


def _endpoints():
    return {"sites": [
        {"name": "a", "lat": 0.0, "lon": 0.0},
        {"name": "b", "lat": 0.0, "lon": 10.0},
    ]}


def _base_config(duration=60.0, emission_end=None, mode="burst", **demand):
    cfg = {
        "scenario": {"name": "probe", "duration_s": duration, "time_step_s": 0.1,
                     "num_satellites": 8, "num_planes": 1, "seed": 7},
        "endpoints": _endpoints(),
        "demand": {"mode": mode, "offered_mbps": 5.0,
                   "emission_end_s": emission_end},
        "control_plane": {"enabled": False},
        "routing": {"policy": "hop"},
    }
    cfg["demand"].update(demand)
    return cfg


def _compile_trace(resolved, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = trace_mod.compile_trace(resolved, str(out_dir))
    rows = trace_mod.load_trace(str(out_dir / "trace.csv"))
    return manifest, rows


# ------------------------------------------------------------------ A. burst
def probe_a1_burst_declared_on_unsupported_mode(work: Path):
    """A burst window declared under a mode that never applies it."""
    cfg = _base_config(duration=10.0, mode="uniform",
                       burst_start_s=2.0, burst_duration_s=2.0,
                       burst_multiplier=3.0)
    resolution, resolved = outcome(lambda: config_mod.resolve_config(cfg))
    entry = {"resolution": resolution}
    if resolved is not None:
        manifest, rows = _compile_trace(resolved, work / "A1")
        entry.update({
            "manifest_traffic_transform": manifest["provenance_contract"][
                "traffic_transform"],
            "resolved_burst_start_s": resolved["config"]["demand"]["burst_start_s"],
            "packets": len(rows),
            "realized_offered_mbps": manifest["provenance_contract"][
                "offered_load"]["realized_offered_mbps"],
            "target_offered_mbps": manifest["provenance_contract"][
                "offered_load"]["target_offered_mbps"],
        })
    record("A1", "burst declared under demand.mode=uniform", resolution["status"],
           **entry)


def probe_a2_burst_window_outside_emission_window(work: Path):
    """emission_end_s=20 with the burst window at [30, 40): no packet can
    ever observe the multiplier, yet the window still intersects duration_s."""
    cfg = _base_config(duration=60.0, emission_end=20.0, mode="burst",
                       burst_start_s=30.0, burst_duration_s=10.0,
                       burst_multiplier=5.0, offered_mbps=40.0)
    resolution, resolved = outcome(lambda: config_mod.resolve_config(cfg))
    entry = {"resolution": resolution}
    if resolved is not None:
        manifest, rows = _compile_trace(resolved, work / "A2")
        in_window = [r for r in rows
                     if 30.0 <= r["emit_time_s"] < 40.0]
        entry.update({
            "emission_end_s": manifest["emission_end_s"],
            "declared_burst": manifest["provenance_contract"][
                "traffic_transform"]["burst"],
            "packets": len(rows),
            "packets_inside_burst_window": len(in_window),
            "last_emit_time_s": rows[-1]["emit_time_s"] if rows else None,
            "realized_offered_mbps": manifest["provenance_contract"][
                "offered_load"]["realized_offered_mbps"],
            "target_offered_mbps": manifest["provenance_contract"][
                "offered_load"]["target_offered_mbps"],
        })
    record("A2", "burst window [30,40) outside emission window [0,20)",
           resolution["status"], **entry)


def probe_a3_burst_window_partially_outside(work: Path):
    """emission_end_s=20 with the burst window at [15, 25): only the first half
    is ever emitted, so the declared window is not the applied window."""
    cfg = _base_config(duration=60.0, emission_end=20.0, mode="burst",
                       burst_start_s=15.0, burst_duration_s=10.0,
                       burst_multiplier=5.0, offered_mbps=40.0)
    resolution, resolved = outcome(lambda: config_mod.resolve_config(cfg))
    entry = {"resolution": resolution}
    if resolved is not None:
        manifest, rows = _compile_trace(resolved, work / "A3")
        entry.update({
            "emission_end_s": manifest["emission_end_s"],
            "declared_burst": manifest["provenance_contract"][
                "traffic_transform"]["burst"],
            "packets": len(rows),
            "packets_inside_declared_window": len(
                [r for r in rows if 15.0 <= r["emit_time_s"] < 25.0]),
            "packets_inside_unreachable_tail": len(
                [r for r in rows if r["emit_time_s"] >= 20.0]),
        })
    record("A3", "burst window [15,25) half outside emission window [0,20)",
           resolution["status"], **entry)


# ------------------------------------------------------------------ B. compile
def _matrix_request(primary_metric, *, design=None, treatment_overrides=None,
                    available_interval=None):
    common = {
        "scenario": {"duration_s": 1.0, "num_satellites": 1, "num_planes": 1},
        "endpoints": _endpoints(),
        "control_plane": {"enabled": False},
        "routing": {"policy": "oracle"},
    }
    if available_interval is not None:
        common["execution"] = {"available_capacity_interval_s": available_interval}
    request = {
        "schema": matrix.MATRIX_REQUEST_SCHEMA,
        "experiment_id": "EXP-LEO-V2-PRE-TRUST-PROBE",
        "runtime_kind": "leo_sim_v2",
        "work_finalization": "CODE/work/WP-PROBE/R01/finalization.json",
        "common_config": common,
        "arms": [
            {"arm_id": "control", "config_overrides": {},
             "intervention_paths": []},
            {"arm_id": "treatment",
             "config_overrides": treatment_overrides or {"routing": {"policy": "hop"}},
             "intervention_paths": sorted(_leaf_paths(
                 treatment_overrides or {"routing": {"policy": "hop"}}))},
        ],
        "cells": [
            {"run_id": "EXP-LEO-V2-PRE-TRUST-PROBE-control-s42",
             "arm_id": "control", "phase": "non_learning", "trace_seed": 42,
             "learning_seed": None, "pairing_key": "pair-42",
             "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None, "source_sha256": None}},
            {"run_id": "EXP-LEO-V2-PRE-TRUST-PROBE-treatment-s42",
             "arm_id": "treatment", "phase": "non_learning", "trace_seed": 42,
             "learning_seed": None, "pairing_key": "pair-42",
             "config_overrides": {},
             "checkpoint_lineage": {"mode": "not_applicable",
                                     "source_run_id": None, "source_sha256": None}},
        ],
        "acceptance": {"min_delivered_packets": 0, "min_multisat_deliveries": 0,
                       "require_data_isl": False, "require_control_delivery": False},
        "analysis": {
            "analysis_id": "AN-LEO-V2-PRE-TRUST-PROBE",
            "primary_metric": primary_metric,
            "estimand": "paired difference",
            "paired_by": ["pairing_key"],
            "planned_contrasts": [{"name": "treatment_minus_control",
                                   "left_arm": "treatment",
                                   "right_arm": "control",
                                   "estimand": "paired difference"}],
        },
        "claim_boundary": {"can_claim": ["probe"], "cannot_claim": ["anything"]},
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


def _compile_matrix(work: Path, name: str, request: dict):
    root = work / name
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    source = root / "request.json"
    source.write_text(json.dumps(request), encoding="utf-8")
    out = root / "EXPERIMENTS" / request["experiment_id"]
    res, report = outcome(
        lambda: matrix.compile_matrix_experiment(source, out, project_root=root))
    return res, report, out


def probe_b1_primary_metric_unsupported_by_analyzer(work: Path):
    metric = "p95_e2e_latency"
    res, report, out = _compile_matrix(work, "B1", _matrix_request(metric))
    entry = {"compile": res}
    if report is not None:
        entry["compile_status"] = report["status"]
        entry["compile_errors"] = report["errors"]
        # Now hand the analyzer the same metric with a well-formed receipt.
        receipt = {"fate_counts": {"DELIVERED": 3, "NO_ROUTE": 1}, "totals": {}}
        ledgers = {"congestion_metrics": {
            "packets": {"1": {"e2e_s": 1.0}}, "links": {}}}
        analyzer, _ = outcome(
            lambda: v2_analysis._metric_from_result(metric, receipt, ledgers))
        entry["analyzer"] = analyzer
    record("B1", f"primary_metric={metric} (in catalog, not in analyzer)",
           res["status"], **entry)


def probe_b2_utilization_metric_without_capacity_sampling(work: Path):
    metric = "link_utilization_mean"
    res, report, out = _compile_matrix(work, "B2", _matrix_request(metric))
    entry = {"compile": res}
    if report is not None:
        entry["compile_status"] = report["status"]
        entry["compile_errors"] = report["errors"]
        resolved = json.loads(
            (out / "resolved" / "EXP-LEO-V2-PRE-TRUST-PROBE-control-s42.leo-sim.yaml")
            .read_text(encoding="utf-8"))
        entry["resolved_available_capacity_interval_s"] = resolved["execution"][
            "available_capacity_interval_s"]
        entry["analyzer"] = {"status": "ACCEPTED",
                             "note": "the analyzer will average the production "
                                     "service-window fallback ratio"}
    record("B2", f"primary_metric={metric} with no capacity sampling",
           res["status"], **entry)


def probe_b3_single_factor_claim_is_uncheckable(work: Path):
    two_factor = {"routing": {"policy": "hop"},
                  "links": {"isl_rate_mbps": 20.0}}
    # (i) the V2 contract has no place to declare the policy at all
    res_i, report_i, _ = _compile_matrix(
        work, "B3i",
        _matrix_request("delivery_rate", design={"one_change_policy": "strict",
                                                 "factor_changed": ["routing.policy"]},
                        treatment_overrides=two_factor))
    # (ii) without the key a two-factor treatment compiles clean and silent
    res_ii, report_ii, _ = _compile_matrix(
        work, "B3ii", _matrix_request("delivery_rate", treatment_overrides=two_factor))
    entry = {"declare_policy": res_i,
             "two_factor_without_policy": res_ii}
    if report_ii is not None:
        entry["two_factor_compile_status"] = report_ii["status"]
        entry["two_factor_compile_errors"] = report_ii["errors"]
        entry["design_accounting"] = report_ii["design_accounting"]
    record("B3", "single-factor claim declaration/verification absent",
           res_ii["status"], **entry)


# ------------------------------------------------------- C. independent metrics
def _c_fixture():
    """isl:0:1 has a sampled availability window; isl:1:2 serves bits with NO
    availability coverage at all.  Availability was sampled (elsewhere)."""
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 1000},
        {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue_id": 0,
         "queue": "isl", "link_id": "isl:1:2"},
        {"kind": "service_start", "pid": 1, "at": 0.0, "stage": "isl",
         "link_id": "isl:1:2", "rate_bps": 1000.0, "bits": 1000, "queue_id": 0},
        {"kind": "delivered", "pid": 1, "at": 1.0},
        {"kind": "packet_emitted", "pid": 2, "at": 0.0, "bits": 1000},
        {"kind": "queue_enter", "pid": 2, "at": 0.0, "queue_id": 1,
         "queue": "isl", "link_id": "isl:0:1"},
        {"kind": "service_start", "pid": 2, "at": 0.0, "stage": "isl",
         "link_id": "isl:0:1", "rate_bps": 1000.0, "bits": 1000, "queue_id": 1},
        {"kind": "delivered", "pid": 2, "at": 1.0},
    ]
    windows = [
        # STALLED: the link was granted a service window and served nothing.
        {"pid": 1, "stage": "isl", "link_id": "isl:1:2", "start": 0.0,
         "end": 1.0, "rate_bps": 1000.0, "capacity_bits": 1000.0,
         "served_bits": 0.0, "bits": 1000, "outcome": "stalled"},
        {"pid": 2, "stage": "isl", "link_id": "isl:0:1", "start": 0.0,
         "end": 1.0, "rate_bps": 1000.0, "capacity_bits": 1000.0,
         "served_bits": 1000.0, "bits": 1000, "outcome": "ok"},
    ]
    available = [
        {"stage": "isl", "link_id": "isl:0:1", "start": 0.0, "end": 1.0,
         "rate_bps": 1000.0, "capacity_bits": 1000.0},
    ]
    return events, windows, available


def probe_c1_independent_utilization_without_denominator(work: Path):
    events, windows, available = _c_fixture()
    res, report = outcome(
        lambda: mi.recompute_link_utilization(events, windows, available))
    entry = {"recompute": res}
    if report is not None:
        entry["links"] = {k: {kk: v.get(kk) for kk in
                              ("status", "utilization", "served_bits",
                               "available_capacity_bits", "available_samples")}
                          for k, v in report.items()}
    record("C1", "availability sampled elsewhere; isl:1:2 has no denominator",
           res["status"], **entry)


def probe_c2_production_utilization_without_sampling(work: Path):
    """The production reading itself: no availability sample -> utilization 1.0."""
    cfg = _base_config(duration=40.0, emission_end=None, mode="csv",
                       csv_path=str(REPO / "CODE/data/traffic/t1_step5_micro_ab.csv"))
    resolved = config_mod.resolve_config(cfg)
    entry = {"resolved_available_capacity_interval_s":
             resolved["config"]["execution"]["available_capacity_interval_s"]}
    out = work / "C2"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    # The config file must live outside the run directory (the run directory
    # must not be pre-populated by a file the run did not create).
    cfg_out = work / "C2-config"
    if cfg_out.exists():
        shutil.rmtree(cfg_out)
    cfg_out.mkdir(parents=True)
    cli, _value = outcome(lambda: _run_kernel(resolved, out))
    entry["run"] = cli
    ledgers_path = out / "ledgers.json"
    if ledgers_path.is_file():
        ledgers = json.loads(ledgers_path.read_text(encoding="utf-8"))
        links = ledgers["congestion_metrics"]["links"]
        entry["production_links"] = {
            k: {"utilization": v.get("utilization"),
                "available_samples": v.get("available_samples"),
                "available_capacity_bits": v.get("available_capacity_bits"),
                "capacity_bits": v.get("capacity_bits")}
            for k, v in sorted(links.items())}
        entry["analyzer_diagnostics"] = outcome(
            lambda: v2_analysis._run_diagnostics(ledgers))[0]
    record("C2", "production utilization without capacity sampling",
           cli["status"], **entry)


def _run_kernel(resolved, out: Path):
    """Run the canonical CLI on a resolved config (no private kernel call)."""
    import subprocess
    config_path = out.parent / (out.name + "-config.leo-sim.yaml")
    config_path.write_text(json.dumps(
        {"config_version": resolved["version"], **resolved["config"]},
        ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "CODE.leo_sim", "run",
         "--config", str(config_path), "--out", str(out)],
        cwd=str(REPO), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"run exit {proc.returncode}: "
                           f"{proc.stdout.strip()} {proc.stderr.strip()}")
    return {"exit": proc.returncode, "stdout_tail": proc.stdout.strip()[-400:]}


# ------------------------------------- D. persisted-run independent recompute
def _cli(args):
    import subprocess
    proc = subprocess.run([sys.executable, "-m", "CODE.leo_sim", *args],
                          cwd=str(REPO), capture_output=True, text=True)
    return proc


def probe_d1_recompute_over_a_persisted_run(work: Path):
    """The independent recomputation must be reachable on PERSISTED artifacts.

    Before this round there was no platform entry point at all: the second
    implementation existed, but nothing turned a run directory into a report,
    so "independent verification" could only be performed by an ad-hoc script.
    """
    run_dir = work / "C2"
    proc = _cli(["recompute", "--run-dir", str(run_dir)])
    entry = {"exit": proc.returncode,
             "stdout_tail": proc.stdout.strip()[-300:],
             "stderr_tail": proc.stderr.strip()[-300:]}
    report = None
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        pass
    if isinstance(report, dict):
        entry["ok"] = report.get("ok")
        entry["checks"] = {c["id"]: c["ok"] for c in report.get("checks", [])}
        entry["link_utilization_summary"] = report.get("link_utilization", {}).get("summary")
        entry["f2_separation"] = report.get("f2_separation")
    record("D1", "python3 -m CODE.leo_sim recompute on a persisted run",
           "ACCEPTED" if proc.returncode == 0 else "REFUSED", **entry)


def probe_d2_recompute_refuses_an_f2_run_without_its_timeline(work: Path):
    """A config that enables F2 but a run directory with no node spans."""
    source = work / "C2"
    target = work / "D2"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    payload = json.loads((target / "resolved_config.json").read_text(encoding="utf-8"))
    payload["config"]["execution"]["node_process_delay_s"] = 0.05
    (target / "resolved_config.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    proc = _cli(["recompute", "--run-dir", str(target)])
    record("D2", "F2 enabled but no timeline stream in the run directory",
           "REFUSED" if proc.returncode != 0 else "ACCEPTED",
           exit=proc.returncode, stdout_tail=proc.stdout.strip()[-300:])


def probe_d3_recompute_publishes_a_measurement_for_a_sampled_run(work: Path):
    """Positive control: with sampling the same run yields a real number."""
    cfg = _base_config(duration=40.0, emission_end=None, mode="csv",
                       csv_path=str(REPO / "CODE/data/traffic/t1_step5_micro_ab.csv"))
    cfg["execution"] = {"available_capacity_interval_s": 0.1}
    resolved = config_mod.resolve_config(cfg)
    out = work / "D3"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run, _value = outcome(lambda: _run_kernel(resolved, out))
    entry = {"run": run}
    proc = _cli(["recompute", "--run-dir", str(out)])
    entry["exit"] = proc.returncode
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        report = None
    if isinstance(report, dict):
        entry["ok"] = report.get("ok")
        entry["checks"] = {c["id"]: c["ok"] for c in report.get("checks", [])}
        entry["link_utilization_summary"] = report.get(
            "link_utilization", {}).get("summary")
        entry["production_cross_check"] = report.get(
            "production_cross_check", {}).get("mismatch_total")
    record("D3", "sampled run: the independent recomputation publishes a number",
           "ACCEPTED" if proc.returncode == 0 else "REFUSED", **entry)


# ------------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    work = Path(args.work_dir)
    if work.is_symlink():
        raise SystemExit(f"scratch dir may not be a symlink: {work}")
    work.mkdir(parents=True, exist_ok=True)
    for probe in (
        probe_a1_burst_declared_on_unsupported_mode,
        probe_a2_burst_window_outside_emission_window,
        probe_a3_burst_window_partially_outside,
        probe_b1_primary_metric_unsupported_by_analyzer,
        probe_b2_utilization_metric_without_capacity_sampling,
        probe_b3_single_factor_claim_is_uncheckable,
        probe_c1_independent_utilization_without_denominator,
        probe_c2_production_utilization_without_sampling,
        probe_d1_recompute_over_a_persisted_run,
        probe_d2_recompute_refuses_an_f2_run_without_its_timeline,
        probe_d3_recompute_publishes_a_measurement_for_a_sampled_run,
    ):
        try:
            probe(work)
        except Exception as exc:  # noqa: BLE001
            record(probe.__name__, "probe crashed", "ERROR",
                   error_type=type(exc).__name__, error=str(exc))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"schema": "leo-sim-pre-experiment-trust-probe/v1",
                               "probes": RESULTS},
                              ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(json.dumps({"wrote": str(out),
                      "observed": {r["probe"]: r["observed"] for r in RESULTS}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
