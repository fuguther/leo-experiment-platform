"""T1-COMPLETE P4: four-group offline state-time comparison at one branch.

WHAT THIS DRIVER DOES
---------------------
At one frozen forward decision it
  1. runs the branch with EVERY legal candidate forced, ONE AT A TIME,
     each from t=0 and each verified to reach the same branch point, so a
     candidate outcome is measured on that candidate own trajectory --
     never copied out of the chosen branch;
  2. scores the four online state-time arms (stale / now / common /
     candidate) with the shared pure scorer in leo_sim.time_alignment;
  3. reports each candidate final fate, its normalized loss, and the
     regret of every arm;
  4. keeps the mirror-image oracle (pick the best final outcome) strictly
     inside the offline evaluator: it is never a policy input.

INFORMATION PERMISSION
----------------------
The policy half sees only an ObservationSnapshot built from the branch
observation record, which the kernel wrote at t0.  The future (fates,
deliveries, peer truth) is read afterwards by the evaluator.  A candidate
whose realised egress differs from the predicted one is reported as
resource_mismatch and is NOT counted as a same-resource prediction error.

Usage
-----
    python3 -m CODE.experiment_platform.time_alignment_compare \
        --config CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml \
        --decision-id 2 --out out/t1/time-alignment.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from CODE.experiment_platform import artifact_identity, t1_stats
from CODE.experiment_platform import scripted_scenarios
from CODE.leo_sim import config as config_mod, counterfactual, decision_ledger
from CODE.leo_sim import kernel, time_alignment as _ta, trace as trace_mod

ta = _ta

SCHEMA = "time-alignment-compare/v1"


class CompareError(RuntimeError):
    """The comparison could not be driven; nothing was published."""


# ----------------------------------------------------------------- helpers
def _rows_digest(rows) -> str:
    payload = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trace_identity(resolved, root: Path):
    work = Path(tempfile.mkdtemp(prefix="ta-compare-", dir=str(root)))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=resolved["config"]["execution"]["max_packets"])
        digest = (manifest.get("__trace_sha256")
                  or manifest.get("trace_sha256") or _rows_digest(rows))
    finally:
        for leftover in sorted(work.glob("*"), reverse=True):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass
    return rows, digest


def capture(result, sink, timeline):
    return {"result": result, "decision_rows": sink, "timeline_rows": timeline}


def _outcome(result, pid):
    delivery = result["deliveries"].get(pid)
    emitted = None
    for ev in result["packet_events"]:
        if ev.get("kind") == "packet_emitted" and ev.get("pid") == pid:
            emitted = ev["at"]
            break
    delivered_at = (delivery or {}).get("delivered_at")
    return {
        "fate": result["fates"].get(pid),
        "delivered": delivered_at is not None,
        "delivered_at": delivered_at,
        "emitted_at": emitted,
        "delay_s": (None if delivered_at is None or emitted is None
                    else float(delivered_at) - float(emitted)),
        "path": (delivery or {}).get("path"),
    }


def _compute_interval(timeline, pid, t_request):
    """(wait, service) for the target decision request, from its events."""
    req = [m for m in timeline if m.get("milestone") == "compute_request"
           and m.get("pid") == pid
           and abs(float(m["at"]) - float(t_request)) <= 1e-12]
    start = [m for m in timeline if m.get("milestone") == "compute_start"
             and m.get("pid") == pid]
    finish = [m for m in timeline if m.get("milestone") == "compute_finish"
              and m.get("pid") == pid]
    wait = service = 0.0
    if req and start:
        wait = max(0.0, float(start[0]["at"]) - float(req[0]["at"]))
    if start and finish:
        service = max(0.0, float(finish[0]["at"]) - float(start[0]["at"]))
    return wait, service


def _resource_timeline(timeline, resource_link_id, t_after):
    """Queue/service rows for ONE named resource from ONE branch.

    EVENT ORDER IS PRESERVED (the timeline is appended in event order, and rows
    at the same instant are ordered by it).  Sorting by instant would destroy
    the tie-break that says which of two same-instant enqueues came first.
    """
    return [m for m in timeline
            if m.get("link_id") == resource_link_id
            and float(m["at"]) >= float(t_after)
            and m.get("milestone") in ("queue_enter", "service_start",
                                       "service_finish",
                                       # B1: a control packet that left the
                                       # queue without being served must stay
                                       # visible, otherwise the rebuild counts
                                       # it as backlog forever
                                       "ctrl_drop")]


def resource_work_ahead(resource_rows, bits_by_pid, instant, exclude_pid):
    """Work AHEAD of one packet on one named resource, at one instant.

    Built from the branch's own event stream, in EVENT ORDER (the timeline is
    appended in event order, and rows at the same instant are ordered by it).

    Components, reported SEPARATELY and never silently folded:
      queued_data_ahead_bits       data waiting at the instant that is ahead of
                                   the excluded packet in FIFO order
      queued_ctrl_ahead_bits       control backlog at the instant; control has
                                   non-preemptive priority, so it is ahead
      in_service_remaining_bits    remaining bits of the packet in service,
                                   from its own service window (None if unknown)
      fifo_behind_bits             data that entered AFTER the excluded packet;
                                   it is behind it and does NOT delay it
      excluded_target_bits         the excluded packet's own contribution

    The kernel measures work ahead BEFORE inserting a packet, so at that packet
    own enqueue instant this reconstruction is EXACT:
        queued_data_ahead + queued_ctrl_ahead + in_service_remaining
        == backlog_before.queued_bits_before
           + backlog_before.in_service_remaining_bits_before
    """
    rows = list(resource_rows)
    if not rows:
        return {
            "instant": float(instant), "available": False,
            "reason": "resource_never_appears_in_this_branch",
            "queued_data_ahead_bits": None, "queued_ctrl_ahead_bits": None,
            "in_service_remaining_bits": None, "fifo_behind_bits": None,
            "excluded_target_bits": None, "work_ahead_bits": None,
            "complete": False, "unknown": ["resource_absent"],
            "components_method": {}, "basis": None,
        }
    target_index = None
    # B2: a control row carries pid=None, so an unset exclude_pid must
    # never match it -- otherwise the first control enqueue became "the
    # target" and every later data packet was filed as FIFO-behind it.
    if exclude_pid is not None:
        for i, row in enumerate(rows):
            if (row.get("milestone") == "queue_enter"
                    and row.get("packet_kind") != "control"
                    and row.get("pid") == exclude_pid):
                target_index = i
                break
    target_enqueue_at = (None if target_index is None
                         else float(rows[target_index]["at"]))

    def _bits(pid, row=None):
        # the row own bits win: control rows carry no pid at all
        if row is not None and row.get("bits") is not None:
            return float(row["bits"])
        value = bits_by_pid.get(pid)
        return None if value is None else float(value)

    starts = []
    finishes = []
    for i, row in enumerate(rows):
        if row.get("milestone") == "service_start":
            starts.append((i, float(row["at"]), row.get("pid")))
        elif row.get("milestone") == "service_finish":
            finishes.append((i, float(row["at"]), row.get("pid")))

    def _window_finish(index, pid, row):
        # S1-B/C: a control packet has no pid, so it is matched by kind; a
        # link serves one packet at a time, so the next control finish after
        # a control start IS that packet finish.
        is_ctrl = row.get("packet_kind") == "control"
        for j, f_at, f_pid in finishes:
            if j <= index:
                continue
            if is_ctrl:
                if rows[j].get("packet_kind") == "control":
                    return f_at
            elif f_pid == pid:
                return f_at
        return None

    # a control row carries pid=None, so an unset exclude_pid must never
    # match it (S1-B/C: that turned a control service into "the excluded
    # packet is already served")
    target_served_by_instant = (
        exclude_pid is not None
        and any(pid == exclude_pid and at <= instant + 1e-12
                for _i, at, pid in finishes))

    queued_data_ahead = 0.0
    fifo_behind = 0.0
    target_bits = 0.0
    ahead_unknown = []
    ctrl_ahead = None
    ctrl_method = None
    last_backlog = None
    in_service_remaining = None
    in_service_method = None
    basis = None
    target_in_service_at_instant = False

    for i, row in enumerate(rows):
        if row.get("milestone") != "queue_enter":
            continue
        at = float(row["at"])
        if at > instant + 1e-12:
            break
        pid = row.get("pid")
        backlog = row.get("backlog_before")
        if isinstance(backlog, dict):
            last_backlog = backlog
        if row.get("packet_kind") == "control":
            # control traffic has its own ledger; it is never a data
            # FIFO member and never the excluded data packet
            continue
        if exclude_pid is not None and pid == exclude_pid:
            if i == target_index:
                basis = backlog
            bits = _bits(pid)
            target_bits = 0.0 if bits is None else bits
            continue
        started = any(s_at <= instant + 1e-12 and s_pid == pid
                      for _j, s_at, s_pid in starts)
        if started:
            continue
        bits = _bits(pid)
        if bits is None:
            ahead_unknown.append("queued_bits_unknown_pid_%s" % (pid,))
            continue
        if target_index is None:
            # the excluded packet has not entered yet: everything queued is
            # ahead of it
            queued_data_ahead += bits
        elif i < target_index:
            queued_data_ahead += bits
        else:
            fifo_behind += bits

    serving = None
    for i, at, pid in starts:
        if at <= instant + 1e-12:
            serving = (i, at, pid)
    if serving is not None:
        i, s_at, s_pid = serving
        finish_at = _window_finish(i, s_pid, rows[i])
        if exclude_pid is not None and s_pid == exclude_pid:
            target_in_service_at_instant = True
            in_service_remaining = 0.0
            in_service_method = "packet_in_service_is_the_excluded_packet"
        elif finish_at is None:
            # The timeline cannot close this service window: a long service
            # may still be running at the recorded horizon.  At the excluded
            # packet OWN enqueue instant the kernel handed us the
            # authoritative backlog, so use its in-service residual instead
            # of reporting unknown.  Review S1-A measured an 86% shortfall
            # here while the correct number sat unused in rec["basis"].
            residual = None
            if (basis is not None and target_enqueue_at is not None
                    and abs(float(instant) - target_enqueue_at) <= 1e-12):
                residual = basis.get("in_service_remaining_bits_before")
            if residual is None:
                in_service_remaining = None
                in_service_method = "service_window_unfinished"
            else:
                in_service_remaining = float(residual)
                in_service_method = (
                    "kernel backlog_before in_service residual at the "
                    "excluded packet own enqueue instant")
        else:
            bits = _bits(s_pid, rows[i])
            duration = finish_at - s_at
            if bits is None or duration <= 0:
                in_service_remaining = None
                in_service_method = "service_window_bits_or_duration_unknown"
            else:
                fraction = max(0.0, min(1.0, (finish_at - instant) / duration))
                in_service_remaining = bits * fraction
                in_service_method = "constant_rate_residual_of_service_window"

    # S1-B/C: control traffic has its own timeline rows on the same named
    # resource now, so the control backlog can be MEASURED instead of
    # inherited from the last DATA enqueue -- which went stale the moment a
    # control packet arrived after it.  Traces without control rows keep the
    # old estimator so historical artifacts still reconstruct.
    ctrl_rows = [r for r in rows if r.get("packet_kind") == "control"]
    if ctrl_rows:
        # B1: each control packet is matched by its STABLE IDENTITY and
        # removed once when it leaves the queue (service_start, or a
        # drop such as CONTROL_EXPIRED).  A running total minus a
        # running total would need a max(0, ...) clamp to hide double
        # deductions; identity matching cannot double count at all.
        control_entered = {}
        control_left = set()
        # the identity tuple must have ONE shape within a window, or a row
        # carrying the generation stamp would fail to cancel its own row
        # that does not: use the full identity when every row has it, and
        # fall back to (origin, seq) for traces/fixtures that do not
        full_identity = all(
            r.get("control_generated_at") is not None for r in ctrl_rows)
        for r in ctrl_rows:
            if float(r["at"]) > instant + 1e-12:
                continue
            key = ((r.get("origin"), r.get("seq"),
                    r.get("control_generated_at"))
                   if full_identity
                   else (r.get("origin"), r.get("seq")))
            if r.get("milestone") == "queue_enter":
                amount = (0.0 if r.get("bits") is None
                          else float(r["bits"]))
                control_entered[key] = control_entered.get(key, 0.0) + amount
            elif r.get("milestone") in ("service_start", "ctrl_drop",
                                        "queue_drop"):
                # started service => already dequeued; dropped => never served.
                # Either way it no longer delays the target.
                control_left.add(key)
        ctrl_ahead = sum(bits for key, bits in control_entered.items()
                         if key not in control_left)
        ctrl_method = ("control timeline on the named resource: enqueued "
                       "bits per control identity, minus identities that "
                       "started service or were dropped at or before the "
                       "instant")
    elif last_backlog is not None:
        value = last_backlog.get("queued_ctrl_bits_before")
        if value is not None:
            ctrl_ahead = float(value)
            ctrl_method = ("queued_ctrl_bits_before of the last data enqueue "
                           "at or before the instant (no control timeline in "
                           "this trace)")
        else:
            ctrl_method = "last_backlog_has_no_ctrl_breakdown"
    else:
        ctrl_method = "no_data_enqueue_at_or_before_the_instant"

    if target_served_by_instant:
        return {
            "instant": float(instant), "available": True,
            "reason": "excluded_packet_already_served",
            "queued_data_ahead_bits": 0.0, "queued_ctrl_ahead_bits": 0.0,
            "in_service_remaining_bits": 0.0,
            "fifo_behind_bits": fifo_behind,
            "excluded_target_bits": target_bits,
            "work_ahead_bits": 0.0, "complete": True, "unknown": [],
            "components_method": {
                "queued_data_ahead_bits": "excluded_packet_already_served",
                "in_service_remaining_bits": "excluded_packet_already_served",
                "queued_ctrl_ahead_bits": ctrl_method},
            "basis": basis,
            "target_in_service_at_instant": target_in_service_at_instant,
        }

    components = {
        "queued_data_ahead_bits": queued_data_ahead,
        "queued_ctrl_ahead_bits": ctrl_ahead,
        "in_service_remaining_bits": in_service_remaining,
    }
    unknown_components = sorted(k for k, v in components.items() if v is None)
    total = sum(v for v in components.values() if v is not None)
    return {
        "instant": float(instant),
        "available": not unknown_components,
        "reason": (None if not unknown_components
                   else "unknown_components:" + ",".join(unknown_components)),
        "queued_data_ahead_bits": queued_data_ahead,
        "queued_ctrl_ahead_bits": ctrl_ahead,
        "in_service_remaining_bits": in_service_remaining,
        "fifo_behind_bits": fifo_behind,
        "excluded_target_bits": target_bits,
        # an unknown component must NOT be folded into a number a caller
        # can mistake for the truth (review S1-D: this used to return the
        # partial sum, so "unknown" was readable as 0)
        "work_ahead_bits": (None if unknown_components else float(total)),
        "work_ahead_bits_known_components_sum": float(total),
        "complete": not unknown_components and not ahead_unknown,
        "unknown": ahead_unknown,
        "components_method": {
            "queued_data_ahead_bits":
                "FIFO order: enqueued at/before the instant, not yet started, "
                "ahead of the excluded packet",
            "queued_ctrl_ahead_bits": ctrl_method,
            "in_service_remaining_bits": in_service_method},
        "basis": basis,
        "target_in_service_at_instant": target_in_service_at_instant,
    }


def truth_at_instant(resource_timeline_rows, bits_by_pid, instant,
                     exclude_pid):
    """Compatibility wrapper: the work-ahead total + its components.

    The returned "bits" is resource_work_ahead's total, i.e. queued data ahead
    + priority control ahead + in-service remaining.  It never subtracts the
    excluded packet a second time (the kernel's own pre-insertion measurement
    already excludes it).
    """
    record = resource_work_ahead(resource_timeline_rows, bits_by_pid, instant,
                                 exclude_pid)
    record = dict(record)
    record["bits"] = (record["work_ahead_bits"]
                      if record.get("available") else None)
    if record.get("reason") is not None:
        record.setdefault("missing_reason", record["reason"])
    record["source"] = "own-branch event trace of the named resource"
    return record


def _legacy_truth_at_instant(resource_timeline_rows, bits_by_pid, instant,
                             exclude_pid):
    """Reconstruct the work ahead on a named resource at one instant.

    Built ONLY from that candidate own branch event trace.  The value is the
    last backlog measured at or before the instant, minus the bits that left
    service between that measurement and the instant, minus the target packet
    itself (it is not work ahead of itself).  No final fate, no other
    candidate result and no whole-satellite queue enters.

    Returns a verifiable record: the instant, the resource link, the raw
    measurement it started from and every adjustment applied.
    """
    rows = sorted(resource_timeline_rows, key=lambda m: (float(m["at"]),
                                                         str(m["milestone"])))
    base = None
    for row in rows:
        if float(row["at"]) > instant + 1e-12:
            break
        if row.get("milestone") == "queue_enter":
            backlog = row.get("backlog_before")
            if isinstance(backlog, dict) and backlog.get(
                    "queued_bits_before") is not None:
                base = {
                    "measured_at": float(row["at"]),
                    "queued_bits_before": float(backlog["queued_bits_before"]),
                    "in_service_remaining_bits":
                        backlog.get("in_service_remaining_bits_before"),
                }
    if base is None:
        # No queue_enter at or before the instant.  If the resource has ANY
        # event later, the resource existed and its queue was empty at the
        # instant -- that is a measurement (zero), not a missing value.  If the
        # resource never appears at all, the truth is genuinely unavailable.
        first_event = min((float(r["at"]) for r in rows), default=None)
        if first_event is None:
            return {"bits": None, "available": False,
                    "reason": "resource_never_appears_in_this_branch",
                    "instant": float(instant), "excluded_pid": exclude_pid}
        return {"bits": 0.0, "available": True,
                "reason": "queue_empty_at_instant",
                "instant": float(instant), "excluded_pid": exclude_pid,
                "first_event_at": first_event,
                "source": "own-branch event trace of the named resource"}
    served = 0.0
    served_pids = []
    for row in rows:
        at = float(row["at"])
        if at <= base["measured_at"] + 1e-12 or at > instant + 1e-12:
            continue
        if row.get("milestone") != "service_finish":
            continue
        pid = row.get("pid")
        if pid is None or pid not in bits_by_pid:
            continue
        if pid == exclude_pid:
            continue
        served += float(bits_by_pid[pid])
        served_pids.append(pid)
    value = base["queued_bits_before"] - served
    excluded_self = False
    if exclude_pid is not None:
        for row in rows:
            if row.get("milestone") != "queue_enter":
                continue
            if row.get("pid") != exclude_pid:
                continue
            if float(row["at"]) <= instant + 1e-12:
                excluded_self = True
                value -= float(bits_by_pid.get(exclude_pid, 0.0))
            break
    if value < 0:
        value = 0.0
    return {
        "bits": float(value), "available": True, "reason": None,
        "instant": float(instant), "base": base,
        "served_bits_subtracted": served, "served_pids": sorted(served_pids),
        "excluded_pid": exclude_pid, "excluded_self": excluded_self,
        "source": "own-branch event trace of the named resource",
    }


def _resource_trajectory(timeline, resource_link_id, t_after):
    """Read-only trajectory of the named peer egress, for the oracle side.

    Built from the branch event stream; it never feeds the policy.
    """
    rows = [m for m in timeline
            if m.get("link_id") == resource_link_id
            and float(m["at"]) >= float(t_after)
            and m.get("milestone") in ("queue_enter", "service_start",
                                       "service_finish", "peer_arrival")]
    return [{"at": float(m["at"]), "milestone": m["milestone"],
             "pid": m.get("pid"), "decision_id": m.get("decision_id"),
             "backlog_before": m.get("backlog_before")}
            for m in sorted(rows, key=lambda m: (float(m["at"]),
                                                 str(m.get("milestone"))))]


# ------------------------------------------------------- snapshot building
def _pairs_from(values):
    return {d: float(v) for d, v in values.items()
            if v is not None and isinstance(v, (int, float))
            and not isinstance(v, bool) and math.isfinite(float(v))}


def build_snapshot(row, resolved, arm, common_horizon_s, pkt_bits,
                   compute_wait, compute_service, provenance=(),
                   common_rule=None):
    """Turn the kernel observation record into an online snapshot.

    Only fields the kernel wrote at t0 are read.  A candidate whose target
    resource could not be resolved from t0 information is simply absent
    from the resource map; the scorer then marks it missing and uses the
    shared fallback instead of inventing a value.
    """
    obs = row.get("observation_at_start") or {}
    cand = obs.get("candidate_resources") or {}
    own_q = obs.get("own_queue_bits") or {}
    cfg_ta = resolved["config"]["time_alignment"]
    node_process = float(resolved["config"]["execution"]["node_process_delay_s"])
    # T1-R8: the compute-wait term must come from the pool state KNOWN at the
    # observation instant.  The realised wait is future information and would
    # make the online arms clairvoyant; it is only reported as a diagnostic.
    state = obs.get("compute_state")
    if isinstance(state, dict):
        compute_wait = float(state.get("wait_estimate_s", 0.0))
        compute_service = float(state.get("service_s", compute_service))
    legal = []
    resources = {}
    history = []
    rate = {}
    prop = {}
    peer_process = {}
    remaining = {}
    resource_rate = {}
    terminal_prop = {}
    resource_available = {}
    target_dst = row.get("dst")
    # the legal forward set is the DECISION legal set, not every direction the
    # candidate map resolved; a direction the decision could not take must not
    # enter the comparison
    legal_set = obs.get("legal_directions")
    if legal_set is None:
        legal_set = sorted(cand)
    for direction in sorted(set(str(d) for d in legal_set)):
        cr = cand.get(direction)
        if not isinstance(cr, dict):
            legal.append(direction)  # unresolved: scored as missing
            continue
        if cr.get("status") == "delivered_downlink":
            legal.append(direction)
            if target_dst is None or cr.get("peer") is None:
                continue
            key = ta.ResourceKey(int(cr["peer"]), str(target_dst),
                                 ta.KIND_DOWNLINK)
            resources[direction] = key
            rate[direction] = cr.get(
                "candidate_isl_rate_bps", cr.get("isl_rate_bps"))
            prop[direction] = cr.get("propagation_s")
            peer_process[direction] = node_process
            remaining[direction] = 0.0
            resource_rate[direction] = cr.get("downlink_rate_bps")
            terminal_prop[direction] = cr.get("downlink_propagation_s")
            resource_available[direction] = cr.get("downlink_available")
            records = ((obs.get("neighbours") or {}).get(str(cr["peer"]))
                       or {}).get("advertised_history") or []
            for rec in records:
                terminal = ((rec.get("advertised_downlink_resources") or {})
                            .get(str(target_dst)))
                bits = None if terminal is None else terminal.get("queue_bits")
                if bits is None:
                    continue
                sample_rate = terminal.get("rate_bps")
                history.append(ta.StateSample(
                    key, float(rec["generated_at"]),
                    float(rec["received_at"]), float(bits),
                    None if sample_rate is None else float(sample_rate)))
            continue
        if cr.get("status") != "ok":
            legal.append(direction)  # stays in the set, scored as missing
            continue
        legal.append(direction)
        key = ta.ResourceKey(int(cr["peer"]), str(cr["egress_direction"]),
                             "isl")
        resources[direction] = key
        rate[direction] = cr.get(
            "candidate_isl_rate_bps", cr.get("isl_rate_bps"))
        prop[direction] = cr.get("propagation_s")
        peer_process[direction] = node_process
        remaining[direction] = cr.get("remaining_prop_s")
        # ordered advertisement history for THIS named resource: every arrived
        # advertisement that reported the peer egress the candidate targets.
        # Using only the newest value would silently degrade bounded_linear to
        # hold_last, which is exactly the comparison this task must not fudge.
        peer = cr.get("peer")
        egress = cr.get("egress_direction")
        rate_bps = cr.get("isl_rate_bps")
        records = ((obs.get("neighbours") or {}).get(str(peer)) or {}).get(
            "advertised_history") or []
        for rec in records:
            bits = (rec.get("advertised_isl_queue_bits") or {}).get(egress)
            if bits is None:
                continue
            history.append(ta.StateSample(
                key, float(rec["generated_at"]), float(rec["received_at"]),
                float(bits), None if rate_bps is None else float(rate_bps)))
        if not records:
            measurement = cr.get("measurement")
            if measurement and cr.get("advertised_queue_known"):
                history.append(ta.StateSample(
                    key, float(measurement["generated_at"]),
                    float(measurement["received_at"]),
                    float(cr["advertised_queue_bits"]),
                    None if rate_bps is None else float(rate_bps)))
    # A3: the probe snapshot is built with the CONFIGURED rule, so a
    # fixed_horizon config must carry its horizon here too -- otherwise the
    # snapshot constructor refuses the pair and EVERY fixed-h candidate fails
    # to build a branch at all (measured on the VM: 12/12 branches refused
    # with "fixed_horizon requires a finite common_horizon_s").  The horizon
    # itself is only used to resolve rule-based candidates; for a fixed-h
    # candidate the caller passes the candidate own value.
    if common_horizon_s is None and str(cfg_ta["common_rule"]) == \
            "fixed_horizon":
        common_horizon_s = cfg_ta.get("common_horizon_s")
    return ta.make_snapshot(
        satellite=int(obs.get("sat", row["sat"])),
        snapshot_at=float(row["t_decision_start"]),
        history=tuple(history),
        legal_directions=tuple(sorted(set(legal))),
        resources=resources,
        egress_queue_bits=_pairs_from(
            {d: own_q.get(d, 0.0) for d in resources}),
        link_rate_bps=_pairs_from(rate),
        link_propagation_s=_pairs_from(prop),
        peer_process_s=_pairs_from(peer_process),
        remaining_prop_s=_pairs_from(remaining),
        resource_service_rate_bps=_pairs_from(resource_rate),
        terminal_propagation_s=_pairs_from(terminal_prop),
        resource_available=resource_available,
        compute_wait_s=compute_wait, compute_service_s=compute_service,
        pkt_bits=pkt_bits,
        max_resource_queue_bits=None,
        arm=arm, predictor=str(cfg_ta["predictor"]),
        history_limit=int(cfg_ta["history_limit"]),
        common_horizon_s=common_horizon_s,
        common_rule=str(common_rule or cfg_ta["common_rule"]),
        query_delay_s=float(cfg_ta["query_delay_s"]),
        provenance=tuple(provenance))


def _common_horizon(probe_snapshot, rule):
    """The shared t0+h for the common arm, from candidate ETA offsets.

    median_eta / mean_eta over the candidates; fixed_horizon is supplied by
    the caller from the configured value.
    """
    offsets = []
    for direction in probe_snapshot.legal_directions:
        eta = ta.estimate_eta(probe_snapshot, direction)
        offsets.append(eta.target_at - probe_snapshot.snapshot_at)
    if not offsets:
        return 0.0
    offsets.sort()
    if rule == "mean_eta":
        return float(sum(offsets) / len(offsets))
    mid = len(offsets) // 2
    if len(offsets) % 2:
        return float(offsets[mid])
    return float(0.5 * (offsets[mid - 1] + offsets[mid]))


def score_common_horizon_candidates(probe, resolved, target, pkt_bits,
                                    candidate_specs, losses, per_candidate):
    """Score predeclared shared-query rules on one frozen branch sample.

    The five candidate horizons reuse this target's exact t0 observation and
    the exact same independently replayed per-direction outcome table.  Only
    the common query instant/rule changes; no branch is rerun per horizon.
    """
    observation_digest = _rows_digest(
        target.get("observation_at_start") or {})
    outcome_digest = _rows_digest({
        direction: {"valid": bool(item.get("valid")),
                    "loss": item.get("loss"),
                    "censored": item.get("censored")}
        for direction, item in sorted(per_candidate.items())})
    result = []
    execution = resolved["config"]["execution"]
    for name, spec in candidate_specs.items():
        spec = dict(spec)
        kind = spec.get("kind")
        if kind == "fixed_horizon":
            rule = "fixed_horizon"
            horizon = float(spec["common_horizon_s"])
        elif kind == "rule" and spec.get("common_rule") in (
                "mean_eta", "median_eta"):
            rule = str(spec["common_rule"])
            horizon = _common_horizon(probe, rule)
        else:
            raise CompareError(
                f"invalid common-horizon candidate {name!r}: {spec!r}")
        snapshot = build_snapshot(
            target, resolved, "common", horizon, pkt_bits, 0.0,
            float(execution["compute_delay_s"]),
            provenance=("common-calibration:" + str(name),),
            common_rule=rule)
        scored = ta.score_snapshot_at(snapshot)
        by_direction = scored.by_direction()
        chosen = scored.ranking[0] if scored.ranking else None
        picked_loss = losses.get(chosen) if chosen is not None else None
        all_actions_scored = (
            set(by_direction) == set(snapshot.legal_directions)
            and all(math.isfinite(float(score.total_s))
                    and not score.fallback and not score.missing
                    for score in by_direction.values()))
        all_outcomes_valid = all(
            direction in per_candidate
            and per_candidate[direction].get("valid")
            and not per_candidate[direction].get("censored")
            and isinstance(per_candidate[direction].get("loss"), (int, float))
            and math.isfinite(float(per_candidate[direction]["loss"]))
            for direction in snapshot.legal_directions)
        fully_scored = bool(all_actions_scored and all_outcomes_valid
                            and picked_loss is not None)
        result.append({
            "candidate": str(name), "candidate_spec": spec,
            "common_rule": rule, "common_horizon_s": horizon,
            "query_at": snapshot.snapshot_at + horizon,
            "ranking": list(scored.ranking),
            "chosen": chosen,
            "loss": picked_loss,
            "valid": fully_scored,
            "fully_scored": fully_scored,
            "fallback_directions": list(scored.fallback_directions),
            "missing_directions": list(scored.missing_directions),
            "scores": {score.direction: {
                "total_s": (None if not math.isfinite(float(score.total_s))
                            else float(score.total_s)),
                "terms": dict(score.terms),
                "missing": list(score.missing),
                "fallback": bool(score.fallback),
            } for score in scored.scores},
            "sample_digest": observation_digest,
            "outcome_digest": outcome_digest,
        })
    return result


# ---------------------------------------------------------------- driving
def _branch(resolved, rows, geometry, decision_id):
    sink, timeline = [], []
    baseline = kernel.run_simulation(
        resolved, rows, geometry=geometry, decision_sink=sink,
        timeline_sink=timeline)
    if decision_id is None:
        target = next((r for r in sink if r.get("kind") == "forward"), None)
        if target is None:
            raise CompareError(
                "the fixture produced no forward decision to compare")
    else:
        target = next((r for r in sink if r.get("decision_id") == decision_id),
                      None)
        if target is None:
            raise CompareError(f"decision {decision_id} never committed")
    return capture(baseline, sink, timeline), target


def _replay_payload(captured):
    """Keep the real event streams needed by the offline branch replay."""
    result = captured["result"]
    return {
        "decision_rows": captured["decision_rows"],
        "timeline_rows": captured["timeline_rows"],
        "packet_events": result["packet_events"],
        "link_service_windows": result["link_service_windows"],
        "link_available_windows": result["link_available_windows"],
        "queue_state_events": [row for row in captured["timeline_rows"]
                                if row.get("milestone") == "queue_state"],
        "topology_trace": result.get("topology_trace"),
        "fates": result["fates"],
        "deliveries": result["deliveries"],
        "handover_events": result["handover"]["events"],
    }


def load_frozen_deadline(path):
    """Load a development-frozen deadline with its provenance intact."""
    path = Path(path)
    if not path.exists():
        raise CompareError(f"frozen deadline file missing: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CompareError(f"frozen deadline file is not JSON: {exc}") from exc
    value = doc.get("deadline_s")
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not math.isfinite(float(value)) or float(value) <= 0:
        raise CompareError(
            f"frozen deadline file has no positive deadline_s: {path}")
    return {
        "deadline_s": float(value),
        "source": "frozen_development_file",
        "weak": bool(doc.get("weak", False)),
        "frozen_file": str(path),
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "frozen_from": doc.get("source"),
        "frozen_at_sha": doc.get("frozen_at_sha"),
        "identity": doc.get("identity"),
        "rule": doc.get("rule"),
    }


def write_frozen_deadline(path, report, resolved, run_kind):
    """Dev stage: freeze D with the identity that produced it."""
    path = Path(path)
    if path.exists():
        raise CompareError(f"refusing to overwrite a frozen deadline: {path}")
    identity = artifact_identity.build_identity(
        config=resolved, driver_paths=artifact_identity.execution_chain_paths())
    doc = dict(report)
    doc.update({
        "schema": "t1-frozen-deadline/v1",
        "rule": "D = multiplier x E2E p95 over the development baseline legal "
                "candidate delivery samples",
        "run_kind": run_kind,
        "frozen_at_sha": identity["git"].get("commit"),
        "identity": identity,
        "note": "frozen on development data only; a confirmation comparison "
                "loads this file and never re-derives D",
    })
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                         suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(doc, stream, ensure_ascii=False, sort_keys=True, indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return doc


def _deadline(resolved, per_candidate, deadline_s, frozen=None,
              allow_branch_derivation=True):
    if frozen is not None:
        return dict(frozen)
    if deadline_s is not None:
        if deadline_s <= 0:
            raise CompareError("--deadline-s must be > 0")
        return {"deadline_s": float(deadline_s), "source": "configured",
                "weak": False}
    if not allow_branch_derivation:
        raise CompareError(
            "this run kind may not derive D from the branch it is comparing: "
            "pass --deadline-from with the development-frozen deadline")
    delays = [c["outcome"]["delay_s"] for c in per_candidate.values()
              if c.get("valid") and c["outcome"]["delay_s"] is not None]
    if delays:
        report = t1_stats.default_deadline(delays)
        return {"deadline_s": report["deadline_s"],
                "source": report["source"], "weak": report["weak"],
                "delivered_samples": len(delays),
                "note": ("a single delivered sample makes D = 2 x that delay, "
                         "so loss is 0.5 by construction and this branch can "
                         "carry no comparative statement")
                        if len(delays) == 1 else None}
    horizon = float(resolved["config"]["scenario"]["duration_s"])
    return {"deadline_s": horizon, "source": "declared_scenario_horizon",
            "weak": True,
            "why": "no delivered candidate in this branch; D is the declared measurement horizon and is weakly interpretable"}


def score_with_state(snapshot, targets, *, use_truth, truth_fn):
    """Score every legal candidate at its own query instant.

    use_truth=True replaces the resource state with a TRUTH value supplied by
    truth_fn(direction, instant) -- the caller may only supply the state of the
    NAMED resource at that instant, never a final outcome.  use_truth=False uses
    the online predictor.  Either way the SAME scorer runs.
    """
    predictions, etas, detail = {}, {}, {}
    for direction in snapshot.legal_directions:
        resource = snapshot.resource_for(direction)
        eta = ta.estimate_eta(snapshot, direction)
        etas[direction] = eta
        instant = targets.get(direction)
        if instant is None:
            predictions[direction] = ta.ResourcePrediction(
                resource or ta.ResourceKey(0, "N", "isl"),
                snapshot.snapshot_at, snapshot.snapshot_at, None,
                "unavailable", missing_reason="query_instant_unknown")
            detail[direction] = {"available": False,
                                 "reason": "query_instant_unknown"}
            continue
        if use_truth:
            truth = truth_fn(direction, instant)
            detail[direction] = truth
            predictions[direction] = ta.ResourcePrediction(
                resource or ta.ResourceKey(0, "N", "isl"),
                snapshot.snapshot_at, instant, truth.get("bits"),
                "truth_at_query_instant",
                missing_reason=(None if truth.get("bits") is not None
                                else truth.get("reason")
                                or "truth_unavailable"))
        else:
            predictions[direction] = ta.predict_resource(
                snapshot.history_for(resource) if resource else (),
                snapshot_at=snapshot.snapshot_at, target_at=instant,
                method=snapshot.predictor,
                history_limit=snapshot.history_limit,
                max_queue_bits=snapshot.max_resource_queue_bits,
                resource=resource)
            detail[direction] = {
                "available": predictions[direction].predicted_bits is not None,
                "bits": predictions[direction].predicted_bits,
                "instant": instant,
                "method": predictions[direction].method}
    scored = ta.score_candidates(snapshot, predictions, etas)
    return scored, detail


def arm_view(scored, detail, label, role, losses, best):
    chosen = scored.ranking[0] if scored.ranking else None
    picked_loss = losses.get(chosen) if chosen in losses else None
    return {
        "arm": label,
        "role": role,
        "ranking": list(scored.ranking),
        "chosen": chosen,
        "loss": picked_loss,
        "regret": (None if picked_loss is None or best is None
                   else picked_loss - best),
        "fallback_directions": list(scored.fallback_directions),
        "truth_inputs": detail,
        "scores": {s.direction: {
            "total_s": (None if s.total_s == math.inf else s.total_s),
            "terms": dict(s.terms), "missing": list(s.missing),
            "fallback": s.fallback} for s in scored.scores},
        "query_instants": {d: detail.get(d, {}).get("instant")
                           for d in scored.by_direction()},
    }


def compare(resolved, rows, geometry, decision_id, deadline_s, source,
            frozen_deadline=None, run_kind="dev", capture_replay=False,
            common_horizon_candidates=None):
    branch, target = _branch(resolved, rows, geometry, decision_id)
    decision_id = int(target["decision_id"])
    if target.get("kind") != "forward":
        raise CompareError(
            "the four-group comparison is defined on a forward branch; "
            f"decision {decision_id} is not a forward decision")
    candidates = sorted(target.get("candidates") or [])
    if not candidates or target.get("chosen") not in candidates:
        raise CompareError("the target decision has no forcible candidate set")
    pid = target["pid"]
    pkt_bits = next((float(r["bits"]) for r in rows
                    if r.get("packet_id") == pid), None)
    if pkt_bits is None:
        pkt_bits = float(resolved["config"]["demand"]["packet_bits"])
    # diagnostic only: the realised compute interval is NOT fed to the arms
    realized_wait, realized_service = _compute_interval(
        branch["timeline_rows"], pid, target["t_decision_start"])

    # --- one closed-loop replay per candidate -------------------------
    base_outcome = _outcome(branch["result"], pid)
    base_ledger, _diag = decision_ledger.build_ledger(
        branch["decision_rows"], branch["timeline_rows"])
    per_candidate = {}
    branch_timelines = {}
    invalid = 0
    mismatch = 0
    for direction in candidates:
        pred = (target.get("observation_at_start") or {}).get(
            "candidate_resources", {}).get(direction) or {}
        if direction == target["chosen"]:
            chosen_branch = branch
            outcome = base_outcome
            entry_ledger = base_ledger
            valid, reason = True, None
        else:
            try:
                replay = counterfactual.replay_with_forced_action(
                    resolved, rows, geometry=geometry,
                    target_decision_id=decision_id,
                    forced_action=direction)
            except (counterfactual.CounterfactualError, kernel.KernelError) as exc:
                valid, reason = False, f"{type(exc).__name__}: {exc}"
                invalid += 1
                per_candidate[direction] = {
                    "direction": direction, "valid": False, "reason": reason,
                    "predicted_resource": pred, "outcome": None,
                    "loss": None, "regret": None, "resource_mismatch": None,
                    "resource_trajectory": []}
                continue
            cf = replay["counterfactual"]
            chosen_branch = capture(cf["result"], cf["decision_rows"],
                                    cf["timeline_rows"])
            outcome = _outcome(cf["result"], pid)
            entry_ledger, _d = decision_ledger.build_ledger(
                cf["decision_rows"], cf["timeline_rows"])
            valid, reason = True, None
        branch_timelines[direction] = chosen_branch["timeline_rows"]
        entry = entry_ledger.get(decision_id, {})
        truth_target = entry.get("truth_at_target")
        actual_egress = (truth_target.get("contended_direction")
                         if isinstance(truth_target, dict) else None)
        predicted_egress = pred.get("egress_direction")
        mismatch_flag = (None if (predicted_egress is None
                                   or actual_egress is None)
                         else predicted_egress != actual_egress)
        if mismatch_flag:
            mismatch += 1
        peer_id = pred.get("peer")
        egress_peer = pred.get("egress_peer")
        resource_link = (None if peer_id is None or egress_peer is None
                         else "isl:%s:%s" % (peer_id, egress_peer))
        per_candidate[direction] = {
            "direction": direction,
            "resource_link": resource_link,
            "valid": valid,
            "reason": reason,
            "taken_in_baseline": direction == target["chosen"],
            "predicted_resource": pred,
            "actual_egress_at_peer": actual_egress,
            "resource_mismatch": mismatch_flag,
            "resource_trajectory": (
                [] if resource_link is None else
                _resource_trajectory(chosen_branch["timeline_rows"],
                                     resource_link,
                                     target["t_decision_start"])),
            "outcome": outcome,
            "replay": (_replay_payload(chosen_branch)
                       if capture_replay else None),
        }

    deadline = _deadline(resolved, per_candidate, deadline_s,
                         frozen_deadline,
                         allow_branch_derivation=(run_kind == "dev"))
    deadline["run_kind"] = run_kind
    D = deadline["deadline_s"]
    horizon = float(resolved["config"]["scenario"]["duration_s"])
    losses = {}
    censored = 0
    for direction, item in per_candidate.items():
        if not item["valid"]:
            continue
        outcome = item["outcome"]
        emitted = outcome.get("emitted_at")
        window = None if emitted is None else horizon - float(emitted)
        item["observation_window_s"] = window
        item["deadline_used_s"] = D
        if window is not None and window < D:
            # the packet cannot be observed for a full D: mark it
            # ADMINISTRATIVELY CENSORED.  Counting it as a timeout failure
            # would invent a loss that the run never had the chance to show.
            item["censored"] = True
            item["loss"] = None
            item["regret"] = None
            item["censor_reason"] = (
                f"observation window {window:.6g}s < deadline {D:.6g}s")
            censored += 1
            continue
        item["censored"] = False
        if not outcome["delivered"]:
            loss = 1.0
        else:
            delay = outcome["delay_s"]
            loss = (1.0 if delay is None
                    else t1_stats.normalized_loss(delay, D))
        item["loss"] = loss
        losses[direction] = loss
    best = min(losses.values()) if losses else None
    for direction, item in per_candidate.items():
        item["regret"] = (None if item["loss"] is None or best is None
                          else item["loss"] - best)

    # --- four online arms --------------------------------------------
    cfg_ta = resolved["config"]["time_alignment"]
    scope = "scope:|%s|%s|default" % (target.get("sat"), target.get("dst"))
    probe = build_snapshot(target, resolved, "candidate", None, pkt_bits,
                           0.0, float(resolved["config"]["execution"][
                               "compute_delay_s"]), provenance=(scope,))
    if cfg_ta["common_rule"] == "fixed_horizon":
        horizon = float(cfg_ta["common_horizon_s"])
    else:
        horizon = _common_horizon(probe, cfg_ta["common_rule"])
    arms = {}
    for arm in ta.ARMS:
        snap = build_snapshot(target, resolved, arm, horizon, pkt_bits,
                              0.0, float(resolved["config"]["execution"][
                                  "compute_delay_s"]), provenance=(scope,))
        scored = ta.score_snapshot_at(snap, snap.snapshot_at + horizon)
        ranking = list(scored.ranking)
        picked = ranking[0] if ranking else None
        picked_loss = losses.get(picked) if picked in losses else None
        arms[arm] = {
            "arm": arm,
            "ranking": ranking,
            "scores": {s.direction: {"total_s": s.total_s,
                                     "terms": s.terms,
                                     "missing": list(s.missing),
                                     "fallback": s.fallback}
                       for s in scored.scores},
            "fallback_directions": list(scored.fallback_directions),
            "missing_directions": list(scored.missing_directions),
            "chosen": picked,
            "loss": picked_loss,
            "regret": (None if picked_loss is None or best is None
                       else picked_loss - best),
        }
    common_candidate_scores = []
    if common_horizon_candidates:
        common_candidate_scores = score_common_horizon_candidates(
            probe, resolved, target, pkt_bits, common_horizon_candidates,
            losses, per_candidate)
    oracle = {
        "arm": "oracle",
        "chosen": (None if not losses else min(losses, key=losses.get)),
        "loss": best,
        "regret": 0.0 if best is not None else None,
        "role": "offline evaluator only; never a policy input",
    }

    # --- R2: the three IDEAL state-time arms -------------------------------
    # Same branch point, same candidate set, same unified scorer; only the
    # resource state is replaced by the TRUTH of the named resource at that
    # arm's query instant, read from EACH CANDIDATE OWN branch.  The final
    # outcome never enters this input; it only scores the result afterwards.
    bits_by_pid = {r["packet_id"]: float(r["bits"]) for r in rows
                   if r.get("packet_id") is not None}

    def _truth(direction, instant):
        entry = per_candidate.get(direction) or {}
        # the WHOLE branch trace of that resource, not only the part after the
        # branch instant: a truth query AT t0 must see what was measured before
        # t0 (an empty queue is a real measurement, not a missing one)
        rows_for_resource = [
            m for m in _resource_timeline(
                branch_timelines.get(direction, ()), entry.get("resource_link"),
                0.0)
        ] if entry.get("resource_link") else []
        return truth_at_instant(rows_for_resource, bits_by_pid, instant, pid)

    def _realized_entry(direction):
        entry = per_candidate.get(direction) or {}
        link = entry.get("resource_link")
        if not link:
            return None
        for row in branch_timelines.get(direction, ()):
            if (row.get("milestone") == "queue_enter"
                    and row.get("pid") == pid and row.get("link_id") == link):
                return float(row["at"])
        return None

    def _truth_fn(direction, instant):
        return _truth(direction, instant)

    def _score_with_predictions(snapshot, targets, use_truth):
        return score_with_state(snapshot, targets, use_truth=use_truth,
                                truth_fn=_truth_fn)

    def _arm_view(scored, detail, label, role):
        return arm_view(scored, detail, label, role, losses, best)

    eta_targets = {d: ta.estimate_eta(probe, d).target_at
                   for d in probe.legal_directions}
    realized = {d: _realized_entry(d) for d in probe.legal_directions}
    ideal_arms = {}
    scored_now, detail_now = _score_with_predictions(
        probe, {d: probe.snapshot_at for d in probe.legal_directions}, True)
    ideal_arms["oracle_now"] = _arm_view(
        scored_now, detail_now, "oracle_now",
        "ideal information at t0 (offline value-space bound)")
    scored_common, detail_common = _score_with_predictions(
        probe, {d: probe.snapshot_at + horizon for d in probe.legal_directions},
        True)
    ideal_arms["oracle_common"] = _arm_view(
        scored_common, detail_common, "oracle_common",
        "ideal information at the shared future instant t0+h")
    scored_cand, detail_cand = _score_with_predictions(
        probe, eta_targets, True)
    ideal_arms["oracle_candidate"] = _arm_view(
        scored_cand, detail_cand, "oracle_candidate",
        "ideal information at each candidate own predicted resource instant")

    # --- R2b: ETA x queue-state 2x2 decomposition --------------------------
    decomposition = {}
    for eta_name, targets in (("eta_estimated", eta_targets),
                              ("eta_true", realized)):
        for queue_name, use_truth in (("queue_predicted", False),
                                      ("queue_truth", True)):
            if eta_name == "eta_estimated" and queue_name == "queue_predicted":
                decomposition[f"{eta_name}_x_{queue_name}"] = dict(
                    arms["candidate"], role="online candidate arm (baseline "
                                            "cell of the 2x2)")
                continue
            scored, detail = _score_with_predictions(probe, targets, use_truth)
            decomposition[f"{eta_name}_x_{queue_name}"] = _arm_view(
                scored, detail, f"{eta_name}_x_{queue_name}",
                "diagnostic only: a true ETA or a true queue state is not "
                "available to any online policy")
    decomposition_meta = {
        "eta_estimated": "ETA built from request-time known terms (compute "
                         "wait estimate, service, local egress queue, tx, "
                         "prop, known peer processing)",
        "eta_true": "the REALISED instant the target packet entered the "
                    "resource, read from that candidate own branch; "
                    "diagnostic only",
        "queue_predicted": "bounded_linear over received advertisements",
        "queue_truth": "reconstructed from that candidate own branch event "
                       "trace at the same query instant; diagnostic only",
    }

    identity = artifact_identity.build_identity(
        config=resolved, trace_digest=source.get("trace_sha256"),
        driver_paths=artifact_identity.execution_chain_paths(),
        extra={"decision_id": int(decision_id)})
    return {
        "schema": SCHEMA,
        "identity": identity,
        "source": dict(source, config_sha256=resolved["sha256"],
                       obs_mode=resolved["config"]["execution"][
                           "decision_observation_mode"],
                       policy=resolved["config"]["routing"]["policy"],
                       arm=cfg_ta["arm"], predictor=cfg_ta["predictor"],
                       execution_mode=cfg_ta["execution_mode"],
                       target_decision_id=int(decision_id), target_pid=pid,
                       target_kind=target.get("kind"),
                       baseline_chosen=target.get("chosen"),
                       candidates=candidates),
        "branch": {"t_decision_start": float(target["t_decision_start"]),
                   "t_decision_commit": float(target["t"]),
                   "realized_compute_wait_s": realized_wait,
                   "realized_compute_service_s": realized_service,
                   "compute_state_at_request":
                       (target.get("observation_at_start") or {}).get(
                           "compute_state"),
                   "pkt_bits": pkt_bits,
                   "candidate_directions": candidates,
                   "legal_directions": (target.get(
                       "observation_at_start") or {}).get(
                       "legal_directions")},
        "deadline": deadline,
        "observation": {
            "history_samples": {
                d: (0 if probe.resource_for(d) is None
                    else len(probe.history_for(probe.resource_for(d))))
                for d in probe.legal_directions},
            "resource_kind": {
                d: (None if probe.resource_for(d) is None
                    else probe.resource_for(d).kind)
                for d in probe.legal_directions},
            "snapshot_provenance": list(probe.provenance),
            "legal_directions": list(probe.legal_directions),
            "eta_terms": {d: dict(_ta.estimate_eta(probe, d).terms)
                          for d in probe.legal_directions},
            "eta_targets": {d: _ta.estimate_eta(probe, d).target_at
                            for d in probe.legal_directions},
            "eta_unknown_terms": {d: list(_ta.estimate_eta(probe, d).unknown_terms)
                                  for d in probe.legal_directions},
        },
        "candidates": per_candidate,
        "replay": ({
            "captured": True,
            "offline_diagnostic": True,
            "selection_rule": "first sampled structural decision with at least two legal directions and complete peer-resource identifiers; no outcome or arm ranking used",
            "target_decision": target,
            "target_packet_id": pid,
            "shared_baseline": _replay_payload(branch),
            "forced_candidate_branches": {
                direction: item.get("replay")
                for direction, item in per_candidate.items()},
            "information_arms": arms,
            "candidate_outcomes": {
                direction: {key: item.get(key) for key in (
                    "valid", "reason", "outcome", "loss", "regret",
                    "resource_trajectory", "predicted_resource",
                    "actual_egress_at_peer")}
                for direction, item in per_candidate.items()},
        } if capture_replay else {"captured": False}),
        "arms": arms,
        "common_horizon_candidates": common_candidate_scores,
        "ideal_arms": ideal_arms,
        "eta_queue_2x2": decomposition,
        "eta_queue_2x2_meaning": decomposition_meta,
        "oracle": oracle,
        "counts": {"candidates": len(candidates), "invalid_pairs": invalid,
                   "resource_mismatch": mismatch,
                   "censored": censored,
                   "valid_pairs": len(candidates) - invalid,
                   "scored_pairs": len(losses)},
        "units": {"loss": "normalized loss in [0,1] (dimensionless)",
                  "regret": "normalized loss difference (dimensionless)",
                  "score_terms": "seconds"},
        "limits": [
            "one branch is one contrast; no statistical statement is made",
            "each candidate is a separate replay from t=0, verified to reach the same branch point, not a copy-on-write fork",
            "candidate outcomes come from that candidate own branch only; no unchosen future is copied out of the chosen trajectory",
            "the oracle is the offline evaluator (best final outcome) and is never available to a policy",
            "a resource_mismatch candidate keeps its end-to-end outcome and is excluded from same-resource prediction-error statistics",
            "the scorer is a seconds-dimension heuristic, NOT an actual E2E equation",
        ],
    }


def compare_config(config_path, decision_id, deadline_s, root,
                   frozen_deadline=None, run_kind="dev"):
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise CompareError(f"config invalid: {exc}") from exc
    if resolved["config"]["learning"]["algorithm"] != "none":
        raise CompareError("the comparison requires a deterministic router")
    if resolved["config"]["execution"]["decision_observation_mode"] != "frozen":
        raise CompareError(
            "the four-group comparison requires decision_observation_mode="
            "frozen (the branch point must be the observation)")
    rows, digest = _trace_identity(resolved, root)
    source = {"scenario": "config", "config": str(config_path),
              "trace_sha256": digest, "rows": len(rows),
              "rows_digest": _rows_digest(rows)}
    return compare(resolved, rows, None, decision_id, deadline_s, source,
                   frozen_deadline=frozen_deadline, run_kind=run_kind)


def compare_scenario(name, decision_id, deadline_s, frozen_deadline=None,
                     run_kind="dev"):
    try:
        resolved, rows, geometry, meta = scripted_scenarios.build(name)
    except KeyError as exc:
        raise CompareError(str(exc)) from exc
    source = {"scenario": name, "config": None, "trace_sha256": None,
              "rows": len(rows), "rows_digest": _rows_digest(rows),
              "scripted_topology": meta["topology"],
              "scripted_cells": meta["cells"]}
    return compare(resolved, rows, geometry, decision_id, deadline_s, source,
                   frozen_deadline=frozen_deadline, run_kind=run_kind)


def publish(document, out: Path):
    if out.exists() or out.is_symlink():
        raise CompareError(f"output destination exists: {out}")
    parent = out.parent
    if not parent.is_dir() or parent.is_symlink():
        raise CompareError(
            f"output parent must be an existing non-symlink directory: {parent}")
    handle, temporary = tempfile.mkstemp(
        prefix=f".{out.name}.", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, out)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Four-group state-time comparison at one frozen branch")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scenario",
                        choices=sorted(scripted_scenarios.SCENARIOS))
    parser.add_argument("--decision-id", required=True,
                        help="an integer decision id, or first_forward")
    parser.add_argument("--deadline-s", type=float, default=None)
    parser.add_argument("--deadline-from", type=Path, default=None,
                        help="development-frozen deadline JSON to load")
    parser.add_argument("--freeze-deadline-to", type=Path, default=None,
                        help="dev only: write the derived deadline + identity")
    parser.add_argument("--run-kind", choices=("dev", "compare", "confirm"),
                        default="dev")
    parser.add_argument("--allow-branch-deadline", action="store_true",
                        help="dev only: permit deriving D from this branch")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.scenario):
        print("COMPARE REFUSED: give exactly one of --config or --scenario")
        return 2
    raw_id = str(args.decision_id)
    decision_id = None if raw_id == "first_forward" else int(raw_id)
    run_kind = "dev" if args.allow_branch_deadline else args.run_kind
    try:
        frozen = (load_frozen_deadline(args.deadline_from)
                  if args.deadline_from else None)
        if args.config:
            document = compare_config(args.config, decision_id,
                                      args.deadline_s, args.root,
                                      frozen_deadline=frozen,
                                      run_kind=run_kind)
        else:
            document = compare_scenario(args.scenario, decision_id,
                                        args.deadline_s,
                                        frozen_deadline=frozen,
                                        run_kind=run_kind)
        if args.freeze_deadline_to is not None:
            if run_kind != "dev":
                raise CompareError(
                    "--freeze-deadline-to is a development action; it needs "
                    "--run-kind dev")
            resolved = config_mod.load_config_file(str(args.config)) \
                if args.config else None
            write_frozen_deadline(args.freeze_deadline_to,
                                  document["deadline"], resolved or {},
                                  run_kind)
        publish(document, args.out)
    except CompareError as exc:
        print(f"COMPARE REFUSED: {exc}")
        return 2
    print(json.dumps({
        "status": "compared", "out": str(args.out),
        "scenario": document["source"]["scenario"],
        "candidates": document["counts"]["candidates"],
        "valid_pairs": document["counts"]["valid_pairs"],
        "invalid_pairs": document["counts"]["invalid_pairs"],
        "resource_mismatch": document["counts"]["resource_mismatch"],
        "deadline_s": document["deadline"]["deadline_s"],
        "regret": {arm: document["arms"][arm]["regret"] for arm in ta.ARMS},
        "oracle_chosen": document["oracle"]["chosen"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
