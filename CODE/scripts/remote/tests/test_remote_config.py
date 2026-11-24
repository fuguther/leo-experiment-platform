from __future__ import annotations

import os
import shutil
import subprocess
import textwrap
from pathlib import Path

COMMON = Path(__file__).resolve().parents[1] / "common.sh"
STATUS_REMOTE = Path(__file__).resolve().parents[1] / "status-remote.sh"

VALID_CONFIG = textwrap.dedent(
    """\
    REMOTE_HOST_ALIAS="test-vm"
    REMOTE_WORKSPACE_DIR="/data/论文/leo-direct-sim"
    REMOTE_CODE_DIR="/data/论文/leo-direct-sim/CODE"
    REMOTE_ENV_ACTIVATE="true"
    """
)


def _run_with_config(
    common: Path,
    *,
    home: Path,
    xdg_config_home: Path | None = None,
    remote_config: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("REMOTE_CONFIG_FILE", None)
    env.pop("XDG_CONFIG_HOME", None)
    env["HOME"] = str(home)
    if xdg_config_home is not None:
        env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    if remote_config is not None:
        env["REMOTE_CONFIG_FILE"] = str(remote_config)
    script = 'source "$1"; load_remote_config; printf "%s\\n" "$CONFIG_FILE"'
    return subprocess.run(
        ["bash", "-c", script, "remote-config-test", str(common)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_remote_config_defaults_to_xdg_user_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    config = home / ".config" / "leo-experiment-platform" / "remote.env"
    config.parent.mkdir(parents=True)
    config.write_text(VALID_CONFIG, encoding="utf-8")

    result = _run_with_config(COMMON, home=home)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(config)


def test_remote_config_honors_xdg_config_home_and_explicit_override(tmp_path: Path) -> None:
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    config = xdg / "leo-experiment-platform" / "remote.env"
    config.parent.mkdir(parents=True)
    config.write_text(VALID_CONFIG, encoding="utf-8")

    xdg_result = _run_with_config(COMMON, home=home, xdg_config_home=xdg)
    assert xdg_result.returncode == 0, xdg_result.stderr
    assert xdg_result.stdout.strip() == str(config)

    override = tmp_path / "override.env"
    override.write_text(VALID_CONFIG, encoding="utf-8")
    override_result = _run_with_config(
        COMMON,
        home=home,
        xdg_config_home=xdg,
        remote_config=override,
    )
    assert override_result.returncode == 0, override_result.stderr
    assert override_result.stdout.strip() == str(override)


def test_repo_local_remote_env_is_not_an_implicit_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    common = repo / "CODE" / "scripts" / "remote" / "common.sh"
    common.parent.mkdir(parents=True)
    shutil.copy2(COMMON, common)
    repo_config = common.parent / "remote.env"
    repo_config.write_text(VALID_CONFIG, encoding="utf-8")

    home = tmp_path / "home"
    expected = home / ".config" / "leo-experiment-platform" / "remote.env"
    result = _run_with_config(common, home=home)

    assert result.returncode != 0
    assert f"missing config: {expected}" in result.stderr
    assert f"missing config: {repo_config}" not in result.stderr


def test_remote_config_requires_home_or_xdg_when_no_override(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("HOME", None)
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("REMOTE_CONFIG_FILE", None)
    script = 'source "$1"; load_remote_config'
    result = subprocess.run(
        ["bash", "-c", script, "remote-config-test", str(COMMON)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "HOME or XDG_CONFIG_HOME is required" in result.stderr


def test_status_help_does_not_require_home_xdg_or_remote_config() -> None:
    env = os.environ.copy()
    env.pop("HOME", None)
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("REMOTE_CONFIG_FILE", None)
    result = subprocess.run(
        ["bash", str(STATUS_REMOTE), "--help"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout


def test_relative_xdg_config_home_is_rejected(tmp_path: Path) -> None:
    result = _run_with_config(
        COMMON,
        home=tmp_path / "home",
        xdg_config_home=Path("relative/config"),
    )

    assert result.returncode != 0
    assert "XDG_CONFIG_HOME must be an absolute path" in result.stderr
