"""Behavioral tests for routing policies and the deliver action contract."""
import pytest

from CODE.leo_sim import control, kernel, link_budget, model, routing
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)

# divergence topology: 0 connects to 1 (E) and 2 (N); both reach 3
DIVERGE = {0: {"E": 1, "N": 2}, 1: {"W": 0, "E": 3}, 2: {"S": 0, "E": 3},
           3: {"W": 1, "S": 2}}


def _cache_with(entries):
    c = control.LocalCache()
    for origin, payload, gen, recv, ttl in entries:
        payload = dict(payload)
        # legal egress requires advertised *service capability*, not visibility
        if "visible_cells" in payload and "serve_cells" not in payload:
            payload["serve_cells"] = payload.pop("visible_cells")
        c.put(control.CacheEntry(origin, payload, gen, recv, ttl))
    return c


def _topo(geo):
    return routing.build_topology(geo, geo.num_satellites, ("N", "S", "E", "W"))


def test_hop_policy_picks_fewest_hops():
    geo = StaticGeometry(4, neighbors_map=DIVERGE)
    topo = _topo(geo)
    cache = _cache_with([(3, {"visible_cells": [B], "isl_queue_bits": {}}, 0.0, 0.01, 10.0)])
    dirs, status = routing.choose_next_hop(
        "hop", 0, B, 1.0, geo, topo, cache, {}, 1e9, lambda km: km / 299_792.458)
    assert status == "ok"
    assert dirs[0] in ("E", "N")  # both are 2 hops; deterministic tie-break
    # the direct 1-hop case: dst visible at neighbor 1
    cache1 = _cache_with([(1, {"visible_cells": [B], "isl_queue_bits": {}}, 0.0, 0.01, 10.0)])
    dirs1, _ = routing.choose_next_hop(
        "hop", 0, B, 1.0, geo, topo, cache1, {}, 1e9, lambda km: km / 299_792.458)
    assert dirs1[0] == "E"


def test_best_only_returns_only_shortest_ties():
    # routing-library parameter test: 0->1->3 is two hops, 0->2->4->3 is
    # three; best_only=True returns only the shortest-tie directions. The
    # kernel's learning path no longer passes best_only=True (the learner's
    # action set spans every locally legal direction), so this test pins the
    # library function itself, not any learning-policy semantics.
    topo_map = {
        0: {"E": 1, "N": 2}, 1: {"W": 0, "E": 3},
        2: {"S": 0, "E": 4}, 4: {"W": 2, "E": 3},
        3: {"W": 1, "S": 4},
    }
    geo = StaticGeometry(5, neighbors_map=topo_map)
    topo = _topo(geo)
    cache = _cache_with([
        (3, {"visible_cells": [B], "isl_queue_bits": {}}, 0.0, 0.01, 10.0)
    ])
    dirs, status = routing.choose_next_hop(
        "hop", 0, B, 1.0, geo, topo, cache, {}, 1e9,
        lambda km: km / 299_792.458, best_only=True)
    assert status == "ok" and dirs == ["E"]


def test_delay_policy_uses_propagation_not_hops():
    ranges = {(0, 1): 100.0, (1, 2): 100.0, (0, 2): 10_000.0}
    fn = lambda a, b, t: ranges.get((a, b), ranges.get((b, a), 100.0))
    topo2 = {0: {"E": 1, "N": 2}, 1: {"W": 0, "E": 2}, 2: {"S": 0, "W": 1}}
    geo = StaticGeometry(3, neighbors_map=topo2, isl_range_fn=fn)
    topo = _topo(geo)
    cache = _cache_with([
        (1, {"visible_cells": [], "isl_queue_bits": {},
             "isl_propagation_s": {"E": {"peer": 2,
                                         "value": 100.0 / 299_792.458}}},
         0.0, 0.01, 10.0),
        (2, {"visible_cells": [B], "isl_queue_bits": {},
             "isl_propagation_s": {"W": {"peer": 1,
                                         "value": 100.0 / 299_792.458},
                                   "S": {"peer": 0,
                                         "value": 10_000.0 / 299_792.458}}},
         0.0, 0.01, 10.0),
    ])
    d_hop, _ = routing.choose_next_hop(
        "hop", 0, B, 1.0, geo, topo, cache, {}, 1e9, lambda km: km / 299_792.458)
    d_delay, _ = routing.choose_next_hop(
        "delay", 0, B, 1.0, geo, topo, cache, {}, 1e9, lambda km: km / 299_792.458)
    assert d_hop[0] == "N"       # 1 direct hop
    assert d_delay[0] == "E"     # 2 short hops beat 1 long hop


