"""Entry, Stop Loss and Take Profit levels for MA Cross.

The signal candle close is the reference market-entry price. Stop Loss uses
the signal candle extreme plus the symbol-specific X buffer; Take Profit uses
KTP times the signal candle ATR.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from core_python.indicators.core import safe_ratio


def add_ma_cross_levels(
    df: pd.DataFrame,
    params: dict,
    symbol: str | None = None,
) -> pd.DataFrame:
    """Add close-entry levels and their actual Risk/Reward ratio.

    BUY:
        entry = close
        SL = low - X
        TP = close + KTP * ATR

    SELL:
        entry = close
        SL = high + X
        TP = close - KTP * ATR
    """
    _ = symbol
    out = df.copy()
    x = float(params["X"])
    ktp = float(params["KTP"])

    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan

    buy = out["signal"].eq(1)
    sell = out["signal"].eq(-1)
    has_signal = buy | sell

    out.loc[has_signal, "entry_time"] = out.loc[has_signal, "bartime"]
    out.loc[has_signal, "entry_price"] = out.loc[has_signal, "close"]

    out.loc[buy, "sl_price"] = out.loc[buy, "low"] - x
    out.loc[sell, "sl_price"] = out.loc[sell, "high"] + x
    out.loc[has_signal, "tp_price"] = (
        out.loc[has_signal, "close"]
        + out.loc[has_signal, "signal"] * ktp * out.loc[has_signal, "atr"]
    )

    risk_distance = pd.Series(np.nan, index=out.index, dtype="float64")
    risk_distance.loc[buy] = (
        out.loc[buy, "entry_price"] - out.loc[buy, "sl_price"]
    )
    risk_distance.loc[sell] = (
        out.loc[sell, "sl_price"] - out.loc[sell, "entry_price"]
    )
    reward_distance = (out["tp_price"] - out["entry_price"]).abs()
    positive_risk = risk_distance.where(risk_distance > 0)
    out.loc[has_signal, "risk_reward"] = safe_ratio(
        reward_distance,
        positive_risk,
    ).loc[has_signal]
    return out
