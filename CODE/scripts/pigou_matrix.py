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


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from CODE.scripts.pigou_study import (
    ALGORITHMS, CADENCES, CORRIDOR_EDGES, LEARNERS, MODULE, TEST_SEEDS, TRAIN_SEEDS,
    VAL_SEEDS, _pinned_env, sha,
)

ROOT = Path("/data/论文/leo-t1-wt")
CALIBRATION_SEEDS = (9001,)
CALIBRATION_LOADS = {
    "A": (180.0, 210.0, 235.0),
    "B": (150.0, 180.0, 210.0),
}
TARGET_STEADY_UTILIZATION = 0.720
UTILIZATION_TOLERANCE = 0.050
ABLATIONS = ("no_ext", "mean", "frame", "no_ais", "argmax", "no_shield")
FIT_ROUNDS = (
    (2, 1200, 5e-4),
    (2, 1000, 4e-4),
    (1, 800, 3e-4),
    (1, 600, 2e-4),
    (1, 400, 1e-4),
)
VARIANTS = {"A": ("full",), "B": ("full",)}
BETA_GRID = (0.25, 0.50, 0.75, 1.00)


def describe():
    learning_families = len(LEARNERS) + 1
    arms = len(ALGORITHMS) - learning_families + learning_families * len(TRAIN_SEEDS)
    model_chains = sum(map(len, VARIANTS.values())) * len(TRAIN_SEEDS)
    jobs = {
        "calibration": sum(map(len, CALIBRATION_LOADS.values())) * len(CALIBRATION_SEEDS),
        "static_guard_validation": 0,
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
        "post_export_onset_censor_s": 5.2,
        "cadence_s": CADENCES,
        "train_seeds": TRAIN_SEEDS, "validation_seeds": VAL_SEEDS, "test_seeds": TEST_SEEDS,
    }


def _params(scene, seed, alg, exp, rate, **options):
    return {"scene": scene, "seed": seed, "alg": alg, "exp": exp,
            "interval": 0.05, "multiplier": 2.5, "offered_mbps": rate,
            "threads": 1, **options}


def _read(path):
    return json.loads(path.read_text())


