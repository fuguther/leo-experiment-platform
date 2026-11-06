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
