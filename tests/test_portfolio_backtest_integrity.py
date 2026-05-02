from __future__ import annotations

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core_python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core_python.strategies.combo.execution import backtest_portfolio
from core_python.strategies.combo.portfolio import backtest as portfolio_backtest
from core_python.strategies.combo.portfolio import walkforward as portfolio_walkforward


def _frame(index: list[str], rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index, name="BarTime"))


def _cfg(**overrides) -> dict:
    cfg = {
        "x": 0.0,
        "ktp": 10.0,
        "contract_value": 1.0,
        "point_size": 1.0,
        "spread_pts": 0.0,
        "commission_per_lot": 0.0,
        "slippage_pts": 0.0,
        "min_lot_size": 0.01,
        "max_lot_size": 100000.0,
        "lot_step": 0.01,
        "swap_long_per_lot_per_day": 0.0,
        "swap_short_per_lot_per_day": 0.0,
    }
    cfg.update(overrides)
    return cfg


def _strategy(**overrides) -> dict:
    strategy = {
        "risk_per_trade": 0.01,
        "daily_loss_limit": 1.0,
        "max_drawdown_limit": 1.0,
        "trailing_activation": 999.0,
        "pending_ttl_bars": 3,
        "partial_tp_fraction": 0.0,
    }
    strategy.update(overrides)
    return strategy


def test_run_portfolio_backtest_uses_symbol_frames_keyword(monkeypatch):
    symbols = {"AAA": {"symbol_id": 1}, "BBB": {"symbol_id": 2}}
    monkeypatch.setattr(portfolio_backtest, "SYMBOLS", symbols)
    monkeypatch.setattr(portfolio_backtest, "get_cost_settings", lambda *args, **kwargs: {})

    raw = _frame(
        ["2023-01-01 00:00", "2023-01-01 04:00"],
        [
            {"open": 90.0, "high": 100.0, "low": 80.0, "close": 95.0, "atr": 100.0, "ma": 90.0, "signal": 0},
            {"open": 95.0, "high": 99.0, "low": 90.0, "close": 96.0, "atr": 100.0, "ma": 90.0, "signal": 0},
        ],
    )

    monkeypatch.setattr(portfolio_backtest, "load_backtest_data", lambda *args, **kwargs: raw.copy())
    monkeypatch.setattr(
        portfolio_backtest,
        "build_symbol_signal_frame",
        lambda sym, df_raw, **kwargs: (df_raw.copy(), _cfg(), {"MIN_RR": 1.25}),
    )

    result = portfolio_backtest.run_portfolio_backtest(
        ["AAA", "BBB"],
        initial_balance=100_000.0,
        allocations={"AAA": 0.8, "BBB": 0.2},
    )

    assert list(result.symbol_results) == ["AAA", "BBB"]
    assert not result.combined_equity.empty
    assert result.portfolio_model == "unified_account"


def test_portfolio_reversal_pnl_is_not_available_before_exit_timestamp():
    a = _frame(
        [
            "2023-01-01 00:00",
            "2023-01-01 04:00",
            "2023-01-01 08:00",
            "2023-01-01 12:00",
        ],
        [
            {"open": 95.0, "high": 100.0, "low": 90.0, "close": 99.0, "atr": 100.0, "ma": 95.0, "signal": 1},
            {"open": 99.0, "high": 101.0, "low": 95.0, "close": 100.0, "atr": 100.0, "ma": 95.0, "signal": 0},
            {"open": 100.0, "high": 121.0, "low": 99.0, "close": 120.0, "atr": 100.0, "ma": 95.0, "signal": -1},
            {"open": 200.0, "high": 201.0, "low": 199.0, "close": 200.0, "atr": 100.0, "ma": 95.0, "signal": 0},
        ],
    )
    b = _frame(
        ["2023-01-01 00:00", "2023-01-01 10:00"],
        [
            {"open": 45.0, "high": 50.0, "low": 40.0, "close": 49.0, "atr": 100.0, "ma": 45.0, "signal": 1},
            {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "atr": 100.0, "ma": 45.0, "signal": 0},
        ],
    )

    trades, account_eq, _, _ = backtest_portfolio(
        {"AAA": a, "BBB": b},
        {"AAA": _cfg(), "BBB": _cfg()},
        100_000.0,
        allocations={"AAA": 0.5, "BBB": 0.5},
        strategy=_strategy(),
    )

    b_trade = next(t for t in trades if t["symbol"] == "BBB")
    assert b_trade["lot_size"] == 50.0
    eq_at_b_fill = account_eq.loc[pd.Timestamp("2023-01-01 10:00")]
    if isinstance(eq_at_b_fill, pd.Series):
        eq_at_b_fill = eq_at_b_fill.iloc[-1]
    assert eq_at_b_fill == 100_000.0
    a_trade = next(t for t in trades if t["symbol"] == "AAA")
    assert a_trade["exit_reason"] == "REVERSED"
    assert a_trade["exit_time"] == pd.Timestamp("2023-01-01 12:00")
    assert account_eq.index.is_unique


