"""Dashboard payload builder for KNN Combo."""

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
from og_core.strategies.knn_combo.config import timeframe_minutes

MAX_SIGNAL_ROWS = 120


def _colored_knn(df: pd.DataFrame) -> list[dict[str, Any]]:
    colors = df["trend_bias"].map({1: "#00e676", -1: "#ff5252", 0: "#ff9800"}).fillna("#ff9800")
    rows: list[dict[str, Any]] = []
    for (_, row), color in zip(df.iterrows(), colors):
        value = _num(row.get("ai_knn"))
        if value is None:
            continue
        rows.append({"time": _ts(row["bartime"]), "value": value, "color": str(color)})
    return rows


def _trend_regime_histogram(df: pd.DataFrame) -> list[dict[str, Any]]:
    colors = {1: "#22c55e", -1: "#ef4444", 0: "#94a3b8"}
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if pd.isna(row.get("bartime")) or pd.isna(row.get("trend_bias")):
            continue
        trend = int(row["trend_bias"])
        rows.append({"time": _ts(row["bartime"]), "value": trend, "color": colors.get(trend, "#94a3b8")})
    return rows


def _accepted_markers(signals: pd.DataFrame) -> list[dict[str, Any]]:
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


def _filtered_markers(filtered: pd.DataFrame) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for _, row in filtered.iterrows():
        direction = int(row["raw_signal"])
        is_buy = direction == 1
        markers.append(
            {
                "time": _ts(row["bartime"]),
                "position": "belowBar" if is_buy else "aboveBar",
                "color": "#f59e0b",
                "shape": "circle",
                "text": "xBUY" if is_buy else "xSELL",
            }
        )
    return markers


def _trend_markers(trend_df: pd.DataFrame) -> list[dict[str, Any]]:
    if "trend_bias" not in trend_df.columns:
        return []
    markers: list[dict[str, Any]] = []
    previous = None
    for _, row in trend_df.iterrows():
        if pd.isna(row.get("bartime")) or pd.isna(row.get("trend_bias")):
            continue
        trend = int(row["trend_bias"])
        if previous is not None and trend == previous:
            continue
        previous = trend
        if trend == 0:
            markers.append(
                {
                    "time": _ts(row["bartime"]),
                    "position": "aboveBar",
                    "color": "#94a3b8",
                    "shape": "square",
                    "text": "NEUTRAL",
                }
            )
            continue
        is_bull = trend == 1
        markers.append(
            {
                "time": _ts(row["bartime"]),
                "position": "belowBar" if is_bull else "aboveBar",
                "color": "#16a34a" if is_bull else "#dc2626",
                "shape": "arrowUp" if is_bull else "arrowDown",
                "text": "BULL" if is_bull else "BEAR",
            }
        )
    return markers


