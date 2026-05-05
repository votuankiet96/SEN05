"""Visual entry, stop-loss and take-profit levels for MA Cross signals."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_ma_cross_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """Add next-bar-open visual entry/SL/TP levels for MA Cross signals."""
    _ = symbol
    out = df.copy()
    spread = float(params["SPREAD_PTS"])
    slippage = float(params["SLIPPAGE_PTS"])
    stop_mult = float(params["ATR_STOP_MULT"])
    tp_mult = float(params["ATR_TP_MULT"])

    next_open = out["open"].shift(-1)
    next_time = out["bartime"].shift(-1)

    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan

    has_next = next_open.notna() & next_time.notna()
    buy = out["signal"].eq(1) & has_next
    sell = out["signal"].eq(-1) & has_next
    has_signal = buy | sell

    cost = spread + slippage
    out.loc[has_signal, "entry_time"] = next_time.loc[has_signal]
    out.loc[buy, "entry_price"] = next_open.loc[buy] + cost
    out.loc[sell, "entry_price"] = next_open.loc[sell] - cost

    sl_dist = stop_mult * out["atr"]
    tp_dist = tp_mult * out["atr"]
    out.loc[buy, "sl_price"] = out.loc[buy, "entry_price"] - sl_dist.loc[buy]
    out.loc[sell, "sl_price"] = out.loc[sell, "entry_price"] + sl_dist.loc[sell]
    if tp_mult > 0:
        out.loc[buy, "tp_price"] = out.loc[buy, "entry_price"] + tp_dist.loc[buy]
        out.loc[sell, "tp_price"] = out.loc[sell, "entry_price"] - tp_dist.loc[sell]
        out.loc[has_signal, "risk_reward"] = tp_mult / stop_mult
    return out

