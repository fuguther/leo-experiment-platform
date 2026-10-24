"""Unit tests for the zero-rate egress guard used by the MCS abstraction."""
import math

import pytest

from CODE.leo_sim.kernel import Kernel


class Stub:
    topo = {0: {"N": 1, "S": 2, "E": 3, "U": 99}}
    rates = {1: 1e9, 2: 0.0, 3: -5.0, 99: None}

    def _link_rate(self, kind, t, sat, peer=None, ep=None):
        if peer == 99:
            raise RuntimeError("lookup exploded")
        return self.rates[peer]


def split(cands):
    return Kernel._split_zero_rate_directions(Stub(), 0, 0.0, cands)


def test_positive_rate_kept_and_zero_or_negative_dropped():
    kept, dropped, errors = split(["N", "S", "E"])
    assert kept == ["N"]
    assert [d["direction"] for d in dropped] == ["S", "E"]
    assert dropped[0]["rate_bps"] == 0.0
    assert dropped[1]["rate_bps"] == -5.0
    assert errors == []


def test_lookup_error_keeps_candidate_and_is_reported():
    kept, dropped, errors = split(["N", "U"])
    assert kept == ["N", "U"]
    assert dropped == []
    assert errors and errors[0]["direction"] == "U"
    assert "lookup exploded" in errors[0]["error"]


def test_unmapped_direction_is_left_to_the_scorer():
    kept, dropped, errors = split(["X"])
    assert kept == ["X"] and dropped == [] and errors == []


def test_none_rate_is_dropped_not_raised():
    kept, dropped, errors = split(["N"])
    assert kept == ["N"]
    kept2, dropped2, errors2 = Kernel._split_zero_rate_directions(
        type("S2", (Stub,), {"rates": {1: None}})(), 0, 0.0, ["N"])
    assert kept2 == []
    assert dropped2 == [{"direction": "N", "rate_bps": None}]
