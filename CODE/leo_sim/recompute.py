"""Independent re-computation of a PERSISTED leo_sim run's key metrics.

WHY
===
The production metrics are recomputed by receipt.py with the very same
metrics.summarize call that produced them, so comparing a receipt against
itself is not verification (audit finding L11).  This module is the runnable
path to the second implementation: it reads only persisted artifacts
(ledgers.json, resolved_config.json, receipt.json, timeline.jsonl) and calls
CODE.leo_sim.metrics_independent, which never imports the production module.

WHAT IT REFUSES
===============
* **A missing utilization denominator.**  If any link (or the whole run) has no
  sampled available-capacity window, no utilization number is published: the
  per-link row carries status DEGENERATE_DENOMINATOR / utilization null and the
  aggregate is reported as unavailable, never as 0.0 or 1.0.
* **F2 folded into decision time.**  When execution.node_process_delay_s > 0
  the satellite node occupancy exists ONLY on the timeline stream.  Without
  that stream the second implementation reports the node time as decision
  computation time.  The run is refused rather than publishing a number that
  names the wrong mechanism; the report states the exact leakage (labelled vs
  unlabelled total) when the stream IS present.
* **A stream that does not match its receipt binding.**  When receipt.json is a
  v6 receipt, every stream named there is re-hashed and compared.

WHAT IT DOES NOT DO
===================
It does not re-run the simulation and it does not authorise anything.  A run
whose receipt is not verified is still re-computable here, and the report says
so: this is a metric cross-check, not a trust-chain verdict.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import metrics_independent as independent

SCHEMA = "leo-sim-recompute/v1"
RECEIPT_SCHEMA_V6 = "leo-sim-receipt/v6"

#: Declared comparison contract for the production cross-check.
BITS_TOLERANCE = 1e-6
UTILIZATION_TOLERANCE = 1e-9


def _cross_check_field(link_id: str, field: str, independent_value,
                       other: dict, tolerance: float) -> list[dict]:
    """One field of one link, compared against the production reading.

    A field the production reading does NOT carry is reported as a mismatch.
    This is the whole point of the function: the historical form was

        abs(float(mine) - float(other.get(field, float("nan")))) > tol

    and every comparison against NaN is False, so a production link missing
    served_bits / capacity_bits / utilization compared EQUAL, contributed no
    mismatch, and left check R5 green on a reading that was never verified
    (measured 2026-09-25: deleting "served_bits" from every production link
    still produced mismatch_total = 0).  A non-numeric value is likewise a
    mismatch, not a crash.
    """
    if field not in other:
        return [{"link_id": link_id, "field": field,
                 "independent": independent_value, "production": None,
                 "why": "field is absent from the production reading"}]
    raw = other[field]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return [{"link_id": link_id, "field": field,
                 "independent": independent_value, "production": raw,
                 "why": "production value is not numeric"}]
    if abs(float(independent_value) - float(raw)) > tolerance:
        return [{"link_id": link_id, "field": field,
                 "independent": independent_value, "production": raw,
                 "why": f"differs by more than {tolerance!r}"}]
    return []


class RecomputeError(ValueError):
    """The persisted run directory cannot be re-computed as it stands."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path, label: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise RecomputeError(f"{label} is missing or symbolic: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecomputeError(f"{label} is unreadable: {exc}") from exc
    if not isinstance(value, dict):
        raise RecomputeError(f"{label} must be a JSON object")
    return value


