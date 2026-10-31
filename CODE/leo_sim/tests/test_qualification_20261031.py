"""A-F simulator qualification exam (SIM-QUAL-20261031), event-level.

Every test in this module drives the REAL discrete-event kernel through
``kernel.run_simulation`` on a tiny deterministic StaticGeometry fixture and
reads only the immutable run artifacts (``packet_events``,
``link_service_windows``, ``service_log``, ``deliveries``, ``caches``,
``control``, ``queue_area_bits_s`` and the optional ``decision_sink`` /
``timeline_sink`` streams).  Assertions are on individual event timestamps and
their ORDER -- never on an average latency, which could hide a wrong
serialization or a missing propagation delay.

The exam has six groups; each test writes its raw event timeline plus every
assertion (expected/actual/pass) to
``ANALYSIS/SIM-QUAL-20261031/qualification-evidence/<case>.json``:

A  link serialization     A1 two packets on one ISL egress occupy disjoint
                          windows and the queue rebuilds from raw events;
                          A2 T_tx = S/R at several (S, R) points.
B  tx / propagation split B1 arrival = service_start + T_tx + T_prop and the
                          link is free during propagation; B2 T_prop follows
                          the real ISL distance.
C  control shares service  C1 control waits for the in-service data packet,
                          is served with a real transmission time and is not
                          overtaken by a waiting data packet; C2 a higher
                          broadcast rate adds real control service work.
D  queueing theory        D1 lambda < mu and lambda ~ mu keep the queue
                          bounded; D2 lambda > mu grows at ~ lambda - mu.
E  state-message chain    E1 generated -> queue -> tx -> prop -> cache, AoI,
                          no future state; E2 TTL expiry; E3 congestion
                          inflates received_at.
F  compute time           F1 network keeps running during a non-zero decision
                          computation; F2 frozen observes at compute start vs
                          refresh at commit; F3 logout-and-reject contract;
                          F4 frozen hold inferred at t0 while the info appears
                          during compute (refresh would forward).

Fixture reuse: the scripted 2-satellite line and ``make_cfg``/``row`` come from
``CODE.leo_sim.tests.helpers``; frozen/refresh fixtures follow the semantics
already pinned by ``test_frozen_observation.py`` (same FLIP-in-interval
geometry, same commit_rejected / frozen_inferred_hold milestones).
"""
from __future__ import annotations

import contextlib
import gzip
import json
from pathlib import Path

import pytest

from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import StaticGeometry, cell, cell_center, make_cfg, row

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC, BC = cell_center(A), cell_center(B)

#: 0 -> 1 line: sat0 sees the source cell A, sat1 sees the destination cell B.
LINE_NB = {0: {"E": 1}, 1: {"W": 0}}
ISL_0_1 = "isl:0:1"
ISL_1_0 = "isl:1:0"
CTRL_BITS = 8_000

#: Speed of light, taken from the problem definition and NOT imported from
#: ``leo_sim.model``: the expected propagation delay must not be produced by
#: the same helper the tests are verifying, or a wrong constant/factor would
#: cancel out.  ``_prop_s`` is the independent reference.
C_KM_S = 299_792.458


def _prop_s(distance_km):
    return float(distance_km) / C_KM_S

#: repo root: CODE/leo_sim/tests/test_qualification_20261031.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
EVIDENCE_DIR = REPO_ROOT / "ANALYSIS" / "SIM-QUAL-20261031" / "qualification-evidence"


# --------------------------------------------------------------------------
# fixture helpers (thin wrappers over the shared test helpers)
# --------------------------------------------------------------------------
def _line_geometry(**overrides) -> StaticGeometry:
    """The scripted line topology: sat0 serves A, sat1 serves B."""

    def visible(sat, lat, lon, _t):
        return (sat == 0 and (lat, lon) == AC) or (sat == 1 and (lat, lon) == BC)

    kwargs = {"neighbors_map": LINE_NB, "visible": visible}
    kwargs.update(overrides)
    return StaticGeometry(2, **kwargs)


def _cfg(duration=10.0, *, isl_mbps=10.0, uplink_mbps=1000.0,
         downlink_mbps=1000.0, control=None, routing=None, execution=None,
         links=None, access=None) -> dict:
    over = {
        "scenario": {"duration_s": duration},
        "links": {"isl_rate_mbps": isl_mbps},
        "access": {"uplink_rate_mbps": uplink_mbps,
                   "downlink_rate_mbps": downlink_mbps},
    }
    if control is not None:
        over["control_plane"] = control
    if routing is not None:
        over["routing"] = routing
    if execution is not None:
        over["execution"] = execution
    if links:
        over["links"].update(links)
    if access:
        over["access"].update(access)
    return make_cfg(over)


def _control_cfg(interval=1.0, *, ttl=30.0, vis_k=2, bits=CTRL_BITS):
    return {"enabled": True, "advertise_interval_s": interval, "ttl_s": ttl,
            "vis_k": vis_k, "packet_bits": bits}


def _run(cfg, rows, *, geometry=None, sink=None, timeline=None, audit=False):
    return kernel.run_simulation(
        cfg, rows, geometry=geometry or _line_geometry(),
        decision_sink=sink if sink is not None else [],
        timeline_sink=timeline if timeline is not None else [],
        control_audit=audit)


# --------------------------------------------------------------------------
# raw-event readers (no kernel internals, only immutable outputs)
# --------------------------------------------------------------------------
def _events(res, kind=None, **match):
    out = []
    for event in res["packet_events"]:
        if kind is not None and event["kind"] != kind:
            continue
        if all(event.get(key) == value for key, value in match.items()):
            out.append(event)
    return out


def _isl_windows(res, link_id=ISL_0_1, stage="isl"):
    return [w for w in res["link_service_windows"]
            if w["stage"] == stage and w["link_id"] == link_id]


def _window_by_pid(res, link_id=ISL_0_1):
    out = {}
    for window in _isl_windows(res, link_id):
        assert window["pid"] not in out, (
            f"pid {window['pid']} served twice on {link_id}")
        out[window["pid"]] = window
    return out


def _rebuild_isl_queue(res, link_id=ISL_0_1):
    """Rebuild the ISL data queue level from raw events only.

    ``queue_enter`` adds the packet's bits, ``service_start`` on the same link
    removes them (the kernel pops the packet from ``data_q`` when service
    begins).  Enqueue sorts before dequeue at the same instant, which is the
    kernel's own ordering.  Returns (series, area_bits_s) where every series
    entry is (t, queued_bits) immediately after the mutation.
    """
    bits = {e["pid"]: e["bits"] for e in _events(res, "packet_emitted")}
    moves = []
    for event in res["packet_events"]:
        if (event["kind"] == "queue_enter" and event.get("queue") == "isl"
                and event.get("link_id") == link_id):
            moves.append((event["at"], 0, bits[event["pid"]], event["pid"]))
        elif (event["kind"] == "service_start"
              and event.get("stage") == "isl"
              and event.get("link_id") == link_id):
            moves.append((event["at"], 1, -bits[event["pid"]], event["pid"]))
    moves.sort(key=lambda move: (move[0], move[1]))
    series = [(0.0, 0)]
    level = 0
    for at, _order, delta, _pid in moves:
        level += delta
        series.append((float(at), level))
    area = 0.0
    for (t0, q0), (t1, _q1) in zip(series, series[1:]):
        area += q0 * (t1 - t0)
    area += series[-1][1] * (res["stop_time_s"] - series[-1][0])
    return series, area


def _read_ledger_series(audit, queue_id):
    """Read the opt-in queue ledger's level rows for one physical queue."""
    rows = []
    with gzip.open(audit["files"]["levels"]["path"], "rt",
                   encoding="utf-8") as handle:
        for line in handle:
            entry = json.loads(line)
            if entry.get("kind") == "level" and entry.get("queue") == queue_id:
                rows.append((float(entry["t"]), int(entry["bits"]),
                             str(entry["event"])))
    return rows


def _ctrl_windows(timeline, link_id):
    """{(origin, seq): {start, finish}} for control service on one link."""
    out = {}
    for mark in timeline:
        if mark.get("packet_kind") != "control":
            continue
        if mark.get("link_id") != link_id:
            continue
        key = (mark["origin"], mark["seq"])
        if mark["milestone"] == "service_start":
            out.setdefault(key, {})["start"] = float(mark["at"])
        elif mark["milestone"] == "service_finish":
            entry = out.setdefault(key, {})
            entry["finish"] = float(mark["at"])
    return {key: value for key, value in out.items()
            if "start" in value and "finish" in value}


def _ctrl_entries(timeline, link_id):
    """Control queue_enter rows (with the pre-insertion backlog) on a link."""
    return [mark for mark in timeline
            if mark.get("packet_kind") == "control"
            and mark.get("link_id") == link_id
            and mark.get("milestone") == "queue_enter"]


def _audit_rows(timeline, milestone, **match):
    rows = [mark for mark in timeline
            if mark.get("audit") == "control_plane"
            and mark.get("milestone") == milestone]
    return [mark for mark in rows
            if all(mark.get(key) == value for key, value in match.items())]


