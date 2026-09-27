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

from CODE.experiment_platform import artifact_identity, scripted_scenarios
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
    """Whether a fixed-inference checkpoint exists; never a performance claim."""
    cfg = resolved["config"]["learning"]
    details = _failure_details(resolved)
    if cfg["algorithm"] == "none":
        return {"state": "NOT_REQUESTED", "details": details,
                "note": "the matrix runs the deterministic scorer"}
    path = cfg["checkpoint_path"]
    if path is None:
        return {"state": "EXTERNAL_BLOCKER", "details": details,
                "reason": "learning.algorithm is set but no checkpoint_path is "
                          "configured, so fixed-inference reasoning cannot be "
                          "validated with a model",
                "recovery": "provide a trained checkpoint plus its sha256"}
    if not Path(path).exists():
        return {"state": "EXTERNAL_BLOCKER", "details": details,
                "reason": f"checkpoint_path does not exist: {path}",
                "recovery": "ship the checkpoint or point at the real artifact"}
    return {"state": "AVAILABLE", "details": details,
            "note": "a checkpoint exists; the learner-training boundary is "
                    "still not exercised by this deterministic matrix"}


def _mode_config(resolved, mode):
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
    return config_mod.resolve_config(cfg)


def _row_for_mode(base_resolved, rows, geometry, mode):
    resolved = _mode_config(base_resolved, mode)
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
            "bins": (None if mode not in ASYNC_MODES
                     else (1 if mode == "async_point"
                           else resolved["config"]["async_routing"][
                               "window_bins"])),
        },
        "precompute": result["execution_mode"]["precompute"],
        "outcome": {
            "delivered": len(result["deliveries"]),
            "fate_counts": {k: v for k, v in result["fate_counts"].items() if v},
            "e2e_samples": len(e2e),
            "e2e_mean_s": (None if not e2e else statistics.fmean(e2e)),
            "e2e_p95_s": _p95(e2e),
            "events_processed": result["events_processed"],
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


def compare(resolved, rows, geometry, source, modes=MODES):
    rows_digest = _rows_digest(rows)
    mode_rows = []
    configs = {}
    for mode in modes:
        if mode not in MODES:
            raise ExecutionCompareError(f"unknown execution mode {mode!r}")
        row = _row_for_mode(resolved, rows, geometry, mode)
        mode_rows.append(row)
        configs[mode] = _mode_config(resolved, mode)["config"]
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
                       base_config_sha256=resolved["sha256"]),
        "fairness": fairness,
        "ddqn": ddqn_status(resolved),
        "modes": mode_rows,
        "units": {"e2e": "seconds", "compute_wait": "seconds",
                  "counts": "events"},
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
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.scenario):
        print("EXECCOMPARE REFUSED: give exactly one of --config or --scenario")
        return 2
    modes = tuple(m.strip() for m in str(args.modes).split(",") if m.strip())
    try:
        resolved, rows, geometry, source = _design(args.config, args.scenario,
                                                   args.root)
        document = compare(resolved, rows, geometry, source, modes=modes)
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
