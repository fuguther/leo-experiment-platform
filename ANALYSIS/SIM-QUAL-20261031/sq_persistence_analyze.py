#!/usr/bin/env python3
"""Directional / persistence analysis for the 2026-10-31 four-policy matrix.

Companion to ``sq_policy_analyze.py`` (which is NOT modified).  That script
answers "how often does a stale action flip".  This one answers the follow-up
Round-2 question:

    does the flip pattern concentrate into a *persistent directional load
    bias* on particular directed ISL links, and does that bias matter when a
    corridor link is close to capacity?

Inputs (read only, one directory per cell, e.g.
``<evidence>/sq-<cell>-20261031-01/<cell>/``):

    arms/now/decisions.jsonl.gz    per-hop forward decisions + TA audit
    arms/now/target_probe.jsonl    per (decision, direction) truth probe
    arms/now/result.json.gz        link_service_windows / packet_events / e2e
    arms/now/config.json           resolved config (policy + link rate)
    trace.jsonl.gz                 pid -> bits (and src/dst grid id)

Frozen semantics reused verbatim from ``sq_policy_analyze.py``:

* flip is a truth-substituted same-decision re-ranking, not a re-simulated
  counterfactual.  ``J_truth = j_used - resource_work_used + truth_bits/rate``
  for queue-aware policies; ``geometry`` keeps ``J_truth = j_used``, so its
  J carries no state term and flip is ZERO by construction (the negative
  control).  Hysteresis re-applies the recorded ``last_direction`` + ``eta``
  to the truth J.
* only ``target_probe`` rows with ``identity_source == "used_state"`` (and
  ``arm == "now"``) are used.

New quantities (full definitions in ``persistence/README.md``):

1. ``delta_r`` = (bits wrongly pushed into r - bits that should have entered r
   but went elsewhere) / window, in bit/s.  Sum over r is 0 by construction
   (every flip is one + move and one - move).  Positive = r receives excess.
2. ``epsilon_r`` = rate_r - served_bits_r/window (bit/s), clipped exactly per
   window from ``link_service_windows`` with ``stage == "isl"``.  Note that
   served_bits <= rate*window can never be violated by a feasible schedule, so
   epsilon >= 0 by construction, epsilon == 0 means "fully saturated in this
   window" (counted separately, z reported as inf).  A separate *offered* load
   (``queue_enter`` arrivals, which CAN exceed mu) is reported as a diagnostic
   so that true overload is not hidden by the served-rate cap.
3. ``z = |delta_r| / epsilon_r`` binned [0,0.1) [0.1,0.3) [0.3,0.5) [0.5,1)
   [1,inf), evaluated on the 0.5 s bin that contains the decision.
4. persistence: consecutive flips with the same chosen direction inside one
   (sat,dst) decision stream, plus same-sign |delta_r| > frac*mu runs per
   resource (``tau_error``).
5. queue relaxation on corridor links rebuilt from ``packet_events``
   (``queue_enter``/``service_start`` share a per-enqueue ``queue_id``):
   Q(t) = bits of packets that entered the link queue at <= t and had not
   started service.  ``tau_relax`` = time from a peak back to 50% of the peak
   and back into the p10-p90 band of the preceding 2 s.
6. flip rate and directional persistent bias are kept as separate blocks.

Windows: evaluation window 4.0 <= t < 14.0 s (PROTOCOL section 2), fine bins
0.5 s; the burst window 8.0-8.5 s is exactly fine bin 8 and is reported
separately.  Nothing outside the evaluation window enters any statistic.

Usage:
    python3 sq_persistence_analyze.py cell   <cell-dir> [--json F] [--md F]
    python3 sq_persistence_analyze.py matrix <cell-dir> [<cell-dir> ...] \
        --out-dir ANALYSIS/SIM-QUAL-20261031/persistence --label p1-high

Deterministic: no timestamps, no wall clock, sorted output, ties broken by the
candidate order recorded in the decision row.
"""
from __future__ import annotations

import argparse
import bisect
import gzip
import json
import math
import statistics
import sys
from collections import defaultdict

# --------------------------------------------------------------- constants
EVAL_START_S = 4.0
EVAL_END_S = 14.0
BIN_S = 0.5
BURST_START_S = 8.0
BURST_END_S = 8.5
N_BINS = int(round((EVAL_END_S - EVAL_START_S) / BIN_S))

QUEUE_AWARE = ("queue_greedy", "queue_greedy_hysteresis", "queue_greedy_ewma",
               "legacy")
Z_BINS = ((0.0, 0.1), (0.1, 0.3), (0.3, 0.5), (0.5, 1.0), (1.0, math.inf))
Z_BIN_LABELS = [f"[{lo:g},{hi:g})" if math.isfinite(hi) else f"[{lo:g},inf)"
                for lo, hi in Z_BINS]
DELTA_FRAC_PRIMARY = 0.05            # |delta_r| > 5% of mu = "persistent"
DELTA_FRAC_SENSITIVITY = (0.01, 0.05, 0.10)
RHO_PRIMARY = 0.10                   # corridor = top 10% of active links
RHO_LEVELS = (0.05, 0.10, 0.20)
RELAX_BASE_WINDOW_S = 2.0
RELAX_PEAK_FLOOR_SLOTS = 2.0         # a peak must hold >= 2 service slots
                                     # (2 packets here): 1 slot is just the
                                     # packet currently in service
RELAX_MIN_PEAK_SEPARATION_S = 0.2
BAND_GRID_S = 0.5            # baseline band recomputed per 0.5 s bin
BAND_SAMPLE_S = 0.001        # sampling used only for the baseline quantiles
EPS_FLOOR_BPS = 1.0             # absolute floor before z is called infinite
Z_EPS_FLOOR_FRAC = 0.01         # z_valid requires epsilon >= 1% of mu (1 Mbps here)


# ----------------------------------------------------------------- helpers
def qstats(values, points=(0.5, 0.9, 0.95, 0.99), scale=1.0):
    """Same estimator as sq_policy_analyze.qstats (nearest rank, no interp)."""
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return {}
    clean.sort()
    n = len(clean)
    return {f"p{int(p * 100)}": scale * clean[min(n - 1, int(round(p * (n - 1))))]
            for p in points}


def _mean(values):
    clean = [float(v) for v in values if v is not None]
    return (sum(clean) / len(clean)) if clean else None


def _ratio(num, den):
    return (num / den) if den else None


def _safe_float(value):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def bin_index(moment):
    if moment is None or not (EVAL_START_S <= moment < EVAL_END_S):
        return None
    return min(N_BINS - 1, int((moment - EVAL_START_S) / BIN_S))


def bin_bounds(index):
    start = EVAL_START_S + index * BIN_S
    return start, start + BIN_S


def bin_label(index):
    start, end = bin_bounds(index)
    return f"[{start:.1f},{end:.1f})"


def overlap(a_start, a_end, b_start, b_end):
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def z_bin_label(z):
    if z is None:
        return None
    for (lo, hi), label in zip(Z_BINS, Z_BIN_LABELS):
        if lo <= z < hi:
            return label
    return Z_BIN_LABELS[-1]


def _arms(cell):
    return cell / "arms" / "now"


