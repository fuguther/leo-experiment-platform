"""Dependency-free observation-contract checks for the information ladder.

This module does not train an agent and does not estimate an algorithm effect.
It gives each tiny link decision an immutable observation view and makes the
hidden-field boundary executable.  The four views mirror the research
protocol: local queue/direction, link rate/availability, remote queue/topology
and per-field age.  The negative controls deliberately alter only a declared
field (shuffle remote queues or replace ages with a fixed value).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


Edge = tuple[int, int]
InfoLevel = str

INFO_LEVELS: Final[tuple[InfoLevel, ...]] = (
    "local_queue_direction",
    "link_rate_availability",
    "remote_queue_topology",
    "field_age",
)

_FIELDS_BY_LEVEL: Final[dict[InfoLevel, tuple[str, ...]]] = {
    "local_queue_direction": (
        "edge", "direction", "local_queue_bits",
    ),
    "link_rate_availability": (
        "edge", "direction", "local_queue_bits", "rate_mbps", "available",
    ),
    "remote_queue_topology": (
        "edge", "direction", "local_queue_bits", "rate_mbps", "available",
        "remote_queue_bits", "topology_available",
    ),
    "field_age": (
        "edge", "direction", "local_queue_bits", "rate_mbps", "available",
        "remote_queue_bits", "topology_available", "field_age_s",
    ),
}


@dataclass(frozen=True)
class TinyLinkState:
    """The complete state used to construct a view, never exposed wholesale."""

    edge: Edge
    direction: str
    local_queue_bits: int
    rate_mbps: float
    available: bool
    remote_queue_bits: int
    topology_available: bool
    field_age_s: float

    def __post_init__(self) -> None:
        if len(self.edge) != 2 or self.edge[0] == self.edge[1]:
            raise ValueError("edge must connect two distinct nodes")
        if self.local_queue_bits < 0 or self.remote_queue_bits < 0:
            raise ValueError("queue sizes must be non-negative")
        if self.rate_mbps < 0 or self.field_age_s < 0:
            raise ValueError("rate and field age must be non-negative")
        if not self.direction:
            raise ValueError("direction must be non-empty")


@dataclass(frozen=True)
class TinyLadderScenario:
    links: tuple[TinyLinkState, ...]

    def __post_init__(self) -> None:
        if not self.links:
            raise ValueError("at least one link is required")
        edges = [link.edge for link in self.links]
        if len(set(edges)) != len(edges):
            raise ValueError("link edges must be unique")


@dataclass(frozen=True)
class TinyObservation:
    level: InfoLevel
    rows: tuple[tuple[Edge, tuple[tuple[str, object], ...]], ...]

    def as_dict(self) -> dict[Edge, dict[str, object]]:
        """Return a fresh mapping for test/audit code, not the policy state."""
        return {
            edge: dict(fields)
            for edge, fields in self.rows
        }

    def fields_for(self, edge: Edge) -> dict[str, object]:
        for row_edge, fields in self.rows:
            if row_edge == edge:
                return dict(fields)
        raise KeyError(edge)


def _validate_level(level: InfoLevel) -> None:
    if level not in _FIELDS_BY_LEVEL:
        raise ValueError(f"unknown information level: {level}")


def observe(
    scenario: TinyLadderScenario,
    level: InfoLevel,
    *,
    shuffle_remote: bool = False,
    fixed_age_s: float | None = None,
) -> TinyObservation:
    """Build exactly one information view without retaining hidden fields."""
    _validate_level(level)
    if shuffle_remote and level not in ("remote_queue_topology", "field_age"):
        raise ValueError("shuffle_remote requires remote queue visibility")
    if fixed_age_s is not None:
        if level != "field_age":
            raise ValueError("fixed_age_s requires field_age visibility")
        if fixed_age_s < 0:
            raise ValueError("fixed age must be non-negative")

    links = tuple(sorted(scenario.links, key=lambda link: link.edge))
    remote_values = [link.remote_queue_bits for link in links]
    if shuffle_remote:
        remote_values.reverse()
    visible = _FIELDS_BY_LEVEL[level]
    rows: list[tuple[Edge, tuple[tuple[str, object], ...]]] = []
    for index, link in enumerate(links):
        values: dict[str, object] = {
            "edge": link.edge,
            "direction": link.direction,
            "local_queue_bits": link.local_queue_bits,
            "rate_mbps": link.rate_mbps,
            "available": link.available,
            "remote_queue_bits": remote_values[index],
            "topology_available": link.topology_available,
            "field_age_s": (
                link.field_age_s if fixed_age_s is None else fixed_age_s
            ),
        }
        rows.append((
            link.edge,
            tuple((name, values[name]) for name in visible),
        ))
    return TinyObservation(level=level, rows=tuple(rows))


def choose_link(observation: TinyObservation) -> Edge:
    """Choose one link using only fields present in ``observation``.

    The score is a deterministic diagnostic policy, not a proposed learning
    reward.  Missing fields contribute nothing; no hidden scenario object is
    reachable from this function.
    """
    rows = observation.as_dict()
    candidates = [
        (edge, values)
        for edge, values in rows.items()
        if values.get("available", True)
        and values.get("topology_available", True)
    ]
    if not candidates:
        raise ValueError("no visible available link")

    def score(values: dict[str, object]) -> float:
        total = 0.0
        if "rate_mbps" in values:
            total += float(values["rate_mbps"])
        if "remote_queue_bits" in values:
            total -= float(values["remote_queue_bits"])
        if "field_age_s" in values:
            # This is only a contract probe: a stale remote field is penalized
            # so the age mask has an observable, deterministic consequence.
            total -= 100.0 * float(values["field_age_s"])
        return total

    return min(candidates, key=lambda item: (-score(item[1]), item[0]))[0]


def demo_scenario() -> TinyLadderScenario:
    return TinyLadderScenario(
        links=(
            TinyLinkState(
                edge=(0, 1), direction="east", local_queue_bits=8,
                rate_mbps=100.0, available=True, remote_queue_bits=60,
                topology_available=True, field_age_s=0.2,
            ),
            TinyLinkState(
                edge=(0, 2), direction="west", local_queue_bits=8,
                rate_mbps=200.0, available=True, remote_queue_bits=20,
                topology_available=True, field_age_s=3.0,
            ),
        )
    )


def run_demo() -> dict[str, object]:
    """Return JSON-compatible evidence for the tiny contract demonstration."""
    scenario = demo_scenario()
    observations = [observe(scenario, level) for level in INFO_LEVELS]
    shuffled = observe(scenario, "remote_queue_topology", shuffle_remote=True)
    fixed = observe(scenario, "field_age", fixed_age_s=1.0)
    original_remote = observe(scenario, "remote_queue_topology").as_dict()
    shuffled_remote = shuffled.as_dict()
    return {
        "schema": "leo-sim-information-ladder-tiny/v1",
        "levels": list(INFO_LEVELS),
        "selected_edges": [choose_link(observation) for observation in observations],
        "visible_fields": {
            level: list(_FIELDS_BY_LEVEL[level]) for level in INFO_LEVELS
        },
        "negative_controls": {
            "shuffle_remote_queue": {
                "preserves_multiset": sorted(
                    row["remote_queue_bits"]
                    for row in shuffled_remote.values()
                ) == sorted(
                    row["remote_queue_bits"]
                    for row in original_remote.values()
                ),
                "changed_assignment": shuffled_remote[(0, 1)]["remote_queue_bits"]
                != original_remote[(0, 1)]["remote_queue_bits"],
            },
            "fixed_age": {
                "all_age_s": sorted(
                    row["field_age_s"] for row in fixed.as_dict().values()
                ),
            },
        },
        "claim_boundary": {
            "supports": [
                "visible-field masks are explicit and deterministic",
                "lower levels cannot react to hidden-field changes",
                "shuffle/fixed-age negative controls are executable",
            ],
            "cannot_conclude": [
                "training convergence or algorithm superiority",
                "a real-trace information value or Q0 upper bound",
                "causal congestion-control effectiveness",
            ],
        },
    }


if __name__ == "__main__":
    print(run_demo())
