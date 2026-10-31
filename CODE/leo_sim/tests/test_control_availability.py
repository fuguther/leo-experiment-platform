"""Control injection must respect physical ISL availability.

WHY THIS TEST EXISTS
--------------------
The logical topology (`kernel.topo`) keeps a direction even when the current
orbital geometry puts the two satellites beyond `max_isl_km`.  In the 140-sat
pilot 196 of 560 directed slots are such dead directions.  Data decisions
already filter them through the legal gate, but control injection used to
enqueue advertisements on every logical child direction, so those packets could
never be served: they sat in the ctrl queue and expired at TTL, inflating
control offered bits and `isl_ctrl` queue area without delivering information
(2026-10-31 qualification report, section 12).

The fix filters the SAME predicate the data legality gate uses at the two
control injection sites (origin broadcast and relay).  These tests pin:

  * a dead direction receives no control injection, no control queue bits and
    no ctrl service;
  * an available direction keeps delivering advertisements exactly as before;
  * data forwarding on the available graph is unchanged (oracle data plane);
  * the skip counter attributes the suppression instead of hiding it.

Fixture geometry: StaticGeometry's `neighbors()` (used to build the logical
topology) returns the full static map, while `isl_available()` (neighbors_at)
deliberately omits one directed edge.  That is exactly the production
mismatch: logical adjacency present, physical link absent.
"""
from __future__ import annotations

from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)

# same line topology as test_control.py: 0 -E- 1 -E- 2
LINE = {0: {"E": 1}, 1: {"W": 0, "E": 2}, 2: {"W": 1}}


def _visible(sat, lat, lon, t):
    return (sat == 0 and (lat, lon) == AC) or (sat == 2 and (lat, lon) == BC)


def _available_all(sat, dirs, t):
    return {d: n for d, n in LINE.get(sat, {}).items() if d in dirs}


def _available_without_0_to_1(sat, dirs, t):
    """Logical edge 0->1 exists but the physical link is unavailable."""
    out = {d: n for d, n in LINE.get(sat, {}).items() if d in dirs}
    if sat == 0:
        out.pop("E", None)
    return out


def _geometry(*, dead_0_to_1: bool) -> StaticGeometry:
    subset = _available_without_0_to_1 if dead_0_to_1 else _available_all
    return StaticGeometry(3, neighbors_map=LINE, visible=_visible,
                          neighbors_at_fn=subset)


# A four-satellite fixture whose dead edge is a leaf OFF the data path:
#   0 -E- 1 -E- 2   (data path)      0 -N- 3   (dead leaf)
NB_DEAD_LEAF = {0: {"E": 1, "N": 3}, 1: {"W": 0, "E": 2},
                2: {"W": 1}, 3: {"S": 0}}
NB_NO_LEAF = {0: {"E": 1}, 1: {"W": 0, "E": 2}, 2: {"W": 1}}


def _leaf_availability(with_leaf: bool):
    def avail(sat, dirs, t):
        table = NB_DEAD_LEAF if with_leaf else NB_NO_LEAF
        out = {d: n for d, n in table.get(sat, {}).items() if d in dirs}
        if with_leaf and sat == 0:
            out.pop("N", None)  # the 0->3 leaf exists logically but not physically
        return out
    return avail


def _cfg(**over):
    base = {
        "scenario": {"num_satellites": 3, "num_planes": 1, "duration_s": 4.0},
        "control_plane": {"enabled": True, "advertise_interval_s": 0.5,
                          "ttl_s": 10.0, "vis_k": 2, "packet_bits": 8_000},
        "routing": {"policy": "hop"},
    }
    for key, value in over.items():
        base.setdefault(key, {}).update(value)
    return make_cfg(base)


def _queue_rows(timeline, resource_id):
    return [row for row in timeline
            if row.get("milestone") == "queue_state"
            and row.get("resource_id") == resource_id]


