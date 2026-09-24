"""T1-FROZEN-LEDGER: one decision must keep four SEPARATE time facts.

The defect this file pins: a frozen decision row used to carry a single
ambiguous set of numbers.  t_measure was set to the commit instant even though
the frozen mode infers from the snapshot taken at decision start, and the
info_audit truth was recorded at commit time from the neighbour's real queues
and the neighbour's own cache.  An offline analysis therefore read "decided
from a stale observation" as "decided from the state at commit", and read a
hindsight truth as the quality of the decision-time belief.

The fix is a separation, not a renamed scalar:

* observation_at_start -- what the decision was actually based on, with the
  measurement time of EVERY contributing neighbour;
* estimate_at_start    -- the only prediction that observation legally
  supported (None when it supported none; never backfilled from truth);
* truth_at_commit      -- the kernel truth at commit (the old audit content);
* truth_at_target      -- the realized truth of the resource the packet really
  contended for, MISSING while that instant has not happened.

The fixture that makes the difference visible flips the state strictly inside
the decision interval, so "what could be known at t0" and "what was true at
t1" disagree by construction.
"""
from __future__ import annotations

import pytest

from CODE.leo_sim import decision_ledger, kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)

# 3-satellite line 0 - 1 - 2, only the ends see a cell, so a packet A -> B is
# forwarded twice and the first hop's peer really does own an onward egress.
LINE_NB = {0: {"E": 1}, 1: {"E": 2, "W": 0}, 2: {"W": 1}}

# The 0-1 ISL of the line is up on one side of FLIP only, so the first hop's
# inferred forward stops being legal strictly inside the decision interval
# while the peer keeps its own onward egress.
FLIP = 6.0
TARGET_EMIT = 5.0
DELAY = 2.0
BITS = 8_000_000


def _line_geo():
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 2 and (lat, lon) == BC)
    return StaticGeometry(3, neighbors_map=LINE_NB, visible=vis)


def _line_flip_geo(up_before: bool):
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                                 (s == 2 and (lat, lon) == BC)

    def nb_at(s, dirs, t):
        up = (t < FLIP) if up_before else (t >= FLIP)
        if s == 0:
            return {"E": 1} if up else {}
        if s == 1:
            return {"E": 2, "W": 0} if up else {"E": 2}
        return {"W": 1}
    return StaticGeometry(3, neighbors_map=LINE_NB, visible=vis,
                          isl_changes=[FLIP], neighbors_at_fn=nb_at)


def _cfg(mode="frozen", delay=DELAY, duration=60.0, num_sats=3,
         isl_rate_mbps=1.0, cp=True, policy="oracle"):
    return make_cfg({
        "scenario": {"duration_s": duration, "num_satellites": num_sats,
                     "num_planes": 1, "seed": 3},
        "control_plane": {"enabled": cp},
        "routing": {"policy": policy},
        "links": {"isl_rate_mbps": isl_rate_mbps},
        "execution": {"compute_delay_s": delay,
                      "decision_observation_mode": mode},
        "access": {"idle_release_s": 100.0, "slot_lease_s": 100.0},
    })


def _run(overrides_cfg, rows, geometry):
    sink, timeline = [], []
    res = kernel.run_simulation(overrides_cfg, rows, geometry=geometry,
                                decision_sink=sink, timeline_sink=timeline)
    return res, sink, timeline


# --------------------------------------------------------------- t_measure

def test_frozen_t_measure_is_the_observation_instant_not_the_commit():
    """A frozen action comes from the observation taken BEFORE the compute
    interval, so t_measure must equal t_decision_start.  A scalar equal to the
    commit instant would report a decision that was never made."""
    _, sink, timeline = _run(
        _cfg("frozen"), [row(99, 0.0, A, B, bits=BITS),
                         row(1, 13.0, A, B, bits=BITS)], _line_geo())
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    assert by_decision, "fixture must decide"
    for entry in by_decision.values():
        assert entry["obs_mode"] == "frozen"
        assert entry["t_decision_start"] < entry["t_decision_commit"], \
            "the compute delay must separate the two instants"
        assert entry["t_measure"] == entry["t_decision_start"]
        assert entry["t_measure"] != entry["t_decision_commit"]


