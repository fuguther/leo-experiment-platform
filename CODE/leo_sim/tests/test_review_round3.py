"""Round-3 permanent regression tests (2026-08-13 Codex round-3 findings).

Each test reproduces a defect demonstrated by /tmp/leo_v2_round3_probe.py
(config physical bounds accepted, adaptive geometry search returning None on
iteration exhaustion, load_trace accepting malformed rows, receipt verifier
ignoring fabricated ledger-only fields). All FAILED on the round-2
implementation and must now pass. Behavioral assertions only.
"""
import hashlib
import json

import pytest

from CODE.leo_sim import config, kernel, model, receipt, trace
from CODE.leo_sim.tests.helpers import cell, make_cfg
from CODE.leo_sim.tests.test_review_round2 import _run_dir


# ------------------------------------------------------- 1. config boundaries

def test_altitude_must_be_valid_leo():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"scenario": {"altitude_km": -7000.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"scenario": {"altitude_km": 100.0}})   # below LEO
    with pytest.raises(config.ConfigError):
        config.resolve_config({"scenario": {"altitude_km": 5000.0}})  # above LEO
    config.resolve_config({"scenario": {"altitude_km": 550.0}})       # valid


def test_inclination_range():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"scenario": {"inclination_deg": 999.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"scenario": {"inclination_deg": -1.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"scenario": {"inclination_deg": 180.5}})
    for ok in (0.0, 53.0, 90.0, 180.0):
        config.resolve_config({"scenario": {"inclination_deg": ok}})


def test_seed_must_be_nonnegative_int():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"scenario": {"seed": -1}})
    config.resolve_config({"scenario": {"seed": 0}})


def test_site_name_and_weight_validated():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"endpoints": {"sites": [{"name": "", "lat": 0.0, "lon": 0.0}]}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "a", "lat": 10.0, "lon": 10.0},
        ]}})
    for bad in (-1.0, 0.0):
        with pytest.raises(config.ConfigError):
            config.resolve_config({"endpoints": {"sites": [
                {"name": "a", "lat": 0.0, "lon": 0.0, "demand_weight": bad}]}})


def test_grid_degrees_must_divide_cleanly():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"endpoints": {"grid_deg": 0.7}})  # 180/0.7 not integral
    with pytest.raises(config.ConfigError):
        config.resolve_config({"endpoints": {"grid_deg": 0.5, "aggregation_deg": 1.6}})
    config.resolve_config({"endpoints": {"grid_deg": 0.25, "aggregation_deg": 1.0}})
    config.resolve_config({"endpoints": {"grid_deg": 0.5, "aggregation_deg": 1.5}})


# ---------------------------------------------------- 2. unified trace checks

def _write_rows(path, rows):
    path.write_text(
        "packet_id,emit_time_s,src_grid_id,dst_grid_id,bits,deadline_at_s\n"
        + "".join(",".join(str(x) for x in r) + "\n" for r in rows),
        encoding="utf-8")


def test_load_trace_rejects_malformed_rows(tmp_path):
    good = [1, 0.0, "G1:90:180", "G1:90:190", 8, ""]
    cases = {
        "nan_time": [[1, "nan", "G1:90:180", "G1:90:190", 8, ""]],
        "inf_time": [[1, "inf", "G1:90:180", "G1:90:190", 8, ""]],
        "negative_bits": [[1, 0.0, "G1:90:180", "G1:90:190", -8, ""]],
        "zero_bits": [[1, 0.0, "G1:90:180", "G1:90:190", 0, ""]],
        "duplicate_id": [good, [1, 0.5, "G1:90:180", "G1:90:190", 8, ""]],
        "nonpositive_id": [[0, 0.0, "G1:90:180", "G1:90:190", 8, ""]],
        "bad_grid": [[1, 0.0, "a", "b", 8, ""]],
        "same_cell": [[1, 0.0, "G1:90:180", "G1:90:180", 8, ""]],
        "deadline_before_emit": [[1, 2.0, "G1:90:180", "G1:90:190", 8, 1.0]],
        "nan_deadline": [[1, 0.0, "G1:90:180", "G1:90:190", 8, "nan"]],
        "beyond_horizon": [[1, 99.0, "G1:90:180", "G1:90:190", 8, ""]],
        "unsorted": [[2, 3.0, "G1:90:180", "G1:90:190", 8, ""],
                     [1, 1.0, "G1:90:180", "G1:90:190", 8, ""]],
    }
    for label, rows in cases.items():
        p = tmp_path / f"{label}.csv"
        _write_rows(p, rows)
        with pytest.raises(trace.TraceError):
            trace.load_trace(str(p), horizon_s=10.0, max_packets=100)


def test_load_trace_enforces_max_packets(tmp_path):
    p = tmp_path / "big.csv"
    _write_rows(p, [[i, float(i), "G1:90:180", "G1:90:190", 8, ""]
                    for i in range(1, 6)])
    with pytest.raises(trace.TraceError, match="max_packets"):
        trace.load_trace(str(p), horizon_s=10.0, max_packets=3)
    rows = trace.load_trace(str(p), horizon_s=10.0, max_packets=5)
    assert len(rows) == 5


