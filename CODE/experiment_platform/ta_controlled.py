"""Controlled two-resource mechanism experiment (E0 treated as a TREATMENT).

WHY THIS EXISTS
---------------
The 96-star confirmation cannot be launched tonight: the full-network kernel
costs ~39 min and ~12.5 GB RSS for a single LOW-load cell (measured on the VM:
17,472 packets, `probe-trunk-cost-20261031`), the contract's D = 2 x p95 is an
inert deadline on this delay distribution (measured: 0/8870 misses), and the
platform's R_phys physical-remaining-path scorer is not implemented.  Before
any of that, the QUESTION this whole study asks is a mechanism question:

    if a router queried each candidate's state at the instant the packet will
    USE that resource -- instead of now / the last measurement / a shared
    future instant -- would it choose differently, and would the real business
    delay improve?

This module answers exactly that question in a controlled two-queue system
whose ground truth is hand-computable, using the PLATFORM'S OWN predictor and
scorer (`CODE.leo_sim.time_alignment.plan_decision`), so the fixture cannot
silently diverge from the code the experiment would use.

WHAT IS SIMULATED (and what is not)
-----------------------------------
* Two candidate next hops N and E.  Each leads to its own FIFO queue with
  capacity C = 1 Gbps and buffer B = 20 Mbit; packets are 12000 bit.
* The target packet reaches queue i after ETA_i (9 / 11 ms) and then follows a
  fixed remainder so the two complete paths cost 20 ms and 20 ms + d.
* Background traffic per queue is a square wave s(t) of half-period tau:
    differential: lambda_N = .9C + .2C s(t),  lambda_E = .9C - .2C s(t)
    common:       lambda_N = lambda_E = .9C + .2C s(t)
  The queue is integrated exactly (piecewise-linear, floor 0, cap B).
* The node only sees ADVERTISEMENTS: one sample every `advert_interval_s`
  that ARRIVES `measurement_age_s` after its source timestamp.  Nothing else.
* Arms query the SAME received history at different instants:
    stale     = the resource's last received source measurement
    now       = the decision instant t0
    common    = t0 + median(candidate ETAs)  (median_eta)
    candidate = each candidate's own ETA (the contract's ARRIVAL semantic,
                `candidate_target_rule="arrival"`; no extra queue step)
* Business delay of choosing i = ETA_i + Q_true(t0+ETA_i)/C + TX + remainder_i
  = P_i + Q_true(t0+ETA_i)/C + TX.  The scorer's own formula is
  ETA_i + W_hat/C + TX + remainder_i, so with a perfect prediction the score
  EQUALS the business delay -- the fixture is not inventing a different cost.
* NOT simulated: 96-star topology, control-plane priority, DRR, GE, dynamic
  topology, downlink congestion, multi-hop re-decision.  This is a mechanism
  test, not a network-performance claim.

PRE-REGISTERED HYPOTHESES (frozen before any run; see HYPOTHESES below)
-----------------------------------------------------------------------
H1  In fast-advertisement differential cells with tau=100 ms and d=0.1 ms the
    CANDIDATE arm must beat NOW on mean business delay by >= 0.5 ms and match
    the oracle more often (queue swap 10 ms >> path margin 0.1 ms).
H2  In the same cells with tau=2 ms (queue swap 0.2 ms, below the 1 ms
    resolution) no arm may beat NOW by >= 0.5 ms; any apparent win is noise.
H3  In COMMON-flow cells the queue cancels from the candidate difference, so
    no arm may beat NOW by >= 0.5 ms regardless of tau or d.
H4  With slow advertisements (50 ms age) the candidate advantage in the
    H1 cells must shrink relative to fast advertisements (5 ms), because the
    prediction horizon grows; the sign may stay positive.
"""
from __future__ import annotations

import argparse
import json
import math
import platform
from dataclasses import dataclass, field
from pathlib import Path

from CODE.leo_sim import time_alignment as ta

#: Frozen engineering constants (contract E0 values).
C_BPS = 1.0e9
PKT_BITS = 12_000.0
BUFFER_BITS = 20.0e6
TX_S = PKT_BITS / C_BPS                      # 12 microseconds
ETA_S = {"N": 0.009, "E": 0.011}
PATH_BASE_S = {"N": 0.020, "E": 0.020}       # E adds the margin d
GRID_START_S = 0.100
GRID_END_S = 0.300                            # exclusive
GRID_STEP_S = 0.001
ARMS = ("stale", "now", "common", "candidate")
PHASES = 8
PRACTICAL_DELTA_MS = 0.5                     # pre-registered practical threshold
HORIZON_S = 0.36


