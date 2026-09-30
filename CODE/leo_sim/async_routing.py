"""T1-COMPLETE P7: asynchronous schedule update state machine.

This module owns the scope state machine

    UNINITIALIZED -> COMPUTING -> INSTALL_PENDING -> ACTIVE

and nothing else.  It is deliberately SimPy-free: the caller passes the
current instant in, so the update rules can be tested at exact instants and
the kernel remains the only place that owns simulated time.

Rules fixed by the task book
---------------------------
* A scope in ACTIVE may have exactly ONE background update in flight; a
  further trigger is MERGED into one pending request (never an unbounded
  queue of stale jobs).
* A new version installs only at actual_finish + install_delay_s; never at
  request time, never at compute start.
* The snapshot and the predicted install instant are frozen at REQUEST; the
  schedule built from them is NOT rebuilt when the actual install slips, so
  a late install cannot be laundered into an accurate prediction.  The
  offset is recorded instead.
* Version numbers are monotonic: an older completed result can never
  overwrite a newer installed version.
* The valid window starts at the ACTUAL install instant.
* Querying an installed table never predicts and never re-scores: it filters
  the stored ranking by legality and by the visited path.
* The four async_window bins are real: their predictions/scorings are costed
  (they are not free because the window is one scope).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import time_alignment as ta

STATE_UNINITIALIZED = "UNINITIALIZED"
STATE_COMPUTING = "COMPUTING"
STATE_INSTALL_PENDING = "INSTALL_PENDING"
STATE_ACTIVE = "ACTIVE"
STATES = (STATE_UNINITIALIZED, STATE_COMPUTING, STATE_INSTALL_PENDING,
          STATE_ACTIVE)


class AsyncRoutingError(ValueError):
    """An invalid asynchronous update request (fail loud)."""


@dataclass
class UpdateTicket:
    """One background schedule computation for one scope."""

    scope: tuple
    version: int
    trigger: str
    requested_at: float
    predicted_install_at: float
    service_s: float
    install_delay_s: float
    valid_window_s: float
    bins: int
    schedule: ta.Schedule
    state: str = STATE_COMPUTING
    finished_at: float | None = None
    install_at: float | None = None
    install_offset_s: float | None = None
    predicted_expires_at: float = 0.0
    cost: dict = field(default_factory=dict)

    def is_ready(self, now: float) -> bool:
        return (self.state == STATE_INSTALL_PENDING
                and self.install_at is not None and now >= self.install_at)


@dataclass
class ScopeRecord:
    scope: tuple
    state: str = STATE_UNINITIALIZED
    version: int = 0
    installed: ta.Schedule | None = None
    computing: UpdateTicket | None = None
    pending: UpdateTicket | None = None
    queries: int = 0
    fallback_queries: int = 0
    installs: int = 0
    rejected_installs: int = 0
    expired_discards: int = 0
    merged_triggers: int = 0


def _default_builder(snapshot: ta.ObservationSnapshot, install_estimate: float,
                     window_s: float, bins: int, version: int,
                     trigger: str) -> ta.Schedule:
    return ta.build_schedule(snapshot, install_estimate, window_s, bins,
                             version=version, trigger=trigger)


class AsyncScheduleManager:
    """Per-scope asynchronous schedule updates driven by explicit instants."""

    def __init__(self, *, install_delay_s: float, valid_window_s: float,
                 window_bins: int, max_pending_per_scope: int = 1,
                 service_s: float = 0.0,
                 builder: Callable | None = None,
                 listener: Callable | None = None) -> None:
        self.install_delay_s = float(install_delay_s)
        self.valid_window_s = float(valid_window_s)
        self.window_bins = int(window_bins)
        self.max_pending_per_scope = int(max_pending_per_scope)
        self.service_s = float(service_s)
        self._builder = builder or _default_builder
        self._listener = listener
        self.scopes: dict[tuple, ScopeRecord] = {}
        self._version_seq = 0
        self.events: list = []

    # -- bookkeeping ---------------------------------------------------
    def record(self, scope: tuple) -> ScopeRecord:
        rec = self.scopes.get(scope)
        if rec is None:
            rec = ScopeRecord(scope=scope)
            self.scopes[scope] = rec
        return rec

    def _emit(self, milestone: str, at: float, **extra) -> None:
        row = {"milestone": milestone, "at": float(at),
               "scope": list(extra.pop("scope", ())) or None}
        row.update(extra)
        self.events.append(row)
        if self._listener is not None:
            self._listener(row)

    def state_of(self, scope: tuple) -> str:
        return self.record(scope).state

    # -- update lifecycle ---------------------------------------------
    def needs_update(self, scope: tuple, now: float, legal=()) -> bool:
        rec = self.record(scope)
        if rec.state == STATE_ACTIVE and rec.installed is not None \
                and rec.installed.bin_for(now) is not None:
            return False
        if rec.computing is not None:
            return False
        return True

    def request_update(self, scope: tuple, snapshot: ta.ObservationSnapshot,
                       now: float, trigger: str = "periodic",
                       legal=()) -> dict:
        """Start (or merge) one background update for the scope.

        The snapshot and the predicted install instant are frozen here.  The
        schedule is built NOW from the estimated install, and is never
        rebuilt when the real install slips.
        """
        rec = self.record(scope)
        now = float(now)
        predicted_finish = now + self.service_s
        predicted_install = predicted_finish + self.install_delay_s
        if rec.computing is not None:
            if rec.pending is None and self.max_pending_per_scope >= 1:
                rec.pending = {"trigger": trigger, "requested_at": now,
                               "merged": 0}
                rec.merged_triggers += 1
                self._emit("update_merged", now, scope=scope,
                           trigger=trigger,
                           reason="scope_busy_with_one_in_flight")
                return {"accepted": False, "merged": True, "ticket": None,
                        "reason": "busy"}
            return {"accepted": False, "merged": False, "ticket": None,
                    "reason": "pending_already_set"}
        self._version_seq += 1
        version = self._version_seq
        schedule = self._builder(snapshot, predicted_install,
                                 self.valid_window_s, self.window_bins,
                                 version, trigger)
        cost = {
            "bins": self.window_bins,
            "predictions": self.window_bins * len(snapshot.legal_directions),
            "scorings": self.window_bins,
            "service_s": self.service_s,
        }
        ticket = UpdateTicket(
            scope=scope, version=version, trigger=trigger,
            requested_at=now, predicted_install_at=predicted_install,
            service_s=self.service_s, install_delay_s=self.install_delay_s,
            valid_window_s=self.valid_window_s, bins=self.window_bins,
            schedule=schedule, state=STATE_COMPUTING,
            predicted_expires_at=predicted_install + self.valid_window_s,
            cost=cost)
        rec.computing = ticket
        rec.state = STATE_COMPUTING
        self._emit("update_requested", now, scope=scope, version=version,
                   trigger=trigger, predicted_install_at=predicted_install,
                   bins=self.window_bins, cost=cost)
        return {"accepted": True, "merged": False, "ticket": ticket,
                "reason": None}

    def finish_compute(self, ticket: UpdateTicket, now: float) -> UpdateTicket:
        """Mark compute finished; the install instant is finish + delay."""
        now = float(now)
        if ticket.state != STATE_COMPUTING:
            raise AsyncRoutingError(
                f"ticket {ticket.version} is {ticket.state}, not COMPUTING")
        ticket.finished_at = now
        ticket.install_at = now + ticket.install_delay_s
        ticket.install_offset_s = ticket.install_at - ticket.predicted_install_at
        ticket.predicted_expires_at = ticket.install_at + ticket.valid_window_s
        ticket.state = STATE_INSTALL_PENDING
        rec = self.record(ticket.scope)
        if rec.computing is ticket:
            rec.state = STATE_INSTALL_PENDING
        self._emit("update_computed", now, scope=ticket.scope,
                   version=ticket.version, install_at=ticket.install_at,
                   install_offset_s=ticket.install_offset_s)
        return ticket

    def install(self, ticket: UpdateTicket, now: float) -> dict:
        """Atomically swap the table in, if it is still the newest version.

        Returns a verdict dict; a refusal is reported, never silent.
        """
        now = float(now)
        rec = self.record(ticket.scope)
        if ticket.state != STATE_INSTALL_PENDING:
            return {"installed": False, "reason": "not_install_pending"}
        if now + 1e-12 < (ticket.install_at or now):
            return {"installed": False, "reason": "install_delay_not_elapsed"}
        if rec.installed is not None and ticket.version <= rec.installed.version:
            rec.rejected_installs += 1
            self._emit("install_rejected", now, scope=ticket.scope,
                       version=ticket.version,
                       installed_version=rec.installed.version,
                       reason="older_or_equal_version")
            return {"installed": False, "reason": "older_version",
                    "installed_version": rec.installed.version}
        window_start = now
        if window_start > ticket.predicted_expires_at + 1e-12:
            rec.expired_discards += 1
            self._emit("install_discarded", now, scope=ticket.scope,
                       version=ticket.version,
                       reason="predicted_window_entirely_elapsed")
            result = {"installed": False, "reason": "expired_before_install"}
        else:
            table = ta.Schedule(
                scope=ticket.schedule.scope, version=ticket.version,
                installed_at=window_start,
                expires_at=window_start + self.valid_window_s,
                source_snapshot_at=ticket.schedule.source_snapshot_at,
                entries=ticket.schedule.entries, trigger=ticket.trigger)
            rec.installed = table
            rec.version = ticket.version
            rec.installs += 1
            rec.state = STATE_ACTIVE
            self._emit("schedule_installed", now, scope=ticket.scope,
                       version=ticket.version, trigger=ticket.trigger,
                       expires_at=table.expires_at, bins=len(table.entries))
            result = {"installed": True, "reason": None, "version": ticket.version}
        if rec.computing is ticket:
            rec.computing = None
            rec.state = STATE_ACTIVE if rec.installed is not None else STATE_UNINITIALIZED
        return result

    def due_installs(self, now: float) -> list:
        out = []
        for rec in self.scopes.values():
            ticket = rec.computing
            if ticket is not None and ticket.is_ready(now):
                out.append(ticket)
        return out

    def take_pending(self, scope: tuple) -> dict | None:
        """The merged request a completed update must hand over to."""
        rec = self.record(scope)
        pending = rec.pending
        rec.pending = None
        return pending

    # -- packet query ---------------------------------------------------
    def query(self, scope: tuple, now: float, legal, path=()) -> dict:
        """Look up the installed table.  Never predicts, never re-scores."""
        rec = self.record(scope)
        rec.queries += 1
        table = rec.installed
        if table is None:
            rec.fallback_queries += 1
            self._emit("schedule_query", now, scope=scope, version=None,
                       bin=None, action=None, fallback=True,
                       reason="no_table_installed")
            return {"action": None, "version": None, "bin": None,
                    "fallback": True, "state": "uninitialized",
                    "reason": "no_table_installed", "needs_update": True}
        result = ta.lookup_schedule(table, now, legal, path)
        result["needs_update"] = bool(result["fallback"])
        if result["fallback"]:
            rec.fallback_queries += 1
        self._emit("schedule_query", now, scope=scope,
                   version=table.version, bin=result["bin"],
                   action=result["action"], fallback=result["fallback"],
                   state=result["state"], reason=result["reason"])
        return result

    def snapshot_counts(self) -> dict:
        return {
            "scopes": len(self.scopes),
            "queries": sum(r.queries for r in self.scopes.values()),
            "fallback_queries": sum(r.fallback_queries
                                    for r in self.scopes.values()),
            "installs": sum(r.installs for r in self.scopes.values()),
            "rejected_installs": sum(r.rejected_installs
                                       for r in self.scopes.values()),
            "expired_discards": sum(r.expired_discards
                                      for r in self.scopes.values()),
            "merged_triggers": sum(r.merged_triggers
                                     for r in self.scopes.values()),
            "versions_issued": self._version_seq,
        }
