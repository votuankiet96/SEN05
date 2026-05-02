"""Shared cTrader validation CSV helpers.

This module owns the export schema and file-writing mechanics only. Strategy
packages are responsible for translating their own backtest results into order
rows that match this schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd


CTRADER_ORDER_COLUMNS = [
    "signal_id",
    "strategy",
    "symbol",
    "timeframe",
    "account_mode",
    "signal_time_utc",
    "execution_time_utc",
    "direction",
    "direction_int",
    "order_type",
    "volume_units",
    "lot_size",
    "label",
    "comment",
    "protection_type",
    "entry_price",
    "expected_entry_price",
    "stop_loss_price",
    "take_profit_price",
    "stop_loss_pips",
    "take_profit_pips",
    "expiration_time_utc",
    "expiry_bars",
    "has_trailing_stop",
    "signal_open",
    "signal_high",
    "signal_low",
    "signal_close",
    "execution_open",
    "atr",
    "spread_pts",
    "slippage_pts",
    "x",
    "ktp",
    "atr_stop_mult",
    "atr_tp_mult",
    "source_trade_index",
    "expected_exit_time_utc",
    "expected_exit_price",
    "expected_exit_reason",
    "expected_pnl_usd",
]

REQUIRED_ORDER_COLUMNS = [
    "signal_id",
    "strategy",
    "symbol",
    "execution_time_utc",
    "direction",
    "order_type",
    "volume_units",
]

TRADE_TIME_COLUMNS = ["entry_time", "exit_time", "half1_exit_time"]
SIGNAL_TIME_COLUMNS = ["BarTime", "bar_time", "time"]


def normalize_ctrader_orders(frame: pd.DataFrame | list[Mapping[str, Any]] | None) -> pd.DataFrame:
    """Return an order frame with the shared cTrader validation schema."""
    out = _as_frame(frame)
    for col in CTRADER_ORDER_COLUMNS:
        if col not in out.columns:
            out[col] = np.nan
    out = out[CTRADER_ORDER_COLUMNS + [c for c in out.columns if c not in CTRADER_ORDER_COLUMNS]]
    for col in ("signal_time_utc", "execution_time_utc", "expiration_time_utc", "expected_exit_time_utc"):
        out[col] = _iso_datetime_series(out[col])
    if "direction" in out:
        out["direction"] = out["direction"].map(_side_label)
    return out


def validate_ctrader_orders(frame: pd.DataFrame | list[Mapping[str, Any]] | None) -> dict[str, Any]:
    """Return a compact validation report for a cTrader order CSV."""
    orders = normalize_ctrader_orders(frame)
    missing_columns = [col for col in REQUIRED_ORDER_COLUMNS if col not in orders.columns]
    blank_required = {
        col: int(orders[col].isna().sum() + orders[col].astype(str).str.strip().eq("").sum())
        for col in REQUIRED_ORDER_COLUMNS
        if col in orders.columns
    }
    pending = orders["order_type"].astype(str).str.upper().eq("PENDING_STOP") if "order_type" in orders else pd.Series(dtype=bool)
    market = orders["order_type"].astype(str).str.upper().str.startswith("MARKET") if "order_type" in orders else pd.Series(dtype=bool)
    return {
        "ok": not missing_columns and len(orders) > 0 and all(v == 0 for v in blank_required.values()),
        "rows": int(len(orders)),
        "missing_columns": missing_columns,
        "blank_required": blank_required,
        "pending_rows": int(pending.sum()) if len(orders) else 0,
        "market_rows": int(market.sum()) if len(orders) else 0,
        "pending_without_entry": int(orders.loc[pending, "entry_price"].isna().sum()) if len(orders) else 0,
        "market_without_sl_pips": int(orders.loc[market, "stop_loss_pips"].isna().sum()) if len(orders) else 0,
    }


def write_ctrader_validation_bundle(
    *,
    strategy: str,
    name: str,
    orders: pd.DataFrame | list[Mapping[str, Any]],
    trades: Any = None,
    equity: Any = None,
    signals: Any = None,
    run_config: Mapping[str, Any] | None = None,
    output_dir: str | Path = "output/ctrader_validation",
) -> Path:
    """Write a complete validation bundle and return its output directory."""
    out = Path(output_dir) / _safe_name(strategy) / _safe_name(name)
    out.mkdir(parents=True, exist_ok=True)

    order_frame = normalize_ctrader_orders(orders)
    order_frame.to_csv(out / "ctrader_orders.csv", index=False, encoding="utf-8-sig")

    trade_frame = trades_to_export_frame(trades)
    if not trade_frame.empty:
        trade_frame.to_csv(out / "python_backtest_trades.csv", index=False, encoding="utf-8-sig")

    equity_frame = equity_to_export_frame(equity)
    if not equity_frame.empty:
        equity_frame.to_csv(out / "python_backtest_equity.csv", index=False, encoding="utf-8-sig")

    signal_frame = signals_to_export_frame(signals)
    if not signal_frame.empty:
        signal_frame.to_csv(out / "python_signals.csv", index=False, encoding="utf-8-sig")

    config_frame = run_config_to_frame(run_config or {})
    if not config_frame.empty:
        config_frame.to_csv(out / "run_config.csv", index=False, encoding="utf-8-sig")

    validation = validate_ctrader_orders(order_frame)
    pd.DataFrame([_json_safe(validation)]).to_csv(out / "validation_summary.csv", index=False, encoding="utf-8-sig")
    print(f"Exported cTrader validation bundle to: {out}")
    return out


def trades_to_export_frame(trades: Any) -> pd.DataFrame:
    """Normalize a trade log to a CSV-friendly DataFrame."""
    frame = _as_frame(trades)
    if frame.empty:
        return frame
    for col in TRADE_TIME_COLUMNS:
        if col in frame.columns:
            frame[col] = _iso_datetime_series(frame[col])
    return _jsonify_object_columns(frame)


def equity_to_export_frame(equity: Any) -> pd.DataFrame:
    """Normalize an equity Series/DataFrame for CSV export."""
    if isinstance(equity, pd.Series):
        frame = equity.rename("equity").to_frame().reset_index()
    elif isinstance(equity, pd.DataFrame):
        frame = equity.copy().reset_index()
    else:
        return pd.DataFrame()
    for col in frame.columns:
        if "time" in str(col).lower() or str(col).lower() in {"index", "bartime", "date"}:
            frame[col] = _iso_datetime_series(frame[col])
    return _jsonify_object_columns(frame)


def signals_to_export_frame(signals: Any, *, nonzero_only: bool = True) -> pd.DataFrame:
    """Normalize signal data for audit export."""
    frame = _as_frame(signals)
    if frame.empty:
        return frame
    if isinstance(signals, pd.DataFrame):
        frame = signals.copy()
        if isinstance(frame.index, pd.DatetimeIndex):
            frame = frame.reset_index()
    if nonzero_only and "signal" in frame.columns:
        frame = frame[frame["signal"].fillna(0).astype(int).ne(0)]
    for col in SIGNAL_TIME_COLUMNS:
        if col in frame.columns:
            frame[col] = _iso_datetime_series(frame[col])
    return _jsonify_object_columns(frame.reset_index(drop=True))


def run_config_to_frame(config: Mapping[str, Any] | None) -> pd.DataFrame:
    """Flatten a run configuration mapping into key/value CSV rows."""
    rows: list[dict[str, Any]] = []
    for key, value in _flatten_mapping(config or {}).items():
        rows.append({"parameter": key, "value": _stringify(value)})
    return pd.DataFrame(rows)


def timeframe_to_timedelta(timeframe: Any) -> pd.Timedelta | None:
    """Parse common timeframe codes like M30, H1, D1 into a Timedelta."""
    if timeframe is None:
        return None
    text = str(timeframe).strip().upper()
    match = re.fullmatch(r"([MHDW])(\d+)", text)
    if not match:
        return None
    unit, value_s = match.groups()
    value = int(value_s)
    if unit == "M":
        return pd.Timedelta(minutes=value)
    if unit == "H":
        return pd.Timedelta(hours=value)
    if unit == "D":
        return pd.Timedelta(days=value)
    if unit == "W":
        return pd.Timedelta(weeks=value)
    return None


def lot_to_volume_units(lot_size: Any, *, lot_to_units: float | int | None = None) -> float | None:
    """Convert lot size to cTrader volume units using a configurable ratio."""
    try:
        lot = float(lot_size)
    except Exception:
        return None
    if not np.isfinite(lot):
        return None
    multiplier = 100_000.0 if lot_to_units is None else float(lot_to_units)
    return round(lot * multiplier, 2)


def price_distance_to_pips(entry: Any, price: Any, *, point_size: Any = 1.0) -> float | None:
    """Convert an absolute price distance into pips/points for cTrader orders."""
    try:
        entry_f = float(entry)
        price_f = float(price)
        point = float(point_size or 1.0)
    except Exception:
        return None
    if not all(np.isfinite(v) for v in (entry_f, price_f, point)) or point <= 0:
        return None
    return round(abs(entry_f - price_f) / point, 8)


def _as_frame(value: Any) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame()
    if isinstance(value, pd.DataFrame):
        return value.copy()
    if isinstance(value, pd.Series):
        return value.to_frame()
    try:
        return pd.DataFrame(list(value))
    except Exception:
        return pd.DataFrame()


def _iso_datetime_series(series: Any) -> pd.Series:
    values = pd.Series(pd.to_datetime(series, errors="coerce"))
    try:
        if values.dt.tz is not None:
            values = values.dt.tz_convert(None)
    except TypeError:
        pass
    return values.dt.strftime("%Y-%m-%dT%H:%M:%S")


def _side_label(value: Any) -> str:
    text = str(value).strip().upper()
    if text in {"BUY", "LONG", "1"}:
        return "BUY"
    if text in {"SELL", "SHORT", "-1"}:
        return "SELL"
    try:
        return "BUY" if int(value) > 0 else "SELL"
    except Exception:
        return text


def _jsonify_object_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in out.columns:
        if out[col].map(lambda v: isinstance(v, (dict, list, tuple))).any():
            out[col] = out[col].map(_stringify)
    return out


def _flatten_mapping(mapping: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(_flatten_mapping(value, full_key))
        else:
            out[full_key] = value
    return out


def _stringify(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return "" if value is None else str(value)


def _json_safe(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _stringify(val) if isinstance(val, (dict, list, tuple)) else val for key, val in value.items()}


def _safe_name(value: Any) -> str:
    text = str(value or "export").strip()
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return text.strip("._") or "export"


__all__ = [
    "CTRADER_ORDER_COLUMNS",
    "REQUIRED_ORDER_COLUMNS",
    "equity_to_export_frame",
    "lot_to_volume_units",
    "normalize_ctrader_orders",
    "price_distance_to_pips",
    "run_config_to_frame",
    "signals_to_export_frame",
    "timeframe_to_timedelta",
    "trades_to_export_frame",
    "validate_ctrader_orders",
    "write_ctrader_validation_bundle",
]
