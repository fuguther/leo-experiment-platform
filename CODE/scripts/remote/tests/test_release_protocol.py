from __future__ import annotations

import subprocess
import json
import os
import stat
import tarfile
import io
import fcntl
import hashlib
from pathlib import PurePosixPath
from pathlib import Path

import pytest

from CODE.scripts.remote.release_protocol import build_release


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(path: Path) -> tuple[Path, str]:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "release test")
    _git(path, "config", "user.email", "release-test@example.invalid")
    for name in ("ANALYSIS", "CODE", "EXPERIMENTS", "docs", "lines"):
        directory = path / name
        directory.mkdir()
        (directory / ".keep").write_text(f"{name}\n", encoding="utf-8")
    for name in (".gitignore", "AGENTS.md", "BACKLOG.md", "CHARTER.md",
                 "README.md", "SOURCE-COMMIT.txt", "STATUS.md", "WORKING-MODEL.md"):
        (path / name).write_text(f"{name}\n", encoding="utf-8")
    (path / "README.md").write_text("from commit\n", encoding="utf-8")
    (path / "CODE" / "payload.py").write_text(
        "import os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['TMPDIR'], 'result.txt').write_text('diagnostic output\\n')\n"
        "print('safe diagnostic')\n"
        "print('API_TOKEN=' + ('verylong' + 'credential'), file=sys.stderr)\n",
        encoding="utf-8",
    )
    helper = path / "CODE" / "scripts" / "remote" / "release_protocol.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("release helper in commit\n", encoding="utf-8")
    (helper.parent / "deployment_guard.py").write_text("guard helper in commit\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "release base")
    return path, _git(path, "rev-parse", "HEAD")


def test_release_uses_exact_clean_git_commit_and_marks_dirty_checkout_rejected(
    tmp_path: Path,
) -> None:
    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    bundle = tmp_path / "bundle"

    built = build_release(
        repo,
        commit,
        bundle,
        remote_refs=[],
    )

    assert built["source_git_commit"] == commit
    assert built["remote_backup"]["status"] == "remote_backup_pending"
    (repo / "CODE" / "payload.py").write_text("dirty bytes must not ship\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty"):
        build_release(repo, commit, tmp_path / "dirty-bundle", remote_refs=[])


def test_bundle_is_byte_stable_and_records_exact_remote_ref(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "git@github.com:example/release-test.git")
    first = build_release(repo, commit, tmp_path / "first", remote_refs=[(commit, "refs/heads/release")])
    second = build_release(repo, commit, tmp_path / "second", remote_refs=[(commit, "refs/heads/release")])

    assert first["release_id"] == second["release_id"]
    assert first["archive_sha256"] == second["archive_sha256"]
    assert first["remote_backup"]["status"] == "verified"
    assert first["remote_backup"]["ref"] == "refs/heads/release"


def test_release_artifact_identity_does_not_change_with_local_branch_name(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    first = build_release(repo, commit, tmp_path / "first", remote_refs=[])
    _git(repo, "branch", "-m", "codex/second-name")
    second = build_release(repo, commit, tmp_path / "second", remote_refs=[])

    assert first["release_id"] == second["release_id"]
    assert first["artifact_sha256"] == second["artifact_sha256"]
    assert first["source_git_branch"] != second["source_git_branch"]


def test_untracked_changes_are_rejected_and_ignored_files_never_enter_release(
    tmp_path: Path,
) -> None:
    from CODE.scripts.remote.release_protocol import build_release, verify_release_bundle

    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    (repo / "CODE" / "untracked.py").write_text("must not ship\n", encoding="utf-8")
    with pytest.raises(ValueError, match="dirty"):
        build_release(repo, commit, tmp_path / "untracked", remote_refs=[])
    (repo / "CODE" / "untracked.py").unlink()
    (repo / ".git" / "info" / "exclude").write_text(".env\n", encoding="utf-8")
    (repo / ".env").write_text("REMOTE_TOKEN=not-a-real-token-value\n", encoding="utf-8")

    bundle = tmp_path / "bundle"
    build_release(repo, commit, bundle, remote_refs=[])
    envelope = verify_release_bundle(bundle)
    with tarfile.open(bundle / "release.tar", "r:") as archive:
        paths = {member.name for member in archive.getmembers()}
    assert ".env" not in paths
    assert envelope["source_git_commit"] == commit


def test_release_envelope_tampering_is_rejected(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import build_release, verify_release_bundle

    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    bundle = tmp_path / "bundle"
    build_release(repo, commit, bundle, remote_refs=[])
    envelope_path = bundle / "release.json"
    envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
    envelope["source_git_commit"] = "0" * 40
    envelope_path.write_text(json.dumps(envelope), encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        verify_release_bundle(bundle)


def test_archive_tampering_is_rejected_before_install(tmp_path: Path) -> None:
    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    bundle = tmp_path / "bundle"
    build_release(repo, commit, bundle, remote_refs=[])

    archive = bundle / "release.tar"
    contents = bytearray(archive.read_bytes())
    contents[-1] ^= 1
    archive.write_bytes(contents)

    from CODE.scripts.remote.release_protocol import verify_release_bundle

    with pytest.raises(ValueError, match="archive SHA-256"):
        verify_release_bundle(bundle)


def test_install_is_idempotent_read_only_and_preserves_existing_release(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import install_release, verify_installed_release

    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    bundle = tmp_path / "bundle"
    envelope = build_release(repo, commit, bundle, remote_refs=[])
    releases = tmp_path / "vm" / "releases"

    first = install_release(bundle, releases, bootstrap=repo / "CODE/scripts/remote/release_protocol.py")
    published = releases / envelope["release_id"]
    assert first["status"] == "published"
    assert verify_installed_release(published)["artifact_sha256"] == envelope["artifact_sha256"]
    assert not (published / "CODE/payload.py").stat().st_mode & stat.S_IWUSR

    second = install_release(bundle, releases)
    assert second["status"] == "already_present"
    assert "diagnostic output" in (published / "CODE/payload.py").read_text(encoding="utf-8")


def test_install_refuses_invalid_existing_id_without_overwriting_it(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import install_release

    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    bundle = tmp_path / "bundle"
    envelope = build_release(repo, commit, bundle, remote_refs=[])
    releases = tmp_path / "vm" / "releases"
    target = releases / envelope["release_id"]
    target.mkdir(parents=True)
    marker = target / "preserve.txt"
    marker.write_text("existing bytes\n", encoding="utf-8")

    with pytest.raises(ValueError):
        install_release(bundle, releases)
    assert marker.read_text(encoding="utf-8") == "existing bytes\n"


def test_new_release_coexists_with_old_release_for_rollback(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import build_release, install_release, verify_installed_release

    repo, first_commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    first_bundle = tmp_path / "first-bundle"
    first = build_release(repo, first_commit, first_bundle, remote_refs=[])
    releases = tmp_path / "vm" / "releases"
    install_release(first_bundle, releases)
    first_file = releases / first["release_id"] / "CODE/payload.py"
    first_bytes = first_file.read_bytes()

    (repo / "CODE" / "next.py").write_text("NEXT = True\n", encoding="utf-8")
    _git(repo, "add", "CODE/next.py")
    _git(repo, "commit", "-q", "-m", "next release")
    second_commit = _git(repo, "rev-parse", "HEAD")
    second_bundle = tmp_path / "second-bundle"
    second = build_release(repo, second_commit, second_bundle, remote_refs=[])
    install_release(second_bundle, releases)

    assert first["release_id"] != second["release_id"]
    assert verify_installed_release(releases / first["release_id"])["release_id"] == first["release_id"]
    assert verify_installed_release(releases / second["release_id"])["release_id"] == second["release_id"]
    assert first_file.read_bytes() == first_bytes


def test_cli_install_cannot_target_the_formal_workspace_or_an_arbitrary_release_root(
    tmp_path: Path,
) -> None:
    from CODE.scripts.remote.release_protocol import build_release, main

    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    bundle = tmp_path / "bundle"
    build_release(repo, commit, bundle, remote_refs=[])
    wrong_root = tmp_path / "formal" / "releases"

    result = main(["install", "--bundle", str(bundle), "--releases-root", str(wrong_root)])

    assert result == 2
    assert not wrong_root.exists()


def test_interrupted_release_partial_is_quarantined_before_retry(tmp_path: Path, monkeypatch) -> None:
    from CODE.scripts.remote import release_protocol as protocol

    root = tmp_path / "T1"
    root.mkdir()
    monkeypatch.setattr(protocol, "T1_ROOT", root)
    monkeypatch.setattr(protocol, "RELEASE_ROOT", root / "releases")
    release_id = "a" * 40 + "-" + "b" * 64
    attempt = protocol.prepare_incoming(release_id, t1_root=root)
    (attempt / "release.tar").write_bytes(b"incomplete transfer")

    quarantined = protocol.quarantine_incoming(release_id, t1_root=root)
    next_attempt = protocol.prepare_incoming(release_id, t1_root=root)

    preserved = Path(quarantined["path"])
    assert quarantined["status"] == "partial_preserved_in_quarantine"
    assert (preserved / "release.tar").read_bytes() == b"incomplete transfer"
    assert next_attempt.is_dir()


def test_safe_release_extractor_rejects_path_escape_and_links(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import safe_extract_tar

    archive_path = tmp_path / "unsafe.tar"
    with tarfile.open(archive_path, "w") as archive:
        member = tarfile.TarInfo("../escape.txt")
        member.size = 1
        import io
        archive.addfile(member, io.BytesIO(b"x"))
    destination = tmp_path / "out"
    destination.mkdir()
    with tarfile.open(archive_path, "r:") as archive:
        with pytest.raises(ValueError, match="unsafe archive member"):
            safe_extract_tar(archive, destination)
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.parametrize("name", [
    ".env", ".env.production", "remote.env", "secrets/service.key", "private.pem",
    "model.ckpt", "weights.pth", "weights.safetensors", "raw/map.pdf",
    "raw/table.docx", "raw/slides.pptx", "data/population.tif", "data/population.png",
    "data/measurements.parquet", "data/input.npz",
])
def test_deployment_exclusions_cover_secrets_and_non_source_assets(name: str) -> None:
    from CODE.scripts.remote.release_protocol import t1_excluded

    assert t1_excluded(PurePosixPath(name))


def test_t1_exclusions_do_not_change_formal_deployment_policy() -> None:
    from CODE.scripts.remote.deployment_guard import excluded
    from CODE.scripts.remote.release_protocol import t1_excluded

    for name in ("raw/map.pdf", "CODE/population_map/world.tif", "docs/figure.png"):
        relative = PurePosixPath(name)
        assert not excluded(relative)
        assert t1_excluded(relative)


@pytest.mark.parametrize("name", [
    "remote.env.template", ".env.template", ".env.example", "CODE/scripts/remote/deployment_guard.py",
])
def test_deployment_exclusions_keep_templates_and_source_code(name: str) -> None:
    from CODE.scripts.remote.deployment_guard import excluded

    assert not excluded(PurePosixPath(name))


def test_secret_scan_reports_location_without_disclosing_value() -> None:
    from CODE.scripts.remote.release_protocol import _redact_argv, scan_secret_text

    secret = "ghp_" + "A" * 36
    findings = scan_secret_text({"CODE/settings.py": f"API_TOKEN = '{secret}'\n".encode()})

    assert findings == [{"path": "CODE/settings.py", "line": 1, "rule": "github_token"}]
    assert secret not in repr(findings)
    credential_url = "https://" + "user:" + "private-value" + "@example.invalid"
    redacted = _redact_argv(["--api_key", "sensitive-value-123", credential_url])
    assert "sensitive-value-123" not in repr(redacted)
    assert "private-value" not in repr(redacted)


def test_release_refuses_tracked_secret_paths_and_detected_secret_content(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import build_release

    repo, _commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    env_file = repo / "CODE" / "remote.env"
    env_file.write_text("REMOTE_TOKEN=filled-secret\n", encoding="utf-8")
    _git(repo, "add", "-f", "CODE/remote.env")
    _git(repo, "commit", "-q", "-m", "unsafe tracked env fixture")
    commit = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="tracked credential file") as error:
        build_release(repo, commit, tmp_path / "bundle", remote_refs=[])
    assert "filled-secret" not in str(error.value)

    _git(repo, "rm", "-q", "--cached", "CODE/remote.env")
    env_file.unlink()
    source = repo / "CODE" / "payload.py"
    secret = "ghp_" + "B" * 36
    source.write_text(f"API_TOKEN = '{secret}'\n", encoding="utf-8")
    _git(repo, "add", "CODE/payload.py")
    _git(repo, "commit", "-q", "-m", "unsafe tracked token fixture")
    commit = _git(repo, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="secret scan") as error:
        build_release(repo, commit, tmp_path / "second-bundle", remote_refs=[])
    assert secret not in str(error.value)


def _installed_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    from CODE.scripts.remote.release_protocol import build_release, install_release

    repo, commit = _repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", "https://github.com/example/release-test.git")
    bundle = tmp_path / "bundle"
    envelope = build_release(repo, commit, bundle, remote_refs=[])
    releases_root = tmp_path / "vm" / "releases"
    install_release(bundle, releases_root, bootstrap=repo / "CODE/scripts/remote/release_protocol.py")
    return repo, releases_root, envelope["release_id"]


def test_run_binds_release_inputs_runtime_and_unique_run_id(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import run_release, verify_run_directory

    repo, releases_root, release_id = _installed_fixture(tmp_path)
    runs_root = tmp_path / "vm" / "runs"
    receipt = run_release(
        release_id,
        "diagnostic-001",
        release_root=releases_root,
        runs_root=runs_root,
        mode="diagnostic",
        config="README.md",
        inputs=["fixture=CODE/payload.py"],
        seed=17,
        argv=["python3", "CODE/payload.py"],
    )

    run_dir = runs_root / "diagnostic-001"
    assert receipt["status"] == "completed"
    assert receipt["source_git_commit"] == _git(repo, "rev-parse", "HEAD")
    verified = verify_run_directory(run_dir, expected_run_id="diagnostic-001",
                                    expected_release_id=release_id)
    manifest = json.loads((run_dir / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["random_seed"] == 17
    assert manifest["config"]["sha256"]
    assert manifest["inputs"][0]["name"] == "fixture"
    assert manifest["environment"]["installed_packages_sha256"]
    assert verified["receipt_sha256"] == receipt["receipt_sha256"]
    assert (run_dir / "tmp" / "result.txt").read_text(encoding="utf-8") == "diagnostic output\n"
    assert (run_dir / "stdout.log").read_text(encoding="utf-8") == "safe diagnostic\n"
    stderr_log = (run_dir / "stderr.log").read_text(encoding="utf-8")
    assert "verylongcredential" not in stderr_log
    assert "REDACTED" in stderr_log

    with pytest.raises(ValueError, match="already exists"):
        run_release(release_id, "diagnostic-001", release_root=releases_root,
                    runs_root=runs_root, mode="diagnostic", argv=["python3", "CODE/payload.py"])


def test_run_rejects_formal_mode_and_non_python_command_without_execution(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import run_release

    _, releases_root, release_id = _installed_fixture(tmp_path)
    runs_root = tmp_path / "vm" / "runs"
    with pytest.raises(ValueError, match="formal runs stay"):
        run_release(release_id, "formal-001", release_root=releases_root,
                    runs_root=runs_root, mode="formal", argv=["python3", "-c", "pass"])
    with pytest.raises(ValueError, match="entrypoint"):
        run_release(release_id, "shell-001", release_root=releases_root,
                    runs_root=runs_root, mode="diagnostic", argv=["python3", "-c", "pass"])
    with pytest.raises(ValueError, match="Python entrypoint"):
        run_release(release_id, "outside-001", release_root=releases_root,
                    runs_root=runs_root, mode="diagnostic", argv=["sh", "-c", "touch pwned"])
    assert not (runs_root / "formal-001").exists()
    assert not (runs_root / "shell-001").exists()
    assert not (runs_root / "outside-001").exists()


def test_two_runs_share_read_only_release_without_sharing_outputs(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    from CODE.scripts.remote.release_protocol import run_release, verify_run_directory, verify_installed_release

    _, releases_root, release_id = _installed_fixture(tmp_path)
    runs_root = tmp_path / "vm" / "runs"
    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(
            lambda run_id: run_release(
                release_id, run_id, release_root=releases_root, runs_root=runs_root,
                mode="diagnostic", argv=["python3", "CODE/payload.py"],
            ),
            ("parallel-a", "parallel-b"),
        ))
    assert {item["run_id"] for item in receipts} == {"parallel-a", "parallel-b"}
    for run_id in ("parallel-a", "parallel-b"):
        receipt = verify_run_directory(runs_root / run_id, expected_release_id=release_id)
        assert receipt["status"] == "completed"
        assert (runs_root / run_id / "tmp" / "result.txt").is_file()
    assert verify_installed_release(releases_root / release_id)["release_id"] == release_id


def test_busy_resource_group_fails_without_consuming_run_id(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import run_release

    _, releases_root, release_id = _installed_fixture(tmp_path)
    runs_root = tmp_path / "vm" / "runs"
    lock_dir = runs_root / ".locks"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / "gpu.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already in use"):
            run_release(release_id, "resource-001", release_root=releases_root,
                        runs_root=runs_root, mode="diagnostic", resource_group="gpu",
                        argv=["python3", "CODE/payload.py"])
    assert not (runs_root / "resource-001").exists()


def test_verified_pullback_is_atomic_and_refuses_identity_or_content_mismatch(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import pullback_run, run_release

    _, releases_root, release_id = _installed_fixture(tmp_path)
    runs_root = tmp_path / "vm" / "runs"
    run_release(release_id, "pull-001", release_root=releases_root,
                runs_root=runs_root, mode="diagnostic", argv=["python3", "CODE/payload.py"])
    run_dir = runs_root / "pull-001"
    archive_path = tmp_path / "run.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in sorted(run_dir.rglob("*")):
            archive.add(path, arcname=path.relative_to(run_dir).as_posix(), recursive=False)
    evidence_root = tmp_path / "evidence"

    result = pullback_run(archive_path.open("rb"), evidence_root,
                          expected_run_id="pull-001", expected_release_id=release_id)

    assert result["status"] == "verified"
    assert (evidence_root / "pull-001" / "run-receipt.json").is_file()
    repeated = pullback_run(archive_path.open("rb"), evidence_root,
                            expected_run_id="pull-001", expected_release_id=release_id)
    assert repeated["status"] == "already_verified"
    with pytest.raises(ValueError, match="unexpected run ID"):
        pullback_run(archive_path.open("rb"), tmp_path / "other-evidence",
                     expected_run_id="different-id", expected_release_id=release_id)

    original = (evidence_root / "pull-001" / "run.log").read_bytes()
    candidate = tmp_path / "tampered.tar.gz"
    with tarfile.open(candidate, "w:gz") as archive:
        for path in sorted(run_dir.rglob("*")):
                if path.is_file():
                    data = path.read_bytes()
                    info = tarfile.TarInfo(path.relative_to(run_dir).as_posix())
                    payload = data + b"tampered" if info.name == "run.log" else data
                    info.size = len(payload)
                    archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(ValueError, match="file set or hash mismatch"):
        pullback_run(candidate.open("rb"), tmp_path / "tampered-evidence",
                     expected_run_id="pull-001", expected_release_id=release_id)
    assert (evidence_root / "pull-001" / "run.log").read_bytes() == original


def test_verified_pullback_appends_one_small_idempotent_index_record(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import (
        append_deployment_index,
        run_release,
        verify_run_directory,
    )

    repo, releases_root, release_id = _installed_fixture(tmp_path)
    runs_root = tmp_path / "vm" / "runs"
    run_release(release_id, "index-001", release_root=releases_root,
                runs_root=runs_root, mode="diagnostic", argv=["python3", "CODE/payload.py"])
    run_dir = runs_root / "index-001"
    receipt = verify_run_directory(run_dir)
    evidence_path = tmp_path / "external-evidence" / "index-001"
    evidence_path.parent.mkdir()
    import shutil
    shutil.copytree(run_dir, evidence_path)

    append_deployment_index(repo, receipt, evidence_path)
    append_deployment_index(repo, receipt, evidence_path)

    index = repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl"
    lines = index.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["run_id"] == "index-001"
    assert record["release_id"] == release_id
    assert record["pullback_status"] == "VERIFIED"
    assert record["analysis_commit"] is None


def test_failed_pullback_is_quarantined_before_a_clean_retry(tmp_path: Path) -> None:
    from CODE.scripts.remote.release_protocol import pullback_run, run_release

    _, releases_root, release_id = _installed_fixture(tmp_path)
    runs_root = tmp_path / "vm" / "runs"
    run_release(release_id, "retry-001", release_root=releases_root,
                runs_root=runs_root, mode="diagnostic", argv=["python3", "CODE/payload.py"])
    run_dir = runs_root / "retry-001"
    good_archive = tmp_path / "good.tar.gz"
    with tarfile.open(good_archive, "w:gz") as archive:
        for path in sorted(run_dir.rglob("*")):
            archive.add(path, arcname=path.relative_to(run_dir).as_posix(), recursive=False)
    evidence_root = tmp_path / "evidence"
    with pytest.raises((tarfile.ReadError, EOFError, ValueError)):
        pullback_run(io.BytesIO(b"truncated transfer"), evidence_root,
                     expected_run_id="retry-001", expected_release_id=release_id)
    partial = evidence_root / ".incoming" / "retry-001.partial"
    assert partial.exists()

    result = pullback_run(good_archive.open("rb"), evidence_root,
                          expected_run_id="retry-001", expected_release_id=release_id)

    assert result["status"] == "verified"
    quarantined = list((evidence_root / ".quarantine").glob("retry-001.*.partial"))
    assert len(quarantined) == 1
    assert quarantined[0].is_dir()
