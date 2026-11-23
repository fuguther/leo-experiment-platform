"""T1-COMPLETE R5: the deadline is frozen on development data.

A comparison must LOAD the development-frozen D; deriving D from the branch it
is comparing would let the answer set its own yardstick.  A packet that cannot
be observed for a full D is administratively censored, never counted as a
timeout failure.
"""
from __future__ import annotations

import json

import pytest

from CODE.experiment_platform import time_alignment_compare as cmp


def test_a_frozen_deadline_is_loaded_with_its_identity(tmp_path):
    frozen = tmp_path / "d.json"
    frozen.write_text(json.dumps({"deadline_s": 4.0, "source": "dev_p95",
                                  "weak": False, "frozen_at_sha": "abc"}))
    loaded = cmp.load_frozen_deadline(frozen)
    assert loaded["deadline_s"] == 4.0
    assert loaded["source"] == "frozen_development_file"
    assert loaded["file_sha256"]
    assert loaded["frozen_at_sha"] == "abc"


def test_a_missing_or_invalid_frozen_deadline_is_refused(tmp_path):
    with pytest.raises(cmp.CompareError):
        cmp.load_frozen_deadline(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"deadline_s": -1}))
    with pytest.raises(cmp.CompareError):
        cmp.load_frozen_deadline(bad)


def test_a_compare_run_may_not_derive_its_own_deadline(tmp_path):
    with pytest.raises(cmp.CompareError) as excinfo:
        cmp.compare_scenario("contention", 4, None, run_kind="compare")
    assert "may not derive D" in str(excinfo.value)


def test_two_different_branches_share_the_same_frozen_deadline(tmp_path):
    frozen = tmp_path / "d.json"
    frozen.write_text(json.dumps({"deadline_s": 3.5, "source": "dev_p95"}))
    loaded = cmp.load_frozen_deadline(frozen)
    first = cmp.compare_scenario("contention", 4, None,
                                 frozen_deadline=loaded, run_kind="compare")
    second = cmp.compare_scenario("reachability", 3, None,
                                  frozen_deadline=loaded, run_kind="compare")
    assert first["deadline"]["deadline_s"] == 3.5
    assert second["deadline"]["deadline_s"] == 3.5
    assert first["deadline"]["source"] == "frozen_development_file"
    assert second["deadline"]["file_sha256"] == first["deadline"][
        "file_sha256"]
    # and neither branch was allowed to move it
    for doc in (first, second):
        assert doc["deadline"]["run_kind"] == "compare"


def test_a_short_observation_window_is_censored_not_counted_as_failure(tmp_path):
    frozen = tmp_path / "d.json"
    frozen.write_text(json.dumps({"deadline_s": 1000.0}))
    doc = cmp.compare_scenario("contention", 4, None,
                               frozen_deadline=cmp.load_frozen_deadline(frozen),
                               run_kind="compare")
    assert doc["counts"]["censored"] == doc["counts"]["valid_pairs"]
    for item in doc["candidates"].values():
        if not item["valid"]:
            continue
        assert item["censored"] is True
        assert item["loss"] is None
        assert "observation window" in item["censor_reason"]
        assert item["observation_window_s"] < 1000.0


def test_a_normal_window_is_scored_with_the_frozen_deadline(tmp_path):
    frozen = tmp_path / "d.json"
    frozen.write_text(json.dumps({"deadline_s": 5.0}))
    doc = cmp.compare_scenario("contention", 4, None,
                               frozen_deadline=cmp.load_frozen_deadline(frozen),
                               run_kind="compare")
    assert doc["counts"]["censored"] == 0
    scored = [c for c in doc["candidates"].values() if c["valid"]]
    assert scored
    for item in scored:
        assert item["censored"] is False
        assert item["deadline_used_s"] == 5.0
        assert item["loss"] is not None


def test_freezing_writes_identity_and_refuses_to_overwrite(tmp_path):
    out = tmp_path / "frozen.json"
    report = {"deadline_s": 2.0, "source": "p95_times_multiplier",
              "weak": False}
    doc = cmp.write_frozen_deadline(out, report, {}, "dev")
    assert doc["schema"] == "t1-frozen-deadline/v1"
    assert doc["frozen_at_sha"]
    assert doc["identity"]["sources"]["combined_sha256"]
    with pytest.raises(cmp.CompareError):
        cmp.write_frozen_deadline(out, report, {}, "dev")


def test_the_artifact_records_which_deadline_file_was_used(tmp_path):
    frozen = tmp_path / "d.json"
    frozen.write_text(json.dumps({"deadline_s": 6.0}))
    doc = cmp.compare_scenario("reachability", 3, None,
                               frozen_deadline=cmp.load_frozen_deadline(frozen),
                               run_kind="confirm")
    assert doc["deadline"]["frozen_file"] == str(frozen)
    assert doc["deadline"]["file_sha256"]
    assert doc["deadline"]["run_kind"] == "confirm"
    assert doc["units"]["loss"].startswith("normalized loss")
