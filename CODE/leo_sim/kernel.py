"""Bounded SimPy discrete-event kernel for leo_sim V2.

Formal data path (no Gateway anywhere):
immutable trace -> sparse TrafficEndpoint -> finite association (K access
slots, acquisition delay) -> satellite ingress -> dynamic finite ISL ->
arrived-control local cache -> legal egress discovery (destination endpoint
must be actively associated and served) -> finite downlink -> destination
TrafficEndpoint.

Fair finite access: endpoints request association from CURRENT demand
(queued uplink packets, or a satellite holding this endpoint's downlink
traffic). Free slots are granted immediately (pre-positioning when nothing
contends). Under contention each satellite keeps a deterministic FIFO wait
queue; holders rotate out when (a) their association exceeds slot_lease_s
(graceful retire: packets assigned while the link was active drain, the
retirement deadline is the hard backstop and races any in-flight service),
or (b) they have been idle for idle_release_s. Rotation period per holder is
bounded by slot_lease_s + min(assigned-backlog drain, retirement_deadline_s)
+ acquisition_delay_s, so a waiting demanding endpoint is served within
queue-position x that bound.

Transmission race semantics: every service races (a) service completion,
(b) certified deterministic geometry loss, (c) Gilbert-Elliott outage,
(d) data deadline, (e) hard link retirement. GE trajectories are
continuous-time two-state processes with exponential dwells on private
per-link RNG streams, so outcomes never depend on query patterns. A failure
mid-flight counts only the service time already occupied; no implicit
pause/resume, no ARQ. A hard-retired packet is requeued in full (the partial
transmission never reached the receiver, so this is not a duplicate send);
its occupied time stays accounted and it keeps exactly one eventual fate.
Deadlines are enforced again after each propagation segment.

Horizon: the closed interval [0, duration_s]; a dedicated closer process
guarantees the clock reaches the exact horizon, where in-service occupation,
queue areas and IN_SYSTEM_AT_STOP all settle.

GSL uplink and downlink are explicit full-duplex resources: separate
capacities, each shared across endpoints by deficit round-robin (DRR) with a
configured quantum. ISL queues are per-direction with a single capacity
shared by data and control; control has non-preemptive priority.
"""
from __future__ import annotations

import math
import hashlib
from collections import deque

import numpy as np
import simpy

from . import (control, fates, grid as gridmod, learning as _learning, metrics,
               model, q0, link_budget)
from . import outage, rng as rngmod, routing
from . import trace as tracemod

LearningUnavailable = _learning.LearningUnavailable


class KernelError(RuntimeError):
    pass


class CapExceeded(KernelError):
    """A configured bound (events/entities/packets) was exceeded. Fail closed."""


class DataPacket:
    __slots__ = ("pid", "src", "dst", "bits", "deadline", "emitted_at", "path",
                 "assigned_sat", "learning_state", "learning_action",
                 "learning_reward", "isl_enqueued_at", "holding_until",
                 "metric_queue_id", "metric_prop_id", "metric_ingress_at",
                 "decision_id")

    def __init__(self, pid, src, dst, bits, deadline, emitted_at):
        self.pid = pid
        self.src = src
        self.dst = dst
        self.bits = bits
        self.deadline = deadline
        self.emitted_at = emitted_at
        self.path: list[int] = []
        self.assigned_sat: int | None = None
        self.learning_state = None
        self.learning_action = None
        self.learning_reward = None
        # enqueue time on the current ISL egress queue; the realized queue
        # wait (service start minus this) feeds the M1 queue reward
        self.isl_enqueued_at = None
        self.holding_until = None
        self.metric_queue_id = None
        self.metric_prop_id = None
        self.metric_ingress_at = None
        # identity of the most recent COMMITTED decision for this packet;
        # set only when a decision/timeline sink is attached, so that
        # downstream milestones can be attributed to the decision that
        # caused them without touching packet_events
        self.decision_id = None


class QueueArea:
    """Exact queued-bits x seconds integral. Mutations call add/remove with
    the current time; close(t) settles the integral at the stop time."""

    __slots__ = ("area", "bits", "last")

    def __init__(self):
        self.area = 0.0
        self.bits = 0
        self.last = 0.0

    def _acc(self, now: float):
        self.area += self.bits * (now - self.last)
        self.last = now

    def add(self, bits: int, now: float):
        self._acc(now)
        self.bits += bits

    def remove(self, bits: int, now: float):
        self._acc(now)
        self.bits -= bits

    def close(self, t: float):
        self._acc(t)


class SatelliteHoldingQueue:
    """Finite FIFO for packets held by a satellite between decisions.

    Unlike the legacy ``pending`` list, admission is capacity checked and
    every mutation updates the shared queue-area integral.  The small list
    compatibility surface is intentional while callers migrate: iteration,
    indexing, ``append`` and equality remain available to old diagnostics.
    """

    __slots__ = ("capacity_bits", "area", "_items", "queued_bits", "_now")

    def __init__(self, capacity_bits: int, area: QueueArea, now_fn=None):
        if capacity_bits < 0:
            raise ValueError("holding queue capacity must be >= 0")
        self.capacity_bits = capacity_bits
        self.area = area
        self._items: list[DataPacket] = []
        self.queued_bits = 0
        self._now = now_fn or (lambda: 0.0)

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, index):
        return self._items[index]

    def __eq__(self, other):
        if isinstance(other, SatelliteHoldingQueue):
            other = other._items
        return self._items == other

    def room(self, bits: int) -> bool:
        return self.queued_bits + bits <= self.capacity_bits

    def put(self, pkt: DataPacket, now: float) -> bool:
        if not self.room(pkt.bits):
            return False
        self._items.append(pkt)
        self.queued_bits += pkt.bits
        self.area.add(pkt.bits, now)
        return True

    def append(self, pkt: DataPacket) -> None:
        """Compatibility append at the kernel's current time.

        New kernel paths use ``put`` so an overflow can receive an explicit
        fate.  Direct legacy-style callers get a loud error instead of an
        unaccounted packet.
        """
        if not self.put(pkt, self._now()):
            raise CapExceeded("holding queue capacity exceeded")

    def remove(self, pkt: DataPacket, now: float) -> None:
        self._items.remove(pkt)
        self.queued_bits -= pkt.bits
        self.area.remove(pkt.bits, now)

    def pop(self, index: int = 0, now: float = 0.0):
        if not self._items:
            return None
        pkt = self._items.pop(index)
        self.queued_bits -= pkt.bits
        self.area.remove(pkt.bits, now)
        return pkt

    def clear(self, now: float) -> list[DataPacket]:
        items = list(self._items)
        self._items.clear()
        if self.queued_bits:
            self.area.remove(self.queued_bits, now)
        self.queued_bits = 0
        return items

    def take_ready(self, now: float) -> list[DataPacket]:
        """Remove packets whose explicit WAIT interval has elapsed."""
        ready = [p for p in self._items
                 if p.holding_until is None or now >= p.holding_until]
        if not ready:
            return []
        for pkt in ready:
            self.remove(pkt, now)
            pkt.holding_until = None
        return ready

    def sweep_expired(self, now: float) -> list[DataPacket]:
        """Remove packets whose data deadline has passed while being held."""
        expired = [p for p in self._items
                   if p.deadline is not None and now > p.deadline]
        for pkt in expired:
            self.remove(pkt, now)
        return expired


class ControlPacket:
    """A real control-plane packet (the task contract: origin, sequence,
    generated_at, received_at, ttl, remaining_hops, payload_bits,
    validity/AoI).

    received_at is None until the packet physically arrives at a receiving
    satellite; the arrival path sets it and records it in the control ledger.
    valid_at(t) is the TTL window from generation — the same rule the
    receiving cache enforces on arrived entries; AoI at any instant is
    t - generated_at (surfaced via the cache entry on arrival).
    """

    __slots__ = ("iid", "origin", "seq", "generated_at", "_received_at",
                 "ttl_s", "remaining_hops", "payload_bits", "payload")

    def __init__(self, iid, origin, seq, generated_at, ttl_s, remaining_hops,
                 bits, payload):
        if not isinstance(generated_at, (int, float)) \
                or isinstance(generated_at, bool) \
                or not math.isfinite(generated_at) or generated_at < 0:
            raise ValueError("ControlPacket generated_at must be finite and >= 0")
        if not isinstance(ttl_s, (int, float)) or isinstance(ttl_s, bool) \
                or not math.isfinite(ttl_s) or ttl_s <= 0:
            raise ValueError("ControlPacket ttl_s must be finite and > 0")
        if not isinstance(remaining_hops, int) \
                or isinstance(remaining_hops, bool) or remaining_hops < 0:
            raise ValueError("ControlPacket remaining_hops must be a non-negative int")
        if not isinstance(bits, int) or isinstance(bits, bool) or bits <= 0:
            raise ValueError("ControlPacket payload_bits must be a positive int")
        self.iid = iid
        self.origin = origin
        self.seq = seq
        self.generated_at = float(generated_at)
        self._received_at = None
        self.ttl_s = float(ttl_s)
        self.remaining_hops = remaining_hops
        self.payload_bits = bits
        self.payload = payload

    @property
    def bits(self) -> int:
        """Compatibility alias; payload_bits is the authoritative field."""
        return self.payload_bits

    @property
    def received_at(self) -> float | None:
        return self._received_at

    def mark_received(self, t: float) -> None:
        if self._received_at is not None:
            raise ValueError("ControlPacket received_at may only be set once")
        if not isinstance(t, (int, float)) or isinstance(t, bool) \
                or not math.isfinite(t) or t < self.generated_at:
            raise ValueError(
                "ControlPacket received_at must be finite and >= generated_at")
        self._received_at = float(t)

    def valid_at(self, t: float) -> bool:
        return self.generated_at <= t <= self.generated_at + self.ttl_s

    def aoi(self, t: float) -> float:
        if not isinstance(t, (int, float)) or isinstance(t, bool) \
                or not math.isfinite(t) or t < self.generated_at:
            raise ValueError("ControlPacket AoI time must be finite and >= generated_at")
        return float(t) - self.generated_at


class Link:
    """Endpoint<->satellite association state.

    cause: what put the link into retiring ("mbb" | "lease"); interrupt fires
    at retire_at so an in-flight service races the hard retirement deadline.
    """

    __slots__ = ("sat", "state", "since", "ready_at", "retire_at", "cause",
                 "interrupt")

    def __init__(self, sat, state, since, ready_at=0.0, retire_at=None,
                 cause=None, interrupt=None):
        self.sat = sat
        self.state = state  # acquiring | active | retiring
        self.since = since
        self.ready_at = ready_at
        self.retire_at = retire_at
        self.cause = cause
        self.interrupt = interrupt


class TrafficEndpoint:
    __slots__ = ("cell", "lat", "lon", "queue", "queued_bits", "links", "area")

    def __init__(self, cell):
        self.cell = cell
        self.lat, self.lon = gridmod.grid_center(cell)
        self.queue: deque[DataPacket] = deque()
        self.queued_bits = 0
        self.links: dict[int, Link] = {}
        self.area = QueueArea()

    def primary_link(self) -> Link | None:
        """The newest non-retiring link, if any."""
        best = None
        for link in self.links.values():
            if link.state in ("active", "acquiring"):
                if best is None or link.since >= best.since:
                    best = link
        return best


class _DRRMixin:
    """Deficit round-robin selection with a configured quantum."""

    def _drr_init(self, quantum: int):
        self.quantum = float(quantum)
        self.deficit: dict[str, float] = {}
        self.rr_cursor = 0

    def _drr_select(self, items, pick):
        """items: sorted keys; pick(key) -> head packet or None.

        Deficit round-robin: visits backlogged keys in rotating order, adds
        one quantum per visit, and serves the head packet when it fits the
        deficit. Packets larger than the quantum accumulate deficit over
        several visits, so bit-level fairness holds for heterogeneous packet
        sizes and no oversize packet can deadlock the server.
        Returns (key, pkt) or None.
        """
        avail = [(k, pick(k)) for k in items]
        avail = [(k, p) for k, p in avail if p is not None]
        if not avail:
            return None
        n = len(avail)
        while True:
            start = self.rr_cursor % n
            for offset in range(n):
                i = (start + offset) % n
                k, pkt = avail[i]
                dc = self.deficit.get(k, 0.0) + self.quantum
                self.deficit[k] = dc
                if pkt.bits <= dc:
                    self.deficit[k] = dc - pkt.bits
                    self.rr_cursor = i + 1
                    return k, pkt

class UplinkServer(_DRRMixin):
    """Shared GSL uplink service: DRR over associated endpoints."""

    def __init__(self, kern, sat):
        self.k = kern
        self.sat = sat
        self.wake = kern.env.event()
        self.current: tuple[TrafficEndpoint, DataPacket] | None = None
        self._svc = None
        self._svc_phase = None  # None | waiting_for_link | transmitting
        self._tx_started_at = None
        self._drr_init(kern.cfg_access["drr_quantum_bits"])
        kern.env.process(self._run())

    def _pick(self, ep: TrafficEndpoint):
        """First queued packet this link may serve (per-link FIFO preserved)."""
        link = ep.links.get(self.sat)
        if link is None or link.state not in ("active", "retiring"):
            return None
        if link.state == "retiring" and self.k.env.now >= link.retire_at:
            return None
        for p in ep.queue:
            if link.state == "retiring":
                # a retiring link drains only packets already assigned to it;
                # unassigned packets belong to the successor association.  The
                # availability gates apply to retiring links exactly as they
                # do to active ones: a gated head stays queued (the shared
                # server can serve other endpoints) instead of being dequeued
                # and pinning the server in _transmit.
                if p.assigned_sat != self.sat:
                    continue
            elif p.assigned_sat not in (None, self.sat):
                continue
            now = self.k.env.now
            # availability gates apply to every rate model (D1 F5): a head
            # whose GSL is geometrically unavailable or GE-down is never
            # dequeued, so the shared server cannot be pinned in _transmit.
            if not self.k.geometry.gsl_available(
                    self.sat, ep.lat, ep.lon, now):
                return None
            if self.k.ge_enabled:
                self.k.mech["ge_gsl_queries"] += 1
                if self.k._gsl_ge(self.sat, ep.cell).is_down(now):
                    return None
            if self.k.rate_model == "mcs":
                if self.k._link_rate(
                        "uplink", now, self.sat, ep=ep) <= 0:
                    return None
            return p
        return None

    def _run(self):
        k = self.k
        while True:
            cells = sorted(ep.cell for ep in k.endpoints.values())
            sel = self._drr_select(cells, lambda c: self._pick(k.endpoints[c]))
            if sel is None:
                if k.rate_model == "mcs":
                    heads = [(ep, p) for ep in k.endpoints.values()
                             for p in ep.queue
                             if p.assigned_sat in (None, self.sat)]
                    until, zero_held = k._gsl_wait_until(
                        self.sat, "uplink", heads)
                    if zero_held:
                        k.mech["mcs_zero_rate_holds"] += 1
                    # the tick stays as a catch-all for uncertified changes
                    # (association, handover), but the server never sleeps
                    # past a certified recovery/deadline instant
                    tick = k.env.now + k.time_step
                    until = tick if until is None else min(until, tick)
                    yield k.env.timeout(max(0.0, until - k.env.now)) | self.wake
                    # Sweep unconditionally: when a poke lands on the same
                    # timestamp as a certified deadline, the deadline must
                    # still be enforced exactly (D1 F2-RACE).  The sweep is
                    # idempotent, so running it on a wake-only resume is
                    # cheap and harmless.
                    for ep in k.endpoints.values():
                        k._sweep_endpoint_queue(ep)
                else:
                    # constant rate: no MCS certified events, but the
                    # geometry/GE pre-dequeue gates mean the server must wake
                    # at their recovery instants too (D1 F5), otherwise a
                    # gated head would strand the server until the next poke.
                    recovery = None
                    now = k.env.now
                    for ep in k.endpoints.values():
                        if not ep.queue:
                            continue
                        nxt = k.geometry.next_gsl_change(
                            self.sat, ep.lat, ep.lon, now, k.horizon)
                        if nxt is not None:
                            recovery = nxt if recovery is None else min(recovery, nxt)
                        if k.ge_enabled:
                            ge = k._gsl_ge(self.sat, ep.cell)
                            if ge.is_down(now):
                                nxt_up = ge.next_up(now)
                                if nxt_up <= k.horizon:
                                    recovery = (nxt_up if recovery is None
                                                else min(recovery, nxt_up))
                    if recovery is not None:
                        yield self.wake | k.env.timeout(
                            max(0.0, recovery - k.env.now))
                        if self.wake.triggered:
                            self.wake = k.env.event()
                    else:
                        yield self.wake
                        self.wake = k.env.event()
                self.wake = k.env.event()
                continue
            cell, pkt = sel
            ep = k.endpoints[cell]
            ep.queue.remove(pkt)
            ep.queued_bits -= pkt.bits
            ep.area.remove(pkt.bits, k.env.now)
            if pkt.assigned_sat is None:
                pkt.assigned_sat = self.sat
            self.current = (ep, pkt)
            k._note_busy(cell)
            k.service_log["uplink"].append((cell, pkt.pid))
            rate = None
            if k.rate_model == "mcs":
                rate = k._link_rate("uplink", k.env.now, self.sat, ep=ep)
                rate_fn = lambda t, sat=self.sat, ep=ep: k._link_rate(
                    "uplink", t, sat, ep=ep)
                rate_recover_fn = lambda t, lim, sat=self.sat, ep=ep: (
                    k.geometry.next_slant_range_under(
                        sat, ep.lat, ep.lon, k.rate_max_uplink_km, t, lim))
                dur = None
            else:
                rate_fn = rate_recover_fn = None
                dur = pkt.bits / k.ul_rate_bps
            self._service_rate_bps = (None if rate is not None
                                      else k.ul_rate_bps)
            self._svc = (k.env.now, "gsl_uplink_s")
            self._svc_phase = "waiting_for_link"
            self._tx_started_at = None
            outcome = yield k.env.process(
                k._transmit(dur, pkt, ("gsl", self.sat, ep, ep.links.get(self.sat)),
                            "gsl_uplink_s", owner=self, rate_fn=rate_fn,
                            rate_recover_fn=rate_recover_fn))
            k.service_log["uplink_bits"].append((k.env.now, cell, pkt.bits))
            self._svc = None
            self._svc_phase = None
            self._tx_started_at = None
            self.current = None
            if outcome == "retired":
                # hard retirement mid-service: the partial transmission never
                # reached the satellite, so requeueing the full packet at the
                # head for the new link is NOT a duplicate send; the occupied
                # service time is already accounted.
                pkt.assigned_sat = None
                ep.queue.appendleft(pkt)
                ep.queued_bits += pkt.bits
                ep.area.add(pkt.bits, k.env.now)
                k._metric_queue_enter(
                    pkt, "uplink", f"gsl:uplink:pending:{cell}")
                k._note_busy(cell)
                k._on_link_retired(ep, self.sat)
                for sat_id in list(ep.links):
                    k._poke(k.uplinks[sat_id].wake)
                continue
            if outcome == "stalled":
                ep.queue.appendleft(pkt)
                ep.queued_bits += pkt.bits
                ep.area.add(pkt.bits, k.env.now)
                k._metric_queue_enter(
                    pkt, "uplink", f"gsl:uplink:pending:{cell}")
                if k.env.now >= k.horizon:
                    # No service can start past the horizon: requeue and stop
                    # retrying in the same time slice, or the process loops
                    # forever with no event to advance the clock.
                    break
                yield self.wake
                self.wake = k.env.event()
                continue
            if outcome != "ok":
                continue
            now = k.env.now
            prop = model.propagation_delay_s(
                k.geometry.slant_range_km(self.sat, ep.lat, ep.lon, now))
            k._metric_propagation_start(
                pkt, "uplink", f"gsl:uplink:{self.sat}:{cell}", prop)
            k._in_flight[pkt.pid] = {
                "kind": "ingress", "sat": self.sat,
                "arrival_at": k.env.now + prop, "pkt": pkt}
            k.env.process(k._ingress_after_prop(pkt, self.sat, prop))


