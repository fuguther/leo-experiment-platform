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
    from CODE.experiment_platform.replay_codec import read_document
    return read_document(path)


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




def collect_scan(path, table):
    """Long rows from a B4 scan artifact: every axis is carried explicitly."""
    doc = _read(path)
    rows = []
    for row in doc["rows"]:
        entry = {"table": table, "run": doc["run"],
                 "cell": Path(path).stem, "scenario": "fixed_hotspot",
                 "arm": row.get("mode"), "config_sha256": None,
                 "load_mbps": row.get("load"),
                 "servers": row.get("servers"),
                 "service_s": row.get("service_s"),
                 "period_s": row.get("period"),
                 "delivered": row.get("delivered"),
                 "e2e_mean_s": row.get("e2e_mean"),
                 "e2e_p95_s": row.get("e2e_p95"),
                 "total_jobs": row.get("total_jobs"),
                 "background_jobs": row.get("bg_jobs"),
                 "decision_jobs": row.get("pd_jobs"),
                 "background_wait_s": row.get("bg_wait_s"),
                 "decision_wait_s": row.get("pd_wait_s"),
                 "installs": row.get("installs"),
                 "fallbacks": row.get("fallbacks")}
        rows.append(entry)
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
    scan = [r for r in rows if r["table"] == "service_scan"]
    if scan:
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
        for service in sorted({r["service_s"] for r in scan}):
            for mode in sorted({r["arm"] for r in scan if r["arm"]}):
                pts = sorted((r["servers"], r["e2e_mean_s"]) for r in scan
                             if r["service_s"] == service and r["arm"] == mode)
                if not pts:
                    continue
                axes[0].plot([p[0] for p in pts], [p[1] for p in pts],
                             marker="o", label=f"{mode} svc={service}s")
            pts = sorted((r["servers"], r["total_jobs"]) for r in scan
                         if r["service_s"] == service and r["arm"])
        axes[0].set_xlabel("compute servers N")
        axes[0].set_ylabel("mean E2E (s)")
        axes[0].set_title("compute pressure: latency vs N", fontsize=10)
        axes[0].grid(alpha=0.3)
        axes[0].legend(fontsize=6)
        for service in sorted({r["service_s"] for r in scan}):
            for mode in sorted({r["arm"] for r in scan if r["arm"]}):
                pts = sorted((r["servers"], r["total_jobs"]) for r in scan
                             if r["service_s"] == service and r["arm"] == mode)
                if not pts:
                    continue
                axes[1].plot([p[0] for p in pts], [p[1] for p in pts],
                             marker="s", linestyle="--",
                             label=f"{mode} svc={service}s")
        axes[1].set_xlabel("compute servers N")
        axes[1].set_ylabel("total compute jobs")
        axes[1].set_title("compute cost vs N", fontsize=10)
        axes[1].grid(alpha=0.3)
        fig.tight_layout()
        path = out_dir / "fig5_compute_pressure.png"
        fig.savefig(path, dpi=140)
        plt.close(fig)
        made.append(str(path))
    return made




