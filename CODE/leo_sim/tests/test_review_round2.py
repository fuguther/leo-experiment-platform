"""Round-2 permanent regression tests (2026-08-13 independent review, round 2).

Each test reproduces a counterexample from /private/tmp/leo_v2_review_round2.py
or a defect named in the round-2 remediation directive. They FAILED on the
round-1 implementation and must now pass. Assertions are behavioral only.
"""
import json

import pytest

from CODE.leo_sim import config, kernel, model, receipt, trace
from CODE.leo_sim.__main__ import main
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
C = cell(10.0, 0.0)
AC = cell_center(A)
BC = cell_center(B)


# ---------------------------------------------------------- 1. trace identity

def test_trace_identity_invariant_to_non_demand_groups():
    """A fair A/B consumes the SAME immutable trace: routing policy, access,
    links, control plane, learning and outputs must not change trace identity."""
    base = config.resolve_config({"routing": {"policy": "hop"}})
    ref = config.trace_identity_sha256(base)
    same = [
        {"routing": {"policy": "delay"}},
        {"routing": {"policy": "capacity"}},
        {"routing": {"policy": "oracle"}},
        {"routing": {"max_hops": 3}},
        {"access": {"slots_per_satellite": 1}},
        {"access": {"association": "mbb", "dual_connect": True}},
        {"access": {"uplink_rate_mbps": 5.0}},
        {"links": {"isl_rate_mbps": 5.0}},
        {"links": {"ge_enabled": True}},
        {"control_plane": {"enabled": False}},
        {"control_plane": {"vis_k": 1}},
        {"learning": {"lr": 0.5}},
        {"outputs": {"out_dir": "elsewhere"}},
        {"execution": {"max_events": 5}},
        {"scenario": {"num_satellites": 12, "num_planes": 3}},
        {"scenario": {"time_step_s": 0.5}},
    ]
    for over in same:
        got = config.trace_identity_sha256(config.resolve_config(over))
        assert got == ref, f"{over} must not change trace identity"


def test_trace_identity_changes_with_demand_scope():
    ref = config.trace_identity_sha256(config.resolve_config())
    changing = [
        {"scenario": {"seed": 43}},
        {"scenario": {"duration_s": 61.0}},
        {"demand": {"offered_mbps": 2.0}},
        {"demand": {"mode": "gravity"}},
        {"demand": {"packet_bits": 1000}},
        {"endpoints": {"aggregation_deg": 2.0}},
        {"endpoints": {"sites": [{"name": "x", "lat": 1.0, "lon": 1.0}]}},
        {"execution": {"max_packets": 100}},  # real compile boundary
    ]
    for over in changing:
        got = config.trace_identity_sha256(config.resolve_config(over))
        assert got != ref, f"{over} must change trace identity"


def test_trace_identity_binds_csv_input_content(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "7,0.1,31.0,121.0,40.0,116.0,8000000,\n")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    m1 = trace.compile_trace(cfg, str(tmp_path / "t1"))
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "7,0.2,31.0,121.0,40.0,116.0,8000000,\n")  # same path, new content
    m2 = trace.compile_trace(cfg, str(tmp_path / "t2"))
    assert m1["trace_identity_sha256"] != m2["trace_identity_sha256"]


def test_compiled_trace_bytes_identical_across_routing_policies(tmp_path):
    sites = [{"name": "a", "lat": 0.1, "lon": 0.1},
             {"name": "b", "lat": 2.0, "lon": 3.0}]
    shas = []
    for policy in ("hop", "delay", "capacity", "oracle"):
        cfg = config.resolve_config({
            "scenario": {"duration_s": 3.0, "seed": 9},
            "endpoints": {"sites": sites},
            "demand": {"mode": "uniform", "offered_mbps": 1.0,
                       "packet_bits": 100_000},
            "routing": {"policy": policy},
        })
        m = trace.compile_trace(cfg, str(tmp_path / policy))
        shas.append(m["trace_sha256"])
    assert len(set(shas)) == 1, "all routing arms must consume one immutable trace"


# ------------------------------------------------------- 2. fair finite access

