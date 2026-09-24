from pathlib import Path


RUNNER = (Path(__file__).resolve().parents[2]
          / "scripts" / "remote" / "run-remote.sh")


def _heredoc_body(script: str, assignment: str) -> str:
    marker = f"{assignment}=$(cat <<EOF\n"
    start = script.index(marker) + len(marker)
    end = script.index("\nEOF\n)", start)
    return script[start:end]


def _active_lines(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_remote_prepare_and_fail_use_the_activated_formal_environment():
    remote_command = _active_lines(_heredoc_body(
        RUNNER.read_text(encoding="utf-8"), "remote_command"))

    strict_shell = remote_command.index("set -euo pipefail")
    activation = remote_command.index("$REMOTE_ENV_ACTIVATE")
    prepare = remote_command.index(
        "$q_python scripts/remote/remote_job.py $q_prepare_args")
    fail = remote_command.index(
        "$q_python scripts/remote/remote_job.py $q_fail_args || true")

    assert remote_command.count("$REMOTE_ENV_ACTIVATE") == 1
    assert strict_shell < activation < prepare < fail


def test_tmux_child_uses_the_activated_formal_environment():
    tmux_script = _active_lines(_heredoc_body(
        RUNNER.read_text(encoding="utf-8"), "tmux_script"))

    strict_shell = tmux_script.index("set -euo pipefail")
    activation = tmux_script.index("$REMOTE_ENV_ACTIVATE")
    run = tmux_script.index(
        "$q_python scripts/remote/remote_job.py $q_runner_args")

    assert tmux_script.count("$REMOTE_ENV_ACTIVATE") == 1
    assert strict_shell < activation < run


def test_formal_runner_help_describes_runtime_specific_execution():
    script = RUNNER.read_text(encoding="utf-8")

    assert "It always executes the deployed\nCODE/run.py" not in script
    assert "legacy_gateway -> deployed CODE/run.py" in script
    assert "leo_sim_v2 -> python -m CODE.leo_sim run" in script
