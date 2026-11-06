"""Four-direction ETA is published from already-computed counterfactual data."""
from __future__ import annotations

from CODE.experiment_platform import t1_tasks


def _candidates():
    return {
        "N": {"valid": True, "outcome": {"fate": "DELIVERED",
                                          "delivered_at": 0.30,
                                          "emitted_at": 0.20,
                                          "delay_s": 0.10},
              "eta_target_s": 0.25, "peer_arrival_at_s": 0.251},
        "S": {"valid": True, "outcome": {"fate": "DELIVERED",
                                          "delivered_at": None,
                                          "emitted_at": 0.20,
                                          "delay_s": None},
              "eta_target_s": 0.30, "peer_arrival_at_s": 0.303},
        "E": {"valid": False, "reason": "geometry_unavailable",
              "outcome": None, "eta_target_s": None,
              "peer_arrival_at_s": None},
    }


def test_candidate_outcomes_publish_timing_and_eta():
    out = t1_tasks._candidate_outcomes(_candidates())
    assert out["N"]["delivered_at_s"] == 0.30
    assert out["N"]["eta_target_s"] == 0.25
    assert out["N"]["peer_arrival_at_s"] == 0.251
    assert out["E"]["eta_target_s"] is None


def test_four_direction_eta_aggregates_only_paired_rows():
    out = t1_tasks._candidate_outcomes(_candidates())
    eta = t1_tasks._four_direction_eta(out)
    assert set(eta) == {"N", "S"}
    assert eta["N"]["n"] == 1
    assert abs(eta["N"]["mean_abs_error_s"] - 0.001) < 1e-12
    assert abs(eta["S"]["mean_abs_error_s"] - 0.003) < 1e-12
