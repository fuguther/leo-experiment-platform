"""Multi-process parallel batch execution for ``CODE.leo_sim``.

Spawns isolated worker processes across CPU cores (respecting cgroup quotas)
with per-worker TensorFlow thread caps so multi-seed or multi-config sweeps
scale cleanly on multi-core hosts without thread-pool thrashing.
"""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import re
import sys
import time
from pathlib import Path

from . import config as config_mod
from . import kernel, learning, receipt as receipt_mod, trace as trace_mod


def compile_run_trace(resolved: dict, out_dir: str) -> tuple[dict, bytes, list[dict]]:
    """Compile trace.csv + manifest.json and attach digest metadata for receipting."""
    evidence: dict = {}
    manifest = trace_mod.compile_trace(resolved, out_dir, evidence=evidence)
    trace_bytes = (Path(out_dir) / "trace.csv").read_bytes()
    manifest_bytes = (Path(out_dir) / "manifest.json").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
    manifest["__materialization"] = evidence["materialization"]
    rows = trace_mod.load_trace(
        str(Path(out_dir) / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"],
    )
    return manifest, trace_bytes, rows


def _batch_worker_run(job: dict) -> dict:
    """Execute one standalone simulation + receipt verification in a worker process."""
    cfg_path = Path(job["config_path"])
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = config_mod.load_config_file(str(cfg_path))["config"]
    if raw["outputs"].get("trace_path"):
        raise config_mod.ConfigError(
            "batch runner requires fresh per-job compilation (outputs.trace_path must be unset)")
    overrides = {"outputs": {"out_dir": str(out_dir)}}
    if job.get("seed") is not None:
        overrides["scenario"] = {"seed": int(job["seed"])}
    resolved = config_mod.resolve_config(raw, overrides=overrides)
    started = time.perf_counter()
    manifest, trace_bytes, rows = compile_run_trace(resolved, str(out_dir))
    alg = resolved["config"]["learning"]["algorithm"]
    learn_dir = (out_dir / alg) if alg in ("ddqn", "qlearning") else None
    result = kernel.run_simulation(resolved, rows, learning_out_dir=learn_dir)
    rcp = receipt_mod.write_run(
        str(out_dir), resolved, trace_bytes, manifest, result, rows)
    verify_errors = receipt_mod.verify_receipt_dir(str(out_dir))
    wall_s = time.perf_counter() - started
    return {
        "name": job["name"],
        "config_path": str(cfg_path),
        "seed": resolved["config"]["scenario"]["seed"],
        "out_dir": str(out_dir),
        "wall_seconds": wall_s,
        "natural_end": bool(rcp["natural_end"]),
        "conservation_ok": bool(rcp["conservation_ok"]),
        "receipt_verified": not verify_errors,
        "receipt_errors": verify_errors,
        "fate_counts": rcp["fate_counts"],
        "config_sha256": resolved["sha256"],
        "trace_sha256": manifest["__trace_sha256"],
    }


def run_batch(
    out_root: str | Path,
    *,
    configs: list[str] | None = None,
    config_path: str | None = None,
    seeds: list[int] | None = None,
    workers: int | None = None,
    threads_per_worker: int | None = None,
) -> dict:
    """Build batch jobs, run across spawned workers, and return summary dict."""
    root = Path(out_root)
    if config_path and configs:
        raise ValueError("pass either config_path/seeds or configs, not both")
    jobs: list[dict] = []
    if configs:
        for i, raw_path in enumerate(configs):
            p = Path(raw_path).resolve()
            stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", p.stem) or f"run_{i}"
            name = f"{i:02d}_{stem}"
            jobs.append({
                "name": name,
                "config_path": str(p),
                "seed": None,
                "out_dir": str(root / name),
            })
    elif config_path:
        p = Path(config_path).resolve()
        seed_list = [int(s) for s in seeds] if seeds else [None]
        for i, seed in enumerate(seed_list):
            label = f"seed_{seed}" if seed is not None else f"run_{i}"
            jobs.append({
                "name": label,
                "config_path": str(p),
                "seed": seed,
                "out_dir": str(root / label),
            })
    else:
        raise ValueError("pass configs or config_path")

    cpus = learning._effective_cpu_count()
    n_workers = int(workers) if workers is not None else min(len(jobs), cpus)
    n_workers = max(1, min(n_workers, len(jobs)))
    if n_workers > 1:
        entrypoint = getattr(sys.modules.get("__main__"), "__file__", None)
        if not isinstance(entrypoint, str) or not Path(entrypoint).is_file():
            raise ValueError(
                "multi-worker batch requires a file entrypoint or "
                "'python -m CODE.leo_sim batch'")
    threads = (
        int(threads_per_worker)
        if threads_per_worker is not None
        else max(1, cpus // n_workers)
    )
    if threads < 1:
        raise ValueError("threads_per_worker must be >= 1")

    root.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    env_keys = ("OMP_NUM_THREADS", "TF_NUM_INTRAOP_THREADS", "TF_NUM_INTEROP_THREADS")
    saved_env = {k: os.environ.get(k) for k in env_keys}
    try:
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ["TF_NUM_INTRAOP_THREADS"] = str(threads)
        os.environ["TF_NUM_INTEROP_THREADS"] = "1"
        if n_workers == 1:
            rows = [_batch_worker_run(job) for job in jobs]
        else:
            ctx = mp.get_context("spawn")
            with ctx.Pool(n_workers) as pool:
                rows = pool.map(_batch_worker_run, jobs)
    finally:
        for k, val in saved_env.items():
            if val is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = val

    wall_s = time.perf_counter() - started
    all_ok = all(r["natural_end"] and r["conservation_ok"] and r["receipt_verified"] for r in rows)
    return {
        "schema": "leo-sim-batch/v1",
        "status": "PASS" if all_ok else "FAIL",
        "effective_cpus": cpus,
        "workers": n_workers,
        "threads_per_worker": threads,
        "wall_seconds": wall_s,
        "runs": rows,
    }
