#!/usr/bin/env python3
"""Step-5 gate-evidence recomputation harness (reviewed artifact).

PURPOSE
=======
Produce the two independent evidence chains required by review step 5, from the
IMMUTABLE persisted record of a formal leo_sim_v2 run:

  chain 1  per directed link utilisation, including zero-service links
  chain 2  per packet delay phase decomposition, with every uncovered interval
           ('gap') individually proven equal to execution.compute_delay_s

INDEPENDENCE CONTRACT
=====================
All arithmetic is delegated to CODE.leo_sim.metrics_independent, which is a
second implementation that walks this repo with ast and refuses any reference to
CODE.leo_sim.metrics.  This harness additionally guards ITSELF with the same
technique (_assert_independent at the bottom): it must never import the
production metrics module.  The production numbers are read ONLY as persisted
data (ledgers.json -> congestion_metrics), never by calling metrics.summarize,
so that a disagreement between the two implementations stays observable.

NO LOSSY REPORTING
==================
Every list in the output is complete.  Nothing is truncated with [:n].  If a
run produces 10,000 mismatches, the report contains 10,000 entries; callers that
need brevity must summarise downstream, not here.

TRUNCATION IS FATE-APPROPRIATE
==============================
DELIVERED packets get the full end-to-end closure
    e2e == queue_wait + holding_wait + tx + prop + uncovered
Non-delivered packets (IN_SYSTEM_AT_STOP, *_OVERFLOW, *_EXPIRED, NO_ROUTE, ...)
have NO realised end-to-end delay.  For them this harness reports the truncated
components that do exist, verifies the truncated spans are disjoint and lie
inside [emitted_at, stop_time_s], and explicitly reports e2e/residual as
undefined.  The delivered formula is NEVER applied to them.

USAGE
=====
  python3 step5_recompute.py analyze \
      --run-id <run_id> --cd 0.05 \
      --ledgers <run_dir>/ledgers.json --receipt <run_dir>/receipt.json \
      --out <out.json>

  python3 step5_recompute.py witness \
      --run-id <run_id> --cd 0.05 \
      --ledgers <run_dir>/ledgers.json \
      --diagnostic-ledgers <diag_dir>/ledgers.json \
      --decision-log <diag_dir>/decisions.jsonl \
      --out <witness.json>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from CODE.leo_sim import metrics_independent as mi  # noqa: E402

SCHEMA = "leo-sim-step5-recompute/v2"
WITNESS_SCHEMA = "leo-sim-step5-decision-witness/v1"

# Declared tolerance contract.  Any comparison in this report uses exactly these.
TOL = {
    "gap_length_s": 1e-9,        # |gap - compute_delay_s|
    "closure_residual_s": 1e-9,  # |e2e - sum(phases)|
    "production_abs_s": 1e-9,    # independent vs production, time quantities
    "production_rel": 1e-9,      # independent vs production, bit quantities
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def digest(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------- chain 1
def chain1(packet_events, service_windows, available_windows, production_links):
    report = mi.recompute_link_utilization(
        packet_events, service_windows, available_windows)
    rows = []
    mismatches = []
    for link_id, item in sorted(report.items()):
        prod = production_links.get(link_id) if isinstance(production_links, dict) else None
        row = {
            "link_id": link_id,
            "stage": item["stage"],
            "service_windows": item["service_windows"],
            "served_bits": item["served_bits"],
            "capacity_bits": item["capacity_bits"],
            "available_samples": item["available_samples"],
            "available_time_s": item["available_time_s"],
            "available_capacity_bits": item["available_capacity_bits"],
            "utilization": item["utilization"],
            "status": item["status"],
            "fallback_utilization": item.get("fallback_utilization"),
            "production_present": prod is not None,
            "mismatch": None,
        }
        # zero-service / zero-capacity / window-boundary coverage is intrinsic
        # to this row: a link appears here even with 0 service windows.
        if prod is None:
            mismatches.append({"link_id": link_id, "field": "<link absent from production reading>",
                               "independent": None, "production": None})
            row["mismatch"] = "LINK_ABSENT_FROM_PRODUCTION"
        else:
            fields = []
            for key in ("served_bits", "capacity_bits", "available_capacity_bits",
                        "available_time_s", "available_samples", "service_windows"):
                if key not in prod:
                    continue
                a, b = item.get(key), prod.get(key)
                same = (a == b) if isinstance(a, int) and isinstance(b, int) else (
                    a is not None and b is not None and math.isclose(
                        float(a), float(b), rel_tol=TOL["production_rel"],
                        abs_tol=TOL["production_rel"]))
                if not same:
                    fields.append({"field": key, "independent": a, "production": b})
            a, b = item["utilization"], prod.get("utilization")
            if a is None or b is None:
                if a != b:
                    fields.append({"field": "utilization", "independent": a,
                                   "production": b})
            elif not math.isclose(float(a), float(b), rel_tol=TOL["production_rel"],
                                  abs_tol=TOL["production_abs_s"]):
                fields.append({"field": "utilization", "independent": a,
                               "production": b})
            if fields:
                row["mismatch"] = "FIELD_MISMATCH"
                for f in fields:
                    mismatches.append({"link_id": link_id, **f})
        rows.append(row)
    prod_only = sorted(set(production_links or {}) - set(report))
    for link_id in prod_only:
        mismatches.append({"link_id": link_id,
                           "field": "<link absent from independent recomputation>",
                           "independent": None, "production": production_links[link_id]})
    return {
        "links_total": len(rows),
        "links_zero_served": sum(1 for r in rows if r["served_bits"] == 0),
        "links_zero_service_windows": sum(1 for r in rows if r["service_windows"] == 0),
        "links_zero_available_capacity": sum(
            1 for r in rows if (r["available_capacity_bits"] or 0.0) == 0.0),
        "degenerate_denominator_links": sorted(
            r["link_id"] for r in rows if r["status"] != mi.STATUS_OK),
        "availability_sampled": bool(available_windows),
        "production_links_total": len(production_links or {}),
        "production_only_links": prod_only,
        "mismatch_total": len(mismatches),
        "mismatches": mismatches,
        "rows": rows,
    }


def chain1_negative_controls(packet_events, service_windows, available_windows):
    """A green chain-1 result is only meaningful if the checker really rejects
    the corruptions it claims to reject.  Both corruptions are injected into
    copies of the real windows; a non-rejection is reported as a failure."""
    se0 = json.loads(json.dumps(packet_events))
    sw0 = json.loads(json.dumps(service_windows))
    aw0 = json.loads(json.dumps(available_windows))
    controls = []

    def attempt(name, mutate):
        se = json.loads(json.dumps(se0)); sw = json.loads(json.dumps(sw0))
        aw = json.loads(json.dumps(aw0))
        try:
            mutate(se, sw, aw)
        except Exception as exc:  # noqa: BLE001
            controls.append({"control": name, "rejected": None,
                             "note": f"control not applicable: {exc}"})
            return
        try:
            mi.recompute_link_utilization(se, sw, aw)
        except mi.IndependentMetricsError as exc:
            controls.append({"control": name, "rejected": True, "error": str(exc)})
        else:
            controls.append({"control": name, "rejected": False,
                             "error": "ACCEPTED - checker did not reject the corruption"})

    def tamper_capacity(se, sw, aw):
        for wd in sw:
            if wd.get("capacity_bits") is not None:
                wd["capacity_bits"] = float(wd["capacity_bits"]) * 1.5 + 1.0
                return
        raise AssertionError("no capacity-bearing service window")

    def overlap_available(se, sw, aw):
        """Slide the second window of some link half a window earlier so that it
        overlaps the first.  Works for any link with >= 2 sampled windows."""
        by_link = {}
        for idx, wd in enumerate(aw):
            by_link.setdefault(wd.get("link_id"), []).append(idx)
        for _lid, idxs in by_link.items():
            if len(idxs) < 2:
                continue
            i0, i1 = idxs[0], idxs[1]
            span = aw[i1]["end"] - aw[i1]["start"]
            shifted = dict(aw[i1])
            shifted["start"] = aw[i0]["end"] - span / 2.0
            shifted["end"] = shifted["start"] + span
            shifted["capacity_bits"] = shifted["rate_bps"] * (
                shifted["end"] - shifted["start"])
            aw[i1] = shifted
            return
        raise AssertionError("no link with >= 2 availability windows to overlap")

    attempt("tampered capacity_bits != rate_bps*(end-start)", tamper_capacity)
    attempt("overlapping available-capacity window on one link", overlap_available)
    return controls


# --------------------------------------------------------------- chain 2
def chain2(packet_events, service_windows, production_packets, cd, stop_time,
           packet_fates=None):
    records = mi._scan_packet_events(mi._mapping_list(packet_events, "packet_events"))
    _, by_pid = mi._validate_service_windows(
        mi._mapping_list(service_windows, "service_windows"), records)

    def fate_of(pid):
        entry = (packet_fates or {}).get(str(pid))
        if isinstance(entry, (list, tuple)) and entry:
            return str(entry[0])
        return "UNKNOWN"

    delivered = sorted(pid for pid, it in records.items()
                       if it["delivered_at"] is not None)
    undelivered = sorted(pid for pid, it in records.items()
                         if it["delivered_at"] is None)

    packets = []
    gap_violations = []
    phase_mismatches = []
    closure_errors = []
    gap_count_hist = Counter()
    total_gaps = 0
    max_abs_residual = 0.0

    for pid in delivered:
        d = mi._decompose(records, by_pid, pid)
        gaps = d["gaps"] or []
        total_gaps += len(gaps)
        for g in gaps:
            gap_count_hist[round(g[2], 12)] += 1
            if abs(g[2] - cd) > TOL["gap_length_s"]:
                gap_violations.append({
                    "pid": pid, "gap_start_s": g[0], "gap_end_s": g[1],
                    "gap_length_s": g[2], "expected_compute_delay_s": cd,
                    "excess_s": g[2] - cd})
        overlap_pairs = [{"first": g1, "second": g2}
                         for g1, g2 in zip(gaps, gaps[1:])
                         if g2[0] < g1[1] - mi.CONTIGUITY_EPS_S]
        negative = [g for g in gaps if g[2] < 0.0 or g[1] < g[0]]
        out_of_bounds = [g for g in gaps
                         if g[0] < d["emitted_at"] - TOL["closure_residual_s"]
                         or g[1] > d["delivered_at"] + TOL["closure_residual_s"]]
        residual = d["residual_s"]
        max_abs_residual = max(max_abs_residual, abs(residual))
        if abs(residual) > TOL["closure_residual_s"]:
            closure_errors.append({"pid": pid, "residual_s": residual,
                                   "e2e_s": d["e2e_s"]})
        prod = production_packets.get(str(pid)) if isinstance(production_packets, dict) else None
        for key, indep in (("e2e_s", d["e2e_s"]),
                           ("queue_wait_s", d["queue_wait_s"]),
                           ("holding_wait_s", d["holding_wait_s"]),
                           ("tx_s", d["tx_s"]),
                           ("prop_s", d["prop_s"])):
            if prod is None or prod.get(key) is None:
                continue
            if not math.isclose(float(indep), float(prod[key]),
                                rel_tol=TOL["production_rel"],
                                abs_tol=TOL["production_abs_s"]):
                phase_mismatches.append({"pid": pid, "field": key,
                                         "independent": indep,
                                         "production": prod[key],
                                         "delta": float(indep) - float(prod[key])})
        packets.append({
            "pid": pid,
            "fate": fate_of(pid),
            "delivered": True,
            "emitted_at_s": d["emitted_at"],
            "delivered_at_s": d["delivered_at"],
            "e2e_s": d["e2e_s"],
            "queue_wait_s": d["queue_wait_s"],
            "holding_wait_s": d["holding_wait_s"],
            "tx_s": d["tx_s"],
            "prop_s": d["prop_s"],
            "decision_compute_s": d["decision_compute_s"],
            "residual_s": residual,
            "gaps": [{"start_s": g[0], "end_s": g[1], "length_s": g[2]}
                     for g in gaps],
            "gap_count": len(gaps),
            "gap_count_x_cd_s": len(gaps) * cd,
            "gap_sum_equals_count_x_cd": math.isclose(
                d["decision_compute_s"], len(gaps) * cd,
                rel_tol=0.0, abs_tol=TOL["gap_length_s"]),
            "overlapping_gap_pairs": overlap_pairs,
            "negative_or_inverted_gaps": negative,
            "gaps_out_of_bounds": out_of_bounds,
            "phase_overlaps": d["overlaps"],
            "production_present": prod is not None,
        })

    truncation = {}
    for pid in undelivered:
        d = mi._decompose(records, by_pid, pid)
        it = records[pid]
        fate = fate_of(pid)
        ends = []
        after_emission = 0
        spans = mi._spans(it, sorted(by_pid.get(pid, ()),
                                     key=lambda x: (x["start"], x["index"])),
                          mi._queue_waits(it), mi._holding_waits(it),
                          mi._propagation_hops(it))
        for span in spans:
            ends.append(span[1])
            if span[0] < it["emitted_at"]:
                after_emission += 1
        bucket = truncation.setdefault(fate, {
            "n": 0, "e2e_defined": 0, "residual_defined": 0, "gaps_defined": 0,
            "phase_overlaps": 0, "spans_starting_before_emission": 0,
            "spans_ending_after_stop": 0, "packets_with_service_windows": 0,
            "sum_queue_wait_s": 0.0, "sum_holding_wait_s": 0.0,
            "sum_tx_s": 0.0, "sum_prop_s": 0.0, "max_span_end_s": 0.0,
            "truncation_rule": ("no realised end-to-end delay exists; the e2e/residual/gap "
                                "formula is NOT applied; only truncated phase components "
                                "inside [emitted_at, stop_time_s] are reported"),
        })
        bucket["n"] += 1
        if d["e2e_s"] is not None:
            bucket["e2e_defined"] += 1
        if d["residual_s"] is not None:
            bucket["residual_defined"] += 1
        if d["gaps"] is not None:
            bucket["gaps_defined"] += 1
        bucket["phase_overlaps"] += len(d["overlaps"])
        bucket["spans_starting_before_emission"] += after_emission
        if d["service_windows"] > 0:
            bucket["packets_with_service_windows"] += 1
        if ends:
            mx = max(ends)
            bucket["max_span_end_s"] = max(bucket["max_span_end_s"], mx)
            if mx > stop_time + TOL["closure_residual_s"]:
                bucket["spans_ending_after_stop"] += 1
        bucket["sum_queue_wait_s"] += d["queue_wait_s"]
        bucket["sum_holding_wait_s"] += d["holding_wait_s"]
        bucket["sum_tx_s"] += d["tx_s"]
        bucket["sum_prop_s"] += d["prop_s"]

    fate_counts = Counter()
    for pid, it in records.items():
        fate_counts[fate_of(pid)] += 1

    return {
        "tolerance_contract_s": TOL,
        "delivered_packets": len(delivered),
        "undelivered_packets": len(undelivered),
        "fate_counts": dict(fate_counts),
        "policy": {
            "DELIVERED": ("full end-to-end closure e2e == queue_wait + holding_wait + tx "
                          "+ prop + decision_compute; residual reported per packet"),
            "UNDELIVERED": ("fate-appropriate truncated accounting; e2e/residual/gaps are "
                            "undefined and are NOT computed with the delivered formula"),
        },
        "closure_max_abs_residual_s": max_abs_residual,
        "closure_error_total": len(closure_errors),
        "closure_errors": closure_errors,
        "gap_count_total": total_gaps,
        "gap_length_histogram_s": {str(k): v for k, v in sorted(gap_count_hist.items())},
        "gap_length_violation_total": len(gap_violations),
        "gap_length_violations": gap_violations,
        "gap_sum_equals_count_x_cd_all": all(
            p["gap_sum_equals_count_x_cd"] for p in packets),
        "production_phase_mismatch_total": len(phase_mismatches),
        "production_phase_mismatches": phase_mismatches,
        "truncation_by_fate": truncation,
        "packets": packets,
    }


def analyze(args) -> int:
    ledgers = load_json(Path(args.ledgers))
    receipt = load_json(Path(args.receipt))
    congestion = ledgers.get("congestion_metrics") or {}
    production_links = congestion.get("links") or {}
    production_packets = congestion.get("packets") or {}

    packet_events = ledgers.get("packet_events")
    service_windows = ledgers.get("link_service_windows")
    available_windows = ledgers.get("link_available_windows")
    if packet_events is None or service_windows is None:
        log("ledgers.json lacks packet_events/link_service_windows; refusing")
        return 2

    report = {
        "schema": SCHEMA,
        "run_id": args.run_id,
        "compute_delay_s": args.cd,
        "stop_time_s": ledgers.get("stop_time_s"),
        "natural_end": receipt.get("natural_end"),
        "conservation_ok": receipt.get("conservation_ok"),
        "receipt_fate_counts": receipt.get("fate_counts"),
        "input_digests": {
            "packet_events_sha256": digest(packet_events),
            "link_service_windows_sha256": digest(service_windows),
            "link_available_windows_sha256": digest(available_windows),
            "trace_sha256": receipt.get("trace_sha256"),
            "config_sha256": receipt.get("config_sha256"),
        },
        "chain1_link_utilization": chain1(
            packet_events, service_windows, available_windows, production_links),
        "chain1_negative_controls": chain1_negative_controls(
            packet_events, service_windows, available_windows),
        "chain2_delay_decomposition": chain2(
            packet_events, service_windows, production_packets, args.cd,
            float(ledgers.get("stop_time_s") or 0.0),
            packet_fates=ledgers.get("packet_fates")),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    log(json.dumps({
        "wrote": str(out),
        "chain1_links": report["chain1_link_utilization"]["links_total"],
        "chain1_mismatches": report["chain1_link_utilization"]["mismatch_total"],
        "chain1_negative_controls": report["chain1_negative_controls"],
        "chain2_delivered": report["chain2_delay_decomposition"]["delivered_packets"],
        "chain2_gap_violations": report["chain2_delay_decomposition"]["gap_length_violation_total"],
        "chain2_closure_errors": report["chain2_delay_decomposition"]["closure_error_total"],
        "chain2_production_mismatches": report["chain2_delay_decomposition"]["production_phase_mismatch_total"],
    }, sort_keys=True))
    return 0


def witness(args) -> int:
    """Decide whether a local diagnostic re-execution may stand in for the
    formal run's (unobtainable) decision_sink, and if so count deferred decisions.

    Admissible ONLY if the diagnostic run is provably the same execution:
    identical trace_sha256 AND bit-identical packet_events / link_service_windows
    / link_available_windows.  Otherwise the witness is reported unavailable and
    the caller must not use it.
    """
    formal = load_json(Path(args.ledgers))
    diag = load_json(Path(args.diagnostic_ledgers))

    def sha_of(led, key):
        return digest(led.get(key)) if led.get(key) is not None else None

    checks = {}
    for key in ("packet_events", "link_service_windows", "link_available_windows"):
        a, b = sha_of(formal, key), sha_of(diag, key)
        checks[key] = {"formal_sha256": a, "diagnostic_sha256": b, "identical": a == b}
    def trace_of(led, receipt_arg, explicit):
        if explicit:
            return explicit
        if receipt_arg:
            rp = Path(receipt_arg)
            if rp.is_file():
                return load_json(rp).get("trace_sha256")
        return led.get("trace_sha256")

    formal_trace = trace_of(formal, args.formal_receipt, args.formal_trace_sha256)
    diag_trace = trace_of(diag, args.diagnostic_receipt, args.diagnostic_trace_sha256)
    checks["trace_sha256"] = {"formal_sha256": formal_trace,
                              "diagnostic_sha256": diag_trace,
                              "identical": formal_trace is not None
                              and formal_trace == diag_trace}
    identical = all(v["identical"] for v in checks.values())

    decisions = []
    log_path = Path(args.decision_log)
    if log_path.is_symlink() or not log_path.is_file():
        log(f"decision log is missing or symbolic: {log_path}; refusing to report a "
            f"witness from zero rows")
        return 2
    with log_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                decisions.append(json.loads(line))
    if not decisions:
        log(f"decision log is empty: {log_path}; refusing to report a witness from "
            f"zero rows")
        return 2
    deferred = [r for r in decisions
                if r.get("t_decision_start") is not None
                and float(r["t"]) > float(r["t_decision_start"]) + TOL["production_abs_s"]]
    immediate = [r for r in decisions
                 if r.get("t_decision_start") is not None
                 and float(r["t"]) <= float(r["t_decision_start"]) + TOL["production_abs_s"]]

    per_pid = Counter()
    for r in deferred:
        per_pid[str(r.get("pid"))] += 1

    records = mi._scan_packet_events(mi._mapping_list(formal.get("packet_events"),
                                                      "packet_events"))
    _, by_pid = mi._validate_service_windows(
        mi._mapping_list(formal.get("link_service_windows"), "service_windows"), records)
    derived = {}
    for pid, it in records.items():
        if it["delivered_at"] is None:
            continue
        d = mi._decompose(records, by_pid, pid)
        derived[str(pid)] = {"gap_count": len(d["gaps"] or []),
                             "gap_sum_s": d["decision_compute_s"],
                             "gap_count_x_cd_s": len(d["gaps"] or []) * args.cd}

    # Uncovered intervals are defined ONLY on [emitted_at, delivered_at].  A
    # deferred decision taken by a packet that is never delivered therefore
    # produces no gap at all, so the per-packet reconciliation is scoped to
    # delivered packets; decisions on never-delivered packets are reported
    # separately and are NOT counted as disagreements.
    delivered_reconciliation = []
    undelivered_decisions = []
    for pid in sorted(set(derived) | set(per_pid), key=lambda x: int(x)):
        g = derived.get(pid, {}).get("gap_count")
        n = per_pid.get(pid, 0)
        if g is None:
            undelivered_decisions.append({
                "pid": int(pid), "diagnostic_deferred_decision_rows": n,
                "formal_gap_count": None,
                "reason": "packet was never delivered, so it has no delivery interval "
                          "and no uncovered interval can exist",
            })
            continue
        delivered_reconciliation.append({
            "pid": int(pid), "formal_gap_count": g,
            "diagnostic_deferred_decision_rows": n,
            "agree": g == n,
        })

    report = {
        "schema": WITNESS_SCHEMA,
        "run_id": args.run_id,
        "compute_delay_s": args.cd,
        "tolerance_contract_s": TOL,
        "admissible": identical,
        "identity_checks": checks,
        "decision_rows_total": len(decisions),
        "deferred_decision_rows": len(deferred),
        "immediate_decision_rows": len(immediate),
        "deferred_decisions_per_packet": {k: v for k, v in sorted(
            per_pid.items(), key=lambda kv: int(kv[0]))},
        "delivered_gap_reconciliation": delivered_reconciliation,
        "delivered_gap_reconciliation_all_agree": all(
            r["agree"] for r in delivered_reconciliation),
        "delivered_packets_checked": len(delivered_reconciliation),
        "undelivered_decision_rows": undelivered_decisions,
        "inadmissible_reason": None if identical else (
            "diagnostic re-execution is NOT bit-identical to the formal run; the "
            "decision-count witness is UNAVAILABLE for this run and must not be used"),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    log(json.dumps({"wrote": str(out), "admissible": identical,
                    "deferred_decision_rows": len(deferred),
                    "delivered_packets_checked": report["delivered_packets_checked"],
                    "delivered_reconciliation_all_agree":
                        report["delivered_gap_reconciliation_all_agree"],
                    "undelivered_packets_with_decisions":
                        len(report["undelivered_decision_rows"])},
                   sort_keys=True))
    return 0


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--run-id", required=True)
    a.add_argument("--cd", type=float, required=True)
    a.add_argument("--ledgers", required=True)
    a.add_argument("--receipt", required=True)
    a.add_argument("--out", required=True)
    a.set_defaults(fn=analyze)
    w = sub.add_parser("witness")
    w.add_argument("--run-id", required=True)
    w.add_argument("--cd", type=float, required=True)
    w.add_argument("--ledgers", required=True)
    w.add_argument("--diagnostic-ledgers", required=True)
    w.add_argument("--decision-log", required=True)
    w.add_argument("--formal-receipt")
    w.add_argument("--diagnostic-receipt")
    w.add_argument("--formal-trace-sha256")
    w.add_argument("--diagnostic-trace-sha256")
    w.add_argument("--out", required=True)
    w.set_defaults(fn=witness)
    st = sub.add_parser("selftest")
    st.add_argument("--out", required=True)
    st.set_defaults(fn=selftest)
    return p.parse_args(argv)



# --------------------------------------------------------------- self-test
def _fixture(gap_end_s, tx_start_s, tx_end_s, delivered_at_s):
    """A one-packet synthetic run with exactly one uncovered interval.

    Timeline: emitted 0.0 -> uplink queue 0.0 -> uplink tx [0.0, 1.0] ->
    propagation [1.0, 1.5] -> UNCOVERED [1.5, gap_end_s] -> isl tx
    [tx_start_s, tx_end_s] -> delivered at delivered_at_s.
    """
    events = [
        {"kind": "packet_emitted", "pid": 0, "at": 0.0, "bits": 1000},
        {"kind": "queue_enter", "pid": 0, "at": 0.0, "queue_id": 0,
         "queue": "uplink", "link_id": "gsl:uplink:0:A"},
        {"kind": "service_start", "pid": 0, "at": 0.0, "stage": "uplink",
         "link_id": "gsl:uplink:0:A", "rate_bps": 1000.0, "bits": 1000,
         "queue_id": 0},
        {"kind": "propagation_start", "pid": 0, "at": 1.0, "prop_id": 0,
         "delay_s": 0.5, "stage": "isl", "link_id": "isl:0:1"},
        {"kind": "propagation_arrival", "pid": 0, "at": 1.5, "prop_id": 0},
        {"kind": "queue_enter", "pid": 0, "at": tx_start_s, "queue_id": 1,
         "queue": "isl", "link_id": "isl:0:1"},
        {"kind": "service_start", "pid": 0, "at": tx_start_s, "stage": "isl",
         "link_id": "isl:0:1", "rate_bps": 1000.0, "bits": 1000, "queue_id": 1},
        {"kind": "delivered", "pid": 0, "at": delivered_at_s},
    ]
    windows = [
        {"pid": 0, "stage": "uplink", "link_id": "gsl:uplink:0:A", "start": 0.0,
         "end": 1.0, "rate_bps": 1000.0, "capacity_bits": 1000.0,
         "served_bits": 1000.0, "bits": 1000, "outcome": "ok"},
        {"pid": 0, "stage": "isl", "link_id": "isl:0:1", "start": tx_start_s,
         "end": tx_end_s, "rate_bps": 1000.0,
         "capacity_bits": 1000.0 * (tx_end_s - tx_start_s),
         "served_bits": 1000.0, "bits": 1000, "outcome": "ok"},
    ]
    # Two sampled windows per link (touching, not overlapping) so that the
    # overlapping-window negative control is actually applicable.
    available = []
    for stage, link in (("uplink", "gsl:uplink:0:A"), ("isl", "isl:0:1")):
        for start, end in ((0.0, 2.5), (2.5, 5.0)):
            available.append({"stage": stage, "link_id": link, "start": start,
                              "end": end, "rate_bps": 1000.0,
                              "capacity_bits": 1000.0 * (end - start)})
    return events, windows, available


def selftest(_args) -> int:
    """Prove the harness REPORTS a lumped/merged or cd+unknown uncovered
    interval instead of silently accepting it.  A green batch result is only
    meaningful if these fail-loud properties hold."""
    results = []

    def check(name, ok, detail):
        results.append({"check": name, "ok": bool(ok), "detail": detail})

    # (1) one uncovered interval exactly equal to cd -> accepted
    ev, sw, aw = _fixture(2.5, 2.5, 3.5, 3.5)
    c2 = chain2(ev, sw, {}, 1.0, 3.5)
    check("gap exactly compute_delay_s is accepted", 
          c2["gap_count_total"] == 1 and c2["gap_length_violation_total"] == 0
          and c2["closure_error_total"] == 0,
          f"gaps={c2['gap_count_total']} violations={c2['gap_length_violation_total']} "
          f"closure_errors={c2['closure_error_total']}")

    # (2) a MERGED / lumped interval of 2*cd must be REPORTED as a violation,
    #     not silently summed into a passing total
    ev, sw, aw = _fixture(3.5, 3.5, 4.5, 4.5)
    c2 = chain2(ev, sw, {}, 1.0, 4.5)
    v = c2["gap_length_violations"]
    check("merged interval of 2x compute_delay_s is REPORTED, not summed away",
          c2["gap_count_total"] == 1 and c2["gap_length_violation_total"] == 1
          and v and abs(v[0]["gap_length_s"] - 2.0) < 1e-9
          and v[0]["pid"] == 0,
          f"gaps={c2['gap_count_total']} violations={c2['gap_length_violation_total']} "
          f"first={v[0] if v else None}")

    # (3) an interval of cd + unexplained extra time must be reported with the excess
    ev, sw, aw = _fixture(3.0, 3.0, 4.0, 4.0)
    c2 = chain2(ev, sw, {}, 1.0, 4.0)
    v = c2["gap_length_violations"]
    check("interval of compute_delay_s + unknown extra is reported with the excess",
          c2["gap_length_violation_total"] == 1 and v
          and abs(v[0]["gap_length_s"] - 1.5) < 1e-9
          and abs(v[0]["excess_s"] - 0.5) < 1e-9,
          f"first={v[0] if v else None}")

    # (4) the two chain-1 corruptions are really rejected
    ev, sw, aw = _fixture(2.5, 2.5, 3.5, 3.5)
    controls = chain1_negative_controls(ev, sw, aw)
    check("tampered capacity_bits is rejected",
          any(c["control"].startswith("tampered") and c["rejected"] is True
              for c in controls), controls)
    check("overlapping available window is rejected",
          any(c["control"].startswith("overlapping") and c["rejected"] is True
              for c in controls), controls)

    # (5) the comparator is two-sided: agreement on an identical reading,
    #     and a reported mismatch when the production reading is tampered with
    ev, sw, aw = _fixture(2.5, 2.5, 3.5, 3.5)
    independent = chain1(ev, sw, aw, {})
    identical_reading = {
        r["link_id"]: {"served_bits": r["served_bits"],
                       "capacity_bits": r["capacity_bits"],
                       "available_capacity_bits": r["available_capacity_bits"],
                       "utilization": r["utilization"]}
        for r in independent["rows"]}
    c1 = chain1(ev, sw, aw, identical_reading)
    check("chain-1 recomputation reports every link and agrees with an identical reading",
          c1["links_total"] == 2 and c1["mismatch_total"] == 0,
          f"links={c1['links_total']} mismatches={c1['mismatch_total']}")

    tampered = json.loads(json.dumps(identical_reading))
    first_link = sorted(tampered)[0]
    tampered[first_link]["utilization"] = tampered[first_link]["utilization"] + 0.25
    c1b = chain1(ev, sw, aw, tampered)
    check("a tampered production utilisation is reported as a mismatch",
          c1b["mismatch_total"] == 1
          and c1b["mismatches"][0]["field"] == "utilization",
          f"mismatches={c1b['mismatch_total']} first={c1b['mismatches'][:1]}")

    # (6) fate-appropriate truncation: an undelivered packet gets no e2e formula
    ev, sw, aw = _fixture(2.5, 2.5, 3.5, 3.5)
    ev = [e for e in ev if e["kind"] != "delivered"]
    c2 = chain2(ev, sw, {}, 1.0, 3.5)
    check("undelivered packet gets truncated accounting, not the delivered formula",
          c2["delivered_packets"] == 0 and c2["undelivered_packets"] == 1
          and c2["truncation_by_fate"]
          and all(b["e2e_defined"] == 0 and b["residual_defined"] == 0
                  and b["gaps_defined"] == 0
                  for b in c2["truncation_by_fate"].values()),
          f"delivered={c2['delivered_packets']} undelivered={c2['undelivered_packets']} "
          f"buckets={list(c2['truncation_by_fate'])}")

    _assert_independent()
    check("harness independence guard holds", True, "ast walk found no metrics import")

    ok = all(r["ok"] for r in results)
    report = {
        "schema": "leo-sim-step5-selftest/v1",
        "tolerance_contract_s": TOL,
        "compute_delay_s_synthetic": 1.0,
        "checks": results,
        "all_checks_ok": ok,
        "existing_fixture": False,
        "issued_by": args_issued_by(_args),
    }
    out = Path(_args.out)
    if out.parent != Path("."):
        out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2,
                              sort_keys=True) + "\n", encoding="utf-8")
    log(json.dumps({"wrote": str(out), "all_checks_ok": ok,
                    "failed": [r["check"] for r in results if not r["ok"]]},
                   sort_keys=True))
    return 0 if ok else 1


def args_issued_by(_args):
    return "step5_recompute selftest (synthetic fixtures, not a formal run)"


def _assert_independent():
    """Fail loud if this harness ever gains a production-metrics dependency."""
    import ast
    with open(__file__, "r", encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=__file__)
    for node in ast.walk(tree):
        names = ()
        if isinstance(node, ast.Import):
            names = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names = tuple([node.module or ""] + [alias.name for alias in node.names])
        for name in names:
            if name == "metrics" or name.endswith(".metrics"):
                raise RuntimeError(
                    "step5_recompute must not import CODE.leo_sim.metrics: the "
                    "production reading is data to diff against, never code to call")
    for value in globals().values():
        if getattr(value, "__name__", "") in ("metrics", "CODE.leo_sim.metrics"):
            raise RuntimeError("step5_recompute holds a reference to production metrics")


_assert_independent()

if __name__ == "__main__":
    _args = parse_args(sys.argv[1:])
    raise SystemExit(_args.fn(_args))
