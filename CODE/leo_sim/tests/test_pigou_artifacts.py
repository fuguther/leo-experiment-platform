"""Focused synthetic-evidence tests for Figure 1-8 metric exports.

These tests never start ``leo_sim``.  They write receipt-shaped directories
with hand-checkable packet fates, event ledgers, service windows, available
capacity windows and optional telemetry rows.  The assertions pin the
formulas and missing-evidence semantics, not any research result.
"""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


MODULE_PATH = (Path(__file__).resolve().parents[2]
               / "scripts" / "export_eval_artifacts.py")
SPEC = importlib.util.spec_from_file_location("pigou_export_eval_artifacts", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
ex = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ex
SPEC.loader.exec_module(ex)


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _packet(
        pid: int, emit: float, deadline: float, fate: str = "DELIVERED",
        delivered: float | None = None, path: list[int] | None = None,
) -> dict:
    return {
        "packet_id": pid,
        "emit": emit,
        "deadline": deadline,
        "fate": fate,
        "delivered": delivered,
        "path": path if path is not None else ([0, 1] if fate == "DELIVERED" else None),
        "bits": 100,
    }


def _write_run(
        root: Path,
        name: str,
        packets: list[dict],
        *,
        events: list[dict] | None = None,
        service_windows: list[dict] | None = None,
        available_windows: list[dict] | None = None,
        telemetry: list[dict] | None = None,
        remove_fate_pid: int | None = None,
        trace_sha_override: str | None = None,
) -> tuple[Path, dict]:
    run = root / name
    run.mkdir(parents=True)
    trace_path = run / "trace.csv"
    with trace_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "packet_id", "emit_time_s", "src_grid_id", "dst_grid_id", "bits",
            "deadline_at_s",
        ])
        for item in packets:
            writer.writerow([
                item["packet_id"], item["emit"], f"S{item['packet_id']}", "D",
                item["bits"], item["deadline"],
            ])
    trace_sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()
    fates = {
        str(item["packet_id"]): [item["fate"], item["bits"]]
        for item in packets if item["packet_id"] != remove_fate_pid
    }
    deliveries = {
        str(item["packet_id"]): {
            "delivered_at": item["delivered"],
            "path": item["path"],
        }
        for item in packets
        if item["fate"] == "DELIVERED" and item["delivered"] is not None
    }
    if events is None:
        events = [
            {"kind": "packet_emitted", "pid": item["packet_id"],
             "at": item["emit"], "bits": item["bits"]}
            for item in packets
        ]
    ledger = {
        "packet_fates": fates,
        "deliveries": deliveries,
        "packet_events": events,
        "link_service_windows": service_windows or [],
        "link_available_windows": available_windows or [],
        "stop_time_s": 9.5,
    }
    ledger_bytes = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
    (run / "ledgers.json").write_bytes(ledger_bytes)
    fate_counts = {fate: 0 for fate in ex.ALL_DATA_FATES}
    for fate, _bits in fates.values():
        fate_counts[fate] += 1
    receipt = {
        "schema": "leo-sim-receipt/v6",
        "trace_sha256": trace_sha_override or trace_sha,
        "trace_identity_sha256": "a" * 64,
        "ledgers_sha256": hashlib.sha256(ledger_bytes).hexdigest(),
        "packet_fates": fates,
        "fate_counts": fate_counts,
        "conservation_ok": True,
        "natural_end": True,
        "interrupted": False,
        "error": None,
    }
    (run / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if telemetry is not None:
        (run / "telemetry.jsonl").write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in telemetry),
            encoding="utf-8")
    return run, ledger


def _entry(
        exp: str,
        run: Path,
        root: Path,
        *,
        scene: str = "A",
        seed: int = 101,
        interval: float = 0.1,
        alg: str = "Ours",
        multiplier: float = 1.0,
        train_seed: int = 11,
        variant: str = "full",
        outage: str = "none",
        **extra,
) -> dict:
    row = {
        "exp": exp,
        "scene": scene,
        "seed": seed,
        "interval": interval,
        "alg": alg,
        "multiplier": multiplier,
        "train_seed": train_seed,
        "variant": variant,
        "outage": outage,
        "run_dir": str(run.relative_to(root)),
    }
    row.update(extra)
    return row


