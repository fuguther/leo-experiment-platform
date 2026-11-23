"""Residual fixes S-2 / S-3 / S-8, from the two-round review.

S-2: the matrix compiler only compared trace_seed / phase /
     controlled_signature while v2_analysis requires paired arms to agree on
     trace identity and input as well, so a request could compile and then
     die at analysis after the runs were paid for.
S-3: a V6 receipt names two stream files that live outside the run directory,
     so verification could only check the digests had sha256 shape.
S-8: the VM formal route never passed --decision-log/--timeline-log, so a
     real formal experiment could only ever emit a V5 receipt and produced no
     T1 evidence at all.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SMOKE = "CODE/leo_sim/profiles/smoke.yaml"


def _run(*args):
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT,
                          capture_output=True, text=True)


@pytest.fixture(scope="module")
def v6_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("residual")
    run_dir = out / "run"
    verdict = _run("CODE.leo_sim", "run", "--config", SMOKE,
                   "--out", str(run_dir),
                   "--decision-log", str(out / "d.jsonl"),
                   "--timeline-log", str(out / "t.jsonl"))
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    return out, run_dir


def test_a_supplied_stream_makes_the_v6_binding_checkable(v6_run):
    """S-3: with the streams in hand the digests are recomputed."""
    out, run_dir = v6_run
    from CODE.leo_sim import receipt as receipt_mod
    assert receipt_mod.verify_receipt_dir(
        str(run_dir), decision_log=str(out / "d.jsonl"),
        timeline_log=str(out / "t.jsonl")) == []


def test_a_tampered_stream_is_caught(v6_run, tmp_path):
    """S-3: this is what turns the V6 binding from a claim into evidence."""
    out, run_dir = v6_run
    from CODE.leo_sim import receipt as receipt_mod
    forged = tmp_path / "d.jsonl"
    forged.write_bytes((out / "d.jsonl").read_bytes() + b"\n")
    errors = receipt_mod.verify_receipt_dir(
        str(run_dir), decision_log=str(forged))
    assert any("decision_log_sha256 does not match" in e for e in errors), errors


def test_omitting_the_streams_keeps_the_old_behaviour(v6_run):
    """S-3: existing callers that verify a bare run directory still pass."""
    _out, run_dir = v6_run
    from CODE.leo_sim import receipt as receipt_mod
    assert receipt_mod.verify_receipt_dir(str(run_dir)) == []


def test_streams_supplied_for_a_v5_receipt_are_refused(tmp_path):
    """A V5 receipt makes no stream claim, so offering streams is an error."""
    from CODE.leo_sim import receipt as receipt_mod
    run_dir = tmp_path / "v5"
    verdict = _run("CODE.leo_sim", "run", "--config", SMOKE,
                   "--out", str(run_dir))
    assert verdict.returncode == 0
    log = tmp_path / "x.jsonl"
    log.write_text("{}\n")
    errors = receipt_mod.verify_receipt_dir(str(run_dir), decision_log=str(log))
    assert any("carries no stream binding" in e for e in errors), errors


def test_the_compiler_requires_what_the_analyzer_requires():
    """S-2: a request the compiler accepts must not fail at analysis for a
    pairing reason the compiler could have seen."""
    import re
    mx = (ROOT/"CODE/leo_sim/matrix.py").read_text()
    va = (ROOT/"CODE/experiment_platform/v2_analysis.py").read_text()
    # [a-z0-9_]+ and not [a-z_]+: every field this guard exists to protect
    # contains digits (trace_identity_sha256, input_sha256, code_sha256), and
    # the narrower class silently captured NEITHER side, so the assertion
    # below passed even with the compiler checks deleted.  Found in round 3.
    i = mx.index("for field, complaint in (")
    compiler = set(re.findall(r'\("([a-z0-9_]+)"', mx[i:mx.index("):", i)]))
    k = va.index("for field in (\"trace_sha256\"")
    analyzer = set(re.findall(r'"([a-z0-9_]+)"', va[k:va.index("):", k)]))
    # Non-vacuity guards: without these the test can pass while reading
    # nothing, which is exactly what the [a-z_]+ class did.
    assert "trace_identity_sha256" in compiler, sorted(compiler)
    assert "trace_identity_sha256" in analyzer, sorted(analyzer)
    assert "trace_sha256" in analyzer, sorted(analyzer)
    # Derived, and therefore covered by construction: trace_sha256 is a
    # function of trace_identity_sha256 + input_sha256, and scenario.seed is
    # written from the cell trace_seed.  Both inputs are checked directly.
    derived = {"trace_sha256", "seed"}
    assert not (analyzer - compiler - derived), sorted(analyzer - compiler - derived)


def test_the_formal_route_requests_both_evidence_streams():
    """S-8: without this a real formal experiment emits no T1 evidence."""
    import argparse
    sys.path.insert(0, str(ROOT/"CODE/scripts/remote"))
    from CODE.scripts.remote import remote_job as rj
    tmp = Path(ROOT/"out"/"_s8_test")
    rj.CANONICAL_RESULTS = tmp
    args = argparse.Namespace(runtime_kind="leo_sim_v2",
                              expected_run_id="EXP-S8-TEST",
                              launch_nonce="a"*32)
    run_dir = tmp/"EXP-S8-TEST"
    try:
        command = rj.formal_command(args, Path("/tmp"), Path("/tmp/c.yaml"),
                                    Path("/tmp/authorization.json"))
        assert "--decision-log" in command
        assert "--timeline-log" in command
        # The streams ride inside the run directory so a results pull carries
        # them, and the run directory must be left EMPTY for the formal gate.
        assert any(a.endswith("decisions.jsonl") for a in command)
        assert any(a.endswith("timeline.jsonl") for a in command)
        assert run_dir.is_dir() and not any(run_dir.iterdir())
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