def freeze_no_confirmation_decision(dev_report_path, deadline_dir, out_path):
    """A3/B3 bounded branch: bind everything, invent nothing.

    The development sample cannot size a confirmation (the five pre-declared
    candidates tie exactly and there are too few blocks), so this artifact
    does NOT contain an n.  It binds the loss table, the per-scenario frozen
    D, the rule actually used and the development block set with its hashes,
    states the no-confirmation decision and names the bounded study design
    that must pass a validity pre-check first.  No number is fabricated to
    make the file look complete.
    """
    import hashlib

    report = _read(dev_report_path)
    design = ((report.get("statistics") or {}).get("development_design")
              or {})
    if design.get("status") != "SELECTED":
        raise SystemExit("the development report carries no selection: "
                         + str(design.get("status")))
    selection = design["selection"]
    loss_table = design["loss_table"]
    deadlines = {}
    for path in sorted(Path(deadline_dir).glob("deadline_*.json")):
        doc = _read(path)
        deadlines[path.stem.replace("deadline_", "")] = {
            "deadline_s": doc.get("deadline_s"),
            "samples": doc.get("samples"),
            "branches_used": doc.get("branches_used"),
            "source": doc.get("source"),
            "file": path.name,
            "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    blocks = loss_table.get("blocks") or []
    artifact = {
        "schema": "t1-selected-design/v2-no-confirmation",
        "status": "NO_CONFIRMATION_PLANNED",
        "why": ("the development sample cannot size a confirmation: the five "
                "pre-declared candidates tie exactly on the primary loss and "
                "the block count is below the declared minimum; the selector "
                "reports this instead of hiding it"),
        "selection_outcomes": selection.get("outcomes"),
        "selected_by_declared_tie_rule": selection.get("selected"),
        "rule": selection.get("rule"),
        "loss_table": loss_table,
        "loss_table_sha256": loss_table.get("loss_table_sha256"),
        "development_blocks": {
            "count": len(blocks),
            "units": sorted(str(row.get("unit")) for row in blocks),
            "set_sha256": hashlib.sha256(json.dumps(
                sorted(str(row.get("block_id")) for row in blocks),
                sort_keys=True).encode("utf-8")).hexdigest()},
        "development_deadlines": deadlines,
        "sample_size": None,
        "sample_size_reason": ("not estimated: a bounded design is proposed "
                               "below rather than a fabricated n"),
        "confirm_seeds": [],
        "confirm_seeds_reason": "no confirmation run is planned in this branch",
        "proposed_bounded_design": {
            "step_1": ("declare an ASYMMETRIC multi-OD scenario and gate it on "
                       "a validity pre-check: forward decisions >= N and at "
                       "least two legal directions per decision"),
            "step_2": ("only then re-freeze D, re-run the five candidates and "
                       "decide whether a confirmation is sizeable at all"),
            "evidence": ("two M-Lab variants were pre-checked and both "
                         "produced zero forward decisions, so no candidate "
                         "comparison exists on them yet")},
        "platform_state": ("development experiments are runnable: three task "
                           "types, full metrics and the total compute cost "
                           "are VM-verified"),
        "authorization": ("no confirmation authorization is held and none is "
                          "requested by the executing side"),
    }
    # The digest is of the canonical body BEFORE this field is added, so a
    # reader can recompute it from the file by deleting artifact_sha256 and
    # re-serialising the same way.
    body = json.dumps(artifact, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    artifact["artifact_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    artifact["artifact_sha256_rule"] = (
        "sha256 of the canonical body (sorted keys, compact separators) with "
        "this field removed")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=1,
                                    sort_keys=True) + "\n", encoding="utf-8")
    return artifact

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="B5 tables and figures")
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument("--period-scan", type=Path, default=None)
    parser.add_argument("--scan", type=Path, action="append", default=[],
                        help="a B4 scan artifact; the table name is the file stem")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--scenarios", default="steady_uniform,fixed_hotspot,"
                                            "burst_hotspot,mlab_asym")
    parser.add_argument("--dev-report", type=Path, default=None,
                        help="development report to freeze the no-confirmation "
                             "decision from")
    parser.add_argument("--deadline-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    scenario_ids = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    rows = []
    for run_dir in args.run_dir:
        rows.extend(collect_b_dev(run_dir, scenario_ids))
    if args.period_scan:
        rows.extend(collect_period_scan(args.period_scan))
    for scan in args.scan:
        rows.extend(collect_scan(scan, Path(scan).stem))
    tables = write_tables(rows, args.out)
    figures = write_figures(rows, args.out)
    decision = None
    if args.dev_report and args.deadline_dir:
        decision = freeze_no_confirmation_decision(
            args.dev_report, args.deadline_dir, Path(args.out) / "selected_design.json")
    print(json.dumps({"status": "written", "rows": tables["rows"],
                      "decision": (None if decision is None
                                   else decision["status"]),
                      "csv": tables["csv"], "json": tables["json"],
                      "figures": figures}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
