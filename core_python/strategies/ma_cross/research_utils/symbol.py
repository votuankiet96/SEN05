"""MA Cross symbol/portfolio workflow helpers for research notebooks."""

from __future__ import annotations

from typing import Any, Mapping, MutableMapping

import pandas as pd

from ..config import TIMEFRAME, TIMEFRAMES, get_indicator_params
from ..portfolio.backtest import run_portfolio_backtest
from ..symbol.matrix import run_timeframe_matrix
from ..symbol.optimize import metric_row, metrics_frame, run_symbol_grid_search
from ..symbol.selection import rank_grid, select_top_candidates
from ..symbol.walkforward import simple_walkforward
from .dashboard import (
    plot_equity_dashboard,
    plot_price_with_trades,
    show_kpi_dashboard,
    show_monthly_pnl,
    show_note,
    show_run_config,
    show_trade_explorer,
)
from ._shared import display, style_table, _widget_output_target


def build_symbol_backtest_widget(
    *,
    symbols: Mapping[str, Any],
    run_symbol_backtest: Any,
    default_config: Mapping[str, Any],
    global_ns: MutableMapping[str, Any] | None = None,
) -> Any:
    """Create a compact symbol backtest control panel for MA Cross."""
    try:
        import ipywidgets as widgets
    except Exception as exc:  # pragma: no cover
        return f"ipywidgets is not available, cannot build backtest widget: {exc}"

    ns = global_ns if global_ns is not None else {}
    previous = ns.get("_ma_cross_symbol_backtest_dashboard")
    if previous is not None and hasattr(previous, "close"):
        try:
            previous.close()
        except Exception:
            pass
    token = object()
    ns["_ma_cross_symbol_backtest_dashboard_token"] = token

    symbol_keys = list(symbols.keys())
    indicator_defaults = get_indicator_params()
    default_indicator_overrides = dict(default_config.get("indicator_overrides") or {})
    default_strategy_overrides = dict(default_config.get("strategy_overrides") or {})

    symbol_widget = widgets.Dropdown(
        options=symbol_keys,
        value=default_config.get("symbol") if default_config.get("symbol") in symbol_keys else symbol_keys[0],
        description="Symbol:",
        layout=widgets.Layout(width="230px"),
    )
    timeframe_widget = widgets.Dropdown(
        options=TIMEFRAMES,
        value=str(default_config.get("tf", TIMEFRAME)).upper()
        if str(default_config.get("tf", TIMEFRAME)).upper() in TIMEFRAMES
        else TIMEFRAME,
        description="TF:",
        layout=widgets.Layout(width="210px"),
    )
    account_widget = widgets.Dropdown(
        options=["standard", "ftmo"],
        value=str(default_config.get("account_mode", "standard")),
        description="Account:",
        layout=widgets.Layout(width="230px"),
    )
    execution_widget = widgets.Dropdown(
        options=["market_single", "basket_reversal"],
        value=str(default_strategy_overrides.get("execution_model", "market_single")),
        description="Engine:",
        layout=widgets.Layout(width="250px"),
    )
    date_from_widget = widgets.Text(
        value=str(default_config.get("date_from") or ""),
        description="From:",
        placeholder="YYYY-MM-DD",
        layout=widgets.Layout(width="230px"),
    )
    date_to_widget = widgets.Text(
        value="" if default_config.get("date_to") is None else str(default_config.get("date_to")),
        description="To:",
        placeholder="blank = latest",
        layout=widgets.Layout(width="230px"),
    )
    balance_widget = widgets.FloatText(
        value=float(default_config.get("initial_balance", 100_000.0)),
        description="Balance:",
        layout=widgets.Layout(width="230px"),
    )
    max_bars_widget = widgets.IntText(
        value=int(default_config.get("max_bars", 50_000)),
        description="Max bars:",
        layout=widgets.Layout(width="230px"),
    )
    ma_type_widget = widgets.Dropdown(
        options=["sma", "ema"],
        value=str(default_indicator_overrides.get("MA_TYPE", indicator_defaults["MA_TYPE"])).lower(),
        description="MA type:",
        layout=widgets.Layout(width="210px"),
    )
    fast_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("FAST_MA", indicator_defaults["FAST_MA"])),
        description="Fast:",
        layout=widgets.Layout(width="180px"),
    )
    slow_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("SLOW_MA", indicator_defaults["SLOW_MA"])),
        description="Slow:",
        layout=widgets.Layout(width="180px"),
    )
    atr_widget = widgets.IntText(
        value=int(default_indicator_overrides.get("ATR_PERIOD", indicator_defaults["ATR_PERIOD"])),
        description="ATR:",
        layout=widgets.Layout(width="180px"),
    )
    stop_widget = widgets.FloatText(
        value=float(default_strategy_overrides.get("atr_stop_mult", 2.0)),
        description="SL ATR:",
        layout=widgets.Layout(width="180px"),
    )
    tp_widget = widgets.FloatText(
        value=float(default_strategy_overrides.get("atr_tp_mult", 2.0)),
        description="TP ATR:",
        layout=widgets.Layout(width="180px"),
    )
    trailing_widget = widgets.FloatText(
        value=float(default_strategy_overrides.get("trailing_activation", 1.0)),
        description="Trail:",
        layout=widgets.Layout(width="180px"),
    )
    run_button = widgets.Button(
        description="Run selected symbol",
        button_style="success",
        icon="play",
        layout=widgets.Layout(width="210px"),
    )
    output = widgets.Output()
    state = {"active": False}

    def current_config() -> dict[str, Any]:
        return {
            "symbol": str(symbol_widget.value),
            "tf": str(timeframe_widget.value),
            "account_mode": str(account_widget.value),
            "initial_balance": float(balance_widget.value),
            "date_from": date_from_widget.value.strip() or None,
            "date_to": date_to_widget.value.strip() or None,
            "max_bars": int(max_bars_widget.value),
            "indicator_overrides": {
                "MA_TYPE": str(ma_type_widget.value),
                "FAST_MA": int(fast_widget.value),
                "SLOW_MA": int(slow_widget.value),
                "ATR_PERIOD": int(atr_widget.value),
            },
            "strategy_overrides": {
                "execution_model": str(execution_widget.value),
                "atr_stop_mult": float(stop_widget.value),
                "atr_tp_mult": float(tp_widget.value),
                "trailing_activation": float(trailing_widget.value),
            },
            "costs": dict(default_config.get("costs") or {}),
            "broker_profile": default_config.get("broker_profile"),
            "replay": dict(default_config.get("replay") or {}),
        }

    def run_selected(_: Any = None) -> None:
        if ns.get("_ma_cross_symbol_backtest_dashboard_token") is not token:
            return
        if state["active"] or run_button.disabled:
            return
        state["active"] = True
        run_button.disabled = True
        run_button.description = "Running..."
        output.clear_output(wait=False)
        try:
            with _widget_output_target(output):
                cfg = current_config()
                ns["RUN_CONFIG"] = cfg
                show_run_config("Active MA Cross Run Configuration", cfg)
                result = run_symbol_backtest(
                    cfg["symbol"],
                    init_eq=cfg["initial_balance"],
                    account_mode=cfg["account_mode"],
                    tf=cfg["tf"],
                    date_from=cfg["date_from"],
                    date_to=cfg["date_to"],
                    max_bars=cfg["max_bars"],
                    indicator_overrides=cfg["indicator_overrides"],
                    strategy_overrides=cfg["strategy_overrides"],
                    costs=cfg["costs"],
                    broker_profile=cfg["broker_profile"],
                )
                ns["result"] = result
                ns["SYMBOL"] = cfg["symbol"]
                ns["TIMEFRAME"] = cfg["tf"]
                render_symbol_backtest_report(result, cfg)
        except Exception as exc:
            output.append_stdout(f"Backtest failed: {exc!r}\n")
        finally:
            state["active"] = False
            run_button.disabled = False
            run_button.description = "Run selected symbol"

    run_button.on_click(run_selected)
    header = widgets.HTML(
        """
        <div style="padding:10px 12px;border:1px solid #30363D;border-radius:6px;background:#F8FAFC">
          <b>MA Cross Symbol Backtest Control</b><br>
          <span style="font-size:12px;color:#374151">
          Choose symbol, timeframe, MA parameters, and execution settings, then run the full report.
          </span>
        </div>
        """
    )
    dashboard = widgets.VBox(
        [
            header,
            widgets.HBox([symbol_widget, timeframe_widget, account_widget, execution_widget]),
            widgets.HBox([date_from_widget, date_to_widget, balance_widget, max_bars_widget]),
            widgets.HBox([ma_type_widget, fast_widget, slow_widget, atr_widget]),
            widgets.HBox([stop_widget, tp_widget, trailing_widget, run_button]),
            output,
        ]
    )
    ns["_ma_cross_symbol_backtest_dashboard"] = dashboard
    return dashboard