class DownlinkServer(_DRRMixin):
    """Shared GSL downlink service: finite shared queue, DRR over endpoints."""

    def __init__(self, kern, sat):
        self.k = kern
        self.sat = sat
        self.queues: dict[str, deque[DataPacket]] = {}
        self.queued_bits = 0
        self.area = QueueArea()
        self.wake = kern.env.event()
        self.current: DataPacket | None = None
        self._svc = None
        self._svc_phase = None  # None | waiting_for_link | transmitting
        self._tx_started_at = None
        self._drr_init(kern.cfg_access["drr_quantum_bits"])
        kern.env.process(self._run())

    def room(self, bits: int) -> bool:
        return self.queued_bits + bits <= self.k.cfg_access["downlink_queue_bits"]

    def put(self, pkt: DataPacket) -> None:
        self.queues.setdefault(pkt.dst, deque()).append(pkt)
        self.queued_bits += pkt.bits
        self.area.add(pkt.bits, self.k.env.now)
        self.k._metric_queue_enter(
            pkt, "downlink", f"gsl:downlink:{self.sat}:{pkt.dst}",
            decision_id=pkt.decision_id)
        self.k._note_busy(pkt.dst)
        self.k._poke(self.wake)

    def _servable(self, cell):
        """Head packet if this satellite may legally serve the endpoint now."""
        q = self.queues.get(cell)
        if not q:
            return None
        ep = self.k.endpoints[cell]
        link = ep.links.get(self.sat)
        if link is None or link.state not in ("active", "retiring"):
            return None
        if link.state == "retiring" and self.k.env.now >= link.retire_at:
            return None
        now = self.k.env.now
        if not self.k.geometry.gsl_available(self.sat, ep.lat, ep.lon, now):
            return None
        if self.k.ge_enabled:
            self.k.mech["ge_gsl_queries"] += 1
            if self.k._gsl_ge(self.sat, cell).is_down(now):
                return None
        if self.k.rate_model == "mcs":
            if self.k._link_rate(
                    "downlink", now, self.sat, ep=ep) <= 0:
                return None
        return q[0]

    def _run(self):
        k = self.k
        while True:
            cells = sorted(c for c, q in self.queues.items() if q)
            # packets whose endpoint lost this association go back to pending
            for c in cells:
                if self.queues[c] and self._servable(c) is None:
                    ep = k.endpoints[c]
                    link = ep.links.get(self.sat)
                    if link is None or link.state not in ("active", "retiring"):
                        # drain the WHOLE queue: every packet whose endpoint
                        # lost this association goes back to pending in FIFO
                        # order in one wake.  Draining one packet per wake
                        # would strand the tail of a backlogged queue until
                        # the next poke (and with no further traffic it could
                        # sit until the horizon).
                        while self.queues[c]:
                            pkt = self.queues[c].popleft()
                            self.queued_bits -= pkt.bits
                            self.area.remove(pkt.bits, k.env.now)
                            k._hold_packet(self.sat, pkt)
            sel = self._drr_select(cells, self._servable)
            if sel is None:
                if k.rate_model == "mcs":
                    # D1 precise wait: geometry/GE/rate recovery and packet
                    # deadlines all have certified times, so the server wakes
                    # at the exact event instead of blind time_step polling.
                    # all queued packets, not just the head: a tail packet's
                    # earlier deadline must also create a certified wake, or
                    # the server would degrade to blind time_step polling and
                    # expire it late (D1 round-5 independent finding 2)
                    heads = [(k.endpoints[c], pkt)
                             for c, q in self.queues.items()
                             for pkt in q]
                    until, zero_held = k._gsl_wait_until(
                        self.sat, "downlink", heads)
                    if zero_held:
                        k.mech["mcs_zero_rate_holds"] += 1
                    tick = k.env.now + k.time_step
                    until = tick if until is None else min(until, tick)
                    yield k.env.timeout(max(0.0, until - k.env.now)) | self.wake
                    # unconditional: a same-timestamp poke must not skip the
                    # deadline sweep (D1 F2-RACE)
                    k._sweep_downlink_queues(self.sat)
                else:
                    # Sleep until poked or until GSL geometry recovers for any
                    # queued cell.  Without this timer a temporary GSL outage
                    # strands queued packets until a new put() pokes the server,
                    # even after the satellite is visible again (the ISL server
                    # already schedules geometry recovery this way).  Wake
                    # events are also poked by kernel association changes
                    # (_release / _associate / _activate_after_delay), so a
                    # released or re-associated endpoint's packets move back to
                    # pending promptly instead of waiting for the next put().
                    recovery = None
                    now = k.env.now
                    for c in cells:
                        ep = k.endpoints[c]
                        nxt = k.geometry.next_gsl_change(
                            self.sat, ep.lat, ep.lon, now, k.horizon)
                        if nxt is not None:
                            recovery = nxt if recovery is None else min(recovery, nxt)
                        if k.ge_enabled:
                            ge = k._gsl_ge(self.sat, c)
                            if ge.is_down(now):
                                nxt_up = ge.next_up(now)
                                if nxt_up <= k.horizon:
                                    recovery = (nxt_up if recovery is None
                                                else min(recovery, nxt_up))
                    if recovery is not None:
                        yield self.wake | k.env.timeout(
                            max(0.0, recovery - k.env.now))
                        if self.wake.triggered:
                            self.wake = k.env.event()
                    else:
                        yield self.wake
                        self.wake = k.env.event()
                self.wake = k.env.event()
                continue
            cell, pkt = sel
            self.queues[cell].popleft()
            self.queued_bits -= pkt.bits
            self.area.remove(pkt.bits, k.env.now)
            self.current = pkt
            ep = k.endpoints[cell]
            k.service_log["downlink"].append((cell, pkt.pid))
            rate = None
            if k.rate_model == "mcs":
                rate = k._link_rate("downlink", k.env.now, self.sat, ep=ep)
                rate_fn = lambda t, sat=self.sat, ep=ep: k._link_rate(
                    "downlink", t, sat, ep=ep)
                rate_recover_fn = lambda t, lim, sat=self.sat, ep=ep: (
                    k.geometry.next_slant_range_under(
                        sat, ep.lat, ep.lon, k.rate_max_downlink_km, t, lim))
                dur = None
            else:
                rate_fn = rate_recover_fn = None
                dur = pkt.bits / k.dl_rate_bps
            self._service_rate_bps = (None if rate is not None
                                      else k.dl_rate_bps)
            self._svc = (k.env.now, "gsl_downlink_s")
            self._svc_phase = "waiting_for_link"
            self._tx_started_at = None
            outcome = yield k.env.process(
                k._transmit(dur, pkt, ("gsl", self.sat, ep, ep.links.get(self.sat)),
                            "gsl_downlink_s", owner=self, rate_fn=rate_fn,
                            rate_recover_fn=rate_recover_fn))
            self._svc = None
            self._svc_phase = None
            self._tx_started_at = None
            self.current = None
            if outcome == "retired":
                # partial downlink never reached the endpoint: re-decide at
                # this satellite (the destination holds a new association).
                k._hold_packet(self.sat, pkt)
                k._on_link_retired(ep, self.sat)
                continue
            if outcome == "stalled":
                self.queues[cell].appendleft(pkt)
                self.queued_bits += pkt.bits
                self.area.add(pkt.bits, k.env.now)
                k._metric_queue_enter(
                    pkt, "downlink", f"gsl:downlink:{self.sat}:{cell}")
                if k.env.now >= k.horizon:
                    break
                yield self.wake
                self.wake = k.env.event()
                continue
            if outcome != "ok":
                continue
            now = k.env.now
            prop = model.propagation_delay_s(
                k.geometry.slant_range_km(self.sat, ep.lat, ep.lon, now))
            k._metric_propagation_start(
                pkt, "downlink", f"gsl:downlink:{self.sat}:{cell}", prop)
            k._in_flight[pkt.pid] = {
                "kind": "deliver", "sat": self.sat,
                "arrival_at": k.env.now + prop, "pkt": pkt}
            k.env.process(k._deliver_after_prop(pkt, self.sat, prop))


class ISLLink:
    """One directional ISL: ONE finite queue capacity shared by data and
    control, control with non-preemptive priority (queued control overtakes
    queued data when the link next goes idle; a packet in service is never
    interrupted). Availability is re-checked at every use."""

    def __init__(self, kern, sat, direction, peer, gen=0):
        self.k = kern
        self.sat = sat
        self.dir = direction
        self.peer = peer
        # monotone per-satellite-link generation id: rematch ordering.  The
        # one-transceiver gate must wait only for OLDER generations, never
        # for younger retired ones (that would deadlock G0 against G1).
        self.gen = gen
        self.data_q: deque[DataPacket] = deque()
        self.ctrl_q: deque[ControlPacket] = deque()
        self.data_bits = 0
        self.ctrl_bits = 0
        self.data_area = QueueArea()
        self.ctrl_area = QueueArea()
        self.wake = kern.env.event()
        self.current: DataPacket | ControlPacket | None = None
        self._svc = None
        self._svc_phase = None  # None | waiting_for_link | transmitting
        self._tx_started_at = None
        # D2 rematch lifecycle: a replaced link keeps draining its control
        # queue/in-service packet (retired) and signals `drained` once the
        # direction slot is physically free again.
        self.retired = False
        # Set when the retired generation has no queue or service left.  The
        # timestamp lets the physical-capacity ledger include a just-drained
        # generation in the interval that contains its final service, without
        # counting that generation in later intervals.
        self.drained_at: float | None = None
        self.drained = kern.env.event()
        ge_cfg = kern.cfg_links["ge_isl"]
        self.ge = outage.GilbertElliott(
            ge_cfg["mean_good_s"], ge_cfg["mean_bad_s"],
            rngmod.link_stream(kern.cfg_sc["seed"], f"isl:{sat}:{direction}"),
            enabled=kern.ge_enabled)
        kern.env.process(self._run())

    def _used(self) -> int:
        return self.data_bits + self.ctrl_bits

    def room(self, bits: int) -> bool:
        return self._used() + bits <= self.k.cfg_links["isl_queue_bits"]

    def available_now(self) -> bool:
        k = self.k
        if k.ge_enabled:
            # every GE state query is a real channel read; count it so a
            # deferral caused by the outage shows up in receipt effective.ge
            k.mech["ge_isl_queries"] += 1
            if self.ge.is_down(k.env.now):
                return False
        return k.geometry.isl_available(self.sat, self.peer, k.env.now)

    def put_data(self, pkt: DataPacket) -> None:
        pkt.isl_enqueued_at = self.k.env.now
        self.data_q.append(pkt)
        self.data_bits += pkt.bits
        self.data_area.add(pkt.bits, self.k.env.now)
        self.k._metric_queue_enter(pkt, "isl", f"isl:{self.sat}:{self.peer}",
                                   decision_id=pkt.decision_id)
        self.k._poke(self.wake)

    def put_ctrl(self, pkt: ControlPacket) -> None:
        self.ctrl_q.append(pkt)
        self.ctrl_bits += pkt.bits
        self.ctrl_area.add(pkt.bits, self.k.env.now)
        self.k._poke(self.wake)

    def _is_drained(self) -> bool:
        """Nothing left to serve: queues empty and no transmission active."""
        return (not self.data_q and not self.ctrl_q and self.current is None)

    def _run(self):
        k = self.k
        while True:
            self._expire_waiting()
            if not self.ctrl_q and not self.data_q:
                if self.retired:
                    # fully drained: release the (sat, direction) slot and
                    # end this server process — the link object stays alive
                    # only for stop-time accounting
                    if not self.drained.triggered:
                        self.drained.succeed()
                    # Reclaim immediately so a later rematch sees the
                    # released slot in its entity-cap accounting even when
                    # no successor generation is waiting on this slot.
                    k._purge_drained_retired()
                    return
                yield self.wake
                self.wake = k.env.event()
                continue
            busy = [l for l in k._retired_isls
                    if l.sat == self.sat and l.dir == self.dir
                    and l.gen < self.gen and not l._is_drained()]
            if busy:
                # ONE transceiver per (sat, direction): every generation —
                # retired or not — must wait for all OLDER generations on
                # the same physical slot to drain before it transmits its
                # own queued packets.  Without this, a second rematch that
                # retires an already-gated successor lets that successor
                # skip the gate (it now sees retired=True) and transmit
                # concurrently with an even older generation still in
                # service; gating on `not self.retired` alone would also
                # deadlock two retired generations waiting on each other,
                # so the wait list is ordered by generation id.
                wait_ev = (simpy.events.AllOf(k.env, [l.drained for l in busy])
                           | self.wake)
                # our own queued packets still expire at their own
                # deadlines while we wait for the slot
                expiries = [p.generated_at + p.ttl_s for p in self.ctrl_q]
                expiries.extend(p.deadline for p in self.data_q
                                if p.deadline is not None)
                waits = [u for u in expiries
                         if k.env.now < u <= k.horizon]
                if waits:
                    wait_ev = wait_ev | k.env.timeout(
                        min(waits) - k.env.now)
                yield wait_ev
                if self.wake.triggered:
                    self.wake = k.env.event()
                k._purge_drained_retired()
                continue

            if not self.available_now():
                # link down right now: wait for the earliest recovery
                ups = [k.geometry.next_isl_change(self.sat, self.peer,
                                                 k.env.now, k.horizon)]
                if k.ge_enabled:
                    ups.append(self.ge.next_up(k.env.now))
                ups = [u for u in ups if u is not None]
                expiries = [p.generated_at + p.ttl_s for p in self.ctrl_q]
                expiries.extend(p.deadline for p in self.data_q
                                if p.deadline is not None)
                waits = [u for u in ups + expiries
                         if k.env.now < u <= k.horizon]
                if not waits:
                    yield self.wake
                    self.wake = k.env.event()
                    continue
                timeout = k.env.timeout(max(0.0, min(waits) - k.env.now))
                yield timeout | self.wake
                if self.wake.triggered:
                    self.wake = k.env.event()
                continue
            is_ctrl = bool(self.ctrl_q)
            if k.rate_model == "mcs":
                rate = k._link_rate("isl", k.env.now, self.sat,
                                    peer=self.peer)
                if rate <= 0:
                    k.mech["mcs_zero_rate_holds"] += 1
                    next_rate = k.geometry.next_isl_range_under(
                        self.sat, self.peer, k.rate_max_isl_km,
                        k.env.now, k.horizon)
                    waits = []
                    if (next_rate is not None
                            and k.env.now < next_rate <= k.horizon):
                        waits.append(next_rate)
                    # every queued packet expires at its OWN deadline/TTL —
                    # not the probe's — and newly enqueued packets wake the
                    # wait so their deadlines join the race immediately
                    waits += [p.generated_at + p.ttl_s for p in self.ctrl_q
                              if k.env.now < p.generated_at + p.ttl_s
                              <= k.horizon]
                    waits += [p.deadline for p in self.data_q
                              if p.deadline is not None
                              and k.env.now < p.deadline <= k.horizon]
                    wait_t = min(waits) if waits else k.horizon
                    delay = wait_t - k.env.now
                    if delay <= 0.0:
                        # at/past the horizon with nothing left to wait for:
                        # sleep until poked — re-arming a zero timeout here
                        # would spin the event loop at the stop time
                        yield self.wake
                        self.wake = k.env.event()
                        continue
                    yield k.env.timeout(delay) | self.wake
                    if self.wake.triggered:
                        self.wake = k.env.event()
                    continue
            pkt = self.ctrl_q.popleft() if is_ctrl else self.data_q.popleft()
            self.current = pkt
            if is_ctrl:
                self.ctrl_bits -= pkt.bits
                self.ctrl_area.remove(pkt.bits, k.env.now)
            else:
                self.data_bits -= pkt.bits
                self.data_area.remove(pkt.bits, k.env.now)
            k.service_log["isl"].append(("ctrl" if is_ctrl else "data",
                                         pkt.iid if is_ctrl else pkt.pid))
            rate = None
            if k.rate_model == "mcs":
                rate = k._link_rate("isl", k.env.now, self.sat,
                                    peer=self.peer)
                rate_fn = lambda t, sat=self.sat, peer=self.peer: (
                    k._link_rate("isl", t, sat, peer=peer))
                rate_recover_fn = lambda t, lim, sat=self.sat, peer=self.peer: (
                    k.geometry.next_isl_range_under(
                        sat, peer, k.rate_max_isl_km, t, lim))
                dur = None
            else:
                rate_fn = rate_recover_fn = None
                dur = pkt.bits / k.isl_rate_bps
            self._service_rate_bps = (None if rate is not None
                                      else k.isl_rate_bps)
            occ = "ctrl_isl_s" if is_ctrl else "isl_s"
            self._svc = (k.env.now, occ)
            self._svc_phase = "waiting_for_link"
            self._tx_started_at = None
            outcome = yield k.env.process(
                k._transmit(dur, pkt, ("isl", self.sat, self.peer, self.ge),
                            occ, owner=self, rate_fn=rate_fn,
                            rate_recover_fn=rate_recover_fn))
            self._svc = None
            self._svc_phase = None
            self._tx_started_at = None
            self.current = None
            if outcome == "stalled":
                if is_ctrl:
                    self.ctrl_q.appendleft(pkt)
                    self.ctrl_bits += pkt.bits
                    self.ctrl_area.add(pkt.bits, k.env.now)
                else:
                    self.data_q.appendleft(pkt)
                    self.data_bits += pkt.bits
                    self.data_area.add(pkt.bits, k.env.now)
                    self.k._metric_queue_enter(
                        pkt, "isl", f"isl:{self.sat}:{self.peer}")
                if k.env.now >= k.horizon:
                    break
                yield self.wake
                self.wake = k.env.event()
                continue
            if outcome != "ok":
                continue
            if is_ctrl:
                k.mech["control_tx_completed"] += 1
            now = k.env.now
            prop = model.propagation_delay_s(
                k.geometry.isl_range_km(self.sat, self.peer, now))
            if is_ctrl:
                k.env.process(k._ctrl_arrive_after_prop(pkt, self.sat, self.peer, prop))
            else:
                k._metric_propagation_start(
                    pkt, "isl", f"isl:{self.sat}:{self.peer}", prop)
                k._in_flight[pkt.pid] = {
                    "kind": "isl", "sat": self.peer,
                    "arrival_at": k.env.now + prop, "pkt": pkt}
                k.env.process(k._isl_arrive_after_prop(pkt, self.peer, prop))

    def _expire_waiting(self) -> None:
        """Retire queued packets at their own deadline/TTL even while an ISL
        is down forever.  Queue residence is part of the information/link
        model and cannot silently turn an expired packet into IN_SYSTEM."""
        now = self.k.env.now
        kept_ctrl = deque()
        for pkt in self.ctrl_q:
            if now >= pkt.generated_at + pkt.ttl_s:
                self.ctrl_bits -= pkt.bits
                self.ctrl_area.remove(pkt.bits, now)
                self.k._fail(pkt, "CONTROL_EXPIRED")
            else:
                kept_ctrl.append(pkt)
        self.ctrl_q = kept_ctrl
        kept_data = deque()
        for pkt in self.data_q:
            if pkt.deadline is not None and now >= pkt.deadline:
                self.data_bits -= pkt.bits
                self.data_area.remove(pkt.bits, now)
                self.k._fail(pkt, "DATA_DEADLINE_EXPIRED")
            else:
                kept_data.append(pkt)
        self.data_q = kept_data


