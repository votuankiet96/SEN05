from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core_python.strategies.combo.execution import backtest_symbol


def _frame(rows: list[dict]) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=len(rows), freq="4h", name="BarTime")
    return pd.DataFrame(rows, index=dates)


def _cfg() -> dict:
    return {
        "x": 0.0,
        "ktp": 10.0,
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


def _strategy() -> dict:
    return {
        "risk_per_trade": 0.005,
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "trailing_activation": 1.0,
        "pending_ttl_bars": 3,
        "partial_tp_fraction": 0.0,
        "min_rr": 1.25,
    }


def test_signal_bar_does_not_fill_on_same_bar():
    """Signal at bar T must only create a pending order for later bars."""
    df = _frame([
        {"open": 95, "high": 100, "low": 90, "close": 99, "atr": 100, "ma": 95, "signal": 1},
        {"open": 96, "high": 99, "low": 94, "close": 97, "atr": 100, "ma": 95, "signal": 0},
        {"open": 97, "high": 98, "low": 93, "close": 96, "atr": 100, "ma": 95, "signal": 0},
    ])

    trades, _ = backtest_symbol("TEST", df, _cfg(), 100_000.0, strategy=_strategy())
    assert trades == []


def test_sl_wins_when_sl_and_tp_hit_on_fill_bar():
    """If SL and TP are both inside one OHLC bar, the engine must choose SL."""
    df = _frame([
        {"open": 95, "high": 100, "low": 90, "close": 99, "atr": 1, "ma": 95, "signal": 1},
        {"open": 99, "high": 120, "low": 80, "close": 100, "atr": 1, "ma": 95, "signal": 0},
    ])

    trades, _ = backtest_symbol("TEST", df, _cfg(), 100_000.0, strategy=_strategy())
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "SL"


def test_reversal_exits_at_next_bar_open_not_signal_close():
    """A reversal signal known at bar close must exit on the next bar open."""
    df = _frame([
        {"open": 95, "high": 100, "low": 90, "close": 99, "atr": 100, "ma": 95, "signal": 1},
        {"open": 99, "high": 101, "low": 95, "close": 100, "atr": 100, "ma": 95, "signal": 0},
        {"open": 100, "high": 121, "low": 99, "close": 120, "atr": 100, "ma": 95, "signal": -1},
        {"open": 105, "high": 110, "low": 104, "close": 106, "atr": 100, "ma": 95, "signal": 0},
    ])

    trades, _ = backtest_symbol("TEST", df, _cfg(), 100_000.0, strategy=_strategy())
    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "REVERSED"
    assert trades[0]["exit_time"] == df.index[3]
    assert trades[0]["exit"] == 105
