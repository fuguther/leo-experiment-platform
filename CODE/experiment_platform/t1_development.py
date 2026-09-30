"""Compile, validate, run and report the frozen T1 development matrix.

This wrapper is the single development-only entrypoint used inside an
immutable T1 release.  It cannot select the formal tier.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from CODE.experiment_platform import t1_admission, t1_suite


def _negative_control_smoke(cell, record, payload):
    """Outcome-independent A0 gate: all four real network arms must run."""
    checks = []

    def check(name, passed, value=None):
        checks.append({"name": name, "passed": bool(passed), "value": value})

    check("cell_predicate_ok", record.get("status") == "ok",
          record.get("status"))
    document = ((payload or {}).get("document") or {})
    arms = document.get("arms") or []
    names = [str(arm.get("arm")) for arm in arms]
    expected = ["stale", "now", "common", "candidate"]
    check("network_result_ok", document.get("status") == "ok",
          document.get("status"))
    check("all_four_arms_present", sorted(names) == sorted(expected), names)
    check("no_arm_failures", not document.get("failures"),
          document.get("failures"))
    for name in expected:
        row = next((arm for arm in arms if arm.get("arm") == name), {})
        scope, outcome = row.get("scope") or {}, row.get("outcome") or {}
        offered = outcome.get("offered")
        packets = scope.get("packets_in_trace")
        check(f"{name}_offered_packets_positive",
              isinstance(packets, int) and packets > 0, packets)
        check(f"{name}_forward_decisions_positive",
              isinstance(scope.get("forward_decisions"), int)
              and scope["forward_decisions"] > 0,
              scope.get("forward_decisions"))
        check(f"{name}_delivered_packets_positive",
              isinstance(outcome.get("delivered"), int)
              and outcome["delivered"] > 0, outcome.get("delivered"))
        check(f"{name}_offered_count_matches_trace", offered == packets,
              {"offered": offered, "trace": packets})
    return {
        "schema": "t1-a0-smoke-gate/v1",
        "purpose": "prove the declared negative-control network simulation ran; no arm ranking or advantage criterion",
        "cell_id": cell.get("cell_id"),
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }


def _validate_a0_smoke_cell(contract, b_dev_cells):
    smoke_contract = contract.get("a0_smoke") or {}
    smoke_scenario = str(smoke_contract.get("scenario_id") or "")
    smoke_seed = smoke_contract.get("network_seed")
    smoke_specs = [spec for spec in
                   ((contract.get("b_round") or {}).get("scenarios") or [])
                   if str(spec.get("id")) == smoke_scenario
                   and spec.get("expectation") == "negative_control"]
    if not smoke_specs or not isinstance(smoke_seed, int) \
            or isinstance(smoke_seed, bool):
        raise RuntimeError(
            "development contract must bind a seeded negative-control "
            "network smoke in a0_smoke")
    smoke_spec = smoke_specs[0]
    network_seeds = [int(value) for value in
                     (smoke_spec.get("task_seeds") or {}).get(
                         "network_alignment", smoke_spec.get("seeds", []))]
    if ("network_alignment" not in (smoke_spec.get("tasks") or [])
            or smoke_seed not in network_seeds):
        raise RuntimeError(
            "a0_smoke must name a network_alignment seed in its negative "
            "control scenario")
    expected_smoke_id = (
        f"b-{smoke_scenario}-network-seed-{smoke_seed}")
    first = b_dev_cells[0] if b_dev_cells else {}
    args = list(first.get("args") or [])
    has_network_task = any(
        args[i] == "--task" and i + 1 < len(args)
        and args[i + 1] == "network_alignment"
        for i in range(len(args)))
    if (not b_dev_cells or first.get("cell_id") != expected_smoke_id
            or first.get("driver") != "CODE.experiment_platform.t1_tasks"
            or not has_network_task):
        observed = None if not b_dev_cells else first.get("cell_id")
        raise RuntimeError(
            f"the first b_dev cell must be the predeclared negative-control "
            f"network smoke ({expected_smoke_id}); observed {observed}")
    return expected_smoke_id


def execute(contract_path: Path, out_dir: Path) -> dict:
    contract_path = Path(contract_path)
    out_dir = Path(out_dir)
    if out_dir.exists():
        raise RuntimeError(f"development output directory exists: {out_dir}")
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    if contract.get("not_formal") is not True:
        raise RuntimeError("development wrapper requires not_formal: true")
    if contract.get("not_training") is not True:
        raise RuntimeError("development wrapper refuses training contracts")
    if (contract.get("formal_design") or {}).get("ready") is True:
        raise RuntimeError("development wrapper refuses a ready formal design")

    out_dir.mkdir(parents=True)
    bundle_dir = out_dir / "bundle"
    bundle = t1_suite.compile_bundle(contract_path, bundle_dir)
    validation = t1_suite.validate_bundle(bundle_dir)
    if validation.get("valid") is not True:
        raise RuntimeError("compiled bundle did not validate")

    b_dev_ids = set((bundle.get("tiers") or {}).get("b_dev") or [])
    b_dev_cells = [cell for cell in bundle.get("cells", [])
                   if cell.get("cell_id") in b_dev_ids]
    cost = t1_suite.estimate_bundle_cost({"cells": b_dev_cells})
    limit = int(contract.get("simulator_call_budget", 80))
    if cost["simulator_calls"] > limit:
        raise RuntimeError(
            f"predeclared simulator-call estimate {cost['simulator_calls']} "
            f"exceeds budget {limit}; no simulation was started")
    (out_dir / "simulator-call-estimate.json").write_text(
        json.dumps(cost, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    _validate_a0_smoke_cell(contract, b_dev_cells)

    run_dir = out_dir / "b_dev"
    run = t1_suite.run_bundle(
        bundle_dir, "b_dev", run_dir, smoke_validator=_negative_control_smoke)
    report = t1_suite.report_run(run_dir)
    admission = t1_admission.evaluate_run(run_dir, bundle, contract)
    (run_dir / "scenario-admission.json").write_text(
        json.dumps(admission, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    summary = {
        "schema": "t1-development-pipeline/v1",
        "tier": "b_dev",
        "not_formal": True,
        "not_training": True,
        "contract_sha256": bundle["contract_sha256"],
        "execution_chain_sha256": bundle["execution_chain"][
            "combined_sha256"],
        "git_identity": bundle["identity"]["git"],
        "bundle_fingerprint": bundle["bundle_fingerprint"],
        "simulator_call_estimate": cost["simulator_calls"],
        "simulator_call_budget": limit,
        "run_status": run.get("status"),
        "run_counts": run.get("counts"),
        "report_status": report.get("run_status"),
        "admission": admission.get("summary"),
        "run_dir": str(run_dir),
    }
    (out_dir / "pipeline-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = execute(args.contract, args.out)
    except (OSError, ValueError, RuntimeError, t1_suite.SuiteError,
            t1_suite.BudgetExceeded) as exc:
        print(f"T1 DEVELOPMENT REFUSED/FAILED: {exc}")
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if (summary["run_status"] == "ok"
                  and summary["report_status"] == "ok") else 3


if __name__ == "__main__":
    raise SystemExit(main())