def test_goodput_and_recovery_use_emission_time_full_confirmation_and_right_censor(tmp_path):
    packets: list[dict] = []
    pid = 1
    # Pre-pulse PDR = 100%.
    for index in range(15):
        emit = 0.5 + index * 0.1
        packets.append(_packet(pid, emit, emit + 0.05, delivered=emit + 0.01))
        pid += 1
    # Recovery contains twenty failed 100 ms buckets followed by twenty good
    # buckets.  Recovery is therefore confirmed exactly at 5.5 s, T_rec=2.0 s.
    for index in range(40):
        emit = 3.5 + index * 0.1
        if index < 20:
            packets.append(_packet(
                pid, emit, emit + 0.05, fate="DATA_DEADLINE_EXPIRED"))
        else:
            packets.append(_packet(pid, emit, emit + 0.05, delivered=emit + 0.01))
        pid += 1
    run, _ = _write_run(tmp_path, "recovery", packets)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert metrics["goodput_pdr_prepulse_pct"] == pytest.approx(100.0)
    assert metrics["goodput_pdr_recovery_pct"] == pytest.approx(50.0)
    assert metrics["t_rec_s"] == pytest.approx(2.0)
    assert metrics["t_rec_status"] == "COMPUTED"
    assert metrics["rgd_delivery_ratio_seconds"] == pytest.approx(2.0)
    assert metrics["rgd_unit"] == "delivery_ratio_second"


def test_recovery_is_right_censored_without_a_complete_0_8_s_confirmation(tmp_path):
    packets = [
        _packet(1, emit, emit + 0.05, fate="DATA_DEADLINE_EXPIRED")
        for emit in [0.5 + index * 0.1 for index in range(15)]
    ]
    packets = [
        _packet(index + 1, emit, emit + 0.05, delivered=emit + 0.01)
        for index, emit in enumerate([0.5 + i * 0.1 for i in range(15)])
    ]
    next_pid = len(packets) + 1
    for index in range(40):
        emit = 3.5 + index * 0.1
        if index < 33:
            packets.append(_packet(
                next_pid, emit, emit + 0.05, fate="DATA_DEADLINE_EXPIRED"))
        else:
            packets.append(_packet(next_pid, emit, emit + 0.05, delivered=emit + 0.01))
        next_pid += 1
    run, _ = _write_run(tmp_path, "censored", packets)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert metrics["t_rec_s"] is None
    assert metrics["t_rec_right_censor_s"] == pytest.approx(3.2)
    assert metrics["t_rec_status"] == "RIGHT_CENSORED"


def test_incomplete_fates_are_explicitly_blank_not_zero(tmp_path):
    packets = [_packet(1, 0.6, 0.7, delivered=0.65)]
    run, _ = _write_run(tmp_path, "missing-fate", packets, remove_fate_pid=1)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert evidence.status == "INCOMPLETE_FATES"
    assert metrics["goodput_pdr_prepulse_pct"] is None
    assert metrics["prepulse_goodput_status"] == "INCOMPLETE_FATES"


def test_receipt_ledger_hash_mismatch_blocks_packet_metrics(tmp_path):
    packets = [_packet(1, 0.6, 0.7, delivered=0.65)]
    run, _ = _write_run(tmp_path, "bad-ledger-hash", packets)
    ledger_path = run / "ledgers.json"
    ledger_path.write_bytes(ledger_path.read_bytes() + b"\n")
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert evidence.status == "INVALID_LEDGER_HASH"
    assert metrics["prepulse_goodput_status"] == "INVALID_LEDGER_HASH"
    assert metrics["goodput_pdr_prepulse_pct"] is None


def test_zombie_work_includes_failed_downlink_and_expired_denominators(tmp_path):
    packets = [
        _packet(1, 0.6, 1.0, delivered=0.8),
        _packet(2, 0.7, 1.0, fate="DATA_DEADLINE_EXPIRED"),
        _packet(3, 0.8, 1.0, fate="ISL_QUEUE_OVERFLOW"),
    ]
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 0.6, "bits": 100},
        {"kind": "packet_emitted", "pid": 2, "at": 0.7, "bits": 100},
        {"kind": "packet_emitted", "pid": 3, "at": 0.8, "bits": 100},
        {"kind": "propagation_arrival", "pid": 2, "at": 0.9, "prop_id": 7},
    ]
    windows = [
        {"pid": 1, "stage": "isl", "link_id": "isl:0:1", "start": 0.6,
         "end": 0.7, "rate_bps": 1000, "capacity_bits": 100,
         "served_bits": 100, "outcome": "ok"},
        {"pid": 2, "stage": "downlink", "link_id": "gsl:downlink:1:D",
         "start": 0.9, "end": 0.95, "rate_bps": 1000, "capacity_bits": 50,
         "served_bits": 0, "outcome": "failed"},
        {"pid": 3, "stage": "isl", "link_id": "isl:0:2", "start": 0.8,
         "end": 0.9, "rate_bps": 1000, "capacity_bits": 100,
         "served_bits": 100, "outcome": "ok"},
    ]
    run, _ = _write_run(
        tmp_path, "zwr", packets, events=events, service_windows=windows)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert metrics["zombie_work_denominator_capacity_bits"] == pytest.approx(250.0)
    assert metrics["zombie_work_numerator_capacity_bits"] == pytest.approx(150.0)
    assert metrics["zombie_work_service_bits_ratio_pct"] == pytest.approx(60.0)
    assert metrics["dropped_arrival_hop_visits"] == 1
    assert metrics["dropped_hop_evidence_status"] == "COMPUTED"
    assert metrics["path_hop_zwr_status"] == "MISSING_DROPPED_PACKET_PATHS"


