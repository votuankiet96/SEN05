"""Dashboard payload builder for the AI Trend two-chart layout."""

from __future__ import annotations

from typing import Any

import pandas as pd

from og_past.chart.payloads.helpers import (
    candlestick_points as _candles,
    clean_params as _clean_params,
    histogram_points as _histogram,
    series_points as _series,
    to_number as _num,
    to_unix_ts as _ts,
)
from og_core.strategies.ai_trend.config import timeframe_minutes


MAX_SIGNAL_ROWS = 120


def _colored_knn(df: pd.DataFrame) -> list[dict[str, Any]]:
    colors = df["ai_direction"].map({1: "#00e676", -1: "#ff5252", 0: "#ff9800"}).fillna("#ff9800")
    rows: list[dict[str, Any]] = []
    for (_, row), color in zip(df.iterrows(), colors):
        value = _num(row.get("ai_knn"))
        if value is None:
            continue
        rows.append({"time": _ts(row["bartime"]), "value": value, "color": str(color)})
    return rows


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
    return markers


def _dow_markers(df: pd.DataFrame) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    if not {"dow_pivot_type", "dow_label", "dow_pivot_price"}.issubset(df.columns):
        return markers
    colors = {"HH": "#22c55e", "HL": "#84cc16", "LH": "#f97316", "LL": "#ef4444"}
    for _, row in df.dropna(subset=["dow_pivot_type", "dow_label", "dow_pivot_price"]).iterrows():
        label = str(row["dow_label"])
        is_high = str(row["dow_pivot_type"]) == "high"
        markers.append(
            {
                "time": _ts(row["bartime"]),
                "position": "aboveBar" if is_high else "belowBar",
                "color": colors.get(label, "#e5e7eb"),
                "shape": "circle",
                "text": label,
            }
        )
    return markers


def _dow_segments(df: pd.DataFrame) -> list[dict[str, Any]]:
    if not {"dow_pivot_type", "dow_pivot_price"}.issubset(df.columns):
        return []
    pivots = df.dropna(subset=["dow_pivot_type", "dow_pivot_price"])
    segments: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for _, row in pivots.iterrows():
        current = {"time": _ts(row["bartime"]), "value": float(row["dow_pivot_price"])}
        if previous is not None:
            segments.append(
                {
                    "key": "dow_wave",
                    "label": "Dow Wave",
                    "color": "#94a3b8",
                    "width": 1,
                    "style": "dashed",
                    "data": [previous, current],
                }
            )
        previous = current
    return segments