@dataclass(frozen=True)
class Cell:
    correlation: str          # "differential" | "common"
    tau_s: float              # square-wave half period
    margin_s: float           # d: extra physical cost of the E path
    advert_interval_s: float  # advertisement cadence
    measurement_age_s: float  # source -> local receive delay
    #: Queue-arrival ETAs of the two candidates.  Defaults are the contract's
    #: E0 values; the dose axis varies them to test the horizon term.
    eta_n_s: float = 0.009
    eta_e_s: float = 0.011
    #: Buffer size of each target queue (the contract's E0 uses 20 Mbit; the
    #: queue-magnitude dose axis raises it so Qmax = .1*C*tau can grow).
    buffer_bits: float = 20.0e6

    @property
    def advert_class(self) -> str:
        return "fast" if self.advert_interval_s <= 0.01 else "slow"

    @property
    def eta_s(self) -> dict:
        return {"N": self.eta_n_s, "E": self.eta_e_s}

    @property
    def path_s(self) -> dict:
        return {"N": PATH_BASE_S["N"], "E": PATH_BASE_S["E"] + self.margin_s}

    @property
    def remaining_s(self) -> dict:
        rem = {d: self.path_s[d] - self.eta_s[d] for d in ("N", "E")}
        if any(v < -1e-12 for v in rem.values()):
            raise ValueError(
                f"candidate ETA exceeds its path cost: {rem!r}")
        return rem

    @property
    def qmax_bits(self) -> float:
        # The square wave builds at .1*C for tau seconds; the buffer can bind.
        return min(0.1 * C_BPS * self.tau_s, self.buffer_bits)

    @property
    def deadline_s(self) -> float:
        # Pre-registered SLO: the slower path's free cost plus HALF the largest
        # queue wait the square wave can build.  Binds for both actions.
        return max(self.path_s.values()) + 0.5 * self.qmax_bits / C_BPS

    def as_dict(self) -> dict:
        return {
            "correlation": self.correlation,
            "tau_ms": self.tau_s * 1e3,
            "margin_ms": self.margin_s * 1e3,
            "advert_interval_ms": self.advert_interval_s * 1e3,
            "measurement_age_ms": self.measurement_age_s * 1e3,
            "advert_class": self.advert_class,
            "path_ms": {k: v * 1e3 for k, v in self.path_s.items()},
            "eta_ms": {k: v * 1e3 for k, v in self.eta_s.items()},
            "buffer_mbit": self.buffer_bits / 1e6,
            "qmax_ms": self.qmax_bits / C_BPS * 1e3,
            "deadline_ms": self.deadline_s * 1e3,
        }


def cell_id(cell: Cell) -> str:
    corr = "diff" if cell.correlation == "differential" else "common"
    swing = cell.qmax_bits / C_BPS * 1e3
    spread = (cell.eta_e_s - cell.eta_n_s) * 1e3
    return (f"{corr}-tau{cell.tau_s * 1e3:g}ms-d{cell.margin_s * 1e3:g}ms-"
            f"qswing{swing:g}ms-spread{spread:g}ms-{cell.advert_class}")


def build_cells() -> list[Cell]:
    cells = []
    for corr in ("differential", "common"):
        for tau in (0.002, 0.100):
            for margin in (0.0001, 0.008):
                for interval, age in ((0.005, 0.005), (0.050, 0.050)):
                    cells.append(Cell(corr, tau, margin, interval, age))
    return cells


def square_wave(t: float, tau_s: float, phase_s: float) -> float:
    x = (t - phase_s) % (2.0 * tau_s)
    return 1.0 if x < tau_s else -1.0


def _rates(cell: Cell, t: float, phase_s: float) -> tuple[float, float]:
    s = square_wave(t, cell.tau_s, phase_s)
    if cell.correlation == "differential":
        return 0.9 * C_BPS + 0.2 * C_BPS * s, 0.9 * C_BPS - 0.2 * C_BPS * s
    both = 0.9 * C_BPS + 0.2 * C_BPS * s
    return both, both