def test_dropped_hop_count_is_blank_if_emission_evidence_is_missing(tmp_path):
    packets = [_packet(1, 0.6, 0.7, fate="ISL_QUEUE_OVERFLOW")]
    events = [{"kind": "service_start", "pid": 1, "at": 0.6,
               "stage": "isl", "link_id": "isl:0:1", "queue_id": 1,
               "bits": 100, "rate_bps": 1000}]
    run, _ = _write_run(tmp_path, "hop-missing", packets, events=events)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert metrics["dropped_arrival_hop_visits"] is None
    assert metrics["dropped_hop_evidence_status"] == "INCOMPLETE_EMISSION_EVENTS"


def test_jain_uses_complete_declared_edge_set_including_idle_edges(tmp_path):
    packets = [_packet(1, 2.0, 3.0, delivered=2.5)]
    events = [{"kind": "packet_emitted", "pid": 1, "at": 2.0, "bits": 100}]
    service = [{
        "pid": 1, "stage": "isl", "link_id": "isl:0:1", "start": 2.0,
        "end": 3.5, "rate_bps": 1000, "capacity_bits": 100,
        "served_bits": 100, "outcome": "ok",
    }]
    available = [
        {"stage": "isl", "link_id": "isl:0:1", "start": 2.0, "end": 3.5,
         "rate_bps": 1000, "capacity_bits": 100},
        {"stage": "isl", "link_id": "isl:1:2", "start": 2.0, "end": 3.5,
         "rate_bps": 1000, "capacity_bits": 100},
    ]
    run, _ = _write_run(
        tmp_path, "jain", packets, events=events,
        service_windows=service, available_windows=available)
    entry = _entry(
        "exp2", run, tmp_path, corridor_edges=["isl:0:1", "isl:1:2"])
    evidence = ex._load_run(tmp_path, entry, 0)
    ex._apply_jain([evidence])
    result = evidence.entry["_jain_result"]

    assert result["jain_status"] == "COMPUTED"
    assert result["jain_fairness_index"] == pytest.approx(0.5)
    assert result["corridor_edge_count"] == 2
    assert result["corridor_edge_source"] == "explicit_index_declaration"


def test_jain_missing_available_capacity_is_not_zero(tmp_path):
    packets = [_packet(1, 2.0, 3.0, delivered=2.5)]
    run, _ = _write_run(tmp_path, "jain-missing", packets)
    evidence = ex._load_run(
        tmp_path, _entry("exp2", run, tmp_path, corridor_edges=["isl:0:1"]), 0)
    ex._apply_jain([evidence])
    result = evidence.entry["_jain_result"]

    assert result["jain_fairness_index"] is None
    assert result["jain_status"] == "MISSING_DECLARED_EDGE_CAPACITY"


def test_victim_ratio_pairs_distinct_trace_hash_only_on_exact_recovery_cohort(tmp_path):
    baseline_packets = [_packet(1, 4.0, 5.0, delivered=4.2)]
    burst_packets = [
        _packet(1, 4.0, 5.0, delivered=4.6),
        _packet(2, 2.5, 3.5, delivered=3.1),
    ]
    baseline, _ = _write_run(tmp_path, "baseline", baseline_packets)
    burst, _ = _write_run(tmp_path, "burst", burst_packets)
    baseline_entry = _entry(
        "exp0b", baseline, tmp_path, multiplier=1.0, cohort_id="recovery-a")
    burst_entry = _entry(
        "exp0b", burst, tmp_path, multiplier=2.5, cohort_id="recovery-a")
    batch = [
        ex._load_run(tmp_path, baseline_entry, 0),
        ex._load_run(tmp_path, burst_entry, 1),
    ]
    assert batch[0].trace_sha256 != batch[1].trace_sha256
    ex._attach_exp0b_pairs(batch)
    victim = batch[1].entry["_victim_result"]

    assert victim["victim_cohort_match"] is True
    assert victim["victim_p50_ratio"] == pytest.approx(3.0)
    assert victim["victim_p99_ratio"] == pytest.approx(3.0)


