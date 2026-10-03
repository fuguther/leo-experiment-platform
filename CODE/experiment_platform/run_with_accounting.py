"""Diagnostic entrypoint: run a compare CLI and account for its real cost.

Why this exists
---------------
The release runner manifest records only cpu_count, so for three rounds peak
memory and the number of kernel invocations were reported as not measured.
This wrapper measures both on the process that actually produces the result:

  peak RSS  the Linux kernel high-water mark of THIS process
            (/proc/self/status VmHWM, reported in bytes), cross-checked
            against resource.getrusage(RUSAGE_SELF).ru_maxrss, plus
            RUSAGE_CHILDREN to show whether a child contributed.  VmHWM is
            maintained by the kernel and is therefore not a sample that can
            miss a spike between two reads.
  calls     every CODE.leo_sim.kernel.run_simulation invocation in order,
            with its wall time and a light argument summary.

It changes no scientific input: the wrapped driver receives exactly the argv
it would otherwise have received.
"""
from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

#: resource.getrusage().ru_maxrss is in bytes on Darwin and kilobytes on
#: Linux.  Reporting the raw number as "kb" would mislabel half the
#: platforms, so the unit travels with the value.
_RU_MAXRSS_UNIT = "bytes" if sys.platform == "darwin" else "kilobytes"


def _ru_maxrss_bytes(usage):
    value = int(usage.ru_maxrss)
    return value if _RU_MAXRSS_UNIT == "bytes" else value * 1024

from CODE.experiment_platform import time_alignment_compare as compare
from CODE.leo_sim import kernel

SCHEMA = "t1-run-accounting/v1"


def _vm_hwm_bytes():
    """Linux high-water mark for this process, in bytes (None off Linux)."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _summarise(args, kwargs):
    rows = args[1] if len(args) > 1 else kwargs.get("rows")
    return {
        "rows": len(rows) if isinstance(rows, (list, tuple)) else None,
        "geometry": ("scripted" if kwargs.get("geometry") is not None
                     else "configured"),
        "decision_sink": kwargs.get("decision_sink") is not None,
        "timeline_sink": kwargs.get("timeline_sink") is not None,
    }


def _write(path, document):
    parent = path.parent
    handle, temporary = tempfile.mkstemp(prefix="." + path.name + ".",
                                         suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, sort_keys=True,
                      indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run a compare driver with peak-RSS and kernel-call "
                    "accounting")
    parser.add_argument("--accounting-out", type=Path, required=True)
    parser.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    rest = list(args.rest)
    if rest and rest[0] == "--":
        rest = rest[1:]
    if not rest:
        parser.error("no wrapped command given; use -- <compare argv>")

    calls = []
    original = kernel.run_simulation
    started = time.time()

    def traced(*positional, **keyword):
        record = {"index": len(calls) + 1,
                  "started_at_s": round(time.time() - started, 6),
                  **_summarise(positional, keyword)}
        calls.append(record)
        begin = time.perf_counter()
        try:
            return original(*positional, **keyword)
        finally:
            record["wall_s"] = round(time.perf_counter() - begin, 6)

    kernel.run_simulation = traced
    status = 1
    try:
        status = int(compare.main(rest) or 0)
    finally:
        kernel.run_simulation = original
        out_path = None
        for index, token in enumerate(rest):
            if token == "--out" and index + 1 < len(rest):
                out_path = Path(rest[index + 1])
        self_usage = resource.getrusage(resource.RUSAGE_SELF)
        child_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        _write(args.accounting_out, {
            "schema": SCHEMA,
            "wall_s": round(time.time() - started, 6),
            "exit_status": status,
            "peak_rss": {
                "unit": "bytes",
                "scope": "this result process only; the simulation is "
                         "single-process",
                "method": "Linux kernel high-water mark "
                          "/proc/self/status VmHWM, read once at exit "
                          "(not a sample)",
                "vm_hwm_bytes": _vm_hwm_bytes(),
                "rusage_raw_unit": _RU_MAXRSS_UNIT,
                "rusage_self_maxrss_raw": int(self_usage.ru_maxrss),
                "rusage_self_maxrss_bytes": _ru_maxrss_bytes(self_usage),
                "rusage_children_maxrss_raw": int(child_usage.ru_maxrss),
                "rusage_children_maxrss_bytes": _ru_maxrss_bytes(
                    child_usage),
            },
            "kernel_calls": {
                "count": len(calls),
                "unit": "one CODE.leo_sim.kernel.run_simulation invocation",
                "calls": calls,
            },
            "output_bytes": (out_path.stat().st_size
                             if out_path is not None and out_path.exists()
                             else None),
        })
    return status


if __name__ == "__main__":
    raise SystemExit(main())