"""Entry, stop-loss, and take-profit levels for AI Trend."""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_python.strategies.dow_stop import DowStopParams, causal_dow_stop


def add_ai_trend_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Add post-signal reference levels for AI Trend.

    Signals remain owned by ``signals.py``. This step enriches signal rows with
    close-based entry references plus a causal Dow stop reference for
    dashboard/export/downstream systems.
    """
    _ = symbol
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_dow"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan

    required = {"signal", "bartime", "close"}
    if out.empty or not required.issubset(out.columns):
        return out

    if {"high", "low"}.issubset(out.columns):
        out["sl_dow"] = causal_dow_stop(
            out,
            params=DowStopParams(
                left=int(params.get("DOW_PIVOT_LEFT", 3)),
                right=int(params.get("DOW_PIVOT_RIGHT", 5)),
                min_atr_mult=float(params.get("DOW_MIN_ATR_MULT", 0.5)),
            ),
        )
    rr = max(0.1, float(params.get("TP_RR", 1.5)))

    signals = out["signal"].fillna(0).astype(int)

    for idx in out.index[signals.ne(0)]:
        direction = int(out.at[idx, "signal"])
        entry_price = out.at[idx, "close"]
        if pd.isna(entry_price):
            continue
        entry_price = float(entry_price)
        entry_time = out.at[idx, "m45_close_time"] if "m45_close_time" in out.columns else out.at[idx, "bartime"]
        out.at[idx, "entry_time"] = entry_time
        out.at[idx, "entry_price"] = entry_price

        sl_dow = out.at[idx, "sl_dow"]
        if pd.isna(sl_dow):
            continue

        sl_price = float(sl_dow)
        if direction == 1:
            risk = entry_price - sl_price
            if risk <= 0:
                continue
            tp_price = entry_price + rr * risk
        else:
            risk = sl_price - entry_price
            if risk <= 0:
                continue
            tp_price = entry_price - rr * risk

        out.at[idx, "sl_price"] = sl_price
        out.at[idx, "tp_price"] = tp_price
        out.at[idx, "risk_reward"] = rr
    return out
