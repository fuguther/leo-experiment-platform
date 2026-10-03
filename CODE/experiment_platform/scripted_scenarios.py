"""Deterministic scripted scenarios for the minimal branch comparison.

Why these exist
---------------
The profile-based fixture (CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml,
a real Walker constellation) is a FAILURE-diagnosis case: at every branch
point it offers, the non-preferred candidate leaves the packet at a peer that
never learns a route, so the contrast it produces is "delivers / never
delivers".  That is a real result and it is KEPT, but it cannot answer "what
does each candidate cost" -- both candidates have to be able to deliver before
a cost comparison means anything.

These scripted scenarios are the smallest topology in which BOTH candidates of
one frozen branch point are viable, so the comparison has a graded outcome to
report instead of a feasibility verdict.  They are parameter tables plus a
scripted geometry, not recorded runs: the numbers in the parameter table below
are DECLARED BEFORE the run and the driver reports what actually happened
against them.

Topology (two parallel two-hop paths, one shared destination server)
-------------------------------------------------------------------
    sat0 --E--> sat1 --E--> sat3        sat3 sees the destination D
     |                       ^          sat0 sees the source S
     +--W--> sat2 --S--------+          sat1 sees the competing source S2
                                        sat0 and sat1 do NOT see D, so neither
                                        may deliver and both must forward

Directions are directed edges; the reverse pairs (1.W->0, 2.W->0, 3.W->1,
3.N->2) exist so the topology is physically bidirectional.

The downstream policy is the SAME in both branches and pre-declared: the
routing policy is "hop" for every satellite, and a peer that may not deliver
forwards on its only legal egress.  Nothing about the policy depends on which
candidate the baseline happened to choose.

Hand-checkable arithmetic (declared, then measured)
---------------------------------------------------
Links are constant-rate at ISL_RATE_MBPS = 1.0, so one packet of B bits costs
exactly B / 1e6 seconds of service on every ISL hop, and the access links are
declared at 100 Mbps so the uplink/downlink legs cost 0.01 s per Mbit.

  reachability : one 1 Mbit packet.  Both branches deliver it; the two
                 branches differ in which peer forwards it and, because the
                 two paths are geometrically identical here, not in how long
                 each hop takes.
  contention   : the SAME target packet, plus one declared 4 Mbit packet from
                 S2.  That packet can only leave through isl:2? no -- through
                 the peer it reaches (sat1) and its egress isl:1:3, which is
                 exactly the egress the target contends for when the baseline
                 chooses E.  Its declared service time on that egress is
                 4 Mbit / 1 Mbps = 4.0 s, so the target's wait at isl:1:3 is
                 the part of those 4.0 s still outstanding when it enqueues.
"""
from __future__ import annotations

import copy

from CODE.leo_sim import config as config_mod, grid

#: Declared constants.  Changing any of these changes the arithmetic above and
#: must be accompanied by an update to the docstring, not just to the number.
ISL_RATE_MBPS = 1.0
PACKET_BITS = 1_000_000
COMPETING_BITS = 4_000_000
TARGET_PID = 10
COMPETING_PID = 1
TARGET_EMIT_S = 6.0
COMPETING_EMIT_S = 4.0
DURATION_S = 30.0
COMPUTE_DELAY_S = 0.1
ADVERTISE_INTERVAL_S = 0.5

def _cell(lat: float, lon: float, agg_deg: float = 1.0) -> str:
    """The aggregate cell id the platform itself would assign.

    The lat/lon are NOT guessed from the id: the kernel asks visibility with
    the endpoint's true cell centre, so a table of hand-written coordinates
    silently produces ACCESS_REJECTED.  Deriving both the id and the centre
    from the same grid makes the fixture self-consistent by construction.
    """
    return grid.aggregate_id(grid.grid_id(lat, lon, 0.25), agg_deg)


