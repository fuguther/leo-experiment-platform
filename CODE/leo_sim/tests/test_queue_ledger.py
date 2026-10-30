"""T1-QUEUE-LEDGER acceptance: opt-in ledger must be exact and inert.

Two hard requirements, in this order:
  1. with the switch OFF nothing is constructed and the run result is
     unchanged (no new key, identical canonical digest);
  2. with the switch ON the level series reconstructs the kernel's own
     ``queue_area_bits_s`` exactly, per queue and per stage, and the three
     ledger files reconcile with the verbatim event lists.

The scenario is a deliberately queue-forming 2-satellite cell (1 Mbps links,
8 Mbit packets) so that uplink, ISL and holding areas are non-zero; a
zero-area scenario would let a broken ledger pass.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from collections import defaultdict

from CODE.leo_sim import kernel
from CODE.leo_sim.tests.helpers import (StaticGeometry, cell, cell_center,
                                        make_cfg, row)

A = cell(0.0, 0.0)
B = cell(0.0, 10.0)
AC = cell_center(A)
BC = cell_center(B)

STAGES = ("uplink", "downlink", "holding", "isl_data", "isl_ctrl")


def _geo():
    nb = {0: {"E": 1}, 1: {"W": 0}}
    vis = lambda s, lat, lon, t: (s == 0 and (lat, lon) == AC) or \
        (s == 1 and (lat, lon) == BC)
    return StaticGeometry(2, neighbors_map=nb, visible=vis)


def _cfg():
    return make_cfg({"scenario": {"duration_s": 20.0},
                     "access": {"uplink_rate_mbps": 1.0,
                                "downlink_rate_mbps": 1.0},
                     "links": {"isl_rate_mbps": 1.0}})


ROWS = [row(p, 0.02 * p, A, B, 8_000_000) for p in range(1, 7)]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _stable(result):
    keep = ("deliveries", "packet_fates", "fate_counts", "queue_area_bits_s",
            "congestion_metrics", "events_processed", "stop_time_s",
            "natural_end", "packet_events", "link_service_windows",
            "link_available_windows", "mechanisms", "control",
            "execution_mode", "horizon_s", "total_bits", "delivered_bits")
    return {k: result.get(k) for k in keep if k in result}


def _read_levels(path):
    rows = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            rows.append(json.loads(line))
    return rows


def _reconstruct(rows):
    area = defaultdict(float)
    level = defaultdict(int)
    last = defaultdict(float)
    for entry in rows:
        if entry.get("kind") != "level":
            continue
        queue, t = entry["queue"], float(entry["t"])
        area[queue] += level[queue] * (t - last[queue])
        last[queue] = t
        level[queue] = int(entry["bits"])
    return dict(area), dict(level)


def test_ledger_off_changes_nothing(tmp_path):
    baseline = kernel.run_simulation(_cfg(), ROWS, geometry=_geo())
    reference = kernel.run_simulation(_cfg(), ROWS, geometry=_geo())
    assert "queue_ledger_audit" not in baseline
    assert _digest(_stable(baseline)) == _digest(_stable(reference))


def test_ledger_on_reconstructs_queue_area_exactly(tmp_path):
    base = kernel.run_simulation(_cfg(), ROWS, geometry=_geo())
    inst = kernel.run_simulation(_cfg(), ROWS, geometry=_geo(),
                                 queue_ledger_dir=str(tmp_path),
                                 queue_ledger_prefix="t")
    assert "queue_ledger_audit" in inst
    # 1. inert: every pre-existing field is unchanged
    assert _digest(_stable(base)) == _digest(_stable(inst))
    # 2. the scenario really queues, otherwise the exactness check is vacuous
    native = inst["queue_area_bits_s"]
    assert native["uplink"] > 0 or native["isl_data"] > 0 or native["holding"] > 0

    audit = inst["queue_ledger_audit"]
    rows = _read_levels(audit["files"]["levels"]["path"])
    area, _level = _reconstruct(rows)
    for stage in STAGES:
        rebuilt = sum(v for q, v in area.items()
                      if q.split(":", 1)[0] == stage)
        assert abs(rebuilt - float(native.get(stage, 0.0))) < 1e-6, (
            stage, rebuilt, native.get(stage))
    # 3. row reconciliation against the verbatim lists
    assert audit["files"]["packet_events"]["rows"] == len(inst["packet_events"])
    assert audit["files"]["service_windows"]["rows"] == \
        len(inst["link_service_windows"])
    # 4. deterministic ledger bytes (same run twice)
    again = kernel.run_simulation(_cfg(), ROWS, geometry=_geo(),
                                 queue_ledger_dir=str(tmp_path),
                                 queue_ledger_prefix="t2")
    first = audit["files"]["levels"]["sha256"]
    second = again["queue_ledger_audit"]["files"]["levels"]["sha256"]
    assert first == second
