"""T1-COUNTERFACTUAL-REPLAY tests: strictly paired forced-action replay.

The review fixed the constraints: no deep-copied SimPy environment, no reading
an un-chosen candidate future out of the original trajectory.  Instead the same
immutable trace/config/seed is replayed to the SAME decision, the pre-branch
state is proven identical by fingerprint, and exactly one action is forced.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import counterfactual, kernel
from CODE.leo_sim.tests.helpers import (StaticGeometry, cell, cell_center,
                                       make_cfg, row)

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)

# 6-ring.  From satellite 0 toward satellite 2 (which serves B) the E direction
# is two hops (0-1-2) and the W direction is four hops (0-5-4-3-2), so both are
# legal candidates but they are NOT symmetric -- forcing the longer one must be
# visible in the outcome.
RING = 6
NB = {i: {"E": (i + 1) % RING, "W": (i - 1) % RING} for i in range(RING)}


def _geo():
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 2 and (lat, lon) == BC)
    return StaticGeometry(RING, neighbors_map=NB, visible=vis)


CFG = {"scenario": {"duration_s": 40.0, "num_satellites": RING,
                    "num_planes": 1, "seed": 5},
       "control_plane": {"enabled": True},
       "routing": {"policy": "oracle"},
       "links": {"isl_rate_mbps": 8.0}}


def _resolved(overrides=None):
    return make_cfg(CFG if overrides is None else {**CFG, **overrides})


def _rows(n=3):
    return [row(i, 0.5 * i, A, B) for i in range(1, n + 1)]


def _first_branching_decision(rows):
    sink: list = []
    kernel.run_simulation(_resolved(), rows, geometry=_geo(),
                          decision_sink=sink)
    return next(r for r in sink
                if r["sat"] == 0 and len(r["candidates"]) > 1)


# ------------------------------------------------------------- happy path

def test_branch_states_are_proven_identical_and_the_action_changes():
    rows = _rows()
    target = _first_branching_decision(rows)
    other = next(c for c in target["candidates"] if c != target["chosen"])
    out = counterfactual.replay_with_forced_action(
        _resolved(), rows, target_decision_id=target["decision_id"],
        forced_action=other, geometry=_geo())
    v = out["verification"]
    assert v["branch_states_identical"] is True
    assert v["baseline_branch_fingerprint"] == \
        v["counterfactual_branch_fingerprint"]
    assert v["baseline_action"] == target["chosen"]
    assert v["forced_action"] == other
    assert v["action_changed"] is True
    assert v["legal_at_branch_point"] is True


def test_forcing_the_longer_route_is_visible_in_that_packets_path():
    rows = _rows()
    target = _first_branching_decision(rows)
    other = next(c for c in target["candidates"] if c != target["chosen"])
    out = counterfactual.replay_with_forced_action(
        _resolved(), rows, target_decision_id=target["decision_id"],
        forced_action=other, geometry=_geo())
    pid = target["pid"]
    base = out["baseline"]["result"]["deliveries"][pid]
    forced = out["counterfactual"]["result"]["deliveries"][pid]
    assert base["path"] != forced["path"], "the forced route must be taken"
    assert len(forced["path"]) > len(base["path"]), \
        "the forced direction is the longer way round"
    assert forced["delivered_at"] != base["delivered_at"]
    assert out["outcome_delta"]["events_processed"] != 0, \
        "a real divergence must be observable"


def test_only_the_target_decision_is_forced():
    rows = _rows()
    target = _first_branching_decision(rows)
    other = next(c for c in target["candidates"] if c != target["chosen"])
    out = counterfactual.replay_with_forced_action(
        _resolved(), rows, target_decision_id=target["decision_id"],
        forced_action=other, geometry=_geo())
    forced = [m for m in out["counterfactual"]["timeline_rows"]
              if m["milestone"] == "forced_action"]
    assert len(forced) == 1
    assert forced[0]["decision_id"] == target["decision_id"]
    assert forced[0]["original"] == target["chosen"]
    assert forced[0]["forced"] == other


def test_the_baseline_is_untouched_by_the_forced_replay():
    """Two unforced runs of the same config/seed/trace must be identical, and
    the baseline inside the harness must equal a plain run: the counterfactual
    may not perturb it."""
    rows = _rows()
    target = _first_branching_decision(rows)
    other = next(c for c in target["candidates"] if c != target["chosen"])
    out = counterfactual.replay_with_forced_action(
        _resolved(), rows, target_decision_id=target["decision_id"],
        forced_action=other, geometry=_geo())
    plain: list = []
    again = kernel.run_simulation(_resolved(), rows, geometry=_geo(),
                                  decision_sink=plain)
    for key in ("fates", "fate_counts", "totals", "deliveries",
                "events_processed", "packet_events"):
        assert out["baseline"]["result"][key] == again[key], key
    assert out["baseline"]["decision_rows"] == plain


# --------------------------------------------------------- refused pairings

def test_forcing_the_action_the_baseline_already_took_is_refused():
    rows = _rows()
    target = _first_branching_decision(rows)
    with pytest.raises(counterfactual.CounterfactualError) as excinfo:
        counterfactual.replay_with_forced_action(
            _resolved(), rows, target_decision_id=target["decision_id"],
            forced_action=target["chosen"], geometry=_geo())
    assert "did not change the action" in str(excinfo.value)


def test_an_unknown_decision_is_refused():
    rows = _rows()
    branch = _first_branching_decision(rows)
    other = next(c for c in branch["candidates"] if c != branch["chosen"])
    with pytest.raises(counterfactual.CounterfactualError) as excinfo:
        counterfactual.replay_with_forced_action(
            _resolved(), rows, target_decision_id=10 ** 6,
            forced_action=other, geometry=_geo())
    assert "never occurred" in str(excinfo.value)


def test_an_illegal_forced_action_fails_loud():
    """Forcing a direction the kernel itself rejected at the branch point must
    raise, not silently commit an action that was never legal."""
    rows = _rows()
    target = _first_branching_decision(rows)
    illegal = next(d for d in ("N", "S", "E", "W")
                   if d not in target["candidates"])
    with pytest.raises(kernel.KernelError) as excinfo:
        counterfactual.replay_with_forced_action(
            _resolved(), rows, target_decision_id=target["decision_id"],
            forced_action=illegal, geometry=_geo())
    assert "not legal at the branch point" in str(excinfo.value)


def test_learning_runs_are_refused_by_the_first_version():
    """The guard is on the harness itself, so it is exercised by handing it a
    resolved dict whose learning algorithm is not none (config validation
    would refuse to build such a config through the normal front door)."""
    import copy

    rows = _rows()
    target = _first_branching_decision(rows)
    other = next(c for c in target["candidates"] if c != target["chosen"])
    learned = copy.deepcopy(_resolved())
    learned["config"]["learning"]["algorithm"] = "qlearning"
    with pytest.raises(counterfactual.CounterfactualError) as excinfo:
        counterfactual.replay_with_forced_action(
            learned, rows, target_decision_id=target["decision_id"],
            forced_action=other, geometry=_geo())
    assert "learning off" in str(excinfo.value)


# ------------------------------------------------------------ fingerprint

def test_fingerprint_separates_different_branch_points():
    """A different branch point must produce a different fingerprint, or the
    pairing proof would be vacuous."""
    rows = _rows()
    sink: list = []
    kernel.run_simulation(_resolved(), rows, geometry=_geo(),
                          decision_sink=sink)
    ids = [r["decision_id"] for r in sink if r["sat"] == 0]
    assert len(ids) >= 2, "fixture needs two decisions on satellite 0"
    prints = {counterfactual.branch_fingerprint(sink, i) for i in ids}
    assert len(prints) == len(ids), "distinct branch points must differ"


# ------------------------------------------------- audit coverage boundary

def test_branch_fingerprint_covers_the_decision_log_and_nothing_else():
    """Platform-audit boundary for the counterfactual pairing proof.

    The proof is a fingerprint over DECISION ROWS: every decision committed
    strictly before the target (full row), plus the target own pre-commit
    fields.  It is NOT a hash of simulator state.  There is no packet /
    queue / link / RNG / routing state hash anywhere in this platform --
    grep -rn "state_hash" CODE/ returns nothing -- so the strongest claim
    two paired runs support is:

        the two runs reached the same branch point AS WITNESSED BY THE
        DECISION LOG,

    and never "full state identical".  This test pins the covered set so
    the claim cannot be quietly inflated later.
    """
    rows = _rows()
    sink: list = []
    kernel.run_simulation(_resolved(), rows, geometry=_geo(),
                          decision_sink=sink)
    # Pick a branching decision that is NOT the first appended row, so the
    # "committed strictly before" half of the proof is non-empty.
    branches = [i for i, r in enumerate(sink)
                if r["sat"] == 0 and len(r["candidates"]) > 1]
    assert branches, "fixture needs a branching decision on satellite 0"
    index = branches[-1]
    assert index > 0, "fixture needs a covered row before the target"
    target = sink[index]
    tid = target["decision_id"]
    base = counterfactual.branch_fingerprint(sink, tid)

    def mutated(position, key, value):
        copy = [dict(r) for r in sink]
        copy[position][key] = value
        return copy

    # (a) the CHOSEN action is deliberately OUTSIDE the proof: the branch
    # point is what must be identical, and the action is what differs.
    assert "chosen" not in counterfactual.PRECOMMIT_FIELDS
    assert counterfactual.branch_fingerprint(
        mutated(index, "chosen", "__forced__"), tid) == base

    # (b) every declared pre-commit field of the TARGET is covered.
    # decision_id is the lookup key itself, so mutating it makes the
    # target unfindable rather than merely changing the digest; it is
    # asserted through that lookup instead.
    assert "decision_id" in counterfactual.PRECOMMIT_FIELDS
    with pytest.raises(counterfactual.CounterfactualError):
        counterfactual.branch_fingerprint(
            mutated(index, "decision_id", 10 ** 9), tid)
    for field in counterfactual.PRECOMMIT_FIELDS:
        if field == "decision_id":
            continue
        assert counterfactual.branch_fingerprint(
            mutated(index, field, "__mutated__"), tid) != base, \
            f"{field} is declared pre-commit but is not covered by the proof"

    # (c) an EARLIER APPENDED row is covered in FULL, not only via its
    # pre-commit fields -- that is what makes "arrived the same way"
    # checkable.  Coverage is by APPEND ORDER, not by decision_id:
    # decision ids are allocated at decision start, so a larger id can be
    # appended before the target and a smaller one after it.  A row
    # appended after the target is deliberately NOT covered, and this is
    # asserted rather than assumed.
    assert counterfactual.branch_fingerprint(
        mutated(index - 1, "chosen", "__mutated__"), tid) != base
    after = next((i for i in range(index + 1, len(sink))
                  if sink[i]["decision_id"] != tid), None)
    if after is not None:
        assert counterfactual.branch_fingerprint(
            mutated(after, "t", 1.0), tid) == base, \
            "rows appended after the target are not part of the proof"

    # (d) the boundary is explicit: no simulator-state object is covered
    covered = set(counterfactual.PRECOMMIT_FIELDS)
    assert covered == {
        "t", "t_decision_start", "decision_id", "state_version",
        "pid", "src", "dst", "sat", "kind", "policy",
        "candidates", "own_queue_bits", "obs", "info_audit"}
    for absent in ("rng_state", "link_state", "queue_state",
                   "topology_hash", "packet_state"):
        assert absent not in covered
