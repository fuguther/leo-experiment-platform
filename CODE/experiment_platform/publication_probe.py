"""Bounded pure-JSON publication probe for the production T1 writer."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import sys
import time
from pathlib import Path

from CODE.experiment_platform import t1_tasks

SCHEMA = "engineering-publication-probe/v1"
DEFAULT_ROWS = 4096
MIN_ROWS = 1
MAX_ROWS = 8192
MAX_OUTPUT_BYTES = 256 * 1024 * 1024
HASH_READ_BYTES = 1024 * 1024


class ProbeError(RuntimeError):
    """The bounded publication check could not be completed."""


def _validate_rows(rows: int) -> int:
    if isinstance(rows, bool) or not isinstance(rows, int):
        raise ProbeError("--rows must be an integer")
    if not MIN_ROWS <= rows <= MAX_ROWS:
        raise ProbeError(f"--rows must be between {MIN_ROWS} and {MAX_ROWS}")
    return rows


def _fixture_row() -> dict:
    """Return a fixed nested fixture with repeated shared Python references."""
    shared_array = [[index, index * 2, "节点😀"] for index in range(36)]
    shared_observation = {
        "卫星": "星河-α-🚀",
        "邻居": [{"id": f"LEO-{index:02d}", "可见": index % 2 == 0}
                 for index in range(6)],
        "队列快照": shared_array,
        "嵌套": {"状态": "可转发", "轨迹": shared_array},
    }
    return {
        "决策": "最短预计时延",
        "观测": shared_observation,
        "候选": [
            {"下一跳": f"LEO-{index:02d}", "观测": shared_observation,
             "预测数组": shared_array}
            for index in range(4)
        ],
        "数组": {"历史": shared_array, "有效": [True, False, True]},
    }


def _output_size(prefix: bytes, row: bytes, suffix: bytes, rows: int) -> int:
    return len(prefix) + len(suffix) + rows * len(row) + max(rows - 1, 0)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(HASH_READ_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _peak_rss() -> dict:
    system = platform.system()
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if system == "Linux":
        unit = "KiB"
    elif system == "Darwin":
        unit = "bytes"
    else:
        unit = "platform_ru_maxrss"
    return {"value": value, "unit": unit, "platform": system}


def _source_driver_sha256() -> str:
    return hashlib.sha256(Path(t1_tasks.__file__).read_bytes()).hexdigest()


def _probe_sha256() -> str:
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _write_report(path: Path, report: dict) -> None:
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def run_probe(out_dir: str | Path, rows: int = DEFAULT_ROWS) -> dict:
    rows = _validate_rows(rows)
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise ProbeError(f"output directory already exists: {out_dir}")
    parent = out_dir.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ProbeError(f"output parent must be a real directory: {parent}")

    # Independent canonical expectation: publish() emits sort_keys=True JSON,
    # so "rows" precedes "schema" in the standard JSON object ordering.
    prefix = b'{"rows":['
    suffix = b'],"schema":"' + SCHEMA.encode("ascii") + b'"}\n'
    fixture_row = _fixture_row()
    row = json.dumps(fixture_row, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    outputbytes = _output_size(prefix, row, suffix, rows)
    if outputbytes > MAX_OUTPUT_BYTES:
        raise ProbeError(
            f"fixture would exceed the {MAX_OUTPUT_BYTES}-byte output limit")

    # The destination is created only after every fixed input bound is checked.
    # t1_tasks.publish owns the fixture's atomic write and cleanup semantics.
    out_dir.mkdir()
    fixture_path = out_dir / "fixture.json"
    expected = hashlib.sha256()
    expected.update(prefix)
    for index in range(rows):
        if index:
            expected.update(b",")
        expected.update(row)
    expected.update(suffix)
    document = {"rows": [fixture_row] * rows, "schema": SCHEMA}
    wall_start = time.perf_counter()
    t1_tasks.publish(document, fixture_path)
    sha_expected = expected.hexdigest()
    sha_actual = _hash_file(fixture_path)
    actual_outputbytes = fixture_path.stat().st_size
    walltime_seconds = time.perf_counter() - wall_start
    report = {
        "schema": SCHEMA,
        "not_scientific": True,
        "simulator_calls": 0,
        "rows": rows,
        "outputbytes": actual_outputbytes,
        "sha_expected": sha_expected,
        "sha_actual": sha_actual,
        "equal": sha_expected == sha_actual and actual_outputbytes == outputbytes,
        "walltime_seconds": walltime_seconds,
        "peak_rss": _peak_rss(),
        "source_driver_sha256": _source_driver_sha256(),
        "probe_sha256": _probe_sha256(),
    }
    _write_report(out_dir / "report.json", report)
    if not report["equal"]:
        raise ProbeError("published fixture hash mismatch")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="new output directory (must not already exist)")
    parser.add_argument("--rows", default=DEFAULT_ROWS, type=int,
                        help=f"fixed repeated rows, {MIN_ROWS}..{MAX_ROWS} "
                             f"(default: {DEFAULT_ROWS})")
    args = parser.parse_args(argv)
    try:
        report = run_probe(args.out_dir, rows=args.rows)
    except (ProbeError, t1_tasks.TaskError, OSError, ValueError) as exc:
        print(f"PUBLICATION PROBE FAILED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "verified", "report": str(args.out_dir / "report.json"),
                      "rows": report["rows"], "outputbytes": report["outputbytes"],
                      "equal": report["equal"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
