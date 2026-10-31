"""Regression tests for the controlled two-resource mechanism fixture.

These are UNIT tests of the fixture's mechanics (fluid queue, advertisement
age, arm targeting, choice sensitivity).  The scientific run itself is executed
on the VM from a release; nothing here is a research result.
"""
from __future__ import annotations

import math

import pytest

from CODE.experiment_platform import ta_controlled as tc
from CODE.leo_sim import time_alignment as ta


def _cell(**kw) -> tc.Cell:
    base = dict(correlation="differential", tau_s=0.100, margin_s=0.0001,
                advert_interval_s=0.050, measurement_age_s=0.050)
    base.update(kw)
    return tc.Cell(**base)


def _constant_history(cell: tc.Cell, q_mbit: dict, t0: float = 0.300,
                      n: int = 6) -> tuple:
    samples = []
    for k in range(n):
        measured = t0 - cell.measurement_age_s - (n - 1 - k) * cell.advert_interval_s
        if measured < 0:
            continue
        for d in ("N", "E"):
            samples.append(ta.StateSample(
                resource=tc._resource(d), measured_at=measured,
                received_at=measured + cell.measurement_age_s,
                queue_bits=q_mbit[d] * 1e6, rate_bps=tc.C_BPS,
                arrival_rate_bps=None))
    return tuple(samples)


# ---- fluid queue is the contract's square wave -----------------------------
def test_fluid_peak_matches_contract_formula():
    for tau, expected_mbit in ((0.100, 10.0), (0.002, 0.2)):
        cell = _cell(tau_s=tau)
        peaks = []
        for phase_i in range(8):
            series = tc.QueueSeries(cell, 2.0 * tau * phase_i / 8)
            # Ignore the first half phase: the assertion is on the recurrent
            # peak, not on the t=0 transient.
            peaks.append(series.peak())
        nmax = max(p["N"] for p in peaks) / 1e6
        emax = max(p["E"] for p in peaks) / 1e6
        assert nmax == pytest.approx(expected_mbit, rel=0.02)
        assert emax == pytest.approx(expected_mbit, rel=0.02)


def test_common_flow_queues_are_identical():
    cell = _cell(correlation="common")
    series = tc.QueueSeries(cell, 0.013)
    for t in (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3):
        q = series.at(t)
        assert q["N"] == pytest.approx(q["E"], abs=1e-9)


# ---- arms query what they claim --------------------------------------------
def test_candidate_arm_queries_the_eta_instant():
    cell = _cell()
    series = tc.QueueSeries(cell, 0.0)
    history = tc.build_history(cell, series, 0.200)
    chosen, plan = tc.decide(cell, history, 0.200, "candidate")
    targets = dict(plan.targets)
    assert targets["N"] == pytest.approx(0.200 + tc.ETA_S["N"], abs=1e-9)
    assert targets["E"] == pytest.approx(0.200 + tc.ETA_S["E"], abs=1e-9)
    assert chosen in ("N", "E")


def test_common_arm_queries_a_shared_future_instant():
    cell = _cell()
    series = tc.QueueSeries(cell, 0.0)
    history = tc.build_history(cell, series, 0.200)
    _, plan = tc.decide(cell, history, 0.200, "common")
    targets = dict(plan.targets)
    assert targets["N"] == pytest.approx(targets["E"], abs=1e-12)
    assert targets["N"] > 0.200


def test_now_arm_queries_the_decision_instant():
    cell = _cell()
    series = tc.QueueSeries(cell, 0.0)
    history = tc.build_history(cell, series, 0.200)
    _, plan = tc.decide(cell, history, 0.200, "now")
    assert dict(plan.targets)["N"] == pytest.approx(0.200, abs=1e-12)


# ---- the choice follows the scored business delay --------------------------
def test_detour_is_chosen_only_when_the_queue_saving_beats_the_margin():
    # N has 5 ms of predicted queue; E is empty.  Path costs are 20.0 vs 20.1+d.
    hist = _constant_history(_cell(), {"N": 5.0, "E": 0.0}, t0=0.300)
    cheap = _cell(margin_s=0.0001)   # E costs 20.1 ms -> better (20.1 < 25.0)
    dear = _cell(margin_s=0.008)     # E costs 28.1 ms -> worse  (28.1 > 25.0)
    assert tc.decide(cheap, hist, 0.300, "now")[0] == "E"
    assert tc.decide(dear, hist, 0.300, "now")[0] == "N"


def test_score_equals_business_delay_when_prediction_is_exact():
    # A constant advertised queue is predicted exactly at every instant, so the
    # platform score must equal the fixture business delay for that candidate.
    cell = _cell()
    hist = _constant_history(cell, {"N": 2.0, "E": 4.0}, t0=0.300)
    _, plan = tc.decide(cell, hist, 0.300, "now")
    for score in plan.scored.scores:
        if score.fallback:
            continue
        q = {"N": 2.0, "E": 4.0}[score.direction] * 1e6
        truth = cell.path_s[score.direction] + q / tc.C_BPS + tc.TX_S
        assert score.total_s == pytest.approx(truth, rel=1e-9)


def test_missing_history_is_explicit_fallback_not_zero():
    cell = _cell()
    chosen, plan = tc.decide(cell, (), 0.300, "candidate")
    assert chosen in ("N", "E")
    assert plan.scored.fallback_directions  # explicit fallback, not silent zero


def test_fixture_is_deterministic():
    cell = _cell(tau_s=0.002, margin_s=0.008,
                 advert_interval_s=0.005, measurement_age_s=0.005)
    grid = [0.100, 0.120, 0.140]
    a = tc.run_cell(cell, grid=grid, phases=2)
    b = tc.run_cell(cell, grid=grid, phases=2)
    assert a == b
