"""Control-plane state dissemination for leo_sim.

ControlPackets are real packets: they occupy directional ISL service time with
non-preemptive priority over queued data, propagate at most vis_k actual hops,
carry TTL/AoI, and are the ONLY source of remote state. A satellite's local
cache contains exactly what has actually arrived and not expired — never
future geometry, never hidden global queues.
"""
from __future__ import annotations

import math


class CacheEntry:
    """One arrived advertisement.

    Time contract (shared by the cache, routing and the learning contracts):
    all timestamps must be finite and ttl_s > 0; an entry is valid at `now`
    iff generated_at <= received_at <= now <= generated_at + ttl_s — i.e. it
    has ACTUALLY arrived (future arrivals are not information) and has not
    expired. AoI is measured from generation.
    """

    __slots__ = ("origin", "payload", "generated_at", "received_at", "ttl_s", "hops")

    def __init__(self, origin, payload, generated_at, received_at, ttl_s, hops=0):
        for name, v in (("generated_at", generated_at),
                        ("received_at", received_at), ("ttl_s", ttl_s)):
            if not isinstance(v, (int, float)) or isinstance(v, bool) \
                    or not math.isfinite(v):
                raise ValueError(f"CacheEntry {name} must be a finite number: {v!r}")
        if ttl_s <= 0:
            raise ValueError(f"CacheEntry ttl_s must be > 0: {ttl_s!r}")
        if received_at < generated_at:
            raise ValueError(
                f"CacheEntry received_at {received_at} < generated_at {generated_at}")
        self.origin = origin
        self.payload = payload
        self.generated_at = float(generated_at)
        self.received_at = float(received_at)
        self.ttl_s = float(ttl_s)
        self.hops = hops  # actual ISL hops the advertisement travelled

    def valid_at(self, now: float) -> bool:
        # not-yet-generated, not-yet-arrived (future received_at) and expired
        # entries are all invalid
        if not isinstance(now, (int, float)) or isinstance(now, bool) \
                or not math.isfinite(now):
            raise ValueError(f"valid_at: now must be finite: {now!r}")
        return self.generated_at <= self.received_at <= now \
            <= self.generated_at + self.ttl_s

    def aoi(self, now: float) -> float:
        return now - self.generated_at


#: how many actually-arrived advertisements per origin are retained for the
#: T1 state-time predictor.  The CURRENT cache entry is what routing may use;
#: this bounded history is output-only evidence for bounded_linear, which needs
#: at least two ordered source measurements of the SAME resource.  It never
#: enters a routing decision.
HISTORY_LIMIT = 64


class LocalCache:
    """Per-satellite cache of arrived control information, keyed by origin.

    Besides the current entry (the only thing routing may read), each origin
    keeps a bounded history of the advertisements that ACTUALLY ARRIVED, in
    arrival order.  The history records what was received even when it is stale
    or duplicated, so a predictor cannot pretend the sequence did not happen;
    consumers must order and deduplicate it by (resource, source time).
    """

    def __init__(self, history_limit: int = HISTORY_LIMIT) -> None:
        self._entries: dict[int, CacheEntry] = {}
        self.expirations = 0
        self.history_limit = int(history_limit)
        self.history: dict[int, list[CacheEntry]] = {}

    def put(self, entry: CacheEntry) -> None:
        bucket = self.history.setdefault(entry.origin, [])
        bucket.append(entry)
        if len(bucket) > self.history_limit:
            del bucket[:len(bucket) - self.history_limit]
        old = self._entries.get(entry.origin)
        if old is not None and old.generated_at >= entry.generated_at:
            return  # stale or duplicate arrival: keep the fresher one
        self._entries[entry.origin] = entry

    def history_for(self, origin: int) -> list[CacheEntry]:
        """Advertisements that actually arrived from origin, arrival order."""
        return list(self.history.get(origin, ()))

    def valid_entries(self, now: float) -> dict[int, CacheEntry]:
        out = {}
        for origin, e in self._entries.items():
            if e.valid_at(now):
                out[origin] = e
            else:
                self.expirations += 0  # expiry is lazily observed, not an event
        return out

    def entry(self, origin: int) -> CacheEntry | None:
        return self._entries.get(origin)

    def count_expired(self, now: float) -> int:
        return sum(1 for e in self._entries.values() if not e.valid_at(now))


def build_snapshot(sat_id: int, now: float, geometry, active_cells: dict,
                   isl_queue_bits: dict, isl_propagation_s: dict,
                   slots_used: int, slots_cap: int) -> dict:
    """Snapshot of directly observable local state at time `now`.

    active_cells: {cell: (lat, lon)} of trace-active endpoints; a satellite can
    directly observe which of them it currently sees.
    """
    visible = []
    for cell_id, (lat, lon) in active_cells.items():
        if geometry.ground_visible(sat_id, lat, lon, now):
            visible.append(cell_id)
    return {
        "origin": sat_id,
        "generated_at": now,
        "position": geometry.positions(now)[sat_id],
        "visible_cells": sorted(visible),
        "isl_queue_bits": dict(isl_queue_bits),
        # Directly measured outgoing-link metric at snapshot generation.
        # Remote satellites may use it only after this packet really arrives.
        "isl_propagation_s": dict(isl_propagation_s),
        "access_slots_used": slots_used,
        "access_slots_cap": slots_cap,
    }
