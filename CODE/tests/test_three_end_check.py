from __future__ import annotations

from pathlib import Path

from CODE.scripts.remote import release_protocol
from CODE.scripts import three_end_check as tec

COMMIT = "a" * 40
OTHER = "b" * 40

def test_compare_rows_detects_drift():
    same = tec.compare_rows({
        "local": {"commit": COMMIT}, "github": {"commit": COMMIT},
        "current": {"commit": COMMIT}})
    assert same["consistent"] is True
    assert same["missing"] == []
    drift = tec.compare_rows({
        "local": {"commit": COMMIT}, "github": {"commit": OTHER}})
    assert drift["consistent"] is False
    assert sorted(drift["distinct_commits"]) == [COMMIT, OTHER]


def test_compare_rows_never_treats_missing_endpoint_as_consistent():
    missing_github = tec.compare_rows({
        "local": {"commit": COMMIT},
        "github": {"commit": None, "error": "offline"},
        "current": {"commit": COMMIT},
    })
    missing_current = tec.compare_rows({
        "local": {"commit": COMMIT},
        "github": {"commit": COMMIT},
        "current": {"commit": None},
    })

    assert missing_github["consistent"] is False
    assert missing_github["missing"] == ["github"]
    assert missing_current["consistent"] is False
    assert missing_current["missing"] == ["current"]


def test_read_current_reports_missing(tmp_path):
    assert tec.read_current(tmp_path / "absent.json")["commit"] is None


def test_read_current_uses_verified_release_not_pointer_bytes_alone(monkeypatch):
    current = Path("/tmp/vm/CURRENT.json")
    monkeypatch.setattr(
        release_protocol,
        "verify_current_release",
        lambda root: {
            "status": "verified",
            "source_git_commit": COMMIT,
            "release_id": COMMIT + "-" + "c" * 64,
            "artifact_sha256": "f" * 64,
        },
    )

    row = tec.read_current(current)

    assert row["commit"] == COMMIT
    assert row["release_id"] == COMMIT + "-" + "c" * 64
