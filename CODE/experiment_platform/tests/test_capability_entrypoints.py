"""Entry points added on 2026-09-24 to close the T1 capability gap.

Covers CODE/experiment_platform/fold_decision_ledger.py (B1) and
CODE/experiment_platform/replay_counterfactual.py (B2).  Before these entry
points existed, decision_ledger.build_ledger and counterfactual.py had no
production caller at all -- the line's "attributable" and "intervenable"
invariants were tested but not runnable.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/smoke.yaml"


def _run(*args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", *args], cwd=cwd,
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def streams(tmp_path_factory):
    """One real run that publishes both streams."""
    out = tmp_path_factory.mktemp("streams")
    run_dir = out / "run"
    verdict = _run("CODE.leo_sim", "run", "--config", SMOKE,
                   "--out", str(run_dir),
                   "--decision-log", str(out / "decisions.jsonl"),
                   "--timeline-log", str(out / "timeline.jsonl"))
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    return out


def test_fold_produces_the_full_eleven_instant_chain(streams):
    from CODE.leo_sim import decision_ledger

    target = streams / "ledger.json"
    done = _run("CODE.experiment_platform.fold_decision_ledger",
                "--decision-log", str(streams / "decisions.jsonl"),
                "--timeline-log", str(streams / "timeline.jsonl"),
                "--out", str(target))
    assert done.returncode == 0, done.stdout + done.stderr
    document = json.loads(target.read_text())
    assert document["schema"] == "decision-ledger-fold/v1"
    assert document["by_decision"], "the fold produced no decisions"
    for decision_id, record in document["by_decision"].items():
        missing = [f for f in decision_ledger.TIMELINE_FIELDS if f not in record]
        assert not missing, (decision_id, missing)
    assert document["diagnostics"]["duplicate_decision_ids"] == []


def test_the_fold_is_bound_to_the_streams_it_read(streams, tmp_path):
    target = tmp_path / "ledger.json"
    done = _run("CODE.experiment_platform.fold_decision_ledger",
                "--decision-log", str(streams / "decisions.jsonl"),
                "--timeline-log", str(streams / "timeline.jsonl"),
                "--out", str(target))
    assert done.returncode == 0, done.stdout + done.stderr
    source = json.loads(target.read_text())["source"]
    import hashlib
    for name in ("decision_log", "timeline_log"):
        raw = Path(source[name]).read_bytes()
        assert source[f"{name}_sha256"] == hashlib.sha256(raw).hexdigest()


def test_fold_refuses_to_overwrite_an_existing_fold(streams, tmp_path):
    target = tmp_path / "ledger.json"
    target.write_text("{}")
    done = _run("CODE.experiment_platform.fold_decision_ledger",
                "--decision-log", str(streams / "decisions.jsonl"),
                "--timeline-log", str(streams / "timeline.jsonl"),
                "--out", str(target))
    assert done.returncode == 2
    assert "FOLD REFUSED" in done.stdout
    assert target.read_text() == "{}", "the existing fold was touched"


def test_fold_refuses_a_malformed_stream(streams, tmp_path):
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}\n")
    done = _run("CODE.experiment_platform.fold_decision_ledger",
                "--decision-log", str(bad),
                "--timeline-log", str(streams / "timeline.jsonl"),
                "--out", str(tmp_path / "ledger.json"))
    assert done.returncode == 2
    assert "FOLD REFUSED" in done.stdout


def _first_decision_with_an_alternative(streams):
    from CODE.leo_sim import decision_ledger  # noqa: F401
    for line in (streams / "decisions.jsonl").read_text().splitlines():
        row = json.loads(line)
        alternatives = [c for c in (row.get("candidates") or [])
                        if c != row.get("chosen")]
        if alternatives:
            return row["decision_id"], alternatives[0]
    pytest.skip("no decision offered an alternative action")


def test_replay_proves_the_branch_point_and_changes_one_action(streams, tmp_path):
    decision_id, forced = _first_decision_with_an_alternative(streams)
    target = tmp_path / "replay.json"
    done = _run("CODE.experiment_platform.replay_counterfactual",
                "--config", SMOKE, "--decision-id", str(decision_id),
                "--forced-action", forced, "--root", str(ROOT),
                "--out", str(target))
    assert done.returncode == 0, done.stdout + done.stderr
    document = json.loads(target.read_text())
    assert document["schema"] == "counterfactual-replay/v1"
    verification = document["verification"]
    assert verification["branch_states_identical"] is True
    assert verification["action_changed"] is True
    assert verification["legal_at_branch_point"] is True
    assert verification["forced_action"] == forced
    assert document["outcome_delta"], "no outcome was compared"


def test_replay_refuses_a_learning_config(tmp_path):
    """The harness requires a deterministic router; the driver must not hide it."""
    config = tmp_path / "learning.yaml"
    config.write_text(
        (ROOT / SMOKE).read_text()
        + "\nlearning:\n  algorithm: qlearning\n")
    done = _run("CODE.experiment_platform.replay_counterfactual",
                "--config", str(config), "--decision-id", "1",
                "--forced-action", "N", "--root", str(ROOT),
                "--out", str(tmp_path / "replay.json"))
    assert done.returncode == 2
    assert "REPLAY REFUSED" in done.stdout


def test_replay_refuses_to_overwrite_an_existing_result(streams, tmp_path):
    decision_id, forced = _first_decision_with_an_alternative(streams)
    target = tmp_path / "replay.json"
    target.write_text("{}")
    done = _run("CODE.experiment_platform.replay_counterfactual",
                "--config", SMOKE, "--decision-id", str(decision_id),
                "--forced-action", forced, "--root", str(ROOT),
                "--out", str(target))
    assert done.returncode == 2
    assert "REPLAY REFUSED" in done.stdout
    assert target.read_text() == "{}"