def test_refresh_t_measure_stays_the_commit_instant():
    """The complement: refresh re-reads the live state when the computation
    lands, so its information instant IS the commit instant -- and the record
    says so instead of leaving it to be inferred."""
    _, sink, timeline = _run(
        _cfg("refresh"), [row(99, 0.0, A, B, bits=BITS),
                          row(1, 13.0, A, B, bits=BITS)], _line_geo())
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    assert by_decision, "fixture must decide"
    for entry in by_decision.values():
        assert entry["obs_mode"] == "refresh"
        assert entry["t_decision_start"] < entry["t_decision_commit"]
        assert entry["t_measure"] == entry["t_decision_commit"]
        observation = entry["observation_at_start"]
        assert observation["mode"] == "refresh"
        assert observation["source"] == "commit_time_state"
        assert observation["t_observed"] == entry["t_decision_commit"]


def test_an_unknown_observation_mode_is_refused_not_guessed():
    """The folder branches on the recorded mode.  A row carrying a mode it
    does not know must fail loud instead of being folded as refresh."""
    row_ = {"decision_id": 1, "t": 1.0, "t_decision_start": 0.5,
            "obs_mode": "stale"}
    with pytest.raises(ValueError, match="unknown obs_mode"):
        decision_ledger.build_ledger([row_], [])


# ----------------------------------------------------- the four objects

def _changing_state_run():
    """Frozen fixture whose state provably changes inside the window.

    packet 99 warms the network up; packet 1 is decided at 13.082 and commits
    at 15.082, while the peer's egress backlog moves between the advertisement
    the decision could see and the truth at commit.
    """
    return _run(_cfg("frozen"), [row(99, 0.0, A, B, bits=BITS),
                                 row(1, 13.0, A, B, bits=BITS)],
                _line_geo())


def _first_target_decision(sink):
    for row_ in sink:
        if row_["pid"] == 1 and row_["kind"] == "forward":
            return row_
    raise AssertionError("fixture must commit a forward for the target packet")


def test_the_four_objects_exist_and_are_four_different_facts():
    _, sink, timeline = _changing_state_run()
    row_ = _first_target_decision(sink)
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    entry = by_decision[row_["decision_id"]]

    observation = entry["observation_at_start"]
    estimate = entry["estimate_at_start"]
    truth_commit = entry["truth_at_commit"]
    truth_target = entry["truth_at_target"]
    for name, obj in (("observation_at_start", observation),
                      ("estimate_at_start", estimate),
                      ("truth_at_commit", truth_commit),
                      ("truth_at_target", truth_target)):
        assert isinstance(obj, dict), name
    assert len({id(obj) for obj in (observation, estimate, truth_commit,
                                    truth_target)}) == 4, \
        "the four facts must be four separate objects"

    # they cannot be confused by their provenance ...
    assert observation["source"] == "frozen_snapshot_before_compute"
    assert truth_commit["source"] == "kernel_state_at_commit"
    assert truth_target["source"] == "peer_arrival_egress_snapshot"
    # ... nor by the instant each describes
    assert observation["t_observed"] == pytest.approx(13.082, abs=1e-3)
    assert observation["t_observed"] == entry["t_decision_start"]
    assert truth_commit["t_observed"] == pytest.approx(15.082, abs=1e-3)
    assert truth_commit["t_observed"] == entry["t_decision_commit"]
    assert truth_commit["t_observed"] != observation["t_observed"]
    assert truth_target["status"] == "ok"
    assert truth_target["t_arrival"] > truth_commit["t_observed"]

    # and the CONTENT differs where the fixture makes the state move: the
    # peer's advertised egress backlog at t0 is not the real one at t1.
    advertised = observation["neighbours"]["1"]["advertised_isl_queue_bits"]
    real_at_commit = truth_commit["candidate_truth"]["E"][
        "peer_egress_queue_bits"]
    assert estimate["peer_egress_queue_bits_estimate"] == advertised["E"]
    assert real_at_commit > 0 and advertised["E"] != real_at_commit
    assert estimate["peer_egress_queue_bits_estimate"] != real_at_commit


