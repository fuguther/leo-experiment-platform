"""T1-COMPUTE-DELAY tests: decision computation that consumes simulated time.

The hard requirement is that the DEFAULT delay of 0 keeps every historical run
bit-identical: _decide must stay a plain synchronous call with no yield.  Only
a non-zero, explicitly configured delay may defer the commit, and then the
decision body must revalidate against the state that exists when it lands.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import config, decision_ledger, kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=VIS)


def _cfg(delay=None):
    over = {} if delay is None else {"execution": {"compute_delay_s": delay}}
    return make_cfg(over)


def _run(delay=None, rows=None):
    sink, timeline = [], []
    res = kernel.run_simulation(
        _cfg(delay), rows or [row(i, 0.5 * i, A, B) for i in (1, 2, 3)],
        geometry=_geo(), decision_sink=sink, timeline_sink=timeline)
    return res, sink, timeline


# ------------------------------------------------------------------ config

def test_the_default_delay_is_zero():
    resolved = config.resolve_config({})
    assert resolved["config"]["execution"]["compute_delay_s"] == 0.0


def test_a_negative_or_non_finite_delay_is_rejected():
    for bad in (-1.0, -0.001, float("inf"), float("nan"), True):
        with pytest.raises(config.ConfigError):
            config.resolve_config({"execution": {"compute_delay_s": bad}})


def test_a_positive_delay_is_accepted_and_changes_the_config_identity():
    """Adding this key changes config_sha256 for every configuration, so
    previously compiled planned-run rows and authorizations cannot be reused
    against the new code.  That consequence is deliberate and tested here so
    it cannot regress silently."""
    base = config.resolve_config({})
    delayed = config.resolve_config({"execution": {"compute_delay_s": 0.5}})
    assert delayed["config"]["execution"]["compute_delay_s"] == 0.5
    assert base["sha256"] != delayed["sha256"]


# --------------------------------------------------- default path unchanged

def test_zero_delay_keeps_the_synchronous_path_and_equal_instants():
    kern = kernel.Kernel(_cfg(0.0), [row(1, 0.0, A, B)], geometry=_geo(),
                         decision_sink=[])
    assert kern.compute_delay_s == 0.0
    res, sink, timeline = _run(0.0)
    assert sink, "fixture must decide"
    for r in sink:
        assert r["t_decision_start"] == r["t"], \
            "with no compute delay the two instants must coincide"


def test_zero_delay_is_identical_to_the_unset_default():
    rows = [row(i, 0.5 * i, A, B) for i in (1, 2, 3)]
    unset, sink_a, tl_a = _run(None, rows)
    zero, sink_b, tl_b = _run(0.0, rows)
    for key in ("fates", "fate_counts", "totals", "deliveries", "occupied",
                "queue_area_bits_s", "access", "service_log", "handover",
                "events_processed", "packet_events", "link_service_windows"):
        assert unset[key] == zero[key], key
    assert [r["decision_id"] for r in sink_a] == \
        [r["decision_id"] for r in sink_b]
    assert tl_a == tl_b


# ------------------------------------------------------ non-zero behaviour

def test_a_positive_delay_separates_start_from_commit():
    res, sink, timeline = _run(0.05)
    assert sink, "fixture must decide"
    separated = [r for r in sink if r["t_decision_start"] < r["t"]]
    assert separated, "a positive delay must be visible in the decision rows"
    for r in separated:
        assert r["t"] - r["t_decision_start"] == pytest.approx(0.05)


def test_the_ledger_reports_the_computation_interval():
    res, sink, timeline = _run(0.05)
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    intervals = [e["t_decision_commit"] - e["t_decision_start"]
                 for e in by_decision.values()]
    assert intervals, "ledger must contain decisions"
    assert max(intervals) > 0, "the delay must reach the folded ledger"
    for e in by_decision.values():
        assert e["t_decision_start"] <= e["t_measure"]
        assert e["t_measure"] == e["t_decision_commit"]


def test_a_delayed_decision_still_commits_a_legal_action():
    """Revalidation must not produce an illegal commit: whatever the state is
    when the computation lands, the chosen action stays in the candidate set
    that was checked at that instant."""
    res, sink, timeline = _run(0.05)
    for r in sink:
        assert r["chosen"] in r["candidates"], (r["decision_id"], r["kind"])
        assert r["kind"] in ("forward", "deliver")
