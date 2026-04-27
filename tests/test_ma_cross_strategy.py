from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.execution_market import backtest_market_symbol
from strategies.ma_cross.logic import add_ma_cross_indicators, detect_ma_cross_signals, session_mask


def _market_cfg() -> dict:
    return {
        "contract_value": 1.0,
        "point_size": 1.0,
        "spread_pts": 0.0,
        "commission_per_lot": 0.0,
        "slippage_pts": 0.0,
        "min_lot_size": 0.01,
        "max_lot_size": 100.0,
        "lot_step": 0.01,
        "swap_long_per_lot_per_day": 0.0,
        "swap_short_per_lot_per_day": 0.0,
    }


def _market_strategy() -> dict:
    return {
        "risk_per_trade": 0.005,
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "atr_stop_mult": 2.0,
        "atr_tp_mult": 0.0,
        "partial_tp_fraction": 0.0,
        "trailing_activation": 1.0,
        "trailing_column": "slow_ma",
        "reverse_on_opposite": True,
    }


def test_ma_cross_signal_is_close_confirmed():
    idx = pd.date_range("2024-01-01", periods=8, freq="30min", name="BarTime")
    df = pd.DataFrame(
        {
            "open": [10, 10, 10, 10, 10, 11, 12, 13],
            "high": [11, 11, 11, 11, 11, 12, 13, 14],
            "low": [9, 9, 9, 9, 9, 10, 11, 12],
            "close": [10, 10, 10, 10, 10, 12, 14, 16],
            "volume": [1] * 8,
        },
        index=idx,
    )
    ind = add_ma_cross_indicators(
        df,
        {"FAST_MA": 2, "SLOW_MA": 4, "ATR_PERIOD": 2, "MA_TYPE": "sma"},
    )
    sig = detect_ma_cross_signals(ind, session_mask(ind, []))

    cross_rows = sig[sig["signal"] == 1]
    assert len(cross_rows) == 1
    cross_time = cross_rows.index[0]
    assert sig.loc[cross_time, "prev_fast_ma"] <= sig.loc[cross_time, "prev_slow_ma"]
    assert sig.loc[cross_time, "fast_ma"] > sig.loc[cross_time, "slow_ma"]


def test_market_entry_uses_next_bar_open_not_signal_close():
    idx = pd.date_range("2024-01-01", periods=3, freq="30min", name="BarTime")
    df = pd.DataFrame(
        {
            "open": [100, 110, 111],
            "high": [101, 112, 112],
            "low": [99, 109, 108],
            "close": [105, 111, 109],
            "atr": [5, 5, 5],
            "signal": [1, 0, -1],
        },
        index=idx,
    )

    trades, _ = backtest_market_symbol(
        "TEST",
        df,
        _market_cfg(),
        100_000.0,
        strategy=_market_strategy(),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert trades[0]["entry_time"] == idx[1]
    assert trades[0]["entry"] == 110


def test_market_reversal_exits_at_next_bar_open():
    idx = pd.date_range("2024-01-01", periods=4, freq="30min", name="BarTime")
    df = pd.DataFrame(
        {
            "open": [100, 110, 111, 106],
            "high": [101, 112, 112, 107],
            "low": [99, 109, 108, 105],
            "close": [105, 111, 109, 106],
            "atr": [20, 20, 20, 20],
            "signal": [1, 0, -1, 0],
        },
        index=idx,
    )

    trades, _ = backtest_market_symbol(
        "TEST",
        df,
        _market_cfg(),
        100_000.0,
        strategy=_market_strategy(),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert trades[0]["exit_reason"] == "REVERSED"
    assert trades[0]["exit_time"] == idx[3]
    assert trades[0]["exit"] == 106


def test_market_partial_tp_then_breakeven_sl():
    idx = pd.date_range("2024-01-01", periods=4, freq="30min", name="BarTime")
    df = pd.DataFrame(
        {
            "open": [100, 100, 104, 100],
            "high": [101, 104, 105, 101],
            "low": [99, 99, 100, 99],
            "close": [100, 103, 101, 100],
            "atr": [2, 2, 2, 2],
            "slow_ma": [98, 99, 100, 100],
            "signal": [1, 0, 0, 0],
        },
        index=idx,
    )
    strategy = {
        **_market_strategy(),
        "atr_tp_mult": 2.0,
        "partial_tp_fraction": 0.5,
        "trailing_column": "slow_ma",
    }

    trades, _ = backtest_market_symbol(
        "TEST",
        df,
        _market_cfg(),
        100_000.0,
        strategy=strategy,
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["partial_tp_hit"] is True
    assert trades[0]["half1_exit"] == 104
    assert trades[0]["exit_reason"] == "SL"
    assert trades[0]["exit"] == 100


def test_market_trailing_uses_configured_ma_column():
    idx = pd.date_range("2024-01-01", periods=4, freq="30min", name="BarTime")
    df = pd.DataFrame(
        {
            "open": [100, 100, 105, 103],
            "high": [101, 105, 106, 104],
            "low": [99, 100, 103, 100],
            "close": [100, 105, 103, 101],
            "atr": [2, 2, 2, 2],
            "slow_ma": [98, 99, 103, 103],
            "signal": [1, 0, 0, 0],
        },
        index=idx,
    )
    strategy = {
        **_market_strategy(),
        "atr_tp_mult": 0.0,
        "partial_tp_fraction": 0.0,
        "trailing_activation": 1.0,
        "trailing_column": "slow_ma",
    }

    trades, _ = backtest_market_symbol(
        "TEST",
        df,
        _market_cfg(),
        100_000.0,
        strategy=strategy,
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "SL"
    assert trades[0]["exit"] == 103
