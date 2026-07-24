"""Level contract for KNN Combo.

This strategy intentionally does not calculate entry, stop-loss, or take-profit
levels yet. It only surfaces accepted and filtered signals.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def add_knn_combo_levels(df: pd.DataFrame, params: dict, symbol: str | None = None) -> pd.DataFrame:
    """Return signal rows with empty level columns for the shared contract."""
    _ = params
    _ = symbol
    out = df.copy()
    out["entry_time"] = pd.NaT
    out["entry_price"] = np.nan
    out["sl_price"] = np.nan
    out["tp_price"] = np.nan
    out["risk_reward"] = np.nan
    return out

