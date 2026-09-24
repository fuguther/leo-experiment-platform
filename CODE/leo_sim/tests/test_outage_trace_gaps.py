"""Gap-fill tests: ISL Gilbert-Elliott outage and trace CSV fail-closed."""
import pytest

from CODE.leo_sim import kernel, trace
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)

NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def test_isl_random_outage_in_flight():
    # fast-flapping ISL channel + long service window: the link goes down
    # strictly inside the transmission -> the packet fails mid-flight
    cfg = make_cfg({
        "links": {"isl_rate_mbps": 1.0, "ge_enabled": True,
                  "ge_gsl": {"mean_good_s": 1e9, "mean_bad_s": 1e-9},
                  "ge_isl": {"mean_good_s": 0.05, "mean_bad_s": 0.05}},
        "scenario": {"duration_s": 30.0},
    })
    geo = StaticGeometry(2, neighbors_map=NB, visible=VIS)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "RANDOM_OUTAGE_IN_FLIGHT"
    eff = res["mechanisms"]["effective"]
    assert eff["ge_isl_queries"] > 0
    # only the service time up to the outage is occupied (full would be 8 s)
    assert 0.0 < res["occupied"]["isl_s"] < 8.0


def test_isl_outage_stream_is_independent_of_gsl_stream():
    # GSL goes down immediately: the packet dies on the uplink and the ISL
    # channel is never even queried
    cfg = make_cfg({
        "links": {"ge_enabled": True,
                  "ge_gsl": {"mean_good_s": 1e-9, "mean_bad_s": 1e9},
                  "ge_isl": {"mean_good_s": 1e9, "mean_bad_s": 1e-9}},
        "scenario": {"duration_s": 5.0},
    })
    geo = StaticGeometry(2, neighbors_map=NB, visible=VIS)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "RANDOM_OUTAGE_IN_FLIGHT"
    eff = res["mechanisms"]["effective"]
    assert eff["ge_gsl_queries"] > 0
    assert eff["ge_isl_queries"] == 0


def test_trace_csv_duplicate_packet_id_rejected(tmp_path):
    # both rows are otherwise valid and in-range: the ONLY defect is the
    # duplicate id, so the duplicate branch is provably reached
    src = tmp_path / "dup.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.1,31.0,121.0,40.0,116.0,8000000,\n"
        "1,0.2,31.0,121.0,51.5,0.1,8000000,\n")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    with pytest.raises(trace.TraceError, match="duplicate packet_id"):
        trace.compile_trace(cfg, str(tmp_path / "t"))


def test_trace_csv_out_of_range_coordinates_rejected(tmp_path):
    src = tmp_path / "bad.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.1,91.0,121.0,40.0,116.0,8000000,\n")
    cfg = make_cfg()
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    with pytest.raises(ValueError):
        trace.compile_trace(cfg, str(tmp_path / "t"))


def test_trace_csv_mode_needs_no_endpoints_sites(tmp_path):
    src = tmp_path / "ok.csv"
    src.write_text(
        "packet_id,emit_time_s,src_lat,src_lon,dst_lat,dst_lon,bits,deadline_at_s\n"
        "1,0.1,31.0,121.0,40.0,116.0,8000000,\n")
    cfg = make_cfg()  # make_cfg sets no endpoints.sites
    assert cfg["config"]["endpoints"]["sites"] == []
    cfg["config"]["demand"]["mode"] = "csv"
    cfg["config"]["demand"]["csv_path"] = str(src)
    m = trace.compile_trace(cfg, str(tmp_path / "t"))
    assert m["offered_packets"] == 1
    assert m["active_endpoints"] == 2  # src cell + dst cell, straight from CSV


def test_trace_compile_enforces_max_packets(tmp_path):
    cfg = make_cfg({
        "execution": {"max_packets": 2},
        "demand": {"offered_mbps": 50.0, "packet_bits": 100_000},
        "endpoints": {"sites": [{"name": "a", "lat": 0.1, "lon": 0.1},
                                {"name": "b", "lat": 2.0, "lon": 3.0}]},
        "scenario": {"duration_s": 5.0},
    })
    # 50 Mbps / 1e5 bits over 5 s >> 2 packets
    with pytest.raises(trace.TraceError, match="max_packets"):
        trace.compile_trace(cfg, str(tmp_path / "t"))
