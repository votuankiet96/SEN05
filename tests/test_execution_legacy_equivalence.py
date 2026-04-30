from __future__ import annotations

import pandas as pd

from core_python.shared.execution import (
    backtest_market_symbol,
    calc_dynamic_slippage,
)
from core_python.shared.execution.primitives import (
    adverse_entry_price,
    adverse_exit_price,
    max_drawdown_breached,
)
from core_python.strategies.combo.execution import (
    backtest_portfolio,
    backtest_symbol,
    build_pending_order,
)
from core_python.strategies.combo.orders import create_pending_breakout_intent


def _combo_cfg(**overrides: float) -> dict:
    cfg = {
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
    cfg.update(overrides)
    return cfg


def _combo_strategy(**overrides: float | bool) -> dict:
    strategy = {
        "risk_per_trade": 0.005,
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "trailing_activation": 1.0,
        "pending_ttl_bars": 3,
        "partial_tp_fraction": 0.0,
        "min_rr": 1.25,
    }
    strategy.update(overrides)
    return strategy


def _frame(rows: list[dict]) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=len(rows), freq="4h", name="BarTime")
    return pd.DataFrame(rows, index=index)


def test_cost_and_risk_legacy_golden_values() -> None:
    assert calc_dynamic_slippage(2.0, atr=100.0, close=10_000.0, k=50.0) == 3.0
    assert calc_dynamic_slippage(2.0, atr=0.0, close=10_000.0, k=50.0) == 2.05
    assert adverse_entry_price(100.0, 1, spread=1.5, slippage=0.5) == 102.0
    assert adverse_entry_price(100.0, -1, spread=1.5, slippage=0.5) == 98.0
    assert adverse_exit_price(100.0, 1, spread=1.5, slippage=0.5) == 98.0
    assert adverse_exit_price(100.0, -1, spread=1.5, slippage=0.5) == 102.0
    assert max_drawdown_breached(89_999.0, 100_000.0, 120_000.0, 0.10, "fixed_initial")
    assert max_drawdown_breached(107_999.0, 100_000.0, 120_000.0, 0.10, "trailing_peak")


def test_combo_pending_order_wrapper_matches_strategy_intent() -> None:
    bar = pd.Series(
        {"high": 106.0, "low": 95.0, "atr": 4.0, "signal": -1},
        name=pd.Timestamp("2024-01-01 04:00"),
    )

    legacy_order = build_pending_order(bar, -1, x=10.0, ktp=2.3, atr=4.0, ttl=3)
    intent = create_pending_breakout_intent(
        "TEST",
        bar,
        direction=-1,
        x=10.0,
        ktp=2.3,
        atr=4.0,
        ttl_bars=3,
    )

    assert intent is not None
    assert legacy_order == {
        "direction": intent.direction,
        "entry": intent.entry,
        "sl": intent.sl,
        "tp": intent.tp,
        "atr": intent.metadata["atr"],
        "ttl": intent.ttl_bars,
    }


def test_combo_partial_tp_then_force_close_golden_trade() -> None:
    df = _frame(
        [
            {"open": 95, "high": 100, "low": 90, "close": 99, "atr": 1, "ma": 90, "signal": 1},
            {"open": 100, "high": 111, "low": 100, "close": 110, "atr": 1, "ma": 90, "signal": 0},
            {"open": 110, "high": 112, "low": 108, "close": 108, "atr": 1, "ma": 90, "signal": 0},
        ]
    )

    trades, equity = backtest_symbol(
        "TEST",
        df,
        _combo_cfg(),
        100_000.0,
        strategy=_combo_strategy(partial_tp_fraction=0.5),
    )

    assert len(trades) == 1
    trade = trades[0]
    assert trade["entry"] == 100
    assert trade["exit"] == 108
    assert trade["exit_reason"] == "FORCE_CLOSE"
    assert trade["partial_tp_hit"] is True
    assert trade["half1_exit"] == 110
    assert trade["pnl_gross"] == 450.0
    assert trade["commission"] == 205.87
    assert trade["pnl_usd"] == 244.13
    assert equity.iloc[-1] == 100_244.13


def test_portfolio_shared_account_golden_trade_and_equity() -> None:
    frames = {
        "A": _frame(
            [
                {"open": 95, "high": 100, "low": 90, "close": 99, "atr": 1, "ma": 90, "signal": 1},
                {"open": 100, "high": 101, "low": 100, "close": 100, "atr": 1, "ma": 90, "signal": 0},
                {"open": 100, "high": 100, "low": 99, "close": 99, "atr": 1, "ma": 90, "signal": 0},
            ]
        ),
        "B": _frame(
            [
                {"open": 95, "high": 100, "low": 90, "close": 99, "atr": 1, "ma": 90, "signal": 0},
                {"open": 100, "high": 101, "low": 100, "close": 100, "atr": 1, "ma": 90, "signal": 0},
                {"open": 100, "high": 100, "low": 99, "close": 99, "atr": 1, "ma": 90, "signal": 0},
            ]
        ),
    }

    trades, account_eq, symbol_eq, trades_by_symbol = backtest_portfolio(
        frames,
        {"A": _combo_cfg(), "B": _combo_cfg()},
        100_000.0,
        allocations={"A": 0.5, "B": 0.5},
        strategy=_combo_strategy(),
    )

    assert len(trades) == 1
    assert trades[0]["symbol"] == "A"
    assert trades[0]["exit_reason"] == "FORCE_CLOSE"
    assert trades[0]["pnl_usd"] == -25.0
    assert account_eq.iloc[-1] == 99_975.0
    assert symbol_eq["A"].iloc[-1] == 49_975.0
    assert len(trades_by_symbol["B"]) == 0


def test_ma_cross_next_open_and_end_of_data_golden_trade() -> None:
    df = _frame(
        [
            {"open": 100, "high": 100, "low": 100, "close": 100, "atr": 2, "slow_ma": 90, "signal": 1},
            {"open": 101, "high": 103, "low": 100, "close": 102, "atr": 2, "slow_ma": 90, "signal": 0},
            {"open": 102, "high": 103, "low": 101, "close": 102, "atr": 2, "slow_ma": 90, "signal": 0},
        ]
    )

    trades, equity = backtest_market_symbol(
        "TEST",
        df,
        _combo_cfg(spread_pts=1.0),
        100_000.0,
        strategy={
            "risk_per_trade": 0.005,
            "daily_loss_limit": 1.0,
            "max_drawdown_limit": 1.0,
            "atr_stop_mult": 2.0,
            "atr_tp_mult": 0.0,
            "partial_tp_fraction": 0.0,
            "trailing_activation": 100.0,
            "trailing_column": "slow_ma",
            "reverse_on_opposite": True,
            "allow_same_bar_exit": False,
        },
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0, "slippage_k": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["entry_time"] == df.index[1]
    assert trades[0]["entry"] == 102.0
    assert trades[0]["exit_reason"] == "END_OF_DATA"
    assert trades[0]["exit"] == 101.0
    assert trades[0]["pnl_usd"] == -100.0
    assert equity.iloc[-1] == 99_900.0
