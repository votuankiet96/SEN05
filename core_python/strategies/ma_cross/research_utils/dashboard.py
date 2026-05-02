"""Notebook display helpers for MA Cross research."""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from ._shared import _short_repr, _show_plot, display, show_note, style_table
from ..config import summary


def configure_notebook() -> None:
    """Configure pandas display defaults for MA Cross research notebooks."""
    pd.set_option("display.max_columns", 100)
    pd.set_option("display.width", 180)
    pd.set_option("display.float_format", lambda x: f"{x:,.4f}")


def show_strategy_summary() -> None:
    """Display the MA Cross strategy summary as a code block."""
    show_note("```text\n" + summary() + "\n```")


def show_run_config(title: str, config: Mapping[str, Any]) -> None:
    """Display the effective run configuration as a compact two-column table."""
    frame = pd.DataFrame(
        [{"Parameter": key, "Value": _short_repr(value)} for key, value in dict(config).items()]
    )
    show_note(title, "Effective configuration for this run.")
    display(style_table(frame, monospace_value_col="Value"))


def metrics_to_frame(metrics: Mapping[str, Any] | None) -> pd.DataFrame:
    """Convert scalar metrics into a tidy display frame."""
    rows = []
    for key, value in dict(metrics or {}).items():
        if _is_scalar(value):
            rows.append({"Metric": key, "Value": value})
    return pd.DataFrame(rows)


def show_kpi_dashboard(metrics: Mapping[str, Any] | None, *, title: str = "KPI Summary") -> pd.DataFrame:
    """Display compact KPI cards as a styled table."""
    frame = metrics_to_frame(metrics)
    if frame.empty:
        show_note(title, "No scalar metrics are available.")
        return frame
    preferred = [
        "total_trades",
        "win_rate",
        "profit_factor",
        "sharpe",
        "total_return",
        "max_drawdown",
        "total_pnl",
    ]
    view = frame[frame["Metric"].isin(preferred)].copy()
    if view.empty:
        view = frame.head(12).copy()
    show_note(title, "Primary performance and risk metrics.")
    display(style_table(view, monospace_value_col="Value"))
    return view


def show_monthly_pnl(metrics: Mapping[str, Any] | None, *, title: str = "Monthly PnL") -> pd.DataFrame:
    """Display monthly PnL table when analytics produced one."""
    table = (metrics or {}).get("monthly_pnl_table")
    if table is None:
        show_note(title, "Monthly PnL is not available for this result.")
        return pd.DataFrame()
    frame = table.copy() if isinstance(table, pd.DataFrame) else pd.DataFrame(table)
    show_note(title, "Month-by-month realized performance.")
    display(style_table(frame.reset_index()))
    return frame


def trades_to_frame(trades: Any) -> pd.DataFrame:
    """Normalize trade logs to a DataFrame."""
    if trades is None:
        return pd.DataFrame()
    if isinstance(trades, pd.DataFrame):
        return trades.copy()
    return pd.DataFrame(list(trades))


def show_trade_explorer(
    trades: Any,
    *,
    title: str = "Trade Explorer",
    tail: int = 25,
) -> pd.DataFrame:
    """Display recent trades with the most useful inspection columns first."""
    frame = trades_to_frame(trades)
    if frame.empty:
        show_note(title, "No closed trades are available.")
        return frame
    cols = [
        "symbol",
        "entry_time",
        "exit_time",
        "direction",
        "entry",
        "exit",
        "lot_size",
        "pnl_usd",
        "r_multiple",
        "exit_reason",
        "partial_tp_hit",
        "commission",
        "swap_cost",
    ]
    view = frame[[c for c in cols if c in frame.columns]].tail(tail).copy()
    show_note(title, "Recent closed trades for inspection.")
    display(style_table(view.reset_index(drop=True)))
    return view