SRC = _cell(0.0, 0.0)           # cell seen only by sat 0
COMPETING_SRC = _cell(0.0, 5.0)  # cell seen only by sat 1
DST = _cell(0.0, 20.0)          # cell seen only by sat 3

TOPO = {0: {"E": 1, "W": 2}, 1: {"W": 0, "E": 3},
        2: {"W": 0, "S": 3}, 3: {"W": 1, "N": 2}}
NUM_SATELLITES = 4
ISL_KM = 300.0
SLANT_KM = 600.0
CELLS = {
    SRC: {"sat": 0, "center": grid.grid_center(SRC)},
    COMPETING_SRC: {"sat": 1, "center": grid.grid_center(COMPETING_SRC)},
    DST: {"sat": 3, "center": grid.grid_center(DST)},
}


class ScriptedGeometry:
    """A fixed constellation and a fixed visibility table.

    Duck-typed to the kernel's geometry contract; it declares
    certifies_change_times because nothing here ever changes, so the kernel
    must not go looking for a change timeline it will never find.
    """

    num_satellites = NUM_SATELLITES
    certifies_change_times = True

    def __init__(self, cells=None):
        # Default keeps every pre-existing scenario on the original table.
        self.cells = dict(CELLS if cells is None else cells)

    def ground_visible(self, sat_id, lat, lon, t):
        for spec in self.cells.values():
            clat, clon = spec["center"]
            if abs(clat - lat) < 1e-6 and abs(clon - lon) < 1e-6:
                return sat_id == spec["sat"]
        return False

    def elevation_deg(self, sat_id, lat, lon, t):
        return 90.0 if self.ground_visible(sat_id, lat, lon, t) else -10.0

    def slant_range_km(self, sat_id, lat, lon, t):
        return SLANT_KM

    def isl_range_km(self, a, b, t):
        return ISL_KM

    def neighbors(self, sat_id, dirs):
        return {d: n for d, n in TOPO.get(sat_id, {}).items() if d in dirs}

    def neighbors_at(self, sat_id, dirs, t):
        return self.neighbors(sat_id, dirs)

    def positions(self, t):
        return tuple((0.0, 0.0, 0.0) for _ in range(NUM_SATELLITES))

    def subpoint(self, sat_id, t):
        """Nadir point of a satellite: (lat_deg, lon_deg, alt_km).

        A1 gap: the LEARNING observation asks for the root satellite own
        position (leo_sim.learning.destination_features), so the geometry
        duck-type must answer it.  ScriptedGeometry declared itself as
        implementing the kernel geometry contract but never needed this method
        while every scripted scenario ran with learning.algorithm=none; the
        first real checkpoint read reached it immediately.
        """
        return (0.0, 0.0, 500.0)

    def gsl_available(self, sat_id, lat, lon, t):
        return self.ground_visible(sat_id, lat, lon, t)

    def next_gsl_change(self, sat_id, lat, lon, t, limit):
        return None

    def isl_available(self, a, b, t):
        return b in TOPO.get(a, {}).values()

    def next_isl_change(self, a, b, t, limit):
        return None


def _base_user():
    return {
        "scenario": {"name": "scripted_branch", "duration_s": DURATION_S,
                     "time_step_s": 0.1, "num_satellites": NUM_SATELLITES,
                     "num_planes": 1, "seed": 1},
        "access": {"hysteresis_deg": 0.0, "min_dwell_s": 0.0,
                   "acquisition_delay_s": 0.0, "idle_release_s": 1000.0,
                   "slot_lease_s": 1000.0},
        "control_plane": {"enabled": True,
                          "advertise_interval_s": ADVERTISE_INTERVAL_S,
                          "vis_k": 2},
        "routing": {"policy": "hop"},
        "links": {"rate_model": "constant", "isl_rate_mbps": ISL_RATE_MBPS,
                  "isl_queue_bits": 64_000_000},
        "demand": {"packet_bits": PACKET_BITS},
        "learning": {"algorithm": "none"},
        "execution": {"compute_delay_s": COMPUTE_DELAY_S,
                      "decision_observation_mode": "frozen",
                      "node_process_delay_s": 0.0,
                      "max_events": 200000, "max_packets": 100},
    }


