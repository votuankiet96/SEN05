"""Small registries for named execution components.

Backtest presets should refer to entry, order, sizing, and exit behavior by
name. This module keeps that lookup pattern consistent without tying it to any
single strategy.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T", bound=Callable)


def register(registry: dict[str, T], name: str, fn: T | None = None):
    """Register a callable under `name` and return the callable unchanged."""

    def decorator(inner: T) -> T:
        registry[name] = inner
        return inner

    if fn is not None:
        return decorator(fn)
    return decorator


def get_component(registry: dict[str, T], name: str, component_type: str) -> T:
    """Return a named component with a readable error on missing names."""
    try:
        return registry[name]
    except KeyError as exc:
        known = ", ".join(sorted(registry)) or "<none>"
        raise ValueError(f"Unknown {component_type}: {name!r}. Known: {known}") from exc
