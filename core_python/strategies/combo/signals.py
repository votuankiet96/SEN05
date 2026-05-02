"""Combo alpha model: indicators, signals, and scan rules."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_python.shared.sessions import session_mask_utc
from modules.indicators import add_indicators as _add_base_indicators

from .config import STRATEGY, get_combo_symbol_params, get_indicator_params, get_symbol_ktp


def _rr_filter_enabled(min_rr: object) -> bool:
    if min_rr is None:
        return False
    try:
        return not bool(pd.isna(min_rr))
    except Exception:
        return True


def _rr_ok(rr: pd.Series, min_rr: object) -> pd.Series:
    if not _rr_filter_enabled(min_rr):
        return pd.Series(True, index=rr.index)
    return rr >= float(min_rr)


def add_combo_indicators(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    defaults = get_indicator_params()
    full_p = {**defaults, **params} if isinstance(params, dict) else defaults

    has_dt_index = isinstance(df.index, pd.DatetimeIndex)
    df_flat = df.reset_index() if has_dt_index else df
    df_out = _add_base_indicators(df_flat, full_p)
    if has_dt_index:
        df_out = df_out.set_index("BarTime")

    df_out["prev_close"] = df_out["close"].shift(1)
    df_out["prev_ma"] = df_out["ma"].shift(1)
    return df_out


add_backtest_indicators = add_combo_indicators


def session_mask(df: pd.DataFrame, hours_utc: list) -> pd.Series:
    return session_mask_utc(df, hours_utc)


def detect_combo_signals(
    df: pd.DataFrame,
    sess_mask: pd.Series,
    sym_key: str | None = None,
    params: dict | None = None,
) -> pd.DataFrame:
    df = df.copy()
    p = params or get_indicator_params()

    if params and "X" in params:
        x = float(params["X"])
    elif sym_key:
        x = float(get_combo_symbol_params(sym_key)["x"])
    else:
        x = 0.0

    ktp = get_symbol_ktp(sym_key) if sym_key else p.get("KTP", STRATEGY["ktp"])
    min_rr = p.get("MIN_RR", STRATEGY["min_rr"])

    valid = (
        sess_mask
        & df.get("in_window", pd.Series(True, index=df.index))
        & df["ma"].notna()
        & df["prev_ma"].notna()
        & df["macd_h"].notna()
        & df["atr"].notna()
    )

    cross_up = (df["prev_close"] <= df["prev_ma"]) & (df["close"] > df["ma"])
    cross_down = (df["prev_close"] >= df["prev_ma"]) & ~(df["close"] > df["ma"])

    buy_cond = valid & cross_up & (df["close"] > df["open"]) & (df["macd_h"] > 0)
    sell_cond = valid & cross_down & (df["close"] < df["open"]) & (df["macd_h"] < 0)

    sl_dist = df["high"] - df["low"] + 2 * x
    tp_dist = ktp * df["atr"]
    rr = tp_dist / sl_dist.replace(0, np.nan)
    rr_ok = _rr_ok(rr, min_rr)

    df["signal"] = 0
    df.loc[buy_cond & rr_ok, "signal"] = 1
    df.loc[sell_cond & rr_ok, "signal"] = -1
    df["rr"] = rr.round(2)
    return df


detect_signals = detect_combo_signals


def build_raw_signal_masks(
    df: pd.DataFrame,
    hours_utc: list,
) -> tuple[pd.Series, pd.Series]:
    if "BarTime" not in df.columns:
        raise ValueError("build_raw_signal_masks() requires a 'BarTime' column.")
    if not pd.api.types.is_datetime64_any_dtype(df["BarTime"]):
        raise TypeError("build_raw_signal_masks() requires datetime-like 'BarTime' values.")
    sess = df["BarTime"].dt.hour.isin(hours_utc) if hours_utc else pd.Series(True, index=df.index)
    valid = sess & df["ma"].notna() & df["macd_h"].notna() & df["atr"].notna()
    prev_close = df["close"].shift(1)
    prev_ma = df["ma"].shift(1)
    bull_candle = df["close"] > df["open"]
    bear_candle = df["close"] < df["open"]
    above_ma = df["close"] > df["ma"]

    buy_raw = valid & (prev_close <= prev_ma) & bull_candle & above_ma & (df["macd_h"] > 0)
    sell_raw = valid & (prev_close >= prev_ma) & bear_candle & (~above_ma) & (df["macd_h"] < 0)
    return buy_raw, sell_raw


def build_fast_backtest_signal_masks(
    df: pd.DataFrame,
    *,
    hours_utc: list,
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    min_rr: float | None,
    x_actual: float,
    ktp: float,
) -> tuple[pd.Series, pd.Series]:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("build_fast_backtest_signal_masks() requires a DatetimeIndex.")
    sess = (
        pd.Series(df.index.hour.isin(hours_utc), index=df.index)
        if hours_utc
        else pd.Series(True, index=df.index)
    )
    in_window = (df.index >= start_ts) & (df.index <= end_ts)
    valid = (
        sess
        & in_window
        & df["ma"].notna()
        & df["prev_ma"].notna()
        & df["macd_h"].notna()
        & df["atr"].notna()
    )

    cross_up = (df["prev_close"] <= df["prev_ma"]) & (df["close"] > df["ma"])
    cross_down = (df["prev_close"] >= df["prev_ma"]) & ~(df["close"] > df["ma"])

    sl_dist = df["high"] - df["low"] + 2 * x_actual
    tp_dist = ktp * df["atr"]
    rr = (tp_dist / sl_dist.replace(0, np.nan)).fillna(0)
    rr_ok = _rr_ok(rr, min_rr)

    buy_raw = valid & cross_up & (df["close"] > df["open"]) & (df["macd_h"] > 0) & rr_ok
    sell_raw = valid & cross_down & (df["close"] < df["open"]) & (df["macd_h"] < 0) & rr_ok
    return buy_raw, sell_raw


def resolve_trade_hit(
    bar: pd.Series,
    direction: int,
    trade_sl: float,
    trade_tp: float,
) -> str | None:
    if direction == 1:
        hit_sl = bar["low"] <= trade_sl
        hit_tp = bar["high"] >= trade_tp
    else:
        hit_sl = bar["high"] >= trade_sl
        hit_tp = bar["low"] <= trade_tp

    if not (hit_sl or hit_tp):
        return None
    return "TP" if (hit_tp and not hit_sl) else "SL"


def build_signal_record(
    bar: pd.Series,
    direction: int,
    x: float,
    ktp: float,
    min_rr: float | None,
    *,
    extra: dict | None = None,
) -> dict:
    sl_dist = float(bar["high"]) - float(bar["low"]) + 2 * float(x)
    tp_dist = ktp * float(bar["atr"])
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0.0
    entry = float(bar["high"]) + float(x) if direction == 1 else float(bar["low"]) - float(x)
    sl = float(bar["low"]) - float(x) if direction == 1 else float(bar["high"]) + float(x)
    tp = entry + direction * ktp * float(bar["atr"])
    passes = True if not _rr_filter_enabled(min_rr) else rr >= float(min_rr)

    record = {
        "bar_time": bar["BarTime"],
        "direction": "BUY" if direction == 1 else "SELL",
        "direction_int": direction,
        "outcome": "Open" if passes else "-",
        "high": bar["high"],
        "low": bar["low"],
        "entry": round(entry, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "sl_dist": round(sl_dist, 4),
        "tp_dist": round(tp_dist, 4),
        "atr": round(bar["atr"], 4),
        "ma": round(bar["ma"], 4),
        "macd_h": round(bar["macd_h"], 6),
        "rr": rr,
        "pass_rr": passes,
    }
    if extra:
        record.update(extra)
    return record


def scan_signals_reversal(df: pd.DataFrame, cfg: dict, p: dict) -> pd.DataFrame:
    x = cfg["x"]
    ktp = p["KTP"]
    min_rr = p["MIN_RR"]
    buy_raw, sell_raw = build_raw_signal_masks(df, cfg["session_hours_utc"])

    signals = []
    in_trade = False
    trade_d = 0
    trade_sl = 0.0
    trade_tp = 0.0
    open_signal_idx = None

    for i, (_, bar) in enumerate(df.iterrows()):
        d = 0
        is_reversal = False

        if in_trade:
            outcome = resolve_trade_hit(bar, trade_d, trade_sl, trade_tp)
            if outcome is not None:
                in_trade = False
                if open_signal_idx is not None:
                    signals[open_signal_idx]["outcome"] = outcome
                    open_signal_idx = None
                continue

            if buy_raw.iloc[i] and trade_d == -1:
                d = 1
                is_reversal = True
                in_trade = False
                if open_signal_idx is not None:
                    signals[open_signal_idx]["outcome"] = "Reversed"
                    open_signal_idx = None
            elif sell_raw.iloc[i] and trade_d == 1:
                d = -1
                is_reversal = True
                in_trade = False
                if open_signal_idx is not None:
                    signals[open_signal_idx]["outcome"] = "Reversed"
                    open_signal_idx = None
            else:
                continue
        else:
            d = 1 if buy_raw.iloc[i] else (-1 if sell_raw.iloc[i] else 0)
            if d == 0:
                continue

        record = build_signal_record(
            bar,
            d,
            x,
            ktp,
            min_rr,
            extra={"is_reversal": is_reversal},
        )
        if record["pass_rr"]:
            in_trade = True
            trade_d = d
            trade_sl = record["sl"]
            trade_tp = record["tp"]
            open_signal_idx = len(signals)

        signals.append(record)

    return pd.DataFrame(signals)


__all__ = [
    "add_backtest_indicators",
    "add_combo_indicators",
    "build_fast_backtest_signal_masks",
    "build_raw_signal_masks",
    "build_signal_record",
    "detect_combo_signals",
    "detect_signals",
    "resolve_trade_hit",
    "scan_signals_reversal",
    "session_mask",
]
