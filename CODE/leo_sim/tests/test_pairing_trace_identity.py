"""S-2: the compiler enforces the analyzer pairing contract.

v2_analysis requires paired arms to agree on trace identity.  The compiler
used to compare only trace_seed / phase / controlled_signature, so a
treatment overriding a trace-affecting knob (demand.offered_mbps) compiled
cleanly and then failed at analysis -- after both runs had been paid for.

Measured 2026-09-24 on THIS fixture common_config: routing.policy
hop/oracle/delay all give trace identity
610900101fd1f21ff53cc449a46f987b939e56d60017d061648e22b261ab4700, while
demand.offered_mbps=2.0 gives
5e51fa445ad887a217af4cbeddb1ad4d2cee2f59f00ad1740c72253faa699191.
(The earlier text quoted 3c8d3443/7d5b339d, which are the SMOKE profile's
identities -- the same conclusion, the wrong measurement.)
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def _fixture_request():
    """Reuse the canonical matrix request fixture."""
    sys.path.insert(0, str(ROOT/"CODE/leo_sim/tests"))
    from CODE.leo_sim.tests import test_matrix_contract as tmc
    return tmc._request()


def test_a_trace_affecting_treatment_is_rejected_at_compile_time():
    """The pairing contract runs in the compile path, not in schema validation."""
    from CODE.leo_sim import matrix
    request = copy.deepcopy(_fixture_request())
    request["arms"][1]["config_overrides"] = {
        "demand": {"offered_mbps": 2.0}}
    request["arms"][1]["intervention_paths"] = ["demand.offered_mbps"]
    with pytest.raises(matrix.MatrixError) as caught:
        matrix._resolve_cells(request, ROOT)
    assert "trace identity" in str(caught.value), str(caught.value)


def test_the_trace_neutral_fixture_still_compiles():
    from CODE.leo_sim import matrix
    matrix._resolve_cells(copy.deepcopy(_fixture_request()), ROOT)
