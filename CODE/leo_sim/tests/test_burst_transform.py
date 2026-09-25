"""The burst transform is checked deterministically; the draw is not judged.

Independent review, 2026-09-25: _burst_cfg() defaults at seed 222 produced 198
in-window packets and the 3-sigma intensity gate REFUSED the compile, with an
unmodified generator.  Scanning seeds 1..400 found two such seeds (222, 227)
while the realized in-window mean over those 400 seeds was 248.40 against an
expected 250 (sample sd 15.57, theoretical 15.81): a correct generator inside a
wrong test.

The two questions are now separated:

  * "does the generator apply the declared transform" -- a property of the code
    and the config, checked deterministically by verify_burst_transform();
  * "where did this draw land" -- a property of the seed, reported as a
    diagnostic, never a gate.

Nothing re-seeds or retries: one seed, one generation, one report.
"""
from __future__ import annotations

import hashlib

import pytest

from CODE.leo_sim import config as config_mod
from CODE.leo_sim import trace as trace_mod

#: PRE-DECLARED Monte-Carlo design for the calibration test, fixed before any
#: sample was looked at.  The mean of N independent Poisson(m) draws has
#: standard deviation sqrt(m/N), so the acceptance band on the MEAN is
#: CALIBRATION_SIGMA * sqrt(m / N).  N is chosen per multiplier so the design
#: can separate the declared rate from the no-burst rate (see
#: REQUIRED_POWER_BANDS); the power is asserted before the result is used.
CALIBRATION_SEEDS = {5.0: 40, 1.2: 200}
CALIBRATION_SIGMA = 3.0
#: A design is only worth running if it can tell the declared rate from the
#: no-burst rate: that gap must exceed this many bands.
REQUIRED_POWER_BANDS = 4.0

REFERENCE_MBPS = 40.0
REFERENCE_WINDOW_S = 10.0
REFERENCE_BITS = 8_000_000


def test_the_compile_gate_probes_every_endpoint_longitude(tmp_path):
    """compile_trace makes the transform check COMPLETE, not a sample.

    The generator only ever calls the multiplier function at the declared
    endpoint longitudes (plus the half-open window boundaries), so probing
    those longitudes covers the real generation.  The 1-degree grid is a
    backstop for direct callers, and a leak confined to irrational endpoint
    longitudes would slip past it.
    """
    from CODE.leo_sim import config as config_mod
    lon_a, lon_b = 0.1234567890123, 10.9876543210987
    resolved = config_mod.resolve_config({
        "scenario": {"duration_s": 60.0, "num_satellites": 8, "num_planes": 1,
                     "seed": 7},
        "endpoints": {"sites": [{"name": "a", "lat": 0.0, "lon": lon_a},
                                {"name": "b", "lat": 0.0, "lon": lon_b}]},
        "demand": {"mode": "burst", "offered_mbps": 40.0, "burst_start_s": 10.0,
                   "burst_duration_s": 10.0, "burst_multiplier": 5.0},
        "control_plane": {"enabled": False}, "routing": {"policy": "hop"},
    })
    evidence: dict = {}
    trace_mod.compile_trace(resolved, str(tmp_path / "t"), evidence=evidence)
    transform = evidence["materialization"]["burst"]["transform"]
    assert transform["checked_longitudes"] == [lon_a, lon_b]
    # 6 boundary instants + a 0.05 s sweep of the 10 s window
    assert transform["checked_probes"] == 2 * (6 + 200)
    assert transform["longitude_source"].startswith(
        "the endpoint longitudes this trace uses")
    # the backstop grid alone would miss a leak confined to those longitudes
    assert lon_a not in trace_mod.BURST_LONGITUDE_PROBES


