"""Hand-computable minimal scenarios with exact analytic assertions.

Every expected value below is derived in the test docstring from the config
parameters alone (rates, queue caps, geometry constants), so a deviation is
by definition a platform behavior change, not a test miscalibration.
Derivations use:
  service_s = bits / rate_bps
  prop_s    = distance_km / C_KM_S   (C_KM_S = 299_792.458, model.py:13)
StaticGeometry defaults: slant_km=600 (GSL), isl_km=1000 (ISL)
(helpers.py:90-91).
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import kernel, model
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

# Independent physical constant: analytic anchors must not inherit their
# "truth" from the production module under test (else a wrong production
# C_KM_S would drift the expectations with it and tests stay green).
LIGHT_SPEED_KM_S = 299_792.458
PROP_GSL = 600.0 / LIGHT_SPEED_KM_S    # one GSL hop, StaticGeometry slant_km
PROP_ISL = 1000.0 / LIGHT_SPEED_KM_S   # one ISL hop, StaticGeometry isl_km

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)
BITS = 8_000_000  # default packet size from helpers.row


def _two_sat_geo():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    return StaticGeometry(2, neighbors_map=nb, visible=vis)


def test_single_sat_direct_delivery_exact_latency():
    """单星直连：delivered_at = 上行服务 + 上行传播 + 下行服务 + 下行传播。

    Derivation (config: uplink/downlink 100 Mbps, packet 8 Mbit):
      ul service  = 8e6 / 100e6          = 0.08 s   (idle at t=0, grant at 0)
      ul prop     = 600 / C_KM_S                  (slant_km default)
      dl service  = 0.08 s                        (downlink idle on arrival)
      dl prop     = 600 / C_KM_S
      delivered_at = 2 * (0.08 + PROP_GSL) = 0.1640027... s
    No queueing anywhere (single packet), no association delay
    (acquisition_delay_s=0 in make_cfg).
    """
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: s == 0)
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["fates"][1] == "DELIVERED"
    expect = 2 * (BITS / 100e6 + PROP_GSL)
    assert res["deliveries"][1]["delivered_at"] == pytest.approx(
        expect, abs=1e-9)
    assert res["deliveries"][1]["path"] == [0]


def test_two_hop_forwarding_exact_total_latency():
    """两跳转发：总时延 = 上行(服务+传播) + ISL(服务+传播) + 下行(服务+传播)。

    Derivation (uplink/downlink 100 Mbps, ISL 1000 Mbps, packet 8 Mbit):
      ul  = 8e6/100e6 + PROP_GSL  = 0.08 + PROP_GSL
      isl = 8e6/1e9  + PROP_ISL   = 0.008 + PROP_ISL   (idle: no queue wait)
      dl  = 8e6/100e6 + PROP_GSL  = 0.08 + PROP_GSL
      delivered_at = 0.168 + 2*PROP_GSL + PROP_ISL
    """
    res = kernel.run_simulation(make_cfg(), [row(1, 0.0, A, B)],
                                geometry=_two_sat_geo())
    assert res["fates"][1] == "DELIVERED"
    assert res["deliveries"][1]["path"] == [0, 1]
    expect = (BITS / 100e6 + PROP_GSL) + (BITS / 1000e6 + PROP_ISL) \
        + (BITS / 100e6 + PROP_GSL)
    assert res["deliveries"][1]["delivered_at"] == pytest.approx(
        expect, abs=1e-9)


def test_access_slots_full_waiting_counts():
    """接入槽满：slots=1 被长服务占死时，第二端点的等待/在系统计数可解析推出。

    Derivation (slots_per_satellite=1, uplink 1 Mbps, horizon 5 s):
      - ep A emits 8 Mbit at t=0 -> lazy endpoint activation makes the first
        emission demand-driven: A is granted the only slot at emission
        (kernel.py _request_or_grant/_try_grant, grants=1, no preposition),
        uplink service
        = 8e6/1e6 = 8 s > 5 s horizon -> still in service at stop
        => A's packet is IN_SYSTEM_AT_STOP (no fate before stop).
      - ep B emits 8 Mbit at t=0 -> satellite visible but no free slot
        => joins the FIFO wait queue (kernel.py:1031-1043) and is NEVER
        granted: A's link never goes idle (8 s service) and lease rotation
        (slot_lease_s=10 s) fires past the 5 s horizon.
      => access: requests=1 (B's demand request), grants=1 (A at t=0),
         preposition_grants=0 (no pre-arming without pre-built endpoints),
         waiting_at_stop=1 (B);
      => fates: both packets IN_SYSTEM_AT_STOP (in_system count = 2),
         offered = 2*8 Mbit = in_system bits; conservation holds.
    """
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 5.0},
        "access": {"slots_per_satellite": 1, "uplink_rate_mbps": 1.0},
    })
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: s == 0)
    rows = [row(1, 0.0, A, B), row(2, 0.0, B, A)]
    res = kernel.run_simulation(cfg, rows, geometry=geo)
    assert res["natural_end"] is True
    assert res["access"]["requests"] == 1
    assert res["access"]["grants"] == 1
    assert res["access"]["preposition_grants"] == 0
    assert res["access"]["waiting_at_stop"] == 1
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    assert res["fates"][2] == "IN_SYSTEM_AT_STOP"
    assert res["totals"]["offered_bits"] == 2 * BITS
    assert res["totals"]["in_system_bits_at_stop"] == 2 * BITS
    assert res["totals"]["delivered_bits"] == 0


def test_horizon_settles_in_flight_packet_with_exact_occupied_time():
    """horizon 结算：在途包计 IN_SYSTEM_AT_STOP，链路占用时间精确到账。

    Derivation (uplink 100 Mbps, ISL 1 Mbps, horizon 1.0 s):
      - ul service ends at 0.08; ISL service starts at t_start =
        0.08 + PROP_GSL (link idle, available).
      - ISL service needs 8e6/1e6 = 8 s >> horizon -> the packet is in
        service at the stop => IN_SYSTEM_AT_STOP (in_system count = 1).
      - occupied["isl_s"] settles as horizon - t_start
        (kernel.py:1560-1564 in-service accounting at stop).
      - occupied["gsl_uplink_s"] == 0.08 exactly (the one full uplink
        service).
    """
    cfg = make_cfg({
        "scenario": {"duration_s": 1.0},
        "links": {"isl_rate_mbps": 1.0},
    })
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)],
                                geometry=_two_sat_geo())
    assert res["natural_end"] is True
    assert res["stop_time_s"] == pytest.approx(1.0, abs=1e-12)
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    assert res["fate_counts"]["IN_SYSTEM_AT_STOP"] == 1
    t_start = BITS / 100e6 + PROP_GSL
    assert res["occupied"]["isl_s"] == pytest.approx(1.0 - t_start, abs=1e-9)
    assert res["occupied"]["gsl_uplink_s"] == pytest.approx(
        BITS / 100e6, abs=1e-12)


def test_production_speed_of_light_matches_independent_literal():
    from CODE.leo_sim import model
    assert model.C_KM_S == LIGHT_SPEED_KM_S, (
        "production model.C_KM_S drifted from the independent analytic "
        "literal; do not edit the literal, fix the production constant")
