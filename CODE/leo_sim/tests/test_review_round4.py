"""Round-4 permanent regression tests (2026-08-13 Codex acceptance review).

Seven frozen defect groups: (1) control-packet geometry-loss fate, (2) directed
ISL routing, (3) geometry-change certification bounds and interval end,
(4) public-entry fail-closed behavior, (5) cache arrival-time validity,
(6) M-Lab grid mapping, (7) the ControlPacket contract. Every test FAILED on
the pre-round-4 implementation. Assertions are behavioral only.
"""
import csv
import copy
import hashlib
import json
import math
import shutil

import numpy as np
import pytest

from CODE.leo_sim import config, control, kernel, learning, model, receipt, routing, trace
from CODE.leo_sim.__main__ import main
from CODE.leo_sim.grid import aggregate_id, grid_id
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg
from CODE.leo_sim.tests.test_review_round2 import _run_dir

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)


# ------------------------------------------- 1. control geometry-loss fate

class _FlappingIsl(StaticGeometry):
    """ISL available strictly before `down_at`, then permanently down."""

    def __init__(self, *args, down_at=0.5, **kw):
        super().__init__(*args, isl_changes=[down_at], **kw)
        self._down_at = down_at

    def isl_available(self, a, b, t):
        return super().isl_available(a, b, t) and t < self._down_at


def test_control_geometry_loss_in_flight_is_legal_and_accounted():
    """A control packet mid-transmission on a directional ISL when geometry
    fails must get the control ledger's geometry-loss fate (distinct from the
    random-outage fate), the run must reach the horizon naturally, and control
    bit conservation must hold."""
    cfg = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 10.0},
        "links": {"isl_rate_mbps": 0.008, "geometry_loss": True},
        "control_plane": {"enabled": True, "vis_k": 1,
                          "advertise_interval_s": 100.0, "packet_bits": 8_000,
                          "ttl_s": 50.0},
    })
    geo = _FlappingIsl(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}}, down_at=0.5)
    res = kernel.run_simulation(cfg, [], geometry=geo)
    assert res["natural_end"] is True, res.get("error")
    assert res["stop_time_s"] == 10.0
    fc = res["control"]["fate_counts"]
    assert fc["GEOMETRY_LOSS_IN_FLIGHT"] == 2  # both sats advertised at t=0
    assert fc["RANDOM_OUTAGE_IN_FLIGHT"] == 0  # ledgers distinguish the two
    c = res["control"]["counters"]
    assert c["geometry_lost"] == 2 and c["lost"] == 0
    assert c["transmission_started"] == 2 and c["transmission_completed"] == 0
    t = res["control"]["totals"]
    assert t["offered_bits"] == 2 * 8_000
    assert t["terminal_loss_bits"] == 2 * 8_000
    assert t["offered_bits"] == (t["delivered_bits"] + t["terminal_loss_bits"]
                                 + t["in_system_bits_at_stop"])
    # service time up to the failure instant stays accounted (0.5 s per link)
    assert res["occupied"]["ctrl_isl_s"] == pytest.approx(1.0, abs=1e-6)


def test_control_geometry_loss_receipt_roundtrip(tmp_path):
    """The new control fate flows through the artifact chain: receipt verify
    of a run with control geometry loss passes and reports it."""
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n",
        encoding="utf-8")
    cfg = config.resolve_config({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 3.0},
        "endpoints": {"sites": [{"name": "a", "lat": 0.1, "lon": 0.1},
                                {"name": "b", "lat": 2.0, "lon": 3.0}]},
        "demand": {"mode": "csv", "csv_path": str(empty_csv)},
        "access": {"hysteresis_deg": 0.0, "min_dwell_s": 0.0,
                   "acquisition_delay_s": 0.0},
        "links": {"isl_rate_mbps": 0.008, "geometry_loss": True},
        "control_plane": {"enabled": True, "vis_k": 1,
                          "advertise_interval_s": 100.0, "packet_bits": 8_000,
                          "ttl_s": 50.0},
        "routing": {"policy": "oracle"},
    })
    import hashlib
    from pathlib import Path
    tdir = tmp_path / "compiled"
    manifest = trace.compile_trace(cfg, str(tdir))
    tbytes = (Path(tdir) / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (Path(tdir) / "manifest.json").read_bytes()).hexdigest()
    geo = _FlappingIsl(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}}, down_at=0.5)
    res = kernel.run_simulation(cfg, [], geometry=geo)
    assert res["natural_end"] is True, res.get("error")
    out = tmp_path / "o"
    receipt.write_run(str(out), cfg, tbytes, manifest, res, [])
    errors = receipt.verify_receipt_dir(str(out))
    assert errors == []
    rep = json.loads((out / "receipt.json").read_text(encoding="utf-8"))
    assert rep["control"]["fate_counts"]["GEOMETRY_LOSS_IN_FLIGHT"] == 2


