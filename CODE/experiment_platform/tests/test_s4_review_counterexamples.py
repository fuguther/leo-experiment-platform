"""Third-round independent review (S4-d): excluded reasons must be exhaustive.

The schema filter used to run BEFORE the status check, so a cell whose result
was missing or unreadable (schema = None) was silently dropped from
statistics.excluded -- the block disappeared without any recorded reason.
"""
from __future__ import annotations

from CODE.experiment_platform import t1_suite


def test_an_unreadable_cell_is_listed_as_an_excluded_block(tmp_path):
    rows = [{"cell_id": "gone", "schema": None, "status": "ok",
             "invalid_reason": "result file is missing",
             "predicate_passed": None}]
    summary = t1_suite._statistics_summary(tmp_path, rows)
    excluded = summary["excluded"]
    assert [e["unit"] for e in excluded] == ["gone"], excluded
    assert "result file is missing" in excluded[0]["reason"]
    assert summary["blocks"] == 0


def test_a_different_cell_kind_is_never_reported_as_excluded(tmp_path):
    """A benchmark cell is not a failed block: it was never a block candidate."""
    rows = [{"cell_id": "bench", "schema": "benchmark-decision/v1",
             "status": "ok", "predicate_passed": True}]
    summary = t1_suite._statistics_summary(tmp_path, rows)
    assert summary["excluded"] == []


def test_a_failed_predicate_is_still_listed(tmp_path):
    rows = [{"cell_id": "poison", "schema": "time-alignment-compare/v1",
             "status": "ok", "predicate_passed": False}]
    summary = t1_suite._statistics_summary(tmp_path, rows)
    assert [e["unit"] for e in summary["excluded"]] == ["poison"]
    assert summary["excluded"][0]["reason"] == "behaviour predicate failed"