def test_the_transform_report_states_what_it_does_not_verify():
    """The R4 review falsified the old claim: the thinning clause is an
    algebraic identity, so patching only the generator's acceptance step (with
    a correct multiplier function) dropped the burst entirely and still gave
    mismatched_probes = 0 and problems = [].  The report must therefore say
    which half is observed and which is not.
    """
    from CODE.leo_sim import config as config_mod
    report = trace_mod.verify_burst_transform(
        config_mod.resolve_config({
            "scenario": {"duration_s": 60.0, "num_satellites": 8,
                         "num_planes": 1, "seed": 7},
            "endpoints": {"sites": [{"name": "a", "lat": 0.0, "lon": 0.0},
                                    {"name": "b", "lat": 0.0, "lon": 10.0}]},
            "demand": {"mode": "burst", "offered_mbps": 40.0,
                       "burst_start_s": 10.0, "burst_duration_s": 10.0,
                       "burst_multiplier": 5.0},
            "control_plane": {"enabled": False}, "routing": {"policy": "hop"},
        }), [0.0, 10.0])
    assert report["thinning_clause_is_algebraic_identity"] is True
    assert "multiplier function" in report["verified"]
    assert "acceptance step" in report["not_verified"]
    assert "never refuses a compile" in report["not_verified"]
    # the old field asserted more than the evidence
    assert "property" not in report


def test_the_window_is_swept_in_time_not_only_at_its_boundaries():
    """Complete in longitude is not complete in time.

    The cold-start review of 2026-09-25 (C7) falsified the "complete" claim
    with a transform that drops the burst only for 12.0 <= t < 13.0: every
    boundary probe agreed and the trace compiled.  The window is now swept on
    BURST_TIME_PROBE_STEP_S.  This is still sampling -- a hole narrower than
    the step is not detectable -- and the report says so.
    """
    resolved = _burst_cfg(burst_start_s=10.0, burst_duration_s=10.0,
                          burst_multiplier=5.0)
    report = trace_mod.verify_burst_transform(resolved, [0.0])
    swept = [p for p in report["probes"] if p["probe"].startswith("window_sweep")]
    assert len(swept) == int(round(10.0 / trace_mod.BURST_TIME_PROBE_STEP_S))
    assert min(p["t"] for p in swept) == 10.0
    assert max(p["t"] for p in swept) < 20.0, (
        "window_end is half-open and is probed separately with expected 1.0")

    original = trace_mod._rate_multiplier

    def with_hole(mode, t, longitude, dm):
        if 12.0 <= t < 13.0:
            return 1.0
        return (float(dm["burst_multiplier"]) if 10.0 <= t < 20.0 else 1.0)

    trace_mod._rate_multiplier = with_hole
    try:
        with pytest.raises(trace_mod.TraceError, match="window_sweep"):
            trace_mod.verify_burst_transform(resolved, [0.0])
    finally:
        trace_mod._rate_multiplier = original


def test_a_non_default_multiplier_without_a_window_is_refused():
    """The other half of the silent-drop hole (independent review, 2026-09-25).

    A config that sets burst_multiplier but declares no window under a mode
    that never applies the transform used to resolve happily with the value
    kept in the resolved config and never used.
    """
    from CODE.leo_sim import config as config_mod
    with pytest.raises(config_mod.ConfigError, match="never applies the burst"):
        config_mod.resolve_config({"demand": {"mode": "uniform",
                                              "burst_multiplier": 5.0}})
    # the default is present in every resolved config whether or not the author
    # asked for it, so it stays acceptable
    assert config_mod.resolve_config(
        {"demand": {"mode": "uniform"}})["config"]["demand"]["burst_multiplier"] == 2.0
    # and a mode that DOES apply the transform still takes a non-default value
    assert config_mod.resolve_config({
        "demand": {"mode": "burst", "burst_start_s": 1.0,
                   "burst_duration_s": 1.0,
                   "burst_multiplier": 4.0}})["config"]["demand"]["burst_multiplier"] == 4.0


def _burst_cfg(seed: int = 7, **demand) -> dict:
    base = {
        "scenario": {"name": "intensity", "duration_s": 60.0,
                     "time_step_s": 0.1, "num_satellites": 8,
                     "num_planes": 1, "seed": seed},
        "endpoints": {"sites": [
            {"name": "a", "lat": 0.0, "lon": 0.0},
            {"name": "b", "lat": 0.0, "lon": 10.0}]},
        "demand": {"mode": "burst", "offered_mbps": REFERENCE_MBPS,
                   "burst_start_s": 10.0,
                   "burst_duration_s": REFERENCE_WINDOW_S,
                   "burst_multiplier": 5.0},
        "control_plane": {"enabled": False},
        "routing": {"policy": "hop"},
        "execution": {"max_packets": 100000},
    }
    base["demand"].update(demand)
    return config_mod.resolve_config(base)


