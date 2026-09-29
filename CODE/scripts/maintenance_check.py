#!/usr/bin/env python3
"""Read-only, scoped repository maintenance checks; this is not a CLEAN verdict."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

# Optional receipt verification imports a project module. Never create bytecode
# caches as a side effect of this read-only command, even without ``python -B``.
sys.dont_write_bytecode = True

_OUTPUT_DIRS = {"results", "out", "leo_sim_out"}
_CACHE_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache",
    ".tox", ".nox", "node_modules",
}
_SECRET_DIRS = {"secrets", ".secrets", "credentials"}
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".secret", ".credentials")
_MODEL_SUFFIXES = (
    ".ckpt", ".pt", ".pth", ".safetensors", ".onnx", ".weights",
    ".model", ".pkl", ".pickle", ".h5", ".hdf5", ".keras", ".keras",
)
_TEMPLATE_NAMES = {".env.template", ".env.example", ".env.sample",
                   "remote.env.template", "remote.env.example", "remote.env.sample"}
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_RELEASE_ID = re.compile(r"^[0-9a-f]{40}-[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_RELEASE_COMMANDS = (
    "publish-release-remote.sh",
    "run-release-remote.sh",
    "pull-release-results-remote.sh",
)


def _git(repo: Path, *args: str) -> bytes:
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        env=env,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def _path_error(path_text: str) -> str | None:
    path = PurePosixPath(path_text.replace("\\", "/"))
    parts = tuple(part.lower() for part in path.parts)
    directories = parts[:-1]
    name = path.name.lower()

    if any(part in _OUTPUT_DIRS for part in directories):
        return "tracked run output path"
    if any(part in _CACHE_DIRS for part in directories):
        return "tracked cache path"
    if name in _TEMPLATE_NAMES:
        return None
    if name in {".env", "remote.env"} or (name.startswith(".env.") or name.startswith("remote.env.")):
        return "tracked real environment file"
    if any(part in _SECRET_DIRS for part in parts) or name.endswith(_SECRET_SUFFIXES):
        return "tracked secret or credential path"
    if name.endswith(_MODEL_SUFFIXES):
        return "tracked model or binary training artifact"
    if name in {"id_rsa", "id_ed25519", "id_ecdsa", "credentials.json", "service-account.json"}:
        return "tracked secret or credential path"
    return None


def _check_tracked_paths(repo: Path, errors: list[str], checked: dict[str, Any]) -> None:
    try:
        paths = [os.fsdecode(item) for item in _git(repo, "ls-files", "-z").split(b"\0") if item]
    except (OSError, RuntimeError) as exc:
        errors.append(f"cannot enumerate tracked paths: {exc}")
        checked["tracked_files"] = None
        return
    checked["tracked_files"] = len(paths)
    for path in paths:
        reason = _path_error(path)
        if reason:
            errors.append(f"{reason}: {path}")


def _check_dirty(repo: Path, infos: list[str], checked: dict[str, Any]) -> None:
    try:
        entries = [item for item in _git(
            repo, "status", "--porcelain=v1", "-z", "--untracked-files=normal"
        ).split(b"\0") if item]
    except (OSError, RuntimeError) as exc:
        infos.append(f"working-tree state unavailable: {exc}")
        checked["dirty_entries"] = None
        return
    checked["dirty_entries"] = len(entries)
    if entries:
        infos.append(f"working tree has {len(entries)} dirty path record(s); preserved as-is")
    else:
        infos.append("working tree reports no dirty paths")


def _check_worktrees(repo: Path, warnings: list[str], checked: dict[str, Any]) -> None:
    try:
        raw = _git(repo, "worktree", "list", "--porcelain", "-z")
    except (OSError, RuntimeError) as exc:
        warnings.append(f"worktree metadata unavailable: {exc}")
        checked["worktrees"] = None
        return
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for field in raw.decode("utf-8", errors="surrogateescape").split("\0"):
        if not field:
            if current:
                records.append(current)
                current = {}
            continue
        key, _, value = field.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "prunable":
            current["prunable"] = value or "prunable"
    if current:
        records.append(current)
    checked["worktrees"] = len(records)
    for record in records:
        path_text = record.get("path")
        if record.get("prunable"):
            warnings.append(f"worktree is marked prunable; inspect before cleanup: {path_text}")
        if path_text and not Path(path_text).exists():
            warnings.append(f"worktree path is missing; inspect before cleanup: {path_text}")


def _read_text(path: Path, label: str, errors: list[str]) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read {label}: {exc}")
        return None


def _check_entry_docs(repo: Path, errors: list[str]) -> None:
    readme = _read_text(repo / "README.md", "README.md", errors)
    if readme is not None and not re.search(
        r"\[[^\]]+\]\((?:\./)?STATUS\.md(?:#[^)]*)?\)", readme, re.IGNORECASE
    ):
        errors.append("README.md must link the repository-root STATUS.md")

    _read_text(repo / "STATUS.md", "STATUS.md", errors)

    agents = _read_text(repo / "AGENTS.md", "root AGENTS.md", errors)
    if agents is not None:
        for command in _REQUIRED_RELEASE_COMMANDS:
            if command not in agents:
                errors.append(f"root AGENTS.md lacks the current release entry {command}")


def _validate_index(index_path: Path, errors: list[str], warnings: list[str],
                    checked: dict[str, Any]) -> list[dict[str, Any]]:
    if not index_path.exists():
        warnings.append("deployment index is absent; no run records were checked")
        checked["deployment_index_records"] = 0
        return []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"cannot read deployment index: {exc}")
        checked["deployment_index_records"] = None
        return []

    records: list[dict[str, Any]] = []
    by_run_id: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            warnings.append(f"deployment index line {line_number} is blank")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"deployment index line {line_number} is invalid JSON: {exc.msg}")
            continue
        if not isinstance(record, dict):
            errors.append(f"deployment index line {line_number} must be a JSON object")
            continue

        valid = True
        run_id = record.get("run_id")
        release_id = record.get("release_id")
        receipt_sha256 = record.get("receipt_sha256")
        status = record.get("pullback_status")
        evidence_uri = record.get("evidence_uri")
        if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
            errors.append(f"deployment index line {line_number} has invalid run_id")
            valid = False
        if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
            errors.append(f"deployment index line {line_number} has invalid release_id")
            valid = False
        if not isinstance(receipt_sha256, str) or not _SHA256.fullmatch(receipt_sha256):
            errors.append(f"deployment index line {line_number} has invalid receipt_sha256")
            valid = False
        if status != "VERIFIED":
            errors.append(f"deployment index line {line_number} pullback_status must be VERIFIED")
            valid = False
        if not isinstance(evidence_uri, str) or not evidence_uri.strip():
            errors.append(f"deployment index line {line_number} has no local evidence_uri")
            valid = False
        if not valid:
            continue

        previous = by_run_id.get(run_id)
        if previous is not None:
            if previous == record:
                warnings.append(f"deployment index has duplicate identical run_id {run_id}")
                continue
            errors.append(f"deployment index run_id {run_id} has different conflicting records")
            continue
        by_run_id[run_id] = record
        records.append(record)
    checked["deployment_index_records"] = len(records)
    return records


def _local_evidence_path(repo: Path, uri: str, run_id: str,
                         errors: list[str]) -> Path | None:
    if "\x00" in uri or "://" in uri or PureWindowsPath(uri).is_absolute() or Path(uri).is_absolute():
        errors.append(f"run {run_id} evidence_uri must be a relative local path")
        return None
    try:
        candidate = (repo / Path(uri)).resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"run {run_id} evidence_uri is invalid: {type(exc).__name__}")
        return None
    if candidate == repo or repo in candidate.parents:
        errors.append(f"run {run_id} evidence_uri points inside the source repository")
        return None
    return candidate


def _check_evidence(repo: Path, records: list[dict[str, Any]], *, verify: bool,
                    errors: list[str], warnings: list[str], checked: dict[str, Any]) -> None:
    verified_count = 0
    for record in records:
        run_id = record["run_id"]
        evidence = _local_evidence_path(repo, record["evidence_uri"], run_id, errors)
        if evidence is None:
            continue
        if not evidence.is_dir() or evidence.is_symlink():
            warnings.append(f"run {run_id} local evidence is missing; external storage was not checked")
            continue
        if not verify:
            continue

        try:
            # Import only the local verifier, with this checkout at the front
            # of sys.path; verify_run_directory reads files and makes no writes.
            trusted_project_root = str(Path(__file__).resolve().parents[2])
            if trusted_project_root not in sys.path:
                sys.path.insert(0, trusted_project_root)
            from CODE.scripts.remote.release_protocol import verify_run_directory

            receipt = verify_run_directory(
                evidence,
                expected_run_id=run_id,
                expected_release_id=record["release_id"],
            )
        except Exception as exc:  # verification failures are report data
            errors.append(f"run {run_id} local receipt verification failed: {exc}")
            continue
        if receipt.get("receipt_sha256") != record["receipt_sha256"]:
            errors.append(f"run {run_id} receipt_sha256 differs from the deployment index")
            continue
        verified_count += 1
    checked["local_receipts_verified"] = verified_count if verify else 0


def run_check(repo: Path, *, verify_evidence: bool = False) -> dict[str, Any]:
    repo = repo.resolve()
    errors: list[str] = []
    warnings: list[str] = [
        "external backup media and dependency lock were not checked; this scoped report does not establish overall cleanliness"
    ]
    infos: list[str] = []
    checked: dict[str, Any] = {}

    if not repo.is_dir():
        errors.append(f"repository path is not a directory: {repo}")
    else:
        try:
            _git(repo, "rev-parse", "--show-toplevel")
        except (OSError, RuntimeError) as exc:
            errors.append(f"not a readable Git repository: {exc}")
        else:
            _check_tracked_paths(repo, errors, checked)
            _check_dirty(repo, infos, checked)
            _check_worktrees(repo, warnings, checked)
            _check_entry_docs(repo, errors)
            records = _validate_index(
                repo / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl", errors, warnings, checked
            )
            _check_evidence(
                repo, records, verify=verify_evidence, errors=errors,
                warnings=warnings, checked=checked,
            )

    return {
        "summary": {
            "checked": "scoped maintenance review completed",
            "errors": len(errors),
            "warnings": len(warnings),
            "info": len(infos),
        },
        "scope": (
            "Local Git path metadata, worktree metadata, root README/AGENTS entry points, "
            "deployment-index JSONL, and optionally indexed local receipts. No file contents "
            "are scanned for secret values; no network, VM, experiment, deletion, move, or repair "
            "is performed. External backup media and dependency locking remain unverified; "
            "this report does not establish overall CLEAN status."
        ),
        "checked": checked,
        "errors": errors,
        "warnings": warnings,
        "info": infos,
    }


def main(argv: list[str] | None = None) -> int:
    default_repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=default_repo,
                        help="repository root (default: this script's repository)")
    parser.add_argument("--verify-evidence", action="store_true",
                        help="read-only verification of existing local receipts referenced by the index")
    args = parser.parse_args(argv)
    report = run_check(args.repo, verify_evidence=args.verify_evidence)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
