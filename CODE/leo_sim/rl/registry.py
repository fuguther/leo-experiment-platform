"""Named registries for observation, model, and agent plugins."""
from __future__ import annotations

from collections.abc import Callable


_OBSERVATIONS: dict[str, Callable] = {}
_MODELS: dict[str, Callable] = {}
_AGENTS: dict[str, Callable] = {}


def _normalise(kind: str, name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{kind} name must be a non-empty string")
    return name.strip()


def _register(kind: str, registry: dict[str, Callable], name: str):
    key = _normalise(kind, name)

    def decorator(value: Callable) -> Callable:
        if key in registry:
            raise ValueError(f"duplicate {kind} registration: {key!r}")
        if not callable(value):
            raise TypeError(f"{kind} plugin {key!r} must be callable")
        registry[key] = value
        return value

    return decorator


def register_observation(name: str):
    return _register("observation", _OBSERVATIONS, name)


def register_model(name: str):
    return _register("model", _MODELS, name)


def register_agent(algorithm: str):
    return _register("agent", _AGENTS, algorithm)


def _lookup(kind: str, registry: dict[str, Callable], name: str) -> Callable:
    key = _normalise(kind, name)
    try:
        return registry[key]
    except KeyError as exc:
        available = ", ".join(sorted(registry)) or "(none)"
        raise ValueError(
            f"unknown {kind} {key!r}; registered: {available}") from exc


def get_observation_builder(name: str) -> Callable:
    return _lookup("observation", _OBSERVATIONS, name)


def get_model_builder(name: str) -> Callable:
    return _lookup("model", _MODELS, name)


def get_agent_class(algorithm: str) -> Callable:
    return _lookup("agent", _AGENTS, algorithm)


def registered_observations() -> tuple[str, ...]:
    return tuple(sorted(_OBSERVATIONS))


def registered_models() -> tuple[str, ...]:
    return tuple(sorted(_MODELS))


def registered_agents() -> tuple[str, ...]:
    return tuple(sorted(_AGENTS))


__all__ = [
    "register_observation", "register_model", "register_agent",
    "get_observation_builder", "get_model_builder", "get_agent_class",
    "registered_observations", "registered_models", "registered_agents",
]
