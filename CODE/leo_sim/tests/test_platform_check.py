"""Critical orchestration checks for the one-command platform outcome."""
from pathlib import Path

import pytest

from CODE.leo_sim import platform_check
from CODE.leo_sim.__main__ import main


def test_platform_check_runs_each_real_stage_once(tmp_path, monkeypatch):
    calls = []

    def fake_acceptance(out):
        calls.append(("mechanisms", Path(out)))
        return {"status": "PASS", "cases": {}}

    def fake_comparison(cfg, out):
        calls.append(("comparison", Path(out)))
        return {"status": "PASS", "same_trace": True}

    def fake_ddqn(cfg, out):
        calls.append(("ddqn", Path(out)))
        return {"status": "PASS", "checks": {}}

    def fake_population(cfg, out):
        calls.append(("population", Path(out)))
        return {"status": "PASS", "checks": {}}

    monkeypatch.setattr(platform_check.acceptance, "run_acceptance", fake_acceptance)
    monkeypatch.setattr(platform_check.comparison, "run_comparison", fake_comparison)
    monkeypatch.setattr(platform_check, "_run_ddqn_chain", fake_ddqn)
    monkeypatch.setattr(platform_check, "_run_population", fake_population)

    out = tmp_path / "platform"
    result = platform_check.run_platform_check(out)
    assert result["status"] == "PASS"
    assert [name for name, _ in calls] == [
        "mechanisms", "population", "comparison", "ddqn"]
    assert (out / "platform-summary.json").is_file()


def test_platform_check_stops_at_first_failed_stage_and_keeps_summary(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        platform_check.acceptance, "run_acceptance",
        lambda out: {"status": "PASS", "cases": {}})
    monkeypatch.setattr(
        platform_check, "_run_population",
        lambda cfg, out: {"status": "PASS", "checks": {}})

    def fail_comparison(cfg, out):
        raise RuntimeError("legacy process exited 1")

    monkeypatch.setattr(
        platform_check.comparison, "run_comparison", fail_comparison)
    monkeypatch.setattr(
        platform_check, "_run_ddqn_chain",
        lambda *args: pytest.fail("DDQN must not run after comparison failure"))

    out = tmp_path / "platform"
    result = platform_check.run_platform_check(out)
    assert result["status"] == "FAIL"
    assert result["failed_stage"] == "gateway_vs_direct"
    assert result["error"]["message"] == "legacy process exited 1"
    assert (out / "platform-summary.json").is_file()


def test_platform_check_rejects_nonempty_output(tmp_path):
    out = tmp_path / "platform"
    out.mkdir()
    (out / "existing").write_text("do not overwrite")
    with pytest.raises(platform_check.PlatformCheckError, match="new or empty"):
        platform_check.run_platform_check(out)