def test_portfolio_equity_marks_open_positions_to_market_but_sizes_on_realized_equity():
    a = _frame(
        [
            "2023-01-01 00:00",
            "2023-01-01 04:00",
            "2023-01-01 08:00",
        ],
        [
            {"open": 95.0, "high": 100.0, "low": 90.0, "close": 99.0, "atr": 100.0, "ma": 95.0, "signal": 1},
            {"open": 100.0, "high": 101.0, "low": 95.0, "close": 95.0, "atr": 100.0, "ma": 95.0, "signal": 0},
            {"open": 95.0, "high": 101.0, "low": 95.0, "close": 100.0, "atr": 100.0, "ma": 95.0, "signal": 0},
        ],
    )
    b = _frame(
        [
            "2023-01-01 00:00",
            "2023-01-01 04:00",
            "2023-01-01 08:00",
        ],
        [
            {"open": 45.0, "high": 50.0, "low": 40.0, "close": 49.0, "atr": 100.0, "ma": 45.0, "signal": 1},
            {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "atr": 100.0, "ma": 45.0, "signal": 0},
            {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "atr": 100.0, "ma": 45.0, "signal": 0},
        ],
    )

    trades, account_eq, symbol_eq, _ = backtest_portfolio(
        {"AAA": a, "BBB": b},
        {"AAA": _cfg(), "BBB": _cfg()},
        100_000.0,
        allocations={"AAA": 0.5, "BBB": 0.5},
        strategy=_strategy(),
    )

    lots = {trade["symbol"]: trade["lot_size"] for trade in trades}
    assert lots["BBB"] == 50.0
    assert symbol_eq["AAA"].loc[pd.Timestamp("2023-01-01 04:00")] == 49_750.0
    assert account_eq.loc[pd.Timestamp("2023-01-01 04:00")] == 99_750.0
    assert account_eq.iloc[-1] == 100_000.0


def test_portfolio_equity_preserves_initial_balance_when_symbol_histories_start_later():
    a = _frame(
        ["2023-01-01 00:00", "2023-01-01 04:00"],
        [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0, "ma": 100.0, "signal": 0},
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0, "ma": 100.0, "signal": 0},
        ],
    )
    b = _frame(
        ["2023-01-02 00:00", "2023-01-02 04:00"],
        [
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0, "ma": 100.0, "signal": 0},
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "atr": 1.0, "ma": 100.0, "signal": 0},
        ],
    )

    _, account_eq, symbol_eq, _ = backtest_portfolio(
        {"AAA": a, "BBB": b},
        {"AAA": _cfg(), "BBB": _cfg()},
        100_000.0,
        allocations={"AAA": 0.5, "BBB": 0.5},
        strategy=_strategy(),
    )

    assert account_eq.iloc[0] == 100_000.0
    assert account_eq.loc[pd.Timestamp("2023-01-01 00:00")] == 100_000.0
    assert symbol_eq["BBB"].loc[pd.Timestamp("2023-01-01 00:00")] == 50_000.0


def test_portfolio_partial_tp_does_not_crash_and_uses_trade_fraction():
    df = _frame(
        ["2023-01-01 00:00", "2023-01-01 04:00", "2023-01-01 08:00"],
        [
            {"open": 95.0, "high": 100.0, "low": 90.0, "close": 99.0, "atr": 1.0, "ma": 95.0, "signal": 1},
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "atr": 1.0, "ma": 95.0, "signal": 0},
            {"open": 100.5, "high": 102.0, "low": 100.0, "close": 102.0, "atr": 1.0, "ma": 95.0, "signal": 0},
        ],
    )

    trades, _, _, _ = backtest_portfolio(
        {"AAA": df},
        {"AAA": _cfg(ktp=1.0)},
        100_000.0,
        strategy=_strategy(partial_tp_fraction=0.5),
    )

    assert len(trades) == 1
    assert trades[0]["partial_tp_hit"] is True
    assert trades[0]["half1_exit"] == 101.0


