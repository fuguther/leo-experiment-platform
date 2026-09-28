"""T1-COMPLETE P10: suite compile/validate/run/resume/report and budgets."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from CODE.experiment_platform import t1_suite

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "CODE/work/WP-T1-COMPLETE/contract.yaml"


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "CODE.experiment_platform.t1_suite", *args],
        cwd=str(ROOT), capture_output=True, text=True, timeout=1800, check=False)


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


def test_compile_enforces_the_max_cell_budget(tmp_path):
    contract = yaml.safe_load(CONTRACT.read_text())
    contract["budgets"] = {"max_cells": 2}
    small = tmp_path / "contract-small.yaml"
    small.write_text(yaml.safe_dump(contract, allow_unicode=True))
    done = _run("compile", "--contract", str(small),
                "--out", str(tmp_path / "b"))
    assert done.returncode == 2
    assert "max_cells" in done.stdout


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


def test_the_dev_tier_runs_with_behaviour_predicates(tmp_path):
    bundle_dir = _compiled(tmp_path)
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
    bundle_dir = _compiled(tmp_path)
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
    # A3: the REAL selection lives in development_design, and with no
    # projected horizon file it says so instead of inventing three horizons.
    design = report["statistics"]["development_design"]
    assert design["status"] == "PENDING_HORIZON_CANDIDATES"
    assert sorted(design["missing"]) == ["p25_offset", "p50_offset",
                                         "p75_offset"]
    assert design["recovery"]
    assert design["blocks"] > 0, design
    assert design["loss_table"]["candidates"], design


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

