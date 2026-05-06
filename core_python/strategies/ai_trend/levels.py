"""Level placeholders for AI Trend phase 1."""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_ai_trend_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """
    Keep the common strategy contract without adding trade levels yet.

    Entry, stop-loss, and take-profit rules will be added in a later phase.
    """
    _ = params
    _ = symbol
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan
    return out