def _in_window(seed: int, **demand) -> tuple[int, str, dict]:
    """Compile one burst trace and return (in-window count, sha256, report)."""
    resolved = _burst_cfg(seed, **demand)
    out = _in_window.scratch / f"s{seed}-{len(_in_window.seen)}"
    out.mkdir(parents=True, exist_ok=True)
    trace_mod.compile_trace(resolved, str(out))
    rows = trace_mod.load_trace(str(out / "trace.csv"))
    report = trace_mod.materialization_report(resolved, rows)
    digest = hashlib.sha256((out / "trace.csv").read_bytes()).hexdigest()
    _in_window.seen.append(seed)
    return report["burst"]["packets_inside_window"], digest, report


_in_window.scratch = None
_in_window.seen = []


@pytest.fixture(autouse=True)
def _scratch(tmp_path):
    _in_window.scratch = tmp_path / "traces"
    _in_window.scratch.mkdir(parents=True, exist_ok=True)
    _in_window.seen = []
    yield


# ------------------------------------------------- deterministic transform
def test_the_declared_transform_holds_at_every_probed_boundary():
    report = trace_mod.verify_burst_transform(_burst_cfg())
    assert report["mismatched_probes"] == 0
    # 6 boundary instants x the declared longitude grid.  The grid is a
    # constant of the platform, not an adjective: the first version probed
    # three longitudes and an independent review falsified it with a transform
    # that leaked at every longitude except those three.
    assert len(trace_mod.BURST_LONGITUDE_PROBES) == 360
    assert trace_mod.BURST_LONGITUDE_PROBE_STEP_DEG == 1.0
    instant_count = report["checked_probes"] // len(trace_mod.BURST_LONGITUDE_PROBES)
    assert instant_count * len(trace_mod.BURST_LONGITUDE_PROBES) == report["checked_probes"]
    assert instant_count == 6 + int(round(10.0 / trace_mod.BURST_TIME_PROBE_STEP_S)), (
        "6 exact boundary instants plus the declared sweep of the window")
    assert report["max_multiplier"] == 5.0
    # the half-open window is what the generator implements: value at
    # window_end belongs to the base rate
    by_name = {(probe["probe"], probe["longitude_deg"]): probe
               for probe in report["probes"]}
    assert by_name[("window_start", 0.0)]["multiplier"] == 5.0
    assert by_name[("window_end_minus_eps", 0.0)]["multiplier"] == 5.0
    assert by_name[("window_end", 0.0)]["multiplier"] == 1.0
    assert by_name[("before_window", -180.0)]["multiplier"] == 1.0


@pytest.mark.parametrize("sabotage, description", [
    (lambda mode, t, lon, dm: 1.0, "never applied"),
    (lambda mode, t, lon, dm: float(dm["burst_multiplier"]), "leaks everywhere"),
    (lambda mode, t, lon, dm: (float(dm["burst_multiplier"]) if lon == 90.0
                               else 1.0), "leaks at exactly one probed point"),
    # The sabotage the three-longitude version let through: correct at 0/90/
    # -180, leaking at EVERY other longitude.
    (lambda mode, t, lon, dm: (
        float(dm["burst_multiplier"]) if lon not in (0.0, 90.0, -180.0)
        else (float(dm["burst_multiplier"]) if 10.0 <= t < 20.0 else 1.0)),
     "leaks at every longitude except the three the first version probed"),
])
def test_a_broken_transform_is_caught_deterministically(monkeypatch, sabotage,
                                                        description):
    monkeypatch.setattr(trace_mod, "_rate_multiplier", sabotage)
    # the message now names what was actually probed -- the multiplier
    # function -- instead of blaming "the generator" (R5 review, 2026-09-25)
    with pytest.raises(trace_mod.TraceError,
                       match="declared burst multiplier function is not applied"):
        trace_mod.verify_burst_transform(_burst_cfg())


