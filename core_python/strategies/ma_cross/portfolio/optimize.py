"""Per-symbol grid search aggregate cho portfolio MA Cross.

QUAN TRỌNG: Đây KHÔNG phải portfolio combination optimizer.
Module này không tìm tổ hợp tham số tối ưu giữa các symbols.
Nó chạy run_symbol_grid_search độc lập trên từng symbol rồi trả về
dict kết quả để researcher tự lựa chọn combination thủ công.

Portfolio-level parameter combination là công việc manual của researcher,
không phải automation của module này.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import pandas as pd

from ..symbol.optimize import run_symbol_grid_search


def run_portfolio_grid_search(
    symbols: Iterable[str],
    *,
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    max_bars: int = 50_000,
    date_from: str | None = None,
    date_to: str | None = None,
    search_space: Mapping[str, Iterable[Any]] | None = None,
    costs: Mapping[str, Any] | None = None,
    broker_profile: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Chạy grid search độc lập cho từng symbol trong portfolio.

    Mỗi symbol được test với sleeve_balance = initial_balance / n_symbols.

    Args:
        symbols: Danh sách symbol keys cần optimize.
        account_mode: "standard" hoặc "ftmo".
        initial_balance: Tổng vốn portfolio; chia đều cho n symbols.
        max_bars: Số bar tối đa mỗi symbol.
        date_from: Giới hạn bắt đầu (ISO string hoặc None).
        date_to: Giới hạn kết thúc (ISO string hoặc None).
        search_space: Override search space (keys: fast_ma, slow_ma, atr_stop_mult, ...).
        costs: Override cost settings.
        broker_profile: Broker profile key.

    Returns:
        Dict {symbol_key: ranked_DataFrame} — một DataFrame grid results mỗi symbol.
    """
    syms = list(symbols)
    if not syms:
        return {}
    sleeve = initial_balance / len(syms)
    return {
        s: run_symbol_grid_search(
            s,
            account_mode=account_mode,
            initial_balance=sleeve,
            max_bars=max_bars,
            date_from=date_from,
            date_to=date_to,
            search_space=search_space,
            costs=costs,
            broker_profile=broker_profile,
        )
        for s in syms
    }


__all__ = ["run_portfolio_grid_search"]