def test_k1_slot_rotation_destination_served():
    # K=1, one satellite: the source must not hold the only slot forever; the
    # destination has to win a real association before any delivery.
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 100.0},
        "access": {"slots_per_satellite": 1},
    })
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    res = kernel.run_simulation(cfg, [row(1, 1.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    assoc_b = [e for e in res["handover"]["events"]
               if e["type"] == "associate" and e["endpoint"] == B]
    assert assoc_b, "destination never held a real association"
    assert assoc_b[0]["t"] < res["deliveries"][1]["delivered_at"]
    assert res["deliveries"][1]["delivered_at"] < 20.0  # bounded wait


def test_k1_rotation_serves_all_contending_endpoints():
    # continuous backlog at two sources + one destination, K=1: nobody starves
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 90.0},
        "access": {"slots_per_satellite": 1, "uplink_rate_mbps": 1.0,
                   "downlink_rate_mbps": 1.0, "slot_lease_s": 2.0,
                   "idle_release_s": 0.5, "retirement_deadline_s": 20.0},
    })
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    rows = []
    for i in range(5):
        rows.append(row(10 + i, i * 2.0, A, B, bits=1_000_000))
        rows.append(row(20 + i, i * 2.0, C, B, bits=1_000_000))
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    served_up = {c for c, _pid in res["service_log"]["uplink"]}
    assert A in served_up and C in served_up, "a source endpoint starved"
    assoc_b = [e for e in res["handover"]["events"]
               if e["type"] == "associate" and e["endpoint"] == B]
    assert assoc_b, "destination never associated"
    delivered = sum(1 for f in res["fates"].values() if f == "DELIVERED")
    assert delivered >= 2
    # starvation bound: each holder keeps the slot at most lease + the
    # assigned backlog drain; every source's first COMPLETED service must
    # land within lease + drain(2 x 1 s) + own service(1 s) + tick slack
    first = {}
    for t, c, _pid in res["service_log"]["uplink_bits"]:
        first.setdefault(c, t)
    assert max(first.values()) <= 2.0 + 2.0 + 1.0 + 1.0


def test_access_counters_exposed():
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 30.0},
        "access": {"slots_per_satellite": 1},
    })
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    res = kernel.run_simulation(cfg, [row(1, 1.0, A, B)], geometry=geo)
    acc = res["access"]
    for key in ("requests", "grants", "wait_time_s_total", "wait_time_s_max",
                "slot_hold_s_total", "waiting_at_stop"):
        assert key in acc, f"missing access counter {key}"
    # source (pre-positioned at t=0) and destination (demand grant) admitted
    assert acc["grants"] + acc["preposition_grants"] >= 2
    assert acc["wait_time_s_max"] > 0  # the destination really queued


# ------------------------------------------------- 3. geometry change contract

def test_narrow_geometry_outage_caught_with_declared_timeline():
    # a 0.1 ms visibility dip strictly inside the service interval, between
    # the old 64-point sampling grid: must still fail the in-flight packet
    def visible(_s, _lat, _lon, t):
        return not (0.0201 <= t < 0.0202)

    geo = StaticGeometry(1, visible=visible, gsl_changes=[0.0201, 0.0202])
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "GEOMETRY_LOSS_IN_FLIGHT"
    assert abs(res["occupied"]["gsl_uplink_s"] - 0.0201) < 1e-6


def test_geometry_provider_must_certify_change_times():
    class Uncertified:
        num_satellites = 1

        def neighbors(self, s, dirs):
            return {}

    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    with pytest.raises(kernel.KernelError, match="certify"):
        kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=Uncertified())


def test_walker_next_gsl_change_matches_dense_scan():
    # the certified adaptive root-find must agree with a dense brute-force scan
    c = model.Constellation(num_satellites=66, num_planes=6, altitude_km=550.0,
                            inclination_deg=53.0, min_elevation_deg=25.0)
    lat, lon, _ = c.subpoint(0, 0.0)
    assert c.ground_visible(0, lat, lon, 0.0)
    flip = c.next_gsl_change(0, lat, lon, 0.0, 4000.0)
    assert flip is not None
    # dense verification around the reported crossing
    assert c.ground_visible(0, lat, lon, flip - 0.5)
    assert not c.ground_visible(0, lat, lon, flip + 0.5)


