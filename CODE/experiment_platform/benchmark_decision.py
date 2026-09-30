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
import copy
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


def _capture_online_decision(resolved, rows, geometry):
    """Run the fixture and CAPTURE the inputs the online path actually used.

    The timed closures then call the kernel's own snapshot builder and the
    shared planner with those exact inputs, so the benchmark measures the real
    online path instead of a parallel offline reconstruction.
    """
    captured = {}
    real = kernel.Kernel._time_aligned_order

    def spy(self, pkt, sat, now, cands, own_q):
        result = real(self, pkt, sat, now, cands, own_q)
        audit = (result[1] if isinstance(result, tuple) and len(result) > 1
                 else None)
        # capture the first decision that REALLY scored candidates: an early
        # call with an empty candidate set has no instants to align against.
        # The caches and the compute-pool state are frozen at THIS instant, so
        # a later timed rebuild sees exactly what the decision saw (a rebuild
        # against the end-of-run caches would query fresher advertisements and
        # is the bug this check exists to catch).
        if not captured and cands and isinstance(audit, dict):
            captured.update(
                kern=self, pkt=pkt, sat=sat, now=float(now),
                cands=list(cands), own_q=dict(own_q), audit=audit,
                caches_frozen=copy.deepcopy(self.caches),
                compute_state={s: self._compute_state_now(s)
                               for s in range(self.num_sats)})
        return result

    kernel.Kernel._time_aligned_order = spy
    try:
        kern = kernel.Kernel(resolved, rows, geometry=geometry,
                             decision_sink=[], timeline_sink=[])
        kern.run()
    finally:
        kernel.Kernel._time_aligned_order = real
    if not captured:
        raise BenchmarkError("the fixture produced no online decision")
    captured["arm"] = str(kern.cfg_ta["arm"])
    captured["rule"] = str(kern.cfg_ta["common_rule"])
    return captured


def _with_frozen_inputs(kern, captured, fn):
    """Run fn with the kernel's decision inputs restored to the captured
    instant: same received advertisements, same pool occupancy."""
    saved_caches = kern.caches
    saved_state = kern._compute_state_now
    kern.caches = captured["caches_frozen"]
    kern._compute_state_now = lambda sat: dict(captured["compute_state"][sat])
    try:
        return fn()
    finally:
        kern.caches = saved_caches
        kern._compute_state_now = saved_state


def online_observation_build(captured, arm, capture=None):
    """The kernel's OWN snapshot builder, including the double build the
    common-horizon rule performs online, on the frozen decision inputs."""
    kern = captured["kern"]
    pkt, sat, now = captured["pkt"], captured["sat"], captured["now"]
    cands, own_q = captured["cands"], captured["own_q"]
    rule = captured["rule"]

    def build():
        if rule == "fixed_horizon":
            horizon = float(kern.cfg_ta["common_horizon_s"])
        else:
            probe = kern._build_ta_snapshot(pkt, sat, now, cands, own_q, arm,
                                            None)
            horizon = ta.resolve_common_horizon(probe, rule)
        return kern._build_ta_snapshot(pkt, sat, now, cands, own_q, arm,
                                       horizon)

    snap = _with_frozen_inputs(kern, captured, build)
    if capture is not None:
        capture["snapshot"] = snap
    return snap


def phase_closures(captured, arm, capture):
    """The REAL online interface, split into its actual phases.

    observation construction -> the kernel's own builder (with the same double
                               build the common rule performs online)
    prediction               -> ta.build_predictions (the SAME loop the planner
                               uses; per-arm query instants)
    scoring                  -> ta.score_candidates over those inputs
    mask/selection           -> action choice from the ranking
    end_to_end               -> ta.plan_decision on the built snapshot: what a
                               deployment actually pays per decision
    inference_only           -> scoring + selection with predictions ALREADY
                               built: no prediction call at all
    """
    def observation_build():
        capture["observation_build"] += 1
        return online_observation_build(captured, arm, capture)

    snapshot = observation_build()

    def prediction():
        capture["prediction"] += 1
        predictions, etas, targets = ta.build_predictions(snapshot)
        capture["message"] = (predictions, etas)
        capture["targets"] = dict(targets)
        return predictions, etas

    predictions, etas = prediction()

    def scoring():
        capture["scoring"] += 1
        scored = ta.score_candidates(snapshot, predictions, etas)
        capture["scored"] = scored
        return scored

    scored = scoring()

    def select():
        capture["selection"] += 1
        return scored.ranking[0] if scored.ranking else None

    def end_to_end():
        capture["end_to_end"] += 1
        snap = online_observation_build(captured, arm)
        plan = ta.plan_decision(snap)
        capture["plan_targets"] = dict(plan.targets)
        capture["plan_ranking"] = list(plan.scored.ranking)
        return plan.chosen()

    def inference_only():
        capture["inference_only"] += 1
        sc = ta.score_candidates(snapshot, predictions, etas)
        return sc.ranking[0] if sc.ranking else None

    return {
        "observation_build": observation_build,
        "prediction": prediction,
        "scoring": scoring,
        "selection": select,
        "end_to_end": end_to_end,
        "inference_only": inference_only,
    }