# --------------------------------------------------- 2. directed ISL routing

def test_topology_construction_fails_closed_on_unidirectional_link():
    """A physical ISL is bidirectional; if a provider hands us a one-way edge
    the topology builder must fail closed instead of letting routing fabricate
    the reverse edge."""
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}})  # no 1 -> 0 reverse
    with pytest.raises(ValueError, match="idirectional"):
        routing.build_topology(geo, 2, ["E", "W"])


def test_directed_routing_never_fabricates_reverse_paths():
    """True directed edges: 0 -> 1 and 2 -> 1 (1 is a sink). The destination
    is served only by sat 0. From sat 2 there is no directed path, so routing
    must say unreachable instead of returning the dead-end direction 'N'."""
    topo = {0: {"E": 1}, 1: {}, 2: {"N": 1}}
    cache = control.LocalCache()
    cache.put(control.CacheEntry(0, {"serve_cells": [B]}, 0.0, 0.01, 50.0))
    geo = StaticGeometry(3, neighbors_map=topo)
    cands, status = routing.choose_next_hop(
        "hop", 2, B, 1.0, geo, topo, cache, {}, 1e9, model.propagation_delay_s)
    assert status == "unreachable"
    assert cands == []


def test_directed_routing_follows_real_forward_edges():
    """0 -> 1 -> 2 with the destination served by sat 2: sat 0's next hop is
    the real outgoing direction 'E' (regression control: works before/after)."""
    topo = {0: {"E": 1}, 1: {"E": 2}, 2: {}}
    cache = control.LocalCache()
    cache.put(control.CacheEntry(2, {"serve_cells": [B]}, 0.0, 0.01, 50.0))
    geo = StaticGeometry(3, neighbors_map=topo)
    cands, status = routing.choose_next_hop(
        "hop", 0, B, 1.0, geo, topo, cache, {}, 1e9, model.propagation_delay_s)
    assert status == "ok"
    assert cands == ["E"]


# --------------------------------------- 3. geometry-change certification

def test_elevation_rate_bound_covers_the_supported_config_domain():
    """Dense numeric scan of |d(elevation)/dt| at the worst corner of the
    supported domain (300 km, the fastest altitude). The scan must reach the
    fast regime (round-4 probe measured 1.4223247 deg/s) and stay strictly
    below the documented bound."""
    dt = 0.05
    worst = 0.0
    for inc in (0.0, 53.0, 90.0, 180.0):
        cons = model.Constellation(6, 1, 300.0, inc)
        period = cons.period_s
        for lon in (0.0, 90.0, 180.0, 270.0):
            t = dt
            while t <= period:
                e1 = cons.elevation_deg(0, 0.0, lon, t - dt)
                e2 = cons.elevation_deg(0, 0.0, lon, t + dt)
                worst = max(worst, abs(e2 - e1) / (2 * dt))
                t += 0.5
    assert worst > 1.40          # the scan really reaches the fast regime
    assert worst < model.ELEV_RATE_DEG_S  # and the bound covers the domain


def test_next_change_detected_exactly_at_interval_end():
    """The contract is (t0, t1]: a crossing exactly at t1 must be found."""
    margin = lambda t: 1.0 if t < 5.0 else -1.0  # noqa: E731
    got = model._next_change_adaptive(margin, 0.0, 5.0, 2.0)
    assert got == pytest.approx(5.0, abs=1e-6)


def test_next_change_start_stays_open_and_deterministic():
    # already in the new state at t0: no change inside (t0, t1]
    assert model._next_change_adaptive(lambda t: -1.0, 0.0, 5.0, 2.0) is None
    # same inputs -> identical answers (scheduling determinism)
    margin = lambda t: 1.0 if t < 2.5 else -1.0  # noqa: E731
    a = model._next_change_adaptive(margin, 0.0, 5.0, 2.0)
    b = model._next_change_adaptive(margin, 0.0, 5.0, 2.0)
    assert a == b == pytest.approx(2.5, abs=1e-6)