def test_the_estimate_is_built_from_t0_information_never_from_truth():
    """The core of the fix: the deployable prediction must not be backfilled
    with the commit-time truth audit."""
    _, sink, timeline = _changing_state_run()
    row_ = _first_target_decision(sink)
    estimate = row_["estimate_at_start"]
    assert estimate is not None
    assert estimate["truth_used"] is False
    assert estimate["t_observed"] == row_["t_decision_start"]
    assert estimate["prediction_method"] == \
        "same_policy_on_advertised_peer_state"
    neighbour = row_["observation_at_start"]["neighbours"][
        str(estimate["for_peer"])]
    assert estimate["peer_egress_queue_bits_estimate"] == \
        neighbour["advertised_isl_queue_bits"][
            estimate["peer_egress_direction"]]
    assert estimate["neighbour_measurement"]["origin"] == estimate["for_peer"]
    assert estimate["neighbour_measurement"]["received_at"] == \
        neighbour["received_at"]
    # the advertisement carries no in-service work, and the record says so
    # instead of inventing a number
    assert estimate["peer_in_service_remaining_bits_estimate"] is None
    assert "peer_in_service_remaining_bits" in estimate["not_advertised"]
    # the truth at commit DID know that quantity: the two are different facts
    downstream = row_["truth_at_commit"]["candidate_truth"]["E"]["downstream"]
    assert downstream["peer_in_service_remaining_bits"] is not None


def test_the_estimate_names_the_information_it_actually_used():
    """The intended T1 configuration is a deterministic router with learning
    OFF, where what a node can know about its neighbour is exactly the control
    advertisement it received.  The estimate must say so -- and must be the
    advertised number, not the neighbour's real backlog."""
    _, sink, _ = _run(
        _cfg("frozen", policy="hop"),
        [row(i, 3.0 * i, A, B, bits=BITS) for i in range(1, 5)], _line_geo())
    candidates = [row_ for row_ in sink
                  if row_["kind"] == "forward" and row_["estimate_at_start"]]
    assert candidates, "a cache-driven run must still commit forwards"
    checked = 0
    for row_ in candidates:
        estimate = row_["estimate_at_start"]
        assert estimate["information_source"] == "control_cache"
        assert estimate["truth_used"] is False
        peer = str(estimate["for_peer"])
        neighbour = row_["observation_at_start"]["neighbours"][peer]
        if estimate["peer_egress_direction"] is None:
            continue
        assert estimate["peer_egress_queue_bits_estimate"] == \
            neighbour["advertised_isl_queue_bits"][
                estimate["peer_egress_direction"]]
        assert estimate["neighbour_measurement"]["received_at"] == \
            neighbour["received_at"]
        assert estimate["neighbour_measurement"]["received_at"] <= \
            row_["t_decision_start"]
        checked += 1
    assert checked, "the fixture must predict a downstream egress from cache"


def test_no_prediction_is_recorded_as_none_not_as_a_truth_value():
    """A deliver decision contends for no ISL egress, so it supports no
    downstream prediction: the record must be None with a stated reason, not
    a number copied from the audit."""
    _, sink, _ = _changing_state_run()
    delivers = [row_ for row_ in sink if row_["kind"] == "deliver"]
    assert delivers, "fixture must deliver"
    for row_ in delivers:
        assert row_["estimate_at_start"] is None
        assert row_["observation_at_start"][
            "estimate_unavailable_reason"] == \
            "no_downstream_isl_resource_for_deliver"
        assert row_["truth_at_commit"]["candidate_truth"] == {}


# --------------------------------------------- rejected / held attempts

def _reject_run():
    """ISL up at decision start, down at commit: the inferred forward is
    refused, and the packet must pay for another computation."""
    return _run(_cfg("frozen"), [row(99, 0.0, A, B, bits=BITS),
                                 row(1, TARGET_EMIT, A, B, bits=BITS)],
                _line_flip_geo(up_before=True))


