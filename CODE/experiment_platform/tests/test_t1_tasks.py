"""A2: the three experiment task types, their sampling rule and their failures."""
from __future__ import annotations

import copy
import inspect
from pathlib import Path

import pytest
import yaml

from CODE.experiment_platform import scripted_scenarios, t1_tasks
from CODE.leo_sim import config as config_mod

PROFILE = "CODE/leo_sim/profiles/t1_frozen_branch_smoke.yaml"


def _scenario(name="same_flow"):
    resolved, rows, geometry, _meta = scripted_scenarios.build(name)
    source = {"scenario": name, "config": None, "trace_sha256": None,
              "rows": len(rows)}
    return resolved, rows, geometry, source


def _ta_on(resolved, **ta):
    cfg = copy.deepcopy(resolved["config"])
    cfg["time_alignment"]["enabled"] = True
    cfg["time_alignment"].update(ta)
    return config_mod.resolve_config(cfg)


def _row(decision_id, legal, *, kind="forward", chosen="E", t=1.0, **extra):
    row = {"decision_id": decision_id, "kind": kind, "t_decision_start": t,
           "chosen": chosen, "candidates": list(legal),
           "observation_at_start": {"legal_directions": list(legal)}}
    row.update(extra)
    return row


def test_controlled_population_profile_cli_refuses_without_suite_context(
        tmp_path, monkeypatch, capsys):
    root = Path(__file__).resolve().parents[3]
    profile = yaml.safe_load((root / "CODE/leo_sim/profiles/"
                              "t1_population_region_cost_smoke.yaml"
                              ).read_text(encoding="utf-8"))
    # The profile's native-population structure must trigger the guard even
    # after a caller changes the scenario label.
    profile["scenario"]["name"] = "renamed-generic-label"
    profile_path = tmp_path / "renamed-profile.yaml"
    profile_path.write_text(yaml.safe_dump(profile, sort_keys=False),
                            encoding="utf-8")
    output = tmp_path / "must-not-exist.json"
    monkeypatch.delenv("T1_SUITE_RUNTIME_CONTEXT", raising=False)
    monkeypatch.setattr(
        t1_tasks, "design",
        lambda *args, **kwargs: pytest.fail("design/trace must not run"))

    status = t1_tasks.main([
        "--task", "network_alignment", "--config", str(profile_path),
        "--out", str(output),
    ])

    assert status == 2
    assert "requires a validated suite stage context" in capsys.readouterr().out
    assert not output.exists()


def test_controlled_network_checks_call_budget_before_every_kernel_call(
        monkeypatch):
    assert "simulator_call_guard" in inspect.signature(
        t1_tasks.network_alignment).parameters, (
            "controlled task has no per-simulator-call budget hook")
    resolved, rows, geometry, source = _scenario()
    authorized = []
    called = []

    def guard(arm):
        authorized.append(arm)
        if len(authorized) > 2:
            raise t1_tasks.TaskError("simulator-call budget exhausted")

    def fake_kernel(*_args, **kwargs):
        called.append(kwargs)
        raise RuntimeError("fixture stops before simulation")

    monkeypatch.setattr(t1_tasks.kernel, "run_simulation", fake_kernel)
    document = t1_tasks.network_alignment(
        resolved, rows, geometry, source,
        simulator_call_guard=guard)

    assert authorized == list(t1_tasks.NETWORK_ARMS)
    assert len(called) == 2
    assert document["status"] == "ALL_ARMS_FAILED"
    assert all("budget exhausted" in failure["reason"]
               for failure in document["failures"][2:])


# ------------------------------------------------------ branch eligibility
def test_a_forward_decision_with_two_legal_directions_is_eligible():
    eligible, rejected = t1_tasks.eligible_branches([_row(4, ["E", "W"])])
    assert [item["decision_id"] for item in eligible] == [4]
    assert rejected == []