# --------------------------------------- 4. public-entry fail-closed behavior

@pytest.mark.parametrize("field,bad", [
    ("lat", "abc"), ("lat", True), ("lon", "1.0"), ("lon", None),
    ("demand_weight", True), ("demand_weight", "2"),
    ("demand_weight", float("nan")),
])
def test_site_field_types_fail_closed(field, bad):
    site = {"name": "x", "lat": 0.1, "lon": 0.1, field: bad}
    with pytest.raises(config.ConfigError):
        config.resolve_config({"endpoints": {"sites": [site]}})


def test_cli_config_validate_bad_site_controlled_exit(tmp_path, capsys):
    f = tmp_path / "bad.yaml"
    f.write_text("endpoints:\n  sites:\n    - {name: x, lat: 'abc', lon: 0.1}\n",
                 encoding="utf-8")
    assert main(["config", "validate", str(f)]) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "CONFIG INVALID" in captured.out


def test_cli_config_validate_malformed_yaml_controlled_exit(tmp_path, capsys):
    f = tmp_path / "broken.yaml"
    f.write_text("scenario: [unclosed\n  bad: {", encoding="utf-8")
    assert main(["config", "validate", str(f)]) == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("bits", ["abc", "1e3", "1.5", "nan", ""])
def test_csv_bits_must_be_plain_positive_int(tmp_path, bits):
    src = tmp_path / "in.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        f"1,0.1,31.0,121.0,40.0,116.0,{bits},\n", encoding="utf-8")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    with pytest.raises(trace.TraceError):
        trace.compile_trace(cfg, str(tmp_path / "t"))


@pytest.mark.parametrize("field,value", [
    ("src_lat", "abc"), ("dst_lon", "xyz"), ("emit_time_s", "soon"),
    ("deadline_at_s", "later"),
])
def test_csv_field_text_fail_closed(tmp_path, field, value):
    row_ = {"packet_id": "1", "emit_time_s": "0.1", "src_lat": "31.0",
            "src_lon": "121.0", "dst_lat": "40.0", "dst_lon": "116.0",
            "bits": "8000000", "deadline_at_s": ""}
    row_[field] = value
    src = tmp_path / "in.csv"
    src.write_text(",".join(row_) + "\n" + ",".join(row_.values()) + "\n",
                   encoding="utf-8")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    with pytest.raises(trace.TraceError):
        trace.compile_trace(cfg, str(tmp_path / "t"))


def test_cli_trace_compile_bad_csv_controlled_exit(tmp_path, capsys):
    src = tmp_path / "in.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.1,31.0,121.0,40.0,116.0,notanumber,\n", encoding="utf-8")
    f = tmp_path / "c.yaml"
    f.write_text(
        f"demand:\n  mode: csv\n  csv_path: '{src}'\n"
        "endpoints:\n  sites:\n    - {name: a, lat: 31.0, lon: 121.0}\n",
        encoding="utf-8")
    assert main(["trace", "compile", "--config", str(f),
                 "--out", str(tmp_path / "t")]) == 2
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("name", ["receipt.json", "manifest.json",
                                  "resolved_config.json", "ledgers.json"])
def test_receipt_verify_corrupted_json_returns_errors(tmp_path, capsys, name):
    out = _run_dir(tmp_path)
    capsys.readouterr()  # flush the producing run's own stdout
    (out / name).write_text("{ not json [", encoding="utf-8")
    assert main(["receipt", "verify", str(out)]) == 2
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    payload = json.loads(captured.out)
    assert payload["status"] == "FAILED" and payload["errors"]


