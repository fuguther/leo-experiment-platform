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
RELAX_PEAK_FLOOR_BITS = 1.0e6        # at least one 1 Mbit packet deep
RELAX_MIN_PEAK_SEPARATION_S = 0.2
RELAX_GRID_S = 0.001
EPS_FLOOR_BPS = 1.0


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


class QueueSeries:
    """Step function Q(t) = bits queued on one directed link at time t."""

    __slots__ = ("times", "levels")

    def __init__(self, times, levels):
        self.times = times
        self.levels = levels

    def at(self, moment):
        index = bisect.bisect_right(self.times, moment) - 1
        return self.levels[index] if index >= 0 else 0.0

    def max_between(self, t0, t1):
        peak = max(self.at(t0), 0.0)
        lo = bisect.bisect_right(self.times, t0)
        hi = bisect.bisect_right(self.times, t1)
        for index in range(lo, hi):
            peak = max(peak, self.levels[index])
        return peak

    def grid(self, step=RELAX_GRID_S, t0=EVAL_START_S, t1=EVAL_END_S):
        out = []
        moment = t0
        while moment < t1 + 1e-12:
            out.append(self.at(moment))
            moment += step
        return out


def build_queues(result, bits_by_pid):
    """link -> QueueSeries from queue_enter / service_start pairs.

    ``queue_id`` is a per-enqueue instance id (in p1-high-greedy there are
    25627 distinct queue_ids for 25627 queue_enter rows), so it pairs each
    enqueue with exactly one service_start; occupancy runs from the enqueue
    until that packet starts service.
    """
    enqueued = {}
    for event in result.get("packet_events") or []:
        if event.get("kind") == "queue_enter" and event.get("queue") == "isl":
            at = _safe_float(event.get("at"))
            if at is not None:
                enqueued[event.get("queue_id")] = (at, event.get("link_id"),
                                                  event.get("pid"))
    deltas = defaultdict(list)
    for event in result.get("packet_events") or []:
        if event.get("kind") != "service_start" or event.get("stage") != "isl":
            continue
        start = _safe_float(event.get("at"))
        item = enqueued.pop(event.get("queue_id"), None)
        if item is None or start is None:
            continue
        at, link, pid = item
        size = float(bits_by_pid.get(pid) or 0.0)
        deltas[link].append((at, size))
        deltas[link].append((start, -size))
    queues = {}
    for link, events in deltas.items():
        events.sort()
        times, levels, running = [], [], 0.0
        for at, delta in events:
            running += delta
            times.append(at)
            levels.append(running)
        queues[link] = QueueSeries(times, levels)
    return queues, {"unmatched_queue_enter": len(enqueued)}


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
              "flip_bits_eval": 0.0, "flips_burst": 0}
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
        totals["eval"] += 1
        if len(j_truth) >= 2:
            totals["eval_with_truth"] += 1
        if flipped:
            totals["flips_eval"] += 1
            totals["flip_bits_eval"] += size
            if BURST_START_S <= record["t"] < BURST_END_S:
                totals["flips_burst"] += 1
        rows.append({
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
    queues, queue_meta = build_queues(result, bits_by_pid)
    e2e_by_pid = packet_e2e(result)
    eps, served_per_bin = epsilon_table(windows_by_link, mu_bps)
    offered = offered_table(link_entries, bits_by_pid)
    totals, rows = collect_decisions(cell, probe, bits_by_pid, dst_by_pid,
                                     cell_policy, log)

    # ------------------------------------------------------ delta per bin
    delta_bits = [defaultdict(float) for _ in range(N_BINS)]
    for row in rows:
        if not row["flip"]:
            continue
        if row["chosen_resource"]:
            delta_bits[row["bin"]][row["chosen_resource"]] += row["bits"]
        if row["fresh_resource"]:
            delta_bits[row["bin"]][row["fresh_resource"]] -= row["bits"]
    delta_bps = []
    for index in range(N_BINS):
        width = bin_bounds(index)[1] - bin_bounds(index)[0]
        delta_bps.append({link: value / width for link, value in delta_bits[index].items()})

    # ------------------------------------------------ z per flip decision
    def decision_z(row):
        link = row["chosen_resource"]
        if link is None:
            return None, None, None
        delta = delta_bps[row["bin"]].get(link, 0.0)
        eps_link = eps[row["bin"]].get(link, mu_bps)
        if eps_link <= EPS_FLOOR_BPS:
            return math.inf, delta, eps_link
        return abs(delta) / eps_link, delta, eps_link

    # --------------------------------------------------- aggregate tables
    zbin = {label: {"decisions": 0, "flips": 0, "e2e": [], "slope_bps": [],
                    "q_at_t": [], "q_at_t2": [], "q_peak_next2": [],
                    "relaxed": 0, "resources": defaultdict(int)}
            for label in Z_BIN_LABELS}
    zbin_nonflip = {"decisions": 0, "e2e": [], "slope_bps": [], "q_at_t": [],
                    "relaxed": 0}
    zbin_burst = {label: {"decisions": 0, "flips": 0, "e2e": []}
                  for label in Z_BIN_LABELS}
    per_bin = [{"decisions": 0, "flips": 0, "flip_bits": 0.0, "e2e": [],
                "slope_bps": [], "q_level": [], "z": []} for _ in range(N_BINS)]
    z_all = []

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
            z, delta, eps_link = decision_z(row)
            z_all.append(z)
            block["z"].append(z)
            label = z_bin_label(z)
            if label is None:
                continue
            entry = zbin[label]
            entry["decisions"] += 1
            entry["flips"] += 1
            entry["e2e"].append(e2e)
            entry["slope_bps"].append(slope)
            entry["q_at_t"].append(q_t)
            entry["q_at_t2"].append(q_t2)
            entry["q_peak_next2"].append(q_peak)
            entry["relaxed"] += 1 if relaxed else 0
            entry["resources"][link] += 1
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
            "links": corridor,
        }
    corridor_count = max(1, int(round(RHO_PRIMARY * active_links))) if active_links else 0
    corridor = [link for link, _ in ranked[:corridor_count]]
    corridor_set = set(corridor)

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

    # ---------------------------------------------------- delta summary
    net = defaultdict(float)
    for table in delta_bps:
        for link, value in table.items():
            net[link] += value / N_BINS
    eval_delta = dict(net)
    absolute_bps = sum(abs(value) for value in eval_delta.values()) / 2.0
    delta_ranked = sorted(eval_delta.items(), key=lambda kv: (-abs(kv[1]), kv[0]))
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
        episodes = detect_excursions(series)
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
                          if key != "peak_bits"} for ep in episodes[:10]],
        })
        corridor_relax[link] = block
    log(f"corridor links={len(corridor)} excursions={len(all_tau50)}")

    comparison = build_error_relax_comparison(primary_runs, corridor_relax,
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
            "flip": "truth-substituted same-decision re-ranking (same rule as sq_policy_analyze.py); geometry has no state term -> flip = 0 by construction",
            "flip_rate": "flips / multi-candidate scored forward decisions inside the window",
            "persistence_stream": "same-(sat,dst) decision stream, dst recovered from trace.jsonl.gz by pid because decisions.jsonl.gz has no dst field",
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
            "queue_rebuild": queue_meta,
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
            "total_misrouted_mbps": absolute_bps / 1e6,
            "net_sum_mbps": sum(eval_delta.values()) / 1e6,
            "n_resources_touched": len(eval_delta),
            "n_resources_abs_over_1mbps": sum(1 for value in eval_delta.values()
                                              if abs(value) > 1e6),
            "top_excess": delta_rows(delta_ranked, z_full, +1, 10),
            "top_deficit": delta_rows(delta_ranked, z_full, -1, 10),
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
        },
        "z_bins": {
            "bin_labels": Z_BIN_LABELS,
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
                                            key=lambda kv: (-kv[1], kv[0]))[:5],
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
        },
        "persistence": {
            "flip_runs_by_stream": run_summary(runs_by_stream),
            "flip_runs_by_sat_only": run_summary(runs_by_sat),
            "load_runs": {key: {k: v for k, v in block.items() if k != "runs"}
                          for key, block in load_runs.items()},
            "load_runs_top": primary_runs[:15],
        },
        "queue_relaxation": {
            "n_corridor_links": len(corridor),
            "n_excursions": len(all_tau50),
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


def delta_rows(ranked, z_full, sign, count):
    selected = ranked[:count] if sign > 0 else ranked[-count:][::-1]
    out = []
    for link, value in selected:
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
    return {
        "n_runs": len(runs),
        "n_runs_ge2": sum(1 for n in lengths if n >= 2),
        "n_runs_ge3": sum(1 for n in lengths if n >= 3),
        "length_p50": qstats(lengths, points=(0.5, 0.9)).get("p50"),
        "length_p90": qstats(lengths, points=(0.9,)).get("p90"),
        "length_max": max(lengths) if lengths else None,
        "duration_ms": qstats(durations, points=(0.5, 0.9, 0.95), scale=1000.0),
        "duration_max_ms": (1000.0 * max(durations)) if durations else None,
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


def detect_excursions(series):
    """Queue peaks above the p10-p90 band of the preceding 2 s, plus relax times."""
    grid = series.grid()
    step = RELAX_GRID_S
    total = len(grid)
    episodes = []
    last_peak = -1e9
    for index in range(2, total - 1):
        if not (grid[index] >= grid[index - 1] and grid[index] > grid[index + 1]):
            continue
        if grid[index] < RELAX_PEAK_FLOOR_BITS:
            continue
        moment = EVAL_START_S + index * step
        if moment - last_peak < RELAX_MIN_PEAK_SEPARATION_S:
            continue
        lo = max(0, index - int(RELAX_BASE_WINDOW_S / step))
        baseline = sorted(grid[lo:index])
        if len(baseline) < 10:
            continue
        p10 = baseline[int(0.10 * (len(baseline) - 1))]
        p90 = baseline[int(0.90 * (len(baseline) - 1))]
        if grid[index] <= p90:
            continue
        peak = grid[index]
        tau50 = tau_band = None
        for later in range(index + 1, total):
            level = grid[later]
            elapsed = (later - index) * step
            if tau50 is None and level <= 0.5 * peak:
                tau50 = elapsed
            if level <= p90:
                tau_band = elapsed
                break
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
    """Split error segments by tau_error/tau_relax >= 1 and compare E2E/queue."""
    tau_relax_by_link = {}
    for link, block in corridor_relax.items():
        values = [ep["tau_50_s"] for ep in (block.get("episodes") or [])
                  if ep.get("tau_50_s") is not None]
        if values:
            tau_relax_by_link[link] = statistics.median(values)
    groups = {">=1": [], "<1": []}
    for run in runs:
        link = run["link"]
        tau_relax = tau_relax_by_link.get(link)
        if not tau_relax:
            continue
        ratio = run["duration_s"] / tau_relax
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
        groups[">=1" if ratio >= 1.0 else "<1"].append({
            "link": link,
            "tau_error_s": run["duration_s"],
            "tau_relax_s": tau_relax,
            "ratio": ratio,
            "delta_mbps": run["mean_mbps"],
            "queue_slope_mbps": None if slope is None else slope / 1e6,
            "q_level_mbit": statistics.median(samples) / 1e6 if samples else None,
            "e2e_mean_ms": 1000.0 * _mean(e2e) if e2e else None,
            "e2e_vs_link_baseline_ms": (1000.0 * (_mean(e2e) - _mean(baseline)))
            if e2e and baseline else None,
            "n_packets": len(e2e),
        })
    out = {}
    for key, rows in groups.items():
        out[key] = {
            "n_segments": len(rows),
            "tau_error_s": qstats([row["tau_error_s"] for row in rows],
                                  points=(0.5, 0.9)),
            "tau_relax_s": qstats([row["tau_relax_s"] for row in rows],
                                  points=(0.5, 0.9)),
            "ratio": qstats([row["ratio"] for row in rows], points=(0.5, 0.9)),
            "delta_mbps": qstats([row["delta_mbps"] for row in rows]),
            "queue_slope_mbps": qstats([row["queue_slope_mbps"] for row in rows
                                        if row["queue_slope_mbps"] is not None]),
            "q_level_mbit": qstats([row["q_level_mbit"] for row in rows
                                    if row["q_level_mbit"] is not None]),
            "e2e_mean_ms": _mean([row["e2e_mean_ms"] for row in rows
                                  if row["e2e_mean_ms"] is not None]),
            "e2e_vs_link_baseline_ms": _mean(
                [row["e2e_vs_link_baseline_ms"] for row in rows
                 if row["e2e_vs_link_baseline_ms"] is not None]),
            "n_packets": sum(row["n_packets"] for row in rows),
            "segments": sorted(rows, key=lambda r: (-r["ratio"], r["link"]))[:10],
        }
    out["note"] = (
        "descriptive split only: no fit, no significance claim. tau_relax is the "
        "per-link median tau_50 over that link's relaxation episodes; tau_error is "
        f"the run duration at |delta| > {DELTA_FRAC_PRIMARY:g}*mu. E2E is measured "
        "on packets that entered the link queue inside the segment; the baseline is "
        "all packets that entered that link at any time in the run.")
    return out


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


def render_cell_markdown(report):
    flip = report["flip"]
    bias = report["directional_bias"]
    eps = report["epsilon"]
    zstat = report["z_bins"]["z_percentiles"]
    lines = [f"# Persistence / directional-bias report - `{report['cell']}`", "",
             f"- policy: `{report['policy']}` "
             f"(eta={report['hysteresis_eta_s']}, alpha={report['ewma_alpha']})",
             f"- flip rate (eval 4-14 s, frozen rule): "
             f"**{fmt(flip['flip_rate_eval'], '{:.4f}')}** "
             f"({flip['n_flips_eval']}/{flip['n_decisions_eval']}); "
             f"whole-run flip rate {fmt(flip['flip_rate_all'], '{:.4f}')} "
             f"({flip['n_flips_all']}/{flip['n_decisions_all']})",
             f"- geometry constructive zero: {flip['geometry_constructive_zero']} "
             f"(check_ok={flip['geometry_check_ok']})",
             f"- directional bias: |delta| total "
             f"**{fmt(bias['total_misrouted_mbps'])} Mbps**; net sum "
             f"{fmt(bias['net_sum_mbps'])} Mbps (0 by construction); "
             f"resources touched {bias['n_resources_touched']} "
             f"(>1 Mbps: {bias['n_resources_abs_over_1mbps']})",
             f"- longest same-sign run: +{fmt(bias['longest_positive_run_s'])} s / "
             f"-{fmt(bias['longest_negative_run_s'])} s",
             f"- z = |delta|/epsilon: p50 {fmt(zstat['p50'], '{:.3f}')}, "
             f"p90 {fmt(zstat['p90'], '{:.3f}')}, max bin share (>=1) "
             f"{fmt(zstat['frac_ge_1'], '{:.3f}')}",
             "",
             "## Epsilon on corridor links",
             "",
             f"`rho={eps['corridor_rho']:g}` -> {len(eps['corridor_links'])} links; "
             f"saturated link-bins {eps['n_saturated_link_bins']}/"
             f"{eps['n_link_bins']}; offered load above mu in "
             f"{eps['offered_overload']['n_link_bins_offered_over_mu']}/"
             f"{eps['offered_overload']['n_link_bins']} corridor link-bins.",
             "",
             "| rho | n links | eps p50 (Mbps) | eps p90 | eps min | max util |",
             "|---|---|---|---|---|---|"]
    for key, block in eps["levels"].items():
        lines.append(f"| {key} | {block['n_links']} | "
                     f"{fmt(block['epsilon_mbps'].get('p50'))} | "
                     f"{fmt(block['epsilon_mbps'].get('p90'))} | "
                     f"{fmt(block['epsilon_mbps'].get('min'))} | "
                     f"{fmt(block['utilization']['max'], '{:.3f}')} |")
    lines += ["", "## z = |delta| / epsilon bins (flip decisions, eval window)", "",
              "| z bin | n flips | frac of flips | E2E p50 | E2E p95 | E2E p99 | "
              "queue growth p50 (Mbit/s) | Q@t p50 (Mbit) | relaxed@+2 s | "
              "top resource |", "|---|---|---|---|---|---|---|---|---|---|"]
    for label in report["z_bins"]["bin_labels"]:
        block = report["z_bins"]["flip_decisions"][label]
        top = block["top_resources"][0][0] if block["top_resources"] else "n/a"
        lines.append(f"| {label} | {block['n_flips']} | "
                     f"{fmt(block['frac_of_flips'], '{:.3f}')} | "
                     f"{fmt(block['e2e_ms'].get('p50'))} | "
                     f"{fmt(block['e2e_ms'].get('p95'))} | "
                     f"{fmt(block['e2e_ms'].get('p99'))} | "
                     f"{fmt(block['queue_growth_mbps'].get('p50'))} | "
                     f"{fmt(block['q_at_t_mbit'].get('p50'))} | "
                     f"{fmt(block['frac_relaxed_at_2s'], '{:.3f}')} | {top} |")
    non = report["z_bins"]["non_flip_decisions"]
    lines.append(f"| non-flip (no delta) | 0 | 0 | "
                 f"{fmt(non['e2e_ms'].get('p50'))} | "
                 f"{fmt(non['e2e_ms'].get('p95'))} | "
                 f"{fmt(non['e2e_ms'].get('p99'))} | "
                 f"{fmt(non['queue_growth_mbps'].get('p50'))} | "
                 f"{fmt(non['q_at_t_mbit'].get('p50'))} | "
                 f"{fmt(non['frac_relaxed_at_2s'], '{:.3f}')} | "
                 f"n={non['n_decisions']} |")
    burst = report["z_bins"]["burst_window"]
    lines += ["", f"Burst window {burst['window']}: {burst['n_flips']} flips, "
                  f"z>=1 {burst['n_flips_z_ge_1']}, z>=0.5 "
                  f"{burst['n_flips_z_ge_0_5']}; per bin "
                  + ", ".join(f"{label}={block['flips']}"
                              for label, block in burst["bins"].items()
                              if block["flips"])
                  + f"; offered util max "
                    f"{fmt(report['epsilon']['offered_overload']['burst_offered_util_max'], '{:.3f}')}",
              "", "## Persistence", ""]
    runs = report["persistence"]["flip_runs_by_stream"]
    lines += [
        f"- same-(sat,dst) same-direction flip runs: {runs['n_runs']} "
        f"(>=2 flips: {runs['n_runs_ge2']}, >=3: {runs['n_runs_ge3']}); "
        f"length p50={fmt(runs['length_p50'], '{:.0f}')} "
        f"p90={fmt(runs['length_p90'], '{:.0f}')} "
        f"max={fmt(runs['length_max'], '{:.0f}')}; duration p50="
        f"{fmt(runs['duration_ms'].get('p50'))} ms, max="
        f"{fmt(runs['duration_max_ms'])} ms",
        f"- sat-only grouping (comparable to RESULTS `W`): max length "
        f"{fmt(report['persistence']['flip_runs_by_sat_only']['length_max'], '{:.0f}')}, "
        f"duration max "
        f"{fmt(report['persistence']['flip_runs_by_sat_only']['duration_max_ms'])} ms",
        "", "| \\|delta\\| threshold | runs | censored | in corridor | >=1 s | "
        "tau_error p50 (s) | p90 | max |", "|---|---|---|---|---|---|---|---|"]
    for key, block in report["persistence"]["load_runs"].items():
        lines.append(f"| {block['threshold_mbps']:g} Mbps | {block['n_runs']} | "
                     f"{block['n_censored']} | {block['n_runs_corridor']} | "
                     f"{block['n_runs_ge_1s']} | "
                     f"{fmt(block['duration_s'].get('p50'))} | "
                     f"{fmt(block['duration_s'].get('p90'))} | "
                     f"{fmt(block['duration_max_s'])} |")
    relax = report["queue_relaxation"]
    lines += ["", "## Queue relaxation (corridor links)", "",
              f"- excursions detected: {relax['n_excursions']} across "
              f"{relax['n_corridor_links']} corridor links",
              f"- tau_50: p50 {fmt(relax['tau_50_ms'].get('p50'))} ms, "
              f"p90 {fmt(relax['tau_50_ms'].get('p90'))} ms",
              f"- tau_band (back into preceding-2 s p10-p90): p50 "
              f"{fmt(relax['tau_band_ms'].get('p50'))} ms, p90 "
              f"{fmt(relax['tau_band_ms'].get('p90'))} ms",
              "", "### Error segment duration vs relaxation time", "",
              "| tau_error/tau_relax | segments | tau_error p50 (s) | "
              "tau_relax p50 (s) | \\|delta\\| p50 (Mbps) | queue slope p50 "
              "(Mbit/s) | Q level p50 (Mbit) | E2E mean (ms) | E2E vs link "
              "baseline (ms) | packets |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for key in (">=1", "<1"):
        block = report["tau_error_vs_relax"][key]
        lines.append(
            f"| {key} | {block['n_segments']} | "
            f"{fmt(block['tau_error_s'].get('p50'))} | "
            f"{fmt(block['tau_relax_s'].get('p50'))} | "
            f"{fmt(block['delta_mbps'].get('p50'))} | "
            f"{fmt(block['queue_slope_mbps'].get('p50'))} | "
            f"{fmt(block['q_level_mbit'].get('p50'))} | "
            f"{fmt(block['e2e_mean_ms'])} | "
            f"{fmt(block['e2e_vs_link_baseline_ms'])} | {block['n_packets']} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def render_matrix_markdown(reports, label):
    lines = [f"# Directional persistence - `{label}` ({len(reports)} cells)", "",
             "Diagnostic only. flip = truth-substituted same-decision re-ranking",
             "(`sq_policy_analyze.py` rule); geometry flip = 0 by construction.",
             "Windows: eval 4-14 s, bins 0.5 s, burst 8-8.5 s listed separately.",
             "", "## Headline", "",
             "| cell | policy | flip rate (eval) | \\|delta\\| total Mbps | "
             "eps p50 corridor Mbps | z>=0.5 share | z>=1 share | max flip run | "
             "tau_error p50 s | tau_relax p50 ms |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for report in reports:
        flip = report["flip"]
        bias = report["directional_bias"]
        rho_key = f"rho={report['epsilon']['corridor_rho']:g}"
        eps = report["epsilon"]["levels"][rho_key]
        zstat = report["z_bins"]["z_percentiles"]
        runs = report["persistence"]["flip_runs_by_stream"]
        load = report["persistence"]["load_runs"][f"{DELTA_FRAC_PRIMARY:g}"]
        relax = report["queue_relaxation"]["tau_50_ms"]
        lines.append(
            f"| {report['cell']} | {report['policy']} | "
            f"{fmt(flip['flip_rate_eval'], '{:.4f}')} | "
            f"{fmt(bias['total_misrouted_mbps'])} | "
            f"{fmt(eps['epsilon_mbps'].get('p50'))} | "
            f"{fmt(zstat['frac_ge_0_5'], '{:.3f}')} | "
            f"{fmt(zstat['frac_ge_1'], '{:.3f}')} | "
            f"{fmt(runs['length_max'], '{:.0f}')} | "
            f"{fmt(load['duration_s'].get('p50'))} | "
            f"{fmt(relax.get('p50'))} |")
    lines += ["", "## z bin share of flips", "",
              "| cell | " + " | ".join(Z_BIN_LABELS) + " | n flips |",
              "|---|" + "---|" * len(Z_BIN_LABELS) + "---|"]
    for report in reports:
        zbin = report["z_bins"]["flip_decisions"]
        cells = [fmt(zbin[label]["frac_of_flips"], "{:.3f}") for label in Z_BIN_LABELS]
        lines.append(f"| {report['cell']} | " + " | ".join(cells)
                     + f" | {report['flip']['n_flips_eval']} |")
    lines += ["", "## Top excess / deficit resources (whole eval window)", ""]
    for report in reports:
        bias = report["directional_bias"]
        excess = ", ".join(f"{row['link']} {row['delta_mbps']:+.1f}"
                           for row in bias["top_excess"][:5])
        deficit = ", ".join(f"{row['link']} {row['delta_mbps']:+.1f}"
                            for row in bias["top_deficit"][:5])
        lines += [f"**{report['cell']}**  ",
                  f"- excess (Mbps): {excess}  ",
                  f"- deficit (Mbps): {deficit}", ""]
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="directional / persistence analysis of the stale-state matrix")
    parser.add_argument("command", choices=["cell", "matrix"])
    parser.add_argument("cells", nargs="+", help="cell directories (…/<cell>/)")
    parser.add_argument("--json", help="JSON output path (cell mode)")
    parser.add_argument("--md", help="Markdown output path (cell mode)")
    parser.add_argument("--out-dir", help="output directory for matrix mode")
    parser.add_argument("--label", default="matrix", help="matrix label")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    from pathlib import Path
    cell_paths = [Path(value) for value in args.cells]
    for path in cell_paths:
        if not (_arms(path) / "decisions.jsonl.gz").exists():
            parser.error("not a cell directory (missing arms/now/decisions.jsonl.gz): "
                         f"{path}")

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
        "cells": {report["cell"]: report for report in reports},
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
