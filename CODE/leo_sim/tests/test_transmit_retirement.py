"""Regression tests for the _transmit down-wait neighbourhood.

K1/K2/K3 from round-3 hunting (Kimi reproduced dynamically):
- K1: a down-wait must race the link retirement interrupt, otherwise a
  retiring link stays pinned past its hard deadline until the outage
  recovers, blocking the whole server.
- K2: the stop-time settle must not book pre-service down-wait as occupied
  (the caller's _svc timestamp must be restamped when transmission actually
  starts).
- K3: a packet waiting on a down link must never be failed with the expiry
  fate BEFORE its deadline (retirement may free it for re-association);
  the expiry fate may only be assigned once the deadline is reached.
"""
import pytest

from CODE.leo_sim import kernel, outage, rng as rngmod
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A, B = cell(0.0, 0.0), cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)


def _two_sat_geo():
    def elev(s, lat, lon, t):
        if (lat, lon) == AC:
            return 90.0 if s == 0 else 80.0
        return 90.0 if s == 0 else -10.0  # B visible only to sat0

    def vis(s, lat, lon, t):
        return elev(s, lat, lon, t) >= 25.0

    return StaticGeometry(2, neighbors_map={0: {"E": 1}, 1: {"W": 0}},
                          visible=vis, elevation=elev, isl_changes=[])


def _contention_cfg(seed=19, monitor=False):
    return make_cfg({
        "scenario": {"num_satellites": 2, "num_planes": 1, "duration_s": 10.0,
                     "time_step_s": 0.1, "seed": seed},
        "access": {"slots_per_satellite": 1, "slot_lease_s": 1.0,
                   "retirement_deadline_s": 0.5, "idle_release_s": 999.0,
                   "min_dwell_s": 0.0, "hysteresis_deg": 0.0,
                   "acquisition_delay_s": 0.0},
        "links": {"ge_enabled": True,
                  "ge_gsl": {"mean_good_s": 1.0, "mean_bad_s": 2.0},
                  "ge_isl": {"mean_good_s": 1.0, "mean_bad_s": 2.0}},
        "control_plane": {"enabled": False},
        "execution": {"monitor": monitor},
    })


def test_downwait_races_retirement_interrupt():
    """K1 (D1-F5): with slot contention forcing a lease retirement at ~1.5 s
    while the GSL is down, the lease rotation must complete by the retirement
    deadline, not be held until the outage recovers at ~5 s.

    The D1-F5 pre-dequeue gate keeps the constant-rate gated head in the
    endpoint queue (the shared server is never pinned in _transmit), and the
    queue-level handover (_evaluate_handover) releases the retiring link on
    time; the packet is re-associated and delivered."""
    # lazy endpoint activation: A takes sat0 when its first packet emits at
    # 0.5; B's later demand (0.7) contends on sat0, forcing A's lease to
    # retire at 1.5 while the GSL is down
    rows = [row(1, 0.5, A, B), row(2, 0.7, B, A)]
    res = kernel.run_simulation(_contention_cfg(), rows, geometry=_two_sat_geo())
    assert res["fates"][1] == "DELIVERED"
    retire = [e for e in res["handover"]["events"]
              if e["type"] == "release"
              and str(e["reason"]).startswith("lease")]
    assert retire and retire[0]["t"] <= 2.0 + 1e-9, (
        "lease rotation did not race: link held until the outage recovered")


def test_no_premature_deadline_fail_while_retirement_pending():
    """K3 (D1-F5): with a 2.0 s deadline and a lease retirement pending at
    ~1.5 s, the packet must never be failed with DATA_DEADLINE_EXPIRED
    before its deadline.  After D1-F5 the constant-rate gated head stays in
    the endpoint queue, the lease rotates on time, and the packet is
    re-associated and delivered before the deadline (rescued); if it ever
    expires, the fate must be at >= the deadline."""
    rows = [row(1, 0.5, A, B, deadline=2.0), row(2, 0.7, B, A)]
    res = kernel.run_simulation(_contention_cfg(monitor=True), rows,
                                geometry=_two_sat_geo())
    assert res["fates"][1] == "DELIVERED", (
        "packet was not rescued by the retirement race: %s" % res["fates"][1])
    fate_events = [t for t, kind, kv in res["monitor_log"]
                   if kind == "fate" and dict(kv).get("pid") == 1]
    if res["fates"][1] == "DATA_DEADLINE_EXPIRED":
        assert fate_events and fate_events[0] >= 2.0 - 1e-9, (
            "packet failed before its deadline (premature fail-fast)")