def _row(pid, t, src, dst, bits):
    return {"packet_id": pid, "emit_time_s": t, "src_grid_id": src,
            "dst_grid_id": dst, "bits": bits, "deadline_at_s": None}


# --------------------------------------------------------------------------
# 2026-10-03 P0 mechanism scenarios (ADDED; the names above are untouched)
# --------------------------------------------------------------------------
# Two gaps were measured on the earlier diagnostics and are fixed here:
#
#   resource_mapping (target_egress_identity_unavailable)
#       advertisement_protocol_version=1 puts only {peer, value} on the wire
#       for every egress, so the kernel cannot bind an online prediction to a
#       concrete peer egress.  Version 2 adds generation, rate_bps and
#       work_ahead_bits_proxy, which the resource mapping requires.
#   no_received_history
#       Received history rows are filtered by matching egress generation;
#       with v1 there is no generation to match, so every arm -- stale, now,
#       common and candidate -- saw an empty history and fell back.
#
# The earlier fixtures also inherited compute_servers_per_satellite=0, which
# the kernel reports as unbounded_pool_no_wait; that is not a finite
# computing resource, so the bounded pool is declared here instead.
V2_CONTROL_PLANE = {
    "enabled": True,
    "advertise_interval_s": ADVERTISE_INTERVAL_S,
    "vis_k": 2,
    "advertisement_protocol_version": 2,
}

V2_EXECUTION = {
    "compute_delay_s": COMPUTE_DELAY_S,
    "decision_observation_mode": "frozen",
    "node_process_delay_s": 0.0,
    "compute_servers_per_satellite": 1,
    "max_events": 200000,
    "max_packets": 100,
}

#: Cell seen only by satellite 2, so the OTHER branch (sat2 -> isl:2:3) can
#: be loaded too and the two candidate egresses can be brought close enough
#: together that a state-time correction is able to cross the ranking gap.
W_SRC = _cell(0.0, -5.0)
CELLS_W = dict(CELLS)
CELLS_W[W_SRC] = {"sat": 2, "center": grid.grid_center(W_SRC)}

# E-branch load: ONE 1 Mbit packet on isl:1:3 whose service ends shortly
# after the decision instant, so its advertised work falls at link rate.
E_LOAD_EMIT_S = 5.29
E_LOAD_BITS = 1_000_000
# W-branch load: small packets entering isl:2:3 faster than it can serve, so
# the advertised work on that egress RISES across the same history window.
W_LOAD_FIRST_S = 5.80
W_LOAD_LAST_S = 7.00
W_LOAD_PERIOD_S = 0.05
W_LOAD_BITS = 100_000
#: Target emit time chosen so the decision lands about 0.31 s after the
#: advertisement generated at t = 6.0 -- that offset is the stale arm's
#: state age, and it is what the stale arm is wrong about.
CROSS_TARGET_EMIT_S = 6.30


def _w_load_rows(first_pid):
    rows = []
    t = W_LOAD_FIRST_S
    pid = first_pid
    while t <= W_LOAD_LAST_S + 1e-9:
        rows.append(_row(pid, round(t, 6), W_SRC, DST, W_LOAD_BITS))
        pid += 1
        t += W_LOAD_PERIOD_S
    return rows