def render_symbol_backtest_report(result: Any, run_config: Mapping[str, Any] | None = None) -> pd.DataFrame:
    """Render a complete MA Cross symbol backtest report."""
    symbol = getattr(result, "symbol", (run_config or {}).get("symbol", ""))
    tf = (getattr(result, "symbol_config", {}) or {}).get("timeframe", (run_config or {}).get("tf", ""))
    show_note(
        f"{symbol} MA Cross Backtest Result",
        f"Timeframe: {tf} | Account: {getattr(result, 'account_mode', (run_config or {}).get('account_mode', ''))}",
    )
    show_kpi_dashboard(getattr(result, "metrics", {}), title=f"{symbol} KPI")
    show_monthly_pnl(getattr(result, "metrics", {}), title=f"{symbol} Monthly PnL")
    plot_equity_dashboard(getattr(result, "equity", None), getattr(result, "trades", None), title=f"{symbol} Equity / Drawdown / Trade PnL")
    trade_frame = show_trade_explorer(getattr(result, "trades", None), title=f"{symbol} Recent Trades")
    plot_price_with_trades(
        getattr(result, "signal_data", pd.DataFrame()),
        getattr(result, "trades", None),
        symbol=str(symbol),
    )
    return trade_frame


def render_symbol_comparison(compare_df: pd.DataFrame) -> pd.DataFrame:
    """Display symbol/timeframe comparison results."""
    if compare_df is None or compare_df.empty:
        show_note("Comparison", "No comparison rows are available.")
        return pd.DataFrame()
    show_note("Comparison", "Compare symbols or timeframes under the active configuration.")
    display(style_table(compare_df))
    return compare_df


def symbol_result_row(symbol: str, result_obj: Any, *, timeframe: str = "") -> dict[str, Any]:
    """Return one row for symbol comparison tables."""
    row = {"symbol": symbol, "timeframe": timeframe}
    row.update(getattr(result_obj, "metrics", {}) or {})
    return row


__all__ = [
    "build_symbol_backtest_widget",
    "metric_row",
    "metrics_frame",
    "rank_grid",
    "render_symbol_backtest_report",
    "render_symbol_comparison",
    "run_portfolio_backtest",
    "run_symbol_grid_search",
    "run_timeframe_matrix",
    "select_top_candidates",
    "simple_walkforward",
    "symbol_result_row",
]
