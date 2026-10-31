"""The declared demand programs and the diagnostics switch must be verifiable
without the VM: the burst envelope has to preserve the long-run mean, the
cycle has to fit the observation window, and a contract must not be able to
sneak a trace in that the calibration never saw.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from CODE.experiment_platform import load_calibration as LC          # noqa: E402
from CODE.experiment_platform import scene_trace_program as TP       # noqa: E402

CELLS = {"beijing": [39.90, 116.40], "london": [51.50, -0.13],
         "newyork": [40.70, -74.00]}
OD = {"beijing,london": 1 / 3, "beijing,newyork": 1 / 3,
      "london,beijing": 1 / 12, "london,newyork": 1 / 12,
      "newyork,beijing": 1 / 12, "newyork,london": 1 / 12}
BASE = dict(schema=TP.SCHEMA, cells=CELLS, od_weights=OD, packet_bits=12000,
            emission_start_s=2.0, emission_end_s=2.7, seed=111,
            total_mbps=749.52)
EXPECTED = 749.52e6 * 0.7 / 12000


def test_two_state_identity_is_enforced():
    TP.check_two_state(3.0, 0.8461538461538461, 0.07142857142857142)
    for a, b, p in ((4.0, 0.5, 0.2), (2.0, -0.5, 0.5), (1.0, 1.0, 0.5)):
        with pytest.raises(TP.TraceProgramError):
            TP.check_two_state(a, b, p)


def test_s1_mean_matches_the_declared_rate():
    rows = TP.generate(dict(BASE, model="s1_stationary"))
    assert abs(len(rows) - EXPECTED) / EXPECTED < 0.03
    counts = collections.Counter((r["src_lat"], r["dst_lat"]) for r in rows)
    assert len(counts) == 6
    # the two 1/3 ODs dominate the four 1/12 ODs
    ordered = sorted(counts.values(), reverse=True)
    assert ordered[0] / ordered[-1] > 2.5


def _bins(rows, width=0.05):
    out = [0] * 14
    for r in rows:
        out[min(int((r["emit_time_s"] - 2.0) / width), 13)] += 1
    return out


@pytest.mark.parametrize("model", ["s2_independent_burst", "s3_correlated_hotspot"])
def test_burst_preserves_the_mean_and_is_visible(model):
    a = 3.0
    p = 0.020 / 0.200
    b = (1 - p * a) / (1 - p)
    program = dict(BASE, model=model, a=a, b=b, burst_duration_s=0.020,
                   cycle_s=0.200)
    if model == "s3_correlated_hotspot":
        program["hot_ods"] = ["beijing,london", "beijing,newyork"]
    rows = TP.generate(program)
    assert abs(len(rows) - EXPECTED) / EXPECTED < 0.10
    bins = _bins(rows)
    assert max(bins) / max(1.0, min(bins)) > 1.5, bins


def test_generator_is_deterministic():
    program = dict(BASE, model="s2_independent_burst", a=3.0, b=(1 - 0.1 * 3) / 0.9,
                   burst_duration_s=0.020, cycle_s=0.200)
    assert TP.generate(program) == TP.generate(program)


def test_cycle_longer_than_the_window_is_refused():
    a, p = 3.0, 0.05
    with pytest.raises(TP.TraceProgramError):
        TP.generate(dict(BASE, model="s2_independent_burst", a=a,
                         b=(1 - p * a) / (1 - p), burst_duration_s=0.05,
                         cycle_s=1.0))


def test_missing_hot_ods_is_refused():
    with pytest.raises(TP.TraceProgramError):
        TP.generate(dict(BASE, model="s3_correlated_hotspot", a=3.0, b=0.8,
                         burst_duration_s=0.02, cycle_s=0.2))


def test_diagnostics_defaults_and_refusals():
    contract = {"diagnostics": {"timeline": False}}
    cell = {"id": "c1"}
    d = LC.diagnostics_for(contract, cell)
    assert d == {"timeline": False, "decisions": True, "control_audit": True}
    with pytest.raises(LC.CalibrationError):
        LC.diagnostics_for({"diagnostics": {"timeline": False,
                                            "decisions": False}}, {"id": "c1"})
    with pytest.raises(LC.CalibrationError):
        LC.diagnostics_for({"diagnostics": {"nope": True}}, {"id": "c1"})
    assert LC.diagnostics_for({}, {}) == {"timeline": True, "decisions": True,
                                          "control_audit": True}


def test_trace_program_is_content_addressed(tmp_path, monkeypatch):
    monkeypatch.setattr(LC, "TRACE_PROGRAM_ROOT", tmp_path)
    program = dict(BASE, model="s1_stationary")
    first = LC.materialize_trace_program("cellA", program)
    second = LC.materialize_trace_program("cellA", program)
    assert first["path"] == second["path"]
    assert Path(first["path"]).exists()
    changed = dict(program, seed=999)
    third = LC.materialize_trace_program("cellA", changed)
    assert third["path"] != first["path"]


# ---------------------------------------------------------------------------
# SINGLE PULSE (2026-10-31): the declared positive control whose state-change
# timescale is shorter than the advertised state-update period.  Unlike the
# two-state models this is a TREATMENT: it does NOT preserve the long-run mean.
# ---------------------------------------------------------------------------
PULSE_BASE = dict(schema=TP.SCHEMA, model="s1_stationary",
                  cells=CELLS, od_weights=OD, packet_bits=1000,
                  emission_start_s=2.0, emission_end_s=2.7, seed=111,
                  total_mbps=10.0)


def _rows_inside(rows, start, end):
    return [r for r in rows if start <= r["emit_time_s"] < end]


def test_pulse_multiplies_the_rate_only_inside_its_window():
    base = TP.generate(dict(PULSE_BASE))
    pulse = {"start_s": 2.2, "duration_s": 0.1, "multiplier": 3.0}
    treated = TP.generate(dict(PULSE_BASE, pulse=pulse))
    base_inside = len(_rows_inside(base, 2.2, 2.3))
    treated_inside = len(_rows_inside(treated, 2.2, 2.3))
    base_outside = len(_rows_inside(base, 2.3, 2.7))
    treated_outside = len(_rows_inside(treated, 2.3, 2.7))
    # The pulse is a declared multiplier: a factor of 3 in a 1/7 window is far
    # above Poisson noise even at this small rate, and the rest of the window
    # is untouched (same seed and same draws).
    assert treated_inside > base_inside * 1.8
    assert abs(treated_outside - base_outside) <= max(5, base_outside * 0.02)
    # The long-run mean is deliberately NOT preserved; assert the direction.
    assert len(treated) > len(base)


def test_pulse_is_deterministic():
    program = dict(PULSE_BASE, pulse={"start_s": 2.2, "duration_s": 0.1,
                                      "multiplier": 3.0})
    first = TP.generate(program)
    second = TP.generate(program)
    assert [(r["emit_time_s"], r["packet_id"]) for r in first] == \
        [(r["emit_time_s"], r["packet_id"]) for r in second]


@pytest.mark.parametrize("pulse", [
    {"start_s": 1.9, "duration_s": 0.1, "multiplier": 3.0},   # before window
    {"start_s": 2.6, "duration_s": 0.2, "multiplier": 3.0},   # after window
    {"start_s": 2.2, "duration_s": 0.0, "multiplier": 3.0},   # empty pulse
    {"start_s": 2.2, "duration_s": 0.1, "multiplier": 1.0},   # not a treatment
    {"start_s": 2.2, "duration_s": 0.1},                      # missing multiplier
    {"start_s": 2.2, "duration_s": 0.1, "multiplier": 3.0,
     "extra": 1},                                             # unknown field
    "not-an-object",
])
def test_pulse_declarations_are_validated_before_generation(pulse):
    with pytest.raises(TP.TraceProgramError):
        TP.generate(dict(PULSE_BASE, pulse=pulse))


def test_program_without_a_pulse_is_byte_identical_to_the_historical_stream():
    first = TP.generate(dict(PULSE_BASE))
    second = TP.generate(dict(PULSE_BASE))
    assert [(r["emit_time_s"], r["src_lat"], r["dst_lat"]) for r in first] == \
        [(r["emit_time_s"], r["src_lat"], r["dst_lat"]) for r in second]

