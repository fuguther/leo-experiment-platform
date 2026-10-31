"""Mock-only tests for the bounded calibration entry point."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from CODE.experiment_platform import load_calibration as lc
from CODE.leo_sim import config as config_mod


REPO_ROOT = Path(lc.__file__).resolve().parents[2]
PROFILE = lc.BASE_PROFILE


def _contract(*, overrides=None, arms=None):
    return {
        "schema": lc.CONTRACT_SCHEMA,
        "base_profile": PROFILE,
        "deadline_s": 4.0,
        "max_packets": 20000,
        "max_duration_s": 12.0,
        "cells": [{"id": "one", "arms": arms or ["stale"],
                   "overrides": overrides or {}}],
    }


def _write_contract(path, document):
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _fake_trace():
    return [
        {"packet_id": 1, "emit_time_s": 0.0, "src_grid_id": "a",
         "dst_grid_id": "b", "bits": 100, "deadline_at_s": None},
        {"packet_id": 2, "emit_time_s": 0.5, "src_grid_id": "a",
         "dst_grid_id": "c", "bits": 100, "deadline_at_s": None},
    ]


def _decision(pid, works):
    scores = {}
    for direction, work in zip(("N", "E"), works):
        scores[direction] = {
            "total_s": 1.0,
            "terms": {"resource_work_s": work},
            "missing": [],
        }
    return {
        "t": 1.0,
        "t_decision_start": 0.9,
        "decision_id": pid,
        "pid": pid,
        "sat": 4,
        "kind": "forward",
        "candidates": ["N", "E"],
        "chosen": "N",
        "observation_at_start": {
            "time_alignment": {"scores": scores, "eta_terms": {"N": {}},
                                "prediction": {"preserve": True}},
            "legal_directions": ["N", "E"],
            "four_direction_audit": {"N": {"legal": True}},
            "neighbours": {"large": "must not be retained"},
        },
        "truth_at_commit": {"t_observed": 1.0},
        "estimate_at_start": {"direction": "N"},
        "advertised_history": ["large"],
    }


def _fake_result():
    return {
        "natural_end": True,
        "interrupted": False,
        "error": None,
        "events_processed": 10,
        "horizon_s": 8.0,
        "stop_time_s": 4.5,
        "packet_events": [
            {"kind": "packet_emitted", "pid": 1, "at": 0.0, "bits": 100},
            {"kind": "packet_emitted", "pid": 2, "at": 0.5, "bits": 100},
        ],
        "deliveries": {1: {"delivered_at": 3.9, "path": [1, 2]}},
        "fates": {1: "DELIVERED", 2: "IN_SYSTEM_AT_STOP"},
        "queue_area_bits_s": {"isl_data": 3.0},
        "access": {"admitted": 2},
        "mechanisms": {"effective": {"time_alignment": True}},
        "service_log": [{"kind": "service", "at": 1.0}],
        "topology_trace": [{"at": 0.0, "edges": []}],
    }


def _free_disk(monkeypatch, free=6 * 1024**3):
    monkeypatch.setattr(lc.shutil, "disk_usage",
                        lambda _path: SimpleNamespace(free=free))


def test_preflight_parses_config_and_boundaries_without_trace_or_kernel(
        tmp_path, monkeypatch):
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    out_dir = tmp_path / "new-run"
    _free_disk(monkeypatch)
    monkeypatch.setattr(lc.t1_tasks, "design",
                        lambda **_kwargs: pytest.fail("preflight compiled trace"))
    monkeypatch.setattr(lc.kernel, "run_simulation",
                        lambda *_args, **_kwargs: pytest.fail("preflight called kernel"))

    result = lc.run_calibration(contract_path, "one", out_dir,
                                do_preflight=True, repo_root=REPO_ROOT)

    assert result["diagnostic"] == "NOT_FULL_REPLAY_DIAGNOSTIC"
    assert result["formal"] is False and result["full_replay"] is False
    assert result["trace_compiled"] is False and result["kernel_calls"] == 0
    assert not out_dir.exists()


@pytest.mark.parametrize("declared", [12.0, 28.0, 60.0, 300.0])
def test_contract_accepts_a_declared_horizon_inside_the_envelope(
        tmp_path, monkeypatch, declared):
    """A warmed time-alignment window needs more than the frozen 12 s."""
    document = _contract()
    document["max_duration_s"] = declared
    contract_path = _write_contract(tmp_path / "contract.json", document)
    _free_disk(monkeypatch)

    lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


@pytest.mark.parametrize("declared", [11.9, 0, -1, 300.1, True, "12"])
def test_contract_rejects_a_horizon_outside_the_envelope(
        tmp_path, monkeypatch, declared):
    document = _contract()
    document["max_duration_s"] = declared
    contract_path = _write_contract(tmp_path / "contract.json", document)
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="max_duration_s"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


@pytest.mark.parametrize("declared", [1, 20000, 100000])
def test_contract_accepts_packet_caps_inside_the_envelope(
        tmp_path, monkeypatch, declared):
    document = _contract(overrides={"execution": {"max_packets": declared}})
    document["max_packets"] = declared
    contract_path = _write_contract(tmp_path / "contract.json", document)
    _free_disk(monkeypatch)

    result = lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)

    assert result["max_packets"] == declared


@pytest.mark.parametrize("declared", [0, -1, 100001, True, 1.0, "1"])
def test_contract_rejects_a_packet_cap_outside_the_integer_envelope(
        tmp_path, monkeypatch, declared):
    document = _contract()
    document["max_packets"] = declared
    contract_path = _write_contract(tmp_path / "contract.json", document)
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="max_packets"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_default_contract_keeps_the_20000_packet_cap():
    assert _contract()["max_packets"] == 20000


@pytest.mark.parametrize("declared", [28.0, 300.0])
def test_a_longer_declared_horizon_reaches_the_resolved_config(
        tmp_path, monkeypatch, declared):
    document = _contract(overrides={"scenario": {"duration_s": declared}})
    document["max_duration_s"] = declared
    contract_path = _write_contract(tmp_path / "contract.json", document)
    _free_disk(monkeypatch)

    lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)
    _raw, _doc, _base, cells = lc._read_contract(contract_path, REPO_ROOT)

    assert cells["one"]["resolved"]["config"]["scenario"]["duration_s"] == declared


@pytest.mark.parametrize("overrides", [
    {"scenario": {"duration_s": 12.01}},
    {"execution": {"max_packets": 20001}},
])
def test_contract_overrides_cannot_exceed_declared_bounds(tmp_path, monkeypatch,
                                                           overrides):
    contract_path = _write_contract(tmp_path / "contract.json",
                                    _contract(overrides=overrides))
    _free_disk(monkeypatch)
    with pytest.raises(lc.CalibrationError, match="exceeds contract cap"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_execution_packet_cap_cannot_exceed_the_declared_100000_cap(
        tmp_path, monkeypatch):
    document = _contract(overrides={"execution": {"max_packets": 100001}})
    document["max_packets"] = 100000
    contract_path = _write_contract(tmp_path / "contract.json", document)
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="max_packets exceeds contract cap"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_effective_duration_cannot_exceed_the_declared_300_second_cap(
        tmp_path, monkeypatch):
    document = _contract(overrides={"scenario": {"duration_s": 300.1}})
    document["max_duration_s"] = 300.0
    contract_path = _write_contract(tmp_path / "contract.json", document)
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="duration_s exceeds contract cap"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_compiled_trace_above_declared_packet_cap_fails_without_truncation(
        tmp_path, monkeypatch):
    document = _contract(overrides={"execution": {"max_packets": 2}})
    document["max_packets"] = 2
    contract_path = _write_contract(tmp_path / "contract.json", document)
    out_dir = tmp_path / "run"
    _free_disk(monkeypatch)
    base = config_mod.load_config_file(str(REPO_ROOT / PROFILE))
    rows = _fake_trace() + [copy.deepcopy(_fake_trace()[0])]
    kernel_calls = []

    def fake_design(*, config_path, overrides):
        cfg = copy.deepcopy(base["config"])
        lc._deep_merge(cfg, overrides)
        return config_mod.resolve_config(cfg), rows, object(), {
            "trace_sha256": "a" * 64,
            "rows_digest": "b" * 64,
            "rows": len(rows),
            "native_population": {"population_sha256": "c" * 64},
        }

    monkeypatch.setattr(lc.t1_tasks, "design", fake_design)
    monkeypatch.setattr(lc.kernel, "run_simulation",
                        lambda *_args, **_kwargs: kernel_calls.append(True))

    summary = lc.run_calibration(contract_path, "one", out_dir,
                                 repo_root=REPO_ROOT)

    assert summary["status"] == "FAILED_CLOSED"
    assert ("compiled trace has 3 packets above contract max_packets"
            in summary["error"]["message"])
    assert summary["trace"] is None
    assert not (out_dir / "trace.jsonl.gz").exists()
    assert kernel_calls == []


def test_contract_rejects_arbitrary_overrides(tmp_path, monkeypatch):
    contract_path = _write_contract(
        tmp_path / "contract.json",
        _contract(overrides={"topology": {"matching": "markovian"}}))
    _free_disk(monkeypatch)
    with pytest.raises(lc.CalibrationError, match="unsupported override group"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_output_exists_is_refused_without_modifying_it(tmp_path, monkeypatch):
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    out_dir = tmp_path / "already-there"
    out_dir.mkdir()
    marker = out_dir / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="already exists"):
        lc.preflight(contract_path, "one", out_dir, REPO_ROOT)
    assert marker.read_text(encoding="utf-8") == "preserve"


def test_routing_policy_override_is_accepted_and_applied(tmp_path, monkeypatch):
    contract_path = _write_contract(
        tmp_path / "contract.json",
        _contract(overrides={"routing": {"policy": "oracle"}}))
    _free_disk(monkeypatch)

    result = lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)

    assert result["cell"] == "one"
    raw, document, _base, cells = lc._read_contract(contract_path, REPO_ROOT)
    assert cells["one"]["resolved"]["config"]["routing"]["policy"] == "oracle"


def test_contract_accepts_an_allowlisted_profile_and_rejects_others(
        tmp_path, monkeypatch):
    # The corridor profile is a 600 s / 200000 packet scenario; a corridor cell
    # must bring both back inside the contract's bounded envelope.
    corridor = dict(_contract(overrides={
        "scenario": {"duration_s": 12},
        "execution": {"max_packets": 20000}}),
        base_profile=lc.ALLOWED_PROFILES[1])
    path = _write_contract(tmp_path / "corridor.json", corridor)
    _free_disk(monkeypatch)

    result = lc.preflight(path, "one", tmp_path / "new-run", REPO_ROOT)

    assert result["cell"] == "one"

    bogus = dict(_contract(), base_profile="CODE/leo_sim/profiles/nope.yaml")
    path2 = _write_contract(tmp_path / "bogus.json", bogus)
    with pytest.raises(lc.CalibrationError, match="base_profile must be one of"):
        lc.preflight(path2, "one", tmp_path / "new-run-2", REPO_ROOT)


def test_the_hotspot_profile_is_allowlisted_and_actually_concentrates(
        tmp_path, monkeypatch):
    """The concentration profile must differ where it claims to, and nowhere else.

    The uniform-gravity smoke profile leaves the queue a decision prices empty
    in ~85% of candidate slots, so a state-time comparison has nothing to act
    on there.  This checks the replacement really moves the two gravity fields
    that concentrate destinations, and that nothing else that matters moved.
    """
    from CODE.leo_sim import config as config_mod

    smoke = config_mod.load_config_file(str(REPO_ROOT / PROFILE))["config"]
    hotspot = config_mod.load_config_file(
        str(REPO_ROOT / "CODE/leo_sim/profiles/t1_hotspot_gravity.yaml"))["config"]
    s, h = smoke["demand"], hotspot["demand"]

    assert h["destination_population_exponent"] > s["destination_population_exponent"]
    assert h["gravity_alpha"] < s["gravity_alpha"]
    for key in ("mode", "source_population_exponent", "packet_bits",
                "population_destination_sampler", "gravity_d_floor_km"):
        assert h[key] == s[key], key
    for key in ("num_satellites", "num_planes", "altitude_km", "inclination_deg"):
        assert hotspot["scenario"][key] == smoke["scenario"][key], key

    contract = dict(_contract(), base_profile="CODE/leo_sim/profiles/t1_hotspot_gravity.yaml")
    path = _write_contract(tmp_path / "hotspot.json", contract)
    _free_disk(monkeypatch)
    assert lc.preflight(path, "one", tmp_path / "new-run", REPO_ROOT)["cell"] == "one"


def test_routing_policy_override_rejects_unknown_policy(tmp_path, monkeypatch):
    contract_path = _write_contract(
        tmp_path / "contract.json",
        _contract(overrides={"routing": {"policy": "shortest"}}))
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="routing.policy must be one of"):
        lc.preflight(contract_path, "one", tmp_path / "new-run", REPO_ROOT)


def test_missing_output_ancestors_are_created_inside_the_run_dir(
        tmp_path, monkeypatch):
    """The runner hands us a fresh run directory; inner paths do not exist."""
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    out_dir = tmp_path / "run" / "cal" / "one"
    _free_disk(monkeypatch)

    result = lc.preflight(contract_path, "one", out_dir, REPO_ROOT)

    assert out_dir.parent.is_dir() and not out_dir.exists()
    assert result["cell"] == "one"


def test_symlinked_output_ancestor_is_refused(tmp_path, monkeypatch):
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    real = tmp_path / "outside"
    real.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(real, target_is_directory=True)
    _free_disk(monkeypatch)

    with pytest.raises(lc.CalibrationError, match="real directory|symlinked"):
        lc.preflight(contract_path, "one", link / "cal" / "one", REPO_ROOT)


def test_decision_projection_is_lossless_for_declared_fields_and_no_mutation(
        tmp_path, monkeypatch):
    row = _decision(8, [0.0, 0.0])
    original = copy.deepcopy(row)
    budget = lc._OutputBudget()
    sink = lc._JsonlSink(tmp_path / "decisions.jsonl.gz", budget, "decision")
    sink.append(row)
    sink.close()

    assert row == original
    with gzip.open(tmp_path / "decisions.jsonl.gz", "rt", encoding="utf-8") as stream:
        saved = json.loads(stream.readline())
    assert saved["observation_at_start"]["time_alignment"]["prediction"] == {
        "preserve": True}
    assert saved["observation_at_start"]["legal_directions"] == ["N", "E"]
    assert saved["observation_at_start"]["four_direction_audit"] == {
        "N": {"legal": True}}
    assert "neighbours" not in saved["observation_at_start"]
    assert "advertised_history" not in saved


def test_decision_projection_keeps_the_cross_arm_hop_key(tmp_path):
    """(pid, hop_index) is the only stable cross-arm decision key.

    decision_id is a per-run counter that denotes different decisions in each
    arm, so an analysis that pairs arms by decision_id silently compares
    different packets.  Dropping hop_index from the projected row would make
    the correct key unavailable from the artifact.
    """
    row = _decision(8, [0.0, 0.0])
    row["hop_index"] = 3
    budget = lc._OutputBudget()
    sink = lc._JsonlSink(tmp_path / "decisions.jsonl.gz", budget, "decision")
    sink.append(row)
    sink.close()
    with gzip.open(tmp_path / "decisions.jsonl.gz", "rt", encoding="utf-8") as stream:
        saved = json.loads(stream.readline())
    assert saved["hop_index"] == 3
    assert saved["pid"] == 8


PILOT_PROFILE = "CODE/leo_sim/profiles/ta_pilot_140_adv1.yaml"


def test_state_age_pilot_profile_is_declared_and_frozen():
    """The pilot's declared design is a frozen input, not a knob a cell edits.

    This pins the values that make the mechanism question meaningful: same
    140-sat Stage C geometry, speed-forced link/packet scale, a 1 s
    advertisement period, bounded_linear with history_limit 4, and the
    candidate arm querying the ARRIVAL instant (the target-probe truth
    instant) rather than the legacy arrival_plus_queue one.
    """
    assert PILOT_PROFILE in lc.ALLOWED_PROFILES
    resolved = config_mod.load_config_file(str(REPO_ROOT / PILOT_PROFILE))
    cfg = resolved["config"]
    assert cfg["scenario"]["num_satellites"] == 140
    assert cfg["scenario"]["num_planes"] == 7
    assert cfg["scenario"]["altitude_km"] == 600.0
    assert cfg["scenario"]["inclination_deg"] == 98.6
    assert cfg["scenario"]["duration_s"] == 20.0
    assert cfg["links"]["isl_rate_mbps"] == 100.0
    assert cfg["links"]["isl_queue_bits"] == 64000000
    assert cfg["links"]["ge_enabled"] is False
    assert cfg["demand"]["packet_bits"] == 1000000
    assert cfg["control_plane"]["advertise_interval_s"] == 1.0
    assert cfg["control_plane"]["advertisement_protocol_version"] == 2
    assert cfg["time_alignment"]["enabled"] is True
    assert cfg["time_alignment"]["predictor"] == "bounded_linear"
    assert cfg["time_alignment"]["history_limit"] == 4
    assert cfg["time_alignment"]["candidate_target_rule"] == "arrival"
    assert cfg["execution"]["compute_servers_per_satellite"] <= 1


def test_sink_stats_count_zero_and_nonzero_work_and_complete_candidates(tmp_path):
    budget = lc._OutputBudget()
    sink = lc._JsonlSink(tmp_path / "decisions.jsonl.gz", budget, "decision")
    sink.append(_decision(8, [0.0, 0.0]))
    sink.append(_decision(9, [0.25, 0.5]))
    sink.close()
    stats = sink.summary()

    assert stats["rows"] == 2
    assert stats["forward_pids"] == [8, 9]
    assert stats["forward_pids_count"] == 2
    assert stats["nonzero_resource_work_candidate_count"] == 2
    assert stats["decisions_with_at_least_two_complete_candidates"] == 2


def test_d4_requires_complete_emission_set_and_matured_windows():
    rows = _fake_trace()
    result = _fake_result()
    complete = lc.d4_outcome(result, rows, 4.0)
    assert complete["status"] == "COMPUTED"
    assert complete["offered_packets"] == 2
    assert complete["deadline_not_delivered_count"] == 1
    assert complete["deadline_not_delivered_proportion"] == 0.5
    assert complete["capped_loss"] == pytest.approx((3.9 / 4.0 + 1.0) / 2)

    incomplete_emission = copy.deepcopy(result)
    incomplete_emission["packet_events"].pop()
    outcome = lc.d4_outcome(incomplete_emission, rows, 4.0)
    assert outcome["status"] == "NOT_COMPARABLE"
    assert outcome["reason"] == "incomplete_packet_emission_set"
    assert outcome["deadline_not_delivered_proportion"] is None

    short_stop = copy.deepcopy(result)
    short_stop["stop_time_s"] = 4.49
    outcome = lc.d4_outcome(short_stop, rows, 4.0)
    assert outcome["status"] == "NOT_COMPARABLE"
    assert outcome["reason"] == "stop_before_all_D4_windows_matured"


def test_one_arm_calls_mock_kernel_once_and_publishes_full_result(tmp_path,
                                                                  monkeypatch):
    contract_path = _write_contract(tmp_path / "contract.json", _contract())
    out_dir = tmp_path / "run"
    _free_disk(monkeypatch)
    base = config_mod.load_config_file(str(REPO_ROOT / PROFILE))
    rows = _fake_trace()
    design_calls = []
    kernel_calls = []

    def fake_design(*, config_path, overrides):
        design_calls.append((config_path, copy.deepcopy(overrides)))
        cfg = copy.deepcopy(base["config"])
        lc._deep_merge(cfg, overrides)
        return config_mod.resolve_config(cfg), rows, object(), {
            "trace_sha256": "a" * 64,
            "rows_digest": "b" * 64,
            "rows": len(rows),
            "native_population": {"population_sha256": "c" * 64},
        }

    def fake_kernel(resolved, passed_rows, *, geometry, decision_sink,
                    timeline_sink, control_audit):
        kernel_calls.append((resolved["config"]["time_alignment"]["arm"],
                             passed_rows, control_audit))
        decision_sink.append(_decision(1, [0.25, 0.0]))
        timeline_sink.append({"milestone": "decision", "at": 1.0,
                              "payload": "full"})
        return _fake_result()

    monkeypatch.setattr(lc.t1_tasks, "design", fake_design)
    monkeypatch.setattr(lc.kernel, "run_simulation", fake_kernel)

    summary = lc.run_calibration(contract_path, "one", out_dir,
                                 repo_root=REPO_ROOT)

    assert len(design_calls) == 1
    assert len(kernel_calls) == 1
    assert kernel_calls[0][0] == "stale"
    assert kernel_calls[0][1] is rows
    assert kernel_calls[0][2] is True
    assert summary["status"] == "DIAGNOSTIC_COMPLETE"
    assert summary["kernel_call_count"] == 1
    assert summary["full_replay"] is False
    assert summary["arms"][0]["config_sha256"]
    result_path = out_dir / "arms/stale/result.json.gz"
    with gzip.open(result_path, "rt", encoding="utf-8") as stream:
        saved = json.load(stream)
    assert saved["service_log"] == [{"kind": "service", "at": 1.0}]
    assert saved["topology_trace"] == [{"at": 0.0, "edges": []}]
    assert json.loads((out_dir / "summary.json").read_text(encoding="utf-8")) == summary
    assert all("sha256" in artifact for artifact in summary["artifact_files"])
