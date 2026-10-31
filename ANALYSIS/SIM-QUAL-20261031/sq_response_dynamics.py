#!/usr/bin/env python3
"""Response dynamics of the stale-state routing policy.

Answers the "what does a flip actually *do*" question that the flip-rate and
persistence analyses leave open:

1. **switch rate** - how often the committed action changes between consecutive
   decisions of the same (sat,dst) stream.
2. **same-direction run lengths** - how long the policy keeps pointing the same
   way (count and duration, decision timestamps only).
3. **action entropy** - the policy logs contain NO softmin / probability vector
   (verified: no ``softmin`` token anywhere in ``CODE/leo_sim``, and no
   probability field in ``state_policy``), so a policy-entropy is not
   computable.  What is reported instead is the **empirical choice entropy**
   H = -sum p_d log2 p_d over the directions a stream actually chose, which is
   an observed-outcome quantity, not a policy-internal one.  This is stated in
   every output, not silently substituted.
4. **flip consequences** - every flip is classified by its truth-substituted
   cost gap
        dJ = J_truth(chosen) - J_truth(fresh)     [ms]
   into ``proxy_gap_tiny`` (|dJ| <= tol), ``fresh_better`` (dJ > tol, the stale
   choice was worse under truth) and ``stale_better`` (dJ < -tol, the stale
   choice was *better*; structurally possible only for hysteresis, because for
   greedy/EWMA ``fresh`` is the argmin).  ``fresh_better`` flips are split again
   into ``quick_remerge`` (the next decision of the same stream is already
   fresh-optimal) and ``persistent_bias`` (the flip sits inside a same-direction
   flip run of length >= 3).
   ``P(consequential|flip)`` and ``P(harmful|flip)`` are reported for a
   threshold sweep, always labelled **proxy** definitions.

Reuse: importers must not fork the flip semantics.  This module imports
``sq_persistence_analyze`` and uses its loaders plus ``truth_costs`` /
``fresh_action`` verbatim, so flip decisions are identical by construction
(``sq_persistence_analyze.py selfcheck`` verifies that path against
``sq_policy_analyze.py``).

Usage
-----
    python3 sq_response_dynamics.py cell   <cell-dir> [--json F] [--md F]
    python3 sq_response_dynamics.py matrix <cell-dir> [...] --out-dir response-dynamics --label m2-c2
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict

import sq_persistence_analyze as spa

# consequence thresholds, milliseconds (primary first)
TOL_MS = (0.1, 0.5, 1.0, 5.0)
TOL_PRIMARY_MS = 0.5
PERSISTENT_RUN_MIN = 3          # >= 3 consecutive same-direction flips
MIN_STREAM_DECISIONS = 4        # entropy needs a few observations


# ----------------------------------------------------------------- helpers
def qstats(values, points=(0.5, 0.9, 0.95, 0.99), scale=1.0):
    return spa.qstats(values, points=points, scale=scale)


def _mean(values):
    clean = [float(v) for v in values if v is not None]
    return (sum(clean) / len(clean)) if clean else None


def _ratio(num, den):
    return (num / den) if den else None


def entropy_bits(counts):
    total = sum(counts.values())
    if total <= 0:
        return None
    out = 0.0
    for value in counts.values():
        if value <= 0:
            continue
        p = value / total
        out -= p * math.log2(p)
    return out


# ------------------------------------------------------------------- facts
def collect_rows(cell, policy):
    """Eval-window decisions with truth costs, fresh action and flip flag."""
    probe, probe_meta = spa.load_probe(cell)
    bits, dst_by_pid = spa.load_packet_bits(cell)
    rows = []
    for record in spa.iter_decisions(cell):
        moment = record["t"]
        if moment is None or not (spa.EVAL_START_S <= moment < spa.EVAL_END_S):
            continue
        j_used, j_truth, resources = spa.truth_costs(
            record, probe.get(record["decision_id"], {}))
        if len(j_used) < 2:
            continue
        fresh = spa.fresh_action(record, j_used, j_truth, policy)
        if fresh is None:
            continue
        chosen = record["chosen"]
        delta_j = None
        if chosen in j_truth and fresh in j_truth:
            delta_j = float(j_truth[chosen]) - float(j_truth[fresh])
        rows.append({
            "decision_id": record["decision_id"],
            "t": moment, "pid": record["pid"], "sat": record["sat"],
            "dst": dst_by_pid.get(record["pid"]),
            "chosen": chosen, "fresh": fresh,
            "flip": chosen != fresh,
            "delta_j_s": delta_j,
            "hop_index": record["hop_index"],
            "bits": float(bits.get(record["pid"]) or 0.0),
            "policy_audit": record["policy_audit"],
        })
    return rows, probe_meta, bits


def annotate_streams(rows):
    """Group by (sat,dst) and mark run ids for actions and for flips."""
    streams = defaultdict(list)
    for row in rows:
        streams[(row["sat"], row["dst"])].append(row)
    action_runs = 0
    flip_runs = 0
    for key in streams:
        seq = streams[key]
        seq.sort(key=lambda row: (row["t"], row["pid"] if row["pid"] is not None else 0))
        previous = None
        for row in seq:
            if previous is None or row["chosen"] != previous["chosen"]:
                action_runs += 1
                row["action_run"] = action_runs
            else:
                row["action_run"] = previous["action_run"]
            previous = row
        flip_previous = None
        for row in seq:
            if not row["flip"]:
                flip_previous = None
                row["flip_run"] = None
                continue
            if (flip_previous is not None
                    and row["chosen"] == flip_previous["chosen"]):
                row["flip_run"] = flip_previous["flip_run"]
                row["flip_run_pos"] = flip_previous["flip_run_pos"] + 1
            else:
                flip_runs += 1
                row["flip_run"] = flip_runs
                row["flip_run_pos"] = 0
                row["flip_run_len"] = 0        # filled below
            flip_previous = row
        lengths = defaultdict(int)
        for row in seq:
            if row["flip_run"] is not None:
                lengths[row["flip_run"]] = max(lengths[row["flip_run"]],
                                               row["flip_run_pos"] + 1)
        for row in seq:
            if row["flip_run"] is not None:
                row["flip_run_len"] = lengths[row["flip_run"]]
    return streams, action_runs, flip_runs


def analyse(cell):
    config = spa.load_config(cell)
    ta = config.get("time_alignment") or {}
    policy = ta.get("state_policy")
    rows, probe_meta, bits = collect_rows(cell, policy)
    streams, action_runs, flip_runs = annotate_streams(rows)

    # ---------------------------------------------------------- switching
    switch_pairs = switch_moves = 0
    for seq in streams.values():
        for left, right in zip(seq, seq[1:]):
            switch_pairs += 1
            switch_moves += 1 if left["chosen"] != right["chosen"] else 0

    # ------------------------------------------- action runs and entropy
    action_run_rows = defaultdict(list)
    for row in rows:
        action_run_rows[row["action_run"]].append(row)
    action_lengths = [len(v) for v in action_run_rows.values()]
    action_durations = [(max(r["t"] for r in v) - min(r["t"] for r in v))
                        for v in action_run_rows.values()]
    entropy_values, concentration = [], []
    directions_used = []
    for seq in streams.values():
        if len(seq) < MIN_STREAM_DECISIONS:
            continue
        counts = defaultdict(int)
        for row in seq:
            counts[row["chosen"]] += 1
        value = entropy_bits(counts)
        if value is not None:
            entropy_values.append((value, len(seq)))
        concentration.append(max(counts.values()) / len(seq))
        directions_used.append(len(counts))

    # ---------------------------------------------------- flip consequences
    flips = [row for row in rows if row["flip"] and row["delta_j_s"] is not None]
    delta_ms = [1000.0 * row["delta_j_s"] for row in flips]
    consequence = {}
    for tol_ms in TOL_MS:
        tol_s = tol_ms / 1000.0
        tiny = sum(1 for row in flips if abs(row["delta_j_s"]) <= tol_s)
        worse = sum(1 for row in flips if row["delta_j_s"] > tol_s)
        better = sum(1 for row in flips if row["delta_j_s"] < -tol_s)
        consequence[f"{tol_ms:g}"] = {
            "tol_ms": tol_ms,
            "n": len(flips),
            "n_proxy_gap_tiny": tiny,
            "n_fresh_better": worse,
            "n_stale_better": better,
            "P_consequential_proxy": _ratio(len(flips) - tiny, len(flips)),
            "P_harmful_proxy": _ratio(worse, len(flips)),
            "P_beneficial_proxy": _ratio(better, len(flips)),
        }
    # primary partition with the persistence split
    tol_s = TOL_PRIMARY_MS / 1000.0
    categories = defaultdict(int)
    remerge_pairs = {"remerged": 0, "not_remerged": 0}
    next_row = {}
    for key, seq in streams.items():
        for index, row in enumerate(seq[:-1]):
            next_row[row["decision_id"]] = seq[index + 1]
    for row in flips:
        if abs(row["delta_j_s"]) <= tol_s:
            categories["proxy_gap_tiny"] += 1
            continue
        if row["delta_j_s"] < -tol_s:
            categories["stale_better"] += 1
            continue
        # fresh_better: did the same stream return to fresh-optimal next?
        following = next_row.get(row["decision_id"])
        if row.get("flip_run_len", 0) >= PERSISTENT_RUN_MIN:
            categories["persistent_bias"] += 1
        elif following is not None and not following["flip"]:
            categories["quick_remerge"] += 1
        else:
            categories["fresh_better_other"] += 1

    # regret per decision (ms) - the "how much does staleness cost" proxy
    regret_ms = [1000.0 * max(0.0, row["delta_j_s"]) for row in flips
                 if row["delta_j_s"] is not None]

    contributions = defaultdict(int)
    for row in rows:
        contributions[row["policy_audit"]] += 1

    return {
        "schema": "sq-response-dynamics-cell/v1",
        "cell": cell.name,
        "policy": policy,
        "hysteresis_eta_s": ta.get("hysteresis_eta_s"),
        "ewma_alpha": ta.get("ewma_alpha"),
        "window_s": [spa.EVAL_START_S, spa.EVAL_END_S],
        "caliber": {
            "flip": "truth-substituted same-decision re-ranking; semantics imported from sq_persistence_analyze (verified identical to sq_policy_analyze by its selfcheck)",
            "delta_j": "J_truth(chosen) - J_truth(fresh) in ms; positive = the stale choice was worse under the truth cost",
            "entropy": "empirical choice entropy over realised directions per (sat,dst) stream; NO policy softmin exists in the logs, so policy entropy is not reported",
            "switch": "adjacent decisions inside one (sat,dst) stream whose committed direction differs",
            "stream_key": "per-(sat,dst), dst joined from trace.jsonl.gz by pid",
            "windows": "eval 4-14 s only",
        },
        "inputs": {"probe": probe_meta,
                   "config_sha256": spa._config_sha(cell),
                   "trace_source_sha256": spa._trace_sha(cell)},
        "decisions": {
            "n_decisions": len(rows),
            "n_streams": len(streams),
            "n_flips": len(flips),
            "flip_rate": _ratio(len(flips), len(rows)),
            "audit_policy_counts": dict(contributions),
        },
        "switch": {
            "rate": _ratio(switch_moves, switch_pairs),
            "pairs": switch_pairs,
            "moves": switch_moves,
        },
        "action_runs": {
            "n_runs": len(action_lengths),
            "length_p50": qstats(action_lengths, points=(0.5, 0.9)).get("p50"),
            "length_p90": qstats(action_lengths, points=(0.9,)).get("p90"),
            "length_max": max(action_lengths) if action_lengths else None,
            "duration_ms": qstats(action_durations, points=(0.5, 0.9, 0.95), scale=1000.0),
            "duration_max_ms": (1000.0 * max(action_durations)) if action_durations else None,
        },
        "entropy": {
            "policy_softmin_available": False,
            "reason": "no softmin/probability field in the decision logs or in CODE/leo_sim; entropy below is over realised choices only",
            "choice_entropy_bits_mean_weighted": (
                sum(value * n for value, n in entropy_values)
                / sum(n for _, n in entropy_values)) if entropy_values else None,
            "choice_entropy_bits_p50": qstats([v for v, _ in entropy_values],
                                              points=(0.5, 0.9)).get("p50"),
            "choice_entropy_bits_max": max((v for v, _ in entropy_values), default=None),
            "n_streams_used": len(entropy_values),
            "modal_direction_share_p50": qstats(concentration, points=(0.5, 0.9)).get("p50"),
            "distinct_directions_mean": _mean([float(v) for v in directions_used]),
        },
        "flip_consequences": {
            "n_flips_with_cost": len(flips),
            "delta_j_ms": {**qstats(delta_ms, scale=1.0)},
            "delta_j_zero_share": _ratio(sum(1 for value in delta_ms if value == 0.0),
                                         len(delta_ms)),
            "primary_tol_ms": TOL_PRIMARY_MS,
            "partition_primary": dict(categories),
            "P_consequential_proxy": _ratio(
                len(flips) - categories.get("proxy_gap_tiny", 0), len(flips)),
            "P_harmful_proxy": _ratio(
                categories.get("quick_remerge", 0)
                + categories.get("persistent_bias", 0)
                + categories.get("fresh_better_other", 0), len(flips)),
            "thresholds": consequence,
            "regret_ms": {**qstats(regret_ms, scale=1.0),
                          "mean": _mean(regret_ms)},
            "regret_ms_per_decision": ((_mean(regret_ms) or 0.0) * len(flips)
                                        / len(rows)) if rows else None,
        },
    }


# --------------------------------------------------------------- rendering
def _f(value, pattern="{:.3f}", dash="n/a"):
    if value is None:
        return dash
    if isinstance(value, float) and not math.isfinite(value):
        return "inf"
    return pattern.format(value)


def render_cell(report):
    d = report["decisions"]
    s = report["switch"]
    a = report["action_runs"]
    e = report["entropy"]
    c = report["flip_consequences"]
    lines = [f"# Response dynamics - `{report['cell']}`", "",
             f"- policy `{report['policy']}` (eta={report['hysteresis_eta_s']}, "
             f"alpha={report['ewma_alpha']}); window {report['window_s']}",
             f"- decisions {d['n_decisions']} over {d['n_streams']} (sat,dst) streams; "
             f"flips {d['n_flips']} ({_f(d['flip_rate'], '{:.4f}')})",
             f"- switch rate **{_f(s['rate'], '{:.4f}')}** "
             f"({s['moves']}/{s['pairs']} adjacent pairs)",
             f"- action runs: {a['n_runs']} runs, length p50 "
             f"{_f(a['length_p50'], '{:.1f}')} / p90 {_f(a['length_p90'], '{:.1f}')} / "
             f"max {_f(a['length_max'], '{:.0f}')}; duration p50 "
             f"{_f(a['duration_ms'].get('p50'))} ms, max {_f(a['duration_max_ms'])} ms",
             f"- action entropy (**realised choices only**; policy softmin is absent): "
             f"weighted mean {_f(e['choice_entropy_bits_mean_weighted'], '{:.3f}')} bit, "
             f"p50 {_f(e['choice_entropy_bits_p50'])} bit, modal-direction share p50 "
             f"{_f(e['modal_direction_share_p50'], '{:.3f}')}",
             "", f"## Flip consequences (dJ = J_truth(chosen) - J_truth(fresh), ms)", "",
             f"- dJ p50 {_f(c['delta_j_ms'].get('p50'))} / p90 "
             f"{_f(c['delta_j_ms'].get('p90'))} / p99 {_f(c['delta_j_ms'].get('p99'))} ms; "
             f"exactly-zero share {_f(c['delta_j_zero_share'], '{:.3f}')}",
             f"- partition at tol={c['primary_tol_ms']:g} ms: "
             + ", ".join(f"{k}={v}" for k, v in sorted(c["partition_primary"].items())),
             f"- **P(consequential|flip) = {_f(c['P_consequential_proxy'], '{:.4f}')}**, "
             f"**P(harmful|flip) = {_f(c['P_harmful_proxy'], '{:.4f}')}** "
             "(proxy definitions, not causal)",
             f"- proxy regret over flips: mean {_f(c['regret_ms'].get('mean'))} ms, "
             f"p90 {_f(c['regret_ms'].get('p90'))} ms", "",
             "| tol (ms) | tiny | fresh better | stale better | P(consequential) | P(harmful) |",
             "|---|---|---|---|---|---|"]
    for key in sorted(c["thresholds"], key=float):
        block = c["thresholds"][key]
        lines.append(f"| {block['tol_ms']:g} | {block['n_proxy_gap_tiny']} | "
                     f"{block['n_fresh_better']} | {block['n_stale_better']} | "
                     f"{_f(block['P_consequential_proxy'], '{:.4f}')} | "
                     f"{_f(block['P_harmful_proxy'], '{:.4f}')} |")
    lines.append("")
    return "\n".join(lines)


def render_matrix(reports, label):
    lines = [f"# Response dynamics - `{label}` ({len(reports)} cells)", "",
             "Flip semantics imported from `sq_persistence_analyze` (verified identical",
             "to `sq_policy_analyze` by its selfcheck). dJ is a truth-substituted proxy,",
             "not a causal measurement; policy softmin does not exist in the logs, so the",
             "reported entropy is over realised choices only.", "",
             "| cell | policy | flips | flip rate | switch rate | modal share | "
             "entropy (bit) | dJ p50 (ms) | dJ p90 | P(consequential) | P(harmful) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for report in reports:
        d = report["decisions"]
        s = report["switch"]
        e = report["entropy"]
        c = report["flip_consequences"]
        lines.append(
            f"| {report['cell']} | {report['policy']} | {d['n_flips']} | "
            f"{_f(d['flip_rate'], '{:.4f}')} | {_f(s['rate'], '{:.4f}')} | "
            f"{_f(e['modal_direction_share_p50'], '{:.3f}')} | "
            f"{_f(e['choice_entropy_bits_mean_weighted'])} | "
            f"{_f(c['delta_j_ms'].get('p50'))} | {_f(c['delta_j_ms'].get('p90'))} | "
            f"{_f(c['P_consequential_proxy'], '{:.4f}')} | "
            f"{_f(c['P_harmful_proxy'], '{:.4f}')} |")
    lines += ["", "## Per-cell detail", ""]
    for report in reports:
        lines.append(f"- [{report['cell']}](./{report['cell']}.md)")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="response dynamics of the stale-state routing policy")
    parser.add_argument("command", choices=["cell", "matrix"])
    parser.add_argument("cells", nargs="+")
    parser.add_argument("--json")
    parser.add_argument("--md")
    parser.add_argument("--out-dir")
    parser.add_argument("--label", default="response")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    from pathlib import Path
    cells = [Path(value) for value in args.cells]
    for path in cells:
        if not (path / "arms" / "now" / "decisions.jsonl.gz").exists():
            parser.error(f"not a cell directory: {path}")

    if args.command == "cell":
        report = analyse(cells[0])
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(report, fh, indent=1, sort_keys=True)
                fh.write("\n")
        if args.md:
            Path(args.md).write_text(render_cell(report), encoding="utf-8")
        print(json.dumps({"cell": report["cell"],
                          "flip_rate": report["decisions"]["flip_rate"],
                          "switch_rate": report["switch"]["rate"],
                          "P_consequential": report["flip_consequences"]["P_consequential_proxy"],
                          "P_harmful": report["flip_consequences"]["P_harmful_proxy"]},
                         indent=1, sort_keys=True))
        return 0

    reports = []
    for path in cells:
        if not args.quiet:
            print(f"[{path.name}] analysing…", file=__import__("sys").stderr)
        reports.append(analyse(path))
    out_dir = Path(args.out_dir or ".")
    out_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        with open(out_dir / f"{report['cell']}.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
            fh.write("\n")
        (out_dir / f"{report['cell']}.md").write_text(render_cell(report),
                                                      encoding="utf-8")
    combined = {"schema": "sq-response-dynamics-matrix/v1", "label": args.label,
                "n_cells": len(reports),
                "cell_order": [report["cell"] for report in reports],
                "policy_softmin_available_anywhere": False,
                "cells": {report["cell"]: {
                    "cell": report["cell"], "policy": report["policy"],
                    "decisions": report["decisions"],
                    "switch": report["switch"],
                    "action_runs": report["action_runs"],
                    "entropy": report["entropy"],
                    "flip_consequences": {
                        key: value for key, value in
                        report["flip_consequences"].items()},
                    "caliber": report["caliber"],
                } for report in reports}}
    with open(out_dir / f"{args.label}-response.json", "w", encoding="utf-8") as fh:
        json.dump(combined, fh, indent=1, sort_keys=True)
        fh.write("\n")
    (out_dir / f"{args.label}-response.md").write_text(
        render_matrix(reports, args.label), encoding="utf-8")
    print(json.dumps({report["cell"]: report["flip_consequences"]["P_harmful_proxy"]
                      for report in reports}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
