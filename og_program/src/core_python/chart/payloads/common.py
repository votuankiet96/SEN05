"""Shared dashboard payload primitives.

This module owns the strategy-agnostic chart contract consumed by app.js:
candles, markers, entry/SL/TP levels, signal table base columns, and stats.
Strategy-specific modules only decide which overlays, indicator panels, and
extra signal-table columns should be attached.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from core_python.chart.payloads.helpers import (
    candlestick_points,
    clean_params,
    to_number,
    to_unix_ts,
)

MAX_SIGNAL_ROWS = 120
ExtraSignalColumn = tuple[str, str, int | None]


def signal_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Return rows that contain BUY/SELL signals."""
    if "signal" not in df.columns:
        return pd.DataFrame(columns=df.columns)
    return df[df["signal"].fillna(0).astype(int).ne(0)].copy()


def markers(signals: pd.DataFrame) -> list[dict[str, Any]]:
    """Build Lightweight Charts BUY/SELL markers for the main OHLC chart."""
    output: list[dict[str, Any]] = []
    for _, row in signals.iterrows():
        direction = int(row["signal"])
        is_buy = direction == 1
        output.append(
            {
                "time": to_unix_ts(row["bartime"]),
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#16a34a" if is_buy else "#dc2626",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": "BUY" if is_buy else "SELL",
            }
        )
    return output


def levels(df: pd.DataFrame, signals: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Build Entry/SL/TP level lines for the main OHLC chart."""
    if not bool(params.get("SHOW_LEVELS", True)):
        return []

    index_by_time = {to_unix_ts(row["bartime"]): idx for idx, row in df.iterrows()}
    times = [to_unix_ts(value) for value in df["bartime"]]
    line_bars = int(params.get("ENTRY_LINE_BARS", 20))
    output: list[dict[str, Any]] = []

    for _, row in signals.iterrows():
        start_time = to_unix_ts(row["entry_time"] if pd.notna(row.get("entry_time")) else row["bartime"])
        start_idx = index_by_time.get(start_time)
        if start_idx is None:
            start_idx = index_by_time.get(to_unix_ts(row["bartime"]))
        if start_idx is None or not times:
            continue
        end_time = times[min(start_idx + line_bars, len(times) - 1)]

        direction = int(row["signal"])
        prefix = "BUY" if direction == 1 else "SELL"
        for key, kind, color, style in (
            ("entry_price", "entry", "#e5e7eb", "dashed"),
            ("sl_price", "sl", "#ef4444", "dotted"),
            ("tp_price", "tp", "#22c55e", "dotted"),
        ):
            value = to_number(row.get(key))
            if value is None:
                continue
            output.append(
                {
                    "kind": kind,
                    "label": f"{prefix} {kind.upper()}",
                    "timeStart": start_time,
                    "timeEnd": end_time,
                    "price": value,
                    "color": color,
                    "style": style,
                }
            )
    return output


def signal_table(signals: pd.DataFrame, extra_columns: Iterable[ExtraSignalColumn]) -> list[dict[str, Any]]:
    """Build signal-table rows with common columns plus strategy-specific extras."""
    output: list[dict[str, Any]] = []
    for _, row in signals.tail(MAX_SIGNAL_ROWS).iterrows():
        direction = int(row["signal"])
        item: dict[str, Any] = {
            "bartime": pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M"),
            "side": "BUY" if direction == 1 else "SELL",
            "reason": row.get("signal_reason", ""),
            "entry": to_number(row.get("entry_price"), 5),
            "sl": to_number(row.get("sl_price"), 5),
            "tp": to_number(row.get("tp_price"), 5),
            "rr": to_number(row.get("risk_reward"), 2),
            "atr": to_number(row.get("atr"), 5),
        }
        for output_key, source_column, digits in extra_columns:
            item[output_key] = to_number(row.get(source_column), digits)
        output.append(item)
    return output


def stats(signals: pd.DataFrame) -> dict[str, Any]:
    """Return signal count summary."""
    if signals.empty:
        return {"total": 0, "buy": 0, "sell": 0, "last": "-"}
    buy = int(signals["signal"].eq(1).sum())
    sell = int(signals["signal"].eq(-1).sum())
    last_signal = int(signals["signal"].iloc[-1])
    return {
        "total": int(len(signals)),
        "buy": buy,
        "sell": sell,
        "last": "BUY" if last_signal == 1 else "SELL",
    }


def build_strategy_payload(
    df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
    overlays: list[dict[str, Any]],
    panels: list[dict[str, Any]],
    signal_table_rows: list[dict[str, Any]],
    signals: pd.DataFrame,
) -> dict[str, Any]:
    """Assemble the shared dashboard payload envelope."""
    return {
        "meta": {
            "strategy": strategy,
            "strategyLabel": strategy_label,
            "symbol": symbol,
            "tf": tf,
            "bars": bars,
        },
        "params": clean_params(params),
        "candles": candlestick_points(df),
        "overlays": overlays,
        "panels": panels,
        "markers": markers(signals),
        "levels": levels(df, signals, params),
        "signals": signal_table_rows,
        "stats": stats(signals),
    }
