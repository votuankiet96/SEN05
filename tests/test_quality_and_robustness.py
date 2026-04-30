from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core_python.shared.data import validate_backtest_data
from core_python.shared.analytics import _bars_per_year, calc_metrics, calc_trade_level_return_stats
from core_python.shared.sessions import detect_time_gaps, normalize_datetime_index_utc
from core_python.shared.timeframes import expected_timedelta_for_tf
from core_python.shared.monte_carlo import run_monte_carlo
from core_python.strategies.combo.execution import backtest_fast
from core_python.strategies.combo.research_utils import _count_signal_window_rows, symbol_result_row
from core_python.strategies.combo.signals import (
    build_fast_backtest_signal_masks,
    build_raw_signal_masks,
    scan_signals_reversal,
)
from core_python.strategies.combo.symbol.selection import (
    filter_optimizer_candidates,
    rank_optimizer_candidates,
    summarize_optimizer_plateau,
)
from core_python.strategies.ma_cross.params import get_symbol_params


def _make_clean_ohlcv(n: int = 200) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=n, freq="4h", name="BarTime")
    close = np.linspace(100.0, 200.0, n)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1000.0,
        },
        index=dates,
    )


def test_close_outside_high_low_is_dropped():
    """Close outside the high-low range must be invalid OHLC."""
    df = _make_clean_ohlcv()
    df.iloc[10, df.columns.get_loc("close")] = df.iloc[10]["high"] + 5

    df_out, report = validate_backtest_data(df, min_bars=10)

    assert report["n_invalid_bars"] == 1
    assert len(df_out) == 199
    assert (df_out["high"] >= df_out["close"]).all()
    assert (df_out["low"] <= df_out["close"]).all()


def test_open_outside_high_low_is_dropped():
    df = _make_clean_ohlcv()
    df.iloc[10, df.columns.get_loc("open")] = df.iloc[10]["high"] + 5

    df_out, report = validate_backtest_data(df, min_bars=10)

    assert report["n_invalid_bars"] == 1
    assert len(df_out) == 199
    assert (df_out["high"] >= df_out["open"]).all()
    assert (df_out["low"] <= df_out["open"]).all()


def test_backtest_data_index_is_explicit_utc_for_session_filters():
    df = _make_clean_ohlcv(20)

    out_index = normalize_datetime_index_utc(df.index, source_timezone="UTC")

    assert out_index.tz is not None
    assert str(out_index.tz) == "UTC"


def test_timeframe_metadata_covers_combo_intraday_timeframes():
    assert _bars_per_year("H2") == 252 * 12
    assert _bars_per_year("H3") == 252 * 8
    assert expected_timedelta_for_tf("H2") == pd.Timedelta(hours=2)
    assert expected_timedelta_for_tf("H3") == pd.Timedelta(hours=3)


def test_time_gap_detection_reports_intraweek_gaps():
    idx = pd.DatetimeIndex(
        [
            "2023-01-02 00:00",
            "2023-01-02 04:00",
            "2023-01-02 16:00",
        ],
        name="BarTime",
    )

    report = detect_time_gaps(idx, tf="H4")

    assert report["n_time_gaps"] == 1
    assert report["n_unexpected_time_gaps"] == 1


def test_time_gap_detection_classifies_weekend_only_when_data_is_not_24x7():
    fx_like = pd.DatetimeIndex(
        ["2023-01-06 20:00", "2023-01-09 00:00"],
        name="BarTime",
    )
    crypto_like = pd.DatetimeIndex(
        ["2023-01-06 20:00", "2023-01-07 00:00", "2023-01-09 00:00"],
        name="BarTime",
    )

    fx_report = detect_time_gaps(fx_like, tf="H4")
    crypto_report = detect_time_gaps(crypto_like, tf="H4")

    assert fx_report["n_expected_weekend_gaps"] == 1
    assert fx_report["n_unexpected_time_gaps"] == 0
    assert crypto_report["n_expected_weekend_gaps"] == 0
    assert crypto_report["n_unexpected_time_gaps"] == 1


