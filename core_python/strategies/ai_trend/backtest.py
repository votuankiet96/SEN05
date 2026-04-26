"""Backtest entry points for AI Trend / KNN + M30 EMA strategy."""

from __future__ import annotations

from math import ceil
from typing import Any

import pandas as pd

from shared.contracts import SymbolBacktestResult
from shared.data import load_backtest_ohlcv, load_backtest_ohlcv_full
from shared.execution import backtest_fast, backtest_symbol
from shared.metrics import calc_metrics, in_bao_cao
from shared.monte_carlo import plot_monte_carlo, run_monte_carlo

from .config import (
    ENTRY_TIMEFRAME,
    STRATEGY,
    TREND_TIMEFRAME,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_params,
)
from .logic import add_ai_trend_indicators, detect_ai_trend_signals, session_mask


def _tf_minutes(tf: str) -> int:
    tf = tf.upper().strip()
    if tf.startswith("M"):
        return int(tf[1:])
    if tf.startswith("H"):
        return int(tf[1:]) * 60
    if tf == "D1":
        return 1440
    if tf == "W":
        return 10080
    raise ValueError(f"Unsupported timeframe code: {tf}")


def _default_warmup(params: dict[str, Any] | None = None) -> int:
    p = {**get_indicator_params(), **(params or {})}
    return max(
        int(p["EMA_SLOW"]),
        int(p["ATR_PERIOD"]),
        int(p["AI_SMOOTHING_PERIOD"]),
    ) * 6


def load_backtest_frames(
    symbol_id: int,
    *,
    date_to: str | None = None,
    max_bars: int = 60000,
    warmup: int | None = None,
    indicator_overrides: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load entry M30 and trend H3 OHLCV frames for one symbol."""
    warmup = _default_warmup(indicator_overrides) if warmup is None else int(warmup)
    df_m30 = load_backtest_ohlcv(
        symbol_id=symbol_id,
        date_to=date_to,
        tf=ENTRY_TIMEFRAME,
        max_bars=max_bars,
        warmup=warmup,
    )

    ratio = max(1, ceil(_tf_minutes(TREND_TIMEFRAME) / _tf_minutes(ENTRY_TIMEFRAME)))
    trend_bars = ceil((max_bars + warmup) / ratio) + warmup
    df_h3 = load_backtest_ohlcv(
        symbol_id=symbol_id,
        date_to=date_to,
        tf=TREND_TIMEFRAME,
        max_bars=trend_bars,
        warmup=warmup,
    )
    return df_m30, df_h3


def load_backtest_full(
    symbol_id: int,
    *,
    tf: str = ENTRY_TIMEFRAME,
    max_bars: int = 80000,
) -> pd.DataFrame:
    """Load a deep history buffer for one timeframe."""
    return load_backtest_ohlcv_full(symbol_id=symbol_id, tf=tf, max_bars=max_bars)


def _build_indicator_params(
    symbol_key: str,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_params: dict[str, Any] | None = None,
    broker_profile: str | None = None,
) -> dict[str, Any]:
    cfg = symbol_params or get_symbol_params(symbol_key, broker_profile=broker_profile)
    params = {
        **get_indicator_params(),
        "KTP": float(cfg["ktp"]),
        "X": float(cfg["x"]),
    }
    if indicator_overrides:
        params.update(indicator_overrides)
    return params


def build_symbol_signal_frame(
    symbol_key: str,
    df_m30: pd.DataFrame,
    df_h3: pd.DataFrame,
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
    params = _build_indicator_params(
        symbol_key,
        indicator_overrides=indicator_overrides,
        symbol_params=cfg,
        broker_profile=broker_profile,
    )

    df_ind = add_ai_trend_indicators(df_m30.copy(), df_h3.copy(), params, trend_tf=TREND_TIMEFRAME)
    if date_from or date_to:
        mask = pd.Series(True, index=df_ind.index)
        if date_from:
            mask &= df_ind.index >= pd.Timestamp(date_from)
        if date_to:
            mask &= df_ind.index <= pd.Timestamp(date_to)
        df_ind["in_window"] = mask

    sess = session_mask(df_ind, cfg.get("session_hours_utc", []))
    df_sig = detect_ai_trend_signals(df_ind, sess, sym_key=symbol_key, params=params)
    return df_sig, cfg, params


def run_symbol_backtest_on_frames(
    symbol_key: str,
    df_m30: pd.DataFrame,
    df_h3: pd.DataFrame,
    init_eq: float = 100_000.0,
    *,
    account_mode: str = "standard",
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
) -> SymbolBacktestResult:
    """Run a complete symbol backtest on preloaded M30/H3 frames."""
    df_sig, cfg, params = build_symbol_signal_frame(
        symbol_key,
        df_m30,
        df_h3,
        date_from=date_from,
        date_to=date_to,
        indicator_overrides=indicator_overrides,
        symbol_overrides=symbol_overrides,
        broker_profile=broker_profile,
    )

    strategy_cfg = get_account_settings(account_mode, strategy_overrides)
    strategy_cfg["trailing_activation"] = float(
        cfg.get("trailing_activation", strategy_cfg["trailing_activation"])
    )
    strategy_cfg["min_rr"] = float(params["MIN_RR"])
    strategy_cfg["ktp"] = float(params["KTP"])

    trades, equity = backtest_symbol(
        symbol_key,
        df_sig,
        cfg,
        init_eq,
        strategy=strategy_cfg,
        costs=get_cost_settings(symbol_key, costs, broker_profile=broker_profile),
    )
    metrics = calc_metrics(trades, equity)

    return SymbolBacktestResult(
        symbol=symbol_key,
        raw_data=df_m30,
        signal_data=df_sig,
        trades=trades,
        equity=equity,
        metrics=metrics,
        symbol_config=cfg,
        strategy_settings=strategy_cfg,
        account_mode=account_mode,
    )


def run_symbol_backtest(
    symbol_key: str,
    init_eq: float = 100_000.0,
    *,
    account_mode: str = "standard",
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
    """Load DB data and run a complete symbol backtest."""
    cfg = {
        **get_symbol_params(symbol_key, broker_profile=broker_profile),
        **(symbol_overrides or {}),
    }
    df_m30, df_h3 = load_backtest_frames(
        cfg["symbol_id"],
        date_to=date_to,
        max_bars=max_bars,
        warmup=warmup,
        indicator_overrides=indicator_overrides,
    )
    return run_symbol_backtest_on_frames(
        symbol_key,
        df_m30,
        df_h3,
        init_eq=init_eq,
        account_mode=account_mode,
        date_from=date_from,
        date_to=date_to,
        indicator_overrides=indicator_overrides,
        symbol_overrides=symbol_overrides,
        strategy_overrides=strategy_overrides,
        costs=costs,
        broker_profile=broker_profile,
    )


__all__ = [
    "add_ai_trend_indicators",
    "backtest_fast",
    "backtest_symbol",
    "build_symbol_signal_frame",
    "detect_ai_trend_signals",
    "in_bao_cao",
    "load_backtest_frames",
    "load_backtest_full",
    "plot_monte_carlo",
    "run_monte_carlo",
    "run_symbol_backtest",
    "run_symbol_backtest_on_frames",
    "session_mask",
]