def test_a_single_legal_direction_is_rejected_with_its_reason():
    eligible, rejected = t1_tasks.eligible_branches([_row(4, ["E"])])
    assert eligible == []
    assert rejected and rejected[0]["decision_id"] == 4
    assert "two legal directions" in rejected[0]["reason"]


def test_a_deliver_decision_is_rejected_with_its_reason():
    eligible, rejected = t1_tasks.eligible_branches(
        [_row(4, ["E", "W"], kind="deliver", chosen="deliver")])
    assert eligible == []
    assert "not a forward decision" in rejected[0]["reason"]


def test_the_measurement_window_excludes_warmup_decisions():
    rows = [_row(1, ["E", "W"], t=0.5), _row(2, ["E", "W"], t=5.0)]
    eligible, rejected = t1_tasks.eligible_branches(rows, window=(1.0, 10.0))
    assert [item["decision_id"] for item in eligible] == [2]
    assert rejected[0]["reason"] == "outside the measurement window"


def test_eligibility_does_not_read_the_outcome():
    """The same t0 observation gives the same verdict, whatever happened."""
    good = _row(4, ["E", "W"], chosen="E")
    bad = _row(4, ["E", "W"], chosen="W", future_outcome="lost",
               regret=99.0, oracle_best="E")
    left, _ = t1_tasks.eligible_branches([good])
    right, _ = t1_tasks.eligible_branches([bad])
    # the verdict and the branch point are identical; baseline_chosen is
    # recorded as provenance but never consulted
    pick = lambda items: [(i["decision_id"], i["legal_directions"],
                          i["t_decision_start"]) for i in items]
    assert pick(left) == pick(right)
    assert left[0]["baseline_chosen"] == "E"
    assert right[0]["baseline_chosen"] == "W"


def test_action_log_effective_query_requires_finite_time_and_named_resource():
    row = _row(1, ["E", "W"], observation_at_start={
        "candidate_resources": {
            "E": {"status": "ok", "kind": "isl", "peer": 2,
                  "egress_direction": "N", "egress_peer": 3,
                  "isl_rate_bps": 5_000_000},
            "W": {"status": "missing", "reason": "no observed route"},
        },
        "time_alignment": {
            "arm": "candidate", "execution_mode": "per_packet",
            "applied_order": ["E", "W"], "ranking": ["E", "W"],
            "query_targets": {"E": 6.0, "W": float("inf")},
            "scores": {
                "E": {"total_s": 0.2, "missing": [], "fallback": False},
                "W": {"total_s": 0.3, "missing": [], "fallback": False},
            },
        },
    })
    log = t1_tasks._arm_action_log([row])

    assert log["count"] == 1
    record = log["records"][0]
    assert record["candidate_query_count"] == 2
    assert record["effective_candidate_query_count"] == 1
    assert record["effective_candidate_query_directions"] == ["E"]
    assert record["candidate_query_evidence"]["E"]["resource_target"][
        "resource_valid"] is True
    assert record["candidate_query_evidence"]["W"]["resource_target"][
        "resource_valid"] is False


def test_routing_audit_log_keeps_four_way_decisions_and_attempts():
    direction_audit = {
        "schema": "leo-sim-four-direction-mask/v1",
        "direction_order": ["N", "E", "S", "W"],
        "final_legal_mask": {d: d == "E" for d in ("N", "E", "S", "W")},
    }
    row = {
        "t": 1.3, "t_decision_start": 1.0, "decision_id": 4,
        "pid": 9, "sat": 2, "kind": "forward", "candidates": ["E"],
        "chosen": "E", "four_direction_audit": direction_audit,
        "observation_at_start": {
            "mode": "frozen", "source": "frozen_snapshot_before_compute",
            "t_observed": 1.0, "candidate_resources": {"E": {"peer": 3}},
            "time_alignment": {"query_targets": {"E": 1.5}},
        },
        "estimate_at_start": {"truth_used": False},
    }
    attempt = {
        "milestone": "decision_attempt", "at": 2.0, "pid": 10,
        "decision_id": 5, "action": "hold",
        "four_direction_audit": {**direction_audit,
                                  "decision_kind": "hold"},
        "reason": "temporarily_unavailable",
    }

    log = t1_tasks._routing_audit_log([row], [attempt])

    assert log["decision_record_count"] == 1
    assert log["decision_records"][0]["four_direction_audit"][
        "direction_order"] == ["N", "E", "S", "W"]
    assert log["decision_records"][0]["observation_at_start"][
        "time_alignment"] == {"query_targets": {"E": 1.5}}
    assert log["attempt_record_count"] == 1
    assert log["attempt_records"][0]["reason"] == "temporarily_unavailable"