def test_scan_signals_reversal_accepts_datetime_index():
    idx = pd.date_range("2023-01-01", periods=3, freq="4h", name="BarTime")
    df = pd.DataFrame(
        {
            "BarTime": idx,
            "open": [100.0, 100.0, 103.0],
            "high": [102.0, 105.0, 106.0],
            "low": [98.0, 99.0, 102.0],
            "close": [99.0, 103.0, 104.0],
            "ma": [100.0, 100.0, 100.0],
            "macd_h": [0.0, 1.0, 1.0],
            "atr": [10.0, 10.0, 10.0],
        },
        index=idx,
    )

    signals = scan_signals_reversal(
        df,
        {"x": 0.0, "session_hours_utc": []},
        {"KTP": 1.0, "MIN_RR": 0.1},
    )

    assert len(signals) == 1
    assert signals.iloc[0]["direction"] == "BUY"


def test_trade_level_stats_use_fixed_initial_equity_denominator():
    pnls = [100.0, -50.0, 120.0, -40.0, 90.0, -30.0, 80.0, -20.0, 70.0, -10.0]

    stats = calc_trade_level_return_stats(
        pnls,
        initial_equity=100_000.0,
        window_start=pd.Timestamp("2023-01-01"),
        window_end=pd.Timestamp("2024-01-01"),
    )
    expected_rets = pd.Series(pnls, dtype=float) / 100_000.0
    expected_sharpe = expected_rets.mean() / expected_rets.std() * np.sqrt(10 / (365 / 365.25))

    assert stats["sharpe_method"] == "trade_level_fixed_initial_equity"
    assert stats["sharpe"] == pytest.approx(expected_sharpe)


def test_calc_metrics_explicit_window_rejects_non_overlapping_equity():
    idx = pd.date_range("2023-01-01", periods=2, freq="4h", name="BarTime")
    eq = pd.Series([100_000.0, 100_100.0], index=idx)
    trades = [
        {
            "pnl_usd": 100.0,
            "r_multiple": 1.0,
            "exit_time": idx[-1],
            "partial_tp_hit": False,
        }
    ] * 10

    with pytest.raises(ValueError, match="does not overlap"):
        calc_metrics(
            trades,
            eq,
            window_start=pd.Timestamp("2024-01-01"),
            window_end=pd.Timestamp("2024-02-01"),
        )


def test_combo_signal_mask_builders_reject_wrong_input_format():
    flat = pd.DataFrame(
        {
            "BarTime": ["2023-01-01 00:00"],
            "open": [1.0],
            "high": [2.0],
            "low": [0.5],
            "close": [1.5],
            "ma": [1.0],
            "macd_h": [1.0],
            "atr": [1.0],
        }
    )
    with pytest.raises(TypeError, match="datetime-like"):
        build_raw_signal_masks(flat, [])

    indexed = flat.drop(columns=["BarTime"]).copy()
    with pytest.raises(TypeError, match="DatetimeIndex"):
        build_fast_backtest_signal_masks(
            indexed,
            hours_utc=[],
            start_ts=pd.Timestamp("2023-01-01"),
            end_ts=pd.Timestamp("2023-01-02"),
            min_rr=1.0,
            x_actual=0.0,
            ktp=2.0,
        )


def test_monte_carlo_accepts_trade_frequency_for_sharpe_scaling():
    pnls = [100.0, -50.0, 120.0, -40.0, 90.0, -30.0] * 5
    mc_slow = run_monte_carlo(pnls, n_iter=50, trades_per_year=30, random_seed=7)
    mc_fast = run_monte_carlo(pnls, n_iter=50, trades_per_year=120, random_seed=7)

    assert mc_slow["sharpe_annualization"] == 30
    assert mc_fast["sharpe_annualization"] == 120
    assert mc_fast["sharpe_ci_high"] > mc_slow["sharpe_ci_high"]


def test_ma_cross_unconfigured_symbol_fails_with_clear_error():
    with pytest.raises(KeyError, match="not found"):
        get_symbol_params("__UNKNOWN_SYMBOL__")


