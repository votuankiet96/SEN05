"""Visual entry, stop-loss and take-profit levels for Combo signals."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_combo_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """Add pending breakout entry/SL/TP levels for visual verification."""
    _ = symbol
    out = df.copy()
    x = float(params["X"])
    ktp = float(params["KTP"])

    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = out.get("rr", np.nan)

    buy = out["signal"].eq(1)
    sell = out["signal"].eq(-1)
    has_signal = buy | sell

    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[buy, "entry_price"] = out.loc[buy, "high"] + x
    out.loc[sell, "entry_price"] = out.loc[sell, "low"] - x
    out.loc[buy, "sl_price"] = out.loc[buy, "low"] - x
    out.loc[sell, "sl_price"] = out.loc[sell, "high"] + x
    out.loc[has_signal, "tp_price"] = (
        out.loc[has_signal, "entry_price"]
        + out.loc[has_signal, "signal"] * ktp * out.loc[has_signal, "atr"]
    )
    return out