def canonical_task_argv(argv):
    """Canonicalize the JSON task spec for exact semantic run reuse."""
    canonical = list(argv)
    for index, token in enumerate(canonical[:-1]):
        if token != "--spec":
            continue
        canonical[index + 1] = json.dumps(
            json.loads(canonical[index + 1]), sort_keys=True,
            separators=(",", ":"))
    return canonical


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
        if rss >= 40 * 2**30:
            raise RuntimeError("VM anonymous RSS has reached the study's 40 GiB ceiling")
        if limit - rss < 16 * 2**30:
            raise RuntimeError("study has less than the reserved 16 GiB memory headroom")

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
            if (manifest.get("release_id") != self.release
                    or canonical_task_argv(manifest.get("argv", [])[1:]) != canonical_task_argv(argv[1:])
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
                inputs.extend((("online.keras", weight),
                               ("metadata.json", weight.with_name("metadata.json"))))
                if spec["alg"] == "B6":
                    inputs.append(("online.npz", weight.with_name("online.npz")))
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

    def calibrate(self, scenes=("A", "B")):
        for scene in scenes:
            items = []
            for rate in CALIBRATION_LOADS[scene]:
                for seed in CALIBRATION_SEEDS:
                    spec = _params(scene, seed, "B1", "exp0", rate, multiplier=1.0)
                    label = f"cal-{scene}-B1-l{int(round(rate * 10))}-s{seed}"
                    items.append((self.cell, (label, spec), {}))
            cells = self.parallel(items, 16)
            self.record(cells)
            by_rate = {}
            for rate in CALIBRATION_LOADS[scene]:
                values = [
                    row["steady_utilization_peak"]
                    for _, row in cells if row["offered_mbps"] == rate
                ]
                if not values or any(value is None for value in values):
                    raise RuntimeError(
                        f"no capacity-matched steady-load measurements for scene {scene}")
                by_rate[rate] = sum(values) / len(values)
            selected = min(
                CALIBRATION_LOADS[scene],
                key=lambda rate: abs(by_rate[rate] - TARGET_STEADY_UTILIZATION),
            )
            measured = float(by_rate[selected])
            low = TARGET_STEADY_UTILIZATION - UTILIZATION_TOLERANCE
            high = TARGET_STEADY_UTILIZATION + UTILIZATION_TOLERANCE
            if not low <= measured <= high:
                raise RuntimeError(
                    f"scene {scene} calibration cannot reach target utilization "
                    f"[{low:.3f}, {high:.3f}]; best {selected} Mbps gives {measured:.4f}")
            self.loads[scene] = selected
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
        for round_no in range(1, 6):
            scenario_seed = (200_000 if scene == "B" else 100_000) + train_seed * 100 + round_no
            parent, report = self.cell(
                f"{scene}-{alg}-t{train_seed}-r{round_no}",
                _params(scene, scenario_seed, alg, "exp1", self.loads[scene],
                        train_seed=train_seed, epoch=round_no, threads=2),
                resume=previous)
            previous = parent / "resume.tar"
            path = Path(report["run_dir"]) / ("primal_cvar" if alg == "B6" else "ddqn") / "online.keras"
            if round_no in {3, 5}:
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

    def native(self, scene, train_seeds=TRAIN_SEEDS):
        tasks = [(self._native_seed, (scene, alg, train_seed), {})
                 for alg in LEARNERS for train_seed in train_seeds]
        for alg, seed, path in self.parallel(tasks, 4):
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
             "variant": variant, "datasets": len(data), "threads": 2}, inputs)
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
            for s in VAL_SEEDS], 16)
        return sum(row["val_score"] for _, row in vals) / len(vals)

    def _pigou_seed(self, scene, train_seed, variants=None):
        arch_variants = tuple(variants or VARIANTS[scene])
        unknown = set(arch_variants) - set(VARIANTS[scene])
        if unknown:
            raise ValueError(f"unsupported Pigou variants for scene {scene}: {sorted(unknown)}")
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
                    capture=True, exploration=0.12 if model else (0.08, 0.24)[i % 2],
                    temperature=(0.06, 0.12)[i % 2] if number < 3 else (0.04, 0.08)[i % 2],
                    threads=2)
                rollouts.append((self.cell, (f"{scene}-roll-t{train_seed}-r{number}-e{i}", spec),
                                 {"model": model}))
            data.extend(parent for parent, _ in self.parallel(rollouts, 2))
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
        return "Ours", train_seed, best["full"][1]

    def pigou(self, scene, train_seeds=TRAIN_SEEDS, variants=None):
        for train_seed in train_seeds:
            self._pigou_seed(scene, train_seed, variants)

    def train_four_concurrent(self, scene):
        tasks = [
            (self._native_seed, (scene, alg, 11), {})
            for alg in LEARNERS
        ]
        tasks.append((self._pigou_seed, (scene, 11, ("full",)), {}))
        for alg, seed, path in self.parallel(tasks, 4):
            if alg != "Ours":
                self.models[(scene, alg, seed, "full")] = path, {}

    def _heldout_tasks(self, scene, experiment, intervals, seeds,
                       algorithms=ALGORITHMS, train_seeds=TRAIN_SEEDS,
                       **options):
        variant = options.get("variant", "full")
        for alg in algorithms:
            learning = alg in LEARNERS or alg == "Ours"
            for train_seed in train_seeds if learning else (None,):
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
        self.record(self.parallel(other, 16))
        self.record(self.parallel(graphs, 16))

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

    def exp0(self):
        self.calibrate()
        self.heldout("A", "exp0", (0.05,), (101,), ("B1", "B2"),
                     multiplier=1.0, tag="workpoint")
        for multiplier in (1.0, 1.6, 2.5):
            self.heldout("A", "exp0b", (0.05,), (101,), ("B1", "B2"),
                         multiplier=multiplier, tag=f"m{round(multiplier * 10)}")
        self.heldout("A", "exp0_hysteresis", (0.05,), (9003,), ("B1", "B2"),
                     sweep=True, multiplier=1.0, tag="step")
        self._export("wave0")

    def pilot(self):
        self.calibrate()
        self.parallel([
            (self.train_four_concurrent, ("A",), {}),
            (self.train_four_concurrent, ("B",), {}),
        ], 2)
        for scene in ("A", "B"):
            self.heldout(scene, "exp2", (0.05,), (101,), ALGORITHMS,
                         train_seeds=(11,), tag="pilot")
        self._export("pilot")

    def core(self):
        self.calibrate()
        for scene in ("A", "B"):
            self.native(scene)
            self.pigou(scene)
            self.heldout(scene, "exp2", (0.05,), TEST_SEEDS, ALGORITHMS, tag="main")
        self._export("core")

    def extras(self):
        self.calibrate()
        for scene in ("A", "B"):
            self.native(scene)
            self.pigou(scene)
            self.heldout(scene, "exp2", (0.010, 0.100), TEST_SEEDS,
                         ALGORITHMS, tag="main")
        self.heldout("A", "exp0_hysteresis", CADENCES, (9003,),
                     ("B1", "B2", "B4", "Ours"), sweep=True,
                     multiplier=1.0, tag="step")
        self.raw.extend({**row, "exp": "exp4_ablation", "variant": "full"}
            for row in list(self.raw) if row["exp"] == "exp2"
            and row["scene"] == "A" and row["interval"] in (0.05, 0.1)
            and row["alg"] == "Ours")
        self.raw.extend({**row, "exp": "exp4_compute", "compute_delay_s": 0.0}
            for row in list(self.raw) if row["exp"] == "exp2"
            and row["scene"] == "A" and row["interval"] == 0.05
            and row["alg"] in ("B2", "Ours"))
        for variant in ABLATIONS:
            self.heldout("A", "exp4_ablation", (0.05, 0.10), TEST_SEEDS,
                         ("Ours",), variant=variant, tag=variant)
        for delay in (0.000010, 0.000050, 0.000100):
            self.heldout("A", "exp4_compute", (0.05,), TEST_SEEDS,
                         ("B2", "Ours"), compute_delay_s=delay,
                         tag=f"us{round(delay * 1e6)}")
        self.heldout("A", "exp3_outage", (0.05,), TEST_SEEDS,
                     outage=True, multiplier=1.0)
        self.heldout("A", "exp3_zeroshot", (0.05,), TEST_SEEDS, large=True)
        self._export("extras")

    @staticmethod
    def _mean_metric(rows, key):
        values = [float(row[key]) for row in rows if row.get(key) is not None]
        return sum(values) / len(values) if values else None

    def _gate2_probe(self):
        rows = []
        for scene in ("A", "B"):
            tasks = list(self._heldout_tasks(
                scene, "gate2", (0.05,), VAL_SEEDS, ALGORITHMS,
                train_seeds=(11,), tag="gate2"))
            rows.extend(row for _, row in self.parallel(tasks, 16))
        no_ext = []
        for scene in ("A", "B"):
            tasks = list(self._heldout_tasks(
                scene, "gate2_no_ext", (0.05,), VAL_SEEDS, ("Ours",),
                train_seeds=(11,), variant="no_ext", tag="gate2-no-ext"))
            no_ext.extend(row for _, row in self.parallel(tasks, 16))
        self.raw.extend(rows)
        self.raw.extend(no_ext)
        report = {}
        for scene in ("A", "B"):
            scene_rows = [row for row in rows if row["scene"] == scene]
            ours = [row for row in scene_rows if row["alg"] == "Ours"]
            baselines = [row for row in scene_rows if row["alg"] != "Ours"]
            ours_tx = self._mean_metric(ours, "tx_goodput")
            baseline_tx = {
                alg: self._mean_metric([r for r in baselines if r["alg"] == alg],
                                       "tx_goodput")
                for alg in ("B1", "B2", "B4", "B5", "B6")
            }
            no_ext_rows = [row for row in no_ext if row["scene"] == scene]
            no_ext_tx = self._mean_metric(no_ext_rows, "tx_goodput")
            passed = (
                ours_tx is not None
                and all(value is not None and ours_tx > value
                        for value in baseline_tx.values())
                and no_ext_tx is not None and ours_tx > no_ext_tx
            )
            report[scene] = {
                "ours_tx_goodput": ours_tx,
                "baseline_tx_goodput": baseline_tx,
                "no_ext_tx_goodput": no_ext_tx,
                "passed_tx_goodput": passed,
            }
        _json_write(ROOT / "runs" / f"{self.prefix}-gate2.json", report)
        if not all(item["passed_tx_goodput"] for item in report.values()):
            raise RuntimeError(f"Gate-2 failed: {report}")
        return report

    def tonight(self):
        """Five-round meeting edition ending in all 14 required figure CSVs."""
        self.calibrate()
        self.parallel([
            (self.train_four_concurrent, ("A",), {}),
            (self.train_four_concurrent, ("B",), {}),
        ], 2)
        self._gate2_probe()

        # Wave 1: calibration is already in self.raw; add shock and hysteresis.
        for multiplier in (1.0, 2.5, 2.8):
            self.heldout("A", "exp0b", (0.05,), (101,), ("B1", "B2"),
                         multiplier=multiplier, tag=f"m{round(multiplier * 10)}")
        self.heldout("A", "exp0_hysteresis", (0.05,), (9003,),
                     ("B1", "B2", "B4", "Ours"), sweep=True,
                     multiplier=1.0, tag="step")
        self._export("wave1")

        # Wave 3: Scene-B only at 50 ms; Scene-A gets the requested scans.
        self.heldout("B", "exp2", (0.05,), TEST_SEEDS, ALGORITHMS,
                     train_seeds=(11,), tag="main")
        self.heldout("A", "exp2", (0.05,), TEST_SEEDS, ALGORITHMS,
                     train_seeds=(11,), tag="main")
        self.heldout("A", "exp2", (0.010, 0.100), TEST_SEEDS, ALGORITHMS,
                     train_seeds=(11,), tag="main")
        outage = self.parallel([(self.cell, (
            f"A-outage-{alg}-t11-s{seed}",
            _params("A", seed, alg, "exp3_outage", self.loads["A"],
                    train_seed=11 if alg in LEARNERS or alg == "Ours" else None,
                    multiplier=1.0, outage=True)),
            ({"model": self.models[("A", alg, 11, "full")]}
             if alg in LEARNERS or alg == "Ours" else {}))
            for alg in ALGORITHMS for seed in TEST_SEEDS], 16)
        self.record(outage)
        large = self.parallel([(self.cell, (
            f"A-large-{alg}-t11-s{seed}",
            _params("A", seed, alg, "exp3_zeroshot", self.loads["A"],
                    train_seed=11 if alg in LEARNERS or alg == "Ours" else None,
                    large=True)),
            ({"model": self.models[("A", alg, 11, "full")]}
             if alg in LEARNERS or alg == "Ours" else {}))
            for alg in ALGORITHMS for seed in TEST_SEEDS], 16)
        self.record(large)
        self._export("wave3")

        # Wave 4: four high-value ablations and the compute-cost sweep.
        for variant in ("no_ext", "mean", "frame", "no_ais"):
            self.heldout("A", "exp4_ablation", (0.05,), TEST_SEEDS, ("Ours",),
                         variant=variant, tag=variant)
        self.raw.extend({**row, "exp": "exp4_ablation", "variant": "full"}
                        for row in self.raw
                        if row.get("exp") == "exp2" and row.get("scene") == "A"
                        and row.get("interval") == 0.05 and row.get("alg") == "Ours")
        for delay in (0.000010, 0.000100):
            self.heldout("A", "exp4_compute", (0.05,), TEST_SEEDS,
                         ("B2", "Ours"), compute_delay_s=delay,
                         tag=f"us{round(delay * 1e6)}")
        self.raw.extend({**row, "exp": "exp4_compute", "compute_delay_s": 0.0}
                        for row in self.raw
                        if row.get("exp") == "exp2" and row.get("scene") == "A"
                        and row.get("interval") == 0.05
                        and row.get("alg") in ("B2", "Ours"))
        self._export("complete")

    def execute(self):
        self.calibrate()
        for scene in ("A", "B"):
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
    parser.add_argument("--wave", choices=("exp0", "pilot", "core", "extras", "tonight", "all"),
                        default="all")
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
        matrix = Matrix(args.release, args.prefix, args.attempt)
        if args.wave == "all":
            matrix.execute()
        else:
            getattr(matrix, args.wave)()


if __name__ == "__main__":
    main()
