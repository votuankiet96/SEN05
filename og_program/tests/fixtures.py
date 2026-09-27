"""Deterministic synthetic OHLCV fixtures for characterization tests.

These are not meant to resemble real market data closely — only to be
reproducible (fixed seed) and varied enough to exercise crossovers, MACD
sign changes, and KNN trend flips in the strategies under test.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def make_ohlcv(
    n: int = 400,
    *,
    freq_minutes: int = 5,
    seed: int = 42,
    start: str = "2026-01-05",
    drift: float = 0.0,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=drift, scale=1.0, size=n).cumsum()
    base = 100.0 + steps
    open_ = base
    close = base + rng.normal(0.0, 0.35, size=n)
    high = np.maximum(open_, close) + np.abs(rng.normal(0.25, 0.15, size=n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0.25, 0.15, size=n))
    volume = rng.integers(100, 1000, size=n).astype(float)
    bartime = pd.date_range(start=start, periods=n, freq=f"{freq_minutes}min")
    return pd.DataFrame(
        {
            "bartime": bartime,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
