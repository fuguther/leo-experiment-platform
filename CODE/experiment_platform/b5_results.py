"""B5: turn the VM run artifacts into long-form tables and figures.

Every row carries the run id, the cell id, the config sha256 and the
scenario, so a figure point can be traced back to the run that produced it.
Nothing here re-runs a simulation: it reads what the VM already produced.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

SCHEMA = "t1-b5-results/v1"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def collect_b_dev(run_dir, scenario_ids):
    """Long rows from one b_dev run: arms, modes, blocks and benchmarks."""
    run_dir = Path(run_dir)
    rows = []
    run_id = run_dir.name
    run = _read(run_dir / "run.json")
    for record in run["cells"]:
        cell_id = record["cell_id"]
        result_path = run_dir / record["result_path"]
        if not result_path.exists() or record["status"] != "ok":
            continue
        payload = _read(result_path)
        task = payload.get("task")
        doc = payload.get("document") or {}
        config_sha = (record.get("input") or {}).get("config_sha256")
        sid = next((s for s in scenario_ids if f"-{s}-" in cell_id), "?")
        if task == "network_alignment":
            for arm in doc.get("arms") or []:
                out = arm["outcome"]
                cost = arm.get("compute") or {}
                rows.append({"table": "network_arms", "run": run_id,
                             "cell": cell_id, "scenario": sid, "arm": arm["arm"],
                             "config_sha256": config_sha,
                             "delivered": out["delivered"],
                             "offered": out["offered"],
                             "admitted": out["admitted"],
                             "goodput_bps": out["goodput_bps_in_window"],
                             "e2e_mean_s": out["e2e_mean_s"],
                             "e2e_p50_s": out["e2e_p50_s"],
                             "e2e_p95_s": out["e2e_p95_s"],
                             "e2e_p99_s": out["e2e_p99_s"],
                             "fate_counts": json.dumps(out["fate_counts"],
                                                     sort_keys=True),
                             "decision_jobs": cost["decision_jobs"]["jobs"],
                             "background_jobs": cost["background_jobs"]["jobs"],
                             "hotspot_max": arm["request_rate_per_satellite"]["max"],
                             "reordered": arm["time_alignment_audit"][
                                 "decisions_where_the_arm_reordered_candidates"]})
        elif task == "branch_alignment":
            block = doc.get("block") or {}
            deadline = (doc.get("deadline") or {}).get("deadline_s")
            for arm, entry in sorted((block.get("arms") or {}).items()):
                rows.append({"table": "branch_arms", "run": run_id,
                             "cell": cell_id, "scenario": sid, "arm": arm,
                             "config_sha256": config_sha,
                             "branches": block.get("branch_count"),
                             "deadline_s": deadline,
                             "mean_loss": entry.get("mean_loss"),
                             "mean_regret": entry.get("mean_regret")})
        elif task == "execution_modes":
            for mode in doc.get("modes") or []:
                out = mode["outcome"]
                cost = mode.get("total_cost") or {}
                total = cost.get("total") or {}
                rows.append({"table": "execution_modes", "run": run_id,
                             "cell": cell_id, "scenario": sid,
                             "arm": mode["mode"], "config_sha256": config_sha,
                             "delivered": out["delivered"],
                             "e2e_mean_s": out["e2e_mean_s"],
                             "e2e_p95_s": out["e2e_p95_s"],
                             "total_jobs": total.get("jobs"),
                             "total_service_s": total.get("service_s"),
                             "queries": mode["reuse"]["schedule_queries"],
                             "installs": mode["reuse"]["schedule_installs"],
                             "fallbacks": mode["reuse"]["fallbacks"]})
        elif payload.get("schema") == "benchmark-decision/v1":
            stats = (doc.get("full_decision") or {}).get("stats") or {}
            rows.append({"table": "benchmark", "run": run_id, "cell": cell_id,
                         "scenario": sid, "arm": "deterministic_scorer",
                         "config_sha256": config_sha,
                         "p50_us": (None if not stats.get("p50_s")
                                    else stats["p50_s"] * 1e6),
                         "p95_us": (None if not stats.get("p95_s")
                                    else stats["p95_s"] * 1e6)})
    return rows


def collect_period_scan(path):
    doc = _read(path)
    rows = []
    for row in doc["rows"]:
        rows.append({"table": "period_scan", "run": doc["run"],
                     "cell": "period_scan", "scenario": doc["scenario"],
                     "arm": row["mode"], "config_sha256": None,
                     "period_s": row["period"], "delivered": row["delivered"],
                     "e2e_mean_s": row["e2e_mean"], "e2e_p95_s": row["e2e_p95"],
                     "total_jobs": row["total_jobs"],
                     "background_jobs": row["bg_jobs"],
                     "queries": row["queries"], "installs": row["installs"],
                     "fallbacks": row["fallbacks"]})
    return rows


def write_tables(rows, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    columns = sorted({key for row in rows for key in row})
    csv_path = out_dir / "results_long.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})
    json_path = out_dir / "results.json"
    json_path.write_text(json.dumps(
        {"schema": SCHEMA, "rows": rows,
         "note": "every row carries run/cell/config_sha256 so a figure point "
                 "is traceable to the VM run that produced it"},
        ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return {"csv": str(csv_path), "json": str(json_path), "rows": len(rows)}


def write_figures(rows, out_dir):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []

    def _bar(ax, labels, values, title, ylabel):
        ax.bar(range(len(labels)), values, color="#4C72B0")
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(ylabel, fontsize=8)
        ax.grid(axis="y", alpha=0.3)

    blocks = [r for r in rows if r["table"] == "branch_arms"]
    if blocks:
        fig, ax = plt.subplots(figsize=(7, 4))
        labels = [f'{r["scenario"]}\n{r["arm"]}' for r in blocks]
        _bar(ax, labels, [r["mean_loss"] for r in blocks],
             "experiment 1: mean primary loss per arm per scenario",
             "mean loss  L = min(delay,D)/D")
        fig.tight_layout()
        path = out_dir / "fig1_branch_loss.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        made.append(str(path))

    arms = [r for r in rows if r["table"] == "network_arms"]
    if arms:
        fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
        labels = [f'{r["scenario"]}\n{r["arm"]}' for r in arms]
        _bar(axes[0], labels, [r["e2e_p95_s"] for r in arms],
             "network p95 E2E", "seconds")
        _bar(axes[1], labels,
             [r["delivered"] for r in arms], "delivered", "packets")
        _bar(axes[2], labels, [r["goodput_bps"] for r in arms],
             "payload goodput", "bit/s in window")
        fig.tight_layout()
        path = out_dir / "fig2_network_outcome.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        made.append(str(path))

    if arms:
        fig, ax = plt.subplots(figsize=(6, 4))
        for r in arms:
            ax.scatter(r["hotspot_max"], r["decision_jobs"], s=28,
                       label=f'{r["scenario"]}')
        ax.set_xlabel("hotspot: max decisions on one satellite")
        ax.set_ylabel("decision compute jobs")
        ax.set_title("request pressure vs compute queueing", fontsize=10)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7)
        fig.tight_layout()
        path = out_dir / "fig3_pressure_cost.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        made.append(str(path))

    modes = [r for r in rows if r["table"] == "execution_modes"]
    if modes:
        fig, ax = plt.subplots(figsize=(7, 4.5))
        for r in modes:
            ax.scatter(r["total_jobs"], r["e2e_mean_s"], s=34)
            ax.annotate(r["arm"], (r["total_jobs"], r["e2e_mean_s"]),
                        fontsize=7, xytext=(3, 3),
                        textcoords="offset points")
        ax.set_xlabel("total compute jobs (background included)")
        ax.set_ylabel("mean E2E (s)")
        ax.set_title("experiment 2: net performance vs total cost",
                     fontsize=10)
        ax.grid(alpha=0.3)
        fig.tight_layout()
        path = out_dir / "fig4_modes_cost.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        made.append(str(path))
    return made


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="B5 tables and figures")
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--period-scan", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenarios", default="steady_uniform,fixed_hotspot,"
                                            "burst_hotspot,mlab_asym")
    args = parser.parse_args(argv)
    scenario_ids = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    rows = []
    for run_dir in args.run_dir:
        rows.extend(collect_b_dev(run_dir, scenario_ids))
    if args.period_scan:
        rows.extend(collect_period_scan(args.period_scan))
    tables = write_tables(rows, args.out)
    figures = write_figures(rows, args.out)
    print(json.dumps({"status": "written", "rows": tables["rows"],
                      "csv": tables["csv"], "json": tables["json"],
                      "figures": figures}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
