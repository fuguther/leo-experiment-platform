#!/usr/bin/env python3
"""Replay recorded candidate scores with same-run target-time queue truth.

This tool is a C2->C3/C4 diagnostic, not a counterfactual routing oracle. It
holds every recorded score term fixed except ``resource_work_s`` and substitutes
``truth.work_ahead_bits / service_rate_bps`` for that term. It reports whether
that substitution changes the scorer's top-1 ranking and whether the scorer's
original top-1 actually matches the recorded final action.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from CODE.leo_sim.truth_score_replay import replay_dataset  # noqa: E402


def _jsonl(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    kwargs = {"mode": "rt", "encoding": "utf-8"} if path.suffix == ".gz" else {"mode": "r", "encoding": "utf-8"}
    with opener(path, **kwargs) as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Substitute target-time truth into recorded candidate score resource_work_s terms."
    )
    ap.add_argument("--probe", required=True, help="same-run target_probe.jsonl")
    ap.add_argument("--decisions", required=True, help="same-run decisions.jsonl or decisions.jsonl.gz")
    ap.add_argument("--out", help="optional JSON summary output path")
    ap.add_argument(
        "--details-out",
        help="optional JSONL per-decision replay output; omit unless detailed diagnosis is needed",
    )
    args = ap.parse_args()

    probe_path = Path(args.probe)
    decisions_path = Path(args.decisions)
    result = replay_dataset(_jsonl(decisions_path), _jsonl(probe_path))
    details = result.pop("results")
    payload = {
        "schema": "target-truth-score-replay/v1",
        "probe": str(probe_path),
        "decisions": str(decisions_path),
        "scope": {
            "counterfactual_oracle": False,
            "changed_term": "resource_work_s only",
            "truth_term": "truth.work_ahead_bits / service_rate_bps",
            "all_other_score_terms_frozen": True,
            "statistical_cohort": "non-forced probe rows only",
        },
        **result,
    }

    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    if args.details_out:
        details_out = Path(args.details_out)
        details_out.parent.mkdir(parents=True, exist_ok=True)
        with details_out.open("w", encoding="utf-8") as fh:
            for row in details:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
