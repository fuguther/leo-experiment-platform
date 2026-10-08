from __future__ import annotations

import pytest
import hashlib
import json
from pathlib import Path

from CODE.experiment_platform import population_run_acceptance as acceptance


def _trace_rows():
    return [
        {"packet_id": 1, "emit_time_s": 2.0, "src_grid_id": "A",
         "dst_grid_id": "B", "bits": 12000},
        {"packet_id": 2, "emit_time_s": 3.0, "src_grid_id": "B",
         "dst_grid_id": "C", "bits": 12000},
        {"packet_id": 3, "emit_time_s": 4.0, "src_grid_id": "C",
         "dst_grid_id": "A", "bits": 12000},
    ]


def _arm():
    return {
        "arm": "stale",
        "scope": {"packets_in_trace": 3},
        "stop_time_s": 8.0,
        "horizon_s": 8.0,
        "outcome": {
            "offered": 3,
            "delivered": 2,
            "fate_counts": {"DELIVERED": 2, "IN_SYSTEM_AT_STOP": 1},
            "e2e_samples": 2,
            "e2e_mean_s": 2.0,
            "e2e_p95_s": 2.9,
        },
        "network_outcome": {
            "counts": {"offered": 3, "delivered": 2},
            "deadline_primary_loss": {
                "status": "COMPUTED",
                "deadline_s": 4.0,
                "packets": 3,
                "exact_packets": 3,
                "interval_censored": 0,
                "not_computable_packets": 0,
                "value": 2.0 / 3.0,
            },
        },
        "replay": {
            "captured": True,
            "arm": "stale",
            "stop_time_s": 8.0,
            "horizon_s": 8.0,
            "packet_events": [
                {"kind": "packet_emitted", "pid": 1, "at": 2.0,
                 "bits": 12000},
                {"kind": "packet_emitted", "pid": 2, "at": 3.0,
                 "bits": 12000},
                {"kind": "packet_emitted", "pid": 3, "at": 4.0,
                 "bits": 12000},
                {"kind": "delivered", "pid": 1, "at": 3.0},
                {"kind": "delivered", "pid": 3, "at": 7.0},
            ],
            "fates": {"1": "DELIVERED", "2": "IN_SYSTEM_AT_STOP",
                      "3": "DELIVERED"},
            "deliveries": {"1": {"delivered_at": 3.0},
                           "3": {"delivered_at": 7.0}},
        },
    }


def test_recomputes_d4_and_delivered_latency_without_treating_censor_as_fate_loss():
    result = acceptance.recompute_arm(
        _arm(), _trace_rows(), deadline_s=4.0,
        population_window=(2.0, 8.0), expected_stop_s=8.0)

    assert result["counts"] == {
        "offered": 3, "delivered": 2, "DELIVERED": 2,
        "IN_SYSTEM_AT_STOP": 1,
    }
    assert result["deadline_primary_loss"] == {
        "status": "COMPUTED", "packets": 3, "exact_packets": 3,
        "interval_censored": 0, "not_computable_packets": 0,
        "value": pytest.approx(2.0 / 3.0),
        "lower_mean": pytest.approx(2.0 / 3.0),
        "upper_mean": pytest.approx(2.0 / 3.0),
    }
    assert result["trace_emissions_match"] is True
    assert result["delivered_latency"]["packets"] == 2
    assert result["delivered_latency"]["mean_s"] == pytest.approx(2.0)
    assert result["delivered_latency"]["p95_s"] == pytest.approx(2.9)


def test_censor_before_deadline_retains_an_interval_instead_of_zero():
    result = acceptance.deadline_loss(
        "IN_SYSTEM_AT_STOP", emit_time_s=2.0, delivered_at_s=None,
        stop_time_s=5.0, deadline_s=4.0)

    assert result == {
        "status": "INTERVAL_CENSORED", "value": None,
        "lower_bound": pytest.approx(0.75), "upper_bound": 1.0,
    }