def plot_equity_dashboard(equity: Any, trades: Any = None, *, title: str = "Equity Dashboard") -> Any:
    """Plot equity, drawdown, and trade PnL when data is available."""
    import matplotlib.pyplot as plt

    eq = _as_series(equity)
    trade_frame = trades_to_frame(trades)
    if eq.empty:
        show_note(title, "No equity series is available.")
        return None

    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
    eq.plot(ax=axes[0], color="#2563EB", title="Equity")
    dd = calc_drawdown(eq)
    dd.plot(ax=axes[1], color="#DC2626", title="Drawdown (%)")
    axes[1].fill_between(dd.index, dd.values, 0, color="#FCA5A5", alpha=0.25)
    if not trade_frame.empty and "pnl_usd" in trade_frame:
        pd.to_numeric(trade_frame["pnl_usd"], errors="coerce").fillna(0).plot(
            ax=axes[2], kind="bar", color="#16A34A", title="Trade PnL"
        )
    else:
        axes[2].set_title("No trade PnL")
    fig.suptitle(title)
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def plot_price_with_trades(
    signal_data: pd.DataFrame,
    trades: Any,
    *,
    symbol: str = "",
    max_trades: int = 150,
) -> Any:
    """Plot close/MA lines with entry and exit markers."""
    import matplotlib.pyplot as plt

    if signal_data is None or signal_data.empty:
        show_note("Price Chart", "No signal data is available.")
        return None
    frame = signal_data.copy()
    idx = _time_index(frame)
    fig, ax = plt.subplots(figsize=(18, 6))
    ax.plot(idx, frame["close"], color="#111827", lw=1.0, label="close")
    if "fast_ma" in frame:
        ax.plot(idx, frame["fast_ma"], color="#2563EB", lw=1.0, label="fast MA")
    if "slow_ma" in frame:
        ax.plot(idx, frame["slow_ma"], color="#F59E0B", lw=1.0, label="slow MA")

    trade_frame = trades_to_frame(trades).tail(max_trades)
    if not trade_frame.empty:
        if {"entry_time", "entry", "direction"}.issubset(trade_frame.columns):
            direction = trade_frame["direction"].astype(str).str.upper()
            buy = direction.isin(["1", "BUY"])
            sell = direction.isin(["-1", "SELL"])
            ax.scatter(trade_frame.loc[buy, "entry_time"], trade_frame.loc[buy, "entry"], marker="^", color="#16A34A", s=55, label="BUY entry")
            ax.scatter(trade_frame.loc[sell, "entry_time"], trade_frame.loc[sell, "entry"], marker="v", color="#DC2626", s=55, label="SELL entry")
        if {"exit_time", "exit"}.issubset(trade_frame.columns):
            ax.scatter(trade_frame["exit_time"], trade_frame["exit"], marker="x", color="#111827", s=45, label="exit")
    ax.set_title(f"{symbol} MA Cross price + trades".strip())
    ax.grid(alpha=0.25)
    ax.legend(loc="best")
    plt.tight_layout()
    _show_plot(fig)
    return fig, ax


def compare_metrics_frame(results: Mapping[str, Any]) -> pd.DataFrame:
    """Build a comparison frame from labeled SymbolBacktestResult objects."""
    rows = []
    for label, result in dict(results or {}).items():
        row = {"label": label}
        row.update(getattr(result, "metrics", {}) or {})
        rows.append(row)
    return pd.DataFrame(rows)


def show_portfolio_summary(portfolio: Mapping[str, Any]) -> pd.DataFrame:
    """Display MA Cross portfolio metrics and per-symbol contribution."""
    metrics = dict(portfolio.get("metrics", {}) if portfolio else {})
    show_kpi_dashboard(metrics, title="Portfolio KPI")
    symbol_metrics = portfolio.get("symbol_metrics", pd.DataFrame()) if portfolio else pd.DataFrame()
    if isinstance(symbol_metrics, pd.DataFrame) and not symbol_metrics.empty:
        show_note("Per-Symbol Contribution", "Compare trade count, return, drawdown, profit factor, and Sharpe by symbol.")
        display(style_table(symbol_metrics))
        return symbol_metrics
    return pd.DataFrame()


