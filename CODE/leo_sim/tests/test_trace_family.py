"""Task 6: deterministic strict nested load families.

An 80 Mbps master demand trace is generated once; each child
(10/20/40/80 Mbps) is a strict multiset subset of the master under the
canonical row contract (packet id excluded), all children share one family
identity, the companion artifact is exact-key, and an independent verifier
rejects any tampering.  The nested_filter stream is appended to the canonical
RNG stream tuple WITHOUT perturbing the seven existing streams.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from CODE.leo_sim import config, population, receipt, rng, trace
from CODE.leo_sim import trace_family

POPULATION_TIFF = (Path(__file__).resolve().parents[2] / "population_map"
                   / "gpw_v4_population_count_rev11_2020_15_min.tif")
POPULATION_PROFILE = (Path(__file__).resolve().parents[2]
                      / "leo_sim" / "profiles" / "population_gravity.yaml")


def _family_table():
    regions = (
        population.PopulationRegion("G5:18:36", 2.5, 2.5, 10.0),
        population.PopulationRegion("G5:18:37", 2.5, 7.5, 3.0),
        population.PopulationRegion("G5:18:38", 2.5, 12.5, 1.0),
        population.PopulationRegion("G5:19:36", -2.5, 2.5, 6.0),
    )
    return population.PopulationTable(
        regions=regions, source_path="/fake/pop.tif",
        source_sha256="e" * 64, source_shape=(720, 1440),
        source_resolution_deg=(0.25, 0.25), aggregation_deg=5.0,
        total_population=20.0)


def _family_cfg(monkeypatch, offered_mbps, master_mbps, **over):
    table = _family_table()
    monkeypatch.setattr(population, "load_population_regions",
                        lambda path, aggregation_deg: table)
    user = {
        "scenario": {"duration_s": 25.0, "seed": 7},
        "endpoints": {"aggregation_deg": 5.0},
        "demand": {
            "mode": "population_gravity",
            "population_path": "/fake/pop.tif",
            "offered_mbps": offered_mbps,
            "nested_master_offered_mbps": master_mbps,
            "packet_bits": 1_000_000,
            "emission_end_s": 20.0,
            "source_population_exponent": 1.0,
            "destination_population_exponent": 1.0,
            "gravity_alpha": 1.25,
            "gravity_d_floor_km": 100.0,
            "temporal_model": "local_diurnal_cosine",
            "utc_start_hour": 3.0,
            "diurnal_amplitude": 0.5,
        },
        "execution": {"max_packets": 20_000},
    }
    for group, fields in over.items():
        user.setdefault(group, {}).update(fields)
    return config.resolve_config(user)


def _load_rows(path):
    return trace.load_trace(
        str(path / "trace.csv"),
        horizon_s=25.0, max_packets=20_000)


def _load_companion(path):
    return json.loads((path / "nested-family.json").read_text(encoding="utf-8"))


def test_nested_children_are_strict_multiset_subsets_of_master(
        tmp_path, monkeypatch):
    """10 < 20 < 40 < 80 Mbps children from one 80 Mbps master must be
    strict multiset subsets (canonical rows without packet id)."""
    master_cfg = _family_cfg(monkeypatch, offered_mbps=80.0,
                             master_mbps=80.0)
    master_dir = tmp_path / "master"
    trace.compile_trace(master_cfg, str(master_dir))
    master_rows = _load_rows(master_dir)
    assert 500 < len(master_rows), "master must be non-degenerate"

    child_counts = []
    for offered in (10.0, 20.0, 40.0, 80.0):
        child_cfg = _family_cfg(monkeypatch, offered_mbps=offered,
                                master_mbps=80.0)
        child_dir = tmp_path / f"child-{int(offered)}"
        trace.compile_trace(child_cfg, str(child_dir))
        child_rows = _load_rows(child_dir)
        child_counts.append(len(child_rows))
        assert trace_family.is_multiset_subset(child_rows, master_rows), \
            f"{offered} Mbps child not subset of master"
        assert len(child_rows) <= len(master_rows)
    assert child_counts[0] <= child_counts[1] <= child_counts[2] <= \
        child_counts[3]
    # the 10 Mbps child is strictly smaller than the master
    assert child_counts[0] < len(master_rows)


def test_nested_family_shares_identity_distinct_traces(tmp_path,
                                                       monkeypatch):
    """All children share one family identity but have distinct trace
    identities and trace hashes."""
    identities = set()
    trace_shas = set()
    for offered in (10.0, 20.0, 40.0, 80.0):
        child_cfg = _family_cfg(monkeypatch, offered_mbps=offered,
                                master_mbps=80.0)
        child_dir = tmp_path / f"id-{int(offered)}"
        trace.compile_trace(child_cfg, str(child_dir))
        companion = _load_companion(child_dir)
        identities.add(companion["family_identity_sha256"])
        trace_shas.add(companion["trace_sha256"])
        assert companion["child_offered_mbps"] == offered
        assert companion["master_offered_mbps"] == 80.0
    assert len(identities) == 1
    assert len(trace_shas) == 4
    # every child's family identity equals the recomputed value
    cfg = _family_cfg(monkeypatch, offered_mbps=20.0, master_mbps=80.0)
    input_sha = "e" * 64
    assert identities.pop() == trace_family.family_identity_sha256(
        cfg, input_sha)


def test_family_identity_breaks_on_trace_determining_changes(monkeypatch):
    base = _family_cfg(monkeypatch, offered_mbps=20.0, master_mbps=80.0)
    ref = trace_family.family_identity_sha256(base, "e" * 64)
    changes = {
        "seed": {"scenario": {"seed": 43}},
        "utc block": {"demand": {"utc_start_hour": 9.0}},
        "population SHA": None,  # input hash handled separately below
        "packet size": {"demand": {"packet_bits": 2_000_000}},
        "emission window": {"demand": {"emission_end_s": 10.0}},
        "master load": {"demand": {"nested_master_offered_mbps": 160.0}},
        "sampler": {"demand": {"population_destination_sampler":
                               "alias_rejection"}},
    }
    for label, over in changes.items():
        if over is None:
            continue
        altered = _family_cfg(monkeypatch, offered_mbps=20.0,
                              master_mbps=80.0, **over)
        got = trace_family.family_identity_sha256(altered, "e" * 64)
        assert got != ref, label
    # the population asset hash is part of the family identity scope
    assert trace_family.family_identity_sha256(base, "e" * 64) != \
        trace_family.family_identity_sha256(base, "0" * 64)


def test_family_identity_invariant_to_non_trace_fields(monkeypatch):
    base = _family_cfg(monkeypatch, offered_mbps=20.0, master_mbps=80.0)
    ref = trace_family.family_identity_sha256(base, "e" * 64)
    invariants = {
        "routing": {"routing": {"policy": "delay"}},
        "access slots": {"access": {"slots_per_satellite": 1}},
        "ISL bandwidth": {"links": {"isl_rate_mbps": 5.0}},
        "learning": {"learning": {"lr": 0.5}},
        "geometry epoch": {"scenario": {"geometry_epoch_s": 3600.0}},
        "output path": {"outputs": {"out_dir": "elsewhere"}},
    }
    for label, over in invariants.items():
        altered = _family_cfg(monkeypatch, offered_mbps=20.0,
                              master_mbps=80.0, **over)
        got = trace_family.family_identity_sha256(altered, "e" * 64)
        assert got == ref, label


def test_family_verifier_rejects_tampered_child_rows(monkeypatch):
    master_cfg = _family_cfg(monkeypatch, offered_mbps=80.0,
                             master_mbps=80.0)
    child_cfg = _family_cfg(monkeypatch, offered_mbps=10.0,
                            master_mbps=80.0)
    master_rows = _generate_rows(master_cfg, 30.0)
    child_rows = _generate_rows(child_cfg, 30.0)
    assert trace_family.verify_family_child(child_rows, master_rows) == []
    # a row not in the parent
    foreign = dict(child_rows[0])
    foreign["packet_id"] = len(child_rows) + 1
    foreign["dst_grid_id"] = "G5:1:1"
    bad = child_rows + [foreign]
    assert any("not in the parent" in e
               for e in trace_family.verify_family_child(bad, master_rows))
    # a duplicate beyond parent multiplicity
    duped = child_rows + [dict(child_rows[0])]
    duped[-1]["packet_id"] = len(duped)
    assert any("multiplicity" in e
               for e in trace_family.verify_family_child(duped, master_rows))
    # non-contiguous / non-sequential ids
    shifted = [dict(r) for r in child_rows]
    shifted[1]["packet_id"] = shifted[1]["packet_id"] + 99
    assert any("packet id" in e.lower() or "contiguous" in e
               for e in trace_family.verify_family_child(shifted,
                                                         master_rows))


def _generate_rows(cfg, horizon):
    """Compile into a scratch dir and return the loaded rows (helper so the
    tamper test does not depend on the on-disk artifact guard)."""
    from pathlib import Path as _P
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        trace.compile_trace(cfg, td)
        return trace.load_trace(
            str(_P(td) / "trace.csv"), horizon_s=horizon,
            max_packets=20_000)


def test_family_verifier_rejects_tampered_companion(tmp_path, monkeypatch):
    child_cfg = _family_cfg(monkeypatch, offered_mbps=10.0,
                            master_mbps=80.0)
    child_dir = tmp_path / "child"
    trace.compile_trace(child_cfg, str(child_dir))
    companion = _load_companion(child_dir)
    child_sha = hashlib.sha256(
        (child_dir / "trace.csv").read_bytes()).hexdigest()
    child_packets = len(_load_rows(child_dir))
    master_packets = companion["master_candidate_packets"]
    errors = trace_family.verify_nested_family_companion(
        companion, child_cfg, "e" * 64, child_sha,
        child_packets=child_packets,
        master_candidate_packets=master_packets)
    assert errors == []
    for key in ("family_identity_sha256", "config_sha256",
                "trace_identity_sha256", "trace_sha256"):
        tampered = dict(companion)
        tampered[key] = "0" * 64
        errors = trace_family.verify_nested_family_companion(
            tampered, child_cfg, "e" * 64, child_sha,
            child_packets=child_packets,
            master_candidate_packets=master_packets)
        assert any(key in e for e in errors), key
    # wrong schema / extra key / wrong stream / wrong count
    bad_schema = dict(companion)
    bad_schema["schema"] = "leo-sim-nested-trace-family/v999"
    assert any("schema" in e for e in trace_family.verify_nested_family_companion(
        bad_schema, child_cfg, "e" * 64, child_sha,
        child_packets=child_packets,
        master_candidate_packets=master_packets))
    extra = dict(companion)
    extra["surprise"] = 1
    assert any("keys mismatch" in e or "unknown" in e
               for e in trace_family.verify_nested_family_companion(
                   extra, child_cfg, "e" * 64, child_sha,
                   child_packets=child_packets,
                   master_candidate_packets=master_packets))
    bad_stream = dict(companion)
    bad_stream["filter_rng_stream"] = "SeedSequence(7).spawn[1]"
    assert any("filter_rng_stream" in e
               for e in trace_family.verify_nested_family_companion(
                   bad_stream, child_cfg, "e" * 64, child_sha,
                   child_packets=child_packets,
                   master_candidate_packets=master_packets))
    bad_count = dict(companion)
    bad_count["child_packets"] = bad_count["child_packets"] + 1
    assert any("child_packets" in e
               for e in trace_family.verify_nested_family_companion(
                   bad_count, child_cfg, "e" * 64, child_sha,
                   child_packets=child_packets,
                   master_candidate_packets=master_packets))
    bad_master = dict(companion)
    bad_master["master_candidate_packets"] =         bad_master["master_candidate_packets"] - 1
    assert any("master_candidate_packets" in e
               for e in trace_family.verify_nested_family_companion(
                   bad_master, child_cfg, "e" * 64, child_sha,
                   child_packets=child_packets,
                   master_candidate_packets=master_packets))


def test_master_candidate_cap_fails_before_artifacts_accepted(
        tmp_path, monkeypatch):
    """execution.max_packets applies to the MASTER candidate count, not only
    the child count: an overloaded master must fail before any trace
    artifact is written into the output directory."""
    table = _family_table()
    monkeypatch.setattr(population, "load_population_regions",
                        lambda path, aggregation_deg: table)
    user = {
        "scenario": {"duration_s": 25.0, "seed": 7},
        "endpoints": {"aggregation_deg": 5.0},
        "demand": {
            "mode": "population_gravity",
            "population_path": "/fake/pop.tif",
            "offered_mbps": 0.5,
            "nested_master_offered_mbps": 80.0,
            "packet_bits": 1_000_000,
            "emission_end_s": 20.0,
        },
        "execution": {"max_packets": 100},  # master candidates exceed this
    }
    resolved = config.resolve_config(user)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(trace.TraceError, match="max_packets"):
        trace.compile_trace(resolved, str(out))
    assert not (out / "trace.csv").exists()
    assert not (out / "manifest.json").exists()
    assert not (out / "nested-family.json").exists()


def test_nested_filter_stream_does_not_perturb_existing_streams():
    """Appending nested_filter leaves the generated values and mapping
    indices of all seven existing streams unchanged; the filter differs from
    every existing stream and is recorded as child 7."""
    assert rng.STREAM_NAMES[-1] == "nested_filter"
    assert len(rng.STREAM_NAMES) == 8
    mapping = rng.stream_mapping(42)
    assert mapping["demand"] == "SeedSequence(42).spawn[0]"
    assert mapping["ge_gsl"] == "SeedSequence(42).spawn[1]"
    assert mapping["ge_isl"] == "SeedSequence(42).spawn[2]"
    assert mapping["association"] == "SeedSequence(42).spawn[3]"
    assert mapping["routing"] == "SeedSequence(42).spawn[4]"
    assert mapping["control"] == "SeedSequence(42).spawn[5]"
    assert mapping["monitor"] == "SeedSequence(42).spawn[6]"
    assert mapping["nested_filter"] == "SeedSequence(42).spawn[7]"
    # permanent regression: appending a stream must not change the seven
    # existing children's states (numeric identity of each stream)
    generators = rng.streams(42)
    # child 0 of an 8-spawn equals child 0 of a 1-spawn (numpy property)
    reference = np.random.default_rng(np.random.SeedSequence(42).spawn(1)[0])
    assert (generators["demand"].random(256) ==
            reference.random(256)).all()
    # the filter differs from every pre-existing stream
    filter_draws = generators["nested_filter"].random(64)
    for name in rng.STREAM_NAMES[:-1]:
        other = generators[name].random(64)
        assert not (other == filter_draws).all(), name