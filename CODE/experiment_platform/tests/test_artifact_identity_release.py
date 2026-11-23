import hashlib
import json

from CODE.experiment_platform.artifact_identity import git_identity


def _release_root(tmp_path):
    root = tmp_path / "immutable-release"
    root.mkdir()
    commit = "a" * 40
    witness = root / ".deployment_commit"
    witness.write_text(commit + "\n", encoding="ascii")
    witness_bytes = witness.read_bytes()
    manifest = {
        "schema": "leo-immutable-release-manifest/v1",
        "source_git_commit": commit,
        "source_git_dirty": False,
        "deployed_files": [{
            "path": ".deployment_commit",
            "size": len(witness_bytes),
            "sha256": hashlib.sha256(witness_bytes).hexdigest(),
        }],
    }
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    manifest["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    (root / ".release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    return root, commit


def test_immutable_release_identity_requires_and_verifies_both_witnesses(tmp_path):
    root, commit = _release_root(tmp_path)

    identity = git_identity(root)

    assert identity["available"] is True
    assert identity["source"] == "immutable_release"
    assert identity["commit"] == commit
    assert identity["dirty"] is False
    assert identity["branch"] is None
    assert identity["diff_sha256"] is None


def test_tampered_immutable_release_witness_does_not_claim_identity(tmp_path):
    root, _ = _release_root(tmp_path)
    (root / ".deployment_commit").write_text("b" * 40 + "\n",
                                             encoding="ascii")

    identity = git_identity(root)

    assert identity["available"] is False


def test_tampered_immutable_release_manifest_does_not_claim_identity(tmp_path):
    root, _ = _release_root(tmp_path)
    manifest_path = root / ".release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_git_dirty"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    identity = git_identity(root)

    assert identity["available"] is False