def _decision_rows(sink, pid=None, sat=None):
    out = []
    for entry in sink:
        if pid is not None and entry.get("pid") != pid:
            continue
        if sat is not None and entry.get("sat") != sat:
            continue
        out.append(entry)
    return out


def _marks(timeline, milestone, pid=None):
    return [mark for mark in timeline
            if mark.get("milestone") == milestone
            and (pid is None or mark.get("pid") == pid)]


def _same(actual, expected, tol=1e-9):
    return abs(float(actual) - float(expected)) <= tol


def _decision_view(entry):
    """The JSON-safe, assertion-relevant slice of one decision row."""
    observation = entry.get("observation_at_start") or {}
    cache = (entry.get("truth_at_commit") or {}).get("cache_entries") or {}
    return {
        "pid": entry.get("pid"),
        "sat": entry.get("sat"),
        "t": entry.get("t"),
        "t_decision_start": entry.get("t_decision_start"),
        "kind": entry.get("kind"),
        "chosen": entry.get("chosen"),
        "candidates": entry.get("candidates"),
        "obs_mode": entry.get("obs_mode"),
        "observation_t_observed": observation.get("t_observed"),
        "observation_source": observation.get("source"),
        "cache_entries": {
            str(origin): {
                "generated_at": record.get("generated_at"),
                "received_at": record.get("received_at"),
                "age_s": record.get("age_s"),
            }
            for origin, record in cache.items()},
    }


def _attempt_view(mark):
    """The JSON-safe slice of one decision_attempt timeline row.

    The full row carries a large four-direction audit; the qualification
    evidence keeps the timing/verdict fields that the assertions use.
    """
    return {key: mark.get(key) for key in
            ("at", "pid", "sat", "action", "route_status", "reason",
             "decision_id", "hop_index")}


# --------------------------------------------------------------------------
# evidence: parameters + raw event timeline + assertion table per case
# --------------------------------------------------------------------------
def _jsonable(value):
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except Exception:  # pragma: no cover - defensive only
            pass
    return repr(value)


class _Case:
    def __init__(self, name, parameters):
        self.name = name
        self.record = {
            "schema": "leo-sim-qualification-case/v1",
            "case": name,
            "parameters": _jsonable(parameters),
            "events": {},
            "assertions": [],
            "status": None,
            "error": None,
        }

    def event(self, key, rows):
        self.record["events"][key] = _jsonable(rows)

    def check(self, label, ok, **detail):
        detail = _jsonable(detail)
        self.record["assertions"].append(
            {"label": label, "pass": bool(ok), "detail": detail})
        assert ok, f"{self.name} / {label}: {detail}"

    def close(self, status, error=None):
        self.record["status"] = status
        self.record["error"] = error
        self.record["assertion_count"] = len(self.record["assertions"])
        self.record["failed_assertions"] = [
            item["label"] for item in self.record["assertions"]
            if not item["pass"]]
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        path = EVIDENCE_DIR / f"{self.name}.json"
        path.write_text(
            json.dumps(self.record, indent=1, sort_keys=True,
                       allow_nan=False) + "\n",
            encoding="utf-8")


@contextlib.contextmanager
def _case(name, parameters):
    case = _Case(name, parameters)
    try:
        yield case
    except BaseException as exc:
        case.close("FAIL", f"{type(exc).__name__}: {exc}")
        raise
    case.close("PASS")


# ==========================================================================
# A. link serialization
# ==========================================================================
def test_A1_two_packets_on_one_isl_occupy_disjoint_windows(tmp_path):
    """Two same-direction packets: packet 2 starts exactly when packet 1 ends.

    Theory: a directional ISL egress serves one packet at a time, so for a
    packet of S bits at link rate R the service window is [t0, t0 + S/R] and
    the next packet cannot begin before t0 + S/R.

    The queue/service timeline is rebuilt from raw ``packet_events`` and then
    cross-checked against the independent opt-in queue ledger
    (``queue_ledger_dir``), so the reconstruction is verified against a second
    output stream rather than against itself.
    """
    bits, mbps = 8_000_000, 10.0
    rate_bps = mbps * 1e6
    service_s = bits / rate_bps
    cfg = _cfg(10.0, isl_mbps=mbps)
    rows = [row(1, 0.0, A, B, bits=bits), row(2, 0.0, A, B, bits=bits)]
    res = _run(cfg, rows)
    series, rebuilt_area = _rebuild_isl_queue(res)
    windows = _window_by_pid(res)
    ledger_res = kernel.run_simulation(
        cfg, rows, geometry=_line_geometry(),
        queue_ledger_dir=str(tmp_path), queue_ledger_prefix="a1")
    ledger_series = _read_ledger_series(
        ledger_res["queue_ledger_audit"], "isl_data:isl:0:1:gen0")
    with _case("A1-link-serialization", {
            "packet_bits": bits, "isl_rate_mbps": mbps,
            "expected_T_tx_s": service_s,
            "queues_ledgered": bool(ledger_series),
            "rows": [{"pid": 1, "emit_time_s": 0.0},
                     {"pid": 2, "emit_time_s": 0.0}]}) as case:
        case.event("isl_service_windows", list(windows.values()))
        case.event("packet_events", [
            event for event in res["packet_events"]
            if event.get("link_id") in (ISL_0_1, ISL_1_0)
            or event["kind"] in ("packet_emitted", "delivered")])
        case.event("rebuilt_queue_series", [
            {"t": t, "queued_bits": level} for t, level in series])
        case.event("queue_ledger_isl_data_series", [
            {"t": t, "queued_bits": level, "event": event}
            for t, level, event in ledger_series])
        case.event("service_log_isl", res["service_log"]["isl"])
        case.event("deliveries", res["deliveries"])

        case.check("both packets delivered",
                   res["fates"] == {1: "DELIVERED", 2: "DELIVERED"},
                   expected={1: "DELIVERED", 2: "DELIVERED"},
                   actual=res["fates"])
        case.check("both packets really used the single ISL egress",
                   set(windows) == {1, 2} and
                   [pid for _kind, pid in res["service_log"]["isl"]] == [1, 2],
                   windows=sorted(windows),
                   service_log=res["service_log"]["isl"])

        first, second = windows[1], windows[2]
        case.check("packet 1 occupies exactly [t0, t0 + S/R]",
                   _same(first["end"] - first["start"], service_s, 1e-12),
                   expected=service_s,
                   actual=first["end"] - first["start"])
        case.check("packet 2 does not start before packet 1 completes",
                   second["start"] >= first["end"] - 1e-12,
                   packet1_end=first["end"], packet2_start=second["start"])
        case.check("packet 2 starts at the completion instant of packet 1",
                   _same(second["start"], first["end"], 1e-9),
                   expected=first["end"], actual=second["start"])
        case.check("the two windows are disjoint (no simultaneous service)",
                   second["start"] >= first["end"] - 1e-12
                   and first["start"] < first["end"],
                   first=[first["start"], first["end"]],
                   second=[second["start"], second["end"]])
        case.check("packet 2 was already queued while packet 1 transmitted",
                   second["start"] > _events(res, "queue_enter",
                                             queue="isl", pid=2)[0]["at"],
                   packet2_enqueue=_events(res, "queue_enter", queue="isl",
                                           pid=2)[0]["at"],
                   packet2_service_start=second["start"])

        # -- rebuild queue/service from raw events only -------------------
        enq1 = _events(res, "queue_enter", queue="isl", pid=1)[0]["at"]
        enq2 = _events(res, "queue_enter", queue="isl", pid=2)[0]["at"]
        case.check("rebuilt queue shows packet 1 as the only occupant first",
                   series[1] == (float(enq1), bits),
                   expected=[float(enq1), bits], actual=list(series[1]))
        case.check("rebuilt queue drains when packet 1 starts service",
                   series[2] == (first["start"], 0),
                   expected=[first["start"], 0], actual=list(series[2]))
        case.check("rebuilt queue holds packet 2 until packet 1 completes",
                   series[3] == (float(enq2), bits),
                   expected=[float(enq2), bits], actual=list(series[3]))
        case.check("rebuilt queue is empty at packet 2 service start",
                   series[4] == (second["start"], 0),
                   expected=[second["start"], 0], actual=list(series[4]))
        native_area = float(res["queue_area_bits_s"]["isl_data"])
        case.check("event-rebuilt queue area equals the native isl_data area",
                   _same(rebuilt_area, native_area, 1e-6),
                   expected=native_area, actual=rebuilt_area)
        ledger_mutations = [(t, level) for t, level, event in ledger_series
                            if event != "close"]
        case.check("the opt-in queue ledger really records the isl_data queue",
                   bool(ledger_mutations),
                   queue="isl_data:isl:0:1:gen0",
                   rows=len(ledger_series))
        case.check("queue ledger levels equal the event-rebuilt levels "
                   "(two independent output streams agree)",
                   ledger_mutations == list(series[1:]),
                   expected=[list(entry) for entry in series[1:]],
                   actual=[list(entry) for entry in ledger_mutations])