def test_csv_compile_out_of_horizon_row_fails_closed(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.1,31.0,121.0,40.0,116.0,8000000,\n"
        "2,999.0,31.0,121.0,40.0,116.0,8000000,\n")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    with pytest.raises(trace.TraceError, match="horizon"):
        trace.compile_trace(cfg, str(tmp_path / "t"))


def test_kernel_rejects_malformed_rows_directly():
    # the kernel is the last gate: rows bypassing load_trace are still checked
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    from CODE.leo_sim.tests.helpers import StaticGeometry
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    bad = [{"packet_id": 1, "emit_time_s": float("nan"), "src_grid_id": cell(0.0, 0.0),
            "dst_grid_id": cell(0.0, 10.0), "bits": 8, "deadline_at_s": None}]
    with pytest.raises(trace.TraceError):
        kernel.run_simulation(cfg, bad, geometry=geo)


# --------------------------------------------------- 3. geometry certification

def test_delayed_crossing_returns_crossing_not_none():
    def margin(t):
        return 0.1 if t < 15000.0 else 15000.1 - t
    got = model._next_change_adaptive(margin, 0.0, 20000.0, 1.0)
    assert got is not None
    assert abs(got - 15000.1) < 1e-6


def test_iteration_exhaustion_fails_closed():
    def margin(t):  # crossing at ~1e9 s: unreachable within 5 iterations
        return 0.1 if t < 1e9 else 1e9 + 0.1 - t
    with pytest.raises(model.GeometryCertificationError):
        model._next_change_adaptive(margin, 0.0, 2e9, 1.0, max_iter=5)


def test_adaptive_input_validation():
    good = lambda t: 1.0
    with pytest.raises(model.GeometryCertificationError):
        model._next_change_adaptive(good, 0.0, 10.0, 0.0)     # rate_bound
    with pytest.raises(model.GeometryCertificationError):
        model._next_change_adaptive(good, 10.0, 0.0, 1.0)     # t1 < t0
    with pytest.raises(model.GeometryCertificationError):
        model._next_change_adaptive(lambda t: float("nan"), 0.0, 10.0, 1.0)
    with pytest.raises(model.GeometryCertificationError):
        model._next_change_adaptive(good, float("nan"), 10.0, 1.0)
    # margin identically zero is degenerate (available exactly at the
    # threshold): a sign change could begin at any instant, so no certified
    # answer exists -> fail closed, never guess
    with pytest.raises(model.GeometryCertificationError):
        model._next_change_adaptive(lambda t: 0.0, 0.0, 10.0, 1.0)


def test_instantaneous_crossing_starting_at_zero_margin_is_certified():
    """A query that starts exactly on a transient zero-margin instant must
    still be certified (the link just crossed the threshold), while a
    genuinely identically-zero margin remains degenerate."""
    def margin(t):  # positive before 10, negative after: no further change
        return 10.0 - t
    # Starting exactly at the crossing: the interval after is all one sign,
    # so the certified answer is None (no new change), not an exception.
    assert model._next_change_adaptive(margin, 10.0, 20.0, 1.0,
                                       tol=1e-9) is None

    def margin2(t):  # zero at 10, down until 15, then recovers at 15
        if t <= 10.0:
            return 10.0 - t          # crosses zero exactly at t=10
        return -1.0 if t < 15.0 else t - 15.0  # recovers (positive) at 15
    ch = model._next_change_adaptive(margin2, 10.0, 20.0, 1.0, tol=1e-9)
    assert ch is not None
    assert abs(ch - 15.0) < 1e-6
    # true degenerate case stays fail-closed
    with pytest.raises(model.GeometryCertificationError):
        model._next_change_adaptive(lambda t: 0.0, 0.0, 10.0, 1.0)


def test_walker_change_times_match_dense_scans():
    """Independent dense-scan cross-check of certified next-change times over
    several satellites, ground points and orbit segments (GSL and ISL)."""
    c = model.Constellation(num_satellites=66, num_planes=6, altitude_km=550.0,
                            inclination_deg=53.0, min_elevation_deg=25.0)
    points = [(0.0, 0.0), (40.0, 120.0), (-30.0, -60.0)]
    for sat in (0, 7, 23):
        for lat, lon in points:
            for t0 in (0.0, 1200.0):
                limit = t0 + 1800.0
                got = c.next_gsl_change(sat, lat, lon, t0, limit)
                # dense reference scan at 0.5 s
                dense = None
                prev = c.ground_visible(sat, lat, lon, t0)
                step = 0.5
                n = int((limit - t0) / step)
                for i in range(1, n + 1):
                    tt = t0 + i * step
                    v = c.ground_visible(sat, lat, lon, tt)
                    if v != prev:
                        dense = tt
                        break
                    prev = v
                if dense is None:
                    assert got is None, (sat, lat, lon, t0, got)
                else:
                    assert got is not None and abs(got - dense) <= 0.5, \
                        (sat, lat, lon, t0, got, dense)
    # ISL: a few directional neighbor pairs over two windows
    for a, b in ((0, 1), (1, 2), (0, 11)):
        for t0 in (0.0, 2000.0):
            limit = t0 + 1800.0
            got = c.next_isl_change(a, b, t0, limit)
            dense = None
            prev = c.isl_available(a, b, t0)
            step = 0.5
            n = int((limit - t0) / step)
            for i in range(1, n + 1):
                tt = t0 + i * step
                v = c.isl_available(a, b, tt)
                if v != prev:
                    dense = tt
                    break
                prev = v
            if dense is None:
                assert got is None, (a, b, t0, got)
            else:
                assert got is not None and abs(got - dense) <= 0.5, \
                    (a, b, t0, got, dense)


