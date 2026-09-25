"""The analyzer must accept the accounting the compiler actually emits.

The compiler adds one_change_policy_check to design_accounting when a request
declares a design block.  The analyzer recomputes the counts from the cells and
compared the WHOLE mapping for equality, so every request with a design block
compiled cleanly and was then refused at analysis time -- the deferral the
platform recorded as S-2, found again by the VM acceptance round on
2026-09-25 (the analyzer cannot recompute the leaf-path check because the
analysis request carries only config digests).
"""
from __future__ import annotations

import copy

import pytest

from CODE.experiment_platform import v2_analysis

CELLS = [
    {"run_id": "EXP-X-control-s7", "config_sha256": "a" * 64},
    {"run_id": "EXP-X-treatment-s7", "config_sha256": "b" * 64},
]
BASE = {
    "schema": "leo-sim-matrix-design-accounting/v1",
    "planned_cells": 2,
    "unique_resolved_configurations": 2,
    "exact_reexecution_cells": 0,
    "exact_reexecution_groups": [],
    "independent_condition_rule": (
        "one independent condition per unique resolved config SHA256"),
}
CHECK = {
    "schema": "leo-sim-matrix-design-check/v1",
    "one_change_policy": "strict",
    "declared_factor_changed": ["routing.policy"],
    "observed_changed_paths_by_contrast": {
        "treatment_minus_control": ["routing.policy"]},
    "single_factor_verified": True,
}


def test_the_compiler_extension_is_accepted_and_the_base_counts_still_checked():
    request = {"design_accounting": {**BASE, "one_change_policy_check": CHECK}}
    assert v2_analysis._validated_design_accounting(request, CELLS) == BASE

    # the extension may be absent (historical sealed matrices)
    assert v2_analysis._validated_design_accounting(
        {"design_accounting": BASE}, CELLS) == BASE
    assert v2_analysis._validated_design_accounting({}, CELLS) == BASE


def test_a_recomputable_field_that_disagrees_is_still_refused():
    for key, wrong in (("planned_cells", 3),
                       ("unique_resolved_configurations", 1),
                       ("independent_condition_rule", "something else")):
        broken = copy.deepcopy({**BASE, "one_change_policy_check": CHECK})
        broken[key] = wrong
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="does not match matrix cells"):
            v2_analysis._validated_design_accounting(
                {"design_accounting": broken}, CELLS)


def test_an_unknown_extension_or_a_malformed_check_is_refused():
    with pytest.raises(v2_analysis.V2AnalysisError, match="unknown fields"):
        v2_analysis._validated_design_accounting(
            {"design_accounting": {**BASE, "something_new": 1}}, CELLS)
    for broken_check in (
            {**CHECK, "schema": "wrong/v9"},
            {**CHECK, "one_change_policy": "whatever"},
            {**CHECK, "single_factor_verified": "yes"},
            {**CHECK, "observed_changed_paths_by_contrast": ["not", "a", "map"]},
            "not-a-mapping"):
        with pytest.raises(v2_analysis.V2AnalysisError,
                           match="one-change policy check is malformed"):
            v2_analysis._validated_design_accounting(
                {"design_accounting": {**BASE,
                                       "one_change_policy_check": broken_check}},
                CELLS)
