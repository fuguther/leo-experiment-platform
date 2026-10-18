#!/usr/bin/env python3
"""Score the same-run prediction/truth probe and check its correspondences.

Inputs (all from ONE run): target_probe.jsonl and arms/<arm>/decisions.jsonl.gz.
Checks: coverage and missing reasons; prediction/score consistency; target and
identity correspondence; error statistics; statistical vs forced cohorts.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import json
import statistics
from pathlib import Path


def _rows(path):
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _decisions(arm_dir):
    path = Path(arm_dir) / "decisions.jsonl.gz"
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def load(probe_path, arm_dir):
    rows = list(_rows(probe_path))
    dec = {}
    for d in _decisions(arm_dir):
        dec[(d.get("pid"), d.get("decision_id"))] = d
    for row in rows:
        row["_decision"] = dec.get((row.get("pid"), row.get("decision_id")))
    return rows


def pct(a, b):
    return 0.0 if not b else 100.0 * a / b


def stats(values):
    if not values:
        return {"n": 0}
    a = [abs(v) for v in values]
    return {"n": len(values), "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "mean_abs": statistics.fmean(a),
            "median_abs": statistics.median(a),
            "p90_abs": sorted(a)[int(0.9 * (len(a) - 1))]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", required=True)
    ap.add_argument("--arms-root", required=True)
    ap.add_argument("--arm", default="candidate")
    ap.add_argument("--stride", type=int, default=10)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    arm_dir = Path(args.arms_root) / args.arm
    rows = load(args.probe, arm_dir)
    summary = {"schema": "target-probe-verification/v1",
               "probe": args.probe, "arm": args.arm, "rows": len(rows)}

    reasons = collections.Counter()
    labelled = 0
    for r in rows:
        if r.get("truth"):
            labelled += 1
        else:
            reasons[r.get("missing_reason") or "unknown"] += 1
    summary["coverage"] = {"labelled": labelled,
                           "coverage_pct": pct(labelled, len(rows)),
                           "missing": dict(sorted(reasons.items()))}

    mismatched = 0
    checked = 0
    for r in rows:
        pred, rate = r.get("predicted_bits"), r.get("service_rate_bps")
        work = r.get("predicted_work_s")
        if pred is None or not rate or work is None:
            continue
        checked += 1
        if abs(float(pred) / float(rate) - float(work)) > 1e-9 * max(1.0, abs(float(work))):
            mismatched += 1
    summary["prediction_score_consistency"] = {"checked": checked,
                                               "mismatched": mismatched}

    sampled_instant_mismatch = 0
    persisted_eta_mismatch = 0
    identity_mismatch = 0
    generation_mismatch = 0
    checked_target = 0
    for r in rows:
        if r.get("sampled_at_s") is None:
            continue
        checked_target += 1
        if abs(float(r["sampled_at_s"]) - float(r["eval_target_s"])) > 1e-9:
            sampled_instant_mismatch += 1
        d = r.get("_decision")
        if not d:
            continue
        ta = ((d.get("observation_at_start") or {}).get("time_alignment") or {})
        eta = (ta.get("eta_targets") or {}).get(r["direction"])
        if eta is not None and abs(float(eta) - float(r["eval_target_s"])) > 1e-12:
            persisted_eta_mismatch += 1
        used = (ta.get("used_state") or {}).get(r["direction"]) or {}
        detail = (((d.get("four_direction_audit") or {}).get("candidate_details")
                   or {}).get(r["direction"]) or {})
        expect = used.get("resource_id") or detail.get("resource_id")
        if expect is not None and r.get("resource_id") != expect:
            identity_mismatch += 1
        if r.get("truth") and r["truth"].get("resource_id") != r.get("resource_id"):
            identity_mismatch += 1
        if (r.get("truth") and r.get("generation") is not None
                and r["truth"].get("generation") != r["generation"]):
            generation_mismatch += 1
    summary["correspondence"] = {
        "checked": checked_target,
        "sampled_instant_mismatch": sampled_instant_mismatch,
        "persisted_eta_mismatch": persisted_eta_mismatch,
        "resource_identity_mismatch": identity_mismatch,
        "generation_mismatch": generation_mismatch}

    ok = [r for r in rows if r.get("truth")
          and r["truth"].get("work_ahead_bits") is not None
          and r.get("predicted_bits") is not None]
    errs = [float(r["predicted_bits"]) - float(r["truth"]["work_ahead_bits"])
            for r in ok]
    labels = [float(r["truth"]["work_ahead_bits"]) for r in ok]
    preds = [float(r["predicted_bits"]) for r in ok]
    summary["errors_bits"] = {"signed": stats(errs), "predicted": stats(preds),
                              "label": stats(labels)}
    nz_label = sum(1 for v in labels if v > 0)
    nz_pred = sum(1 for v in preds if v > 0)
    summary["label_nonzero"] = {"count": nz_label, "pct": pct(nz_label, len(labels))}
    summary["predicted_nonzero"] = {"count": nz_pred, "pct": pct(nz_pred, len(preds))}

    stat_rows = [r for i, r in enumerate(ok) if args.stride <= 1 or i % args.stride == 0]
    forced = [r for r in rows if r.get("forced")]
    summary["cohorts"] = {
        "statistical": {
            "rows": len(stat_rows), "stride": args.stride,
            "mean_abs_bits": (statistics.fmean(
                [abs(float(r["predicted_bits"]) - float(r["truth"]["work_ahead_bits"]))
                 for r in stat_rows]) if stat_rows else None)},
        "forced_debug": {"rows": len(forced)},
        "rule": ("systematic stride over labelled rows; a forced/debug row is",
                 " counted separately and never mixed into the sample")}

    by_sel = collections.defaultdict(list)
    for r in ok:
        by_sel["selected" if r.get("selected") else "rejected"].append(
            abs(float(r["predicted_bits"]) - float(r["truth"]["work_ahead_bits"])))
    summary["by_selection"] = {k: {"n": len(v),
                                   "mean_abs_bits": (statistics.fmean(v) if v else None)}
                               for k, v in sorted(by_sel.items())}

    text = json.dumps(summary, indent=1, sort_keys=True, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
