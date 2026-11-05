from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from CODE.experiment_platform import code_pointer
from CODE.scripts import three_end_check as tec

COMMIT = "a" * 40
OTHER = "b" * 40


def test_pointer_roundtrip_and_tamper(tmp_path):
    out = tmp_path / "CURRENT.json"
    code_pointer.write_current(out, {
        "schema": code_pointer.SCHEMA, "commit": COMMIT, "branch": "main",
        "dirty": False, "release_id": "r1", "source": "git",
        "updated_at": "2026-10-24T00:00:00+00:00"})
    doc = code_pointer.read_current(out)
    assert doc["commit"] == COMMIT
    assert len(doc["pointer_sha256"]) == 64
    doc["commit"] = OTHER
    out.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(code_pointer.PointerError, match="pointer_sha256"):
        code_pointer.read_current(out)


def test_build_current_requires_verified_git(tmp_path, monkeypatch):
    monkeypatch.setattr(
        code_pointer.artifact_identity, "git_identity",
        lambda root: {"available": False, "reason": "no git"})
    with pytest.raises(code_pointer.PointerError, match="no verified commit"):
        code_pointer.build_current(tmp_path)


def test_build_from_release(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    (release / ".deployment_commit").write_text(COMMIT + "\n", encoding="ascii")
    (release / ".release-envelope.json").write_text(json.dumps({
        "schema": "leo-immutable-release/v1",
        "source_git_commit": COMMIT,
        "source_git_branch": "main",
        "source_git_dirty": False,
        "release_id": "r1",
    }), encoding="utf-8")
    doc = code_pointer.build_from_release(release)
    assert doc["commit"] == COMMIT
    assert doc["release_id"] == "r1"
    assert doc["source"] == "immutable_release"


def test_compare_rows_detects_drift():
    same = tec.compare_rows({
        "local": {"commit": COMMIT}, "github": {"commit": COMMIT},
        "release": {"commit": None}, "current": {"commit": COMMIT}})
    assert same["consistent"] is True
    assert same["missing"] == ["release"]
    drift = tec.compare_rows({
        "local": {"commit": COMMIT}, "github": {"commit": OTHER}})
    assert drift["consistent"] is False
    assert sorted(drift["distinct_commits"]) == [COMMIT, OTHER]


def test_read_latest_release_by_mtime(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    for path, commit in ((old, COMMIT), (new, OTHER)):
        path.mkdir()
        (path / ".release-envelope.json").write_text(json.dumps({
            "source_git_commit": commit, "release_id": path.name}),
            encoding="utf-8")
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    assert tec.read_latest_release(tmp_path)["commit"] == OTHER


def test_read_current_reports_missing(tmp_path):
    assert tec.read_current(tmp_path / "absent.json")["commit"] is None


def test_pointer_without_hash_is_rejected(tmp_path):
    out = tmp_path / "CURRENT.json"
    doc = {"schema": code_pointer.SCHEMA, "commit": COMMIT, "dirty": False}
    out.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(code_pointer.PointerError, match="pointer_sha256 is missing"):
        code_pointer.read_current(out)
