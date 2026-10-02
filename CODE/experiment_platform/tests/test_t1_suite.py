"""T1-COMPLETE P10: suite compile/validate/run/resume/report and budgets."""
from __future__ import annotations

import argparse
import json
import hashlib
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from CODE.experiment_platform import t1_development, t1_suite, t1_tasks
from CODE.leo_sim import trace as leo_trace

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "CODE/work/WP-T1-COMPLETE/contract.yaml"
C_CONTRACT = ROOT / "CODE/work/WP-T1-COMPLETE/contract_dev_c.yaml"
DEV_COST_PROBE_CONTRACT = ROOT / (
    "CODE/work/WP-T1-COMPLETE/contract_dev_cost_probe.yaml")


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "CODE.experiment_platform.t1_suite", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800, check=False)


def _small_contract(tmp_path):
    """A contract whose dev tier is SMALL ENOUGH for this machine to finish.

    Several tests only need "a dev tier that runs".  Running them on the full
    contract (39 dev cells, four seeds, plus the 24-satellite corridor and the
    burst scenario in the b_round tier) made the whole file exceed this
    pipeline resource limit, and the process was killed part way with no
    traceback (measured twice).  The fixture keeps the SAME code paths and
    predicates and only shrinks the matrix: one development seed, and the
    b_round scenarios marked as not-for-this-fixture.
    """
    contract = yaml.safe_load(CONTRACT.read_text())
    contract.setdefault("statistics", {})["dev_seeds"] = [7]
    for spec in (contract.get("b_round") or {}).get("scenarios") or []:
        spec["status"] = "FIXTURE_SKIPPED"
    path = tmp_path / "contract-small.yaml"
    path.write_text(yaml.safe_dump(contract, allow_unicode=True))
    return path


