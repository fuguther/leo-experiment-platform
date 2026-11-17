"""External inference-only policy plugin loader (generic capability).

WHY THIS MODULE EXISTS
======================
The platform charter keeps ALGORITHMS out of the simulation core: a research
algorithm (a predictor, an attention fusion, a learned Q-head, ...) must not
be implemented inside ``leo_sim/``.  Until now the only way to run a frozen
policy was ``inference.FixedModelPolicy`` with a linear adapter and the
platform's own feature builder, which cannot express a model with its own
observation contract.

This module adds the missing *generic* seam and nothing else: it loads a
declared plugin (a plain Python module + manifest), verifies the manifest
binds the exact module bytes, and hands the resulting object to the kernel's
existing inference-only hook.  It knows nothing about any particular
algorithm: no predictor, no attention, no Q-network, no TAP-DAR concept
appears here.

CONTRACT (leo-policy-plugin/v1)
===============================
The manifest is JSON::

    {
      "schema": "leo-policy-plugin/v1",
      "name": "example",                  # non-empty, informational
      "entry_module": "policy.py",        # relative to the manifest directory
      "entry_class": "ExamplePolicy",     # instantiated with no arguments
      "module_sha256": "<64 hex>",        # SHA-256 of entry_module bytes
      "checkpoint_sha256": {"w.h5": "..."} # optional; verified if present,
                                           # resolved relative to the manifest
      "info_rights": ["..."]              # declared information the policy reads
    }

The policy object must expose ``act(snapshot) -> direction`` and must be
inference-only (``inference.assert_inference_only`` is applied; it refuses any
training entry point that does not raise).  The kernel already refuses to run
an inference policy without ``time_alignment.enabled``, because there would be
no frozen snapshot for it to act on.

FAIL CLOSED
===========
Every step refuses rather than degrades: unknown manifest fields, missing keys,
a module digest that does not match the bytes on disk, a checkpoint digest that
does not match, a policy without ``act``, or a policy that exposes training
entry points all raise :class:`PolicyPluginError`.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

PLUGIN_SCHEMA = "leo-policy-plugin/v1"

_REQUIRED_KEYS = {
    "schema", "name", "entry_module", "entry_class", "module_sha256",
}
_ALLOWED_KEYS = _REQUIRED_KEYS | {"checkpoint_sha256", "info_rights"}


class PolicyPluginError(ValueError):
    """A declared policy plugin cannot be loaded or is not inference-only."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(root: Path, raw: str, what: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise PolicyPluginError(f"{what} must be a non-empty string")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PolicyPluginError(
            f"{what} must stay inside the manifest directory: {raw!r}")
    resolved = (root / candidate).resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        raise PolicyPluginError(f"{what} escapes the manifest directory")
    if not resolved.is_file():
        raise PolicyPluginError(f"{what} does not exist: {resolved}")
    return resolved


def load_manifest(manifest_path: str | Path) -> dict:
    """Read and validate one plugin manifest (no import yet)."""
    path = Path(manifest_path)
    if not path.is_file():
        raise PolicyPluginError(f"policy plugin manifest not found: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyPluginError(f"policy plugin manifest is not JSON: {exc}") from exc
    if not isinstance(manifest, dict):
        raise PolicyPluginError("policy plugin manifest must be a JSON object")
    unknown = set(manifest) - _ALLOWED_KEYS
    if unknown:
        raise PolicyPluginError(
            f"unknown policy plugin manifest fields {sorted(unknown)}; "
            f"allowed {sorted(_ALLOWED_KEYS)}")
    missing = _REQUIRED_KEYS - set(manifest)
    if missing:
        raise PolicyPluginError(
            f"policy plugin manifest missing {sorted(missing)}")
    if manifest["schema"] != PLUGIN_SCHEMA:
        raise PolicyPluginError(
            f"policy plugin schema must be {PLUGIN_SCHEMA!r}, "
            f"got {manifest['schema']!r}")
    for key in ("name", "entry_module", "entry_class", "module_sha256"):
        if not isinstance(manifest[key], str) or not manifest[key]:
            raise PolicyPluginError(f"policy plugin {key} must be a non-empty string")
    checksums = manifest.get("checkpoint_sha256")
    if checksums is not None:
        if not isinstance(checksums, dict) or not checksums:
            raise PolicyPluginError(
                "policy plugin checkpoint_sha256 must be a non-empty object")
        for name, digest in checksums.items():
            if not isinstance(digest, str) or len(digest) != 64:
                raise PolicyPluginError(
                    f"checkpoint_sha256[{name!r}] must be a 64-hex digest")
    return manifest


def verify_files(path: str | Path, manifest: dict) -> Path:
    """Verify module/checkpoint bytes against the manifest; return module path."""
    root = Path(path).resolve().parent
    module = _safe_relative(root, manifest["entry_module"], "entry_module")
    actual = _sha256_file(module)
    if actual != manifest["module_sha256"]:
        raise PolicyPluginError(
            "policy plugin module bytes do not match the manifest: "
            f"{actual} != {manifest['module_sha256']}")
    for name, expected in (manifest.get("checkpoint_sha256") or {}).items():
        target = _safe_relative(root, name, f"checkpoint {name!r}")
        got = _sha256_file(target)
        if got != expected:
            raise PolicyPluginError(
                f"policy plugin checkpoint {name!r} does not match the "
                f"manifest: {got} != {expected}")
    return module


def load_policy_plugin(manifest_path: str | Path):
    """Load, verify and instantiate the declared inference-only policy."""
    from . import inference as _inference

    manifest = load_manifest(manifest_path)
    module_path = verify_files(manifest_path, manifest)
    spec = importlib.util.spec_from_file_location(
        f"leo_policy_plugin_{manifest['name']}", module_path)
    if spec is None or spec.loader is None:
        raise PolicyPluginError(
            f"cannot build an import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - surfaced with context
        raise PolicyPluginError(
            f"policy plugin module failed to import: "
            f"{type(exc).__name__}: {exc}") from exc
    factory = getattr(module, manifest["entry_class"], None)
    if factory is None:
        raise PolicyPluginError(
            f"policy plugin module has no {manifest['entry_class']!r}")
    try:
        policy = factory()
    except Exception as exc:  # noqa: BLE001
        raise PolicyPluginError(
            f"policy plugin {manifest['entry_class']}() failed: "
            f"{type(exc).__name__}: {exc}") from exc
    if not hasattr(policy, "act"):
        raise PolicyPluginError(
            "policy plugin object has no act(snapshot); the kernel calls act()")
    _inference.assert_inference_only(policy)
    return policy