def test_receipt_verify_corrupted_trace_csv_returns_errors(tmp_path):
    out = _run_dir(tmp_path)
    (out / "trace.csv").write_text(
        "packet_id,emit_time_s,src_grid_id,dst_grid_id,bits,deadline_at_s\n"
        "1,0.0,G1:100:200,G1:101:200,notanumber,\n", encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert errors  # must be an error list, never a ValueError crash


def test_receipt_verify_trace_csv_missing_columns_returns_errors(tmp_path):
    out = _run_dir(tmp_path)
    (out / "trace.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert receipt.verify_receipt_dir(str(out))


def test_receipt_verify_receipt_json_wrong_type_returns_errors(tmp_path):
    out = _run_dir(tmp_path)
    (out / "receipt.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert receipt.verify_receipt_dir(str(out))


# ------------------------------------------- 5. cache arrival-time validity

def test_cache_entry_future_arrival_is_not_valid():
    e = control.CacheEntry(1, {"serve_cells": [B]}, generated_at=0.0,
                           received_at=5.0, ttl_s=50.0)
    assert e.valid_at(3.0) is False   # it has not actually arrived yet
    assert e.valid_at(5.0) is True
    assert e.valid_at(51.0) is False  # TTL window runs from generation


@pytest.mark.parametrize("kw", [
    {"generated_at": 2.0, "received_at": 1.0},      # arrival before generation
    {"generated_at": float("nan"), "received_at": 0.0},
    {"generated_at": 0.0, "received_at": float("inf")},
    {"generated_at": 0.0, "received_at": 0.01, "ttl_s": float("nan")},
    {"generated_at": 0.0, "received_at": 0.01, "ttl_s": 0.0},
    {"generated_at": 0.0, "received_at": 0.01, "ttl_s": -1.0},
])
def test_cache_entry_rejects_malformed_times(kw):
    base = {"generated_at": 0.0, "received_at": 0.01, "ttl_s": 5.0}
    base.update(kw)
    with pytest.raises(ValueError):
        control.CacheEntry(1, {}, **base)


def test_information_set_routing_and_learning_share_the_arrival_rule():
    cache = control.LocalCache()
    cache.put(control.CacheEntry(0, {"serve_cells": [B], "isl_queue_bits": {}},
                                 generated_at=0.0, received_at=5.0, ttl_s=50.0))
    topo = {0: {"E": 1}, 1: {"W": 0}}
    now = 3.0  # before the entry's arrival time
    assert routing.destinations_in_cache(cache, B, now) == []
    own = learning.own_state(0, 4, {}, 256_000_000, 0, 2)
    for c in learning.CONTRACTS:
        assert learning.information_set(c, 1, cache, now, topo) == {}
        obs = learning.build_observation(c, 1, cache, now, topo, own)
        if c in learning.GRAPH_CONTRACTS:
            # Graph contracts carry the root's own directly measured features
            # (root/valid flags and ECEF position) even with an empty cache;
            # only neighbor rows and the tail block are meaningful to check.
            assert np.array_equal(obs[-(learning.OWN_FEATURES + 3):-3], own)
            assert np.array_equal(obs[-3:], np.zeros(learning.DEST_FEATURES))
            feats = obs[:learning.GRAPH_MAX_NODES * learning.GRAPH_NODE_FEAT_DIM]
            feats = feats.reshape(learning.GRAPH_MAX_NODES,
                                  learning.GRAPH_NODE_FEAT_DIM)
            assert feats[0, 6] == 1.0 and feats[0, 7] == 1.0  # root+valid
            assert np.all(feats[1:, 7] == 0.0)  # no valid neighbor rows
        else:
            # only the own-state block may be non-zero
            assert not np.any(obs[learning.OWN_FEATURES:])


# ------------------------------------------------------- 6. M-Lab grid mapping

def _mlab_fixture(tmp_path, lines):
    p = tmp_path / "mlab.csv"
    p.write_text(
        "client_city,client_lat,client_lon,server_city,server_lat,server_lon,"
        "hour_utc,sample_count,mean_throughput_mbps\n" + "".join(lines),
        encoding="utf-8")
    return p


def test_mlab_weights_follow_configured_grid_degrees(tmp_path, monkeypatch):
    """With grid_deg=0.5 / aggregation_deg=2.0 the adapter must key weights on
    THOSE cells; the extreme a->b vs a->c weight ratio must visibly steer the
    destination distribution (the old fixed-default grid silently fell back to
    ~uniform via 1e-9 smoothing)."""
    fx = _mlab_fixture(tmp_path, [
        "a,0.1,0.1,b,10.1,10.1,10,10,1000.0\n",
        "a,0.1,0.1,c,20.1,20.1,10,10,1.0\n",
        "b,10.1,10.1,a,0.1,0.1,10,10,1000.0\n",
        "c,20.1,20.1,a,0.1,0.1,10,10,1000.0\n",
    ])
    monkeypatch.setattr(trace, "REPO_MLAB_CSV", fx)
    cfg = config.resolve_config({
        "endpoints": {"grid_deg": 0.5, "aggregation_deg": 2.0,
                      "sites": [{"name": "a", "lat": 0.1, "lon": 0.1},
                                {"name": "b", "lat": 10.1, "lon": 10.1},
                                {"name": "c", "lat": 20.1, "lon": 20.1}]},
        "scenario": {"duration_s": 20.0, "seed": 7},
        "demand": {"mode": "mlab", "offered_mbps": 5.0, "packet_bits": 100_000},
    })
    m = trace.compile_trace(cfg, str(tmp_path / "t"))
    assert m["provenance"] == "measurement_proxy"
    assert m["not_calibrated_user_demand"] is True
    with open(tmp_path / "t" / "trace.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    a_cell = aggregate_id(grid_id(0.1, 0.1, 0.5), 2.0)
    b_cell = aggregate_id(grid_id(10.1, 10.1, 0.5), 2.0)
    from_a = [r for r in rows if r["src_grid_id"] == a_cell]
    assert from_a, "endpoint a must emit packets"
    to_b = sum(1 for r in from_a if r["dst_grid_id"] == b_cell)
    assert to_b / len(from_a) > 0.9


def test_mlab_without_active_od_coverage_fails_closed(tmp_path, monkeypatch):
    """Measurements that map to no active OD must fail closed — never a
    silent 1e-9 smoothing into uniform demand."""
    fx = _mlab_fixture(tmp_path, [
        "x,-33.9,151.2,y,-37.8,144.9,10,10,500.0\n",  # Sydney -> Melbourne only
    ])
    monkeypatch.setattr(trace, "REPO_MLAB_CSV", fx)
    cfg = config.resolve_config({
        "endpoints": {"sites": [{"name": "a", "lat": 0.1, "lon": 0.1},
                                {"name": "b", "lat": 10.1, "lon": 10.1}]},
        "demand": {"mode": "mlab"},
    })
    with pytest.raises(trace.TraceError, match="cover"):
        trace.compile_trace(cfg, str(tmp_path / "t"))


# -------------------------------------------------- 7. ControlPacket contract

def test_control_packet_carries_task_contract_fields():
    p = kernel.ControlPacket(7, 3, 11, 1.0, 50.0, 2, 8_000, {"x": 1})
    assert (p.origin, p.seq, p.generated_at, p.ttl_s, p.remaining_hops,
            p.payload_bits, p.payload) == (3, 11, 1.0, 50.0, 2, 8_000,
                                           {"x": 1})
    assert p.bits == p.payload_bits  # read-only compatibility alias
    assert p.received_at is None  # set only by a real arrival
    assert p.valid_at(1.0) is True
    assert p.valid_at(51.0) is True
    assert p.valid_at(51.1) is False  # TTL window from generation
    assert p.valid_at(0.5) is False   # before generation
    assert p.aoi(4.5) == 3.5


@pytest.mark.parametrize("kwargs", [
    {"generated_at": float("nan")},
    {"ttl_s": 0.0},
    {"ttl_s": float("inf")},
    {"remaining_hops": -1},
    {"remaining_hops": 1.5},
    {"bits": 0},
    {"bits": 1.5},
])
def test_control_packet_rejects_invalid_contract_fields(kwargs):
    args = {"iid": 7, "origin": 3, "seq": 11, "generated_at": 1.0,
            "ttl_s": 50.0, "remaining_hops": 2, "bits": 8_000,
            "payload": {"x": 1}}
    args.update(kwargs)
    with pytest.raises(ValueError):
        kernel.ControlPacket(**args)


def test_control_packet_receive_time_is_validated_and_write_once():
    p = kernel.ControlPacket(7, 3, 11, 1.0, 50.0, 2, 8_000, {})
    with pytest.raises(ValueError):
        p.mark_received(0.5)
    p.mark_received(1.5)
    assert p.received_at == 1.5
    with pytest.raises(ValueError):
        p.mark_received(2.0)


def test_control_received_at_enters_ledger_and_matches_cache():
    cfg = make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 0.2},
        "control_plane": {"enabled": True, "vis_k": 1,
                          "advertise_interval_s": 100.0, "packet_bits": 8_000,
                          "ttl_s": 50.0},
    })
    geo = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}})
    res = kernel.run_simulation(cfg, [], geometry=geo)
    assert res["natural_end"] is True
    inst = res["control"]["instances"]
    assert inst, "expected control instances"
    delivered = {}
    for iid, rec in inst.items():
        fate, bits, received_at = rec  # full contract: fate, bits, received_at
        assert bits == 8_000
        if fate == "DELIVERED":
            assert isinstance(received_at, float)
            assert 0.0 <= received_at <= 0.2
            delivered[iid] = rec
        else:
            assert received_at is None
    assert len(delivered) == 2  # one advertisement per satellite
    # the receive time recorded in the cache contract equals the ledger's
    assert res["caches"][1][0]["received_at"] == delivered[1][2]
    assert res["caches"][0][1]["received_at"] == delivered[2][2]
    # AoI is derived from generated_at at the cache entry, consistently
    e = res["caches"][1][0]
    assert e["aoi"] == pytest.approx(0.2 - e["generated_at"])


