#!/usr/bin/env python3
"""Build, verify, and publish immutable T1 releases from exact Git objects.

The existing deployment_guard remains the authority for the owned source roots,
file exclusions, file hashes, and tree hash. This module adds a separate T1
release store and never writes to the canonical formal deployment.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import signal
import tarfile
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable

try:
    from . import deployment_guard as guard
except ImportError:  # executed as a standalone script on the VM
    import deployment_guard as guard


T1_ROOT = Path("/data/论文/leo-t1-wt")
RELEASE_ROOT = T1_ROOT / "releases"
RUN_ROOT = T1_ROOT / "runs"
ENVELOPE_SCHEMA = "leo-immutable-release/v1"
MANIFEST_SCHEMA = "leo-immutable-release-manifest/v1"
RUN_SCHEMA = "leo-release-run/v1"
RUN_RECEIPT_SCHEMA = "leo-release-run-receipt/v1"
T1_REQUIRED_DIRS = ("ANALYSIS", "CODE", "EXPERIMENTS", "lines")
T1_OPTIONAL_DIRS = ("docs",)
T1_DEPLOYED_DIRS = (*T1_REQUIRED_DIRS, *T1_OPTIONAL_DIRS)
T1_DEPLOYED_FILES = (
    ".deployment_commit", ".gitignore", "AGENTS.md", "BACKLOG.md", "CHARTER.md",
    "README.md", "SOURCE-COMMIT.txt", "STATUS.md", "WORKING-MODEL.md",
)
RELEASE_ID_RE = re.compile(r"^[0-9a-f]{40}-[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RESOURCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MANIFEST_NAME = ".release-manifest.json"
ENVELOPE_NAME = ".release-envelope.json"
RUN_MANIFEST_NAME = "run-manifest.json"
RUN_RECEIPT_NAME = "run-receipt.json"
T1_RUNTIME_IDENTITY_RELATIVE = (
    "CODE/dependencies/t1-vm-linux-aarch64/runtime-identity.json")
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b")),
    ("url_credentials", re.compile(r"(?i)://[^/\s:@]+:([^@\s]{8,})@")),
    ("credential_assignment", re.compile(
        r"(?i)\b(?:api[_-]?(?:key|token)|access[_-]?token|auth[_-]?token|"
        r"client[_-]?secret|password|passwd|secret|bearer)\b\s*[:=]\s*"
        r"[\"']?([^\s\"'`,;]{8,})"
    )),
)
_SENSITIVE_PATH_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".secret", ".credentials")
_SENSITIVE_PATH_NAMES = {".env", "remote.env"}
_SENSITIVE_PATH_DIRS = {".secrets", "secrets", "credentials"}
_T1_SOURCE_ONLY_SUFFIXES = (
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".png", ".jpg", ".jpeg",
    ".gif", ".bmp", ".tif", ".tiff", ".zip", ".tar", ".gz", ".npy",
    ".npz", ".parquet", ".ckpt", ".pt", ".pth", ".safetensors", ".pkl",
    ".pickle", ".h5", ".hdf5",
)
# These M-Lab assets are locally registered as having unverified provenance
# and redistribution rights. Development releases must not copy them into the
# VM release store. The synthetic hand-declared profiles used by the T1 work
# package do not depend on these files.
_UNVERIFIED_MLAB_ASSETS = {
    "CODE/data/traffic/mlab_2026-05-27.csv",
    "CODE/data/traffic/mlab_sample.csv",
    "CODE/data/traffic/t1_step5_micro_ab.csv",
    "CODE/data/traffic/diurnal_mlab_2026-05-27.json",
    "CODE/data/geoip/sites.json",
}


def t1_excluded(relative: PurePosixPath) -> bool:
    """Apply T1's data/credential boundary without changing formal deploy rules."""
    lower_name = relative.name.lower()
    lower_parts = {part.lower() for part in relative.parts}
    sensitive_name = (
        lower_name in _SENSITIVE_PATH_NAMES
        or (lower_name.startswith(".env.") and lower_name not in {".env.template", ".env.example"})
        or lower_name.endswith(_SENSITIVE_PATH_SUFFIXES)
        or bool(lower_parts & _SENSITIVE_PATH_DIRS)
    )
    return (relative.as_posix() in _UNVERIFIED_MLAB_ASSETS
            or guard.excluded(relative) or sensitive_name
            or lower_name.endswith(_T1_SOURCE_ONLY_SUFFIXES))


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return guard.sha256_file(path)


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing to replace symbolic path: {path.name}")
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.partial")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    try:
        with temp.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temp.unlink(missing_ok=True)


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise ValueError(f"Git command failed: git {' '.join(args[:2])}")
    return result


def _is_placeholder(value: str) -> bool:
    normalized = value.strip().strip("\"'").strip().lower()
    return (
        normalized in {"changeme", "change-me", "replace_me", "replace-me", "placeholder",
                       "redacted", "your_token_here", "your_secret_here", "example", "none"}
        or normalized.startswith("${")
        or (normalized.startswith("{") and normalized.endswith("}"))
        or (normalized.startswith("<") and normalized.endswith(">"))
    )


def scan_secret_text(files: dict[str, bytes]) -> list[dict[str, Any]]:
    """Return rule locations only; matched credential text is never retained."""
    findings: list[dict[str, Any]] = []
    for path, raw in sorted(files.items()):
        if b"\x00" in raw:
            raise ValueError(f"binary source file is not allowed in a source release: {path}")
        for line_number, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
            for rule, pattern in _SECRET_PATTERNS:
                match = pattern.search(line)
                if not match:
                    continue
                if rule == "credential_assignment" and _is_placeholder(match.group(1)):
                    continue
                if rule == "url_credentials" and _is_placeholder(match.group(1)):
                    continue
                findings.append({"path": path, "line": line_number, "rule": rule})
                break
    return findings


def _tracked_credential_paths(repo: Path, commit: str) -> list[str]:
    result = _git(repo, "ls-tree", "-r", "-z", "--name-only", commit)
    candidates: list[str] = []
    for raw in result.stdout.split("\x00"):
        if not raw:
            continue
        path = PurePosixPath(raw)
        lower_name = path.name.lower()
        lower_parts = {part.lower() for part in path.parts}
        if (
            lower_name in _SENSITIVE_PATH_NAMES
            or (lower_name.startswith(".env.") and lower_name not in {".env.template", ".env.example"})
            or lower_name.endswith(_SENSITIVE_PATH_SUFFIXES)
            or lower_parts & _SENSITIVE_PATH_DIRS
        ):
            candidates.append(path.as_posix())
    return sorted(candidates)


