"""Outcome-oriented acceptance runner for the LEO V2 platform.

This is intentionally different from the unit-test suite.  It executes a
small set of real Walker-geometry scenarios through the public runtime and
checks that the requested mechanism was actually observed in the resulting
event/fate ledgers.  A scenario that merely reaches the horizon is not a pass.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from . import config, kernel, receipt, trace


PROFILE_DIR = Path(__file__).resolve().parent / "profiles" / "acceptance"
SCENARIOS = ("direct", "k1", "bbm", "mbb", "ge")


class AcceptanceError(RuntimeError):
    pass


def _max_satellite_occupancy(events: list[dict]) -> int:
    holders: dict[int, set[str]] = {}
    maximum = 0
    for event in events:
        sat = event.get("sat")
        endpoint = event.get("endpoint")
        if not isinstance(sat, int) or not isinstance(endpoint, str):
            continue
        if event.get("type") == "associate":
            holders.setdefault(sat, set()).add(endpoint)
            maximum = max(maximum, len(holders[sat]))
        elif event.get("type") == "release":
            holders.setdefault(sat, set()).discard(endpoint)
    return maximum


def _case_checks(name: str, result: dict) -> dict[str, bool]:
    events = result["handover"]["events"]
    fates = result["fate_counts"]
    access = result["access"]
    effective = result["mechanisms"]["effective"]
    common = {
        "natural_end": result["natural_end"] is True,
        "data_conservation": result["conservation_ok"] is True,
            "delivered_data": fates["DELIVERED"] > 0,
    }
    if name == "direct":
        # routing_label is ORACLE_LABEL ("analysis_upper_bound") for oracle
        # policy and None otherwise; "!= 'oracle'" would be always true and
        # would pass even if the direct scenario accidentally used oracle
        return {
            **common,
            "multi_satellite_data_service": result["occupied"]["isl_s"] > 0,
            "control_packets_arrived": result["control"]["counters"]["arrived"] > 0,
            "control_plane_used": effective["control_plane"] is True,
            "non_oracle_routing": result["routing_label"] is None,
        }
    if name == "k1":
        return {
            **common,
            "access_requests_observed": access["requests"] > 0,
            "queued_wait_observed": access["wait_time_s_max"] > 0,
            "queued_grants_observed": access["grants"] > 0,
            "single_slot_never_exceeded": _max_satellite_occupancy(events) <= 1,
        }
    if name == "bbm":
        return {
            **common,
            "bbm_switch_observed": any(e.get("type") == "bbm" for e in events),
            "old_link_broken": access["releases"].get("bbm_switch", 0) > 0,
        }
    if name == "mbb":
        return {
            **common,
            "mbb_switch_observed": any(e.get("type") == "mbb" for e in events),
            "mbb_effective_receipt": effective["mbb"] is True,
            "old_link_retired": any(
                str(reason).startswith("mbb_") and count > 0
                for reason, count in access["releases"].items()),
        }
    if name == "ge":
        return {
            **common,
            "ge_send_path_evaluated": effective["ge"] is True,
            "random_outage_fate_observed": fates["RANDOM_OUTAGE_IN_FLIGHT"] > 0,
            "ge_failure_counter_observed": effective["ge_failures"] > 0,
        }
    raise AcceptanceError(f"unknown acceptance scenario: {name}")


def _run_case(name: str, out_dir: Path) -> dict:
    profile = PROFILE_DIR / f"{name}.yaml"
    resolved = config.load_config_file(str(profile))
    out_dir.mkdir(parents=True, exist_ok=False)
    manifest = trace.compile_trace(resolved, str(out_dir))
    trace_bytes = (out_dir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = hashlib.sha256(trace_bytes).hexdigest()
    manifest["__sha256"] = hashlib.sha256(
        (out_dir / "manifest.json").read_bytes()).hexdigest()
    rows = trace.load_trace(
        str(out_dir / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"],
    )
    started = time.perf_counter()
    result = kernel.run_simulation(resolved, rows)
    elapsed = time.perf_counter() - started
    run_receipt = receipt.write_run(
        str(out_dir), resolved, trace_bytes, manifest, result, rows)
    verify_errors = receipt.verify_receipt_dir(str(out_dir))
    observed = dict(result)
    observed["conservation_ok"] = run_receipt["conservation_ok"]
    observed["routing_label"] = run_receipt["routing_label"]
    checks = _case_checks(name, observed)
    checks["receipt_verified"] = not verify_errors
    return {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "profile": str(profile.relative_to(Path(__file__).resolve().parents[2])),
        "result_dir": str(out_dir),
        "wall_seconds": elapsed,
        "checks": checks,
        "receipt_errors": verify_errors,
        "outcomes": {
            "fate_counts": run_receipt["fate_counts"],
            "access": result["access"],
            "effective": run_receipt["mechanisms"]["effective"],
            "handover_event_count": len(result["handover"]["events"]),
            "occupied": result["occupied"],
            "control": result["control"]["counters"],
        },
    }


def run_acceptance(out_dir: str | Path) -> dict:
    root = Path(out_dir)
    if root.is_symlink():
        raise AcceptanceError("acceptance output directory may not be symbolic")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise AcceptanceError("acceptance output must be a new or empty directory")
    root.mkdir(parents=True, exist_ok=True)
    cases = {}
    for name in SCENARIOS:
        cases[name] = _run_case(name, root / name)
    summary = {
        "schema": "leo-sim-acceptance/v1",
        "status": "PASS" if all(row["status"] == "PASS" for row in cases.values()) else "FAIL",
        "cases": cases,
    }
    (root / "acceptance-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