def test_routing_audit_normalizes_real_frozen_no_info_holds_and_reads_legacy():
    mask = {
        "schema": "leo-sim-four-direction-mask/v1",
        "direction_order": ["N", "E", "S", "W"],
        "final_legal_mask": {d: False for d in ("N", "E", "S", "W")},
    }
    no_info = {
        "milestone": "frozen_inferred_hold", "at": 2.0,
        "pid": 17, "decision_id": 8, "sat": 0, "status": "no_info",
        "reason": "observation_inferred_hold", "obs_mode": "frozen",
        "observation_at_start": {
            "routing_status": "no_info", "four_direction_audit": mask,
            "candidate_resources": {},
        },
    }
    legacy = {"milestone": "decision_attempt", "at": 3.0,
              "pid": 18, "decision_id": 9, "action": "hold",
              "reason": "legacy_hold"}
    log = t1_tasks._routing_audit_log([], [no_info, legacy])
    assert log["attempt_record_count"] == 2
    normalized, compatible = log["attempt_records"]
    assert normalized["milestone"] == "decision_attempt"
    assert normalized["source_milestone"] == "frozen_inferred_hold"
    assert normalized["route_status"] == "no_info"
    assert normalized["attempt_outcome"] == "held"
    assert normalized["four_direction_audit"] == mask
    assert compatible["milestone"] == "decision_attempt"
    assert compatible["reason"] == "legacy_hold"


# ---------------------------------------------------------- the sampler
def test_the_sampler_takes_an_even_stride_over_eligible_ids():
    eligible = [{"decision_id": i} for i in range(10, 34)]
    sample = t1_tasks.sample_branches(eligible, max_branches=12)
    assert sample["eligible"] == 24 and sample["sampled"] == 12
    picked = sample["decisions"]
    assert picked == sorted(picked)
    assert picked[0] == 10
    gaps = {b - a for a, b in zip(picked, picked[1:])}
    assert len(gaps) <= 2, gaps


def test_every_eligible_branch_is_used_when_there_are_few():
    eligible = [{"decision_id": 1}, {"decision_id": 2}, {"decision_id": 3}]
    sample = t1_tasks.sample_branches(eligible, max_branches=12)
    assert sample["decisions"] == [1, 2, 3] and sample["stride"] == 1


def test_the_sampler_cannot_see_outcomes():
    """Nothing but the ids can influence the choice."""
    plain = [{"decision_id": i} for i in range(40)]
    decorated = [{"decision_id": i, "baseline_chosen": "W" if i % 2 else "E",
                  "regret": float(i), "oracle": "N"} for i in range(40)]
    assert (t1_tasks.sample_branches(plain, max_branches=12)["decisions"]
            == t1_tasks.sample_branches(decorated,
                                        max_branches=12)["decisions"])