def test_portfolio_final_bar_reversal_closes_as_reversed_not_force_close():
    df = _frame(
        ["2023-01-01 00:00", "2023-01-01 04:00", "2023-01-01 08:00"],
        [
            {"open": 95.0, "high": 100.0, "low": 90.0, "close": 99.0, "atr": 100.0, "ma": 95.0, "signal": 1},
            {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "atr": 100.0, "ma": 95.0, "signal": 0},
            {"open": 100.5, "high": 102.0, "low": 99.5, "close": 101.0, "atr": 100.0, "ma": 95.0, "signal": -1},
        ],
    )

    trades, _, _, _ = backtest_portfolio(
        {"AAA": df},
        {"AAA": _cfg()},
        100_000.0,
        strategy=_strategy(),
    )

    assert len(trades) == 1
    assert trades[0]["exit_reason"] == "REVERSED"
    assert trades[0]["exit_time"] == pd.Timestamp("2023-01-01 08:00")


def test_custom_allocations_affect_portfolio_reporting(monkeypatch):
    symbols = {"AAA": {"symbol_id": 1}, "BBB": {"symbol_id": 2}}
    monkeypatch.setattr(portfolio_backtest, "SYMBOLS", symbols)
    monkeypatch.setattr(portfolio_backtest, "get_cost_settings", lambda *args, **kwargs: {})
    raw = _frame(
        ["2023-01-01 00:00"],
        [{"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "atr": 1.0, "ma": 1.0, "signal": 0}],
    )
    monkeypatch.setattr(portfolio_backtest, "load_backtest_data", lambda *args, **kwargs: raw.copy())
    monkeypatch.setattr(
        portfolio_backtest,
        "build_symbol_signal_frame",
        lambda sym, df_raw, **kwargs: (df_raw.copy(), _cfg(), {"MIN_RR": 1.25}),
    )

    result = portfolio_backtest.run_portfolio_backtest(
        ["AAA", "BBB"],
        initial_balance=100_000.0,
        allocations={"AAA": 0.8, "BBB": 0.2},
    )

    first = result.equity_frame.iloc[0]
    assert first["AAA"] == 80_000.0
    assert first["BBB"] == 20_000.0


def test_per_symbol_strategy_settings_are_preserved_and_used():
    a = _frame(
        ["2023-01-01 00:00", "2023-01-01 04:00"],
        [
            {"open": 45.0, "high": 50.0, "low": 40.0, "close": 49.0, "atr": 100.0, "ma": 45.0, "signal": 1},
            {"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "atr": 100.0, "ma": 45.0, "signal": 0},
        ],
    )
    b = a.copy()

    trades, _, _, _ = backtest_portfolio(
        {"AAA": a, "BBB": b},
        {"AAA": _cfg(), "BBB": _cfg()},
        100_000.0,
        allocations={"AAA": 0.5, "BBB": 0.5},
        strategy={
            **_strategy(risk_per_trade=0.01),
            "_per_symbol": {"BBB": _strategy(risk_per_trade=0.02)},
        },
    )

    lots = {t["symbol"]: t["lot_size"] for t in trades}
    assert lots["AAA"] == 50.0
    assert lots["BBB"] == 100.0


def test_portfolio_walk_forward_uses_symbol_frames_keyword(monkeypatch):
    symbols = {"AAA": {"symbol_id": 1}, "BBB": {"symbol_id": 2}}
    monkeypatch.setattr(portfolio_walkforward, "SYMBOLS", symbols)
    monkeypatch.setattr(portfolio_walkforward, "get_cost_settings", lambda *args, **kwargs: {})

    raw = _frame(
        [f"2023-01-01 {hour:02d}:00" for hour in range(8)],
        [
            {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "atr": 1.0, "ma": 1.0, "signal": 0}
            for _ in range(8)
        ],
    )
    monkeypatch.setattr(portfolio_walkforward, "load_backtest_full", lambda *args, **kwargs: raw.copy())
    monkeypatch.setattr(
        portfolio_walkforward,
        "build_symbol_signal_frame",
        lambda sym, df_slice, **kwargs: (df_slice.copy(), _cfg(), {"MIN_RR": 1.25}),
    )

    windows, summary = portfolio_walkforward.walk_forward_portfolio(
        {"AAA": {}, "BBB": {}},
        initial_balance=100_000.0,
        is_bars=2,
        oos_bars=2,
        step_bars=2,
    )

    assert not windows.empty
    assert summary["n_windows"] >= 1
