"""Dashboard payload builder for the AI Trend two-chart layout."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.strategies.ai_trend.config import timeframe_minutes


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
            is_up = current["value"] >= previous["value"]
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
                "ema_fast": _num(row.get("ema_fast"), 5),
                "ema_slow": _num(row.get("ema_slow"), 5),
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


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or isinstance(value, (str, bool, int, float, list)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


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
    entry_segments = _dow_segments(entry_df) if params.get("SHOW_M45_DOW", True) else []
    entry_markers = _markers(signals)
    if params.get("SHOW_M45_DOW", True) and params.get("SHOW_M45_DOW_LABELS", True):
        entry_markers.extend(_dow_markers(entry_df))

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
            },
        },
        "linkedEvents": linked_events,
        "signals": _signal_table(signals),
        "stats": _stats(signals),
    }
