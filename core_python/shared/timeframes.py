"""Shared timeframe metadata for analytics and data-quality checks."""

from __future__ import annotations

import pandas as pd


TIMEFRAME_MINUTES: dict[str, int] = {
    "M5": 5,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "M45": 45,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "D1": 1440,
}


def normalize_timeframe_code(tf_code: str | None) -> str:
    return (tf_code or "H4").strip().upper()


def expected_timedelta_for_tf(tf_code: str | None) -> pd.Timedelta:
    minutes = TIMEFRAME_MINUTES.get(normalize_timeframe_code(tf_code), TIMEFRAME_MINUTES["H4"])
    return pd.Timedelta(minutes=minutes)


def bars_per_year(tf_code: str | None) -> int:
    minutes = TIMEFRAME_MINUTES.get(normalize_timeframe_code(tf_code), TIMEFRAME_MINUTES["H4"])
    if minutes >= 1440:
        return 252
    return int(252 * (24 * 60 // minutes))


__all__ = [
    "TIMEFRAME_MINUTES",
    "bars_per_year",
    "expected_timedelta_for_tf",
    "normalize_timeframe_code",
]
