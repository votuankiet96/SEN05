"""Entry models shared by strategy-neutral backtests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtest_optimize.contracts import Direction, SignalRow
from backtest_optimize.execution.registry import get_component, register
from backtest_optimize.io.market_data import get_bar_row, get_next_open_bar


@dataclass(frozen=True)
class EntryResult:
    """Resolved entry target for a signal."""

    price: float
    model: str
    order_type: str = "market"
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "calculated"


EntryFunction = Callable[[SignalRow, pd.DataFrame, dict[str, Any]], EntryResult]
ENTRY_REGISTRY: dict[str, EntryFunction] = {}


def register_entry_model(name: str, fn: EntryFunction | None = None):
    """Register an entry model by name."""
    return register(ENTRY_REGISTRY, name, fn)


def get_entry_model(name: str) -> EntryFunction:
    return get_component(ENTRY_REGISTRY, name, "entry model")


def calculate_entry(
    model: str,
    signal: SignalRow,
    bars: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> EntryResult:
    """Dispatch a named entry model."""
    return get_entry_model(model)(signal, bars, params or {})


def _signal_bar(signal: SignalRow, bars: pd.DataFrame, model: str) -> pd.Series:
    try:
        return get_bar_row(bars, signal.bartime)
    except ValueError as exc:
        raise ValueError(f"{model} requires the signal bartime to exist in bars.") from exc


@register_entry_model("next_open")
def next_open(signal: SignalRow, bars: pd.DataFrame, params: dict[str, Any]) -> EntryResult:
    """Enter at the open of the bar after the signal bar."""
    del params
    bar = get_next_open_bar(signal, bars)
    return EntryResult(
        price=float(bar.open),
        model="next_open",
        order_type="market",
        source="next_bar_open",
    )


@register_entry_model("stop_breakout_signal_bar")
def stop_breakout_signal_bar(
    signal: SignalRow,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> EntryResult:
    """Place a stop order beyond the completed signal bar high/low.

    BUY uses `high + x_offset`; SELL uses `low - x_offset`, matching the Combo
    V0 cBot entry family while remaining strategy-neutral.
    """
    bar = _signal_bar(signal, bars, "stop_breakout_signal_bar")
    x_offset = float(params.get("x_offset", params.get("X", 0.0)))
    if x_offset < 0:
        raise ValueError("stop_breakout_signal_bar requires x_offset >= 0.")

    price = (
        float(bar["high"]) + x_offset
        if signal.direction == Direction.BUY
        else float(bar["low"]) - x_offset
    )
    resolved = dict(params)
    resolved["x_offset"] = x_offset
    return EntryResult(
        price=float(price),
        model="stop_breakout_signal_bar",
        order_type="stop",
        params=resolved,
        source="signal_bar",
    )
