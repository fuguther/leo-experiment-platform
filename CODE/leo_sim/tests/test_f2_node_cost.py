"""F2 (node processing / scheduling cost): an independently attributable stage.

Design contract exercised here
------------------------------
F2 is the time a satellite node spends receiving, processing and scheduling one
arriving data packet before that packet becomes available to the forwarding
function.  It is deliberately NOT a transmit-time term: it never multiplies or
divides pkt.bits / rate, so it cannot degenerate into the same variable as the
PHY bandwidth (F3, links.isl_rate_mbps).

Placement on the timeline (one occupancy per satellite visit):

    propagation_arrival -> [ node_process_start .. node_process_end ] -> decision

so the stage ends exactly where the decision stage starts
(t_decision_start == node_process_end) and never overlaps
execution.compute_delay_s.

Recording boundary (why this is a timeline-sink-only feature)
-------------------------------------------------------------
queue_wait / holding_wait / tx / propagation are the only stages the immutable
event contract can name.  Reusing any of them would make the node cost
indistinguishable from an existing mechanism, and reusing service windows would
put it into tx_s -- i.e. into F3's own variable.  A new packet_events kind would
extend the closed whitelist in metrics.py:90-218, and a new counter would extend
receipt.MECHANISM_COUNTER_KEYS; both are frozen contracts owned by other tasks.
F2 therefore follows the frozen-mode precedent
(ANALYSIS/T1-MEASUREMENT-PROTOCOL.md 4.4) and records on the timeline sink only.

What is proven below is therefore the strongest statement the frozen contract
allows: the uncovered set of the packet timeline is EXACTLY the F2 stage -- no
more and no less -- and the decomposition closes to machine precision once the
F2 stage is supplied.  Promoting F2 to a first-class named term of the receipt
requires the contract change described in the PR body, not a silent extension
here.
"""
from __future__ import annotations

import hashlib
import json
import math

import pytest

from CODE.leo_sim import config, decision_ledger, kernel, metrics
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

try:  # second implementation (PR #214); absent on main until that lands
    from CODE.leo_sim import metrics_independent as _indep
except ImportError:  # pragma: no cover - exercised on main today
    _indep = None

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)
NB = {0: {"E": 1}, 1: {"W": 0}}
VIS = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
                             (s == 1 and (lat, lon) == BC)

F2 = "node_process_delay_s"
CLOSURE_TOL = 1e-9
DUR_TOL = 1e-12  # durations are re-derived in floating point; a picosecond is noise


def _geo():
    return StaticGeometry(2, neighbors_map=NB, visible=VIS)


def _cfg(overrides=None):
    return make_cfg(overrides)


def _run(f2=None, rows=None, n_packets=1, overrides=None, decisions=False):
    """Run the 2-satellite A->B fixture and return (result, timeline[, decisions])."""
    over = dict(overrides or {})
    if f2 is not None:
        over.setdefault("execution", {})[F2] = f2
    rows = rows if rows is not None else [row(i, 0.5 * i, A, B)
                                          for i in range(1, n_packets + 1)]
    timeline = []
    sink = [] if decisions else None
    res = kernel.run_simulation(_cfg(over), rows, geometry=_geo(),
                                timeline_sink=timeline, decision_sink=sink)
    return (res, timeline, sink) if decisions else (res, timeline)


# --------------------------------------------------------------- small algebra

def _digest(obj):
    blob = json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _digest_payload(res):
    return {key: res[key] for key in (
        "fates", "fate_counts", "totals", "deliveries", "occupied",
        "queue_area_bits_s", "access", "service_log", "handover",
        "events_processed", "packet_events", "link_service_windows",
        "mechanism_counters")}


