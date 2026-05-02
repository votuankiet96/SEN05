"""cTrader validation export adapter for Combo research notebooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from core_python.shared.ctrader_export import (
    lot_to_volume_units,
    price_distance_to_pips,
    timeframe_to_timedelta,
    trades_to_export_frame,
    write_ctrader_validation_bundle,
)

from ..config import TIMEFRAME
from ..orders import create_order_intent_from_signal


def build_ctrader_orders_from_symbol_result(
    result: Any,
    run_config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Build cTrader order rows from one Combo symbol backtest result."""
    cfg = dict(_get(result, "symbol_config", {}) or {})
    strategy = dict(_get(result, "strategy_settings", {}) or {})
    trades = trades_to_export_frame(_get(result, "trades", []))
    signal_data = _signal_frame(_get(result, "signal_data", pd.DataFrame()))
    symbol = str(_get(result, "symbol", "") or cfg.get("symbol") or "")
    timeframe = _timeframe(run_config, cfg)
    account_mode = str(_get(result, "account_mode", "") or (run_config or {}).get("account_mode", ""))
    lot_to_units = _lot_to_units(run_config, cfg)

    rows: list[dict[str, Any]] = []
    for trade_index, trade in trades.reset_index(drop=True).iterrows():
        direction = _direction_int(trade.get("direction"))
        if direction == 0:
            continue
        signal_row, intent = _match_signal_for_trade(signal_data, trade, symbol, cfg, strategy, direction)
        signal_time = _bar_time(signal_row) if signal_row is not None else pd.NaT
        execution_time = pd.to_datetime(trade.get("entry_time"), errors="coerce")
        expiry_bars = int(strategy.get("pending_ttl_bars", cfg.get("pending_ttl_bars", 3)))
        expiration_time = _expiration_time(signal_time, timeframe, expiry_bars)
        lot_size = _float_or_none(trade.get("lot_size"))
        entry_price = _float_or_none(getattr(intent, "entry", None)) or _float_or_none(trade.get("entry"))
        stop_loss = _float_or_none(getattr(intent, "sl", None)) or _float_or_none(trade.get("sl_initial"))
        take_profit = _float_or_none(getattr(intent, "tp", None)) or _float_or_none(trade.get("tp"))

        rows.append(
            {
                "signal_id": f"combo-{symbol}-{trade_index + 1}",
                "strategy": "combo",
                "symbol": symbol,
                "timeframe": timeframe,
                "account_mode": account_mode,
                "signal_time_utc": signal_time,
                "execution_time_utc": execution_time,
                "direction": "BUY" if direction > 0 else "SELL",
                "direction_int": direction,
                "order_type": "PENDING_STOP",
                "volume_units": lot_to_volume_units(lot_size, lot_to_units=lot_to_units),
                "lot_size": lot_size,
                "label": f"combo_{symbol}",
                "comment": f"combo|source=python_backtest|trade={trade_index + 1}",
                "protection_type": "Absolute",
                "entry_price": entry_price,
                "expected_entry_price": _float_or_none(trade.get("entry")),
                "stop_loss_price": stop_loss,
                "take_profit_price": take_profit,
                "stop_loss_pips": price_distance_to_pips(entry_price, stop_loss, point_size=cfg.get("point_size", 1.0)),
                "take_profit_pips": price_distance_to_pips(entry_price, take_profit, point_size=cfg.get("point_size", 1.0)),
                "expiration_time_utc": expiration_time,
                "expiry_bars": expiry_bars,
                "has_trailing_stop": bool(strategy.get("trailing_activation", cfg.get("trailing_activation", 0))),
                "signal_open": _bar_value(signal_row, "open"),
                "signal_high": _bar_value(signal_row, "high"),
                "signal_low": _bar_value(signal_row, "low"),
                "signal_close": _bar_value(signal_row, "close"),
                "execution_open": np.nan,
                "atr": _bar_value(signal_row, "atr"),
                "spread_pts": cfg.get("spread_pts"),
                "slippage_pts": cfg.get("slippage_pts"),
                "x": cfg.get("x"),
                "ktp": strategy.get("ktp", cfg.get("ktp")),
                "source_trade_index": trade_index,
                "expected_exit_time_utc": pd.to_datetime(trade.get("exit_time"), errors="coerce"),
                "expected_exit_price": _float_or_none(trade.get("exit")),
                "expected_exit_reason": trade.get("exit_reason"),
                "expected_pnl_usd": _float_or_none(trade.get("pnl_usd")),
            }
        )
    return pd.DataFrame(rows)


