from dataclasses import fields
from types import SimpleNamespace

import numpy as np
import pytest

from CODE.leo_sim import config, control, kernel, model, outage, routing
from CODE.leo_sim.rl.local_forward import LocalForwardAdapter, primal_observation
from CODE.leo_sim.rl.pigou_labels import build_dataset


class _Geometry:
    num_satellites = 3
    certifies_change_times = True
    min_elevation_deg = 10
    max_isl_km = 6000

    def ecef(self, *_args):
        raise AssertionError("scripted outage was bypassed by geometry composition")

    def isl_available(self, *_args):
        return True

    def next_isl_change(self, *_args):
        return None

    def subpoint(self, sat, _at):
        return 0.0, float(sat), 800.0

    def isl_range_km(self, *_args):
        return 1000


def test_scheduled_outage_blocks_both_directions_and_recovers():
    g = model.MemoizedGeometry(outage.ScheduledISLGeometry(_Geometry(), [
        {"a": 0, "b": 1, "start_s": 2.0, "end_s": 3.5}
    ]))
    assert g.isl_available(0, 1, 1.999)
    assert not g.isl_available(1, 0, 2.0)
    assert g.isl_available(0, 1, 3.5)
    assert g.isl_available(0, 2, 3.0)
    assert g.next_isl_change(0, 1, 1.0, 9.5) == 2.0
    assert g.next_isl_change(1, 0, 2.0, 9.5) == 3.5
    assert g.next_isl_change(0, 1, 3.5, 9.5) is None


def test_egress_fallback_requires_non_oracle_single_hop():
    routing_cfg = {"ephemeris_egress_fallback": True}
    for policy, hops in (("known_serving", 1), ("oracle", 1), ("hop", 2)):
        with pytest.raises(config.ConfigError, match="one-hop"):
            config.resolve_config({"routing": {**routing_cfg, "policy": policy},
                                   "control_plane": {"vis_k": hops}})
    cfg = config.resolve_config({"routing": routing_cfg, "control_plane": {"vis_k": 1}})
    assert cfg["config"]["routing"]["policy"] == "hop"


def test_local_features_cannot_read_remote_links_future_or_expired_entries():
    sim = kernel.Kernel.__new__(kernel.Kernel)
    sim.env = SimpleNamespace(now=0.5)
    sim.cfg_links = {"isl_queue_bits": 100}
    sim.cfg_rt = {"policy": "hop", "ephemeris_egress_fallback": True}
    sim._staged_policy = None
    sim._planned_egress = {"dest": (2,)}
    sim.isl_rate_bps = 100
    sim._link_rate = lambda *_args, **_kwargs: 100
    sim.geometry = _Geometry()
    sim.endpoints = {"dest": SimpleNamespace(lat=0, lon=15)}
    sim._routing_reverse_adj = {0: [1], 1: [0, 2], 2: [1]}
    link = SimpleNamespace(peer=1, data_bits=20, ctrl_bits=10, gen=1)
    sim.isls = {0: {"N": link}}
    sim.caches = {0: control.LocalCache()}
    sim._serving_sats = lambda _: pytest.fail("queried live serving satellites")
    packet = SimpleNamespace(src="src", dst="dest", pid=1, bits=10, emitted_at=0.4, path=[0])
    payload = {"serve_cells": ["dest"], "isl_queue_bits": {"N": {"peer": 2, "value": 99,
               "rate_bps": 100, "work_ahead_bits_proxy": 100}},
               "isl_propagation_s": {"N": {"peer": 2, "value": 0.01}}}
    sim.caches[0].put(control.CacheEntry(1, payload, 0.1, 0.15, 0.2, 1))
    sim.caches[0].put(control.CacheEntry(1, payload, 0.49, 0.6, 0.5, 1))
    unread = sim._local_forward_view(0, packet, ("N",), ("N",))
    assert unread.ports[0].reports == (None,) * 4
    assert unread.ports[0].queue == 0.3
    assert "deadline" not in [item.name for item in fields(unread)]
    assert primal_observation(unread).shape == (50,)
    sim.env.now = 0.7
    visible = sim._local_forward_view(0, packet, ("N",), ("N",))
    assert visible.ports[0].reports[0].queue == 0.99
    assert visible.ports[0].received_at == 0.6
    assert sim._serving_source(0, "dest", 0.7, 1) == [1]
    sim.env.now = 1.0
    assert sim._local_forward_view(0, packet, ("N",), ("N",)).ports[0].reports == (None,) * 4


