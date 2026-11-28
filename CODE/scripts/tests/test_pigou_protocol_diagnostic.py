from CODE.scripts import pigou_protocol_diagnostic as diagnostic
from CODE.scripts import pigou_study


def _row(pid, when, delivered):
    row = {"packet_id": pid, "emit_time_s": when, "deadline_at_s": when + 0.05}
    delivery = {"delivered_at": when + 0.001}
    return row, (delivery if delivered else None)


def test_recovery_reports_onset_clear_and_right_censoring():
    rows = []
    deliveries = {}
    for pid, when in enumerate((0.5, 0.6, 2.1, *(
            3.55 + i * 0.1 for i in range(10))), 1):
        delivered = abs(when - 2.1) > 1e-12
        row, delivery = _row(pid, when, delivered)
        rows.append(row)
        if delivery is not None:
            deliveries[pid] = delivery
    events = [
        {"kind": "service_start", "stage": "isl", "pid": pid}
        for pid in (1, 2, 3, 4)
    ]
    metrics = pigou_study._metric_components(
        rows, {"deliveries": deliveries, "packet_events": events}
    )
    assert metrics["t_rec_onset_s"] == 0.0
    assert metrics["t_rec_clear_s"] == 0.8
    assert metrics["t_rec_right_censored"] is False
    assert metrics["rgd_fraction_seconds"] == 0.0
    assert metrics["hop_attempts_total"] == 4
    assert metrics["hop_attempts_wasted"] == 1
    assert metrics["whr"] == 0.25

    censored = pigou_study._metric_components(
        rows[:3], {"deliveries": {1: {"delivered_at": 0.501},
                                  2: {"delivered_at": 0.601}},
                   "packet_events": []},
    )
    assert censored["t_rec_onset_s"] is None
    assert censored["t_rec_clear_s"] is None
    assert censored["t_rec_right_censored"] is True


def test_protocol_diagnostic_plan_separates_main_and_sensitivity_arms(tmp_path):
    manifest = diagnostic.build_plan(tmp_path, seed=101)
    assert manifest["status"] == "PLANNED_NOT_RUN"
    assert manifest["arm_count"] == 10
    names = {arm["name"] for arm in manifest["arms"]}
    assert {"sp-retry-off", "dyn-retry-off", "sp-rto-exp", "dyn-rto-exp"} <= names
    assert {"sp-granularity-64k", "sp-granularity-12k"} <= names
    for arm in manifest["arms"]:
        if arm["name"].endswith("retry-off"):
            assert arm["retry"]["enabled"] is False
        if arm["retry"]["enabled"]:
            assert arm["retry"]["discovery"] == "endpoint_timeout"
            assert arm["retry"]["deadline_policy"] == "endpoint_only"


def test_age_aware_diagnostic_spec_is_applied_before_resolution():
    from CODE.leo_sim import config
    from CODE.leo_sim.tests.helpers import make_cfg

    raw = make_cfg()["config"]
    cfg = pigou_study.cell_config(raw, {
        "alg": "B2",
        "time_alignment": {
            "enabled": True,
            "state_source": "ideal_current",
            "predictor": "hold_last",
        },
        "ephemeris_egress_fallback": False,
    })
    config.resolve_config(cfg)
    assert cfg["time_alignment"]["state_source"] == "ideal_current"
    assert cfg["routing"]["ephemeris_egress_fallback"] is False
