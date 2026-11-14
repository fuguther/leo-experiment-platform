"""External inference-only policy plugin: manifest, digests, fail-closed.

The platform must be able to run a research algorithm WITHOUT implementing it
inside leo_sim/.  This test pins the generic seam: a declared plugin is loaded
only when its manifest binds the exact module and checkpoint bytes, and only
when the object is inference-only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from CODE.leo_sim import policy_plugin

POLICY_SRC = '''
class ExamplePolicy:
    inference_only = True
    name = "example"

    def act(self, snapshot):
        return sorted(snapshot.legal_directions)[0]

    def counters(self):
        return {"calls": 1}


class Learner:
    def act(self, snapshot):
        return "N"

    def train_step(self, *a, **k):
        return 0.0
'''


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp: Path, *, entry_class="ExamplePolicy", digest=None,
              extra=None) -> Path:
    module = tmp / "policy.py"
    module.write_text(POLICY_SRC, encoding="utf-8")
    manifest = {
        "schema": "leo-policy-plugin/v1",
        "name": "example",
        "entry_module": "policy.py",
        "entry_class": entry_class,
        "module_sha256": digest or _sha(module),
        "info_rights": ["legal_directions"],
    }
    if extra:
        manifest.update(extra)
    path = tmp / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_a_verified_plugin_loads_and_is_inference_only(tmp_path):
    policy = policy_plugin.load_policy_plugin(_manifest(tmp_path))
    assert policy.name == "example"
    assert policy.counters() == {"calls": 1}


def test_a_module_digest_mismatch_is_refused(tmp_path):
    with pytest.raises(policy_plugin.PolicyPluginError, match="module bytes"):
        policy_plugin.load_policy_plugin(_manifest(tmp_path, digest="0" * 64))


def test_a_checkpoint_digest_mismatch_is_refused(tmp_path):
    (tmp_path / "w.h5").write_bytes(b"weights")
    manifest = _manifest(tmp_path, extra={"checkpoint_sha256": {"w.h5": "1" * 64}})
    with pytest.raises(policy_plugin.PolicyPluginError, match="checkpoint"):
        policy_plugin.load_policy_plugin(manifest)


def test_a_matching_checkpoint_passes(tmp_path):
    (tmp_path / "w.h5").write_bytes(b"weights")
    manifest = _manifest(
        tmp_path, extra={"checkpoint_sha256": {"w.h5": _sha(tmp_path / "w.h5")}})
    assert policy_plugin.load_policy_plugin(manifest).name == "example"


def test_a_training_capable_object_is_refused(tmp_path):
    with pytest.raises(Exception, match="train_step"):
        policy_plugin.load_policy_plugin(_manifest(tmp_path, entry_class="Learner"))


def test_path_traversal_in_entry_module_is_refused(tmp_path):
    manifest = _manifest(tmp_path, extra={"entry_module": "../escape.py"})
    with pytest.raises(policy_plugin.PolicyPluginError, match="manifest directory"):
        policy_plugin.load_policy_plugin(manifest)


def test_unknown_manifest_fields_are_refused(tmp_path):
    manifest = _manifest(tmp_path, extra={"surprise": 1})
    with pytest.raises(policy_plugin.PolicyPluginError, match="unknown"):
        policy_plugin.load_policy_plugin(manifest)


def test_missing_manifest_is_refused(tmp_path):
    with pytest.raises(policy_plugin.PolicyPluginError, match="not found"):
        policy_plugin.load_policy_plugin(tmp_path / "nope.json")