def test_capacity_policy_avoids_advertised_congestion():
    geo = StaticGeometry(4, neighbors_map=DIVERGE)
    topo = _topo(geo)
    cache = _cache_with([
        (3, {"visible_cells": [B], "isl_queue_bits": {},
             "isl_propagation_s": {"W": {"peer": 1, "value": 0.001},
                                   "S": {"peer": 2, "value": 0.001}}},
         0.0, 0.01, 10.0),
        (1, {"visible_cells": [],
             "isl_queue_bits": {"E": {"peer": 3, "value": 900_000_000}},
             "isl_propagation_s": {"E": {"peer": 3, "value": 0.001}}},
         0.0, 0.01, 10.0),
        (2, {"visible_cells": [],
             "isl_queue_bits": {"E": {"peer": 3, "value": 0}},
             "isl_propagation_s": {"E": {"peer": 3, "value": 0.001}}},
         0.0, 0.01, 10.0),
    ])
    dirs, status = routing.choose_next_hop(
        "capacity", 0, B, 1.0, geo, topo, cache, {}, 1e9, lambda km: km / 299_792.458)
    assert status == "ok"
    assert dirs[0] == "N"  # path via 2: sat1's E queue is advertised congested


def test_info_queue_policy_uses_only_local_first_hop_queue():
    """F0 must be a local queue/direction arm, not a remote-cache arm."""
    geo = StaticGeometry(4, neighbors_map=DIVERGE)
    topo = _topo(geo)
    cache = _cache_with([
        (3, {"visible_cells": [B], "isl_queue_bits": {}}, 0.0, 0.01, 10.0),
    ])
    dirs, status = routing.choose_next_hop(
        "info_queue", 0, B, 1.0, geo, topo, cache,
        {"E": 900_000_000, "N": 0}, 1e9,
        lambda km: km / 299_792.458,
    )
    assert status == "ok"
    assert dirs[0] == "N"


def test_info_physical_policy_rejects_zero_rate_first_hop():
    """F1 must use current candidate distance/rate/availability, not a
    fixed-rate tie-break when the direct MCS rate is zero."""
    ranges = {(0, 1): 5900.0, (0, 2): 1000.0,
              (1, 3): 1000.0, (2, 3): 1000.0}
    geo = StaticGeometry(
        4, neighbors_map=DIVERGE,
        isl_range_fn=lambda a, b, _t: ranges.get(
            (a, b), ranges.get((b, a), 1000.0)),
    )
    topo = _topo(geo)
    cache = _cache_with([
        (3, {"visible_cells": [B], "isl_queue_bits": {}}, 0.0, 0.01, 10.0),
    ])
    dirs, status = routing.choose_next_hop(
        "info_physical", 0, B, 1.0, geo, topo, cache, {}, 1e9,
        lambda km: km / 299_792.458,
        rate_from_propagation=lambda prop_s: 0.0 if prop_s > 0.01 else 1e9,
    )
    assert status == "ok"
    assert dirs[0] == "N"


