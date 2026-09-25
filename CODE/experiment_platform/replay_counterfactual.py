"""Drive a strictly paired counterfactual replay from the command line.

Why this entry point exists
---------------------------
``counterfactual.replay_with_forced_action``` is the implementation of the
platform's "intervenable" invariant (可干预) for research line
TIME-SEMANTICS: replay the SAME immutable trace / config / seed to the SAME
decision, prove the pre-branch state is identical, then change exactly one
action.  Measured on 2026-09-24 it had **no production caller**: its only
non-test consumer of ``kernel.forced_actions`` was itself, and nothing called
it.  The capability existed and was tested, but no run could exercise it.

This module is the missing driver.  It does NOT touch the engine: the kernel
side hook (``forced_actions``, "strictly opt-in") and the harness are both used
as they are.

Why it takes a config and not a trace artifact
----------------------------------------------
The trace is a deterministic function of the resolved config -- measured on
2026-09-24 by compiling the same config twice and comparing trace.csv bytes
(identical: 47a4af9f...).  So replaying from the config reproduces the same
immutable trace, and the harness's own branch fingerprint is what proves the
two runs reached the same branch point.

Constraint
----------
The first version of the harness requires a deterministic router:
``learning.algorithm`` must be "none".  A config with learning on is refused
by the harness itself, and this driver lets that refusal through unchanged.

Usage
-----
    python3 -m CODE.experiment_platform.replay_counterfactual \
        --config CODE/leo_sim/profiles/smoke.yaml \
        --decision-id 18 --forced-action S \
        --out out/replay.json
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from CODE.leo_sim import config as config_mod
from CODE.leo_sim import counterfactual, trace as trace_mod

SCHEMA = "counterfactual-replay/v1"


class ReplayDriverError(RuntimeError):
    """The replay could not be driven; nothing was published."""


def _check_new_destination(target: Path) -> None:
    if target.exists() or target.is_symlink():
        raise ReplayDriverError(f"output destination exists: {target}")
    parent = target.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ReplayDriverError(
            f"output parent must be an existing non-symlink directory: {parent}")


def replay(config_path: Path, decision_id: int, forced_action: str,
           root: Path) -> dict[str, Any]:
    """Compile the trace, replay twice, force one action at one decision."""
    try:
        resolved = config_mod.load_config_file(str(config_path))
    except (config_mod.ConfigError, FileNotFoundError) as exc:
        raise ReplayDriverError(f"config invalid: {exc}") from exc

    algorithm = resolved["config"]["learning"]["algorithm"]
    if algorithm != "none":
        raise ReplayDriverError(
            "the counterfactual harness requires a deterministic router; "
            f"learning.algorithm={algorithm!r} is not supported")

    work = Path(tempfile.mkdtemp(prefix="counterfactual-", dir=str(root)))
    try:
        manifest = trace_mod.compile_trace(resolved, str(work))
        rows = trace_mod.load_trace(
            str(work / "trace.csv"),
            horizon_s=manifest["emission_end_s"],
            max_packets=resolved["config"]["execution"]["max_packets"])
        result = counterfactual.replay_with_forced_action(
            resolved, rows,
            target_decision_id=decision_id,
            forced_action=forced_action)
    except counterfactual.CounterfactualError as exc:
        raise ReplayDriverError(f"replay refused: {exc}") from exc
    finally:
        for leftover in sorted(work.glob("*"), reverse=True):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass

    result["schema"] = SCHEMA
    result["source"] = {
        "config": str(config_path),
        "config_sha256": resolved["sha256"],
        "trace_sha256": manifest.get("__trace_sha256")
                        or manifest.get("trace_sha256"),
        "target_decision_id": decision_id,
        "forced_action": forced_action,
    }
    return result


def publish(document: dict[str, Any], out: Path) -> None:
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
        description="Strictly paired counterfactual replay of one decision")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--decision-id", type=int, required=True)
    parser.add_argument("--forced-action", required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        document = replay(args.config, args.decision_id, args.forced_action,
                          args.root)
        publish(document, args.out)
    except ReplayDriverError as exc:
        print(f"REPLAY REFUSED: {exc}")
        return 2
    verification = document["verification"]
    print(json.dumps({
        "status": "replayed",
        "out": str(args.out),
        "branch_states_identical": verification["branch_states_identical"],
        "baseline_action": verification["baseline_action"],
        "forced_action": verification["forced_action"],
        "outcome_delta": document["outcome_delta"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