def _entry_trend_links(entry_df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    required = {"bartime", "trend_bartime", "trend_close_time"}
    if not required.issubset(entry_df.columns):
        return rows
    for _, row in entry_df.iterrows():
        if pd.isna(row.get("bartime")) or pd.isna(row.get("trend_bartime")):
            continue
        rows.append(
            {
                "entryTime": _ts(row["bartime"]),
                "trendTime": _ts(row["trend_bartime"]),
                "trendCloseTime": _ts(row["trend_close_time"]),
                "trend": _num(row.get("trend_bias"), 0),
                "trendLabel": row.get("trend_bias_label", ""),
            }
        )
    return rows


def _signal_table(signals: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in signals.tail(MAX_SIGNAL_ROWS).iterrows():
        direction = int(row["signal"])
        rows.append(
            {
                "bartime": pd.Timestamp(row["bartime"]).strftime("%Y-%m-%d %H:%M"),
                "side": "BUY" if direction == 1 else "SELL",
                "reason": row.get("signal_reason", ""),
                "trend_bias_label": row.get("trend_bias_label", ""),
                "trend_close_time": _fmt_time(row.get("trend_close_time")),
                "trend_ai_knn": _num(row.get("trend_ai_knn"), 5),
                "trend_ai_avg": _num(row.get("trend_ai_avg"), 5),
                "ma": _num(row.get("ma"), 5),
                "macd_h": _num(row.get("macd_h"), 5),
                "atr": _num(row.get("atr"), 5),
            }
        )
    return rows


def _stats(entry_df: pd.DataFrame, signals: pd.DataFrame, filtered: pd.DataFrame) -> dict[str, Any]:
    raw_total = int(entry_df.get("raw_signal", pd.Series(dtype=int)).fillna(0).astype(int).ne(0).sum())
    if signals.empty:
        return {
            "total": 0,
            "buy": 0,
            "sell": 0,
            "last": "-",
            "rawTotal": raw_total,
            "filteredTotal": int(len(filtered)),
        }
    buy = int(signals["signal"].eq(1).sum())
    sell = int(signals["signal"].eq(-1).sum())
    last_signal = int(signals["signal"].iloc[-1])
    return {
        "total": int(len(signals)),
        "buy": buy,
        "sell": sell,
        "last": "BUY" if last_signal == 1 else "SELL",
        "rawTotal": raw_total,
        "filteredTotal": int(len(filtered)),
    }


def _fmt_time(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M")


def build_knn_combo_payload(
    trend_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Build the two-chart KNN Combo dashboard payload."""
    signals = entry_df[entry_df["signal"].fillna(0).astype(int).ne(0)].copy()
    raw_signal = entry_df.get("raw_signal", pd.Series(0, index=entry_df.index)).fillna(0).astype(int)
    filtered = entry_df[raw_signal.ne(0) & entry_df["signal"].fillna(0).astype(int).eq(0)].copy()

    trend_overlays: list[dict[str, Any]] = []
    if params.get("SHOW_TREND_KNN", True):
        trend_overlays.extend(
            [
                {"key": "ai_knn", "label": "KNN Classifier", "color": "#00e676", "data": _colored_knn(trend_df)},
                {"key": "ai_avg", "label": "Average KNN", "color": "#009688", "data": _series(trend_df, "ai_avg")},
            ]
        )

    entry_overlays: list[dict[str, Any]] = []
    if params.get("SHOW_MA", True):
        entry_overlays.append(
            {
                "key": "ma",
                "label": f"MA {params['MA_PERIOD']}",
                "color": "#f59e0b",
                "data": _series(entry_df, "ma"),
            }
        )

    entry_markers = _accepted_markers(signals)
    if params.get("SHOW_FILTERED_SIGNALS", True):
        entry_markers.extend(_filtered_markers(filtered))

    entry_panels: list[dict[str, Any]] = []
    if params.get("SHOW_MACD", True) and "macd_h" in entry_df:
        entry_panels.append(
            {
                "key": "macd_h",
                "label": f"MACD {params['MACD_FAST']}/{params['MACD_SLOW']}/{params['MACD_SIGNAL']} Histogram",
                "type": "histogram",
                "data": _histogram(entry_df, "macd_h"),
            }
        )
    if params.get("SHOW_ATR", True) and "atr" in entry_df:
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
        "layout": "knn_combo_mtf",
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
                "label": f"{symbol} {params['TREND_TF']} - KNN Trend Gate",
                "tf": params["TREND_TF"],
                "candles": _candles(trend_df),
                "overlays": trend_overlays,
                "markers": _trend_markers(trend_df),
                "panels": [
                    {
                        "key": "trend_bias",
                        "label": "KNN Trend Bias",
                        "type": "histogram",
                        "data": _trend_regime_histogram(trend_df),
                    }
                ],
            },
            "entry": {
                "key": "entry",
                "label": f"{symbol} {params['ENTRY_TF']} - Combo Signals Filtered by KNN",
                "tf": params["ENTRY_TF"],
                "candles": _candles(entry_df),
                "overlays": entry_overlays,
                "segments": [],
                "markers": entry_markers,
                "panels": entry_panels,
            },
        },
        "entryTrendLinks": _entry_trend_links(entry_df),
        "signals": _signal_table(signals),
        "filteredSignals": _signal_table(filtered.assign(signal=filtered.get("raw_signal", 0))),
        "stats": _stats(entry_df, signals, filtered),
    }

