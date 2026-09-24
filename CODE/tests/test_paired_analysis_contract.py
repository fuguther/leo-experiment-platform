"""CI bridge for canonical analysis and claim tests without double collection.

The hosted workflow targets ``CODE/tests`` and therefore does not discover the
canonical tests under ANALYSIS and PAPER.  This bridge inspects the tests that
pytest actually collected: unless both canonical source modules are already
present it runs only the missing modules in one isolated subprocess; when both
source modules are in the current session it skips, so no module is duplicated.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CANONICAL_TESTS = (
    "ANALYSIS/tests/test_paired_analysis.py",
    "PAPER/tests/test_eligible_claims.py",
)


def _missing_canonical_tests(collected_item_paths: Iterable[Path]) -> tuple[str, ...]:
    collected_paths = {path.resolve() for path in collected_item_paths}
    return tuple(
        relative
        for relative in CANONICAL_TESTS
        if (ROOT / relative).resolve() not in collected_paths
    )


@pytest.mark.parametrize(
    ("collected", "expected"),
    (
        ((), CANONICAL_TESTS),
        ((ROOT / CANONICAL_TESTS[0],), (CANONICAL_TESTS[1],)),
        (
            tuple(ROOT / relative for relative in CANONICAL_TESTS),
            (),
        ),
    ),
    ids=("all-missing", "one-missing", "none-missing"),
)
def test_missing_canonical_tests_do_not_duplicate_collected_modules(
    collected: tuple[Path, ...],
    expected: tuple[str, ...],
) -> None:
    assert _missing_canonical_tests(collected) == expected


def test_canonical_analysis_and_claim_contracts_for_code_ci_scope(
    request: pytest.FixtureRequest,
) -> None:
    missing = _missing_canonical_tests(
        Path(item.path) for item in request.session.items
    )
    if not missing:
        pytest.skip("canonical ANALYSIS/PAPER source tests already collected")

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", *missing, "-q"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
