"""Dashboard payload builder for Combo with an HTF trend chart."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.strategies.combo.trend_filter import timeframe_minutes


MAX_SIGNAL_ROWS = 120


def _ts(value: object) -> int:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
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


def _trend_regime_histogram(df: pd.DataFrame) -> list[dict[str, Any]]:
    colors = {1: "#22c55e", -1: "#ef4444", 0: "#94a3b8"}
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        if pd.isna(row.get("bartime")) or pd.isna(row.get("htf_trend")):
            continue
        trend = int(row["htf_trend"])
        rows.append({"time": _ts(row["bartime"]), "value": trend, "color": colors.get(trend, "#94a3b8")})
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
    if "htf_trend" not in trend_df.columns:
        return []
    markers: list[dict[str, Any]] = []
    previous = None
    for _, row in trend_df.iterrows():
        if pd.isna(row.get("bartime")) or pd.isna(row.get("htf_trend")):
            continue
        trend = int(row["htf_trend"])
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


def _level_segments(df: pd.DataFrame, signals: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    if not bool(params.get("SHOW_LEVELS", True)) or signals.empty:
        return []
    index_by_time = {
        _ts(row["bartime"]): pos
        for pos, (_, row) in enumerate(df.iterrows())
        if pd.notna(row.get("bartime"))
    }
    times = [_ts(value) for value in df["bartime"] if pd.notna(value)]
    if not times:
        return []

    line_bars = int(params.get("ENTRY_LINE_BARS", 2))
    segments: list[dict[str, Any]] = []
    for _, row in signals.iterrows():
        start_value = row.get("entry_time") if pd.notna(row.get("entry_time")) else row.get("bartime")
        if pd.isna(start_value):
            continue
        start_time = _ts(start_value)
        start_idx = index_by_time.get(start_time)
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
                    "data": [{"time": start_time, "value": price}, {"time": end_time, "value": price}],
                }
            )
    return segments


def _entry_trend_links(entry_df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    required = {"bartime", "htf_bartime", "htf_close_time"}
    if not required.issubset(entry_df.columns):
        return rows
    for _, row in entry_df.iterrows():
        if pd.isna(row.get("bartime")) or pd.isna(row.get("htf_bartime")):
            continue
        rows.append(
            {
                "entryTime": _ts(row["bartime"]),
                "trendTime": _ts(row["htf_bartime"]),
                "trendCloseTime": _ts(row["htf_close_time"]),
                "trend": _num(row.get("htf_trend"), 0),
                "trendLabel": row.get("htf_trend_label", ""),
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
                "entry": _num(row.get("entry_price"), 5),
                "sl": _num(row.get("sl_price"), 5),
                "tp": _num(row.get("tp_price"), 5),
                "rr": _num(row.get("risk_reward"), 2),
                "atr": _num(row.get("atr"), 5),
                "ma": _num(row.get("ma"), 5),
                "macd_h": _num(row.get("macd_h"), 5),
                "htf_trend": row.get("htf_trend_label", ""),
                "htf_slope": _num(row.get("htf_slow_slope"), 5),
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


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or isinstance(value, (str, bool, int, float, list)):
            clean[key] = value
        else:
            clean[key] = str(value)
    return clean


def attach_dow_trend_filter(
    entry_df: pd.DataFrame,
    dow_df: pd.DataFrame,
    params: dict[str, Any],
) -> pd.DataFrame:
    """Attach a Dow-structure filter to raw Combo entry signals."""
    out = entry_df.copy()
    if out.empty:
        return out

    out["raw_signal"] = out.get("raw_signal", out.get("signal", 0)).fillna(0).astype(int)
    out["signal"] = out["raw_signal"]
    if "raw_signal_reason" not in out.columns:
        out["raw_signal_reason"] = out.get("signal_reason", "")
    if "signal_reason" not in out.columns:
        out["signal_reason"] = out["raw_signal_reason"]

    out["d_structure_label"] = ""
    out["d_high_label"] = ""
    out["d_low_label"] = ""
    out["d_high_price"] = pd.NA
    out["d_low_price"] = pd.NA
    out["d_filter_status"] = ""

    required = {"bartime", "dow_pivot_type", "dow_label", "dow_pivot_price"}
    if dow_df.empty or not required.issubset(dow_df.columns):
        active = out["raw_signal"].ne(0)
        out.loc[active, "d_filter_status"] = "missing_structure"
        return _apply_dow_filter_blocking(out, params)

    dow = dow_df.copy()
    dow["bartime"] = pd.to_datetime(dow["bartime"], errors="coerce")
    dow["dow_pivot_price"] = pd.to_numeric(dow["dow_pivot_price"], errors="coerce")
    dow = dow.dropna(subset=["bartime", "dow_pivot_type", "dow_label", "dow_pivot_price"]).sort_values("bartime")

    tf_minutes = timeframe_minutes(params.get("DOW_TF", "D1"))
    right = max(0, int(params.get("DOW_PIVOT_RIGHT", 0)))
    dow["confirmed_time"] = dow["bartime"] + pd.Timedelta(minutes=tf_minutes * right)

    for idx, row in out.iterrows():
        raw_signal = int(row.get("raw_signal", 0) or 0)
        if raw_signal == 0:
            continue

        entry_time = pd.Timestamp(row["bartime"])
        usable = dow.loc[dow["confirmed_time"].le(entry_time)]
        latest_high = _latest_dow_pivot(usable, "high")
        latest_low = _latest_dow_pivot(usable, "low")
        if latest_high is None or latest_low is None:
            out.at[idx, "d_filter_status"] = "missing_structure"
            continue

        high_label = str(latest_high["dow_label"])
        low_label = str(latest_low["dow_label"])
        high_price = float(latest_high["dow_pivot_price"])
        low_price = float(latest_low["dow_pivot_price"])

        out.at[idx, "d_high_label"] = high_label
        out.at[idx, "d_low_label"] = low_label
        out.at[idx, "d_high_price"] = high_price
        out.at[idx, "d_low_price"] = low_price
        out.at[idx, "d_structure_label"] = f"{high_label}+{low_label}"

        trend = _dow_structure_direction(high_label, low_label)
        status = "aligned" if trend == raw_signal else "counter_trend" if trend == -raw_signal else "neutral_structure"
        if status == "aligned" and _dow_level_is_broken(row, raw_signal, high_price, low_price):
            status = "level_break"
        out.at[idx, "d_filter_status"] = status

    return _apply_dow_filter_blocking(out, params)


def _latest_dow_pivot(df: pd.DataFrame, pivot_type: str) -> pd.Series | None:
    pivots = df.loc[df["dow_pivot_type"].astype(str).str.lower().eq(pivot_type)]
    if pivots.empty:
        return None
    return pivots.iloc[-1]


def _dow_structure_direction(high_label: str, low_label: str) -> int:
    if high_label == "HH" and low_label == "HL":
        return 1
    if high_label == "LH" and low_label == "LL":
        return -1
    return 0


def _dow_level_is_broken(row: pd.Series, signal: int, high_price: float, low_price: float) -> bool:
    price = pd.to_numeric(pd.Series([row.get("entry_price", row.get("close"))]), errors="coerce").iloc[0]
    if pd.isna(price):
        return False
    if signal == 1:
        return float(price) <= low_price
    if signal == -1:
        return float(price) >= high_price
    return False


def _apply_dow_filter_blocking(out: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    mode = str(params.get("DOW_TREND_FILTER_MODE", "warn_conflicts")).strip().lower()
    if mode == "warn_conflicts":
        return out

    blocking_statuses = {"missing_structure", "neutral_structure", "counter_trend"}
    if mode == "require_structure_level":
        blocking_statuses.add("level_break")
    blocked = out["raw_signal"].ne(0) & out["d_filter_status"].isin(blocking_statuses)
    out.loc[blocked, "signal"] = 0
    if blocked.any():
        reason = out.loc[blocked, "signal_reason"].fillna("").astype(str)
        suffix = "blocked by D structure filter"
        out.loc[blocked, "signal_reason"] = reason.where(reason.eq(""), reason + " | ") + suffix
    return out


def build_combo_mtf_payload(
    trend_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    *,
    strategy: str,
    strategy_label: str,
    symbol: str,
    entry_tf: str,
    entry_bars: int,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Build the four-pane Combo MTF dashboard payload."""
    signals = entry_df[entry_df["signal"].fillna(0).astype(int).ne(0)].copy()
    raw_signal = entry_df.get("raw_signal", pd.Series(0, index=entry_df.index)).fillna(0).astype(int)
    filtered = entry_df[raw_signal.ne(0) & entry_df["signal"].fillna(0).astype(int).eq(0)].copy()

    trend_overlays: list[dict[str, Any]] = []
    if params.get("SHOW_HTF_TREND", True):
        trend_overlays.extend(
            [
                {
                    "key": "htf_fast_ema",
                    "label": f"EMA {params['HTF_FAST_EMA']}",
                    "color": "#38bdf8",
                    "data": _series(trend_df, "htf_fast_ema"),
                },
                {
                    "key": "htf_slow_ema",
                    "label": f"EMA {params['HTF_SLOW_EMA']}",
                    "color": "#f59e0b",
                    "data": _series(trend_df, "htf_slow_ema"),
                },
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

    entry_markers = _markers(signals)
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

    trend_panels: list[dict[str, Any]] = []
    if params.get("SHOW_HTF_TREND", True):
        trend_panels.append(
            {
                "key": "htf_trend",
                "label": "HTF Trend Regime",
                "type": "histogram",
                "data": _trend_regime_histogram(trend_df),
            }
        )

    return {
        "layout": "combo_mtf",
        "meta": {
            "strategy": strategy,
            "strategyLabel": strategy_label,
            "symbol": symbol,
            "tf": f"{params['HTF_TF']} / {entry_tf}",
            "trendTf": params["HTF_TF"],
            "entryTf": entry_tf,
            "trendTfMinutes": timeframe_minutes(params["HTF_TF"]),
            "entryTfMinutes": timeframe_minutes(entry_tf),
            "bars": int(entry_bars),
            "trendBars": int(params["HTF_BARS"]),
            "entryBars": int(entry_bars),
        },
        "params": _clean_params(params),
        "charts": {
            "trend": {
                "key": "trend",
                "label": f"{symbol} {params['HTF_TF']} - EMA Trend Regime",
                "tf": params["HTF_TF"],
                "candles": _candles(trend_df),
                "overlays": trend_overlays,
                "markers": _trend_markers(trend_df),
                "panels": trend_panels,
            },
            "entry": {
                "key": "entry",
                "label": f"{symbol} {entry_tf} - Combo Entry Signals",
                "tf": entry_tf,
                "candles": _candles(entry_df),
                "overlays": entry_overlays,
                "segments": _level_segments(entry_df, signals, params),
                "markers": entry_markers,
                "panels": entry_panels,
            },
        },
        "entryTrendLinks": _entry_trend_links(entry_df),
        "signals": _signal_table(signals),
        "filteredSignals": _signal_table(filtered.assign(signal=filtered.get("raw_signal", 0))),
        "stats": _stats(entry_df, signals, filtered),
    }
