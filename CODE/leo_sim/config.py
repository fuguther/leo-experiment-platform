"""Strict, versioned YAML configuration for leo_sim V2.

Resolution order: built-in defaults -> named profile -> user file -> explicit
overrides. The result is a single canonical object with a SHA256 identity.
Unknown fields and invalid combinations are rejected (fail closed).
No environment-variable bridge exists in this package.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from typing import Any, Mapping

import yaml

from . import link_budget

CONFIG_SCHEMA_VERSION = "leo-sim-config/v1"
TRACE_IDENTITY_VERSION_V1 = "leo-sim-trace-identity/v1"
TRACE_IDENTITY_VERSION_V2 = "leo-sim-trace-identity/v2"
TRACE_IDENTITY_VERSION = "leo-sim-trace-identity/v3"


class ConfigError(ValueError):
    """Raised for any configuration validation failure."""


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable mapping key", key_node.start_mark) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping)


# Allowed fields per top-level group. Anything else is rejected.
SCHEMA: dict[str, dict[str, type | tuple[type, ...]]] = {
    "scenario": {
        "name": str,
        "duration_s": (int, float),
        "time_step_s": (int, float),
        "num_satellites": int,
        "num_planes": int,
        "altitude_km": (int, float),
        "inclination_deg": (int, float),
        "min_elevation_deg": (int, float),
        "seed": int,
        "geometry_epoch_s": (int, float),
    },
    "endpoints": {
        "grid_deg": (int, float),
        "aggregation_deg": (int, float),
        "sites": list,  # list of {name, lat, lon, demand_weight}
        # Explicit opt-in for deterministic endpoint selection from the
        # repository M-Lab measurement snapshot. Existing named-site
        # profiles remain byte-compatible when this is false.
        "mlab_auto": bool,
        "mlab_max_sites": int,
    },
    "demand": {
        "mode": str,  # uniform|gravity|hotspot|burst|diurnal|csv|mlab
        "offered_mbps": (int, float),
        "emission_end_s": (int, float, type(None)),
        "packet_bits": int,
        "deadline_s": (int, float, type(None)),
        "csv_path": (str, type(None)),
        "population_path": (str, type(None)),
        "source_population_exponent": (int, float),
        "destination_population_exponent": (int, float),
        "gravity_alpha": (int, float),
        "gravity_d_floor_km": (int, float),
        "hotspot_fraction": (int, float),
        "hotspot_concentration": (int, float),
        "burst_start_s": (int, float, type(None)),
        "burst_duration_s": (int, float, type(None)),
        "burst_multiplier": (int, float),
        "diurnal_amplitude": (int, float),
        "diurnal_phase_h": (int, float),
        # Global populated-land direct-access scene (frozen in Task 1):
        "temporal_model": str,  # constant | local_diurnal_cosine
        "utc_start_hour": (int, float),
        "population_destination_sampler": str,  # scan | alias_rejection
        "destination_rejection_max_draws": int,
        "nested_master_offered_mbps": (int, float, type(None)),
    },
    "access": {
        "unavailable_policy": str,  # reject | queue
        "slots_per_satellite": int,  # K
        "uplink_rate_mbps": (int, float),
        "downlink_rate_mbps": (int, float),
        "uplink_queue_bits": int,
        "downlink_queue_bits": int,
        "drr_quantum_bits": int,
        "association": str,  # bbm | mbb
        "hysteresis_deg": (int, float),  # elevation-angle margin, degrees
        "min_dwell_s": (int, float),
        "acquisition_delay_s": (int, float),
        "retiring_link_limit": int,
        "retirement_deadline_s": (int, float),
        "dual_connect": bool,
        "slot_lease_s": (int, float),  # max hold time under contention
        "idle_release_s": (int, float),  # idle hold before releasing to waiters
        "holding_queue_bits": int,  # per-satellite re-decision holding area
    },
    "links": {
        "rate_model": str,  # constant|mcs (legacy distance-dependent MCS rates)
        "mcs_table": str,   # legacy-dvbs2x (the only supported table)
        "rf_isl": dict,     # legacy markovianMatchingTwo ISL RF params
        "rf_uplink": dict,  # legacy Gateway.gs2ngeo (ground -> satellite)
        "rf_downlink": dict,  # legacy Satellite.ngeo2gt (satellite -> ground)
        "isl_rate_mbps": (int, float),
        "isl_queue_bits": int,
        "isl_dirs": list,  # subset of ["N","S","E","W"]
        "max_isl_km": (int, float),
        "geometry_loss": bool,
        "ge_enabled": bool,
        "ge_gsl": dict,  # {mean_good_s, mean_bad_s} continuous-time, abstract defaults
        "ge_isl": dict,
    },
    "topology": {
        "recompute_interval_s": (int, float, type(None)),
        "matching": str,  # markovian (legacy greedy shortest-edge matching)
    },
    "control_plane": {
        "enabled": bool,
        "vis_k": int,  # the single propagation-limit contract (actual ISL hops)
        "ttl_s": (int, float),
        "advertise_interval_s": (int, float),
        "packet_bits": int,
        "priority": str,  # nonpreemptive_priority (only supported mode)
    },
    "routing": {
        "policy": str,  # hop|delay|capacity|oracle|info_queue|info_physical
        "max_hops": int,  # data-packet loop cap
        "learning_enabled": bool,
        "contract": str,  # C1|C3|C4|C5|C6|C7 (observation contracts)
    },
    "learning": {
        "algorithm": str,  # ddqn | none
        "mode": str,  # train | eval
        "seed": (int, type(None)),  # train/eval RNG seed; None -> scenario.seed
        "obs_hops": (int, type(None)),  # observation aggregation hops; None -> control_plane.vis_k
        "checkpoint_path": (str, type(None)),
        "checkpoint_sha256": (str, type(None)),
        "checkpoint_metadata_sha256": (str, type(None)),
        # Exact training continuation bundle.  This is distinct from an
        # eval-only model checkpoint: it binds replay, optimizer/target state,
        # counters and RNG state to the same training contract.
        "resume_path": (str, type(None)),
        "resume_sha256": (str, type(None)),
        "gamma": (int, float),
        "lr": (int, float),
        "batch_size": int,
        "replay_size": int,
        "target_update_interval": int,
        "reward": str,  # queue (corrected queue reward) — the ONLY v1 reward
        "reward_w1": (int, float),  # M1 queue reward weight (legacy w1=20)
        "reward_beta": (int, float),  # M1 decay rate s^-1 (legacy _M1_BETA=200)
        "forward_step_penalty": (int, float),  # non-positive hop cost; must dominate reward_w1
        "arrive_reward": (int, float),  # terminal delivery reward (legacy 50)
        "qlearning_alpha": (int, float),  # tabular Q update rate (legacy 0.25)
        "epsilon_start": (int, float),
        "epsilon_end": (int, float),
        "epsilon_decay_s": (int, float),
        # DDQN-only: tf.function-compiled train step vs eager fallback. It
        # lives in the resolved config (and therefore the config SHA) because
        # it selects between two training execution paths; no environment
        # variable may steer it.
        "fast_train": bool,
    },
    "execution": {
        "max_events": int,
        "max_entities": int,
        "max_packets": int,
        "monitor": bool,
        "dry_run": bool,
        # Physical-capacity measurement samples are diagnostic evidence; a
        # fixed interval makes the denominator explicit and reproducible.
        "available_capacity_interval_s": (int, float, type(None)),
        # T1-COMPUTE-DELAY: how long one routing decision takes to compute, in
        # SIMULATED seconds.  0 keeps the historical instantaneous decision
        # exactly (the decision body then runs synchronously with no yield).
        # Greater than 0 defers the commit, so the body revalidates against
        # the state that exists when the decision actually lands.
        "compute_delay_s": (int, float),
        # Which observation a delayed decision infers on: "refresh" re-reads
        # the state when the computation lands; "frozen" infers on the
        # observation captured at decision start and only re-checks legality
        # at commit time.  Only "frozen" can represent a state that went stale
        # during the computation.
        "decision_observation_mode": str,
        # F2 (node processing / scheduling cost): how long ONE satellite visit
        # occupies the node's receive/process/schedule element, in SIMULATED
        # seconds, before the arriving packet becomes available to the
        # forwarding function.  Deliberately NOT a transmit-time term: it never
        # scales pkt.bits / rate, so it cannot degenerate into the same
        # variable as the PHY bandwidth (F3, links.isl_rate_mbps).  0 keeps the
        # historical instantaneous arrival path exactly.
        "node_process_delay_s": (int, float),
    },
    "outputs": {
        "out_dir": str,
        "trace_path": (str, type(None)),
        "plotting": bool,
    },
}

VALID_DEMAND_MODES = {
    "uniform", "gravity", "population_gravity", "hotspot", "burst",
    "diurnal", "csv", "mlab",
}
VALID_ASSOCIATION = {"bbm", "mbb"}
VALID_UNAVAILABLE_POLICIES = {"reject", "queue"}
VALID_POLICIES = {
    "hop", "delay", "capacity", "oracle", "info_queue", "info_physical",
}
VALID_CONTRACTS = {"C1", "C3", "C4", "C5", "C6", "C7", "GAT", "MPNN"}
VALID_ALGORITHMS = {"none", "ddqn", "qlearning"}
VALID_ISL_DIRS = {"N", "S", "E", "W"}
VALID_RATE_MODELS = {"constant", "mcs"}
# T1-COMPUTE-DELAY observation semantics.  "refresh" is the historical
# behaviour (the deferred decision re-reads state when it lands, so staleness
# during the computation cannot exist); "frozen" infers on the observation
# taken at decision start and only checks legality at commit time.
VALID_OBSERVATION_MODES = {"refresh", "frozen"}
RF_KEYS = {
    "frequency_hz", "bandwidth_hz", "max_ptx_w",
    "antenna_diameter_tx_m", "antenna_diameter_rx_m",
    "pointing_loss_db", "noise_figure_db", "noise_temperature_k",
    "min_rate_bps",
}

DEFAULTS: dict[str, dict[str, Any]] = {
    "scenario": {
        "name": "default",
        "duration_s": 60.0,
        "time_step_s": 0.1,
        "num_satellites": 66,
        "num_planes": 6,
        "altitude_km": 550.0,
        "inclination_deg": 53.0,
        "min_elevation_deg": 25.0,
        "seed": 42,
        "geometry_epoch_s": 0.0,
    },
    "endpoints": {
        "grid_deg": 0.25,
        "aggregation_deg": 1.0,
        "sites": [],
        "mlab_auto": False,
        # Bound sparse ground-side entities while retaining many measured OD
        # alternatives. Automatic selection is an explicit opt-in below.
        "mlab_max_sites": 64,
    },
    "demand": {
        "mode": "uniform",
        "offered_mbps": 1.0,
        "emission_end_s": None,
        "packet_bits": 8_000_000,
        "deadline_s": None,
        "csv_path": None,
        "population_path": None,
        "source_population_exponent": 1.0,
        "destination_population_exponent": 1.0,
        "gravity_alpha": 1.5,
        "gravity_d_floor_km": 500.0,
        "hotspot_fraction": 0.1,
        "hotspot_concentration": 0.8,
        "burst_start_s": None,
        "burst_duration_s": None,
        "burst_multiplier": 2.0,
        "diurnal_amplitude": 0.5,
        "diurnal_phase_h": 12.0,
        "temporal_model": "constant",
        "utc_start_hour": 0.0,
        "population_destination_sampler": "scan",
        "destination_rejection_max_draws": 10_000,
        "nested_master_offered_mbps": None,
    },
    "access": {
        "unavailable_policy": "reject",
        "slots_per_satellite": 4,
        "uplink_rate_mbps": 100.0,
        "downlink_rate_mbps": 100.0,
        "uplink_queue_bits": 64_000_000,
        "downlink_queue_bits": 64_000_000,
        "drr_quantum_bits": 1_000_000,
        "association": "bbm",
        "hysteresis_deg": 1.0,
        "min_dwell_s": 1.0,
        "acquisition_delay_s": 0.1,
        "retiring_link_limit": 4,
        "retirement_deadline_s": 5.0,
        "dual_connect": False,
        "slot_lease_s": 10.0,
        "idle_release_s": 1.0,
        "holding_queue_bits": 64_000_000,
    },
    "links": {
        "rate_model": "constant",
        "mcs_table": link_budget.LEGACY_DVBS2X,
        # legacy ISL params, markovianMatchingTwo (SimulationRL.py:8353)
        "rf_isl": {
            "frequency_hz": 26e9, "bandwidth_hz": 500e6, "max_ptx_w": 10.0,
            "antenna_diameter_tx_m": 0.26, "antenna_diameter_rx_m": 0.26,
            "pointing_loss_db": 0.3, "noise_figure_db": 2.0,
            "noise_temperature_k": 290.0, "min_rate_bps": 10_000.0,
        },
        # legacy Gateway.gs2ngeo (SimulationRL.py:2617)
        "rf_uplink": {
            "frequency_hz": 30e9, "bandwidth_hz": 500e6, "max_ptx_w": 20.0,
            "antenna_diameter_tx_m": 0.33, "antenna_diameter_rx_m": 0.26,
            "pointing_loss_db": 0.3, "noise_figure_db": 2.0,
            "noise_temperature_k": 290.0, "min_rate_bps": 10_000.0,
        },
        # legacy Satellite.ngeo2gt globals (SimulationRL.py:297-310, :1935)
        "rf_downlink": {
            "frequency_hz": 20e9, "bandwidth_hz": 500e6, "max_ptx_w": 10.0,
            "antenna_diameter_tx_m": 0.26, "antenna_diameter_rx_m": 0.26,
            "pointing_loss_db": 0.3, "noise_figure_db": 2.0,
            "noise_temperature_k": 290.0, "min_rate_bps": 10_000.0,
        },
        "isl_rate_mbps": 1000.0,
        "isl_queue_bits": 256_000_000,
        "isl_dirs": ["N", "S", "E", "W"],
        "max_isl_km": 6000.0,
        "geometry_loss": True,
        "ge_enabled": False,
        # abstract defaults, NOT calibrated to any real constellation operator
        "ge_gsl": {"mean_good_s": 300.0, "mean_bad_s": 1.0},
        "ge_isl": {"mean_good_s": 900.0, "mean_bad_s": 0.5},
    },
    "topology": {
        "recompute_interval_s": None,  # None = static (V2 current behavior)
        "matching": "markovian",
    },
    "control_plane": {
        "enabled": True,
        "vis_k": 2,
        "ttl_s": 10.0,
        "advertise_interval_s": 1.0,
        "packet_bits": 8_000,
        "priority": "nonpreemptive_priority",
    },
    "routing": {"policy": "hop", "max_hops": 16, "learning_enabled": False,
                "contract": "C3"},
    "learning": {
        "algorithm": "none",
        "mode": "train",
        "seed": None,
        "obs_hops": None,
        "checkpoint_path": None,
        "checkpoint_sha256": None,
        "checkpoint_metadata_sha256": None,
        "resume_path": None,
        "resume_sha256": None,
        "gamma": 0.99,
        "lr": 0.001,
        "batch_size": 64,
        "replay_size": 50_000,
        "target_update_interval": 500,
        "reward": "queue",
        # corrected (M1) queue reward parameters, absorbed as the v1 baseline
        # (legacy w1/_M1_BETA defaults, SimulationRL.py:270/345) plus the
        # legacy ArriveReward (SimulationRL.py:579)
        "reward_w1": 20.0,
        "reward_beta": 200.0,
        # The raw legacy queue reward is diagnostic; the learning objective
        # adds this cost so an extra forwarding hop can never be profitable
        # before delivery.  Validation keeps the invariant for overrides.
        "forward_step_penalty": -20.0,
        "arrive_reward": 50.0,
        "qlearning_alpha": 0.25,
        "epsilon_start": 1.0,
        "epsilon_end": 0.05,
        "epsilon_decay_s": 300.0,
        "fast_train": True,
    },
    "execution": {
        "max_events": 1_000_000,
        "max_entities": 10_000,
        "max_packets": 200_000,
        "monitor": False,
        "dry_run": False,
        # Opt in for E0/diagnostic profiles; training/smoke runs that do not
        # report utilization should not pay the sampling and artifact cost.
        "available_capacity_interval_s": None,
        # Historical runs decide instantaneously; a non-zero delay is opt-in.
        "compute_delay_s": 0.0,
        # Historical runs re-read state at commit time; frozen is opt-in.
        "decision_observation_mode": "refresh",
        # Historical runs process an arriving packet instantaneously; a
        # non-zero node cost is opt-in.
        "node_process_delay_s": 0.0,
    },
    "outputs": {"out_dir": "leo_sim_out", "trace_path": None, "plotting": False},
}

PROFILES: dict[str, dict[str, Any]] = {
    "smoke": {
        "scenario": {"duration_s": 5.0, "num_satellites": 12, "num_planes": 3},
        "demand": {"offered_mbps": 0.5},
        "execution": {"max_events": 50_000, "max_packets": 5_000},
    },
}


def _check_group(group: str, values: Mapping[str, Any]) -> None:
    allowed = SCHEMA[group]
    for key, value in values.items():
        if key not in allowed:
            raise ConfigError(f"unknown field {group}.{key}")
        expected = allowed[key]
        allowed_types = expected if isinstance(expected, tuple) else (expected,)
        if isinstance(value, bool) and bool not in allowed_types:
            raise ConfigError(f"field {group}.{key} must be {expected}, got bool")
        if not isinstance(value, expected):
            raise ConfigError(f"field {group}.{key} must be {expected}, got {type(value).__name__}")


def _deep_merge(base: dict, override: Mapping) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _check_finite(node: Any, path: str = "") -> None:
    """Reject NaN / +/-Inf anywhere in the merged config (fail closed)."""
    if isinstance(node, bool):
        return
    if isinstance(node, float):
        if not math.isfinite(node):
            raise ConfigError(f"non-finite value at {path or '<root>'}: {node}")
        return
    if isinstance(node, Mapping):
        for k, v in node.items():
            _check_finite(v, f"{path}.{k}" if path else str(k))
        return
    if isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            _check_finite(v, f"{path}[{i}]")


def _validate_semantics(cfg: Mapping[str, Any]) -> None:
    sc, ep, dm, ac, lk, tp, cp, rt, lr, ex = (
        cfg["scenario"], cfg["endpoints"], cfg["demand"], cfg["access"],
        cfg["links"], cfg["topology"], cfg["control_plane"], cfg["routing"],
        cfg["learning"], cfg["execution"],
    )
    if sc["duration_s"] <= 0:
        raise ConfigError("scenario.duration_s must be > 0")
    if sc["time_step_s"] <= 0:
        raise ConfigError("scenario.time_step_s must be > 0")
    # LEO envelope for the abstract orbital model: 300-2000 km. 2000 km is
    # the conventional LEO upper bound; 300 km is the practical lower bound
    # below which a circular-orbit abstraction is not meaningful (drag
    # regime). Anything else is outside what this simulator claims to model.
    if not 300.0 <= sc["altitude_km"] <= 2000.0:
        raise ConfigError("scenario.altitude_km must be within the LEO range [300, 2000] km")
    # orbital inclination is an angle: [0, 180] degrees (0 equatorial,
    # 90 polar, >90 retrograde)
    if not 0.0 <= sc["inclination_deg"] <= 180.0:
        raise ConfigError("scenario.inclination_deg must be within [0, 180]")
    # NumPy SeedSequence entropy: a non-negative int
    if sc["seed"] < 0:
        raise ConfigError("scenario.seed must be a non-negative integer")
    if sc["num_satellites"] < 1 or sc["num_planes"] < 1:
        raise ConfigError("scenario requires >=1 satellite and plane")
    if sc["num_satellites"] % sc["num_planes"] != 0:
        raise ConfigError("scenario.num_satellites must be divisible by num_planes")
    if not 0 < sc["min_elevation_deg"] < 90:
        raise ConfigError("scenario.min_elevation_deg out of range")
    if ep["grid_deg"] <= 0 or ep["aggregation_deg"] <= 0:
        raise ConfigError("endpoints grid/aggregation degrees must be > 0")
    if ep["aggregation_deg"] < ep["grid_deg"]:
        raise ConfigError("endpoints.aggregation_deg must be >= grid_deg")
    if ep["mlab_max_sites"] < 2 or ep["mlab_max_sites"] > 256:
        raise ConfigError("endpoints.mlab_max_sites must be in [2, 256]")
    if ep["mlab_auto"] and dm["mode"] != "mlab":
        raise ConfigError("endpoints.mlab_auto is only valid with demand.mode=mlab")
    if ep["mlab_auto"] and ep["sites"]:
        raise ConfigError(
            "endpoints.mlab_auto requires endpoints.sites to be empty; "
            "choose automatic or explicit measurement endpoints")
    # grid IDs are only stable if cells tile the sphere evenly: both degrees
    # must divide 180/360 exactly, and aggregation must be an exact multiple
    # of the fine grid
    for deg, label in ((ep["grid_deg"], "grid_deg"), (ep["aggregation_deg"], "aggregation_deg")):
        for span in (180.0, 360.0):
            q = span / float(deg)
            if abs(q - round(q)) > 1e-9:
                raise ConfigError(
                    f"endpoints.{label}={deg} does not divide {span} evenly; "
                    "grid cell ids would not be stable")
    ratio = ep["aggregation_deg"] / ep["grid_deg"]
    if abs(ratio - round(ratio)) > 1e-9:
        raise ConfigError("endpoints.aggregation_deg must be an exact multiple of grid_deg")
    seen_names: set = set()
    for i, site in enumerate(ep["sites"]):
        if not isinstance(site, Mapping):
            raise ConfigError(f"endpoints.sites[{i}] must be a mapping")
        missing = {"name", "lat", "lon"} - set(site)
        if missing:
            raise ConfigError(f"endpoints.sites[{i}] missing {sorted(missing)}")
        extra = set(site) - {"name", "lat", "lon", "demand_weight"}
        if extra:
            raise ConfigError(f"endpoints.sites[{i}] unknown fields {sorted(extra)}")
        if not isinstance(site["name"], str) or not site["name"]:
            raise ConfigError(f"endpoints.sites[{i}].name must be a non-empty string")
        if site["name"] in seen_names:
            raise ConfigError(f"duplicate site name {site['name']!r}")
        seen_names.add(site["name"])
        for f in ("lat", "lon"):
            v = site[f]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise ConfigError(
                    f"endpoints.sites[{i}].{f} must be a real number, "
                    f"got {type(v).__name__}")
        if not -90 <= site["lat"] <= 90 or not -180 <= site["lon"] <= 180:
            raise ConfigError(f"endpoints.sites[{i}] lat/lon out of range")
        w = site.get("demand_weight", 1.0)
        if not isinstance(w, (int, float)) or isinstance(w, bool) \
                or not math.isfinite(w) or w <= 0:
            raise ConfigError(f"endpoints.sites[{i}].demand_weight must be a positive number")
    if dm["mode"] not in VALID_DEMAND_MODES:
        raise ConfigError(f"demand.mode must be one of {sorted(VALID_DEMAND_MODES)}")
    if dm["offered_mbps"] <= 0:
        raise ConfigError("demand.offered_mbps must be > 0")
    emission_end = dm["emission_end_s"]
    if emission_end is not None and not (0 < emission_end <= sc["duration_s"]):
        raise ConfigError(
            "demand.emission_end_s must be finite, > 0 and <= "
            "scenario.duration_s")
    if dm["packet_bits"] <= 0:
        raise ConfigError("demand.packet_bits must be > 0")
    if dm["mode"] == "csv" and not dm["csv_path"]:
        raise ConfigError("demand.mode=csv requires demand.csv_path")
    if dm["mode"] == "population_gravity" and not dm["population_path"]:
        raise ConfigError(
            "demand.mode=population_gravity requires demand.population_path")
    if dm["source_population_exponent"] <= 0 \
            or dm["destination_population_exponent"] <= 0:
        raise ConfigError("population exponents must be > 0")
    burst_declared = (
        dm["burst_start_s"] is not None
        or dm["burst_duration_s"] is not None
    )
    if burst_declared and dm["mode"] not in {"burst", "mlab"}:
        # The transform exists ONLY in _rate_multiplier() for mode in
        # {burst, mlab} (trace.py:302-307).  Declaring the window under any
        # other mode used to resolve happily and then be silently dropped:
        # the manifest published traffic_transform.burst = null while the
        # resolved config still carried burst_start_s, so nothing downstream
        # could separate "no burst intended" from "burst intended and lost".
        # Measured 2026-09-25: demand.mode=uniform + burst [2,4) resolved and
        # compiled with traffic_transform.burst = null and no diagnostic.
        raise ConfigError(
            "demand.burst_start_s/burst_duration_s are declared but "
            f"demand.mode={dm['mode']} never applies the burst transform; "
            "the window would be silently dropped. Use demand.mode=burst or "
            "demand.mode=mlab, or remove the burst window")
    # The multiplier alone is the other half of the same hole: a config that
    # sets burst_multiplier but declares no window under a mode that never
    # applies the transform used to resolve happily with the value kept in the
    # resolved config and never used (independent review of 2026-09-25).  The
    # default is exempt because it is present in every resolved config whether
    # or not the author asked for it; a non-default value is a claim that
    # something will use it.
    default_multiplier = DEFAULTS["demand"]["burst_multiplier"]
    if dm["mode"] not in {"burst", "mlab"} \
            and dm["burst_multiplier"] != default_multiplier:
        raise ConfigError(
            f"demand.burst_multiplier={dm['burst_multiplier']} is declared but "
            f"demand.mode={dm['mode']} never applies the burst transform, so "
            "the value would be silently ignored. Use demand.mode=burst or "
            f"demand.mode=mlab, or leave burst_multiplier at its default "
            f"({default_multiplier})")
    if dm["mode"] in {"burst", "mlab"} and (
            dm["mode"] == "burst" or burst_declared):
        if dm["burst_start_s"] is None or dm["burst_duration_s"] is None:
            raise ConfigError(
                f"demand.mode={dm['mode']} requires burst_start_s and "
                "burst_duration_s")
        if dm["burst_start_s"] < 0 or dm["burst_duration_s"] <= 0:
            raise ConfigError("burst window invalid")
        # The window must lie inside the interval in which packets are
        # actually emitted, not merely intersect the scenario horizon.  The
        # historical check used scenario.duration_s only, so a window placed
        # after demand.emission_end_s passed validation and then produced
        # ZERO packets inside it: the run was a plain baseline while the
        # manifest still declared a burst.  Measured 2026-09-25:
        # emission_end_s=20 with the window at [30, 40) gave 100 packets,
        # 0 inside the window, and realized offered load == target 40 Mbps.
        emitted_until = (float(sc["duration_s"])
                         if dm["emission_end_s"] is None
                         else float(dm["emission_end_s"]))
        window_end = dm["burst_start_s"] + dm["burst_duration_s"]
        if window_end > emitted_until:
            raise ConfigError(
                "burst window [burst_start_s, burst_start_s+burst_duration_s) "
                f"= [{dm['burst_start_s']}, {window_end}) must lie inside the "
                f"emission window [0, {emitted_until}] "
                "(demand.emission_end_s, or scenario.duration_s when unset); "
                "a window that extends past it is only partially applied")
    if dm["deadline_s"] is not None and dm["deadline_s"] <= 0:
        raise ConfigError("demand.deadline_s must be > 0 when set")
    if dm["gravity_alpha"] <= 0 or dm["gravity_d_floor_km"] <= 0:
        raise ConfigError("gravity parameters must be > 0")
    if not 0 < dm["hotspot_fraction"] <= 1 or not 0 <= dm["hotspot_concentration"] <= 1:
        raise ConfigError("hotspot parameters out of range")
    if dm["burst_multiplier"] <= 0:
        raise ConfigError("burst_multiplier must be > 0")
    if dm["diurnal_amplitude"] < 0 or not 0 <= dm["diurnal_phase_h"] < 24:
        raise ConfigError("diurnal parameters out of range")
    if sc["geometry_epoch_s"] < 0:
        raise ConfigError("scenario.geometry_epoch_s must be >= 0")
    if dm["temporal_model"] not in {"constant", "local_diurnal_cosine"}:
        raise ConfigError(
            "demand.temporal_model must be constant or local_diurnal_cosine")
    if dm["temporal_model"] == "local_diurnal_cosine" \
            and dm["mode"] != "population_gravity":
        raise ConfigError(
            "local_diurnal_cosine is only valid with population_gravity")
    if not 0 <= dm["utc_start_hour"] < 24:
        raise ConfigError("demand.utc_start_hour must be in [0, 24)")
    if dm["mode"] != "population_gravity" and dm["utc_start_hour"] != 0:
        raise ConfigError(
            "demand.utc_start_hour is only configurable for population_gravity")
    if dm["population_destination_sampler"] not in {"scan", "alias_rejection"}:
        raise ConfigError(
            "demand.population_destination_sampler must be scan or alias_rejection")
    if dm["population_destination_sampler"] != "scan" \
            and dm["mode"] != "population_gravity":
        raise ConfigError(
            "population destination sampler is only valid with population_gravity")
    if dm["destination_rejection_max_draws"] < 1:
        raise ConfigError("demand.destination_rejection_max_draws must be >= 1")
    master = dm["nested_master_offered_mbps"]
    if master is not None:
        if dm["mode"] != "population_gravity":
            raise ConfigError(
                "nested master load is only valid with population_gravity")
        if master < dm["offered_mbps"]:
            raise ConfigError(
                "demand.nested_master_offered_mbps must be >= demand.offered_mbps")
    if ac["association"] not in VALID_ASSOCIATION:
        raise ConfigError(f"access.association must be one of {sorted(VALID_ASSOCIATION)}")
    if ac["unavailable_policy"] not in VALID_UNAVAILABLE_POLICIES:
        raise ConfigError(
            "access.unavailable_policy must be one of "
            f"{sorted(VALID_UNAVAILABLE_POLICIES)}")
    if ac["slots_per_satellite"] < 1:
        raise ConfigError("access.slots_per_satellite must be >= 1")
    for f in ("uplink_rate_mbps", "downlink_rate_mbps"):
        if ac[f] <= 0:
            raise ConfigError(f"access.{f} must be > 0")
    for f in ("uplink_queue_bits", "downlink_queue_bits"):
        if ac[f] < 0:
            raise ConfigError(f"access.{f} must be >= 0")
    if ac["holding_queue_bits"] < 0:
        raise ConfigError("access.holding_queue_bits must be >= 0")
    if ac["drr_quantum_bits"] < 1:
        raise ConfigError("access.drr_quantum_bits must be >= 1")
    if ac["hysteresis_deg"] < 0 or ac["min_dwell_s"] < 0 or ac["acquisition_delay_s"] < 0:
        raise ConfigError("access hysteresis/dwell/acquisition must be >= 0")
    if ac["retirement_deadline_s"] <= 0 or ac["retiring_link_limit"] < 0:
        raise ConfigError("access retirement parameters invalid")
    if ac["slot_lease_s"] <= 0 or ac["idle_release_s"] <= 0:
        raise ConfigError("access slot_lease_s/idle_release_s must be > 0")
    if ac["association"] == "mbb":
        if not ac["dual_connect"]:
            raise ConfigError("access.association=mbb requires access.dual_connect=true")
        if ac["retiring_link_limit"] < 1:
            raise ConfigError("mbb requires retiring_link_limit >= 1")
    if not ac["dual_connect"] and ac["association"] == "mbb":
        raise ConfigError("mbb without dual_connect is invalid")
    if set(lk["isl_dirs"]) - VALID_ISL_DIRS or not lk["isl_dirs"]:
        raise ConfigError(f"links.isl_dirs must be a non-empty subset of {sorted(VALID_ISL_DIRS)}")
    if lk["isl_rate_mbps"] <= 0 or lk["max_isl_km"] <= 0:
        raise ConfigError("links.isl_rate_mbps and links.max_isl_km must be > 0")
    if lk["isl_queue_bits"] < 0:
        raise ConfigError("links.isl_queue_bits must be >= 0")
    for name in ("ge_gsl", "ge_isl"):
        ge = lk[name]
        if not isinstance(ge, Mapping) or set(ge) != {"mean_good_s", "mean_bad_s"}:
            raise ConfigError(f"links.{name} must have exactly mean_good_s and mean_bad_s")
        if any(isinstance(ge[k], bool)
               or not isinstance(ge[k], (int, float))
               or ge[k] <= 0
               for k in ("mean_good_s", "mean_bad_s")):
            raise ConfigError(f"links.{name} mean dwell times must be > 0")
    if tp["recompute_interval_s"] is not None and (
            isinstance(tp["recompute_interval_s"], bool)
            or not isinstance(tp["recompute_interval_s"], (int, float))
            or not math.isfinite(tp["recompute_interval_s"])
            or tp["recompute_interval_s"] <= 0):
        raise ConfigError(
            "topology.recompute_interval_s must be null or a positive number")
    if tp["matching"] != "markovian":
        raise ConfigError("topology.matching currently supports only 'markovian'")
    if lk["rate_model"] not in VALID_RATE_MODELS:
        raise ConfigError(
            f"links.rate_model must be one of {sorted(VALID_RATE_MODELS)}")
    for label in ("rf_isl", "rf_uplink", "rf_downlink"):
        rf = lk[label]
        if not isinstance(rf, Mapping) or set(rf) != RF_KEYS:
            raise ConfigError(
                f"links.{label} must have exactly "
                f"{sorted(RF_KEYS)}")
    if lk["rate_model"] == "mcs":
        if lk["mcs_table"] != link_budget.LEGACY_DVBS2X:
            raise ConfigError(
                f"links.mcs_table currently supports only "
                f"{link_budget.LEGACY_DVBS2X!r}")
        for label in ("rf_isl", "rf_uplink", "rf_downlink"):
            rf = lk[label]
            for key in RF_KEYS:
                v = rf[key]
                if isinstance(v, bool) or not isinstance(v, (int, float)) \
                        or not math.isfinite(v) or v <= 0:
                    raise ConfigError(
                        f"links.{label}.{key} must be a positive number")
        # The certified rate-recovery threshold assumes the first non-zero MCS
        # step already satisfies min_rate_bps; otherwise the effective
        # rate-up distance is not the SNR threshold and recovery would be
        # mis-scheduled.  Fail closed instead of silently approximating.
        for label in ("rf_isl", "rf_uplink", "rf_downlink"):
            rf = lk[label]
            step_rate = link_budget.LEGACY_DVBS2X_SPEFF[1] \
                * rf["bandwidth_hz"]
            if step_rate < rf["min_rate_bps"]:
                raise ConfigError(
                    f"links.rate_model=mcs requires {label}.min_rate_bps <= "
                    f"bandwidth_hz*min(MCS speff) (got {rf['min_rate_bps']} "
                    f"vs {step_rate})")
    if cp["vis_k"] < 0:
        raise ConfigError("control_plane.vis_k must be >= 0")
    if cp["ttl_s"] <= 0 or cp["advertise_interval_s"] <= 0 or cp["packet_bits"] < 1:
        raise ConfigError("control_plane ttl/advertise_interval/packet_bits invalid")
    if cp["priority"] != "nonpreemptive_priority":
        raise ConfigError("only nonpreemptive_priority control is supported")
    if rt["policy"] not in VALID_POLICIES:
        raise ConfigError(f"routing.policy must be one of {sorted(VALID_POLICIES)}")
    if rt["max_hops"] < 1:
        raise ConfigError("routing.max_hops must be >= 1")
    if rt["contract"] not in VALID_CONTRACTS:
        raise ConfigError(f"routing.contract must be one of {sorted(VALID_CONTRACTS)}")
    if rt["learning_enabled"] and lr["algorithm"] == "none":
        raise ConfigError("routing.learning_enabled requires learning.algorithm != none")
    if lr["algorithm"] != "none" and not rt["learning_enabled"]:
        raise ConfigError("learning.algorithm != none requires routing.learning_enabled=true")
    if lr["algorithm"] != "none" and rt["policy"] == "oracle":
        raise ConfigError("learning may not use oracle routing/global information")
    if lr["algorithm"] != "none" and not cp["enabled"]:
        raise ConfigError("learning requires the real control plane and arrived local cache")
    if lr["algorithm"] not in VALID_ALGORITHMS:
        raise ConfigError(f"learning.algorithm must be one of {sorted(VALID_ALGORITHMS)}")
    if lr["mode"] not in {"train", "eval"}:
        raise ConfigError("learning.mode must be 'train' or 'eval'")
    if lr["seed"] is not None and (
            not isinstance(lr["seed"], int) or isinstance(lr["seed"], bool)
            or lr["seed"] < 0):
        raise ConfigError("learning.seed must be null or a non-negative integer")
    if lr["obs_hops"] is not None and (
            not isinstance(lr["obs_hops"], int)
            or isinstance(lr["obs_hops"], bool)
            or lr["obs_hops"] < 0):
        raise ConfigError(
            "learning.obs_hops must be null or a non-negative integer")
    if lr["obs_hops"] is not None and lr["obs_hops"] > cp["vis_k"]:
        raise ConfigError(
            "learning.obs_hops must be <= control_plane.vis_k (observation "
            "aggregation cannot exceed the control propagation range)")
    if lr["checkpoint_path"] is not None and not lr["checkpoint_path"]:
        raise ConfigError("learning.checkpoint_path must be null or a non-empty path")
    if lr["checkpoint_sha256"] is not None and (
            not isinstance(lr["checkpoint_sha256"], str)
            or len(lr["checkpoint_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in lr["checkpoint_sha256"])):
        raise ConfigError("learning.checkpoint_sha256 must be null or lowercase SHA-256")
    if lr["checkpoint_metadata_sha256"] is not None and (
            not isinstance(lr["checkpoint_metadata_sha256"], str)
            or len(lr["checkpoint_metadata_sha256"]) != 64
            or any(c not in "0123456789abcdef"
                   for c in lr["checkpoint_metadata_sha256"])):
        raise ConfigError(
            "learning.checkpoint_metadata_sha256 must be null or lowercase SHA-256")
    if lr["resume_path"] is not None and not lr["resume_path"]:
        raise ConfigError("learning.resume_path must be null or a non-empty path")
    if lr["resume_sha256"] is not None and (
            not isinstance(lr["resume_sha256"], str)
            or len(lr["resume_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in lr["resume_sha256"])):
        raise ConfigError("learning.resume_sha256 must be null or lowercase SHA-256")
    if (lr["resume_path"] is None) != (lr["resume_sha256"] is None):
        raise ConfigError(
            "learning.resume_path and learning.resume_sha256 must be provided together")
    if lr["algorithm"] == "none" and lr["mode"] != "train":
        raise ConfigError("learning.mode=eval requires a learning algorithm")
    if lr["algorithm"] == "none" and lr["checkpoint_path"] is not None:
        raise ConfigError("learning.checkpoint_path requires a learning algorithm")
    if lr["algorithm"] == "none" and lr["checkpoint_sha256"] is not None:
        raise ConfigError("learning.checkpoint_sha256 requires a learning algorithm")
    if lr["algorithm"] == "none" and lr["checkpoint_metadata_sha256"] is not None:
        raise ConfigError(
            "learning.checkpoint_metadata_sha256 requires a learning algorithm")
    if lr["algorithm"] == "none" and lr["resume_path"] is not None:
        raise ConfigError("learning.resume_path requires a learning algorithm")
    if lr["algorithm"] == "none" and lr["resume_sha256"] is not None:
        raise ConfigError("learning.resume_sha256 requires a learning algorithm")
    if lr["algorithm"] != "none" and lr["mode"] == "eval" \
            and (lr["checkpoint_path"] is None or lr["checkpoint_sha256"] is None):
        raise ConfigError(
            "learning.mode=eval requires checkpoint_path and checkpoint_sha256")
    if lr["algorithm"] == "ddqn" and lr["mode"] == "eval" \
            and lr["checkpoint_metadata_sha256"] is None:
        # DDQN artifacts are opaque model files whose observation contract
        # lives only in the sibling metadata.json; that metadata must itself
        # be pinned, otherwise a C3/C4-same-width checkpoint can be relabeled
        # and silently loaded (learning provenance gate).
        raise ConfigError(
            "learning.mode=eval with algorithm=ddqn requires "
            "checkpoint_metadata_sha256 (sibling metadata trust anchor)")
    if lr["mode"] == "eval" and lr["resume_path"] is not None:
        raise ConfigError("learning.mode=eval cannot load a training resume bundle")
    if lr["mode"] == "train" and (lr["checkpoint_path"] is not None
                                    or lr["checkpoint_sha256"] is not None
                                    or lr["checkpoint_metadata_sha256"] is not None):
        raise ConfigError("learning.mode=train does not load a checkpoint")
    if lr["reward"] != "queue":
        # v1 keeps exactly one reward: the corrected queue reward (M1/M2
        # semantics absorbed). distance/linear rewards are plan-excluded dead
        # entry points and are rejected, not parked "for later".
        raise ConfigError("learning.reward must be 'queue' (corrected queue reward); "
                          "distance/linear rewards are excluded from v1")
    if lr["reward_w1"] <= 0:
        raise ConfigError("learning.reward_w1 must be > 0")
    if lr["reward_beta"] <= 0:
        raise ConfigError("learning.reward_beta must be > 0")
    if lr["forward_step_penalty"] > -lr["reward_w1"]:
        raise ConfigError(
            "learning.forward_step_penalty must be <= -reward_w1 so "
            "an extra forwarding hop cannot have positive reward")
    if lr["arrive_reward"] < 0:
        raise ConfigError("learning.arrive_reward must be >= 0")
    if not 0 < lr["qlearning_alpha"] <= 1:
        raise ConfigError("learning.qlearning_alpha must be in (0, 1]")
    if lr["lr"] <= 0:
        raise ConfigError("learning.lr must be > 0")
    if lr["batch_size"] < 1 or lr["replay_size"] < 1 or lr["target_update_interval"] < 1:
        raise ConfigError("learning batch_size/replay_size/target_update_interval must be >= 1")
    if lr["batch_size"] > lr["replay_size"]:
        raise ConfigError("learning.batch_size must be <= replay_size")
    if lr["epsilon_decay_s"] <= 0:
        raise ConfigError("learning.epsilon_decay_s must be > 0")
    if not 0 <= lr["gamma"] <= 1:
        raise ConfigError("learning.gamma out of range")
    if not 0 <= lr["epsilon_end"] <= lr["epsilon_start"] <= 1:
        raise ConfigError("learning epsilon range invalid")
    for f in ("max_events", "max_entities", "max_packets"):
        if ex[f] < 1:
            raise ConfigError(f"execution.{f} must be >= 1")
    interval = ex["available_capacity_interval_s"]
    if (interval is not None and (
            isinstance(interval, bool)
            or interval < 0.01
            or not math.isfinite(interval))):
        raise ConfigError(
            "execution.available_capacity_interval_s must be null or finite and >= 0.01")
    effective_interval = interval
    topo_interval = tp["recompute_interval_s"]
    if (effective_interval is not None and topo_interval is not None):
        effective_interval = min(effective_interval, topo_interval)
    if (effective_interval is not None
            and sc["duration_s"] / effective_interval > 100_000):
        raise ConfigError(
            "execution.available_capacity_interval_s creates more than "
            "100000 sampling intervals")
    compute_delay = ex["compute_delay_s"]
    if (isinstance(compute_delay, bool) or compute_delay < 0
            or not math.isfinite(compute_delay)):
        raise ConfigError(
            "execution.compute_delay_s must be finite and >= 0")
    if ex["decision_observation_mode"] not in VALID_OBSERVATION_MODES:
        raise ConfigError(
            "execution.decision_observation_mode must be one of "
            f"{sorted(VALID_OBSERVATION_MODES)}")
    if ex["decision_observation_mode"] == "frozen" and compute_delay <= 0:
        # Fail loud instead of silently ignoring the requested mode: with no
        # computation time there is no interval during which an observation
        # could go stale, so the two modes are the same run and the request is
        # a configuration error rather than a no-op.
        raise ConfigError(
            "execution.decision_observation_mode=frozen requires "
            "execution.compute_delay_s > 0")
    node_delay = ex["node_process_delay_s"]
    if (isinstance(node_delay, bool) or node_delay < 0
            or not math.isfinite(node_delay)):
        raise ConfigError(
            "execution.node_process_delay_s must be finite and >= 0")


# The five demand fields defaulted since identity/v2 (Task 1 global scene
# contract).  The frozen v2 builder removes exactly these after config
# resolution so an old v2 receipt can be reconstructed byte-for-byte.
V2_REMOVED_DEMAND_FIELDS = (
    "temporal_model",
    "utc_start_hour",
    "population_destination_sampler",
    "destination_rejection_max_draws",
    "nested_master_offered_mbps",
)


def _trace_identity_payload(resolved: dict, identity_version: str,
                            removed_demand_fields=()) -> dict:
    c = resolved["config"]
    demand = copy.deepcopy(c["demand"])
    emission_end = demand.pop("emission_end_s")
    if emission_end is None:
        emission_end = c["scenario"]["duration_s"]
    for field in removed_demand_fields:
        demand.pop(field, None)
    return {
        "identity_version": identity_version,
        "config_version": resolved["version"],
        "scenario": {"emission_end_s": emission_end,
                     "seed": c["scenario"]["seed"]},
        "endpoints": c["endpoints"],
        "demand": demand,
        "execution": {"max_packets": c["execution"]["max_packets"]},
    }


def trace_identity_payload_v2(resolved: dict) -> dict:
    """Frozen identity/v2 builder.

    Removes the five demand fields defaulted by the Task 1 global scene
    contract after config resolution, so a v2 receipt compiled before those
    fields existed still reconstructs byte-for-byte."""
    return _trace_identity_payload(
        resolved, TRACE_IDENTITY_VERSION_V2, V2_REMOVED_DEMAND_FIELDS)


def trace_identity_payload(resolved: dict) -> dict:
    """The v3 trace identity payload for new compilations.

    The exact config scope that determines trace bytes / compile bounds.
    Two mechanism arms (routing policy, access, links, control plane,
    learning, outputs, operational execution limits) MUST consume the same
    immutable trace, so none of them appear here. Included:
      - trace identity/schema versions;
      - effective demand.emission_end_s and scenario.seed (demand RNG);
      - the full endpoints group (grid, aggregation, sites);
      - the full demand group (generator parameters, csv/mlab inputs by path);
      - execution.max_packets (the compile-time bound).
    `scenario.geometry_epoch_s` is deliberately absent: it changes the
    geometry phase, never the trace bytes.
    """
    return _trace_identity_payload(resolved, TRACE_IDENTITY_VERSION)


def legacy_trace_identity_payload(resolved: dict) -> dict:
    """Rebuild the closed v1 trace identity contract for old manifests."""
    c = resolved["config"]
    demand = copy.deepcopy(c["demand"])
    demand.pop("emission_end_s", None)
    return {
        "identity_version": TRACE_IDENTITY_VERSION_V1,
        "config_version": resolved["version"],
        "scenario": {"duration_s": c["scenario"]["duration_s"],
                      "seed": c["scenario"]["seed"]},
        "endpoints": c["endpoints"],
        "demand": demand,
        "execution": {"max_packets": c["execution"]["max_packets"]},
    }


def trace_identity_sha256_v2(resolved: dict, input_sha256: str = "") -> str:
    """SHA256 of the frozen identity/v2 payload plus the demand input content
    hash.  Used to verify persisted v2 contracts; produces the exact legacy
    v2 hash for configs resolved under the Task 1 contract."""
    payload = trace_identity_payload_v2(resolved)
    payload["input_sha256"] = input_sha256
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def trace_identity_sha256(resolved: dict, input_sha256: str = "") -> str:
    """SHA256 of the trace identity payload plus the demand input content
    hash (csv/mlab file bytes; empty for synthetic modes)."""
    payload = trace_identity_payload(resolved)
    payload["input_sha256"] = input_sha256
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def legacy_trace_identity_sha256(resolved: dict, input_sha256: str = "") -> str:
    payload = legacy_trace_identity_payload(resolved)
    payload["input_sha256"] = input_sha256
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def demand_sha256(resolved: dict) -> str:
    """DEPRECATED alias for trace_identity_sha256(resolved) without an input
    hash. Kept only so frozen external probe scripts referencing the old
    ambiguous name still execute; all new code must use
    trace_identity_sha256, and receipts/manifests bind the new name."""
    return trace_identity_sha256(resolved)


def resolve_config(
    user: Mapping[str, Any] | None = None,
    profile: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve defaults -> profile -> user -> overrides into one canonical config.

    Returns a dict with keys: version, config (canonical dict), canonical_json, sha256.
    """
    user = user or {}
    overrides = overrides or {}
    for src, label in ((user, "config"), (overrides, "overrides")):
        unknown_top = set(src) - set(SCHEMA)
        if unknown_top:
            raise ConfigError(f"{label}: unknown top-level groups {sorted(unknown_top)}")
    merged = copy.deepcopy(DEFAULTS)
    if profile is not None:
        if profile not in PROFILES:
            raise ConfigError(f"unknown profile {profile!r}; available: {sorted(PROFILES)}")
        merged = _deep_merge(merged, PROFILES[profile])
    merged = _deep_merge(merged, user)
    merged = _deep_merge(merged, overrides)
    for group, values in merged.items():
        _check_group(group, values)
    _check_finite(merged)
    _validate_semantics(merged)
    canonical_json = json.dumps(merged, sort_keys=True, separators=(",", ":"))
    sha = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return {
        "version": CONFIG_SCHEMA_VERSION,
        "config": merged,
        "canonical_json": canonical_json,
        "sha256": sha,
    }


def load_config_file(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.load(fh, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError(f"{path}: top level must be a mapping")
    raw = dict(raw)
    profile = raw.pop("profile", None)
    version = raw.pop("config_version", CONFIG_SCHEMA_VERSION)
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(f"unsupported config_version {version!r}")
    return resolve_config(raw, profile=profile)
