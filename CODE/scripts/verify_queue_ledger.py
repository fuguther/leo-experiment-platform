#!/usr/bin/env python3
"""Verify the opt-in queue ledger against the simulator's own accounting.

Read-only.  This is the acceptance gate for T1-QUEUE-LEDGER; it never runs the
simulator and never writes into a run directory.

It answers three questions from artifacts alone:
  1. IDENTITY -- is the instrumented run's existing content byte-for-byte the
     baseline run's content (excluding the new ledger block and argv)?
  2. AREA -- does an independent reconstruction of ``queue_area_bits_s`` from
     the level series equal the kernel's own accumulator, per queue and per
     stage, with the full numeric error printed (not a rounded percentage)?
  3. CONSERVATION -- for every forwarding queue, does
         Q_end = Q_start + entered_bits - left_bits
     hold, where entered/left come from the verbatim packet_events and the
     queue level comes from the level series?

Usage:
  python3 -B CODE/scripts/verify_queue_ledger.py \
      --baseline-run A.json --instrumented-run B.json \
      --ledger-dir DIR --prefix STEM [--out report.json]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from CODE.experiment_platform import replay_codec  # noqa: E402

STAGES = ("uplink", "downlink", "holding", "isl_data", "isl_ctrl")

# The only fields allowed to differ between the two runs: the new ledger block
# and the CLI argv that selected it.
EXCLUDED_TOP = ("queue_ledger", "queue_ledger_audit")


def load_run(path):
    with open(path, "r", encoding="utf-8") as handle:
        return replay_codec.load_graph(handle)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      default=str)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def arms_of(document):
    return {arm.get("arm"): arm for arm in document.get("document", {})
            .get("arms", [])}


def stable_arm_view(arm):
    return {k: v for k, v in arm.items() if k not in EXCLUDED_TOP}


def read_levels(path):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def read_jsonl_gz(path):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def reconstruct_area(level_rows):
    """Independent trapezoid over the piecewise-constant level series."""
    area = defaultdict(float)
    level = defaultdict(int)
    last = defaultdict(float)
    transitions = defaultdict(int)
    for row in level_rows:
        if row.get("kind") != "level":
            continue
        queue = row["queue"]
        t = float(row["t"])
        area[queue] += level[queue] * (t - last[queue])
        last[queue] = t
        level[queue] = int(row["bits"])
        transitions[queue] += 1
    return dict(area), dict(level), dict(last), dict(transitions)


def stage_of(queue):
    return queue.split(":", 1)[0]


def conservation(packet_events, windows, final_level):
    final_level = _aggregate_level(final_level)
    """Per-queue bits conservation from the verbatim packet events.

    leaves are derived three ways, because the platform has three exit kinds:
      * service_start on the SAME (stage, link_id) -- uplink/downlink/isl;
      * for holding, the next queue_enter of the same pid that is not holding;
      * a terminal fate (delivered / lost) -- leaves the queue at that instant.
    Returns per-queue {entered_bits, left_bits, end_bits, ok}.
    """
    bits_of = {}
    enters = defaultdict(list)          # queue -> [(t, pid)]
    leaves = defaultdict(list)          # queue -> [(t, pid)]
    for event in packet_events:
        kind = event.get("kind")
        if kind == "packet_emitted":
            bits_of[int(event["pid"])] = int(event["bits"])
        elif kind == "queue_enter":
            queue = event.get("queue")
            key = _queue_key(queue, event.get("link_id"))
            enters[key].append((float(event["at"]), int(event["pid"])))
        elif kind == "service_start":
            key = _queue_key(event.get("stage"), event.get("link_id"))
            leaves[key].append((float(event["at"]), int(event["pid"])))

    # holding leaves when the held packet is enqueued elsewhere or delivered
    for key, rows in list(enters.items()):
        if not key.startswith("holding:"):
            continue
        next_enqueue = defaultdict(list)
        for other_key, other in enters.items():
            if other_key == key:
                continue
            for t, pid in other:
                next_enqueue[pid].append(t)
        for event in packet_events:
            if event.get("kind") == "delivered":
                next_enqueue[int(event["pid"])].append(float(event["at"]))
        for t, pid in rows:
            later = [x for x in next_enqueue.get(pid, []) if x >= t]
            if later:
                leaves[key].append((min(later), pid))

    report = {}
    for key in sorted(set(enters) | set(leaves)):
        entered = sum(bits_of.get(pid, 0) for _, pid in enters.get(key, []))
        left = sum(bits_of.get(pid, 0) for _, pid in leaves.get(key, []))
        end = int(final_level.get(key, 0))
        start = 0
        ok = (entered - left == end - start)
        report[key] = {"entered_bits": entered, "left_bits": left,
                       "start_bits": start, "end_bits": end,
                       "conserved": bool(ok)}
    return report


def _queue_key(queue, link_id):
    """Normalise an event's (queue, link_id) to the ledger's physical qid.

    Three naming mismatches have to be reconciled, otherwise entered and left
    sets never meet and every queue looks unconserved:
      * uplink enqueue names the pending queue ``gsl:uplink:pending:<cell>``
        while the service names ``gsl:uplink:<sat>:<cell>``;
      * the downlink queue is shared per satellite, so its qid is
        ``downlink:sat:<sat>``, not per destination cell;
      * the ISL qid carries a link generation, which the packet events do not.
    """
    if queue == "isl":
        sat, peer = link_id.split(":")[1], link_id.split(":")[2]
        return f"isl_data:isl:{sat}:{peer}"
    if queue == "uplink":
        cell = link_id.split("gsl:uplink:pending:", 1)[1] \
            if "pending:" in link_id else link_id.split(":", 3)[-1]
        return f"uplink:cell:{cell}"
    if queue == "downlink":
        sat = link_id.split(":")[2]
        return f"downlink:sat:{sat}"
    if queue == "holding":
        return f"holding:sat:{link_id.split(':')[-1]}"
    return f"{queue}:{link_id}"


def _physical(qid):
    """Strip the ISL generation suffix: conservation is per physical link."""
    marker = ":gen"
    return qid.split(marker, 1)[0] if marker in qid else qid


def _aggregate_level(final_level):
    out = defaultdict(int)
    for qid, bits in final_level.items():
        out[_physical(qid)] += int(bits)
    return dict(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", required=True)
    parser.add_argument("--instrumented-run", required=True)
    parser.add_argument("--ledger-dir", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    base = load_run(args.baseline_run)
    inst = load_run(args.instrumented_run)
    base_arms, inst_arms = arms_of(base), arms_of(inst)

    report = {"schema": "t1-queue-ledger-verification/v1",
              "baseline_run": os.path.abspath(args.baseline_run),
              "instrumented_run": os.path.abspath(args.instrumented_run),
              "arms": {}}
    identity_ok = True
    for arm_name in sorted(set(base_arms) & set(inst_arms)):
        b, i = base_arms[arm_name], inst_arms[arm_name]
        b_view, i_view = stable_arm_view(b), stable_arm_view(i)
        b_digest, i_digest = digest(b_view), digest(i_view)
        dec_b = digest(b.get("routing_audit_log", {}).get("decision_records"))
        dec_i = digest(i.get("routing_audit_log", {}).get("decision_records"))
        att_b = digest(b.get("routing_audit_log", {}).get("attempt_records"))
        att_i = digest(i.get("routing_audit_log", {}).get("attempt_records"))
        same = (b_digest == i_digest and dec_b == dec_i and att_b == att_i)
        identity_ok = identity_ok and same
        report["arms"][arm_name] = {
            "stable_digest_baseline": b_digest,
            "stable_digest_instrumented": i_digest,
            "decision_records_digest_baseline": dec_b,
            "decision_records_digest_instrumented": dec_i,
            "attempt_records_digest_baseline": att_b,
            "attempt_records_digest_instrumented": att_i,
            "identical": bool(same),
        }
    report["identity_pass"] = bool(identity_ok)

    # ---- area reconstruction (one ledger per arm) --------------------
    area_report = {}
    conservation_pass = True
    for arm_name, arm in sorted(inst_arms.items()):
        prefix = f"{args.prefix}-{arm_name}"
        files = {"levels": os.path.join(args.ledger_dir,
                                        f"{prefix}-queue-levels.jsonl.gz"),
                 "packet_events": os.path.join(
                     args.ledger_dir, f"{prefix}-packet-events.jsonl.gz"),
                 "service_windows": os.path.join(
                     args.ledger_dir, f"{prefix}-service-windows.jsonl.gz")}
        missing = [name for name, path in files.items()
                   if not os.path.exists(path)]
        if missing:
            area_report[arm_name] = {"status": "MISSING", "missing": missing}
            conservation_pass = False
            continue
        level_rows = read_levels(files["levels"])
        area, final_level, last_t, transitions = reconstruct_area(level_rows)
        native = ((arm.get("outcome") or {}).get("queue_area_bits_s")
                  or arm.get("queue_area_bits_s") or {})
        stages = {}
        worst_abs = 0.0
        worst_rel = 0.0
        for stage in STAGES:
            rebuilt = sum(v for q, v in area.items() if stage_of(q) == stage)
            native_v = float(native.get(stage, 0.0))
            abs_err = abs(rebuilt - native_v)
            denom = max(abs(native_v), abs(rebuilt), 1e-12)
            rel = abs_err / denom
            worst_abs = max(worst_abs, abs_err)
            worst_rel = max(worst_rel, rel)
            stages[stage] = {"native": native_v, "rebuilt": rebuilt,
                             "abs_error": abs_err, "rel_error": rel,
                             "queues": sum(1 for q in area
                                           if stage_of(q) == stage)}
        events = read_jsonl_gz(files["packet_events"])
        windows = read_jsonl_gz(files["service_windows"])
        cons = conservation(events, windows, final_level)
        bad = {k: v for k, v in cons.items() if not v["conserved"]}
        if bad:
            conservation_pass = False
        area_report[arm_name] = {
            "status": "OK",
            "stage_area": stages,
            "worst_abs_error": worst_abs,
            "worst_rel_error": worst_rel,
            "queues": len(area),
            "level_rows": len(level_rows),
            "packet_event_rows": len(events),
            "service_window_rows": len(windows),
            "queues_with_transitions": len(transitions),
            "conservation": cons,
            "conservation_failures": bad,
        }
    report["area"] = area_report
    report["area_pass"] = all(
        v.get("status") == "OK" and v.get("worst_abs_error", 1.0) < 1e-6
        for v in area_report.values())
    report["conservation_pass"] = bool(conservation_pass)
    report["pass"] = bool(report["identity_pass"] and report["area_pass"]
                          and report["conservation_pass"])

    text = json.dumps(report, indent=1, sort_keys=True, default=str)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