def _read_jsonl(path: Path, label: str) -> list[dict]:
    if path.is_symlink() or not path.is_file():
        raise RecomputeError(f"{label} is missing or symbolic: {path}")
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecomputeError(
                    f"{label} line {number} is not JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise RecomputeError(f"{label} line {number} is not an object")
            rows.append(row)
    return rows


def recompute_run(run_dir: str | Path) -> dict:
    """Recompute the key metrics of one persisted run directory."""
    import os
    run_dir = Path(run_dir)
    if run_dir.is_symlink():
        raise RecomputeError(f"run directory may not be symbolic: {run_dir}")
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise RecomputeError(f"run directory does not exist: {run_dir}")

    receipt = _read_json(run_dir / "receipt.json", "receipt.json")
    resolved = _read_json(run_dir / "resolved_config.json",
                          "resolved_config.json")
    ledgers = _read_json(run_dir / "ledgers.json", "ledgers.json")
    try:
        execution = resolved["config"]["execution"]
        node_process_delay_s = float(execution["node_process_delay_s"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RecomputeError(
            f"resolved_config.json has no usable execution.node_process_delay_s: "
            f"{exc}") from exc

    packet_events = ledgers.get("packet_events")
    service_windows = ledgers.get("link_service_windows")
    available_windows = ledgers.get("link_available_windows") or []
    if packet_events is None or service_windows is None:
        raise RecomputeError(
            "ledgers.json lacks packet_events/link_service_windows; the "
            "independent recomputation needs the persisted per-packet and "
            "per-link event streams and will not guess them")

    # ---- F2 conflation gate -------------------------------------------
    timeline_path = run_dir / "timeline.jsonl"
    spans: dict | None = None
    f2_leakage = None
    if timeline_path.is_file() and not timeline_path.is_symlink():
        timeline_rows = _read_jsonl(timeline_path, "timeline.jsonl")
        spans = independent.node_process_spans(timeline_rows)
        if not spans and node_process_delay_s > 0:
            raise RecomputeError(
                "execution.node_process_delay_s > 0 but timeline.jsonl carries "
                "no node_process span; decision_compute_s would silently "
                "include satellite node processing time")
    elif node_process_delay_s > 0:
        raise RecomputeError(
            "execution.node_process_delay_s > 0 requires the timeline stream: "
            "satellite node processing time is recorded nowhere else, so "
            "decision_compute_s cannot be separated from it (run with "
            "--timeline-log)")

    # ---- independent recomputation -------------------------------------
    utilization = independent.recompute_link_utilization(
        packet_events, service_windows, available_windows)
    decomposition = independent.verify_delay_decomposition(
        packet_events, service_windows, ledgers,
        node_spans_by_pid=(spans if spans else None))
    if spans is not None:
        unlabelled = independent.verify_delay_decomposition(
            packet_events, service_windows, ledgers)
        f2_leakage = {
            "labelled_decision_compute_s": decomposition[
                "total_decision_compute_s"],
            "unlabelled_decision_compute_s": unlabelled[
                "total_decision_compute_s"],
            "node_process_s": decomposition["total_node_process_s"],
            "note": ("the unlabelled reading folds node processing into "
                     "decision computation; the two agree exactly when the "
                     "run has no F2 occupancy"),
        }

    degenerate = sorted(link_id for link_id, item in utilization.items()
                        if item["status"] != independent.STATUS_OK)
    served = {link_id: item for link_id, item in utilization.items()
              if item["served_bits"] > 0.0}
    utilization_summary = None
    if degenerate:
        # A missing denominator is not a zero.  Publish nothing rather than an
        # average that would read as a measurement.
        utilization_summary = {
            "status": independent.STATUS_DEGENERATE,
            "reason": ("no sampled available-capacity denominator for "
                       + ", ".join(degenerate)),
            "link_utilization_mean": None,
            "served_links": len(served),
            "degenerate_links": degenerate,
        }
    else:
        values = [item["utilization"] for item in utilization.values()]
        utilization_summary = {
            "status": independent.STATUS_OK,
            "link_utilization_mean": (sum(values) / len(values)
                                      if values else None),
            "served_links": len(served),
            "degenerate_links": [],
        }

    # ---- cross-check against the persisted production reading ----------
    production = ledgers.get("congestion_metrics")
    if not isinstance(production, dict):
        raise RecomputeError(
            "ledgers.congestion_metrics is missing or not a mapping; without "
            "it the independent reading has nothing to be checked against")
    production_links = production.get("links")
    if not isinstance(production_links, dict):
        raise RecomputeError(
            "ledgers.congestion_metrics.links is missing or not a mapping; "
            "the per-link cross-check cannot be performed")
    mismatches = []
    # Both directions.  Iterating only over the independent report lets a link
    # the production reading invented (or one the independent walk dropped)
    # disappear from the comparison entirely.
    for link_id in sorted(set(utilization) | set(production_links)):
        mine = utilization.get(link_id)
        other = production_links.get(link_id)
        if mine is None:
            mismatches.append({"link_id": link_id, "field": "<absent>",
                               "independent": None, "production": other,
                               "why": "link is absent from the independent "
                                      "recomputation"})
            continue
        if not isinstance(other, dict):
            mismatches.append({"link_id": link_id, "field": "<absent>",
                               "independent": mine["served_bits"],
                               "production": other,
                               "why": "link is absent from the production "
                                      "reading"})
            continue
        for key in ("served_bits", "capacity_bits"):
            mismatches.extend(_cross_check_field(
                link_id, key, mine[key], other, BITS_TOLERANCE))
        if mine["status"] == independent.STATUS_OK:
            mismatches.extend(_cross_check_field(
                link_id, "utilization", mine["utilization"], other,
                UTILIZATION_TOLERANCE))

    # ---- stream bindings ------------------------------------------------
    bindings: dict[str, dict] = {}
    for key, name in (("decision_log_sha256", "decisions.jsonl"),
                      ("timeline_log_sha256", "timeline.jsonl")):
        recorded = receipt.get(key)
        if recorded is None:
            continue
        candidate = run_dir / name
        if candidate.is_symlink() or not candidate.is_file():
            bindings[name] = {"recorded": recorded, "actual": None,
                              "matches": False,
                              "why": "named by the receipt but not present"}
            continue
        actual = _sha256(candidate)
        bindings[name] = {"recorded": recorded, "actual": actual,
                          "matches": actual == recorded}

    checks = [
        {"id": "R1", "statement": "the persisted streams were read, not guessed",
         "ok": True},
        {"id": "R2",
         "statement": "no link publishes a utilization without a sampled "
                      "denominator",
         "ok": not degenerate},
        {"id": "R3",
         "statement": "every delivered packet's e2e closes over the six named "
                      "phases",
         "ok": bool(decomposition["ok"])},
        {"id": "R4",
         "statement": "satellite node processing time is not reported as "
                      "decision computation time",
         "ok": (node_process_delay_s <= 0.0
                or decomposition["node_process_spans_supplied"])},
        {"id": "R5",
         "statement": "the independent and production link readings agree",
         "ok": not mismatches},
        {"id": "R6",
         "statement": "every stream the receipt names is present and matches "
                      "its recorded digest",
         "ok": all(entry["matches"] for entry in bindings.values())},
    ]
    return {
        "schema": SCHEMA,
        "run_dir": str(run_dir),
        "receipt_schema": receipt.get("schema"),
        "run_id": receipt.get("run_id"),
        "node_process_delay_s": node_process_delay_s,
        "compute_delay_s": execution.get("compute_delay_s"),
        "input_digests": {
            "receipt.json": _sha256(run_dir / "receipt.json"),
            "resolved_config.json": _sha256(run_dir / "resolved_config.json"),
            "ledgers.json": _sha256(run_dir / "ledgers.json"),
        },
        "stream_bindings": bindings,
        "f2_separation": f2_leakage,
        "link_utilization": {
            "summary": utilization_summary,
            "links": {link_id: {
                "stage": item["stage"],
                "served_bits": item["served_bits"],
                "capacity_bits": item["capacity_bits"],
                "available_capacity_bits": item["available_capacity_bits"],
                "available_samples": item["available_samples"],
                "utilization": item["utilization"],
                "status": item["status"],
                "fallback_utilization": item.get("fallback_utilization"),
            } for link_id, item in sorted(utilization.items())},
        },
        "delay_decomposition": {
            "ok": decomposition["ok"],
            "checked_packets": decomposition["checked_packets"],
            "max_abs_residual_s": decomposition["max_abs_residual_s"],
            "total_decision_compute_s": decomposition["total_decision_compute_s"],
            "total_node_process_s": decomposition["total_node_process_s"],
            "node_process_spans_supplied": decomposition[
                "node_process_spans_supplied"],
            "errors": decomposition["errors"][:50],
            "error_total": len(decomposition["errors"]),
        },
        "production_cross_check": {
            "mismatch_total": len(mismatches),
            "mismatches": mismatches[:50],
        },
        "checks": checks,
        "ok": all(check["ok"] for check in checks),
        "scope": ("metric cross-check over persisted artifacts; this is not a "
                  "receipt verification and not an authorization"),
    }
