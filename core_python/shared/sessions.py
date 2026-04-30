"""Session and timestamp helpers shared by strategy packages."""

from __future__ import annotations

from typing import Any

import pandas as pd

from core_python.shared.timeframes import expected_timedelta_for_tf


def normalize_datetime_index_utc(
    index: pd.Index,
    *,
    source_timezone: str | None,
) -> pd.DatetimeIndex:
    """Return an explicit UTC DatetimeIndex."""
    dt_index = pd.DatetimeIndex(index)
    if dt_index.tz is None:
        dt_index = dt_index.tz_localize(source_timezone or "UTC")
    return dt_index.tz_convert("UTC")


def session_mask_utc(data: pd.DataFrame | pd.Index, hours_utc: list[int] | None) -> pd.Series:
    """Return a boolean mask for UTC bar-open hours; empty hours means all bars."""
    index = data.index if isinstance(data, pd.DataFrame) else data
    dt_index = normalize_datetime_index_utc(index, source_timezone="UTC")
    if not hours_utc:
        return pd.Series(True, index=index)
    valid_hours = {int(hour) for hour in hours_utc}
    return pd.Series(dt_index.hour.isin(valid_hours), index=index)


def detect_time_gaps(
    index: pd.Index,
    *,
    tf: str = "H4",
    threshold: float = 1.5,
) -> dict[str, Any]:
    """Summarize unexpected time gaps while classifying normal weekend closures."""
    if len(index) < 2:
        return _empty_gap_report()

    dt_index = normalize_datetime_index_utc(index, source_timezone="UTC").sort_values()
    expected = expected_timedelta_for_tf(tf)
    min_gap = expected * float(threshold)
    diffs = dt_index.to_series().diff()
    gap_mask = diffs > min_gap
    if not bool(gap_mask.any()):
        return _empty_gap_report()

    has_weekend_bars = bool((dt_index.weekday >= 5).any())
    expected_weekend = 0
    unexpected: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timedelta]] = []

    for end_ts, delta in diffs[gap_mask].items():
        pos = dt_index.get_loc(end_ts)
        if isinstance(pos, slice):
            start_pos = pos.start - 1
        else:
            start_pos = int(pos) - 1
        if start_pos < 0:
            continue
        start_ts = dt_index[start_pos]
        if _is_expected_weekend_gap(start_ts, end_ts, has_weekend_bars):
            expected_weekend += 1
        else:
            unexpected.append((start_ts, end_ts, delta))

    max_gap = max((delta for _, _, delta in unexpected), default=max(diffs[gap_mask]))
    first_start = unexpected[0][0] if unexpected else None
    first_end = unexpected[0][1] if unexpected else None
    return {
        "n_time_gaps": int(gap_mask.sum()),
        "n_expected_weekend_gaps": int(expected_weekend),
        "n_unexpected_time_gaps": int(len(unexpected)),
        "max_gap": max_gap,
        "first_unexpected_gap_start": first_start,
        "first_unexpected_gap_end": first_end,
    }


def _is_expected_weekend_gap(
    start_ts: pd.Timestamp,
    end_ts: pd.Timestamp,
    has_weekend_bars: bool,
) -> bool:
    if has_weekend_bars:
        return False
    return start_ts.weekday() in {4, 5} and end_ts.weekday() in {6, 0}


def _empty_gap_report() -> dict[str, Any]:
    return {
        "n_time_gaps": 0,
        "n_expected_weekend_gaps": 0,
        "n_unexpected_time_gaps": 0,
        "max_gap": pd.NaT,
        "first_unexpected_gap_start": None,
        "first_unexpected_gap_end": None,
    }


__all__ = [
    "detect_time_gaps",
    "normalize_datetime_index_utc",
    "session_mask_utc",
]