def _scan_source_paths(root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    contents = {path.relative_to(root).as_posix(): path.read_bytes() for path in paths}
    return scan_secret_text(contents)


def _repo_identity(repo: Path) -> str:
    result = _git(repo, "remote", "get-url", "origin", check=False)
    if result.returncode:
        raise ValueError("source repository has no origin remote")
    raw = result.stdout.strip()
    if "@" in raw and "://" in raw:
        from urllib.parse import urlsplit

        parsed = urlsplit(raw)
        if parsed.username or parsed.password:
            raise ValueError("origin URL contains embedded credentials")
    if raw.startswith("git@github.com:"):
        path = raw.removeprefix("git@github.com:")
    else:
        from urllib.parse import urlsplit

        parsed = urlsplit(raw)
        if parsed.hostname != "github.com" or parsed.username or parsed.password:
            raise ValueError("origin must identify a GitHub repository without credentials")
        path = parsed.path.lstrip("/")
    path = path.removesuffix(".git").strip("/")
    parts = path.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ValueError("origin does not identify one GitHub owner/repository")
    return f"{parts[0].lower()}/{parts[1].lower()}"


def _remote_commit_state(repo: Path, commit: str,
                         remote_refs: Iterable[tuple[str, str]] | None) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    refs: list[tuple[str, str]] = []
    if remote_refs is None:
        result = _git(repo, "ls-remote", "--heads", "origin", check=False)
        if result.returncode:
            return {
                "status": "remote_backup_pending",
                "checked_at": checked_at,
                "reason": "live remote refs could not be verified",
            }
        for line in result.stdout.splitlines():
            fields = line.split("\t", 1)
            if len(fields) == 2:
                refs.append((fields[0].lower(), fields[1]))
    else:
        refs = [(sha.lower(), ref) for sha, ref in remote_refs]
    matching = sorted(ref for sha, ref in refs if sha == commit)
    if matching:
        return {
            "status": "verified",
            "checked_at": checked_at,
            "ref": matching[0],
        }
    return {
        "status": "remote_backup_pending",
        "checked_at": checked_at,
        "reason": "no advertised origin branch points to this exact commit",
    }


def _verify_full_commit(repo: Path, commit: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit) or set(commit) == {"0"}:
        raise ValueError("commit must be a non-zero full 40-character SHA")
    resolved = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").stdout.strip().lower()
    if resolved != commit.lower():
        raise ValueError("commit did not resolve to the supplied full SHA")
    return resolved


def _check_gitlinks(repo: Path, commit: str) -> None:
    result = _git(repo, "ls-tree", "-r", "--full-tree", commit)
    if any(line.startswith("160000 commit ") for line in result.stdout.splitlines()):
        raise ValueError("Git submodules are not supported in immutable T1 releases")


def t1_source_paths(root: Path) -> list[Path]:
    """Enumerate only the T1 checkout roots, using deployment_guard exclusions."""
    paths: list[Path] = []
    for name in T1_DEPLOYED_FILES:
        candidate = root / name
        if candidate.is_symlink():
            raise ValueError(f"release refuses symbolic root file: {name}")
        if candidate.is_file():
            paths.append(candidate)
    for name in T1_DEPLOYED_DIRS:
        directory = root / name
        if directory.is_symlink():
            raise ValueError(f"required T1 release directory missing or unsafe: {name}")
        if not directory.exists() and name in T1_OPTIONAL_DIRS:
            continue
        if not directory.is_dir():
            raise ValueError(f"required T1 release directory missing or unsafe: {name}")
        for candidate in sorted(directory.rglob("*")):
            relative = PurePosixPath(candidate.relative_to(root).as_posix())
            if t1_excluded(relative):
                continue
            if candidate.is_symlink():
                raise ValueError(f"release refuses symbolic source path: {relative}")
            if candidate.is_file():
                paths.append(candidate)
            elif not candidate.is_dir():
                raise ValueError(f"release refuses special source path: {relative}")
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def validate_t1_tree(root: Path, records: list[dict[str, Any]], *, exact: bool) -> None:
    expected: dict[str, dict[str, Any]] = {}
    allowed_roots = set(T1_DEPLOYED_DIRS)
    allowed_files = set(T1_DEPLOYED_FILES)
    for record in records:
        relative = PurePosixPath(str(record.get("path", "")))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError(f"unsafe release manifest path: {relative}")
        if relative.parts[0] not in allowed_roots and relative.as_posix() not in allowed_files:
            raise ValueError(f"release manifest path is outside T1 roots: {relative}")
        if relative.as_posix() in expected:
            raise ValueError(f"duplicate release manifest path: {relative}")
        expected[relative.as_posix()] = record
    for relative, record in expected.items():
        candidate = root / relative
        if candidate.is_symlink() or not candidate.is_file():
            raise ValueError(f"release file missing or unsafe: {relative}")
        if candidate.stat().st_size != record.get("size") or sha256_file(candidate) != record.get("sha256"):
            raise ValueError(f"release file hash mismatch: {relative}")
    if exact:
        actual = {path.relative_to(root).as_posix() for path in t1_source_paths(root)}
        if actual != set(expected):
            extra = sorted(actual - set(expected))[:5]
            missing = sorted(set(expected) - actual)[:5]
            raise ValueError(f"release file set mismatch; extra={extra}, missing={missing}")


def _check_clean_checkout(repo: Path, commit: str) -> str:
    if _git(repo, "rev-parse", "--show-toplevel").stdout.strip() != str(repo):
        raise ValueError("source path must be the Git worktree root")
    current = _git(repo, "rev-parse", "HEAD").stdout.strip().lower()
    if current != commit:
        raise ValueError("clean source checkout HEAD must equal the requested release commit")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip():
        raise ValueError("source Git worktree is dirty; release build refused")
    branch = _git(repo, "branch", "--show-current").stdout.strip()
    if not branch:
        raise ValueError("source checkout must be on a named task branch")
    return branch


def _safe_relative(name: str) -> PurePosixPath:
    if "\\" in name or "\x00" in name:
        raise ValueError(f"unsafe archive member path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "." in path.parts:
        raise ValueError(f"unsafe archive member path: {name!r}")
    return path


def safe_extract_tar(archive: tarfile.TarFile, destination: Path) -> set[str]:
    """Extract only regular files/directories, rejecting links and path aliases."""
    seen_files: set[str] = set()
    seen_dirs: set[str] = set()
    for member in archive:
        relative = _safe_relative(member.name.rstrip("/"))
        key = relative.as_posix()
        target = destination.joinpath(*relative.parts)
        if member.isdir():
            if key in seen_files:
                raise ValueError(f"archive member changes file type: {key}")
            seen_dirs.add(key)
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise ValueError(f"unsupported archive member type: {key}")
        if key in seen_files or key in seen_dirs:
            raise ValueError(f"duplicate archive member: {key}")
        if any(parent.as_posix() in seen_files for parent in relative.parents if parent.parts):
            raise ValueError(f"archive member is nested below a file: {key}")
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise ValueError(f"cannot read archive member: {key}")
        count = 0
        with source, target.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
                count += len(chunk)
        if count != member.size:
            raise ValueError(f"archive member size mismatch: {key}")
        os.chmod(target, member.mode & 0o777)
        seen_files.add(key)
    return seen_files


def _lock_identity(records: list[dict[str, Any]]) -> dict[str, Any]:
    lock_names = {
        "requirements.lock", "conda-lock.yml", "environment.lock.yml",
        "poetry.lock", "Pipfile.lock", "conda-linux-aarch64.explicit.lock",
    }
    found = [record for record in records if PurePosixPath(record["path"]).name in lock_names]
    if found:
        return {
            "status": "pinned",
            "files": [{"path": item["path"], "sha256": item["sha256"]} for item in found],
        }
    return {
        "status": "not_pinned",
        "reason": "the source commit contains no recognized dependency lock file; capture the VM runtime in each run receipt",
    }


def _manifest_payload(root: Path, commit: str, repository: str,
                      secret_scan: dict[str, Any]) -> dict[str, Any]:
    witness = root / ".deployment_commit"
    witness.write_text(commit + "\n", encoding="ascii")
    os.chmod(witness, 0o444)
    paths = t1_source_paths(root)
    records = guard.file_records(root, paths)
    records.sort(key=lambda item: item["path"])
    payload: dict[str, Any] = {
        "schema": MANIFEST_SCHEMA,
        "repository": repository,
        "source_git_commit": commit,
        "source_git_dirty": False,
        "deployment_roots": list(T1_DEPLOYED_DIRS),
        "deployed_files": records,
        "dependency_lock": _lock_identity(records),
        "source_audit": secret_scan,
    }
    payload["source_tree_sha256"] = guard.tree_sha256(records)
    payload["manifest_sha256"] = sha256_bytes(canonical_json(payload))
    return payload


def _write_deterministic_archive(root: Path, archive_path: Path, manifest: dict[str, Any],
                                 commit_epoch: int) -> None:
    records = manifest["deployed_files"]
    file_names = {item["path"] for item in records}
    file_names.add(MANIFEST_NAME)
    directories: set[str] = set(T1_DEPLOYED_DIRS)
    for name in file_names:
        parent = PurePosixPath(name).parent
        while parent.parts:
            directories.add(parent.as_posix())
            parent = parent.parent
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as archive:
        for name in sorted(directories, key=lambda item: (item.count("/"), item)):
            info = tarfile.TarInfo(name + "/")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = commit_epoch
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info)
        for name in sorted(file_names):
            data_path = root / name
            info = tarfile.TarInfo(name)
            info.size = len(canonical_json(manifest) + b"\n") if name == MANIFEST_NAME else data_path.stat().st_size
            info.mode = 0o444 if name == MANIFEST_NAME or name == ".deployment_commit" else (data_path.stat().st_mode & 0o777) & ~0o222
            info.mtime = commit_epoch
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if name == MANIFEST_NAME:
                import io

                archive.addfile(info, io.BytesIO(canonical_json(manifest) + b"\n"))
            else:
                with data_path.open("rb") as handle:
                    archive.addfile(info, handle)


def _envelope_payload(manifest: dict[str, Any], archive_path: Path, branch: str,
                      remote_state: dict[str, Any]) -> dict[str, Any]:
    commit = manifest["source_git_commit"]
    source_tree = manifest["source_tree_sha256"]
    release_id = f"{commit}-{source_tree}"
    artifact = {
        "schema": ENVELOPE_SCHEMA,
        "release_id": release_id,
        "repository": manifest["repository"],
        "source_git_commit": commit,
        "source_git_branch": branch,
        "source_tree_sha256": source_tree,
        "archive_sha256": sha256_file(archive_path),
        "manifest_sha256": manifest["manifest_sha256"],
        "dependency_lock": manifest["dependency_lock"],
    }
    artifact_identity = {key: value for key, value in artifact.items()
                         if key != "source_git_branch"}
    artifact["artifact_sha256"] = sha256_bytes(canonical_json(artifact_identity))
    payload = {**artifact, "remote_backup": remote_state}
    payload["envelope_sha256"] = sha256_bytes(canonical_json(payload))
    return payload


def build_release(repo: Path, commit: str, bundle_dir: Path, *,
                  remote_refs: Iterable[tuple[str, str]] | None = None) -> dict[str, Any]:
    repo = Path(os.path.abspath(repo))
    bundle_dir = Path(os.path.abspath(bundle_dir))
    commit = _verify_full_commit(repo, commit)
    branch = _check_clean_checkout(repo, commit)
    repository = _repo_identity(repo)
    _check_gitlinks(repo, commit)
    tracked_credentials = _tracked_credential_paths(repo, commit)
    if tracked_credentials:
        raise ValueError(f"tracked credential file paths must be removed from Git before release: {tracked_credentials[:8]}")
    if bundle_dir.exists() or bundle_dir.is_symlink():
        if bundle_dir.is_symlink() or not bundle_dir.is_dir() or any(bundle_dir.iterdir()):
            raise ValueError("bundle output must be a new or empty directory")
    else:
        bundle_dir.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix="leo-release-source.") as raw_source:
        source = Path(raw_source)
        top_level = set(_git(repo, "ls-tree", "--name-only", commit).stdout.splitlines())
        archive_paths = [name for name in (*T1_DEPLOYED_DIRS, *T1_DEPLOYED_FILES)
                         if name in top_level and name != ".deployment_commit"]
        result = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", commit,
             *archive_paths],
            capture_output=True,
        )
        if result.returncode:
            raise ValueError("git archive could not materialize the exact source commit")
        with tarfile.open(fileobj=__import__("io").BytesIO(result.stdout), mode="r:") as archive:
            safe_extract_tar(archive, source)
        for name in T1_DEPLOYED_DIRS:
            directory = source / name
            if directory.is_symlink():
                raise ValueError(f"required release root missing or unsafe: {name}")
            if not directory.exists() and name in T1_OPTIONAL_DIRS:
                continue
            if not directory.is_dir():
                raise ValueError(f"required release root missing or unsafe: {name}")
        commit_epoch = int(_git(repo, "show", "-s", "--format=%ct", commit).stdout.strip())
        source_paths = t1_source_paths(source)
        findings = _scan_source_paths(source, source_paths)
        if findings:
            locations = [f"{item['path']}:{item['line']}:{item['rule']}" for item in findings[:20]]
            raise ValueError(f"release secret scan found credential-like content: {locations}")
        secret_scan = {
            "status": "passed",
            "scanner": "release_protocol/v1",
            "text_files_scanned": len(source_paths),
            "binary_assets": "excluded or rejected",
            "credential_values_retained": False,
        }
        manifest = _manifest_payload(source, commit, repository, secret_scan)
        manifest_path = source / MANIFEST_NAME
        manifest_path.write_bytes(canonical_json(manifest) + b"\n")
        archive_path = bundle_dir / "release.tar"
        _write_deterministic_archive(source, archive_path, manifest, commit_epoch)
        remote_state = _remote_commit_state(repo, commit, remote_refs)
        envelope = _envelope_payload(manifest, archive_path, branch, remote_state)
        _write_json_atomic(bundle_dir / "release.json", envelope)
    verified = verify_release_bundle(bundle_dir)
    return verified


