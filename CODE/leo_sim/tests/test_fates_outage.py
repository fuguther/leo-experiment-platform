"""Tests for fates and outage modules."""
import pytest

from CODE.leo_sim import fates, outage, rng


def test_unique_fate_and_conservation():
    led = fates.DataFateLedger()
    led.register(1, 100)
    led.register(2, 200)
    led.register(3, 300)
    led.record(1, "DELIVERED")
    led.record(2, "ISL_QUEUE_OVERFLOW")
    with pytest.raises(fates.FateError, match="duplicate fate"):
        led.record(2, "NO_ROUTE")
    led.close_at_stop()  # packet 3 -> IN_SYSTEM_AT_STOP
    t = led.check_conservation()
    assert t == {
        "offered_bits": 600,
        "delivered_bits": 100,
        "terminal_loss_bits": 200,
        "in_system_bits_at_stop": 300,
    }
    assert led.fate_counts()["IN_SYSTEM_AT_STOP"] == 1


def test_invalid_fate_and_unregistered_rejected():
    led = fates.DataFateLedger()
    with pytest.raises(fates.FateError):
        led.record(9, "DELIVERED")
    led.register(1, 10)
    with pytest.raises(fates.FateError, match="invalid data fate"):
        led.record(1, "CONTROL_EXPIRED")


def test_fate_bits_must_match_registered_offered_bits():
    """Per-packet bit identity is part of the ledger invariant: offsetting
    bit errors must not be able to fake bit conservation."""
    led = fates.DataFateLedger()
    led.register(1, 100)
    led.register(2, 100)
    with pytest.raises(fates.FateError):
        led.record(1, "DELIVERED", 150)
    with pytest.raises(fates.FateError):
        led.record(2, "ACCESS_REJECTED", 50)
    ctl = fates.ControlFateLedger()
    ctl.register(10, 80)
    with pytest.raises(fates.FateError):
        ctl.record(10, "DELIVERED", 81, received_at=1.0)


def test_control_ledger_full_accounting_and_conservation():
    c = fates.ControlFateLedger()
    c.register(1, 8000)
    c.register(2, 8000)
    c.register(3, 8000)
    c.register(4, 8000)
    c.record(1, "DELIVERED", 8000, received_at=0.5)
    c.record(2, "CONTROL_EXPIRED", 8000, received_at=0.9)
    c.record(3, "DUPLICATE", 8000, received_at=1.1)
    c.close_at_stop()  # instance 4 -> IN_SYSTEM_AT_STOP
    t = c.check_conservation()
    assert t == {
        "offered_bits": 32000,
        "delivered_bits": 8000,
        "terminal_loss_bits": 16000,  # expired + duplicate
        "in_system_bits_at_stop": 8000,
    }
    with pytest.raises(fates.FateError):
        c.record(1, "DELIVERED", 8000, received_at=1.2)  # duplicate fate
    with pytest.raises(fates.FateError):
        # arrival fates require the receive time (round-4 contract)
        c2 = fates.ControlFateLedger()
        c2.register(9, 8000)
        c2.record(9, "DELIVERED", 8000)


def test_ge_disabled_by_default_and_never_down():
    ge = outage.GilbertElliott(mean_good_s=1.0, mean_bad_s=1.0,
                               gen=rng.streams(1)["ge_isl"], enabled=False)
    assert not ge.is_down(100.0)


def test_ge_deterministic_and_eventually_down():
    kw = dict(mean_good_s=0.05, mean_bad_s=0.02, enabled=True)
    g1 = outage.GilbertElliott(gen=rng.streams(1)["ge_isl"], **kw)
    g2 = outage.GilbertElliott(gen=rng.streams(1)["ge_isl"], **kw)
    seq1 = [g1.is_down(t * 0.005) for t in range(2000)]
    seq2 = [g2.is_down(t * 0.005) for t in range(2000)]
    assert seq1 == seq2
    assert any(seq1) and not all(seq1)


def test_ge_state_is_query_pattern_independent():
    # the continuous-time trajectory must not depend on how often it is read
    kw = dict(mean_good_s=0.1, mean_bad_s=0.05, enabled=True)
    dense = outage.GilbertElliott(gen=rng.streams(7)["ge_isl"], **kw)
    sparse = outage.GilbertElliott(gen=rng.streams(7)["ge_isl"], **kw)
    dense_states = [dense.is_down(t * 0.001) for t in range(3001)]
    sparse_states = [sparse.is_down(float(t)) for t in range(4)]
    for t in range(4):
        assert dense_states[t * 1000] == sparse_states[t]


def test_ge_link_streams_order_independent():
    a1 = outage.GilbertElliott(0.1, 0.1, rng.link_stream(3, "isl:0:E"), enabled=True)
    b1 = outage.GilbertElliott(0.1, 0.1, rng.link_stream(3, "isl:0:W"), enabled=True)
    b2 = outage.GilbertElliott(0.1, 0.1, rng.link_stream(3, "isl:0:W"), enabled=True)
    a2 = outage.GilbertElliott(0.1, 0.1, rng.link_stream(3, "isl:0:E"), enabled=True)
    # created in the opposite order: identical trajectories per link key
    assert [a1.is_down(t) for t in (0.01, 0.05, 0.2)] == \
           [a2.is_down(t) for t in (0.01, 0.05, 0.2)]
    assert [b1.is_down(t) for t in (0.01, 0.05, 0.2)] == \
           [b2.is_down(t) for t in (0.01, 0.05, 0.2)]


def test_geometry_loss_flag():
    # geometry loss is a deterministic function of availability, not RNG
    assert outage.geometry_loss(available=False, enabled=True)
    assert not outage.geometry_loss(available=False, enabled=False)
    assert not outage.geometry_loss(available=True, enabled=True)