def test_victim_pair_validation_rejects_metadata_mismatch(tmp_path):
    packets = [_packet(1, 4.0, 5.0, delivered=4.2)]
    baseline, _ = _write_run(tmp_path, "baseline-mismatch", packets)
    burst, _ = _write_run(tmp_path, "burst-mismatch", packets)
    baseline = ex._load_run(
        tmp_path, _entry("exp0b", baseline, tmp_path, multiplier=1.0), 0)
    burst = ex._load_run(
        tmp_path, _entry("exp0b", burst, tmp_path, multiplier=2.5,
                         train_seed=22), 1)
    with pytest.raises(ex.ExportValidationError, match="cohort metadata mismatch"):
        ex._attach_exp0b_pairs([baseline, burst])


def test_telemetry_internal_trace_preserves_direction_spread_and_missing_status(tmp_path):
    packets = [_packet(1, 0.6, 1.0, delivered=0.8)]
    telemetry = [{
        "pid": 1, "at": 2.05, "sat": 3, "decision_id": 9,
        "action": 2, "legal": {"N": True, "S": False, "E": True, "W": False},
        "queues": [1, 2, 3, 4], "phi_spill": 0.5, "z": [0, 1],
        "delta_h": [0.1, 0.2, 0.3, 0.4], "ego": [0.2, 0.5, 0.1, 0.3],
        "cvar": [0.2, 0.3, 0.9, 0.1], "actual_aoi": [0.1, 0.2, 0.3, 0.4],
        "direction": "N",
    }]
    run, _ = _write_run(tmp_path, "telemetry", packets, telemetry=telemetry)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    rows = ex._telemetry_rows(evidence, seed_filter=101)

    assert len(rows) == 1
    assert rows[0]["action_direction"] == "E"
    assert rows[0]["ego_argmax_direction"] == "S"
    assert rows[0]["cvar_argmax_direction"] == "E"
    assert rows[0]["ego_cvar_direction_disagreement"] is True
    assert rows[0]["cvar_direction_spread"] == pytest.approx(0.8)
    assert rows[0]["queue_E"] == 3.0
    assert rows[0]["actual_aoi_W"] == 0.4


def test_telemetry_accepts_direction_scoped_actual_aoi(tmp_path):
    packets = [_packet(1, 0.6, 1.0, delivered=0.8)]
    telemetry = [{
        "pid": 1, "at": 2.05, "sat": 3, "decision_id": 9,
        "action": "N", "legal": [True, False, False, False],
        "queues": [0, 0, 0, 0], "phi_spill": 0.1, "z": [0],
        "delta_h": [0, 0, 0, 0], "ego": [0.1, 0.2, 0.3, 0.4],
        "cvar": [0.1, 0.2, 0.3, 0.4], "actual_aoi": 0.25,
        "direction": "S",
    }]
    run, _ = _write_run(tmp_path, "scalar-aoi", packets, telemetry=telemetry)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    row = ex._telemetry_rows(evidence, seed_filter=101)[0]

    assert row["actual_aoi_scalar"] == pytest.approx(0.25)
    assert row["actual_aoi_direction_value"] == pytest.approx(0.25)
    assert row["actual_aoi_S"] is None