class QueueSeries:
    """Exact fluid queue for one cell+phase, integrated on a fine grid."""

    DT_S = 2.0e-5

    def __init__(self, cell: Cell, phase_s: float):
        self.cell = cell
        self.phase_s = phase_s
        n = int(math.ceil(HORIZON_S / self.DT_S)) + 2
        self.times = [i * self.DT_S for i in range(n)]
        q = {"N": 0.0, "E": 0.0}
        self.q = {"N": [], "E": []}
        for i in range(n):
            t = self.times[i]
            lam = _rates(cell, t, phase_s)
            for d in ("N", "E"):
                if q[d] <= 0.0 and lam[0 if d == "N" else 1] <= C_BPS:
                    q[d] = 0.0
                else:
                    q[d] += (lam[0 if d == "N" else 1] - C_BPS) * self.DT_S
                q[d] = min(cell.buffer_bits, max(0.0, q[d]))
                self.q[d].append(q[d])

    def at(self, t: float) -> dict:
        if t <= 0.0:
            return {"N": 0.0, "E": 0.0}
        i = int(t / self.DT_S)
        if i >= len(self.times) - 1:
            return {"N": self.q["N"][-1], "E": self.q["E"][-1]}
        frac = (t - self.times[i]) / self.DT_S
        return {
            d: self.q[d][i] + frac * (self.q[d][i + 1] - self.q[d][i])
            for d in ("N", "E")
        }

    def peak(self) -> dict:
        return {d: max(self.q[d]) for d in ("N", "E")}


def _resource(direction: str) -> ta.ResourceKey:
    return ta.ResourceKey(0, direction, ta.KIND_ISL)


def build_history(cell: Cell, series: QueueSeries, t0: float,
                  history_limit: int = 8) -> tuple:
    """Only received advertisements: source time + age <= t0."""
    if cell.advert_interval_s <= 0:
        raise ValueError("advert_interval_s must be > 0")
    last_src = t0 - cell.measurement_age_s
    k_last = int(math.floor(last_src / cell.advert_interval_s + 1e-9))
    k_first = max(0, k_last - history_limit + 1)
    samples = []
    for k in range(k_first, k_last + 1):
        measured = k * cell.advert_interval_s
        received = measured + cell.measurement_age_s
        if received > t0 + 1e-12:
            continue
        q = series.at(measured)
        for d in ("N", "E"):
            samples.append(ta.StateSample(
                resource=_resource(d), measured_at=measured,
                received_at=received, queue_bits=q[d],
                rate_bps=C_BPS, arrival_rate_bps=None))
    return tuple(samples)


def true_delay(cell: Cell, series: QueueSeries, t0: float, direction: str) -> float:
    arrival = t0 + cell.eta_s[direction]
    backlog = series.at(arrival)[direction]
    return cell.path_s[direction] + backlog / C_BPS + TX_S


def decide(cell: Cell, history, t0: float, arm: str):
    """Plan with the arm's own query instant, using the platform's scorer."""
    def snapshot(candidate_arm: str, common_horizon_s=None):
        return ta.make_snapshot(
            satellite=0, snapshot_at=t0, history=history,
            legal_directions=("N", "E"),
            resources={d: _resource(d) for d in ("N", "E")},
            egress_queue_bits={"N": 0.0, "E": 0.0},
            local_egress_in_service_s={"N": 0.0, "E": 0.0},
            link_rate_bps={"N": C_BPS, "E": C_BPS},
            link_propagation_s={d: cell.eta_s[d] - TX_S for d in ("N", "E")},
            peer_process_s={"N": 0.0, "E": 0.0},
            remaining_prop_s=cell.remaining_s,
            resource_service_rate_bps={"N": C_BPS, "E": C_BPS},
            pkt_bits=PKT_BITS,
            max_resource_queue_bits=cell.buffer_bits,
            arm=candidate_arm,
            predictor="bounded_linear",
            history_limit=8,
            common_horizon_s=common_horizon_s,
            common_rule="median_eta",
            # The contract's ARRIVAL semantics: the query instant is the ETA,
            # and the wait is what the prediction AT that instant says.
            candidate_target_rule="arrival",
        )

    snap = snapshot(arm)
    if arm == "common":
        # The kernel resolves the shared horizon from the SAME estimator before
        # building the snapshot; with a None horizon the scorer would fall back
        # to "query at t0" (NO_STRONG_COMMON), which is NOT the common arm.
        horizon = ta.resolve_common_horizon(snap, "median_eta")
        if horizon is None:
            raise RuntimeError("common arm has no strong common horizon")
        snap = snapshot(arm, common_horizon_s=horizon)
    plan = ta.plan_decision(snap)
    chosen = plan.chosen()
    if chosen is None:
        raise RuntimeError("fixture produced an empty ranking")
    return chosen, plan