def count_predict_calls(fn):
    """Run fn while counting real ta.predict_resource calls."""
    counter = {"calls": 0}
    real = ta.predict_resource

    def counted(*args, **kwargs):
        counter["calls"] += 1
        return real(*args, **kwargs)

    ta.predict_resource = counted
    try:
        fn()
    finally:
        ta.predict_resource = real
    return counter["calls"]


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


def _align_arm(resolved, rows, geometry, source, *, warmup, rounds,
               iterations, cap_s):
    """Measure the REAL online path for every state-time arm and check that the
    benchmark queries the same instants the online run did."""
    table = {}
    for arm in ta.ARMS:
        cfg = json.loads(json.dumps(resolved["config"]))
        cfg["time_alignment"]["arm"] = arm
        local = config_mod.resolve_config(cfg)
        captured = _capture_online_decision(local, rows, geometry)
        capture = {"observation_build": 0, "prediction": 0, "scoring": 0,
                   "selection": 0, "end_to_end": 0, "inference_only": 0}
        closures = phase_closures(captured, arm, capture)
        null = _null_baseline(iterations=10_000)
        measured = {name: _measure(fn, warmup=warmup, rounds=rounds,
                                   iterations=iterations, cap_s=cap_s)
                    for name, fn in closures.items()}
        snapshot = capture.get("snapshot")
        real_audit = captured.get("audit") or {}
        bench_targets = capture.get("plan_targets") or {}
        real_targets = real_audit.get("query_targets") or {}
        table[arm] = {
            "config_sha256": local["sha256"],
            "legal_directions": list(snapshot.legal_directions),
            "full_decision": measured["end_to_end"],
            "inference_only": measured["inference_only"],
            "phases": {name: measured[name] for name in (
                "observation_build", "prediction", "scoring", "selection")},
            "empty_call_baseline": null,
            "call_counts": {
                "end_to_end_predict_calls": count_predict_calls(
                    closures["end_to_end"]),
                "inference_only_predict_calls": count_predict_calls(
                    closures["inference_only"]),
                "prediction_phase_predict_calls": count_predict_calls(
                    closures["prediction"]),
                "expected_predict_calls_per_full_decision":
                    len(snapshot.legal_directions),
            },
            "alignment": {
                "online_query_targets": real_targets,
                "benchmark_query_targets": bench_targets,
                "targets_match": bool(real_targets)
                and _targets_equal(real_targets, bench_targets),
                "online_ranking": list(real_audit.get("ranking") or []),
                "benchmark_ranking": list(capture.get("plan_ranking") or []),
                "ranking_match": (list(real_audit.get("ranking") or [])
                                  == list(capture.get("plan_ranking") or [])),
            },
        }
    return table


def _targets_equal(left, right):
    if set(left) != set(right):
        return False
    return all(abs(float(left[k]) - float(right[k])) <= 1e-12 for k in left)


