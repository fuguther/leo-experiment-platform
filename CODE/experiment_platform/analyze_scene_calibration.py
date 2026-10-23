#!/usr/bin/env python3
"""Reduce scene-calibration cells to the predicted-vs-measured tables and the
three calibration figures.

Inputs are pulled run directories (one per cell) plus the calibration record
that produced the injection rates.  Outputs are CSV/JSON tables and PNG
figures; nothing here re-runs a simulation and nothing here changes a cell.

The point of the comparison table is falsification, not decoration: the
calibration predicts rho_e = (R*b_e + c_e)/C_e on one named directed link from
a FIXED reference route set, and this script measures that same link in the
kernel.  A disagreement is a result about the reference model, not a bug to
hide.
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import json
import math
import statistics
import sys
from pathlib import Path

def _repo_root():
    """The checkout this file lives in, or None when it is run from elsewhere
    (the script only needs it for one optional default path)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "CODE/leo_sim").is_dir():
            return parent
    return None


REPO = _repo_root()


# --------------------------------------------------------------------------
def load_cell(run_dir: Path):
    """Return {cell_id, arm, summary, result} for one pulled run directory."""
    cal = run_dir / "cal"
    if not cal.is_dir():
        return None
    for cell_dir in sorted(p for p in cal.iterdir() if p.is_dir()):
        summary_path = cell_dir / "summary.json"
        if not summary_path.exists():
            continue
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for arm_dir in sorted((cell_dir / "arms").iterdir()):
            result_path = arm_dir / "result.json.gz"
            if not result_path.exists():
                continue
            with gzip.open(result_path, "rt", encoding="utf-8") as fh:
                result = json.load(fh)
            return dict(cell=cell_dir.name, arm=arm_dir.name,
                        summary=summary, result=result, run_dir=run_dir)
    return None


def isl_link_rows(result):
    """Per directed ISL: served bits, capacity, utilisation, control bits."""
    links = (result.get("congestion_metrics") or {}).get("links") or {}
    rows = {}
    for link_id, rec in links.items():
        if rec.get("stage") != "isl":
            continue
        rows[link_id] = dict(
            link_id=link_id,
            served_bits=float(rec.get("served_bits") or 0.0),
            capacity_bits=float(rec.get("available_capacity_bits") or 0.0),
            available_time_s=float(rec.get("available_time_s") or 0.0),
            service_windows=int(rec.get("service_windows") or 0),
            utilization=float(rec.get("utilization") or 0.0))
    return rows


def per_packet(result):
    return (result.get("congestion_metrics") or {}).get("packets") or {}


def hops_per_packet(result):
    hops = collections.Counter()
    for ev in result.get("packet_events") or []:
        if ev.get("kind") == "propagation_start" and ev.get("stage") == "isl":
            hops[ev["pid"]] += 1
    return hops


def queue_timeline(result, link_id):
    """(t, bits_in_queue) series for one directed ISL, from raw events.

    Occupancy is reconstructed by conservation (queue_enter adds, service_start
    removes) rather than read from a counter, so it can be checked against the
    queue-area integral independently.
    """
    events = []
    for ev in result.get("packet_events") or []:
        if ev.get("link_id") != link_id:
            continue
        if ev.get("kind") == "queue_enter":
            events.append((ev["at"], +ev.get("bits", 0)))
        elif ev.get("kind") == "service_start":
            events.append((ev["at"], -ev.get("bits", 0)))
    events.sort()
    t_out, q_out = [], []
    q = 0
    for t, d in events:
        q += d
        t_out.append(t)
        q_out.append(q)
    return t_out, q_out


