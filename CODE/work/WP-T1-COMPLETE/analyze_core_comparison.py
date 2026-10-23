"""Paired stale/now/common/candidate comparison from calibration-style runs.

The bounded calibration entry records, per arm, the full per-decision rows and
the gzipped kernel result.  This script turns those into the comparison the
status-time-misalignment question actually needs:

1. per-arm business outcome (offered / delivered / D4 not delivered / capped
   loss / delay distribution / queue areas / mean state age);
2. per-packet paired differences between arms on the SAME emitted packet;
3. per-decision divergence: how often the arms choose a different direction,
   and how often their candidate rankings differ;
4. the offline hindsight bound: on packets where the arms disagree, which arm
   was best, i.e. how much of the gap is recoverable at all.

Arms are matched by packet id (emitted ledger) and by (pid, decision_id) for
decisions.  Read-only.

Usage:
  python3 -B CODE/work/WP-T1-COMPLETE/analyze_core_comparison.py \
      --arm stale=<dir> --arm now=<dir> ... [--json out.json] [--md out.md]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
from pathlib import Path


def _load_gzip_json(path: Path):
    if not path.is_file():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, json.JSONDecodeError, EOFError):
        return None


def _iter_gzip_jsonl(path: Path):
    if not path.is_file():
        return
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except (OSError, EOFError):
        return


def _find(root: Path, name: str):
    direct = root / name
    if direct.is_file():
        return direct
    matches = sorted(root.glob(f"cal/*/arms/*/{name}"))
    return matches[0] if matches else None


def _emissions(result):
    out = {}
    for event in (result or {}).get("packet_events") or []:
        if isinstance(event, dict) and event.get("kind") == "packet_emitted":
            pid, at = event.get("pid"), event.get("at")
            if pid is not None and isinstance(at, (int, float)):
                out[int(pid)] = float(at)
    return out


def _delivery_delays(result, deadline_s):
    emissions = _emissions(result)
    deliveries = (result or {}).get("deliveries") or {}
    delays = {}
    for pid, emitted in emissions.items():
        record = deliveries.get(str(pid))
        delivered_at = record.get("delivered_at") if isinstance(record, dict) else None
        if isinstance(delivered_at, (int, float)) and delivered_at >= emitted:
            delays[pid] = float(delivered_at) - emitted
    return emissions, delays


def _pct(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def _fate_counts(result):
    counts = (result or {}).get("fate_counts") or {}
    return {str(k): v for k, v in counts.items() if isinstance(v, (int, float))}


def load_arm(name: str, root: Path, deadline_s: float) -> dict:
    result = _load_gzip_json(_find(root, "result.json.gz"))
    decisions_path = _find(root, "decisions.jsonl.gz")
    decisions = {}
    complete_decisions = 0
    ranking_missing = 0
    for row in _iter_gzip_jsonl(decisions_path) or []:
        if row.get("kind") != "forward":
            continue
        key = (row.get("pid"), row.get("decision_id"))
        observation = row.get("observation_at_start") or {}
        audit = observation.get("time_alignment") or {}
        scores = audit.get("scores") or {}
        complete = 0
        for direction, score in scores.items():
            if not isinstance(score, dict):
                continue
            total = score.get("total_s")
            if score.get("missing") == [] and isinstance(total, (int, float)) \
                    and math.isfinite(float(total)):
                complete += 1
        if complete >= 2:
            complete_decisions += 1
        if audit.get("fallback_directions"):
            ranking_missing += 1
        decisions[key] = {
            "chosen": row.get("chosen"),
            "candidates": tuple(row.get("candidates") or ()),
            "ranking": tuple(audit.get("ranking") or ()),
            "complete": complete,
            "t": row.get("t"),
        }
    emissions, delays = _delivery_delays(result, deadline_s)
    offered = len(emissions)
    delivered_by_d4 = sum(1 for pid in emissions
                          if delays.get(pid) is not None and delays[pid] <= deadline_s)
    queue = (result or {}).get("queue_area_bits_s") or {}
    return {
        "arm": name,
        "dir": str(root),
        "offered": offered,
        "delivered": len(delays),
        "delivered_by_d4": delivered_by_d4,
        "d4_not_delivered_proportion":
            (offered - delivered_by_d4) / offered if offered else None,
        "capped_loss": (sum(min(delays.get(pid, math.inf) / deadline_s, 1.0)
                            for pid in emissions) / offered) if offered else None,
        "delay_mean_s": statistics.fmean(delays.values()) if delays else None,
        "delay_p50_s": _pct(list(delays.values()), 0.50),
        "delay_p95_s": _pct(list(delays.values()), 0.95),
        "delay_max_s": max(delays.values()) if delays else None,
        "fate_counts": _fate_counts(result),
        "queue_area_bits_s": {k: v for k, v in queue.items()},
        "decision_rows": len(decisions),
        "decisions_with_two_complete_candidates": complete_decisions,
        "emissions": emissions,
        "delays": delays,
        "decision_map": decisions,
        "ranking_fallback_rows": ranking_missing,
    }


def compare(arms: list) -> dict:
    names = [arm["arm"] for arm in arms]
    common_pids = None
    for arm in arms:
        pids = set(arm["emissions"])
        common_pids = pids if common_pids is None else (common_pids & pids)
    common_pids = sorted(common_pids or ())

    per_packet = []
    for pid in common_pids:
        row = {"pid": pid}
        for arm in arms:
            delay = arm["delays"].get(pid)
            row[arm["arm"]] = None if delay is None else round(delay, 9)
        per_packet.append(row)

    pairwise = {}
    for i, left in enumerate(arms):
        for right in arms[i + 1:]:
            diffs, left_only, right_only, both = [], 0, 0, 0
            for pid in common_pids:
                ld, rd = left["delays"].get(pid), right["delays"].get(pid)
                if ld is None and rd is None:
                    both += 1
                    continue
                if ld is None:
                    right_only += 1
                    continue
                if rd is None:
                    left_only += 1
                    continue
                diffs.append(ld - rd)
            pairwise[f"{left['arm']}_minus_{right['arm']}"] = {
                "paired_delivered_packets": len(diffs),
                "mean_delay_diff_s": statistics.fmean(diffs) if diffs else None,
                "median_delay_diff_s": statistics.median(diffs) if diffs else None,
                "left_faster": sum(1 for d in diffs if d < 0),
                "right_faster": sum(1 for d in diffs if d > 0),
                "equal": sum(1 for d in diffs if d == 0),
                "left_delivered_only": left_only,
                "right_delivered_only": right_only,
                "neither_delivered": both,
            }

    keys = None
    for arm in arms:
        arm_keys = set(arm["decision_map"])
        keys = arm_keys if keys is None else (keys & arm_keys)
    keys = sorted(keys, key=lambda k: (k[0] or -1, k[1] or -1))

    divergences = {name: 0 for name in names}
    ranking_divergences = {name: 0 for name in names}
    comparable = 0
    sample = []
    for key in keys:
        rows = [arm["decision_map"][key] for arm in arms]
        if not any(row["complete"] >= 2 for row in rows):
            continue
        comparable += 1
        chosen = {row["chosen"] for row in rows}
        rankings = {row["ranking"] for row in rows}
        if len(chosen) > 1:
            for arm, row in zip(arms, rows):
                if row["chosen"] != rows[0]["chosen"]:
                    divergences[arm["arm"]] += 1
            if len(sample) < 20:
                sample.append({"key": list(key),
                               "chosen": {arm["arm"]: row["chosen"]
                                          for arm, row in zip(arms, rows)},
                               "ranking": {arm["arm"]: list(row["ranking"])
                                           for arm, row in zip(arms, rows)}})
        if len(rankings) > 1:
            for arm, row in zip(arms, rows):
                if row["ranking"] != rows[0]["ranking"]:
                    ranking_divergences[arm["arm"]] += 1

    hindsight = {}
    for arm in arms:
        wins = sum(1 for row in per_packet
                   if row.get(arm["arm"]) is not None
                   and all(row.get(other["arm"]) is None
                           or row[arm["arm"]] <= row[other["arm"]]
                           for other in arms))
        hindsight[arm["arm"]] = wins

    return {
        "arms": names,
        "common_offered_packets": len(common_pids),
        "pairwise": pairwise,
        "decisions_comparable": comparable,
        "decision_divergences_vs_first_arm": divergences,
        "ranking_divergences_vs_first_arm": ranking_divergences,
        "hindsight_best_arm_counts": hindsight,
        "divergence_samples": sample,
    }


def _fmt(value, digits=4):
    if value is None:
        return "NA"
    if isinstance(value, float):
        return f"{value:.{digits}g}"
    return str(value)


def to_markdown(summary: dict) -> str:
    lines = ["| arm | offered | delivered | D4 not delivered | capped loss | mean delay s | p95 delay s | >=2cand decisions |",
             "|---|---|---|---|---|---|---|---|"]
    for arm in summary["arms"]:
        lines.append("| " + " | ".join([
            arm["arm"], _fmt(arm["offered"]), _fmt(arm["delivered"]),
            _fmt(arm["d4_not_delivered_proportion"]), _fmt(arm["capped_loss"]),
            _fmt(arm["delay_mean_s"]), _fmt(arm["delay_p95_s"]),
            _fmt(arm["decisions_with_two_complete_candidates"]),
        ]) + " |")
    lines.append("")
    lines.append("| pair | paired packets | mean diff s | median diff s | left faster | right faster | left-only delivered | right-only delivered |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, row in summary["comparison"]["pairwise"].items():
        lines.append("| " + " | ".join([
            name, _fmt(row["paired_delivered_packets"]),
            _fmt(row["mean_delay_diff_s"]), _fmt(row["median_delay_diff_s"]),
            _fmt(row["left_faster"]), _fmt(row["right_faster"]),
            _fmt(row["left_delivered_only"]), _fmt(row["right_delivered_only"]),
        ]) + " |")
    lines.append("")
    lines.append(f"comparable decisions: {summary['comparison']['decisions_comparable']}; "
                 f"decision divergences: {summary['comparison']['decision_divergences_vs_first_arm']}; "
                 f"ranking divergences: {summary['comparison']['ranking_divergences_vs_first_arm']}; "
                 f"hindsight wins: {summary['comparison']['hindsight_best_arm_counts']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", action="append", required=True,
                        help="NAME=RUN_DIR (repeat per arm)")
    parser.add_argument("--deadline-s", type=float, default=4.0)
    parser.add_argument("--json")
    parser.add_argument("--md")
    args = parser.parse_args(argv)
    specs = []
    for item in args.arm:
        name, _, path = item.partition("=")
        specs.append((name, Path(path)))
    arms = [load_arm(name, path, args.deadline_s) for name, path in specs]
    summary = {
        "deadline_s": args.deadline_s,
        "arms": [{k: v for k, v in arm.items()
                  if k not in ("emissions", "delays", "decision_map")}
                 for arm in arms],
        "comparison": compare(arms),
    }
    table = to_markdown(summary)
    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    if args.md:
        Path(args.md).write_text(table + "\n", encoding="utf-8")
    print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
