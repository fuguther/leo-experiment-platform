"""T1 time-ledger tests: per-decision identity, lifecycle milestones, folding.

Covers the instrumentation added for T1-TIME-LEDGER-PASS.  The hard
requirement is that with both sinks absent (the default) nothing is allocated
and no behaviour changes; the second requirement is that with a timeline sink
attached the simulation results are still byte-identical to the default run.
"""
from __future__ import annotations

from CODE.leo_sim import decision_ledger, kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)


def _two_sat_geo():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 1 and (lat, lon) == BC)
    return StaticGeometry(2, neighbors_map=nb, visible=vis)


def _cfg(overrides=None):
    return make_cfg(overrides)


def _run(rows, decision_sink=None, timeline_sink=None, overrides=None):
    return kernel.run_simulation(_cfg(overrides), rows,
                                 geometry=_two_sat_geo(),
                                 decision_sink=decision_sink,
                                 timeline_sink=timeline_sink)


# --------------------------------------------------------------- identity

def test_decision_sink_rows_gain_a_unique_decision_id():
    sink = []
    _run([row(1, 0.0, A, B)], decision_sink=sink)
    ids = [r["decision_id"] for r in sink]
    assert len(sink) == 2, "one decision per hop, milestones must not land here"
    assert all(i is not None for i in ids)
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_redecision_links_the_two_attempts_of_one_packet():
    sink, timeline = [], []
    _run([row(1, 0.0, A, B)], decision_sink=sink, timeline_sink=timeline)
    first, second = sink
    # same packet, two attempts: the old (t, pid, sat, kind) key could not
    # express that these belong to one forwarding chain
    assert first["pid"] == second["pid"] == 1
    assert first["decision_id"] != second["decision_id"]
    relink = [m for m in timeline if m["milestone"] == "redecision"]
    assert len(relink) == 1
    assert relink[0]["prev_decision_id"] == first["decision_id"]
    assert relink[0]["decision_id"] == second["decision_id"]


def test_default_off_allocates_nothing():
    k = kernel.Kernel(make_cfg(), [row(1, 0.0, A, B)],
                      geometry=_two_sat_geo())
    assert k.decision_sink is None
    assert k.timeline_sink is None
    assert k._decision_seq == 0
    assert k._next_decision_id() is None
    assert k._decision_seq == 0, "no id may be consumed when nothing records"


# --------------------------------------------------------------- behaviour

def test_timeline_sink_does_not_change_behavior():
    rows = [row(i, 0.0, A, B) for i in (1, 2, 3)]
    base = _run(rows)
    timeline = []
    with_timeline = _run(rows, timeline_sink=timeline)
    for key in ("fates", "fate_counts", "totals", "deliveries", "occupied",
                "queue_area_bits_s", "access", "service_log", "handover",
                "events_processed"):
        assert with_timeline[key] == base[key], key
    assert timeline, "timeline sink must actually record something"


def test_both_sinks_together_do_not_change_behavior():
    rows = [row(i, 0.0, A, B) for i in (1, 2, 3)]
    base = _run(rows)
    sink, timeline = [], []
    both = _run(rows, decision_sink=sink, timeline_sink=timeline)
    for key in ("fates", "fate_counts", "totals", "deliveries", "occupied",
                "queue_area_bits_s", "access", "service_log", "handover",
                "events_processed"):
        assert both[key] == base[key], key
    assert len(sink) == 6  # 3 packets x 2 decisions


# --------------------------------------------------------------- milestones

def test_timeline_records_the_decision_lifecycle_in_order():
    timeline = []
    _run([row(1, 0.0, A, B)], timeline_sink=timeline)
    names = [m["milestone"] for m in timeline]
    for expected in ("queue_enter", "service_start", "service_finish",
                     "peer_arrival", "redecision"):
        assert expected in names, expected
    # milestones attributed to a decision must follow that decision's commit
    for m in timeline:
        if m["decision_id"] is not None:
            assert isinstance(m["at"], float)
        assert m["pid"] == 1


def test_timeline_rows_stay_out_of_the_decision_sink():
    sink, timeline = [], []
    _run([row(1, 0.0, A, B)], decision_sink=sink, timeline_sink=timeline)
    assert {r["kind"] for r in sink} == {"forward", "deliver"}
    assert all("milestone" not in r for r in sink)
    assert all("kind" not in m for m in timeline)


# --------------------------------------------------------------- folding