# ------------------------------------------------------------ 4. exact horizon

def test_horizon_closes_exactly_off_tick_grid():
    # horizon=0.1 is not a multiple of time_step=0.07: accounting must still
    # close at the exact horizon, not at the last event time
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1,
                     "duration_s": 0.1, "time_step_s": 0.07},
        "access": {"uplink_rate_mbps": 1.0},
    })
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B), row(2, 0.0, A, B)],
                                geometry=geo)
    assert res["natural_end"] is True
    assert abs(res["stop_time_s"] - 0.1) < 1e-12
    # packet 1 in service 0 -> horizon: occupies the GSL for the FULL 0.1 s
    assert abs(res["occupied"]["gsl_uplink_s"] - 0.1) < 1e-12
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    # packet 2 sat in the uplink queue for the whole horizon: 8e6 bits * 0.1 s
    assert abs(res["queue_area_bits_s"]["uplink"] - 8e6 * 0.1) < 1.0


# -------------------------------------------- 5-6. receipt + mechanism binding

def _run_dir(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "scenario:\n  duration_s: 2.0\n  num_satellites: 1\n  num_planes: 1\n"
        "endpoints:\n  sites:\n"
        "    - {name: a, lat: 0.1, lon: 0.1}\n"
        "    - {name: b, lat: 2.0, lon: 3.0}\n"
        "demand:\n  mode: uniform\n  offered_mbps: 4.0\n  packet_bits: 1000000\n"
        "routing:\n  policy: oracle\ncontrol_plane:\n  enabled: false\n",
        encoding="utf-8")
    out = tmp_path / "out"
    assert main(["run", "--config", str(cfg), "--out", str(out)]) == 0
    return out


def _tamper(out, mutate):
    rp = out / "receipt.json"
    r = json.loads(rp.read_text(encoding="utf-8"))
    mutate(r)
    rp.write_text(json.dumps(r, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt.verify_receipt_dir(str(out))


def test_receipt_untampered_still_verifies(tmp_path):
    out = _run_dir(tmp_path)
    assert receipt.verify_receipt_dir(str(out)) == []


@pytest.mark.parametrize("field", [
    "config_version", "seed", "horizon_s", "events_processed", "routing_label",
    "occupied", "handover_event_count", "control", "fate_counts", "totals",
    "research_eligible", "natural_end", "ledgers_sha256",
])
def test_receipt_tamper_each_field_fails(tmp_path, field):
    out = _run_dir(tmp_path)

    def mutate(r):
        v = r[field]
        if isinstance(v, bool):
            r[field] = not v
        elif isinstance(v, (int, float)):
            r[field] = v + 1
        elif isinstance(v, str):
            r[field] = v + "-fabricated"
        elif isinstance(v, dict):
            r[field] = {"fabricated": 1}
        else:
            r[field] = "fabricated"
    errors = _tamper(out, mutate)
    assert errors, f"tampering {field} must fail verification"


def test_receipt_deps_missing_or_unknown_fails(tmp_path):
    out = _run_dir(tmp_path)
    assert _tamper(out, lambda r: r.pop("deps")), "missing deps must fail"
    out = _run_dir(tmp_path / "b")

    def bad_dep(r):
        r["deps"]["python"] = "0.0.0-fabricated"
    assert _tamper(out, bad_dep)

    out = _run_dir(tmp_path / "c")

    def extra_dep(r):
        r["deps"]["mystery"] = "1.0"
    assert _tamper(out, extra_dep), "unknown dependency key must fail"


def test_receipt_unknown_or_missing_keys_fail(tmp_path):
    out = _run_dir(tmp_path)
    assert _tamper(out, lambda r: r.__setitem__("surprise", 1))
    out = _run_dir(tmp_path / "b")
    assert _tamper(out, lambda r: r.pop("seed"))


def test_ledgers_artifact_tamper_fails(tmp_path):
    out = _run_dir(tmp_path)
    lp = out / "ledgers.json"
    led = json.loads(lp.read_text(encoding="utf-8"))
    pid = sorted(led["packet_fates"], key=int)[0]
    led["packet_fates"][pid][0] = "NO_ROUTE"
    lp.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert receipt.verify_receipt_dir(str(out))


def test_control_effective_requires_send_path():
    # one satellite, no ISL: snapshots are created but no control packet can
    # enter any send path -> control NOT effective, run not research eligible
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 0.2},
        "control_plane": {"enabled": True, "vis_k": 1},
    })
    res = kernel.run_simulation(cfg, [], geometry=StaticGeometry(1, neighbors_map={0: {}}))
    c = res["control"]["counters"]
    assert c["snapshots_created"] == 1
    assert c["registered"] == 0
    assert c["entered_queue"] == 0
    assert res["mechanisms"]["effective"]["control_plane"] is False
    assert res["research_eligible"] is False


