"""Higher-timeframe EMA trend regime helpers for the Combo strategy."""

from __future__ import annotations

import pandas as pd

from core_python.config import TF_MINUTES
from core_python.indicators.core import ema


def timeframe_minutes(tf: object) -> int:
    """Return timeframe length in minutes, or raise for unsupported codes."""
    code = str(tf or "").strip().upper()
    if code not in TF_MINUTES:
        raise ValueError(f"Unsupported timeframe '{code}'.")
    return int(TF_MINUTES[code])


def drop_open_bar(df: pd.DataFrame, tf: str, *, now_utc: pd.Timestamp | None = None) -> pd.DataFrame:
    """Drop bars whose open time is too recent to be fully closed."""
    if df.empty or "bartime" not in df:
        return df
    minutes = timeframe_minutes(tf)
    now = pd.Timestamp.now("UTC") if now_utc is None else pd.Timestamp(now_utc)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")
    cutoff = now - pd.Timedelta(minutes=minutes)
    bar_times = pd.to_datetime(df["bartime"], errors="coerce")
    if getattr(bar_times.dt, "tz", None) is None:
        bar_times = bar_times.dt.tz_localize("UTC")
    else:
        bar_times = bar_times.dt.tz_convert("UTC")
    return df.loc[bar_times <= cutoff].reset_index(drop=True)


def prepare_combo_trend_frame(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Add HTF EMA stack, slope, close time, and trend regime columns."""
    out = df.sort_values("bartime").reset_index(drop=True).copy()
    trend_tf = str(params["HTF_TF"]).upper()
    minutes = timeframe_minutes(trend_tf)
    fast = int(params["HTF_FAST_EMA"])
    slow = int(params["HTF_SLOW_EMA"])
    slope_lookback = int(params["HTF_SLOPE_LOOKBACK"])
    slope_min = float(params["HTF_SLOPE_MIN"])

    out["htf_fast_ema"] = ema(out["close"], fast)
    out["htf_slow_ema"] = ema(out["close"], slow)
    out["htf_slow_slope"] = out["htf_slow_ema"] - out["htf_slow_ema"].shift(slope_lookback)
    out["htf_close_time"] = pd.to_datetime(out["bartime"], errors="coerce") + pd.Timedelta(minutes=minutes)
    out["htf_trend"] = 0

    valid = out["htf_fast_ema"].notna() & out["htf_slow_ema"].notna() & out["htf_slow_slope"].notna()
    bullish = (
        valid
        & out["htf_fast_ema"].gt(out["htf_slow_ema"])
        & out["close"].gt(out["htf_slow_ema"])
        & out["htf_slow_slope"].gt(slope_min)
    )
    bearish = (
        valid
        & out["htf_fast_ema"].lt(out["htf_slow_ema"])
        & out["close"].lt(out["htf_slow_ema"])
        & out["htf_slow_slope"].lt(-slope_min)
    )
    out.loc[bullish, "htf_trend"] = 1
    out.loc[bearish, "htf_trend"] = -1
    out["htf_trend_label"] = out["htf_trend"].map({1: "Bullish", -1: "Bearish", 0: "Neutral"})
    change = out["htf_trend"].ne(out["htf_trend"].shift(1))
    out["htf_trend_segment"] = change.cumsum().astype(int)
    return out


def merge_combo_trend_into_entry(entry_df: pd.DataFrame, trend_df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach the latest closed HTF trend row to each entry bar.

    A trend bar is usable only after its close time is <= the entry bar open time.
    """
    if entry_df.empty:
        return entry_df.copy()
    entry = entry_df.sort_values("bartime").copy()
    if trend_df.empty:
        merged = entry.copy()
        merged["htf_trend"] = pd.NA
        return merged.reset_index(drop=True)

    trend_cols = [
        "bartime",
        "htf_close_time",
        "close",
        "htf_fast_ema",
        "htf_slow_ema",
        "htf_slow_slope",
        "htf_trend",
        "htf_trend_label",
        "htf_trend_segment",
    ]
    trend = (
        trend_df[[col for col in trend_cols if col in trend_df.columns]]
        .sort_values("htf_close_time")
        .rename(columns={"bartime": "htf_bartime", "close": "htf_close"})
    )
    merged = pd.merge_asof(
        entry,
        trend,
        left_on="bartime",
        right_on="htf_close_time",
        direction="backward",
    )
    return merged.sort_values("bartime").reset_index(drop=True)


def apply_combo_htf_filter(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Filter raw Combo signals by the merged HTF trend regime."""
    out = df.copy()
    if not bool(params.get("HTF_TREND_ENABLED", False)):
        return out

    if "raw_signal" not in out.columns:
        out["raw_signal"] = out.get("signal", 0)
    if "raw_signal_reason" not in out.columns:
        out["raw_signal_reason"] = out.get("signal_reason", "")

    raw = out["raw_signal"].fillna(0).astype(int)
    if "htf_trend" in out.columns:
        trend = pd.to_numeric(out["htf_trend"], errors="coerce")
    else:
        trend = pd.Series(pd.NA, index=out.index, dtype="Float64")
    trend_tf = str(params.get("HTF_TF", "HTF")).upper()
    neutral_block = bool(params.get("HTF_NEUTRAL_BLOCK", True))

    allow_buy = raw.eq(1) & trend.eq(1)
    allow_sell = raw.eq(-1) & trend.eq(-1)

    out["signal"] = 0
    out.loc[allow_buy, "signal"] = 1
    out.loc[allow_sell, "signal"] = -1
    out["filter_reason"] = ""

    blocked = raw.ne(0) & out["signal"].eq(0)
    missing = blocked & trend.isna()
    neutral = blocked & trend.eq(0)
    opposite_buy = blocked & raw.eq(1) & trend.eq(-1)
    opposite_sell = blocked & raw.eq(-1) & trend.eq(1)

    out.loc[missing, "filter_reason"] = f"missing closed {trend_tf} trend"
    out.loc[neutral, "filter_reason"] = f"{trend_tf} trend neutral"
    out.loc[opposite_buy, "filter_reason"] = f"{trend_tf} bearish trend blocks BUY"
    out.loc[opposite_sell, "filter_reason"] = f"{trend_tf} bullish trend blocks SELL"

    if not neutral_block:
        neutral_buy = raw.eq(1) & trend.eq(0)
        neutral_sell = raw.eq(-1) & trend.eq(0)
        out.loc[neutral_buy, "signal"] = 1
        out.loc[neutral_sell, "signal"] = -1
        out.loc[neutral_buy | neutral_sell, "filter_reason"] = ""

    out["signal_reason"] = ""
    valid = out["signal"].ne(0)
    out.loc[valid, "signal_reason"] = out.loc[valid, "raw_signal_reason"].astype(str)
    if "htf_trend_label" in out.columns:
        htf_note = out.loc[valid, "htf_trend_label"].fillna("Unknown").astype(str)
    else:
        htf_note = trend.loc[valid].map({1: "Bullish", -1: "Bearish", 0: "Neutral"}).fillna("Unknown")
    out.loc[valid, "signal_reason"] = (
        out.loc[valid, "signal_reason"] + f" | {trend_tf} trend: " + htf_note
    )
    return out
