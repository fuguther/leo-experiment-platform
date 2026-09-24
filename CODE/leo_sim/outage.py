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
