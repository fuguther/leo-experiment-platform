"""Bounded pure-JSON publication probe for the production T1 writer."""
from __future__ import annotations

import argparse
import gc
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
HISTORY_ARM_COUNT = 4
DEFAULT_HISTORY_RECEIVERS = 8
DEFAULT_HISTORY_ORIGINS = 12
DEFAULT_HISTORY_LENGTH = 64
DEFAULT_HISTORY_DOWNLINK = 99
DEFAULT_HISTORY_QUERIES = 20_000
MAX_HISTORY_RECEIVERS = 96
MAX_HISTORY_ORIGINS = 96
MAX_HISTORY_LENGTH = 64
MAX_HISTORY_DOWNLINK = 99
MAX_HISTORY_QUERIES = 20_000
MAX_HISTORY_RESOURCE_RECORDS = 3_000_000
HISTORY_ARRIVAL_EVENTS = 2
HISTORY_TOPOLOGY_REMATCHES = 1
HISTORY_INVALIDATION_QUERIES = 4


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


def run_graph_probe(out_dir, references=500000):
    """Exercise the production network codec with a large shared replay graph.

    Expanded size is calculated from standard JSON on the fixture row, not
    estimated from a partial failed file. This is engineering evidence only.
    """
    from CODE.experiment_platform import replay_codec, t1_suite
    if not 1 <= references <= 500000 or isinstance(references, bool):
        raise ProbeError("graph references must be in 1..500000")
    out_dir = Path(out_dir)
    if out_dir.exists() or not out_dir.parent.is_dir():
        raise ProbeError("graph probe needs a new output directory")
    row = _fixture_row()
    document = {"schema": t1_tasks.SCHEMA_TASK, "task": "network_alignment",
                "document": {"fixture_rows": [row] * references}}
    expected_row = json.loads(json.dumps(row, ensure_ascii=False, allow_nan=False))
    row_bytes = len(json.dumps(row, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode())
    empty = {"schema": t1_tasks.SCHEMA_TASK, "task": "network_alignment",
             "document": {"fixture_rows": []}}
    expanded_bytes = len(json.dumps(empty, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")).encode()) + 1
    expanded_bytes += references * row_bytes + max(0, references - 1)
    out_dir.mkdir()
    path = out_dir / "fixture.json"
    started = time.perf_counter()
    t1_tasks.publish(document, path)
    published = time.perf_counter()
    probe = t1_suite._inspect_result(path)
    decoded = probe.get("payload") or {}
    records = (decoded.get("document") or {}).get("fixture_rows", [])
    equal = (probe["parse_error"] is None and len(records) == references
             and records[0] == expected_row
             and all(record is records[0] for record in records)
             and decoded.get("schema") == t1_tasks.SCHEMA_TASK
             and decoded.get("task") == "network_alignment")
    report = {"schema": "engineering-graph-publication-probe/v1",
        "not_scientific": True, "simulator_calls": 0, "references": references,
        "expanded_standard_json_bytes": expanded_bytes, "outputbytes": path.stat().st_size,
        "publish_s": published - started, "read_verify_s": time.perf_counter() - published,
        "equal": equal, "encoded_file_sha256": probe["sha256"], "peak_rss": _peak_rss(),
        "source_driver_sha256": _source_driver_sha256(),
        "codec_sha256": hashlib.sha256(Path(replay_codec.__file__).read_bytes()).hexdigest(),
        "scope": "shared-reference fixture; actual scientific replay still requires a complete run"}
    _write_report(out_dir / "report.json", report)
    if not equal:
        raise ProbeError("lossless production graph round trip failed")
    return report


def _validate_history_shape(receivers, origins, history, downlink, queries):
    bounds = (
        ("receivers", receivers, 1, MAX_HISTORY_RECEIVERS),
        ("origins", origins, 1, MAX_HISTORY_ORIGINS),
        ("history", history, 1, MAX_HISTORY_LENGTH),
        ("downlink", downlink, 1, MAX_HISTORY_DOWNLINK),
        ("queries", queries, HISTORY_INVALIDATION_QUERIES,
         MAX_HISTORY_QUERIES),
    )
    for name, value, lower, upper in bounds:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ProbeError(f"history {name} must be an integer")
        if not lower <= value <= upper:
            raise ProbeError(
                f"history {name} must be between {lower} and {upper}")
    resource_records = (HISTORY_ARM_COUNT * receivers * origins
                        * (history + HISTORY_ARRIVAL_EVENTS) * downlink)
    if resource_records > MAX_HISTORY_RESOURCE_RECORDS:
        raise ProbeError(
            "history fixture exceeds the fixed resource-record limit "
            f"({resource_records} > {MAX_HISTORY_RESOURCE_RECORDS})")
    return receivers, origins, history, downlink, queries


def _history_fixture_payload(arm, receiver, origin, sequence, peer, downlink):
    """Build a distinct payload with fixed-width advertised resources."""
    base = (((arm * 10_000 + receiver * 100 + origin) * 1000) + sequence) * 100
    resources = {}
    for cell_index in range(downlink):
        cell = f"cell-{cell_index:03d}"
        resources[cell] = {
            "queue_bits": base + cell_index,
            "rate_bps": 1_000_000.0 + base + cell_index,
            "propagation_s": 0.001 + cell_index / 1_000_000.0,
            "available": (sequence + cell_index) % 2 == 0,
            "queue_scope": "cell",
            "queue_semantics": "queued_data_bits",
        }
    return {
        "isl_queue_bits": {
            "N": {
                "peer": peer,
                "value": base,
                "generation": sequence,
                "rate_bps": 2_000_000.0 + base,
                "work_ahead_bits_proxy": float(base + 7),
            },
        },
        "processing_resources": {
            "queue_bits": base + 11,
            "rate_bps": 3_000_000.0 + base,
            "available": True,
        },
        "downlink_resources": resources,
    }


def _history_digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _history_state_key(kernel, receiver, origin, now):
    entries = tuple(
        entry for entry in kernel.caches[receiver].history_for(origin)
        if entry.received_at <= now
    )
    topology = tuple(sorted(kernel.topo.get(origin, {}).items()))
    return tuple(id(entry) for entry in entries), topology


def _validated_record_query(kernel, receiver, origin, now, validated_slots,
                            counters):
    """Check one recorded query against the old full conversion on cache miss."""
    from time import perf_counter

    slot = (receiver, origin)
    state_key = _history_state_key(kernel, receiver, origin, now)
    previous = validated_slots.get(slot)
    if previous is None or previous[0] != state_key:
        started = perf_counter()
        reference = kernel._advertisement_history(
            receiver, origin, now, force=True)
        reference_digest = _history_digest(reference)
        counters["reference_validation_s"] += perf_counter() - started
        del reference

        recorded_started = perf_counter()
        actual = kernel._recorded_advertisement_history(receiver, origin, now)
        counters["recorded_path_s"] += perf_counter() - recorded_started
        actual_digest = _history_digest(actual)
        if actual_digest != reference_digest:
            raise ProbeError(
                f"recorded history differs from force=True reference at {slot}")
        counters["reference_states"] += 1
        validated_slots[slot] = (
            state_key, reference_digest, tuple(actual), actual)
        return actual, reference_digest, True

    recorded_started = perf_counter()
    actual = kernel._recorded_advertisement_history(receiver, origin, now)
    counters["recorded_path_s"] += perf_counter() - recorded_started
    _old_key, reference_digest, prior_rows, prior_list = previous
    if actual is prior_list:
        raise ProbeError("recorded history reused the caller-owned list wrapper")
    if len(actual) != len(prior_rows) or any(
            row is not prior_rows[index] for index, row in enumerate(actual)):
        raise ProbeError("cache hit did not reuse the validated history samples")
    validated_slots[slot] = (
        state_key, reference_digest, prior_rows, actual)
    counters["cache_hit_queries"] += 1
    return actual, reference_digest, False


def _build_history_arm(arm_index, receivers, origins, history, downlink,
                       queries, counters):
    """Create one Kernel via __new__, exercise recording, and return its rows."""
    from CODE.leo_sim import control, kernel as kernel_module

    kernel = kernel_module.Kernel.__new__(kernel_module.Kernel)
    kernel.decision_sink = []
    kernel._recorded_advertisement_history_cache = {}
    kernel.caches = [control.LocalCache() for _ in range(receivers)]
    initial_peers = {
        origin: f"arm-{arm_index}-origin-{origin}-peer-initial"
        for origin in range(origins)
    }
    kernel.topo = {origin: {"N": peer}
                   for origin, peer in initial_peers.items()}

    total_entries = receivers * origins * history
    entry_ids = set()
    payload_ids = set()
    for receiver in range(receivers):
        for origin in range(origins):
            cache = kernel.caches[receiver]
            for sequence in range(history):
                payload = _history_fixture_payload(
                    arm_index, receiver, origin, sequence,
                    initial_peers[origin], downlink)
                generated_at = float(sequence)
                entry = control.CacheEntry(
                    origin, payload, generated_at, generated_at + 0.5,
                    1_000_000.0, hops=sequence % 8)
                cache.put(entry)
                entry_ids.add(id(entry))
                payload_ids.add(id(payload))
    if len(entry_ids) != total_entries or len(payload_ids) != total_entries:
        raise ProbeError("fixture did not create distinct entries and payloads")

    slots = [(receiver, origin)
             for receiver in range(receivers)
             for origin in range(origins)]
    event_slot = slots[0]
    event_receiver, event_origin = event_slot
    event_peer = initial_peers[event_origin]
    validated_slots = {}
    expected_event_digests = []
    query_rows = []

    # These first four calls exercise the same slot through its original state,
    # two actual CacheEntry arrivals, and one topology rematch. Remaining calls
    # are round-robin hits over the full receiver/origin shape.
    query_started = time.perf_counter()
    for query_index in range(queries):
        if query_index < HISTORY_INVALIDATION_QUERIES:
            receiver, origin = event_slot
            if query_index in (1, 2):
                sequence = history + query_index - 1
                generated_at = float(sequence)
                payload = _history_fixture_payload(
                    arm_index, receiver, origin, sequence, event_peer, downlink)
                entry = control.CacheEntry(
                    origin, payload, generated_at, generated_at + 0.5,
                    1_000_000.0, hops=sequence % 8)
                kernel.caches[receiver].put(entry)
            elif query_index == 3:
                kernel.topo[origin]["N"] = event_peer + "-rematched"
            now = float(history + 3.0) if query_index >= 3 else float(
                history + query_index + 1.0)
        else:
            receiver, origin = slots[
                (query_index - HISTORY_INVALIDATION_QUERIES) % len(slots)]
            now = float(history + HISTORY_ARRIVAL_EVENTS + 1.0)

        rows, digest, _was_reference_state = _validated_record_query(
            kernel, receiver, origin, now, validated_slots, counters)
        query_rows.append({
            "query_index": query_index,
            "receiver": receiver,
            "origin": origin,
            "samples": rows,
            "reference_sha256": digest,
        })
        if query_index < HISTORY_INVALIDATION_QUERIES:
            expected_event_digests.append(digest)
    counters["query_loop_s"] += time.perf_counter() - query_started

    if len(expected_event_digests) != HISTORY_INVALIDATION_QUERIES:
        raise ProbeError("invalidation sequence did not complete")
    if len(query_rows) != queries:
        raise ProbeError("benchmark query rows were dropped")
    invalidation = {
        "arrival_1_changed_digest": (
            expected_event_digests[1] != expected_event_digests[0]),
        "arrival_2_changed_digest": (
            expected_event_digests[2] != expected_event_digests[1]),
        "topology_rematch_changed_digest": (
            expected_event_digests[3] != expected_event_digests[2]),
    }
    if not all(invalidation.values()):
        raise ProbeError(f"cache invalidation did not change history: {invalidation}")

    final_now = float(history + HISTORY_ARRIVAL_EVENTS + 1.0)
    history_rows = []
    for receiver, origin in slots:
        samples, digest, _was_reference_state = _validated_record_query(
            kernel, receiver, origin, final_now, validated_slots, counters)
        history_rows.append({
            "receiver": receiver,
            "origin": origin,
            "samples": samples,
            "reference_sha256": digest,
        })

    counters["record_queries"] += queries + len(slots)
    counters["benchmark_record_queries"] += queries
    counters["materialization_record_queries"] += len(slots)
    counters["reference_digest_count"] += len(history_rows)
    # Drop the validation tuples and Kernel before the caller publishes. The
    # published rows retain only the data that belongs in the result graph.
    del validated_slots
    del kernel
    return {"arm": arm_index, "queries": query_rows,
            "final_histories": history_rows}, invalidation


def run_history_probe(out_dir, *, receivers=DEFAULT_HISTORY_RECEIVERS,
                      origins=DEFAULT_HISTORY_ORIGINS,
                      history=DEFAULT_HISTORY_LENGTH,
                      downlink=DEFAULT_HISTORY_DOWNLINK,
                      queries=DEFAULT_HISTORY_QUERIES):
    """Compare recorded histories to force=True and publish/read them losslessly.

    This uses synthetic CacheEntry objects and Kernel.__new__; it never
    constructs or runs the simulator. Default shape is a bounded engineering
    fixture (4 arms x 8 receiver labels x 12 origins x 64 initial entries x
    99 downlink resources, plus two arrival events), not a 96-satellite run.
    """
    from CODE.experiment_platform import replay_codec, t1_suite

    receivers, origins, history, downlink, queries = _validate_history_shape(
        receivers, origins, history, downlink, queries)
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise ProbeError(f"output directory already exists: {out_dir}")
    if not out_dir.parent.is_dir() or out_dir.parent.is_symlink():
        raise ProbeError(f"output parent must be a real directory: {out_dir.parent}")

    out_dir.mkdir()
    fixture_path = out_dir / "fixture.json"
    document = {
        "schema": t1_tasks.SCHEMA_TASK,
        "task": "network_alignment",
        "document": {
            "probe": "recorded-advertisement-history",
            "arms": [],
        },
    }
    counters = {
        "query_loop_s": 0.0,
        "recorded_path_s": 0.0,
        "reference_validation_s": 0.0,
        "reference_states": 0,
        "reference_digest_count": 0,
        "benchmark_record_queries": 0,
        "materialization_record_queries": 0,
        "record_queries": 0,
        "cache_hit_queries": 0,
    }
    invalidation_checks = {
        "arrival_1_changed_digest": True,
        "arrival_2_changed_digest": True,
        "topology_rematch_changed_digest": True,
    }
    build_started = time.perf_counter()
    for arm_index in range(HISTORY_ARM_COUNT):
        arm, arm_checks = _build_history_arm(
            arm_index, receivers, origins, history, downlink, queries, counters)
        document["document"]["arms"].append(arm)
        for key, passed in arm_checks.items():
            invalidation_checks[key] = invalidation_checks[key] and passed
    fixture_and_query_s = time.perf_counter() - build_started

    # The force=True reference lists were deleted immediately after their
    # digests were computed. Preserve every benchmark query's fresh outer list
    # and rows, including pre-arrival and pre-rematch states.
    expected_digest_by_slot = {}
    expected_digest_by_query = {}
    digest_rows = []
    expected_query_count = HISTORY_ARM_COUNT * queries
    for arm in document["document"]["arms"]:
        for history_row in arm["final_histories"]:
            key = (arm["arm"], history_row["receiver"], history_row["origin"])
            expected_digest_by_slot[key] = history_row["reference_sha256"]
            digest_rows.append([*key, history_row["reference_sha256"]])
        if len(arm["queries"]) != queries:
            raise ProbeError("arm query rows do not match the requested count")
        for query_row in arm["queries"]:
            key = (arm["arm"], query_row["query_index"])
            expected_digest_by_query[key] = query_row["reference_sha256"]
            digest_rows.append([*key, query_row["reference_sha256"]])
    if len(expected_digest_by_query) != expected_query_count:
        raise ProbeError("published query reference count is incomplete")

    peak_rss_before_publish = _peak_rss()
    current_rss_before_publish_kib = t1_tasks._current_rss_kib()
    publish_started = time.perf_counter()
    t1_tasks.publish(document, fixture_path)
    publish_s = time.perf_counter() - publish_started
    publish_bytes = fixture_path.stat().st_size
    peak_rss_after_publish = _peak_rss()
    current_rss_after_publish_kib = t1_tasks._current_rss_kib()

    # Avoid holding both the source fixture graph and decoded graph through
    # the production reader's parse/validation path. Loop variables also hold
    # the final arm and its last history lists until explicitly released.
    del document, arm, history_row, query_row
    gc.collect()
    current_rss_after_source_release_kib = t1_tasks._current_rss_kib()
    read_started = time.perf_counter()
    probe = t1_suite._inspect_result(fixture_path)
    decoded = probe.get("payload")
    if (probe.get("parse_error") is not None or not probe.get("exists")
            or decoded is None):
        raise ProbeError(
            f"production result inspection failed: {probe.get('parse_error')}")
    if (decoded.get("schema") != t1_tasks.SCHEMA_TASK
            or decoded.get("task") != "network_alignment"):
        raise ProbeError("production reader returned the wrong task schema")
    arms = (decoded.get("document") or {}).get("arms")
    expected_history_count = HISTORY_ARM_COUNT * receivers * origins
    if not isinstance(arms, list) or len(arms) != HISTORY_ARM_COUNT:
        raise ProbeError("production reader returned the wrong arm count")

    digest_memo = {}
    digest_memo_hits = 0
    observed_query_count = 0
    observed_final_history_count = 0
    equal = True
    for arm_index, arm in enumerate(arms):
        queries_read = arm.get("queries") if isinstance(arm, dict) else None
        histories = (arm.get("final_histories")
                     if isinstance(arm, dict) else None)
        if (not isinstance(arm, dict) or arm.get("arm") != arm_index
                or not isinstance(queries_read, list)
                or len(queries_read) != queries
                or not isinstance(histories, list)
                or len(histories) != receivers * origins):
            equal = False
            continue
        for query_index, query_row in enumerate(queries_read):
            if not isinstance(query_row, dict):
                equal = False
                continue
            expected_query_digest = expected_digest_by_query.get(
                (arm_index, query_index))
            samples = query_row.get("samples")
            if (query_row.get("query_index") != query_index
                    or expected_query_digest is None
                    or not isinstance(samples, list)):
                equal = False
                continue
            # Fresh lists on cache hits share their sample dictionaries. Memo
            # by sample identity tuple to avoid re-encoding identical histories.
            sample_identity = tuple(id(sample) for sample in samples)
            if sample_identity in digest_memo:
                actual_digest = digest_memo[sample_identity]
                digest_memo_hits += 1
            else:
                actual_digest = _history_digest(samples)
                digest_memo[sample_identity] = actual_digest
            observed_query_count += 1
            if (query_row.get("reference_sha256") != expected_query_digest
                    or actual_digest != expected_query_digest):
                equal = False
        for row in histories:
            if not isinstance(row, dict):
                equal = False
                continue
            key = (arm_index, row.get("receiver"), row.get("origin"))
            expected = expected_digest_by_slot.get(key)
            samples = row.get("samples")
            if expected is None or not isinstance(samples, list):
                equal = False
                continue
            expected_sample_count = (
                min(history + HISTORY_ARRIVAL_EVENTS, MAX_HISTORY_LENGTH)
                if row.get("receiver") == 0 and row.get("origin") == 0
                else history)
            if len(samples) != expected_sample_count:
                equal = False
            sample_identity = tuple(id(sample) for sample in samples)
            if sample_identity in digest_memo:
                actual_digest = digest_memo[sample_identity]
                digest_memo_hits += 1
            else:
                actual_digest = _history_digest(samples)
                digest_memo[sample_identity] = actual_digest
            observed_final_history_count += 1
            if row.get("reference_sha256") != expected \
                    or actual_digest != expected:
                equal = False

    if (observed_query_count != expected_query_count
            or observed_final_history_count != expected_history_count):
        equal = False
    digest_set_sha256 = _history_digest(sorted(
        digest_rows, key=lambda row: json.dumps(row, separators=(",", ":"))))
    readback_verify_s = time.perf_counter() - read_started
    peak_rss_after_readback = _peak_rss()
    current_rss_after_readback_kib = t1_tasks._current_rss_kib()
    report = {
        "schema": "engineering-history-publication-probe/v1",
        "not_scientific": True,
        "scope": "engineering fixture; not a 96-satellite simulation",
        "simulator_calls": 0,
        "arms": HISTORY_ARM_COUNT,
        "dimensions": {
            "receivers_per_arm": receivers,
            "origins_per_receiver": origins,
            "initial_history_per_origin": history,
            "downlink_resources_per_entry": downlink,
            "record_queries_per_arm": queries,
            "arrival_events_per_arm": HISTORY_ARRIVAL_EVENTS,
            "topology_rematches_per_arm": HISTORY_TOPOLOGY_REMATCHES,
        },
        "history_count": expected_history_count,
        "query_record_count": observed_query_count,
        "query_reference_digest_count": expected_query_count,
        "final_history_digest_count": observed_final_history_count,
        "reference_digest_count": observed_query_count + observed_final_history_count,
        "record_queries": counters["record_queries"],
        "benchmark_record_queries": counters["benchmark_record_queries"],
        "materialization_record_queries": counters["materialization_record_queries"],
        "cache_hit_queries": counters["cache_hit_queries"],
        "reference_validation_states": counters["reference_states"],
        "reference_digest_set_sha256": digest_set_sha256,
        "invalidation_checks": invalidation_checks,
        "fixture_and_query_s": fixture_and_query_s,
        "query_loop_s": counters["query_loop_s"],
        "recorded_path_s": counters["recorded_path_s"],
        "reference_validation_s": counters["reference_validation_s"],
        "publish_s": publish_s,
        "readback_verify_s": readback_verify_s,
        "outputbytes": publish_bytes,
        "encoded_file_sha256": probe.get("sha256"),
        "readback_digest_memo_hits": digest_memo_hits,
        "equal": equal and bool(probe.get("sha256")),
        "peak_rss": peak_rss_after_readback,
        "peak_rss_before_publish": peak_rss_before_publish,
        "peak_rss_after_publish": peak_rss_after_publish,
        "peak_rss_after_readback": peak_rss_after_readback,
        "current_rss_before_publish_kib": current_rss_before_publish_kib,
        "current_rss_after_publish_kib": current_rss_after_publish_kib,
        "current_rss_after_source_release_kib": current_rss_after_source_release_kib,
        "current_rss_after_readback_kib": current_rss_after_readback_kib,
        "source_driver_sha256": _source_driver_sha256(),
        "kernel_sha256": hashlib.sha256(
            Path(__import__("CODE.leo_sim.kernel", fromlist=["Kernel"]).__file__)
            .read_bytes()).hexdigest(),
        "codec_sha256": hashlib.sha256(Path(replay_codec.__file__).read_bytes()).hexdigest(),
    }
    _write_report(out_dir / "report.json", report)
    if not report["equal"]:
        raise ProbeError("production history publication round trip failed")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="new output directory (must not already exist)")
    parser.add_argument("--rows", default=DEFAULT_ROWS, type=int,
                        help=f"fixed repeated rows, {MIN_ROWS}..{MAX_ROWS} "
                             f"(default: {DEFAULT_ROWS})")
    parser.add_argument("--receivers", default=DEFAULT_HISTORY_RECEIVERS,
                        type=int, help="history mode receiver labels "
                                       f"(1..{MAX_HISTORY_RECEIVERS})")
    parser.add_argument("--origins", default=DEFAULT_HISTORY_ORIGINS,
                        type=int, help="history mode origins per receiver "
                                       f"(1..{MAX_HISTORY_ORIGINS})")
    parser.add_argument("--history-length", default=DEFAULT_HISTORY_LENGTH,
                        type=int, help="history samples per (receiver, origin) "
                                       f"(1..{MAX_HISTORY_LENGTH})")
    parser.add_argument("--downlink", default=DEFAULT_HISTORY_DOWNLINK,
                        type=int, help="downlink resources per sample "
                                       f"(1..{MAX_HISTORY_DOWNLINK})")
    parser.add_argument("--queries", default=DEFAULT_HISTORY_QUERIES,
                        type=int, help="recorded queries per arm "
                                       f"({HISTORY_INVALIDATION_QUERIES}.."
                                       f"{MAX_HISTORY_QUERIES})")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--graph", action="store_true",
                       help="test the production network object-graph storage path")
    modes.add_argument("--history", action="store_true",
                       help="test cached advertisement-history publication/readback")
    args = parser.parse_args(argv)
    try:
        report = (run_graph_probe(args.out_dir) if args.graph else
                  run_history_probe(args.out_dir,
                                     receivers=args.receivers,
                                     origins=args.origins,
                                     history=args.history_length,
                                     downlink=args.downlink,
                                     queries=args.queries) if args.history else
                  run_probe(args.out_dir, rows=args.rows))
    except (ProbeError, t1_tasks.TaskError, OSError, ValueError) as exc:
        print(f"PUBLICATION PROBE FAILED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"status": "verified", "report": str(args.out_dir / "report.json"),
                      "rows": report.get("rows", report.get("references")),
                      "history_count": report.get("history_count"),
                      "outputbytes": report["outputbytes"],
                      "equal": report["equal"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
