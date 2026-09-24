"""Packet fate ledger and bit conservation for leo_sim.

Every offered data packet has exactly one terminal fate or is explicitly
IN_SYSTEM_AT_STOP. Control packets live in a separate ledger and never enter
the data-bit conservation equation.
"""
from __future__ import annotations

DATA_FATES = (
    "DELIVERED",
    "ACCESS_REJECTED",
    "ACCESS_QUEUE_OVERFLOW",
    "ISL_QUEUE_OVERFLOW",
    "HOLDING_QUEUE_OVERFLOW",
    "GEOMETRY_LOSS_IN_FLIGHT",
    "RANDOM_OUTAGE_IN_FLIGHT",
    "NO_ROUTE",
    "DATA_DEADLINE_EXPIRED",
    "IN_SYSTEM_AT_STOP",
)
TERMINAL_LOSS_FATES = frozenset(
    f for f in DATA_FATES if f not in ("DELIVERED", "IN_SYSTEM_AT_STOP")
)
CONTROL_FATES = ("DELIVERED", "CONTROL_EXPIRED", "IN_SYSTEM_AT_STOP",
                 "QUEUE_OVERFLOW", "GEOMETRY_LOSS_IN_FLIGHT",
                 "RANDOM_OUTAGE_IN_FLIGHT", "DUPLICATE")
CONTROL_TERMINAL_LOSS = frozenset(
    f for f in CONTROL_FATES if f not in ("DELIVERED", "IN_SYSTEM_AT_STOP"))
# Fates that always imply physical arrival. CONTROL_EXPIRED is deliberately
# not here: TTL may expire either while queued (no receive time) or only after
# propagation at the receiver (receive time present).
CONTROL_ARRIVAL_FATES = frozenset({"DELIVERED", "DUPLICATE"})


class FateError(RuntimeError):
    pass


class DataFateLedger:
    """One entry per offered packet; fate assigned exactly once."""

    def __init__(self) -> None:
        self._fates: dict[int, str] = {}
        self._bits: dict[int, int] = {}
        self._offered: dict[int, int] = {}

    def register(self, packet_id: int, bits: int) -> None:
        if packet_id in self._offered:
            raise FateError(f"duplicate offered packet {packet_id}")
        self._offered[packet_id] = bits

    def record(self, packet_id: int, fate: str, bits: int | None = None) -> None:
        if fate not in DATA_FATES:
            raise FateError(f"invalid data fate {fate}")
        if packet_id not in self._offered:
            raise FateError(f"fate for unregistered packet {packet_id}")
        if packet_id in self._fates:
            raise FateError(f"duplicate fate for packet {packet_id}")
        if bits is not None and bits != self._offered[packet_id]:
            # per-packet bit identity is part of the ledger invariant: two
            # offsetting bit errors must not be able to fake conservation
            raise FateError(
                f"fate bits {bits} != registered offered bits "
                f"{self._offered[packet_id]} for packet {packet_id}")
        self._fates[packet_id] = fate
        self._bits[packet_id] = self._offered[packet_id] if bits is None else bits

    def fate_of(self, packet_id: int) -> str | None:
        return self._fates.get(packet_id)

    def close_at_stop(self) -> None:
        """Any offered packet without a fate becomes IN_SYSTEM_AT_STOP."""
        for pid in self._offered:
            if pid not in self._fates:
                self._fates[pid] = "IN_SYSTEM_AT_STOP"
                self._bits[pid] = self._offered[pid]

    def totals(self) -> dict[str, int]:
        offered = sum(self._offered.values())
        delivered = sum(b for pid, b in self._bits.items() if self._fates[pid] == "DELIVERED")
        loss = sum(b for pid, b in self._bits.items() if self._fates[pid] in TERMINAL_LOSS_FATES)
        in_system = sum(b for pid, b in self._bits.items() if self._fates[pid] == "IN_SYSTEM_AT_STOP")
        return {
            "offered_bits": offered,
            "delivered_bits": delivered,
            "terminal_loss_bits": loss,
            "in_system_bits_at_stop": in_system,
        }

    def check_conservation(self) -> dict[str, int]:
        t = self.totals()
        if t["offered_bits"] != t["delivered_bits"] + t["terminal_loss_bits"] + t["in_system_bits_at_stop"]:
            raise FateError(f"bit conservation violated: {t}")
        missing = [pid for pid in self._offered if pid not in self._fates]
        if missing:
            raise FateError(f"packets without fate: {missing[:5]}...")
        return t

    def fate_counts(self) -> dict[str, int]:
        counts = {f: 0 for f in DATA_FATES}
        for f in self._fates.values():
            counts[f] += 1
        return counts


