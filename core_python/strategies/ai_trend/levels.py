"""Entry, stop-loss, and take-profit levels for AI Trend."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_ai_trend_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Add trade levels for AI Trend signals.

    AI Trend is a market-order signal on the entry bar close. The numeric
    entry reference uses the signal bar close so a newly closed signal can have
    SL/TP immediately. Stop-loss comes from the nearest confirmed Dow swing:
    BUY uses the latest confirmed Dow low, SELL uses the latest confirmed Dow
    high. Dow swings are only usable after their right-side confirmation bars
    are available, so the lookup requires
    ``pivot_index + DOW_PIVOT_RIGHT <= signal_index``.
    """
    _ = symbol
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan

    required = {"signal", "bartime", "close", "dow_pivot_type", "dow_pivot_price"}
    if out.empty or not required.issubset(out.columns):
        return out

    right = max(1, int(params.get("DOW_PIVOT_RIGHT", 5)))
    rr = max(0.1, float(params.get("TP_RR", 1.5)))

    signals = out["signal"].fillna(0).astype(int)
    pivot_types = out["dow_pivot_type"]
    pivot_prices = out["dow_pivot_price"]

    for idx in out.index[signals.ne(0)]:
        pos = out.index.get_loc(idx)

        direction = int(out.at[idx, "signal"])
        entry_price = out.at[idx, "close"]
        if pd.isna(entry_price):
            continue
        entry_price = float(entry_price)
        entry_time = out.at[idx, "m45_close_time"] if "m45_close_time" in out.columns else out.at[idx, "bartime"]
        out.at[idx, "entry_time"] = entry_time
        out.at[idx, "entry_price"] = entry_price

        pivot_type = "low" if direction == 1 else "high"
        confirmed_positions = [
            pivot_pos
            for pivot_pos in range(0, pos + 1)
            if pivot_pos + right <= pos
            and pd.notna(pivot_types.iloc[pivot_pos])
            and str(pivot_types.iloc[pivot_pos]) == pivot_type
            and pd.notna(pivot_prices.iloc[pivot_pos])
        ]
        if not confirmed_positions:
            continue

        sl_price = float(pivot_prices.iloc[confirmed_positions[-1]])
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
