"""Symbol-level backtest helpers for Combo.

Architecture role
-----------------
- This module is the bridge between raw market data and the generic execution
  engine in `shared.execution`.
- It resolves effective symbol config, account mode, indicators, and signal
  columns before handing off to the execution layer.

Upstream dependencies
---------------------
- `shared.data` for DB-backed OHLCV loading
- `strategies.combo.config` for canonical Combo parameters and symbol config
- `strategies.combo.universe` for the Combo-owned symbol universe
- `strategies.combo.config` only as a compatibility wrapper for old imports
- `strategies.combo.signals`, `strategies.combo.signals`, and
  `strategies.combo.signals` for Combo-owned alpha/rule helpers
- `strategies.combo.signals` only as the compatibility wrapper for old imports

Downstream dependencies
-----------------------
- `strategies.combo.portfolio.backtest`
- `strategies.combo.portfolio.walkforward`
- future reports / charts / notebooks
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.shared.contracts import SymbolBacktestResult
from core_python.shared.data import load_backtest_ohlcv, load_backtest_ohlcv_full
from core_python.shared.analytics import calc_metrics, in_bao_cao
from core_python.shared.analytics import plot_monte_carlo, run_monte_carlo

from ..execution import backtest_fast, backtest_symbol
from ..config import (
    TIMEFRAME,
    get_account_settings,
    get_cost_settings,
    get_indicator_params,
    get_symbol_params,
)
from ..signals import add_combo_indicators
from ..orders import create_order_intent_from_signal
from ..signals import detect_combo_signals, session_mask


def _timestamp_for_index(value: str | pd.Timestamp, index: pd.Index) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is not None and ts.tzinfo is None:
            ts = ts.tz_localize(index.tz)
        elif index.tz is None and ts.tzinfo is not None:
            ts = ts.tz_convert("UTC").tz_localize(None)
        elif index.tz is not None and ts.tzinfo is not None:
            ts = ts.tz_convert(index.tz)
    return ts


def load_backtest_data(
    symbol_id: int,
    date_to: str | None = None,
    tf: str | None = None,
    max_bars: int = 60000,
    warmup: int | None = None,
) -> pd.DataFrame:
    """Compatibility loader for symbol backtests."""
    tf = tf or TIMEFRAME
    if warmup is None:
        p = get_indicator_params()
        warmup = max(p["MA_PERIOD"], p["MACD_SLOW"], p["ATR_PERIOD"]) * 4
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
    """Load a deep history buffer for optimization / caching."""
    return load_backtest_ohlcv_full(symbol_id=symbol_id, tf=tf or TIMEFRAME, max_bars=max_bars)


def _build_indicator_params(
    symbol_key: str,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_params: dict[str, Any] | None = None,
    broker_profile: str | None = None,
) -> dict[str, Any]:
    cfg = symbol_params or get_symbol_params(symbol_key, broker_profile=broker_profile)
    params = {
        **get_indicator_params(),
        "MA_PERIOD": int(cfg["ma_period"]),
        "KTP": float(cfg["ktp"]),
        "X": float(cfg["x"]),
    }
    if indicator_overrides:
        params.update(indicator_overrides)
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
    """Prepare indicators + signal column for one symbol.

    This is the last pure-data stage before order execution begins.
    """
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

    df_ind = add_combo_indicators(df_raw.copy(), params)
    if date_from or date_to:
        mask = pd.Series(True, index=df_ind.index)
        if date_from:
            mask &= df_ind.index >= _timestamp_for_index(date_from, df_ind.index)
        if date_to:
            mask &= df_ind.index <= _timestamp_for_index(date_to, df_ind.index)
        df_ind["in_window"] = mask

    sess = session_mask(df_ind, cfg.get("session_hours_utc", []))
    df_sig = detect_combo_signals(df_ind, sess, sym_key=None, params=params)
    return df_sig, cfg, params


def run_symbol_backtest_on_frame(
    symbol_key: str,
    df_raw: pd.DataFrame,
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
    collect_events: bool = False,
) -> SymbolBacktestResult:
    """Run a complete symbol backtest on a preloaded raw OHLCV frame.

    Use this when data is already cached in memory, for example inside a
    portfolio walk-forward loop.
    """
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
    strategy_cfg["trailing_activation"] = float(
        strategy_cfg.get("trailing_activation", cfg["trailing_activation"])
    )
    if "trailing_activation" not in (strategy_overrides or {}):
        strategy_cfg["trailing_activation"] = float(cfg["trailing_activation"])
    min_rr = params.get("MIN_RR")
    strategy_cfg["min_rr"] = None if min_rr is None else float(min_rr)

    replay_events = [] if collect_events else None
    trades, equity = backtest_symbol(
        symbol_key,
        df_sig,
        cfg,
        init_eq,
        strategy=strategy_cfg,
        costs=get_cost_settings(symbol_key, costs, broker_profile=broker_profile),
        event_sink=replay_events,
    )
    metrics = calc_metrics(
        trades,
        equity,
        window_start=date_from,
        window_end=date_to,
        initial_equity=init_eq,
    )

    return SymbolBacktestResult(
        symbol=symbol_key,
        raw_data=df_raw,
        signal_data=df_sig,
        trades=trades,
        equity=equity,
        metrics=metrics,
        symbol_config=cfg,
        strategy_settings=strategy_cfg,
        account_mode=account_mode,
        replay_events=replay_events or [],
    )


def run_symbol_backtest(
    symbol_key: str,
    init_eq: float = 100_000.0,
    *,
    account_mode: str = "standard",
    date_from: str | None = None,
    date_to: str | None = None,
    tf: str | None = None,
    max_bars: int = 60000,
    warmup: int | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    collect_events: bool = False,
) -> SymbolBacktestResult:
    """Load data from DB and run a complete symbol backtest."""
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
    )
    return run_symbol_backtest_on_frame(
        symbol_key,
        df_raw,
        init_eq=init_eq,
        account_mode=account_mode,
        date_from=date_from,
        date_to=date_to,
        indicator_overrides=indicator_overrides,
        symbol_overrides=symbol_overrides,
        strategy_overrides=strategy_overrides,
        costs=costs,
        broker_profile=broker_profile,
        collect_events=collect_events,
    )