def plot_portfolio_dashboard(portfolio: Mapping[str, Any]) -> Any:
    """Plot combined equity, per-symbol equity, and PnL contribution."""
    import matplotlib.pyplot as plt

    combined = _as_series(portfolio.get("combined_equity") if portfolio else None)
    equity_frame = portfolio.get("equity_frame", pd.DataFrame()) if portfolio else pd.DataFrame()
    trades = trades_to_frame(portfolio.get("trades") if portfolio else None)
    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
    if not combined.empty:
        combined.plot(ax=axes[0], color="#2563EB", title="Combined equity")
    else:
        axes[0].set_title("No combined equity")
    if isinstance(equity_frame, pd.DataFrame) and not equity_frame.empty:
        equity_frame.plot(ax=axes[1], lw=1.0, title="Per-symbol equity")
    else:
        axes[1].set_title("No per-symbol equity")
    if not trades.empty and {"symbol", "pnl_usd"}.issubset(trades.columns):
        contrib = trades.groupby("symbol")["pnl_usd"].sum().sort_values()
        contrib.plot(ax=axes[2], kind="barh", color="#16A34A", title="PnL contribution")
    else:
        axes[2].set_title("No trade contribution")
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def show_ftmo_check(ftmo: Mapping[str, Any] | None, *, title: str = "FTMO Check") -> pd.DataFrame:
    """Display FTMO/account-rule check payload."""
    if not ftmo:
        show_note(title, "No FTMO check is available.")
        return pd.DataFrame()
    frame = pd.DataFrame([{"Rule": k, "Value": v} for k, v in dict(ftmo).items()])
    show_note(title, "Account-rule check for the selected equity curve.")
    display(style_table(frame, monospace_value_col="Value"))
    return frame


def plot_optimization_dashboard(grid: pd.DataFrame, *, score_col: str = "sharpe") -> Any:
    """Plot basic optimizer diagnostics."""
    import matplotlib.pyplot as plt

    if grid is None or grid.empty:
        show_note("Optimization Dashboard", "No grid results are available.")
        return None
    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
    top = grid.head(20).copy()
    if score_col in top:
        top[score_col].plot(ax=axes[0], kind="bar", color="#2563EB", title=f"Top {score_col}")
    if "profit_factor" in top:
        top["profit_factor"].plot(ax=axes[1], kind="bar", color="#16A34A", title="Profit factor")
    if "max_drawdown" in top:
        top["max_drawdown"].abs().plot(ax=axes[2], kind="bar", color="#DC2626", title="Abs max drawdown")
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def show_optimizer_filter_report(grid: pd.DataFrame, candidates: pd.DataFrame, rules: Mapping[str, Any]) -> pd.DataFrame:
    """Display compact before/after optimizer filtering counts."""
    frame = pd.DataFrame(
        [
            {"Item": "Grid rows", "Value": 0 if grid is None else len(grid)},
            {"Item": "Selected rows", "Value": 0 if candidates is None else len(candidates)},
            {"Item": "Rules", "Value": _short_repr(dict(rules or {}))},
        ]
    )
    show_note("Candidate Filter Report", "How many candidates survived the configured gates.")
    display(style_table(frame, monospace_value_col="Value"))
    return frame


def show_best_candidate(candidates: pd.DataFrame, *, title: str = "Best Candidate") -> pd.Series | None:
    """Display the top optimizer candidate."""
    if candidates is None or candidates.empty:
        show_note(title, "No candidate passed the configured gates.")
        return None
    best = candidates.iloc[0]
    show_note(title, "Highest-ranked candidate after filtering.")
    display(style_table(best.to_frame("Value").reset_index().rename(columns={"index": "Parameter"}), monospace_value_col="Value"))
    return best


def show_selected_params(params: Mapping[str, Any] | None, *, title: str = "Selected Parameters") -> pd.DataFrame:
    """Display selected parameter dictionary."""
    frame = pd.DataFrame([{"Parameter": k, "Value": _short_repr(v)} for k, v in dict(params or {}).items()])
    show_note(title, "Parameter set selected for the next research step.")
    display(style_table(frame, monospace_value_col="Value"))
    return frame


def summarize_walkforward(frame: pd.DataFrame) -> pd.DataFrame:
    """Display a compact walk-forward summary."""
    if frame is None or frame.empty:
        show_note("Walk-Forward Summary", "No walk-forward rows are available.")
        return pd.DataFrame()
    cols = [c for c in ["window", "phase", "total_trades", "profit_factor", "sharpe", "total_return", "max_drawdown"] if c in frame.columns]
    view = frame[cols].copy()
    show_note("Walk-Forward Summary", "Compare IS and OOS stability across windows.")
    display(style_table(view))
    return view