def _level_segments(df: pd.DataFrame, signals: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Build horizontal Entry/SL/TP segments for the entry chart."""
    if not bool(params.get("SHOW_LEVELS", True)):
        return []
    if signals.empty or "bartime" not in df.columns:
        return []

    index_by_time = {
        _ts(row["bartime"]): pos
        for pos, (_, row) in enumerate(df.iterrows())
        if pd.notna(row.get("bartime"))
    }
    times = [_ts(value) for value in df["bartime"] if pd.notna(value)]
    if not times:
        return []

    line_bars = int(params.get("ENTRY_LINE_BARS", 3))
    segments: list[dict[str, Any]] = []
    for _, row in signals.iterrows():
        start_value = row.get("entry_time") if pd.notna(row.get("entry_time")) else row.get("bartime")
        if pd.isna(start_value):
            continue
        start_time = _ts(start_value)
        start_idx = index_by_time.get(start_time)
        if start_idx is None:
            start_idx = index_by_time.get(_ts(row["bartime"]))
        if start_idx is None:
            continue
        end_time = times[min(start_idx + line_bars, len(times) - 1)]

        direction = int(row["signal"])
        prefix = "BUY" if direction == 1 else "SELL"
        for column, kind, color, style in [
            ("entry_price", "entry", "#e5e7eb", "dashed"),
            ("sl_price", "sl", "#ef4444", "dotted"),
            ("tp_price", "tp", "#22c55e", "dotted"),
        ]:
            price = _num(row.get(column))
            if price is None:
                continue
            segments.append(
                {
                    "key": kind,
                    "label": f"{prefix} {kind.upper()}",
                    "color": color,
                    "width": 1,
                    "style": style,
                    "data": [
                        {"time": start_time, "value": price},
                        {"time": end_time, "value": price},
                    ],
                }
            )
    return segments


def _trend_events(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Build H3 transition events used for markers and linked M45 navigation."""
    specs = [
        ("knn_cross_over_avg", "cross_over_avg", "BUY", "KNN crossed above average", "#16a34a", "arrowUp", "belowBar"),
        ("knn_cross_under_avg", "cross_under_avg", "SELL", "KNN crossed below average", "#dc2626", "arrowDown", "aboveBar"),
        ("knn_switch_up", "switch_up", "BUY", "KNN switch up", "#84cc16", "circle", "belowBar"),
        ("knn_switch_down", "switch_down", "SELL", "KNN switch down", "#f97316", "circle", "aboveBar"),
    ]
    events: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if pd.isna(row.get("bartime")) or pd.isna(row.get("h3_close_time")):
            continue
        for column, kind, side, label, color, shape, position in specs:
            if not bool(row.get(column, False)):
                continue
            h3_time = _ts(row["bartime"])
            h3_close_time = _ts(row["h3_close_time"])
            events.append(
                {
                    "kind": kind,
                    "side": side,
                    "label": label,
                    "color": color,
                    "shape": shape,
                    "position": position,
                    "h3Time": h3_time,
                    "h3CloseTime": h3_close_time,
                    "m45WindowStart": _ts(row.get("h3_window_start", row["bartime"])),
                    "m45WindowEnd": _ts(row.get("h3_window_end", row["h3_close_time"])),
                    "aiKnn": _num(row.get("ai_knn"), 5),
                    "aiAvg": _num(row.get("ai_avg"), 5),
                }
            )
    return sorted(events, key=lambda item: (item["h3Time"], item["kind"]))


def _trend_markers(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Create Lightweight Charts markers for H3 transition events."""
    return [
        {
            "time": event["h3Time"],
            "position": event["position"],
            "color": event["color"],
            "shape": event["shape"],
            "text": event["side"] if event["kind"].startswith("cross") else event["label"].replace("KNN ", ""),
        }
        for event in events
    ]


def _signal_table(signals: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in signals.tail(MAX_SIGNAL_ROWS).iterrows():
        direction = int(row["signal"])
        rows.append(
            {
                "bartime": pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M"),
                "side": "BUY" if direction == 1 else "SELL",
                "reason": row.get("signal_reason", ""),
                "h3_bias": _num(row.get("h3_bias"), 0),
                "h3_ai_knn": _num(row.get("h3_ai_knn"), 5),
                "h3_ai_avg": _num(row.get("h3_ai_avg"), 5),
                "entry": _num(row.get("entry_price"), 5),
                "sl": _num(row.get("sl_price"), 5),
                "tp": _num(row.get("tp_price"), 5),
                "rr": _num(row.get("risk_reward"), 2),
                "ema_fast": _num(row.get("ema_fast"), 5),
                "ema_slow": _num(row.get("ema_slow"), 5),
                "macd_h": _num(row.get("macd_h"), 5),
                "close": _num(row.get("close"), 5),
            }
        )
    return rows


def _stats(signals: pd.DataFrame) -> dict[str, Any]:
    if signals.empty:
        return {"total": 0, "buy": 0, "sell": 0, "last": "-"}
    buy = int(signals["signal"].eq(1).sum())
    sell = int(signals["signal"].eq(-1).sum())
    last_signal = int(signals["signal"].iloc[-1])
    return {"total": int(len(signals)), "buy": buy, "sell": sell, "last": "BUY" if last_signal == 1 else "SELL"}


def build_ai_trend_payload(
    trend_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Build a two-chart payload for the AI Trend dashboard."""
    signals = entry_df[entry_df["signal"].fillna(0).astype(int).ne(0)].copy()
    linked_events = _trend_events(trend_df)

    trend_overlays: list[dict[str, Any]] = []
    if params.get("SHOW_H3_KNN", True):
        trend_overlays.extend(
            [
                {"key": "ai_knn", "label": "KNN Classifier", "color": "#00e676", "data": _colored_knn(trend_df)},
                {"key": "ai_avg", "label": "Average KNN", "color": "#009688", "data": _series(trend_df, "ai_avg")},
            ]
        )

    entry_overlays: list[dict[str, Any]] = []
    if params.get("SHOW_M45_EMA", True):
        entry_overlays.extend(
            [
                {"key": "ema_fast", "label": f"EMA {params['EMA_FAST']}", "color": "#38bdf8", "data": _series(entry_df, "ema_fast")},
                {"key": "ema_slow", "label": f"EMA {params['EMA_SLOW']}", "color": "#f59e0b", "data": _series(entry_df, "ema_slow")},
            ]
        )
    entry_segments: list[dict[str, Any]] = []
    if params.get("SHOW_M45_DOW", True):
        entry_segments.extend(_dow_segments(entry_df))
    entry_segments.extend(_level_segments(entry_df, signals, params))
    entry_markers = _markers(signals)
    if params.get("SHOW_M45_DOW", True) and params.get("SHOW_M45_DOW_LABELS", True):
        entry_markers.extend(_dow_markers(entry_df))
    entry_panels: list[dict[str, Any]] = []
    if params.get("SHOW_M45_MACD", True) and "macd_h" in entry_df:
        entry_panels.append(
            {
                "key": "macd_h",
                "label": f"MACD {params['MACD_FAST']}/{params['MACD_SLOW']}/{params['MACD_SIGNAL']} Histogram",
                "type": "histogram",
                "data": _histogram(entry_df, "macd_h"),
            }
        )
    if params.get("SHOW_M45_ATR", True) and "atr" in entry_df:
        entry_panels.append(
            {
                "key": "atr",
                "label": f"ATR {params['ATR_PERIOD']}",
                "type": "line",
                "color": "#a855f7",
                "data": _series(entry_df, "atr"),
            }
        )

    return {
        "layout": "ai_trend_mtf",
        "meta": {
            "strategy": strategy,
            "strategyLabel": strategy_label,
            "symbol": symbol,
            "tf": f"{params['TREND_TF']} / {params['ENTRY_TF']}",
            "trendTf": params["TREND_TF"],
            "entryTf": params["ENTRY_TF"],
            "trendTfMinutes": timeframe_minutes(params["TREND_TF"]),
            "entryTfMinutes": timeframe_minutes(params["ENTRY_TF"]),
            "bars": int(params["ENTRY_BARS"]),
            "trendBars": int(params["TREND_BARS"]),
            "entryBars": int(params["ENTRY_BARS"]),
        },
        "params": _clean_params(params),
        "charts": {
            "trend": {
                "key": "trend",
                "label": f"{symbol} {params['TREND_TF']} - AI Trend KNN",
                "tf": params["TREND_TF"],
                "candles": _candles(trend_df),
                "overlays": trend_overlays,
                "markers": [],
            },
            "entry": {
                "key": "entry",
                "label": f"{symbol} {params['ENTRY_TF']} - EMA {params['EMA_FAST']}/{params['EMA_SLOW']}",
                "tf": params["ENTRY_TF"],
                "candles": _candles(entry_df),
                "overlays": entry_overlays,
                "segments": entry_segments,
                "markers": entry_markers,
                "panels": entry_panels,
            },
        },
        "linkedEvents": linked_events,
        "signals": _signal_table(signals),
        "stats": _stats(signals),
    }