# -------------------------------------- acceptance-seal regression cases

@pytest.mark.parametrize("gid", [
    "G1.0:90:180", "G01:90:180", "G1e0:90:180", "G1:+90:180",
    "G1:090:180", "G1:90:0180",
])
def test_noncanonical_grid_ids_fail_closed(gid):
    rows = [{"packet_id": 1, "emit_time_s": 0.0,
             "src_grid_id": "G1:90:180", "dst_grid_id": gid,
             "bits": 8, "deadline_at_s": None}]
    with pytest.raises(trace.TraceError, match="grid id"):
        trace.validate_packet_rows(rows, 1.0, 10)


def test_compiler_output_roundtrips_after_time_serialization(tmp_path):
    src = tmp_path / "input.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.0000006,0,0,10,10,8,0.0000006\n",
        encoding="utf-8")
    cfg = config.resolve_config({
        "scenario": {"duration_s": 1.0},
        "demand": {"mode": "csv", "csv_path": str(src)},
    })
    trace.compile_trace(cfg, str(tmp_path / "compiled"))
    rows = trace.load_trace(str(tmp_path / "compiled" / "trace.csv"), 1.0, 10)
    assert rows[0]["deadline_at_s"] >= rows[0]["emit_time_s"]


def _rebind_ledger(out):
    lp = out / "ledgers.json"
    rp = out / "receipt.json"
    rcp = json.loads(rp.read_text(encoding="utf-8"))
    rcp["ledgers_sha256"] = hashlib.sha256(lp.read_bytes()).hexdigest()
    rp.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")


