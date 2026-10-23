"""Read-only acceptance of the named deterministic four-arm diagnostic.

Run with four explicit --stale/--now/--common/--candidate run directories.
A verified receipt proves integrity, not scientific correctness. This module
also rebuilds the offered input, reconciles packet events and recomputes
outcomes. It never calls the simulation kernel. Only the fully delivered
net_h1_restricted_rate4 condition is supported; other fates require a separate
reviewed acceptance contract. Preserve and report those original outcomes;
rejection here is not an algorithm failure verdict. Zero or negative benefits
still ACCEPTED_DATA.
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import itertools
import json
import math
import re
import statistics
from pathlib import Path

from CODE.experiment_platform import scripted_scenarios
from CODE.leo_sim.time_alignment import ARMS
from CODE.scripts.remote import release_protocol

SCENARIO = "net_h1_restricted_rate4"
SCHEMA = "network-arm-acceptance/v1"
DEADLINE_S = 4.0
REQUIRED_FILES = {"network-outcome.json", "replay.json", "run-accounting.json"}
REPLAY_LISTS = ("decision_rows", "timeline_rows", "packet_events",
                "link_service_windows", "link_available_windows",
                "topology_trace", "handover_events")


class AcceptanceError(ValueError):
    """An input failed the bounded acceptance contract."""


def _require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def _number(value, field):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool)
             and math.isfinite(value), f"{field}: expected finite number")
    return value


def _close(actual, expected, field):
    _require(math.isclose(_number(actual, field), expected,
                         rel_tol=1e-12, abs_tol=1e-9),
             f"{field}: {actual!r} does not match recomputed {expected!r}")


def _read(path):
    _require(path.is_file() and not path.is_symlink(),
             f"missing or unsafe input: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _unique(rows, field, label):
    result = {}
    for row in rows:
        key = row[field]
        _require(key not in result, f"duplicate {label}: {key}")
        result[key] = row
    return result


def _stats(delays):
    values = sorted(delays)
    _require(bool(values), "empty metric population")
    index = (len(values) - 1) * 0.95
    low = math.floor(index)
    p95 = values[low] + (index - low) * (values[math.ceil(index)] - values[low])
    return {"packets": len(values), "mean_delay_s": statistics.mean(values),
            "p95_delay_s": p95, "mean_D4_loss": statistics.mean(
                min(delay / DEADLINE_S, 1.0) for delay in values),
            "D4_met": sum(delay <= DEADLINE_S for delay in values),
            "D4_exceeded": sum(delay > DEADLINE_S for delay in values)}


def _check_query_semantics(row, audit, legal, arm):
    """An arm label alone cannot prove that its query-time rule was applied."""
    _require(audit.get("enabled") is True and audit.get("execution_mode") == "per_packet",
             "time alignment must be enabled in per_packet mode")
    t0 = _number(row["t_decision_start"], "decision start")
    _close(audit["snapshot_at"], t0, "snapshot time")
    etas, queries = audit["eta_targets"], audit["query_targets"]
    _require(set(etas) == set(queries) == set(legal), "query/ETA candidate population mismatch")
    for direction in legal:
        _require(_number(etas[direction], "ETA") >= t0, "ETA precedes snapshot")
        target = _number(queries[direction], "query target")
        if arm == "stale":
            _require(target <= t0, "stale query target is in the future")
        else:
            expected = (t0 if arm == "now" else statistics.median(etas.values())
                        if arm == "common" else etas[direction])
            _close(target, expected, f"{arm} query target {direction}")


def _recompute(replay, rows, meta, arm):
    """Reconcile actual packet lifecycle and reconstruct routes from sends."""
    offered = _unique(rows, "packet_id", "offered PID")
    pids = set(offered)
    events = replay["packet_events"]
    seen = set()
    last_at = -math.inf
    for event in events:
        encoded = json.dumps(event, sort_keys=True)
        _require(encoded not in seen, "duplicate packet event")
        seen.add(encoded)
        at = _number(event["at"], "packet event time")
        _require(at >= last_at, "packet events are not time ordered")
        last_at = at
        _require(event["pid"] in pids, "packet event PID absent from offered input")
    emitted = _unique([e for e in events if e["kind"] == "packet_emitted"],
                      "pid", "emission")
    delivered = _unique([e for e in events if e["kind"] == "delivered"],
                        "pid", "delivery")
    ingress = _unique([e for e in events if e["kind"] == "satellite_ingress"],
                      "pid", "satellite ingress")
    _require(set(emitted) == set(delivered) == set(ingress) == pids,
             "missing emission/delivery/ingress or unsupported non-delivered fate; "
             "preserve and report original outcomes; this is not an algorithm-effect verdict")
    _require(set(replay["fates"]) == set(replay["deliveries"]) == {str(i) for i in pids},
             "fates/deliveries PID population mismatch")
    paths, delays = {}, {}
    for pid, row in offered.items():
        _close(emitted[pid]["at"], row["emit_time_s"], f"PID {pid} emission time")
        _close(emitted[pid]["bits"], row["bits"], f"PID {pid} emitted bits")
        _require(row["deadline_at_s"] is None, "physical deadline dropping unsupported")
        _require(replay["fates"][str(pid)] == "DELIVERED",
                 f"PID {pid}: unsupported fate (not a D4 failure)")
        record = replay["deliveries"][str(pid)]
        _close(record["delivered_at"], delivered[pid]["at"], f"PID {pid} delivery time")
        delays[pid] = delivered[pid]["at"] - emitted[pid]["at"]
        _require(delays[pid] >= 0, "negative packet delay")
        source_sat = meta["cells"][row["src_grid_id"]]["sat"]
        _require(ingress[pid]["satellite"] == source_sat, "source satellite mismatch")
        _require(ingress[pid]["endpoint"] == row["src_grid_id"], "source endpoint mismatch")
        paths[pid] = [source_sat]

    # Pair each service with its propagation and arrival. Rebuild ISL paths
    # from physical sends, then compare the saved delivery path and decisions.
    starts, propagating, service_keys = {}, {}, set()
    queue_entries, windows = {}, {}
    service_counts = {pid: 0 for pid in pids}
    isl_sends = 0
    for event in events:
        pid, kind, at = event["pid"], event["kind"], event["at"]
        if kind == "queue_enter" and event.get("queue_id") is not None:
            key = (pid, event["queue_id"])
            _require(key not in queue_entries, "duplicate queue identifier for packet")
            queue_entries[key] = at
        elif kind == "service_start":
            key = (pid, event["link_id"])
            _require(key not in service_keys, "duplicate physical service for packet/link")
            service_keys.add(key)
            _require((pid, event["queue_id"]) in queue_entries, "service has no queue entry")
            _require(at >= queue_entries[(pid, event["queue_id"])], "service precedes queue entry")
            _close(event["bits"], offered[pid]["bits"], "service bits")
            starts[key] = event
            service_counts[pid] += 1
            if event["stage"] == "isl":
                parts = event["link_id"].split(":")
                _require(len(parts) == 3 and parts[0] == "isl", "invalid ISL link")
                source, target = map(int, parts[1:])
                _require(paths[pid][-1] == source, "physical ISL path is discontinuous")
                paths[pid].append(target)
                isl_sends += 1
        elif kind == "propagation_start":
            key = (pid, event["link_id"])
            _require(key in starts, "propagation has no unique service start")
            start = starts.pop(key)
            _require(start["stage"] == event["stage"], "service/propagation stage mismatch")
            _require(at >= start["at"], "negative service duration")
            prop = (pid, event["prop_id"])
            _require(prop not in propagating, "duplicate propagation identifier")
            propagating[prop] = event
            windows[key] = (start, event)
        elif kind == "propagation_arrival":
            key = (pid, event["prop_id"])
            _require(key in propagating, "arrival has no unique propagation start")
            start = propagating.pop(key)
            _close(at - start["at"], start["delay_s"], "propagation duration")
    _require(not starts and not propagating, "incomplete physical service/propagation")
    _require(isl_sends == meta["declared"]["expected_isl_transmissions"],
             "ISL transmission count differs from declared contract")
    window_keys = set()
    for window in replay["link_service_windows"]:
        key = (window["pid"], window["link_id"])
        _require(key not in window_keys and key in windows, "service-window population mismatch")
        window_keys.add(key)
        start, end = windows[key]
        _close(window["start"], start["at"], "window start")
        _close(window["end"], end["at"], "window end")
        _close(window["served_bits"], start["bits"], "window served bits")
    _require(window_keys == set(windows), "missing physical service window")

    decisions, dual = {}, set()
    for row in replay["decision_rows"]:
        pid, sat = row["pid"], row["sat"]
        _require(pid in pids, "decision PID absent from offered input")
        key = (pid, sat)
        _require(key not in decisions, "duplicate packet/satellite decision")
        decisions[key] = row
        observation = row["observation_at_start"]
        legal = observation["legal_directions"]
        _require(row["chosen"] in legal, "chosen action is not legal")
        if len(legal) == 2:
            _require(pid not in dual and sat == paths[pid][0],
                     "duplicate or non-source dual-exit decision")
            dual.add(pid)
        audit = observation.get("time_alignment")
        if row["chosen"] != "deliver":
            _require(isinstance(audit, dict) and audit.get("arm") == arm,
                     "decision time-alignment arm mismatch or missing audit")
            if len(legal) == 2:
                _require(not audit.get("fallback_directions") and not audit.get("missing_directions"),
                         "dual-exit decision has fallback or missing information")
                _check_query_semantics(row, audit, legal, arm)
    probes = {i for i in pids if i >= 400}
    _require(dual == probes and len(dual) == meta["declared"]["expected_dual_exit_opportunities"],
             "true dual-exit source population differs from contract")
    source_actions = {}
    expected_decisions = set()
    for pid, path in paths.items():
        _require(path == replay["deliveries"][str(pid)]["path"], "saved path differs from physical sends")
        _require(len(path) == (3 if pid in probes else 2), "path violates restricted-route condition")
        _require(service_counts[pid] == len(path) + 1, "uplink/ISL/downlink service count mismatch")
        _require(path[-1] == meta["cells"][offered[pid]["dst_grid_id"]]["sat"],
                 "delivered path ends at wrong destination")
        for index, sat in enumerate(path):
            key = (pid, sat)
            expected_decisions.add(key)
            _require(key in decisions, "missing packet path decision")
            chosen = decisions[key]["chosen"]
            if index + 1 < len(path):
                _require(meta["topology"][str(sat)].get(chosen) == path[index + 1],
                         "decision action differs from physical path")
            else:
                _require(chosen == "deliver", "missing terminal downlink decision")
        source_actions[pid] = decisions[(pid, path[0])]["chosen"]
    _require(set(decisions) == expected_decisions, "extra decision outside physical paths")
    groups = {"all": pids, "background": pids - probes, "probe": probes,
              "original_probe": pids & set(range(400, 427)),
              "new_probe": pids & set(range(600, 678))}
    return {"metrics": _stats(delays.values()),
            "groups": {name: _stats(delays[i] for i in ids) for name, ids in groups.items()},
            "isl_transmissions": isl_sends, "dual_exit_opportunities": len(dual),
            "physical_terminal_loss_packets": 0, "censored_packets": 0,
            "packets": {str(i): {"delay_s": delays[i], "D4_loss": min(delays[i] / 4.0, 1.0),
                                    "source_action": source_actions[i], "path": paths[i]}
                        for i in sorted(pids)}}


def _check_summary(document, result, rows):
    metric = document["primary_metric"]
    outcome = document["network_outcome"]
    _close(metric["deadline_s"], DEADLINE_S, "declared deadline")
    _require(metric["status"] == "COMPUTED", "primary metric not computed")
    _close(metric["value"], result["metrics"]["mean_D4_loss"], "primary D4 loss")
    _close(outcome["deadline_primary_loss"]["value"], metric["value"], "outcome D4 loss")
    for block in (metric, outcome):
        for field in ("offered", "delivered", "admitted"):
            _require(block["counts"][field] == len(rows), f"summary {field} count mismatch")
        _close(block["counts"]["offered_bits"], sum(r["bits"] for r in rows), "offered bits")
        _close(block["e2e"]["mean_s"], result["metrics"]["mean_delay_s"], "summary mean delay")
        _close(block["e2e"]["p95_s"], result["metrics"]["p95_delay_s"], "summary p95 delay")
        _require(block["censoring"]["packets"] == 0, "summary claims censored packets")
    packets = _unique(outcome["packet_outcomes"], "pid", "outcome PID")
    _require(set(packets) == {r["packet_id"] for r in rows}, "packet-outcome population mismatch")
    for row in rows:
        packet, observed = packets[row["packet_id"]], result["packets"][str(row["packet_id"])]
        _require(packet["fate"] == "DELIVERED" and packet["in_population"] is True
                 and not packet["terminal_reason"] and not packet["censor_reason"],
                 "packet outcome fate/population mismatch")
        _close(packet["emit_time_s"], row["emit_time_s"], "packet outcome emission")
        _close(packet["bits"], row["bits"], "packet outcome bits")
        _close(packet["delivery_time_s"] - row["emit_time_s"], observed["delay_s"], "packet outcome delay")
        _close(packet["deadline_loss"]["value"], observed["D4_loss"], "packet outcome D4 loss")
    for name in ("background", "probe"):
        saved, actual = document["traffic_groups"][name], result["groups"][name]
        _require(saved["packets"] == saved["with_loss"] == actual["packets"]
                 and saved["terminal_failures"] == 0, "summary traffic group count mismatch")
        _close(saved["mean_loss"], actual["mean_D4_loss"], "summary group D4 loss")


def validate_run(directory, arm):
    """One run's verified identity and event reconstruction (not a full set)."""
    directory = Path(directory)
    receipt = release_protocol.verify_run_directory(directory, expected_run_id=directory.name)
    manifest = _read(directory / "run-manifest.json")
    _require(manifest["status"] == "completed" and manifest["exit_code"] == 0,
             "run did not complete successfully")
    _require(manifest["execution_class"] == "diagnostic", "only diagnostic execution is supported")
    _require(re.fullmatch(r"[0-9a-f]{40}", manifest["source_git_commit"]) is not None,
             "missing full source commit identity")
    _require(REQUIRED_FILES <= {item["path"] for item in receipt["files"]},
             "receipt does not bind outcome, replay and accounting")
    account = _read(directory / "run-accounting.json")
    calls = account["kernel_calls"]
    _require(account["schema"] == "t1-run-accounting/v1" and account["exit_status"] == 0
             and calls["count"] == 1 and len(calls["calls"]) == 1,
             "accounting must show exactly one successful kernel invocation")
    call = calls["calls"][0]
    _require(call["rows"] == 251 and call["decision_sink"] is True
             and call["timeline_sink"] is True and call["geometry"] == "scripted",
             "kernel accounting input/recording mismatch")
    document = _read(directory / "network-outcome.json")
    identity = document["identity"]
    _require(document["schema"] == "network-arm-run/v1", "unsupported outcome schema")
    _require(identity["scenario"] == SCENARIO, "unsupported scenario")
    _require(identity["arm"] == arm, "run arm differs from explicit slot")
    argv = manifest["argv"]
    for flag, expected in (("--arm", arm), ("--scenario", SCENARIO)):
        _require(argv.count(flag) == 1 and argv[argv.index(flag) + 1] == expected,
                 f"manifest {flag} mismatch")
    resolved, rows, _, meta = scripted_scenarios.build(SCENARIO, arm=arm)
    _require(len(rows) == 251 and sum(r["bits"] for r in rows) == 25_100_000,
             "supported scenario declaration has changed")
    trace_hash = hashlib.sha256(json.dumps(rows, sort_keys=True).encode("utf-8")).hexdigest()
    _require(identity["config_sha256"] == resolved["sha256"], "config hash mismatch")
    _require(identity["trace_sha256"] == trace_hash and identity["trace_rows"] == len(rows),
             "trace hash or row count mismatch")
    _require(identity["time_alignment"] == resolved["config"]["time_alignment"],
             "declared time-alignment config mismatch")
    for field in ("scenario", "arm", "config_sha256"):
        _require(document["context"][field] == identity[field], "outcome context identity mismatch")
    replay = _read(directory / "replay.json")
    _require(replay["schema"] == "network-arm-replay/v1", "unsupported replay schema")
    for field in REPLAY_LISTS:
        _require(isinstance(replay[field], list), f"missing replay list {field}")
    _require(replay["timeline_rows"] and replay["topology_trace"], "incomplete replay timeline/topology")
    _require(isinstance(replay["control_totals"], dict), "missing replay control totals")
    _require(document["replay"]["published"] is True, "full replay not published")
    for field in ("decision_rows", "timeline_rows", "packet_events", "link_service_windows"):
        _require(document["replay"][field] == len(replay[field]), f"replay {field} count mismatch")
    result = _recompute(replay, rows, meta, arm)
    _check_summary(document, result, rows)
    result["identity"] = {key: manifest[key] for key in
                          ("run_id", "release_id", "source_git_commit", "source_tree_sha256")}
    result["identity"].update({"receipt_sha256": receipt["receipt_sha256"],
                               "config_sha256": resolved["sha256"], "trace_sha256": trace_hash})
    config = copy.deepcopy(resolved["config"])
    config["time_alignment"].pop("arm")
    return result, config, rows


