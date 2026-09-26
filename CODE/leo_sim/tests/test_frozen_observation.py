"""T1-COMPUTE-DELAY observation semantics: frozen vs refresh.

The two modes answer different questions and must never be conflated:

* `refresh` (historical default) defers the decision and then RE-READS the
  state when the computation lands.  It can represent a slow decision, but it
  cannot represent a state that went stale DURING the computation, because the
  re-read erases exactly that.  Measured consequence: with the only ISL down at
  decision start and up at commit, refresh commits a forward that the decision
  was in no position to know about.
* `frozen` takes the observation at decision start, infers on it, and at
  commit time only checks legality.  A rejected action is recorded and the
  packet is parked; it is never silently replaced by an optimum re-solved
  against fresh state.

The fixture flips the availability of the only ISL direction strictly inside
the decision interval, so "what the decision could know" and "what is true at
commit" disagree by construction.  The target packet is preceded by a warm-up
packet because the destination endpoint is created lazily on its first
decision and its association cannot complete inside the same synchronous step;
without the warm-up the first observation legitimately reports no_info.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import config, kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)
FLIP = 6.0
BITS = 8_000_000
# Decision starts at 5.082, commits at 7.082 with delay=2.0, so FLIP=6.0 lies
# strictly inside every measured interval.
TARGET_EMIT = 5.0
DELAY = 2.0


def _geo(up_before: bool) -> StaticGeometry:
    """The only ISL direction is available on one side of FLIP only."""
    def nb_at(s, dirs, t):
        up = (t < FLIP) if up_before else (t >= FLIP)
        return dict(NB.get(s, {})) if up else {}
    return StaticGeometry(2, neighbors_map=NB, visible=VIS,
                          isl_changes=[FLIP], neighbors_at_fn=nb_at)


def _cfg(mode: str, delay: float = DELAY) -> dict:
    return make_cfg({
        "scenario": {"duration_s": 12.0},
        "execution": {"compute_delay_s": delay,
                      "decision_observation_mode": mode},
        # pin access retention: a lapsed destination association would
        # confound the geometry lever with an information outage
        "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
    })


def _run(mode: str, up_before: bool, delay: float = DELAY, forced=None):
    sink, timeline = [], []
    rows = [row(99, 0.0, A, B, bits=BITS), row(1, TARGET_EMIT, A, B, bits=BITS)]
    res = kernel.run_simulation(_cfg(mode, delay), rows, geometry=_geo(up_before),
                                decision_sink=sink, timeline_sink=timeline,
                                forced_actions=forced)
    return res, sink, timeline


def _target_rows(sink):
    """(t_decision_start, kind, chosen) for the target packet only.

    t_decision_start is the instant the observation was taken, so it is what
    says WHICH state a committed action could possibly have been inferred
    from."""
    return [(round(r["t_decision_start"], 4), r["kind"], r["chosen"])
            for r in sink if r.get("pid") == 1]


def _target_marks(timeline, milestone):
    return [m for m in timeline
            if m.get("pid") == 1 and m.get("milestone") == milestone]


# ------------------------------------------------------------------- config

def test_the_default_mode_is_refresh():
    resolved = config.resolve_config({})
    assert resolved["config"]["execution"]["decision_observation_mode"] == "refresh"


def test_an_unknown_observation_mode_is_rejected():
    for bad in ("stale", "FROZEN", "", True, None):
        with pytest.raises(config.ConfigError):
            config.resolve_config(
                {"execution": {"decision_observation_mode": bad}})


def test_frozen_without_a_compute_delay_is_a_config_error():
    """With no computation time there is no interval to freeze, so asking for
    the mode is a configuration error rather than a silent no-op."""
    with pytest.raises(config.ConfigError, match="requires"):
        config.resolve_config(
            {"execution": {"decision_observation_mode": "frozen",
                           "compute_delay_s": 0.0}})


def test_the_mode_is_part_of_the_config_identity():
    """A new config key changes config_sha256 for every configuration, so
    previously compiled manifests and authorizations cannot be reused against
    this code.  Deliberate, and pinned here so it cannot regress silently."""
    base = config.resolve_config({})
    frozen = config.resolve_config(
        {"execution": {"decision_observation_mode": "frozen",
                       "compute_delay_s": 1.0}})
    assert base["sha256"] != frozen["sha256"]


# ------------------------------------------------------------ v1 boundaries

def test_frozen_may_not_be_combined_with_a_learning_arm():
    cfg = _cfg("frozen")
    cfg["config"]["learning"]["algorithm"] = "qlearning"
    cfg["config"]["routing"]["learning_enabled"] = True
    with pytest.raises(kernel.KernelError, match="learning arm"):
        kernel.Kernel(cfg, [], geometry=_geo(True))


# ------------------------------------- T1-FROZEN-BRANCH: forcing at t_observe

STEADY_VIS = VIS


def _steady_run(forced=None):
    """The negative-control geometry: nothing changes across the interval, so
    the target's first observation is a legal forward that also commits."""
    sink, timeline = [], []
    rows = [row(99, 0.0, A, B, bits=BITS), row(1, TARGET_EMIT, A, B, bits=BITS)]
    res = kernel.run_simulation(
        _cfg("frozen"), rows,
        geometry=StaticGeometry(2, neighbors_map=NB, visible=STEADY_VIS),
        decision_sink=sink, timeline_sink=timeline, forced_actions=forced)
    return res, sink, timeline


