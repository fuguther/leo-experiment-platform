"""Pre-run launch manifest for T1 runs.

The 2026-10-23 three-end audit found 0/175 summaries with a usable Git
identity: ad-hoc sprint runs had no launch manifest, and the only
``launch.json`` was stale and one directory above the run root.  This module
is the missing piece: it writes ONE JSON document *before* the simulator
starts, containing the code identity, every input file hash, argv, env, host,
arms and deadline.  A run that cannot produce a complete manifest is refused
when ``require_pinned`` is set.

Schema: ``t1-launch-manifest/v1``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path

from CODE.experiment_platform import artifact_identity

SCHEMA = "t1-launch-manifest/v1"


class ManifestError(RuntimeError):
    """A manifest cannot be built or verified."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_sha256(doc: dict) -> str:
    unhashed = dict(doc)
    unhashed.pop("manifest_sha256", None)
    return hashlib.sha256(json.dumps(
        unhashed, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _resolve(root: Path, raw: str | os.PathLike) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (root / path)


def build_launch_manifest(
    *,
    root: Path | str,
    out: Path | str,
    run_id: str,
    task: str,
    argv: list[str] | None = None,
    inputs: list[tuple[str, str | os.PathLike]] | None = None,
    arms: list[str] | None = None,
    deadline_s: float | None = None,
    require_pinned: bool = False,
) -> dict:
    root = Path(root).resolve()
    out = Path(out)
    code = artifact_identity.current_identity(root)
    sources = code.get("sources") or {}
    if not sources.get("combined_sha256"):
        raise ManifestError(
            "execution chain is incomplete: at least one CODE file is missing "
            "or unreadable, so no combined_sha256 exists")
    git = code.get("git") or {}
    if require_pinned:
        if not git.get("available"):
            raise ManifestError(
                f"code identity unavailable: {git.get('reason', 'unknown')}; "
                "refusing to launch a pinned run")
        if git.get("dirty"):
            raise ManifestError(
                "code identity is dirty; refusing a pinned run "
                "(commit/stash the worktree first)")

    resolved_inputs = []
    for role, raw in inputs or []:
        path = _resolve(root, raw)
        if not path.is_file():
            raise ManifestError(f"input {role!r} is missing: {path}")
        resolved_inputs.append({
            "role": role,
            "path": str(path),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        })

    manifest = {
        "schema": SCHEMA,
        "run_id": str(run_id),
        "task": str(task),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "host": {
            "hostname": socket.gethostname(),
            "platform": sys.platform,
            "python": sys.version.split()[0],
        },
        "code": code,
        "inputs": resolved_inputs,
        "argv": list(argv or []),
        "env": artifact_identity.env_identity(),
        "arms": list(arms or []),
        "deadline_s": deadline_s,
    }
    manifest["manifest_sha256"] = _canonical_sha256(manifest)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(out)
    return manifest


def verify_launch_manifest(path: Path | str, *, require_pinned: bool = False) -> dict:
    path = Path(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read launch manifest {path}: {exc}") from exc
    if manifest.get("schema") != SCHEMA:
        raise ManifestError(f"unexpected schema: {manifest.get('schema')!r}")
    for key in ("run_id", "task", "code", "inputs", "argv", "env"):
        if key not in manifest:
            raise ManifestError(f"missing required field: {key}")
    code = manifest["code"]
    if not (code.get("sources") or {}).get("combined_sha256"):
        raise ManifestError("manifest has no combined_sha256 for the code chain")
    if require_pinned and not (code.get("git") or {}).get("available"):
        raise ManifestError(
            "manifest code identity is not pinned: "
            f"{(code.get('git') or {}).get('reason', 'unknown')}")
    for item in manifest["inputs"]:
        digest = item.get("sha256") or ""
        if len(digest) != 64:
            raise ManifestError(f"input without sha256: {item!r}")
        path = Path(item.get("path") or "")
        if not path.is_file():
            raise ManifestError(f"input file is missing: {path}")
        if path.stat().st_size != item.get("size"):
            raise ManifestError(f"input size changed: {path}")
        if sha256_file(path) != digest:
            raise ManifestError(f"input sha256 changed: {path}")
    recorded = manifest.get("manifest_sha256")
    actual = _canonical_sha256(manifest)
    if recorded != actual:
        raise ManifestError("manifest_sha256 does not match its content")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Build/verify a pre-run T1 launch manifest")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", default="")
    parser.add_argument("--task", default="network_alignment")
    parser.add_argument("--input", action="append", default=[],
                        metavar="ROLE=PATH")
    parser.add_argument("--arms", default="")
    parser.add_argument("--deadline-s", type=float, default=None)
    parser.add_argument("--require-pinned", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            manifest = verify_launch_manifest(
                args.out, require_pinned=args.require_pinned)
            print(json.dumps({"status": "ok", "run_id": manifest["run_id"],
                              "manifest_sha256": manifest["manifest_sha256"]},
                             ensure_ascii=False))
            return 0
        inputs = []
        for raw in args.input:
            if "=" not in raw:
                parser.error(f"--input must be ROLE=PATH, got {raw!r}")
            role, path = raw.split("=", 1)
            inputs.append((role, path))
        manifest = build_launch_manifest(
            root=args.root, out=args.out,
            run_id=args.run_id or args.out.stem,
            task=args.task, argv=sys.argv[1:], inputs=inputs,
            arms=[a for a in args.arms.split(",") if a],
            deadline_s=args.deadline_s,
            require_pinned=args.require_pinned)
        print(json.dumps({"status": "ok", "out": str(args.out),
                          "manifest_sha256": manifest["manifest_sha256"]},
                         ensure_ascii=False))
        return 0
    except ManifestError as exc:
        print(f"LAUNCH-MANIFEST REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
