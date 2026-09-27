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
    # the report must NOT still claim the run is ok
    done = _run("report", "--run-dir", str(run_dir))
    assert done.returncode == 0, done.stdout + done.stderr
    report = json.loads((run_dir / "report.json").read_text())
    assert report["run_status"] == "FAILED_CELLS"
    assert report["counts"]["invalidated_at_report"] == 1
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
    assert done.returncode == 0, done.stdout + done.stderr
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
    assert done.returncode == 0, done.stdout + done.stderr
    run = json.loads((run_dir / "run.json").read_text())
    assert run["status"] == "BUDGET_EXCEEDED"
    assert run["counts"]["executed"] == 0


def test_an_unknown_tier_is_refused(tmp_path):
    bundle_dir = _compiled(tmp_path)
    done = _run("run", "--bundle", str(bundle_dir), "--tier", "formal",
                "--out", str(tmp_path / "r"))
    assert done.returncode == 2
    assert "unknown tier" in done.stdout