def _rebind_trace_artifacts(out):
    """Rebind only hashes after a deliberate trace/manifest mutation."""
    tp = out / "trace.csv"
    mp = out / "manifest.json"
    rp = out / "receipt.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["trace_sha256"] = hashlib.sha256(tp.read_bytes()).hexdigest()
    mp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    rcp = json.loads(rp.read_text(encoding="utf-8"))
    rcp["trace_sha256"] = manifest["trace_sha256"]
    rcp["trace_manifest_sha256"] = hashlib.sha256(mp.read_bytes()).hexdigest()
    rp.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")


@pytest.mark.parametrize("bad", [None, 7, "x", [], {}, ["DELIVERED"],
                                  ["DELIVERED", True]])
def test_receipt_malformed_packet_fate_never_crashes(tmp_path, bad):
    out = _run_dir(tmp_path)
    lp = out / "ledgers.json"
    led = json.loads(lp.read_text(encoding="utf-8"))
    led["packet_fates"][next(iter(led["packet_fates"]))] = bad
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    _rebind_ledger(out)
    errors = receipt.verify_receipt_dir(str(out))
    assert errors


def test_receipt_malformed_packet_fate_key_never_crashes(tmp_path):
    out = _run_dir(tmp_path)
    lp = out / "ledgers.json"
    led = json.loads(lp.read_text(encoding="utf-8"))
    # rename one key to a non-numeric id: the id-set diff sort used key=int
    # and crashed with ValueError on ids like "abc" instead of returning
    # the error list
    old = next(iter(led["packet_fates"]))
    led["packet_fates"]["abc"] = led["packet_fates"].pop(old)
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    _rebind_ledger(out)
    errors = receipt.verify_receipt_dir(str(out))
    assert errors  # fail-closed with an error list, never an exception


