from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.data import validate_backtest_data
from shared.execution import backtest_fast
from shared.monte_carlo import run_monte_carlo
from strategies.combo.notebook_utils import _count_signal_window_rows, symbol_result_row
from strategies.combo.symbol.selection import (
    filter_optimizer_candidates,
    rank_optimizer_candidates,
    summarize_optimizer_plateau,
)
from strategies.ma_cross.strategy_config import get_symbol_params


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


def test_monte_carlo_accepts_trade_frequency_for_sharpe_scaling():
    pnls = [100.0, -50.0, 120.0, -40.0, 90.0, -30.0] * 5
    mc_slow = run_monte_carlo(pnls, n_iter=50, trades_per_year=30, random_seed=7)
    mc_fast = run_monte_carlo(pnls, n_iter=50, trades_per_year=120, random_seed=7)

    assert mc_slow["sharpe_annualization"] == 30
    assert mc_fast["sharpe_annualization"] == 120
    assert mc_fast["sharpe_ci_high"] > mc_slow["sharpe_ci_high"]


def test_ma_cross_unconfigured_symbol_fails_with_clear_error():
    with pytest.raises(KeyError, match="not configured"):
        get_symbol_params("US30")


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
