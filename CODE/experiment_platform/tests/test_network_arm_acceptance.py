"""Synthetic event fixtures only: no simulation, training or historical writes."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

import pytest

from CODE.experiment_platform import network_arm_acceptance as acceptance
from CODE.experiment_platform import scripted_scenarios
from CODE.scripts.remote import release_protocol as protocol


@pytest.fixture(autouse=True)
def forbid_kernel(monkeypatch):
    from CODE.leo_sim import kernel

    def forbidden(*args, **kwargs):
        pytest.fail("acceptance/unit tests must not run the scientific kernel")

    monkeypatch.setattr(kernel, "run_simulation", forbidden)


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _seal(directory):
    """Make test-only receipts using the production protocol's hash helpers."""
    manifest = json.loads((directory / "run-manifest.json").read_text())
    keys = ("run_id", "release_id", "source_git_commit", "source_tree_sha256",
            "archive_sha256", "execution_class", "status", "exit_code")
    receipt = {key: manifest[key] for key in keys}
    receipt.update(schema=protocol.RUN_RECEIPT_SCHEMA,
                   run_manifest_sha256=protocol.sha256_file(directory / "run-manifest.json"),
                   files=protocol._hash_run_files(directory, exclude={"run-receipt.json"}))
    receipt["receipt_sha256"] = protocol.sha256_bytes(protocol.canonical_json(receipt))
    _write(directory / "run-receipt.json", receipt)


def _mutate(directory, name, change, *, reseal=True):
    path = directory / name
    value = json.loads(path.read_text())
    change(value)
    _write(path, value)
    if reseal:
        _seal(directory)