def percentile(values, q):
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize_cell(cell, calibration):
    result, summary = cell["result"], cell["summary"]
    arm = next((a for a in summary.get("arms") or []
                if a.get("arm") == cell["arm"]), {})
    links = isl_link_rows(result)
    pk = per_packet(result)
    hops = hops_per_packet(result)

    # the calibrated bottleneck edge, as a directed satellite pair
    be = calibration["link_coefficients"]
    edge = be.get("max_b_e_edge")
    bottleneck = None
    measured_bottleneck_util = None
    measured_bottleneck_link = None
    if edge is not None:
        want = f"isl:{edge[0]}:{edge[1]}"
        cand = [v for k, v in links.items() if k.endswith(f":{edge[0]}:{edge[1]}")
                or k == want]
        if cand:
            measured_bottleneck_util = cand[0]["utilization"]
            measured_bottleneck_link = cand[0]["link_id"]
    if links:
        busiest = max(links.values(), key=lambda r: r["utilization"])
        bottleneck = busiest

    qw = [float(v.get("queue_wait_s") or 0.0) for v in pk.values()]
    tx = [float(v.get("tx_s") or 0.0) for v in pk.values()]
    prop = [float(v.get("prop_s") or 0.0) for v in pk.values()]
    hold = [float(v.get("holding_wait_s") or 0.0) for v in pk.values()]
    pre = [float(v.get("pre_ingress_s") or 0.0) for v in pk.values()]
    e2e = [float(v.get("e2e_s") or 0.0) for v in pk.values()]
    fates = result.get("fate_counts") or {}
    offered = int(result.get("totals", {}).get("offered_bits") or 0)
    delivered = int(result.get("totals", {}).get("delivered_bits") or 0)
    horizon = float(result.get("stop_time_s") or 0.0)
    qa = result.get("queue_area_bits_s") or {}

    # the injection actually realised, from the emitted events (not the config)
    emitted_bits = sum(ev.get("bits", 0)
                       for ev in result.get("packet_events") or []
                       if ev.get("kind") == "packet_emitted")
    row = dict(
        cell=cell["cell"], arm=cell["arm"],
        run_dir=str(cell["run_dir"].name),
        wall_s=float(arm.get("execution_wall_s") or 0.0),
        events=int(result.get("events_processed") or 0),
        sim_stop_s=horizon,
        packets_offered=len(pk),
        emitted_bits=emitted_bits,
        delivered_bits=delivered,
        delivery_ratio=(delivered / offered) if offered else None,
        isl_queue_overflow=int(fates.get("ISL_QUEUE_OVERFLOW") or 0),
        no_route=int(fates.get("NO_ROUTE") or 0),
        in_system_at_stop=int(fates.get("IN_SYSTEM_AT_STOP") or 0),
        access_rejected=int(fates.get("ACCESS_REJECTED") or 0),
        holding_overflow=int(fates.get("HOLDING_QUEUE_OVERFLOW") or 0),
        isl_links_active=len(links),
        isl_util_max=(bottleneck["utilization"] if bottleneck else None),
        isl_util_max_link=(bottleneck["link_id"] if bottleneck else None),
        isl_util_mean=(statistics.fmean([r["utilization"] for r in links.values()])
                       if links else None),
        bottleneck_link=measured_bottleneck_link,
        bottleneck_util=measured_bottleneck_util,
        isl_util_sum=sum(r["utilization"] for r in links.values()),
        hops_mean=(statistics.fmean(hops.values()) if hops else 0.0),
        hops_p95=percentile(list(hops.values()), 0.95),
        q_isl_mean_s=statistics.fmean(qw) if qw else 0.0,
        q_isl_p95_s=percentile(qw, 0.95),
        tx_mean_s=statistics.fmean(tx) if tx else 0.0,
        tx_p95_s=percentile(tx, 0.95),
        prop_mean_s=statistics.fmean(prop) if prop else 0.0,
        hold_mean_s=statistics.fmean(hold) if hold else 0.0,
        hold_p95_s=percentile(hold, 0.95),
        pre_ingress_mean_s=statistics.fmean(pre) if pre else 0.0,
        e2e_mean_s=statistics.fmean(e2e) if e2e else 0.0,
        e2e_p95_s=percentile(e2e, 0.95),
        queue_area_isl_data=float(qa.get("isl_data") or 0.0),
        queue_area_isl_ctrl=float(qa.get("isl_ctrl") or 0.0),
        queue_area_holding=float(qa.get("holding") or 0.0),
        access_wait_total=float((arm.get("access") or {}).get("wait_time_s_total") or 0.0),
        access_wait_max=float((arm.get("access") or {}).get("wait_time_s_max") or 0.0),
        # control vs data share of the SAME ISL capacity, in link-seconds
        control_offered_bits=int(((result.get("control") or {}).get("bits") or {}).get("offered") or 0),
        control_isl_link_s=float((result.get("occupied") or {}).get("ctrl_isl_s") or 0.0),
        data_isl_link_s=float((result.get("occupied") or {}).get("isl_s") or 0.0),
        control_generated=int((result.get("control") or {}).get("generated") or 0),
    )
    total_isl_link_s = row["control_isl_link_s"] + row["data_isl_link_s"]
    row["control_share_of_isl"] = (row["control_isl_link_s"] / total_isl_link_s
                                   if total_isl_link_s else None)
    return row, links, pk, hops, result