def _load_hashed_json(path: Path, hash_field: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"required JSON file missing or unsafe: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file: {path.name}") from exc
    expected = payload.pop(hash_field, "")
    actual = sha256_bytes(canonical_json(payload))
    payload[hash_field] = expected
    if not expected or expected != actual:
        raise ValueError(f"{path.name} hash mismatch")
    return payload


def _verify_release_tree(root: Path, manifest: dict[str, Any], *,
                         allow_envelope: bool = False) -> None:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("unsupported immutable release manifest schema")
    if manifest.get("source_git_dirty") is not False:
        raise ValueError("dirty source release is forbidden")
    if manifest.get("deployment_roots") != list(T1_DEPLOYED_DIRS):
        raise ValueError("release does not declare the exact T1 source roots")
    commit = str(manifest.get("source_git_commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit) or set(commit) == {"0"}:
        raise ValueError("invalid release commit witness")
    records = manifest.get("deployed_files")
    if not isinstance(records, list) or not records:
        raise ValueError("release manifest has no deployed file records")
    if manifest.get("source_tree_sha256") != guard.tree_sha256(records):
        raise ValueError("release source tree hash mismatch")
    witness = [item for item in records if item.get("path") == ".deployment_commit"]
    if len(witness) != 1:
        raise ValueError("release manifest must include exactly one Git commit witness")
    witness_path = root / ".deployment_commit"
    if witness_path.is_symlink() or not witness_path.is_file() or witness_path.read_text(encoding="ascii") != commit + "\n":
        raise ValueError("release Git commit witness does not match the manifest")
    if witness[0].get("sha256") != sha256_file(witness_path):
        raise ValueError("release Git commit witness hash mismatch")
    validate_t1_tree(root, records, exact=True)
    expected_files = {str(item["path"]) for item in records} | {MANIFEST_NAME}
    if allow_envelope:
        expected_files.add(ENVELOPE_NAME)
    expected_dirs = set(T1_DEPLOYED_DIRS)
    for name in expected_files:
        parent = PurePosixPath(name).parent
        while parent.parts:
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    actual_files: set[str] = set()
    actual_dirs: set[str] = set()
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise ValueError(f"release contains a symbolic link: {candidate.relative_to(root)}")
        if candidate.is_file():
            actual_files.add(candidate.relative_to(root).as_posix())
        elif candidate.is_dir():
            actual_dirs.add(candidate.relative_to(root).as_posix())
        else:
            raise ValueError(f"release contains a special file: {candidate.relative_to(root)}")
    if actual_files != expected_files:
        extra = sorted(actual_files - expected_files)[:5]
        missing = sorted(expected_files - actual_files)[:5]
        raise ValueError(f"release contains missing or unlisted files; extra={extra}, missing={missing}")
    if actual_dirs != expected_dirs:
        extra = sorted(actual_dirs - expected_dirs)[:5]
        missing = sorted(expected_dirs - actual_dirs)[:5]
        raise ValueError(f"release contains missing or unlisted directories; extra={extra}, missing={missing}")


def verify_release_bundle(bundle_dir: Path) -> dict[str, Any]:
    bundle_dir = Path(os.path.abspath(bundle_dir))
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise ValueError("release bundle must be a real directory")
    envelope = _load_hashed_json(bundle_dir / "release.json", "envelope_sha256")
    archive_path = bundle_dir / "release.tar"
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ValueError("release archive is missing or unsafe")
    if sha256_file(archive_path) != envelope.get("archive_sha256"):
        raise ValueError("release archive SHA-256 mismatch")
    if envelope.get("schema") != ENVELOPE_SCHEMA:
        raise ValueError("unsupported release envelope schema")
    if not RELEASE_ID_RE.fullmatch(str(envelope.get("release_id", ""))):
        raise ValueError("invalid release ID")
    with tempfile.TemporaryDirectory(prefix="leo-release-verify.") as raw:
        root = Path(raw)
        with tarfile.open(archive_path, "r:") as archive:
            safe_extract_tar(archive, root)
        manifest = _load_hashed_json(root / MANIFEST_NAME, "manifest_sha256")
        _verify_release_tree(root, manifest)
    commit = manifest["source_git_commit"]
    tree_hash = manifest["source_tree_sha256"]
    if envelope["release_id"] != f"{commit}-{tree_hash}":
        raise ValueError("release ID does not match commit and source tree")
    if envelope.get("source_git_commit") != commit or envelope.get("source_tree_sha256") != tree_hash:
        raise ValueError("release envelope does not match source manifest")
    if envelope.get("manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("release envelope manifest hash mismatch")
    if envelope.get("dependency_lock") != manifest.get("dependency_lock"):
        raise ValueError("release envelope dependency-lock identity mismatch")
    artifact = {key: envelope[key] for key in (
        "schema", "release_id", "repository", "source_git_commit",
        "source_tree_sha256", "archive_sha256", "manifest_sha256", "dependency_lock",
    )}
    expected_artifact = envelope.get("artifact_sha256")
    actual_artifact = sha256_bytes(canonical_json(artifact))
    if expected_artifact != actual_artifact:
        raise ValueError("release artifact identity hash mismatch")
    return envelope


def _require_real_directory(path: Path, *, create: bool = False) -> Path:
    lexical = Path(os.path.abspath(path))
    current = Path(lexical.anchor)
    for part in lexical.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError(f"directory path may not contain symbolic links: {current}")
        if current.exists() and not current.is_dir():
            raise ValueError(f"directory path component is not a directory: {current}")
        if not current.exists():
            if not create:
                raise ValueError(f"directory path component is missing: {current}")
            try:
                current.mkdir()
            except FileExistsError:
                if current.is_symlink() or not current.is_dir():
                    raise ValueError(f"directory path component was concurrently created unsafely: {current}")
    return lexical


def _open_real_directory_fd(path: Path) -> int:
    """Open every path component without following symlinks for safe cleanup."""
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        raise RuntimeError("this platform cannot safely remove a directory tree")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if not nofollow or not directory or os.open not in os.supports_dir_fd:
        raise RuntimeError("this platform lacks no-follow directory-descriptor support")
    lexical = Path(os.path.abspath(path))
    flags = os.O_RDONLY | directory | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(lexical.anchor, flags)
    try:
        for part in lexical.parts[1:]:
            child = os.open(part, flags | nofollow, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except OSError as exc:
        os.close(fd)
        raise ValueError(f"directory path contains a missing or symbolic component: {lexical}") from exc


def _checked_directory_entry(parent_fd: int, name: str, label: str, *, missing_ok: bool = False) -> bool:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return False
        raise ValueError(f"{label} is missing")
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"{label} is not a real directory")
    return True


def _open_child_directory_fd(parent_fd: int, name: str, *, create: bool = False,
                              missing_ok: bool = False) -> int | None:
    """Open one directory below an already anchored parent without following links."""
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise ValueError("directory child name is unsafe")
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError(f"directory child is missing: {name}")
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"directory child is not a real directory: {name}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ValueError(f"directory child changed or is unsafe: {name}") from exc


def _remove_tree_at(parent_fd: int, name: str, label: str) -> None:
    _checked_directory_entry(parent_fd, name, label)
    # dir_fd keeps the parent anchored even if its pathname is concurrently
    # replaced. shutil's fd-based implementation also refuses a swapped link.
    shutil.rmtree(name, dir_fd=parent_fd)


def _readonly_tree(root: Path, *, keep_root_writable: bool = False) -> None:
    for candidate in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if candidate.is_symlink():
            raise ValueError(f"refusing to publish symbolic path: {candidate.relative_to(root)}")
        mode = candidate.stat().st_mode
        if candidate.is_dir():
            os.chmod(candidate, mode & 0o555)
        elif candidate.is_file():
            os.chmod(candidate, mode & 0o555)
        else:
            raise ValueError(f"refusing special release path: {candidate.relative_to(root)}")
    root_mode = root.stat().st_mode & 0o777
    os.chmod(root, root_mode & 0o755 if keep_root_writable else root_mode & 0o555)


def verify_installed_release(release_dir: Path) -> dict[str, Any]:
    release_dir = _require_real_directory(release_dir, create=False)
    envelope = _load_hashed_json(release_dir / ENVELOPE_NAME, "envelope_sha256")
    manifest = _load_hashed_json(release_dir / MANIFEST_NAME, "manifest_sha256")
    if release_dir.name != envelope.get("release_id"):
        raise ValueError("installed release directory identity mismatch")
    if sha256_file(release_dir / ".deployment_commit") != next(
        item["sha256"] for item in manifest["deployed_files"] if item.get("path") == ".deployment_commit"
    ):
        raise ValueError("installed release commit witness hash mismatch")
    _verify_release_tree(release_dir, manifest, allow_envelope=True)
    if envelope.get("release_id") != f"{manifest['source_git_commit']}-{manifest['source_tree_sha256']}":
        raise ValueError("installed release identity mismatch")
    for candidate in [release_dir, *release_dir.rglob("*")]:
        if candidate.stat().st_mode & 0o222:
            raise ValueError("installed release is writable")
    return envelope


def install_release(bundle_dir: Path, releases_root: Path, *, bootstrap: Path | None = None) -> dict[str, Any]:
    envelope = verify_release_bundle(bundle_dir)
    bundle_dir = Path(os.path.abspath(bundle_dir))
    releases_root = _require_real_directory(releases_root, create=True)
    if releases_root.name != "releases":
        raise ValueError("release store must be the T1 releases directory")
    release_id = envelope["release_id"]
    final = releases_root / release_id
    if final.is_symlink():
        raise ValueError("release target may not be a symbolic link")
    if final.exists():
        existing = verify_installed_release(final)
        if existing.get("artifact_sha256") != envelope.get("artifact_sha256"):
            raise ValueError("release ID collision: existing content differs")
        return {"status": "already_present", "release_id": release_id,
                "artifact_sha256": envelope["artifact_sha256"]}
    with tempfile.TemporaryDirectory(prefix="leo-release-stage.", dir=releases_root) as raw:
        stage = Path(raw) / release_id
        stage.mkdir()
        archive_path = bundle_dir / "release.tar"
        with tarfile.open(archive_path, "r:") as archive:
            safe_extract_tar(archive, stage)
        manifest = _load_hashed_json(stage / MANIFEST_NAME, "manifest_sha256")
        _verify_release_tree(stage, manifest)
        protocol_record = next((item for item in manifest["deployed_files"]
                                if item.get("path") == "CODE/scripts/remote/release_protocol.py"), None)
        if protocol_record is None:
            raise ValueError("release does not contain its release protocol verifier")
        if bootstrap is not None:
            if bootstrap.is_symlink() or not bootstrap.is_file() or sha256_file(bootstrap) != protocol_record["sha256"]:
                raise ValueError("uploaded release bootstrap does not match the commit-bound file manifest")
            guard_record = next((item for item in manifest["deployed_files"]
                                 if item.get("path") == "CODE/scripts/remote/deployment_guard.py"), None)
            guard_bootstrap = bootstrap.with_name("deployment_guard.py")
            if (guard_record is None or guard_bootstrap.is_symlink() or not guard_bootstrap.is_file()
                    or sha256_file(guard_bootstrap) != guard_record["sha256"]):
                raise ValueError("uploaded deployment guard does not match the commit-bound file manifest")
        _write_json_atomic(stage / ENVELOPE_NAME, envelope)
        _verify_release_tree(stage, manifest, allow_envelope=True)
        _readonly_tree(stage, keep_root_writable=True)
        try:
            os.replace(stage, final)
            os.chmod(final, final.stat().st_mode & 0o555)
        except OSError:
            if final.exists() and not final.is_symlink():
                existing = verify_installed_release(final)
                if existing.get("artifact_sha256") == envelope.get("artifact_sha256"):
                    return {"status": "already_present", "release_id": release_id,
                            "artifact_sha256": envelope["artifact_sha256"]}
            raise
    return {"status": "published", "release_id": release_id,
            "artifact_sha256": envelope["artifact_sha256"]}


def prepare_incoming(release_id: str, *, t1_root: Path = T1_ROOT) -> Path:
    release_id = _safe_release_id(release_id)
    if Path(os.path.abspath(t1_root)) != T1_ROOT:
        raise ValueError("incoming staging is restricted to /data/论文/leo-t1-wt")
    _require_real_directory(T1_ROOT, create=False)
    releases = _require_real_directory(RELEASE_ROOT, create=True)
    incoming_root = _require_real_directory(releases / "incoming", create=True)
    attempt = incoming_root / f"{release_id}.partial"
    if attempt.is_symlink() or attempt.exists():
        raise ValueError("release incoming attempt already exists; preserve it for inspection")
    attempt.mkdir(mode=0o700)
    return attempt


def cleanup_incoming(release_id: str, *, t1_root: Path = T1_ROOT) -> dict[str, str]:
    release_id = _safe_release_id(release_id)
    if Path(os.path.abspath(t1_root)) != T1_ROOT:
        raise ValueError("incoming cleanup is restricted to /data/论文/leo-t1-wt")
    verify_installed_release(RELEASE_ROOT / release_id)
    incoming_path = RELEASE_ROOT / "incoming"
    bootstrap_path = RELEASE_ROOT / ".bootstrap"
    incoming_fd = _open_real_directory_fd(incoming_path)
    bootstrap_fd: int | None = None
    try:
        # Validate both parent roots and both targets before deleting either.
        # In particular, a symlinked .bootstrap parent must not redirect rmtree.
        bootstrap_path_exists = bootstrap_path.exists() or bootstrap_path.is_symlink()
        if bootstrap_path_exists:
            bootstrap_fd = _open_real_directory_fd(bootstrap_path)
        attempt_name = f"{release_id}.partial"
        bootstrap_name = release_id
        _checked_directory_entry(incoming_fd, attempt_name, "incoming attempt")
        bootstrap_exists = (
            _checked_directory_entry(bootstrap_fd, bootstrap_name, "bootstrap attempt", missing_ok=True)
            if bootstrap_fd is not None else False
        )
        attempt = incoming_path / attempt_name
        bootstrap = bootstrap_path / bootstrap_name
        if any(path.is_symlink() for path in attempt.rglob("*")):
            raise ValueError("incoming attempt contains a symbolic path; preserve it for inspection")
        if bootstrap_exists and any(path.is_symlink() for path in bootstrap.rglob("*")):
            raise ValueError("bootstrap attempt contains a symbolic path; preserve it for inspection")
        _remove_tree_at(incoming_fd, attempt_name, "incoming attempt")
        if bootstrap_exists and bootstrap_fd is not None:
            _remove_tree_at(bootstrap_fd, bootstrap_name, "bootstrap attempt")
    finally:
        os.close(incoming_fd)
        if bootstrap_fd is not None:
            os.close(bootstrap_fd)
    return {"status": "cleaned_after_verified_publish", "release_id": release_id}


def quarantine_incoming(release_id: str, *, t1_root: Path = T1_ROOT) -> dict[str, str]:
    """Preserve abandoned incoming and bootstrap paths before a same-ID retry."""
    release_id = _safe_release_id(release_id)
    if Path(os.path.abspath(t1_root)) != T1_ROOT:
        raise ValueError("incoming quarantine is restricted to /data/论文/leo-t1-wt")
    releases_fd = _open_real_directory_fd(RELEASE_ROOT)
    incoming_fd: int | None = None
    bootstrap_fd: int | None = None
    quarantine_fd: int | None = None
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    nonce = uuid.uuid4().hex
    incoming_root = RELEASE_ROOT / "incoming"
    bootstrap_root = RELEASE_ROOT / ".bootstrap"
    quarantine_root = RELEASE_ROOT / ".quarantine"
    result: dict[str, str] = {"status": "partial_preserved_in_quarantine"}
    attempt_name = f"{release_id}.partial"
    bootstrap_name = release_id
    incoming_target_name = f"{release_id}.{stamp}.{nonce}.partial"
    bootstrap_target_name = f"{release_id}.{stamp}.{nonce}.bootstrap"
    attempt_exists = False
    bootstrap_exists = False
    try:
        incoming_fd = _open_child_directory_fd(releases_fd, "incoming", missing_ok=True)
        bootstrap_fd = _open_child_directory_fd(releases_fd, ".bootstrap", missing_ok=True)
        attempt_exists = (
            _checked_directory_entry(incoming_fd, attempt_name, "incoming attempt", missing_ok=True)
            if incoming_fd is not None else False
        )
        bootstrap_exists = (
            _checked_directory_entry(bootstrap_fd, bootstrap_name, "bootstrap attempt", missing_ok=True)
            if bootstrap_fd is not None else False
        )
        if not attempt_exists and not bootstrap_exists:
            raise ValueError("release incoming and bootstrap attempts are both missing")

        quarantine_fd = _open_child_directory_fd(releases_fd, ".quarantine", create=True)
        assert quarantine_fd is not None

        if attempt_exists and incoming_fd is not None:
            _checked_directory_entry(incoming_fd, attempt_name, "incoming attempt")
            if _checked_directory_entry(quarantine_fd, incoming_target_name,
                                        "incoming quarantine target", missing_ok=True):
                raise ValueError("incoming quarantine target already exists")
            os.replace(attempt_name, incoming_target_name,
                       src_dir_fd=incoming_fd, dst_dir_fd=quarantine_fd)
            result["path"] = str(quarantine_root / incoming_target_name)
        if bootstrap_exists and bootstrap_fd is not None:
            _checked_directory_entry(bootstrap_fd, bootstrap_name, "bootstrap attempt")
            if _checked_directory_entry(quarantine_fd, bootstrap_target_name,
                                        "bootstrap quarantine target", missing_ok=True):
                raise ValueError("bootstrap quarantine target already exists")
            try:
                os.replace(bootstrap_name, bootstrap_target_name,
                           src_dir_fd=bootstrap_fd, dst_dir_fd=quarantine_fd)
            except OSError as exc:
                if "path" in result:
                    raise RuntimeError(
                        "could not preserve bootstrap attempt; incoming was preserved at "
                        f"{result['path']}; bootstrap remains at {bootstrap_root / bootstrap_name}"
                    ) from exc
                raise
            result["bootstrap_path"] = str(quarantine_root / bootstrap_target_name)
            result.setdefault("path", str(quarantine_root / bootstrap_target_name))
        return result
    finally:
        for fd in (quarantine_fd, bootstrap_fd, incoming_fd, releases_fd):
            if fd is not None:
                os.close(fd)


def _safe_release_id(value: str) -> str:
    if not RELEASE_ID_RE.fullmatch(value):
        raise ValueError("invalid release ID")
    return value


def _safe_run_id(value: str) -> str:
    if not RUN_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError("run ID must contain only letters, digits, dot, underscore, or dash")
    return value


def _redact_argv(argv: list[str]) -> list[str]:
    secret_flags = {
        "--token", "--refresh-token", "--secret", "--password", "--passwd",
        "--passphrase", "--api-key", "--access-key", "--secret-key",
        "--private-key", "--authorization", "--client-secret", "--bearer",
    }
    result: list[str] = []
    redact_next = False
    for item in argv:
        if redact_next:
            result.append("[REDACTED]")
            redact_next = False
            continue
        lower = item.lower().replace("_", "-")
        if lower in secret_flags:
            result.append(item)
            redact_next = True
        elif any(lower.startswith(flag + "=") for flag in secret_flags):
            result.append(item.split("=", 1)[0] + "=[REDACTED]")
        elif scan_secret_text({"argv": item.encode("utf-8", errors="replace")}):
            result.append("[REDACTED secret-like argument]")
        else:
            result.append(item)
    return result


def _runtime_identity() -> dict[str, Any]:
    try:
        import importlib.metadata as metadata

        packages = sorted(
            f"{item.metadata['Name']}=={item.version}"
            for item in metadata.distributions()
            if item.metadata.get("Name")
        )
    except Exception:
        packages = []
    encoded = ("\n".join(packages) + ("\n" if packages else "")).encode("utf-8")
    return {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": sys.platform,
        "machine": platform.machine().lower(),
        "hostname": socket.gethostname(),
        "cpu_count": os.cpu_count(),
        "installed_packages_sha256": sha256_bytes(encoded),
        "installed_packages": packages,
    }


def _verify_runtime_contract(
    release_dir: Path,
    contract_relative: str,
    runtime_identity: dict[str, Any],
) -> dict[str, str]:
    """Fail closed when a T1 run uses an environment outside the committed lock."""
    if contract_relative != T1_RUNTIME_IDENTITY_RELATIVE:
        raise ValueError("runtime identity contract path is not the canonical T1 lock")
    contract_path = release_dir / T1_RUNTIME_IDENTITY_RELATIVE
    if contract_path.is_symlink() or not contract_path.is_file():
        raise ValueError("runtime identity contract is missing or symbolic")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    required_keys = {
        "schema", "python", "platform", "machine",
        "installed_packages_sha256", "dependency_lock_sha256s",
    }
    if not isinstance(contract, dict) or set(contract) != required_keys \
            or contract.get("schema") != "leo-t1-runtime-identity/v1":
        raise ValueError("unsupported T1 runtime identity contract")
    lock_paths = {
        "CODE/dependencies/t1-vm-linux-aarch64/requirements.lock",
        "CODE/dependencies/t1-vm-linux-aarch64/conda-linux-aarch64.explicit.lock",
    }
    recorded_locks = contract.get("dependency_lock_sha256s")
    if not isinstance(recorded_locks, dict) or set(recorded_locks) != lock_paths:
        raise ValueError("T1 runtime identity contract has incomplete dependency locks")
    for raw in sorted(lock_paths):
        digest = recorded_locks[raw]
        lock_path = release_dir / raw
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest) \
                or lock_path.is_symlink() or not lock_path.is_file() \
                or sha256_file(lock_path) != digest:
            raise ValueError(f"runtime dependency lock mismatch: {raw}")
    for key in ("python", "platform", "machine", "installed_packages_sha256"):
        expected = contract.get(key)
        if not isinstance(expected, str) or runtime_identity.get(key) != expected:
            raise ValueError(f"runtime identity mismatch: {key}")
    return {
        "path": T1_RUNTIME_IDENTITY_RELATIVE,
        "sha256": sha256_file(contract_path),
    }


def _release_python_argv(argv: list[str], release_dir: Path) -> list[str]:
    """Allow only a Python file/module committed in the immutable release."""
    if not argv or Path(argv[0]).name not in {"python", "python3"} or len(argv) < 2:
        raise ValueError("runner requires a Python entrypoint from the immutable release")
    if argv[1] == "-m":
        if len(argv) < 3 or not re.fullmatch(r"CODE(?:\.[A-Za-z_][A-Za-z0-9_]*)+", argv[2]):
            raise ValueError("Python module entrypoints must be inside the committed CODE package")
        module_path = PurePosixPath(*argv[2].split("."))
        candidates = (release_dir / module_path.with_suffix(".py"),
                      release_dir.joinpath(*module_path.parts, "__init__.py"))
        if not any(path.is_file() and not path.is_symlink() for path in candidates):
            raise ValueError("Python module entrypoint is missing from the immutable release")
    else:
        relative = _safe_relative(argv[1])
        if relative.parts[0] != "CODE":
            raise ValueError("Python script entrypoints must be inside the committed CODE tree")
        script = release_dir.joinpath(*relative.parts)
        if script.is_symlink() or not script.is_file():
            raise ValueError("Python script entrypoint is missing or unsafe in the immutable release")
    return [sys.executable, *argv[1:]]


def _resolve_input_path(name: str, raw_path: str, release_dir: Path) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
        raise ValueError("input identity must be a short safe name")
    path = Path(raw_path)
    if not path.is_absolute():
        relative = _safe_relative(raw_path)
        path = release_dir.joinpath(*relative.parts)
    path = Path(os.path.abspath(path))
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"input {name} is missing, non-regular, or symbolic")
    return path


def _hash_input(name: str, raw_path: str, release_dir: Path) -> dict[str, Any]:
    path = _resolve_input_path(name, raw_path, release_dir)
    return {"name": name, "size": path.stat().st_size, "sha256": sha256_file(path)}


def _snapshot_input(name: str, raw_path: str, release_dir: Path,
                    run_dir: Path) -> tuple[dict[str, Any], Path]:
    source = _resolve_input_path(name, raw_path, release_dir)
    input_root = run_dir / "inputs"
    input_root.mkdir(mode=0o700, exist_ok=True)
    relative = Path("inputs") / name
    snapshot = run_dir / relative
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"input {name} is not a regular file")
        with os.fdopen(os.dup(source_fd), "rb") as input_handle, snapshot.open("xb") as output_handle:
            while chunk := input_handle.read(1024 * 1024):
                output_handle.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output_handle.flush()
            os.fsync(output_handle.fileno())
        after = os.fstat(source_fd)
    finally:
        os.close(source_fd)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if size != before.st_size or any(getattr(before, field) != getattr(after, field)
                                     for field in stable_fields):
        raise ValueError(f"input {name} changed while its run snapshot was being made")
    os.chmod(snapshot, 0o400)
    return {
        "name": name,
        "size": size,
        "sha256": digest.hexdigest(),
        "snapshot_path": relative.as_posix(),
    }, snapshot