def _node_occupancies(timeline):
    """Fold node_process_start/node_process_end into one record per occupancy."""
    rows = []
    pending = {}
    for m in timeline:
        if m["milestone"] == "node_process_start":
            pending[(m["pid"], m["sat"], m["via"], m["at"])] = m
        elif m["milestone"] == "node_process_end":
            key = (m["pid"], m["sat"], m["via"], m["started_at"])
            start = pending.pop(key)
            rows.append({"pid": m["pid"], "sat": m["sat"], "via": m["via"],
                         "start": float(start["at"]), "end": float(m["at"]),
                         "seconds": float(m["at"]) - float(start["at"])})
    assert not pending, "every node_process_start must be closed by its end"
    return sorted(rows, key=lambda r: (r["pid"], r["start"]))


def _node_spans(occupancies, pid):
    return [(r["start"], r["end"], "node_cost", (r["sat"], r["via"]))
            for r in occupancies if r["pid"] == pid]


def _tx_windows(res, pid):
    return sorted((w for w in res["link_service_windows"] if w["pid"] == pid),
                  key=lambda w: (w["start"], w["stage"]))


def _prop_hops(res, pid):
    starts, hops = {}, []
    for event in res["packet_events"]:
        if event["pid"] != pid:
            continue
        if event["kind"] == "propagation_start":
            starts[event["prop_id"]] = event
        elif event["kind"] == "propagation_arrival":
            start = starts[event["prop_id"]]
            hops.append({"stage": start["stage"], "link_id": start["link_id"],
                         "start": start["at"], "end": event["at"],
                         "seconds": event["at"] - start["at"]})
    return sorted(hops, key=lambda h: h["start"])


# The span algebra below mirrors metrics_independent._spans / _uncovered.  The
# mirror exists because that module does not know the F2 stage yet; whenever it
# is importable the same test asserts that both implementations agree
# term-by-term, so the mirror can never silently drift from it.

def _spans(res, pid, node_spans=()):
    events = [(index, e) for index, e in enumerate(res["packet_events"])
              if e["pid"] == pid]
    queue_entries = {e["queue_id"]: e for _i, e in events
                     if e["kind"] == "queue_enter"}
    spans = []
    for _i, e in events:
        if e["kind"] == "service_start" and e.get("queue_id") in queue_entries:
            entry = queue_entries[e["queue_id"]]
            spans.append((entry["at"], e["at"], "queue_wait", e["queue_id"]))
    for w in _tx_windows(res, pid):
        spans.append((w["start"], w["end"], "tx", w["link_id"]))
    for hop in _prop_hops(res, pid):
        spans.append((hop["start"], hop["end"], "propagation", hop["link_id"]))
    holds = sorted(((index, e) for index, e in events
                    if e["kind"] == "queue_enter"),
                   key=lambda pair: (pair[1]["at"], pair[0]))
    for (first, (_, second)) in zip((e for _i, e in holds), holds[1:]):
        if first["queue"] == "holding":
            spans.append((first["at"], second["at"], "holding_wait",
                          first["queue_id"]))
    spans.extend(node_spans)
    return sorted(spans, key=lambda span: (span[0], span[1]))


def _uncovered(emitted_at, delivered_at, spans, eps=CLOSURE_TOL):
    gaps, cursor = [], emitted_at
    for start, end, _label, _key in spans:
        if start > cursor + eps:
            gaps.append((cursor, start, start - cursor))
        cursor = max(cursor, end)
    if delivered_at > cursor + eps:
        gaps.append((cursor, delivered_at, delivered_at - cursor))
    return gaps


def _overlaps(spans):
    found = []
    for previous, following in zip(spans, spans[1:]):
        if following[0] < previous[1] - CLOSURE_TOL:
            found.append((previous[2], following[2],
                          min(previous[1], following[1]) - following[0]))
    return found