def _target_offset(cell: Cell, arm: str, direction: str,
                   last_measured: dict) -> float:
    """Where each arm's query instant sits relative to t0 (seconds)."""
    if arm == "now":
        return 0.0
    if arm == "common":
        return 0.5 * (cell.eta_s["N"] + cell.eta_s["E"])
    if arm == "stale":
        raise ValueError("stale offset is per resource; use last_measured")
    if arm == "candidate":
        return cell.eta_s[direction]
    raise ValueError(f"unknown arm {arm!r}")


def _oracle_predictor_choice(cell: Cell, series: QueueSeries, t0: float,
                             arm: str, last_measured: dict) -> str:
    """Perfect forecast at the arm's own query instant (bound, not online)."""
    best_d, best_score = None, None
    for d in ("N", "E"):
        if arm == "stale":
            offset = last_measured[d] - t0
        else:
            offset = _target_offset(cell, arm, d, last_measured)
        backlog = series.at(t0 + offset)[d]
        score = cell.path_s[d] + backlog / C_BPS + TX_S
        if best_score is None or score < best_score:
            best_d, best_score = d, score
    return best_d


def run_cell(cell: Cell, grid=None, phases: int = PHASES) -> dict:
    if grid is None:
        grid = []
        t = GRID_START_S
        while t < GRID_END_S - 1e-12:
            grid.append(round(t, 9))
            t += GRID_STEP_S
    grid = list(grid)
    arms = {arm: {"delays_ms": [], "miss": 0, "regret_ms": [],
                  "oracle_hit": 0, "changed_vs_now": 0,
                  "pred_error_ms": [],
                  "oracle_predictor": {"delays_ms": [], "miss": 0,
                                       "regret_ms": [], "oracle_hit": 0}}
            for arm in ARMS}
    oracle_delays = []
    for phase_i in range(phases):
        phase = (2.0 * cell.tau_s) * (phase_i / phases)
        series = QueueSeries(cell, phase)
        for t0 in grid:
            history = build_history(cell, series, t0)
            truth = {d: true_delay(cell, series, t0, d) for d in ("N", "E")}
            best_d = min(truth, key=truth.get)
            best = truth[best_d]
            oracle_delays.append(best)
            chosen_by_arm = {}
            last_measured = {d: 0.0 for d in ("N", "E")}
            for sample in history:
                d = sample.resource.direction
                last_measured[d] = max(last_measured[d], sample.measured_at)
            for arm in ARMS:
                chosen, plan = decide(cell, history, t0, arm)
                chosen_by_arm[arm] = chosen
                delay = truth[chosen]
                rec = arms[arm]
                rec["delays_ms"].append(delay * 1e3)
                rec["regret_ms"].append((delay - best) * 1e3)
                if delay > cell.deadline_s:
                    rec["miss"] += 1
                if chosen == best_d:
                    rec["oracle_hit"] += 1
                # PERFECT-FORECAST companion: same query rule, but W equals the
                # TRUE queue at that rule's target instant.  This separates
                # "query time is right" from "estimator can predict it".
                oracle_chosen = _oracle_predictor_choice(
                    cell, series, t0, arm, last_measured)
                oracle_delay = truth[oracle_chosen]
                orec = arms[arm]["oracle_predictor"]
                orec["delays_ms"].append(oracle_delay * 1e3)
                orec["regret_ms"].append((oracle_delay - best) * 1e3)
                if oracle_chosen == best_d:
                    orec["oracle_hit"] += 1
                if oracle_delay > cell.deadline_s:
                    orec["miss"] += 1
                # C2 evidence: |predicted - true| queue at the arm's target
                # instant, in delay ms.  Only fully-known scores are scored.
                targets = dict(plan.targets)
                for score in plan.scored.scores:
                    if score.fallback:
                        continue
                    target = targets.get(score.direction)
                    if target is None or score.predicted_bits is None:
                        continue
                    truth_bits = series.at(target)[score.direction]
                    rec["pred_error_ms"].append(
                        abs(float(score.predicted_bits) - truth_bits)
                        / C_BPS * 1e3)
            for arm in ARMS:
                if chosen_by_arm[arm] != chosen_by_arm["now"]:
                    arms[arm]["changed_vs_now"] += 1
    n = len(grid) * phases
    out = {}
    for arm in ARMS:
        rec = arms[arm]
        delays = sorted(rec["delays_ms"])
        out[arm] = {
            "n": n,
            "mean_delay_ms": sum(delays) / n,
            "p95_delay_ms": delays[min(n - 1, int(round(0.95 * (n - 1))))],
            "miss_pct": 100.0 * rec["miss"] / n,
            "mean_regret_ms": sum(rec["regret_ms"]) / n,
            "oracle_hit_pct": 100.0 * rec["oracle_hit"] / n,
            "changed_vs_now_pct": 100.0 * rec["changed_vs_now"] / n,
            "pred_error_ms": (sum(rec["pred_error_ms"])
                              / len(rec["pred_error_ms"])
                              if rec["pred_error_ms"] else None),
        }
        op = rec["oracle_predictor"]
        od = sorted(op["delays_ms"])
        out[arm]["oracle_predictor"] = {
            "mean_delay_ms": sum(od) / n,
            "p95_delay_ms": od[min(n - 1, int(round(0.95 * (n - 1))))],
            "miss_pct": 100.0 * op["miss"] / n,
            "mean_regret_ms": sum(op["regret_ms"]) / n,
            "oracle_hit_pct": 100.0 * op["oracle_hit"] / n,
        }
    out["oracle"] = {"mean_delay_ms": 1e3 * sum(oracle_delays) / n}
    out["cell"] = cell.as_dict()
    return out


