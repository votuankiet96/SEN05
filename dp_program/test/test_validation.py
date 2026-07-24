"""Tests for core_engine.shared.warehouse.validation - the OHLCV cleaning gate every
bar passes through before staging/Fact, both in the live and historical
paths. A regression here means bad rows silently reach the warehouse.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from core_engine.shared.warehouse.validation import normalize_tv_hist_df_to_utc, validate_ohlcv_df


@pytest.fixture
def logger():
    log = logging.getLogger("test_validation")
    log.addHandler(logging.NullHandler())
    return log


def _df(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_validate_ohlcv_df_passes_clean_data_unchanged(logger):
    now = _now_naive() - timedelta(minutes=10)
    idx = [now, now + timedelta(minutes=5)]
    df = pd.DataFrame(
        {"open": [1.0, 1.1], "high": [1.2, 1.3], "low": [0.9, 1.0], "close": [1.1, 1.2]},
        index=idx,
    )
    cleaned, had_issues = validate_ohlcv_df(df, "EURUSD", "M5", logger)
    assert had_issues is False
    assert len(cleaned) == 2


def test_validate_ohlcv_df_none_or_empty_passthrough(logger):
    cleaned, had_issues = validate_ohlcv_df(None, "EURUSD", "M5", logger)
    assert cleaned is None
    assert had_issues is False

    empty = pd.DataFrame()
    cleaned, had_issues = validate_ohlcv_df(empty, "EURUSD", "M5", logger)
    assert cleaned.empty
    assert had_issues is False


def test_validate_ohlcv_df_drops_rows_with_null_ohlc(logger):
    now = _now_naive() - timedelta(minutes=10)
    idx = [now, now + timedelta(minutes=5)]
    df = pd.DataFrame(
        {"open": [1.0, None], "high": [1.2, 1.3], "low": [0.9, 1.0], "close": [1.1, 1.2]},
        index=idx,
    )
    cleaned, had_issues = validate_ohlcv_df(df, "EURUSD", "M5", logger)
    assert had_issues is True
    assert len(cleaned) == 1


def test_validate_ohlcv_df_drops_high_less_than_low(logger):
    now = _now_naive() - timedelta(minutes=10)
    idx = [now, now + timedelta(minutes=5)]
    df = pd.DataFrame(
        {"open": [1.0, 1.1], "high": [1.2, 0.5], "low": [0.9, 1.0], "close": [1.1, 1.2]},
        index=idx,
    )
    cleaned, had_issues = validate_ohlcv_df(df, "EURUSD", "M5", logger)
    assert had_issues is True
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["high"] == 1.2


def test_validate_ohlcv_df_dedupes_keeping_first(logger):
    now = _now_naive() - timedelta(minutes=10)
    idx = [now, now]
    df = pd.DataFrame(
        {"open": [1.0, 9.9], "high": [1.2, 9.9], "low": [0.9, 9.9], "close": [1.1, 9.9]},
        index=idx,
    )
    cleaned, had_issues = validate_ohlcv_df(df, "EURUSD", "M5", logger)
    assert had_issues is True
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["open"] == 1.0


def test_validate_ohlcv_df_sorts_non_monotonic_index(logger):
    now = _now_naive() - timedelta(minutes=30)
    idx = [now + timedelta(minutes=5), now]
    df = pd.DataFrame(
        {"open": [2.0, 1.0], "high": [2.2, 1.2], "low": [1.9, 0.9], "close": [2.1, 1.1]},
        index=idx,
    )
    cleaned, had_issues = validate_ohlcv_df(df, "EURUSD", "M5", logger)
    assert had_issues is True
    assert cleaned.index.is_monotonic_increasing
    assert cleaned.iloc[0]["open"] == 1.0


def test_validate_ohlcv_df_drops_future_bars(logger):
    now = _now_naive()
    idx = [now - timedelta(minutes=5), now + timedelta(hours=1)]
    df = pd.DataFrame(
        {"open": [1.0, 99.0], "high": [1.2, 99.2], "low": [0.9, 98.9], "close": [1.1, 99.1]},
        index=idx,
    )
    # normalize_timestamps=False: this test targets the future-cutoff check in
    # isolation. With normalization on, a naive index is treated as local wall
    # clock and shifted to UTC first, which would move the boundary out from
    # under a hand-built UTC-naive index whenever the host isn't UTC+0.
    cleaned, had_issues = validate_ohlcv_df(df, "EURUSD", "M5", logger, normalize_timestamps=False)
    assert had_issues is True
    assert len(cleaned) == 1
    assert cleaned.iloc[0]["open"] == 1.0


def test_validate_ohlcv_df_can_skip_timestamp_normalization(logger):
    now = _now_naive() - timedelta(minutes=10)
    df = pd.DataFrame(
        {"open": [1.0], "high": [1.2], "low": [0.9], "close": [1.1]},
        index=[now],
    )
    cleaned, had_issues = validate_ohlcv_df(df, "EURUSD", "M5", logger, normalize_timestamps=False)
    assert had_issues is False
    assert len(cleaned) == 1


def test_normalize_tv_hist_df_to_utc_converts_tz_aware_index():
    idx = pd.date_range("2026-01-01 10:00", periods=2, freq="5min", tz="US/Eastern")
    df = pd.DataFrame({"open": [1.0, 1.1]}, index=idx)
    result, normalized = normalize_tv_hist_df_to_utc(df)
    assert normalized is True
    assert result.index.tz is None


def test_normalize_tv_hist_df_to_utc_none_or_empty():
    result, normalized = normalize_tv_hist_df_to_utc(None)
    assert result is None
    assert normalized is False

    empty = pd.DataFrame()
    result, normalized = normalize_tv_hist_df_to_utc(empty)
    assert result.empty
    assert normalized is False