# ------------------------------------------------- the reviewer's seed
def test_seed_222_compiles_and_keeps_its_sample():
    """The exact case that was refused: legitimate draw, far from the mean."""
    count, _digest, report = _in_window(222)
    burst = report["burst"]
    assert report["problems"] == []
    assert burst["transform"]["mismatched_probes"] == 0
    intensity = burst["intensity"]
    assert intensity["scenario_seed"] == 222
    assert intensity["observed_packets"] == count
    assert intensity["status"] == "INCOMPATIBLE"
    assert burst["intensity_compatible"] is False
    assert intensity["sigma_distance"] > trace_mod.INTENSITY_SIGMA
    assert "property of THIS SEED" in intensity["reason"]
    assert intensity["raw_sample_preserved"]["inside_window"] == count


#: PRE-DECLARED scan range.  It starts at 1 and covers the two outliers the
#: independent review of 2026-09-25 measured (222 and 227); a scan that stops
#: before them would "prove" nothing about the defect it exists for.
SCAN_SEEDS = 400


def test_no_seed_is_refused_across_a_wide_scan():
    """SCAN_SEEDS seeds, zero refusals -- a draw is never a compile error."""
    refused = []
    incompatible = []
    for seed in range(1, SCAN_SEEDS + 1):
        try:
            _count, _digest, report = _in_window(seed)
        except trace_mod.TraceError as exc:  # pragma: no cover - the defect
            refused.append((seed, str(exc)))
            continue
        if report["burst"]["intensity"]["status"] == "INCOMPATIBLE":
            incompatible.append(seed)
    assert refused == []
    assert {222, 227} <= set(incompatible), (
        "the scan must actually contain the seeds the review measured, "
        "otherwise it proves nothing")
    assert len(incompatible) < 10, incompatible


def test_one_seed_generates_once_and_is_never_reseeded():
    """Deterministic replay: same seed, same bytes, same verdict."""
    first_count, first_digest, first = _in_window(222)
    second_count, second_digest, second = _in_window(222)
    assert (first_count, first_digest) == (second_count, second_digest)
    assert first["burst"]["intensity"] == second["burst"]["intensity"]
    third_count, third_digest, _ = _in_window(223)
    assert (third_count, third_digest) != (first_count, first_digest)


# --------------------------------------------------- statistical design
@pytest.mark.parametrize("multiplier", [5.0, 1.2])
def test_the_realized_rate_is_calibrated_against_the_declaration(multiplier):
    """A design fixed in advance, on the MEAN of N draws.

    Per-run bands cannot separate 60 from 50 at multiplier 1.2; the mean of 40
    draws can, and the assertion is that the design HAS that power before it is
    used -- a calibration test that cannot fail is not evidence.
    """
    seeds = CALIBRATION_SEEDS[multiplier]
    counts = []
    for seed in range(1, seeds + 1):
        count, _digest, _report = _in_window(seed,
                                             burst_multiplier=multiplier)
        counts.append(count)
    mean = sum(counts) / len(counts)
    expected_burst = reference_expected(multiplier)
    expected_base = reference_expected(1.0)
    band = CALIBRATION_SIGMA * (expected_burst / seeds) ** 0.5
    gap = expected_burst - expected_base
    assert gap > REQUIRED_POWER_BANDS * band, (
        f"design cannot separate the declared rate {expected_burst} from the "
        f"no-burst rate {expected_base} at N={seeds}")
    assert abs(mean - expected_burst) <= band, (
        f"mean {mean} over {seeds} seeds is outside "
        f"{expected_burst} +/- {band}")
    if multiplier > 1.0:
        assert abs(mean - expected_base) > band


def reference_expected(multiplier: float) -> float:
    """total_rate * multiplier * window, from the fixture's own parameters."""
    total_rate = REFERENCE_MBPS * 1e6 / REFERENCE_BITS
    return total_rate * multiplier * REFERENCE_WINDOW_S
