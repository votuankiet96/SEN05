"""Stop-loss calculation registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from backtest_optimize.contracts import Direction, SLResult, SignalRow
from backtest_optimize.io.market_data import ensure_normalized_ohlcv, get_bar_row

SLFunction = Callable[[SignalRow, float, pd.DataFrame, dict[str, Any]], SLResult]
SL_REGISTRY: dict[str, SLFunction] = {}
DEFAULT_COMBO_X_BUFFER = 10.0
CBOT_V0_KSL_LEVELS: dict[int, float] = {
    1: 0.382,
    2: 0.618,
    3: 1.000,
    4: 1.272,
}
DEFAULT_CBOT_V0_KSL_LEVEL = 2


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
    try:
        return get_bar_row(bars, signal.bartime)
    except ValueError as exc:
        raise ValueError(f"{method} requires the signal bartime to exist in bars.") from exc


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


def _combo_ksl(params: dict[str, Any]) -> tuple[int, float]:
    level_value = params.get("ksl_level", params.get("fib_level", params.get("level")))
    if level_value is not None:
        level = int(level_value)
        if level not in CBOT_V0_KSL_LEVELS:
            raise ValueError("signal_bar_atr_buffer ksl_level must be between 1 and 4.")
        return level, CBOT_V0_KSL_LEVELS[level]

    if params.get("ksl") is not None:
        ksl = float(params["ksl"])
        for level, multiplier in CBOT_V0_KSL_LEVELS.items():
            if abs(ksl - multiplier) <= 1e-9:
                return level, multiplier
        allowed = ", ".join(str(value) for value in CBOT_V0_KSL_LEVELS.values())
        raise ValueError(f"signal_bar_atr_buffer ksl must be one of: {allowed}.")

    return DEFAULT_CBOT_V0_KSL_LEVEL, CBOT_V0_KSL_LEVELS[DEFAULT_CBOT_V0_KSL_LEVEL]


@register_sl_method("signal_bar_atr_buffer")
def signal_bar_atr_buffer(
    signal: SignalRow,
    entry_price: float,
    bars: pd.DataFrame,
    params: dict[str, Any],
) -> SLResult:
    """Place SL outside the signal bar using a cBot-style KSL * ATR buffer.

    BUY: signal low - KSL * ATR.
    SELL: signal high + KSL * ATR.
    """
    bar = _signal_bar(signal, bars, "signal_bar_atr_buffer")
    if signal.atr is None or signal.atr <= 0:
        raise ValueError("signal_bar_atr_buffer requires signal.atr > 0.")
    ksl_level, ksl = _combo_ksl(params)

    if signal.direction == Direction.BUY:
        price = float(bar["low"]) - ksl * float(signal.atr)
        if price >= entry_price:
            raise ValueError("Calculated BUY signal-bar ATR SL is not below entry.")
    else:
        price = float(bar["high"]) + ksl * float(signal.atr)
        if price <= entry_price:
            raise ValueError("Calculated SELL signal-bar ATR SL is not above entry.")

    resolved = dict(params)
    resolved["ksl_level"] = ksl_level
    resolved["ksl"] = ksl
    return SLResult(
        price=float(price),
        method="signal_bar_atr_buffer",
        params=resolved,
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
    normalized = ensure_normalized_ohlcv(bars)
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
