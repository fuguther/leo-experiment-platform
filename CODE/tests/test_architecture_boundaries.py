"""Freeze the remaining domain-to-platform reverse imports while they are paid down.

`experiment_platform` is an application/experiment layer over `leo_sim`.  The
current code still has a small allowlist of legacy reverse imports.  New
imports are rejected so architecture cleanup cannot silently regress.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pytest


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


def _resolve_from_module(
    path: Path,
    root: Path,
    *,
    level: int,
    module: str | None,
) -> str | None:
    if level == 0:
        return module
    package = path.parent.resolve().relative_to(root.resolve()).parts
    if level > len(package):
        return None
    base = package[: len(package) - (level - 1)]
    if module:
        base += tuple(module.split("."))
    return ".".join(base) if base else None


def _record_from_import(found: set[str], module: str, names: list[ast.alias]) -> None:
    if module == "CODE":
        found.update(
            f"CODE.{alias.name}"
            for alias in names
            if alias.name.startswith("experiment_platform")
        )
    elif module.startswith("CODE.experiment_platform"):
        if module == "CODE.experiment_platform":
            found.update(
                f"{module}.{alias.name}"
                for alias in names
                if alias.name != "*"
            )
        else:
            found.add(module)


def _reverse_imports(path: Path, *, root: Path = ROOT) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _resolve_from_module(
                path, root, level=node.level, module=node.module)
            if module:
                _record_from_import(found, module, list(node.names))
        elif isinstance(node, ast.Import):
            found.update(
                alias.name for alias in node.names
                if alias.name.startswith("CODE.experiment_platform")
            )
        elif isinstance(node, ast.Call) and node.args:
            function_name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else None
            )
            target = node.args[0]
            if (
                function_name in {"__import__", "import_module"}
                and isinstance(target, ast.Constant)
                and isinstance(target.value, str)
                and target.value.startswith("CODE.experiment_platform")
            ):
                found.add(target.value)
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


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "from ..experiment_platform.v2_analysis import thing\n",
            {"CODE.experiment_platform.v2_analysis"},
        ),
        (
            "from CODE import experiment_platform\n",
            {"CODE.experiment_platform"},
        ),
        (
            "import importlib\n"
            "importlib.import_module('CODE.experiment_platform.v2_analysis')\n",
            {"CODE.experiment_platform.v2_analysis"},
        ),
        (
            "__import__('CODE.experiment_platform.v2_analysis')\n",
            {"CODE.experiment_platform.v2_analysis"},
        ),
    ],
)
def test_reverse_import_detector_covers_import_spellings(
    tmp_path: Path, source: str, expected: set[str]
) -> None:
    path = tmp_path / "CODE" / "leo_sim" / "submodule.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")

    assert _reverse_imports(path, root=tmp_path) == expected
