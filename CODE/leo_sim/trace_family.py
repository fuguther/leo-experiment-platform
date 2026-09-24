"""Deterministic nested load family companion contract (Task 6).

The trace manifest stays exact-key (no nested metadata may enter it): when
``demand.nested_master_offered_mbps`` is non-null, ``compile_trace`` writes a
separate ``nested-family.json`` companion binding the child trace to its
master candidate universe.  The companion is exact-key and versioned; an
independent verifier recomputes every identity/hash and checks that the
child is a strict multiset subset of the master under the canonical row
contract (packet id excluded).
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

from . import config

FAMILY_SCHEMA = "leo-sim-nested-trace-family/v1"
CANONICAL_ROW_CONTRACT = "emit_time_s,src_grid_id,dst_grid_id,bits,deadline_at_s"
FAMILY_KEYS = {
    "schema", "family_identity_sha256", "master_offered_mbps",
    "child_offered_mbps", "inclusion_probability", "master_candidate_packets",
    "child_packets", "demand_rng_stream", "filter_rng_stream",
    "canonical_row_contract", "config_sha256", "trace_identity_sha256",
    "trace_sha256",
}
_MASTER_STREAM = "demand"
_FILTER_STREAM = "nested_filter"

_FLOAT_TOL = 1e-9


def family_identity_payload(resolved: dict) -> dict:
    """Family identity scope: the trace-determining config with ONLY
    demand.offered_mbps removed (the child rate is the filtering knob, not a
    member identity).  Retains seed, master load, temporal model, population
    asset fields, geometry-independent trace fields, packet size, emission
    window and sampler settings; output paths are already absent from the
    trace identity payload."""
    payload = config.trace_identity_payload(resolved)
    demand = dict(payload["demand"])
    demand.pop("offered_mbps", None)
    payload["demand"] = demand
    return payload


def family_identity_sha256(resolved: dict, input_sha256: str = "") -> str:
    """SHA256 of the family identity payload plus the demand input content
    hash (population raster bytes for population_gravity)."""
    payload = family_identity_payload(resolved)
    payload["input_sha256"] = input_sha256
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def canonical_row(row: dict) -> tuple:
    return (
        row["emit_time_s"],
        row["src_grid_id"],
        row["dst_grid_id"],
        row["bits"],
        row["deadline_at_s"],
    )


def is_multiset_subset(child_rows: list[dict], parent_rows: list[dict]) -> bool:
    child = Counter(canonical_row(row) for row in child_rows)
    parent = Counter(canonical_row(row) for row in parent_rows)
    return all(count <= parent[item] for item, count in child.items())


def verify_family_child(child_rows: list[dict],
                        parent_rows: list[dict]) -> list[str]:
    """Independent child-vs-parent verifier.  Empty list = verified.

    - every child canonical row must appear in the parent with at least its
      multiplicity (no foreign row, no duplicate beyond the parent);
    - child packet ids must be exactly 1..N sequential in emission order.
    """
    errors: list[str] = []
    child = Counter(canonical_row(row) for row in child_rows)
    parent = Counter(canonical_row(row) for row in parent_rows)
    for item, count in child.items():
        available = parent.get(item, 0)
        if available == 0:
            errors.append(
                f"child row {item} is not in the parent trace")
        elif count > available:
            errors.append(
                f"child row {item} appears {count}x, parent multiplicity "
                f"only {available}x")
    ids = [row["packet_id"] for row in child_rows]
    if ids != list(range(1, len(ids) + 1)):
        errors.append(
            f"child packet ids must be contiguous 1..N, got {ids[:5]}...")
    times = [row["emit_time_s"] for row in child_rows]
    if times != sorted(times):
        errors.append("child rows are not in emission order")
    return errors


def _is_num(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and value > 0


def verify_nested_family_companion(
        companion: dict, resolved: dict, input_sha256: str,
        child_trace_sha256: str, child_packets: int | None = None,
        master_candidate_packets: int | None = None) -> list[str]:
    """Independent companion verifier (empty = verified): recomputes the
    family identity, stream labels, loads, inclusion probability, config and
    trace identities, and the child/master packet counts from the resolved
    config and the actual trace bytes."""
    errors: list[str] = []
    if not isinstance(companion, dict):
        return ["nested-family.json must be a JSON object"]
    if companion.get("schema") != FAMILY_SCHEMA:
        errors.append(f"companion schema must be {FAMILY_SCHEMA}")
    if set(companion) != FAMILY_KEYS:
        errors.append(
            "companion keys mismatch: "
            f"unknown={sorted(set(companion) - FAMILY_KEYS)} "
            f"missing={sorted(FAMILY_KEYS - set(companion))}")
    dm = resolved["config"]["demand"]
    master = dm.get("nested_master_offered_mbps")
    if master is None:
        errors.append("nested-family.json requires "
                      "demand.nested_master_offered_mbps")
    else:
        expected_inclusion = float(dm["offered_mbps"]) / float(master)
        if not _is_num(companion.get("master_offered_mbps")) \
                or abs(float(companion["master_offered_mbps"])
                       - float(master)) > _FLOAT_TOL:
            errors.append("companion master_offered_mbps != resolved config")
        if not _is_num(companion.get("child_offered_mbps")) \
                or abs(float(companion["child_offered_mbps"])
                       - float(dm["offered_mbps"])) > _FLOAT_TOL:
            errors.append("companion child_offered_mbps != resolved config")
        if not _is_num(companion.get("inclusion_probability")) \
                or abs(float(companion["inclusion_probability"])
                       - expected_inclusion) > _FLOAT_TOL:
            errors.append("companion inclusion_probability != "
                          "offered_mbps / master_offered_mbps")
        if companion.get("demand_rng_stream") != _canonical_stream_label(
                resolved, _MASTER_STREAM):
            errors.append("companion demand_rng_stream mismatch")
        if companion.get("filter_rng_stream") != _canonical_stream_label(
                resolved, _FILTER_STREAM):
            errors.append("companion filter_rng_stream mismatch")
    if companion.get("canonical_row_contract") != CANONICAL_ROW_CONTRACT:
        errors.append("companion canonical_row_contract mismatch")
    expected_family = family_identity_sha256(resolved, input_sha256)
    if companion.get("family_identity_sha256") != expected_family:
        errors.append("companion family_identity_sha256 mismatch")
    if companion.get("config_sha256") != resolved["sha256"]:
        errors.append("companion config_sha256 mismatch")
    expected_trace_identity = config.trace_identity_sha256(
        resolved, input_sha256)
    if companion.get("trace_identity_sha256") != expected_trace_identity:
        errors.append("companion trace_identity_sha256 mismatch")
    if companion.get("trace_sha256") != child_trace_sha256:
        errors.append("companion trace_sha256 mismatch")
    if child_packets is not None and companion.get("child_packets") != \
            child_packets:
        errors.append("companion child_packets mismatch")
    if master_candidate_packets is not None and \
            companion.get("master_candidate_packets") != \
            master_candidate_packets:
        errors.append("companion master_candidate_packets mismatch")
    for key in ("master_candidate_packets", "child_packets"):
        value = companion.get(key)
        if not isinstance(value, int) or isinstance(value, bool) \
                or value < 1:
            errors.append(f"companion {key} must be a positive integer")
    if isinstance(companion.get("master_candidate_packets"), int) \
            and isinstance(companion.get("child_packets"), int) \
            and companion["child_packets"] > \
            companion["master_candidate_packets"]:
        errors.append("companion child_packets > master_candidate_packets")
    return errors


def _canonical_stream_label(resolved: dict, name: str) -> str:
    from . import rng as rng_mod
    seed = resolved["config"]["scenario"]["seed"]
    return rng_mod.stream_mapping(seed, rng_mod.STREAM_NAMES)[name]