def _closure(res, pid, node_spans=()):
    emitted_at = next(e["at"] for e in res["packet_events"]
                      if e["pid"] == pid and e["kind"] == "packet_emitted")
    delivered_at = res["deliveries"][pid]["delivered_at"]
    spans = _spans(res, pid, node_spans)
    terms = {}
    for start, end, label, _key in spans:
        terms[label] = terms.get(label, 0.0) + (end - start)
    for label in ("queue_wait", "holding_wait", "tx", "propagation",
                  "node_cost"):
        terms.setdefault(label, 0.0)
    e2e = delivered_at - emitted_at
    residual = e2e - math.fsum(terms[l] for l in ("queue_wait",
                                                  "holding_wait", "tx",
                                                  "propagation", "node_cost"))
    return {
        "pid": pid, "emitted_at": emitted_at, "delivered_at": delivered_at,
        "e2e_s": e2e, "terms": terms, "spans": spans,
        "gaps": _uncovered(emitted_at, delivered_at, spans),
        "overlaps": _overlaps(spans), "residual_s": residual,
    }


def _indep_terms(res, pid):
    """Term-by-term view from the second implementation, or the mirror."""
    if _indep is None:
        closure = _closure(res, pid)
        return {"queue_wait_s": closure["terms"]["queue_wait"],
                "holding_wait_s": closure["terms"]["holding_wait"],
                "tx_s": closure["terms"]["tx"],
                "prop_s": closure["terms"]["propagation"],
                "gaps": closure["gaps"]}
    base = _indep.decompose_packet_delay(res["packet_events"],
                                         res["link_service_windows"], pid)
    return {"queue_wait_s": base["queue_wait_s"],
            "holding_wait_s": base["holding_wait_s"],
            "tx_s": base["tx_s"], "prop_s": base["prop_s"],
            "gaps": [tuple(gap) for gap in base["gaps"]]}


def test_the_mirror_agrees_with_the_second_implementation():
    """Guard the mirror: if metrics_independent is present, it is authoritative.

    On main this module does not exist yet (it lands with PR #214), so the test
    reports the mirror-only path instead of silently skipping the guard.
    """
    if _indep is None:
        pytest.skip("metrics_independent not on this base (PR #214 pending)")
    for f2 in (0.0, 0.05):
        res, _timeline = _run(f2)
        mine = _closure(res, 1)
        theirs = _indep_terms(res, 1)
        assert mine["terms"]["queue_wait"] == pytest.approx(
            theirs["queue_wait_s"], abs=DUR_TOL)
        assert mine["terms"]["holding_wait"] == pytest.approx(
            theirs["holding_wait_s"], abs=DUR_TOL)
        assert mine["terms"]["tx"] == pytest.approx(theirs["tx_s"], abs=DUR_TOL)
        assert mine["terms"]["propagation"] == pytest.approx(
            theirs["prop_s"], abs=DUR_TOL)
        report = _indep.verify_delay_decomposition(
            res["packet_events"], res["link_service_windows"], res)
        assert report["ok"], report["errors"]
        assert report["max_abs_residual_s"] <= CLOSURE_TOL


# --------------------------------------------------------------------- config

def test_the_default_node_cost_is_zero():
    resolved = config.resolve_config({})
    assert resolved["config"]["execution"][F2] == 0.0


def test_a_negative_or_non_finite_node_cost_is_rejected():
    for bad in (-1.0, -0.001, float("inf"), float("nan"), True):
        with pytest.raises(config.ConfigError):
            config.resolve_config({"execution": {F2: bad}})


def test_a_positive_node_cost_is_accepted_and_changes_the_config_identity():
    """Adding this key changes config_sha256 for every configuration, so
    previously compiled planned-run rows and authorizations cannot be reused
    against the new code.  That consequence is deliberate and documented."""
    base = config.resolve_config({})
    costed = config.resolve_config({"execution": {F2: 0.05}})
    assert costed["config"]["execution"][F2] == 0.05
    assert base["sha256"] != costed["sha256"]