def test_a_rejected_frozen_attempt_keeps_its_observation_and_reason():
    _, sink, timeline = _reject_run()
    attempts, diagnostics = decision_ledger.build_attempts(sink, timeline)
    rejected = [a for a in attempts if a["outcome"] == "rejected"]
    assert len(rejected) == 1, "the fixture must produce exactly one rejection"
    attempt = rejected[0]
    assert attempt["reason"] == "action_no_longer_legal"
    assert attempt["mode"] == "frozen"
    assert attempt["inferred_action"] == "E"
    assert attempt["t_observed"] == pytest.approx(5.082, abs=1e-3)
    assert attempt["t_outcome"] == pytest.approx(7.082, abs=1e-3)
    assert attempt["has_observation"] is True
    observation = attempt["observation_at_start"]
    assert observation["mode"] == "frozen"
    assert observation["t_observed"] == attempt["t_observed"]
    # the action was legal WHERE IT WAS INFERRED, which is exactly why the
    # rejection is a fact worth keeping
    assert "E" in observation["legal_directions"]
    assert attempt["estimate_at_start"]["peer_egress_direction"] == "E"
    assert diagnostics["by_outcome"]["rejected"] == 1
    assert diagnostics["without_observation"] == []
    # a rejected action is not a commit: no decision row may claim it
    assert attempt["decision_id"] not in {r["decision_id"] for r in sink}


def test_a_held_frozen_attempt_keeps_its_observation_and_reason():
    _, sink, timeline = _reject_run()
    attempts, _ = decision_ledger.build_attempts(sink, timeline)
    held = [a for a in attempts if a["outcome"] == "held"]
    assert held, "the fixture must produce holds"
    for attempt in held:
        assert attempt["reason"] == "observation_inferred_hold"
        assert attempt["mode"] == "frozen"
        assert attempt["has_observation"] is True
        observation = attempt["observation_at_start"]
        assert observation["kind"] == "hold"
        assert observation["mode"] == "frozen"
        assert observation["t_observed"] == attempt["t_observed"]
        assert observation["t_observed"] < attempt["t_outcome"], \
            "a hold is judged at commit time against an observation from t0"
        assert attempt["decision_id"] not in {r["decision_id"] for r in sink}


def test_a_frozen_redecision_records_the_observation_it_restarted_from():
    """A re-decision is an attempt too.  In frozen mode it restarts from a
    NEW observation taken at its own start, so the milestone must name that
    instant instead of the commit it is emitted at."""
    _, sink, timeline = _changing_state_run()
    attempts, _ = decision_ledger.build_attempts(sink, timeline)
    by_id = {a["decision_id"]: a for a in attempts}
    relinks = [m for m in timeline if m["milestone"] == "redecision"]
    assert relinks, "the fixture must re-decide"
    for mark in relinks:
        assert mark["obs_mode"] == "frozen"
        assert mark["observed_at"] == by_id[mark["decision_id"]]["t_observed"]
        assert mark["at"] != mark["observed_at"], \
            "the commit of a frozen attempt is later than its observation"
        assert mark["prev_decision_id"] in by_id, \
            "the previous attempt must be enumerated, not dropped"
        assert by_id[mark["decision_id"]]["prev_decision_id"] == \
            mark["prev_decision_id"]


def test_committed_attempts_are_still_enumerated_next_to_the_rejections():
    """Attempts are a superset of commits: dropping the non-commits would
    hide the computation the rejected tries already paid for."""
    _, sink, timeline = _reject_run()
    attempts, diagnostics = decision_ledger.build_attempts(sink, timeline)
    committed = [a for a in attempts if a["outcome"] == "committed"]
    assert {a["decision_id"] for a in committed} == \
        {r["decision_id"] for r in sink}
    assert diagnostics["attempts"] == len(attempts)
    assert diagnostics["attempts"] > len(sink)
    assert len({a["decision_id"] for a in attempts}) == len(attempts)


