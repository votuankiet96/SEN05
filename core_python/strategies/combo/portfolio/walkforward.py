"""Portfolio-level walk-forward evaluation for Combo.

Architecture role
-----------------
- This module evaluates a fixed set of per-symbol parameters across rolling OOS
  windows at portfolio level.
- Unlike symbol walk-forward, it does not re-optimize inside each window; it is
  meant to validate a portfolio specification you already selected.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from shared.contracts import PortfolioWindowResult
from shared.portfolio import build_combined_equity, calc_portfolio_metrics

from ..config import SYMBOLS, get_indicator_params
from ..symbol.backtest import load_backtest_full, run_symbol_backtest_on_frame


def walk_forward_portfolio(
    symbol_params: dict[str, dict[str, Any]],
    *,
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    is_bars: int = 5000,
    oos_bars: int = 1250,
    step_bars: int = 1250,
    indicator_overrides: dict[str, Any] | None = None,
    strategy_overrides: dict[str, Any] | None = None,
    costs: dict[str, Any] | None = None,
    broker_profile: str | None = None,
    tf: str = "H4",
    max_bars: int = 80000,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run a fixed-parameter rolling OOS test at portfolio level.

    The function aligns all symbols to a common history length so each OOS window
    compares the same chronological slice across the portfolio.
    """
    if not symbol_params:
        return pd.DataFrame(), {}

    raw_cache = {
        sym: load_backtest_full(SYMBOLS[sym]["symbol_id"], tf=tf, max_bars=max_bars)
        for sym in symbol_params
    }
    common_n = min(len(df) for df in raw_cache.values())
    if common_n < is_bars + oos_bars:
        return pd.DataFrame(), {}

    rows = []
    per_symbol_balance = initial_balance / len(symbol_params)
    seeds = {sym: per_symbol_balance for sym in symbol_params}

    # Warmup cần thiết để indicator (MA/MACD/ATR) có đủ state khi bắt đầu OOS
    _base_ind = get_indicator_params()
    _warmup = max(
        int(_base_ind.get("MACD_SLOW", 25)),
        int(_base_ind.get("MA_PERIOD", 20)),
        int(_base_ind.get("ATR_PERIOD", 5)),
    ) + 5

    step = 0
    while True:
        oos_start = is_bars + step * step_bars
        oos_end = oos_start + oos_bars
        if oos_end > common_n:
            break

        trades_by_symbol = {}
        equity_by_symbol = {}
        start_ts = None
        end_ts = None

        for sym, params in symbol_params.items():
            # Carry warmup bars từ IS tail để indicator có đủ history
            warmup_from = max(0, oos_start - _warmup)
            aligned = raw_cache[sym].tail(common_n)
            df_raw_wide = aligned.iloc[warmup_from:oos_end].copy()
            if df_raw_wide.empty:
                continue
            # date_from giới hạn execution chỉ trong OOS bars thật
            oos_date_from = str(aligned.index[oos_start])
            oos_date_to   = str(aligned.index[oos_end - 1])
            result = run_symbol_backtest_on_frame(
                sym,
                df_raw_wide,
                init_eq=per_symbol_balance,
                account_mode=account_mode,
                date_from=oos_date_from,
                date_to=oos_date_to,
                indicator_overrides=indicator_overrides,
                symbol_overrides=params,
                strategy_overrides=strategy_overrides,
                costs=costs,
                broker_profile=broker_profile,
            )
            trades_by_symbol[sym] = result.trades
            equity_by_symbol[sym] = result.equity
            start_ts = oos_date_from if start_ts is None else min(start_ts, oos_date_from)
            end_ts   = oos_date_to   if end_ts   is None else max(end_ts,   oos_date_to)

        if not equity_by_symbol:
            break

        _, combined_equity = build_combined_equity(equity_by_symbol, fill_value=seeds)
        metrics = calc_portfolio_metrics(
            trades_by_symbol,
            equity_by_symbol,
            fill_value=seeds,
        )
        rows.append(
            PortfolioWindowResult(
                window=step,
                start=pd.Timestamp(start_ts),
                end=pd.Timestamp(end_ts),
                metrics=metrics,
                combined_equity=combined_equity,
            )
        )
        step += 1

    if not rows:
        return pd.DataFrame(), {}

    df = pd.DataFrame(
        [
            {
                "window": row.window,
                "oos_start": row.start,
                "oos_end": row.end,
                **row.metrics,
            }
            for row in rows
        ]
    )
    summary = {
        "n_windows": len(rows),
        "profitable_windows": int((df.get("total_return", pd.Series(dtype=float)) > 0).sum()),
        "avg_total_return": float(df.get("total_return", pd.Series(dtype=float)).mean()),
        "avg_max_drawdown": float(df.get("max_drawdown", pd.Series(dtype=float)).mean()),
        "avg_sharpe": float(df.get("sharpe", pd.Series(dtype=float)).mean()),
    }
    return df, summary
