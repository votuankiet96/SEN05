from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core_python.strategies.ma_cross.basket import (
    BasketOrder,
    basket_snapshot,
    next_linear_lot,
)
from core_python.strategies.ma_cross.execution_basket import backtest_basket_reversal_symbol
from core_python.strategies.ma_cross.filters import add_entry_filter_columns
from core_python.strategies.ma_cross.symbol.backtest import run_symbol_backtest_on_frame


def _cfg() -> dict:
    return {
        "contract_value": 1000.0,
        "point_size": 1.0,
        "spread_pts": 0.0,
        "commission_per_lot": 0.0,
        "slippage_pts": 0.0,
        "min_lot_size": 0.01,
        "max_lot_size": 100.0,
        "lot_step": 0.01,
        "swap_long_per_lot_per_day": 0.0,
        "swap_short_per_lot_per_day": 0.0,
        "session_hours_utc": [],
    }


def _strategy(**basket_overrides) -> dict:
    basket = {
        "lot_sizing_mode": "fixed_linear",
        "initial_lot": 0.01,
        "lot_step": 0.01,
        "max_single_lot": 0.03,
        "max_orders": 10,
        "max_total_lot": 1.0,
        "max_net_lot": 1.0,
        "basket_take_profit": 10_000.0,
        "basket_stop_loss": 10_000.0,
        "allow_same_bar_basket_close": False,
        "reversal_only": True,
    }
    basket.update(basket_overrides)
    return {
        "execution_model": "basket_reversal",
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "max_drawdown_mode": "fixed_initial",
        "risk_per_trade": 0.005,
        "atr_stop_mult": 2.0,
        "hedge_allowed": True,
        "basket": basket,
    }


def _frame(signals: list[int], opens: list[float] | None = None, closes: list[float] | None = None) -> pd.DataFrame:
    n = len(signals)
    idx = pd.date_range("2024-01-01", periods=n, freq="30min", name="BarTime")
    opens = opens or [100.0] * n
    closes = closes or opens
    highs = [max(o, c) + 1 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 1 for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "atr": [5.0] * n,
            "signal": signals,
        },
        index=idx,
    )


def test_next_linear_lot_is_capped_not_martingale():
    lots = [next_linear_lot(i, _strategy(), _cfg()) for i in range(5)]
    assert lots == [0.01, 0.02, 0.03, 0.03, 0.03]


def test_basket_snapshot_tracks_buy_sell_gross_and_net_exposure():
    idx = pd.date_range("2024-01-01", periods=1, freq="30min", name="BarTime")
    orders = [
        BasketOrder(1, "TEST", 1, idx[0], 100.0, 0.01, 0),
        BasketOrder(2, "TEST", 1, idx[0], 101.0, 0.02, 1),
        BasketOrder(3, "TEST", -1, idx[0], 102.0, 0.03, 2),
    ]
    snap = basket_snapshot(
        orders,
        timestamp=idx[0],
        mark_price=100.0,
        cfg=_cfg(),
        spread_pts=0.0,
        slippage_pts=0.0,
        commission_per_lot=0.0,
    )

    assert snap.total_buy_lot == 0.03
    assert snap.total_sell_lot == 0.03
    assert snap.gross_exposure == 0.06
    assert snap.net_exposure == 0.0
    assert snap.order_count == 3


def test_opposite_signal_adds_new_leg_without_reversed_exit():
    df = _frame([1, -1, 0], opens=[100, 101, 102], closes=[100, 101, 102])

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "END_OF_DATA"
    assert trades[0]["orders_closed"] == 2
    assert [row["direction"] for row in trades[0]["child_orders"]] == [1, -1]
    assert all(row["exit_time"] == df.index[-1] for row in trades[0]["child_orders"])