# --------------------------------------------------------------------------
# Third-round H1 scenario: a trend ALREADY VISIBLE before the decision
# --------------------------------------------------------------------------
# Hand-computed design (declared BEFORE the run, to be checked against it):
#
#   A = isl:1:3 (E branch)   arrivals 1.5 Mbps vs 1 Mbps service
#                            -> net +0.5 Mbit/s, rising from t=2.5 s
#   B = isl:2:3 (W branch)   arrivals 2.5 Mbps until t=4.0 s, then pure drain
#                            -> net -1.0 Mbit/s after 4.0 s
#
#   The predictor takes the MEDIAN of the 7 slopes of the last 8 samples
#   (measured_at 2.5, 3.0, ... 6.0 s at a 0.5 s advertisement interval).
#   All 7 A-slopes are positive (+0.5 Mbit/s) and 4 of the 7 B-slopes are
#   negative (-1.0 Mbit/s), so BOTH medians are non-zero -- which is what
#   the earlier scenarios lacked: there the median was 0 for both
#   resources, so every query instant returned the same number.
#
#   Prediction at the three query instants (snapshot at 6.312 s):
#     work_A: 6.000 -> 1.75 Mbit   6.312 -> 1.906   7.413 -> 2.457
#     work_B: 6.000 -> 1.80 Mbit   6.312 -> 1.488   7.413 -> 0.387
#   Ranking:  stale sees A=1.75 < B=1.80  -> picks A
#             now/future sees A > B        -> picks B
#   Realised: the packet reaches isl:1:3 at ~7.51 s (A still rising,
#   ~2.5 Mbit ahead) and isl:2:3 at ~8.40 s (B drained empty), so B is the
#   actually cheaper action and the stale arm should lose real completion
#   time.  This is the crossover the earlier cells never had.
H1_TARGET_EMIT_S = 6.30
#: Neighbour/phase control: same scenario, target 0.5 s earlier, so the
#: last received advertisement is the one generated at 5.5 s instead of 6.0.
H1_NEIGHBOUR_EMIT_S = 5.80

#: Fourth-round frozen phases.  Chosen from the known 0.5 s advertisement
#: period and how the received history window moves, NOT from any outcome:
#:   6.05 and 6.45 keep the same 8-sample window (2.5-6.0 s) as the 6.30
#:     reference and differ only in the decision instant, which straddles
#:     the unfitted crossing time t_cross = 6.0 + (2.493000-1.925401)/
#:     (0.6356-(-0.982)) = 6.350890 s;
#:   6.55 crosses to the next advertisement, so its window becomes 3.0-6.5 s.
H1_PHASE_605_S = 6.05
H1_PHASE_645_S = 6.45
H1_PHASE_655_S = 6.55

A_FIRST_S = 2.5
A_LAST_S = 8.0
A_PERIOD_S = 0.0667
A_BITS = 100_000
B_FIRST_S = 1.5
B_LAST_S = 4.0
B_PERIOD_S = 0.04
B_BITS = 100_000

#: The background streams ask for 15/s of routing decisions at sat1 and
#: 25/s at sat2.  DECLARED IDEAL CONDITION: the compute service is shortened
#: to 10 ms on the SINGLE server, giving 100 jobs/s per satellite, so the
#: streams sit at 15% and 25% utilisation.  Compute congestion is thereby
#: kept out of the egress-queue mechanism.
#:
#: The server COUNT must stay 1.  The shared projection deliberately refuses
#: multi-server telemetry (servers not in (0,1)) and reports the peer wait as
#: unknown, which makes every candidate fall back -- measured on the VM in
#: ta-third-h1_visible-20261003-01, where 8 servers produced exactly that.
H1_COMPUTE_DELAY_S = 0.01
H1_COMPUTE_SERVERS = 1
H1_CONTROL_PLANE = dict(V2_CONTROL_PLANE)
H1_EXECUTION = dict(V2_EXECUTION, compute_delay_s=H1_COMPUTE_DELAY_S,
                    compute_servers_per_satellite=H1_COMPUTE_SERVERS,
                    max_packets=400)


def _stream_rows(first_pid, first_s, last_s, period_s, bits, src):
    rows = []
    t = first_s
    pid = first_pid
    while t <= last_s + 1e-9:
        rows.append(_row(pid, round(t, 6), src, DST, bits))
        pid += 1
        t += period_s
    return rows


