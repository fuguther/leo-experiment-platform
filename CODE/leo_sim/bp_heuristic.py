"""Pure destination/path-biased queue-pressure candidate scoring.

The caller supplies already-legal candidates in stable direction order. This
helper does not inspect or change queues, links, FIFO state, or service state.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CandidateDiagnostic:
    direction: str | None
    remaining_total_hops: int | None
    status: str
    pressure: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CandidateChoice:
    """Selection plus one diagnostic, in input order, for every candidate."""

    status: str
    chosen: str | None
    diagnostics: tuple[CandidateDiagnostic, ...]


@dataclass(frozen=True)
class _Candidate:
    direction: str | None
    remaining_total_hops: int | None
    terminal_sink: bool | None
    reason: str | None


def _as_finite_nonnegative(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _as_finite_positive(value: object) -> float | None:
    number = _as_finite_nonnegative(value)
    if number is None or number <= 0:
        return None
    return number


def _as_nonnegative_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _finite_qh(queue_packets: float, remaining_hops: int) -> float | None:
    try:
        qh = queue_packets * remaining_hops
    except OverflowError:
        return None
    return qh if math.isfinite(qh) else None


def _parse_candidate(value: object) -> _Candidate:
    if not isinstance(value, Mapping):
        return _Candidate(None, None, None, "candidate_not_mapping")

    direction_value = value.get("direction")
    if not isinstance(direction_value, str) or not direction_value:
        direction = None
        direction_error = "missing_direction" if direction_value is None else "invalid_direction"
    else:
        direction = direction_value
        direction_error = None

    if "remaining_total_hops" not in value:
        remaining_hops = None
        hops_error = "missing_remaining_total_hops"
    else:
        remaining_hops = _as_nonnegative_integer(value["remaining_total_hops"])
        hops_error = None if remaining_hops is not None else "invalid_remaining_total_hops"

    terminal_value = value.get("terminal_sink", False)
    if isinstance(terminal_value, bool):
        terminal_sink = terminal_value
        terminal_error = None
    else:
        terminal_sink = None
        terminal_error = "invalid_terminal_sink"

    reason = direction_error or hops_error or terminal_error
    return _Candidate(direction, remaining_hops, terminal_sink, reason)


def _unknown_source_reason(source_q_packets: object,
                           source_remaining_hops: object) -> tuple[float | None, str | None]:
    if source_q_packets is None:
        return None, "missing_source_q_packets"
    q_packets = _as_finite_nonnegative(source_q_packets)
    if q_packets is None:
        return None, "invalid_source_q_packets"
    if source_remaining_hops is None:
        return None, "missing_source_remaining_hops"
    remaining_hops = _as_nonnegative_integer(source_remaining_hops)
    if remaining_hops is None:
        return None, "invalid_source_remaining_hops"
    qh = _finite_qh(q_packets, remaining_hops)
    if qh is None:
        return None, "non_finite_source_qh"
    return qh, None


def _pressure_for(candidate: _Candidate,
                  source_qh: float | None,
                  source_reason: str | None,
                  neighbors: object) -> tuple[float | None, str | None]:
    if source_reason is not None:
        return None, source_reason
    if candidate.reason is not None:
        return None, candidate.reason
    if not isinstance(neighbors, Mapping):
        return None, "invalid_neighbors_mapping"

    neighbor = neighbors.get(candidate.direction)
    if neighbor is None:
        return None, "missing_neighbor_state"
    if not isinstance(neighbor, Mapping):
        return None, "invalid_neighbor_state"

    if "service_rate_pps" not in neighbor:
        return None, "missing_service_rate_pps"
    service_rate = _as_finite_positive(neighbor["service_rate_pps"])
    if service_rate is None:
        return None, "invalid_service_rate_pps"

    if candidate.terminal_sink:
        neighbor_qh = 0.0
    else:
        if "q_packets" not in neighbor:
            return None, "missing_neighbor_q_packets"
        queue_packets = _as_finite_nonnegative(neighbor["q_packets"])
        if queue_packets is None:
            return None, "invalid_neighbor_q_packets"
        if "remaining_hops" not in neighbor:
            return None, "missing_neighbor_remaining_hops"
        remaining_hops = _as_nonnegative_integer(neighbor["remaining_hops"])
        if remaining_hops is None:
            return None, "invalid_neighbor_remaining_hops"
        neighbor_qh = _finite_qh(queue_packets, remaining_hops)
        if neighbor_qh is None:
            return None, "non_finite_neighbor_qh"

    pressure = service_rate * (source_qh - neighbor_qh)
    if not math.isfinite(pressure):
        return None, "non_finite_pressure"
    return pressure, None


def choose_candidate(candidates: Sequence[Mapping[str, object]],
                     *,
                     source_q_packets: object,
                     source_remaining_hops: object,
                     neighbors: Mapping[str, Mapping[str, object]]
                     ) -> CandidateChoice:
    """Choose among caller-prepared legal candidates using queue pressure.

    Each candidate is a mapping with ``direction`` and non-negative integer
    ``remaining_total_hops``; ``terminal_sink`` may mark an egress candidate.
    ``neighbors`` is keyed by direction. ISL records contain ``q_packets``,
    ``remaining_hops``, and ``service_rate_pps``. A terminal sink uses peer
    QH=0 and still requires its service rate. Missing or invalid pressure data
    on any shortest candidate causes a stable input-order fallback across the
    complete shortest-hop set.

    Status is ``ok``, ``fallback_unknown``, ``unknown_shortest_set``, or
    ``no_candidates``. A malformed candidate/remaining-hop count makes the
    true shortest set unknowable, so that case returns no chosen direction.
    """
    candidate_values = list(candidates)
    if not candidate_values:
        return CandidateChoice("no_candidates", None, ())

    parsed = [_parse_candidate(candidate) for candidate in candidate_values]
    if any(candidate.direction is None or candidate.remaining_total_hops is None
           for candidate in parsed):
        diagnostics = tuple(
            CandidateDiagnostic(
                direction=candidate.direction,
                remaining_total_hops=candidate.remaining_total_hops,
                status="unknown",
                reason=(candidate.reason or "shortest_set_indeterminate"),
            )
            for candidate in parsed
        )
        return CandidateChoice("unknown_shortest_set", None, diagnostics)

    min_hops = min(candidate.remaining_total_hops for candidate in parsed)
    shortest_indices = [
        index for index, candidate in enumerate(parsed)
        if candidate.remaining_total_hops == min_hops
    ]
    source_qh, source_reason = _unknown_source_reason(
        source_q_packets, source_remaining_hops)

    diagnostics: list[CandidateDiagnostic] = []
    pressures: dict[int, float] = {}
    unknown_shortest = False
    shortest_index_set = set(shortest_indices)
    for index, candidate in enumerate(parsed):
        if index not in shortest_index_set:
            diagnostics.append(CandidateDiagnostic(
                direction=candidate.direction,
                remaining_total_hops=candidate.remaining_total_hops,
                status="not_shortest",
            ))
            continue
        pressure, reason = _pressure_for(
            candidate, source_qh, source_reason, neighbors)
        if reason is not None:
            unknown_shortest = True
            diagnostics.append(CandidateDiagnostic(
                direction=candidate.direction,
                remaining_total_hops=candidate.remaining_total_hops,
                status="unknown",
                reason=reason,
            ))
        else:
            pressures[index] = pressure
            diagnostics.append(CandidateDiagnostic(
                direction=candidate.direction,
                remaining_total_hops=candidate.remaining_total_hops,
                status="scored",
                pressure=pressure,
            ))

    if unknown_shortest:
        first_shortest = parsed[shortest_indices[0]]
        return CandidateChoice("fallback_unknown", first_shortest.direction,
                               tuple(diagnostics))

    best_index = shortest_indices[0]
    for index in shortest_indices[1:]:
        if pressures[index] > pressures[best_index]:
            best_index = index
    return CandidateChoice("ok", parsed[best_index].direction, tuple(diagnostics))