# --------------------------------------------- per-neighbour measurement

def test_every_neighbour_keeps_its_own_measurement_time():
    """A scalar t_measure cannot represent several advertisements generated
    and received at different instants, so the observation carries one
    measurement record per origin."""
    _, sink, _ = _changing_state_run()
    observations = [r["observation_at_start"] for r in sink]
    assert any(len(o["neighbours"]) >= 2 for o in observations), \
        "the fixture must show a decision informed by more than one origin"
    distinct_received = False
    distinct_generated = False
    for observation in observations:
        for origin, neighbour in observation["neighbours"].items():
            assert neighbour["origin"] == int(origin)
            assert neighbour["measurement"] == "control_cache_advertisement"
            for field in ("generated_at", "received_at", "age_s", "hops"):
                assert field in neighbour
            # an advertisement that has not arrived yet is not information
            assert neighbour["received_at"] <= observation["t_observed"]
            assert neighbour["generated_at"] <= neighbour["received_at"]
        received = {n["received_at"] for n in observation["neighbours"].values()}
        generated = {n["generated_at"]
                     for n in observation["neighbours"].values()}
        distinct_received = distinct_received or len(received) >= 2
        distinct_generated = distinct_generated or len(generated) >= 2
    assert distinct_received, "per-origin arrivals must differ"
    assert distinct_generated, "per-origin measurement instants must differ"


def test_t_measure_is_not_a_stand_in_for_the_neighbour_measurements():
    _, sink, timeline = _changing_state_run()
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    separated = 0
    for decision_id, entry in by_decision.items():
        observation = entry["observation_at_start"]
        arrivals = {n["received_at"] for n in observation["neighbours"].values()}
        if arrivals and entry["t_measure"] not in arrivals:
            separated += 1
    assert separated, "t_measure must not be presented as a neighbour's " \
                      "measurement instant"


# ------------------------------------------------------- truth_at_target

def test_truth_at_target_is_missing_when_the_packet_never_arrives():
    """packet 2 is forwarded at 22.082 on a link that needs ~8 s, so its
    arrival falls past the 30 s horizon: the target instant never happened and
    must read MISSING, never 0 and never the commit-time value."""
    _, sink, timeline = _run(
        _cfg("frozen", duration=30.0),
        [row(1, 0.0, A, B, bits=BITS), row(2, 20.0, A, B, bits=BITS)],
        _line_geo())
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    late = [e for e in by_decision.values()
            if e["pid"] == 2 and e["kind"] == "forward"]
    assert late, "the fixture must commit a forward that never lands"
    for entry in late:
        target = entry["truth_at_target"]
        assert target["status"] == decision_ledger.MISSING
        assert target["reason"] == "no_arrival_recorded"
        assert target["t_arrival"] == decision_ledger.MISSING
        assert target["egress"] is None
        # the commit truth existed and was NOT copied into the target slot
        assert entry["truth_at_commit"]["t_observed"] == \
            entry["t_decision_commit"]
        assert entry["truth_at_commit"]["candidate_truth"]
    # ... while a hop that really arrives carries the realized snapshot
    arrived = [e for e in by_decision.values()
               if e["truth_at_target"]["status"] == "ok"]
    assert arrived, "fixture must also record a real arrival"


def test_truth_at_target_does_not_read_as_zero_for_a_delivered_hop():
    """A hop whose peer already serves the destination contends for no ISL
    egress; that absence must be explicit, with the arrival instant still
    reported because it did happen."""
    _, sink, timeline = _changing_state_run()
    by_decision, _ = decision_ledger.build_ledger(sink, timeline)
    delivered_at_peer = [
        e for e in by_decision.values()
        if e["truth_at_target"]["status"] == decision_ledger.MISSING
        and e["truth_at_target"]["reason"] == "peer_delivered_the_packet"]
    assert delivered_at_peer, "the second hop's peer serves the destination"
    for entry in delivered_at_peer:
        target = entry["truth_at_target"]
        assert target["egress"] is None
        assert target["t_arrival"] != decision_ledger.MISSING