def run_all(cells=None, mode: str = "baseline") -> dict:
    if cells is None:
        cells = {"baseline": build_cells, "dose": build_dose_cells,
                 "age": build_age_cells}[mode]()
    rows = {}
    for cell in cells:
        cid = cell_id(cell)
        rows[cid] = run_cell(cell)
    return {"schema": "ta-controlled/v1",
            "mode": mode,
            "constants": {"C_bps": C_BPS, "pkt_bits": PKT_BITS,
                          "buffer_bits": BUFFER_BITS, "tx_s": TX_S,
                          "eta_ms": {k: v * 1e3 for k, v in ETA_S.items()},
                          "grid": [GRID_START_S, GRID_END_S, GRID_STEP_S],
                          "phases": PHASES,
                          "practical_delta_ms": PRACTICAL_DELTA_MS,
                          "dose_axis": DOSE_AXIS, "age_axis": AGE_AXIS},
            "cells": rows}


AGE_AXIS = {
    "geometry": "tau=100 ms differential, d=0.1 ms, ETA spread 2 ms",
    "advert_interval_ms": 50.0,
    "measurement_age_ms": [5.0, 50.0, 150.0],
    "note": ("Pre-registered BEFORE the age run: C1 -> C2 -> C4 -> C5 chain. "
             "Prediction error at the use instant, flip rate and business "
             "delay/miss are predicted to grow with the receive age; the "
             "common-flow cells must keep business metrics identical."),
}


def build_age_cells() -> list[Cell]:
    """Information-age axis: fixed cadence, varying source->receive delay."""
    cells = []
    for corr in ("differential", "common"):
        for age in AGE_AXIS["measurement_age_ms"]:
            cells.append(Cell(corr, 0.100, 0.0001, 0.050, age / 1e3,
                              eta_n_s=0.009, eta_e_s=0.011,
                              buffer_bits=20.0e6))
    return cells


