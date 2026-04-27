"""Symbol-level backtest helpers for MA Cross."""

from __future__ import annotations

from typing import Any

import pandas as pd

from shared.contracts import SymbolBacktestResult
from shared.data import load_backtest_ohlcv, load_backtest_ohlcv_full
from shared.execution_market import backtest_market_symbol
from shared.metrics import calc_metrics, in_bao_cao
from shared.monte_carlo import plot_monte_carlo, run_monte_carlo

from ..config import (
    TIMEFRAME,
    TIMEFRAMES,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_params,
)
from ..logic import add_ma_cross_indicators, detect_ma_cross_signals, session_mask


def _default_warmup(params: dict[str, Any] | None = None) -> int:
    p = {**get_indicator_params(), **(params or {})}
    return max(int(p["FAST_MA"]), int(p["SLOW_MA"]), int(p["ATR_PERIOD"])) * 6


def load_backtest_data(
    symbol_id: int,
    date_to: str | None = None,
    tf: str | None = None,
    max_bars: int = 60000,
    warmup: int | None = None,
    indicator_overrides: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Load OHLCV data for one MA Cross timeframe."""
    tf = (tf or TIMEFRAME).upper()
    if tf not in TIMEFRAMES:
        raise ValueError(f"Unsupported MA Cross timeframe '{tf}'. Use one of {TIMEFRAMES}.")
    warmup = _default_warmup(indicator_overrides) if warmup is None else int(warmup)
    return load_backtest_ohlcv(
        symbol_id=symbol_id,
        date_to=date_to,
        tf=tf,
        max_bars=max_bars,
        warmup=warmup,
    )


def load_backtest_full(
    symbol_id: int,
    tf: str | None = None,
    max_bars: int = 80000,
) -> pd.DataFrame:
    """Load a deep history buffer for optimization/caching."""
    tf = (tf or TIMEFRAME).upper()
    if tf not in TIMEFRAMES:
        raise ValueError(f"Unsupported MA Cross timeframe '{tf}'. Use one of {TIMEFRAMES}.")
    return load_backtest_ohlcv_full(symbol_id=symbol_id, tf=tf, max_bars=max_bars)


def _build_indicator_params(
    indicator_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = get_indicator_params()
    if indicator_overrides:
        params.update(indicator_overrides)
    fast = int(params["FAST_MA"])
    slow = int(params["SLOW_MA"])
    if fast >= slow:
        raise ValueError(f"FAST_MA must be smaller than SLOW_MA. Got {fast}/{slow}.")
    return params


def build_symbol_signal_frame(
    symbol_key: str,
    df_raw: pd.DataFrame,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, Any] | None = None,
    broker_profile: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], dict[str, Any]]:
    """Prepare indicators + signal column for one symbol."""
    cfg = {
        **get_symbol_params(symbol_key, broker_profile=broker_profile),
        **(symbol_overrides or {}),
    }
    params = _build_indicator_params(indicator_overrides)
    df_ind = add_ma_cross_indicators(df_raw.copy(), params)
    if date_from or date_to:
        mask = pd.Series(True, index=df_ind.index)
        if date_from:
            mask &= df_ind.index >= pd.Timestamp(date_from)
        if date_to:
            mask &= df_ind.index <= pd.Timestamp(date_to)
        df_ind["in_window"] = mask

    sess = session_mask(df_ind, cfg.get("session_hours_utc", []))
    df_sig = detect_ma_cross_signals(df_ind, sess, sym_key=symbol_key, params=params)
    return df_sig, cfg, params


def run_symbol_backtest_on_frame(
    symbol_key: str,
    df_raw: pd.DataFrame,
    init_eq: float = 100_000.0,
    *,
    account_mode: str = "standard",
    tf: str = TIMEFRAME,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
) -> SymbolBacktestResult:
    """Run a complete MA Cross backtest on a preloaded frame."""
    tf = tf.upper()
    df_sig, cfg, params = build_symbol_signal_frame(
        symbol_key,
        df_raw,
        date_from=date_from,
        date_to=date_to,
        indicator_overrides=indicator_overrides,
        symbol_overrides=symbol_overrides,
        broker_profile=broker_profile,
    )

    strategy_cfg = get_account_settings(account_mode, strategy_overrides)
    strategy_cfg["timeframe"] = tf
    strategy_cfg["fast_ma"] = int(params["FAST_MA"])
    strategy_cfg["slow_ma"] = int(params["SLOW_MA"])
    strategy_cfg["atr_period"] = int(params["ATR_PERIOD"])

    trades, equity = backtest_market_symbol(
        symbol_key,
        df_sig,
        cfg,
        init_eq,
        strategy=strategy_cfg,
        costs=get_cost_settings(symbol_key, costs, broker_profile=broker_profile),
    )
    metrics = calc_metrics(trades, equity, tf_code=tf)

    return SymbolBacktestResult(
        symbol=symbol_key,
        raw_data=df_raw,
        signal_data=df_sig,
        trades=trades,
        equity=equity,
        metrics=metrics,
        symbol_config={**cfg, "timeframe": tf},
        strategy_settings=strategy_cfg,
        account_mode=account_mode,
    )


def run_symbol_backtest(
    symbol_key: str,
    init_eq: float = 100_000.0,
    *,
    account_mode: str = "standard",
    tf: str = TIMEFRAME,
    date_from: str | None = None,
    date_to: str | None = None,
    max_bars: int = 60000,
    warmup: int | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
) -> SymbolBacktestResult:
    """Load data from DB and run a complete MA Cross symbol backtest."""
    cfg = {
        **get_symbol_params(symbol_key, broker_profile=broker_profile),
        **(symbol_overrides or {}),
    }
    df_raw = load_backtest_data(
        cfg["symbol_id"],
        date_to=date_to,
        tf=tf,
        max_bars=max_bars,
        warmup=warmup,
        indicator_overrides=indicator_overrides,
    )
    return run_symbol_backtest_on_frame(
        symbol_key,
        df_raw,
        init_eq=init_eq,
        account_mode=account_mode,
        tf=tf,
        date_from=date_from,
        date_to=date_to,
        indicator_overrides=indicator_overrides,
        symbol_overrides=symbol_overrides,
        strategy_overrides=strategy_overrides,
        costs=costs,
        broker_profile=broker_profile,
    )


__all__ = [
    "add_ma_cross_indicators",
    "backtest_market_symbol",
    "build_symbol_signal_frame",
    "detect_ma_cross_signals",
    "in_bao_cao",
    "load_backtest_data",
    "load_backtest_full",
    "plot_monte_carlo",
    "run_monte_carlo",
    "run_symbol_backtest",
    "run_symbol_backtest_on_frame",
    "session_mask",
]
