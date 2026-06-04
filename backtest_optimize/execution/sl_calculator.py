"""Stop-loss calculation registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from backtest_optimize.contracts import Direction, SLResult, SignalRow
from backtest_optimize.io.market_data import normalize_ohlcv_frame

SLFunction = Callable[[SignalRow, float, pd.DataFrame, dict[str, Any]], SLResult]
SL_REGISTRY: dict[str, SLFunction] = {}
DEFAULT_COMBO_X_BUFFER = 10.0


def register_sl_method(name: str, fn: SLFunction | None = None):
    """Register an SL method by name."""

    def decorator(inner: SLFunction) -> SLFunction:
        SL_REGISTRY[name] = inner
        return inner

    if fn is not None:
        return decorator(fn)
    return decorator


def get_sl_method(name: str) -> SLFunction:
    try:
        return SL_REGISTRY[name]
    except KeyError as exc:
        raise ValueError(f"Unknown SL method: {name!r}") from exc


def calculate_sl(
    method: str,
    signal: SignalRow,
    entry_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any] | None = None,
) -> SLResult:
    """Dispatch an SL calculation."""
    return get_sl_method(method)(signal, float(entry_price), bars, params or {})


def _signal_bar(signal: SignalRow, bars: pd.DataFrame, method: str) -> pd.Series:
    normalized = normalize_ohlcv_frame(bars)
    matches = normalized[normalized["bartime"] == signal.bartime]
    if matches.empty:
        raise ValueError(f"{method} requires the signal bartime to exist in bars.")
    return matches.iloc[-1]


@register_sl_method("signal_sl")
def signal_sl(signal: SignalRow, entry_price: float, bars: pd.DataFrame, params: dict[str, Any]) -> SLResult:
    """Use an explicit SL price supplied by the signal table."""
    del entry_price, bars
    column = str(params.get("column", "sl_price"))
    value = signal.extras.get(column)
    if value is None or pd.isna(value):
        raise ValueError(f"Signal has no usable {column!r} value for signal_sl.")
    return SLResult(price=float(value), method="signal_sl", params=dict(params), source="signal")


@register_sl_method("atr_multiple")
def atr_multiple(
    signal: SignalRow,
    entry_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> SLResult:
    """Place SL at ATR multiple from entry."""
    del bars
    atr = signal.atr
    if atr is None or atr <= 0:
        raise ValueError("atr_multiple requires signal.atr > 0.")
    mult = float(params.get("atr_mult", params.get("multiplier", 1.5)))
    distance = atr * mult
    if signal.direction == Direction.BUY:
        price = entry_price - distance
    else:
        price = entry_price + distance
    return SLResult(price=float(price), method="atr_multiple", params=dict(params))


@register_sl_method("fixed_distance")
def fixed_distance(
    signal: SignalRow,
    entry_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> SLResult:
    """Place SL at a fixed price distance from entry."""
    del bars
    distance = float(params["distance"])
    if distance <= 0:
        raise ValueError("fixed_distance requires distance > 0.")
    price = entry_price - distance if signal.direction == Direction.BUY else entry_price + distance
    return SLResult(price=float(price), method="fixed_distance", params=dict(params))


@register_sl_method("combo_signal_bar")
def combo_signal_bar(
    signal: SignalRow,
    entry_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> SLResult:
    """Place Combo-style SL beyond the signal bar high/low plus X buffer.

    Entry remains controlled by the execution engine. This method only anchors
    risk to the original signal bar:

    - BUY:  signal bar low - x_buffer
    - SELL: signal bar high + x_buffer
    """
    bar = _signal_bar(signal, bars, "combo_signal_bar")
    x_buffer = float(params.get("x_buffer", params.get("X", DEFAULT_COMBO_X_BUFFER)))
    if x_buffer < 0:
        raise ValueError("combo_signal_bar requires x_buffer >= 0.")

    if signal.direction == Direction.BUY:
        price = float(bar["low"]) - x_buffer
        if price >= entry_price:
            raise ValueError("Calculated BUY combo SL is not below entry.")
    else:
        price = float(bar["high"]) + x_buffer
        if price <= entry_price:
            raise ValueError("Calculated SELL combo SL is not above entry.")

    resolved_params = dict(params)
    resolved_params["x_buffer"] = x_buffer
    return SLResult(
        price=float(price),
        method="combo_signal_bar",
        params=resolved_params,
        source="signal_bar",
    )


@register_sl_method("swing_extreme")
def swing_extreme(
    signal: SignalRow,
    entry_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> SLResult:
    """Place SL beyond recent bar-level swing extreme."""
    normalized = normalize_ohlcv_frame(bars)
    lookback = int(params.get("lookback", 10))
    if lookback <= 0:
        raise ValueError("lookback must be positive.")

    history = normalized[normalized["bartime"] <= signal.bartime].tail(lookback)
    if history.empty:
        raise ValueError("No historical bars available for swing_extreme SL.")

    buffer_price = float(params.get("buffer_price", 0.0))
    if params.get("buffer_atr_mult") is not None:
        if signal.atr is None:
            raise ValueError("buffer_atr_mult requires signal.atr.")
        buffer_price += float(params["buffer_atr_mult"]) * signal.atr

    if signal.direction == Direction.BUY:
        price = float(history["low"].min()) - buffer_price
        if price >= entry_price:
            raise ValueError("Calculated BUY swing SL is not below entry.")
    else:
        price = float(history["high"].max()) + buffer_price
        if price <= entry_price:
            raise ValueError("Calculated SELL swing SL is not above entry.")

    return SLResult(price=price, method="swing_extreme", params=dict(params))