def hypotheses_age(rows: dict) -> list[dict]:
    def cid(corr, age):
        for c in rows:
            row = rows[c]
            if (row["cell"]["correlation"] == corr
                    and abs(row["cell"]["measurement_age_ms"] - age) < 1e-9):
                return c
        return None

    diff = {age: cid("differential", age) for age in AGE_AXIS["measurement_age_ms"]}
    common = {age: cid("common", age) for age in AGE_AXIS["measurement_age_ms"]}
    checks = []
    err = {age: rows[c]["candidate"]["pred_error_ms"] for age, c in diff.items()}
    checks.append({
        "id": "A1",
        "claim": ("C1->C2: the mean state error at the use instant grows with "
                  "the receive age (5 < 50 < 150 ms)"),
        "cells": diff,
        "pred_error_ms": err,
        "supported": (all(v is not None for v in err.values())
                      and err[5.0] < err[50.0] < err[150.0]),
    })
    delay = {age: rows[c]["candidate"]["mean_delay_ms"] for age, c in diff.items()}
    miss = {age: rows[c]["candidate"]["miss_pct"] for age, c in diff.items()}
    checks.append({
        "id": "A2",
        "claim": ("C5: business delay at the age-150 ms cell exceeds the "
                  "age-5 ms cell by at least 1.0 ms"),
        "cells": diff,
        "mean_delay_ms": delay,
        "supported": delay[150.0] - delay[5.0] >= 1.0,
    })
    checks.append({
        "id": "A3",
        "claim": ("C5: deadline-miss at the age-150 ms cell exceeds the "
                  "age-5 ms cell by at least 1 percentage point"),
        "cells": diff,
        "miss_pct": miss,
        "supported": miss[150.0] - miss[5.0] >= 1.0,
    })
    ok = True
    for age in AGE_AXIS["measurement_age_ms"]:
        c = common[age]
        if c is None:
            ok = False
            continue
        row = rows[c]
        spans = max(abs(row[a]["mean_delay_ms"] - row["now"]["mean_delay_ms"])
                    for a in ARMS)
        ok = ok and spans < 1e-9
    checks.append({
        "id": "A4",
        "claim": ("common-flow control: every arm identical at every age "
                  "(no business effect without a candidate distinction)"),
        "cells": common,
        "supported": ok,
    })
    return checks


DOSE_AXIS = {
    "queue_swing_ms": [2.5, 10.0, 18.0],
    "margin_ms": [0.1, 8.0],
    "eta_spread_ms": [2.0, 10.0],
    "advert": ["fast", "slow"],
    "correlation": "differential (plus one common-flow validity cell pair)",
    "note": ("Pre-registered BEFORE the dose run: query-time value is predicted "
             "to scale with queue swing / path margin and with the candidate "
             "horizon spread, and to be captured only partially by the "
             "bounded_linear estimator at slow advertisement age."),
}


def build_dose_cells() -> list[Cell]:
    """Pre-registered dose matrix: queue swing x margin x horizon x advert."""
    cells = []
    # (tau, buffer) chosen so Qmax = .1*C*tau is 2.5 / 10 / 18 ms; each half
    # period is short enough that a full build+drain cycle lies inside the
    # observation horizon, and the 20 Mbit buffer never clips the construction.
    queue_levels = ((0.025, 20.0e6), (0.100, 20.0e6), (0.180, 20.0e6))
    for tau, buf in queue_levels:
        for margin in (0.0001, 0.008):
            for spread in (0.002, 0.010):
                eta_e = 0.009 + spread
                if eta_e > PATH_BASE_S["E"] + margin + 1e-12:
                    # The ETA cannot exceed the faster path's total cost; skip
                    # combinations that are physically impossible.
                    continue
                for interval, age in ((0.005, 0.005), (0.050, 0.050)):
                    cells.append(Cell(
                        "differential", tau, margin, interval, age,
                        eta_n_s=0.009, eta_e_s=eta_e, buffer_bits=buf))
    # Validity control: the same queue level with COMMON flow must show no
    # candidate distinction.
    for tau, buf in queue_levels:
        cells.append(Cell("common", tau, 0.0001, 0.050, 0.050,
                          eta_n_s=0.009, eta_e_s=0.011, buffer_bits=buf))
    return cells