def test_enabling_the_node_cost_without_a_timeline_sink_fails_loud():
    """Unattributable node time is refused instead of silently added."""
    with pytest.raises(kernel.KernelError):
        kernel.Kernel(_cfg({"execution": {F2: 0.05}}), [row(1, 0.5, A, B)],
                      geometry=_geo())


# ------------------------------------------------ default path: bit-identical

def test_a_zero_cost_matches_the_unset_default_bit_for_bit():
    for n in (1, 3):
        unset, tl_unset = _run(None, n_packets=n)
        zero, tl_zero = _run(0.0, n_packets=n)
        assert _digest(_digest_payload(unset)) == _digest(_digest_payload(zero))
        assert _digest_payload(unset) == _digest_payload(zero)
        assert tl_unset == tl_zero
        assert not [m for m in tl_zero
                    if m["milestone"].startswith("node_process")]


def test_a_zero_cost_records_nothing_and_leaves_no_uncovered_interval():
    res, timeline = _run(0.0, n_packets=3)
    assert _node_occupancies(timeline) == []
    for pid in res["deliveries"]:
        closure = _closure(res, pid)
        assert closure["gaps"] == [], "baseline must have no uncovered interval"
        assert abs(closure["residual_s"]) <= CLOSURE_TOL
        assert closure["terms"]["node_cost"] == 0.0


# ------------------------------------------------------- the recorded stage

def test_the_node_stage_records_one_occupancy_per_satellite_visit():
    res, timeline = _run(0.05, n_packets=3)
    rows = _node_occupancies(timeline)
    assert [(r["pid"], r["sat"], r["via"]) for r in rows] == [
        (1, 0, "uplink"), (1, 1, "isl"),
        (2, 0, "uplink"), (2, 1, "isl"),
        (3, 0, "uplink"), (3, 1, "isl")]
    for r in rows:
        assert r["seconds"] == pytest.approx(0.05, abs=DUR_TOL)
        assert r["end"] > r["start"]
    assert [res["deliveries"][pid]["path"] for pid in sorted(res["deliveries"])]         == [[0, 1]] * 3


def test_the_node_stage_is_disjoint_from_every_frozen_stage():
    res, timeline = _run(0.05, n_packets=3)
    rows = _node_occupancies(timeline)
    for pid in sorted(res["deliveries"]):
        node = _node_spans(rows, pid)
        frozen = _spans(res, pid)  # spans without the node stage
        assert not _overlaps(sorted(frozen + node,
                                    key=lambda s: (s[0], s[1]))), \
            "the node stage must not charge time already charged by a frozen stage"
        for start, end, _label, _key in frozen:
            for n_start, n_end, _nl, _nk in node:
                assert n_end <= start + CLOSURE_TOL or n_start >= end - CLOSURE_TOL


# ------------------------------------------- attributable, not "uncovered time"

def test_the_uncovered_set_is_exactly_the_node_stage():
    """The strongest statement the frozen contract allows: nothing else hides
    in the uncovered set, and none of F2 hides anywhere else."""
    res, timeline = _run(0.05, n_packets=1)
    rows = _node_occupancies(timeline)
    node = _node_spans(rows, 1)
    assert len(node) == 2
    closure = _closure(res, 1)  # spans WITHOUT the node stage
    gaps = [(round(g[0], 9), round(g[1], 9)) for g in closure["gaps"]]
    assert gaps == [(round(s, 9), round(e, 9)) for s, e, _l, _k in node]
    assert math.fsum(g[2] for g in closure["gaps"]) == pytest.approx(
        math.fsum(r["seconds"] for r in rows), abs=CLOSURE_TOL)
    # the second implementation sees the identical uncovered set
    theirs = _indep_terms(res, 1)
    assert math.fsum(g[2] for g in theirs["gaps"]) == pytest.approx(
        math.fsum(r["seconds"] for r in rows), abs=CLOSURE_TOL)