def accept_runs(run_dirs):
    """Fail closed unless exactly the four named run directories reconcile."""
    _require(set(run_dirs) == set(ARMS), "exactly stale/now/common/candidate are required")
    _require(len({Path(p).resolve() for p in run_dirs.values()}) == 4, "run directories must be distinct")
    runs, configs, inputs = {}, [], []
    for arm in ARMS:
        try:
            result, config, rows = validate_run(run_dirs[arm], arm)
        except (ValueError, KeyError, IndexError, TypeError, OSError) as exc:
            raise AcceptanceError(f"{arm}: {exc}") from exc
        runs[arm] = result
        configs.append(config)
        inputs.append(rows)
    _require(all(c == configs[0] for c in configs), "configs differ beyond arm")
    _require(all(rows == inputs[0] for rows in inputs), "offered inputs differ between arms")
    pairs = {}
    for first, second in itertools.combinations(ARMS, 2):
        before, after = runs[first]["packets"], runs[second]["packets"]
        pairs[f"{second}_minus_{first}"] = {
            "mean_D4_loss_change": runs[second]["metrics"]["mean_D4_loss"] - runs[first]["metrics"]["mean_D4_loss"],
            "mean_delay_s_change": runs[second]["metrics"]["mean_delay_s"] - runs[first]["metrics"]["mean_delay_s"],
            "changed_source_action_pids": [int(i) for i in before if before[i]["source_action"] != after[i]["source_action"]],
            "changed_final_path_pids": [int(i) for i in before if before[i]["path"] != after[i]["path"]],
            "delay_improved_packets": sum(after[i]["delay_s"] < before[i]["delay_s"] - 1e-9 for i in before),
            "delay_worsened_packets": sum(after[i]["delay_s"] > before[i]["delay_s"] + 1e-9 for i in before),
        }
    return {"schema": SCHEMA, "status": "ACCEPTED_DATA", "claimable": False,
            "execution_class": "diagnostic", "formal": False, "scenario": SCENARIO,
            "validator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "input_identity": {"configs_differ_only_in_arm": True, "offered_rows_identical": True},
            "scientific_source_equivalence": "NOT_CHECKED: external exact source diff review is required",
            "boundaries": ["Named deterministic, fully delivered diagnostic only; not a general paper platform.",
                           "D4 is capped delay after the run, not physical packet loss.",
                           "Zero/negative gain is accepted data, not evidence of benefit.",
                           "Packet events, services, paths and outcomes reconciled offline; no simulation was executed.",
                           "Non-delivery, terminal loss and censoring are outside this bounded contract. "
                           "Preserve/report their raw outcomes; rejection is not an algorithm-effect verdict.",
                           "Timeline/topology/control artifacts are receipt-bound; this is not full kernel re-execution.",
                           "Source-action and final-path changes are paired by PID, never decision_id.",
                           "No confidence interval, multi-seed claim, counterfactual causal effect or formal authorization."],
            "runs": runs, "paired_comparisons": pairs}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for arm in ARMS:
        parser.add_argument("--" + arm, type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, help="new directory outside the original run inputs")
    args = parser.parse_args(argv)
    run_dirs = {arm: getattr(args, arm) for arm in ARMS}
    try:
        if args.output_dir is not None:
            target = args.output_dir.resolve()
            _require(not args.output_dir.exists(), "output directory already exists; refusing overwrite")
            _require(all(target != p.resolve() and p.resolve() not in target.parents for p in run_dirs.values()),
                     "output directory may not be inside a source run")
        report = accept_runs(run_dirs)
        if args.output_dir is not None:
            args.output_dir.mkdir(parents=True, exist_ok=False)
            (args.output_dir / "acceptance.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            with (args.output_dir / "metrics.csv").open("x", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=["arm", "group", *report["runs"]["stale"]["metrics"]])
                writer.writeheader()
                for arm in ARMS:
                    for group, metrics in report["runs"][arm]["groups"].items():
                        writer.writerow({"arm": arm, "group": group, **metrics})
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except (ValueError, KeyError, IndexError, TypeError, OSError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "REJECTED_DATA", "claimable": False,
                          "formal": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
