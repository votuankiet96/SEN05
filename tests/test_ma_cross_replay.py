from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from core_python.shared.contracts import BacktestReplayEvent
from core_python.strategies.ma_cross.execution import backtest_market_symbol
from core_python.strategies.ma_cross.research_utils import build_symbol_replay_events, replay_state_at
from core_python.strategies.ma_cross.research_utils.replay import _render_symbol_replay_png


def _result_with_engine_events(symbol: str = "US30") -> SimpleNamespace:
    idx = pd.date_range("2023-01-01", periods=5, freq="4h", name="BarTime")
    signal_data = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "fast_ma": [99.0, 100.0, 101.0, 102.0, 103.0],
            "slow_ma": [98.0, 99.0, 100.0, 101.0, 102.0],
            "atr": [10.0, 10.0, 10.0, 10.0, 10.0],
            "signal": [0, 1, 0, 0, 0],
        },
        index=idx,
    )
    equity = pd.Series([100_000.0, 100_000.0, 100_000.0, 100_050.0, 100_120.0], index=idx)
    return SimpleNamespace(
        symbol=symbol,
        signal_data=signal_data,
        trades=[],
        equity=equity,
        replay_events=[
            BacktestReplayEvent(
                time=idx[1],
                symbol=symbol,
                event_type="SIGNAL_DETECTED",
                side="BUY",
                price=101.0,
                reason="close_confirmed",
            ),
            BacktestReplayEvent(
                time=idx[2],
                symbol=symbol,
                event_type="POSITION_OPENED",
                side="BUY",
                price=102.0,
                entry=102.0,
                sl=82.0,
                tp=None,
                lot_size=0.5,
                equity=100_000.0,
                reason="next_bar_open",
            ),
            BacktestReplayEvent(
                time=idx[4],
                symbol=symbol,
                event_type="FORCE_CLOSE_END_OF_DATA",
                side="BUY",
                price=104.0,
                pnl_usd=120.0,
                equity=100_120.0,
                reason="end_of_data",
            ),
        ],
    )


def test_market_engine_replay_events_do_not_change_trades_or_equity():
    idx = pd.date_range("2023-01-01", periods=5, freq="4h", name="BarTime")
    frame = pd.DataFrame(
        {
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "fast_ma": [99.0, 100.0, 101.0, 102.0, 103.0],
            "slow_ma": [98.0, 99.0, 100.0, 101.0, 102.0],
            "atr": [10.0, 10.0, 10.0, 10.0, 10.0],
            "signal": [0, 1, 0, 0, 0],
        },
        index=idx,
    )
    cfg = {
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
        "atr_stop_mult": 2.0,
        "atr_tp_mult": 0.0,
        "partial_tp_fraction": 0.0,
        "trailing_activation": 10.0,
        "allow_same_bar_exit": False,
    }
    costs = {"slippage_pts": 0.0, "commission_per_lot": 0.0}

    trades_plain, equity_plain = backtest_market_symbol(
        "TEST",
        frame,
        cfg,
        100_000.0,
        strategy=strategy,
        costs=costs,
    )
    events = []
    trades_events, equity_events = backtest_market_symbol(
        "TEST",
        frame,
        cfg,
        100_000.0,
        strategy=strategy,
        costs=costs,
        event_sink=events,
    )

    assert trades_events == trades_plain
    pd.testing.assert_series_equal(equity_events, equity_plain)
    event_types = {event.event_type for event in events}
    assert {"SIGNAL_DETECTED", "POSITION_OPENED", "FORCE_CLOSE_END_OF_DATA"}.issubset(event_types)


def test_symbol_replay_events_prefer_engine_events():
    result = _result_with_engine_events()

    events = build_symbol_replay_events(result)

    assert events["event_type"].tolist() == [
        "SIGNAL_DETECTED",
        "POSITION_OPENED",
        "FORCE_CLOSE_END_OF_DATA",
    ]
    assert events.iloc[1]["sl"] == 82.0
    assert events.iloc[-1]["pnl_usd"] == 120.0


def test_replay_state_counts_engine_open_and_closed_events():
    result = _result_with_engine_events()
    events = build_symbol_replay_events(result)

    mid_state = replay_state_at(events, pd.Timestamp("2023-01-01 08:00"), equity=result.equity)
    final_state = replay_state_at(events, pd.Timestamp("2023-01-01 16:00"), equity=result.equity)

    assert mid_state["open_trades"] == 1
    assert mid_state["closed_trades"] == 0
    assert final_state["open_trades"] == 0
    assert final_state["closed_trades"] == 1
    assert final_state["pnl_usd"] == 120.0


def test_symbol_replay_png_renders_from_engine_events():
    result = _result_with_engine_events()
    events = build_symbol_replay_events(result)

    image_bytes = _render_symbol_replay_png(
        result.signal_data,
        events,
        current_time=result.equity.index[-1],
        equity=result.equity,
        symbol=result.symbol,
    )

    assert image_bytes.startswith(b"\x89PNG")