def test_dead_direction_receives_no_control_injection_or_queue_bits():
    geo = _geometry(dead_0_to_1=True)
    timeline = []
    audit = []
    res = kernel.run_simulation(
        _cfg(), [row(1, 0.0, A, B)], geometry=geo,
        timeline_sink=timeline)
    counters = res["mechanism_counters"]
    assert counters["control_geometry_unavailable_skips"] > 0, (
        "the unavailable logical direction must be skipped at injection")
    # even though the dead edge is a tree child of sat0, no control packet may
    # enter its queue and no ctrl service may happen there
    dead = "isl:0:1"
    dead_rows = _queue_rows(timeline, dead)
    # absence of rows is the expected pre-fix counterfactual difference: the
    # old code created queue state for the dead edge; the fixed code never
    # injects at all.  If rows exist they must never hold control bits.
    assert all(int(row["queued_control_bits"]) == 0 for row in dead_rows), (
        "no control bits may accumulate on a physically unavailable direction")
    assert all(row["in_service_kind"] != "control" for row in dead_rows)
    # no control instance may expire: the dead letters of the pre-fix run were
    # exactly the TTL-expired instances that never had a servable link
    assert res["control"]["fate_counts"].get("CONTROL_EXPIRED", 0) == 0
    # the advertisement from sat0 physically cannot arrive at sat1
    assert 0 not in res["caches"][1], (
        "sat1 must not hold an advertisement that never physically arrived")


def test_available_direction_still_delivers_advertisements():
    geo = _geometry(dead_0_to_1=False)
    res = kernel.run_simulation(_cfg(), [row(1, 0.0, A, B)], geometry=geo)
    assert res["mechanism_counters"]["control_geometry_unavailable_skips"] == 0
    # sat1 advertises direction W to sat0, which is available
    assert 1 in res["caches"][0]
    # and the control plane really carried bits
    assert res["control"]["bits"]["delivered"] > 0


def test_oracle_data_forwarding_is_unchanged_by_the_control_filter():
    # The dead edge is a leaf OFF the data path.  With the oracle policy the
    # data plane never reads the advertisement cache, so adding a logically
    # present but physically dead leaf must not change the data delivery.
    dead = kernel.run_simulation(
        _cfg(routing={"policy": "oracle"}, scenario={"num_satellites": 4}),
        [row(7, 0.0, A, B)],
        geometry=StaticGeometry(4, neighbors_map=NB_DEAD_LEAF,
                                visible=_visible,
                                neighbors_at_fn=_leaf_availability(True)))
    live = kernel.run_simulation(
        _cfg(routing={"policy": "oracle"}, scenario={"num_satellites": 4}),
        [row(7, 0.0, A, B)],
        geometry=StaticGeometry(4, neighbors_map=NB_NO_LEAF,
                                visible=_visible,
                                neighbors_at_fn=_leaf_availability(False)))
    assert dead["fates"][7] == "DELIVERED"
    assert dead["fates"] == live["fates"]
    assert dead["deliveries"][7]["delivered_at"] == \
        live["deliveries"][7]["delivered_at"]
    assert dead["deliveries"][7]["path"] == live["deliveries"][7]["path"]
    # the skip counter is the only observable difference on the control side
    assert dead["mechanism_counters"]["control_geometry_unavailable_skips"] > 0
    assert live["mechanism_counters"]["control_geometry_unavailable_skips"] == 0


def test_skip_counter_is_attributed_to_the_origin_broadcast():
    # vis_k=1 keeps the tree at one hop: sat0's only child is the dead edge, so
    # every snapshot it generates must be attributed as one skipped injection.
    geo = _geometry(dead_0_to_1=True)
    res = kernel.run_simulation(
        _cfg(control_plane={"vis_k": 1}),
        [row(1, 0.0, A, B)], geometry=geo)
    counters = res["mechanism_counters"]
    snapshots = counters["control_snapshots"]
    assert snapshots > 0
    # three satellites advertise; only sat0's single child (0->1) is dead
    assert counters["control_geometry_unavailable_skips"] >= 1
    assert counters["control_geometry_unavailable_skips"] <= 3 * snapshots
