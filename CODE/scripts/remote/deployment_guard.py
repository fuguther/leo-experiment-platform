#!/usr/bin/env python3
"""Build and verify exact, clean deployments for the isolated VM workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import stat
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


CANONICAL_WORKSPACE = Path("/data/论文/leo-direct-sim")
DEPLOYED_DIRS = (
    "ANALYSIS",
    "CODE",
    "EXPERIMENTS",
    "LITERATURE",
    "PAPER",
)
DEPLOYED_FILES = (
    ".deployment_commit",
    ".gitignore",
    "AGENTS.md",
    "DECISIONS.md",
    "LICENSE",
    "NOTES.md",
    "README.md",
)


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def require_lexical_real_directory(path: Path, expected: Path, label: str) -> Path:
    """Require the exact lexical path and reject symlinks in every component."""
    lexical = lexical_absolute(path)
    expected_lexical = lexical_absolute(expected)
    if lexical != expected_lexical:
        raise ValueError(f"{label} must use lexical canonical path {expected_lexical}, got {lexical}")
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise ValueError(f"{label} component is missing: {current}") from exc
        if stat.S_ISLNK(mode):
            raise ValueError(f"{label} component may not be a symbolic link: {current}")
        if not stat.S_ISDIR(mode):
            raise ValueError(f"{label} component is not a directory: {current}")
    return lexical


def excluded(relative: PurePosixPath) -> bool:
    parts = relative.parts
    name = relative.name
    if any(part in {".git", ".cursor", ".venv", ".cache", ".pytest_cache", ".mypy_cache", "__pycache__", "node_modules"} for part in parts):
        return True
    if name in {".DS_Store", "remote.env"} or name.startswith("._"):
        return True
    if name.endswith((".pyc", ".pyo", ".swp", ".tmp")):
        return True
    if parts[:2] == ("CODE", "Results"):
        return True
    if parts[:2] == ("CODE", "config") and name.startswith("_runtime_") and name.endswith(".json"):
        return True
    if len(parts) >= 3 and parts[0] == "EXPERIMENTS" and parts[1].startswith("EXP-"):
        if parts[2] == "raw":
            return True
        if len(parts) >= 5 and parts[2] == "attempts" and parts[4] == "raw":
            return True
    return False


def source_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for top_file in DEPLOYED_FILES:
        if top_file == ".deployment_commit" and not (root / top_file).is_file():
            continue
        candidate = root / top_file
        if candidate.is_file() and not candidate.is_symlink():
            paths.append(candidate)
    for top_dir in DEPLOYED_DIRS:
        directory = root / top_dir
        if not directory.is_dir() or directory.is_symlink():
            raise ValueError(f"required deployment directory missing or unsafe: {directory}")
        for candidate in directory.rglob("*"):
            relative = PurePosixPath(candidate.relative_to(root).as_posix())
            if excluded(relative):
                continue
            if candidate.is_symlink():
                raise ValueError(f"deployment refuses symbolic links: {relative}")
            if candidate.is_file():
                paths.append(candidate)
            elif not candidate.is_dir():
                raise ValueError(f"deployment refuses special file: {relative}")
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def file_records(root: Path, paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in paths
    ]


def tree_sha256(records: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json({"files": records})).hexdigest()


def build_archive(root: Path, archive_path: Path, commit: str, branch: str) -> dict[str, Any]:
    root = root.resolve()
    if not commit or len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit.lower()):
        raise ValueError("source commit must be a non-zero 40-character hexadecimal Git commit")
    if set(commit) == {"0"}:
        raise ValueError("source commit cannot be all zeroes")
    paths = [path for path in source_paths(root) if path.relative_to(root).as_posix() != ".deployment_commit"]
    records = file_records(root, paths)
    witness_bytes = (commit.lower() + "\n").encode("ascii")
    records.append(
        {
            "path": ".deployment_commit",
            "size": len(witness_bytes),
            "sha256": hashlib.sha256(witness_bytes).hexdigest(),
        }
    )
    records.sort(key=lambda record: record["path"])
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "source_git_commit": commit.lower(),
        "source_git_branch": branch,
        "source_git_dirty": False,
        "remote_workspace_dir": str(CANONICAL_WORKSPACE),
        "remote_code_dir": str(CANONICAL_WORKSPACE / "CODE"),
        "remote_results_dir": str(CANONICAL_WORKSPACE / "CODE" / "Results"),
        "deployed_dirs": list(DEPLOYED_DIRS),
        "deployed_files": records,
    }
    manifest["source_tree_sha256"] = tree_sha256(records)
    manifest["manifest_sha256"] = hashlib.sha256(canonical_json(manifest)).hexdigest()

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        for directory in DEPLOYED_DIRS:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        for path in paths:
            archive.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)
        witness = tarfile.TarInfo(".deployment_commit")
        witness.size = len(witness_bytes)
        witness.mode = 0o444
        with tempfile.SpooledTemporaryFile() as handle:
            handle.write(witness_bytes)
            handle.seek(0)
            archive.addfile(witness, handle)
        encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        info = tarfile.TarInfo(".deployment-manifest.json")
        info.size = len(encoded)
        info.mode = 0o600
        with tempfile.SpooledTemporaryFile() as handle:
            handle.write(encoded)
            handle.seek(0)
            archive.addfile(info, handle)
    return manifest


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected = payload.pop("manifest_sha256", "")
    actual = hashlib.sha256(canonical_json(payload)).hexdigest()
    payload["manifest_sha256"] = expected
    if not expected or expected != actual:
        raise ValueError("deployment manifest hash mismatch")
    if payload.get("schema_version") != 2:
        raise ValueError("unsupported deployment manifest schema")
    if payload.get("source_git_dirty") is not False:
        raise ValueError("dirty deployment manifests are forbidden")
    commit = str(payload.get("source_git_commit", "")).lower()
    if len(commit) != 40 or set(commit) == {"0"} or any(ch not in "0123456789abcdef" for ch in commit):
        raise ValueError("invalid source Git commit in deployment manifest")
    expected_paths = {
        "remote_workspace_dir": str(CANONICAL_WORKSPACE),
        "remote_code_dir": str(CANONICAL_WORKSPACE / "CODE"),
        "remote_results_dir": str(CANONICAL_WORKSPACE / "CODE" / "Results"),
    }
    for key, value in expected_paths.items():
        if payload.get(key) != value:
            raise ValueError(f"deployment manifest {key} is not canonical")
    if payload.get("deployed_dirs") != list(DEPLOYED_DIRS):
        raise ValueError("deployment directory ownership set mismatch")
    records = payload.get("deployed_files")
    if not isinstance(records, list) or not records:
        raise ValueError("deployment manifest has no file records")
    if payload.get("source_tree_sha256") != tree_sha256(records):
        raise ValueError("deployment tree hash mismatch")
    commit = str(payload["source_git_commit"]).lower()
    witness_bytes = (commit + "\n").encode("ascii")
    witness_records = [record for record in records if record.get("path") == ".deployment_commit"]
    if len(witness_records) != 1:
        raise ValueError("deployment manifest must contain one commit witness")
    witness_record = witness_records[0]
    if witness_record.get("size") != len(witness_bytes) or witness_record.get("sha256") != hashlib.sha256(witness_bytes).hexdigest():
        raise ValueError("deployment commit witness does not match source_git_commit")
    return payload


def validate_tree(root: Path, manifest: dict[str, Any], *, exact: bool) -> None:
    expected: dict[str, dict[str, Any]] = {}
    for record in manifest["deployed_files"]:
        relative = PurePosixPath(str(record.get("path", "")))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(f"unsafe manifest path: {relative}")
        if relative.parts[0] not in DEPLOYED_DIRS and relative.as_posix() not in DEPLOYED_FILES:
            raise ValueError(f"manifest path is outside owned deployment roots: {relative}")
        if relative.as_posix() in expected:
            raise ValueError(f"duplicate manifest path: {relative}")
        expected[relative.as_posix()] = record

    for relative, record in expected.items():
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"deployed file missing or unsafe: {relative}")
        if candidate.stat().st_size != record.get("size") or sha256_file(candidate) != record.get("sha256"):
            raise ValueError(f"deployed file hash mismatch: {relative}")

    if exact:
        actual = {path.relative_to(root).as_posix() for path in source_paths(root)}
        if actual != set(expected):
            extra = sorted(actual - set(expected))[:5]
            missing = sorted(set(expected) - actual)[:5]
            raise ValueError(f"deployed file set mismatch; extra={extra}, missing={missing}")


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def install(staging: Path, workspace: Path) -> dict[str, Any]:
    staging = staging.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    workspace = require_lexical_real_directory(workspace, CANONICAL_WORKSPACE, "workspace")
    manifest = load_manifest(staging / ".deployment-manifest.json")
    validate_tree(staging, manifest, exact=True)

    runtime = workspace / ".remote_runtime"
    if runtime.is_symlink():
        raise ValueError("canonical runtime directory may not be a symbolic link")
    for name in (*DEPLOYED_DIRS, *DEPLOYED_FILES):
        current = workspace / name
        if current.is_symlink():
            raise ValueError(f"canonical deployment target is a symlink: {current}")
    existing_results = workspace / "CODE" / "Results"
    if existing_results.is_symlink():
        raise ValueError("canonical Results directory may not be a symbolic link")
    receipt_path = runtime / "deployment.json"
    runtime.mkdir(parents=True, exist_ok=True)
    receipt_path.unlink(missing_ok=True)  # fail closed before any canonical source mutation
    backup_root = runtime / "deploy-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix=manifest["source_tree_sha256"] + ".", dir=backup_root))

    for name in (*DEPLOYED_DIRS, *DEPLOYED_FILES):
        current = workspace / name
        if current.exists() or current.is_symlink():
            destination = backup / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(current, destination)

    # Results are persistent data, not deployed source. Preserve them across CODE replacement.
    previous_results = backup / "CODE" / "Results"
    for name in DEPLOYED_DIRS:
        source = staging / name
        destination = workspace / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            os.replace(source, destination)
        else:
            destination.mkdir(parents=True, exist_ok=True)
    for name in DEPLOYED_FILES:
        source = staging / name
        if source.is_file():
            os.replace(source, workspace / name)
    results = workspace / "CODE" / "Results"
    if previous_results.exists():
        os.replace(previous_results, results)
    else:
        results.mkdir(parents=True, exist_ok=True)

    validate_tree(workspace, manifest, exact=True)
    receipt = dict(manifest)
    receipt.pop("manifest_sha256", None)
    receipt.update(
        {
            "deployed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "remote_host": socket.gethostname(),
        }
    )
    receipt["receipt_sha256"] = hashlib.sha256(canonical_json(receipt)).hexdigest()
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shutil.rmtree(backup, ignore_errors=True)
    return receipt


def verify_receipt(receipt_path: Path, workspace: Path = CANONICAL_WORKSPACE) -> dict[str, Any]:
    workspace = require_lexical_real_directory(workspace, CANONICAL_WORKSPACE, "workspace")
    results = workspace / "CODE" / "Results"
    require_lexical_real_directory(results, CANONICAL_WORKSPACE / "CODE" / "Results", "Results")
    receipt_path = lexical_absolute(receipt_path)
    expected_receipt = workspace / ".remote_runtime" / "deployment.json"
    if receipt_path != expected_receipt or not receipt_path.is_file():
        raise ValueError("deployment receipt is missing or outside the canonical runtime directory")
    if receipt_path.is_symlink():
        raise ValueError("deployment receipt may not be a symbolic link")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected = receipt.pop("receipt_sha256", "")
    actual = hashlib.sha256(canonical_json(receipt)).hexdigest()
    receipt["receipt_sha256"] = expected
    if not expected or expected != actual:
        raise ValueError("deployment receipt hash mismatch")
    if receipt.get("source_git_dirty") is not False:
        raise ValueError("formal runs refuse dirty deployments")
    try:
        deployed_at = datetime.fromisoformat(str(receipt["deployed_at"]))
        now = datetime.now().astimezone()
        if deployed_at.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("deployment receipt has no valid timezone-aware deployed_at") from exc
    if deployed_at > now + timedelta(minutes=5) or deployed_at < now - timedelta(days=30):
        raise ValueError("deployment receipt is stale or implausibly future-dated; redeploy the clean commit")
    manifest_like = dict(receipt)
    manifest_like.pop("receipt_sha256", None)
    manifest_like.pop("deployed_at", None)
    manifest_like.pop("remote_host", None)
    manifest_like["manifest_sha256"] = hashlib.sha256(canonical_json(manifest_like)).hexdigest()
    load_candidate = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    try:
        json.dump(manifest_like, load_candidate, ensure_ascii=False)
        load_candidate.close()
        manifest = load_manifest(Path(load_candidate.name))
    finally:
        Path(load_candidate.name).unlink(missing_ok=True)
    validate_tree(workspace, manifest, exact=True)
    witness = workspace / ".deployment_commit"
    if witness.is_symlink() or not witness.is_file():
        raise ValueError("deployed commit witness is missing or unsafe")
    observed_commit = witness.read_text(encoding="ascii")
    if observed_commit != str(receipt["source_git_commit"]).lower() + "\n":
        raise ValueError("deployment receipt commit does not match deployed commit witness")
    return receipt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--root", type=Path, required=True)
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--commit", required=True)
    build.add_argument("--branch", required=True)
    install_cmd = sub.add_parser("install")
    install_cmd.add_argument("--staging", type=Path, required=True)
    install_cmd.add_argument("--workspace", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "build":
        manifest = build_archive(args.root, args.archive, args.commit, args.branch)
        print(json.dumps({"source_tree_sha256": manifest["source_tree_sha256"], "file_count": len(manifest["deployed_files"])}))
    elif args.command == "install":
        receipt = install(args.staging, args.workspace)
        print(json.dumps({"receipt_sha256": receipt["receipt_sha256"], "source_tree_sha256": receipt["source_tree_sha256"]}))
    else:
        receipt = verify_receipt(args.receipt)
        print(json.dumps({"receipt_sha256": receipt["receipt_sha256"], "source_tree_sha256": receipt["source_tree_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
