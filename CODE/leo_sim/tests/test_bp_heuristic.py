from __future__ import annotations

import pytest

from leo_sim.bp_heuristic import choose_candidate


def candidate(direction, remaining_total_hops, *, terminal_sink=False):
    return {
        "direction": direction,
        "remaining_total_hops": remaining_total_hops,
        "terminal_sink": terminal_sink,
    }


def test_scores_only_shortest_candidates_and_chooses_maximum_pressure():
    result = choose_candidate(
        [candidate("E", 2), candidate("N", 3), candidate("W", 2)],
        source_q_packets=4,
        source_remaining_hops=3,
        neighbors={
            "E": {"q_packets": 2, "remaining_hops": 3, "service_rate_pps": 1},
            "N": {"q_packets": 0, "remaining_hops": 0, "service_rate_pps": 100},
            "W": {"q_packets": 1, "remaining_hops": 1, "service_rate_pps": 2},
        },
    )

    assert result.status == "ok"
    assert result.chosen == "W"
    assert result.diagnostics[0].pressure == 6
    assert result.diagnostics[1].status == "not_shortest"
    assert result.diagnostics[1].pressure is None
    assert result.diagnostics[2].pressure == 22


def test_exact_pressure_tie_uses_candidate_input_order():
    result = choose_candidate(
        [candidate("S", 1), candidate("N", 1)],
        source_q_packets=2,
        source_remaining_hops=2,
        neighbors={
            "S": {"q_packets": 1, "remaining_hops": 1, "service_rate_pps": 2},
            "N": {"q_packets": 1, "remaining_hops": 1, "service_rate_pps": 2},
        },
    )

    assert result.status == "ok"
    assert result.chosen == "S"
    assert [item.pressure for item in result.diagnostics] == [6, 6]


def test_unknown_shortest_candidate_falls_back_by_input_order_without_zero_fill():
    result = choose_candidate(
        [candidate("W", 2), candidate("E", 2)],
        source_q_packets=3,
        source_remaining_hops=2,
        neighbors={
            "E": {"q_packets": 0, "remaining_hops": 0, "service_rate_pps": 100},
        },
    )

    assert result.status == "fallback_unknown"
    assert result.chosen == "W"
    assert result.diagnostics[0].status == "unknown"
    assert result.diagnostics[0].reason == "missing_neighbor_state"
    assert result.diagnostics[0].pressure is None
    assert result.diagnostics[1].pressure == 600


def test_terminal_sink_has_zero_peer_qh_and_still_uses_its_service_rate():
    result = choose_candidate(
        [candidate("DELIVER", 0, terminal_sink=True), candidate("E", 0)],
        source_q_packets=2,
        source_remaining_hops=3,
        neighbors={
            "DELIVER": {"service_rate_pps": 2},
            "E": {"q_packets": 0, "remaining_hops": 0, "service_rate_pps": 1},
        },
    )

    assert result.status == "ok"
    assert result.chosen == "DELIVER"
    assert result.diagnostics[0].pressure == 12
    assert result.diagnostics[1].pressure == 6


@pytest.mark.parametrize(
    ("source_q", "source_hops", "neighbor", "reason"),
    [
        (-1, 1, {"q_packets": 0, "remaining_hops": 0, "service_rate_pps": 1}, "invalid_source_q_packets"),
        (1, 1, {"q_packets": 0, "remaining_hops": True, "service_rate_pps": 1}, "invalid_neighbor_remaining_hops"),
        (1, 1, {"q_packets": 0, "remaining_hops": 0, "service_rate_pps": 0}, "invalid_service_rate_pps"),
    ],
)
def test_invalid_pressure_inputs_produce_unknown_and_stable_fallback(
    source_q, source_hops, neighbor, reason
):
    result = choose_candidate(
        [candidate("N", 1)],
        source_q_packets=source_q,
        source_remaining_hops=source_hops,
        neighbors={"N": neighbor},
    )

    assert result.status == "fallback_unknown"
    assert result.chosen == "N"
    assert result.diagnostics[0].status == "unknown"
    assert result.diagnostics[0].reason == reason
    assert result.diagnostics[0].pressure is None


def test_unknown_candidate_hop_count_does_not_guess_the_shortest_set():
    result = choose_candidate(
        [candidate("N", None), candidate("E", 1)],
        source_q_packets=1,
        source_remaining_hops=1,
        neighbors={"E": {"q_packets": 0, "remaining_hops": 0, "service_rate_pps": 1}},
    )

    assert result.status == "unknown_shortest_set"
    assert result.chosen is None
    assert result.diagnostics[0].status == "unknown"
    assert result.diagnostics[1].status == "unknown"


def test_unrepresentable_source_qh_is_unknown_instead_of_raising():
    result = choose_candidate(
        [candidate("N", 1)],
        source_q_packets=1,
        source_remaining_hops=10**1000,
        neighbors={"N": {"q_packets": 0, "remaining_hops": 0, "service_rate_pps": 1}},
    )

    assert result.status == "fallback_unknown"
    assert result.chosen == "N"
    assert result.diagnostics[0].reason == "non_finite_source_qh"
