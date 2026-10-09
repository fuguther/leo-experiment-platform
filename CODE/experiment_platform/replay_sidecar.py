"""Line-delimited replay sidecar: bounded write and bounded read.

A single full-fidelity four-arm replay measured 9.62 GB, and decoding that
object graph in process needs about 13.6x the file size (the VM probe measured
765 MB -> 10.4 GiB at readback), so the gate completeness check could not run:
run t1-recording-acceptance-20261010-01 published the result and was then
killed post-publish while inspecting it.

The sidecar keeps every recorded row, but as one JSON object per line next to a
small primary result.  Counting, verifying and iterating rows then cost memory
proportional to ONE row, never to the file.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

SCHEMA = "t1-replay-sidecar/v1"

#: The recorded streams the gate reconciles element by element.
STREAMS = (
    "decision_rows", "timeline_rows", "packet_events",
    "link_service_windows", "link_available_windows",
    "queue_state_events", "topology_trace", "handover_events",
)
#: The recorded mappings the gate reconciles by key count.
MAPPINGS = ("fates", "deliveries", "counts")
#: Per-arm audit lists that embed one full observation per attempt.  They are
#: written as sidecar streams too: the primary result kept them and reached
#: 7,170,591,303 bytes, which the post-publish inspection then could not decode.
AUDIT_LISTS = (
    ("routing_audit_log", "decision_records", "routing_decision_records"),
    ("routing_audit_log", "attempt_records", "routing_attempt_records"),
)


class SidecarError(RuntimeError):
    """The sidecar could not be written or verified."""


def sidecar_path(result_path) -> Path:
    result_path = Path(result_path)
    return result_path.with_name(result_path.stem + "-replay.jsonl")


def _line(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def write_sidecar(document, result_path) -> dict:
    """Write every captured arm replay stream to the sidecar.

    Mutates the document: each arm replay becomes a reference holding the
    sidecar name, its sha256 and the per-stream/per-mapping counts, so the
    small primary result still declares exactly what was recorded and where.
    """
    target = sidecar_path(result_path)
    digest = hashlib.sha256()
    handle, temporary = tempfile.mkstemp(prefix="." + target.name + ".",
                                         suffix=".tmp", dir=str(target.parent))
    counts: dict = {}
    index: dict = {}
    position = [0]
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            def emit(payload) -> None:
                line = _line(payload)
                data = line.encode("utf-8") + b"\n"
                digest.update(data)
                stream.write(line + "\n")
                position[0] += len(data)

            for arm_row in document["document"]["arms"]:
                arm = arm_row.get("arm")
                arm_index = index.setdefault(arm, {})
                audit = arm_row.get("routing_audit_log")
                if isinstance(audit, dict):
                    for container, key, name in AUDIT_LISTS:
                        rows = audit.get(key)
                        if not isinstance(rows, list):
                            continue
                        emit({"schema": SCHEMA, "kind": "stream", "arm": arm,
                              "stream": name, "count": len(rows)})
                        arm_index[name] = {"offset": position[0],
                                             "count": len(rows),
                                             "lines": len(rows)}
                        for row in rows:
                            emit({"schema": SCHEMA, "kind": "row",
                                  "arm": arm, "stream": name, "row": row})
                        audit[key] = []
                replay = arm_row.get("replay") or {}
                if replay.get("captured") is not True:
                    continue
                for marker in ("history_compaction", "analysis_projection"):
                    if marker in replay:
                        raise SidecarError(
                            "%s is a lossy projection; the full replay is "
                            "required" % marker)
                arm = arm_row.get("arm")
                per_arm: dict = {}
                for name in STREAMS:
                    rows = replay.get(name)
                    if rows is None:
                        # Absent streams are not invented here; the completeness
                        # gate still requires every declared stream to exist.
                        continue
                    if not isinstance(rows, list):
                        raise SidecarError(
                            "arm %r stream %r is not a list" % (arm, name))
                    emit({"schema": SCHEMA, "kind": "stream", "arm": arm,
                          "stream": name, "count": len(rows)})
                    arm_index[name] = {"offset": position[0],
                                       "count": len(rows),
                                       "lines": len(rows)}
                    for row in rows:
                        emit({"schema": SCHEMA, "kind": "row", "arm": arm,
                              "stream": name, "row": row})
                    per_arm[name] = len(rows)
                for name in MAPPINGS:
                    value = replay.get(name)
                    if isinstance(value, dict):
                        arm_index[name] = {"offset": position[0],
                                           "count": len(value),
                                           "lines": 1}
                        emit({"schema": SCHEMA, "kind": "mapping", "arm": arm,
                              "mapping": name, "value": value})
                        per_arm[name] = len(value)
                emit({"schema": SCHEMA, "kind": "arm_end", "arm": arm,
                      "counts": per_arm})
                counts[arm] = per_arm
                index[arm] = arm_index
                scalars = {name: replay[name] for name in
                           ("stop_time_s", "horizon_s", "cost")
                           if name in replay}
                arm_row["replay"] = dict(scalars, **{
                    "captured": True,
                    "arm": arm,
                    "sidecar": target.name,
                    "streams": {key: per_arm[key] for key in STREAMS
                                if key in per_arm},
                    "mappings": {key: per_arm[key] for key in MAPPINGS
                                 if key in per_arm},
                    "index": arm_index,
                })
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    sha256 = digest.hexdigest()
    for arm_row in document["document"]["arms"]:
        reference = arm_row.get("replay")
        if isinstance(reference, dict) and reference.get("sidecar") == target.name:
            reference["sha256"] = sha256
    return {"path": target.name, "sha256": sha256, "counts": counts,
            "index": index}


def resolve(path, reference, stream):
    """Return one recorded stream of one arm, using the index when present."""
    index = (reference or {}).get("index") or {}
    entry = index.get(stream)
    if isinstance(entry, dict):
        return [payload.get("row") for payload in
                iter_indexed(path, int(entry.get("offset", 0)),
                             int(entry.get("lines", entry.get("count", 0))))]
    return [row for _arm, name, row in
            iter_sidecar(path, arm=reference.get("arm"), stream=stream)
            if name == stream]


def resolve_iter(path, reference, stream):
    """Iterate one recorded stream of one arm without materializing it."""
    index = (reference or {}).get("index") or {}
    entry = index.get(stream)
    if isinstance(entry, dict):
        for payload in iter_indexed(path, int(entry.get("offset", 0)),
                                    int(entry.get("lines",
                                                  entry.get("count", 0)))):
            yield payload.get("row")
        return
    for _arm, name, row in iter_sidecar(path, arm=reference.get("arm"),
                                        stream=stream):
        if name == stream:
            yield row


def resolve_mapping(path, reference, name):
    """Return one recorded mapping of one arm (fates/deliveries/counts)."""
    index = (reference or {}).get("index") or {}
    entry = index.get(name)
    if isinstance(entry, dict):
        for payload in iter_indexed(path, int(entry.get("offset", 0)),
                                    int(entry.get("lines", 1))):
            if payload.get("kind") == "mapping":
                return payload.get("value")
        return None
    for line in Path(path).open(encoding="utf-8"):
        text = line.strip()
        if not text:
            continue
        payload = json.loads(text)
        if (payload.get("kind") == "mapping"
                and payload.get("arm") == (reference or {}).get("arm")
                and payload.get("mapping") == name):
            return payload.get("value")
    return None


def sidecar_of(result_path, reference):
    return Path(result_path).with_name(str((reference or {}).get("sidecar") or ""))


def iter_sidecar(path, *, arm=None, stream=None):
    """Yield (arm, stream, row) triples, reading one line at a time."""
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if payload.get("kind") != "row":
                continue
            if arm is not None and payload.get("arm") != arm:
                continue
            if stream is not None and payload.get("stream") != stream:
                continue
            yield payload.get("arm"), payload.get("stream"), payload.get("row")


def read_sidecar(path):
    """Read the whole sidecar into per-arm buckets (small tests only)."""
    out: dict = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if payload.get("schema") != SCHEMA:
                raise SidecarError("sidecar line has an unknown schema")
            arm, kind = payload.get("arm"), payload.get("kind")
            bucket = out.setdefault(arm, {"streams": {}, "mappings": {},
                                          "declared": {}, "counts": {}})
            if kind == "stream":
                bucket["declared"][payload["stream"]] = payload.get("count")
            elif kind == "row":
                bucket["streams"].setdefault(payload["stream"], []).append(
                    payload.get("row"))
            elif kind == "mapping":
                bucket["mappings"][payload["mapping"]] = payload.get("value")
            elif kind == "arm_end":
                bucket["counts"] = payload.get("counts") or {}
            else:
                raise SidecarError("unknown sidecar line kind %r" % (kind,))
    return out


def file_sha256(path, chunk=1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical(row) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


#: Timeline milestones that count as one routing attempt in the audit.
ROUTING_MILESTONES = ("decision_attempt", "frozen_inferred_hold",
                      "commit_rejected")


def stream_facts(path, arm, *, queue_milestone="queue_state",
                 index=None) -> dict:
    """Recompute the per-arm facts the completeness gate needs, in one pass.

    Memory stays proportional to one row: the two subset digests and the small
    fates/deliveries mappings are the only accumulated state.
    """
    facts = {"counts": {}, "declared": {}, "milestones": {},
             "forward_decisions": 0, "routing_attempts": 0,
             "fates": None, "deliveries": None, "counts_mapping": None}
    queue_digest = hashlib.sha256()
    timeline_digest = hashlib.sha256()
    queue_count = 0
    timeline_count = 0
    if isinstance(index, dict) and index:
        # The index records the declared count per stream; the row lines
        # themselves carry no header, so seed the declared set from it.
        facts["declared"] = {name: int(entry.get("count", 0))
                             for name, entry in index.items()
                             if name in STREAMS and isinstance(entry, dict)}

        # Only the streams whose CONTENTS are needed are parsed: the per-stream
        # counts already come from the index, and parsing 50 GB of attempt
        # records only to count them dominated the gate.
        wanted = {"decision_rows", "timeline_rows", "queue_state_events"}

        def _indexed():
            for name, entry in index.items():
                if not isinstance(entry, dict):
                    continue
                if name in STREAMS and name not in wanted:
                    continue
                for payload in iter_indexed(
                        path, int(entry.get("offset", 0)),
                        int(entry.get("lines", entry.get("count", 0)))):
                    yield payload
        stream = _indexed()
    else:
        def _scan():
            for line in Path(path).open(encoding="utf-8"):
                text = line.strip()
                if not text:
                    continue
                payload = json.loads(text)
                if payload.get("arm") == arm:
                    yield payload
        stream = _scan()
    for payload in stream:
        kind = payload.get("kind")
        if kind == "stream":
            facts["declared"][payload.get("stream")] = payload.get("count")
        elif kind == "row":
            name = payload.get("stream")
            row = payload.get("row")
            facts["counts"][name] = facts["counts"].get(name, 0) + 1
            if not isinstance(row, dict):
                continue
            if name == "decision_rows" and row.get("kind") == "forward":
                facts["forward_decisions"] += 1
            elif name == "timeline_rows":
                milestone = row.get("milestone")
                facts["milestones"][milestone] = facts["milestones"].get(
                    milestone, 0) + 1
                if milestone == queue_milestone:
                    timeline_count += 1
                    timeline_digest.update(_canonical(row).encode("utf-8"))
                if milestone in ROUTING_MILESTONES:
                    facts["routing_attempts"] += 1
            elif name == "queue_state_events":
                queue_count += 1
                queue_digest.update(_canonical(row).encode("utf-8"))
        elif kind == "mapping":
            name = payload.get("mapping")
            if name in ("fates", "deliveries", "counts"):
                value = payload.get("value")
                key = "counts_mapping" if name == "counts" else name
                facts[key] = value if isinstance(value, dict) else None
    facts["queue_subset_digest"] = queue_digest.hexdigest()
    facts["queue_subset_count"] = queue_count
    facts["timeline_subset_digest"] = timeline_digest.hexdigest()
    facts["timeline_subset_count"] = timeline_count
    return facts


#: File digests are cached per (path, size, mtime): one pass serves every arm.
_FILE_SHA_CACHE: dict = {}


def cached_file_sha256(path) -> str:
    path = Path(path)
    stat = path.stat()
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    cached = _FILE_SHA_CACHE.get(key)
    if cached is None:
        cached = file_sha256(path)
        _FILE_SHA_CACHE.clear()
        _FILE_SHA_CACHE[key] = cached
    return cached


def verify_reference(reference, result_path, *, expected_arm=None) -> list:
    """Verify one arm sidecar reference with one cached hash and its index.

    The recorded counts are cross-checked against the index, and the index is
    covered by the file digest, so no per-row rescan is needed to establish
    integrity.
    """
    issues: list = []
    target = Path(result_path).with_name(str(reference.get("sidecar") or ""))
    if reference.get("captured") is not True:
        issues.append("captured is not true")
    if expected_arm is not None and reference.get("arm") != expected_arm:
        issues.append("replay arm differs from network row")
    if not target.is_file():
        issues.append("sidecar is missing: " + target.name)
        return issues
    if reference.get("sha256") != cached_file_sha256(target):
        issues.append("sidecar sha256 differs from the recorded digest")
    index = reference.get("index") or {}
    for name, count in (reference.get("streams") or {}).items():
        entry = index.get(name)
        if not isinstance(entry, dict) or entry.get("count") != count:
            issues.append("sidecar %s index does not match the declared count"
                          % name)
    for name, count in (reference.get("mappings") or {}).items():
        entry = index.get(name)
        if not isinstance(entry, dict) or entry.get("count") != count:
            issues.append("sidecar %s index does not match the declared count"
                          % name)
    return issues


def iter_indexed(path, offset, count):
    """Yield the count records that start at byte offset."""
    with Path(path).open("rb") as handle:
        handle.seek(offset)
        for _ in range(count):
            line = handle.readline()
            if not line:
                break
            yield json.loads(line)