class ControlFateLedger:
    """One entry per generated control packet instance; fate assigned once.

    Conservation: offered = delivered + terminal_loss + in_system_at_stop,
    where terminal_loss includes CONTROL_EXPIRED, QUEUE_OVERFLOW,
    GEOMETRY_LOSS_IN_FLIGHT, RANDOM_OUTAGE_IN_FLIGHT and DUPLICATE.
    Geometry loss and random outage are distinct fates, never merged.

    Every record carries the instance's receive time: the physical arrival
    instant for DELIVERED / DUPLICATE and receiver-observed CONTROL_EXPIRED;
    None for instances that never arrived (including queue-side TTL expiry).
    """

    def __init__(self) -> None:
        self._offered: dict[int, int] = {}
        self._fates: dict[int, str] = {}
        self._bits: dict[int, int] = {}
        self._received: dict[int, float | None] = {}

    def register(self, iid: int, bits: int) -> None:
        if iid in self._offered:
            raise FateError(f"duplicate control packet instance {iid}")
        self._offered[iid] = bits

    def record(self, iid: int, fate: str, bits: int,
               received_at: float | None = None) -> None:
        if fate not in CONTROL_FATES:
            raise FateError(f"invalid control fate {fate}")
        if iid not in self._offered:
            raise FateError(f"fate for unregistered control packet {iid}")
        if iid in self._fates:
            raise FateError(f"duplicate control fate for {iid}")
        if bits != self._offered[iid]:
            raise FateError(
                f"fate bits {bits} != registered offered bits "
                f"{self._offered[iid]} for control instance {iid}")
        if fate in CONTROL_ARRIVAL_FATES:
            if received_at is None:
                raise FateError(f"control fate {fate} requires a receive time")
        elif fate != "CONTROL_EXPIRED" and received_at is not None:
            raise FateError(f"control fate {fate} never arrives; "
                            "received_at must be None")
        self._fates[iid] = fate
        self._bits[iid] = bits
        self._received[iid] = received_at

    def close_at_stop(self) -> None:
        for iid in self._offered:
            if iid not in self._fates:
                self._fates[iid] = "IN_SYSTEM_AT_STOP"
                self._bits[iid] = self._offered[iid]
                self._received[iid] = None

    def totals(self) -> dict[str, int]:
        offered = sum(self._offered.values())
        delivered = sum(b for i, b in self._bits.items() if self._fates[i] == "DELIVERED")
        loss = sum(b for i, b in self._bits.items() if self._fates[i] in CONTROL_TERMINAL_LOSS)
        in_system = sum(b for i, b in self._bits.items() if self._fates[i] == "IN_SYSTEM_AT_STOP")
        return {
            "offered_bits": offered,
            "delivered_bits": delivered,
            "terminal_loss_bits": loss,
            "in_system_bits_at_stop": in_system,
        }

    def check_conservation(self) -> dict[str, int]:
        t = self.totals()
        if t["offered_bits"] != (t["delivered_bits"] + t["terminal_loss_bits"]
                                 + t["in_system_bits_at_stop"]):
            raise FateError(f"control bit conservation violated: {t}")
        missing = [i for i in self._offered if i not in self._fates]
        if missing:
            raise FateError(f"control packets without fate: {missing[:5]}...")
        return t

    @property
    def bits(self) -> dict[str, int]:
        t = self.totals()
        return {"offered": t["offered_bits"], "delivered": t["delivered_bits"],
                "terminal_loss": t["terminal_loss_bits"],
                "in_system": t["in_system_bits_at_stop"]}

    def instances(self) -> dict[int, list]:
        """Exact per-instance [fate, bits, received_at] export for the run
        ledger artifact (received_at is None for instances that never
        arrived)."""
        return {iid: [self._fates[iid], self._bits[iid], self._received[iid]]
                for iid in self._fates}

    def fate_counts(self) -> dict[str, int]:
        counts = {f: 0 for f in CONTROL_FATES}
        for f in self._fates.values():
            counts[f] += 1
        return counts
