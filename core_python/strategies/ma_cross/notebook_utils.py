"""Notebook helpers for MA Cross research.

The helpers in this module are intentionally thin. They orchestrate the
MA Cross source-of-truth modules, format research output, and keep notebooks
from reimplementing signal or execution rules.
"""

from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from shared.portfolio import (
    build_combined_equity,
    calc_portfolio_metrics,
    check_portfolio_ftmo,
    combine_trade_logs,
)

from .config import (
    SYMBOLS,
    TIMEFRAMES,
    get_indicator_params,
    get_symbol_search_space,
    summary,
)
from .symbol.backtest import run_symbol_backtest

try:
    from IPython.display import Markdown, display
except Exception:  # pragma: no cover - console fallback.
    Markdown = None

    def display(obj: Any) -> None:
        print(obj)


METRIC_COLUMNS = [
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


def configure_notebook() -> None:
    """Set pandas display defaults useful in Jupyter research."""
    pd.set_option("display.max_columns", 80)
    pd.set_option("display.width", 160)
    pd.set_option("display.float_format", lambda x: f"{x:,.4f}")


def show_note(text: str) -> None:
    """Render a short markdown note when available."""
    if Markdown is None:
        print(text)
    else:
        display(Markdown(text))


def show_strategy_summary() -> None:
    """Display the MA Cross source-of-truth summary."""
    show_note("```text\n" + summary() + "\n```")


def show_run_config(title: str, config: Mapping[str, Any]) -> None:
    """Display a config mapping as a two-column table."""
    show_note(f"### {title}")
    display(pd.Series(dict(config), name="value").to_frame())


def metric_row(
    result: Any,
    *,
    symbol: str,
    timeframe: str,
    params: Mapping[str, Any] | None = None,
    strategy_overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten one SymbolBacktestResult into a research table row."""
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
    """Build a stable metrics table with preferred columns first."""
    df = pd.DataFrame(list(rows))
    if df.empty:
        return df
    preferred = [c for c in METRIC_COLUMNS if c in df.columns]
    rest = [c for c in df.columns if c not in preferred]
    return df[preferred + rest]


def run_timeframe_matrix(
    symbol: str,
    *,
    account_mode: str = "standard",
    initial_balance: float = 100_000.0,
    timeframes: Iterable[str] = TIMEFRAMES,
    max_bars: int = 50_000,
    date_from: str | None = None,
    date_to: str | None = None,
    indicator_overrides: Mapping[str, Any] | None = None,
    strategy_overrides: Mapping[str, Any] | None = None,
    costs: Mapping[str, Any] | None = None,
    broker_profile: str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Run one symbol across MA Cross timeframes and return table + results."""
    rows: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    for tf in timeframes:
        result = run_symbol_backtest(
            symbol,
            init_eq=initial_balance,
            account_mode=account_mode,
            tf=str(tf).upper(),
            date_from=date_from,
            date_to=date_to,
            max_bars=max_bars,
            indicator_overrides=dict(indicator_overrides or {}),
            strategy_overrides=dict(strategy_overrides or {}),
            costs=dict(costs or {}),
            broker_profile=broker_profile,
        )
        results[str(tf).upper()] = result
        rows.append(
            metric_row(
                result,
                symbol=symbol,
                timeframe=str(tf).upper(),
                params=indicator_overrides,
                strategy_overrides=strategy_overrides,
            )
        )
    return metrics_frame(rows), results


def _candidate_grid(symbol: str, search_space: Mapping[str, Iterable[Any]] | None = None) -> list[dict[str, Any]]:
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
    """Run a conservative full-engine grid for MA Cross.

    This is slower than a vectorized optimizer, but it keeps the notebook honest:
    every candidate uses the same market execution engine as normal backtests.
    """
    rows: list[dict[str, Any]] = []
    for candidate in _candidate_grid(symbol, search_space):
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


def rank_grid(df: pd.DataFrame, score_column: str = "sharpe") -> pd.DataFrame:
    """Sort candidate table by a selected metric, then by robustness proxies."""
    if df.empty:
        return df
    sort_cols = [c for c in [score_column, "profit_factor", "total_trades"] if c in df.columns]
    return df.sort_values(sort_cols, ascending=[False] * len(sort_cols)).reset_index(drop=True)


def select_top_candidates(
    df: pd.DataFrame,
    *,
    top_n: int = 10,
    min_trades: int = 30,
    min_profit_factor: float = 1.0,
    max_drawdown: float | None = None,
    score_column: str = "sharpe",
) -> pd.DataFrame:
    """Filter optimizer rows with simple anti-overfit gates."""
    if df.empty:
        return df
    out = df.copy()
    if "total_trades" in out.columns:
        out = out[out["total_trades"].fillna(0) >= min_trades]
    if "profit_factor" in out.columns:
        out = out[out["profit_factor"].fillna(0) >= min_profit_factor]
    if max_drawdown is not None and "max_drawdown" in out.columns:
        out = out[out["max_drawdown"].abs().fillna(999) <= max_drawdown]
    return rank_grid(out, score_column=score_column).head(top_n).reset_index(drop=True)


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
    """Run equal-sleeve symbol backtests and combine them as a portfolio."""
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
    }


def simple_walkforward(
    symbol: str,
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
    """Run explicit IS/OOS date windows.

    Each window tuple is (is_from, is_to, oos_from, oos_to). The helper does not
    optimize inside the window; it validates one MA Cross parameter set across
    time so the notebook can catch regime sensitivity.
    """
    rows: list[dict[str, Any]] = []
    for idx, (is_from, is_to, oos_from, oos_to) in enumerate(windows, start=1):
        for phase, start, end in [("IS", is_from, is_to), ("OOS", oos_from, oos_to)]:
            result = run_symbol_backtest(
                symbol,
                init_eq=initial_balance,
                account_mode=account_mode,
                tf=tf,
                date_from=start,
                date_to=end,
                max_bars=max_bars,
                indicator_overrides=dict(indicator_overrides or {}),
                strategy_overrides=dict(strategy_overrides or {}),
                costs=dict(costs or {}),
                broker_profile=broker_profile,
            )
            row = metric_row(
                result,
                symbol=symbol,
                timeframe=tf,
                params=indicator_overrides,
                strategy_overrides=strategy_overrides,
            )
            row.update({"window": idx, "phase": phase, "date_from": start, "date_to": end})
            rows.append(row)
    return metrics_frame(rows)


def export_research_bundle(
    bundle: Mapping[str, Any],
    *,
    name: str,
    output_dir: str | Path = "output/ma_cross/research",
) -> Path:
    """Export pandas objects from a research run to CSV files."""
    out = Path(output_dir) / name
    out.mkdir(parents=True, exist_ok=True)
    for key, value in bundle.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(out / f"{key}.csv", index=True)
        elif isinstance(value, pd.Series):
            value.to_csv(out / f"{key}.csv", header=True)
    return out


__all__ = [
    "configure_notebook",
    "export_research_bundle",
    "metric_row",
    "metrics_frame",
    "rank_grid",
    "run_portfolio_backtest",
    "run_symbol_grid_search",
    "run_timeframe_matrix",
    "select_top_candidates",
    "show_note",
    "show_run_config",
    "show_strategy_summary",
    "simple_walkforward",
]
