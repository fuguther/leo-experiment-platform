"""Direct coverage for D2 dynamic topology rematch semantics.

Complements the indirect routing/holding-queue coverage: retired-link
control draining and Q0 state-version staleness on recompute.
"""
from __future__ import annotations

import numpy as np
import pytest

from CODE.leo_sim import kernel, learning, receipt
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, make_cfg, row


class _DrainGeo(StaticGeometry):
    """Topology rematch decoupled from physical availability.

    The greedy rematch switches 0:E from peer 1 to peer 2 at t=0.5, but the
    physical 0-1 link stays up (as with the real Constellation geometry,
    where availability comes from range, not from the matcher), so the
    retired link can drain its queue naturally.
    """

    def isl_available(self, a, b, t):
        return b in self._nb.get(a, {}).values()


class _LearningRematchGeo(_DrainGeo):
    """Three-satellite rematch with endpoint visibility for one packet."""

    def __init__(self, src_ll, dst_ll, **kwargs):
        super().__init__(**kwargs)
        self._src_ll = src_ll
        self._dst_ll = dst_ll

    def ground_visible(self, sat_id, lat, lon, t):
        if (lat, lon) == self._src_ll:
            return sat_id == 0
        if (lat, lon) == self._dst_ll:
            return (sat_id == 1 and t < 0.5) or (sat_id == 2 and t >= 0.5)
        return False


def _geometry():
    def at(sat, dirs, t):
        if t < 0.5:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        else:
            nb = {0: {"E": 2}, 2: {"W": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    return _DrainGeo(3, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                     neighbors_at_fn=at)


def _cfg():
    return make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1,
                     "duration_s": 1.0},
        "topology": {"recompute_interval_s": 0.5},
    })


def test_retired_isl_drains_queued_control_after_rematch():
    geo = _geometry()
    k = kernel.Kernel(_cfg(), [], geometry=geo)
    old = k.isls[0]["E"]
    cp = kernel.ControlPacket(1, 0, 1, 0.0, 10.0, 1, 8_000, {})
    k.ctrl_ledger.register(cp.iid, cp.bits)
    old.put_ctrl(cp)

    k._recompute_topology(0.5)
    assert old in k._retired_isls
    assert k.isls[0]["E"].peer == 2

    res = k.run()
    assert res["natural_end"]
    # the retired link kept serving its queued control to the OLD peer
    assert not old.ctrl_q
    assert old.ctrl_bits == 0
    assert cp.received_at is not None


def test_recompute_bumps_state_version_and_stales_snapshots():
    geo = _geometry()
    k = kernel.Kernel(_cfg(), [], geometry=geo)
    snap = k.snapshot_global()
    v0 = snap["state_version"]

    k._recompute_topology(0.5)

    assert k._state_version == v0 + 1
    snap2 = k.snapshot_global()
    assert snap2["state_version"] == v0 + 1
    # the rematch is visible in the snapshot content, not just the counter
    assert snap2["topology"] != snap["topology"]


def test_learning_rematch_requeue_discards_open_forward_transition():
    src_ll, dst_ll = (0.0, 0.0), (10.0, 0.0)
    geo = _LearningRematchGeo(
        src_ll, dst_ll, num_satellites=3,
        neighbors_map={0: {"E": 1}, 1: {"W": 0}},
        neighbors_at_fn=lambda s, dirs, t: {
            d: n for d, n in (
                {0: {"E": 1}, 1: {"W": 0}} if t < 0.5
                else {0: {"E": 2}, 2: {"W": 0}}).get(s, {}).items()
            if d in dirs})
    cfg = make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1,
                     "duration_s": 1.0},
        "topology": {"recompute_interval_s": 0.5},
        "endpoints": {"sites": [
            {"name": "src", "lat": src_ll[0], "lon": src_ll[1]},
            {"name": "dst", "lat": dst_ll[0], "lon": dst_ll[1]},
        ]},
        "control_plane": {"enabled": True},
        "routing": {"policy": "hop", "learning_enabled": True},
        "learning": {"algorithm": "qlearning"},
    })
    src, dst = cell(*src_ll), cell(*dst_ll)
    k = kernel.Kernel(cfg, [row(1, 0.0, src, dst)], geometry=geo)
    old = k.isls[0]["E"]
    pkt = kernel.DataPacket(999, src, dst, 8_000, None, 0.0)
    # simulate a packet whose forward action was decided but whose ISL
    # service has not started when the rematch retires that link
    pkt.learning_state = np.zeros(learning.CONTRACT_DIMS["C3"])
    pkt.learning_action = "E"
    pkt.learning_reward = None
    k._learning_open.add(pkt)
    old.put_data(pkt)

    k._recompute_topology(0.5)

    assert k.pending[0] == [pkt]
    assert pkt.learning_state is None
    assert pkt.learning_action is None
    assert pkt not in k._learning_open
    assert k.mech["learning_discarded_at_rematch"] == 1

    # decision/transition accounting must make room for an environment abort
    # before receipt verification can reuse the existing stop-time identity
    mc = dict(k.mech)
    mc["learning_decisions"] = 1  # the one queued decision before rematch
    assert receipt._learning_transition_accounting(mc) == []


