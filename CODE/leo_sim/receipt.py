"""Run receipts for leo_sim: build, write, and fail-closed verification.

TRUST MODEL (honest): local verification proves INTERNAL CONSISTENCY between
the on-disk artifacts (resolved config, trace.csv, manifest.json,
ledgers.json, receipt.json) and nothing more. There is no external anchor
here — anyone who can rewrite ledgers.json AND rebind receipt.ledgers_sha256
can fabricate a consistent-looking run. Formal tamper-evidence requires the
governance chain: a clean committed code identity, an authorization
manifest, and an external artifact-hash anchor. That remains a VM/governance
gate and is NOT claimed solved here.

Field authority classes (recorded in ledgers.json field_authority):
- recomputed: verifier rebuilds from trace.csv/resolved_config.json (packet
  id/bit sets, fate counts, totals, conservation, requested mechanisms,
  config bindings, stop==horizon, deliveries-vs-fates/time relations);
- ledger_consistency: bound by ledgers SHA and checked for schema and
  internal relations, but not independently recomputable (mechanism/control
  counters);
- diagnostic: reported, schema-checked, never usable as research-eligibility
  or scientific-metric evidence (occupied, queue areas, events_processed,
  access stats, handover events, raw packet/service events).  The derived
  congestion metrics are recomputed from those raw events before acceptance.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
from pathlib import Path

from . import (config as config_mod, fates, metrics, rng as rng_mod,
               trace as trace_mod)

LEGACY_RECEIPT_SCHEMA = "leo-sim-receipt/v3"
LEGACY_RECEIPT_SCHEMA_V4 = "leo-sim-receipt/v4"
RECEIPT_SCHEMA = "leo-sim-receipt/v5"
METRICS_V1_SCHEMA = "leo-sim-congestion-metrics/v1"
METRICS_V2_SCHEMA = "leo-sim-congestion-metrics/v2"

# exact top-level receipt key set (unknown or missing keys fail verification)
RECEIPT_KEYS_V3 = {
    "schema", "config_sha256", "config_version", "trace_manifest_sha256",
    "trace_sha256", "trace_identity_sha256", "code_sha256", "ledgers_sha256",
    "deps", "seed", "horizon_s", "natural_end", "interrupted", "error",
    "events_processed", "mechanisms", "research_eligible", "routing_label",
    "totals", "fate_counts", "packet_fates", "control", "occupied",
    "handover_event_count", "conservation_ok",
}
RECEIPT_KEYS_V4 = RECEIPT_KEYS_V3 | {"congestion_metrics_contract"}
RECEIPT_KEYS_V5 = RECEIPT_KEYS_V4 | {
    "trace_manifest_contract", "trace_identity_contract",
}
RECEIPT_KEYS = RECEIPT_KEYS_V5
DEP_KEYS = {"python", "simpy", "numpy", "pyyaml"}
# DDQN runs additionally pin the TensorFlow build: the training path depends
# on it, so its version is part of the run identity (and its absence on the
# verifying host fails closed).
TF_DEP_KEY = "tensorflow"
REQUESTED_KEYS = {"policy", "association", "ge_enabled", "control_enabled", "monitor",
                  "learning_algorithm", "learning_mode",
                  "topology_recompute_interval_s", "topology_matching"}
REQUESTED_KEYS |= {"rate_model"}
REQUESTED_KEYS |= {"forward_step_penalty"}
EFFECTIVE_KEYS = {"control_plane", "mcs", "ge", "mbb", "learning",
                   "dynamic_topology"}

LEDGER_KEYS = {
    "packet_fates", "control_instances", "control_counters",
    "mechanism_counters", "occupied", "queue_area_bits_s", "handover_events",
    "access", "events_processed", "stop_time_s", "deliveries",
    "field_authority", "learning", "packet_events", "link_service_windows",
    "link_available_windows",
    "congestion_metrics",
}
CONTROL_COUNTER_KEYS = {
    "snapshots_created", "registered", "entered_queue", "transmission_started",
    "transmission_completed", "arrived", "expired", "lost", "geometry_lost",
    "overflow", "duplicate", "in_system",
}
MECHANISM_COUNTER_KEYS = {
    "ge_gsl_queries", "ge_isl_queries", "ge_waits", "ge_failures",
    "control_snapshots", "control_registered", "control_entered_queue",
    "control_tx_started", "control_tx_completed", "control_initialized",
    "ge_initialized", "mbb_events", "learning_initialized",
    "learning_decisions", "learning_transitions", "learning_train_steps",
    "learning_discarded_at_stop", "learning_discarded_at_rematch",
    "holding_queue_overflows", "topo_recomputes", "topo_dynamic_init",
    "mcs_rate_samples",
    "mcs_zero_rate_holds", "mcs_rate_min_bps", "mcs_rate_max_bps",
}
MECHANISM_COUNTER_BOOLS = {"control_initialized", "ge_initialized",
                           "learning_initialized", "topo_dynamic_init"}
OCCUPIED_KEYS = {"gsl_uplink_s", "gsl_downlink_s", "isl_s", "ctrl_isl_s"}
QUEUE_AREA_KEYS = {"uplink", "downlink", "holding", "isl_data", "isl_ctrl"}
ACCESS_KEYS = {
    "requests", "grants", "preposition_grants", "wait_time_s_total",
    "wait_time_s_max", "slot_hold_s_total", "waiting_at_stop", "releases",
}
ACCESS_INT_KEYS = {"requests", "grants", "preposition_grants", "waiting_at_stop"}
HANDOVER_TYPES = {"associate", "release", "bbm", "mbb", "lease_retire"}

FIELD_AUTHORITY = {
    "packet_fates": "recomputed",
    "control_instances": "ledger_consistency",
    "control_counters": "ledger_consistency",
    "mechanism_counters": "ledger_consistency",
    "occupied": "diagnostic",
    "queue_area_bits_s": "diagnostic",
    "handover_events": "diagnostic",
    "access": "diagnostic",
    "events_processed": "diagnostic",
    "stop_time_s": "recomputed",
    "deliveries": "recomputed",
    "packet_events": "diagnostic",
    "link_service_windows": "diagnostic",
    "link_available_windows": "diagnostic",
    "congestion_metrics": "recomputed",
    "learning": "ledger_consistency",
}


def code_sha256() -> str:
    """SHA256 over the leo_sim package sources (sorted, path-bound)."""
    pkg = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(pkg.glob("*.py")):
        h.update(p.name.encode())
        h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def dependency_versions(with_tensorflow: bool = False) -> dict:
    import numpy
    import simpy
    import yaml
    deps = {
        "python": platform.python_version(),
        "simpy": simpy.__version__,
        "numpy": numpy.__version__,
        "pyyaml": yaml.__version__,
    }
    if with_tensorflow:
        # a DDQN run pins the TF build; on a TF-less host this ImportError
        # is the intended fail-closed behavior
        import tensorflow
        deps[TF_DEP_KEY] = tensorflow.__version__
    return deps


def requested_from_config(cfg: dict) -> dict:
    """Requested mechanisms, rebuilt from the resolved config alone."""
    return {
        "policy": cfg["routing"]["policy"],
        "association": cfg["access"]["association"],
        "rate_model": cfg["links"]["rate_model"],
        "ge_enabled": bool(cfg["links"]["ge_enabled"]),
        "control_enabled": bool(cfg["control_plane"]["enabled"]),
        "monitor": bool(cfg["execution"]["monitor"]),
        "learning_algorithm": cfg["learning"]["algorithm"],
        "learning_mode": cfg["learning"]["mode"],
        # Bind the safe learning objective to the receipt; the resolved config
        # hash alone is not enough for human-readable mechanism audits.
        "forward_step_penalty": cfg["learning"]["forward_step_penalty"],
        "topology_recompute_interval_s": cfg["topology"]["recompute_interval_s"],
        "topology_matching": cfg["topology"]["matching"],
    }


def effective_from_counters(counters: dict, requested: dict) -> dict:
    """Effective flags, recomputed from raw mechanism counters. A mechanism
    is effective only if it really entered the send path."""
    return {
        "control_plane": counters.get("control_entered_queue", 0) > 0,
        "mcs": (counters.get("mcs_rate_samples", 0) > 0
                or counters.get("mcs_zero_rate_holds", 0) > 0),
        "ge": requested.get("ge_enabled", False) and (
            counters.get("ge_gsl_queries", 0)
            + counters.get("ge_isl_queries", 0) > 0),
        "mbb": counters.get("mbb_events", 0) > 0,
        "dynamic_topology": (
            counters.get("topo_recomputes", 0) > 0
            or counters.get("topo_dynamic_init", False)),
        # Evaluation legitimately performs no gradient updates; a learning
        # policy is effective when the real model made at least one routed
        # decision, regardless of train/eval mode.
        "learning": (
            counters.get("learning_train_steps", 0) > 0
            if requested.get("learning_mode") == "train"
            else counters.get("learning_decisions", 0) > 0
        ),
    }


def expected_research_eligible(requested: dict, effective: dict,
                               natural_end: bool, interrupted: bool) -> bool:
    """Local artifacts never confer formal research eligibility.

    The arguments remain part of the compatibility surface because they are
    useful mechanism diagnostics.  Formal eligibility belongs to the external
    governance receipt that binds review, authorization and deployed commit.
    """
    return False


def _validate_manifest(manifest: dict, resolved_cfg: dict | None,
                       resolved_version: str | None) -> list[str]:
    """Validate every trace-manifest field derivable without trusting it."""
    errors: list[str] = []
    base_keys = {
        "schema", "trace_schema", "trace_sha256", "trace_identity_sha256",
        "config_version", "input_sha256", "mode", "provenance",
        "rng_streams", "packet_id_contract", "offered_packets",
        "offered_bits", "ledger", "active_endpoints", "time_range_s",
        "provenance_contract",
    }
    manifest_schema = manifest.get("schema")
    is_v1 = manifest_schema == trace_mod.TRACE_MANIFEST_SCHEMA_V1
    is_v2 = manifest_schema == trace_mod.TRACE_MANIFEST_SCHEMA
    if is_v2:
        base_keys |= {"simulation_horizon_s", "emission_end_s", "drain_s"}
    proxy_keys = {"not_calibrated_user_demand", "provenance_note"}
    population_keys = proxy_keys | {"population"}
    if manifest.get("mode") == "mlab":
        expected_keys = base_keys | proxy_keys
    elif manifest.get("mode") == "population_gravity":
        expected_keys = base_keys | population_keys
    else:
        expected_keys = base_keys
    if set(manifest) != expected_keys:
        errors.append(
            f"manifest keys mismatch: unknown={sorted(set(manifest) - expected_keys)} "
            f"missing={sorted(expected_keys - set(manifest))}")
    if not (is_v1 or is_v2):
        errors.append("manifest schema mismatch")
    if manifest.get("trace_schema") != trace_mod.TRACE_SCHEMA:
        errors.append("manifest trace schema mismatch")
    if manifest.get("packet_id_contract") != trace_mod.PACKET_ID_CONTRACT:
        errors.append("manifest packet_id_contract mismatch")
    provenance_contract = manifest.get("provenance_contract")
    provenance_schema = (trace_mod.TRACE_PROVENANCE_SCHEMA
                         if is_v2 else trace_mod.TRACE_PROVENANCE_SCHEMA_V1)
    provenance_keys = {
        "schema", "source", "units", "od_mapping", "offered_load",
        "traffic_transform", "measurement_summary"}
    if is_v2:
        provenance_keys |= {"simulation_horizon_s", "emission_end_s", "drain_s"}
    if not isinstance(provenance_contract, dict) or set(provenance_contract) != provenance_keys:
        errors.append("manifest provenance_contract keys mismatch")
    else:
        if provenance_contract.get("schema") != provenance_schema:
            errors.append("manifest provenance_contract schema mismatch")
        source = provenance_contract.get("source")
        if not isinstance(source, dict) or set(source) != {"type", "path", "sha256"}:
            errors.append("manifest provenance source keys mismatch")
        units = provenance_contract.get("units")
        if not isinstance(units, dict) or set(units) != {
                "emit_time", "deadline", "coordinates", "bits"}:
            errors.append("manifest provenance units keys mismatch")
        od_mapping = provenance_contract.get("od_mapping")
        if not isinstance(od_mapping, dict) or set(od_mapping) != {
                "input_coordinate_fields", "output_fields", "grid_deg",
                "aggregation_deg", "rule"}:
            errors.append("manifest provenance OD mapping keys mismatch")
        offered_load = provenance_contract.get("offered_load")
        if not isinstance(offered_load, dict) or set(offered_load) != {
                "load_mode", "target_offered_mbps", "realized_offered_mbps",
                "horizon_s", "packet_bits", "offered_packets", "offered_bits"}:
            errors.append("manifest provenance offered_load keys mismatch")
        transform = provenance_contract.get("traffic_transform")
        if not isinstance(transform, dict) or set(transform) != {
                "mode", "burst", "diurnal"}:
            errors.append("manifest provenance traffic_transform keys mismatch")
        summary = provenance_contract.get("measurement_summary")
        if summary is not None and (
                not isinstance(summary, dict)
                or not {"row_count", "od_pair_count", "hour_utc_values"}
                <= set(summary)
                or set(summary) - {
                    "row_count", "od_pair_count", "hour_utc_values",
                    "endpoint_selection",
                }):
            errors.append("manifest measurement_summary keys mismatch")
    if resolved_version is not None and manifest.get("config_version") != resolved_version:
        errors.append("manifest config_version mismatch")
    if resolved_cfg is None:
        return errors
    if is_v2:
        simulation_horizon = manifest.get("simulation_horizon_s")
        emission_end = manifest.get("emission_end_s")
        drain = manifest.get("drain_s")
        expected_horizon = float(resolved_cfg["scenario"]["duration_s"])
        expected_emission = resolved_cfg["demand"]["emission_end_s"]
        expected_emission = (expected_horizon if expected_emission is None
                             else float(expected_emission))
        if simulation_horizon != expected_horizon:
            errors.append("manifest simulation_horizon_s mismatch")
        if emission_end != expected_emission:
            errors.append("manifest emission_end_s mismatch")
        if drain != expected_horizon - expected_emission:
            errors.append("manifest drain_s mismatch")
    mode = resolved_cfg["demand"]["mode"]
    if manifest.get("mode") != mode:
        errors.append("manifest mode != resolved config demand mode")
    expected_provenance = {
        "mlab": "measurement_proxy",
        "population_gravity": "population_proxy",
    }.get(mode, "synthetic")
    if manifest.get("provenance") != expected_provenance:
        errors.append("manifest provenance mismatch")
    # rng_streams: reconstruct the exact config feature branch.  Legacy and
    # non-nested traces keep only the canonical demand mapping; nested
    # traces select the canonical demand and nested-filter entries from the
    # full mapping.  Nothing is guessed from the manifest itself.
    expected_rng = rng_mod.stream_mapping(
        resolved_cfg["scenario"]["seed"], ["demand"])
    if (resolved_cfg["demand"].get("mode") == "population_gravity"
            and resolved_cfg["demand"].get("nested_master_offered_mbps")
            is not None):
        full_rng = rng_mod.stream_mapping(
            resolved_cfg["scenario"]["seed"], rng_mod.STREAM_NAMES)
        expected_rng = {"demand": full_rng["demand"],
                        "nested_filter": full_rng["nested_filter"]}
    if manifest.get("rng_streams") != expected_rng:
        errors.append("manifest RNG stream mapping mismatch")
    input_sha = manifest.get("input_sha256")
    if mode in {"csv", "mlab", "population_gravity"}:
        if not isinstance(input_sha, str) or len(input_sha) != 64 \
                or any(c not in "0123456789abcdef" for c in input_sha):
            errors.append("manifest input_sha256 must be a lowercase SHA256")
    elif input_sha != "":
        errors.append("synthetic manifest input_sha256 must be empty")
    if mode == "mlab":
        if manifest.get("not_calibrated_user_demand") is not True:
            errors.append("M-Lab manifest must say not_calibrated_user_demand")
        if manifest.get("provenance_note") != (
                "M-Lab measurements reused as OD weight proxy only; "
                "this is measurement_proxy traffic, never calibrated user demand."):
            errors.append("M-Lab manifest provenance note mismatch")
    if mode == "population_gravity":
        if manifest.get("not_calibrated_user_demand") is not True:
            errors.append("population manifest must say not_calibrated_user_demand")
        if manifest.get("provenance_note") != (
                "GPW population counts drive source intensity and gravity "
                "destination probabilities; this is population_proxy demand, "
                "never calibrated Internet traffic."):
            errors.append("population manifest provenance note mismatch")
        pop = manifest.get("population")
        expected_pop_keys = {
            "source_path", "source_sha256", "source_shape",
            "source_resolution_deg", "aggregation_deg", "total_population",
            "candidate_regions", "source_population_exponent",
            "destination_population_exponent", "distance_exponent",
            "distance_floor_km",
        }
        if not isinstance(pop, dict) or set(pop) != expected_pop_keys:
            errors.append("population manifest metadata keys mismatch")
        else:
            if pop.get("source_sha256") != input_sha:
                errors.append("population source SHA != manifest input SHA")
            expected_values = {
                "aggregation_deg": resolved_cfg["endpoints"]["aggregation_deg"],
                "source_population_exponent": resolved_cfg["demand"][
                    "source_population_exponent"],
                "destination_population_exponent": resolved_cfg["demand"][
                    "destination_population_exponent"],
                "distance_exponent": resolved_cfg["demand"]["gravity_alpha"],
                "distance_floor_km": resolved_cfg["demand"][
                    "gravity_d_floor_km"],
            }
            for key, expected in expected_values.items():
                if pop.get(key) != expected:
                    errors.append(f"population manifest {key} mismatch")
            if not _is_nonneg_num(pop.get("total_population")) \
                    or pop.get("total_population", 0) <= 0:
                errors.append("population total_population must be positive")
            if not _is_nonneg_int(pop.get("candidate_regions")) \
                    or pop.get("candidate_regions", 0) < 2:
                errors.append("population candidate_regions must be >= 2")
    if isinstance(provenance_contract, dict):
        source = provenance_contract.get("source", {})
        units = provenance_contract.get("units", {})
        od_mapping = provenance_contract.get("od_mapping", {})
        offered_load = provenance_contract.get("offered_load", {})
        transform = provenance_contract.get("traffic_transform", {})
        measurement_summary = provenance_contract.get("measurement_summary")
        if source.get("sha256") != manifest.get("input_sha256"):
            errors.append("provenance source SHA != manifest input SHA")
        expected_source_type = {
            "csv": "csv_input", "mlab": "mlab_snapshot",
            "population_gravity": "population_raster",
        }.get(mode, "synthetic_generator")
        if source.get("type") != expected_source_type:
            errors.append("provenance source type mismatch")
        if units != {
                "emit_time": "seconds_since_run_start",
                "deadline": "seconds_since_run_start_or_empty",
                "coordinates": "degrees_wgs84", "bits": "bits"}:
            errors.append("provenance units mismatch")
        if od_mapping.get("grid_deg") != resolved_cfg["endpoints"]["grid_deg"] \
                or od_mapping.get("aggregation_deg") != resolved_cfg["endpoints"]["aggregation_deg"]:
            errors.append("provenance OD grid mapping mismatch")
        if offered_load.get("horizon_s") != resolved_cfg["scenario"]["duration_s"]:
            errors.append("provenance offered_load horizon mismatch")
        if is_v2:
            expected_emission_for_rate = resolved_cfg["demand"]["emission_end_s"]
            expected_emission_for_rate = (
                resolved_cfg["scenario"]["duration_s"]
                if expected_emission_for_rate is None
                else expected_emission_for_rate)
            expected_realized = (float(manifest.get("offered_bits", 0))
                                 / float(expected_emission_for_rate)
                                 / 1_000_000.0)
            if offered_load.get("realized_offered_mbps") != expected_realized:
                errors.append("provenance realized_offered_mbps mismatch")
        if is_v2:
            if provenance_contract.get("simulation_horizon_s") != resolved_cfg["scenario"]["duration_s"]:
                errors.append("provenance simulation_horizon_s mismatch")
            expected_emission = resolved_cfg["demand"]["emission_end_s"]
            expected_emission = (resolved_cfg["scenario"]["duration_s"]
                                 if expected_emission is None else expected_emission)
            if provenance_contract.get("emission_end_s") != expected_emission:
                errors.append("provenance emission_end_s mismatch")
            if provenance_contract.get("drain_s") != (
                    resolved_cfg["scenario"]["duration_s"] - expected_emission):
                errors.append("provenance drain_s mismatch")
        if offered_load.get("packet_bits") != resolved_cfg["demand"]["packet_bits"]:
            errors.append("provenance offered_load packet size mismatch")
        if offered_load.get("offered_packets") != manifest.get("offered_packets") \
                or offered_load.get("offered_bits") != manifest.get("offered_bits"):
            errors.append("provenance offered_load ledger mismatch")
        if transform.get("mode") != mode:
            errors.append("provenance traffic transform mode mismatch")
        if mode == "mlab":
            if not isinstance(measurement_summary, dict):
                errors.append("M-Lab manifest measurement_summary missing")
            else:
                if not _is_nonneg_int(measurement_summary.get("row_count")) \
                        or measurement_summary.get("row_count", 0) <= 0:
                    errors.append("M-Lab measurement row_count must be positive")
                if not _is_nonneg_int(measurement_summary.get("od_pair_count")) \
                        or measurement_summary.get("od_pair_count", 0) <= 0:
                    errors.append("M-Lab measurement od_pair_count must be positive")
                hours = measurement_summary.get("hour_utc_values")
                if not (isinstance(hours, list) and bool(hours)
                        and all(_is_nonneg_int(h) and h <= 23 for h in hours)
                        and hours == sorted(hours)
                        and len(hours) == len(set(hours))):
                    errors.append("M-Lab measurement hour_utc_values invalid")
                selection = measurement_summary.get("endpoint_selection")
                auto = resolved_cfg["endpoints"].get("mlab_auto", False)
                if auto:
                    expected_selection_keys = {
                        "method", "candidate_aggregate_cells",
                        "candidate_scc_count", "selected_aggregate_cells",
                        "selected_aggregate_ids", "max_sites", "source_weighting",
                    }
                    if not isinstance(selection, dict) \
                            or set(selection) != expected_selection_keys:
                        errors.append("M-Lab endpoint_selection metadata keys mismatch")
                    else:
                        ids = selection.get("selected_aggregate_ids")
                        selected_count = selection.get("selected_aggregate_cells")
                        max_sites = selection.get("max_sites")
                        if selection.get("method") != \
                                "largest_strongly_connected_component":
                            errors.append("M-Lab endpoint_selection method mismatch")
                        if max_sites != \
                                resolved_cfg["endpoints"]["mlab_max_sites"]:
                            errors.append("M-Lab endpoint_selection cap mismatch")
                        if selection.get("source_weighting") != \
                                "measured_outgoing_throughput":
                            errors.append("M-Lab endpoint_selection weighting mismatch")
                        ids_valid = (
                            isinstance(ids, list)
                            and all(isinstance(value, str) and value for value in ids)
                            and ids == sorted(set(ids))
                        ) if isinstance(ids, list) else False
                        if not (ids_valid and _is_nonneg_int(selected_count)
                                and _is_nonneg_int(max_sites)
                                and len(ids) == selected_count
                                and 2 <= len(ids) <= max_sites):
                            errors.append("M-Lab endpoint_selection IDs invalid")
                elif selection is not None:
                    errors.append(
                        "explicit M-Lab endpoints must not contain endpoint_selection")
        elif measurement_summary is not None:
            errors.append("non-M-Lab manifest must not contain measurement_summary")
        expected_burst = None
        if mode in {"burst", "mlab"} \
                and resolved_cfg["demand"]["burst_start_s"] is not None:
            expected_burst = {
                "start_s": float(resolved_cfg["demand"]["burst_start_s"]),
                "duration_s": float(resolved_cfg["demand"]["burst_duration_s"]),
                "multiplier": float(resolved_cfg["demand"]["burst_multiplier"]),
            }
        if transform.get("burst") != expected_burst:
            errors.append("provenance burst transform mismatch")
        expected_diurnal = None
        if mode == "diurnal":
            expected_diurnal = {
                "amplitude": float(resolved_cfg["demand"]["diurnal_amplitude"]),
                "phase_h": float(resolved_cfg["demand"]["diurnal_phase_h"]),
            }
        elif (mode == "population_gravity"
              and resolved_cfg["demand"]["temporal_model"]
              == "local_diurnal_cosine"):
            # the opt-in local-solar-time population proxy carries an exact
            # four-key value; anything else is a tampered transform
            expected_diurnal = {
                "amplitude": float(resolved_cfg["demand"]["diurnal_amplitude"]),
                "phase_h": float(resolved_cfg["demand"]["diurnal_phase_h"]),
                "utc_start_hour": float(
                    resolved_cfg["demand"]["utc_start_hour"]),
                "clock": "source_local_solar_time_proxy",
            }
        if transform.get("diurnal") != expected_diurnal:
            errors.append("provenance diurnal transform mismatch")
    return errors


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_ledgers(result: dict, rows: list[dict]) -> dict:
    """The structured, bounded run-ledger artifact.

    NOTE: this is the run's own testimony, not an independent ground truth —
    see the module docstring trust model."""
    bits_by_pid = {r["packet_id"]: r["bits"] for r in rows}
    return {
        "packet_fates": {str(pid): [fate, bits_by_pid[pid]]
                         for pid, fate in result["fates"].items()},
        "control_instances": {str(iid): list(pair)
                              for iid, pair in result["control"]["instances"].items()},
        "control_counters": result["control"]["counters"],
        "mechanism_counters": result["mechanism_counters"],
        "occupied": result["occupied"],
        "queue_area_bits_s": result["queue_area_bits_s"],
        "handover_events": result["handover"]["events"],
        "access": result["access"],
        "events_processed": result["events_processed"],
        "stop_time_s": result["stop_time_s"],
        "deliveries": {str(pid): d for pid, d in result["deliveries"].items()},
        "packet_events": result["packet_events"],
        "link_service_windows": result["link_service_windows"],
        "link_available_windows": result["link_available_windows"],
        "congestion_metrics": result["congestion_metrics"],
        "learning": (result.get("learning")
                     if result.get("learning") is not None
                     else {"algorithm": "none"}),
        "field_authority": dict(FIELD_AUTHORITY),
    }


def build_receipt(resolved: dict, manifest: dict, result: dict,
                  rows: list[dict], ledgers: dict, ledgers_sha256: str) -> dict:
    bits_by_pid = {r["packet_id"]: r["bits"] for r in rows}
    pkt_fates = {str(pid): [fate, bits_by_pid[pid]]
                 for pid, fate in result["fates"].items()}
    requested = requested_from_config(resolved["config"])
    effective = {k: result["mechanisms"]["effective"][k] for k in EFFECTIVE_KEYS}
    return {
        "schema": RECEIPT_SCHEMA,
        "congestion_metrics_contract": METRICS_V2_SCHEMA,
        "trace_manifest_contract": trace_mod.TRACE_MANIFEST_SCHEMA,
        "trace_identity_contract": config_mod.TRACE_IDENTITY_VERSION,
        "config_sha256": resolved["sha256"],
        "config_version": resolved["version"],
        "trace_manifest_sha256": manifest["__sha256"],
        "trace_sha256": manifest["__trace_sha256"],
        "trace_identity_sha256": manifest.get("trace_identity_sha256", ""),
        "code_sha256": code_sha256(),
        "ledgers_sha256": ledgers_sha256,
        "deps": dependency_versions(
            with_tensorflow=requested["learning_algorithm"] == "ddqn"),
        "seed": resolved["config"]["scenario"]["seed"],
        "horizon_s": result["horizon_s"],
        "natural_end": result["natural_end"],
        "interrupted": result["interrupted"],
        "error": result["error"],
        "events_processed": result["events_processed"],
        "mechanisms": {"requested": requested, "effective": effective},
        "research_eligible": result["research_eligible"],
        "routing_label": result["routing_label"],
        "totals": result["totals"],
        "fate_counts": result["fate_counts"],
        "packet_fates": pkt_fates,
        "control": {
            "counters": result["control"]["counters"],
            "totals": result["control"]["totals"],
            "fate_counts": result["control"]["fate_counts"],
        },
        "occupied": result["occupied"],
        "handover_event_count": len(result["handover"]["events"]),
        "conservation_ok": (
            result["totals"]["offered_bits"]
            == result["totals"]["delivered_bits"]
            + result["totals"]["terminal_loss_bits"]
            + result["totals"]["in_system_bits_at_stop"]),
    }


def write_run(out_dir: str, resolved: dict, trace_csv: bytes, manifest: dict,
              result: dict, rows: list[dict]) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "resolved_config.json").write_text(
        json.dumps({"version": resolved["version"],
                    "config": resolved["config"]}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    (out / "trace.csv").write_bytes(trace_csv)
    (out / "manifest.json").write_text(
        json.dumps({k: v for k, v in manifest.items() if not k.startswith("__")},
                   indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ledgers = build_ledgers(result, rows)
    (out / "ledgers.json").write_text(
        json.dumps(ledgers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ledgers_sha256 = _sha_file(out / "ledgers.json")
    receipt = build_receipt(resolved, manifest, result, rows, ledgers,
                            ledgers_sha256)
    if result["monitor_log"]:
        with open(out / "monitor.log", "w", encoding="utf-8") as fh:
            for t, kind, kv in result["monitor_log"]:
                fh.write(f"{t:.6f} {kind} {dict(kv)}\n")
    (out / "receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def _is_nonneg_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) \
        and math.isfinite(x) and x >= 0


def _is_nonneg_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def _learning_transition_accounting(mc: dict) -> list[str]:
    """Every learning decision opens exactly one transition; by the stop time
    each is either remembered (transitions) or explicitly discarded
    (learning_discarded_at_stop, or learning_discarded_at_rematch when a
    topology rematch requeues a queued packet before its action is served).
    Any other difference means transitions were silently lost."""
    if not all(_is_nonneg_int(mc.get(k)) for k in (
            "learning_decisions", "learning_transitions",
            "learning_discarded_at_stop", "learning_discarded_at_rematch")):
        return []  # the counter schema check reports the type problem
    if (mc["learning_decisions"] - mc["learning_transitions"]
            != mc["learning_discarded_at_stop"]
            + mc["learning_discarded_at_rematch"]):
        return ["learning decisions != transitions + discarded_at_stop "
                "+ discarded_at_rematch (transitions silently lost)"]
    return []


def _validate_v2_event_authority(errors: list[str], ledgers: dict,
                                 trace_rows: dict, raw_events,
                                 stored_metrics) -> None:
    """Cross-bind v2 event claims to the authoritative fate/delivery ledgers."""
    if not isinstance(raw_events, list):
        return
    if not isinstance(stored_metrics, dict):
        errors.append("v2 congestion metrics must be a mapping")
        return
    packet_fates = ledgers.get("packet_fates")
    deliveries = ledgers.get("deliveries")
    if not isinstance(packet_fates, dict):
        errors.append("v2 validation requires packet_fates mapping")
        packet_fates = {}
    if not isinstance(deliveries, dict):
        errors.append("v2 validation requires deliveries mapping")
        deliveries = {}
    ingress_events = [e for e in raw_events
                      if isinstance(e, dict) and e.get("kind") == "satellite_ingress"]
    delivered_events = [e for e in raw_events
                        if isinstance(e, dict) and e.get("kind") == "delivered"]
    ingress_pids = {str(e.get("pid")) for e in ingress_events}
    delivered_event_pids = {str(e.get("pid")) for e in delivered_events}
    trace_bits = {str(pid): item.get("bits") for pid, item in trace_rows.items()}
    offered_bits = sum(bits for bits in trace_bits.values()
                       if isinstance(bits, int) and not isinstance(bits, bool))
    if stored_metrics.get("offered_packets") != len(trace_rows):
        errors.append("v2 offered_packets != trace row count")
    if stored_metrics.get("offered_bits") != offered_bits:
        errors.append("v2 offered_bits != trace bit sum")
    for event in ingress_events:
        pid_s = str(event.get("pid"))
        pair = packet_fates.get(pid_s)
        if pid_s not in trace_rows:
            errors.append(f"satellite_ingress pid {pid_s} is not in trace")
        if not isinstance(pair, list) or len(pair) != 2:
            errors.append(f"satellite_ingress pid {pid_s} has no authoritative fate")
            continue
        # ACCESS_REJECTED is always pre-ingress by construction (the kernel
        # rejects at emission before any uplink admission), so an ingress
        # event for it is contradictory.  ACCESS_QUEUE_OVERFLOW is NOT here:
        # the historical kernel reuses that fate for BOTH the source uplink
        # (pre-ingress) and the destination downlink (post-ingress) queue —
        # the real ingress event is the authoritative split, and the scene
        # checker classifies by it (access-limited vs downlink-limited).
        if pair[0] == "ACCESS_REJECTED":
            errors.append(f"satellite_ingress pid {pid_s} has terminal access fate {pair[0]}")
        if pid_s in trace_bits and event.get("bits") != trace_bits[pid_s]:
            errors.append(f"satellite_ingress bits != trace bits for {pid_s}")
    admitted_bits = sum(trace_bits[pid] for pid in ingress_pids
                        if pid in trace_bits and isinstance(trace_bits[pid], int))
    if stored_metrics.get("admitted_at_satellite_ingress_packets") != len(ingress_pids):
        errors.append("v2 admitted packet count != satellite_ingress events")
    if stored_metrics.get("admitted_at_satellite_ingress_bits") != admitted_bits:
        errors.append("v2 admitted bits != satellite_ingress trace bits")

    delivered_fate_pids = {pid for pid, pair in packet_fates.items()
                           if isinstance(pair, list) and len(pair) == 2
                           and pair[0] == "DELIVERED"}
    if delivered_event_pids != set(deliveries):
        errors.append("delivered raw event ids != deliveries ids")
    if delivered_event_pids != delivered_fate_pids:
        errors.append("delivered raw event ids != DELIVERED fate ids")
    if not delivered_event_pids <= ingress_pids:
        errors.append("delivered packets must be a satellite_ingress subset")
    for event in delivered_events:
        pid_s = str(event.get("pid"))
        delivery = deliveries.get(pid_s)
        if not isinstance(delivery, dict):
            continue
        if event.get("at") != delivery.get("delivered_at"):
            errors.append(f"delivered event time != delivery time for {pid_s}")
        pair = packet_fates.get(pid_s)
        if not isinstance(pair, list) or len(pair) != 2 or pair[0] != "DELIVERED":
            errors.append(f"delivered event has non-DELIVERED fate for {pid_s}")
    delivered_bits = sum(pair[1] for pid, pair in packet_fates.items()
                         if pid in delivered_fate_pids and isinstance(pair, list)
                         and len(pair) == 2 and isinstance(pair[1], int))
    if stored_metrics.get("delivered_packets") != len(delivered_fate_pids):
        errors.append("v2 delivered packet count != DELIVERED fates")
    if stored_metrics.get("delivered_bits") != delivered_bits:
        errors.append("v2 delivered bits != DELIVERED fate bits")
    if stored_metrics.get("delivered_by_horizon_given_ingress_packets") != len(delivered_fate_pids):
        errors.append("v2 horizon delivery packet count != DELIVERED fates")
    if stored_metrics.get("delivered_by_horizon_given_ingress_bits") != delivered_bits:
        errors.append("v2 horizon delivery bits != DELIVERED fate bits")


def _validate_ledgers(ledgers, receipt: dict, trace_rows: dict,
                      verify_root: Path, resolved_cfg: dict | None,
                      metrics_contract: str) -> list[str]:
    """Schema/type/range and internal-relation checks for ledgers.json.

    Defensive throughout: malformed content appends error strings, never
    raises. `trace_rows`: pid(str) -> {bits, emit, deadline} from trace.csv.
    """
    errors: list[str] = []
    if not isinstance(ledgers, dict):
        return ["ledgers.json is not a mapping"]
    keys = set(ledgers)
    if keys != LEDGER_KEYS:
        errors.append(f"ledgers keys mismatch: unknown={sorted(keys - LEDGER_KEYS)} "
                      f"missing={sorted(LEDGER_KEYS - keys)}")
        # continue with whatever is present; per-field guards handle absence
    if ledgers.get("field_authority") != FIELD_AUTHORITY:
        errors.append("ledgers field_authority mismatch")

    raw_events = ledgers.get("packet_events")
    raw_windows = ledgers.get("link_service_windows")
    raw_available = ledgers.get("link_available_windows")
    if not isinstance(raw_available, list):
        errors.append("ledgers link_available_windows must be a list")
        raw_available = []
    stored_metrics = ledgers.get("congestion_metrics")
    # A packet may have a valid propagation start but no arrival because the
    # run horizon (or an explicit terminal loss) cut the flight short.  The
    # kernel passes this same fate-qualified set when it first computes the
    # metrics; receipt verification must use it too, otherwise a valid
    # high-load run becomes unverifiable at the horizon.
    non_arrival_pids = set()
    packet_fates = ledgers.get("packet_fates")
    if isinstance(packet_fates, dict):
        for pid_s, pair in packet_fates.items():
            if (isinstance(pid_s, str) and pid_s.isdigit()
                    and isinstance(pair, list) and len(pair) == 2
                    and pair[0] in {
                        "IN_SYSTEM_AT_STOP", "GEOMETRY_LOSS_IN_FLIGHT",
                        "RANDOM_OUTAGE_IN_FLIGHT", "DATA_DEADLINE_EXPIRED"}):
                non_arrival_pids.add(int(pid_s))
    if metrics_contract not in {METRICS_V1_SCHEMA, METRICS_V2_SCHEMA}:
        errors.append(f"unsupported congestion metrics contract: {metrics_contract!r}")
    try:
        recomputed_metrics = metrics.summarize(
            raw_events, raw_windows,
            available_capacity_windows=raw_available,
            non_arrival_pids=non_arrival_pids,
            access_boundary=(metrics_contract == METRICS_V2_SCHEMA))
    except metrics.MetricsError as exc:
        errors.append(f"congestion metrics invalid: {exc}")
    else:
        if stored_metrics != recomputed_metrics:
            errors.append("congestion_metrics != recomputed raw event metrics")
        if (isinstance(stored_metrics, dict)
                and stored_metrics.get("schema") != metrics_contract):
            errors.append("congestion_metrics schema != receipt contract")

    if metrics_contract == METRICS_V2_SCHEMA:
        _validate_v2_event_authority(
            errors, ledgers, trace_rows, raw_events, stored_metrics)

    # Learning artifact: the ledger SHA binds the metadata and verification
    # recomputes the actual checkpoint hash.  This prevents a saved/loaded
    # model from being silently replaced after a run while retaining a valid
    # receipt.  The external governance anchor remains the final trust layer.
    learning = ledgers.get("learning")
    requested_learning = receipt.get("mechanisms", {}).get(
        "requested", {}).get("learning_algorithm")
    if requested_learning == "none":
        if learning != {"algorithm": "none"}:
            errors.append("non-learning ledger must be exactly {'algorithm': 'none'}")
    elif requested_learning == "ddqn":
        if not isinstance(learning, dict):
            errors.append("DDQN run must have a learning ledger")
        else:
            expected_contract = ((resolved_cfg or {}).get("routing", {}).get("contract"))
            if expected_contract is not None and learning.get("contract") != expected_contract:
                errors.append("learning.contract != resolved routing.contract")
            for key in ("decisions", "transitions", "train_steps", "replay_size"):
                if not _is_nonneg_int(learning.get(key)):
                    errors.append(f"learning.{key} must be a non-negative integer")
            if learning.get("algorithm") != "ddqn":
                errors.append("learning.algorithm mismatch")
            if learning.get("mode") not in {"train", "eval"}:
                errors.append("learning.mode invalid")
            if learning.get("checkpoint_verified") is not True:
                errors.append("learning checkpoint was not verified after save/load")
            sha = learning.get("checkpoint_sha256")
            if not (isinstance(sha, str) and len(sha) == 64
                    and all(c in "0123456789abcdef" for c in sha)):
                errors.append("learning.checkpoint_sha256 invalid")
            checkpoint = verify_root / "ddqn" / "online.keras"
            if not checkpoint.is_file() or checkpoint.is_symlink():
                errors.append("learning checkpoint artifact missing")
            elif isinstance(sha, str) and _sha_file(checkpoint) != sha:
                errors.append("learning checkpoint artifact SHA mismatch")
            mc = ledgers.get("mechanism_counters")
            if isinstance(mc, dict):
                for key, counter in (
                        ("decisions", "learning_decisions"),
                        ("transitions", "learning_transitions"),
                        ("train_steps", "learning_train_steps")):
                    if learning.get(key) != mc.get(counter):
                        errors.append(f"learning.{key} != mechanism counter")
                errors.extend(_learning_transition_accounting(mc))
            if learning.get("op_determinism") is not True:
                errors.append("learning.op_determinism must be true "
                              "(fail-closed: runs without TF op determinism "
                              "are rejected)")
            expected_fast = (resolved_cfg or {}).get("learning", {}).get(
                "fast_train")
            if expected_fast is not None \
                    and learning.get("fast_train") is not expected_fast:
                errors.append("learning.fast_train != resolved config")
            if learning.get("mode") == "eval":
                if learning.get("train_steps") != 0:
                    errors.append("learning eval mode performed training")
                expected = (resolved_cfg or {}).get("learning", {}).get(
                    "checkpoint_sha256")
                if expected is not None and learning.get(
                        "loaded_checkpoint_sha256") != expected:
                    errors.append("loaded checkpoint SHA != resolved eval config")
                expected_meta = (resolved_cfg or {}).get(
                    "learning", {}).get("checkpoint_metadata_sha256")
                if expected_meta is not None and learning.get(
                        "loaded_checkpoint_metadata_sha256") != expected_meta:
                    errors.append(
                        "loaded checkpoint metadata SHA != resolved eval config")
    elif requested_learning == "qlearning":
        if not isinstance(learning, dict):
            errors.append("Q-learning run must have a learning ledger")
        else:
            expected_contract = ((resolved_cfg or {}).get("routing", {}).get("contract"))
            if expected_contract is not None and learning.get("contract") != expected_contract:
                errors.append("learning.contract != resolved routing.contract")
            for key in ("decisions", "transitions", "train_steps",
                        "table_size"):
                if not _is_nonneg_int(learning.get(key)):
                    errors.append(f"learning.{key} must be a non-negative integer")
            if learning.get("algorithm") != "qlearning":
                errors.append("learning.algorithm mismatch")
            if learning.get("mode") not in {"train", "eval"}:
                errors.append("learning.mode invalid")
            if learning.get("checkpoint_verified") is not True:
                errors.append("learning checkpoint was not verified after save/load")
            sha = learning.get("checkpoint_sha256")
            if not (isinstance(sha, str) and len(sha) == 64
                    and all(c in "0123456789abcdef" for c in sha)):
                errors.append("learning.checkpoint_sha256 invalid")
            checkpoint = verify_root / "qlearning" / "q_table.json"
            if not checkpoint.is_file() or checkpoint.is_symlink():
                errors.append("learning checkpoint artifact missing")
            elif isinstance(sha, str) and _sha_file(checkpoint) != sha:
                errors.append("learning checkpoint artifact SHA mismatch")
            mc = ledgers.get("mechanism_counters")
            if isinstance(mc, dict):
                for key, counter in (
                        ("decisions", "learning_decisions"),
                        ("transitions", "learning_transitions"),
                        ("train_steps", "learning_train_steps")):
                    if learning.get(key) != mc.get(counter):
                        errors.append(f"learning.{key} != mechanism counter")
                errors.extend(_learning_transition_accounting(mc))
            if learning.get("mode") == "eval":
                if learning.get("train_steps") != 0:
                    errors.append("learning eval mode performed training")
                expected = (resolved_cfg or {}).get("learning", {}).get(
                    "checkpoint_sha256")
                if expected is not None and learning.get(
                        "loaded_checkpoint_sha256") != expected:
                    errors.append("loaded checkpoint SHA != resolved eval config")
                expected_meta = (resolved_cfg or {}).get(
                    "learning", {}).get("checkpoint_metadata_sha256")
                if expected_meta is not None and learning.get(
                        "loaded_checkpoint_metadata_sha256") != expected_meta:
                    errors.append(
                        "loaded checkpoint metadata SHA != resolved eval config")
    elif requested_learning is not None:
        errors.append(f"unknown requested learning algorithm "
                      f"{requested_learning!r}")

    # stop_time: finite, and exactly the horizon on a natural end
    stop = ledgers.get("stop_time_s")
    if not _is_nonneg_num(stop):
        errors.append("ledgers stop_time_s must be a finite non-negative number")
        stop = None
    if receipt.get("natural_end") and stop is not None:
        if stop != receipt.get("horizon_s"):
            errors.append(f"stop_time_s {stop} != horizon_s "
                          f"{receipt.get('horizon_s')} on a natural end")

    # packet fates: [fate, bits] pairs with valid fate and positive int bits
    pf = ledgers.get("packet_fates")
    if not isinstance(pf, dict):
        errors.append("ledgers packet_fates must be a mapping")
        pf = {}
    for pid_s, pair in pf.items():
        if not (isinstance(pair, list) and len(pair) == 2
                and isinstance(pair[0], str)
                and isinstance(pair[1], int) and not isinstance(pair[1], bool)
                and pair[1] > 0):
            errors.append(f"packet_fates[{pid_s}] must be [fate, positive int bits]")

    # deliveries: exactly the DELIVERED set, with trace-consistent times
    dlv = ledgers.get("deliveries")
    if not isinstance(dlv, dict):
        errors.append("ledgers deliveries must be a mapping")
        dlv = {}
    delivered_pids = {p for p, pair in pf.items()
                      if isinstance(pair, list) and pair[0] == "DELIVERED"}
    if set(dlv) != delivered_pids:
        errors.append(f"deliveries set != DELIVERED fate set "
                      f"(extra={sorted(set(dlv) - delivered_pids, key=str)[:5]}, "
                      f"missing={sorted(delivered_pids - set(dlv), key=str)[:5]})")
    for pid_s, d in dlv.items():
        if not isinstance(d, dict) or set(d) != {"delivered_at", "path"}:
            errors.append(f"deliveries[{pid_s}] must have exactly delivered_at/path")
            continue
        t_at, path = d["delivered_at"], d["path"]
        if not _is_nonneg_num(t_at):
            errors.append(f"deliveries[{pid_s}].delivered_at not finite/>=0")
            continue
        tr = trace_rows.get(pid_s)
        if tr is not None:
            if t_at < tr["emit"]:
                errors.append(f"deliveries[{pid_s}] before emit_time")
            if tr["deadline"] is not None and t_at > tr["deadline"]:
                errors.append(f"deliveries[{pid_s}] after deadline")
        if stop is not None and t_at > stop:
            errors.append(f"deliveries[{pid_s}] after stop_time_s")
        if not (isinstance(path, list)
                and all(isinstance(x, int) and not isinstance(x, bool) for x in path)):
            errors.append(f"deliveries[{pid_s}].path must be a list of sat ids")

    # occupied / queue areas: fixed key sets, finite, non-negative
    for field, want in (("occupied", OCCUPIED_KEYS), ("queue_area_bits_s", QUEUE_AREA_KEYS)):
        val = ledgers.get(field)
        if not isinstance(val, dict) or set(val) != want:
            errors.append(f"ledgers {field} must have exactly keys {sorted(want)}")
        else:
            for k, v in val.items():
                if not _is_nonneg_num(v):
                    errors.append(f"ledgers {field}.{k} must be finite and >= 0")

    # events_processed
    if not _is_nonneg_int(ledgers.get("events_processed")):
        errors.append("ledgers events_processed must be a non-negative integer")

    # access stats: key set, types, and internal relations
    acc = ledgers.get("access")
    if not isinstance(acc, dict) or set(acc) != ACCESS_KEYS:
        errors.append(f"ledgers access must have exactly keys {sorted(ACCESS_KEYS)}")
    else:
        for k in ACCESS_INT_KEYS:
            if not _is_nonneg_int(acc.get(k)):
                errors.append(f"access.{k} must be a non-negative integer")
        for k in ("wait_time_s_total", "wait_time_s_max", "slot_hold_s_total"):
            if not _is_nonneg_num(acc.get(k)):
                errors.append(f"access.{k} must be finite and >= 0")
        if _is_nonneg_num(acc.get("wait_time_s_max")) and \
                _is_nonneg_num(acc.get("wait_time_s_total")) and \
                acc["wait_time_s_max"] > acc["wait_time_s_total"] + 1e-9:
            errors.append("access.wait_time_s_max > wait_time_s_total")
        rel = acc.get("releases")
        if not isinstance(rel, dict) or not all(
                isinstance(k, str) and _is_nonneg_int(v) for k, v in rel.items()):
            errors.append("access.releases must map reason -> non-negative int")

    # handover events: list of well-formed records within the run window
    ho = ledgers.get("handover_events")
    if not isinstance(ho, list):
        errors.append("ledgers handover_events must be a list")
    else:
        for i, e in enumerate(ho):
            if not isinstance(e, dict) or "t" not in e or "endpoint" not in e \
                    or "type" not in e:
                errors.append(f"handover_events[{i}] missing t/endpoint/type")
                continue
            if not _is_nonneg_num(e["t"]):
                errors.append(f"handover_events[{i}].t invalid")
            elif stop is not None and e["t"] > stop:
                errors.append(f"handover_events[{i}].t after stop_time_s")
            if e["type"] not in HANDOVER_TYPES:
                errors.append(f"handover_events[{i}].type unknown: {e['type']}")
            if e["type"] == "release" and not isinstance(e.get("reason"), str):
                errors.append(f"handover_events[{i}] release without reason")

    # control counters: exact keys, non-negative ints, lifecycle relations
    cc = ledgers.get("control_counters")
    if not isinstance(cc, dict) or set(cc) != CONTROL_COUNTER_KEYS:
        errors.append(f"control_counters must have exactly {sorted(CONTROL_COUNTER_KEYS)}")
        cc = {}
    else:
        for k, v in cc.items():
            if not _is_nonneg_int(v):
                errors.append(f"control_counters.{k} must be a non-negative integer")
        if all(_is_nonneg_int(cc.get(k)) for k in CONTROL_COUNTER_KEYS):
            if cc["entered_queue"] > cc["registered"]:
                errors.append("control entered_queue > registered")
            if cc["transmission_started"] > cc["entered_queue"]:
                errors.append("control transmission_started > entered_queue")
            if cc["transmission_completed"] > cc["transmission_started"]:
                errors.append("control transmission_completed > transmission_started")
            if cc["arrived"] > cc["transmission_completed"]:
                errors.append("control arrived > transmission_completed")
            fate_sum = (cc["arrived"] + cc["expired"] + cc["lost"]
                        + cc["geometry_lost"] + cc["overflow"] + cc["duplicate"]
                        + cc["in_system"])
            if fate_sum != cc["registered"]:
                errors.append("control fate sum != registered instances")

    # mechanism counters: exact keys, bool flags and non-negative ints
    mc = ledgers.get("mechanism_counters")
    if not isinstance(mc, dict) or set(mc) != MECHANISM_COUNTER_KEYS:
        errors.append(f"mechanism_counters must have exactly {sorted(MECHANISM_COUNTER_KEYS)}")
    else:
        for k, v in mc.items():
            if k in MECHANISM_COUNTER_BOOLS:
                if not isinstance(v, bool):
                    errors.append(f"mechanism_counters.{k} must be bool")
            elif not _is_nonneg_int(v):
                errors.append(f"mechanism_counters.{k} must be a non-negative integer")
    return errors


def verify_receipt_dir(out_dir: str) -> list[str]:
    """Strict exact-runtime verification, unchanged: recompute every
    checkable claim AND require the local analyzer sources/dependencies to
    equal the run-time identity recorded in the receipt.  Empty list =
    verified.  Any local != runtime difference (code sha or dependency
    versions) still FAILS."""
    errors, _identity = _verify_receipt_dir_impl(
        out_dir, require_local_runtime_identity=True)
    return errors


def _verify_receipt_dir_artifacts_unbound(
        out_dir: str) -> tuple[list[str], dict]:
    """Internal, unbound artifact-integrity primitive.

    Recomputed here, exactly as in the strict verifier: receipt/manifest/
    trace/config/ledgers/fates/totals/conservation/mechanisms/eligibility
    flag plus every hash binding.  The ONE thing this path does not require
    is that the LOCAL analyzer source code and dependencies equal the
    run-time record -- that local-equality gate is a statement about the
    verifying checkout, not about the historical artifact set.

    Returns (errors, run_identity) where run_identity carries the
    run-time identity recorded in the receipt (code_sha256, deps).
    This function is deliberately private and its empty error list is NOT an
    evidence verdict.  Only the higher-level V2 analyzer may call it, after
    binding run_identity through authorization + formal run + governance
    receipt + external launch witness.  The public verify_receipt_dir remains
    strict and rejects every local/runtime identity difference.
    """
    return _verify_receipt_dir_impl(out_dir, require_local_runtime_identity=False)


def _verify_receipt_dir_impl(out_dir: str, *,
                             require_local_runtime_identity: bool,
                             ) -> tuple[list[str], dict]:
    """Shared verification core.  require_local_runtime_identity selects
    the strict local==runtime gate (public strict verify) or the posterior
    artifacts path that returns the recorded run-time identity instead."""
    out = Path(out_dir)
    errors: list[str] = []
    rcp_path = out / "receipt.json"
    if not rcp_path.exists():
        return [f"missing receipt: {rcp_path}"], {}
    try:
        receipt = json.loads(rcp_path.read_text(encoding="utf-8"))
    except Exception as exc:  # corrupted JSON must never crash verify
        return [f"receipt.json unreadable: {exc}"], {}
    if not isinstance(receipt, dict):
        return ["receipt.json must be a JSON object"], {}

    # 0. strict versioned receipt contract.  The metrics contract comes from
    # this receipt schema, never from mutable ledgers.congestion_metrics.
    receipt_schema = receipt.get("schema")
    if receipt_schema == LEGACY_RECEIPT_SCHEMA:
        expected_receipt_keys = RECEIPT_KEYS_V3
        metrics_contract = METRICS_V1_SCHEMA
    elif receipt_schema == LEGACY_RECEIPT_SCHEMA_V4:
        expected_receipt_keys = RECEIPT_KEYS_V4
        metrics_contract = METRICS_V2_SCHEMA
        if receipt.get("congestion_metrics_contract") != metrics_contract:
            errors.append("v4 congestion_metrics_contract must be v2")
    elif receipt_schema == RECEIPT_SCHEMA:
        expected_receipt_keys = RECEIPT_KEYS_V5
        metrics_contract = METRICS_V2_SCHEMA
        if receipt.get("congestion_metrics_contract") != metrics_contract:
            errors.append("v5 congestion_metrics_contract must be v2")
        if receipt.get("trace_manifest_contract") != trace_mod.TRACE_MANIFEST_SCHEMA:
            errors.append("v5 trace_manifest_contract must be manifest/v2")
        if receipt.get("trace_identity_contract") not in {
                config_mod.TRACE_IDENTITY_VERSION_V2,
                config_mod.TRACE_IDENTITY_VERSION}:
            errors.append(
                "v5 trace_identity_contract must be identity/v2 or identity/v3")
    else:
        expected_receipt_keys = RECEIPT_KEYS_V5
        metrics_contract = METRICS_V2_SCHEMA
        errors.append(f"receipt schema mismatch: {receipt_schema}")
    keys = set(receipt)
    if keys != expected_receipt_keys:
        errors.append(f"receipt keys mismatch: unknown={sorted(keys - expected_receipt_keys)} "
                      f"missing={sorted(expected_receipt_keys - keys)}")
    for bf in ("natural_end", "interrupted", "research_eligible", "conservation_ok"):
        if not isinstance(receipt.get(bf), bool):
            errors.append(f"{bf} must be bool")

    # 1. artifact hashes
    manifest = None
    trace_rows: dict[str, dict] = {}
    trace_list: list[dict] = []
    trace_loaded = False
    rcp_cfg = out / "resolved_config.json"
    tpath = out / "trace.csv"
    if not tpath.is_file() or tpath.is_symlink():
        errors.append("missing artifact trace.csv")
    else:
        if _sha_file(tpath) != receipt.get("trace_sha256"):
            errors.append("trace.csv sha mismatch")
        try:
            trace_list = trace_mod.load_trace(str(tpath))
            trace_loaded = True
            trace_rows = {
                str(r["packet_id"]): {
                    "bits": r["bits"], "emit": r["emit_time_s"],
                    "deadline": r["deadline_at_s"],
                }
                for r in trace_list
            }
        except Exception as exc:  # corrupted CSV must never crash verify
            errors.append(f"trace.csv unreadable: {exc}")
            trace_rows = {}
            trace_list = []
    mpath = out / "manifest.json"
    if not mpath.is_file() or mpath.is_symlink():
        errors.append("missing artifact manifest.json")
    else:
        if hashlib.sha256(mpath.read_bytes()).hexdigest() != receipt.get("trace_manifest_sha256"):
            errors.append("manifest sha mismatch")
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest.json must be a JSON object")
        except Exception as exc:
            errors.append(f"manifest.json unreadable: {exc}")
            manifest = None
        if manifest is not None and manifest.get("trace_sha256") and tpath.exists():
            if manifest["trace_sha256"] != _sha_file(tpath):
                errors.append("manifest trace sha != trace.csv sha")
        if manifest is not None and trace_loaded:
            offered_bits = sum(r["bits"] for r in trace_list)
            time_range = ([trace_list[0]["emit_time_s"],
                           trace_list[-1]["emit_time_s"]]
                          if trace_list else [0.0, 0.0])
            active_endpoints = len(
                {r["src_grid_id"] for r in trace_list}
                | {r["dst_grid_id"] for r in trace_list})
            if manifest.get("offered_packets") != len(trace_list):
                errors.append("manifest offered_packets != trace row count")
            if manifest.get("offered_bits") != offered_bits:
                errors.append("manifest offered_bits != trace bit sum")
            if manifest.get("ledger") != {"packets": len(trace_list),
                                           "bits": offered_bits}:
                errors.append("manifest ledger != trace totals")
            if manifest.get("time_range_s") != time_range:
                errors.append("manifest time_range_s != trace time range")
            if manifest.get("active_endpoints") != active_endpoints:
                errors.append("manifest active_endpoints != trace active cells")

    # 2. config identity from the on-disk resolved config
    resolved_cfg = None
    raw_resolved_cfg = None
    resolved_version = None
    if not rcp_cfg.is_file() or rcp_cfg.is_symlink():
        errors.append("missing artifact resolved_config.json")
    else:
        try:
            _rc = json.loads(rcp_cfg.read_text())
            raw_resolved_cfg = _rc["config"]
            resolved_cfg = raw_resolved_cfg
            resolved_version = _rc["version"]
            canonical = json.dumps(raw_resolved_cfg, sort_keys=True, separators=(",", ":"))
            if hashlib.sha256(canonical.encode()).hexdigest() != receipt.get("config_sha256"):
                errors.append("resolved config sha mismatch")
        except Exception as exc:  # fail closed on any parse problem
            errors.append(f"resolved config unreadable: {exc}")
    if resolved_cfg is not None and not (
            isinstance(resolved_cfg, dict)
            and isinstance(resolved_cfg.get("scenario"), dict)
            and isinstance(resolved_cfg.get("routing"), dict)):
        errors.append("resolved config malformed: missing scenario/routing groups")
        resolved_cfg = None
    if resolved_cfg is not None:
        legacy_contract = receipt_schema in {
            LEGACY_RECEIPT_SCHEMA, LEGACY_RECEIPT_SCHEMA_V4}
        has_emission = (isinstance(raw_resolved_cfg.get("demand"), dict)
                        and "emission_end_s" in raw_resolved_cfg["demand"])
        if legacy_contract and has_emission:
            errors.append("legacy receipt raw resolved config must omit emission_end_s")
        if receipt_schema == RECEIPT_SCHEMA and not has_emission:
            errors.append("v5 receipt raw resolved config must include emission_end_s")
        if legacy_contract and not has_emission:
            resolved_cfg = json.loads(json.dumps(raw_resolved_cfg))
            resolved_cfg.setdefault("demand", {})["emission_end_s"] = None
        try:
            validated = config_mod.resolve_config(resolved_cfg)
            if validated["config"] != resolved_cfg:
                raise ValueError("artifact is not a complete resolved config")
            if resolved_version != validated["version"]:
                raise ValueError("resolved config schema version mismatch")
        except Exception as exc:
            errors.append(f"resolved config semantic validation failed: {exc}")
            resolved_cfg = None
    if resolved_version is not None and receipt.get("config_version") != resolved_version:
        errors.append("config_version != resolved_config.json version")
    if resolved_cfg is not None:
        if receipt.get("seed") != resolved_cfg["scenario"]["seed"]:
            errors.append("seed != resolved config seed")
        if receipt.get("horizon_s") != resolved_cfg["scenario"]["duration_s"]:
            errors.append("horizon_s != resolved config duration_s")
        expected_label = ("analysis_upper_bound"
                          if resolved_cfg["routing"]["policy"] == "oracle" else None)
        if receipt.get("routing_label") != expected_label:
            errors.append("routing_label != label derived from resolved config")
        if trace_loaded:
            try:
                emission_horizon = resolved_cfg["demand"]["emission_end_s"]
                emission_horizon = (resolved_cfg["scenario"]["duration_s"]
                                    if emission_horizon is None else emission_horizon)
                trace_mod.validate_packet_rows(
                    trace_list,
                    horizon_s=float(emission_horizon),
                    max_packets=int(resolved_cfg["execution"]["max_packets"]))
            except Exception as exc:
                errors.append(f"trace violates resolved config: {exc}")
    if manifest is not None:
        if receipt_schema == RECEIPT_SCHEMA and manifest.get("schema") != trace_mod.TRACE_MANIFEST_SCHEMA:
            errors.append("v5 receipt requires trace manifest contract v2")
        if receipt_schema in {LEGACY_RECEIPT_SCHEMA, LEGACY_RECEIPT_SCHEMA_V4} \
                and manifest.get("schema") != trace_mod.TRACE_MANIFEST_SCHEMA_V1:
            errors.append("legacy receipt requires trace manifest contract v1")
        errors.extend(_validate_manifest(manifest, resolved_cfg, resolved_version))
    # trace identity: rebuilt from resolved config + manifest input hash.
    # The builder is chosen ONLY from the persisted trace_identity_contract
    # (v5 receipts): identity/v2 or identity/v3, never guessed from the
    # current code version or the manifest schema.  Legacy receipts keep the
    # unchanged identity/v1 path.
    if manifest is not None and resolved_cfg is not None and resolved_version:
        from . import config as _config
        if receipt_schema == RECEIPT_SCHEMA:
            contract = receipt.get("trace_identity_contract")
            if contract == _config.TRACE_IDENTITY_VERSION_V2:
                identity_fn = _config.trace_identity_sha256_v2
            elif contract == _config.TRACE_IDENTITY_VERSION:
                identity_fn = _config.trace_identity_sha256
            else:
                # the v5 gate above already reported the invalid contract;
                # never fall through to a guess
                identity_fn = None
            if identity_fn is None:
                errors.append(
                    "v5 trace identity cannot be recomputed: unknown "
                    "trace_identity_contract")
            else:
                expected_identity = identity_fn(
                    {"version": resolved_version, "config": raw_resolved_cfg},
                    manifest.get("input_sha256", ""))
                if manifest.get("trace_identity_sha256") != expected_identity:
                    errors.append(
                        "manifest trace identity != resolved config trace scope")
                if receipt.get("trace_identity_sha256") != expected_identity:
                    errors.append("receipt trace identity mismatch")
        else:
            # legacy receipt: identity/v1 stays unchanged
            expected_identity = _config.legacy_trace_identity_sha256(
                {"version": resolved_version, "config": raw_resolved_cfg},
                manifest.get("input_sha256", ""))
            if manifest.get("trace_identity_sha256") != expected_identity:
                errors.append(
                    "manifest trace identity != resolved config trace scope")
            if receipt.get("trace_identity_sha256") != expected_identity:
                errors.append("receipt trace identity mismatch")

    # 3. code and dependency identity.  The strict default requires the
    #    local analyzer source/deps to equal the run-time record.  The
    #    internal unbound artifact-integrity path instead
    #    records the run-time identity for the caller to bind through the
    #    formal governance chain (authorization + formal witness + governance
    #    receipt + external launch witness), never through a local copy.
    run_identity = {
        "code_sha256": receipt.get("code_sha256"),
        "deps": receipt.get("deps"),
    }
    code_sha = receipt.get("code_sha256")
    if not (isinstance(code_sha, str) and len(code_sha) == 64
            and all(ch in "0123456789abcdef" for ch in code_sha)):
        errors.append("receipt code_sha256 must be a lowercase SHA256")
    want_tf = bool(resolved_cfg) and (
        (resolved_cfg.get("learning") or {}).get("algorithm") == "ddqn")
    expected_dep_keys = DEP_KEYS | ({TF_DEP_KEY} if want_tf else set())
    deps = receipt.get("deps")
    if require_local_runtime_identity and code_sha256() != code_sha:
        errors.append("leo_sim code sha mismatch (sources changed since the run)")
    if not isinstance(deps, dict) or set(deps) != expected_dep_keys:
        errors.append(f"deps must be exactly {sorted(expected_dep_keys)}")
    elif require_local_runtime_identity:
        try:
            local_deps = dependency_versions(with_tensorflow=want_tf)
        except ImportError:
            errors.append(
                "tensorflow is not importable on this host; cannot verify "
                "the DDQN dependency pin")
        else:
            if deps != local_deps:
                errors.append(f"dependency versions differ from the run: "
                              f"{deps} vs {local_deps}")


    # 4. run completion state
    if not receipt.get("natural_end") or receipt.get("interrupted"):
        errors.append("run did not end naturally (natural_end/interrupted)")

    # 5. ledgers artifact: sha binding, strict schema, cross-checks
    ledgers = None
    lpath = out / "ledgers.json"
    if not lpath.is_file() or lpath.is_symlink():
        errors.append("missing artifact ledgers.json")
    else:
        if _sha_file(lpath) != receipt.get("ledgers_sha256"):
            errors.append("ledgers.json sha mismatch")
        try:
            ledgers = json.loads(lpath.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"ledgers.json unreadable: {exc}")
    if ledgers is not None:
        try:
            errors.extend(_validate_ledgers(
                ledgers, receipt, trace_rows, out, resolved_cfg,
                metrics_contract))
        except Exception as exc:  # never let malformed content crash verify
            errors.append(f"ledgers validation failure (fail closed): {exc}")
        lg_pf = ledgers.get("packet_fates", {})
        r_pf = receipt.get("packet_fates", {})
        if not isinstance(r_pf, dict):
            errors.append("receipt packet_fates must be a mapping")
            r_pf = {}
        if isinstance(lg_pf, dict):
            if set(r_pf) != set(lg_pf):
                errors.append("packet_fates id set != ledgers id set")
            elif r_pf != lg_pf:
                errors.append("packet_fates != ledgers packet_fates")
        if receipt.get("events_processed") != ledgers.get("events_processed"):
            errors.append("events_processed != ledgers")
        if receipt.get("occupied") != ledgers.get("occupied"):
            errors.append("occupied != ledgers occupied")
        ho = ledgers.get("handover_events", [])
        if receipt.get("handover_event_count") != (len(ho) if isinstance(ho, list) else -1):
            errors.append("handover_event_count != ledgers handover events")
    # exact packet set vs trace, fate validity, fate_counts, totals,
    # conservation — recomputed from the ledger packet fates
    pf_raw = (ledgers or {}).get("packet_fates", receipt.get("packet_fates", {}))
    if not isinstance(pf_raw, dict):
        pf_raw = {}
    # Only schema-valid pairs participate in downstream recomputation.  This
    # keeps malformed artifacts fail-closed without a second-pass crash.
    pf = {
        pid_s: pair for pid_s, pair in pf_raw.items()
        if isinstance(pair, list) and len(pair) == 2
        and isinstance(pair[0], str)
        and isinstance(pair[1], int) and not isinstance(pair[1], bool)
        and pair[1] > 0
    }
    if set(pf) != set(trace_rows):
        # lexicographic sort keeps malformed ids (non-numeric strings) in the
        # error message without crashing verify with int("abc")
        missing = sorted(set(trace_rows) - set(pf))[:5]
        extra = sorted(set(pf) - set(trace_rows))[:5]
        errors.append(f"packet_fates id set != trace id set (missing={missing}, extra={extra})")
    for pid_s, pair in pf.items():
        fate, bits = pair
        if pid_s in trace_rows and trace_rows[pid_s]["bits"] != bits:
            errors.append(f"packet {pid_s} bits != trace bits")
        if fate not in fates.DATA_FATES:
            errors.append(f"invalid fate {fate} for packet {pid_s}")
    recomputed_counts = {f: 0 for f in fates.DATA_FATES}
    for pid_s, pair in pf.items():
        if pair[0] in recomputed_counts:
            recomputed_counts[pair[0]] += 1
    if recomputed_counts != receipt.get("fate_counts"):
        errors.append("recomputed fate_counts != receipt fate_counts")
    delivered = sum(b for f, b in pf.values() if f == "DELIVERED")
    loss = sum(b for f, b in pf.values() if f in fates.TERMINAL_LOSS_FATES)
    in_system = sum(b for f, b in pf.values() if f == "IN_SYSTEM_AT_STOP")
    t = receipt.get("totals", {})
    if (delivered, loss, in_system) != (t.get("delivered_bits"),
                                        t.get("terminal_loss_bits"),
                                        t.get("in_system_bits_at_stop")):
        errors.append("recomputed fate bits != receipt totals")
    if delivered + loss + in_system != t.get("offered_bits"):
        errors.append("bit conservation violated")
    if not receipt.get("conservation_ok"):
        errors.append("receipt conservation_ok is false")
    if manifest is not None and manifest.get("offered_bits") != t.get("offered_bits"):
        errors.append("manifest offered_bits != receipt offered_bits")
    # control ledger: recomputed per instance from ledgers
    if ledgers is not None:
        ci = ledgers.get("control_instances", {})
        if not isinstance(ci, dict):
            errors.append("control_instances must be a mapping")
            ci = {}
        c_counts = {f: 0 for f in fates.CONTROL_FATES}
        valid_ci = {}
        for iid, pair in ci.items():
            if not (isinstance(pair, list) and len(pair) == 3
                    and isinstance(pair[0], str)
                    and isinstance(pair[1], int) and not isinstance(pair[1], bool)
                    and pair[1] > 0):
                errors.append(f"control_instances[{iid}] must be "
                              "[fate, positive int bits, received_at|None]")
                continue
            fate, bits, received_at = pair
            if resolved_cfg is not None:
                expected = (resolved_cfg.get("control_plane") or {}).get(
                    "packet_bits")
                if expected is not None and bits != expected:
                    errors.append(
                        f"control_instances[{iid}] bits {bits} != resolved "
                        f"control_plane.packet_bits {expected}")
            if not (received_at is None or _is_nonneg_num(received_at)):
                errors.append(f"control_instances[{iid}] received_at must be "
                              "None or a finite non-negative number")
            elif fate in fates.CONTROL_ARRIVAL_FATES and received_at is None:
                errors.append(f"control_instances[{iid}] fate {fate} arrived "
                              "but received_at is None")
            elif (fate not in fates.CONTROL_ARRIVAL_FATES
                  and fate != "CONTROL_EXPIRED" and received_at is not None):
                errors.append(f"control_instances[{iid}] fate {fate} never "
                              "arrives but received_at is set")
            elif received_at is not None:
                stop_t = ledgers.get("stop_time_s")
                if _is_nonneg_num(stop_t) and received_at > stop_t:
                    errors.append(f"control_instances[{iid}] received_at after "
                                  "stop_time_s")
            if fate not in fates.CONTROL_FATES:
                errors.append(f"invalid control fate {fate} for instance {iid}")
            elif fate in c_counts:
                c_counts[fate] += 1
            valid_ci[iid] = pair
        c_delivered = sum(p[1] for p in valid_ci.values()
                          if p[0] == "DELIVERED")
        c_loss = sum(p[1] for p in valid_ci.values()
                     if p[0] in fates.CONTROL_TERMINAL_LOSS)
        c_insys = sum(p[1] for p in valid_ci.values()
                      if p[0] == "IN_SYSTEM_AT_STOP")
        expected_control = {
            "counters": ledgers.get("control_counters"),
            "totals": {"offered_bits": c_delivered + c_loss + c_insys,
                       "delivered_bits": c_delivered,
                       "terminal_loss_bits": c_loss,
                       "in_system_bits_at_stop": c_insys},
            "fate_counts": c_counts,
        }
        if receipt.get("control") != expected_control:
            errors.append("receipt control summary != recomputed control ledger")
        cc = ledgers.get("control_counters", {})
        mc = ledgers.get("mechanism_counters", {})
        if isinstance(cc, dict) and isinstance(mc, dict):
            relations = {
                "snapshots_created": "control_snapshots",
                "registered": "control_registered",
                "entered_queue": "control_entered_queue",
                "transmission_started": "control_tx_started",
                "transmission_completed": "control_tx_completed",
            }
            for ck, mk in relations.items():
                if cc.get(ck) != mc.get(mk):
                    errors.append(f"control {ck} != mechanism counter {mk}")
            if cc.get("registered") != len(valid_ci):
                errors.append("control registered != control instance count")
            fate_to_counter = {
                "DELIVERED": "arrived", "CONTROL_EXPIRED": "expired",
                "RANDOM_OUTAGE_IN_FLIGHT": "lost",
                "GEOMETRY_LOSS_IN_FLIGHT": "geometry_lost",
                "QUEUE_OVERFLOW": "overflow", "DUPLICATE": "duplicate",
                "IN_SYSTEM_AT_STOP": "in_system",
            }
            for fate, counter in fate_to_counter.items():
                if cc.get(counter) != c_counts.get(fate):
                    errors.append(
                        f"control {counter} != {fate} instance count")

    # 6. requested mechanisms rebuilt from the on-disk config; effective flags
    #    recomputed from ledgers counters; research_eligible recomputed
    mech = receipt.get("mechanisms", {})
    if not isinstance(mech, dict) or set(mech) != {"requested", "effective"}:
        errors.append("mechanisms must have exactly requested/effective")
        mech = {"requested": {}, "effective": {}}
    if set(mech.get("requested", {})) - REQUESTED_KEYS:
        errors.append("unknown keys in mechanisms.requested")
    if set(mech.get("effective", {})) != EFFECTIVE_KEYS:
        errors.append(f"mechanisms.effective must be exactly {sorted(EFFECTIVE_KEYS)}")
    req = None
    if resolved_cfg is not None:
        req = requested_from_config(resolved_cfg)
        if req != mech.get("requested"):
            errors.append(f"receipt requested mechanisms != resolved config: "
                          f"{mech.get('requested')} vs {req}")
    if req is None:
        req = mech.get("requested", {})
    counters = (ledgers or {}).get("mechanism_counters", {})
    if not isinstance(counters, dict):
        errors.append("mechanism_counters must be a mapping for effective flags")
        counters = {}
    expected_eff = effective_from_counters(counters, req)
    if mech.get("effective") != expected_eff:
        errors.append(f"effective flags != recomputed: {mech.get('effective')} "
                      f"vs {expected_eff}")
    expected_eligible = expected_research_eligible(
        req, expected_eff,
        bool(receipt.get("natural_end")), bool(receipt.get("interrupted")))
    if receipt.get("research_eligible") != expected_eligible:
        errors.append(f"research_eligible should be {expected_eligible}")
    if req.get("monitor") and not (out / "monitor.log").exists():
        errors.append("monitor requested but monitor.log missing")
    return errors, run_identity
