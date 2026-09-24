#!/usr/bin/env python3
"""Transactionally validate and stage a remote Results tar stream before writes."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reject_symlink_components(path: Path, stop: Path) -> None:
    current = stop
    try:
        relative = path.relative_to(stop)
    except ValueError as exc:
        raise SystemExit(f"destination escapes Results: {path}") from exc
    for part in relative.parts:
        current /= part
        if not current.exists() and not current.is_symlink():
            continue
        mode = current.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise SystemExit(f"destination component is a symlink: {current}")
        if not stat.S_ISDIR(mode):
            raise SystemExit(f"destination component is not a directory: {current}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code-dir", type=Path, required=True)
    args = parser.parse_args()
    code_dir = Path(os.path.abspath(os.fspath(args.code_dir)))
    if code_dir.is_symlink() or not code_dir.is_dir() or code_dir.resolve() != code_dir:
        raise SystemExit("local CODE directory must be an existing lexical non-symlink directory")
    results_root = code_dir / "Results"
    if results_root.is_symlink():
        raise SystemExit("local Results directory may not be a symbolic link")
    if results_root.exists() and (not results_root.is_dir() or results_root.resolve() != results_root):
        raise SystemExit("local Results lexical path resolves elsewhere")

    with tempfile.TemporaryDirectory(prefix="leo-results-pull.", dir=code_dir.parent) as raw_staging:
        staging = Path(raw_staging)
        staged: dict[str, Path] = {}

        # Phase 1: consume and validate the entire stream into an isolated directory.
        with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
            for member in archive:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "Results":
                    raise SystemExit(f"unsafe tar member path: {member.name}")
                if not member.isfile():
                    raise SystemExit(f"unsafe tar member type: {member.name}")
                key = relative.as_posix()
                if key in staged:
                    raise SystemExit(f"duplicate tar member: {member.name}")
                source = archive.extractfile(member)
                if source is None:
                    raise SystemExit(f"could not read tar member: {member.name}")
                staged_path = staging.joinpath(*relative.parts)
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                with staged_path.open("xb") as output:
                    shutil.copyfileobj(source, output)
                staged[key] = staged_path

        # Phase 2: preflight every destination. Existing different content is a
        # conflict, never an overwrite. No destination is touched in this phase.
        pending: list[tuple[Path, Path]] = []
        for key in sorted(staged):
            relative = PurePosixPath(key)
            destination = code_dir.joinpath(*relative.parts)
            reject_symlink_components(destination.parent, code_dir)
            if destination.exists() or destination.is_symlink():
                if destination.is_symlink() or not destination.is_file():
                    raise SystemExit(f"existing destination is unsafe: {destination}")
                if destination.stat().st_size != staged[key].stat().st_size or sha256_file(destination) != sha256_file(staged[key]):
                    raise SystemExit(f"existing destination content conflict: {destination}")
                continue
            pending.append((staged[key], destination))

        # Phase 3: commit only missing files. Existing files are never modified.
        for staged_path, destination in pending:
            destination.parent.mkdir(parents=True, exist_ok=True)
            reject_symlink_components(destination.parent, code_dir)
            os.replace(staged_path, destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
