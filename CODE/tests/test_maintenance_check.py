from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "CODE" / "scripts" / "maintenance_check.py"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "maintenance test")
    _git(repo, "config", "user.email", "maintenance@example.invalid")
    (repo / "ANALYSIS").mkdir()
    (repo / "CODE" / "scripts").mkdir(parents=True)
    (repo / "README.md").write_text("See [STATUS.md](STATUS.md).\n", encoding="utf-8")
    (repo / "STATUS.md").write_text("Current status.\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text(
        "CODE/scripts/remote/publish-release-remote.sh\n"
        "CODE/scripts/remote/run-release-remote.sh\n"
        "CODE/scripts/remote/pull-release-results-remote.sh\n",
        encoding="utf-8",
    )
    (repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl").write_text("", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-B", str(CHECKER), "--repo", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.stdout, result.stderr
    return result


def _report(result: subprocess.CompletedProcess[str]) -> dict:
    payload = json.loads(result.stdout)
    assert "warnings" in payload["summary"]
    assert "checked" in payload["summary"]
    assert payload["scope"]
    return payload


def _index_row(run_id: str = "run-001", receipt_sha256: str | None = None) -> dict:
    return {
        "run_id": run_id,
        "release_id": "a" * 40 + "-" + "b" * 64,
        "receipt_sha256": receipt_sha256 or "c" * 64,
        "pullback_status": "VERIFIED",
        "evidence_uri": f"evidence://t1/{run_id}",
    }


def _write_verified_evidence(repo: Path, evidence_dir: Path, run_id: str) -> str:
    from CODE.scripts.remote import release_protocol as protocol

    evidence_dir.mkdir(parents=True)
    release_id = _index_row(run_id)["release_id"]
    manifest = {
        "schema": protocol.RUN_SCHEMA,
        "run_id": run_id,
        "release_id": release_id,
        "source_git_commit": "d" * 40,
        "source_tree_sha256": "e" * 64,
        "archive_sha256": "f" * 64,
        "execution_class": "diagnostic",
        "status": "completed",
        "finished_at": "2026-09-29T00:00:00+00:00",
        "exit_code": 0,
    }
    manifest_path = evidence_dir / protocol.RUN_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (evidence_dir / "run.log").write_text("local fixture\n", encoding="utf-8")
    receipt = {
        "schema": protocol.RUN_RECEIPT_SCHEMA,
        "run_id": run_id,
        "release_id": release_id,
        "execution_class": "diagnostic",
        "source_git_commit": manifest["source_git_commit"],
        "source_tree_sha256": manifest["source_tree_sha256"],
        "archive_sha256": manifest["archive_sha256"],
        "run_manifest_sha256": protocol.sha256_file(manifest_path),
        "exit_code": 0,
        "status": "completed",
        "files": protocol._hash_run_files(evidence_dir, exclude={protocol.RUN_RECEIPT_NAME}),
    }
    receipt["receipt_sha256"] = protocol.sha256_bytes(protocol.canonical_json(receipt))
    (evidence_dir / protocol.RUN_RECEIPT_NAME).write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    return receipt["receipt_sha256"]


def test_tracked_outputs_env_models_and_cache_are_errors_but_dirty_is_preserved(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    forbidden = {
        "out/run/result.json": "{}\n",
        "CODE/leo_sim_out/result.json": "{}\n",
        "CODE/.pytest_cache/state": "cache\n",
        ".env": "must not inspect contents\n",
        "CODE/models/weights.safetensors": "model bytes\n",
        "CODE/models/checkpoint.keras": "model bytes\n",
    }
    for relative, contents in forbidden.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    (repo / ".env.template").write_text("template\n", encoding="utf-8")
    (repo / "remote.env.template").write_text("template\n", encoding="utf-8")
    _git(repo, "add", "-f", *forbidden.keys(), ".env.template", "remote.env.template")
    _git(repo, "commit", "-q", "-m", "forbidden tracked paths fixture")

    dirty = repo / "untracked-note.txt"
    dirty.write_text("preserve me\n", encoding="utf-8")
    status_before = _git(repo, "status", "--porcelain=v1")
    result = _run(repo)

    assert result.returncode == 1
    report = _report(result)
    assert len(report["errors"]) >= len(forbidden)
    assert all(any(relative in error for error in report["errors"]) for relative in forbidden)
    assert any("dirty" in message.lower() for message in report["info"])
    assert dirty.read_text(encoding="utf-8") == "preserve me\n"
    assert _git(repo, "status", "--porcelain=v1") == status_before
    assert not any(".env.template" in error or "remote.env.template" in error
                   for error in report["errors"])


def test_index_requires_identity_and_verified_status(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    row = _index_row()
    row["receipt_sha256"] = "bad"
    row["pullback_status"] = "PENDING"
    with (repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")

    result = _run(repo)

    assert result.returncode == 1
    report = _report(result)
    assert any("receipt_sha256" in error for error in report["errors"])
    assert any("pullback_status" in error for error in report["errors"])


def test_identical_duplicate_warns_and_conflicting_run_id_errors(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    row = _index_row()
    index = repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl"
    index.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n", encoding="utf-8")

    same = _report(_run(repo))
    assert any("duplicate" in warning.lower() for warning in same["warnings"])
    assert not same["errors"]

    changed = dict(row, receipt_sha256="9" * 64)
    index.write_text(json.dumps(row) + "\n" + json.dumps(changed) + "\n", encoding="utf-8")
    conflict = _run(repo)
    assert conflict.returncode == 1
    assert any("run_id" in error and "different" in error.lower()
               for error in _report(conflict)["errors"])


def test_verify_evidence_is_local_and_detects_index_receipt_tampering(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    evidence = tmp_path / "evidence" / "run-001"
    receipt_sha256 = _write_verified_evidence(repo, evidence, "run-001")
    row = _index_row(receipt_sha256=receipt_sha256)
    index = repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl"
    index.write_text(json.dumps(row) + "\n", encoding="utf-8")

    before = {
        (root, path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in (repo, evidence)
        for path in root.rglob("*")
        if path.is_file()
    }
    verified = _run(repo, "--verify-evidence", "--evidence-root", str(evidence.parent))
    assert verified.returncode == 0, verified.stderr
    assert not _report(verified)["errors"]
    after = {
        (root, path.relative_to(root).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
        for root in (repo, evidence)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before

    row["receipt_sha256"] = "9" * 64
    index.write_text(json.dumps(row) + "\n", encoding="utf-8")
    tampered = _run(repo, "--verify-evidence", "--evidence-root", str(evidence.parent))
    assert tampered.returncode == 1
    assert any("receipt" in error.lower() for error in _report(tampered)["errors"])


def test_missing_local_evidence_is_only_a_warning_and_read_only_check_changes_nothing(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    row = _index_row()
    index = repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl"
    index.write_text(json.dumps(row) + "\n", encoding="utf-8")
    before = {
        path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in repo.rglob("*")
        if path.is_file()
    }

    evidence_root = tmp_path / "missing-evidence-root"
    result = _run(repo, "--verify-evidence", "--evidence-root", str(evidence_root))

    assert result.returncode == 0
    report = _report(result)
    assert not report["errors"]
    assert any("evidence" in warning.lower() and "missing" in warning.lower()
               for warning in report["warnings"])
    after = {
        path.relative_to(repo).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in repo.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_opaque_evidence_uri_requires_explicit_external_root_for_verification(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    index = repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl"
    index.write_text(json.dumps(_index_row()) + "\n", encoding="utf-8")

    unconfigured = _run(repo, "--verify-evidence")
    assert unconfigured.returncode == 0, unconfigured.stderr
    assert any("evidence root" in warning.lower()
               for warning in _report(unconfigured)["warnings"])

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    missing = _run(repo, "--verify-evidence", "--evidence-root", str(evidence_root))
    assert missing.returncode == 0, missing.stderr
    assert any("missing" in warning.lower() for warning in _report(missing)["warnings"])


def test_deployment_index_rejects_local_paths_in_evidence_uri(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    row = _index_row()
    row["evidence_uri"] = "../private/evidence/run-001"
    (repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )

    result = _run(repo)
    assert result.returncode == 1
    assert any("evidence_uri" in error for error in _report(result)["errors"])


@pytest.mark.parametrize("case", ["read-only", "history"])
def test_scope_and_old_workflow_history_are_not_misclassified(tmp_path: Path, case: str) -> None:
    repo = _repo(tmp_path)
    if case == "history":
        agents = repo / "AGENTS.md"
        agents.write_text(
            agents.read_text(encoding="utf-8")
            + "\nHistorical note: the former rsync workflow is retired.\n",
            encoding="utf-8",
        )
    result = _run(repo)
    assert result.returncode == 0, result.stderr
    report = _report(result)
    assert "does not establish" in report["scope"].lower()
    assert not report["errors"]


def test_missing_status_entry_target_is_an_error(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "STATUS.md").unlink()

    result = _run(repo)

    assert result.returncode == 1
    report = _report(result)
    assert any("STATUS.md" in error for error in report["errors"])


def test_nul_evidence_uri_is_reported_as_invalid_input_not_a_checker_crash(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    row = _index_row()
    row["evidence_uri"] = "\x00"
    (repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl").write_text(
        json.dumps(row) + "\n", encoding="utf-8"
    )

    result = _run(repo)

    assert result.stdout.strip(), result.stderr
    assert result.returncode == 1
    report = _report(result)
    assert any("evidence_uri" in error for error in report["errors"])


def test_invalid_utf8_deployment_index_is_reported_without_crashing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl").write_bytes(b"\xff\n")

    result = _run(repo)

    assert result.returncode == 1
    report = _report(result)
    assert any("utf-8" in error.lower() for error in report["errors"])
    assert "Traceback" not in result.stderr
