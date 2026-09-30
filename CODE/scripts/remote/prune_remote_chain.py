"""Remote tree hygiene: make the VM CODE tree match the synced file set.

macOS tar can emit AppleDouble sidecars (._name.py) and a tar stream cannot
delete files that no longer exist locally, so the deployed tree is pruned to
exactly the chain the launch manifest declares.  Run on the VM, from the
workspace root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ("CODE/leo_sim", "CODE/experiment_platform")


def main() -> int:
    manifest = json.loads((ROOT / "launch.json").read_text(encoding="utf-8"))
    declared = set(manifest.get("chain_files") or [])
    if not declared:
        print("no chain_files in launch.json: refusing to prune")
        return 2
    removed = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).rglob("*.py")):
            rel = str(path.relative_to(ROOT))
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            if path.name.startswith(("._", ".")):
                path.unlink()
                removed.append(rel)
                continue
            if rel not in declared:
                path.unlink()
                removed.append(rel)
    for junk in list(ROOT.rglob("._*")) + list(ROOT.rglob("*.pyc")):
        if junk.is_file():
            junk.unlink()
            removed.append(str(junk.relative_to(ROOT)))
    print(json.dumps({"pruned": len(removed), "sample": removed[:5]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
