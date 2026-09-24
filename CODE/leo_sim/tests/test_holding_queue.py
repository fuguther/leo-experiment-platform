from types import SimpleNamespace

import pytest

from CODE.leo_sim import kernel
from CODE.leo_sim.kernel import QueueArea, SatelliteHoldingQueue
from CODE.leo_sim.tests.helpers import StaticGeometry, make_cfg, row


def packet(pid, bits):
    return SimpleNamespace(pid=pid, bits=bits)


def test_holding_queue_enforces_capacity_and_fifo():
    area = QueueArea()
    queue = SatelliteHoldingQueue(capacity_bits=10, area=area)
    first = packet(1, 6)
    second = packet(2, 4)
    rejected = packet(3, 1)

    assert queue.put(first, now=0.0)
    assert queue.put(second, now=1.0)
    assert not queue.put(rejected, now=2.0)
    assert queue.queued_bits == 10
    assert [p.pid for p in queue] == [1, 2]

    assert queue.pop(now=3.0) is first
    assert queue.queued_bits == 4
    area.close(4.0)
    assert area.area == pytest.approx(30.0)


def test_holding_queue_remove_updates_bits_and_preserves_order():
    area = QueueArea()
    queue = SatelliteHoldingQueue(capacity_bits=20, area=area)
    first = packet(1, 5)
    second = packet(2, 7)
    third = packet(3, 3)
    for pkt in (first, second, third):
        assert queue.put(pkt, now=0.0)

    queue.remove(second, now=2.0)

    assert queue.queued_bits == 8
    assert [p.pid for p in queue] == [1, 3]
    assert queue.pop(now=2.0) is first
    assert queue.pop(now=2.0) is third
    assert queue.pop(now=2.0) is None


def test_kernel_holding_overflow_is_a_terminal_fate_and_snapshot_is_bounded():
    geo = StaticGeometry(1, neighbors_map={0: {}})
    cfg = make_cfg({
        "scenario": {"num_satellites": 1, "duration_s": 1.0},
        "endpoints": {"sites": [
            {"name": "src", "lat": 0.0, "lon": 0.0},
            {"name": "dst", "lat": 0.0, "lon": 10.0},
        ]},
        "access": {"holding_queue_bits": 8_000},
    })
    k = kernel.Kernel(cfg, [row(99, 0.0, "G1:90:0", "G1:90:10")], geometry=geo)
    # lazy activation (#28): endpoints are not pre-built from the trace, so
    # use the row's cell ids directly
    src, dst = "G1:90:0", "G1:90:10"
    first = kernel.DataPacket(1, src, dst, 8_000, None, 0.0)
    second = kernel.DataPacket(2, src, dst, 8_000, None, 0.0)
    k.ledger.register(first.pid, first.bits)
    k.ledger.register(second.pid, second.bits)

    assert k._hold_packet(0, first)
    assert not k._hold_packet(0, second)
    assert k.ledger.fate_of(second.pid) == "HOLDING_QUEUE_OVERFLOW"
    assert k.snapshot_global()["holding"][0] == {
        "queued_bits": 8_000, "capacity_bits": 8_000}

    result = k.run()
    assert result["fate_counts"]["HOLDING_QUEUE_OVERFLOW"] == 1
    assert result["queue_area_bits_s"]["holding"] == pytest.approx(8_000.0)


def test_wait_until_keeps_packet_held_until_the_requested_time():
    geo = StaticGeometry(1, neighbors_map={0: {}})
    cfg = make_cfg({"scenario": {"num_satellites": 1}})
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, "src", "dst", 8_000, None, 0.0)
    pkt.holding_until = 5.0
    assert k.pending[0].put(pkt, now=0.0)
    snapshot = k.snapshot_global()
    assert snapshot["pending"][0][0]["holding_until"] == 5.0

    assert k.pending[0].take_ready(4.9) == []
    assert [p.pid for p in k.pending[0].take_ready(5.0)] == [1]


def test_holding_deadline_sweep_removes_expired_packets():
    geo = StaticGeometry(1, neighbors_map={0: {}})
    cfg = make_cfg({"scenario": {"num_satellites": 1}})
    k = kernel.Kernel(cfg, [], geometry=geo)
    pkt = kernel.DataPacket(1, "src", "dst", 8_000, 2.0, 0.0)
    pkt.deadline = 2.0
    k.ledger.register(pkt.pid, pkt.bits)
    assert k.pending[0].put(pkt, now=0.0)

    assert k.pending[0].sweep_expired(2.1) == [pkt]
    k._fail(pkt, "DATA_DEADLINE_EXPIRED")
    assert k.ledger.fate_of(pkt.pid) == "DATA_DEADLINE_EXPIRED"
