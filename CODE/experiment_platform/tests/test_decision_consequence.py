"""The synthetic self-test shipped with decision_consequence must pass."""
from __future__ import annotations

from CODE.experiment_platform.decision_consequence import self_test


def test_self_test_passes():
    assert self_test() == 0
