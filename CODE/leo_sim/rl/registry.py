"""Named registries for observation, model, and agent plugins."""
from __future__ import annotations

from collections.abc import Callable


_OBSERVATIONS: dict[str, Callable] = {}
_MODELS: dict[str, Callable] = {}
_AGENTS: dict[tuple[str, str | None], Callable] = {}


def _normalise(kind: str, name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{kind} name must be a non-empty string")
    return name.strip()


def _register(kind: str, registry: dict, key):
    def decorator(value: Callable) -> Callable:
        if key in registry:
            raise ValueError(f"duplicate {kind} registration: {key!r}")
        if not callable(value):
            raise TypeError(f"{kind} plugin {key!r} must be callable")
        registry[key] = value
        return value

    return decorator


def register_observation(name: str):
    return _register("observation", _OBSERVATIONS, _normalise("observation", name))


def register_model(name: str):
    return _register("model", _MODELS, _normalise("model", name))


def register_agent(algorithm: str, *, scope: str | None = None):
    key = _normalise("agent", algorithm)
    if scope is not None:
        scope = _normalise("agent scope", scope)
    return _register("agent", _AGENTS, (key, scope))


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


def get_agent_class(algorithm: str,
                    scope: str | None = None) -> Callable:
    key = _normalise("agent", algorithm)
    if scope is not None:
        scoped = (key, _normalise("agent scope", scope))
        if scoped in _AGENTS:
            return _AGENTS[scoped]
    default = (key, None)
    if default in _AGENTS:
        return _AGENTS[default]
    available = ", ".join(sorted({name for name, _ in _AGENTS})) or "(none)"
    raise ValueError(
        f"unknown agent {key!r} for scope {scope!r}; registered: {available}")


def registered_observations() -> tuple[str, ...]:
    return tuple(sorted(_OBSERVATIONS))


def registered_models() -> tuple[str, ...]:
    return tuple(sorted(_MODELS))


def registered_agents() -> tuple[str, ...]:
    return tuple(sorted({name for name, _ in _AGENTS}))


__all__ = [
    "register_observation", "register_model", "register_agent",
    "get_observation_builder", "get_model_builder", "get_agent_class",
    "registered_observations", "registered_models", "registered_agents",
]
