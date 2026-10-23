from __future__ import annotations

import json
from pathlib import Path

import pytest

from CODE.experiment_platform import launch_manifest as lm


def test_build_and_verify(tmp_path):
    cfg = tmp_path / "scene.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    out = tmp_path / "run.launch-manifest.json"
    manifest = lm.build_launch_manifest(
        root=Path.cwd(), out=out, run_id="r1", task="network_alignment",
        argv=["t1_tasks", "--task", "network_alignment"],
        inputs=[("config", cfg)], arms=["stale", "now"], deadline_s=1.5)
    assert manifest["schema"] == lm.SCHEMA
    assert manifest["run_id"] == "r1"
    assert manifest["deadline_s"] == 1.5
    assert manifest["inputs"][0]["sha256"] == lm.sha256_file(cfg)
    assert manifest["argv"][0] == "t1_tasks"
    verified = lm.verify_launch_manifest(out)
    assert verified["manifest_sha256"] == manifest["manifest_sha256"]


def test_missing_input_is_refused(tmp_path):
    out = tmp_path / "m.json"
    with pytest.raises(lm.ManifestError, match="missing"):
        lm.build_launch_manifest(
            root=Path.cwd(), out=out, run_id="r", task="network_alignment",
            inputs=[("config", tmp_path / "absent.yaml")])


def test_require_pinned_rejects_unpinned_code(tmp_path, monkeypatch):
    out = tmp_path / "m.json"
    monkeypatch.setattr(
        lm.artifact_identity, "current_identity",
        lambda root: {"sources": {"combined_sha256": "a" * 64},
                      "git": {"available": False, "reason": "no git"}})
    with pytest.raises(lm.ManifestError, match="code identity unavailable"):
        lm.build_launch_manifest(
            root=tmp_path, out=out, run_id="r", task="network_alignment",
            require_pinned=True)


def test_tamper_is_detected(tmp_path):
    cfg = tmp_path / "scene.yaml"
    cfg.write_text("a: 1\n", encoding="utf-8")
    out = tmp_path / "m.json"
    lm.build_launch_manifest(
        root=Path.cwd(), out=out, run_id="r", task="network_alignment",
        inputs=[("config", cfg)])
    data = json.loads(out.read_text(encoding="utf-8"))
    data["run_id"] = "changed"
    out.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(lm.ManifestError, match="manifest_sha256"):
        lm.verify_launch_manifest(out)


def test_cli_build_and_verify(tmp_path):
    cfg = tmp_path / "s.yaml"
    cfg.write_text("x: 2\n", encoding="utf-8")
    out = tmp_path / "m.json"
    assert lm.main(["--out", str(out), "--root", str(Path.cwd()),
                    "--run-id", "cli", "--input", f"config={cfg}"]) == 0
    assert lm.main(["--out", str(out), "--verify"]) == 0
