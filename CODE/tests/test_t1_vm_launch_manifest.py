"""Regression: the VM launch manifest must not make a clean tree look dirty.

The T1 sync step used to write .t1-launch.json *into* the workspace and only
then ask git for "status --short".  A shell redirection target is created
before the command runs, so the recipe always saw its own untracked file and
recorded dirty=true on a perfectly clean commit.  That false positive ended up
inside the pulled-back evidence identity of runs c4dd85f and a70d65c
(identity.git.dirty=true while status_short was empty).

The manifest is now staged outside the checkout.  These tests pin the mechanism
and the invariant so a revert cannot silently reintroduce it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "CODE" / "scripts" / "remote" / "t1-vm.sh"

# the exact dirty probe the runner manifest step performs
_DIRTY_PROBE = (
    "import subprocess,sys\n"
    "print(bool(subprocess.run(['git','-C',sys.argv[1],'status','--short'],\n"
    "    capture_output=True,text=True).stdout.strip()))\n"
)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.invalid")
    _git(path, "config", "user.name", "t")
    (path / "tracked.txt").write_text("x\n")
    _git(path, "add", "tracked.txt")
    _git(path, "commit", "-q", "-m", "init")


def _probe_with_redirect_target(target: Path, repo: Path) -> str:
    """Reproduce the runner recipe: redirect to *target*, probe *repo*."""
    with open(target, "w") as handle:
        subprocess.run(
            [sys.executable, "-c", _DIRTY_PROBE, str(repo)],
            stdout=handle,
            check=True,
        )
    return target.read_text().strip()


def test_manifest_staged_outside_the_repo_reports_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    staging = tmp_path / "staging"
    staging.mkdir()

    assert _probe_with_redirect_target(staging / ".t1-launch.json", repo) == "False"
    assert _git(repo, "status", "--short") == ""


def test_manifest_written_into_the_repo_is_a_false_positive(tmp_path: Path) -> None:
    """The old recipe, kept as the failing counterexample it always was."""
    repo = tmp_path / "repo"
    _init_repo(repo)

    assert _probe_with_redirect_target(repo / ".t1-launch.json", repo) == "True"
    # ... and the only thing that ever made it dirty was the probe own output
    (repo / ".t1-launch.json").unlink()
    assert _git(repo, "status", "--short") == ""


def test_runner_no_longer_stages_the_manifest_inside_the_workspace() -> None:
    text = RUNNER.read_text()
    assert '> "$staging/.t1-launch.json"' in text
    assert '> "$LOCAL_WORKSPACE/.t1-launch.json"' not in text
    assert '-C "$staging" .t1-launch.json' in text
    assert ".t1-launch.json" in (REPO_ROOT / ".gitignore").read_text()