def _synthetic_run(directory, arm, penalty=0.0):
    """Small-field production-format records over the fixed 251-row input.

    These hand-created service records test reconciliation, not simulator
    fidelity or research performance. Known delays alternate 1 and 5 seconds.
    """
    directory.mkdir()
    resolved, rows, _, meta = scripted_scenarios.build(acceptance.SCENARIO, arm=arm)
    events, windows, decisions, outcomes, deliveries = [], [], [], [], {}
    delays = {}
    for index, row in enumerate(rows):
        pid, emit = row["packet_id"], row["emit_time_s"]
        source = meta["cells"][row["src_grid_id"]]["sat"]
        path = [0, 1, 3] if source == 0 else [source, 3]
        delay = (1.0 if index % 2 == 0 else 5.0) + penalty
        delays[pid] = delay
        deliveries[str(pid)] = {"delivered_at": emit + delay, "path": path}
        events.extend([
            {"kind": "packet_emitted", "pid": pid, "at": emit, "bits": row["bits"]},
            {"kind": "satellite_ingress", "pid": pid, "at": emit + .011,
             "bits": row["bits"], "endpoint": row["src_grid_id"], "satellite": source},
            {"kind": "delivered", "pid": pid, "at": emit + delay},
        ])
        links = [("uplink", f"gsl:uplink:{source}:{row['src_grid_id']}")]
        links += [("isl", f"isl:{a}:{b}") for a, b in zip(path, path[1:])]
        links += [("downlink", f"gsl:downlink:3:{row['dst_grid_id']}")]
        for hop, (stage, link) in enumerate(links):
            start = emit + hop * .1
            prop = pid * 10 + hop
            events.extend([
                {"kind": "queue_enter", "pid": pid, "at": start,
                 "queue_id": prop, "link_id": link},
                {"kind": "service_start", "pid": pid, "at": start,
                 "queue_id": prop, "link_id": link, "bits": row["bits"],
                 "stage": stage, "rate_bps": 10_000_000},
                {"kind": "propagation_start", "pid": pid, "at": start + .01,
                 "link_id": link, "stage": stage, "prop_id": prop, "delay_s": .001},
                {"kind": "propagation_arrival", "pid": pid, "at": start + .011,
                 "prop_id": prop},
            ])
            windows.append({"pid": pid, "link_id": link, "stage": stage,
                            "start": start, "end": start + .01, "served_bits": row["bits"]})
        for hop, sat in enumerate(path):
            chosen = (next(d for d, target in meta["topology"][str(sat)].items()
                           if target == path[hop + 1]) if hop + 1 < len(path) else "deliver")
            legal = ["E", "W"] if sat == 0 else [chosen]
            t0 = emit + .02 + hop * .1
            etas = {d: t0 + .15 * (index + 1) for index, d in enumerate(legal)}
            targets = {d: (t0 - .5 if arm == "stale" else t0 if arm == "now"
                           else sum(etas.values()) / len(etas) if arm == "common"
                           else etas[d]) for d in legal}
            decisions.append({"pid": pid, "sat": sat, "chosen": chosen,
                              "decision_id": index * 3 + hop,
                              "t_decision_start": t0,
                              "observation_at_start": {
                                  "legal_directions": legal,
                                  "time_alignment": None if chosen == "deliver" else {
                                      "arm": arm, "enabled": True,
                                      "execution_mode": "per_packet", "snapshot_at": t0,
                                      "eta_targets": etas, "query_targets": targets,
                                      "fallback_directions": [], "missing_directions": []}}})
        outcomes.append({"pid": pid, "bits": row["bits"], "emit_time_s": emit,
                         "delivery_time_s": emit + delay, "fate": "DELIVERED",
                         "in_population": True, "terminal_reason": None, "censor_reason": None,
                         "deadline_loss": {"value": min(delay / 4, 1)}})
    events.sort(key=lambda event: event["at"])
    replay = {"schema": "network-arm-replay/v1", "packet_events": events,
              "decision_rows": decisions, "timeline_rows": [{"at": 0, "milestone": "fixture"}],
              "link_service_windows": windows, "link_available_windows": [],
              "topology_trace": [{"at": 0, "topology": meta["topology"]}], "handover_events": [],
              "fates": {str(pid): "DELIVERED" for pid in delays}, "deliveries": deliveries,
              "control_totals": {"bits": {}, "counters": {}}}
    sorted_delays = sorted(delays.values())
    p95_index = (len(delays) - 1) * .95
    p95 = sorted_delays[math.floor(p95_index)] * (1 - p95_index % 1) + sorted_delays[math.ceil(p95_index)] * (p95_index % 1)
    primary = {"value": sum(min(d / 4, 1) for d in delays.values()) / len(delays),
               "deadline_s": 4, "status": "COMPUTED", "counts": {
                   "offered": 251, "delivered": 251, "admitted": 251, "offered_bits": 25_100_000},
               "e2e": {"mean_s": sum(delays.values()) / len(delays), "p95_s": p95},
               "censoring": {"packets": 0}}
    outcome = copy.deepcopy(primary)
    outcome.update(packet_outcomes=outcomes, deadline_primary_loss={"value": primary["value"]})
    identity = {"scenario": acceptance.SCENARIO, "arm": arm, "config_sha256": resolved["sha256"],
                "trace_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
                "trace_rows": 251, "time_alignment": resolved["config"]["time_alignment"]}
    document = {"schema": "network-arm-run/v1", "identity": identity,
                "context": {k: identity[k] for k in ("scenario", "arm", "config_sha256")},
                "primary_metric": primary, "network_outcome": outcome,
                "replay": {"published": True, **{key: len(replay[key]) for key in
                    ("decision_rows", "timeline_rows", "packet_events", "link_service_windows")}},
                "traffic_groups": {}}
    for name, ids in (("probe", [i for i in delays if i >= 400]),
                      ("background", [i for i in delays if i < 400])):
        document["traffic_groups"][name] = {"packets": len(ids), "with_loss": len(ids),
            "terminal_failures": 0, "mean_loss": sum(min(delays[i] / 4, 1) for i in ids) / len(ids)}
    # Distinct source commits are allowed but never silently certified equivalent.
    commit = str(acceptance.ARMS.index(arm) + 1) * 40
    manifest = {"schema": protocol.RUN_SCHEMA, "run_id": directory.name,
                "release_id": commit + "-" + "a" * 64, "source_git_commit": commit,
                "source_tree_sha256": "a" * 64, "archive_sha256": "b" * 64,
                "execution_class": "diagnostic", "status": "completed", "exit_code": 0,
                "finished_at": "2026-01-01T00:00:00+00:00", "inputs": [],
                "argv": ["python", "--scenario", acceptance.SCENARIO, "--arm", arm]}
    account = {"schema": "t1-run-accounting/v1", "exit_status": 0,
               "kernel_calls": {"count": 1, "calls": [{"rows": 251, "decision_sink": True,
                    "timeline_sink": True, "geometry": "scripted"}]}}
    for filename, value in (("run-manifest.json", manifest), ("run-accounting.json", account),
                            ("network-outcome.json", document), ("replay.json", replay)):
        _write(directory / filename, value)
    _seal(directory)
    return directory


