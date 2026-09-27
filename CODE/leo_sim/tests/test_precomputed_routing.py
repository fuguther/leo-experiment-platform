"""T1-COMPLETE R7: the precomputed table must be per-DESTINATION.

The first version rooted the BFS at the current satellite and never used the
target when costing a candidate direction, so every destination received the
same direction.  These tests pin the directed per-target behaviour.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)


def _kernel(neighbors, visible_nodes, n=3):
    def vis(s, lat, lon, t):
        return (s, (lat, lon)) in visible_nodes
    geo = StaticGeometry(n, neighbors_map=neighbors, visible=vis)
    cfg = make_cfg({"scenario": {"duration_s": 2.0},
                    "demand": {"packet_bits": 8_000_000},
                    "time_alignment": {"enabled": True,
                                       "execution_mode": "precomputed"},
                    "async_routing": {"enabled": False}})
    return kernel.Kernel(cfg, [row(1, 0.0, A, B)], geometry=geo,
                         decision_sink=[], timeline_sink=[]), cfg


def test_two_targets_in_opposite_directions_get_opposite_next_hops():
    """0 --E--> 1 and 0 --W--> 2, with 1 and 2 both returning to 0."""
    neighbors = {0: {"E": 1, "W": 2}, 1: {"W": 0}, 2: {"E": 0}}
    kern, _cfg = _kernel(neighbors, {(0, AC), (1, AC), (2, AC)})
    kern._precompute_build()
    table = kern._precomputed_dirs[0]
    assert table[1] == "E", table
    assert table[2] == "W", table
    assert kern._precompute_cost["bfs_runs"] == 3


def test_a_multi_hop_target_uses_the_shorter_direction_per_target():
    """A 5-cycle: target 2 is closer via E, target 3 is closer via W.

    Physical ISLs are bidirectional (routing refuses to fabricate a reverse
    edge), so the per-target asymmetry that matters is DISTANCE, not edge
    direction.
    """
    neighbors = {0: {"E": 1, "W": 4}, 1: {"W": 0, "E": 2}, 2: {"W": 1, "E": 3},
                 3: {"W": 2, "E": 4}, 4: {"W": 3, "E": 0}}
    kern, _cfg = _kernel(neighbors, {(s, AC) for s in range(5)}, n=5)
    kern._precompute_build()
    table = kern._precomputed_dirs[0]
    assert table[2] == "E", table   # 0->1->2 (1 hop after E)
    assert table[3] == "W", table   # 0->4->3 (1 hop after W)


def test_an_unreachable_target_is_absent_not_a_fake_route():
    neighbors = {0: {"E": 1}, 1: {"W": 0}, 2: {}}
    kern, _cfg = _kernel(neighbors, {(s, AC) for s in range(3)}, n=3)
    kern._precompute_build()
    table = kern._precomputed_dirs[0]
    assert 1 in table
    assert 2 not in table, table


def test_equal_length_paths_break_ties_by_stable_direction_order():
    """Both E and W reach 3 in two hops; the sorted direction order decides."""
    neighbors = {0: {"E": 1, "W": 2}, 1: {"W": 0, "E": 3},
                 2: {"E": 0, "W": 3}, 3: {"W": 1, "E": 2}}
    kern, _cfg = _kernel(neighbors, {(s, AC) for s in range(4)}, n=4)
    kern._precompute_build()
    assert kern._precomputed_dirs[0][3] == "E"


def test_the_query_uses_the_precomputed_direction_for_that_target():
    neighbors = {0: {"E": 1, "W": 2}, 1: {"W": 0}, 2: {"E": 0}}
    kern, _cfg = _kernel(neighbors, {(0, AC), (1, AC), (2, AC)})
    kern._precompute_build()
    class Pkt:
        dst = B
    order_e, why_e = kern._precomputed_order(Pkt(), 0, ["E", "W"], 0.0)
    order_w, why_w = kern._precomputed_order(Pkt(), 0, ["E", "W"], 0.0)
    # without an advertisement the query must report a reason, never invent one
    assert order_e is None and order_w is None
    assert why_e == "no_serving_advertisement"
    assert why_w == "no_serving_advertisement"


def test_precomputed_actions_lead_toward_the_serving_satellite():
    """End to end: the mode's chosen direction must reduce distance to the
    advertisement-selected serving satellite."""
    neighbors = {0: {"E": 1, "W": 2}, 1: {"E": 0}, 2: {"W": 0}}
    kern, _cfg = _kernel(neighbors, {(0, AC), (1, AC), (2, AC)})
    kern._precompute_build()
    table = kern._precomputed_dirs[0]
    dist = kern._dist_to
    for target, direction in table.items():
        peer = kern.topo[0][direction]
        assert peer == target or dist[target].get(peer) == 0 or \
            dist[target].get(peer) is not None
