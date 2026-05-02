"""cTrader validation export adapter for MA Cross research notebooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from core_python.shared.ctrader_export import (
    lot_to_volume_units,
    price_distance_to_pips,
    write_ctrader_validation_bundle,
)

from ..config import TIMEFRAME


def build_ctrader_orders_from_symbol_result(
    result: Any,
    run_config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Build cTrader order rows from one MA Cross symbol backtest result."""
    cfg = dict(_get(result, "symbol_config", {}) or {})
    strategy = dict(_get(result, "strategy_settings", {}) or {})
    trades = _raw_trades_frame(_get(result, "trades", []))
    signal_data = _signal_frame(_get(result, "signal_data", pd.DataFrame()))
    symbol = str(_get(result, "symbol", "") or cfg.get("symbol") or "")
    timeframe = str(_config_value(run_config, "tf", cfg.get("timeframe", TIMEFRAME))).upper()
    account_mode = str(_get(result, "account_mode", "") or (run_config or {}).get("account_mode", ""))
    lot_to_units = float(_config_value(run_config, "lot_to_units", cfg.get("lot_to_units", 100_000)))
    point_size = cfg.get("point_size", 1.0)

    rows: list[dict[str, Any]] = []
    for trade_index, trade in trades.reset_index(drop=True).iterrows():
        child_orders = trade.get("child_orders")
        if isinstance(child_orders, str):
            child_orders = None
        if isinstance(child_orders, list) and child_orders:
            for child_index, child in enumerate(child_orders):
                rows.append(
                    _market_order_row(
                        trade=trade,
                        source_trade_index=trade_index,
                        child_order=child,
                        child_index=child_index,
                        signal_data=signal_data,
                        symbol=symbol,
                        timeframe=timeframe,
                        account_mode=account_mode,
                        strategy=strategy,
                        cfg=cfg,
                        lot_to_units=lot_to_units,
                        point_size=point_size,
                    )
                )
        else:
            rows.append(
                _market_order_row(
                    trade=trade,
                    source_trade_index=trade_index,
                    child_order=None,
                    child_index=None,
                    signal_data=signal_data,
                    symbol=symbol,
                    timeframe=timeframe,
                    account_mode=account_mode,
                    strategy=strategy,
                    cfg=cfg,
                    lot_to_units=lot_to_units,
                    point_size=point_size,
                )
            )
    return pd.DataFrame(rows)