def test_platform_check_rejects_symlink_output(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "platform"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(platform_check.PlatformCheckError, match="symbolic link"):
        platform_check.run_platform_check(link)


def test_platform_check_cli_returns_nonzero_for_failed_outcome(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        platform_check, "run_platform_check",
        lambda *args, **kwargs: {"status": "FAIL", "failed_stage": "ddqn"})
    rc = main(["platform", "check", "--out", str(tmp_path / "out")])
    assert rc == 9
    assert '"status": "FAIL"' in capsys.readouterr().out


# ---------------------------------------------------------------- Task 8:
# bounded global population diagnostic profile (cheap path only).

DIAGNOSTIC_PROFILE = (Path(__file__).resolve().parents[1]
                      / "profiles" / "population_global_1deg_diagnostic.yaml")


def test_diagnostic_profile_compiles_byte_identical_twice(tmp_path):
    import hashlib
    import json

    from CODE.leo_sim import config, trace
    cfg = config.load_config_file(str(DIAGNOSTIC_PROFILE))
    first = trace.compile_trace(cfg, str(tmp_path / "one"))
    second = trace.compile_trace(cfg, str(tmp_path / "two"))
    for name in ("trace.csv", "manifest.json", "nested-family.json"):
        a = (tmp_path / "one" / name).read_bytes()
        b = (tmp_path / "two" / name).read_bytes()
        assert a == b, name
    assert hashlib.sha256(
        (tmp_path / "one" / "trace.csv").read_bytes()).hexdigest() == \
        hashlib.sha256(
            (tmp_path / "two" / "trace.csv").read_bytes()).hexdigest()
    assert first["trace_sha256"] == second["trace_sha256"]
    manifest = json.loads(
        (tmp_path / "one" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["population"]["candidate_regions"] == 16_988
    assert manifest["provenance"] == "population_proxy"


def test_diagnostic_profile_reports_observed_support_separately(tmp_path):
    """The manifest reports the 16,988 candidate universe; the derived
    observed source / destination / runtime endpoint sets are reported
    separately and never conflated."""
    from CODE.leo_sim import config, trace
    cfg = config.load_config_file(str(DIAGNOSTIC_PROFILE))
    manifest = trace.compile_trace(cfg, str(tmp_path / "one"))
    rows = trace.load_trace(
        str(tmp_path / "one" / "trace.csv"),
        horizon_s=30.0, max_packets=5000)
    assert manifest["population"]["candidate_regions"] == 16_988
    observed_sources = sorted({row["src_grid_id"] for row in rows})
    observed_destinations = sorted({row["dst_grid_id"] for row in rows})
    runtime_endpoints = sorted(set(observed_sources)
                               | set(observed_destinations))
    assert len(observed_sources) < 16_988
    assert len(observed_destinations) < 16_988
    assert len(runtime_endpoints) < 16_988
    assert len(runtime_endpoints) == manifest["active_endpoints"]


def test_diagnostic_family_children_strictly_nested_microbenchmark(
        tmp_path, monkeypatch):
    """Trace-only microbenchmark: child loads 5/10/20/40/80 Mbps from one 80
    Mbps master are strict multiset subsets.  No network simulations run."""
    # the diagnostic profile is already the 5 Mbps child of an 80 Mbps
    # master; compile sibling children by overriding only offered_mbps
    from CODE.leo_sim import config, trace
    from CODE.leo_sim import trace_family
    base = config.load_config_file(str(DIAGNOSTIC_PROFILE))
    master_dir = tmp_path / "master"
    master_cfg = config.resolve_config({
        **{"demand": base["config"]["demand"],
           "endpoints": base["config"]["endpoints"]},
        "demand": {**base["config"]["demand"],
                   "offered_mbps": 80.0},
        "scenario": base["config"]["scenario"],
        "execution": base["config"]["execution"],
    })
    master_manifest = trace.compile_trace(master_cfg, str(master_dir))
    master_rows = trace.load_trace(
        str(master_dir / "trace.csv"), horizon_s=30.0, max_packets=5000)
    assert len(master_rows) > 500  # non-degenerate master
    child_counts = []
    for offered in (5.0, 10.0, 20.0, 40.0, 80.0):
        child_cfg = config.resolve_config({
            "demand": {**base["config"]["demand"],
                       "offered_mbps": offered},
            "scenario": base["config"]["scenario"],
            "endpoints": base["config"]["endpoints"],
            "execution": base["config"]["execution"],
        })
        child_dir = tmp_path / f"child-{int(offered)}"
        trace.compile_trace(child_cfg, str(child_dir))
        child_rows = trace.load_trace(
            str(child_dir / "trace.csv"), horizon_s=30.0,
            max_packets=5000)
        child_counts.append(len(child_rows))
        assert trace_family.is_multiset_subset(child_rows, master_rows), \
            f"{offered} Mbps child not subset of master"
    assert child_counts[0] <= child_counts[1] <= child_counts[2] <= \
        child_counts[3] <= child_counts[4]
    assert child_counts[0] < len(master_rows)


@pytest.mark.scene_smoke
def test_diagnostic_5mbps_local_smoke(tmp_path):
    """Exactly one local 5 Mbps engineering smoke: natural end, conservation,
    receipt verification, no silent sampler fallback, resource measurements.
    A low delivery rate or ACCESS_LIMITED classification is a plausible
    cost-smoke outcome, not a bug.  The smoke is the single expensive run in
    the diagnostic package (one 30 s markovian 280-satellite sim); its wall
    time is a recorded resource measurement, not a pass criterion."""
    import json
    import resource
    import time

    from CODE.leo_sim import config, kernel, receipt, trace

    t_start = time.perf_counter()
    cfg = config.load_config_file(str(DIAGNOSTIC_PROFILE))
    resolved = cfg
    tdir = tmp_path / "trace"
    manifest = trace.compile_trace(resolved, str(tdir))
    rows = trace.load_trace(
        str(tdir / "trace.csv"),
        horizon_s=resolved["config"]["scenario"]["duration_s"],
        max_packets=resolved["config"]["execution"]["max_packets"])
    result = kernel.run_simulation(resolved, rows)
    wall_s = time.perf_counter() - t_start
    rss_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0

    assert result["natural_end"] is True
    # conservation: offered = delivered + terminal loss + in-system
    totals = result["totals"]
    assert totals["delivered_bits"] + totals["terminal_loss_bits"] +         totals["in_system_bits_at_stop"] == totals["offered_bits"]
    out = tmp_path / "run"
    tbytes = (tdir / "trace.csv").read_bytes()
    manifest["__trace_sha256"] = __import__("hashlib").sha256(
        tbytes).hexdigest()
    manifest["__sha256"] = __import__("hashlib").sha256(
        (tdir / "manifest.json").read_bytes()).hexdigest()
    receipt.write_run(str(out), resolved, tbytes, manifest, result, rows)
    errors = receipt.verify_receipt_dir(str(out))
    assert errors == [], errors[:5]

    # record resource measurements and classify by layer (cheap path only:
    # a low delivery rate is a plausible outcome for a 30 s cost smoke and
    # must be classified by the pure scene checker, never asserted as a
    # pass criterion)
    report = {
        "schema": "leo-sim-local-smoke/v1",
        "wall_s": wall_s,
        "peak_rss_kib": rss_mib,
        "events_processed": result["events_processed"],
        "fate_counts": result["fate_counts"],
        "totals": totals,
        "natural_end": result["natural_end"],
        "conservation_ok": (
            totals["delivered_bits"] + totals["terminal_loss_bits"]
            + totals["in_system_bits_at_stop"] == totals["offered_bits"]),
        "receipt_verified": True,
    }
    (tmp_path / "smoke-evidence.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert report["wall_s"] > 0
    assert report["events_processed"] > 0
