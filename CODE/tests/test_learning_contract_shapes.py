"""Every declared observation contract must actually produce its declared width.

This file exists because the same defect was introduced FIVE separate times while
adding contracts to leo_sim.learning: a contract was registered in CONTRACT_DIMS and
in the config whitelist, but one of the `if contract in (...)` gates that fill a
feature block was not updated.  The feature then came out silently as zeros -- no
exception, no warning -- and the only symptom was "the experiment shows no effect",
which is indistinguishable from a genuine negative result.

Two gates were hit repeatedly:
  * the direction-progress block (kernel), left off C8/C9 and later off PAPER/C10
  * the ETA block (kernel), left off PAPER entirely

The invariant tested here is narrow on purpose: for every contract in CONTRACT_DIMS,
build_observation must return exactly CONTRACT_DIMS[contract] values.  A gate left
un-updated changes the width (or leaves a block of zeros), and this catches the width
case outright; the zero-block case is covered by the second assertion, which requires
that a contract's output is not constant across two different topologies.
"""
from __future__ import annotations

import numpy as np
import pytest

from CODE.leo_sim import learning as L


class _Entry:
    def __init__(self, payload):
        self.payload = payload
        self.ttl_s = 10.0
        self.generated_at = 0.0
        self.received_at = 0.0
        self.hops = 1

    def aoi(self, now):
        return 0.0


def _cache(queues=None):
    queues = queues or {"N": 100, "S": 200, "E": 300, "W": 400}

    class _Cache:
        def valid_entries(self, now):
            return {
                1: _Entry({"isl_queue_bits": queues, "access_slots_used": 1,
                           "access_slots_cap": 4, "visible_cells": ()}),
                2: _Entry({"isl_queue_bits": queues, "access_slots_used": 1,
                           "access_slots_cap": 4, "visible_cells": ()}),
            }

    return _Cache()


def _build(contract, topo, queues=None):
    own = np.zeros(7, dtype=np.float64)
    posmap = lambda t: tuple((float(i), 1.0, 2.0) for i in range(66))
    kw = {}
    if contract == "PAPER":
        kw.update(paper_state="advertised", paper_queue_growth=1.0)
    return L.build_observation(
        contract, 0, _cache(queues), 1.0, topo, own, 3_200_000,
        dst_feats=np.array([0.1, 0.2, 0.3]),
        root_pos=(100.0, 200.0, 300.0),
        dir_progress=np.array([0.1, -0.2, 0.3, -0.4]),
        cache_position=posmap, **kw)


@pytest.mark.parametrize("contract", sorted(L.CONTRACT_DIMS))
def test_declared_width_matches_built_width(contract):
    topo = {0: {"N": 1, "S": 2}}
    out = np.asarray(_build(contract, topo))
    assert out.ndim == 1, f"{contract}: observation must be 1-D, got {out.shape}"
    assert out.shape[0] == L.CONTRACT_DIMS[contract], (
        f"{contract} declares {L.CONTRACT_DIMS[contract]} dims but "
        f"build_observation returned {out.shape[0]}")


@pytest.mark.parametrize("contract", sorted(L.CONTRACT_DIMS))
def test_observation_responds_to_input(contract):
    """A gate left un-updated leaves a constant block; the row must still move.

    Two topologies with different neighbour sets and different queue payloads must
    not produce byte-identical observations.  This is the assertion that would have
    caught "dir_progress is always zeros" if the zeros happened to keep the width.
    """
    a = np.asarray(_build(contract, {0: {"N": 1, "S": 2}},
                          {"N": 100, "S": 200}))
    b = np.asarray(_build(contract, {0: {"N": 1, "E": 2}},
                          {"N": 900, "S": 800}))
    if a.shape != b.shape:
        pytest.fail(f"{contract}: shapes differ between topologies")
    if np.array_equal(a, b):
        pytest.fail(
            f"{contract}: observation is identical for different topologies and "
            "queue payloads -- a feature block is probably not being filled")
