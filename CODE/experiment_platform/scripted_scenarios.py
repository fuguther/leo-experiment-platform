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

    def ground_visible(self, sat_id, lat, lon, t):
        for spec in CELLS.values():
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
    return (resolved, [dict(r) for r in spec["rows"]], ScriptedGeometry(),
            {"scenario": name, "purpose": spec["purpose"],
             "declared": spec["declared"],
             "topology": {str(k): dict(v) for k, v in TOPO.items()},
             "cells": {k: dict(v) for k, v in CELLS.items()}})