def test_control_counters_full_lifecycle():
    topo = {0: {"E": 1}, 1: {"W": 0}}
    cfg = make_cfg({
        # one advertise round at t=0 only (horizon is inclusive: an interval
        # of exactly the duration would fire a second round at t=horizon)
        "scenario": {"duration_s": 0.5},
        "control_plane": {"enabled": True, "vis_k": 1,
                          "advertise_interval_s": 1.0, "packet_bits": 8_000},
    })
    res = kernel.run_simulation(cfg, [], geometry=StaticGeometry(2, neighbors_map=topo))
    c = res["control"]["counters"]
    assert c["snapshots_created"] >= 2
    assert c["registered"] == 2
    assert c["entered_queue"] == 2
    assert c["transmission_started"] == 2
    assert c["transmission_completed"] == 2
    assert c["arrived"] == 2
    assert res["mechanisms"]["effective"]["control_plane"] is True


# ------------------------------------------------- 7. MBB hard retirement

def test_mbb_inflight_interrupted_at_retirement_deadline():
    line = {0: {"E": 1}, 1: {"W": 0}}

    def elev(s, lat, lon, t):
        if (lat, lon) == BC:
            return 90.0 if s == 1 else -10.0
        if s == 0:
            return 80.0 if t < 5.0 else 30.0
        return 20.0 if t < 5.0 else 80.0

    geo = StaticGeometry(
        2, neighbors_map=line, gsl_changes=[5.0],
        visible=lambda s, lat, lon, t: elev(s, lat, lon, t) >= 25.0,
        elevation=elev)
    cfg = make_cfg({
        "scenario": {"duration_s": 25.0, "time_step_s": 0.1},
        "access": {"association": "mbb", "dual_connect": True,
                   "uplink_rate_mbps": 1.0, "retirement_deadline_s": 2.0},
    })
    res = kernel.run_simulation(cfg, [row(1, 3.9, A, B)], geometry=geo)
    mbb = next(e for e in res["handover"]["events"] if e["type"] == "mbb")
    deadline = mbb["t"] + 2.0
    # the old link must die at the deadline even though a packet is in flight
    rel = [e for e in res["handover"]["events"]
           if e["type"] == "release" and e["reason"] == "mbb_retire_deadline"]
    assert rel, "hard retirement never fired"
    assert abs(rel[0]["t"] - deadline) < 0.15
    # the interrupted packet is requeued (no duplicate fate) and completes via
    # the NEW link only: a full 8 s service restarts at the deadline
    assert res["fates"][1] == "DELIVERED"
    assert res["deliveries"][1]["path"] == [1]
    assert res["deliveries"][1]["delivered_at"] > deadline + 7.9


# ------------------------------------------------------ 8. config fail-closed

def test_config_rejects_nan_and_inf():
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(config.ConfigError):
            config.resolve_config({"scenario": {"duration_s": bad}})
        with pytest.raises(config.ConfigError):
            config.resolve_config({"access": {"uplink_rate_mbps": bad}})
        with pytest.raises(config.ConfigError):
            config.resolve_config({"learning": {"lr": bad}})
        with pytest.raises(config.ConfigError):
            config.resolve_config(
                {"endpoints": {"sites": [{"name": "x", "lat": bad, "lon": 0.0}]}})


