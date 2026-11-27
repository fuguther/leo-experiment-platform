"""Fail-closed persistence and resume helpers for learning artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


class LearningUnavailable(RuntimeError):
    """Learning execution requested without a required runtime/artifact."""


def _verify_checkpoint_metadata(meta, expected_contract, checkpoint_name,
                                checkpoint_sha, schema, algorithm):
    """Fail-closed provenance gate for sibling checkpoint metadata.

    The metadata file must be a regular, non-symlink artifact whose fields
    bind it to the exact checkpoint artifact being loaded: schema, algorithm,
    contract, filename and the artifact SHA.  Any mismatch or missing field
    is a LearningUnavailable (never a silent skip)."""
    if not isinstance(meta, dict):
        raise LearningUnavailable("checkpoint metadata is not a mapping")
    if meta.get("schema") != schema:
        raise LearningUnavailable(
            f"checkpoint metadata schema {meta.get('schema')!r} != {schema!r}")
    if meta.get("algorithm") != algorithm:
        raise LearningUnavailable(
            f"checkpoint metadata algorithm {meta.get('algorithm')!r} "
            f"!= {algorithm!r}")
    if meta.get("contract") != expected_contract:
        raise LearningUnavailable(
            "checkpoint contract mismatch: metadata says "
            f"{meta.get('contract')!r}, resolved config wants "
            f"{expected_contract!r}")
    if meta.get("checkpoint") != checkpoint_name:
        raise LearningUnavailable(
            f"checkpoint metadata filename {meta.get('checkpoint')!r} "
            f"!= {checkpoint_name!r}")
    if meta.get("checkpoint_sha256") != checkpoint_sha:
        raise LearningUnavailable(
            "checkpoint metadata SHA does not match the artifact")
    if meta.get("checkpoint_verified") is not True:
        raise LearningUnavailable(
            "checkpoint metadata was not verified at save time")


def _read_json_bytes(data: bytes, what: str) -> dict:
    """Decode+parse one UTF-8 JSON artifact from already-read bytes.

    Decode errors (invalid UTF-8) and parse errors must surface as
    LearningUnavailable, never as a bare UnicodeError/JSONDecodeError that
    escapes the learning contract and makes a corrupt artifact look like an
    unrelated crash.  Parsing from the same bytes that were hashed closes
    the hash-then-reopen TOCTOU window for JSON artifacts."""
    try:
        return json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LearningUnavailable(f"{what} unreadable: {exc}") from exc


def _read_checkpoint_metadata(path: Path, expected_sha, what: str):
    """Read, hash, verify and parse a sibling metadata artifact atomically.

    The metadata file must be a regular non-symlink file whose own SHA-256 is
    pinned in the resolved config.  Without this independent anchor, a
    checkpoint could be relabeled (e.g. C3 -> C4) by rewriting only the
    sibling metadata while the pinned checkpoint SHA stays valid.  Returns
    (meta, actual_sha); the SHA and the parsed content come from the SAME
    byte read, so a concurrent rewrite cannot slip a different payload past
    the pin (single-read, no hash-then-reopen for JSON)."""
    if path.is_symlink() or not path.is_file():
        raise LearningUnavailable(f"{what} missing or symbolic")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise LearningUnavailable(
            f"{what} SHA-256 not pinned in resolved config")
    data = path.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise LearningUnavailable(
            f"{what} SHA-256 differs from resolved config")
    return _read_json_bytes(data, what), actual_sha


def _resume_json(value):
    """Convert numpy/TensorFlow scalar state to deterministic JSON values."""
    if isinstance(value, np.ndarray):
        return {
            "__ndarray__": value.tolist(),
            "dtype": str(value.dtype),
            "shape": list(value.shape),
        }
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(k): _resume_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_resume_json(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise LearningUnavailable(
        f"resume state contains non-serializable value {type(value).__name__}")


def _resume_unjson(value):
    if isinstance(value, dict) and set(value) == {"__ndarray__", "dtype", "shape"}:
        try:
            arr = np.asarray(value["__ndarray__"], dtype=value["dtype"])
            if list(arr.shape) != list(value["shape"]):
                raise ValueError("array shape mismatch")
            return arr
        except (TypeError, ValueError) as exc:
            raise LearningUnavailable(f"resume ndarray is invalid: {exc}") from exc
    if isinstance(value, dict):
        return {k: _resume_unjson(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resume_unjson(v) for v in value]
    return value


def _resume_file_record(path: Path) -> dict:
    if path.is_symlink() or not path.is_file():
        raise LearningUnavailable(f"resume artifact missing or symbolic: {path.name}")
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}


def _write_resume_manifest(root: Path, schema: str, identity: dict) -> str:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.name == "manifest.json" or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith("../") or Path(rel).is_absolute():
            raise LearningUnavailable("resume artifact path escapes bundle")
        files[rel] = _resume_file_record(path)
    if not files:
        raise LearningUnavailable("resume bundle contains no state artifacts")
    payload = {
        "schema": schema,
        "identity": identity,
        "files": files,
    }
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return hashlib.sha256(manifest.read_bytes()).hexdigest()


def _verify_resume_bundle(root: Path, expected_sha: str, schema: str) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise LearningUnavailable("resume_path must be a regular directory")
    manifest = root / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise LearningUnavailable("resume manifest missing or symbolic")
    data = manifest.read_bytes()
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise LearningUnavailable("resume manifest SHA-256 differs from resolved config")
    payload = _read_json_bytes(data, "resume manifest")
    if payload.get("schema") != schema:
        raise LearningUnavailable(
            f"resume schema {payload.get('schema')!r} != {schema!r}")
    files = payload.get("files")
    if not isinstance(files, dict) or not files:
        raise LearningUnavailable("resume manifest has no file records")
    for rel, record in files.items():
        candidate = Path(rel)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise LearningUnavailable("resume manifest contains unsafe file path")
        path = root / candidate
        actual = _resume_file_record(path)
        if actual != record:
            raise LearningUnavailable(f"resume artifact hash mismatch: {rel}")
    return payload


def _resume_identity(cfg: dict, contract: str, algorithm: str, seed: int) -> dict:
    ident = {
        "algorithm": algorithm,
        "contract": contract,
        "seed": int(seed),
        "gamma": float(cfg["gamma"]),
        "lr": float(cfg["lr"]),
        "batch_size": int(cfg["batch_size"]),
        "replay_size": int(cfg["replay_size"]),
        "target_update_interval": int(cfg["target_update_interval"]),
        "fast_train": bool(cfg["fast_train"]),
    }
    if bool(cfg.get("include_last_hop", False)):
        ident["include_last_hop"] = True
    if cfg.get("clip_norm") is not None:
        ident["clip_norm"] = float(cfg["clip_norm"])
    return ident


def _require_resume_identity(manifest: dict, expected: dict) -> None:
    if manifest.get("identity") != expected:
        raise LearningUnavailable(
            "resume training contract identity differs from resolved config")


__all__ = [
    "LearningUnavailable",
    "_verify_checkpoint_metadata",
    "_read_json_bytes",
    "_read_checkpoint_metadata",
    "_resume_json",
    "_resume_unjson",
    "_resume_file_record",
    "_write_resume_manifest",
    "_verify_resume_bundle",
    "_resume_identity",
    "_require_resume_identity",
]