@pytest.fixture
def four_runs(tmp_path):
    return {arm: _synthetic_run(tmp_path / arm, arm) for arm in acceptance.ARMS}


def test_accepts_complete_zero_effect_data_and_marks_boundary(four_runs):
    result = acceptance.accept_runs(four_runs)
    assert result["status"] == "ACCEPTED_DATA"
    assert result["claimable"] is False and result["formal"] is False
    assert result["scientific_source_equivalence"].startswith("NOT_CHECKED")
    assert result["paired_comparisons"]["candidate_minus_now"]["mean_D4_loss_change"] == 0
    assert result["runs"]["stale"]["metrics"]["D4_met"] == 126
    assert result["runs"]["stale"]["metrics"]["p95_delay_s"] == 5
    assert result["runs"]["stale"]["physical_terminal_loss_packets"] == 0
    assert {g: v["packets"] for g, v in result["runs"]["stale"]["groups"].items()} == {
        "all": 251, "background": 146, "probe": 105, "original_probe": 27, "new_probe": 78}


def test_negative_result_is_still_accepted_data(four_runs, tmp_path):
    four_runs["candidate"] = _synthetic_run(tmp_path / "candidate-negative", "candidate", penalty=.5)
    report = acceptance.accept_runs(four_runs)
    assert report["status"] == "ACCEPTED_DATA"
    assert report["paired_comparisons"]["candidate_minus_now"]["mean_D4_loss_change"] > 0


def test_uses_pid_and_source_action_not_decision_id(four_runs):
    _mutate(four_runs["candidate"], "replay.json",
            lambda x: [r.update(decision_id=r["decision_id"] + 9999) for r in x["decision_rows"]])
    pair = acceptance.accept_runs(four_runs)["paired_comparisons"]["candidate_minus_now"]
    assert pair["changed_source_action_pids"] == pair["changed_final_path_pids"] == []


@pytest.mark.parametrize("missing", acceptance.ARMS)
def test_missing_arm_is_rejected(four_runs, missing):
    del four_runs[missing]
    with pytest.raises(acceptance.AcceptanceError, match="exactly"):
        acceptance.accept_runs(four_runs)


def test_duplicate_run_directory_is_rejected(four_runs):
    four_runs["candidate"] = four_runs["now"]
    with pytest.raises(acceptance.AcceptanceError, match="distinct"):
        acceptance.accept_runs(four_runs)


def test_tampered_receipt_file_hash_is_rejected(four_runs):
    _mutate(four_runs["stale"], "replay.json", lambda x: x["packet_events"].pop(), reseal=False)
    with pytest.raises(acceptance.AcceptanceError, match="file set or hash"):
        acceptance.accept_runs(four_runs)


@pytest.mark.parametrize("filename,change,match", [
    ("run-manifest.json", lambda x: x.update(status="failed", exit_code=2), "complete successfully"),
    ("run-accounting.json", lambda x: x["kernel_calls"].update(count=2), "one successful"),
    ("network-outcome.json", lambda x: x["identity"].update(arm="common"), "explicit slot"),
    ("network-outcome.json", lambda x: x["identity"].update(scenario="net_h1"), "unsupported scenario"),
    ("network-outcome.json", lambda x: x["identity"].update(config_sha256="0"*64), "config hash"),
    ("network-outcome.json", lambda x: x["identity"].update(trace_sha256="0"*64), "trace hash"),
    ("network-outcome.json", lambda x: x["primary_metric"].update(value=.123), "primary D4"),
    ("network-outcome.json", lambda x: x["primary_metric"]["e2e"].update(p95_s=99), "p95"),
    ("network-outcome.json", lambda x: x["network_outcome"]["packet_outcomes"][0]["deadline_loss"].update(value=1), "packet outcome D4"),
    ("replay.json", lambda x: x["packet_events"].insert(0, copy.deepcopy(x["packet_events"][0])), "count mismatch"),
    ("replay.json", lambda x: next(e for e in x["packet_events"] if e["kind"] == "packet_emitted").update(bits=7), "emitted bits"),
    ("replay.json", lambda x: x["fates"].update({"200": "IN_SYSTEM_AT_STOP"}), "unsupported fate"),
    ("replay.json", lambda x: x["deliveries"]["200"].update(delivered_at=999), "delivery time"),
    ("replay.json", lambda x: x["deliveries"]["200"].update(path=[1, 2, 3]), "saved path"),
    ("replay.json", lambda x: x["decision_rows"][0]["observation_at_start"]["time_alignment"].update(arm="candidate"), "arm mismatch"),
    ("replay.json", lambda x: x["link_service_windows"][0].update(served_bits=1), "served bits"),
])
def test_semantic_corruption_is_rejected_even_with_valid_receipt(four_runs, filename, change, match):
    _mutate(four_runs["stale"], filename, change)
    with pytest.raises(acceptance.AcceptanceError, match=match):
        acceptance.accept_runs(four_runs)


