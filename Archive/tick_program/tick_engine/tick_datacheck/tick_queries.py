"""Read-only tick series queries for the tick data check viewer.

Queries per-symbol tick tables to produce BID/ASK line-chart data.
All table names are resolved from a hardcoded whitelist — no user input
is ever interpolated into SQL text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

_TICK_TABLES: dict[str, str] = {
    "FR40":   "tick.FR40",
    "DE40":   "tick.DE40",
    "HK50":   "tick.HK50",
    "J225":   "tick.J225",
    "SP35":   "tick.SP35",
    "UK100":  "tick.UK100",
    "US500":  "tick.US500",
    "US100":  "tick.US100",
    "US30":   "tick.US30",
    "GOLD":   "tick.GOLD",
    "BTCUSD": "tick.BTCUSD",
}

_ASSET_TYPES: dict[str, str] = {
    "FR40": "Indice", "DE40": "Indice", "HK50": "Indice",
    "J225": "Indice", "SP35": "Indice", "UK100": "Indice",
    "US500": "Indice", "US100": "Indice", "US30": "Indice",
    "GOLD": "Metal", "BTCUSD": "Crypto",
}

_ASSET_GROUP: dict[str, str] = {
    "Indice": "Indices",
    "Metal": "Metal & Crypto",
    "Crypto": "Metal & Crypto",
}

_WINDOWS = [
    {"label": "1h",  "minutes": 60},
    {"label": "4h",  "minutes": 240},
    {"label": "1d",  "minutes": 1440},
    {"label": "3d",  "minutes": 4320},
    {"label": "7d",  "minutes": 10080},
]


def _get_connection():
    from tick_engine.data_storage.db_connector import get_connection
    return get_connection()


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


def _unix_seconds(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.astimezone(timezone.utc).timestamp())


def load_symbols() -> dict[str, list[str]]:
    """Return symbol names grouped by asset class."""
    groups: dict[str, list[str]] = {}
    for sym, asset_type in _ASSET_TYPES.items():
        group = _ASSET_GROUP.get(asset_type, asset_type)
        groups.setdefault(group, []).append(sym)
    return {g: sorted(v) for g, v in groups.items()}


def load_windows() -> list[dict[str, Any]]:
    return _WINDOWS


def load_tick_series(symbol: str, minutes: int, max_ticks: int) -> dict[str, Any]:
    """Fetch BID/ASK/MID series for the given symbol and time window.

    Returns one point per unique second (last tick within each second wins).
    Lightweight Charts requires strictly increasing, unique timestamps.
    """
    sym = str(symbol or "").strip().upper()
    if sym not in _TICK_TABLES:
        raise ValueError(f"Unknown symbol: {sym!r}")
    minutes = max(5, min(int(minutes), 10080))
    max_ticks = max(100, min(int(max_ticks), 10000))

    table = _TICK_TABLES[sym]
    conn = _get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT TOP (?)
                TickTimeUtc,
                Bid,
                Ask,
                CAST((Bid + Ask) / 2 AS DECIMAL(19,8)) AS Mid
            FROM {table} WITH (NOLOCK)
            WHERE TickTimeUtc >= DATEADD(minute, -{minutes}, SYSUTCDATETIME())
              AND Bid IS NOT NULL
              AND Ask IS NOT NULL
            ORDER BY TickTimeUtc DESC, TickID DESC
            """,
            (max_ticks,),
        )
        columns = [col[0] for col in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
    finally:
        conn.close()

    # reverse to chronological, deduplicate by second (last tick per second wins)
    deduped: dict[int, dict] = {}
    for row in reversed(rows):
        ts = _unix_seconds(row["TickTimeUtc"])
        deduped[ts] = row

    bid_series: list[dict[str, Any]] = []
    ask_series: list[dict[str, Any]] = []
    mid_series: list[dict[str, Any]] = []

    for ts in sorted(deduped):
        row = deduped[ts]
        bid = row.get("Bid")
        ask = row.get("Ask")
        mid = row.get("Mid")
        if bid is not None:
            bid_series.append({"time": ts, "value": float(bid)})
        if ask is not None:
            ask_series.append({"time": ts, "value": float(ask)})
        if mid is not None:
            mid_series.append({"time": ts, "value": float(mid)})

    latest_row = deduped[max(deduped)] if deduped else None
    return {
        "symbol": sym,
        "minutes": minutes,
        "ticks_returned": len(deduped),
        "latest_tick_utc": _to_json_value(latest_row["TickTimeUtc"]) if latest_row else None,
        "latest_bid": float(latest_row["Bid"]) if latest_row and latest_row.get("Bid") is not None else None,
        "latest_ask": float(latest_row["Ask"]) if latest_row and latest_row.get("Ask") is not None else None,
        "bid": bid_series,
        "ask": ask_series,
        "mid": mid_series,
    }