def test_A2_transmission_time_equals_bits_over_rate_at_two_points():
    """T_tx = S/R at several (S, R) points, re-derived from raw windows."""
    points = [(8_000_000, 10.0), (2_000_000, 8.0), (4_000_000, 20.0)]
    measured = []
    with _case("A2-transmission-time-scaling", {
            "points": [{"packet_bits": bits, "isl_rate_mbps": mbps,
                        "expected_T_tx_s": bits / (mbps * 1e6)}
                       for bits, mbps in points]}) as case:
        for bits, mbps in points:
            rate_bps = mbps * 1e6
            expect = bits / rate_bps
            res = _run(_cfg(10.0, isl_mbps=mbps),
                       [row(1, 0.0, A, B, bits=bits),
                        row(2, 0.0, A, B, bits=bits)])
            windows = _window_by_pid(res)
            measured.append({"packet_bits": bits, "isl_rate_mbps": mbps,
                             "T_tx_s": windows[1]["end"] - windows[1]["start"],
                             "expected_T_tx_s": expect})
            case.event(f"windows_{bits}_{mbps}", list(windows.values()))
            case.check(f"T_tx = S/R at S={bits}, R={mbps} Mbps",
                       _same(windows[1]["end"] - windows[1]["start"],
                             expect, 1e-9),
                       expected=expect,
                       actual=windows[1]["end"] - windows[1]["start"])
            case.check(f"second packet serialized after the first at "
                       f"S={bits}, R={mbps} Mbps",
                       _same(windows[2]["start"], windows[1]["end"], 1e-9),
                       expected=windows[1]["end"],
                       actual=windows[2]["start"])
            case.check(f"all queued bits served (served_bits == S) at "
                       f"S={bits}, R={mbps} Mbps",
                       windows[1]["served_bits"] == bits
                       and windows[2]["served_bits"] == bits,
                       served=[windows[1]["served_bits"],
                               windows[2]["served_bits"]], bits=bits)
        case.event("measured_points", measured)


# ==========================================================================
# B. transmission / propagation separation
# ==========================================================================
def test_B1_arrival_is_service_start_plus_tx_plus_prop():
    """t_arrival = t_service_start + T_tx + T_prop, and the link keeps working."""
    bits, mbps, distance_km = 8_000_000, 10.0, 1000.0
    rate_bps = mbps * 1e6
    t_tx = bits / rate_bps
    t_prop = _prop_s(distance_km)
    res = _run(_cfg(10.0, isl_mbps=mbps),
               [row(1, 0.0, A, B, bits=bits), row(2, 0.0, A, B, bits=bits)],
               geometry=_line_geometry(isl_km=distance_km))
    windows = _window_by_pid(res)
    starts = {event["pid"]: event
              for event in _events(res, "service_start", stage="isl")}
    props = {event["pid"]: event
             for event in _events(res, "propagation_start", stage="isl")}
    arrivals = {event["prop_id"]: event
                for event in _events(res, "propagation_arrival")}
    with _case("B1-tx-propagation-separation", {
            "packet_bits": bits, "isl_rate_mbps": mbps,
            "isl_distance_km": distance_km,
            "expected_T_tx_s": t_tx, "expected_T_prop_s": t_prop,
            "expected_arrival_formula": "service_start + T_tx + T_prop"}) as case:
        case.event("isl_service_windows", list(windows.values()))
        case.event("isl_service_starts", list(starts.values()))
        case.event("isl_propagation_starts", list(props.values()))
        case.event("arrivals", [
            {"pid": pid, "at": arrivals[prop["prop_id"]]["at"],
             "prop_id": prop["prop_id"]}
            for pid, prop in props.items()])
        downstream = [event for event in res["packet_events"]
                      if event.get("pid") == 1
                      and event["kind"] in ("queue_enter",
                                            "service_start", "delivered")
                      and (event.get("stage") == "downlink"
                           or event.get("queue") == "downlink"
                           or event["kind"] == "delivered")]
        case.event("pid1_downstream_events", downstream)

        for pid in (1, 2):
            arrival = arrivals[props[pid]["prop_id"]]["at"]
            case.check(
                f"pid{pid}: transmission starts at the service start and lasts "
                f"exactly S/R",
                _same(props[pid]["at"] - starts[pid]["at"], t_tx, 1e-9),
                expected=t_tx,
                actual=props[pid]["at"] - starts[pid]["at"])
            case.check(
                f"pid{pid}: propagation delay equals d/c for the real distance",
                _same(props[pid]["delay_s"], t_prop, 1e-15),
                expected=t_prop, actual=props[pid]["delay_s"])
            case.check(
                f"pid{pid}: t_arrival == t_service_start + T_tx + T_prop",
                _same(arrival,
                      starts[pid]["at"] + t_tx + t_prop, 1e-9),
                expected=starts[pid]["at"] + t_tx + t_prop,
                actual=arrival)
            case.check(
                f"pid{pid}: arrival happens strictly after transmission ends",
                arrival > props[pid]["at"] + 1e-12,
                transmission_end=props[pid]["at"], arrival=arrival)

        arrival_1 = arrivals[props[1]["prop_id"]]["at"]
        case.check(
            "the link serves packet 2 while packet 1 is still propagating",
            windows[2]["start"] < arrival_1,
            packet2_service_start=windows[2]["start"],
            packet1_arrival=arrival_1)
        case.check(
            "packet 2 is served back-to-back after packet 1 (no propagation "
            "hold on the transmitter)",
            _same(windows[2]["start"], windows[1]["end"], 1e-9),
            expected=windows[1]["end"], actual=windows[2]["start"])

        # the next hop may only read the packet once it has ARRIVED
        read_at = [event for event in downstream if event["kind"] == "queue_enter"]
        case.check("the next hop first reads the packet at/after its arrival",
                   bool(read_at) and read_at[0]["at"] >= arrival_1 - 1e-12,
                   first_read_at=(read_at[0]["at"] if read_at else None),
                   arrival=arrival_1)
        delivered = [event for event in downstream
                     if event["kind"] == "delivered"]
        case.check("delivery cannot precede the propagation arrival",
                   bool(delivered)
                   and delivered[0]["at"] >= arrival_1 - 1e-12,
                   delivered_at=(delivered[0]["at"] if delivered else None),
                   arrival=arrival_1)


def test_B2_propagation_delay_follows_the_real_isl_distance():
    """Doubling the ISL distance doubles T_prop inside the same arrival identity."""
    bits, mbps = 2_000_000, 10.0
    t_tx = bits / (mbps * 1e6)
    distances = (1000.0, 2000.0)
    props = {}
    with _case("B2-propagation-distance", {
            "packet_bits": bits, "isl_rate_mbps": mbps,
            "distances_km": list(distances),
            "expected_T_prop_s": [_prop_s(d)
                                  for d in distances]}) as case:
        for distance_km in distances:
            t_prop = _prop_s(distance_km)
            res = _run(_cfg(10.0, isl_mbps=mbps),
                       [row(1, 0.0, A, B, bits=bits)],
                       geometry=_line_geometry(isl_km=distance_km))
            start = _events(res, "service_start", stage="isl", pid=1)[0]
            prop = _events(res, "propagation_start", stage="isl", pid=1)[0]
            arrival = [event for event in _events(res, "propagation_arrival")
                       if event["prop_id"] == prop["prop_id"]][0]
            props[distance_km] = t_prop
            case.event(f"events_d_{int(distance_km)}", [start, prop, arrival])
            case.check(f"d={distance_km:g} km: T_prop == d/c",
                       _same(prop["delay_s"], t_prop, 1e-15),
                       expected=t_prop, actual=prop["delay_s"])
            case.check(f"d={distance_km:g} km: arrival == start + T_tx + T_prop",
                       _same(arrival["at"], start["at"] + t_tx + t_prop, 1e-9),
                       expected=start["at"] + t_tx + t_prop,
                       actual=arrival["at"])
        case.check("doubling the distance doubles the propagation delay",
                   _same(props[2000.0], 2.0 * props[1000.0], 1e-15),
                   expected=2.0 * props[1000.0], actual=props[2000.0])


