from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from core_python.strategies.combo.execution import backtest_symbol
from core_python.strategies.combo.research_utils import (
    build_portfolio_replay_events,
    build_symbol_replay_events,
    replay_state_at,
)
from core_python.strategies.combo.research_utils.replay import _render_symbol_replay_png


def _symbol_result(symbol: str = "US30") -> SimpleNamespace:
    idx = pd.date_range("2023-01-01", periods=5, freq="4h", name="BarTime")
    signal_data = pd.DataFrame(
        {
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "ma": [99.0, 100.0, 101.0, 102.0, 103.0],
            "macd_h": [-0.5, 0.2, 0.4, -0.1, 0.3],
            "signal": [0, 1, 0, -1, 0],
        },
        index=idx,
    )
    trades = [
        {
            "symbol": symbol,
            "direction": "BUY",
            "lot_size": 0.5,
            "entry_time": idx[1],
            "exit_time": idx[4],
            "entry": 101.0,
            "exit": 104.0,
            "sl_initial": 99.0,
            "tp": 103.0,
            "pnl_usd": 150.0,
            "equity": 100_150.0,
            "exit_reason": "TP",
            "partial_tp_hit": True,
            "half1_exit": 103.0,
            "half1_exit_time": idx[3],
        }
    ]
    equity = pd.Series(
        [100_000.0, 100_000.0, 100_000.0, 100_050.0, 100_150.0],
        index=idx,
        name="equity",
    )
    return SimpleNamespace(
        symbol=symbol,
        signal_data=signal_data,
        trades=trades,
        equity=equity,
        metrics={},
    )


def test_symbol_replay_events_include_signals_trade_lifecycle_and_partial_tp():
    result = _symbol_result()

    events = build_symbol_replay_events(result)

    assert events["event_type"].tolist() == ["SIGNAL", "ENTRY", "SIGNAL", "PARTIAL_TP", "EXIT"]
    assert events.iloc[-1]["pnl_usd"] == 150.0
    assert events.iloc[-1]["equity"] == 100_150.0


def test_replay_state_counts_open_and_closed_trades_at_selected_time():
    result = _symbol_result()
    events = build_symbol_replay_events(result)

    mid_state = replay_state_at(events, pd.Timestamp("2023-01-01 08:00"), equity=result.equity)
    final_state = replay_state_at(events, pd.Timestamp("2023-01-01 16:00"), equity=result.equity)

    assert mid_state["open_trades"] == 1
    assert mid_state["closed_trades"] == 0
    assert final_state["open_trades"] == 0
    assert final_state["closed_trades"] == 1
    assert final_state["pnl_usd"] == 150.0


def test_portfolio_replay_events_merge_symbols_and_attach_portfolio_equity():
    us30 = _symbol_result("US30")
    gold = _symbol_result("GOLD")
    combined_equity = pd.Series(
        [200_000.0, 200_000.0, 200_000.0, 200_100.0, 200_300.0],
        index=us30.equity.index,
        name="combined_equity",
    )
    portfolio = SimpleNamespace(
        symbol_results={"US30": us30, "GOLD": gold},
        combined_equity=combined_equity,
        trades=[*us30.trades, *gold.trades],
        symbol_keys=["US30", "GOLD"],
    )

    events = build_portfolio_replay_events(portfolio)

    assert set(events["symbol"]) == {"US30", "GOLD"}
    assert int(events["event_type"].eq("EXIT").sum()) == 2
    assert events["portfolio_equity"].notna().any()


def test_symbol_engine_replay_events_do_not_change_trades_or_equity():
    idx = pd.date_range("2023-01-01", periods=5, freq="4h", name="BarTime")
    df = pd.DataFrame(
        {
            "open": [90.0, 95.0, 101.0, 210.0, 215.0],
            "high": [95.0, 100.0, 210.0, 220.0, 225.0],
            "low": [85.0, 90.0, 101.0, 205.0, 210.0],
            "close": [90.0, 99.0, 150.0, 215.0, 220.0],
            "ma": [95.0, 98.0, 100.0, 100.0, 100.0],
            "atr": [10.0, 100.0, 100.0, 100.0, 100.0],
            "signal": [0, 0, 1, 0, 0],
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
    }
    strategy = {
        "risk_per_trade": 0.005,
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "pending_ttl_bars": 3,
        "partial_tp_fraction": 0.5,
        "trailing_activation": 10.0,
    }
    costs = {"slippage_pts": 0.0, "commission_per_lot": 0.0}

    trades_plain, equity_plain = backtest_symbol(
        "TEST",
        df,
        cfg,
        100_000.0,
        strategy=strategy,
        costs=costs,
    )
    events = []
    trades_events, equity_events = backtest_symbol(
        "TEST",
        df,
        cfg,
        100_000.0,
        strategy=strategy,
        costs=costs,
        event_sink=events,
    )

    assert trades_events == trades_plain
    pd.testing.assert_series_equal(equity_events, equity_plain)
    event_types = {event.event_type for event in events}
    assert {"SIGNAL_DETECTED", "PENDING_CREATED", "POSITION_OPENED", "FORCE_CLOSE_END_OF_DATA"}.issubset(event_types)


def test_symbol_replay_png_renders_candlestick_frame():
    result = _symbol_result()
    events = build_symbol_replay_events(result)

    image_bytes = _render_symbol_replay_png(
        result.signal_data,
        events,
        current_time=result.equity.index[-1],
        equity=result.equity,
        symbol=result.symbol,
    )

    assert image_bytes.startswith(b"\x89PNG")
