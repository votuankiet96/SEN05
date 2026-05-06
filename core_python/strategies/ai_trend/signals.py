"""Signal logic for the AI Trend multi-timeframe dashboard strategy."""

from __future__ import annotations

import pandas as pd

from core_python.indicators.ai_trend import calc_ai_trend_navigator
from core_python.indicators.core import ema, safe_ratio
from core_python.indicators.dow_wave import calc_dow_wave
from core_python.strategies.ai_trend.config import normalize_params, timeframe_minutes


def add_ai_trend_indicators(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """
    Add entry-timeframe indicators.

    This callable keeps the generic StrategySpec contract valid. The full MTF
    strategy path uses ``build_ai_trend_frames`` below.
    """
    p = normalize_params(params)
    return prepare_entry_frame(df, p)


def prepare_trend_frame(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add trend-timeframe KNN fields and bias columns."""
    out = df.copy()
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
    out["h3_bias"] = 0
    bullish = out["ai_direction"].eq(1)
    bearish = out["ai_direction"].eq(-1)
    out.loc[bullish, "h3_bias"] = 1
    out.loc[bearish, "h3_bias"] = -1
    bias_change = out["h3_bias"].ne(out["h3_bias"].shift(1))
    out["h3_bias_segment"] = bias_change.cumsum().astype(int)
    out["h3_close_time"] = out["bartime"] + pd.Timedelta(minutes=trend_minutes)
    out["h3_window_start"] = out["bartime"]
    out["h3_window_end"] = out["h3_close_time"]

    prev_knn = out["ai_knn"].shift(1)
    prev_avg = out["ai_avg"].shift(1)
    prev2_knn = out["ai_knn"].shift(2)
    out["knn_cross_over_avg"] = prev_knn.le(prev_avg) & out["ai_knn"].gt(out["ai_avg"])
    out["knn_cross_under_avg"] = prev_knn.ge(prev_avg) & out["ai_knn"].lt(out["ai_avg"])
    out["knn_switch_up"] = prev_knn.lt(out["ai_knn"]) & prev_knn.le(prev2_knn)
    out["knn_switch_down"] = prev_knn.gt(out["ai_knn"]) & prev_knn.ge(prev2_knn)
    out["knn_neutral"] = out["ai_direction"].eq(0) & out["ai_direction"].shift(1).ne(0)
    return out


def prepare_entry_frame(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add entry-timeframe EMA fields used for signal timing."""
    out = df.copy()
    entry_minutes = timeframe_minutes(params["ENTRY_TF"])
    out["ema_fast"] = ema(out["close"], int(params["EMA_FAST"]))
    out["ema_slow"] = ema(out["close"], int(params["EMA_SLOW"]))
    out["prev_close"] = out["close"].shift(1)
    out["prev_ema_fast"] = out["ema_fast"].shift(1)
    out["prev_ema_slow"] = out["ema_slow"].shift(1)
    out["ema_gap"] = out["ema_fast"] - out["ema_slow"]
    out["ema_gap_pct"] = safe_ratio(out["ema_gap"], out["close"])
    out["m45_close_time"] = out["bartime"] + pd.Timedelta(minutes=entry_minutes)
    dow = calc_dow_wave(
        out,
        left=int(params["DOW_PIVOT_LEFT"]),
        right=int(params["DOW_PIVOT_RIGHT"]),
        min_atr_mult=float(params["DOW_MIN_ATR_MULT"]),
    )
    out = pd.concat([out, dow], axis=1)
    return out


def merge_trend_into_entry(entry_df: pd.DataFrame, trend_df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach the latest closed trend row to every entry-timeframe bar.

    A trend bar is considered usable only when its close time is <= the entry
    bar open time. This ensures the trend is confirmed before the entry candle
    starts, so entry markers do not appear before trend confirmation.
    """
    if entry_df.empty:
        return entry_df.copy()

    entry = entry_df.sort_values("bartime").copy()
    trend_cols = [
        "h3_close_time",
        "ai_knn",
        "ai_avg",
        "ai_prediction",
        "ai_direction",
        "h3_bias",
        "h3_bias_segment",
    ]
    trend = trend_df[trend_cols].sort_values("h3_close_time").rename(
        columns={
            "ai_knn": "h3_ai_knn",
            "ai_avg": "h3_ai_avg",
            "ai_prediction": "h3_ai_prediction",
            "ai_direction": "h3_ai_direction",
        }
    )
    merged = pd.merge_asof(
        entry,
        trend,
        left_on="bartime",
        right_on="h3_close_time",
        direction="backward",
    )
    return merged.sort_values("bartime").reset_index(drop=True)


def detect_ai_trend_signals(
    df: pd.DataFrame,
    symbol: str | None = None,
    params: dict | None = None,
    sess_mask: pd.Series | None = None,
) -> pd.DataFrame:
    """Detect the first entry bar in each trend segment that agrees with EMA order."""
    _ = symbol
    _ = sess_mask
    p = normalize_params(params)
    trend_tf = p["TREND_TF"]
    entry_tf = p["ENTRY_TF"]
    out = df.copy()
    out["signal"] = 0
    out["signal_reason"] = ""

    required = {"h3_bias", "h3_bias_segment", "ema_fast", "ema_slow"}
    if not required.issubset(out.columns):
        return out

    valid = (
        out["h3_bias"].notna()
        & out["h3_bias_segment"].notna()
        & out["ema_fast"].notna()
        & out["ema_slow"].notna()
    )
    if {"bartime", "h3_close_time"}.issubset(out.columns):
        valid &= out["bartime"].ge(out["h3_close_time"])
    buy_candidate = valid & out["h3_bias"].eq(1) & out["ema_fast"].gt(out["ema_slow"])
    sell_candidate = valid & out["h3_bias"].eq(-1) & out["ema_fast"].lt(out["ema_slow"])

    buy = pd.Series(False, index=out.index)
    sell = pd.Series(False, index=out.index)
    for _segment, segment_df in out[valid & out["h3_bias"].ne(0)].groupby("h3_bias_segment", sort=True):
        direction = int(segment_df["h3_bias"].iloc[0])
        if direction == 1:
            matches = segment_df[buy_candidate.loc[segment_df.index]]
            if not matches.empty:
                buy.loc[matches.index[0]] = True
        elif direction == -1:
            matches = segment_df[sell_candidate.loc[segment_df.index]]
            if not matches.empty:
                sell.loc[matches.index[0]] = True

    out.loc[buy, "signal"] = 1
    out.loc[sell, "signal"] = -1
    out.loc[buy, "signal_reason"] = (
        f"First {entry_tf} bar after {trend_tf} close in bullish segment with EMA fast above EMA slow"
    )
    out.loc[sell, "signal_reason"] = (
        f"First {entry_tf} bar after {trend_tf} close in bearish segment with EMA fast below EMA slow"
    )
    return out


def build_ai_trend_frames(
    trend_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    params: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the enriched trend frame and entry signal frame."""
    trend = prepare_trend_frame(trend_df, params)
    entry = prepare_entry_frame(entry_df, params)
    merged = merge_trend_into_entry(entry, trend)
    signals = detect_ai_trend_signals(merged, params=params)
    return trend, signals
