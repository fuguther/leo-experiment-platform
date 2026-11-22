"""Freeze the remaining domain-to-platform reverse imports while they are paid down.

`experiment_platform` is an application/experiment layer over `leo_sim`.  The
current code still has a small allowlist of legacy reverse imports.  New
imports are rejected so architecture cleanup cannot silently regress.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LEO_SIM = ROOT / "CODE" / "leo_sim"

# Path -> exact imported experiment-platform modules. This is debt, not a
# target architecture. Removing an entry is welcome; adding one requires an
# explicit architecture review.
ALLOWED_REVERSE_IMPORTS = {
    "CODE/leo_sim/__main__.py": {
        "CODE.experiment_platform.authorize_experiment",
    },
    "CODE/leo_sim/matrix.py": {
        "CODE.experiment_platform.primary_metrics",
    },
    "CODE/leo_sim/pullback.py": {
        "CODE.experiment_platform.v2_analysis",
    },
    "CODE/leo_sim/scene_check.py": {
        "CODE.experiment_platform.v2_analysis",
    },
}


def _reverse_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("CODE.experiment_platform"):
                if node.module == "CODE.experiment_platform":
                    found.update(
                        f"{node.module}.{alias.name}" for alias in node.names
                        if alias.name != "*")
                else:
                    found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names
                if alias.name.startswith("CODE.experiment_platform")
            )
    return found


def test_leo_sim_does_not_add_platform_reverse_imports():
    actual = defaultdict(set)
    for path in sorted(LEO_SIM.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in rel:
            continue
        imports = _reverse_imports(path)
        if imports:
            actual[rel] = imports

    assert dict(actual) == ALLOWED_REVERSE_IMPORTS