def test_dynamic_t0_matching_counts_as_effective_without_recompute():
    def at(sat, dirs, t):
        if t < 0.3:
            nb = {0: {"E": 2}, 2: {"W": 0}}
        else:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    # the static builder defaults to the 0-1 edge; the dynamic t=0 matching
    # already differs, so no interval recompute is needed for effectiveness
    geo = _DrainGeo(3, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                    neighbors_at_fn=at)
    k = kernel.Kernel(_cfg(), [], geometry=geo)
    assert k.mech["topo_dynamic_init"] is True
    assert k.mech["topo_recomputes"] == 0
    res = k.run()
    assert res["natural_end"]
    mc = res["mechanism_counters"]
    assert mc["topo_dynamic_init"] is True
    assert mc["topo_recomputes"] == 1
    assert res["mechanisms"]["effective"]["dynamic_topology"] is True


class _AlwaysAvailGeo(_DrainGeo):
    """Every rematch generation is physically available, so lifecycle tests
    focus purely on the transceiver/drain/reclaim ordering."""

    def isl_available(self, a, b, t):
        return True


def _triple_rematch_geometry():
    """0:E passes 1 -> 2 -> 3 at t=0.5 / t=1.0 (three generations)."""

    def at(sat, dirs, t):
        if t < 0.5:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        elif t < 1.0:
            nb = {0: {"E": 2}, 2: {"W": 0}}
        else:
            nb = {0: {"E": 3}, 3: {"W": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    return _AlwaysAvailGeo(4, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                           neighbors_at_fn=at)


def _step_run_checking_transceiver(k):
    """Step the environment to the horizon, asserting after every step that
    at most one generation holds a (sat, direction) transceiver in service."""
    import math
    while True:
        t_next = k.env.peek()
        if t_next > k.horizon or t_next == math.inf:
            break
        k.env.step()
        active = []
        for s in range(k.num_sats):
            for d, link in k.isls[s].items():
                if link._svc is not None:
                    active.append((s, d))
        for link in k._retired_isls:
            if link._svc is not None:
                active.append((link.sat, link.dir))
        assert len(active) == len(set(active)), \
            f"transceiver overlap on (sat,dir) at t={k.env.now}: {active}"


def test_continuous_rematch_never_overlaps_transceiver():
    """G0 -> G1 -> G2: a second rematch retires an already-gated successor;
    it must still wait for the older generation instead of starting to
    transmit while G0 is in service (one transceiver per (sat, direction))."""
    geo = _triple_rematch_geometry()
    cfg = make_cfg({
        "scenario": {"num_satellites": 4, "num_planes": 1,
                     "duration_s": 8.0},
        "topology": {"recompute_interval_s": 0.5},
        # 8000 bits / 4000 bps = 2s per control service, long enough for the
        # rematches at t=0.5 and t=1.0 to overlap the older generation's
        # in-service transmission.
        "links": {"isl_rate_mbps": 0.004},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    cps = [kernel.ControlPacket(i + 1, 0, 1, 0.0, 10.0, 1, 8_000, {})
           for i in range(3)]
    for cp in cps:
        k.ctrl_ledger.register(cp.iid, cp.bits)
    # G0 is queued now; G1/G2 are injected right after their rematch tick at
    # t=0.5 / t=1.0 (the ticker was created before these injectors, so it
    # recomputes first at the shared instant and isls[0]["E"] is the new gen).
    k.isls[0]["E"].put_ctrl(cps[0])

    def injector(at_t, cp):
        def proc():
            yield k.env.timeout(max(0.0, at_t - k.env.now))
            k.isls[0]["E"].put_ctrl(cp)
        return k.env.process(proc())

    injector(0.5, cps[1])
    injector(1.0, cps[2])

    _step_run_checking_transceiver(k)

    # every generation eventually transmitted on the single physical slot
    assert all(cp.received_at is not None for cp in cps)
    # two rematches retire four generations (0:E plus each reverse 1:W/2:W);
    # every one drained and must be reclaimed
    assert k._isl_dyn_drained == 4
    assert k._retired_isls == []


def test_entity_cap_counts_lazy_endpoint_and_dynamic_isl_together():
    """A rematch that would exceed max_entities must fail closed even when
    the over-cap entity is a lazy endpoint, not an ISL."""
    def at(sat, dirs, t):
        if t < 0.5:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        else:
            nb = {0: {"E": 2}, 2: {"W": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    geo = _DrainGeo(3, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                    neighbors_at_fn=at)
    cfg = make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1,
                     "duration_s": 1.0},
        "topology": {"recompute_interval_s": 0.5},
        # base = 3 sats + 2 directed ISLs = 5; one runtime creation (endpoint
        # or a 2-link replacement) fits, both together must fail closed
        "execution": {"max_entities": 7},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    assert k._entity_base == 5
    k._ensure_endpoint(cell(0.0, 0.0))
    assert k._live_entity_count() == 6
    with pytest.raises(kernel.CapExceeded):
        k._recompute_topology(0.5)


def test_entity_cap_counts_dynamic_isl_before_lazy_endpoint():
    """A lazy endpoint must fail closed when the live count is already at
    max_entities because dynamic ISL additions consumed the budget."""
    def at(sat, dirs, t):
        if t < 0.5:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        else:
            nb = {0: {"E": 1, "N": 3}, 1: {"W": 0}, 3: {"S": 0}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    geo = _DrainGeo(4, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                    neighbors_at_fn=at)
    cfg = make_cfg({
        "scenario": {"num_satellites": 4, "num_planes": 1,
                     "duration_s": 1.0},
        "topology": {"recompute_interval_s": 0.5},
        # base = 4 sats + 2 directed ISLs = 6; two new ISLs fit, an endpoint
        # on top does not
        "execution": {"max_entities": 8},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    assert k._entity_base == 6
    k._recompute_topology(0.5)  # adds 0:N->3 and 3:S->0: live 6 -> 8
    assert k._live_entity_count() == 8
    with pytest.raises(kernel.CapExceeded):
        k._ensure_endpoint(cell(0.0, 0.0))


def test_removed_direction_reclaims_drained_generation_without_successor():
    """A direction removed entirely retires its generation with no successor
    waiting; when it finishes draining it must be reclaimed promptly by the
    ISL server itself (ISLLink._run prompt purge), instead of lingering as
    live-counted until a later topology recompute.

    The drain is made deterministic: the 8,000-bit control service at 0.004
    Mbps lasts 2.0 s, the rematch happens at t=1.5 and the next recompute
    tick is at t=3.0, so the generation drains strictly between two
    recomputes.  We assert immediately after the rematch that the old
    generation is still live/retired, then right after the t=2.0 drain step
    that it was already removed from _retired_isls with _isl_dyn_drained
    incremented and the live entity count back down -- proof the prompt purge
    ran, not the recompute-time purge.
    """
    def at(sat, dirs, t):
        if t < 1.5:
            nb = {0: {"E": 1}, 1: {"W": 0}}
        else:
            nb = {0: {}}
        return {d: n for d, n in nb.get(sat, {}).items() if d in dirs}

    geo = _DrainGeo(3, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                    neighbors_at_fn=at)
    cfg = make_cfg({
        "scenario": {"num_satellites": 3, "num_planes": 1,
                     "duration_s": 4.0},
        "topology": {"recompute_interval_s": 1.5},
        # 8,000 bits / 4,000 bps = 2.0 s of ISL service: the generation is
        # still mid-service at the t=1.5 rematch and completes at t=2.0,
        # strictly between the t=1.5 and t=3.0 recompute ticks.
        "links": {"isl_rate_mbps": 0.004},
        "execution": {"max_entities": 5},
    })
    k = kernel.Kernel(cfg, [], geometry=geo)
    old = k.isls[0]["E"]
    cp = kernel.ControlPacket(1, 0, 1, 0.0, 10.0, 1, 8_000, {})
    k.ctrl_ledger.register(cp.iid, cp.bits)
    old.put_ctrl(cp)  # in service from t=0 until t=2.0

    import math
    prompt_reclaim_seen = False
    while True:
        t_next = k.env.peek()
        if t_next > k.horizon or t_next == math.inf:
            break
        k.env.step()
        now = k.env.now
        if now == 1.5 and k.env.peek() > 1.5:
            # last event at the rematch instant: the old generation is still
            # live/retired and mid-service; only the empty reverse 1:W has
            # been reclaimed by the rematch purge so far (live = 3 sats +
            # the still-draining 0:E = 4)
            assert old in k._retired_isls
            assert not old._is_drained()
            assert k._isl_dyn_drained == 1
            live_at_rematch = k._live_entity_count()
        if now == 2.0 and k.env.peek() > 2.0:
            # t=2.0 fires twice: first the transmit completes, then the ISL
            # server resumes and prompt-purges.  This is the last event at
            # t=2.0, strictly before the next recompute at t=3.0 -- only the
            # ISL server's prompt purge could have reclaimed the drained
            # generation this early.
            assert old not in k._retired_isls
            assert k._isl_dyn_drained == 2
            # live count dropped by exactly the drained 0:E: 3 satellites,
            # no ISLs, no endpoints
            assert k._live_entity_count() == live_at_rematch - 1
            assert k._live_entity_count() == 3
            prompt_reclaim_seen = True

    assert prompt_reclaim_seen
    assert cp.received_at is not None
    assert k._isl_dyn_drained == 2
    assert k._retired_isls == []