def test_naming_the_node_stage_closes_the_decomposition_to_machine_precision():
    res, timeline = _run(0.05, n_packets=3)
    rows = _node_occupancies(timeline)
    for pid in sorted(res["deliveries"]):
        closure = _closure(res, pid, _node_spans(rows, pid))
        assert closure["gaps"] == [], "no uncovered interval may survive"
        assert abs(closure["residual_s"]) <= CLOSURE_TOL
        assert closure["terms"]["node_cost"] == pytest.approx(0.1, abs=DUR_TOL)
        assert closure["overlaps"] == []


def _next_milestone_after(timeline, row):
    later = [m["at"] for m in timeline
             if m["pid"] == row["pid"] and m["at"] >= row["end"] - DUR_TOL
             and m["milestone"] != "node_process_end"]
    assert later, "an occupancy must be followed by the decision stage"
    return min(later)


def test_the_node_stage_ends_exactly_where_the_decision_stage_starts():
    """F2 must not be a hidden part of the decision computation: with a zero
    compute delay the next stage of the packet starts at the very instant the
    node stage ends, and with a compute delay it starts exactly one whole
    computation interval after that boundary -- never inside it."""
    _res, timeline = _run(0.05)
    rows = _node_occupancies(timeline)
    assert len(rows) == 2
    for row in rows:
        assert _next_milestone_after(timeline, row) == pytest.approx(
            row["end"], abs=DUR_TOL)

    res, timeline, sink = _run(0.05, overrides={"execution":
                                                {"compute_delay_s": 0.02}},
                               decisions=True)
    rows = _node_occupancies(timeline)
    assert len(rows) == 2
    for row in rows:
        assert _next_milestone_after(timeline, row) - row["end"] == \
            pytest.approx(0.02, abs=DUR_TOL)
    deliver = [d for d in sink if d["kind"] == "deliver"]
    assert deliver, "the fixture must deliver"
    assert deliver[0]["t_decision_start"] == pytest.approx(rows[-1]["end"],
                                                           abs=DUR_TOL)
    assert deliver[0]["t"] - deliver[0]["t_decision_start"] == pytest.approx(
        0.02, abs=DUR_TOL)
    by_decision, _diag = decision_ledger.build_ledger(sink, timeline)
    for entry in by_decision.values():
        assert entry["t_decision_start"] <= entry["t_measure"]


# ------------------------------------------------------ two-factor separation

@pytest.mark.parametrize("isl_rate_mbps", [200.0, 400.0, 1000.0])
def test_fixed_node_cost_sweeping_the_phy_rate_keeps_the_node_reading(isl_rate_mbps):
    """F3 = links.isl_rate_mbps.  Sweeping the PHY bandwidth moves tx and
    leaves the F2 reading untouched: they are not the same variable."""
    res, timeline = _run(0.05, overrides={"links": {"isl_rate_mbps":
                                                    isl_rate_mbps}})
    rows = [(r["sat"], r["via"], r["seconds"]) for r in _node_occupancies(timeline)]
    assert rows == [(0, "uplink", pytest.approx(0.05, abs=DUR_TOL)),
                    (1, "isl", pytest.approx(0.05, abs=DUR_TOL))]
    isl = [w for w in _tx_windows(res, 1) if w["stage"] == "isl"]
    assert len(isl) == 1
    assert isl[0]["end"] - isl[0]["start"] == pytest.approx(
        8_000_000 / (isl_rate_mbps * 1e6), rel=1e-9)


def test_sweeping_the_phy_rate_changes_tx_and_never_the_node_reading():
    readings, tx = {}, {}
    for rate in (200.0, 400.0, 1000.0):
        res, timeline = _run(0.05, overrides={"links": {"isl_rate_mbps": rate}})
        readings[rate] = [(r["sat"], r["via"]) for r in _node_occupancies(timeline)]
        tx[rate] = [round(w["end"] - w["start"], 9) for w in _tx_windows(res, 1)]
    assert readings[200.0] == readings[400.0] == readings[1000.0]
    assert tx[200.0][1] > tx[400.0][1] > tx[1000.0][1], "F3 must move tx"
    assert tx[200.0][0] == tx[400.0][0] == tx[1000.0][0], "uplink is not F3"
    assert tx[200.0][2] == tx[400.0][2] == tx[1000.0][2], "downlink is not F3"


