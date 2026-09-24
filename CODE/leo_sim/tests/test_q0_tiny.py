"""Dependency-free Q0-I/Q0-F tiny upper-bound cross-checks."""

from CODE.leo_sim.q0_tiny import (
    TinyPacket,
    TinyScenario,
    enumerate_optimum,
    solve_q0_f,
    solve_q0_i,
    replay_online,
)


def _scenario() -> TinyScenario:
    # At t=0 both branches look equivalent.  The future realization closes
    # 1->3 at t=1 but leaves 2->3 open.  Q0-I is deliberately causal and sees
    # only the current edge set; Q0-F sees the complete availability calendar.
    return TinyScenario(
        nodes=(0, 1, 2, 3),
        edges=((0, 1), (1, 3), (0, 2), (2, 3)),
        availability=(
            ((0, 1), (1, 3), (0, 2), (2, 3)),
            ((2, 3),),
            ((1, 3), (2, 3)),
        ),
        packets=(TinyPacket(1, 0, 3, deadline=2),),
        horizon=3,
    )


def test_q0_future_information_is_not_smuggled_into_current_solver():
    scenario = _scenario()
    current = solve_q0_i(scenario)
    future = solve_q0_f(scenario)

    assert current.first_action[1] == (0, 1)
    assert future.first_action[1] == (0, 2)
    assert current.objective[0] == 0
    assert future.objective[0] == 1
    assert future.objective >= current.objective


def test_q0_future_solver_matches_independent_enumeration_and_replay():
    scenario = _scenario()
    future = solve_q0_f(scenario)
    independent = enumerate_optimum(scenario)
    replay = future.replay(scenario)

    assert future.objective == independent.objective
    assert future.action_trace == independent.action_trace
    assert replay.objective == future.objective
    assert replay.violations == ()
    assert replay.delivered == (1,)


def test_q0_i_replay_only_uses_current_information():
    scenario = _scenario()
    current = solve_q0_i(scenario)
    replay = replay_online(scenario)

    assert replay.objective == current.objective
    assert replay.action_trace[0][1] == (0, 1)
    assert all(step.future_edges is None for step in replay.steps)