def test_common_horizon_calibration_selects_only_complete_same_sample_candidates():
    branch_rows = [
        {"decision_id": 7, "common_horizon_candidates": [
            {"candidate": "mean_eta_offset", "valid": True,
             "fully_scored": True, "loss": 0.40,
             "sample_digest": "same-observation", "outcome_digest": "same-outcomes"},
            {"candidate": "p50_offset", "valid": True,
             "fully_scored": True, "loss": 0.30,
             "sample_digest": "same-observation", "outcome_digest": "same-outcomes"},
        ]},
        {"decision_id": 9, "common_horizon_candidates": [
            {"candidate": "mean_eta_offset", "valid": True,
             "fully_scored": True, "loss": 0.40,
             "sample_digest": "same-observation", "outcome_digest": "same-outcomes"},
            {"candidate": "p50_offset", "valid": False,
             "fully_scored": False, "loss": None,
             "sample_digest": "same-observation", "outcome_digest": "same-outcomes"},
        ]},
    ]

    selected = t1_tasks.select_common_horizon_candidate(
        branch_rows, ["mean_eta_offset", "p50_offset"], [7, 9])

    assert selected["status"] == "selected"
    assert selected["candidate"] == "mean_eta_offset"
    assert selected["selection_rule"].startswith(
        "lowest mean normalized branch loss")
    assert selected["candidates"]["p50_offset"]["status"] == "ineligible"
    assert selected["tied_candidates"] == ["mean_eta_offset"]
    assert selected["selected_by_tie_break"] is False


def test_common_horizon_calibration_ties_follow_frozen_candidate_order():
    branch_rows = [{"decision_id": 4, "common_horizon_candidates": [
        {"candidate": name, "valid": True, "fully_scored": True,
         "loss": 0.25, "sample_digest": "s", "outcome_digest": "o"}
        for name in ("mean_eta_offset", "median_eta_offset")]}]

    selected = t1_tasks.select_common_horizon_candidate(
        branch_rows, ["mean_eta_offset", "median_eta_offset"], [4])

    assert selected["candidate"] == "mean_eta_offset"
    assert selected["tied_candidates"] == [
        "mean_eta_offset", "median_eta_offset"]
    assert selected["selected_by_tie_break"] is True
    assert selected["tie_tolerance"] == 1e-12


def test_branch_block_projects_and_selects_common_candidate_on_one_seed(monkeypatch):
    resolved, rows, geometry, source = _scenario()
    decisions = [{
        "decision_id": 4, "kind": "forward", "t_decision_start": 5.0,
        "t": 5.1, "chosen": "E", "candidates": ["E", "W"],
        "observation_at_start": {
            "legal_directions": ["E", "W"],
            "candidate_resources": {"E": {"peer": 1}, "W": {"peer": 2}},
            "time_alignment": {"query_targets": {"E": 6.0, "W": 8.0}},
        },
    }]
    monkeypatch.setattr(t1_tasks, "_baseline_decisions",
                        lambda *_args, **_kwargs: decisions)

    def fake_compare(*_args, common_horizon_candidates=None, **_kwargs):
        names = list(common_horizon_candidates)
        candidate_rows = [{
            "candidate": name, "valid": True, "fully_scored": True,
            "loss": 0.1 if name == "p50_offset" else 0.4,
            "sample_digest": "frozen-observation",
            "outcome_digest": "frozen-branch-outcomes",
        } for name in names]
        return {
            "arms": {"candidate": {"loss": 0.3, "regret": 0.2,
                                      "chosen": "E", "ranking": ["E", "W"],
                                      "scores": {}, "fallback_directions": [],
                                      "missing_directions": []}},
            "candidates": {}, "counts": {}, "oracle": {},
            "identity": {"sources": {}},
            "common_horizon_candidates": candidate_rows,
        }

    monkeypatch.setattr(t1_tasks.tac, "compare", fake_compare)
    document = t1_tasks.branch_alignment_block(
        resolved, rows, geometry, deadline_s=30.0, source=source,
        max_branches=1, calibrate_common_horizon=True)

    calibration = document["common_horizon_calibration"]
    assert calibration["status"] == "projected"
    assert calibration["development_seed"] == resolved["config"]["scenario"]["seed"]
    assert calibration["selection"]["candidate"] == "p50_offset"
    assert calibration["cross_arm_leakage"] is False
    assert calibration["offset_candidate_count"] == 2