def _h1_rows(target_emit_s):
    return ([_row(TARGET_PID, target_emit_s, SRC, DST, PACKET_BITS)]
            + _stream_rows(200, A_FIRST_S, A_LAST_S, A_PERIOD_S, A_BITS,
                           COMPETING_SRC)
            + _stream_rows(300, B_FIRST_S, B_LAST_S, B_PERIOD_S, B_BITS,
                           W_SRC))


def _h1_declared(target_emit_s, expectation=None):
    return {
        "target_bits": PACKET_BITS,
        "target_emit_s": target_emit_s,
        "a_resource": "isl:1:3",
        "b_resource": "isl:2:3",
        "a_first_s": A_FIRST_S, "a_last_s": A_LAST_S,
        "a_period_s": A_PERIOD_S, "a_bits": A_BITS,
        "b_first_s": B_FIRST_S, "b_last_s": B_LAST_S,
        "b_period_s": B_PERIOD_S, "b_bits": B_BITS,
        "advertisement_protocol_version": 2,
        "compute_servers_per_satellite": H1_COMPUTE_SERVERS,
        "compute_delay_s": H1_COMPUTE_DELAY_S,
        "declared_compute_utilisation": "15/100 at sat1, 25/100 at sat2",
        "declared_median_slope_a_mbit_s": 0.5,
        "declared_median_slope_b_mbit_s": -1.0,
        "declared_work_a_mbit_at_6s": 1.75,
        "declared_work_b_mbit_at_6s": 1.80,
        "declared_expectation": (expectation or (
            "stale should see A cheaper than B and pick A; now/common/"
            "candidate should extrapolate A above B and pick B; B is the "
            "actually cheaper action because A is still rising when the "
            "packet arrives and B has drained empty.  Declared, not "
            "measured; a mismatch is a result, not a failure to hide.")),
    }


