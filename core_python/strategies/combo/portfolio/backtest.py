"""Portfolio-level backtest helpers for Combo.

Architecture role
-----------------
- This file assembles many symbol-level backtests into one equal-weight portfolio.
- It does not own signal logic or execution rules; it delegates those to the
  symbol runner and only handles capital splitting and aggregation.
"""

from __future__ import annotations

from typing import Any

from shared.contracts import PortfolioBacktestResult
from shared.portfolio import (
    build_combined_equity,
    calc_portfolio_metrics,
    check_portfolio_ftmo,
    combine_trade_logs,
)

from ..config import SYMBOLS, get_account_settings
from ..symbol.backtest import run_symbol_backtest


def run_portfolio_backtest(
    symbol_keys: list[str] | None = None,
    *,
    initial_balance: float = 100_000.0,
    account_mode: str = "standard",
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, dict[str, Any]] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    max_bars: int = 60000,
) -> PortfolioBacktestResult:
    """Run a portfolio backtest by backtesting each symbol independently, then combining.

    Current implementation uses equal starting capital per symbol. If you later
    introduce allocation logic, this is the orchestration layer to change.
    """
    symbols = symbol_keys or list(SYMBOLS.keys())
    if not symbols:
        raise ValueError("run_portfolio_backtest() requires at least one symbol.")

    per_symbol_balance = initial_balance / len(symbols)
    symbol_results = {}
    trades_by_symbol = {}
    equity_by_symbol = {}
    seeds = {}

    for sym in symbols:
        result = run_symbol_backtest(
            sym,
            init_eq=per_symbol_balance,
            account_mode=account_mode,
            date_from=date_from,
            date_to=date_to,
            indicator_overrides=indicator_overrides,
            symbol_overrides=(symbol_overrides or {}).get(sym),
            strategy_overrides=strategy_overrides,
            costs=costs,
            broker_profile=broker_profile,
            max_bars=max_bars,
        )
        symbol_results[sym] = result
        trades_by_symbol[sym] = result.trades
        equity_by_symbol[sym] = result.equity
        seeds[sym] = per_symbol_balance

    equity_frame, combined_equity = build_combined_equity(equity_by_symbol, fill_value=seeds)
    metrics = calc_portfolio_metrics(
        trades_by_symbol,
        equity_by_symbol,
        fill_value=seeds,
    )

    # FTMO account-level guard: kiểm tra tổng portfolio có vi phạm FTMO không.
    # Execution engine chỉ kiểm tra ở cấp sleeve (per_symbol_balance), không thể
    # thấy tổng portfolio. Hàm này thêm view ở cấp account để notebook có thể
    # báo cáo đúng kết quả pass/fail cho challenge FTMO.
    if account_mode == "ftmo" and not combined_equity.empty:
        ftmo_cfg = get_account_settings("ftmo", strategy_overrides)
        metrics["ftmo_account_check"] = check_portfolio_ftmo(
            combined_equity,
            initial_balance,
            daily_loss_limit=float(ftmo_cfg.get("daily_loss_limit", 0.05)),
            max_dd_limit=float(ftmo_cfg.get("max_drawdown_limit", 0.10)),
            max_dd_mode=str(ftmo_cfg.get("max_drawdown_mode", "fixed_initial")),
        )

    return PortfolioBacktestResult(
        symbol_results=symbol_results,
        trades=combine_trade_logs(trades_by_symbol),
        equity_frame=equity_frame,
        combined_equity=combined_equity,
        metrics=metrics,
        account_mode=account_mode,
        symbol_keys=symbols,
    )


def compare_account_modes(
    symbol_keys: list[str] | None = None,
    *,
    initial_balance: float = 100_000.0,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: dict[str, Any] | None = None,
    symbol_overrides: dict[str, dict[str, Any]] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    max_bars: int = 60000,
) -> dict[str, PortfolioBacktestResult]:
    """Run the same portfolio under standard and FTMO capital rules."""
    return {
        mode: run_portfolio_backtest(
            symbol_keys,
            initial_balance=initial_balance,
            account_mode=mode,
            date_from=date_from,
            date_to=date_to,
            indicator_overrides=indicator_overrides,
            symbol_overrides=symbol_overrides,
            strategy_overrides=strategy_overrides,
            costs=costs,
            broker_profile=broker_profile,
            max_bars=max_bars,
        )
        for mode in ("standard", "ftmo")
    }