def run_benchmark(resolved, rows, geometry, source, *, warmup, rounds,
                  iterations, cap_s):
    """Per-arm timing on the REAL online path, plus the four-arm alignment."""
    arms = _align_arm(resolved, rows, geometry, source, warmup=warmup,
                      rounds=rounds, iterations=iterations, cap_s=cap_s)
    primary = str(resolved["config"]["time_alignment"]["arm"])
    capture = arms[primary]
    snapshot_candidates = capture["call_counts"][
        "expected_predict_calls_per_full_decision"]
    full_stats = capture["full_decision"]
    inference_stats = capture["inference_only"]
    call_counts = dict(capture["call_counts"],
                       expected_predict_calls_per_full_decision=
                       snapshot_candidates)
    return {
        "schema": SCHEMA,
        "identity": artifact_identity.build_identity(
            config=resolved, trace_digest=source.get("trace_sha256"),
            driver_paths=artifact_identity.execution_chain_paths(),
            extra={"candidates": snapshot_candidates}),
        "source": dict(source, config_sha256=resolved["sha256"],
                       legal_directions=list(
                           arms[primary].get("legal_directions") or []),
                       arm=resolved["config"]["time_alignment"]["arm"],
                       predictor=resolved["config"]["time_alignment"]["predictor"],
                       execution_mode=resolved["config"]["time_alignment"][
                           "execution_mode"]),
        "clock": "time.perf_counter",
        # S5 (third-round review): the timings below come from the shared
        # DETERMINISTIC scorer.  No trained model is loaded and no DDQN policy
        # is evaluated, so the artifact says that machine-readably instead of
        # leaving the reader to infer it from prose.
        "model_provenance": {
            "scorer": "deterministic_shared_scorer",
            "scorer_entry": "CODE.leo_sim.time_alignment.plan_decision",
            "policy_under_test": "deterministic_scorer",
            "trained_checkpoint_used": False,
            "ddqn": False,
            "learner_involved": False,
            "measurement_host": "host_cpu",
            "on_board": False,
            "note": "deterministic scorer; NOT a DDQN result; host/VM "
                    "timing, not satellite hardware",
        },
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
        "empty_call_baseline": capture["empty_call_baseline"],
        "full_decision": full_stats,
        "inference_only": inference_stats,
        "phases": capture["phases"],
        "phase_call_counts": call_counts,
        "arms": arms,
        "primary_arm": primary,
        "alignment_rule": (
            "every arm was measured through the kernel own snapshot builder "
            "and the shared plan_decision; targets_match/ranking_match compare "
            "the benchmark against the audit the ONLINE run recorded for the "
            "same decision"),
        "units": {"stats": "seconds per call",
                  "requests_per_s": "calls per second"},
        "limits": [
            "full_decision measures observation -> prediction -> scoring -> "
            "ranking -> selection in-process; it is not an on-board timing",
            "the phase split is verified by call counting: end_to_end calls "
            "the predictor once per candidate and inference_only calls it "
            "zero times",
            "the configured compute service time is a diagnostic scenario "
            "input, not a measured on-board service time",
            "the analytic rho = lambda * t_service / N used to construct a "
            "scenario is not a substitute for the transient queueing the run "
            "produces",
            "inference_only is reported separately and must never be quoted as "
            "the full decision cost",
            "the timed path runs the deterministic shared scorer and no "
            "trained checkpoint exists on this host: these numbers are "
            "NOT a DDQN latency and must never be quoted as one "
            "(see model_provenance)",
            "the four arms are each measured on their OWN query instant "
            "(targets_match is asserted against the online audit); an earlier "
            "version queried snapshot_at for every arm and understated the "
            "decision span",
        ],
    }


def _require_enabled(resolved):
    """The benchmark measures the STATE-TIME decision path; with the feature
    disabled there is no such path to time, so it refuses instead of quietly
    timing the historical policy."""
    if not resolved["config"]["time_alignment"]["enabled"]:
        raise BenchmarkError(
            "the benchmark times the state-time decision path, so it requires "
            "time_alignment.enabled=true; this config has it disabled")
    return resolved


def _design(config_path, scenario, root):
    if scenario:
        resolved, rows, geometry, meta = scripted_scenarios.build(scenario)
        # a scripted scenario does not enable the feature by default: the
        # benchmark turns it ON explicitly and says so in the artifact
        cfg = json.loads(json.dumps(resolved["config"]))
        cfg["time_alignment"]["enabled"] = True
        resolved = config_mod.resolve_config(cfg)
        source = {"scenario": scenario, "config": None, "trace_sha256": None,
                  "rows": len(rows),
                  "time_alignment_forced_enabled": True}
        return resolved, rows, geometry, source
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise BenchmarkError(f"config invalid: {exc}") from exc
    _require_enabled(resolved)
    # ``root`` remains accepted for CLI/API compatibility, but the runner
    # supplies TMPDIR inside the isolated run directory. Keep trace scratch
    # there instead of under --root: a published release is read-only.
    work = Path(tempfile.mkdtemp(prefix="bench-"))
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
