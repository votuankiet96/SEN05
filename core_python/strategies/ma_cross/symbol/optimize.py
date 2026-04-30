from __future__ import annotations

from itertools import product
from typing import Any, Iterable, Mapping

import pandas as pd

from ..config import TIMEFRAMES, get_symbol_search_space
from .backtest import run_symbol_backtest
from .selection import rank_grid, select_top_candidates


def candidate_grid(
    symbol: str,
    search_space: Mapping[str, Iterable[Any]] | None = None,
) -> list[dict[str, Any]]:
    base = get_symbol_search_space(symbol)
    if search_space:
        base.update(search_space)

    candidates: list[dict[str, Any]] = []
    for fast, slow, stop, tp, timeframe, ma_type in product(
        base.get("fast_ma", []),
        base.get("slow_ma", []),
        base.get("atr_stop_mult", []),
        base.get("atr_tp_mult", []),
        base.get("timeframe", TIMEFRAMES),
        base.get("ma_type", ["sma"]),
    ):
        fast_i = int(fast)
        slow_i = int(slow)
        if fast_i >= slow_i:
            continue
        candidates.append(
            {
                "FAST_MA": fast_i,
                "SLOW_MA": slow_i,
                "MA_TYPE": str(ma_type).lower(),
                "atr_stop_mult": float(stop),
                "atr_tp_mult": float(tp),
                "timeframe": str(timeframe).upper(),
            }
        )
    return candidates


def metric_row(
    result: Any,
    *,
    symbol: str,
    timeframe: str,
    params: Mapping[str, Any] | None = None,
    strategy_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from ..config import get_indicator_params

    params = {**get_indicator_params(), **(params or {})}
    strategy_overrides = dict(strategy_overrides or {})
    row = {
        "symbol": symbol,
        "timeframe": timeframe,
        "ma_type": str(params.get("MA_TYPE", "")).lower(),
        "fast_ma": int(params.get("FAST_MA", 0)),
        "slow_ma": int(params.get("SLOW_MA", 0)),
        "atr_stop_mult": strategy_overrides.get("atr_stop_mult"),
        "atr_tp_mult": strategy_overrides.get("atr_tp_mult"),
    }
    row.update(getattr(result, "metrics", {}) or {})
    return row


def metrics_frame(rows: Iterable[Mapping[str, Any]]) -> pd.DataFrame:
    metric_columns = [
        "symbol",
        "timeframe",
        "ma_type",
        "fast_ma",
        "slow_ma",
        "atr_stop_mult",
        "atr_tp_mult",
        "total_trades",
        "win_rate",
        "profit_factor",
        "sharpe",
        "sortino",
        "total_pnl",
        "total_return",
        "max_drawdown",
        "max_drawdown_usd",
        "avg_rr",
    ]
    df = pd.DataFrame(list(rows))
    if df.empty:
        return df
    preferred = [c for c in metric_columns if c in df.columns]
    rest = [c for c in df.columns if c not in preferred]
    return df[preferred + rest]


def run_symbol_grid_search(
    symbol: str,
    *,
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    max_bars: int = 50_000,
    date_from: str | None = None,
    date_to: str | None = None,
    search_space: Mapping[str, Iterable[Any]] | None = None,
    costs: Mapping[str, Any] | None = None,
    broker_profile: str | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candidate in candidate_grid(symbol, search_space):
        indicator = {
            "MA_TYPE": candidate["MA_TYPE"],
            "FAST_MA": candidate["FAST_MA"],
            "SLOW_MA": candidate["SLOW_MA"],
        }
        strategy = {
            "atr_stop_mult": candidate["atr_stop_mult"],
            "atr_tp_mult": candidate["atr_tp_mult"],
        }
        result = run_symbol_backtest(
            symbol,
            init_eq=initial_balance,
            account_mode=account_mode,
            tf=candidate["timeframe"],
            date_from=date_from,
            date_to=date_to,
            max_bars=max_bars,
            indicator_overrides=indicator,
            strategy_overrides=strategy,
            costs=dict(costs or {}),
            broker_profile=broker_profile,
        )
        rows.append(
            metric_row(
                result,
                symbol=symbol,
                timeframe=candidate["timeframe"],
                params=indicator,
                strategy_overrides=strategy,
            )
        )
    return rank_grid(metrics_frame(rows))


__all__ = [
    "candidate_grid",
    "metric_row",
    "metrics_frame",
    "rank_grid",
    "run_symbol_grid_search",
    "select_top_candidates",
]