class Kernel:
    def __init__(self, resolved: dict, rows: list[dict], geometry=None,
                 learning_out_dir=None, decision_sink=None, timeline_sink=None,
                 forced_actions=None):
        cfg = resolved["config"]
        self.resolved = resolved
        self.cfg_sc = cfg["scenario"]
        self.cfg_access = cfg["access"]
        self.cfg_links = cfg["links"]
        self.cfg_topo = cfg["topology"]
        self.cfg_cp = cfg["control_plane"]
        self.cfg_rt = cfg["routing"]
        self.cfg_learning = cfg["learning"]
        self.cfg_ex = cfg["execution"]
        self.horizon = float(self.cfg_sc["duration_s"])
        self.time_step = float(self.cfg_sc["time_step_s"])
        self.env = simpy.Environment()
        self.learning_gate(cfg)
        self.learning_out_dir = learning_out_dir
        algorithm = cfg["learning"]["algorithm"]
        if algorithm == "ddqn":
            self.learner = _learning.TensorflowDDQN(
                self.cfg_rt["contract"], cfg["learning"],
                cfg["learning"]["seed"]
                if cfg["learning"]["seed"] is not None
                else self.cfg_sc["seed"])
        elif algorithm == "qlearning":
            self.learner = _learning.TabularQLearning(
                self.cfg_rt["contract"], cfg["learning"],
                cfg["learning"]["seed"]
                if cfg["learning"]["seed"] is not None
                else self.cfg_sc["seed"])
        else:
            self.learner = None

        if geometry is None:
            geometry = model.Constellation(
                self.cfg_sc["num_satellites"], self.cfg_sc["num_planes"],
                self.cfg_sc["altitude_km"], self.cfg_sc["inclination_deg"],
                self.cfg_sc["min_elevation_deg"],
                max_isl_km=self.cfg_links["max_isl_km"],
                geometry_epoch_s=self.cfg_sc["geometry_epoch_s"])
        if self.cfg_links["geometry_loss"] and not getattr(
                geometry, "certifies_change_times", False):
            # a provider that cannot authoritatively answer "next availability
            # change" would force the kernel to guess link continuity inside
            # service intervals; fail closed instead.
            raise KernelError(
                "geometry provider does not certify next-change times; "
                "failing closed (set links.geometry_loss=false only for "
                "diagnostic runs)")
        # exact-argument memoization wrapper: transparent and bit-equivalent
        # (cached values are the first-computed results for identical pure
        # queries), bounded LRU, filled only on demand — never reads future
        # times itself.
        self.geometry = model.MemoizedGeometry(geometry)
        self.num_sats = geometry.num_satellites
        # optional output-only per-hop decision snapshot sink (a list); when
        # None the recording code paths are never entered
        self.decision_sink = decision_sink
        # optional output-only decision lifecycle stream (a list of dicts).
        # Deliberately a SEPARATE sink: the per-hop decision rows are asserted
        # by shape and count in existing tests, so lifecycle milestones must
        # never be appended to decision_sink.  When None no row is built.
        self.timeline_sink = timeline_sink
        # per-decision identity; allocated only when a sink can record it
        self._decision_seq = 0
        # T1-COUNTERFACTUAL-REPLAY: strictly opt-in override of the action
        # taken at specific decisions, keyed by decision_id.  Empty by
        # default, so normal runs never consult it.
        self.forced_actions = dict(forced_actions or {})
        self._forced_applied: set[int] = set()
        # T1-COMPUTE-DELAY: simulated seconds one routing decision takes to
        # compute.  0 (the default) keeps every historical run exactly as it
        # was: _decide stays a plain synchronous call with no yield at all.
        self.compute_delay_s = float(self.cfg_ex["compute_delay_s"])
        # T1-COMPUTE-DELAY observation semantics (protocol doc 4.1/4.4):
        #   "refresh" (default) -- the deferred decision re-reads env.now and
        #       the live state when it lands.  That is a *delayed re-observation*
        #       decision: it CANNOT represent a state that went stale during the
        #       computation, because the staleness is erased by the re-read.
        #   "frozen" -- the observation is taken at decision start, the action
        #       is inferred from that observation, and commit time only checks
        #       whether the inferred action is still LEGAL.  A rejected action
        #       is recorded and the packet is parked; it is never silently
        #       replaced by a freshly solved optimum.
        # The first version of "frozen" is deliberately restricted: a learning
        # arm needs its own observation contract, and the counterfactual
        # harness forces actions at the branch point, which frozen moves to the
        # observation instant.
        self.obs_mode = str(self.cfg_ex["decision_observation_mode"])
        if self.obs_mode not in ("refresh", "frozen"):
            raise KernelError(
                f"unknown execution.decision_observation_mode {self.obs_mode!r}")
        if self.obs_mode == "frozen" and self.learner is not None:
            raise KernelError(
                "execution.decision_observation_mode=frozen is not supported "
                "together with a learning arm in the first version")
        if self.obs_mode == "frozen" and self.forced_actions:
            raise KernelError(
                "execution.decision_observation_mode=frozen cannot be combined "
                "with forced_actions: the counterfactual harness forces at the "
                "branch point, and frozen moves the branch point to the "
                "observation instant")

        # F2 (node processing / scheduling cost).  A satellite visit costs the
        # node the configured receive/process/schedule time BEFORE the packet
        # is handed to the forwarding function, so this stage ends exactly
        # where the decision stage begins: F2 and compute_delay_s are disjoint
        # intervals and can never be mistaken for each other.  It is recorded
        # on the timeline sink only (see _node_process) because the four frozen
        # packet-event stages cannot carry it without becoming unattributable,
        # and extending the closed kind whitelist in metrics.py is a contract
        # change owned by a separate task.
        self.node_process_delay_s = float(self.cfg_ex["node_process_delay_s"])
        if self.node_process_delay_s > 0 and self.timeline_sink is None:
            # Fail loud: node processing time that no stream can attribute is
            # exactly the "time hidden inside an existing stage" this mechanism
            # exists to avoid (AGENTS.md hard fact 4).
            raise KernelError(
                "execution.node_process_delay_s > 0 requires a timeline_sink: "
                "F2 node processing time must be attributable, never added "
                "silently")

        self.ge_enabled = bool(self.cfg_links["ge_enabled"])

        self.ul_rate_bps = self.cfg_access["uplink_rate_mbps"] * 1e6
        self.dl_rate_bps = self.cfg_access["downlink_rate_mbps"] * 1e6
        self.isl_rate_bps = self.cfg_links["isl_rate_mbps"] * 1e6
        # D1: distance-dependent MCS rates (rate_model=constant keeps the
        # historical fixed-rate behavior exactly; rate_model=mcs samples the
        # legacy RF params once at service start).
        self.rate_model = self.cfg_links["rate_model"]
        self.mcs_table = self.cfg_links["mcs_table"]
        if self.rate_model == "mcs":
            self.rf_isl = link_budget.RFParams.from_mapping(
                self.cfg_links["rf_isl"])
            self.rf_uplink = link_budget.RFParams.from_mapping(
                self.cfg_links["rf_uplink"])
            self.rf_downlink = link_budget.RFParams.from_mapping(
                self.cfg_links["rf_downlink"])
            self.rate_max_isl_km = link_budget.max_rate_range_km(
                self.rf_isl, self.mcs_table)
            self.rate_max_uplink_km = link_budget.max_rate_range_km(
                self.rf_uplink, self.mcs_table)
            self.rate_max_downlink_km = link_budget.max_rate_range_km(
                self.rf_downlink, self.mcs_table)
        else:
            # Constant-rate mode never consumes RF/MCS configuration.  Keep
            # explicit sentinels so accidental future use fails at the use
            # site instead of silently deriving inactive physical semantics.
            self.rf_isl = self.rf_uplink = self.rf_downlink = None
            self.rate_max_isl_km = None
            self.rate_max_uplink_km = None
            self.rate_max_downlink_km = None

        # Sparse activation: endpoints are created lazily the first time a
        # cell becomes active (first emission from it, or first packet routed
        # to it).  Pre-building every trace cell at t=0 would leak the full
        # future trace into the present: future endpoints pre-position K
        # slots, appear in control advertisements (visible/serve cells) and
        # inflate observation denominators before any demand exists.
        rows = sorted(rows, key=lambda r: (r["emit_time_s"], r["packet_id"]))
        tracemod.validate_packet_rows(
            rows, horizon_s=self.horizon,
            max_packets=self.cfg_ex["max_packets"])
        self.endpoints: dict[str, TrafficEndpoint] = {}
        per_ep_rows: dict[str, list[dict]] = {}
        for r in rows:
            per_ep_rows.setdefault(r["src_grid_id"], []).append(r)
        # Measurement-only endpoint universe.  These coordinates are kept
        # separate from lazy TrafficEndpoint activation so the physical
        # capacity denominator cannot depend on whether a demand packet has
        # arrived yet (and cannot leak that universe into routing/learning).
        self._metric_endpoint_specs = {
            cell: gridmod.grid_center(cell)
            for r in rows
            for cell in (r["src_grid_id"], r["dst_grid_id"])
        }

        self.topo = routing.build_topology(
            self.geometry, self.num_sats, self.cfg_links["isl_dirs"],
            t=0.0 if self.cfg_topo["recompute_interval_s"] is not None else None)
        self._build_routing_structures()

        # per-satellite state
        self.slots: list[set[str]] = [set() for _ in range(self.num_sats)]
        self.caches: list[control.LocalCache] = [control.LocalCache() for _ in range(self.num_sats)]
        self.holding_areas = [QueueArea() for _ in range(self.num_sats)]
        self.pending: list[SatelliteHoldingQueue] = [
            SatelliteHoldingQueue(
                self.cfg_access["holding_queue_bits"], self.holding_areas[s],
                now_fn=lambda self=self: self.env.now)
            for s in range(self.num_sats)]
        self._pending_wake: list[float | None] = [None] * self.num_sats
        self.seen_ctrl: list[set[tuple[int, int]]] = [set() for _ in range(self.num_sats)]
        self.gsl_ge: dict[tuple[int, str], outage.GilbertElliott] = {}

        # fair access state: per-satellite FIFO wait queues (cell -> request
        # time), endpoint last-activity tracking, and admission/occupancy
        # accounting
        self.access_wait: list[dict[str, float]] = [dict() for _ in range(self.num_sats)]
        self.access_last_busy: dict[str, float] = {}
        self.access_stats = {
            "requests": 0, "grants": 0, "preposition_grants": 0,
            "wait_time_s_total": 0.0, "wait_time_s_max": 0.0,
            "slot_hold_s_total": 0.0, "waiting_at_stop": 0,
            "releases": {},
        }

        n_entities = len(self.endpoints) + self.num_sats
        self.uplinks = [UplinkServer(self, s) for s in range(self.num_sats)]
        self.downlinks = [DownlinkServer(self, s) for s in range(self.num_sats)]
        self.isls: list[dict[str, ISLLink]] = [{} for _ in range(self.num_sats)]
        self._retired_isls: list[ISLLink] = []
        # drained generations are kept for stop-time area/occupied accounting
        # only — no queue, no process, no live state
        self._retired_isls_done: list[ISLLink] = []
        self._isl_gen_seq = 0
        for s in range(self.num_sats):
            for d, n in self.topo[s].items():
                self.isls[s][d] = ISLLink(
                    self, s, d, n, gen=self._isl_gen_seq)
                self._isl_gen_seq += 1
                n_entities += 1
        if n_entities > self.cfg_ex["max_entities"]:
            raise CapExceeded(f"entities {n_entities} > max_entities")
        self._n_entities_base = n_entities
        self._isl_dyn_created = 0
        self._isl_dyn_drained = 0
        # base entity count (sat servers + ISL links) for the lazy-endpoint
        # cap check; endpoints created at runtime must respect the same bound
        self._entity_base = n_entities

        self.ledger = fates.DataFateLedger()
        self.ctrl_ledger = fates.ControlFateLedger()
        self.deliveries: dict[int, dict] = {}
        self.occupied = {"gsl_uplink_s": 0.0, "gsl_downlink_s": 0.0,
                         "isl_s": 0.0, "ctrl_isl_s": 0.0}
        self.service_log = {"uplink": [], "downlink": [], "isl": [],
                            "uplink_bits": []}
        # Raw, bounded-by-packet event evidence for congestion metrics.  The
        # metrics module recomputes queue/tx/propagation values from these
        # records instead of trusting a pre-aggregated counter.
        self.packet_events: list[dict] = []
        self.link_service_windows: list[dict] = []
        self.link_available_windows: list[dict] = []
        self._metric_queue_seq = 0
        self._metric_prop_seq = 0
        self.handover_events: list[dict] = []
        self.monitor_log: list[tuple] = []
        self.q0_plan_audit: list[dict] = []
        self.monitor = bool(self.cfg_ex["monitor"])
        self.data_packet_count = 0
        # Q0 readiness: monotonic state version (bumped once per event step)
        # and the set of data packets currently propagating between nodes
        # (scheduled timeout arrivals), so a global snapshot can include the
        # in-flight component of the network state.
        self._state_version = 0
        self._in_flight: dict[int, dict] = {}
        self.ctrl_seq = 0
        self.ctrl_iid = 0
        self.mech = {
            "ge_gsl_queries": 0, "ge_isl_queries": 0,
            "ge_waits": 0, "ge_failures": 0,
            "control_snapshots": 0,
            "control_registered": 0,
            "control_entered_queue": 0,
            "control_tx_started": 0,
            "control_tx_completed": 0,
            "control_initialized": bool(self.cfg_cp["enabled"]),
            "ge_initialized": self.ge_enabled,
            "mbb_events": 0,
            "learning_initialized": self.learner is not None,
            "learning_decisions": 0,
            "learning_transitions": 0,
            "learning_train_steps": 0,
            "learning_discarded_at_stop": 0,
            "learning_discarded_at_rematch": 0,
            "holding_queue_overflows": 0,
            "topo_recomputes": 0,
            # D2 receipt truthfulness: True when the dynamic t=0 matching
            # already differed from the static topology, even if no interval
            # recompute happened before the horizon.
            "topo_dynamic_init": False,
            "mcs_rate_samples": 0,
            # D1 attribution: zero-rate holds count every deferral event
            # where the MCS gate (not geometry/GE) delayed a scheduling or
            # decision step; min/max are the sampled transmission-start
            # rates in integer bps (0 when no sample exists).
            "mcs_zero_rate_holds": 0,
            "mcs_rate_min_bps": 0,
            "mcs_rate_max_bps": 0,
        }
        if self.cfg_topo["recompute_interval_s"] is not None:
            static_topo = routing.build_topology(
                self.geometry, self.num_sats, self.cfg_links["isl_dirs"])
            self.mech["topo_dynamic_init"] = self.topo != static_topo
        # packets holding an open (not yet closed) learning transition; the
        # horizon close must account for every one of them, never silently
        self._learning_open: set[DataPacket] = set()
        self.closed_at: float | None = None

        # process creation order fixes same-time ordering: control
        # advertisers at t=0 precede emissions at t=0. Endpoint tickers are
        # created by _ensure_endpoint when a cell first becomes active. The
        # horizon closer is created last; events AT the horizon are still
        # processed (closed interval [0, horizon]) and final accounting
        # settles at the exact horizon, never at an incidental last-event
        # time.
        if self.cfg_cp["enabled"] and self.cfg_cp["vis_k"] > 0:
            for s in range(self.num_sats):
                self.env.process(self._control_advertiser(s))
        for cell in sorted(per_ep_rows):
            self.env.process(self._emitter(cell, per_ep_rows[cell]))
        if self.cfg_topo["recompute_interval_s"] is not None:
            self.env.process(self._topology_ticker())
        for s in range(self.num_sats):
            self.env.process(self._pending_ticker(s))
        self.env.process(self._horizon_closer())

    def _live_entity_count(self) -> int:
        """Total live entities for the max_entities fail-closed gate.

        Base entities (satellite servers + initial ISL links) plus lazy
        endpoints plus live dynamic ISL generations.  Every runtime creator
        — lazy endpoint activation and dynamic topology rematch — must use
        this single formula, or the two can each pass their own check while
        the combined live count exceeds the configured cap.
        """
        return (self._entity_base + len(self.endpoints)
                + self._isl_dyn_created - self._isl_dyn_drained)

    def _ensure_endpoint(self, cell: str) -> TrafficEndpoint:
        """Create a trace cell's endpoint the first time it becomes active.

        Future-only endpoints must not pre-exist: they would pre-position K
        slots, appear in control advertisements and inflate observation
        denominators before any demand exists (causal leak from the full
        trace into the present).
        """
        ep = self.endpoints.get(cell)
        if ep is not None:
            return ep
        if self._live_entity_count() >= self.cfg_ex["max_entities"]:
            raise CapExceeded(
                f"entities {self._live_entity_count() + 1} > max_entities")
        ep = TrafficEndpoint(cell)
        self.endpoints[cell] = ep
        self.env.process(self._endpoint_ticker(ep))
        return ep

    # ------------------------------------------------------------------ util
    @staticmethod
    def learning_gate(cfg):
        requested = (cfg["routing"]["learning_enabled"]
                     or cfg["learning"]["algorithm"] != "none")
        # tabular Q-learning is pure numpy; only the DDQN arm needs TF
        if requested and cfg["learning"]["algorithm"] == "ddqn":
            _learning.require_tensorflow()

    def _poke(self, event):
        if not event.triggered:
            event.succeed()

    def _link_rate(self, kind: str, t: float, sat: int,
                   peer: int | None = None, ep=None) -> float:
        """Sample the service rate for kind in {isl,uplink,downlink} at t."""
        if self.rate_model == "constant":
            if kind == "isl":
                return self.isl_rate_bps
            if kind == "uplink":
                return self.ul_rate_bps
            if kind == "downlink":
                return self.dl_rate_bps
            raise ValueError(f"unknown link kind {kind!r}")
        if kind == "isl":
            return link_budget.mcs_rate_bps(
                self.geometry.isl_range_km(sat, peer, t),
                self.rf_isl, self.mcs_table)
        if kind not in ("uplink", "downlink"):
            raise ValueError(f"unknown link kind {kind!r}")
        rf = self.rf_uplink if kind == "uplink" else self.rf_downlink
        return link_budget.mcs_rate_bps(
            self.geometry.slant_range_km(sat, ep.lat, ep.lon, t),
            rf, self.mcs_table)

    def _gsl_wait_until(self, sat: int, kind: str,
                        heads) -> tuple[float | None, bool]:
        """Earliest certified time a currently unservable GSL head on `sat`
        may become servable again or expire (D1 precise wait).

        Replaces pure time_step polling for MCS-gated GSL service: geometry
        recovery, GE recovery, rate recovery and packet deadlines all have
        certified times from the geometry/outage contracts, so the server
        wakes at the exact event instead of the next blind tick.  Returns
        (None, ...) when no finite certified event exists before the horizon;
        the second element reports whether any head is currently held by a
        zero MCS rate (receipt attribution).
        """
        now = self.env.now
        rate_max = (self.rate_max_uplink_km if kind == "uplink"
                    else self.rate_max_downlink_km)
        ups = []
        saw_zero_rate = False
        for ep, pkt in heads:
            link = ep.links.get(sat)
            if link is None or link.state not in ("active", "retiring"):
                continue
            geom_up = self.geometry.gsl_available(sat, ep.lat, ep.lon, now)
            if not geom_up:
                nxt = self.geometry.next_gsl_change(sat, ep.lat, ep.lon,
                                                    now, self.horizon)
                if nxt is not None:
                    ups.append(nxt)
            ge = self._gsl_ge(sat, ep.cell)
            ge_up = not self.ge_enabled or not ge.is_down(now)
            if self.ge_enabled and ge.is_down(now):
                nxt_up = ge.next_up(now)
                if nxt_up <= self.horizon:
                    ups.append(nxt_up)
            # D1 F4 attribution: a zero MCS rate only counts as an MCS hold
            # when geometry and GE are NOT the actual blocking gates; if the
            # head is already blocked by geometry/GE, the deferral belongs to
            # that mechanism, not to MCS.
            if (geom_up and ge_up
                    and self._link_rate(kind, now, sat, ep=ep) <= 0):
                saw_zero_rate = True
                nxt = self.geometry.next_slant_range_under(
                    sat, ep.lat, ep.lon, rate_max, now, self.horizon)
                if nxt is not None:
                    ups.append(nxt)
            if pkt.deadline is not None:
                ups.append(pkt.deadline)
        ups = [u for u in ups if now < u <= self.horizon]
        return (min(ups) if ups else None), saw_zero_rate

    def _note_busy(self, cell: str):
        """Last-activity stamp for fair-access idle measurement."""
        self.access_last_busy[cell] = self.env.now

    def _hold_packet(self, sat: int, pkt: DataPacket,
                     decision_id: int | None = None) -> bool:
        """Admit a packet to finite satellite holding, or assign its fate.

        ``decision_id``, when given, is the id of the decision attempt that
        is parking the packet: a hold IS a decision (it decided not to
        forward now), so the id must be accounted for or the id space leaks
        silently (R8-A7).
        """
        if self.pending[sat].put(pkt, self.env.now):
            self._metric_queue_enter(pkt, "holding", f"holding:{sat}",
                                     decision_id=decision_id)
            if self.timeline_sink is not None and decision_id is not None:
                self._timeline("hold", pkt, decision_id, sat=int(sat),
                               obs_mode=self.obs_mode)
            self._note_busy(pkt.dst)
            return True
        self.mech["holding_queue_overflows"] += 1
        self._fail(pkt, "HOLDING_QUEUE_OVERFLOW", decision_id=decision_id)
        return False

    def _log(self, kind, **kv):
        if self.monitor:
            self.monitor_log.append((self.env.now, kind, tuple(sorted(kv.items()))))

    # -------------------------------------------------- decision ledger
    def _next_decision_id(self) -> int | None:
        """Allocate one per-decision identity, or None when nothing records.

        Output only: the counter never influences routing, learning, timing
        or fates.  With both sinks absent (the default) nothing is allocated
        and the hot path is unchanged.
        """
        if self.decision_sink is None and self.timeline_sink is None:
            return None
        decision_id = self._decision_seq
        self._decision_seq += 1
        return decision_id

    def _timeline(self, milestone: str, pkt: DataPacket,
                  decision_id: int | None, **extra) -> None:
        """Append one decision-lifecycle milestone to the optional sink.

        Output only: never influences routing, learning, timing or fates.
        Callers guard on ``self.timeline_sink is not None`` before calling, so
        this method never runs on the default path.  ``decision_id`` is the
        decision the milestone belongs to; it stays None for traffic that was
        never decided (e.g. pre-ingress uplink service).  ``extra`` may carry
        ``at`` to override the environment clock (used where a window end is
        authoritative).
        """
        row = {
            "milestone": milestone,
            "at": float(self.env.now),
            "pid": pkt.pid,
            "decision_id": decision_id,
        }
        row.update(extra)
        self.timeline_sink.append(row)

    # ------------------------------------------------------- raw metrics
    def _metric_packet_emitted(self, pkt: DataPacket) -> None:
        self.packet_events.append({
            "kind": "packet_emitted", "pid": pkt.pid,
            "at": float(pkt.emitted_at), "bits": pkt.bits,
        })

    def _metric_queue_enter(self, pkt: DataPacket, queue: str,
                            link_id: str,
                            decision_id: int | None = None) -> None:
        """Record one enqueue into a physical queue.

        ``decision_id`` is the decision that CAUSED this enqueue and must be
        passed explicitly.  Attributing by ``pkt.decision_id`` is wrong for
        every enqueue that is not the direct consequence of the packet's last
        committed decision (R8-A8): a hold that follows a commit would be
        credited to the previous decision.  Requeues caused by a link stall
        or retirement therefore pass None, the ISL and downlink enqueues a
        commit performs pass the committed id, and the holding enqueue passes
        the id of the holding attempt.
        """
        qid = self._metric_queue_seq
        self._metric_queue_seq += 1
        pkt.metric_queue_id = qid
        self.packet_events.append({
            "kind": "queue_enter", "pid": pkt.pid,
            "at": float(self.env.now), "queue": queue,
            "link_id": link_id, "queue_id": qid,
        })
        if self.timeline_sink is not None:
            self._timeline("queue_enter", pkt, decision_id,
                           queue=queue, link_id=link_id)

    def _metric_link_id(self, link_ref, occ_key: str) -> tuple[str, str]:
        if link_ref[0] == "isl":
            _, sat, peer, _ge = link_ref
            return "isl", f"isl:{sat}:{peer}"
        _, sat, ep, _link = link_ref
        stage = "uplink" if occ_key == "gsl_uplink_s" else "downlink"
        return stage, f"gsl:{stage}:{sat}:{ep.cell}"

    def _metric_service_start(self, pkt: DataPacket, stage: str,
                               link_id: str, rate_bps: float,
                               bits: int) -> None:
        qid = pkt.metric_queue_id
        self.packet_events.append({
            "kind": "service_start", "pid": pkt.pid,
            "at": float(self.env.now), "stage": stage,
            "link_id": link_id, "queue_id": qid,
            "bits": bits, "rate_bps": float(rate_bps),
        })
        pkt.metric_queue_id = None
        if self.timeline_sink is not None:
            self._timeline("service_start", pkt, pkt.decision_id,
                           stage=stage, link_id=link_id)

    def _metric_service_window(self, pkt: DataPacket, stage: str,
                               link_id: str, start: float, end: float,
                               rate_bps: float, outcome: str) -> None:
        duration = max(0.0, float(end) - float(start))
        self.link_service_windows.append({
            "pid": pkt.pid, "stage": stage, "link_id": link_id,
            "start": float(start), "end": float(end),
            "rate_bps": float(rate_bps),
            "capacity_bits": float(rate_bps) * duration,
            "served_bits": int(pkt.bits) if outcome == "ok" else 0,
            "bits": int(pkt.bits), "outcome": outcome,
        })
        if self.timeline_sink is not None:
            self._timeline("service_finish", pkt, pkt.decision_id,
                           stage=stage, link_id=link_id, outcome=outcome,
                           at=float(end))

    def _metric_propagation_start(self, pkt: DataPacket, stage: str,
                                  link_id: str, delay_s: float) -> None:
        prop_id = self._metric_prop_seq
        self._metric_prop_seq += 1
        pkt.metric_prop_id = prop_id
        self.packet_events.append({
            "kind": "propagation_start", "pid": pkt.pid,
            "at": float(self.env.now), "stage": stage,
            "link_id": link_id, "prop_id": prop_id,
            "delay_s": float(delay_s),
        })

    def _metric_propagation_arrival(self, pkt: DataPacket,
                                    sat: int | None = None) -> None:
        prop_id = pkt.metric_prop_id
        if prop_id is None:
            raise KernelError(f"packet {pkt.pid} propagation arrived without start")
        self.packet_events.append({
            "kind": "propagation_arrival", "pid": pkt.pid,
            "at": float(self.env.now), "prop_id": prop_id,
        })
        pkt.metric_prop_id = None
        if self.timeline_sink is not None:
            extra = {} if sat is None else {
                "sat": int(sat),
                # realized ground truth for scoring the decision-time
                # downstream prediction (T1-DOWNSTREAM-RESOURCE-PASS).
                # Attached only for ISL arrivals, where the peer satellite
                # really does own ISL egresses to contend for.
                "egress_snapshot": self._egress_snapshot(sat),
            }
            self._timeline("peer_arrival", pkt, pkt.decision_id, **extra)

    def _metric_satellite_ingress(self, pkt: DataPacket, sat: int) -> None:
        """Record the one physical uplink admission boundary exactly once."""
        if pkt.metric_ingress_at is not None:
            raise KernelError(f"packet {pkt.pid} duplicate satellite ingress")
        pkt.metric_ingress_at = float(self.env.now)
        self.packet_events.append({
            "kind": "satellite_ingress", "pid": pkt.pid,
            "at": float(self.env.now), "endpoint": pkt.src,
            "satellite": int(sat), "bits": int(pkt.bits),
        })
        if self.timeline_sink is not None:
            self._timeline("satellite_ingress", pkt, pkt.decision_id,
                           satellite=int(sat))

    def _metric_delivered(self, pkt: DataPacket) -> None:
        self.packet_events.append({
            "kind": "delivered", "pid": pkt.pid,
            "at": float(self.env.now),
        })

    def _build_routing_structures(self):
        self._routing_reverse_adj = routing._reverse_adj(self.topo)
        self._routing_sorted_rev_adj = {
            s: sorted(self._routing_reverse_adj.get(s, ()))
            for s in self._routing_reverse_adj}
        self.control_children = [
            routing.control_broadcast_children(
                self.topo, origin, self.cfg_cp["vis_k"])
            for origin in range(self.num_sats)
        ] if self.cfg_cp["enabled"] and self.cfg_cp["vis_k"] > 0 else []

    def _all_isls(self):
        """Current plus retired links, including queues draining after rematch."""
        return [link for links in self.isls for link in links.values()] + list(
            self._retired_isls) + list(self._retired_isls_done)

    def _purge_drained_retired(self):
        """Reclaim fully drained retired generations (M5).

        A drained link has no queue, no in-service packet and (once its
        server notices) no process; it moves to the accounting-only list so
        long horizons cannot accumulate live entities without bound.
        """
        still = []
        for link in self._retired_isls:
            if link._is_drained():
                if not link.drained.triggered:
                    link.drained.succeed()
                link.drained_at = float(self.env.now)
                self._isl_dyn_drained += 1
                self._retired_isls_done.append(link)
            else:
                still.append(link)
        self._retired_isls = still

    def _topology_ticker(self):
        interval = self.cfg_topo["recompute_interval_s"]
        while True:
            yield self.env.timeout(interval)
            if self.env.now >= self.horizon:
                return
            self._recompute_topology(self.env.now)

    def _metric_available_rate(self, stage: str, sat: int, t: float,
                               *, peer: int | None = None,
                               lat: float | None = None,
                               lon: float | None = None) -> float:
        """Return the physical rate used by the availability denominator.

        This is deliberately geometry/rate only.  Queue occupancy, access
        association, and stochastic GE outages are not capacity: they are
        explained by the numerator/fate and queue metrics instead.
        """
        if self.rate_model == "constant":
            if stage == "isl":
                return self.isl_rate_bps
            return self.ul_rate_bps if stage == "uplink" else self.dl_rate_bps
        if stage == "isl":
            return link_budget.mcs_rate_bps(
                self.geometry.isl_range_km(sat, peer, t),
                self.rf_isl, self.mcs_table)
        rf = self.rf_uplink if stage == "uplink" else self.rf_downlink
        return link_budget.mcs_rate_bps(
            self.geometry.slant_range_km(sat, lat, lon, t),
            rf, self.mcs_table)

    def _metric_capacity_segments(self, stage: str, sat: int,
                                  start: float, end: float, *,
                                  peer: int | None = None,
                                  lat: float | None = None,
                                  lon: float | None = None):
        """Yield geometry/rate-stable pieces of one metric interval.

        A midpoint is not sufficient evidence for a moving link: visibility
        can change inside an interval, and an MCS threshold can be crossed
        and crossed back before the next sample.  The geometry provider's
        certified change/root APIs provide deterministic cut points.
        """
        if end <= start:
            return
        if stage == "isl":
            available = lambda t: self.geometry.isl_available(sat, peer, t)
            next_change = lambda a, b: self.geometry.next_isl_change(
                sat, peer, a, b)
            range_at = lambda t: self.geometry.isl_range_km(sat, peer, t)
            next_range_under = lambda threshold, a, b: (
                self.geometry.next_isl_range_under(
                    sat, peer, threshold, a, b))
            rf = self.rf_isl
        else:
            available = lambda t: self.geometry.gsl_available(sat, lat, lon, t)
            next_change = lambda a, b: self.geometry.next_gsl_change(
                sat, lat, lon, a, b)
            range_at = lambda t: self.geometry.slant_range_km(
                sat, lat, lon, t)
            next_range_under = lambda threshold, a, b: (
                self.geometry.next_slant_range_under(
                    sat, lat, lon, threshold, a, b))
            rf = self.rf_uplink if stage == "uplink" else self.rf_downlink

        boundaries = [float(start), float(end)]
        eps = min(1e-9, max((end - start) * 1e-8, 1e-12))

        def add_roots(root_fn):
            cursor = float(start)
            for _ in range(1024):
                if cursor >= end - eps:
                    return
                nxt = root_fn(cursor, end)
                if nxt is None:
                    return
                nxt = float(nxt)
                if not (cursor < nxt <= end):
                    raise KernelError(
                        f"non-monotone geometry root {nxt} from {cursor}")
                boundaries.append(nxt)
                if nxt >= end - eps:
                    return
                cursor = nxt
            raise KernelError("geometry change certification exceeded 1024 roots")

        add_roots(next_change)
        if self.rate_model == "mcs":
            # Only thresholds that could be reached in this interval need a
            # root search.  RANGE_RATE_KM_S is conservative for supported
            # constellation geometry and keeps the opt-in metric affordable.
            probe_times = (start, (start + end) / 2.0,
                           max(start, end - eps))
            ranges = [float(range_at(t)) for t in probe_times]
            if any(not math.isfinite(v) or v <= 0 for v in ranges):
                raise KernelError("non-finite geometry range in capacity metric")
            margin = model.RANGE_RATE_KM_S * (end - start)
            lo, hi = min(ranges) - margin, max(ranges) + margin
            thresholds = link_budget.mcs_rate_threshold_ranges_km(
                rf, self.mcs_table)
            for threshold in thresholds:
                if lo <= threshold <= hi:
                    add_roots(lambda a, b, threshold=threshold:
                              next_range_under(threshold, a, b))

        unique = sorted(set(boundaries))
        for a, b in zip(unique, unique[1:]):
            if b - a <= eps:
                continue
            t = (a + b) / 2.0
            if not available(t):
                continue
            rate = self._metric_available_rate(
                stage, sat, t, peer=peer, lat=lat, lon=lon)
            if rate > 0:
                yield float(a), float(b), float(rate)

    def _record_available_capacity_sample(self, start: float, end: float,
                                           sample_at: float) -> None:
        """Record geometry-segmented fixed-interval capacity quadrature.

        ``sample_at`` remains in this private signature for compatibility with
        early diagnostic probes; certified segment midpoints are authoritative.
        Idle physical links remain in the denominator.
        """
        if end <= start:
            return
        for cell, (lat, lon) in sorted(self._metric_endpoint_specs.items()):
            for sat in range(self.num_sats):
                for stage in ("uplink", "downlink"):
                    for seg_start, seg_end, rate in self._metric_capacity_segments(
                            stage, sat, start, end, lat=lat, lon=lon):
                        self.link_available_windows.append({
                            "stage": stage,
                            "link_id": f"gsl:{stage}:{sat}:{cell}",
                            "start": seg_start,
                            "end": seg_end,
                            "rate_bps": float(rate),
                            "capacity_bits": float(rate) * (seg_end - seg_start),
                        })

        # A retired ISL generation may drain an in-flight packet after a
        # rematch.  Include all generations, deduplicated by physical directed
        # edge, so the independent denominator cannot undercount that service.
        current_edges = {(sat, peer) for sat in range(self.num_sats)
                         for peer in self.topo[sat].values()}
        live_edges = {(link.sat, link.peer) for link in self._retired_isls}
        drained_by_edge: dict[tuple[int, int], list[float]] = {}
        for link in self._retired_isls_done:
            if link.drained_at is not None and link.drained_at > start + 1e-12:
                drained_by_edge.setdefault((link.sat, link.peer), []).append(
                    float(link.drained_at))
        edges = current_edges | live_edges | set(drained_by_edge)
        for sat, peer in sorted(edges):
            # If no current/live generation owns this edge, a drained
            # generation contributes only until its last drain instant.  This
            # prevents a service ending mid-window from inflating the physical
            # capacity denominator through the rest of that window.
            edge_end = end
            if ((sat, peer) not in current_edges
                    and (sat, peer) not in live_edges):
                edge_end = min(end, max(drained_by_edge[(sat, peer)]))
            if edge_end <= start + 1e-12:
                continue
            for seg_start, seg_end, rate in self._metric_capacity_segments(
                    "isl", sat, start, edge_end, peer=peer):
                self.link_available_windows.append({
                    "stage": "isl",
                    "link_id": f"isl:{sat}:{peer}",
                    "start": seg_start,
                    "end": seg_end,
                    "rate_bps": float(rate),
                    "capacity_bits": float(rate) * (seg_end - seg_start),
                })

    def _truncate_drained_capacity_windows(self) -> None:
        """Clip an interval that contained a retired generation's drain.

        The sampler cannot know the future drain instant when it opens a
        window.  At natural stop all retired generations have a definitive
        ``drained_at``; clip the raw evidence before metrics recomputation so
        an old generation is not counted after its final service.
        """
        drains = {
            f"isl:{link.sat}:{link.peer}": float(link.drained_at)
            for link in self._retired_isls_done
            if link.drained_at is not None
        }
        if not drains:
            return
        clipped = []
        for window in self.link_available_windows:
            drain = drains.get(window["link_id"])
            if drain is None or window["start"] >= drain:
                clipped.append(window)
                continue
            end = min(float(window["end"]), drain)
            if end <= float(window["start"]) + 1e-12:
                continue
            item = dict(window)
            item["end"] = end
            item["capacity_bits"] = float(item["rate_bps"]) * (
                end - float(item["start"]))
            clipped.append(item)
        self.link_available_windows = clipped

    def _available_capacity_ticker(self):
        configured = self.cfg_ex["available_capacity_interval_s"]
        if configured is None:
            return
        interval = float(configured)
        topo_interval = self.cfg_topo["recompute_interval_s"]
        start = 0.0
        while start < self.horizon:
            end = min(self.horizon, start + interval)
            # Topology rematching is a real state transition.  A capacity
            # window may not straddle it, otherwise the midpoint can describe
            # the old generation while service in the second half uses the
            # newly installed neighbor.
            if topo_interval is not None:
                next_topo = ((math.floor(start / topo_interval) + 1)
                             * topo_interval)
                if next_topo > start + 1e-12:
                    end = min(end, next_topo)
            sample_at = start + (end - start) / 2.0
            self._record_available_capacity_sample(start, end, sample_at)
            yield self.env.timeout(max(0.0, end - self.env.now))
            start = end

    def _recompute_topology(self, now: float):
        new_topo = routing.build_topology(
            self.geometry, self.num_sats, self.cfg_links["isl_dirs"], t=now)
        old_links = self.isls
        # Reclaim drained generations before the cap check: a direction that
        # was removed may already have fully drained with no successor
        # waiting, and counting it as live here would spuriously fail a
        # later re-add at the cap boundary.
        self._purge_drained_retired()
        planned_new = 0
        for s in range(self.num_sats):
            for direction, peer in new_topo[s].items():
                old = old_links[s].get(direction)
                if old is None or old.peer != peer:
                    planned_new += 1
        live = self._live_entity_count()
        if live + planned_new > self.cfg_ex["max_entities"]:
            raise CapExceeded(
                f"dynamic topology would create {planned_new} ISL links "
                f"({live + planned_new} entities > max_entities "
                f"{self.cfg_ex['max_entities']})")
        for s in range(self.num_sats):
            for link in old_links[s].values():
                if new_topo[s].get(link.dir) == link.peer:
                    continue
                while link.data_q:
                    pkt = link.data_q.popleft()
                    link.data_bits -= pkt.bits
                    link.data_area.remove(pkt.bits, now)
                    if pkt in self._learning_open:
                        # the queued forward action was superseded by the
                        # rematch before any service started; re-deciding it
                        # with a stale open transition would fail loud, and
                        # remembering it would fabricate a next state for an
                        # action that never happened
                        self._learning_open.discard(pkt)
                        pkt.learning_state = None
                        pkt.learning_action = None
                        pkt.learning_reward = None
                        self.mech["learning_discarded_at_rematch"] += 1
                    self._hold_packet(s, pkt)
                link.retired = True
                # wake a sleeping server so a fully drained link releases
                # its direction slot immediately instead of dozing forever
                self._poke(link.wake)
                self._retired_isls.append(link)
            new_links = {}
            for direction, peer in new_topo[s].items():
                old = old_links[s].get(direction)
                if old is not None and old.peer == peer:
                    new_links[direction] = old
                else:
                    new_links[direction] = ISLLink(
                        self, s, direction, peer, gen=self._isl_gen_seq)
                    self._isl_gen_seq += 1
                    self._isl_dyn_created += 1
            self.isls[s] = new_links
        self.topo = new_topo
        self._build_routing_structures()
        self._state_version += 1
        self.mech["topo_recomputes"] += 1
        self._purge_drained_retired()

    def _count_data_packet(self):
        """Enforce the trace/data-packet cap.

        Control instances are bounded by max_events and their finite queues;
        counting them here made a valid, already max_packets-validated trace
        fail merely because the control plane advertised for a long horizon.
        """
        self.data_packet_count += 1
        if self.data_packet_count > self.cfg_ex["max_packets"]:
            raise CapExceeded("max_packets exceeded")

    # ------------------------------------------------------- Q0 snapshot
    def snapshot_global(self) -> dict:
        """Read-only global state snapshot for a centralized Q0 planner.

        Bound to the current simulation time and the monotonic
        ``_state_version`` (bumped once per event step), so a plan computed
        from this snapshot is provably stale after any intervening event.
        All nested containers are freshly constructed per call: the caller
        may not reach into kernel objects through this view.  The snapshot is
        CURRENT-state only: Q0-A (global current information) may use it;
        future information (Q0-B) needs a separate, explicitly labelled
        future view.  Physical constraints remain enforced by the kernel at
        execution time; this interface never writes.

        Read-only holds at the observable-state level: ge.is_down() lazily
        advances each GE's internal trajectory, but GilbertElliott
        trajectories are query-pattern independent, so the snapshot never
        mutates the world state it reports (R6-A3).
        """
        now = self.env.now

        def _drr_state(srv) -> dict:
            return {"deficit": dict(srv.deficit),
                    "rr_cursor": srv.rr_cursor}

        def _svc_state(srv) -> dict | None:
            if srv._svc is None:
                return None
            t0, occ = srv._svc
            return {
                "started_at": t0,
                "occ_key": occ,
                "phase": getattr(srv, "_svc_phase", None),
                "tx_started_at": getattr(srv, "_tx_started_at", None),
                "service_rate_bps": getattr(srv, "_service_rate_bps", None),
            }

        def _packet_info(pkt) -> dict:
            return {
                "src": pkt.src,
                "bits": pkt.bits,
                "deadline": pkt.deadline,
                "dst": pkt.dst,
                "emitted_at": pkt.emitted_at,
                "path": list(pkt.path),
                "assigned_sat": getattr(pkt, "assigned_sat", None),
                "holding_until": getattr(pkt, "holding_until", None),
            }

        def _remaining_service(bits, rate_bps, srv) -> float | None:
            """Phase-aware residual service time.

            waiting_for_link: no bit has been transmitted yet; report the
            full duration (what the server will need once the link is up).
            transmitting: duration - elapsed since the real transmission
            started (never negative by construction).
            """
            if srv._svc is None:
                return None
            sampled = getattr(srv, "_service_rate_bps", None)
            if self.rate_model == "mcs" and sampled is None:
                # No positive MCS sample exists yet.  Falling back to the
                # constant-rate argument would fabricate residual service
                # progress while the server is still waiting for recovery.
                return None
            if sampled is not None:
                rate_bps = sampled
            if rate_bps <= 0:
                return None
            if srv._svc_phase == "waiting_for_link":
                return bits / rate_bps
            if srv._svc_phase == "transmitting" \
                    and srv._tx_started_at is not None:
                return (bits / rate_bps) - (now - srv._tx_started_at)
            return None
        isl_links = {}
        for s in range(self.num_sats):
            isl_links[s] = {}
            for d, lnk in self.isls[s].items():
                in_service = None
                if lnk.current is not None:
                    if isinstance(lnk.current, ControlPacket):
                        in_service = {"iid": lnk.current.iid}
                    else:
                        in_service = {"pid": lnk.current.pid}
                remaining = None
                if lnk._svc is not None and lnk.current is not None:
                    remaining = _remaining_service(
                        lnk.current.bits, self.isl_rate_bps, lnk)
                isl_links[s][d] = {
                    "peer": lnk.peer,
                    "data_bits": lnk.data_bits,
                    "ctrl_bits": lnk.ctrl_bits,
                    "data_q": [{"pid": p.pid, **_packet_info(p)}
                               for p in lnk.data_q],
                    "ctrl_q": [c.iid for c in lnk.ctrl_q],
                    "in_service": in_service,
                    "svc": _svc_state(lnk),
                    "remaining_service_s": remaining,
                    "ge_bad": bool(lnk.ge.is_down(now)) if self.ge_enabled
                    else False,
                    "ge_next_flip": (float(lnk.ge._next_flip)
                                     if self.ge_enabled else math.inf),
                }

        endpoints = {}
        for cell, ep in self.endpoints.items():
            endpoints[cell] = {
                "queue": [{"pid": p.pid, **_packet_info(p)}
                          for p in ep.queue],
                "queued_bits": ep.queued_bits,
                "links": {
                    sat: {"state": lk.state, "since": lk.since,
                          "ready_at": lk.ready_at,
                          "retire_at": lk.retire_at, "cause": lk.cause}
                    for sat, lk in ep.links.items()
                },
            }

        downlinks = {}
        for s in range(self.num_sats):
            dl = self.downlinks[s]
            downlinks[s] = {
                "queues": {cell: [{"pid": p.pid, **_packet_info(p)}
                                  for p in q]
                           for cell, q in dl.queues.items()},
                "queued_bits": dl.queued_bits,
                "in_service": ({"pid": dl.current.pid}
                               if dl.current is not None else None),
                "svc": _svc_state(dl),
                "remaining_service_s": (
                    _remaining_service(dl.current.bits, self.dl_rate_bps, dl)
                    if dl.current is not None else None),
                "drr": _drr_state(dl),
            }

        uplinks = {}
        for s in range(self.num_sats):
            up = self.uplinks[s]
            uplinks[s] = {
                "in_service": ({"pid": up.current[1].pid}
                               if up.current is not None else None),
                "svc": _svc_state(up),
                "remaining_service_s": (
                    _remaining_service(up.current[1].bits, self.ul_rate_bps,
                                       up)
                    if up.current is not None else None),
                "drr": _drr_state(up),
            }

        gsl_ge = {}
        # Universe = every materialized GSL GE pair plus every current
        # endpoint-satellite association.  A pair must never be implied by
        # key absence: un-materialized pairs are explicit with bad=None.
        gsl_pairs = set(self.gsl_ge)
        for cell, ep in self.endpoints.items():
            for sat in ep.links:
                gsl_pairs.add((sat, cell))
        for sat, cell in sorted(gsl_pairs, key=lambda p: (p[0], p[1])):
            ge = self.gsl_ge.get((sat, cell))
            if ge is None:
                gsl_ge[f"{sat}:{cell}"] = {
                    "materialized": False, "bad": None, "next_flip": None,
                }
            else:
                gsl_ge[f"{sat}:{cell}"] = {
                    "materialized": True,
                    "bad": bool(ge.is_down(now)),
                    "next_flip": float(ge._next_flip),
                }

        return {
            "now": now,
            "state_version": self._state_version,
            "topology": {s: dict(nb) for s, nb in self.topo.items()},
            "slots": {s: sorted(v) for s, v in enumerate(self.slots)},
            "access_wait": {
                s: {cell: req_t for cell, req_t in self.access_wait[s].items()}
                for s in range(self.num_sats)},
            "access_last_busy": dict(self.access_last_busy),
            "endpoints": endpoints,
            "pending": {s: [{"pid": p.pid, **_packet_info(p)}
                            for p in self.pending[s]]
                        for s in range(self.num_sats)},
            "holding": {s: {"queued_bits": self.pending[s].queued_bits,
                             "capacity_bits": self.pending[s].capacity_bits}
                         for s in range(self.num_sats)},
            "uplinks": uplinks,
            "downlinks": downlinks,
            "isl_links": isl_links,
            # live draining generations replaced by a rematch: still real
            # network state (queued control, in-service packet) until drained
            "retired_isls": [{
                "sat": lnk.sat,
                "dir": lnk.dir,
                "peer": lnk.peer,
                "data_bits": lnk.data_bits,
                "ctrl_bits": lnk.ctrl_bits,
                "ctrl_q": [c.iid for c in lnk.ctrl_q],
                "in_service": (
                    {"pid": lnk.current.pid}
                    if isinstance(lnk.current, DataPacket)
                    else {"iid": lnk.current.iid}
                    if lnk.current is not None else None),
                "svc": _svc_state(lnk),
            } for lnk in self._retired_isls],
            "gsl_ge": gsl_ge,
            "in_flight": {
                pid: {"pid": pid, "kind": v["kind"], "sat": v["sat"],
                      "arrival_at": v["arrival_at"],
                      **_packet_info(v["pkt"])}
                for pid, v in self._in_flight.items()},
            "caches": {
                s: {origin: {"serve_cells": sorted(entry.payload.get(
                        "serve_cells", ())),
                             "generated_at": entry.generated_at}
                    for origin, entry in self.caches[s].valid_entries(now).items()}
                for s in range(self.num_sats)
            },
        }

    def _gsl_ge(self, sat: int, cell: str) -> outage.GilbertElliott:
        key = (sat, cell)
        ge = self.gsl_ge.get(key)
        if ge is None:
            cfg = self.cfg_links["ge_gsl"]
            ge = outage.GilbertElliott(
                cfg["mean_good_s"], cfg["mean_bad_s"],
                rngmod.link_stream(self.cfg_sc["seed"], f"gsl:{sat}:{cell}"),
                enabled=self.ge_enabled)
            self.gsl_ge[key] = ge
        return ge

    def _data_packet_locations(self) -> dict[int, tuple[str, int | None]]:
        """Read-only index of live data packets for Q0 plan validation."""
        found: dict[int, tuple[str, int | None]] = {}
        for sat, packets in enumerate(self.pending):
            for pkt in packets:
                found[pkt.pid] = ("pending", sat)
        for ep in self.endpoints.values():
            for pkt in ep.queue:
                found[pkt.pid] = ("uplink", pkt.assigned_sat)
        for srv in self.uplinks:
            if srv.current is not None:
                found[srv.current[1].pid] = ("in_service", srv.sat)
        for sat, srv in enumerate(self.downlinks):
            for packets in srv.queues.values():
                for pkt in packets:
                    found[pkt.pid] = ("downlink", sat)
            if isinstance(srv.current, DataPacket):
                found[srv.current.pid] = ("in_service", sat)
        for link in self._all_isls():
            # current AND draining retired generations: a packet in service
            # on a replaced link is still real network state
            for pkt in link.data_q:
                found[pkt.pid] = ("isl", link.sat)
            if isinstance(link.current, DataPacket):
                found[link.current.pid] = ("in_service", link.sat)
        for value in self._in_flight.values():
            pkt = value["pkt"]
            if isinstance(pkt, DataPacket):
                found[pkt.pid] = ("in_flight", value.get("sat"))
        return found

    def _data_packets(self) -> dict[int, DataPacket]:
        packets: dict[int, DataPacket] = {}
        for ep in self.endpoints.values():
            packets.update({p.pid: p for p in ep.queue})
        for packets_by_cell in (srv.queues for srv in self.downlinks):
            for queue in packets_by_cell.values():
                packets.update({p.pid: p for p in queue})
        for link in self._all_isls():
            packets.update({p.pid: p for p in link.data_q})
        for packets_at_sat in self.pending:
            packets.update({p.pid: p for p in packets_at_sat})
        return packets

    def validate_joint_plan(self, plan: q0.JointPlan) -> tuple[bool, tuple[str, ...]]:
        """Validate a Q0 plan without mutating kernel state.

        This is intentionally narrower than execution: it proves the plan is
        current and physically admissible at this instant.  Applying actions
        remains a separate atomic operation and is not exposed yet.
        """
        errors: list[str] = []
        ok, version_errors = q0.validate_plan_version(plan, self._state_version)
        errors.extend(version_errors)
        locations = self._data_packet_locations()
        packets = self._data_packets()
        forward_bits: dict[tuple[int, str], int] = {}
        deliver_bits: dict[int, int] = {}
        for action in plan.actions:
            location = locations.get(action.packet_id)
            if location is None:
                errors.append(f"packet {action.packet_id} is not live")
                continue
            if location[0] in ("in_service", "in_flight"):
                errors.append(f"packet {action.packet_id} is not actionable")
                continue
            if action.sat >= self.num_sats:
                errors.append(f"packet {action.packet_id}: sat {action.sat} out of range")
                continue
            packet = packets.get(action.packet_id)
            if packet is None:
                errors.append(f"packet {action.packet_id}: packet lookup failed")
                continue
            if location[0] != "pending":
                errors.append(f"packet {action.packet_id}: plan requires pending packet")
                continue
            if action.kind == "forward":
                if location[1] is not None and action.sat != location[1] \
                        and location[0] == "pending":
                    errors.append(f"packet {action.packet_id}: wrong pending satellite")
                    continue
                peer = self.topo.get(action.sat, {}).get(action.direction)
                if peer is None:
                    errors.append(f"packet {action.packet_id}: non-adjacent direction")
                    continue
                link = self.isls[action.sat].get(action.direction)
                if link is None:
                    errors.append(f"packet {action.packet_id}: ISL capacity unavailable")
                    continue
                forward_bits[(action.sat, action.direction)] = (
                    forward_bits.get((action.sat, action.direction), 0) + packet.bits)
            elif action.kind == "deliver":
                if action.packet_id not in locations:
                    continue
                ep = self.endpoints.get(packet.dst)
                link = ep.links.get(action.sat) if ep is not None else None
                if link is None or link.state != "active" \
                        or not self.geometry.ground_visible(action.sat, ep.lat, ep.lon, self.env.now):
                    errors.append(f"packet {action.packet_id}: deliver target unavailable")
                    continue
                if not self.downlinks[action.sat].room(packet.bits):
                    errors.append(f"packet {action.packet_id}: downlink capacity unavailable")
                deliver_bits[action.sat] = deliver_bits.get(action.sat, 0) + packet.bits
            else:
                if location[0] != "pending":
                    errors.append(f"packet {action.packet_id}: WAIT requires pending packet")
                elif action.sat != location[1]:
                    errors.append(
                        f"packet {action.packet_id}: wrong pending satellite")
                elif action.until is None or action.until <= self.env.now or action.until > self.horizon:
                    errors.append(f"packet {action.packet_id}: invalid WAIT deadline")
                elif (self.pending[action.sat].queued_bits
                      > self.pending[action.sat].capacity_bits):
                    errors.append(
                        f"packet {action.packet_id}: holding capacity already exceeded")
        for (sat, direction), bits in forward_bits.items():
            link = self.isls[sat][direction]
            if link._used() + bits > self.cfg_links["isl_queue_bits"]:
                errors.append(f"ISL plan capacity overcommitted at {sat}:{direction}")
        for sat, bits in deliver_bits.items():
            if self.downlinks[sat].queued_bits + bits > self.cfg_access["downlink_queue_bits"]:
                errors.append(f"downlink plan capacity overcommitted at {sat}")
        return not errors, tuple(errors)

    def apply_joint_plan(self, plan: q0.JointPlan) -> tuple[bool, tuple[str, ...]]:
        """Atomically apply a validated pending-packet plan.

        The first Q0 execution contract intentionally accepts only packets in
        finite kernel ``pending`` lists.  All actions are revalidated against
        the same live version before any mutation, so an invalid action cannot
        partially consume a plan.
        """
        ok, errors = self.validate_joint_plan(plan)
        if not ok:
            return False, errors
        packets = self._data_packets()
        pending_by_pid = {
            pkt.pid: sat for sat, queue in enumerate(self.pending)
            for pkt in queue
        }
        for action in plan.actions:
            sat = pending_by_pid[action.packet_id]
            self.pending[sat].remove(packets[action.packet_id], self.env.now)
        for action in plan.actions:
            pkt = packets[action.packet_id]
            if action.kind == "forward":
                pkt.assigned_sat = None
                self.isls[action.sat][action.direction].put_data(pkt)
            elif action.kind == "deliver":
                self.downlinks[action.sat].put(pkt)
                pkt.assigned_sat = action.sat
            else:
                pkt.holding_until = action.until
                self._hold_packet(action.sat, pkt)
        if plan.actions:
            self._state_version += 1
            self.q0_plan_audit.append({
                "version": plan.version,
                "applied_at": float(self.env.now),
                "actions": len(plan.actions),
            })
        return True, ()

    # --------------------------------------------------------- transmission
    def _fire_interrupt(self, link: Link, at: float):
        yield self.env.timeout(max(0.0, at - self.env.now))
        if not link.interrupt.triggered:
            link.interrupt.succeed()
        # D1 F1-R: a pre-dequeue-gated head never enters _transmit, so it
        # never races this interrupt; the hard-retirement cleanup must also
        # run here immediately instead of waiting for the next
        # _evaluate_handover tick (up to one time_step of stale assignment /
        # slot hold).  If a service IS in flight, _transmit's own interrupt
        # race performs the retirement side effect ("retired" -> caller ->
        # _on_link_retired), so skip to avoid a double release.
        for cell, ep in self.endpoints.items():
            if ep.links.get(link.sat) is not link:
                continue
            if link.state != "retiring" or self.env.now < link.retire_at:
                continue
            if self._in_service(ep, link.sat):
                continue
            for p in ep.queue:
                if p.assigned_sat == link.sat:
                    p.assigned_sat = None
            self._release(ep, link.sat, self.env.now,
                          f"{link.cause or 'mbb'}_retire_deadline")
            break

    def _transmit(self, dur: float | None, pkt, link_ref, occ_key: str,
                  owner=None, rate_fn=None, rate_recover_fn=None):
        """Race service completion vs geometry loss vs GE outage vs deadline
        vs (GSL only) hard link retirement.

        link_ref: ("gsl", sat, endpoint, link) or ("isl", a, b, ge). Returns
        "ok" only if the full service completed with the link continuously up;
        "retired" when the hard retirement deadline fired mid-service (no
        fate: the sender requeues the never-completed packet); "stalled" when
        the link never comes back before the horizon (no fate: the packet
        returns to its queue and settles as IN_SYSTEM_AT_STOP); otherwise the
        packet gets exactly one fate and only the service time already
        occupied is accounted. A link that is down before any service begins
        simply defers the start (this is not pause/resume: no transmission
        has started yet, so nothing is resumed).
        """
        link = None
        if link_ref[0] == "gsl":
            _, sat, ep, link = link_ref
            ge = self._gsl_ge(sat, ep.cell)

            def avail(x):
                return self.geometry.gsl_available(sat, ep.lat, ep.lon, x)

            def next_change(a, b):
                return self.geometry.next_gsl_change(sat, ep.lat, ep.lon, a, b)
        else:
            _, a, b, ge = link_ref

            def avail(x):
                return self.geometry.isl_available(a, b, x)

            def next_change(x, y):
                return self.geometry.next_isl_change(a, b, x, y)

        ge_q_key = "ge_gsl_queries" if link_ref[0] == "gsl" else "ge_isl_queries"
        if isinstance(pkt, ControlPacket):
            expiry = pkt.generated_at + pkt.ttl_s
            expiry_fate = "CONTROL_EXPIRED"
        else:
            expiry = pkt.deadline
            expiry_fate = "DATA_DEADLINE_EXPIRED"
        while True:
            t0 = self.env.now
            interrupt = link.interrupt if link is not None else None
            if self.ge_enabled:
                self.mech[ge_q_key] += 1
            geom_up = (not self.cfg_links["geometry_loss"]) or avail(t0)
            ge_up = (not self.ge_enabled) or not ge.is_down(t0)
            rate_up = True
            if rate_fn is not None:
                rate = rate_fn(t0)
                if rate <= 0:
                    # D1 zero/low-rate gate: the physical link is up but no
                    # feasible MCS rate exists at this distance.  Wait for the
                    # certified range-under recovery (plus deadline/retire);
                    # the packet stays queued and is NEVER failed here.
                    rate_up = False
                    # D1 F4 attribution: count the hold only when MCS is the
                    # actual blocking gate (geometry/GE both up); a deferral
                    # caused by geometry/GE belongs to that mechanism.
                    if geom_up and ge_up:
                        self.mech["mcs_zero_rate_holds"] += 1
                else:
                    dur = pkt.bits / rate
            retire_t = None
            if (link is not None and link.state == "retiring"
                    and link.retire_at is not None):
                retire_t = link.retire_at
                if retire_t <= t0:
                    return "retired"  # no service may start past the deadline
            if not (geom_up and ge_up and rate_up):
                ups = []
                if not geom_up:
                    nxt = next_change(t0, self.horizon)
                    if nxt is not None:
                        ups.append(nxt)
                if not ge_up:
                    nxt_up = ge.next_up(t0)
                    if nxt_up <= self.horizon:
                        ups.append(nxt_up)
                    self.mech["ge_waits"] += 1
                if not rate_up:
                    nxt_rate = rate_recover_fn(t0, self.horizon)
                    if nxt_rate is not None:
                        ups.append(nxt_rate)
                # Wait for the earliest of: link recovery, the actual
                # deadline, or the hard retirement interrupt.  The packet is
                # NEVER failed at t0 before its deadline: retirement may free
                # it for re-association, and the expiry fate may only be
                # assigned once the deadline is actually reached (or, with no
                # deadline, settle as IN_SYSTEM_AT_STOP at the horizon).
                wake_at = None
                for u in ups:
                    wake_at = u if wake_at is None else min(wake_at, u)
                if expiry is not None and expiry <= self.horizon:
                    wake_at = (expiry if wake_at is None
                               else min(wake_at, expiry))
                if wake_at is None:
                    # never available again within the horizon and no
                    # deadline: settle the packet at the exact horizon
                    if t0 >= self.horizon:
                        return "stalled"
                    wait = self.env.timeout(max(0.0, self.horizon - t0))
                else:
                    wait = self.env.timeout(max(0.0, wake_at - t0))
                # the down-wait MUST race the link retirement interrupt: a
                # retiring link's hard deadline applies to the waiting
                # service too ("retirement deadline races any in-flight
                # service").  Without the race the link stays pinned until
                # the outage recovers, blocking the whole server and every
                # endpoint on it.
                if interrupt is not None and not interrupt.triggered:
                    yield wait | interrupt
                else:
                    yield wait
                # The link may have entered the retiring state while we were
                # waiting (retire_t was captured at loop top while it was
                # still active).  The hard retirement deadline races this
                # down-wait just the same: when it is due, return "retired"
                # so the caller runs the retirement side effect (release +
                # requeue) instead of letting the expiry fate swallow it.
                if (link is not None and link.state == "retiring"
                        and link.retire_at is not None
                        and self.env.now >= link.retire_at):
                    return "retired"
                if retire_t is not None and self.env.now >= retire_t:
                    # hard retirement is due NOW: return "retired" so the
                    # caller performs the retirement side effect (release +
                    # requeue).  The packet is re-decided afterwards and, if
                    # the deadline has also been reached, fails there with
                    # the expiry fate -- the tie is resolved in favour of
                    # running the link lifecycle, not swallowed by the fate.
                    return "retired"
                if expiry is not None and expiry <= self.horizon \
                        and self.env.now >= expiry:
                    # the deadline has actually been reached while the link
                    # is still not usable: fail at the deadline
                    self._fail(pkt, expiry_fate)
                    return "fail"
                if wake_at is None and self.env.now >= self.horizon:
                    return "stalled"
                continue
            if owner is not None and owner._svc is not None \
                    and owner._svc[0] != t0:
                # service is about to actually start: restamp the caller's
                # _svc so the stop-time settle does not book the pre-service
                # down-wait as occupied (K2)
                owner._svc = (t0, occ_key)
            end = t0 + dur
            if owner is not None and owner._svc_phase == "waiting_for_link":
                # Real transmission starts only after every availability
                # check passed (geometry up, GE up, no retirement deadline).
                # Down-wait before this point must not be reported as
                # service progress: stamp the phase and start time here.
                owner._svc_phase = "transmitting"
                owner._tx_started_at = t0
                if rate_fn is not None:
                    owner._service_rate_bps = rate
            if rate_fn is not None:
                self.mech["mcs_rate_samples"] += 1
                rate_bps = int(round(rate))
                if (self.mech["mcs_rate_min_bps"] == 0
                        or rate_bps < self.mech["mcs_rate_min_bps"]):
                    self.mech["mcs_rate_min_bps"] = rate_bps
                if rate_bps > self.mech["mcs_rate_max_bps"]:
                    self.mech["mcs_rate_max_bps"] = rate_bps
            metric_stage = metric_link_id = metric_window_start = None
            metric_rate_bps = None
            if isinstance(pkt, DataPacket):
                metric_stage, metric_link_id = self._metric_link_id(link_ref, occ_key)
                metric_rate_bps = float(rate if rate_fn is not None
                                        else (owner._service_rate_bps
                                              if owner is not None
                                              else pkt.bits / dur))
                self._metric_service_start(
                    pkt, metric_stage, metric_link_id, metric_rate_bps, pkt.bits)
                metric_window_start = t0

            def record_metric_window(outcome: str) -> None:
                if isinstance(pkt, DataPacket):
                    self._metric_service_window(
                        pkt, metric_stage, metric_link_id,
                        metric_window_start, self.env.now,
                        metric_rate_bps, outcome)
            if isinstance(pkt, ControlPacket):
                self.mech["control_tx_started"] += 1
            if (link_ref[0] == "isl" and isinstance(pkt, DataPacket)
                    and pkt.learning_state is not None
                    and pkt.isl_enqueued_at is not None):
                # Settle the realized M1 queue diagnostic plus the safe
                # per-forward-step cost at the instant service starts.  The
                # raw queue reward remains available through the diagnostic
                # helper, while the training objective cannot profit from
                # extra hops.
                pkt.learning_reward = _learning.forward_reward(
                    t0 - pkt.isl_enqueued_at,
                    self.cfg_learning["reward_w1"],
                    self.cfg_learning["reward_beta"],
                    self.cfg_learning["forward_step_penalty"])
                pkt.isl_enqueued_at = None
            fail_t, fail_kind = end, None
            if self.cfg_links["geometry_loss"]:
                nxt = next_change(t0, end)
                if nxt is not None and nxt < fail_t:
                    fail_t, fail_kind = nxt, "GEOMETRY_LOSS_IN_FLIGHT"
            if self.ge_enabled:
                gd = ge.next_down(t0)
                if gd < fail_t:
                    fail_t, fail_kind = gd, "RANDOM_OUTAGE_IN_FLIGHT"
            if expiry is not None and expiry < fail_t:
                fail_t, fail_kind = expiry, expiry_fate
            if retire_t is not None and retire_t <= fail_t:
                fail_t, fail_kind = retire_t, "RETIRE"
            # race the wait against a possibly later-scheduled retirement
            # interrupt: on ANY wake the whole race is recomputed from now.
            wait = self.env.timeout(max(0.0, fail_t - t0))
            if interrupt is not None and not interrupt.triggered:
                yield wait | interrupt
            else:
                yield wait
            self.occupied[occ_key] += self.env.now - t0
            # The link can enter ``retiring`` after service starts, so the
            # retire_t snapshot above may be absent.  Re-read the mutable
            # lifecycle after every race wake.  Retirement wins an exact tie
            # with completion/deadline, matching the pre-service/down-wait
            # lifecycle rule and preventing a completed-looking packet from
            # escaping onto a link whose hard deadline is already due.
            if (link is not None and link.state == "retiring"
                    and link.retire_at is not None
                    and self.env.now >= link.retire_at):
                record_metric_window("retired")
                return "retired"
            if self.env.now < fail_t - 1e-12:
                record_metric_window("interrupted")
                continue  # woken by the interrupt: recompute the race
            if fail_kind is None:
                record_metric_window("ok")
                return "ok"
            if fail_kind == "RETIRE":
                record_metric_window("retired")
                return "retired"
            if fail_kind == "RANDOM_OUTAGE_IN_FLIGHT":
                self.mech["ge_failures"] += 1
            record_metric_window("failed")
            self._fail(pkt, fail_kind)
            return "fail"

    # ------------------------------------------------------------- processes
    def _horizon_closer(self):
        """Guarantees the simulation clock reaches the exact horizon, so
        final accounting (in-service occupation, queue areas, IN_SYSTEM)
        settles at the configured horizon, never at a stray last event."""
        # Start measurement after the already-created endpoint/emitter
        # processes.  This preserves the historical same-time ordering used
        # by deterministic kernel tests while still sampling from t=0.
        self.env.process(self._available_capacity_ticker())
        yield self.env.timeout(self.horizon)
        self.closed_at = self.env.now

    def _emitter(self, cell: str, rows):
        for r in rows:
            delay = r["emit_time_s"] - self.env.now
            if delay > 0:
                yield self.env.timeout(delay)
            # activate at the actual emission instant, never earlier: an
            # emitter for a future row must not create its endpoint at t=0
            # (that would re-open the A3 preposition/ads/obs leak)
            ep = self._ensure_endpoint(cell)
            self._count_data_packet()
            pkt = DataPacket(r["packet_id"], r["src_grid_id"], r["dst_grid_id"],
                             r["bits"], r["deadline_at_s"], self.env.now)
            self._metric_packet_emitted(pkt)
            self.ledger.register(pkt.pid, pkt.bits)
            now = self.env.now
            if pkt.deadline is not None and now > pkt.deadline:
                self._fail(pkt, "DATA_DEADLINE_EXPIRED")
                continue
            link = ep.primary_link()
            if link is None and not self._visible_sats(ep):
                # Access coverage is a boundary condition, not a network
                # congestion outcome.  The historical default rejects at
                # emission; the explicit research queue profile keeps the
                # packet in the existing finite endpoint uplink queue so the
                # normal access ticker can retry when coverage returns.
                if self.cfg_access["unavailable_policy"] == "reject":
                    self._fail(pkt, "ACCESS_REJECTED")
                    continue
            if ep.queued_bits + pkt.bits > self.cfg_access["uplink_queue_bits"]:
                self._fail(pkt, "ACCESS_QUEUE_OVERFLOW")
                continue
            # MBB: new arrivals go to the newest active/acquiring link; an
            # unassociated endpoint queues and requests fair access instead of
            # being rejected just because every slot is currently taken.
            pkt.assigned_sat = link.sat if (link is not None and link.state == "active") else None
            ep.queue.append(pkt)
            ep.queued_bits += pkt.bits
            ep.area.add(pkt.bits, now)
            self._metric_queue_enter(
                pkt, "uplink", f"gsl:uplink:pending:{cell}")
            self._note_busy(ep.cell)
            if link is None:
                self._request_or_grant(ep, now)
            for sat_id in list(ep.links):
                self._poke(self.uplinks[sat_id].wake)
            # let link servers dequeue at the same instant before the next
            # emission; this keeps same-time ordering deterministic.
            yield self.env.timeout(0.0)

    def _endpoint_ticker(self, ep: TrafficEndpoint):
        while True:
            self._sweep_endpoint_queue(ep)
            self._access_tick_endpoint(ep)
            self._evaluate_handover(ep)
            yield self.env.timeout(self.time_step)

    def _pending_ticker(self, sat: int):
        while True:
            yield self.env.timeout(self.time_step)
            for pkt in self.pending[sat].sweep_expired(self.env.now):
                self._fail(pkt, "DATA_DEADLINE_EXPIRED")
            self._redecide_pending(sat)
            self._sweep_downlink_queues(sat)
            self._access_tick_sat(sat)

    def _sweep_downlink_queues(self, sat: int):
        now = self.env.now
        dl = self.downlinks[sat]
        for cell, q in list(dl.queues.items()):
            kept = deque()
            for pkt in q:
                if pkt.deadline is not None and now >= pkt.deadline:
                    dl.queued_bits -= pkt.bits
                    dl.area.remove(pkt.bits, now)
                    self._fail(pkt, "DATA_DEADLINE_EXPIRED")
                else:
                    kept.append(pkt)
            dl.queues[cell] = kept

    # -------------------------------------------------------- fair access
    def _endpoint_demand(self, ep: TrafficEndpoint) -> bool:
        """Current demand: queued uplink packets, or some satellite holding
        packets destined to this endpoint (pending re-decision or an actual
        downlink queue)."""
        if ep.queue:
            return True
        return bool(self._downlink_demand_sats(ep.cell))

    def _downlink_demand_sats(self, cell: str) -> list[int]:
        out = []
        for s in range(self.num_sats):
            if any(p.dst == cell for p in self.pending[s]):
                out.append(s)
                continue
            if self.downlinks[s].queues.get(cell):
                out.append(s)
        return out

    def _candidates(self, ep: TrafficEndpoint, now: float):
        """Association candidates, demand-aware: satellites already holding
        this endpoint's downlink traffic first (by elevation), then every
        other currently visible satellite by elevation. Current geometry
        only — no future ephemeris."""
        dl = [s for s in self._downlink_demand_sats(ep.cell)
              if self.geometry.ground_visible(s, ep.lat, ep.lon, now)]
        dl.sort(key=lambda s: (-self.geometry.elevation_deg(s, ep.lat, ep.lon, now), s))
        seen = set(dl)
        rest = [(elev, s) for elev, s in self._visible_sats(ep) if s not in seen]
        return [(self.geometry.elevation_deg(s, ep.lat, ep.lon, now), s) for s in dl] + rest

    def _try_grant(self, ep: TrafficEndpoint, now: float, preposition: bool = False) -> bool:
        for _elev, s in self._candidates(ep, now):
            if len(self.slots[s]) < self.cfg_access["slots_per_satellite"]:
                waiters = self.access_wait[s]
                if waiters:
                    # FIFO contract: a free slot on a contended satellite goes
                    # to the earliest request.  An endpoint that has not
                    # queued here (or is not the oldest waiter) must join the
                    # queue via _request_or_grant and wait its turn, instead
                    # of jumping ahead because its endpoint ticker happens to
                    # run first.
                    req_t = waiters.get(ep.cell)
                    if req_t is None:
                        continue
                    oldest_cell, _oldest_t = min(
                        waiters.items(), key=lambda kv: (kv[1], kv[0]))
                    if ep.cell != oldest_cell:
                        continue
                    del waiters[ep.cell]  # grant consumes this request
                else:
                    req_t = None
                # a successful grant anywhere ends this endpoint's presence
                # on every satellite's wait queue; stale entries on other
                # satellites must not linger and create phantom contention
                # (or silently drop their accumulated wait from the stats)
                for s2 in range(self.num_sats):
                    if s2 == s:
                        continue  # the granting satellite is settled below
                    stale_t = self.access_wait[s2].pop(ep.cell, None)
                    if stale_t is not None:
                        wt = now - stale_t
                        self.access_stats["wait_time_s_total"] += wt
                        self.access_stats["wait_time_s_max"] = max(
                            self.access_stats["wait_time_s_max"], wt)
                self._associate(ep, s, now)
                if preposition:
                    self.access_stats["preposition_grants"] += 1
                else:
                    self.access_stats["grants"] += 1
                    if req_t is not None:
                        wt = now - req_t
                        self.access_stats["wait_time_s_total"] += wt
                        self.access_stats["wait_time_s_max"] = max(
                            self.access_stats["wait_time_s_max"], wt)
                return True
        return False

    def _request_or_grant(self, ep: TrafficEndpoint, now: float):
        """Explicit access request from current demand. Grants immediately
        when a candidate has a free slot; otherwise the endpoint joins the
        FIFO wait queue of its best candidate (deterministic, reproducible)."""
        if self._try_grant(ep, now):
            return
        cand = self._candidates(ep, now)
        if not cand:
            return  # nothing visible: retried at the next tick
        s = cand[0][1]
        if ep.cell not in self.access_wait[s]:
            self.access_wait[s][ep.cell] = now
            self.access_stats["requests"] += 1

    def _access_tick_endpoint(self, ep: TrafficEndpoint):
        now = self.env.now
        # idle = no uplink queue, no satellite-side demand, nothing in
        # service; idleness is measured from the last ACTIVITY (emission,
        # service start, requeue, arriving downlink demand), so work done
        # between ticks never counts as idle time
        idle = (not ep.queue
                and not self._downlink_demand_sats(ep.cell)
                and not any(self._in_service(ep, s) for s in ep.links))
        last_busy = self.access_last_busy.get(ep.cell, 0.0)
        # lease rotation and idle release apply only under contention; with
        # no waiters, keep-stable wins and nothing rotates gratuitously
        for sat, link in list(ep.links.items()):
            if link.state != "active" or not self.access_wait[sat]:
                continue
            if now - link.since >= self.cfg_access["slot_lease_s"]:
                # planned rotation: graceful retire — only already-assigned
                # (in-flight) packets drain; the hard retirement deadline is
                # the backstop and races any in-flight service
                link.state = "retiring"
                link.cause = "lease"
                link.retire_at = now + self.cfg_access["retirement_deadline_s"]
                self.env.process(self._fire_interrupt(link, link.retire_at))
                self.handover_events.append(
                    {"t": now, "endpoint": ep.cell, "type": "lease_retire",
                     "sat": sat})
                continue
            if idle and now - last_busy >= self.cfg_access["idle_release_s"]:
                self._release(ep, sat, now, "idle_release")
        # demand-driven requests, or free-slot pre-positioning when nothing
        # contends for any slot
        if ep.primary_link() is not None:
            return
        if any(l.state == "retiring" for l in ep.links.values()):
            return  # wait for the retiring link to clear first
        if self._endpoint_demand(ep):
            self._request_or_grant(ep, now)
            if ep.primary_link() is not None:
                # fresh grant for an endpoint with pending/downlink demand:
                # re-decide its pending packets now instead of waiting for the
                # next tick (lazy endpoint activation must not add one
                # time_step of latency to first delivery)
                self._redecide_cell_pending(ep.cell)
        elif not any(self.access_wait[s] for s in range(self.num_sats)):
            self._try_grant(ep, now, preposition=True)

    def _access_tick_sat(self, sat: int):
        """Grant freed slots to waiting endpoints in FIFO request order."""
        now = self.env.now
        q = self.access_wait[sat]
        for cell in list(q):
            ep = self.endpoints[cell]
            if ep.primary_link() is not None or not self._endpoint_demand(ep):
                del q[cell]  # stale request
                continue
            if len(self.slots[sat]) >= self.cfg_access["slots_per_satellite"]:
                break
            if not self.geometry.ground_visible(sat, ep.lat, ep.lon, now):
                continue  # stays queued until geometry allows
            req_t = q.pop(cell)
            self._associate(ep, sat, now)
            self.access_stats["grants"] += 1
            wt = now - req_t
            self.access_stats["wait_time_s_total"] += wt
            self.access_stats["wait_time_s_max"] = max(
                self.access_stats["wait_time_s_max"], wt)
            self._redecide_cell_pending(cell)

    def _redecide_cell_pending(self, cell: str) -> None:
        """Re-decide pending packets destined to ``cell`` on every satellite.

        A fresh destination grant can happen on a different satellite than the
        one currently holding the packet in its pending list (e.g. the egress
        grant fires while the packet still waits on the previous hop), so the
        scan is global.
        """
        for s in range(self.num_sats):
            waiting = [p for p in self.pending[s] if p.dst == cell]
            if not waiting:
                continue
            # the satellite holding queue is capacity-checked and area
            # accounted; remove each packet through the queue API instead of
            # replacing the queue object with a plain list (D2 holding
            # queue)
            q = self.pending[s]
            for pkt in waiting:
                q.remove(pkt, self.env.now)
            for pkt in waiting:
                if self.compute_delay_s > 0:
                    self.env.process(self.decide_deferred(pkt, s))
                else:
                    self._decide(pkt, s)

    def _control_advertiser(self, sat: int):
        interval = self.cfg_cp["advertise_interval_s"]
        while True:
            self._advertise(sat)
            yield self.env.timeout(interval)

    # --------------------------------------------------------------- control
    def _advertise(self, sat: int):
        self.ctrl_seq += 1
        # metrics are bound to the CURRENT peer identity: after a rematch an
        # old advertisement must read as "unknown" for the new edge, never be
        # reinterpreted as the new peer's metric (D2 review M2)
        isl_bits = {d: {"peer": link.peer,
                        "value": link.data_bits + link.ctrl_bits}
                    for d, link in self.isls[sat].items()}
        isl_prop = {
            d: {"peer": link.peer,
                "value": model.propagation_delay_s(
                    self.geometry.isl_range_km(sat, link.peer, self.env.now))}
            for d, link in self.isls[sat].items()
        }
        serve = sorted(c for c, ep in self.endpoints.items()
                       if ep.links.get(sat) is not None
                       and ep.links[sat].state == "active")
        snap = control.build_snapshot(
            sat, self.env.now, self.geometry,
            {c: (ep.lat, ep.lon) for c, ep in self.endpoints.items()},
            isl_bits, isl_prop, len(self.slots[sat]),
            self.cfg_access["slots_per_satellite"])
        snap["serve_cells"] = serve
        self.mech["control_snapshots"] += 1
        # the origin never accepts its own advertisement back, however long
        # it loops: its own (origin, seq) keys are pre-seeded as seen
        self.seen_ctrl[sat].add((sat, self.ctrl_seq))
        for d in self.control_children[sat][sat]:
            link = self.isls[sat][d]
            self.ctrl_iid += 1
            pkt = ControlPacket(
                self.ctrl_iid, sat, self.ctrl_seq, self.env.now,
                self.cfg_cp["ttl_s"], self.cfg_cp["vis_k"],
                self.cfg_cp["packet_bits"], snap)
            self.ctrl_ledger.register(pkt.iid, pkt.bits)
            self.mech["control_registered"] += 1
            if not link.room(pkt.bits):
                self.ctrl_ledger.record(pkt.iid, "QUEUE_OVERFLOW", pkt.bits)
                continue
            self.mech["control_entered_queue"] += 1
            link.put_ctrl(pkt)

    def _ctrl_arrive_after_prop(self, pkt: ControlPacket, from_sat: int, sat: int, prop: float):
        yield self.env.timeout(prop)
        now = self.env.now
        pkt.mark_received(now)  # the physical arrival instant, whatever the fate
        if not pkt.valid_at(now):
            self.ctrl_ledger.record(pkt.iid, "CONTROL_EXPIRED", pkt.bits,
                                    received_at=now)
            return
        if pkt.origin == sat:
            # explicit guard: an origin never consumes its own looped
            # advertisement, independent of topology/vis_k
            self.ctrl_ledger.record(pkt.iid, "DUPLICATE", pkt.bits,
                                    received_at=now)
            return
        key = (pkt.origin, pkt.seq)
        if key in self.seen_ctrl[sat]:
            self.ctrl_ledger.record(pkt.iid, "DUPLICATE", pkt.bits,
                                    received_at=now)
            return
        self.seen_ctrl[sat].add(key)
        self.ctrl_ledger.record(pkt.iid, "DELIVERED", pkt.bits, received_at=now)
        hops = self.cfg_cp["vis_k"] - pkt.remaining_hops + 1
        entry = control.CacheEntry(pkt.origin, pkt.payload, pkt.generated_at,
                                   pkt.received_at, pkt.ttl_s, hops=hops)
        self.caches[sat].put(entry)
        if pkt.remaining_hops > 1:
            for d in self.control_children[pkt.origin][sat]:
                link = self.isls[sat][d]
                self.ctrl_iid += 1
                fwd = ControlPacket(
                    self.ctrl_iid, pkt.origin, pkt.seq, pkt.generated_at,
                    pkt.ttl_s, pkt.remaining_hops - 1, pkt.bits, pkt.payload)
                self.ctrl_ledger.register(fwd.iid, fwd.bits)
                self.mech["control_registered"] += 1
                if not link.room(fwd.bits):
                    self.ctrl_ledger.record(fwd.iid, "QUEUE_OVERFLOW", fwd.bits)
                    continue
                self.mech["control_entered_queue"] += 1
                link.put_ctrl(fwd)

    # -------------------------------------------------------------- handover
    def _sweep_endpoint_queue(self, ep: TrafficEndpoint):
        now = self.env.now
        kept = deque()
        for pkt in ep.queue:
            if pkt.deadline is not None and now >= pkt.deadline:
                ep.queued_bits -= pkt.bits
                ep.area.remove(pkt.bits, now)
                self._fail(pkt, "DATA_DEADLINE_EXPIRED")
            else:
                kept.append(pkt)
        ep.queue = kept

    def _visible_sats(self, ep: TrafficEndpoint):
        now = self.env.now
        out = []
        for s in range(self.num_sats):
            if self.geometry.ground_visible(s, ep.lat, ep.lon, now):
                out.append((self.geometry.elevation_deg(s, ep.lat, ep.lon, now), s))
        out.sort(key=lambda x: (-x[0], x[1]))
        return out

    def _associate(self, ep: TrafficEndpoint, sat: int, now: float):
        acq = self.cfg_access["acquisition_delay_s"]
        link = Link(sat, "acquiring", now, ready_at=now + acq,
                    interrupt=self.env.event())
        ep.links[sat] = link
        # GSL GE is part of the Q0 global-state universe: materialize it at
        # association so snapshot_global() can report every current
        # endpoint-satellite pair explicitly instead of silently omitting
        # not-yet-queried pairs (keyed RNG stream keeps this deterministic).
        self._gsl_ge(sat, ep.cell)
        self.slots[sat].add(ep.cell)
        self._poke(self.downlinks[sat].wake)
        self.handover_events.append({"t": now, "endpoint": ep.cell,
                                     "type": "associate", "sat": sat})
        if acq <= 0:
            link.state = "active"
            self._poke(self.uplinks[sat].wake)
        else:
            self.env.process(self._activate_after_delay(ep, link))

    def _activate_after_delay(self, ep: TrafficEndpoint, link: Link):
        yield self.env.timeout(max(0.0, link.ready_at - self.env.now))
        if ep.links.get(link.sat) is link and link.state == "acquiring":
            link.state = "active"
            self._poke(self.uplinks[link.sat].wake)
            self._poke(self.downlinks[link.sat].wake)

    def _release(self, ep: TrafficEndpoint, sat: int, now: float, reason: str):
        link = ep.links.pop(sat, None)
        if link is None:
            return
        self.slots[sat].discard(ep.cell)
        self._poke(self.downlinks[sat].wake)
        self.access_stats["slot_hold_s_total"] += now - link.since
        rel = self.access_stats["releases"]
        rel[reason] = rel.get(reason, 0) + 1
        self.handover_events.append({"t": now, "endpoint": ep.cell,
                                     "type": "release", "sat": sat,
                                     "reason": reason})

    def _on_link_retired(self, ep: TrafficEndpoint, sat: int):
        """An in-flight service hit the hard retirement deadline: the link
        dies NOW (never used past retire_at). Any still-assigned queued
        packets are unassigned so a later association can serve them."""
        link = ep.links.get(sat)
        if link is None or link.state != "retiring":
            return
        if link.retire_at is None or self.env.now < link.retire_at:
            return
        for p in ep.queue:
            if p.assigned_sat == sat:
                p.assigned_sat = None
        self._release(ep, sat, self.env.now,
                      f"{link.cause or 'mbb'}_retire_deadline")

    def _in_service(self, ep: TrafficEndpoint, sat: int) -> bool:
        up = self.uplinks[sat].current
        if up is not None and up[0] is ep:
            return True
        dl = self.downlinks[sat].current
        return dl is not None and dl.dst == ep.cell

    def _evaluate_handover(self, ep: TrafficEndpoint):
        now = self.env.now
        # state transitions due
        for sat, link in list(ep.links.items()):
            if link.state == "retiring":
                in_service = self._in_service(ep, sat)
                drained = not any(p.assigned_sat == sat for p in ep.queue) and not in_service
                if drained:
                    self._release(ep, sat, now, f"{link.cause or 'mbb'}_drained")
                elif now >= link.retire_at and not in_service:
                    for p in ep.queue:
                        if p.assigned_sat == sat:
                            p.assigned_sat = None
                    self._release(ep, sat, now,
                                  f"{link.cause or 'mbb'}_retire_deadline")
        cand = self._visible_sats(ep)
        current = ep.primary_link()
        if current is None:
            return  # (re-)association is the fair access manager's job
        cur_sat = current.sat
        cur_vis = self.geometry.ground_visible(cur_sat, ep.lat, ep.lon, now)
        cur_elev = self.geometry.elevation_deg(cur_sat, ep.lat, ep.lon, now) if cur_vis else -90.0
        best_elev, best_sat = cand[0] if cand else (-90.0, None)
        if cur_vis:
            if not cand:
                return
            if best_sat == cur_sat:
                return
            if best_elev - cur_elev < self.cfg_access["hysteresis_deg"]:
                return  # keep-stable via elevation hysteresis (degrees)
            if now - current.since < self.cfg_access["min_dwell_s"]:
                return  # minimum dwell time
        # switch (or re-establish after geometry loss)
        target = None
        for elev, s in cand:
            if s == cur_sat:
                continue
            if len(self.slots[s]) < self.cfg_access["slots_per_satellite"]:
                target = s
                break
        if target is None:
            if not cur_vis:
                self._release(ep, cur_sat, now, "geometry_lost_no_candidate")
            return
        mbb = (self.cfg_access["association"] == "mbb"
               and self.cfg_access["dual_connect"]
               and cur_vis
               and len([l for l in ep.links.values() if l.state == "retiring"])
               < self.cfg_access["retiring_link_limit"])
        if mbb:
            # old link keeps draining already-assigned packets, but the hard
            # retirement deadline races any in-flight service on it
            old = ep.links[cur_sat]
            old.state = "retiring"
            old.cause = "mbb"
            old.retire_at = now + self.cfg_access["retirement_deadline_s"]
            self.env.process(self._fire_interrupt(old, old.retire_at))
            for p in ep.queue:
                if p.assigned_sat is None:
                    p.assigned_sat = cur_sat
            self._associate(ep, target, now)
            self.mech["mbb_events"] += 1
            self.handover_events.append({"t": now, "endpoint": ep.cell,
                                         "type": "mbb", "from": cur_sat,
                                         "to": target})
        else:
            # BBM: never preempts a packet currently in service on the old link
            if self._in_service(ep, cur_sat):
                return  # defer the break until the in-flight packet completes
            self._release(ep, cur_sat, now, "bbm_switch")
            self._associate(ep, target, now)
            self.handover_events.append({"t": now, "endpoint": ep.cell,
                                         "type": "bbm", "from": cur_sat,
                                         "to": target})

    # --------------------------------------------------------------- routing
    def _serving_sats(self, cell: str) -> list[int]:
        """Satellites whose association with the endpoint is active right now
        (direct kernel truth; used ONLY by the labeled oracle)."""
        ep = self.endpoints[cell]
        return sorted(s for s, l in ep.links.items() if l.state == "active")

    def _learning_observation(self, sat: int, dst_cell: str) -> np.ndarray:
        queues = {d: lnk.data_bits + lnk.ctrl_bits
                  for d, lnk in self.isls[sat].items()}
        visible = sum(
            1 for ep in self.endpoints.values()
            if self.geometry.ground_visible(sat, ep.lat, ep.lon, self.env.now)
        )
        own = _learning.own_state(
            len(self.slots[sat]), self.cfg_access["slots_per_satellite"],
            queues, self.cfg_links["isl_queue_bits"], visible,
            len(self.endpoints),
        )
        obs_hops = self.cfg_learning.get("obs_hops")
        ep = self.endpoints.get(dst_cell)
        dst_feats = None
        if ep is not None:
            sat_lat, sat_lon, _ = self.geometry.subpoint(sat, self.env.now)
            dst_feats = _learning.destination_features(
                sat_lat, sat_lon, ep.lat, ep.lon)
        root_pos = None
        if self.cfg_rt["contract"] in _learning.GRAPH_CONTRACTS:
            # The root satellite's own position is directly measured local
            # state: query geometry, never the control cache — the control
            # plane explicitly refuses to cache a satellite's own looped
            # advertisement, so a cache lookup would read (0, 0, 0).
            root_pos = self.geometry.positions(self.env.now)[sat]
        return _learning.build_observation(
            self.cfg_rt["contract"], sat, self.caches[sat], self.env.now,
            self.topo, own, self.cfg_links["isl_queue_bits"],
            obs_hops=obs_hops, dst_feats=dst_feats, root_pos=root_pos,
        )

    def _finish_learning_transition(self, pkt: DataPacket, next_state,
                                    next_mask: dict, done: bool,
                                    terminal_reward: float | None = None) -> None:
        if self.learner is None or pkt.learning_state is None:
            return
        if terminal_reward is None and pkt.learning_reward is None:
            # every reward is settled where the rewarded event actually
            # happens: the forward queue reward at ISL service start
            # (_transmit), the arrival reward at real delivery
            # (_deliver_after_prop, passed as terminal_reward). Reaching here
            # with neither means the reward was never realized — fail loud
            # instead of storing a silent None
            raise KernelError(
                "learning transition closed with unrealized reward "
                f"(pid={pkt.pid})")
        reward = (pkt.learning_reward if terminal_reward is None
                  else float(terminal_reward))
        self.learner.remember(
            pkt.learning_state, pkt.learning_action, reward,
            next_state, next_mask, done,
        )
        pkt.learning_state = None
        pkt.learning_action = None
        pkt.learning_reward = None
        self._learning_open.discard(pkt)

    def _close_learning_at_stop(self) -> None:
        """Explicitly discard every learning transition still open at the
        stop time (packets pending re-decision, queued on an ISL/downlink,
        in service, or in propagation).

        A horizon-truncated episode is NOT remembered: fabricating a terminal
        reward for it would corrupt training. The discards are counted in the
        mechanism counters so the receipt can check
        ``decisions == transitions + discarded_at_stop`` instead of the
        difference vanishing silently."""
        if self.learner is None:
            return
        for pkt in self._learning_open:
            pkt.learning_state = None
            pkt.learning_action = None
            pkt.learning_reward = None
            self.mech["learning_discarded_at_stop"] += 1
        self._learning_open.clear()

    def _learning_action(self, pkt: DataPacket, sat: int, mask: dict) -> str:
        state = self._learning_observation(sat, pkt.dst)
        if pkt.learning_state is not None and pkt.learning_reward is None:
            # The only action allowed to be open without a settled reward is
            # deliver: its arrival reward exists only at real delivery. A
            # deliver that never reached the user (e.g. the downlink was hard
            # retired and the packet bounced back to pending) settles at 0 on
            # re-decision — it must NOT collect arrive_reward.
            if pkt.learning_action != "deliver":
                raise KernelError(
                    "unsettled non-deliver learning transition at re-decision "
                    f"(pid={pkt.pid}, action={pkt.learning_action})")
            self._finish_learning_transition(
                pkt, state, mask, False, terminal_reward=0.0)
        else:
            self._finish_learning_transition(pkt, state, mask, False)
        action = self.learner.choose(state, mask, self.env.now)
        # No reward is known at decision time by construction: a forward
        # action's M1 queue reward is settled when its ISL service actually
        # starts (_transmit); the deliver arrival reward is settled at real
        # delivery (_deliver_after_prop).
        pkt.learning_state = state
        pkt.learning_action = action
        pkt.learning_reward = None
        self._learning_open.add(pkt)
        return action

    # ------------------------- T1-FROZEN-LEDGER: what was known, and when

    def _observed_cache_entries(self, sat: int, now: float) -> dict:
        """{origin: CacheEntry} that this node had ACTUALLY been told by now.

        One definition of "what could be known", shared by the truth audit
        (which reports the age of every contributing origin) and the
        observation record (which reports the measurement time of every
        neighbour separately).  Two call sites that disagreed about the
        information boundary would silently break the very quantity the T1
        ledger exists to measure.
        """
        if self.learner is not None:
            contract = self.cfg_rt["contract"]
            return _learning.information_set(
                contract, sat, self.caches[sat], now, self.topo,
                obs_hops=(1 if contract == "C1"
                          else self.cfg_learning.get("obs_hops")))
        if self.cfg_cp["enabled"]:
            # R8-A6: a non-learning run has no observation contract to crop
            # against, but the node's actual knowledge is still exactly its
            # valid control cache.
            return self.caches[sat].valid_entries(now)
        return {}

    def _observation_at_start(self, pkt: DataPacket, sat: int, now: float, *,
                              mode: str, source: str, own_queue_bits: dict,
                              considered: list, legal: list,
                              status: str | None, kind: str,
                              action: str | None) -> dict:
        """The observation a decision was ACTUALLY based on.

        Every contributing neighbour keeps its OWN measurement/arrival time:
        a single scalar cannot represent several advertisements that were
        generated and arrived at different instants, and averaging them would
        invent a time at which nothing was measured.

        mode="frozen": the snapshot taken before the computation, so obs-time
        is t_decision_start and no later state may appear here.  mode
        ="refresh": the state re-read when the computation landed, so obs-time
        is t_decision_commit.  The label, never the numbers, is what keeps the
        two apart.
        """
        neighbours = {}
        for origin, entry in sorted(
                self._observed_cache_entries(sat, now).items()):
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            advertised = {}
            for direction, record in (payload.get("isl_queue_bits")
                                      or {}).items():
                if not isinstance(record, dict):
                    continue
                # the advertised metric is bound to the peer it was measured
                # on: after a rematch the same direction may point elsewhere,
                # and then this record is not information about this topology
                if record.get("peer") != self.topo.get(int(origin), {}).get(
                        direction):
                    continue
                advertised[direction] = int(record["value"])
            neighbours[str(origin)] = {
                "origin": int(origin),
                "measurement": "control_cache_advertisement",
                "generated_at": float(entry.generated_at),
                "received_at": float(entry.received_at),
                "age_s": float(max(0.0, entry.aoi(now))),
                "hops": int(entry.hops),
                "advertised_isl_queue_bits": advertised,
                "advertised_serve_cells": sorted(
                    payload.get("serve_cells", ())),
            }
        return {
            "schema": "leo-sim-observation-at-start/v1",
            "mode": mode,
            "source": source,
            "t_observed": float(now),
            "sat": int(sat),
            "own_queue_bits": {d: int(bits)
                               for d, bits in own_queue_bits.items()},
            "neighbours": neighbours,
            "candidate_directions": list(considered),
            "legal_directions": list(legal),
            "routing_status": status,
            "kind": kind,
            "action": action,
        }

    def _estimate_at_start(self, pkt: DataPacket, sat: int, now: float,
                           chosen: str | None, kind: str
                           ) -> tuple[dict | None, str | None]:
        """Predict the downstream resource from information available at now.

        Returns (estimate, unavailable_reason); exactly one of the two is not
        None.  Only what this node had ACTUALLY been told at obs-time may
        enter the estimate: its own queues, static topology, current geometry
        and the control advertisements it has received.  The peer's real
        queues and the peer's OWN cache are deliberately NOT read -- that is
        the truth audit (_peer_downstream_truth), and reading it here would
        backfill the deployable prediction with hindsight, which is exactly
        the confusion this record exists to remove.
        """
        if kind != "forward" or chosen is None \
                or chosen not in self.isls[sat]:
            return None, "no_downstream_isl_resource_for_%s" % kind
        link = self.isls[sat][chosen]
        peer = link.peer
        if peer is None:
            return None, "chosen_direction_has_no_peer"
        policy = self.cfg_rt["policy"]
        cache_hops = None
        if policy == "oracle":
            # The labeled oracle has perfect global current knowledge by
            # contract, and the kernel already hands it the true serving set
            # at decision time; the estimate stays inside that same contract
            # instead of pretending the oracle is cache-limited.
            serving = self._serving_sats(pkt.dst)
            is_destination = peer in serving
            targets = [s for s in serving if s != peer]
            information_source = "oracle_global_knowledge"
        else:
            cache_hops = (1 if self.cfg_rt["contract"] == "C1"
                          else self.cfg_learning.get("obs_hops")) \
                if self.learner is not None else None
            serving = routing.destinations_in_cache(
                self.caches[sat], pkt.dst, now, max_cache_hops=cache_hops)
            is_destination = peer in serving
            targets = [s for s in serving if s != peer]
            information_source = "control_cache"
        entry = self._observed_cache_entries(sat, now).get(peer)
        advertised_q: dict[str, int] = {}
        if entry is not None:
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            for direction, record in (payload.get("isl_queue_bits")
                                      or {}).items():
                if not isinstance(record, dict):
                    continue
                if record.get("peer") != self.topo.get(peer, {}).get(direction):
                    continue
                advertised_q[direction] = int(record["value"])
        if is_destination:
            egress = None
        elif not self.topo.get(peer):
            return None, "peer_has_no_isl_egress"
        else:
            cands, status = routing.choose_next_hop(
                policy, peer, pkt.dst, now, self.geometry, self.topo,
                self.caches[sat], advertised_q, self.isl_rate_bps,
                model.propagation_delay_s,
                oracle_targets=(targets if policy == "oracle" else None),
                best_only=False,
                reverse_adj=self._routing_reverse_adj,
                sorted_adj=self._routing_sorted_rev_adj,
                rate_from_propagation=(
                    (lambda prop_s: link_budget.mcs_rate_bps(
                        prop_s * model.C_KM_S, self.rf_isl, self.mcs_table))
                    if self.rate_model == "mcs" else None),
                cache_hops=cache_hops)
            if status != "ok" or not cands:
                return None, "peer_route_%s" % status
            egress = cands[0]
        return {
            "schema": "leo-sim-estimate-at-start/v1",
            # explicit, because "was this number known then or only later" is
            # the single question that separates this object from
            # truth_at_commit
            "truth_used": False,
            "for_direction": chosen,
            "for_peer": int(peer),
            "t_observed": float(now),
            "information_source": information_source,
            "neighbour_measurement": (None if entry is None else {
                "origin": int(peer),
                "generated_at": float(entry.generated_at),
                "received_at": float(entry.received_at),
                "age_s": float(max(0.0, entry.aoi(now))),
                "hops": int(entry.hops),
            }),
            "peer_is_destination": bool(is_destination),
            "peer_egress_direction": egress,
            # the advertised per-direction backlog; None means "not told",
            # never "told it was zero"
            "peer_egress_queue_bits_estimate": (
                advertised_q.get(egress) if egress is not None else None),
            "peer_egress_queue_bits_known": bool(
                egress is not None and egress in advertised_q),
            # the advertisement carries no in-service work, so no number can
            # be produced here at all
            "peer_in_service_remaining_bits_estimate": None,
            "not_advertised": ["peer_in_service_remaining_bits"],
            "prediction_method": "same_policy_on_advertised_peer_state",
        }, None

    def _record_decision(self, pkt: DataPacket, sat: int, kind: str,
                         candidates: list, chosen: str,
                         audit_candidates: list | None = None,
                         decision_id: int | None = None,
                         decision_started_at: float | None = None,
                         observation: dict | None = None) -> None:
        """Append one per-hop decision snapshot to the optional decision sink.

        Output only: never influences routing, learning, timing, or fates.
        ``candidates`` is the legal action set at decision time; for learning
        runs ``obs`` summarizes the observation actually used (dim, short
        content hash, L2 norm) so decision streams are diffable without
        storing full vectors.

        T1-FROZEN-LEDGER: the row keeps four DISTINCT objects instead of one
        ambiguous measurement instant --

        * observation_at_start -- what the decision was actually based on,
          with the measurement time of every contributing neighbour;
        * estimate_at_start -- the prediction that observation supported
          (None when it supported none; never backfilled from truth);
        * truth_at_commit -- the kernel's own truth when the action was
          committed (the pre-existing info_audit content);
        * truth_at_target -- folded later from the arrival snapshot, because
          it does not exist yet at commit time.

        The observation argument is the frozen half's snapshot when the
        caller is the frozen commit path; None means the decision re-read the
        state when the computation landed (refresh), which is then recorded
        as such rather than left to be inferred from the numbers.
        """
        if self.decision_sink is None:
            return
        obs = pkt.learning_state
        obs_summary = None
        if obs is not None:
            arr = np.ascontiguousarray(np.asarray(obs, dtype=np.float64))
            obs_summary = {
                "contract": self.cfg_rt["contract"],
                "dim": int(arr.size),
                "sha256_16": hashlib.sha256(arr.tobytes()).hexdigest()[:16],
                "l2_norm": float(np.linalg.norm(arr)),
            }
        considered = (candidates if audit_candidates is None
                      else audit_candidates)
        info_audit = self._decision_info_audit(pkt, sat, considered)
        committed_at = float(self.env.now)
        own_queue_bits = {d: int(lnk.data_bits + lnk.ctrl_bits)
                          for d, lnk in self.isls[sat].items()}
        if observation is None:
            obs_mode = "refresh"
            observation_record = self._observation_at_start(
                pkt, sat, committed_at, mode="refresh",
                # refresh re-reads the live state where the computation
                # landed: its observation instant IS the commit instant, and
                # the label -- not the numbers -- says so
                source="commit_time_state",
                own_queue_bits=own_queue_bits, considered=list(considered),
                legal=list(candidates), status=None, kind=kind, action=chosen)
            estimate, estimate_reason = self._estimate_at_start(
                pkt, sat, committed_at, chosen, kind)
        else:
            obs_mode = "frozen"
            observation_record = observation["observation"]
            estimate = observation["estimate"]
            estimate_reason = observation["estimate_unavailable_reason"]
        if estimate_reason is not None:
            observation_record = dict(observation_record)
            observation_record["estimate_unavailable_reason"] = estimate_reason
        truth_at_commit = {
            "schema": "leo-sim-truth-at-commit/v1",
            "t_observed": committed_at,
            "source": "kernel_state_at_commit",
            "mapping_status": info_audit["mapping_status"],
            "contract": info_audit["contract"],
            # the pre-existing audit content, unchanged and by reference: it
            # is the t1 truth, NOT a prediction available at t0
            "candidate_truth": info_audit["candidate_truth"],
            "cache_entries": info_audit["cache_entries"],
        }
        self.decision_sink.append({
            "t": committed_at,
            # When computation consumed simulated time these two differ:
            # t_decision_start is when the computation began, t is when the
            # chosen action was committed against the revalidated state.
            "t_decision_start": (committed_at
                                 if decision_started_at is None
                                 else float(decision_started_at)),
            "decision_id": decision_id,
            "state_version": self._state_version,
            "pid": pkt.pid,
            "src": pkt.src,
            "dst": pkt.dst,
            "sat": sat,
            "kind": kind,
            "policy": (self.cfg_rt["policy"] if self.learner is None
                       else f"{self.cfg_learning['algorithm']}:{self.cfg_rt['contract']}"),
            "candidates": list(candidates),
            "chosen": chosen,
            "own_queue_bits": own_queue_bits,
            "obs": obs_summary,
            "info_audit": info_audit,
            # T1-FROZEN-LEDGER: additive keys on the existing sink.  The
            # simulation reads none of them.
            "obs_mode": obs_mode,
            "observation_at_start": observation_record,
            "estimate_at_start": estimate,
            "truth_at_commit": truth_at_commit,
        })

    def _in_service_remaining(self, link, now: float):
        """In-service work on one ISL egress, with an honest method label.

        Returns (bits, remaining_bits, phase, is_control, method).  Linear
        extrapolation of the remaining work is only valid when the service
        rate cannot change mid-transmission; under MCS the rate is
        distance-dependent, so no number is produced rather than a wrong one.
        """
        if link.current is None:
            return 0, None, None, False, "no_service"
        current = link.current
        phase = link._svc_phase
        is_ctrl = isinstance(current, ControlPacket)
        if phase == "transmitting" and link._tx_started_at is not None:
            if self.rate_model == "constant":
                elapsed = max(0.0, now - float(link._tx_started_at))
                return (int(current.bits),
                        max(0.0, float(current.bits)
                            - elapsed * self.isl_rate_bps),
                        phase, is_ctrl, "linear_at_constant_rate")
            return (int(current.bits), None, phase, is_ctrl,
                    "unavailable_varying_rate")
        # transmission has not actually started: the whole packet is ahead
        return int(current.bits), float(current.bits), phase, is_ctrl, \
            "not_started_full_bits"

    def _egress_snapshot(self, sat: int) -> dict:
        """Realized per-egress state of one satellite, for scoring predictions.

        Taken when a packet ARRIVES at this satellite and before it
        re-decides, so it is the ground truth a decision-time prediction can
        be scored against: the difference between the two is exactly the
        stale-neighbour-state misalignment T1 studies.
        """
        now = float(self.env.now)
        out = {}
        for direction, link in self.isls[sat].items():
            bits, remaining, phase, is_ctrl, method = \
                self._in_service_remaining(link, now)
            out[direction] = {
                "peer": int(link.peer),
                "data_bits": int(link.data_bits),
                "ctrl_bits": int(link.ctrl_bits),
                "ctrl_packets": len(link.ctrl_q),
                "in_service_bits": bits,
                "in_service_remaining_bits": remaining,
                "in_service_remaining_method": method,
                "in_service_phase": phase,
                "in_service_is_control": bool(is_ctrl),
            }
        return out

    def _peer_downstream_truth(self, pkt: DataPacket, peer: int,
                               now: float) -> dict:
        """Audit-only prediction of the resource the packet would contend for.

        For one candidate direction this records the egress the PEER would
        pick for this destination under the same fixed downstream policy,
        together with that egress's current occupancy, control backlog and
        in-service remaining work.  This is the candidate-specific downstream
        truth that peer_egress_queue_bits -- a sum over ALL of the peer's
        directions -- cannot express (R8-A4 / T1-DOWNSTREAM-RESOURCE-PASS).

        Truth audit only: it reads the peer's own control cache and the true
        topology, and is never fed to a policy
        (mapping_status = truth_audit_not_learner_tensor).  Called only from
        the decision-sink path, so normal runs pay nothing.
        """
        is_destination = peer in self._serving_sats(pkt.dst)
        egress = None
        if not is_destination and self.isls[peer]:
            own_q = {d: lnk.data_bits + lnk.ctrl_bits
                     for d, lnk in self.isls[peer].items()}
            cands, status = routing.choose_next_hop(
                self.cfg_rt["policy"], peer, pkt.dst, now, self.geometry,
                self.topo, self.caches[peer], own_q, self.isl_rate_bps,
                model.propagation_delay_s,
                oracle_targets=([s for s in self._serving_sats(pkt.dst)
                                 if s != peer]
                                if self.cfg_rt["policy"] == "oracle" else None),
                best_only=False,
                reverse_adj=self._routing_reverse_adj,
                sorted_adj=self._routing_sorted_rev_adj)
            if status == "ok" and cands:
                egress = cands[0]
        link = self.isls[peer].get(egress) if egress is not None else None
        if link is None:
            in_service_bits, remaining, phase, is_ctrl = 0, None, None, False
            remaining_method = "no_service_on_predicted_egress"
        else:
            (in_service_bits, remaining, phase, is_ctrl,
             remaining_method) = self._in_service_remaining(link, now)
            if remaining_method == "no_service":
                remaining_method = "no_service_on_predicted_egress"
        return {
            "prediction_method": "same_policy_full_cache_at_decision_time",
            "peer_is_destination": bool(is_destination),
            "peer_egress_direction": egress,
            "peer_egress_link_id": (None if link is None
                                    else "isl:%d:%d" % (peer, link.peer)),
            "peer_egress_data_bits": int(link.data_bits) if link else 0,
            "peer_egress_ctrl_bits": int(link.ctrl_bits) if link else 0,
            "peer_egress_ctrl_packets": len(link.ctrl_q) if link else 0,
            "peer_in_service_bits": in_service_bits,
            "peer_in_service_phase": phase,
            "peer_in_service_is_control": bool(is_ctrl),
            "peer_in_service_remaining_bits": remaining,
            "peer_in_service_remaining_method": remaining_method,
        }

    def _decision_info_audit(self, pkt: DataPacket, sat: int,
                             candidates: list) -> dict:
        """Record decision-time physical truth without feeding it to a policy.

        The learner still receives exactly the vector built by
        ``_learning_observation``.  This optional sink is an audit stream for
        information-ladder experiments: it binds each legal direction to the
        direct geometry/rate/queue values available at that instant and, for
        learning runs, records the control-cache entry ages that contributed
        to the configured observation contract.  The method is called only
        when a decision sink is enabled, so normal runs retain the old path.
        """
        now = float(self.env.now)
        truth: dict[str, dict] = {}
        for direction in candidates:
            if direction == "deliver" or direction not in self.isls[sat]:
                continue
            link = self.isls[sat][direction]
            peer = link.peer
            if peer is None:
                continue
            distance = float(self.geometry.isl_range_km(sat, peer, now))
            rate = float(self._link_rate("isl", now, sat, peer=peer))
            geom_up = (not self.cfg_links["geometry_loss"]
                       or self.geometry.isl_available(sat, peer, now))
            ge_up = (not self.ge_enabled or not link.ge.is_down(now))
            reverse = next(
                (candidate for candidate in self.isls[peer].values()
                 if candidate.peer == sat), None)
            reverse_queue = (
                int(reverse.data_bits + reverse.ctrl_bits)
                if reverse is not None else None)
            peer_egress_queue = int(sum(
                candidate.data_bits + candidate.ctrl_bits
                for candidate in self.isls[peer].values()))
            fields = {
                "distance_km": distance,
                "rate_bps": rate,
                "available": bool(geom_up and ge_up and rate > 0
                                   and link.room(pkt.bits)),
                "peer_egress_queue_bits": peer_egress_queue,
                "reverse_link_queue_bits": reverse_queue,
                "topology_available": bool(
                    self.topo.get(sat, {}).get(direction) == peer),
                "downstream": self._peer_downstream_truth(pkt, peer, now),
            }
            truth[direction] = {
                "edge": [int(sat), int(peer)],
                **fields,
                "field_sources": {
                    field: {
                        "source": "direct_kernel_state",
                        "observed_at": now,
                        "age_s": 0.0,
                    }
                    for field in fields
                },
            }

        cache_entries: dict[str, dict] = {}
        contract = (self.cfg_rt["contract"]
                    if self.learner is not None else None)
        # R8-A6: a non-learning run has no observation contract to crop
        # against, but the node's actual knowledge is still exactly its valid
        # control cache.  Recording it is required for T1: the intended
        # first-version configuration is a deterministic router with learning
        # OFF, and the control-arrival time is the independent variable of the
        # stale-neighbour-state question.  Without that branch the timeline
        # field is MISSING for exactly that configuration.  The definition
        # lives in _observed_cache_entries so the truth audit and the
        # observation record can never disagree about what could be known.
        entries = self._observed_cache_entries(sat, now)
        for origin, entry in sorted(entries.items()):
            age = float(max(0.0, entry.aoi(now)))
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            cache_entries[str(origin)] = {
                "generated_at": float(entry.generated_at),
                "received_at": float(entry.received_at),
                "age_s": age,
                "hops": int(entry.hops),
                "source": "control_cache",
                "payload_field_age_s": {
                    str(field): age for field in sorted(payload)
                },
            }
        return {
            "schema": "leo-sim-decision-info/v1",
            "contract": contract,
            "mapping_status": "truth_audit_not_learner_tensor",
            "candidate_truth": truth,
            "cache_entries": cache_entries,
        }

    def _apply_forced_action(self, pkt: DataPacket, sat: int,
                             decision_id: int | None, action: str,
                             legal: list) -> str:
        """Override the chosen direction for a counterfactual replay.

        Strictly opt-in: only decision ids present in this kernel's
        forced_actions are touched, each at most once.  The forced direction
        must be legal AT THE BRANCH POINT, otherwise the replay would commit
        an action the kernel itself refused, so this fails loud instead of
        guessing.  A timeline milestone records the original and the forced
        choice, so a substitution is auditable rather than invisible.
        """
        if not self.forced_actions or decision_id is None:
            return action
        forced = self.forced_actions.get(decision_id)
        if forced is None or decision_id in self._forced_applied:
            return action
        if forced not in legal:
            raise KernelError(
                f"forced action {forced!r} for decision {decision_id} is not "
                f"legal at the branch point {sorted(legal)}")
        self._forced_applied.add(decision_id)
        if self.timeline_sink is not None:
            self._timeline("forced_action", pkt, decision_id, sat=int(sat),
                           original=action, forced=forced)
        return forced

    def decide_deferred(self, pkt: DataPacket, sat: int):
        """Decide after consuming simulated computation time (T1-COMPUTE-DELAY).

        Only used when execution.compute_delay_s > 0.  The decision body is
        NOT modified: it re-reads env.now, rebuilds the candidate set and
        re-checks geometry, rate and queue room, so the body that runs after
        this timeout IS the revalidation step -- the action is chosen against
        the state that exists when the computation lands, not the state that
        was visible when it began.  The decision row records both ends of the
        interval (t_decision_start and t).

        With the default delay of 0 this generator is never created: the call
        sites call _decide synchronously exactly as before, so historical runs
        stay bit-identical.
        """
        started = float(self.env.now)
        # frozen: the observation and the inference happen NOW, before the
        # timeout; only the legality check and the commit happen after it.
        observation = (self._observe_preferred_action(pkt, sat)
                       if self.obs_mode == "frozen" else None)
        yield self.env.timeout(self.compute_delay_s)
        self._decide(pkt, sat, compute_started_at=started,
                     observation=observation)

    # ------------------------------- T1-COMPUTE-DELAY frozen observation

    def _deliver_legal_now(self, pkt: DataPacket, sat: int, now: float) -> bool:
        """Is the deliver action legal at instant now?  Shared by the frozen
        observer (as part of the observation) and by the frozen commit check
        (as a legality gate)."""
        ep = self._ensure_endpoint(pkt.dst)
        link = ep.links.get(sat)
        if not (link is not None and link.state == "active"
                and self.geometry.gsl_available(sat, ep.lat, ep.lon, now)):
            return False
        if (self.rate_model == "mcs"
                and self._link_rate("downlink", now, sat, ep=ep) <= 0):
            return False
        return self.downlinks[sat].room(pkt.bits)

    def _forward_legal_now(self, pkt: DataPacket, sat: int, now: float,
                           cands) -> list:
        """Which of these directions is legal at instant now: geometry up, a
        non-zero rate under the MCS model, and queue room for this packet."""
        legal = []
        for d in cands:
            link = self.isls[sat][d]
            geom_up = (not self.cfg_links["geometry_loss"]
                       or self.geometry.isl_available(sat, link.peer, now))
            if not geom_up:
                continue
            if (self.rate_model == "mcs"
                    and self._link_rate("isl", now, sat, peer=link.peer) <= 0):
                continue
            if link.room(pkt.bits):
                legal.append(d)
        return legal

    def _observe_preferred_action(self, pkt: DataPacket, sat: int) -> dict:
        """Observation + inference half of the frozen mode: decide what to do
        from the state visible NOW, committing nothing.

        Kept deliberately parallel to _decide's choice rule for the
        deterministic router -- deliver wins whenever it is legal, otherwise
        the first legal candidate in choose_next_hop's order -- so that the two
        implementations cannot drift apart unnoticed (test_frozen_observation
        asserts the equivalence directly).
        """
        now = self.env.now
        own_q = {d: lnk.data_bits + lnk.ctrl_bits
                 for d, lnk in self.isls[sat].items()}
        if self._deliver_legal_now(pkt, sat, now):
            kind, action = "deliver", "deliver"
            legal, cands, status = ["deliver"], ["deliver"], "ok"
        else:
            cands, status = routing.choose_next_hop(
                self.cfg_rt["policy"], sat, pkt.dst, now, self.geometry,
                self.topo, self.caches[sat], own_q, self.isl_rate_bps,
                model.propagation_delay_s,
                oracle_targets=([s for s in self._serving_sats(pkt.dst)
                                 if s != sat]
                                if self.cfg_rt["policy"] == "oracle" else None),
                best_only=False,
                reverse_adj=self._routing_reverse_adj,
                sorted_adj=self._routing_sorted_rev_adj,
                rate_from_propagation=(
                    (lambda prop_s: link_budget.mcs_rate_bps(
                        prop_s * model.C_KM_S, self.rf_isl, self.mcs_table))
                    if self.rate_model == "mcs" else None),
                cache_hops=None)
            cands = [d for d in cands if self.topo[sat][d] not in pkt.path]
            legal = self._forward_legal_now(pkt, sat, now, cands)
            if legal:
                kind, action = "forward", legal[0]
            else:
                kind, action, legal = "hold", None, []
        # T1-FROZEN-LEDGER: the observation the commit half will be judged
        # against, plus the only prediction it legally supports.  Both are
        # read-only records: they never enter the routing decision.
        observation = self._observation_at_start(
            pkt, sat, now, mode="frozen",
            source="frozen_snapshot_before_compute",
            own_queue_bits=own_q, considered=cands, legal=legal,
            status=status, kind=kind, action=action)
        estimate, estimate_reason = self._estimate_at_start(
            pkt, sat, now, action, kind)
        return {"t_observe": now, "kind": kind, "action": action,
                "legal": legal, "cands": cands, "status": status,
                "observation": observation, "estimate": estimate,
                "estimate_unavailable_reason": estimate_reason}

    def _decide_from_frozen_observation(self, pkt: DataPacket, sat: int,
                                        obs: dict,
                                        compute_started_at: float | None = None
                                        ) -> None:
        """Commit half of the frozen mode.

        The action was inferred from the observation taken at obs["t_observe"];
        this only asks whether that action is still LEGAL.  A rejected action
        is never silently replaced by an optimum re-solved against fresh state
        -- that is precisely the refresh semantics this mode exists to be
        distinguished from -- so the packet is parked and must pay for another
        computation before it can act.  The rejection is recorded on the
        timeline sink (an additive output channel) rather than in a new
        mechanism counter, which would change the receipt key set.
        """
        now = self.env.now
        decision_id = self._next_decision_id()
        if self.timeline_sink is not None and pkt.decision_id is not None:
            self._timeline("redecision", pkt, decision_id,
                           prev_decision_id=pkt.decision_id, sat=int(sat),
                           obs_mode="frozen", observed_at=obs["t_observe"])
        if pkt.deadline is not None and now >= pkt.deadline:
            self._fail(pkt, "DATA_DEADLINE_EXPIRED", decision_id=decision_id,
                       observation=obs["observation"])
            return
        if len(pkt.path) > self.cfg_rt["max_hops"]:
            self._fail(pkt, "NO_ROUTE", decision_id=decision_id,
                       observation=obs["observation"])
            return
        kind = obs["kind"]
        if kind == "deliver" and self._deliver_legal_now(pkt, sat, now):
            pkt.decision_id = decision_id
            self._record_decision(pkt, sat, "deliver", ["deliver"], "deliver",
                                  decision_id=decision_id,
                                  decision_started_at=compute_started_at,
                                  observation=obs)
            self.downlinks[sat].put(pkt)
            return
        if kind == "forward":
            action = obs["action"]
            if self._forward_legal_now(pkt, sat, now, [action]):
                pkt.decision_id = decision_id
                self._record_decision(
                    pkt, sat, "forward", obs["legal"], action,
                    audit_candidates=obs["cands"], decision_id=decision_id,
                    decision_started_at=compute_started_at,
                    observation=obs)
                self.isls[sat][action].put_data(pkt)
                return
            if self.timeline_sink is not None:
                # a rejected commit is still an ATTEMPT: it keeps the
                # observation it was inferred from and the reason it died, or
                # the ledger would count only the attempts that happened to
                # succeed and the frozen cost would be invisible
                self._timeline("commit_rejected", pkt, decision_id,
                               sat=int(sat), inferred_at=obs["t_observe"],
                               t_observed=obs["t_observe"],
                               action=action,
                               reason="action_no_longer_legal",
                               obs_mode="frozen",
                               observation_at_start=obs["observation"],
                               estimate_at_start=obs["estimate"],
                               estimate_unavailable_reason=(
                                   obs["estimate_unavailable_reason"]))
        elif kind == "deliver":
            if self.timeline_sink is not None:
                self._timeline("commit_rejected", pkt, decision_id,
                               sat=int(sat), inferred_at=obs["t_observe"],
                               t_observed=obs["t_observe"],
                               action="deliver",
                               reason="deliver_no_longer_legal",
                               obs_mode="frozen",
                               observation_at_start=obs["observation"],
                               estimate_at_start=obs["estimate"],
                               estimate_unavailable_reason=(
                                   obs["estimate_unavailable_reason"]))
        else:
            if self.timeline_sink is not None:
                self._timeline("frozen_inferred_hold", pkt, decision_id,
                               sat=int(sat), inferred_at=obs["t_observe"],
                               t_observed=obs["t_observe"],
                               status=obs["status"],
                               reason="observation_inferred_hold",
                               obs_mode="frozen",
                               observation_at_start=obs["observation"],
                               estimate_at_start=obs["estimate"],
                               estimate_unavailable_reason=(
                                   obs["estimate_unavailable_reason"]))
        # Park it: the inferred action is gone, and re-solving against the
        # state that killed it is exactly what this mode refuses to do.
        if pkt.deadline is not None:
            self._schedule_pending_wake(sat, pkt.deadline)
        self._hold_packet(sat, pkt, decision_id=decision_id)

    def _decide(self, pkt: DataPacket, sat: int,
                compute_started_at: float | None = None,
                observation: dict | None = None) -> None:
        if observation is not None:
            self._decide_from_frozen_observation(
                pkt, sat, observation, compute_started_at)
            return
        now = self.env.now
        decision_id = self._next_decision_id()
        if self.timeline_sink is not None and pkt.decision_id is not None:
            # the packet was COMMITTED at least once before (pkt.decision_id
            # is written only by the two commit sites), possibly at another
            # satellite.  The explicit link is what the old (t, pid, sat,
            # kind) row key could not express.
            self._timeline("redecision", pkt, decision_id,
                           prev_decision_id=pkt.decision_id, sat=int(sat),
                           obs_mode=self.obs_mode)
        if pkt.deadline is not None and now >= pkt.deadline:
            self._fail(pkt, "DATA_DEADLINE_EXPIRED", decision_id=decision_id)
            return
        if len(pkt.path) > self.cfg_rt["max_hops"]:
            self._fail(pkt, "NO_ROUTE", decision_id=decision_id)
            return
        ep = self._ensure_endpoint(pkt.dst)
        link = ep.links.get(sat)
        if (link is not None and link.state == "active"
                and self.geometry.gsl_available(sat, ep.lat, ep.lon, now)):
            if (self.rate_model == "mcs"
                    and self._link_rate("downlink", now, sat, ep=ep) <= 0):
                # D1: no feasible MCS rate on the downlink, so "deliver" is
                # not a legal action now.  Park in pending (re-decided every
                # time_step) exactly like a temporarily unavailable ISL
                # direction, instead of enqueueing into a queue that cannot
                # be served; the deadline check above still applies on every
                # re-decision.
                # D1 F4 attribution: only a zero MCS rate with geometry AND
                # GE up counts as an MCS hold; if the GSL GE is down the
                # deferral belongs to the outage, not to MCS (receipt
                # effective.mcs recomputes from this counter).
                ge = self._gsl_ge(sat, ep.cell)
                ge_up = not self.ge_enabled or not ge.is_down(now)
                if self.ge_enabled:
                    self.mech["ge_gsl_queries"] += 1
                if ge_up:
                    self.mech["mcs_zero_rate_holds"] += 1
                    nxt = self.geometry.next_slant_range_under(
                        sat, ep.lat, ep.lon, self.rate_max_downlink_km,
                        now, self.horizon)
                    if nxt is not None:
                        self._schedule_pending_wake(sat, nxt)
                else:
                    nxt_up = ge.next_up(now)
                    if nxt_up <= self.horizon:
                        self._schedule_pending_wake(sat, nxt_up)
                if pkt.deadline is not None:
                    self._schedule_pending_wake(sat, pkt.deadline)
                self._hold_packet(sat, pkt, decision_id=decision_id)
                return
            dl = self.downlinks[sat]
            if dl.room(pkt.bits):
                if self.learner is not None:
                    action = self._learning_action(
                        pkt, sat,
                        {a: a == "deliver" for a in _learning.ACTIONS},
                    )
                    if action != "deliver":
                        raise KernelError("DDQN selected a non-deliver action from deliver-only mask")
                if decision_id in self.forced_actions:
                    raise KernelError(
                        f"decision {decision_id} is a deliver decision; the "
                        f"first version of the counterfactual harness only "
                        f"forces a choice among the ISL forward candidates")
                pkt.decision_id = decision_id
                self._record_decision(pkt, sat, "deliver", ["deliver"],
                                      "deliver", decision_id=decision_id,
                                      decision_started_at=compute_started_at)
                dl.put(pkt)
            else:
                self._fail(pkt, "ACCESS_QUEUE_OVERFLOW",
                           decision_id=decision_id)
            return
        own_q = {d: lnk.data_bits + lnk.ctrl_bits for d, lnk in self.isls[sat].items()}
        # The action/decision gate is observable information too.  A learning
        # arm may not use destination or path metrics from cache entries that
        # its configured observation crops away (R1-A2).  C1 is intrinsically
        # one-hop; other contracts use obs_hops=None for the full vis_k cache.
        cache_hops = None
        if self.learner is not None:
            cache_hops = (1 if self.cfg_rt["contract"] == "C1"
                          else self.cfg_learning.get("obs_hops"))
        cands, status = routing.choose_next_hop(
            self.cfg_rt["policy"], sat, pkt.dst, now, self.geometry, self.topo,
            self.caches[sat], own_q, self.isl_rate_bps, model.propagation_delay_s,
            # oracle_targets is consumed only by the oracle policy; compute it
            # only there instead of scanning serving satellites every decision
            oracle_targets=([s for s in self._serving_sats(pkt.dst) if s != sat]
                            if self.cfg_rt["policy"] == "oracle" else None),
            # learning must choose among ALL local legal directions: the
            # heuristic only orders candidates, it never pre-clips the
            # learner's action set (otherwise DDQN is a tie-breaker over the
            # heuristic-best path and cannot learn to deviate from it)
            best_only=False,
            reverse_adj=self._routing_reverse_adj,
            sorted_adj=self._routing_sorted_rev_adj,
            rate_from_propagation=(
                (lambda prop_s: link_budget.mcs_rate_bps(
                    prop_s * model.C_KM_S, self.rf_isl, self.mcs_table))
                if self.rate_model == "mcs" else None),
            cache_hops=cache_hops)
        if status == "unreachable":
            self._fail(pkt, "NO_ROUTE", decision_id=decision_id)
            return
        if status == "no_info":
            if not self.cfg_cp["enabled"] and self.cfg_rt["policy"] != "oracle":
                self._fail(pkt, "NO_ROUTE", decision_id=decision_id)
            else:
                if pkt.deadline is not None:
                    self._schedule_pending_wake(sat, pkt.deadline)
                self._hold_packet(sat, pkt,
                                  decision_id=decision_id)  # wait
            return
        # loop avoidance: never forward back onto a satellite already visited
        cands = [d for d in cands if self.topo[sat][d] not in pkt.path]
        unavailable = False
        rate_blocked = False
        recover_at = float("inf")
        legal = []
        for d in cands:
            link = self.isls[sat][d]
            geom_up = (not self.cfg_links["geometry_loss"]
                       or self.geometry.isl_available(sat, link.peer, now))
            if not geom_up:
                unavailable = True
                continue
            if self.rate_model == "mcs" and self._link_rate(
                    "isl", now, sat, peer=link.peer) <= 0:
                unavailable = True
                # D1 F4 attribution: an ISL GE outage is the actual blocker
                # when it overlaps a zero rate; only geometry-up AND GE-up
                # zero-rate counts as an MCS hold.
                ge_up = not self.ge_enabled or not link.ge.is_down(now)
                if self.ge_enabled:
                    self.mech["ge_isl_queries"] += 1
                if ge_up:
                    rate_blocked = True
                    nxt = self.geometry.next_isl_range_under(
                        sat, link.peer, self.rate_max_isl_km,
                        now, self.horizon)
                    if nxt is not None:
                        recover_at = min(recover_at, nxt)
                else:
                    nxt_up = link.ge.next_up(now)
                    if nxt_up <= self.horizon:
                        recover_at = min(recover_at, nxt_up)
                continue
            if link.room(pkt.bits):
                legal.append(d)
        if legal:
            if self.learner is not None:
                mask = {a: a in legal for a in _learning.ACTIONS}
                action = self._learning_action(pkt, sat, mask)
                if action not in legal:
                    # fail loud like the deliver-only branch: a learner that
                    # returns an action outside the legal mask must never
                    # silently overflow an ISL queue (put_data does not
                    # re-check room())
                    raise KernelError(
                        f"learner selected action {action!r} outside the "
                        f"legal mask {sorted(legal)}")
            else:
                action = legal[0]
            action = self._apply_forced_action(pkt, sat, decision_id, action,
                                               legal)
            pkt.decision_id = decision_id
            self._record_decision(pkt, sat, "forward", legal, action,
                                  audit_candidates=cands,
                                  decision_id=decision_id,
                                  decision_started_at=compute_started_at)
            self.isls[sat][action].put_data(pkt)
            return
        if unavailable:
            if rate_blocked:
                self.mech["mcs_zero_rate_holds"] += 1
            if pkt.deadline is not None:
                self._schedule_pending_wake(sat, pkt.deadline)
            if recover_at != float("inf"):
                self._schedule_pending_wake(sat, recover_at)
            self._hold_packet(sat, pkt,
                              decision_id=decision_id)  # unavailable: wait
            return
        if cands:
            self._fail(pkt, "ISL_QUEUE_OVERFLOW", decision_id=decision_id)
        else:
            self._fail(pkt, "NO_ROUTE",
                       decision_id=decision_id)  # every candidate loops

    def _redecide_pending(self, sat: int):
        if not self.pending[sat]:
            return
        waiting = self.pending[sat].take_ready(self.env.now)
        for pkt in waiting:
            if self.compute_delay_s > 0:
                self.env.process(self.decide_deferred(pkt, sat))
            else:
                self._decide(pkt, sat)

    def _schedule_pending_wake(self, sat: int, at: float) -> None:
        """Certified re-decision for parked packets on `sat` (D1 precise
        wait).  A parked packet is normally re-decided by the time_step
        ticker, but a deadline or an MCS/GE recovery has a certified event
        time; schedule a one-shot wake at the earliest such time so expiry
        and recovery are exact instead of degraded to polling."""
        if at is None or at <= self.env.now or at >= self.horizon:
            return
        if self._pending_wake[sat] is None or at < self._pending_wake[sat]:
            self._pending_wake[sat] = at
            self.env.process(self._pending_wake_once(sat, at))

    def _pending_wake_once(self, sat: int, at: float):
        yield self.env.timeout(max(0.0, at - self.env.now))
        if self._pending_wake[sat] == at:
            self._pending_wake[sat] = None
            self._redecide_pending(sat)

    # ------------------------------------------------------- F2 node cost
    def _node_process(self, pkt: DataPacket, sat: int, via: str):
        """Occupy the arriving satellite node for the configured F2 cost.

        One satellite visit == one receive/process/schedule occupancy.  The
        packet has physically arrived (its ``propagation_arrival`` is already
        recorded, and so is ``satellite_ingress`` on the uplink path) but it is
        not yet available to the forwarding function; the occupancy ends where
        the decision stage begins.

        The interval is recorded as the milestone pair ``node_process_start`` /
        ``node_process_end`` on the timeline sink, the same output-only channel
        frozen mode uses for its rejected commits.  It is deliberately NOT
        folded into any frozen stage:

        * reusing queue/holding would make the cost indistinguishable from
          queueing, and reusing service_start/service_window would put it into
          tx_s -- the same variable as the PHY bandwidth (F3);
        * a new packet_events kind would extend the closed kind whitelist in
          metrics.py:90-218 (a frozen contract, not this task's to change),
          and a new mechanism counter would extend
          receipt.MECHANISM_COUNTER_KEYS.

        Scope (deliberate): data packets only, one cost per satellite visit --
        uplink ingress and every ISL arrival.  The destination ground endpoint
        and the control plane are not satellite-node processing and are left
        untouched, so F2 cannot perturb the control plane or the delivery
        terminus.

        With the default delay of 0 the generator returns before touching the
        clock, so no event is created, the same-time ordering of the arrival
        path is preserved, and historical runs stay bit-identical.
        """
        delay = self.node_process_delay_s
        if delay <= 0:
            return
        started_at = float(self.env.now)
        self._timeline("node_process_start", pkt, pkt.decision_id,
                       sat=int(sat), via=via, node_cost_s=delay)
        yield self.env.timeout(delay)
        self._timeline("node_process_end", pkt, pkt.decision_id,
                       sat=int(sat), via=via, started_at=started_at,
                       node_cost_s=delay)

    def _ingress_after_prop(self, pkt: DataPacket, sat: int, prop: float):
        yield self.env.timeout(prop)
        self._in_flight.pop(pkt.pid, None)
        self._metric_propagation_arrival(pkt)
        self._metric_satellite_ingress(pkt, sat)
        if pkt.deadline is not None and self.env.now > pkt.deadline:
            self._fail(pkt, "DATA_DEADLINE_EXPIRED")
            return
        pkt.path.append(sat)
        self._note_busy(pkt.dst)  # new downlink demand may have appeared
        yield from self._node_process(pkt, sat, "uplink")
        if self.compute_delay_s > 0:
            yield from self.decide_deferred(pkt, sat)
        else:
            self._decide(pkt, sat)

    def _isl_arrive_after_prop(self, pkt: DataPacket, sat: int, prop: float):
        yield self.env.timeout(prop)
        self._in_flight.pop(pkt.pid, None)
        self._metric_propagation_arrival(pkt, sat)
        if pkt.deadline is not None and self.env.now > pkt.deadline:
            self._fail(pkt, "DATA_DEADLINE_EXPIRED")
            return
        pkt.path.append(sat)
        self._note_busy(pkt.dst)  # new downlink demand may have appeared
        yield from self._node_process(pkt, sat, "isl")
        if self.compute_delay_s > 0:
            yield from self.decide_deferred(pkt, sat)
        else:
            self._decide(pkt, sat)

    def _deliver_after_prop(self, pkt: DataPacket, sat: int, prop: float):
        yield self.env.timeout(prop)
        self._in_flight.pop(pkt.pid, None)
        self._metric_propagation_arrival(pkt)
        now = self.env.now
        if pkt.deadline is not None and now >= pkt.deadline:
            self._fail(pkt, "DATA_DEADLINE_EXPIRED")
            return
        self._finish_learning_transition(
            pkt, np.zeros(_learning.CONTRACT_DIMS[self.cfg_rt["contract"]]),
            {a: False for a in _learning.ACTIONS}, True,
            # the arrival reward (legacy ArriveReward,
            # ANALYSIS/REWARD-DIFF-20260816.md) exists only here, at real
            # delivery — never at the deliver decision
            terminal_reward=float(self.cfg_learning["arrive_reward"]),
        )
        self.ledger.record(pkt.pid, "DELIVERED", pkt.bits)
        self.deliveries[pkt.pid] = {"delivered_at": now, "path": list(pkt.path)}
        self._metric_delivered(pkt)
        self._log("delivered", pid=pkt.pid, sat=sat)

    # ----------------------------------------------------------------- fates
    def _fail(self, pkt, fate: str, decision_id: int | None = None,
              observation: dict | None = None):
        if self.timeline_sink is not None and decision_id is not None:
            # R8-A7: a failed attempt is still a decision; without this row
            # its decision_id would be consumed and vanish.  The observation
            # the attempt was based on is attached when the caller has it
            # (frozen mode), so a failure is never an unattributed attempt.
            extra = {} if observation is None else {
                "observation_at_start": observation}
            self._timeline("fail", pkt, decision_id, fate=fate,
                           obs_mode=self.obs_mode, **extra)
        if isinstance(pkt, ControlPacket):
            self.ctrl_ledger.record(pkt.iid, fate, pkt.bits)
        else:
            # A forward whose ISL service already started has settled its
            # realized M1 queue reward at service start (_transmit). A later
            # mid-service failure (geometry/GE/deadline/retire) must not
            # erase that realized reward: keep it, and settle 0 only when no
            # reward was realized yet (failure before service start).
            self._finish_learning_transition(
                pkt,
                np.zeros(_learning.CONTRACT_DIMS[self.cfg_rt["contract"]]),
                {a: False for a in _learning.ACTIONS}, True,
                terminal_reward=(None
                                 if pkt.learning_reward is not None
                                 else 0.0),
            )
            self.ledger.record(pkt.pid, fate, pkt.bits)
            self._log("fate", pid=pkt.pid, fate=fate)

    # ------------------------------------------------------------------- run
    def run(self) -> dict:
        interrupted = False
        error = None
        events = 0
        try:
            while True:
                # simpy.peek() returns inf when the queue is empty; no
                # exception is expected here, so a raise must propagate
                # (fail loud) instead of being converted into a natural end
                t_next = self.env.peek()
                if t_next > self.horizon or t_next == math.inf:
                    break
                v_before = self._state_version
                self.env.step()
                # exactly one version bump per event step: handlers that
                # already bumped (topology recompute, plan application)
                # count as this step's bump
                if self._state_version == v_before:
                    self._state_version += 1
                events += 1
                if events > self.cfg_ex["max_events"]:
                    raise CapExceeded("max_events exceeded")
        except (CapExceeded, fates.FateError) as exc:
            interrupted = True
            error = f"{type(exc).__name__}: {exc}"
        # packets still in service at stop occupied their link up to the end
        for s in range(self.num_sats):
            for srv in (self.uplinks[s], self.downlinks[s]):
                if srv._svc is not None:
                    t0, key = srv._svc
                    self.occupied[key] += self.env.now - t0
        for lnk in self._all_isls():
            if lnk._svc is not None:
                t0, key = lnk._svc
                self.occupied[key] += self.env.now - t0
        stop_time = self.env.now  # == horizon on a natural end (closer)
        # Settle deadline fates no sweep reached before the stop: a packet
        # parked behind an unavailable link (e.g. a D1 zero-rate gate) whose
        # deadline fell between the last tick and the horizon must expire as
        # DATA_DEADLINE_EXPIRED, never settle as IN_SYSTEM_AT_STOP.
        for ep in self.endpoints.values():
            self._sweep_endpoint_queue(ep)
        for s in range(self.num_sats):
            self._sweep_downlink_queues(s)
            for pkt in list(self.pending[s]):
                if pkt.deadline is not None and stop_time >= pkt.deadline:
                    self.pending[s].remove(pkt, stop_time)
                    self._fail(pkt, "DATA_DEADLINE_EXPIRED")
            for lnk in self.isls[s].values():
                lnk._expire_waiting()
        # settle all queue-area integrals at the exact stop time
        for ep in self.endpoints.values():
            ep.area.close(stop_time)
        for s in range(self.num_sats):
            self.holding_areas[s].close(stop_time)
            self.downlinks[s].area.close(stop_time)
        for lnk in self._all_isls():
            lnk.data_area.close(stop_time)
            lnk.ctrl_area.close(stop_time)
        self._purge_drained_retired()
        self._truncate_drained_capacity_windows()
        queue_area = {
            "uplink": sum(ep.area.area for ep in self.endpoints.values()),
            "downlink": sum(self.downlinks[s].area.area for s in range(self.num_sats)),
            "holding": sum(area.area for area in self.holding_areas),
            "isl_data": sum(lnk.data_area.area for lnk in self._all_isls()),
            "isl_ctrl": sum(lnk.ctrl_area.area for lnk in self._all_isls()),
        }
        self.access_stats["waiting_at_stop"] = sum(
            len(q) for q in self.access_wait)
        self._close_learning_at_stop()
        self.ledger.close_at_stop()
        self.ctrl_ledger.close_at_stop()
        if interrupted:
            totals = self.ledger.totals()
            ctrl_totals = self.ctrl_ledger.totals()
        else:
            totals = self.ledger.check_conservation()
            ctrl_totals = self.ctrl_ledger.check_conservation()
        requested = {
            "policy": self.cfg_rt["policy"],
            "association": self.cfg_access["association"],
            "rate_model": self.rate_model,
            "ge_enabled": self.ge_enabled,
            "control_enabled": bool(self.cfg_cp["enabled"]),
            "monitor": self.monitor,
            "learning_algorithm": (
                self.cfg_learning["algorithm"]
                if self.learner is not None else "none"),
            "learning_mode": (
                self.learner.mode if self.learner is not None else "train"),
            "topology_recompute_interval_s": self.cfg_topo["recompute_interval_s"],
            "topology_matching": self.cfg_topo["matching"],
        }
        ctrl_fc = self.ctrl_ledger.fate_counts()
        control_counters = {
            "snapshots_created": self.mech["control_snapshots"],
            "registered": self.mech["control_registered"],
            "entered_queue": self.mech["control_entered_queue"],
            "transmission_started": self.mech["control_tx_started"],
            "transmission_completed": self.mech["control_tx_completed"],
            "arrived": ctrl_fc["DELIVERED"],
            "expired": ctrl_fc["CONTROL_EXPIRED"],
            "lost": ctrl_fc["RANDOM_OUTAGE_IN_FLIGHT"],
            "geometry_lost": ctrl_fc["GEOMETRY_LOSS_IN_FLIGHT"],
            "overflow": ctrl_fc["QUEUE_OVERFLOW"],
            "duplicate": ctrl_fc["DUPLICATE"],
            "in_system": ctrl_fc["IN_SYSTEM_AT_STOP"],
        }
        # a requested mechanism is EFFECTIVE only if it really entered the
        # send path: control requires a real ControlPacket admitted to a link
        # queue (a bare snapshot proves nothing); GE requires the channel to
        # have been consulted on a service path; MBB requires a real event.
        effective = {
            "control_plane": self.mech["control_entered_queue"] > 0,
            # MCS is effective when it paced a transmission OR held a packet
            # behind the zero-rate gate — an all-zero-rate run is precisely
            # the mechanism changing the outcome.
            "mcs": (self.mech["mcs_rate_samples"] > 0
                    or self.mech["mcs_zero_rate_holds"] > 0),
            "ge": self.ge_enabled and (
                self.mech["ge_gsl_queries"] + self.mech["ge_isl_queries"] > 0),
            "mbb": self.mech["mbb_events"] > 0,
            "dynamic_topology": (
                self.mech["topo_recomputes"] > 0
                or self.mech["topo_dynamic_init"]),
            "learning": False,
            "ge_gsl_queries": self.mech["ge_gsl_queries"],
            "ge_isl_queries": self.mech["ge_isl_queries"],
            "ge_waits": self.mech["ge_waits"],
            "ge_failures": self.mech["ge_failures"],
            "mbb_events": self.mech["mbb_events"],
        }
        learning_result = None
        if self.learner is not None:
            learning_result = self.learner.diagnostics()
            if self.learning_out_dir is not None:
                learning_result = self.learner.save_and_verify(
                    self.learning_out_dir)
            self.mech["learning_decisions"] = self.learner.decisions
            self.mech["learning_transitions"] = self.learner.transitions
            self.mech["learning_train_steps"] = self.learner.train_steps
            effective["learning"] = (
                self.learner.train_steps > 0 if self.learner.mode == "train"
                else self.learner.decisions > 0
            )
        # A local kernel cannot self-authorize a scientific result. Mechanism
        # effectiveness is reported above; research eligibility requires an
        # externally anchored review/authorization/deployment receipt and is
        # therefore always false for this ungoverned runtime entry point.
        research_eligible = False
        # A propagation start can legitimately have no arrival event when
        # the horizon cuts the flight short or the packet is assigned an
        # explicit in-flight/deadline/loss fate.  Pass only those packet ids
        # from the authoritative fate ledger; all other orphan starts remain
        # a hard metrics error.
        non_arrival_pids = {
            pid for pid, fate in self.ledger._fates.items()
            if fate in {
                "IN_SYSTEM_AT_STOP",
                "GEOMETRY_LOSS_IN_FLIGHT",
                "RANDOM_OUTAGE_IN_FLIGHT",
                "DATA_DEADLINE_EXPIRED",
            }
        }
        congestion_metrics = metrics.summarize(
            self.packet_events, self.link_service_windows,
            available_capacity_windows=self.link_available_windows,
            non_arrival_pids=non_arrival_pids,
            access_boundary=True)
        result = {
            "natural_end": not interrupted,
            "interrupted": interrupted,
            "error": error,
            "events_processed": events,
            "horizon_s": self.horizon,
            "stop_time_s": stop_time,
            "fates": dict(self.ledger._fates),
            "fate_counts": self.ledger.fate_counts(),
            "totals": totals,
            "deliveries": self.deliveries,
            "occupied": dict(self.occupied),
            "queue_area_bits_s": queue_area,
            "access": dict(self.access_stats),
            "service_log": self.service_log,
            "packet_events": list(self.packet_events),
            "link_service_windows": list(self.link_service_windows),
            "link_available_windows": list(self.link_available_windows),
            "congestion_metrics": congestion_metrics,
            "handover": {"events": self.handover_events},
            "control": {
                "counters": control_counters,
                # deprecated mirror of counters["snapshots_created"], kept so
                # frozen external probes still execute
                "generated": control_counters["snapshots_created"],
                "bits": dict(self.ctrl_ledger.bits),
                "totals": ctrl_totals,
                "fate_counts": ctrl_fc,
                "instances": self.ctrl_ledger.instances(),
                "cache_expired_open": sum(
                    c.count_expired(self.env.now) for c in self.caches),
            },
            "caches": {s: {o: {"generated_at": e.generated_at,
                               "received_at": e.received_at,
                               "visible_cells": list(e.payload.get("visible_cells", ())),
                               "serve_cells": list(e.payload.get("serve_cells", ())),
                               "aoi": e.aoi(self.env.now),
                               "valid": e.valid_at(self.env.now)}
                           for o, e in c._entries.items()}
                       for s, c in enumerate(self.caches)},
            "mechanisms": {"requested": requested, "effective": effective},
            "learning": learning_result,
            "mechanism_counters": dict(self.mech),
            "research_eligible": research_eligible,
            "monitor_log": list(self.monitor_log),
            "routing_label": routing.ORACLE_LABEL if self.cfg_rt["policy"] == "oracle" else None,
        }
        return result


def run_simulation(resolved: dict, rows: list[dict], geometry=None,
                   learning_out_dir=None, decision_sink=None,
                   timeline_sink=None, forced_actions=None) -> dict:
    kern = Kernel(resolved, rows, geometry=geometry,
                  learning_out_dir=learning_out_dir,
                  decision_sink=decision_sink, timeline_sink=timeline_sink,
                  forced_actions=forced_actions)
    return kern.run()
