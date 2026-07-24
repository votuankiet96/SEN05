"""Signal logic for the KNN Combo strategy."""

from __future__ import annotations

import pandas as pd

from core_python.indicators.ai_trend import calc_ai_trend_navigator
from core_python.indicators.core import atr, macd_hist, sma
from core_python.strategies.knn_combo.config import normalize_params, timeframe_minutes


def add_knn_combo_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Keep the StrategySpec indicator contract valid for the entry frame."""
    p = normalize_params(params)
    return prepare_entry_frame(df, p)


def prepare_trend_frame(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add KNN trend bias fields on the higher timeframe."""
    out = df.sort_values("bartime").reset_index(drop=True).copy()
    trend_minutes = timeframe_minutes(params["TREND_TF"])
    ai = calc_ai_trend_navigator(
        out,
        price_value=str(params["PRICE_VALUE"]),
        ma_len=int(params["AI_MA_LEN"]),
        target_value=str(params["TARGET_VALUE"]),
        target_len=int(params["AI_TARGET_LEN"]),
        number_of_closest_values=int(params["AI_K"]),
        smoothing_period=int(params["AI_SMOOTH"]),
    )
    out = pd.concat([out, ai], axis=1)
    out["trend_bias"] = 0
    out.loc[out["ai_direction"].eq(1), "trend_bias"] = 1
    out.loc[out["ai_direction"].eq(-1), "trend_bias"] = -1
    out["trend_bias_label"] = out["trend_bias"].map({1: "Bullish", -1: "Bearish", 0: "Neutral"})
    out["trend_close_time"] = pd.to_datetime(out["bartime"], errors="coerce") + pd.Timedelta(minutes=trend_minutes)
    out["trend_window_start"] = out["bartime"]
    out["trend_window_end"] = out["trend_close_time"]
    prev_trend_bias = out["trend_bias"].shift(1)
    change = out["trend_bias"].ne(prev_trend_bias)
    out["trend_bias_changed"] = change & prev_trend_bias.notna() & out["trend_bias"].ne(0)
    out["trend_bias_segment"] = change.cumsum().astype(int)

    prev_knn = out["ai_knn"].shift(1)
    prev_avg = out["ai_avg"].shift(1)
    out["knn_cross_over_avg"] = prev_knn.le(prev_avg) & out["ai_knn"].gt(out["ai_avg"])
    out["knn_cross_under_avg"] = prev_knn.ge(prev_avg) & out["ai_knn"].lt(out["ai_avg"])
    return out


