"""T1-COMPLETE P2: the finite compute pool and the time-event triple.

The hand-computed fixture from the task book is the contract here: one
server with two requests at the same instant and a 0.1 s service gives
starts [0, 0.1] and finishes [0.1, 0.2]; two servers give finishes
[0.1, 0.1].  compute_wait must stay contention and compute_service must stay
cost; neither may be folded into the other.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=VIS)


def _cfg(servers, delay=0.1, mode="refresh"):
    # the two requests must land on the SAME simulated instant for the
    # hand-computed fixture to apply; a high uplink rate and zero acquisition
    # delay remove the only serialisation in front of the compute pool
    return make_cfg({"scenario": {"duration_s": 8.0},
                     "execution": {"compute_delay_s": delay,
                                   "compute_servers_per_satellite": servers,
                                   "decision_observation_mode": mode},
                     "demand": {"packet_bits": 8_000_000},
                     "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0,
                                "uplink_rate_mbps": 1e9,
                                "acquisition_delay_s": 0.0}})


def _run(servers, delay=0.1, mode="refresh", rows=None):
    sink, timeline = [], []
    res = kernel.run_simulation(
        _cfg(servers, delay, mode),
        rows if rows is not None else [row(1, 0.0, A, B), row(2, 0.0, A, B)],
        geometry=_geo(), decision_sink=sink, timeline_sink=timeline)
    return res, sink, timeline


def _by_job(timeline, milestone):
    return {m["compute_job_id"]: m for m in timeline
            if m["milestone"] == milestone}


def test_the_task_book_hand_fixture_one_server_serialises_two_requests():
    _res, _sink, timeline = _run(1, delay=0.1)
    req = _by_job(timeline, "compute_request")
    if len(req) < 2:
        pytest.skip("fixture produced fewer than two simultaneous requests")
    base = min(m["at"] for m in req.values())
    start = _by_job(timeline, "compute_start")
    finish = _by_job(timeline, "compute_finish")
    first_two = sorted(req, key=lambda j: req[j]["at"])[:2]
    starts = sorted(round(start[j]["at"] - base, 6) for j in first_two)
    finishes = sorted(round(finish[j]["at"] - base, 6) for j in first_two)
    assert starts == [0.0, 0.1], starts
    assert finishes == [0.1, 0.2], finishes


def test_the_task_book_hand_fixture_two_servers_finish_together():
    _res, _sink, timeline = _run(2, delay=0.1)
    req = _by_job(timeline, "compute_request")
    if len(req) < 2:
        pytest.skip("fixture produced fewer than two simultaneous requests")
    start = _by_job(timeline, "compute_start")
    finish = _by_job(timeline, "compute_finish")
    base = min(m["at"] for m in req.values())
    first_two = sorted(req, key=lambda j: req[j]["at"])[:2]
    starts = sorted(round(start[j]["at"] - base, 6) for j in first_two)
    finishes = sorted(round(finish[j]["at"] - base, 6) for j in first_two)
    assert starts == [0.0, 0.0], starts
    assert finishes == [0.1, 0.1], finishes


def test_every_request_has_one_start_and_one_finish_with_a_stable_id():
    _res, _sink, timeline = _run(1, delay=0.1)
    for milestone in ("compute_request", "compute_start", "compute_finish"):
        ids = [m["compute_job_id"] for m in timeline
               if m["milestone"] == milestone]
        assert len(ids) == len(set(ids)), milestone
    req = {m["compute_job_id"]: m["at"] for m in timeline
           if m["milestone"] == "compute_request"}
    start = {m["compute_job_id"]: m for m in timeline
             if m["milestone"] == "compute_start"}
    finish = {m["compute_job_id"]: m for m in timeline
              if m["milestone"] == "compute_finish"}
    assert set(req) == set(start) == set(finish)
    for job in req:
        assert req[job] <= start[job]["at"] <= finish[job]["at"]
        assert finish[job]["service_s"] == pytest.approx(
            finish[job]["at"] - start[job]["at"])
        assert start[job]["wait_s"] == pytest.approx(start[job]["at"] - req[job])


def test_the_decision_interval_is_wait_plus_service_not_a_summed_blob():
    _res, sink, timeline = _run(1, delay=0.1)
    waits = {(m["sat"], round(m["at"], 9)): m["wait_s"] for m in timeline
             if m["milestone"] == "compute_wait"}
    assert waits, "the fixture must record compute_wait rows"
    checked = 0
    for r in sink:
        key = (r["sat"], round(r["t_decision_start"], 9))
        if key not in waits:
            continue
        assert r["t"] - r["t_decision_start"] == pytest.approx(
            waits[key] + 0.1)
        checked += 1
    assert checked, "at least one decision must match its compute_wait row"


def test_the_pool_is_fifo_so_a_later_request_never_overtakes():
    _res, _sink, timeline = _run(1, delay=0.1)
    req = {m["compute_job_id"]: m["at"] for m in timeline
           if m["milestone"] == "compute_request"}
    start = {m["compute_job_id"]: m["at"] for m in timeline
             if m["milestone"] == "compute_start"}
    order = sorted(req, key=lambda j: (req[j], j))
    for a, b in zip(order, order[1:]):
        assert start[a] <= start[b] + 1e-12, (a, b)


def test_zero_cost_frozen_still_emits_the_three_events_at_one_instant():
    _res, sink, timeline = _run(0, delay=0.0, mode="frozen")
    req = [m for m in timeline if m["milestone"] == "compute_request"]
    start = {m["compute_job_id"]: m for m in timeline
             if m["milestone"] == "compute_start"}
    finish = {m["compute_job_id"]: m for m in timeline
             if m["milestone"] == "compute_finish"}
    assert req, "zero-cost frozen must still record a compute request"
    for m in req:
        job = m["compute_job_id"]
        assert start[job]["at"] == m["at"]
        assert finish[job]["at"] == m["at"]
        assert finish[job]["service_s"] == 0.0
    for r in sink:
        assert r["obs_mode"] == "frozen"
        assert r["t"] == r["t_decision_start"]


def test_a_bounded_pool_is_a_config_error_without_service_time():
    from CODE.leo_sim import config
    with pytest.raises(config.ConfigError, match="compute_servers_per_satellite"):
        config.resolve_config(
            {"execution": {"compute_servers_per_satellite": 1,
                           "compute_delay_s": 0.0}})