def test_capacity_policy_uses_observed_dynamic_mcs_rate():
    """D1: capacity cost uses the rate implied by the same current/cached
    propagation observation, not the legacy constant service rate.  The E
    edge is beyond the MCS threshold and therefore cannot be preferred merely
    by direction tie-break."""
    ranges = {(0, 1): 5900.0, (0, 2): 1000.0,
              (1, 3): 1000.0, (2, 3): 1000.0}
    geo = StaticGeometry(
        4, neighbors_map=DIVERGE,
        isl_range_fn=lambda a, b, _t: ranges.get(
            (a, b), ranges.get((b, a), 1000.0)))
    topo = _topo(geo)
    prop_1000 = model.propagation_delay_s(1000.0)
    cache = _cache_with([
        (3, {"visible_cells": [B], "isl_queue_bits": {},
             "isl_propagation_s": {"W": {"peer": 1, "value": prop_1000},
                                   "S": {"peer": 2, "value": prop_1000}}},
         0.0, 0.01, 10.0),
        (1, {"visible_cells": [],
             "isl_queue_bits": {"E": {"peer": 3, "value": 0}},
             "isl_propagation_s": {"E": {"peer": 3, "value": prop_1000}}},
         0.0, 0.01, 10.0),
        (2, {"visible_cells": [],
             "isl_queue_bits": {"E": {"peer": 3, "value": 0}},
             "isl_propagation_s": {"E": {"peer": 3, "value": prop_1000}}},
         0.0, 0.01, 10.0),
    ])
    dirs, status = routing.choose_next_hop(
        "capacity", 0, B, 1.0, geo, topo, cache,
        {"E": 1_000_000, "N": 1_000_000}, 1e9,
        model.propagation_delay_s,
        rate_from_propagation=lambda prop: link_budget.mcs_rate_bps(
            prop * model.C_KM_S, link_budget.LEGACY_ISL_RF))
    assert status == "ok"
    assert dirs[0] == "N"


@pytest.mark.parametrize("policy", ["delay", "capacity"])
def test_cropped_cache_metrics_cannot_make_remote_path_reachable(policy):
    """Learning routing must crop path metrics with the observation cache.

    The destination advertisement is inside the one-hop information boundary,
    but both possible intermediate satellites advertise their remote-edge
    metrics at two hops.  Those hidden metrics may make the route usable with
    the full cache, but not with ``cache_hops=1``.
    """
    geo = StaticGeometry(4, neighbors_map=DIVERGE)
    topo = _topo(geo)
    cache = control.LocalCache()
    cache.put(control.CacheEntry(
        3, {"serve_cells": [B], "isl_queue_bits": {},
            "isl_propagation_s": {}},
        0.0, 0.01, 10.0, hops=1))
    for origin, direction in ((1, "E"), (2, "E")):
        cache.put(control.CacheEntry(
            origin,
            {"serve_cells": [],
             "isl_queue_bits": {direction: {"peer": 3, "value": 0}},
             "isl_propagation_s": {direction: {"peer": 3, "value": 0.001}}},
            0.0, 0.01, 10.0, hops=2))

    full_dirs, full_status = routing.choose_next_hop(
        policy, 0, B, 1.0, geo, topo, cache, {}, 1e9,
        lambda km: km / 299_792.458)
    cropped_dirs, cropped_status = routing.choose_next_hop(
        policy, 0, B, 1.0, geo, topo, cache, {}, 1e9,
        lambda km: km / 299_792.458, cache_hops=1)

    assert full_status == "ok" and full_dirs
    assert cropped_status == "unreachable" and cropped_dirs == []


def test_capacity_policy_without_info_is_no_info_or_unreachable():
    geo = StaticGeometry(4, neighbors_map=DIVERGE)
    topo = _topo(geo)
    cache = control.LocalCache()  # nothing arrived
    dirs, status = routing.choose_next_hop(
        "capacity", 0, B, 1.0, geo, topo, cache, {}, 1e9, lambda km: km / 299_792.458)
    assert status == "no_info" and dirs == []