def test_config_learning_numeric_fail_closed():
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"lr": 0.0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"lr": -1e-3}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"batch_size": 0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"replay_size": 0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"target_update_interval": 0}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"batch_size": 100, "replay_size": 10}})
    with pytest.raises(config.ConfigError):
        config.resolve_config({"learning": {"epsilon_decay_s": 0.0}})


def test_excluded_rewards_rejected():
    # v1 keeps ONLY the corrected queue reward; distance/linear are dead entry
    # points the plan explicitly excludes
    for bad in ("distance", "linear"):
        with pytest.raises(config.ConfigError):
            config.resolve_config({"learning": {"reward": bad}})


# ----------------------------------------------------- 9. CSV packet identity

def test_csv_preserves_source_packet_ids(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "100,0.3,31.0,121.0,40.0,116.0,8000000,\n"
        "5,0.1,31.0,121.0,51.5,0.1,8000000,\n"
        "42,0.2,31.0,121.0,40.0,116.0,8000000,\n")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    trace.compile_trace(cfg, str(tmp_path / "t"))
    rows = trace.load_trace(str(tmp_path / "t" / "trace.csv"))
    assert {r["packet_id"] for r in rows} == {5, 42, 100}, \
        "source packet identity must survive compilation (no renumbering)"
    assert [r["packet_id"] for r in rows] == [5, 42, 100]  # emit-time order


def test_csv_rejects_non_integer_or_nonpositive_ids(tmp_path):
    src = tmp_path / "in.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "abc,0.1,31.0,121.0,40.0,116.0,8000000,\n")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    with pytest.raises(trace.TraceError):
        trace.compile_trace(cfg, str(tmp_path / "t"))


# ------------------------------------------- 10. looped advertisement guard

def test_origin_never_caches_own_looped_advertisement():
    # triangle + vis_k=3: copies of an origin's advertisement that loop back
    # must never enter the origin's own cache (explicit guard, not luck)
    tri = {0: {"E": 1, "N": 2}, 1: {"W": 0, "E": 2}, 2: {"S": 0, "W": 1}}
    geo = StaticGeometry(3, neighbors_map=tri, visible=lambda s, lat, lon, t: False)
    cfg = make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1, "duration_s": 0.2},
        "control_plane": {"enabled": True, "vis_k": 3, "advertise_interval_s": 1.0},
    })
    res = kernel.run_simulation(cfg, [], geometry=geo)
    for s in range(3):
        assert s not in res["caches"][s], f"sat {s} cached its own advertisement"


# ------------------------------------------------------------ CLI trace SHA

def test_run_expect_trace_sha256(tmp_path, capsys):
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "scenario:\n  duration_s: 2.0\n  num_satellites: 1\n  num_planes: 1\n"
        "endpoints:\n  sites:\n"
        "    - {name: a, lat: 0.1, lon: 0.1}\n"
        "    - {name: b, lat: 2.0, lon: 3.0}\n"
        "demand:\n  mode: uniform\n  offered_mbps: 4.0\n  packet_bits: 1000000\n"
        "routing:\n  policy: oracle\ncontrol_plane:\n  enabled: false\n",
        encoding="utf-8")
    tdir = tmp_path / "tr"
    assert main(["trace", "compile", "--config", str(cfg), "--out", str(tdir)]) == 0
    out = json.loads(capsys.readouterr().out)
    sha = out["trace_sha256"]
    text = cfg.read_text() + f"outputs:\n  trace_path: {tdir}\n"
    cfg2 = tmp_path / "cfg2.yaml"
    cfg2.write_text(text)
    capsys.readouterr()
    rc = main(["run", "--config", str(cfg2), "--out", str(tmp_path / "o1"),
               "--expect-trace-sha256", sha])
    assert rc == 0, capsys.readouterr().out
    rc = main(["run", "--config", str(cfg2), "--out", str(tmp_path / "o2"),
               "--expect-trace-sha256", "0" * 64])
    assert rc == 2
    assert "trace sha256" in capsys.readouterr().out.lower()