@pytest.mark.parametrize("mutation, message", [
    ("duplicate_emit", "duplicate packet_emitted"),
    ("missing_fate", "trace/fate packet set mismatch"),
    ("duplicate_delivery", "duplicate delivered event"),
    ("delivery_time", "delivery event and record differ"),
    ("negative_time", "outside observation interval"),
    ("nonfinite_time", "must be finite"),
])
def test_rejects_inconsistent_packet_replay(mutation, message):
    arm = _arm()
    events = arm["replay"]["packet_events"]
    if mutation == "duplicate_emit":
        events.append(dict(events[0]))
    elif mutation == "missing_fate":
        arm["replay"]["fates"].pop("2")
    elif mutation == "duplicate_delivery":
        events.append({"kind": "delivered", "pid": 1, "at": 3.0})
    elif mutation == "delivery_time":
        arm["replay"]["deliveries"]["1"]["delivered_at"] = 3.1
    elif mutation == "negative_time":
        events[0]["at"] = -0.1
    elif mutation == "nonfinite_time":
        events[0]["at"] = float("inf")

    with pytest.raises(acceptance.PopulationRunAcceptanceError,
                       match=message):
        acceptance.recompute_arm(
            arm, _trace_rows(), deadline_s=4.0,
            population_window=(2.0, 8.0), expected_stop_s=8.0)


