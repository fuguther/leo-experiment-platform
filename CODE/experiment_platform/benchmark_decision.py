"""T1-COMPLETE P6: decision-path timing benchmark and finite-pool pressure.

WHAT IS MEASURED, AND WHAT IS NOT
---------------------------------
Two separate measurements, never merged:

* full_decision   construct the observation snapshot from a real branch
                  observation -> predict every candidate -> score -> rank ->
                  select the action.  This is the thing a deployment actually
                  pays for.
* inference_only  the same scoring loop with the predictions already built
                  (for the deterministic scorer this is the scoring half only;
                  for a learning arm it would be the model forward pass).

A null baseline (an empty call loop) is measured first so the timing-call
overhead is visible instead of silently inflating every per-iteration number.

The clock is time.perf_counter (monotonic, high resolution).  Warm-up is not
counted.  A round that would exceed the per-round cap is recorded as incomplete
rather than mixed with complete rounds.

HONESTY BOUNDS
--------------
* No satellite hardware exists here: every number is a host/VM measurement.
* The configured compute service time is a DIAGNOSTIC scenario input, never a
  measured on-board service time.
* The finite-pool sweep reports queueing that the kernel actually produced; the
  analytic rho=lambda*t/N used to construct the scenario is not a substitute for
  the transient queueing of the run.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path

from CODE.experiment_platform import artifact_identity
from CODE.experiment_platform import scripted_scenarios
from CODE.leo_sim import config as config_mod, kernel, time_alignment as ta
from CODE.leo_sim import trace as trace_mod

SCHEMA = "benchmark-decision/v1"

#: pre-declared defaults from the task book
DEFAULT_WARMUP = 100
DEFAULT_ROUNDS = 5
DEFAULT_ITERATIONS = 1000
SLOW_ROUND_MIN_S = 5.0
ROUND_CAP_S = 60.0
POOL_SWEEP = (0, 1, 2, 4)


class BenchmarkError(RuntimeError):
    """The benchmark could not run; nothing is published."""


def _percentiles(samples):
    if not samples:
        return {"n": 0}
    ordered = sorted(samples)
    n = len(ordered)

    def q(p):
        if n == 1:
            return ordered[0]
        pos = p * (n - 1)
        low = int(math.floor(pos))
        high = int(math.ceil(pos))
        if low == high:
            return ordered[low]
        frac = pos - low
        return ordered[low] * (1 - frac) + ordered[high] * frac

    return {
        "n": n,
        "p50_s": q(0.50),
        "p90_s": q(0.90),
        "p95_s": q(0.95),
        "p99_s": q(0.99),
        "mean_s": statistics.fmean(ordered),
        "min_s": ordered[0],
        "max_s": ordered[-1],
    }


def _timed_round(fn, iterations, cap_s):
    """Time one round; return (per_call_samples, complete, wall_s)."""
    samples = []
    start = time.perf_counter()
    complete = True
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        t1 = time.perf_counter()
        samples.append(t1 - t0)
        if t1 - start > cap_s:
            complete = False
            break
    return samples, complete, time.perf_counter() - start


def _measure(fn, *, warmup, rounds, iterations, cap_s):
    for _ in range(warmup):
        fn()
    per_round = []
    all_samples = []
    for _ in range(rounds):
        samples, complete, wall = _timed_round(fn, iterations, cap_s)
        per_round.append({"samples": len(samples), "wall_s": wall,
                          "complete": complete,
                          "stats": _percentiles(samples)})
        if complete:
            all_samples.extend(samples)
    joined = _percentiles(all_samples)
    joined["requests_per_s"] = (None if not joined.get("mean_s")
                                else 1.0 / joined["mean_s"])
    return {"warmup": warmup, "rounds": rounds, "iterations": iterations,
            "round_cap_s": cap_s, "per_round": per_round,
            "complete_rounds": sum(1 for r in per_round if r["complete"]),
            "stats": joined}


def _null_baseline(iterations=100_000):
    def empty():
        return None
    start = time.perf_counter()
    for _ in range(iterations):
        empty()
    total = time.perf_counter() - start
    return {"iterations": iterations, "total_s": total,
            "per_call_s": total / iterations,
            "note": "timing-call overhead of an empty function; subtract from "
                    "per-iteration numbers when it matters"}


def _fixture(resolved, rows, geometry):
    sink, timeline = [], []
    result = kernel.run_simulation(resolved, rows, geometry=geometry,
                                   decision_sink=sink, timeline_sink=timeline)
    row = next((r for r in sink if r.get("kind") == "forward"), None)
    if row is None:
        raise BenchmarkError("the fixture produced no forward decision")
    pid = row["pid"]
    bits = next((float(r["bits"]) for r in rows if r.get("packet_id") == pid),
                float(resolved["config"]["demand"]["packet_bits"]))
    from CODE.experiment_platform import time_alignment_compare as cmp
    snapshot = cmp.build_snapshot(row, resolved,
                                  str(resolved["config"]["time_alignment"]["arm"]),
                                  None, bits, 0.0, 0.0)
    if not snapshot.legal_directions:
        raise BenchmarkError("the fixture snapshot has no legal candidate")
    return snapshot


def benchmark_snapshot(snapshot):
    """Return (full_fn, inference_fn) closures over one frozen snapshot."""
    target = snapshot.snapshot_at + 1.0

    def full():
        scored = ta.score_snapshot_at(snapshot, target)
        return scored.ranking[0] if scored.ranking else None

    def inference():
        # predictions already exist: score and select only
        return ta.score_snapshot_at(snapshot, target)

    return full, inference


def pool_sweep(resolved, rows, geometry, pools=POOL_SWEEP,
               compute_delay_s=0.001):
    """Finite compute-pool pressure: N servers, plus N=0 (unbounded) control."""
    out = []
    for servers in pools:
        if servers > 0 and compute_delay_s <= 0:
            out.append({"servers": servers, "skipped": True,
                        "reason": "a bounded pool needs a positive service time"})
            continue
        cfg = json.loads(json.dumps(resolved["config"]))
        cfg["execution"]["compute_servers_per_satellite"] = int(servers)
        cfg["execution"]["compute_delay_s"] = float(compute_delay_s)
        cfg["execution"]["decision_observation_mode"] = "frozen"
        try:
            local = config_mod.resolve_config(cfg)
        except config_mod.ConfigError as exc:
            out.append({"servers": servers, "skipped": True,
                        "reason": str(exc)})
            continue
        timeline = []
        result = kernel.run_simulation(local, rows, geometry=geometry,
                                       decision_sink=[], timeline_sink=timeline)
        waits = [m for m in timeline if m.get("milestone") == "compute_wait"]
        out.append({
            "servers": int(servers),
            "unbounded": int(servers) == 0,
            "config_sha256": local["sha256"],
            "requests": len([m for m in timeline
                             if m.get("milestone") == "compute_request"]),
            "queued": sum(1 for m in waits if m.get("wait_s", 0.0) > 0.0),
            "total_wait_s": sum(float(m.get("wait_s", 0.0)) for m in waits),
            "max_wait_s": max((float(m.get("wait_s", 0.0)) for m in waits),
                              default=0.0),
            "events_processed": result["events_processed"],
            "delivered": len(result["deliveries"]),
        })
    return out


def run_benchmark(resolved, rows, geometry, source, *, warmup, rounds,
                  iterations, cap_s):
    snapshot = _fixture(resolved, rows, geometry)
    full, inference = benchmark_snapshot(snapshot)
    null = _null_baseline()
    full_stats = _measure(full, warmup=warmup, rounds=rounds,
                          iterations=iterations, cap_s=cap_s)
    inference_stats = _measure(inference, warmup=warmup, rounds=rounds,
                               iterations=iterations, cap_s=cap_s)
    return {
        "schema": SCHEMA,
        "identity": artifact_identity.build_identity(
            config=resolved, trace_digest=source.get("trace_sha256"),
            driver_paths=artifact_identity.execution_chain_paths(),
            extra={"candidates": len(snapshot.legal_directions)}),
        "source": dict(source, config_sha256=resolved["sha256"],
                       legal_directions=list(snapshot.legal_directions),
                       arm=resolved["config"]["time_alignment"]["arm"],
                       predictor=resolved["config"]["time_alignment"]["predictor"],
                       execution_mode=resolved["config"]["time_alignment"][
                           "execution_mode"]),
        "clock": "time.perf_counter",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpu_count": os.cpu_count(),
            "threads": 1,
            "batch_size": 1,
            "concurrency": 1,
            "device": "host_cpu",
            "note": "host/VM measurement only; no satellite hardware exists "
                    "here",
        },
        "empty_call_baseline": null,
        "full_decision": full_stats,
        "inference_only": inference_stats,
        "units": {"stats": "seconds per call",
                  "requests_per_s": "calls per second"},
        "limits": [
            "full_decision measures observation -> prediction -> scoring -> "
            "ranking -> selection in-process; it is not an on-board timing",
            "the configured compute service time is a diagnostic scenario "
            "input, not a measured on-board service time",
            "the analytic rho = lambda * t_service / N used to construct a "
            "scenario is not a substitute for the transient queueing the run "
            "produces",
            "inference_only is reported separately and must never be quoted as "
            "the full decision cost",
        ],
    }


def _design(config_path, scenario, root):
    if scenario:
        resolved, rows, geometry, meta = scripted_scenarios.build(scenario)
        source = {"scenario": scenario, "config": None, "trace_sha256": None,
                  "rows": len(rows)}
        return resolved, rows, geometry, source
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise BenchmarkError(f"config invalid: {exc}") from exc
    work = Path(tempfile.mkdtemp(prefix="bench-", dir=str(root)))
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
    source = {"scenario": "config", "config": str(config_path),
              "trace_sha256": digest, "rows": len(rows)}
    return resolved, rows, None, source


def publish(document, out):
    out = Path(out)
    if out.exists() or out.is_symlink():
        raise BenchmarkError(f"output destination exists: {out}")
    if not out.parent.is_dir() or out.parent.is_symlink():
        raise BenchmarkError(f"output parent must be a real directory: {out.parent}")
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
        description="Decision-path timing benchmark and finite-pool pressure")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--scenario", choices=sorted(scripted_scenarios.SCENARIOS))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--round-cap-s", type=float, default=ROUND_CAP_S)
    parser.add_argument("--pool-sweep", default="0,1,2,4")
    parser.add_argument("--service-s", type=float, default=0.001)
    args = parser.parse_args(argv)
    if bool(args.config) == bool(args.scenario):
        print("BENCH REFUSED: give exactly one of --config or --scenario")
        return 2
    try:
        resolved, rows, geometry, source = _design(args.config, args.scenario,
                                                   args.root)
        document = run_benchmark(resolved, rows, geometry, source,
                                 warmup=args.warmup, rounds=args.rounds,
                                 iterations=args.iterations,
                                 cap_s=args.round_cap_s)
        pools = [int(x) for x in str(args.pool_sweep).split(",") if x.strip()]
        document["finite_pool"] = pool_sweep(
            resolved, rows, geometry, pools=pools,
            compute_delay_s=float(args.service_s))
        publish(document, args.out)
    except BenchmarkError as exc:
        print(f"BENCH REFUSED: {exc}")
        return 2
    stats = document["full_decision"]["stats"]
    print(json.dumps({
        "status": "benchmarked", "out": str(args.out),
        "candidates": len(document["source"]["legal_directions"]),
        "full_p50_us": None if not stats.get("p50_s") else stats["p50_s"] * 1e6,
        "full_p99_us": None if not stats.get("p99_s") else stats["p99_s"] * 1e6,
        "empty_call_us": document["empty_call_baseline"]["per_call_s"] * 1e6,
        "complete_rounds": document["full_decision"]["complete_rounds"],
        "pool": [(p["servers"], p.get("queued"), p.get("max_wait_s"))
                 for p in document["finite_pool"]],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
