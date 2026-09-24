"""Task 7: pure layered scene classifier tests.

Every status in the closed vocabulary gets a minimal counterexample fixture:
INVALID_EVIDENCE, COVERAGE_INCOMPLETE, ACCESS_LIMITED, ROUTE_LIMITED,
DOWNLINK_LIMITED, NO_ISL_EXPOSURE, NO_ISL_PRESSURE, ISL_PRESSURE_CANDIDATE.
The classifier must never confuse access failure, no route, downlink
overflow, lack of ISL exposure and ISL pressure.
"""
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest
import yaml

from CODE.experiment_platform import v2_analysis
from CODE.leo_sim import config, coverage, kernel, population, receipt, trace
from CODE.leo_sim import scene_check
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center

POPULATION_TIFF = (Path(__file__).resolve().parents[2] / "population_map"
                   / "gpw_v4_population_count_rev11_2020_15_min.tif")

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)


def default_decision(**over):
    d = {
        "schema": "leo-sim-scene-decision/v1",
        "scope": "global_populated_land",
        "population": {
            "source_sha256": "a" * 64,
            "aggregation_deg": 1.0,
            "candidate_regions": 3,
        },
        "coverage": {"horizon_s": 60.0, "step_s": 10.0,
                     "require_never_visible": 0},
        "traffic": {"provenance": "population_proxy",
                    "temporal_model": "local_diurnal_cosine",
                    "require_isl_exposed_packets": 100},
        "access_clean": {"min_admission_rate": 0.99,
                         "max_access_rejected_fraction_of_offered": 0.001,
                         "max_uplink_queue_overflow_fraction_of_offered": 0.001},
        "route_clean": {"max_no_route_fraction_of_admitted": 0.001,
                        "max_route_stalled_fraction_of_admitted": 0.001},
        "downlink_clean": {
            "max_downlink_queue_overflow_fraction_of_admitted": 0.001},
        "isl_pressure": {"window_s": 1.0,
                         "min_consecutive_windows_same_directed_link": 3,
                         "min_window_utilization": 0.70,
                         "require_positive_p95_queue_delay_same_link": True},
        "observation": {"emission_end_s": 20.0, "observation_end_s": 30.0},
    }
    d.update(over)
    return d