def test_export_publishes_all_figure_sources_and_conditional_training_rows(tmp_path):
    packets = [_packet(1, 0.6, 1.0, delivered=0.8)]
    run, _ = _write_run(tmp_path / "runs", "one", packets)
    index = [
        {
            **_entry("exp2", run, tmp_path / "runs", alg="Ours"),
            "run_dir": str(run),
        },
        {
            "exp": "exp1", "scene": "A", "seed": 11, "interval": 0.1,
            "alg": "Ours", "multiplier": 1.0, "train_seed": 11,
            "variant": "full", "outage": "none", "run_dir": str(run),
            "epoch": 1, "train_loss": 0.5,
        },
    ]
    result = ex.export(tmp_path / "exports", index)

    for filename in ex.REQUIRED_FILES:
        assert (tmp_path / "exports" / filename).is_file()
        assert filename in result["row_counts"]
    assert set(result["figure_sources"]) == {str(index) for index in range(1, 9)}
    assert {name for names in result["figure_sources"].values() for name in names} <= set(
        result["filenames"])
    assert "exp1_train_curves.csv" in result["files"]
    training_rows = _csv_rows(tmp_path / "exports" / "exp1_train_curves.csv")
    assert training_rows[0]["status"] == "COMPUTED_EXPLICIT_INDEX_ROW"
    assert float(training_rows[0]["train_loss"]) == pytest.approx(0.5)
    internal_rows = _csv_rows(
        tmp_path / "exports" / "exp2_internal_q_trace_seed101.csv")
    assert internal_rows[0]["status"] == "MISSING_TELEMETRY"


def test_export_omits_training_curve_file_without_explicit_exp1_rows(tmp_path):
    packets = [_packet(1, 0.6, 1.0, delivered=0.8)]
    run, _ = _write_run(tmp_path / "runs", "two", packets)
    result = ex.export(
        tmp_path / "exports",
        [{**_entry("exp2", run, tmp_path / "runs"), "run_dir": str(run)}],
    )

    assert "exp1_train_curves.csv" not in result["files"]
    assert not (tmp_path / "exports" / "exp1_train_curves.csv").exists()


def test_five_trace_summary_weights_runs_equally_not_packets(tmp_path):
    evidences = []
    packet_lists = [
        [_packet(index + 1, 0.6, 1.0, delivered=0.8) for index in range(4)],
        [_packet(1, 0.6, 1.0, delivered=0.8)],
        [_packet(1, 0.601, 1.001, fate="DATA_DEADLINE_EXPIRED")],
        [_packet(1, 0.602, 1.002, fate="DATA_DEADLINE_EXPIRED")],
        [_packet(1, 0.603, 1.003, fate="DATA_DEADLINE_EXPIRED")],
    ]
    for offset, packets in enumerate(packet_lists):
        seed = 101 + offset
        run, _ = _write_run(tmp_path, f"seed-{seed}", packets)
        evidences.append(ex._load_run(
            tmp_path, _entry("exp2", run, tmp_path, seed=seed), offset))
    metrics = {id(evidence): ex._run_metrics(evidence) for evidence in evidences}
    rows = ex._summary_rows(evidences, metrics)

    assert len(rows) == 1
    assert rows[0]["n_traces"] == 5
    assert rows[0]["inference_status"] == "DESCRIPTIVE_5_TRACES"
    # Per-trace PDRs are 100, 100, 0, 0, 0.  A packet-weighted pool would be
    # 4/8 = 50%, which is explicitly not the independent experimental unit.
    assert rows[0]["goodput_pdr_full_pct_mean"] == pytest.approx(40.0)
    assert rows[0]["goodput_pdr_full_pct_n"] == 5
    assert rows[0]["goodput_pdr_full_pct_ci95_low"] < 40.0
    assert rows[0]["goodput_pdr_full_pct_ci95_high"] > 40.0


def test_summary_does_not_average_away_right_censored_recovery(tmp_path):
    evidences = []
    for offset in range(5):
        run, _ = _write_run(
            tmp_path, f"right-censored-{offset}",
            [_packet(1, 0.6 + offset * 0.001, 1.0 + offset * 0.001,
                     delivered=0.8 + offset * 0.001)])
        evidences.append(ex._load_run(
            tmp_path,
            _entry("exp2", run, tmp_path, seed=101 + offset), offset))
    metrics = {id(evidence): ex._run_metrics(evidence) for evidence in evidences}
    for value in metrics.values():
        value.update(t_rec_s=None, t_rec_status="RIGHT_CENSORED")
    rows = ex._summary_rows(evidences, metrics)

    assert rows[0]["t_rec_right_censored_n"] == 5
    assert rows[0]["t_rec_observed_n"] == 0
    assert rows[0]["t_rec_s_mean"] is None
    assert rows[0]["t_rec_summary_status"] == "RIGHT_CENSORED_PRESENT"