def _committed_branch_id() -> int:
    """The target packet's first committed decision id, discovered from an
    unforced run.  The harness is given a decision id by its caller, so the
    tests discover it the same way rather than hard-coding an allocation order
    a future change would silently invalidate."""
    _res, sink, _tl = _steady_run()
    mine = [r["decision_id"] for r in sink if r.get("pid") == 1]
    assert mine, "the control fixture must commit a decision for pid 1"
    return mine[0]


def _held_branch_id() -> int:
    """The target packet's HOLD attempt id: the flip fixture where the ISL is
    down at t_observe, so no forward direction was legal at the branch point."""
    _res, _sink, timeline = _run("frozen", up_before=False)
    holds = _target_marks(timeline, "frozen_inferred_hold")
    assert holds, "the flip fixture must record the hold attempt"
    return holds[0]["decision_id"]


def test_frozen_accepts_a_forced_action_taken_from_the_observation_legal_set():
    """A frozen branch point is obs["t_observe"], so an override must be
    resolved against the legal set the observation recorded -- and the
    substitution must be auditable at that instant."""
    decision_id = _committed_branch_id()
    res, _sink, timeline = _steady_run(forced={decision_id: "E"})
    forced = [m for m in timeline
              if m.get("milestone") == "forced_action"
              and m.get("decision_id") == decision_id]
    assert len(forced) == 1, "the substitution must be recorded exactly once"
    mark = forced[0]
    assert mark["obs_mode"] == "frozen"
    assert mark["branch_instant"] == "observation"
    assert mark["t_observe"] == pytest.approx(5.082), \
        "the branch instant is the OBSERVATION, not the commit"
    assert mark["original"] == "E" and mark["forced"] == "E"
    assert mark["legal_at_branch_point"] == ["E"]
    assert res["fates"][1] == "DELIVERED"


def test_a_forced_action_illegal_at_the_frozen_branch_point_fails_loud():
    """Forcing outside the observation's legal set would commit an action the
    kernel had already refused; that is a different experiment, not a
    counterfactual, so it must refuse rather than silently drop the override."""
    decision_id = _committed_branch_id()
    with pytest.raises(kernel.KernelError, match="frozen branch point"):
        _steady_run(forced={decision_id: "W"})


def test_forcing_a_frozen_hold_branch_point_fails_loud():
    """When the observation inferred a hold there was no legal forward
    direction at the branch point, so no counterfactual action exists."""
    decision_id = _held_branch_id()
    with pytest.raises(kernel.KernelError, match="frozen branch point"):
        _run("frozen", up_before=False, forced={decision_id: "E"})


def test_no_override_leaves_a_frozen_run_bit_identical():
    """Negative control: supplying no forced_actions must not move a single
    recorded byte, so the new capability is strictly opt-in."""
    def run(**kw):
        sink, timeline = [], []
        rows = [row(99, 0.0, A, B, bits=BITS), row(1, TARGET_EMIT, A, B, bits=BITS)]
        res = kernel.run_simulation(_cfg("frozen"), rows, geometry=_geo(True),
                                    decision_sink=sink, timeline_sink=timeline,
                                    **kw)
        return (res["fates"], res["totals"], sink, timeline)
    plain = run()
    empty = run(forced_actions={})
    assert plain[0] == empty[0] and plain[1] == empty[1]
    assert plain[2] == empty[2], "decision rows must be unchanged"
    assert plain[3] == empty[3], "timeline rows must be unchanged"


# ------------------------------------------------- the discriminating cases

def test_refresh_uses_information_that_did_not_exist_when_it_started():
    """ISL down at decision start, up at commit.  refresh must commit the
    forward -- that is the defect this mode is being contrasted with."""
    res, sink, timeline = _run("refresh", up_before=False)
    rows = _target_rows(sink)
    assert rows and rows[0][0] == pytest.approx(5.082), \
        "refresh observes and decides at the same instant"
    assert rows[0][1:] == ("forward", "E"), \
        "the ISL was down at 5.082, so this forward is not knowable then"
    assert res["fates"][1] == "DELIVERED"


def test_frozen_refuses_to_use_information_that_appeared_during_compute():
    """Same fixture, frozen: the observation said hold, so the commit holds --
    even though the ISL is up by then and a re-solve would have forwarded."""
    res, sink, timeline = _run("frozen", up_before=False)
    rows = _target_rows(sink)
    assert rows, "the packet is expected to act once the ISL is genuinely up"
    assert all(start > FLIP for start, _k, _c in rows), \
        f"no attempt may be committed from an observation taken while the ISL " \
        f"was down; got observation instants {[r[0] for r in rows]}"
    holds = _target_marks(timeline, "frozen_inferred_hold")
    assert holds, "the observation's own verdict must be recorded"
    assert holds[0]["inferred_at"] == pytest.approx(5.082)
    assert holds[0]["at"] == pytest.approx(7.082), "recorded at commit time"
    assert holds[0]["status"] == "ok", \
        "the hold came from legality at t0, not from missing information"


