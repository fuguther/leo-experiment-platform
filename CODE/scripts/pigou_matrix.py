"""Compile and execute the preregistered Pigou development matrix on the T1 VM.

Every simulation and fit is a separately receipted immutable-release job.
Reinvoking a prefix verifies and reuses only byte-matched completed jobs.
Failed attempts are preserved under their own IDs.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait
import fcntl
import json
from pathlib import Path
import re
import shutil
import sys

from CODE.scripts.pigou_study import (
    ALGORITHMS, CADENCES, CORRIDOR_EDGES, LEARNERS, MODULE, TEST_SEEDS, TRAIN_SEEDS,
    VAL_SEEDS, _pinned_env, sha,
)

ROOT = Path("/data/论文/leo-t1-wt")
CALIBRATION_SEEDS = (9001, 9002)
CALIBRATION_LOADS = (82.5, 93.0, 98.0, 100.0)
ABLATIONS = ("no_ext", "mean", "frame", "no_ais", "argmax", "no_shield")
FIT_ROUNDS = ((6, 2000, 5e-4), (6, 1500, 3e-4), (6, 1000, 1e-4))
VARIANTS = {"A": ("full", "mean", "frame", "no_ais"), "B": ("full",)}
BETA_GRID = (0.25, 0.50, 0.75, 1.00)


def describe():
    learning_families = len(LEARNERS) + 1
    arms = len(ALGORITHMS) - learning_families + learning_families * len(TRAIN_SEEDS)
    model_chains = sum(map(len, VARIANTS.values())) * len(TRAIN_SEEDS)
    jobs = {
        "calibration": len(CALIBRATION_LOADS) * len(CALIBRATION_SEEDS) * 2,
        "static_guard_validation": 2 * len(VAL_SEEDS) * 3,
        "hysteresis_plateaus": len(CADENCES) * (2 + 2 * len(TRAIN_SEEDS)),
        "pigou_rollouts": 2 * len(TRAIN_SEEDS) * sum(r[0] for r in FIT_ROUNDS),
        "pigou_fit_jobs": model_chains * len(FIT_ROUNDS),
        "pigou_checkpoint_selection": model_chains * len(FIT_ROUNDS) * len(VAL_SEEDS),
        "pigou_beta_validation": model_chains * len(BETA_GRID) * len(VAL_SEEDS),
        "export_jobs": 3,
        "heldout_shock_diagnostic": 2 * 2 * 3,
        "native_training_cells": 2 * len(LEARNERS) * len(TRAIN_SEEDS) * 8,
        "native_validation_cells": 2 * len(LEARNERS) * len(TRAIN_SEEDS) * 4 * len(VAL_SEEDS),
        "main_evaluation_cells": 2 * len(CADENCES) * len(TEST_SEEDS) * arms,
        "physical_outage": len(TEST_SEEDS) * arms,
        "large_zero_shot": len(TEST_SEEDS) * arms,
        "ablation_comparisons": len(ABLATIONS) * 2 * len(TEST_SEEDS) * len(TRAIN_SEEDS),
        "compute_sensitivity": 3 * len(TEST_SEEDS) * (1 + len(TRAIN_SEEDS)),
    }
    return {**jobs, "total_jobs_excluding_validation_probes": sum(jobs.values()),
        "post_export_onset_censor_s": 3.2,
        "cadence_s": CADENCES,
        "train_seeds": TRAIN_SEEDS, "validation_seeds": VAL_SEEDS, "test_seeds": TEST_SEEDS,
    }


def _params(scene, seed, alg, exp, rate, **options):
    return {"scene": scene, "seed": seed, "alg": alg, "exp": exp,
            "interval": 0.05, "multiplier": 2.5, "offered_mbps": rate, **options}


def _read(path):
    return json.loads(path.read_text())


class Matrix:
    def __init__(self, release, prefix, attempt):
        from CODE.scripts.remote import release_protocol

        self.release = release
        self.prefix = prefix
        self.attempt = attempt
        self.runner = release_protocol
        self.raw = []
        self.models = {}
        self.thresholds = {}
        self.loads = {}
        self.fit_index = []

    def _capacity(self):
        if shutil.disk_usage(ROOT / "runs").free < 45 * 2**30:
            raise RuntimeError("study has less than the reserved 45 GiB disk headroom")
        limits = Path("/sys/fs/cgroup/memory")
        lines = dict(line.split() for line in (limits / "memory.stat").read_text().splitlines())
        rss = int(lines["total_rss"])
        limit = int((limits / "memory.limit_in_bytes").read_text())
        if rss >= 22 * 2**30:
            raise RuntimeError("VM anonymous memory has reached the study's 22 GiB ceiling")
        if limit - rss < 14 * 2**30:
            raise RuntimeError("study has less than the reserved 14 GiB memory headroom")

    def _argv(self, task, spec):
        encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"))
        return ["python3", "-m", MODULE, task, "--spec", encoded]

    def run(self, label, task, spec, inputs=()):
        self._capacity()
        run_root = ROOT / "runs"
        expected = {name: sha(path) for name, path in inputs}
        argv = self._argv(task, spec)
        run_id = f"{self.prefix}-{label}"
        for candidate in (run_id, *[f"{run_id}-r{i}" for i in range(1, self.attempt + 1)]):
            dest = run_root / candidate
            if not dest.exists():
                run_id = candidate
                break
            manifest = _read(dest / "run-manifest.json")
            if (manifest.get("release_id") != self.release or manifest.get("argv", [])[1:] != argv[1:]
                    or {x["name"]: x["sha256"] for x in manifest.get("inputs", [])} != expected):
                raise RuntimeError(f"existing experimental cell has a different contract: {candidate}")
            if manifest["status"] == "completed":
                self.runner.verify_run_directory(dest, expected_run_id=candidate)
                return dest
            if manifest["status"] == "running":
                raise RuntimeError(f"existing experimental cell is still marked running: {candidate}")
        else:
            raise RuntimeError(f"no new retry granted by this attempt for {run_id}")

        value = self.runner.run_release(
            self.release, run_id, mode="development", require_current=False,
            inputs=[f"{name}={path}" for name, path in inputs],
            seed=spec.get("seed"), timeout_seconds=7200, argv=argv)
        print(f"{run_id} {value['status']}", flush=True)
        if value["status"] != "completed":
            raise RuntimeError(f"cell {run_id} ended {value['status']}; inspect its retained logs")
        self.runner.verify_run_directory(run_root / run_id, expected_run_id=run_id)
        return run_root / run_id

    def cell(self, label, spec, *, model=None, resume=None):
        inputs = []
        if model:
            weight, options = model
            spec = {**spec, **options}
            if spec["alg"] in LEARNERS:
                inputs.extend((("online.keras", weight), ("metadata.json", weight.with_name("metadata.json"))))
            else:
                inputs.append(("frozen.npz", weight))
        if resume:
            inputs.append(("resume.tar", resume))
        parent = self.run(label, "cell", spec, inputs)
        result = _read(parent / "cell" / "summary.json")
        result.update(run_id=parent.name, base_trace_sha256=result["background_sha256"],
                      outer_receipt_sha256=sha(parent / "run-receipt.json"))
        return parent, result

    def parallel(self, tasks, workers):
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fn, *args, **kwargs) for fn, args, kwargs in tasks]
            finished, queued = wait(futures, return_when=FIRST_EXCEPTION)
            for future in finished:
                if future.exception() is not None:
                    for pending in queued:
                        pending.cancel()
                    future.result()
            return [future.result() for future in futures]

    def record(self, items):
        self.raw.extend(summary for _, summary in items)

    def calibrate(self):
        items = []
        for alg in ("B1", "B2"):
            for rate in CALIBRATION_LOADS:
                for seed in CALIBRATION_SEEDS:
                    spec = _params("A", seed, alg, "exp0", rate, multiplier=1.0)
                    items.append((self.cell, (f"cal-{alg}-l{int(round(rate*10))}-s{seed}", spec), {}))
        cells = self.parallel(items, 4)
        self.record(cells)
        def loss(rate):
            values = [
                row["steady_utilization_peak"]
                for _, row in cells if row["alg"] == "B1" and row["offered_mbps"] == rate]
            if not values or any(value is None for value in values):
                raise RuntimeError("no capacity-matched steady-load measurements")
            return abs(sum(values) / len(values) - 0.685)
        self.loads.update(A=min(CALIBRATION_LOADS, key=loss), B=85.0)
        print(f"frozen offered Mbps {self.loads}", flush=True)

    def static_guard(self, scene):
        points = []
        for t in (0.3, 0.5, 0.7):
            vals = self.parallel([
                (self.cell, (f"{scene}-guard-t{int(t * 10)}-v{s}", _params(
                    scene, s, "B3", "validation", self.loads[scene], threshold=t)), {})
                for s in VAL_SEEDS], 2)
            points.append((sum(row["val_score"] for _, row in vals), t))
        self.thresholds[scene] = max(points, key=lambda x: (x[0], -x[1]))[1]

    def _native_seed(self, scene, alg, train_seed):
        previous = None
        best = None
        for round_no in range(1, 9):
            scenario_seed = (200_000 if scene == "B" else 100_000) + train_seed * 100 + round_no
            parent, report = self.cell(
                f"{scene}-{alg}-t{train_seed}-r{round_no}",
                _params(scene, scenario_seed, alg, "exp1", self.loads[scene],
                        train_seed=train_seed, epoch=round_no), resume=previous)
            previous = parent / "resume.tar"
            path = Path(report["run_dir"]) / ("primal_cvar" if alg == "B6" else "ddqn") / "online.keras"
            if round_no % 2 == 0:
                vals = [self.cell(f"{scene}-{alg}-t{train_seed}-r{round_no}-v{s}",
                                  _params(scene, s, alg, "validation", self.loads[scene],
                                          train_seed=train_seed), model=(path, {}))
                        for s in VAL_SEEDS]
                score = sum(row["pdr"] for _, row in vals) / len(vals)
                report.update(validation_score=score, checkpoint_sha256=sha(path))
                if best is None or score > best[0]:
                    best = score, path
            self.raw.append(report)
        return alg, train_seed, best[1]

    def native(self, scene):
        tasks = [(self._native_seed, (scene, alg, train_seed), {})
                 for alg in LEARNERS for train_seed in TRAIN_SEEDS]
        for alg, seed, path in self.parallel(tasks, 2):
            self.models[(scene, alg, seed, "full")] = path, {}

    def _pigou_fit(self, scene, train_seed, variant, number, data, previous):
        _, steps, lr = FIT_ROUNDS[number - 1]
        inputs = []
        for i, path in enumerate(data):
            inputs.extend(((f"snap-{i}.npz", path / "cell" / "snaps.npz"),
                           (f"pkt-{i}.npz", path / "cell" / "pkts.npz")))
        if previous:
            inputs.append(("previous.npz", previous))
        parent = self.run(
            f"{scene}-pigou-{variant}-t{train_seed}-f{number}", "fit",
            {"seed": train_seed, "steps": steps, "lr": lr, "epochs": number,
             "variant": variant, "datasets": len(data), "threads": 8}, inputs)
        stats = _read(parent / "fit.json")
        self.fit_index.append({"scene": scene, "train_seed": train_seed, "round": number,
                               "variant": variant, "fit_path": str(parent / "fit.json"),
                               "run_id": parent.name,
                               "outer_receipt_sha256": sha(parent / "run-receipt.json"),
                               "weights_sha256": stats["weights_sha256"]})
        return parent / "frozen.npz"

    def _pigou_validation(self, scene, train_seed, variant, number, model, beta):
        vals = self.parallel([
            (self.cell, (
                f"{scene}-pv-{variant}-t{train_seed}-r{number}-b{round(beta*100)}-v{s}",
                _params(scene, s, "Ours", "validation", self.loads[scene], train_seed=train_seed,
                        variant=variant)),
                {"model": (model, {"beta_ext": beta})})
            for s in VAL_SEEDS], 2)
        return sum(row["val_score"] for _, row in vals) / len(vals)

    def pigou(self, scene):
        arch_variants = VARIANTS[scene]
        for train_seed in TRAIN_SEEDS:
            data = []
            previous = {variant: None for variant in arch_variants}
            best = {variant: None for variant in arch_variants}
            for number, (n, _, _) in enumerate(FIT_ROUNDS, 1):
                rollouts = []
                for i in range(n):
                    seed = (400_000 if scene == "B" else 300_000) + 1000 * train_seed + 100 * number + i
                    model = None if number == 1 else (previous["full"], {})
                    spec = _params(
                        scene, seed, "Collect" if model is None else "Ours",
                        "collection", self.loads[scene], train_seed=train_seed,
                        capture=True, exploration=0.12 if model else (0.05, 0.24, 0.45)[i % 3],
                        temperature=(0.06, 0.12, 0.25)[i % 3] if number < 3 else (0.04, 0.08)[i % 2])
                    rollouts.append((self.cell, (f"{scene}-roll-t{train_seed}-r{number}-e{i}", spec),
                                     {"model": model}))
                data.extend(parent for parent, _ in self.parallel(rollouts, 4))
                for variant in arch_variants:
                    fitted = self._pigou_fit(scene, train_seed, variant, number, data, previous[variant])
                    previous[variant] = fitted
                    score = self._pigou_validation(scene, train_seed, variant, number, fitted, 0.50)
                    if best[variant] is None or score > best[variant][0]:
                        best[variant] = score, fitted
            for variant in arch_variants:
                _, path = best[variant]
                candidates = [(self._pigou_validation(
                    scene, train_seed, variant, 9, path, beta), beta)
                    for beta in BETA_GRID]
                beta = max(candidates, key=lambda point: (point[0], -point[1]))[1]
                self.models[(scene, "Ours", train_seed, variant)] = path, {"beta_ext": beta}

    def _heldout_tasks(self, scene, experiment, intervals, seeds, algorithms=ALGORITHMS,
                       **options):
        variant = options.get("variant", "full")
        for alg in algorithms:
            learning = alg in LEARNERS or alg == "Ours"
            for train_seed in TRAIN_SEEDS if learning else (None,):
                selected = self.models.get((scene, alg, train_seed,
                    variant if variant in {"full", "mean", "frame", "no_ais"} else "full"))
                if learning and selected is None:
                    raise RuntimeError(f"no frozen model for {scene} {alg} {train_seed}")
                for interval in intervals:
                    for seed in seeds:
                        spec = _params(scene, seed, alg, experiment, self.loads[scene],
                                       interval=interval, train_seed=train_seed, **options)
                        if variant == "no_ext" or variant == "argmax" or variant == "no_shield":
                            path, knobs = selected
                            override = {"no_ext": {"beta_ext": 0.0},
                                        "argmax": {"argmax": True},
                                        "no_shield": {"shield": False}}[variant]
                            use_model = path, {**knobs, **override}
                        else:
                            use_model = selected
                        if alg == "B3":
                            spec["threshold"] = self.thresholds[scene]
                        if scene == "A" and experiment == "exp2" and seed == 101 and alg == "Ours":
                            spec["telemetry_sat"] = CORRIDOR_EDGES[0][0]
                        tag = options.get("tag", "main")
                        label = f"{scene}-{experiment.replace('_','-')}-{alg}-{tag}-t{train_seed}-p{int(interval*1000)}-s{seed}"
                        yield (self.cell, (label, spec), {"model": use_model})

    def heldout(self, scene, experiment, intervals, seeds, algorithms=ALGORITHMS, **options):
        tasks = list(self._heldout_tasks(scene, experiment, intervals, seeds, algorithms, **options))
        graphs = [(fn, a, kw) for fn, a, kw in tasks if a[1]["alg"] in ("B5", "B6")]
        other = [(fn, a, kw) for fn, a, kw in tasks if a[1]["alg"] not in ("B5", "B6")]
        self.record(self.parallel(other, 4))
        self.record(self.parallel(graphs, 2))

    def _export(self, suffix):
        # Bind the run-ID index as one byte-exact input to the export run.
        import tempfile
        with tempfile.TemporaryDirectory(prefix=f"{self.prefix}-index-") as temp:
            path = Path(temp) / "index.json"
            path.write_text(json.dumps({
                "cells": sorted(self.raw, key=lambda row: (row["exp"], row["run_id"])),
                "fit_index": self.fit_index, "loads": self.loads,
                "static_guard": self.thresholds, "matrix": describe(),
            }, sort_keys=True) + "\n")
            self.run("export-" + suffix, "export", {"snapshot": suffix}, [("index.json", path)])

    def execute(self):
        self.calibrate()
        for scene in ("A", "B"):
            self.static_guard(scene)
            self.native(scene)
            self.pigou(scene)
            self.heldout(scene, "exp2", CADENCES, TEST_SEEDS)
            if scene == "A":
                for multiplier in (1.0, 1.6, 2.5):
                    self.heldout(scene, "exp0b", (0.05, 0.10), (101,), ("B1", "B2"),
                                 multiplier=multiplier, tag=f"m{round(multiplier * 10)}")
                self.heldout(scene, "exp0_hysteresis", CADENCES, (9003,), ("B1", "B2", "B4", "Ours"),
                             sweep=True, multiplier=1.0, tag="step")
                self._export("scene-a")
                self.raw.extend({**row, "exp": "exp4_ablation", "variant": "full"}
                    for row in list(self.raw) if row["exp"] == "exp2" and row["scene"] == "A"
                    and row["interval"] in (0.05, 0.1) and row["alg"] == "Ours")
                self.raw.extend({**row, "exp": "exp4_compute", "compute_delay_s": 0.0}
                    for row in list(self.raw) if row["exp"] == "exp2" and row["scene"] == "A"
                    and row["interval"] == 0.05 and row["alg"] in ("B2", "Ours"))
                for variant in ABLATIONS:
                    self.heldout(scene, "exp4_ablation", (0.05, 0.10), TEST_SEEDS, ("Ours",),
                                 variant=variant, tag=variant)
                for delay in (0.000010, 0.000050, 0.000100):
                    self.heldout(scene, "exp4_compute", (0.05,), TEST_SEEDS, ("B2", "Ours"),
                                 compute_delay_s=delay, tag=f"us{round(delay * 1e6)}")
                self._export("scene-a-mechanisms")
        self.heldout("A", "exp3_outage", (0.05,), TEST_SEEDS, outage=True, multiplier=1.0)
        self.heldout("A", "exp3_zeroshot", (0.05,), TEST_SEEDS, large=True)
        self._export("complete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--release")
    parser.add_argument("--prefix")
    parser.add_argument("--preflight")
    parser.add_argument("--attempt", type=int, default=0)
    args = parser.parse_args()
    if args.describe:
        print(json.dumps(describe(), indent=2))
        return
    if not args.release or not re.fullmatch(r"pigou-[A-Za-z0-9-]{3,30}", args.prefix or ""):
        parser.error("supply --release and --prefix pigou-<short-id>")
    _pinned_env()
    from CODE.scripts.remote import release_protocol

    if not re.fullmatch(r"pigou-[A-Za-z0-9-]+", args.preflight or ""):
        parser.error("supply the successful exact-release --preflight run ID")
    test_run = ROOT / "runs" / args.preflight
    witness = release_protocol.verify_run_directory(test_run, expected_run_id=args.preflight)
    if (_read(test_run / "preflight.json")["status"] != "passed"
            or _read(test_run / "run-manifest.json")["release_id"] != args.release
            or witness.get("status") != "completed"):
        raise RuntimeError("matrix requires a completed exact-release VM validation")
    # One study writer. Native release locks and independent run IDs protect
    # each trial. This lock carries no experiment authorization of its own.
    lock = ROOT / "runs" / ".locks" / "pigou-matrix.lock"
    with lock.open("a+b") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        Matrix(args.release, args.prefix, args.attempt).execute()


if __name__ == "__main__":
    main()