# ==========================================================================
# C. control really occupies the shared ISL service
# ==========================================================================
def test_C1_control_waits_for_data_and_cannot_be_overtaken_by_data():
    """data_1 in service, control arrives inside it, data_2 already waiting.

    Contract: non-preemptive priority.  data_1 is never interrupted, the
    control packet that arrived during its transmission is served with a real
    transmission time as soon as data_1 completes, and data_2 (queued earlier
    than the control packet) may not jump over it.
    """
    bits, mbps = 8_000_000, 1.0
    rate_bps = mbps * 1e6
    data_service_s = bits / rate_bps
    ctrl_service_s = CTRL_BITS / rate_bps
    cfg = _cfg(30.0, isl_mbps=mbps, control=_control_cfg(1.0),
               routing={"policy": "hop"})
    # warm-up packet (small) establishes the destination association so the
    # measured packets can be routed; measurements start at t=4.
    rows = [row(99, 0.0, A, B, bits=CTRL_BITS),
            row(1, 4.0, A, B, bits=bits),
            row(2, 4.0, A, B, bits=bits)]
    sink, timeline = [], []
    res = _run(cfg, rows, sink=sink, timeline=timeline, audit=True)
    windows = _window_by_pid(res)
    ctrl = _ctrl_windows(timeline, ISL_0_1)
    enters = _ctrl_entries(timeline, ISL_0_1)
    with _case("C1-control-nonpreemptive", {
            "packet_bits": bits, "isl_rate_mbps": mbps,
            "control_packet_bits": CTRL_BITS,
            "control_advertise_interval_s": 1.0,
            "expected_data_service_s": data_service_s,
            "expected_ctrl_service_s": ctrl_service_s,
            "rows": [{"pid": 99, "emit_time_s": 0.0, "bits": CTRL_BITS},
                     {"pid": 1, "emit_time_s": 4.0, "bits": bits},
                     {"pid": 2, "emit_time_s": 4.0, "bits": bits}]}) as case:
        case.event("isl_service_windows", list(windows.values()))
        case.event("control_service_windows", [
            {"origin": key[0], "seq": key[1], "start": value["start"],
             "finish": value["finish"],
             "duration": value["finish"] - value["start"]}
            for key, value in sorted(ctrl.items())])
        case.event("control_queue_entries", [
            {key: mark.get(key) for key in
             ("at", "origin", "seq", "link_id", "backlog_before")}
            for mark in enters])
        case.event("data_queue_entries", [
            event for event in _events(res, "queue_enter", queue="isl",
                                       link_id=ISL_0_1)])
        case.event("fates", res["fates"])

        data1, data2 = windows[1], windows[2]
        case.check("data_1 owns a full S/R window",
                   _same(data1["end"] - data1["start"], data_service_s, 1e-9),
                   expected=data_service_s,
                   actual=data1["end"] - data1["start"])
        inside = [key for key, value in ctrl.items()
                  if data1["start"] < value["start"] < data1["end"]]
        case.check("no control packet is served inside data_1's window "
                   "(no preemption)", inside == [],
                   served_inside=inside,
                   data1_window=[data1["start"], data1["end"]])

        # control packets generated during data_1's transmission wait in queue
        during = [mark for mark in enters
                  if data1["start"] < mark["at"] < data1["end"]]
        case.check("a control packet is enqueued while data_1 transmits",
                   len(during) >= 1,
                   enqueued_at=[mark["at"] for mark in during],
                   data1_window=[data1["start"], data1["end"]])
        case.check("the first control enqueue during data_1 is its next "
                   "advertisement (t=5.0)",
                   _same(during[0]["at"], 5.0, 1e-12),
                   expected=5.0, actual=during[0]["at"])
        case.check("control packets were really served on the shared ISL",
                   bool(ctrl), served_control_packets=len(ctrl),
                   link_id=ISL_0_1)
        first_after = min(
            (value for value in ctrl.values()
             if value["start"] >= data1["end"] - 1e-12),
            key=lambda value: value["start"])
        case.check("the queued control is served exactly when data_1 completes",
                   _same(first_after["start"], data1["end"], 1e-9),
                   expected=data1["end"], actual=first_after["start"])
        case.check(
            "the control packet really transmits (duration == ctrl_bits/R)",
            all(_same(value["finish"] - value["start"], ctrl_service_s, 1e-12)
                for value in ctrl.values()),
            expected=ctrl_service_s,
            actual=sorted({round(value["finish"] - value["start"], 12)
                           for value in ctrl.values()}))
        case.check(
            "control backlog was measured behind a data packet still in "
            "service (non-preemptive wait)",
            any((mark.get("backlog_before") or {}).get(
                "in_service_remaining_bits_before") not in (None, 0)
                for mark in during),
            backlog=[(mark.get("backlog_before") or {}).get(
                "in_service_remaining_bits_before") for mark in during])

        # data_2 was queued BEFORE that control packet and still cannot overtake
        data2_enqueue = _events(res, "queue_enter", queue="isl", pid=2)[0]["at"]
        case.check("data_2 is already waiting when the control packet arrives",
                   data2_enqueue < during[0]["at"],
                   data2_enqueue=data2_enqueue,
                   control_enqueue=during[0]["at"])
        served_between = sorted(
            (value for value in ctrl.values()
             if data1["end"] - 1e-12 <= value["start"] <= data2["start"] + 1e-12),
            key=lambda value: value["start"])
        last_ctrl = served_between[-1]
        case.check("data_2 starts only after the last queued control completes",
                   _same(data2["start"], last_ctrl["finish"], 1e-9),
                   expected=last_ctrl["finish"], actual=data2["start"])
        case.check("data_2 cannot skip the control packets",
                   len(served_between) >= 1
                   and data2["start"] >= max(
                       value["finish"] for value in served_between) - 1e-12,
                   served_controls=len(served_between),
                   data2_start=data2["start"])

        # exact service order on the link, rebuilt from raw service intervals
        intervals = [{"start": window["start"], "kind": "data",
                      "pid": window["pid"]}
                     for window in windows.values()]
        intervals += [{"start": value["start"], "kind": "ctrl",
                       "origin": key[0], "seq": key[1]}
                      for key, value in ctrl.items()]
        intervals.sort(key=lambda item: item["start"])
        order = [(item["kind"], item.get("pid", item.get("seq")))
                 for item in intervals
                 if item["start"] >= data1["start"] - 1e-12]
        expected_order = ([("data", 1)]
                          + [("ctrl", key[1])
                             for _start, key in sorted(
                                 (value["start"], key)
                                 for key, value in ctrl.items()
                                 if data1["end"] - 1e-12 <= value["start"]
                                 <= data2["start"] + 1e-12)]
                          + [("data", 2)])
        case.check("raw service order is data_1 -> control* -> data_2",
                   order[:len(expected_order)] == expected_order,
                   expected=expected_order,
                   actual=order[:len(expected_order)])


def test_C2_higher_broadcast_rate_adds_real_control_service_work():
    """Two runs, same traffic: a 4x higher advert rate adds ~4x control work."""
    bits, mbps = 8_000_000, 1.0
    rate_bps = mbps * 1e6
    ctrl_service_s = CTRL_BITS / rate_bps
    rows = [row(99, 0.0, A, B, bits=CTRL_BITS),
            row(1, 4.0, A, B, bits=bits),
            row(2, 4.0, A, B, bits=bits)]
    runs = {}
    with _case("C2-control-rate-bandwidth", {
            "packet_bits": bits, "isl_rate_mbps": mbps,
            "control_packet_bits": CTRL_BITS,
            "control_intervals_s": [1.0, 0.25],
            "expected_ctrl_service_s": ctrl_service_s,
            "rows": [{"pid": 99, "emit_time_s": 0.0, "bits": CTRL_BITS},
                     {"pid": 1, "emit_time_s": 4.0, "bits": bits},
                     {"pid": 2, "emit_time_s": 4.0, "bits": bits}]}) as case:
        for interval in (1.0, 0.25):
            cfg = _cfg(30.0, isl_mbps=mbps,
                       control=_control_cfg(interval, ttl=60.0),
                       routing={"policy": "hop"})
            timeline = []
            res = _run(cfg, rows, timeline=timeline, audit=True)
            ctrl = _ctrl_windows(timeline, ISL_0_1)
            served = len(ctrl)
            total = sum(value["finish"] - value["start"]
                        for value in ctrl.values())
            delivered = int(res["control"]["bits"]["delivered"])
            runs[interval] = {"served": served, "total_service_s": total,
                              "delivered_bits": delivered}
            case.event(f"control_windows_interval_{interval}", [
                {"origin": key[0], "seq": key[1], "start": value["start"],
                 "finish": value["finish"]}
                for key, value in sorted(ctrl.items())])
            case.check(
                f"interval={interval}: every control packet has a real S/R "
                f"transmission time",
                bool(ctrl) and all(
                    _same(value["finish"] - value["start"],
                          ctrl_service_s, 1e-12)
                    for value in ctrl.values()),
                served_control_packets=len(ctrl),
                expected=ctrl_service_s,
                actual=sorted({round(value["finish"] - value["start"], 12)
                               for value in ctrl.values()}))
        lo, hi = runs[1.0], runs[0.25]
        case.event("summary", runs)
        case.check("4x higher broadcast rate serves more control packets",
                   hi["served"] > lo["served"],
                   interval_1_0=lo["served"], interval_0_25=hi["served"])
        case.check("4x higher broadcast rate adds real control service time",
                   hi["total_service_s"] > lo["total_service_s"],
                   interval_1_0=lo["total_service_s"],
                   interval_0_25=hi["total_service_s"])
        case.check("control service work scales ~with the broadcast rate",
                   hi["total_service_s"] >= 3.5 * lo["total_service_s"],
                   expected=">= 3.5x", actual=(
                       hi["total_service_s"] / lo["total_service_s"]))
        case.check("the control ledger charges the same scaling in bits",
                   hi["delivered_bits"] >= 3.5 * lo["delivered_bits"],
                   interval_1_0=lo["delivered_bits"],
                   interval_0_25=hi["delivered_bits"],
                   expected=">= 3.5x")


# ==========================================================================
# D. basic queueing: lambda < mu bounded, lambda > mu grows at lambda - mu
# ==========================================================================
QUEUE_BITS = 8_000_000
QUEUE_RATE_BPS = 10_000_000.0
QUEUE_RATE_MBPS = QUEUE_RATE_BPS / 1e6
MU_PKTS_PER_S = QUEUE_RATE_BPS / QUEUE_BITS