# ----------------------------------------------------------- scoring split

def test_the_two_scores_are_different_quantities():
    """score_downstream_predictions scores the commit-time truth audit (a
    hindsight upper bound); score_start_estimates scores the t0-legal belief.
    Reading the first as the second is the defect this split removes."""
    _, sink, timeline = _changing_state_run()
    deployable, summary = decision_ledger.score_start_estimates(sink, timeline)
    hindsight, hindsight_summary = \
        decision_ledger.score_downstream_predictions(sink, timeline)
    assert summary["source"] == "estimate_at_start"
    assert summary["truth_used"] is False
    assert hindsight_summary["source"] == "info_audit_truth_at_commit"
    assert summary["scored_decisions"] == hindsight_summary["scored_decisions"]
    target = _first_target_decision(sink)
    score = deployable[target["decision_id"]]
    assert score["estimate_available"] is True
    assert score["predicted_egress_bits"] == \
        target["estimate_at_start"]["peer_egress_queue_bits_estimate"]
    assert hindsight[target["decision_id"]]["predicted_egress_bits"] == \
        target["truth_at_commit"]["candidate_truth"]["E"][
            "peer_egress_queue_bits"]
    assert score["predicted_egress_bits"] != \
        hindsight[target["decision_id"]]["predicted_egress_bits"]


# --------------------------------------------------------- negative control

def _result_fingerprint(res):
    return {key: res[key] for key in (
        "fates", "fate_counts", "totals", "deliveries", "occupied",
        "queue_area_bits_s", "access", "service_log", "handover",
        "events_processed", "packet_events", "link_service_windows")}


@pytest.mark.parametrize("mode", ["frozen", "refresh"])
def test_the_record_fields_do_not_change_behavior_bit_for_bit(mode,
                                                               monkeypatch):
    """Negative control: attaching the sinks and BUILDING the new fields must
    leave the simulation bit-identical.  The switch is the record-building
    code itself -- the two helpers that compute the new objects are replaced
    by no-ops for the third run while the sink channel stays attached, so the
    comparison isolates the new records from the sinks that carry them."""
    cfg = _cfg(mode)
    rows = [row(i, 2.0 * i, A, B, bits=BITS) for i in (1, 2, 3)]
    geometry = _line_geo()

    silent = kernel.run_simulation(cfg, rows, geometry=geometry)

    sink, timeline = [], []
    recorded = kernel.run_simulation(cfg, rows, geometry=geometry,
                                     decision_sink=sink,
                                     timeline_sink=timeline)

    monkeypatch.setattr(kernel.Kernel, "_estimate_at_start",
                        lambda self, *a, **k: (None, "record_building_off"))
    monkeypatch.setattr(kernel.Kernel, "_observation_at_start",
                        lambda self, *a, **k: {"schema": "record_building_off"})
    sink_off, timeline_off = [], []
    unrecorded = kernel.run_simulation(cfg, rows, geometry=geometry,
                                       decision_sink=sink_off,
                                       timeline_sink=timeline_off)

    assert recorded["fates"] == silent["fates"]
    assert _result_fingerprint(recorded) == _result_fingerprint(silent)
    assert _result_fingerprint(unrecorded) == _result_fingerprint(silent)

    # the switch really did switch the new records off, while leaving the
    # lifecycle identical
    assert any(r["estimate_at_start"] is not None for r in sink) or mode == \
        "refresh", "records-on must actually build estimates where legal"
    assert all(r["estimate_at_start"] is None for r in sink_off)
    assert all(r["truth_at_commit"]["source"] == "kernel_state_at_commit"
               for r in sink)
    assert [r["decision_id"] for r in sink] == \
        [r["decision_id"] for r in sink_off]
    assert [(m["milestone"], m["decision_id"], m["at"]) for m in timeline] == \
        [(m["milestone"], m["decision_id"], m["at"]) for m in timeline_off]
    assert timeline, "the fixture must exercise the timeline channel"