def test_cli_rejects_summary_only_run_and_writes_nonclaimable_rejection(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "official-run-01"
    dev = run_dir / "t1-development"
    cell_id = "b-bounded_population_cost_smoke-network-seed-7"
    cell_dir = dev / "b_dev" / "cells" / cell_id
    config_rel = "configs/seed-7-b-bounded_population_cost_smoke-seed-7.yaml"
    config_path = dev / "bundle" / config_rel
    config_path.parent.mkdir(parents=True)
    config_path.write_text("scenario: {}\n", encoding="utf-8")
    profile_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    raster = run_dir / "inputs" / "population_raster"
    raster.parent.mkdir(parents=True)
    raster.write_bytes(b"receipt-bound raster fixture")
    raster_sha = hashlib.sha256(raster.read_bytes()).hexdigest()
    remote_config = f"/release/bundle/{config_rel}"
    remote_raster = "/run/inputs/population_raster"
    predicate = {"kind": "t1_task", "require": {
        "require_task": "network_alignment",
        "arms": ["stale", "now", "common", "candidate"],
        "require_native_population_trace": True,
        "require_full_replay": True,
        "require_routing_audit_log": True,
        "population_sha256": raster_sha,
        "min_decisions_per_arm": 1,
        "min_satellites": 1,
    }}
    cell = {
        "cell_id": cell_id,
        "driver": "CODE.experiment_platform.t1_tasks",
        "group": "b_round",
        "seed": 7,
        "args": ["--task", "network_alignment", "--config", remote_config,
                 "--deadline-s", "4.0", "--window-start", "2.0",
                 "--window-end", "8.0", "--arms",
                 "stale,now,common,candidate", "--capture-replay"],
        "input": {
            "config_identity": f"bundle:{config_rel}",
            "config_path": remote_config,
            "config_sha256": profile_sha,
            "files": {
                "profile_config": {"identity": f"bundle:{config_rel}",
                                   "path": remote_config,
                                   "sha256": profile_sha},
                "population_path": {
                    "identity": f"source:{acceptance.PROFILE_POPULATION_PATH}",
                    "path": remote_raster, "sha256": raster_sha,
                },
            },
        },
        "predicate": predicate,
    }
    bundle = {
        "bundle_fingerprint": "bundle-fixture",
        "identity": {"git": {"commit": "a" * 40}},
        "cells": [cell],
    }
    bundle_path = dev / "bundle" / "bundle.json"
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    record = {
        "cell_id": cell_id, "status": "ok", "returncode": 0,
        "child_returncode": 0,
        "result_path": f"cells/{cell_id}/result.json",
        "argv": ["/python", "-m", "CODE.experiment_platform.t1_tasks",
                 *cell["args"], "--out",
                 f"/run/t1-development/b_dev/cells/{cell_id}/result.json"],
        "result_sha256": "b" * 64, "result_schema": "t1-task-result/v1",
        "input": cell["input"],
        "predicate": predicate,
        "predicate_verdict": {"passed": True, "checks": []},
    }
    cell_dir.mkdir(parents=True)
    (cell_dir / "cell.json").write_text(json.dumps(record), encoding="utf-8")
    (cell_dir / "result-summary.json").write_text(
        json.dumps({"schema": "t1-network-summary/v1"}), encoding="utf-8")
    run_doc = {
        "schema": "t1-suite-run/v1", "tier": "b_dev", "status": "ok",
        "bundle_fingerprint": "bundle-fixture", "cells": [record],
    }
    bdev = dev / "b_dev"
    (bdev / "run.json").write_text(json.dumps(run_doc), encoding="utf-8")
    manifest = {
        "schema": "leo-release-run/v1", "run_id": run_dir.name,
        "release_id": "release-fixture", "source_git_commit": "a" * 40,
        "status": "completed", "exit_code": 0,
        "execution_class": "development",
        "inputs": [{"name": "population_raster",
                    "snapshot_path": "inputs/population_raster",
                    "sha256": raster_sha}],
    }
    (run_dir / "run-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        acceptance.release_protocol, "verify_run_directory",
        lambda path, expected_run_id: {
            "receipt_sha256": "c" * 64,
            "source_git_commit": "a" * 40,
            "files": [
                {"path": "run-manifest.json", "sha256": "d" * 64},
                {"path": "inputs/population_raster", "sha256": raster_sha},
                {"path": "t1-development/bundle/bundle.json",
                 "sha256": hashlib.sha256(bundle_path.read_bytes()).hexdigest()},
                {"path": f"t1-development/bundle/{config_rel}",
                 "sha256": profile_sha},
                {"path": f"t1-development/b_dev/run.json",
                 "sha256": hashlib.sha256((bdev / "run.json").read_bytes()).hexdigest()},
                {"path": f"t1-development/b_dev/cells/{cell_id}/cell.json",
                 "sha256": hashlib.sha256((cell_dir / "cell.json").read_bytes()).hexdigest()},
                {"path": f"t1-development/b_dev/cells/{cell_id}/result-summary.json",
                 "sha256": hashlib.sha256((cell_dir / "result-summary.json").read_bytes()).hexdigest()},
            ],
        })

    output = tmp_path / "acceptance.json"
    status = acceptance.main(["--run-dir", str(run_dir), "--out", str(output)])

    assert status != 0
    rejection = json.loads(output.read_text(encoding="utf-8"))
    assert rejection["status"] == "REJECTED"
    assert rejection["claimable"] is False
    assert any("result.json" in reason for reason in rejection["reasons"])


@pytest.mark.parametrize("through_symlink", [False, True])
def test_cli_never_writes_rejection_into_source_run(
        tmp_path, monkeypatch, through_symlink):
    run_dir = tmp_path / "source-run"
    run_dir.mkdir()
    if through_symlink:
        output_parent = tmp_path / "run-alias"
        output_parent.symlink_to(run_dir, target_is_directory=True)
    else:
        output_parent = run_dir
    output = output_parent / "acceptance.json"
    calls = []
    monkeypatch.setattr(acceptance, "accept_run",
                        lambda path: calls.append(path))

    status = acceptance.main([
        "--run-dir", str(run_dir), "--out", str(output),
    ])

    assert status != 0
    assert calls == []
    assert not (run_dir / "acceptance.json").exists()


def test_cli_rejects_existing_output_before_inspecting_run(tmp_path, monkeypatch):
    run_dir = tmp_path / "source-run"
    run_dir.mkdir()
    output = tmp_path / "existing.json"
    output.write_text('{"preserve":true}\n', encoding="utf-8")
    calls = []
    monkeypatch.setattr(acceptance, "accept_run",
                        lambda path: calls.append(path))

    status = acceptance.main([
        "--run-dir", str(run_dir), "--out", str(output),
    ])

    assert status != 0
    assert calls == []
    assert output.read_text(encoding="utf-8") == '{"preserve":true}\n'


def test_rebuild_trace_uses_only_receipt_bound_profile_and_raster_snapshots(
        tmp_path, monkeypatch):
    run_dir = tmp_path / "official-run-01"
    dev_root = run_dir / "t1-development"
    raster = run_dir / "inputs" / "population_raster"
    raster.parent.mkdir(parents=True)
    raster.write_bytes(b"bound raster")
    raster_sha = hashlib.sha256(raster.read_bytes()).hexdigest()
    monkeypatch.setattr(acceptance, "POPULATION_SHA256", raster_sha)
    config_rel = "configs/frozen-profile.yaml"
    config_path = dev_root / "bundle" / config_rel
    config_path.parent.mkdir(parents=True)
    config_path.write_text("fixture profile snapshot\n", encoding="utf-8")
    config_sha = hashlib.sha256(config_path.read_bytes()).hexdigest()
    remote_config = "/release/bundle/configs/frozen-profile.yaml"
    bundle_cell = {
        "input": {
            "config_identity": f"bundle:{config_rel}",
            "config_path": remote_config,
            "config_sha256": config_sha,
            "files": {
                "profile_config": {
                    "identity": f"bundle:{config_rel}",
                    "path": remote_config, "sha256": config_sha,
                },
                "population_path": {
                    "identity": f"source:{acceptance.PROFILE_POPULATION_PATH}",
                    "path": "/data/runs/official-run-01/inputs/population_raster",
                    "sha256": raster_sha,
                },
            },
        },
    }
    rows = [
        {"packet_id": 1, "emit_time_s": 2.0, "src_grid_id": "A",
         "dst_grid_id": "B", "bits": 12000},
        {"packet_id": 2, "emit_time_s": 3.0, "src_grid_id": "B",
         "dst_grid_id": "A", "bits": 12000},
    ]
    trace_sha = "f" * 64
    source = {
        "trace_sha256": trace_sha,
        "rows_digest": acceptance.t1_tasks._rows_digest(rows),
        "rows": len(rows),
    }
    manifest = {"inputs": [{
        "name": "population_raster",
        "snapshot_path": "inputs/population_raster",
        "sha256": raster_sha,
    }]}
    receipt_files = {
        f"t1-development/bundle/{config_rel}": {"sha256": config_sha},
        "inputs/population_raster": {"sha256": raster_sha},
    }
    resolved = {
        "config": {
            "demand": {"population_path": str(raster.resolve())},
            "execution": {"max_packets": 10},
        },
    }
    monkeypatch.setattr(
        acceptance, "_validate_fixed_config",
        lambda path, digest, population, result_source: resolved)

    observed = {}

    def compile_trace(trace_config, output_dir):
        observed["population_path"] = trace_config["config"]["demand"][
            "population_path"]
        return {
            "trace_sha256": trace_sha,
            "emission_end_s": 8.0,
            "input_sha256": raster_sha,
            "population": {"source_sha256": raster_sha},
        }

    def load_trace(path, *, horizon_s, max_packets):
        observed["load_args"] = (Path(path).name, horizon_s, max_packets)
        return rows

    monkeypatch.setattr(acceptance.trace_mod, "compile_trace", compile_trace)
    monkeypatch.setattr(acceptance.trace_mod, "load_trace", load_trace)
    monkeypatch.setattr(acceptance, "EXPECTED_PACKETS", len(rows))

    rebuilt, rebuilt_sha, rebuilt_rows_sha = acceptance._rebuild_trace(
        run_dir, dev_root, manifest, receipt_files, bundle_cell, source)

    assert observed["population_path"] == str(raster.resolve())
    assert observed["load_args"] == ("trace.csv", 8.0, 10)
    assert rebuilt == rows
    assert rebuilt_sha == trace_sha
    assert rebuilt_rows_sha == source["rows_digest"]


def test_zero_delivery_is_valid_data_with_uncomputable_delivered_latency():
    arm = _arm()
    arm['replay']['packet_events'] = [event for event in arm['replay']['packet_events']
                                       if event['kind'] != 'delivered']
    arm['replay']['deliveries'] = {}
    arm['replay']['fates'] = {str(pid): 'IN_SYSTEM_AT_STOP' for pid in (1, 2, 3)}
    arm['outcome'].update(delivered=0, fate_counts={'IN_SYSTEM_AT_STOP': 3},
                          e2e_samples=0, e2e_mean_s=None, e2e_p95_s=None)
    arm['network_outcome']['counts']['delivered'] = 0
    arm['network_outcome']['deadline_primary_loss']['value'] = 1.0
    result = acceptance.recompute_arm(arm, _trace_rows(), deadline_s=4,
        population_window=(2, 8), expected_stop_s=8)
    assert result['counts']['delivered'] == 0
    assert result['delivered_latency'] == {'packets': 0, 'mean_s': None, 'p95_s': None}
    assert result['deadline_primary_loss']['value'] == 1.0


def test_complete_run_metadata_reaches_independent_packet_recomputation(tmp_path, monkeypatch):
    """Use real file/record layout; receipt and trace generation have separate tests."""
    import copy
    from CODE.experiment_platform import t1_tasks, t1_suite
    run = tmp_path / 'fixture-run'
    dev = run / 't1-development'
    cell_id = acceptance.CELL_ID
    cell_dir = dev / 'b_dev' / 'cells' / cell_id
    cell_dir.mkdir(parents=True)
    commit = 'a' * 40
    manifest = {'status': 'completed', 'exit_code': 0, 'execution_class': 'development',
                'source_git_commit': commit, 'release_id': 'fixture'}
    source = {'native_population': {'population_sha256': acceptance.POPULATION_SHA256},
              'rows_digest': 'rows'}
    arms = []
    for name in acceptance.ARMS:
        arm = copy.deepcopy(_arm())
        arm.update(arm=name, seed=7, resolved_arm=name)
        arm['scope']['duration_s'] = 8
        arm['replay']['arm'] = name
        arms.append(arm)
    payload = {'schema': t1_tasks.SCHEMA_TASK, 'driver_schema': t1_tasks.SCHEMA_NETWORK,
        'task': 'network_alignment', 'status': 'ok', 'failed_units': 0,
        'document': {'status': 'ok', 'failures': [], 'source': source,
            'identity': {'git': {'commit': commit}},
            'deadline': {'deadline_s': 4, 'population_window_s': [2, 8]},
            'fairness': {'same_trace': True, 'same_seed': 7, 'rows_digest': 'rows'},
            'arms': arms}}
    result_path = cell_dir / 'result.json'
    t1_tasks.publish(payload, result_path)
    result_sha = hashlib.sha256(result_path.read_bytes()).hexdigest()
    input_binding = {'config_path': '/run/bundle/config.yaml'}
    args = ['--task', 'network_alignment', '--config', input_binding['config_path'],
            '--deadline-s', '4.0', '--window-start', '2.0', '--window-end', '8.0',
            '--arms', ','.join(acceptance.ARMS), '--capture-replay']
    predicate = {'kind': 't1_task', 'require': {'require_task': 'network_alignment',
        'require_full_replay': True, 'require_native_population_trace': True,
        'require_routing_audit_log': True, 'arms': list(acceptance.ARMS)}}
    cell = {'cell_id': cell_id, 'group': 'b_round', 'seed': 7,
        'driver': 'CODE.experiment_platform.t1_tasks', 'args': args,
        'input': input_binding, 'predicate': predicate}
    record = {'cell_id': cell_id, 'status': 'ok', 'returncode': 0, 'child_returncode': 0,
        'predicate': predicate, 'predicate_verdict': {'passed': True},
        'input': input_binding,
        'argv': ['python', '-m', cell['driver'], *args, '--out',
                 f'/run/cells/{cell_id}/result.json'],
        'result_path': f'cells/{cell_id}/result.json',
        'result_sha256': result_sha, 'result_schema': t1_tasks.SCHEMA_TASK}
    bundle = {'bundle_fingerprint': 'bound', 'identity': {'git': {'commit': commit}},
              'cells': [cell]}
    run_doc = {'schema': t1_suite.SCHEMA_RUN, 'tier': 'b_dev', 'status': 'ok',
               'bundle_fingerprint': 'bound', 'cells': [record]}
    docs = {run/'run-manifest.json': manifest, dev/'bundle/bundle.json': bundle,
            dev/'b_dev/run.json': run_doc, cell_dir/'cell.json': record}
    for path, doc in docs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc))
    files = [{'path': p.relative_to(run).as_posix(),
              'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in run.rglob('*') if p.is_file()]
    receipt = {'source_git_commit': commit, 'files': files, 'receipt_sha256': 'receipt'}
    monkeypatch.setattr(acceptance.release_protocol, 'verify_run_directory',
                        lambda *a, **kw: receipt)
    monkeypatch.setattr(acceptance, '_rebuild_trace', lambda *a: (_trace_rows(), 'trace', 'rows'))
    monkeypatch.setattr(acceptance, 'EXPECTED_PACKETS', 3)
    monkeypatch.setattr(t1_suite, 'check_predicate', lambda *a: {'passed': True})
    report = acceptance.accept_run(run)
    assert report['status'] == 'ACCEPTED_DATA'
    assert report['claimable'] is False
    assert report['identity']['result_sha256'] == result_sha
    assert report['arms']['candidate']['deadline_primary_loss']['value'] == pytest.approx(2/3)
    assert all(v == 0 for v in report['pairwise']['D4_mean_change'].values())
    assert len(report['validator_sha256']) == 64
    assert len(report['analysis_execution_chain_sha256']) == 64


def test_named_scope_rejects_changed_altitude_even_with_consistent_hashes(tmp_path):
    import yaml
    from CODE.leo_sim import config
    profile = Path(acceptance.__file__).parents[1] / 'leo_sim/profiles/t1_population_region_cost_smoke.yaml'
    raw = yaml.safe_load(profile.read_text())
    raw['scenario']['altitude_km'] = 900.0
    altered = tmp_path / 'changed.yaml'
    altered.write_text(yaml.safe_dump(raw))
    resolved = config.load_config_file(str(altered))
    with pytest.raises(acceptance.PopulationRunAcceptanceError, match='frozen resolved'):
        acceptance._validate_fixed_config(altered, hashlib.sha256(altered.read_bytes()).hexdigest(),
            tmp_path/'raster', {'config_sha256': resolved['sha256']})