def _queue_load_run(lam, duration=40.0):
    """Deterministic periodic arrivals at rate ``lam`` packets/s."""
    last_emit = duration - 2.0
    count = int(lam * last_emit)
    rows = [row(index + 1, (index + 1) / lam, A, B, bits=QUEUE_BITS)
            for index in range(count)]
    cfg = _cfg(duration, isl_mbps=QUEUE_RATE_MBPS,
               links={"isl_queue_bits": 20_000_000_000},
               access={"uplink_queue_bits": 2_000_000_000})
    res = _run(cfg, rows)
    series, area = _rebuild_isl_queue(res)
    return {"rows": rows, "res": res, "series": series, "area": area}


def _fit_slope(series, t_lo, t_hi):
    points = [(t, q) for t, q in series if t_lo <= t <= t_hi]
    assert len(points) >= 2, "fit window must contain queue mutations"
    mean_t = sum(t for t, _q in points) / len(points)
    mean_q = sum(q for _t, q in points) / len(points)
    numerator = sum((t - mean_t) * (q - mean_q) for t, q in points)
    denominator = sum((t - mean_t) ** 2 for t, _q in points)
    return numerator / denominator, points


def test_D1_underloaded_and_critical_queues_stay_bounded():
    """lambda < mu and lambda ~ mu: the reconstructed queue stays bounded."""
    loads = [("low", 0.5, 0.4), ("near_capacity", 1.2, 0.96)]
    with _case("D1-queue-bounded", {
            "packet_bits": QUEUE_BITS, "service_rate_bps": QUEUE_RATE_BPS,
            "service_rate_pkts_per_s": MU_PKTS_PER_S,
            "loads": [{"name": name, "lambda_pkts_per_s": lam,
                       "offered_mbps": lam * QUEUE_BITS / 1e6,
                       "utilization": util}
                      for name, lam, util in loads]}) as case:
        for name, lam, util in loads:
            data = _queue_load_run(lam)
            res, series = data["res"], data["series"]
            qmax = max(level for _t, level in series)
            qend = series[-1][1]
            slope, points = _fit_slope(series, 2.0, 34.0)
            case.event(f"queue_series_{name}", [
                {"t": t, "queued_bits": level} for t, level in series])
            case.event(f"fates_{name}", res["fates"])
            case.check(f"{name}: the queue is bounded (<= 2 packets at "
                       f"lambda={lam}, rho={util})",
                       qmax <= 2 * QUEUE_BITS, lambda_=lam, rho=util,
                       max_queued_bits=qmax, bound_bits=2 * QUEUE_BITS)
            case.check(f"{name}: the queue drains back to empty",
                       qend == 0, lambda_=lam, trailing_queued_bits=qend)
            case.check(f"{name}: reconstructed slope shows no sustained growth",
                       abs(slope) < 1e5, lambda_=lam, fitted_slope_bps=slope,
                       theory_slope_bps=lam * QUEUE_BITS - QUEUE_RATE_BPS)
            case.check(
                f"{name}: event-rebuilt queue area matches native isl_data",
                _same(data["area"], float(res["queue_area_bits_s"]["isl_data"]),
                      1e-6),
                expected=float(res["queue_area_bits_s"]["isl_data"]),
                actual=data["area"])
            case.check(f"{name}: every emitted packet is eventually delivered",
                       all(fate == "DELIVERED" for fate in res["fates"].values()),
                       non_delivered=[(pid, fate) for pid, fate
                                      in res["fates"].items()
                                      if fate != "DELIVERED"])


def test_D2_overloaded_queue_grows_at_lambda_minus_mu():
    """lambda > mu: the reconstructed queue grows with slope ~ lambda - mu."""
    lam = 2.0
    theory = lam * QUEUE_BITS - QUEUE_RATE_BPS  # bits/s, positive
    data = _queue_load_run(lam)
    res, series = data["res"], data["series"]
    slope, points = _fit_slope(series, 5.0, 34.0)
    with _case("D2-queue-overload", {
            "packet_bits": QUEUE_BITS, "service_rate_bps": QUEUE_RATE_BPS,
            "lambda_pkts_per_s": lam,
            "offered_bps": lam * QUEUE_BITS,
            "theory_slope_bps": theory,
            "fit_window_s": [5.0, 34.0]}) as case:
        case.event("queue_series", [
            {"t": t, "queued_bits": level} for t, level in series])
        case.event("fates", res["fates"])
        q_at_start = [level for t, level in series if t <= 5.0][-1]
        q_at_end = series[-1][1]
        case.check("the queue grows without bound while lambda > mu",
                   q_at_end > q_at_start + 10 * QUEUE_BITS,
                   queued_at_5s=q_at_start, queued_at_stop=q_at_end)
        case.check("the fitted slope is positive (sustained growth)",
                   slope > 0.5 * theory, fitted_slope_bps=slope,
                   theory_slope_bps=theory)
        case.check("the fitted slope magnitude matches lambda - mu",
                   abs(slope - theory) <= 0.10 * abs(theory),
                   fitted_slope_bps=slope, theory_slope_bps=theory,
                   relative_error=abs(slope - theory) / abs(theory))
        # the instantaneous level saw-tooths (every packet is served), so the
        # sustained growth is asserted on window means rather than pointwise
        window_means = []
        for window_start in (5.0, 10.0, 15.0, 20.0, 25.0):
            window_end = window_start + 5.0
            levels = [level for t, level in series
                      if window_start <= t < window_end]
            window_means.append(sum(levels) / len(levels))
        case.event("window_mean_queued_bits", [
            {"window_s": [5.0 + 5.0 * index, 10.0 + 5.0 * index],
             "mean_queued_bits": mean}
            for index, mean in enumerate(window_means)])
        case.check("queue growth is sustained (successive 5 s means increase)",
                   all(second > first for first, second in
                       zip(window_means, window_means[1:])),
                   window_means=window_means)
        case.check("overload leaves packets in system at the horizon "
                   "(the queue really was growing)",
                   any(fate != "DELIVERED" for fate in res["fates"].values()),
                   fate_counts=res["fate_counts"])
        case.check("event-rebuilt queue area matches native isl_data",
                   _same(data["area"],
                         float(res["queue_area_bits_s"]["isl_data"]), 1e-6),
                   expected=float(res["queue_area_bits_s"]["isl_data"]),
                   actual=data["area"])


# ==========================================================================
# E. state-message time chain
# ==========================================================================
E_TTL = 30.0
E_CONTROL = _control_cfg(2.0, ttl=E_TTL)


def _state_run(ttl=E_TTL, duration=3.0, extra_rows=None, audit=True):
    cfg = _cfg(duration, isl_mbps=1.0,
               control=_control_cfg(2.0, ttl=ttl),
               routing={"policy": "hop"})
    rows = [row(99, 0.0, A, B, bits=CTRL_BITS)]
    rows += list(extra_rows or [])
    sink, timeline = [], []
    res = _run(cfg, rows, sink=sink, timeline=timeline, audit=audit)
    return res, sink, timeline


def _advert_chain(timeline, origin=1, receiver=0, serve_cell=B):
    """(t_m, t_rx, seq) of the first advertisement from ``origin`` that
    carries ``serve_cell`` and actually arrives at ``receiver``."""
    generated = [mark for mark in _audit_rows(
        timeline, "ctrl_advert_generated", origin=origin)
        if serve_cell in (mark.get("serve_cells") or [])
        and mark["seq"] in {row_["seq"] for row_ in _audit_rows(
            timeline, "ctrl_advert_arrived", origin=origin, sat=receiver)}]
    assert generated, "the fixture must generate an advertisement carrying B"
    first = min(generated, key=lambda mark: mark["at"])
    arrived = [mark for mark in _audit_rows(
        timeline, "ctrl_advert_arrived", origin=origin, sat=receiver,
        seq=first["seq"])]
    assert arrived, "the advertisement must physically arrive"
    return float(first["at"]), float(arrived[0]["at"]), first["seq"]


