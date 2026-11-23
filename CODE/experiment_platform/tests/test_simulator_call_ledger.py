import json
import time

import pytest

from CODE.experiment_platform import t1_suite
from CODE.leo_sim import kernel


def test_kernel_call_ledger_records_real_begin_and_end(tmp_path, monkeypatch):
    path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("T1_SIM_CALL_LEDGER", str(path))
    monkeypatch.setenv("T1_SIM_CALL_CONTEXT", "unit-cell")
    monkeypatch.setattr(kernel, "_run_simulation_impl",
                        lambda *args, **kwargs: {"ok": True})

    result = kernel.run_simulation({}, [])

    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert result == {"ok": True}
    assert [item["event"] for item in events] == ["begin", "end"]
    assert events[0]["call_id"] == events[1]["call_id"]
    assert events[0]["context"] == "unit-cell"
    assert events[1]["duration_s"] >= 0


def test_kernel_call_ledger_records_simulator_failures(tmp_path, monkeypatch):
    path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("T1_SIM_CALL_LEDGER", str(path))
    monkeypatch.setattr(kernel, "_run_simulation_impl",
                        lambda *args, **kwargs: (_ for _ in ()).throw(
                            RuntimeError("simulator failed")))

    with pytest.raises(RuntimeError, match="simulator failed"):
        kernel.run_simulation({}, [])

    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [item["event"] for item in events] == ["begin", "fail"]
    assert events[1]["error_type"] == "RuntimeError"


def test_outer_cell_timeout_closes_open_simulator_call(tmp_path):
    path = tmp_path / "calls.jsonl"
    path.write_text(json.dumps({
        "schema": "t1-simulator-call/v1", "call_id": "cell:0001",
        "sequence": 1, "context": "cell", "event": "begin",
        "wall_time_ns": time.time_ns(),
        "monotonic_ns": time.perf_counter_ns() - 50_000_000,
        "packet_count": 3,
    }) + "\n", encoding="utf-8")

    report = t1_suite._finalize_simulator_call_ledger(path, timed_out=True)

    assert report["started"] == 1 and report["timed_out"] == 1
    assert report["unresolved"] == 0
    assert report["simulator_wall_s"] >= 0.05
    assert json.loads(path.read_text().splitlines()[-1])["reason"] == \
        "outer_cell_wall_timeout"


def test_call_aggregate_does_not_lose_failed_or_timed_out_counts():
    aggregate = t1_suite._aggregate_simulator_calls([
        {"simulator_calls": {"started": 3, "ended": 1, "failed": 1,
                             "timed_out": 1, "simulator_wall_s": 8.0}},
        {"simulator_calls": {"started": 1, "ended": 1, "failed": 0,
                             "timed_out": 0, "simulator_wall_s": 2.0}},
    ])

    assert aggregate["started"] == 4
    assert aggregate["terminal"] == 4
    assert aggregate["unaccounted"] == 0
    assert aggregate["simulator_wall_s"] == 10.0
