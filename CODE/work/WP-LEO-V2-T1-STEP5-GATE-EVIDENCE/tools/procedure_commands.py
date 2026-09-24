#!/usr/bin/env python3
"""Canonical generator for the four formal run commands (step-5 batch, R06).

WHY THIS EXISTS
===============
Four review rounds tried to close "the procedure points at the wrong revision" by
SCANNING human prose for suspicious experiment ids. Each patch was evaded:

  * R04: the scan covered only backtick-fenced blocks, so an unfenced command passed;
  * R05: the scan covered the whole document, so an id split across backslash-
    continued lines, or written in lower case, passed unnoticed.

Scanning human text is whack-a-mole: variable assembly, character insertion or
homoglyphs are always one step ahead. This module inverts the relationship. The
run commands are DERIVED from the compiled run-manifests, and the procedure is
required to contain the derived text BYTE-FOR-BYTE inside explicit markers. A
command is therefore no longer "checked"; it is reproduced. Any deviation -
fenced or not, split, re-cased, obfuscated or edited by one character - simply is
not the generated text and fails the containment check.

This module is part of the reviewed artifact set, so the generator itself is
audited rather than trusted.

USAGE
=====
  python3 procedure_commands.py --root . \
      --experiment-id EXP-...-MICRO-R06 --experiment-id EXP-...-PRESSURE-R06 \
      --out /tmp/run-commands.md          # writes the marked block

  python3 procedure_commands.py --root . --experiment-id ... --print   # stdout
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "leo-sim-step5-run-command-generator/v1"
NL = chr(10)
FENCE = chr(96) * 3
BEGIN = "<!-- GENERATED-RUN-COMMANDS:BEGIN (do not edit by hand) -->"
END = "<!-- GENERATED-RUN-COMMANDS:END -->"

# The ONE canonical shape of a formal run command. Everything else is deviation.
TEMPLATE = (
    "CODE/scripts/remote/run-remote.sh --runtime-kind leo_sim_v2 " + chr(92) + NL +
    "  --config {config} " + chr(92) + NL +
    "  --authorization {authorization} " + chr(92) + NL +
    "  --session {session}"
)


def cells(root: Path, experiment_ids):
    """Yield (experiment_id, run_id, config_path) from the compiled manifests."""
    for eid in experiment_ids:
        manifest = json.loads((root / "EXPERIMENTS" / eid / "run-manifest.json").read_text())
        for cell in manifest["cells"]:
            yield eid, cell["run_id"], cell["config_path"]


def command_for(experiment_id: str, run_id: str, config_path: str) -> str:
    return TEMPLATE.format(
        config="EXPERIMENTS/" + experiment_id + "/" + config_path,
        authorization="EXPERIMENTS/" + experiment_id + "/authorization.json",
        session=run_id.lower(),
    )


def generate_body(root: Path, experiment_ids) -> str:
    """The canonical body: one fenced block per cell, blank line between them."""
    blocks = []
    for eid, run_id, config_path in cells(root, experiment_ids):
        blocks.append(FENCE + "bash" + NL + command_for(eid, run_id, config_path) + NL + FENCE)
    return (NL + NL).join(blocks) + NL


def generate_block(root: Path, experiment_ids) -> str:
    """The canonical marked block that a procedure must contain byte-for-byte."""
    return BEGIN + NL + generate_body(root, experiment_ids) + END + NL


def generate_document_appendix(root: Path, experiment_ids) -> str:
    """A ready-to-paste section documenting the structural rule."""
    body = generate_body(root, experiment_ids)
    lines = [
        "## 5. Formal runs (GENERATED - reproduced, not checked)",
        "",
        "These commands are generated from the compiled run-manifests by",
        "CODE/work/WP-LEO-V2-T1-STEP5-GATE-EVIDENCE/tools/procedure_commands.py and must appear",
        "here byte-for-byte between the markers. Editing them by even one character fails gate",
        "check G0, which is the structural guarantee of this revision.",
        "",
        BEGIN,
        body + END,
    ]
    return NL.join(lines) + NL


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".")
    ap.add_argument("--experiment-id", action="append", required=True)
    ap.add_argument("--out")
    ap.add_argument("--print", dest="to_stdout", action="store_true")
    ap.add_argument("--appendix", action="store_true",
                    help="emit the full procedure section rather than only the marked block")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    for eid in a.experiment_id:
        if not (root / "EXPERIMENTS" / eid / "run-manifest.json").is_file():
            print("generator: missing run-manifest for " + eid, file=sys.stderr)
            return 2
    text = (generate_document_appendix(root, a.experiment_id) if a.appendix
            else generate_block(root, a.experiment_id))
    if a.to_stdout or not a.out:
        sys.stdout.write(text)
        return 0
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(json.dumps({"wrote": str(out), "bytes": len(text.encode("utf-8")),
                      "cells": len(list(cells(root, a.experiment_id)))}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
