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


def _run(mode: str, up_before: bool, delay: float = DELAY):
    sink, timeline = [], []
    rows = [row(99, 0.0, A, B, bits=BITS), row(1, TARGET_EMIT, A, B, bits=BITS)]
    res = kernel.run_simulation(_cfg(mode, delay), rows, geometry=_geo(up_before),
                                decision_sink=sink, timeline_sink=timeline)
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


def test_frozen_may_not_be_combined_with_forced_actions():
    with pytest.raises(kernel.KernelError, match="forced_actions"):
        kernel.Kernel(_cfg("frozen"), [], geometry=_geo(True),
                      forced_actions={0: "E"})


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
