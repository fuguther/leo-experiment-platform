from pathlib import Path


RUN_WRAPPER = Path(__file__).resolve().parents[1] / "run-release-remote.sh"


def test_t1_runner_defaults_to_the_versioned_isolated_runtime_without_user_paths():
    script = RUN_WRAPPER.read_text(encoding="utf-8")

    assert 'REMOTE_RUN_PYTHON="${T1_REMOTE_RUN_PYTHON:-/data/' not in script
    assert (
        'REMOTE_RUN_PYTHON="${T1_REMOTE_RUN_PYTHON:-'
        '$REMOTE_ROOT/envs/t1-linux-aarch64-py311-pkgset-v1/bin/python}"'
    ) in script
