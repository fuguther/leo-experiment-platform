"""Fold the decision log and the timeline stream into the per-decision time ledger.

Why this entry point exists
---------------------------
```decision_ledger.build_ledger``` is the only thing that turns the two streams a
run produces into the eleven-instant chain that research line TIME-SEMANTICS
needs (attributable / 可归因).  Measured on 2026-09-24, it had **no production
caller at all**: 13 call sites, every one of them a test.  The line's capability
table therefore said "producer-verified" while the analysis path could not
reach the folding step.  This module is the missing entry point.

It deliberately does NOT reimplement the folding or touch the engine: it reads
the two published JSONL streams, calls ``build_ledger``, and publishes the
result atomically together with the hashes of its inputs.

Usage
-----
    python3 -m CODE.experiment_platform.fold_decision_ledger \
        --decision-log out/run-decisions.jsonl \
        --timeline-log out/run-timeline.jsonl \
        --out out/run-ledger.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from CODE.leo_sim import decision_ledger

SCHEMA = "decision-ledger-fold/v1"


class FoldError(RuntimeError):
    """The fold could not be produced; nothing was published."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _check_new_destination(target: Path) -> None:
    """Refuse an existing target and any symlink in its parent chain.

    Same fail-closed posture as the run CLI: a fold must never silently
    overwrite a previous one, and must never be written through a symlink.
    """
    if target.exists() or target.is_symlink():
        raise FoldError(f"output destination exists: {target}")
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise FoldError(
            f"output parent must be an existing non-symlink directory: {parent}")


def _read_jsonl(path: Path, what: str) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise FoldError(f"{what} is missing or symbolic: {path}")
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FoldError(f"{what} is unreadable: {exc}") from exc
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FoldError(f"{what} line {number} is not JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise FoldError(f"{what} line {number} is not a JSON object")
        rows.append(row)
    if not rows:
        raise FoldError(f"{what} is empty: {path}")
    return rows


def fold(decision_log: Path, timeline_log: Path) -> dict[str, Any]:
    """Read both streams and produce the folded ledger document."""
    decision_rows = _read_jsonl(decision_log, "decision log")
    timeline_rows = _read_jsonl(timeline_log, "timeline log")
    by_decision, diagnostics = decision_ledger.build_ledger(
        decision_rows, timeline_rows)
    if not by_decision:
        raise FoldError(
            "the fold produced no decisions; the two streams do not describe "
            "the same run")
    return {
        "schema": SCHEMA,
        "source": {
            "decision_log": str(decision_log),
            "decision_log_sha256": _sha256_file(decision_log),
            "decision_rows": len(decision_rows),
            "timeline_log": str(timeline_log),
            "timeline_log_sha256": _sha256_file(timeline_log),
            "timeline_rows": len(timeline_rows),
        },
        "timeline_fields": list(decision_ledger.TIMELINE_FIELDS),
        "by_decision": by_decision,
        "diagnostics": diagnostics,
    }


def publish(document: dict[str, Any], out: Path) -> None:
    """Publish atomically: a reader never sees a partial fold."""
    _check_new_destination(out)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{out.name}.", suffix=".tmp", dir=str(out.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, out)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fold a decision log and a timeline stream into the "
                    "per-decision eleven-instant ledger")
    parser.add_argument("--decision-log", type=Path, required=True)
    parser.add_argument("--timeline-log", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = fold(args.decision_log, args.timeline_log)
        publish(document, args.out)
    except FoldError as exc:
        print(f"FOLD REFUSED: {exc}")
        return 2
    print(json.dumps({
        "status": "folded",
        "out": str(args.out),
        "decisions": len(document["by_decision"]),
        "diagnostics": document["diagnostics"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