def prepare_entry_frame(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add Combo-style entry timeframe indicators."""
    out = df.sort_values("bartime").reset_index(drop=True).copy()
    entry_minutes = timeframe_minutes(params["ENTRY_TF"])
    out["ma"] = sma(out["close"], int(params["MA_PERIOD"]))
    out["macd_h"] = macd_hist(
        out["close"],
        fast=int(params["MACD_FAST"]),
        slow=int(params["MACD_SLOW"]),
        signal=int(params["MACD_SIGNAL"]),
    )
    out["atr"] = atr(out, int(params["ATR_PERIOD"]))
    out["prev_close"] = out["close"].shift(1)
    out["prev_ma"] = out["ma"].shift(1)
    out["entry_close_time"] = pd.to_datetime(out["bartime"], errors="coerce") + pd.Timedelta(minutes=entry_minutes)
    return out


def merge_trend_into_entry(entry_df: pd.DataFrame, trend_df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach the latest closed KNN trend row to each entry bar.

    A trend bar is usable only when ``trend_close_time <= entry.bartime``.
    """
    if entry_df.empty:
        return entry_df.copy()

    entry = entry_df.sort_values("bartime").copy()
    if trend_df.empty:
        out = entry.copy()
        out["trend_bias"] = pd.NA
        return out.reset_index(drop=True)

    trend_cols = [
        "bartime",
        "trend_close_time",
        "close",
        "ai_knn",
        "ai_avg",
        "ai_direction",
        "trend_bias",
        "trend_bias_label",
        "trend_bias_changed",
        "trend_bias_segment",
    ]
    trend = (
        trend_df[[col for col in trend_cols if col in trend_df.columns]]
        .sort_values("trend_close_time")
        .rename(
            columns={
                "bartime": "trend_bartime",
                "close": "trend_close",
                "ai_knn": "trend_ai_knn",
                "ai_avg": "trend_ai_avg",
                "ai_direction": "trend_ai_direction",
            }
        )
    )
    merged = pd.merge_asof(
        entry,
        trend,
        left_on="bartime",
        right_on="trend_close_time",
        direction="backward",
    )
    return merged.sort_values("bartime").reset_index(drop=True)


def detect_knn_combo_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """Detect Combo-style raw signals, then filter them through the KNN trend bias."""
    _ = symbol
    p = normalize_params(params)
    out = df.copy()
    if sess_mask is None:
        sess_mask = _session_mask(out, p.get("SESSION_HOURS_UTC", []))
    sess_mask = pd.Series(sess_mask, index=out.index).fillna(False).astype(bool)

    required = {"ma", "prev_ma", "macd_h", "atr", "prev_close", "open", "close"}
    out["raw_signal"] = 0
    out["raw_signal_reason"] = ""
    out["signal"] = 0
    out["signal_reason"] = ""
    out["filter_reason"] = ""
    if not required.issubset(out.columns):
        return out

    valid = (
        sess_mask
        & out["ma"].notna()
        & out["prev_ma"].notna()
        & out["macd_h"].notna()
        & out["atr"].notna()
    )
    cross_up = out["prev_close"].le(out["prev_ma"]) & out["close"].gt(out["ma"])
    cross_down = out["prev_close"].ge(out["prev_ma"]) & out["close"].le(out["ma"])
    bullish_setup = out["close"].gt(out["open"]) & out["macd_h"].gt(0)
    bearish_setup = out["close"].lt(out["open"]) & out["macd_h"].lt(0)
    if "trend_bias" in out.columns:
        trend = pd.to_numeric(out["trend_bias"], errors="coerce")
    else:
        trend = pd.Series(pd.NA, index=out.index, dtype="Float64")

    new_trend_segment = _new_trend_segment_mask(out)
    first_buy_candidate = valid & new_trend_segment & trend.eq(1) & out["close"].gt(out["ma"]) & bullish_setup
    first_sell_candidate = valid & new_trend_segment & trend.eq(-1) & out["close"].lt(out["ma"]) & bearish_setup
    first_buy = _first_qualifying_entry_after_trend_signal(out, first_buy_candidate)
    first_sell = _first_qualifying_entry_after_trend_signal(out, first_sell_candidate)

    buy_raw = valid & cross_up & bullish_setup
    sell_raw = valid & cross_down & bearish_setup

    out.loc[buy_raw, "raw_signal"] = 1
    out.loc[sell_raw, "raw_signal"] = -1
    out.loc[buy_raw, "raw_signal_reason"] = "close crossed above MA, bullish candle, MACD histogram > 0"
    out.loc[sell_raw, "raw_signal_reason"] = "close crossed below MA, bearish candle, MACD histogram < 0"
    out.loc[first_buy & ~buy_raw, "raw_signal"] = 1
    out.loc[first_sell & ~sell_raw, "raw_signal"] = -1
    out.loc[first_buy & ~buy_raw, "raw_signal_reason"] = (
        "first entry bar after KNN bullish trend, close above MA, bullish candle, MACD histogram > 0"
    )
    out.loc[first_sell & ~sell_raw, "raw_signal_reason"] = (
        "first entry bar after KNN bearish trend, close below MA, bearish candle, MACD histogram < 0"
    )

    raw = out["raw_signal"].fillna(0).astype(int)

    allow_buy = raw.eq(1) & trend.eq(1)
    allow_sell = raw.eq(-1) & trend.eq(-1)
    if not bool(p.get("NEUTRAL_BLOCK", True)):
        allow_buy |= raw.eq(1) & trend.eq(0)
        allow_sell |= raw.eq(-1) & trend.eq(0)

    out.loc[allow_buy, "signal"] = 1
    out.loc[allow_sell, "signal"] = -1

    trend_tf = str(p.get("TREND_TF", "Trend")).upper()
    blocked = raw.ne(0) & out["signal"].eq(0)
    missing = blocked & trend.isna()
    neutral = blocked & trend.eq(0)
    opposite_buy = blocked & raw.eq(1) & trend.eq(-1)
    opposite_sell = blocked & raw.eq(-1) & trend.eq(1)
    out.loc[missing, "filter_reason"] = f"missing closed {trend_tf} KNN trend"
    out.loc[neutral, "filter_reason"] = f"{trend_tf} KNN trend neutral"
    out.loc[opposite_buy, "filter_reason"] = f"{trend_tf} KNN bearish trend blocks BUY"
    out.loc[opposite_sell, "filter_reason"] = f"{trend_tf} KNN bullish trend blocks SELL"

    valid_signal = out["signal"].ne(0)
    out.loc[valid_signal, "signal_reason"] = out.loc[valid_signal, "raw_signal_reason"].astype(str)
    if "trend_bias_label" in out.columns:
        trend_label = out.loc[valid_signal, "trend_bias_label"].fillna("Unknown").astype(str)
    else:
        trend_label = trend.loc[valid_signal].map({1: "Bullish", -1: "Bearish", 0: "Neutral"}).fillna("Unknown")
    out.loc[valid_signal, "signal_reason"] = (
        out.loc[valid_signal, "signal_reason"] + f" | {trend_tf} KNN trend: " + trend_label
    )
    return out


def build_knn_combo_frames(
    trend_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    params: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the KNN trend frame and filtered entry signal frame."""
    trend = prepare_trend_frame(trend_df, params)
    entry = prepare_entry_frame(entry_df, params)
    merged = merge_trend_into_entry(entry, trend)
    signals = detect_knn_combo_signals(merged, params=params)
    return trend, signals


def _new_trend_segment_mask(df: pd.DataFrame) -> pd.Series:
    """Return True for entry rows that belong to a newly changed non-neutral KNN trend segment."""
    if "trend_bias_changed" not in df.columns:
        return pd.Series(False, index=df.index)

    changed = pd.Series(df["trend_bias_changed"], index=df.index).fillna(False).astype(bool)
    if not changed.any():
        return changed

    if "trend_bias_segment" not in df.columns:
        return changed

    segment = pd.Series(df["trend_bias_segment"], index=df.index)
    changed_segments = set(segment[changed & segment.notna()])
    return segment.isin(changed_segments)


def _first_qualifying_entry_after_trend_signal(df: pd.DataFrame, candidate: pd.Series) -> pd.Series:
    """Return the first qualifying entry setup in each new KNN trend segment."""
    candidate = pd.Series(candidate, index=df.index).fillna(False).astype(bool)
    if not candidate.any():
        return candidate

    if "trend_bias_segment" in df.columns:
        segment = pd.Series(df["trend_bias_segment"], index=df.index)
    elif "trend_close_time" in df.columns:
        segment = pd.to_datetime(df["trend_close_time"], errors="coerce")
    elif "trend_bartime" in df.columns:
        segment = pd.to_datetime(df["trend_bartime"], errors="coerce")
    else:
        segment = pd.Series(range(len(df)), index=df.index)

    return candidate & candidate.groupby(segment, dropna=False).cumsum().eq(1)


def _session_mask(df: pd.DataFrame, hours_utc: list[int] | None) -> pd.Series:
    if not hours_utc:
        return pd.Series(True, index=df.index)
    if "bartime" not in df.columns:
        return pd.Series(True, index=df.index)
    bar_hours = pd.to_datetime(df["bartime"], errors="coerce").dt.hour
    return bar_hours.isin({int(hour) for hour in hours_utc})