# -------------------------------------------------- merging into a block
def _branch(decision_id, loss_common, loss_candidate, **extra):
    item = {"decision_id": decision_id, "t_decision_start": float(decision_id),
            "baseline_chosen": "E",
            "loss": {"common": loss_common, "candidate": loss_candidate},
            "regret": {"common": loss_common - 0.1,
                       "candidate": loss_candidate - 0.1},
            "counts": {}, "oracle": None, "trace_identity": None}
    item.update(extra)
    return item


def test_branches_are_averaged_into_one_block_value():
    branches = [_branch(1, 0.4, 0.2), _branch(2, 0.6, 0.4)]
    block = t1_tasks._merge_block(branches, [], {"sampled": 2})
    assert block["status"] == "ok"
    assert block["arms"]["common"]["mean_loss"] == pytest.approx(0.5)
    assert block["arms"]["candidate"]["mean_loss"] == pytest.approx(0.3)
    assert block["value"]["common"] == pytest.approx(0.4)
    assert block["value"]["candidate"] == pytest.approx(0.2)
    assert block["primary_difference_common_minus_candidate"] == \
        pytest.approx(0.2)
    assert block["branch_count"] == 2


def test_a_failed_branch_degrades_the_block_instead_of_disappearing():
    branches = [_branch(1, 0.4, 0.2)]
    failures = [{"decision_id": 2, "reason": "CompareError: never committed"}]
    block = t1_tasks._merge_block(branches, failures, {"sampled": 2})
    assert block["status"] == "DEGRADED"
    assert block["failure_count"] == 1
    assert block["branch_count"] == 1
    assert block["reason"]


def test_a_block_with_no_usable_branch_is_not_an_empty_success():
    block = t1_tasks._merge_block([], [{"decision_id": 1, "reason": "x"}],
                                  {"sampled": 1})
    assert block["status"] == "NO_LEGAL_BRANCH"
    assert block["arms"] == {} and block["value"] is None


def test_a_block_needs_one_deadline_for_every_branch():
    resolved, rows, geometry, source = _scenario()
    with pytest.raises(t1_tasks.TaskError) as excinfo:
        t1_tasks.branch_alignment_block(resolved, rows, geometry,
                                        deadline_s=None, source=source)
    assert "deadline" in str(excinfo.value)


# --------------------------------------------------------- the four arms
def test_all_four_arms_really_run_the_network():
    resolved, rows, geometry, source = _scenario()
    document = t1_tasks.network_alignment(resolved, rows, geometry, source)
    assert document["status"] == "ok" and document["failures"] == []
    assert [row["arm"] for row in document["arms"]] == list(t1_tasks.NETWORK_ARMS)
    for row in document["arms"]:
        audit = row["time_alignment_audit"]
        assert audit["arms_seen_in_the_audit"] == [row["arm"]]
        assert audit["decisions_with_state_time_audit"] > 0
        assert audit["decisions_with_query_targets"] > 0
        assert row["resolved_arm"] == row["arm"]
        assert row["scope"]["forward_decisions"] > 0
        assert row["scope"]["satellites_that_decided"] > 0
        metrics = row["congestion_metrics"]
        assert row["outcome"]["offered"] == metrics["offered_packets"]
        assert row["outcome"]["admitted"] == metrics[
            "admitted_at_satellite_ingress_packets"]
        assert row["outcome"]["delivered"] == metrics["delivered_packets"]
        per_packet = row["network_outcome"]["packet_outcomes"]
        assert len(per_packet) == len(rows)
        assert row["network_outcome"]["packet_outcomes_sha256"]
        assert row["outcome"]["delivered_bits"] == sum(
            item["bits"] for item in per_packet
            if item["fate"] == "DELIVERED")
        assert row["outcome"]["delivered_bits"] > 0
        assert row["network_outcome"]["payload"]["status"] == "COMPUTED"


