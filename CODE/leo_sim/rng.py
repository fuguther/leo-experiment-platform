"""Deterministic, separately-named RNG streams derived from one seed.

Each mechanism draws from its own stream so enabling/disabling one mechanism
does not perturb the randomness of others. Link-level Gilbert-Elliott channels
use link_stream(seed, key): a private stream derived from the run seed and a
stable link identity string, independent of object creation order and of any
other link's traffic.
"""
from __future__ import annotations

import hashlib

import numpy as np

STREAM_NAMES = (
    "demand",
    "ge_gsl",
    "ge_isl",
    "association",
    "routing",
    "control",
    "monitor",
    # Task 6: nested-family member filter.  MUST stay appended at the END:
    # numpy SeedSequence.spawn(n) children are positional, so the first seven
    # streams keep their exact child indices 0..6 and their generated values
    # byte-for-byte (spawn(1)[0] state == spawn(8)[0] state); the filter is
    # child 7 and never reuses child 1 (ge_gsl).
    "nested_filter",
)


def streams(seed: int, names=STREAM_NAMES) -> dict[str, np.random.Generator]:
    ss = np.random.SeedSequence(seed)
    children = ss.spawn(len(names))
    return {name: np.random.default_rng(child) for name, child in zip(names, children)}


def stream_mapping(seed: int, names=STREAM_NAMES) -> dict[str, str]:
    """Human-readable seed->stream mapping recorded in the trace manifest."""
    return {name: f"SeedSequence({seed}).spawn[{i}]" for i, name in enumerate(names)}


def link_stream(seed: int, link_key: str) -> np.random.Generator:
    """Private per-link stream: SeedSequence([seed, sha256(link_key)])."""
    digest = hashlib.sha256(link_key.encode("utf-8")).digest()
    key_int = int.from_bytes(digest[:8], "little")
    return np.random.default_rng(np.random.SeedSequence([seed, key_int]))