# ---------------------------------------------------- 4. receipt trust model

def _mutate_ledgers(out, mutate):
    lp = out / "ledgers.json"
    led = json.loads(lp.read_text(encoding="utf-8"))
    mutate(led)
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rp = out / "receipt.json"
    rcp = json.loads(rp.read_text(encoding="utf-8"))
    rcp["ledgers_sha256"] = hashlib.sha256(lp.read_bytes()).hexdigest()
    rp.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt.verify_receipt_dir(str(out))


def test_stop_time_must_equal_horizon_on_natural_end(tmp_path):
    out = _run_dir(tmp_path)
    errors = _mutate_ledgers(out, lambda led: led.__setitem__("stop_time_s", 987654.0))
    assert errors


def test_queue_area_schema_enforced(tmp_path):
    out = _run_dir(tmp_path)
    errors = _mutate_ledgers(
        out, lambda led: led.__setitem__("queue_area_bits_s", {"fabricated": 1e99}))
    assert errors
    out = _run_dir(tmp_path / "b")
    errors = _mutate_ledgers(
        out, lambda led: led["queue_area_bits_s"].__setitem__("uplink", -1.0))
    assert errors


def test_deliveries_must_match_delivered_fates(tmp_path):
    out = _run_dir(tmp_path)
    errors = _mutate_ledgers(
        out, lambda led: led.__setitem__("deliveries", {"1": {"delivered_at": -123.0}}))
    assert errors
    out = _run_dir(tmp_path / "b")
    # dropping a delivered packet from deliveries must also fail
    errors = _mutate_ledgers(out, lambda led: led.__setitem__("deliveries", {}))
    assert errors


def test_access_and_occupied_schema_enforced(tmp_path):
    out = _run_dir(tmp_path)
    assert _mutate_ledgers(out, lambda led: led.__setitem__("access", {"fabricated": True}))
    out = _run_dir(tmp_path / "b")
    assert _mutate_ledgers(out, lambda led: led["occupied"].__setitem__("isl_s", -0.5))
    out = _run_dir(tmp_path / "c")
    assert _mutate_ledgers(out, lambda led: led.__setitem__("events_processed", "many"))


def test_counter_relations_enforced(tmp_path):
    out = _run_dir(tmp_path)

    def bad(led):
        led["control_counters"]["transmission_completed"] = \
            led["control_counters"]["entered_queue"] + 5
    assert _mutate_ledgers(out, bad)


def test_malformed_ledgers_return_errors_not_crashes(tmp_path):
    out = _run_dir(tmp_path)
    lp = out / "ledgers.json"
    lp.write_text(json.dumps({"packet_fates": "not-a-dict"}) + "\n", encoding="utf-8")
    rp = out / "receipt.json"
    rcp = json.loads(rp.read_text(encoding="utf-8"))
    rcp["ledgers_sha256"] = hashlib.sha256(lp.read_bytes()).hexdigest()
    rp.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    errors = receipt.verify_receipt_dir(str(out))
    assert errors  # must be a list of error strings, never an exception


def test_round3_probe_fabrication_rejected(tmp_path):
    # exact replay of the round-3 probe's ledger fabrication
    out = _run_dir(tmp_path)
    assert receipt.verify_receipt_dir(str(out)) == []
    ledgers_path = out / "ledgers.json"
    receipt_path = out / "receipt.json"
    ledgers = json.loads(ledgers_path.read_text())
    ledgers["stop_time_s"] = 987654.0
    ledgers["queue_area_bits_s"] = {"fabricated": 1e99}
    ledgers["deliveries"] = {"1": {"delivered_at_s": -123.0}}
    ledgers["access"] = {"fabricated": True}
    ledgers_path.write_text(json.dumps(ledgers, indent=2, sort_keys=True) + "\n")
    rcp = json.loads(receipt_path.read_text())
    rcp["ledgers_sha256"] = hashlib.sha256(ledgers_path.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n")
    errors = receipt.verify_receipt_dir(str(out))
    assert errors, "fabricated ledger-only fields must be rejected"
