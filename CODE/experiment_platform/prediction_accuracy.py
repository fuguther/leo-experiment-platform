"""Prediction-vs-truth accuracy from the same-run target probe.

The probe (`leo_sim/target_probe.py`) reads, for every audited decision and
candidate direction, the live resource state at the candidate's evaluation
target instant.  Each record therefore carries BOTH sides of the question:

    prediction : predicted_bits / service_rate_bps / predicted_work_s
    truth      : truth.work_ahead_bits (queued + in-service remaining)
    timing     : eval_target_s, state_measured_at_s, state_used_at_s, t_commit_s

This module aggregates those records; it is audit-only and never feeds routing.
It deliberately reports COVERAGE next to accuracy: a metric computed on 7% of
decisions must never be quoted as the predictor's accuracy for the run.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path


SCHEMA = "prediction-accuracy/v1"


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _bucket(value, edges):
    if value is None:
        return "unknown"
    for lo, hi, label in edges:
        if value < hi:
            return label
    return edges[-1][2]


AGE_EDGES = ((0.0, 0.010, "0-10ms"), (0.010, 0.025, "10-25ms"),
             (0.025, 0.050, "25-50ms"), (0.050, float("inf"), "50ms+"))
HORIZON_EDGES = ((0.0, 0.005, "0-5ms"), (0.005, 0.010, "5-10ms"),
                 (0.010, 0.025, "10-25ms"), (0.025, float("inf"), "25ms+"))


def load_rows(paths):
    rows = []
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    return rows


def _timing(row):
    """Return (age_at_target_s, horizon_s) or (None, None).

    age_at_target = eval_target - state_measured_at  (how old the state is at
    the instant we are predicting for); horizon = eval_target - state_used_at
    (how far ahead the prediction reaches beyond the query instant).
    """
    target = row.get("eval_target_s")
    measured = row.get("state_measured_at_s")
    used = row.get("state_used_at_s")
    age = None
    horizon = None
    if _finite(target) and _finite(measured):
        age = float(target) - float(measured)
    if _finite(target) and _finite(used):
        horizon = float(target) - float(used)
    return age, horizon


def _error(row):
    truth = row.get("truth") or {}
    predicted = row.get("predicted_bits")
    work = truth.get("work_ahead_bits")
    if not _finite(predicted) or not _finite(work):
        return None
    return float(predicted) - float(work)


def _stats(errors, predicted_values, truths):
    n = len(errors)
    if n == 0:
        return {"n": 0}
    abs_errors = [abs(e) for e in errors]
    return {
        "n": n,
        "bias_mean_bits": statistics.fmean(errors),
        "mae_bits": statistics.fmean(abs_errors),
        "median_abs_bits": statistics.median(abs_errors),
        "p90_abs_bits": sorted(abs_errors)[int(0.9 * (n - 1))],
        "max_abs_bits": max(abs_errors),
        "truth_nonzero_rate": (sum(1 for t in truths if t) / n),
        "pred_nonzero_rate": (sum(1 for p in predicted_values if p) / n),
    }


def _is_extra_horizon(row):
    offset = row.get("horizon_offset_s")
    return (isinstance(offset, (int, float))
            and not isinstance(offset, bool)
            and float(offset) != 0.0)


def _is_forced(row):
    return bool(row.get("forced"))


def _identity(rows):
    """Check optional run/config identity; incompatible inputs fail closed.

    Probe rows are produced per arm/run.  When they carry run_id or
    config_sha256, mixing different values silently would make the reported
    accuracy describe no single run.  Rows produced by older probes do not
    carry these fields; in that case the summary must say so rather than imply
    identity was verified.
    """
    def values(field):
        return sorted({str(row[field]) for row in rows
                       if row.get(field) not in (None, "")})
    configs = values("config_sha256")
    runs = values("run_id")
    if len(configs) > 1 or len(runs) > 1:
        raise ValueError(
            "probe identity mismatch: config_sha256=%r run_id=%r"
            % (configs, runs))
    return {
        "config_sha256": configs[0] if configs else None,
        "run_id": runs[0] if runs else None,
        "verified": bool(configs or runs),
        "note": ("rows do not carry run/config identity; do not treat this as "
                 "a single-run accuracy claim" if not (configs or runs) else
                 "identity fields are consistent across inputs"),
    }


def _labeled(row):
    return (isinstance(row.get("truth"), dict)
            and _finite(row["truth"].get("work_ahead_bits"))
            and _finite(row.get("predicted_bits")))


def summarize(rows):
    """Aggregate probe rows into coverage + accuracy cuts.

    Only the BASE probe rows (the prediction made for the original eval
    target) enter the accuracy estimates.  Rows at +horizon are the same
    prediction scored against a later truth; they measure drift, not forecast
    accuracy, and mixing them into ``overall`` would let truth drift be
    reported as predictor error.  Forced/debug decisions are excluded from
    accuracy and coverage; they are interventions, not samples of the online
    policy.  ``coverage`` is the labeled share of eligible BASE rows; skipped
    or overflow rows are not in the JSONL and must be read from the probe
    summary.
    """
    forced = [r for r in rows if _is_forced(r)]
    nonforced = [r for r in rows if not _is_forced(r)]
    base = [r for r in nonforced if not _is_extra_horizon(r)]
    extra = [r for r in nonforced if _is_extra_horizon(r)]
    labeled_base = [r for r in base if _labeled(r)]
    labeled_extra = [r for r in extra if _labeled(r)]
    labeled = labeled_base
    reasons = {}
    for r in rows:
        reason = r.get("missing_reason")
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
    document = {
        "schema": SCHEMA,
        "rows": len(rows),
        "base_rows": len(base),
        "extra_horizon_rows": len(extra),
        "forced_rows": len(forced),
        "labeled": len(labeled_base),
        "labeled_extra_horizon": len(labeled_extra),
        "coverage": (len(labeled_base) / len(base)) if base else None,
        "coverage_scope": (
            "labeled share of eligible BASE rows; forced rows and +horizon "
            "rows are excluded; skipped/overflow rows are only in the probe "
            "summary and are not part of this denominator"),
        "identity": _identity(rows),
        "missing_reasons": dict(sorted(reasons.items())),
        "horizon_note": (
            "+horizon rows reuse the prediction made for the base eval target "
            "and compare it with a later truth; they are drift diagnostics, "
            "not forecast accuracy"),
        "overall": _stats([_error(r) for r in labeled_base],
                          [r["predicted_bits"] for r in labeled_base],
                          [float(r["truth"]["work_ahead_bits"]) for r in labeled_base]),
        "by_arm": {},
        "by_direction": {},
        "by_age_bucket": {},
        "by_horizon_bucket": {},
        "by_selection": {},
        "by_horizon_offset": {},
    }
    for arm in sorted({str(r.get("arm")) for r in labeled_base}):
        subset = [r for r in labeled_base if str(r.get("arm")) == arm]
        document["by_arm"][arm] = _stats(
            [_error(r) for r in subset],
            [r["predicted_bits"] for r in subset],
            [float(r["truth"]["work_ahead_bits"]) for r in subset])
    for direction in sorted({str(r.get("direction")) for r in labeled_base}):
        subset = [r for r in labeled_base if str(r.get("direction")) == direction]
        document["by_direction"][direction] = _stats(
            [_error(r) for r in subset],
            [r["predicted_bits"] for r in subset],
            [float(r["truth"]["work_ahead_bits"]) for r in subset])
    for label, selector, edges in (
            ("by_age_bucket", lambda r: _timing(r)[0], AGE_EDGES),
            ("by_horizon_bucket", lambda r: _timing(r)[1], HORIZON_EDGES)):
        groups = {}
        for row in labeled_base:
            key = _bucket(selector(row), edges)
            groups.setdefault(key, []).append(row)
        for key in sorted(groups):
            subset = groups[key]
            document[label][key] = _stats(
                [_error(r) for r in subset],
                [r["predicted_bits"] for r in subset],
                [float(r["truth"]["work_ahead_bits"]) for r in subset])
    # Drift diagnostic only: base + extra rows, keyed by offset.  A consumer
    # must not compare offset+ entries with overall as if both were accuracy.
    all_labeled = labeled_base + labeled_extra
    offsets = sorted({r.get("horizon_offset_s") for r in all_labeled
                      if isinstance(r.get("horizon_offset_s"), (int, float))})
    for offset in offsets:
        subset = [r for r in all_labeled
                  if r.get("horizon_offset_s") == offset]
        document["by_horizon_offset"]["+%.3fs" % offset] = _stats(
            [_error(r) for r in subset],
            [r["predicted_bits"] for r in subset],
            [float(r["truth"]["work_ahead_bits"]) for r in subset])
    for key, flag in (("chosen", True), ("not_chosen", False)):
        subset = [r for r in labeled_base if bool(r.get("selected")) is flag]
        if subset:
            document["by_selection"][key] = _stats(
                [_error(r) for r in subset],
                [r["predicted_bits"] for r in subset],
                [float(r["truth"]["work_ahead_bits"]) for r in subset])
    return document

def _print(document, stream=sys.stdout):
    def line(*parts):
        stream.write("  ".join(str(p) for p in parts) + "\n")

    line("rows=%d base=%d extra_horizon=%d forced_excluded=%d labeled_base=%d coverage=%s" % (
        document["rows"], document["base_rows"], document["extra_horizon_rows"],
        document["forced_rows"], document["labeled"],
        "n/a" if document["coverage"] is None
        else "%.2f%%" % (100 * document["coverage"])))
    line("coverage_scope:", document["coverage_scope"])
    if not document["identity"]["verified"]:
        line("identity: UNVERIFIED (rows carry no run/config identity)")
    if document["missing_reasons"]:
        line("missing:", document["missing_reasons"])
    line("")
    line("%-12s %6s %12s %12s %12s %8s %8s" % (
        "group", "n", "bias(bit)", "MAE(bit)", "median|e|", "truth>0", "pred>0"))
    for group in ("overall",):
        stats = document[group]
        line("%-12s %6d %12.1f %12.1f %12.1f %7.2f%% %7.2f%%" % (
            group, stats["n"], stats["bias_mean_bits"], stats["mae_bits"],
            stats["median_abs_bits"], 100 * stats["truth_nonzero_rate"],
            100 * stats["pred_nonzero_rate"]))
    for section, label in (("by_arm", "arm"), ("by_direction", "direction"),
                           ("by_age_bucket", "age"),
                           ("by_horizon_bucket", "horizon"),
                           ("by_horizon_offset", "offset"),
                           ("by_selection", "selection")):
        for key, stats in document[section].items():
            line("%-12s %6d %12.1f %12.1f %12.1f %7.2f%% %7.2f%%" % (
                "%s:%s" % (label, key), stats["n"], stats["bias_mean_bits"],
                stats["mae_bits"], stats["median_abs_bits"],
                100 * stats["truth_nonzero_rate"],
                100 * stats["pred_nonzero_rate"]))
    line("")
    line("bias = predicted - truth; truth = queued+in_service at eval target")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prediction-vs-truth accuracy from same-run target probe")
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    rows = load_rows(args.inputs)
    document = summarize(rows)
    _print(document)
    if args.json is not None:
        args.json.write_text(json.dumps(document, indent=1, sort_keys=True),
                             encoding="utf-8")
        print("wrote %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