def test_basket_take_profit_closes_all_orders():
    df = _frame([1, 0], opens=[100, 100], closes=[100, 110])

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(basket_take_profit=50.0, allow_same_bar_basket_close=True),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "BASKET_TP"
    assert trades[0]["orders_closed"] == 1
    assert trades[0]["pnl_usd"] == 100.0


def test_basket_stop_loss_closes_all_orders():
    df = _frame([1, 0], opens=[100, 100], closes=[100, 90])

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(basket_stop_loss=50.0, allow_same_bar_basket_close=True),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "BASKET_SL"
    assert trades[0]["orders_closed"] == 1
    assert trades[0]["pnl_usd"] == -100.0


def test_basket_blocks_new_orders_at_max_orders():
    df = _frame(
        [1, -1, 1, -1],
        opens=[100, 100, 100, 100],
        closes=[100, 100, 100, 100],
    )

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(max_orders=2),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["orders_closed"] == 2


def test_basket_blocks_new_orders_at_total_and_net_caps():
    df = _frame([1, -1, 1], opens=[100, 100, 100], closes=[100, 100, 100])

    total_cap_trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(max_total_lot=0.02),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )
    net_cap_trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(max_net_lot=0.015),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert total_cap_trades[0]["orders_closed"] == 1
    assert net_cap_trades[0]["orders_closed"] == 2


def test_daily_loss_mtm_blocks_on_floating_loss():
    df = _frame([1, -1, 0], opens=[100, 100, 90], closes=[100, 90, 90])
    strategy = _strategy()
    strategy["daily_loss_limit"] = 0.0005

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=strategy,
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "END_OF_DATA"
    assert trades[0]["orders_closed"] == 1


def test_daily_loss_resets_from_day_start_mtm_not_initial_equity():
    idx = pd.DatetimeIndex(
        [
            pd.Timestamp("2024-01-01 23:00"),
            pd.Timestamp("2024-01-01 23:30"),
            pd.Timestamp("2024-01-02 00:00"),
        ],
        name="BarTime",
    )
    df = pd.DataFrame(
        {
            "open": [100.0, 100.0, 86.0],
            "high": [101.0, 101.0, 87.0],
            "low": [99.0, 89.0, 85.0],
            "close": [100.0, 90.0, 86.0],
            "atr": [5.0, 5.0, 5.0],
            "signal": [1, -1, 0],
        },
        index=idx,
    )
    strategy = _strategy()
    strategy["daily_loss_limit"] = 0.0005

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=strategy,
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["orders_closed"] == 2


def test_same_direction_signal_ignored_by_default():
    df = _frame([1, 1, 0], opens=[100, 100, 100], closes=[100, 100, 100])

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["orders_closed"] == 1


def test_net_zero_basket_allows_new_direction():
    df = _frame([1, -1, 1, 0], opens=[100, 100, 100, 100], closes=[100, 100, 100, 100])

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(lot_step=0.0, max_single_lot=0.01),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["orders_closed"] == 3


def test_allow_same_bar_close_false_waits_for_next_bar():
    df = _frame([1, 0, 0], opens=[100, 100, 100], closes=[100, 110, 110])

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=_strategy(basket_take_profit=50.0),
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "BASKET_TP"
    assert trades[0]["exit_time"] == df.index[2]


def test_commission_and_spread_reduce_basket_pnl():
    cfg = {**_cfg(), "spread_pts": 2.0, "commission_per_lot": 5.0}
    df = _frame([1, 0], opens=[100, 100], closes=[100, 110])

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        cfg,
        100_000.0,
        strategy=_strategy(basket_take_profit=1.0, allow_same_bar_basket_close=True),
        costs={"slippage_pts": 0.0, "commission_per_lot": 5.0},
    )

    assert len(trades) == 1
    assert trades[0]["gross_pnl"] == 60.0
    assert trades[0]["commission"] == 0.1
    assert trades[0]["pnl_usd"] == 59.9