def _input_env_name(name: str) -> str:
    return "T1_INPUT_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _rewrite_input_argv(argv: list[str], replacements: dict[str, str]) -> list[str]:
    rewritten: list[str] = []
    for argument in argv:
        replacement = replacements.get(argument)
        if replacement is None and argument.startswith("-") and "=" in argument:
            option, value = argument.split("=", 1)
            if value in replacements:
                replacement = f"{option}={replacements[value]}"
        rewritten.append(replacement if replacement is not None else argument)
    return rewritten


def _hash_run_files(run_dir: Path, *, exclude: set[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"run output contains a symbolic path: {path.relative_to(run_dir)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"run output contains a special file: {path.relative_to(run_dir)}")
        relative = path.relative_to(run_dir).as_posix()
        if relative in exclude:
            continue
        records.append({"path": relative, "size": path.stat().st_size, "sha256": sha256_file(path)})
    return records


def _append_log(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _drain_child(argv: list[str], cwd: Path, run_dir: Path,
                 timeout_seconds: int, *, extra_env: dict[str, str] | None = None
                 ) -> tuple[int, dict[str, Any]]:
    if not argv or Path(argv[0]).name != Path(sys.executable).name:
        raise ValueError("release runner must use its recorded Python interpreter")
    timeout_seconds = max(1, min(int(timeout_seconds), 7200))
    env = dict(os.environ)
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": "",
        "MPLCONFIGDIR": str(run_dir / ".mplconfig"),
        "XDG_CACHE_HOME": str(run_dir / ".cache"),
        "TMPDIR": str(run_dir / "tmp"),
    })
    env.update(extra_env or {})
    for name in ("MPLCONFIGDIR", "XDG_CACHE_HOME", "TMPDIR"):
        Path(env[name]).mkdir(parents=True, exist_ok=True)
    env.pop("PYTHONHOME", None)
    import selectors

    process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True)
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    digests = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}
    byte_counts = {"stdout": 0, "stderr": 0}
    pending = {"stdout": bytearray(), "stderr": bytearray()}
    dropping_line = {"stdout": False, "stderr": False}
    log_handles = {
        name: (run_dir / f"{name}.log").open("xb")
        for name in ("stdout", "stderr")
    }

    def persist_safe_line(name: str, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace")
        if scan_secret_text({f"{name}.log": text.encode("utf-8")}):
            log_handles[name].write(b"[REDACTED secret-like output line]\n")
        else:
            log_handles[name].write(text.encode("utf-8"))

    deadline = time.monotonic() + timeout_seconds
    timed_out = False
    try:
        while selector.get_map():
            if time.monotonic() > deadline and not timed_out:
                timed_out = True
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 5
            elif timed_out and time.monotonic() > deadline:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = float("inf")
            for key, _ in selector.select(timeout=0.2):
                name = key.data
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    if pending[name] and not dropping_line[name]:
                        persist_safe_line(name, bytes(pending[name]))
                    continue
                digests[name].update(chunk)
                byte_counts[name] += len(chunk)
                pending[name].extend(chunk)
                while True:
                    newline = pending[name].find(b"\n")
                    if newline < 0:
                        break
                    line = bytes(pending[name][:newline + 1])
                    del pending[name][:newline + 1]
                    if not dropping_line[name]:
                        persist_safe_line(name, line)
                    dropping_line[name] = False
                if len(pending[name]) > 65536:
                    pending[name].clear()
                    dropping_line[name] = True
                    log_handles[name].write(b"[REDACTED overlong output line]\n")
        return_code = process.wait()
    finally:
        selector.close()
        for handle in log_handles.values():
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
    return return_code, {
        "timed_out": timed_out,
        "stdout_bytes": byte_counts["stdout"],
        "stdout_sha256": digests["stdout"].hexdigest(),
        "stderr_bytes": byte_counts["stderr"],
        "stderr_sha256": digests["stderr"].hexdigest(),
        "persisted_logs": ["stdout.log", "stderr.log"],
        "log_policy": "secret-like and overlong output lines are redacted",
    }


def run_release(release_id: str, run_id: str, *, release_root: Path = RELEASE_ROOT,
                runs_root: Path = RUN_ROOT, mode: str = "diagnostic",
                config: str = "", authorization: str = "", inputs: list[str] | None = None,
                seed: int | None = None, resource_group: str = "",
                timeout_seconds: int = 1800, argv: list[str]) -> dict[str, Any]:
    release_id = _safe_release_id(release_id)
    run_id = _safe_run_id(run_id)
    if mode not in {"development", "diagnostic"}:
        raise ValueError("release runner accepts development or diagnostic mode; formal runs stay on the existing authorized runner")
    if not argv:
        raise ValueError("a Python command is required")
    release_dir = Path(os.path.abspath(release_root / release_id))
    release = verify_installed_release(release_dir)
    if release.get("release_id") != release_id:
        raise ValueError("requested release identity mismatch")
    argv = _release_python_argv(argv, release_dir)
    runtime_identity = _runtime_identity()
    runtime_contract_record = _verify_runtime_contract(
        release_dir, T1_RUNTIME_IDENTITY_RELATIVE, runtime_identity)
    if resource_group and not RESOURCE_RE.fullmatch(resource_group):
        raise ValueError("invalid resource group")
    runs_root = _require_real_directory(runs_root, create=True)
    run_dir = runs_root / run_id
    if run_dir.is_symlink():
        raise ValueError("run directory may not be a symbolic link")
    if run_dir.exists():
        raise ValueError("run ID already exists; IDs are never reused")
    lock_handle = None
    try:
        if resource_group:
            lock_root = runs_root / ".locks"
            _require_real_directory(lock_root, create=True)
            lock_path = lock_root / f"{resource_group}.lock"
            if lock_path.is_symlink():
                raise ValueError("resource lock path may not be symbolic")
            lock_handle = lock_path.open("a+b")
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("resource group is already in use") from exc
        authorization_record = _hash_input("authorization", authorization, release_dir) if authorization else None
        input_specs: list[tuple[str, str, Path, str]] = []
        for item in inputs or []:
            if "=" not in item:
                raise ValueError("each --input must use name=path")
            name, input_path = item.split("=", 1)
            if name in {"config", "authorization"}:
                raise ValueError("input names 'config' and 'authorization' are reserved")
            source_path = _resolve_input_path(name, input_path, release_dir)
            env_name = _input_env_name(name)
            input_specs.append((name, input_path, source_path, env_name))
        if len({item[0] for item in input_specs}) != len(input_specs):
            raise ValueError("input identities must be unique")
        if len({item[3] for item in input_specs}) != len(input_specs):
            raise ValueError("input identities map to conflicting environment names")
        if any(item[3] == _input_env_name("config") for item in input_specs):
            raise ValueError("input identity conflicts with the reserved config environment name")
        try:
            run_dir.mkdir()
        except FileExistsError as exc:
            raise ValueError("run ID already exists; IDs are never reused") from exc
        config_record: dict[str, Any] | None = None
        input_records: list[dict[str, Any]] = []
        input_env: dict[str, str] = {}
        path_replacements: dict[str, str] = {}
        if config:
            config_record, config_snapshot = _snapshot_input("config", config, release_dir, run_dir)
            input_env[_input_env_name("config")] = str(config_snapshot)
            path_replacements[config] = str(config_snapshot)
            path_replacements[str(_resolve_input_path("config", config, release_dir))] = str(config_snapshot)
        for name, raw_path, source_path, env_name in input_specs:
            record, snapshot = _snapshot_input(name, raw_path, release_dir, run_dir)
            input_records.append(record)
            input_env[env_name] = str(snapshot)
            path_replacements[raw_path] = str(snapshot)
            path_replacements[str(source_path)] = str(snapshot)
        if (run_dir / "inputs").is_dir():
            os.chmod(run_dir / "inputs", 0o500)
        argv = _rewrite_input_argv(argv, path_replacements)
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run_manifest: dict[str, Any] = {
            "schema": RUN_SCHEMA,
            "run_id": run_id,
            "release_id": release_id,
            "execution_class": mode,
            "source_git_commit": release["source_git_commit"],
            "source_tree_sha256": release["source_tree_sha256"],
            "archive_sha256": release["archive_sha256"],
            "release_artifact_sha256": release["artifact_sha256"],
            "repository": release["repository"],
            "remote_backup": release.get("remote_backup", {}),
            "config": config_record,
            "authorization_receipt_sha256": authorization_record["sha256"] if authorization_record else None,
            "inputs": input_records,
            "environment": runtime_identity,
            "runtime_contract": runtime_contract_record,
            "argv": _redact_argv(argv),
            "random_seed": seed,
            "resources": {"cpu_count": os.cpu_count(), "resource_group": resource_group or None},
            "started_at": started,
            "finished_at": None,
            "exit_code": None,
            "status": "running",
        }
        _write_json_atomic(run_dir / RUN_MANIFEST_NAME, run_manifest)
        _append_log(run_dir / "run.log", f"started_at={started}")
        _append_log(run_dir / "run.log", f"command={shlex.join(_redact_argv(argv))}")
        try:
            rc, output = _drain_child(argv, release_dir, run_dir, timeout_seconds,
                                      extra_env=input_env)
        except Exception as exc:
            rc, output = 127, {"runner_error": type(exc).__name__}
            _append_log(run_dir / "run.log", f"runner_error={type(exc).__name__}")
        run_manifest.update({
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "exit_code": rc,
            "status": "timed_out" if output.get("timed_out") else ("completed" if rc == 0 else "failed"),
            "child_output": output,
        })
        _append_log(run_dir / "run.log", f"status={run_manifest['status']} exit_code={rc}")
        _write_json_atomic(run_dir / RUN_MANIFEST_NAME, run_manifest)
        files = _hash_run_files(run_dir, exclude={RUN_RECEIPT_NAME})
        receipt: dict[str, Any] = {
            "schema": RUN_RECEIPT_SCHEMA,
            "run_id": run_id,
            "release_id": release_id,
            "execution_class": mode,
            "source_git_commit": release["source_git_commit"],
            "source_tree_sha256": release["source_tree_sha256"],
            "archive_sha256": release["archive_sha256"],
            "run_manifest_sha256": sha256_file(run_dir / RUN_MANIFEST_NAME),
            "exit_code": rc,
            "status": run_manifest["status"],
            "files": files,
        }
        receipt["receipt_sha256"] = sha256_bytes(canonical_json(receipt))
        _write_json_atomic(run_dir / RUN_RECEIPT_NAME, receipt)
        return receipt
    finally:
        if lock_handle is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()


def verify_run_directory(run_dir: Path, *, expected_run_id: str = "",
                         expected_release_id: str = "") -> dict[str, Any]:
    run_dir = Path(os.path.abspath(run_dir))
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ValueError("run directory is missing or unsafe")
    run_id = _safe_run_id(expected_run_id or run_dir.name)
    manifest_path = run_dir / RUN_MANIFEST_NAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("run manifest is missing or unsafe")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    receipt = _load_hashed_json(run_dir / RUN_RECEIPT_NAME, "receipt_sha256")
    if manifest.get("schema") != RUN_SCHEMA or receipt.get("schema") != RUN_RECEIPT_SCHEMA:
        raise ValueError("unsupported run manifest or receipt schema")
    if (manifest.get("status") not in {"completed", "failed", "timed_out"}
            or not manifest.get("finished_at")):
        raise ValueError("run manifest is not a terminal attempt")
    if expected_run_id and manifest.get("run_id") != expected_run_id:
        raise ValueError("unexpected run ID")
    if manifest.get("run_id") != run_id or receipt.get("run_id") != run_id:
        raise ValueError("run ID does not match its manifest and receipt")
    if expected_run_id and run_id != expected_run_id:
        raise ValueError("unexpected run ID")
    if expected_release_id and manifest.get("release_id") != expected_release_id:
        raise ValueError("unexpected release ID")
    for key in ("release_id", "source_git_commit", "source_tree_sha256", "archive_sha256"):
        if manifest.get(key) != receipt.get(key):
            raise ValueError(f"run receipt {key} does not match its manifest")
    if manifest.get("execution_class") != receipt.get("execution_class"):
        raise ValueError("run receipt execution class does not match its manifest")
    if manifest.get("status") != receipt.get("status") or manifest.get("exit_code") != receipt.get("exit_code"):
        raise ValueError("run receipt status or exit code does not match its manifest")
    if receipt.get("run_manifest_sha256") != sha256_file(run_dir / RUN_MANIFEST_NAME):
        raise ValueError("run manifest SHA-256 mismatch")
    expected_files = receipt.get("files")
    if not isinstance(expected_files, list):
        raise ValueError("run receipt file list is invalid")
    records = _hash_run_files(run_dir, exclude={RUN_RECEIPT_NAME})
    if records != expected_files:
        raise ValueError("run output file set or hash mismatch")
    files_by_path = {item["path"]: item for item in expected_files}
    snapshot_records = ([manifest.get("config")] if manifest.get("config") else [])
    snapshot_records.extend(manifest.get("inputs", []))
    seen_snapshots: set[str] = set()
    for item in snapshot_records:
        if not isinstance(item, dict):
            raise ValueError("run input record is invalid")
        snapshot_path = item.get("snapshot_path")
        if snapshot_path is None:  # Older run receipts predate local input snapshots.
            continue
        relative = _safe_relative(snapshot_path)
        key = relative.as_posix()
        file_record = files_by_path.get(key)
        if (not relative.parts or relative.parts[0] != "inputs" or key in seen_snapshots
                or file_record is None or file_record.get("size") != item.get("size")
                or file_record.get("sha256") != item.get("sha256")):
            raise ValueError("run input snapshot does not match its receipt identity")
        seen_snapshots.add(key)
    return receipt


def _safe_extract_run_stream(stream: BinaryIO, destination: Path) -> set[str]:
    seen: set[str] = set()
    seen_dirs: set[str] = set()
    with tarfile.open(fileobj=stream, mode="r|gz") as archive:
        for member in archive:
            raw = member.name
            while raw.startswith("./"):
                raw = raw[2:]
            if raw.rstrip("/") in {"", "."} and member.isdir():
                continue
            relative = _safe_relative(raw.rstrip("/"))
            if not member.isfile() and not member.isdir():
                raise ValueError(f"unsupported run archive member: {relative}")
            key = relative.as_posix()
            if member.isdir():
                if key in seen:
                    raise ValueError(f"run archive member changes file type: {key}")
                seen_dirs.add(key)
                (destination / Path(*relative.parts)).mkdir(parents=True, exist_ok=True)
                continue
            if key in seen or key in seen_dirs:
                raise ValueError(f"duplicate run archive member: {key}")
            if any(parent.as_posix() in seen for parent in relative.parents if parent.parts):
                raise ValueError(f"run archive member is nested below a file: {key}")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read run archive member: {key}")
            count = 0
            with source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
                    count += len(chunk)
            if count != member.size:
                raise ValueError(f"run archive member size mismatch: {key}")
            seen.add(key)
    return seen


def pullback_run(stream: BinaryIO, evidence_root: Path, *, expected_run_id: str,
                 expected_release_id: str) -> dict[str, Any]:
    run_id = _safe_run_id(expected_run_id)
    release_id = _safe_release_id(expected_release_id)
    evidence_root = _require_real_directory(evidence_root, create=True)
    incoming_root = _require_real_directory(evidence_root / ".incoming", create=True)
    partial = incoming_root / f"{run_id}.partial"
    final = evidence_root / run_id
    if partial.is_symlink() or final.is_symlink():
        raise ValueError("evidence destination may not be a symbolic link")
    if final.exists():
        while stream.read(1024 * 1024):
            pass
        existing = verify_run_directory(final, expected_run_id=run_id,
                                        expected_release_id=release_id)
        return {"status": "already_verified", "run_id": run_id,
                "receipt_sha256": existing["receipt_sha256"], "path": str(final)}
    if partial.exists():
        quarantine = _require_real_directory(evidence_root / ".quarantine", create=True)
        archived = quarantine / f"{run_id}.{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.{uuid.uuid4().hex[:8]}.partial"
        os.replace(partial, archived)
    partial.mkdir()
    _safe_extract_run_stream(stream, partial)
    receipt = verify_run_directory(partial, expected_run_id=run_id,
                                   expected_release_id=release_id)
    _readonly_tree(partial, keep_root_writable=True)
    os.replace(partial, final)
    os.chmod(final, final.stat().st_mode & 0o555)
    return {"status": "verified", "run_id": run_id,
            "receipt_sha256": receipt["receipt_sha256"], "path": str(final)}


def append_deployment_index(repo_root: Path, receipt: dict[str, Any], evidence_path: Path) -> None:
    repo_root = Path(os.path.abspath(repo_root))
    evidence_path = Path(os.path.abspath(evidence_path))
    if evidence_path == repo_root or repo_root in evidence_path.parents:
        raise ValueError("verified evidence must be stored outside the source repository")
    if evidence_path.is_symlink():
        raise ValueError("evidence directory may not be symbolic")
    verified = verify_run_directory(evidence_path, expected_run_id=receipt.get("run_id", ""),
                                    expected_release_id=receipt.get("release_id", ""))
    if verified.get("receipt_sha256") != receipt.get("receipt_sha256"):
        raise ValueError("deployment index receipt does not match verified evidence")
    run_manifest = json.loads((evidence_path / RUN_MANIFEST_NAME).read_text(encoding="utf-8"))
    index = repo_root / "ANALYSIS" / "DEPLOYMENT-INDEX.jsonl"
    if index.is_symlink():
        raise ValueError("deployment index may not be symbolic")
    record = {
        "run_id": receipt["run_id"],
        "release_id": receipt["release_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "pullback_status": "VERIFIED",
        "execution_class": run_manifest.get("execution_class"),
        "analysis_commit": None,
        "evidence_uri": f"evidence://t1/{receipt['run_id']}",
    }
    encoded = canonical_json(record) + b"\n"
    index.parent.mkdir(parents=True, exist_ok=True)
    with index.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        lock.seek(0)
        existing = lock.read()
        for line in existing.splitlines():
            old = json.loads(line)
            if old.get("run_id") == record["run_id"]:
                if old == record:
                    return
                raise ValueError("deployment index already contains this run ID with different evidence")
        with index.open("ab") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build a release from an exact clean Git commit")
    build.add_argument("--repo", type=Path, required=True)
    build.add_argument("--commit", required=True)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--expected-repository", default="")
    install = sub.add_parser("install", help="verify and atomically install a T1 release")
    install.add_argument("--bundle", type=Path, required=True)
    install.add_argument("--releases-root", type=Path, required=True)
    install.add_argument("--bootstrap", type=Path)
    verify = sub.add_parser("verify", help="verify a local release bundle")
    verify.add_argument("--bundle", type=Path, required=True)
    run = sub.add_parser("run", help="run one isolated diagnostic/development attempt")
    run.add_argument("--release-id", required=True)
    run.add_argument("--run-id", required=True)
    run.add_argument("--release-root", type=Path, default=RELEASE_ROOT)
    run.add_argument("--runs-root", type=Path, default=RUN_ROOT)
    run.add_argument("--mode", choices=("development", "diagnostic", "formal"), required=True)
    run.add_argument("--config", default="")
    run.add_argument("--authorization", default="")
    run.add_argument("--input", action="append", default=[])
    run.add_argument("--seed", type=int)
    run.add_argument("--resource-group", default="")
    run.add_argument("--timeout-seconds", type=int, default=1800)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    verify_run = sub.add_parser("verify-run", help="verify a completed run receipt")
    verify_run.add_argument("--run-dir", type=Path, required=True)
    verify_run.add_argument("--run-id", default="")
    verify_run.add_argument("--release-id", default="")
    pull = sub.add_parser("pull", help="verify and atomically pull back one run")
    pull.add_argument("--evidence-root", type=Path, required=True)
    pull.add_argument("--repo-root", type=Path, required=True)
    pull.add_argument("--run-id", required=True)
    pull.add_argument("--release-id", required=True)
    prepare = sub.add_parser("prepare-incoming", help="create a unique T1 incoming attempt")
    prepare.add_argument("--release-id", required=True)
    cleanup = sub.add_parser("cleanup-incoming", help="remove an incoming attempt after verified publish")
    cleanup.add_argument("--release-id", required=True)
    quarantine = sub.add_parser("quarantine-incoming", help="preserve an abandoned partial before retry")
    quarantine.add_argument("--release-id", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            if args.expected_repository and _repo_identity(Path(os.path.abspath(args.repo))) != args.expected_repository.lower():
                raise ValueError("source origin does not match the expected T1 repository")
            result = build_release(args.repo, args.commit, args.out)
        elif args.command == "verify":
            result = verify_release_bundle(args.bundle)
        elif args.command == "install":
            if Path(os.path.abspath(args.releases_root)) != RELEASE_ROOT:
                raise ValueError("remote install target must be exactly /data/论文/leo-t1-wt/releases")
            result = install_release(args.bundle, args.releases_root, bootstrap=args.bootstrap)
        elif args.command == "run":
            if Path(os.path.abspath(args.release_root)) != RELEASE_ROOT or Path(os.path.abspath(args.runs_root)) != RUN_ROOT:
                raise ValueError("remote run paths must use the isolated T1 releases and runs roots")
            child_argv = list(args.argv)
            if child_argv[:1] == ["--"]:
                child_argv = child_argv[1:]
            result = run_release(
                args.release_id, args.run_id, release_root=args.release_root,
                runs_root=args.runs_root, mode=args.mode, config=args.config,
                authorization=args.authorization, inputs=args.input, seed=args.seed,
                resource_group=args.resource_group, timeout_seconds=args.timeout_seconds,
                argv=child_argv,
            )
        elif args.command == "prepare-incoming":
            result = {"status": "prepared", "path": str(prepare_incoming(args.release_id))}
        elif args.command == "cleanup-incoming":
            result = cleanup_incoming(args.release_id)
        elif args.command == "quarantine-incoming":
            result = quarantine_incoming(args.release_id)
        elif args.command == "verify-run":
            result = verify_run_directory(args.run_dir, expected_run_id=args.run_id,
                                          expected_release_id=args.release_id)
        else:
            repo_root = Path(os.path.abspath(args.repo_root))
            evidence_root = Path(os.path.abspath(args.evidence_root))
            if evidence_root == repo_root or repo_root in evidence_root.parents:
                raise ValueError("evidence root must be outside the source repository")
            result = pullback_run(sys.stdin.buffer, args.evidence_root,
                                  expected_run_id=args.run_id,
                                  expected_release_id=args.release_id)
            verified = verify_run_directory(Path(result["path"]), expected_run_id=args.run_id,
                                            expected_release_id=args.release_id)
            append_deployment_index(repo_root, verified, Path(result["path"]))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, tarfile.TarError, json.JSONDecodeError, EOFError) as exc:
        print(f"release_protocol: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