def _small_compiled(tmp_path):
    out = tmp_path / "compiled-small"
    done = _run("compile", "--contract", str(_small_contract(tmp_path)),
                "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    return out

def _compiled(tmp_path):
    out = tmp_path / "compiled"
    done = _run("compile", "--contract", str(CONTRACT), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    return out


def test_compile_writes_a_unique_cell_matrix_and_pre_registration(tmp_path):
    out = _compiled(tmp_path)
    bundle = json.loads((out / "bundle.json").read_text())
    assert bundle["schema"] == t1_suite.SCHEMA_BUNDLE
    ids = [c["cell_id"] for c in bundle["cells"]]
    assert len(ids) == len(set(ids))
    assert bundle["contract_sha256"] == t1_suite._sha256_file(CONTRACT)
    assert bundle["pre_registration"]["minimum_substantive_difference"] == 0.01
    assert bundle["identity"]["git"]["commit"]
    assert (out / "configs").is_dir()


def test_retired_c_campaign_contract_is_explicitly_not_runnable():
    contract = yaml.safe_load(C_CONTRACT.read_text(encoding="utf-8"))
    readiness = contract["design_readiness"]
    assert readiness["status"] == "INVALID_SUPERSEDED_NOT_RELEASE_READY"
    assert readiness["runtime_gate_implemented"] is True
    assert contract["design_validity"]["status"] == \
        "SUPERSEDED_INVALID_NOT_RUNNABLE"
    assert contract["historical_ledger"]["wp_b_runs_01_and_02"][
        "simulator_calls"] == 8

    # The archived C profile remains evidence only.  Its old C0/C1 estimates
    # must not be compiled or interpreted as a current executable design.
    with pytest.raises(t1_suite.SuiteError,
                       match="only COST_PROBE_READY or RELEASE_READY"):
        t1_suite.require_runtime_ready_contract(contract,
                                                source=str(C_CONTRACT))


def test_unreviewed_dev_cost_probe_candidate_without_authorization_is_not_runnable():
    # Preserve the root-approved persisted contract on disk.  A candidate copy
    # with its authorization removed must return to design-only status and be
    # refused by the runtime gate.
    contract = yaml.safe_load(
        DEV_COST_PROBE_CONTRACT.read_text(encoding="utf-8"))
    readiness = contract["design_readiness"]
    assert readiness["status"] == "COST_PROBE_READY"
    assert readiness["runtime_authorization"]
    readiness["status"] = "DESIGN_READY"
    readiness["runtime_authorization"] = None

    with pytest.raises(t1_suite.SuiteError,
                       match="only COST_PROBE_READY or RELEASE_READY"):
        t1_suite.require_runtime_ready_contract(
            contract, source=str(DEV_COST_PROBE_CONTRACT))


def test_root_authorized_persistent_contract_allows_only_exact_four_call_cell(
        tmp_path):
    contract = yaml.safe_load(
        DEV_COST_PROBE_CONTRACT.read_text(encoding="utf-8"))
    readiness = t1_suite.require_runtime_ready_contract(
        contract, source=str(DEV_COST_PROBE_CONTRACT))
    assert readiness["status"] == "COST_PROBE_READY"
    auth = readiness["runtime_authorization"]
    assert auth["cell_ids"] == [
        "b-bounded_population_cost_smoke-network-seed-7"]
    assert auth["expected_simulator_calls"] == 4
    assert auth["max_simulator_calls"] == 4
    assert auth["allow_append"] is False

    bundle_dir = tmp_path / "root-authorized-probe"
    bundle = t1_suite.compile_bundle(DEV_COST_PROBE_CONTRACT, bundle_dir)
    cell_id = auth["cell_ids"][0]
    cell = next(row for row in bundle["cells"]
                if row["cell_id"] == cell_id)
    assert bundle["execution_chain"]["combined_sha256"] == \
        auth["execution_chain_sha256"]
    assert t1_suite._cell_input_sha256(cell["input"]) == \
        auth["cell_input_sha256"][cell_id]
    assert t1_suite.estimate_bundle_cost({"cells": [cell]})[
        "simulator_calls"] == 4

    frozen = {
        "tier": "b_dev", "selected_cell_ids": [cell_id],
        "append": False, "cells": {cell_id: cell},
        "estimated_calls": 4, "bundle_root": bundle_dir,
        "source_root": ROOT,
    }
    t1_suite.enforce_runtime_stage(contract, bundle, **frozen)

    for override, message in (
            ({"selected_cell_ids": [cell_id, cell_id]},
             "exactly its one"),
            ({"append": True}, "append is forbidden"),
            ({"estimated_calls": 5}, "estimate differs"),
            ({"tier": "a_dev"}, "tier differs")):
        request = dict(frozen)
        request.update(override)
        with pytest.raises(t1_suite.SuiteError, match=message):
            t1_suite.enforce_runtime_stage(contract, bundle, **request)


def test_persistent_cost_probe_compile_preserves_native_profile_parameters(
        tmp_path, monkeypatch):
    input_identity = yaml.safe_load(Path(DEV_COST_PROBE_CONTRACT).read_text(
        encoding="utf-8"))["input_identity"]
    source_raster = ROOT / input_identity["population_path"]
    snapshot = tmp_path / "run/inputs/population_raster.tif"
    snapshot.parent.mkdir(parents=True)
    snapshot.write_bytes(source_raster.read_bytes())
    monkeypatch.setenv("T1_INPUT_POPULATION_RASTER", str(snapshot))
    bundle = t1_suite.compile_bundle(
        DEV_COST_PROBE_CONTRACT, tmp_path / "compiled-native-probe")
    cell_id = "b-bounded_population_cost_smoke-network-seed-7"
    cell = next(row for row in bundle["cells"]
                if row["cell_id"] == cell_id)
    config_path = Path(cell["args"][cell["args"].index("--config") + 1])
    resolved = t1_tasks.config_mod.load_config_file(str(config_path))["config"]
    assert resolved["scenario"]["num_satellites"] == 280
    assert resolved["scenario"]["num_planes"] == 14
    assert resolved["scenario"]["seed"] == 7
    assert resolved["endpoints"]["region_lat_bounds_deg"] == [5.0, 50.0]
    assert resolved["endpoints"]["region_lon_bounds_deg"] == [65.0, 140.0]
    assert resolved["scenario"]["duration_s"] == 8.0
    assert resolved["demand"]["mode"] == "population_gravity"
    assert resolved["demand"]["offered_mbps"] == 5.0
    assert resolved["demand"]["packet_bits"] == 12000
    assert resolved["demand"]["emission_start_s"] == 2.0
    assert resolved["demand"]["emission_end_s"] == 4.0
    assert resolved["demand"]["research_deadline_s"] == 4.0
    assert resolved["demand"]["deadline_s"] is None
    assert resolved["demand"]["burst_start_s"] == 2.5
    assert resolved["demand"]["burst_duration_s"] == 1.0
    assert resolved["demand"]["burst_multiplier"] == 2.0
    assert resolved["control_plane"]["ttl_s"] == 10.0
    assert resolved["execution"]["compute_servers_per_satellite"] == 1
    assert resolved["execution"]["compute_delay_s"] == 0.001
    assert resolved["control_plane"]["advertisement_protocol_version"] == 2
    assert cell["seed"] == 7
    assert cell["predicate"]["require"]["arms"] == [
        "stale", "now", "common", "candidate"]
    assert cell["predicate"]["require"][
        "require_native_population_trace"] is True
    contract = yaml.safe_load(Path(DEV_COST_PROBE_CONTRACT).read_text(
        encoding="utf-8"))
    population_sha = contract["input_identity"]["population_sha256"]
    profile_path = ROOT / contract["input_identity"]["profile_path"]
    assert hashlib.sha256(profile_path.read_bytes()).hexdigest() == \
        contract["input_identity"]["profile_sha256"]
    assert cell["predicate"]["require"]["population_sha256"] == \
        population_sha
    assert cell["input"]["files"]["population_path"]["sha256"] == \
        population_sha
    assert cell["input"]["files"]["population_path"]["path"] == \
        str(snapshot.resolve())
    snapshot_digest = t1_suite._cell_input_sha256(cell["input"])
    assert snapshot_digest == contract["design_readiness"][
        "runtime_authorization"]["cell_input_sha256"][cell_id]

    # Same bytes at the original source path produce the same semantic input
    # identity even though the recorded physical locator differs.
    monkeypatch.delenv("T1_INPUT_POPULATION_RASTER")
    local_binding = t1_suite._cell_input_binding(
        cell, bundle_root=tmp_path / "compiled-native-probe",
        source_root=ROOT)
    assert local_binding["files"]["population_path"]["path"] == \
        str(source_raster.resolve())
    assert t1_suite._cell_input_sha256(local_binding) == snapshot_digest
    monkeypatch.setenv("T1_INPUT_POPULATION_RASTER", str(snapshot))
    validation = t1_suite.validate_bundle(
        tmp_path / "compiled-native-probe")
    assert validation["valid"] is True
    assert "synthetic_od_count" not in cell["predicate"]["require"]
    assert "require_compiled_od_mapping" not in cell["predicate"]["require"]
    argv = cell["args"]
    options = dict(zip(argv[::2], argv[1::2]))
    assert options["--task"] == "network_alignment"
    assert options["--deadline-s"] == "4.0"
    assert options["--window-start"] == "2.0"
    assert options["--window-end"] == "8.0"
    assert options["--arms"] == "stale,now,common,candidate"
    assert t1_suite.estimate_bundle_cost({"cells": [cell]})[
        "simulator_calls"] == 4

    # Authorization metadata can change the contract identity without
    # changing the semantic per-cell digest; the latter is what the reviewed
    # allowlist binds and is independent of temporary bundle locations.
    cell_sha = t1_suite._cell_input_sha256(cell["input"])
    authorized = yaml.safe_load(Path(DEV_COST_PROBE_CONTRACT).read_text(
        encoding="utf-8"))
    authorized["frozen_at_sha"] = "test-only metadata does not bind itself"
    authorized_contract = tmp_path / "authorized-contract.yaml"
    authorized_contract.write_text(
        yaml.safe_dump(authorized, allow_unicode=True, sort_keys=False),
        encoding="utf-8")
    second_bundle = t1_suite.compile_bundle(
        authorized_contract, tmp_path / "compiled-authorized-probe")
    second_cell = next(row for row in second_bundle["cells"]
                       if row["cell_id"] == cell_id)
    assert second_bundle["contract_sha256"] != bundle["contract_sha256"]
    assert t1_suite._cell_input_sha256(second_cell["input"]) == cell_sha


def _write_small_global_tiff(path, first=11, second=23):
    pytest.importorskip("PIL")
    from PIL import Image, TiffImagePlugin

    values = np.zeros((2, 4), dtype=np.uint16)
    values[0, 0] = first
    values[1, 3] = second
    info = TiffImagePlugin.ImageFileDirectory_v2()
    info[33550] = (90.0, 90.0, 0.0)
    info[33922] = (0.0, 0.0, 0.0, -180.0, 90.0, 0.0)
    Image.fromarray(values).save(path, tiffinfo=info)


def test_cold_release_population_cell_binds_and_traces_same_snapshot(
        tmp_path, monkeypatch):
    # Model the immutable release exactly: profile and driver are present,
    # while the excluded source raster is absent. The runner-provided run
    # input is the only available population file.
    release_root = tmp_path / "release"
    profile_path = Path("CODE/leo_sim/profiles/cold-probe.yaml")
    release_profile = release_root / profile_path
    release_profile.parent.mkdir(parents=True)
    profile = yaml.safe_load((ROOT / "CODE/leo_sim/profiles/"
                              "t1_population_region_cost_smoke.yaml"
                              ).read_text(encoding="utf-8"))
    profile["scenario"]["duration_s"] = 1.0
    profile["endpoints"].update({
        "grid_deg": 90.0, "aggregation_deg": 90.0,
        "region_lat_bounds_deg": None, "region_lon_bounds_deg": None,
    })
    profile["demand"].update({
        "population_path": "CODE/population_map/gpw.tif",
        "offered_mbps": 0.12, "emission_start_s": 0.0,
        "emission_end_s": 1.0, "burst_start_s": 0.25,
        "burst_duration_s": 0.25,
    })
    profile["execution"]["max_packets"] = 1000
    release_profile.write_text(
        yaml.safe_dump(profile, sort_keys=False), encoding="utf-8")
    driver = release_root / "CODE/experiment_platform/t1_tasks.py"
    driver.parent.mkdir(parents=True)
    driver.write_bytes((ROOT / "CODE/experiment_platform/t1_tasks.py"
                        ).read_bytes())

    release_raster = release_root / "CODE/population_map/gpw.tif"
    assert not release_raster.exists()
    snapshot = tmp_path / "run-a/inputs/population_raster.tif"
    snapshot.parent.mkdir(parents=True)
    _write_small_global_tiff(snapshot)
    snapshot_sha = hashlib.sha256(snapshot.read_bytes()).hexdigest()

    contract = yaml.safe_load(Path(DEV_COST_PROBE_CONTRACT).read_text(
        encoding="utf-8"))
    contract["input_identity"].update({
        "profile_path": profile_path.as_posix(),
        "profile_sha256": hashlib.sha256(
            release_profile.read_bytes()).hexdigest(),
        "population_path": "CODE/population_map/gpw.tif",
        "population_sha256": snapshot_sha,
    })
    scenario = contract["b_round"]["scenarios"][0]
    scenario["profile"] = profile_path.as_posix()
    scenario["parameters"].update({
        "scenario.duration_s": 1.0,
        "endpoints.aggregation_deg": 90.0,
        "endpoints.region_lat_bounds_deg": None,
        "endpoints.region_lon_bounds_deg": None,
        "demand.emission_start_s": 0.0,
        "demand.emission_end_s": 1.0,
        "execution.max_packets": 1000,
    })
    contract["statistics"]["population_window_s"] = [0.0, 1.0]
    contract["admission_measurement_window_s"] = [0.0, 1.0]

    monkeypatch.setattr(t1_suite, "REPO_ROOT", release_root)
    monkeypatch.chdir(release_root)
    monkeypatch.setenv("T1_INPUT_POPULATION_RASTER", str(snapshot))
    bundle_dir = tmp_path / "bundle-a"
    cells = t1_suite._b_cells(contract, bundle_dir)
    assert len(cells) == 1
    cell = cells[0]
    cell["input"] = t1_suite._cell_input_binding(
        cell, bundle_root=bundle_dir, source_root=release_root)
    assert cell["predicate"]["require"]["population_sha256"] == snapshot_sha
    pop_binding = cell["input"]["files"]["population_path"]
    assert pop_binding["path"] == str(snapshot.resolve())
    assert pop_binding["identity"] == "source:CODE/population_map/gpw.tif"
    assert pop_binding["sha256"] == snapshot_sha
    assert t1_suite._cell_input_sha256(cell["input"]) == \
        t1_suite._cell_input_sha256(t1_suite._cell_input_binding(
            cell, bundle_root=bundle_dir, source_root=release_root))

    # Trace compilation resolves the same declared path through the same
    # runner snapshot, and retains the logical profile path in provenance.
    config_path = Path(cell["args"][cell["args"].index("--config") + 1])
    resolved = t1_tasks.config_mod.load_config_file(str(config_path))
    trace_manifest = leo_trace.compile_trace(
        resolved, tmp_path / "trace-from-run-a")
    assert trace_manifest["input_sha256"] == snapshot_sha
    assert trace_manifest["population"]["source_path"] == \
        "CODE/population_map/gpw.tif"

    # Different physical run location, identical bytes, same semantic cell
    # authorization. The absolute locator is intentionally not in the digest.
    snapshot_b = tmp_path / "run-b/inputs/population_raster.tif"
    snapshot_b.parent.mkdir(parents=True)
    snapshot_b.write_bytes(snapshot.read_bytes())
    monkeypatch.setenv("T1_INPUT_POPULATION_RASTER", str(snapshot_b))
    cell_b = dict(cell)
    cell_b["input"] = t1_suite._cell_input_binding(
        cell_b, bundle_root=bundle_dir, source_root=release_root)
    assert cell_b["input"]["files"]["population_path"]["path"] == \
        str(snapshot_b.resolve())
    assert t1_suite._cell_input_sha256(cell_b["input"]) == \
        t1_suite._cell_input_sha256(cell["input"])

    # Content changes are rejected by the contract input SHA, and a missing
    # explicit runner snapshot never falls back to the release-local path.
    tampered = tmp_path / "run-c/inputs/population_raster.tif"
    tampered.parent.mkdir(parents=True)
    _write_small_global_tiff(tampered, first=12, second=24)
    monkeypatch.setenv("T1_INPUT_POPULATION_RASTER", str(tampered))
    with pytest.raises(t1_suite.SuiteError, match="differs from contract"):
        t1_suite._b_cells(contract, tmp_path / "bundle-tampered")

    release_raster.parent.mkdir(parents=True)
    _write_small_global_tiff(release_raster)
    monkeypatch.setenv(
        "T1_INPUT_POPULATION_RASTER",
        str(tmp_path / "run-c/inputs/missing-population.tif"))
    with pytest.raises(t1_suite.SuiteError, match="not found"):
        t1_suite._cell_input_binding(
            cell, bundle_root=bundle_dir, source_root=release_root)
    with pytest.raises(t1_suite.SuiteError, match="not found"):
        t1_suite._b_cells(contract, tmp_path / "bundle-missing")


def test_native_population_predicate_checks_source_and_full_offered_count():
    require = {"require_task": "network_alignment",
               "require_native_population_trace": True,
               "population_sha256": "a" * 64,
               "arms": ["stale", "now", "common", "candidate"]}
    arms = [
        {"arm": name,
         "scope": {"packets_in_trace": 2,
                   "satellites_that_decided": 1},
         "outcome": {"offered": 2},
         "outcome_document": {"partition_exact": True},
         "time_alignment_audit": {
             "arms_seen_in_the_audit": [name],
             "decisions_with_query_targets": 1},
         "request_rate_per_satellite": {"max": 0.0}}
        for name in ("stale", "now", "common", "candidate")]
    source = {
        "scenario": "config", "rows": 2, "trace_sha256": "b" * 64,
        "native_population": {
            "mode": "population_gravity", "population_sha256": "a" * 64,
            "population_source": "CODE/population_map/gpw.tif",
            "all_rows_have_ground_grid_ids": True,
            "source_grid_count": 2, "destination_grid_count": 2,
            "directed_od_count": 2},
    }
    result = {"status": "ok", "failed_units": 0,
              "task": "network_alignment",
              "document": {"source": source, "arms": arms}}
    assert all(ok for _name, ok, _detail in
               t1_suite._check_task_predicate(result, require))

    source["synthetic_workload"] = {"summary": {"unique_od_count": 6}}
    checks = t1_suite._check_task_predicate(result, require)
    native_check = next(row for row in checks
                        if row[0] == "input is native population trace")
    assert native_check[1] is False


def test_compile_refuses_an_existing_destination(tmp_path):
    out = _compiled(tmp_path)
    done = _run("compile", "--contract", str(CONTRACT), "--out", str(out))
    assert done.returncode == 2
    assert "exists" in done.stdout


def test_compile_refuses_a_missing_contract(tmp_path):
    done = _run("compile", "--contract", str(tmp_path / "nope.yaml"),
                "--out", str(tmp_path / "b"))
    assert done.returncode == 2
    assert "not found" in done.stdout


def test_runtime_readiness_fails_closed_for_plan_old_contract_and_probe_scope(
        tmp_path):
    with pytest.raises(t1_suite.SuiteError, match="runtime_gate_implemented"):
        t1_suite.require_runtime_ready_contract({
            "design_readiness": {
                "status": "PLAN_ONLY_NOT_RELEASE_READY",
                "runtime_gate_implemented": False,
            },
        })

    retired = yaml.safe_load(C_CONTRACT.read_text(encoding="utf-8"))
    with pytest.raises(t1_suite.SuiteError, match="only COST_PROBE_READY"):
        t1_suite.require_runtime_ready_contract(retired)

    cell_id = "b-probe-network-seed-7"
    profile = yaml.safe_load((ROOT / "CODE/leo_sim/profiles/"
                              "t1_population_region_cost_smoke.yaml"
                              ).read_text(encoding="utf-8"))
    profile["scenario"]["seed"] = 7
    profile_path = tmp_path / "profile-seed-7.yaml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False),
                            encoding="utf-8")
    cell = {
        "cell_id": cell_id,
        "group": "b_round",
        "driver": "CODE.experiment_platform.t1_tasks",
        "args": ["--task", "network_alignment", "--config",
                 str(profile_path)],
        "seed": 7,
    }
    cell["input"] = t1_suite._cell_input_binding(cell)
    input_sha = t1_suite._cell_input_sha256(cell["input"])
    chain_sha = "b" * 64
    contract = {
        "design_readiness": {
            "status": "COST_PROBE_READY",
            "runtime_gate_implemented": True,
            "runtime_authorization": {
                "stage": "cost_probe", "tier": "b_dev",
                "cell_ids": [cell_id], "max_selected_cells": 1,
                "expected_simulator_calls": 4, "max_simulator_calls": 4,
                "allow_append": False, "task": "network_alignment",
                "seed": 7, "execution_chain_sha256": chain_sha,
                "cell_input_sha256": {cell_id: input_sha},
            },
        },
    }
    bundle = {"execution_chain": {"combined_sha256": chain_sha}}
    cells = {cell_id: cell}
    t1_suite.enforce_runtime_stage(
        contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
        append=False, cells=cells, estimated_calls=4)
    for kwargs, message in (
            ({"tier": "a_dev"}, "tier differs"),
            ({"selected_cell_ids": ["another-cell"]}, "exactly its one"),
            ({"append": True}, "append is forbidden"),
            ({"estimated_calls": 3}, "estimate differs")):
        call = {"tier": "b_dev", "selected_cell_ids": [cell_id],
                "append": False, "estimated_calls": 4}
        call.update(kwargs)
        with pytest.raises(t1_suite.SuiteError, match=message):
            t1_suite.enforce_runtime_stage(
                contract, bundle, cells=cells, **call)

    bad_metadata = dict(cell, seed=11)
    with pytest.raises(t1_suite.SuiteError, match="cell seed metadata"):
        t1_suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=False, cells={cell_id: bad_metadata}, estimated_calls=4)

    wrong_seed_profile = dict(profile)
    wrong_seed_profile["scenario"]["seed"] = 11
    profile_path.write_text(yaml.safe_dump(wrong_seed_profile,
                                           sort_keys=False),
                            encoding="utf-8")
    with pytest.raises(t1_suite.SuiteError, match="resolved profile seed"):
        t1_suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=False, cells=cells, estimated_calls=4)


