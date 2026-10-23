#!/usr/bin/env python3
"""Read-only three-end code identity check.

Compares: local git HEAD, GitHub main, the newest immutable release, and
CURRENT.json (when present).  It never writes, fetches, merges or deletes.

Usage:
  python3 -m CODE.scripts.three_end_check \
      --local-repo . --github fuguther/leo-experiment-platform \
      --release-dir /data/论文/leo-t1-wt/releases \
      --current /data/论文/leo-t1-wt/CURRENT.json --fail-on-drift
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from CODE.experiment_platform import code_pointer

COMMIT_FIELDS = ("local", "github", "release", "current")


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(("git", "-C", str(repo)) + args,
                             capture_output=True, text=True, timeout=60,
                             check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def read_local(repo: Path) -> dict:
    head = _git(repo, "rev-parse", "HEAD")
    status = _git(repo, "status", "--porcelain") or ""
    return {"commit": head, "dirty": bool(status.strip())}


def read_github(slug: str, *, offline: bool = False,
                local_repo: Path | None = None) -> dict:
    if offline and local_repo is not None:
        head = _git(local_repo, "rev-parse", "origin/main")
        return {"commit": head, "source": "origin/main (offline)"}
    try:
        out = subprocess.run(
            ("git", "ls-remote", f"https://github.com/{slug}.git",
             "refs/heads/main"),
            capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "error": "git ls-remote failed"}
    if out.returncode != 0:
        return {"commit": None, "error": out.stderr.strip()[:200]}
    line = out.stdout.strip().splitlines()
    return {"commit": line[0].split()[0] if line else None,
            "source": "ls-remote refs/heads/main"}


def read_latest_release(release_dir: Path | None) -> dict:
    if release_dir is None or not release_dir.is_dir():
        return {"commit": None, "release_id": None}
    candidates = [p for p in release_dir.iterdir()
                  if p.is_dir() and (p / ".release-envelope.json").is_file()]
    if not candidates:
        return {"commit": None, "release_id": None}
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    try:
        env = json.loads((latest / ".release-envelope.json").read_text(
            encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"commit": None, "release_id": latest.name}
    return {"commit": env.get("source_git_commit"),
            "release_id": env.get("release_id") or latest.name}


def read_current(path: Path | None) -> dict:
    if path is None:
        return {"commit": None}
    try:
        doc = code_pointer.read_current(path)
    except code_pointer.PointerError as exc:
        return {"commit": None, "error": str(exc)}
    return {"commit": doc["commit"], "release_id": doc.get("release_id"),
            "dirty": doc.get("dirty")}


def compare_rows(rows: dict) -> dict:
    values = {name: row.get("commit") for name, row in rows.items()
              if row.get("commit")}
    distinct = sorted(set(values.values()))
    return {
        "values": values,
        "distinct_commits": distinct,
        "consistent": len(distinct) <= 1,
        "missing": [name for name in rows if not rows[name].get("commit")],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-repo", type=Path, default=Path.cwd())
    parser.add_argument("--github", default="fuguther/leo-experiment-platform")
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--current", type=Path)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-drift", action="store_true")
    args = parser.parse_args(argv)

    rows = {
        "local": read_local(args.local_repo),
        "github": read_github(args.github, offline=args.offline,
                              local_repo=args.local_repo),
        "release": read_latest_release(args.release_dir),
        "current": read_current(args.current),
    }
    comparison = compare_rows(rows)
    payload = {"rows": rows, **comparison}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for name in COMMIT_FIELDS:
            row = rows[name]
            print(f"{name:8} {row.get('commit') or '-':40} "
                  f"{row.get('release_id') or ''} {row.get('error') or ''}")
        print("consistent:", comparison["consistent"])
        if comparison["missing"]:
            print("missing:", ", ".join(comparison["missing"]))
    if args.fail_on_drift and not comparison["consistent"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