def test_frozen_records_a_rejected_action_instead_of_resolving_again():
    """ISL up at decision start, down at commit: the inferred forward is
    refused and the refusal is auditable.  refresh, by contrast, produces no
    decision row at all and simply holds, hiding that a legal-at-t0 action was
    discarded."""
    res, sink, timeline = _run("frozen", up_before=True)
    assert _target_rows(sink) == [], "a rejected action is not a commit"
    rejected = _target_marks(timeline, "commit_rejected")
    assert len(rejected) == 1
    assert rejected[0]["action"] == "E"
    assert rejected[0]["reason"] == "action_no_longer_legal"
    assert rejected[0]["inferred_at"] == pytest.approx(5.082)
    assert rejected[0]["at"] == pytest.approx(7.082)
    assert _target_marks(timeline, "hold"), "the packet must be parked"
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP"

    _res_r, sink_r, tl_r = _run("refresh", up_before=True)
    assert _target_rows(sink_r) == []
    assert not _target_marks(tl_r, "commit_rejected"), \
        "refresh has no concept of a rejected action: it re-solves instead"


def test_frozen_agrees_with_refresh_when_nothing_changes_in_the_window():
    """Negative control: with the state static across the interval the two
    modes must commit exactly the same action, so the difference above cannot
    be an artefact of the frozen path being broken in general."""
    def steady(mode):
        sink, timeline = [], []
        rows = [row(99, 0.0, A, B, bits=BITS), row(1, TARGET_EMIT, A, B, bits=BITS)]
        kernel.run_simulation(
            _cfg(mode), rows,
            geometry=StaticGeometry(2, neighbors_map=NB, visible=VIS),
            decision_sink=sink, timeline_sink=timeline)
        return sink
    frozen_rows = _target_rows(steady("frozen"))
    assert frozen_rows, "the control fixture must actually decide"
    assert frozen_rows == _target_rows(steady("refresh"))


# ------------------------------------------- audit boundary: audit trail

def _run_without_timeline(mode: str, up_before: bool, delay: float = DELAY):
    """Same fixture, decision sink only: no timeline stream is attached."""
    sink: list = []
    rows = [row(99, 0.0, A, B, bits=BITS), row(1, TARGET_EMIT, A, B, bits=BITS)]
    res = kernel.run_simulation(_cfg(mode, delay), rows,
                                geometry=_geo(up_before), decision_sink=sink)
    return res, sink


def test_frozen_refusals_are_visible_only_through_the_timeline_sink():
    """Audit boundary: a refused frozen commit is not a decision row.

    ``_record_decision`` writes only on a COMMIT (kernel.py:3571), so an
    attempt that is inferred at t0 and refused at commit leaves NO row in
    the decision sink; the refusal is recorded as ``commit_rejected`` on the
    timeline sink (kernel.py:3991-4005).  That is a deliberate output-only
    design -- nothing is allocated when nothing records -- but it is a real
    analysis boundary:

      an analyst holding only decision rows cannot see that a frozen action
      was inferred, attempted and refused, so the frozen cost is invisible
      and "acted late" is indistinguishable from "tried and was refused".

    The BEHAVIOUR is identical either way; only the audit trail differs.
    Pinned here so the limitation is stated rather than discovered.
    """
    # the discriminating fixture: ISL up at t0, down at commit
    res, sink = _run_without_timeline("frozen", up_before=True)
    assert [r for r in sink if r.get("pid") == 1] == [], \
        "a refused commit must not produce a decision row"
    assert res["fates"][1] == "IN_SYSTEM_AT_STOP", \
        "behaviour is unchanged; only the audit trail is absent"

    # the same fixture WITH a timeline does record the refusal, and the
    # record carries the instant the action was inferred from
    _res, _sink, timeline = _run("frozen", up_before=True)
    rejected = _target_marks(timeline, "commit_rejected")
    assert len(rejected) == 1
    assert rejected[0]["action"] == "E"
    assert rejected[0]["reason"] == "action_no_longer_legal"
    assert rejected[0]["t_observed"] == pytest.approx(5.082)
    assert rejected[0]["inferred_at"] == pytest.approx(5.082)
    assert rejected[0]["at"] == pytest.approx(7.082)

    # and the t0 observation is attached, so the refused attempt stays
    # attributable to the state it was actually based on
    observation = rejected[0]["observation_at_start"]
    assert observation["mode"] == "frozen"
    assert observation["source"] == "frozen_snapshot_before_compute"
    assert observation["schema"] == "leo-sim-observation-at-start/v1"
    assert observation["t_observed"] == pytest.approx(5.082)
