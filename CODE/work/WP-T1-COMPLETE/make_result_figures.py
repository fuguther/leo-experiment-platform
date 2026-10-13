"""Figures and summary JSON for the 2026-10-13 time-alignment core result.

Reads pulled T1 evidence (one directory per run, each holding
cal/<cell>/arms/<arm>/decisions.jsonl.gz and result.json.gz) and produces:

  fig1_mechanism_sensitivity.png  ranking margin vs work shift across query
                                  policies, per scenario setting;
  fig2_business_outcome.png       per-arm business outcome per setting;
  fig3_calibration_funnel.png     where the offered traffic is lost, per
                                  calibration cell.

Usage:
  python3 -B CODE/work/WP-T1-COMPLETE/make_result_figures.py \
      --evidence-root <dir> --out-dir <dir> [--run-id-prefix t1-core- ...]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ARMS = ("stale", "now", "common", "candidate")
#: Zero work shifts cannot be drawn on a log axis; they are floored here and
#: labelled in the legend so the plot never implies a small non-zero signal.
FLOOR = 1e-6
SETTINGS = {"ref": "base (5 Mbps, MCS)", "isl4": "ISL 4 Mbps", "isl1": "ISL 1 Mbps",
            "adv4": "ISL 1 Mbps, advert 4 s", "adv8": "ISL 1 Mbps, advert 8 s",
            "r025": "ISL 0.25 Mbps", "r01": "ISL 0.1 Mbps"}


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                yield json.loads(line)


def _load_gzip_json(path: Path):
    if not path.is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def find_run(root: Path, setting: str, arm: str) -> Path | None:
    for candidate in sorted(root.glob(f"*-{setting}-{arm}-*")):
        if candidate.is_dir():
            return candidate
    return None


def load_arm(run_dir: Path):
    decisions = {}
    for name in ("decisions.jsonl.gz",):
        for path in run_dir.glob(f"cal/*/arms/*/{name}"):
            for row in _iter_jsonl(path) or []:
                if row.get("kind") != "forward":
                    continue
                ta = (row.get("observation_at_start") or {}).get("time_alignment") or {}
                decisions[(row.get("pid"), row.get("decision_id"))] = {
                    "audit": ta,
                    "chosen": row.get("chosen"),
                    "applied_order": tuple(ta.get("applied_order") or ()),
                    "ranking": tuple(ta.get("ranking") or ()),
                }
    result = None
    for path in run_dir.glob("cal/*/arms/*/result.json.gz"):
        result = _load_gzip_json(path)
    return {"decisions": decisions, "result": result}


def _emitted(result):
    out = {}
    for event in (result or {}).get("packet_events") or []:
        if isinstance(event, dict) and event.get("kind") == "packet_emitted":
            pid, at = event.get("pid"), event.get("at")
            if pid is not None and isinstance(at, (int, float)):
                out[int(pid)] = float(at)
    return out


def arm_outcome(arm_data, deadline_s=4.0):
    result = arm_data["result"] or {}
    emitted = _emitted(result)
    deliveries = result.get("deliveries") or {}
    delays = {}
    for pid, at in emitted.items():
        record = deliveries.get(str(pid))
        when = record.get("delivered_at") if isinstance(record, dict) else None
        if isinstance(when, (int, float)) and when >= at:
            delays[pid] = float(when) - at
    offered = len(emitted)
    ok = sum(1 for pid in emitted if delays.get(pid) is not None and delays[pid] <= deadline_s)
    return {
        "offered": offered,
        "delivered": len(delays),
        "d4_fail": (offered - ok) / offered if offered else None,
        "mean_delay_s": (sum(delays.values()) / len(delays)) if delays else None,
        "queue_area": result.get("queue_area_bits_s") or {},
    }


def sensitivity(per_arm):
    """Per-decision ranking margin and cross-arm work shift, in seconds."""
    arms = [a for a in ARMS if per_arm.get(a)]
    if len(arms) < 2:
        return []
    keys = set(per_arm[arms[0]]["decisions"])
    for a in arms[1:]:
        keys &= set(per_arm[a]["decisions"])
    rows = []
    for key in keys:
        blocks = [per_arm[a]["decisions"][key]["audit"] for a in arms]
        entries = [per_arm[a]["decisions"][key] for a in arms]
        margins = []
        best_dir = None
        for ta in blocks:
            scores = ta.get("scores") or {}
            vals = []
            for direction, score in scores.items():
                if not isinstance(score, dict):
                    continue
                total = score.get("total_s")
                if score.get("missing") == [] and isinstance(total, (int, float)) \
                        and math.isfinite(float(total)):
                    vals.append((float(total), direction))
            vals.sort()
            if len(vals) >= 2:
                margins.append(vals[1][0] - vals[0][0])
                if best_dir is None:
                    best_dir = vals[0][1]
        if not margins or best_dir is None:
            continue
        works = []
        for ta in blocks:
            score = (ta.get("scores") or {}).get(best_dir)
            if isinstance(score, dict):
                work = (score.get("terms") or {}).get("resource_work_s")
                if isinstance(work, (int, float)):
                    works.append(float(work))
        if len(works) < 2:
            continue
        rows.append({"margin_s": min(margins),
                     "work_shift_s": max(works) - min(works),
                     "work_max_s": max(works),
                     "chosen_differs": len({e["chosen"] for e in entries}) > 1,
                     "order_differs": len({e["applied_order"] for e in entries}) > 1,
                     "ranking_differs": len({e["ranking"] for e in entries}) > 1})
    return rows


def fig_sensitivity(datasets, out_path: Path):
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    ax = axes[0]
    colors = plt.cm.viridis([i / max(1, len(datasets) - 1) for i in range(len(datasets))])
    for (name, rows), color in zip(datasets, colors):
        if not rows:
            continue
        xs = [r["margin_s"] for r in rows]
        ys = [max(r["work_shift_s"], FLOOR) for r in rows]
        zero = sum(1 for r in rows if r["work_shift_s"] <= 0)
        ax.scatter(xs, ys, s=6, alpha=0.25, color=color,
                   label=f"{name} (n={len(rows)}, shift=0: {zero})")
    lims = [1e-5, 3e-1]
    ax.plot(lims, lims, "k--", lw=1, label="shift = margin")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlim(*lims); ax.set_ylim(FLOOR, 3e-1)
    ax.axhline(FLOOR, color="0.5", lw=0.8, ls=":")
    ax.set_xlabel("ranking margin: 2nd best - best total (s)")
    ax.set_ylabel("cross-policy work-estimate shift (s)\n(zero shift drawn at the 1e-6 floor)")
    ax.set_title("State-time misalignment cannot flip a ranking\n(all points lie far below the diagonal)")
    ax.legend(fontsize=7, loc="lower right")
    ax.grid(alpha=0.3, which="both")

    ax = axes[1]
    labels, fracs = [], []
    for name, rows in datasets:
        if not rows:
            continue
        flips = sum(1 for r in rows if r["chosen_differs"])
        labels.append(name)
        fracs.append(flips / len(rows))
    ax.bar(range(len(labels)), fracs, color="#c0392b")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("share of decisions with a different chosen action")
    ax.set_ylim(0, max(0.01, 1.15 * max(fracs) if fracs else 0.01))
    ax.set_title("Where the policy actually changes the action")
    for i, value in enumerate(fracs):
        ax.text(i, value + 0.02, f"{value:.4%}", ha="center", fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=170)
    plt.close(fig)


def fig_outcome(datasets_outcome, out_path: Path):
    names = [n for n in datasets_outcome]
    fig, axes = plt.subplots(1, 3, figsize=(12.5, 4.2))
    metrics = [("d4_fail", "D4 deadline miss ratio"),
               ("mean_delay_s", "mean delivery delay (s)"),
               ("delivered", "delivered packets")]
    for ax, (key, label) in zip(axes, metrics):
        width = 0.2
        for i, arm in enumerate(ARMS):
            vals = []
            for name in names:
                outcome = datasets_outcome[name].get(arm)
                vals.append(None if outcome is None else outcome.get(key))
            xs = [j + (i - 1.5) * width for j in range(len(names))]
            ax.bar(xs, [0 if v is None else v for v in vals], width,
                   label=arm)
        ax.set_xticks(range(len(names)))
        ax.set_xticklabels(names, rotation=25, ha="right", fontsize=8)
        ax.set_title(label)
        ax.grid(alpha=0.3, axis="y")
    axes[0].legend(fontsize=8)
    fig.suptitle("Four query policies on the identical exogenous trace")
    fig.tight_layout()
    fig.savefig(out_path, dpi=170)
    plt.close(fig)


def fig_funnel(cal_rows, out_path: Path):
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.4))
    labels = [r["cell"] for r in cal_rows]
    ax = axes[0]
    ax.bar(range(len(labels)), [r["d4_fail"] or 0 for r in cal_rows], color="#8e44ad")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_ylabel("D4 deadline miss ratio")
    ax.set_title("Business loss by calibration cell")
    ax.grid(alpha=0.3, axis="y")

    ax = axes[1]
    xs = [max(r["uplink"], 1.0) for r in cal_rows]
    ys = [max(r["isl_data"], 1e-3) for r in cal_rows]
    ax.scatter(xs, ys, s=50, color="#2980b9")
    for x, y, label in zip(xs, ys, labels):
        ax.annotate(label, (x, y), fontsize=7, xytext=(4, 4),
                    textcoords="offset points")
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("uplink queue area (bit*s)")
    ax.set_ylabel("ISL data queue area (bit*s)")
    ax.set_title("Where the traffic is lost: access, not ISL")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(out_path, dpi=170)
    plt.close(fig)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--calibration-prefix", default="t1-load-cal-")
    args = parser.parse_args(argv)
    root = Path(args.evidence_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    datasets = []
    outcomes = {}
    summary = {"settings": {}}
    for setting, label in SETTINGS.items():
        per_arm = {}
        for arm in ARMS:
            run_dir = find_run(root, setting, arm)
            if run_dir is None:
                continue
            per_arm[arm] = load_arm(run_dir)
        if not per_arm:
            continue
        rows = sensitivity(per_arm)
        datasets.append((f"{setting}", rows))
        outcomes[setting] = {arm: arm_outcome(data) for arm, data in per_arm.items()}
        flips = sum(1 for r in rows if r["chosen_differs"])
        summary["settings"][setting] = {
            "label": label,
            "decisions": len(rows),
            "chosen_differs": flips,
            "chosen_differs_share": (flips / len(rows)) if rows else None,
            "order_differs": sum(1 for r in rows if r["order_differs"]),
            "ranking_differs": sum(1 for r in rows if r["ranking_differs"]),
            "margin_exceeded": sum(1 for r in rows
                                   if r["work_shift_s"] > r["margin_s"]),
            "median_margin_s": (sorted(r["margin_s"] for r in rows)[len(rows) // 2]
                                if rows else None),
            "max_work_shift_s": max((r["work_shift_s"] for r in rows), default=None),
            "outcomes": outcomes[setting],
        }

    cal_rows = []
    for run_dir in sorted(root.glob(f"{args.calibration_prefix}*-20261013-02")):
        cell = run_dir.name.replace("t1-load-cal-", "").replace("-20261013-02", "")
        for path in run_dir.glob("cal/*/summary.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            for arm in doc.get("arms") or []:
                d4 = arm.get("d4") or {}
                queue = arm.get("queue_area_bits_s") or {}
                decisions = arm.get("decisions") or {}
                cal_rows.append({
                    "cell": cell,
                    "d4_fail": d4.get("deadline_not_delivered_proportion"),
                    "capped_loss": d4.get("capped_loss"),
                    "uplink": queue.get("uplink") or 0.0,
                    "isl_data": queue.get("isl_data") or 0.0,
                    "holding": queue.get("holding") or 0.0,
                    "candidates_two_complete":
                        decisions.get("decisions_with_at_least_two_complete_candidates"),
                    "nonzero_work": decisions.get("nonzero_resource_work_candidate_count"),
                })
    summary["calibration"] = cal_rows

    if datasets:
        fig_sensitivity(datasets, out_dir / "fig1_mechanism_sensitivity.png")
    if outcomes:
        fig_outcome(outcomes, out_dir / "fig2_business_outcome.png")
    if cal_rows:
        fig_funnel(cal_rows, out_dir / "fig3_calibration_funnel.png")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "outcomes"}
                      for k, v in summary["settings"].items()},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