def test_max_drawdown_mtm_blocks_new_orders():
    df = _frame([1, -1, 0], opens=[100, 100, 90], closes=[100, 90, 90])
    strategy = _strategy()
    strategy["max_drawdown_limit"] = 0.0005

    trades, _ = backtest_basket_reversal_symbol(
        "TEST",
        df,
        _cfg(),
        100_000.0,
        strategy=strategy,
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert len(trades) == 1
    assert trades[0]["orders_closed"] == 1


def test_hedge_disallowed_rejects_basket_engine():
    strategy = _strategy()
    strategy["hedge_allowed"] = False

    with pytest.raises(ValueError, match="hedge_allowed=True"):
        backtest_basket_reversal_symbol(
            "TEST",
            _frame([1, 0]),
            _cfg(),
            100_000.0,
            strategy=strategy,
            costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
        )


def test_risk_based_lot_scales_with_equity():
    cfg = {**_cfg(), "contract_value": 1.0}
    strategy = _strategy(
        lot_sizing_mode="risk_based",
        max_single_lot_mult=3.0,
        max_total_lot_mult=10.0,
        max_net_lot_mult=6.0,
    )

    low_equity_lot = next_linear_lot(
        0,
        strategy,
        cfg,
        current_equity=10_000.0,
        atr_stop_pts=50.0,
    )
    high_equity_lot = next_linear_lot(
        0,
        strategy,
        cfg,
        current_equity=100_000.0,
        atr_stop_pts=50.0,
    )

    assert high_equity_lot > low_equity_lot


def test_risk_based_invalid_atr_returns_zero_lot():
    lot = next_linear_lot(
        0,
        _strategy(lot_sizing_mode="risk_based"),
        _cfg(),
        current_equity=100_000.0,
        atr_stop_pts=0.0,
    )

    assert lot == 0.0


def test_risk_based_lot_respects_broker_max_lot():
    cfg = {**_cfg(), "contract_value": 1.0, "max_lot_size": 0.05}
    lot = next_linear_lot(
        0,
        _strategy(lot_sizing_mode="risk_based"),
        cfg,
        current_equity=100_000.0,
        atr_stop_pts=1.0,
    )

    assert lot == 0.05


def test_entry_filters_block_unaligned_ma20_slope():
    idx = pd.date_range("2024-01-01", periods=4, freq="30min", name="BarTime")
    df = pd.DataFrame(
        {
            "fast_ma": [100.0, 100.0, 100.0, 105.0],
            "slow_ma": [104.0, 103.0, 102.0, 101.0],
            "atr": [2.0, 2.0, 2.0, 2.0],
            "signal": [0, 0, 0, 1],
        },
        index=idx,
    )

    filtered = add_entry_filter_columns(
        df,
        symbol_config=_cfg(),
        filter_overrides={"filters": {"ma20_slope_lookback": 1, "min_ma20_slope": 0.0}},
    )

    assert filtered["signal"].iloc[-1] == 1
    assert filtered["entry_signal"].iloc[-1] == 0
    assert bool(filtered["ma20_slope_ok"].iloc[-1]) is False


def test_symbol_runner_can_select_basket_reversal_engine():
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

    result = run_symbol_backtest_on_frame(
        "US30",
        df,
        init_eq=100_000.0,
        tf="M30",
        indicator_overrides={"FAST_MA": 2, "SLOW_MA": 4, "ATR_PERIOD": 2, "MA_TYPE": "sma"},
        strategy_overrides={
            "execution_model": "basket_reversal",
            "basket": {"basket_take_profit": 10_000.0, "basket_stop_loss": 10_000.0},
            "filters": {"min_ma20_slope": None},
        },
        costs={"slippage_pts": 0.0, "commission_per_lot": 0.0},
    )

    assert result.strategy_settings["execution_model"] == "basket_reversal"
    assert "entry_signal" in result.signal_data.columns
    assert all(trade["exit_reason"] != "REVERSED" for trade in result.trades)
