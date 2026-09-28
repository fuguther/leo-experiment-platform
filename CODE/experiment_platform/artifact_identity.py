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
import json
import platform
import subprocess
from pathlib import Path
from typing import Iterable, Mapping

IDENTITY_SCHEMA = "t1-artifact-identity/v1"

# CODE/experiment_platform/artifact_identity.py -> CODE -> repo
REPO_ROOT = Path(__file__).resolve().parents[2]

# The COMPLETE execution chain an artifact depends on.  A short hand-listed
# driver set was demonstrably incomplete (it omitted time_alignment,
# async_routing, the comparison drivers and the statistics module), so the chain
# is discovered from the package tree instead of being enumerated by hand: any
# .py under CODE/leo_sim or CODE/experiment_platform that is not a test or a
# cache participates.  Adding a new module therefore changes the identity
# automatically, which is the failure mode the reviewer found.
EXECUTION_PACKAGES = ("CODE/leo_sim", "CODE/experiment_platform")


def execution_chain_paths(root: Path = REPO_ROOT) -> tuple:
    root = Path(root)
    out = []
    for package in EXECUTION_PACKAGES:
        base = root / package
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            parts = path.parts
            if "tests" in parts or "__pycache__" in parts:
                continue
            out.append(str(path.relative_to(root)))
    return tuple(out)


#: Backward-compatible alias; new code should use execution_chain_paths().
DEFAULT_DRIVER_PATHS = execution_chain_paths()


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
        # A deployed tree (the VM) has no .git.  The sync step writes
        # launch.json with the exact local HEAD and dirty flag, and an artifact
        # produced there must still be bound to that identity rather than
        # reporting "unknown".
        manifest_path = root / "launch.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"available": False,
                    "reason": "git not available and no launch manifest found"}
        if not manifest.get("head"):
            return {"available": False, "reason": "launch manifest has no head"}
        return {
            "available": True,
            "source": "launch_manifest",
            "commit": str(manifest["head"]),
            "branch": None,
            "dirty": bool(manifest.get("dirty", False)),
            "status_short": [],
            "diff_sha256": None,
            "manifest_path": str(manifest_path),
            "manifest": {k: manifest.get(k) for k in
                         ("schema", "head", "dirty", "synced_at")},
        }
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


def chain_sha256(root: Path = REPO_ROOT) -> str | None:
    """Combined digest of the current execution chain (None if incomplete)."""
    return source_identity(execution_chain_paths(root), root)["combined_sha256"]


def current_identity(root: Path = REPO_ROOT) -> dict:
    """A FRESH identity of the code on disk right now.

    Recovery and validation must compare against this, never against an
    identity string recorded inside an old artifact: a self-consistent old
    record proves nothing about the code that is about to run.
    """
    return {
        "schema": IDENTITY_SCHEMA,
        "git": git_identity(root),
        "sources": source_identity(execution_chain_paths(root), root),
        "runtime": runtime_identity(),
    }


def build_identity(config: Mapping | None = None,
                   trace_digest: str | None = None,
                   driver_paths: Iterable[str | Path] | None = None,
                   root: Path = REPO_ROOT,
                   extra: Mapping | None = None) -> dict:
    """The full execution-chain identity block for an artifact."""
    if driver_paths is None:
        driver_paths = execution_chain_paths(root)
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