def hypotheses(rows: dict) -> list[dict]:
    """Evaluate the pre-registered hypotheses on aggregate means only."""
    def delta(cid, arm="candidate"):
        now = rows[cid]["now"]["mean_delay_ms"]
        return now - rows[cid][arm]["mean_delay_ms"]

    checks = []
    h1_ids = [cid for cid in rows
              if cid.startswith("diff-") and "-tau100ms-" in cid
              and "-d0.1ms-" in cid and cid.endswith("fast")]
    checks.append({
        "id": "H1",
        "claim": "candidate beats now >= 0.5 ms in fast differential "
                 "tau=100ms d=0.1ms cells",
        "cells": h1_ids,
        "deltas_ms": {cid: delta(cid) for cid in h1_ids},
        "supported": all(delta(cid) >= PRACTICAL_DELTA_MS for cid in h1_ids),
    })
    h2_ids = [cid for cid in rows
              if cid.startswith("diff-") and "-tau2ms-" in cid]
    checks.append({
        "id": "H2",
        "claim": "no arm beats now by >= 0.5 ms in tau=2ms cells",
        "cells": h2_ids,
        "deltas_ms": {cid: max(delta(cid), delta(cid, "common"),
                               delta(cid, "stale")) for cid in h2_ids},
        "supported": all(
            max(delta(cid), delta(cid, "common"), delta(cid, "stale"))
            < PRACTICAL_DELTA_MS for cid in h2_ids),
    })
    h3_ids = [cid for cid in rows if cid.startswith("common-")]
    checks.append({
        "id": "H3",
        "claim": "common-flow cells show no candidate win >= 0.5 ms",
        "cells": h3_ids,
        "deltas_ms": {cid: max(delta(cid), delta(cid, "common"),
                               delta(cid, "stale")) for cid in h3_ids},
        "supported": all(
            max(delta(cid), delta(cid, "common"), delta(cid, "stale"))
            < PRACTICAL_DELTA_MS for cid in h3_ids),
    })
    shrink = []
    for fast in h1_ids:
        slow = fast.replace("fast", "slow")
        if slow in rows:
            shrink.append(delta(fast) > delta(slow))
    checks.append({
        "id": "H4",
        "claim": "fast-advertisement advantage exceeds slow-advertisement "
                 "advantage in the H1 cells",
        "cells": [h1_ids, [c.replace("fast", "slow") for c in h1_ids]],
        "supported": bool(shrink) and all(shrink),
    })
    return checks


def hypotheses_dose(rows: dict) -> list[dict]:
    """Evaluate the pre-registered dose hypotheses on aggregate means."""
    def pure(cid):
        row = rows[cid]
        return (row["now"]["oracle_predictor"]["mean_delay_ms"]
                - row["candidate"]["oracle_predictor"]["mean_delay_ms"])

    def real(cid):
        row = rows[cid]
        return (row["now"]["mean_delay_ms"]
                - row["candidate"]["mean_delay_ms"])

    def by_swing(swing):
        return [cid for cid in rows
                if f"qswing{swing}" in cid and "d0.1ms" in cid
                and "spread2ms" in cid and cid.endswith("fast")]
    big, mid, small = by_swing("18"), by_swing("10"), by_swing("2.5")
    checks = []
    checks.append({
        "id": "H5",
        "claim": ("pure query-time value is monotone in the queue swing "
                  "(18 ms > 10 ms > 2.5 ms) and the 18 ms / 2.5 ms ratio is "
                  "at least 2x"),
        "cells": {"big": big, "mid": mid, "small": small},
        "values_ms": {"big": {c: round(pure(c), 4) for c in big},
                      "mid": {c: round(pure(c), 4) for c in mid},
                      "small": {c: round(pure(c), 4) for c in small}},
        "supported": (bool(big) and bool(mid) and bool(small)
                      and all(pure(b) > pure(m) for b in big for m in mid)
                      and all(pure(m) > pure(sm) for m in mid for sm in small)
                      and all(pure(b) >= 2.0 * pure(sm)
                              for b in big for sm in small)),
    })
    slow_cells = [cid for cid in rows if cid.endswith("slow")]
    capture = {cid: (real(cid) / pure(cid) if pure(cid) > 1e-9 else None)
               for cid in slow_cells}
    fast_cells = [cid for cid in rows if cid.endswith("fast")]
    capture_fast = {cid: (real(cid) / pure(cid) if pure(cid) > 1e-9 else None)
                    for cid in fast_cells}
    checks.append({
        "id": "H6",
        "claim": ("the estimator captures < 50% of the pure query-time value "
                  "at 50 ms advertisement age"),
        "cells": {"slow": slow_cells},
        "capture_slow": {k: (None if v is None else round(v, 3))
                         for k, v in capture.items()},
        "capture_fast_median": _median(
            [v for v in capture_fast.values() if v is not None]),
        "supported": all(v is not None and v < 0.5 for v in capture.values()),
    })
    wide = [cid for cid in rows if "spread10ms" in cid
            and "qswing10" in cid and "d0.1ms" in cid and cid.endswith("fast")]
    narrow = [cid for cid in rows if "spread2ms" in cid
              and "qswing10" in cid and "d0.1ms" in cid and cid.endswith("fast")]
    checks.append({
        "id": "H7",
        "claim": ("the wider candidate horizon (10 ms) yields more pure "
                  "query-time value than the 2 ms horizon at the same queue "
                  "level"),
        "cells": {"wide": wide, "narrow": narrow},
        "values_ms": {"wide": {c: pure(c) for c in wide},
                      "narrow": {c: pure(c) for c in narrow}},
        "supported": bool(wide) and bool(narrow)
        and all(pure(w) > pure(n) for w in wide for n in narrow),
    })
    common = [cid for cid in rows if cid.startswith("common-")]
    checks.append({
        "id": "H8",
        "claim": ("common-flow validity: every arm identical (no candidate "
                  "query-time distinction)"),
        "cells": common,
        "supported": all(
            abs(rows[c]["candidate"]["mean_delay_ms"]
                - rows[c]["now"]["mean_delay_ms"]) < 1e-9 for c in common),
    })
    return checks


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 3)
    return round(0.5 * (ordered[mid - 1] + ordered[mid]), 3)


