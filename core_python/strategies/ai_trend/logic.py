"""Signal logic for AI Trend / KNN H3 + EMA15/30 M30 pullback strategy."""

from __future__ import annotations

import numpy as np
import pandas as pd

from modules.indicators import calc_ai_trend_navigator, calc_atr, calc_ema

from .config import STRATEGY, get_indicator_params


def _ensure_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy indexed by BarTime."""
    out = df.copy()
    if "BarTime" in out.columns:
        out["BarTime"] = pd.to_datetime(out["BarTime"])
        out = out.set_index("BarTime")
    if not isinstance(out.index, pd.DatetimeIndex):
        raise TypeError("DataFrame must have a DatetimeIndex or a BarTime column.")
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _to_title_case(df: pd.DataFrame) -> pd.DataFrame:
    """Convert lowercase OHLCV schema to Title-Case indicator schema."""
    work = _ensure_datetime_index(df)
    cols = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    missing = [c for c in ("open", "high", "low", "close") if c not in work.columns]
    if missing:
        raise KeyError(f"Missing OHLC columns: {missing}")
    out = work.rename(columns={k: v for k, v in cols.items() if k in work.columns})
    if "Volume" not in out.columns:
        out["Volume"] = 0.0
    return out[["Open", "High", "Low", "Close", "Volume"]]


def _timeframe_delta(tf: str) -> pd.Timedelta:
    tf = tf.upper().strip()
    if tf.startswith("M"):
        return pd.Timedelta(minutes=int(tf[1:]))
    if tf.startswith("H"):
        return pd.Timedelta(hours=int(tf[1:]))
    if tf == "D1":
        return pd.Timedelta(days=1)
    if tf == "W":
        return pd.Timedelta(weeks=1)
    raise ValueError(f"Unsupported timeframe code: {tf}")


def _as_bar_time_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.reset_index()
    idx_name = df.index.name or "index"
    return out.rename(columns={idx_name: "BarTime"}).sort_values("BarTime")


def _confirmed_bias(raw_bias: pd.Series, confirm_bars: int) -> pd.Series:
    confirm_bars = max(1, int(confirm_bars))
    if confirm_bars == 1:
        return raw_bias.fillna(0).astype(int)
    long_ok = raw_bias.eq(1).rolling(confirm_bars, min_periods=confirm_bars).sum() >= confirm_bars
    short_ok = raw_bias.eq(-1).rolling(confirm_bars, min_periods=confirm_bars).sum() >= confirm_bars
    return pd.Series(np.where(long_ok, 1, np.where(short_ok, -1, 0)), index=raw_bias.index)


def build_h3_trend_features(df_h3: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Compute closed-bar H3 AI Trend features."""
    p = {**get_indicator_params(), **(params or {})}
    h3_title = _to_title_case(df_h3)
    ai = calc_ai_trend_navigator(
        h3_title,
        price_value=p["AI_PRICE_VALUE"],
        ma_len=int(p["AI_MA_LEN"]),
        target_value=p["AI_TARGET_VALUE"],
        target_len=int(p["AI_TARGET_LEN"]),
        number_of_closest_values=int(p["AI_K"]),
        smoothing_period=int(p["AI_SMOOTHING_PERIOD"]),
    )

    raw_bias = pd.Series(0, index=ai.index, dtype=int)
    raw_bias[(ai["knn"] > ai["average"]) & (ai["direction"] > 0)] = 1
    raw_bias[(ai["knn"] < ai["average"]) & (ai["direction"] < 0)] = -1
    bias = _confirmed_bias(raw_bias, int(p["AI_CONFIRM_BARS"]))

    out = pd.DataFrame({
        "h3_ai_knn": ai["knn"],
        "h3_ai_avg": ai["average"],
        "h3_ai_prediction": ai["prediction"],
        "h3_ai_direction": ai["direction"],
        "h3_bias_raw": raw_bias,
        "h3_bias": bias,
    }, index=h3_title.index)
    return out


def add_ai_trend_indicators(
    df_m30: pd.DataFrame,
    df_h3: pd.DataFrame,
    params: dict | None = None,
    *,
    trend_tf: str = "H3",
) -> pd.DataFrame:
    """Add H3 AI Trend features and M30 EMA/ATR columns.

    H3 features are shifted by the H3 bar duration before merge_asof. This means
    an H3 candle that opens at 09:00 is not visible to M30 logic until 12:00,
    avoiding higher-timeframe lookahead.
    """
    p = {**get_indicator_params(), **(params or {})}
    entry = _ensure_datetime_index(df_m30)
    entry_title = _to_title_case(entry)

    out = entry.copy()
    out["ema_fast"] = calc_ema(out["close"], int(p["EMA_FAST"]))
    out["ema_slow"] = calc_ema(out["close"], int(p["EMA_SLOW"]))
    out["atr"] = calc_atr(entry_title, int(p["ATR_PERIOD"]))
    out["ma"] = out["ema_fast"] if p.get("TRAIL_MA") == "ema_fast" else out["ema_slow"]
    out["prev_close"] = out["close"].shift(1)
    out["prev_ema_fast"] = out["ema_fast"].shift(1)
    out["prev_ema_slow"] = out["ema_slow"].shift(1)

    h3_feat = build_h3_trend_features(df_h3, p)
    h3_feat = h3_feat.copy()
    h3_feat.index = h3_feat.index + _timeframe_delta(trend_tf)
    h3_feat.index.name = "BarTime"

    merged = pd.merge_asof(
        _as_bar_time_frame(out),
        _as_bar_time_frame(h3_feat),
        on="BarTime",
        direction="backward",
    )
    merged = merged.set_index("BarTime").sort_index()
    return merged