def test_oracle_is_labeled_analysis_upper_bound():
    geo = StaticGeometry(1, visible=lambda s, lat, lon, t: True)
    cfg = make_cfg({"scenario": {"num_satellites": 1, "num_planes": 1}})
    res = kernel.run_simulation(cfg, [row(1, 0.0, A, B)], geometry=geo)
    assert res["routing_label"] == routing.ORACLE_LABEL == "analysis_upper_bound"
    assert res["mechanisms"]["requested"]["policy"] == "oracle"


def test_dynamic_build_topology_uses_time_specific_neighbors():
    def at(sat, dirs, t):
        if t < 1.0:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        else:
            nb = {0: {"E": 2}, 2: {"W": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    geo = StaticGeometry(3, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                         neighbors_at_fn=at)
    assert routing.build_topology(geo, 3, ("E", "W"))[0] == {"E": 1}
    assert routing.build_topology(geo, 3, ("E", "W"), t=1.0)[0] == {"E": 2}


def test_dynamic_topology_recomputes_and_reports_effective_mechanism():
    def at(sat, dirs, t):
        if t < 0.5:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        else:
            nb = {0: {"E": 2}, 2: {"W": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    geo = StaticGeometry(3, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                         neighbors_at_fn=at)
    cfg = make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1,
                      "duration_s": 1.6},
        "topology": {"recompute_interval_s": 0.5},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    res = k.run()
    assert res["natural_end"]
    assert res["mechanism_counters"]["topo_recomputes"] == 3
    assert res["mechanisms"]["requested"]["topology_recompute_interval_s"] == 0.5
    assert res["mechanisms"]["effective"]["dynamic_topology"] is True


def test_dynamic_topology_requeues_data_from_retired_isl_before_rebuild():
    def at(sat, dirs, t):
        if t < 0.5:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        else:
            nb = {0: {"E": 2}, 2: {"W": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    geo = StaticGeometry(3, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                         neighbors_at_fn=at)
    cfg = make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1,
                      "duration_s": 1.0},
        "topology": {"recompute_interval_s": 0.5},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, "src", "dst", 8_000, None, 0.0)
    old_link = k.isls[0]["E"]
    old_link.data_q.append(pkt)
    old_link.data_bits = pkt.bits
    old_link.data_area.add(pkt.bits, 0.0)

    k._recompute_topology(0.5)

    assert k.pending[0] == [pkt]
    assert k.pending[0].queued_bits == pkt.bits
    assert old_link not in k._retired_isls
    assert old_link in k._retired_isls_done
    assert k.isls[0]["E"].peer == 2


def test_integration_hop_vs_delay_paths_differ():
    ranges = {(0, 1): 100.0, (1, 2): 100.0, (0, 2): 10_000.0}
    fn = lambda a, b, t: ranges.get((a, b), ranges.get((b, a), 100.0))
    topo2 = {0: {"E": 1, "N": 2}, 1: {"W": 0, "E": 2}, 2: {"S": 0, "W": 1}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 2 and (lat, lon) == BC)

    def run(policy):
        cfg = make_cfg({
            "scenario": {"num_satellites": 3, "num_planes": 1, "duration_s": 5.0},
            "control_plane": {"enabled": True, "advertise_interval_s": 0.5,
                              "vis_k": 2, "ttl_s": 10.0},
            "routing": {"policy": policy},
        })
        geo = StaticGeometry(3, neighbors_map=topo2, visible=vis, isl_range_fn=fn)
        return kernel.run_simulation(cfg, [row(1, 1.0, A, B)], geometry=geo)

    r_hop = run("hop")
    r_delay = run("delay")
    assert r_hop["fates"][1] == "DELIVERED"
    assert r_delay["fates"][1] == "DELIVERED"
    assert r_hop["deliveries"][1]["path"] == [0, 2]
    assert r_delay["deliveries"][1]["path"] == [0, 1, 2]
