from pathlib import Path


RUN_WRAPPER = Path(__file__).resolve().parents[1] / "run-release-remote.sh"
PUBLISH_WRAPPER = Path(__file__).resolve().parents[1] / "publish-release-remote.sh"
PUSH_WRAPPER = Path(__file__).resolve().parents[1] / "push-remote.sh"
PULL_WRAPPER = Path(__file__).resolve().parents[1] / "pull-release-results-remote.sh"


def test_t1_runner_defaults_to_the_versioned_isolated_runtime_without_user_paths():
    script = RUN_WRAPPER.read_text(encoding="utf-8")

    assert 'REMOTE_RUN_PYTHON="${T1_REMOTE_RUN_PYTHON:-/data/' not in script
    assert (
        'REMOTE_RUN_PYTHON="${T1_REMOTE_RUN_PYTHON:-'
        '$REMOTE_ROOT/envs/t1-linux-aarch64-py311-pkgset-v1/bin/python}"'
    ) in script


def test_t1_runner_defaults_to_current_release_and_requires_explicit_history_override():
    script = RUN_WRAPPER.read_text(encoding="utf-8")

    assert '--allow-historical-release' in script
    assert 't1-current-release/v2' in script
    assert '"$REMOTE_ROOT/CURRENT.json"' in script
    assert 'requested release is not CURRENT.json' in script


def test_publish_defaults_to_clean_main_and_promotes_only_verified_current_release():
    script = PUBLISH_WRAPPER.read_text(encoding="utf-8")

    assert '--required-local-branch main' in script
    assert '--required-remote-ref refs/heads/main' in script
    assert 'verify-remote-ref' in script
    assert 'set-current --release-id "$release_id"' in script
    assert '--development-release' in script
    assert 'publication_channel=' in script


def test_legacy_deploy_wrapper_uses_the_same_canonical_source_gate():
    script = PUSH_WRAPPER.read_text(encoding="utf-8")

    assert 'verify-source' in script
    assert '--required-local-branch main' in script
    assert '--required-remote-ref refs/heads/main' in script
    assert script.count('verify_canonical_source') >= 3


def test_pullback_defaults_to_installed_release_verification():
    script = PULL_WRAPPER.read_text(encoding="utf-8")

    assert '--release-dir "$REMOTE_ROOT/releases/$release_id"' in script
    assert '--allow-legacy-self-consistent-pull' in script
    assert 'WARNING: legacy self-consistent receipt verification selected explicitly' in script
