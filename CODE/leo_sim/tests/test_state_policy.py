"""State-policy tests (POLICY-SPEC-20261031 §7).

Three layers, matching the frozen spec:

1. pure-function unit tests for the frozen definitions (geometry ignores queue,
   queue_greedy is the existing ranking, hysteresis keeps inside eta and
   switches outside it, EWMA n=1 == greedy / alpha=1 == instantaneous /
   alpha->0 approaches the first sample);
2. config tests (defaults, fail-loud, and the policy entering config identity);
3. a tiny deterministic kernel fixture (helpers.StaticGeometry) for the legacy
   regression against the PRE-POLICY baseline (full packet-outcome, packet-event
   and link-service-window stream), geometry's queue-blindness under real queue
   drift, and the hysteresis switch reduction.

The fixture is a 4-satellite diamond: satellite 0 serves source cell A, is the
only satellite an A-packet can enter, and has two equal-length branches
``0 -> 1 -> 3`` and ``0 -> 2 -> 3``; satellite 3 serves destination cell B.
The two branches are geometrically identical, so every action change on
satellite 0 is caused by queue state (local egress wait and/or advertised peer
queue), which is exactly the drift the policies are supposed to differ on.

The golden digest below was captured from the UNMODIFIED code at branch base
``a90c1f9`` before ``state_policy`` existed, with the same fixture and seed.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from CODE.leo_sim import config, kernel, routing  # noqa: F401  (imports exist)
from CODE.leo_sim import state_policy as sp
from CODE.leo_sim import time_alignment as ta
from CODE.leo_sim.tests.helpers import (StaticGeometry, cell, cell_center,
                                        make_cfg, row)

# --------------------------------------------------------------------------
# fixture
# --------------------------------------------------------------------------
A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)

#: 0 = source (serves A), 3 = destination (serves B); 1 and 2 are the two
#: equal-length branches 0 -> 1 -> 3 and 0 -> 2 -> 3.
NB = {0: {"E": 1, "S": 2}, 1: {"W": 0, "S": 3}, 2: {"N": 0, "E": 3},
      3: {"N": 1, "W": 2}}


def _visible(sat, lat, lon, _t):
    if (lat, lon) == AC:
        return sat == 0
    if (lat, lon) == BC:
        return sat == 3
    return False


def _geometry():
    return StaticGeometry(4, neighbors_map=NB, visible=_visible)


_FIXTURE = {
    "scenario": {"duration_s": 6.0, "num_satellites": 4, "num_planes": 1,
                 "seed": 3},
    "demand": {"packet_bits": 20_000},
    "links": {"isl_rate_mbps": 10.0},
    "access": {"uplink_rate_mbps": 1000.0, "downlink_rate_mbps": 100.0,
               "acquisition_delay_s": 0.0,
               "idle_release_s": 100.0, "slot_lease_s": 100.0},
    "control_plane": {"enabled": True,
                      "advertisement_protocol_version": 2,
                      "advertise_interval_s": 0.05, "ttl_s": 10.0,
                      "vis_k": 4, "packet_bits": 20_000},
    "routing": {"policy": "hop"},
    "execution": {"decision_observation_mode": "frozen",
                  "compute_delay_s": 0.002},
}


def _cfg(**ta_overrides):
    ta_cfg = {"enabled": True, "arm": "candidate"}
    ta_cfg.update(ta_overrides)
    return make_cfg({**_FIXTURE, "time_alignment": ta_cfg})


def _rows(count, interval, bits=20_000):
    return [row(i, interval * i, A, B, bits=bits) for i in range(1, count + 1)]


#: 20 packets over 2 s: the exact trace of the pre-policy golden capture.
SMALL_ROWS = _rows(20, 0.1)
#: 300 packets over 0.45 s: saturates the branch ISLs so the local egress
#: queue actually drifts between decisions (about 70 greedy action flips).
JITTER_ROWS = _rows(300, 0.0015)


def _run(cfg, rows):
    sink = []
    res = kernel.run_simulation(cfg, list(rows), geometry=_geometry(),
                                decision_sink=sink)
    return res, sink


def _forwards(sink, sat=0):
    return [r for r in sink
            if r.get("kind") == "forward" and r["sat"] == sat]


def _policy_audit(row):
    """The ``ta_audit.state_policy`` block of one decision row."""
    return row["observation_at_start"]["time_alignment"]["state_policy"]


def _switches(directions):
    return sum(1 for before, after in zip(directions, directions[1:])
               if before != after)


def _result_signature(res):
    """Full outcome/service stream of one run (fates, deliveries, packet
    events, link service windows) as one SHA-256."""
    payload = {
        "fates": {str(k): v for k, v in res["fates"].items()},
        "deliveries": {
            str(k): {"delivered_at": round(float(v["delivered_at"]), 12),
                     "path": list(v["path"])}
            for k, v in res["deliveries"].items()},
        "events": [{k: v for k, v in e.items()} for e in res["packet_events"]],
        "windows": [{k: v for k, v in w.items()}
                    for w in res["link_service_windows"]],
    }
    blob = json.dumps(payload, sort_keys=True, default=str,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


#: Captured from the UNMODIFIED base commit (a90c1f9) with SMALL_ROWS, this
#: exact fixture and seed=3.  Any change to the legacy action order shows up
#: here as a different event/service stream, not merely a different return
#: value.
_LEGACY_GOLDEN_SHA256 = \
    "d7471c2dd90e11cfa41630cfa29e7e35a1389aea0d33dc415c259e44d5afcee6"


# ==========================================================================
# 1. pure function: geometry
# ==========================================================================
def _sample(resource, measured_at, received_at, queue_bits,
            rate_bps=10_000_000.0):
    return ta.StateSample(resource, measured_at, received_at, queue_bits,
                          rate_bps)


def _score(direction, total_s, terms, *, fallback=False, resource=None,
           rate_bps=None, visible_index=0):
    return ta.CandidateScore(
        direction=direction,
        resource=resource,
        total_s=(float("inf") if fallback else float(total_s)),
        terms=dict(terms),
        missing=(("missing_term",) if fallback else ()),
        fallback=fallback,
        tie_key=((visible_index, direction) if fallback
                 else (0, visible_index, direction)),
        predicted_bits=None,
        service_rate_bps=rate_bps,
    )


def _scored(scores, visible_order):
    """Assemble ScoredCandidates the way score_candidates does: fallbacks last,
    then total, then visible order, then direction."""
    index = {d: i for i, d in enumerate(visible_order)}
    ranking = tuple(s.direction for s in sorted(
        scores, key=lambda s: (s.fallback,
                               0.0 if s.fallback else s.total_s,
                               index[s.direction], s.direction)))
    return ta.ScoredCandidates(
        ranking=ranking, scores=tuple(scores),
        fallback_directions=tuple(d for d in ranking
                                  if any(s.direction == d and s.fallback
                                         for s in scores)),
        missing_directions=tuple(d for d in ranking
                                 if any(s.direction == d and s.missing
                                        for s in scores)))


def _known(direction, total_s, visible_index, **extra_terms):
    terms = {"tx_s": 0.01, "prop_s": 0.002, "remaining_prop_s": 0.002,
             "local_egress_wait_s": 0.0, "peer_process_s": 0.0,
             "resource_work_s": 0.0}
    terms.update(extra_terms)
    return _score(direction, total_s, terms,
                  visible_index=visible_index, rate_bps=10_000_000.0)


def test_geometry_excludes_every_queue_term():
    """Same geometry terms, wildly different queue terms: geometry cannot tell
    the two candidates apart, greedy follows the queue."""
    e = _known("E", 5.0, 0, resource_work_s=4.0, local_egress_wait_s=0.9)
    s = _known("S", 0.5, 1, resource_work_s=0.0, local_egress_wait_s=0.0)
    scored = _scored([e, s], ("E", "S"))
    assert scored.ranking == ("S", "E")          # the existing scorer's answer

    order, audit = sp.rank(scored, None, 10.0, sp.GEOMETRY,
                           visible_order=("E", "S"))
    geometry_j = {d: entry["j_used_s"]
                  for d, entry in audit["per_direction"].items()}
    assert geometry_j == {"E": pytest.approx(0.014), "S": pytest.approx(0.014)}
    # equal geometry cost -> the scorer's stable visible order decides
    assert order == ["E", "S"]
    for entry in audit["per_direction"].values():
        assert entry["resource_work_used_s"] == 0.0
        assert entry["q_used_bits"] is None

    greedy_order, _ = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY,
                              visible_order=("E", "S"))
    assert greedy_order == ["S", "E"]


def test_geometry_ignores_peer_process_and_local_wait_but_keeps_its_own_terms():
    base = _known("E", 0.02, 0)
    # a peer-process / egress-wait change must not move the geometry cost
    noisy = _known("E", 9.0, 0, peer_process_s=3.0, local_egress_wait_s=1.0)
    scored = _scored([base, noisy], ("E",))
    _order, audit = sp.rank(scored, None, 1.0, sp.GEOMETRY, visible_order=("E",))
    j = audit["per_direction"]["E"]["j_used_s"]
    assert j == pytest.approx(0.014)


def test_geometry_candidate_with_unknown_geometry_term_stays_fallback():
    known = _known("E", 0.02, 0)
    terms = {"tx_s": 0.01, "prop_s": 0.002}          # remaining_prop_s missing
    partial = _score("S", float("inf"), terms, fallback=True, visible_index=1)
    scored = _scored([known, partial], ("E", "S"))
    order, audit = sp.rank(scored, None, 1.0, sp.GEOMETRY,
                           visible_order=("E", "S"))
    assert order == ["E", "S"]
    assert audit["per_direction"]["S"]["j_used_s"] is None      # no invention
    assert audit["per_direction"]["S"]["fallback"] is True


# ==========================================================================
# 2. pure function: queue_greedy
# ==========================================================================
def test_queue_greedy_is_the_existing_ranking():
    e = _known("E", 5.0, 0, resource_work_s=4.0)
    s = _known("S", 0.5, 1)
    scored = _scored([e, s], ("E", "S"))
    order, audit = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY,
                           visible_order=("E", "S"))
    assert order == list(scored.ranking) == ["S", "E"]
    assert audit["per_direction"]["E"]["j_used_s"] == pytest.approx(5.0)
    assert audit["per_direction"]["S"]["j_used_s"] == pytest.approx(0.5)
    assert audit["selection"]["rule"] == "argmin"
    assert audit["selection"]["winner"] == "S"
    assert audit["hysteresis_eta_s"] is None and audit["ewma_alpha"] is None


def test_queue_greedy_returns_a_deliberately_unsorted_ranking_verbatim():
    """Greedy is DEFINED as the existing ranking, so it may not re-sort: a
    float tie the scorer already broke must survive."""
    e = _known("E", 1.0, 0)
    s = _known("S", 1.0, 1)
    unsorted = ta.ScoredCandidates(ranking=("E", "S"), scores=(s, e),
                                   fallback_directions=(),
                                   missing_directions=())
    order, _audit = sp.rank(unsorted, None, 10.0, sp.QUEUE_GREEDY,
                            visible_order=("E", "S"))
    assert order == ["E", "S"]


# ==========================================================================
# 3. pure function: hysteresis
# ==========================================================================
def test_hysteresis_keeps_the_committed_direction_inside_eta():
    # best S = 1.0; last E = 1.004 is 4 ms worse, inside eta = 5 ms
    e = _known("E", 1.004, 0)
    s = _known("S", 1.0, 1)
    scored = _scored([e, s], ("E", "S"))
    order, audit = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY_HYSTERESIS,
                           eta_s=0.005, last_direction="E",
                           visible_order=("E", "S"))
    assert order == ["E", "S"]
    assert audit["hysteresis_eta_s"] == 0.005
    assert audit["selection"] == {"winner": "E", "rule": "hysteresis_keep",
                                  "kept_direction": "E"}
    assert audit["per_direction"]["E"]["kept"] is True
    assert audit["per_direction"]["S"]["kept"] is False
    assert audit["j_last_s"] == pytest.approx(1.004)
    assert audit["j_best_s"] == pytest.approx(1.0)


def test_hysteresis_switches_when_the_margin_exceeds_eta():
    e = _known("E", 1.006, 0)     # 6 ms worse than S, outside eta = 5 ms
    s = _known("S", 1.0, 1)
    scored = _scored([e, s], ("E", "S"))
    order, audit = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY_HYSTERESIS,
                           eta_s=0.005, last_direction="E",
                           visible_order=("E", "S"))
    assert order == ["S", "E"]
    assert audit["selection"] == {"winner": "S", "rule": "argmin",
                                  "kept_direction": None}
    assert audit["per_direction"]["E"]["kept"] is False


def test_hysteresis_eta_zero_keeps_only_a_still_optimal_direction():
    optimal = _known("E", 1.0, 0)
    better = _known("S", 0.999, 1)
    scored = _scored([optimal, better], ("E", "S"))
    _keep_order, keep = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY_HYSTERESIS,
                                eta_s=0.0, last_direction="S",
                                visible_order=("E", "S"))
    assert keep["selection"]["rule"] == "hysteresis_keep"
    switch_order, switch = sp.rank(
        scored, None, 10.0, sp.QUEUE_GREEDY_HYSTERESIS, eta_s=0.0,
        last_direction="E", visible_order=("E", "S"))
    assert switch["selection"]["rule"] == "argmin"
    assert switch_order == ["S", "E"]


def test_hysteresis_without_memory_uses_the_best_direction():
    e = _known("E", 1.0, 0)
    s = _known("S", 1.0, 1)
    scored = _scored([e, s], ("E", "S"))
    order, audit = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY_HYSTERESIS,
                           eta_s=0.005, last_direction=None,
                           visible_order=("E", "S"))
    assert order == ["E", "S"]
    assert audit["last_direction"] is None
    assert audit["selection"]["rule"] == "argmin"
    assert audit["selection"]["winner"] == "E"


def test_hysteresis_memory_that_is_no_longer_a_candidate_is_dropped():
    e = _known("E", 1.0, 0)
    scored = _scored([e], ("E",))
    order, audit = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY_HYSTERESIS,
                           eta_s=0.005, last_direction="S",
                           visible_order=("E", "S"))
    assert order == ["E"]
    assert audit["selection"]["rule"] == "argmin"
    assert audit["per_direction"]["E"]["kept"] is False


def test_hysteresis_never_keeps_a_fallback_last_direction():
    known = _known("E", 1.0, 0)
    other = _known("S", 0.5, 1)
    fallback = _score("W", float("inf"), {}, fallback=True, visible_index=2)
    scored = _scored([known, other, fallback], ("E", "S", "W"))
    order, audit = sp.rank(scored, None, 10.0, sp.QUEUE_GREEDY_HYSTERESIS,
                           eta_s=0.005, last_direction="W",
                           visible_order=("E", "S", "W"))
    assert order == ["S", "E", "W"]      # greedy order: fallback last
    assert audit["selection"]["rule"] == "argmin"


# ==========================================================================
# 4. pure function: EWMA
# ==========================================================================
R_E = ta.ResourceKey(1, "S", ta.KIND_ISL, generation=0)


def test_ewma_single_arrived_sample_equals_greedy():
    terms = {"tx_s": 0.01, "prop_s": 0.002, "remaining_prop_s": 0.002,
             "resource_work_s": 500.0 / 10_000_000.0}
    e = _score("E", sum(terms.values()), terms, resource=R_E,
               rate_bps=10_000_000.0, visible_index=0)
    scored = _scored([e], ("E",))
    history = {R_E.as_tuple(): [_sample(R_E, 1.0, 1.05, 500.0)]}
    ewma_order, ewma_audit = sp.rank(
        scored, history, 2.0, sp.QUEUE_GREEDY_EWMA, alpha=0.25,
        visible_order=("E",))
    greedy_order, greedy_audit = sp.rank(
        scored, history, 2.0, sp.QUEUE_GREEDY, visible_order=("E",))
    assert ewma_order == greedy_order == ["E"]
    assert ewma_audit["per_direction"]["E"]["ewma_samples"] == 1
    assert ewma_audit["per_direction"]["E"]["q_used_bits"] == pytest.approx(500.0)
    assert ewma_audit["per_direction"]["E"]["j_used_s"] == pytest.approx(
        greedy_audit["per_direction"]["E"]["j_used_s"])


def test_ewma_alpha_one_is_the_instantaneous_last_sample():
    series = [_sample(R_E, 1.0, 1.05, 100.0),
              _sample(R_E, 2.0, 2.05, 900.0)]
    level, count = sp.ewma_queue_bits(series, 3.0, 1.0)
    assert (level, count) == (900.0, 2)


def test_ewma_alpha_to_zero_approaches_the_first_sample():
    series = [_sample(R_E, 1.0, 1.05, 0.0),
              _sample(R_E, 2.0, 2.05, 1000.0)]
    level_slow, _ = sp.ewma_queue_bits(series, 3.0, 0.1)
    level_half, _ = sp.ewma_queue_bits(series, 3.0, 0.5)
    level_fast, _ = sp.ewma_queue_bits(series, 3.0, 0.9)
    assert level_slow == pytest.approx(100.0)
    assert level_half == pytest.approx(500.0)
    assert level_fast == pytest.approx(900.0)
    assert 0.0 <= level_slow < level_half < level_fast <= 1000.0


def test_ewma_never_uses_an_unarrived_or_future_sample():
    series = [
        _sample(R_E, 1.0, 1.05, 100.0),        # arrived before the snapshot
        _sample(R_E, 2.0, 9.00, 500.0),        # measured before, arrives later
        _sample(R_E, 4.0, 4.10, 900.0),        # measured after the snapshot
    ]
    level, count = sp.ewma_queue_bits(series, 3.0, 0.25)
    assert (level, count) == (100.0, 1)
    # exact source-time duplicates keep the freshest arrival only
    duplicated = [
        _sample(R_E, 1.0, 1.05, 100.0),
        _sample(R_E, 1.0, 1.50, 700.0),
    ]
    level_dup, count_dup = sp.ewma_queue_bits(duplicated, 3.0, 0.25)
    assert (level_dup, count_dup) == (700.0, 1)


def test_ewma_replaces_the_instantaneous_queue_work():
    """J = total - resource_work_s + qbar/rate, with qbar from the arrived
    series: the stale predicted bits are what the policy replaces."""
    def candidate(direction, bits, index):
        terms = {"tx_s": 0.01, "prop_s": 0.002, "remaining_prop_s": 0.002,
                 "resource_work_s": bits / 10_000_000.0}
        return _score(direction, sum(terms.values()), terms,
                      resource=(R_E if direction == "E" else
                                ta.ResourceKey(2, "E", ta.KIND_ISL, 0)),
                      rate_bps=10_000_000.0, visible_index=index)

    e = candidate("E", 1_000.0, 0)
    s = candidate("S", 0.0, 1)
    scored = _scored([e, s], ("E", "S"))
    r_s = ta.ResourceKey(2, "E", ta.KIND_ISL, 0)
    history = {R_E.as_tuple(): [_sample(R_E, 1.0, 1.05, 0.0),
                                _sample(R_E, 2.0, 2.05, 0.0)],
               r_s.as_tuple(): [_sample(r_s, 1.0, 1.05, 3000.0)]}
    order, audit = sp.rank(scored, history, 3.0, sp.QUEUE_GREEDY_EWMA,
                           alpha=0.25, visible_order=("E", "S"))
    # E's stale 1000 bits become qbar=0; S's stale 0 bits become qbar=3000
    assert audit["per_direction"]["E"]["resource_work_used_s"] == 0.0
    assert audit["per_direction"]["S"]["q_used_bits"] == pytest.approx(3000.0)
    assert audit["per_direction"]["S"]["resource_work_used_s"] == pytest.approx(
        3000.0 / 10_000_000.0)
    assert order == ["E", "S"]
    assert audit["per_direction"]["E"]["j_used_s"] == pytest.approx(
        0.014)
    assert audit["per_direction"]["S"]["j_used_s"] == pytest.approx(
        0.014 + 3000.0 / 10_000_000.0)


def test_ewma_without_arrived_samples_produces_no_invented_j():
    r_s = ta.ResourceKey(2, "E", ta.KIND_ISL, 0)
    terms = {"tx_s": 0.01, "prop_s": 0.002, "remaining_prop_s": 0.002,
             "resource_work_s": 0.0}
    no_history = _score("E", sum(terms.values()), terms, resource=R_E,
                        rate_bps=10_000_000.0, visible_index=0)
    with_history = _score("S", sum(terms.values()), terms, resource=r_s,
                          rate_bps=10_000_000.0, visible_index=1)
    scored = _scored([no_history, with_history], ("E", "S"))
    history = {r_s.as_tuple(): [_sample(r_s, 1.0, 1.05, 0.0)]}
    order, audit = sp.rank(scored, history, 3.0, sp.QUEUE_GREEDY_EWMA,
                           alpha=0.25, visible_order=("E", "S"))
    assert audit["per_direction"]["E"]["ewma_samples"] == 0
    assert audit["per_direction"]["E"]["j_used_s"] is None
    assert audit["per_direction"]["S"]["ewma_samples"] == 1
    assert order == ["S", "E"]        # only a known J may lead


# ==========================================================================
# 5. pure function: audit contract, parameters
# ==========================================================================
_REQUIRED_PER_DIRECTION_KEYS = {"j_used_s", "resource_work_used_s",
                                "q_used_bits", "service_rate_bps",
                                "ewma_samples", "fallback", "kept"}
_REQUIRED_BLOCK_KEYS = {"policy", "hysteresis_eta_s", "ewma_alpha",
                        "last_direction", "per_direction", "selection",
                        "snapshot_at"}


@pytest.mark.parametrize("policy,kwargs", [
    (sp.LEGACY, {}),
    (sp.GEOMETRY, {}),
    (sp.QUEUE_GREEDY, {}),
    (sp.QUEUE_GREEDY_HYSTERESIS, {"eta_s": 0.005, "last_direction": "E"}),
    (sp.QUEUE_GREEDY_EWMA, {"alpha": 0.25}),
])
def test_audit_block_matches_the_fixed_contract(policy, kwargs):
    resource = ta.ResourceKey(1, "S", ta.KIND_ISL, 0)
    terms = {"tx_s": 0.01, "prop_s": 0.002, "remaining_prop_s": 0.002,
             "resource_work_s": 100.0 / 10_000_000.0}
    scored = _scored([
        _score("E", sum(terms.values()), terms, resource=resource,
               rate_bps=10_000_000.0, visible_index=0),
        _score("S", 0.5, {"tx_s": 0.5}, fallback=True, visible_index=1),
    ], ("E", "S"))
    history = {resource.as_tuple(): [_sample(resource, 1.0, 1.05, 100.0)]}
    order, audit = sp.rank(scored, history, 2.0, policy,
                           visible_order=("E", "S"), **kwargs)
    assert _REQUIRED_BLOCK_KEYS <= set(audit)
    assert audit["policy"] == policy
    assert audit["snapshot_at"] == 2.0
    assert set(audit["per_direction"]) == {"E", "S"}
    for direction, entry in audit["per_direction"].items():
        assert _REQUIRED_PER_DIRECTION_KEYS <= set(entry)
        if entry["resource_work_used_s"] is not None \
                and entry["q_used_bits"] is not None \
                and entry["service_rate_bps"] is not None:
            # the offline replay identity: work == bits / rate
            assert entry["resource_work_used_s"] == pytest.approx(
                entry["q_used_bits"] / entry["service_rate_bps"], rel=1e-12)
    assert audit["selection"]["rule"] in {"argmin", "hysteresis_keep"}
    assert audit["selection"]["winner"] == order[0]
    # parameters are recorded only where they are used
    assert audit["hysteresis_eta_s"] == (
        0.005 if policy == sp.QUEUE_GREEDY_HYSTERESIS else None)
    assert audit["ewma_alpha"] == (
        0.25 if policy == sp.QUEUE_GREEDY_EWMA else None)


def test_legacy_never_reranks():
    e = _known("E", 5.0, 0)
    s = _known("S", 0.5, 1)
    scored = _scored([e, s], ("E", "S"))
    order, audit = sp.rank(scored, None, 10.0, sp.LEGACY,
                           visible_order=("E", "S"))
    assert order == ["S", "E"] == list(scored.ranking)
    assert audit["applied_order"] == list(scored.ranking)


def test_unknown_policy_and_off_grid_parameters_fail_loud():
    e = _known("E", 1.0, 0)
    scored = _scored([e], ("E",))
    with pytest.raises(sp.StatePolicyError, match="state_policy must be"):
        sp.rank(scored, None, 1.0, "greedyish", visible_order=("E",))
    with pytest.raises(sp.StatePolicyError, match="hysteresis_eta_s"):
        sp.rank(scored, None, 1.0, sp.QUEUE_GREEDY_HYSTERESIS, eta_s=-0.1,
                visible_order=("E",))
    with pytest.raises(sp.StatePolicyError, match="ewma_alpha"):
        sp.rank(scored, None, 1.0, sp.QUEUE_GREEDY_EWMA, alpha=0.0,
                visible_order=("E",))
    # the RUN grid is enforced on the registered parameters
    with pytest.raises(sp.StatePolicyError, match="pre-registered grid"):
        sp.validate_eta_s(0.003)
    with pytest.raises(sp.StatePolicyError, match="pre-registered grid"):
        sp.validate_alpha(0.3)
    assert sp.validate_eta_s(0.005) == 0.005
    assert sp.validate_alpha(0.25) == 0.25


# ==========================================================================
# 6. config: defaults, fail-loud, identity
# ==========================================================================
def _resolve(**ta_overrides):
    ta_cfg = {"enabled": True}
    ta_cfg.update(ta_overrides)
    return config.resolve_config({"time_alignment": ta_cfg})


def test_defaults_keep_the_historical_routing_rule():
    resolved = config.resolve_config({})
    ta_cfg = resolved["config"]["time_alignment"]
    assert ta_cfg["state_policy"] == "legacy"
    assert ta_cfg["hysteresis_eta_s"] == 0.0
    assert ta_cfg["ewma_alpha"] == 0.25


def test_invalid_state_policy_fails_loud():
    with pytest.raises(config.ConfigError, match="state_policy"):
        _resolve(state_policy="greedyish")


def test_off_grid_parameters_fail_loud():
    with pytest.raises(config.ConfigError, match="pre-registered grid"):
        _resolve(state_policy="queue_greedy_hysteresis", hysteresis_eta_s=0.003)
    with pytest.raises(config.ConfigError, match="pre-registered grid"):
        _resolve(state_policy="queue_greedy_ewma", ewma_alpha=0.3)


def test_hysteresis_eta_outside_its_policy_fails_loud():
    with pytest.raises(config.ConfigError, match="only meaningful"):
        _resolve(state_policy="queue_greedy", hysteresis_eta_s=0.005)


def test_policy_requires_time_alignment_to_be_enabled():
    with pytest.raises(config.ConfigError, match="requires"):
        config.resolve_config({"time_alignment": {"enabled": False,
                                                  "state_policy": "geometry"}})


def test_policy_cannot_silently_miss_the_scoring_path():
    for mode in ("precomputed", "async_point", "async_window"):
        overrides = {"time_alignment": {"enabled": True,
                                        "state_policy": "geometry",
                                        "execution_mode": mode}}
        if mode != "precomputed":
            overrides["async_routing"] = {"enabled": True}
        with pytest.raises(config.ConfigError, match="state_policy"):
            config.resolve_config(overrides)


def test_state_policy_enters_the_config_identity():
    legacy = config.resolve_config({})
    geometry = config.resolve_config(
        {"time_alignment": {"enabled": True, "state_policy": "geometry"}})
    assert "state_policy" in legacy["config"]["time_alignment"]
    assert legacy["sha256"] != geometry["sha256"]
    # the parameters are identity-bearing too, not free-floating knobs
    slow = _resolve(state_policy="queue_greedy_hysteresis",
                    hysteresis_eta_s=0.002)["sha256"]
    fast = _resolve(state_policy="queue_greedy_hysteresis",
                    hysteresis_eta_s=0.010)["sha256"]
    assert slow != fast
    a01 = _resolve(state_policy="queue_greedy_ewma", ewma_alpha=0.1)["sha256"]
    a05 = _resolve(state_policy="queue_greedy_ewma", ewma_alpha=0.5)["sha256"]
    assert a01 != a05


# ==========================================================================
# 7. integration: legacy regression against the PRE-POLICY baseline
# ==========================================================================
def test_legacy_reproduces_the_pre_policy_baseline_exactly():
    """Same tiny fixture + seed as the pre-change capture: the complete
    outcome/service stream must be byte-identical, not merely plausible."""
    res, sink = _run(_cfg(state_policy="legacy"), SMALL_ROWS)
    assert len(res["packet_events"]) == 381
    assert len(res["link_service_windows"]) == 80
    assert set(res["fates"].values()) == {"DELIVERED"}
    assert len(res["fates"]) == len(SMALL_ROWS)
    assert _result_signature(res) == _LEGACY_GOLDEN_SHA256
    # the audit block exists but never changes the legacy order
    forwards = _forwards(sink)
    assert forwards
    for decision in forwards:
        audit = _policy_audit(decision)
        assert audit["policy"] == "legacy"
        assert audit["applied_order"] == audit["scorer_ranking"]
        assert decision["chosen"] == audit["applied_order"][0]


def test_legacy_and_queue_greedy_make_the_same_decisions():
    legacy_res, legacy_sink = _run(_cfg(state_policy="legacy"), SMALL_ROWS)
    greedy_res, greedy_sink = _run(_cfg(state_policy="queue_greedy"),
                                   SMALL_ROWS)
    assert _result_signature(legacy_res) == _result_signature(greedy_res)
    for legacy_row, greedy_row in zip(_forwards(legacy_sink),
                                      _forwards(greedy_sink)):
        legacy_audit = _policy_audit(legacy_row)
        greedy_audit = _policy_audit(greedy_row)
        assert legacy_audit["policy"] == "legacy"
        assert greedy_audit["policy"] == "queue_greedy"
        assert greedy_audit["applied_order"] == list(
            greedy_audit["scorer_ranking"])
        assert greedy_row["chosen"] == greedy_audit["scorer_ranking"][0]


# ==========================================================================
# 8. integration: geometry is blind to the queue drift
# ==========================================================================
def test_geometry_ignores_queue_drift_that_moves_the_greedy_actions():
    geometry_res, geometry_sink = _run(_cfg(state_policy="geometry"),
                                       JITTER_ROWS)
    geometry_forwards = _forwards(geometry_sink)
    assert len(geometry_forwards) == len(JITTER_ROWS)
    assert set(geometry_res["fates"].values()) == {"DELIVERED"}
    # the policy never flips: every decision is explained by geometry alone
    assert _switches([d["chosen"] for d in geometry_forwards]) == 0
    for decision in geometry_forwards:
        audit = _policy_audit(decision)
        assert audit["policy"] == "geometry"
        assert audit["selection"]["rule"] == "argmin"
        assert decision["chosen"] == audit["applied_order"][0]
        known = [audit["per_direction"][direction]["j_used_s"]
                 for direction in audit["applied_order"]
                 if audit["per_direction"][direction]["j_used_s"] is not None]
        assert known == sorted(known)          # ordered by the geometry J
        for entry in audit["per_direction"].values():
            assert entry["resource_work_used_s"] == 0.0
            assert entry["q_used_bits"] is None
            assert entry["ewma_samples"] == 0
    # control: on the SAME fixture the queue state really does move the
    # greedy (stale-state sensitive) baseline
    greedy_res, greedy_sink = _run(_cfg(state_policy="queue_greedy"),
                                   JITTER_ROWS)
    assert set(greedy_res["fates"].values()) == {"DELIVERED"}
    assert _switches([d["chosen"] for d in _forwards(greedy_sink)]) > 0


# ==========================================================================
# 9. integration: hysteresis reduces action churn
# ==========================================================================
def test_hysteresis_reduces_jitter_switches():
    greedy_res, greedy_sink = _run(_cfg(state_policy="queue_greedy"),
                                   JITTER_ROWS)
    hyst_res, hyst_sink = _run(
        _cfg(state_policy="queue_greedy_hysteresis", hysteresis_eta_s=0.005),
        JITTER_ROWS)
    greedy_switches = _switches([d["chosen"] for d in _forwards(greedy_sink)])
    hyst_switches = _switches([d["chosen"] for d in _forwards(hyst_sink)])
    assert set(greedy_res["fates"].values()) == {"DELIVERED"}
    assert set(hyst_res["fates"].values()) == {"DELIVERED"}
    assert greedy_switches > 0
    assert hyst_switches < greedy_switches
    keeps = [d for d in _forwards(hyst_sink)
             if _policy_audit(d)["selection"]["rule"] == "hysteresis_keep"]
    assert keeps, "eta must actually hold a direction on this fixture"
    for decision in keeps:
        audit = _policy_audit(decision)
        kept = audit["selection"]["kept_direction"]
        assert kept == audit["last_direction"]
        assert audit["selection"]["winner"] == kept
        assert audit["per_direction"][kept]["kept"] is True
        assert sum(1 for entry in audit["per_direction"].values()
                   if entry["kept"]) == 1
        assert audit["j_last_s"] <= audit["j_best_s"] + audit["hysteresis_eta_s"]


def test_hysteresis_memory_is_written_only_by_committed_forwards():
    """A frozen observation, a hold, a deliver or a fail must never teach the
    policy a direction: the audited ``last_direction`` is exactly the last
    FORWARD commit that happened before the observation instant."""
    _res, sink = _run(_cfg(state_policy="queue_greedy_hysteresis",
                           hysteresis_eta_s=0.005), JITTER_ROWS)
    commits = [(decision["t"], (decision["sat"], decision["dst"]),
                decision["chosen"])
               for decision in sink if decision.get("kind") == "forward"]
    assert commits
    commit_times = [t for t, _key, _chosen in commits]
    assert commit_times == sorted(commit_times)

    def committed_before(key, instant):
        value = None
        for committed_at, committed_key, chosen in commits:
            if committed_at > instant:
                break
            if committed_key == key:
                value = chosen
        return value

    forward_seen = 0
    for decision in sink:
        block = ((decision.get("observation_at_start") or {})
                 .get("time_alignment") or {}).get("state_policy")
        if block is None:
            continue
        assert block["policy"] == "queue_greedy_hysteresis"
        key = (decision["sat"], decision["dst"])
        assert block["last_direction"] == committed_before(
            key, decision["t_decision_start"])
        if decision["kind"] == "forward":
            forward_seen += 1
    assert forward_seen == len(commits)
    # the fixture really does contain non-forward decisions, so the test is
    # not vacuous: they simply never write the memory
    kinds = {decision["kind"] for decision in sink}
    assert "deliver" in kinds


# ==========================================================================
# 10. integration: EWMA audit is replayable
# ==========================================================================
def test_ewma_audit_is_replayable_against_the_offline_identity():
    res, sink = _run(_cfg(state_policy="queue_greedy_ewma", ewma_alpha=0.25),
                     JITTER_ROWS)
    assert set(res["fates"].values()) == {"DELIVERED"}
    forwards = _forwards(sink)
    assert forwards
    for decision in forwards:
        time_alignment = decision["observation_at_start"]["time_alignment"]
        audit = time_alignment["state_policy"]
        assert audit["policy"] == "queue_greedy_ewma"
        assert audit["ewma_alpha"] == 0.25
        assert audit["selection"]["rule"] == "argmin"
        for direction, entry in audit["per_direction"].items():
            if entry["j_used_s"] is None:
                continue
            score = time_alignment["scores"][direction]
            assert entry["ewma_samples"] >= 1
            # J = total - resource_work_s + qbar/rate, exactly as defined
            expected = (score["total_s"]
                        - score["terms"]["resource_work_s"]
                        + entry["resource_work_used_s"])
            assert entry["j_used_s"] == pytest.approx(expected, rel=1e-12)
            assert entry["resource_work_used_s"] == pytest.approx(
                entry["q_used_bits"] / entry["service_rate_bps"], rel=1e-12)


# ==========================================================================
# 11. integration: the policy layer never touches the physics
# ==========================================================================
@pytest.mark.parametrize("policy,extra", [
    ("legacy", {}),
    ("geometry", {}),
    ("queue_greedy", {}),
    ("queue_greedy_hysteresis", {"hysteresis_eta_s": 0.005}),
    ("queue_greedy_ewma", {"ewma_alpha": 0.25}),
])
def test_every_policy_keeps_the_same_loop_free_physics(policy, extra):
    res, sink = _run(_cfg(state_policy=policy, **extra), SMALL_ROWS)
    assert set(res["fates"].values()) == {"DELIVERED"}
    for decision in _forwards(sink):
        # every committed forward direction is legal, path-free and scored
        assert decision["chosen"] in decision["candidates"]
        assert decision["chosen"] in (
            decision["observation_at_start"]["time_alignment"]["applied_order"])
        # the path never repeats a satellite (the fixture has a 3-node diamond)
        for pid_delivery in res["deliveries"].values():
            assert len(pid_delivery["path"]) == len(set(pid_delivery["path"]))