def build_ctrader_orders_from_portfolio_result(
    portfolio: Any,
    run_config: Mapping[str, Any] | None = None,
) -> pd.DataFrame:
    """Build cTrader order rows from a Combo portfolio backtest result."""
    results = _get(portfolio, "symbol_results", {}) or {}
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
    """Export Combo cTrader orders plus Python audit files."""
    if _get(result_or_portfolio, "symbol_results", None) is not None:
        orders = build_ctrader_orders_from_portfolio_result(result_or_portfolio, run_config=run_config)
        trades = _get(result_or_portfolio, "trades", None)
        equity = _get(result_or_portfolio, "combined_equity", None)
        signals = _portfolio_signals(result_or_portfolio)
        default_name = f"portfolio_{_config_value(run_config, 'account_mode', _get(result_or_portfolio, 'account_mode', 'standard'))}_backtest"
    else:
        orders = build_ctrader_orders_from_symbol_result(result_or_portfolio, run_config=run_config)
        trades = _get(result_or_portfolio, "trades", None)
        equity = _get(result_or_portfolio, "equity", None)
        signals = _get(result_or_portfolio, "signal_data", None)
        symbol = _get(result_or_portfolio, "symbol", _config_value(run_config, "symbol", "symbol"))
        account = _config_value(run_config, "account_mode", _get(result_or_portfolio, "account_mode", "standard"))
        default_name = f"{symbol}_{account}_symbol_backtest"

    return write_ctrader_validation_bundle(
        strategy="combo",
        name=name or default_name,
        orders=orders,
        trades=trades,
        equity=equity,
        signals=signals,
        run_config=run_config,
        output_dir=output_dir,
    )


def _match_signal_for_trade(
    signals: pd.DataFrame,
    trade: pd.Series,
    symbol: str,
    cfg: dict[str, Any],
    strategy: dict[str, Any],
    direction: int,
) -> tuple[pd.Series | None, Any | None]:
    if signals.empty:
        return None, None
    entry_time = pd.to_datetime(trade.get("entry_time"), errors="coerce")
    candidates = signals[signals["signal"].fillna(0).astype(int).eq(direction)]
    if pd.notna(entry_time):
        candidates = candidates[pd.to_datetime(candidates["BarTime"], errors="coerce").le(entry_time)]
    if candidates.empty:
        return None, None

    best_row = None
    best_intent = None
    best_score = float("inf")
    trade_sl = _float_or_none(trade.get("sl_initial"))
    trade_tp = _float_or_none(trade.get("tp"))
    for _, row in candidates.tail(10).iterrows():
        intent = create_order_intent_from_signal(symbol, row, cfg, strategy)
        if intent is None:
            continue
        score = 0.0
        if trade_sl is not None and intent.sl is not None:
            score += abs(float(intent.sl) - trade_sl)
        if trade_tp is not None and intent.tp is not None:
            score += abs(float(intent.tp) - trade_tp)
        if score <= best_score:
            best_row = row
            best_intent = intent
            best_score = score
    if best_row is not None:
        return best_row, best_intent
    row = candidates.iloc[-1]
    return row, create_order_intent_from_signal(symbol, row, cfg, strategy)


def _signal_frame(value: Any) -> pd.DataFrame:
    if not isinstance(value, pd.DataFrame) or value.empty:
        return pd.DataFrame()
    frame = value.copy()
    if "BarTime" not in frame.columns:
        frame["BarTime"] = frame.index
    return frame.reset_index(drop=True)


def _portfolio_signals(portfolio: Any) -> pd.DataFrame:
    frames = []
    for symbol, result in (_get(portfolio, "symbol_results", {}) or {}).items():
        frame = _signal_frame(_get(result, "signal_data", pd.DataFrame()))
        if not frame.empty:
            frame.insert(0, "symbol", symbol)
            frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _expiration_time(signal_time: Any, timeframe: str, expiry_bars: int) -> pd.Timestamp | None:
    ts = pd.to_datetime(signal_time, errors="coerce")
    delta = timeframe_to_timedelta(timeframe)
    if pd.isna(ts) or delta is None:
        return None
    return ts + delta * int(expiry_bars)


def _timeframe(run_config: Mapping[str, Any] | None, cfg: Mapping[str, Any]) -> str:
    return str(_config_value(run_config, "tf", cfg.get("timeframe", TIMEFRAME))).upper()


def _lot_to_units(run_config: Mapping[str, Any] | None, cfg: Mapping[str, Any]) -> float:
    return float(_config_value(run_config, "lot_to_units", cfg.get("lot_to_units", 100_000)))


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
