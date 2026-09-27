"""Execution-chain identity for diagnostic artifacts (T1-COMPLETE P1).

A diagnostic artifact must be traceable back to a UNIQUE code identity.  The
core simulator hash (receipt.code_sha256) only covers leo_sim/*.py; a T1
diagnostic also depends on the experiment drivers, the resolved config and the
compiled trace.  This module produces the whole chain:

  * Git commit + branch + dirty flag + diff digest (worktree identity),
  * per-file SHA256 over the named source paths (core + drivers),
  * runtime (python/simpy/numpy/pyyaml),
  * resolved config SHA256 and trace digest when the caller has them.

It is read-only: it never writes, mutates or checks out anything, and it fails
soft (available: false) when Git is not present so an offline artifact can
still be produced with an explicit provenance gap rather than a crash.
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

IDENTITY_SCHEMA = "t1-artifact-identity/v1"

# CODE/experiment_platform/artifact_identity.py -> CODE -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]

# The drivers whose behaviour an artifact depends on, relative to the repo root.
DEFAULT_DRIVER_PATHS = (
    "CODE/experiment_platform/artifact_identity.py",
    "CODE/leo_sim/kernel.py",
    "CODE/leo_sim/config.py",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_identity(paths: Iterable[str | Path], root: Path = REPO_ROOT) -> dict:
    """Path-bound SHA256 for each source file plus a combined digest.

    A missing file is recorded as None and is visible in the output; the
    combined digest is only produced when every file exists.
    """
    files: dict[str, str | None] = {}
    root = Path(root).resolve()
    for raw in paths:
        p = Path(raw)
        full = p if p.is_absolute() else root / p
        try:
            rel = str(full.resolve().relative_to(root))
        except ValueError:
            rel = str(full)
        try:
            files[rel] = _sha256_file(full)
        except OSError:
            files[rel] = None
    combined = None
    if files and all(v is not None for v in files.values()):
        h = hashlib.sha256()
        for name in sorted(files):
            h.update(name.encode("utf-8"))
            h.update(bytes.fromhex(files[name]))
        combined = h.hexdigest()
    return {"schema": IDENTITY_SCHEMA, "files": files, "combined_sha256": combined}


def git_identity(root: Path = REPO_ROOT) -> dict:
    """Commit / branch / dirty / diff digest.  Read-only, fails soft."""
    root = Path(root)

    def _git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ("git",) + args, cwd=str(root), capture_output=True,
                text=True, timeout=30, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout

    commit = _git("rev-parse", "HEAD")
    if commit is None:
        return {"available": False, "reason": "git not available or not a repository"}
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    porcelain = _git("status", "--porcelain")
    status_lines = [ln for ln in (porcelain or "").splitlines() if ln.strip()]
    diff_branch = _git("diff")
    diff_staged = _git("diff", "--cached")
    h = hashlib.sha256()
    h.update((diff_branch or "").encode("utf-8"))
    h.update(b"\0")
    h.update((diff_staged or "").encode("utf-8"))
    return {
        "available": True,
        "commit": commit.strip(),
        "branch": (branch or "").strip() or None,
        "dirty": bool(status_lines),
        "status_short": status_lines,
        "diff_sha256": h.hexdigest(),
    }


def runtime_identity() -> dict:
    import numpy
    import simpy
    import yaml

    return {
        "python": platform.python_version(),
        "simpy": simpy.__version__,
        "numpy": numpy.__version__,
        "pyyaml": yaml.__version__,
        "platform": platform.platform(),
    }


def build_identity(config: Mapping | None = None,
                   trace_digest: str | None = None,
                   driver_paths: Iterable[str | Path] = DEFAULT_DRIVER_PATHS,
                   root: Path = REPO_ROOT,
                   extra: Mapping | None = None) -> dict:
    """The full execution-chain identity block for an artifact."""
    doc = {
        "schema": IDENTITY_SCHEMA,
        "git": git_identity(root),
        "sources": source_identity(driver_paths, root),
        "runtime": runtime_identity(),
    }
    if config is not None:
        doc["config_sha256"] = config.get("sha256")
    if trace_digest is not None:
        doc["trace_digest"] = trace_digest
    if extra:
        doc["extra"] = dict(extra)
    return doc
