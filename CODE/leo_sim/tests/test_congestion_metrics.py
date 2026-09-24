"""Tests for the event-backed congestion and link-utilization metrics."""

import hashlib
import json
from pathlib import Path

import pytest

from CODE.leo_sim import config, metrics
from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row


def _convert_run_to_legacy_v1(out, receipt_schema="leo-sim-receipt/v3"):
    """Build a genuine pre-emission-window artifact, not a schema disguise."""
    out = Path(out)
    manifest = json.loads((out / "manifest.json").read_text())
    for key in ("simulation_horizon_s", "emission_end_s", "drain_s"):
        manifest.pop(key)
    manifest["schema"] = "leo-sim-trace-manifest/v1"
    contract = manifest["provenance_contract"]
    for key in ("simulation_horizon_s", "emission_end_s", "drain_s"):
        contract.pop(key)
    contract["schema"] = "leo-sim-trace-provenance/v1"
    resolved = json.loads((out / "resolved_config.json").read_text())
    resolved["config"]["demand"].pop("emission_end_s")
    (out / "resolved_config.json").write_text(
        json.dumps(resolved, indent=2, sort_keys=True) + "\n")
    input_sha = manifest["input_sha256"]
    manifest["trace_identity_sha256"] = config.legacy_trace_identity_sha256(
        resolved, input_sha)
    (out / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    receipt = json.loads((out / "receipt.json").read_text())
    receipt["schema"] = receipt_schema
    receipt["config_sha256"] = hashlib.sha256(
        json.dumps(resolved["config"], sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    receipt["trace_manifest_sha256"] = hashlib.sha256(
        (out / "manifest.json").read_bytes()).hexdigest()
    receipt["trace_identity_sha256"] = manifest["trace_identity_sha256"]
    receipt.pop("trace_manifest_contract", None)
    receipt.pop("trace_identity_contract", None)
    if receipt_schema == "leo-sim-receipt/v3":
        receipt.pop("congestion_metrics_contract", None)
    (out / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n")


def test_summarize_recomputes_queue_tx_propagation_and_link_utilization():
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
        {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue": "isl",
         "queue_id": 7},
        {"kind": "service_start", "pid": 1, "at": 0.5,
         "stage": "isl", "link_id": "isl:0:1", "queue_id": 7,
         "bits": 100, "rate_bps": 400.0},
        {"kind": "propagation_start", "pid": 1, "at": 0.75,
         "stage": "isl", "link_id": "isl:0:1", "prop_id": 3,
         "delay_s": 0.25},
        {"kind": "propagation_arrival", "pid": 1, "at": 1.0,
         "stage": "isl", "prop_id": 3},
        {"kind": "delivered", "pid": 1, "at": 1.0},
    ]
    windows = [{
        "pid": 1, "stage": "isl", "link_id": "isl:0:1",
        "start": 0.5, "end": 0.75, "rate_bps": 400.0,
        "capacity_bits": 100.0, "served_bits": 100,
        "bits": 100, "outcome": "ok",
    }]

    got = metrics.summarize(events, windows)

    packet = got["packets"]["1"]
    assert packet["queue_wait_s"] == pytest.approx(0.5)
    assert packet["tx_s"] == pytest.approx(0.25)
    assert packet["prop_s"] == pytest.approx(0.25)
    assert packet["e2e_s"] == pytest.approx(1.0)
    link = got["links"]["isl:0:1"]
    assert link["capacity_bits"] == pytest.approx(100.0)
    assert link["served_bits"] == 100
    assert link["utilization"] == pytest.approx(1.0)
    assert got["validation"]["ok"] is True


def test_summarize_v2_recomputes_admission_and_pre_ingress_wait():
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
        {"kind": "propagation_start", "pid": 1, "at": 1.0,
         "stage": "uplink", "link_id": "gsl:uplink:2:src",
         "prop_id": 4, "delay_s": 0.5},
        {"kind": "propagation_arrival", "pid": 1, "at": 1.5,
         "prop_id": 4},
        {"kind": "satellite_ingress", "pid": 1, "at": 1.5,
         "endpoint": "src", "satellite": 2, "bits": 100},
        {"kind": "delivered", "pid": 1, "at": 2.0},
        {"kind": "packet_emitted", "pid": 2, "at": 0.5, "bits": 200},
    ]
    got = metrics.summarize(events, [])
    assert got["schema"] == "leo-sim-congestion-metrics/v2"
    assert got["offered_packets"] == 2
    assert got["admitted_at_satellite_ingress_packets"] == 1
    assert got["delivered_packets"] == 1
    assert got["access_admission_rate"] == pytest.approx(0.5)
    assert got["network_delivery_rate_by_horizon"] == pytest.approx(1.0)
    assert got["packets"]["1"]["admitted_at"] == pytest.approx(1.5)
    assert got["packets"]["1"]["access_wait_s"] == pytest.approx(1.5)


@pytest.mark.parametrize("events, message", [
    ([{"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 1},
      {"kind": "propagation_start", "pid": 1, "at": 0.5,
       "stage": "uplink", "link_id": "gsl:uplink:0:a",
       "prop_id": 2, "delay_s": 0.5},
      {"kind": "propagation_arrival", "pid": 1, "at": 1.0,
       "prop_id": 2},
      {"kind": "satellite_ingress", "pid": 1, "at": 1.0,
       "endpoint": "a", "satellite": 0, "bits": 1},
      {"kind": "satellite_ingress", "pid": 1, "at": 1.1,
       "endpoint": "a", "satellite": 0, "bits": 1}], "duplicate satellite_ingress"),
    ([{"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 1},
      {"kind": "delivered", "pid": 1, "at": 0.5},
      {"kind": "satellite_ingress", "pid": 1, "at": 1.0,
       "endpoint": "a", "satellite": 0, "bits": 1}], "before satellite_ingress"),
])
def test_summarize_v2_rejects_invalid_ingress_order(events, message):
    with pytest.raises(metrics.MetricsError, match=message):
        metrics.summarize(events, [])


def test_summarize_v2_rejects_ingress_without_completed_uplink_arrival():
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 1},
        {"kind": "satellite_ingress", "pid": 1, "at": 1.0,
         "endpoint": "a", "satellite": 0, "bits": 1},
    ]
    with pytest.raises(metrics.MetricsError, match="uplink propagation"):
        metrics.summarize(events, [])


def test_summarize_v2_uses_zero_for_no_admitted_denominators():
    got = metrics.summarize([
        {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 8},
    ], [], access_boundary=True)
    assert got["access_admission_rate"] == 0.0
    assert got["network_delivery_rate_by_horizon"] == 0.0


def test_receipt_v4_rejects_v1_but_legacy_v3_reverifies(tmp_path):
    import hashlib
    import json

    from CODE.leo_sim import receipt, trace

    cfg = make_cfg({
        "scenario": {"duration_s": 0.2},
        "demand": {"mode": "csv", "csv_path": str(tmp_path / "input.csv")},
    })
    (tmp_path / "input.csv").write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.0,0.0,0.0,0.0,10.0,8000000,\n", encoding="utf-8")
    trace_dir = tmp_path / "trace"
    manifest = trace.compile_trace(cfg, str(trace_dir))
    trace_bytes = (trace_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (trace_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(trace_dir / "trace.csv"), horizon_s=0.2,
        max_packets=cfg["config"]["execution"]["max_packets"])
    result = kernel.run_simulation(
        cfg, rows, geometry=StaticGeometry(1, visible=lambda *_: False))
    assert result["congestion_metrics"]["schema"] == "leo-sim-congestion-metrics/v2"
    result["congestion_metrics"] = metrics.summarize(
        result["packet_events"], result["link_service_windows"],
        available_capacity_windows=result["link_available_windows"],
        non_arrival_pids=set())
    assert result["congestion_metrics"]["schema"] == "leo-sim-congestion-metrics/v1"
    out = tmp_path / "run"
    written = receipt.write_run(str(out), cfg, trace_bytes, manifest, result, rows)
    assert written["schema"] == "leo-sim-receipt/v5"
    assert written["congestion_metrics_contract"] == "leo-sim-congestion-metrics/v2"
    errors = receipt.verify_receipt_dir(str(out))
    assert any("schema != receipt contract" in error for error in errors)
    _convert_run_to_legacy_v1(out)
    assert receipt.verify_receipt_dir(str(out)) == []


def test_v4_zero_ingress_downgrade_to_v1_is_rejected_at_receipt_level(tmp_path):
    import hashlib
    import json

    from CODE.leo_sim import receipt, trace

    cfg = make_cfg({
        "scenario": {"duration_s": 0.2},
        "demand": {"mode": "csv", "csv_path": str(tmp_path / "input.csv")},
    })
    (tmp_path / "input.csv").write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.0,0.0,0.0,0.0,10.0,8000000,\n", encoding="utf-8")
    trace_dir = tmp_path / "trace"
    manifest = trace.compile_trace(cfg, str(trace_dir))
    trace_bytes = (trace_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (trace_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(str(trace_dir / "trace.csv"), horizon_s=0.2,
                            max_packets=cfg["config"]["execution"]["max_packets"])
    result = kernel.run_simulation(
        cfg, rows, geometry=StaticGeometry(1, visible=lambda *_: False))
    out = tmp_path / "run"
    receipt.write_run(str(out), cfg, trace_bytes, manifest, result, rows)
    assert receipt.verify_receipt_dir(str(out)) == []
    ledgers = json.loads((out / "ledgers.json").read_text(encoding="utf-8"))
    ledgers["congestion_metrics"] = metrics.summarize(
        ledgers["packet_events"], ledgers["link_service_windows"],
        available_capacity_windows=ledgers["link_available_windows"],
        non_arrival_pids=set())
    (out / "ledgers.json").write_text(
        json.dumps(ledgers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_doc = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    receipt_doc["ledgers_sha256"] = hashlib.sha256(
        (out / "ledgers.json").read_bytes()).hexdigest()
    (out / "receipt.json").write_text(
        json.dumps(receipt_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert errors
    assert any("schema != receipt contract" in error for error in errors)


def test_v4_access_rejected_packet_fake_ingress_is_rejected_at_receipt_level(tmp_path):
    import hashlib
    import json

    from CODE.leo_sim import receipt, trace

    cfg = make_cfg({
        "scenario": {"duration_s": 0.2},
        "demand": {"mode": "csv", "csv_path": str(tmp_path / "input.csv")},
    })
    (tmp_path / "input.csv").write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.0,0.0,0.0,0.0,10.0,8000000,\n", encoding="utf-8")
    trace_dir = tmp_path / "trace"
    manifest = trace.compile_trace(cfg, str(trace_dir))
    trace_bytes = (trace_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (trace_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(str(trace_dir / "trace.csv"), horizon_s=0.2,
                            max_packets=cfg["config"]["execution"]["max_packets"])
    result = kernel.run_simulation(
        cfg, rows, geometry=StaticGeometry(1, visible=lambda *_: False))
    assert result["fates"][1] == "ACCESS_REJECTED"
    out = tmp_path / "run"
    receipt.write_run(str(out), cfg, trace_bytes, manifest, result, rows)
    ledgers = json.loads((out / "ledgers.json").read_text(encoding="utf-8"))
    ledgers["packet_events"].extend([
        {"kind": "propagation_start", "pid": 1, "at": 0.0,
         "stage": "uplink", "link_id": "gsl:uplink:0:fake",
         "prop_id": 999, "delay_s": 0.0},
        {"kind": "propagation_arrival", "pid": 1, "at": 0.0,
         "prop_id": 999},
        {"kind": "satellite_ingress", "pid": 1, "at": 0.0,
         "endpoint": rows[0]["src_grid_id"], "satellite": 0,
         "bits": rows[0]["bits"]},
    ])
    ledgers["congestion_metrics"] = metrics.summarize(
        ledgers["packet_events"], ledgers["link_service_windows"],
        available_capacity_windows=ledgers["link_available_windows"],
        non_arrival_pids=set(), access_boundary=True)
    (out / "ledgers.json").write_text(
        json.dumps(ledgers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_doc = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    receipt_doc["ledgers_sha256"] = hashlib.sha256(
        (out / "ledgers.json").read_bytes()).hexdigest()
    (out / "receipt.json").write_text(
        json.dumps(receipt_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert errors
    assert any("terminal access fate ACCESS_REJECTED" in error for error in errors)
    receipt_doc["schema"] = "leo-sim-receipt/v3"
    del receipt_doc["congestion_metrics_contract"]
    (out / "receipt.json").write_text(
        json.dumps(receipt_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert receipt.verify_receipt_dir(str(out))
    stored = json.loads((out / "ledgers.json").read_text(encoding="utf-8"))
    assert stored["congestion_metrics"] == metrics.summarize(
        stored["packet_events"], stored["link_service_windows"],
        available_capacity_windows=stored["link_available_windows"],
        non_arrival_pids=set())


def test_summarize_uses_idle_physical_capacity_as_utilization_denominator():
    windows = [{
        "pid": 1, "stage": "isl", "link_id": "isl:0:1",
        "start": 0.5, "end": 0.75, "rate_bps": 400.0,
        "capacity_bits": 100.0, "served_bits": 100,
        "bits": 100, "outcome": "ok",
    }]
    available = [{
        "stage": "isl", "link_id": "isl:0:1",
        "start": 0.0, "end": 1.0, "rate_bps": 400.0,
        "capacity_bits": 400.0,
    }]
    got = metrics.summarize([], windows,
                            available_capacity_windows=available)
    link = got["links"]["isl:0:1"]
    assert link["capacity_bits"] == pytest.approx(100.0)
    assert link["available_capacity_bits"] == pytest.approx(400.0)
    assert link["available_time_s"] == pytest.approx(1.0)
    assert link["available_samples"] == 1
    assert link["utilization"] == pytest.approx(0.25)


def test_summarize_rejects_service_above_sampled_available_capacity():
    with pytest.raises(metrics.MetricsError, match="available capacity"):
        metrics.summarize(
            [], [{
                "pid": 1, "stage": "isl", "link_id": "isl:0:1",
                "start": 0.0, "end": 1.0, "rate_bps": 200.0,
                "capacity_bits": 200.0, "served_bits": 200,
                "bits": 200, "outcome": "ok",
            }],
            available_capacity_windows=[{
                "stage": "isl", "link_id": "isl:0:1",
                "start": 0.0, "end": 1.0, "rate_bps": 100.0,
                "capacity_bits": 100.0,
            }])


def test_summarize_rejects_orphan_service_start():
    with pytest.raises(metrics.MetricsError, match="unknown queue_id"):
        metrics.summarize([
            {"kind": "service_start", "pid": 1, "at": 0.0,
             "stage": "isl", "link_id": "isl:0:1", "queue_id": 99,
             "bits": 100, "rate_bps": 400.0},
        ], [])


def test_summarize_accepts_in_flight_packet_without_fabricating_arrival():
    events = [
        {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
        {"kind": "queue_enter", "pid": 1, "at": 0.0, "queue": "isl",
         "queue_id": 7},
        {"kind": "service_start", "pid": 1, "at": 0.5,
         "stage": "isl", "link_id": "isl:0:1", "queue_id": 7,
         "bits": 100, "rate_bps": 400.0},
        {"kind": "propagation_start", "pid": 1, "at": 0.75,
         "stage": "isl", "link_id": "isl:0:1", "prop_id": 3,
         "delay_s": 0.25},
    ]
    windows = [{
        "pid": 1, "stage": "isl", "link_id": "isl:0:1",
        "start": 0.5, "end": 0.75, "rate_bps": 400.0,
        "capacity_bits": 100.0, "served_bits": 100,
        "outcome": "ok",
    }]

    with pytest.raises(metrics.MetricsError, match="unmatched propagation"):
        metrics.summarize(events, windows)

    got = metrics.summarize(events, windows, non_arrival_pids={1})
    packet = got["packets"]["1"]
    assert packet["prop_s"] == pytest.approx(0.0)
    assert "e2e_s" not in packet


def test_receipt_recomputation_uses_fate_qualified_in_flight_set(tmp_path):
    """An in-flight packet at horizon remains verifiable from raw events."""
    import hashlib
    from CODE.leo_sim import receipt, trace

    cfg = make_cfg({
        "scenario": {"duration_s": 0.1},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0},
        ]},
        "demand": {"mode": "csv", "csv_path": str(tmp_path / "input.csv")},
    })
    (tmp_path / "input.csv").write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.099,0.0,0.0,0.0,10.0,8000000,\n",
        encoding="utf-8")
    trace_dir = tmp_path / "trace"
    manifest = trace.compile_trace(cfg, str(trace_dir))
    trace_bytes = (trace_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (trace_dir / "manifest.json").read_bytes()).hexdigest()
    geo = StaticGeometry(2, visible=lambda *_: True,
                         neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                         slant_km=600.0)
    result = kernel.run_simulation(
        cfg, trace.load_trace(str(trace_dir / "trace.csv"),
                              horizon_s=0.1,
                              max_packets=cfg["config"]["execution"]["max_packets"]),
        geometry=geo)
    assert result["fate_counts"]["IN_SYSTEM_AT_STOP"] == 1
    out = tmp_path / "run"
    receipt.write_run(str(out), cfg, trace_bytes, manifest, result,
                      trace.load_trace(str(trace_dir / "trace.csv"),
                                       horizon_s=0.1,
                                       max_packets=cfg["config"]["execution"]["max_packets"]))
    assert receipt.verify_receipt_dir(str(out)) == []


def test_kernel_persists_real_event_metrics_for_a_delivered_packet():
    a = cell(0.0, 0.0)
    b = cell(0.0, 10.0)
    geo = StaticGeometry(1, visible=lambda *_: True)
    result = kernel.run_simulation(
        make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1},
                  "execution": {"available_capacity_interval_s": 1.0}}),
        [row(1, 0.0, a, b)], geometry=geo)

    packet = result["congestion_metrics"]["packets"]["1"]
    assert result["congestion_metrics"]["validation"]["ok"] is True
    assert packet["queue_wait_s"] >= 0.0
    assert packet["tx_s"] > 0.0
    assert packet["prop_s"] > 0.0
    assert packet["e2e_s"] == pytest.approx(
        result["deliveries"][1]["delivered_at"])
    assert result["packet_events"]
    assert result["link_service_windows"]
    assert result["link_available_windows"]
    assert any(v["available_capacity_bits"] > v["capacity_bits"]
               for v in result["congestion_metrics"]["links"].values())


def test_capacity_metric_splits_scripted_visibility_change_inside_interval():
    class Flap(StaticGeometry):
        def __init__(self):
            super().__init__(1, visible=lambda *_: True,
                             gsl_changes=[0.25, 0.75])

        def ground_visible(self, sat_id, lat, lon, t):
            return 0.25 <= t < 0.75

    result = kernel.run_simulation(
        make_cfg({"scenario": {"duration_s": 1.0},
                  "execution": {"available_capacity_interval_s": 1.0}}),
        [row(1, 0.0, cell(0.0, 0.0), cell(0.0, 10.0))],
        geometry=Flap())
    link_id = f"gsl:uplink:0:{cell(0.0, 0.0)}"
    windows = [w for w in result["link_available_windows"]
               if w["link_id"] == link_id]
    assert [(w["start"], w["end"]) for w in windows] == [(0.25, 0.75)]


def test_capacity_metric_includes_retired_isl_generation():
    from CODE.leo_sim.tests.test_dynamic_topology import _cfg, _geometry

    cfg = _cfg()
    cfg["config"]["execution"]["available_capacity_interval_s"] = 1.0
    geo = _geometry()
    # Keep the scripted rematch while making both generations physically
    # usable, matching the in-flight retired-service case under review.
    geo.isl_available = lambda _a, _b, _t: True
    result = kernel.run_simulation(cfg, [], geometry=geo)
    old = [w for w in result["link_available_windows"]
           if w["link_id"] == "isl:0:1"]
    new = [w for w in result["link_available_windows"]
           if w["link_id"] == "isl:0:2"]
    assert [(w["start"], w["end"]) for w in old] == [(0.0, 0.5)]
    assert [(w["start"], w["end"]) for w in new] == [(0.5, 1.0)]


def test_capacity_metric_cuts_a_retired_generation_at_drain_time():
    from CODE.leo_sim.tests.test_dynamic_topology import _cfg, _geometry

    cfg = _cfg()
    cfg["config"]["scenario"]["duration_s"] = 2.0
    cfg["config"]["links"]["isl_rate_mbps"] = 0.004
    cfg["config"]["execution"]["available_capacity_interval_s"] = 0.5
    geo = _geometry()
    geo.isl_available = lambda _a, _b, _t: True
    k = kernel.Kernel(cfg, [], geometry=geo)
    old = k.isls[0]["E"]
    # Keep the old generation in service past the rematch, then let it drain
    # inside a later capacity window.
    cp = kernel.ControlPacket(1, 0, 1, 0.0, 10.0, 1, 5_000, {})
    k.ctrl_ledger.register(cp.iid, cp.bits)
    old.put_ctrl(cp)
    result = k.run()
    old_windows = [w for w in result["link_available_windows"]
                   if w["link_id"] == "isl:0:1"]
    assert old_windows
    assert max(w["end"] for w in old_windows) <= old.drained_at + 1e-9
    assert any(w["end"] == pytest.approx(old.drained_at)
               for w in old_windows)


def test_legacy_v3_v1_delivered_run_without_new_ingress_event_reverifies(tmp_path):
    import hashlib
    import json

    from CODE.leo_sim import receipt, trace

    cfg = make_cfg({
        "scenario": {"duration_s": 2.0},
        "demand": {"mode": "csv", "csv_path": str(tmp_path / "input.csv")},
    })
    (tmp_path / "input.csv").write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.0,0.0,0.0,0.0,10.0,8000000,\n", encoding="utf-8")
    trace_dir = tmp_path / "trace"
    manifest = trace.compile_trace(cfg, str(trace_dir))
    trace_bytes = (trace_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (trace_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(str(trace_dir / "trace.csv"), horizon_s=2.0,
                            max_packets=cfg["config"]["execution"]["max_packets"])
    result = kernel.run_simulation(
        cfg, rows, geometry=StaticGeometry(1, visible=lambda *_: True))
    assert result["fate_counts"]["DELIVERED"] == 1
    assert any(e["kind"] == "satellite_ingress" for e in result["packet_events"])
    result["packet_events"] = [
        e for e in result["packet_events"] if e["kind"] != "satellite_ingress"
    ]
    result["congestion_metrics"] = metrics.summarize(
        result["packet_events"], result["link_service_windows"],
        available_capacity_windows=result["link_available_windows"],
        non_arrival_pids=set())
    assert result["congestion_metrics"]["schema"] == "leo-sim-congestion-metrics/v1"
    out = tmp_path / "run"
    receipt.write_run(str(out), cfg, trace_bytes, manifest, result, rows)
    _convert_run_to_legacy_v1(out)
    assert receipt.verify_receipt_dir(str(out)) == []
