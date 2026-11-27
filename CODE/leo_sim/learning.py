"""Compatibility facade for the modular :mod:`CODE.leo_sim.rl` package.

All historical ``CODE.leo_sim.learning`` imports remain valid. New code should
import from ``CODE.leo_sim.rl`` and use ``create_learner`` rather than branching
on an algorithm name.
"""
from __future__ import annotations

from .rl import *  # noqa: F401,F403 - compatibility facade re-exports every symbol
from .rl import __all__ as _rl_all

__all__ = list(_rl_all)