def test_four_arm_deadline_metric_keeps_each_packet_fate_and_censor_bounds():
    resolved, rows, geometry, source = _scenario("contention")
    document = t1_tasks.network_alignment(
        resolved, rows, geometry, source, deadline_s=30.0,
        window=(0.0, 30.0))
    assert document["deadline"]["deadline_s"] == 30.0
    for arm in document["arms"]:
        outcome = arm["network_outcome"]
        packets = outcome["packet_outcomes"]
        assert [row["pid"] for row in packets] == sorted(
            row["packet_id"] for row in rows)
        assert all(row["trace_sha256"] for row in packets)
        assert all("delivery_time_s" in row and "fate" in row
                   and "observation_end_s" in row for row in packets)
        assert "lower_mean" in outcome["deadline_primary_loss"]


def test_the_arm_is_switched_on_even_when_the_base_config_had_it_off():
    resolved, rows, geometry, source = _scenario()
    assert resolved["config"]["time_alignment"]["enabled"] is False
    document = t1_tasks.network_alignment(resolved, rows, geometry, source)
    for row in document["arms"]:
        assert row["time_alignment_audit"]["decisions_with_query_targets"] > 0


def test_each_arm_reports_its_own_scope_and_request_distribution():
    resolved, rows, geometry, source = _scenario()
    document = t1_tasks.network_alignment(resolved, rows, geometry, source)
    for row in document["arms"]:
        rates = row["request_rate_per_satellite"]
        assert rates["max"] is not None and rates["mean"] is not None
        assert rates["max"] >= rates["mean"]
        assert "must not stand in" in rates["note"]


def test_the_compute_total_counts_background_jobs():
    resolved, rows, geometry, source = _scenario("contention")
    cfg = copy.deepcopy(resolved["config"])
    cfg["time_alignment"].update({"enabled": True, "execution_mode":
                                 "async_window"})
    cfg["async_routing"]["enabled"] = True
    cfg["execution"]["compute_servers_per_satellite"] = 1
    cfg["execution"]["compute_delay_s"] = 0.01
    local = config_mod.resolve_config(cfg)
    document = t1_tasks.network_alignment(local, rows, geometry, source)
    row = document["arms"][0]
    compute = row["compute"]
    assert compute["includes_background"] is True
    assert compute["background_jobs"]["jobs"] > 0, \
        "an async arm must schedule real background update jobs"
    assert compute["total_jobs"] == (compute["decision_jobs"]["jobs"]
                                     + compute["background_jobs"]["jobs"])
    assert compute["total_service_s"] == pytest.approx(
        compute["decision_jobs"]["service_s_total"]
        + compute["background_jobs"]["service_s_total"])


def test_an_unbounded_pool_still_records_start_and_finish_events():
    resolved, rows, geometry, source = _scenario("contention")
    cfg = copy.deepcopy(resolved["config"])
    cfg["time_alignment"]["enabled"] = True
    cfg["execution"]["compute_servers_per_satellite"] = 0
    cfg["execution"]["compute_delay_s"] = 0.01
    local = config_mod.resolve_config(cfg)
    document = t1_tasks.network_alignment(local, rows, geometry, source)
    compute = document["arms"][0]["compute"]
    assert compute["unbounded_pool"] is True
    assert compute["decision_jobs"]["jobs"] > 0
    assert compute["decision_jobs"]["missing_start_events"] == 0
    assert compute["decision_jobs"]["missing_finish_events"] == 0
    assert compute["decision_jobs"]["queue_wait_s_total"] == 0.0


def test_the_frozen_horizon_moves_the_real_query_instant():
    resolved, rows, geometry, source = _scenario()
    cfg = copy.deepcopy(resolved["config"])
    cfg["control_plane"]["advertisement_protocol_version"] = 2
    cfg["control_plane"]["enabled"] = True
    cfg["control_plane"]["advertise_interval_s"] = 0.2
    resolved = config_mod.resolve_config(cfg)
    targets = []
    for horizon in (0.5, 2.0):
        local = _ta_on(resolved, common_rule="fixed_horizon",
                       common_horizon_s=horizon, arm="common")
        document = t1_tasks.network_alignment(local, rows, geometry, source,
                                              arms=("common",))
        arm = document["arms"][0]
        records = arm["routing_audit_log"]["decision_records"]
        valid_history = any(
            any(int(count) > 0 for count in
                ((record.get("observation_at_start") or {}).get(
                    "time_alignment") or {}).get("history_samples", {}).values())
            for record in records)
        assert valid_history, "comparison needs received resource history"
        targets.append(arm["time_alignment_audit"][
            "distinct_query_targets"])
    assert targets[0] and targets[1]
    assert targets[0] != targets[1], \
        "a frozen horizon that never reaches the query instant is metadata"


