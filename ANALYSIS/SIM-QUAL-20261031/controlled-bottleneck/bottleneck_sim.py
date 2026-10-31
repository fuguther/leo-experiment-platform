#!/usr/bin/env python3
"""Controlled two-corridor bottleneck: does directional error get amplified?

    ** MECHANISM MODEL - NOT LEO EVIDENCE **

A deliberately tiny queueing model, kept strictly separate from the 140-sat
kernel.  It tests ONE hypothesis the full simulation could not settle
(CONCLUSION-ROUND2 "net impact" #3): when a directional routing error delta_r
is comparable to the capacity slack epsilon_r, does the queue nonlinearity
amplify it into sustained backlog growth and tail-delay loss?

Model
-----
Two symmetric parallel corridors (0, 1), each an independent single-server
FIFO bit queue:

    capacity           C          packets/s (one service slot = 1/C s)
    background load    lam_bg     packets/s per corridor, exogenous, and the
                                  SAME arrival list feeds both corridors
    routable load      lam_src    packets/s, one source, one destination, each
                                  packet must take exactly one corridor
    queue              infinite by default, or finite B packets with
                       drop-on-arrival and recorded overflow
    backlog(t)         queued bits + in-service remaining bits: the same
                       caliber as sq_persistence_analyze.py (the packet in
                       service decays linearly across its slot)

Policy: queue-aware greedy on the predicted delay of the immediate next hop,
the same object the LEO scorer minimises:

    J_r = (observed backlog_r + own packet size) / C

(propagation cancels because the corridors are symmetric).  Two information
conditions use identical arrival lists, so every packet is paired:

    fresh  observed backlog = true backlog at the arrival instant
    stale  observed backlog = true backlog at (arrival instant - tau)

Exact ties are broken by packet-id parity, so tie-breaking itself carries no
directional bias.  ``tau = 0`` is simulated as its own condition and must be
bit-identical to fresh; that is a checked negative control, not an assumption.

Error accounting (same calibers as the 140-sat persistence analysis)
-------------------------------------------------------------------
* flip: truth-substituted, NOT re-simulated.  The stale run's committed choice
  differs from the choice its own TRUE backlog at that instant would make.
* delta_r  = (bits wrongly pushed into r - bits that should have gone to r)
  / window, packets/s; sum_r delta_r = 0.
* epsilon_r = C - served_r/window (residual capacity, >= 0 by construction).
* z = |delta_r| / epsilon_r, reported three ways because a sustained
  oscillation can have ~0 net delta while herding transiently:
      z_net  window-level |delta_r| / epsilon_r
      z_bin  per 1 s bin; p50 / p95 / max and the share of bins with z > 1

Slack sweep
-----------
``slack`` = realised reference slack

    slack = 1 - (lam_bg + lam_src/2) / C

i.e. the residual capacity of each corridor in the balanced (fresh) reference.
It is swept over {0.50, 0.20, 0.10, 0.05, 0.02} with **lam_src held fixed**, so
the amount of traffic available to misroute does not change with slack
(otherwise delta and epsilon would be confounded).  lam_bg follows from the
target slack.  The literal task-book quantity 1 - lam_bg/C is recorded next to
it; they differ because the routable load also occupies the corridor.

Usage
-----
    python3 bottleneck_sim.py selftest
    python3 bottleneck_sim.py cell  --slack 0.05 --tau 0.1 --seed 7
    python3 bottleneck_sim.py grid  --json bottleneck-results.json --md REPORT-TABLE.md

Deterministic: arrivals from ``random.Random`` with a documented string seed,
no wall clock, no unrecorded randomness (each result carries the generator and
an arrival-list digest).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import heapq
import json
import math
import random
import statistics
from collections import deque

# --------------------------------------------------------------- model knobs
CAPACITY = 100.0                 # packets/s -> one service slot = 0.01 s
PACKET_SIZE = 1.0                # packet units (all packets identical)
LAM_SRC = 0.90 * CAPACITY        # routable load, FIXED across the slack sweep
SLACK_LEVELS = (0.50, 0.20, 0.10, 0.05, 0.02)
TAU_LEVELS = (0.0, 0.02, 0.1, 0.5)
SEEDS = (7, 8, 9)
WARMUP_S = 60.0
MEASURE_S = 120.0
END_S = WARMUP_S + MEASURE_S
BIN_S = 1.0
QUEUE_CAP_PACKETS = None         # primary: infinite queue
FINITE_QUEUE_PACKETS = 50        # sensitivity run (about 2x mean backlog at rho=.98)
SAMPLE_S = 0.01                  # backlog sampling for means/slopes
CONSEQUENCE_TOL_S = 0.005        # unused here, kept for symmetry with part B


# ----------------------------------------------------------------- helpers
def qstats(values, points=(0.5, 0.9, 0.95, 0.99), scale=1.0):
    clean = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    if not clean:
        return {}
    clean.sort()
    n = len(clean)
    return {f"p{int(p * 100)}": scale * clean[min(n - 1, int(round(p * (n - 1))))]
            for p in points}


def mean(values):
    clean = [float(v) for v in values if v is not None]
    return (sum(clean) / len(clean)) if clean else None


def lsq_slope(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def gen_arrivals(rate, t_end, seed_key):
    """Deterministic Poisson arrival list; generator recorded in every result."""
    rng = random.Random(f"bottleneck|{seed_key}|{rate:.6f}|{t_end:.6f}")
    times, moment = [], 0.0
    while True:
        moment += rng.expovariate(rate)
        if moment >= t_end:
            break
        times.append(moment)
    return times


def arrival_digest(times):
    payload = ",".join(f"{t:.9f}" for t in times).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


# ----------------------------------------------------------------- corridor
class Corridor:
    """Single-server FIFO bit queue with an exact backlog history."""

    def __init__(self, capacity, queue_cap=None):
        self.C = capacity
        self.cap = queue_cap
        self.waiting = deque()           # (size, arrival, pid)
        self.waiting_bits = 0.0
        self.busy_until = None           # None = idle
        self.service = None              # (finish, pid, arrival, size)
        self.history_t = [0.0]
        self.history_w = [0.0]
        self.history_b = [None]
        self.overflow_packets = 0
        self.overflow_bits = 0.0
        self.finished = []               # (finish_time, pid, arrival)
        self.max_backlog = 0.0

    # -- state -----------------------------------------------------------
    def backlog(self, moment):
        if self.busy_until is None or moment >= self.busy_until:
            return self.waiting_bits
        return self.waiting_bits + (self.busy_until - moment) * self.C

    def backlog_at(self, moment):
        """Backlog as it was at ``moment`` (the stale observation)."""
        if moment <= 0.0:
            return 0.0
        index = bisect.bisect_right(self.history_t, moment) - 1
        if index < 0:
            return 0.0
        busy_until = self.history_b[index]
        waiting = self.history_w[index]
        if busy_until is None or moment >= busy_until:
            return waiting
        return waiting + (busy_until - moment) * self.C

    def _record(self, moment):
        value = self.backlog(moment)
        if value > self.max_backlog:
            self.max_backlog = value
        if self.history_t[-1] == moment:
            self.history_w[-1] = self.waiting_bits
            self.history_b[-1] = self.busy_until
        else:
            self.history_t.append(moment)
            self.history_w.append(self.waiting_bits)
            self.history_b.append(self.busy_until)

    # -- events ----------------------------------------------------------
    def enqueue(self, size, arrival, pid, moment):
        """Returns (accepted, finish_time_or_None, overflowed)."""
        if self.cap is not None and self.waiting_bits + size > self.cap:
            self.overflow_packets += 1
            self.overflow_bits += size
            return False, None, True
        if self.busy_until is None or moment >= self.busy_until:
            # start service immediately: backlog is continuous (queued -> service)
            self.busy_until = moment + size / self.C
            self.service = (self.busy_until, pid, arrival, size)
            self._record(moment)
            return True, self.busy_until, False
        self.waiting.append((size, arrival, pid))
        self.waiting_bits += size
        self._record(moment)
        return True, None, False

    def finish_service(self, moment):
        """Close the in-service packet, start the next one if any.

        Returns (finished_record, next_finish_or_None).
        """
        finished = self.service
        self.service = None
        self.busy_until = None
        if self.waiting:
            size, arrival, pid = self.waiting.popleft()
            self.waiting_bits -= size
            self.busy_until = moment + size / self.C
            self.service = (self.busy_until, pid, arrival, size)
            next_finish = self.busy_until
        else:
            next_finish = None
        self._record(moment)
        return finished, next_finish


# --------------------------------------------------------------- simulation
def simulate(condition, tau_s, arrivals_src, arrivals_bg, capacity=CAPACITY,
             queue_cap=None, end_s=END_S):
    """Run one condition; returns (decisions, corridors)."""
    corridors = [Corridor(capacity, queue_cap), Corridor(capacity, queue_cap)]
    heap = []
    seq = 0
    for pid, moment in enumerate(arrivals_src):
        heap.append((moment, seq, "src", pid)); seq += 1
    for moment in arrivals_bg:
        for index in (0, 1):
            heap.append((moment, seq, "bg", index)); seq += 1
    heapq.heapify(heap)

    decisions = []
    while heap:
        moment, _, kind, payload = heapq.heappop(heap)
        if moment >= end_s:
            break
        if kind == "src":
            pid = payload
            if condition == "fresh" or tau_s <= 0.0:
                observed = [corridors[index].backlog(moment) for index in (0, 1)]
            else:
                observed = [corridors[index].backlog_at(moment - tau_s)
                            for index in (0, 1)]
            j_obs = [(observed[index] + PACKET_SIZE) / capacity for index in (0, 1)]
            j_true = [((corridors[index].backlog(moment) + PACKET_SIZE) / capacity)
                      for index in (0, 1)]
            if j_obs[0] == j_obs[1]:
                chosen = pid % 2
            else:
                chosen = 0 if j_obs[0] < j_obs[1] else 1
            if j_true[0] == j_true[1]:
                truth_pick = pid % 2
            else:
                truth_pick = 0 if j_true[0] < j_true[1] else 1
            accepted, finish, overflow = corridors[chosen].enqueue(
                PACKET_SIZE, moment, pid, moment)
            if finish is not None:
                heapq.heappush(heap, (finish, seq, "done", chosen)); seq += 1
            decisions.append({
                "pid": pid, "t": moment, "chosen": chosen,
                "truth_pick": truth_pick, "flip": chosen != truth_pick,
                "j_obs": j_obs, "j_true": j_true,
                "accepted": accepted, "overflow": overflow,
            })
        elif kind == "bg":
            index = payload
            _, finish, _ = corridors[index].enqueue(PACKET_SIZE, moment, None, moment)
            if finish is not None:
                heapq.heappush(heap, (finish, seq, "done", index)); seq += 1
        else:                                   # a service finished
            index = payload
            finished, next_finish = corridors[index].finish_service(moment)
            if finished is not None:
                corridors[index].finished.append(
                    (moment, finished[1], finished[2]))
            if next_finish is not None:
                heapq.heappush(heap, (next_finish, seq, "done", index)); seq += 1
    return decisions, corridors


# ------------------------------------------------------------ measurements
def measure(decisions, corridors, capacity, window_start=WARMUP_S,
            window_end=END_S, bin_s=BIN_S, sample_s=SAMPLE_S):
    """Window metrics for one condition."""
    window = window_end - window_start
    src = [d for d in decisions if window_start <= d["t"] < window_end]

    # --- delta from truth-substituted flips (task-7 caliber) -------------
    delta_bits = [0.0, 0.0]
    flips = [d for d in src if d["flip"]]
    for d in flips:
        delta_bits[d["chosen"]] += PACKET_SIZE
        delta_bits[d["truth_pick"]] -= PACKET_SIZE
    delta = [value / window for value in delta_bits]

    # --- served rate / residual capacity per corridor --------------------
    served = [0.0, 0.0]
    delays = {0: [], 1: []}
    censored = 0
    for index in (0, 1):
        for finish, pid, arrival in corridors[index].finished:
            if window_start <= finish < window_end:
                served[index] += PACKET_SIZE
            if pid is not None and window_start <= finish < window_end:
                delays[index].append(finish - arrival)
    lam = [value / window for value in served]
    epsilon = [capacity - value for value in lam]

    # source packets admitted in the window but not finished by window end
    finished_pids = {pid for index in (0, 1)
                    for _, pid, _ in corridors[index].finished if pid is not None}
    censored = sum(1 for d in src if d["pid"] not in finished_pids)

    # --- backlog series --------------------------------------------------
    grid = []
    moment = window_start
    while moment < window_end:
        grid.append(moment)
        moment += sample_s
    # NOTE: sample with backlog_at() (the historical value).  backlog() only
    # describes the *current* instant, so using it for past sample times would
    # fold the final in-service state into every sample.
    backlog = {0: [corridors[0].backlog_at(t) for t in grid],
               1: [corridors[1].backlog_at(t) for t in grid]}
    growth = {index: lsq_slope(grid, backlog[index]) for index in (0, 1)}

    # --- per-bin delta / epsilon / growth --------------------------------
    n_bins = int(round(window / bin_s))
    bin_rows = []
    for b in range(n_bins):
        t0 = window_start + b * bin_s
        t1 = t0 + bin_s
        bin_delta = [0.0, 0.0]
        bin_flips = 0
        for d in src:
            if t0 <= d["t"] < t1 and d["flip"]:
                bin_delta[d["chosen"]] += PACKET_SIZE
                bin_delta[d["truth_pick"]] -= PACKET_SIZE
                bin_flips += 1
        bin_served = [0.0, 0.0]
        for index in (0, 1):
            for finish, _, _ in corridors[index].finished:
                if t0 <= finish < t1:
                    bin_served[index] += PACKET_SIZE
        bin_lam = [value / bin_s for value in bin_served]
        bin_eps = [capacity - value for value in bin_lam]
        samples = [backlog[index][int(round((t0 - window_start) / sample_s))]
                   for index in (0, 1)]
        nxt = min(len(grid) - 1, int(round((t1 - window_start) / sample_s)))
        bin_growth = [(backlog[index][nxt] - samples[index]) / bin_s
                      for index in (0, 1)]
        bin_rows.append({
            "bin": b, "t0": t0, "t1": t1,
            "n_decisions": sum(1 for d in src if t0 <= d["t"] < t1),
            "n_flips": bin_flips,
            "delta": [value / bin_s for value in bin_delta],
            "epsilon": bin_eps,
            "growth": bin_growth,
        })

    def z_of(delta_value, epsilon_value):
        # no bias is no bias, even when the residual capacity happens to be 0
        if abs(delta_value) <= 1e-12:
            return 0.0
        if epsilon_value <= 1e-9:
            return math.inf
        return abs(delta_value) / epsilon_value

    # The amplification channel is the corridor that RECEIVES the excess inside
    # a bin: its local backlog grows during a herd and drains when the herd
    # switches sides.  The window-level least-squares slope cancels the two
    # signs, so this per-bin quantity is the honest "queue growth" number.
    excess_growth = []
    excess_z = []
    for row in bin_rows:
        index = 0 if row["delta"][0] >= row["delta"][1] else 1
        excess_growth.append(row["growth"][index])
        delta_value = row["delta"][index]
        denominator = row["epsilon"][index]
        if abs(delta_value) <= 1e-12:
            excess_z.append(0.0)
        elif denominator > 1e-9:
            excess_z.append(abs(delta_value) / denominator)
        else:
            excess_z.append(math.inf)

    z_net = [z_of(delta[index], epsilon[index]) for index in (0, 1)]
    z_bins = []
    for row in bin_rows:
        z_bins.append([z_of(row["delta"][index], row["epsilon"][index])
                       for index in (0, 1)])
    flat_z = [value for pair in z_bins for value in pair if math.isfinite(value)]
    drive = max(range(2), key=lambda index: (z_net[index], -index))  # excess side

    all_delays = delays[0] + delays[1]
    return {
        "condition": None,
        "n_decisions": len(src),
        "n_flips": len(flips),
        "flip_rate": (len(flips) / len(src)) if src else None,
        "lam": lam, "epsilon": epsilon,
        "delta": delta,
        "z_net": z_net,
        "z_net_max": max(z_net) if z_net else None,
        "z_net_drive_corridor": drive,
        "z_bin_p50": statistics.median(flat_z) if flat_z else None,
        "z_bin_p95": qstats(flat_z, points=(0.95,)).get("p95"),
        "z_bin_max": max(flat_z) if flat_z else None,
        "z_bin_share_gt1": (sum(1 for value in flat_z if value > 1.0) / len(flat_z))
        if flat_z else None,
        "growth": growth,
        "growth_max": max(growth.values()) if growth else None,
        "growth_bin_excess_mean": mean(excess_growth),
        "growth_bin_excess_max": max(excess_growth) if excess_growth else None,
        "z_bin_excess_median": statistics.median(excess_z) if excess_z else None,
        "share_bins_z_gt1_excess": (sum(1 for value in excess_z if value > 1.0)
                                    / len(excess_z)) if excess_z else None,
        "backlog_mean": {index: mean(backlog[index]) for index in (0, 1)},
        "backlog_p95": {index: qstats(backlog[index], points=(0.95,)).get("p95")
                        for index in (0, 1)},
        "backlog_max": {index: max(backlog[index]) for index in (0, 1)},
        "delay_mean": mean(all_delays),
        "delay_q": qstats(all_delays, scale=1000.0),
        "delay_by_corridor": {index: qstats(delays[index], scale=1000.0)
                              for index in (0, 1)},
        "censored_packets": censored,
        "overflow_packets": sum(c.overflow_packets for c in corridors),
        "overflow_bits": sum(c.overflow_bits for c in corridors),
        "bins": bin_rows,
    }


# ------------------------------------------------------------------- cells
def cell_params(slack):
    """Solve lam_bg from the realised reference slack with lam_src fixed."""
    lam_bg = CAPACITY * (1.0 - slack) - LAM_SRC / 2.0
    if lam_bg < 0:
        raise ValueError(f"slack {slack} too large for lam_src={LAM_SRC}")
    return {"capacity": CAPACITY, "lam_src": LAM_SRC, "lam_bg": lam_bg,
            "slack_target": slack,
            "slack_realized": 1.0 - (lam_bg + LAM_SRC / 2.0) / CAPACITY,
            "literal_1_minus_lam_bg_over_C": 1.0 - lam_bg / CAPACITY,
            "rho_ref": (lam_bg + LAM_SRC / 2.0) / CAPACITY}


def run_cell(slack, tau_s, seed, queue_cap=None, warmup=None, window=None):
    """Fresh + stale on identical arrivals; returns both metric blocks."""
    warm = WARMUP_S if warmup is None else float(warmup)
    win = MEASURE_S if window is None else float(window)
    end = warm + win
    params = cell_params(slack)
    arrivals_src = gen_arrivals(params["lam_src"], end, f"{seed}|src")
    arrivals_bg = gen_arrivals(params["lam_bg"], end, f"{seed}|bg")
    fresh_dec, fresh_cor = simulate("fresh", 0.0, arrivals_src, arrivals_bg,
                                    queue_cap=queue_cap, end_s=end)
    stale_dec, stale_cor = simulate("stale", tau_s, arrivals_src, arrivals_bg,
                                    queue_cap=queue_cap, end_s=end)
    fresh = measure(fresh_dec, fresh_cor, params["capacity"],
                    window_start=warm, window_end=end)
    stale = measure(stale_dec, stale_cor, params["capacity"],
                    window_start=warm, window_end=end)
    fresh["condition"] = "fresh"
    stale["condition"] = f"stale(tau={tau_s})"

    # paired per-packet delay (same pid, same arrival list)
    fresh_finish = {pid: finish for index in (0, 1)
                    for finish, pid, _ in fresh_cor[index].finished if pid is not None}
    stale_finish = {pid: finish for index in (0, 1)
                    for finish, pid, _ in stale_cor[index].finished if pid is not None}
    arrivals = {pid: moment for pid, moment in enumerate(arrivals_src)}
    paired = []
    for pid, finish_stale in stale_finish.items():
        if pid not in fresh_finish or pid not in arrivals:
            continue
        arrive = arrivals[pid]
        if not (warm <= arrive and warm <= finish_stale < end):
            continue
        paired.append((finish_stale - arrive) - (fresh_finish[pid] - arrive))
    return {
        "params": {**params, "tau_s": tau_s, "seed": seed,
                   "queue_cap_packets": queue_cap,
                   "capacity_packets_s": CAPACITY,
                   "packet_size": PACKET_SIZE,
                   "warmup_s": warm, "measure_s": win, "end_s": end,
                   "arrival_generator": "random.Random('bottleneck|seed|rate|t_end').expovariate(rate)",
                   "arrivals_src_digest": arrival_digest(arrivals_src),
                   "arrivals_bg_digest": arrival_digest(arrivals_bg),
                   "n_src_arrivals": len(arrivals_src),
                   "n_bg_arrivals": len(arrivals_bg)},
        "fresh": fresh,
        "stale": stale,
        "delta_realized": [stale["lam"][index] - fresh["lam"][index]
                           for index in (0, 1)],
        "paired": {
            "n": len(paired),
            "mean_delta_ms": 1000.0 * mean(paired) if paired else None,
            "q_ms": qstats(paired, scale=1000.0),
            "slower": sum(1 for value in paired if value > 0),
            "faster": sum(1 for value in paired if value < 0),
            "equal": sum(1 for value in paired if value == 0),
        },
    }


def run_sensitivity(slacks=(0.05, 0.02), taus=(0.1, 0.5),
                    settings=((60.0, 120.0), (120.0, 120.0), (200.0, 200.0)),
                    seeds=SEEDS):
    """Is the paired delay loss stable against warm-up / window choices?

    At eps/mu = 0.02 the reference itself is nearly critical and its own
    finite-window transient is heavy-tailed, so the *growth* estimate is not
    trustworthy there.  The paired delay loss must survive a change of
    warm-up/window or it is not a mechanism result.
    """
    rows = []
    for warmup, window in settings:
        for slack in slacks:
            for tau in taus:
                cells = [run_cell(slack, tau, seed, warmup=warmup, window=window)
                         for seed in seeds]
                paired = [cell["paired"]["mean_delta_ms"] for cell in cells]
                rows.append({
                    "warmup_s": warmup, "window_s": window,
                    "slack": slack, "tau_s": tau,
                    "paired_delta_ms": mean(paired),
                    "paired_delta_ms_min": min(paired),
                    "paired_delta_ms_max": max(paired),
                    "slow_share": mean([cell["paired"]["slower"]
                                        / max(1, cell["paired"]["n"]) for cell in cells]),
                    "fresh_delay_ms": 1000.0 * mean([cell["fresh"]["delay_mean"]
                                                     for cell in cells]),
                    "stale_delay_ms": 1000.0 * mean([cell["stale"]["delay_mean"]
                                                     for cell in cells]),
                })
    return rows


def aggregate(cells):
    """Across seeds: mean / min / max of the headline numbers."""
    keys = ("flip_rate", "z_net_max", "z_bin_p95", "z_bin_max", "z_bin_share_gt1",
            "growth_max", "delay_mean")
    out = {"z_bin_share_gt1": None}
    for key in keys:
        values = [cell["stale"][key] for cell in cells if cell["stale"][key] is not None]
        if values:
            out[key] = mean(values)
            out[key + "_min"] = min(values)
            out[key + "_max"] = max(values)
    paired = [cell["paired"]["mean_delta_ms"] for cell in cells
              if cell["paired"]["mean_delta_ms"] is not None]
    if paired:
        out["paired_mean_delta_ms"] = mean(paired)
        out["paired_mean_delta_ms_min"] = min(paired)
        out["paired_mean_delta_ms_max"] = max(paired)
    fresh_delay = [cell["fresh"]["delay_mean"] for cell in cells
                   if cell["fresh"]["delay_mean"] is not None]
    if fresh_delay:
        out["fresh_delay_mean_ms"] = 1000.0 * mean(fresh_delay)
    if out["delay_mean"] is not None and out["fresh_delay_mean_ms"]:
        out["delay_ratio_stale_over_fresh"] = (
            out["delay_mean"] / (out["fresh_delay_mean_ms"] / 1000.0))
    growth = [cell["stale"]["growth_max"] for cell in cells
              if cell["stale"]["growth_max"] is not None]
    fresh_growth = [cell["fresh"]["growth_max"] for cell in cells
                    if cell["fresh"]["growth_max"] is not None]
    if growth:
        out["growth_max_mean"] = mean(growth)
    if fresh_growth:
        out["fresh_growth_max_mean"] = mean(fresh_growth)
    if growth and fresh_growth:
        # the reference itself carries a finite-window transient, so only the
        # excess over the fresh control can be attributed to staleness
        out["growth_excess_over_fresh"] = mean(
            [stale_g - fresh_g for stale_g, fresh_g in zip(growth, fresh_growth)])
    # two calibers for "how much load moved": the truth-substituted bookkeeping
    # delta used by the 140-sat analysis, and the actual stale-vs-fresh load
    # difference.  Both are window-level and both are expected to be ~0 here
    # because the herding alternates; the transient (binned) z is the one that
    # tracks the damage.
    out["delta_truthsub_abs_mean"] = mean(
        [abs(cell["stale"]["delta"][index]) for cell in cells for index in (0, 1)])
    out["delta_realized_abs_mean"] = mean(
        [abs(value) for cell in cells for value in cell["delta_realized"]])
    out["delta_realized_signed_mean"] = mean(
        [value for cell in cells for value in cell["delta_realized"]])
    excess_growth = [cell["stale"]["growth_bin_excess_mean"] for cell in cells
                     if cell["stale"]["growth_bin_excess_mean"] is not None]
    if excess_growth:
        out["growth_bin_excess_mean"] = mean(excess_growth)
    excess_share = [cell["stale"].get("share_bins_z_gt1_excess") for cell in cells
                    if cell["stale"].get("share_bins_z_gt1_excess") is not None]
    if excess_share:
        out["share_bins_z_gt1_excess_mean"] = mean(excess_share)
    out["overflow_mean"] = mean([cell["stale"]["overflow_packets"] for cell in cells])
    out["overflow_fresh_mean"] = mean([cell["fresh"]["overflow_packets"] for cell in cells])
    return out


def classify(agg):
    """Pre-registered reading of one (slack, tau) cell.

    absorbed  : no tail loss and no drift  (z small is expected here)
    amplified : tail loss with bounded drift
    sustained : positive drift on the driving corridor
    """
    ratio = agg.get("delay_ratio_stale_over_fresh")
    delta_ms = agg.get("paired_mean_delta_ms") or 0.0
    excess = agg.get("growth_excess_over_fresh")
    if delta_ms > 1.0 and excess is not None and excess > 0.25:
        return "sustained_drift"
    if delta_ms > 1.0 and ratio is not None and ratio > 1.05:
        return "amplified_bounded"
    return "absorbed"


SEED_CELL_KEYS = ("flip_rate", "n_decisions", "n_flips", "lam", "epsilon",
                  "delta", "z_net", "z_net_max", "z_bin_p50", "z_bin_p95",
                  "z_bin_max", "z_bin_share_gt1", "z_bin_excess_median",
                  "share_bins_z_gt1_excess", "growth", "growth_max",
                  "growth_bin_excess_mean", "growth_bin_excess_max",
                  "backlog_mean", "delay_mean", "delay_q", "overflow_packets",
                  "censored_packets")


def slim_cell(cell):
    """Keep the aggregates; the 120-bin tables are per-cell detail that the
    report does not need at seed level."""
    out = {"params": cell["params"],
           "delta_realized": cell["delta_realized"],
           "paired": cell["paired"]}
    for condition in ("fresh", "stale"):
        out[condition] = {key: cell[condition][key] for key in SEED_CELL_KEYS
                          if key in cell[condition]}
    return out


def run_grid(queue_caps=(None, FINITE_QUEUE_PACKETS), seeds=SEEDS,
             slacks=SLACK_LEVELS, taus=TAU_LEVELS):
    grid = []
    for queue_cap in queue_caps:
        for slack in slacks:
            for tau in taus:
                cells = [run_cell(slack, tau, seed, queue_cap=queue_cap)
                         for seed in seeds]
                agg = aggregate(cells)
                agg["classification"] = classify(agg)
                agg["params"] = cell_params(slack)
                agg["tau_s"] = tau
                agg["queue_cap_packets"] = queue_cap
                agg["seed_cells"] = [slim_cell(cell) for cell in cells]
                grid.append(agg)
    return grid


# ------------------------------------------------------------------ output
def _f(value, pattern="{:.3f}", dash="n/a"):
    if value is None:
        return dash
    if isinstance(value, float) and not math.isfinite(value):
        return "inf"
    return pattern.format(value)


def render_table(grid, queue_cap=None):
    rows = [g for g in grid if g["queue_cap_packets"] == queue_cap]
    lines = []
    header = ("| slack eps/mu | tau (s) | flip rate | z_net max | z_bin p95 | "
              "z_bin max | stale delay mean (ms) | fresh delay mean (ms) | "
              "ratio | paired dDelay (ms) | |delta| truth-sub (pkt/s) | "
              "|delta| realized (pkt/s) | window growth (pkt/s) | "
              "bin growth excess-side (pkt/s) | bins z>1 (excess) | overflow | class |")
    rule = "|" + "---|" * 17
    lines += [header, rule]
    for row in rows:
        lines.append(
            f"| {_f(row['params']['slack_realized'], '{:.3f}')} | {_f(row['tau_s'], '{:g}')} | "
            f"{_f(row['flip_rate'], '{:.4f}')} | {_f(row['z_net_max'])} | "
            f"{_f(row['z_bin_p95'])} | {_f(row['z_bin_max'])} | "
            f"{_f(row['z_bin_share_gt1'], '{:.3f}')} | "
            f"{_f(None if row.get('delay_mean') is None else 1000.0 * row['delay_mean'], '{:.2f}')} | "
            f"{_f(row.get('fresh_delay_mean_ms'), '{:.2f}')} | "
            f"{_f(row.get('delay_ratio_stale_over_fresh'), '{:.3f}')} | "
            f"{_f(row.get('paired_mean_delta_ms'), '{:+.2f}')} | "
            f"{_f(row.get('delta_truthsub_abs_mean'), '{:.3f}')} | "
            f"{_f(row.get('delta_realized_abs_mean'), '{:.3f}')} | "
            f"{_f(row.get('growth_max_mean'), '{:+.3f}')} | "
            f"{_f(row.get('growth_bin_excess_mean'), '{:+.2f}')} | "
            f"{_f(row.get('share_bins_z_gt1_excess_mean'), '{:.3f}')} | "
            f"{_f(row.get('overflow_mean'), '{:.1f}')} | "
            f"{row['classification']} |")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="controlled two-corridor bottleneck (MECHANISM MODEL, not LEO)")
    parser.add_argument("command",
                        choices=["cell", "grid", "selftest", "sensitivity"])
    parser.add_argument("--slack", type=float, default=0.05)
    parser.add_argument("--tau", type=float, default=0.1)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--json")
    parser.add_argument("--md")
    args = parser.parse_args(argv)

    if args.command == "cell":
        result = run_cell(args.slack, args.tau, args.seed)
        keys = ("flip_rate", "delta", "z_net", "z_net_max", "z_bin_p50",
                "z_bin_p95", "z_bin_max", "z_bin_share_gt1", "lam", "epsilon",
                "growth", "growth_max", "backlog_mean", "delay_mean", "delay_q",
                "censored_packets", "overflow_packets")
        print(json.dumps({"params": result["params"],
                          "fresh": {k: result["fresh"].get(k) for k in keys},
                          "stale": {k: result["stale"].get(k) for k in keys},
                          "paired": result["paired"]}, indent=1, sort_keys=True))
        return 0

    if args.command == "sensitivity":
        rows = run_sensitivity()
        header = ("| warm-up (s) | window (s) | slack | tau | paired dDelay (ms) | "
                  "min | max | share slower | fresh delay (ms) | stale delay (ms) |")
        print(header)
        print("|---|---|---|---|---|---|---|---|---|---|")
        for row in rows:
            print(f"| {row['warmup_s']:.0f} | {row['window_s']:.0f} | {row['slack']:.2f} | "
                  f"{row['tau_s']:g} | {row['paired_delta_ms']:+.2f} | "
                  f"{row['paired_delta_ms_min']:+.2f} | {row['paired_delta_ms_max']:+.2f} | "
                  f"{row['slow_share']:.3f} | {row['fresh_delay_ms']:.1f} | "
                  f"{row['stale_delay_ms']:.1f} |")
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump({"schema": "controlled-bottleneck-sensitivity/v1",
                           "warning": "MECHANISM MODEL - NOT LEO EVIDENCE",
                           "rows": rows}, fh, indent=1, sort_keys=True)
                fh.write("\n")
        return 0

    if args.command == "selftest":
        # tau = 0 must reproduce fresh exactly (flip 0, identical delays)
        for slack in SLACK_LEVELS:
            result = run_cell(slack, 0.0, 7)
            stale, fresh = result["stale"], result["fresh"]
            assert stale["n_flips"] == 0, (slack, stale["n_flips"])
            assert abs(result["paired"]["mean_delta_ms"]) < 1e-9, (slack, result["paired"])
            assert stale["delay_q"] == fresh["delay_q"], slack
        print("selftest OK: tau=0 is bit-identical to fresh for all slack levels")
        return 0

    grid = run_grid()
    payload = {
        "schema": "controlled-bottleneck-grid/v1",
        "warning": "MECHANISM MODEL - NOT LEO EVIDENCE",
        "model": {
            "capacity_packets_s": CAPACITY, "packet_size": PACKET_SIZE,
            "lam_src": LAM_SRC, "lam_bg": "solved per slack from eps/mu",
            "tau_levels": list(TAU_LEVELS), "slack_levels": list(SLACK_LEVELS),
            "seeds": list(SEEDS), "warmup_s": WARMUP_S, "measure_s": MEASURE_S,
            "bin_s": BIN_S, "sample_s": SAMPLE_S,
            "backlog": "queued bits + in-service remaining (linear decay)",
            "policy": "queue-aware greedy on J_r=(observed_backlog+packet)/C",
            "fresh": "observed backlog = true backlog at arrival",
            "stale": "observed backlog = true backlog at arrival - tau",
            "flip": "truth-substituted: committed vs same-run true-backlog argmin",
            "delta": "sum over flips of (+chosen, -truth) / window; sum_r delta_r = 0",
            "epsilon": "capacity - served/window",
            "z": "|delta_r|/epsilon_r; z_bin computed per 1 s bin",
            "queue_cap_packets": {"primary": None, "sensitivity": FINITE_QUEUE_PACKETS},
        },
        "table": render_table(grid, None),
        "table_finite_queue": render_table(grid, FINITE_QUEUE_PACKETS),
        "grid": grid,
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
            fh.write("\n")
    print(payload["table"])
    if args.md:
        with open(args.md, "w", encoding="utf-8") as fh:
            fh.write("## Primary (infinite queue)\n\n" + payload["table"] + "\n\n")
            fh.write("## Sensitivity (finite queue = "
                     f"{FINITE_QUEUE_PACKETS} packets)\n\n"
                     + payload["table_finite_queue"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
