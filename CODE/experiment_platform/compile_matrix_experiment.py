#!/usr/bin/env python3
"""CLI for compiling the independent leo_sim V2 matrix contract."""
from __future__ import annotations

import argparse
from pathlib import Path

from CODE.leo_sim.matrix import compile_matrix_experiment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    compile_matrix_experiment(args.request.resolve(), args.out.resolve(),
                               project_root=args.root.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
