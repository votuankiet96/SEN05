"""Exit-management helpers shared by component engines."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backtest_optimize.contracts import Direction, PositionLeg
from backtest_optimize.execution.registry import get_component, register


ExitFunction = Callable[[list[PositionLeg], dict[str, Any]], None]
EXIT_REGISTRY: dict[str, ExitFunction] = {}


def register_exit_model(name: str, fn: ExitFunction | None = None):
    """Register an exit-management model by name."""
    return register(EXIT_REGISTRY, name, fn)


def get_exit_model(name: str) -> ExitFunction:
    return get_component(EXIT_REGISTRY, name, "exit model")


def apply_exit_model(model: str, legs: list[PositionLeg], params: dict[str, Any] | None = None) -> None:
    """Apply a named exit model to mutable legs."""
    get_exit_model(model)(legs, params or {})


@register_exit_model("none")
def no_exit_management(legs: list[PositionLeg], params: dict[str, Any]) -> None:
    del legs, params


def improves_stop(direction: Direction, candidate: float, current: float) -> bool:
    """Return True when `candidate` reduces risk relative to `current`."""
    if direction == Direction.BUY:
        return float(candidate) > float(current)
    return float(candidate) < float(current)


def apply_sma_trailing_to_leg(leg: PositionLeg, sma_value: float) -> bool:
    """Trail one leg's SL toward SMA without widening risk."""
    current = float(leg.current_sl if leg.current_sl is not None else leg.sl_price)
    candidate = max(current, float(sma_value)) if leg.direction == Direction.BUY else min(current, float(sma_value))
    if not improves_stop(leg.direction, candidate, current):
        return False
    leg.current_sl = float(candidate)
    return True
