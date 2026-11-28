"""Execute isolated Pigou study cells with the native T1 simulator and receipts."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time


ALGORITHMS = ("B1", "B2", "B3", "B4", "B5", "B6", "Ours")
LEARNERS = ("B4", "B5", "B6")
CADENCES = (0.010, 0.050, 0.100)
TRAIN_SEEDS = (11, 22, 33)
VAL_SEEDS = (41, 42)
TEST_SEEDS = (101, 102, 103, 104, 105)
CORRIDOR_EDGES = ((2, 90), (90, 82), (82, 74))
MODULE = "CODE.scripts.pigou_study"


def _json_write(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _release_run_dir():
    # The existing T1 release runner provisions a TMPDIR within this exact run.
    root = Path(os.environ["TMPDIR"]).resolve().parent
    if not (root / "run-manifest.json").is_file():
        raise ValueError("expected a T1 release-run directory")
    return root


def _input(name):
    return Path(os.environ["T1_INPUT_" + name.upper().replace(".", "_").replace("-", "_")])


def _set_threads(threads):
    for name in ("OMP_NUM_THREADS", "TF_NUM_INTRAOP_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[name] = str(threads)
    os.environ["TF_NUM_INTEROP_THREADS"] = "1"
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"


def cell_config(cfg, spec, checkpoint=None, metadata=None, resume=None):
    cfg = deepcopy(cfg)
    alg = spec["alg"]
    cfg["routing"].update(policy="hop", ephemeris_egress_fallback=True)
    cfg["control_plane"].update(vis_k=1)
    delay = spec.get("compute_delay_s", 0.0)
    cfg["execution"].update(compute_delay_s=delay, compute_servers_per_satellite=int(delay > 0),
                            decision_observation_mode="refresh",
                            available_capacity_interval_s=0.1)
    if alg in ("B2", "B3", "Collect"):
        cfg["time_alignment"].update(enabled=True, arm="candidate", predictor="ou_shrink",
                                     state_source="advertised", ou_tau_s=0.10)
    if alg == "B3":
        cfg["routing"]["local_forward_adapter"] = {
            "kind": "static_guard", "threshold": spec.get("threshold", 0.50),
        }
    if alg == "Collect":
        cfg["routing"]["local_forward_adapter"] = {
            "kind": "explore", "capture": True, "exploration": spec.get("exploration", 0.33),
        }
    if alg == "Ours":
        if checkpoint is None:
            raise ValueError("Pigou evaluation/rollout requires a frozen checkpoint")
        options = {
            "kind": "pigou_metadrl", "checkpoint_path": str(checkpoint),
            "checkpoint_sha256": sha(checkpoint), "beta_ext": spec.get("beta_ext", 0.50),
            "temperature": spec.get("temperature", 0.08), "shield": spec.get("shield", True),
            "argmax": spec.get("argmax", False), "capture": spec.get("capture", False),
            "exploration": spec.get("exploration", 0.10),
        }
        for name in ("telemetry_sat", "hop_guard", "hop_penalty",
                     "expert_checkpoint_path", "expert_checkpoint_sha256",
                     "expert_metadata_sha256", "expert_weight"):
            if name in spec:
                options[name] = spec[name]
        cfg["routing"]["local_forward_adapter"] = options
    if alg in LEARNERS:
        alg_name = "primal_cvar" if alg == "B6" else "ddqn"
        cfg["routing"].update(learning_enabled=True,
                              contract={"B4": "neighbor_1hop", "B5": "GAT", "B6": "PRIMAL"}[alg])
        cfg["learning"].update(
            algorithm=alg_name, seed=spec["train_seed"], obs_hops=1,
            include_last_hop=alg != "B6", replay_size=50_000,
            warmup_steps=spec.get("warmup_steps", 128), batch_size=32, train_every_steps=4,
            lr=0.001, clip_norm=10.0, gamma=1.0, reward="deadline_loss_v1",
            epsilon_start=0.35, epsilon_end=0.02, epsilon_decay_s=15.0,
            target_update_interval=500,
        )
        if checkpoint is not None:
            if metadata is None or Path(metadata) != Path(checkpoint).with_name("metadata.json"):
                raise ValueError("learning checkpoint requires sibling metadata.json")
            cfg["learning"].update(
                mode="eval", checkpoint_path=str(checkpoint), checkpoint_sha256=sha(checkpoint),
                checkpoint_metadata_sha256=sha(metadata), fixed_inference=alg != "B6",
                epsilon_start=0.0, epsilon_end=0.0,
            )
        elif resume is not None:
            cfg["learning"].update(resume_path=str(resume),
                                   resume_sha256=sha(resume / "manifest.json"))
    if spec.get("outage"):
        cfg["links"]["scheduled_isl_outages"] = [
            {"a": min(a, b), "b": max(a, b), "start_s": 2.0, "end_s": 3.5}
            for a, b in CORRIDOR_EDGES
        ]
    if spec.get("large"):
        cfg["scenario"].update(num_satellites=280, num_planes=14, altitude_km=550.0)
    return cfg


def _metric_components(rows, results, *, burst_end=3.5, recovery_end=9.5):
    delivered = results["deliveries"]
    success = {
        row["packet_id"] for row in rows
        if row["packet_id"] in delivered and
        delivered[row["packet_id"]]["delivered_at"] < row["deadline_at_s"]
    }
    counts = {"steady": [0, 0], "burst": [0, 0], "recovery": [0, 0]}
    buckets = [[0, 0] for _ in range(round((recovery_end - burst_end) / 0.1))]
    for row in rows:
        when = row["emit_time_s"]
        ok = row["packet_id"] in success
        if 0.5 <= when < 2.0:
            mark = "steady"
        elif 2.0 <= when < burst_end:
            mark = "burst"
        elif burst_end <= when < recovery_end:
            mark = "recovery"
            index = min(len(buckets) - 1, int((when - burst_end) / 0.1))
            buckets[index][0] += ok
            buckets[index][1] += 1
        else:
            continue
        counts[mark][0] += ok
        counts[mark][1] += 1
    rates = {name: ok / n if n else None for name, (ok, n) in counts.items()}
    t_rec = None
    if rates["steady"] is not None and rates["steady"] > 0.0:
        for i in range(len(buckets) - 7):
            if all(n and ok / n >= 0.95 * rates["steady"] for ok, n in buckets[i:i + 8]):
                t_rec = round(i * 0.1, 9)
                break
    score = len(success) / len(rows) - 0.02 * (
        t_rec if t_rec is not None else recovery_end - burst_end - 0.8)
    return {"pdr": len(success) / len(rows), "phase_pdr": rates, "t_rec_onset_s": t_rec,
            "t_rec_censor_onset_s": recovery_end - burst_end - 0.8, "val_score": score}


def _steady_utilization(result):
    """Peak directed data plus control serialization relative to available ISL capacity."""
    from collections import defaultdict

    def clip(row):
        return max(0.0, min(2.0, row["end"]) - max(0.5, row["start"]))

    cap = defaultdict(float)
    used = defaultdict(float)
    for row in result["link_available_windows"]:
        if row["stage"] == "isl":
            cap[row["link_id"]] += clip(row) * row["rate_bps"]
    for row in result["link_service_windows"]:
        if row["stage"] == "isl":
            used[row["link_id"]] += clip(row) * row["rate_bps"]
    for link, start, end, rate in result["local_routing_measurements"]["control_wire_windows"]:
        used[link] += max(0.0, min(2.0, end) - max(0.5, start)) * rate
    return max((used[key] / bits for key, bits in cap.items() if bits > 0), default=None)


def _nominal_hops(cfg, rows):
    """Apply a fixed time-zero zero-queue graph to every offered OD."""
    from collections import Counter

    from CODE.leo_sim import grid, model, routing

    sc = cfg["scenario"]
    lk = cfg["links"]
    geometry = model.Constellation(sc["num_satellites"], sc["num_planes"],
        sc["altitude_km"], sc["inclination_deg"], sc["min_elevation_deg"],
        max_isl_km=lk["max_isl_km"], geometry_epoch_s=sc["geometry_epoch_s"])
    graph = routing.build_topology(geometry, sc["num_satellites"], lk["isl_dirs"], 0.0)
    reverse = routing._reverse_adj({
        sat: {d: peer for d, peer in ports.items() if geometry.isl_available(sat, peer, 0.0)}
        for sat, ports in graph.items()
    })
    links = {}
    by_od = Counter((row["src_grid_id"], row["dst_grid_id"]) for row in rows)
    for cell in sorted({node for od in by_od for node in od}):
        lat, lon = grid.grid_center(cell)
        choices = [sat for sat in graph if geometry.ground_visible(sat, lat, lon, 0.0)]
        if choices:
            links[cell] = min(choices, key=lambda sat: (geometry.slant_range_km(sat, lat, lon, 0.0), sat))
    distances = {cell: routing._multi_source_bfs(reverse, [sat]) for cell, sat in links.items()}
    covered = total = 0
    for (src, dst), n in by_od.items():
        hops = distances.get(dst, {}).get(links.get(src))
        if hops is not None:
            total += n * hops
            covered += n
    return {"hops": total if covered == len(rows) else None, "covered_packets": covered,
            "offered_packets": len(rows), "basis": "time_zero_nearest_nominal_egress_min_hops"}


def simulate(out, spec, *, checkpoint=None, metadata=None, resume=None):
    from CODE.leo_sim import config, kernel, receipt
    from CODE.leo_sim.batch import compile_run_trace
    from CODE.scripts.build_three_phase_trace import build, build_staircase

    start = time.monotonic()
    out = Path(out)
    sim = out / "sim"
    sim.mkdir(parents=True)
    if spec.get("sweep"):
        workload = build_staircase(out / "traffic", seed=spec["seed"],
                                   interval=spec["interval"], offered_mbps=spec["offered_mbps"])
    else:
        workload = build(out / "traffic", scene=spec["scene"], seed=spec["seed"],
                         interval=spec.get("interval", 0.05), multiplier=spec.get("multiplier", 2.5),
                         offered_mbps=spec.get("offered_mbps"))
    raw = cell_config(workload["config"], spec, checkpoint=checkpoint, metadata=metadata, resume=resume)
    raw["outputs"]["out_dir"] = str(sim)
    resolved = config.resolve_config(raw)
    manifest, trace_bytes, rows = compile_run_trace(resolved, str(sim))
    env = resolved["config"]
    resources = [] if env["routing"].get("local_forward_adapter", {}).get("capture") else None
    algorithm = env["learning"]["algorithm"]
    result = kernel.run_simulation(resolved, rows, resource_sink=resources,
                                   learning_out_dir=sim / algorithm if algorithm != "none" else None)
    rcp = receipt.write_run(str(sim), resolved, trace_bytes, manifest, result, rows)
    errors = receipt.verify_receipt_dir(str(sim))
    if not rcp["natural_end"] or not rcp["conservation_ok"] or errors:
        raise ValueError(f"unverified simulation {spec['alg']}: {rcp.get('error') or errors}")
    if rcp["fate_counts"].get("IN_SYSTEM_AT_STOP", 0):
        raise ValueError("some offered data remains unsettled after the hard deadline and drain")
    if spec["alg"] in LEARNERS and not rcp["mechanisms"]["effective"]["learning"]:
        raise ValueError(f"{spec['alg']} made no train/eval decision updates under this workload")
    policy = result.get("local_forward_adapter")
    if policy and policy["calls"] == 0:
        raise ValueError("local forwarding adapter never handled a legal choice")
    if policy and policy["telemetry"]:
        with (sim / "telemetry.jsonl").open("x", encoding="utf-8") as stream:
            for row in policy["telemetry"]:
                stream.write(json.dumps(row, allow_nan=False, separators=(",", ":")) + "\n")
    audit = None
    if resources is not None:
        import gzip
        import numpy as np
        from CODE.leo_sim.rl.pigou_labels import build_dataset

        snaps, pkts, audit = build_dataset(result, resources, rows, env)
        np.savez_compressed(out / "snaps.npz", **snaps)
        np.savez_compressed(out / "pkts.npz", **pkts)
        with gzip.open(out / "resource-events.jsonl.gz", "wt", encoding="utf-8") as stream:
            for event in resources:
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        audit.update(snapshots_sha256=sha(out / "snaps.npz"),
                     packets_sha256=sha(out / "pkts.npz"),
                     raw_events_sha256=sha(out / "resource-events.jsonl.gz"))
    import gzip
    import statistics

    measurements = result["local_routing_measurements"]
    raw_measurements = sim / "routing-measurements.json.gz"
    with gzip.open(raw_measurements, "wt", encoding="utf-8") as handle:
        json.dump(measurements, handle, separators=(",", ":"), allow_nan=False)
    samples = [age for row in measurements["neighbor_age_samples"] for age in row[4]]
    ages = [age for age in samples if age is not None]
    vals = _metric_components(rows, result)
    if policy:
        _json_write(sim / "policy-audit.json", {
            "adapter": policy["kind"], "forwarding_calls": policy["calls"],
            "options": env["routing"]["local_forward_adapter"],
            "telemetry_sha256": sha(sim / "telemetry.jsonl") if (sim / "telemetry.jsonl").is_file() else None,
        })
    summary = {**spec, **vals, "trace_sha256": rcp["trace_sha256"],
               "run_dir": str(sim), "offered_packets": len(rows),
               "receipt_sha256": sha(sim / "receipt.json"),
               "background_sha256": workload["background_packet_sha256"],
               "fate_counts": rcp["fate_counts"], "audit": audit,
               "policy_calls": None if policy is None else policy["calls"],
               "control_bits": rcp["control"]["totals"].get("offered_bits"),
               "steady_utilization_peak": _steady_utilization(result),
               "measured_neighbor_aoi_p50_s": statistics.median(ages) if ages else None,
               "measured_neighbor_aoi_p95_s": (sorted(ages)[int(0.95 * (len(ages) - 1))]
                                               if ages else None),
               "measured_neighbor_aoi_obs_fraction": len(ages) / len(samples) if samples else None,
               "routing_measurements_sha256": sha(raw_measurements),
               "nominal_shortest_hops": _nominal_hops(env, rows),
               "plateaus": workload.get("plateaus"),
               "wall_seconds": time.monotonic() - start,
               "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
                   1024 if sys.platform.startswith("linux") else 1)}
    _json_write(out / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("alg", "scene", "seed", "offered_packets",
                                              "trace_sha256", "wall_seconds", "pdr")}), flush=True)
    return summary


def _cell_main(spec):
    _set_threads(spec.get("threads", 2))
    root = _release_run_dir()
    checkpoint = _input("online.keras") if "T1_INPUT_ONLINE_KERAS" in os.environ else (
        _input("frozen.npz") if "T1_INPUT_FROZEN_NPZ" in os.environ else None)
    metadata = _input("metadata.json") if "T1_INPUT_METADATA_JSON" in os.environ else None
    if "T1_INPUT_EXPERT_KERAS" in os.environ:
        expert_path = _input("expert.keras")
        if metadata is None:
            raise ValueError("expert checkpoint requires metadata.json")
        spec = {**spec, "expert_checkpoint_path": str(expert_path),
                "expert_checkpoint_sha256": sha(expert_path),
                "expert_metadata_sha256": sha(metadata)}
    resume = None
    if "T1_INPUT_RESUME_TAR" in os.environ:
        import tarfile
        resume = root / "unpacked-resume"
        resume.mkdir()
        with tarfile.open(_input("resume.tar"), "r:") as archive:
            members = archive.getmembers()
            if any(not item.isfile() or Path(item.name).name != item.name for item in members):
                raise ValueError("unexpected member in native learner resume bundle")
            archive.extractall(resume, members)
    summary = simulate(root / "cell", spec, checkpoint=checkpoint, metadata=metadata, resume=resume)
    if spec["alg"] in LEARNERS and checkpoint is None:
        import tarfile
        learner = Path(summary["run_dir"]) / ("primal_cvar" if spec["alg"] == "B6" else "ddqn")
        with tarfile.open(root / "resume.tar", "x") as archive:
            for item in sorted((learner / "resume").iterdir()):
                archive.add(item, arcname=item.name, recursive=False)


def _fit(out, bundles, spec, previous=None):
    import numpy as np
    from CODE.leo_sim.rl import pigou_model
    from CODE.leo_sim.rl.pigou_labels import merge_dataset

    snap, pkt = merge_dataset(bundles)
    if not np.array_equal(np.flatnonzero(np.bincount(pkt["action"], minlength=4) > 0),
                          np.arange(4)):
        raise ValueError("collection has no samples in at least one physical direction")
    model, stats = pigou_model.train(
        snap, pkt, seed=spec["seed"], steps=spec["steps"], batch_size=spec.get("batch_size", 512),
        lr=spec["lr"], variant=spec.get("variant", "full"), previous_weights=previous)
    if not (stats["stage_i_changed_parameters"] and stats["stage_ii_changed_parameters"]):
        raise ValueError("gradient updates did not change both stages")
    weights = out / "frozen.npz"
    model.save(weights)
    restored = pigou_model.NumpyPigou.load(weights)
    sid = int(pkt["snap_id"][0])
    obs = snap["x_2hop"][sid], snap["snap_cur"][sid], snap["hist_seq"][sid]
    fast = pkt["x_pkt_global"][0], pkt["x_pkt_port"][0]
    encoded = model.encode(*obs)
    before = model.score(encoded, *fast)
    after = restored.score(restored.encode(*obs), *fast)
    for original, saved in zip(before, after):
        np.testing.assert_array_equal(original, saved)
    import tensorflow as tf

    observed = pigou_model._tensorflow_forward(
        tf, {k: tf.convert_to_tensor(v) for k, v in restored.parameters.items()},
        restored.variant, *(tf.convert_to_tensor(v[None]) for v in (*obs, *fast)),
        include_aux=False)
    for name, numpy_value in zip(("ego", "ext"), after):
        np.testing.assert_allclose(observed[name].numpy()[0], numpy_value, rtol=4e-5, atol=4e-6)
    stats.update(weights_sha256=sha(weights),
                 input_shas=[(sha(a), sha(b)) for a, b in bundles])
    _json_write(out / "fit.json", stats)
    return weights, stats


def fit_main(spec):
    _set_threads(spec.get("threads", 8))
    root = _release_run_dir()
    paths = [(_input(f"snap-{i}.npz"), _input(f"pkt-{i}.npz"))
             for i in range(spec["datasets"])]
    previous = _input("previous.npz") if "T1_INPUT_PREVIOUS_NPZ" in os.environ else None
    _fit(root, paths, spec, previous)


def preflight(spec):
    _set_threads(2)
    root = _release_run_dir()
    import tensorflow as tf
    if not tf.__version__.startswith("2.13.") or tf.config.list_physical_devices("GPU"):
        raise RuntimeError("expected TensorFlow CPU 2.13")
    seed = int(spec.get("seed", 29001))
    small = {"scene": "A", "seed": seed, "interval": 0.1, "multiplier": 1.6,
             "offered_mbps": 2.5, "warmup_steps": 32, "exp": "preflight"}
    summaries = []
    for alg in ("B1", "B2", "B3", "Collect"):
        summaries.append(simulate(root / alg, {**small, "alg": alg}))
    for alg in ("B6", "B4", "B5"):
        train = simulate(root / alg, {**small, "alg": alg, "train_seed": 11})
        artifact = Path(train["run_dir"]) / ("primal_cvar" if alg == "B6" else "ddqn")
        summaries.append(simulate(root / (alg + "-eval"), {**small, "alg": alg, "train_seed": 11},
                                  checkpoint=artifact / "online.keras",
                                  metadata=artifact / "metadata.json"))
    checkpoint, stats = _fit(root, [(root / "Collect" / "snaps.npz", root / "Collect" / "pkts.npz")],
                             {"seed": 11, "steps": 1000, "lr": 0.0005, "batch_size": 64})
    summaries.append(simulate(root / "Ours", {**small, "alg": "Ours",
                             "telemetry_sat": CORRIDOR_EDGES[0][0]}, checkpoint=checkpoint))
    if len({row["trace_sha256"] for row in summaries}) != 1:
        raise ValueError("preflight arms used different packet cohorts")
    b = simulate(root / "scene-b", {**small, "scene": "B", "alg": "B1"})
    fault = simulate(root / "outage", {**small, "alg": "B2", "outage": True,
                                      "multiplier": 1.0})
    staircase = simulate(root / "staircase", {**small, "alg": "B1", "sweep": True,
                                             "multiplier": 1.0})
    summaries.extend((b, fault, staircase))
    _json_write(root / "preflight.json", {"status": "passed", "tensorflow": tf.__version__,
                                         "fit_steps": stats["steps"], "arms": summaries})


def export_main():
    import csv
    from collections import defaultdict
    from tempfile import TemporaryDirectory

    from CODE.scripts.export_eval_artifacts import export
    from CODE.scripts.remote.release_protocol import verify_run_directory

    root = _release_run_dir()
    index = json.loads(_input("index.json").read_text())
    release_id = json.loads((root / "run-manifest.json").read_text())["release_id"]
    verified = set()

    def require_source(record, directory):
        expected = root.parent / record["run_id"]
        if expected.name != record["run_id"] or directory != expected:
            raise ValueError(f"export row is outside its receipted source run: {record['run_id']}")
        receipt_path = directory / "run-receipt.json"
        if sha(receipt_path) != record["outer_receipt_sha256"]:
            raise ValueError(f"export source receipt changed: {record['run_id']}")
        if directory not in verified:
            evidence = verify_run_directory(directory, expected_run_id=record["run_id"],
                                            expected_release_id=release_id)
            if evidence["status"] != "completed":
                raise ValueError("plot sources must be completed runs")
            verified.add(directory)

    for item in index["cells"]:
        sim = Path(item["run_dir"])
        require_source(item, sim.parent.parent)
        if sha(sim / "receipt.json") != item["receipt_sha256"]:
            raise ValueError(f"plot source simulator receipt changed: {item['run_id']}")
    for item in index["fit_index"]:
        parent = Path(item["fit_path"]).parent
        require_source(item, parent)
        if sha(parent / "frozen.npz") != item["weights_sha256"]:
            raise ValueError(f"plot source checkpoint changed: {item['run_id']}")
    cohorts = {}
    batches = defaultdict(list)
    for item in index["cells"]:
        exp = item["exp"]
        key = (exp, item["scene"], item["seed"], item["interval"],
               item["multiplier"], item.get("offered_mbps"), item.get("outage"), item.get("large"))
        if exp in {"exp2", "exp3_outage", "exp3_zeroshot", "exp4_ablation", "exp4_compute"}:
            if cohorts.setdefault(key, item["trace_sha256"]) != item["trace_sha256"]:
                raise ValueError(f"held-out cohorts differ by algorithm or checkpoint: {key}")
        batches[(exp, item["scene"], item["alg"], item.get("train_seed"), item["interval"],
                 item.get("variant"), item.get("compute_delay_s"))].append(item)

    plots = root / "figures-data"
    plots.mkdir()
    by_file = defaultdict(list)
    with TemporaryDirectory(dir=root / "tmp", prefix="plot-groups-") as tmp:
        for i, group in enumerate(batches.values()):
            # Bound memory by one policy/seed family and allow existing export
            # code to perform the independent-seed summaries within the group.
            directory = Path(tmp) / str(i)
            info = export(directory, group)
            for name, count in info["row_counts"].items():
                if count:
                    by_file[name].append(directory / name)
        for name, group_files in sorted(by_file.items()):
            columns = {}
            for group_file in group_files:
                with group_file.open(newline="") as source:
                    columns.update(dict.fromkeys(csv.DictReader(source).fieldnames))
            with (plots / name).open("x", newline="") as target:
                writer = csv.DictWriter(target, columns)
                writer.writeheader()
                for group_file in group_files:
                    with group_file.open(newline="") as source:
                        writer.writerows(csv.DictReader(source))
    if index["fit_index"]:
        with (plots / "exp1_pigou_fit_steps.csv").open("x", newline="") as target:
            fields = ("scene", "train_seed", "round", "variant", "step", "loss", "weights_sha256")
            writer = csv.DictWriter(target, fields)
            writer.writeheader()
            for record in index["fit_index"]:
                stats = json.loads(Path(record["fit_path"]).read_text())
                for i, loss in enumerate(stats["loss_history"], 1):
                    writer.writerow({**{key: record[key] for key in fields if key in record},
                                     "step": i, "loss": loss})
    _json_write(root / "figure-manifest.json",
                {"files": {p.name: sha(p) for p in sorted(plots.iterdir())},
                 "input_index_sha256": sha(_input("index.json")), "cells": len(index["cells"])})


def _pinned_env():
    quota = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    limit = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if sys.platform != "linux" or not quota.is_file() or not limit.is_file():
        raise RuntimeError("Pigou execution requires the pinned T1 Linux VM")
    if int(quota.read_text()) < 1 or int(limit.read_text()) < 22 * 2**30:
        raise RuntimeError("VM cgroup does not meet the declared resource budget")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("cell", "preflight", "fit", "export"))
    parser.add_argument("--spec", default="{}")
    args = parser.parse_args()
    spec = json.loads(args.spec)
    _pinned_env()
    if args.operation == "cell":
        _cell_main(spec)
    elif args.operation == "fit":
        fit_main(spec)
    elif args.operation == "export":
        export_main()
    else:
        preflight(spec)


if __name__ == "__main__":
    main()