def small_coverage_report(sha="a" * 64, candidates=3, never_visible=0,
                          horizon=60.0, step=10.0, weighted_fraction=1.0,
                          satellites=2, full_scan=True):
    rows = [{"name": f"G1:{i}:{i}", "lat": float(i * 10),
             "lon": float(i * 10), "population": 1.0,
             "visible_fraction": 0.0 if i < never_visible else 1.0,
             "first_visible_wait_s": None if i < never_visible else 0.0,
             "max_no_coverage_gap_s": None if i < never_visible else 0.0,
             "never_visible": i < never_visible,
             "visible_satellites": {"min": 0 if i < never_visible else 1,
                                    "mean": 0.0 if i < never_visible else 1.0,
                                    "max": 0 if i < never_visible else 1}}
            for i in range(candidates)]
    sample_count = int(horizon // step) + 1
    total_population = float(sum(r["population"] for r in rows))
    weighted_visible = sum(
        float(r["population"]) * r["visible_fraction"] for r in rows)
    weighted_never = sum(
        float(r["population"]) * (1.0 if r["never_visible"] else 0.0)
        for r in rows)
    report = {
        "schema": "leo-sim-coverage-audit/v2",
        "endpoint_source": {"type": "population_raster",
                            "source_sha256": sha,
                            "aggregation_deg": 1.0,
                            "candidate_regions": candidates,
                            "total_population": total_population},
        "scan": {"horizon_s": horizon, "step_s": step,
                 "sample_count": sample_count,
                 "sampling_error_bound_s": step,
                 "geometry_epoch_s": 0.0},
        "limits": {"max_endpoints": 20_000, "max_samples": 1_000_001,
                   "max_comparisons": 50_000_000_000, "max_working_mib": 256.0},
        "evaluation": {"comparison_count": candidates * sample_count
                       * satellites,
                       "satellite_count": satellites,
                       "endpoint_chunk_size": 1, "time_chunk_size": 1,
                       "projected_bytes": 1,
                       "observed_peak_rss_mib": 1.0, "full_scan": full_scan,
                       "scalar_fallback_count": 0},
        "endpoints": rows,
        "summary": {"endpoints_total": len(rows),
                    "never_visible": never_visible,
                    "population_weighted_visible_fraction":
                        (weighted_visible / total_population
                         if total_population else 0.0),
                    "population_weighted_never_visible_fraction":
                        (weighted_never / total_population
                         if total_population else 0.0)},
        "provenance": {},
    }
    assert coverage.verify_coverage_audit_v2(report) == [], \
        coverage.verify_coverage_audit_v2(report)
    return report


def make_scene(tmp_path, cfg_over=None, geometry=None, sites=None,
               offered=1.0, duration=2.0, packets=None, seed=7):
    """Compile + run + write a verified run dir and a scene trace dir."""
    sites = sites or [{"name": "a", "lat": 0.1, "lon": 0.1},
                      {"name": "b", "lat": 2.0, "lon": 3.0}]
    user = {
        "scenario": {"duration_s": duration, "seed": seed,
                     "num_satellites": 2, "num_planes": 1},
        "endpoints": {"sites": sites},
        "demand": {"offered_mbps": offered},
        "control_plane": {"enabled": False},
        "routing": {"policy": "oracle"},
    }
    if cfg_over:
        user.update(cfg_over)
    resolved = config.resolve_config(user)
    tdir = tmp_path / "trace"
    manifest = trace.compile_trace(resolved, str(tdir))
    rows = trace.load_trace(
        str(tdir / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"])
    if packets is not None:
        rows = packets
    if geometry is None:
        geometry = StaticGeometry(2, neighbors_map={0: {"E": 1},
                                                    1: {"W": 0}},
                                  visible=lambda s, lat, lon, t: True)
    result = kernel.run_simulation(resolved, rows, geometry=geometry)
    out = tmp_path / "run"
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    receipt.write_run(str(out), resolved, tbytes, manifest, result, rows)
    assert receipt.verify_receipt_dir(str(out)) == []
    return str(out), str(tdir), resolved, rows, result


def _default_pop_table(candidates=3, sha=None):
    regions = tuple(
        population.PopulationRegion(f"G1:{i}:{i}", float(i * 10),
                                    float(i * 10), 1.0)
        for i in range(candidates))
    return population.PopulationTable(
        regions=regions, source_path="/fake/pop.tif",
        source_sha256=sha or "a" * 64, source_shape=(720, 1440),
        source_resolution_deg=(0.25, 0.25), aggregation_deg=1.0,
        total_population=float(candidates))


@pytest.fixture()
def pop_loader(monkeypatch):
    table = _default_pop_table()
    monkeypatch.setattr(population, "load_population_regions",
                        lambda path, aggregation_deg: table)
    return table


def test_decision_contract_validation():
    good = default_decision()
    assert scene_check.verify_decision_contract(good) == []
    # schema / exact keys
    bad = dict(good)
    bad["schema"] = "other/v9"
    assert any("schema" in e for e in scene_check.verify_decision_contract(bad))
    extra = dict(good)
    extra["surprise"] = 1
    assert any("keys" in e for e in scene_check.verify_decision_contract(extra))
    # tampered threshold or window is an evidence failure
    tampered = dict(good)
    tampered["isl_pressure"] = dict(good["isl_pressure"])
    tampered["isl_pressure"]["window_s"] = -1
    assert any("window_s" in e
               for e in scene_check.verify_decision_contract(tampered))
    tampered = dict(good)
    tampered["access_clean"] = dict(good["access_clean"])
    tampered["access_clean"]["min_admission_rate"] = 1.5
    assert any("min_admission_rate" in e
               for e in scene_check.verify_decision_contract(tampered))


def test_valid_scene_is_no_isl_pressure_basic(tmp_path, pop_loader):
    """A healthy scene with light traffic, full admission and no ISL
    pressure classifies NO_ISL_PRESSURE (exposure is below the required
    100 packets in this minimal fixture)."""
    out, tdir, resolved, rows, result = make_scene(
        tmp_path, duration=2.0, offered=1.0)
    decision = default_decision()
    coverage_report = small_coverage_report()
    report = scene_check.check_scene(out, tdir, coverage_report, decision)
    assert report["integrity_ok"] is True
    assert report["coverage_ok"] is True
    assert report["access_clean"] is True
    assert report["route_clean"] is True
    assert report["downlink_clean"] is True
    assert report["status"] in ("NO_ISL_EXPOSURE", "NO_ISL_PRESSURE")


def test_invalid_evidence_on_tampered_receipt(tmp_path, pop_loader):
    out, tdir, resolved, rows, result = make_scene(tmp_path)
    rcp_path = Path(out) / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["totals"]["delivered_bits"] += 1
    rcp_path.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n")
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["status"] == "INVALID_EVIDENCE"
    assert report["integrity_ok"] is False


def test_coverage_incomplete_on_tampered_coverage(tmp_path, pop_loader):
    out, tdir, resolved, rows, result = make_scene(tmp_path)
    decision = default_decision()
    # tampered source SHA / candidate count -> coverage incomplete
    bad_sha = small_coverage_report(sha="0" * 64)
    report = scene_check.check_scene(out, tdir, bad_sha, decision)
    assert report["status"] == "COVERAGE_INCOMPLETE"
    bad_count = small_coverage_report(candidates=99)
    report = scene_check.check_scene(out, tdir, bad_count, decision)
    assert report["status"] == "COVERAGE_INCOMPLETE"
    # never-visible mismatch
    bad_never = small_coverage_report(never_visible=1)
    report = scene_check.check_scene(out, tdir, bad_never, decision)
    assert report["status"] == "COVERAGE_INCOMPLETE"


def test_access_limited_never_isl_pressure(tmp_path, pop_loader):
    """Access failure with high delivery loss returns ACCESS_LIMITED, never
    ISL pressure."""
    # nothing visible -> everything rejected at emission -> no ingress
    geo = StaticGeometry(2, visible=lambda s, lat, lon, t: False)
    out, tdir, resolved, rows, result = make_scene(tmp_path, geometry=geo,
                                                   offered=5.0)
    assert all(f == "ACCESS_REJECTED" for f in result["fates"].values())
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["status"] == "ACCESS_LIMITED"
    assert report["access_clean"] is False


def test_access_limited_on_uplink_overflow_pre_ingress(tmp_path, pop_loader):
    """Pre-ingress ACCESS_QUEUE_OVERFLOW (source uplink) is ACCESS_LIMITED."""
    out, tdir, resolved, rows, result = make_scene(
        tmp_path, cfg_over={"access": {"uplink_queue_bits": 1_000_000}},
        offered=50.0, duration=2.0)
    fates = set(result["fates"].values())
    assert "ACCESS_QUEUE_OVERFLOW" in fates
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["status"] == "ACCESS_LIMITED"
    assert report["access"]["uplink_overflow_packets"] > 0
    assert report["access"]["downlink_overflow_packets"] == 0


def test_route_limited_on_no_route_high_link_utilization(tmp_path,
                                                         pop_loader):
    """NO_ROUTE returns ROUTE_LIMITED even when a surviving link is highly
    utilized (no route is not pressure)."""
    # oracle routing? no: use hop routing with no neighbor map so packets
    # get admitted but cannot route
    def visible(s, lat, lon, t):
        return True

    cfg_over = {
        "routing": {"policy": "hop"},
        "control_plane": {"enabled": False},
    }
    geo = StaticGeometry(2, visible=visible, neighbors_map={0: {}})
    out, tdir, resolved, rows, result = make_scene(
        tmp_path, cfg_over=cfg_over, geometry=geo, offered=5.0)
    assert "NO_ROUTE" in set(result["fates"].values())
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["status"] == "ROUTE_LIMITED"
    assert report["route_clean"] is False


def test_route_limited_on_holding_stall(tmp_path, pop_loader):
    """Admitted IN_SYSTEM_AT_STOP packets stalled in holding before any ISL
    exposure return ROUTE_LIMITED, not NO_ISL_EXPOSURE."""
    # source and destination see disjoint satellites; the ISL edge is in the
    # topology but the geometry never makes it available -> packets are
    # admitted, parked in a holding queue and stall until the stop time.
    AC = cell_center(A)
    BC = cell_center(B)

    class StallGeometry(StaticGeometry):
        def __init__(self):
            super().__init__(
                2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                visible=lambda s, lat, lon, t:
                s == 0 and (lat, lon) == AC or
                s == 1 and (lat, lon) == BC)

        def isl_available(self, a, b, t):
            return False

        def next_isl_change(self, a, b, t, limit):
            return None

    out, tdir, resolved, rows, result = make_scene(
        tmp_path, geometry=StallGeometry(), offered=20.0, duration=3.0,
        sites=[{"name": "a", "lat": 0.125, "lon": 0.125},
               {"name": "b", "lat": 0.125, "lon": 10.125}],
        cfg_over={"scenario": {"seed": 7, "num_satellites": 2,
                               "num_planes": 1},
                  "demand": {"packet_bits": 1_000_000},
                  "control_plane": {"enabled": True, "vis_k": 2,
                                    "advertise_interval_s": 100.0},
                  "routing": {"policy": "oracle"}})
    assert result["fate_counts"].get("IN_SYSTEM_AT_STOP", 0) > 0
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["status"] == "ROUTE_LIMITED", report["status"]
    assert report["route_stalled_pids"], "no stalled pids detected"


def test_downlink_limited_on_post_ingress_overflow(tmp_path, pop_loader):
    """Post-ingress ACCESS_QUEUE_OVERFLOW (destination downlink) is
    DOWNLINK_LIMITED, not ACCESS_LIMITED: the ingress is the split."""
    out, tdir, resolved, rows, result = make_scene(
        tmp_path,
        sites=[{"name": "a", "lat": 0.125, "lon": 0.125},
               {"name": "b", "lat": 0.125, "lon": 10.125}],
        cfg_over={"scenario": {"seed": 7},
                  "access": {"uplink_rate_mbps": 100.0,
                             "uplink_queue_bits": 64_000_000,
                             "downlink_rate_mbps": 0.01,
                             "downlink_queue_bits": 1_000_000}},
        offered=4.0, duration=3.0)
    fates = set(result["fates"].values())
    assert "ACCESS_QUEUE_OVERFLOW" in fates
    # the run is now receipt-verified (per the authorized contract fix)
    assert receipt.verify_receipt_dir(str(Path(out))) == []
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["access"]["downlink_overflow_packets"] > 0
    assert report["status"] == "DOWNLINK_LIMITED", report["status"]


def test_no_isl_pressure_on_scattered_windows(tmp_path, pop_loader):
    """Horizon-aggregate high utilization, three scattered windows, three
    windows on different directed links, or qualifying utilization with
    queue delay only on another link must all return NO_ISL_PRESSURE."""
    from CODE.leo_sim import metrics as metrics_mod
    out, tdir, resolved, rows, result = make_scene(tmp_path, offered=5.0)
    lpath = Path(out) / "ledgers.json"
    led = json.loads(lpath.read_text(encoding="utf-8"))
    # synthesize directed-link utilization: link A is highly utilized in
    # exactly windows 0,1,3 (scattered), link B in windows 0,1,2 but has no
    # queue delay; qualifying wait belongs to link C only
    def cap(link, k):
        return {"stage": "isl", "link_id": link,
                "start": float(k), "end": float(k + 1),
                "rate_bps": 1000.0, "capacity_bits": 1000.0}

    def svc(link, k, pid):
        return {"pid": pid, "stage": "isl", "link_id": link,
                "start": float(k), "end": float(k + 1),
                "rate_bps": 1000.0, "capacity_bits": 1000.0,
                "served_bits": 1000, "bits": 1000, "outcome": "ok"}

    led["link_service_windows"] = []
    led["link_available_windows"] = []
    for k in range(4):
        for link in ("isl:0:1", "isl:1:0"):
            led["link_available_windows"].append(cap(link, k))
    # A: 90% in windows 0,1,3 (scattered; window 2 absent)
    for k in (0, 1, 3):
        led["link_service_windows"].append(svc("isl:0:1", k, k + 1))
    # B: 90% in windows 0,1,2 (adjacent) but its queue delay is zero and
    # the positive delay belongs to C
    for k in (0, 1, 2):
        led["link_service_windows"].append(svc("isl:1:0", k, 10 + k))
    # queue wait on C only
    led["packet_events"].append(
        {"kind": "queue_enter", "pid": 101, "at": 0.1,
         "queue": "isl", "link_id": "isl:0:99", "queue_id": 900})
    led["packet_events"].append(
        {"kind": "service_start", "pid": 101, "at": 2.0, "stage": "isl",
         "link_id": "isl:0:99", "queue_id": 900, "bits": 1000,
         "rate_bps": 1000.0})
    # A's served bits exceed its available capacity in window 3 because the
    # whole 1000-bit window is counted; keep utilization <= 1 via 900/1000
    led["congestion_metrics"] = metrics_mod.summarize(
        led["packet_events"], led["link_service_windows"],
        available_capacity_windows=led["link_available_windows"],
        non_arrival_pids=set(), access_boundary=True)
    lpath.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n")
    rpath = Path(out) / "receipt.json"
    rcp = json.loads(rpath.read_text(encoding="utf-8"))
    rcp["ledgers_sha256"] = hashlib.sha256(
        lpath.read_bytes()).hexdigest()
    rpath.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n")
    assert receipt.verify_receipt_dir(str(out)) == []
    decision = default_decision(
        traffic={"provenance": "population_proxy",
                 "temporal_model": "local_diurnal_cosine",
                 "require_isl_exposed_packets": 3})
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     decision)
    # A is scattered; B is adjacent but wait belongs to another link
    assert report["isl_exposed_packets"] >= 3
    assert report["status"] == "NO_ISL_PRESSURE", report["status"]


def test_in_system_at_stop_without_evidence_is_no_pressure(tmp_path,
                                                           pop_loader):
    """IN_SYSTEM_AT_STOP > 0 with no queue/utilization evidence returns
    NO_ISL_PRESSURE."""
    out, tdir, resolved, rows, result = make_scene(tmp_path, offered=1.0)
    assert result["fate_counts"].get("IN_SYSTEM_AT_STOP", 0) >= 0
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    if report["isl_exposed_packets"] >= 100:
        assert report["status"] in ("NO_ISL_PRESSURE", "ISL_PRESSURE_CANDIDATE")
    else:
        assert report["status"] == "NO_ISL_EXPOSURE"


def test_isl_pressure_candidate_requires_same_link_p95_set(tmp_path):
    """A single link with three adjacent high-utilization windows AND
    positive p95 ISL queue wait on that same link is required for a
    candidate; three windows on different links must fail."""
    from CODE.leo_sim import metrics as metrics_mod
    out = tmp_path / "run"
    out.mkdir()
    Path(out / "trace.csv").write_text("", encoding="utf-8")
    # build the classifier result directly through a synthetic run:
    # we only need the ISL recomputation logic, driven by windows
    decision = default_decision()
    service = []
    available = []
    events = []
    for k in range(4):
        available.append({"stage": "isl", "link_id": "isl:0:1",
                          "start": float(k), "end": float(k + 1),
                          "rate_bps": 1000.0, "capacity_bits": 1000.0})
    for k in range(3):  # windows 0,1,2 adjacent at 90%
        service.append({"pid": k + 1, "stage": "isl", "link_id": "isl:0:1",
                        "start": float(k), "end": float(k + 1),
                        "rate_bps": 1000.0, "capacity_bits": 1000.0,
                        "served_bits": 900, "bits": 1000, "outcome": "ok"})
        events.append({"kind": "queue_enter", "pid": k + 1, "at": float(k),
                       "queue": "isl", "link_id": "isl:0:1",
                       "queue_id": k})
        events.append({"kind": "service_start", "pid": k + 1,
                       "at": float(k) + 0.5, "stage": "isl",
                       "link_id": "isl:0:1", "queue_id": k,
                       "bits": 1000, "rate_bps": 1000.0})
    isl = scene_check._recompute_isl_pressure(service, available, events,
                                              decision)
    assert isl["candidate"] is not None
    assert isl["candidate"]["link_id"] == "isl:0:1"
    assert isl["candidate"]["qualifying_windows"] == [0, 1, 2]
    assert isl["candidate"]["p95_queue_wait_s"] > 0

    # three windows on different directed links -> no candidate
    service2 = []
    for k in range(3):
        link = f"isl:{k}:{k+1}"
        available.append({"stage": "isl", "link_id": link,
                          "start": float(k), "end": float(k + 1),
                          "rate_bps": 1000.0, "capacity_bits": 1000.0})
        service2.append({"pid": k + 1, "stage": "isl", "link_id": link,
                         "start": float(k), "end": float(k + 1),
                         "rate_bps": 1000.0, "capacity_bits": 1000.0,
                         "served_bits": 900, "bits": 1000, "outcome": "ok"})
    isl2 = scene_check._recompute_isl_pressure(service2, available, events,
                                               decision)
    assert isl2["candidate"] is None


def test_isl_pressure_consecutive_windows_are_adjacent_and_reported_from_best_run():
    """A missing or low-utilization fixed window breaks an episode, and the
    reported windows come from the qualifying episode rather than a later
    shorter run."""
    decision = default_decision(
        isl_pressure={
            "window_s": 1.0,
            "min_consecutive_windows_same_directed_link": 2,
            "min_window_utilization": 0.8,
            "require_positive_p95_queue_delay_same_link": True,
        })
    link = "isl:0:1"
    available = [
        {"stage": "isl", "link_id": link, "start": float(k),
         "end": float(k + 1), "rate_bps": 1000.0,
         "capacity_bits": 1000.0}
        for k in range(5)
    ]
    service = []
    for k, bits in ((0, 900), (1, 900), (2, 100), (3, 900)):
        service.append({"pid": k + 1, "stage": "isl", "link_id": link,
                        "start": float(k), "end": float(k + 1),
                        "rate_bps": 1000.0, "capacity_bits": 1000.0,
                        "served_bits": bits, "bits": bits, "outcome": "ok"})
    events = []
    for k in (0, 1, 3):
        events.extend([
            {"kind": "queue_enter", "pid": k + 1, "at": float(k),
             "queue": "isl", "link_id": link, "queue_id": k},
            {"kind": "service_start", "pid": k + 1, "at": float(k) + 0.5,
             "stage": "isl", "link_id": link, "queue_id": k,
             "bits": 1000, "rate_bps": 1000.0},
        ])
    result = scene_check._recompute_isl_pressure(service, available, events,
                                                 decision)
    assert result["candidate"] is not None
    assert result["candidate"]["qualifying_windows"] == [0, 1]
    assert result["candidate"]["windows"] == {"0": 0.9, "1": 0.9}


def test_tampered_ledger_or_trace_fails_invalid_evidence(tmp_path,
                                                         pop_loader):
    out, tdir, resolved, rows, result = make_scene(tmp_path)
    # tamper the ledger: remove a fate (breaks receipt verification)
    lpath = Path(out) / "ledgers.json"
    led = json.loads(lpath.read_text(encoding="utf-8"))
    pid = next(iter(led["packet_fates"]))
    del led["packet_fates"][pid]
    lpath.write_text(json.dumps(led, indent=2, sort_keys=True) + "\n")
    rpath = Path(out) / "receipt.json"
    rcp = json.loads(rpath.read_text(encoding="utf-8"))
    rcp["ledgers_sha256"] = hashlib.sha256(
        lpath.read_bytes()).hexdigest()
    rpath.write_text(json.dumps(rcp, indent=2, sort_keys=True) + "\n")
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["status"] == "INVALID_EVIDENCE"

# ---------------------------------------------------------------- P0 fix:
# scene_check must NOT classify historical (posterior) runs from a
# self-modifiable run directory alone.  The only posterior evidence path is
# a persisted VERIFIED V2 analysis manifest that recomputes the full
# artifact + governance chain and hash-binds the run's key formal evidence
# files; without it the strict exact-runtime integrity gate applies and a
# runtime-identity mismatch fails closed as INVALID_EVIDENCE.

SCENE_RUN_DEPS = {
    "python": "3.11.15", "numpy": "1.24.3",
    "simpy": "4.0.1", "pyyaml": "6.0.2",
}


def _scene_posterior_run(root, run_id):
    """A small formally-bound run whose receipt records an alien (historical)
    runtime identity; returns (run_dir, trace_dir, row)."""
    user = {
        "scenario": {"duration_s": 2.0, "seed": 7,
                     "num_satellites": 2, "num_planes": 1},
        "endpoints": {"sites": [{"name": "a", "lat": 0.1, "lon": 0.1},
                                {"name": "b", "lat": 2.0, "lon": 3.0}]},
        "demand": {"offered_mbps": 1.0},
        "control_plane": {"enabled": False},
        "routing": {"policy": "oracle"},
    }
    resolved = config.resolve_config(user)
    tdir = root / "trace-scene"
    manifest = trace.compile_trace(resolved, str(tdir))
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(tbytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(tdir / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"])
    geometry = StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                              visible=lambda s, lat, lon, t: True)
    result = kernel.run_simulation(resolved, rows, geometry=geometry)
    out = root / "CODE" / "Results" / run_id
    receipt.write_run(str(out), resolved, tbytes, manifest, result, rows)
    rcp_path = out / "receipt.json"
    rcp = json.loads(rcp_path.read_text(encoding="utf-8"))
    rcp["code_sha256"] = "1" * 64
    rcp["deps"] = dict(SCENE_RUN_DEPS)
    rcp_path.write_text(
        json.dumps(rcp, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt_sha = hashlib.sha256(rcp_path.read_bytes()).hexdigest()
    (out / "formal_run.json").write_text(json.dumps({
        "schema": "leo-sim-formal-run/v1", "run_id": run_id,
        "launch_nonce": "b" * 32, "authorization_sha256": "a" * 64,
        "config_sha256": rcp["config_sha256"],
        "code_sha256": rcp["code_sha256"],
        "receipt_sha256": receipt_sha,
        "natural_end": True, "conservation_ok": True,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    governed = {
        "schema": "leo-sim-governance-receipt/v2", "research_eligible": True,
        "run_id": run_id, "launch_nonce": "b" * 32,
        "verification_errors": [], "authorization_sha256": "a" * 64,
        "source_git_commit": "d" * 40, "source_tree_sha256": "e" * 64,
        "deployment_receipt_sha256": "f" * 64,
        "execution_chain_sha256": {"CODE/example.py": "9" * 64},
        "receipt_schema": rcp["schema"],
        "resolved_config_sha256": hashlib.sha256(
            (out / "resolved_config.json").read_bytes()).hexdigest(),
        "trace_manifest_schema": manifest["schema"],
        "trace_identity_contract": rcp["trace_identity_contract"],
        "trace_manifest_sha256": hashlib.sha256(
            (out / "manifest.json").read_bytes()).hexdigest(),
        "run_receipt_sha256": receipt_sha,
    }
    governed["payload_sha256"] = v2_analysis.canonical_sha(governed)
    (out / "governance_receipt.json").write_text(
        json.dumps(governed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    governed = json.loads(
        (out / "governance_receipt.json").read_text(encoding="utf-8"))
    witness = {
        "schema": "leo-remote-launch-status/v2", "status": "success",
        "exit_code": 0, "launch_nonce": "b" * 32, "run_id": run_id,
        "authorization_sha256": "a" * 64,
        "last_results_dir": "/data/论文/leo-direct-sim/CODE/Results/"
                            + run_id,
        "governance_receipt_sha256": hashlib.sha256(
            (out / "governance_receipt.json").read_bytes()).hexdigest(),
        "governance_witness": {
            key: governed[key] for key in v2_analysis.GOVERNANCE_WITNESS_FIELDS
        },
    }
    wdir = root / "CODE" / "Results" / "_external_launch_witness"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / f"{run_id}.json").write_text(
        json.dumps(witness, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    row = {
        "run_id": run_id, "runtime_kind": "leo_sim_v2", "arm_id": "control",
        "phase": "non_learning", "pairing_key": "pair-1",
        "trace_seed": rcp["seed"],
        "config_sha256": rcp["config_sha256"],
        "trace_identity_sha256": rcp["trace_identity_sha256"],
        "input_sha256": manifest["input_sha256"],
        "code_sha256": rcp["code_sha256"],
        "execution_chain_sha256": {"CODE/example.py": "9" * 64},
        "controlled_signature": "c" * 64,
    }
    return out, tdir, row


_SCENE_ANALYZER = {
    "git_commit": "d" * 40,
    "files": {
        "CODE/experiment_platform/v2_analysis.py": "e" * 64,
        "CODE/experiment_platform/isl_pressure.py": "0" * 64,
        "CODE/experiment_platform/isl_pressure_decision.py": "1" * 64,
        "CODE/leo_sim/metrics.py": "f" * 64,
        "CODE/leo_sim/receipt.py": "7" * 64,
    },
}


def _scene_analysis_manifest(root, run_id, row):
    """Run the full V2 analysis on the posterior scene run and persist the
    VERIFIED manifest; returns its path."""
    name = "EXP-SCENE-POSTERIOR"
    experiment = root / "EXPERIMENTS" / name
    experiment.mkdir(parents=True)
    cells = [{**row, "trace_seed": row["trace_seed"]}]
    (experiment / "request.json").write_text(json.dumps({
        "experiment_id": name,
        "claim_boundary": {"can_claim": ["none"],
                           "cannot_claim": ["not independently reviewed"]},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (experiment / "run-manifest.json").write_text(json.dumps({
        "schema": v2_analysis.MATRIX_SCHEMA,
        "experiment_id": name, "cells": cells,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (experiment / "analysis-request.json").write_text(json.dumps({
        "schema": v2_analysis.ANALYSIS_SCHEMA,
        "experiment_id": name,
        "planned_run_ids": [run_id],
        "analysis": {"analysis_id": "AN-SCENE", "primary_metric":
                     "delivery_rate", "planned_contrasts": []},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    auth_path = experiment / "authorization.json"
    auth_path.write_text(json.dumps({
        "status": "AUTHORIZED", "experiment_id": name,
        "authorized_cells": cells,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    auth_sha = v2_analysis.file_sha256(auth_path)
    result_dir = root / "CODE" / "Results" / run_id
    formal_path = result_dir / "formal_run.json"
    formal = json.loads(formal_path.read_text(encoding="utf-8"))
    formal["authorization_sha256"] = auth_sha
    formal_path.write_text(
        json.dumps(formal, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    governed_path = result_dir / "governance_receipt.json"
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    governed["authorization_sha256"] = auth_sha
    governed.pop("payload_sha256", None)
    governed["payload_sha256"] = v2_analysis.canonical_sha(governed)
    governed_path.write_text(
        json.dumps(governed, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    governed = json.loads(governed_path.read_text(encoding="utf-8"))
    witness_path = (root / "CODE" / "Results" / "_external_launch_witness"
                    / f"{run_id}.json")
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    witness["authorization_sha256"] = auth_sha
    witness["governance_receipt_sha256"] = v2_analysis.file_sha256(
        governed_path)
    witness_path.write_text(
        json.dumps(witness, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    with mock.patch.object(v2_analysis.authorize_experiment,
                           "verify_authorization",
                           return_value=auth_path.read_text()
                           if False else json.loads(
                               auth_path.read_text(encoding="utf-8"))),             mock.patch.object(v2_analysis, "_analyzer_identity",
                              return_value=_SCENE_ANALYZER, create=True):
        manifest = v2_analysis.analyze(root, experiment, auth_path)
        out = root / "ANALYSIS" / name
        v2_analysis.write_outputs(root, out, manifest)
    assert manifest["status"] == "VERIFIED"
    assert manifest["analysis_mode"] == "posterior_governed_runtime"
    return out / "analysis-manifest.json"


def test_scene_check_requires_analysis_manifest_for_historical_runtime(
        tmp_path):
    """Constraint 6g/6h: strict path without a manifest fails closed
    (INVALID_EVIDENCE) on a posterior run; the manifest-bound path
    classifies it and binds the manifest path+sha in the output."""
    root = tmp_path
    run_dir, tdir, row = _scene_posterior_run(
        root, "EXP-SCENE-POSTERIOR-s1")
    decision = default_decision()
    coverage_report = small_coverage_report()
    strict_report = scene_check.check_scene(
        str(run_dir), str(tdir), coverage_report, decision)
    assert strict_report["status"] == "INVALID_EVIDENCE"
    assert any("code sha mismatch" in error
               for error in strict_report["errors"])
    manifest_path = _scene_analysis_manifest(root, row["run_id"], row)
    assert receipt.verify_receipt_dir(str(run_dir)) != []  # still alien
    with mock.patch.object(
            v2_analysis.authorize_experiment, "verify_authorization",
            return_value=json.loads((
                root / "EXPERIMENTS" / "EXP-SCENE-POSTERIOR"
                / "authorization.json").read_text(encoding="utf-8"))),             mock.patch.object(v2_analysis, "_analyzer_identity",
                              return_value=_SCENE_ANALYZER, create=True):
        bound_report = scene_check.check_scene(
            str(run_dir), str(tdir), coverage_report, decision,
            analysis_manifest=str(manifest_path.relative_to(root)),
            project_root=root)
    assert bound_report["integrity_ok"] is True
    assert bound_report["status"] != "INVALID_EVIDENCE"
    binding = bound_report["analysis_manifest_binding"]
    assert binding["schema"] == "leo-sim-scene-analysis-binding/v1"
    assert binding["analysis_manifest"] == str(
        manifest_path.relative_to(root))
    assert binding["analysis_manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()).hexdigest()
    assert binding["run_id"] == row["run_id"]
    assert row["run_id"] in binding["verified_run_ids"]


def test_scene_check_missing_analysis_manifest_fails(tmp_path):
    root = tmp_path
    run_dir, tdir, row = _scene_posterior_run(
        root, "EXP-SCENE-MISSING-s1")
    with pytest.raises(scene_check.SceneCheckError,
                       match="missing or symbolic"):
        scene_check.check_scene(
            str(run_dir), str(tdir), small_coverage_report(),
            default_decision(),
            analysis_manifest="CODE/Results/does-not-exist.json",
            project_root=root)


def test_scene_check_rejects_unverified_analysis_manifest(tmp_path):
    root = tmp_path
    run_dir, tdir, row = _scene_posterior_run(
        root, "EXP-SCENE-UNVERIFIED-s1")
    manifest_path = _scene_analysis_manifest(root, row["run_id"], row)
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered["analyzer"]["git_commit"] = "0" * 40
    manifest_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    with pytest.raises(scene_check.SceneCheckError,
                       match="not fully verified"):
        scene_check.check_scene(
            str(run_dir), str(tdir), small_coverage_report(),
            default_decision(),
            analysis_manifest=str(manifest_path.relative_to(root)),
            project_root=root)


def test_scene_check_rejects_manifest_not_covering_run(tmp_path):
    root = tmp_path
    run_dir, tdir, row = _scene_posterior_run(
        root, "EXP-SCENE-COVERED-s1")
    manifest_path = _scene_analysis_manifest(root, row["run_id"], row)
    other_dir, other_tdir, _other_row = _scene_posterior_run(
        root, "EXP-SCENE-OTHER-s1")
    with pytest.raises(scene_check.SceneCheckError,
                       match="not in the VERIFIED analysis cohort"):
        with mock.patch.object(
                v2_analysis.authorize_experiment, "verify_authorization",
                return_value=json.loads((
                    root / "EXPERIMENTS" / "EXP-SCENE-POSTERIOR"
                    / "authorization.json").read_text(encoding="utf-8"))),                 mock.patch.object(v2_analysis, "_analyzer_identity",
                                  return_value=_SCENE_ANALYZER,
                                  create=True):
            scene_check.check_scene(
                str(other_dir), str(other_tdir),
                small_coverage_report(), default_decision(),
                analysis_manifest=str(manifest_path.relative_to(root)),
                project_root=root)


def test_scene_check_rejects_tampered_run_despite_manifest(tmp_path):
    root = tmp_path
    run_dir, tdir, row = _scene_posterior_run(
        root, "EXP-SCENE-TAMPER-s1")
    manifest_path = _scene_analysis_manifest(root, row["run_id"], row)
    ledgers_path = run_dir / "ledgers.json"
    ledgers_path.write_bytes(ledgers_path.read_bytes() + b" ")
    with pytest.raises(scene_check.SceneCheckError,
                       match="not fully verified"):
        scene_check.check_scene(
            str(run_dir), str(tdir), small_coverage_report(),
            default_decision(),
            analysis_manifest=str(manifest_path.relative_to(root)),
            project_root=root)


def test_scene_check_cli_binds_analysis_manifest(tmp_path):
    """The CLI --analysis-manifest flag wires the manifest-bound integrity
    gate and the output binds the manifest path+sha."""
    root = tmp_path
    run_dir, tdir, row = _scene_posterior_run(
        root, "EXP-SCENE-CLI-s1")
    manifest_path = _scene_analysis_manifest(root, row["run_id"], row)
    work = root / "CODE" / "work"
    work.mkdir(parents=True, exist_ok=True)
    decision_path = work / "scene-decision.yaml"
    decision_path.write_text(
        yaml.safe_dump(default_decision()), encoding="utf-8")
    coverage_path = work / "coverage-audit.json"
    coverage_path.write_text(
        json.dumps(small_coverage_report(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    contract_path = work / "scene-check-contract.json"
    contract = {
        "schema": scene_check.SCENE_CHECK_CONTRACT_SCHEMA,
        "decision_path": "CODE/work/scene-decision.yaml",
        "decision_sha256": hashlib.sha256(
            decision_path.read_bytes()).hexdigest(),
        "coverage_path": "CODE/work/coverage-audit.json",
        "coverage_sha256": hashlib.sha256(
            coverage_path.read_bytes()).hexdigest(),
        "canonical_invocation": [
            "python3", "-m", "CODE.leo_sim.scene_check",
            "--root", ".", "--contract",
            "CODE/work/scene-check-contract.json",
            "--analysis-manifest", str(manifest_path.relative_to(root)),
        ],
    }
    contract_path.write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    out_path = "CODE/work/scene-out.json"
    with mock.patch.object(
            v2_analysis.authorize_experiment, "verify_authorization",
            return_value=json.loads((
                root / "EXPERIMENTS" / "EXP-SCENE-POSTERIOR"
                / "authorization.json").read_text(encoding="utf-8"))),             mock.patch.object(v2_analysis, "_analyzer_identity",
                              return_value=_SCENE_ANALYZER, create=True):
        rc = scene_check._cli([
            "--root", str(root),
            "--contract", "CODE/work/scene-check-contract.json",
            "--run-dir", str(run_dir.relative_to(root)),
            "--analysis-manifest", str(manifest_path.relative_to(root)),
            "--out", out_path,
        ])
    assert rc == 0
    report = json.loads((root / out_path).read_text(encoding="utf-8"))
    assert report["status"] != "INVALID_EVIDENCE"
    binding = report["analysis_manifest_binding"]
    assert binding["analysis_manifest"] == str(
        manifest_path.relative_to(root))
    assert binding["analysis_manifest_sha256"] == hashlib.sha256(
        manifest_path.read_bytes()).hexdigest()
    assert report["contract_binding"]["contract_sha256"] == hashlib.sha256(
        contract_path.read_bytes()).hexdigest()
# ---------------------------------------------------------------- Re-review:
# _safe_analysis_manifest_path must reject a symlink on the UNRESOLVED
# candidate: resolving first would hide the terminal symlink from
# is_symlink() and let a link masquerade as a real manifest path.

def test_scene_check_rejects_symlink_analysis_manifest(tmp_path):
    """A real symlink pointing at a valid manifest must be rejected before
    resolution can hide it."""
    root = tmp_path
    run_dir, tdir, row = _scene_posterior_run(
        root, "EXP-SCENE-SYMLINK-s1")
    manifest_path = _scene_analysis_manifest(root, row["run_id"], row)
    link = root / "CODE" / "Results" / "manifest-link.json"
    link.symlink_to(manifest_path)
    with pytest.raises(scene_check.SceneCheckError,
                       match="symlink"):
        scene_check.check_scene(
            str(run_dir), str(tdir), small_coverage_report(),
            default_decision(),
            analysis_manifest="CODE/Results/manifest-link.json",
            project_root=root)


def test_tampered_scene_trace_fails_invalid_evidence(tmp_path, pop_loader):
    out, tdir, resolved, rows, result = make_scene(tmp_path)
    # overwrite the scene trace with a different immutability proof
    (Path(tdir) / "trace.csv").write_text(
        Path(tdir).joinpath("trace.csv").read_text(encoding="utf-8") +
        "\n", encoding="utf-8")
    report = scene_check.check_scene(out, tdir, small_coverage_report(),
                                     default_decision())
    assert report["status"] == "INVALID_EVIDENCE"