def test_fixed_phy_rate_sweeping_the_node_cost_leaves_tx_and_prop_unchanged():
    """F2 moves and tx/prop/queue stay put: the node cost is not a transmit
    service time, so F2 and F3 cannot be the same experiment."""
    base_tx = base_prop = None
    node_totals = []
    for f2 in (0.0, 0.05, 0.1):
        res, timeline = _run(f2, n_packets=3)
        rows = _node_occupancies(timeline)
        node_totals.append(math.fsum(r["seconds"] for r in rows))
        tx = [(w["stage"], w["link_id"]) for w in _tx_windows(res, 1)]
        durations = [w["end"] - w["start"] for w in _tx_windows(res, 1)]
        props = [h["seconds"] for h in _prop_hops(res, 1)]
        served = [(w["served_bits"], w["bits"], w["rate_bps"])
                  for w in _tx_windows(res, 1)]
        assert [res["deliveries"][p]["path"] for p in sorted(res["deliveries"])] \
            == [[0, 1]] * 3
        if base_tx is None:
            base_tx, base_prop = (tx, durations, served), props
        else:
            assert (tx, [round(d, 9) for d in durations], served) == \
                (base_tx[0], [round(d, 9) for d in base_tx[1]], base_tx[2])
            assert [round(p, 9) for p in props] == \
                [round(p, 9) for p in base_prop]
    assert node_totals[0] == 0.0
    assert node_totals == sorted(node_totals)
    assert node_totals[1] == pytest.approx(0.3, abs=1e-9)   # 3 packets x 2 visits x 0.05
    assert node_totals[2] == pytest.approx(0.6, abs=1e-9)


def test_end_to_end_delay_grows_by_exactly_the_recorded_node_cost():
    """Negative control (0 -> 0) plus monotonicity plus an exact identity: in
    this contention-free fixture the whole e2e increase IS the recorded stage."""
    e2e = {}
    node = {}
    for f2 in (0.0, 0.05, 0.1):
        res, timeline = _run(f2, n_packets=3)
        e2e[f2] = {pid: d["delivered_at"] - 0.5 * pid
                   for pid, d in res["deliveries"].items()}
        node[f2] = {pid: math.fsum(r["seconds"] for r in _node_occupancies(timeline)
                                   if r["pid"] == pid)
                    for pid in res["deliveries"]}
    for pid in e2e[0.0]:
        assert node[0.0][pid] == 0.0
        assert e2e[0.0][pid] < e2e[0.05][pid] < e2e[0.1][pid]
        for f2 in (0.05, 0.1):
            assert e2e[f2][pid] - e2e[0.0][pid] == pytest.approx(node[f2][pid],
                                                                 abs=CLOSURE_TOL)


def test_the_node_cost_adds_no_mechanism_counter_and_no_new_event_kind():
    """Frozen key sets stay closed: F2 must not extend MECHANISM_COUNTER_KEYS
    or the packet_events kind whitelist."""
    base, _ = _run(0.0)
    costed, _ = _run(0.05)
    assert set(costed["mechanism_counters"]) == set(base["mechanism_counters"])
    assert all(costed["mechanism_counters"][k] == base["mechanism_counters"][k]
               for k in base["mechanism_counters"])
    kinds = {e["kind"] for e in costed["packet_events"]}
    assert kinds == {e["kind"] for e in base["packet_events"]}
    # summarize is the closed-whitelist enforcer; it must accept the streams
    metrics.summarize(costed["packet_events"], costed["link_service_windows"])
