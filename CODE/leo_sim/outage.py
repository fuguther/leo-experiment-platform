"""Link loss models for leo_sim.

Two separate mechanisms:
- geometry loss: deterministic; a link whose endpoints are not mutually
  available at time t fails any packet in flight. No RNG involved.
- Gilbert-Elliott random outages: optional, reproducible, continuous-time
  two-state (good/bad) Markov channel with exponential dwell times
  (mean_good_s / mean_bad_s). The state trajectory is a pure function of time
  given the link's private RNG stream: query frequency never changes it.
  Disabled by default. GE models random link outages only — never congestion
  or handover. Default parameters are abstract placeholders, not calibrated
  to any real operator.

A loss during transmission fails the current packet after accounting only for
the service already occupied; there is no implicit pause/resume and no ARQ.
"""
from __future__ import annotations

import math

import numpy as np


def geometry_loss(available: bool, enabled: bool) -> bool:
    """Deterministic loss decision from instantaneous availability."""
    return bool(enabled and not available)


class GilbertElliott:
    """Continuous-time two-state Markov channel.

    State transitions occur at exponential dwell times drawn lazily but in
    trajectory order: each transition consumes exactly one draw, so is_down(t)
    is independent of the caller's query pattern. Requires non-decreasing t.
    """

    def __init__(self, mean_good_s: float, mean_bad_s: float,
                 gen: np.random.Generator, enabled: bool = False):
        if not (mean_good_s > 0 and mean_bad_s > 0):
            raise ValueError("Gilbert-Elliott mean dwell times must be > 0")
        self.mean_good_s = float(mean_good_s)
        self.mean_bad_s = float(mean_bad_s)
        self._gen = gen
        self.enabled = enabled
        self._bad = False
        self._last_t: float | None = None
        # time of the next state flip, drawn from the good-state dwell
        self._next_flip = float(self._gen.exponential(self.mean_good_s)) if enabled else math.inf

    def _advance(self, t: float) -> None:
        if self._last_t is not None and t < self._last_t - 1e-12:
            raise ValueError("GilbertElliott requires non-decreasing time")
        self._last_t = t
        while t >= self._next_flip:
            self._bad = not self._bad
            mean = self.mean_bad_s if self._bad else self.mean_good_s
            self._next_flip += float(self._gen.exponential(mean))

    def is_down(self, t: float) -> bool:
        if not self.enabled:
            return False
        self._advance(t)
        return self._bad

    def next_down(self, t: float) -> float:
        """First time >= t at which the link is down (inf if never/NA)."""
        if not self.enabled:
            return math.inf
        self._advance(t)
        return t if self._bad else self._next_flip

    def next_up(self, t: float) -> float:
        """First time >= t at which the link is up (inf if never/NA)."""
        if not self.enabled:
            return t
        self._advance(t)
        return self._next_flip if self._bad else t


def validate_isl_windows(windows, satellites, horizon):
    """Validate nonoverlapping, bidirectional half-open fault windows."""
    by_link = {}
    for row in windows:
        if not isinstance(row, dict) or set(row) != {"a", "b", "start_s", "end_s"}:
            raise ValueError("each window requires a, b, start_s and end_s")
        a, b, start, end = (row[k] for k in ("a", "b", "start_s", "end_s"))
        if type(a) is not int or type(b) is not int or not 0 <= a < b < satellites:
            raise ValueError("window endpoints must satisfy 0 <= a < b < satellites")
        if (type(start) not in (float, int) or type(end) not in (float, int)
                or not 0 <= start < end <= horizon):
            raise ValueError("window must satisfy 0 <= start < end <= horizon")
        by_link.setdefault((a, b), []).append((float(start), float(end)))
    for edge, spans in by_link.items():
        spans.sort()
        if any(left[1] >= right[0] for left, right in zip(spans, spans[1:])):
            raise ValueError(f"adjacent or overlapping windows for {edge}")
    return by_link


class ScheduledISLGeometry:
    """Combine nominal availability with fixed experimental link removals."""

    compose_from_geometry = False

    def __init__(self, inner, windows):
        self._inner = inner
        self.windows = validate_isl_windows(windows, inner.num_satellites, math.inf)

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _spans(self, a, b):
        return self.windows.get((min(a, b), max(a, b)), ())

    def isl_available(self, a, b, t):
        return (not any(start <= t < end for start, end in self._spans(a, b))
                and self._inner.isl_available(a, b, t))

    def next_isl_change(self, a, b, t, limit):
        spans = self._spans(a, b)
        if not spans:
            return self._inner.next_isl_change(a, b, t, limit)
        cursor = t
        for start, end in spans:
            if end <= cursor:
                continue
            if limit < start:
                break
            if cursor < start:
                nominal = self._inner.next_isl_change(a, b, cursor, min(limit, start))
                if nominal is not None and nominal < start:
                    return nominal
                if self._inner.isl_available(a, b, math.nextafter(start, -math.inf)):
                    return start
            if end > limit:
                return None
            if self._inner.isl_available(a, b, end):
                return end
            cursor = end
        return self._inner.next_isl_change(a, b, cursor, limit)