def test_cell_input_binding_hashes_profile_and_population_path(tmp_path):
    profile = yaml.safe_load((ROOT / "CODE/leo_sim/profiles/"
                              "t1_population_region_cost_smoke.yaml"
                              ).read_text(encoding="utf-8"))
    population_path = tmp_path / "gpw_fixture.tif"
    population_path.write_bytes(b"bounded GPW fixture bytes")
    profile["scenario"]["name"] = "renamed-generic-label"
    profile["demand"]["population_path"] = str(population_path)
    profile_path = tmp_path / "renamed-profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False),
                            encoding="utf-8")
    cell = {"args": ["--task", "network_alignment", "--config",
                      str(profile_path)]}

    binding = t1_suite._cell_input_binding(cell)

    assert binding["files"]["profile_config"] == {
        "path": str(profile_path),
        "identity": "external:profile_config",
        "sha256": hashlib.sha256(profile_path.read_bytes()).hexdigest(),
    }
    assert binding["files"]["population_path"] == {
        "path": str(population_path),
        "identity": "external:population_path",
        "sha256": hashlib.sha256(population_path.read_bytes()).hexdigest(),
    }


def test_probe_input_authorization_is_content_bound_not_checkout_path_bound(
        tmp_path):
    cell_id = "b-bounded_population_cost_smoke-network-seed-7"
    auth_chain = "c" * 64
    bindings, cells = [], []
    for label in ("source-a", "source-b"):
        source_root = tmp_path / label
        bundle_root = source_root / "bundle"
        config_dir = bundle_root / "configs"
        population_path = source_root / "CODE/population_map/gpw.tif"
        driver_path = source_root / "CODE/experiment_platform/t1_tasks.py"
        config_dir.mkdir(parents=True)
        population_path.parent.mkdir(parents=True)
        driver_path.parent.mkdir(parents=True)
        population_path.write_bytes(b"same GPW content")
        driver_path.write_bytes((ROOT / "CODE/experiment_platform/t1_tasks.py"
                                 ).read_bytes())
        profile = yaml.safe_load((ROOT / "CODE/leo_sim/profiles/"
                                  "t1_population_region_cost_smoke.yaml"
                                  ).read_text(encoding="utf-8"))
        profile["demand"]["population_path"] = \
            "CODE/population_map/gpw.tif"
        config_path = config_dir / "seed-7.yaml"
        config_path.write_text(yaml.safe_dump(profile, sort_keys=False),
                               encoding="utf-8")
        cell = {
            "cell_id": cell_id, "group": "b_round",
            "driver": "CODE.experiment_platform.t1_tasks",
            "seed": 7,
            "args": ["--task", "network_alignment", "--config",
                     str(config_path), "--deadline-s", "4.0",
                     "--window-start", "2.0", "--window-end", "8.0",
                     "--arms", "stale,now,common,candidate"],
        }
        cell["input"] = t1_suite._cell_input_binding(
            cell, bundle_root=bundle_root, source_root=source_root)
        bindings.append(cell["input"])
        cells.append(cell)

    assert bindings[0]["config_path"] != bindings[1]["config_path"]
    assert bindings[0]["files"]["population_path"]["path"] != \
        bindings[1]["files"]["population_path"]["path"]
    expected_input_sha = t1_suite._cell_input_sha256(bindings[0])
    assert expected_input_sha == t1_suite._cell_input_sha256(bindings[1])
    auth = {
        "stage": "cost_probe", "tier": "b_dev", "cell_ids": [cell_id],
        "max_selected_cells": 1, "expected_simulator_calls": 4,
        "max_simulator_calls": 4, "allow_append": False,
        "task": "network_alignment", "seed": 7,
        "execution_chain_sha256": auth_chain,
        "cell_input_sha256": {cell_id: expected_input_sha},
    }
    contract = {"design_readiness": {
        "status": "COST_PROBE_READY", "runtime_gate_implemented": True,
        "runtime_authorization": auth}}
    bundle = {"execution_chain": {"combined_sha256": auth_chain}}
    for label, cell in zip(("source-a", "source-b"), cells):
        t1_suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=False, cells={cell_id: cell}, estimated_calls=4,
            bundle_root=tmp_path / label / "bundle",
            source_root=tmp_path / label)

    changed_window = dict(cells[1])
    changed_window["args"] = list(cells[1]["args"])
    changed_window["args"][changed_window["args"].index(
        "--window-end") + 1] = "9.0"
    # Keep the compiled binding unchanged: runtime authorization must hash the
    # current argv rather than trusting a stale input record.
    with pytest.raises(t1_suite.SuiteError, match="cell input SHA differs"):
        t1_suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=False, cells={cell_id: changed_window}, estimated_calls=4,
            bundle_root=tmp_path / "source-b" / "bundle",
            source_root=tmp_path / "source-b")

    changed_population = (tmp_path / "source-b/CODE/population_map/gpw.tif")
    changed_population.write_bytes(b"tampered GPW content")
    tampered = dict(cells[1])
    tampered["input"] = t1_suite._cell_input_binding(
        tampered, bundle_root=tmp_path / "source-b/bundle",
        source_root=tmp_path / "source-b")
    with pytest.raises(t1_suite.SuiteError, match="cell input SHA differs"):
        t1_suite.enforce_runtime_stage(
            contract, bundle, tier="b_dev", selected_cell_ids=[cell_id],
            append=False, cells={cell_id: tampered}, estimated_calls=4,
            bundle_root=tmp_path / "source-b" / "bundle",
            source_root=tmp_path / "source-b")


def _launch_ledger_context(tmp_path, *, nonce="a" * 64, max_calls=1,
                           expires_at=200.0, suffix=""):
    call_ledger = tmp_path / f"simulator-calls{suffix}.jsonl"
    call_ledger.write_text("", encoding="utf-8")
    return {
        "schema": "t1-suite-task-runtime-context/v1",
        "bundle_fingerprint": "b" * 64,
        "cell_id": "probe-cell",
        "result_path": str((tmp_path / "run" / "cells" / "probe-cell"
                             / "result.json").resolve()),
        "launch_nonce": nonce,
        "launch_ledger_path": str((tmp_path / f"suite-launch-{nonce}{suffix}"
                                    ".json").resolve()),
        "simulator_call_ledger_path": str(call_ledger.resolve()),
        "max_simulator_calls": max_calls,
        "expires_at": expires_at,
    }


def _suite_launch_ledger_api():
    names = ("_write_suite_launch_ledger", "_validate_suite_launch_ledger",
             "_claim_suite_launch_ledger", "_reserve_suite_simulator_call",
             "_close_suite_launch_ledger")
    api = {name: getattr(t1_suite, name, None) for name in names}
    assert all(callable(function) for function in api.values()), (
        "suite launch nonce and per-call ledger lifecycle is not implemented"
    )
    return api


def test_suite_launch_ledger_rejects_missing_wrong_reused_and_expired(tmp_path):
    api = _suite_launch_ledger_api()

    missing = _launch_ledger_context(tmp_path, suffix="-missing")
    with pytest.raises(t1_suite.SuiteError, match="missing"):
        api["_validate_suite_launch_ledger"](missing, now=100.0)

    wrong = _launch_ledger_context(tmp_path, suffix="-wrong")
    api["_write_suite_launch_ledger"](wrong)
    wrong_nonce = dict(wrong, launch_nonce="c" * 64)
    with pytest.raises(t1_suite.SuiteError,
                       match="nonce|identity|path|mismatch"):
        api["_validate_suite_launch_ledger"](wrong_nonce, now=100.0)

    expired = _launch_ledger_context(
        tmp_path, expires_at=99.0, suffix="-expired")
    api["_write_suite_launch_ledger"](expired)
    with pytest.raises(t1_suite.SuiteError, match="expired"):
        api["_validate_suite_launch_ledger"](expired, now=100.0)

    reused = _launch_ledger_context(tmp_path, suffix="-reused")
    api["_write_suite_launch_ledger"](reused)
    api["_claim_suite_launch_ledger"](reused, now=100.0)
    with pytest.raises(t1_suite.SuiteError, match="single|active|claimed|used"):
        api["_claim_suite_launch_ledger"](reused, now=101.0)
    api["_close_suite_launch_ledger"](reused, now=102.0)
    with pytest.raises(t1_suite.SuiteError, match="closed|single|used"):
        api["_validate_suite_launch_ledger"](reused, now=103.0)