def test_stop_settle_excludes_preservice_downwait():
    """K2: when a GSL uplink is down before service starts, recovers at r
    within the horizon, and the service then crosses the stop, the stop-time
    settle must count only the real transmission time (horizon - r), never
    the pre-service down-wait from the caller's original _svc stamp."""
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1, "duration_s": 1.2033,
                     "time_step_s": 0.1, "seed": 4},
        "access": {"uplink_rate_mbps": 1.0},
        "links": {"ge_enabled": True,
                  "ge_gsl": {"mean_good_s": 1.0, "mean_bad_s": 2.0}},
        "control_plane": {"enabled": False},
    })
    geo = StaticGeometry(1, visible=lambda *_: True)
    res = kernel.run_simulation(cfg, [row(1, 0.6, A, B)], geometry=geo)
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"
    # replay the private GE stream to recover the true up-time r
    ge = outage.GilbertElliott(1.0, 2.0,
                               rngmod.link_stream(4, "gsl:0:" + A),
                               enabled=True)
    r = None
    t = 0.6
    horizon = cfg["config"]["scenario"]["duration_s"]
    while t <= horizon + 1e-6:
        if not ge.is_down(t):
            r = t
            break
        t += 0.0005
    assert r is not None
    expected = horizon - r
    # the replay scan is quantized to 5e-4 s, so allow that tolerance
    assert res["occupied"]["gsl_uplink_s"] == pytest.approx(expected, abs=1e-3)


def test_deadline_equals_retire_at_does_not_swallow_retirement():
    """R4B B1 (D1-F5): when the packet deadline equals the hard retirement
    instant and the constant-rate pre-dequeue gate keeps the packet assigned
    to the retiring link, the retirement side effect (release + requeue) must
    still run at the retire deadline -- never swallowed by the deadline fate.
    The packet is re-decided on the new association and fails at the
    deadline."""
    # A holds sat0 from t=0 with a 1.0 s lease (retire_at = 1.5); B's demand
    # at 0.7 forces the rotation.  pkt1 is assigned to sat0 and GE-gated for
    # the whole run, so it stays queued past the retire deadline; its
    # deadline equals retire_at (1.5).
    cfg = _contention_cfg(monitor=True)
    k = kernel.Kernel(cfg, [row(2, 0.7, B, A)], geometry=_two_sat_geo())
    epA = k._ensure_endpoint(A)
    # real association registers A in slots[0] so B's demand at 0.7 queues
    # on the access wait list and forces A's lease rotation at 1.0
    k._associate(epA, 0, k.env.now)
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pkt.deadline = 1.5
    pkt.assigned_sat = 0
    k.ledger.register(pkt.pid, pkt.bits)
    epA.queue.append(pkt)
    epA.queued_bits += pkt.bits
    epA.area.add(pkt.bits, k.env.now)
    ge = k._gsl_ge(0, A)
    ge._bad = True
    ge._last_t = 0.0
    ge._next_flip = float("inf")
    k._poke(k.uplinks[0].wake)
    k.env.run(until=10.0)
    # In the queue-level flow the deadline sweep at 1.5 empties the queue
    # before _evaluate_handover runs, so the retiring link is released as
    # "lease_drained" at the same instant -- the side effect must still run
    # (never swallowed by the deadline fate).
    release = [e for e in k.handover_events
               if e["type"] == "release"
               and str(e["reason"]).startswith("lease")]
    assert release, "retirement side effect was swallowed by the deadline fate"
    assert release[0]["t"] <= 1.5 + 1e-9
    # the packet must not be failed before the deadline
    fate_t = [t for t, kind, kv in k.monitor_log
              if kind == "fate" and dict(kv).get("pid") == 1]
    assert fate_t and fate_t[0] >= 1.5 - 1e-9


@pytest.mark.parametrize("deadline", [None, 1.0])
def test_active_service_retiring_at_completion_is_not_reported_ok(deadline):
    """R7-D1-F1: a retirement scheduled after service starts still wins a
    tie with service completion (and with the packet deadline).  ``_transmit``
    must re-read the mutable link lifecycle after the interrupt race; the
    ``retire_t`` snapshot taken while the link was active is necessarily
    absent.
    """
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "num_planes": 1,
                     "duration_s": 2.0},
        "links": {"geometry_loss": False, "ge_enabled": False},
        "control_plane": {"enabled": False},
    })
    k = kernel.Kernel(cfg, [], geometry=StaticGeometry(1))
    ep = k._ensure_endpoint(A)
    link = kernel.Link(0, "active", k.env.now,
                       interrupt=k.env.event())
    ep.links[0] = link
    pkt = kernel.DataPacket(1, A, B, 1_000_000, None, 0.0)
    pkt.deadline = deadline
    outcome = []

    def transmit():
        result = yield k.env.process(k._transmit(
            1.0, pkt, ("gsl", 0, ep, link), "gsl_uplink_s"))
        outcome.append(result)

    def retire_at_completion():
        yield k.env.timeout(1.0)
        link.state = "retiring"
        link.retire_at = 1.0
        if not link.interrupt.triggered:
            link.interrupt.succeed()

    k.env.process(transmit())
    k.env.process(retire_at_completion())
    k.env.run(until=1.1)
    assert outcome == ["retired"]
