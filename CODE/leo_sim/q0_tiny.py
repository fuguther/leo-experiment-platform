"""Small, dependency-free exact solvers for the Q0 information split.

The production simulator has the immutable ``snapshot_global`` and atomic
``JointPlan`` contracts.  This module is intentionally smaller: it provides a
fully enumerable packet/edge calendar used to verify the research contract
before attempting a MILP/CP-SAT adapter.  Q0-I sees only the links available at
the current decision epoch and assumes that view persists; Q0-F sees the
complete realized calendar.  The hidden-calendar distinction is explicit in
the types and in the replay evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from itertools import product
from typing import Iterable


Edge = tuple[int, int]
Position = int
State = tuple[Position, ...]


@dataclass(frozen=True)
class TinyPacket:
    pid: int
    src: int
    dst: int
    deadline: int
    bits: int = 1


@dataclass(frozen=True)
class TinyScenario:
    nodes: tuple[int, ...]
    edges: tuple[Edge, ...]
    availability: tuple[tuple[Edge, ...], ...]
    packets: tuple[TinyPacket, ...]
    horizon: int

    def __post_init__(self) -> None:
        if self.horizon <= 0:
            raise ValueError("horizon must be positive")
        node_set = set(self.nodes)
        edge_set = set(self.edges)
        if len(node_set) != len(self.nodes):
            raise ValueError("nodes must be unique")
        if any(u not in node_set or v not in node_set or u == v
               for u, v in self.edges):
            raise ValueError("edges must join distinct declared nodes")
        if len(self.availability) != self.horizon:
            raise ValueError("availability must contain one entry per tick")
        if any(not set(window) <= edge_set for window in self.availability):
            raise ValueError("availability contains an undeclared edge")
        if len({p.pid for p in self.packets}) != len(self.packets):
            raise ValueError("packet ids must be unique")
        if any(p.src not in node_set or p.dst not in node_set
               or p.deadline <= 0 or p.deadline > self.horizon
               or p.bits <= 0 for p in self.packets):
            raise ValueError("invalid packet bounds")


@dataclass(frozen=True)
class TinyStep:
    tick: int
    actions: tuple[tuple[int, Edge | None], ...]
    # None is deliberate: Q0-I replay must not accidentally persist a future
    # calendar.  Q0-F records the exact realized window for auditability.
    future_edges: tuple[Edge, ...] | None


@dataclass(frozen=True)
class TinyReplay:
    objective: tuple[int, int, int, int]
    delivered: tuple[int, ...]
    violations: tuple[str, ...]
    action_trace: tuple[tuple[int, Edge | None], ...]
    steps: tuple[TinyStep, ...]


@dataclass(frozen=True)
class TinySolution:
    information: str
    objective: tuple[int, int, int, int]
    action_trace: tuple[tuple[int, Edge | None], ...]
    first_action: tuple[int, Edge | None]
    steps: tuple[TinyStep, ...]

    def replay(self, scenario: TinyScenario) -> TinyReplay:
        return replay_plan(scenario, self.action_trace, reveal_future=True)


def _packet_index(scenario: TinyScenario) -> dict[int, int]:
    return {p.pid: i for i, p in enumerate(scenario.packets)}


def _trace_key(trace: tuple[tuple[int, Edge | None], ...]):
    """Make wait/edge actions comparable for deterministic tie-breaking."""
    return tuple((pid, (-1, -1) if edge is None else edge)
                 for pid, edge in trace)


def _better(score, trace, best) -> bool:
    if best is None:
        return True
    return score > best[0] or (
        score == best[0] and _trace_key(trace) < _trace_key(best[1]))


def _action_options(scenario: TinyScenario, state: State, tick: int,
                    edges: tuple[Edge, ...]) -> tuple[tuple[Edge | None, ...], ...]:
    """Return all joint actions, deterministically ordered.

    Every packet may wait or take one currently available outgoing edge.  Two
    packets may not reserve the same edge in one tick.  A packet whose next
    arrival exceeds its deadline has no useful send action and is allowed to
    wait so the terminal objective records it as unfinished.
    """
    outgoing: list[tuple[Edge | None, ...]] = []
    for i, pos in enumerate(state):
        packet = scenario.packets[i]
        if pos < 0 or tick >= packet.deadline:
            outgoing.append((None,))
            continue
        choices: list[Edge | None] = [None]
        choices.extend(sorted((edge for edge in edges if edge[0] == pos
                               and tick + 1 <= packet.deadline)))
        outgoing.append(tuple(choices))
    result: list[tuple[Edge | None, ...]] = []
    for candidate in product(*outgoing):
        used = [edge for edge in candidate if edge is not None]
        if len(set(used)) != len(used):
            continue
        result.append(candidate)
    return tuple(result)


def _transition(scenario: TinyScenario, state: State,
                actions: tuple[Edge | None, ...], tick: int,
                edges: tuple[Edge, ...]) -> State:
    allowed = set(edges)
    next_state = list(state)
    for i, (pos, action) in enumerate(zip(state, actions)):
        packet = scenario.packets[i]
        if pos < 0 or action is None:
            continue
        if action not in allowed or action[0] != pos:
            raise ValueError(f"invalid action for packet {packet.pid}: {action}")
        arrival = tick + 1
        next_state[i] = action[1] if arrival <= packet.deadline else -2
        if next_state[i] == packet.dst:
            next_state[i] = -1
    return tuple(next_state)


def _objective(scenario: TinyScenario, state: State,
               arrival: tuple[int, ...]) -> tuple[int, int, int, int]:
    delivered = sum(pos == -1 for pos in state)
    missed = sum(pos == -2 for pos in state)
    unfinished = sum(pos >= 0 for pos in state)
    arrival_penalty = sum(t if t >= 0 else scenario.horizon + 1
                          for t in arrival)
    # Lexicographic maximization: deliver first, then avoid terminal misses,
    # then avoid unfinished packets, then prefer earlier completion.
    return delivered, -missed, -unfinished, -arrival_penalty


def _solve_exact(scenario: TinyScenario,
                 calendar: tuple[tuple[Edge, ...], ...],
                 *, memoize: bool = True) -> TinySolution:
    packets = scenario.packets
    initial = tuple(p.src for p in packets)
    no_arrival = tuple(-1 for _ in packets)

    def rec(tick: int, state: State,
            arrival: tuple[int, ...]) -> tuple[tuple[int, int, int, int],
                                               tuple[tuple[int, Edge | None], ...]]:
        if tick >= scenario.horizon or all(pos < 0 for pos in state):
            return _objective(scenario, state, arrival), ()
        best: tuple[tuple[int, int, int, int],
                    tuple[tuple[int, Edge | None], ...]] | None = None
        for actions in _action_options(scenario, state, tick, calendar[tick]):
            next_state = _transition(scenario, state, actions, tick,
                                     calendar[tick])
            next_arrival = list(arrival)
            for i, (before, after) in enumerate(zip(state, next_state)):
                if before >= 0 and after == -1 and next_arrival[i] < 0:
                    next_arrival[i] = tick + 1
            score, tail = rec(tick + 1, next_state, tuple(next_arrival))
            actions_named = tuple((packets[i].pid, action)
                                  for i, action in enumerate(actions))
            candidate_trace = actions_named + tail
            if _better(score, candidate_trace, best):
                best = score, candidate_trace
        if best is None:
            return _objective(scenario, state, arrival), ()
        return best

    if memoize:
        # The explicit function above is kept readable for the independent
        # enumerator.  This memoized wrapper caches only future state values;
        # action traces remain deterministic because options are ordered.
        @lru_cache(maxsize=None)
        def cached(tick: int, state: State, arrival: tuple[int, ...]):
            if tick >= scenario.horizon or all(pos < 0 for pos in state):
                return _objective(scenario, state, arrival), ()
            best = None
            for actions in _action_options(scenario, state, tick, calendar[tick]):
                next_state = _transition(scenario, state, actions, tick,
                                         calendar[tick])
                next_arrival = list(arrival)
                for i, (before, after) in enumerate(zip(state, next_state)):
                    if before >= 0 and after == -1 and next_arrival[i] < 0:
                        next_arrival[i] = tick + 1
                score, tail = cached(tick + 1, next_state,
                                     tuple(next_arrival))
                actions_named = tuple((packets[i].pid, action)
                                      for i, action in enumerate(actions))
                candidate_trace = actions_named + tail
                if _better(score, candidate_trace, best):
                    best = score, candidate_trace
            return best
        score, trace = cached(0, initial, no_arrival)
    else:
        score, trace = rec(0, initial, no_arrival)
    return TinySolution(
        information="full_future" if calendar == scenario.availability
        else "current_only",
        objective=score,
        action_trace=trace,
        first_action=trace[0] if trace else (-1, None),
        steps=tuple(),
    )


def solve_q0_f(scenario: TinyScenario) -> TinySolution:
    """Solve the clairvoyant tiny problem over the complete future calendar."""
    solution = _solve_exact(scenario, scenario.availability, memoize=True)
    steps = tuple(
        TinyStep(tick=tick, actions=tuple(
            solution.action_trace[tick:tick + len(scenario.packets)]),
            future_edges=scenario.availability[tick])
        for tick in range(scenario.horizon)
    )
    return TinySolution(solution.information, solution.objective,
                        solution.action_trace, solution.first_action, steps)


def solve_q0_i(scenario: TinyScenario) -> TinySolution:
    """Run the causal current-state optimum with hidden future availability."""
    replay = replay_online(scenario)
    return TinySolution("current_only", replay.objective, replay.action_trace,
                        replay.action_trace[0] if replay.action_trace else (-1, None),
                        replay.steps)


def replay_plan(scenario: TinyScenario,
                action_trace: tuple[tuple[int, Edge | None], ...],
                *, reveal_future: bool) -> TinyReplay:
    by_pid = _packet_index(scenario)
    state = tuple(p.src for p in scenario.packets)
    arrival = [-1] * len(scenario.packets)
    violations: list[str] = []
    steps: list[TinyStep] = []
    trace_index = 0
    for tick in range(scenario.horizon):
        actions = []
        for packet in scenario.packets:
            if trace_index >= len(action_trace):
                action = (packet.pid, None)
            else:
                action = action_trace[trace_index]
                trace_index += 1
            if action[0] not in by_pid:
                violations.append(f"unknown packet {action[0]}")
                continue
            actions.append(action)
        action_by_i = [None] * len(scenario.packets)
        for pid, edge in actions:
            action_by_i[by_pid[pid]] = edge
        try:
            next_state = _transition(scenario, state, tuple(action_by_i), tick,
                                     scenario.availability[tick])
        except ValueError as exc:
            violations.append(str(exc))
            next_state = state
        for i, (before, after) in enumerate(zip(state, next_state)):
            if before >= 0 and after == -1 and arrival[i] < 0:
                arrival[i] = tick + 1
        steps.append(TinyStep(tick, tuple(actions),
                              scenario.availability[tick] if reveal_future else None))
        state = next_state
    score = _objective(scenario, state, tuple(arrival))
    delivered = tuple(scenario.packets[i].pid for i, pos in enumerate(state)
                      if pos == -1)
    return TinyReplay(score, delivered, tuple(violations),
                      tuple(action_trace), tuple(steps))


def replay_online(scenario: TinyScenario) -> TinyReplay:
    """Receding-horizon replay that only passes the current window to Q0-I."""
    state = tuple(p.src for p in scenario.packets)
    arrival = [-1] * len(scenario.packets)
    trace: list[tuple[int, Edge | None]] = []
    steps: list[TinyStep] = []
    violations: list[str] = []
    for tick in range(scenario.horizon):
        current = scenario.availability[tick]
        calendar = tuple(current for _ in range(scenario.horizon))
        view = TinyScenario(scenario.nodes, scenario.edges, calendar,
                            scenario.packets, scenario.horizon)
        solution = _solve_from_state(view, state, tick)
        actions = solution[1]
        trace.extend(actions)
        action_by_i = tuple(edge for _, edge in actions)
        try:
            next_state = _transition(scenario, state, action_by_i, tick, current)
        except ValueError as exc:
            violations.append(str(exc))
            next_state = state
        for i, (before, after) in enumerate(zip(state, next_state)):
            if before >= 0 and after == -1 and arrival[i] < 0:
                arrival[i] = tick + 1
        steps.append(TinyStep(tick, actions, None))
        state = next_state
    return TinyReplay(_objective(scenario, state, tuple(arrival)),
                      tuple(scenario.packets[i].pid for i, pos in enumerate(state)
                            if pos == -1), tuple(violations), tuple(trace),
                      tuple(steps))


def _solve_from_state(scenario: TinyScenario, state: State, tick: int):
    """Return only the first causal action for a live state."""
    # Rebase the remaining horizon into a temporary scenario and enumerate
    # from the supplied state.  This is tiny by construction and avoids
    # exposing a mutable planner handle to the production kernel.
    packets = scenario.packets
    calendar = scenario.availability

    def rec(t: int, current: State):
        if t >= scenario.horizon:
            return _objective(scenario, current, tuple(-1 for _ in packets)), ()
        best = None
        for actions in _action_options(scenario, current, t, calendar[t]):
            nxt = _transition(scenario, current, actions, t, calendar[t])
            score, tail = rec(t + 1, nxt)
            named = tuple((packets[i].pid, a) for i, a in enumerate(actions))
            candidate_trace = named + tail
            if _better(score, candidate_trace, best):
                best = score, candidate_trace
        return best

    score, trace = rec(tick, state)
    return score, trace[:len(packets)]


def enumerate_optimum(scenario: TinyScenario) -> TinySolution:
    """Independent no-memo enumeration used as the second exact checker."""
    return _solve_exact(scenario, scenario.availability, memoize=False)


if __name__ == "__main__":
    # A small command-line smoke is intentionally JSON-free and human-readable
    # so it can be pasted into a review receipt without importing a solver.
    scenario = TinyScenario(
        nodes=(0, 1, 2, 3),
        edges=((0, 1), (1, 3), (0, 2), (2, 3)),
        availability=(((0, 1), (1, 3), (0, 2), (2, 3)),
                      ((2, 3),), ((1, 3), (2, 3))),
        packets=(TinyPacket(1, 0, 3, deadline=2),),
        horizon=3,
    )
    current = solve_q0_i(scenario)
    future = solve_q0_f(scenario)
    print({"q0_i": current.objective, "q0_f": future.objective,
           "q0_i_first": current.first_action,
           "q0_f_first": future.first_action,
           "vf_ge_vi": future.objective >= current.objective})