@pytest.mark.parametrize("bad", [None, 7, "x", []])
def test_receipt_malformed_mechanism_counters_never_crashes(tmp_path, bad):
    out = _run_dir(tmp_path)
    lp = out / "ledgers.json"
    led = json.loads(lp.read_text(encoding="utf-8"))
    led["mechanism_counters"] = bad
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    _rebind_ledger(out)
    assert receipt.verify_receipt_dir(str(out))


def test_receipt_duplicate_trace_id_rejected_after_hash_rebind(tmp_path):
    out = _run_dir(tmp_path)
    tp = out / "trace.csv"
    lines = tp.read_text(encoding="utf-8").splitlines()
    tp.write_text("\n".join(lines + [lines[1]]) + "\n", encoding="utf-8")
    trace_sha = hashlib.sha256(tp.read_bytes()).hexdigest()
    mp = out / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["trace_sha256"] = trace_sha
    mp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    rp = out / "receipt.json"
    rcp = json.loads(rp.read_text(encoding="utf-8"))
    rcp["trace_sha256"] = trace_sha
    rcp["trace_manifest_sha256"] = hashlib.sha256(mp.read_bytes()).hexdigest()
    rp.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert any("duplicate packet_id" in e for e in errors)


def test_receipt_checks_trace_against_resolved_horizon(tmp_path):
    out = _run_dir(tmp_path)
    tp = out / "trace.csv"
    rows = list(csv.DictReader(tp.open(newline="", encoding="utf-8")))
    for row in rows:
        row["emit_time_s"] = str(float(row["emit_time_s"]) + 10.0)
        if row["deadline_at_s"]:
            row["deadline_at_s"] = str(float(row["deadline_at_s"]) + 10.0)
    with tp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    _rebind_trace_artifacts(out)
    errors = receipt.verify_receipt_dir(str(out))
    assert any("horizon" in e for e in errors), errors


def test_receipt_recomputes_manifest_active_endpoints(tmp_path):
    out = _run_dir(tmp_path)
    mp = out / "manifest.json"
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    manifest["active_endpoints"] += 1
    mp.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    rp = out / "receipt.json"
    rcp = json.loads(rp.read_text(encoding="utf-8"))
    rcp["trace_manifest_sha256"] = hashlib.sha256(mp.read_bytes()).hexdigest()
    rp.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert any("active_endpoints" in e for e in errors), errors


@pytest.mark.parametrize("bad", [None, 7, "x", [], ["DELIVERED"],
                                  ["DELIVERED", 8_000, "yesterday"]])
def test_receipt_malformed_control_instance_never_crashes(tmp_path, bad):
    out = _run_dir(tmp_path)
    lp = out / "ledgers.json"
    led = json.loads(lp.read_text(encoding="utf-8"))
    led["control_instances"]["fabricated"] = bad
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    _rebind_ledger(out)
    assert receipt.verify_receipt_dir(str(out))


@pytest.mark.parametrize("ledger_key", sorted(receipt.LEDGER_KEYS))
def test_receipt_each_ledger_field_wrong_type_never_crashes(tmp_path,
                                                            ledger_key):
    base = _run_dir(tmp_path / "base")
    out = tmp_path / ledger_key
    shutil.copytree(base, out)
    lp = out / "ledgers.json"
    led = copy.deepcopy(json.loads(lp.read_text(encoding="utf-8")))
    led[ledger_key] = None
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    _rebind_ledger(out)
    errors = receipt.verify_receipt_dir(str(out))
    assert errors


def test_receipt_control_bits_bound_to_resolved_packet_bits(tmp_path):
    out = _run_dir(tmp_path)
    lp = out / "ledgers.json"
    led = json.loads(lp.read_text(encoding="utf-8"))
    # fabricate a well-formed instance whose bits disagree with the resolved
    # config's control_plane.packet_bits: the receipt must reject it
    led["control_instances"]["fabricated_bits"] = ["CONTROL_EXPIRED", 9000, None]
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n",
                  encoding="utf-8")
    _rebind_ledger(out)
    errors = receipt.verify_receipt_dir(str(out))
    assert any("packet_bits" in e for e in errors), errors