def test_duplicate_emission_cannot_hide_behind_dict_overwrite(four_runs):
    directory = four_runs["stale"]
    def duplicate(x):
        event = copy.deepcopy(next(e for e in x["packet_events"] if e["kind"] == "packet_emitted"))
        x["packet_events"].insert(0, event)
    _mutate(directory, "replay.json", duplicate)
    _mutate(directory, "network-outcome.json",
            lambda x: x["replay"].update(packet_events=x["replay"]["packet_events"] + 1))
    with pytest.raises(acceptance.AcceptanceError, match="duplicate packet event"):
        acceptance.accept_runs(four_runs)


@pytest.mark.parametrize("corruption", ("current_time_instead_of_candidate_eta", "disabled", "wrong_snapshot"))
def test_correct_arm_label_cannot_hide_wrong_query_semantics(four_runs, corruption):
    def change(replay):
        row = next(r for r in replay["decision_rows"]
                   if len(r["observation_at_start"]["legal_directions"]) == 2)
        audit = row["observation_at_start"]["time_alignment"]
        if corruption == "current_time_instead_of_candidate_eta":
            audit["query_targets"] = {d: row["t_decision_start"] for d in audit["query_targets"]}
        elif corruption == "disabled":
            audit["enabled"] = False
        else:
            audit["snapshot_at"] += .5
    _mutate(four_runs["candidate"], "replay.json", change)
    with pytest.raises(acceptance.AcceptanceError, match="query|snapshot|enabled"):
        acceptance.accept_runs(four_runs)


def test_missing_delivery_rejected_even_with_consistent_declared_record_count(four_runs):
    directory = four_runs["stale"]
    def remove(x):
        index = next(i for i, e in enumerate(x["packet_events"]) if e["kind"] == "delivered")
        x["packet_events"].pop(index)
    _mutate(directory, "replay.json", remove)
    _mutate(directory, "network-outcome.json",
            lambda x: x["replay"].update(packet_events=x["replay"]["packet_events"] - 1))
    with pytest.raises(acceptance.AcceptanceError, match="missing emission/delivery/ingress"):
        acceptance.accept_runs(four_runs)


def _argv(run_dirs):
    return [value for arm, path in run_dirs.items() for value in ("--" + arm, str(path))]


def test_cli_defaults_to_stdout_without_changing_inputs(four_runs, tmp_path, capsys):
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert acceptance.main(_argv(four_runs)) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ACCEPTED_DATA"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_cli_writes_only_explicit_new_output_directory(four_runs, tmp_path, capsys):
    target = tmp_path / "reviewed-report"
    assert acceptance.main(_argv(four_runs) + ["--output-dir", str(target)]) == 0
    capsys.readouterr()
    assert {p.name for p in target.iterdir()} == {"acceptance.json", "metrics.csv"}
    assert len((target / "metrics.csv").read_text().splitlines()) == 21
    assert acceptance.main(_argv(four_runs) + ["--output-dir", str(target)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "REJECTED_DATA"


def test_cli_rejects_output_within_original_run(four_runs, capsys):
    assert acceptance.main(_argv(four_runs) + ["--output-dir", str(four_runs["stale"] / "new")]) == 2
    assert "source run" in json.loads(capsys.readouterr().out)["error"]


def test_cli_rejected_data_returns_nonzero_without_output(four_runs, tmp_path, capsys):
    _mutate(four_runs["candidate"], "run-manifest.json", lambda x: x.update(status="failed", exit_code=2))
    target = tmp_path / "not-created"
    assert acceptance.main(_argv(four_runs) + ["--output-dir", str(target)]) == 2
    assert "candidate" in json.loads(capsys.readouterr().out)["error"]
    assert not target.exists()
