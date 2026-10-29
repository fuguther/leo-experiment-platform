from __future__ import annotations

import pytest


def _load_replay():
    try:
        from CODE.leo_sim.truth_score_replay import replay_decision
    except ImportError as exc:
        pytest.fail(f"truth-score replay is not implemented yet: {exc}")
    return replay_decision


def _decision(
    e_total: float,
    w_total: float,
    *,
    pid: int = 7,
    decision_id: int = 11,
    chosen: str = "E",
) -> dict:
    return {
        "pid": pid,
        "decision_id": decision_id,
        "chosen": chosen,
        "candidates": ["E", "W"],
        "observation_at_start": {
            "time_alignment": {
                "ranking": ["E", "W"],
                "used_state": {
                    "E": {"resource_id": "isl:0:1", "generation": 1},
                    "W": {"resource_id": "isl:1:0", "generation": 1},
                },
                "eta_targets": {"E": 2.0, "W": 2.0},
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


def _probe(
    direction: str,
    truth_bits: float,
    *,
    pid: int = 7,
    decision_id: int = 11,
    forced: bool = False,
) -> dict:
    return {
        "pid": pid,
        "decision_id": decision_id,
        "direction": direction,
        "forced": forced,
        "candidate_directions": ["E", "W"],
        "eval_target_s": 2.0,
        "sampled_at_s": 2.0,
        "identity_source": "used_state",
        "resource_id": ("isl:0:1" if direction == "E" else "isl:1:0"),
        "generation": 1,
        "sampled_resource_id": ("isl:0:1" if direction == "E" else "isl:1:0"),
        "sampled_generation": 1,
        "predicted_bits": 1.0,
        "service_rate_bps": 1000.0,
        "predicted_work_s": 0.001,
        "score_total_s": (0.010 if direction == "E" else 0.012),
        "truth": {
            "work_ahead_bits": truth_bits,
            "resource_id": ("isl:0:1" if direction == "E" else "isl:1:0"),
            "generation": 1,
        },
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


def test_summary_separates_ranking_flip_from_actual_choice_mismatch() -> None:
    from CODE.leo_sim.truth_score_replay import summarize_replays

    rows = [
        {
            "comparable": True,
            "ranking_flipped": True,
            "proxy_regret_s": 0.004,
            "online_choice": "E",
            "truth_choice": "W",
            "actual_chosen": "E",
            "online_top2_margin_s": 0.002,
            "differential_perturbation_span_s": 0.010,
        },
        {
            "comparable": True,
            "ranking_flipped": False,
            "proxy_regret_s": 0.0,
            "online_choice": "E",
            "truth_choice": "E",
            "actual_chosen": "W",
            "online_top2_margin_s": 0.003,
            "differential_perturbation_span_s": 0.001,
        },
        {
            "comparable": False,
            "ranking_flipped": None,
            "proxy_regret_s": None,
            "online_choice": "E",
            "truth_choice": None,
            "actual_chosen": "E",
            "online_top2_margin_s": None,
            "differential_perturbation_span_s": None,
        },
    ]
    summary = summarize_replays(rows)

    assert summary["decisions"] == 3
    assert summary["comparable"] == 2
    assert summary["ranking_flips"] == 1
    assert summary["ranking_flip_rate"] == pytest.approx(0.5)
    assert summary["online_top1_matches_actual"] == 1
    assert summary["online_top1_match_rate"] == pytest.approx(0.5)
    assert summary["proxy_regret_s"]["mean"] == pytest.approx(0.002)
    assert summary["proxy_regret_s_on_flips"]["mean"] == pytest.approx(0.004)


def test_dataset_excludes_forced_only_groups_and_reports_missing_decisions() -> None:
    from CODE.leo_sim.truth_score_replay import replay_dataset

    decisions = [_decision(0.010, 0.012, pid=1, decision_id=1)]
    probes = [
        _probe("E", 10.0, pid=1, decision_id=1),
        _probe("W", 0.0, pid=1, decision_id=1),
        _probe("E", 5.0, pid=2, decision_id=2, forced=True),
        _probe("E", 5.0, pid=3, decision_id=3),
        _probe("W", 5.0, pid=3, decision_id=3),
    ]
    result = replay_dataset(decisions, probes)

    assert result["probe_decisions"] == 3
    assert result["forced_groups_excluded"] == 1
    assert result["missing_decision_records"] == 1
    assert result["summary"]["decisions"] == 1
    assert result["summary"]["ranking_flips"] == 1



def test_incomplete_candidate_set_is_not_comparable():
    from CODE.leo_sim.truth_score_replay import replay_decision

    result = replay_decision(_decision(0.010, 0.012), [_probe("E", 10.0)])
    assert result["comparable"] is False
    assert result["missing_truth_directions"] == ["W"]
    assert result["integrity_errors"]["W"] == ["missing_probe_row"]


def test_truth_generation_mismatch_fails_closed():
    from CODE.leo_sim.truth_score_replay import replay_decision

    row_e = _probe("E", 10.0)
    row_e["truth"]["generation"] = 99
    result = replay_decision(_decision(0.010, 0.012), [row_e, _probe("W", 0.0)])
    assert result["comparable"] is False
    assert "E" in result["invalid_directions"]
    assert "truth_resource_identity_mismatch" in result["integrity_errors"]["E"]


def test_target_time_mismatch_fails_closed():
    from CODE.leo_sim.truth_score_replay import replay_decision

    row_e = _probe("E", 10.0)
    row_e["eval_target_s"] = 2.01
    result = replay_decision(_decision(0.010, 0.012), [row_e, _probe("W", 0.0)])
    assert result["comparable"] is False
    assert "evaluation_target_mismatch" in result["integrity_errors"]["E"]


def test_duplicate_candidate_rows_are_not_silently_overwritten():
    from CODE.leo_sim.truth_score_replay import replay_decision

    row_e = _probe("E", 10.0)
    result = replay_decision(_decision(0.010, 0.012), [row_e, dict(row_e), _probe("W", 0.0)])
    assert result["comparable"] is False
    assert result["duplicate_directions"] == ["E"]


def test_dataset_excludes_groups_without_explicit_forced_status():
    from CODE.leo_sim.truth_score_replay import replay_dataset

    decision = _decision(0.010, 0.012, pid=1, decision_id=1)
    rows = [_probe("E", 10.0, pid=1, decision_id=1), _probe("W", 0.0, pid=1, decision_id=1)]
    for row in rows:
        row.pop("forced")
    result = replay_dataset([decision], rows)
    assert result["forced_status_unknown_groups"] == 1
    assert result["summary"]["decisions"] == 0


def test_duplicate_decision_keys_fail_loudly():
    from CODE.leo_sim.truth_score_replay import replay_dataset

    decision = _decision(0.010, 0.012, pid=1, decision_id=1)
    with pytest.raises(ValueError, match="duplicate decision key"):
        replay_dataset([decision, dict(decision)], [])
