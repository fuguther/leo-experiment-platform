from __future__ import annotations

import pytest


def _load_replay():
    try:
        from CODE.leo_sim.truth_score_replay import replay_decision
    except ImportError as exc:
        pytest.fail(f"truth-score replay is not implemented yet: {exc}")
    return replay_decision


def _decision(e_total: float, w_total: float) -> dict:
    return {
        "pid": 7,
        "decision_id": 11,
        "chosen": "E",
        "candidates": ["E", "W"],
        "observation_at_start": {
            "time_alignment": {
                "ranking": ["E", "W"],
                "scores": {
                    "E": {
                        "total_s": e_total,
                        "fallback": False,
                        "missing": [],
                        "service_rate_bps": 1000.0,
                        "predicted_bits": 1.0,
                        "terms": {"resource_work_s": 0.001, "prop_s": e_total - 0.001},
                    },
                    "W": {
                        "total_s": w_total,
                        "fallback": False,
                        "missing": [],
                        "service_rate_bps": 1000.0,
                        "predicted_bits": 1.0,
                        "terms": {"resource_work_s": 0.001, "prop_s": w_total - 0.001},
                    },
                },
            }
        },
    }


def _probe(direction: str, truth_bits: float) -> dict:
    return {
        "pid": 7,
        "decision_id": 11,
        "direction": direction,
        "predicted_bits": 1.0,
        "service_rate_bps": 1000.0,
        "truth": {"work_ahead_bits": truth_bits},
    }


def test_truth_resource_work_can_flip_existing_score_ranking() -> None:
    replay_decision = _load_replay()
    result = replay_decision(
        _decision(0.010, 0.012),
        [_probe("E", 10.0), _probe("W", 0.0)],
    )

    assert result["comparable"] is True
    assert result["online_choice"] == "E"
    assert result["truth_choice"] == "W"
    assert result["ranking_flipped"] is True
    assert result["proxy_regret_s"] == pytest.approx(0.008)
    assert result["directions"]["E"]["delta_resource_work_s"] == pytest.approx(0.009)
    assert result["directions"]["W"]["delta_resource_work_s"] == pytest.approx(-0.001)


def test_common_mode_resource_work_shift_does_not_create_flip() -> None:
    replay_decision = _load_replay()
    result = replay_decision(
        _decision(0.010, 0.012),
        [_probe("E", 6.0), _probe("W", 6.0)],
    )

    assert result["comparable"] is True
    assert result["online_choice"] == "E"
    assert result["truth_choice"] == "E"
    assert result["ranking_flipped"] is False
    assert result["proxy_regret_s"] == pytest.approx(0.0)
    assert result["differential_perturbation_span_s"] == pytest.approx(0.0)
