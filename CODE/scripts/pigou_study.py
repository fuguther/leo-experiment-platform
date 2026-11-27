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
        }
        if "telemetry_sat" in spec:
            options["telemetry_sat"] = spec["telemetry_sat"]
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


def _metric_components(rows, results, *, burst_end=3.5, recovery_end=7.5):
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


def simulate(out, spec, *, checkpoint=None, metadata=None, resume=None):
    from CODE.leo_sim import config, kernel, receipt
    from CODE.leo_sim.batch import compile_run_trace
    from CODE.scripts.build_three_phase_trace import build

    start = time.monotonic()
    out = Path(out)
    sim = out / "sim"
    sim.mkdir(parents=True)
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
    for alg in LEARNERS:
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
    _json_write(root / "preflight.json", {"status": "passed", "tensorflow": tf.__version__,
                                         "fit_steps": stats["steps"], "arms": summaries})


def _pinned_env():
    quota = Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    limit = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    if sys.platform != "linux" or not quota.is_file() or not limit.is_file():
        raise RuntimeError("Pigou execution requires the pinned T1 Linux VM")
    if int(quota.read_text()) < 1 or int(limit.read_text()) < 22 * 2**30:
        raise RuntimeError("VM cgroup does not meet the declared resource budget")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("cell", "preflight", "fit"))
    parser.add_argument("--spec", default="{}")
    args = parser.parse_args()
    spec = json.loads(args.spec)
    _pinned_env()
    if args.operation == "cell":
        _cell_main(spec)
    elif args.operation == "fit":
        fit_main(spec)
    else:
        preflight(spec)


if __name__ == "__main__":
    main()