# ---------------------------------------------------------------- loaders
def load_packet_bits(cell):
    """pid -> bits and pid -> dst_grid_id from trace.jsonl.gz."""
    bits, dst = {}, {}
    with gzip.open(cell / "trace.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            pid = int(row["packet_id"])
            bits[pid] = float(row.get("bits") or 0.0)
            dst[pid] = row.get("dst_grid_id")
    return bits, dst


def load_probe(cell):
    """decision_id -> {direction: (resource_id, rate_bps, work_ahead_bits,
    eval_target_s)} for used_state rows only (frozen filter)."""
    probe = defaultdict(dict)
    used = other = 0
    with open(_arms(cell) / "target_probe.jsonl", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("identity_source") != "used_state" or row.get("arm") != "now":
                other += 1
                continue
            truth = row.get("truth") or {}
            probe[row["decision_id"]][row["direction"]] = (
                row.get("resource_id"),
                _safe_float(row.get("service_rate_bps")),
                _safe_float(truth.get("work_ahead_bits")),
                _safe_float(row.get("eval_target_s")),
            )
            used += 1
    return probe, {"rows_used": used, "rows_skipped": other,
                   "filter": "identity_source == 'used_state' and arm == 'now'"}


def iter_decisions(cell):
    """Yield compact per-decision records (streamed, never all held in RAM)."""
    with gzip.open(_arms(cell) / "decisions.jsonl.gz", "rt", encoding="utf-8") as fh:
        for line in fh:
            decision = json.loads(line)
            if decision.get("kind") != "forward":
                continue
            directions = decision.get("candidates") or []
            if len(directions) < 2:
                continue
            audit = decision["observation_at_start"]["time_alignment"]
            policy = audit.get("state_policy") or {}
            yield {
                "decision_id": decision["decision_id"],
                "t": _safe_float(decision.get("t")),
                "pid": decision.get("pid"),
                "sat": decision.get("sat"),
                "hop_index": decision.get("hop_index"),
                "chosen": decision.get("chosen"),
                "directions": directions,
                "policy_audit": policy.get("policy"),
                "last_direction": policy.get("last_direction"),
                "eta_s": _safe_float(policy.get("hysteresis_eta_s")),
                "per_dir": policy.get("per_direction") or {},
            }


def load_result(cell):
    with gzip.open(_arms(cell) / "result.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


def load_config(cell):
    return json.loads((_arms(cell) / "config.json").read_text(
        encoding="utf-8"))["resolved_config"]


def service_windows_by_link(result):
    """stage==isl service windows grouped by directed link, sorted by start."""
    by_link = defaultdict(list)
    for window in result.get("link_service_windows") or []:
        if window.get("stage") != "isl":
            continue
        rate = _safe_float(window.get("rate_bps"))
        start = _safe_float(window.get("start"))
        end = _safe_float(window.get("end"))
        if rate is None or rate <= 0 or start is None or end is None or end <= start:
            continue
        by_link[window["link_id"]].append((start, end, rate))
    for link in by_link:
        by_link[link].sort()
    return by_link


def load_link_entries(result):
    """link -> sorted [(at, pid)] for data-packet ISL queue entries."""
    entries = defaultdict(list)
    for event in result.get("packet_events") or []:
        if event.get("kind") != "queue_enter" or event.get("queue") != "isl":
            continue
        link = event.get("link_id")
        at = _safe_float(event.get("at"))
        if link is None or at is None:
            continue
        entries[link].append((at, event.get("pid")))
    for link in entries:
        entries[link].sort()
    return entries


class BacklogSeries:
    """Piecewise-linear backlog on one directed link, in bits.

    ``backlog = queued_bits + in-service_remaining_bits``:
    a packet contributes its full size while it waits, then decays linearly to
    zero across ``[service_start, service_finish]`` (finish taken from
    ``link_service_windows[*].end``).  Subtracting at ``service_start`` only
    would model "dequeue == drained", which understates how long the link is
    actually occupied.
    """

    __slots__ = ("times", "levels", "slopes")

    def __init__(self, times, levels, slopes):
        self.times = times
        self.levels = levels
        self.slopes = slopes

    def at(self, moment):
        if not self.times or moment < self.times[0]:
            return 0.0
        index = bisect.bisect_right(self.times, moment) - 1
        value = self.levels[index] + self.slopes[index] * (moment - self.times[index])
        return value if value > 0.0 else 0.0

    def max_between(self, t0, t1):
        peak = max(self.at(t0), self.at(t1))
        lo = bisect.bisect_right(self.times, t0)
        hi = bisect.bisect_right(self.times, t1)
        for index in range(lo, hi):
            peak = max(peak, self.levels[index])
        return max(0.0, peak)

    def samples(self, t0, t1, step):
        out = []
        moment = t0
        while moment < t1 - 1e-12:
            out.append(self.at(moment))
            moment += step
        return out


def build_backlog(result, bits_by_pid):
    """link -> BacklogSeries built from queue_enter / service_start / windows.

    ``queue_id`` is a per-enqueue instance id, so each ``queue_enter`` pairs
    with exactly one ``service_start``; the matching ``link_service_windows``
    entry supplies the service ``end`` used as the drain deadline.
    """
    windows = defaultdict(dict)      # (link, pid) -> {round(start, 6): end}
    for window in result.get("link_service_windows") or []:
        if window.get("stage") != "isl":
            continue
        start = _safe_float(window.get("start"))
        end = _safe_float(window.get("end"))
        if start is None or end is None:
            continue
        windows[(window.get("link_id"), window.get("pid"))][round(start, 6)] = end

    enqueued = {}
    for event in result.get("packet_events") or []:
        if event.get("kind") == "queue_enter" and event.get("queue") == "isl":
            at = _safe_float(event.get("at"))
            if at is not None:
                enqueued[event.get("queue_id")] = (at, event.get("link_id"),
                                                   event.get("pid"))
    ops = defaultdict(list)          # link -> [(time, order, kind, payload)]
    stats = {"unmatched_queue_enter": 0, "windows_matched": 0,
             "finish_from_rate_fallback": 0}
    for event in result.get("packet_events") or []:
        if event.get("kind") != "service_start" or event.get("stage") != "isl":
            continue
        start = _safe_float(event.get("at"))
        item = enqueued.pop(event.get("queue_id"), None)
        if item is None or start is None:
            continue
        enter_at, link, pid = item
        size = float(bits_by_pid.get(pid) or 0.0)
        finish = windows.get((link, pid), {}).get(round(start, 6))
        if finish is None:
            rate = _safe_float(event.get("rate_bps")) or 100e6
            finish = start + size / rate
            stats["finish_from_rate_fallback"] += 1
        else:
            stats["windows_matched"] += 1
        duration = finish - start
        ops[link].append((enter_at, 0, "enter", size))
        ops[link].append((start, 1, "start", (size, duration)))
        ops[link].append((finish, 2, "finish", size / duration if duration > 0 else 0.0))
    stats["unmatched_queue_enter"] = len(enqueued)

    series = {}
    for link, link_ops in ops.items():
        link_ops.sort()
        times, levels, slopes = [], [], []
        backlog = 0.0
        slope = 0.0
        for moment, order, kind, payload in link_ops:
            if times:
                backlog += slope * (moment - times[-1])
            if kind == "finish":
                slope += payload                 # in-service contribution reaches 0
            elif kind == "enter":
                backlog += payload
            else:
                # start: the bits stop waiting but stay counted (in-service
                # remaining), then decay linearly to zero at the service end.
                size, duration = payload
                if duration > 0:
                    slope -= size / duration
            if times and times[-1] == moment:
                levels[-1] = max(0.0, backlog)
                slopes[-1] = slope
            else:
                times.append(moment)
                levels.append(max(0.0, backlog))
                slopes.append(slope)
        # collapse duplicate timestamps already handled above
        series[link] = BacklogSeries(times, levels, slopes)
    return series, stats


def packet_e2e(result):
    out = {}
    for pid, row in ((result.get("congestion_metrics") or {}).get("packets")
                     or {}).items():
        value = _safe_float(row.get("e2e_s"))
        if value is not None:
            out[int(pid)] = value
    return out


# ----------------------------------------------------------- flip semantics
def truth_costs(record, probe_for_decision):
    """Replicate sq_policy_analyze.load_decisions' j_used / j_truth."""
    queue_aware = record["policy_audit"] in QUEUE_AWARE
    j_used, j_truth, resources = {}, {}, {}
    for direction in record["directions"]:
        entry = record["per_dir"].get(direction) or {}
        j = _safe_float(entry.get("j_used_s"))
        work = _safe_float(entry.get("resource_work_used_s"))
        rate = _safe_float(entry.get("service_rate_bps"))
        if j is None or work is None or rate is None or rate <= 0:
            continue
        j_used[direction] = j
        probe_row = probe_for_decision.get(direction)
        if probe_row is None or probe_row[2] is None:
            continue
        j_truth[direction] = j - work + probe_row[2] / rate
        if probe_row[0] is not None:
            resources[direction] = probe_row[0]
    return j_used, j_truth, resources


def fresh_action(record, j_used, j_truth, cell_policy):
    """Identical to sq_policy_analyze._fresh_action."""
    if len(j_truth) < 2:
        return None
    directions = record["directions"]
    best = min(j_truth, key=lambda d: (j_truth[d], directions.index(d)))
    if cell_policy == "geometry":
        if len(j_used) < 2:
            return None
        return min(j_used, key=lambda d: (j_used[d], directions.index(d)))
    if cell_policy == "queue_greedy_hysteresis":
        last = record["last_direction"]
        eta = float(record["eta_s"] or 0.0)
        if (last is not None and last in j_truth
                and float(j_truth[last]) <= float(j_truth[best]) + eta):
            return last
    return best


# --------------------------------------------------------- epsilon / delta
def epsilon_table(windows_by_link, mu_bps, n_bins=N_BINS):
    """eps[bin][link] = mu - served_bits/window, clipped exactly per window."""
    served = [defaultdict(float) for _ in range(n_bins)]
    for link, segments in windows_by_link.items():
        starts = [segment[0] for segment in segments]
        for index in range(n_bins):
            t0, t1 = bin_bounds(index)
            lo = bisect.bisect_left(starts, t0 - 1.0)
            hi = bisect.bisect_right(starts, t1)
            total = 0.0
            for start, end, rate in segments[lo:hi]:
                piece = overlap(start, end, t0, t1)
                if piece > 0:
                    total += rate * piece
            if total > 0:
                served[index][link] = total
    table = [dict() for _ in range(n_bins)]
    for index in range(n_bins):
        width = bin_bounds(index)[1] - bin_bounds(index)[0]
        for link, bits in served[index].items():
            table[index][link] = mu_bps - bits / width
    return table, served


def offered_table(entries, bits_by_pid, n_bins=N_BINS):
    """Offered rate per link per bin from queue_enter (CAN exceed mu)."""
    table = [defaultdict(float) for _ in range(n_bins)]
    for link, items in entries.items():
        times = [item[0] for item in items]
        for index in range(n_bins):
            t0, t1 = bin_bounds(index)
            lo = bisect.bisect_left(times, t0)
            hi = bisect.bisect_left(times, t1)
            total = 0.0
            for _, pid in items[lo:hi]:
                total += float(bits_by_pid.get(pid) or 0.0)
            if total > 0:
                table[index][link] = total / (t1 - t0)
    return table


# ---------------------------------------------------------------- analysis
def collect_decisions(cell, probe, bits_by_pid, dst_by_pid, cell_policy, log):
    """One streaming pass: totals + compact eval-window decision rows."""
    totals = {"multi": 0, "with_truth": 0, "flips_all": 0,
              "eval": 0, "eval_with_truth": 0, "flips_eval": 0,
              "flip_bits_eval": 0.0, "flips_burst": 0,
              "eval_missing_dst": 0, "eval_missing_bits": 0}
    rows = []
    for record in iter_decisions(cell):
        totals["multi"] += 1
        probe_for_decision = probe.get(record["decision_id"], {})
        j_used, j_truth, resources = truth_costs(record, probe_for_decision)
        if len(j_used) < 2:
            totals["multi"] -= 1          # not a scored multi-candidate decision
            continue
        if len(j_truth) >= 2:
            totals["with_truth"] += 1
        fresh = fresh_action(record, j_used, j_truth, cell_policy)
        if fresh is None or record["t"] is None:
            continue
        flipped = record["chosen"] != fresh
        if flipped:
            totals["flips_all"] += 1
        index = bin_index(record["t"])
        if index is None:
            continue
        pid = record["pid"]
        size = float(bits_by_pid.get(pid) or 0.0)
        if pid not in dst_by_pid:
            totals["eval_missing_dst"] += 1
        if not size:
            totals["eval_missing_bits"] += 1
        totals["eval"] += 1
        if len(j_truth) >= 2:
            totals["eval_with_truth"] += 1
        if flipped:
            totals["flips_eval"] += 1
            totals["flip_bits_eval"] += size
            if BURST_START_S <= record["t"] < BURST_END_S:
                totals["flips_burst"] += 1
        rows.append({
            "decision_id": record["decision_id"],
            "t": record["t"],
            "bin": index,
            "pid": pid,
            "sat": record["sat"],
            "dst": dst_by_pid.get(pid),
            "hop_index": record["hop_index"],
            "chosen": record["chosen"],
            "fresh": fresh,
            "chosen_resource": resources.get(record["chosen"]),
            "fresh_resource": resources.get(fresh),
            "flip": flipped,
            "bits": size,
        })
    log(f"decisions: multi={totals['multi']} flips_all={totals['flips_all']} "
        f"eval={totals['eval']} flips_eval={totals['flips_eval']}")
    return totals, rows


def analyze_cell(cell, verbose=False):
    def log(message):
        if not verbose:
            return
        name = getattr(cell, "name", str(cell))
        print(f"[{name}] {message}", file=sys.stderr)

    config = load_config(cell)
    ta = config.get("time_alignment") or {}
    cell_policy = ta.get("state_policy")
    mu_bps = float(config["links"]["isl_rate_mbps"]) * 1e6
    queue_bits = float(config["links"].get("isl_queue_bits") or 0.0)

    bits_by_pid, dst_by_pid = load_packet_bits(cell)
    probe, probe_meta = load_probe(cell)
    log(f"probe rows used={probe_meta['rows_used']} "
        f"skipped={probe_meta['rows_skipped']}")
    result = load_result(cell)
    windows_by_link = service_windows_by_link(result)
    link_entries = load_link_entries(result)
    queues, queue_meta = build_backlog(result, bits_by_pid)
    e2e_by_pid = packet_e2e(result)
    eps, served_per_bin = epsilon_table(windows_by_link, mu_bps)
    offered = offered_table(link_entries, bits_by_pid)
    totals, rows = collect_decisions(cell, probe, bits_by_pid, dst_by_pid,
                                     cell_policy, log)

    # ------------------------------------------------------ delta per bin
    # A flip is one +move (into the link it actually took) and one -move (out
    # of the link it should have taken).  Both endpoints must be known, else the
    # pair is dropped and counted, so that sum_r delta_r == 0 exactly.
    delta_bits = [defaultdict(float) for _ in range(N_BINS)]
    n_flip_unattributed = 0
    for row in rows:
        if not row["flip"]:
            continue
        if not row["chosen_resource"] or not row["fresh_resource"]:
            n_flip_unattributed += 1
            continue
        delta_bits[row["bin"]][row["chosen_resource"]] += row["bits"]
        delta_bits[row["bin"]][row["fresh_resource"]] -= row["bits"]
    delta_bps = []
    for index in range(N_BINS):
        width = bin_bounds(index)[1] - bin_bounds(index)[0]
        delta_bps.append({link: value / width for link, value in delta_bits[index].items()})

    # ------------------------------------------------ z per flip decision
    def decision_z(row):
        """(z, delta, epsilon, above_floor) for the link the packet actually took."""
        link = row["chosen_resource"]
        if link is None:
            return None, None, None, False
        delta = delta_bps[row["bin"]].get(link, 0.0)
        eps_link = eps[row["bin"]].get(link, mu_bps)
        if eps_link <= EPS_FLOOR_BPS:
            return math.inf, delta, eps_link, False
        return abs(delta) / eps_link, delta, eps_link, eps_link >= z_eps_floor

    # --------------------------------------------------- aggregate tables
    z_eps_floor = max(EPS_FLOOR_BPS, Z_EPS_FLOOR_FRAC * mu_bps)
    zbin = {}          # raw: every flip where z is computable (eps > EPS_FLOOR)
    zbin_valid = {}    # valid: only eps >= z_eps_floor (guards against z inflation)
    for label in Z_BIN_LABELS:
        for target in (zbin, zbin_valid):
            target[label] = {"decisions": 0, "flips": 0, "e2e": [], "slope_bps": [],
                             "q_at_t": [], "q_at_t2": [], "q_peak_next2": [],
                             "relaxed": 0, "resources": defaultdict(int)}
    n_flips_below_z_floor = 0
    n_flips_no_resource = 0
    zbin_nonflip = {"decisions": 0, "e2e": [], "slope_bps": [], "q_at_t": [],
                    "relaxed": 0}
    zbin_burst = {label: {"decisions": 0, "flips": 0, "e2e": []}
                  for label in Z_BIN_LABELS}
    per_bin = [{"decisions": 0, "flips": 0, "flip_bits": 0.0, "e2e": [],
                "slope_bps": [], "q_level": [], "z": []} for _ in range(N_BINS)]
    z_all = []
    z_valid = []

    for row in rows:
        t = row["t"]
        pid = row["pid"]
        e2e = e2e_by_pid.get(int(pid)) if pid is not None else None
        link = row["chosen_resource"]
        series = queues.get(link) if link else None
        q_t = series.at(t) if series else 0.0
        q_t2 = series.at(t + 2.0) if series else 0.0
        q_peak = series.max_between(t, t + 2.0) if series else 0.0
        slope = ((series.at(t + BIN_S) - q_t) / BIN_S) if series else 0.0
        relaxed = q_t2 <= q_t + 1e-9
        index = row["bin"]
        block = per_bin[index]
        block["decisions"] += 1
        block["e2e"].append(e2e)
        block["slope_bps"].append(slope)
        block["q_level"].append(q_t)
        if row["flip"]:
            block["flips"] += 1
            block["flip_bits"] += row["bits"]
            z, delta, eps_link, above_floor = decision_z(row)
            z_all.append(z)
            if link is None:
                n_flips_no_resource += 1
            elif above_floor:
                z_valid.append(z)
            else:
                n_flips_below_z_floor += 1
            block["z"].append(z)
            label = z_bin_label(z)
            if label is None:
                continue
            tables = (zbin, zbin_valid) if above_floor else (zbin,)
            for table in tables:
                entry = table[label]
                entry["decisions"] += 1
                entry["flips"] += 1
                entry["e2e"].append(e2e)
                entry["slope_bps"].append(slope)
                entry["q_at_t"].append(q_t)
                entry["q_at_t2"].append(q_t2)
                entry["q_peak_next2"].append(q_peak)
                entry["relaxed"] += 1 if relaxed else 0
                entry["resources"][link] += 1
            entry = zbin[label]
            if BURST_START_S <= t < BURST_END_S:
                zbin_burst[label]["decisions"] += 1
                zbin_burst[label]["flips"] += 1
                zbin_burst[label]["e2e"].append(e2e)
        else:
            zbin_nonflip["decisions"] += 1
            zbin_nonflip["e2e"].append(e2e)
            zbin_nonflip["slope_bps"].append(slope)
            zbin_nonflip["q_at_t"].append(q_t)
            zbin_nonflip["relaxed"] += 1 if relaxed else 0
    log("z bins done")

    # ------------------------------------------------ epsilon / corridor
    eval_width = EVAL_END_S - EVAL_START_S
    eval_served = defaultdict(float)          # bits served over the eval window
    for index in range(N_BINS):
        for link, bits in served_per_bin[index].items():
            eval_served[link] += bits
    util = {link: (served / eval_width) / mu_bps for link, served in eval_served.items()}
    ranked = sorted(util.items(), key=lambda kv: (-kv[1], kv[0]))
    active_links = len(ranked)
    rho_blocks = {}
    for rho in RHO_LEVELS:
        count = max(1, int(round(rho * active_links))) if active_links else 0
        corridor = [link for link, _ in ranked[:count]]
        values = [mu_bps - eval_served[link] / eval_width for link in corridor]
        rho_blocks[f"rho={rho:g}"] = {
            "n_links": count,
            "epsilon_mbps": {**{k: v / 1e6 for k, v in qstats(values).items()},
                             "min": (min(values) / 1e6) if values else None,
                             "max": (max(values) / 1e6) if values else None},
            "utilization": {"min": min((util[link] for link in corridor), default=None),
                            "max": max((util[link] for link in corridor), default=None)},
            "links": corridor if abs(rho - RHO_PRIMARY) < 1e-9 else None,
        }
    corridor_count = max(1, int(round(RHO_PRIMARY * active_links))) if active_links else 0
    corridor = [link for link, _ in ranked[:corridor_count]]
    corridor_set = set(corridor)

    subbin = subbin_saturation(windows_by_link, link_entries, bits_by_pid,
                              corridor, mu_bps)
    overload = {"n_link_bins_offered_over_mu": 0, "n_link_bins": 0,
                "burst_offered_util_max": None, "burst_links_over_mu": 0}
    for index in range(N_BINS):
        t0, t1 = bin_bounds(index)
        is_burst = t0 < BURST_END_S and t1 > BURST_START_S
        for link in corridor_set:
            value = offered[index].get(link)
            overload["n_link_bins"] += 1
            if value is None:
                continue
            ratio = value / mu_bps
            if ratio > 1.0:
                overload["n_link_bins_offered_over_mu"] += 1
                if is_burst:
                    overload["burst_links_over_mu"] += 1
            if is_burst:
                overload["burst_offered_util_max"] = max(
                    ratio, overload["burst_offered_util_max"] or 0.0)

    # ------------------------------------------------- persistent load runs
    load_runs = {}
    for frac in DELTA_FRAC_SENSITIVITY:
        threshold = frac * mu_bps
        runs = build_load_runs(delta_bps, threshold)
        complete = [run for run in runs if not run["censored"]]
        load_runs[f"{frac:g}"] = {
            "threshold_mbps": threshold / 1e6,
            "n_runs": len(runs),
            "n_censored": len(runs) - len(complete),
            "n_runs_corridor": sum(1 for run in runs if run["link"] in corridor_set),
            "n_runs_ge_1s": sum(1 for run in complete if run["duration_s"] >= 1.0 - 1e-9),
            "duration_s": qstats([run["duration_s"] for run in complete],
                                 points=(0.5, 0.9, 0.95), scale=1.0),
            "duration_max_s": max((run["duration_s"] for run in complete), default=None),
            "runs": sorted(({"link": run["link"], "sign": run["sign"],
                             "t0": run["t0"], "t1": run["t1"],
                             "duration_s": run["duration_s"],
                             "mean_mbps": run["mean_bps"] / 1e6,
                             "censored": run["censored"],
                             "in_corridor": run["link"] in corridor_set}
                            for run in runs),
                           key=lambda r: (-r["duration_s"], r["link"])),
        }
    primary_runs = load_runs[f"{DELTA_FRAC_PRIMARY:g}"]["runs"]

    # tau_error on decision timestamps (the binned runs above are only a
    # 0.5 s-quantized view of the same bias, kept for the aggregate tables).
    exact_runs = build_exact_error_runs(rows, delta_bps,
                                        DELTA_FRAC_PRIMARY * mu_bps)
    exact_multi = [run for run in exact_runs if run["count"] >= 2]

    # ---------------------------------------------------- delta summary
    net = defaultdict(float)
    for table in delta_bps:
        for link, value in table.items():
            net[link] += value / N_BINS
    eval_delta = dict(net)
    sum_abs_bps = sum(abs(value) for value in eval_delta.values())
    one_way_bps = sum_abs_bps / 2.0            # each flip counts in and out
    positive_bps = sum(value for value in eval_delta.values() if value > 0)
    negative_bps = sum(value for value in eval_delta.values() if value < 0)
    excess_ranked = sorted(((link, value) for link, value in eval_delta.items()
                            if value > 0), key=lambda kv: (-kv[1], kv[0]))
    deficit_ranked = sorted(((link, value) for link, value in eval_delta.items()
                             if value < 0), key=lambda kv: (kv[1], kv[0]))
    z_full = {}
    for link, value in eval_delta.items():
        eps_link = mu_bps - eval_served.get(link, 0.0) / eval_width
        z_full[link] = value / eps_link if eps_link > EPS_FLOOR_BPS else math.inf

    # ------------------------------------------- flip persistence (streams)
    streams = defaultdict(list)
    for row in rows:
        streams[(row["sat"], row["dst"])].append(row)
    runs_by_stream, runs_by_sat = [], []
    for key, seq in streams.items():
        seq.sort(key=lambda row: (row["t"], row["pid"] or 0))
        run = None
        for row in seq:
            if run and row["flip"] and run["direction"] == row["chosen"]:
                run["count"] += 1
                run["t1"] = row["t"]
                run["bits"] += row["bits"]
            else:
                if run:
                    runs_by_stream.append(run)
                run = ({"key": key, "direction": row["chosen"],
                        "t0": row["t"], "t1": row["t"], "count": 1,
                        "bits": row["bits"]}
                       if row["flip"] else None)
        if run:
            runs_by_stream.append(run)
    for run in runs_by_stream:
        run["duration_s"] = run["t1"] - run["t0"]
    sat_best = {}
    for run in runs_by_stream:
        sat = run["key"][0]
        current = sat_best.get(sat)
        if current is None or (run["count"], run["duration_s"]) > (
                current["count"], current["duration_s"]):
            sat_best[sat] = run
    runs_by_sat = list(sat_best.values())

    # ------------------------------------------------ queue relaxation
    e2e_by_link = defaultdict(list)
    for link, items in link_entries.items():
        for _, pid in items:
            value = e2e_by_pid.get(int(pid)) if pid is not None else None
            if value is not None:
                e2e_by_link[link].append(value)
    service_durations = [end - start for segments in windows_by_link.values()
                         for start, end, _ in segments]
    service_slot_s = (statistics.median(service_durations)
                      if service_durations else 0.0)
    peak_floor_bits = RELAX_PEAK_FLOOR_SLOTS * mu_bps * service_slot_s
    corridor_relax = {}
    all_tau50, all_tauband = [], []
    for link in corridor:
        series = queues.get(link)
        block = {
            "utilization": util.get(link),
            "lambda_mbps": util.get(link, 0.0) * mu_bps / 1e6,
            "epsilon_mbps": (mu_bps - eval_served.get(link, 0.0) / eval_width) / 1e6,
            "delta_mbps": eval_delta.get(link, 0.0) / 1e6,
        }
        if series is None:
            block.update({"n_excursions": 0, "note": "no data queue"})
            corridor_relax[link] = block
            continue
        episodes = detect_excursions(series, peak_floor_bits)
        tau50 = [ep["tau_50_s"] for ep in episodes if ep["tau_50_s"] is not None]
        tauband = [ep["tau_band_s"] for ep in episodes if ep["tau_band_s"] is not None]
        all_tau50.extend(tau50)
        all_tauband.extend(tauband)
        block.update({
            "n_excursions": len(episodes),
            "n_tau_50": len(tau50), "n_tau_band": len(tauband),
            "tau_50_ms": qstats(tau50, points=(0.5, 0.9), scale=1000.0),
            "tau_band_ms": qstats(tauband, points=(0.5, 0.9), scale=1000.0),
            "q_peak_mbit": (max(ep["peak_bits"] for ep in episodes) / 1e6)
            if episodes else None,
            "episodes": [{key: value for key, value in ep.items()
                          if key != "peak_bits"} for ep in episodes[:2]],
        })
        corridor_relax[link] = block
    log(f"corridor links={len(corridor)} excursions={len(all_tau50)}")

    comparison = build_error_relax_comparison(exact_multi, corridor_relax,
                                              link_entries, e2e_by_pid,
                                              queues, e2e_by_link)

    # ------------------------------------------------------------ report
    report = {
        "schema": "sq-persistence-cell/v1",
        "cell": cell.name,
        "policy": cell_policy,
        "hysteresis_eta_s": ta.get("hysteresis_eta_s"),
        "ewma_alpha": ta.get("ewma_alpha"),
        "definitions": {
            "delta": "delta_r = (bits wrongly routed into r - bits that should have entered r but went elsewhere)/window [bit/s]; sum_r delta_r = 0",
            "epsilon": "epsilon_r = mu_r - served_bits_r/window; mu_r = link rate_bps; clipped exactly per window from link_service_windows (stage=isl)",
            "epsilon_caveat": "served <= rate*window always holds, so epsilon >= 0 by construction; epsilon == 0 means saturated in that window (z = inf). Offered load from queue_enter arrivals is reported separately because it CAN exceed mu.",
            "z": "z = |delta_r| / epsilon_r, evaluated on the 0.5 s bin containing the decision; non-flips carry no delta and are a separate contrast row",
            "z_caveat": "delta_r and lambda_r are on different accounting bases: lambda_r counts bits actually served (misrouted bits included), delta_r is a counterfactual bookkeeping swing. z is therefore a NORMALISED BIAS MAGNITUDE (bias relative to residual capacity), not a load-triggered overload indicator; z >= 1 says the swing is comparable to or larger than the link's spare capacity in that bin, so correcting the bias would need more headroom than the link had.",
            "flip": "truth-substituted same-decision re-ranking (same rule as sq_policy_analyze.py); geometry has no state term -> flip = 0 by construction",
            "flip_rate": "flips / multi-candidate scored forward decisions inside the window",
            "persistence_stream": "same-(sat,dst) decision stream; dst and bits are joined from trace.jsonl.gz by pid because load_calibration's decision projection drops dst (a decision-row dst would silently degrade this to per-sat)",
            "tau_error": "duration of a same-sign |delta_r| > frac*mu run per resource (0.5 s bins, censored at window edges)",
            "tau_relax": "time from a queue peak to 50% of the peak, and back into the p10-p90 band of the preceding 2 s",
            "windows": {"eval_s": [EVAL_START_S, EVAL_END_S], "bin_s": BIN_S,
                        "burst_s": [BURST_START_S, BURST_END_S]},
            "corridor": f"top {RHO_PRIMARY:g} of active directed ISL links ranked by served rate in the eval window",
        },
        "inputs": {
            "mu_bps": mu_bps,
            "isl_queue_bits": queue_bits,
            "control_share_of_isl_busy_time": control_share(result),
            "probe": probe_meta,
            "trace_join": {
                "dst_source": "trace.jsonl.gz dst_grid_id joined by pid",
                "bits_source": "trace.jsonl.gz bits joined by pid",
                "reason": "load_calibration._project_decision drops the decision dst field, so reading dst from the decision row would silently collapse the (sat,dst) stream key to per-sat",
                "superseded_lens": "per-sat grouping (Round-1 W/switch) is kept only as a documented cross-check in persistence.flip_runs_by_sat_only",
                "eval_rows_without_dst": totals["eval_missing_dst"],
                "eval_rows_without_bits": totals["eval_missing_bits"],
            },
            "backlog_rebuild": dict(
                queue_meta,
                definition="backlog = queued bits + in-service remaining bits (linear decay over [service_start, service_end])",
                service_duration_ms=qstats(
                    [1000.0 * (end - start) for link, segments in windows_by_link.items()
                     for start, end, _ in segments], points=(0.5, 0.9)),
                baseline_band={"grid_s": BAND_GRID_S, "sample_s": BAND_SAMPLE_S,
                               "window_s": RELAX_BASE_WINDOW_S}),
            "n_active_isl_links": active_links,
            "n_link_entries": len(link_entries),
            "config_sha256": _config_sha(cell),
            "trace_source_sha256": _trace_sha(cell),
        },
        "flip": {
            "flip_rate_all": _ratio(totals["flips_all"], totals["multi"]),
            "flip_rate_eval": _ratio(totals["flips_eval"], totals["eval"]),
            "n_decisions_all": totals["multi"],
            "n_decisions_with_truth": totals["with_truth"],
            "n_decisions_eval": totals["eval"],
            "n_decisions_eval_with_truth": totals["eval_with_truth"],
            "n_flips_all": totals["flips_all"],
            "n_flips_eval": totals["flips_eval"],
            "flip_bits_eval_mbit": totals["flip_bits_eval"] / 1e6,
            "n_flips_burst": totals["flips_burst"],
            "geometry_constructive_zero": cell_policy == "geometry",
            "geometry_check_ok": (totals["flips_all"] == 0)
            if cell_policy == "geometry" else None,
        },
        "directional_bias": {
            "total_misrouted_mbps": one_way_bps / 1e6,
            "accounting": {
                "sum_abs_delta_mbps": sum_abs_bps / 1e6,
                "one_way_moved_mbps": one_way_bps / 1e6,
                "sum_positive_excess_mbps": positive_bps / 1e6,
                "sum_negative_deficit_mbps": negative_bps / 1e6,
                "net_sum_mbps": sum(eval_delta.values()) / 1e6,
                "top5_positive_excess_mbps": sum(
                    value for _, value in excess_ranked[:5]) / 1e6,
                "note": ("delta_r counts the bits a decision moved from the link it "
                         "should have taken to the link it actually took. It is a "
                         "per-decision attribution, NOT end-to-end rerouted net "
                         "traffic: later hops of the same packet can correct it. "
                         "Every flip contributes one + and one -, so sum_r delta_r "
                         "= 0 and sum_r |delta_r| counts each moved bit twice."),
            },
            "net_sum_mbps": sum(eval_delta.values()) / 1e6,
            "n_resources_touched": len(eval_delta),
            "n_resources_abs_over_1mbps": sum(1 for value in eval_delta.values()
                                              if abs(value) > 1e6),
            "n_flips_unattributed": n_flip_unattributed,
            "note": "a flip needs both endpoint links; unpaired flips are dropped so that sum_r delta_r = 0 exactly",
            "top_excess": delta_rows(excess_ranked, z_full, 10),
            "top_deficit": delta_rows(deficit_ranked, z_full, 10),
            "longest_positive_run_s": longest_run(primary_runs, +1),
            "longest_negative_run_s": longest_run(primary_runs, -1),
            "corridor_delta_mbps": {
                link: eval_delta.get(link, 0.0) / 1e6 for link in corridor},
        },
        "epsilon": {
            "corridor_rho": RHO_PRIMARY,
            "corridor_links": corridor,
            "levels": rho_blocks,
            "n_saturated_link_bins": sum(
                1 for table in eps for value in table.values()
                if value <= EPS_FLOOR_BPS),
            "n_link_bins": sum(len(table) for table in eps),
            "eval_epsilon_mbps": qstats(
                [mu_bps - eval_served[link] / eval_width for link in eval_served],
                scale=1e-6),
            "utilization": qstats(list(util.values())),
            "offered_overload": overload,
            "subbin_resolution": subbin,
        },
        "z_bins": {
            "bin_labels": Z_BIN_LABELS,
            "epsilon_floor_mbps": z_eps_floor / 1e6,
            "epsilon_floor_rule": "z_valid only keeps flips whose epsilon_r >= max(EPS_FLOOR, 1% of mu)",
            "n_flips_excluded_by_floor": n_flips_below_z_floor,
            "n_flips_without_resource": n_flips_no_resource,
            "flip_decisions": {
                label: {
                    "n_decisions": zbin[label]["decisions"],
                    "n_flips": zbin[label]["flips"],
                    "frac_of_flips": _ratio(zbin[label]["flips"],
                                            totals["flips_eval"]),
                    "e2e_ms": qstats(zbin[label]["e2e"], scale=1000.0),
                    "queue_growth_mbps": qstats(
                        [value / 1e6 for value in zbin[label]["slope_bps"]]),
                    "q_at_t_mbit": qstats([v / 1e6 for v in zbin[label]["q_at_t"]]),
                    "q_peak_next2_mbit": qstats(
                        [v / 1e6 for v in zbin[label]["q_peak_next2"]]),
                    "q_at_t2_mbit": qstats([v / 1e6 for v in zbin[label]["q_at_t2"]]),
                    "frac_relaxed_at_2s": _ratio(zbin[label]["relaxed"],
                                                 zbin[label]["decisions"]),
                    "top_resources": sorted(zbin[label]["resources"].items(),
                                            key=lambda kv: (-kv[1], kv[0]))[:3],
                } for label in Z_BIN_LABELS},
            "flip_decisions_valid": {
                label: {
                    "n_decisions": zbin_valid[label]["decisions"],
                    "n_flips": zbin_valid[label]["flips"],
                    "frac_of_valid_flips": _ratio(zbin_valid[label]["flips"],
                                                  len(z_valid)),
                    "e2e_ms": qstats(zbin_valid[label]["e2e"], scale=1000.0),
                    "queue_growth_mbps": qstats(
                        [value / 1e6 for value in zbin_valid[label]["slope_bps"]]),
                    "q_at_t_mbit": qstats(
                        [v / 1e6 for v in zbin_valid[label]["q_at_t"]]),
                    "q_peak_next2_mbit": qstats(
                        [v / 1e6 for v in zbin_valid[label]["q_peak_next2"]]),
                    "frac_relaxed_at_2s": _ratio(zbin_valid[label]["relaxed"],
                                                 zbin_valid[label]["decisions"]),
                    "top_resources": sorted(zbin_valid[label]["resources"].items(),
                                            key=lambda kv: (-kv[1], kv[0]))[:3],
                } for label in Z_BIN_LABELS},
            "non_flip_decisions": {
                "n_decisions": zbin_nonflip["decisions"],
                "e2e_ms": qstats(zbin_nonflip["e2e"], scale=1000.0),
                "queue_growth_mbps": qstats(
                    [value / 1e6 for value in zbin_nonflip["slope_bps"]]),
                "q_at_t_mbit": qstats([v / 1e6 for v in zbin_nonflip["q_at_t"]]),
                "frac_relaxed_at_2s": _ratio(zbin_nonflip["relaxed"],
                                             zbin_nonflip["decisions"]),
                "note": "no delta by definition -> no z; contrast row only",
            },
            "burst_window": {
                "window": [BURST_START_S, BURST_END_S],
                "n_flips": totals["flips_burst"],
                "bins": zbin_burst,
                "n_flips_z_ge_0_5": sum(
                    zbin_burst[label]["flips"] for label in
                    ("[0.5,1)", "[1,inf)")),
                "n_flips_z_ge_1": zbin_burst["[1,inf)"]["flips"],
            },
            "z_percentiles": {
                "p50": qstats(z_all, points=(0.5,)).get("p50") if z_all else None,
                "p90": qstats(z_all, points=(0.9,)).get("p90") if z_all else None,
                "p99": qstats(z_all, points=(0.99,)).get("p99") if z_all else None,
                "frac_ge_0_5": _ratio(sum(1 for z in z_all
                                          if z is not None and z >= 0.5), len(z_all)),
                "frac_ge_1": _ratio(sum(1 for z in z_all
                                        if z is not None and z >= 1.0), len(z_all)),
                "n": len(z_all),
            },
            "z_percentiles_valid": {
                "p50": qstats(z_valid, points=(0.5,)).get("p50") if z_valid else None,
                "p90": qstats(z_valid, points=(0.9,)).get("p90") if z_valid else None,
                "p99": qstats(z_valid, points=(0.99,)).get("p99") if z_valid else None,
                "frac_ge_0_5": _ratio(sum(1 for z in z_valid
                                          if z is not None and z >= 0.5), len(z_valid)),
                "frac_ge_1": _ratio(sum(1 for z in z_valid
                                        if z is not None and z >= 1.0), len(z_valid)),
                "n": len(z_valid),
            },
        },
        "persistence": {
            "tau_error_definition": ("duration = t(last flip) - t(first flip) of a "
                                     "maximal run of consecutive flips with the same "
                                     "chosen direction inside one (sat,dst) stream; "
                                     "decision timestamps, no bin quantization"),
            "flip_runs_by_stream": run_summary(runs_by_stream),
            "flip_runs_by_sat_only": dict(
                run_summary(runs_by_sat),
                superseded_round1_lens=True,
                note=("per-sat key (the Round-1 W/switch lens) - superseded: the "
                      "decision projection drops dst, so a per-sat key is what the "
                      "old numbers implicitly used; the (sat,dst) join above is "
                      "authoritative")),
            "error_runs_exact": {
                "definition": ("per-link runs of flip decisions wrongly pushed into "
                               "the same link, joined only while consecutive "
                               "decisions are <= 1 s apart; duration is "
                               "t_last - t_first"),
                "threshold_mbps": DELTA_FRAC_PRIMARY * mu_bps / 1e6,
                "n_runs": len(exact_runs),
                "n_runs_ge2": len(exact_multi),
                "n_runs_ge1s": sum(1 for run in exact_multi
                                   if run["duration_s"] >= 1.0 - 1e-9),
                "n_runs_censored": sum(1 for run in exact_runs if run["censored"]),
                "n_runs_on_corridor": sum(1 for run in exact_runs
                                          if run["link"] in corridor_set),
                "n_runs_with_relax": sum(1 for run in exact_runs
                                         if run["link"] in corridor_relax
                                         and (corridor_relax[run["link"]].get("tau_50_ms") or {})
                                         .get("p50") is not None),
                "tau_error_s": qstats([run["duration_s"] for run in exact_multi],
                                     points=(0.5, 0.9, 0.95)),
                "tau_error_max_s": max((run["duration_s"] for run in exact_multi),
                                       default=None),
                "count": qstats([run["count"] for run in exact_multi],
                                points=(0.5, 0.9)),
                "top_runs": [{key: run[key] for key in
                              ("link", "t0", "t1", "duration_s", "count",
                               "mean_mbps", "censored")}
                             for run in exact_multi[:5]],
            },
            "binned_bias_runs": {
                "note": ("0.5 s-bin view of the same |delta_r| bias: durations are "
                         "QUANTIZED to the bin width and are NOT tau_error"),
                "quantization_s": BIN_S,
                "by_threshold": {key: {k: v for k, v in block.items()
                                       if k not in ("runs",)}
                                 for key, block in load_runs.items()},
            },
            "load_runs": {key: {k: v for k, v in block.items() if k != "runs"}
                          for key, block in load_runs.items()},
            "load_runs_top": primary_runs[:3],
        },
        "queue_relaxation": {
            "n_corridor_links": len(corridor),
            "n_excursions": len(all_tau50),
            "peak_floor_mbit": peak_floor_bits / 1e6,
            "peak_floor_rule": "peak backlog >= 2 service slots (1 slot = one packet in service)",
            "service_slot_ms": 1000.0 * service_slot_s,
            "tau_50_ms": qstats(all_tau50, points=(0.5, 0.9, 0.95), scale=1000.0),
            "tau_band_ms": qstats(all_tauband, points=(0.5, 0.9, 0.95), scale=1000.0),
            "per_link": corridor_relax,
        },
        "tau_error_vs_relax": comparison,
        "per_bin": [{
            "bin": bin_label(index),
            "is_burst": bin_bounds(index)[0] < BURST_END_S
            and bin_bounds(index)[1] > BURST_START_S,
            "n_decisions": per_bin[index]["decisions"],
            "n_flips": per_bin[index]["flips"],
            "flip_bits_mbit": per_bin[index]["flip_bits"] / 1e6,
            "e2e_ms": qstats(per_bin[index]["e2e"], scale=1000.0),
            "queue_growth_mbps": qstats(
                [value / 1e6 for value in per_bin[index]["slope_bps"]]),
            "z_p50": qstats([z for z in per_bin[index]["z"] if z is not None],
                            points=(0.5,), scale=1.0).get("p50"),
            "n_z_ge_1": sum(1 for z in per_bin[index]["z"]
                            if z is not None and z >= 1.0),
            "top_delta_mbps": sorted(
                ((link, value / 1e6) for link, value in delta_bps[index].items()),
                key=lambda kv: (-abs(kv[1]), kv[0]))[:5],
        } for index in range(N_BINS)],
    }
    return report


def build_exact_error_runs(rows, delta_bps, threshold, max_gap_s=1.0):
    """Per-link persistent-excess runs measured on decision timestamps.

    A run is a maximal sequence of flip decisions that were wrongly pushed into
    the same directed link, each falling in a 0.5 s bin where that link's net
    delta exceeds ``threshold``, and with consecutive decisions no further
    apart than ``max_gap_s``.  ``duration_s`` is ``t_last - t_first`` of the
    actual decisions - the 0.5 s bin is only used to decide membership, never as
    a duration.
    """
    per_link = defaultdict(list)
    for row in rows:
        if not row["flip"] or not row["chosen_resource"]:
            continue
        link = row["chosen_resource"]
        if delta_bps[row["bin"]].get(link, 0.0) <= threshold:
            continue
        per_link[link].append(row)
    runs = []
    for link, seq in per_link.items():
        seq.sort(key=lambda row: (row["t"], row["pid"] or 0))
        current = None
        for row in seq:
            if current and row["t"] - current["t1"] <= max_gap_s:
                current["count"] += 1
                current["t1"] = row["t"]
                current["bits"] += row["bits"]
            else:
                if current:
                    runs.append(current)
                current = {"link": link, "t0": row["t"], "t1": row["t"],
                           "count": 1, "bits": row["bits"]}
        if current:
            runs.append(current)
    for run in runs:
        run["duration_s"] = run["t1"] - run["t0"]
        run["mean_mbps"] = (run["bits"] / run["duration_s"] / 1e6
                            if run["duration_s"] > 0 else None)
        run["censored"] = (abs(run["t0"] - EVAL_START_S) < BIN_S
                           or abs(run["t1"] - (EVAL_END_S - 1e-9)) < BIN_S)
    runs.sort(key=lambda run: (-run["duration_s"], run["link"]))
    return runs


def subbin_saturation(windows_by_link, link_entries, bits_by_pid, corridor,
                      mu_bps, step=0.1):
    """Expose saturation that the 0.5 s attribution bins average away.

    Served utilisation cannot exceed 1 by construction, so a 0.5 s average of
    0.94 can hide 0.1 s slices that are pinned at 1.00.  This diagnostic is
    reporting only; the frozen z attribution stays on the 0.5 s grid.
    """

    def served_util(link, t0, t1):
        segments = windows_by_link.get(link)
        if not segments:
            return None
        starts = [segment[0] for segment in segments]
        lo = bisect.bisect_left(starts, t0 - 1.0)
        hi = bisect.bisect_right(starts, t1)
        total = 0.0
        for start, end, rate in segments[lo:hi]:
            piece = overlap(start, end, t0, t1)
            if piece > 0:
                total += rate * piece
        return total / ((t1 - t0) * mu_bps)

    def offered_util(link, t0, t1):
        items = link_entries.get(link)
        if not items:
            return None
        times = [item[0] for item in items]
        lo = bisect.bisect_left(times, t0)
        hi = bisect.bisect_left(times, t1)
        total = sum(float(bits_by_pid.get(pid) or 0.0)
                    for _, pid in items[lo:hi])
        return total / ((t1 - t0) * mu_bps)

    out = {"sub_bin_s": step, "n_corridor_links": len(corridor)}
    for name, window_start, window_end in (
            ("eval", EVAL_START_S, EVAL_END_S),
            ("burst", BURST_START_S, BURST_END_S)):
        n_bins = n_saturated = 0
        n_offered_over = 0
        util_max = 0.0
        offered_max = 0.0
        eps_min = None
        saturated_links = set()
        for link in corridor:
            moment = window_start
            while moment < window_end - 1e-12:
                util = served_util(link, moment, moment + step)
                if util is not None:
                    n_bins += 1
                    util_max = max(util_max, util)
                    epsilon = max(0.0, mu_bps * (1.0 - util))
                    eps_min = epsilon if eps_min is None else min(eps_min, epsilon)
                    if util >= 0.999:
                        n_saturated += 1
                        saturated_links.add(link)
                offered = offered_util(link, moment, moment + step)
                if offered is not None:
                    offered_max = max(offered_max, offered)
                    if offered > 1.0:
                        n_offered_over += 1
                moment += step
        out[name] = {
            "n_link_subbins": n_bins,
            "n_saturated_subbins": n_saturated,
            "frac_saturated_subbins": _ratio(n_saturated, n_bins),
            "saturated_links": sorted(saturated_links),
            "served_util_max": util_max,
            "epsilon_min_mbps": None if eps_min is None else eps_min / 1e6,
            "n_subbins_offered_over_mu": n_offered_over,
            "offered_util_max": offered_max,
        }
    return out


def build_load_runs(delta_bps, threshold):
    """Runs of consecutive 0.5 s bins where delta on one link keeps its sign
    and stays above ``threshold`` (bit/s)."""
    links = sorted({link for table in delta_bps for link in table})
    runs = []
    for link in links:
        current = None
        for index in range(N_BINS):
            value = delta_bps[index].get(link, 0.0)
            active = abs(value) > threshold
            sign = 1 if value > 0 else -1
            if not active:
                if current:
                    runs.append(current)
                    current = None
                continue
            if current and current["sign"] == sign and current["end"] == index:
                current["end"] = index + 1
                current["peak_bps"] = max(current["peak_bps"], abs(value))
                current["sum_bps"] += value
                continue
            if current:
                runs.append(current)
            current = {"link": link, "sign": sign, "start": index,
                       "end": index + 1, "peak_bps": abs(value),
                       "sum_bps": value}
        if current:
            runs.append(current)
    for run in runs:
        count = run["end"] - run["start"]
        run["duration_s"] = count * BIN_S
        run["t0"], run["t1"] = bin_bounds(run["start"])[0], bin_bounds(run["end"] - 1)[1]
        run["mean_bps"] = run["sum_bps"] / count
        run["censored"] = run["start"] == 0 or run["end"] == N_BINS
    return runs


def delta_rows(ranked, z_full, count):
    out = []
    for link, value in ranked[:count]:
        z = z_full.get(link)
        out.append({"link": link, "delta_mbps": value / 1e6,
                    "z": (None if z is None else
                          ("inf" if not math.isfinite(z) else z))})
    return out


def longest_run(runs, sign):
    values = [run["duration_s"] for run in runs if run["sign"] == sign]
    return max(values) if values else None


def run_summary(runs):
    lengths = [run["count"] for run in runs]
    durations = [run["duration_s"] for run in runs]
    multi = [run for run in runs if run["count"] >= 2]
    multi_durations = [run["duration_s"] for run in multi]
    return {
        "n_runs": len(runs),
        "n_runs_ge2": len(multi),
        "n_runs_ge3": sum(1 for n in lengths if n >= 3),
        "length_p50": qstats(lengths, points=(0.5, 0.9)).get("p50"),
        "length_p90": qstats(lengths, points=(0.9,)).get("p90"),
        "length_max": max(lengths) if lengths else None,
        "duration_ms": qstats(durations, points=(0.5, 0.9, 0.95), scale=1000.0),
        "duration_max_ms": (1000.0 * max(durations)) if durations else None,
        # single-flip "runs" have zero duration by definition; the multi-flip
        # subset is the one worth reading as persistence.
        "duration_ms_ge2": qstats(multi_durations, points=(0.5, 0.9, 0.95),
                                  scale=1000.0),
        "duration_max_ms_ge2": (1000.0 * max(multi_durations))
        if multi_durations else None,
        "top_runs": sorted(({"sat": run["key"][0], "dst": run["key"][1],
                             "direction": run["direction"],
                             "count": run["count"], "t0": run["t0"],
                             "t1": run["t1"],
                             "duration_ms": 1000.0 * run["duration_s"]}
                            for run in runs),
                           key=lambda r: (-r["count"], -r["duration_ms"],
                                          r["sat"] if r["sat"] is not None else -1))[:10],
    }


def control_share(result):
    occupied = result.get("occupied") or {}
    busy = _safe_float(occupied.get("isl_s"))
    control = _safe_float(occupied.get("ctrl_isl_s"))
    if busy is None or control is None or (busy + control) <= 0:
        return None
    return control / (busy + control)


def level_at(times, levels, moment):
    """Value of the step function at ``moment`` (0 before the first event)."""
    index = bisect.bisect_right(times, moment) - 1
    return levels[index] if index >= 0 else 0.0


def weighted_quantile(times, levels, t0, t1, quantile):
    """Exact time-weighted quantile of a step function over [t0, t1).

    The level is constant between consecutive event timestamps, so the
    distribution is a finite set of (duration, level) pairs; no resampling and
    therefore no grid floor.
    """
    if t1 <= t0:
        return None
    first = bisect.bisect_right(times, t0)          # first event time > t0
    stop = bisect.bisect_left(times, t1)            # first event time >= t1
    points = [t0] + [times[index] for index in range(first, stop)] + [t1]
    pairs = []
    for left, right in zip(points, points[1:]):
        if right <= left:
            continue
        pairs.append((right - left, level_at(times, levels, left)))
    total = sum(duration for duration, _ in pairs)
    if total <= 0:
        return None
    # quantiles are over the *value* distribution: sort by level, accumulate
    # duration, then read the level where the cumulative mass reaches q.
    pairs.sort(key=lambda pair: pair[1])
    target = quantile * total
    running = 0.0
    for duration, level in pairs:
        running += duration
        if running >= target:
            return level
    return pairs[-1][1]


def _baseline_band(series, moment, cache):
    """p10-p90 band of the backlog over the 2 s preceding the 0.5 s bin."""
    index = int((moment - EVAL_START_S) // BAND_GRID_S)
    if index in cache:
        return cache[index]
    bin_start = EVAL_START_S + index * BAND_GRID_S
    band_start = max(EVAL_START_S, bin_start - RELAX_BASE_WINDOW_S)
    if bin_start - band_start < 0.5:
        cache[index] = (None, None)
        return cache[index]
    values = sorted(series.samples(band_start, bin_start, BAND_SAMPLE_S))
    if len(values) < 10:
        cache[index] = (None, None)
        return cache[index]
    cache[index] = (values[int(0.10 * (len(values) - 1))],
                    values[int(0.90 * (len(values) - 1))])
    return cache[index]


def _tau_to_level(series, peak_index, target):
    """Exact time from a peak to the first moment the linear backlog <= target."""
    times, levels, slopes = series.times, series.levels, series.slopes
    peak_time = times[peak_index]
    for index in range(peak_index, len(times) - 1):
        slope = slopes[index]
        if slope >= 0:
            continue
        level = levels[index]
        if level <= target:
            return times[index] - peak_time
        crossing = times[index] + (target - level) / slope
        if times[index] <= crossing <= times[index + 1]:
            return crossing - peak_time
    if levels and levels[-1] <= target:
        return times[-1] - peak_time
    return None


def detect_excursions(series, peak_floor_bits):
    """Backlog peaks above the preceding p10-p90 band, with exact relax times.

    The backlog is piecewise linear, so peaks sit on breakpoints (an upward jump
    from a ``queue_enter``) and every crossing is solved analytically: tau is
    resolved to the microsecond, with no resampling floor.
    """
    times, levels, slopes = series.times, series.levels, series.slopes
    cache = {}
    episodes = []
    last_peak = -1e9
    for index in range(1, len(levels)):
        peak = levels[index]
        if peak < peak_floor_bits:
            continue
        if peak <= levels[index - 1]:
            continue                      # peaks only at upward jumps
        if slopes[index] >= 0:
            continue                      # must decay afterwards
        moment = times[index]
        if not (EVAL_START_S <= moment < EVAL_END_S):
            continue
        if moment - last_peak < RELAX_MIN_PEAK_SEPARATION_S:
            continue
        p10, p90 = _baseline_band(series, moment, cache)
        if p10 is None or peak <= p90:
            continue
        tau50 = _tau_to_level(series, index, 0.5 * peak)
        tau_band = _tau_to_level(series, index, p90)
        episodes.append({
            "t_peak": moment,
            "peak_mbit": peak / 1e6,
            "baseline_p10_mbit": p10 / 1e6,
            "baseline_p90_mbit": p90 / 1e6,
            "tau_50_ms": None if tau50 is None else 1000.0 * tau50,
            "tau_band_ms": None if tau_band is None else 1000.0 * tau_band,
            "tau_50_s": tau50,
            "tau_band_s": tau_band,
            "peak_bits": peak,
        })
        last_peak = moment
    return episodes


def build_error_relax_comparison(runs, corridor_relax, link_entries, e2e_by_pid,
                                 queues, e2e_by_link):
    """Compare error segments against queue relaxation and against bias size.

    Two splits, both descriptive (no fit, no significance claim):

    * ``group_by_ratio`` - tau_error / tau_relax >= 1 vs < 1.  In this regime
      tau_relax resolves to one packet service time, so the split can be
      degenerate; ``degenerate_ratio_split`` says so explicitly.
    * ``group_by_delta`` - above/below the median |delta| of the segments, which
      stays informative even when the ratio split collapses.
    """
    tau_relax_by_link = {}
    for link, block in corridor_relax.items():
        values = [ep["tau_50_s"] for ep in (block.get("episodes") or [])
                  if ep.get("tau_50_s") is not None]
        if values:
            tau_relax_by_link[link] = statistics.median(values)
    rows = []
    for run in runs:
        link = run["link"]
        tau_relax = tau_relax_by_link.get(link)
        if not tau_relax:
            continue
        series = queues.get(link)
        samples = []
        if series is not None and run["duration_s"] > 0:
            moment = run["t0"]
            while moment < run["t1"]:
                samples.append(series.at(moment))
                moment += BIN_S * 0.5
            samples.append(series.at(run["t1"]))
        slope = ((samples[-1] - samples[0]) / run["duration_s"]
                 if len(samples) >= 2 and run["duration_s"] else None)
        e2e = []
        for at, pid in link_entries.get(link, []):
            if run["t0"] <= at < run["t1"]:
                value = e2e_by_pid.get(int(pid)) if pid is not None else None
                if value is not None:
                    e2e.append(value)
        baseline = e2e_by_link.get(link) or []
        rows.append({
            "link": link,
            "tau_error_s": run["duration_s"],
            "tau_relax_s": tau_relax,
            "ratio": run["duration_s"] / tau_relax,
            "delta_mbps": run["mean_mbps"],
            "queue_slope_mbps": None if slope is None else slope / 1e6,
            "q_level_mbit": statistics.median(samples) / 1e6 if samples else None,
            "e2e_mean_ms": 1000.0 * _mean(e2e) if e2e else None,
            "e2e_vs_link_baseline_ms": (1000.0 * (_mean(e2e) - _mean(baseline)))
            if e2e and baseline else None,
            "n_packets": len(e2e),
        })

    def summarise(group):
        if not group:
            return {"n_segments": 0}
        return {
            "n_segments": len(group),
            "tau_error_s": qstats([row["tau_error_s"] for row in group],
                                  points=(0.5, 0.9)),
            "tau_relax_s": qstats([row["tau_relax_s"] for row in group],
                                  points=(0.5, 0.9)),
            "ratio": qstats([row["ratio"] for row in group], points=(0.5, 0.9)),
            "delta_mbps": qstats([row["delta_mbps"] for row in group]),
            "queue_slope_mbps": qstats([row["queue_slope_mbps"] for row in group
                                        if row["queue_slope_mbps"] is not None]),
            "q_level_mbit": qstats([row["q_level_mbit"] for row in group
                                    if row["q_level_mbit"] is not None]),
            "e2e_mean_ms": _mean([row["e2e_mean_ms"] for row in group
                                  if row["e2e_mean_ms"] is not None]),
            "e2e_vs_link_baseline_ms": _mean(
                [row["e2e_vs_link_baseline_ms"] for row in group
                 if row["e2e_vs_link_baseline_ms"] is not None]),
            "n_packets": sum(row["n_packets"] for row in group),
            "segments": sorted(group, key=lambda r: (-r["ratio"], r["link"]))[:5],
        }

    by_ratio = {">=1": [row for row in rows if row["ratio"] >= 1.0],
                "<1": [row for row in rows if row["ratio"] < 1.0]}
    magnitudes = sorted(row["delta_mbps"] for row in rows)
    median_delta = (statistics.median(magnitudes) if magnitudes else None)
    by_delta = {"high": [row for row in rows
                         if median_delta is not None
                         and abs(row["delta_mbps"]) >= median_delta],
                "low": [row for row in rows
                        if median_delta is not None
                        and abs(row["delta_mbps"]) < median_delta]}
    return {
        "note": (
            "coverage: only error runs on a corridor link that has at least one "
        "relaxation episode can enter this table; see "
        "persistence.error_runs_exact.n_runs_with_relax for the covered count. "
        "descriptive split only: no fit, no significance claim. tau_relax is "
            "the per-link median tau_50 over that link's relaxation episodes "
            "(resolved at event timestamps); tau_error is the run duration at "
            f"|delta| > {DELTA_FRAC_PRIMARY:g}*mu. E2E is measured on packets that "
            "entered the link queue inside the segment; the baseline is every "
            "packet that entered that link at any time in the run."),
        "n_segments_total": len(rows),
        "degenerate_ratio_split": by_ratio["<1"] == [],
        "group_by_ratio": {key: summarise(value) for key, value in by_ratio.items()},
        "group_by_delta": {"median_delta_mbps": median_delta,
                           **{key: summarise(value)
                              for key, value in by_delta.items()}},
    }


def _config_sha(cell):
    try:
        return json.loads((_arms(cell) / "config.json").read_text(
            encoding="utf-8")).get("sha256")
    except (OSError, ValueError):
        return None


def _trace_sha(cell):
    try:
        return json.loads((cell / "trace-manifest.json").read_text(
            encoding="utf-8")).get("source_trace_sha256")
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------- rendering
def fmt(value, pattern="{:.2f}", dash="n/a"):
    if value is None:
        return dash
    if isinstance(value, float) and not math.isfinite(value):
        return "inf"
    try:
        return pattern.format(value)
    except (TypeError, ValueError):
        return str(value)


def digest_report(report):
    """Headline digest used for the combined matrix file.

    The full per-cell aggregates live in ``<cell>.json``; the combined file only
    keeps what a reader needs to compare cells, so the repository does not grow
    with duplicated detail.
    """
    eps = report["epsilon"]
    persistence = report["persistence"]
    comparison = report["tau_error_vs_relax"]

    def strip_groups(block):
        return {key: ({k: v for k, v in value.items() if k != "segments"}
                      if isinstance(value, dict) else value)
                for key, value in block.items()}

    return {
        "schema": "sq-persistence-cell/v1",
        "detail_level": "digest",
        "cell": report["cell"],
        "policy": report["policy"],
        "hysteresis_eta_s": report["hysteresis_eta_s"],
        "ewma_alpha": report["ewma_alpha"],
        "flip": report["flip"],
        "directional_bias": {key: value for key, value in
                             report["directional_bias"].items()
                             if key != "corridor_delta_mbps"},
        "epsilon": {
            "corridor_rho": eps["corridor_rho"],
            "n_saturated_link_bins": eps["n_saturated_link_bins"],
            "n_link_bins": eps["n_link_bins"],
            "eval_epsilon_mbps": eps["eval_epsilon_mbps"],
            "utilization": eps["utilization"],
            "levels": {key: {k: v for k, v in block.items() if k != "links"}
                       for key, block in eps["levels"].items()},
            "offered_overload": eps["offered_overload"],
            "subbin_resolution": eps["subbin_resolution"],
        },
        "z_bins": report["z_bins"],
        "persistence": {
            "flip_runs_by_stream": {k: v for k, v in
                                    persistence["flip_runs_by_stream"].items()
                                    if k != "top_runs"},
            "flip_runs_by_sat_only": {k: v for k, v in
                                      persistence["flip_runs_by_sat_only"].items()
                                      if k != "top_runs"},
            "load_runs": persistence["load_runs"],
            "load_runs_top": persistence["load_runs_top"][:5],
            "error_runs_exact": {key: value for key, value in
                                 persistence["error_runs_exact"].items()
                                 if key != "top_runs"},
            "binned_bias_runs": persistence["binned_bias_runs"],
        },
        "queue_relaxation": {key: value for key, value in
                             report["queue_relaxation"].items()
                             if key != "per_link"},
        "tau_error_vs_relax": {
            "note": comparison["note"],
            "n_segments_total": comparison["n_segments_total"],
            "degenerate_ratio_split": comparison["degenerate_ratio_split"],
            "group_by_ratio": strip_groups(comparison["group_by_ratio"]),
            "group_by_delta": strip_groups(comparison["group_by_delta"]),
        },
        "per_bin_note": ("the 0.5 s bin table lives in the per-cell file "
                         "(<cell>.json -> per_bin) to avoid duplicating it here"),
    }


def _z_table(report, key, title, denominator):
    lines = [title, "",
             "| z bin | n flips | frac | E2E p50 | E2E p95 | E2E p99 | "
             "backlog growth p50 (Mbit/s) | backlog@t p50 (Mbit) | relaxed@+2 s | "
             "top resource |", "|---|---|---|---|---|---|---|---|---|---|"]
    for label in report["z_bins"]["bin_labels"]:
        block = report["z_bins"][key][label]
        top = block["top_resources"][0][0] if block["top_resources"] else "n/a"
        share = block.get("frac_of_flips")
        if share is None:
            share = block.get("frac_of_valid_flips")
        lines.append(f"| {label} | {block['n_flips']} | "
                     f"{fmt(share, '{:.3f}')} | "
                     f"{fmt(block['e2e_ms'].get('p50'))} | "
                     f"{fmt(block['e2e_ms'].get('p95'))} | "
                     f"{fmt(block['e2e_ms'].get('p99'))} | "
                     f"{fmt(block['queue_growth_mbps'].get('p50'))} | "
                     f"{fmt(block['q_at_t_mbit'].get('p50'))} | "
                     f"{fmt(block['frac_relaxed_at_2s'], '{:.3f}')} | {top} |")
    lines.append("")
    return lines


def render_cell_markdown(report):
    flip = report["flip"]
    bias = report["directional_bias"]
    accounting = bias["accounting"]
    eps = report["epsilon"]
    zstat = report["z_bins"]["z_percentiles"]
    zstat_valid = report["z_bins"]["z_percentiles_valid"]
    persistence = report["persistence"]
    exact = persistence["error_runs_exact"]
    stream_runs = persistence["flip_runs_by_stream"]
    relax = report["queue_relaxation"]
    comp = report["tau_error_vs_relax"]
    lines = [f"# Persistence / directional-bias report - `{report['cell']}`", "",
             "**Caliber:** stream key = per-(sat,dst), with dst and bits joined from "
             "`trace.jsonl.gz` by pid (the decision projection drops dst); "
             "`tau_relax` is measured on the backlog **including in-service "
             "remaining bits**; `z` is reported both raw and restricted to "
             f"`epsilon_r >= {fmt(report['z_bins']['epsilon_floor_mbps'])} Mbps`; "
             "`delta` is in bit/s.",
             "",
             f"- policy: `{report['policy']}` "
             f"(eta={report['hysteresis_eta_s']}, alpha={report['ewma_alpha']})",
             f"- flip rate (eval 4-14 s, frozen rule): "
             f"**{fmt(flip['flip_rate_eval'], '{:.4f}')}** "
             f"({flip['n_flips_eval']}/{flip['n_decisions_eval']}); whole-run "
             f"{fmt(flip['flip_rate_all'], '{:.4f}')} "
             f"({flip['n_flips_all']}/{flip['n_decisions_all']})",
             (f"- geometry negative control: flip = 0 by construction "
              f"(check_ok={flip['geometry_check_ok']})"
              if flip["geometry_constructive_zero"]
              else f"- queue-aware policy: {flip['n_flips_eval']} flips in the eval "
                   f"window, {flip['n_flips_all']} over the whole run"),
             "",
             "## Directional bias (delta_r, bit/s)", "",
             f"- sum_r delta_r = **{fmt(accounting['net_sum_mbps'])} Mbps** "
             "(0 by construction; every flip is one + move and one - move)",
             f"- one-way moved load: **{fmt(accounting['one_way_moved_mbps'])} Mbps** "
             f"(= sum|delta_r|/2, the number to quote); sum|delta_r| = "
             f"{fmt(accounting['sum_abs_delta_mbps'])} Mbps counts each moved bit twice",
             f"- positive excess total {fmt(accounting['sum_positive_excess_mbps'])} Mbps, "
             f"negative deficit total {fmt(accounting['sum_negative_deficit_mbps'])} Mbps, "
             f"top-5 excess {fmt(accounting['top5_positive_excess_mbps'])} Mbps",
             f"- resources touched {bias['n_resources_touched']} "
             f"(|delta|>1 Mbps: {bias['n_resources_abs_over_1mbps']}); "
             f"unpaired flips dropped {bias['n_flips_unattributed']}",
             "- caveat: delta is a per-decision attribution of bits moved between the "
             "link that should have been used and the one actually used; it is NOT "
             "end-to-end rerouted net traffic (later hops can correct it).",
             "",
             "| side | link | delta (Mbps) | z |", "|---|---|---|---|"]
    for row in bias["top_excess"][:5]:
        zvalue = row["z"]
        lines.append(f"| excess | {row['link']} | {row['delta_mbps']:+.2f} | "
                     f"{fmt(zvalue if isinstance(zvalue, float) else None, '{:.2f}')} |")
    for row in bias["top_deficit"][:5]:
        lines.append(f"| deficit | {row['link']} | {row['delta_mbps']:+.2f} | - |")
    lines += ["",
              f"Longest same-sign delta run: +{fmt(bias['longest_positive_run_s'])} s / "
              f"-{fmt(bias['longest_negative_run_s'])} s (0.5 s bins).",
              "", "## Epsilon on corridor links", "",
              f"`rho={eps['corridor_rho']:g}` -> {len(eps['corridor_links'])} links; "
              f"saturated 0.5 s link-bins {eps['n_saturated_link_bins']}/"
              f"{eps['n_link_bins']}.", "",
              "| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |",
              "|---|---|---|---|---|---|"]
    for key, block in eps["levels"].items():
        lines.append(f"| {key} | {block['n_links']} | "
                     f"{fmt(block['epsilon_mbps'].get('p50'))} | "
                     f"{fmt(block['epsilon_mbps'].get('p90'))} | "
                     f"{fmt(block['epsilon_mbps'].get('min'))} | "
                     f"{fmt(block['utilization']['max'], '{:.3f}')} |")
    sub = eps["subbin_resolution"]
    lines += ["", f"Sub-bin check at {sub['sub_bin_s']:g} s on the same corridor "
              "(0.5 s attribution bins hide saturation):", "",
              "| window | link-subbins | saturated | frac | served util max | "
              "eps min (Mbps) | offered > mu | offered util max |",
              "|---|---|---|---|---|---|---|---|"]
    for name in ("eval", "burst"):
        block = sub[name]
        lines.append(f"| {name} | {block['n_link_subbins']} | "
                     f"{block['n_saturated_subbins']} | "
                     f"{fmt(block['frac_saturated_subbins'], '{:.4f}')} | "
                     f"{fmt(block['served_util_max'], '{:.4f}')} | "
                     f"{fmt(block['epsilon_min_mbps'], '{:.3f}')} | "
                     f"{block['n_subbins_offered_over_mu']} | "
                     f"{fmt(block['offered_util_max'], '{:.3f}')} |")
    lines += ["", "## z = |delta| / epsilon (flip decisions, eval window)", "",
              "`z` is a normalised bias magnitude, **not** an overload indicator: "
              "`epsilon` is residual capacity after the bits that actually flowed "
              "(misrouted ones included), while `delta` is a counterfactual "
              "bookkeeping swing.", "",
              f"- raw: p50 {fmt(zstat['p50'], '{:.3f}')}, p90 "
              f"{fmt(zstat['p90'], '{:.3f}')}, share>=0.5 "
              f"{fmt(zstat['frac_ge_0_5'], '{:.3f}')}, share>=1 "
              f"{fmt(zstat['frac_ge_1'], '{:.3f}')} (n={zstat['n']})",
              f"- valid (epsilon_r >= {fmt(report['z_bins']['epsilon_floor_mbps'])} Mbps): "
              f"p50 {fmt(zstat_valid['p50'], '{:.3f}')}, p90 "
              f"{fmt(zstat_valid['p90'], '{:.3f}')}, share>=0.5 "
              f"{fmt(zstat_valid['frac_ge_0_5'], '{:.3f}')}, share>=1 "
              f"{fmt(zstat_valid['frac_ge_1'], '{:.3f}')} (n={zstat_valid['n']}; "
              f"excluded: {report['z_bins']['n_flips_excluded_by_floor']} below the "
              f"epsilon floor, {report['z_bins']['n_flips_without_resource']} with no "
              f"endpoint resource)",
              ""]
    lines += _z_table(report, "flip_decisions", "### Raw z bins (all flips)", None)
    lines += _z_table(report, "flip_decisions_valid",
                      "### z_valid bins (epsilon_r >= floor only)",
                      "valid")
    non = report["z_bins"]["non_flip_decisions"]
    lines.append(f"Non-flip contrast row (no delta, hence no z): n={non['n_decisions']}, "
                 f"E2E p50 {fmt(non['e2e_ms'].get('p50'))} ms, backlog growth p50 "
                 f"{fmt(non['queue_growth_mbps'].get('p50'))} Mbit/s, relaxed@+2 s "
                 f"{fmt(non['frac_relaxed_at_2s'], '{:.3f}')}.")
    unattributed = bias["n_flips_unattributed"]
    if unattributed:
        lines.append(f"{unattributed} flips have no pair of endpoint links and are "
                     "excluded from the z bins and from delta.")
    burst = report["z_bins"]["burst_window"]
    lines += ["", f"Burst window {burst['window']}: {burst['n_flips']} flips, "
                  f"z>=1 {burst['n_flips_z_ge_1']}, z>=0.5 "
                  f"{burst['n_flips_z_ge_0_5']}; 0.5 s-bin offered util max "
                  f"{fmt(eps['offered_overload']['burst_offered_util_max'], '{:.3f}')}",
              "", "## Persistence (tau_error on decision timestamps)", "",
              f"- (sat,dst) same-direction flip runs: {stream_runs['n_runs']} "
              f"(>=2 flips: {stream_runs['n_runs_ge2']}, >=3: "
              f"{stream_runs['n_runs_ge3']}); length p50 "
              f"{fmt(stream_runs['length_p50'], '{:.0f}')}, p90 "
              f"{fmt(stream_runs['length_p90'], '{:.0f}')}, max "
              f"{fmt(stream_runs['length_max'], '{:.0f}')}; duration p50 "
              f"{fmt(stream_runs['duration_ms_ge2'].get('p50'))} ms, p90 "
              f"{fmt(stream_runs['duration_ms_ge2'].get('p90'))} ms, max "
              f"{fmt(stream_runs['duration_max_ms_ge2'])} ms "
              "(runs with >=2 flips; single-flip runs have zero duration)",
              f"- per-sat lens (**superseded** Round-1 lens, cross-check only): "
              f"max length {fmt(persistence['flip_runs_by_sat_only']['length_max'], '{:.0f}')}, "
              f"max duration "
              f"{fmt(persistence['flip_runs_by_sat_only']['duration_max_ms_ge2'])} ms",
              f"- per-link persistent-excess runs (|delta_r| > "
              f"{fmt(exact['threshold_mbps'])} Mbps): {exact['n_runs']} total, "
              f"{exact['n_runs_ge2']} with >=2 decisions, "
              f"{exact['n_runs_ge1s']} lasting >=1 s, "
              f"{exact['n_runs_censored']} censored",
              f"- **tau_error**: p50 {fmt(exact['tau_error_s'].get('p50'))} s, "
              f"p90 {fmt(exact['tau_error_s'].get('p90'))} s, max "
              f"{fmt(exact['tau_error_max_s'])} s; decisions per run p50 "
              f"{fmt(exact['count'].get('p50'), '{:.0f}')}", ""]
    if exact["top_runs"]:
        lines += ["| link | t0 (s) | t1 (s) | tau_error (s) | decisions | "
                  "|delta| mean (Mbps) | censored |", "|---|---|---|---|---|---|---|"]
        for run in exact["top_runs"][:8]:
            lines.append(f"| {run['link']} | {run['t0']:.2f} | {run['t1']:.2f} | "
                         f"{fmt(run['duration_s'], '{:.3f}')} | {run['count']} | "
                         f"{fmt(run['mean_mbps'])} | {run['censored']} |")
        lines.append("")
    binned = persistence["binned_bias_runs"]["by_threshold"]
    lines += ["|delta| threshold sensitivity (0.5 s bins, **durations quantized - "
              "not tau_error**):", "",
              "| threshold | runs | censored | in corridor | >=1 s | binned dur p50 (s) "
              "| p90 | max |", "|---|---|---|---|---|---|---|---|"]
    for key, block in binned.items():
        lines.append(f"| {fmt(block['threshold_mbps'])} Mbps | {block['n_runs']} | "
                     f"{block['n_censored']} | {block['n_runs_corridor']} | "
                     f"{block['n_runs_ge_1s']} | "
                     f"{fmt(block['duration_s'].get('p50'))} | "
                     f"{fmt(block['duration_s'].get('p90'))} | "
                     f"{fmt(block['duration_max_s'])} |")
    lines += ["", "## Backlog relaxation (corridor links)", "",
              f"- backlog = queued + in-service remaining; service slot p50 "
              f"{fmt(report['inputs']['backlog_rebuild']['service_duration_ms'].get('p50'))} ms",
              f"- excursions: {relax['n_excursions']} across "
              f"{relax['n_corridor_links']} corridor links "
              f"(peak floor {fmt(relax['peak_floor_mbit'])} Mbit = 2 service slots)",
              f"- tau_50: p50 {fmt(relax['tau_50_ms'].get('p50'))} ms, p90 "
              f"{fmt(relax['tau_50_ms'].get('p90'))} ms",
              f"- tau_band (back to preceding p10-p90): p50 "
              f"{fmt(relax['tau_band_ms'].get('p50'))} ms, p90 "
              f"{fmt(relax['tau_band_ms'].get('p90'))} ms",
              "", "### Error segments vs relaxation / vs bias size", ""]
    header = ("| group | segments | tau_error p50 (s) | tau_relax p50 (s) | ratio p50 "
              "| \\|delta\\| p50 (Mbps) | backlog slope p50 (Mbit/s) | backlog p50 "
              "(Mbit) | E2E mean (ms) | E2E vs link baseline (ms) | packets |")
    rule = "|---|---|---|---|---|---|---|---|---|---|---|"

    def comparison_row(name, block):
        if not block.get("n_segments"):
            return f"| {name} | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 |"
        return (f"| {name} | {block['n_segments']} | "
                f"{fmt(block['tau_error_s'].get('p50'), '{:.3f}')} | "
                f"{fmt(block['tau_relax_s'].get('p50'), '{:.4f}')} | "
                f"{fmt(block['ratio'].get('p50'))} | "
                f"{fmt(block['delta_mbps'].get('p50'))} | "
                f"{fmt(block['queue_slope_mbps'].get('p50'))} | "
                f"{fmt(block['q_level_mbit'].get('p50'))} | "
                f"{fmt(block['e2e_mean_ms'])} | "
                f"{fmt(block['e2e_vs_link_baseline_ms'])} | {block['n_packets']} |")

    lines += ["Split by tau_error / tau_relax"
              + (" - **degenerate in this cell** (no segment has ratio < 1)"
                 if comp["degenerate_ratio_split"] and comp["n_segments_total"]
                 else "") + ":",
              "", header, rule]
    for key in (">=1", "<1"):
        lines.append(comparison_row(f"ratio {key}", comp["group_by_ratio"][key]))
    lines += ["", "Split by |delta| (median "
              f"{fmt(comp['group_by_delta']['median_delta_mbps'])} Mbps):", "",
              header, rule]
    for key in ("high", "low"):
        lines.append(comparison_row(f"|delta| {key}", comp["group_by_delta"][key]))
    lines.append("")
    return "\n".join(lines) + "\n"


def render_matrix_markdown(reports, label):
    lines = [f"# Directional persistence - `{label}` ({len(reports)} cells)", "",
             "**Caliber:** per-(sat,dst) streams (dst/bits joined from `trace.jsonl.gz`); "
             "`tau_relax` includes in-service remaining bits; `z` is reported raw and "
             "with an epsilon floor; `delta` is bit/s per-decision attribution.",
             "",
             "Diagnostic only. flip = truth-substituted same-decision re-ranking "
             "(`sq_policy_analyze.py` rule); geometry flip = 0 by construction. "
             "Windows: eval 4-14 s, bins 0.5 s, burst 8-8.5 s.", "",
             "## Headline", "",
             "| cell | policy | flip rate (eval) | one-way moved Mbps | eps p50 corridor "
             "Mbps | z>=0.5 raw | z>=1 raw | z>=0.5 valid | z>=1 valid | max flip run "
             "| tau_error p50 s | tau_relax p50 ms |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for report in reports:
        flip = report["flip"]
        bias = report["directional_bias"]
        eps = report["epsilon"]["levels"][f"rho={report['epsilon']['corridor_rho']:g}"]
        raw = report["z_bins"]["z_percentiles"]
        valid = report["z_bins"]["z_percentiles_valid"]
        runs = report["persistence"]["error_runs_exact"]
        relax = report["queue_relaxation"]["tau_50_ms"]
        lines.append(
            f"| {report['cell']} | {report['policy']} | "
            f"{fmt(flip['flip_rate_eval'], '{:.4f}')} | "
            f"{fmt(bias['accounting']['one_way_moved_mbps'])} | "
            f"{fmt(eps['epsilon_mbps'].get('p50'))} | "
            f"{fmt(raw['frac_ge_0_5'], '{:.3f}')} | "
            f"{fmt(raw['frac_ge_1'], '{:.3f}')} | "
            f"{fmt(valid['frac_ge_0_5'], '{:.3f}')} | "
            f"{fmt(valid['frac_ge_1'], '{:.3f}')} | "
            f"{fmt(report['persistence']['flip_runs_by_stream']['length_max'], '{:.0f}')} | "
            f"{fmt(runs['tau_error_s'].get('p50'))} | "
            f"{fmt(relax.get('p50'))} |")
    lines += ["", "## z bin share of flips (raw | valid)", "",
              "| cell | set | " + " | ".join(Z_BIN_LABELS) + " | n |",
              "|---|---|" + "---|" * len(Z_BIN_LABELS) + "---|"]
    for report in reports:
        for key, name in (("flip_decisions", "raw"),
                          ("flip_decisions_valid", "valid")):
            table = report["z_bins"][key]
            values = []
            for label in Z_BIN_LABELS:
                share = table[label].get("frac_of_flips")
                if share is None:
                    share = table[label].get("frac_of_valid_flips")
                values.append(fmt(share, "{:.3f}"))
            total = sum(table[label]["n_flips"] for label in Z_BIN_LABELS)
            lines.append(f"| {report['cell']} | {name} | " + " | ".join(values)
                         + f" | {total} |")
    lines += ["", "## Directional bias and persistence", "",
              "| cell | sum_r \\|delta_r\\| Mbps | positive excess Mbps | "
              "top excess link | longest +run s | error runs >=1 s | tau_error max s | "
              "tau_relax p90 ms |", "|---|---|---|---|---|---|---|---|"]
    for report in reports:
        bias = report["directional_bias"]
        acc = bias["accounting"]
        runs = report["persistence"]["error_runs_exact"]
        relax = report["queue_relaxation"]["tau_50_ms"]
        top = bias["top_excess"][0] if bias["top_excess"] else None
        lines.append(
            f"| {report['cell']} | {fmt(acc['sum_abs_delta_mbps'])} | "
            f"{fmt(acc['sum_positive_excess_mbps'])} | "
            f"{top['link'] + ' ' + format(top['delta_mbps'], '+.1f') if top else 'n/a'} | "
            f"{fmt(bias['longest_positive_run_s'])} | {runs['n_runs_ge1s']} | "
            f"{fmt(runs['tau_error_max_s'])} | {fmt(relax.get('p90'))} |")
    lines += ["", "## Per-cell detail", ""]
    for report in reports:
        lines.append(f"- [{report['cell']}](./{report['cell']}.md)")
    lines.append("")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------- output
def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, sort_keys=True)
        fh.write("\n")


def selfcheck(cell_paths, out=None):
    """Verify the flip rule against the frozen reference analyzer.

    For every cell, recompute the flip decision set with
    ``sq_policy_analyze.load_decisions`` + ``_fresh_action`` and compare it to
    the set this script derives.  This is the only cross-check that matters for
    the delta/persistence numbers: if the two flip sets are identical, delta_r
    and every downstream statistic are built on the frozen semantics.
    """
    from pathlib import Path
    import importlib.util
    ref_path = Path(__file__).with_name("sq_policy_analyze.py")
    spec = importlib.util.spec_from_file_location("sq_policy_analyze_ref", ref_path)
    reference = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reference)

    rows_out = []
    failures = 0
    for cell in cell_paths:
        config = load_config(cell)
        policy = (config.get("time_alignment") or {}).get("state_policy")
        ref_flips = set()
        for row in reference.load_decisions(cell):
            if len(row["j_truth"]) < 2:
                continue
            fresh = reference._fresh_action(row, policy)
            if row["chosen"] != fresh:
                ref_flips.add((row["decision_id"], float(row["t"])))
        ref_eval = {(key, moment) for key, moment in ref_flips
                    if EVAL_START_S <= moment < EVAL_END_S}
        probe, _ = load_probe(cell)
        bits, dst = load_packet_bits(cell)
        _, rows = collect_decisions(cell, probe, bits, dst, policy, lambda _: None)
        mine = {(row["decision_id"], row["t"]) for row in rows if row["flip"]}
        ok = ref_eval == mine
        failures += 0 if ok else 1
        rows_out.append({"cell": getattr(cell, "name", str(cell)),
                         "policy": policy,
                         "reference_flips_whole_run": len(ref_flips),
                         "reference_flips_eval": len(ref_eval),
                         "recomputed_flips_eval": len(mine),
                         "identical": ok,
                         "only_in_reference": len(ref_eval - mine),
                         "only_in_recomputed": len(mine - ref_eval)})
        print(f"{rows_out[-1]['cell']:18s} policy={policy:26s} "
              f"ref_eval={len(ref_eval):5d} mine={len(mine):5d} identical={ok}",
              file=sys.stderr)
    payload = {"schema": "sq-persistence-selfcheck/v1",
               "reference": "ANALYSIS/SIM-QUAL-20261031/sq_policy_analyze.py "
                            "(load_decisions + _fresh_action, unmodified semantics)",
               "eval_window_s": [EVAL_START_S, EVAL_END_S],
               "cells": rows_out, "all_identical": failures == 0}
    if out:
        write_json(Path(out), payload)
    print(json.dumps(payload, indent=1, sort_keys=True))
    return 0 if failures == 0 else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="directional / persistence analysis of the stale-state matrix")
    parser.add_argument("command", choices=["cell", "matrix", "selfcheck"])
    parser.add_argument("cells", nargs="+", help="cell directories (…/<cell>/)")
    parser.add_argument("--json", help="JSON output path (cell mode)")
    parser.add_argument("--md", help="Markdown output path (cell mode)")
    parser.add_argument("--out-dir", help="output directory for matrix mode")
    parser.add_argument("--label", default="matrix", help="matrix label")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--check-json", help="JSON path for selfcheck")
    args = parser.parse_args(argv)
    from pathlib import Path
    cell_paths = [Path(value) for value in args.cells]
    for path in cell_paths:
        if not (_arms(path) / "decisions.jsonl.gz").exists():
            parser.error("not a cell directory (missing arms/now/decisions.jsonl.gz): "
                         f"{path}")

    if args.command == "selfcheck":
        raise SystemExit(selfcheck(cell_paths, out=args.check_json))

    if args.command == "cell":
        report = analyze_cell(cell_paths[0], verbose=not args.quiet)
        if args.json:
            write_json(Path(args.json), report)
        if args.md:
            Path(args.md).write_text(render_cell_markdown(report), encoding="utf-8")
        print(json.dumps({key: report[key] for key in
                          ("cell", "policy", "flip", "directional_bias", "epsilon")},
                         indent=1, sort_keys=True))
        return

    reports = [analyze_cell(path, verbose=not args.quiet) for path in cell_paths]
    out_dir = Path(args.out_dir or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        write_json(out_dir / f"{report['cell']}.json", report)
        (out_dir / f"{report['cell']}.md").write_text(
            render_cell_markdown(report), encoding="utf-8")
    combined = {
        "schema": "sq-persistence-matrix/v1",
        "label": args.label,
        "n_cells": len(reports),
        "cell_order": [report["cell"] for report in reports],
        "protocol": {"eval_window_s": [EVAL_START_S, EVAL_END_S],
                     "bin_s": BIN_S,
                     "burst_window_s": [BURST_START_S, BURST_END_S],
                     "z_bins": Z_BIN_LABELS,
                     "delta_frac_primary": DELTA_FRAC_PRIMARY,
                     "corridor_rho": RHO_PRIMARY,
                     "flip_rule": "truth-substituted same-decision re-ranking, identical to sq_policy_analyze.py"},
        "cells": {report["cell"]: digest_report(report) for report in reports},
    }
    write_json(out_dir / f"{args.label}-persistence.json", combined)
    (out_dir / f"{args.label}-persistence.md").write_text(
        render_matrix_markdown(reports, args.label), encoding="utf-8")
    print(json.dumps({"label": args.label,
                      "cells": [report["cell"] for report in reports],
                      "flip_rate_eval": {report["cell"]:
                                         report["flip"]["flip_rate_eval"]
                                         for report in reports},
                      "misrouted_mbps": {report["cell"]:
                                         report["directional_bias"]["total_misrouted_mbps"]
                                         for report in reports}},
                     indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