# --------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", nargs="+", required=True,
                    help="one or more pulled run directories")
    ap.add_argument("--calibration", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--figures", action="store_true")
    args = ap.parse_args(argv)

    calibration = json.loads(Path(args.calibration).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    spec_path = (REPO / "CODE/work/WP-T1-SCENE-CAL/scene-spec-trunk-v1.json"
                 if REPO else None)
    spec = (json.loads(spec_path.read_text(encoding="utf-8"))
            if spec_path and spec_path.exists() else {})

    cells, all_links, per_packet_rows = [], {}, []
    timelines = {}
    for run in args.runs:
        cell = load_cell(Path(run))
        if cell is None:
            print(f"WARN no completed cell in {run}", file=sys.stderr)
            continue
        row, links, pk, hops, result = summarize_cell(cell, calibration)
        # the injection rate this cell declared
        cfg = None
        arm_dir = Path(run) / "cal" / cell["cell"] / "arms" / cell["arm"]
        for cand in (arm_dir / "config.json", Path(run) / "cal" / cell["cell"] / "resolved-config.json"):
            if cand.exists():
                raw = json.loads(cand.read_text(encoding="utf-8"))
                cfg = raw.get("resolved_config") or raw
                break
        if cfg:
            dm = cfg["config"]["demand"] if "config" in cfg else cfg["demand"]
            row["offered_mbps"] = float(dm["offered_mbps"])
            row["burst_multiplier"] = float(dm.get("burst_multiplier") or 1.0)
            row["burst_duration_s"] = float(dm.get("burst_duration_s") or 0.0)
            row["packet_bits"] = int(dm["packet_bits"])
            row["isl_rate_mbps"] = float(cfg["config"]["links"]["isl_rate_mbps"] if "config" in cfg
                                         else cfg["links"]["isl_rate_mbps"])
            row["isl_queue_bits"] = float(cfg["config"]["links"]["isl_queue_bits"] if "config" in cfg
                                          else cfg["links"]["isl_queue_bits"])
            row["demand_mode"] = dm.get("mode")
            row["compute_servers"] = float(cfg["config"]["execution"]["compute_servers_per_satellite"]
                                           if "config" in cfg else cfg["execution"]["compute_servers_per_satellite"])
            row["compute_delay_s"] = float(cfg["config"]["execution"]["compute_delay_s"]
                                           if "config" in cfg else cfg["execution"]["compute_delay_s"])
        # predicted utilisation of the calibrated bottleneck edge
        edge = calibration["link_coefficients"].get("max_b_e_edge")
        if edge is not None and row.get("offered_mbps"):
            b = calibration["_b_e"][f"{edge[0]},{edge[1]}"] if "_b_e" in calibration else calibration["link_coefficients"]["max_b_e"]
            c = calibration.get("_c_e", {}).get(f"{edge[0]},{edge[1]}", 0.0)
            cap = row["isl_rate_mbps"] * 1e6
            row["predicted_bottleneck_rho"] = (row["offered_mbps"] * 1e6 * b + c) / cap
        cells.append(row)
        for k, v in links.items():
            all_links.setdefault(k, []).append((row["cell"], v))
        if edge is not None:
            want = f"{edge[0]}:{edge[1]}"
            for k, v in links.items():
                if k.endswith(want):
                    t, q = queue_timeline(result, v["link_id"])
                    timelines[(row["cell"], v["link_id"])] = (t, q, float(row["isl_rate_mbps"]) * 1e6)
                    break

    if not cells:
        print("no cells", file=sys.stderr)
        return 2
    keys = sorted({k for c in cells for k in c})
    with (out / "per-cell.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys)
        wr.writeheader()
        for c in sorted(cells, key=lambda r: (r.get("offered_mbps") or 0)):
            wr.writerow(c)
    with (out / "per-link.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["cell", "link_id", "utilization", "served_bits", "capacity_bits",
                     "available_time_s", "service_windows"])
        for link_id in sorted(all_links):
            for cell_id, rec in all_links[link_id]:
                wr.writerow([cell_id, link_id, repr(rec["utilization"]),
                             repr(rec["served_bits"]), repr(rec["capacity_bits"]),
                             repr(rec["available_time_s"]), rec["service_windows"]])
    with (out / "bottleneck-queue-timeline.csv").open("w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["cell", "link_id", "t_s", "queue_bits", "queue_packets"])
        for (cell_id, link_id), (t, q, rate) in sorted(timelines.items()):
            for ti, qi in zip(t, q):
                wr.writerow([cell_id, link_id, repr(ti), qi, repr(qi / 12000.0)])
    (out / "predicted-vs-measured.json").write_text(
        json.dumps(dict(cells=cells,
                        calibration_definition=calibration.get("definitions"),
                        bottleneck_edge=calibration["link_coefficients"].get("max_b_e_edge"),
                        max_b_e=calibration["link_coefficients"].get("max_b_e")),
                   indent=1, sort_keys=True) + "\n", encoding="utf-8")

    if args.figures:
        try:
            make_figures(out, cells, calibration)
        except ImportError as exc:      # tables are the deliverable; plots are not
            print(f"WARN figures skipped: {exc}", file=sys.stderr)
    print(json.dumps(dict(cells=len(cells), out=str(out)), indent=1))
    return 0


def make_figures(out: Path, cells, calibration):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_dir = out / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)

    def save(fig, stem):
        for ext in ("png", "pdf"):
            fig.savefig(fig_dir / f"{stem}.{ext}", dpi=170)

    steady = [c for c in cells if c.get("demand_mode") == "uniform"
              and c.get("predicted_bottleneck_rho") is not None]

    # F1: injection rate -> measured link utilisation
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    for c in steady:
        ax.scatter(c["offered_mbps"], c["bottleneck_util"] * 100,
                   s=48, label=c["cell"])
        ax.annotate(c["cell"].split("-")[1],
                    (c["offered_mbps"], c["bottleneck_util"] * 100),
                    fontsize=6, xytext=(3, 3), textcoords="offset points")
    xs = sorted(c["offered_mbps"] for c in steady)
    if xs:
        b = calibration["link_coefficients"]["max_b_e"]
        cap = steady[0]["isl_rate_mbps"]
        ax.plot(xs, [100 * x * b / cap for x in xs], "k--", lw=1.2,
                label=f"calibrated prediction (b_e={b:.4f})")
    ax.set_xlabel("network-total injection rate R (Mbps)")
    ax.set_ylabel("measured utilisation of the calibrated bottleneck ISL (%)")
    ax.set_title("F1  injection rate vs actual ISL utilisation")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    save(fig, "fig_scene_injection_utilisation")
    plt.close(fig)

    # F2: load -> latency components
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    comps = [("q_isl_mean_s", "ISL queue wait"),
             ("tx_mean_s", "serialization"),
             ("prop_mean_s", "propagation"),
             ("hold_mean_s", "holding / on-board node"),
             ("pre_ingress_mean_s", "access (pre-ingress)")]
    order = sorted(steady, key=lambda c: c["offered_mbps"])
    x = [c["offered_mbps"] for c in order]
    bottom = [0.0] * len(order)
    for key, label in comps:
        vals = [c.get(key) or 0.0 for c in order]
        ax.bar(range(len(order)), vals, bottom=bottom, label=label)
        bottom = [b + v for b, v in zip(bottom, vals)]
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([f"{c['offered_mbps']:.0f}\n{c['cell'].split('-')[1]}"
                        for c in order], fontsize=6, rotation=90)
    ax.set_xlabel("network-total injection rate (Mbps) / target tier")
    ax.set_ylabel("mean per-packet latency component (s)")
    ax.set_title("F2  load vs latency components (delivered packets)")
    ax.grid(alpha=0.3, axis="y")
    ax.legend(fontsize=7)
    fig.tight_layout()
    save(fig, "fig_scene_latency_decomposition")
    plt.close(fig)

    # F3: burst -> hotspot queue over time
    lines = {}
    path = out / "bottleneck-queue-timeline.csv"
    if path.exists():
        for row in csv.DictReader(path.open(newline="")):
            lines.setdefault(row["cell"], ([], []))
            lines[row["cell"]][0].append(float(row["t_s"]))
            lines[row["cell"]][1].append(float(row["queue_bits"]))
    fig, ax = plt.subplots(figsize=(7.2, 5.0))
    for cell_id, (t, q) in sorted(lines.items()):
        ax.plot(t, [v / 1e6 for v in q], lw=1.0, label=cell_id)
    for c in cells:
        if c.get("demand_mode") == "burst":
            ax.axvspan(2.0, 2.0 + (c.get("burst_duration_s") or 0),
                       alpha=0.10, color="r")
    ax.set_xlabel("simulation time (s)")
    ax.set_ylabel("queue occupancy at the bottleneck ISL (Mbit)")
    ax.set_title("F3  burst input vs hotspot queue over time")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=6)
    fig.tight_layout()
    save(fig, "fig_scene_burst_queue")
    plt.close(fig)


if __name__ == "__main__":
    raise SystemExit(main())