def plot_walkforward_dashboard(frame: pd.DataFrame) -> Any:
    """Plot walk-forward return, Sharpe, and drawdown by window/phase."""
    import matplotlib.pyplot as plt

    if frame is None or frame.empty:
        show_note("Walk-Forward Dashboard", "No walk-forward rows are available.")
        return None
    fig, axes = plt.subplots(1, 3, figsize=(18, 4.5))
    x = frame.apply(lambda r: f"{r.get('window', '')}-{r.get('phase', '')}", axis=1)
    for ax, col, title, color in [
        (axes[0], "total_return", "Return", "#2563EB"),
        (axes[1], "sharpe", "Sharpe", "#16A34A"),
        (axes[2], "max_drawdown", "Max drawdown", "#DC2626"),
    ]:
        if col in frame:
            ax.bar(x, pd.to_numeric(frame[col], errors="coerce").fillna(0), color=color)
            ax.set_title(title)
            ax.tick_params(axis="x", rotation=45)
        else:
            ax.set_title(f"No {col}")
    plt.tight_layout()
    _show_plot(fig)
    return fig, axes


def plot_scan_summary(summary_df: pd.DataFrame) -> Any:
    """Display and plot scanner summary."""
    import matplotlib.pyplot as plt

    if summary_df is None or summary_df.empty:
        show_note("Scanner Summary", "No scanner rows are available.")
        return None
    show_note("Scanner Summary", "Visual signal scan. This is not performance evidence.")
    display(style_table(summary_df))
    fig, ax = plt.subplots(figsize=(12, 4))
    if "ma_gap_atr" in summary_df:
        summary_df.plot(x="symbol", y="ma_gap_atr", kind="bar", ax=ax, color="#2563EB", title="MA gap / ATR")
    else:
        ax.set_title("No ma_gap_atr column")
    plt.tight_layout()
    _show_plot(fig)
    return fig, ax


def plot_mode_comparison(results: Mapping[str, Any]) -> pd.DataFrame:
    """Display and plot equity curves for account-mode comparison."""
    import matplotlib.pyplot as plt

    frame = compare_metrics_frame(results)
    show_note("Account-Mode Comparison", "Compare the same configuration under each account mode.")
    display(style_table(frame))
    fig, ax = plt.subplots(figsize=(12, 4))
    for mode, result in dict(results or {}).items():
        eq = _as_series(getattr(result, "equity", None))
        if not eq.empty:
            eq.rename(str(mode)).plot(ax=ax, legend=True)
    ax.set_title("MA Cross account-mode equity")
    plt.tight_layout()
    _show_plot(fig)
    return frame


def calc_drawdown(equity: pd.Series) -> pd.Series:
    """Return percent drawdown from peak equity."""
    eq = _as_series(equity)
    if eq.empty:
        return pd.Series(dtype=float)
    peak = eq.cummax().replace(0, np.nan)
    return (eq / peak - 1.0) * 100.0


def _as_series(value: Any) -> pd.Series:
    if isinstance(value, pd.Series):
        return value.sort_index()
    return pd.Series(dtype=float)


def _time_index(frame: pd.DataFrame) -> pd.Index:
    if isinstance(frame.index, pd.DatetimeIndex):
        return frame.index
    if "BarTime" in frame.columns:
        return pd.DatetimeIndex(pd.to_datetime(frame["BarTime"], errors="coerce"))
    return pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool, np.integer, np.floating, np.bool_))


__all__ = [
    "calc_drawdown",
    "compare_metrics_frame",
    "configure_notebook",
    "metrics_to_frame",
    "plot_equity_dashboard",
    "plot_mode_comparison",
    "plot_optimization_dashboard",
    "plot_portfolio_dashboard",
    "plot_price_with_trades",
    "plot_scan_summary",
    "plot_walkforward_dashboard",
    "show_best_candidate",
    "show_ftmo_check",
    "show_kpi_dashboard",
    "show_monthly_pnl",
    "show_optimizer_filter_report",
    "show_portfolio_summary",
    "show_run_config",
    "show_selected_params",
    "show_strategy_summary",
    "show_trade_explorer",
    "summarize_walkforward",
    "trades_to_frame",
]