def test_ledger_rebuilds_the_full_eleven_field_chain():
    sink, timeline = [], []
    _run([row(1, 0.0, A, B)], decision_sink=sink, timeline_sink=timeline)
    by_decision, diag = decision_ledger.build_ledger(sink, timeline)
    assert diag["duplicate_decision_ids"] == []
    assert diag["orphan_redecisions"] == []
    assert diag["decisions"] == 2
    for entry in by_decision.values():
        for field in decision_ledger.TIMELINE_FIELDS:
            assert field in entry, field
    first = by_decision[sink[0]["decision_id"]]
    second = by_decision[sink[1]["decision_id"]]
    # the three-way split must survive even while the values are equal
    assert first["t_measure"] == first["t_decision_start"]
    assert first["t_decision_start"] == first["t_decision_commit"]
    # forward decision: enqueued, served, finished, then arrived at the peer
    assert first["t_local_queue_enter"] != decision_ledger.MISSING
    assert first["t_service_start"] != decision_ledger.MISSING
    assert first["t_service_finish"] != decision_ledger.MISSING
    assert first["t_peer_arrival"] != decision_ledger.MISSING
    # peer-side target egress is the successor's own local enqueue/service
    assert first["t_peer_redecision"] == second["t_measure"]
    assert first["t_peer_target_egress_enter"] == \
        second["t_local_queue_enter"]
    assert first["t_peer_target_egress_service_start"] == \
        second["t_service_start"]
    # ordering that the whole T1 question rests on
    assert first["t_measure"] <= first["t_local_queue_enter"]
    assert first["t_local_queue_enter"] <= first["t_service_start"]
    assert first["t_service_start"] <= first["t_service_finish"]
    assert first["t_service_finish"] <= first["t_peer_arrival"]
    assert first["t_peer_arrival"] <= first["t_peer_redecision"]


def test_ledger_marks_missing_instead_of_filling_zero():
    sink, timeline = [], []
    _run([row(1, 0.0, A, B)], decision_sink=sink, timeline_sink=timeline)
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    last_id = sink[-1]["decision_id"]
    last = by_decision[last_id]
    # the deliver decision has no successor, so the peer-side fields are
    # genuinely absent and must not read as 0.0
    assert last["t_peer_redecision"] == decision_ledger.MISSING
    assert last["t_peer_target_egress_enter"] == decision_ledger.MISSING
    assert last["t_peer_target_egress_service_start"] == \
        decision_ledger.MISSING
    gaps = decision_ledger.audit_ledger(by_decision)
    # audit_ledger reports the NAMES of the fields still missing
    assert "t_peer_redecision" in gaps[last_id]
    assert "t_peer_target_egress_enter" in gaps[last_id]
    # a non-learning run contributes no control-cache entry, so the decision
    # provably knew nothing from cache; that must read as MISSING, not 0.0
    assert "t_control_rx" in gaps[last_id]
    assert last["t_control_rx"] == decision_ledger.MISSING

# ---------------------------------------------- R8-A6: control arrival time

def test_control_arrival_time_is_recorded_without_a_learner():
    """R8-A6: the T1 first version is a deterministic router with learning
    OFF, so the audit must still record what the node knew.  Before the fix
    cache_entries was populated only when a learner existed, which left
    t_control_rx permanently MISSING for exactly that configuration."""
    sink, timeline = [], []
    _run([row(1, 0.0, A, B)], decision_sink=sink, timeline_sink=timeline,
         overrides={"control_plane": {"enabled": True},
                    "routing": {"policy": "hop"}})
    assert sink[0]["info_audit"]["cache_entries"], \
        "CP on + learner off must still audit the cache"
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    assert any(e["t_control_rx"] != decision_ledger.MISSING
               for e in by_decision.values())


def test_control_arrival_time_stays_missing_with_the_control_plane_off():
    """Companion negative control: with no control plane nothing can arrive,
    so the field must read MISSING rather than 0.0."""
    sink, timeline = [], []
    _run([row(1, 0.0, A, B)], decision_sink=sink, timeline_sink=timeline)
    assert sink[0]["info_audit"]["cache_entries"] == {}
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    assert all(e["t_control_rx"] == decision_ledger.MISSING
               for e in by_decision.values())


# ------------------------------- R8-A7: holds/fails must not leak identifiers

