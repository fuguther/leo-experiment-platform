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
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            def emit(payload) -> None:
                line = _line(payload)
                digest.update(line.encode("utf-8"))
                digest.update(b"\n")
                stream.write(line)
                stream.write("\n")

            for arm_row in document["document"]["arms"]:
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
                    for row in rows:
                        emit({"schema": SCHEMA, "kind": "row", "arm": arm,
                              "stream": name, "row": row})
                    per_arm[name] = len(rows)
                for name in MAPPINGS:
                    value = replay.get(name)
                    if isinstance(value, dict):
                        emit({"schema": SCHEMA, "kind": "mapping", "arm": arm,
                              "mapping": name, "value": value})
                        per_arm[name] = len(value)
                emit({"schema": SCHEMA, "kind": "arm_end", "arm": arm,
                      "counts": per_arm})
                counts[arm] = per_arm
                arm_row["replay"] = {
                    "captured": True,
                    "arm": arm,
                    "sidecar": target.name,
                    "streams": {key: per_arm[key] for key in STREAMS
                                if key in per_arm},
                    "mappings": {key: per_arm[key] for key in MAPPINGS
                                 if key in per_arm},
                }
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
    return {"path": target.name, "sha256": sha256, "counts": counts}


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


def stream_facts(path, arm, *, queue_milestone="queue_state") -> dict:
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
    for line in Path(path).open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        if payload.get("arm") != arm:
            continue
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


def verify_reference(reference, result_path, *, expected_arm=None) -> list:
    """Stream-verify one arm sidecar reference without materializing it."""
    issues: list = []
    target = Path(result_path).with_name(str(reference.get("sidecar") or ""))
    if reference.get("captured") is not True:
        issues.append("captured is not true")
    if expected_arm is not None and reference.get("arm") != expected_arm:
        issues.append("replay arm differs from network row")
    if not target.is_file():
        issues.append("sidecar is missing: " + target.name)
        return issues
    if reference.get("sha256") != file_sha256(target):
        issues.append("sidecar sha256 differs from the recorded digest")
    declared = reference.get("streams") or {}
    seen: dict = {}
    mapping_seen: dict = {}
    for line in target.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        kind = payload.get("kind")
        if kind == "row":
            key = payload.get("stream")
            seen[key] = seen.get(key, 0) + 1
        elif kind == "mapping":
            mapping_seen[payload.get("mapping")] = mapping_seen.get(
                payload.get("mapping"), 0) + 1
    for name, count in declared.items():
        if seen.get(name, 0) != count:
            issues.append("sidecar %s rows differ from declared count" % name)
    for name, count in (reference.get("mappings") or {}).items():
        if mapping_seen.get(name, 0) != 1:
            issues.append("sidecar %s mapping is not recorded exactly once" % name)
    return issues