def render_markdown(result: dict) -> str:
    lines = ["# Controlled two-resource mechanism experiment", ""]
    lines.append("All delays are business delays in ms; `delta = now - arm` "
                 "(positive means the arm is better).")
    lines.append("")
    header = ("| cell | oracle | stale | now | common | candidate | "
              "miss% (stale/now/common/cand) | oracle-hit% (cand/now) |")
    sep = "|---|---:|---:|---:|---:|---:|---|---|"
    lines += [header, sep]
    for cid, row in result["cells"].items():
        lines.append(
            "| {cid} | {oracle:.3f} | {s:.3f} | {n:.3f} | {c:.3f} | {k:.3f} | "
            "{ms:.1f}/{mn:.1f}/{mc:.1f}/{mk:.1f} | {hk:.1f}/{hn:.1f} |".format(
                cid=cid,
                oracle=row["oracle"]["mean_delay_ms"],
                s=row["stale"]["mean_delay_ms"],
                n=row["now"]["mean_delay_ms"],
                c=row["common"]["mean_delay_ms"],
                k=row["candidate"]["mean_delay_ms"],
                ms=row["stale"]["miss_pct"], mn=row["now"]["miss_pct"],
                mc=row["common"]["miss_pct"], mk=row["candidate"]["miss_pct"],
                hk=row["candidate"]["oracle_hit_pct"],
                hn=row["now"]["oracle_hit_pct"]))
    lines.append("")
    lines.append("## Pre-registered hypotheses")
    lines.append("")
    for check in result.get("hypotheses") or hypotheses(result["cells"]):
        lines.append(f"* **{check['id']}** {'SUPPORTED' if check['supported'] else 'REFUTED'}"
                     f" — {check['claim']}")
        deltas = check.get("deltas_ms")
        if deltas is not None:
            lines.append(
                "  deltas: "
                + json.dumps({k: round(v, 3) for k, v in deltas.items()}))
        else:
            lines.append("  cells: " + json.dumps(check["cells"]))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="result JSON path")
    ap.add_argument("--markdown", default=None)
    ap.add_argument("--mode", choices=("baseline", "dose", "age"),
                    default="baseline")
    args = ap.parse_args(argv)
    result = run_all(mode=args.mode)
    result["host"] = {"platform": platform.platform(),
                      "python": platform.python_version()}
    result["hypotheses"] = {
        "baseline": lambda: hypotheses(result["cells"]),
        "dose": lambda: hypotheses_dose(result["cells"]),
        "age": lambda: hypotheses_age(result["cells"]),
    }[args.mode]()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(result), encoding="utf-8")
    print(json.dumps({"status": "ok", "cells": len(result["cells"]),
                      "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
