"""Execute isolated Pigou study cells with the native T1 simulator and receipts."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


ALGORITHMS = ("B1", "B2", "B4", "B5", "B6", "Ours")
LEARNERS = ("B4", "B5", "B6")
CADENCES = (0.010, 0.050, 0.100)
TRAIN_SEEDS = (11,)
VAL_SEEDS = (41, 42)
TEST_SEEDS = (101, 102, 103)
CORRIDOR_EDGES = ((2, 90), (90, 82), (82, 74))
CORRIDOR_EDGES_BY_SCENE = {
    "A": CORRIDOR_EDGES,
    "B": ((12, 23), (23, 34), (34, 45)),
    "C": ((12, 23), (23, 34), (34, 45)),
    "D": ((22, 33), (33, 34), (34, 35), (22, 32)),
}
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
    if alg == "B2":
        cfg["time_alignment"].update(
            enabled=True, arm="candidate", predictor="hold_last",
            state_source="advertised", queue_weight=4.0)
    if alg in ("B3", "Collect"):
        cfg["time_alignment"].update(
            enabled=True, arm="candidate", predictor="ou_shrink",
            state_source="advertised", ou_tau_s=0.10)
    if alg == "B3":
        cfg["routing"]["local_forward_adapter"] = {
            "kind": "static_guard", "threshold": spec.get("threshold", 0.50),
        }
    if alg == "Collect":
        cfg["routing"]["local_forward_adapter"] = {
            "kind": "explore", "capture": True, "exploration": spec.get("exploration", 0.33),
        }
    if alg == "Ours" and spec.get("online_ours"):
        cfg["routing"].update(learning_enabled=True, contract="PIGOU_V2")
        cfg["learning"].update(
            algorithm="pigou_online", seed=spec["train_seed"], obs_hops=1,
            include_last_hop=False, replay_size=50_000,
            warmup_steps=spec.get("warmup_steps", 128), batch_size=32,
            train_every_steps=spec.get("train_every_steps", 32),
            lr=0.001, clip_norm=10.0, gamma=1.0, reward="deadline_loss_v1",
            epsilon_start=0.35, epsilon_end=0.02, epsilon_decay_s=15.0,
            target_update_interval=500,
            pigou_variant=spec.get("variant", "full"),
        )
        if "telemetry_sat" in spec:
            cfg["learning"]["pigou_telemetry_sat"] = int(spec["telemetry_sat"])
        if checkpoint is not None:
            if metadata is None or Path(metadata) != Path(checkpoint).with_name("metadata.json"):
                raise ValueError("pigou_online checkpoint requires sibling metadata.json")
            cfg["learning"].update(
                mode="eval", checkpoint_path=str(checkpoint), checkpoint_sha256=sha(checkpoint),
                checkpoint_metadata_sha256=sha(metadata), fixed_inference=True,
                epsilon_start=0.0, epsilon_end=0.0,
            )
        elif resume is not None:
            cfg["learning"].update(resume_path=str(resume),
                                   resume_sha256=sha(resume / "manifest.json"))
    elif alg == "Ours":
        if checkpoint is None:
            raise ValueError("Pigou evaluation/rollout requires a frozen checkpoint")
        options = {
            "kind": "pigou_metadrl", "checkpoint_path": str(checkpoint),
            "checkpoint_sha256": sha(checkpoint), "beta_ext": spec.get("beta_ext", 0.50),
            "temperature": spec.get("temperature", 0.08), "shield": spec.get("shield", True),
            "argmax": spec.get("argmax", False), "capture": spec.get("capture", False),
            "exploration": spec.get("exploration", 0.10),
        }
        for name in ("telemetry_sat", "hop_guard", "hop_penalty"):
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
            warmup_steps=spec.get("warmup_steps", 128), batch_size=32,
            train_every_steps=spec.get("train_every_steps", 32),
            lr=0.001, clip_norm=10.0, gamma=1.0, reward="deadline_loss_v1",
            epsilon_start=0.35, epsilon_end=0.02, epsilon_decay_s=15.0,
            target_update_interval=500,
        )
        if checkpoint is not None:
            if metadata is None or Path(metadata) != Path(checkpoint).with_name("metadata.json"):
                raise ValueError("learning checkpoint requires sibling metadata.json")
            cfg["learning"].update(
                mode="eval", checkpoint_path=str(checkpoint), checkpoint_sha256=sha(checkpoint),
                checkpoint_metadata_sha256=sha(metadata), fixed_inference=True,
                epsilon_start=0.0, epsilon_end=0.0,
            )
        elif resume is not None:
            cfg["learning"].update(resume_path=str(resume),
                                   resume_sha256=sha(resume / "manifest.json"))
    if spec.get("outage"):
        edges = CORRIDOR_EDGES_BY_SCENE.get(str(spec.get("scene", "A")).upper(), CORRIDOR_EDGES)
        cfg["links"]["scheduled_isl_outages"] = [
            {"a": min(a, b), "b": max(a, b), "start_s": 2.0, "end_s": 3.5}
            for a, b in edges
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
    retried_pids = {
        int(event["pid"]) for event in results.get("packet_events", [])
        if event.get("kind") == "retry_reinject"
    }
    first_attempt_success = sum(1 for pid in success if pid not in retried_pids)
    tx_goodput = len(success) / len(rows)
    return {
        "pdr": tx_goodput,
        "tx_goodput": tx_goodput,
        "first_attempt_pdr": first_attempt_success / len(rows),
        "phase_pdr": rates,
        "t_rec_onset_s": t_rec,
        "t_rec_censor_onset_s": recovery_end - burst_end - 0.8,
        "val_score": score,
    }


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
    """Apply the zero-queue graph with current-time nearest nominal egress.

    Egress selection uses only public ephemeris at each packet's deterministic
    simulation-time bucket.  Topology and egress caches are keyed by time so a
    packet emitted after a pass is never scored against the time-zero plan.
    """
    from collections import Counter

    from CODE.leo_sim import grid, model, routing

    sc = cfg["scenario"]
    lk = cfg["links"]
    geometry = model.Constellation(sc["num_satellites"], sc["num_planes"],
        sc["altitude_km"], sc["inclination_deg"], sc["min_elevation_deg"],
        max_isl_km=lk["max_isl_km"], geometry_epoch_s=sc["geometry_epoch_s"])
    egress_step = float(sc["time_step_s"])
    topology_step = float(cfg["topology"]["recompute_interval_s"])

    def bucket(now, step):
        return int(math.floor(float(now) / step))

    graph_cache = {}
    reverse_cache = {}
    egress_cache = {}
    distance_cache = {}
    by_od = Counter(
        (row["src_grid_id"], row["dst_grid_id"],
         bucket(row["emit_time_s"], egress_step))
        for row in rows
    )
    covered = total = 0
    for (src, dst, time_bucket), count in by_od.items():
        at = time_bucket * egress_step
        topo_bucket = bucket(at, topology_step)
        topo_at = topo_bucket * topology_step
        if topo_bucket not in graph_cache:
            graph = routing.build_topology(
                geometry, sc["num_satellites"], lk["isl_dirs"], topo_at)
            available = {
                sat: {direction: peer for direction, peer in ports.items()
                      if geometry.isl_available(sat, peer, topo_at)}
                for sat, ports in graph.items()
            }
            graph_cache[topo_bucket] = graph
            reverse_cache[topo_bucket] = routing._reverse_adj(available)
        graph = graph_cache[topo_bucket]
        reverse = reverse_cache[topo_bucket]

        def nearest_egress(cell):
            key = (time_bucket, cell)
            if key not in egress_cache:
                lat, lon = grid.grid_center(cell)
                visible = [
                    (geometry.slant_range_km(sat, lat, lon, at), sat)
                    for sat in graph
                    if geometry.ground_visible(sat, lat, lon, at)
                ]
                visible.sort(key=lambda item: (item[0], item[1]))
                egress_cache[key] = visible[0][1] if visible else None
            return egress_cache[key]

        src_sat = nearest_egress(src)
        dst_sat = nearest_egress(dst)
        if src_sat is None or dst_sat is None:
            continue
        distance_key = (topo_bucket, dst_sat)
        if distance_key not in distance_cache:
            distance_cache[distance_key] = routing._multi_source_bfs(
                reverse, [dst_sat])
        hops = distance_cache[distance_key].get(src_sat)
        if hops is not None:
            total += count * hops
            covered += count
    return {"hops": total if covered == len(rows) else None,
            "covered_packets": covered, "offered_packets": len(rows),
            "basis": "current_time_nearest_nominal_egress_min_hops"}


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
                                   interval=spec["interval"], offered_mbps=spec["offered_mbps"],
                                   scene=spec.get("scene", "A"))
    else:
        workload = build(out / "traffic", scene=spec["scene"], seed=spec["seed"],
                         interval=spec.get("interval", 0.05), multiplier=spec.get("multiplier", 2.5),
                         offered_mbps=spec.get("offered_mbps"),
                         duration_s=spec.get("duration_s"))
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
    if (spec["alg"] in LEARNERS or spec.get("online_ours")) \
            and not rcp["mechanisms"]["effective"]["learning"]:
        raise ValueError(f"{spec['alg']} made no train/eval decision updates under this workload")
    policy = result.get("local_forward_adapter")
    if policy and policy["calls"] == 0:
        raise ValueError("local forwarding adapter never handled a legal choice")
    telemetry_rows = (
        policy["telemetry"] if (policy and policy.get("telemetry"))
        else result.get("pigou_online_telemetry")
    )
    if telemetry_rows:
        with (sim / "telemetry.jsonl").open("x", encoding="utf-8") as stream:
            for row in telemetry_rows:
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
    offered_packets = len(rows)
    retry_reinjections = int(result.get("retry_reinjections", 0))
    load_amplification = (
        (offered_packets + retry_reinjections) / offered_packets
        if offered_packets else None)
    retry_rate_by_100ms = {}
    for event in result.get("retry_events", []):
        at = float(event["inject_at_s"])
        bucket = math.floor(at / 0.1) * 0.1
        key = f"{bucket:.1f}"
        retry_rate_by_100ms[key] = retry_rate_by_100ms.get(key, 0) + 1
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
               "retry_reinjections": retry_reinjections,
               "load_amplification": load_amplification,
               "retry_reinjections_by_100ms": retry_rate_by_100ms,
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
    if (spec["alg"] in LEARNERS or spec.get("online_ours")) and checkpoint is None:
        import tarfile
        algorithm = ("pigou_online" if spec.get("online_ours")
                     else ("primal_cvar" if spec["alg"] == "B6" else "ddqn"))
        learner = Path(summary["run_dir"]) / algorithm
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
    for alg in ("B1", "B2", "Collect"):
        summaries.append(simulate(root / alg, {**small, "alg": alg}))
    for alg in ("B6", "B4", "B5", "Ours"):
        online = alg == "Ours"
        train_spec = {**small, "alg": alg, "train_seed": 11, "train_every_steps": 32}
        if online:
            train_spec.update(online_ours=True, variant="full")
        train = simulate(root / alg, train_spec)
        artifact_name = ("pigou_online" if online
                         else ("primal_cvar" if alg == "B6" else "ddqn"))
        artifact = Path(train["run_dir"]) / artifact_name
        summaries.append(simulate(root / (alg + "-eval"), train_spec,
                                  checkpoint=artifact / "online.keras",
                                  metadata=artifact / "metadata.json"))
    if len({row["trace_sha256"] for row in summaries}) != 1:
        raise ValueError("preflight arms used different packet cohorts")
    b = simulate(root / "scene-b", {**small, "scene": "B", "alg": "B1"})
    c = simulate(root / "scene-c", {**small, "scene": "C", "alg": "B1"})
    c_ours = simulate(root / "scene-c-ours", {
        **small, "scene": "C", "alg": "Ours", "train_seed": 11,
        "train_every_steps": 8, "online_ours": True, "variant": "full",
    })
    c_ours_r2 = simulate(root / "scene-c-ours-r2", {
        **small, "scene": "C", "alg": "Ours", "train_seed": 11, "epoch": 2,
        "train_every_steps": 8, "online_ours": True, "variant": "full",
    }, resume=Path(c_ours["run_dir"]) / "pigou_online" / "resume")
    fault = simulate(root / "outage", {**small, "alg": "B2", "outage": True,
                                      "multiplier": 1.0})
    staircase = simulate(root / "staircase", {**small, "alg": "B1", "sweep": True,
                                             "multiplier": 1.0})
    summaries.extend((b, c, c_ours, c_ours_r2, fault, staircase))
    _json_write(root / "preflight.json", {
        "status": "passed", "tensorflow": tf.__version__,
        "online_ours": True, "arms": summaries,
    })


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
