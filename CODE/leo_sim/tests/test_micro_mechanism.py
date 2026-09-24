"""Stage-1 micro-mechanism scenarios (M1 / M2 / M3) for the leo_sim V2 kernel.

Read-only with respect to the platform: every scenario drives the real kernel
through kernel.run_simulation(..., decision_sink=sink) and reads only immutable
outputs (decision rows, packet_events, link_service_windows, fate_counts,
caches).  No source file, mechanism counter or other test is touched.


Why this fixture can represent these mechanisms
-----------------------------------------------
Topology: a 4-cycle plus one isolated satellite (sat2 keeps the constellation
size of the task brief's 4-satellite baseline without joining any path)::

    sat0 --E--> sat1 --E--> sat4         sat0 --W--> sat3 --W--> sat4
    sat1 --W--> sat0, sat4 --W--> sat1   sat3 --E--> sat0, sat4 --E--> sat3
    sat2: no neighbours (isolated)

sat0 serves source cell A; sat4 is the only satellite that can see destination
cell B.  sat0 therefore has two **exactly equal-cost** first-hop candidates
towards B: direction E (0 -> 1 -> 4) and direction W (0 -> 3 -> 4).  Both are
viable end-to-end paths (M2 measures real deliveries over both [0, 1, 4] and
[0, 3, 4]), and both carry the same hop anchor dist = 1 because sat1 and sat3
are each one hop from the serving satellite sat4.  Under
routing.policy = info_queue the only term left that can order them is the
documented first-hop queue term (routing.py:292-296, 310, 323)::

    w(dir)   = 1.0 + own_queue_bits[dir] / max(1.0, isl_rate_bps)
    total    = w(dir) + dist(peer -> advertised destination server)
    sort key = (total, direction name)        # tie: "E" < "W"

Deliberate, measured deviation from the task brief.  The brief assumed sat1
*and* sat3 both serve B, giving two 1-hop candidates.  That is impossible on
this platform: an endpoint holds at most ONE active access association at a
time (Kernel._try_grant grants the best-elevation candidate and returns;
_access_tick_endpoint stops requesting once primary_link() exists), so exactly
one satellite ever advertises serve_cells = [B].  With the brief's Y-topology
the other branch then has dist = 2 *and* is a routing dead end (it would
forward straight back into the packet's own path), which cannot isolate the
queue term.  The 4-cycle keeps every property the brief needed (two
equal-cost, both-viable first hops; tie broken by direction name) and drops
only the broken premise.

Parameters (all explicit: the platform defaults do not give a clean window):

* links.isl_rate_mbps = 10: one 8 Mbit data packet occupies an ISL egress for
  0.8 s (queueing is observable), while one 8000 bit control packet costs
  0.8 ms, so control-plane noise in own_queue_bits stays below 1e-3 of a data
  packet.
* access.uplink_rate_mbps = 1000: one 8 Mbit packet crosses the uplink in
  8 ms, so several packets can enter sat0 inside one burst window.
* control_plane: enabled, advertise_interval_s = 2.0, ttl_s = 30, vis_k = 2,
  packet_bits = 8000.  A non-oracle policy discovers legal egresses only from
  arrived advertisements, so the control plane must be on; vis_k = 2 is what
  lets sat4's service claim actually reach sat0 (4 -> 1 -> 0).

Cold-start trap (why every scenario emits a warm-up packet first): the
destination endpoint is created lazily by Kernel._ensure_endpoint and its
association only becomes active on a later access tick, so the first decision
for a destination cell sees an empty serving set.  Concretely, the t=0
advertisement of sat4 still has serve_cells = []; the first advertisement that
carries B is the t=2.0 round, and sat0 can first forward legally at t=2.1.
row(99, 0.0, A, B) performs that warm-up, and all measurements start at
t >= 3.0, i.e. after the cache is warm and valid.

Known limits (do not over-read the numbers below)
-------------------------------------------------
1. own_queue_bits counts only bits **waiting** in the egress queue:
   ISLLink.data_bits + ctrl_bits excludes the packet currently in service (it
   was popped from data_q).  So "E is loaded" first appears once a second
   same-direction packet starts *waiting*, not while the first one is
   transmitting.  M2's measured choice sequence (E, E, W, W, E) is exactly
   this semantics plus equal-cost branch queue feedback - it is not jitter.
2. Control packets have non-preemptive priority over queued data (ISLLink
   docstring: queued control overtakes queued data when the link next goes
   idle).  All FIFO assertions here are therefore scoped to **data** packets;
   M3 additionally measures the resulting service gap, which is an integer
   number of control-packet service times.
3. sat1/sat3 forward each other's advertisements, so sat0's two egress
   directions carry a little control noise.  It is bounded well below one data
   packet at these parameters, and every ordering conclusion is re-derived
   from the **measured** own_queue_bits via _derived_info_queue_order instead
   of being assumed.
4. StaticGeometry is scripted geometry: these tests characterise platform
   mechanics, not any real constellation's visibility statistics.
5. Emission times and durations are chosen for observability, not as a load
   model; every number is a deterministic single-run value.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import (StaticGeometry, cell, cell_center,
                                        make_cfg, row)

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)

# 4-cycle (0-1-4-3-0) plus isolated sat2; every edge has its directed reverse.
CYCLE_NB = {0: {"E": 1, "W": 3},
            1: {"W": 0, "E": 4},
            3: {"E": 0, "W": 4},
            4: {"W": 1, "E": 3}}

ISL_RATE_MBPS = 10.0
UPLINK_RATE_MBPS = 1000.0
ISL_RATE_BPS = ISL_RATE_MBPS * 1e6
DATA_BITS = 8_000_000
CTRL_BITS = 8_000            # control_plane.packet_bits
CTRL_SERVICE_S = CTRL_BITS / ISL_RATE_BPS
WARM_PID = 99
DURATION_S = 40.0
LINK_0_1 = "isl:0:1"          # sat0 egress in direction E
LINK_0_3 = "isl:0:3"          # sat0 egress in direction W


def _visible(sat, lat, lon, t):
    """sat0 sees only the source cell, sat4 only the destination cell."""
    return (sat == 0 and (lat, lon) == AC) or (sat == 4 and (lat, lon) == BC)


def _run(policy, rows):
    """Run the shared fixture with one routing policy; return (res, sink)."""
    cfg = make_cfg({
        "scenario": {"duration_s": DURATION_S, "num_satellites": 5,
                     "num_planes": 1, "time_step_s": 0.1},
        "links": {"isl_rate_mbps": ISL_RATE_MBPS},
        "access": {"uplink_rate_mbps": UPLINK_RATE_MBPS},
        "control_plane": {"enabled": True, "vis_k": 2, "ttl_s": 30.0,
                          "advertise_interval_s": 2.0,
                          "packet_bits": CTRL_BITS},
        "routing": {"policy": policy},
    })
    geo = StaticGeometry(5, neighbors_map=CYCLE_NB, visible=_visible)
    sink: list[dict] = []
    res = kernel.run_simulation(cfg, rows, geometry=geo, decision_sink=sink)
    return res, sink


def _forwards(sink, sat=0):
    """Decision rows for forward decisions at one satellite, in commit order."""
    return [d for d in sink if d["sat"] == sat and d["kind"] == "forward"]


def _derived_info_queue_order(q, dist=1.0, rate_bps=ISL_RATE_BPS):
    """Reproduce the candidate order from MEASURED own_queue_bits.

    info_queue weight (routing.py:292-296) plus the hop anchor
    (routing.py:310) and the deterministic tie-break on the direction name
    (routing.py:323).  In this fixture both branches have dist = 1 (sat1 and
    sat3 are each one hop from the serving satellite sat4), so the queue term
    is the only thing that can order E before W.
    """
    scored = [(1.0 + q[d] / max(1.0, rate_bps) + dist, d) for d in ("E", "W")]
    scored.sort(key=lambda item: (item[0], item[1]))
    return [d for _total, d in scored]


def _derived_hop_order():
    """hop weight is the constant 1.0 (routing.py:291-292).

    The queue never enters the ordering, so the order stays ["E", "W"]
    however long the egress queue grows.
    """
    scored = [(1.0 + 1.0, d) for d in ("E", "W")]
    scored.sort(key=lambda item: (item[0], item[1]))
    return [d for _total, d in scored]


def _isl_data_pairs(res):
    """link_id -> [(queue_id, enqueue_at, service_start_at, pid)] for data.

    queue_enter allocates queue_id and service_start carries it back
    (Kernel._metric_queue_enter / _metric_service_start), so the pair
    identifies the exact enqueue a service consumed.  A packet that was
    requeued after a stall gets a fresh queue_id and its stale enqueue is left
    unpaired, which is why only actually-served enqueues appear here.  Control
    packets emit no data events at all (see limit 2 in the file docstring), so
    this helper is inherently data-only.
    """
    enters = {}
    for e in res["packet_events"]:
        if e["kind"] == "queue_enter" and str(e.get("link_id", "")).startswith("isl:"):
            enters[e["queue_id"]] = (e["link_id"], float(e["at"]), e["pid"])
    per_link: dict[str, list] = {}
    for e in res["packet_events"]:
        if e["kind"] != "service_start" or e.get("stage") != "isl":
            continue
        key = e.get("queue_id")
        assert key in enters, f"isl service_start without queue_enter: {e}"
        link_id, enqueue_at, pid = enters[key]
        assert e["link_id"] == link_id, (e, enters[key])
        assert e["pid"] == pid, (e, enters[key])
        per_link.setdefault(link_id, []).append(
            (key, enqueue_at, float(e["at"]), pid))
    return per_link


def _assert_data_fifo(res, *, min_links, min_packets):
    """On every ISL: data enqueue order == data service-start order.

    queue_id is a monotone counter, so sorting by it is the true enqueue
    order; the comparison is against the service-start order.  The coverage
    floors make the check fail loudly (instead of passing vacuously) if the
    fixture ever degenerates to fewer links/packets than the mechanism needs.
    """
    per_link = _isl_data_pairs(res)
    links = 0
    packets = 0
    for link_id, rows in sorted(per_link.items()):
        if len(rows) < 2:
            continue
        links += 1
        packets += len(rows)
        by_enqueue = sorted(rows, key=lambda r: r[0])
        by_service = sorted(rows, key=lambda r: r[2])
        assert [r[3] for r in by_enqueue] == [r[3] for r in by_service], (
            f"{link_id} data FIFO violated: enqueue={by_enqueue} "
            f"service={by_service}")
        for i in range(1, len(by_service)):
            assert by_service[i - 1][2] <= by_service[i][2]
    assert links >= min_links, f"FIFO check covered only {links} ISLs"
    assert packets >= min_packets, f"FIFO check covered only {packets} packets"
    return per_link


def _assert_link_fifo(res, link_id, expected_pids):
    """Exact enqueue/service pid sequence on one ISL (data only)."""
    rows = _isl_data_pairs(res)[link_id]
    by_enqueue = [r[3] for r in sorted(rows, key=lambda r: r[0])]
    by_service = [r[3] for r in sorted(rows, key=lambda r: r[2])]
    assert by_enqueue == list(expected_pids), (link_id, by_enqueue)
    assert by_service == list(expected_pids), (link_id, by_service)


def _windows_by_pid(res, link_id):
    """pid -> service window on one link; duplicate service fails loudly."""
    out = {}
    for w in res["link_service_windows"]:
        if w["link_id"] != link_id:
            continue
        assert w["pid"] not in out, f"pid {w['pid']} served twice on {link_id}"
        out[w["pid"]] = w
    return out


def _assert_no_queue_overflow(res):
    """No ISL/access/holding queue overflow anywhere in the run."""
    counts = res["fate_counts"]
    assert counts["ISL_QUEUE_OVERFLOW"] == 0, counts
    assert counts["ACCESS_QUEUE_OVERFLOW"] == 0, counts
    assert counts["HOLDING_QUEUE_OVERFLOW"] == 0, counts
    assert "ISL_QUEUE_OVERFLOW" not in set(res["fates"].values())
    assert "ACCESS_QUEUE_OVERFLOW" not in set(res["fates"].values())


def _assert_cache_serves_destination(res):
    """sat4 really advertised B and the entry is fresh (non-oracle policies)."""
    assert res["mechanisms"]["effective"]["control_plane"] is True
    # NOTE: cache-map keys are integer satellite ids, not strings.
    entry = res["caches"][0][4]
    assert entry["visible_cells"] == [B]
    assert entry["serve_cells"] == [B]
    assert entry["valid"] is True


def test_m1_idle_link_keeps_routing_order_and_isl_fifo():
    """M1 no-queue negative control.

    (a) the chosen direction is the first candidate, (b) both candidates are
    legal, (c) no ISL/access queue overflow, (d) on every ISL the data enqueue
    order equals the data service-start order.
    """
    # (i) the queue-SENSITIVE policy used by M2, at an idle instant.
    res_idle, sink_idle = _run("info_queue", [row(WARM_PID, 0.0, A, B),
                                              row(1, 3.0, A, B)])
    _assert_cache_serves_destination(res_idle)
    forwards = _forwards(sink_idle)
    assert [d["pid"] for d in forwards] == [WARM_PID, 1]
    probe = forwards[-1]
    # decided promptly at its own emission time (no stale hold, no re-decision)
    assert 3.0 <= probe["t"] < 3.0 + 0.05
    # (b) both directions are legal at commit time: _record_decision stores the
    # legal action set, so a filtered/illegal branch could not appear here.
    assert probe["candidates"] == ["E", "W"]
    # (a) the commit equals the first candidate, and it is the idle tie winner
    assert probe["chosen"] == probe["candidates"][0] == "E"
    # measured idle state feeding the weight: zero queued bits either way
    assert probe["own_queue_bits"] == {"E": 0, "W": 0}
    assert _derived_info_queue_order(probe["own_queue_bits"]) == probe["candidates"]
    assert res_idle["fates"] == {WARM_PID: "DELIVERED", 1: "DELIVERED"}
    # (c) no queue overflow on either the ISL or the access boundary
    _assert_no_queue_overflow(res_idle)
    # (d) FIFO per ISL over both hops used by this run (isl:0:1 and isl:1:4,
    # two served data packets each).  No overlap here by construction - the
    # overlapping-queue evidence is the (ii) run below plus M2/M3.
    _assert_data_fifo(res_idle, min_links=2, min_packets=4)

    # (ii) queue-BLIND control on the SAME fixture and emission schedule: the
    # egress queue really builds up (up to two waiting data packets), yet the
    # order never changes.  So the M2 reversal cannot come from the fixture
    # itself, only from the policy's queue term.
    burst = [row(WARM_PID, 0.0, A, B)] + [
        row(1 + i, 3.0 + 0.05 * i, A, B) for i in range(4)]
    res_hop, sink_hop = _run("hop", burst)
    hop_forwards = _forwards(sink_hop)
    assert [d["pid"] for d in hop_forwards] == [WARM_PID, 1, 2, 3, 4]
    assert [d["chosen"] for d in hop_forwards] == ["E"] * 5
    assert all(d["candidates"] == ["E", "W"] for d in hop_forwards)
    assert all(d["chosen"] == d["candidates"][0] for d in hop_forwards)
    # measured queue growth that hop ignores: 0, 0, 0, 8 Mbit, 16 Mbit waiting
    assert [d["own_queue_bits"]["E"] for d in hop_forwards] == [
        0, 0, 0, DATA_BITS, 2 * DATA_BITS]
    assert [d["own_queue_bits"]["W"] for d in hop_forwards] == [0, 0, 0, 0, 0]
    for d in hop_forwards:
        assert _derived_hop_order() == d["candidates"] == ["E", "W"]
    assert set(res_hop["fates"].values()) == {"DELIVERED"}
    _assert_no_queue_overflow(res_hop)
    # (d) again, now with genuine queue overlap: four data packets wait behind
    # the in-service one on isl:0:1, and the order is still FIFO.
    _assert_data_fifo(res_hop, min_links=2, min_packets=8)
    _assert_link_fifo(res_hop, LINK_0_1, [WARM_PID, 1, 2, 3, 4])


def test_m2_burst_reverses_candidate_order_on_equal_cost_branches():
    """M2: one burst flips the candidate order from [E, W] to [W, E].

    (a) the first measurement packet chooses E, (b) later packets in the same
    burst, under the same policy and the same fixture, choose W, (c) the
    measured own_queue_bits at the reversal show E far above W, (d) the
    physical cause is an E-queue enqueue event strictly before the reversal.
    """
    rows = [row(WARM_PID, 0.0, A, B)] + [
        row(1 + i, 3.0 + 0.05 * i, A, B) for i in range(5)]
    res, sink = _run("info_queue", rows)
    _assert_cache_serves_destination(res)
    forwards = {d["pid"]: d for d in _forwards(sink)}
    assert set(forwards) == {WARM_PID, 1, 2, 3, 4, 5}

    # (a) pre-burst baseline: idle queues -> tie -> direction name decides -> E
    first = forwards[1]
    # absolute times carry a deterministic sub-millisecond offset, so the
    # anchors below are pinned to 1 ms while the mechanism moves on 50 ms and
    # 800 ms scales
    assert first["t"] == pytest.approx(3.01, abs=1e-3)
    assert first["candidates"] == ["E", "W"]
    assert first["chosen"] == "E" == first["candidates"][0]
    assert first["own_queue_bits"] == {"E": 0, "W": 0}

    # (b) the reversal.  Measured sequence, one entry per measurement packet:
    #   1 E  (idle tie)
    #   2 E  (packet 1 is IN SERVICE, so own_queue_bits is still 0)
    #   3 W  <- packet 2 is now WAITING behind packet 1 on isl:0:1
    #   4 W  (E still holds one waiting packet)
    #   5 E  (both queues hold one waiting packet -> tie again -> E)
    choices = [forwards[p]["chosen"] for p in (1, 2, 3, 4, 5)]
    assert choices == ["E", "E", "W", "W", "E"]
    turned = forwards[3]
    assert turned["t"] == pytest.approx(3.11, abs=1e-3)
    # the candidate ORDER itself reversed, not just the commit
    assert turned["candidates"] == ["W", "E"]
    assert turned["chosen"] == turned["candidates"][0] == "W"
    assert first["candidates"] == ["E", "W"]

    # (c) measured queue bits at the reversal: E carries a full waiting data
    # packet, W carries nothing
    assert turned["own_queue_bits"]["E"] == DATA_BITS
    assert turned["own_queue_bits"]["W"] == 0
    assert turned["own_queue_bits"]["E"] > 8 * turned["own_queue_bits"]["W"]
    # the order is exactly reproduced from the measured bits (dist is equal on
    # both branches, so the queue term is the sole cause)
    for pid in (1, 2, 3, 4, 5):
        q = forwards[pid]["own_queue_bits"]
        assert _derived_info_queue_order(q) == forwards[pid]["candidates"]

    # (d) physical timing: pid2 enqueued into isl:0:1 at 3.06 and was still
    # waiting at the 3.11 reversal (its service only starts at 3.81), because
    # pid1 occupies the E egress from 3.01 to 3.81.
    enqueue = {e["pid"]: float(e["at"]) for e in res["packet_events"]
               if e["kind"] == "queue_enter" and e.get("link_id") == LINK_0_1}
    windows = _windows_by_pid(res, LINK_0_1)
    assert enqueue[2] == pytest.approx(3.06, abs=1e-3)
    assert enqueue[2] < turned["t"] < windows[2]["start"]
    assert windows[1]["end"] == pytest.approx(windows[2]["start"], abs=1e-6)
    assert windows[2]["start"] == pytest.approx(3.81, abs=1e-3)
    assert DATA_BITS / ISL_RATE_BPS == pytest.approx(0.8, abs=1e-12)

    # both equal-cost branches really carry traffic to the destination, so the
    # W choice is a viable path, not a dead end
    assert {tuple(v["path"]) for v in res["deliveries"].values()} == {
        (0, 1, 4), (0, 3, 4)}
    assert set(res["fates"].values()) == {"DELIVERED"}
    _assert_no_queue_overflow(res)
    # W-branch FIFO with real overlap: pids 3 and 4 both wait on isl:0:3
    _assert_link_fifo(res, LINK_0_3, [3, 4])
    _assert_data_fifo(res, min_links=3, min_packets=8)


def test_m3_later_data_packet_cannot_jump_ahead_of_target_in_isl_fifo():
    """M3: on one ISL a later data packet may not overtake an earlier target.

    Assertions are scoped to data packets on purpose: control packets have
    non-preemptive priority and are allowed to overtake queued data (see limit
    2 in the file docstring).  The control exception is measured at the end
    rather than assumed away.
    """
    target = 1
    later = (2, 3)
    res, sink = _run("hop", [row(WARM_PID, 0.0, A, B),
                             row(target, 3.0, A, B),
                             row(2, 3.05, A, B),
                             row(3, 3.10, A, B)])
    # hop -> the only legal direction is E, so all three data packets contend
    # for the same ISL and the FIFO question is not confounded by routing.
    assert [d["chosen"] for d in _forwards(sink)] == ["E"] * 4

    enqueue = {e["pid"]: float(e["at"]) for e in res["packet_events"]
               if e["kind"] == "queue_enter" and e.get("link_id") == LINK_0_1}
    windows = _windows_by_pid(res, LINK_0_1)
    assert set(windows) == {WARM_PID, target, 2, 3}

    # the target is enqueued first, the later packets after it, in order
    assert enqueue[target] < enqueue[2] < enqueue[3]
    # data service order == data enqueue order on this ISL
    assert windows[target]["start"] < windows[2]["start"] < windows[3]["start"]
    _assert_link_fifo(res, LINK_0_1, [WARM_PID, target, 2, 3])

    # no later packet is served before the target has finished, and each of
    # them really did wait in the queue (so the FIFO claim is not vacuous)
    for pid in later:
        assert windows[pid]["start"] >= windows[target]["end"]
        assert windows[pid]["start"] > enqueue[pid]
    # all three overlapped in the queue: the third was enqueued while the
    # target was still being served
    assert enqueue[3] < windows[target]["end"]

    # measured windows: target 3.01-3.81, pid2 3.81-4.61, pid3 4.6116-5.4116
    assert windows[target]["start"] == pytest.approx(3.01, abs=1e-3)
    assert windows[target]["end"] == pytest.approx(3.81, abs=1e-3)
    assert windows[2]["start"] == pytest.approx(3.81, abs=1e-3)
    assert windows[3]["start"] == pytest.approx(4.6116, abs=1e-3)

    # control exception, measured: data packets can be served back to back
    # (gap 0 on the 1 -> 2 boundary), while the 2 -> 3 boundary is exactly two
    # control-packet service times (2 x 8000 bit / 10 Mbps = 1.6 ms) - i.e.
    # control traffic was served in between, which is why the FIFO assertion
    # above is data-only.
    assert windows[2]["start"] - windows[1]["end"] <= 1e-6
    gap = windows[3]["start"] - windows[2]["end"]
    assert gap > 0.0
    assert round(gap / CTRL_SERVICE_S) >= 1
    assert gap / CTRL_SERVICE_S == pytest.approx(
        round(gap / CTRL_SERVICE_S), abs=1e-6)

    assert set(res["fates"].values()) == {"DELIVERED"}
    _assert_no_queue_overflow(res)