#: name -> (customer overrides, trace rows, what the run is expected to show)
SCENARIOS = {
    "reachability": {
        "purpose": "no load: both candidate directions must deliver",
        "overrides": {},
        "rows": [_row(TARGET_PID, TARGET_EMIT_S, SRC, DST, PACKET_BITS)],
        "declared": {
            "target_bits": PACKET_BITS,
            "competing_bits": 0,
            "isl_service_s_per_mbit": 1.0 / ISL_RATE_MBPS,
            "note": "both legal candidates must reach the destination; if "
                    "either does not, the scenario is broken, not the "
                    "mechanism",
        },
    },
    "same_flow": {
        "purpose": "many packets on ONE flow, so a per-flow cache can be hit",
        "overrides": {},
        "rows": [_row(TARGET_PID + i, TARGET_EMIT_S + 0.05 * i, SRC, DST,
                      PACKET_BITS) for i in range(8)],
        "declared": {
            "target_bits": PACKET_BITS,
            "flow_packets": 8,
            "inter_arrival_s": 0.05,
            "isl_service_s_per_mbit": 1.0 / ISL_RATE_MBPS,
            "note": "same satellite, same source and destination: a per-flow "
                    "cache with a TTL above 0.05 s must hit, and a per-packet "
                    "arm must recompute every time",
        },
    },
    "contention": {
        "purpose": "one declared competing packet on the target egress",
        "overrides": {},
        "rows": [_row(TARGET_PID, TARGET_EMIT_S, SRC, DST, PACKET_BITS),
                 _row(COMPETING_PID, COMPETING_EMIT_S, COMPETING_SRC, DST,
                      COMPETING_BITS)],
        "declared": {
            "target_bits": PACKET_BITS,
            "competing_bits": COMPETING_BITS,
            "competing_source": COMPETING_SRC,
            "isl_service_s_per_mbit": 1.0 / ISL_RATE_MBPS,
            "declared_competing_service_s": COMPETING_BITS / (ISL_RATE_MBPS * 1e6),
            "note": "the competing packet leaves through the peer's egress "
                    "isl:1:3, the same resource the target contends for when "
                    "the baseline takes E; the target wait is the outstanding "
                    "part of the declared service time",
        },
    },
    # ---- 2026-10-03 P0 scenarios: protocol v2 + finite compute pool ------
    "flat_v2": {
        "purpose": "P0 negative control: v2 wire, no competing load at all",
        "overrides": {"control_plane": V2_CONTROL_PLANE,
                      "execution": V2_EXECUTION},
        "rows": [_row(TARGET_PID, TARGET_EMIT_S, SRC, DST, PACKET_BITS)],
        "declared": {
            "target_bits": PACKET_BITS,
            "competing_bits": 0,
            "advertisement_protocol_version": 2,
            "compute_servers_per_satellite": 1,
            "note": "every egress is empty and stays empty, so all four "
                    "arms must agree; any arm difference here would be a "
                    "harness artefact, not a state-time effect",
        },
    },
    "drain_v2": {
        "purpose": "P0 positive control: the earlier contention fixture on v2",
        "overrides": {"control_plane": V2_CONTROL_PLANE,
                      "execution": V2_EXECUTION},
        "rows": [_row(TARGET_PID, TARGET_EMIT_S, SRC, DST, PACKET_BITS),
                 _row(COMPETING_PID, COMPETING_EMIT_S, COMPETING_SRC, DST,
                      COMPETING_BITS)],
        "declared": {
            "target_bits": PACKET_BITS,
            "competing_bits": COMPETING_BITS,
            "declared_competing_service_s":
                COMPETING_BITS / (ISL_RATE_MBPS * 1e6),
            "advertisement_protocol_version": 2,
            "compute_servers_per_satellite": 1,
            "note": "identical science to the v1 contention fixture; only "
                    "the wire version and the compute bound change, so a "
                    "difference here isolates the P0 fix",
        },
    },
    "cross_v2": {
        "purpose": "the two candidate egresses cross inside the observed "
                    "history: E-branch work falls at link rate, W-branch "
                    "work rises, and the crossing instant sits between the "
                    "newest received advertisement and the decision instant",
        "overrides": {"control_plane": V2_CONTROL_PLANE,
                      "execution": V2_EXECUTION},
        "cells": CELLS_W,
        "rows": ([_row(TARGET_PID, CROSS_TARGET_EMIT_S, SRC, DST,
                       PACKET_BITS),
                  _row(COMPETING_PID, E_LOAD_EMIT_S, COMPETING_SRC, DST,
                       E_LOAD_BITS)]
                 + _w_load_rows(100)),
        "declared": {
            "target_bits": PACKET_BITS,
            "target_emit_s": CROSS_TARGET_EMIT_S,
            "e_side_bits": E_LOAD_BITS,
            "e_side_emit_s": E_LOAD_EMIT_S,
            "w_side_bits_each": W_LOAD_BITS,
            "w_side_emit_span_s": [W_LOAD_FIRST_S, W_LOAD_LAST_S],
            "w_side_period_s": W_LOAD_PERIOD_S,
            "advertisement_protocol_version": 2,
            "compute_servers_per_satellite": 1,
            "note": "declared expectation, not a measured result: the "
                    "stale arm should see the E egress still loaded and "
                    "the W egress light, while now/common/candidate see "
                    "the E egress drained and the W egress loaded",
        },
    },
    # ---- third-round H1 cell + its phase neighbour ----------------------
    "h1_visible": {
        "purpose": "one H1 competition cell whose trend is already "
                    "visible in the history received before t0, so the "
                    "query instant can actually change the ranking",
        "overrides": {"control_plane": H1_CONTROL_PLANE,
                      "execution": H1_EXECUTION},
        "cells": CELLS_W,
        "rows": _h1_rows(H1_TARGET_EMIT_S),
        "declared": _h1_declared(H1_TARGET_EMIT_S),
    },
    "h1_visible_shift": {
        "purpose": "phase neighbour of h1_visible: identical science, "
                    "target 0.5 s earlier, to test whether the effect "
                    "depends on one exact emission instant",
        "overrides": {"control_plane": H1_CONTROL_PLANE,
                      "execution": H1_EXECUTION},
        "cells": CELLS_W,
        "rows": _h1_rows(H1_NEIGHBOUR_EMIT_S),
        "declared": _h1_declared(H1_NEIGHBOUR_EMIT_S),
    },
    # ---- fourth-round frozen phases (declared before running) -----------
    "h1_phase_605": {
        "purpose": "frozen phase 6.05 s: same 8-sample window as the "
                    "6.30 reference, decision instant BEFORE t_cross",
        "overrides": {"control_plane": H1_CONTROL_PLANE,
                      "execution": H1_EXECUTION},
        "cells": CELLS_W,
        "rows": _h1_rows(H1_PHASE_605_S),
        "declared": _h1_declared(
            H1_PHASE_605_S,
            "DECLARED BEFORE THE RUN: 8-sample window 2.5-6.0 s, median "
            "slopes A=+0.6356 and B=-0.982 Mbit/s, t0=6.062001 < t_cross "
            "6.350890 s, so stale and now are expected to pick E and "
            "common/candidate W, the same split as the 6.30 reference."),
    },
    "h1_phase_645": {
        "purpose": "frozen phase 6.45 s: same 8-sample window, decision "
                    "instant AFTER t_cross",
        "overrides": {"control_plane": H1_CONTROL_PLANE,
                      "execution": H1_EXECUTION},
        "cells": CELLS_W,
        "rows": _h1_rows(H1_PHASE_645_S),
        "declared": _h1_declared(
            H1_PHASE_645_S,
            "DECLARED BEFORE THE RUN: same window and slopes as the 6.05 "
            "and 6.30 cells, but t0=6.462001 > t_cross 6.350890 s, so now "
            "is expected to join common/candidate on W while stale still "
            "picks E.  This is the point that tests whether the crossing "
            "time is the real boundary rather than the emission instant."),
    },
    "h1_phase_655": {
        "purpose": "frozen phase 6.55 s: decision after the 6.5 s "
                    "advertisement, so the 8-sample window shifts to 3.0-6.5 s",
        "overrides": {"control_plane": H1_CONTROL_PLANE,
                      "execution": H1_EXECUTION},
        "cells": CELLS_W,
        "rows": _h1_rows(H1_PHASE_655_S),
        "declared": _h1_declared(
            H1_PHASE_655_S,
            "DECLARED BEFORE THE RUN: window 3.0-6.5 s.  A keeps its "
            "positive slopes (+0.4356/+0.6356) and B keeps 5 of 7 negative "
            "(-0.982), so the medians are +0.6356 and -0.982 as before, but "
            "A is already above B at the last measurement (about 2.24 vs "
            "2.00 Mbit), so stale is expected to pick W as well and NO arm "
            "difference is expected: this is a declared boundary/negative "
            "point, not a hoped-for win."),
    },
}


def build(name: str):
    """Return (resolved_config, trace_rows, geometry, declared_meta)."""
    if name not in SCENARIOS:
        raise KeyError(f"unknown scripted scenario {name!r}; "
                       f"available: {sorted(SCENARIOS)}")
    spec = SCENARIOS[name]
    user = copy.deepcopy(_base_user())
    for key, value in spec["overrides"].items():
        user[key] = value
    resolved = config_mod.resolve_config(user)
    return (resolved, [dict(r) for r in spec["rows"]],
            ScriptedGeometry(spec.get("cells")),
            {"scenario": name, "purpose": spec["purpose"],
             "declared": spec["declared"],
             "topology": {str(k): dict(v) for k, v in TOPO.items()},
             "cells": {k: dict(v) for k, v in CELLS.items()}})
