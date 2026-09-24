#!/usr/bin/env python3
"""Verify one pulled formal run before it is admitted to paired analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
RESULTS_ROOT = PROJECT_ROOT / "CODE" / "Results"
EXPERIMENTS_ROOT = PROJECT_ROOT / "EXPERIMENTS"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def direct_child(path: Path, root: Path, label: str) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path)))
    if lexical.is_symlink() or not lexical.is_dir() or lexical.resolve(strict=True) != lexical:
        raise ValueError(f"{label} must be an existing lexical non-symlink directory")
    if lexical.parent != root.resolve(strict=True) or lexical.name.startswith("_"):
        raise ValueError(f"{label} must be a direct non-control child of {root}")
    return lexical


def verify(
    run_id: str,
    config_path: Path,
    result_path: Path,
    *,
    launch_nonce: str,
    authorization_path: Path,
    run_attempt_id: str,
) -> list[str]:
    errors: list[str] = []
    config_abs = Path(os.path.abspath(os.fspath(config_path)))
    experiments = EXPERIMENTS_ROOT.resolve(strict=True)
    if config_abs.is_symlink() or not config_abs.is_file():
        return ["compiled config is missing or symbolic"]
    try:
        config_abs.resolve(strict=True).relative_to(experiments)
    except ValueError:
        return ["compiled config is outside EXPERIMENTS"]
    result = direct_child(result_path, RESULTS_ROOT, "result")
    config = load_json(config_abs)
    config_sha = canonical_sha(config)
    authorization_abs = Path(os.path.abspath(os.fspath(authorization_path)))
    if authorization_abs.is_symlink() or not authorization_abs.is_file():
        return ["authorization is missing or symbolic"]
    try:
        authorization_abs.resolve(strict=True).relative_to(experiments)
    except ValueError:
        return ["authorization is outside EXPERIMENTS"]
    authorization_sha = file_sha256(authorization_abs)
    if len(launch_nonce) != 32 or any(char not in "0123456789abcdef" for char in launch_nonce):
        errors.append("launch nonce must be exactly 32 lowercase hex characters")
    if len(run_attempt_id) != 32 or any(char not in "0123456789abcdef" for char in run_attempt_id):
        errors.append("run attempt id must be exactly 32 lowercase hex characters")
    provenance = config.get("provenance", {})
    if provenance.get("run_id") != run_id:
        errors.append("config provenance run_id mismatch")

    meta_path = result / "run_trace" / "run_meta.json"
    used_path = result / "config_used.json"
    artifact_path = result / "artifact_manifest.json"
    for path in (meta_path, used_path, artifact_path):
        if path.is_symlink() or not path.is_file():
            errors.append(f"missing or unsafe required receipt: {path.relative_to(result)}")
    if errors:
        return errors

    meta = load_json(meta_path)
    receipt = meta.get("effective_receipt")
    if not (
        meta.get("natural_end") is True
        and meta.get("interrupted") is False
        and meta.get("requested_run_id") == run_id
        and meta.get("config_canonical_sha256") == config_sha
        and meta.get("launch_nonce") == launch_nonce
        and meta.get("authorization_sha256") == authorization_sha
        and meta.get("run_attempt_id") == run_attempt_id
        and isinstance(receipt, dict)
        and receipt.get("schema") == "leo-effective-receipt/v1"
        and receipt.get("research_eligible") is True
        and receipt.get("mismatches") == []
    ):
        errors.append("run_meta completion, run/config/launch/authorization/attempt identity, or effective receipt is not eligible")

    used = load_json(used_path)
    if used != config or canonical_sha(used) != config_sha:
        errors.append("config_used differs from the compiled config")

    manifest = load_json(artifact_path)
    required = provenance.get("required_artifacts")
    if manifest.get("schema") != "artifact-manifest/v1":
        errors.append("artifact manifest schema mismatch")
    if manifest.get("run_id") != run_id or manifest.get("config_sha256") != config_sha:
        errors.append("artifact manifest identity or config hash mismatch")
    if not isinstance(required, list) or manifest.get("required_artifacts") != required:
        errors.append("artifact manifest required set differs from config provenance")
        required_set: set[str] = set()
    else:
        required_set = set(required)
    entries = manifest.get("artifacts")
    seen: set[str] = set()
    if not isinstance(entries, list):
        errors.append("artifact manifest entries are malformed")
        entries = []
    root = result.resolve(strict=True)
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            errors.append("artifact manifest entry is malformed")
            continue
        rel = entry["path"]
        candidate = (root / rel).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"artifact escapes result: {rel}")
            continue
        if rel in seen or candidate.is_symlink() or not candidate.is_file():
            errors.append(f"artifact missing, duplicate, or unsafe: {rel}")
            continue
        if candidate.stat().st_size != entry.get("size") or file_sha256(candidate) != entry.get("sha256"):
            errors.append(f"artifact byte receipt mismatch: {rel}")
            continue
        seen.add(rel)
    if seen != required_set - {"artifact_manifest.json"}:
        errors.append("artifact manifest does not cover the exact required set")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--launch-nonce", required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--run-attempt-id", required=True)
    args = parser.parse_args()
    try:
        errors = verify(
            args.run_id,
            args.config,
            args.result,
            launch_nonce=args.launch_nonce,
            authorization_path=args.authorization,
            run_attempt_id=args.run_attempt_id,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = [str(exc)]
    if errors:
        for error in errors:
            print(f"BLOCK: {error}")
        return 1
    print(f"VERIFIED: {args.run_id} -> {args.result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
