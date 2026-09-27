"""T1-COMPLETE P7/P8: asynchronous execution in the real forwarding path."""
from __future__ import annotations

import pytest

from CODE.leo_sim import async_routing as ar, kernel, time_alignment as ta
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=VIS)


def _cfg(mode, *, bins=4, service=2.0, install=0.5, interval=1.0,
         window=10.0, arm="candidate"):
    return make_cfg({
        "scenario": {"duration_s": 20.0},
        "demand": {"packet_bits": 800_000, "offered_mbps": 2.0},
        "execution": {"decision_observation_mode": "frozen",
                      "compute_delay_s": service,
                      "compute_servers_per_satellite": 0},
        "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
        "time_alignment": {"enabled": True, "arm": arm,
                           "execution_mode": mode},
        "async_routing": {"enabled": True, "update_interval_s": interval,
                          "valid_window_s": window, "install_delay_s": install,
                          "window_bins": bins},
    })


def _run(cfg, rows=None):
    sink, timeline = [], []
    rows = rows if rows is not None else [row(i, 1.0 + 0.5 * i, A, B)
                                          for i in range(1, 6)]
    res = kernel.run_simulation(cfg, rows, geometry=_geo(),
                                decision_sink=sink, timeline_sink=timeline)
    return res, sink, timeline


def _events(timeline, milestone):
    return [m for m in timeline if m.get("milestone") == milestone]


def test_async_window_requests_computes_and_only_then_installs():
    _res, _sink, timeline = _run(_cfg("async_window"))
    req = _events(timeline, "schedule_update_requested")
    computed = _events(timeline, "schedule_update_computed")
    installed = _events(timeline, "schedule_installed")
    assert req, timeline[:5]
    assert computed and installed
    assert req[0]["bins"] == 4, "async_window must cost four bins"
    # the install instant is the actual finish plus the install delay
    assert installed[0]["at"] == pytest.approx(computed[0]["at"] + 0.5)
    assert installed[0]["version"] == 1


def test_the_async_update_uses_the_shared_compute_pool():
    _res, _sink, timeline = _run(_cfg("async_window"))
    req = [m for m in _events(timeline, "compute_request")
           if m.get("pid") is None]
    finish = [m for m in _events(timeline, "compute_finish")
              if m.get("pid") is None]
    assert req and finish, "an async update must consume the compute pool"
    assert finish[0]["at"] - req[0]["at"] == pytest.approx(2.0)
    assert req[0]["compute_job_id"]
    assert req[0]["sat"] == 0
    assert finish[0]["service_s"] == pytest.approx(2.0)


def test_a_packet_during_compute_still_uses_the_old_state_and_never_v2():
    _res, sink, timeline = _run(_cfg("async_window", service=0.5, install=0.2))
    installed = _events(timeline, "schedule_installed")
    assert installed
    first_install = installed[0]["at"]
    queries = _events(timeline, "schedule_query")
    assert queries
    for q in queries:
        if q["at"] < first_install:
            assert q.get("version") in (None, 1)
    # and after the install at least one query sees the installed version
    after = [q for q in queries if q["at"] >= first_install]
    assert after, "the fixture must query after the install"
    assert any(q.get("version") == 1 for q in after)


def test_async_point_installs_a_single_bin_table():
    _res, _sink, timeline = _run(_cfg("async_point", bins=1))
    req = _events(timeline, "schedule_update_requested")
    assert req
    assert req[0]["bins"] == 1
    installed = _events(timeline, "schedule_installed")
    assert installed[0]["bins"] == 1


def test_every_forward_decision_records_its_async_order():
    _res, sink, _tl = _run(_cfg("async_window"))
    forwards = [r for r in sink if r.get("kind") == "forward"]
    assert forwards
    for r in forwards:
        audit = r["observation_at_start"]["time_alignment"]
        assert audit is not None
        assert audit["execution_mode"] == "async_window"
        assert audit["bins"] == 4
        assert audit["scope"][0] == r["sat"]
        assert "installed schedule table" in audit["source"]
        # the applied order is a permutation of the legal candidates
        assert sorted(audit["applied_order"]) == sorted(r["candidates"])


def test_a_low_load_run_does_not_compute_per_packet():
    """The whole point of async: model calls must not scale with packets."""
    _res, sink, timeline = _run(_cfg("async_window", interval=1000.0))
    forwards = [r for r in sink if r.get("kind") == "forward"]
    requests = _events(timeline, "schedule_update_requested")
    assert len(forwards) >= 4
    assert len(requests) < len(forwards), (len(requests), len(forwards))


def test_query_counts_are_reported_per_scope():
    _res, _sink, timeline = _run(_cfg("async_window"))
    q = _events(timeline, "schedule_query")
    assert q
    for row in q:
        assert row["scope"] is not None and len(row["scope"]) == 3
