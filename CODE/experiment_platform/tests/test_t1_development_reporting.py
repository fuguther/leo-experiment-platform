import csv
import hashlib
import json

from CODE.experiment_platform import t1_development


ARMS = ("stale", "now", "common", "candidate")
MODES = ("per_packet", "per_flow", "precomputed", "async_point",
         "async_window")


def _cost():
    return {
        "packet_decisions": {"service_s_completed": 0.01},
        "background_updates": {"service_s_completed": 0.0},
        "query_service": {"service_s": 0.001},
        "install": {"installs": 1},
        "control_traffic": {"precompute": {
            "builds": 1, "targets": 24, "installs": 1,
            "cost_kind": "offline_build", "build_wall_s": 0.02}},
    }


def _metric(loss):
    packets = [
        {"pid": 1, "trace_sha256": "a" * 64, "emit_time_s": 5.0,
         "bits": 1000, "observation_end_s": 50.0, "fate": "DELIVERED",
         "delivery_record_present": True, "delivery_time_s": 11.0,
         "in_population": True,
         "deadline_loss": {"status": "COMPUTED", "value": loss,
                           "lower_bound": loss, "upper_bound": loss,
                           "deadline_s": 30.0},
         "terminal_reason": None, "censor_reason": None},
        {"pid": 2, "trace_sha256": "a" * 64, "emit_time_s": 8.0,
         "bits": 1000, "observation_end_s": 50.0, "fate": "NO_ROUTE",
         "delivery_record_present": False, "delivery_time_s": None,
         "in_population": True,
         "deadline_loss": {"status": "COMPUTED", "value": 1.0,
                           "lower_bound": 1.0, "upper_bound": 1.0,
                           "deadline_s": 30.0},
         "terminal_reason": "NO_ROUTE",
         "censor_reason": None},
    ]
    return {"status": "COMPUTED", "deadline_s": 30.0,
            "packets": 2, "exact_packets": 2,
            "interval_censored": 0, "value": loss,
            "packet_outcomes": packets}


def _task_document(task, seed):
    manifest = [{"pid": 1, "od_id": "a_to_b", "emit_time_s": 5.0},
                {"pid": 2, "od_id": "b_to_c", "emit_time_s": 8.0}]
    source = {"trace_sha256": "a" * 64,
              "synthetic_workload": {"summary": {
                  "workload_id": "transfer", "packet_manifest": manifest}}}
    deadline = {"deadline_s": 30.0,
                "population_window_s": [5.0, 20.0]}
    if task == "network_alignment":
        arms = []
        for i, arm in enumerate(ARMS):
            metric = _metric(0.2 + i * 0.01 + seed / 10000)
            arms.append({"arm": arm,
                         "outcome": {"deadline_primary_loss": metric},
                         "network_outcome": {"packet_outcomes":
                                             metric["packet_outcomes"]},
                         "total_cost": _cost()})
        return {"schema": "t1-task-result/v1", "status": "ok",
                "document": {"status": "ok", "source": source,
                             "deadline": deadline, "arms": arms}}
    modes = []
    for i, mode in enumerate(MODES):
        metric = _metric(0.25 + i * 0.01 + seed / 10000)
        modes.append({"mode": mode,
                      "network_outcome": {
                          "deadline_primary_loss": metric,
                          "packet_outcomes": metric["packet_outcomes"]},
                      "total_cost": _cost()})
    return {"schema": "t1-task-result/v1", "status": "ok",
            "document": {"status": "ok", "source": source,
                         "deadline": deadline, "modes": modes}}


def _fixtures(tmp_path, *, omit=()):
    run_dir = tmp_path / "run"
    out_dir = tmp_path / "out"
    (run_dir / "cells").mkdir(parents=True)
    out_dir.mkdir()
    cells, records = [], []
    for seed in (11, 23, 42):
        for task in ("network_alignment", "execution_modes"):
            suffix = ("network" if task == "network_alignment"
                      else "execution-modes")
            cell_id = f"b-temporal-{suffix}-seed-{seed}"
            cell = {"cell_id": cell_id, "seed": seed,
                    "args": ["--task", task]}
            cells.append(cell)
            if (seed, task) in omit:
                continue
            payload = _task_document(task, seed)
            cell_dir = run_dir / "cells" / cell_id
            cell_dir.mkdir()
            path = cell_dir / "result.json"
            path.write_text(json.dumps(payload, sort_keys=True),
                            encoding="utf-8")
            records.append({"cell_id": cell_id, "status": "ok",
                            "predicate_verdict": {"passed": True},
                            "result_path": str(path.relative_to(run_dir)),
                            "result_sha256": hashlib.sha256(
                                path.read_bytes()).hexdigest()})
    contract = {"statistics": {"deadline": {"value_s": 30.0},
                               "population_window_s": [5.0, 20.0]},
                "b_round": {"scenarios": [{
                    "id": "temporal", "expectation": "competitive_multi_od",
                    "seeds": [11, 23, 42],
                    "task_seeds": {"network_alignment": [11, 23, 42]}}]}}
    return (run_dir, out_dir, {"cells": cells}, contract,
            {"cells": records})


def test_paired_reports_use_all_verified_seed_blocks_and_keep_precomputed(tmp_path):
    run_dir, out_dir, bundle, contract, run_doc = _fixtures(tmp_path)

    output = t1_development._paired_development_outputs(
        run_dir, bundle, contract, run_doc, out_dir)
    analysis = output["analysis"]
    rows = list(csv.DictReader(open(output["packet_rows_path"], encoding="utf-8")))

    assert analysis["status"] == "COMPUTED"
    assert analysis["completed_independent_blocks"] == 3
    assert len(rows) == 3 * (len(ARMS) + len(MODES)) * 2
    assert "per_packet_loss_minus_precomputed" in analysis["comparisons"]
    assert analysis["comparisons"][
        "common_loss_minus_candidate"]["bootstrap_95_percentile_ci"]["n"] == 3
    assert analysis["blocks"][0]["costs"]["mode_precomputed"][
        "control_traffic"]["precompute"]["builds"] == 1


def test_paired_reports_mark_missing_seed_without_silent_resampling(tmp_path):
    run_dir, out_dir, bundle, contract, run_doc = _fixtures(
        tmp_path, omit=((42, "execution_modes"),))

    output = t1_development._paired_development_outputs(
        run_dir, bundle, contract, run_doc, out_dir)

    assert output["analysis"]["status"] == "INCOMPLETE"
    assert output["analysis"]["completed_independent_blocks"] == 2
    assert output["analysis"]["comparisons"] == {}
    assert output["analysis"]["missing_blocks"][0]["seed"] == 42