def build_ctrader_orders_from_portfolio_result(
    portfolio: Any,
    run_config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Build cTrader order rows from an MA Cross portfolio backtest result."""
    results = _get(portfolio, "results", None) or _get(portfolio, "symbol_results", {}) or {}
    frames = [
        build_ctrader_orders_from_symbol_result(result, run_config=run_config)
        for result in results.values()
    ]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def export_ctrader_validation_bundle(
    result_or_portfolio: Any,
    run_config: Mapping[str, Any] | None = None,
    *,
    name: str | None = None,
    output_dir: str | Path = "output/ctrader_validation",
) -> Path:
    """Export MA Cross cTrader orders plus Python audit files."""
    is_portfolio = _get(result_or_portfolio, "results", None) is not None or _get(result_or_portfolio, "symbol_results", None) is not None
    if is_portfolio:
        orders = build_ctrader_orders_from_portfolio_result(result_or_portfolio, run_config=run_config)
        trades = _get(result_or_portfolio, "trades", None)
        equity = _get(result_or_portfolio, "combined_equity", None)
        signals = _portfolio_signals(result_or_portfolio)
        default_name = f"portfolio_{_config_value(run_config, 'account_mode', 'standard')}_backtest"
    else:
        orders = build_ctrader_orders_from_symbol_result(result_or_portfolio, run_config=run_config)
        trades = _get(result_or_portfolio, "trades", None)
        equity = _get(result_or_portfolio, "equity", None)
        signals = _get(result_or_portfolio, "signal_data", None)
        symbol = _get(result_or_portfolio, "symbol", _config_value(run_config, "symbol", "symbol"))
        account = _config_value(run_config, "account_mode", _get(result_or_portfolio, "account_mode", "standard"))
        default_name = f"{symbol}_{account}_symbol_backtest"

    return write_ctrader_validation_bundle(
        strategy="ma_cross",
        name=name or default_name,
        orders=orders,
        trades=trades,
        equity=equity,
        signals=signals,
        run_config=run_config,
        output_dir=output_dir,
    )


def _market_order_row(
    *,
    trade: pd.Series,
    source_trade_index: int,
    child_order: Mapping[str, Any] | None,
    child_index: int | None,
    signal_data: pd.DataFrame,
    symbol: str,
    timeframe: str,
    account_mode: str,
    strategy: Mapping[str, Any],
    cfg: Mapping[str, Any],
    lot_to_units: float,
    point_size: Any,
) -> dict[str, Any]:
    source = child_order or trade
    direction = _direction_int(source.get("direction", trade.get("direction")))
    execution_time = pd.to_datetime(source.get("entry_time", trade.get("entry_time")), errors="coerce")
    signal_row = _match_signal_for_execution(signal_data, execution_time, direction)
    entry = _float_or_none(source.get("entry", trade.get("entry")))
    stop_loss = _float_or_none(trade.get("sl"))
    take_profit = _float_or_none(trade.get("tp"))
    if take_profit is not None and not np.isfinite(take_profit):
        take_profit = None
    lot_size = _float_or_none(source.get("lot_size", trade.get("lot_size")))
    suffix = f"-child{child_index + 1}" if child_index is not None else ""
    signal_time = _bar_time(signal_row)

    return {
        "signal_id": f"ma_cross-{symbol}-{source_trade_index + 1}{suffix}",
        "strategy": "ma_cross",
        "symbol": symbol,
        "timeframe": timeframe,
        "account_mode": account_mode,
        "signal_time_utc": signal_time,
        "execution_time_utc": execution_time,
        "direction": "BUY" if direction > 0 else "SELL",
        "direction_int": direction,
        "order_type": "MARKET_NEXT_OPEN",
        "volume_units": lot_to_volume_units(lot_size, lot_to_units=lot_to_units),
        "lot_size": lot_size,
        "label": f"ma_cross_{symbol}",
        "comment": f"ma_cross|source=python_backtest|trade={source_trade_index + 1}{suffix}",
        "protection_type": "Relative",
        "entry_price": np.nan,
        "expected_entry_price": entry,
        "stop_loss_price": stop_loss,
        "take_profit_price": take_profit,
        "stop_loss_pips": price_distance_to_pips(entry, stop_loss, point_size=point_size),
        "take_profit_pips": price_distance_to_pips(entry, take_profit, point_size=point_size),
        "expiration_time_utc": None,
        "expiry_bars": np.nan,
        "has_trailing_stop": bool(strategy.get("trailing_activation", 0)),
        "signal_open": _bar_value(signal_row, "open"),
        "signal_high": _bar_value(signal_row, "high"),
        "signal_low": _bar_value(signal_row, "low"),
        "signal_close": _bar_value(signal_row, "close"),
        "execution_open": _execution_open(signal_data, execution_time),
        "atr": _bar_value(signal_row, "atr"),
        "spread_pts": cfg.get("spread_pts"),
        "slippage_pts": cfg.get("slippage_pts"),
        "atr_stop_mult": strategy.get("atr_stop_mult"),
        "atr_tp_mult": strategy.get("atr_tp_mult"),
        "source_trade_index": source_trade_index,
        "expected_exit_time_utc": pd.to_datetime(trade.get("exit_time"), errors="coerce"),
        "expected_exit_price": _float_or_none(trade.get("exit")),
        "expected_exit_reason": trade.get("exit_reason"),
        "expected_pnl_usd": _float_or_none(trade.get("pnl_usd")),
    }


def _match_signal_for_execution(signal_data: pd.DataFrame, execution_time: pd.Timestamp, direction: int) -> pd.Series | None:
    if signal_data.empty or pd.isna(execution_time):
        return None
    signal_col = "entry_signal" if "entry_signal" in signal_data.columns else "signal"
    candidates = signal_data[signal_data[signal_col].fillna(0).astype(int).eq(direction)]
    candidates = candidates[pd.to_datetime(candidates["BarTime"], errors="coerce").lt(execution_time)]
    if candidates.empty:
        return None
    return candidates.iloc[-1]


def _execution_open(signal_data: pd.DataFrame, execution_time: pd.Timestamp) -> Any:
    if signal_data.empty or pd.isna(execution_time):
        return np.nan
    times = pd.to_datetime(signal_data["BarTime"], errors="coerce")
    matches = signal_data[times.eq(execution_time)]
    if matches.empty:
        return np.nan
    return matches.iloc[0].get("open", np.nan)


def _signal_frame(value: Any) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        return pd.DataFrame()
    frame = value.copy()
    if "BarTime" not in frame.columns:
        frame["BarTime"] = frame.index
    return frame.reset_index(drop=True)


def _raw_trades_frame(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()
    return pd.DataFrame(list(value))


def _portfolio_signals(portfolio: Any) -> pd.DataFrame:
    results = _get(portfolio, "results", None) or _get(portfolio, "symbol_results", {}) or {}
    frames = []
    for symbol, result in results.items():
        frame = _signal_frame(_get(result, "signal_data", pd.DataFrame()))
        if not frame.empty:
            frame.insert(0, "symbol", symbol)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _config_value(config: Mapping[str, Any] | None, key: str, default: Any = None) -> Any:
    return (config or {}).get(key, default)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _direction_int(value: Any) -> int:
    text = str(value).strip().upper()
    if text in {"BUY", "LONG", "1"}:
        return 1
    if text in {"SELL", "SHORT", "-1"}:
        return -1
    try:
        number = int(value)
    except Exception:
        return 0
    return 1 if number > 0 else (-1 if number < 0 else 0)


def _bar_time(row: pd.Series | None) -> Any:
    if row is None:
        return pd.NaT
    return row.get("BarTime", row.name)


def _bar_value(row: pd.Series | None, key: str) -> Any:
    if row is None:
        return np.nan
    return row.get(key, np.nan)


def _float_or_none(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    return out if np.isfinite(out) else None


__all__ = [
    "build_ctrader_orders_from_portfolio_result",
    "build_ctrader_orders_from_symbol_result",
    "export_ctrader_validation_bundle",
]