def test_backtest_fast_date_to_none_uses_data_end():
    """date_to=None must not become NaT and suppress every optimizer signal."""
    idx = pd.date_range("2023-01-01", periods=5, freq="4h", name="BarTime")
    df = pd.DataFrame(
        {
            "open": [90.0, 95.0, 101.0, 120.0, 120.0],
            "high": [95.0, 100.0, 210.0, 150.0, 130.0],
            "low": [85.0, 90.0, 101.0, 100.0, 110.0],
            "close": [90.0, 99.0, 150.0, 120.0, 125.0],
            "ma": [95.0, 98.0, 100.0, 100.0, 100.0],
            "prev_ma": [np.nan, 95.0, 98.0, 100.0, 100.0],
            "prev_close": [np.nan, 90.0, 99.0, 150.0, 120.0],
            "macd_h": [0.0, 1.0, 1.0, 1.0, 1.0],
            "atr": [10.0, 100.0, 100.0, 100.0, 100.0],
        },
        index=idx,
    )
    cfg = {
        "x": 0.0,
        "ktp": 1.0,
        "contract_value": 1.0,
        "point_size": 1.0,
        "spread_pts": 0.0,
        "slippage_pts": 0.0,
        "commission_per_lot": 0.0,
        "min_lot_size": 0.01,
        "max_lot_size": 100.0,
        "lot_step": 0.01,
        "swap_long_per_lot_per_day": 0.0,
        "swap_short_per_lot_per_day": 0.0,
        "session_hours_utc": [],
    }

    metrics = backtest_fast(
        "TEST",
        df,
        cfg,
        ktp=1.0,
        x_actual=0.0,
        trailing_act=1.0,
        date_from="2023-01-01",
        date_to=None,
        init_eq=100_000.0,
        strategy={
            "risk_per_trade": 0.005,
            "daily_loss_limit": 1.0,
            "max_drawdown_limit": 1.0,
            "pending_ttl_bars": 3,
            "partial_tp_fraction": 0.5,
            "trailing_activation": 1.0,
        },
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert metrics["trades"] >= 1


def test_optimizer_candidate_filter_and_robust_ranking():
    grid = pd.DataFrame(
        [
            {"ktp": 2.3, "x": 10, "trailing_activation": 1.0, "ma_period": 20, "trades": 8, "pf": 5.0, "ret": 10.0, "maxdd": 2.0, "sharpe": 3.0, "score": 1.0},
            {"ktp": 2.3, "x": 12, "trailing_activation": 1.0, "ma_period": 20, "trades": 60, "pf": 1.4, "ret": 12.0, "maxdd": 8.0, "sharpe": 1.2, "score": 0.8},
            {"ktp": 2.3, "x": 14, "trailing_activation": 1.0, "ma_period": 20, "trades": 70, "pf": 1.1, "ret": 14.0, "maxdd": 6.0, "sharpe": 1.4, "score": 0.9},
        ]
    )

    filtered = filter_optimizer_candidates(
        grid,
        {"min_trades": 40, "min_profit_factor": 1.15, "max_drawdown_pct": 20, "min_sharpe": 0.5},
    )
    ranked = rank_optimizer_candidates(filtered)

    assert len(filtered) == 1
    assert ranked.iloc[0]["x"] == 12
    assert "robust_score" in ranked.columns


def test_optimizer_plateau_adapter_reads_grid_dataframe():
    rows = []
    for x in [8.0, 10.0, 12.0]:
        rows.append({
            "ktp": 2.3,
            "x": x,
            "trailing_activation": 1.0,
            "ma_period": 20,
            "sharpe": 1.0 if x != 10.0 else 1.5,
        })
    grid = pd.DataFrame(rows)

    plateau = summarize_optimizer_plateau(grid, grid.iloc[1], score_col="sharpe")

    assert plateau["neighbors_checked"] >= 2
    assert plateau["stable_ratio"] > 0
    assert "is_plateau" in plateau


def test_symbol_backtest_report_counts_window_rows_separately_from_loaded_rows():
    idx = pd.date_range("2023-01-01", periods=5, freq="4h", name="BarTime")
    signal_data = pd.DataFrame(
        {
            "in_window": [False, True, True, False, False],
            "signal": [0, 1, 0, 0, -1],
        },
        index=idx,
    )
    result = SimpleNamespace(
        account_mode="standard",
        raw_data=pd.DataFrame(index=idx),
        signal_data=signal_data,
        trades=[],
        equity=pd.Series([100_000.0], index=[idx[0]]),
        metrics={},
    )

    assert _count_signal_window_rows(signal_data) == 2

    row = symbol_result_row("TEST", result)
    assert row["window_rows"] == 2
    assert row["raw_rows"] == 5
