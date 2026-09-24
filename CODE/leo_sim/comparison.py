"""One-command diagnostic comparison on one immutable demand trace.

The direct arm uses leo_sim V2.  The retained arm invokes SimulationRL with
endogenous Gateway traffic disabled and injects the exact same trace into its
real Gateway uplink.  This is a demand-controlled engineering comparison, not
an algorithm-effect experiment or a claim that both geometry implementations
are physically identical.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config, kernel, receipt, trace
from CODE.legacy_trace_runtime import load_and_project_trace


class ComparisonError(RuntimeError):
    pass


@dataclass
class _GatewaySite:
    name: str
    latitude: float
    longitude: float
    active_index: int


def _canonical_sha(value: dict) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_constellation(cfg: dict) -> str:
    sc = cfg["scenario"]
    signature = (
        int(sc["num_satellites"]), int(sc["num_planes"]),
        float(sc["altitude_km"]), float(sc["inclination_deg"]),
        float(sc["min_elevation_deg"]),
    )
    known = {
        (32, 4, 1000.0, 53.0, 30.0): "small",
        (140, 7, 600.0, 98.6, 30.0): "Kepler",
        (66, 6, 780.0, 86.4, 30.0): "Iridium_NEXT",
        (648, 18, 1200.0, 86.4, 30.0): "OneWeb",
        (1584, 72, 550.0, 53.0, 25.0): "Starlink",
    }
    try:
        return known[signature]
    except KeyError as exc:
        raise ComparisonError(
            "comparison config does not match a retained legacy constellation shell: "
            f"{signature}") from exc


def _gateway_sites(code_dir: Path) -> list[_GatewaySite]:
    path = code_dir / "Gateways.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return [
        _GatewaySite(
            name=str(row["Location"]),
            latitude=float(row["Latitude"]),
            longitude=float(row["Longitude"]),
            active_index=index,
        )
        for index, row in enumerate(rows)
    ]


def _legacy_pathing(policy: str) -> str:
    mapping = {"hop": "hop", "delay": "slant_range", "capacity": "dataRate"}
    if policy not in mapping:
        raise ComparisonError(
            f"legacy comparison has no non-oracle equivalent for routing.policy={policy!r}")
    return mapping[policy]


def _write_legacy_input(path: Path, gateway_names: list[str], constellation: str,
                        duration_s: float) -> None:
    if len(gateway_names) < 2:
        raise ComparisonError("comparison requires at least two projected Gateways")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["Locations", "Constellation", "Fraction", "Test type", "Test length"])
        for index, full_name in enumerate(gateway_names):
            alias = full_name.split(",", 1)[0]
            if index == 0:
                writer.writerow([alias, constellation, 0.5, "Latency", duration_s])
            else:
                writer.writerow([alias, "", "", "", ""])


def _write_decisions_jsonl(records: list[dict], path: Path) -> str:
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False, sort_keys=True)
                         + "\n")
    return str(path)


def _direct_arm(resolved: dict, rows: list[dict], trace_bytes: bytes,
                manifest: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    decisions: list[dict] = []
    result = kernel.run_simulation(resolved, rows, decision_sink=decisions)
    wall = time.perf_counter() - started
    decisions_path = _write_decisions_jsonl(
        decisions, out_dir / "decisions.jsonl")
    run_receipt = receipt.write_run(
        str(out_dir), resolved, trace_bytes, manifest, result, rows)
    errors = receipt.verify_receipt_dir(str(out_dir))
    if not run_receipt["natural_end"] or errors:
        raise ComparisonError(
            "satellite_direct arm failed: "
            + (run_receipt.get("error") or "; ".join(errors) or "unnatural end"))
    return {
        "runtime": "satellite_direct",
        "wall_seconds": wall,
        "trace_sha256": run_receipt["trace_sha256"],
        "natural_end": run_receipt["natural_end"],
        "conservation_ok": run_receipt["conservation_ok"],
        "fate_counts": run_receipt["fate_counts"],
        "totals": run_receipt["totals"],
        "mechanisms": run_receipt["mechanisms"],
        "decisions_log": decisions_path,
        "result_dir": str(out_dir),
    }


def _legacy_decision_rows(run_trace_dir: Path) -> list[dict]:
    """Normalize the retained runtime's packet_fate diagnostic dump into
    per-hop decision rows.

    The legacy runtime is read-only: for non-learning comparison policies it
    logs only each packet's final hop path (packet_fate_log, dumped by
    flush_replay_trace, SimulationRL.py:1292; columns :870-873). Per-hop
    candidate sets and observation vectors are NOT logged by that runtime —
    those fields are null here by construction, not by omission.
    """
    parquet = run_trace_dir / "packet_fate.parquet"
    csv_gz = run_trace_dir / "packet_fate.csv.gz"
    rows: list[dict] = []
    if csv_gz.is_file():
        import gzip
        with gzip.open(csv_gz, "rt", encoding="utf-8") as handle:
            records = list(csv.DictReader(handle))
    elif parquet.is_file():
        try:
            import pandas as pd
        except ImportError as exc:
            raise ComparisonError(
                "legacy packet_fate dump is parquet but pandas is unavailable; "
                "cannot normalize legacy decision rows") from exc
        records = pd.read_parquet(parquet).to_dict("records")
    else:
        raise ComparisonError(
            f"legacy packet_fate dump missing in {run_trace_dir} "
            "(SIM_LOG_LEVEL=1 was requested)")
    for rec in records:
        path_ids = [p for p in str(rec["path_csv"]).split("|") if p]
        status = int(rec["status"])
        terminal = "DELIVERED" if status == 0 else "LOST"
        for i, sat_id in enumerate(path_ids):
            nxt = path_ids[i + 1] if i + 1 < len(path_ids) else terminal
            rows.append({
                "t": None,
                "pid": str(rec["block_id"]),
                "od_pair": str(rec["od_pair"]),
                "hop": i,
                "sat": sat_id,
                "kind": "forward" if i + 1 < len(path_ids) else "terminal",
                "chosen": nxt,
                "candidates": None,
                "obs": None,
                "source": "legacy_packet_fate_log",
            })
    return rows


def _legacy_arm(resolved: dict, trace_path: Path, trace_sha: str,
                selected: list[_GatewaySite], out_dir: Path, code_dir: Path) -> dict:
    cfg = resolved["config"]
    legacy_root = out_dir / "results"
    legacy_root.mkdir(parents=True, exist_ok=False)
    input_rl = out_dir / "inputRL.csv"
    constellation = _legacy_constellation(cfg)
    _write_legacy_input(
        input_rl, [site.name for site in selected], constellation,
        float(cfg["scenario"]["duration_s"]),
    )
    trace_cfg = {"mode": "trace", "trace_sha256": trace_sha}
    env = os.environ.copy()
    env.update({
        "MPLBACKEND": "Agg",
        "SIM_PATHING": _legacy_pathing(cfg["routing"]["policy"]),
        "SIM_FAST": "1",
        "SIM_FAIL_CLOSED": "1",
        "SIM_GTS": str(len(selected)),
        "SIM_TIME_LIMIT": str(float(cfg["scenario"]["duration_s"])),
        "SIM_MOVEMENT_TIME": str(float(cfg["scenario"]["time_step_s"])),
        # Legacy defaults compress orbital time by ~290x and Kepler uses a
        # Walker-star half-RAAN layout.  The direct kernel uses physical
        # seconds and Walker-delta; align both explicitly for this comparison
        # without changing retained legacy defaults elsewhere.
        "SIM_MOVEMENT_SPEEDUP": "1",
        "SIM_WALKER_PATTERN": "delta",
        "SIM_INPUT_RL_PATH": str(input_rl.resolve()),
        "SIM_TRAFFIC_TRACE_PATH": str(trace_path.resolve()),
        "SIM_EXPECTED_TRAFFIC_TRACE_SHA256": trace_sha,
        "SIM_TRAFFIC_TRACE_MAX_PACKETS": str(int(cfg["execution"]["max_packets"])),
        "SIM_REQUESTED_TRAFFIC_MODE": "trace",
        "SIM_EXPECTED_TRAFFIC_CONFIG_SHA256": _canonical_sha(trace_cfg),
        "SIM_RESULTS_ROOT": str(legacy_root.resolve()),
        "SIM_SEED": str(int(cfg["scenario"]["seed"])),
        "SIM_GSL_KEEP_STABLE": "1",
        # LOG_LEVEL 1 enables the retained runtime's packet_fate diagnostic
        # dump (output only; no simulation semantics change) so the legacy
        # arm can emit per-packet hop-path decision snapshots.
        "SIM_LOG_LEVEL": "1",
        "SIM_GSL_HANDOVER_MODE": (
            "mbb" if cfg["access"]["association"] == "mbb" else "legacy"),
    })
    log_path = out_dir / "legacy.log"
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            [sys.executable, str(code_dir / "SimulationRL.py")],
            cwd=str(code_dir), env=env, stdout=log, stderr=subprocess.STDOUT,
            check=False,
        )
    wall = time.perf_counter() - started
    if completed.returncode != 0:
        raise ComparisonError(
            f"legacy_gateway arm exited {completed.returncode}; inspect {log_path}")
    receipts = list(legacy_root.glob("*/run_trace/run_meta.json"))
    if len(receipts) != 1:
        raise ComparisonError(
            f"legacy_gateway arm produced {len(receipts)} run receipts, expected exactly one")
    meta = json.loads(receipts[0].read_text(encoding="utf-8"))
    trace_receipt = meta.get("trace_traffic")
    if not isinstance(trace_receipt, dict) or not trace_receipt.get("valid"):
        raise ComparisonError("legacy_gateway trace receipt is absent or invalid")
    if trace_receipt.get("trace_sha256") != trace_sha:
        raise ComparisonError("legacy_gateway consumed a different trace SHA-256")
    decisions_path = _write_decisions_jsonl(
        _legacy_decision_rows(receipts[0].parent),
        out_dir / "decisions.jsonl")
    return {
        "runtime": "legacy_gateway",
        "wall_seconds": wall,
        "trace_sha256": trace_sha,
        "natural_end": bool(meta.get("natural_end")),
        "conservation_ok": not trace_receipt.get("errors"),
        "packets": trace_receipt["packets"],
        "bits": trace_receipt["bits"],
        "projection": trace_receipt["projection"],
        "decisions_log": decisions_path,
        "result_dir": str(receipts[0].parent.parent),
        "log": str(log_path),
    }


def run_comparison(config_path: str | Path, out_dir: str | Path) -> dict:
    root = Path(out_dir).resolve()
    if root.is_symlink() or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
        raise ComparisonError("comparison output must be a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)
    resolved = config.load_config_file(str(config_path))
    cfg = resolved["config"]
    if cfg["learning"]["algorithm"] != "none":
        raise ComparisonError("comparison runner currently accepts non-learning routing only")
    if cfg["links"]["ge_enabled"]:
        raise ComparisonError("disable GE for access-path comparison; outage parameters are not calibrated across runtimes")
    code_dir = Path(__file__).resolve().parents[1]
    trace_dir = root / "immutable_trace"
    manifest = trace.compile_trace(resolved, str(trace_dir))
    trace_path = trace_dir / "trace.csv"
    trace_bytes = trace_path.read_bytes()
    trace_sha = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__trace_sha256"] = trace_sha
    manifest["__sha256"] = hashlib.sha256((trace_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(trace_path), horizon_s=cfg["scenario"]["duration_s"],
        max_packets=cfg["execution"]["max_packets"])

    sites = _gateway_sites(code_dir)
    projected, _ = load_and_project_trace(
        trace_path, sites, horizon_s=cfg["scenario"]["duration_s"],
        expected_sha256=trace_sha, max_packets=cfg["execution"]["max_packets"])
    used_indices = sorted({row["source_gateway"].active_index for row in projected}
                          | {row["destination_gateway"].active_index for row in projected})
    selected = [sites[index] for index in used_indices]

    direct = _direct_arm(
        resolved, rows, trace_bytes, manifest, root / "satellite_direct")
    legacy_dir = root / "legacy_gateway"
    legacy_dir.mkdir(exist_ok=False)
    legacy = _legacy_arm(
        resolved, trace_path, trace_sha, selected, legacy_dir, code_dir)
    same_trace = direct["trace_sha256"] == legacy["trace_sha256"] == trace_sha
    checks = {
        "same_trace": same_trace,
        "same_offered_bits": (
            int(direct["totals"]["offered_bits"])
            == int(legacy["bits"]["offered"])),
        "direct_natural_end": direct["natural_end"] is True,
        "legacy_natural_end": legacy["natural_end"] is True,
        "direct_conservation": direct["conservation_ok"] is True,
        "legacy_conservation": legacy["conservation_ok"] is True,
        "direct_delivered": int(direct["fate_counts"]["DELIVERED"]) > 0,
        "legacy_delivered": int(legacy["packets"]["delivered"]) > 0,
    }
    summary = {
        "schema": "leo-sim-access-comparison/v1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "comparison_scope": "same immutable demand, physical time scale, Walker-delta pattern and shell parameters; runtime geometry implementations remain distinct",
        "alignment": {
            "movement_speedup": 1.0,
            "walker_pattern": "delta",
            "topology_tick_s": float(cfg["scenario"]["time_step_s"]),
        },
        "scientific_effect_claim": False,
        "trace_sha256": trace_sha,
        "same_trace": same_trace,
        "checks": checks,
        "seed": cfg["scenario"]["seed"],
        "decision_snapshots": {
            "satellite_direct": "per-hop: candidates + chosen + own queues + observation summary (kernel decision sink, output only)",
            "legacy_gateway": "per-packet hop path only (packet_fate_log); per-hop candidates/observations are not logged by the retained read-only runtime",
        },
        "arms": {"satellite_direct": direct, "legacy_gateway": legacy},
    }
    (root / "comparison-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
