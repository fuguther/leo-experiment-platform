"""Audit-only queue ledger: exact per-resource queue level at every mutation.

WHY THIS EXISTS
---------------
`queue_area_bits_s` is a stage-level aggregate.  It answers "how much queueing
did the network do" but it cannot be decomposed into "what was link (sat,peer)'s
backlog at instant t".  Every offline question that needs the truth of a queue
at a chosen instant -- most importantly "what would candidate a's egress
backlog be when my packet arrives" -- is therefore unanswerable from the run
artifact, even though the simulator computes that integral from exactly the
mutations this ledger records.

WHAT IS RECORDED
----------------
Three separate files, never merged into the run artifact namespace:

  <prefix>-queue-levels.jsonl.gz    one row per queue mutation (POST-transition
                                    level), so q(t) is piecewise constant and
                                    exact between rows.
  <prefix>-packet-events.jsonl.gz   the kernel's ``packet_events`` list verbatim.
  <prefix>-service-windows.jsonl.gz the kernel's ``link_service_windows``
                                    verbatim.

Level row:  {"kind":"level","t":<float>,"queue":"<qid>","bits":<int>,
             "event":"add"|"remove"|"close"}
Header row: {"kind":"header","schema":<SCHEMA>,"columns":[...],"queues":[...]}
Trailer:    {"kind":"trailer","rows":<n>,"queues":[...],"closed_at":<float>}

The level rows are sufficient to recompute the integral independently
(trapezoid over a piecewise-constant series), which is what makes the native
``queue_area_bits_s`` verifiable rather than merely trusted.

CONTRACT (deliberately narrow, same shape as ``resource_journal``)
-----------------------------------------------------------------
1. OPT-IN.  With the switch off nothing is constructed and every existing
   artifact field is unchanged, byte for byte.
2. WRITE-ONLY.  Routing, learning, metrics, the deadline objective, the
   predictor and the target probe never read it.
3. NO RNG.  It calls no distribution and adds no SimPy event; the business RNG
   stream and the event schedule are therefore untouched.
4. NO RESOURCE.  It does not request, release or hold a link, a compute slot,
   a query server or a queue entry.
5. DETERMINISTIC BYTES.  gzip headers are written with ``mtime=0`` and rows
   follow mutation order, so the same run produces the same ledger bytes.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os

SCHEMA = "leo-sim-queue-ledger/v1"

_LEVEL_COLUMNS = ("t", "queue", "bits", "event")
_EVENT_COLUMNS = ("kind", "pid", "at", "queue", "link_id", "queue_id",
                  "stage", "bits", "rate_bps", "delay_s", "prop_id",
                  "endpoint", "satellite")
_WINDOW_COLUMNS = ("pid", "stage", "link_id", "start", "end", "rate_bps",
                   "capacity_bits", "served_bits", "bits", "outcome")


def _open_gz(path: str):
    """Open a deterministic gzip stream (mtime=0, no filename in header)."""
    raw = open(path, "wb")
    stream = gzip.GzipFile(filename="", mode="wb", compresslevel=6,
                           fileobj=raw, mtime=0)
    return raw, stream


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class QueueLedger:
    """Opt-in writer for the three ledger files."""

    def __init__(self, directory, prefix):
        self.dir = os.path.abspath(str(directory))
        self.prefix = str(prefix)
        os.makedirs(self.dir, exist_ok=True)
        self.paths = {
            "levels": os.path.join(self.dir, f"{self.prefix}-queue-levels.jsonl.gz"),
            "packet_events": os.path.join(self.dir, f"{self.prefix}-packet-events.jsonl.gz"),
            "service_windows": os.path.join(self.dir, f"{self.prefix}-service-windows.jsonl.gz"),
        }
        self.rows = 0
        self._queues: set[str] = set()
        self._closed = False
        self._raw, self._stream = _open_gz(self.paths["levels"])
        header = {"kind": "header", "schema": SCHEMA,
                  "columns": list(_LEVEL_COLUMNS),
                  "note": "post-transition queue level, one row per mutation"}
        self._write(header)

    # -- level stream ---------------------------------------------------
    def _write(self, row: dict) -> None:
        self._stream.write((json.dumps(row, sort_keys=True,
                                       separators=(",", ":")) + "\n").encode())

    def level(self, t: float, queue: str, bits: int, event: str) -> None:
        """Record one post-transition queue level.  Hot path: no json module."""
        self._stream.write(
            ('{"bits":%d,"event":"%s","kind":"level","queue":"%s","t":%.17g}\n'
             % (int(bits), event, queue, float(t))).encode())
        self.rows += 1
        if queue not in self._queues:
            self._queues.add(queue)

    # -- verbatim exports -----------------------------------------------
    def export_packet_events(self, rows) -> int:
        raw, stream = _open_gz(self.paths["packet_events"])
        count = 0
        try:
            for row in rows:
                stream.write((json.dumps(row, sort_keys=True,
                                         separators=(",", ":")) + "\n").encode())
                count += 1
        finally:
            stream.close()
            raw.close()
        return count

    def export_service_windows(self, rows) -> int:
        raw, stream = _open_gz(self.paths["service_windows"])
        count = 0
        try:
            for row in rows:
                stream.write((json.dumps(row, sort_keys=True,
                                         separators=(",", ":")) + "\n").encode())
                count += 1
        finally:
            stream.close()
            raw.close()
        return count

    # -- lifecycle ------------------------------------------------------
    def close(self, closed_at: float = 0.0, *, packet_events=0,
              service_windows=0) -> dict:
        if self._closed:
            return self.summary()
        self._write({"kind": "trailer", "rows": self.rows,
                     "queues": sorted(self._queues),
                     "closed_at": float(closed_at)})
        self._stream.close()
        self._raw.close()
        self._closed = True
        self._rows_by_file = {"packet_events": int(packet_events),
                              "service_windows": int(service_windows)}
        return self.summary()

    def summary(self) -> dict:
        counts = getattr(self, "_rows_by_file", {})
        files = {}
        for name, path in self.paths.items():
            exists = os.path.exists(path)
            files[name] = {
                "path": path,
                "bytes": os.path.getsize(path) if exists else None,
                "sha256": _sha256(path) if exists else None,
                "rows": (self.rows if name == "levels"
                         else counts.get(name)),
            }
        return {
            "schema": SCHEMA,
            "prefix": self.prefix,
            "queue_count": len(self._queues),
            "queues": sorted(self._queues),
            "files": files,
        }