def test_E1_state_message_chain_generated_queue_tx_prop_cache():
    """t_m -> queue -> S/R -> d/c -> t_rx; nothing can be read before t_rx."""
    res, sink, timeline = _state_run(
        extra_rows=[row(1, 2.001, A, B, bits=CTRL_BITS),
                    row(2, 2.3, A, B, bits=CTRL_BITS)])
    t_m, t_rx, seq = _advert_chain(timeline)
    ctrl_service_s = CTRL_BITS / 1e6
    prop_s = _prop_s(1000.0)
    decisions = [_decision_view(entry) for entry in sink]
    attempts = [mark for mark in timeline
                if mark["milestone"] == "decision_attempt"
                and mark.get("pid") == 1]
    with _case("E1-state-message-chain", {
            "control_packet_bits": CTRL_BITS, "isl_rate_mbps": 1.0,
            "isl_distance_km": 1000.0,
            "ttl_s": E_TTL, "advertise_interval_s": 2.0,
            "expected_t_m_plus_tx_plus_prop": ctrl_service_s + prop_s,
            "rows": [{"pid": 99, "emit_time_s": 0.0},
                     {"pid": 1, "emit_time_s": 2.001},
                     {"pid": 2, "emit_time_s": 2.3}],
            "advert_seq": seq}) as case:
        case.event("audit_generated", _audit_rows(
            timeline, "ctrl_advert_generated", origin=1, seq=seq))
        case.event("audit_arrived", _audit_rows(
            timeline, "ctrl_advert_arrived", origin=1, sat=0, seq=seq))
        case.event("decision_attempts_pid1", [_attempt_view(mark)
                                              for mark in attempts])
        case.event("decisions", decisions)
        case.event("cache_sat0", res["caches"][0])

        case.check("the advertisement needed T_tx + T_prop to arrive",
                   _same(t_rx - t_m, ctrl_service_s + prop_s, 1e-9),
                   expected=ctrl_service_s + prop_s, actual=t_rx - t_m)
        entry = res["caches"][0][1]
        case.check("the receiver cache entry carries generated_at == t_m",
                   _same(entry["generated_at"], t_m, 1e-12),
                   expected=t_m, actual=entry["generated_at"])
        case.check("the receiver cache entry carries received_at == t_rx",
                   _same(entry["received_at"], t_rx, 1e-12),
                   expected=t_rx, actual=entry["received_at"])
        case.check("before t_rx the packet cannot read the state "
                   "(binding hold attempt, no information)",
                   bool(attempts)
                   and attempts[0]["at"] < t_rx
                   and attempts[0]["route_status"] == "no_info"
                   and attempts[0]["action"] == "hold",
                   attempts=[{"at": mark["at"],
                              "route_status": mark["route_status"],
                              "action": mark["action"]}
                             for mark in attempts],
                   t_rx=t_rx)
        early = [row_ for row_ in decisions
                 if row_["pid"] == 1 and row_["t"] < t_rx - 1e-12]
        case.check("no decision for pid1 is committed before t_rx "
                   "(no future state is read)", early == [],
                   early_decisions=early, t_rx=t_rx)
        pid1_forward = [row_ for row_ in decisions
                        if row_["pid"] == 1 and row_["kind"] == "forward"]
        case.check("once t >= t_rx the state is usable (pid1 forwards)",
                   bool(pid1_forward)
                   and pid1_forward[0]["t"] >= t_rx - 1e-12
                   and pid1_forward[0]["chosen"] == "E",
                   forward=pid1_forward[0] if pid1_forward else None,
                   t_rx=t_rx)
        pid2 = [row_ for row_ in decisions
                if row_["pid"] == 2 and row_["kind"] == "forward"]
        pid2_attempts = [mark for mark in timeline
                         if mark["milestone"] == "decision_attempt"
                         and mark.get("pid") == 2]
        expected_ingress = (2.3 + CTRL_BITS / 1e9
                            + _prop_s(600.0))
        case.check("a packet whose ingress happens after t_rx forwards "
                   "immediately (no hold attempt, commit at ingress)",
                   bool(pid2) and pid2_attempts == []
                   and _same(pid2[0]["t"], expected_ingress, 1e-9),
                   forward=pid2[0] if pid2 else None,
                   hold_attempts=len(pid2_attempts),
                   expected_ingress=expected_ingress, t_rx=t_rx)
        for row_ in decisions:
            for origin, record in row_["cache_entries"].items():
                case.check(
                    f"pid{row_['pid']} @t={row_['t']:.6f}: cache entry "
                    f"origin {origin} had already arrived (received_at <= t)",
                    record["received_at"] <= row_["t"] + 1e-12,
                    received_at=record["received_at"], decision_t=row_["t"])
                case.check(
                    f"pid{row_['pid']} @t={row_['t']:.6f}: AoI(t) = t - t_m "
                    f"for origin {origin}",
                    _same(record["age_s"],
                          row_["t"] - record["generated_at"], 1e-9),
                    expected=row_["t"] - record["generated_at"],
                    actual=record["age_s"])
        case.check("AoI at the stop instant is t - t_m",
                   _same(entry["aoi"], res["stop_time_s"] - t_m, 1e-9),
                   expected=res["stop_time_s"] - t_m, actual=entry["aoi"])
        case.check("the entry is still valid at stop (TTL not exceeded)",
                   entry["valid"] is True, valid=entry["valid"])


def test_E2_ttl_expiry_blocks_use_of_a_received_state_message():
    """A received advertisement becomes unusable exactly after t_m + TTL."""
    ttl = 0.5
    res, sink, timeline = _state_run(
        ttl=ttl,
        extra_rows=[row(1, 2.4, A, B, bits=CTRL_BITS),
                    row(2, 2.6, A, B, bits=CTRL_BITS)])
    t_m, t_rx, seq = _advert_chain(timeline)
    expiry = t_m + ttl
    decisions = [_decision_view(entry) for entry in sink]
    attempts = [mark for mark in timeline
                if mark["milestone"] == "decision_attempt"]
    with _case("E2-ttl-expiry", {
            "control_packet_bits": CTRL_BITS, "isl_rate_mbps": 1.0,
            "ttl_s": ttl, "advertise_interval_s": 2.0, "duration_s": 3.0,
            "expected_expiry_at_s": expiry, "advert_seq": seq,
            "rows": [{"pid": 99, "emit_time_s": 0.0},
                     {"pid": 1, "emit_time_s": 2.4},
                     {"pid": 2, "emit_time_s": 2.6}]}) as case:
        case.event("audit_generated", _audit_rows(
            timeline, "ctrl_advert_generated", origin=1, seq=seq))
        case.event("audit_arrived", _audit_rows(
            timeline, "ctrl_advert_arrived", origin=1, sat=0, seq=seq))
        case.event("decision_attempts", [_attempt_view(mark)
                                        for mark in attempts])
        case.event("decisions", decisions)
        case.event("cache_sat0", res["caches"][0])
        case.event("fates", res["fates"])

        entry = res["caches"][0][1]
        case.check("the entry is received before the expiry instant",
                   t_rx < expiry, received_at=t_rx, expiry=expiry)
        case.check("the entry is expired at the stop instant",
                   entry["valid"] is False, valid=entry["valid"],
                   aoi=entry["aoi"], ttl=ttl)
        pid1 = [row_ for row_ in decisions
                if row_["pid"] == 1 and row_["kind"] == "forward"]
        case.check("a packet decided while the entry is valid still uses it",
                   bool(pid1) and pid1[0]["t"] < expiry
                   and pid1[0]["chosen"] == "E",
                   forward=pid1[0] if pid1 else None, expiry=expiry)
        late_attempts = [mark for mark in attempts if mark.get("pid") == 2]
        case.check("after expiry the state cannot be read (no_info holds)",
                   bool(late_attempts)
                   and all(mark["at"] > expiry for mark in late_attempts)
                   and all(mark["route_status"] == "no_info"
                           and mark["action"] == "hold"
                           for mark in late_attempts),
                   attempts=[{"at": mark["at"],
                              "route_status": mark["route_status"]}
                             for mark in late_attempts],
                   expiry=expiry)
        case.check("the expired packet never forwards and settles in system",
                   not [row_ for row_ in decisions if row_["pid"] == 2]
                   and res["fates"][2] == "IN_SYSTEM_AT_STOP",
                   decisions=[row_ for row_ in decisions
                              if row_["pid"] == 2],
                   fate=res["fates"][2])


