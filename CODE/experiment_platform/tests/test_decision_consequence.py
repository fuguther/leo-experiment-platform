"""The synthetic self-test shipped with decision_consequence must pass."""
from __future__ import annotations

from CODE.experiment_platform.decision_consequence import self_test


def test_self_test_passes():
    assert self_test() == 0


from CODE.experiment_platform import decision_consequence as dc


def _pkt(fate="DELIVERED", delay=0.1, deadline=0.2, loss=0.5,
         censor=None):
    return {"pid": 1, "fate": fate, "emit_time_s": 0.0,
            "delivery_time_s": delay, "observed_delay_s": delay,
            "deadline_s": deadline, "deadline_loss": loss,
            "terminal_reason": None, "censor_reason": censor}


def test_pp_delta_is_percentage_points_not_continuous_loss():
    packets = {"A": {1: _pkt(delay=0.1, deadline=0.2, loss=0.5)},
               "B": {1: _pkt(delay=0.3, deadline=0.2, loss=1.0)}}
    biz = dc.packet_business(1, packets, "A", "B")
    assert biz["pp_delta"] == 100.0
    assert abs(biz["deadline_loss_delta_frac"] - 0.5) < 1e-12
    assert biz["miss_delta"] == 1


def test_censored_packet_is_not_a_miss():
    pkt = _pkt(fate="IN_SYSTEM_AT_STOP", censor="administrative_censoring_at_stop")
    assert dc.packet_miss(pkt) is None


def test_business_bootstrap_deduplicates_packets():
    packets = {"A": {1: _pkt()}, "B": {1: _pkt(delay=0.1)}}
    out = dc.aggregate_business([(1, 0), (1, 1), (1, 2)], None, packets, "A", "B")
    assert out["n_decisions"] == 3
    assert out["n_packets"] == 1
    assert out["pp_delta"]["n"] == 1
    assert out["delay_delta_s"]["n"] == 1


def test_forced_decisions_are_excluded_from_flips_and_business():
    audit = dc._synthetic_case()
    for arm, chosen in (("stale", "N"), ("common", "S")):
        audit[arm]["decisions"].append({
            "pid": 999, "hop_index": 0, "chosen": chosen, "kind": "forward",
            "t_commit_s": 1.0, "pred": {}, "forced": True})
        audit[arm]["packets"].append({
            "pid": 999, "fate": "DELIVERED", "emit_time_s": 0.0,
            "delivery_time_s": 0.3, "observed_delay_s": 0.3,
            "deadline_s": 0.2, "deadline_loss": 1.0,
            "terminal_reason": None, "censor_reason": None})
    result = dc.analyse(audit, "stale", "common")
    assert result["audit"]["n_flipped"] == 2
    assert result["flip_business"]["flipped"]["n_packets"] == 2
    assert "observable_subset" in result["flip_business"]["flipped"]
    assert "deadline_loss_delta_frac" in result["flip_business"]["flipped"]
    assert "not population" in result["estimand_note"]
