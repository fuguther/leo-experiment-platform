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
import re
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
            # macOS tar writes AppleDouble sidecars (._name.py); a metadata
            # file is not source and must never change the execution identity
            if path.name.startswith("._") or path.name.startswith("."):
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
        # Immutable T1 releases have no .git or launch.json.  Their commit is
        # witnessed by a read-only file and a self-hashed release manifest.
        # Verify both before claiming that identity; never infer a commit from
        # an unverified artifact field.
        witness_path = root / ".deployment_commit"
        release_manifest_path = root / ".release-manifest.json"
        try:
            witness = witness_path.read_text(encoding="ascii").strip()
            release_manifest = json.loads(
                release_manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            witness = ""
            release_manifest = None
        if (isinstance(release_manifest, dict)
                and re.fullmatch(r"[0-9a-f]{40}", witness)
                and release_manifest.get("schema") ==
                    "leo-immutable-release-manifest/v1"
                and release_manifest.get("source_git_commit") == witness
                and release_manifest.get("source_git_dirty") is False):
            recorded_hash = release_manifest.get("manifest_sha256")
            unhashed = dict(release_manifest)
            unhashed.pop("manifest_sha256", None)
            actual_hash = hashlib.sha256(json.dumps(
                unhashed, ensure_ascii=False, sort_keys=True,
                separators=(",", ":")).encode("utf-8")).hexdigest()
            deployed_files = release_manifest.get("deployed_files") or []
            witness_records = [item for item in deployed_files
                               if item.get("path") == ".deployment_commit"]
            witness_sha = hashlib.sha256(witness_path.read_bytes()).hexdigest()
            if (recorded_hash == actual_hash and len(witness_records) == 1
                    and witness_records[0].get("sha256") == witness_sha
                    and witness_records[0].get("size") ==
                        witness_path.stat().st_size):
                return {
                    "available": True,
                    "source": "immutable_release",
                    "commit": witness,
                    "branch": None,
                    "dirty": False,
                    "status_short": [],
                    "diff_sha256": None,
                    "manifest_path": str(release_manifest_path),
                    "manifest_sha256": actual_hash,
                }
        # A legacy development deployment has no Git checkout.  Its sync step
        # writes launch.json with the exact local HEAD and dirty flag.
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