def _kernel_with_sinks(overrides, rows):
    sink, timeline = [], []
    kern = kernel.Kernel(_cfg(overrides), rows, geometry=_two_sat_geo(),
                         decision_sink=sink, timeline_sink=timeline)
    return kern, sink, timeline


def _unaccounted(kern, sink, timeline):
    """Ids that were allocated but left no record anywhere."""
    row_ids = {r["decision_id"] for r in sink}
    terminal = {m["decision_id"] for m in timeline
                if m["milestone"] in ("hold", "fail")
                and m["decision_id"] is not None}
    return sorted(set(range(kern._decision_seq)) - (row_ids | terminal))


def test_holds_do_not_leak_decision_ids():
    """R8-A7: _decide allocates an id at entry but only the two commit sites
    wrote a decision row, so every hold/fail consumed an id and vanished.
    Forcing the no_info hold path (deterministic router, control plane on,
    packet emitted before any advertisement arrives) used to leak 12 ids."""
    kern, sink, timeline = _kernel_with_sinks(
        {"control_plane": {"enabled": True}, "routing": {"policy": "hop"}},
        [row(1, 0.0, A, B)])
    kern.run()
    holds = [m for m in timeline if m["milestone"] == "hold"]
    assert holds, "this fixture must actually produce holds"
    assert len(holds) > len(sink), "holds outnumber commits here"
    assert _unaccounted(kern, sink, timeline) == []


def test_every_allocated_id_is_accounted_for_in_a_plain_run():
    kern, sink, timeline = _kernel_with_sinks(
        {}, [row(i, 0.5 * i, A, B) for i in (1, 2, 3)])
    kern.run()
    assert _unaccounted(kern, sink, timeline) == []
    assert kern._decision_seq >= len(sink)


def test_hold_milestones_do_not_enter_the_decision_sink():
    """Holds go on the timeline only: hold rows in the decision sink would
    break the per-hop decision contract its tests pin."""
    kern, sink, timeline = _kernel_with_sinks(
        {"control_plane": {"enabled": True}, "routing": {"policy": "hop"}},
        [row(1, 0.0, A, B)])
    kern.run()
    assert {r["kind"] for r in sink} <= {"forward", "deliver"}
    assert all("milestone" not in r for r in sink)
    assert all(m["pid"] == 1 for m in timeline)


def test_default_off_allocates_nothing_even_when_holds_would_occur():
    """The zero-overhead guarantee must hold on a path that would otherwise
    hold: with no sinks attached, no id may be consumed at all."""
    kern = kernel.Kernel(_cfg({"control_plane": {"enabled": True},
                               "routing": {"policy": "hop"}}),
                         [row(1, 0.0, A, B)], geometry=_two_sat_geo())
    kern.run()
    assert kern._decision_seq == 0


# ------------------------------- R8-A8: an enqueue belongs to its own causer

def test_holding_enqueue_is_not_credited_to_the_previous_decision():
    """R8-A8: _metric_queue_enter attributed by pkt.decision_id, so a hold
    that followed a commit credited its holding enqueue to the PREVIOUS
    committed decision.  Drive the two causally distinct enqueues directly."""
    kern, sink, timeline = _kernel_with_sinks({}, [row(1, 0.0, A, B)])
    pkt = kernel.DataPacket(1, A, B, 8_000_000, None, 0.0)
    pkt.decision_id = 42                      # a previous COMMITTED decision
    assert kern._hold_packet(0, pkt, decision_id=99)
    enq = [m for m in timeline if m["milestone"] == "queue_enter"]
    hold = [m for m in timeline if m["milestone"] == "hold"]
    assert len(enq) == 1 and len(hold) == 1
    assert enq[0]["decision_id"] == 99, \
        "the holding enqueue must not inherit the previous decision id"
    assert hold[0]["decision_id"] == 99
    assert enq[0]["queue"] == "holding"


def test_commit_caused_enqueue_carries_the_committed_decision():
    """The complement: the ISL enqueue a forward commit performs must be
    credited to that commit, or t_local_queue_enter could never resolve."""
    sink, timeline = [], []
    _run([row(1, 0.0, A, B)], decision_sink=sink, timeline_sink=timeline)
    forward = [r for r in sink if r["kind"] == "forward"]
    assert forward, "fixture must forward at least once"
    did = forward[0]["decision_id"]
    credited = [m for m in timeline
                if m["milestone"] == "queue_enter" and m["decision_id"] == did]
    assert credited, "the committed decision must own its own enqueue"
    assert credited[0]["queue"] == "isl"