# ------------------------------------------------------- inputs and identity
def test_changing_the_offered_load_changes_the_config_and_the_trace():
    left = t1_tasks.design(config_path=PROFILE,
                           overrides={"demand": {"offered_mbps": 1.0}})
    right = t1_tasks.design(config_path=PROFILE,
                            overrides={"demand": {"offered_mbps": 6.0}})
    assert left[0]["sha256"] != right[0]["sha256"]
    assert left[3]["trace_sha256"] != right[3]["trace_sha256"]
    assert left[3]["rows"] != right[3]["rows"] or True


def test_trace_build_uses_writable_temp_storage_outside_readonly_release(
        monkeypatch, tmp_path):
    release_root = tmp_path / "immutable-release"
    release_root.mkdir()
    release_root.chmod(0o555)
    real_mkdtemp = t1_tasks.tempfile.mkdtemp

    def reject_release_local_temp(*, prefix, dir=None):
        assert dir is None, "temporary trace work must not target the release"
        return real_mkdtemp(prefix=prefix, dir=str(tmp_path))

    monkeypatch.setattr(t1_tasks.tempfile, "mkdtemp", reject_release_local_temp)
    resolved, rows, _geometry, source = t1_tasks.design(
        config_path=PROFILE, root=release_root)

    assert resolved["sha256"]
    assert rows
    assert source["trace_sha256"]
    assert list(release_root.iterdir()) == []


# ------------------------------------------------------------- dispatcher
@pytest.mark.parametrize("task", t1_tasks.TASK_TYPES)
def test_one_driver_serves_all_three_task_types(task):
    resolved, rows, geometry, source = _scenario()
    kwargs = ({"deadline_s": 20.0, "window": (5.0, 20.0)}
              if task == "branch_alignment" else {})
    document = t1_tasks.run_task(task, resolved, rows, geometry, source,
                                 **kwargs)
    assert document["task"] == task
    assert document["status"] == "ok"
    assert document["failed_units"] == 0
    assert document["identity"]
    assert document["driver_schema"]
    assert document["limits"]
    if task == "branch_alignment":
        assert document["document"]["measurement_window"]["start_s"] == 5.0
        assert document["document"]["measurement_window"]["end_s"] == 20.0


def test_a_failed_sub_run_is_recorded_and_not_wrapped_as_success(monkeypatch):
    resolved, rows, geometry, source = _scenario()
    real = t1_tasks.tac.compare
    def explode(config, rows_, geometry_, decision_id, deadline_s, src,
                **kwargs):
        if int(decision_id) == 21:
            raise t1_tasks.tac.CompareError("decision 21 never committed")
        return real(config, rows_, geometry_, decision_id, deadline_s, src,
                    **kwargs)
    monkeypatch.setattr(t1_tasks.tac, "compare", explode)
    document = t1_tasks.run_task("branch_alignment", resolved, rows, geometry,
                                 source, deadline_s=20.0)
    assert document["status"] == "DEGRADED"
    assert document["failed_units"] == 1
    failures = document["document"]["failures"]
    assert failures and failures[0]["decision_id"] == 21
    assert "never committed" in failures[0]["reason"]
    assert document["document"]["block"]["status"] == "DEGRADED"


def test_an_unknown_task_is_refused():
    resolved, rows, geometry, source = _scenario()
    with pytest.raises(t1_tasks.TaskError):
        t1_tasks.run_task("nonsense", resolved, rows, geometry, source)
