from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import _helpers as helpers


def test_validate_ohlcv_df_drops_invalid_duplicate_unsorted_and_future_bars():
    now = datetime.now(timezone.utc)
    t1 = now - timedelta(minutes=20)
    t2 = now - timedelta(minutes=15)
    t3 = now - timedelta(minutes=10)
    t4 = now - timedelta(minutes=5)
    future = now + timedelta(minutes=5)

    df = pd.DataFrame(
        [
            {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0, "volume": 10},
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 11},
            {"open": 999.0, "high": 1001.0, "low": 998.0, "close": 1000.0, "volume": 12},
            {"open": 110.0, "high": 109.0, "low": 111.0, "close": 110.5, "volume": 13},
            {"open": 120.0, "high": 121.0, "low": 119.0, "close": 120.5, "volume": 14},
            {"open": None, "high": 131.0, "low": 129.0, "close": 130.5, "volume": 15},
        ],
        index=[t3, t1, t3, t2, future, t4],
    )

    cleaned, had_issues = helpers._validate_ohlcv_df(
        df,
        "US30",
        "M5",
        logging.getLogger("test.helpers"),
    )

    assert had_issues is True
    expected_t1 = t1.replace(tzinfo=None)
    expected_t3 = t3.replace(tzinfo=None)
    assert list(cleaned.index) == [expected_t1, expected_t3]
    assert cleaned.loc[expected_t1, "open"] == 100.0
    assert cleaned.loc[expected_t3, "open"] == 103.0


def test_validate_ohlcv_df_keeps_contiguous_m45_anchor_shift():
    idx = [
        datetime(2026, 3, 30, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 30, 0, 45, tzinfo=timezone.utc),
        datetime(2026, 3, 30, 1, 30, tzinfo=timezone.utc),
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 30, 2, 45, tzinfo=timezone.utc),
        datetime(2026, 3, 30, 3, 30, tzinfo=timezone.utc),
    ]
    df = pd.DataFrame(
        [
            {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
            {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
            {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
            {"open": 1.1, "high": 2.1, "low": 0.6, "close": 1.6, "volume": 11},
            {"open": 1.1, "high": 2.1, "low": 0.6, "close": 1.6, "volume": 11},
            {"open": 1.1, "high": 2.1, "low": 0.6, "close": 1.6, "volume": 11},
        ],
        index=idx,
    )

    cleaned, had_issues = helpers._validate_ohlcv_df(
        df,
        "GOLD",
        "M45",
        logging.getLogger("test.helpers"),
    )

    assert had_issues is False
    assert list(cleaned.index) == [dt.replace(tzinfo=None) for dt in idx]


def test_pull_and_store_cleans_staging_if_direct_etl_fails(monkeypatch):
    class FakeTV:
        def get_hist(self, **kwargs):
            idx = [
                datetime(2026, 4, 1, 0, 0),
                datetime(2026, 4, 1, 0, 5),
                datetime(2026, 4, 1, 0, 10),
            ]
            return pd.DataFrame(
                [
                    {"open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 1},
                    {"open": 11.0, "high": 12.0, "low": 10.0, "close": 11.5, "volume": 2},
                    {"open": 12.0, "high": 13.0, "low": 11.0, "close": 12.5, "volume": 3},
                ],
                index=idx,
            )

    cleanup_calls: list[tuple[int, str]] = []

    monkeypatch.setattr(helpers, "wait_for_historical_slot", lambda *args, **kwargs: True)
    monkeypatch.setattr(helpers, "insert_staging_batch", lambda df, symbol_id, staging: len(df))
    monkeypatch.setattr(helpers, "clean_staging_transitions", lambda *args, **kwargs: 0)

    def _fail_etl(symbol_id, tf_code, staging):
        raise helpers.DatabaseWriteError("etl failed")

    def _cleanup(symbol_id, staging):
        cleanup_calls.append((symbol_id, staging))
        return 0

    monkeypatch.setattr(helpers, "run_etl_direct", _fail_etl)
    monkeypatch.setattr(helpers, "delete_staging_bars", _cleanup)

    sym = {
        "symbol_id": 10,
        "tv_symbol": "US30",
        "tv_exchange": "CAPITALCOM",
        "asset_type": "Indice",
    }

    result = helpers.pull_and_store(
        FakeTV(),
        sym,
        "M5",
        5,
        object(),
        logging.getLogger("test.helpers"),
    )

    assert result == helpers.RESULT_ERROR
    assert cleanup_calls == [(10, helpers.TF_STAGING["M5"])]


def test_repull_full_symbol_keeps_fact_when_staging_replacement_fails(monkeypatch):
    calls: list[tuple[str, object]] = []

    def _delete_staging(symbol_id, staging):
        calls.append(("delete_staging", symbol_id, staging))
        return 0

    def _pull_and_fail(*args, **kwargs):
        calls.append(("pull_and_store", args[1]["symbol_id"], args[2]))
        return helpers.RESULT_ERROR

    def _unexpected(*args, **kwargs):
        pytest.fail("Fact data must stay untouched when staging replacement fails")

    monkeypatch.setattr(helpers, "delete_staging_bars", _delete_staging)
    monkeypatch.setattr(helpers, "pull_and_store", _pull_and_fail)
    monkeypatch.setattr(helpers, "delete_fact_bars", _unexpected)
    monkeypatch.setattr(helpers, "run_etl_direct", _unexpected)

    sym = {"symbol_id": 81, "tv_symbol": "BTCUSD"}
    deleted, inserted = helpers.repull_full_symbol(
        object(),
        sym,
        "M5",
        object(),
        logging.getLogger("test.helpers"),
    )

    assert deleted == 0
    assert inserted == helpers.RESULT_ERROR
    assert calls == [
        ("delete_staging", 81, helpers.TF_STAGING["M5"]),
        ("pull_and_store", 81, "M5"),
    ]
