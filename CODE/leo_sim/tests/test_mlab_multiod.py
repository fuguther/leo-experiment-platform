"""Tests for deterministic M-Lab measurement -> multi-OD endpoint selection."""
from pathlib import Path

import pytest

from CODE.leo_sim import config, grid, trace


def _write_source(path: Path) -> None:
    path.write_text(
        "client_city,client_lat,client_lon,server_city,server_lat,server_lon,"
        "hour_utc,sample_count,mean_throughput_mbps\n"
        # A -> B -> C -> A is the selected strongly connected component.
        "A,0.1,0.1,B,10.1,10.1,0,10,100.0\n"
        "B,10.1,10.1,C,20.1,20.1,0,10,90.0\n"
        "C,20.1,20.1,A,0.1,0.1,0,10,80.0\n"
        "B,10.1,10.1,A,0.1,0.1,0,10,70.0\n"
        # This one-way leaf must not be selected because it cannot emit into
        # the selected closed measurement subgraph.
        "D,30.1,30.1,A,0.1,0.1,0,10,1000.0\n",
        encoding="utf-8",
    )


def _cfg(max_sites=8):
    return config.resolve_config({
        "scenario": {"duration_s": 20.0, "seed": 7},
        "endpoints": {
            "sites": [],
            "mlab_auto": True,
            "mlab_max_sites": max_sites,
        },
        "demand": {
            "mode": "mlab",
            "offered_mbps": 5.0,
            "packet_bits": 100_000,
        },
    })


def test_mlab_auto_selects_closed_component_and_records_mapping(tmp_path, monkeypatch):
    source = tmp_path / "mlab.csv"
    _write_source(source)
    monkeypatch.setattr(trace, "REPO_MLAB_CSV", source)

    manifest = trace.compile_trace(_cfg(), str(tmp_path / "out"))
    summary = manifest["provenance_contract"]["measurement_summary"]
    selection = summary["endpoint_selection"]

    assert manifest["provenance"] == "measurement_proxy"
    assert selection["method"] == "largest_strongly_connected_component"
    assert selection["selected_aggregate_cells"] == 3
    assert selection["max_sites"] == 8
    assert selection["source_weighting"] == "measured_outgoing_throughput"
    assert manifest["active_endpoints"] == 3

    with (tmp_path / "out" / "trace.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(__import__("csv").DictReader(fh))
    selected = {r["src_grid_id"] for r in rows} | {r["dst_grid_id"] for r in rows}
    a = grid.aggregate_id(grid.grid_id(0.1, 0.1, 0.25), 1.0)
    d = grid.aggregate_id(grid.grid_id(30.1, 30.1, 0.25), 1.0)
    assert a in selected
    assert d not in selected


def test_mlab_auto_is_byte_reproducible_and_respects_cap(tmp_path, monkeypatch):
    source = tmp_path / "mlab.csv"
    _write_source(source)
    monkeypatch.setattr(trace, "REPO_MLAB_CSV", source)
    cfg = _cfg(max_sites=2)

    first = trace.compile_trace(cfg, str(tmp_path / "first"))
    second = trace.compile_trace(cfg, str(tmp_path / "second"))
    assert first["provenance_contract"]["measurement_summary"]["endpoint_selection"][
        "selected_aggregate_cells"
    ] == 2
    for name in ("trace.csv", "manifest.json"):
        assert (tmp_path / "first" / name).read_bytes() == (
            tmp_path / "second" / name
        ).read_bytes()


def test_mlab_auto_requires_a_valid_cap(tmp_path):
    with pytest.raises(config.ConfigError, match="mlab_max_sites"):
        config.resolve_config({
            "endpoints": {"sites": [], "mlab_auto": True, "mlab_max_sites": 1},
            "demand": {"mode": "mlab"},
        })


def test_mlab_auto_is_only_for_mlab_mode():
    with pytest.raises(config.ConfigError, match="mlab_auto"):
        config.resolve_config({
            "endpoints": {"sites": [], "mlab_auto": True},
            "demand": {"mode": "uniform"},
        })