def test_E3_control_congestion_increases_received_at():
    """A data packet in service delays the advertisement by exactly its
    residual transmission time: received_at moves by that amount."""
    interval = 3.0
    bits = 3_000_000
    data_service_s = bits / 1e6
    t_m_expected = interval
    warm = row(99, 0.0, A, B, bits=CTRL_BITS)
    data_row = row(7, 2.2, B, A, bits=bits)

    def _one(rows):
        cfg = _cfg(5.9, isl_mbps=1.0, control=_control_cfg(interval),
                   routing={"policy": "oracle"})
        timeline = []
        res = _run(cfg, rows, timeline=timeline, audit=True)
        return res, timeline

    idle_res, idle_timeline = _one([warm])
    cong_res, cong_timeline = _one([warm, data_row])
    idle_m, idle_rx, idle_seq = _advert_chain(idle_timeline)
    cong_m, cong_rx, cong_seq = _advert_chain(cong_timeline)
    ctrl = _ctrl_windows(cong_timeline, ISL_1_0)
    data_windows = [window for window in cong_res["link_service_windows"]
                    if window["stage"] == "isl" and window["pid"] == 7]
    with _case("E3-congestion-delays-state", {
            "control_packet_bits": CTRL_BITS, "isl_rate_mbps": 1.0,
            "advertise_interval_s": interval,
            "data_packet_bits": bits, "data_service_s": data_service_s,
            "t_m_expected_s": t_m_expected,
            "idle_advert_seq": idle_seq, "congested_advert_seq": cong_seq}) as case:
        case.event("idle_generated", _audit_rows(
            idle_timeline, "ctrl_advert_generated", origin=1, seq=idle_seq))
        case.event("idle_arrived", _audit_rows(
            idle_timeline, "ctrl_advert_arrived", origin=1, sat=0,
            seq=idle_seq))
        case.event("congested_generated", _audit_rows(
            cong_timeline, "ctrl_advert_generated", origin=1, seq=cong_seq))
        case.event("congested_arrived", _audit_rows(
            cong_timeline, "ctrl_advert_arrived", origin=1, sat=0,
            seq=cong_seq))
        case.event("congested_control_windows", [
            {"origin": key[0], "seq": key[1], "start": value["start"],
             "finish": value["finish"]}
            for key, value in sorted(ctrl.items())])
        case.event("congested_data_windows", data_windows)
        case.event("caches_idle", idle_res["caches"][0])
        case.event("caches_congested", cong_res["caches"][0])

        case.check("both runs generate the same advertisement at t=3",
                   _same(idle_m, t_m_expected, 1e-12)
                   and _same(cong_m, t_m_expected, 1e-12),
                   idle_t_m=idle_m, congested_t_m=cong_m,
                   expected=t_m_expected)
        case.check("congestion puts a data packet in service across t_m",
                   bool(data_windows)
                   and data_windows[0]["start"] < t_m_expected
                   < data_windows[0]["end"],
                   data_window=[(window["start"], window["end"])
                                for window in data_windows],
                   t_m=t_m_expected)
        control_after = [value for value in ctrl.values()
                         if value["start"] >= data_windows[0]["end"] - 1e-12]
        case.check("the advertisement waits for the data packet "
                   "(non-preemptive) and starts when it completes",
                   bool(control_after)
                   and _same(min(value["start"] for value in control_after),
                             data_windows[0]["end"], 1e-9),
                   first_control_start=(min(
                       (value["start"] for value in control_after),
                       default=None)),
                   data_end=data_windows[0]["end"])
        congested_entry = cong_res["caches"][0][1]
        ctrl_duration = CTRL_BITS / 1e6
        expected_rx = (data_windows[0]["end"] + ctrl_duration
                       + _prop_s(1000.0))
        case.check(
            "congested received_at == data_end + T_tx_ctrl + T_prop",
            _same(congested_entry["received_at"], expected_rx, 1e-9),
            expected=expected_rx,
            actual=congested_entry["received_at"])
        case.check(
            "the idle and congested runs deliver the SAME advertisement, so "
            "the arrival difference is pure link congestion",
            _same(congested_entry["generated_at"], idle_res["caches"][0][1]
                  ["generated_at"], 1e-12),
            idle_generated_at=idle_res["caches"][0][1]["generated_at"],
            congested_generated_at=congested_entry["generated_at"])
        delta = congested_entry["received_at"] - idle_res["caches"][0][1][
            "received_at"]
        case.check("congestion increases received_at by exactly the residual "
                   "data service time",
                   delta > 0
                   and _same(delta, data_windows[0]["end"] - t_m_expected, 1e-9),
                   expected=data_windows[0]["end"] - t_m_expected,
                   actual=delta)


# ==========================================================================
# F. decision computation consumes simulated time
# ==========================================================================
def test_F1_network_keeps_running_during_a_non_zero_computation():
    """compute=0 commits synchronously; compute>0 lets the network advance."""
    control_cfg = _control_cfg(1.0)
    delay = 2.0
    rows = [row(99, 0.0, A, B, bits=CTRL_BITS),
            row(1, 2.0, A, B, bits=CTRL_BITS),
            row(2, 3.0, A, B, bits=CTRL_BITS)]
    modes = ("refresh", "frozen")
    with _case("F1-compute-time-advances-clock", {
            "compute_delay_s": delay, "row_emit_s": [0.0, 2.0, 3.0],
            "control_advertise_interval_s": 1.0,
            "observation_modes": list(modes)}) as case:
        # ---- compute=0: the two instants coincide for every decision ----
        zero_sink = []
        _zero = _run(_cfg(12.0, isl_mbps=1.0, control=control_cfg,
                          execution={"compute_delay_s": 0.0}),
                     rows, sink=zero_sink, audit=True)
        zero_view = [_decision_view(entry) for entry in zero_sink]
        case.event("zero_delay_decisions", zero_view)
        case.check("compute=0 keeps every decision synchronous",
                   all(row_["t"] == row_["t_decision_start"]
                       for row_ in zero_view),
                   decisions=zero_view,
                   max_interval=max((row_["t"] - row_["t_decision_start"]
                                     for row_ in zero_view), default=None))

        for mode in modes:
            sink, timeline = [], []
            res = _run(_cfg(12.0, isl_mbps=1.0, control=control_cfg,
                            execution={"compute_delay_s": delay,
                                       "decision_observation_mode": mode}),
                       rows, sink=sink, timeline=timeline, audit=True)
            targets = [entry for entry in sink
                       if entry["pid"] == 1 and entry["kind"] == "forward"]
            case.check(f"{mode}: the target packet commits after a computation",
                       bool(targets), mode=mode)
            target = targets[0]
            start, commit = target["t_decision_start"], target["t"]
            inside = [mark for mark in timeline if start < mark["at"] < commit]
            milestones = sorted({mark["milestone"] for mark in inside})
            case.event(f"{mode}_target_decision", _decision_view(target))
            case.event(f"{mode}_events_inside_compute", [
                {key: mark.get(key) for key in
                 ("milestone", "at", "pid", "origin", "seq", "link_id")}
                for mark in inside])
            case.check(f"{mode}: commit == start + compute_delay",
                       _same(commit - start, delay, 1e-9),
                       expected=delay, actual=commit - start)
            case.check(f"{mode}: control advertisements keep arriving during "
                       f"the computation",
                       "ctrl_advert_arrived" in milestones, milestones=milestones)
            case.check(f"{mode}: link service keeps running during the "
                       f"computation",
                       "service_start" in milestones
                       and "service_finish" in milestones,
                       milestones=milestones)
            case.check(f"{mode}: queues keep mutating during the computation",
                       "queue_enter" in milestones, milestones=milestones)
            case.check(f"{mode}: another packet is being processed during the "
                       f"computation", any(mark.get("pid") == 2
                                           for mark in inside),
                       pids=sorted({mark.get("pid") for mark in inside
                                    if mark.get("pid") is not None}))
            if mode == "frozen":
                observation = target["observation_at_start"]
                case.check("frozen: the observation is taken at compute start",
                           observation.get("t_observed") == start
                           and observation.get("source")
                           == "frozen_snapshot_before_compute",
                           t_observed=observation.get("t_observed"),
                           t_decision_start=start,
                           source=observation.get("source"))
                case.check(
                    "frozen: the committed decision is based on the start "
                    "observation, not on the post-compute state",
                    observation.get("t_observed") < commit,
                    t_observed=observation.get("t_observed"), commit=commit)


FLIP_S = 6.0
FLIP_TARGET_EMIT = 5.0
FLIP_BITS = 8_000_000
FLIP_DELAY = 2.0
#: The 8 Mbit packet is emitted at 5.0 onto the fixture's 1 Gbps uplink with a
#: 600 km slant range, so its observation instant is 5.0 + 8e6/1e9 + 600/c and
#: its commit instant is that plus the 2 s computation.  The ISL availability
#: flips at 6.0, strictly inside [start, commit].
FLIP_START = FLIP_TARGET_EMIT + FLIP_BITS / 1e9 + _prop_s(600.0)
FLIP_COMMIT = FLIP_START + FLIP_DELAY


def _flip_geometry(up_before: bool) -> StaticGeometry:
    """The only ISL direction is available on exactly one side of FLIP_S."""

    def neighbors_at(sat, dirs, t):
        up = (t < FLIP_S) if up_before else (t >= FLIP_S)
        return dict(LINE_NB.get(sat, {})) if up else {}

    return _line_geometry(isl_changes=[FLIP_S],
                          neighbors_at_fn=neighbors_at)


def _mode_run(mode, geometry, delay=FLIP_DELAY):
    # oracle routing + no control plane, exactly the negative-control fixture
    # of test_frozen_observation.py: the only lever is ISL availability, so a
    # difference between frozen and refresh cannot come from discovery.
    cfg = _cfg(12.0, routing={"policy": "oracle"},
               execution={"compute_delay_s": delay,
                          "decision_observation_mode": mode},
               access={"uplink_rate_mbps": 1000.0,
                       "downlink_rate_mbps": 1000.0,
                       "idle_release_s": 100.0, "slot_lease_s": 100.0},
               links={"isl_rate_mbps": 10.0})
    rows = [row(99, 0.0, A, B, bits=FLIP_BITS),
            row(1, FLIP_TARGET_EMIT, A, B, bits=FLIP_BITS)]
    sink, timeline = [], []
    res = _run(cfg, rows, geometry=geometry, sink=sink,
               timeline=timeline, audit=True)
    return res, sink, timeline


def _flip_run(mode, up_before, delay=FLIP_DELAY):
    return _mode_run(mode, _flip_geometry(up_before), delay=delay)


def _flip_marks(timeline, milestone, pid=1):
    return [mark for mark in _marks(timeline, milestone, pid=pid)]