def test_paired_event_label_uses_generation_and_excludes_unassigned_overflow():
    snapshot = (1.0, 0, (1, None, None, None), np.zeros((4, 4, 8)),
                np.zeros((4, 8)), np.zeros((4, 8, 9)))
    decision = (0, 1, 1.0, 0, 1, 7, 0, np.zeros(10), np.zeros((4, 10)), 0, 0.9, 0)
    events = [
        ("topology", 0.0, ((1,), (0, 2), (1,))),
        (1.0, 0, "N", 1, 7, False, 10, 0, False, "enqueue_data", 1),
        (1.1, 0, "E", 2, 4, False, 100, 0, True, "transmission_start", None),
        (1.2, 0, "N", 1, 7, False, 0, 0, False, "service_end", None),
    ]
    result = {"local_forward_adapter": {"snapshots": [snapshot], "decisions": [decision]},
              "stop_time_s": 3, "fates": {1: "DELIVERED", 2: "ISL_QUEUE_OVERFLOW"},
              "deliveries": {1: {"delivered_at": 1.15}}}
    rows = [{"packet_id": 1, "emit_time_s": 0.95, "deadline_at_s": 1.95}]
    cfg = {"demand": {"deadline_s": 1.0, "packet_bits": 10},
           "links": {"isl_queue_bits": 100, "isl_rate_mbps": 1}}
    snaps, packets, audit = build_dataset(result, events, rows, cfg)
    assert snaps["y_valid"].shape == (1, 4, 3)
    assert packets["g_ego"][0] == pytest.approx(0.90)
    assert packets["g_ext"][0] == 0.0
    assert audit["unassigned_overflow_packets"] == 1


def test_static_fallback_never_invents_an_admissible_port():
    from CODE.leo_sim.rl.local_forward import LocalForwardView, Port

    view = LocalForwardView(0, 0.0, 1, "src", "dst", 0.0, 10, 0,
                            (0.0, 1.0, 0.8), 1,
                            (Port(1, queue=0.99, hops=1), Port(2, hops=2), Port(None), Port(None)),
                            ("S",), ("N",), ("S",))
    assert LocalForwardAdapter({"kind": "static_guard"}, 13).choose(view, 4) == "S"


def test_two_episode_merge_rejects_cross_episode_snapshot_alias(tmp_path):
    from CODE.leo_sim.rl.pigou_labels import merge_dataset

    paths = []
    for episode, ids in enumerate(([0, 2], [0, 1])):
        snap = tmp_path / f"snap-{episode}.npz"
        pkt = tmp_path / f"pkt-{episode}.npz"
        np.savez(snap, x_2hop=np.zeros((2, 4, 4, 8)))
        np.savez(pkt, snap_id=np.asarray(ids))
        paths.append((snap, pkt))
    with pytest.raises(ValueError, match="outside its own episode"):
        merge_dataset(paths)


def test_pigou_hop_guard_restricts_late_decisions_to_shortest_legal_ports():
    from CODE.leo_sim.rl.local_forward import LocalForwardView, Port

    class _Model:
        def encode(self, *_args):
            return np.zeros(32, dtype=np.float32)

        def score(self, *_args):
            ego = np.asarray([0.0, 0.0, 1.0, 0.0], dtype=np.float32)
            quantile = np.zeros((4, 16), dtype=np.float32)
            return ego, quantile

    view = LocalForwardView(
        0, 0.0, 1, "src", "dst", 0.0, 10, 6,
        (0.0, 1.0, 0.8), 2,
        (Port(1, hops=1), Port(2, hops=3), Port(None), Port(None)),
        ("N", "S"), ("N",), ("N",))
    adapter = LocalForwardAdapter(
        {"kind": "pigou_metadrl", "checkpoint_path": "/unused",
         "checkpoint_sha256": "0" * 64, "hop_guard": 4,
         "argmax": True}, 7)
    adapter.model = _Model()
    assert adapter.choose(view, 1) == "N"
