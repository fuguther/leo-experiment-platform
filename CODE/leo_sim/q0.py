"""Contracts for Q0 planner plans.

This module is deliberately execution-free: a planner can construct an
immutable plan, but only the kernel may validate it against live state and
apply it.  Keeping the wire contract separate prevents planners from gaining
access to mutating kernel methods.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


class PlanError(ValueError):
    """Malformed or internally inconsistent planner input."""


ActionKind = Literal["forward", "deliver", "wait"]


@dataclass(frozen=True)
class PlanAction:
    """One packet action; validation rejects irrelevant fields by kind."""

    kind: ActionKind
    packet_id: int
    sat: int
    direction: str | None = None
    until: float | None = None

    def __post_init__(self):
        if self.kind not in ("forward", "deliver", "wait"):
            raise PlanError(f"unknown action kind: {self.kind!r}")
        if isinstance(self.packet_id, bool) or not isinstance(self.packet_id, int):
            raise PlanError("packet_id must be an integer")
        if isinstance(self.sat, bool) or not isinstance(self.sat, int) or self.sat < 0:
            raise PlanError("sat must be a non-negative integer")
        if self.kind == "forward":
            if self.direction not in ("N", "S", "E", "W"):
                raise PlanError("forward requires direction N/S/E/W")
            if self.until is not None:
                raise PlanError("forward cannot carry until")
        elif self.kind == "deliver":
            if self.direction is not None or self.until is not None:
                raise PlanError("deliver cannot carry direction or until")
        else:
            if self.direction is not None:
                raise PlanError("wait cannot carry direction")
            if not isinstance(self.until, (int, float)) or isinstance(self.until, bool):
                raise PlanError("wait requires numeric until")


@dataclass(frozen=True)
class JointPlan:
    """Immutable, versioned plan candidate returned by a Q0 planner."""

    version: int
    actions: tuple[PlanAction, ...]

    def __post_init__(self):
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise PlanError("version must be an integer")
        if not isinstance(self.actions, tuple):
            raise PlanError("actions must be a tuple")
        if len({a.packet_id for a in self.actions}) != len(self.actions):
            raise PlanError("a plan may contain at most one action per packet")
        if not all(isinstance(a, PlanAction) for a in self.actions):
            raise PlanError("actions must contain only PlanAction values")


def validate_plan_version(plan: JointPlan, state_version: int) -> tuple[bool, tuple[str, ...]]:
    """Pure fail-closed version check used before live-state validation."""
    if plan.version != state_version:
        return False, (f"stale plan version {plan.version} != {state_version}",)
    return True, ()