def test_cli_accepts_option_form_index_and_out(tmp_path, capsys):
    packets = [_packet(1, 0.6, 1.0, delivered=0.8)]
    run, _ = _write_run(tmp_path / "runs", "cli", packets)
    index = [_entry("exp2", run, tmp_path / "runs")]
    index_path = tmp_path / "index.json"
    index_path.write_text(json.dumps(index) + "\n", encoding="utf-8")
    export_dir = tmp_path / "cli-exports"

    assert ex.main([
        "--index", str(index_path), "--out", str(export_dir)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["root"] == str(export_dir)
    assert (export_dir / "exp2_main_raw.csv").is_file()


def test_compute_resource_summary_uses_burst_interval_not_recovery(tmp_path):
    packets = [
        _packet(1, 2.0, 3.0, delivered=2.2),
        _packet(2, 5.0, 7.0, delivered=7.0),
    ]
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 2.0, "bits": 100},
        {"kind": "packet_emitted", "pid": 2, "at": 5.0, "bits": 100},
        {"kind": "queue_enter", "pid": 1, "at": 2.0, "queue": "isl",
         "link_id": "isl:0:1", "queue_id": 1},
        {"kind": "service_start", "pid": 1, "at": 2.2, "stage": "isl",
         "link_id": "isl:0:1", "queue_id": 1, "bits": 100, "rate_bps": 1000},
        {"kind": "queue_enter", "pid": 2, "at": 5.0, "queue": "isl",
         "link_id": "isl:0:1", "queue_id": 2},
        {"kind": "service_start", "pid": 2, "at": 7.0, "stage": "isl",
         "link_id": "isl:0:1", "queue_id": 2, "bits": 100, "rate_bps": 1000},
    ]
    run, _ = _write_run(tmp_path, "resource-interval", packets, events=events)
    evidence = ex._load_run(
        tmp_path, _entry("exp4_compute", run, tmp_path, variant="full"), 0)
    metrics = {id(evidence): ex._run_metrics(evidence)}
    rows = ex._resource_rows([evidence], metrics)

    assert rows[0]["matched_interval_start_s"] == pytest.approx(2.0)
    assert rows[0]["matched_interval_end_s"] == pytest.approx(3.5)
    assert rows[0]["resource_queue_wait_mean_s_mean"] == pytest.approx(0.2)
    assert rows[0]["resource_queue_wait_status_counts"] == '{"COMPUTED":1}'


def test_scene_b_reports_incomplete_recovery_baseline(tmp_path):
    packets = [_packet(1, 0.6, 1.0, delivered=0.8)]
    run, _ = _write_run(tmp_path, "scene-b", packets)
    evidence = ex._load_run(
        tmp_path, _entry("exp2", run, tmp_path, scene="B"), 0)
    metrics = ex._run_metrics(evidence)

    assert metrics["t_rec_s"] is None
    assert metrics["t_rec_status"] == "MISSING_BASELINE"
    assert metrics["rgd_status"] == "MISSING_BASELINE"


def test_latency_exports_separate_whole_run_from_recovery(tmp_path):
    packets = [
        _packet(1, 0.6, 1.0, delivered=0.8),
        _packet(2, 4.0, 5.0, delivered=5.0),
    ]
    run, _ = _write_run(tmp_path, "latency-windows", packets)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert metrics["e2e_p50_s"] == pytest.approx(0.2)
    assert metrics["recovery_e2e_p50_s"] == pytest.approx(1.0)
    assert metrics["latency_window_start_s"] == pytest.approx(0.2)
    assert metrics["recovery_latency_window_start_s"] == pytest.approx(3.5)


def test_rgd_baseline_is_mean_of_prepulse_time_buckets(tmp_path):
    packets: list[dict] = []
    pid = 1
    for index in range(15):
        emit = 0.5 + index * 0.1
        count = 10 if index == 1 else 1
        for inner in range(count):
            if index == 1:
                packets.append(_packet(
                    pid, emit + inner * 1e-5, emit + 0.05,
                    fate="DATA_DEADLINE_EXPIRED"))
            else:
                packets.append(_packet(
                    pid, emit, emit + 0.05, delivered=emit + 0.01))
            pid += 1
    for index in range(40):
        emit = 3.5 + index * 0.1
        packets.append(_packet(pid, emit, emit + 0.05, delivered=emit + 0.01))
        pid += 1
    run, _ = _write_run(tmp_path, "bucket-baseline", packets)
    evidence = ex._load_run(tmp_path, _entry("exp2", run, tmp_path), 0)
    metrics = ex._run_metrics(evidence)

    assert metrics["goodput_pdr_prepulse_pct"] == pytest.approx(100.0 * 14 / 24)
    assert metrics["prepulse_pdr_fraction"] == pytest.approx(14 / 15)
