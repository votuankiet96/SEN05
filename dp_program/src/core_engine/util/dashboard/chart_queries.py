"""Read-only OHLCV chart queries.

This module deliberately does not import or call TradingView code. It only
reads already-stored warehouse data so the chart viewer cannot affect live or
historical collection.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from core_engine.shared.warehouse.connection import get_connection
from core_engine.settings import SYMBOLS, TF_DISPLAY_ORDER


_ASSET_GROUP_MAP = {
    "Indice": "Indices",
    "FOREX": "FOREX",
    "Metal": "Metal & Crypto",
    "Crypto": "Metal & Crypto",
}
_GROUP_ORDER = ["Indices", "FOREX", "Metal & Crypto"]
_ALLOWED_SYMBOLS = {str(item["tv_symbol"]).upper() for item in SYMBOLS}
_ALLOWED_TFS = {tf.upper() for tf in TF_DISPLAY_ORDER}


def _read_sql(query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        rows = cursor.fetchall()
        cursor.close()
        return [dict(zip(columns, row)) for row in rows]
    finally:
        conn.close()


def _to_json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        else:
            value = value.astimezone(timezone.utc)
        return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return float(value)
    return value


def _unix_seconds(value: Any) -> int:
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def load_symbols() -> dict[str, list[str]]:
    """Return symbols grouped for operator browsing.

    The source of truth is the configured instrument list. The DB may have fewer
    rows during first setup, but the UI should still show the configured scope.
    """
    groups: dict[str, list[str]] = {name: [] for name in _GROUP_ORDER}
    for item in SYMBOLS:
        group = _ASSET_GROUP_MAP.get(str(item.get("asset_type") or ""))
        symbol = str(item.get("tv_symbol") or "").upper()
        if group and symbol:
            groups.setdefault(group, []).append(symbol)
    return {group: sorted(values) for group, values in groups.items() if values}


def load_timeframes() -> dict[str, list[str]]:
    return {"direct": list(TF_DISPLAY_ORDER), "computed": []}


def load_candles(symbol: str, timeframe: str, bars: int) -> dict[str, Any]:
    symbol = str(symbol or "").strip().upper()
    timeframe = str(timeframe or "").strip().upper()
    if symbol not in _ALLOWED_SYMBOLS:
        raise ValueError(f"Unknown symbol: {symbol}")
    if timeframe not in _ALLOWED_TFS:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    bars = max(50, min(int(bars), 5000))

    rows = _read_sql(
        """
        SELECT TOP (?)
               f.BarTime,
               f.[Open],
               f.High,
               f.Low,
               f.[Close],
               f.Volume
        FROM DWH.Fact_OHLCV f WITH (NOLOCK)
        JOIN DWH.Dim_Symbol s WITH (NOLOCK)
          ON s.SymbolID = f.SymbolID
        JOIN DWH.Dim_Timeframe tf WITH (NOLOCK)
          ON tf.TimeframeID = f.TimeframeID
        WHERE s.Symbol = ?
          AND tf.Code = ?
        ORDER BY f.BarTime DESC
        """,
        (bars, symbol, timeframe),
    )
    candles = []
    for row in reversed(rows):
        volume = row.get("Volume")
        candles.append(
            {
                "time": _unix_seconds(row["BarTime"]),
                "bar_time": _to_json_value(row["BarTime"]),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(volume) if volume is not None else 0.0,
            }
        )
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "bars_requested": bars,
        "candles": candles,
    }

