"""Build Lightweight Charts JSON payloads from strategy DataFrames."""

from __future__ import annotations

from typing import Any

import pandas as pd


MAX_SIGNAL_ROWS = 120


def _ts(value: object) -> int:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.timestamp())


def _num(value: object, digits: int | None = None) -> float | None:
    if value is None or pd.isna(value):
        return None
    out = float(value)
    return round(out, digits) if digits is not None else out


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            clean[key] = None
        elif isinstance(value, (str, bool, int, float, list)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def _series(df: pd.DataFrame, column: str, *, color: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _num(row.get(column))
        if value is None:
            continue
        item: dict[str, Any] = {"time": _ts(row["bartime"]), "value": value}
        if color:
            item["color"] = color
        rows.append(item)
    return rows


def _histogram(df: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        value = _num(row.get(column))
        if value is None:
            continue
        rows.append(
            {
                "time": _ts(row["bartime"]),
                "value": value,
                "color": "#22c55e" if value >= 0 else "#ef4444",
            }
        )
    return rows


def _candles(df: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {
            "time": _ts(row["bartime"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for _, row in df.iterrows()
        if pd.notna(row.get("bartime"))
    ]


def _markers(signals: pd.DataFrame) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for _, row in signals.iterrows():
        direction = int(row["signal"])
        is_buy = direction == 1
        markers.append(
            {
                "time": _ts(row["bartime"]),
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#16a34a" if is_buy else "#dc2626",
                "shape": "arrowUp" if is_buy else "arrowDown",
                "text": "BUY" if is_buy else "SELL",
            }
        )
        if pd.notna(row.get("entry_time")):
            markers.append(
                {
                    "time": _ts(row["entry_time"]),
                    "position": "belowBar" if is_buy else "aboveBar",
                    "color": "#2563eb",
                    "shape": "circle",
                    "text": "ENTRY",
                }
            )
    return markers


def _levels(df: pd.DataFrame, signals: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    if not bool(params.get("SHOW_LEVELS", True)):
        return []

    index_by_time = {_ts(row["bartime"]): idx for idx, row in df.iterrows()}
    times = [_ts(value) for value in df["bartime"]]
    line_bars = int(params.get("ENTRY_LINE_BARS", 20))
    result: list[dict[str, Any]] = []

    for _, row in signals.iterrows():
        start_time = _ts(row["entry_time"] if pd.notna(row.get("entry_time")) else row["bartime"])
        start_idx = index_by_time.get(start_time)
        if start_idx is None:
            start_idx = index_by_time.get(_ts(row["bartime"]))
        if start_idx is None or not times:
            continue
        end_time = times[min(start_idx + line_bars, len(times) - 1)]

        direction = int(row["signal"])
        prefix = "BUY" if direction == 1 else "SELL"
        for key, kind, color, style in [
            ("entry_price", "entry", "#e5e7eb", "dashed"),
            ("sl_price", "sl", "#ef4444", "dotted"),
            ("tp_price", "tp", "#22c55e", "dotted"),
        ]:
            value = _num(row.get(key))
            if value is None:
                continue
            result.append(
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
    return result


def _signal_table(signals: pd.DataFrame, strategy: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in signals.tail(MAX_SIGNAL_ROWS).iterrows():
        direction = int(row["signal"])
        item = {
            "signal_time": pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M"),
            "entry_time": (
                pd.Timestamp(row["entry_time"]).strftime("%Y-%m-%d %H:%M")
                if pd.notna(row.get("entry_time"))
                else ""
            ),
            "side": "BUY" if direction == 1 else "SELL",
            "entry": _num(row.get("entry_price"), 5),
            "sl": _num(row.get("sl_price"), 5),
            "tp": _num(row.get("tp_price"), 5),
            "atr": _num(row.get("atr"), 5),
            "rr": _num(row.get("risk_reward"), 3),
            "reason": row.get("signal_reason", ""),
        }
        if strategy == "combo":
            item["ma"] = _num(row.get("ma"), 5)
            item["macd_h"] = _num(row.get("macd_h"), 6)
        elif strategy == "ma_cross":
            item["fast_ma"] = _num(row.get("fast_ma"), 5)
            item["slow_ma"] = _num(row.get("slow_ma"), 5)
            item["ma_gap_atr"] = _num(row.get("ma_gap_atr"), 4)
        rows.append(item)
    return rows


def _stats(signals: pd.DataFrame) -> dict[str, Any]:
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


def build_chart_payload(
    df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    tf: str,
    bars: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Build a frontend-only chart payload from a fully enriched strategy frame."""
    signals = df[df["signal"].fillna(0).astype(int).ne(0)].copy()

    overlays: list[dict[str, Any]] = []
    if strategy == "combo" and params.get("SHOW_MA", True) and "ma" in df:
        overlays.append({"key": "ma", "label": f"MA {params['MA_PERIOD']}", "color": "#f59e0b", "data": _series(df, "ma")})
    if strategy == "ma_cross" and params.get("SHOW_MA", True):
        overlays.extend(
            [
                {"key": "fast_ma", "label": f"Fast {params['FAST_MA']}", "color": "#38bdf8", "data": _series(df, "fast_ma")},
                {"key": "slow_ma", "label": f"Slow {params['SLOW_MA']}", "color": "#f59e0b", "data": _series(df, "slow_ma")},
            ]
        )

    panels: list[dict[str, Any]] = []
    if strategy == "combo" and params.get("SHOW_MACD", True) and "macd_h" in df:
        panels.append({"key": "macd", "label": "MACD Histogram", "type": "histogram", "data": _histogram(df, "macd_h")})
    if params.get("SHOW_ATR", True) and "atr" in df:
        panels.append({"key": "atr", "label": f"ATR {params['ATR_PERIOD']}", "type": "line", "color": "#a855f7", "data": _series(df, "atr")})

    return {
        "meta": {
            "strategy": strategy,
            "strategyLabel": strategy_label,
            "symbol": symbol,
            "tf": tf,
            "bars": bars,
        },
        "params": _clean_params(params),
        "candles": _candles(df),
        "overlays": overlays,
        "panels": panels,
        "markers": _markers(signals),
        "levels": _levels(df, signals, params),
        "signals": _signal_table(signals, strategy),
        "stats": _stats(signals),
    }

