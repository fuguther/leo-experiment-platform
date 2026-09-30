"""T1-COMPLETE P8: fair comparison of the five execution modes.

MODES
-----
per_packet   every routing request scores fully and waits for the shared pool
per_flow     (sat, src, dst, class) cache; a hit only queries, a miss/kick is
             recomputed and still pays the shared pool
precomputed  a topology-only next-hop table built once; per-decision scoring is
             skipped, and the build/install cost is reported
async_point  one background ranking per scope, reused by later packets
async_window four background rankings per scope, one per time bin

FAIRNESS
--------
Every row runs the SAME trace rows, the SAME seed, the SAME state-time arm and
predictor, the SAME compute service time and pool bound, and the same legality
and fallback rules.  The only difference is the execution mechanism, which is
asserted by diffing the resolved configs.  Model calls, table queries and
fallbacks are reported separately and never summed into one number.

DDQN BOUNDARY
-------------
The matrix runs the deterministic scorer.  A learning arm is checked only for
availability: the report says whether a fixed inference checkpoint exists and,
when it does not, records the precise blocker instead of pretending the
interface was validated with a model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import tempfile
from pathlib import Path

from CODE.experiment_platform import (artifact_identity, outcome_metrics,
                                      scripted_scenarios)
from CODE.leo_sim import config as config_mod, kernel, time_alignment as ta
from CODE.leo_sim import trace as trace_mod

SCHEMA = "execution-compare/v1"
MODES = ("per_packet", "per_flow", "precomputed", "async_point", "async_window")
ASYNC_MODES = ("async_point", "async_window")


class ExecutionCompareError(RuntimeError):
    pass


def _rows_digest(rows):
    payload = json.dumps(rows, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _e2e(deliveries, packet_events):
    emitted = {ev["pid"]: ev["at"] for ev in packet_events
               if ev.get("kind") == "packet_emitted"}
    out = []
    for pid, d in deliveries.items():
        if pid in emitted:
            out.append(float(d["delivered_at"]) - float(emitted[pid]))
    return out


def _p95(values):
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = 0.95 * (len(ordered) - 1)
    low, high = int(pos), min(int(pos) + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def _failure_details(resolved):
    return {"algorithm": resolved["config"]["learning"]["algorithm"],
            "checkpoint_path": resolved["config"]["learning"]["checkpoint_path"],
            "checkpoint_sha256":
                resolved["config"]["learning"]["checkpoint_sha256"]}


def ddqn_status(resolved):
    """Hash-verified fixed-inference availability; never a performance claim.

    AVAILABLE is never inferred from file existence: the checkpoint hash, the
    sibling metadata hash and the loader availability are all checked, and the
    inference-only adapter (leo_sim.inference) is what a caller would use.
    """
    from CODE.leo_sim import inference

    cfg = resolved["config"]["learning"]
    details = _failure_details(resolved)
    if cfg["algorithm"] == "none":
        return {"state": "NOT_REQUESTED", "details": details,
                "adapter": "CODE.leo_sim.inference.FixedInferenceAdapter",
                "note": "the matrix runs the deterministic scorer"}
    metadata_path = cfg.get("checkpoint_metadata_path")
    if metadata_path is None:
        path = cfg.get("checkpoint_path")
        metadata_path = (str(Path(str(path)).with_name("metadata.json"))
                         if path else None)
    verification = inference.verify_checkpoint(
        cfg.get("checkpoint_path"), cfg.get("checkpoint_sha256"),
        metadata_path, cfg.get("checkpoint_metadata_sha256"))
    state = verification["state"]
    return {
        "state": ("EXTERNAL_BLOCKER" if state.startswith("METADATA")
                  or state == "EXTERNAL_BLOCKER" else state),
        "details": dict(details, verification=verification),
        "reason": verification.get("reason"),
        "recovery": verification.get("recovery"),
        "adapter": "CODE.leo_sim.inference.FixedInferenceAdapter",
        "note": "a fixed-inference adapter is implemented and validated with a "
                "fixed-parameter small model; a REAL trained checkpoint still "
                "needs all hashes plus the loader on this host",
    }


def _mode_config(resolved, mode, overrides=None):
    cfg = json.loads(json.dumps(resolved["config"]))
    cfg["time_alignment"]["enabled"] = True
    cfg["time_alignment"]["execution_mode"] = mode
    if mode in ASYNC_MODES:
        cfg["async_routing"]["enabled"] = True
        if mode == "async_point":
            cfg["async_routing"]["window_bins"] = 1
    else:
        cfg["async_routing"]["enabled"] = False
    cfg["execution"]["decision_observation_mode"] = "frozen"
    for group, values in dict(overrides or {}).items():
        cfg.setdefault(group, {})
        cfg[group].update(values)
    return config_mod.resolve_config(cfg)


def _row_for_mode(base_resolved, rows, geometry, mode, overrides=None, *,
                  deadline_s=None, window=None, source=None):
    resolved = _mode_config(base_resolved, mode, overrides)
    sink, timeline = [], []
    result = kernel.run_simulation(resolved, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=timeline)
    compute_req = [m for m in timeline
                   if m.get("milestone") == "compute_request"
                   and m.get("pid") is not None]
    compute_wait = [m for m in timeline if m.get("milestone") == "compute_wait"
                    and m.get("pid") is not None]
    queries = [m for m in timeline if m.get("milestone") == "schedule_query"]
    installs = [m for m in timeline if m.get("milestone") == "schedule_installed"]
    def _audit(row):
        record = row.get("observation_at_start") or {}
        audit = record.get("time_alignment")
        return audit if isinstance(audit, dict) else None

    cache_hits = sum(1 for r in sink
                     if (_audit(r) or {}).get("cache_hit") is True)
    fallbacks = sum(1 for r in sink
                    if (_audit(r) or {}).get("fallback") is True)
    e2e = _e2e(result["deliveries"], result["packet_events"])
    forwards = [r for r in sink if r.get("kind") == "forward"]
    bin_counts = {}
    version_counts = {}
    for row in queries:
        key = row.get("bin")
        bin_counts[key] = bin_counts.get(key, 0) + 1
        key = row.get("version")
        version_counts[key] = version_counts.get(key, 0) + 1
    duration = float(resolved["config"]["scenario"]["duration_s"])
    n_sats = int(resolved["config"]["scenario"]["num_satellites"])
    packet_bits = int(resolved["config"]["demand"]["packet_bits"])
    # A4: the FINAL outcome and the TOTAL compute cost, computed once per mode
    # from the SAME raw records the row above is built from.  The measurement
    # window is derived from the trace rows (not from the configured duration)
    # once per mode and carried on the row, because every window-scoped number
    # below is uninterpretable without it.
    measurement_window = (window if window is not None
                          else outcome_metrics.build_measurement_window(rows))
    outcome = outcome_metrics.compare_outcome(
        result, timeline, sink, rows, window=measurement_window,
        deadline_s=deadline_s,
        cost={"service_s": resolved["config"]["execution"]["compute_delay_s"],
              "servers": resolved["config"]["execution"][
                  "compute_servers_per_satellite"]},
        context={"cell": mode, "run_id": mode, "mode": mode,
                 "config_sha256": resolved["sha256"],
                 "trace_sha256": ((source or {}).get("trace_sha256")
                                  or (source or {}).get("rows_digest")
                                  or _rows_digest(rows))})
    return {
        "mode": mode,
        "config_sha256": resolved["sha256"],
        "resolved_execution_mode":
            resolved["config"]["time_alignment"]["execution_mode"],
        "async_enabled": resolved["config"]["async_routing"]["enabled"],
        "seed": resolved["config"]["scenario"]["seed"],
        "arm": resolved["config"]["time_alignment"]["arm"],
        "predictor": resolved["config"]["time_alignment"]["predictor"],
        "compute": {
            "service_s": resolved["config"]["execution"]["compute_delay_s"],
            "servers": resolved["config"]["execution"][
                "compute_servers_per_satellite"],
            "decision_requests": len(compute_req),
            "queued_requests": sum(1 for m in compute_wait
                                   if m.get("wait_s", 0.0) > 0.0),
            "total_wait_s": sum(float(m.get("wait_s", 0.0))
                                for m in compute_wait),
            "max_wait_s": max((float(m.get("wait_s", 0.0))
                               for m in compute_wait), default=0.0),
        },
        "reuse": {
            "forward_decisions": len(forwards),
            "per_flow_cache_hits": cache_hits,
            "schedule_queries": len(queries),
            "schedule_installs": len(installs),
            "fallbacks": fallbacks,
            "installed_versions": sorted({m.get("version") for m in installs}),
            "query_bin_counts": {str(k): v for k, v in sorted(
                bin_counts.items(), key=lambda kv: str(kv[0]))},
            "query_version_counts": {str(k): v for k, v in sorted(
                version_counts.items(), key=lambda kv: str(kv[0]))},
            "bins": (None if mode not in ASYNC_MODES
                     else (1 if mode == "async_point"
                           else resolved["config"]["async_routing"][
                               "window_bins"])),
        },
        "scale": {
            "num_satellites": n_sats,
            "duration_s": duration,
            "packet_bits": packet_bits,
            "packet_bytes": packet_bits / 8.0,
            "forward_requests_per_satellite_per_s": (
                len(forwards) / (n_sats * duration) if n_sats and duration
                else None),
            "compute_requests_per_satellite_per_s": (
                len(compute_req) / (n_sats * duration) if n_sats and duration
                else None),
        },
        "query_service": result["execution_mode"]["query_service"],
        "precompute": result["execution_mode"]["precompute"],
        "outcome": {
            "delivered": len(result["deliveries"]),
            "fate_counts": {k: v for k, v in result["fate_counts"].items() if v},
            "e2e_samples": len(e2e),
            "e2e_mean_s": (None if not e2e else statistics.fmean(e2e)),
            "e2e_p95_s": _p95(e2e),
            "events_processed": result["events_processed"],
        },
        # A4 deliverables: FINAL outcome metrics and TOTAL compute cost, on
        # every mode row.  Neither replaces nor renames anything above; the
        # per-metric rows (with their NOT_COMPUTABLE status) stay available in
        # the outcome document for the CSV writer.
        "measurement_window": measurement_window,
        "network_outcome": outcome["network_outcome"],
        "total_cost": outcome["total_cost"],
        "outcome_document": {
            "schema": outcome["schema"],
            "partition_exact": outcome["partition_exact"],
            "not_computable": outcome["not_computable"],
            "e2e_stage_disclaimer": outcome["e2e_stage_disclaimer"],
            "row_count": len(outcome["rows"]),
            "rows": outcome["rows"],
        },
    }


def _config_diff(left, right):
    """Top-level group/key differences between two resolved configs."""
    diffs = []
    for group in sorted(set(left) | set(right)):
        a, b = left.get(group), right.get(group)
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if a.get(key) != b.get(key):
                    diffs.append(f"{group}.{key}")
        elif a != b:
            diffs.append(group)
    return diffs


def compare(resolved, rows, geometry, source, modes=MODES, overrides=None, *,
            deadline_s=None, window=None):
    rows_digest = _rows_digest(rows)
    mode_rows = []
    configs = {}
    for mode in modes:
        if mode not in MODES:
            raise ExecutionCompareError(f"unknown execution mode {mode!r}")
        row = _row_for_mode(
            resolved, rows, geometry, mode, overrides,
            deadline_s=deadline_s, window=window, source=source)
        mode_rows.append(row)
        configs[mode] = _mode_config(resolved, mode, overrides)["config"]
    base_mode = modes[0]
    fairness = {"rows_digest": rows_digest, "seed": resolved["config"][
        "scenario"]["seed"], "base_mode": base_mode, "diffs": {}}
    for mode in modes[1:]:
        fairness["diffs"][mode] = _config_diff(configs[base_mode],
                                               configs[mode])
    return {
        "schema": SCHEMA,
        "identity": artifact_identity.build_identity(
            config=resolved, trace_digest=source.get("trace_sha256"),
            driver_paths=artifact_identity.execution_chain_paths(),
            extra={"modes": list(modes)}),
        "source": dict(source, rows_digest=rows_digest,
                       base_config_sha256=resolved["sha256"],
                       declared_overrides=dict(overrides or {})),
        "fairness": fairness,
        "deadline": {"deadline_s": (None if deadline_s is None
                                      else float(deadline_s)),
                     "population_window_s": (list(window)
                                             if isinstance(window, tuple)
                                             else window),
                     "rule": "one frozen D and population window shared by all five modes"},
        "ddqn": ddqn_status(resolved),
        "modes": mode_rows,
        "units": {"e2e": "seconds", "compute_wait": "seconds",
                  "counts": "events",
                  "outcome": {"schema":
                              mode_rows[0]["outcome_document"]["schema"],
                              "service": "seconds", "queue_wait": "seconds",
                              "queue_area": "bits_s", "rate": "1/s",
                              "bits": "bits", "counts": "counts"}},
        "limits": [
            "the comparison is one trace, one seed, one arm and one predictor: "
            "no statistical statement is made",
            "a mode that reuses a result (per_flow hit, async table, "
            "precomputed table) skips that decision scoring by construction; "
            "that is the mechanism under comparison, not an optimisation",
            "model calls, schedule queries and fallbacks are reported "
            "separately and are never summed into one number",
            "the deterministic scorer is used; DDQN is reported only as "
            "availability/blocker, never as a performance result",
            "each mode row carries network_outcome (final delivered/lost/"
            "censored split, both throughput denominators, E2E percentiles, "
            "per-satellite pressure incl. the hotspot maximum, table and "
            "prediction quality) and total_cost (per-packet decision compute "
            "vs background update compute, query service, install and control "
            "traffic as separate lines); a quantity whose raw field the run "
            "did not produce is reported NOT_COMPUTABLE with the field named, "
            "never as 0",
            "the measurement window is derived from the trace rows' emission "
            "times and carried on every row, because every window-scoped "
            "number is uninterpretable without it",
        ],
    }


def _design(config_path, scenario, root):
    if scenario:
        resolved, rows, geometry, meta = scripted_scenarios.build(scenario)
        return resolved, rows, geometry, {"scenario": scenario, "config": None,
                                          "trace_sha256": None,
                                          "rows": len(rows)}
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise ExecutionCompareError(f"config invalid: {exc}") from exc
    work = Path(tempfile.mkdtemp(prefix="execcmp-", dir=str(root)))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=resolved["config"]["execution"]["max_packets"])
        digest = (manifest.get("__trace_sha256")
                  or manifest.get("trace_sha256"))
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
    return resolved, rows, None, {"scenario": "config",
                                 "config": str(config_path),
                                 "trace_sha256": digest, "rows": len(rows)}


def publish(document, out):
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise ExecutionCompareError(f"output destination exists: {out}")
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise ExecutionCompareError(
            f"output parent must be a real directory: {out.parent}")
    handle, temporary = tempfile.mkstemp(prefix="." + out.name + ".",
                                         suffix=".tmp", dir=str(out.parent))
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Five execution modes on one fair trace")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scenario", choices=sorted(scripted_scenarios.SCENARIOS))
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--compute-servers", type=int, default=None,
                        help="finite compute pool size for every mode")
    parser.add_argument("--service-s", type=float, default=None,
                        help="compute service time for every mode")
    parser.add_argument("--packet-bits", type=int, default=None,
                        help="packet size for every mode")
    parser.add_argument("--per-flow-ttl-s", type=float, default=None)
    parser.add_argument("--update-interval-s", type=float, default=None)
    parser.add_argument("--window-bins", type=int, default=None)
    parser.add_argument("--query-delay-s", type=float, default=None,
                        help="shared per-satellite query service time")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.scenario):
        print("EXECCOMPARE REFUSED: give exactly one of --config or --scenario")
        return 2
    modes = tuple(m.strip() for m in str(args.modes).split(",") if m.strip())
    overrides = {}
    if args.compute_servers is not None:
        overrides.setdefault("execution", {})[
            "compute_servers_per_satellite"] = int(args.compute_servers)
    if args.service_s is not None:
        overrides.setdefault("execution", {})["compute_delay_s"] = float(
            args.service_s)
    if args.packet_bits is not None:
        overrides.setdefault("demand", {})["packet_bits"] = int(
            args.packet_bits)
    if args.per_flow_ttl_s is not None:
        overrides.setdefault("time_alignment", {})["per_flow_ttl_s"] = float(
            args.per_flow_ttl_s)
    if args.update_interval_s is not None:
        overrides.setdefault("async_routing", {})[
            "update_interval_s"] = float(args.update_interval_s)
    if args.window_bins is not None:
        overrides.setdefault("async_routing", {})[
            "window_bins"] = int(args.window_bins)
    if args.query_delay_s is not None:
        overrides.setdefault("time_alignment", {})[
            "query_delay_s"] = float(args.query_delay_s)
    try:
        resolved, rows, geometry, source = _design(args.config, args.scenario,
                                                   args.root)
        document = compare(resolved, rows, geometry, source, modes=modes,
                           overrides=overrides)
        publish(document, args.out)
    except ExecutionCompareError as exc:
        print(f"EXECCOMPARE REFUSED: {exc}")
        return 2
    print(json.dumps({
        "status": "compared", "out": str(args.out),
        "rows_digest": document["fairness"]["rows_digest"][:16],
        "ddqn": document["ddqn"]["state"],
        "modes": [{"mode": r["mode"],
                   "delivered": r["outcome"]["delivered"],
                   "compute_requests": r["compute"]["decision_requests"],
                   "cache_hits": r["reuse"]["per_flow_cache_hits"],
                   "queries": r["reuse"]["schedule_queries"],
                   "installs": r["reuse"]["schedule_installs"],
                   "bins": r["reuse"]["bins"]} for r in document["modes"]],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
