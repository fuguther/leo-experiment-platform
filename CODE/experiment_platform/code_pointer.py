"""The one mutable pointer to the code a deployment should run.

Immutable releases remain the source of truth for a run; this module only
answers "which commit is current right now" in a way a script can check.  It
is intentionally small: a JSON document with a commit, an optional release id,
a dirty flag, and a self-hash.

Schema: ``t1-current-code/v1``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from CODE.experiment_platform import artifact_identity

SCHEMA = "t1-current-code/v1"
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class PointerError(RuntimeError):
    """A pointer cannot be built, written or verified."""


def _canonical_sha256(doc: dict) -> str:
    unhashed = dict(doc)
    unhashed.pop("pointer_sha256", None)
    return hashlib.sha256(json.dumps(
        unhashed, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_current(root: Path | str, *, release_id: str | None = None,
                  source: str = "git") -> dict:
    git = artifact_identity.git_identity(Path(root))
    commit = git.get("commit")
    if not git.get("available") or not commit or not COMMIT_RE.match(commit):
        raise PointerError(
            f"no verified commit under {root}: {git.get('reason', 'unknown')}")
    return {
        "schema": SCHEMA,
        "commit": commit,
        "branch": git.get("branch"),
        "dirty": bool(git.get("dirty")),
        "release_id": release_id,
        "source": source,
        "updated_at": _now(),
    }


def build_from_release(release_dir: Path | str) -> dict:
    release_dir = Path(release_dir)
    try:
        commit = (release_dir / ".deployment_commit").read_text(
            encoding="ascii").strip()
        envelope = json.loads(
            (release_dir / ".release-envelope.json").read_text(
                encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PointerError(f"not a readable immutable release: {exc}") from exc
    if not COMMIT_RE.match(commit):
        raise PointerError(f"invalid .deployment_commit: {commit!r}")
    if envelope.get("source_git_commit") != commit:
        raise PointerError("release envelope does not match .deployment_commit")
    return {
        "schema": SCHEMA,
        "commit": commit,
        "branch": envelope.get("source_git_branch"),
        "dirty": bool(envelope.get("source_git_dirty", False)),
        "release_id": envelope.get("release_id"),
        "source": "immutable_release",
        "updated_at": _now(),
    }


def write_current(path: Path | str, doc: dict) -> dict:
    if doc.get("schema") != SCHEMA:
        raise PointerError(f"unexpected schema: {doc.get('schema')!r}")
    if not COMMIT_RE.match(str(doc.get("commit") or "")):
        raise PointerError("pointer commit must be a 40-char hex SHA")
    doc = dict(doc)
    doc["pointer_sha256"] = _canonical_sha256(doc)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(path)
    return doc


def read_current(path: Path | str) -> dict:
    path = Path(path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PointerError(f"cannot read pointer {path}: {exc}") from exc
    if doc.get("schema") != SCHEMA:
        raise PointerError(f"unexpected schema: {doc.get('schema')!r}")
    if not COMMIT_RE.match(str(doc.get("commit") or "")):
        raise PointerError("pointer commit must be a 40-char hex SHA")
    recorded = doc.get("pointer_sha256")
    if recorded and recorded != _canonical_sha256(doc):
        raise PointerError("pointer_sha256 does not match its content")
    return doc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Write/verify CURRENT.json")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            doc = read_current(args.out)
        elif args.release_dir:
            doc = build_from_release(args.release_dir)
            write_current(args.out, doc)
        else:
            doc = build_current(args.root)
            write_current(args.out, doc)
        print(json.dumps({"status": "ok", "out": str(args.out),
                          "commit": doc["commit"],
                          "pointer_sha256": doc.get("pointer_sha256")},
                         ensure_ascii=False))
        return 0
    except PointerError as exc:
        print(f"CURRENT REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