def test_F2_frozen_observes_at_start_refresh_observes_at_commit():
    """Static state: the two modes agree on the action but not on WHICH state
    the decision was based on."""
    with _case("F2-frozen-vs-refresh-observation", {
            "compute_delay_s": FLIP_DELAY,
            "target_emit_s": FLIP_TARGET_EMIT,
            "expected_observation_start_s": FLIP_START,
            "expected_commit_s": FLIP_COMMIT}) as case:
        results = {}
        for mode in ("frozen", "refresh"):
            # truly static geometry: the two modes must agree on the action
            res, sink, timeline = _mode_run(mode, _line_geometry())
            forwards = [entry for entry in sink
                        if entry["pid"] == 1 and entry["kind"] == "forward"]
            case.check(f"{mode}: the target forwards (static geometry)",
                       bool(forwards), decisions=[_decision_view(entry)
                                                  for entry in sink
                                                  if entry["pid"] == 1])
            target = forwards[0]
            results[mode] = _decision_view(target)
            case.event(f"{mode}_target_decision", results[mode])
            case.check(f"{mode}: compute interval is exactly the delay",
                       _same(target["t"] - target["t_decision_start"],
                             FLIP_DELAY, 1e-9),
                       expected=FLIP_DELAY,
                       actual=target["t"] - target["t_decision_start"])
        frozen, refresh = results["frozen"], results["refresh"]
        case.check("both modes commit the same action",
                   frozen["chosen"] == refresh["chosen"] == "E",
                   frozen=frozen["chosen"], refresh=refresh["chosen"])
        case.check("frozen's observation instant is the computation start",
                   _same(frozen["observation_t_observed"],
                         frozen["t_decision_start"], 1e-12)
                   and frozen["observation_source"]
                   == "frozen_snapshot_before_compute",
                   t_observed=frozen["observation_t_observed"],
                   t_decision_start=frozen["t_decision_start"],
                   source=frozen["observation_source"])
        case.check("refresh's observation instant is the commit instant",
                   _same(refresh["observation_t_observed"],
                         refresh["t"], 1e-12)
                   and refresh["observation_source"] == "commit_time_state",
                   t_observed=refresh["observation_t_observed"],
                   commit=refresh["t"], source=refresh["observation_source"])
        case.check("both observation instants are separated by the delay",
                   _same(refresh["observation_t_observed"]
                         - frozen["observation_t_observed"], FLIP_DELAY, 1e-9),
                   expected=FLIP_DELAY,
                   actual=(refresh["observation_t_observed"]
                           - frozen["observation_t_observed"]))


def test_F3_frozen_action_illegal_at_commit_is_rejected_and_parked():
    """ISL up at compute start, down at commit: frozen refuses its own action."""
    res, sink, timeline = _flip_run("frozen", up_before=True)
    rejected = _flip_marks(timeline, "commit_rejected")
    with _case("F3-frozen-commit-rejected", {
            "compute_delay_s": FLIP_DELAY, "target_emit_s": FLIP_TARGET_EMIT,
            "flip_at_s": FLIP_S, "observation_while_link_up": True,
            "expected_commit_s": FLIP_COMMIT}) as case:
        case.event("timeline_marks", [
            {key: mark.get(key) for key in
             ("milestone", "at", "pid", "action", "reason", "inferred_at",
              "t_observed", "status")}
            for mark in timeline
            if mark.get("pid") == 1
            and mark["milestone"] in ("commit_rejected", "hold",
                                      "frozen_inferred_hold")])
        case.event("decisions_pid1", [_decision_view(entry) for entry in sink
                                      if entry["pid"] == 1])
        case.event("fates", res["fates"])

        case.check("exactly one rejected commit is recorded", len(rejected) == 1,
                   rejected=[{"action": mark["action"],
                              "reason": mark["reason"],
                              "at": mark["at"]} for mark in rejected])
        case.check("the rejected action is the one inferred while the link "
                   "was up", rejected[0]["action"] == "E"
                   and rejected[0]["reason"] == "action_no_longer_legal",
                   action=rejected[0]["action"],
                   reason=rejected[0]["reason"])
        case.check("the refusal is attributed to the computation start",
                   _same(rejected[0]["inferred_at"], FLIP_START, 1e-9),
                   expected=FLIP_START, actual=rejected[0]["inferred_at"])
        case.check("the refusal lands at the commit instant",
                   _same(rejected[0]["at"], FLIP_COMMIT, 1e-9),
                   expected=FLIP_COMMIT, actual=rejected[0]["at"])
        case.check("a rejected action is NOT a commit (no decision row)",
                   [entry for entry in sink if entry["pid"] == 1] == [],
                   decisions=[_decision_view(entry) for entry in sink
                              if entry["pid"] == 1])
        case.check("the packet is parked (hold milestone at commit)",
                   any(_same(mark["at"], FLIP_COMMIT, 1e-9)
                       for mark in _flip_marks(timeline, "hold")),
                   holds=[mark["at"] for mark in
                          _flip_marks(timeline, "hold")],
                   expected_at=FLIP_COMMIT)
        case.check("the packet is not silently re-solved into a delivery",
                   res["fates"][1] == "IN_SYSTEM_AT_STOP",
                   fate=res["fates"][1])

        # contrast: refresh re-solves against commit-time state
        _res_r, sink_r, timeline_r = _flip_run("refresh", up_before=True)
        case.check("refresh has no rejected-action concept: it re-solves "
                   "instead",
                   not _flip_marks(timeline_r, "commit_rejected")
                   and not [entry for entry in sink_r if entry["pid"] == 1],
                   rejected_marks=[mark["at"] for mark in
                                   _flip_marks(timeline_r, "commit_rejected")],
                   decisions=[_decision_view(entry) for entry in sink_r
                              if entry["pid"] == 1])


def test_F4_frozen_hold_inferred_at_start_is_not_reoptimised_at_commit():
    """ISL down at compute start, up at commit: frozen keeps its hold while
    refresh commits a forward that was unknowable when the computation began."""
    res, sink, timeline = _flip_run("frozen", up_before=False)
    holds = _flip_marks(timeline, "frozen_inferred_hold")
    with _case("F4-frozen-hold-vs-refresh-forward", {
            "compute_delay_s": FLIP_DELAY, "target_emit_s": FLIP_TARGET_EMIT,
            "flip_at_s": FLIP_S, "observation_while_link_down": True,
            "expected_observation_start_s": FLIP_START,
            "expected_commit_s": FLIP_COMMIT}) as case:
        case.event("timeline_marks", [
            {key: mark.get(key) for key in
             ("milestone", "at", "pid", "inferred_at", "t_observed", "status")}
            for mark in timeline
            if mark.get("pid") == 1
            and mark["milestone"] in ("frozen_inferred_hold", "hold",
                                      "commit_rejected")])
        case.event("frozen_decisions_pid1", [
            _decision_view(entry) for entry in sink if entry["pid"] == 1])
        case.event("fates", res["fates"])

        case.check("the frozen observation inferred a hold at t0",
                   bool(holds)
                   and _same(holds[0]["inferred_at"], FLIP_START, 1e-9)
                   and holds[0]["status"] == "ok",
                   holds=[{"inferred_at": mark["inferred_at"],
                           "status": mark["status"]} for mark in holds],
                   expected_start=FLIP_START)
        case.check("the hold is committed at the commit instant, not before",
                   _same(holds[0]["at"], FLIP_COMMIT, 1e-9),
                   expected=FLIP_COMMIT, actual=holds[0]["at"])
        no_commit_at_flip = [entry for entry in sink
                             if entry["pid"] == 1
                             and _same(entry["t"], FLIP_COMMIT, 1e-9)]
        case.check("frozen does not commit a forward at the early commit "
                   "instant", no_commit_at_flip == [],
                   decisions=[_decision_view(entry)
                              for entry in no_commit_at_flip])
        forwards = [entry for entry in sink
                    if entry["pid"] == 1 and entry["kind"] == "forward"]
        case.check("frozen retries later and forwards only from a genuinely "
                   "fresh observation",
                   bool(forwards)
                   and forwards[0]["t_decision_start"] > FLIP_COMMIT,
                   forward=_decision_view(forwards[0]) if forwards else None,
                   commit=FLIP_COMMIT)

        _res_r, sink_r, timeline_r = _flip_run("refresh", up_before=False)
        refresh_forward = [entry for entry in sink_r
                           if entry["pid"] == 1 and entry["kind"] == "forward"]
        case.event("refresh_decisions_pid1", [
            _decision_view(entry) for entry in sink_r if entry["pid"] == 1])
        case.check("refresh DOES forward at the same commit instant using the "
                   "newly available state",
                   bool(refresh_forward)
                   and _same(refresh_forward[0]["t"], FLIP_COMMIT, 1e-9)
                   and refresh_forward[0]["chosen"] == "E",
                   forward=_decision_view(refresh_forward[0])
                   if refresh_forward else None,
                   expected_commit=FLIP_COMMIT)
        case.check("the refresh activity span contains the geometry flip "
                   "(its information did not exist at start)",
                   refresh_forward[0]["t_decision_start"] < FLIP_S
                   < refresh_forward[0]["t"],
                   t_decision_start=refresh_forward[0]["t_decision_start"],
                   flip=FLIP_S, commit=refresh_forward[0]["t"])
        case.check("frozen and refresh disagree exactly at the frozen commit",
                   not [entry for entry in sink
                        if entry["pid"] == 1
                        and _same(entry["t"], FLIP_COMMIT, 1e-9)]
                   and bool(refresh_forward),
                   frozen_commits_at_flip=len(no_commit_at_flip),
                   refresh_forwards=len(refresh_forward))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