def test_suite_launch_ledger_checks_remaining_calls_before_each_call(tmp_path):
    api = _suite_launch_ledger_api()
    context = _launch_ledger_context(tmp_path, max_calls=1,
                                     suffix="-budget")
    api["_write_suite_launch_ledger"](context)
    api["_claim_suite_launch_ledger"](context, now=100.0)

    assert api["_reserve_suite_simulator_call"](context, now=101.0) == 1
    call_ledger = Path(context["simulator_call_ledger_path"])
    call_ledger.write_text(json.dumps({
        "schema": "t1-simulator-call/v1",
        "call_id": "probe-cell:0001",
        "sequence": 1,
        "context": "probe-cell",
        "event": "begin",
    }) + "\n" + json.dumps({
        "schema": "t1-simulator-call/v1",
        "call_id": "probe-cell:0001",
        "sequence": 1,
        "context": "probe-cell",
        "event": "end",
    }) + "\n", encoding="utf-8")
    with pytest.raises(t1_suite.SuiteError, match="budget|remaining"):
        api["_reserve_suite_simulator_call"](context, now=102.0)


def test_execute_cell_issues_one_bounded_launch_without_running_simulation(
        tmp_path, monkeypatch):
    profile = yaml.safe_load((ROOT / "CODE/leo_sim/profiles/"
                              "t1_population_region_cost_smoke.yaml"
                              ).read_text(encoding="utf-8"))
    population_path = tmp_path / "population-fixture.tif"
    population_path.write_bytes(b"fixture")
    profile["demand"]["population_path"] = str(population_path)
    profile_path = tmp_path / "population-profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False),
                            encoding="utf-8")
    cell = {
        "cell_id": "probe-cell", "driver": "CODE.experiment_platform.t1_tasks",
        "args": ["--task", "network_alignment", "--config",
                 str(profile_path)], "predicate": {"kind": None},
    }
    runtime_context = {
        "schema": "t1-suite-task-runtime-context/v1",
        "bundle_dir": str(tmp_path.resolve()),
        "bundle_fingerprint": "b" * 64,
        "contract_path": str((tmp_path / "contract.yaml").resolve()),
        "contract_sha256": "c" * 64, "tier": "b_dev",
        "cell_id": "probe-cell", "selected_cell_ids": ["probe-cell"],
        "append": False,
        "result_path": str((tmp_path / "run" / "cells" / "probe-cell"
                             / "result.json").resolve()),
    }
    observed = {}

    def fake_subprocess_run(argv, *, cwd, capture_output, text, timeout, env):
        assert argv[1:3] == ["-m", "CODE.experiment_platform.t1_tasks"]
        assert cwd == str(t1_suite.REPO_ROOT)
        assert capture_output and text and timeout == 30.0
        context = json.loads(env["T1_SUITE_RUNTIME_CONTEXT"])
        observed["context"] = context
        assert env["T1_SUITE_LAUNCH_NONCE"] == context["launch_nonce"]
        assert env["T1_SIM_CALL_LEDGER"] == context[
            "simulator_call_ledger_path"]
        t1_suite._claim_suite_launch_ledger(context)
        ledger = Path(context["simulator_call_ledger_path"])
        for sequence in range(1, 5):
            assert t1_suite._reserve_suite_simulator_call(context) == sequence
            call_id = f"probe-cell:{sequence:04d}"
            with ledger.open("a", encoding="utf-8") as stream:
                for event_name in ("begin", "end"):
                    stream.write(json.dumps({
                        "schema": "t1-simulator-call/v1",
                        "call_id": call_id, "sequence": sequence,
                        "context": "probe-cell", "event": event_name,
                    }) + "\n")
        t1_suite._close_suite_launch_ledger(context)
        result_path = Path(context["result_path"])
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({
            "schema": "t1-task-result/v1", "status": "ok",
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(t1_suite.subprocess, "run", fake_subprocess_run)

    record = t1_suite._execute_cell(
        cell, tmp_path / "run", {"cell_wall_s": 30.0,
                                  "simulator_call_budget": 4},
        runtime_context=runtime_context)

    assert record["status"] == "ok"
    assert observed["context"]["max_simulator_calls"] == 4
    assert t1_suite._suite_launch_ledger_snapshot(
        observed["context"])["status"] == "closed"


def test_compile_enforces_the_max_cell_budget(tmp_path):
    contract = yaml.safe_load(CONTRACT.read_text())
    contract["budgets"] = {"max_cells": 2}
    small = tmp_path / "contract-small.yaml"
    small.write_text(yaml.safe_dump(contract, allow_unicode=True))
    done = _run("compile", "--contract", str(small),
                "--out", str(tmp_path / "b"))
    assert done.returncode == 2
    assert "max_cells" in done.stdout


def test_controlled_population_task_context_binds_bundle_cell_and_stage(
        tmp_path, monkeypatch):
    """A controlled task can run only from its validated frozen probe cell."""
    profile = yaml.safe_load((ROOT / "CODE/leo_sim/profiles/"
                              "t1_population_region_cost_smoke.yaml"
                              ).read_text(encoding="utf-8"))
    profile["scenario"]["name"] = "t1-population-region-cli-test"
    profile_path = tmp_path / "t1-population-region-cli-test.yaml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False),
                            encoding="utf-8")

    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    contract.setdefault("statistics", {})["dev_seeds"] = [7]
    contract["b_round"]["scenarios"] = [{
        "id": "probe-cli", "profile": str(profile_path),
        "status": "READY", "seeds": [7],
        "tasks": ["network_alignment"],
        "task_seeds": {"network_alignment": [7]},
    }]
    contract_path = tmp_path / "contract-cost-probe.yaml"
    contract_path.write_text(yaml.safe_dump(contract, allow_unicode=True),
                             encoding="utf-8")
    bundle_dir = tmp_path / "bundle-cost-probe"
    bundle = t1_suite.compile_bundle(contract_path, bundle_dir)
    cell_id = "b-probe-cli-network-seed-7"
    cell = next(c for c in bundle["cells"] if c["cell_id"] == cell_id)
    assert cell["seed"] == 7

    # Freeze the single compiled cell's actual trace/profile binding in the
    # stage authorization. The test edits only this temporary contract/bundle.
    contract["design_readiness"] = {
        "status": "COST_PROBE_READY",
        "runtime_gate_implemented": True,
        "runtime_authorization": {
            "stage": "cost_probe", "tier": "b_dev",
            "cell_ids": [cell_id], "max_selected_cells": 1,
            "expected_simulator_calls": 4, "max_simulator_calls": 4,
            "allow_append": False, "task": "network_alignment", "seed": 7,
            "execution_chain_sha256": bundle["execution_chain"][
                "combined_sha256"],
            "cell_input_sha256": {
                cell_id: t1_suite._cell_input_sha256(cell["input"])},
        },
    }
    contract_path.write_text(yaml.safe_dump(contract, allow_unicode=True),
                             encoding="utf-8")
    bundle["contract_sha256"] = t1_suite._sha256_file(contract_path)
    bundle["bundle_fingerprint"] = t1_suite._bundle_fingerprint(bundle)
    t1_suite._write_json(bundle_dir / "bundle.json", bundle)

    result_path = (tmp_path / "run" / "cells" / cell_id / "result.json")
    context = t1_suite._suite_task_runtime_context(
        bundle_dir, bundle, "b_dev", cell_id, [cell_id], False, result_path)
    call_ledger = (tmp_path / "simulator-calls.jsonl").resolve()
    call_ledger.write_text("", encoding="utf-8")
    context.update({
        "launch_nonce": "d" * 64,
        "launch_ledger_path": str((tmp_path / "suite-launch-test.json").resolve()),
        "simulator_call_ledger_path": str(call_ledger),
        "max_simulator_calls": 4,
        "expires_at": time.time() + 300.0,
    })
    t1_suite._write_suite_launch_ledger(context)
    monkeypatch.setenv(t1_tasks._SUITE_RUNTIME_CONTEXT_ENV,
                       json.dumps(context, sort_keys=True))
    monkeypatch.setenv("T1_SUITE_LAUNCH_NONCE", context["launch_nonce"])
    monkeypatch.setenv("T1_SIM_CALL_LEDGER", str(call_ledger))
    monkeypatch.setenv("T1_SIM_CALL_CONTEXT", cell_id)
    compiled_profile = Path(cell["args"][cell["args"].index("--config") + 1])
    raw_args = [*cell["args"], "--out", str(result_path)]
    # A real compiled network cell carries its seed in its profile and cell
    # metadata; it does not have a synthetic --seed CLI flag.
    t1_tasks._validate_suite_runtime_context(
        argparse.Namespace(config=compiled_profile, out=result_path), raw_args)

    for override in (
            ["--arms", "stale,now,candidate"],
            ["--compute-servers", "8"],
            ["--service-s", "0.5"]):
        with pytest.raises(t1_tasks.TaskError, match="argv differs"):
            t1_tasks._validate_suite_runtime_context(
                argparse.Namespace(config=compiled_profile, out=result_path),
                [*cell["args"], *override, "--out", str(result_path)])

    changed = dict(context)
    changed["cell_id"] = "another-cell"
    monkeypatch.setenv(t1_tasks._SUITE_RUNTIME_CONTEXT_ENV,
                       json.dumps(changed, sort_keys=True))
    with pytest.raises(t1_tasks.TaskError, match="selection is malformed"):
        t1_tasks._validate_suite_runtime_context(
            argparse.Namespace(config=compiled_profile, out=result_path), raw_args)

    # A frozen diagnostic sub-authorization is carried through the actual
    # compiled bundle and reduces only this child launch to one kernel call.
    diagnostic = {
        "mode": "first_kernel_call_stack_sampling",
        "max_simulator_calls": 1,
        "maximum_cell_wall_s": 45,
        "sample_interval_s": 1,
    }
    contract["design_readiness"]["runtime_authorization"][
        "diagnostic"] = diagnostic
    contract_path.write_text(yaml.safe_dump(contract, allow_unicode=True),
                             encoding="utf-8")
    bundle["contract_sha256"] = t1_suite._sha256_file(contract_path)
    bundle["bundle_fingerprint"] = t1_suite._bundle_fingerprint(bundle)
    t1_suite._write_json(bundle_dir / "bundle.json", bundle)
    diagnostic_context = t1_suite._suite_task_runtime_context(
        bundle_dir, bundle, "b_dev", cell_id, [cell_id], False, result_path,
        diagnostic=diagnostic)
    diagnostic_ledger = (tmp_path / "diagnostic-simulator-calls.jsonl").resolve()
    diagnostic_ledger.write_text("", encoding="utf-8")
    diagnostic_context.update({
        "launch_nonce": "e" * 64,
        "launch_ledger_path": str(
            (tmp_path / "diagnostic-suite-launch.json").resolve()),
        "simulator_call_ledger_path": str(diagnostic_ledger),
        "max_simulator_calls": 1,
        "expires_at": time.time() + 300.0,
    })
    t1_suite._write_suite_launch_ledger(diagnostic_context)
    monkeypatch.setenv(t1_tasks._SUITE_RUNTIME_CONTEXT_ENV,
                       json.dumps(diagnostic_context, sort_keys=True))
    monkeypatch.setenv("T1_SUITE_LAUNCH_NONCE",
                       diagnostic_context["launch_nonce"])
    monkeypatch.setenv("T1_SIM_CALL_LEDGER", str(diagnostic_ledger))
    assert t1_tasks._validate_suite_runtime_context(
        argparse.Namespace(config=compiled_profile, out=result_path),
        raw_args)["diagnostic"] == diagnostic

    tampered_diagnostic = dict(diagnostic_context)
    tampered_diagnostic["diagnostic"] = None
    monkeypatch.setenv(t1_tasks._SUITE_RUNTIME_CONTEXT_ENV,
                       json.dumps(tampered_diagnostic, sort_keys=True))
    with pytest.raises(t1_tasks.TaskError,
                       match="diagnostic differs from its contract"):
        t1_tasks._validate_suite_runtime_context(
            argparse.Namespace(config=compiled_profile, out=result_path),
            raw_args)

    suite_launch = {}

    def fake_execute_cell(_cell, _out_dir, budgets, *, runtime_context=None):
        (_out_dir / "cells" / cell_id).mkdir(parents=True, exist_ok=True)
        suite_launch["wall_s"] = budgets["cell_wall_s"]
        suite_launch["diagnostic"] = runtime_context["diagnostic"]
        return {
            "cell_id": cell_id, "status": "error", "returncode": 2,
            "simulator_calls": {"started": 0, "ended": 0, "failed": 0,
                                "timed_out": 0, "interrupted": 0,
                                "unresolved": 0, "simulator_wall_s": 0},
        }

    original_execute_cell = t1_suite._execute_cell
    monkeypatch.setattr(t1_suite, "_execute_cell", fake_execute_cell)
    diagnostic_run = t1_suite.run_bundle(
        bundle_dir, "b_dev", tmp_path / "diagnostic-run",
        cell_ids=[cell_id], stop_on_failure=True)
    assert suite_launch == {"wall_s": 45.0, "diagnostic": diagnostic}
    assert diagnostic_run["status"] == "FAILED_CELLS"

    # Exercise the real process-launch boundary too: even a caller that hands
    # _execute_cell a 120-second budget receives a 45-second subprocess cap.
    monkeypatch.setattr(t1_suite, "_execute_cell", original_execute_cell)
    child_launch = {}

    def fake_diagnostic_child(argv, **kwargs):
        child_launch["timeout"] = kwargs["timeout"]
        child_context = json.loads(
            kwargs["env"][t1_tasks._SUITE_RUNTIME_CONTEXT_ENV])
        child_launch["max_simulator_calls"] = child_context[
            "max_simulator_calls"]
        child_launch["diagnostic"] = child_context["diagnostic"]
        t1_suite._claim_suite_launch_ledger(child_context)
        t1_suite._reserve_suite_simulator_call(child_context)
        Path(child_context["simulator_call_ledger_path"]).write_text(
            "\n".join(json.dumps(event) for event in ({
                "schema": "t1-simulator-call/v1",
                "context": cell_id,
                "call_id": f"{cell_id}:0001",
                "event": "begin",
                "sequence": 1,
                "monotonic_ns": time.perf_counter_ns(),
            }, {
                "schema": "t1-simulator-call/v1",
                "context": cell_id,
                "call_id": f"{cell_id}:0001",
                "event": "fail",
                "sequence": 1,
                "duration_s": 0.01,
            })) + "\n", encoding="utf-8")
        t1_suite._close_suite_launch_ledger(child_context)
        child_result = Path(argv[argv.index("--out") + 1])
        (child_result.parent / t1_tasks.DIAGNOSTIC_STACK_ARTIFACT).write_text(
            "sampled stack\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 2, "", "diagnostic child stopped")

    original_subprocess_run = subprocess.run
    monkeypatch.setattr(t1_suite.subprocess, "run", fake_diagnostic_child)
    monkeypatch.setattr(t1_tasks, "_is_controlled_population_region_profile",
                        lambda _path: True)
    direct_root = tmp_path / "direct-execute"
    direct_result = direct_root / "cells" / cell_id / "result.json"
    direct_context = t1_suite._suite_task_runtime_context(
        bundle_dir, bundle, "b_dev", cell_id, [cell_id], False, direct_result,
        diagnostic=diagnostic)
    direct_record = t1_suite._execute_cell(
        cell, direct_root, {**t1_suite.DEFAULT_BUDGETS,
                            "cell_wall_s": 120.0,
                            "simulator_call_budget": 4},
        runtime_context=direct_context)
    diagnostic_record = direct_record["diagnostic"]
    assert child_launch == {
        "timeout": 45.0,
        "max_simulator_calls": 1,
        "diagnostic": diagnostic,
    }
    assert diagnostic_record["artifact_path"].endswith(
        t1_tasks.DIAGNOSTIC_STACK_ARTIFACT)
    assert diagnostic_record["artifact_sha256"] == hashlib.sha256(
        b"sampled stack\n").hexdigest()
    assert diagnostic_record["artifact_size_bytes"] == len(b"sampled stack\n")
    assert diagnostic_record["max_simulator_calls"] == 1
    assert diagnostic_record["maximum_cell_wall_s"] == 45
    assert diagnostic_record["sample_interval_s"] == 1
    assert diagnostic_record["effective_cell_wall_s"] == 45.0
    assert diagnostic_record["static_estimated_simulator_calls"] == 4
    assert diagnostic_record["launch_max_simulator_calls"] == 1
    monkeypatch.setattr(t1_suite.subprocess, "run", original_subprocess_run)

    # Editing the bound profile after compile (including a seed change) fails
    # before design/trace loading, even with the old context still present.
    monkeypatch.setenv(t1_tasks._SUITE_RUNTIME_CONTEXT_ENV,
                       json.dumps(diagnostic_context, sort_keys=True))
    resolved_profile = compiled_profile
    changed_profile = yaml.safe_load(resolved_profile.read_text(
        encoding="utf-8"))
    changed_profile["scenario"]["seed"] = 8
    resolved_profile.write_text(yaml.safe_dump(changed_profile,
                                               sort_keys=False),
                                encoding="utf-8")
    with pytest.raises(t1_tasks.TaskError, match="bundle validation failed"):
        t1_tasks._validate_suite_runtime_context(
            argparse.Namespace(config=resolved_profile, out=result_path),
            raw_args)


def test_development_matrix_expands_branches_benchmarks_and_task_seeds(tmp_path):
    contract = yaml.safe_load(CONTRACT.read_text())
    contract["statistics"]["dev_seeds"] = [7]
    contract["b_round"]["scenarios"] = [{
        "id": "multiod-test",
        "profile": "CODE/leo_sim/profiles/t1_dev_asymmetric_multiod.yaml",
        "seeds": [7, 11],
        "tasks": ["branch_alignment", "network_alignment",
                  "execution_modes", "benchmark"],
        "task_seeds": {"branch_alignment": [7],
                       "network_alignment": [7, 11],
                       "execution_modes": [7, 11],
                       "benchmark": [7]},
        "max_branches": 5,
        "execution_matrix": [{"id": "n1-p2", "seed": 7,
                              "compute_servers": 1, "service_s": 0.25,
                              "update_interval_s": 2.0}],
        "benchmark_pool_servers": [1],
        "benchmark_args": ["--pool-sweep", "1"],
    }]
    contract["budgets"] = {"max_cells": 80}
    path = tmp_path / "contract-matrix.yaml"
    path.write_text(yaml.safe_dump(contract, allow_unicode=True))
    out = tmp_path / "compiled-matrix"
    done = _run("compile", "--contract", str(path), "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    bundle = json.loads((out / "bundle.json").read_text())
    ids = set(bundle["tiers"]["b_dev"])
    assert ids == {
        "b-multiod-test-branch-seed-7",
        "b-multiod-test-network-seed-7",
        "b-multiod-test-execution-modes-seed-7",
        "b-multiod-test-benchmark-seed-7",
        "b-multiod-test-network-seed-11",
        "b-multiod-test-execution-modes-seed-11",
        "b-multiod-test-modes-n1-p2-seed-7",
    }
    cost = t1_suite.estimate_bundle_cost({
        "cells": [cell for cell in bundle["cells"]
                  if cell["cell_id"] in ids]})
    assert cost["simulator_calls"] == 54


def test_simulator_cost_bound_covers_four_way_branch_fanout_and_benchmark():
    cells = [
        {"cell_id": "branch", "driver": "CODE.experiment_platform.t1_tasks",
         "args": ["--task", "branch_alignment", "--max-branches", "5"]},
        {"cell_id": "bench", "driver":
         "CODE.experiment_platform.benchmark_decision",
         "args": ["--pool-sweep", "1,2"]},
    ]

    cost = t1_suite.estimate_bundle_cost({"cells": cells})

    assert cost["per_cell"][0]["simulator_calls"] == 26
    assert cost["per_cell"][1]["simulator_calls"] == 6
    assert cost["simulator_calls"] == 32


def test_validate_accepts_a_fresh_bundle_and_rejects_tampering(tmp_path):
    bundle_dir = _compiled(tmp_path)
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    bundle["cells"][0].pop("driver")
    (bundle_dir / "bundle.json").write_text(json.dumps(bundle))
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 2
    assert "missing driver" in done.stdout


def test_run_then_report_aggregates_every_cell(tmp_path):
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
                "--out", str(run_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    run = json.loads((run_dir / "run.json").read_text())
    assert run["status"] == "ok"
    assert run["counts"]["ok"] == run["counts"]["total"]
    assert run["counts"]["error"] == 0
    for record in run["cells"]:
        assert (run_dir / record["result_path"]).exists()
        assert record["identity"]["bundle_fingerprint"]
        assert record["identity"]["chain_sha256"]
        assert record["result_sha256"]
        assert record["result_schema"]
        assert record["input"]["driver_sha256"]
    done = _run("report", "--run-dir", str(run_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads((run_dir / "report.json").read_text())
    assert report["schema"] == t1_suite.SCHEMA_REPORT
    assert len(report["cells"]) == run["counts"]["total"]
    assert report["run_status"] == "ok"
    assert report["counts"]["verified_ok"] == run["counts"]["total"]
    assert report["counts"]["invalidated_at_report"] == 0
    assert (run_dir / "REPORT.md").exists()


def test_run_refuses_an_existing_destination(tmp_path):
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    run_dir.mkdir()
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
                "--out", str(run_dir))
    assert done.returncode == 2
    assert "exists" in done.stdout


def test_resume_retries_only_failed_cells(tmp_path):
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
         "--out", str(run_dir))
    run = json.loads((run_dir / "run.json").read_text())
    victim = run["cells"][0]["cell_id"]
    # simulate a failed cell: drop its result and mark it failed
    (run_dir / "cells" / victim / "result.json").unlink()
    for record in run["cells"]:
        if record["cell_id"] == victim:
            record["status"] = "error"
            record["exit_reason"] = "simulated"
    (run_dir / "run.json").write_text(json.dumps(run))
    done = _run("resume", "--run-dir", str(run_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    resumed = json.loads((run_dir / "run.json").read_text())
    assert resumed["resume"]["retried"] == [victim]
    assert resumed["counts"]["ok"] == resumed["counts"]["total"]
    assert (run_dir / "cells" / victim / "result.json").exists()


def test_any_post_compile_bundle_edit_is_refused_not_reused(tmp_path):
    """A structure-preserving parameter edit must not be silently accepted."""
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
         "--out", str(run_dir))
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    bundle["cells"][0]["description"] = "tampered after the run"
    (bundle_dir / "bundle.json").write_text(json.dumps(bundle))
    for command in (("validate", "--bundle", str(bundle_dir)),
                    ("run", "--bundle", str(bundle_dir), "--tier",
                     "acceptance", "--out", str(tmp_path / "again")),
                    ("resume", "--run-dir", str(run_dir))):
        done = _run(*command)
        assert done.returncode == 2, (command, done.stdout)
        assert "fingerprint mismatch" in done.stdout, done.stdout


def test_a_cell_parameter_change_is_refused(tmp_path):
    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    victim = next(c for c in bundle["cells"]
                  if c["cell_id"] == "hand-contention")
    victim["args"] = ["--scenario", "reachability", "--decision-id", "3"]
    (bundle_dir / "bundle.json").write_text(json.dumps(bundle))
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 2
    assert "fingerprint mismatch" in done.stdout
    assert "input binding changed" in done.stdout


def test_a_config_file_edit_is_refused(tmp_path):
    bundle_dir = _compiled(tmp_path)
    cfg = next((bundle_dir / "configs").glob("*.yaml"))
    cfg.write_text(cfg.read_text() + "\n# tampered\n")
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 2
    assert "input binding changed" in done.stdout


def test_a_dependency_source_change_is_refused(tmp_path, monkeypatch):
    """A new/changed module in the execution chain invalidates the bundle."""
    bundle_dir = _compiled(tmp_path)
    import CODE.experiment_platform.t1_suite as suite

    # simulate a changed chain WITHOUT touching real sources: append a synthetic
    # module that participates in the chain
    extra = tmp_path / "extra_chain_module.py"
    extra.write_text("VALUE = 1\n")
    real = suite.artifact_identity.execution_chain_paths

    def fake_paths(root=None):
        base = real() if root is None else real(root)
        return tuple(base) + (str(extra),)

    monkeypatch.setattr(suite.artifact_identity, "execution_chain_paths",
                        fake_paths)
    with pytest.raises(suite.SuiteError) as excinfo:
        suite.validate_bundle(bundle_dir)
    assert "execution chain changed" in str(excinfo.value)


def test_a_missing_result_invalidates_the_cell_and_is_rerun(tmp_path):
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
         "--out", str(run_dir))
    run = json.loads((run_dir / "run.json").read_text())
    victim = run["cells"][0]["cell_id"]
    (run_dir / "cells" / victim / "result.json").unlink()
    # the report must NOT still claim the run is ok -- and its exit status
    # must say so too, so automation cannot read a broken round as green
    done = _run("report", "--run-dir", str(run_dir))
    assert done.returncode == 3, done.stdout + done.stderr
    report = json.loads((run_dir / "report.json").read_text())
    assert report["run_status"] == "FAILED_CELLS"
    assert report["counts"]["invalidated_at_report"] == 1
    assert report["counts"]["not_ok_at_report"] == 1
    # resume re-runs exactly that cell and restores a verified ok
    done = _run("resume", "--run-dir", str(run_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    resumed = json.loads((run_dir / "run.json").read_text())
    assert resumed["resume"]["retried"] == [victim]
    assert resumed["counts"]["ok"] == resumed["counts"]["total"]


def test_a_tampered_result_is_detected_by_its_hash(tmp_path):
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
         "--out", str(run_dir))
    run = json.loads((run_dir / "run.json").read_text())
    victim = run["cells"][0]["cell_id"]
    result = run_dir / "cells" / victim / "result.json"
    payload = json.loads(result.read_text())
    payload["tampered"] = True
    result.write_text(json.dumps(payload))
    done = _run("report", "--run-dir", str(run_dir))
    assert done.returncode == 3, done.stdout + done.stderr
    report = json.loads((run_dir / "report.json").read_text())
    assert report["run_status"] == "FAILED_CELLS"
    row = next(r for r in report["cells"] if r["cell_id"] == victim)
    assert row["status"] == "invalidated"
    assert "content changed" in row["invalid_reason"]
    done = _run("resume", "--run-dir", str(run_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    resumed = json.loads((run_dir / "run.json").read_text())
    assert resumed["counts"]["ok"] == resumed["counts"]["total"]


def test_a_tiny_total_budget_marks_budget_exceeded_and_keeps_cells(tmp_path):
    contract = yaml.safe_load(CONTRACT.read_text())
    contract["budgets"] = {"total_wall_s": 0.0}
    small = tmp_path / "contract-tiny.yaml"
    small.write_text(yaml.safe_dump(contract, allow_unicode=True))
    bundle_dir = tmp_path / "compiled-tiny"
    done = _run("compile", "--contract", str(small), "--out", str(bundle_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    run_dir = tmp_path / "acceptance"
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
                "--out", str(run_dir))
    # a budget-exceeded round is NOT a success and its exit status says so
    assert done.returncode == 3, done.stdout + done.stderr
    run = json.loads((run_dir / "run.json").read_text())
    assert run["status"] == "BUDGET_EXCEEDED"
    assert run["counts"]["executed"] == 0
    assert run["counts"]["not_ok"] == run["counts"]["total"]


def test_a_failed_smoke_gate_stops_the_remaining_tier_and_is_terminal(
        tmp_path, monkeypatch):
    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    cell_ids = bundle["tiers"]["b_dev"]
    assert len(cell_ids) >= 2
    calls = []

    def fake_execute(cell, out_root, budgets, *, runtime_context=None):
        assert runtime_context is None
        calls.append(cell["cell_id"])
        cell_dir = Path(out_root) / "cells" / cell["cell_id"]
        cell_dir.mkdir(parents=True)
        payload = {"schema": "test-smoke/v1", "document": {}}
        result_path = cell_dir / "result.json"
        raw = json.dumps(payload).encode("utf-8")
        result_path.write_bytes(raw)
        return {
            "cell_id": cell["cell_id"], "status": "ok", "wall_s": 0.1,
            "result_path": str(Path("cells") / cell["cell_id"] / "result.json"),
            "result_sha256": hashlib.sha256(raw).hexdigest(),
            "result_schema": payload["schema"],
            "predicate_verdict": {"passed": True, "checks": []},
        }

    monkeypatch.setattr(t1_suite, "_execute_cell", fake_execute)
    run_dir = tmp_path / "smoke-failed"
    run = t1_suite.run_bundle(
        bundle_dir, "b_dev", run_dir,
        smoke_validator=lambda _cell, _record, _payload: {
            "passed": False, "reason": "test gate stop", "checks": []})

    assert run["status"] == "SMOKE_FAILED"
    assert calls == [cell_ids[0]]
    assert run["counts"]["executed"] == 1
    assert run["counts"]["not_ok"] == run["counts"]["total"] - 1
    assert run["counts"]["smoke_failed"] == 1
    assert run["smoke_gate"]["reason"] == "test gate stop"
    report = t1_suite.report_run(run_dir)
    assert report["run_status"] == "SMOKE_FAILED"
    assert report["cells"][0]["smoke_gate_passed"] is False
    with pytest.raises(t1_suite.SuiteError, match="terminal"):
        t1_suite.resume_run(run_dir)


def test_b_dev_pilot_can_continue_only_under_the_same_bundle_identity(
        tmp_path, monkeypatch):
    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    cell_ids = bundle["tiers"]["b_dev"]
    assert len(cell_ids) >= 2

    def fake_execute(cell, out_root, budgets, *, runtime_context=None):
        assert runtime_context is None
        calls = t1_suite.estimate_bundle_cost({"cells": [cell]})[
            "simulator_calls"]
        cell_dir = Path(out_root) / "cells" / cell["cell_id"]
        cell_dir.mkdir(parents=True, exist_ok=True)
        payload = {"schema": "test-b-dev/v1"}
        raw = json.dumps(payload).encode("utf-8")
        (cell_dir / "result.json").write_bytes(raw)
        return {"cell_id": cell["cell_id"], "status": "ok", "wall_s": 0.1,
                "result_path": str(Path("cells") / cell["cell_id"]
                                    / "result.json"),
                "result_sha256": hashlib.sha256(raw).hexdigest(),
                "result_schema": payload["schema"],
                "predicate_verdict": {"passed": True, "checks": []},
                "simulator_calls": {"started": calls, "ended": calls,
                                     "failed": 0, "timed_out": 0,
                                     "interrupted": 0, "unresolved": 0,
                                     "simulator_wall_s": 0.05 * calls}}

    monkeypatch.setattr(t1_suite, "_execute_cell", fake_execute)
    run_dir = tmp_path / "staged-run"
    pilot = t1_suite.run_bundle(
        bundle_dir, "b_dev", run_dir, cell_ids=[cell_ids[0]],
        smoke_validator=lambda *_: {"passed": True, "checks": []})
    assert pilot["status"] == "INCOMPLETE"
    assert pilot["pending_cell_ids"] == cell_ids[1:]

    run_path = run_dir / "run.json"
    saved_run = json.loads(run_path.read_text())
    mismatched_run = dict(saved_run, bundle_fingerprint="wrong-bundle")
    run_path.write_text(json.dumps(mismatched_run), encoding="utf-8")
    with pytest.raises(t1_suite.SuiteError, match="identity"):
        t1_suite.run_bundle(
            bundle_dir, "b_dev", run_dir, cell_ids=cell_ids[1:],
            append=True,
            smoke_validator=None)
    run_path.write_text(json.dumps(saved_run), encoding="utf-8")

    completed = t1_suite.run_bundle(
        bundle_dir, "b_dev", run_dir, cell_ids=cell_ids[1:], append=True)
    assert completed["status"] == "ok"
    assert completed["counts"]["ok"] == len(cell_ids)
    assert completed["simulator_call_accounting"]["started"] == \
        t1_suite.estimate_bundle_cost({"cells": [
            next(cell for cell in bundle["cells"] if cell["cell_id"] == cid)
            for cid in cell_ids]})["simulator_calls"]


def test_a0_smoke_requires_real_four_arm_traffic_and_delivery():
    arms = [{
        "arm": name,
        "scope": {"packets_in_trace": 4, "forward_decisions": 8},
        "outcome": {"offered": 4, "delivered": 3,
                    "delivered_bits": 3000,
                    "goodput_bps_in_window": 900.0},
        # Production nests the exact packet partition on the arm's
        # outcome_document.  Keep a contradictory decoy at the wrong level
        # so this test catches schema drift in the smoke gate.
        "network_outcome": {"partition_exact": False},
        "outcome_document": {"partition_exact": True},
    } for name in ("stale", "now", "common", "candidate")]
    payload = {"document": {
        "status": "ok", "arms": arms, "failures": [],
        "source": {"synthetic_workload": {"summary": {
            "unique_od_count": 6,
            "flows": [{"id": f"od-{i}"} for i in range(6)],
            "active_od_counts_by_1s_bin": {"0": 2, "1": 6},
        }}},
    }}
    record = {"status": "ok"}
    cell = {"cell_id": "b-steady_uniform_negative_control-network-seed-7"}

    assert t1_development._negative_control_smoke(
        cell, record, payload)["passed"] is True
    arms[0]["outcome_document"]["partition_exact"] = False
    arms[0]["network_outcome"]["partition_exact"] = True
    failed_partition = t1_development._negative_control_smoke(
        cell, record, payload)
    assert failed_partition["passed"] is False
    assert any(check["name"] == "stale_event_partition_exact"
               and check["value"] is False and not check["passed"]
               for check in failed_partition["checks"])
    arms[0]["outcome_document"]["partition_exact"] = True
    arms[2]["outcome"]["delivered"] = 0
    failed = t1_development._negative_control_smoke(cell, record, payload)
    assert failed["passed"] is False
    assert any(check["name"] == "common_delivered_packets_positive"
               and not check["passed"] for check in failed["checks"])
    arms[2]["outcome"]["delivered"] = 3
    arms[2]["outcome"]["delivered_bits"] = 0
    failed_bits = t1_development._negative_control_smoke(
        cell, record, payload)
    assert failed_bits["passed"] is False
    assert any(check["name"] == "common_delivered_bits_positive"
               and not check["passed"] for check in failed_bits["checks"])


def test_a0_smoke_is_the_first_predeclared_cell_and_is_in_the_cost_bound(
        tmp_path):
    contract_path = ROOT / "CODE/work/WP-T1-COMPLETE/contract_dev_a.yaml"
    bundle_dir = tmp_path / "development-bundle"
    bundle = t1_suite.compile_bundle(contract_path, bundle_dir)
    b_dev_ids = bundle["tiers"]["b_dev"]
    cells = {cell["cell_id"]: cell for cell in bundle["cells"]}
    b_dev_cells = [cells[cell_id] for cell_id in b_dev_ids]

    assert len(b_dev_cells) == 13
    assert t1_development._validate_a0_smoke_cell(
        yaml.safe_load(contract_path.read_text()), b_dev_cells) == b_dev_ids[0]
    assert b_dev_ids[0] == "b-steady_uniform_negative_control-network-seed-7"
    cost = t1_suite.estimate_bundle_cost({"cells": b_dev_cells})
    assert cost["simulator_calls"] == 83
    with pytest.raises(RuntimeError, match="first b_dev cell"):
        t1_development._validate_a0_smoke_cell(
            yaml.safe_load(contract_path.read_text()), b_dev_cells[1:])


def test_an_unknown_tier_is_refused(tmp_path):
    bundle_dir = _compiled(tmp_path)
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "confirm",
                "--out", str(tmp_path / "r"))
    assert done.returncode == 2
    assert "unknown tier" in done.stdout


# ------------------------------------------------- R3: predicates and tiers
def test_a_cell_whose_predicate_fails_is_not_reported_ok(tmp_path):
    """A zero exit code is not evidence: the mechanism must have fired."""
    import CODE.experiment_platform.t1_suite as suite

    cell = {
        "cell_id": "impossible-cache-hit", "group": "test",
        "driver": "CODE.experiment_platform.execution_compare",
        "args": ["--scenario", "reachability"],
        "description": "no same-flow packets, so a cache hit is impossible",
        "seed": None,
        "predicate": {"kind": "execution_modes",
                      "require": {"per_flow": {"min_cache_hits": 5}}},
        "input": {},
    }
    out = tmp_path / "cells-out"
    out.mkdir()
    record = suite._execute_cell(cell, out, suite.DEFAULT_BUDGETS)
    assert record["returncode"] == 0
    assert record["status"] == "predicate_failed"
    assert "cache hits" in record["predicate_verdict"]["reason"]
    assert record["predicate_verdict"]["checks"]


def test_common_horizon_predicate_requires_explicit_tie_disclosure():
    import CODE.experiment_platform.t1_suite as suite

    names = ["mean_eta_offset", "median_eta_offset", "p25_offset",
             "p50_offset", "p75_offset"]
    selection = {
        "status": "selected", "candidate": "mean_eta_offset",
        "candidate_order": names,
        "tied_candidates": ["mean_eta_offset"],
        "selected_by_tie_break": False, "tie_tolerance": 1e-12,
    }
    result = {
        "status": "ok", "failed_units": 0, "task": "branch_alignment",
        "document": {
            "deadline": {"deadline_s": 30.0},
            "common_horizon_calibration": {
                "status": "projected", "cross_arm_leakage": False,
                "candidate_definitions": {name: {} for name in names},
                "selection": selection,
            }
        },
    }
    predicate = {"kind": "t1_task", "require": {
        "require_task": "branch_alignment",
        "require_common_horizon_audit": True}}

    assert suite.check_predicate(result, predicate)["passed"] is True
    result["document"]["common_horizon_calibration"]["selection"] = {
        k: v for k, v in selection.items() if k not in (
            "tied_candidates", "selected_by_tie_break", "tie_tolerance")}
    assert suite.check_predicate(result, predicate)["passed"] is False


def test_the_dev_tier_runs_with_behaviour_predicates(tmp_path):
    bundle_dir = _small_compiled(tmp_path)
    run_dir = tmp_path / "dev"
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "dev",
                "--out", str(run_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    run = json.loads((run_dir / "run.json").read_text())
    assert run["counts"]["total"] >= 5
    assert run["counts"]["ok"] == run["counts"]["total"], [
        (r["cell_id"], r["status"], r.get("exit_reason"))
        for r in run["cells"] if r["status"] != "ok"]
    kinds = set()
    for record in run["cells"]:
        kinds.add(record["predicate"]["kind"])
        assert record["predicate_verdict"]["passed"] is True
        assert record["predicate_verdict"]["checks"]
    # the dev tier covers the execution sweep AND the A2 task cells: the
    # offline branch blocks, the four-arm network runs and the candidate
    # horizons all go through the same t1_task predicate
    assert kinds == {"execution_modes", "t1_task"}, kinds


def test_the_formal_package_is_compiled_and_validated_not_run(tmp_path):
    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    package = bundle["formal_package"]
    assert package["execution_status"] == "NOT_EXECUTED"
    assert package["status"] == "PENDING_DEV_SELECTION"
    assert package["design_state"]["required"]
    for field in ("dev_confirm_separation", "sample_size_plan",
                  "effect_thresholds", "information_permissions",
                  "cost_sources", "scenario_validity", "failure_rules",
                  "planned_matrix"):
        assert field in package
    assert package["sample_size_plan"]["formula_examples"]
    assert "formal" in bundle["tiers"]
    # A5: the formal tier is no longer refused unconditionally.  With no
    # confirmation cell compiled there is nothing to authorize, so the refusal
    # is now about the EMPTY matrix, and with cells present it is about the
    # missing authorization -- never a permanent "not runnable" flag.
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "formal",
                "--out", str(tmp_path / "formal"))
    assert done.returncode == 2
    assert "formal tier is empty" in done.stdout


def test_validate_requires_the_formal_package(tmp_path):
    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    bundle["formal_package"] = {}
    (bundle_dir / "bundle.json").write_text(json.dumps(bundle))
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 2
    assert "bundle validation failed" in done.stdout
    assert "formal" in done.stdout


def test_the_report_carries_the_frozen_statistics(tmp_path):
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
         "--out", str(run_dir))
    _run("report", "--run-dir", str(run_dir))
    report = json.loads((run_dir / "report.json").read_text())
    stats = report["statistics"]
    assert stats["blocks"] >= 2
    assert stats["unit_of_replication"] == "scenario x trace x seed (one cell here)"
    assert stats["minimum_substantive_difference"] == 0.01
    assert stats["sensitivity"] == [0.005, 0.02]
    assert len(stats["per_block"]) == stats["blocks"]
    assert stats["bootstrap"]["n_boot"] == 10000
    assert stats["sample_size_plan"]["rule"].startswith("n = max(20")
    for block in stats["per_block"]:
        assert "common_regret" in block
        assert "candidate_regret" in block
    # with identical dev differences the horizon cannot be selected: the report
    # must say so instead of calling the online common arm "common_strong"
    assert stats["common_strong"]["frozen"] is False
    assert "cannot discriminate" in stats["common_strong"]["reason"]
    assert "NOT frozen" in stats["primary_comparison"]


def test_the_acceptance_matrix_records_its_declared_overrides(tmp_path):
    bundle_dir = _compiled(tmp_path)
    run_dir = tmp_path / "acceptance"
    _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
         "--out", str(run_dir))
    run = json.loads((run_dir / "run.json").read_text())
    exec_cell = next(r for r in run["cells"]
                     if r["cell_id"] == "exec-five-modes")
    payload = json.loads((run_dir / exec_cell["result_path"]).read_text())
    overrides = payload["source"]["declared_overrides"]
    assert overrides["execution"]["compute_servers_per_satellite"] == 1
    assert overrides["execution"]["compute_delay_s"] == 0.05
    assert overrides["time_alignment"]["query_delay_s"] == 0.001



# ------------------------------- S4: a predicate failure fails the whole round
def _compiled_with_impossible_predicate(tmp_path):
    """A bundle as a compiler WOULD produce it, but with one cell whose
    pre-declared behaviour can never pass."""
    import CODE.experiment_platform.t1_suite as suite

    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    cell = next(c for c in bundle["cells"]
                if c["cell_id"] == "hand-reachability")
    cell["predicate"] = {"kind": "time_alignment",
                         "require": {"min_candidates": 999}}
    cell["input"] = suite._cell_input_binding(cell)
    bundle["bundle_fingerprint"] = suite._bundle_fingerprint(bundle)
    (bundle_dir / "bundle.json").write_text(json.dumps(bundle))
    return bundle_dir


def test_a_predicate_failure_fails_run_report_and_resume(tmp_path):
    bundle_dir = _compiled_with_impossible_predicate(tmp_path)
    run_dir = tmp_path / "acceptance"
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
                "--out", str(run_dir))
    assert done.returncode == 3, done.stdout + done.stderr
    run = json.loads((run_dir / "run.json").read_text())
    assert run["status"] == "FAILED_CELLS"
    assert run["counts"]["predicate_failed"] == 1
    assert run["counts"]["not_ok"] == 1
    assert run["counts"]["ok"] == run["counts"]["total"] - 1
    assert run["counts"]["not_ok"] == (run["counts"]["executed"]
                                       - run["counts"]["ok"])
    victim = next(r for r in run["cells"]
                  if r["status"] == "predicate_failed")
    assert "candidates >= min" in victim["exit_reason"]

    done = _run("report", "--run-dir", str(run_dir))
    assert done.returncode == 3, done.stdout + done.stderr
    report = json.loads((run_dir / "report.json").read_text())
    assert report["run_status"] == "FAILED_CELLS"
    assert report["counts"]["predicate_failed_at_report"] == 1
    assert report["counts"]["not_ok_at_report"] == 1
    stats = report["statistics"]
    excluded = {e["unit"]: e["reason"] for e in stats["excluded"]}
    assert "hand-reachability" in excluded
    assert "predicate" in excluded["hand-reachability"]
    assert all(b["unit"] != "hand-reachability"
               for b in stats["per_block"])
    assert stats["blocks"] == len(stats["per_block"])
    assert "integrity AND behaviour predicate" in stats["rule"]

    # resume must NOT turn a behaviour failure into a green round
    done = _run("resume", "--run-dir", str(run_dir))
    assert done.returncode == 3, done.stdout + done.stderr
    resumed = json.loads((run_dir / "run.json").read_text())
    assert resumed["status"] == "FAILED_CELLS"
    assert resumed["counts"]["predicate_failed"] == 1


def test_a_successful_round_still_exits_zero(tmp_path):
    bundle_dir = _compiled(tmp_path)
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "acceptance",
                "--out", str(tmp_path / "ok"))
    assert done.returncode == 0, done.stdout + done.stderr
    done = _run("report", "--run-dir", str(tmp_path / "ok"))
    assert done.returncode == 0, done.stdout + done.stderr



# ------------------------------------- S7: formal package and common_strong
def test_the_formal_package_thresholds_are_part_of_the_fingerprint(tmp_path):
    """The reviewer's repro: rewriting a threshold must NOT stay valid."""
    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    bundle["formal_package"]["effect_thresholds"][
        "minimum_substantive_difference"] = 999
    (bundle_dir / "bundle.json").write_text(json.dumps(bundle))
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 2, done.stdout
    assert "fingerprint mismatch" in done.stdout


def test_the_formal_design_is_part_of_the_fingerprint(tmp_path):
    bundle_dir = _compiled(tmp_path)
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    bundle["formal_design"] = {"ready": True, "confirm_seeds": [1001]}
    (bundle_dir / "bundle.json").write_text(json.dumps(bundle))
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 2
    assert "fingerprint mismatch" in done.stdout


def _ready_contract(tmp_path):
    contract = yaml.safe_load(CONTRACT.read_text())
    frozen = tmp_path / "deadline.json"
    frozen.write_text(json.dumps({"deadline_s": 4.0, "source": "dev_p95",
                                  "frozen_at_sha": "abc"}))
    contract["formal_design"] = {
        "ready": True,
        "confirm_seeds": [1001, 1002],
        "deadline_file": str(frozen),
        "common_strong_horizon_s": 0.75,
        "sample_size": 20,
    }
    path = tmp_path / "contract-ready.yaml"
    path.write_text(yaml.safe_dump(contract, allow_unicode=True))
    return path, frozen


def test_a_ready_design_compiles_real_pending_cells(tmp_path):
    contract_path, _frozen = _ready_contract(tmp_path)
    bundle_dir = tmp_path / "compiled-ready"
    done = _run("compile", "--contract", str(contract_path),
                "--out", str(bundle_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    assert bundle["tiers"]["formal"] == ["confirm-seed-1001",
                                         "confirm-seed-1002"]
    assert bundle["formal_package"]["status"] == "READY_TO_EXECUTE"
    assert bundle["formal_package"]["design_state"]["deadline_sha256"]
    for cell in bundle["cells"]:
        if cell["group"] != "confirm":
            continue
        assert cell["seed"] in (1001, 1002)
        assert "--deadline-from" in cell["args"]
        assert cell["input"]["files"]["--deadline-from"]["sha256"], cell
    done = _run("validate", "--bundle", str(bundle_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    # compiled and validated, and still NOT runnable: the refusal is now the
    # ABSENT AUTHORIZATION rather than a permanent flag
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "formal",
                "--out", str(tmp_path / "formal"))
    assert done.returncode == 2
    assert "authorization" in done.stdout


def test_a_ready_design_without_a_frozen_deadline_is_refused(tmp_path):
    contract_path, _frozen = _ready_contract(tmp_path)
    contract = yaml.safe_load(contract_path.read_text())
    contract["formal_design"]["deadline_file"] = str(tmp_path / "nope.json")
    bad = tmp_path / "contract-bad.yaml"
    bad.write_text(yaml.safe_dump(contract, allow_unicode=True))
    done = _run("compile", "--contract", str(bad), "--out",
                str(tmp_path / "b2"))
    assert done.returncode == 2
    assert "frozen D" in done.stdout


def test_the_common_strong_state_is_reported_with_its_reason(tmp_path):
    bundle_dir = _small_compiled(tmp_path)
    run_dir = tmp_path / "dev"
    _run("run", "--bundle", str(bundle_dir), "--tier", "dev",
         "--out", str(run_dir))
    _run("report", "--run-dir", str(run_dir))
    report = json.loads((run_dir / "report.json").read_text())
    # The five candidates are still declared, and the legacy heuristic still
    # refuses to call anything frozen without development blocks.
    state = report["statistics"]["common_strong"]
    assert state["frozen"] is False
    assert set(state["candidates"]) == {
        "mean_eta_offset", "median_eta_offset", "p25_offset", "p50_offset",
        "p75_offset"}
    # the legacy heuristic now has no time-alignment cell to look at, and it
    # says so instead of reporting a state it did not compute
    assert state["evaluated"] == []
    assert state["reason"]
    # A3: with the VM-projected candidate file in the contract the REAL
    # selection runs.  What it finds is reported, not tuned: on this profile
    # the five candidates tie exactly and the loss is identical for all four
    # arms, which is a RESULT (the scenario does not discriminate) and not a
    # platform failure.
    design = report["statistics"]["development_design"]
    assert design["status"] == "SELECTED", design
    losses = design["loss_table"]["losses"]
    assert sorted(losses) == ["mean_eta_offset", "median_eta_offset",
                              "p25_offset", "p50_offset", "p75_offset"]
    selection = design["selection"]
    assert selection["selected"] in losses
    outcomes = selection["outcomes"]
    assert set(outcomes) == {"tie", "zero_variance",
                             "insufficient_scenario_coverage",
                             "insufficient_statistical_evidence"}
    assert outcomes["insufficient_statistical_evidence"] is True, (
        "four development blocks cannot size a confirmation")


def test_a_contract_without_the_projected_candidates_refuses_to_select(tmp_path):
    """The five candidates are pre-declared: two are not a selection."""
    contract = yaml.safe_load(CONTRACT.read_text())
    contract["formal_design"] = {k: v for k, v in
                                 (contract.get("formal_design") or {}).items()
                                 if k != "horizon_candidates_file"}
    path = tmp_path / "contract-no-horizons.yaml"
    path.write_text(yaml.safe_dump(contract, allow_unicode=True))
    bundle_dir = tmp_path / "compiled-no-horizons"
    done = _run("compile", "--contract", str(path), "--out", str(bundle_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    run_dir = tmp_path / "dev-no-horizons"
    _run("run", "--bundle", str(bundle_dir), "--tier", "dev",
         "--out", str(run_dir))
    _run("report", "--run-dir", str(run_dir))
    report = json.loads((run_dir / "report.json").read_text())
    design = report["statistics"]["development_design"]
    assert design["status"] == "PENDING_HORIZON_CANDIDATES"
    assert sorted(design["missing"]) == ["p25_offset", "p50_offset",
                                         "p75_offset"]
    assert "loss_table" not in design and "selection" not in design


def test_the_five_candidates_get_their_own_loss_columns(tmp_path):
    """A candidate must be scored on ITS OWN blocks, never on the others'."""
    contract = yaml.safe_load(CONTRACT.read_text())
    horizons = tmp_path / "horizons.json"
    horizons.write_text(json.dumps({
        "schema": "t1-candidate-horizons/v1",
        "analysis_phase": "development",
        "candidates": [
            {"candidate": "mean_eta_offset", "kind": "rule",
             "horizon_s": 0.5},
            {"candidate": "median_eta_offset", "kind": "rule",
             "horizon_s": 0.25},
            {"candidate": "p25_offset", "kind": "fixed_h", "horizon_s": 0.1,
             "quantile": 0.25},
            {"candidate": "p50_offset", "kind": "fixed_h", "horizon_s": 0.25,
             "quantile": 0.5},
            {"candidate": "p75_offset", "kind": "fixed_h", "horizon_s": 0.75,
             "quantile": 0.75}],
        "quantile_source": "development_baseline_candidate_eta_offsets",
    }))
    contract.setdefault("formal_design", {})["horizon_candidates_file"] = str(
        horizons)
    path = tmp_path / "contract-horizons.yaml"
    path.write_text(yaml.safe_dump(contract, allow_unicode=True))
    bundle_dir = tmp_path / "compiled-horizons"
    done = _run("compile", "--contract", str(path), "--out", str(bundle_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    bundle = json.loads((bundle_dir / "bundle.json").read_text())
    ids = [c["cell_id"] for c in bundle["cells"]
           if c["cell_id"].startswith("dev-common-")]
    assert len(ids) == 5 * 4, ids
    run_dir = tmp_path / "dev-horizons"
    _run("run", "--bundle", str(bundle_dir), "--tier", "dev",
         "--out", str(run_dir))
    _run("report", "--run-dir", str(run_dir))
    report = json.loads((run_dir / "report.json").read_text())
    design = report["statistics"]["development_design"]
    table = design["loss_table"]
    losses = table["losses"]
    assert losses, table
    # THE claim that was broken before the integration: a candidate column may
    # only contain blocks that belong to THAT candidate.  Handing every
    # candidate the same block set produced one shared column and then an
    # exact tie -- a comparison that never happened.
    provenance = {}
    for row in table["blocks"]:
        provenance.setdefault(row.get("arm"), set()).add(row.get("unit"))
    for name, values in losses.items():
        own = provenance.get(name, set())
        assert set(values) <= own, (
            name, sorted(set(values) - own))
        assert values, name
    # a candidate with no scorable block must be REPORTED, never scored from
    # somebody else blocks
    scored = set(losses)
    for item in table.get("unscored") or []:
        name = item.get("candidate")
        if name and name not in scored:
            assert item.get("reason"), item
