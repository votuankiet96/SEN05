from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

from core_python.shared.analytics import (
    build_combined_equity,
    calc_portfolio_metrics,
    check_portfolio_ftmo,
    combine_trade_logs,
)

from ..config import SYMBOLS
from ..symbol.backtest import run_symbol_backtest
from ..symbol.optimize import metric_row, metrics_frame


def run_portfolio_backtest(
    symbols: Iterable[str],
    *,
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    tf: str = "M30",
    max_bars: int = 50_000,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: Mapping[str, Any] | None = None,
    strategy_overrides: Mapping[str, Any] | None = None,
    costs: Mapping[str, Any] | None = None,
    broker_profile: str | None = None,
) -> dict[str, Any]:
    symbols = [s for s in symbols if s in SYMBOLS]
    if not symbols:
        raise ValueError("No valid symbols supplied.")

    sleeve_balance = initial_balance / len(symbols)
    results: dict[str, Any] = {}
    trades_by_symbol: dict[str, list[dict[str, Any]]] = {}
    equity_by_symbol: dict[str, pd.Series] = {}
    rows: list[dict[str, Any]] = []

    for symbol in symbols:
        result = run_symbol_backtest(
            symbol,
            init_eq=sleeve_balance,
            account_mode=account_mode,
            tf=tf,
            date_from=date_from,
            date_to=date_to,
            max_bars=max_bars,
            indicator_overrides=dict(indicator_overrides or {}),
            strategy_overrides=dict(strategy_overrides or {}),
            costs=dict(costs or {}),
            broker_profile=broker_profile,
        )
        results[symbol] = result
        trades_by_symbol[symbol] = list(result.trades or [])
        equity_by_symbol[symbol] = result.equity
        rows.append(
            metric_row(
                result,
                symbol=symbol,
                timeframe=tf,
                params=indicator_overrides,
                strategy_overrides=strategy_overrides,
            )
        )

    equity_frame, combined_equity = build_combined_equity(
        equity_by_symbol,
        fill_value=sleeve_balance,
    )
    metrics = calc_portfolio_metrics(
        trades_by_symbol,
        equity_by_symbol,
        tf_code=tf,
        fill_value=sleeve_balance,
    )
    ftmo = check_portfolio_ftmo(
        combined_equity,
        initial_balance,
        daily_loss_limit=float((strategy_overrides or {}).get("daily_loss_limit", 0.05)),
        max_dd_limit=float((strategy_overrides or {}).get("max_drawdown_limit", 0.10)),
    )
    return {
        "results": results,
        "symbol_metrics": metrics_frame(rows),
        "trades": combine_trade_logs(trades_by_symbol),
        "equity_frame": equity_frame,
        "combined_equity": combined_equity,
        "metrics": metrics,
        "ftmo": ftmo,
        "portfolio_model": "independent_sleeves",
    }


__all__ = ["run_portfolio_backtest"]
