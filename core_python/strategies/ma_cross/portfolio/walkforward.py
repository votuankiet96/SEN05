"""Portfolio-level walk-forward validation cho MA Cross.

Chạy run_portfolio_backtest trên từng cặp IS/OOS window và gom metrics
vào một DataFrame để so sánh hiệu quả in-sample vs out-of-sample.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

from .backtest import run_portfolio_backtest


def portfolio_walkforward(
    symbols: Iterable[str],
    *,
    tf: str = "M30",
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    windows: Iterable[tuple[str, str, str, str]],
    indicator_overrides: Mapping[str, Any] | None = None,
    strategy_overrides: Mapping[str, Any] | None = None,
    max_bars: int = 80_000,
    costs: Mapping[str, Any] | None = None,
    broker_profile: str | None = None,
) -> pd.DataFrame:
    """Chạy portfolio backtest trên dãy IS/OOS windows.

    Args:
        symbols: Symbol keys cần backtest.
        tf: Timeframe code.
        account_mode: "standard" hoặc "ftmo".
        initial_balance: Tổng vốn portfolio.
        windows: Iterable của (is_from, is_to, oos_from, oos_to) — ISO date strings.
        indicator_overrides: Override indicator params (FAST_MA, SLOW_MA, ...).
        strategy_overrides: Override strategy params (atr_stop_mult, ...).
        max_bars: Số bar tối đa mỗi lần load.
        costs: Override cost settings.
        broker_profile: Broker profile key.

    Returns:
        DataFrame với một hàng mỗi (window, phase) — cột IS/OOS rõ ràng.
    """
    syms = list(symbols)
    rows: list[dict[str, Any]] = []

    for idx, (is_from, is_to, oos_from, oos_to) in enumerate(windows, start=1):
        for phase, start, end in [("IS", is_from, is_to), ("OOS", oos_from, oos_to)]:
            result = run_portfolio_backtest(
                syms,
                account_mode=account_mode,
                initial_balance=initial_balance,
                tf=tf,
                date_from=start,
                date_to=end,
                max_bars=max_bars,
                indicator_overrides=indicator_overrides,
                strategy_overrides=strategy_overrides,
                costs=costs,
                broker_profile=broker_profile,
            )
            row: dict[str, Any] = {
                "window": idx,
                "phase": phase,
                "date_from": start,
                "date_to": end,
            }
            row.update(result.get("metrics", {}))
            rows.append(row)

    return pd.DataFrame(rows)


__all__ = ["portfolio_walkforward"]
