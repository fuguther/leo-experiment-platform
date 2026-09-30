#!/usr/bin/env python3
"""Rebuild compact WP-A tables and a bounded tradeoff plot from a pullback.

This is a post-processing tool only. It reads one verified T1 pullback and
never invokes the simulator. Example:

  python3 -B CODE/work/WP-T1-COMPLETE/analyze_wp_a_run.py \
    /path/to/evidence/t1/wp-a-dev-20260930-02 \
    --out-dir /path/to/derived/wp-a-dev-20260930-02
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
from pathlib import Path

import yaml


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_csv(path: Path, rows: list[dict]):
    fields = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def rebuild(run_dir: Path, out_dir: Path) -> tuple[list[dict], list[dict]]:
    run_dir = run_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    receipt = _json(run_dir / "run-receipt.json")
    manifest = _json(run_dir / "run-manifest.json")
    if _sha256(run_dir / "run-manifest.json") != receipt.get(
            "run_manifest_sha256"):
        raise ValueError("run manifest hash differs from receipt")
    suite = _json(run_dir / "development/b_dev/run.json")
    suite_report = _json(run_dir / "development/b_dev/report.json")
    pipeline = _json(run_dir / "development/pipeline-summary.json")
    receipt_files = {item["path"]: item["sha256"]
                     for item in receipt.get("files", [])}
    suite_cells = {item["cell_id"]: item
                   for item in suite.get("cells", [])}
    reported_cells = {item["cell_id"]: item
                      for item in suite_report.get("cells", [])}

    mode_rows = []
    arm_rows = []
    for cell_id, cell in suite_cells.items():
        result_sha = cell.get("result_sha256")
        if not result_sha:
            continue
        relpath = f"development/b_dev/cells/{cell_id}/result.json"
        result_path = run_dir / relpath
        if not result_path.is_file():
            raise ValueError(f"manifested cell result is missing: {relpath}")
        actual_sha = _sha256(result_path)
        if actual_sha != result_sha:
            raise ValueError(f"cell result hash differs from run.json: {cell_id}")
        if receipt_files.get(relpath) != actual_sha:
            raise ValueError(f"cell result hash differs from receipt: {cell_id}")
        result = _json(result_path)
        outer = reported_cells.get(cell_id, {})
        outer_status = outer.get("status", cell.get("status", "unknown"))
        doc = result.get("document", {})

        if result.get("task") == "network_alignment":
            for arm in doc.get("arms", []):
                congestion = arm.get("congestion_metrics", {})
                outcome = arm.get("outcome", {})
                log = arm.get("action_log", {}).get("records", [])
                arm_rows.append({
                    "run_id": receipt["run_id"],
                    "cell_id": cell_id,
                    "outer_cell_status": outer_status,
                    "arm": arm.get("arm"),
                    "offered": outcome.get("offered"),
                    "admitted_recorded_in_derived_outcome": outcome.get("admitted"),
                    "admitted_canonical_satellite_ingress": congestion.get(
                        "admitted_at_satellite_ingress_packets"),
                    "delivered": congestion.get("delivered_packets"),
                    "forward_decisions": arm.get("scope", {}).get(
                        "forward_decisions"),
                    "satellites_that_decided": arm.get("scope", {}).get(
                        "satellites_that_decided"),
                    "reordered_decisions": sum(bool(item.get("reordered"))
                                                for item in log),
                    "action_log_decisions": len(log),
                    "fate_counts": json.dumps(
                        outcome.get("fate_counts", {}), sort_keys=True),
                    "result_sha256": actual_sha,
                })
            continue

        if result.get("task") != "execution_modes":
            continue
        remote_config = Path((cell.get("input") or {}).get("config_path", ""))
        config_path = run_dir / "development/bundle/configs" / remote_config.name
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        update_interval = (config.get("async_routing") or {}).get(
            "update_interval_s")
        argv = cell.get("argv", [])
        if "--update-interval-s" in argv:
            update_interval = float(
                argv[argv.index("--update-interval-s") + 1])
        for mode in doc.get("modes", []):
            network = mode["network_outcome"]
            counts = network["counts"]
            foreground = mode["compute"]
            background = mode["total_cost"]["background_updates"]
            query = mode["query_service"]["totals"]
            precompute = mode.get("precompute", {})
            mode_rows.append({
                "run_id": receipt["run_id"],
                "release_id": receipt["release_id"],
                "source_git_commit": receipt["source_git_commit"],
                "contract_sha256": pipeline.get("contract_sha256", ""),
                "cell_id": cell_id,
                "outer_cell_status": outer_status,
                "task_result_status": result.get("status"),
                "scenario_role": "negative_control" if "negative_control" in cell_id
                else "asymmetric_multiod",
                "seed": mode.get("seed"),
                "mode": mode.get("mode"),
                "servers": foreground.get("servers"),
                "service_s": foreground.get("service_s"),
                "update_interval_s": update_interval,
                "config_sha256": mode.get("config_sha256"),
                "offered": counts.get("offered"),
                "admitted": counts.get("admitted"),
                "delivered": counts.get("delivered"),
                "delivery_fraction_all_offered": network.get(
                    "delivery_ratio", {}).get("by_packets"),
                "terminal_loss": network.get("terminal_loss", {}).get(
                    "packets"),
                "administratively_censored": network.get(
                    "censoring", {}).get("packets"),
                "fate_counts": json.dumps(counts.get("fate_counts", {}),
                                          sort_keys=True),
                "e2e_mean_delivered_s": network.get("e2e", {}).get("mean_s"),
                "e2e_p95_delivered_s": network.get("e2e", {}).get("p95_s"),
                "foreground_jobs": foreground.get("decision_requests"),
                "foreground_service_s": (
                    foreground.get("decision_requests", 0)
                    * foreground.get("service_s", 0)),
                "foreground_wait_s": foreground.get("total_wait_s"),
                "background_jobs": background.get("jobs"),
                "background_service_s": background.get("service_s"),
                "background_wait_s": background.get("queue_wait_s"),
                "query_requests": query.get("requests"),
                "query_service_s": query.get("total_service_s"),
                "query_wait_s": query.get("total_wait_s"),
                "precompute_builds": precompute.get("builds"),
                "precompute_bfs_runs": precompute.get("bfs_runs"),
                "precompute_targets": precompute.get("targets"),
                "recorded_precompute_build_wall_s": precompute.get(
                    "build_wall_s"),
                "outcome_partition_exact": mode.get(
                    "outcome_document", {}).get("partition_exact"),
                "D_declared_s": 30.0,
                "deadline_primary_loss": "NOT_COMPUTABLE",
                "deadline_metric_limit": (
                    "run output keeps delivered E2E summaries and fate counts, "
                    "not per-packet E2E values or an applied D=30 classification; "
                    "administrative censoring must remain separate"),
                "result_sha256": actual_sha,
            })

    mode_rows.sort(key=lambda row: (row["cell_id"], row["mode"]))
    arm_rows.sort(key=lambda row: (row["cell_id"], row["arm"]))
    _write_csv(out_dir / "mode-summary.csv", mode_rows)
    _write_csv(out_dir / "negative-control-arms.csv", arm_rows)
    (out_dir / "provenance.json").write_text(json.dumps({
        "schema": "wp-a-derived-analysis/v1",
        "run_id": receipt["run_id"],
        "release_id": receipt["release_id"],
        "source_git_commit": receipt["source_git_commit"],
        "run_receipt_sha256_canonical": receipt["receipt_sha256"],
        "run_manifest_sha256": receipt["run_manifest_sha256"],
        "runtime_contract_sha256": manifest.get("runtime_contract", {}).get(
            "sha256"),
        "source_run_status": receipt["status"],
        "suite_status": suite_report.get("run_status"),
        "suite_cell_counts": suite_report.get("counts"),
        "mode_rows": len(mode_rows),
        "network_arm_rows": len(arm_rows),
        "note": "No simulation is run; timed-out cells with a hash-verified result remain labeled timeout.",
    }, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return mode_rows, arm_rows


def plot_pressure(mode_rows: list[dict], out_path: Path):
    settings = [
        (1, 0.25, 0.5, "N=1, service=0.25 s, update=0.5 s"),
        (2, 0.25, 0.5, "N=2, service=0.25 s, update=0.5 s"),
        (1, 0.25, 2.0, "N=1, service=0.25 s, update=2.0 s"),
    ]
    colors = {
        "per_packet": "#b33c2e",
        "per_flow": "#e18b24",
        "precomputed": "#286a9b",
        "async_point": "#36875d",
        "async_window": "#7651a1",
    }
    modes = ("per_packet", "per_flow", "precomputed", "async_point",
             "async_window")
    width, height = 1680, 770
    panel_left, panel_width = 82, 510
    plot_top, plot_height = 135, 475
    x_min, x_max, y_min, y_max = 0.0, 45.0, 0.0, 35.0

    def esc(value):
        return html.escape(str(value), quote=True)

    def xy(panel, delivery_pct, mean_e2e):
        left = panel_left + panel * panel_width + 55
        plot_width = panel_width - 90
        x = left + (delivery_pct - x_min) / (x_max - x_min) * plot_width
        y = plot_top + plot_height - (mean_e2e - y_min) / (y_max - y_min) * plot_height
        return x, y

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#17202a} .small{font-size:13px} .axis{stroke:#485460;stroke-width:1} .grid{stroke:#dfe4ea;stroke-width:1}</style>',
        '<text x="840" y="38" text-anchor="middle" font-size="22" font-weight="bold">WP-A development: completed pressure cells only (seed 7)</text>',
    ]
    for panel, (servers, service, update, title) in enumerate(settings):
        subset = [row for row in mode_rows
                  if row["outer_cell_status"] == "ok"
                  and row["scenario_role"] == "asymmetric_multiod"
                  and row["servers"] == servers
                  and row["service_s"] == service
                  and row["update_interval_s"] == update]
        left = panel_left + panel * panel_width + 55
        plot_width = panel_width - 90
        bottom = plot_top + plot_height
        svg.append(f'<text x="{left + plot_width / 2:.1f}" y="83" text-anchor="middle" font-size="16" font-weight="bold">{esc(title)}</text>')
        for tick in range(0, 46, 10):
            x, _ = xy(panel, tick, 0)
            svg.append(f'<line class="grid" x1="{x:.1f}" y1="{plot_top}" x2="{x:.1f}" y2="{bottom}"/>')
            svg.append(f'<text class="small" x="{x:.1f}" y="{bottom + 22}" text-anchor="middle">{tick}</text>')
        for tick in range(0, 36, 5):
            _, y = xy(panel, 0, tick)
            svg.append(f'<line class="grid" x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}"/>')
            if panel == 0:
                svg.append(f'<text class="small" x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{tick}</text>')
        svg.append(f'<line class="axis" x1="{left}" y1="{plot_top}" x2="{left}" y2="{bottom}"/>')
        svg.append(f'<line class="axis" x1="{left}" y1="{bottom}" x2="{left + plot_width}" y2="{bottom}"/>')
        for row in subset:
            mode = row["mode"]
            service_cost = ((row["foreground_service_s"] or 0)
                            + (row["background_service_s"] or 0))
            radius = 5 + 1.45 * math.log1p(service_cost)
            delivery_pct = 100 * row["delivery_fraction_all_offered"]
            x, y = xy(panel, delivery_pct, row["e2e_mean_delivered_s"])
            svg.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{colors[mode]}" fill-opacity="0.86" stroke="#202a33" stroke-width="1"><title>{esc(mode)}: delivery={delivery_pct:.2f}%, mean E2E={row["e2e_mean_delivered_s"]:.3f}s, configured foreground+background service={service_cost:.3f}s</title></circle>')
        svg.append(f'<text class="small" x="{left + plot_width / 2:.1f}" y="{bottom + 47}" text-anchor="middle">Delivered / all offered (%)</text>')
    svg.append('<text transform="translate(22 372) rotate(-90)" text-anchor="middle" font-size="14">E2E mean among delivered packets (s)</text>')
    legend_y, legend_x = 685, 90
    for i, mode in enumerate(modes):
        x = legend_x + i * 300
        svg.append(f'<circle cx="{x}" cy="{legend_y}" r="7" fill="{colors[mode]}" stroke="#202a33"/>')
        svg.append(f'<text class="small" x="{x + 14}" y="{legend_y + 5}">{esc(mode)}</text>')
    svg.append('<text x="840" y="728" text-anchor="middle" font-size="12">Marker radius scales with configured foreground + background service seconds. Query wait is excluded. Precomputed full build lifecycle is unmeasured; its recorded timer starts after BFS.</text>')
    svg.append('</svg>')
    out_path.write_text("\n".join(svg) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path,
                        help="verified pullback directory for one T1 run")
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="separate directory for derived, non-raw outputs")
    args = parser.parse_args(argv)
    modes, arms = rebuild(args.run_dir, args.out_dir)
    plot_pressure(modes, args.out_dir / "pressure-tradeoff.svg")
    print(json.dumps({
        "mode_rows": len(modes),
        "negative_control_arm_rows": len(arms),
        "outputs": {
            name: _sha256(args.out_dir / name)
            for name in ("mode-summary.csv", "negative-control-arms.csv",
                         "provenance.json", "pressure-tradeoff.svg")
        },
    }, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