def session_mask(df: pd.DataFrame, hours_utc: list) -> pd.Series:
    """Build a UTC session mask. Empty hours list means all bars are allowed."""
    work = _ensure_datetime_index(df)
    if not hours_utc:
        return pd.Series(True, index=work.index)
    return pd.Series(work.index.hour.isin(hours_utc), index=work.index)


def detect_ai_trend_signals(
    df: pd.DataFrame,
    sess_mask: pd.Series,
    sym_key: str | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    """Detect M30 EMA pullback/reclaim entries aligned with H3 AI Trend bias."""
    p = {**get_indicator_params(), **(params or {})}
    out = _ensure_datetime_index(df)

    sess = sess_mask.reindex(out.index).fillna(False).astype(bool)
    in_window = out.get("in_window", pd.Series(True, index=out.index))

    required = ["h3_bias", "ema_fast", "ema_slow", "atr", "prev_close", "prev_ema_fast"]
    valid = sess & in_window
    for col in required:
        valid &= out[col].notna()

    ema_gap_atr = (out["ema_fast"] - out["ema_slow"]).abs() / out["atr"].replace(0, np.nan)
    distance_atr = (out["close"] - out["ema_slow"]).abs() / out["atr"].replace(0, np.nan)
    gap_ok = ema_gap_atr >= float(p["MIN_EMA_GAP_ATR"])
    distance_ok = distance_atr <= float(p["MAX_ENTRY_DISTANCE_ATR"])

    require_slope = bool(p["REQUIRE_EMA_SLOPE"])
    slope_long = out["ema_slow"] > out["prev_ema_slow"] if require_slope else True
    slope_short = out["ema_slow"] < out["prev_ema_slow"] if require_slope else True

    lookback = max(1, int(p["PULLBACK_LOOKBACK"]))
    pullback_long = (out["low"] <= out["ema_fast"]).rolling(lookback, min_periods=1).max().astype(bool)
    deep_pullback_long = (out["low"] <= out["ema_slow"]).rolling(lookback, min_periods=1).max().astype(bool)
    pullback_short = (out["high"] >= out["ema_fast"]).rolling(lookback, min_periods=1).max().astype(bool)
    deep_pullback_short = (out["high"] >= out["ema_slow"]).rolling(lookback, min_periods=1).max().astype(bool)

    if bool(p["REQUIRE_RECLAIM_CROSS"]):
        reclaim_long = ((out["prev_close"] <= out["prev_ema_fast"]) & (out["close"] > out["ema_fast"])) | (
            deep_pullback_long & (out["close"] > out["ema_fast"])
        )
        reclaim_short = ((out["prev_close"] >= out["prev_ema_fast"]) & (out["close"] < out["ema_fast"])) | (
            deep_pullback_short & (out["close"] < out["ema_fast"])
        )
    else:
        reclaim_long = out["close"] > out["ema_fast"]
        reclaim_short = out["close"] < out["ema_fast"]

    h3_long = out["h3_bias"] == 1
    h3_short = out["h3_bias"] == -1
    ema_long = (out["ema_fast"] > out["ema_slow"]) & slope_long
    ema_short = (out["ema_fast"] < out["ema_slow"]) & slope_short
    candle_long = out["close"] > out["open"]
    candle_short = out["close"] < out["open"]

    ktp = float(p.get("KTP", STRATEGY["ktp"]))
    min_rr = float(p.get("MIN_RR", STRATEGY["min_rr"]))
    x = float(p.get("X", 0.0))
    sl_dist = out["high"] - out["low"] + 2 * x
    tp_dist = ktp * out["atr"]
    rr = tp_dist / sl_dist.replace(0, np.nan)
    rr_ok = rr >= min_rr

    buy = (
        valid & h3_long & ema_long & pullback_long & reclaim_long &
        candle_long & gap_ok & distance_ok & rr_ok
    )
    sell = (
        valid & h3_short & ema_short & pullback_short & reclaim_short &
        candle_short & gap_ok & distance_ok & rr_ok
    )

    out["signal"] = 0
    out.loc[buy, "signal"] = 1
    out.loc[sell, "signal"] = -1
    out["rr"] = rr.round(2)
    out["pass_rr"] = rr_ok.fillna(False)
    out["ema_gap_atr"] = ema_gap_atr.round(4)
    out["entry_distance_atr"] = distance_atr.round(4)
    out["setup"] = ""
    out.loc[buy, "setup"] = "H3_KNN_UP_M30_EMA_RECLAIM"
    out.loc[sell, "setup"] = "H3_KNN_DN_M30_EMA_REJECT"
    return out


def build_signal_frame(
    df_m30: pd.DataFrame,
    df_h3: pd.DataFrame,
    *,
    params: dict | None = None,
    session_hours_utc: list | None = None,
) -> pd.DataFrame:
    """Convenience pipeline: indicators -> session mask -> signals."""
    df_ind = add_ai_trend_indicators(df_m30, df_h3, params)
    sess = session_mask(df_ind, session_hours_utc or [])
    return detect_ai_trend_signals(df_ind, sess, params=params)

