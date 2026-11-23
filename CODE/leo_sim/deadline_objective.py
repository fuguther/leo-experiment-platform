"""Stateless deadline-loss objective for individual packet episodes."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Literal


DeadlineOutcome = Literal[
    "intermediate",
    "delivered",
    "failed",
    "deadline_expired",
    "admin_censored",
]


@dataclass(frozen=True)
class DeadlineLossTransition:
    """One immutable learning transition produced by ``deadline_loss_v1``."""

    reward: float
    terminal: bool


def _finite_real(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    try:
        converted = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def deadline_loss_v1(
    *,
    deadline_s: float,
    outcome: DeadlineOutcome,
    e2e_delay_s: float | None,
    gamma: float,
    episode_closed: bool,
) -> DeadlineLossTransition | None:
    """Return the transition for one packet under ``deadline_loss_v1``.

    ``e2e_delay_s`` is the actual cumulative generation-to-delivery delay and
    is supplied only for ``outcome="delivered"``. ``deadline_expired`` means
    the packet remained nonterminal when its deadline elapsed. Intermediate
    transitions receive zero reward, while administrative censoring produces
    no learning transition.

    The helper stores no packet or simulator state. Its caller must pass the
    packet episode's current ``episode_closed`` flag on every call, set that
    flag after a terminal result or administrative closure, and avoid creating
    a new episode for a physical delivery that arrives after closure. Calls
    for an already closed episode are rejected. Only undiscounted ``gamma=1``
    is supported.
    """
    deadline = _finite_real(deadline_s, "deadline_s")
    if deadline <= 0.0:
        raise ValueError("deadline_s must be positive")

    discount = _finite_real(gamma, "gamma")
    if discount != 1.0:
        raise ValueError("deadline_loss_v1 requires gamma=1.0")

    if not isinstance(episode_closed, bool):
        raise TypeError("episode_closed must be a bool")
    if not isinstance(outcome, str):
        raise TypeError("outcome must be a supported string")
    if outcome not in {
        "intermediate",
        "delivered",
        "failed",
        "deadline_expired",
        "admin_censored",
    }:
        raise ValueError(f"unsupported deadline outcome: {outcome!r}")
    if episode_closed:
        raise ValueError("packet episode is already closed")

    if outcome == "delivered":
        if e2e_delay_s is None:
            raise ValueError("delivered outcome requires e2e_delay_s")
        delay = _finite_real(e2e_delay_s, "e2e_delay_s")
        if delay < 0.0:
            raise ValueError("e2e_delay_s must be nonnegative")
        return DeadlineLossTransition(
            reward=-min(delay, deadline) / deadline,
            terminal=True,
        )

    if e2e_delay_s is not None:
        raise ValueError("e2e_delay_s is only valid for delivered outcome")
    if outcome == "admin_censored":
        return None
    if outcome in {"failed", "deadline_expired"}:
        return DeadlineLossTransition(reward=-1.0, terminal=True)
    return DeadlineLossTransition(reward=0.0, terminal=False)
